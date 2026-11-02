"""Unsaved-change prompts and notices for the panel. Mixed into the panel class."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import slicer

from session import differs_from_saved, format_notice, state_signature

NOTICE_TITLE = "Bounding Box Navigator"


class UnsavedChangesGuard:
    """Remembers the saved state of the current case and asks before leaving it."""

    _saved_signature: Optional[str] = None

    def _box_states(self) -> List[Dict[str, Any]]:
        """Box name, finding, visibility, and geometry, rounded so that noise does not count."""
        states: List[Dict[str, Any]] = []
        for roi in self.logic.current_roi_nodes:
            states.append(
                {
                    "name": roi.GetName() or "",
                    "finding": self.logic.finding_label(roi),
                    "visible": self.logic.is_roi_visible(roi),
                    "center": [round(float(v), 3) for v in roi.GetCenter()],
                    "size": [round(float(v), 3) for v in roi.GetSize()],
                }
            )
        return states

    def _current_signature(self) -> str:
        return state_signature(self._box_states(), self.notes_edit.text)

    def _mark_saved(self) -> None:
        """Record the state that matches disk. Call after a load or a save."""
        self._saved_signature = self._current_signature()

    def _confirm_leave_case(self) -> bool:
        """True when the physician may leave the current case. Asks if there are unsaved changes."""
        if self.logic.current_case is None:
            return True
        if not differs_from_saved(self._saved_signature, self._current_signature()):
            return True
        case_id = self.logic.current_case["case_id"]
        return bool(
            slicer.util.confirmYesNoDisplay(
                f"Case '{case_id}' has changes that are not saved.\n\n"
                "Leave this case without saving? The changes will be lost."
            )
        )

    def _restore_case_combobox(self) -> None:
        """Put the case list back on the case that is still loaded."""
        was_updating = self._updating_ui
        self._updating_ui = True
        self.case_combobox.setCurrentIndex(self.logic.current_index)
        self._updating_ui = was_updating

    def _show_notice(self, title: str, items: List[str]) -> None:
        """Show a pop-up with the items, or nothing when there are no items."""
        text = format_notice(title, items)
        if text:
            slicer.util.infoDisplay(text, windowTitle=NOTICE_TITLE)
