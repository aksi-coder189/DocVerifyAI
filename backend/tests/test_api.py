import json

import pytest
from fastapi.testclient import TestClient

from app import main as api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "FILES_DIR", tmp_path / "documents")
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(api, "API_TOKEN", "")
    with TestClient(api.app) as test_client:
        yield test_client


def test_health_types_and_upload_validation(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert len(client.get("/api/document-types").json()) >= 5
    assert client.post("/api/documents", files={"file": ("note.txt", b"hello", "text/plain")}).status_code == 415
    assert client.post("/api/documents", files={"file": ("bad.pdf", b"not a pdf", "application/pdf")}).status_code == 415


def test_upload_verify_review_and_delete(client, tmp_path):
    # PDF signature validation and lifecycle coverage; extraction is intentionally optional.
    response = client.post("/api/documents", files={"file": ("sample.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")})
    assert response.status_code == 201, response.text
    document = response.json()
    doc_id = document["id"]
    saved_path = api.FILES_DIR / api.get_row(doc_id)["stored_name"]
    assert saved_path.exists()
    preview = client.get(f"/api/documents/{doc_id}/content")
    assert preview.status_code == 200
    assert preview.content.startswith(b"%PDF-")
    assert client.get(f"/api/documents/{doc_id}/report").json()["issuer_verification"]["status"] == "not_assessed"
    assert client.post(f"/api/documents/{doc_id}/verify", json={"demo_consent": False}).status_code == 400
    result = client.post(f"/api/documents/{doc_id}/verify", json={"demo_consent": True})
    assert result.status_code == 200
    assert result.json()["issuer_verification"]["simulated"] is True
    assert "cannot establish" in result.json()["authenticity_statement"]
    review = client.post(f"/api/documents/{doc_id}/review", json={
        "decision": "needs_more_information", "note": "Check the scan", "corrections": {"student_name": "Reviewer Name"}
    })
    assert review.status_code == 201
    assert len(client.get(f"/api/documents/{doc_id}/reviews").json()) == 1
    assert client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert not saved_path.exists()
    assert client.get(f"/api/documents/{doc_id}").status_code == 404


def test_extraction_evidence_and_synthetic_scenarios():
    extraction = api.extract(b"%PDF-1.4", ".pdf")
    assert "extracted_fields" in extraction
    assert "evidence_text" in api.ExtractedField(name="student_name", value="Example Person", confidence=.8).model_dump()
    for roll, status in (("DEMO-2025", "matched"), ("DEMO-MISMATCH", "mismatch"),
                         ("DEMO-UNAVAILABLE", "unavailable"), ("NO-SUCH-RECORD", "not_found")):
        result = api.issuer_simulation({"document_type": "marksheet", "extracted_fields": [
            {"name": "roll_number", "value": roll}, {"name": "student_name", "value": "Aarav Sharma"},
            {"name": "exam_year", "value": "2025"}]})
        assert result["status"] == status
        assert result["simulated"] is True
    partial = api.issuer_simulation({"document_type": "marksheet", "extracted_fields": [
        {"name": "roll_number", "value": "DEMO-2025"}]})
    assert partial["status"] == "partial"


def test_synthetic_sample_uses_upload_and_verification_pipeline(client):
    pytest.importorskip("pymupdf")
    sample = api.BASE_DIR / "examples" / "synthetic-marksheet.pdf"
    if not sample.exists():
        pytest.skip("Generate the bundled synthetic PDF with scripts/create_demo_document.py")
    response = client.post("/api/documents", files={
        "file": (sample.name, sample.read_bytes(), "application/pdf")
    })
    assert response.status_code == 201
    document = response.json()
    assert document["extraction"]["document_type"] == "marksheet"
    extracted = {f["name"]: f for f in document["extraction"]["extracted_fields"]}
    assert extracted["roll_number"]["value"] == "DEMO-2025"
    assert extracted["roll_number"]["source_page"] == 1
    assert {name: extracted[name]["value"] for name in (
        "student_name", "roll_number", "exam_year", "issuing_authority", "result_status"
    )} == {
        "student_name": "Aarav Sharma", "roll_number": "DEMO-2025", "exam_year": "2025",
        "issuing_authority": "Example Demo Board", "result_status": "Pass"
    }
    assert [(row["subject_code"], row["subject"], row["theory_marks"], row["practical_marks"], row["total_marks"], row["grade"])
            for row in document["extraction"]["subjects"]] == [
        ("041", "MATHEMATICS", 78, 20, 98, "A1"),
        ("184", "ENGLISH LANGUAGE", 75, 20, 95, "A1"),
        ("086", "APPLIED SCIENCE", 72, 20, 92, "A2"),
    ]
    assert document["extraction"]["diagnostics"] == []
    result = client.post(f"/api/documents/{document['id']}/verify", json={"demo_consent": True})
    assert result.json()["issuer_verification"]["status"] == "matched"
    assert result.json()["status"] == "simulation_only"


def test_pdf_fields_keep_source_page():
    pymupdf = pytest.importorskip("pymupdf")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Cover page")
    pdf.new_page().insert_text((72, 72), "Student Name: Demo Student\nRoll Number: DEMO-2025\nYear: 2025")
    extraction = api.extract(pdf.tobytes(), ".pdf")
    pages = {field["name"]: field["source_page"] for field in extraction["extracted_fields"]}
    assert pages["roll_number"] == 2


def test_reviewer_corrections_update_canonical_fields(client):
    # Place a deterministic extracted field in SQLite to validate the correction path.
    uploaded = client.post("/api/documents", files={"file": ("blank.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")}).json()
    doc_id = uploaded["id"]
    row = api.get_row(doc_id)
    extraction = json.loads(row["extraction"])
    extraction["extracted_fields"] = [{"name": "student_name", "value": "Aarav Sharm", "confidence": .7,
                                        "source_page": 1, "evidence_text": "Name: Aarav Sharm"}]
    with api.db() as con:
        con.execute("UPDATE documents SET extraction=? WHERE id=?", (json.dumps(extraction), doc_id))
    client.post(f"/api/documents/{doc_id}/review", json={"decision": "needs_more_information",
                "corrections": {"student_name": "Aarav Sharma"}})
    report = client.get(f"/api/documents/{doc_id}/report").json()
    assert report["fields"][0]["value"] == "Aarav Sharma"
    assert report["fields"][0]["original_value"] == "Aarav Sharm"
    assert report["fields"][0]["reviewer_correction"]["value"] == "Aarav Sharma"
    assert report["fields"][0]["confidence"] == .7
    assert report["fields"][0]["reviewer_corrected"] is True


def test_issuer_fixture_is_packaged_maintained_and_matches_sample():
    assert api.ISSUER_FIXTURE_PATH == api.BASE_DIR / "policies" / "synthetic-issuer-records.json"
    assert api.ISSUER_FIXTURE_PATH.is_file()
    assert not (api.BASE_DIR / "data" / "synthetic_issuer_records.json").exists()
    record = next(r for r in api.SYNTHETIC_RECORDS if r["roll_number"] == "DEMO-2025")
    assert (record["student_name"], record["exam_year"], record["document_type"]) == ("Aarav Sharma", "2025", "marksheet")
    assert api.ISSUER_PROVIDER.simulated is True


def test_subject_table_and_canonical_provenance():
    pymupdf = pytest.importorskip("pymupdf")
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "CBSE Academic Marksheet\nStudent Name: Example Student\nRoll No: CB-12345\nExam Year: 2025\nSubject | Max Marks | Marks Obtained\nMathematics | 100 | 94\nEnglish | 100 | 89")
    result = api.extract(pdf.tobytes(), ".pdf")
    assert result["board"] == "CBSE"
    assert result["document_type"] == "marksheet"
    assert {s["subject"] for s in result["subjects"]} == {"Mathematics", "English"}
    roll = next(f for f in result["extracted_fields"] if f["name"] == "roll_number")
    assert roll["value"] == "CB-12345"
    assert roll["provenance"]["source_page"] == roll["source_page"] == 1
    assert roll["original_value"] == roll["value"]


