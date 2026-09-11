# 3D Slicer Bounding Box Navigator

A streamlined 3D Slicer extension designed for radiologists to quickly navigate through a folder of medical imaging scans (CT volumes) and annotate 3D bounding boxes (Markups ROI). Each bounding box is automatically saved as its own `.mrk.json` file in a dedicated case directory.

> 📄 **Radiologist User Manual:** A complete, printable PDF guide is available in this repository: [**`how-to-use.pdf`**](how-to-use.pdf). Please share this PDF directly with the radiologist!

---

## Key Features

- **Fast Case-by-Case Navigation:** Step sequentially through a cohort of scans with one click (`Save + Next`).
- **Native AX / COR / SAG views:** Files named `{case_id}_{AX|COR|SAG}_{phase}_{3mm|TS}.nii.gz` are grouped into one case. The axial, coronal, and sagittal viewers each show the original acquisition (not a reconstruction from a single volume).
- **Axial thin-slice (TS) toggle:** Switch the axial viewer between 3 mm and thin slices for small nodules. Coronal and sagittal stay on 3 mm.
- **Automatic Case ID Extraction:** Reads the case ID from the `{case_id}_...` filename pattern (or a 4- or 5-digit run in unmatched names).
- **Direct 3D Bounding Box Annotation:** Press **`B`** to immediately draw interactive bounding boxes on any 2D slice view or 3D view.
- **Per-Box `.mrk.json` Outputs:** Saves Slicer's native Markups JSON (`<case_id>_box_01.mrk.json`) for each lesion.
- **Notes & PCI Score Support:** Field to record PCI scores or clinical observations stored alongside the annotations in `_annotation_done.json`.
- **Skip Completed Cases:** Automatically resumes where you left off; uncheck the option anytime to review prior cases.
- **Click-to-Jump:** Selecting any box in the table automatically centers all orthogonal slice viewers (Axial, Sagittal, Coronal) on that box.

---

## Installation & Setup (Windows 11)

