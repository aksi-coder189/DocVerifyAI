"""Extensible, evidence-preserving academic document extraction."""
from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Any

class DocumentLimitError(ValueError):
    """A valid upload exceeds a safe processing limit."""


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "document_schemas.json"
SCHEMAS = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _text_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text.casefold())


def _read_page_words(page: Any, textpage: Any | None = None) -> list[tuple[float, float, float, float, str]]:
    try:
        raw_words = page.get_text("words", **({"textpage": textpage} if textpage is not None else {}))
    except (AttributeError, TypeError, ValueError):
        return []
    output = []
    if not isinstance(raw_words, (list, tuple)):
        return []
    for item in raw_words:
        if len(item) < 5:
            continue
        try:
            x0, y0, x1, y1 = (float(item[i]) for i in range(4))
        except (TypeError, ValueError):
            continue
        value = str(item[4]).strip()
        if value:
            output.append((x0, y0, x1, y1, value))
    return output


def _pdf_pages(data: bytes, *, force_ocr_pages: set[int] | None = None) -> tuple[list[dict[str, Any]], str, dict[str, str] | None]:
    try:
        import pymupdf
    except ImportError:
        return [], "embedded_text_extractor_unavailable", None
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.page_count > 50:
            raise DocumentLimitError("PDF exceeds the 50 page processing limit")
        pages: list[dict[str, Any]] = []
        force_ocr_pages = force_ocr_pages or set()
        for number, page in enumerate(doc, 1):
            embedded = page.get_text("text").strip()
            text = embedded
            method = "embedded_pdf_text"
            diagnostic = None
            ocr_textpage = None
            ocr_candidate = None
            image_count = len(getattr(page, "get_images", lambda **kwargs: [])(full=True))
            alphanumeric_count = len(re.sub(r"\W", "", embedded))
            needs_ocr = (alphanumeric_count < 20 or (image_count and alphanumeric_count < 120)
                         or number in force_ocr_pages)
            if needs_ocr:
                try:
                    ocr_textpage = page.get_textpage_ocr(
                        language=os.getenv("DOCVERIFY_OCR_LANGUAGE", "eng"), dpi=200, full=True)
                    ocr_text = page.get_text("text", textpage=ocr_textpage).strip()
                    if ocr_text:
                        # Preserve the embedded layer until extraction quality can be compared.
                        # Sparse/image-only pages have no competing text evidence.
                        ocr_candidate = {"text": ocr_text, "words": _read_page_words(page, ocr_textpage)}
                        ocr_quality = len(re.sub(r"\W", "", ocr_text))
                        if not embedded or (number not in force_ocr_pages and ocr_quality > alphanumeric_count * 1.05):
                            text, method = ocr_text, "local_tesseract_pdf_ocr"
                        else:
                            method = "embedded_pdf_text"
                    else:
                        diagnostic = {"code": "PDF_OCR_NO_TEXT", "message": "OCR ran but found no readable text on this page. Try a higher-resolution, straight, well-lit scan."}
                except Exception as exc:
                    detail = str(exc).casefold()
                    if "tesseract" in detail and any(token in detail for token in ("not installed", "not found", "no such file")):
                        diagnostic = {"code": "TESSERACT_UNAVAILABLE", "message": "Scanned-page OCR requires the Tesseract executable. Install it and ensure it is on PATH."}
                    elif "traineddata" in detail or ("language" in detail and "data" in detail):
                        diagnostic = {"code": "OCR_LANGUAGE_DATA_UNAVAILABLE", "message": "Tesseract language data is missing. Install the configured language pack or set DOCVERIFY_OCR_LANGUAGE to an installed language."}
                    else:
                        diagnostic = {"code": "PDF_OCR_FAILED", "message": "Local OCR could not process this scanned page. Check Tesseract, its language data, and scan quality."}
            content_kind = "mixed" if embedded and image_count else ("embedded_text" if embedded else ("image_only" if image_count else "empty"))
            page_row = {"page_number": number, "text": text, "method": method,
                        "content_kind": content_kind,
                        "embedded_text_character_count": len(embedded),
                        "embedded_text_alphanumeric_count": alphanumeric_count,
                        "embedded_image_count": image_count,
                        "words": _read_page_words(page, ocr_textpage if method == "local_tesseract_pdf_ocr" else None),
                        "ocr_attempted": needs_ocr,
                        "ocr_decision": "initial sparse/image-page OCR" if needs_ocr else "skipped: embedded text present; quality assessed after parsing"}
            if ocr_candidate:
                page_row["ocr_candidate"] = ocr_candidate
            if diagnostic:
                page_row["diagnostic"] = diagnostic
                if not text:
                    page_row["method"] = diagnostic["code"].lower()
            pages.append(page_row)
        methods = {page["method"] for page in pages if page["text"]}
        method = "mixed_pdf_text_and_ocr" if len(methods) > 1 else next(iter(methods), "no_text")
        return pages, method, None
    except DocumentLimitError:
        raise
    except Exception as exc:
        exception_name = type(exc).__name__
        detail = str(exc).strip()
        invalid_hints = ("xref", "syntax", "malformed", "invalid pdf", "cannot open broken document", "encrypted")
        if exception_name in {"FileDataError", "EmptyFileError"} or any(hint in detail.casefold() for hint in invalid_hints):
            return [], "pdf_parse_failed", {"code": "PDF_INVALID_OR_CORRUPT", "message": f"PyMuPDF {exception_name}: the file is invalid, damaged, or encrypted. Check the PDF and export it again."}
        safe_detail = re.sub(r"(?:[A-Za-z]:\\|/)[^\s:]+", "[path]", detail)[:240]
        technical = f" {safe_detail}" if safe_detail else ""
        return [], "pdf_processing_failed", {"code": "PDF_PROCESSING_FAILED", "message": f"PDF processing raised {exception_name}:{technical} Check the PDF, then retry; if this repeats, inspect the backend logs."}

