# settings_window.py
import sys
import os
try:
    import serial.tools.list_ports
except ImportError:
    print("Warning: 'pyserial' library not found. Serial port detection will not work.")
    serial = None

from PySide6.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFormLayout, QGroupBox, QComboBox, QMessageBox, QDialogButtonBox,
    QSizePolicy, QFrame, QToolButton, QCheckBox # Added QCheckBox
)
# **** Import QTimer ****
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QFontDatabase # Added QFontDatabase

# --- Helper function ---
def get_resource_path(relative_path):
    # Simplified, copy from login_window if needed
    try: base_path = sys._MEIPASS
    except AttributeError: base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class SettingsWindow(QDialog):
    settings_saved = Signal(dict)

    def __init__(self, current_settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings / Setup")
        self.setMinimumWidth(400)
        self.current_settings = current_settings if current_settings else {}

        main_layout = QVBoxLayout(self)

        # --- Meshtastic Settings ---
        mesh_group = QGroupBox("Meshtastic Node Configuration")
        mesh_form_layout = QFormLayout(mesh_group)
        self.mesh_connection_type = QComboBox()
        self.mesh_connection_type.addItems(["None", "Serial", "Network (IP)"])
        self.mesh_connection_details = QLineEdit()
        self.mesh_connection_details.setPlaceholderText("Auto-detected or e.g., /dev/ttyUSB0 or 192.168.1.X")
        self.mesh_connection_details.setEnabled(False)
        self.screen_name_input = QLineEdit()
        self.screen_name_input.setPlaceholderText("Alias for your node (required)") # Make required?
        mesh_form_layout.addRow("Screen Name:", self.screen_name_input)
        mesh_form_layout.addRow("Connection:", self.mesh_connection_type)
        mesh_form_layout.addRow("Details:", self.mesh_connection_details)
        main_layout.addWidget(mesh_group)

        # --- MQTT Settings (Collapsible) ---
        self.mqtt_toggle_button = QToolButton()
        self.mqtt_toggle_button.setText("MQTT Server Settings (Optional)")
        self.mqtt_toggle_button.setCheckable(True); self.mqtt_toggle_button.setChecked(False)
        self.mqtt_toggle_button.setStyleSheet("QToolButton { border: none; text-align: left; padding-left: 0px; }")
        self.mqtt_toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.mqtt_toggle_button.setArrowType(Qt.RightArrow)
        self.mqtt_group = QGroupBox()
        self.mqtt_group.setStyleSheet("QGroupBox { border: none; margin-top: 0px; padding-top: 0px; }")
        self.mqtt_group.setVisible(False)
        mqtt_form_layout = QFormLayout(self.mqtt_group)
        mqtt_form_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        mqtt_form_layout.setContentsMargins(15, 5, 5, 5)
        self.server_input = QLineEdit()
        self.port_input = QLineEdit("1883")
        self.username_input = QLineEdit()
        self.password_input = QLineEdit(); self.password_input.setEchoMode(QLineEdit.Password)
        mqtt_form_layout.addRow("Server:", self.server_input)
        mqtt_form_layout.addRow("Port:", self.port_input)
        mqtt_form_layout.addRow("Username:", self.username_input)
        mqtt_form_layout.addRow("Password:", self.password_input)
        main_layout.addWidget(self.mqtt_toggle_button)
        main_layout.addWidget(self.mqtt_group)

        # **** Add Chat Settings Section ****
        chat_group = QGroupBox("Chat Settings")
        chat_layout = QVBoxLayout(chat_group)
        self.auto_save_checkbox = QCheckBox("Auto-Save Conversations Locally")
        self.auto_save_checkbox.setToolTip("Save sent and received messages to files.")
        chat_layout.addWidget(self.auto_save_checkbox)
        main_layout.addWidget(chat_group)
        general_group = QGroupBox("General Settings")
        general_layout = QVBoxLayout(general_group)
        self.enable_sounds_checkbox = QCheckBox("Enable Sounds")
        self.enable_sounds_checkbox.setToolTip("Enable application sound effects (requires restart).")
        general_layout.addWidget(self.enable_sounds_checkbox)
        # ****************************
        main_layout.addWidget(general_group)

        # --- Standard Dialog Buttons ---
        # Use standard buttons for Save/Cancel
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept) # Triggers save
        button_box.rejected.connect(self.reject) # Triggers cancel
        main_layout.addStretch(1) # Push buttons to bottom
        main_layout.addWidget(button_box)

        # --- Connections ---
        self.mesh_connection_type.currentIndexChanged.connect(self.update_mesh_details_state)
        self.mqtt_toggle_button.toggled.connect(self.toggle_mqtt_section)

        # --- Populate fields from current settings ---
        self.load_initial_settings() # Load all settings
        # update_mesh_details_state needs to be called *after* combo box is populated
        # Using QTimer ensures it runs after the constructor finishes setup
        QTimer.singleShot(0, lambda: self.update_mesh_details_state(self.mesh_connection_type.currentIndex()))

    def toggle_mqtt_section(self, checked):
        self.mqtt_group.setVisible(checked)
        self.mqtt_toggle_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def update_mesh_details_state(self, index):
        selected_type = self.mesh_connection_type.currentText()
        is_none = (selected_type == "None")
        self.mesh_connection_details.setEnabled(not is_none)
        if is_none: self.mesh_connection_details.clear(); self.mesh_connection_details.setPlaceholderText("No connection selected")
        elif selected_type == "Serial": self.find_serial_ports()
        else: # Network (IP)
             if not self.mesh_connection_details.text(): self.mesh_connection_details.setPlaceholderText("e.g., 192.168.1.X")

    def find_serial_ports(self):
        if self.mesh_connection_details.text(): return # Don't overwrite user input
        if serial is None: self.mesh_connection_details.setPlaceholderText("pyserial not found. Enter manually."); return
        try:
            ports = serial.tools.list_ports.comports(); port_list = [p.device for p in ports]
            if port_list: self.mesh_connection_details.setText(port_list[0]); self.mesh_connection_details.setPlaceholderText("Auto-detected port")
            else: self.mesh_connection_details.setPlaceholderText("No ports found. Enter manually.")
        except Exception as e: print(f"Error detecting serial ports: {e}"); self.mesh_connection_details.setPlaceholderText("Error detecting ports.")

    def load_initial_settings(self):
        """Populate fields from the settings passed in."""
        print(f"Loading settings into dialog: {self.current_settings}")
        self.screen_name_input.setText(self.current_settings.get("screen_name", ""))
        self.mesh_connection_type.setCurrentText(self.current_settings.get("mesh_conn_type", "None"))
        self.mesh_connection_details.setText(self.current_settings.get("mesh_details", ""))

        mqtt_server = self.current_settings.get("server", "")
        mqtt_enabled = bool(mqtt_server)
        self.mqtt_toggle_button.setChecked(mqtt_enabled)
        self.toggle_mqtt_section(mqtt_enabled)
        self.server_input.setText(mqtt_server)
        self.port_input.setText(str(self.current_settings.get("port", "1883")))
        self.username_input.setText(self.current_settings.get("username", ""))
        self.password_input.setText(self.current_settings.get("password", ""))

        self.auto_save_checkbox.setChecked(bool(self.current_settings.get("auto_save_chats", False)))
        self.enable_sounds_checkbox.setChecked(bool(self.current_settings.get("sounds_enabled", True)))

    def get_settings(self):
        """Collects settings from the UI fields."""
        mesh_type = self.mesh_connection_type.currentText()
        settings = {
            "screen_name": self.screen_name_input.text().strip(),
            "mesh_conn_type": mesh_type,
            "mesh_details": self.mesh_connection_details.text().strip() if mesh_type != "None" else "",
            "server": "", "port": 0, "username": "", "password": "",
            "auto_save_chats": self.auto_save_checkbox.isChecked(),
            "sounds_enabled": self.enable_sounds_checkbox.isChecked()
             # *********************************
        }
        # Populate MQTT settings only if section is enabled/checked
        if self.mqtt_toggle_button.isChecked() or settings["server"]:
             settings["server"] = self.server_input.text().strip()
             settings["username"] = self.username_input.text().strip()
             settings["password"] = self.password_input.text()
             try:
                 port_val = int(self.port_input.text().strip())
                 settings["port"] = port_val if 1 <= port_val <= 65535 else 1883
             except ValueError:
                 settings["port"] = 1883
        return settings

    def accept(self):
        """Called when Save is clicked."""
        new_settings = self.get_settings()

        if not new_settings["screen_name"]: QMessageBox.warning(self, "Input Error", "Screen Name cannot be empty."); return
        if new_settings["mesh_conn_type"] != "None" and not new_settings["mesh_details"]: QMessageBox.warning(self, "Input Error", "Meshtastic connection details required."); return

        if new_settings["server"]:
             if not 1 <= new_settings["port"] <= 65535: QMessageBox.warning(self, "Input Error", "Invalid MQTT Port (1-65535)."); return

        print("Settings validated. Emitting settings_saved signal:", new_settings)
        self.settings_saved.emit(new_settings)
        super().accept()

    def reject(self):
        print("Settings cancelled.")
        super().reject() # Close the dialog

# --- Standalone Test ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    # Example settings including the new key
    test_settings = {
        "screen_name": "OldName", "mesh_conn_type": "Serial",
        "mesh_details": "/dev/ttyUSB0", "server": "test.broker.com",
        "port": 1883, "username": "user", "password": "pw",
        "auto_save_chats": True # Test loading True
    }
    settings_win = SettingsWindow(test_settings)
    if settings_win.exec():
        print("Dialog accepted (saved)")
        print("Returned Settings:", settings_win.get_settings())
    else:
        print("Dialog cancelled")
    sys.exit(app.exec())