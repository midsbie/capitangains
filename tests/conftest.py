import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

# Ensure 'src' and 'tests' are on sys.path for imports
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


@pytest.fixture
def out_path(tmp_path: Path) -> Path:
    """The conventional workbook output path inside the test's tmp_path.

    Collapses the inline ``tmp_path / "out.xlsx"`` repeated across the IO tests.
    """
    return tmp_path / "out.xlsx"


@pytest.fixture
def write_statement(tmp_path: Path) -> Callable[..., Path]:
    """Return a closure that writes statement rows to a CSV under tmp_path.

    Usage: ``path = write_statement(rows)`` or ``write_statement(rows, name="a.csv")``.
    """
    from tests.support import write_statement_csv

    def _write(rows: Sequence[Sequence[str]], *, name: str = "stmt.csv") -> Path:
        return write_statement_csv(tmp_path / name, rows)

    return _write
