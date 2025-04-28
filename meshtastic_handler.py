# meshtastic_handler.py
import threading
import time
import traceback
import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
from meshtastic import mesh_interface
from meshtastic.protobuf.mesh_pb2 import MeshPacket
# Ensure PortNum is imported correctly
from meshtastic.protobuf.portnums_pb2 import PortNum

try:
    from pubsub import pub
    print("[Meshtastic Handler] PyPubSub imported successfully.")
except ImportError:
    print("ERROR: PyPubSub library not found. Meshtastic handler cannot function.")
    print("Please install it: pip install pypubsub")
    pub = None # Ensure pub exists but is None if import fails

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from sound_utils import play_sound_async

# Global callback counter for debugging PubSub issues
callback_counter = {"established": 0, "lost": 0, "receive": 0}
BROADCAST_ADDR_INT = 0xffffffff # Standard Meshtastic broadcast nodeNum
BROADCAST_ADDR_STR = "^all"     # Standard Meshtastic broadcast nodeNum string representation

class MeshtasticHandler(QObject):
    """Handles communication with a Meshtastic device."""
    # Signals emitted by the handler
    connection_status = Signal(bool, str) # connected (bool), message (str)
    message_received = Signal(str, str, str) # sender_id (str), text (str), msg_type ('direct'/'broadcast')
    node_list_updated = Signal(list) # List of node dictionaries

    # Internal signal used to trigger actions after connection established
    _connection_established_signal = Signal()

    def __init__(self, connection_settings, parent=None):
        super().__init__(parent)
        if pub is None:
             raise RuntimeError("PyPubSub not loaded. Meshtastic Handler cannot operate.")

        self.settings = connection_settings
        self.meshtastic_interface: mesh_interface.MeshInterface | None = None
        self.is_running = False
        self._nodes = {}
        self._subscribed_to_pubsub = False
        self._my_node_num = None

        print("[Meshtastic Handler] Initialized.")

    @Slot()
    def connect_to_device(self):
        """Attempts to establish connection based on stored settings."""
        conn_type = self.settings.get('mesh_conn_type', 'None')
        details = self.settings.get('mesh_details', '')
        print(f"[Meshtastic Handler] connect_to_device called: type='{conn_type}', details='{details}'")

        if self.meshtastic_interface and self.is_running:
            print("[Meshtastic Handler] Interface already exists and is running.")
            QTimer.singleShot(0, lambda: self.connection_status.emit(True, "Already connected"))
            return True

        if self.meshtastic_interface:
            print("[Meshtastic Handler] Cleaning up previous interface before reconnecting.")
            self.disconnect()

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
            else: # Includes 'None' and unknown types
                err_msg = "Connection type is None." if conn_type == 'None' else f"Unknown connection type: {conn_type}"
                print(f"[Meshtastic Handler] {err_msg}")
                self.connection_status.emit(False, err_msg)
                return False

            print("[Meshtastic Handler] Interface object created.")

            if not self._subscribed_to_pubsub:
                print("[Meshtastic Handler] Subscribing to PyPubSub topics...")
                global callback_counter
                callback_counter = {"established": 0, "lost": 0, "receive": 0}
                pub.subscribe(self._on_receive_packet, "meshtastic.receive")
                pub.subscribe(self._on_connection_established, "meshtastic.connection.established")
                pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
                self._subscribed_to_pubsub = True
                print("[Meshtastic Handler] PubSub subscriptions registered.")
            else:
                print("[Meshtastic Handler] PubSub already subscribed.")

            print("[Meshtastic Handler] connect_to_device sequence initiated. Waiting for connection events...")
            return True

        except Exception as e:
            error_type = type(e).__name__
            error_msg = f"Connection failed during setup ({error_type}): {e}"
            print(f"[Meshtastic Handler] {error_msg}")
            traceback.print_exc()
            self.disconnect()
            self.connection_status.emit(False, error_msg)
            return False

    @Slot()
    def disconnect(self):
        """Disconnects from the device and cleans up resources."""
        print("[Meshtastic Handler] disconnect() called.")
        self.is_running = False
        self._my_node_num = None

        if self._subscribed_to_pubsub:
            print("[Meshtastic Handler] Unsubscribing from PyPubSub...")
            try:
                if pub:
                    try: pub.unsubscribe(self._on_receive_packet, "meshtastic.receive")
                    except Exception as e: print(f"  -Warn unsub receive: {e}")
                    try: pub.unsubscribe(self._on_connection_established, "meshtastic.connection.established")
                    except Exception as e: print(f"  -Warn unsub established: {e}")
                    try: pub.unsubscribe(self._on_connection_lost, "meshtastic.connection.lost")
                    except Exception as e: print(f"  -Warn unsub lost: {e}")
                    print("[Meshtastic Handler] PubSub unsubscribe attempt finished.")
                else:
                    print("[Meshtastic Handler Warning] Cannot unsubscribe, pubsub not loaded.")
                self._subscribed_to_pubsub = False
            except Exception as unsub_err:
                print(f"[Meshtastic Warning] Error during PubSub unsubscribe block: {unsub_err}")

        if self.meshtastic_interface:
            print("[Meshtastic Handler] Closing interface...")
            interface_to_close = self.meshtastic_interface
            self.meshtastic_interface = None
            try:
                interface_to_close.close()
                print("[Meshtastic Handler] Interface closed.")
            except Exception as e:
                print(f"[Meshtastic Handler] Error during interface close: {e}")
        else:
            print("[Meshtastic Handler] No active interface to close.")

        self._nodes = {}
        print("[Meshtastic Handler] Disconnect process finished.")

    # --- PubSub Callbacks (Called from Meshtastic Threads) ---

    def _on_connection_established(self, interface, topic=pub.AUTO_TOPIC):
        """PubSub: Called when a connection is established."""
        global callback_counter
        callback_counter["established"] += 1
        print(f"[Meshtastic Handler CB] _on_connection_established CALLED ({callback_counter['established']})")

        if interface == self.meshtastic_interface:
            print("[Meshtastic Handler CB] Connection established event matches current interface.")
            self.is_running = True
            self._my_node_num = None
            try:
                if interface.myInfo and hasattr(interface.myInfo, 'my_node_num'):
                    self._my_node_num = interface.myInfo.my_node_num
                    print(f"[Meshtastic Handler CB] Successfully obtained My Node Number: {self._my_node_num:#010x} ({self._my_node_num})")
                else:
                    print("[Meshtastic Handler CB Warning] interface.myInfo or my_node_num attribute not available.")
            except Exception as e:
                 print(f"[Meshtastic Handler CB Warning] Could not get own node number: {e}")

            print("[Meshtastic Handler CB] Playing sign-on sound.")
            play_sound_async("signon.wav")
            self.connection_status.emit(True, "Connected")
            print("[Meshtastic Handler CB] Emitting _connection_established_signal.")
            self._connection_established_signal.emit()
        else:
            print("[Meshtastic Warning] Connection established event for unexpected/old interface. Ignoring.")

    def _on_connection_lost(self, interface, topic=pub.AUTO_TOPIC):
        """PubSub: Called when the connection is lost."""
        global callback_counter
        callback_counter["lost"] += 1
        print(f"[Meshtastic Handler CB] _on_connection_lost CALLED ({callback_counter['lost']})")

        if interface == self.meshtastic_interface:
            print("[Meshtastic Handler CB] Connection lost event matches current interface.")
            if self.is_running:
                print("[Meshtastic Handler CB] Was running, emitting status and triggering disconnect.")
                self.connection_status.emit(False, "Connection Lost")
                self.disconnect()
            else:
                print("[Meshtastic Handler CB] Connection lost event received, but already not running.")
                self.disconnect()
        else:
             print("[Meshtastic Warning] Connection lost event for unexpected/old interface. Ignoring.")


    # Using simple approach from "old code" for parsing portnum
    def _on_receive_packet(self, packet, interface): # raw packet dictionary
        """PubSub: Called when any packet is received."""
        # print(f"[Meshtastic Handler CB] _on_receive_packet triggered.") # Verbose

        # --- Node List Update ---
        node_id = packet.get('fromId')
        if node_id:
            current_time = time.time()
            if node_id not in self._nodes: self._nodes[node_id] = {'nodeId': node_id}
            self._nodes[node_id]['lastHeard'] = packet.get('rxTime', current_time)

        # --- Message Filtering (Reverted to simpler logic based on old code) ---
        decoded_part = packet.get('decoded', {})
        portnum = decoded_part.get('portnum') # Get portnum ONLY from decoded part
        payload = decoded_part.get('payload') # Get payload ONLY from decoded part

        # Use direct string comparison as in the old code
        is_text_message = (portnum == 'TEXT_MESSAGE_APP')

        # Optional logging
        # if portnum:
        #    print(f"[Meshtastic Handler CB] PortNum Check: Found in decoded='{portnum}', IsText={is_text_message}")

        if is_text_message and payload: # Check payload exists too
            global callback_counter
            callback_counter["receive"] += 1
            sender_id_log = packet.get('fromId', 'Unknown')
            to_id_log = packet.get('toId', 'Unknown')
            channel_log = packet.get('channel', 'Unknown')
            print(f"[Meshtastic Handler CB] <<< Processing Text Packet ({callback_counter['receive']}) From: {sender_id_log}, To: {to_id_log}, Ch: {channel_log} >>>")
            # Call handler that extracts text from decoded part
            self._handle_text_message(packet, interface)
        # else:
            # Optional logging for ignored packets
            # portnum_str = str(portnum) if portnum is not None else "N/A (or not in decoded)"
            # payload_info = f"Present" if payload else "None (or not in decoded)"
            # print(f"[Meshtastic Handler CB] Ignored packet: PortNum='{portnum_str}', PayloadIsPresent={payload is not None}, HasDecodedKey={'decoded' in packet}")


    # Kept refined msg_type logic, reverted text extraction
    def _handle_text_message(self, packet, interface):
        """Processes a received packet confirmed to be a text message."""
        sender_id = packet.get('fromId', 'Unknown')
        # Get text directly from decoded part
        text = packet.get('decoded', {}).get('text', '')

        if not text:
            print("[Meshtastic Rx Warning] Received empty text message payload in decoded part.")
            return

        # --- FIXED: Correctly handle to_id conversion ---
        raw_to_id = packet.get('toId')
        to_id = BROADCAST_ADDR_INT # Default value

        if raw_to_id is not None:
            if isinstance(raw_to_id, int):
                to_id = raw_to_id # Already an int
            elif isinstance(raw_to_id, str):
                if raw_to_id.startswith('!'):
                    try:
                        to_id = int(raw_to_id[1:], 16) # Convert hex string (after '!') to int
                        print(f"[Meshtastic Rx Proc] Converted to_id string '{raw_to_id}' to int {to_id:#010x}")
                    except ValueError:
                        print(f"[Meshtastic Rx Warning] Invalid hex format for to_id string '{raw_to_id}'. Defaulting to broadcast.")
                        to_id = BROADCAST_ADDR_INT
                elif raw_to_id == '^all': # Handle '^all' string explicitly
                     to_id = BROADCAST_ADDR_INT
                     print(f"[Meshtastic Rx Proc] Converted to_id string '^all' to int {to_id:#010x}")
                else:
                    # Try converting as plain integer string just in case (might be node num without !)
                    try:
                         to_id = int(raw_to_id)
                         print(f"[Meshtastic Rx Proc] Converted plain to_id string '{raw_to_id}' to int {to_id}")
                    except ValueError:
                         print(f"[Meshtastic Rx Warning] Unrecognized to_id string format '{raw_to_id}'. Defaulting to broadcast.")
                         to_id = BROADCAST_ADDR_INT
            else:
                # Handle other potential types if necessary, default to broadcast
                print(f"[Meshtastic Rx Warning] Unexpected type for to_id '{raw_to_id}' ({type(raw_to_id).__name__}). Defaulting to broadcast.")
                to_id = BROADCAST_ADDR_INT
        else:
             # toId was missing from packet, keep default broadcast
             print("[Meshtastic Rx Info] to_id missing from packet. Assuming broadcast.")
             to_id = BROADCAST_ADDR_INT
        # --- End FIXED to_id conversion ---


        channel_index = packet.get('channel', 0)

        my_node_num_str = f"{self._my_node_num:#010x} ({self._my_node_num})" if self._my_node_num is not None else "None"
        print(f"[Meshtastic Rx Proc] Raw Packet Info: From={sender_id}, To={to_id:#010x} ({to_id}), Ch={channel_index}, MyNode={my_node_num_str}, Text='{text}'")

        # --- Determine message type ---
        # Add detailed logging before comparison
        to_id_type = type(to_id).__name__
        my_node_num_type = type(self._my_node_num).__name__ if self._my_node_num is not None else "NoneType"
        print(f"[Meshtastic Rx Proc] Checking Direct: Comparing ToId={to_id} (Type: {to_id_type}) with MyNodeNum={self._my_node_num} (Type: {my_node_num_type})")

        is_direct = False
        if self._my_node_num is not None:
             # Comparison should now work as both are expected to be integers
             is_direct = (to_id == self._my_node_num)


        is_explicit_broadcast = (to_id == BROADCAST_ADDR_INT)
        is_primary_channel = (channel_index == 0)

        msg_type = None
        if is_direct:
            msg_type = 'direct'
            print(f"[Meshtastic Rx Proc] Classified as: DIRECT (Comparison Result: {is_direct})")
        elif is_primary_channel and is_explicit_broadcast:
            msg_type = 'broadcast'
            print(f"[Meshtastic Rx Proc] Classified as: BROADCAST (Ch=0 and ToId=BroadcastAddr)")
        else:
            print(f"[Meshtastic Rx Proc] Ignoring message: Not direct to self or primary channel broadcast (To: {to_id:#010x}, Ch: {channel_index})")
            return # Exit handling
        # --- End determine message type ---


        # Emit the message for the controller (UI thread)
        if msg_type == 'broadcast':
             print(f"[Meshtastic Rx Proc] Emitting 'broadcast' message: Sender={sender_id}, Text='{text[:20]}...'")
             self.message_received.emit(sender_id, text, 'broadcast')
        elif msg_type == 'direct':
             print(f"[Meshtastic Rx Proc] Emitting 'direct' message: Sender={sender_id}, Text='{text[:20]}...'")
             self.message_received.emit(sender_id, text, 'direct')


    @Slot()
    def request_node_list(self):
        """Requests the current node list from the Meshtastic interface."""
        # print("[Meshtastic Handler] request_node_list SLOT CALLED.") # Can be noisy
        if not self.meshtastic_interface or not self.is_running:
            # print("[Meshtastic Handler] Cannot request node list: not connected/running.")
            return
        # print("[Meshtastic Handler] Attempting to fetch nodes from interface...")
        try:
            # Accessing interface.nodes forces an update
            current_nodes_dict = self.meshtastic_interface.nodes
            # print(f"[Meshtastic Handler DEBUG] Raw nodes dictionary received: Count={len(current_nodes_dict) if current_nodes_dict else 0}")

            if current_nodes_dict is None:
                print("[Meshtastic Handler] Node list is 'None'.")
                self.node_list_updated.emit([]) # Emit empty list
                return
            if not current_nodes_dict:
                # print("[Meshtastic Handler] Node list is empty.")
                self.node_list_updated.emit([]) # Emit empty list
                return

            # Convert dictionary values to a list of node dictionaries
            node_list_data = list(current_nodes_dict.values())

            # Basic validation of the data format
            if not node_list_data or not isinstance(node_list_data[0], dict):
                print(f"[Meshtastic Handler] Invalid node data format received: {node_list_data}")
                self.node_list_updated.emit([]) # Emit empty list
                return

            # Update internal cache and emit the list
            self._nodes = current_nodes_dict
            # print(f"[Meshtastic Handler] Nodes fetched successfully: Count={len(node_list_data)}")
            self.node_list_updated.emit(node_list_data)

        except mesh_interface.MeshInterfaceError as mesh_err:
            print(f"[Meshtastic Error] Failed fetching nodes (MeshInterfaceError): {mesh_err}")
            # Optionally emit disconnect status if this error persists?
        except AttributeError as ae:
             print(f"[Meshtastic Error] Failed fetching nodes, interface might be closing (AttributeError): {ae}")
        except Exception as e:
            print(f"[Meshtastic Error] Unexpected error fetching node list: {e}")
            traceback.print_exc()

    @Slot(str, str, int)
    def send_message(self, destination_id, text, channel_index=0):
        """Sends a text message via the Meshtastic interface."""
        print(f"[Meshtastic Handler] send_message CALLED: Dest={destination_id}, Chan={channel_index}, Text='{text[:20]}...'")
        if not self.meshtastic_interface or not self.is_running:
            print("[Meshtastic Handler] Cannot send message: not connected/running.")
            # TODO: Add user feedback? Maybe emit a signal?
            return

        # Use the standard broadcast address string if destination is ^all
        effective_destination_id = BROADCAST_ADDR_STR if destination_id == BROADCAST_ADDR_STR else destination_id

        try:
            print(f"[Meshtastic Tx] Queuing sendText to {effective_destination_id} on Ch {channel_index}")
            # Use the sendText method which handles encoding etc.
            self.meshtastic_interface.sendText(
                text=text,
                destinationId=effective_destination_id, # Use '^all' for broadcast
                channelIndex=channel_index
                # wantAck=False # Optional: Request ACK for direct messages?
            )
            print("[Meshtastic Tx] Message queued successfully.")
            # Optional: Play send sound here? Or wait for confirmation?
            # play_sound_async("send.wav")

        except mesh_interface.MeshInterfaceError as mesh_err:
            print(f"[Meshtastic Tx Error] MeshInterfaceError: {mesh_err}")
            # TODO: Add user feedback
        except Exception as e:
            print(f"[Meshtastic Tx Error] Unexpected error sending: {e}")
            traceback.print_exc()
            # TODO: Add user feedback
