import sys
import os
import time
from sound_utils import play_sound_async
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeView, QMenu, QMenuBar, QStatusBar,
    QSpacerItem, QSizePolicy, QComboBox, QApplication, QMessageBox,
    QInputDialog, QLineEdit, QFrame, QDialog
)
from PySide6.QtGui import (
    QStandardItemModel, QStandardItem, QFont, QIcon, QAction, QPixmap,
    QFontDatabase, QKeySequence
)
from PySide6.QtCore import Qt, Signal, QTimer, Slot, QStandardPaths, QCoreApplication, QSize
from pathlib import Path
from settings_window import SettingsWindow

# --- Helper function to retrieve resource paths ---
def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Timeout in seconds to consider a node 'Offline' based on lastHeard
# Reduced timeout to 10 minutes for faster reflection of inactive nodes
NODE_OFFLINE_TIMEOUT_SEC = 10 * 60  # 10 minutes (600 seconds)

# **** Define where logs should go relative to config ****
LOGS_SUBDIR = "chat_logs"

# --- New Helper Function: Compute Node Status ---
def compute_node_status(node_data):
    """
    Determines and returns the node status based on the 'lastHeard' timestamp.
    If the node was heard within NODE_OFFLINE_TIMEOUT_SEC seconds, returns "Online".
    Otherwise, returns "Offline".
    """
    last_heard = node_data.get('lastHeard')
    if last_heard and (time.time() - last_heard < NODE_OFFLINE_TIMEOUT_SEC):
        return "Online"
    return "Offline"

