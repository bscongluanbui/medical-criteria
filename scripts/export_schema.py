import json
from pathlib import Path
from app.schemas import Card

root = Path(__file__).resolve().parents[1]
target = root / "config/knowledge-card.schema.json"
target.write_text(json.dumps(Card.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
print("SCHEMA_EXPORTED")