def test_local_rag_citations_metadata_and_insufficient_evidence(tmp_path):
    from app.rag import ingest_policy_sources, retrieve_policy
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "sample_policy.json").write_text(json.dumps([{
        "source_title":"Synthetic board checklist", "issuer":"Demo only", "document_type":"marksheet",
        "board":"CBSE", "page":7, "policy_date":"2024-01-01",
        "passage":"A marksheet review can record the visible student name and examination year."
    }]), encoding="utf-8")
    class FakeEmbedder:
        model_name = "test-vector"
        def encode(self, texts):
            return [[1.0, 0.0] if "marksheet" in t.casefold() else [0.0, 1.0] for t in texts]
    index = tmp_path / "policy-index.json"
    assert ingest_policy_sources(policy_dir, index, FakeEmbedder()) == 1
    result = retrieve_policy("marksheet student name", index, FakeEmbedder(), document_type="marksheet", board="CBSE")
    assert result["status"] == "evidence_found"
    assert result["citations"][0]["source_title"] == "Synthetic board checklist"
    assert result["citations"][0]["page"] == 7
    assert result["citations"][0]["policy_date"] == "2024-01-01"
    assert "student name" in result["answer"]
    no_evidence = retrieve_policy("unrelated topic", index, FakeEmbedder(), document_type="marksheet", board="CBSE")
    assert no_evidence["status"] == "insufficient_evidence"
    assert no_evidence["citations"] == []


def test_cross_document_comparison_handles_variants_and_uncertainty():
    from app.cross_document import compare_documents
    current = {"extracted_fields":[{"name":"student_name", "value":"Aarav Sharma", "confidence":.8, "source_page":1, "evidence_text":"Name: Aarav Sharma"}]}
    other = {"id":"doc-2", "filename":"transcript.pdf", "extraction":{"extracted_fields":[{"name":"student_name", "value":"Sharma, Aarav", "confidence":.8, "source_page":2, "evidence_text":"Candidate: Sharma, Aarav"}]}}
    result = compare_documents("doc-1", current, [other])
    assert result[0]["status"] == "consistent"
    other["extraction"]["extracted_fields"][0]["confidence"] = .2
    assert compare_documents("doc-1", current, [other])[0]["status"] == "inconclusive"


def test_anomaly_features_and_controlled_evaluation_cases(tmp_path):
    from app.anomaly import controlled_examples, features, train_model, predict
    normal, cases, labels = controlled_examples()
    assert len(normal) >= 8 and len(cases) == len(labels)
    assert features(normal[0])["page_count"] == 1
    pytest.importorskip("sklearn")
    model_path = tmp_path / "isolation-forest.joblib"
    trained = train_model(normal, model_path)
    assert model_path.exists() and trained["training_examples"] == len(normal)
    prediction = predict(cases[0], model_path)
    assert prediction["status"] in {"quality_outlier", "within_baseline"}
    assert prediction["explanations"]


