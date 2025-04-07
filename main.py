# main.py
import sys
import os
import json
import paho.mqtt.client as mqtt
import traceback 
from pathlib import Path
# **************************
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QObject, QDir, Qt, Slot, QTimer, QStandardPaths, QCoreApplication, Signal
from PySide6.QtGui import QFontDatabase, QFont
from sound_utils import play_sound_async
from sound_utils import set_sounds_enabled
from login_window import LoginWindow
from buddy_list_window import BuddyListWindow
from meshtastic_handler import MeshtasticHandler
from settings_window import SettingsWindow

# Define update interval (e.g., 5 minutes = 5 * 60 * 1000 ms)
NODE_UPDATE_INTERVAL_MS = 5 * 60 * 1000
CONFIG_FILE_NAME = "mim_meshtastic_config.json"

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- Configuration Save/Load Functions ---
def get_config_path():

    app_data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not app_data_dir:
        print("Warning: Could not determine AppDataLocation. Using current directory for config.")
        app_data_dir = "."

    app_name_folder = QCoreApplication.applicationName()
    if not app_name_folder: 
        app_name_folder = "MIMMeshtastic"
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
    """Loads the configuration dictionary from a JSON file."""
    path = get_config_path()
    config_data = {}
    if not os.path.exists(path):
        print(f"Configuration file not found: {path}")
        return config_data

    try:
        with open(path, 'r', encoding='utf-8') as f:
            loaded_json = json.load(f)
            if isinstance(loaded_json, dict):
                config_data = loaded_json
                print(f"Configuration loaded from: {path}")
            else:
                print(f"Warning: Config file {path} does not contain a valid JSON object. Using defaults.")
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading or parsing configuration from {path}: {e}. Using defaults.")
    except Exception as e:
        print(f"Unexpected error loading configuration: {e}")

    return config_data

# --- Application Controller ---
class ApplicationController(QObject):
    # Define signals here if not already defined
    mqtt_connection_updated = Signal(bool, str)
    mqtt_message_received_signal = Signal(str, str, str)

    def __init__(self, app: QApplication): # Added type hint for clarity
        super().__init__()
        self.app = app
        self.login_window = None
        self.buddy_list_window = None
        self.settings_window = None
        self.current_config = load_config() # Load config on startup
        self.connection_settings = {}
        self.mqtt_client = None
        self.meshtastic_handler = None
        self._signing_off = False
        self._connection_error_shown = False
        self._node_list_timer_active = False # Flag for initial delay timer

        # Set initial sound state from config
        initial_sound_state = self.current_config.get("sounds_enabled", True) # Default true
        set_sounds_enabled(initial_sound_state)

        # Initialize the periodic node update timer
        self.node_update_timer = QTimer(self)
        self.node_update_timer.timeout.connect(self._request_periodic_node_update)

        # Connect MQTT Signals (Ensure these signals are defined above)
        self.mqtt_connection_updated.connect(self._handle_mqtt_connection_update)
        self.mqtt_message_received_signal.connect(self._route_incoming_mqtt_message)

        # Connect app signals
        self.app.aboutToQuit.connect(self.cleanup)

        # Allow app to run without windows initially
        self.app.setQuitOnLastWindowClosed(False)
        print("[Controller] Initialized.") # Minimal print

        # Start the application by showing the login window
        self.show_login_window()

    def show_login_window(self):
        """Creates and shows the login window."""
        print("[Controller] show_login_window CALLED.")
        self._signing_off = False
        self._connection_error_shown = False
        self._disconnect_services() # Ensure clean state before showing login

        # Close other primary windows if open
        if self.buddy_list_window:
            print("[Controller] Closing existing buddy list window.")
            # Disconnect signals before closing to avoid issues? Maybe not needed.
            self.buddy_list_window.close()
            self.buddy_list_window = None
        if self.settings_window:
             print("[Controller] Closing existing settings window.")
             self.settings_window.close()
             self.settings_window = None

        # Make sure app quits if login window is the only one closed by user
        self.app.setQuitOnLastWindowClosed(True)
        print("[Controller] QuitOnLastWindowClosed set to True for Login Window.")

        # Load config and prepare login window state
        saved_screen_name = self.current_config.get("screen_name")
        raw_auto_login = self.current_config.get("auto_login", False) # Default to False
        saved_auto_login = raw_auto_login if isinstance(raw_auto_login, bool) else False

        print(f"[Controller] Creating LoginWindow (ScreenName: {saved_screen_name}, AutoLogin: {saved_auto_login})")
        self.login_window = LoginWindow(saved_screen_name, saved_auto_login)
        # Connect signals from login window
        self.login_window.setup_requested.connect(self.show_settings_window)
        self.login_window.sign_on_requested.connect(self.handle_sign_on_request)
        self.login_window.show()
        
