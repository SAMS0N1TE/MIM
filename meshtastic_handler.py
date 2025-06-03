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
NODE_ACTIVE_TIMEOUT_SEC = 60 * 5  # 5 minutes


class MeshtasticHandler(QObject):
    connection_status = Signal(bool, str)
    message_received = Signal(str, str, str, str)
    node_list_updated = Signal(list)
    channel_list_updated = Signal(list)

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


    def _on_receive_packet(self, packet, interface):
        global callback_counter
        callback_counter["receive"] += 1

        if interface != self.meshtastic_interface:
            return


        if not isinstance(packet, dict):
            print(f"[Meshtastic Rx Warning] Unexpected packet type: {type(packet).__name__}")
            return

        from_id = packet.get('fromId')
        to_id = packet.get('toId')
        channel = packet.get('channel', 0)

        my_node_id_str = None
        if self.meshtastic_interface and self.meshtastic_interface.myInfo:
            if self._my_node_num is not None:
                my_node_id_str = f"!{self._my_node_num:x}"
            elif hasattr(self.meshtastic_interface.myInfo, 'node_num_str'):
                my_node_id_str = self.meshtastic_interface.myInfo.node_num_str

        if from_id and from_id in self._nodes and (my_node_id_str is None or from_id != my_node_id_str):
            self._nodes[from_id]['lastHeard'] = time.time()
            self._nodes[from_id]['active_report'] = True


        decoded = packet.get('decoded', {})

        if 'text' in decoded:
            self._handle_text_message(packet, interface)
            return

        port_num_val = decoded.get('portnum')
        if port_num_val is None:
            port_num_val = packet.get('portnum')

        if port_num_val is None:
            return

        if port_num_val == PortNum.TEXT_MESSAGE_APP:
            self._handle_text_message(packet, interface)
        elif port_num_val == PortNum.POSITION_APP:
            pass
        elif port_num_val == PortNum.TELEMETRY_APP:
            pass
        elif port_num_val == PortNum.ROUTING_APP:
            pass
        else:
            pass

    @Slot()
    def connect_to_device(self):
        conn_type = self.settings.get('mesh_conn_type', 'None')
        details = self.settings.get('mesh_details', '')
        print(f"[Meshtastic Handler] connect_to_device called: type='{conn_type}', details='{details}'")

        if self.meshtastic_interface and self.is_running:
            print("[Meshtastic Handler] Interface already exists and is running.")
            QTimer.singleShot(0, lambda: self.connection_status.emit(True, "Already connected"))
            QTimer.singleShot(100, self.request_channel_list)
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
                time.sleep(0.5)
                if interface.myInfo and hasattr(interface.myInfo, 'my_node_num'):
                    self._my_node_num = interface.myInfo.my_node_num
                    print(f"[Meshtastic Handler CB] Successfully obtained My Node Number: {self._my_node_num:#010x} ({self._my_node_num})")
                else:
                    print("[Meshtastic Handler CB Warning] interface.myInfo or my_node_num attribute not available after delay.")
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

    def _handle_text_message(self, packet, interface):
        sender_id = packet.get('fromId', 'Unknown')
        text = packet.get('decoded', {}).get('text', '')

        if not text:
            print("[Meshtastic Rx Warning] Received empty text message payload in decoded part.")
            return

        display_name = sender_id
        if sender_id in self._nodes:
            node_info = self._nodes[sender_id]
            user_info = node_info.get('user', {})
            long_name = user_info.get('longName')
            short_name = user_info.get('shortName')
            if long_name:
                display_name = long_name
            elif short_name:
                display_name = short_name
        print(f"[Meshtastic Rx Proc] Resolved sender: ID={sender_id}, DisplayName='{display_name}'")

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
                        print(
                            f"[Meshtastic Rx Warning] Invalid hex format for to_id string '{raw_to_id}'. Defaulting to broadcast.")
                        to_id = BROADCAST_ADDR_INT
                elif raw_to_id == '^all':
                    to_id = BROADCAST_ADDR_INT
                    print(f"[Meshtastic Rx Proc] Converted to_id string '^all' to int {to_id:#010x}")
                else:
                    try:
                        to_id = int(raw_to_id)
                        print(f"[Meshtastic Rx Proc] Converted plain to_id string '{raw_to_id}' to int {to_id}")
                    except ValueError:
                        print(
                            f"[Meshtastic Rx Warning] Unrecognized to_id string format '{raw_to_id}'. Defaulting to broadcast.")
                        to_id = BROADCAST_ADDR_INT
            else:
                print(
                    f"[Meshtastic Rx Warning] Unexpected type for to_id '{raw_to_id}' ({type(raw_to_id).__name__}). Defaulting to broadcast.")
                to_id = BROADCAST_ADDR_INT
        else:
            print("[Meshtastic Rx Info] to_id missing from packet. Assuming broadcast.")
            to_id = BROADCAST_ADDR_INT

        channel_index = packet.get('channel', 0)

        my_node_num_str = f"{self._my_node_num:#010x} ({self._my_node_num})" if self._my_node_num is not None else "None"
        print(
            f"[Meshtastic Rx Proc] Raw Packet Info: From={sender_id}, To={to_id:#010x} ({to_id}), Ch={channel_index}, MyNode={my_node_num_str}, Text='{text}'")

        print(
            f"[Meshtastic Rx DEBUG] Comparing as numbers: to_id={to_id} (type={type(to_id).__name__}), my_node_num={self._my_node_num} (type={type(self._my_node_num).__name__})")

        my_node_num_int = None
        if self._my_node_num is not None:
            try:
                if isinstance(self._my_node_num, str) and self._my_node_num.startswith('!'):
                    my_node_num_int = int(self._my_node_num[1:], 16)
                else:
                    my_node_num_int = int(self._my_node_num)
            except (ValueError, TypeError):
                print(f"[Meshtastic Rx Warning] Could not convert my_node_num '{self._my_node_num}' to integer.")

        is_direct = False
        if my_node_num_int is not None:
            is_direct = (to_id == my_node_num_int)
            print(
                f"[Meshtastic Rx DEBUG] Direct comparison result: {is_direct} (compared {to_id} == {my_node_num_int})")

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
            print(
                f"[Meshtastic Rx Proc] Ignoring message: Not direct to self or primary channel broadcast (To: {to_id:#010x}, Ch: {channel_index})")
            return

        if sender_id in self._nodes:
            self._nodes[sender_id]['active_report'] = True
            self._nodes[sender_id]['lastHeard'] = time.time()
            print(f"[Meshtastic Rx] Marked node {sender_id} as active after receiving message")

        if msg_type == 'broadcast':
            print(
                f"[Meshtastic Rx Proc] Emitting 'broadcast' message: SenderID={sender_id}, DisplayName='{display_name}', Text='{text[:20]}...'")
            self.message_received.emit(sender_id, display_name, text, 'broadcast')
        elif msg_type == 'direct':
            print(
                f"[Meshtastic Rx Proc] Emitting 'direct' message: SenderID={sender_id}, DisplayName='{display_name}', Text='{text[:20]}...'")
            self.message_received.emit(sender_id, display_name, text, 'direct')


    @Slot()
    def request_node_list(self):
        if not self.meshtastic_interface or not self.is_running:
            self.node_list_updated.emit([])
            return
        try:
            current_nodes_dict = self.meshtastic_interface.nodes

            if current_nodes_dict is None:
                print("[Meshtastic Handler] Node list is 'None' from interface.")
                self.node_list_updated.emit([])
                return
            if not current_nodes_dict:
                self.node_list_updated.emit([])
                return

            for node_id, node_data_from_lib in current_nodes_dict.items():
                if node_id == "!4357ebfc":
                    print(f"[DEBUG MH MAP] Node !4357ebfc data from lib: {node_data_from_lib}")
                    print(f"[DEBUG MH MAP] Position for !4357ebfc from lib: {node_data_from_lib.get('position')}")

                lh_value_from_lib = node_data_from_lib.get('lastHeard')
                sanitized_lh = 0.0
                if lh_value_from_lib is not None:
                    try:
                        sanitized_lh = float(lh_value_from_lib)
                    except (ValueError, TypeError):
                        pass
                node_data_from_lib['lastHeard'] = sanitized_lh

                current_active_report_state = self._nodes.get(node_id, {}).get('active_report', False)

                self._nodes[node_id] = node_data_from_lib

                if 'active_report' in self._nodes[node_id]:
                    self._nodes[node_id]['active_report'] = current_active_report_state
                else:
                    self._nodes[node_id]['active_report'] = False

            node_list_to_emit = list(self._nodes.values())
            if node_list_to_emit and not all(isinstance(n, dict) for n in node_list_to_emit):
                print(f"[Meshtastic Handler Error] Invalid node data format in list to emit.")
                self.node_list_updated.emit([])
                return

            self.node_list_updated.emit(node_list_to_emit)

        except mesh_interface.MeshInterfaceError as mesh_err:
            print(f"[Meshtastic Error] Failed fetching nodes (MeshInterfaceError): {mesh_err}")
            self.node_list_updated.emit(list(self._nodes.values()))
        except AttributeError as ae:
            print(f"[Meshtastic Error] Failed fetching nodes, interface might be closing (AttributeError): {ae}")
            self.node_list_updated.emit(list(self._nodes.values()))
        except Exception as e:
            print(f"[Meshtastic Error] Unexpected error fetching node list: {e}")
            traceback.print_exc()
            self.node_list_updated.emit(list(self._nodes.values()))

    def reset_active_flags(self):
        """Reset all active_report flags and mark old nodes as inactive.
        This should be called periodically."""
        current_time = time.time()
        for node_id, node_data in self._nodes.items():
            last_heard_val = node_data.get("lastHeard")

            lh_for_calculation = 0.0  # Default
            if last_heard_val is not None:
                try:
                    lh_for_calculation = float(last_heard_val)
                except (ValueError, TypeError):
                    print(
                        f"[Meshtastic Handler Warning] Node {node_id} had unconvertible lastHeard '{last_heard_val}' in reset_active_flags. Using 0.0.")

            time_diff = current_time - lh_for_calculation

            if time_diff > NODE_ACTIVE_TIMEOUT_SEC:
                if node_data.get('active_report', False):
                    print(f"[Meshtastic Handler] Node {node_id} marked inactive due to timeout ({time_diff:.1f}s).")
                node_data['active_report'] = False
            else:
                if node_data.get('active_report', False):
                    pass
                node_data['active_report'] = False

    @Slot()
    def request_channel_list(self):
        print("[Meshtastic Handler] Requesting channel list...")
        if not self.meshtastic_interface or not self.is_running:
            print("[Meshtastic Handler] Cannot request channel list: not connected/running.")
            self.channel_list_updated.emit([])
            return

        try:
            if not hasattr(self.meshtastic_interface, 'localNode') or not self.meshtastic_interface.localNode:
                 print("[Meshtastic Handler] Error: localNode not available on interface.")
                 self.channel_list_updated.emit([])
                 return

            channels = getattr(self.meshtastic_interface.localNode, 'channels', None)
            if channels is None:
                 print("[Meshtastic Handler] localNode returned None for channels.")
                 self.channel_list_updated.emit([])
                 return

            channel_data_list = []
            for i, ch in enumerate(channels):
                ch_settings = getattr(ch, 'settings', None)
                if ch_settings:
                    name = getattr(ch_settings, 'name', '') or (f"Primary" if i == 0 else f"Channel {i}")
                    psk = getattr(ch_settings, 'psk', b'')
                    is_encrypted = bool(psk)
                    channel_data_list.append({
                        'index': i,
                        'name': name,
                        'encrypted': is_encrypted
                    })
                else:
                     print(f"[Meshtastic Handler] Warning: Channel at index {i} has no settings attribute.")

            print(f"[Meshtastic Handler] Channels fetched: Count={len(channel_data_list)}")
            self.channel_list_updated.emit(channel_data_list)

        except AttributeError as ae:
            print(f"[Meshtastic Handler] Error accessing channel attribute: {ae} (library version?).")
            self.channel_list_updated.emit([])
        except Exception as e:
            print(f"[Meshtastic Error] Unexpected error fetching channel list: {e}")
            traceback.print_exc()
            self.channel_list_updated.emit([])

    def get_latest_nodes(self) -> list:
        return list(self._nodes.values())

    @Slot(str, str, int)
    def send_message(self, destination_id, text, channel_index=0):
        print(
            f"[Meshtastic Handler] send_message CALLED: Dest={destination_id}, Chan={channel_index}, Text='{text[:20]}...'")
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

            if self._my_node_num is not None:
                node_id = f"!{self._my_node_num:x}"
                if node_id in self._nodes:
                    self._nodes[node_id]['active_report'] = True
                    self._nodes[node_id]['lastHeard'] = time.time()
                    print(f"[Meshtastic Tx] Updated own node {node_id} to active status")
                else:
                    print(f"[Meshtastic Tx] Warning: Couldn't find own node {node_id} in nodes list")

        except mesh_interface.MeshInterfaceError as mesh_err:
            print(f"[Meshtastic Tx Error] MeshInterfaceError: {mesh_err}")
        except Exception as e:
            print(f"[Meshtastic Tx Error] Unexpected error sending: {e}")
            traceback.print_exc()
