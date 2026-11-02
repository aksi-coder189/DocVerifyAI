# Synthetic university document review dataset

`university_documents_synthetic.jsonl` contains 24 fictional, text-only examples: one unchanged example and one paired single-field alteration for each of 12 common university document families. Every body is prominently marked `SYNTHETIC DEMONSTRATION — NOT AN OFFICIAL RECORD`; every person, institution, identifier, and value is invented.

Families: marksheet, transcript, degree certificate, bonafide certificate, student ID, provisional certificate, admission letter, examination admit card, fee receipt, migration certificate, transfer certificate, and scholarship award letter. Some families are currently outside the application's extraction schema; their inclusion is for evaluation and future parser development, not a claim that the app supports them.

Each JSONL record has a `pair_id`, `document_type`, `label`, sample `content`, `expected_fields`, `planted_changes`, and `review_signal`. Labels describe only how this artificial example was constructed: `synthetic_unaltered` means no planted edit; `synthetic_field_altered` means one value was changed in the paired text. They are not ground-truth labels for authenticity or fraud.

## Appropriate use

Use these examples to exercise text ingestion, field extraction, and detection of the specific planted pairwise value changes. Report counts and exact match by field and document family. Keep synthetic results separate from any licensed real-world evaluation. The examples are deliberately simple and do not model document design, seals, signatures, QR codes, security features, OCR variation, issuer-specific formats, or realistic adversarial edits. Do not train or present an authenticity/fraud classifier from this dataset. A detected mismatch is a prompt for human review against an authorized source, never proof of forgery.
