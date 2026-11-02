"""Save rules, unsaved-change checks, and notice text. No 3D Slicer import."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional


def filenames_to_delete(
    existing_names: Iterable[str],
    planned: List[dict],
    protected: Iterable[str] = (),
) -> List[str]:
    """
    Markup files in the case folder that a save replaces.

    A file stays when the save plans it again or when it is protected (it could not be
    loaded, so the tool does not know its content).
    """
    kept = {item["filename"] for item in planned} | set(protected)
    return [name for name in existing_names if name not in kept]


def state_signature(box_states: List[Dict[str, Any]], notes: str) -> str:
    """Stable text that describes the boxes and notes of the current case."""
    payload = {"boxes": box_states, "notes": str(notes or "").strip()}
    return json.dumps(payload, sort_keys=True, default=str)


def differs_from_saved(baseline: Optional[str], current: str) -> bool:
    """True when the case changed since it was loaded or saved. No baseline means no change."""
    return baseline is not None and baseline != current


def format_notice(title: str, items: Iterable[str], limit: int = 12) -> str:
    """Title plus a bullet per item, capped at limit. Empty items give an empty string."""
    values = list(items)
    if not values:
        return ""
    lines = [title] + [f"- {item}" for item in values[:limit]]
    if len(values) > limit:
        lines.append(f"... and {len(values) - limit} more.")
    return "\n".join(lines)
