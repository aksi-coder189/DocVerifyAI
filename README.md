# DocVerifyAI

DocVerifyAI is a local-first academic-document review demo. It extracts supported fields, shows source evidence, runs deterministic quality checks, compares documents in the same workspace, and records reviewer actions. It does not make authenticity or eligibility decisions.

## Implemented

- React/TypeScript UI with upload, preview, reports, corrections, review notes, and deletion.
- FastAPI backend with local SQLite metadata and private file storage. The maintained synthetic issuer fixture is packaged under `backend/policies/` and matches the synthetic PDF and scenario tests.
- Page-wise embedded PDF text extraction (PyMuPDF is installed by default); local Tesseract OCR fallback for image-backed/sparse PDF pages and OCR for images. Extraction pages, method, evidence text, source page, and provenance are retained. Configurable document schemas live in `backend/schemas/document_schemas.json`; subject-table rows are retained separately.
- Semantic policy retrieval pipeline: chunking, local Sentence Transformer vectors, metadata filters, cosine vector search, source/page/date citations, and insufficient-evidence behavior. No paid API is required. The supplied policy passages are synthetic examples only.
- Optional unsupervised Isolation Forest quality-outlier baseline, model persistence, ranked feature deviations, controlled synthetic examples, and an evaluation script. It is a review-quality signal, not a fraud classifier.
- Marksheet review checks flag subject marks outside the printed maximum, conflicting duplicate subject rows, and subject mark differences between uploaded marksheets only when roll number and exam year match confidently. Cross-document signals include both source files/pages; they are review prompts, not authenticity or fraud findings. Cross-document name and same-family roll-number comparisons also retain evidence and low-confidence/inconclusive handling.
- Typed issuer-provider and digital-credential-validator interfaces. Only the deterministic synthetic issuer provider is implemented; no real issuer or credential trust registry is queried.
- Reviewer corrections retain the first extracted value and confidence, provenance, and a separate correction record. Retention cleanup is disabled by default and can be configured or run explicitly. Optional bearer-token protection is available for a single local workspace.

## Limitations and simulations

There is no official policy corpus, authorized issuer integration, cryptographic credential validator, labeled real-world dataset, production anomaly model, or real-world accuracy metric. The sample records/policies are synthetic and must not be used for consequential decisions. The OCR and extraction confidence fields describe local engine/heuristic outputs; they are not calibrated correctness probabilities. The optional shared token is not multi-user identity or role-based access control. Do not expose the service publicly or use it for education eligibility decisions.

## Run locally

