"""
Append-only evidence manifest logger.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.cases import ensure_case, case_path


def _sha256_json(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def append_evidence(
    *,
    action: str,
    source: str,
    query: Optional[Dict[str, Any]] = None,
    response: Optional[Any] = None,
    case_id: Optional[str] = None,
) -> None:
    if case_id:
        case = ensure_case(case_id)
        target = case_path(case["case_id"]) / "artifacts" / "evidence_manifest.jsonl"
    else:
        root = Path(__file__).resolve().parents[1]
        target = root / "evidence_manifest.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": int(time.time()),
        "action": action,
        "source": source,
        "query": query or {},
        "response_hash": _sha256_json(response if response is not None else {}),
        "case_id": case_id or None,
    }
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
