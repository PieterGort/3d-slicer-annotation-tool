"""Bounding Box Navigator panel. Loaded by BoundingBoxNavigator.py."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import ctk
import qt
import slicer
from slicer.ScriptedLoadableModule import ScriptedLoadableModuleWidget

from filenames import (
    SETTINGS_KEY_INPUT_DIR,
    SETTINGS_KEY_OUTPUT_DIR,
    SETTINGS_KEY_SEG_PATH,
    SETTINGS_KEY_SKIP_COMPLETED,
)
from markups import FINDING_TYPES
from widget_actions import WidgetActions
from widget_guard import UnsavedChangesGuard

class BoundingBoxNavigatorWidget(WidgetActions, UnsavedChangesGuard, ScriptedLoadableModuleWidget):
    """GUI widget for Bounding Box Navigator in Slicer."""

    # Box table column indices
    COL_VISIBLE = 0
    COL_NAME = 1
    COL_FINDING = 2
    COL_SIZE = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        import BoundingBoxNavigator as bbn

        self.logic = bbn.BoundingBoxNavigatorLogic()
        self.shortcut_b: Optional[qt.QShortcut] = None
        self.shortcut_shift_b: Optional[qt.QShortcut] = None
        self._updating_ui: bool = False
        self._is_saving: bool = False
        self._is_loading_case: bool = False
        self._saved_signature: Optional[str] = None
        self._thickness_buttons: Dict[str, Any] = {}

    def setup(self):
        super().setup()

        # -------------------------------------------------------------
        # Section 1: Directories & Setup
        # -------------------------------------------------------------
        setup_collapsible = ctk.ctkCollapsibleButton()
        setup_collapsible.text = "1. Setup & Folders"
        self.layout.addWidget(setup_collapsible)
        setup_layout = qt.QFormLayout(setup_collapsible)

        # Input Directory Picker
        self.input_dir_picker = ctk.ctkPathLineEdit()
        self.input_dir_picker.filters = ctk.ctkPathLineEdit.Dirs
        self.input_dir_picker.toolTip = "Directory containing patient CT scan volumes (.nii.gz, .nrrd, etc.)"
        saved_input = slicer.util.settingsValue(SETTINGS_KEY_INPUT_DIR, "")
        if saved_input:
            self.input_dir_picker.setCurrentPath(saved_input)
        setup_layout.addRow("Input Scans Folder:", self.input_dir_picker)

        # Output Directory Picker
        self.output_dir_picker = ctk.ctkPathLineEdit()
        self.output_dir_picker.filters = ctk.ctkPathLineEdit.Dirs
        self.output_dir_picker.toolTip = "Directory where per-case folders and .mrk.json boxes will be saved."
        saved_output = slicer.util.settingsValue(SETTINGS_KEY_OUTPUT_DIR, "")
        if saved_output:
            self.output_dir_picker.setCurrentPath(saved_output)
        setup_layout.addRow("Output Annotations Folder:", self.output_dir_picker)

        # Scan Button and Options row
        scan_row = qt.QHBoxLayout()
        self.scan_button = qt.QPushButton("Scan Folder")
        self.scan_button.setStyleSheet("font-weight: bold; padding: 4px 12px;")
        scan_row.addWidget(self.scan_button)

        self.skip_completed_checkbox = qt.QCheckBox("Skip completed cases during navigation")
        saved_skip = slicer.util.settingsValue(
            SETTINGS_KEY_SKIP_COMPLETED, True, converter=slicer.util.toBool
        )
        self.skip_completed_checkbox.setChecked(saved_skip)
        scan_row.addWidget(self.skip_completed_checkbox)
        scan_row.addStretch(1)
        setup_layout.addRow(scan_row)

        # Progress / Status label
        self.progress_label = qt.QLabel("No folder scanned yet.")
        self.progress_label.setStyleSheet("font-weight: bold; color: #1565C0;")
        setup_layout.addRow("Progress:", self.progress_label)

        self.status_label = qt.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        setup_layout.addRow("Status:", self.status_label)

        # -------------------------------------------------------------
        # Section 2: Case Navigation
        # -------------------------------------------------------------
        nav_collapsible = ctk.ctkCollapsibleButton()
        nav_collapsible.text = "2. Case Navigation"
        self.layout.addWidget(nav_collapsible)
        nav_layout = qt.QVBoxLayout(nav_collapsible)

        # Case selection combobox row
        case_select_row = qt.QHBoxLayout()
        case_select_row.addWidget(qt.QLabel("Active Case:"))
        self.case_combobox = qt.QComboBox()
        self.case_combobox.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
        case_select_row.addWidget(self.case_combobox, 1)
        nav_layout.addLayout(case_select_row)

        # Case Details label
        self.case_details_label = qt.QLabel("No case loaded.")
        self.case_details_label.setWordWrap(True)
        self.case_details_label.setStyleSheet(
            "padding: 8px; font-size: 13px; background-color: rgba(128,128,128,0.1); border-radius: 4px;"
        )
        nav_layout.addWidget(self.case_details_label)

        self.plane_warning_label = qt.QLabel("")
        self.plane_warning_label.setWordWrap(True)
        self.plane_warning_label.setStyleSheet("color: #E65100; font-weight: bold; padding: 2px 4px;")
        self.plane_warning_label.setVisible(False)
        nav_layout.addWidget(self.plane_warning_label)

        thickness_header = qt.QLabel("Slice thickness:")
        thickness_header.setStyleSheet("font-weight: bold; margin-top: 6px;")
        nav_layout.addWidget(thickness_header)

        self.thickness_buttons_widget = qt.QWidget()
        self.thickness_buttons_layout = qt.QHBoxLayout(self.thickness_buttons_widget)
        self.thickness_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.thickness_button_group = qt.QButtonGroup(self.thickness_buttons_widget)
        self.thickness_button_group.setExclusive(True)
        nav_layout.addWidget(self.thickness_buttons_widget)
        self._thickness_buttons: Dict[str, Any] = {}

        self.axial_file_label = qt.QLabel("Showing: none")
        self.axial_file_label.setWordWrap(True)
        self.axial_file_label.setStyleSheet("color: #555; font-size: 11px; padding: 2px 0;")
        nav_layout.addWidget(self.axial_file_label)

        # Primary Navigation buttons
        button_row_1 = qt.QHBoxLayout()
        self.prev_button = qt.QPushButton("◀ Previous")
        self.next_no_save_button = qt.QPushButton("Next (No Save) ▶")
        self.reload_button = qt.QPushButton("⟳ Reload")

        button_row_1.addWidget(self.prev_button)
        button_row_1.addWidget(self.next_no_save_button)
        button_row_1.addWidget(self.reload_button)
        nav_layout.addLayout(button_row_1)

        button_row_2 = qt.QHBoxLayout()
        self.save_button = qt.QPushButton("💾 Save")
        self.save_button.setStyleSheet("font-weight: bold; padding: 6px;")

        self.save_next_button = qt.QPushButton("💾 Save + Next ▶")
        self.save_next_button.setStyleSheet(
            "font-weight: bold; font-size: 13px; padding: 8px; "
            "background-color: #2E7D32; color: white; border-radius: 4px;"
        )
        button_row_2.addWidget(self.save_button)
        button_row_2.addWidget(self.save_next_button, 1)
        nav_layout.addLayout(button_row_2)

        # -------------------------------------------------------------
        # Section 3: Bounding Boxes Annotation & Review
        # -------------------------------------------------------------
        box_collapsible = ctk.ctkCollapsibleButton()
        box_collapsible.text = "3. Bounding Boxes (Markups ROI)"
        self.layout.addWidget(box_collapsible)
        box_layout = qt.QVBoxLayout(box_collapsible)

        finding_row = qt.QHBoxLayout()
        finding_row.addWidget(qt.QLabel("Finding type:"))
        self.finding_combo = qt.QComboBox()
        for letter, label, _class_id in FINDING_TYPES:
            self.finding_combo.addItem(f"{label}  ({letter})", letter)
        self.finding_combo.setCurrentIndex(0)
        self.finding_combo.setToolTip(
            "Saved as <letter>_<n>.mrk.json. PM nodule (R) is the default. "
            "R boxes ≥ 30 mm are labelled pm_confluent automatically on save."
        )
        finding_row.addWidget(self.finding_combo, 1)
        box_layout.addLayout(finding_row)

        # Box Action Buttons
        box_actions_row = qt.QHBoxLayout()
        self.add_box_button = qt.QPushButton("➕ Add Bounding Box (Shortcut: B)")
        self.add_box_button.setStyleSheet(
            "font-weight: bold; padding: 6px; background-color: #1976D2; color: white; border-radius: 4px;"
        )
        self.delete_box_button = qt.QPushButton("✖ Delete Selected")
        self.delete_all_button = qt.QPushButton("🗑 Delete All")

        box_actions_row.addWidget(self.add_box_button, 2)
        box_actions_row.addWidget(self.delete_box_button, 1)
        box_actions_row.addWidget(self.delete_all_button, 1)
        box_layout.addLayout(box_actions_row)

        # Box Table Widget
        # Columns: 0 = Show (visibility checkbox), 1 = Name, 2 = Finding, 3 = Size
        self.box_table = qt.QTableWidget(0, 4)
        self.box_table.setHorizontalHeaderLabels(["Show", "Name", "Finding", "Size (W × L × H mm)"])
        self.box_table.horizontalHeader().setStretchLastSection(True)
        self.box_table.horizontalHeader().setSectionResizeMode(
            self.COL_VISIBLE, qt.QHeaderView.ResizeToContents
        )
        self.box_table.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
        self.box_table.setSelectionMode(qt.QAbstractItemView.SingleSelection)
        self.box_table.setFixedHeight(140)
        box_layout.addWidget(self.box_table)

        # Notes / PCI Score row
        notes_row = qt.QHBoxLayout()
        notes_row.addWidget(qt.QLabel("Notes / PCI:"))
        self.notes_edit = qt.QLineEdit()
        self.notes_edit.setPlaceholderText("Optional notes or PCI score for this case")
        notes_row.addWidget(self.notes_edit, 1)
        box_layout.addLayout(notes_row)

        # Workflow hint
        hint_label = qt.QLabel(
            "Workflow: Choose a finding type (PM nodule is default), press 'B', then click and drag "
            "in any slice. New boxes start as R_1, A_1, … ; double-click the Name cell to rename any "
            "box, and type in the Finding cell to set or change its type. Untick 'Show' to hide a box "
            "while you inspect the scan (hidden boxes are still saved). Use the large thickness buttons "
            "to switch series; if a thickness has no native COR/SAG, those views show reconstructions "
            "of the current axial volume."
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: #666; font-size: 11px; padding: 2px;")
        box_layout.addWidget(hint_label)

        # -------------------------------------------------------------
        # Section 4: Segmentation overlay
        # -------------------------------------------------------------
        seg_collapsible = ctk.ctkCollapsibleButton()
        seg_collapsible.text = "4. Segmentation"
        self.layout.addWidget(seg_collapsible)
        seg_layout = qt.QVBoxLayout(seg_collapsible)

        self.seg_path_picker = ctk.ctkPathLineEdit()
        self.seg_path_picker.filters = ctk.ctkPathLineEdit.Files | ctk.ctkPathLineEdit.Dirs
        self.seg_path_picker.nameFilters = ["Segmentations (*.seg.nrrd *.nii.gz *.nii)", "All files (*)"]
        self.seg_path_picker.toolTip = (
            "A single .seg.nrrd / .nii.gz file, or a folder of segmentations for this scan. "
            ".nii.gz files are loaded as labelmap segmentations (not as CT volumes)."
        )
        saved_seg = slicer.util.settingsValue(SETTINGS_KEY_SEG_PATH, "")
        if saved_seg:
            self.seg_path_picker.setCurrentPath(saved_seg)
        seg_layout.addWidget(qt.QLabel("Segmentation file or folder:"))
        seg_layout.addWidget(self.seg_path_picker)

        seg_btn_row = qt.QHBoxLayout()
        self.load_seg_button = qt.QPushButton("Load segmentation")
        self.load_seg_button.setStyleSheet(
            "font-weight: bold; padding: 8px; background-color: #6A1B9A; color: white; border-radius: 4px;"
        )
        self.clear_seg_button = qt.QPushButton("Clear overlay")
        seg_btn_row.addWidget(self.load_seg_button, 2)
        seg_btn_row.addWidget(self.clear_seg_button, 1)
        seg_layout.addLayout(seg_btn_row)

        self.seg_status_label = qt.QLabel("No segmentation loaded.")
        self.seg_status_label.setWordWrap(True)
        self.seg_status_label.setStyleSheet("color: #555; font-size: 11px;")
        seg_layout.addWidget(self.seg_status_label)

        # Add vertical stretch
        self.layout.addStretch(1)

        # Connect signals
        self._connect_signals()

        # Keyboard shortcuts for Add Bounding Box: 'b' and 'Shift+B'
        main_win = slicer.util.mainWindow()
        if main_win:
            self.shortcut_b = qt.QShortcut(qt.QKeySequence("b"), main_win)
            self.shortcut_b.activated.connect(self.on_add_box_clicked)
            self.shortcut_shift_b = qt.QShortcut(qt.QKeySequence("Shift+b"), main_win)
            self.shortcut_shift_b.activated.connect(self.on_add_box_clicked)

        # Auto-scan if paths already set
        if saved_input and os.path.isdir(saved_input):
            self.on_scan_clicked()

    def _connect_signals(self):
        self.input_dir_picker.currentPathChanged.connect(self.on_input_dir_changed)
        self.output_dir_picker.currentPathChanged.connect(self.on_output_dir_changed)
        self.skip_completed_checkbox.toggled.connect(self.on_skip_completed_toggled)
        self.scan_button.clicked.connect(self.on_scan_clicked)

        self.case_combobox.currentIndexChanged.connect(self.on_case_combobox_changed)
        self.prev_button.clicked.connect(self.on_prev_clicked)
        self.next_no_save_button.clicked.connect(self.on_next_no_save_clicked)
        self.reload_button.clicked.connect(self.on_reload_clicked)
        self.save_button.clicked.connect(self.on_save_clicked)
        self.save_next_button.clicked.connect(self.on_save_next_clicked)
        self.thickness_button_group.buttonClicked.connect(self.on_thickness_button_clicked)

        self.add_box_button.clicked.connect(self.on_add_box_clicked)
        self.seg_path_picker.currentPathChanged.connect(self.on_seg_path_changed)
        self.load_seg_button.clicked.connect(self.on_load_segmentation_clicked)
        self.clear_seg_button.clicked.connect(self.on_clear_segmentation_clicked)
        self.delete_box_button.clicked.connect(self.on_delete_box_clicked)
        self.delete_all_button.clicked.connect(self.on_delete_all_clicked)
        self.box_table.itemSelectionChanged.connect(self.on_box_table_selection_changed)
        self.box_table.itemChanged.connect(self.on_box_table_item_changed)

    def cleanup(self):
        if self.shortcut_b:
            self.shortcut_b.disconnect()
            self.shortcut_b = None
        if self.shortcut_shift_b:
            self.shortcut_shift_b.disconnect()
            self.shortcut_shift_b = None

    def on_input_dir_changed(self, new_path: str):
        slicer.app.settings().setValue(SETTINGS_KEY_INPUT_DIR, new_path)

    def on_output_dir_changed(self, new_path: str):
        slicer.app.settings().setValue(SETTINGS_KEY_OUTPUT_DIR, new_path)

    def on_seg_path_changed(self, new_path: str):
        slicer.app.settings().setValue(SETTINGS_KEY_SEG_PATH, new_path)

    def on_skip_completed_toggled(self, checked: bool):
        slicer.app.settings().setValue(SETTINGS_KEY_SKIP_COMPLETED, checked)

    def set_status(self, text: str, is_error: bool = False):
        self.status_label.setText(text)
        if is_error:
            self.status_label.setStyleSheet("color: #b00020; font-weight: bold;")
        else:
            self.status_label.setStyleSheet("color: #2E7D32;")
        print(text)

