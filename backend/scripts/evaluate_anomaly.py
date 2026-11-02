"""Evaluate the anomaly baseline only against its declared controlled synthetic cases."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.anomaly import controlled_examples, train_model, predict

ROOT = Path(__file__).resolve().parents[1]
normal, cases, labels = controlled_examples()
model_path = Path(os.getenv("DOCVERIFY_MODEL_PATH", ROOT / "data" / "models" / "isolation-forest.joblib"))
train_model(normal, model_path)
rows = [(predict(item, model_path), label) for item, label in zip(cases, labels)]
counts = {"tp": sum(r["status"] == "quality_outlier" and y == 1 for r, y in rows),
          "fp": sum(r["status"] == "quality_outlier" and y == 0 for r, y in rows),
          "tn": sum(r["status"] == "within_baseline" and y == 0 for r, y in rows),
          "fn": sum(r["status"] == "within_baseline" and y == 1 for r, y in rows)}
counts["precision"] = counts["tp"] / max(1, counts["tp"] + counts["fp"])
counts["recall"] = counts["tp"] / max(1, counts["tp"] + counts["fn"])
print(json.dumps({"scope": "controlled_synthetic_cases_only", "n_normal_train": len(normal),
                  "n_evaluation": len(cases), "confusion_counts": counts,
                  "warning": "These values are a software smoke test, not an accuracy estimate."}, indent=2))

