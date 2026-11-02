"""Local semantic policy retrieval with explicit provenance and no hosted APIs."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str | None = None, *, local_files_only: bool = True):
        from sentence_transformers import SentenceTransformer
        self.model_name = model_name or "sentence-transformers/all-MiniLM-L6-v2"
        self.model = SentenceTransformer(self.model_name, local_files_only=local_files_only)

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()


@dataclass
class PolicyChunk:
    text: str
    source_title: str
    issuer: str
    page: int | None
    policy_date: str | None
    document_type: str | None
    board: str | None
    source_path: str
    chunk_index: int
    vector: list[float]


def _chunks(text: str, max_chars: int = 850, overlap: int = 120) -> list[str]:
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    result: list[str] = []
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > max_chars:
            if current:
                result.append(current)
                current = ""
            result.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars - overlap:]
        candidate = f"{current}\n{paragraph}".strip()
        if current and len(candidate) > max_chars:
            result.append(current)
            current = (current[-overlap:] + " " + paragraph).strip()
        else:
            current = candidate
    if current:
        result.append(current)
    return result


def policy_sources(policy_dir: Path) -> list[dict[str, Any]]:
    """Read source passages from JSON catalogues and Markdown files under a policy folder."""
    sources: list[dict[str, Any]] = []
    for source_path in sorted(policy_dir.rglob("*")):
        if not source_path.is_file() or source_path.name == "synthetic-issuer-records.json":
            continue
        if source_path.suffix.lower() == ".json":
            try:
                payload = json.loads(source_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            records = payload if isinstance(payload, list) else payload.get("passages", payload.get("documents", []))
            for item in records if isinstance(records, list) else []:
                if not isinstance(item, dict) or not item.get("passage"):
                    continue
                sources.append({**item, "source_path": str(source_path.relative_to(policy_dir)),
                                "policy_date": item.get("policy_date"), "board": item.get("board")})
        elif source_path.suffix.lower() in {".md", ".markdown"}:
            try:
                text = source_path.read_text(encoding="utf-8")
            except OSError:
                continue
            title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), source_path.stem)
            page_match = re.search(r"page\s+(\d+)", text, re.I)
            date_match = re.search(r"(?:policy date|effective date|published)\s*:\s*(\d{4}-\d{2}-\d{2})", text, re.I)
            for chunk in _chunks(text):
                sources.append({"passage": chunk, "source_title": title,
                    "issuer": "Unspecified source; verify issuer metadata before relying on it",
                    "document_type": None, "board": None,
                    "page": int(page_match.group(1)) if page_match else None,
                    "policy_date": date_match.group(1) if date_match else None,
                    "source_path": str(source_path.relative_to(policy_dir))})
    return sources


def ingest_policy_sources(policy_dir: Path, index_path: Path, embedder: Embedder) -> int:
    source_records = policy_sources(policy_dir)
    chunks: list[dict[str, Any]] = []
    for source in source_records:
        for number, text in enumerate(_chunks(str(source.get("passage", "")))):
            chunks.append({**source, "passage": text, "chunk_index": number})
    if not chunks:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps({"version": 1, "chunks": []}), encoding="utf-8")
        return 0
    vectors = embedder.encode([chunk["passage"] for chunk in chunks])
    if len(vectors) != len(chunks):
        raise ValueError("Embedding count does not match policy chunk count")
    payload = {"version": 1, "embedding_model": getattr(embedder, "model_name", "local-sentence-transformer"),
               "created_at": date.today().isoformat(),
               "chunks": [{**chunk, "vector": vector} for chunk, vector in zip(chunks, vectors)]}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = index_path.with_suffix(index_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload), encoding="utf-8")
    temp_path.replace(index_path)
    return len(chunks)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    denom = math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / denom if denom else 0.0


def retrieve_policy(query: str, index_path: Path, embedder: Embedder, *, document_type: str | None = None,
                    board: str | None = None, top_k: int = 3, min_score: float = 0.25) -> dict[str, Any]:
    if not index_path.exists():
        return {"status": "insufficient_evidence", "answer": "No policy passages have been indexed.", "citations": [], "reason": "index_missing"}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        configured_model = getattr(embedder, "model_name", None)
        if configured_model and data.get("embedding_model") not in (None, configured_model):
            return {"status": "insufficient_evidence", "answer": "The index was built with a different embedding model; rebuild it with the configured local model.", "citations": [], "reason": "embedding_model_mismatch"}
        candidates = data.get("chunks", [])
        if document_type:
            candidates = [c for c in candidates if c.get("document_type") in (None, document_type)]
        if board:
            candidates = [c for c in candidates if c.get("board") in (None, board)]
        if not candidates:
            return {"status": "insufficient_evidence", "answer": "No indexed passage applies to this document type or board.", "citations": [], "reason": "no_metadata_match"}
        query_vector = embedder.encode([query])[0]
    except Exception as exc:
        return {"status": "insufficient_evidence", "answer": "The local policy index is unavailable; no policy conclusion was produced.", "citations": [], "reason": type(exc).__name__}
    ranked = sorted((( _cosine(query_vector, item.get("vector", [])), item) for item in candidates), key=lambda x: x[0], reverse=True)[:top_k]
    selected = [(score, item) for score, item in ranked if score >= min_score]
    if not selected:
        return {"status": "insufficient_evidence", "answer": "The indexed passages do not contain enough relevant evidence to answer this question.", "citations": [], "reason": "below_relevance_threshold"}
    citations = [{"source_title": item.get("source_title"), "issuer": item.get("issuer"),
                  "page": item.get("page"), "policy_date": item.get("policy_date"),
                  "passage": item.get("passage"), "score": round(score, 4),
                  "source_path": item.get("source_path")} for score, item in selected]
    # Extractive grounded response: no generative claim beyond retrieved text.
    answer = "Relevant indexed passage(s): " + " ".join(c["passage"] for c in citations)
    return {"status": "evidence_found", "answer": answer, "citations": citations, "retrieval": "local_sentence_transformer_cosine"}
