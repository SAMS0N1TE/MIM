import threading
import time
import traceback
import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
from meshtastic import mesh_interface
from meshtastic.protobuf.mesh_pb2 import MeshPacket
from meshtastic.protobuf.portnums_pb2 import PortNum

try:
    from pubsub import pub
    print("[Meshtastic Handler] PyPubSub imported successfully.")
except ImportError:
    print("ERROR: PyPubSub library not found. Meshtastic handler cannot function.")
    print("Please install it: pip install pypubsub")
    pub = None

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from sound_utils import play_sound_async

callback_counter = {"established": 0, "lost": 0, "receive": 0}
BROADCAST_ADDR_INT = 0xffffffff
BROADCAST_ADDR_STR = "^all"

class MeshtasticHandler(QObject):
    connection_status = Signal(bool, str)
    # --- FIX: Updated signal signature ---
    message_received = Signal(str, str, str, str) # sender_id (str), display_name (str), text (str), msg_type ('direct'/'broadcast')
    node_list_updated = Signal(list)

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
            else:
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

    def _on_connection_established(self, interface, topic=pub.AUTO_TOPIC):
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


    def _on_receive_packet(self, packet, interface):
        node_id = packet.get('fromId')
        if node_id:
            current_time = time.time()
            if node_id not in self._nodes: self._nodes[node_id] = {'nodeId': node_id}
            self._nodes[node_id]['lastHeard'] = packet.get('rxTime', current_time)
            # --- FIX: Update node info immediately on packet receive if possible ---
            # This helps ensure the node name is available sooner
            try:
                node_info_from_packet = packet.get('user') # Or wherever user info might be in the packet structure
                if node_info_from_packet and isinstance(node_info_from_packet, dict):
                    self._nodes[node_id]['user'] = node_info_from_packet
            except Exception: # Ignore errors updating cache from packet
                pass

        decoded_part = packet.get('decoded', {})
        portnum = decoded_part.get('portnum')
        payload = decoded_part.get('payload')

        is_text_message = (portnum == 'TEXT_MESSAGE_APP')

        if is_text_message and payload:
            global callback_counter
            callback_counter["receive"] += 1
            sender_id_log = packet.get('fromId', 'Unknown')
            to_id_log = packet.get('toId', 'Unknown')
            channel_log = packet.get('channel', 'Unknown')
            print(f"[Meshtastic Handler CB] <<< Processing Text Packet ({callback_counter['receive']}) From: {sender_id_log}, To: {to_id_log}, Ch: {channel_log} >>>")
            self._handle_text_message(packet, interface)

    def _handle_text_message(self, packet, interface):
        sender_id = packet.get('fromId', 'Unknown')
        text = packet.get('decoded', {}).get('text', '')

        if not text:
            print("[Meshtastic Rx Warning] Received empty text message payload in decoded part.")
            return

        # --- FIX: Look up display name ---
        display_name = sender_id # Default to ID
        if sender_id in self._nodes:
            node_info = self._nodes[sender_id]
            user_info = node_info.get('user', {})
            long_name = user_info.get('longName')
            short_name = user_info.get('shortName')
            if long_name:
                display_name = long_name
            elif short_name:
                display_name = short_name
            # else: display_name remains sender_id
        print(f"[Meshtastic Rx Proc] Resolved sender: ID={sender_id}, DisplayName='{display_name}'")
        # --- End Display Name Lookup ---

        raw_to_id = packet.get('toId')
        to_id = BROADCAST_ADDR_INT

        if raw_to_id is not None:
            if isinstance(raw_to_id, int):
                to_id = raw_to_id
            elif isinstance(raw_to_id, str):
                if raw_to_id.startswith('!'):
                    try:
                        to_id = int(raw_to_id[1:], 16)
                        print(f"[Meshtastic Rx Proc] Converted to_id string '{raw_to_id}' to int {to_id:#010x}")
                    except ValueError:
                        print(f"[Meshtastic Rx Warning] Invalid hex format for to_id string '{raw_to_id}'. Defaulting to broadcast.")
                        to_id = BROADCAST_ADDR_INT
                elif raw_to_id == '^all':
                     to_id = BROADCAST_ADDR_INT
                     print(f"[Meshtastic Rx Proc] Converted to_id string '^all' to int {to_id:#010x}")
                else:
                    try:
                         to_id = int(raw_to_id)
                         print(f"[Meshtastic Rx Proc] Converted plain to_id string '{raw_to_id}' to int {to_id}")
                    except ValueError:
                         print(f"[Meshtastic Rx Warning] Unrecognized to_id string format '{raw_to_id}'. Defaulting to broadcast.")
                         to_id = BROADCAST_ADDR_INT
            else:
                print(f"[Meshtastic Rx Warning] Unexpected type for to_id '{raw_to_id}' ({type(raw_to_id).__name__}). Defaulting to broadcast.")
                to_id = BROADCAST_ADDR_INT
        else:
             print("[Meshtastic Rx Info] to_id missing from packet. Assuming broadcast.")
             to_id = BROADCAST_ADDR_INT

        channel_index = packet.get('channel', 0)

        my_node_num_str = f"{self._my_node_num:#010x} ({self._my_node_num})" if self._my_node_num is not None else "None"
        print(f"[Meshtastic Rx Proc] Raw Packet Info: From={sender_id}, To={to_id:#010x} ({to_id}), Ch={channel_index}, MyNode={my_node_num_str}, Text='{text}'")

        to_id_type = type(to_id).__name__
        my_node_num_type = type(self._my_node_num).__name__ if self._my_node_num is not None else "NoneType"
        print(f"[Meshtastic Rx Proc] Checking Direct: Comparing ToId={to_id} (Type: {to_id_type}) with MyNodeNum={self._my_node_num} (Type: {my_node_num_type})")

        is_direct = False
        if self._my_node_num is not None:
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
            return

        # --- FIX: Emit display_name with signal ---
        if msg_type == 'broadcast':
             print(f"[Meshtastic Rx Proc] Emitting 'broadcast' message: SenderID={sender_id}, DisplayName='{display_name}', Text='{text[:20]}...'")
             self.message_received.emit(sender_id, display_name, text, 'broadcast')
        elif msg_type == 'direct':
             print(f"[Meshtastic Rx Proc] Emitting 'direct' message: SenderID={sender_id}, DisplayName='{display_name}', Text='{text[:20]}...'")
             self.message_received.emit(sender_id, display_name, text, 'direct')


    @Slot()
    def request_node_list(self):
        if not self.meshtastic_interface or not self.is_running:
            return
        try:
            current_nodes_dict = self.meshtastic_interface.nodes

            if current_nodes_dict is None:
                print("[Meshtastic Handler] Node list is 'None'.")
                self.node_list_updated.emit([])
                return
            if not current_nodes_dict:
                self.node_list_updated.emit([])
                return

            node_list_data = list(current_nodes_dict.values())

            if not node_list_data or not isinstance(node_list_data[0], dict):
                print(f"[Meshtastic Handler] Invalid node data format received: {node_list_data}")
                self.node_list_updated.emit([])
                return

            self._nodes = current_nodes_dict
            self.node_list_updated.emit(node_list_data)

        except mesh_interface.MeshInterfaceError as mesh_err:
            print(f"[Meshtastic Error] Failed fetching nodes (MeshInterfaceError): {mesh_err}")
        except AttributeError as ae:
             print(f"[Meshtastic Error] Failed fetching nodes, interface might be closing (AttributeError): {ae}")
        except Exception as e:
            print(f"[Meshtastic Error] Unexpected error fetching node list: {e}")
            traceback.print_exc()

    @Slot(str, str, int)
    def send_message(self, destination_id, text, channel_index=0):
        print(f"[Meshtastic Handler] send_message CALLED: Dest={destination_id}, Chan={channel_index}, Text='{text[:20]}...'")
        if not self.meshtastic_interface or not self.is_running:
            print("[Meshtastic Handler] Cannot send message: not connected/running.")
            return

        effective_destination_id = BROADCAST_ADDR_STR if destination_id == BROADCAST_ADDR_STR else destination_id

        try:
            print(f"[Meshtastic Tx] Queuing sendText to {effective_destination_id} on Ch {channel_index}")
            self.meshtastic_interface.sendText(
                text=text,
                destinationId=effective_destination_id,
                channelIndex=channel_index
            )
            print("[Meshtastic Tx] Message queued successfully.")

        except mesh_interface.MeshInterfaceError as mesh_err:
            print(f"[Meshtastic Tx Error] MeshInterfaceError: {mesh_err}")
        except Exception as e:
            print(f"[Meshtastic Tx Error] Unexpected error sending: {e}")
            traceback.print_exc()