def test_retention_is_disabled_by_default_and_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "FILES_DIR", tmp_path / "documents")
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "retention.sqlite3")
    api.initialize()
    with api.db() as con:
        con.execute("INSERT INTO documents(id,filename,media_type,size,sha256,stored_name,created_at,extraction) VALUES(?,?,?,?,?,?,?,?)",
                    ("old", "old.pdf", "application/pdf", 1, "x", "old.pdf", "2020-01-01T00:00:00+00:00", "{}"))
    assert api.purge_expired_data(0) == 0
    assert api.purge_expired_data(30) == 1


def test_scanned_pdf_uses_local_page_ocr_fallback(monkeypatch):
    import sys
    from types import SimpleNamespace
    class FakePage:
        def get_text(self, kind, textpage=None):
            return "" if textpage is None else "Scanned Marksheet\nStudent Name: Demo Student\nRoll Number: SCAN-123\nYear: 2025"
        def get_textpage_ocr(self, **kwargs):
            assert kwargs["full"] is True
            return object()
    page = FakePage()
    fake_pdf = SimpleNamespace(open=lambda **kwargs: SimpleNamespace(page_count=1, __iter__=lambda self: iter([page])))
    # Make the fake document genuinely iterable, as PyMuPDF documents are.
    class FakeDocument:
        page_count = 1
        def __iter__(self): return iter([page])
    fake_pdf.open = lambda **kwargs: FakeDocument()
    monkeypatch.setitem(sys.modules, "pymupdf", fake_pdf)
    extraction = api.extract(b"%PDF-fake", ".pdf")
    assert extraction["extraction_method"] == "local_tesseract_pdf_ocr"
    assert extraction["pages"][0]["page_number"] == 1
    assert next(f for f in extraction["extracted_fields"] if f["name"] == "roll_number")["value"] == "SCAN-123"



def test_optional_token_comparison_is_constant_time_and_exact():
    assert api.token_is_valid("Bearer test-secret", "test-secret")
    assert not api.token_is_valid("Bearer wrong", "test-secret")
    assert not api.token_is_valid("test-secret", "test-secret")
    assert not api.token_is_valid("Bearer öther", "test-secret")


def test_anomaly_evaluation_script_runs_only_controlled_synthetic_cases(tmp_path):
    import os
    import subprocess
    import sys
    pytest.importorskip("sklearn")
    script = api.BASE_DIR / "scripts" / "evaluate_anomaly.py"
    env = os.environ.copy()
    env["DOCVERIFY_MODEL_PATH"] = str(tmp_path / "evaluation-model.joblib")
    completed = subprocess.run([sys.executable, str(script)], cwd=api.BASE_DIR, env=env,
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["scope"] == "controlled_synthetic_cases_only"
    assert "not an accuracy estimate" in result["warning"]


def test_cross_document_database_integration_preserves_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "FILES_DIR", tmp_path / "documents")
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "cross.sqlite3")
    api.initialize()
    first = {"extracted_fields":[{"name":"student_name","value":"Aarav Sharma","confidence":.9,"source_page":1,"evidence_text":"Name: Aarav Sharma"}]}
    second = {"document_type":"transcript","extracted_fields":[{"name":"student_name","value":"Sharma, Aarav","confidence":.9,"source_page":3,"evidence_text":"Candidate: Sharma, Aarav"}]}
    with api.db() as con:
        for doc_id, filename, extraction in (("current", "marksheet.pdf", first), ("other", "transcript.pdf", second)):
            con.execute("INSERT INTO documents(id,filename,media_type,size,sha256,stored_name,created_at,extraction) VALUES(?,?,?,?,?,?,?,?)",
                        (doc_id, filename, "application/pdf", 1, "hash", f"{doc_id}.pdf", "2026-01-01T00:00:00+00:00", json.dumps(extraction)))
    checks = api.cross_document_checks("current", first)
    assert checks[0]["status"] == "consistent"
    assert checks[0]["evidence"][1]["document_id"] == "other"
    assert checks[0]["evidence"][1]["page"] == 3


def test_policy_assessment_uses_local_semantic_index(tmp_path, monkeypatch):
    class FakeEmbedder:
        model_name = "unit-test-model"
        def encode(self, texts): return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "_policy_embedder", lambda: FakeEmbedder())
    (tmp_path / "policy-index.json").write_text(json.dumps({"embedding_model":"unit-test-model","chunks":[{
        "passage":"Synthetic marksheet review may record visible name, year and roll number.",
        "source_title":"Fixture policy", "issuer":"Synthetic only", "page":4,"policy_date":None,
        "document_type":"marksheet", "board":"CBSE", "source_path":"fixture.json", "vector":[1.0,0.0]
    }]}),encoding="utf-8")
    extraction={"document_type":"marksheet","board":"CBSE","extracted_fields":[
        {"name":"student_name"},{"name":"exam_year"},{"name":"roll_number"}]}
    result=api.policy_assessment(extraction)
    assert result["status"] == "evidence_found"
    assert result["citations"][0]["source_title"] == "Fixture policy"
    assert result["citations"][0]["page"] == 4