Requirements: Python 3.10+, Node.js 20+, npm. Open one PowerShell terminal for the API and another for the UI.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --no-cache-dir --retries 10 --timeout 600 -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL (normally http://localhost:5173). The Vite development server proxies `/api` to the local API. Use `127.0.0.1` binding for local work.

### Optional local capabilities

- **PDF text extraction and Python OCR packages:** installed by default through `requirements.txt` (PyMuPDF, Pillow, pytesseract). **Scanned PDF and image OCR:** install the Tesseract executable and configured language data (`eng` by default). The Docker image includes English and Hindi (`tesseract-ocr-eng`, `tesseract-ocr-hin`) and configures `DOCVERIFY_OCR_LANGUAGE=eng+hin`; local deployments need the equivalent Tesseract install. OCR is attempted for image-backed pages with absent or sparse embedded text, while text-rich PDFs use their embedded layer. The API health response reports each capability separately.
- **Semantic RAG:** install `requirements-rag.txt`, then explicitly build the local index from the bundled illustrative corpus:

  ```powershell
  cd backend
  python -m pip install --no-cache-dir --retries 10 --timeout 600 -r requirements-rag.txt
  $env:DOCVERIFY_ALLOW_MODEL_DOWNLOAD = "1"
  $env:DOCVERIFY_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
  python scripts/ingest_policies.py
  ```

  The explicit ingestion command downloads model weights from the configured Hugging Face model source if they are not cached. Runtime retrieval uses local cached weights only and does not send document text to a hosted model. Set the same `DOCVERIFY_EMBEDDING_MODEL` before starting the API. Re-run ingestion when the policy files or embedding model change. No index/model is bundled; without it, reports say `insufficient_evidence`.
- **Anomaly baseline:** install `requirements-ml.txt`, then run `python scripts/train_anomaly.py` and `python scripts/evaluate_anomaly.py` from `backend/`. The trainer uses controlled synthetic normal examples; the evaluator reports only a synthetic software smoke test, never a real accuracy claim.

### Optional local access and retention settings

The service is intended for one trusted local user. To require a shared bearer token, set `DOCVERIFY_API_TOKEN` before starting the backend and set the same `VITE_DOCVERIFY_API_TOKEN` in `frontend/.env.local` before starting Vite. The frontend build contains this shared token; this is only a local single-user guard, not secure multi-user authentication. Keep the API bound to loopback. For multi-user deployment, add user identity, per-user authorization, key management, audit controls, and deployment security first.

`DOCVERIFY_RETENTION_DAYS` defaults to `0` (automatic cleanup off). Set a positive integer before startup to delete older documents on startup, or run `python scripts/cleanup_retention.py --days 90` from `backend` for an explicit cleanup. Deletion removes local document records, files, and review history.

Runtime data is stored under `backend/data/` by default; `DOCVERIFY_DATA_DIR` and `DOCVERIFY_MAX_UPLOAD_MB` can override the location and size limit. Environment variables must be exported by the shell; `.env.example` is a reference, not auto-loaded.

## Tests and build

Install development dependencies (PyMuPDF is included in the normal backend requirements):

```powershell
cd backend
python -m pip install --no-cache-dir --retries 10 --timeout 600 -r requirements-dev.txt
python -m pytest -q
cd ../frontend
npm run build
```

The anomaly-model test is skipped when optional scikit-learn is not installed. Install `requirements-ml.txt` to exercise model training/persistence. RAG unit tests use a deterministic test embedder; real retrieval requires the optional Sentence Transformer package, local model weights, and an ingested index.

If pip reports `getaddrinfo failed`, check internet/DNS/proxy access to `pypi.org`. If downloads are slow or reset, retry from one terminal with the longer timeout above. The OCR Python packages are included in the normal backend requirements; Tesseract remains a system dependency. ML/RAG dependencies are optional requirement files. Close competing pip processes if Windows reports a temporary wheel-file sharing violation.

## Demonstration flow

1. Upload the bundled synthetic sample, inspect extracted field evidence and any limitations.
2. Review the source-linked report and cross-document comparisons.
3. The simulated issuer lookup requires explicit consent and always identifies itself as simulated.
4. Record a human review decision; corrections remain distinct from original values.
5. Delete the document or configure a retention period.

The sample fixture uses `DEMO-2025`; `DEMO-MISMATCH` and `DEMO-UNAVAILABLE` exercise deterministic simulated scenarios. Other roll numbers return a synthetic `not_found` result, never a real issuer response.

## Project docs

See [API](docs/API.md), [architecture](docs/ARCHITECTURE.md), [evaluation](docs/EVALUATION.md), [security and privacy](docs/SECURITY_AND_PRIVACY.md), and [implementation plan](docs/IMPLEMENTATION_PLAN.md). Interactive local API docs are at http://127.0.0.1:8000/docs.

## What changed in this version (clear output + simpler UI)

- Reports separate extraction completeness, consistency-check outcomes, review signals, suspicious classification, and issuer verification. Partial extraction never receives a passed-check headline; even a clean automated result states that authenticity is unverified until an authoritative source confirms it.
- The backend builds this in `backend/app/summary.py` and returns it as `summary` on `/report` and `/verify`; the document list also returns `verdict`.
- Technical sections (fields, checks, policy) are folded under "Show detailed evidence".
