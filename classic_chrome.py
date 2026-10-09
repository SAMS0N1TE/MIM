"""Accessible classic title strip; moving/resizing delegated to the window manager."""
from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QWidget, QLabel, QToolButton, QHBoxLayout, QVBoxLayout, QMainWindow
from PySide6.QtGui import QIcon, QPainter, QColor, QPen
from resources import get_resource_path


class CaptionButton(QToolButton):
    """Pixel-aligned caption symbols, independent of text/font glyph metrics."""
    def __init__(self, kind, window):
        super().__init__()
        self.kind = kind
        self.window = window
        self.setFixedSize(18, 16)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#d4d0c8'))
        down = self.isDown()
        for color, segments in (
            ('#404040' if down else '#ffffff', [(0,0,17,0),(0,0,0,15)]),
            ('#ffffff' if down else '#404040', [(0,15,17,15),(17,0,17,15)]),
            ('#808080', [(1,14,16,14),(16,1,16,14)]),
        ):
            p.setPen(QColor(color))
            for segment in segments: p.drawLine(*segment)
        p.translate(1 if down else 0, 1 if down else 0)
        p.setPen(QPen(QColor('#000000' if self.isEnabled() else '#808080'), 1))
        if self.kind == 'minimize':
            p.fillRect(4, 10, 7, 2, p.pen().color())
        elif self.kind == 'maximize':
            if self.window.isMaximized():
                p.drawRect(6,3,7,6)
                p.fillRect(4,6,8,6,QColor('#d4d0c8'))
                p.drawRect(4,6,7,6)
                p.drawLine(4,7,11,7)
            else:
                p.drawRect(4,3,9,9)
                p.drawLine(4,4,13,4)
        else:
            for offset in (0,1):
                p.drawLine(5+offset,4,11+offset,10)
                p.drawLine(11+offset,4,5+offset,10)
        if self.hasFocus():
            p.setPen(QPen(QColor('#000000'),1,Qt.DotLine))
            p.drawRect(2,2,13,11)
        p.end()


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setObjectName("classicTitle")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(22)
        self.setStyleSheet("#classicTitle { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #000080,stop:1 #1084d0); } QLabel { color:white; font-weight:bold; background:transparent; } QToolButton { color:black; background:#d4d0c8; border:2px outset #fff; padding:0; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 2, 2, 2)
        row.setSpacing(2)
        icon = QLabel()
        icon.setPixmap(QIcon(get_resource_path("resources/icons/mim-mark.svg")).pixmap(16, 16))
        row.addWidget(icon)
        self.title = QLabel(window.windowTitle())
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.title, 1)
        window.windowTitleChanged.connect(self.title.setText)
        for kind, label, callback in (("minimize", "Minimize", window.showMinimized),
                                      ("maximize", "Maximize or restore", self.toggle_maximized),
                                      ("close", "Close", window.close)):
            button = CaptionButton(kind, window)
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.clicked.connect(callback)
            if label.startswith("Maximize") and window.minimumWidth() == window.maximumWidth():
                button.setEnabled(False)
            row.addWidget(button)

    def toggle_maximized(self):
        if self.window.minimumWidth() == self.window.maximumWidth():
            return
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window.windowHandle():
            self.window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximized()


def install_chrome(window):
    window.setWindowFlag(Qt.FramelessWindowHint)
    window.setObjectName("classicWindow")
    title = TitleBar(window)
    if isinstance(window, QMainWindow):
        menu = window.menuBar()
        container = QWidget(window)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(title)
        layout.addWidget(menu)
        window.setMenuWidget(container)
        window.statusBar().setSizeGripEnabled(True)
    else:
        window.layout().insertWidget(0, title)
    window._classic_title = title
