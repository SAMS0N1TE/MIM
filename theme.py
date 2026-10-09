"""Classic desktop styling shared by the app and visual test harness."""
import os
from pathlib import Path
from PySide6.QtGui import QFont, QIcon, QFontDatabase
from resources import get_resource_path


def apply_theme(app):
    # Qt's offscreen Windows plugin does not always discover installed fonts.
    # Load from the OS without redistributing proprietary font files.
    font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("tahoma.ttf", "tahomabd.ttf", "arial.ttf", "arialbd.ttf"):
        font = font_root / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setStyle("Windows")
    app.setFont(QFont("Tahoma", 8))
    app.setWindowIcon(QIcon(get_resource_path("resources/icons/mim-mark.svg")))
    with open(get_resource_path("resources/styles/styles.qss"), encoding="utf-8") as source:
        app.setStyleSheet(source.read())
