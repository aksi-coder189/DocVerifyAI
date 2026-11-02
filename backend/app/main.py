"""Local-first DocVerifyAI API.

The pipeline reports evidence and screening signals; it never determines that a
document is authentic based on OCR, rules, or anomaly heuristics.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DOCVERIFY_DATA_DIR", BASE_DIR / "data")).resolve()
FILES_DIR = DATA_DIR / "documents"
DB_PATH = DATA_DIR / "docverifyai.sqlite3"
MAX_BYTES = int(os.getenv("DOCVERIFY_MAX_UPLOAD_MB", "15")) * 1024 * 1024
RETENTION_DAYS = max(0, int(os.getenv("DOCVERIFY_RETENTION_DAYS", "0")))
API_TOKEN = os.getenv("DOCVERIFY_API_TOKEN", "").strip()
ALLOWED = {".pdf": {"application/pdf"}, ".png": {"image/png"}, ".jpg": {"image/jpeg", "image/jpg"},
           ".jpeg": {"image/jpeg", "image/jpg"}, ".webp": {"image/webp"}}

def token_is_valid(supplied: str, token: str) -> bool:
    return bool(token) and hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {token}".encode("utf-8"))


class OptionalAPITokenMiddleware:
    """ASGI middleware for an optional shared token in single-user local deployments."""
    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if (self.token and scope["type"] == "http" and scope.get("method") != "OPTIONS"
                and scope.get("path", "").startswith("/api/") and scope.get("path") != "/api/health"):
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"authorization", b"").decode("latin1")
            if not token_is_valid(supplied, self.token):
                response = JSONResponse(status_code=401, content={"detail": "A valid local API token is required"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    if RETENTION_DAYS:
        purge_expired_data(RETENTION_DAYS)
    yield


app = FastAPI(title="DocVerifyAI API", version="0.1.0", lifespan=lifespan, description=(
    "Local document screening and review API. Extracted data and heuristics are not proof of authenticity."
))
app.add_middleware(OptionalAPITokenMiddleware, token=API_TOKEN)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_credentials=False, allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["Content-Type", "Authorization"])




def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def initialize() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, media_type TEXT NOT NULL,
            size INTEGER NOT NULL, sha256 TEXT NOT NULL, stored_name TEXT NOT NULL,
            created_at TEXT NOT NULL, extraction TEXT NOT NULL, report TEXT,
            consent_at TEXT, deleted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL,
            decision TEXT NOT NULL, note TEXT NOT NULL, corrections TEXT NOT NULL,
            created_at TEXT NOT NULL, FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
        );
        """)


def purge_expired_data(retention_days: int, *, reference_time: datetime | None = None) -> int:
    if retention_days <= 0:
        return 0
    reference_time = reference_time or datetime.now(timezone.utc)
    cutoff = (reference_time - timedelta(days=retention_days)).isoformat()
    with db() as con:
        expired = con.execute("SELECT id,stored_name FROM documents WHERE created_at < ?", (cutoff,)).fetchall()
        con.executemany("DELETE FROM reviews WHERE document_id=?", [(row["id"],) for row in expired])
        con.executemany("DELETE FROM documents WHERE id=?", [(row["id"],) for row in expired])
    for row in expired:
        try:
            (FILES_DIR / row["stored_name"]).unlink(missing_ok=True)
        except OSError:
            pass
    return len(expired)


class ExtractedField(BaseModel):
    name: str
    value: str
    confidence: float = Field(ge=0, le=1)
    source_page: int | None = None
    bounding_box: list[float] | None = None
    evidence_text: str | None = None
    model_config = {"extra": "allow"}


class VerifyRequest(BaseModel):
    demo_consent: bool = Field(description="Explicitly allow the synthetic demo issuer lookup")


class ReviewRequest(BaseModel):
    decision: Literal["approved_for_next_step", "needs_more_information", "not_approved"]
    note: str = Field(default="", max_length=2000)
    corrections: dict[str, str] = Field(default_factory=dict)


class DocumentSummary(BaseModel):
    id: str
    filename: str
    media_type: str
    size: int
    sha256: str
    created_at: str
    extraction: dict[str, Any]
    verification_status: str
    verdict: str | None = None
    verdict_title: str | None = None


class ReviewRecord(BaseModel):
    id: int
    decision: str
    note: str
    corrections: dict[str, str]
    created_at: str


