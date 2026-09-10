#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="${SCRIPT_DIR}/BoundingBoxNavigator"

if [ ! -f "${MODULE_DIR}/BoundingBoxNavigator.py" ]; then
    echo "[ERROR] Module not found at: ${MODULE_DIR}/BoundingBoxNavigator.py"
    exit 1
fi

SLICER_BIN=""
if command -v Slicer >/dev/null 2>&1; then
    SLICER_BIN="$(command -v Slicer)"
elif [ -x "/opt/Slicer-5.10.0/Slicer" ]; then
    SLICER_BIN="/opt/Slicer-5.10.0/Slicer"
else
    # Check for Slicer in /opt
    for opt_slicer in /opt/Slicer*/Slicer; do
        if [ -x "$opt_slicer" ]; then
            SLICER_BIN="$opt_slicer"
            break
        fi
    done
fi

if [ -z "$SLICER_BIN" ] || [ ! -x "$SLICER_BIN" ]; then
    echo "[ERROR] Slicer executable not found on PATH or /opt."
    read -rp "Please enter full path to Slicer executable: " SLICER_BIN
fi

if [ ! -x "$SLICER_BIN" ]; then
    echo "[ERROR] Executable not found at: $SLICER_BIN"
    exit 1
fi

echo "[OK] Using Slicer: $SLICER_BIN"
echo "[OK] Module path:  $MODULE_DIR"
echo "Launching 3D Slicer..."

exec "$SLICER_BIN" --additional-module-paths "$MODULE_DIR" --python-code "slicer.util.selectModule('BoundingBoxNavigator')" "$@"
