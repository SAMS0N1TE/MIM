import json
import os
import ssl
import sys
import traceback
from datetime import time
from pathlib import Path
import uuid
import time

import paho.mqtt.client as mqtt
from PySide6.QtCore import QObject, Slot, QTimer, QStandardPaths, QCoreApplication, Signal
from PySide6.QtGui import QFontDatabase, QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from buddy_list_window import BuddyListWindow, PUBLIC_CHAT_ID
from login_window import LoginWindow
from meshtastic_handler import MeshtasticHandler
from settings_window import SettingsWindow
from sound_utils import play_sound_async, set_sounds_enabled

NODE_UPDATE_INTERVAL_MS = 1 * 60 * 1000
CONFIG_FILE_NAME = "mim_meshtastic_config.json"

UPDATES_MQTT_SERVER = "mim-updates-ns.eastus-1.ts.eventgrid.azure.net"
UPDATES_MQTT_PORT = 8883
UPDATES_MQTT_TOPIC = "mim/public/updates"
UPDATES_CLIENT_CERT_PATH = "certs/client.crt"
UPDATES_CLIENT_KEY_PATH = "certs/client.key"
UPDATES_CLIENT_AUTH_NAME = "mim-client"
MQTT_MAP_JSON_TOPIC = "msh/US/2/json/#"
MQTT_MAP_PROTO_TOPIC = "msh/US/2/map/#"

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def get_config_path():
    app_data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not app_data_dir:
        app_data_dir = "."
    app_name_folder = QCoreApplication.applicationName() or "MIMMeshtastic"
    config_dir = os.path.join(app_data_dir, app_name_folder)
    try:
        Path(config_dir).mkdir(parents=True, exist_ok=True)
    except OSError:
        config_dir = "."
    return os.path.join(config_dir, CONFIG_FILE_NAME)

def save_config(config_data):
    path = get_config_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
        return True
    except IOError:
        return False
    except Exception:
         return False

def load_config():
    path = get_config_path()
    default_config = {
        "screen_name": "", "mesh_conn_type": "None", "mesh_details": "",
        "meshtastic_channel_index": 0,
        "mqtt_group_topics": [],
        "buddy_group_assignments": {},
        "custom_buddy_groups": [],
        "server": "", "port": 1883,
        "username": "", "password": "", "auto_save_chats": False,
        "sounds_enabled": True, "enable_update_notifications": True,
        "message_notifications_enabled": True,
        "auto_login": False
    }
    if not os.path.exists(path):
        return default_config
    config_data = default_config.copy()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            loaded_json = json.load(f)
            if isinstance(loaded_json, dict):
                for key in ['auto_save_chats', 'sounds_enabled', 'enable_update_notifications', 'auto_login']:
                    if key in loaded_json and not isinstance(loaded_json[key], bool):
                        loaded_json[key] = str(loaded_json[key]).lower() in ['true', '1', 'yes']
                config_data["mqtt_group_topics"] = loaded_json.get("mqtt_group_topics", [])
                config_data["buddy_group_assignments"] = loaded_json.get("buddy_group_assignments", {})
                config_data["custom_buddy_groups"] = loaded_json.get("custom_buddy_groups", [])
                config_data.update(loaded_json)
            else:
                config_data = default_config.copy()
    except (IOError, json.JSONDecodeError):
        config_data = default_config.copy()
    except Exception:
        config_data = default_config.copy()

    for key, default_value in default_config.items():
        if key not in config_data:
            config_data[key] = default_value
    return config_data