def _image_pages(data: bytes) -> tuple[list[dict[str, Any]], str]:
    try:
        from PIL import Image
        import pytesseract
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > 40_000_000:
            raise DocumentLimitError("Image dimensions exceed the 40 megapixel processing limit")
        data_rows = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(image).strip()
        confs = [w for w in data_rows.get("conf", []) if str(w) not in ("-1", "")]
        values = [float(w) for w in confs if float(w) >= 0]
        # Engine confidence is retained as provenance, not interpreted as a correctness probability.
        engine_confidence = sum(values) / (100 * len(values)) if values else None
        words = []
        count = len(data_rows.get("text", []))
        for i in range(count):
            value = str(data_rows["text"][i]).strip()
            try:
                confidence = float(data_rows.get("conf", ["-1"] * count)[i])
                x, y = float(data_rows["left"][i]), float(data_rows["top"][i])
                width, height = float(data_rows["width"][i]), float(data_rows["height"][i])
            except (IndexError, TypeError, ValueError):
                continue
            if value and confidence >= 0:
                words.append((x, y, x + width, y + height, value))
        return [{"page_number": 1, "text": text, "method": "local_tesseract_image_ocr",
                 "engine_confidence": engine_confidence, "words": words}], "local_tesseract_image_ocr"
    except DocumentLimitError:
        raise
    except ImportError as exc:
        missing_module = getattr(exc, "name", None)
        missing = {"PIL": "Pillow"}.get(missing_module, missing_module) or "Pillow or pytesseract"
        diagnostic = {"code": "IMAGE_OCR_PYTHON_DEPENDENCY_UNAVAILABLE",
                      "message": f"Image OCR cannot start because Python package {missing} is missing. Install backend/requirements.txt, then install the Tesseract executable and configured language data."}
    except Exception as exc:
        detail = str(exc).casefold()
        if "tesseract" in detail and any(token in detail for token in ("not installed", "not found", "no such file")):
            diagnostic = {"code": "TESSERACT_UNAVAILABLE", "message": "Image OCR requires the Tesseract executable. Install it and ensure it is on PATH."}
        elif "traineddata" in detail or ("language" in detail and "data" in detail):
            diagnostic = {"code": "OCR_LANGUAGE_DATA_UNAVAILABLE", "message": "Tesseract language data is missing. Install the configured language pack or set DOCVERIFY_OCR_LANGUAGE to an installed language."}
        else:
            diagnostic = {"code": "IMAGE_OCR_FAILED", "message": "Local OCR could not process this image. Check the image format, Tesseract, language data, and image quality."}
    return [{"page_number": 1, "text": "", "method": diagnostic["code"].lower(), "diagnostic": diagnostic}], diagnostic["code"].lower()


def _normalized_document_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _identify_document(text: str) -> str:
    low = _normalized_document_text(text)
    for doc_type, schema in SCHEMAS["document_types"].items():
        if any(_normalized_document_text(label) in low for label in schema["labels"]):
            return doc_type
    return "unknown"


def _board(text: str) -> str | None:
    low = _normalized_document_text(text)
    for board, config in SCHEMAS.get("boards", {}).items():
        terms = config.get("labels", []) if isinstance(config, dict) else config
        if any(_normalized_document_text(term) in low for term in terms):
            return board
    return None


def _known_issuer_headings() -> list[list[str]]:
    headings: list[list[str]] = []
    for config in SCHEMAS.get("boards", {}).values():
        labels = config.get("labels", []) if isinstance(config, dict) else config
        for label in labels:
            words = _text_words(label)
            if len(words) >= 3 and words not in headings:
                headings.append(words)
    return headings


def _reconstruct_issuer_heading(pages: list[dict[str, Any]]) -> tuple[str, int, str] | None:
    """Match a known full issuer name across adjacent extracted layout lines."""
    headings = _known_issuer_headings()
    for page in pages:
        lines = _page_lines(page)
        for start in range(len(lines)):
            for end in range(start + 1, min(len(lines), start + 5) + 1):
                source = " ".join(lines[start:end])
                source_words = re.findall(r"[A-Za-z0-9]+", source)
                folded = [word.casefold() for word in source_words]
                for heading in headings:
                    width = len(heading)
                    for offset in range(max(0, len(folded) - width + 1)):
                        if folded[offset:offset + width] == heading:
                            reconstructed = " ".join(source_words[offset:offset + width])
                            evidence = " / ".join(lines[start:end])
                            return reconstructed, page["page_number"], evidence
    return None

def _iter_aliases(document_type: str, board: str | None = None) -> dict[str, list[str]]:
    base = {
        "student_name": ["student name", "candidate name", "name"],
        "roll_number": ["roll number", "roll no", "registration number", "registration no", "register number", "enrollment number"],
        "exam_year": ["exam year", "year of examination", "academic year", "year"],
        "result_status": ["result status", "result", "status"],
        "issue_date": ["issue date", "date of issue"],
        "issuing_authority": ["issuing authority", "issuing body", "awarding authority", "examining authority", "institution", "college", "university", "board", "issuer"],
        "credential": ["degree", "qualification", "credential", "award"]
    }
    base.update(SCHEMAS["document_types"].get(document_type, {}).get("aliases", {}))
    board_config = SCHEMAS.get("boards", {}).get(board or "", {})
    if isinstance(board_config, dict):
        for field, values in board_config.get("aliases", {}).items():
            base.setdefault(field, [])
            base[field] = list(dict.fromkeys(base[field] + values))
    return base


