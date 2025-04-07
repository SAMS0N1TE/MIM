# buddy_list_window.py
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

# --- Helper function ---
def get_resource_path(relative_path):

    try:

        base_path = sys._MEIPASS
    except AttributeError:

        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Timeout in seconds to consider a node 'Offline' based on lastHeard
NODE_OFFLINE_TIMEOUT_SEC = 30 * 60 # 30 minutes

# **** Define where logs should go relative to config ****
LOGS_SUBDIR = "chat_logs"

class BuddyListWindow(QMainWindow):

    sign_off_requested = Signal()
    send_message_requested = Signal(str, str, str)
    config_updated = Signal(dict)

    def __init__(self, screen_name, connection_settings, app_config=None):
        super().__init__()
        self.screen_name = screen_name
        self.connection_settings = connection_settings
        self.app_config = app_config if app_config else {}
        self.chat_windows = {}
        self.displayed_mesh_nodes = set()

        # --- Define Icons ---
        icon_path_base = get_resource_path("resources/icons/")
        self.online_icon = QIcon(os.path.join(icon_path_base, "buddy_online.png"))
        self.offline_icon = QIcon(os.path.join(icon_path_base, "buddy_offline.png"))
        self.away_icon = QIcon(os.path.join(icon_path_base, "buddy_away.png"))

        self.setWindowTitle(f"{self.screen_name} - Buddy List")
        self.setMinimumSize(200, 450)

        # --- Central Widget and Layout ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # --- Logo Area ---
        LOGO_AREA_BG_COLOR = "#033b72"
        LOGO_SIZE = QSize(90, 90)
        LOGO_AREA_MARGINS = (10, 10, 10, 10)
        LOGO_ICON_PATH = "resources/icons/mim_logo.png"

        logo_frame = QFrame()
        logo_frame.setStyleSheet(f"background-color: {LOGO_AREA_BG_COLOR}; border: none;")

        # Use QVBoxLayout
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
            logo_label.setText("Logo")

        logo_layout.addWidget(logo_label)

        main_layout.addWidget(logo_frame, 0) 

        # --- Status Selector ---
        status_layout = QHBoxLayout()
        status_label = QLabel("Status:")
        self.status_combo = QComboBox(); self.status_combo.addItems(["Online", "Away", "Invisible", "Offline"])
        self.status_combo.setCurrentText("Online"); self.status_combo.currentIndexChanged.connect(self.update_my_status)
        status_layout.addWidget(status_label); status_layout.addWidget(self.status_combo, 1)
        main_layout.addLayout(status_layout)

        # --- Menu Bar ---
        self._create_menu_bar()

        # --- Buddy List Tree View ---
        self.buddy_tree = QTreeView()
        self.buddy_tree.setHeaderHidden(True); self.buddy_tree.setEditTriggers(QTreeView.NoEditTriggers); self.buddy_tree.setAlternatingRowColors(False)
        self.model = QStandardItemModel(); self.buddy_tree.setModel(self.model)
        self._populate_initial_groups()
        main_layout.addWidget(self.buddy_tree, 1)

        # --- Bottom Buttons ---
        button_layout = QHBoxLayout()
        self.im_button = QPushButton("IM"); self.chat_button = QPushButton("Chat"); self.setup_button = QPushButton("Setup")
        button_layout.addStretch(1); button_layout.addWidget(self.im_button); button_layout.addWidget(self.chat_button); button_layout.addWidget(self.setup_button); button_layout.addStretch(1)
        main_layout.addLayout(button_layout)

        # --- Status Bar ---
        self.statusBar().showMessage("Initializing...")

        # --- Connections ---
        self.buddy_tree.doubleClicked.connect(self.handle_double_click)
        self.im_button.clicked.connect(self.send_im_button_clicked)
        self.setup_button.clicked.connect(self._request_settings)

        QTimer.singleShot(150, lambda: self.statusBar().showMessage("Ready"))

    def _create_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        settings_action = QAction("&Settings...", self)
        settings_action.triggered.connect(self._request_settings)
        file_menu.addAction(settings_action); file_menu.addSeparator()
        sign_off_action = QAction("&Sign Off", self); sign_off_action.triggered.connect(self.request_sign_off); file_menu.addAction(sign_off_action); file_menu.addSeparator()
        exit_action = QAction("E&xit", self); exit_action.setShortcut(QKeySequence.Quit); exit_action.triggered.connect(self.close); file_menu.addAction(exit_action)
        people_menu = menu_bar.addMenu("&People"); im_action = QAction("&Send Instant Message...", self); im_action.triggered.connect(self.send_im_button_clicked); add_buddy_action = QAction("&Add Buddy...", self); add_buddy_action.triggered.connect(self.add_buddy_placeholder); setup_groups_action = QAction("Setup &Groups...", self); setup_groups_action.triggered.connect(self.open_list_setup); people_menu.addAction(im_action); people_menu.addSeparator(); people_menu.addAction(add_buddy_action); people_menu.addAction(setup_groups_action)
        help_menu = menu_bar.addMenu("&Help"); about_action = QAction("&About Meshtastic Instant Messenger...", self); help_menu.addAction(about_action) # TODO: Connect about

    def _populate_initial_groups(self):
        self.groups = {}; root_node = self.model.invisibleRootItem()
        group_names = ["Buddies", "Family", "Co-Workers", "Meshtastic Nodes", "Sensors", "Other Nodes", "Offline"]
        for name in group_names:
            group_item = QStandardItem(name); group_font = QFont(); group_font.setBold(True); group_item.setFont(group_font)
            group_item.setEditable(False); group_item.setData("group", Qt.UserRole + 1); root_node.appendRow(group_item)
            self.groups[name.lower()] = group_item; is_expanded = name.lower() in ["buddies", "meshtastic nodes", "offline"]
            self.buddy_tree.setExpanded(group_item.index(), is_expanded)

    def find_buddy_item(self, buddy_id):
        root = self.model.invisibleRootItem();
        for i in range(root.rowCount()):
            group_item = root.child(i, 0)
            if group_item:
                for j in range(group_item.rowCount()):
                    buddy_item = group_item.child(j, 0)
                    if buddy_item and buddy_item.data(Qt.UserRole) == buddy_id: return buddy_item
        return None
    def find_group_item(self, group_name): return self.groups.get(group_name.lower()) if group_name else self.groups.get("offline")
    
    @Slot(str, str, str, str)
    def add_or_update_buddy(self, group_name, buddy_id, display_name, status):
        existing_item = self.find_buddy_item(buddy_id)
        old_status = None
        # Determine old status if the item exists
        if existing_item:
            current_group_item = existing_item.parent()
            if current_group_item:
                 is_offline_group = current_group_item.text().lower() == "offline" # Check if currently in Offline group
                 # Rough check based on icon or group - refine if needed
                 if existing_item.icon() == self.offline_icon or is_offline_group:
                     old_status = "Offline"
                 elif existing_item.icon() == self.away_icon:
                     old_status = "Away"
                 else: # Assume Online if not offline/away
                     old_status = "Online"


        target_group_name = "Offline" if status=="Offline" else (group_name or "Buddies")
        target_group_item = self.find_group_item(target_group_name)
        icon = self.offline_icon
        icon = self.online_icon if status=="Online" else (self.away_icon if status=="Away" else icon)
        tooltip = f"ID:{buddy_id}\nStatus:{status}"

        if existing_item:
            current_group_item=existing_item.parent()
            if target_group_item!=current_group_item:
                taken=current_group_item.takeRow(existing_item.row()) if current_group_item else None
                target_group_item.appendRow(taken[0]) if taken else target_group_item.appendRow(existing_item)
                # Update existing_item reference if row was moved
                existing_item = target_group_item.child(target_group_item.rowCount() - 1, 0) if taken else existing_item

            existing_item.setText(display_name)
            existing_item.setIcon(icon)
            existing_item.setToolTip(tooltip)
            # Expand target group if buddy becomes online/away
            if status != "Offline":
                self.buddy_tree.setExpanded(target_group_item.index(), True)
                target_group_item.sortChildren(0, Qt.AscendingOrder) # Sort within the new group
        else:
            # This is a new buddy being added
            item = QStandardItem(display_name); item.setEditable(False); item.setData(buddy_id,Qt.UserRole); item.setIcon(icon); item.setToolTip(tooltip)
            target_group_item.appendRow(item)
            target_group_item.sortChildren(0,Qt.AscendingOrder)
            if status!="Offline":
                self.buddy_tree.setExpanded(target_group_item.index(),True)
            old_status = "Offline" # Treat newly added non-offline buddies as coming online


        # --- Play Sounds based on status change ---
        if old_status != "Online" and status == "Online":
             print(f"Buddy {display_name} came online.")
             play_sound_async("buddyin.wav") # Buddy sign on sound
        elif old_status != "Offline" and status == "Offline":
             print(f"Buddy {display_name} went offline.")
             play_sound_async("buddyout.wav") # Buddy sign off sound

    def remove_buddy(self, bid): item=self.find_buddy_item(bid); parent=item.parent() if item else None; parent.removeRow(item.row()) if parent else None

    @Slot(list)
    def handle_node_list_update(self, nodes_list):
        now=time.time(); cset=set(); [ (user:=n.get('user',{}), id:=user.get('id'), cset.add(id) if id else None, dname:=(ln:=user.get('longName')) or (sn:=user.get('shortName')) or id if id else None, last:=n.get('lastHeard'), stat:="Online" if last and now-last<NODE_OFFLINE_TIMEOUT_SEC else "Offline", self.add_or_update_buddy("Meshtastic Nodes",id,dname,stat) ) for n in nodes_list ]; [ self.remove_buddy(nid) for nid in (self.displayed_mesh_nodes-cset) ]; self.displayed_mesh_nodes=cset

    def handle_double_click(self, index): item=self.model.itemFromIndex(index); buddy_id=item.data(Qt.UserRole); display_name=item.text(); self.open_chat_window(buddy_id, display_name) if item and item.data(Qt.UserRole+1)!="group" and buddy_id else None
    def get_selected_buddy(self): indexes=self.buddy_tree.selectedIndexes(); index=indexes[0] if indexes else None; item=self.model.itemFromIndex(index) if index else None; buddy_id=item.data(Qt.UserRole) if item and item.data(Qt.UserRole) is not None and item.data(Qt.UserRole+1)!="group" else None; return (buddy_id, item.text()) if buddy_id else (None, None)
    def send_im_button_clicked(self): buddy_id, display_name = self.get_selected_buddy(); self.open_chat_window(buddy_id, display_name) if buddy_id else QMessageBox.information(self, "Send IM", "Please select a buddy first.")

    def open_chat_window(self, buddy_id, display_name=None):

        if not display_name: display_name = buddy_id

        if buddy_id in self.chat_windows and self.chat_windows[buddy_id].isVisible():
             self.chat_windows[buddy_id].activateWindow(); self.chat_windows[buddy_id].raise_()
        else:
             auto_save = self.app_config.get("auto_save_chats", False)
             logs_base_dir = None
             if auto_save:
                 app_data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
                 if not app_data_dir: app_data_dir = "." 
                 base_config_dir = Path(app_data_dir)

                 try: base_config_dir.mkdir(parents=True, exist_ok=True)
                 except OSError as e: print(f"Warning: Could not create base dir {base_config_dir}: {e}")

                 logs_path = base_config_dir / LOGS_SUBDIR
                 logs_base_dir = str(logs_path)
                 print(f"[BuddyList] Opening chat for {buddy_id}, AutoSave={auto_save}, LogDir={logs_base_dir}")
             else:
                 print(f"[BuddyList] Opening chat for {buddy_id}, AutoSave=False")


             from chat_window import ChatWindow
             chat_win = ChatWindow(
                 my_screen_name=self.screen_name,
                 buddy_id=buddy_id,
                 auto_save_enabled=auto_save,
                 logs_base_dir=logs_base_dir
             )
             chat_win.setWindowTitle(f"IM with {display_name}"); self.chat_windows[buddy_id] = chat_win
             chat_win.closing.connect(self.handle_chat_window_close); chat_win.message_sent.connect(self.handle_send_request_from_chat); chat_win.show()

    @Slot(str, str, str, str)
    def handle_incoming_message(self, sid, txt, src='mqtt', mtype='direct'):
        play_sound_async("receive.wav")
        item=self.find_buddy_item(sid)
        dname=item.text() if item else sid
        win_exists=sid in self.chat_windows and self.chat_windows[sid].isVisible()
        if not win_exists:
            self.open_chat_window(sid, dname)

        QTimer.singleShot(50, lambda s=sid, t=txt, dn=dname: self.route_message_to_window(s, t, dn))
        if not self.isActiveWindow():
            QApplication.alert(self)


    def route_message_to_window(self, sid, txt, dname):

        win = self.chat_windows.get(sid)
        if win:
            win.receive_message(txt)
            win.activateWindow()
            win.raise_()
        else:
            print(f"[BuddyList Warning] Chat window for {sid} not found when routing message.")

    def handle_chat_window_close(self, bid):
        closed=self.chat_windows.pop(bid, None)
    def handle_chat_window_close(self, bid): closed=self.chat_windows.pop(bid, None)
    @Slot(str, str)
    def handle_send_request_from_chat(self, rid, txt): ntype='meshtastic' if rid.startswith('!') else 'mqtt'; self.send_message_requested.emit(rid, txt, ntype)

    # **** Slot to directly show settings ****
    @Slot()
    def _request_settings(self):

        print("[BuddyList] _request_settings called.")
        settings_win = SettingsWindow(self.app_config, parent=self)

        settings_win.settings_saved.connect(self._handle_settings_saved_locally)
        
        result = settings_win.exec()


        if result == QDialog.Accepted:
            print("[BuddyList] Settings dialog accepted (Saved).")
        else:
            print("[BuddyList] Settings dialog cancelled or closed.")

    # **** Slot to handle saved settings from dialog ****
    @Slot(dict)
    def _handle_settings_saved_locally(self, new_settings):
        print("[BuddyList] Received settings saved signal from dialog:", new_settings)
        self.app_config.update(new_settings)
        self.config_updated.emit(self.app_config)


    def update_my_status(self, index): status = self.status_combo.currentText(); print(f"Status: {status}") # TODO: Send
    def open_list_setup(self): QMessageBox.information(self, "Not Implemented", "Setup Groups NYI.")
    def add_buddy_placeholder(self): text,ok=QInputDialog.getText(self,'Add Buddy','Enter buddy ID'); group="Meshtastic Nodes" if ok and text and text.startswith('!') else ("Buddies" if ok and text else None); self.add_or_update_buddy(group,text,text,"Offline") if group else None
    def request_sign_off(self): print("[BuddyList] Sign off requested."); self.sign_off_requested.emit()
    def closeEvent(self, event): print("[BuddyList] closeEvent."); [win.close() for win in list(self.chat_windows.values())]; self.chat_windows.clear(); QTimer.singleShot(0, self.request_sign_off); event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv);
    test_config={"auto_save_chats":True, "screen_name": "Tester"}
    dummy_connection_settings={}
    buddy_win=BuddyListWindow("Tester", dummy_connection_settings, app_config=test_config)
    buddy_win.show();
    sys.exit(app.exec())