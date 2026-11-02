"""Plain-language report statuses for extraction, checks, findings, and issuer verification."""
from __future__ import annotations

from typing import Any

FIELD_LABELS = {
    "student_name": "Student name", "roll_number": "Roll / registration no.",
    "exam_year": "Exam year", "result_status": "Printed result status", "issuing_authority": "Issuer",
    "board": "Board", "credential": "Degree / credential", "institution": "Institution",
}
KEY_ORDER = ["student_name", "roll_number", "exam_year", "result_status", "issuing_authority",
             "board", "credential", "institution"]
REQUIRED_FIELDS = {
    "marksheet": ["student_name", "roll_number", "exam_year", "issuing_authority"],
    "transcript": ["student_name", "issuing_authority"],
    "degree_certificate": ["student_name", "credential", "issuing_authority"],
    "bonafide_certificate": ["student_name", "issuing_authority"],
    "student_id": ["student_name", "roll_number", "issuing_authority"],
}


def _pretty(name: str) -> str:
    return FIELD_LABELS.get(name, name.replace("_", " ").capitalize())


def missing_required_fields(extraction: dict[str, Any]) -> list[str]:
    fields = {f.get("name"): f for f in extraction.get("extracted_fields", [])}
    doc_type = extraction.get("document_type", "unknown")
    required = REQUIRED_FIELDS.get(doc_type, [])
    missing = []
    for name in required:
        field = fields.get(name)
        if field is None or (name == "issuing_authority" and not field.get("issuer_confident", True)):
            missing.append(name)
    return missing


def _officially_verified(issuer: dict[str, Any] | None) -> bool:
    if not issuer or issuer.get("simulated") is not False or issuer.get("authoritative") is not True:
        return False
    if issuer.get("status") not in {"matched", "verified"}:
        return False
    source = issuer.get("verification_source")
    return isinstance(source, dict) and source.get("authoritative") is True and bool(source.get("name"))


