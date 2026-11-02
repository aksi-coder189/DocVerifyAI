"""Build the local semantic policy index. Model weights are downloaded only when explicitly invoked."""
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag import SentenceTransformerEmbedder, ingest_policy_sources

ROOT = Path(__file__).resolve().parents[1]
model_name = os.getenv("DOCVERIFY_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
embedder = SentenceTransformerEmbedder(model_name, local_files_only=os.getenv("DOCVERIFY_ALLOW_MODEL_DOWNLOAD", "0") != "1")
count = ingest_policy_sources(ROOT / "policies", ROOT / "data" / "policy-index.json", embedder)
print(f"Indexed {count} policy chunks using {model_name}")
