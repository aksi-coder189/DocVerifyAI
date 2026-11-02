"""Optional, separately trained Isolation Forest baseline for document-quality outliers."""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

FEATURE_NAMES = ("page_count", "field_count", "text_chars_per_page", "mean_field_confidence", "ocr_page_fraction", "subject_count")


def features(extraction: dict[str, Any]) -> dict[str, float]:
    pages = extraction.get("pages") or []
    fields = extraction.get("extracted_fields") or []
    page_count = max(1, int(extraction.get("page_count") or len(pages) or 1))
    text_chars = sum(len(p.get("text", "")) for p in pages) if pages else len(extraction.get("raw_text", ""))
    confidences = [float(f.get("confidence", 0.0)) for f in fields if f.get("confidence") is not None]
    return {"page_count": float(page_count), "field_count": float(len(fields)),
            "text_chars_per_page": float(text_chars / page_count),
            "mean_field_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
            "ocr_page_fraction": sum("ocr" in p.get("method", "") for p in pages) / len(pages) if pages else float("ocr" in extraction.get("extraction_method", "")),
            "subject_count": float(len(extraction.get("subjects") or []))}


def train_model(normal_examples: list[dict[str, Any]], model_path: Path, *, contamination: float = 0.08,
                random_state: int = 17) -> dict[str, Any]:
    if len(normal_examples) < 8:
        raise ValueError("At least eight controlled normal examples are required")
    from sklearn.ensemble import IsolationForest
    import joblib
    matrix = [[features(item)[name] for name in FEATURE_NAMES] for item in normal_examples]
    medians = [statistics.median(column) for column in zip(*matrix)]
    iqrs = []
    for column in zip(*matrix):
        ordered = sorted(column)
        q1 = ordered[len(ordered)//4]
        q3 = ordered[(3*len(ordered))//4]
        iqrs.append(max(q3-q1, 1.0))
    model = IsolationForest(n_estimators=150, contamination=contamination, random_state=random_state)
    model.fit(matrix)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "feature_names": FEATURE_NAMES, "medians": medians, "iqrs": iqrs,
                 "training_examples": len(matrix), "baseline_type": "synthetic_or_local_unsupervised_quality_outlier"}, model_path)
    return {"model_path": str(model_path), "training_examples": len(matrix), "feature_names": list(FEATURE_NAMES),
            "contamination": contamination, "interpretation": "Unsupervised quality-outlier baseline; not a fraud classifier."}


def predict(extraction: dict[str, Any], model_path: Path) -> dict[str, Any]:
    if not model_path.exists():
        return {"status": "not_configured", "model": None, "score": None, "explanations": [],
                "message": "No trained anomaly baseline is installed. Deterministic checks are reported separately."}
    try:
        import joblib
        bundle = joblib.load(model_path)
        row = features(extraction)
        vector = [[row[name] for name in bundle["feature_names"]]]
        model = bundle["model"]
        score = float(model.decision_function(vector)[0])
        prediction = int(model.predict(vector)[0])
        deviations = [(abs(row[name]-median)/iqr, name, row[name], median) for name, median, iqr in zip(bundle["feature_names"], bundle["medians"], bundle["iqrs"])]
        explanations = [{"feature": name, "value": value, "training_median": median, "robust_deviation": round(deviation, 3)}
                        for deviation, name, value, median in sorted(deviations, reverse=True)[:3]]
        return {"status": "quality_outlier" if prediction < 0 else "within_baseline", "model": bundle.get("baseline_type"),
                "score": score, "explanations": explanations,
                "message": "Outlier screening is a review signal only; it does not indicate document fraud."}
    except Exception as exc:
        return {"status": "unavailable", "model": None, "score": None, "explanations": [], "message": f"Model unavailable ({type(exc).__name__}); deterministic checks remain available."}


def controlled_examples() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    normal = []
    for pages, count, chars, confidence, ocr, subjects in [(1,5,440,.8,0,0),(2,8,370,.82,0,5),(3,12,510,.78,0,8),(2,7,330,.84,.5,4),(1,6,500,.8,0,0),(3,10,290,.75,0,6),(2,9,460,.8,.5,5),(1,4,380,.85,0,0),(2,11,420,.77,0,7),(3,13,390,.79,.33,8),(1,6,470,.81,0,0),(2,8,355,.83,0,4)]:
        normal.append({"page_count":pages,"extracted_fields":[{"confidence":confidence} for _ in range(count)],
                      "raw_text":"x"*(chars*pages),"pages":[{"text":"x"*chars,"method":"embedded_pdf_text"} for _ in range(pages)],
                      "subjects":[{} for _ in range(subjects)]})
    anomalies = [
        {"page_count":1,"extracted_fields":[],"raw_text":"","pages":[{"text":"","method":"no_text"}],"subjects":[]},
        {"page_count":50,"extracted_fields":[{"confidence":.2}]*1,"raw_text":"x"*100,"pages":[{"text":"x"*2,"method":"local_tesseract_pdf_ocr"} for _ in range(50)],"subjects":[]},
        {"page_count":1,"extracted_fields":[{"confidence":.2}]*30,"raw_text":"x"*60000,"pages":[{"text":"x"*60000,"method":"embedded_pdf_text"}],"subjects":[{}]*40},
    ]
    return normal, anomalies, [0,1,1]
