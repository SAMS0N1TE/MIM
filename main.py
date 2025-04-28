# main.py
import sys
import os
import json
import ssl
import paho.mqtt.client as mqtt
import traceback
from pathlib import Path
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QObject, QDir, Qt, Slot, QTimer, QStandardPaths, QCoreApplication, Signal
from PySide6.QtGui import QFontDatabase, QFont, QIcon
from sound_utils import play_sound_async, set_sounds_enabled
from login_window import LoginWindow
from buddy_list_window import BuddyListWindow, PUBLIC_CHAT_ID
from meshtastic_handler import MeshtasticHandler
from settings_window import SettingsWindow

NODE_UPDATE_INTERVAL_MS = 1 * 60 * 1000
CONFIG_FILE_NAME = "mim_meshtastic_config.json"

UPDATES_MQTT_SERVER = "mim-updates-ns.eastus-1.ts.eventgrid.azure.net"
UPDATES_MQTT_PORT = 8883
UPDATES_MQTT_TOPIC = "mim/public/updates"
UPDATES_CLIENT_CERT_PATH = "certs/client.crt"
UPDATES_CLIENT_KEY_PATH = "certs/client.key"
UPDATES_CLIENT_AUTH_NAME = "mim-client"

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def get_config_path():
    app_data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not app_data_dir:
        print("Warning: Could not determine AppDataLocation. Using current directory for config.")
        app_data_dir = "."
    app_name_folder = QCoreApplication.applicationName() or "MIMMeshtastic"
    config_dir = os.path.join(app_data_dir, app_name_folder)
    try:
        Path(config_dir).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Warning: Could not create config directory {config_dir}: {e}. Using fallback.")
        config_dir = "."
    return os.path.join(config_dir, CONFIG_FILE_NAME)

def save_config(config_data):
    path = get_config_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
        print(f"Configuration saved to: {path}")
        return True
    except IOError as e:
        print(f"Error saving configuration to {path}: {e}")
        return False
    except Exception as e:
         print(f"Unexpected error saving configuration: {e}")
         return False

def load_config():
    path = get_config_path()
    default_config = {
        "screen_name": "", "mesh_conn_type": "None", "mesh_details": "",
        "meshtastic_channel_index": 0, "server": "", "port": 1883,
        "username": "", "password": "", "auto_save_chats": False,
        "sounds_enabled": True, "enable_update_notifications": True,
        "auto_login": False
    }
    if not os.path.exists(path):
        print(f"Configuration file not found: {path}. Using defaults.")
        return default_config
    config_data = default_config.copy()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            loaded_json = json.load(f)
            if isinstance(loaded_json, dict):
                # Ensure boolean values are correctly interpreted
                for key in ['auto_save_chats', 'sounds_enabled', 'enable_update_notifications', 'auto_login']:
                    if key in loaded_json and not isinstance(loaded_json[key], bool):
                        print(f"Warning: Config key '{key}' is not boolean, attempting conversion.")
                        loaded_json[key] = str(loaded_json[key]).lower() in ['true', '1', 'yes']
                config_data.update(loaded_json)
                print(f"Configuration loaded and merged from: {path}")
            else:
                print(f"Warning: Config file {path} does not contain a valid JSON object. Using defaults.")
                config_data = default_config.copy()
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading or parsing configuration from {path}: {e}. Using defaults.")
        config_data = default_config.copy()
    except Exception as e:
        print(f"Unexpected error loading configuration: {e}")
        config_data = default_config.copy()

    # Ensure essential keys exist after loading, using defaults if missing
    for key, default_value in default_config.items():
        if key not in config_data:
            print(f"Warning: Config missing key '{key}', adding default value: {default_value}")
            config_data[key] = default_value
    return config_data


