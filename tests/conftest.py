"""Put the Slicer module directory on the path. Tests import pure modules only."""

import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1] / "BoundingBoxNavigator"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
