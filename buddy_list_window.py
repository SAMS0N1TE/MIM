import sys
import os
import time
import datetime
import traceback
from sound_utils import play_sound_async, set_sounds_enabled
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeView, QMenu, QMenuBar, QStatusBar,
    QSpacerItem, QSizePolicy, QComboBox, QApplication, QMessageBox,
    QInputDialog, QLineEdit, QFrame, QSystemTrayIcon, QTabWidget
)
from PySide6.QtGui import (
    QStandardItemModel, QStandardItem, QFont, QIcon, QAction, QPixmap,
    QFontDatabase, QKeySequence
)
from PySide6.QtCore import Qt, Signal, QTimer, Slot, QStandardPaths, QCoreApplication, QSize, QEvent, QPoint

from pathlib import Path
from settings_window import SettingsWindow
from chat_window import ChatWindow, sanitize_filename
import paho.mqtt.client as mqtt

NODE_OFFLINE_TIMEOUT_SEC = 10 * 60
LOGS_SUBDIR = "chat_logs"
PUBLIC_CHAT_ID = "^all"

NODE_ID_ROLE = Qt.UserRole + 0
ITEM_TYPE_ROLE = Qt.UserRole + 1
HW_MODEL_ROLE = Qt.UserRole + 2
BATTERY_LEVEL_ROLE = Qt.UserRole + 3
SNR_ROLE = Qt.UserRole + 4
LAST_HEARD_ROLE = Qt.UserRole + 5

ASSIGNED_GROUP_ROLE = Qt.UserRole + 6


def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def compute_node_status(node_data):
    NODE_RECENTLY_ACTIVE_TIMEOUT_SEC = 60 * 5  # e.g., Online if heard within 5 minutes
    NODE_CONSIDERED_AWAY_TIMEOUT_SEC = 60 * 15  # e.g., Away if heard within 15 minutes (and not Online)

    if not node_data:
        return "Offline"

    if node_data.get('active_report', False):
        return "Online"

    last_heard_val = node_data.get('lastHeard', 0.0)
    try:
        last_heard = float(last_heard_val)
    except (ValueError, TypeError):
        last_heard = 0.0

    current_time = time.time()
    time_diff = current_time - last_heard

    if time_diff < 0:
        return "Online"

    if time_diff <= NODE_RECENTLY_ACTIVE_TIMEOUT_SEC:
        return "Online"
    elif time_diff <= NODE_CONSIDERED_AWAY_TIMEOUT_SEC:
        return "Away"
    else:
        return "Offline"

def format_timestamp(ts):
    if ts is None:
        return "N/A"
    try:
        dt_object = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(tz=None)
        return dt_object.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "Invalid Date"