class VerificationReport(BaseModel):
    document_id: str
    generated_at: str
    status: str
    authenticity_statement: str
    consistency_checks: list[dict[str, Any]] = Field(default_factory=list)
    anomaly_signals: list[dict[str, Any]] = Field(default_factory=list)
    policy_assessment: dict[str, Any]
    issuer_verification: dict[str, Any]
    review_history: list[ReviewRecord] = Field(default_factory=list)
    filename: str | None = None
    document_type: str | None = None
    fields: list[ExtractedField] = Field(default_factory=list)
    extracted_fields: list[ExtractedField] = Field(default_factory=list)
    extraction: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    extracted_text: str | None = None
    summary: dict[str, Any] | None = None

    model_config = {"extra": "allow"}


from .extraction import DocumentLimitError, extract_document
from .issuer import IssuerRequest, SyntheticIssuerProvider
from .rag import SentenceTransformerEmbedder, retrieve_policy
from .cross_document import compare_documents
from .anomaly import predict as predict_anomaly
from .summary import build_summary, missing_required_fields


def extract(data: bytes, suffix: str) -> dict[str, Any]:
    try:
        return extract_document(data, suffix)
    except DocumentLimitError as exc:
        raise HTTPException(413, str(exc)) from exc


def _findings(extraction: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fields = {f["name"]: f for f in extraction["extracted_fields"]}
    rules: list[dict[str, Any]] = []
    if extraction["document_type"] == "unknown":
        rules.append({"code": "TYPE_UNSUPPORTED", "category": "consistency", "severity": "info",
                      "summary": "The document family could not be identified from available text.", "evidence": []})
    if not fields and not extraction.get("subjects"):
        rules.append({"code": "INSUFFICIENT_TEXT", "category": "extraction", "severity": "info",
                      "summary": "No supported fields were extracted. A clearer scan or manual review may help.", "evidence": []})
    if "student_name" in fields and len(fields["student_name"]["value"].split()) < 2:
        rules.append({"code": "NAME_INCOMPLETE", "category": "consistency", "severity": "review",
                      "summary": "The extracted name may be incomplete; please check the source document.",
                      "evidence": ["student_name"]})
    for field_name in ("student_name", "roll_number", "exam_year", "result_status"):
        field = fields.get(field_name)
        evidence = str(field.get("evidence_text") or "") if field else ""
        value = str(field.get("value") or "") if field else ""
        if field and evidence and " ".join(value.casefold().split()) not in " ".join(evidence.casefold().split()):
            rules.append({"code": "FIELD_EVIDENCE_MISMATCH", "category": "consistency", "severity": "review",
                          "summary": f"The extracted {field_name.replace('_', ' ')} is not visible in its retained source evidence. Check that field against the document.",
                          "evidence": [{"field": field_name, "page": field.get("source_page"), "text": evidence}]})
    if "exam_year" in fields:
        y = int(fields["exam_year"]["value"])
        if y > datetime.now().year or y < 1950:
            rules.append({"code": "YEAR_OUT_OF_RANGE", "category": "consistency", "severity": "review",
                          "summary": "The extracted year is outside the expected range and should be checked.", "evidence": ["exam_year"]})
    if extraction.get("document_type") == "marksheet":
        seen_subjects: dict[str, dict[str, Any]] = {}
        for subject in extraction.get("subjects", []):
            name = str(subject.get("subject", "")).strip()
            key = " ".join(name.casefold().split())
            try:
                maximum = int(subject.get("maximum_marks"))
                obtained = int(subject.get("marks_obtained"))
            except (TypeError, ValueError):
                continue
            evidence = [{"field": "subject_marks", "page": subject.get("source_page"),
                         "text": subject.get("evidence_text") or f"{name}: {obtained}/{maximum}"}]
            theory = subject.get("theory_marks")
            practical = subject.get("practical_marks", subject.get("internal_marks"))
            total = subject.get("total_marks")
            if theory is not None and practical is not None and total is not None:
                try:
                    if int(theory) + int(practical) != int(total):
                        rules.append({"code": "SUBJECT_COMPONENT_TOTAL_MISMATCH", "category": "consistency", "severity": "review",
                                      "summary": f"The theory and practical/internal marks for {name} do not add up to the printed total. Check the source values.",
                                      "evidence": evidence})
                except (TypeError, ValueError):
                    pass
            if maximum <= 0 or obtained < 0 or obtained > maximum:
                rules.append({"code": "MARKS_OUT_OF_RANGE", "category": "consistency", "severity": "review",
                              "summary": f"The marks shown for {name or 'a subject'} ({obtained}/{maximum}) are outside the stated maximum. Check the source document; extraction or OCR may be wrong.",
                              "evidence": evidence})
            if key in seen_subjects and (seen_subjects[key].get("maximum_marks"), seen_subjects[key].get("marks_obtained")) != (maximum, obtained):
                previous = seen_subjects[key]
                rules.append({"code": "DUPLICATE_SUBJECT_CONFLICT", "category": "consistency", "severity": "review",
                              "summary": f"Conflicting marks rows were read for {name}. Check the scan and source layout.",
                              "evidence": evidence + [{"field": "subject_marks", "page": previous.get("source_page"), "text": previous.get("evidence_text")} ]})
            else:
                seen_subjects[key] = subject
    # Interpretable quality/anomaly indicators, not a trained fraud classifier.
    anomaly = []
    if extraction["extraction_method"] in ("ocr_unavailable", "pdf_parse_failed", "embedded_text_extractor_unavailable"):
        anomaly.append({"code": "PROCESSING_LIMITATION", "category": "quality", "severity": "info",
                        "summary": "The local extraction engine could not read this file; this is not evidence of fraud.", "evidence": []})
    if fields and any(f["confidence"] < 0.5 for f in fields.values()):
        anomaly.append({"code": "LOW_CONFIDENCE", "category": "quality", "severity": "info",
                        "summary": "Some text was read with low confidence and should be checked manually.", "evidence": []})
    return rules, anomaly


ISSUER_FIXTURE_PATH = BASE_DIR / "policies" / "synthetic-issuer-records.json"
ISSUER_PROVIDER = SyntheticIssuerProvider(ISSUER_FIXTURE_PATH)
SYNTHETIC_RECORDS = ISSUER_PROVIDER.records
_POLICY_EMBEDDER: Any | None = None
_POLICY_EMBEDDER_ERROR: str | None = None


def _policy_embedder() -> Any | None:
    global _POLICY_EMBEDDER, _POLICY_EMBEDDER_ERROR
    if _POLICY_EMBEDDER is not None:
        return _POLICY_EMBEDDER
    if _POLICY_EMBEDDER_ERROR is not None:
        return None
    try:
        _POLICY_EMBEDDER = SentenceTransformerEmbedder(os.getenv("DOCVERIFY_EMBEDDING_MODEL"))
        return _POLICY_EMBEDDER
    except Exception as exc:
        _POLICY_EMBEDDER_ERROR = type(exc).__name__
        return None


def policy_assessment(extraction: dict[str, Any]) -> dict[str, Any]:
    doc_type = extraction.get("document_type")
    board = extraction.get("board")
    names = {field["name"] for field in extraction.get("extracted_fields", [])}
    required = {"marksheet": ["student_name", "exam_year", "roll_number"],
                "degree_certificate": ["student_name", "credential", "issuing_authority"],
                "transcript": ["student_name", "issuing_authority"]}.get(doc_type, [])
    missing = [field for field in required if field not in names]
    index_path = DATA_DIR / "policy-index.json"
    # The index is optional. Avoid initializing/loading a potentially large local
    # sentence-transformer model on every first report request when there is no index.
    if not index_path.is_file():
        retrieval = {"status": "insufficient_evidence", "answer": "Local semantic retrieval is not configured. No policy conclusion was produced.",
                     "citations": [], "reason": "policy_index_unavailable"}
    else:
        embedder = _policy_embedder()
        if embedder is None:
            retrieval = {"status": "insufficient_evidence", "answer": "Local semantic retrieval is not configured. No policy conclusion was produced.",
                         "citations": [], "reason": "embedding_model_unavailable"}
        else:
            query = f"Academic document policy checklist for {board or 'unspecified board'} {doc_type or 'unknown document'}; required visible details and review criteria."
            retrieval = retrieve_policy(query, index_path, embedder, document_type=doc_type, board=board)
    if retrieval["status"] != "evidence_found":
        return {"status": "insufficient_evidence", "summary": retrieval["answer"], "missing_fields": missing,
                "citations": retrieval.get("citations", []), "retrieval": retrieval}
    status = "checklist_incomplete" if missing else "evidence_found"
    summary = ("The retrieved passage mentions details not extracted: " + ", ".join(missing) + ". " if missing else "") + retrieval["answer"]
    return {"status": status, "summary": summary, "missing_fields": missing,
            "citations": retrieval["citations"], "retrieval": retrieval,
            "disclaimer": "Retrieval reflects only the cited local corpus; it is not an official requirement or authenticity decision."}


def issuer_simulation(extraction: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper around the typed, synthetic-only issuer provider."""
    fields = {f["name"]: str(f["value"]) for f in extraction["extracted_fields"]}
    return ISSUER_PROVIDER.verify(IssuerRequest(document_type=extraction["document_type"], fields=fields)).model_dump()


def row_to_doc(row: sqlite3.Row) -> dict[str, Any]:
    extraction = json.loads(row["extraction"])
    rules, anomalies = _findings(extraction)
    quick = build_summary(extraction, rules, anomalies, None, {"missing_fields": _missing_fields(extraction)})
    return {"id": row["id"], "filename": row["filename"], "media_type": row["media_type"],
            "size": row["size"], "sha256": row["sha256"], "created_at": row["created_at"],
            "extraction": extraction, "verification_status": "not_assessed" if not row["report"] else json.loads(row["report"]).get("status", "review_required"),
            "verdict": quick["verdict"], "verdict_title": quick["title"]}


def _missing_fields(extraction: dict[str, Any]) -> list[str]:
    return missing_required_fields(extraction)


def get_row(doc_id: str) -> sqlite3.Row:
    with db() as con:
        row = con.execute("SELECT * FROM documents WHERE id=? AND deleted_at IS NULL", (doc_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Document not found")
    return row


@app.get("/api/health", response_model=dict[str, Any], summary="Check local API readiness")
def health() -> dict[str, Any]:
    capabilities = _ocr_capabilities()
    return {"status": "ok", "service": "DocVerifyAI API", "version": app.version,
            "storage": "local", **capabilities, "issuer_provider": "synthetic_demo_only"}


def _ocr_capabilities() -> dict[str, Any]:
    try:
        import pymupdf  # noqa: F401
        pdf_text = True
    except ImportError:
        pdf_text = False
    try:
        import pytesseract
        from PIL import Image  # noqa: F401
        image_packages = True
        configured = str(pytesseract.pytesseract.tesseract_cmd)
    except ImportError:
        image_packages = False
        configured = "tesseract"
    executable = Path(configured).is_file() if ("\\" in configured or "/" in configured) else shutil.which(configured) is not None
    languages: list[str] = []
    if executable and image_packages:
        try:
            languages = pytesseract.get_languages(config="")
        except Exception:
            languages = []
    language = os.getenv("DOCVERIFY_OCR_LANGUAGE", "eng")
    requested_languages = [part.strip() for part in language.split("+") if part.strip()]
    language_data = bool(executable and requested_languages and all(part in languages for part in requested_languages))
    return {"pdf_text_extraction": pdf_text,
            "pdf_ocr": bool(pdf_text and executable and language_data),
            "image_ocr": bool(image_packages and executable and language_data),
            "tesseract_executable": executable,
            "ocr_language_data": language_data,
            "available_ocr_languages": sorted(languages),
            "ocr_language": language}


@app.get("/api/document-types", response_model=list[dict[str, str]], summary="List supported document families")
def document_types() -> list[dict[str, str]]:
    from .extraction import SCHEMAS
    return [{"id": key, "label": schema.get("display_name", key.replace("_", " ").title())}
            for key, schema in SCHEMAS["document_types"].items()]


@app.post("/api/documents", status_code=201, response_model=DocumentSummary, summary="Validate and locally process a PDF or image")
async def upload_document(file: UploadFile = File(...)) -> dict[str, Any]:
    original_name = Path(file.filename or "upload").name[:255]
    suffix = Path(original_name).suffix.lower()
    media_type = (file.content_type or "application/octet-stream").lower().split(";")[0].strip()
    if suffix not in ALLOWED or media_type not in ALLOWED[suffix]:
        raise HTTPException(415, "Unsupported file type. Upload a PDF, PNG, or JPEG with a matching content type.")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_BYTES // (1024 * 1024)} MB upload limit")
    valid_sig = ((suffix == ".pdf" and data.startswith(b"%PDF-")) or
                 (suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
                 (suffix in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff")) or
                 (suffix == ".webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"))
    if not valid_sig:
        raise HTTPException(415, "File signature does not match its extension")
    extraction = extract(data, suffix)
    doc_id = secrets.token_urlsafe(18)
    stored = f"{secrets.token_hex(24)}{suffix}"
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    blob_path = FILES_DIR / stored
    created = now()
    try:
        blob_path.write_bytes(data)
        with db() as con:
            con.execute("INSERT INTO documents(id,filename,media_type,size,sha256,stored_name,created_at,extraction) VALUES(?,?,?,?,?,?,?,?)",
                        (doc_id, original_name, media_type, len(data), hashlib.sha256(data).hexdigest(), stored, created, json.dumps(extraction)))
            row = con.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    except Exception:
        try:
            blob_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return row_to_doc(row)


@app.get("/api/documents", response_model=list[DocumentSummary], summary="List active local documents")
def list_documents() -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute("SELECT * FROM documents WHERE deleted_at IS NULL ORDER BY created_at DESC").fetchall()
    return [row_to_doc(r) for r in rows]


@app.get("/api/documents/{document_id}", response_model=DocumentSummary, summary="Get document processing status")
def get_document(document_id: str) -> dict[str, Any]:
    return row_to_doc(get_row(document_id))


@app.get("/api/documents/{document_id}/content", summary="Preview a locally stored document")
def document_content(document_id: str) -> FileResponse:
    row = get_row(document_id)
    path = (FILES_DIR / row["stored_name"]).resolve()
    try:
        path.relative_to(FILES_DIR.resolve())
    except ValueError:
        raise HTTPException(404, "Document content not found")
    if not path.is_file():
        raise HTTPException(404, "Document content not found")
    return FileResponse(path, media_type=row["media_type"], content_disposition_type="inline",
                        headers={"Cache-Control": "no-store"})


@app.post("/api/documents/{document_id}/verify", response_model=VerificationReport, summary="Run checks and an explicitly consented simulated issuer lookup")
def verify_document(document_id: str, request: VerifyRequest) -> dict[str, Any]:
    if not request.demo_consent:
        raise HTTPException(400, "Explicit demo consent is required before running the synthetic issuer lookup")
    row = get_row(document_id)
    extraction = json.loads(row["extraction"])
    rules, anomalies = _findings(extraction)
    issuer = issuer_simulation(extraction)
    cross_checks = cross_document_checks(document_id, extraction)
    ml_anomaly = predict_anomaly(extraction, DATA_DIR / "models" / "isolation-forest.joblib")
    if rules or anomalies or any(check.get("status") == "review" for check in cross_checks) or issuer["status"] in ("partial", "mismatch", "unsupported"):
        case_status = "review_required"
    elif issuer["status"] in ("not_found", "unavailable"):
        case_status = "verification_unavailable"
    else:
        case_status = "simulation_only"
    report = {"document_id": document_id, "generated_at": now(),
        "status": case_status,
        "authenticity_statement": "Automated extraction, heuristics, and simulated issuer data cannot establish that a document is authentic.",
        "consistency_checks": rules + cross_checks, "anomaly_signals": anomalies,
        "ml_anomaly": ml_anomaly, "policy_assessment": policy_assessment(extraction), "issuer_verification": issuer,
        "review_history": get_reviews(document_id)}
    report["summary"] = build_summary(extraction, report["consistency_checks"], anomalies, issuer, report["policy_assessment"])
    with db() as con:
        con.execute("UPDATE documents SET report=?,consent_at=? WHERE id=?", (json.dumps(report), now(), document_id))
    return report


def cross_document_checks(document_id: str, extraction: dict[str, Any]) -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute("SELECT id,filename,extraction FROM documents WHERE deleted_at IS NULL AND id<>?", (document_id,)).fetchall()
    other_documents = [{"id": row["id"], "filename": row["filename"], "extraction": json.loads(row["extraction"])} for row in rows]
    return compare_documents(document_id, extraction, other_documents)


def get_reviews(document_id: str) -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute("SELECT id,decision,note,corrections,created_at FROM reviews WHERE document_id=? ORDER BY id", (document_id,)).fetchall()
    return [{"id": r["id"], "decision": r["decision"], "note": r["note"],
             "corrections": json.loads(r["corrections"]), "created_at": r["created_at"]} for r in rows]


@app.get("/api/documents/{document_id}/report", response_model=VerificationReport, summary="Get evidence, policy citations, and review report")
def report(document_id: str) -> dict[str, Any]:
    row = get_row(document_id)
    extraction = json.loads(row["extraction"])
    rules, anomalies = _findings(extraction)
    cross_checks = cross_document_checks(document_id, extraction)
    ml_anomaly = predict_anomaly(extraction, DATA_DIR / "models" / "isolation-forest.joblib")
    policy = policy_assessment(extraction)
    result = json.loads(row["report"]) if row["report"] else {
        "document_id": document_id, "generated_at": now(), "status": "not_assessed",
        "authenticity_statement": "Automated extraction and heuristics cannot establish that a document is authentic.",
        "issuer_verification": {"status": "not_assessed", "provider": None,
                                "message": "Run the explicitly simulated issuer check only if you consent."},
    }
    checks = rules + cross_checks
    findings = checks + anomalies
    result.update({"consistency_checks": checks, "anomaly_signals": anomalies,
                   "ml_anomaly": ml_anomaly, "policy_assessment": policy,
                   "filename": row["filename"], "document_type": extraction["document_type"],
                   "fields": extraction["extracted_fields"], "extracted_fields": extraction["extracted_fields"],
                   "extraction": extraction, "findings": findings,
                   "checks": findings, "extracted_text": extraction["raw_text"]})
    result["review_history"] = get_reviews(document_id)
    result["summary"] = build_summary(extraction, result["consistency_checks"], anomalies,
                                      result.get("issuer_verification"), result.get("policy_assessment"))
    return result


@app.post("/api/documents/{document_id}/review", status_code=201, response_model=ReviewRecord, summary="Record a reviewer decision and field corrections")
def record_review(document_id: str, request: ReviewRequest) -> dict[str, Any]:
    row = get_row(document_id)
    extraction = json.loads(row["extraction"])
    field_map = {f["name"]: f for f in extraction["extracted_fields"]}
    for key, value in request.corrections.items():
        if key in field_map and isinstance(value, str):
            field = field_map[key]
            field.setdefault("original_value", field.get("value", ""))
            field.setdefault("original_confidence", field.get("confidence"))
            corrected = value[:500]
            field["value"] = corrected
            field["reviewer_correction"] = {"value": corrected, "corrected_at": now()}
            field["reviewer_corrected"] = True
    extraction["extracted_fields"] = list(field_map.values())
    with db() as con:
        con.execute("UPDATE documents SET extraction=? WHERE id=?", (json.dumps(extraction), document_id))
        cursor = con.execute("INSERT INTO reviews(document_id,decision,note,corrections,created_at) VALUES(?,?,?,?,?)",
            (document_id, request.decision, request.note, json.dumps(request.corrections), now()))
        review_id = cursor.lastrowid
        rows = con.execute("SELECT id,decision,note,corrections,created_at FROM reviews WHERE document_id=? ORDER BY id", (document_id,)).fetchall()
        hist = [{"id": r["id"], "decision": r["decision"], "note": r["note"], "corrections": json.loads(r["corrections"]), "created_at": r["created_at"]} for r in rows]
        report_row = con.execute("SELECT report FROM documents WHERE id=?", (document_id,)).fetchone()
        if report_row and report_row["report"]:
            data = json.loads(report_row["report"])
            data["review_history"] = hist
            con.execute("UPDATE documents SET report=? WHERE id=?", (json.dumps(data), document_id))
    return next(x for x in hist if x["id"] == review_id)


@app.get("/api/documents/{document_id}/reviews", response_model=list[ReviewRecord], summary="List reviewer audit history")
def review_history(document_id: str) -> list[dict[str, Any]]:
    get_row(document_id)
    return get_reviews(document_id)


@app.delete("/api/documents/{document_id}", status_code=204, response_class=Response)
def delete_document(document_id: str) -> Response:
    row = get_row(document_id)
    path = FILES_DIR / row["stored_name"]
    with db() as con:
        con.execute("DELETE FROM reviews WHERE document_id=?", (document_id,))
        con.execute("DELETE FROM documents WHERE id=?", (document_id,))
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Metadata removal is complete; an operator can remove an orphaned blob.
        pass
    return Response(status_code=204)