def _extract_fields(pages: list[dict[str, Any]], doc_type: str, board: str | None = None) -> list[dict[str, Any]]:
    aliases = _iter_aliases(doc_type, board)
    found: dict[str, dict[str, Any]] = {}
    year_pattern = r"((?:19|20)\d{2})"
    value_patterns = {
        "student_name": r"([A-Z][A-Za-z .’'\-]{1,90})",
        "roll_number": r"([A-Z0-9][A-Z0-9/\-]{2,30})",
        "exam_year": year_pattern,
        "result_status": r"(PASS(?:ED)?|FAIL(?:ED)?|DISTINCTION|FIRST CLASS|SECOND CLASS)",
        "issue_date": r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        "issuing_authority": r"([A-Za-z0-9][A-Za-z0-9 .,&()'\-]{1,100})",
        "credential": r"([A-Za-z0-9][A-Za-z0-9 .,&()'\-]{1,100})"
    }
    for page in pages:
        for line in page["text"].splitlines():
            clean = line.strip()
            if not clean:
                continue
            for field, labels in aliases.items():
                if field in found:
                    continue
                labels_rx = "|".join(re.escape(label).replace(r"\ ", r"\s+") for label in sorted(labels, key=len, reverse=True))
                if field == "issuing_authority":
                    # Authority names may themselves contain words like "Board" or "University".
                    # Only parse an explicitly labeled value; never treat a suffix of an issuer name as its label.
                    match = re.search(rf"(?i)^\s*(?:{labels_rx})\s*[:#\-]\s*{value_patterns[field]}", clean)
                else:
                    match = re.search(rf"(?i)(?:^|\b)(?:{labels_rx})\s*[:#\-]?\s*{value_patterns[field]}", clean)
                if not match:
                    continue
                raw_value = match.group(1).strip().rstrip(".,;:")
                value = " ".join(raw_value.split())
                if field == "student_name" and value.casefold() in {label.casefold() for label in labels}:
                    continue
                if field == "exam_year" and not re.fullmatch(year_pattern, value):
                    continue
                issuer_confident = True
                if field == "issuing_authority":
                    issuer_confident = bool(value and not re.match(r"(?i)^(of|for|from|by|in|at)\b", value)
                                            and len(re.findall(r"[A-Za-z0-9]+", value)) >= 2)
                confidence = 0.78 if "ocr" not in page["method"] else (page.get("engine_confidence") or 0.5)
                if field == "issuing_authority" and not issuer_confident:
                    confidence = min(confidence, 0.35)
                found[field] = {"name": field, "value": value, "original_value": raw_value,
                    "confidence": confidence, **({"issuer_confident": issuer_confident} if field == "issuing_authority" else {}),
                    "source_page": page["page_number"], "bounding_box": None, "evidence_text": clean[:500],
                    "provenance": {"method": page["method"], "source_page": page["page_number"],
                                   "evidence_text": clean[:500], "confidence_kind": "ocr_engine_score" if page.get("engine_confidence") is not None else "heuristic_extraction_score"}}
    if board == "CBSE" and doc_type == "marksheet":
        _extract_cbse_fields(pages, found)
    reconstructed = _reconstruct_issuer_heading(pages)
    if reconstructed:
        issuer, source_page, evidence = reconstructed
        _add_field(found, "issuing_authority", issuer, next(p for p in pages if p["page_number"] == source_page), evidence)
        found["issuing_authority"].update({"value": issuer, "original_value": issuer,
                                           "issuer_confident": True, "confidence": 0.94})
    return list(found.values())


def _add_field(found: dict[str, dict[str, Any]], name: str, value: str, page: dict[str, Any], evidence: str,
               *, replace: bool = False) -> None:
    value = " ".join(value.split()).strip(" .,:;-")
    if not value or (name in found and not replace):
        return
    found[name] = {"name": name, "value": value, "original_value": value, "confidence": 0.88,
                   "source_page": page["page_number"], "bounding_box": None, "evidence_text": evidence[:500],
                   "provenance": {"method": page["method"], "source_page": page["page_number"],
                                  "evidence_text": evidence[:500], "confidence_kind": "heuristic_extraction_score"}}


def _next_nonempty(lines: list[str], index: int) -> str | None:
    return next((line.strip() for line in lines[index + 1:] if line.strip()), None)


