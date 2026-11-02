"""Typed boundary for issuer checks; only the synthetic provider is implemented."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


class IssuerRequest(BaseModel):
    document_type: str
    fields: dict[str, str]


class IssuerResult(BaseModel):
    status: str
    provider: str | None = None
    message: str
    fields_compared: list[str] = Field(default_factory=list)
    checked_at: str
    simulated: bool
    evidence: dict[str, Any] | None = None


class IssuerProvider(Protocol):
    name: str
    simulated: bool
    def verify(self, request: IssuerRequest) -> IssuerResult: ...


class DigitalCredentialValidationResult(BaseModel):
    status: str
    credential_format: str
    cryptographic_proof_valid: bool | None = None
    trust_registry_checked: bool = False
    issuer: str | None = None
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class DigitalCredentialValidator(Protocol):
    """Typed boundary for supported credential formats and cryptographic proofs."""
    def validate(self, credential: dict[str, Any]) -> DigitalCredentialValidationResult: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyntheticIssuerProvider:
    name = "DocVerifyAI synthetic demo (simulated)"
    simulated = True

    def __init__(self, fixture_path: Path):
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        if payload.get("simulated") is not True:
            raise ValueError("Issuer demonstration fixture must explicitly be marked simulated")
        self.records = payload["records"]

    def verify(self, request: IssuerRequest) -> IssuerResult:
        fields = request.fields
        common = {"provider": self.name, "simulated": True, "checked_at": utc_now()}
        if not fields.get("roll_number"):
            return IssuerResult(status="unsupported", message="No roll or registration number was extracted for the demo lookup.", **common)
        record = next((r for r in self.records if r.get("document_type") == request.document_type and r.get("roll_number") == fields["roll_number"]), None)
        if record is None:
            return IssuerResult(status="not_found", message="No record matched in the synthetic demo dataset; this is not proof of fraud.",
                fields_compared=[k for k in ("student_name", "roll_number", "exam_year") if fields.get(k)], **common)
        scenario = record.get("scenario")
        if scenario == "unavailable":
            return IssuerResult(status="unavailable", message="The synthetic demo provider is unavailable for this scenario.", **common)
        compared = [key for key in ("student_name", "roll_number", "exam_year") if fields.get(key) is not None and record.get(key) is not None]
        if scenario == "mismatch":
            return IssuerResult(status="mismatch", message="A field differs from the synthetic demo record; this is not proof of fraud.", fields_compared=compared, **common)
        if not compared:
            status = "not_found"
        elif all(fields[k].casefold() == record[k].casefold() for k in compared):
            status = "matched" if len(compared) == 3 else "partial"
        elif fields.get("roll_number") == record.get("roll_number"):
            status = "mismatch"
        else:
            status = "not_found"
        messages = {
            "matched": "All available synthetic demo fields matched; this is not real issuer confirmation.",
            "partial": "Some available fields matched a synthetic demo record, but the comparison is incomplete.",
            "mismatch": "A field differs from the synthetic demo record; this is not proof of fraud.",
            "not_found": "No record matched in the synthetic demo dataset; this is not proof of fraud.",
        }
        return IssuerResult(status=status, message=messages[status], fields_compared=compared, **common)