class ApplicationController(QObject):
    mqtt_connection_updated = Signal(bool, str)
    mqtt_message_received_signal = Signal(str, str, str) # sender_id, text, msg_type
    update_notification_received = Signal(str) # message_text

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.login_window = None
        self.buddy_list_window = None
        self.settings_window = None
        self.current_config = load_config()
        self.connection_settings = {} # Runtime settings used for connection
        self.mqtt_client = None # For user chat
        self.update_mqtt_client = None # For update notifications
        self.meshtastic_handler = None
        self._signing_off = False # Flag to indicate intentional disconnect
        self._quitting = False # Flag to indicate app quit initiated
        self._connection_error_shown = False
        self._node_list_initial_request_pending = False

        initial_sound_state = self.current_config.get("sounds_enabled", True)
        set_sounds_enabled(initial_sound_state)

        # Timer for periodic Meshtastic node list updates
        self.node_update_timer = QTimer(self)
        self.node_update_timer.timeout.connect(self._request_periodic_node_update)

        # Connect internal signals
        self.mqtt_connection_updated.connect(self._handle_mqtt_connection_update)
        self.mqtt_message_received_signal.connect(self._route_incoming_mqtt_message)
        # self.update_notification_received signal is connected below after buddy list exists

        # Handle application quit properly
        self.app.aboutToQuit.connect(self.cleanup)
        self.app.setQuitOnLastWindowClosed(False) # Keep running even if main window closed (for tray)

        print("[Controller] Initialized.")
        self._connect_update_service() # Attempt to connect update service on startup
        self.show_login_window()

    # --- MQTT Callbacks (Update Service) ---
    def _on_update_mqtt_log(self, client, userdata, level, buf):
        # print(f"[MQTT Update Log] Level={level}: {buf}") # Can be noisy, uncomment if needed
        pass

    def _connect_update_service(self):
        if not self.current_config.get("enable_update_notifications", True):
            print("[Controller Update] Update notifications disabled in config.")
            return
        if self.update_mqtt_client and getattr(self.update_mqtt_client, 'is_connected', lambda: False)():
            print("[Controller Update] Update client already connected.")
            return
        if self.update_mqtt_client:
            self._disconnect_update_client() # Clean up old client if exists

        print(f"[Controller Update] Connecting to update server: {UPDATES_MQTT_SERVER}:{UPDATES_MQTT_PORT} (TLS)")
        try:
            # Verify certificate paths
            cert_path = get_resource_path(UPDATES_CLIENT_CERT_PATH)
            key_path = get_resource_path(UPDATES_CLIENT_KEY_PATH)
            if not os.path.exists(cert_path):
                print(f"[Controller Update] ERROR: Client cert not found at {cert_path}")
                return
            if not os.path.exists(key_path):
                print(f"[Controller Update] ERROR: Client key not found at {key_path}")
                return

            client_id = UPDATES_CLIENT_AUTH_NAME + "_" + os.urandom(4).hex()
            self.update_mqtt_client = mqtt.Client(
                client_id=client_id,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                protocol=mqtt.MQTTv5 # Use MQTTv5 for Azure Event Grid
            )
            print(f"[Controller Update] Using Client ID: {client_id}")
            self.update_mqtt_client.on_log = self._on_update_mqtt_log

            # Configure TLS using client cert/key, requires server validation
            self.update_mqtt_client.tls_set(
                certfile=cert_path,
                keyfile=key_path,
                cert_reqs=ssl.CERT_REQUIRED, # Verify server cert using system CAs
                tls_version=ssl.PROTOCOL_TLS_CLIENT # Use modern TLS protocol
            )
            # Use the client auth name as the MQTT username for Event Grid
            self.update_mqtt_client.username_pw_set(username=UPDATES_CLIENT_AUTH_NAME)

            # Assign callbacks
            self.update_mqtt_client.on_connect = self._on_update_mqtt_connect
            self.update_mqtt_client.on_disconnect = self._on_update_mqtt_disconnect
            self.update_mqtt_client.on_message = self._on_update_mqtt_message

            # Connect and start loop
            print("[Controller Update] Attempting synchronous connect...")
            # Note: connect() might block, consider connect_async if UI hangs
            self.update_mqtt_client.connect(
                UPDATES_MQTT_SERVER,
                UPDATES_MQTT_PORT,
                keepalive=60 # Standard keepalive
                # Properties like SessionExpiryInterval=0 are needed for MQTTv5 compliance with Event Grid
                # but paho-mqtt handles basic properties; ensure library is up-to-date
            )
            print("[Controller Update] Connect successful, starting loop...")
            self.update_mqtt_client.loop_start() # Start background thread for network loop

        except FileNotFoundError as fnf_err:
             print(f"[Controller Update] ERROR: Certificate file not found during setup - {fnf_err}")
             self.update_mqtt_client = None
        except ssl.SSLError as ssl_err:
             print(f"[Controller Update] ERROR: SSL Error during connection - {ssl_err}")
             traceback.print_exc(); self.update_mqtt_client = None
        except Exception as e:
            print(f"[Controller Update] ERROR connecting update client: {e}")
            traceback.print_exc()
            self.update_mqtt_client = None

    def _on_update_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        connect_rc = rc.value if isinstance(rc, mqtt.ReasonCode) else rc
        if connect_rc == 0:
            print(f"[Controller Update CB] Successfully connected to update server. Subscribing to {UPDATES_MQTT_TOPIC}")
            try:
                # Use QoS 0 for update messages (fire and forget)
                result, mid = client.subscribe(UPDATES_MQTT_TOPIC, qos=0)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    print(f"[Controller Update CB] Subscribed to update topic successfully (mid={mid}).")
                else:
                    print(f"[Controller Update CB] ERROR: Failed to subscribe to update topic (Code: {result}).")
            except Exception as e:
                print(f"[Controller Update CB] Exception during update subscribe: {e}")
                traceback.print_exc()
        else:
            try: error_string = mqtt.connack_string(rc)
            except ValueError: error_string = f"Unknown reason code {rc}"
            print(f"[Controller Update CB] ERROR: Failed to connect to update server: {error_string} (Code: {rc})")

    # Added *args to handle potential extra arguments from paho-mqtt
    def _on_update_mqtt_disconnect(self, client, userdata, rc, properties=None, *args):
        disconnect_rc = rc.value if isinstance(rc, mqtt.ReasonCode) else rc
        print(f"[Controller Update CB] Disconnected from update server (Code: {disconnect_rc}). Args received: {args}")
        # Optional: Add reconnect logic here if needed for the update service

    def _on_update_mqtt_message(self, client, userdata, msg):
        print(f"[Controller Update CB] Message received on topic: {msg.topic}")
        try:
            payload_str = msg.payload.decode("utf-8")
            print(f"[Controller Update CB] Update message payload: '{payload_str}'")
            # Emit the signal to be handled in the UI thread
            self.update_notification_received.emit(payload_str)
        except UnicodeDecodeError:
             print(f"[Controller Update CB] Error: Could not decode update payload as UTF-8.")
        except Exception as e:
            print(f"[Controller Update CB] Error processing update message: {e}")
            traceback.print_exc()

    def _disconnect_update_client(self):
        print("[Controller Update] Disconnecting update client...")
        if self.update_mqtt_client:
            client = self.update_mqtt_client
            self.update_mqtt_client = None # Clear reference first
            try:
                client.loop_stop(force=False) # Stop background thread gracefully
                print("[Controller Update] Update client loop stopped.")
                # Disconnect can potentially trigger the _on_update_mqtt_disconnect callback
                client.disconnect() # Send DISCONNECT packet
                print("[Controller Update] Update client disconnect called.")
                # Clear callbacks to prevent issues if object lingers
                client.on_connect = None; client.on_disconnect = None; client.on_message = None; client.on_log = None
                print("[Controller Update] Update client callbacks cleared.")
            except Exception as e:
                print(f"[Controller Update] Error during update client disconnect: {e}")
                traceback.print_exc()
        else:
             print("[Controller Update] No active update client to disconnect.")

    @Slot(str)
    def _handle_update_notification(self, message_text):
        """Handles the update notification signal (UI Thread)."""
        print(f"[Controller UI] Received update notification signal: {message_text}")
        # Display the notification via the buddy list window's method
        if self.buddy_list_window and self.buddy_list_window.isVisible():
             print("[Controller UI] Forwarding update notification to buddy list window.")
             self.buddy_list_window.show_update_notification(message_text)
        elif self.buddy_list_window and not self.buddy_list_window.isVisible() and self.buddy_list_window.tray_icon and self.buddy_list_window.tray_icon.isVisible():
             print("[Controller UI] Showing update notification via tray icon.")
             self.buddy_list_window.show_update_notification(message_text) # Let buddy list handle tray message
        else:
             print("[Controller UI] Buddy list window not available/visible for update notification.")
             # Optionally show a global QMessageBox as fallback, but might be annoying
             # QMessageBox.information(None, "MIM Update", message_text)

    # --- Window Management ---
    def show_login_window(self):
        print("[Controller] show_login_window CALLED.")
        self._signing_off = False # Reset flags
        self._quitting = False
        self._connection_error_shown = False
        self._disconnect_services() # Disconnect chat services before showing login

        # Close other main windows if open
        if self.buddy_list_window:
            print("[Controller] Closing existing buddy list window.")
            self.buddy_list_window.close() # Let its closeEvent handle cleanup
            self.buddy_list_window = None
        if self.settings_window:
             print("[Controller] Closing existing settings window.")
             self.settings_window.close()
             self.settings_window = None

        # Only quit app if login window is closed *manually*
        self.app.setQuitOnLastWindowClosed(True)
        print("[Controller] QuitOnLastWindowClosed set to True for Login Window.")

        self.current_config = load_config() # Reload config in case it changed
        saved_screen_name = self.current_config.get("screen_name")
        saved_auto_login = self.current_config.get("auto_login", False)

        print(f"[Controller] Creating LoginWindow (ScreenName: {saved_screen_name}, AutoLogin: {saved_auto_login})")
        # Instantiate LoginWindow
        new_login_window = LoginWindow(saved_screen_name, saved_auto_login)
        # Assign to self.login_window BEFORE connecting signals
        self.login_window = new_login_window
        print(f"[Controller] LoginWindow instance assigned: {self.login_window}")

        # Connect signals FROM the LoginWindow instance
        self.login_window.setup_requested.connect(self.show_settings_window)
        self.login_window.sign_on_requested.connect(self.handle_sign_on_request)
        # Connect destroyed signal AFTER other signals
        self.login_window.destroyed.connect(self._login_window_destroyed)
        print("[Controller] LoginWindow signals connected.")

        self.login_window.show()
        print("[Controller] LoginWindow shown.")

    @Slot()
    def _login_window_destroyed(self):
        print("[Controller] Login window destroyed.")
        self.login_window = None
        # If no other windows are open (buddy list, settings) and we're not quitting,
        # the app should exit because QuitOnLastWindowClosed was True.

    # --- MQTT Callbacks (User Chat) ---
    def _on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        connect_rc = rc.value if isinstance(rc, mqtt.ReasonCode) else rc
        print(f"[Controller MQTT CB - User] _on_mqtt_connect: Result code={connect_rc}")
        if connect_rc == 0:
            print("[Controller MQTT CB - User] MQTT Connected successfully.")
            # Use the screen name from runtime settings for the topic
            my_topic = self.connection_settings.get("screen_name")
            if my_topic:
                try:
                    print(f"[Controller MQTT CB - User] Subscribing to MQTT topic: {my_topic}")
                    # Use QoS 1 for reliable message delivery for chat
                    result, mid = self.mqtt_client.subscribe(my_topic, qos=1)
                    if result == mqtt.MQTT_ERR_SUCCESS:
                        print(f"[Controller MQTT CB - User] MQTT subscription request successful (mid={mid}).")
                        # Emit signal to update UI (in UI thread)
                        self.mqtt_connection_updated.emit(True, "Connected")
                    else:
                        print(f"[Controller MQTT CB - User] Error: MQTT subscription failed (Code: {result}).")
                        self.mqtt_connection_updated.emit(False, f"Subscription failed (Code: {result})")
                except Exception as e:
                    print(f"[Controller MQTT CB - User] Exception during MQTT subscribe: {e}")
                    traceback.print_exc()
                    self.mqtt_connection_updated.emit(False, f"Exception during subscribe: {e}")
            else:
                print("[Controller MQTT CB - User] Warning: Cannot subscribe, screen_name not found in runtime settings.")
                self.mqtt_connection_updated.emit(False, "Cannot subscribe (no screen name)")
        else:
            try: error_string = mqtt.connack_string(rc)
            except ValueError: error_string = f"Unknown reason code {rc}"
            print(f"[Controller MQTT CB - User] Error: MQTT Connection failed: {error_string} (Code: {rc})")
            self.mqtt_connection_updated.emit(False, f"Connection failed: {error_string}")

    def _on_mqtt_disconnect(self, client, userdata, rc, properties=None): # Adjusted signature for V2
        disconnect_rc = rc.value if isinstance(rc, mqtt.ReasonCode) else rc
        print(f"[Controller MQTT CB - User] _on_mqtt_disconnect: Result code={disconnect_rc}")
        if disconnect_rc == 0:
            print("[Controller MQTT CB - User] MQTT Disconnected cleanly.")
            # Only emit update if not intentionally signing off
            if not self._signing_off and not self._quitting:
                 self.mqtt_connection_updated.emit(False, "Disconnected")
        else:
            print(f"[Controller MQTT CB - User] Error: MQTT Unexpected disconnection (Code: {rc}).")
            if not self._signing_off and not self._quitting:
                 # Emit signal for unexpected disconnect
                 self.mqtt_connection_updated.emit(False, f"Unexpected disconnection (Code: {rc})")
                 # Optional: Add reconnect logic here if desired for user MQTT

    def _on_mqtt_message(self, client, userdata, msg):
        """Callback when an MQTT message is received on a subscribed topic (User Chat)."""
        print(f"[Controller MQTT CB - User] _on_mqtt_message: Topic={msg.topic}")
        try:
            payload_str = msg.payload.decode("utf-8")
            print(f"[Controller MQTT CB - User] MQTT Received Raw: '{payload_str}'")

            # Assuming the topic IS the sender ID for MQTT direct messages
            sender_id = msg.topic
            message_text = payload_str
            msg_type = 'direct' # MQTT messages are treated as direct here

            # Emit signal to pass data to the UI thread
            self.mqtt_message_received_signal.emit(sender_id, message_text, msg_type)

        except UnicodeDecodeError:
             print(f"[Controller MQTT CB - User] Error: Could not decode MQTT payload as UTF-8 on topic {msg.topic}")
        except Exception as e:
            print(f"[Controller MQTT CB - User] Error processing incoming MQTT message: {e}")
            traceback.print_exc()

    def _on_mqtt_publish(self, client, userdata, mid, properties=None): # Adjusted for V2
        print(f"[Controller MQTT CB - User] _on_mqtt_publish: Confirmed publish for mid={mid}")
        # Can be used to confirm message sent if needed

    def _on_mqtt_subscribe(self, client, userdata, mid, granted_qos, properties=None): # Adjusted for V2
        print(f"[Controller MQTT CB - User] _on_mqtt_subscribe: Confirmed subscription mid={mid}, QoS={granted_qos}")
        # Can be used to confirm subscription succeeded

    @Slot(str, str, str)
    def _route_incoming_mqtt_message(self, sender_id, text, msg_type):
        """Routes incoming USER MQTT messages (received via signal) to the buddy list (UI Thread)."""
        print(f"[Controller UI] _route_incoming_mqtt_message: From={sender_id}, Type={msg_type}")
        if self.buddy_list_window:
             try:
                  # Pass all info to buddy list's handler
                  self.buddy_list_window.handle_incoming_message(sender_id, text, 'mqtt', msg_type)
             except Exception as e:
                  print(f"ERROR calling handle_incoming_message for MQTT: {e}"); traceback.print_exc()
        else:
             print("[Controller UI] Warning: Buddy list window not available for incoming MQTT message.")

    @Slot(bool, str)
    def _handle_mqtt_connection_update(self, connected, message):
        """Handles connection status updates for the user MQTT client (UI Thread)."""
        print(f"[Controller UI - User] _handle_mqtt_connection_update: connected={connected}, message='{message}'")
        if self._signing_off or self._quitting:
            print("[Controller UI - User] Ignoring MQTT connection update during sign off/quit.")
            return

        if self.buddy_list_window:
            status_prefix = "MQTT: " if self.connection_settings.get("server") else ""
            status_message = f"{status_prefix}{message}" if connected else f"{status_prefix}Error: {message}"
            self.buddy_list_window.statusBar().showMessage(status_message, 5000)

        if not connected:
            print(f"[Controller UI - User] MQTT connection failed or lost: {message}")
            # Decide whether to show a critical error messagebox
            if not self._connection_error_shown:
                meshtastic_connected = self.meshtastic_handler and self.meshtastic_handler.is_running
                mqtt_was_configured = self.connection_settings.get("server")
                # Show critical error only if MQTT was configured AND Meshtastic is also not connected/configured
                show_critical_error = mqtt_was_configured and (not meshtastic_connected or self.connection_settings.get("mesh_conn_type", "None") == "None")

                if show_critical_error:
                    print("[Controller UI - User] Showing MQTT connection failed/lost critical error message.")
                    QMessageBox.warning(self.buddy_list_window or self.login_window or None,
                                       "MQTT Connection Failed",
                                       f"MQTT connection failed or lost:\n{message}\n\nNo other connections active. Signing off.")
                    self._connection_error_shown = True
                    # If critical, sign off
                    QTimer.singleShot(0, self.handle_sign_off) # Use timer to avoid issues during callback
                # Don't automatically disconnect the client here, it might reconnect
        else:
            # Connection successful
            self._connection_error_shown = False # Reset error flag on successful connection


    # --- Meshtastic Slots ---
    @Slot()
    def _start_initial_node_list_request(self):
        """Requests the initial node list after Meshtastic connection."""
        if self._signing_off or self._quitting: return # Don't request if disconnecting
        if not self.meshtastic_handler or not self.meshtastic_handler.is_running:
             print("[Controller] Skipping initial node list request (handler not ready).")
             return
        print(f"[Controller] Requesting *initial* node list...")
        self.meshtastic_handler.request_node_list()
        # Start the periodic timer only after the first successful connection
        print(f"[Controller] Starting periodic node update timer ({NODE_UPDATE_INTERVAL_MS} ms interval).")
        if not self.node_update_timer.isActive():
            self.node_update_timer.start(NODE_UPDATE_INTERVAL_MS)

    @Slot()
    def _request_periodic_node_update(self):
        """Requests node list update periodically."""
        if self._signing_off or self._quitting: return
        if self.meshtastic_handler and self.meshtastic_handler.is_running:
            # print("[Controller] Requesting periodic node update...") # Can be noisy
            self.meshtastic_handler.request_node_list()
        else:
            # Stop timer if handler is gone or not running
            if self.node_update_timer.isActive():
                print("[Controller] Stopping periodic node update timer (handler not running).")
                self.node_update_timer.stop()


    # --- Settings Handling ---
    @Slot()
    def show_settings_window(self):
        print("[Controller] show_settings_window CALLED.")
        if self.settings_window is None:
            print("[Controller] Creating new SettingsWindow.")
            # Determine parent window (login or buddy list)
            parent = None
            if self.login_window and self.login_window.isVisible(): parent = self.login_window
            elif self.buddy_list_window and self.buddy_list_window.isVisible(): parent = self.buddy_list_window
            print(f"[Controller] Setting parent for SettingsWindow: {parent}")

            # If no parent, ensure app doesn't quit if settings window is closed
            if not parent:
                 print("[Controller] No parent window found, setting QuitOnLastWindowClosed(False) temporarily.")
                 self.app.setQuitOnLastWindowClosed(False)

            # Create and show the settings window
            self.settings_window = SettingsWindow(self.current_config, parent=parent)
            print(f"[Controller] SettingsWindow instance created: {self.settings_window}")
            self.settings_window.settings_saved.connect(self.handle_settings_saved)
            self.settings_window.finished.connect(self._settings_window_closed) # Use finished signal
            self.settings_window.show()
            print("[Controller] Called show() on new SettingsWindow.")
        else:
            # Settings window already exists, just activate it
            print("[Controller] SettingsWindow exists. Activating existing window.")
            self.settings_window.activateWindow()
            self.settings_window.raise_()

    @Slot(int)
    def _settings_window_closed(self, result):
        """Slot called when the SettingsWindow is closed."""
        print(f"[Controller] _settings_window_closed CALLED with result: {result}")
        # Check if any other main windows are still visible
        login_vis = self.login_window and self.login_window.isVisible()
        buddy_vis = self.buddy_list_window and self.buddy_list_window.isVisible()
        print(f"[Controller] Window visibility check: Login={login_vis}, BuddyList={buddy_vis}")

        # If no other primary windows are open, allow the app to quit naturally
        # unless the buddy list is hidden in the tray
        is_buddy_list_in_tray = self.buddy_list_window and not self.buddy_list_window.isVisible() and self.buddy_list_window.tray_icon and self.buddy_list_window.tray_icon.isVisible()

        if not login_vis and not buddy_vis and not is_buddy_list_in_tray:
             print("[Controller] No other primary windows open after settings close, setting QuitOnLastWindowClosed(True).")
             self.app.setQuitOnLastWindowClosed(True)
        else:
             # Keep app running if login or buddy list is still open/in tray
             self.app.setQuitOnLastWindowClosed(False)

        print(f"[Controller] Clearing SettingsWindow reference (was {self.settings_window}).")
        self.settings_window = None # Clear the reference


    @Slot(dict)
    def handle_settings_saved(self, new_settings):
        """Handles saving settings received from the SettingsWindow."""
        print("[Controller] handle_settings_saved CALLED.")
        old_update_setting = self.current_config.get("enable_update_notifications", True)
        old_sound_setting = self.current_config.get("sounds_enabled", True)

        self.current_config.update(new_settings) # Update in-memory config

        new_update_setting = self.current_config.get("enable_update_notifications", True)
        new_sound_setting = self.current_config.get("sounds_enabled", True)

        # Apply sound setting immediately
        if old_sound_setting != new_sound_setting:
            set_sounds_enabled(new_sound_setting)

        # Save the updated config to file
        saved_ok = save_config(self.current_config)

        if saved_ok:
            print("[Controller] Config successfully saved via handle_settings_saved.")
            # Update login window field if visible
            if self.login_window and self.login_window.isVisible():
                 new_name = new_settings.get("screen_name", "")
                 print(f"[Controller] Updating login window screen name field to: '{new_name}'")
                 self.login_window.screen_name_input.setText(new_name)

            # Show confirmation message (use settings window as parent if available)
            parent_widget = self.settings_window if self.settings_window else QApplication.activeWindow()
            QMessageBox.information(parent_widget, "Settings Saved", "Your settings have been saved.")

            # Connect/disconnect update service if setting changed
            if old_update_setting != new_update_setting:
                print(f"[Controller] Update notification setting changed to: {new_update_setting}")
                if new_update_setting:
                    self._connect_update_service()
                else:
                    self._disconnect_update_client()
        else:
            print("[Controller] ERROR: Failed to save config via handle_settings_saved.")
            parent_widget = self.settings_window if self.settings_window else QApplication.activeWindow()
            QMessageBox.warning(parent_widget, "Save Error", "Could not save settings to configuration file.")

    @Slot(dict)
    def handle_config_updated(self, updated_config):
        """Handles config updates originating from the BuddyListWindow (e.g., sound toggle)."""
        print("[Controller] handle_config_updated CALLED.")
        if updated_config:
            self.current_config = updated_config.copy() # Update memory copy

            # Apply relevant settings immediately (like sounds)
            new_sound_state = self.current_config.get("sounds_enabled", True)
            set_sounds_enabled(new_sound_state) # sound_utils handles its own state

            # Save the updated config
            if save_config(self.current_config):
                print("[Controller] Config successfully saved via handle_config_updated.")
            else:
                print("[Controller] ERROR: Failed to save config via handle_config_updated.")
                # Notify user if save failed
                if self.buddy_list_window and self.buddy_list_window.isVisible():
                    QMessageBox.warning(self.buddy_list_window, "Save Error", "Could not save updated settings.")
        else:
             print("[Controller] Warning: handle_config_updated received empty config.")

    # --- Sign On/Off and Quit ---
    @Slot(str, str, bool)
    def handle_sign_on_request(self, screen_name, password, auto_login):
        print(f"[Controller] handle_sign_on_request CALLED for: '{screen_name}', AutoLogin={auto_login}")
        self._connection_error_shown = False # Reset error flag for new sign on attempt
        if not screen_name:
             QMessageBox.warning(self.login_window, "Sign On Error", "Screen Name cannot be empty.")
             return

        # Validate screen name against loaded config
        self.current_config = load_config() # Reload config to be sure
        config_screen_name = self.current_config.get("screen_name")
        if config_screen_name != screen_name:
            QMessageBox.warning(self.login_window,"Sign On Error",f"The Screen Name '{screen_name}' does not match the configured name ('{config_screen_name}').\nPlease use 'Setup' to configure this name first, or enter the correct configured name.")
            return

        # Use the validated current config
        config_to_use = self.current_config
        print(f"[Controller] Using configuration for '{screen_name}'.")

        # Validate connection details if required
        mesh_type = config_to_use.get("mesh_conn_type","None")
        mesh_details = config_to_use.get("mesh_details","")
        if mesh_type != "None" and not mesh_details:
            QMessageBox.warning(self.login_window, "Config Incomplete", f"Meshtastic connection details for '{screen_name}' are missing.\nPlease use 'Setup'.")
            return

        # Check if password is required and provided for MQTT
        mqtt_server = config_to_use.get("server")
        mqtt_user = config_to_use.get("username")
        mqtt_needs_pass = bool(mqtt_user) # Password needed if username is set
        if mqtt_server and mqtt_needs_pass and not password:
            QMessageBox.warning(self.login_window, "Sign On Error", f"Password required for MQTT user '{mqtt_user}'.")
            return

        # Save auto-login preference if "Save configuration" was checked in login window
        should_save_prefs = False
        if self.login_window and self.login_window.get_save_config_preference():
             # Check if the auto_login checkbox state differs from saved config
             if self.current_config.get("auto_login") != auto_login:
                 self.current_config["auto_login"] = auto_login
                 should_save_prefs = True
                 print(f"[Controller] Auto-login preference updated in config: {auto_login}")

        if should_save_prefs:
            print("[Controller] Saving config due to changed preferences (Auto-Login)...")
            save_config(self.current_config) # Save updated auto_login state

        # Store runtime settings including password
        self.connection_settings = config_to_use.copy()
        self.connection_settings['password'] = password # Add potentially entered password
        print("[Controller] Proceeding to connect_services().")

        # Close login window and start connection process
        if self.login_window:
            self.login_window.setWindowTitle("Connecting...") # Give user feedback
            QApplication.processEvents() # Update UI
            # We don't close it here; connect_services will close it after buddy list is ready
            # self.login_window.close_window()
            # self.login_window = None

        # Initiate connections
        self.connect_services()

    # --- START ADDED show_buddy_list ---
    def show_buddy_list(self):
        """Creates and shows the buddy list window."""
        if self.buddy_list_window:
            print("[Controller] Buddy list window already exists.")
            self.buddy_list_window.activateWindow()
            self.buddy_list_window.raise_()
            return

        print("[Controller] show_buddy_list CALLED.")
        if not self.connection_settings:
            print("[Controller] Error: Cannot show buddy list, no runtime connection settings.")
            self.handle_sign_off() # Go back to login if called without settings
            return

        screen_name = self.connection_settings.get("screen_name", "Unknown")
        print(f"[Controller] Creating BuddyListWindow for '{screen_name}'...");

        try:
            self.buddy_list_window = BuddyListWindow(
                screen_name=screen_name,
                connection_settings=self.connection_settings,
                app_config=self.current_config
            )
            print("[Controller] Connecting buddy list signals...")
            # Connect signals from BuddyListWindow to Controller slots
            self.buddy_list_window.config_updated.connect(self.handle_config_updated)
            self.buddy_list_window.sign_off_requested.connect(self.handle_sign_off)
            self.buddy_list_window.quit_requested.connect(self.handle_quit) # Connect quit signal
            self.buddy_list_window.send_message_requested.connect(self.handle_send_request)
            self.buddy_list_window.destroyed.connect(self._buddy_list_destroyed)
            # Connect update notification signal from Controller to BuddyListWindow slot
            self.update_notification_received.connect(self.buddy_list_window.show_update_notification)


            print("[Controller] Showing buddy list window.");
            self.buddy_list_window.show()
            # Initial status message set in connect_services

        except Exception as e:
            print(f"[Controller] CRITICAL ERROR creating BuddyListWindow: {e}")
            traceback.print_exc()
            QMessageBox.critical(None, "UI Error", f"Failed to create buddy list window:\n{e}")
            self.buddy_list_window = None # Ensure reference is cleared
            self.handle_sign_off() # Go back to login on critical error

    # --- END ADDED show_buddy_list ---


    @Slot(bool, str)
    def handle_meshtastic_connection_status(self, connected, message):
        """Handles connection status updates for the Meshtastic handler (UI Thread)."""
        print(f"[Controller UI] handle_meshtastic_connection_status: connected={connected}, message='{message}'")

        if self._signing_off or self._quitting:
            print("[Controller UI] Ignoring Meshtastic connection status update during sign off/quit.")
            return

        if self.buddy_list_window:
            status_prefix = "Meshtastic: " if self.connection_settings.get("mesh_conn_type", "None") != "None" else ""
            status_message = f"{status_prefix}{message}" if connected else f"{status_prefix}Error: {message}"
            self.buddy_list_window.statusBar().showMessage(status_message, 5000)

        if connected:
            print("[Controller UI] Meshtastic Status: CONNECTED.")
            self._connection_error_shown = False # Reset error flag

            # If buddy list isn't shown yet, show it now
            if not self.buddy_list_window:
                print("[Controller UI] Meshtastic connected, showing buddy list window.")
                self.show_buddy_list() # This handles closing login window
            else:
                 print("[Controller UI] Meshtastic connected, buddy list window already open.")

            # Request initial node list (signal connected internally in handler)
            # self._start_initial_node_list_request() # Let the handler's signal trigger this
        else:
            print(f"[Controller UI] Meshtastic Status: DISCONNECTED/FAILED. Message: {message}")
            # Stop node update timer if running
            if self.node_update_timer.isActive():
                print("[Controller UI] Stopping periodic node update timer due to Meshtastic disconnect.")
                self.node_update_timer.stop()

            # Decide if this is a critical error
            if not self._connection_error_shown:
                mqtt_connected = self.mqtt_client and self.mqtt_client.is_connected()
                mesh_was_configured = self.connection_settings.get("mesh_conn_type", "None") != "None"
                # Show critical error only if Mesh was configured AND MQTT is also not connected/configured
                show_critical_error = mesh_was_configured and (not mqtt_connected or not self.connection_settings.get("server"))

                if show_critical_error:
                    print("[Controller UI] Showing Meshtastic connection failed/lost critical error message.")
                    QMessageBox.warning(self.buddy_list_window or self.login_window or None,
                                        "Meshtastic Connection Failed",
                                        f"Meshtastic connection failed or lost:\n{message}\n\nNo other connections active. Signing off.")
                    self._connection_error_shown = True
                    # If critical, sign off
                    QTimer.singleShot(0, self.handle_sign_off)
            # Disconnect handler properly after status update
            self._disconnect_mesh_handler() # Ensure handler is cleaned up

    # --- Main Connection Function ---
    def connect_services(self):
        """Initiates connections to Meshtastic and/or MQTT based on runtime settings."""
        print("[Controller] connect_services CALLED.")
        settings = self.connection_settings
        if not settings:
            print("[Controller] Error: connect_services called with empty runtime settings.")
            self.show_login_window(); # Go back to login if settings are missing
            return

        print(f"[Controller] Runtime Settings for Connection: {settings}")
        # Ensure app doesn't quit when buddy list is eventually closed (handled by tray logic)
        self.app.setQuitOnLastWindowClosed(False)
        print("[Controller] QuitOnLastWindowClosed set to False during connection.")

        # Create and show buddy list window first (it will show "Connecting...")
        # We close the login window *after* showing the buddy list
        if not self.buddy_list_window:
            print("[Controller] Creating and showing buddy list window...")
            self.show_buddy_list() # <--- Calls the added method
            if self.buddy_list_window:
                self.buddy_list_window.statusBar().showMessage("Connecting...", 0) # Indeterminate status
                QApplication.processEvents() # Update UI
            else:
                print("[Controller] Error: Failed to create buddy list window during connect_services.")
                self.handle_sign_off() # Critical error, go back to login
                return

        # Now close the login window if it's still open
        if self.login_window:
            print("[Controller] Closing login window.")
            self.login_window.close() # Trigger close event
            # Login window destruction is handled by its destroyed signal

        # --- Initialize USER MQTT (if configured) ---
        if settings.get('server'):
            mqtt_server = settings['server']
            mqtt_port = settings.get('port', 1883)
            mqtt_user = settings.get('username')
            mqtt_pass = settings.get('password') # Already retrieved in handle_sign_on_request

            print(f"[Controller User MQTT] Configured: Server={mqtt_server}:{mqtt_port}, User={mqtt_user}")
            if self.mqtt_client:
                print("[Controller User MQTT] Disconnecting existing client first...")
                self._disconnect_mqtt_client() # Ensure clean state

            try:
                print("[Controller User MQTT] Creating client instance...");
                # Use V2 API for consistency
                self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                # Assign callbacks
                self.mqtt_client.on_connect = self._on_mqtt_connect
                self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
                self.mqtt_client.on_message = self._on_mqtt_message
                self.mqtt_client.on_publish = self._on_mqtt_publish
                self.mqtt_client.on_subscribe = self._on_mqtt_subscribe
                # Set credentials if username provided
                if mqtt_user:
                    print(f"[Controller User MQTT] Setting username: {mqtt_user}")
                    self.mqtt_client.username_pw_set(mqtt_user, mqtt_pass)

                print(f"[Controller User MQTT] Connecting client to {mqtt_server}:{mqtt_port} asynchronously...")
                self.mqtt_client.connect_async(mqtt_server, mqtt_port, 60) # Connect in background
                print("[Controller User MQTT] Starting network loop in background thread...")
                self.mqtt_client.loop_start() # Start thread for network events

            except Exception as e:
                print(f"[Controller User MQTT] CRITICAL ERROR initializing client: {e}")
                traceback.print_exc()
                if not self._connection_error_shown:
                    parent = self.buddy_list_window or None
                    QMessageBox.critical(parent, "MQTT Setup Error", f"Failed to initialize MQTT client:\n{e}")
                    self._connection_error_shown = True
                self._disconnect_mqtt_client() # Clean up failed client
                # If Meshtastic isn't configured either, sign off
                if settings.get('mesh_conn_type', 'None') == 'None':
                    QTimer.singleShot(0, self.handle_sign_off)
        else:
            print("[Controller User MQTT] Not configured.")
            self.mqtt_client = None # Ensure client is None if not configured

        # --- Initialize Meshtastic Handler (if configured) ---
        mesh_type = settings.get('mesh_conn_type', 'None')
        if mesh_type != 'None':
            print("[Controller Meshtastic] Connection required. Initializing handler...")
            if self.meshtastic_handler:
                print("[Controller Meshtastic] Disconnecting existing handler first...")
                self._disconnect_mesh_handler()
                # Give a short delay before creating the new one
                QTimer.singleShot(250, lambda: self._create_and_connect_meshtastic(settings))
            else:
                # Create immediately if no previous handler exists
                self._create_and_connect_meshtastic(settings)
        else:
            print("[Controller Meshtastic] Connection type is 'None'.");
            self.meshtastic_handler = None # Ensure handler is None
            # Check if BOTH connections are None - this is an error state
            if not settings.get('server'):
                print("[Controller] Error: No connections configured.")
                if not self._connection_error_shown:
                    parent = self.buddy_list_window or None
                    QMessageBox.critical(parent, "Connection Error",
                                         "No Meshtastic or MQTT connection configured.\nPlease use Setup.")
                    self._connection_error_shown = True
                self.handle_sign_off() # Sign off if no connections possible
                return

        print("[Controller] connect_services finished initiating connections.")


    def _create_and_connect_meshtastic(self, settings):
         """Creates and initiates connection for the Meshtastic handler."""
         print("[Controller] _create_and_connect_meshtastic CALLED.")
         if self._signing_off or self._quitting:
             print("[Controller] Aborting Meshtastic connect during sign off/quit.")
             return
         if self.meshtastic_handler:
             print("[Controller] Warning: Meshtastic handler already exists in _create_and_connect... - disconnecting first.")
             self._disconnect_mesh_handler() # Should not happen due to check before calling, but safeguard

         try:
             print("[Controller] Creating Meshtastic Handler...");
             self.meshtastic_handler = MeshtasticHandler(settings) # Pass runtime settings
             print("[Controller] Connecting Meshtastic Handler signals...");
             # Connect signals from handler to controller slots
             self.meshtastic_handler.connection_status.connect(self.handle_meshtastic_connection_status)
             self.meshtastic_handler.message_received.connect(self.route_incoming_message_from_mesh)
             self.meshtastic_handler.node_list_updated.connect(self._handle_node_list_update)
             # Connect internal signal for when connection *actually* establishes
             self.meshtastic_handler._connection_established_signal.connect(self._start_initial_node_list_request)

             print("[Controller] Calling handler.connect_to_device()...")
             connect_initiated = self.meshtastic_handler.connect_to_device()
             print(f"[Controller] Meshtastic connect_to_device setup initiated: {connect_initiated}")
             # Note: Actual connection happens asynchronously via pubsub.
             # Status updates will come via the connection_status signal.
             if not connect_initiated and self.meshtastic_handler:
                 # If connect_to_device itself reported failure (e.g., bad port)
                 print("[Controller] Meshtastic synchronous connection setup failed.")
                 # Use QTimer to ensure UI update happens after current execution stack
                 QTimer.singleShot(0, lambda: self.handle_meshtastic_connection_status(False, "Initial connection setup failed (e.g., invalid port/IP)"))

         except Exception as e:
             print(f"[Controller] CRITICAL ERROR creating/connecting Meshtastic Handler: {e}")
             traceback.print_exc()
             if not self._connection_error_shown:
                 parent = self.buddy_list_window or None
                 QMessageBox.critical(parent, "Meshtastic Error", f"Failed Meshtastic handler initialization:\n{e}")
                 self._connection_error_shown = True
             self._disconnect_mesh_handler() # Clean up failed handler
             # If MQTT also failed or isn't configured, sign off
             if not self.mqtt_client or not self.mqtt_client.is_connected():
                 QTimer.singleShot(0, self.handle_sign_off)


    @Slot()
    def handle_sign_off(self):
        """Handles user request to sign off."""
        print("[Controller] handle_sign_off CALLED.")
        if self._signing_off: return # Prevent recursion
        self._signing_off = True # Set flag

        print("[Controller] Disconnecting services for sign off...")
        self._disconnect_services()

        # Close buddy list window nicely
        if self.buddy_list_window:
            print("[Controller] Closing buddy list window during sign off.")
            # Disconnect signals specific to buddy list first
            try:
                self.buddy_list_window.sign_off_requested.disconnect(self.handle_sign_off)
                self.buddy_list_window.quit_requested.disconnect(self.handle_quit)
                # Disconnect update notification signal if connected
                self.update_notification_received.disconnect(self.buddy_list_window.show_update_notification)
            except (TypeError, RuntimeError) as e:
                 print(f"[Controller] Warning: Error disconnecting buddy list signals: {e}")
            self.buddy_list_window._is_closing = True # Prevent hide-to-tray on close
            self.buddy_list_window.close() # Let closeEvent handle hiding etc.
            # self.buddy_list_window = None # Set to None in _buddy_list_destroyed

        # Clear runtime settings
        self.connection_settings = {}
        print("[Controller] Showing login window after sign off.")

        # Play sign-off sound
        if self.current_config.get("sounds_enabled", True):
            print("[Controller] Playing sign-off sound.")
            play_sound_async("signoff.wav")

        # Show login window (resets signing_off flag internally)
        self.show_login_window()

    @Slot()
    def handle_quit(self):
        """Handles user request to quit the application (e.g., from tray)."""
        print("[Controller] handle_quit CALLED.")
        if self._quitting: return # Prevent recursion
        self._quitting = True # Set flag
        self.cleanup() # Perform full cleanup
        print("[Controller] Quitting application.")
        self.app.quit() # Tell QApplication to exit event loop


    # --- Helper Disconnect Methods ---
    def _disconnect_mesh_handler(self):
        """Safely disconnects and cleans up the Meshtastic handler."""
        print("[Controller] Disconnecting Meshtastic Handler...")
        if self.node_update_timer.isActive():
            print("[Controller] Stopping periodic node update timer.")
            self.node_update_timer.stop()

        if self.meshtastic_handler:
            handler = self.meshtastic_handler
            self.meshtastic_handler = None # Clear reference first
            print("[Controller] Disconnecting Meshtastic handler signals...")
            # Disconnect all signals connected in _create_and_connect_meshtastic
            try: handler._connection_established_signal.disconnect(self._start_initial_node_list_request)
            except (TypeError, RuntimeError): pass
            try: handler.connection_status.disconnect(self.handle_meshtastic_connection_status)
            except (TypeError, RuntimeError): pass
            try: handler.message_received.disconnect(self.route_incoming_message_from_mesh)
            except (TypeError, RuntimeError): pass
            try: handler.node_list_updated.disconnect(self._handle_node_list_update)
            except (TypeError, RuntimeError): pass

            print("[Controller] Calling handler.disconnect()...")
            try:
                handler.disconnect() # Tell handler to close interface etc.
            except Exception as e:
                print(f"Error during Meshtastic handler disconnect call: {e}")
            print("[Controller] Meshtastic handler disconnected.")
        else:
            print("[Controller] No active Meshtastic handler to disconnect.")

    def _disconnect_mqtt_client(self):
        """Safely disconnects and cleans up the user MQTT client."""
        print("[Controller] Disconnecting USER MQTT client...")
        if self.mqtt_client:
            client = self.mqtt_client
            self.mqtt_client = None # Clear reference first
            try:
                print("[Controller] Stopping USER MQTT loop...")
                client.loop_stop(force=False) # Stop network thread gracefully
                print("[Controller] Sending USER MQTT disconnect...")
                client.disconnect() # Send DISCONNECT packet
                # Clear callbacks
                client.on_connect = None; client.on_disconnect = None; client.on_message = None
                client.on_publish = None; client.on_subscribe = None
                print("[Controller] USER MQTT client disconnected.")
            except Exception as e:
                print(f"[Controller] Error during USER MQTT disconnect/cleanup: {e}")
                traceback.print_exc()
        else:
            print("[Controller] No active USER MQTT client to disconnect.")

    def _disconnect_services(self):
        """Disconnects both Meshtastic and user MQTT services."""
        print("[Controller] _disconnect_services CALLED (User MQTT & Mesh).")
        self._disconnect_mesh_handler()
        self._disconnect_mqtt_client()
        print("[Controller] _disconnect_services finished (User MQTT & Mesh).")

    # --- Message Routing ---
    @Slot(list)
    def _handle_node_list_update(self, nodes_list):
        """Routes node list updates from handler to buddy list (UI Thread)."""
        if self.buddy_list_window:
            self.buddy_list_window.handle_node_list_update(nodes_list)

    @Slot()
    def _buddy_list_destroyed(self):
        """Slot called when the BuddyListWindow is destroyed."""
        print("[Controller] Buddy list window destroyed.")
        # Disconnect update notification signal if connected
        try:
            # Check if buddy_list_window still holds a reference before accessing it
            # Note: This might be too late if the object is already partially destroyed
            # Safer to just attempt disconnect and catch error if it fails
            self.update_notification_received.disconnect(self.buddy_list_window.show_update_notification)
            print("[Controller] Disconnected update notification from destroyed buddy list.")
        except (TypeError, RuntimeError) as e:
            # print(f"[Controller] Warning: Error disconnecting update notification: {e}")
            pass # Ignore if already disconnected or object invalid

        self.buddy_list_window = None # Clear reference

        # If sign off or quit wasn't requested, this was likely an unexpected close.
        if not self._signing_off and not self._quitting:
             print("[Controller] Buddy list destroyed unexpectedly. Signing off.")
             # Use timer to avoid calling sign off during destruction handling
             QTimer.singleShot(0, self.handle_sign_off)


    @Slot(str, str, str) # sender_id, text, msg_type ('direct'/'broadcast')
    def route_incoming_message_from_mesh(self, sender_id, text, msg_type):
        """Routes incoming Meshtastic messages from handler to buddy list (UI Thread)."""
        # ** REMOVED INCORRECT LINE **
        # if self.buddy_list_window:
        #     self.buddy_list_window.public_chat_area.append(f"{sender_id}: {text}")

        # This is the correct logic:
        if self.buddy_list_window:
            try:
                # Pass all info to buddy list's handler
                self.buddy_list_window.handle_incoming_message(sender_id, text, 'meshtastic', msg_type)
            except Exception as e:
                print(f"ERROR calling handle_incoming_message for Meshtastic: {e}");
                traceback.print_exc()
        else:
             print("[Controller] Warning: Buddy list window not available for incoming Meshtastic message.")


    @Slot(str, str, str) # recipient_id, message_text, network_type ('mqtt'/'meshtastic')
    def handle_send_request(self, recipient_id, message_text, network_type):
        """Handles requests to send messages, originating from chat windows."""
        print(f"[Controller UI] handle_send_request: Type={network_type}, To={recipient_id}")

        if network_type == 'meshtastic':
            if self.meshtastic_handler and self.meshtastic_handler.is_running:
                # Determine channel index: 0 for public chat, configured default otherwise
                channel_index = 0 if recipient_id == PUBLIC_CHAT_ID else self.connection_settings.get("meshtastic_channel_index", 0)
                # Use '^all' as destination for public chat
                dest_id = PUBLIC_CHAT_ID if recipient_id == PUBLIC_CHAT_ID else recipient_id

                print(f"[Controller UI] Forwarding send request to Meshtastic handler (Dest: {dest_id}, Channel: {channel_index}).")
                self.meshtastic_handler.send_message(dest_id, message_text, channel_index)
            else:
                print("[Controller UI] Warning: Cannot send Meshtastic message. Handler not available or not running.")
                if self.buddy_list_window: # Give feedback if possible
                    self.buddy_list_window.statusBar().showMessage("Error: Meshtastic not connected.", 3000)

        elif network_type == 'mqtt':
            if self.mqtt_client and self.mqtt_client.is_connected():
                try:
                    # For MQTT, the recipient ID is the target topic
                    target_topic = recipient_id
                    print(f"[Controller UI] Publishing MQTT message to topic '{target_topic}'")
                    # Publish with QoS 1 for reliability
                    result, mid = self.mqtt_client.publish(
                        topic=target_topic, payload=message_text.encode('utf-8'), qos=1, retain=False
                    )
                    if result == mqtt.MQTT_ERR_SUCCESS:
                        print(f"[Controller UI] MQTT message queued successfully (mid={mid}).")
                        # Optional: Play send sound only after publish succeeds? (Needs publish callback)
                    else:
                        print(f"[Controller UI] Error: Failed to queue MQTT message (Code: {result}).")
                        if self.buddy_list_window:
                            self.buddy_list_window.statusBar().showMessage(f"Error sending IM (Code: {result})", 3000)
                except Exception as e:
                    print(f"[Controller UI] Exception during MQTT publish: {e}")
                    traceback.print_exc()
                    if self.buddy_list_window:
                        self.buddy_list_window.statusBar().showMessage("Error sending IM.", 3000)
            else:
                print("[Controller UI] Warning: Cannot send MQTT message. Client not available or not connected.")
                if self.buddy_list_window:
                    self.buddy_list_window.statusBar().showMessage("Error: MQTT not connected.", 3000)
        else:
            print(f"[Controller UI] Warning: Unknown network type '{network_type}' requested for send.")

    # --- Application Cleanup ---
    @Slot()
    def cleanup(self):
        """Cleans up resources before the application exits."""
        print("[Controller] cleanup CALLED.")
        self._quitting = True # Set quitting flag

        # Stop timers
        if self.node_update_timer.isActive():
             print("[Controller] Stopping periodic node update timer during cleanup.")
             self.node_update_timer.stop()

        # Disconnect network services
        self._disconnect_services() # Disconnects user MQTT & Mesh
        self._disconnect_update_client() # Disconnect update MQTT

        # Close windows explicitly if needed
        # Use a copy of the chat windows list to avoid modifying during iteration
        if self.buddy_list_window:
            print("[Controller] Closing open chat windows during cleanup...")
            # Make sure chat_windows attribute exists before iterating
            if hasattr(self.buddy_list_window, 'chat_windows'):
                for chat_win in list(self.buddy_list_window.chat_windows.values()):
                    try:
                        print(f"[Controller] Closing chat window for {chat_win.buddy_id}")
                        chat_win.close()
                    except Exception as e:
                        print(f"Error closing chat window during cleanup: {e}")

        if self.buddy_list_window:
            print("[Controller] Closing buddy list window during cleanup.")
            # Prevent its closeEvent from trying to hide to tray during quit
            self.buddy_list_window._is_closing = True
            # Hide tray icon before closing window
            if self.buddy_list_window.tray_icon:
                 self.buddy_list_window.tray_icon.hide()
            self.buddy_list_window.close()
        if self.settings_window:
             print("[Controller] Closing settings window during cleanup.")
             self.settings_window.close()
        if self.login_window:
            print("[Controller] Closing login window during cleanup.")
            self.login_window.close()


        print("[Controller] Cleanup finished.")
        # Allow app to quit now main windows are closed/closing
        # self.app.setQuitOnLastWindowClosed(True) # Let app.quit() handle this


