import threading
import time
import traceback
import meshtastic # <<< Ensure this is imported
import meshtastic.serial_interface
import meshtastic.tcp_interface
from meshtastic import mesh_interface
from meshtastic.protobuf.portnums_pb2 import PortNum


try:
    from pubsub import pub
except ImportError:
    print("ERROR: PyPubSub library not found. Please install it: pip install pypubsub")
    raise

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from sound_utils import play_sound_async

callback_counter = {"established": 0, "lost": 0, "receive": 0}

class MeshtasticHandler(QObject):
    connection_status = Signal(bool, str)
    message_received = Signal(str, str, str)
    node_list_updated = Signal(list)
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
        self._request_node_list_signal.connect(self.request_node_list)
    
    @Slot()
    def connect_to_device(self):
        conn_type = self.settings.get('mesh_conn_type', 'None')
        details = self.settings.get('mesh_details', '')
        print(f"[Meshtastic Handler] connect_to_device called: type='{conn_type}', details='{details}'")
        if self.meshtastic_interface and self.is_running:
            print("[Meshtastic Handler] Interface already exists and running.")
            return True
        if self.meshtastic_interface:
            print("[Meshtastic Handler] Cleaning up previous interface.")
            try:
                self.meshtastic_interface.close()
            except Exception:
                pass
            self.meshtastic_interface = None
            self._subscribed = False
        try:
            print("[Meshtastic Handler] Creating interface...")
            if conn_type == 'Serial':
                if not details:
                    raise ValueError("Serial port not specified.")
                print(f"[Meshtastic Handler] Creating SerialInterface for {details}...")
                self.meshtastic_interface = meshtastic.serial_interface.SerialInterface(devPath=details)
            elif conn_type == 'Network (IP)':
                if not details:
                    raise ValueError("Network IP/Hostname not specified.")
                print(f"[Meshtastic Handler] Creating TCPInterface for {details}...")
                self.meshtastic_interface = meshtastic.tcp_interface.TCPInterface(hostname=details)
            elif conn_type == 'None':
                print("[Meshtastic Handler] Connection type is None, skipping.")
                self.connection_status.emit(False, "Connection type is None.")
                return False
            else:
                print(f"[Meshtastic Handler] Unknown connection type '{conn_type}'.")
                self.connection_status.emit(False, f"Unknown connection type: {conn_type}")
                return False
            print("[Meshtastic Handler] Interface object created.")
            if not self._subscribed:
                print("[Meshtastic Handler] Subscribing to PubSub topics...")
                if 'pub' not in globals():
                    raise RuntimeError("PyPubSub 'pub' not imported correctly.")
                global callback_counter
                callback_counter = {"established": 0, "lost": 0, "receive": 0}
                pub.subscribe(self._on_receive_filtered, "meshtastic.receive")
                pub.subscribe(self._on_connection_established, "meshtastic.connection.established")
                pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
                self._subscribed = True
                print("[Meshtastic Handler] PubSub subscriptions registered.")
            else:
                print("[Meshtastic Handler] PubSub already subscribed.")
            print("[Meshtastic Handler] connect_to_device sequence complete.")
            return True
        except Exception as e:
            error_type = type(e).__name__
            error_msg = f"Connection failed during setup ({error_type}): {e}"
            print(f"[Meshtastic Handler] {error_msg}")
            traceback.print_exc()
            if self.meshtastic_interface:
                print("[Meshtastic Handler] Cleaning up interface after error.")
                try:
                    self.meshtastic_interface.close()
                except Exception:
                    pass
                finally:
                    self.meshtastic_interface = None
            self.is_running = False
            self.connection_status.emit(False, error_msg)
            return False
    
    @Slot()
    def disconnect(self):
        print("[Meshtastic Handler] disconnect() called.")
        self.is_running = False
        if self._subscribed:
            print("[Meshtastic Handler] Unsubscribing from PubSub.")
            try:
                if 'pub' in globals():
                    try:
                        pub.unsubscribe(self._on_receive_filtered, "meshtastic.receive")
                    except Exception as e:
                        print(f"  -Warn unsub receive: {e}")
                    try:
                        pub.unsubscribe(self._on_connection_established, "meshtastic.connection.established")
                    except Exception as e:
                        print(f"  -Warn unsub established: {e}")
                    try:
                        pub.unsubscribe(self._on_connection_lost, "meshtastic.connection.lost")
                    except Exception as e:
                        print(f"  -Warn unsub lost: {e}")
                    print("[Meshtastic Handler] PubSub unsubscribed attempt finished.")
                else:
                    print("[Meshtastic Handler Warning] Cannot unsubscribe, pubsub not loaded.")
                self._subscribed = False
            except Exception as unsub_err:
                print(f"[Meshtastic Warning] Error during unsubscribe block: {unsub_err}")
        if self.meshtastic_interface:
            print("[Meshtastic Handler] Closing interface...")
            try:
                self.meshtastic_interface.close()
                print("[Meshtastic Handler] Interface closed.")
            except Exception as e:
                print(f"[Meshtastic Handler] Error during interface close: {e}")
            finally:
                self.meshtastic_interface = None
        else:
            print("[Meshtastic Handler] No active interface to close.")
        self._nodes = {}
    
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
        else:
            print("[Meshtastic Warning] Connection established event for unexpected interface.")
    
    def _on_connection_lost(self, interface, topic=pub.AUTO_TOPIC):
        global callback_counter
        callback_counter["lost"] += 1
        print(f"[Meshtastic Handler] _on_connection_lost CALLED ({callback_counter['lost']})")
        if interface == self.meshtastic_interface:
            print("[Meshtastic Handler] Connection lost event matches current interface.")
            if self.is_running:
                print("[Meshtastic Handler] Was running, emitting status and disconnecting.")
                self.connection_status.emit(False, "Connection Lost")
                self.disconnect()
            else:
                print("[Meshtastic Handler] Connection lost event received, but already not running.")
                if self.meshtastic_interface:
                    self.disconnect()
        else:
            print("[Meshtastic Warning] Connection lost event for unexpected interface.")
    
    def _on_receive_filtered(self, interface, packet, topic=pub.AUTO_TOPIC):
        node_id = packet.get('fromId')
        if not node_id:
             return

        current_time = time.time()
        if node_id in self._nodes:
            node = self._nodes[node_id]
        else:
            node = {'nodeId': node_id}
            self._nodes[node_id] = node

        user_info = packet.get('user')
        if user_info:
            node['user'] = user_info

        if 'deviceMetrics' in packet:
            node['deviceMetrics'] = packet['deviceMetrics']
        if 'position' in packet:
            node['position'] = packet['position']
        if 'rxTime' in packet:
            node['lastHeard'] = packet['rxTime']
        else:
             node['lastHeard'] = current_time
        node['lastReceived'] = packet
        self.node_list_updated.emit(list(self._nodes.values()))

        decoded_part = packet.get('decoded', {})
        portnum = decoded_part.get('portnum')
        payload = decoded_part.get('payload')

        print(f"\n--- Packet Received ---")
        print(f"From Node ID: {node_id}")
        print(f"Decoded Part: {decoded_part}")
        print(f"Extracted PortNum: {portnum} (Type: {type(portnum).__name__})")
        if payload:
             try:
                  print(f"Payload (decoded): '{payload.decode('utf-8', errors='replace')}'")
             except Exception:
                  print(f"Payload (raw bytes): {payload}")
        else:
             print("Payload: None")

        if portnum == 'TEXT_MESSAGE_APP':
            print(f"[Meshtastic Handler] PortNum MATCHES TextMessage ({PortNum.TEXT_MESSAGE_APP}). Calling _handle_text_message.")
            self._handle_text_message(packet, interface)
        else:
            print(f"[Meshtastic Handler] PortNum ({portnum}) does NOT match TextMessage ({PortNum.TEXT_MESSAGE_APP}). Not processing as text.")

        print(f"--- End Packet Processing ---\n")
            
    def _handle_text_message(self, packet, interface):
        sender_id = packet.get('fromId')
        text = packet.get('decoded', {}).get('text', '')
        if not text:
            return
        to_id = packet.get('toId')
        msg_type = 'broadcast'
        try:
            if hasattr(interface, 'myInfo') and interface.myInfo:
                my_node_num = getattr(interface.myInfo, 'my_node_num', None)
                if to_id and my_node_num and to_id == my_node_num:
                    msg_type = 'direct'
        except Exception as e:
            print(f"[Meshtastic Rx Warning] Could not determine message type: {e}")
        self.message_received.emit(sender_id, text, msg_type)
    
    @Slot()
    def request_node_list(self):
        print("[Meshtastic Handler] request_node_list SLOT CALLED.")
        if not self.meshtastic_interface or not self.is_running:
            print("[Meshtastic Handler] Cannot request node list: not connected/running.")
            return
        print("[Meshtastic Handler] Attempting to fetch nodes from interface...")
        try:
            current_nodes_dict = self.meshtastic_interface.nodes
            print(f"[Meshtastic Handler DEBUG] Raw nodes dictionary received: {current_nodes_dict}")
            if current_nodes_dict is None:
                print("[Meshtastic Handler] Node list is 'None'.")
                return
            if not current_nodes_dict:
                print("[Meshtastic Handler] Node list is empty.")
                self.node_list_updated.emit([])
                return
            node_list_data = list(current_nodes_dict.values())
            if not node_list_data or not isinstance(node_list_data[0], dict):
                print(f"[Meshtastic Handler] Invalid node data format received.")
                self.node_list_updated.emit([])
                return
            self._nodes = current_nodes_dict
            print(f"[Meshtastic Handler] Nodes fetched successfully: Count={len(node_list_data)}")
            self.node_list_updated.emit(node_list_data)
        except mesh_interface.MeshInterfaceError as mesh_err:
            print(f"[Meshtastic Error] Failed fetching nodes (MeshInterfaceError): {mesh_err}")
        except AttributeError as ae:
            print(f"[Meshtastic Error] Failed fetching nodes (AttributeError): {ae}")
        except Exception as e:
            print(f"[Meshtastic Error] Unexpected error fetching node list: {e}")
            traceback.print_exc()
    
    @Slot(str, str, int)
    def send_message(self, destination_id, text, channelIndex=0):
        print(f"[Meshtastic Handler] send_message CALLED: Dest={destination_id}, Chan={channelIndex}")
        if not self.meshtastic_interface or not self.is_running:
            print("[Meshtastic Handler] Cannot send message: not connected/running.")
            return
        try:
            print(f"[Meshtastic Tx] Queuing sendText to {destination_id} on Ch {channelIndex}: {text}")
            self.meshtastic_interface.sendText(
                text=text,
                destinationId=destination_id,
                channelIndex=channelIndex
            )
            print("[Meshtastic Tx] Message queued successfully.")
        except mesh_interface.MeshInterfaceError as mesh_err:
            print(f"[Meshtastic Tx Error] MeshInterfaceError: {mesh_err}")
        except Exception as e:
            print(f"[Meshtastic Tx Error] Unexpected error sending: {e}")
            traceback.print_exc()
