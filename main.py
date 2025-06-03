import sys
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFormLayout, QCheckBox, QSpacerItem,
    QSizePolicy, QFrame
)
from PySide6.QtGui import QPixmap, QFont, QFontDatabase, QIcon, QCursor, QKeySequence
from PySide6.QtCore import Qt, Signal, QSize

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

LOGO_AREA_BG_COLOR = "#033b72"
TITLE_COLOR = "white"
LINK_BUTTON_COLOR = "blue"
LINK_BUTTON_HOVER_COLOR = "darkblue"
LINK_BUTTON_PRESSED_COLOR = "purple"
ICON_BUTTON_DEFAULT_BG_COLOR = "transparent"
ICON_BUTTON_HOVER_BG_COLOR = "#c2c0b2"
ICON_BUTTON_PRESSED_BG_COLOR = "#a8a699"

TITLE_FONT_FAMILY = "Helvetica"
TITLE_FONT_SIZE = 10
TITLE_FONT_WEIGHT = QFont.Bold
LABEL_FONT_FAMILY = "Helvetica"
LABEL_FONT_SIZE = 8
LABEL_FONT_WEIGHT = QFont.Normal

LOGO_SIZE = QSize(90, 90)
ICON_SIZE = QSize(60, 60)
ICON_BUTTON_SIZE = QSize(ICON_SIZE.width() + 10, ICON_SIZE.height() + 10)
MIN_WINDOW_WIDTH = 240
MIN_WINDOW_HEIGHT = 350
MAX_WINDOW_WIDTH = 280

MAIN_LAYOUT_MARGINS = (0, 0, 0, 5)
MAIN_LAYOUT_SPACING = 0
LOGO_AREA_MARGINS = (10, 10, 10, 10)
INPUT_AREA_MARGINS = (10, 5, 10, 10)
INPUT_AREA_SPACING = 8
INPUT_LABEL_SPACING = 2
CHECKBOX_TOP_MARGIN = 5
BOTTOM_BAR_MARGINS = (5, 3, 5, 3)
BOTTOM_BAR_SPACING = 8

LOGO_AREA_STRETCH = 1
INPUT_AREA_STRETCH = 2

LOGO_ICON_PATH = "resources/icons/app_icon.png"
HELP_ICON_PATH = "resources/icons/help_icon.png"
SETUP_ICON_PATH = "resources/icons/setup_icon.png"
SIGNON_ICON_PATH = "resources/icons/signon_icon.png"

LINK_STYLE = f"""
QPushButton {{
    border: none;
    color: {LINK_BUTTON_COLOR};
    background-color: transparent;
    text-decoration: underline;
    text-align: left;
    padding: 0px;
    font-size: {LABEL_FONT_SIZE}pt;
    font-family: {LABEL_FONT_FAMILY};
}}
QPushButton:hover {{
    color: {LINK_BUTTON_HOVER_COLOR};
}}
QPushButton:pressed {{
    color: {LINK_BUTTON_PRESSED_COLOR};
}}
"""


ICON_BUTTON_STYLE = f"""
QPushButton {{
    background-color: {ICON_BUTTON_DEFAULT_BG_COLOR};
    border: 1px solid transparent;
    padding: 4px;
}}
QPushButton:hover {{
    background-color: {ICON_BUTTON_HOVER_BG_COLOR};
    border: 1px solid #B0B0B0;
    border-style: outset;
}}
QPushButton:pressed {{
    background-color: {ICON_BUTTON_PRESSED_BG_COLOR};
    border: 1px solid #B0B0B0;
    border-style: inset;
}}
"""

