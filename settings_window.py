import sys
import os
import traceback
from typing import Dict, Any, List

try:
    import serial.tools.list_ports
except ImportError:
    serial = None

from PySide6.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFormLayout, QGroupBox, QComboBox, QMessageBox, QDialogButtonBox,
    QSizePolicy, QFrame, QToolButton, QCheckBox, QSpinBox, QListWidget,
    QInputDialog, QStackedWidget, QDoubleSpinBox
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QFont, QFontDatabase, QIcon


def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class SettingsWindow(QDialog):
    settings_saved = Signal(dict)
    current_settings: Dict[str, Any]

    def __init__(self, current_settings=None, channel_list: List[Dict[str, Any]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings / Setup")
        self.setMinimumSize(650, 450) # Increased minimum size
        self.current_settings = current_settings if current_settings else {}
        self._initial_channel_list = channel_list if channel_list else []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(10)
        main_layout.addLayout(content_layout)


        self.category_list = QListWidget()
        self.category_list.setFixedWidth(180) # Increased width
        self.category_list.setMovement(QListWidget.Static)
        self.category_list.setSelectionMode(QListWidget.SingleSelection)
        self.category_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #c0c0c0;
                border-radius: 5px;
                padding: 5px;
                background-color: #f0f0f0;
            }
            QListWidget::item {
                padding: 5px 2px;
                border-bottom: 1px solid #e0e0e0;
            }
            QListWidget::item:selected {
                background-color: #a0c0f0;
                color: black;
            }
        """)
        content_layout.addWidget(self.category_list)

        # Frame to give structure to the settings pages area
        settings_pages_frame = QFrame()
        settings_pages_frame.setFrameShape(QFrame.StyledPanel)
        settings_pages_frame.setFrameShadow(QFrame.Sunken)
        settings_pages_layout = QVBoxLayout(settings_pages_frame)
        settings_pages_layout.setContentsMargins(10, 10, 10, 10)
        settings_pages_layout.setSpacing(10)


        self.pages_stack = QStackedWidget()
        settings_pages_layout.addWidget(self.pages_stack)

        content_layout.addWidget(settings_pages_frame, 1)


        self._create_pages()

        self._add_category_and_page("Meshtastic Node", self.meshtastic_page)
        self._add_category_and_page("Meshtastic Channels", self.channels_page)
        self._add_category_and_page("MQTT Server", self.mqtt_server_page)
        self._add_category_and_page("MQTT Groups", self.mqtt_groups_page)
        self._add_category_and_page("Buddy Groups", self.buddy_groups_page)
        self._add_category_and_page("Node Map", self.map_page) # Added Map page
        self._add_category_and_page("General", self.general_page)

        self.category_list.currentRowChanged.connect(self.pages_stack.setCurrentIndex)


        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

        self.mesh_connection_type.currentIndexChanged.connect(self.update_mesh_details_state)
        self.mqtt_toggle_button.toggled.connect(self.mqtt_settings_frame.setVisible)
        self.add_mqtt_topic_button.clicked.connect(self.add_mqtt_group_topic)
        self.remove_mqtt_topic_button.clicked.connect(self.remove_mqtt_group_topic)
        self.add_group_button.clicked.connect(self.add_buddy_group)
        self.edit_group_button.clicked.connect(self.edit_buddy_group)
        self.remove_group_button.clicked.connect(self.remove_buddy_group)

        self.map_offline_toggle.toggled.connect(self.map_offline_settings_group.setVisible)


        self.load_initial_settings()
        self.update_channel_display(self._initial_channel_list)
        QTimer.singleShot(0, lambda: self.update_mesh_details_state(self.mesh_connection_type.currentIndex()))

        if self.category_list.count() > 0:
            self.category_list.setCurrentRow(0)


    def _create_pages(self):
        # Meshtastic Node Settings
        self.meshtastic_page = QWidget()
        mesh_group = QGroupBox("Node Connection & Identity")
        mesh_form_layout = QFormLayout(mesh_group)
        mesh_form_layout.setContentsMargins(10, 10, 10, 10)
        self.screen_name_input = QLineEdit()
        self.screen_name_input.setPlaceholderText("Alias for your node (required)")
        mesh_form_layout.addRow("Screen Name:", self.screen_name_input)
        self.mesh_connection_type = QComboBox()
        self.mesh_connection_type.addItems(["None", "Serial", "Network (IP)"])
        mesh_form_layout.addRow("Connection:", self.mesh_connection_type)
        self.mesh_connection_details = QLineEdit()
        self.mesh_connection_details.setPlaceholderText("Auto-detected or e.g., /dev/ttyUSB0 or 192.168.1.X")
        self.mesh_connection_details.setEnabled(False)
        mesh_form_layout.addRow("Details:", self.mesh_connection_details)
        self.mesh_channel_index_input = QLineEdit()
        self.mesh_channel_index_input.setPlaceholderText("Usually 0")
        self.mesh_channel_index_input.setToolTip("Default channel index for sending Meshtastic messages (0=Primary).")
        mesh_form_layout.addRow("Default Send Channel Index:", self.mesh_channel_index_input)
        meshtastic_page_layout = QVBoxLayout(self.meshtastic_page)
        meshtastic_page_layout.addWidget(mesh_group)
        meshtastic_page_layout.addStretch(1)


        # Meshtastic Channels Display
        self.channels_page = QWidget()
        channel_group = QGroupBox("Device Channels")
        channel_layout = QVBoxLayout(channel_group)
        channel_layout.setContentsMargins(10, 10, 10, 10)
        self.channel_list_widget = QListWidget()
        self.channel_list_widget.setAlternatingRowColors(True)
        self.channel_list_widget.setMinimumHeight(80)
        self.channel_list_widget.setSelectionMode(QListWidget.NoSelection)
        channel_layout.addWidget(QLabel("Channels configured on connected device:"))
        channel_layout.addWidget(self.channel_list_widget)
        channels_page_layout = QVBoxLayout(self.channels_page)
        channels_page_layout.addWidget(channel_group)
        channels_page_layout.addStretch(1)


        # MQTT Server Settings
        self.mqtt_server_page = QWidget()
        mqtt_page_layout = QVBoxLayout(self.mqtt_server_page)

        self.mqtt_toggle_button = QToolButton()
        self.mqtt_toggle_button.setText("Enable MQTT Connection")
        self.mqtt_toggle_button.setCheckable(True)
        self.mqtt_toggle_button.setStyleSheet("QToolButton { border: none; text-align: left; padding: 5px 0px; font-weight: bold; }")
        self.mqtt_toggle_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        mqtt_page_layout.addWidget(self.mqtt_toggle_button)

        self.mqtt_settings_frame = QFrame()
        self.mqtt_settings_frame.setFrameShape(QFrame.StyledPanel)
        self.mqtt_settings_frame.setFrameShadow(QFrame.Sunken)
        self.mqtt_settings_frame.setVisible(False) # Controlled by toggle
        mqtt_settings_layout = QFormLayout(self.mqtt_settings_frame)
        mqtt_settings_layout.setContentsMargins(10, 10, 10, 10)
        mqtt_settings_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.server_input = QLineEdit()
        self.port_input = QLineEdit("1883")
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        mqtt_settings_layout.addRow("Server:", self.server_input)
        mqtt_settings_layout.addRow("Port:", self.port_input)
        mqtt_settings_layout.addRow("Username:", self.username_input)
        mqtt_settings_layout.addRow("Password:", self.password_input)

        mqtt_page_layout.addWidget(self.mqtt_settings_frame)
        mqtt_page_layout.addStretch(1)


        # MQTT Groups Settings
        self.mqtt_groups_page = QWidget()
        mqtt_groups_group = QGroupBox("MQTT Group Topics")
        mqtt_groups_layout = QVBoxLayout(mqtt_groups_group)
        mqtt_groups_layout.setContentsMargins(10, 10, 10, 10)
        mqtt_groups_layout.addWidget(QLabel("Manage MQTT Topics for Group Chats:"))

        self.mqtt_group_list_widget = QListWidget()
        self.mqtt_group_list_widget.setAlternatingRowColors(True)
        self.mqtt_group_list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.mqtt_group_list_widget.setMinimumHeight(60)
        mqtt_groups_layout.addWidget(self.mqtt_group_list_widget)

        mqtt_groups_buttons_layout = QHBoxLayout()
        self.add_mqtt_topic_button = QPushButton("Add Topic")
        self.remove_mqtt_topic_button = QPushButton("Remove Selected")
        mqtt_groups_buttons_layout.addWidget(self.add_mqtt_topic_button)
        mqtt_groups_buttons_layout.addWidget(self.remove_mqtt_topic_button)
        mqtt_groups_buttons_layout.addStretch(1)
        mqtt_groups_layout.addLayout(mqtt_groups_buttons_layout)
        mqtt_groups_page_layout = QVBoxLayout(self.mqtt_groups_page)
        mqtt_groups_page_layout.addWidget(mqtt_groups_group)
        mqtt_groups_page_layout.addStretch(1)


        # Buddy Groups Settings
        self.buddy_groups_page = QWidget()
        buddy_groups_group = QGroupBox("Custom Buddy Groups")
        buddy_groups_layout = QVBoxLayout(buddy_groups_group)
        buddy_groups_layout.setContentsMargins(10, 10, 10, 10)
        buddy_groups_layout.addWidget(QLabel("Manage Custom Groups for Buddies:"))

        self.buddy_groups_list_widget = QListWidget()
        self.buddy_groups_list_widget.setAlternatingRowColors(True)
        self.buddy_groups_list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.buddy_groups_list_widget.setMinimumHeight(60)
        buddy_groups_layout.addWidget(self.buddy_groups_list_widget)

        buddy_groups_buttons_layout = QHBoxLayout()
        self.add_group_button = QPushButton("Add Group")
        self.edit_group_button = QPushButton("Edit Selected")
        self.remove_group_button = QPushButton("Remove Selected")
        buddy_groups_buttons_layout.addWidget(self.add_group_button)
        buddy_groups_buttons_layout.addWidget(self.edit_group_button)
        buddy_groups_buttons_layout.addWidget(self.remove_group_button)
        buddy_groups_buttons_layout.addStretch(1)
        buddy_groups_layout.addLayout(buddy_groups_buttons_layout)
        buddy_groups_page_layout = QVBoxLayout(self.buddy_groups_page)
        buddy_groups_page_layout.addWidget(buddy_groups_group)
        buddy_groups_page_layout.addStretch(1)


        # Node Map Settings
        self.map_page = QWidget()
        map_group = QGroupBox("Node Map Settings")
        map_layout = QVBoxLayout(map_group)
        map_layout.setContentsMargins(10, 10, 10, 10)

        # Online Settings
        map_online_group = QGroupBox("Online Tile Server")
        map_online_form = QFormLayout(map_online_group)
        map_online_form.setContentsMargins(10, 10, 10, 10)
        self.map_online_url_input = QLineEdit()
        self.map_online_url_input.setPlaceholderText("e.g., https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        self.map_online_url_input.setToolTip("URL pattern for fetching online map tiles.")
        map_online_form.addRow("Tile URL:", self.map_online_url_input)
        map_layout.addWidget(map_online_group)

        # Offline Settings Toggle
        self.map_offline_toggle = QToolButton()
        self.map_offline_toggle.setText("Enable Offline Map Tiles")
        self.map_offline_toggle.setCheckable(True)
        self.map_offline_toggle.setStyleSheet("QToolButton { border: none; text-align: left; padding: 5px 0px; font-weight: bold; }")
        self.map_offline_toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        map_layout.addWidget(self.map_offline_toggle)


        # Offline Settings Group (hidden by default)
        self.map_offline_settings_group = QGroupBox("Offline Tiles")
        self.map_offline_settings_group.setVisible(False)
        map_offline_form = QFormLayout(self.map_offline_settings_group)
        map_offline_form.setContentsMargins(10, 10, 10, 10)
        self.map_offline_dir_input = QLineEdit()
        self.map_offline_dir_input.setPlaceholderText("Path to your offline tile directory")
        self.map_offline_dir_input.setToolTip("Directory containing offline map tiles (e.g., .../tiles/{z}/{x}/{y}.png)")
        map_offline_form.addRow("Tile Directory:", self.map_offline_dir_input)
        map_layout.addWidget(self.map_offline_settings_group)

        # Default View Settings
        map_default_view_group = QGroupBox("Default View")
        map_default_view_form = QFormLayout(map_default_view_group)
        map_default_view_form.setContentsMargins(10, 10, 10, 10)

        self.map_default_lat_input = QDoubleSpinBox()
        self.map_default_lat_input.setRange(-90.0, 90.0)
        self.map_default_lat_input.setDecimals(6)
        self.map_default_lat_input.setToolTip("Default latitude for the map center.")
        map_default_view_form.addRow("Center Latitude:", self.map_default_lat_input)

        self.map_default_lon_input = QDoubleSpinBox()
        self.map_default_lon_input.setRange(-180.0, 180.0)
        self.map_default_lon_input.setDecimals(6)
        self.map_default_lon_input.setToolTip("Default longitude for the map center.")
        map_default_view_form.addRow("Center Longitude:", self.map_default_lon_input)

        self.map_default_zoom_input = QSpinBox()
        self.map_default_zoom_input.setRange(0, 19) # Typical zoom levels
        self.map_default_zoom_input.setToolTip("Default zoom level for the map.")
        map_default_view_form.addRow("Default Zoom:", self.map_default_zoom_input)
        map_layout.addWidget(map_default_view_group)

        map_page_layout = QVBoxLayout(self.map_page)
        map_page_layout.addWidget(map_group)
        map_page_layout.addStretch(1)


        # General Settings
        self.general_page = QWidget()
        general_group = QGroupBox("General Application Settings")
        general_layout = QVBoxLayout(general_group)
        general_layout.setContentsMargins(10, 10, 10, 10)

        self.auto_save_checkbox = QCheckBox("Auto-Save Conversations Locally")
        self.auto_save_checkbox.setToolTip("Save sent and received messages to files.")
        general_layout.addWidget(self.auto_save_checkbox)

        self.enable_sounds_checkbox = QCheckBox("Enable Sounds")
        self.enable_sounds_checkbox.setToolTip("Enable application sound effects.")
        general_layout.addWidget(self.enable_sounds_checkbox)

        self.enable_updates_checkbox = QCheckBox("Enable Update Notifications")
        self.enable_updates_checkbox.setToolTip("Receive notifications about application updates (requires internet).")
        self.enable_updates_checkbox.setChecked(True)
        general_layout.addWidget(self.enable_updates_checkbox)

        self.enable_message_notifications_checkbox = QCheckBox("Enable Message Notifications")
        self.enable_message_notifications_checkbox.setToolTip("Show a notification when a new message is received.")
        general_layout.addWidget(self.enable_message_notifications_checkbox)

        general_page_layout = QVBoxLayout(self.general_page)
        general_page_layout.addWidget(general_group)
        general_page_layout.addStretch(1)


    def _add_category_and_page(self, name, page_widget):
        self.category_list.addItem(name)
        self.pages_stack.addWidget(page_widget)


    def update_channel_display(self, channels: List[Dict[str, Any]]):
        self.channel_list_widget.clear()
        if not channels:
            self.channel_list_widget.addItem("No channel data received from device.")
            return

        for ch_data in channels:
            index = ch_data.get('index', '?')
            name = ch_data.get('name', 'Unknown')
            encrypted = ch_data.get('encrypted', False)
            status = "(Encrypted)" if encrypted else "(Open)"
            display_text = f"[{index}] {name} {status}"
            self.channel_list_widget.addItem(display_text)

    def update_mesh_details_state(self, index):
        selected_type = self.mesh_connection_type.currentText()
        is_none = (selected_type == "None")
        self.mesh_connection_details.setEnabled(not is_none)
        if is_none:
            self.mesh_connection_details.clear()
            self.mesh_connection_details.setPlaceholderText("No connection selected")
        elif selected_type == "Serial":
            if not self.mesh_connection_details.text():
                self.find_serial_ports()
            else:
                 self.mesh_connection_details.setPlaceholderText("e.g., COM3 or /dev/ttyUSB0")
        else:
            if not self.mesh_connection_details.text():
                 self.mesh_connection_details.setPlaceholderText("e.g., 192.168.1.X or device.local")

    def find_serial_ports(self):
        if 'serial' not in globals() or serial is None:
             self.mesh_connection_details.setPlaceholderText("pyserial not found. Enter manually.")
             return
        try:
            ports = serial.tools.list_ports.comports()
            port_list = [p.device for p in ports]
            if port_list:
                if not self.mesh_connection_details.text():
                    self.mesh_connection_details.setText(port_list[0])
                self.mesh_connection_details.setPlaceholderText("Auto-detected port (or enter manually)")
            else:
                self.mesh_connection_details.setPlaceholderText("No ports found. Enter manually.")
        except Exception:
            self.mesh_connection_details.setPlaceholderText("Error detecting ports.")

    def add_mqtt_group_topic(self):
        topic, ok = QInputDialog.getText(self, "Add MQTT Group Topic", "Enter MQTT Topic:")
        if ok and topic:
            topic = topic.strip()
            if topic and self.mqtt_group_list_widget.findItems(topic, Qt.MatchExactly | Qt.MatchCaseSensitive):
                QMessageBox.warning(self, "Duplicate Topic", f"Topic '{topic}' already exists.")
            elif topic:
                self.mqtt_group_list_widget.addItem(topic)

    def remove_mqtt_group_topic(self):
        selected_items = self.mqtt_group_list_widget.selectedItems()
        if not selected_items:
            return
        for item in selected_items:
            self.mqtt_group_list_widget.takeItem(self.mqtt_group_list_widget.row(item))

    def add_buddy_group(self):
        group_name, ok = QInputDialog.getText(self, "Add Buddy Group", "Enter Group Name:")
        if ok and group_name:
            group_name = group_name.strip()
            default_groups_lower = ["public chat", "buddies", "family", "co-workers", "meshtastic nodes", "sensors", "other nodes", "offline"]
            if group_name.lower() in default_groups_lower:
                 QMessageBox.warning(self, "Invalid Group Name", f"'{group_name}' is a reserved default group name.")
            elif group_name and self.buddy_groups_list_widget.findItems(group_name, Qt.MatchExactly | Qt.MatchCaseSensitive):
                QMessageBox.warning(self, "Duplicate Group Name", f"Group '{group_name}' already exists.")
            elif group_name:
                self.buddy_groups_list_widget.addItem(group_name)

    def edit_buddy_group(self):
        selected_items = self.buddy_groups_list_widget.selectedItems()
        if not selected_items:
            return
        current_item = selected_items[0]
        current_name = current_item.text()
        new_name, ok = QInputDialog.getText(self, "Edit Buddy Group", "Edit Group Name:", QLineEdit.Normal, current_name)
        if ok and new_name and new_name != current_name:
            new_name = new_name.strip()
            default_groups_lower = ["public chat", "buddies", "family", "co-workers", "meshtastic nodes", "sensors", "other nodes", "offline"]
            if new_name.lower() in default_groups_lower:
                 QMessageBox.warning(self, "Invalid Group Name", f"'{new_name}' is a reserved default group name.")
            elif new_name and self.buddy_groups_list_widget.findItems(new_name, Qt.MatchExactly | Qt.MatchCaseSensitive):
                QMessageBox.warning(self, "Duplicate Group Name", f"Group '{new_name}' already exists.")
            elif new_name:
                current_item.setText(new_name)

    def remove_buddy_group(self):
        selected_items = self.buddy_groups_list_widget.selectedItems()
        if not selected_items:
            return
        reply = QMessageBox.question(self, 'Remove Group', 'Are you sure you want to remove the selected group(s)? Buddy assignments to this group will be lost.',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
             for item in selected_items:
                 self.buddy_groups_list_widget.takeItem(self.buddy_groups_list_widget.row(item))


    def load_initial_settings(self):
        self.screen_name_input.setText(self.current_settings.get("screen_name", ""))
        self.mesh_connection_type.setCurrentText(self.current_settings.get("mesh_conn_type", "None"))
        self.mesh_connection_details.setText(self.current_settings.get("mesh_details", ""))
        self.enable_message_notifications_checkbox.setChecked(
            bool(self.current_settings.get("message_notifications_enabled", True)))

        default_channel_index = self.current_settings.get("meshtastic_channel_index", 0)
        self.mesh_channel_index_input.setText(str(default_channel_index))

        mqtt_server = self.current_settings.get("server", "")
        mqtt_enabled = bool(mqtt_server) or bool(self.current_settings.get("username"))
        self.mqtt_toggle_button.setChecked(mqtt_enabled)
        self.mqtt_settings_frame.setVisible(mqtt_enabled)

        self.server_input.setText(mqtt_server)
        self.port_input.setText(str(self.current_settings.get("port", "1883")))
        self.username_input.setText(self.current_settings.get("username", ""))
        self.password_input.setText(self.current_settings.get("password", ""))

        mqtt_group_topics = self.current_settings.get("mqtt_group_topics", [])
        self.mqtt_group_list_widget.clear()
        for topic in mqtt_group_topics:
            self.mqtt_group_list_widget.addItem(topic)

        custom_buddy_groups = self.current_settings.get("custom_buddy_groups", [])
        self.buddy_groups_list_widget.clear()
        for group_name in custom_buddy_groups:
             self.buddy_groups_list_widget.addItem(group_name)

        # Load Map Settings
        map_offline_enabled = self.current_settings.get("map_offline_enabled", False)
        self.map_offline_toggle.setChecked(map_offline_enabled)
        self.map_offline_settings_group.setVisible(map_offline_enabled)
        self.map_online_url_input.setText(self.current_settings.get("map_online_tile_url", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"))
        self.map_offline_dir_input.setText(self.current_settings.get("map_offline_directory", ""))
        self.map_default_lat_input.setValue(self.current_settings.get("map_default_center_lat", 40.0))
        self.map_default_lon_input.setValue(self.current_settings.get("map_default_center_lon", -100.0))
        self.map_default_zoom_input.setValue(self.current_settings.get("map_default_zoom", 4))


        self.auto_save_checkbox.setChecked(bool(self.current_settings.get("auto_save_chats", False)))
        self.enable_sounds_checkbox.setChecked(bool(self.current_settings.get("sounds_enabled", True)))
        self.enable_updates_checkbox.setChecked(bool(self.current_settings.get("enable_update_notifications", True)))


    def get_settings(self):
        mesh_type = self.mesh_connection_type.currentText()

        channel_index_str = self.mesh_channel_index_input.text().strip()
        try:
            channel_index = int(channel_index_str)
            if channel_index < 0: channel_index = 0
        except ValueError:
            channel_index = 0

        mqtt_group_topics = [self.mqtt_group_list_widget.item(i).text() for i in range(self.mqtt_group_list_widget.count())]
        custom_buddy_groups = [self.buddy_groups_list_widget.item(i).text() for i in range(self.buddy_groups_list_widget.count())]


        settings = {
            "screen_name": self.screen_name_input.text().strip(),
            "mesh_conn_type": mesh_type,
            "mesh_details": self.mesh_connection_details.text().strip() if mesh_type != "None" else "",
            "meshtastic_channel_index": channel_index,
            "mqtt_group_topics": mqtt_group_topics,
            "custom_buddy_groups": custom_buddy_groups,
            "auto_save_chats": self.auto_save_checkbox.isChecked(),
            "sounds_enabled": self.enable_sounds_checkbox.isChecked(),
            "enable_update_notifications": self.enable_updates_checkbox.isChecked(),
            "server": "",
            "port": 1883,
            "username": "",
            "password": "",
            # Map Settings
            "map_online_tile_url": self.map_online_url_input.text().strip(),
            "map_offline_enabled": self.map_offline_toggle.isChecked(),
            "map_offline_directory": self.map_offline_dir_input.text().strip(),
            "map_default_center_lat": self.map_default_lat_input.value(),
            "map_default_center_lon": self.map_default_lon_input.value(),
            "map_default_zoom": self.map_default_zoom_input.value(),
            "message_notifications_enabled": self.enable_message_notifications_checkbox.isChecked()
        }

        if self.mqtt_toggle_button.isChecked() or self.server_input.text().strip() or self.username_input.text().strip():
             settings["server"] = self.server_input.text().strip()
             settings["username"] = self.username_input.text().strip()
             settings["password"] = self.password_input.text()
             try:
                 port_val = int(self.port_input.text().strip())
                 settings["port"] = port_val if 1 <= port_val <= 65535 else 1883
             except ValueError:
                 settings["port"] = 1883

        if "buddy_group_assignments" in self.current_settings:
             settings["buddy_group_assignments"] = self.current_settings["buddy_group_assignments"]


        return settings

    def accept(self):
        new_settings = self.get_settings()

        if not new_settings["screen_name"]:
            QMessageBox.warning(self, "Input Error", "Screen Name cannot be empty.")
            return

        if new_settings["mesh_conn_type"] != "None" and not new_settings["mesh_details"]:
            QMessageBox.warning(self, "Input Error", "Meshtastic connection details required when connection type is not 'None'.")
            return

        if new_settings["meshtastic_channel_index"] < 0:
            QMessageBox.warning(self, "Input Error", "Meshtastic Channel Index cannot be negative.")
            return

        if new_settings["server"]:
             if not (1 <= new_settings["port"] <= 65535):
                 QMessageBox.warning(self, "Input Error", "Invalid MQTT Port (must be 1-65535).")
                 return

        if new_settings["map_offline_enabled"] and not new_settings["map_offline_directory"]:
             QMessageBox.warning(self, "Input Error", "Offline map directory is required when offline tiles are enabled.")
             return

        self.settings_saved.emit(new_settings)
        super().accept()

    def reject(self):
        super().reject()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    dummy_config = {
        "screen_name": "TestUser",
        "mesh_conn_type": "Serial",
        "mesh_details": "COM1",
        "meshtastic_channel_index": 0,
        "mqtt_group_topics": ["test/topic1", "another/group"],
        "custom_buddy_groups": ["Family", "Work", "Friends"],
        "buddy_group_assignments": {"!123": "Family", "!456": "Work"},
        "auto_save_chats": True,
        "sounds_enabled": True,
        "enable_update_notifications": True,
        "server": "mqtt.example.com",
        "port": 1883,
        "username": "test",
        "password": "password",
        "map_online_tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "map_offline_enabled": True,
        "map_offline_directory": "/home/user/tiles",
        "map_default_center_lat": 34.05,
        "map_default_center_lon": -118.25,
        "map_default_zoom": 10
    }

    settings_win = SettingsWindow(current_settings=dummy_config)
    settings_win.settings_saved.connect(lambda s: print("Saved Settings:", s))
    settings_win.show()
    sys.exit(app.exec())
