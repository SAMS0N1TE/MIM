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
NODE_UPDATE_INTERVAL_MS = 1 * 60 * 1000 # 1 minute (60,000 ms)
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
    mqtt_message_received_signal = Signal(str, str, str) # sender_id, text, msg_type (mqtt)

    def __init__(self, app: QApplication): # Added type hint for clarity
        super().__init__()
        self.app = app
        self.login_window = None
        self.buddy_list_window = None
        self.settings_window = None
        self.current_config = load_config() # Load config on startup
        self.connection_settings = {} # Runtime settings derived from current_config + password
        self.mqtt_client = None
        self.meshtastic_handler = None
        self._signing_off = False # Flag to prevent actions during sign-off
        self._connection_error_shown = False # Prevent multiple error popups
        self._node_list_initial_request_pending = False # Flag for initial mesh request delay

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
        print("[Controller] Initialized.")

        # Start the application by showing the login window
        self.show_login_window()

    def show_login_window(self):
        """Creates and shows the login window, ensuring a clean state."""
        print("[Controller] show_login_window CALLED.")
        self._signing_off = False # Reset sign-off flag
        self._connection_error_shown = False # Reset error flag
        self._disconnect_services() # Ensure clean state before showing login

        # Close other primary windows if open
        if self.buddy_list_window:
            print("[Controller] Closing existing buddy list window.")
            try: # Gracefully close
                 self.buddy_list_window.close()
            except Exception as e: print(f"Error closing buddy list window: {e}")
            self.buddy_list_window = None
        if self.settings_window:
             print("[Controller] Closing existing settings window.")
             try: # Gracefully close
                 self.settings_window.close()
             except Exception as e: print(f"Error closing settings window: {e}")
             self.settings_window = None

        # Make sure app quits if login window is the only one closed by user
        self.app.setQuitOnLastWindowClosed(True)
        print("[Controller] QuitOnLastWindowClosed set to True for Login Window.")

        # Load config and prepare login window state
        self.current_config = load_config() # Reload config in case it changed via Setup
        saved_screen_name = self.current_config.get("screen_name")
        # Ensure auto_login is strictly boolean
        raw_auto_login = self.current_config.get("auto_login", False) # Default to False
        saved_auto_login = raw_auto_login if isinstance(raw_auto_login, bool) else False

        print(f"[Controller] Creating LoginWindow (ScreenName: {saved_screen_name}, AutoLogin: {saved_auto_login})")
        self.login_window = LoginWindow(saved_screen_name, saved_auto_login)
        # Connect signals from login window
        self.login_window.setup_requested.connect(self.show_settings_window)
        self.login_window.sign_on_requested.connect(self.handle_sign_on_request)
        self.login_window.show()

    # --- MQTT Callback Methods --- (Ensure these run correctly) ---
    # Note: These callbacks are triggered by the Paho MQTT thread.
    # They emit signals to be handled safely in the main Qt thread.

    def _on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        """Callback for when MQTT client connects (Runs in MQTT Thread)."""
        print(f"[Controller MQTT CB] _on_mqtt_connect: Result code={rc}")
        if rc == 0:
            print("[Controller MQTT CB] MQTT Connected successfully.")
            my_topic = self.connection_settings.get("screen_name")
            if my_topic:
                try:
                    print(f"[Controller MQTT CB] Subscribing to MQTT topic: {my_topic}")
                    # Use QoS 1 for reliability
                    result, mid = self.mqtt_client.subscribe(my_topic, qos=1)
                    if result == mqtt.MQTT_ERR_SUCCESS:
                        print(f"[Controller MQTT CB] MQTT subscription request successful (mid={mid}).")
                        # Emit success signal only after successful critical subscription
                        self.mqtt_connection_updated.emit(True, "Connected")
                    else:
                        print(f"[Controller MQTT CB] Error: MQTT subscription failed (Code: {result}).")
                        self.mqtt_connection_updated.emit(False, f"Subscription failed (Code: {result})")
                except Exception as e:
                    print(f"[Controller MQTT CB] Exception during MQTT subscribe: {e}")
                    traceback.print_exc()
                    self.mqtt_connection_updated.emit(False, f"Exception during subscribe: {e}")
            else:
                print("[Controller MQTT CB] Warning: Cannot subscribe, screen_name not found.")
                self.mqtt_connection_updated.emit(False, "Cannot subscribe (no screen name)")
        else:
            error_string = mqtt.connack_string(rc)
            print(f"[Controller MQTT CB] Error: MQTT Connection failed: {error_string} (Code: {rc})")
            self.mqtt_connection_updated.emit(False, f"Connection failed: {error_string}")

    def _on_mqtt_disconnect(self, client, userdata, rc, properties=None):
        """Callback for when MQTT client disconnects (Runs in MQTT Thread)."""
        print(f"[Controller MQTT CB] _on_mqtt_disconnect: Result code={rc}")
        if rc == 0:
            print("[Controller MQTT CB] MQTT Disconnected cleanly.")
            if not self._signing_off: # Avoid signaling during intentional disconnect
                 self.mqtt_connection_updated.emit(False, "Disconnected")
        else:
            print(f"[Controller MQTT CB] Error: MQTT Unexpected disconnection (Code: {rc}).")
            # Signal unexpected disconnections
            if not self._signing_off:
                 self.mqtt_connection_updated.emit(False, f"Unexpected disconnection (Code: {rc})")

    def _on_mqtt_message(self, client, userdata, msg):
        """Callback for when an MQTT message is received (Runs in MQTT Thread)."""
        print(f"[Controller MQTT CB] _on_mqtt_message: Topic={msg.topic}")
        try:
            payload_str = msg.payload.decode("utf-8")
            print(f"[Controller MQTT CB] MQTT Received Raw: '{payload_str}'")

            sender_id = msg.topic # Assuming topic is the sender for direct messages
            message_text = payload_str
            msg_type = 'direct' # Assume direct based on topic subscription

            # Emit signal to safely route message to UI thread
            self.mqtt_message_received_signal.emit(sender_id, message_text, msg_type)

        except UnicodeDecodeError:
             print(f"[Controller MQTT CB] Error: Could not decode MQTT payload as UTF-8 on topic {msg.topic}")
        except Exception as e:
            print(f"[Controller MQTT CB] Error processing incoming MQTT message: {e}")
            traceback.print_exc()

    def _on_mqtt_publish(self, client, userdata, mid):
        """Optional callback: Message with QoS > 0 sent (Runs in MQTT Thread)."""
        print(f"[Controller MQTT CB] _on_mqtt_publish: Confirmed publish for mid={mid}")

    def _on_mqtt_subscribe(self, client, userdata, mid, granted_qos, properties=None):
        """Optional callback: Broker confirmed subscription (Runs in MQTT Thread)."""
        print(f"[Controller MQTT CB] _on_mqtt_subscribe: Confirmed subscription mid={mid}, QoS={granted_qos}")

    # --- Meshtastic Related Slots (Triggered by Signals from Handler or Timers) ---

    @Slot()
    def _start_initial_node_list_request(self):
        """Requests the *initial* node list after connection establishment."""
        if not self.meshtastic_handler or not self.meshtastic_handler.is_running:
             print("[Controller] Skipping initial node list request (handler not ready).")
             return

        print(f"[Controller] Requesting *initial* node list...")
        # Immediately request the list
        self.meshtastic_handler.request_node_list()
        # Start the PERIODIC timer *after* the initial request
        print(f"[Controller] Starting periodic node update timer ({NODE_UPDATE_INTERVAL_MS} ms interval).")
        self.node_update_timer.start(NODE_UPDATE_INTERVAL_MS)


    @Slot()
    def _request_periodic_node_update(self):
        """Slot called by the periodic timer to request node updates."""
        # print("[Controller] Periodic timer timeout.") # Can be noisy, uncomment if needed
        if self.meshtastic_handler and self.meshtastic_handler.is_running:
            # print("[Controller] Requesting periodic node list update from handler.")
            self.meshtastic_handler.request_node_list()
        # else:
            # print("[Controller] Skipping periodic node update (handler not available or not running).")

    @Slot()
    def show_settings_window(self):
        """Shows the settings/setup window."""
        print("[Controller] show_settings_window CALLED.")

        if self.settings_window is None:
            print("[Controller] Creating new SettingsWindow.")
            # Determine parent: should be the window that requested it (login or buddy list)
            parent = None
            if self.login_window and self.login_window.isVisible():
                 parent = self.login_window
            elif self.buddy_list_window and self.buddy_list_window.isVisible():
                 parent = self.buddy_list_window
            print(f"[Controller] Setting parent for SettingsWindow: {parent}")

            # Keep app alive if settings are opened without other main windows visible
            if not parent:
                 print("[Controller] No parent window found, setting QuitOnLastWindowClosed(False).")
                 self.app.setQuitOnLastWindowClosed(False)

            self.settings_window = SettingsWindow(self.current_config, parent=parent)
            print(f"[Controller] SettingsWindow instance created: {self.settings_window}")
            self.settings_window.settings_saved.connect(self.handle_settings_saved)
            self.settings_window.finished.connect(self._settings_window_closed) # Clean up reference
            self.settings_window.show()
            print("[Controller] Called show() on new SettingsWindow.")
        else:
            # If instance somehow already exists, just bring it to front
            print("[Controller] SettingsWindow exists. Activating existing window.")
            self.settings_window.activateWindow()
            self.settings_window.raise_() # Ensure it's on top

    @Slot(int)
    def _settings_window_closed(self, result):
        """Slot connected to finished signal of settings window for cleanup."""
        print(f"[Controller] _settings_window_closed CALLED with result: {result}")

        # Restore Quit setting ONLY if no other primary windows are visible
        login_vis = self.login_window and self.login_window.isVisible()
        buddy_vis = self.buddy_list_window and self.buddy_list_window.isVisible()
        print(f"[Controller] Window visibility check: Login={login_vis}, BuddyList={buddy_vis}")

        if not login_vis and not buddy_vis:
             print("[Controller] No other primary windows open, setting QuitOnLastWindowClosed(True).")
             self.app.setQuitOnLastWindowClosed(True)
        # else:
             # print("[Controller] Primary window still open, QuitOnLastWindowClosed unchanged.")

        # Clear the reference to the window instance
        print(f"[Controller] Clearing SettingsWindow reference (was {self.settings_window}).")
        self.settings_window = None


    @Slot(dict)
    def handle_settings_saved(self, new_settings):
        """Handles the settings_saved signal from the SettingsWindow."""
        print("[Controller] handle_settings_saved CALLED.")
        self.current_config.update(new_settings) # Update internal config copy

        # Update sound state immediately
        new_sound_state = self.current_config.get("sounds_enabled", True)
        set_sounds_enabled(new_sound_state)

        saved_ok = save_config(self.current_config) # Attempt to save the merged config

        if saved_ok:
            print("[Controller] Config successfully saved via handle_settings_saved.")
            # Update login window screen name if it's currently open
            if self.login_window and self.login_window.isVisible():
                 new_name = new_settings.get("screen_name", "")
                 print(f"[Controller] Updating login window screen name field to: '{new_name}'")
                 self.login_window.screen_name_input.setText(new_name)
            # Show confirmation message (use settings_window as parent if possible)
            parent_widget = self.settings_window if self.settings_window else QApplication.activeWindow()
            QMessageBox.information(parent_widget, "Settings Saved", "Your settings have been saved.")
        else:
            print("[Controller] ERROR: Failed to save config via handle_settings_saved.")
            # Show error message (use settings_window as parent if possible)
            parent_widget = self.settings_window if self.settings_window else QApplication.activeWindow()
            QMessageBox.warning(parent_widget, "Save Error", "Could not save settings to configuration file.")


    @Slot(dict)
    def handle_config_updated(self, updated_config):
        """Saves the configuration when updated by other windows (like buddy list)."""
        print("[Controller] handle_config_updated CALLED.")
        if updated_config:
            # Update sound state
            new_sound_state = updated_config.get("sounds_enabled", True)
            set_sounds_enabled(new_sound_state)

            if save_config(updated_config):
                print("[Controller] Config successfully saved via handle_config_updated.")
                self.current_config = updated_config.copy() # Update controller's copy
            else:
                print("[Controller] ERROR: Failed to save config via handle_config_updated.")
                # Show error on buddy list window if possible
                if self.buddy_list_window and self.buddy_list_window.isVisible():
                    QMessageBox.warning(self.buddy_list_window, "Save Error", "Could not save updated settings.")
        else:
             print("[Controller] Warning: handle_config_updated received empty config.")

    @Slot(str, str, bool) # screen_name, password, auto_login
    def handle_sign_on_request(self, screen_name, password, auto_login):
        """Handles the request to sign on from the LoginWindow."""
        print(f"[Controller] handle_sign_on_request CALLED for: '{screen_name}', AutoLogin={auto_login}")
        self._connection_error_shown = False # Reset error flag for new attempt
        if not screen_name:
             QMessageBox.warning(self.login_window, "Sign On Error", "Screen Name cannot be empty.")
             return

        # Verify screen name matches current config (simple single-profile check)
        config_screen_name = self.current_config.get("screen_name")
        if config_screen_name != screen_name:
            QMessageBox.warning(self.login_window,"Sign On Error",f"Configured Screen Name is '{config_screen_name}'.\nCannot sign on as '{screen_name}'.\nPlease use 'Setup' or enter the correct name.")
            return

        # Use the current, validated config
        config_to_use = self.current_config
        print(f"[Controller] Using configuration for '{screen_name}'.")

        # Check Meshtastic config details are sufficient
        mesh_type = config_to_use.get("mesh_conn_type","None")
        mesh_details = config_to_use.get("mesh_details","")
        if mesh_type != "None" and not mesh_details:
            QMessageBox.warning(self.login_window, "Config Incomplete", f"Meshtastic connection details for '{screen_name}' missing.\nPlease use 'Setup'.")
            return

        # Check MQTT config (only if server is specified)
        mqtt_server = config_to_use.get("server")
        mqtt_user = config_to_use.get("username")
        mqtt_needs_pass = bool(mqtt_user) # Require password only if username is set

        if mqtt_server and mqtt_needs_pass and not password:
            # Password is provided via login window input, not saved config
            QMessageBox.warning(self.login_window, "Sign On Error", f"Password required for MQTT user '{mqtt_user}'.")
            return

        # Update auto_login preference if "Save Config" checkbox was checked in login window
        should_save_prefs = False
        if self.login_window and self.login_window.get_save_config_preference():
             if self.current_config.get("auto_login") != auto_login:
                 self.current_config["auto_login"] = auto_login
                 should_save_prefs = True
                 print(f"[Controller] Auto-login preference updated in config: {auto_login}")
        if should_save_prefs:
            print("[Controller] Saving config due to changed preferences...")
            save_config(self.current_config) # Save the updated preference

        # Prepare runtime settings and proceed
        self.connection_settings = config_to_use.copy()
        self.connection_settings['password'] = password # Add the provided password for runtime
        print("[Controller] Proceeding to connect_services().")

        # Show some status in login window before closing it
        if self.login_window:
            self.login_window.setWindowTitle("Connecting...")
            QApplication.processEvents() # Allow UI update

        self.connect_services()

    # --- Slots for Handling Connection Events in UI Thread ---

    @Slot(bool, str)
    def _handle_mqtt_connection_update(self, connected, message):
        """Handles MQTT connection status changes in the main Qt thread."""
        print(f"[Controller UI] _handle_mqtt_connection_update: connected={connected}, message='{message}'")

        if self._signing_off:
            print("[Controller UI] Ignoring MQTT connection update during sign off.")
            return

        if connected:
            self._connection_error_shown = False # Reset error flag on success
            if self.buddy_list_window:
                self.buddy_list_window.statusBar().showMessage(f"MQTT: {message}", 5000)
        else:
            # Handle MQTT disconnection or failure
            print(f"[Controller UI] MQTT connection failed or lost: {message}")

            # Show error only if not signing off and not already shown
            if not self._signing_off and not self._connection_error_shown:
                # Check if Meshtastic is also disconnected (or not configured)
                meshtastic_connected = self.meshtastic_handler and self.meshtastic_handler.is_running
                mqtt_was_configured = self.connection_settings.get("server")

                # Show critical error ONLY if MQTT was configured AND Meshtastic isn't running
                # Or if MQTT was the *only* configured connection
                show_critical_error = mqtt_was_configured and (not meshtastic_connected or self.connection_settings.get("mesh_conn_type", "None") == "None")

                if show_critical_error:
                    print("[Controller UI] Showing MQTT connection failed/lost error message.")
                    QMessageBox.warning(self.login_window or self.buddy_list_window or None, # Try to find a parent
                                       "MQTT Connection Failed",
                                       f"MQTT connection failed or lost:\n{message}")
                    self._connection_error_shown = True # Mark error as shown
                    # If buddy list is open, close it and show login
                    if self.buddy_list_window:
                         self.handle_sign_off() # Use sign off logic to clean up and show login
                    elif not self.login_window: # If login isn't already showing
                         self.show_login_window()
                elif self.buddy_list_window: # If Meshtastic is still running, just show status bar message
                     self.buddy_list_window.statusBar().showMessage(f"MQTT Error: {message}", 5000)

            # Clean up MQTT client resources if disconnected unexpectedly
            if self.mqtt_client:
                 # Check if disconnect was unexpected (rc != 0 in callback -> message reflects error)
                 if "failed" in message.lower() or "unexpected" in message.lower():
                      print("[Controller UI] Cleaning up MQTT client due to unexpected disconnect.")
                      try:
                          self.mqtt_client.loop_stop()
                          # No need to call disconnect() again if it was unexpected
                      except Exception as e: print(f"Error stopping MQTT loop: {e}")
                      self.mqtt_client = None


    @Slot(str, str, str) # sender_id, text, msg_type
    def _route_incoming_mqtt_message(self, sender_id, text, msg_type):
        """Routes incoming MQTT messages (received via signal) to the buddy list (UI Thread)."""
        # print(f"[Controller UI] _route_incoming_mqtt_message: From={sender_id}") # Can be noisy
        if self.buddy_list_window:
             try:
                  self.buddy_list_window.handle_incoming_message(sender_id, text, 'mqtt', msg_type)
             except Exception as e:
                  print(f"ERROR calling handle_incoming_message for MQTT: {e}"); traceback.print_exc()
        # else:
             # print("[Controller UI] Warning: Buddy list window not available for incoming MQTT message.")

    def connect_services(self):
        """Initiates connections based on self.connection_settings."""
        print("[Controller] connect_services CALLED.")
        settings = self.connection_settings
        if not settings:
            print("[Controller] Error: connect_services called with empty runtime settings.")
            self.show_login_window(); return

        print(f"[Controller] Runtime Settings for Connection: {settings}")

        # Keep app running even if login window is closed now
        self.app.setQuitOnLastWindowClosed(False)
        print("[Controller] QuitOnLastWindowClosed set to False during connection.")

        # Close login window if it's still open
        if self.login_window:
            print("[Controller] Closing login window.")
            self.login_window.close_window(); self.login_window = None

        # --- Show Buddy List Window Immediately (will show status) ---
        # Don't wait for connections, show it now to give feedback
        if not self.buddy_list_window:
            print("[Controller] Showing buddy list window (status will update)...")
            self.show_buddy_list() # Create and show the window
            if self.buddy_list_window: # Check if creation succeeded
                 self.buddy_list_window.statusBar().showMessage("Connecting...", 0) # Persistent message
                 QApplication.processEvents() # Update UI

        # --- Initialize MQTT (if configured) ---
        if settings.get('server'):
            mqtt_server = settings['server']
            mqtt_port = settings.get('port', 1883)
            mqtt_user = settings.get('username')
            mqtt_pass = settings.get('password') # Password from login window

            print(f"[Controller] MQTT Configured: Server={mqtt_server}:{mqtt_port}, User={mqtt_user}")

            if self.mqtt_client:
                 print("[Controller] Disconnecting existing MQTT client first...")
                 self._disconnect_mqtt_client() # Use helper

            try:
                print("[Controller] Creating MQTT client instance...")
                # Specify protocol v3.1.1 explicitly if needed, or let default handle it
                self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)

                # Assign callbacks (these run in MQTT thread)
                self.mqtt_client.on_connect = self._on_mqtt_connect
                self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
                self.mqtt_client.on_message = self._on_mqtt_message
                self.mqtt_client.on_publish = self._on_mqtt_publish
                self.mqtt_client.on_subscribe = self._on_mqtt_subscribe

                # Set username/password if provided
                if mqtt_user:
                    print(f"[Controller] Setting MQTT username: {mqtt_user}")
                    self.mqtt_client.username_pw_set(mqtt_user, mqtt_pass)

                print(f"[Controller] Connecting MQTT client to {mqtt_server}:{mqtt_port} asynchronously...")
                # Connect asynchronously, callbacks will handle status
                self.mqtt_client.connect_async(mqtt_server, mqtt_port, 60)

                print("[Controller] Starting MQTT network loop in background thread...")
                self.mqtt_client.loop_start()

            except Exception as e:
                print(f"[Controller] CRITICAL ERROR initializing MQTT client: {e}")
                traceback.print_exc()
                # Show error immediately as this is a setup failure
                if not self._connection_error_shown:
                     # Use buddy list window as parent if available
                     parent = self.buddy_list_window or None
                     QMessageBox.critical(parent, "MQTT Setup Error", f"Failed to initialize MQTT client:\n{e}")
                     self._connection_error_shown = True
                self._disconnect_mqtt_client() # Clean up partially created client

        else:
            print("[Controller] MQTT not configured.")
            self.mqtt_client = None # Ensure it's None

        # --- Initialize Meshtastic Handler (if configured) ---
        mesh_type = settings.get('mesh_conn_type', 'None')
        if mesh_type != 'None':
            print("[Controller] Meshtastic connection required. Initializing handler...")
            if self.meshtastic_handler:
                print("[Controller] Disconnecting existing Meshtastic handler first...")
                self._disconnect_mesh_handler()
                # Add a small delay before creating the new one to ensure cleanup
                QTimer.singleShot(250, lambda: self._create_and_connect_meshtastic(settings))
            else:
                self._create_and_connect_meshtastic(settings)
        else:
            print("[Controller] Meshtastic connection type is 'None'.");
            self.meshtastic_handler = None # Ensure it's None

            # Check if *no* connections were configured at all
            if not settings.get('server'): # No MQTT either
                print("[Controller] Error: No connections configured.")
                if not self._connection_error_shown:
                     parent = self.buddy_list_window or None
                     QMessageBox.critical(parent, "Connection Error", "No Meshtastic or MQTT connection configured.\nPlease use Setup.")
                     self._connection_error_shown = True
                self.handle_sign_off() # Go back to login screen
                return # Stop further processing

        # Buddy list should already be visible showing "Connecting..."
        print("[Controller] connect_services finished initiating connections.")


    def _create_and_connect_meshtastic(self, settings):
         """Creates, connects signals, and initiates Meshtastic handler connection."""
         print("[Controller] _create_and_connect_meshtastic CALLED.")
         if self.meshtastic_handler:
              print("[Controller] Warning: Meshtastic handler already exists in _create_and_connect... - disconnecting first.")
              self._disconnect_mesh_handler() # Ensure clean state

         try:
             print("[Controller] Creating Meshtastic Handler...");
             self.meshtastic_handler = MeshtasticHandler(settings) # Pass runtime settings
             print("[Controller] Connecting Meshtastic Handler signals...");
             # Connect signals from the handler to controller slots
             self.meshtastic_handler.connection_status.connect(self.handle_meshtastic_connection_status)
             self.meshtastic_handler.message_received.connect(self.route_incoming_message_from_mesh)
             self.meshtastic_handler.node_list_updated.connect(self._handle_node_list_update)
             # Signal emitted by handler *after* pubsub reports established connection
             self.meshtastic_handler._connection_established_signal.connect(self._start_initial_node_list_request)
             # --- End Signal Connections ---

             print("[Controller] Calling handler.connect_to_device()...");
             # connect_to_device now primarily sets up the interface object
             # The actual connection happens via pubsub events
             connect_initiated = self.meshtastic_handler.connect_to_device()
             print(f"[Controller] Meshtastic connect_to_device setup initiated: {connect_initiated}")
             if not connect_initiated and self.meshtastic_handler:
                  # If connect_to_device failed synchronously (e.g., bad path), handle it
                  print("[Controller] Meshtastic synchronous connection setup failed.")
                  # Status might have already been emitted by handler, but ensure cleanup
                  self.handle_meshtastic_connection_status(False, "Initial connection setup failed")

         except Exception as e:
              print(f"[Controller] CRITICAL ERROR creating/connecting Meshtastic Handler: {e}")
              traceback.print_exc()
              if not self._connection_error_shown:
                   parent = self.buddy_list_window or None
                   QMessageBox.critical(parent, "Meshtastic Error", f"Failed Meshtastic handler initialization:\n{e}")
                   self._connection_error_shown = True
              self._disconnect_mesh_handler() # Clean up
              # If MQTT is also not working/configured, go back to login
              if not self.mqtt_client or not self.mqtt_client.is_connected():
                    self.handle_sign_off()


    @Slot()
    def handle_sign_off(self):
        """Handles user request to sign off."""
        print("[Controller] handle_sign_off CALLED.")
        self._signing_off = True # Set flag to prevent race conditions
        self._disconnect_services() # Disconnect MQTT and Meshtastic

        # Close buddy list window
        if self.buddy_list_window:
            print("[Controller] Closing buddy list window during sign off.")
            try:
                self.buddy_list_window.close()
            except Exception as e: print(f"Error closing buddy list window: {e}")
            self.buddy_list_window = None

        self.connection_settings = {} # Clear runtime settings
        print("[Controller] Showing login window after sign off.")

        # Play sign-off sound if sounds are enabled
        if self.current_config.get("sounds_enabled", True):
            print("[Controller] Playing sign-off sound.")
            play_sound_async("signoff.wav") # Assuming signoff.wav exists

        self.show_login_window() # Go back to login screen

    # --- Helper Disconnect Methods ---
    def _disconnect_mesh_handler(self):
        """Safely disconnects and cleans up the Meshtastic handler."""
        print("[Controller] Disconnecting Meshtastic Handler...")
        if self.node_update_timer.isActive():
             print("[Controller] Stopping periodic node update timer.")
             self.node_update_timer.stop() # Stop the timer

        if self.meshtastic_handler:
            handler = self.meshtastic_handler
            self.meshtastic_handler = None # Clear reference immediately

            print("[Controller] Disconnecting Meshtastic handler signals...")
            try: handler._connection_established_signal.disconnect(self._start_initial_node_list_request)
            except (TypeError, RuntimeError): pass # Ignore if already disconnected
            try: handler.connection_status.disconnect(self.handle_meshtastic_connection_status)
            except (TypeError, RuntimeError): pass
            try: handler.message_received.disconnect(self.route_incoming_message_from_mesh)
            except (TypeError, RuntimeError): pass
            try: handler.node_list_updated.disconnect(self._handle_node_list_update)
            except (TypeError, RuntimeError): pass

            print("[Controller] Calling handler.disconnect()...")
            try:
                 handler.disconnect() # Tell handler to clean up pubsub/interface
            except Exception as e: print(f"Error during handler disconnect: {e}")
            print("[Controller] Meshtastic handler disconnected.")
        else:
             print("[Controller] No active Meshtastic handler to disconnect.")


    def _disconnect_mqtt_client(self):
        """Safely disconnects and cleans up the MQTT client."""
        print("[Controller] Disconnecting MQTT client...")
        if self.mqtt_client:
            client = self.mqtt_client
            self.mqtt_client = None # Clear reference immediately
            try:
                print("[Controller] Stopping MQTT loop...")
                client.loop_stop()
                print("[Controller] Sending MQTT disconnect...")
                client.disconnect() # Clean disconnect
                # Clear callbacks to prevent issues if disconnect takes time
                client.on_connect = None
                client.on_disconnect = None
                client.on_message = None
                client.on_publish = None
                client.on_subscribe = None
                print("[Controller] MQTT client disconnected.")
            except Exception as e:
                print(f"[Controller] Error during MQTT disconnect/cleanup: {e}")
                traceback.print_exc()
        else:
             print("[Controller] No active MQTT client to disconnect.")


    def _disconnect_services(self):
        """Disconnects MQTT and Meshtastic services."""
        print("[Controller] _disconnect_services CALLED.")
        self._disconnect_mesh_handler() # Disconnect Meshtastic first
        self._disconnect_mqtt_client() # Then disconnect MQTT
        print("[Controller] _disconnect_services finished.")


    @Slot(bool, str)
    def handle_meshtastic_connection_status(self, connected, message):
        """Handles connection status updates from MeshtasticHandler (UI Thread)."""
        print(f"[Controller UI] handle_meshtastic_connection_status: connected={connected}, message='{message}'")

        if self._signing_off:
            print("[Controller UI] Ignoring Meshtastic connection status update during sign off.")
            return

        if connected:
            print("[Controller UI] Meshtastic Status: CONNECTED.")
            self._connection_error_shown = False # Reset error flag
            if not self.buddy_list_window:
                print("[Controller UI] Meshtastic connected, showing buddy list window.")
                self.show_buddy_list() # Show window if not already visible
            else:
                print("[Controller UI] Meshtastic connected, buddy list window already open.")
                self.buddy_list_window.statusBar().showMessage("Meshtastic: Connected", 5000)
                # Note: Initial node list request is now triggered by _connection_established_signal
        else:
            # Handle Meshtastic disconnection or failure
            print(f"[Controller UI] Meshtastic Status: DISCONNECTED/FAILED. Message: {message}")

            # Stop node update timer if it was running
            if self.node_update_timer.isActive():
                 print("[Controller UI] Stopping periodic node update timer due to disconnect.")
                 self.node_update_timer.stop()

            # Show error message only if not signing off and not already shown
            if not self._signing_off and not self._connection_error_shown:
                # Check if MQTT is also disconnected (or not configured)
                mqtt_connected = self.mqtt_client and self.mqtt_client.is_connected()
                mesh_was_configured = self.connection_settings.get("mesh_conn_type", "None") != "None"

                 # Show critical error ONLY if Mesh was configured AND MQTT isn't running
                 # Or if Mesh was the *only* configured connection
                show_critical_error = mesh_was_configured and (not mqtt_connected or not self.connection_settings.get("server"))

                if show_critical_error:
                     print("[Controller UI] Showing Meshtastic connection failed/lost error message.")
                     QMessageBox.warning(self.buddy_list_window or self.login_window or None, # Try to find parent
                                        "Meshtastic Connection Failed",
                                        f"Meshtastic connection failed or lost:\n{message}")
                     self._connection_error_shown = True # Mark error as shown
                     # Clean up and show login screen
                     self.handle_sign_off()
                elif self.buddy_list_window: # If MQTT is still okay, just show status
                     self.buddy_list_window.statusBar().showMessage(f"Meshtastic Error: {message}", 5000)

            # Ensure handler is cleaned up even if disconnect was triggered elsewhere
            self._disconnect_mesh_handler()



    @Slot(list)
    def _handle_node_list_update(self, nodes_list):
        """Receives node list update from handler and passes to buddy list (UI Thread)."""
        # print(f"[Controller UI] _handle_node_list_update: Received {len(nodes_list)} nodes.") # Noisy
        if self.buddy_list_window:
            self.buddy_list_window.handle_node_list_update(nodes_list)
        # else:
            # print("[Controller UI] Warning: Buddy list window not available for node update.")

    def show_buddy_list(self):
        """Creates and shows the BuddyListWindow."""
        if self.buddy_list_window: # Prevent creating multiple instances
             print("[Controller] Buddy list window already exists.")
             self.buddy_list_window.activateWindow()
             self.buddy_list_window.raise_()
             return

        print("[Controller] show_buddy_list CALLED.")
        if not self.connection_settings:
            print("[Controller] Error: Cannot show buddy list, no runtime connection settings.")
            self.handle_sign_off() # Go back to login
            return

        screen_name = self.connection_settings.get("screen_name", "Unknown")
        print(f"[Controller] Creating BuddyListWindow for '{screen_name}'...");

        try:
            self.buddy_list_window = BuddyListWindow(
                screen_name=screen_name,
                connection_settings=self.connection_settings,
                app_config=self.current_config # Pass current full config
            )
            print("[Controller] Connecting buddy list signals...")
            # Connect signals FROM buddy list TO controller slots
            self.buddy_list_window.config_updated.connect(self.handle_config_updated)
            self.buddy_list_window.sign_off_requested.connect(self.handle_sign_off)
            self.buddy_list_window.send_message_requested.connect(self.handle_send_request)
            # Connect destroyed signal for cleanup
            self.buddy_list_window.destroyed.connect(self._buddy_list_destroyed)
            # --- End Signal Connections ---

            print("[Controller] Showing buddy list window.");
            self.buddy_list_window.show()
            # Initial status message (will be updated by connection events)
            self.buddy_list_window.statusBar().showMessage("Initializing connections...", 0)

        except Exception as e:
             print(f"[Controller] CRITICAL ERROR creating BuddyListWindow: {e}")
             traceback.print_exc()
             QMessageBox.critical(None, "UI Error", f"Failed to create buddy list window:\n{e}")
             self.buddy_list_window = None # Ensure it's None on failure
             self.handle_sign_off() # Attempt to go back to login


    @Slot()
    def _buddy_list_destroyed(self):
        """Slot called when the buddy list window is closed/destroyed."""
        print("[Controller] Buddy list window destroyed.")
        self.buddy_list_window = None # Clear the reference

        # Check if this was the last main window
        login_vis = self.login_window and self.login_window.isVisible()
        settings_vis = self.settings_window and self.settings_window.isVisible()

        if not login_vis and not settings_vis:
             print("[Controller] Buddy list was last window, setting QuitOnLastWindowClosed(True).")
             self.app.setQuitOnLastWindowClosed(True)
        # else:
             # print("[Controller] Other primary window still open, QuitOnLastWindowClosed unchanged.")


    @Slot(str, str, str) # sender_id, text, msg_type ('meshtastic')
    def route_incoming_message_from_mesh(self, sender_id, text, msg_type):
        """Routes incoming Meshtastic messages (received via signal) to the buddy list (UI Thread)."""
        # print(f"[Controller UI] route_incoming_message_from_mesh: From={sender_id}") # Noisy
        if self.buddy_list_window:
             try:
                  # Pass 'meshtastic' as the source identifier
                  self.buddy_list_window.handle_incoming_message(sender_id, text, 'meshtastic', msg_type)
             except Exception as e:
                  print(f"ERROR calling handle_incoming_message for Meshtastic: {e}"); traceback.print_exc()
        # else:
             # print("[Controller UI] Warning: Buddy list window not available for incoming Meshtastic message.")

    @Slot(str, str, str) # recipient_id, message_text, network_type ('meshtastic' or 'mqtt')
    def handle_send_request(self, recipient_id, message_text, network_type):
        """Handles request to send a message from UI (UI Thread)."""
        print(f"[Controller UI] handle_send_request: Type={network_type}, To={recipient_id}")

        if network_type == 'meshtastic':
             if self.meshtastic_handler and self.meshtastic_handler.is_running:
                 # Get channel index from runtime settings (originally from config)
                 channel_index = self.connection_settings.get("meshtastic_channel_index", 0) # Default 0
                 print(f"[Controller UI] Forwarding send request to Meshtastic handler (Channel: {channel_index}).")
                 # Pass channel_index to handler's send_message
                 self.meshtastic_handler.send_message(recipient_id, message_text, channel_index)
             else:
                 print("[Controller UI] Warning: Cannot send Meshtastic message. Handler not available or not running.")
                 if self.buddy_list_window:
                     self.buddy_list_window.statusBar().showMessage("Error: Meshtastic not connected.", 3000)

        elif network_type == 'mqtt':
            # --- Start MQTT Send Logic ---
            if self.mqtt_client and self.mqtt_client.is_connected():
                try:
                    target_topic = recipient_id # Use recipient ID as MQTT topic
                    print(f"[Controller UI] Publishing MQTT message to topic '{target_topic}'")

                    # Publish the message (use QoS 1 for reliability)
                    result, mid = self.mqtt_client.publish(
                        topic=target_topic,
                        payload=message_text.encode('utf-8'), # Encode string to bytes
                        qos=1,
                        retain=False
                    )

                    if result == mqtt.MQTT_ERR_SUCCESS:
                        print(f"[Controller UI] MQTT message queued successfully (mid={mid}).")
                        # Optionally update status bar, but _on_publish callback is more accurate confirmation
                        # if self.buddy_list_window:
                        #     self.buddy_list_window.statusBar().showMessage(f"IM sending to {recipient_id}...", 2000)
                    else:
                        # Publish failed immediately (e.g., invalid topic, client buffer full)
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
            # --- End MQTT Send Logic ---

        else:
             print(f"[Controller UI] Warning: Unknown network type '{network_type}' requested for send.")


    def cleanup(self):
        """Called when the application is about to quit."""
        print("[Controller] cleanup CALLED.")
        self._signing_off = True # Set flag to prevent issues during cleanup
        if self.node_update_timer.isActive():
             print("[Controller] Stopping periodic node update timer during cleanup.")
             self.node_update_timer.stop() # Ensure timer stops on exit
        self._disconnect_services() # Disconnect MQTT and Meshtastic cleanly
        print("[Controller] Cleanup finished.")
        # Ensure app can quit now
        self.app.setQuitOnLastWindowClosed(True)