# --- MQTT Callback Methods ---

    def _on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        """Callback for when MQTT client connects."""
        print(f"[Controller] _on_mqtt_connect CALLED with result code: {rc}")
        if rc == 0:
            print("[Controller] MQTT Connected successfully.")
            # Subscribe to own topic upon successful connection
            my_topic = self.connection_settings.get("screen_name")
            if my_topic:
                try:
                    print(f"[Controller] Subscribing to MQTT topic: {my_topic}")
                    # Use QoS 1 for subscriptions too, for example
                    result, mid = self.mqtt_client.subscribe(my_topic, qos=1)
                    if result == mqtt.MQTT_ERR_SUCCESS:
                        print(f"[Controller] MQTT subscription request successful (mid={mid}).")
                    else:
                        print(f"[Controller] Error: MQTT subscription failed (Error code: {result}).")
                        # Emit failure signal if subscribe fails critical topic
                        self.mqtt_connection_updated.emit(False, f"Subscription failed (Code: {result})")
                        return # Stop further processing on critical subscription failure
                    # Emit success signal only after successful critical subscription
                    self.mqtt_connection_updated.emit(True, "Connected")
                except Exception as e:
                    print(f"[Controller] Exception during MQTT subscribe: {e}")
                    traceback.print_exc()
                    self.mqtt_connection_updated.emit(False, f"Exception during subscribe: {e}")
            else:
                print("[Controller] Warning: Cannot subscribe, screen_name not found in settings.")
                self.mqtt_connection_updated.emit(False, "Cannot subscribe (no screen name)")
        else:
            error_string = mqtt.connack_string(rc)
            print(f"[Controller] Error: MQTT Connection failed: {error_string} (Code: {rc})")
            self.mqtt_connection_updated.emit(False, f"Connection failed: {error_string}")

    def _on_mqtt_disconnect(self, client, userdata, rc, properties=None):
        """Callback for when MQTT client disconnects."""
        print(f"[Controller] _on_mqtt_disconnect CALLED with result code: {rc}")
        if rc == 0:
            print("[Controller] MQTT Disconnected cleanly.")
            # Only emit update if not during intentional sign-off/cleanup
            if not self._signing_off:
                 self.mqtt_connection_updated.emit(False, "Disconnected")
        else:
            print(f"[Controller] Error: MQTT Unexpected disconnection (Code: {rc}).")
            # Emit update for unexpected disconnections
            self.mqtt_connection_updated.emit(False, f"Unexpected disconnection (Code: {rc})")

    def _on_mqtt_message(self, client, userdata, msg):
        """Callback for when an MQTT message is received."""
        print(f"[Controller] _on_mqtt_message CALLED for topic: {msg.topic}")
        try:
            payload_str = msg.payload.decode("utf-8")
            print(f"[Controller] MQTT Received: '{payload_str}' on topic '{msg.topic}'")

            sender_id = msg.topic
            message_text = payload_str
            msg_type = 'direct'

            # Emit signal to safely route message to UI thread
            self.mqtt_message_received_signal.emit(sender_id, message_text, msg_type)

        except Exception as e:
            print(f"[Controller] Error processing incoming MQTT message: {e}")
            traceback.print_exc()

    def _on_mqtt_publish(self, client, userdata, mid):
        """Optional callback: Called when a message with QoS > 0 is successfully sent."""
        print(f"[Controller] _on_mqtt_publish CALLED for mid: {mid}")

    def _on_mqtt_subscribe(self, client, userdata, mid, granted_qos, properties=None):
        """Optional callback: Called when the broker responds to a subscription request."""
        print(f"[Controller] _on_mqtt_subscribe CALLED - mid: {mid}, granted QoS: {granted_qos}")

    @Slot()
    def _start_delayed_node_list_request(self):
        """Starts the timer to delay the *initial* node list request."""
        if not self.meshtastic_handler or self._node_list_timer_active:
             # print("[Controller] Skipping delayed node list request...") # Optional print
             return

        delay_ms = 5000 # 5 seconds delay for initial request
        print(f"[Controller] Starting {delay_ms}ms timer for *initial* node list request...")
        self._node_list_timer_active = True
        # Note: This timer only runs ONCE for the initial delay
        QTimer.singleShot(delay_ms, self._emit_node_list_request_signal)

    @Slot()
    def _emit_node_list_request_signal(self):
         """Emits signal for initial node list request and starts periodic timer."""
         if self.meshtastic_handler:
              print("[Controller] Initial timer finished, emitting node list request signal.")
              self.meshtastic_handler._request_node_list_signal.emit()
              # **** Start the PERIODIC timer AFTER the initial request ****
              print(f"[Controller] Starting periodic node update timer ({NODE_UPDATE_INTERVAL_MS} ms interval).")
              self.node_update_timer.start(NODE_UPDATE_INTERVAL_MS)
              # **********************************************************
         self._node_list_timer_active = False # Reset flag for the single-shot timer

    @Slot()
    def _request_periodic_node_update(self):
        """Slot called by the periodic timer to request node updates."""
        print("[Controller] Periodic timer timeout.")
        if self.meshtastic_handler and self.meshtastic_handler.is_running:
            print("[Controller] Requesting periodic node list update from handler.")
            self.meshtastic_handler.request_node_list()
        else:
            print("[Controller] Skipping periodic node update (handler not available or not running).")

    @Slot()
    def show_settings_window(self):
        """Shows the settings/setup window."""
        print("[Controller] show_settings_window CALLED.")
        print(f"[Controller] Current self.settings_window state: {self.settings_window}")

        if self.settings_window is None:
            print("[Controller] self.settings_window is None. Creating new SettingsWindow.")
            # Determine parent: should be the window that requested it (usually login)
            parent = self.login_window if self.login_window and self.login_window.isVisible() else None
            print(f"[Controller] Setting parent for SettingsWindow: {parent}")

            # Keep app alive if login window isn't visible when settings are opened
            if not parent:
                 print("[Controller] No parent window found, setting QuitOnLastWindowClosed(False).")
                 self.app.setQuitOnLastWindowClosed(False)

            self.settings_window = SettingsWindow(self.current_config, parent=parent)
            print(f"[Controller] New SettingsWindow instance created: {self.settings_window}")
            # Connect signal from settings window for when save occurs
            self.settings_window.settings_saved.connect(self.handle_settings_saved)
            # Connect finished signal to clean up reference
            self.settings_window.finished.connect(self._settings_window_closed)
            self.settings_window.show()
            print("[Controller] Called show() on new SettingsWindow.")
        else:
            # If instance somehow already exists, just bring it to front
            print("[Controller] self.settings_window exists. Activating existing window.")
            self.settings_window.activateWindow()
            self.settings_window.raise_() # Ensure it's on top

    @Slot(int)
    def _settings_window_closed(self, result):
        """Slot connected to finished signal of settings window."""
        print(f"[Controller] _settings_window_closed CALLED with result: {result}")
        print(f"[Controller] State before clearing: self.settings_window = {self.settings_window}")

        # Restore Quit setting ONLY if no other primary windows are visible
        login_vis = self.login_window and self.login_window.isVisible()
        buddy_vis = self.buddy_list_window and self.buddy_list_window.isVisible()
        print(f"[Controller] Window visibility check: Login={login_vis}, BuddyList={buddy_vis}")

        if not login_vis and not buddy_vis:
             print("[Controller] No other primary windows open, setting QuitOnLastWindowClosed(True).")
             self.app.setQuitOnLastWindowClosed(True)
        else:
             print("[Controller] Primary window still open, QuitOnLastWindowClosed unchanged.")

        # Clear the reference to the window instance
        self.settings_window = None
        print(f"[Controller] State after clearing: self.settings_window = {self.settings_window}")

    @Slot(dict)
    def handle_settings_saved(self, new_settings):
        """Handles the settings_saved signal from the SettingsWindow."""
        print("[Controller] handle_settings_saved CALLED.")
        self.current_config.update(new_settings)

        new_sound_state = self.current_config.get("sounds_enabled", True)
        set_sounds_enabled(new_sound_state)

        saved_ok = save_config(self.current_config)

        if saved_ok:
            print("[Controller] Config successfully saved via handle_settings_saved.")
            # Update login window screen name if it's currently open
            if self.login_window:
                 new_name = new_settings.get("screen_name", "")
                 print(f"[Controller] Updating login window screen name to: {new_name}")
                 self.login_window.screen_name_input.setText(new_name)
            # Show confirmation message (use settings_window as parent if possible)
            parent_widget = self.settings_window if self.settings_window else None
            QMessageBox.information(parent_widget, "Settings Saved", "Your settings have been saved.")
        else:
            print("[Controller] ERROR: Failed to save config via handle_settings_saved.")
            # Show error message (use settings_window as parent if possible)
            parent_widget = self.settings_window if self.settings_window else None
            QMessageBox.warning(parent_widget, "Save Error", "Could not save settings to configuration file.")


    @Slot(dict)
    def handle_config_updated(self, updated_config):
        """Saves the configuration when updated by other windows (like buddy list)."""
        print("[Controller] handle_config_updated CALLED.")
        if updated_config:
            new_sound_state = updated_config.get("sounds_enabled", True)
            set_sounds_enabled(new_sound_state)

            if save_config(updated_config):
                print("[Controller] Config successfully saved via handle_config_updated.")
                self.current_config = updated_config.copy() # Update controller's copy
            else:
                print("[Controller] ERROR: Failed to save config via handle_config_updated.")
                # Show error on buddy list window if possible
                if self.buddy_list_window:
                    QMessageBox.warning(self.buddy_list_window, "Save Error", "Could not save updated settings.")
        else:
             print("[Controller] Warning: handle_config_updated received empty config.")

    @Slot(str, str, bool) # screen_name, password, auto_login
    def handle_sign_on_request(self, screen_name, password, auto_login):
        """Handles the request to sign on from the LoginWindow."""
        print(f"[Controller] handle_sign_on_request CALLED for: '{screen_name}', AutoLogin={auto_login}")
        self._connection_error_shown = False
        if not screen_name: return

        # Verify screen name matches current config (simple single-profile check)
        config_screen_name=self.current_config.get("screen_name")
        if config_screen_name != screen_name:
            QMessageBox.warning(self.login_window,"Sign On Error",f"Config for '{screen_name}' not found.\nPlease use 'Setup'.")
            return

        config_to_use=self.current_config
        print(f"[Controller] Using configuration for '{screen_name}'.")

        # Check Meshtastic config details are sufficient
        mesh_type=config_to_use.get("mesh_conn_type","None")
        mesh_details=config_to_use.get("mesh_details","")
        if mesh_type != "None" and not mesh_details:
            QMessageBox.warning(self.login_window, "Config Incomplete", f"Mesh details for '{screen_name}' missing.\nPlease use 'Setup'.")
            return

        # Update auto_login preference if "Save Config" checked in login window
        should_save=False
        if self.login_window and self.login_window.get_save_config_preference():
             if self.current_config.get("auto_login") != auto_login:
                 self.current_config["auto_login"]=auto_login
                 should_save=True
                 print(f"Auto-login pref updated: {auto_login}")
        if should_save:
            print("[Controller] Saving config due to changed preferences...")
            save_config(self.current_config)

        # Prepare runtime settings and proceed
        self.connection_settings = config_to_use.copy()
        self.connection_settings['password'] = password
        print("[Controller] Proceeding to connect_services().")
        self.connect_services()
        
