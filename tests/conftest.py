import os
from pathlib import Path
import pytest
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
task_tmp = Path(__file__).resolve().parent.parent / ".tmp"
task_tmp.mkdir(exist_ok=True)
os.environ["TEMP"] = str(task_tmp)
os.environ["TMP"] = str(task_tmp)


@pytest.fixture
def embedding():
    a = np.zeros(128, dtype=np.float32)
    a[0] = 1
    return a


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestProtector:
    """Test-only reversible envelope. Never used in application code."""
    def protect(self, data):
        return b"TEST:" + bytes(b ^ 0xA5 for b in data)

    def unprotect(self, data):
        if not data.startswith(b"TEST:"):
            raise ValueError("Corrupted test data")
        return bytes(b ^ 0xA5 for b in data[5:])


@pytest.fixture
def database(tmp_path):
    from visionguard.storage import Database
    db = Database(tmp_path / "test.sqlite3", TestProtector())
    yield db
    db.close()