class LoginWindow(QWidget):
    setup_requested = Signal()
    sign_on_requested = Signal(str, str, bool)

    def __init__(self, saved_screen_name=None, saved_auto_login=False):
        super().__init__()
        self.setWindowTitle("MIM Sign On")
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.setMaximumWidth(MAX_WINDOW_WIDTH)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(*MAIN_LAYOUT_MARGINS)
        main_layout.setSpacing(MAIN_LAYOUT_SPACING)

        logo_frame = QFrame()
        logo_frame.setStyleSheet(f"background-color: {LOGO_AREA_BG_COLOR};")
        logo_layout = QVBoxLayout(logo_frame)
        logo_layout.setAlignment(Qt.AlignCenter)
        logo_layout.setContentsMargins(*LOGO_AREA_MARGINS)

        logo_path = get_resource_path(LOGO_ICON_PATH)
        logo_label = QLabel()
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(LOGO_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled_pixmap)
        else:
            logo_label.setText("MIM")
            logo_label.setFont(QFont("Arial", 24, QFont.Bold))
            logo_label.setStyleSheet(f"color: {TITLE_COLOR};")

        title_label = QLabel("Welcome!")
        title_label.setFont(QFont(TITLE_FONT_FAMILY, TITLE_FONT_SIZE, TITLE_FONT_WEIGHT))
        title_label.setStyleSheet(f"color: {TITLE_COLOR};")
        title_label.setAlignment(Qt.AlignCenter)

        logo_layout.addWidget(logo_label)
        logo_layout.addWidget(title_label)
        main_layout.addWidget(logo_frame, LOGO_AREA_STRETCH)

        input_area_widget = QWidget()
        input_layout = QVBoxLayout(input_area_widget)
        input_layout.setContentsMargins(*INPUT_AREA_MARGINS)
        input_layout.setSpacing(INPUT_AREA_SPACING)

        input_form_layout = QVBoxLayout()
        input_form_layout.setSpacing(INPUT_AREA_SPACING)

        screen_name_v_layout = QVBoxLayout()
        screen_name_v_layout.setSpacing(INPUT_LABEL_SPACING)
        screen_name_label = QLabel("ScreenName:")
        screen_name_label.setFont(QFont(LABEL_FONT_FAMILY, LABEL_FONT_SIZE, LABEL_FONT_WEIGHT))
        self.screen_name_input = QLineEdit()
        if saved_screen_name:
            self.screen_name_input.setText(saved_screen_name)
        self.screen_name_input.setPlaceholderText("Enter Node Name/Alias")
        screen_name_v_layout.addWidget(screen_name_label)
        screen_name_v_layout.addWidget(self.screen_name_input)
        input_form_layout.addLayout(screen_name_v_layout)

        get_name_layout = QHBoxLayout()
        self.get_name_button = QPushButton("Get a Screen Name")
        self.get_name_button.setStyleSheet(LINK_STYLE)
        self.get_name_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.get_name_button.clicked.connect(self.setup_requested.emit)
        get_name_layout.addWidget(self.get_name_button)
        get_name_layout.addStretch(1)
        input_form_layout.addLayout(get_name_layout)

        password_v_layout = QVBoxLayout()
        password_v_layout.setSpacing(INPUT_LABEL_SPACING)
        password_label = QLabel("Password:")
        password_label.setFont(QFont(LABEL_FONT_FAMILY, LABEL_FONT_SIZE, LABEL_FONT_WEIGHT))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("MQTT Password (if req.)")
        self.password_input.returnPressed.connect(self.on_sign_on_clicked)
        password_v_layout.addWidget(password_label)
        password_v_layout.addWidget(self.password_input)
        input_form_layout.addLayout(password_v_layout)

        checkbox_layout = QHBoxLayout()
        checkbox_layout.setContentsMargins(0, CHECKBOX_TOP_MARGIN, 0, 0)
        self.save_config_checkbox = QCheckBox("Save configuration")
        self.auto_login_checkbox = QCheckBox("Auto Sign-On")
        self.save_config_checkbox.setFont(QFont(LABEL_FONT_FAMILY, LABEL_FONT_SIZE, LABEL_FONT_WEIGHT))
        self.auto_login_checkbox.setFont(QFont(LABEL_FONT_FAMILY, LABEL_FONT_SIZE, LABEL_FONT_WEIGHT))

        print(f"Setting Auto-Login checkbox with value: {saved_auto_login} (Type: {type(saved_auto_login)})")
        try:
             is_checked = bool(saved_auto_login)
        except Exception:
             is_checked = False
        self.auto_login_checkbox.setChecked(is_checked)

        checkbox_layout.addWidget(self.save_config_checkbox)
        checkbox_layout.addStretch(1)
        checkbox_layout.addWidget(self.auto_login_checkbox)
        input_form_layout.addLayout(checkbox_layout)

        input_layout.addLayout(input_form_layout)
        input_layout.addStretch(1)
        main_layout.addWidget(input_area_widget, INPUT_AREA_STRETCH)

        bottom_bar = QFrame()
        bottom_bar.setFrameShape(QFrame.StyledPanel)
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(*BOTTOM_BAR_MARGINS)
        bottom_layout.setSpacing(BOTTOM_BAR_SPACING)

        self.help_button = QPushButton("")
        self.help_button.setToolTip("Help")
        self.help_button.setStyleSheet(ICON_BUTTON_STYLE)
        self.help_button.setFixedSize(ICON_BUTTON_SIZE)
        self.help_button.setCursor(QCursor(Qt.PointingHandCursor))
        try:
            help_icon = QIcon(get_resource_path(HELP_ICON_PATH))
            if not help_icon.isNull():
                 self.help_button.setIcon(help_icon)
                 self.help_button.setIconSize(ICON_SIZE)
        except Exception as e: print(f"Could not load help icon: {e}")

        self.setup_button = QPushButton("")
        self.setup_button.setToolTip("Setup / Settings")
        self.setup_button.setStyleSheet(ICON_BUTTON_STYLE)
        self.setup_button.setFixedSize(ICON_BUTTON_SIZE)
        self.setup_button.setCursor(QCursor(Qt.PointingHandCursor))
        try:
            setup_icon = QIcon(get_resource_path(SETUP_ICON_PATH))
            if not setup_icon.isNull():
                 self.setup_button.setIcon(setup_icon)
                 self.setup_button.setIconSize(ICON_SIZE)
        except Exception as e: print(f"Could not load setup icon: {e}")

        self.signon_button = QPushButton("")
        self.signon_button.setToolTip("Sign On")
        self.signon_button.setStyleSheet(ICON_BUTTON_STYLE)
        self.signon_button.setFixedSize(ICON_BUTTON_SIZE)
        self.signon_button.setDefault(True)
        self.signon_button.setCursor(QCursor(Qt.PointingHandCursor))
        try:
            signon_icon = QIcon(get_resource_path(SIGNON_ICON_PATH))
            if not signon_icon.isNull():
                 self.signon_button.setIcon(signon_icon)
                 self.signon_button.setIconSize(ICON_SIZE)
        except Exception as e: print(f"Could not load signon icon: {e}")

        bottom_layout.addWidget(self.help_button)
        bottom_layout.addWidget(self.setup_button)
        bottom_layout.addStretch(1)

        bottom_layout.addWidget(self.signon_button)

        main_layout.addWidget(bottom_bar)

        self.setup_button.clicked.connect(self.setup_requested.emit)
        self.signon_button.clicked.connect(self.on_sign_on_clicked)
        self.help_button.clicked.connect(self.show_help_placeholder)

        if saved_auto_login and saved_screen_name:
             print("Auto Sign-On triggered.")
             from PySide6.QtCore import QTimer
             QTimer.singleShot(100, self.on_sign_on_clicked)


    def on_sign_on_clicked(self):
        screen_name = self.screen_name_input.text().strip()
        password = self.password_input.text()
        auto_login = self.auto_login_checkbox.isChecked()
        save_config = self.save_config_checkbox.isChecked()

        if not screen_name:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Sign On Error", "Please enter a Screen Name.")
            return

        print(f"Sign On requested for: {screen_name}, Auto Login: {auto_login}, Save Config: {save_config}")
        self.sign_on_requested.emit(screen_name, password, auto_login)

    def get_save_config_preference(self):
        return self.save_config_checkbox.isChecked()

    def show_help_placeholder(self):
         from PySide6.QtWidgets import QMessageBox
         QMessageBox.information(self, "Help", "Help documentation is not yet available.")

    def close_window(self):
        self.close()
