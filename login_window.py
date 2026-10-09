"""Compact sign-on window; all connections remain in the application controller."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QCheckBox, QMessageBox)
from resources import get_resource_path


class LoginWindow(QWidget):
    setup_requested = Signal()
    sign_on_requested = Signal(str, str, bool)

    def __init__(self, saved_screen_name=None, saved_auto_login=False):
        super().__init__()
        self.setWindowTitle("MIM Sign On")
        self.setFixedWidth(280)
        self.resize(280, 345)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        body = QWidget()
        layout.addWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(7)
        banner = QLabel()
        banner.setPixmap(QPixmap(get_resource_path("resources/icons/mim-banner.svg")))
        banner.setAlignment(Qt.AlignCenter)
        layout.addWidget(banner)
        welcome = QLabel("Welcome to MIM")
        welcome.setAlignment(Qt.AlignCenter)
        layout.addWidget(welcome)
        screen_label = QLabel("&Screen Name:")
        self.screen_name_input = QLineEdit(saved_screen_name or "")
        screen_label.setBuddy(self.screen_name_input)
        layout.addWidget(screen_label)
        layout.addWidget(self.screen_name_input)
        password_label = QLabel("&Password:")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setToolTip("Only needed for an authenticated MQTT broker")
        password_label.setBuddy(self.password_input)
        layout.addWidget(password_label)
        layout.addWidget(self.password_input)
        self.save_config_checkbox = QCheckBox("Remember screen name")
        self.save_config_checkbox.setChecked(bool(saved_screen_name))
        self.auto_login_checkbox = QCheckBox("Auto sign-on")
        self.auto_login_checkbox.setChecked(saved_auto_login)
        layout.addWidget(self.save_config_checkbox)
        layout.addWidget(self.auto_login_checkbox)
        layout.addStretch()
        row = QHBoxLayout()
        self.help_button = QPushButton("&Help")
        self.setup_button = QPushButton("Se&tup")
        self.signon_button = QPushButton("&Sign On")
        self.signon_button.setDefault(True)
        for button in (self.help_button, self.setup_button, self.signon_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.help_button.clicked.connect(self.show_help)
        self.setup_button.clicked.connect(self.setup_requested.emit)
        self.signon_button.clicked.connect(self.on_sign_on_clicked)
        self.password_input.returnPressed.connect(self.on_sign_on_clicked)
        self.screen_name_input.returnPressed.connect(self.on_sign_on_clicked)
        self.screen_name_input.setFocus()
        from classic_chrome import install_chrome
        install_chrome(self)
        # Auto-login is scheduled by the controller after connecting this window's signals.

    def on_sign_on_clicked(self):
        name = self.screen_name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Sign On", "Please enter a screen name.")
            self.screen_name_input.setFocus()
            return
        self.sign_on_requested.emit(name, self.password_input.text(), self.auto_login_checkbox.isChecked())

    def get_save_config_preference(self):
        return self.save_config_checkbox.isChecked()

    def show_help(self):
        QMessageBox.information(self, "MIM Help", "Open Setup to choose Meshtastic or MeshCore, then a serial port or TCP connection.\n\nMeshCore TCP uses host:port. MQTT is optional. Its password is used for this session only.\n\nDouble-click a buddy to chat. Enter sends; Shift+Enter starts a new line. Radio acceptance does not confirm delivery.")

    def close_window(self):
        self.close()