def build_summary(extraction: dict[str, Any], checks: list[dict[str, Any]], anomalies: list[dict[str, Any]],
                  issuer: dict[str, Any] | None, policy: dict[str, Any] | None) -> dict[str, Any]:
    fields = extraction.get("extracted_fields", [])
    by_name = {f.get("name"): f for f in fields}
    doc_type = extraction.get("document_type", "unknown")
    missing = missing_required_fields(extraction)
    known_type = doc_type in REQUIRED_FIELDS
    extracted = bool(fields or extraction.get("subjects"))
    source_text = " ".join([
        str(extraction.get("raw_text") or ""),
        *(str(page.get("text") or "") for page in extraction.get("pages", [])),
    ]).casefold()
    # Only treat explicit self-identification as synthetic. Generic sample/demo
    # words or extraction failures alone are not enough to label a document.
    synthetic_markers = []
    for marker, pattern in (
        ("synthetic test data", r"\bsynthetic\s+test\s+data\b"),
        ("not an official document", r"\bnot\s+an\s+official\s+(?:academic\s+)?document\b"),
        ("fictional issuer", r"\b(?:board|university|issuer|authority)\b[^\n]{0,80}\bfictional\b|\bfictional\s+(?:board|university|issuer|authority)\b"),
    ):
        if __import__("re").search(pattern, source_text):
            synthetic_markers.append(marker)
    explicitly_synthetic = len(synthetic_markers) >= 2 or (
        "synthetic test data" in synthetic_markers and "not an official document" in synthetic_markers
    )
    if not extracted:
        extraction_status = "failed"
        extraction_label = "Could not read document"
    elif not known_type or missing:
        extraction_status = "partial"
        extraction_label = "Partial extraction"
    else:
        extraction_status = "complete"
        extraction_label = "Required fields extracted"

    review_items = [c for c in checks if c.get("severity") == "review" or c.get("status") == "review"]
    quality_limits = [a for a in anomalies if a.get("code") == "LOW_CONFIDENCE"]
    checks_ran = known_type and extracted
    if explicitly_synthetic:
        consistency_status = "needs_review"
        consistency_label = "Not a valid official result"
    elif review_items or quality_limits:
        consistency_status = "needs_review"
        consistency_label = "Needs review"
    elif checks_ran and extraction_status == "complete":
        consistency_status = "passed"
        consistency_label = "No issues detected in automated checks"
    elif checks_ran:
        consistency_status = "incomplete"
        consistency_label = "Checks limited by incomplete extraction"
    else:
        consistency_status = "not_run"
        consistency_label = "Automated checks not run"

    review_signals = review_items + quality_limits + [a for a in anomalies if a.get("severity") in {"review", "warning", "error"}]
    findings_status = "review_signals_present" if review_signals else ("none_reported" if checks_ran else "not_run")
    issuer_verified = _officially_verified(issuer)
    if issuer_verified:
        issuer_status, issuer_label = "verified", "Verified by authoritative source"
    elif issuer and issuer.get("simulated") is True:
        issuer_status, issuer_label = "simulated_only", "Simulated lookup only"
    else:
        issuer_status, issuer_label = "unverified", "Not officially verified"

    details = [{"label": _pretty(n), "value": by_name[n].get("value", ""),
                "confidence": by_name[n].get("confidence")} for n in KEY_ORDER if n in by_name]
    for field in fields:
        if field.get("name") not in KEY_ORDER:
            details.append({"label": _pretty(field.get("name", "field")), "value": field.get("value", ""),
                            "confidence": field.get("confidence")})

    reasons: list[str] = []
    reasons.extend(d.get("message", "") for d in extraction.get("diagnostics", []) if d.get("message"))
    if explicitly_synthetic:
        reasons.append(
            "The document explicitly identifies itself as synthetic test data and not an official document. "
            "Its printed PASS is sample text, not a valid academic result."
        )
    if not extracted:
        if not reasons:
            reasons.append("No supported fields could be read from this file.")
    if not known_type:
        reasons.append("The document family is unsupported or could not be identified, so required fields cannot be assessed.")
    elif missing:
        reasons.append("Missing or uncertain required fields: " + ", ".join(_pretty(name) for name in missing) + ".")
    for check in review_items:
        message = check.get("summary")
        if message and message not in reasons:
            reasons.append(message)
    if any(f.get("name") == "issuing_authority" and not f.get("issuer_confident", True) for f in fields):
        reasons.append("Issuer not confidently identified. The extracted text is shown as read; no missing words have been inferred.")
    if quality_limits:
        reasons.append("Some extracted text has low OCR confidence. Check it against the source image; this does not indicate document fraud.")
    if issuer and issuer.get("simulated") is True and issuer.get("status") in {"mismatch", "partial"}:
        reasons.append("The simulated demo record differs from some extracted details; it is not an official issuer result.")

    if explicitly_synthetic:
        verdict, title = "synthetic_document", "Synthetic document - not an official pass"
        headline = "The source marks this as synthetic test data; its printed result is not an official passing result."
        next_step = "Use an official document issued by the board or university and verify it with that issuer."
    elif extraction_status == "failed":
        verdict, title = "could_not_read", "Could not read document"
        headline = "The document could not be read well enough to assess its required fields."
        next_step = "Try a clearer scan or PDF, or enter the visible details for manual review."
    elif consistency_status == "needs_review" or extraction_status == "partial":
        verdict, title = ("needs_review", "Needs review") if extraction_status == "complete" else ("partial_extraction", "Partial extraction")
        headline = "Review the missing details and any reported findings against the source document."
        next_step = "Check the source document and provide the missing information before relying on these checks."
    elif consistency_status == "passed":
        verdict, title = "automated_checks_passed", "No issues detected in automated checks"
        headline = "Required fields were extracted and the applicable automated checks reported no issues."
        next_step = "Compare the extracted details with the original and obtain issuer confirmation for authenticity."
    else:
        verdict, title = "needs_review", "Needs review"
        headline = "There is not enough information to report an automated check result."
        next_step = "Review the document manually and confirm it with the issuing institution."

    method = extraction.get("extraction_method") or "not available"
    status_breakdown = {
        "extraction": {"status": extraction_status, "label": extraction_label,
                       "method": method, "missing_fields": missing,
                       "missing_fields_status": "assessed" if known_type else "not_assessed",
                       "missing_fields_label": (", ".join(_pretty(name) for name in missing) if missing else
                                                ("None" if known_type else "Not assessed; document family unknown")),
                       "field_count": len(fields)},
        "consistency_checks": {"status": consistency_status, "label": consistency_label},
        "review_signals": {"status": findings_status,
                           "label": "Review signals present" if review_signals else ("No review signals reported" if checks_ran else "Not assessed")},
        "suspicious_findings": ({"status": "explicit_synthetic_marker", "label": "Explicitly marked synthetic / unofficial"}
                                if explicitly_synthetic else
                                {"status": "not_classified", "label": "Not classified by this system"}),
        "issuer_verification": {"status": issuer_status, "label": issuer_label},
    }
    return {
        "verdict": verdict, "title": title, "headline": headline,
        "reasons": [r for r in reasons if r], "positives": [], "key_details": details,
        "next_step": next_step, "statuses": status_breakdown,
        "extraction_method": method, "missing_fields": missing,
        "authenticity_statement": "Authenticity remains unverified unless an authoritative issuer source confirms it. OCR success and consistency checks cannot establish authenticity.",
        "disclaimer": ("The source explicitly identifies this file as synthetic and unofficial; this is not an issuer verification or fraud determination."
                       if explicitly_synthetic else
                       "Automated checks are review aids only. This report does not determine whether a document is genuine or fake."),
    }
