"""Case navigation, saving, and the box table. Mixed into the panel class."""

from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Any, Dict, List

import qt
import slicer

from filenames import available_axial_thicknesses, thickness_display_label
from markups import DEFAULT_FINDING_LETTER


class WidgetActions:
    """Slots and case loading for BoundingBoxNavigatorWidget."""

    def on_scan_clicked(self):
        """Scan input directory, build case list, and initialize navigation."""
        input_path_str = self.input_dir_picker.currentPath.strip()
        output_path_str = self.output_dir_picker.currentPath.strip()

        if not input_path_str or not os.path.isdir(input_path_str):
            self.set_status("Please select a valid input scans folder.", is_error=True)
            return

        if not self._confirm_leave_case():
            return

        input_dir = Path(input_path_str)
        output_dir = Path(output_path_str) if output_path_str else (input_dir.parent / "annotations")
        if not output_path_str:
            self.output_dir_picker.setCurrentPath(str(output_dir))

        try:
            cases, warnings = self.logic.scan_folder(input_dir, output_dir)
        except Exception as exc:
            self.set_status(f"Scan failed: {exc}", is_error=True)
            slicer.util.errorDisplay(f"Scan failed:\n{exc}")
            return
        self._saved_signature = None

        if not cases:
            self.set_status(f"No supported medical scans found in {input_dir}.", is_error=True)
            self._update_progress_summary()
            self._populate_case_combobox()
            self._show_notice("Scan notes:", warnings)
            return

        self._populate_case_combobox()
        self._update_progress_summary()

        warning_info = f" ({len(warnings)} warning(s), see the pop-up)" if warnings else ""
        self.set_status(f"Scanned {len(cases)} case(s) successfully{warning_info}.")
        self._show_notice("Scan notes (check these files):", warnings)

        # Auto-load initial case
        skip_completed = self.skip_completed_checkbox.isChecked()
        initial_idx = self.logic.find_next_case_index(-1, step=1, skip_completed=skip_completed)
        if initial_idx is None:
            initial_idx = 0
            if skip_completed:
                self.set_status("All cases appear completed! You can review them with 'Skip completed' unchecked.")

        self.load_case_index(initial_idx)

    def _update_progress_summary(self):
        summary = self.logic.get_status_summary()
        self.progress_label.setText(
            f"Total: {summary['total']}  |  Completed: {summary['completed']}  |  Remaining: {summary['pending']}"
        )

    def _populate_case_combobox(self):
        self._updating_ui = True
        self.case_combobox.clear()
        total = len(self.logic.cases)
        for idx, c in enumerate(self.logic.cases):
            case_id = c["case_id"]
            completed = self.logic.is_case_completed(case_id)
            status_mark = "✓ " if completed else "   "
            item_text = f"[{idx + 1:02d}/{total:02d}] {status_mark}{case_id}"
            self.case_combobox.addItem(item_text, idx)
        self._updating_ui = False

    def load_case_index(self, index: int):
        """Load case and update all UI views."""
        if self._is_loading_case:
            return
        if index < 0 or index >= len(self.logic.cases):
            return
        if not self._confirm_leave_case():
            self._restore_case_combobox()
            return

        self._is_loading_case = True
        try:
            result = self.logic.load_case(index)
        except Exception as exc:
            self.set_status(f"Failed to load case {index + 1}: {exc}", is_error=True)
            traceback.print_exc()
            slicer.util.errorDisplay(f"Failed to load case {index + 1}:\n{exc}")
            return
        finally:
            self._is_loading_case = False

        case_info = result["case_info"]
        case_id = case_info["case_id"]

        self._updating_ui = True
        self.case_combobox.setCurrentIndex(index)
        self._rebuild_thickness_buttons(case_info)
        self._updating_ui = False

        self.case_details_label.setText(
            f"Case id: {case_id}\n"
            f"Scan number: {index + 1} out of {len(self.logic.cases)}"
        )
        self._update_plane_warning(result.get("load_warnings") or [])
        self._update_axial_file_label()

        self.notes_edit.setText(result.get("notes", ""))
        self.refresh_box_table()
        self._mark_saved()
        self._update_progress_summary()
        self._try_autoload_segmentations()

        warning_note = ""
        if result.get("load_warnings"):
            warning_note = " " + result["load_warnings"][0]
        self.set_status(f"Loaded case '{case_id}'. Press 'B' to add bounding boxes.{warning_note}")
        self._show_notice(
            "These files in the case folder could not be used. They are kept unchanged on disk:",
            result.get("load_problems") or [],
        )
        self._show_geometry_check()

    def _rebuild_thickness_buttons(self, case_info: Dict[str, Any]) -> None:
        while self.thickness_buttons_layout.count():
            item = self.thickness_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget:
                self.thickness_button_group.removeButton(widget)
                widget.deleteLater()
        self._thickness_buttons = {}

        volumes = case_info.get("volumes") or {}
        thicknesses = case_info.get("axial_thicknesses") or available_axial_thicknesses(volumes)
        active = self.logic.current_axial_thickness or case_info.get("default_axial_thickness")
        if not thicknesses:
            placeholder = qt.QLabel("No axial thicknesses found for this case.")
            self.thickness_buttons_layout.addWidget(placeholder)
            return

        for thickness in thicknesses:
            button = qt.QPushButton(thickness_display_label(thickness))
            button.setCheckable(True)
            button.setMinimumHeight(32)
            button.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
            button.setStyleSheet(
                "QPushButton { font-weight: bold; font-size: 12px; padding: 6px 10px; "
                "border-radius: 4px; border: 2px solid #90A4AE; }"
                "QPushButton:checked { background-color: #1565C0; color: white; border-color: #0D47A1; }"
            )
            button.setProperty("thickness", thickness)
            if thickness.upper() == "TS":
                button.setToolTip(
                    "Show the original axial thin-slice scan. Coronal and sagittal become "
                    "reconstructions of this axial volume."
                )
            else:
                button.setToolTip(
                    f"Show the {thickness_display_label(thickness)} series. "
                    "Native COR/SAG are used when that thickness exists; otherwise those "
                    "views are reconstructed from this axial volume."
                )
            self.thickness_button_group.addButton(button)
            self.thickness_buttons_layout.addWidget(button)
            self._thickness_buttons[thickness] = button
            if thickness == active:
                button.setChecked(True)

        if active and active in self._thickness_buttons:
            self._thickness_buttons[active].setChecked(True)
        elif self._thickness_buttons:
            next(iter(self._thickness_buttons.values())).setChecked(True)

    def _update_plane_warning(self, warnings: List[str]) -> None:
        if warnings:
            self.plane_warning_label.setText("Warning: " + " ".join(warnings))
            self.plane_warning_label.setVisible(True)
        else:
            self.plane_warning_label.setText("")
            self.plane_warning_label.setVisible(False)

    def _update_axial_file_label(self) -> None:
        filename = self.logic.active_axial_filename()
        thickness = self.logic.current_axial_thickness
        if not filename:
            self.axial_file_label.setText("Showing: none")
            return
        extra = ""
        if self.logic.cor_sag_are_reconstructions():
            extra = ". Coronal and sagittal are reconstructions of this axial scan"
        label = thickness_display_label(thickness) if thickness else "axial"
        self.axial_file_label.setText(f"Showing {label}: {filename}{extra}")

    def on_thickness_button_clicked(self, button) -> None:
        if self._updating_ui:
            return
        if self.logic.current_case is None:
            return
        if isinstance(button, int):
            button = self.thickness_button_group.button(button)
        if button is None:
            return
        thickness = button.property("thickness")
        if not thickness:
            return
        try:
            self.logic.set_slice_thickness(str(thickness))
            self._update_axial_file_label()
            self._show_geometry_check()
            self._update_plane_warning(self.logic.load_warnings)
            if self.logic.cor_sag_are_reconstructions():
                self.set_status(
                    f"Switched to {thickness_display_label(str(thickness))}. "
                    "Coronal and sagittal show reconstructions of the current axial scan."
                )
            else:
                self.set_status(
                    f"Switched to native {thickness_display_label(str(thickness))} AX/COR/SAG."
                )
        except Exception as exc:
            self.set_status(f"Could not switch slice thickness: {exc}", is_error=True)

    def refresh_box_table(self):
        """Populate the table widget with current ROI nodes and dimensions."""
        was_updating = self._updating_ui
        self._updating_ui = True
        self.box_table.setRowCount(0)
        for idx, roi in enumerate(self.logic.current_roi_nodes):
            self.box_table.insertRow(idx)

            visible_item = qt.QTableWidgetItem("")
            visible_item.setFlags(
                qt.Qt.ItemIsUserCheckable | qt.Qt.ItemIsEnabled | qt.Qt.ItemIsSelectable
            )
            is_visible = self.logic.is_roi_visible(roi) if roi else True
            visible_item.setCheckState(qt.Qt.Checked if is_visible else qt.Qt.Unchecked)
            visible_item.setToolTip(
                "Tick to show this box in the views, untick to hide it. "
                "Hidden boxes are still saved with the case."
            )

            name = roi.GetName() if roi else f"R_{idx + 1}"
            name_item = qt.QTableWidgetItem(name)
            name_item.setData(qt.Qt.UserRole, roi)
            name_item.setToolTip("Double-click to rename this box. The filename follows this name on save.")

            size_text = "(not placed)"
            if roi and self.logic.is_roi_valid(roi):
                size = roi.GetSize()
                size_text = f"{size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f}"

            finding_item = qt.QTableWidgetItem(self.logic.finding_label(roi) if roi else "")
            finding_item.setToolTip(
                "Double-click to type a finding (e.g. PM nodule, ascites, or any free text)."
            )

            size_item = qt.QTableWidgetItem(size_text)
            size_item.setFlags(size_item.flags() & ~qt.Qt.ItemIsEditable)

            self.box_table.setItem(idx, self.COL_VISIBLE, visible_item)
            self.box_table.setItem(idx, self.COL_NAME, name_item)
            self.box_table.setItem(idx, self.COL_FINDING, finding_item)
            self.box_table.setItem(idx, self.COL_SIZE, size_item)
        self._updating_ui = was_updating

    def _roi_for_row(self, row: int):
        """Return the ROI node stored on the Name cell of a table row, or None."""
        if row < 0 or row >= self.box_table.rowCount:
            return None
        name_item = self.box_table.item(row, self.COL_NAME)
        return name_item.data(qt.Qt.UserRole) if name_item else None

    def on_box_table_item_changed(self, item) -> None:
        if self._updating_ui or item is None:
            return
        roi_node = self._roi_for_row(item.row())
        if not roi_node:
            return
        column = item.column()
        if column == self.COL_VISIBLE:
            visible = item.checkState() == qt.Qt.Checked
            self.logic.set_roi_visibility(roi_node, visible)
            state = "shown" if visible else "hidden"
            self.set_status(f"Box '{roi_node.GetName()}' is now {state}.")
            return
        if column == self.COL_NAME:
            new_name = self.logic.rename_roi(roi_node, item.text())
            self._updating_ui = True
            item.setText(new_name)
            self._updating_ui = False
            self.set_status(f"Box name is now '{new_name}'.")
            return
        if column == self.COL_FINDING:
            label = self.logic.set_roi_finding_text(roi_node, item.text())
            self._updating_ui = True
            item.setText(label)
            self._updating_ui = False
            self.set_status(
                f"Finding for '{roi_node.GetName()}' is now '{label or 'none'}'."
            )

    def on_box_table_selection_changed(self):
        """When an ROI row is selected in the table, jump slice viewers to its center."""
        selected_rows = self.box_table.selectedItems()
        if not selected_rows:
            return
        roi_node = self._roi_for_row(selected_rows[0].row())
        if roi_node and self.logic.is_roi_valid(roi_node):
            self.logic.jump_to_roi(roi_node)

    def on_case_combobox_changed(self, combo_idx: int):
        if self._updating_ui:
            return
        if combo_idx >= 0:
            case_index = self.case_combobox.itemData(combo_idx)
            if case_index is not None and case_index != self.logic.current_index:
                self.load_case_index(case_index)

    def on_add_box_clicked(self):
        """Add new bounding box ROI and enter interactive placement."""
        if self.logic.current_case is None:
            self.set_status("Scan and load a case before adding bounding boxes.", is_error=True)
            return
        try:
            letter = self.finding_combo.itemData(self.finding_combo.currentIndex) or DEFAULT_FINDING_LETTER
            roi_node = self.logic.add_bounding_box(letter=str(letter))
            self.refresh_box_table()
            self.set_status(
                f"Placing '{roi_node.GetName()}' ({self.logic.finding_label(roi_node)}). "
                "Click and drag in any slice viewer to draw box."
            )
        except Exception as exc:
            self.set_status(f"Error adding bounding box: {exc}", is_error=True)

    def on_delete_box_clicked(self):
        """Delete currently selected bounding box."""
        selected_items = self.box_table.selectedItems()
        if not selected_items:
            self.set_status("Select a bounding box in the table to delete.")
            return
        roi_node = self._roi_for_row(selected_items[0].row())
        if roi_node:
            self.logic.delete_roi_node(roi_node)
            self.refresh_box_table()
            self.set_status("Deleted selected bounding box.")

    def on_delete_all_clicked(self):
        """Delete all bounding boxes for current case."""
        if not self.logic.current_roi_nodes:
            return
        confirmed = slicer.util.confirmYesNoDisplay(
            "Are you sure you want to delete ALL bounding boxes for this case?"
        )
        if confirmed:
            self.logic.delete_all_roi_nodes()
            self.refresh_box_table()
            self.set_status("Deleted all bounding boxes.")

    def on_save_clicked(self) -> bool:
        """Save current case annotations."""
        if self._is_saving:
            return False
        if self.logic.current_case is None:
            self.set_status("No case loaded to save.", is_error=True)
            return False

        self._is_saving = True
        try:
            notes = self.notes_edit.text.strip()
            saved = self.logic.save_current_case(
                notes=notes,
                confirm_zero_boxes_fn=slicer.util.confirmYesNoDisplay,
            )
            if not saved:
                self.set_status("Save canceled by user.")
                return False

            self.refresh_box_table()
            self._mark_saved()
            self._populate_case_combobox()
            self._update_progress_summary()
            case_id = self.logic.current_case["case_id"]
            num_boxes = len(self.logic.current_roi_nodes)
            self.set_status(f"Saved case '{case_id}' successfully ({num_boxes} boxes).")
            self._show_notice(
                "These boxes had no size, so they were removed from the case folder on save:",
                self.logic.last_dropped_boxes,
            )
            return True
        except Exception as exc:
            self.set_status(f"Save failed: {exc}", is_error=True)
            traceback.print_exc()
            slicer.util.errorDisplay(f"Save failed for case '{self.logic.current_case['case_id']}':\n{exc}")
            return False
        finally:
            self._is_saving = False

    def on_save_next_clicked(self):
        """Save current case and advance to next."""
        if self._is_saving:
            return
        if self.on_save_clicked():
            self._go_to_next_case()

    def on_next_no_save_clicked(self):
        """Advance to next case without saving."""
        self._go_to_next_case()

    def _go_to_next_case(self):
        skip_completed = self.skip_completed_checkbox.isChecked()
        next_idx = self.logic.find_next_case_index(
            self.logic.current_index, step=1, skip_completed=skip_completed
        )
        if next_idx is None:
            if skip_completed:
                msg = "No more uncompleted cases! All scanned scans are done."
            else:
                msg = "Reached the end of the case list."
            self.set_status(msg)
            slicer.util.infoDisplay(msg)
            return
        self.load_case_index(next_idx)

    def on_prev_clicked(self):
        """Go to previous case."""
        skip_completed = self.skip_completed_checkbox.isChecked()
        prev_idx = self.logic.find_next_case_index(
            self.logic.current_index, step=-1, skip_completed=skip_completed
        )
        if prev_idx is None:
            self.set_status("Already at the first case.")
            return
        self.load_case_index(prev_idx)

    def on_reload_clicked(self):
        """Reload current case from disk."""
        if self.logic.current_index >= 0:
            self.load_case_index(self.logic.current_index)

    def _try_autoload_segmentations(self) -> None:
        path_str = self.seg_path_picker.currentPath.strip()
        if not path_str or not os.path.exists(path_str):
            self.seg_status_label.setText("No segmentation loaded.")
            return
        path = Path(path_str)
        if not path.is_dir():
            self.seg_status_label.setText("No segmentation loaded. Click Load to overlay a file.")
            return
        case = self.logic.current_case
        if case is None:
            return
        try:
            names = self.logic.load_segmentations_from_path(
                path, case_id=case["case_id"], require_case_match=True
            )
            self.seg_status_label.setText(
                f"Loaded {len(names)} segmentation(s) for case {case['case_id']}: " + ", ".join(names)
            )
        except Exception as exc:
            self.seg_status_label.setText(f"No matching segmentation in folder ({exc}).")

    def on_load_segmentation_clicked(self):
        if self.logic.current_case is None:
            self.set_status("Load a case before adding a segmentation overlay.", is_error=True)
            return
        path_str = self.seg_path_picker.currentPath.strip()
        if not path_str or not os.path.exists(path_str):
            self.set_status("Select a segmentation file or folder first.", is_error=True)
            return
        try:
            names = self.logic.load_segmentations_from_path(
                Path(path_str), case_id=self.logic.current_case["case_id"]
            )
            self.seg_status_label.setText(f"Loaded {len(names)} segmentation(s): " + ", ".join(names))
            self.set_status(f"Loaded segmentation overlay ({len(names)}). .nii.gz files were imported as labelmaps.")
        except Exception as exc:
            self.set_status(f"Failed to load segmentation: {exc}", is_error=True)
            slicer.util.errorDisplay(f"Failed to load segmentation:\n{exc}")

    def on_clear_segmentation_clicked(self):
        self.logic.clear_segmentations()
        self.seg_status_label.setText("No segmentation loaded.")
        self.set_status("Cleared segmentation overlay.")
