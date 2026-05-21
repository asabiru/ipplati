from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_wallet_export(path: str | Path) -> list[dict[str, Any]]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        if "deals" in payload and isinstance(payload["deals"], list):
            return payload["deals"]
        raise ValueError("Expected JSON object with a 'deals' list")
    if isinstance(payload, list):
        return payload
    raise ValueError("Expected JSON array or object with a 'deals' list")
