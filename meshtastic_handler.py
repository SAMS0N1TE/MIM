"""Meshtastic SDK adapter with serialized I/O and Qt-safe result signals."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time

import meshtastic.serial_interface
import meshtastic.tcp_interface
from meshtastic.protobuf import mesh_pb2, portnums_pb2
from pubsub import pub
from PySide6.QtCore import QObject, Signal


class MeshtasticHandler(QObject):
    connection_status = Signal(bool, str)
    message_received = Signal(str, str, str, str)
    node_list_updated = Signal(list)
    channel_list_updated = Signal(list)
    send_result = Signal(str, bool, str)
    _connection_established_signal = Signal()

    def __init__(self, connection_settings, parent=None):
        super().__init__(parent)
        self.settings = dict(connection_settings)
        self.meshtastic_interface = None
        self.is_running = False
        self._my_node_num = None
        self._nodes = {}
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mim-meshtastic")
        pub.subscribe(self._on_receive_packet, "meshtastic.receive")
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")

    def connect_to_device(self):
        if self._stopping.is_set():
            return False
        self._executor.submit(self._connect)
        return True

    def _connect(self):
        try:
            details = self.settings.get("mesh_details", "")
            if self.settings.get("mesh_conn_type") == "Serial":
                interface = meshtastic.serial_interface.SerialInterface(devPath=details, timeout=30)
            elif self.settings.get("mesh_conn_type") == "Network (IP)":
                interface = meshtastic.tcp_interface.TCPInterface(hostname=details, timeout=30)
            else:
                raise ValueError("Select a serial or network connection")
            # SDK constructors complete the initial configuration handshake. Subscribing
            # to established afterwards misses that event on fast devices.
            if self._stopping.is_set():
                interface.close()
                return
            with self._lock:
                self.meshtastic_interface = interface
                self._my_node_num = getattr(interface.myInfo, "my_node_num", None)
                self.is_running = True
            self.connection_status.emit(True, "Connected")
            self._connection_established_signal.emit()
        except Exception as exc:
            if not self._stopping.is_set():
                self.connection_status.emit(False, str(exc))

    def _on_connection_lost(self, interface, **_kwargs):
        if interface is self.meshtastic_interface and not self._stopping.is_set():
            self.is_running = False
            self.connection_status.emit(False, "Connection lost")

    def _on_receive_packet(self, packet, interface):
        if interface is not self.meshtastic_interface or self._stopping.is_set() or not isinstance(packet, dict):
            return
        decoded = packet.get("decoded", {})
        port = decoded.get("portnum")
        if isinstance(port, str):
            try:
                port = portnums_pb2.PortNum.Value(port)
            except ValueError:
                return
        if port != portnums_pb2.TEXT_MESSAGE_APP:
            return
        text = decoded.get("text")
        if text is None:
            payload = decoded.get("payload", b"")
            if not isinstance(payload, bytes):
                return
            text = payload.decode("utf-8", errors="replace")
        sender = packet.get("fromId") or f"!{packet.get('from', 0):08x}"
        if packet.get("from") == self._my_node_num:
            return
        with self._lock:
            node = self._nodes.get(sender, {})
            user = node.get("user", {})
            name = user.get("longName") or user.get("shortName") or sender
            if node:
                node["lastHeard"] = time.time()
                node["active_report"] = True
        broadcast = packet.get("to") == 0xffffffff or packet.get("toId") == "^all"
        if broadcast:
            index = packet.get("channel", 0)
            if index == self.settings.get("meshtastic_channel_index", 0):
                self.message_received.emit(sender, name, text, "broadcast")
            else:
                self.message_received.emit(f"mt-channel:{index}", name, text, "channel")
        else:
            self.message_received.emit(sender, name, text, "direct")

    def request_node_list(self):
        if self.is_running and not self._stopping.is_set():
            self._executor.submit(self._refresh_nodes)

    def _refresh_nodes(self):
        interface = self.meshtastic_interface
        if not interface or self._stopping.is_set():
            return
        nodes = deepcopy(interface.nodes or {})
        with self._lock:
            for key, node in nodes.items():
                node.setdefault("user", {}).setdefault("id", key)
                node["active_report"] = self._nodes.get(key, {}).get("active_report", False)
            self._nodes = nodes
        self.node_list_updated.emit(self.get_latest_nodes())

    def get_latest_nodes(self):
        with self._lock:
            return deepcopy(list(self._nodes.values()))

    def reset_active_flags(self):
        with self._lock:
            for node in self._nodes.values():
                node["active_report"] = False

    def request_channel_list(self):
        if self.is_running and not self._stopping.is_set():
            self._executor.submit(self._refresh_channels)

    def _refresh_channels(self):
        interface = self.meshtastic_interface
        if not interface or self._stopping.is_set():
            return
        channels = []
        for channel in interface.localNode.channels or []:
            if channel.role:
                channels.append({"index": channel.index, "name": channel.settings.name or "Primary",
                                 "encrypted": bool(channel.settings.psk)})
        self.channel_list_updated.emit(channels)

    def send_message(self, destination_id, text, channel_index=0):
        if not self.is_running or self._stopping.is_set():
            self.send_result.emit(destination_id, False, "Meshtastic is disconnected")
            return
        self._executor.submit(self._send, destination_id, text, channel_index)

    def _send(self, destination_id, text, channel_index):
        try:
            if len(text.encode("utf-8")) > mesh_pb2.Constants.DATA_PAYLOAD_LEN:
                raise ValueError("Meshtastic messages are limited to 233 UTF-8 bytes")
            if self._stopping.is_set() or not self.meshtastic_interface:
                raise ConnectionError("Meshtastic is disconnected")
            destination = destination_id
            if destination_id.startswith("mt-channel:"):
                channel_index = int(destination_id.split(":")[1])
                destination = "^all"
            self.meshtastic_interface.sendText(text, destinationId=destination,
                                             channelIndex=channel_index, wantAck=destination != "^all")
            self.send_result.emit(destination_id, True, "Accepted by radio; delivery unconfirmed")
        except Exception as exc:
            self.send_result.emit(destination_id, False, str(exc))

    def disconnect(self):
        if self._stopping.is_set():
            return
        self._stopping.set()
        self.is_running = False
        pub.unsubscribe(self._on_receive_packet, "meshtastic.receive")
        pub.unsubscribe(self._on_connection_lost, "meshtastic.connection.lost")
        def close():
            interface, self.meshtastic_interface = self.meshtastic_interface, None
            if interface:
                interface.close()
        self._executor.submit(close)
        self._executor.shutdown(wait=False)