def test_multiline_known_issuer_heading_reconstructs_generic_name():
    from app.extraction import _extract_fields
    page = {"page_number": 1, "method": "embedded_pdf_text",
            "text": "Council for the Indian\nSchool Certificate Examinations"}
    issuer = next(field for field in _extract_fields([page], "unknown") if field["name"] == "issuing_authority")
    assert issuer["value"] == "Council for the Indian School Certificate Examinations"
    assert issuer["issuer_confident"] is True


def test_split_line_cbse_heading_identifies_board_and_full_issuer():
    from app.extraction import _board, _extract_fields, _identify_document
    page = {"page_number": 1, "method": "embedded_pdf_text",
            "text": "MARKS STATEMENT\nCENTRAL BOARD\nOF SECONDARY EDUCATION"}
    assert _identify_document(page["text"]) == "marksheet"
    assert _board(page["text"]) == "CBSE"
    issuer = next(field for field in _extract_fields([page], "marksheet", "CBSE")
                  if field["name"] == "issuing_authority")
    assert issuer["value"] == "CENTRAL BOARD OF SECONDARY EDUCATION"
    assert issuer["original_value"] == issuer["value"]


def test_subject_table_reconstruction_uses_y_rows_and_header_columns():
    from app.extraction import _subject_rows
    def word(x, y, text):
        return (x, y, x + max(5, len(text) * 4), y + 10, text)
    words = [word(34, 100, "CODE"), word(100, 100, "SUBJECT"), word(146, 100, "NAME"),
             word(220, 100, "THEORY"), word(285, 100, "PRACTICAL"), word(370, 100, "TOTAL"), word(520, 100, "GRADE"),
             word(34, 120, "731"), word(75, 120, "Applied"), word(113, 120, "Physics"),
             word(225, 120, "081"), word(300, 120, "019"), word(375, 120, "100"), word(530, 120, "A1"),
             word(34, 140, "202"), word(75, 140, "Data"), word(100, 140, "Systems"),
             word(225, 140, "070"), word(300, 140, "020"), word(375, 140, "090"), word(530, 140, "A2")]
    rows = _subject_rows([{"page_number": 1, "method": "embedded_pdf_text", "text": "", "words": words}])
    assert [(r["subject_code"], r["subject"], r["theory_marks"], r["practical_marks"], r["total_marks"], r["grade"]) for r in rows] == [
        ("731", "Applied Physics", 81, 19, 100, "A1"), ("202", "Data Systems", 70, 20, 90, "A2")]



def test_subject_table_without_code_column_is_supported():
    from app.extraction import _subject_rows
    def word(x, y, text): return (x, y, x + max(5, len(text) * 4), y + 10, text)
    words = [word(40, 100, "SUBJECT"), word(180, 100, "THEORY"), word(260, 100, "PRACTICAL"),
             word(370, 100, "TOTAL"), word(500, 100, "GRADE"),
             word(40, 120, "Environmental"), word(98, 120, "Studies"), word(185, 120, "080"),
             word(270, 120, "020"), word(375, 120, "100"), word(510, 120, "A1")]
    rows = _subject_rows([{"page_number": 1, "method": "embedded_pdf_text", "text": "", "words": words}])
    assert len(rows) == 1
    assert rows[0]["subject_code"] is None
    assert rows[0]["subject"] == "Environmental Studies"
    assert rows[0]["theory_marks"] == 80
    assert rows[0]["practical_marks"] == 20
    assert rows[0]["total_marks"] == 100
    assert rows[0]["grade"] == "A1"

def test_cbse_marks_statement_column_order_extracts_fields_and_subject_rows():
    pymupdf = pytest.importorskip("pymupdf")
    pdf = pymupdf.open()
    page = pdf.new_page()
    # Synthetic, coordinate-positioned page fixture: heading wraps to multiple lines
    # and table cells are laid out in columns like a printed marksheet.
    for point, text in [
        ((40, 40), "CENTRAL BOARD"), ((40, 55), "OF SECONDARY EDUCATION"),
        ((40, 75), "MARKS STATEMENT CUM CERTIFICATE"),
        ((40, 95), "SECONDARY SCHOOL EXAMINATION, 2024"),
        ((40, 125), "This is to certify that SAMPLE STUDENT"),
        ((40, 145), "Roll No. TST12345"), ((40, 165), "Mother's Name PARENT ONE"),
        ((40, 185), "Father's / Guardian's Name PARENT TWO"),
        ((40, 205), "Date of Birth 01/02/2006"), ((40, 225), "School SAMPLE PUBLIC SCHOOL"),
        ((40, 250), "RESULT: PASS"),
        ((35, 300), "CODE"), ((100, 300), "SUBJECT NAME"), ((220, 300), "THEORY"),
        ((285, 300), "INTERNAL"), ((285, 310), "PRACTICAL"), ((370, 300), "TOTAL"),
        ((410, 300), "TOTAL IN WORDS"), ((525, 300), "GRADE"),
        ((35, 320), "184"), ((75, 320), "ENGLISH"), ((220, 320), "076"),
        ((290, 320), "020"), ((370, 320), "096"), ((410, 320), "NINETY SIX"), ((530, 320), "A1"),
        ((35, 340), "002"), ((75, 340), "HINDI COURSE-A"), ((220, 340), "070"),
        ((290, 340), "020"), ((370, 340), "090"), ((410, 340), "NINETY"), ((530, 340), "A2"),
        ((40, 380), "Date of declaration of result: 03/08/2024"),
    ]:
        page.insert_text(point, text)
    result = api.extract(pdf.tobytes(), ".pdf")
    fields = {item["name"]: item for item in result["extracted_fields"]}
    assert result["document_type"] == "marksheet"
    assert result["board"] == "CBSE"
    assert result["extraction_method"] == "embedded_pdf_text"
    assert result["status"] == "complete"
    assert result["pages"][0]["embedded_text_character_count"] > 200
    assert fields["issuing_authority"]["value"] == "CENTRAL BOARD OF SECONDARY EDUCATION"
    assert fields["student_name"]["value"] == "SAMPLE STUDENT"
    assert fields["roll_number"]["value"] == "TST12345"
    assert fields["exam_year"]["value"] == "2024"
    assert fields["result_status"]["value"] == "PASS"
    assert fields["result_declaration_date"]["value"] == "03/08/2024"
    assert {name: fields[name]["value"] for name in ("mother_name", "father_name", "date_of_birth", "school_name")} == {
        "mother_name": "PARENT ONE", "father_name": "PARENT TWO", "date_of_birth": "01/02/2006", "school_name": "SAMPLE PUBLIC SCHOOL"}
    assert [(row["subject_code"], row["subject"], row["theory_marks"], row["practical_marks"], row["total_marks"], row["grade"]) for row in result["subjects"]] == [
        ("184", "ENGLISH", 76, 20, 96, "A1"), ("002", "HINDI COURSE-A", 70, 20, 90, "A2")]
    assert all(row["maximum_marks"] is None for row in result["subjects"])
    summary = api.build_summary(result, [], [], None, None)
    assert summary["missing_fields"] == []
    assert summary["statuses"]["issuer_verification"]["status"] == "unverified"
    assert "remains unverified" in summary["authenticity_statement"]


