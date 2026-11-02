from pathlib import Path
import sys
import os
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.anomaly import controlled_examples, train_model

ROOT = Path(__file__).resolve().parents[1]
normal, _, _ = controlled_examples()
result = train_model(normal, Path(os.getenv("DOCVERIFY_MODEL_PATH", ROOT / "data" / "models" / "isolation-forest.joblib")))
print(result)