# --- Slots for Handling MQTT Events in UI Thread ---

    @Slot(bool, str)
    def _handle_mqtt_connection_update(self, connected, message):
        """Handles MQTT connection status changes in the main thread."""
        print(f"[Controller] _handle_mqtt_connection_update CALLED: connected={connected}, message='{message}'")

        if self._signing_off:
            print("[Controller] Ignoring MQTT connection update during sign off.")
            return

        if connected:
            self._connection_error_shown = False
            if self.buddy_list_window:
                self.buddy_list_window.statusBar().showMessage(f"MQTT: {message}", 5000)
        else:
            # Handle disconnection or failure
            print(f"[Controller] MQTT connection failed or lost: {message}")

            if not self._signing_off and not self._connection_error_shown:
                meshtastic_connected = self.meshtastic_handler and self.meshtastic_handler.is_running
                if not meshtastic_connected:
                    print("[Controller] Showing MQTT connection failed/lost error message (Meshtastic also down).")
                    QMessageBox.warning(None,"MQTT Connection Failed",f"MQTT connection failed or lost:\n{message}")
                    self._connection_error_shown = True
                    if self.buddy_list_window:
                        self.buddy_list_window.close()
                        self.buddy_list_window = None
                    self.show_login_window()
                else:
                    if self.buddy_list_window:
                         self.buddy_list_window.statusBar().showMessage(f"MQTT Error: {message}", 5000)

            # Clean up MQTT client resources if disconnected unexpectedly
            if self.mqtt_client:

                 if "Unexpected" in message:
                      print("[Controller] Cleaning up MQTT client due to unexpected disconnect.")
                      self.mqtt_client.loop_stop()
                      self.mqtt_client = None


    @Slot(str, str, str) # sender_id, text, msg_type
    def _route_incoming_mqtt_message(self, sender_id, text, msg_type):
        """Routes incoming MQTT messages (received via signal) to the buddy list."""
        print(f"[Controller] _route_incoming_mqtt_message CALLED: From={sender_id}")
        if self.buddy_list_window:
             try:
                  self.buddy_list_window.handle_incoming_message(sender_id, text, 'mqtt', msg_type)
             except Exception as e:
                  print(f"ERROR calling handle_incoming_message for MQTT: {e}"); traceback.print_exc()
        else:
             print("[Controller] Warning: Buddy list window not available for incoming MQTT message.")
             
    def connect_services(self):
        """Initiates connections based on self.connection_settings."""
        print("[Controller] connect_services CALLED.")
        settings = self.connection_settings
        if not settings:
            print("[Controller] Error: connect_services called with empty settings.")
            self.show_login_window(); return

        print(f"[Controller] Runtime Settings for Connection: {settings}")

        self.app.setQuitOnLastWindowClosed(False)
        print("[Controller] QuitOnLastWindowClosed set to False during connection.")

        if self.login_window:
            print("[Controller] Closing login window.")
            self.login_window.close_window(); self.login_window = None

        # --- Initialize MQTT (if configured) ---
        if settings.get('server'):
            mqtt_server = settings['server']
            mqtt_port = settings.get('port', 1883)
            mqtt_user = settings.get('username')
            mqtt_pass = settings.get('password')

            print(f"[Controller] MQTT Configured: Server={mqtt_server}:{mqtt_port}")

            if self.mqtt_client:
                 print("[Controller] Disconnecting existing MQTT client first...")
                 try:
                      if self.mqtt_client.is_connected():
                           self.mqtt_client.loop_stop()
                           self.mqtt_client.disconnect()
                 except Exception as e: print(f"Error disconnecting old MQTT client: {e}")
                 self.mqtt_client = None

            try:
                print("[Controller] Creating MQTT client instance...")
                # Specify protocol v3.1.1 explicitly if needed, or let default handle it
                self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)

                # Assign callbacks
                self.mqtt_client.on_connect = self._on_mqtt_connect
                self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
                self.mqtt_client.on_message = self._on_mqtt_message
                self.mqtt_client.on_publish = self._on_mqtt_publish
                self.mqtt_client.on_subscribe = self._on_mqtt_subscribe

                # Set username/password if provided
                if mqtt_user:
                    print(f"[Controller] Setting MQTT username: {mqtt_user}")
                    self.mqtt_client.username_pw_set(mqtt_user, mqtt_pass)

                print(f"[Controller] Connecting MQTT client to {mqtt_server}:{mqtt_port}...")

                self.mqtt_client.connect_async(mqtt_server, mqtt_port, 60)

                print("[Controller] Starting MQTT network loop...")
                self.mqtt_client.loop_start()

            except Exception as e:
                print(f"[Controller] CRITICAL ERROR initializing MQTT client: {e}")
                traceback.print_exc()
                if not self._connection_error_shown:
                     QMessageBox.critical(None, "MQTT Error", f"Failed MQTT client init:\n{e}")
                     self._connection_error_shown = True
                self.mqtt_client = None

        else:
            print("[Controller] MQTT not configured.")
            self.mqtt_client = None

        # --- Initialize Meshtastic Handler (Existing Logic) ---
        mesh_type = settings.get('mesh_conn_type', 'None')
        if mesh_type != 'None':
            print("[Controller] Meshtastic connection required. Initializing handler...")
            if self.meshtastic_handler:
                print("[Controller] Disconnecting existing handler first...")
                self._disconnect_mesh_handler()
                QTimer.singleShot(200, lambda: self._create_and_connect_meshtastic(settings))
            else:
                self._create_and_connect_meshtastic(settings)
        else:
            print("[Controller] Meshtastic connection type is 'None'.");
            self.meshtastic_handler = None

            if not settings.get('server'):
                print("[Controller] No connections configured.")
                if not self._connection_error_shown:
                     QMessageBox.critical(None, "Connection Error", "No connection configured.\nPlease use Setup.")
                     self._connection_error_shown = True
                self.show_login_window(); return


        if mesh_type != 'None' or settings.get('server'):

             if not self.buddy_list_window:
                  print("[Controller] Connections initiated, showing buddy list window...")

                  QTimer.singleShot(100, self.show_buddy_list)
             else:
                  print("[Controller] Connections initiated, buddy list already open.")


        print("[Controller] connect_services finished initiating connections.")

    def _create_and_connect_meshtastic(self, settings):
         print("[Controller] _create_and_connect_meshtastic CALLED.")
         if self.meshtastic_handler:
              print("[Controller] Warning: Meshtastic handler already exists in _create_and_connect... - skipping creation")
              return

         try:
             print("[Controller] Creating Handler...");
             self.meshtastic_handler = MeshtasticHandler(settings)
             print("[Controller] Connecting Handler signals...");
             self.meshtastic_handler.connection_status.connect(self.handle_meshtastic_connection_status)
             self.meshtastic_handler.message_received.connect(self.route_incoming_message_from_mesh)
             self.meshtastic_handler.node_list_updated.connect(self._handle_node_list_update)
             self.meshtastic_handler._connection_established_signal.connect(self._start_delayed_node_list_request)
             # ************************************************
             print("[Controller] Calling handler.connect_to_device()...");
             connect_initiated = self.meshtastic_handler.connect_to_device()
             print(f"[Controller] connect_to_device initiated: {connect_initiated}")
         except Exception as e:
              import traceback
              print(f"[Controller] CRITICAL ERROR creating/connecting Handler: {e}")
              traceback.print_exc()
              if not self._connection_error_shown:
                   QMessageBox.critical(None, "Meshtastic Error", f"Failed handler init:\n{e}")
                   self._connection_error_shown = True
              self.meshtastic_handler = None
              self.show_login_window()

    @Slot()
    def handle_sign_off(self):
        """Handles user request to sign off."""
        print("[Controller] handle_sign_off CALLED.")
        self._signing_off = True
        self._disconnect_services()

        if self.buddy_list_window:
            print("[Controller] Closing buddy list window during sign off.")
            self.buddy_list_window.close()
            self.buddy_list_window = None
        self.connection_settings = {} 
        print("[Controller] Showing login window after sign off.")
        print("[Controller] Playing sign-off sound.")
        play_sound_async("signoff.wav")
        self.show_login_window()

    def _disconnect_mesh_handler(self):
        print("[Controller] Stopping periodic node update timer.")
        self.node_update_timer.stop() # Stop the timer
        self._node_list_timer_active = False
        if self.meshtastic_handler:
            print("[Controller] Disconnecting Meshtastic handler signals...")
            # ... (disconnect signals as before) ...
            try:
                 if hasattr(self,'_start_delayed_node_list_request') and self._start_delayed_node_list_request:
                      self.meshtastic_handler._connection_established_signal.disconnect(self._start_delayed_node_list_request)
            except (TypeError, RuntimeError) as e: print(f"  - Warn: Error disconnecting _connection_established_signal: {e}")
            try:
                 if hasattr(self,'handle_meshtastic_connection_status') and self.handle_meshtastic_connection_status:
                     self.meshtastic_handler.connection_status.disconnect(self.handle_meshtastic_connection_status)
            except (TypeError, RuntimeError) as e: print(f"  - Warn: Error disconnecting connection_status: {e}")
            try:
                 if hasattr(self,'route_incoming_message_from_mesh') and self.route_incoming_message_from_mesh:
                     self.meshtastic_handler.message_received.disconnect(self.route_incoming_message_from_mesh)
            except (TypeError, RuntimeError) as e: print(f"  - Warn: Error disconnecting message_received: {e}")
            try:
                 if hasattr(self,'_handle_node_list_update') and self._handle_node_list_update:
                     self.meshtastic_handler.node_list_updated.disconnect(self._handle_node_list_update)
            except (TypeError, RuntimeError) as e: print(f"  - Warn: Error disconnecting node_list_updated: {e}")

            print("[Controller] Calling handler.disconnect()...")
            self.meshtastic_handler.disconnect()
            self.meshtastic_handler = None
            print("[Controller] Meshtastic handler disconnected and cleared.")

    def _disconnect_services(self):
        """Disconnects MQTT and Meshtastic."""
        print("[Controller] _disconnect_services CALLED.")
        self._disconnect_mesh_handler() # Disconnect Meshtastic first

        # --- Disconnect MQTT ---
        if self.mqtt_client:
            print("[Controller] Disconnecting MQTT client...")
            try:
                # Stop the network loop first
                print("[Controller] Stopping MQTT loop...")
                self.mqtt_client.loop_stop()
                # Perform clean disconnect
                print("[Controller] Sending MQTT disconnect...")
                self.mqtt_client.disconnect()
                print("[Controller] MQTT client disconnected method called.")
            except Exception as e:
                print(f"[Controller] Error during MQTT disconnect: {e}")
                traceback.print_exc()
            finally:
                try:
                    self.mqtt_client.on_connect = None
                    self.mqtt_client.on_disconnect = None
                    self.mqtt_client.on_message = None
                    self.mqtt_client.on_publish = None
                    self.mqtt_client.on_subscribe = None
                except: pass
                self.mqtt_client = None
                print("[Controller] MQTT client instance cleared.")
        else:
             print("[Controller] No active MQTT client instance to disconnect.")
        # ---------------------

        print("[Controller] _disconnect_services finished.")

    @Slot(bool, str)
    def handle_meshtastic_connection_status(self, connected, message):
        """Handles connection status updates from MeshtasticHandler."""
        print(f"[Controller] handle_meshtastic_connection_status CALLED: connected={connected}, message='{message}'")

        if self._signing_off:
            print("[Controller] Ignoring connection status update during sign off.")
            return

        if connected:
            print("[Controller] Status is CONNECTED.")
            self._connection_error_shown = False
            if not self.buddy_list_window:
                print("[Controller] Showing buddy list window.")
                self.show_buddy_list()

            else:
                print("[Controller] Buddy list window already open.")
                if self.buddy_list_window:
                    self.buddy_list_window.statusBar().showMessage("Meshtastic: Connected", 5000)
        else:

            print(f"[Controller] Status is DISCONNECTED/FAILED. Message: {message}")

            if self.buddy_list_window:
                print("[Controller] Closing buddy list window due to disconnection/failure.")
                self.buddy_list_window.close()
                self.buddy_list_window = None


            if not self._signing_off and not self._connection_error_shown:
                print("[Controller] Showing connection failed/lost error message.")
                QMessageBox.warning(None,"Connection Failed",f"Mesh connection failed or lost:\n{message}")
                self._connection_error_shown = True

            self._disconnect_mesh_handler()

            if not self._signing_off:
                print("[Controller] Showing login window due to connection failure/loss.")
                self.show_login_window()


    @Slot(list)
    def _handle_node_list_update(self, nodes_list):

        if self.buddy_list_window:
            self.buddy_list_window.handle_node_list_update(nodes_list)

    def show_buddy_list(self):

        if self.buddy_list_window: return
        print("[Controller] show_buddy_list CALLED.")
        if not self.connection_settings: print("[Controller] Error: No connection settings."); self.show_login_window(); return
        
        screen_name = self.connection_settings.get("screen_name", "Unknown")
        print(f"[Controller] Creating BuddyListWindow for '{screen_name}'...");

        self.buddy_list_window = BuddyListWindow(
            screen_name=screen_name,
            connection_settings=self.connection_settings,
            app_config=self.current_config
        )
        print("[Controller] Connecting buddy list signals...")

        self.buddy_list_window.config_updated.connect(self.handle_config_updated)
        self.buddy_list_window.sign_off_requested.connect(self.handle_sign_off)
        self.buddy_list_window.send_message_requested.connect(self.handle_send_request)

        self.buddy_list_window.destroyed.connect(self._buddy_list_destroyed)

        print("[Controller] Showing buddy list window."); self.buddy_list_window.show()

        QTimer.singleShot(100, lambda: self.buddy_list_window.statusBar().showMessage("Meshtastic: Connected", 5000) if self.buddy_list_window else None)


    @Slot()
    def _buddy_list_destroyed(self):

        print("[Controller] Buddy list window destroyed.")
        self.buddy_list_window = None

        if not self.login_window or not self.login_window.isVisible():
             if not self.settings_window or not self.settings_window.isVisible():
                   print("[Controller] Buddy list was last window, setting QuitOnLastWindowClosed(True).")
                   self.app.setQuitOnLastWindowClosed(True)


    @Slot(str, str, str)
    def route_incoming_message_from_mesh(self, sender_id, text, msg_type):

        if self.buddy_list_window:
             try:

                  self.buddy_list_window.handle_incoming_message(sender_id, text, 'meshtastic', msg_type)
             except Exception as e:
                  print(f"ERROR calling handle_incoming_message: {e}"); import traceback; traceback.print_exc()
        else: print("[Controller] Warning: Buddy list window not available for incoming Meshtastic message.")

    @Slot(str, str, str) # recipient_id, message_text, network_type
    def handle_send_request(self, recipient_id, message_text, network_type):
        """Handles request to send a message from UI."""
        print(f"[Controller] handle_send_request CALLED: Type={network_type}, To={recipient_id}")
        if network_type == 'meshtastic':
             # Check handler exists and thinks it's running
             if self.meshtastic_handler and self.meshtastic_handler.is_running:
                 print("[Controller] Forwarding send request to Meshtastic handler.")
                 self.meshtastic_handler.send_message(recipient_id, message_text)
             else:
                 print("[Controller] Warning: Cannot send Meshtastic message. Handler not available or not running.")
                 if self.buddy_list_window:
                     self.buddy_list_window.statusBar().showMessage("Error: Meshtastic not connected.", 3000)

        elif network_type == 'mqtt':
            # --- Start MQTT Logic ---
            if self.mqtt_client and self.mqtt_client.is_connected():
                try:
                    # Use the recipient_id as the topic for direct messages (adjust if needed)
                    target_topic = recipient_id
                    print(f"[Controller] Publishing MQTT message to topic '{target_topic}'")

                    # Publish the message
                    # adjust QoS (Quality of Service) - 0, 1, or 2
                    # Retain=False is usually correct for chat messages
                    result, mid = self.mqtt_client.publish(
                        topic=target_topic,
                        payload=message_text.encode('utf-8'), # Encode string to bytes
                        qos=1, # Example: QoS 1 (at least once delivery)
                        retain=False
                    )

                    if result == mqtt.MQTT_ERR_SUCCESS:
                        print(f"[Controller] MQTT message queued successfully (mid={mid}).")
                        if self.buddy_list_window:
                            self.buddy_list_window.statusBar().showMessage(f"IM sent to {recipient_id}", 2000)
                    else:
                        print(f"[Controller] Error: Failed to queue MQTT message (Error code: {result}).")
                        if self.buddy_list_window:
                            self.buddy_list_window.statusBar().showMessage(f"Error sending IM to {recipient_id}", 3000)

                except Exception as e:
                    print(f"[Controller] Exception during MQTT publish: {e}")
                    import traceback
                    traceback.print_exc()
                    if self.buddy_list_window:
                        self.buddy_list_window.statusBar().showMessage("Error sending IM.", 3000)
            else:
                print("[Controller] Warning: Cannot send MQTT message. Client not available or not connected.")
                if self.buddy_list_window:
                    self.buddy_list_window.statusBar().showMessage("Error: MQTT not connected.", 3000)
            # --- End MQTT Logic ---

        else:
             print(f"[Controller] Warning: Unknown network type '{network_type}' requested for send.")

    def cleanup(self):
        print("[Controller] cleanup CALLED.")
        print("[Controller] Stopping periodic node update timer during cleanup.")
        self.node_update_timer.stop() # Ensure timer stops on exit
        self._signing_off = True
        self._disconnect_services()
        print("[Controller] Cleanup finished.")
        self.app.setQuitOnLastWindowClosed(True)

