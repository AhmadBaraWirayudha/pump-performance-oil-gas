"""pytest setup: make src/ importable. Tests use synthetic workbooks only, so
they run without the (private) lab workbook."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