### 1. Install 3D Slicer
1. Download **3D Slicer 5.x** (version 5.6 or newer recommended) from [slicer.org](https://download.slicer.org/).
2. Run the Windows installer and finish setup using default options.

### 2. Download / Unzip this Tool
1. Download the zip archive (e.g. `3d-slicer-annotation-tool-main.zip`).
2. **Important:** Right-click the `.zip` file and choose **Extract All...** (Alles uitpakken).
   *(Do not run directly from inside the zip file without extracting, or Windows will not extract the required subfolders).*
3. Choose a permanent location, e.g. `C:\Users\YourName\Documents\3d-slicer-annotation-tool`.

---

## Launching the Tool

### Option A: Double-Click the Launcher
Inside the extracted folder, double-click **`launch_windows.bat`**.
- It automatically detects your Slicer installation (including Start Menu shortcuts), pre-loads the module, and opens Slicer directly into **Bounding Box Navigator**.
- *(On Linux, run `./launch_linux.sh`)*.

### Option B: Manual 1-Time Setup inside 3D Slicer (Most Robust)
If you prefer to start Slicer normally from your desktop or Start Menu without a batch file:
1. Open 3D Slicer.
2. In the top menu, go to **Edit** > **Application Settings** > **Modules**.
3. Under **Additional module paths**, click **Add** and select the `BoundingBoxNavigator` directory inside the extracted tool.
4. *(Optional & Recommended)*: In the same settings page, set **Default module** to **Bounding Box Navigator**. That way, every time you start Slicer, it opens straight into your annotation tool!
5. Click **OK** and restart Slicer when prompted.
6. The module is now permanently installed. You can also find it anytime in the module dropdown under **Annotation** > **Bounding Box Navigator**.

---

## Step-by-Step Annotation Workflow

### 0. Open the Module (if panel is not visible)
If **BoundingBoxNavigator** did not open automatically on startup:
- In the top toolbar, go to: **Modules** > **Annotation** > **BoundingBoxNavigator** to display the tool panel on the right.

### 1. Select Folders & Scan
1. In the **1. Setup & Folders** section:
   - **Input Scans Folder:** Choose the folder containing your CT scan volumes (`.nii.gz`, `.nii`, `.nrrd`, `.mha`). For the radiologist package, use files named `{case_id}_{AX|COR|SAG}_{phase}_{3mm|TS}.nii.gz`.
   - **Output Annotations Folder:** Choose where annotations should be saved.
2. Click **Scan Folder**.
   - Matching files are grouped into one case per `case_id` (e.g. all `1001_*.nii.gz` files become case 1001).
   - Cases are sorted naturally by case ID (e.g. case 1, 2, ..., 10).
   - The first unannotated case loads in Four-Up layout: **Red = original axial**, **Green = original coronal**, **Yellow = original sagittal**, with standard CT-Abdomen windowing (Window: 350, Level: 40).
   - Tick **Axial view: thin slices (TS)** when a small nodule is hard to see on 3 mm. This swaps only the axial viewer; coronal and sagittal stay on 3 mm. The checkbox is disabled if the case has no `*_AX_*_TS` file.

### 2. Draw Bounding Boxes
1. Press keyboard key **`B`** (or click **➕ Add Bounding Box**).
2. In any 2D slice view (Red, Yellow, or Green), **click and drag** across the lesion to define its initial bounds.
3. Use the colored 3D handles on the box to resize or move the box in all three dimensions.
4. If a scan has multiple lesions, repeat (press **`B`** for each additional lesion).
5. To delete a box, select it in the table and click **✖ Delete Selected**.

### 3. Record Notes / PCI Score
- In the **Notes / PCI:** field, optionally type the case's PCI score or comments (e.g. `PCI score 3, localized`).

### 4. Save and Proceed
- Click **💾 Save + Next ▶** (large green button).
  - Each box is saved as `<output>/<case_id>/<case_id>_box_NN.mrk.json`.
  - A completion marker `_annotation_done.json` is written.
  - Slicer clears the scene and loads the next uncompleted scan.
- If a case has **0 lesions** (e.g. PCI score 0), clicking Save will ask for confirmation and then mark the case completed with 0 boxes.
- When all scans are complete, an alert will notify you that the entire set is done.

---

## Output Structure

Inside your selected output folder, each case has its own folder named after its case ID:

```
<Output_Folder>/
├── 1001/
│   ├── 1001_box_01.mrk.json
│   ├── 1001_box_02.mrk.json
│   └── _annotation_done.json
├── 1002/
│   └── _annotation_done.json        (example of case with 0 boxes)
└── ...
```

### The `_annotation_done.json` Marker
Each case folder contains a metadata summary:
```json
{
  "case_id": "1001",
  "source_volume": "C:/Scans/1001_AX_pvp_3mm.nii.gz",
  "source_volumes": {
    "AX_3mm": "C:/Scans/1001_AX_pvp_3mm.nii.gz",
    "AX_TS": "C:/Scans/1001_AX_pvp_TS.nii.gz",
    "COR_3mm": "C:/Scans/1001_COR_pvp_3mm.nii.gz",
    "SAG_3mm": "C:/Scans/1001_SAG_pvp_3mm.nii.gz"
  },
  "completed_at_utc": "2026-09-10T09:30:00Z",
  "num_boxes": 2,
  "box_files": [
    "1001_box_01.mrk.json",
    "1001_box_02.mrk.json"
  ],
  "notes": "PCI score 2, upper abdomen",
  "format_version": 1
}
```

---

## Keyboard Shortcuts & Helpful Tips

| Key / Action | Function |
| :--- | :--- |
| **`B`** | Add new bounding box (immediately enters draw/place mode) |
| **Mouse Wheel** | Scroll through CT slices |
| **Middle Click + Drag** | Pan slice view |
| **Right Click + Drag** | Zoom in / out |
| **Click on table row** | Jumps and centers all slice views to the center of that bounding box |
| **Skip completed checkbox** | Toggle on to fast-track through pending scans; toggle off to review previously annotated scans |
| **Axial view: thin slices (TS)** | Swap the axial viewer to the original thin-slice scan (3 mm remains the default; COR/SAG stay on 3 mm) |

---

## Expected Input Folder Structure

Place all volumes for a batch in one folder (subfolders are scanned recursively). The preferred naming pattern is:

```
{case_id}_{view plane}_{phase}_{slice thickness}.nii.gz
```

| Token | Values | Role |
| :--- | :--- | :--- |
| `{case_id}` | e.g. `1001` | Groups files into one case |
| `{view plane}` | `AX`, `COR`, `SAG` | Native acquisition shown in that slice viewer |
| `{phase}` | e.g. `pvp` | Contrast phase (parsed, not used for grouping) |
| `{slice thickness}` | `3mm` or `TS` | Default views use `3mm`; `TS` is axial-only on demand |

Example (cases 1001–1004; 1004 has no thin-slice axial):

```
scans/
├── 1001_AX_pvp_3mm.nii.gz
├── 1001_AX_pvp_TS.nii.gz
├── 1001_COR_pvp_3mm.nii.gz
├── 1001_SAG_pvp_3mm.nii.gz
├── 1002_AX_pvp_3mm.nii.gz
├── 1002_AX_pvp_TS.nii.gz
├── 1002_COR_pvp_3mm.nii.gz
├── 1002_SAG_pvp_3mm.nii.gz
├── ...
├── 1004_AX_pvp_3mm.nii.gz
├── 1004_COR_pvp_3mm.nii.gz
└── 1004_SAG_pvp_3mm.nii.gz
```

Behaviour:
- **Red (axial)** shows `*_AX_*_3mm` by default. Tick **Axial view: thin slices (TS)** to load `*_AX_*_TS` (lazy-loaded; not read until requested).
- **Green (coronal)** shows `*_COR_*_3mm`.
- **Yellow (sagittal)** shows `*_SAG_*_3mm`.
- If COR or SAG is missing, that viewer falls back to a reconstruction from the axial volume and a scan warning is logged.
- A case with no axial volume is skipped.

## Supported File Formats

The module automatically searches recursively for the following medical imaging volume extensions:
- NIfTI: `.nii.gz`, `.nii`
- NRRD: `.nrrd`, `.nhdr`
- MetaImage: `.mha`, `.mhd`

Filenames matching `{case_id}_{AX|COR|SAG}_{phase}_{3mm|TS}` are grouped per case. Unmatched names fall back to single-volume mode: the case ID is a 4- or 5-digit run in the filename (e.g. `patient_01234_CT.nii.gz` → `01234`), or the file stem if no digits are found. That single volume is shown in all three views (reconstructions in COR/SAG).

---

## Repository Structure

```
├── BoundingBoxNavigator/
│   └── BoundingBoxNavigator.py   # 3D Slicer scripted loadable module
├── launch_windows.bat             # One-click Windows launcher (detects Slicer & opens module)
├── launch_linux.sh                # One-click Linux launcher
├── how-to-use.pdf                 # Printable 1-page radiologist quick reference guide
├── how-to-use.tex                 # LaTeX source for the guide
├── LICENSE                        # MIT License
├── README.md                      # Documentation
├── .gitattributes                 # Cross-platform line ending normalization
└── .gitignore                     # Git ignore rules for build and scan artifacts
```

---

## Requirements

- **3D Slicer:** Version 5.0 or newer (tested on 5.6+).
- **Operating System:** Windows 10/11, Linux (Ubuntu 20.04+), macOS.
- **Hardware:** Standard radiologist or researcher workstation capable of running 3D Slicer.

---

## Contributing & Issues

Contributions and suggestions are welcome! If you run into issues, have questions, or wish to request features, please open an issue on the [GitHub Issues](https://github.com/PieterGort/3d-slicer-annotation-tool/issues) page.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