def _extract_cbse_fields(pages: list[dict[str, Any]], found: dict[str, dict[str, Any]]) -> None:
    """Read CBSE's commonly printed English labels from either same-line or split layout text."""
    for page in pages:
        lines = _page_lines(page)
        for i, line in enumerate(lines):
            if not line:
                continue
            certified = re.search(r"(?i)this is to certify that\s*[:#-]?\s*(.*)$", line)
            if certified:
                candidate = certified.group(1).strip() or _next_nonempty(lines, i)
                if candidate and re.fullmatch(r"[A-Z][A-Z .’'\-]{2,90}", candidate, re.I) and not re.search(r"(?i)roll|mother|father|school|date", candidate):
                    _add_field(found, "student_name", candidate, page, line, replace=True)
            roll = re.search(r"(?i)\broll\s*no\.?\s*[:#-]?\s*([A-Z0-9][A-Z0-9/\-]{2,30})?", line)
            if roll:
                candidate = roll.group(1) or _next_nonempty(lines, i)
                if candidate and re.fullmatch(r"[A-Z0-9][A-Z0-9/\-]{2,30}", candidate, re.I):
                    _add_field(found, "roll_number", candidate, page, line if roll.group(1) else f"{line} {candidate}", replace=True)
            labels = (("mother_name", r"Mother['’]?s\s+Name"),
                      ("father_name", r"Father(?:'s)?\s*/\s*Guardian(?:'s)?\s+Name"),
                      ("date_of_birth", r"Date\s+of\s+Birth"),
                      ("school_name", r"School"))
            for field, label in labels:
                if field == "school_name" and re.search(r"(?i)(?:secondary|senior) school (?:examination|certificate)", line):
                    continue
                match = re.search(rf"(?i)\b{label}\s*[:#-]?\s*(.*)$", line)
                if not match:
                    continue
                candidate = match.group(1).strip() or _next_nonempty(lines, i)
                if not candidate or len(candidate) > 120:
                    continue
                if field == "date_of_birth":
                    date = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", candidate)
                    if not date:
                        continue
                    candidate = date.group()
                elif not re.search(r"[A-Za-z]", candidate):
                    continue
                _add_field(found, field, candidate, page, line if match.group(1).strip() else f"{line} {candidate}", replace=True)
            exam = re.search(r"(?i)\b(SECONDARY SCHOOL EXAMINATION|SENIOR SCHOOL CERTIFICATE EXAMINATION)\s*,?\s*((?:19|20)\d{2})\b", line)
            if exam:
                _add_field(found, "exam_name", exam.group(1).title(), page, line, replace=True)
                _add_field(found, "exam_year", exam.group(2), page, line, replace=True)
            declaration = re.search(r"(?i)date of declaration of result\s*:?\s*(.*)$", line)
            if declaration:
                candidate = declaration.group(1).strip() or _next_nonempty(lines, i)
                if candidate:
                    date = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", candidate)
                    if date:
                        _add_field(found, "result_declaration_date", date.group(), page, f"{line} {candidate}", replace=True)

def _coordinate_rows(page: dict[str, Any], *, tolerance: float = 3.0) -> list[dict[str, Any]]:
    words = page.get("words") or []
    rows: list[dict[str, Any]] = []
    for x0, y0, x1, y1, value in sorted(words, key=lambda word: (word[1], word[0])):
        row = next((candidate for candidate in reversed(rows)
                    if abs(candidate["y"] - y0) <= tolerance), None)
        if row is None:
            row = {"y": y0, "words": []}
            rows.append(row)
        row["words"].append((x0, x1, value))
        row["y"] = sum(x for x in (row["y"], y0)) / 2
    for row in rows:
        row["words"].sort(key=lambda item: item[0])
        row["text"] = " ".join(word for _, _, word in row["words"])
    return rows


def _page_lines(page: dict[str, Any], *, y_tolerance: float = 3.0) -> list[str]:
    rows = _coordinate_rows(page, tolerance=y_tolerance)
    if rows:
        return [row["text"] for row in rows]
    return [line.strip() for line in page.get("text", "").splitlines() if line.strip()]