def test_embedded_text_with_weak_fields_retries_pdf_ocr(monkeypatch):
    import sys
    from types import SimpleNamespace
    embedded = "MARKS STATEMENT\nCENTRAL BOARD OF SECONDARY EDUCATION\nSUBJECT THEORY TOTAL GRADE"
    ocr = ("MARKS STATEMENT\nCENTRAL BOARD OF SECONDARY EDUCATION\nSECONDARY SCHOOL EXAMINATION, 2025\n"
           "This is to certify that OCR STUDENT\nRoll No. OCR12345\nResult: PASS")
    marker = object()
    class Page:
        def get_text(self, kind, textpage=None):
            if kind == "words": return []
            return ocr if textpage is marker else embedded
        def get_images(self, full=True): return []
        def get_textpage_ocr(self, **kwargs):
            assert kwargs["full"] is True
            return marker
    class Doc:
        page_count = 1
        def __iter__(self): return iter([Page()])
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda **kwargs: Doc()))
    result = api.extract(b"%PDF-text-layer", ".pdf")
    assert result["extraction_method"] == "local_tesseract_pdf_ocr"
    assert result["pages"][0]["method"] == "local_tesseract_pdf_ocr"
    assert {f["name"] for f in result["extracted_fields"]} >= {"student_name", "roll_number", "exam_year", "result_status"}


def test_partial_fields_still_run_available_consistency_checks():
    extraction = {"document_type": "marksheet", "extraction_method": "embedded_pdf_text", "extracted_fields": [
        {"name": "student_name", "value": "OneName", "confidence": .9, "evidence_text": "Name: OneName"},
        {"name": "roll_number", "value": "R-100", "confidence": .9, "evidence_text": "Roll Number: R-100"},
        {"name": "exam_year", "value": "2099", "confidence": .9, "evidence_text": "Year: 2099"},
        {"name": "result_status", "value": "PASS", "confidence": .9, "evidence_text": "Result: PASS"}], "subjects": []}
    rules, _ = api._findings(extraction)
    assert {rule["code"] for rule in rules} == {"NAME_INCOMPLETE", "YEAR_OUT_OF_RANGE"}
    summary = api.build_summary(extraction, rules, [], None, None)
    assert summary["statuses"]["consistency_checks"]["status"] == "needs_review"
    assert summary["statuses"]["consistency_checks"]["status"] != "not_run"

def test_image_ocr_missing_python_dependency_has_actionable_diagnostic(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    result = api.extract(b"\x89PNG\r\n\x1a\n", ".png")
    assert result["diagnostics"][0]["code"] == "IMAGE_OCR_PYTHON_DEPENDENCY_UNAVAILABLE"
    assert "requirements.txt" in result["diagnostics"][0]["message"]


def test_missing_pymupdf_reports_actionable_pdf_diagnostic(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    result = api.extract(b"%PDF-1.4", ".pdf")
    assert result["diagnostics"][0]["code"] == "PDF_TEXT_EXTRACTOR_UNAVAILABLE"
    assert "PyMuPDF" in result["diagnostics"][0]["message"]


def test_missing_tesseract_is_reported_for_scanned_pdf(monkeypatch):
    import sys
    from types import SimpleNamespace
    class Page:
        def get_text(self, kind, textpage=None): return "" if textpage is None else ""
        def get_textpage_ocr(self, **kwargs): raise RuntimeError("tesseract is not installed or it's not in your PATH")
    class Doc:
        page_count = 1
        def __iter__(self): return iter([Page()])
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda **kwargs: Doc()))
    result = api.extract(b"%PDF-fake", ".pdf")
    assert result["diagnostics"][0]["code"] == "TESSERACT_UNAVAILABLE"
    assert "Install it" in result["diagnostics"][0]["message"]