class ApplicationController(QObject):
    mqtt_connection_updated = Signal(bool, str)
    mqtt_message_received_signal = Signal(str, str, str, str, str)
    update_notification_received = Signal(str)
    mqtt_map_node_update_received = Signal(dict)

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.login_window = None
        self.buddy_list_window = None
        self.settings_window = None
        self.map_window = None
        self.current_config = load_config()
        self.connection_settings = {}
        self.mqtt_client = None
        self.update_mqtt_client = None
        self.meshtastic_handler = None
        self._signing_off = False
        self._quitting = False
        self._connection_error_shown = False
        self._node_list_initial_request_pending = False
        self._last_channel_list = []
        self._last_nodes_list = []
        self._subscribed_mqtt_groups = set()

        initial_sound_state = self.current_config.get("sounds_enabled", True)
        set_sounds_enabled(initial_sound_state)

        self.node_update_timer = QTimer(self)
        self.node_update_timer.timeout.connect(self._request_periodic_node_update)

        self.mqtt_connection_updated.connect(self._handle_mqtt_connection_update)
        self.mqtt_message_received_signal.connect(self._route_incoming_mqtt_message)
        self.update_notification_received.connect(self._handle_update_notification)

        self.app.aboutToQuit.connect(self.cleanup)
        self.app.setQuitOnLastWindowClosed(False)

        self._connect_update_service()
        self.show_login_window()


    @Slot(str)
    def _handle_update_notification(self, message_text):
        if self.buddy_list_window and self.buddy_list_window.isVisible():
             self.buddy_list_window.show_update_notification(message_text)
        elif self.buddy_list_window and not self.buddy_list_window.isVisible() and self.buddy_list_window.tray_icon and self.buddy_list_window.tray_icon.isVisible():
             self.buddy_list_window.show_update_notification(message_text)
        else:
             pass

    @Slot(bool)
    def set_message_notifications_enabled(self, enabled):
        self._message_notifications_enabled = enabled
        self.app_config["message_notifications_enabled"] = enabled

    def show_login_window(self):
        self._signing_off = False
        self._quitting = False
        self._connection_error_shown = False
        self._disconnect_services()

        if self.buddy_list_window:
            self.app.setQuitOnLastWindowClosed(False)
            self.buddy_list_window.close()
            self.buddy_list_window = None
        if self.settings_window:
            self.settings_window.close()
            self.settings_window = None

        self.current_config = load_config()
        saved_screen_name = self.current_config.get("screen_name")
        saved_auto_login = self.current_config.get("auto_login", False)

        new_login_window = LoginWindow(saved_screen_name, saved_auto_login)
        self.login_window = new_login_window

        if self.login_window:
            self.login_window.setup_requested.connect(self.show_settings_window)
            self.login_window.sign_on_requested.connect(self.handle_sign_on_request)
            self.login_window.destroyed.connect(self._login_window_destroyed)
            self.login_window.show()
            self.app.setQuitOnLastWindowClosed(True)
        else:
            self.app.setQuitOnLastWindowClosed(True)


    @Slot()
    def _login_window_destroyed(self):
        self.login_window = None

    def _on_update_mqtt_log(self, _client, _userdata, _level, _buf):
        pass

    def _disconnect_update_client(self):
        """Disconnects and cleans up the update MQTT client."""
        if self.update_mqtt_client:
            client = self.update_mqtt_client
            self.update_mqtt_client = None
            try:
                print("[ApplicationController] Stopping update MQTT loop...")
                client.loop_stop()
                print("[ApplicationController] Disconnecting update MQTT client...")
                client.disconnect()
                client.on_connect = None
                client.on_disconnect = None
                client.on_message = None
                print("[ApplicationController] Update MQTT client disconnected.")
            except Exception:
                traceback.print_exc()
                print("[ApplicationController Error] Exception during update MQTT client disconnect.")
        else:
            print("[ApplicationController] No update MQTT client to disconnect.")

    def _connect_update_service(self):
        if not self.current_config.get("enable_update_notifications", True):
            return
        if self.update_mqtt_client and getattr(self.update_mqtt_client, 'is_connected', lambda: False)():
            return
        if self.update_mqtt_client:
            self._disconnect_update_client()

        try:
            cert_path = get_resource_path(UPDATES_CLIENT_CERT_PATH)
            key_path = get_resource_path(UPDATES_CLIENT_KEY_PATH)
            if not os.path.exists(cert_path):
                return
            if not os.path.exists(key_path):
                return

            client_id = UPDATES_CLIENT_AUTH_NAME + "_" + os.urandom(4).hex()
            self.update_mqtt_client = mqtt.Client(
                client_id=client_id,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                protocol=mqtt.MQTTv5
            )
            self.update_mqtt_client.on_log = self._on_update_mqtt_log

            self.update_mqtt_client.tls_set(
                certfile=cert_path,
                keyfile=key_path,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT
            )
            self.update_mqtt_client.username_pw_set(username=UPDATES_CLIENT_AUTH_NAME)

            self.update_mqtt_client.on_connect = self._on_update_mqtt_connect
            self.update_mqtt_client.on_disconnect = self._on_update_mqtt_disconnect
            self.update_mqtt_client.on_message = self._on_update_mqtt_message

            self.update_mqtt_client.connect(
                UPDATES_MQTT_SERVER,
                UPDATES_MQTT_PORT,
                keepalive=60
            )
            self.update_mqtt_client.loop_start()

        except FileNotFoundError:
             self.update_mqtt_client = None
        except ssl.SSLError:
             traceback.print_exc(); self.update_mqtt_client = None
        except Exception:
            traceback.print_exc()
            self.update_mqtt_client = None

    def _on_update_mqtt_connect(self, client, _userdata, _flags, rc, properties=None):
        connect_rc = rc.value if isinstance(rc, mqtt.ReasonCode) else rc
        if connect_rc == 0:
            try:
                result, mid = client.subscribe(UPDATES_MQTT_TOPIC, qos=0)
                if result != mqtt.MQTT_ERR_SUCCESS:
                    pass
            except Exception:
                traceback.print_exc()
        else:
            pass

    def _on_update_mqtt_disconnect(self, _client, _userdata, rc, reasonCode=None, properties=None):
        pass


    def _on_update_mqtt_message(self, _client, _userdata, msg):
        try:
            payload_str = msg.payload.decode("utf-8")
            self.update_notification_received.emit(payload_str)
        except UnicodeDecodeError:
             pass
        except Exception:
            traceback.print_exc()

    @Slot(str, str, str, str)
    def handle_mesh_message_received(self, sender_id, display_name, text, msg_type):
        print(f"[Main] Handling mesh message: From={sender_id}, Type={msg_type}, Text='{text[:20]}...'")

        if self._signing_off or self._quitting:
            return

        if not self.buddy_list_window:
            print("[Main] Can't handle message, buddy list window not available")
            return

        if msg_type == 'direct':
            print(f"[Main] Opening direct chat window for {sender_id} ({display_name})")
            self.buddy_list_window.open_chat_window(sender_id, display_name, 'mesh')
            window_key = sender_id
            if window_key in self.buddy_list_window.chat_windows:
                chat_win = self.buddy_list_window.chat_windows[window_key]
                if chat_win:
                    chat_win.receive_message(text, display_name)

                play_sound_async("send.wav")

        elif msg_type == 'broadcast':
            print(f"[Main] Adding message to public chat: {sender_id} ({display_name})")
            if not self.buddy_list_window.is_public_chat_open():
                self.buddy_list_window.open_public_chat()
            public_chat = self.buddy_list_window.get_public_chat_window()
            if public_chat:
                public_chat.receive_message(text, display_name)
                play_sound_async("send.wav")

    def _on_mqtt_connect(self, client, _userdata, _flags, rc, properties=None):
        connect_rc = rc.value if isinstance(rc, mqtt.ReasonCode) else rc
        if connect_rc == 0:
            my_topic = self.connection_settings.get("screen_name")
            topics_to_subscribe = []
            if my_topic:
                pass

            group_topics = self.connection_settings.get("mqtt_group_topics", [])
            print(f"[MQTT Connect] Subscribing to MQTT Group Topics: {group_topics}")
            for topic_str in group_topics:
                if topic_str:
                    topics_to_subscribe.append((topic_str, 1))
                    self._subscribed_mqtt_groups.add(topic_str)

            map_topics_to_subscribe = []
            map_json_topic_pattern = self.current_config.get("mqtt_map_json_topic", MQTT_MAP_JSON_TOPIC)
            map_proto_topic_pattern = self.current_config.get("mqtt_map_proto_topic", MQTT_MAP_PROTO_TOPIC)

            if map_json_topic_pattern:
                print(f"[MQTT Connect] Subscribing to MQTT Map JSON Topic: {map_json_topic_pattern}")
                map_topics_to_subscribe.append((map_json_topic_pattern, 0))  # QoS 0 might be fine for map updates
            if map_proto_topic_pattern:
                print(f"[MQTT Connect] Subscribing to MQTT Map Proto Topic: {map_proto_topic_pattern}")
                map_topics_to_subscribe.append((map_proto_topic_pattern, 0))

            all_topics_to_subscribe = topics_to_subscribe + map_topics_to_subscribe

            if all_topics_to_subscribe:
                try:
                    overall_success = True
                    for mqtt_topic, qos in all_topics_to_subscribe:
                        result, mid = self.mqtt_client.subscribe(mqtt_topic, qos)
                        if result != mqtt.MQTT_ERR_SUCCESS:
                            print(
                                f"[MQTT Connect Error] Subscription failed with code {result} for topic: {mqtt_topic}")
                            overall_success = False

                    if overall_success:
                        print(
                            f"[MQTT Connect] Successfully initiated subscriptions to: {[t[0] for t in all_topics_to_subscribe]}")
                        self.mqtt_connection_updated.emit(True, "Connected and Subscribed")
                    else:
                        self.mqtt_connection_updated.emit(False, "Subscription failed for one or more topics")

                except Exception as e:
                    print(f"[MQTT Connect Error] Exception during subscribe: {e}")
                    traceback.print_exc()
                    self.mqtt_connection_updated.emit(False, "Exception during subscribe.")
            else:
                print("[MQTT Connect Warning] No topics (chat or map) to subscribe to.")
                self.mqtt_connection_updated.emit(True, "Connected (No MQTT topics to subscribe)")
        else:
            try:
                error_string = mqtt.connack_string(rc)
            except ValueError:
                error_string = f"Unknown reason code {rc}"
            self.mqtt_connection_updated.emit(False, f"Connection failed: {error_string}")


    def _on_mqtt_disconnect(self, _client, _userdata, rc, reasonCode=None, properties=None):
        disconnect_rc = rc.value if isinstance(rc, mqtt.ReasonCode) else rc
        if disconnect_rc == 0:
            if not self._signing_off and not self._quitting:
                 self.mqtt_connection_updated.emit(False, "Disconnected")
        else:
            if not self._signing_off and not self._quitting:
                 self.mqtt_connection_updated.emit(False, f"Unexpected disconnection (Code: {rc})")
        self._subscribed_mqtt_groups.clear()


    def _on_mqtt_publish(self, _client, _userdata, mid, reasonCode=None, properties=None):
        pass

    def _on_mqtt_subscribe(self, _client, _userdata, mid, granted_qos, properties=None):
        pass

    @Slot(str, str, str, str, str)
    def _route_incoming_mqtt_message(self, topic, sender_id, text, msg_type, display_name_of_sender):
        print(
            f"[MQTT Route] Routing: topic='{topic}', sender_id='{sender_id}', type='{msg_type}', name='{display_name_of_sender}'")  # DEBUG
        if self.buddy_list_window:
            try:
                chat_id_for_window = topic if msg_type == 'group' else sender_id

                self.buddy_list_window.handle_incoming_message(
                    chat_id_for_window,
                    text,
                    'mqtt',
                    msg_type,
                    display_name_of_sender
                )
            except Exception:
                traceback.print_exc()
        else:
            print("[MQTT Route Warning] Buddy list window not available.")  # DEBUG

    @Slot(bool, str)
    def _handle_mqtt_connection_update(self, connected, message):
        if self._signing_off or self._quitting:
            return

        if self.buddy_list_window:
            status_prefix = "MQTT: " if self.connection_settings.get("server") else ""
            status_message = f"{status_prefix}{message}" if connected else f"{status_prefix}Error: {message}"
            self.buddy_list_window.statusBar().showMessage(status_message, 5000)

        if not connected:
            if not self._connection_error_shown:
                meshtastic_connected = self.meshtastic_handler and self.meshtastic_handler.is_running
                mqtt_was_configured = self.connection_settings.get("server")
                show_critical_error = mqtt_was_configured and (not meshtastic_connected or self.connection_settings.get("mesh_conn_type", "None") == "None")

                if show_critical_error:
                    QMessageBox.warning(self.buddy_list_window or self.login_window or None,
                                       "MQTT Connection Failed",
                                       f"MQTT connection failed or lost:\n{message}\n\nNo other connections active. Signing off.")
                    self._connection_error_shown = True
                    QTimer.singleShot(0, self.handle_sign_off)
        else:
            self._connection_error_shown = False

    @Slot()
    def _start_initial_node_list_request(self):
        if self._signing_off or self._quitting: return
        if not self.meshtastic_handler or not self.meshtastic_handler.is_running:
             return
        self.meshtastic_handler.request_node_list()
        self.meshtastic_handler.request_channel_list()
        if not self.node_update_timer.isActive():
            self.node_update_timer.start(NODE_UPDATE_INTERVAL_MS)

    @Slot()
    def _request_periodic_node_update(self):
        if self._signing_off or self._quitting: return
        if not self.meshtastic_handler or not self.meshtastic_handler.is_running:
            return

        self.meshtastic_handler.reset_active_flags()

        self.meshtastic_handler.request_node_list()
        self.meshtastic_handler.request_channel_list()

    @Slot(list)
    def _handle_channel_list_update(self, channels):
        self._last_channel_list = channels
        if self.settings_window:
            try:
                self.settings_window.update_channel_display(self._last_channel_list)
            except Exception:
                 pass

    @Slot()
    def show_settings_window(self):
        if self.settings_window is None:
            parent = None
            if self.login_window and self.login_window.isVisible(): parent = self.login_window
            elif self.buddy_list_window and self.buddy_list_window.isVisible(): parent = self.buddy_list_window

            if not parent:
                 self.app.setQuitOnLastWindowClosed(False)

            self.settings_window = SettingsWindow(self.current_config, self._last_channel_list, parent=parent)
            self.settings_window.settings_saved.connect(self.handle_settings_saved)
            self.settings_window.finished.connect(self._settings_window_closed)
            self.settings_window.show()
        else:
            if hasattr(self.settings_window, 'update_channel_display'):
                self.settings_window.update_channel_display(self._last_channel_list)
            self.settings_window.activateWindow()
            self.settings_window.raise_()

    @Slot(int)
    def _settings_window_closed(self, result):
        login_vis = self.login_window and self.login_window.isVisible()
        buddy_vis = self.buddy_list_window and self.buddy_list_window.isVisible()

        is_buddy_list_in_tray = self.buddy_list_window and not self.buddy_list_window.isVisible() and self.buddy_list_window.tray_icon and self.buddy_list_window.tray_icon.isVisible()

        if not login_vis and not buddy_vis and not is_buddy_list_in_tray:
             self.app.setQuitOnLastWindowClosed(True)
        else:
             self.app.setQuitOnLastWindowClosed(False)

        self.settings_window = None


    @Slot(dict)
    def handle_settings_saved(self, new_settings):
        old_update_setting = self.current_config.get("enable_update_notifications", True)
        old_sound_setting = self.current_config.get("sounds_enabled", True)
        old_message_notifications_setting = self.current_config.get("message_notifications_enabled", True)

        self.current_config.update(new_settings)

        new_update_setting = self.current_config.get("enable_update_notifications", True)
        new_sound_setting = self.current_config.get("sounds_enabled", True)
        new_message_notifications_setting = self.current_config.get("message_notifications_enabled", True)

        if old_sound_setting != new_sound_setting:
            set_sounds_enabled(new_sound_setting)

        saved_ok = save_config(self.current_config)

        if saved_ok:
            if self.login_window and self.login_window.isVisible():
                new_name = new_settings.get("screen_name", "")
                self.login_window.screen_name_input.setText(new_name)

            parent_widget = self.settings_window if self.settings_window else QApplication.activeWindow()
            QMessageBox.information(parent_widget, "Settings Saved", "Your settings have been saved.")

            if old_update_setting != new_update_setting:
                if new_update_setting:
                    self._connect_update_service()
                else:
                    self._disconnect_update_client()

            if self.buddy_list_window:
                self.buddy_list_window._handle_settings_saved_locally(
                    self.current_config)
                self.buddy_list_window.config_updated.emit(self.current_config)

                if old_message_notifications_setting != new_message_notifications_setting:
                    self.buddy_list_window.set_message_notifications_enabled(new_message_notifications_setting)
        else:
            parent_widget = self.settings_window if self.settings_window else QApplication.activeWindow()
            QMessageBox.warning(parent_widget, "Save Error", "Could not save settings to configuration file.")

    @Slot(dict)
    def handle_config_updated(self, updated_config):
        if updated_config:
            self.current_config.update(updated_config)

            new_sound_state = self.current_config.get("sounds_enabled", True)
            set_sounds_enabled(new_sound_state)

            if save_config(self.current_config):
                pass
            else:
                if self.buddy_list_window and self.buddy_list_window.isVisible():
                    QMessageBox.warning(self.buddy_list_window, "Save Error", "Could not save updated settings.")

    @Slot(str, str, bool)
    def handle_sign_on_request(self, screen_name, password, auto_login):
        self._connection_error_shown = False
        if not screen_name:
             QMessageBox.warning(self.login_window, "Sign On Error", "Screen Name cannot be empty.")
             return

        self.current_config = load_config()
        config_screen_name = self.current_config.get("screen_name")
        if config_screen_name != screen_name:
            QMessageBox.warning(self.login_window,"Sign On Error",f"The Screen Name '{screen_name}' does not match the configured name ('{config_screen_name}').\nPlease use 'Setup' to configure this name first, or enter the correct configured name.")
            return

        config_to_use = self.current_config

        mesh_type = config_to_use.get("mesh_conn_type","None")
        mesh_details = config_to_use.get("mesh_details","")
        if mesh_type != "None" and not mesh_details:
            QMessageBox.warning(self.login_window, "Config Incomplete", f"Meshtastic connection details for '{screen_name}' are missing.\nPlease use 'Setup'.")
            return

        mqtt_server = config_to_use.get("server")
        mqtt_user = config_to_use.get("username")
        mqtt_needs_pass = bool(mqtt_user)
        if mqtt_server and mqtt_needs_pass and not password:
            QMessageBox.warning(self.login_window, "Sign On Error", f"Password required for MQTT user '{mqtt_user}'.")
            return

        should_save_prefs = False
        if self.login_window and self.login_window.get_save_config_preference():
             if self.current_config.get("auto_login") != auto_login:
                 self.current_config["auto_login"] = auto_login
                 should_save_prefs = True

        if should_save_prefs:
            save_config(self.current_config)

        self.connection_settings = config_to_use.copy()
        self.connection_settings['password'] = password

        if self.login_window:
            self.login_window.setWindowTitle("Connecting...")
            QApplication.processEvents()

        self.connect_services()

    def show_buddy_list(self):
        if self.buddy_list_window:
            self.buddy_list_window.activateWindow()
            self.buddy_list_window.raise_()
            return

        if not self.connection_settings:
            self.handle_sign_off()
            return

        screen_name = self.connection_settings.get("screen_name", "Unknown")

        try:
            self.buddy_list_window = BuddyListWindow(
                screen_name=screen_name,
                connection_settings=self.connection_settings,
                app_config=self.current_config
            )
            self.buddy_list_window.config_updated.connect(self.handle_config_updated)
            self.buddy_list_window.sign_off_requested.connect(self.handle_sign_off)
            self.buddy_list_window.quit_requested.connect(self.handle_quit)
            self.buddy_list_window.send_message_requested.connect(self.handle_send_request)
            self.buddy_list_window.map_view_requested.connect(self.show_map_window)
            self.buddy_list_window.settings_requested.connect(self.show_settings_window)
            self.buddy_list_window.destroyed.connect(self._buddy_list_destroyed)
            self.update_notification_received.connect(self.buddy_list_window.show_update_notification)

            self.buddy_list_window.show()

        except Exception:
            traceback.print_exc()
            QMessageBox.critical(None, "UI Error", f"Failed to create buddy list window.")
            self.buddy_list_window = None
            self.handle_sign_off()

    @Slot(bool, str)
    def handle_meshtastic_connection_status(self, connected, message):
        if self._signing_off or self._quitting:
            return

        if self.buddy_list_window:
            status_prefix = "Meshtastic: " if self.connection_settings.get("mesh_conn_type", "None") != "None" else ""
            status_message = f"{status_prefix}{message}" if connected else f"{status_prefix}Error: {message}"
            self.buddy_list_window.statusBar().showMessage(status_message, 5000)

        if connected:
            self._connection_error_shown = False

            if not self.buddy_list_window:
                self.show_buddy_list()
        else:
            if self.node_update_timer.isActive():
                self.node_update_timer.stop()

            if not self._connection_error_shown:
                mqtt_connected = self.mqtt_client and self.mqtt_client.is_connected()
                mesh_was_configured = self.connection_settings.get("mesh_conn_type", "None") != "None"
                show_critical_error = mesh_was_configured and (not mqtt_connected or not self.connection_settings.get("server"))

                if show_critical_error:
                    QMessageBox.warning(self.buddy_list_window or self.login_window or None,
                                        "Meshtastic Connection Failed",
                                        f"Meshtastic connection failed or lost:\n{message}\n\nNo other connections active. Signing off.")
                    self._connection_error_shown = True
                    QTimer.singleShot(0, self.handle_sign_off)
            self._disconnect_mesh_handler()

    def connect_services(self):
        settings = self.connection_settings
        if not settings:
            self.show_login_window()
            return

        self.app.setQuitOnLastWindowClosed(False)

        if not self.buddy_list_window:
            self.show_buddy_list()
            if self.buddy_list_window:
                self.buddy_list_window.statusBar().showMessage("Connecting...", 0)
                QApplication.processEvents()
            else:
                self.handle_sign_off()
                return

        if self.login_window:
            self.login_window.close()

        if settings.get('server'):
            mqtt_server = settings['server']
            mqtt_port = settings.get('port', 1883)
            mqtt_user = settings.get('username')
            mqtt_pass = settings.get('password')

            if self.mqtt_client:
                self._disconnect_mqtt_client()

            try:
                self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                self.mqtt_client.on_connect = self._on_mqtt_connect
                self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
                self.mqtt_client.on_message = self._on_main_mqtt_message
                self.mqtt_client.on_publish = self._on_mqtt_publish
                self.mqtt_client.on_subscribe = self._on_mqtt_subscribe
                if mqtt_user:
                    self.mqtt_client.username_pw_set(mqtt_user, mqtt_pass)

                self.mqtt_client.connect_async(mqtt_server, mqtt_port, 60)
                self.mqtt_client.loop_start()

            except Exception:
                traceback.print_exc()
                if not self._connection_error_shown:
                    parent = self.buddy_list_window or None
                    QMessageBox.critical(parent, "MQTT Setup Error", f"Failed to initialize MQTT client.")
                    self._connection_error_shown = True
                self._disconnect_mqtt_client()
                if settings.get('mesh_conn_type', 'None') == 'None':
                    QTimer.singleShot(0, self.handle_sign_off)
        else:
            self.mqtt_client = None

        mesh_type = settings.get('mesh_conn_type', 'None')
        if mesh_type != 'None':
            if self.meshtastic_handler:
                self._disconnect_mesh_handler()
                QTimer.singleShot(250, lambda: self._create_and_connect_meshtastic(settings))
            else:
                self._create_and_connect_meshtastic(settings)
        else:
            self.meshtastic_handler = None
            if not settings.get('server'):
                if not self._connection_error_shown:
                    parent = self.buddy_list_window or None
                    QMessageBox.critical(parent, "Connection Error",
                                         "No Meshtastic or MQTT connection configured.\nPlease use Setup.")
                    self._connection_error_shown = True
                self.handle_sign_off()
                return

    def _is_map_topic(self, topic_str):
        map_json_base = self.current_config.get("mqtt_map_json_topic", MQTT_MAP_JSON_TOPIC)
        map_proto_base = self.current_config.get("mqtt_map_proto_topic", MQTT_MAP_PROTO_TOPIC)

        if map_json_base.endswith("/#"): map_json_base = map_json_base[:-2]
        elif map_json_base.endswith("/+"): map_json_base = map_json_base[:-2]

        if map_proto_base.endswith("/#"): map_proto_base = map_proto_base[:-2]
        elif map_proto_base.endswith("/+"): map_proto_base = map_proto_base[:-2]

        if map_json_base and topic_str.startswith(map_json_base):
            return "json"
        if map_proto_base and topic_str.startswith(map_proto_base):
            return "proto"
        return None

    def _parse_mqtt_map_json_payload(self, topic, payload_str):
        try:
            data = json.loads(payload_str)
            node_id = None
            lat = None
            lon = None
            position_data = None

            if "fromId" in data:
                node_id = data["fromId"]
            elif "sender" in data:
                node_id = data["sender"]
            elif "from" in data:  # Meshtastic 'from' is usually an int node ID
                try:
                    node_id = f"!{data['from']:x}"
                except (TypeError, ValueError):
                    pass
            elif "nodeId" in data:
                node_id = data.get("nodeId")

            if not node_id:
                parts = topic.split('/')
                if len(parts) > 0 and parts[-1].startswith('!'):
                    node_id = parts[-1]

            if not node_id:
                print(f"[Map MQTT JSON Parse] No usable nodeId found in payload or topic: {topic}")
                return None

            if "payload" in data and isinstance(data["payload"], dict):
                position_data = data["payload"]
            elif "decoded" in data and isinstance(data["decoded"], dict):
                if "position" in data["decoded"] and isinstance(data["decoded"]["position"], dict):
                    position_data = data["decoded"]["position"]
                elif "telemetry" in data["decoded"] and isinstance(data["decoded"]["telemetry"], dict):
                    position_data = data["decoded"]["telemetry"]
            elif isinstance(data, dict):
                position_data = data

            if isinstance(position_data, dict):
                lat = position_data.get("latitude", position_data.get("lat"))
                lon = position_data.get("longitude", position_data.get("lon"))

                lat_i_key_found = None
                if "latitudeI" in position_data:
                    lat_i_key_found = "latitudeI"
                elif "latitude_i" in position_data:
                    lat_i_key_found = "latitude_i"

                lon_i_key_found = None
                if "longitudeI" in position_data:
                    lon_i_key_found = "longitudeI"
                elif "longitude_i" in position_data:
                    lon_i_key_found = "longitude_i"

                if lat is None and lat_i_key_found:
                    lat_i_val = position_data.get(lat_i_key_found)
                    if isinstance(lat_i_val, (int, float)):
                        lat = float(lat_i_val) / 10000000.0
                if lon is None and lon_i_key_found:
                    lon_i_val = position_data.get(lon_i_key_found)
                    if isinstance(lon_i_val, (int, float)):
                        lon = float(lon_i_val) / 10000000.0

            if lat is None or lon is None:
                return None

            timestamp = time.time()  # Default to now
            if isinstance(position_data, dict) and "time" in position_data and isinstance(position_data["time"],
                                                                                          (int, float)):
                timestamp = position_data["time"]
            elif "timestamp" in data and isinstance(data["timestamp"], (int, float)):  # Check top-level data
                timestamp = data["timestamp"]
            elif "time" in data and isinstance(data["time"], (int, float)):  # Check top-level data for 'time'
                timestamp = data["time"]

            user_long_name = node_id
            user_short_name = ""
            if "decoded" in data and isinstance(data["decoded"], dict) and \
                    "user" in data["decoded"] and isinstance(data["decoded"]["user"], dict):
                user_long_name = data["decoded"]["user"].get("longName", node_id)
                user_short_name = data["decoded"]["user"].get("shortName", "")
            elif "user" in data and isinstance(data["user"], dict):
                user_long_name = data["user"].get("longName", data["user"].get("id", node_id))
                user_short_name = data["user"].get("shortName", "")
            elif "longname" in data:  # Some Meshtastic JSON has these at top level
                user_long_name = data.get("longname", node_id)
                user_short_name = data.get("shortname", "")
            elif "name" in data:  # Generic name field
                user_long_name = data.get("name", node_id)

            altitude_val = 0.0
            battery_level_val = None
            snr_val = None

            if isinstance(position_data, dict):
                altitude_val = float(position_data.get("altitude", 0.0))
                if "deviceMetrics" in position_data and isinstance(position_data["deviceMetrics"], dict):
                    battery_level_val = position_data["deviceMetrics"].get("batteryLevel")
                if battery_level_val is None and "batteryLevel" in position_data:
                    battery_level_val = position_data.get("batteryLevel")
                if snr_val is None and "snr" in position_data:
                    snr_val = position_data.get("snr")

            if battery_level_val is None and "battery_level" in data and isinstance(data["battery_level"], (int,
                                                                                                            float)):  # Common in Meshtastic JSON
                battery_level_val = data.get("battery_level")
            elif battery_level_val is None and "battery" in data and isinstance(data["battery"], (int, float)):
                battery_level_val = data.get("battery")

            if snr_val is None:
                snr_val = data.get("snr",
                                   data.get("rssi_snr", data.get("rssi")))

            node_map_data = {
                "nodeId": node_id,
                "user": {
                    "id": node_id,
                    "longName": user_long_name,
                    "shortName": user_short_name
                },
                "position": {
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "altitude": altitude_val
                },
                "deviceMetrics": {
                    "batteryLevel": battery_level_val
                },
                "snr": snr_val,
                "lastHeard": int(timestamp),
                "active_report": True,
                "source": "mqtt_json"
            }
            return node_map_data
        except json.JSONDecodeError:
            print(f"[Map MQTT JSON Parse] Failed to decode JSON from topic {topic}: {payload_str[:100]}")
            return None
        except Exception as e:
            print(f"[Map MQTT JSON Parse] Error parsing map JSON for topic {topic}: {e}")
            traceback.print_exc()
            return None

    def _on_main_mqtt_message(self, _client, _userdata, msg):
        topic = msg.topic
        map_topic_type = self._is_map_topic(topic)

        if map_topic_type:
            payload_str_map = None
            try:
                payload_str_map = msg.payload.decode("utf-8")
                node_update_data = None
                if map_topic_type == "json":
                    node_update_data = self._parse_mqtt_map_json_payload(topic, payload_str_map)
                elif map_topic_type == "proto":
                    print(f"[Map MQTT RX] Protobuf parsing for topic '{topic}' not yet fully implemented.")
                    if msg.payload:
                        pass

                if node_update_data:
                    self.mqtt_map_node_update_received.emit(node_update_data)

            except UnicodeDecodeError:
                if map_topic_type == "proto":
                    print(
                        f"[Map MQTT RX] Received binary payload (expected for proto) on topic {topic}. Length: {len(msg.payload)}")
                    if msg.payload:
                        pass
                else:
                    print(f"[Map MQTT RX Error] Failed to decode UTF-8 payload on JSON map topic {topic}.")
            except Exception as e:
                print(f"[Map MQTT RX Error] General error processing map message from topic {topic}: {e}")
                traceback.print_exc()
            return

        try:
            payload_str_chat = msg.payload.decode("utf-8")

            text_content = payload_str_chat
            sender = "Unknown Sender"
            display_name_of_sender = "Unknown Sender"

            is_group_msg = any(
                mqtt.topic_matches_sub(sub_pattern, topic) for sub_pattern in self._subscribed_mqtt_groups)
            msg_type_for_routing = 'group' if is_group_msg else 'direct'

            try:
                message_data = json.loads(payload_str_chat)
                if isinstance(message_data, dict):
                    if "text" in message_data:
                        text_content = message_data["text"]
                    elif "message" in message_data:
                        text_content = message_data["message"]
                    elif "msg" in message_data:
                        text_content = message_data["msg"]

                    if "sender" in message_data:
                        sender = message_data["sender"]
                    elif "from" in message_data:
                        sender = message_data["from"]
                    elif "user" in message_data:
                        sender = message_data["user"]

                    display_name_of_sender = message_data.get("display_name", sender)
                    if not display_name_of_sender or display_name_of_sender == "Unknown Sender":
                        display_name_of_sender = sender

                elif isinstance(message_data, str):
                    text_content = message_data
                    if is_group_msg:
                        sender = topic
                        display_name_of_sender = topic
                    else:
                        sender = topic
                        display_name_of_sender = topic

                if not isinstance(text_content, str):
                    text_content = json.dumps(text_content)

            except json.JSONDecodeError:
                if is_group_msg:
                    sender = topic
                    display_name_of_sender = topic
                else:
                    sender = topic
                    display_name_of_sender = topic

            if not display_name_of_sender or display_name_of_sender == "Unknown Sender":
                if sender != "Unknown Sender":
                    display_name_of_sender = sender
                elif not is_group_msg:
                    display_name_of_sender = topic

            self.mqtt_message_received_signal.emit(topic, sender, text_content, msg_type_for_routing,
                                                   display_name_of_sender)

        except UnicodeDecodeError:
            print(f"[MQTT Chat Message Error] Failed to decode UTF-8 payload on topic {topic}. Likely binary content.")
        except Exception as e:
            print(f"[MQTT Chat Message Error] General error processing chat message from topic {topic}: {e}")
            traceback.print_exc()

    def _create_and_connect_meshtastic(self, settings):
        if self._signing_off or self._quitting:
            return
        if self.meshtastic_handler:
            self._disconnect_mesh_handler()

        try:
            self.meshtastic_handler = MeshtasticHandler(settings)
            self.meshtastic_handler.connection_status.connect(self.handle_meshtastic_connection_status)
            self.meshtastic_handler.message_received.connect(self.route_incoming_message_from_mesh)
            self.meshtastic_handler.node_list_updated.connect(self._handle_node_list_update)
            self.meshtastic_handler.channel_list_updated.connect(self._handle_channel_list_update)
            self.meshtastic_handler._connection_established_signal.connect(self._start_initial_node_list_request)

            connect_initiated = self.meshtastic_handler.connect_to_device()
            if not connect_initiated and self.meshtastic_handler:
                QTimer.singleShot(0, lambda: self.handle_meshtastic_connection_status(False,
                                                                                      "Initial connection setup failed (e.g., invalid port/IP)"))

        except Exception:
             traceback.print_exc()
             if not self._connection_error_shown:
                 parent = self.buddy_list_window or None
                 QMessageBox.critical(parent, "Meshtastic Error", f"Failed Meshtastic handler initialization.")
                 self._connection_error_shown = True
             self._disconnect_mesh_handler()
             if not self.mqtt_client or not self.mqtt_client.is_connected():
                 QTimer.singleShot(0, self.handle_sign_off)


    @Slot()
    def handle_sign_off(self):
        if self._signing_off: return
        self._signing_off = True

        self._disconnect_services()

        self.app.setQuitOnLastWindowClosed(False)

        if self.buddy_list_window:
            if self.buddy_list_window.tray_icon:
                 self.buddy_list_window.tray_icon.hide()
            try:
                self.buddy_list_window.config_updated.disconnect(self.handle_config_updated)
                self.buddy_list_window.sign_off_requested.disconnect(self.handle_sign_off)
                self.buddy_list_window.quit_requested.disconnect(self.handle_quit)
                if hasattr(self.buddy_list_window, 'settings_requested'):
                     self.buddy_list_window.settings_requested.disconnect(self.show_settings_window)
                if hasattr(self.buddy_list_window, 'map_view_requested'):
                    self.buddy_list_window.map_view_requested.disconnect(self.show_map_window)
                if hasattr(self.buddy_list_window, 'show_update_notification'):
                     self.update_notification_received.disconnect(self.buddy_list_window.show_update_notification)
            except (TypeError, RuntimeError):
                 pass
            self.buddy_list_window._is_closing = True
            self.buddy_list_window.close()

        self.connection_settings = {}

        if self.current_config.get("sounds_enabled", True):
            play_sound_async("signoff.wav")

        QTimer.singleShot(0, self.show_login_window)

    @Slot()
    def handle_quit(self):
        if self._quitting: return
        self._quitting = True
        self.cleanup()
        self.app.quit()

    def _disconnect_mesh_handler(self):
        if self.node_update_timer.isActive():
             self.node_update_timer.stop()

        if self.meshtastic_handler:
            handler = self.meshtastic_handler
            self.meshtastic_handler = None
            try: handler._connection_established_signal.disconnect(self._start_initial_node_list_request)
            except (TypeError, RuntimeError): pass
            try: handler.connection_status.disconnect(self.handle_meshtastic_connection_status)
            except (TypeError, RuntimeError): pass
            try: handler.message_received.disconnect(self.route_incoming_message_from_mesh)
            except (TypeError, RuntimeError): pass
            try: handler.node_list_updated.disconnect(self._handle_node_list_update)
            except (TypeError, RuntimeError): pass
            try: handler.channel_list_updated.disconnect(self._handle_channel_list_update)
            except (TypeError, RuntimeError): pass

            try:
                handler.disconnect()
            except Exception:
                pass


    def _disconnect_mqtt_client(self):
        if self.mqtt_client:
            client = self.mqtt_client
            self.mqtt_client = None
            try:
                client.loop_stop()
                client.disconnect()
                client.on_connect = None; client.on_disconnect = None; client.on_message = None
                client.on_publish = None; client.on_subscribe = None
            except Exception:
                traceback.print_exc()
        else:
            pass
        self._subscribed_mqtt_groups.clear()

    def _disconnect_services(self):
        self._disconnect_mesh_handler()
        self._disconnect_mqtt_client()

    @Slot(list)
    def _handle_node_list_update(self, nodes_list_from_meshtastic):
        if self.buddy_list_window:
            self.buddy_list_window.handle_node_list_update(nodes_list_from_meshtastic)

        if self.map_window:
            print(f"[AppController] Relaying {len(nodes_list_from_meshtastic)} Meshtastic nodes to MapWindow.")

            transformed_nodes_for_map = []
            for node_data in nodes_list_from_meshtastic:
                if not isinstance(node_data, dict): continue

                user_info = node_data.get('user', {})
                node_id_str = user_info.get('id')
                if not node_id_str and 'num' in node_data:
                    node_id_str = f"!{node_data['num']:x}"
                if not node_id_str:
                    node_id_str = str(uuid.uuid4())

                pos_info = node_data.get('position', {})
                metrics_info = node_data.get('deviceMetrics', {})

                entry = {
                    "nodeId": node_id_str,
                    "user": {
                        "id": node_id_str,
                        "longName": user_info.get('longName', node_id_str),
                        "shortName": user_info.get('shortName', '')
                    },
                    "lastHeard": int(node_data.get('lastHeard', 0.0)), # Ensure it's a number
                    "snr": node_data.get('snr'),
                    "active_report": node_data.get('active_report', False),
                    "deviceMetrics": metrics_info.copy()
                }

                if pos_info and 'latitude' in pos_info and 'longitude' in pos_info and \
                        pos_info['latitude'] is not None and pos_info['longitude'] is not None:
                    entry["position"] = {
                        "latitude": pos_info.get('latitude'),
                        "longitude": pos_info.get('longitude'),
                        "altitude": pos_info.get('altitude', 0)
                    }

                transformed_nodes_for_map.append(entry)

            if transformed_nodes_for_map:
                self.map_window.update_nodes(transformed_nodes_for_map)

    @Slot()
    def _buddy_list_destroyed(self):
        buddy_win_instance = self.buddy_list_window
        update_slot = getattr(buddy_win_instance, 'show_update_notification', None) if buddy_win_instance else None

        if buddy_win_instance:
            try: buddy_win_instance.config_updated.disconnect(self.handle_config_updated)
            except (TypeError, RuntimeError): pass
            try: buddy_win_instance.sign_off_requested.disconnect(self.handle_sign_off)
            except (TypeError, RuntimeError): pass
            try: buddy_win_instance.quit_requested.disconnect(self.handle_quit)
            except (TypeError, RuntimeError): pass
            if hasattr(buddy_win_instance, 'settings_requested'):
                try: buddy_win_instance.settings_requested.disconnect(self.show_settings_window)
                except (TypeError, RuntimeError): pass
            if hasattr(buddy_win_instance, 'map_view_requested'):
                try: buddy_win_instance.map_view_requested.disconnect(self.show_map_window)
                except (TypeError, RuntimeError): pass
            if update_slot:
                try: self.update_notification_received.disconnect(update_slot)
                except (TypeError, RuntimeError): pass


        self.buddy_list_window = None

        if not self._signing_off and not self._quitting:
             QTimer.singleShot(0, self.handle_sign_off)

    @Slot(str, str, str, str)
    def route_incoming_message_from_mesh(self, sender_id, display_name, text, msg_type):
        print(f"[Main] Received {msg_type} message from {sender_id} ({display_name}): '{text[:30]}...'")
        play_sound_async("receive.wav")  # Uses "receive.wav"

        if msg_type == 'direct':
            print(f"[Main] Opening chat window for direct message from {sender_id}")
            if self.buddy_list_window:
                self.buddy_list_window.open_chat_window(sender_id, display_name, 'meshtastic')
                chat_window = self.buddy_list_window.chat_windows.get(sender_id)
                if chat_window:
                    chat_window.receive_message(text, display_name)
                else:
                    print(f"[Main] ERROR: Could not get chat window for {sender_id} after opening it!")
            else:
                print(f"[Main] ERROR: Cannot open chat window, buddy_list_window is None!")


        elif msg_type == 'broadcast':

            if self.buddy_list_window:

                public_chat_id = "^all"

                self.buddy_list_window.open_chat_window(public_chat_id, "Public Chat", 'meshtastic')

                chat_window = self.buddy_list_window.chat_windows.get(public_chat_id)

                if chat_window:
                    chat_window.receive_message(text, display_name)
            else:
                print(f"[Main] ERROR: Cannot handle broadcast message, buddy_list_window is None!")


    @Slot(str, str, str)
    def handle_send_request(self, recipient_id, message_text, network_type):
        if network_type == 'meshtastic':
            if self.meshtastic_handler and self.meshtastic_handler.is_running:
                channel_index = 0 if recipient_id == PUBLIC_CHAT_ID else self.connection_settings.get("meshtastic_channel_index", 0)
                dest_id = PUBLIC_CHAT_ID if recipient_id == PUBLIC_CHAT_ID else recipient_id

                self.meshtastic_handler.send_message(dest_id, message_text, channel_index)
            else:
                if self.buddy_list_window:
                    self.buddy_list_window.statusBar().showMessage("Error: Meshtastic not connected.", 3000)

        elif network_type == 'mqtt':
            if self.mqtt_client and self.mqtt_client.is_connected():
                try:
                    target_topic = recipient_id
                    my_screen_name = self.connection_settings.get("screen_name", "Unknown")
                    payload = json.dumps({"sender": my_screen_name, "text": message_text}).encode('utf-8')

                    result, mid = self.mqtt_client.publish(
                        topic=target_topic, payload=payload, qos=1, retain=False
                    )
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        if self.buddy_list_window:
                            self.buddy_list_window.statusBar().showMessage(f"Error sending IM (Code: {result})", 3000)
                except Exception:
                    traceback.print_exc()
                    if self.buddy_list_window:
                        self.buddy_list_window.statusBar().showMessage("Error sending IM.", 3000)
            else:
                if self.buddy_list_window:
                    self.buddy_list_window.statusBar().showMessage("Error: MQTT not connected.", 3000)
        else:
            pass

    @Slot()
    def cleanup(self):
        self._quitting = True

        if self.node_update_timer.isActive():
             self.node_update_timer.stop()

        self._disconnect_services()
        self._disconnect_update_client()

        if self.map_window:
            self.map_window.close()

        if self.buddy_list_window:
            if hasattr(self.buddy_list_window, 'chat_windows'):
                for chat_win in list(self.buddy_list_window.chat_windows.values()):
                    try:
                        chat_win.close()
                    except Exception:
                        pass

        if self.buddy_list_window:
            self.buddy_list_window._is_closing = True
            if self.buddy_list_window.tray_icon:
                 self.buddy_list_window.tray_icon.hide()
            self.buddy_list_window.close()
        if self.settings_window:
             self.settings_window.close()
        if self.login_window:
            self.login_window.close()

    @Slot()
    def _map_window_destroyed_slot(self):
        self.map_window = None

    @Slot()
    def show_map_window(self):
        from map_window import MapWindow

        if self.map_window is None:
            map_settings = {
                "map_online_tile_url": self.current_config.get("map_online_tile_url",
                                                               "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
                "map_offline_enabled": self.current_config.get("map_offline_enabled", False),
                "map_offline_directory": self.current_config.get("map_offline_directory", ""),
                "map_default_center_lat": self.current_config.get("map_default_center_lat", 40.0),
                "map_default_center_lon": self.current_config.get("map_default_center_lon", -100.0),
                "map_default_zoom": self.current_config.get("map_default_zoom", 4)
            }
            self.map_window = MapWindow(settings=map_settings)
            self.map_window.destroyed.connect(self._map_window_destroyed_slot)

            self.mqtt_map_node_update_received.connect(self.map_window.handle_mqtt_node_update)

            initial_nodes = []
            if self.meshtastic_handler:
                initial_nodes = self.meshtastic_handler.get_latest_nodes()
            if initial_nodes:
                self.map_window.update_nodes(initial_nodes)
            self.map_window.show()
        else:
            if self.meshtastic_handler:
                self.map_window.update_nodes(self.meshtastic_handler.get_latest_nodes())
            self.map_window.show()
            self.map_window.activateWindow()
            self.map_window.raise_()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName("MIMMeshtastic")
    app.setOrganizationName("MIMDev")
    app.setStyle("Fusion")

    app_icon_path = get_resource_path("resources/icons/mim_logo.png")
    app_icon = QIcon(app_icon_path)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    font_dir = get_resource_path("resources/fonts")
    loaded_font_families = []
    if os.path.isdir(font_dir):
        for filename in os.listdir(font_dir):
            if filename.lower().endswith((".ttf", ".otf")):
                font_path = os.path.join(font_dir, filename)
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    if "Helvetica" in families and "Helvetica" not in loaded_font_families:
                        loaded_font_families.append("Helvetica")

    default_font_family = "Helvetica" if "Helvetica" in loaded_font_families else "Arial"
    default_font_size = 9
    app.setFont(QFont(default_font_family, default_font_size))

    qss_path = get_resource_path("resources/styles/styles.qss")
    try:
        with open(qss_path, "r") as f:
            app.setStyleSheet(f.read())
        print(f"DEBUG: Stylesheet '{qss_path}' loaded successfully.")  # Optional: for confirmation
    except FileNotFoundError:
        print(f"WARNING: Stylesheet file not found at '{qss_path}'. Proceeding without custom styles.")
    except Exception as e:
        import traceback

        print(f"CRITICAL ERROR: Failed to load or apply stylesheet from '{qss_path}'.")
        print("Error details:")
        traceback.print_exc()

    controller = ApplicationController(app)
    exit_code = app.exec()
    sys.exit(exit_code)
