# settings_window.py
import sys
import os
import traceback # Import traceback for better error printing

# Attempt to import pyserial, but allow fallback
try:
    import serial.tools.list_ports
except ImportError:
    print("Warning: 'pyserial' library not found. Serial port detection will not work.")
    serial = None

from PySide6.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFormLayout, QGroupBox, QComboBox, QMessageBox, QDialogButtonBox,
    QSizePolicy, QFrame, QToolButton, QCheckBox, QSpinBox # Ensure all widgets are imported
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QFontDatabase # Added QFontDatabase

# --- Helper function ---
def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
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

        self.screen_name_input = QLineEdit()
        self.screen_name_input.setPlaceholderText("Alias for your node (required)")
        mesh_form_layout.addRow("Screen Name:", self.screen_name_input)

        self.mesh_connection_type = QComboBox()
        self.mesh_connection_type.addItems(["None", "Serial", "Network (IP)"])
        mesh_form_layout.addRow("Connection:", self.mesh_connection_type)

        self.mesh_connection_details = QLineEdit()
        self.mesh_connection_details.setPlaceholderText("Auto-detected or e.g., /dev/ttyUSB0 or 192.168.1.X")
        self.mesh_connection_details.setEnabled(False) # Disabled until connection type selected
        mesh_form_layout.addRow("Details:", self.mesh_connection_details)

        # Add Channel Index Input (Using QLineEdit)
        self.mesh_channel_index_input = QLineEdit()
        self.mesh_channel_index_input.setPlaceholderText("Usually 0")
        self.mesh_channel_index_input.setToolTip("Default channel index for sending Meshtastic messages (0=Primary).")
        mesh_form_layout.addRow("Default Send Channel Index:", self.mesh_channel_index_input)

        main_layout.addWidget(mesh_group)

        # --- MQTT Settings (Collapsible) ---
        self.mqtt_toggle_button = QToolButton()
        self.mqtt_toggle_button.setText("MQTT Server Settings (Optional)")
        self.mqtt_toggle_button.setCheckable(True)
        self.mqtt_toggle_button.setChecked(False) # Start unchecked
        self.mqtt_toggle_button.setStyleSheet("QToolButton { border: none; text-align: left; padding-left: 0px; }")
        self.mqtt_toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.mqtt_toggle_button.setArrowType(Qt.RightArrow)

        self.mqtt_group = QGroupBox()
        self.mqtt_group.setStyleSheet("QGroupBox { border: none; margin-top: 0px; padding-top: 0px; }")
        self.mqtt_group.setVisible(False) # Start hidden
        mqtt_form_layout = QFormLayout(self.mqtt_group)
        mqtt_form_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        mqtt_form_layout.setContentsMargins(15, 5, 5, 5) # Indent content

        self.server_input = QLineEdit()
        self.port_input = QLineEdit("1883")
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        mqtt_form_layout.addRow("Server:", self.server_input)
        mqtt_form_layout.addRow("Port:", self.port_input)
        mqtt_form_layout.addRow("Username:", self.username_input)
        mqtt_form_layout.addRow("Password:", self.password_input)

        main_layout.addWidget(self.mqtt_toggle_button)
        main_layout.addWidget(self.mqtt_group)

        # --- General Settings ---
        general_group = QGroupBox("General Settings")
        general_layout = QVBoxLayout(general_group)

        self.auto_save_checkbox = QCheckBox("Auto-Save Conversations Locally")
        self.auto_save_checkbox.setToolTip("Save sent and received messages to files.")
        general_layout.addWidget(self.auto_save_checkbox)

        self.enable_sounds_checkbox = QCheckBox("Enable Sounds")
        self.enable_sounds_checkbox.setToolTip("Enable application sound effects.")
        general_layout.addWidget(self.enable_sounds_checkbox)

        main_layout.addWidget(general_group)

        # --- Standard Dialog Buttons ---
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addStretch(1)
        main_layout.addWidget(button_box)

        # --- Connections ---
        self.mesh_connection_type.currentIndexChanged.connect(self.update_mesh_details_state)
        self.mqtt_toggle_button.toggled.connect(self.toggle_mqtt_section)

        # --- Populate fields ---
        self.load_initial_settings()
        # Ensure mesh details state is updated after loading
        QTimer.singleShot(0, lambda: self.update_mesh_details_state(self.mesh_connection_type.currentIndex()))

    def toggle_mqtt_section(self, checked):
        """Shows or hides the MQTT settings group."""
        self.mqtt_group.setVisible(checked)
        self.mqtt_toggle_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def update_mesh_details_state(self, index):
        """Enables/disables the mesh details field based on connection type."""
        selected_type = self.mesh_connection_type.currentText()
        is_none = (selected_type == "None")
        self.mesh_connection_details.setEnabled(not is_none)
        if is_none:
            self.mesh_connection_details.clear()
            self.mesh_connection_details.setPlaceholderText("No connection selected")
        elif selected_type == "Serial":
            # Auto-detect only if field is empty, preserve user input
            if not self.mesh_connection_details.text():
                self.find_serial_ports()
            else:
                 self.mesh_connection_details.setPlaceholderText("e.g., COM3 or /dev/ttyUSB0")
        else: # Network (IP)
            if not self.mesh_connection_details.text():
                 self.mesh_connection_details.setPlaceholderText("e.g., 192.168.1.X or device.local")

    def find_serial_ports(self):
        """Attempts to find serial ports and prefill the details field."""
        if 'serial' not in globals() or serial is None:
             self.mesh_connection_details.setPlaceholderText("pyserial not found. Enter manually.")
             return
        try:
            ports = serial.tools.list_ports.comports()
            port_list = [p.device for p in ports]
            if port_list:
                # Simple prefill with the first found port if field is empty
                if not self.mesh_connection_details.text():
                    self.mesh_connection_details.setText(port_list[0])
                self.mesh_connection_details.setPlaceholderText("Auto-detected port (or enter manually)")
            else:
                self.mesh_connection_details.setPlaceholderText("No ports found. Enter manually.")
        except Exception as e:
            print(f"Error detecting serial ports: {e}")
            traceback.print_exc() # Print traceback for debugging
            self.mesh_connection_details.setPlaceholderText("Error detecting ports.")

    def load_initial_settings(self):
        """Populate dialog fields from the current_settings dictionary."""
        print(f"Loading settings into dialog: {self.current_settings}")
        self.screen_name_input.setText(self.current_settings.get("screen_name", ""))
        self.mesh_connection_type.setCurrentText(self.current_settings.get("mesh_conn_type", "None"))
        self.mesh_connection_details.setText(self.current_settings.get("mesh_details", ""))

        # Load Meshtastic Channel Index (Default to 0)
        default_channel_index = self.current_settings.get("meshtastic_channel_index", 0)
        self.mesh_channel_index_input.setText(str(default_channel_index))

        # Load MQTT Settings
        mqtt_server = self.current_settings.get("server", "")
        mqtt_enabled = bool(mqtt_server) # Enable section if server has a value
        self.mqtt_toggle_button.setChecked(mqtt_enabled)
        self.toggle_mqtt_section(mqtt_enabled) # Update visibility based on loaded state
        self.server_input.setText(mqtt_server)
        self.port_input.setText(str(self.current_settings.get("port", "1883")))
        self.username_input.setText(self.current_settings.get("username", ""))
        self.password_input.setText(self.current_settings.get("password", "")) # Load password field

        # Load General Settings
        self.auto_save_checkbox.setChecked(bool(self.current_settings.get("auto_save_chats", False)))
        self.enable_sounds_checkbox.setChecked(bool(self.current_settings.get("sounds_enabled", True)))

    def get_settings(self):
        """Collects settings from the UI fields into a dictionary."""
        mesh_type = self.mesh_connection_type.currentText()

        # Get Channel Index, default 0 if invalid
        channel_index_str = self.mesh_channel_index_input.text().strip()
        try:
            channel_index = int(channel_index_str)
            if channel_index < 0: channel_index = 0
        except ValueError:
            channel_index = 0

        settings = {
            "screen_name": self.screen_name_input.text().strip(),
            "mesh_conn_type": mesh_type,
            "mesh_details": self.mesh_connection_details.text().strip() if mesh_type != "None" else "",
            "meshtastic_channel_index": channel_index,
            "auto_save_chats": self.auto_save_checkbox.isChecked(),
            "sounds_enabled": self.enable_sounds_checkbox.isChecked(),
            # Initialize MQTT keys
            "server": "",
            "port": 1883, # Default port
            "username": "",
            "password": ""
        }

        # Populate MQTT settings only if section is enabled or server field has content
        if self.mqtt_toggle_button.isChecked() or self.server_input.text().strip():
             settings["server"] = self.server_input.text().strip()
             settings["username"] = self.username_input.text().strip()
             settings["password"] = self.password_input.text() # Don't strip password
             try:
                 port_val = int(self.port_input.text().strip())
                 settings["port"] = port_val if 1 <= port_val <= 65535 else 1883
             except ValueError:
                 settings["port"] = 1883 # Default if not an integer

        return settings

    def accept(self):
        """Validates settings and emits signal if valid."""
        new_settings = self.get_settings()

        # --- Validation ---
        if not new_settings["screen_name"]:
            QMessageBox.warning(self, "Input Error", "Screen Name cannot be empty.")
            return # Stop accept process

        if new_settings["mesh_conn_type"] != "None" and not new_settings["mesh_details"]:
            QMessageBox.warning(self, "Input Error", "Meshtastic connection details required when connection type is not 'None'.")
            return

        if new_settings["meshtastic_channel_index"] < 0: # Basic channel validation
            QMessageBox.warning(self, "Input Error", "Meshtastic Channel Index cannot be negative.")
            return

        # Validate MQTT port only if server is specified
        if new_settings["server"]:
             if not (1 <= new_settings["port"] <= 65535):
                 QMessageBox.warning(self, "Input Error", "Invalid MQTT Port (must be 1-65535).")
                 return
        # --- End Validation ---

        print("Settings validated. Emitting settings_saved signal:", new_settings)
        self.settings_saved.emit(new_settings)
        super().accept() # Close the dialog with Accepted state

    def reject(self):
        """Called when Cancel is clicked."""
        print("Settings cancelled.")
        super().reject() # Close the dialog with Rejected state

# --- Standalone Test (Optional) ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    test_settings = {
        "screen_name": "TestUser",
        "mesh_conn_type": "Serial",
        "mesh_details": "COM5",
        "meshtastic_channel_index": 1,
        "server": "mqtt.example.com",
        "port": 1883,
        "username": "user",
        "password": "pw",
        "auto_save_chats": True,
        "sounds_enabled": False
    }
    settings_win = SettingsWindow(test_settings)
    if settings_win.exec(): # exec() shows the dialog modally
        print("Dialog accepted (saved)")
        print("Returned Settings:", settings_win.get_settings())
    else:
        print("Dialog cancelled")
    sys.exit() # Use sys.exit() instead of app.exec() in test