def test_corrupt_pdf_has_structured_reason(monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad xref"))))
    result = api.extract(b"%PDF-corrupt", ".pdf")
    assert result["diagnostics"][0]["code"] == "PDF_INVALID_OR_CORRUPT"


def test_valid_pdf_unsupported_layout_is_not_called_fraud():
    pymupdf = pytest.importorskip("pymupdf")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Unrelated invoice layout with no supported labels")
    result = api.extract(pdf.tobytes(), ".pdf")
    assert result["status"] == "no_supported_fields"
    assert result["diagnostics"][0]["code"] == "UNSUPPORTED_LAYOUT"
    assert "fraud" in result["diagnostics"][0]["message"].casefold()


def test_health_separates_pdf_text_and_ocr_capabilities(monkeypatch):
    from types import SimpleNamespace
    import sys
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    result = api._ocr_capabilities()
    assert result["pdf_text_extraction"] is True
    assert result["pdf_ocr"] is False
    assert result["tesseract_executable"] in (True, False)
    assert result["ocr_language_data"] is False



def test_synthetic_university_dataset_has_pairs_and_disclosed_labels():
    path = api.BASE_DIR / "datasets" / "university_documents_synthetic.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 24
    types = {record["document_type"] for record in records}
    assert len(types) == 12
    for document_type in types:
        pair = [record for record in records if record["document_type"] == document_type]
        assert {record["label"] for record in pair} == {"synthetic_unaltered", "synthetic_field_altered"}
        changed = next(record for record in pair if record["label"] == "synthetic_field_altered")
        assert len(changed["planted_changes"]) == 1
        assert "SYNTHETIC DEMONSTRATION" in changed["content"]
        assert changed["review_signal"] == "compare_with_authorized_issuer_record"


def test_unexpected_pdf_processing_error_keeps_technical_reason(monkeypatch):
    import sys
    from types import SimpleNamespace
    def fail(**kwargs): raise ValueError("decoder state unavailable")
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=fail))
    result = api.extract(b"%PDF-unusual", ".pdf")
    assert result["diagnostics"][0]["code"] == "PDF_PROCESSING_FAILED"
    assert "ValueError" in result["diagnostics"][0]["message"]
    assert "decoder state unavailable" in result["diagnostics"][0]["message"]


def test_health_accepts_combined_installed_ocr_languages(monkeypatch):
    import sys
    from types import SimpleNamespace
    import app.main as main
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace())
    fake_tesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
        get_languages=lambda config="": ["eng", "hin"])
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tesseract)
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=object(), __path__=[]))
    monkeypatch.setenv("DOCVERIFY_OCR_LANGUAGE", "eng+hin")
    monkeypatch.setattr(main.shutil, "which", lambda name: "C:/tesseract.exe")
    assert main._ocr_capabilities()["ocr_language_data"] is True




def test_marksheet_internal_marks_checks_are_review_signals_only():
    extraction = {"document_type": "marksheet", "extraction_method": "embedded_pdf_text",
                  "extracted_fields": [], "subjects": [
                      {"subject": "Mathematics", "maximum_marks": 100, "marks_obtained": 120, "source_page": 1, "evidence_text": "Mathematics 100 120"},
                      {"subject": " Mathematics ", "maximum_marks": 100, "marks_obtained": 90, "source_page": 1, "evidence_text": "Mathematics 100 90"}]}
    rules, _ = api._findings(extraction)
    assert {rule["code"] for rule in rules} == {"MARKS_OUT_OF_RANGE", "DUPLICATE_SUBJECT_CONFLICT"}
    assert all("fraud" not in rule["summary"].casefold() for rule in rules)


def test_cross_marksheet_subject_difference_has_evidence_and_needs_same_exam_identity():
    from app.cross_document import compare_documents
    def field(name, value): return {"name": name, "value": value, "confidence": .9, "source_page": 1, "evidence_text": f"{name}: {value}"}
    current = {"document_type": "marksheet", "extracted_fields": [field("student_name", "Aarav Sharma"), field("roll_number", "R-100"), field("exam_year", "2025")],
               "subjects": [{"subject": "Mathematics", "maximum_marks": 100, "marks_obtained": 91, "source_page": 1, "evidence_text": "Math 100 91"}]}
    other = {"id": "prior", "filename": "copy.pdf", "extraction": {"document_type": "marksheet",
              "extracted_fields": [field("student_name", "Aarav Sharma"), field("roll_number", "R100"), field("exam_year", "2025")],
              "subjects": [{"subject": "Mathematics", "maximum_marks": 100, "marks_obtained": 71, "source_page": 1, "evidence_text": "Math 100 71"}]}}
    checks = compare_documents("current", current, [other])
    mismatch = next(check for check in checks if check["code"] == "CROSS_DOCUMENT_SUBJECT_MARKS_REVIEW")
    assert mismatch["status"] == "review" and len(mismatch["evidence"]) == 2
    other["extraction"]["extracted_fields"][-1]["value"] = "2024"
    assert not any(check["code"] == "CROSS_DOCUMENT_SUBJECT_MARKS_REVIEW" for check in compare_documents("current", current, [other]))


def test_summary_partial_extraction_is_never_a_pass():
    from app.summary import build_summary
    extraction = {"document_type": "marksheet", "extraction_method": "embedded_pdf_text",
                  "extracted_fields": [{"name": "student_name", "value": "Example Student", "confidence": .9},
                                       {"name": "result_status", "value": "PASS", "confidence": .9}], "diagnostics": []}
    summary = build_summary(extraction, [], [], None, None)
    assert summary["verdict"] == "partial_extraction"
    assert summary["title"] == "Partial extraction"
    assert summary["statuses"]["extraction"]["missing_fields"] == ["roll_number", "exam_year", "issuing_authority"]
    assert summary["statuses"]["consistency_checks"]["status"] == "incomplete"
    assert summary["statuses"]["issuer_verification"]["status"] == "unverified"