def _subject_rows_from_words(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct subject rows from coordinate-clustered word boxes.

    Header positions define column bands. Text outside an unambiguous mark band
    is kept with the subject name; ambiguous numeric tokens are omitted instead
    of being assigned a guessed mark column.
    """
    rows = _coordinate_rows(page)
    if not rows:
        return []
    # Explicit cell separators are reliable layout evidence even when header text
    # widths differ from numeric data widths (common in generated/text PDFs).
    def pipe_cells(row: dict[str, Any]) -> list[str]:
        cells: list[list[str]] = [[]]
        for _, _, value in row["words"]:
            if value.strip() == "|": cells.append([])
            else: cells[-1].append(value)
        return [" ".join(cell).strip() for cell in cells]
    for hi, header_row in enumerate(rows):
        header_cells = pipe_cells(header_row)
        if len(header_cells) < 3 or not any(re.search(r"(?i)\b(subject|course)\b", cell) for cell in header_cells): continue
        if not any(re.search(r"(?i)(mark|score|grade|theory|total|credit)", cell) for cell in header_cells): continue
        columns = []
        for cell in header_cells:
            low = cell.casefold()
            if re.search(r"\b(code|number|no)\b", low): columns.append("subject_code")
            elif re.search(r"\b(subject|course)\b", low): columns.append("subject")
            elif re.search(r"\b(maximum|max)\b", low): columns.append("maximum_marks")
            elif re.search(r"\b(theory|written|external)\b", low): columns.append("theory_marks")
            elif re.search(r"\b(internal|practical|lab|oral|project)\b", low): columns.append("practical_marks")
            elif re.search(r"\b(obtained|scored|total)\b", low): columns.append("total_marks")
            elif re.search(r"\bgrade\b", low): columns.append("grade")
            else: columns.append(None)
        context = " ".join(row["text"] for row in rows[max(0, hi - 3):hi])
        semester_match = re.search(r"(?i)\bsemester\s*(?:no\.?\s*)?([0-9]{1,2}|[ivx]+)\b", context)
        semester = semester_match.group(1).upper() if semester_match else None
        table_index = sum(1 for prior in rows[:hi] if any(re.search(r"(?i)\b(subject|course)\b", cell) for cell in pipe_cells(prior))) + 1
        output = []
        for row in rows[hi + 1:]:
            parts = pipe_cells(row)
            if len(parts) != len(columns):
                if len(parts) == 1 and output and re.search(r"[A-Za-z]", parts[0]):
                    output[-1]["subject"] = (output[-1]["subject"] + " " + parts[0]).strip()
                    output[-1]["evidence_text"] = (output[-1]["evidence_text"] + " | " + parts[0])[:300]
                continue
            cells = {col: part for col, part in zip(columns, parts) if col and part}
            subject, code = cells.get("subject", ""), cells.get("subject_code", "")
            if not re.search(r"[A-Za-z]", subject): continue
            numbers = {}
            for col in ("maximum_marks", "theory_marks", "practical_marks", "total_marks"):
                found = re.findall(r"(?<![A-Za-z])\d{1,3}(?![A-Za-z])", cells.get(col, ""))
                if len(found) == 1: numbers[col] = int(found[0])
            if not numbers: continue
            total = numbers.get("total_marks")
            grade_match = re.search(r"\b[A-F][1-2]\b", cells.get("grade", ""), re.I)
            evidence_parts = ([code] if code else []) + [subject] + [f"{key}={value}" for key, value in numbers.items()]
            if cells.get("grade"): evidence_parts.append(f"grade={cells['grade']}")
            if cells.get("total_in_words"): evidence_parts.append(f"total_in_words={cells['total_in_words']}")
            evidence = " | ".join(evidence_parts)
            output.append({"subject_code": code or None, "subject": subject,
                           "theory_marks": numbers.get("theory_marks"), "internal_marks": None,
                           "practical_marks": numbers.get("practical_marks"), "total_marks": total,
                           "marks_obtained": total, "maximum_marks": numbers.get("maximum_marks"),
                           "total_in_words": None, "grade": grade_match.group().upper() if grade_match else None,
                           "source_page": page["page_number"], "table_index": table_index, "semester": semester,
                           "evidence_text": evidence[:300],
                           "provenance": {"method": page["method"], "source_page": page["page_number"], "evidence_text": evidence[:300]}})
        if output: return output
    value_terms = {"theory", "internal", "practical", "maximum", "max", "obtained", "total", "grade", "score", "mark", "marks", "credit", "credits"}
    header_indexes = []
    for i, row in enumerate(rows):
        folded = row["text"].casefold()
        if not any(re.search(rf"\b{token}\b", folded) for token in ("subject", "course")):
            continue
        neighborhood = " ".join(r["text"] for r in rows[i:min(len(rows), i + 3)]).casefold()
        numeric_tokens = re.findall(r"\b\d{1,3}\b", folded)
        if len(numeric_tokens) < 2 and any(term in neighborhood for term in value_terms):
            header_indexes.append(i)
    all_output: list[dict[str, Any]] = []
    for table_index, header_index in enumerate(header_indexes, 1):
        context = " ".join(row["text"] for row in rows[max(0, header_index - 3):header_index])
        semester_match = re.search(r"(?i)\bsemester\s*(?:no\.?\s*)?([0-9]{1,2}|[ivx]+)\b", context)
        semester = semester_match.group(1).upper() if semester_match else None
        header_rows = []
        for candidate_header_row in rows[header_index:min(len(rows), header_index + 8)]:
            if len(re.findall(r"\b\d{1,3}\b", candidate_header_row["text"])) >= 2:
                break
            header_rows.append(candidate_header_row)
        header_words = [(x0, value, value.casefold().strip(" :./()"))
                        for row in header_rows for x0, _, value in row["words"]]
        def centers_for(tokens: set[str]) -> list[float]:
            return [x0 for x0, _, token in header_words if token in tokens]
        code_centers = centers_for({"code", "number", "no", "subjectcode"})
        subject_heads = [(x0, token) for x0, _, token in header_words
                         if token in {"subject", "course"} and (not code_centers or x0 > min(code_centers) + 5)]
        if not subject_heads:
            continue
        sx0, _ = min(subject_heads, key=lambda item: item[0])
        subject_center = sx0
        name_centers = centers_for({"name", "title"})
        if name_centers and min(name_centers) - subject_center < 90:
            subject_center = (subject_center + min(name_centers)) / 2
        theory = centers_for({"theory", "written", "external"})
        internal = centers_for({"internal", "assessment", "project", "practical"})
        practical = centers_for({"practical", "lab", "oral"})
        totals = sorted(centers_for({"total", "aggregate"}))
        maximum = centers_for({"maximum", "max"})
        obtained = centers_for({"obtained", "scored"})
        grades = centers_for({"grade", "letter"})
        words_col = centers_for({"words", "word"})
        if not any((theory, internal, practical, totals, maximum, obtained, grades)):
            continue
        columns: list[tuple[str, float]] = []
        if code_centers:
            columns.append(("subject_code", min(code_centers)))
        columns.append(("subject", subject_center))
        if theory:
            columns.append(("theory_marks", min(theory)))
        # Distinct Internal and Practical headers sharing a center are one component.
        if internal and practical and abs(min(internal) - min(practical)) <= 22:
            columns.append(("internal_practical_marks", min(internal + practical)))
        else:
            if internal: columns.append(("internal_marks", min(internal)))
            if practical: columns.append(("practical_marks", min(practical)))
        if maximum: columns.append(("maximum_marks", min(maximum)))
        if obtained: columns.append(("marks_obtained", min(obtained)))
        if totals: columns.append(("total_marks", totals[0]))
        if words_col: columns.append(("total_in_words", min(words_col)))
        elif len(totals) > 1: columns.append(("total_in_words", totals[1]))
        if grades: columns.append(("grade", min(grades)))
        columns.sort(key=lambda item: item[1])
        compact: list[tuple[str, float]] = []
        for item in columns:
            if compact and abs(item[1] - compact[-1][1]) < 8:
                if item[0] in {"subject", "internal_practical_marks", "total_in_words"}: compact[-1] = item
            else: compact.append(item)
        if not any(name == "subject" for name, _ in compact):
            continue
        boundaries = [float("-inf")] + [(compact[i][1] + compact[i + 1][1]) / 2 for i in range(len(compact) - 1)] + [float("inf")]
        def cells_for(row: dict[str, Any]) -> dict[str, str]:
            cells: dict[str, list[str]] = {}
            for x0, x1, value in row["words"]:
                center = (x0 + x1) / 2
                index = next((i for i in range(len(compact)) if boundaries[i] <= center < boundaries[i + 1]), None)
                if index is None: continue
                column = compact[index][0]
                # A text token cannot be a marks value; retain it in the subject when
                # it falls in the immediately adjacent subject/mark boundary band.
                if column in {"theory_marks", "internal_marks", "practical_marks", "internal_practical_marks", "maximum_marks", "marks_obtained", "total_marks"} and re.search(r"[A-Za-z]", value):
                    subject_index = next(i for i, (name, _) in enumerate(compact) if name == "subject")
                    if subject_index < index and center < boundaries[index] + 18:
                        column = "subject"
                cells.setdefault(column, []).append(value)
            return {name: " ".join(values).strip() for name, values in cells.items()}
        has_code = any(name == "subject_code" for name, _ in compact)
        start = next((i for i in range(header_index + 1, len(rows))
                      if (not has_code or re.fullmatch(r"\d{2,5}", cells_for(rows[i]).get("subject_code", "")))
                      and re.search(r"[A-Za-z]", cells_for(rows[i]).get("subject", ""))
                      and any(re.search(r"\d", cells_for(rows[i]).get(col, "")) for col in
                              ("theory_marks", "internal_marks", "practical_marks", "internal_practical_marks", "maximum_marks", "marks_obtained", "total_marks"))), None)
        if start is None:
            continue
        output: list[dict[str, Any]] = []
        table_rows = rows[start:]
        if len(table_rows) > 1:
            observed_gaps = [table_rows[i + 1]["y"] - table_rows[i]["y"] for i in range(min(4, len(table_rows) - 1))]
            positive_gaps = sorted(gap for gap in observed_gaps if gap > 0)
            row_gap = positive_gaps[len(positive_gaps) // 2] if positive_gaps else 20
        else:
            row_gap = 20
        previous_data_y = None
        for row in table_rows:
            if output and previous_data_y is not None and row["y"] - previous_data_y > max(28, row_gap * 1.8):
                break
            row_text = row["text"].casefold()
            if output and any(re.search(rf"\b{token}\b", row_text) for token in ("subject", "course")) and any(term in row_text for term in value_terms):
                break
            values = cells_for(row)
            code = values.get("subject_code", "").strip()
            subject = values.get("subject", "").strip()
            numbers: dict[str, int] = {}
            for col in ("theory_marks", "internal_marks", "practical_marks", "internal_practical_marks", "maximum_marks", "marks_obtained", "total_marks"):
                # Accept a marks cell only when it contains one unambiguous numeric value.
                found = re.findall(r"(?<![A-Za-z])\d{1,3}(?![A-Za-z])", values.get(col, ""))
                if len(found) == 1: numbers[col] = int(found[0])
            if has_code and not re.fullmatch(r"\d{2,5}", code):
                # A wrapped subject-name continuation has no code or marks. Append
                # its words to the preceding subject rather than inventing another row.
                if (output and subject and not code and not any(numbers.values())
                        and not re.search(r"(?i)\b(date|result|declaration|page|overall)\b", subject)
                        and re.search(r"[A-Za-z]", subject)):
                    output[-1]["subject"] = f"{output[-1]['subject']} {subject}".strip()
                    output[-1]["evidence_text"] = f"{output[-1]['evidence_text']} | {subject}"[:300]
                    previous_data_y = row["y"]
                continue
            if not re.search(r"[A-Za-z]", subject) or subject.casefold() in {"subject", "subject name", "course"}:
                continue
            total = numbers.get("total_marks", numbers.get("marks_obtained"))
            if total is None and not any(key in numbers for key in ("theory_marks", "practical_marks", "internal_marks", "internal_practical_marks")):
                continue
            grade_match = re.search(r"\b[A-F][1-2]\b", values.get("grade", ""), re.I)
            evidence_parts = ([code] if code else []) + [subject] + [f"{key}={value}" for key, value in numbers.items()]
            if values.get("grade"): evidence_parts.append(f"grade={values['grade']}")
            if values.get("total_in_words"): evidence_parts.append(f"total_in_words={values['total_in_words']}")
            evidence = " | ".join(evidence_parts)
            output.append({"subject_code": code or None, "subject": subject,
                           "theory_marks": numbers.get("theory_marks"),
                           "internal_marks": numbers.get("internal_marks", numbers.get("internal_practical_marks")),
                           "practical_marks": numbers.get("practical_marks", numbers.get("internal_practical_marks")),
                           "total_marks": total, "marks_obtained": total,
                           "maximum_marks": numbers.get("maximum_marks"),
                           "total_in_words": values.get("total_in_words") or None,
                           "grade": grade_match.group().upper() if grade_match else None,
                           "source_page": page["page_number"], "table_index": table_index, "semester": semester,
                           "evidence_text": evidence[:300],
                           "provenance": {"method": page["method"], "source_page": page["page_number"], "evidence_text": evidence[:300]}})
            previous_data_y = row["y"]
        all_output.extend(output)
    return all_output

def _subject_rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subjects = []
    for page in pages:
        geometric_rows = _subject_rows_from_words(page)
        if geometric_rows:
            subjects.extend(geometric_rows)
            continue
        lines = page["text"].splitlines()
        combined_header = " ".join(lines).casefold()
        header_found = "subject" in combined_header and any(m in combined_header for m in ("mark", "score", "grade", "theory", "total"))
        if not header_found:
            continue
        # Fallback for OCR/text-only PDFs without usable word geometry.
        for line in lines:
            parts = re.split(r"\s*[|\t]\s*|\s{2,}", line.strip())
            if len(parts) < 3:
                continue
            match = re.fullmatch(r"(.{2,60}?)\s+(\d{1,3})\s+(\d{1,3})", " ".join(parts[:3]))
            if not match:
                continue
            name, first, second = match.groups()
            if name.casefold() == "subject":
                continue
            subjects.append({"subject": name.strip(), "maximum_marks": int(first), "marks_obtained": int(second),
                             "source_page": page["page_number"], "evidence_text": line.strip()[:300],
                             "provenance": {"method": page["method"], "source_page": page["page_number"], "evidence_text": line.strip()[:300]}})
    return subjects

_REQUIRED_EXTRACTION_FIELDS = {
    "marksheet": ("student_name", "roll_number", "exam_year", "issuing_authority"),
    "transcript": ("student_name", "issuing_authority"),
    "degree_certificate": ("student_name", "credential", "issuing_authority"),
    "bonafide_certificate": ("student_name", "issuing_authority"),
    "student_id": ("student_name", "roll_number", "issuing_authority"),
}


def _weak_pdf_pages(pages: list[dict[str, Any]], document_type: str,
                    fields: list[dict[str, Any]], subjects: list[dict[str, Any]]) -> set[int]:
    required = _REQUIRED_EXTRACTION_FIELDS.get(document_type)
    if not pages or not required:
        return set()
    by_name = {field.get("name"): field for field in fields}
    missing = {name for name in required if name not in by_name}
    issuer = by_name.get("issuing_authority")
    if issuer and not issuer.get("issuer_confident", True):
        missing.add("issuing_authority")
    table_expected_pages: set[int] = set()
    if document_type in {"marksheet", "transcript"} and not subjects:
        for page in pages:
            text = " ".join(_page_lines(page)).casefold()
            has_subject_header = any(term in text for term in ("subject", "course"))
            has_result_header = any(term in text for term in ("theory", "practical", "marks", "grade", "credits", "total", "obtained", "score"))
            if has_subject_header and has_result_header:
                table_expected_pages.add(page["page_number"])
    weak = len(missing) / len(required) >= 0.25 or bool(table_expected_pages)
    if not weak:
        return set()
    if table_expected_pages:
        return table_expected_pages
    # Prefer pages whose text/layout indicates a relevant field. If labels are
    # absent because the layer is badly damaged, OCR each page to avoid guessing.
    hints = {
        "student_name": ("name", "certify that", "candidate"),
        "roll_number": ("roll", "registration", "enrollment"),
        "exam_year": ("exam", "year", "session"),
        "issuing_authority": ("authority", "board", "university", "issuer", "council"),
    }
    relevant = set()
    for page in pages:
        text = page.get("text", "").casefold()
        if any(any(hint in text for hint in hints.get(name, ())) for name in missing):
            relevant.add(page["page_number"])
    return relevant or {page["page_number"] for page in pages}


def _page_quality(page: dict[str, Any], document_type: str, board: str | None) -> tuple[int, int, int]:
    fields = _extract_fields([page], document_type, board)
    by_name = {field.get("name"): field for field in fields}
    required = _REQUIRED_EXTRACTION_FIELDS.get(document_type, ())
    required_count = sum(name in by_name for name in required)
    issuer_good = int(bool(by_name.get("issuing_authority", {}).get("issuer_confident", True))) if "issuing_authority" in required else 0
    subjects = _subject_rows([page]) if document_type in {"marksheet", "transcript"} else []
    # Required details and each structured subject row outweigh raw character count.
    structured = required_count * 3 + issuer_good + len(subjects) * 4 + sum(bool(field.get("value")) for field in fields)
    return structured, len(subjects), len(re.sub(r"\W", "", page.get("text", "")))


def _fallback_reason(page: dict[str, Any], document_type: str,
                     fields: list[dict[str, Any]], subjects: list[dict[str, Any]]) -> str:
    by_name = {field.get("name"): field for field in fields}
    missing = [name for name in _REQUIRED_EXTRACTION_FIELDS.get(document_type, ()) if name not in by_name]
    issuer = by_name.get("issuing_authority")
    if issuer and not issuer.get("issuer_confident", True):
        missing.append("complete issuer name")
    if missing:
        return "required information not confidently parsed: " + ", ".join(dict.fromkeys(missing))
    if not subjects:
        text = " ".join(_page_lines(page)).casefold()
        if any(term in text for term in ("subject", "course")) and any(term in text for term in ("theory", "practical", "marks", "grade", "credits", "total", "obtained", "score")):
            return "subject/marks table is indicated by the page text but no structured rows were parsed"
    return "structured extraction was incomplete"

def _aggregate_pdf_method(pages: list[dict[str, Any]]) -> str:
    methods = {page["method"] for page in pages if page.get("text")}
    if len(methods) > 1:
        return "mixed_pdf_text_and_ocr"
    return next(iter(methods), "no_text")


def extract_document(data: bytes, suffix: str) -> dict[str, Any]:
    pdf_diagnostic = None
    if suffix == ".pdf":
        pages, method, pdf_diagnostic = _pdf_pages(data)
    else:
        pages, method = _image_pages(data)
    text = "\n".join(f"[Page {p['page_number']}]\n{p['text']}" for p in pages if p["text"])
    reconstructed_issuer = _reconstruct_issuer_heading(pages)
    family_text = text + (f"\n{reconstructed_issuer[0]}" if reconstructed_issuer else "")
    doc_type = _identify_document(family_text)
    board = _board(family_text)
    fields = _extract_fields(pages, doc_type, board)
    subjects = _subject_rows(pages)

    # Text may be present but insufficient or poorly mapped. OCR just those PDF
    # documents where required fields/issuer/table extraction is demonstrably weak.
    if suffix == ".pdf":
        force_pages = _weak_pdf_pages(pages, doc_type, fields, subjects)
        if force_pages:
            reasons = {page["page_number"]: _fallback_reason(page, doc_type, fields, subjects)
                       for page in pages if page["page_number"] in force_pages}
            retried_pages, _, retry_diagnostic = _pdf_pages(data, force_ocr_pages=force_pages)
            by_number = {page["page_number"]: page for page in pages}
            for candidate_page in retried_pages:
                number = candidate_page["page_number"]
                if number not in force_pages:
                    continue
                current = by_number[number]
                candidate = candidate_page.get("ocr_candidate")
                current["ocr_attempted"] = True
                if not candidate:
                    current["ocr_decision"] = f"OCR fallback attempted because {reasons[number]}, but no OCR text was available"
                    if candidate_page.get("diagnostic"):
                        current["diagnostic"] = candidate_page["diagnostic"]
                    continue
                ocr_page = {**current, "text": candidate["text"], "words": candidate["words"],
                            "method": "local_tesseract_pdf_ocr"}
                before = _page_quality(current, doc_type, board)
                after = _page_quality(ocr_page, doc_type, board)
                if after[:2] > before[:2] or (after[:2] == before[:2] and after[2] > before[2] * 1.05):
                    ocr_page["ocr_attempted"] = True
                    ocr_page["ocr_decision"] = (f"OCR selected: {reasons[number]}; structured quality improved "
                                                 f"from {before[:2]} to {after[:2]}")
                    by_number[number] = ocr_page
                else:
                    current["ocr_decision"] = (f"OCR fallback attempted because {reasons[number]}, but embedded text was retained "
                                               f"(structured quality {before[:2]} vs OCR {after[:2]})")
            pages = [by_number[number] for number in sorted(by_number)]
            method = _aggregate_pdf_method(pages)
            pdf_diagnostic = retry_diagnostic or pdf_diagnostic
            text = "\n".join(f"[Page {p['page_number']}]\n{p['text']}" for p in pages if p["text"])
            reconstructed_issuer = _reconstruct_issuer_heading(pages)
            family_text = text + (f"\n{reconstructed_issuer[0]}" if reconstructed_issuer else "")
            doc_type = _identify_document(family_text)
            board = _board(family_text)
            fields = _extract_fields(pages, doc_type, board)
            subjects = _subject_rows(pages)
        else:
            for page in pages:
                page.setdefault("ocr_attempted", False)
                if not page.get("ocr_attempted"):
                    page["ocr_decision"] = "skipped: required fields are present and no unparsed subject-table header was detected"

    normalized = text.strip()
    diagnostics = [p["diagnostic"] for p in pages if p.get("diagnostic")]
    if pdf_diagnostic:
        diagnostics.append(pdf_diagnostic)
    if method == "embedded_text_extractor_unavailable":
        diagnostics.append({"code": "PDF_TEXT_EXTRACTOR_UNAVAILABLE", "message": "PDF support requires PyMuPDF. Install backend/requirements.txt and restart the API."})

    if not fields and not subjects and normalized and not diagnostics:
        diagnostics.append({"code": "UNSUPPORTED_LAYOUT", "message": "Text was extracted, but its labels or layout do not match the supported fields. Check the document manually; this is not evidence of fraud."})
    field_names = {field["name"] for field in fields}
    required = _REQUIRED_EXTRACTION_FIELDS.get(doc_type, ())
    required_missing = [name for name in required if name not in field_names]
    if doc_type in _REQUIRED_EXTRACTION_FIELDS and (fields or subjects):
        status = "complete" if not required_missing else "partial"
    else:
        status = "no_text" if not normalized else ("complete" if fields or subjects else "no_supported_fields")
    return {"schema_version": SCHEMAS["schema_version"], "document_type": doc_type, "board": board,
            "issuing_authority": next((f["value"] for f in fields if f["name"] == "issuing_authority"), None),
            "extracted_fields": fields, "subjects": subjects,
            "pages": [{"page_number": p["page_number"], "method": p["method"],
                       "content_kind": p.get("content_kind"),
                       "embedded_text_character_count": p.get("embedded_text_character_count"),
                       "embedded_text_alphanumeric_count": p.get("embedded_text_alphanumeric_count"),
                       "embedded_image_count": p.get("embedded_image_count"),
                       "ocr_attempted": p.get("ocr_attempted", False),
                       "ocr_decision": p.get("ocr_decision"),
                       "text": p["text"][:12000], **({"diagnostic": p["diagnostic"]} if p.get("diagnostic") else {})}
                      for p in pages],
            "raw_text": normalized[:20000], "page_count": len(pages), "extraction_method": method,
            "status": status, "diagnostics": diagnostics,
            "limitations": [] if normalized else [d["message"] for d in diagnostics] or ["No text was found. Try a clearer scan or review the source manually; this is not a fraud finding."]}