# --- Main Execution ---
if __name__ == '__main__':
    # Setup application basics
    app = QApplication(sys.argv)
    app.setApplicationName("MIMMeshtastic") # Important for QStandardPaths
    app.setOrganizationName("MIMDev") # Optional, but good practice
    app.setStyle("Fusion") # Or another style like "Windows", "macOS"

    # --- Font Loading ---
    font_dir = get_resource_path("resources/fonts") # Assuming fonts are in resources/fonts
    loaded_font_families = []
    if os.path.isdir(font_dir):
        print(f"Looking for fonts in: {font_dir}")
        for filename in os.listdir(font_dir):
            if filename.lower().endswith((".ttf", ".otf")):
                font_path = os.path.join(font_dir, filename)
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    # Specifically check for Helvetica if needed
                    if "Helvetica" in families and "Helvetica" not in loaded_font_families:
                        loaded_font_families.append("Helvetica")
                    print(f"Loaded font: {filename} (Families: {families})")
                else:
                    print(f"Warning: Failed to load font: {font_path}")
    else:
        print(f"Warning: Font directory not found: {font_dir}")

    # Set default application font (fallback to Arial if Helvetica not loaded)
    default_font_family = "Helvetica" if "Helvetica" in loaded_font_families else "Arial"
    default_font_size = 9 # Standard UI size
    app.setFont(QFont(default_font_family, default_font_size))
    print(f"Default application font set to: {default_font_family} {default_font_size}pt")

    # --- Stylesheet Loading (Optional) ---
    qss_path = get_resource_path("resources/styles/styles.qss") # Example path
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
    controller = ApplicationController(app)
    # controller.app.aboutToQuit is connected inside controller.__init__
    print("Starting Qt event loop (app.exec)...")
    exit_code = app.exec()
    print(f"Qt event loop finished with exit code: {exit_code}")
    sys.exit(exit_code)
