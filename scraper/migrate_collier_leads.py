from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .collier_score import score_collier_lead
except ImportError:  # pragma: no cover
    from collier_score import score_collier_lead


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "collier_leads.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def migrate(path: Path = DATA_PATH) -> dict[str, int]:
    if not path.exists():
        print("data/collier_leads.json not found; nothing to migrate.")
        return {"processed": 0, "failed": 0}

    payload = json.loads(path.read_text(encoding="utf-8"))
    leads = payload.get("leads") if isinstance(payload.get("leads"), list) else []
    processed = 0
    failed = 0
    migrated: list[dict[str, Any]] = []

    for lead in leads:
        try:
            migrated.append(score_collier_lead(dict(lead)))
            processed += 1
        except Exception as exc:  # keep malformed records instead of crashing
            failed += 1
            print(f"Collier migration skipped malformed record {lead.get('owner_name', 'unknown')}: {exc}")
            migrated.append(lead)

    payload["leads"] = migrated
    payload["lead_count"] = len(migrated)
    payload["active_lead_count"] = sum(1 for lead in migrated if not lead.get("is_expired"))
    payload["fire_lead_count"] = sum(1 for lead in migrated if not lead.get("is_expired") and lead.get("tier") == "FIRE")
    payload["critical_count"] = sum(1 for lead in migrated if not lead.get("is_expired") and lead.get("fl_urgency") == "CRITICAL")
    payload["hot_fee_window_count"] = sum(1 for lead in migrated if not lead.get("is_expired") and lead.get("fee_tier") == "HIGH_URGENCY_15PCT")
    payload["expired_count"] = sum(1 for lead in migrated if lead.get("is_expired"))
    payload["migration_meta"] = {
        "migrated_at": now_iso(),
        "processed": processed,
        "failed": failed,
        "fixes": [
            "expired_leads_dead",
            "fl_entity_classification",
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Collier migration complete: {processed} processed | {failed} failed")
    return {"processed": processed, "failed": failed}


if __name__ == "__main__":
    migrate()