# --- Main Execution ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    # **** Set Application Name **** It's important for QStandardPaths
    app.setApplicationName("MIMMeshtastic")
    app.setStyle("Fusion")

    # --- Font Loading ---
    font_dir = get_resource_path("helvetica")
    loaded_font_families = []
    if os.path.isdir(font_dir):
        print(f"Looking for fonts in: {font_dir}")
        for filename in os.listdir(font_dir):
            if filename.lower().endswith((".ttf", ".otf")):
                font_path = os.path.join(font_dir, filename)
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    if "Helvetica" in families and "Helvetica" not in loaded_font_families:
                        loaded_font_families.append("Helvetica")
                    print(f"Loaded font: {filename} (Families: {families})")
                else:
                    print(f"Warning: Failed to load font: {font_path}")
    else:
        print(f"Warning: Font directory not found: {font_dir}")
    default_font_family = "Helvetica" if "Helvetica" in loaded_font_families else "Arial"
    default_font_size = 9
    app.setFont(QFont(default_font_family, default_font_size))
    print(f"Default application font set to: {default_font_family} {default_font_size}pt")

    # --- Stylesheet Loading ---
    qss_path = get_resource_path("styles.qss")
    try:
        with open(qss_path, "r") as f:
            app.setStyleSheet(f.read())
        print(f"Stylesheet '{os.path.basename(qss_path)}' applied.")
    except FileNotFoundError:
        print(f"Warning: Stylesheet '{os.path.basename(qss_path)}' not found at '{qss_path}'.")
    except Exception as e:
        print(f"Error loading stylesheet from '{qss_path}': {e}")

    # --- Run Application ---
    print("Creating ApplicationController...")
    controller = ApplicationController(app)
    print("Connecting app.aboutToQuit signal...")
    app.aboutToQuit.connect(controller.cleanup)
    print("Starting Qt event loop (app.exec)...")
    exit_code = app.exec()
    print(f"Qt event loop finished with exit code: {exit_code}")
    sys.exit(exit_code)
