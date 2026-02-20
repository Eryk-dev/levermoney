"""
Sync Conta Azul categories to a local JSON file.

Fetches all income/expense categories from the CA API daily and saves them
to ca_categories.json at the project root so the rest of the application
can do fast offline lookups without hitting the API on every request.

Exposed functions:
  sync_ca_categories()     — fetch + save (called by scheduler and admin endpoint)
  get_last_sync_result()   — status of the last run
  load_categories()        — read the saved file (offline)
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Written next to the project root (same level as app/, Dockerfile, etc.)
_CATEGORIES_FILE = Path(__file__).resolve().parent.parent.parent / "ca_categories.json"

_last_sync_result: dict = {}


async def sync_ca_categories() -> dict:
    """Fetch all CA categories and persist them to ca_categories.json.

    Returns {"status": "ok", "count": int, "file": str, "synced_at": str}.
    Raises on failure (caller is responsible for error handling).
    """
    from app.services.ca_api import listar_categorias

    logger.info("CaCategoriesSync: fetching categories from CA API...")
    categories = await listar_categorias()
    synced_at = datetime.now(timezone.utc).isoformat()

    payload = {
        "synced_at": synced_at,
        "count": len(categories),
        "categories": categories,
    }

    _CATEGORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CATEGORIES_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "CaCategoriesSync: saved %d categories to %s", len(categories), _CATEGORIES_FILE
    )

    result = {
        "status": "ok",
        "count": len(categories),
        "file": str(_CATEGORIES_FILE),
        "synced_at": synced_at,
    }
    _last_sync_result.clear()
    _last_sync_result.update(result)
    return result


def get_last_sync_result() -> dict:
    """Return the result of the last sync run (in-memory or from file metadata)."""
    if _last_sync_result:
        return dict(_last_sync_result)

    # No in-memory state yet — check if the file already exists from a previous run
    if _CATEGORIES_FILE.exists():
        try:
            data = json.loads(_CATEGORIES_FILE.read_text(encoding="utf-8"))
            return {
                "status": "ok",
                "count": data.get("count", 0),
                "file": str(_CATEGORIES_FILE),
                "synced_at": data.get("synced_at"),
                "note": "metadata from existing file (no in-memory state)",
            }
        except Exception:
            pass

    return {"status": "never_run"}


def load_categories() -> list[dict]:
    """Load categories from the local JSON file for offline lookups.

    Returns an empty list if the file doesn't exist or is malformed.
    """
    if not _CATEGORIES_FILE.exists():
        return []
    try:
        data = json.loads(_CATEGORIES_FILE.read_text(encoding="utf-8"))
        return data.get("categories", [])
    except Exception as exc:
        logger.warning("CaCategoriesSync: failed to read categories file: %s", exc)
        return []
