"""Cross-document field comparison with conservative handling of OCR uncertainty."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any


def normalize_name(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def compare_documents(document_id: str, extraction: dict[str, Any], documents: list[dict[str, Any]], *, confidence_floor: float = 0.58) -> list[dict[str, Any]]:
    current_fields = {f.get("name"): f for f in extraction.get("extracted_fields", [])}
    current_type = extraction.get("document_type")
    checks: list[dict[str, Any]] = []
    for document in documents:
        if document.get("id") == document_id:
            continue
        other_fields = {f.get("name"): f for f in document.get("extraction", {}).get("extracted_fields", [])}
        other_type = document.get("extraction", {}).get("document_type")
        # Names can be compared across academic records; roll numbers only within the same document family.
        compare_fields = ["student_name"]
        if current_type and current_type == other_type and current_type in {"marksheet", "transcript"}:
            compare_fields.append("roll_number")
        for field_name in compare_fields:
            current = current_fields.get(field_name)
            other = other_fields.get(field_name)
            if not current or not other:
                continue
            left_value = current.get("reviewer_correction", {}).get("value", current.get("value", ""))
            right_value = other.get("reviewer_correction", {}).get("value", other.get("value", ""))
            left_conf = float(current.get("confidence", 0) or 0)
            right_conf = float(other.get("confidence", 0) or 0)
            evidence = [{"document_id": document_id, "filename": extraction.get("filename"), "field": field_name,
                         "page": current.get("source_page"), "text": current.get("evidence_text")},
                        {"document_id": document.get("id"), "filename": document.get("filename"), "field": field_name,
                         "page": other.get("source_page"), "text": other.get("evidence_text")}]
            if min(left_conf, right_conf) < confidence_floor:
                checks.append({"code": "CROSS_DOCUMENT_UNCERTAIN", "field": field_name, "status": "inconclusive", "category": "cross_document",
                               "summary": f"{field_name.replace('_', ' ').title()} comparison is inconclusive because one source has low extraction confidence.", "evidence": evidence})
                continue
            if field_name == "student_name":
                left, right = normalize_name(left_value), normalize_name(right_value)
                left_tokens, right_tokens = set(left.split()), set(right.split())
                token_similarity = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
                score = max(SequenceMatcher(None, left, right).ratio(), token_similarity)
                threshold = 0.84
            else:
                left, right = re.sub(r"[^a-z0-9]", "", str(left_value).casefold()), re.sub(r"[^a-z0-9]", "", str(right_value).casefold())
                score = 1.0 if left == right else 0.0
                threshold = 1.0
            if score >= threshold:
                checks.append({"code": f"CROSS_DOCUMENT_{field_name.upper()}_CONSISTENT", "field": field_name, "status": "consistent",
                               "similarity": round(score, 3), "category": "cross_document",
                               "summary": f"{field_name.replace('_', ' ').title()} values across documents appear consistent.", "evidence": evidence})
            else:
                checks.append({"code": f"CROSS_DOCUMENT_{field_name.upper()}_REVIEW", "field": field_name, "status": "review",
                               "similarity": round(score, 3), "category": "cross_document",
                               "summary": f"{field_name.replace('_', ' ').title()} values differ across uploaded documents and should be checked against an authorized source.",
                               "recommendation": "A difference is a review signal only; these uploaded documents are not independently issuer-verified.",
                               "evidence": evidence})
        # Compare marks only for same-family mark sheets from the same exam year and a matching roll number.
        if current_type == other_type == "marksheet":
            year_field = current_fields.get("exam_year", {})
            other_year_field = other_fields.get("exam_year", {})
            roll_field = current_fields.get("roll_number", {})
            other_roll_field = other_fields.get("roll_number", {})
            references_confident = min(float(year_field.get("confidence", 0) or 0), float(other_year_field.get("confidence", 0) or 0),
                                       float(roll_field.get("confidence", 0) or 0), float(other_roll_field.get("confidence", 0) or 0)) >= confidence_floor
            current_year = current_fields.get("exam_year", {}).get("value")
            other_year = other_fields.get("exam_year", {}).get("value")
            current_roll = current_fields.get("roll_number", {}).get("value")
            other_roll = other_fields.get("roll_number", {}).get("value")
            same_roll = re.sub(r"[^a-z0-9]", "", str(current_roll or "").casefold()) == re.sub(r"[^a-z0-9]", "", str(other_roll or "").casefold())
            if references_confident and current_year and current_year == other_year and current_roll and same_roll:
                current_subjects = {" ".join(str(row.get("subject", "")).casefold().split()): row for row in extraction.get("subjects", [])}
                other_subjects = {" ".join(str(row.get("subject", "")).casefold().split()): row for row in document.get("extraction", {}).get("subjects", [])}
                for subject_name in sorted(current_subjects.keys() & other_subjects.keys()):
                    left_row, right_row = current_subjects[subject_name], other_subjects[subject_name]
                    if left_row.get("marks_obtained") == right_row.get("marks_obtained") and left_row.get("maximum_marks") == right_row.get("maximum_marks"):
                        continue
                    checks.append({"code": "CROSS_DOCUMENT_SUBJECT_MARKS_REVIEW", "field": "subjects", "status": "review", "category": "cross_document",
                                   "summary": f"Marks read for {left_row.get('subject', subject_name)} differ between two uploaded marksheets for the same roll number and exam year.",
                                   "recommendation": "Compare both source files with the issuing board. A discrepancy can also come from OCR or a correction; it does not establish forgery.",
                                   "evidence": [{"document_id": document_id, "filename": extraction.get("filename"), "page": left_row.get("source_page"), "text": left_row.get("evidence_text")},
                                                {"document_id": document.get("id"), "filename": document.get("filename"), "page": right_row.get("source_page"), "text": right_row.get("evidence_text")}]})
    return checks