# --- Main Execution Block ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName("MIMMeshtastic")
    app.setOrganizationName("MIMDev")
    app.setStyle("Fusion") # Or another style like "Windows", "macOS"

    # Set application icon
    app_icon_path = get_resource_path("resources/icons/mim_logo.png")
    app_icon = QIcon(app_icon_path)
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    else:
        print(f"Warning: Application icon not found at {app_icon_path}")


    # --- Font Loading ---
    font_dir = get_resource_path("resources/fonts")
    loaded_font_families = []
    if os.path.isdir(font_dir):
        print(f"Looking for fonts in: {font_dir}")
        for filename in os.listdir(font_dir):
            if filename.lower().endswith((".ttf", ".otf")):
                font_path = os.path.join(font_dir, filename)
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    # Specifically track if Helvetica loaded
                    if "Helvetica" in families and "Helvetica" not in loaded_font_families:
                        loaded_font_families.append("Helvetica")
                    print(f"Loaded font: {filename} (Families: {families})")
                else:
                    print(f"Warning: Failed to load font: {font_path}")
    else:
        print(f"Warning: Font directory not found: {font_dir}")

    # Set default application font (prefer Helvetica if loaded)
    default_font_family = "Helvetica" if "Helvetica" in loaded_font_families else "Arial" # Fallback
    default_font_size = 9 # Reasonable default size
    app.setFont(QFont(default_font_family, default_font_size))
    print(f"Default application font set to: {default_font_family} {default_font_size}pt")

    # --- Stylesheet Loading ---
    qss_path = get_resource_path("resources/styles/styles.qss")
    try:
        with open(qss_path, "r") as f:
            app.setStyleSheet(f.read())
        print(f"Stylesheet '{os.path.basename(qss_path)}' applied.")
    except FileNotFoundError:
        print(f"Info: Stylesheet not found at '{qss_path}'. Using default style.")
    except Exception as e:
        print(f"Error loading stylesheet from '{qss_path}': {e}")

    # --- Initialize and Run ---
    print("Creating ApplicationController...")
    controller = ApplicationController(app) # Controller shows login window
    print("Starting Qt event loop (app.exec)...")
    exit_code = app.exec()
    print(f"Qt event loop finished with exit code: {exit_code}")
    sys.exit(exit_code)
