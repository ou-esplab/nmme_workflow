from __future__ import annotations
from typing import Any, Dict, List
from pathlib import Path
import yaml

def load_config(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    with path.open("r") as f:
        return yaml.safe_load(f) or {}

def get_region(regions: List[dict], name: str) -> dict:
    for r in regions:
        if r.get("name") == name:
            return r
    raise ValueError(f"Region '{name}' not found in configuration.")

def resolve_models(global_models: List[str], region: dict) -> List[str]:
    if region.get("models"):
        return list(region["models"])
    return list(global_models)
