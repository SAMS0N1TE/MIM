import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest
from PySide6.QtWidgets import QApplication
from theme import apply_theme


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    apply_theme(instance)
    yield instance