def test_summary_extraction_failure_shows_actual_diagnostic():
    from app.summary import build_summary
    extraction = {"document_type": "unknown", "extraction_method": "pdf_processing_failed",
                  "extracted_fields": [], "diagnostics": [{"code": "PDF_PROCESSING_FAILED", "message": "PyMuPDF RuntimeError: decoder state unavailable."}]}
    summary = build_summary(extraction, [], [], None, None)
    assert summary["title"] == "Could not read document"
    assert "PyMuPDF RuntimeError: decoder state unavailable." in summary["reasons"]
    assert summary["statuses"]["consistency_checks"]["status"] == "not_run"
    assert summary["statuses"]["extraction"]["missing_fields_status"] == "not_assessed"


def test_issuer_extraction_requires_explicit_label_and_preserves_uncertain_text():
    from app.extraction import _extract_fields
    pages = [{"page_number": 1, "method": "embedded_pdf_text",
              "text": "Marksheet\nIssuing Authority:   OF   SECONDARY EDUCATION"}]
    fields = _extract_fields(pages, "marksheet", "CBSE")
    issuer = next(field for field in fields if field["name"] == "issuing_authority")
    assert issuer["value"] == "OF SECONDARY EDUCATION"
    assert issuer["original_value"] == "OF   SECONDARY EDUCATION"
    assert issuer["issuer_confident"] is False
    from app.summary import build_summary
    summary = build_summary({"document_type": "marksheet", "extracted_fields": fields, "extraction_method": "embedded_pdf_text"}, [], [], None, None)
    assert "issuing_authority" in summary["missing_fields"]
    assert any("Issuer not confidently identified" in reason for reason in summary["reasons"])


def test_successful_automated_checks_do_not_confirm_authenticity():
    from app.summary import build_summary
    extraction = {"document_type": "marksheet", "extraction_method": "embedded_pdf_text",
                  "extracted_fields": [{"name": key, "value": value, "confidence": .9}
                                       for key, value in (("student_name", "Example Student"), ("roll_number", "R-001"), ("exam_year", "2025"), ("issuing_authority", "Example University"))]}
    summary = build_summary(extraction, [], [], None, None)
    assert summary["title"] == "No issues detected in automated checks"
    assert summary["statuses"]["consistency_checks"]["status"] == "passed"
    assert summary["statuses"]["issuer_verification"]["status"] == "unverified"
    assert summary["statuses"]["suspicious_findings"]["status"] == "not_classified"
    assert "remains unverified" in summary["authenticity_statement"]


def test_only_authoritative_non_simulated_source_can_be_reported_verified():
    from app.summary import build_summary
    extraction = {"document_type": "marksheet", "extraction_method": "embedded_pdf_text", "extracted_fields": [
        {"name": key, "value": value, "confidence": .9}
        for key, value in (("student_name", "Example Student"), ("roll_number", "R-001"), ("exam_year", "2025"), ("issuing_authority", "Example University"))]}
    demo = {"status": "matched", "simulated": True, "authoritative": False}
    assert build_summary(extraction, [], [], demo, None)["statuses"]["issuer_verification"]["status"] == "simulated_only"
    official = {"status": "verified", "simulated": False, "authoritative": True,
                "verification_source": {"name": "Authoritative registry", "authoritative": True}}
    assert build_summary(extraction, [], [], official, None)["statuses"]["issuer_verification"]["status"] == "verified"


def test_wrapped_subject_names_and_multiple_coordinate_tables_are_reconstructed():
    from app.extraction import _subject_rows
    def word(x, y, text): return (x, y, x + max(6, len(text) * 5), y + 9, text)
    words = []
    def row(y, items): words.extend(word(x, y, text) for x, text in items)
    row(90, [(100, "SEMESTER 1")])
    row(100, [(30, "CODE"), (100, "SUBJECT"), (145, "NAME"), (240, "THEORY"),
              (320, "PRACTICAL"), (405, "TOTAL"), (500, "GRADE")])
    row(120, [(30, "501"), (100, "FOUNDATION"), (240, "075"), (320, "025"), (405, "100"), (500, "A1")])
    row(132, [(100, "OF"), (118, "COMPUTING")])
    row(152, [(30, "502"), (100, "MODERN"), (143, "HISTORY"), (240, "070"), (320, "020"), (405, "090"), (500, "A2")])
    row(200, [(100, "SEMESTER 2")])
    row(210, [(30, "CODE"), (100, "SUBJECT"), (145, "NAME"), (240, "THEORY"),
              (320, "PRACTICAL"), (405, "TOTAL"), (500, "GRADE")])
    row(230, [(30, "601"), (100, "DATA"), (128, "SCIENCE"), (240, "080"), (320, "020"), (405, "100"), (500, "A1")])
    rows = _subject_rows([{"page_number": 1, "method": "embedded_pdf_text", "text": "", "words": words}])
    assert [(r["subject_code"], r["subject"], r["theory_marks"], r["practical_marks"], r["total_marks"], r["grade"]) for r in rows] == [
        ("501", "FOUNDATION OF COMPUTING", 75, 25, 100, "A1"),
        ("502", "MODERN HISTORY", 70, 20, 90, "A2"),
        ("601", "DATA SCIENCE", 80, 20, 100, "A1")]
    assert all(r["source_page"] == 1 and r["evidence_text"] for r in rows)
    assert [r["semester"] for r in rows] == ["1", "1", "2"]