class BuddyListWindow(QMainWindow):
    groupAssignmentsChanged = Signal(dict)
    sign_off_requested = Signal()
    quit_requested = Signal()
    send_message_requested = Signal(str, str, str)
    config_updated = Signal(dict)
    map_view_requested = Signal()
    settings_requested = Signal()

    def __init__(self, screen_name, connection_settings, app_config=None):
        super().__init__()
        self.screen_name = screen_name
        self.connection_settings = connection_settings
        self.app_config = app_config if app_config else {}
        self.chat_windows = {}
        self.displayed_mesh_nodes = set()
        self.tray_icon = None
        self._is_closing = False
        self._buddy_group_assignments = self.app_config.get("buddy_group_assignments", {}) # Load assignments
        self.app_config = app_config if app_config else {}
        self._message_notifications_enabled = self.app_config.get("message_notifications_enabled", True)
        self.mqtt_group_unread_counts = {}
        self.default_font = self.font()
        self.bold_font = QFont(self.default_font)
        self.bold_font.setBold(True)


        icon_path_base = get_resource_path("resources/icons/")
        self.online_icon = QIcon(os.path.join(icon_path_base, "buddy_online.png"))
        self.offline_icon = QIcon(os.path.join(icon_path_base, "buddy_offline.png"))
        self.away_icon = QIcon(os.path.join(icon_path_base, "buddy_away.png"))
        self.public_chat_icon = QIcon(os.path.join(icon_path_base, "group_chat.png"))
        self.mqtt_group_icon = QIcon(os.path.join(icon_path_base, "mqtt_group.png"))
        if self.mqtt_group_icon.isNull():
             self.mqtt_group_icon = QIcon(os.path.join(icon_path_base, "group_chat.png"))
        self.app_icon = QIcon(os.path.join(icon_path_base, "mim_logo.png"))

        self.setWindowIcon(self.app_icon)
        self.setWindowTitle(f"{self.screen_name} - Buddy List")
        self.setMinimumSize(200, 450)

        self._create_tray_icon()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        LOGO_AREA_BG_COLOR = "#033b72"
        LOGO_SIZE = QSize(90, 90)
        LOGO_AREA_MARGINS = (10, 10, 10, 10)

        logo_frame = QFrame()
        logo_frame.setStyleSheet(f"background-color: {LOGO_AREA_BG_COLOR}; border: none;")
        logo_layout = QVBoxLayout(logo_frame)
        logo_layout.setAlignment(Qt.AlignCenter)
        logo_layout.setContentsMargins(*LOGO_AREA_MARGINS)

        logo_label = QLabel()
        pixmap = self.app_icon.pixmap(LOGO_SIZE)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(LOGO_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled_pixmap)
        else:
            logo_label.setText("Logo")

        logo_layout.addWidget(logo_label)
        main_layout.addWidget(logo_frame, 0)

        status_layout = QHBoxLayout()
        status_label = QLabel("Status:")
        self.status_combo = QComboBox()
        self.status_combo.addItems(["Online", "Away", "Invisible", "Offline"])
        self.status_combo.setCurrentText("Online")
        self.status_combo.currentIndexChanged.connect(self.update_my_status)
        status_layout.addWidget(status_label)
        status_layout.addWidget(self.status_combo, 1)
        main_layout.addLayout(status_layout)

        self._create_menu_bar()

        self.list_tabs = QTabWidget()
        main_layout.addWidget(self.list_tabs, 1)

        self.meshtastic_buddies_widget = QWidget()
        meshtastic_layout = QVBoxLayout(self.meshtastic_buddies_widget)
        meshtastic_layout.setContentsMargins(0,0,0,0)

        self.buddy_tree = QTreeView()
        self.buddy_tree.setHeaderHidden(True)
        self.buddy_tree.setEditTriggers(QTreeView.NoEditTriggers)
        self.buddy_tree.setAlternatingRowColors(False)
        self.model = QStandardItemModel()
        self.buddy_tree.setModel(self.model)
        self.buddy_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.buddy_tree.customContextMenuRequested.connect(self.show_buddy_context_menu)

        meshtastic_layout.addWidget(self.buddy_tree)
        self.list_tabs.addTab(self.meshtastic_buddies_widget, "Buddies")

        self.mqtt_groups_widget = QWidget()
        mqtt_groups_layout = QVBoxLayout(self.mqtt_groups_widget)
        mqtt_groups_layout.setContentsMargins(0,0,0,0)

        self.mqtt_group_list_model = QStandardItemModel()
        self.mqtt_group_list_view = QTreeView()
        self.mqtt_group_list_view.setHeaderHidden(True)
        self.mqtt_group_list_view.setEditTriggers(QTreeView.NoEditTriggers)
        self.mqtt_group_list_view.setAlternatingRowColors(True)
        self.mqtt_group_list_view.setModel(self.mqtt_group_list_model)
        self.mqtt_group_list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.mqtt_group_list_view.customContextMenuRequested.connect(self.show_group_context_menu)

        mqtt_groups_layout.addWidget(self.mqtt_group_list_view)
        self.list_tabs.addTab(self.mqtt_groups_widget, "MQTT Groups")


        self._populate_initial_groups()
        self._load_mqtt_group_topics_from_config()


        button_layout = QHBoxLayout()
        self.im_button = QPushButton("IM")
        self.chat_button = QPushButton("Chat")
        self.setup_button = QPushButton("Setup")
        button_layout.addStretch(1)
        button_layout.addWidget(self.im_button)
        button_layout.addWidget(self.setup_button)
        button_layout.addStretch(1)
        main_layout.addLayout(button_layout)

        self.statusBar().showMessage("Initializing...")

        self.buddy_tree.doubleClicked.connect(self.handle_double_click)
        self.mqtt_group_list_view.doubleClicked.connect(self.handle_double_click)

        self.im_button.clicked.connect(self.send_im_button_clicked)
        self.setup_button.clicked.connect(self._request_settings)

        QTimer.singleShot(150, lambda: self.statusBar().showMessage("Ready"))

    def _get_log_file_path_for_chat_id(self, chat_id):
        if not self.app_config.get("auto_save_chats", False) or not chat_id:
            return None

        logs_base_dir = None
        app_data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "."
        app_name_folder = QCoreApplication.applicationName() or "MIMMeshtastic"
        base_config_dir = Path(app_data_dir) / app_name_folder
        try:
            base_config_dir.mkdir(parents=True, exist_ok=True)
            logs_path = base_config_dir / LOGS_SUBDIR
            logs_base_dir = str(logs_path)
            logs_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None

        if logs_base_dir:
            safe_chat_id = sanitize_filename(chat_id)
            return Path(logs_base_dir) / f"{safe_chat_id}.log"
        return None

    def _save_message_to_log(self, log_file_path, timestamp, sender, message_text):
        if not log_file_path:
            return
        try:
            log_line = f"[{timestamp.isoformat()}] {sender}: {message_text}\n"
            with open(log_file_path, 'a', encoding='utf-8') as f:
                f.write(log_line)
        except IOError:
            print(f"IOError saving message to {log_file_path}")
        except Exception as e:
            print(f"Exception saving message to {log_file_path}: {e}")

    def _get_matched_subscribed_group_topic(self, specific_topic):
        for subscribed_pattern in self.app_config.get("mqtt_group_topics", []):
            if mqtt.topic_matches_sub(subscribed_pattern, specific_topic):
                return subscribed_pattern
        return None

    def _indicate_new_mqtt_group_message(self, specific_message_topic, sender_name, message_text):
        matched_group_pattern = self._get_matched_subscribed_group_topic(specific_message_topic)

        ui_update_topic = matched_group_pattern if matched_group_pattern else specific_message_topic

        item = self.find_group_topic_item(ui_update_topic)

        if item:
            current_count = self.mqtt_group_unread_counts.get(ui_update_topic, 0)
            new_count = current_count + 1
            self.mqtt_group_unread_counts[ui_update_topic] = new_count

            original_text = ui_update_topic
            item.setText(f"{original_text} ({new_count})")
            item.setFont(self.bold_font)

        log_file_path = self._get_log_file_path_for_chat_id(ui_update_topic)
        if log_file_path:
            timestamp = datetime.datetime.now(datetime.timezone.utc)
            log_entry_text = f"[{specific_message_topic}] {message_text}"
            self._save_message_to_log(log_file_path, timestamp, sender_name if sender_name else "Unknown",
                                      log_entry_text)
        else:
            print(
                f"Message for group '{ui_update_topic}' (from specific topic '{specific_message_topic}') not saved to log (auto-save off or path error).")

    def _create_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip(f"{self.screen_name} - Meshtastic IM")

        tray_menu = QMenu(self)
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show_normal_window)
        quit_action = QAction("Quit", self)
        if hasattr(self, 'request_quit'):
            quit_action.triggered.connect(self.request_quit)
        else:
            pass
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)

        self.tray_icon.activated.connect(self.handle_tray_activation)

    @Slot(QSystemTrayIcon.ActivationReason)
    def handle_tray_activation(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.show_normal_window()

    def show_normal_window(self):
        if self.tray_icon:
            self.tray_icon.hide()
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _create_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._request_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        sign_off_action = QAction("&Sign Off", self)
        sign_off_action.triggered.connect(self.request_sign_off)
        file_menu.addAction(sign_off_action)
        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.Quit)
        if hasattr(self, 'request_quit'):
             exit_action.triggered.connect(self.request_quit)
        else:
             pass
        file_menu.addAction(exit_action)

        people_menu = menu_bar.addMenu("&People")
        im_action = QAction("&Send Instant Message...", self)
        im_action.triggered.connect(self.send_im_button_clicked)
        add_buddy_action = QAction("&Add Buddy...", self)
        add_buddy_action.triggered.connect(self.add_buddy_placeholder)
        people_menu.addAction(im_action)
        people_menu.addSeparator()
        people_menu.addAction(add_buddy_action)

        view_menu = menu_bar.addMenu("&View")
        map_action = QAction("&Map View", self)
        map_action.triggered.connect(self.map_view_requested.emit)
        view_menu.addAction(map_action)

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About Meshtastic Instant Messenger...", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    @Slot(bool)
    def set_message_notifications_enabled(self, enabled):
        self._message_notifications_enabled = enabled
        self.app_config["message_notifications_enabled"] = enabled

    def _populate_initial_groups(self):
        root_node = self.model.invisibleRootItem()
        root_node.removeRows(0, root_node.rowCount())
        self.groups = {}

        public_chat_group = QStandardItem("Public Chat")
        public_chat_group.setIcon(self.public_chat_icon)
        public_chat_font = QFont(); public_chat_font.setBold(True)
        public_chat_group.setFont(public_chat_font)
        public_chat_group.setEditable(False)
        public_chat_group.setData("group_public", ITEM_TYPE_ROLE)
        public_chat_group.setData(PUBLIC_CHAT_ID, NODE_ID_ROLE)
        root_node.appendRow(public_chat_group)
        self.groups["public chat"] = public_chat_group


        group_names = ["Buddies", "Family", "Co-Workers", "Meshtastic Nodes", "Sensors", "Other Nodes", "Offline"]
        custom_groups = self.app_config.get("custom_buddy_groups", [])
        for custom_group in custom_groups:
             if custom_group and custom_group.lower() not in self.groups:
                  group_names.append(custom_group)

        unique_group_names = []
        seen_groups_lower = set()
        for name in group_names:
            if name and name.lower() not in seen_groups_lower:
                unique_group_names.append(name)
                seen_groups_lower.add(name.lower())


        for name in unique_group_names:
            group_item = QStandardItem(name)
            group_font = QFont(); group_font.setBold(True)
            group_item.setFont(group_font)
            group_item.setEditable(False)
            group_item.setData("group", ITEM_TYPE_ROLE)
            root_node.appendRow(group_item)
            self.groups[name.lower()] = group_item
            is_expanded = name.lower() in ["public chat", "buddies", "meshtastic nodes", "offline"]
            self.buddy_tree.setExpanded(group_item.index(), is_expanded)

        self.buddy_tree.setExpanded(public_chat_group.index(), True)


    def _load_mqtt_group_topics_from_config(self):
        mqtt_group_topics = self.app_config.get("mqtt_group_topics", [])
        root_node = self.mqtt_group_list_model.invisibleRootItem()

        root_node.removeRows(0, root_node.rowCount())

        for topic in mqtt_group_topics:
            if topic:
                item = QStandardItem(topic)
                item.setEditable(False)
                item.setData(topic, NODE_ID_ROLE)
                item.setIcon(self.mqtt_group_icon)
                item.setToolTip(f"MQTT Group Topic: {topic}")
                item.setData("mqtt_group", ITEM_TYPE_ROLE)
                root_node.appendRow(item)
        root_node.sortChildren(0, Qt.AscendingOrder)
        if root_node.rowCount() > 0:
             self.mqtt_group_list_view.expandAll()


    def find_buddy_item(self, buddy_id):
        if buddy_id == PUBLIC_CHAT_ID: return None
        root = self.model.invisibleRootItem()
        for i in range(root.rowCount()):
            group_item = root.child(i, 0)
            item_type = group_item.data(ITEM_TYPE_ROLE)
            if item_type != "group_public" and item_type != "mqtt_group":
                for j in range(group_item.rowCount()):
                    buddy_item = group_item.child(j, 0)
                    if buddy_item and buddy_item.data(NODE_ID_ROLE) == buddy_id and buddy_item.data(ITEM_TYPE_ROLE) == "buddy":
                        return buddy_item
        return None

    def find_group_topic_item(self, topic):
         root = self.mqtt_group_list_model.invisibleRootItem()
         for i in range(root.rowCount()):
              topic_item = root.child(i, 0)
              if topic_item and topic_item.data(NODE_ID_ROLE) == topic and topic_item.data(ITEM_TYPE_ROLE) == "mqtt_group":
                  return topic_item
         return None


    def find_group_item(self, group_name):
        return self.groups.get(group_name.lower()) if group_name else self.groups.get("offline")


    @Slot(str, str, str, str, dict)
    def add_or_update_buddy(self, group_name, buddy_id, display_name, status, node_info=None, force_icon=None):
        if buddy_id == PUBLIC_CHAT_ID: return
        if buddy_id in self.app_config.get("mqtt_group_topics", []): return

        if node_info is None: node_info = {}

        existing_item = self.find_buddy_item(buddy_id)
        old_status = None

        user_data = node_info.get('user', {})
        metrics_data = node_info.get('deviceMetrics', {})
        hw_model = user_data.get('hwModel', 'N/A')
        battery_level = metrics_data.get('batteryLevel')
        snr = node_info.get('snr', 'N/A')
        last_heard_ts = node_info.get('lastHeard')

        last_heard_str = format_timestamp(last_heard_ts)

        if existing_item:
            current_group_item = existing_item.parent()
            if current_group_item:
                is_offline_group = current_group_item.text().lower() == "offline"
                if existing_item.icon() == self.offline_icon or is_offline_group:
                    old_status = "Offline"
                elif existing_item.icon() == self.away_icon:
                    old_status = "Away"
                else:
                    old_status = "Online"

        assigned_group_name = existing_item.data(
            ASSIGNED_GROUP_ROLE) if existing_item else self._buddy_group_assignments.get(buddy_id)

        if status == "Offline":
            if assigned_group_name and assigned_group_name.lower() != "offline" and assigned_group_name.lower() in self.groups:
                target_group_name = assigned_group_name
            else:
                target_group_name = "Offline"
        else:
            if assigned_group_name and assigned_group_name.lower() in self.groups:
                target_group_name = assigned_group_name
            else:
                target_group_name = group_name or "Buddies"

        target_group_item = self.find_group_item(target_group_name)
        if not target_group_item:
            target_group_item = self.find_group_item("Offline")  # Fallback

        if force_icon:
            icon = force_icon
        else:
            icon = self.online_icon if status == "Online" else self.away_icon if status == "Away" else self.offline_icon

        tooltip = f"ID: {buddy_id}\nStatus: {status}\nLast Heard: {last_heard_str}"
        if hw_model and hw_model != 'N/A': tooltip += f"\nHW Model: {hw_model}"
        if battery_level is not None: tooltip += f"\nBattery: {battery_level}%"
        if snr != 'N/A': tooltip += f"\nSNR: {snr}"

        if existing_item:
            current_group_item = existing_item.parent()
            if target_group_item != current_group_item:
                if current_group_item:
                    taken_item_row = current_group_item.takeRow(existing_item.row())
                    if taken_item_row:
                        target_group_item.appendRow(taken_item_row[0])
                        existing_item = target_group_item.child(target_group_item.rowCount() - 1, 0)
                    else:
                        existing_item = None
                else:
                    target_group_item.appendRow(existing_item)

            if existing_item:
                existing_item.setText(display_name)
                existing_item.setIcon(icon)
                existing_item.setToolTip(tooltip)
                existing_item.setData(hw_model, HW_MODEL_ROLE)
                existing_item.setData(battery_level, BATTERY_LEVEL_ROLE)
                existing_item.setData(snr, SNR_ROLE)
                existing_item.setData(last_heard_ts, LAST_HEARD_ROLE)
                existing_item.setData("buddy", ITEM_TYPE_ROLE)
                # Keep the assigned group role data if it exists, even if status-grouped differently
                if assigned_group_name:
                    existing_item.setData(assigned_group_name, ASSIGNED_GROUP_ROLE)

            if status != "Offline" or (
                    assigned_group_name and assigned_group_name.lower() != "offline"):
                self.buddy_tree.setExpanded(target_group_item.index(), True)
            target_group_item.sortChildren(0, Qt.AscendingOrder)

        else:
            item = QStandardItem(display_name)
            item.setEditable(False)
            item.setData(buddy_id, NODE_ID_ROLE)
            item.setIcon(icon)
            item.setToolTip(tooltip)
            item.setData(hw_model, HW_MODEL_ROLE)
            item.setData(battery_level, BATTERY_LEVEL_ROLE)
            item.setData(snr, SNR_ROLE)
            item.setData(last_heard_ts, LAST_HEARD_ROLE)
            item.setData("buddy", ITEM_TYPE_ROLE)
            if assigned_group_name:
                item.setData(assigned_group_name, ASSIGNED_GROUP_ROLE)

            target_group_item.appendRow(item)
            target_group_item.sortChildren(0, Qt.AscendingOrder)
            if status != "Offline" or (
                    assigned_group_name and assigned_group_name.lower() != "offline"):
                self.buddy_tree.setExpanded(target_group_item.index(), True)
            old_status = "Offline"

        play_buddy_sounds = self.app_config.get("sounds_enabled", True)
        if play_buddy_sounds:
            if old_status != "Online" and status == "Online":
                play_sound_async("buddyin.wav")
            elif old_status != "Offline" and status == "Offline":
                play_sound_async("buddyout.wav")


    def remove_buddy(self, buddy_id):
        item = self.find_buddy_item(buddy_id)
        if item:
            parent = item.parent()
            if parent:
                parent.removeRow(item.row())
            else:
                pass
            if buddy_id in self._buddy_group_assignments:
                 del self._buddy_group_assignments[buddy_id]
                 self.groupAssignmentsChanged.emit(self._buddy_group_assignments)

    @Slot(list)
    def handle_node_list_update(self, nodes_list):
        now = time.time()
        current_mesh_node_ids = set()

        print("[Buddy List] Processing node list update with", len(nodes_list), "nodes")

        my_node_id = None
        if hasattr(self, 'meshtastic_handler') and self.meshtastic_handler:
            if hasattr(self.meshtastic_handler, '_my_node_num') and self.meshtastic_handler._my_node_num:
                my_node_id = f"!{self.meshtastic_handler._my_node_num:x}"
                print(f"[Buddy List] Our node ID is: {my_node_id}")

        for node_data in nodes_list:
            user_info = node_data.get('user', {})
            node_id = user_info.get('id')

            if not node_id or node_id == self.connection_settings.get("screen_name") or node_id == PUBLIC_CHAT_ID:
                continue

            current_mesh_node_ids.add(node_id)
            display_name = user_info.get('longName') or user_info.get('shortName') or node_id

            is_active = node_data.get('active_report', False)
            if is_active:
                print(f"[Buddy List] Node {node_id} is ACTIVE, forcing Online status")
                status = "Online"
                icon = self.online_icon
            else:
                status = compute_node_status(node_data)
                if status == "Online":
                    icon = self.online_icon
                elif status == "Away":
                    icon = self.away_icon
                else:
                    icon = self.offline_icon

            self.add_or_update_buddy(None, node_id, display_name, status, node_data, force_icon=icon)

        nodes_to_remove = self.displayed_mesh_nodes - current_mesh_node_ids
        for node_id_to_remove in nodes_to_remove:
            item = self.find_buddy_item(node_id_to_remove)
            if item:
                display_name = item.text()
                self.add_or_update_buddy(None, node_id_to_remove, display_name, "Offline", None,
                                         force_icon=self.offline_icon)
            else:
                pass

        self.displayed_mesh_nodes = current_mesh_node_ids

        self._apply_saved_group_assignments()

        if my_node_id and hasattr(self, 'meshtastic_handler') and self.meshtastic_handler:
            if my_node_id in self.meshtastic_handler._nodes:
                node = self.meshtastic_handler._nodes[my_node_id]

                current_ui_status = self.status_combo.currentText()

                NODE_ACTIVE_TIMEOUT_SEC = 60 * 5

                is_active = node.get('active_report', False)

                if is_active:
                    my_actual_status = "Online"
                else:
                    last_heard = node.get('lastHeard', 0)
                    time_diff = now - last_heard

                    if time_diff > NODE_ACTIVE_TIMEOUT_SEC:
                        my_actual_status = "Offline"
                    else:
                        my_actual_status = "Away"

                if my_actual_status != current_ui_status and current_ui_status != "Offline":
                    print(f"[Buddy List] Updating our status in UI from '{current_ui_status}' to '{my_actual_status}'")
                    self.status_combo.blockSignals(True)
                    self.status_combo.setCurrentText(my_actual_status)
                    self.status_combo.blockSignals(False)

                    if my_actual_status == "Online":
                        node['active_report'] = True
                        node['lastHeard'] = now


    def _apply_saved_group_assignments(self):
        for buddy_id, assigned_group_name in list(self._buddy_group_assignments.items()):
            if not assigned_group_name:
                continue
            buddy_item = self.find_buddy_item(buddy_id)
            if buddy_item:
                target_group_item = self.find_group_item(assigned_group_name)
                if target_group_item:
                    current_group_item = buddy_item.parent()
                    is_offline = buddy_item.icon().cacheKey() == self.offline_icon.cacheKey()
                    should_move = (not is_offline) or (assigned_group_name.lower() == "offline")
                    if should_move and current_group_item != target_group_item:
                        if current_group_item:
                            row = current_group_item.takeRow(buddy_item.row())
                            if row:
                                target_group_item.appendRow(row[0])
                        else:
                            target_group_item.appendRow(buddy_item)
                        target_group_item.sortChildren(0, Qt.AscendingOrder)
                        self.buddy_tree.setExpanded(target_group_item.index(), True)
                        moved_item = self.find_buddy_item(buddy_id)
                        if moved_item:
                            moved_item.setData(assigned_group_name, ASSIGNED_GROUP_ROLE)
                else:
                    del self._buddy_group_assignments[buddy_id]
                    buddy_item.setData(None, ASSIGNED_GROUP_ROLE)
                    self.groupAssignmentsChanged.emit(self._buddy_group_assignments)
                    status = "Offline" if buddy_item.icon().cacheKey() == self.offline_icon.cacheKey() else "Online"
                    display_name = buddy_item.text()
                    self.add_or_update_buddy(None, buddy_id, display_name, status, None)


    def handle_double_click(self, index):
        item = None
        if index.model() is self.buddy_tree.model():
            item = self.model.itemFromIndex(index)
        elif index.model() is self.mqtt_group_list_view.model():
             item = self.mqtt_group_list_model.itemFromIndex(index)

        if not item: return

        item_type = item.data(ITEM_TYPE_ROLE)
        item_id = item.data(NODE_ID_ROLE)
        display_name = item.text()

        if item_type == "group_public":
            self.open_chat_window(PUBLIC_CHAT_ID, "Public Chat", 'meshtastic')
        elif item_type == "buddy" and item_id:
            self.open_chat_window(item_id, display_name, 'meshtastic')
        elif item_type == "mqtt_group" and item_id:
             self.open_chat_window(item_id, display_name, 'mqtt')


    def get_selected_item_info(self):
        current_view = self.list_tabs.currentWidget()
        selected_index = None
        item = None
        item_type = None
        item_id = None
        display_name = None

        if current_view == self.meshtastic_buddies_widget:
            indexes = self.buddy_tree.selectedIndexes()
            if indexes:
                selected_index = indexes[0]
                item = self.model.itemFromIndex(selected_index)
        elif current_view == self.mqtt_groups_widget:
             indexes = self.mqtt_group_list_view.selectedIndexes()
             if indexes:
                 selected_index = indexes[0]
                 item = self.mqtt_group_list_model.itemFromIndex(selected_index)

        if item:
            item_type = item.data(ITEM_TYPE_ROLE)
            item_id = item.data(NODE_ID_ROLE)
            display_name = item.text()

            if item_type == "group_public":
                 return PUBLIC_CHAT_ID, "Public Chat", "group_public"
            elif item_type == "buddy" and item_id:
                 return item_id, display_name, "buddy"
            elif item_type == "mqtt_group" and item_id:
                 return item_id, display_name, "mqtt_group"

        return None, None, None


    def send_im_button_clicked(self):
        item_id, display_name, item_type = self.get_selected_item_info()

        if item_type == "buddy":
            self.open_chat_window(item_id, display_name, 'meshtastic')
        elif item_type == "group_public":
            self.open_chat_window(PUBLIC_CHAT_ID, "Public Chat", 'meshtastic')
        elif item_type == "mqtt_group":
             self.open_chat_window(item_id, display_name, 'mqtt')
        else:
            QMessageBox.information(self, "Send IM", "Please select a buddy, Public Chat, or MQTT Group first.")

    def handle_send_request_from_chat(self, destination_id, text):
        """Handle message send requests from chat windows and route them to the appropriate network"""
        print(f"[Buddy List] Handling send request from chat: To={destination_id}, Text='{text[:20]}...'")

        if self.status_combo.currentText() != "Online":
            self.status_combo.setCurrentText("Online")
            self.update_my_status()  # Call the method to apply the change

        network_type = 'meshtastic'  # Default

        for window_key, chat_window in self.chat_windows.items():
            if window_key == destination_id and hasattr(chat_window, '_network_type'):
                network_type = chat_window._network_type
                break

        print(f"[Buddy List] Emitting send_message_requested with network_type={network_type}")
        self.send_message_requested.emit(destination_id, text, network_type)

    def open_chat_window(self, chat_id, display_name, network_type):
        if not chat_id:
            return

        if network_type == 'mqtt' and chat_id in self.app_config.get("mqtt_group_topics", []):
            self._reset_mqtt_group_message_indicator(chat_id)

        if chat_id in self.chat_windows and self.chat_windows[chat_id].isVisible():
            chat_win = self.chat_windows[chat_id]
            chat_win.activateWindow()
            chat_win.raise_()
            chat_win.setFocus()
            return

        auto_save = self.app_config.get("auto_save_chats", False)
        logs_base_dir = None
        if auto_save:
            app_data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or "."
            app_name_folder = QCoreApplication.applicationName() or "MIMMeshtastic"
            base_config_dir = Path(app_data_dir) / app_name_folder
            try:
                base_config_dir.mkdir(parents=True, exist_ok=True)
                logs_path = base_config_dir / LOGS_SUBDIR
                logs_base_dir = str(logs_path)
                logs_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                auto_save = False

        try:
            chat_win = ChatWindow(
                my_screen_name=self.screen_name,
                buddy_id=chat_id,
                display_name=display_name,
                auto_save_enabled=auto_save,
                logs_base_dir=logs_base_dir
            )
            title = display_name
            if network_type == 'mqtt' and chat_id in self.app_config.get("mqtt_group_topics", []):
                title = f"Group: {display_name}"
            elif chat_id == PUBLIC_CHAT_ID:
                title = "Public Chat"
            elif network_type == 'mqtt':
                title = f"IM with {display_name} (MQTT)"
            else:
                title = f"IM with {display_name}"

            chat_win.setWindowTitle(title)
            chat_win._network_type = network_type

            self.chat_windows[chat_id] = chat_win
            chat_win.closing.connect(lambda bid=chat_id: self.handle_chat_window_close(bid))
            chat_win.message_sent.connect(self.handle_send_request_from_chat)

            chat_win.show()
            chat_win.activateWindow()
            chat_win.raise_()
        except ImportError:
            QMessageBox.critical(self, "Error", "Chat window component failed.")
            traceback.print_exc()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Could not open chat window: {e}")

    @Slot(str, str, str, str, str)
    def handle_incoming_message(self,
                                chat_id_for_window_arg,
                                actual_message_text_arg,
                                source_network_arg,
                                message_type_arg,
                                sender_display_name_arg=None
                                ):
        is_mqtt_group_msg = (source_network_arg == 'mqtt' and message_type_arg == 'group')
        chat_id = chat_id_for_window_arg

        if is_mqtt_group_msg:
            self._indicate_new_mqtt_group_message(chat_id, sender_display_name_arg, actual_message_text_arg)

            if self._message_notifications_enabled and self.tray_icon and self.tray_icon.isVisible():
                group_item = self.find_group_topic_item(chat_id)
                group_display_name = group_item.text().split(' (')[0] if group_item else chat_id

                notification_title = f"New message in {group_display_name}"
                message_snippet_text = f"{sender_display_name_arg if sender_display_name_arg else 'Someone'}: {actual_message_text_arg}"
                message_snippet = message_snippet_text[:50] + "..." if len(
                    message_snippet_text) > 50 else message_snippet_text
                self.tray_icon.showMessage(notification_title, message_snippet, self.app_icon, 5000)

            if self.app_config.get("sounds_enabled", True):
                play_sound_async("receive.wav")

            return

        is_public_chat_msg = (source_network_arg == 'meshtastic' and message_type_arg == 'broadcast')
        notification_title_sender_name = sender_display_name_arg

        if is_public_chat_msg:
            notification_title_sender_name = sender_display_name_arg if sender_display_name_arg else chat_id
        elif not notification_title_sender_name:
            notification_title_sender_name = chat_id

        if self._message_notifications_enabled and self.tray_icon and self.tray_icon.isVisible():
            is_chat_window_active = False
            if chat_id in self.chat_windows and self.chat_windows[chat_id].isActiveWindow():
                is_chat_window_active = True
            if not self.isActiveWindow() and not is_chat_window_active:
                message_snippet_content = actual_message_text_arg
                final_notification_title = ""
                if is_public_chat_msg:
                    final_notification_title = f"Public: {notification_title_sender_name}"
                else:
                    final_notification_title = f"From: {notification_title_sender_name}"
                message_snippet = message_snippet_content[:50] + "..." if len(
                    message_snippet_content) > 50 else message_snippet_content
                self.tray_icon.showMessage(final_notification_title, message_snippet, self.app_icon, 5000)

        if self.app_config.get("sounds_enabled", True):
            play_sound_async("receive.wav")

        chat_window_title = chat_id
        if is_public_chat_msg:
            chat_window_title = "Public Chat"
        elif sender_display_name_arg:
            chat_window_title = sender_display_name_arg

        name_for_message_line = sender_display_name_arg if sender_display_name_arg else chat_id

        win_exists = chat_id in self.chat_windows and self.chat_windows[chat_id].isVisible()

        if not win_exists:
            self.open_chat_window(chat_id, chat_window_title, source_network_arg)
            QTimer.singleShot(150, lambda cid=chat_id, txt=actual_message_text_arg,
                                          sdn=name_for_message_line: self.route_message_to_window(cid, txt, sdn))
        else:
            self.route_message_to_window(chat_id, actual_message_text_arg, name_for_message_line)

        if not self.isActiveWindow():
            QApplication.alert(self)
        if chat_id in self.chat_windows and not self.chat_windows[chat_id].isActiveWindow():
            QApplication.alert(self.chat_windows[chat_id])

    def route_message_to_window(self, chat_id, text, sender_display_name):
        win = self.chat_windows.get(chat_id)
        if win and win.isVisible():
            try:
                win.receive_message(text, sender_display_name=sender_display_name)
                if not win.isActiveWindow():
                    win.raise_()
                    win.activateWindow()
            except Exception:
                traceback.print_exc()
        else:
            pass

    def _indicate_new_mqtt_group_message(self, group_topic, sender_name, message_text):
        item = self.find_group_topic_item(group_topic)
        if item:
            current_count = self.mqtt_group_unread_counts.get(group_topic, 0)
            new_count = current_count + 1
            self.mqtt_group_unread_counts[group_topic] = new_count

            original_text = group_topic
            item.setText(f"{original_text} ({new_count})")
            item.setFont(self.bold_font)

            if group_topic not in self.chat_windows or not self.chat_windows[group_topic].isVisible():
                print(f"Message for non-open group '{group_topic}': {sender_name}: {message_text}")

    def _reset_mqtt_group_message_indicator(self, group_topic_pattern):
        item = self.find_group_topic_item(group_topic_pattern)
        if item:
            self.mqtt_group_unread_counts[group_topic_pattern] = 0
            original_text = group_topic_pattern
            item.setText(original_text)
            item.setFont(self.default_font)

    @Slot(str)
    def handle_chat_window_close(self, chat_id):
        closed_window = self.chat_windows.pop(chat_id, None)
        if closed_window:
            pass

    @Slot(list)
    def handle_node_list_update(self, nodes_list):
        now = time.time()
        current_mesh_node_ids = set()

        print("[Buddy List] Processing node list update with", len(nodes_list), "nodes")

        my_node_id = None
        if hasattr(self, 'meshtastic_handler') and self.meshtastic_handler:
            if hasattr(self.meshtastic_handler, '_my_node_num') and self.meshtastic_handler._my_node_num:
                my_node_id = f"!{self.meshtastic_handler._my_node_num:x}"
                print(f"[Buddy List] Our node ID is: {my_node_id}")

        for node_data in nodes_list:
            user_info = node_data.get('user', {})
            node_id = user_info.get('id')

            if not node_id or node_id == self.connection_settings.get("screen_name") or node_id == PUBLIC_CHAT_ID:
                continue

            current_mesh_node_ids.add(node_id)
            display_name = user_info.get('longName') or user_info.get('shortName') or node_id

            is_active = node_data.get('active_report', False)
            if is_active:
                print(f"[Buddy List] Node {node_id} is ACTIVE, forcing Online status")
                status = "Online"
                icon = self.online_icon
            else:
                status = compute_node_status(node_data)
                if status == "Online":
                    icon = self.online_icon
                elif status == "Away":
                    icon = self.away_icon
                else:
                    icon = self.offline_icon

            self.add_or_update_buddy(None, node_id, display_name, status, node_data, force_icon=icon)

        nodes_to_remove = self.displayed_mesh_nodes - current_mesh_node_ids
        for node_id_to_remove in nodes_to_remove:
            item = self.find_buddy_item(node_id_to_remove)
            if item:
                display_name = item.text()
                self.add_or_update_buddy(None, node_id_to_remove, display_name, "Offline", None,
                                         force_icon=self.offline_icon)
            else:
                pass

        self.displayed_mesh_nodes = current_mesh_node_ids

        self._apply_saved_group_assignments()

        if my_node_id and hasattr(self, 'meshtastic_handler') and self.meshtastic_handler:
            if my_node_id in self.meshtastic_handler._nodes:
                node = self.meshtastic_handler._nodes[my_node_id]

                current_ui_status = self.status_combo.currentText()

                if node.get('active_report',
                            False) and current_ui_status != "Online" and current_ui_status != "Offline":
                    print(f"[Buddy List] Updating our status in UI from '{current_ui_status}' to 'Online'")
                    self.status_combo.blockSignals(True)
                    self.status_combo.setCurrentText("Online")
                    self.status_combo.blockSignals(False)


    @Slot()
    def _request_settings(self):
        self.settings_requested.emit()


    @Slot(dict)
    def _handle_settings_saved_locally(self, new_settings):
        old_assignments = self._buddy_group_assignments.copy()
        self.app_config.update(new_settings)
        self.set_message_notifications_enabled(self.app_config.get("message_notifications_enabled", True))
        self._buddy_group_assignments = self.app_config.get("buddy_group_assignments", {})

        set_sounds_enabled(self.app_config.get("sounds_enabled", True))
        self._load_mqtt_group_topics_from_config()
        self._populate_initial_groups()
        self._apply_saved_group_assignments()

    def update_my_status(self):
        status = self.status_combo.currentText()
        print(f"[Buddy List] Manual status change to: {status}")

        if hasattr(self, 'meshtastic_handler') and self.meshtastic_handler:
            my_node_id = None
            if hasattr(self.meshtastic_handler, '_my_node_num') and self.meshtastic_handler._my_node_num:
                my_node_id = f"!{self.meshtastic_handler._my_node_num:x}"

            if my_node_id and my_node_id in self.meshtastic_handler._nodes:
                node = self.meshtastic_handler._nodes[my_node_id]

                if status == "Online":
                    node['active_report'] = True
                    node['lastHeard'] = time.time()
                    print(f"[Buddy List] Set own node {my_node_id} to active/online")
                elif status == "Away":
                    node['active_report'] = False
                    node['lastHeard'] = time.time()
                    print(f"[Buddy List] Set own node {my_node_id} to inactive/away")
                elif status == "Offline":
                    print(f"[Buddy List] Manual offline status not fully implemented")
        else:
            print("[Buddy List] Cannot update status - no meshtastic handler available")


    def open_list_setup(self):
        QMessageBox.information(self, "Not Implemented", "List Setup not implemented.")


    def add_buddy_placeholder(self):
        text, ok = QInputDialog.getText(self, 'Add Buddy', 'Enter buddy ID (!hexid or mqtt_topic):')
        if ok and text:
            text = text.strip()
            if not text: return
            if text in self.app_config.get("mqtt_group_topics", []):
                 QMessageBox.information(self, "Add Buddy", "This ID is configured as an MQTT Group Topic.")
                 return

            if self.find_buddy_item(text):
                 QMessageBox.information(self, "Add Buddy", f"Buddy '{text}' already exists.")
                 return

            self.add_or_update_buddy("Buddies", text, text, "Offline", None)


    @Slot(str)
    def show_update_notification(self, message_text):
        if self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage("MIM Update", message_text, self.app_icon, 5000)
        else:
            QMessageBox.information(self, "MIM Update Notification", message_text)


    def show_about_dialog(self):
        QMessageBox.about(self, "About Meshtastic Instant Messenger",
                          "MIM - Meshtastic Instant Messenger\n\n"
                          "A simple AIM-like client using Meshtastic and/or MQTT.\n"
                          "(Based on initial concepts and code structure)")


    def request_sign_off(self):
        self._is_closing = True
        self.sign_off_requested.emit()


    def request_quit(self):
        self._is_closing = True
        self.quit_requested.emit()


    def closeEvent(self, event):
        if self._is_closing:
            event.accept()
            return

        event.ignore()
        self.hide()
        if self.tray_icon:
            self.tray_icon.show()
            self.tray_icon.showMessage("MIM", "Running in the background.", self.app_icon, 1500)

    @Slot(QPoint)
    def show_buddy_context_menu(self, point):
        index = self.buddy_tree.indexAt(point)
        if not index.isValid():
            return

        item = self.model.itemFromIndex(index)
        if not item:
            return

        item_type = item.data(ITEM_TYPE_ROLE)
        item_id = item.data(NODE_ID_ROLE)

        if item_type in ["group", "group_public"]:
             if item_type == "group_public":
                 menu = QMenu(self)
                 im_action = QAction("Open Public Chat", self)
                 im_action.triggered.connect(
                     lambda checked=False, id=PUBLIC_CHAT_ID, name="Public Chat", nt='meshtastic': self.open_chat_window(id, name, nt))
                 menu.addAction(im_action)
                 global_point = self.buddy_tree.viewport().mapToGlobal(point)
                 menu.exec_(global_point)
             return

        if item_type == "buddy" and item_id:
            menu = QMenu(self)

            im_action = QAction("Send Message", self)
            network_type = 'meshtastic' if item_id.startswith('!') else 'mqtt' # Simple heuristic for context menu
            im_action.triggered.connect(
                lambda checked=False, id=item_id, name=item.text(), nt=network_type: self.open_chat_window(id, name, nt))
            menu.addAction(im_action)

            info_action = QAction("Get Info", self)
            info_action.triggered.connect(lambda checked=False, itm=item: self.show_buddy_info(itm))
            menu.addAction(info_action)

            move_menu = menu.addMenu("Move to Group")
            available_groups = [g for g in self.groups.keys() if g != "public chat"] # Exclude public chat for moving buddies
            available_groups.sort()

            for group_name in available_groups:
                 group_action = QAction(group_name.capitalize(), self)
                 group_action.triggered.connect(
                     lambda checked=False, buddy=item_id, group=group_name: self.move_buddy_to_group(buddy, group))
                 move_menu.addAction(group_action)

            remove_action = QAction("Remove Buddy", self)
            remove_action.triggered.connect(lambda checked=False, id=item_id: self.remove_buddy(id))
            menu.addAction(remove_action)

            global_point = self.buddy_tree.viewport().mapToGlobal(point)
            menu.exec_(global_point)

    @Slot(str, str)
    def move_buddy_to_group(self, buddy_id, target_group_name):
        buddy_item = self.find_buddy_item(buddy_id)
        target_group_item = self.find_group_item(target_group_name)
        if buddy_item and target_group_item:
            current_group_item = buddy_item.parent()
            if current_group_item == target_group_item:
                 return

            taken = current_group_item.takeRow(buddy_item.row()) if current_group_item else None
            if taken:
                target_group_item.appendRow(taken[0])
                target_group_item.sortChildren(0, Qt.AscendingOrder)
                self.buddy_tree.setExpanded(target_group_item.index(), True)
                self._buddy_group_assignments[buddy_id] = target_group_name
                buddy_item.setData(target_group_name, ASSIGNED_GROUP_ROLE)
                self.groupAssignmentsChanged.emit(self._buddy_group_assignments)
            elif not current_group_item:
                 target_group_item.appendRow(buddy_item)
                 target_group_item.sortChildren(0, Qt.AscendingOrder)
                 self.buddy_tree.setExpanded(target_group_item.index(), True)
                 self._buddy_group_assignments[buddy_id] = target_group_name
                 buddy_item.setData(target_group_name, ASSIGNED_GROUP_ROLE)
                 self.groupAssignmentsChanged.emit(self._buddy_group_assignments)


    @Slot(QPoint)
    def show_group_context_menu(self, point):
         index = self.mqtt_group_list_view.indexAt(point)
         if not index.isValid():
              return

         item = self.mqtt_group_list_model.itemFromIndex(index)
         if not item or item.data(ITEM_TYPE_ROLE) != "mqtt_group":
              return

         item_id = item.data(NODE_ID_ROLE)
         display_name = item.text()

         menu = QMenu(self)

         im_action = QAction("Join/Send Message", self)
         im_action.triggered.connect(
             lambda checked=False, id=item_id, name=display_name, nt='mqtt': self.open_chat_window(id, name, nt))
         menu.addAction(im_action)

         global_point = self.mqtt_group_list_view.viewport().mapToGlobal(point)
         menu.exec_(global_point)


    def show_buddy_info(self, item):
        if not item or item.data(ITEM_TYPE_ROLE) != "buddy":
            return

        display_name = item.text()
        node_id = item.data(NODE_ID_ROLE)
        hw_model = item.data(HW_MODEL_ROLE) or "N/A"
        battery_level_raw = item.data(BATTERY_LEVEL_ROLE)
        snr_raw = item.data(SNR_ROLE)
        last_heard_ts = item.data(LAST_HEARD_ROLE)

        if battery_level_raw is None:
            battery_str = "N/A"
        elif battery_level_raw == 0:
            battery_str = "External/Unknown"
        else:
            battery_str = f"{battery_level_raw}%"

        try:
            snr_str = f"{snr_raw:.2f}" if isinstance(snr_raw, (int, float)) else str(snr_raw)
        except (TypeError, ValueError):
            snr_str = "N/A"

        last_heard_str = format_timestamp(last_heard_ts)

        info_text = (
            f"Node ID: {node_id}\n"
            f"Hardware: {hw_model}\n"
            f"Battery: {battery_str}\n"
            f"Last SNR: {snr_str}\n"
            f"Last Heard: {last_heard_str}"
        )

        QMessageBox.information(self, f"Node Info: {display_name}", info_text)
