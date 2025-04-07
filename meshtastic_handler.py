# meshtastic_handler.py
import threading
import time
import meshtastic # Import base library
import meshtastic.serial_interface
import meshtastic.tcp_interface
from meshtastic import mesh_interface # Use lowercase with underscore

try:
    from pubsub import pub
except ImportError:
    print("ERROR: PyPubSub library not found. Please install it: pip install pypubsub")
    raise

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from sound_utils import play_sound_async

# Add a counter for debugging callback calls
callback_counter = {"established": 0, "lost": 0, "receive": 0}

class MeshtasticHandler(QObject):
    """Handles connection and communication using Meshtastic PubSub."""

    # Public signals
    connection_status = Signal(bool, str)
    message_received = Signal(str, str, str)
    node_list_updated = Signal(list)

    # Internal signal for thread-safe slot invocation
    _request_node_list_signal = Signal()
    _connection_established_signal = Signal()

    def __init__(self, connection_settings, parent=None):
        super().__init__(parent)
        self.settings = connection_settings
        self.meshtastic_interface = None
        self.is_running = False
        self._nodes = {}
        self._subscribed = False
        print("[Meshtastic Handler] Initialized.")

        # Connect internal signal to the slot
        self._request_node_list_signal.connect(self.request_node_list)


    @Slot()
    def connect_to_device(self):
        """Attempts to connect and subscribes via pubsub."""
        conn_type = self.settings.get('mesh_conn_type', 'None')
        details = self.settings.get('mesh_details', '')
        print(f"[Meshtastic Handler] connect_to_device called: type='{conn_type}', details='{details}'")

        if self.meshtastic_interface and self.is_running:
            print("[Meshtastic Handler] Interface already exists and is considered running.")
            return True

        if self.meshtastic_interface:
             print("[Meshtastic Handler] Cleaning up previous interface before reconnecting...")
             try: self.meshtastic_interface.close()
             except Exception: pass
             self.meshtastic_interface = None
             self._subscribed = False

        try:
            print("[Meshtastic Handler] Creating interface...")
            if conn_type == 'Serial':
                if not details: raise ValueError("Serial port not specified.")
                print(f"[Meshtastic Handler] Creating SerialInterface for {details}...")
                self.meshtastic_interface = meshtastic.serial_interface.SerialInterface(devPath=details)
            elif conn_type == 'Network (IP)':
                 if not details: raise ValueError("Network IP/Hostname not specified.")
                 print(f"[Meshtastic Handler] Creating TCPInterface for {details}...")
                 self.meshtastic_interface = meshtastic.tcp_interface.TCPInterface(hostname=details)
            elif conn_type == 'None':
                 print("[Meshtastic Handler] Connection type is None, skipping.");
                 self.connection_status.emit(False, "Connection type is None."); return False
            else:
                 print(f"[Meshtastic Handler] Unknown connection type '{conn_type}', skipping.");
                 self.connection_status.emit(False, f"Unknown connection type: {conn_type}"); return False

            print("[Meshtastic Handler] Interface object created.")

            if not self._subscribed:
                print("[Meshtastic Handler] Subscribing to PubSub topics...")
                if 'pub' not in globals(): raise RuntimeError("PyPubSub 'pub' not imported correctly.")
                global callback_counter
                callback_counter = {"established": 0, "lost": 0, "receive": 0}
                pub.subscribe(self._on_receive_filtered, "meshtastic.receive")
                pub.subscribe(self._on_connection_established, "meshtastic.connection.established")
                pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
                self._subscribed = True
                print("[Meshtastic Handler] PubSub subscriptions registered.")
            else:
                print("[Meshtastic Handler] PubSub already subscribed.")

            print("[Meshtastic Handler] connect_to_device sequence complete. Waiting for pubsub events...")
            return True

        except Exception as e:
            error_type = type(e).__name__; error_msg = f"Meshtastic connection failed during setup ({error_type}): {e}"
            print(f"[Meshtastic Handler] {error_msg}")
            if self.meshtastic_interface:
                print("[Meshtastic Handler] Cleaning up interface after connection setup error...")
                try: self.meshtastic_interface.close()
                except Exception: pass
                finally: self.meshtastic_interface = None
            self.is_running = False
            self._subscribed = False
            self.connection_status.emit(False, error_msg); return False


    @Slot()
    def disconnect(self):
        print("[Meshtastic Handler] disconnect() called...")
        self.is_running = False
        if self._subscribed:
             print("[Meshtastic Handler] Unsubscribing from PubSub topics...")
             try:
                 if 'pub' in globals():
                     pub.unsubscribe(self._on_receive_filtered, "meshtastic.receive")
                     pub.unsubscribe(self._on_connection_established, "meshtastic.connection.established")
                     pub.unsubscribe(self._on_connection_lost, "meshtastic.connection.lost")
                     print("[Meshtastic Handler] PubSub unsubscribed.")
                 else: print("[Meshtastic Handler Warning] Cannot unsubscribe, pubsub not available.")
                 self._subscribed = False
             except Exception as unsub_err: print(f"[Meshtastic Warning] Error unsubscribing: {unsub_err}")
        if self.meshtastic_interface:
            print("[Meshtastic Handler] Closing interface...")
            try:
                self.meshtastic_interface.close()
                print("[Meshtastic Handler] Interface closed.")
            except Exception as e: print(f"[Meshtastic Handler] Error during interface close: {e}")
            finally: self.meshtastic_interface = None
        else: print("[Meshtastic Handler] No active interface to close.")
        self._nodes = {}


    # --- PubSub Callbacks ---
    def _on_connection_established(self, interface, topic=pub.AUTO_TOPIC):
        global callback_counter
        callback_counter["established"] += 1
        print(f"[Meshtastic Handler] _on_connection_established CALLED ({callback_counter['established']})")
        if interface == self.meshtastic_interface:
            print("[Meshtastic Handler] Connection established event matches current interface.")
            self.is_running = True

            print("[Meshtastic Handler] Playing sign-on sound.")
            play_sound_async("signon.wav")

            self.connection_status.emit(True, "Connected")

            print("[Meshtastic Handler] Emitting _connection_established_signal.")
            self._connection_established_signal.emit()
            # -----------------------------------------------------

        else: print("[Meshtastic Warning] Connection established event for unexpected interface.")


    def _on_connection_lost(self, interface, topic=pub.AUTO_TOPIC):
        global callback_counter
        callback_counter["lost"] += 1
        print(f"[Meshtastic Handler] _on_connection_lost CALLED ({callback_counter['lost']})")
        if interface == self.meshtastic_interface:
            print("[Meshtastic Handler] Connection lost event matches current interface.")
            if self.is_running:
                 self.connection_status.emit(False, "Connection Lost")
                 self.disconnect()
            else:
                 print("[Meshtastic Handler] Connection lost event received, but not considered running.")
        else: print("[Meshtastic Warning] Connection lost event for unexpected interface.")


    def _on_receive_filtered(self, packet, interface):
        if not self.is_running or interface != self.meshtastic_interface: return
        try:
            if not isinstance(packet, dict): return
            portnum = packet.get('decoded', {}).get('portnum'); from_id = packet.get('fromId')
            if portnum == 'TEXT_MESSAGE_APP' and from_id: self._handle_text_message(packet, interface)
        except Exception as e: import traceback; print(f"[Meshtastic Rx Error] {e}"); traceback.print_exc()


    def _handle_text_message(self, packet, interface):
        sender_id = packet.get('fromId'); text = packet.get('decoded', {}).get('text', '');
        if not text: return
        to_id = packet.get('toId', ''); msg_type = 'broadcast'
        try:
            if hasattr(interface, 'myInfo') and interface.myInfo:
                 my_node_id = getattr(interface.myInfo, 'my_node_num', None)
                 if to_id and my_node_id and to_id == my_node_id: msg_type = 'direct'
        except Exception as e: print(f"[Meshtastic Rx Warning] Could not determine message type: {e}")
        self.message_received.emit(sender_id, text, msg_type)


    # --- Slots ---
    @Slot()
    def request_node_list(self):
        print("[Meshtastic Handler] request_node_list SLOT CALLED.")
        if not self.meshtastic_interface or not self.is_running:
            print("[Meshtastic Handler] Cannot request node list, not connected/running.")
            return

        print("[Meshtastic Handler] Attempting to fetch nodes from interface...")
        try:
            current_nodes_dict = self.meshtastic_interface.nodes
            if current_nodes_dict is None:
                print("[Meshtastic Handler] Node list is 'None'. Still waiting?")
                return

            if not current_nodes_dict:
                 print("[Meshtastic Handler] Node list is empty. Waiting...")
                 return

            node_list_data = list(current_nodes_dict.values())
            if not node_list_data or not isinstance(node_list_data[0], dict):
                 print(f"[Meshtastic Handler] Invalid node data received? Type: {type(node_list_data[0])}")
                 return

            self._nodes = current_nodes_dict
            print(f"[Meshtastic Handler] Nodes fetched successfully: Count={len(node_list_data)}")
            self.node_list_updated.emit(node_list_data)

        except mesh_interface.MeshInterfaceError as mesh_err:
             print(f"[Meshtastic Error] Failed fetching nodes (MeshInterfaceError): {mesh_err}")
        except AttributeError as ae:
            print(f"[Meshtastic Error] Failed fetching nodes (AttributeError): {ae}")
        except Exception as e:
            import traceback
            print(f"[Meshtastic Error] Unexpected error fetching node list: {e}")
            traceback.print_exc()


    @Slot(str, str)
    def send_message(self, destination_id, text):
        print(f"[Meshtastic Handler] send_message CALLED: Dest={destination_id}")
        if not self.meshtastic_interface or not self.is_running:
            print("[Meshtastic Handler] Cannot send message, not connected/running.")
            return
        try:
            print(f"[Meshtastic Tx] Queuing message via sendText to {destination_id}: {text}")
            self.meshtastic_interface.sendText(text=text, destinationId=destination_id)
            print("[Meshtastic Tx] Message queued successfully.")
        except Exception as e: import traceback; print(f"[Meshtastic Tx Error] {e}"); traceback.print_exc()