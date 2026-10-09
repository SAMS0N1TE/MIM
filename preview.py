"""Render a deterministic, radio-free UI preview: uv run python preview.py output.png."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import sys
import time
from pathlib import Path
from PySide6.QtGui import QImage, QPainter, QColor, QFont
from PySide6.QtWidgets import QApplication
from theme import apply_theme
from login_window import LoginWindow
from buddy_list_window import BuddyListWindow
from chat_window import ChatWindow
from sound_utils import set_sounds_enabled


def render(destination):
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    set_sounds_enabled(False)
    BuddyListWindow._create_tray_icon = lambda self: None
    login = LoginWindow("TrailRadio")
    buddies = BuddyListWindow("TrailRadio", {}, {"sounds_enabled": False})
    buddies.handle_node_list_update([
        {"user": {"id": "!12345678", "longName": "SummitStation"}, "lastHeard": time.time(), "active_report": True},
        {"user": {"id": "!22345678", "longName": "RiverRelay"}, "lastHeard": time.time() - 400},
        {"user": {"id": "!32345678", "longName": "CampBase"}, "lastHeard": time.time() - 2000},
    ])
    buddies.buddy_tree.expandAll()
    chat = ChatWindow("TrailRadio", "!12345678", "SummitStation")
    chat.receive_message("Made it to the summit. Can you hear me?", "SummitStation")
    chat.message_display.append(chat.format_message("TrailRadio", "Loud and clear. What a view up there."))
    chat.receive_message("No cell service. Still in touch. :)", "SummitStation")
    chat.message_input.setPlainText("Meet you at the trailhead.")
    chat.statusBar().showMessage("Meshtastic · Channel 0")
    windows = [(login, 24, 75), (buddies, 326, 75), (chat, 625, 130)]
    for window, _, _ in windows:
        window.show()
    app.processEvents()
    buddies.statusBar().showMessage("Meshtastic · Ready")
    canvas = QImage(1120, 590, QImage.Format_ARGB32)
    canvas.fill(QColor("#3b7275"))
    painter = QPainter(canvas)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Tahoma", 14))
    painter.drawText(24, 34, "MIM / Classic desktop preview")
    painter.setFont(QFont("Tahoma", 9))
    painter.drawText(24, 54, "Illustrative contacts and conversation. No radio connection.")
    for window, x, y in windows:
        painter.drawPixmap(x, y, window.grab())
    painter.end()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(destination))
    for window, _, _ in windows:
        window.hide()
    return str(destination.resolve())


if __name__ == "__main__":
    print(render(sys.argv[1] if len(sys.argv) > 1 else "preview.png"))
