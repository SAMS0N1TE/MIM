import sys
import os
import time
import traceback
from sound_utils import play_sound_async, set_sounds_enabled
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeView, QMenu, QMenuBar, QStatusBar,
    QSpacerItem, QSizePolicy, QComboBox, QApplication, QMessageBox,
    QInputDialog, QLineEdit, QFrame, QSystemTrayIcon
)
from PySide6.QtGui import (
    QStandardItemModel, QStandardItem, QFont, QIcon, QAction, QPixmap,
    QFontDatabase, QKeySequence
)
from PySide6.QtCore import Qt, Signal, QTimer, Slot, QStandardPaths, QCoreApplication, QSize, QEvent
from pathlib import Path
from settings_window import SettingsWindow

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

NODE_OFFLINE_TIMEOUT_SEC = 10 * 60
LOGS_SUBDIR = "chat_logs"
PUBLIC_CHAT_ID = "^all"

def compute_node_status(node_data):
    last_heard = node_data.get('lastHeard')
    if last_heard and (time.time() - last_heard < NODE_OFFLINE_TIMEOUT_SEC):
        return "Online"
    return "Offline"

class BuddyListWindow(QMainWindow):
    sign_off_requested = Signal()
    quit_requested = Signal()
    send_message_requested = Signal(str, str, str)
    config_updated = Signal(dict)

    def __init__(self, screen_name, connection_settings, app_config=None):
        super().__init__()
        self.screen_name = screen_name
        self.connection_settings = connection_settings
        self.app_config = app_config if app_config else {}
        self.chat_windows = {}
        self.displayed_mesh_nodes = set()
        self.tray_icon = None
        self._is_closing = False

        icon_path_base = get_resource_path("resources/icons/")
        self.online_icon = QIcon(os.path.join(icon_path_base, "buddy_online.png"))
        self.offline_icon = QIcon(os.path.join(icon_path_base, "buddy_offline.png"))
        self.away_icon = QIcon(os.path.join(icon_path_base, "buddy_away.png"))
        self.public_chat_icon = QIcon(os.path.join(icon_path_base, "group_chat.png"))
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

        self.buddy_tree = QTreeView()
        self.buddy_tree.setHeaderHidden(True)
        self.buddy_tree.setEditTriggers(QTreeView.NoEditTriggers)
        self.buddy_tree.setAlternatingRowColors(False)
        self.model = QStandardItemModel()
        self.buddy_tree.setModel(self.model)
        self._populate_initial_groups()
        main_layout.addWidget(self.buddy_tree, 1)

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
        self.im_button.clicked.connect(self.send_im_button_clicked)
        self.setup_button.clicked.connect(self._request_settings)

        QTimer.singleShot(150, lambda: self.statusBar().showMessage("Ready"))

    def _create_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("System tray not available.")
            return

        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip(f"{self.screen_name} - Meshtastic IM")

        tray_menu = QMenu(self)
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show_normal_window)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.request_quit)
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
        exit_action.triggered.connect(self.request_quit)
        file_menu.addAction(exit_action)

        people_menu = menu_bar.addMenu("&People")
        im_action = QAction("&Send Instant Message...", self)
        im_action.triggered.connect(self.send_im_button_clicked)
        add_buddy_action = QAction("&Add Buddy...", self)
        add_buddy_action.triggered.connect(self.add_buddy_placeholder)
        people_menu.addAction(im_action)
        people_menu.addSeparator()
        people_menu.addAction(add_buddy_action)

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About Meshtastic Instant Messenger...", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def _populate_initial_groups(self):
        self.groups = {}
        root_node = self.model.invisibleRootItem()

        public_chat_group = QStandardItem("Public Chat")
        public_chat_group.setIcon(self.public_chat_icon)
        public_chat_font = QFont(); public_chat_font.setBold(True)
        public_chat_group.setFont(public_chat_font)
        public_chat_group.setEditable(False)
        public_chat_group.setData("group_public", Qt.UserRole + 1)
        public_chat_group.setData(PUBLIC_CHAT_ID, Qt.UserRole)
        root_node.appendRow(public_chat_group)
        self.groups["public chat"] = public_chat_group

        group_names = ["Buddies", "Family", "Co-Workers", "Meshtastic Nodes", "Sensors", "Other Nodes", "Offline"]
        for name in group_names:
            group_item = QStandardItem(name)
            group_font = QFont(); group_font.setBold(True)
            group_item.setFont(group_font)
            group_item.setEditable(False)
            group_item.setData("group", Qt.UserRole + 1)
            root_node.appendRow(group_item)
            self.groups[name.lower()] = group_item
            is_expanded = name.lower() in ["public chat", "buddies", "meshtastic nodes", "offline"]
            self.buddy_tree.setExpanded(group_item.index(), is_expanded)
        self.buddy_tree.setExpanded(public_chat_group.index(), True)

    def find_buddy_item(self, buddy_id):
        if buddy_id == PUBLIC_CHAT_ID: return None
        root = self.model.invisibleRootItem()
        for i in range(root.rowCount()):
            group_item = root.child(i, 0)
            if group_item and group_item.data(Qt.UserRole + 1) != "group_public":
                for j in range(group_item.rowCount()):
                    buddy_item = group_item.child(j, 0)
                    if buddy_item and buddy_item.data(Qt.UserRole) == buddy_id:
                        return buddy_item
        return None

    def find_group_item(self, group_name):
        return self.groups.get(group_name.lower()) if group_name else self.groups.get("offline")

    @Slot(str, str, str, str)
    def add_or_update_buddy(self, group_name, buddy_id, display_name, status):
        if buddy_id == PUBLIC_CHAT_ID: return

        existing_item = self.find_buddy_item(buddy_id)
        old_status = None

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

        target_group_name = "Offline" if status == "Offline" else (group_name or "Buddies")
        target_group_item = self.find_group_item(target_group_name)
        if not target_group_item:
            print(f"Warning: Target group '{target_group_name}' not found for buddy '{buddy_id}'. Using Offline.")
            target_group_item = self.find_group_item("Offline")

        icon = self.online_icon if status == "Online" else self.away_icon if status == "Away" else self.offline_icon
        tooltip = f"ID: {buddy_id}\nStatus: {status}"

        if existing_item:
            current_group_item = existing_item.parent()
            if target_group_item != current_group_item:
                if current_group_item:
                    taken_item_row = current_group_item.takeRow(existing_item.row())
                    if taken_item_row:
                        target_group_item.appendRow(taken_item_row[0])
                        existing_item = target_group_item.child(target_group_item.rowCount() - 1, 0)
                    else:
                        print(f"Warning: Could not take row for existing buddy {buddy_id} from group {current_group_item.text()}")
                        existing_item = None
                else:
                     print(f"Warning: Existing buddy {buddy_id} has no parent group. Appending to target.")
                     target_group_item.appendRow(existing_item)

            if existing_item:
                 existing_item.setText(display_name)
                 existing_item.setIcon(icon)
                 existing_item.setToolTip(tooltip)

            if status != "Offline":
                self.buddy_tree.setExpanded(target_group_item.index(), True)
            target_group_item.sortChildren(0, Qt.AscendingOrder)

        else:
            item = QStandardItem(display_name)
            item.setEditable(False)
            item.setData(buddy_id, Qt.UserRole)
            item.setIcon(icon)
            item.setToolTip(tooltip)
            target_group_item.appendRow(item)
            target_group_item.sortChildren(0, Qt.AscendingOrder)
            if status != "Offline":
                self.buddy_tree.setExpanded(target_group_item.index(), True)
            old_status = "Offline"

        play_buddy_sounds = self.app_config.get("sounds_enabled", True)
        if play_buddy_sounds:
            if old_status != "Online" and status == "Online":
                print(f"Buddy {display_name} ({buddy_id}) came online.")
                play_sound_async("buddyin.wav")
            elif old_status != "Offline" and status == "Offline":
                print(f"Buddy {display_name} ({buddy_id}) went offline.")
                play_sound_async("buddyout.wav")

    def remove_buddy(self, buddy_id):
        item = self.find_buddy_item(buddy_id)
        if item:
            parent = item.parent()
            if parent:
                parent.removeRow(item.row())
                print(f"Removed buddy: {buddy_id}")
            else:
                print(f"Warning: Cannot remove buddy {buddy_id}, no parent found.")

    @Slot(list)
    def handle_node_list_update(self, nodes_list):
        now = time.time()
        current_mesh_node_ids = set()

        for node_data in nodes_list:
            user_info = node_data.get('user', {})
            node_id = user_info.get('id')
            if not node_id or node_id == self.connection_settings.get("screen_name") or node_id == PUBLIC_CHAT_ID:
                 continue

            current_mesh_node_ids.add(node_id)
            display_name = user_info.get('longName') or user_info.get('shortName') or node_id

            last_heard = node_data.get('lastHeard')
            is_online = False
            if last_heard:
                 try:
                      time_since_heard = now - float(last_heard)
                      if time_since_heard < NODE_OFFLINE_TIMEOUT_SEC:
                           is_online = True
                 except (ValueError, TypeError):
                      print(f"Warning: Could not process lastHeard time for node {node_id}: {last_heard}")

            status = "Online" if is_online else "Offline"
            self.add_or_update_buddy("Meshtastic Nodes", node_id, display_name, status)

        nodes_to_remove = self.displayed_mesh_nodes - current_mesh_node_ids
        for node_id_to_remove in nodes_to_remove:
             item = self.find_buddy_item(node_id_to_remove)
             if item:
                 display_name = item.text()
                 self.add_or_update_buddy(None, node_id_to_remove, display_name, "Offline")
             else:
                 self.remove_buddy(node_id_to_remove)

        self.displayed_mesh_nodes = current_mesh_node_ids

    def handle_double_click(self, index):
        item = self.model.itemFromIndex(index)
        if not item: return

        item_type = item.data(Qt.UserRole + 1)
        buddy_id = item.data(Qt.UserRole)
        display_name = item.text()

        if item_type == "group_public":
            print(f"Double-clicked Public Chat group.")
            self.open_chat_window(PUBLIC_CHAT_ID, "Public Chat")
        elif item_type != "group" and buddy_id:
            print(f"Double-clicked buddy: {display_name} ({buddy_id})")
            self.open_chat_window(buddy_id, display_name)

    def get_selected_buddy(self):
        indexes = self.buddy_tree.selectedIndexes()
        if not indexes: return None, None, None
        item = self.model.itemFromIndex(indexes[0])
        if item:
            item_type = item.data(Qt.UserRole + 1)
            buddy_id = item.data(Qt.UserRole)
            display_name = item.text()
            if item_type == "group_public":
                return PUBLIC_CHAT_ID, "Public Chat", "public"
            elif item_type != "group" and buddy_id:
                return buddy_id, display_name, "buddy"
        return None, None, None

    def send_im_button_clicked(self):
        buddy_id, display_name, item_type = self.get_selected_buddy()
        if item_type == "buddy":
            print(f"IM button clicked for: {display_name} ({buddy_id})")
            self.open_chat_window(buddy_id, display_name)
        elif item_type == "public":
            print(f"IM button clicked for: Public Chat")
            self.open_chat_window(PUBLIC_CHAT_ID, "Public Chat")
        else:
            QMessageBox.information(self, "Send IM", "Please select a buddy or Public Chat first.")

    def open_chat_window(self, buddy_id, display_name=None):
        if not buddy_id: return
        display_name = display_name or buddy_id

        window_key = buddy_id

        if window_key in self.chat_windows and self.chat_windows[window_key].isVisible():
            print(f"Activating existing chat window for {display_name} ({window_key})")
            chat_win = self.chat_windows[window_key]
            chat_win.activateWindow(); chat_win.raise_(); chat_win.setFocus()
            return

        print(f"Opening new chat window for {display_name} ({window_key})")
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
                print(f"[BuddyList] AutoSave=True, LogDir={logs_base_dir}")
            except OSError as e:
                print(f"Warning: Could not create log directories {base_config_dir / LOGS_SUBDIR}: {e}")
                auto_save = False

        try:
            from chat_window import ChatWindow
            chat_win = ChatWindow(
                my_screen_name=self.screen_name,
                buddy_id=buddy_id,
                display_name=display_name,
                auto_save_enabled=auto_save,
                logs_base_dir=logs_base_dir
            )
            title = "Public Chat" if buddy_id == PUBLIC_CHAT_ID else f"IM with {display_name}"
            chat_win.setWindowTitle(title)

            self.chat_windows[window_key] = chat_win
            chat_win.closing.connect(lambda bid=window_key: self.handle_chat_window_close(bid))
            chat_win.message_sent.connect(self.handle_send_request_from_chat)

            chat_win.show()
            chat_win.activateWindow()
            chat_win.raise_()
        except ImportError:
            print("Error: Could not import ChatWindow."); QMessageBox.critical(self, "Error", "Chat window component failed.")
        except Exception as e:
            print(f"Error creating chat window for {buddy_id}: {e}"); traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Could not open chat window:\n{e}")


    def handle_incoming_message(self, sender_id, text, source='mqtt', msg_type='direct', display_name=None):
        print(f"Incoming message from {sender_id} (Source: {source}, Type: {msg_type})")

        is_public_chat_msg = (source == 'meshtastic' and msg_type == 'broadcast')
        target_window_id = PUBLIC_CHAT_ID if is_public_chat_msg else sender_id
        target_display_name = display_name if display_name else sender_id

        if self.app_config.get("sounds_enabled", True):
            play_sound_async("receive.wav")

        win_exists = target_window_id in self.chat_windows and self.chat_windows[target_window_id].isVisible()

        if not win_exists:
            print(f"Opening chat window for incoming message to {target_display_name} ({target_window_id})")
            self.open_chat_window(target_window_id, target_display_name)
            QTimer.singleShot(150, lambda s=target_window_id, t=text, dn=target_display_name: self.route_message_to_window(s, t, dn))
        else:
            self.route_message_to_window(target_window_id, text, target_display_name)

        if not self.isActiveWindow():
            QApplication.alert(self)
        if target_window_id in self.chat_windows and not self.chat_windows[target_window_id].isActiveWindow():
             QApplication.alert(self.chat_windows[target_window_id])

    def route_message_to_window(self, target_window_id, text, sender_display_name):
        win = self.chat_windows.get(target_window_id)
        if win and win.isVisible():
            try:
                win.receive_message(text, sender_display_name=sender_display_name)
                if not win.isActiveWindow():
                    win.raise_()
                    win.activateWindow()
            except Exception as e:
                print(f"Error routing message to chat window for {target_window_id}: {e}")
                traceback.print_exc()
        else:
            print(f"[BuddyList Warning] Chat window for {target_window_id} not found/visible when routing message.")


    @Slot(str)
    def handle_chat_window_close(self, buddy_id):
        closed_window = self.chat_windows.pop(buddy_id, None)
        if closed_window:
            print(f"Chat window for {buddy_id} closed.")


    @Slot(str, str)
    def handle_send_request_from_chat(self, recipient_id, message_text):
        network_type = 'meshtastic' if recipient_id == PUBLIC_CHAT_ID or recipient_id.startswith('!') else 'mqtt'
        print(f"Forwarding send request: To={recipient_id}, Type={network_type}")
        self.send_message_requested.emit(recipient_id, message_text, network_type)

    @Slot()
    def _request_settings(self):
        print("[BuddyList] _request_settings called.")
        settings_win = SettingsWindow(self.app_config, parent=self)
        settings_win.settings_saved.connect(self._handle_settings_saved_locally)
        settings_win.exec()

    @Slot(dict)
    def _handle_settings_saved_locally(self, new_settings):
        print("[BuddyList] Received settings saved signal from dialog:", new_settings)
        self.app_config.update(new_settings)
        self.config_updated.emit(self.app_config)
        set_sounds_enabled(self.app_config.get("sounds_enabled", True))

    def update_my_status(self):
        status = self.status_combo.currentText()
        print(f"My status changed to: {status}")

    def open_list_setup(self):
        QMessageBox.information(self, "Not Implemented", "Setup Groups not implemented.")

    def add_buddy_placeholder(self):
        text, ok = QInputDialog.getText(self, 'Add Buddy', 'Enter buddy ID (!hexid or mqtt_topic):')
        if ok and text:
            text = text.strip()
            if not text: return
            group = "Meshtastic Nodes" if text.startswith('!') else "Buddies"
            print(f"Manually adding buddy: {text} to group {group}")
            self.add_or_update_buddy(group, text, text, "Offline")

    @Slot(str)
    def show_update_notification(self, message_text):
        print(f"[BuddyList UI] Displaying update notification: {message_text}")
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
        print("[BuddyList] Sign off requested by user.")
        self._is_closing = True
        self.sign_off_requested.emit()

    def request_quit(self):
        print("[BuddyList] Quit requested by user (likely via tray).")
        self._is_closing = True
        self.quit_requested.emit()

    def closeEvent(self, event):
        if self._is_closing:
            event.accept()
            return

        print("[BuddyList] closeEvent triggered (user clicked 'X'). Hiding to tray.")
        event.ignore()
        self.hide()
        if self.tray_icon:
            self.tray_icon.show()
            self.tray_icon.showMessage("MIM", "Running in the background.", self.app_icon, 1500)
