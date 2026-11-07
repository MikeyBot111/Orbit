"""
Local case workspace storage.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "cases"


def _safe_case_id(raw: str) -> str:
    base = (raw or "").strip().lower()
    base = re.sub(r"[^a-z0-9_-]+", "-", base).strip("-")
    return base[:64] or f"case-{int(time.time())}"


def case_path(case_id: str) -> Path:
    return CASES_DIR / _safe_case_id(case_id)


def ensure_case(case_id: str, title: Optional[str] = None) -> Dict:
    cid = _safe_case_id(case_id)
    cdir = case_path(cid)
    adir = cdir / "artifacts"
    cdir.mkdir(parents=True, exist_ok=True)
    adir.mkdir(parents=True, exist_ok=True)
    manifest = cdir / "case.json"
    notes = cdir / "notes.md"
    if not manifest.exists():
        manifest.write_text(
            json.dumps(
                {
                    "case_id": cid,
                    "title": title or cid,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "finding_count": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    if not notes.exists():
        notes.write_text(f"# Case {cid}\n\n", encoding="utf-8")
    return load_case(cid) or {"case_id": cid, "title": title or cid}


def load_case(case_id: str) -> Optional[Dict]:
    path = case_path(case_id) / "case.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_cases() -> List[Dict]:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    out: List[Dict] = []
    for cdir in sorted(CASES_DIR.iterdir(), key=lambda p: p.name):
        if not cdir.is_dir():
            continue
        manifest = cdir / "case.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            out.append(data)
        except Exception:
            continue
    out.sort(key=lambda c: c.get("updated_at", 0), reverse=True)
    return out


def save_finding(case_id: str, finding: Dict) -> Dict:
    case = ensure_case(case_id)
    cid = case["case_id"]
    cdir = case_path(cid)
    findings_path = cdir / "artifacts" / "findings.jsonl"
    entry = {
        "saved_at": int(time.time()),
        "finding": finding,
    }
    with findings_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    manifest_path = cdir / "case.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["updated_at"] = int(time.time())
    data["finding_count"] = int(data.get("finding_count", 0)) + 1
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