class BuddyListWindow(QMainWindow):
    """
    Displays the buddy list, manages chat windows, and handles node updates.
    """
    sign_off_requested = Signal()
    send_message_requested = Signal(str, str, str)  # recipient_id, message_text, network_type
    config_updated = Signal(dict)  # Emit updated config for saving

    def __init__(self, screen_name, connection_settings, app_config=None):
        super().__init__()
        self.screen_name = screen_name
        self.connection_settings = connection_settings
        self.app_config = app_config if app_config else {}
        self.chat_windows = {}  # Dictionary to store active chat windows {buddy_id: ChatWindow}
        self.displayed_mesh_nodes = set()  # Set of node IDs currently shown in the list (excluding self)

        # --- Define Icons ---
        icon_path_base = get_resource_path("resources/icons/")  # Assuming icons are in resources/icons
        self.online_icon = QIcon(os.path.join(icon_path_base, "buddy_online.png"))
        self.offline_icon = QIcon(os.path.join(icon_path_base, "buddy_offline.png"))
        self.away_icon = QIcon(os.path.join(icon_path_base, "buddy_away.png"))  # Keep away icon if needed

        self.setWindowTitle(f"{self.screen_name} - Buddy List")
        self.setMinimumSize(200, 450)

        # --- Central Widget and Layout ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)  # Consistent small margins
        main_layout.setSpacing(5)

        # --- Logo Area ---
        LOGO_AREA_BG_COLOR = "#033b72"
        LOGO_SIZE = QSize(90, 90)
        LOGO_AREA_MARGINS = (10, 10, 10, 10)
        LOGO_ICON_PATH = "resources/icons/mim_logo.png"  # Assuming logo path

        logo_frame = QFrame()
        logo_frame.setStyleSheet(f"background-color: {LOGO_AREA_BG_COLOR}; border: none;")
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
            logo_label.setText("Logo")  # Fallback text

        logo_layout.addWidget(logo_label)
        main_layout.addWidget(logo_frame, 0)  # Logo area doesn't stretch

        # --- Status Selector ---
        status_layout = QHBoxLayout()
        status_label = QLabel("Status:")
        self.status_combo = QComboBox()
        self.status_combo.addItems(["Online", "Away", "Invisible", "Offline"])  # Standard statuses
        self.status_combo.setCurrentText("Online")  # Default
        self.status_combo.currentIndexChanged.connect(self.update_my_status)  # Connect signal
        status_layout.addWidget(status_label)
        status_layout.addWidget(self.status_combo, 1)  # Combo box stretches
        main_layout.addLayout(status_layout)

        # --- Menu Bar ---
        self._create_menu_bar()

        # --- Buddy List Tree View ---
        self.buddy_tree = QTreeView()
        self.buddy_tree.setHeaderHidden(True)
        self.buddy_tree.setEditTriggers(QTreeView.NoEditTriggers)  # Read-only list
        self.buddy_tree.setAlternatingRowColors(False)  # Style choice
        self.model = QStandardItemModel()
        self.buddy_tree.setModel(self.model)
        self._populate_initial_groups()  # Create default groups
        main_layout.addWidget(self.buddy_tree, 1)  # Tree view stretches

        # --- Bottom Buttons ---
        button_layout = QHBoxLayout()
        self.im_button = QPushButton("IM")
        self.chat_button = QPushButton("Chat")  # Placeholder/alternative?
        self.setup_button = QPushButton("Setup")
        # Align buttons nicely
        button_layout.addStretch(1)
        button_layout.addWidget(self.im_button)
        button_layout.addWidget(self.chat_button)
        button_layout.addWidget(self.setup_button)
        button_layout.addStretch(1)
        main_layout.addLayout(button_layout)

        # --- Status Bar ---
        self.statusBar().showMessage("Initializing...")

        # --- Connections ---
        self.buddy_tree.doubleClicked.connect(self.handle_double_click)
        self.im_button.clicked.connect(self.send_im_button_clicked)
        self.setup_button.clicked.connect(self._request_settings)  # Connect Setup button

        # Show "Ready" after a brief delay
        QTimer.singleShot(150, lambda: self.statusBar().showMessage("Ready"))

    def _create_menu_bar(self):
        """Creates the main menu bar."""
        menu_bar = self.menuBar()
        # --- File Menu ---
        file_menu = menu_bar.addMenu("&File")
        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._request_settings)  # Connect to show settings
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        sign_off_action = QAction("&Sign Off", self)
        sign_off_action.triggered.connect(self.request_sign_off)  # Connect to sign off signal
        file_menu.addAction(sign_off_action)
        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.Quit)
        exit_action.triggered.connect(self.close)  # Connect to close window
        file_menu.addAction(exit_action)

        # --- People Menu ---
        people_menu = menu_bar.addMenu("&People")
        im_action = QAction("&Send Instant Message...", self)
        im_action.triggered.connect(self.send_im_button_clicked)  # Connect to IM action
        add_buddy_action = QAction("&Add Buddy...", self)
        add_buddy_action.triggered.connect(self.add_buddy_placeholder)  # Placeholder
        setup_groups_action = QAction("Setup &Groups...", self)
        setup_groups_action.triggered.connect(self.open_list_setup)  # Placeholder
        people_menu.addAction(im_action)
        people_menu.addSeparator()
        people_menu.addAction(add_buddy_action)
        people_menu.addAction(setup_groups_action)

        # --- Help Menu ---
        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About Meshtastic Instant Messenger...", self)
        # about_action.triggered.connect(...) # TODO: Implement About dialog
        help_menu.addAction(about_action)

    def _populate_initial_groups(self):
        """Sets up the default groups in the buddy list model."""
        self.groups = {}  # Dictionary to hold group items {group_name_lower: QStandardItem}
        root_node = self.model.invisibleRootItem()
        # Define standard groups
        group_names = ["Buddies", "Family", "Co-Workers", "Meshtastic Nodes", "Sensors", "Other Nodes", "Offline"]
        for name in group_names:
            group_item = QStandardItem(name)
            group_font = QFont()
            group_font.setBold(True)
            group_item.setFont(group_font)
            group_item.setEditable(False)
            group_item.setData("group", Qt.UserRole + 1)  # Mark item as a group
            root_node.appendRow(group_item)
            self.groups[name.lower()] = group_item  # Store reference by lower case name
            # Default expanded state for key groups
            is_expanded = name.lower() in ["buddies", "meshtastic nodes", "offline"]
            self.buddy_tree.setExpanded(group_item.index(), is_expanded)

    def find_buddy_item(self, buddy_id):
        """Finds an existing QStandardItem for a given buddy_id."""
        root = self.model.invisibleRootItem()
        for i in range(root.rowCount()):  # Iterate through groups
            group_item = root.child(i, 0)
            if group_item:
                for j in range(group_item.rowCount()):  # Iterate through buddies in group
                    buddy_item = group_item.child(j, 0)
                    # Check if item exists and its buddy_id matches
                    if buddy_item and buddy_item.data(Qt.UserRole) == buddy_id:
                        return buddy_item
        return None  # Not found

    def find_group_item(self, group_name):
        """Finds the QStandardItem for a given group name (case-insensitive)."""
        # Fallback to 'offline' group if provided group name is invalid or None
        return self.groups.get(group_name.lower()) if group_name else self.groups.get("offline")

    @Slot(str, str, str, str)  # group_name, buddy_id, display_name, status ("Online", "Offline", "Away")
    def add_or_update_buddy(self, group_name, buddy_id, display_name, status):
        """Adds a new buddy or updates an existing buddy's status, group, and display name."""
        existing_item = self.find_buddy_item(buddy_id)
        old_status = None

        # Determine the buddy's previous status if they existed
        if existing_item:
            current_group_item = existing_item.parent()
            if current_group_item:
                is_offline_group = current_group_item.text().lower() == "offline"
                # Infer status based on icon or group (refine if more statuses added)
                if existing_item.icon() == self.offline_icon or is_offline_group:
                    old_status = "Offline"
                elif existing_item.icon() == self.away_icon:
                    old_status = "Away"
                else:  # Assume Online if not offline/away and not in offline group
                    old_status = "Online"

        # Determine target group and icon based on the new status
        target_group_name = "Offline" if status == "Offline" else (group_name or "Buddies")  # Default to Buddies if group unknown
        target_group_item = self.find_group_item(target_group_name)
        if not target_group_item:  # Safety check if group somehow doesn't exist
            print(f"Warning: Target group '{target_group_name}' not found for buddy '{buddy_id}'. Using Offline.")
            target_group_item = self.find_group_item("Offline")

        # Select appropriate icon
        if status == "Online":
            icon = self.online_icon
        elif status == "Away":
            icon = self.away_icon
        else:
            icon = self.offline_icon  # Default to offline

        tooltip = f"ID: {buddy_id}\nStatus: {status}"

        if existing_item:
            # --- Update Existing Buddy ---
            current_group_item = existing_item.parent()
            # Move item if group needs changing (e.g., Offline -> Buddies)
            if target_group_item != current_group_item:
                if current_group_item:
                    # Take item ownership from the old group
                    taken_item_row = current_group_item.takeRow(existing_item.row())
                    if taken_item_row:
                        target_group_item.appendRow(taken_item_row[0])  # Add to new group
                        # existing_item reference is now invalid, get new reference from target group
                        existing_item = target_group_item.child(target_group_item.rowCount() - 1, 0)
                    else:
                        print(f"Warning: Could not take row for existing buddy {buddy_id} from group {current_group_item.text()}")
                        existing_item = None  # Mark as not found to force recreation if take failed
                else:
                    print(f"Warning: Existing buddy {buddy_id} has no parent group.")
                    target_group_item.appendRow(existing_item)

            # Update text, icon, and tooltip (if item reference is still valid)
            if existing_item:
                existing_item.setText(display_name)
                existing_item.setIcon(icon)
                existing_item.setToolTip(tooltip)

            # Expand target group if buddy becomes online/away
            if status != "Offline":
                self.buddy_tree.setExpanded(target_group_item.index(), True)
            # Sort children within the target group alphabetically
            target_group_item.sortChildren(0, Qt.AscendingOrder)

        else:
            # --- Add New Buddy ---
            item = QStandardItem(display_name)
            item.setEditable(False)
            item.setData(buddy_id, Qt.UserRole)  # Store buddy ID in item data
            item.setIcon(icon)
            item.setToolTip(tooltip)
            target_group_item.appendRow(item)
            # Sort after adding
            target_group_item.sortChildren(0, Qt.AscendingOrder)
            # Expand group if adding an online/away buddy
            if status != "Offline":
                self.buddy_tree.setExpanded(target_group_item.index(), True)
            # Treat newly added non-offline buddies as coming online for sound purposes
            old_status = "Offline"

        # --- Play Sounds based on status change ---
        play_buddy_sounds = self.app_config.get("sounds_enabled", True)  # Check global sound setting

        if play_buddy_sounds:
            # Play sound if buddy comes online from offline/non-existent state
            if old_status != "Online" and status == "Online":
                print(f"Buddy {display_name} ({buddy_id}) came online.")
                play_sound_async("buddyin.wav")  # Buddy sign on sound
            # Play sound if buddy goes offline from online/away state
            elif old_status != "Offline" and status == "Offline":
                print(f"Buddy {display_name} ({buddy_id}) went offline.")
                play_sound_async("buddyout.wav")  # Buddy sign off sound

    def remove_buddy(self, buddy_id):
        """Removes a buddy item from the list."""
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
        """Processes the node list received from the Meshtastic handler."""
        now = time.time()
        current_mesh_node_ids = set()

        for node_data in nodes_list:
            user_info = node_data.get('user', {})
            node_id = user_info.get('id')

            if not node_id:
                continue

            if node_id == self.connection_settings.get("screen_name"):
                 # Optionally update self-info elsewhere if needed, but skip buddy list add
                 continue

            current_mesh_node_ids.add(node_id)
            display_name = user_info.get('longName') or user_info.get('shortName') or node_id

            # --- MODIFIED STATUS LOGIC ---
            last_heard_direct = node_data.get('lastHeard') # From main node entry
            last_received_packet_info = node_data.get('lastReceived', {}) # From embedded last packet info
            last_received_time = last_received_packet_info.get('rxTime') # Timestamp when *we* received the last packet update

            # Use last_heard if available, otherwise fallback to rxTime from lastReceived
            # (Ensure both are treated as timestamps)
            effective_last_heard = None
            if last_heard_direct:
                 effective_last_heard = last_heard_direct
            elif last_received_time:
                 effective_last_heard = last_received_time
                 # Optional: Add a small buffer if using rxTime, as it's local reception time?
                 # print(f"Node {node_id}: Using rxTime ({last_received_time}) as fallback for lastHeard.") # Debug

            # Determine status based on the effective time
            is_online = False
            if effective_last_heard:
                 # Ensure it's treated as a number (it should be)
                 try:
                      time_since_heard = now - float(effective_last_heard)
                      if time_since_heard < NODE_OFFLINE_TIMEOUT_SEC:
                           is_online = True
                 except (ValueError, TypeError):
                      print(f"Warning: Could not process effective_last_heard time for node {node_id}: {effective_last_heard}")

            status = "Online" if is_online else "Offline"
            # --- END MODIFIED STATUS LOGIC ---

            # print(f"Updating Node: ID={node_id}, Name={display_name}, LH={last_heard_direct}, LRT={last_received_time}, EffLH={effective_last_heard}, Status={status}") # Debug print

            self.add_or_update_buddy("Meshtastic Nodes", node_id, display_name, status)

        # --- Remove nodes that are no longer in the list ---
        nodes_to_remove = self.displayed_mesh_nodes - current_mesh_node_ids
        for node_id_to_remove in nodes_to_remove:
             # print(f"Removing node no longer present: {node_id_to_remove}") # Debug
             self.remove_buddy(node_id_to_remove)

        self.displayed_mesh_nodes = current_mesh_node_ids

    def handle_double_click(self, index):
        """Handles double-clicking on an item in the buddy list."""
        item = self.model.itemFromIndex(index)
        if item and item.data(Qt.UserRole + 1) != "group":
            buddy_id = item.data(Qt.UserRole)
            display_name = item.text()
            if buddy_id:
                print(f"Double-clicked buddy: {display_name} ({buddy_id})")
                self.open_chat_window(buddy_id, display_name)

    def get_selected_buddy(self):
        """Gets the ID and display name of the currently selected buddy."""
        indexes = self.buddy_tree.selectedIndexes()
        if not indexes:
            return None, None
        index = indexes[0]
        item = self.model.itemFromIndex(index)
        if item and item.data(Qt.UserRole) is not None and item.data(Qt.UserRole + 1) != "group":
            buddy_id = item.data(Qt.UserRole)
            display_name = item.text()
            return buddy_id, display_name
        return None, None

    def send_im_button_clicked(self):
        """Handles clicking the 'IM' button or related menu action."""
        buddy_id, display_name = self.get_selected_buddy()
        if buddy_id:
            print(f"IM button clicked for: {display_name} ({buddy_id})")
            self.open_chat_window(buddy_id, display_name)
        else:
            QMessageBox.information(self, "Send IM", "Please select a buddy from the list first.")

    def open_chat_window(self, buddy_id, display_name=None):
        """Opens a new chat window or activates an existing one."""
        if not buddy_id:
            print("Error: Cannot open chat window without buddy_id.")
            return
        if not display_name:
            display_name = buddy_id
        if buddy_id in self.chat_windows and self.chat_windows[buddy_id].isVisible():
            print(f"Activating existing chat window for {display_name} ({buddy_id})")
            chat_win = self.chat_windows[buddy_id]
            chat_win.activateWindow()
            chat_win.raise_()
            chat_win.setFocus()
        else:
            print(f"Opening new chat window for {display_name} ({buddy_id})")
            auto_save = self.app_config.get("auto_save_chats", False)
            logs_base_dir = None
            if auto_save:
                app_data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
                if not app_data_dir:
                    print("Warning: Could not get AppDataLocation. Using current directory for logs.")
                    app_data_dir = "."
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
                    auto_save_enabled=auto_save,
                    logs_base_dir=logs_base_dir
                )
                chat_win.setWindowTitle(f"IM with {display_name}")
                self.chat_windows[buddy_id] = chat_win
                chat_win.closing.connect(self.handle_chat_window_close)
                chat_win.message_sent.connect(self.handle_send_request_from_chat)
                chat_win.show()
                chat_win.activateWindow()
                chat_win.raise_()
            except ImportError:
                print("Error: Could not import ChatWindow.")
                QMessageBox.critical(self, "Error", "Could not load the chat window component.")
            except Exception as e:
                print(f"Error creating chat window for {buddy_id}: {e}")
                QMessageBox.critical(self, "Error", f"Could not open chat window:\n{e}")

    @Slot(str, str, str, str)  # sender_id, text, source, msg_type
    def handle_incoming_message(self, sender_id, text, source='mqtt', msg_type='direct'):
        """Handles an incoming message from MQTT or Meshtastic."""
        print(f"Incoming message from {sender_id} ({source}, {msg_type})")
        if self.app_config.get("sounds_enabled", True):
            play_sound_async("receive.wav")
        item = self.find_buddy_item(sender_id)
        display_name = item.text() if item else sender_id
        win_exists = sender_id in self.chat_windows and self.chat_windows[sender_id].isVisible()
        if not win_exists:
            print(f"Opening chat window for incoming message from {display_name} ({sender_id})")
            self.open_chat_window(sender_id, display_name)
        QTimer.singleShot(100, lambda s=sender_id, t=text, dn=display_name: self.route_message_to_window(s, t, dn))
        if not self.isActiveWindow():
            QApplication.alert(self)
            if sender_id in self.chat_windows:
                QApplication.alert(self.chat_windows[sender_id])

    def route_message_to_window(self, sender_id, text, display_name):
        """Safely routes the message content to the correct chat window."""
        win = self.chat_windows.get(sender_id)
        if win:
            try:
                win.receive_message(text)
                if not win.isActiveWindow():
                    win.raise_()
            except Exception as e:
                print(f"Error routing message to chat window for {sender_id}: {e}")
        else:
            print(f"[BuddyList Warning] Chat window for {sender_id} not found when routing message.")

    @Slot(str)
    def handle_chat_window_close(self, buddy_id):
        """Removes reference to a closed chat window."""
        closed_window = self.chat_windows.pop(buddy_id, None)
        if closed_window:
            print(f"Chat window for {buddy_id} closed.")

    @Slot(str, str)
    def handle_send_request_from_chat(self, recipient_id, message_text):
        """Forwards send request from chat window to the main controller."""
        network_type = 'meshtastic' if '!' in recipient_id else 'mqtt'
        print(f"Forwarding send request: To={recipient_id}, Type={network_type}")
        self.send_message_requested.emit(recipient_id, message_text, network_type)

    @Slot()
    def _request_settings(self):
        """Opens the Settings dialog."""
        print("[BuddyList] _request_settings called.")
        settings_win = SettingsWindow(self.app_config, parent=self)
        settings_win.settings_saved.connect(self._handle_settings_saved_locally)
        result = settings_win.exec()
        if result == QDialog.Accepted:
            print("[BuddyList] Settings dialog accepted (Save clicked).")
        else:
            print("[BuddyList] Settings dialog cancelled or closed.")

    @Slot(dict)
    def _handle_settings_saved_locally(self, new_settings):
        """Receives saved settings from the dialog and emits for main controller."""
        print("[BuddyList] Received settings saved signal from dialog:", new_settings)
        self.app_config.update(new_settings)
        self.config_updated.emit(self.app_config)

    def update_my_status(self, index):
        """Handles user changing their own status via the combo box."""
        status = self.status_combo.currentText()
        print(f"My status changed to: {status}")
        # TODO: Implement logic to send status update via Meshtastic/MQTT if applicable

    def open_list_setup(self):
        """Placeholder for group setup functionality."""
        QMessageBox.information(self, "Not Implemented", "Setup Groups feature is not yet implemented.")

    def add_buddy_placeholder(self):
        """Placeholder for manually adding a buddy."""
        text, ok = QInputDialog.getText(self, 'Add Buddy', 'Enter buddy ID (e.g., !hexid or mqtt_topic):')
        if ok and text:
            text = text.strip()
            if not text:
                return
            group = "Meshtastic Nodes" if '!' in text else "Buddies"
            print(f"Manually adding buddy: {text} to group {group}")
            self.add_or_update_buddy(group, text, text, "Offline")

    def request_sign_off(self):
        """Emits signal to request sign-off process."""
        print("[BuddyList] Sign off requested by user.")
        self.sign_off_requested.emit()

    def closeEvent(self, event):
        """Handles the window close event."""
        print("[BuddyList] closeEvent triggered.")
        print("Closing open chat windows...")
        for chat_win in list(self.chat_windows.values()):
            try:
                chat_win.close()
            except Exception as e:
                print(f"Error closing chat window: {e}")
        self.chat_windows.clear()
        print("Requesting sign off via QTimer...")
        QTimer.singleShot(0, self.request_sign_off)
        print("Accepting close event.")
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    test_config = {"auto_save_chats": True, "screen_name": "Tester", "sounds_enabled": True}
    dummy_connection_settings = {"screen_name": "Tester"}
    buddy_win = BuddyListWindow("Tester", dummy_connection_settings, app_config=test_config)
    buddy_win.show()

    def add_test_nodes():
        print("Simulating node updates...")
        buddy_win.handle_node_list_update([
            {'user': {'id': '!1234abcd', 'longName': 'Node Alpha', 'shortName': 'Alpha'}, 'lastHeard': time.time() - 10},
            {'user': {'id': '!5678efgh', 'longName': 'Node Bravo', 'shortName': 'Bravo'}, 'lastHeard': time.time() - 700 * 60},
            {'user': {'id': 'mqtt_buddy_1', 'longName': 'MQTT Friend'}, 'lastHeard': time.time()},
        ])
        QTimer.singleShot(2000, lambda: buddy_win.handle_incoming_message("!1234abcd", "Hello from Alpha!", "meshtastic", "direct"))
        QTimer.singleShot(7000, lambda: buddy_win.handle_node_list_update([
            {'user': {'id': '!5678efgh', 'longName': 'Node Bravo', 'shortName': 'Bravo'}, 'lastHeard': time.time() - 700 * 60},
            {'user': {'id': 'mqtt_buddy_1', 'longName': 'MQTT Friend'}, 'lastHeard': time.time()},
        ]))

    QTimer.singleShot(2000, add_test_nodes)
    sys.exit(app.exec())