def test_text_rich_pdf_missing_table_retries_ocr_and_retains_better_embedded_text(monkeypatch):
    import sys
    from types import SimpleNamespace
    embedded = ("MARKS STATEMENT\nCENTRAL BOARD OF SECONDARY EDUCATION\nSECONDARY SCHOOL EXAMINATION, 2024\n"
                "This is to certify that RETAINED STUDENT\nRoll No. RET12345\n"
                "Subject Code Subject Name Theory Maximum Marks Practical Total Grade")
    marker = object()
    class Page:
        def get_text(self, kind, textpage=None):
            if kind == "words": return []
            return "OCR NOISE ONLY" if textpage is marker else embedded
        def get_images(self, full=True): return []
        def get_textpage_ocr(self, **kwargs): return marker
    class Doc:
        page_count = 1
        def __iter__(self): return iter([Page()])
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda **kwargs: Doc()))
    result = api.extract(b"%PDF-text-layer", ".pdf")
    assert result["pages"][0]["ocr_attempted"] is True
    assert "retained" in result["pages"][0]["ocr_decision"].casefold()
    assert result["pages"][0]["method"] == "embedded_pdf_text"
    assert "RETAINED STUDENT" in result["raw_text"]
    assert {f["name"]: f["value"] for f in result["extracted_fields"]}["student_name"] == "RETAINED STUDENT"


def test_text_rich_partial_pdf_reports_unavailable_ocr_fallback(monkeypatch):
    import sys
    from types import SimpleNamespace
    embedded = ("MARKS STATEMENT\nCENTRAL BOARD OF SECONDARY EDUCATION\nSECONDARY SCHOOL EXAMINATION, 2024\n"
                "This is to certify that PARTIAL STUDENT\nRoll No. PART12345\n"
                "Subject Code Subject Name Theory Maximum Marks Practical Total Grade")
    class Page:
        def get_text(self, kind, textpage=None): return [] if kind == "words" else embedded
        def get_images(self, full=True): return []
        def get_textpage_ocr(self, **kwargs): raise RuntimeError("tesseract is not installed or it's not in your PATH")
    class Doc:
        page_count = 1
        def __iter__(self): return iter([Page()])
    monkeypatch.setitem(sys.modules, "pymupdf", SimpleNamespace(open=lambda **kwargs: Doc()))
    result = api.extract(b"%PDF-text-layer", ".pdf")
    assert result["pages"][0]["ocr_attempted"] is True
    assert "fallback attempted" in result["pages"][0]["ocr_decision"].casefold()
    assert result["diagnostics"][0]["code"] == "TESSERACT_UNAVAILABLE"
    assert result["extracted_fields"]


def test_bundled_synthetic_pdf_has_documented_coordinate_table_and_expected_rows():
    sample = api.BASE_DIR / "examples" / "synthetic-marksheet.pdf"
    result = api.extract(sample.read_bytes(), ".pdf")
    assert result["status"] == "complete"
    assert result["extraction_method"] == "embedded_pdf_text"
    assert [(row["subject_code"], row["subject"], row["theory_marks"], row["practical_marks"], row["total_marks"], row["grade"])
            for row in result["subjects"]] == [
        ("041", "MATHEMATICS", 78, 20, 98, "A1"),
        ("184", "ENGLISH LANGUAGE", 75, 20, 95, "A1"),
        ("086", "APPLIED SCIENCE", 72, 20, 92, "A2")]
    assert result["pages"][0]["ocr_attempted"] is False
    assert "skipped" in result["pages"][0]["ocr_decision"]
    assert result["diagnostics"] == []


def test_policy_assessment_does_not_load_model_when_optional_index_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "_policy_embedder", lambda: (_ for _ in ()).throw(AssertionError("model load should be skipped")))
    result = api.policy_assessment({"document_type": "marksheet", "board": "CBSE", "extracted_fields": []})
    assert result["status"] == "insufficient_evidence"
    assert result["retrieval"]["reason"] == "policy_index_unavailable"


def test_report_computes_cross_document_checks_once(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "FILES_DIR", tmp_path / "documents")
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "report.sqlite3")
    api.initialize()
    extraction = {"document_type": "marksheet", "board": "CBSE", "extracted_fields": [], "subjects": [], "raw_text": ""}
    with api.db() as con:
        con.execute("INSERT INTO documents(id,filename,media_type,size,sha256,stored_name,created_at,extraction) VALUES(?,?,?,?,?,?,?,?)",
                    ("report-case", "sample.pdf", "application/pdf", 1, "hash", "sample.pdf", api.now(), json.dumps(extraction)))
    calls = []
    monkeypatch.setattr(api, "cross_document_checks", lambda doc_id, data: calls.append(doc_id) or [])
    monkeypatch.setattr(api, "predict_anomaly", lambda *_: {"status": "not_configured"})
    monkeypatch.setattr(api, "policy_assessment", lambda *_: {"status": "insufficient_evidence"})
    result = api.report("report-case")
    assert calls == ["report-case"]
    assert result["status"] == "not_assessed"
    assert result["issuer_verification"]["status"] == "not_assessed"
