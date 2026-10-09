"""MeshCore companion adapter. All radio I/O runs on a dedicated asyncio loop."""
import asyncio
import threading
from copy import deepcopy

from meshcore import EventType, MeshCore
from PySide6.QtCore import QObject, Signal
from meshcore_serial import connect_serial


class MeshCoreHandler(QObject):
    connection_status = Signal(bool, str)
    message_received = Signal(str, str, str, str)
    node_list_updated = Signal(list)
    channel_list_updated = Signal(list)
    send_result = Signal(str, bool, str)
    _connection_established_signal = Signal()

    def __init__(self, connection_settings, parent=None):
        super().__init__(parent)
        self.settings = dict(connection_settings)
        self.is_running = False
        self._my_node_num = None
        self._nodes = []
        self._contacts = {}
        self._thread = None
        self._loop = None
        self._radio = None
        self._stop_requested = threading.Event()

    def connect_to_device(self):
        if self._thread and self._thread.is_alive():
            return False
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mim-meshcore")
        self._thread.start()
        return True

    def _run(self):
        asyncio.run(self._session())

    async def _session(self):
        self._loop = asyncio.get_running_loop()
        self._command_lock = asyncio.Lock()
        try:
            details = self.settings.get("mesh_details", "").strip()
            if self.settings.get("mesh_conn_type") == "Serial":
                self._radio = await connect_serial(details, default_timeout=10)
            else:
                host, separator, port = details.rpartition(":")
                if not separator:
                    raise ValueError("MeshCore TCP requires host:port")
                self._radio = await MeshCore.create_tcp(host.strip("[]"), int(port), default_timeout=10)
            if self._radio is None:
                raise ConnectionError("No response from MeshCore companion")
            if self._stop_requested.is_set():
                return
            self._radio.subscribe(EventType.CONTACT_MSG_RECV, self._on_direct)
            self._radio.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel)
            self._radio.subscribe(EventType.DISCONNECTED, self._on_disconnected)
            await self._refresh_contacts()
            await self._radio.start_auto_message_fetching()
            self.is_running = True
            self.connection_status.emit(True, "Connected")
            self._connection_established_signal.emit()
            while not self._stop_requested.is_set():
                await asyncio.sleep(0.1)
        except Exception as exc:
            detail = str(exc)
            if 'Access is denied' in detail or 'PermissionError' in detail:
                detail = (f"{self.settings.get('mesh_details', 'Serial port')} is busy or access was denied. "
                          "Close other radio apps, browser serial connections, or another MIM instance, "
                          "then try signing on again. If it persists, reconnect the USB cable.")
            self.connection_status.emit(False, detail)
        finally:
            self.is_running = False
            if self._radio:
                await self._radio.disconnect()
            self._radio = None
            self._loop = None

    def _on_disconnected(self, _event):
        self.is_running = False
        self._stop_requested.set()
        self.connection_status.emit(False, "Disconnected")

    async def _refresh_contacts(self):
        async with self._command_lock:
            result = await self._radio.commands.get_contacts()
        if result.type == EventType.ERROR:
            raise RuntimeError(f"Cannot read contacts: {result.payload}")
        self._contacts = result.payload
        self._nodes = [
            {"user": {"id": "mc:" + key, "longName": contact.get("adv_name", key[:12])},
             "lastHeard": contact.get("last_advert", 0), "active_report": False}
            for key, contact in self._contacts.items()
        ]
        self.node_list_updated.emit(self.get_latest_nodes())

    def _on_direct(self, event):
        prefix = event.payload.get("pubkey_prefix", "")
        matches = [key for key in self._contacts if prefix and key.startswith(prefix)]
        # A prefix collision must never silently send a reply to the wrong contact.
        key = matches[0] if len(matches) == 1 else prefix
        name = self._contacts.get(key, {}).get("adv_name", prefix)
        self.message_received.emit("mc:" + key, name, event.payload.get("text", ""), "direct")

    def _on_channel(self, event):
        index = event.payload.get("channel_idx", 0)
        if index == self.settings.get("meshtastic_channel_index", 0):
            self.message_received.emit("^all", f"Channel {index}",
                                       event.payload.get("text", ""), "broadcast")
            return
        self.message_received.emit(f"mc-channel:{index}", f"Channel {index}",
                                   event.payload.get("text", ""), "channel")

    def _submit(self, operation, destination=None):
        loop = self._loop
        if not self.is_running or loop is None or loop.is_closed():
            if destination is not None:
                self.send_result.emit(destination, False, "MeshCore is disconnected")
            return

        async def run():
            try:
                await operation()
            except Exception as exc:
                if destination is not None:
                    self.send_result.emit(destination, False, str(exc))
                else:
                    self.connection_status.emit(self.is_running, str(exc))

        asyncio.run_coroutine_threadsafe(run(), loop)

    def request_node_list(self):
        self._submit(self._refresh_contacts)

    def request_channel_list(self):
        # Only inspect the selected channel; never expose channel secrets to the UI.
        async def refresh():
            index = self.settings.get("meshtastic_channel_index", 0)
            async with self._command_lock:
                result = await self._radio.commands.get_channel(index)
            if result.type != EventType.ERROR:
                self.channel_list_updated.emit([{"index": index,
                    "name": result.payload.get("channel_name", f"Channel {index}"),
                    "encrypted": None}])
        self._submit(refresh)

    def reset_active_flags(self):
        pass  # MeshCore advertisements do not promise online presence.

    def get_latest_nodes(self):
        return deepcopy(self._nodes)

    def send_message(self, destination_id, text, channel_index=0):
        async def send():
            if len(text.encode("utf-8")) > 160:
                raise ValueError("MeshCore messages are limited to 160 UTF-8 bytes")
            async with self._command_lock:
                if destination_id == "^all" or destination_id.startswith("mc-channel:"):
                    index = int(destination_id.split(":")[1]) if destination_id.startswith("mc-channel:") else channel_index
                    result = await self._radio.commands.send_chan_msg(index, text)
                else:
                    key = destination_id.removeprefix("mc:")
                    if key not in self._contacts:
                        raise ValueError("Contact unavailable; refresh the buddy list before replying")
                    result = await self._radio.commands.send_msg(self._contacts[key], text)
            if result.type == EventType.ERROR:
                raise RuntimeError(f"Radio rejected message: {result.payload}")
            self.send_result.emit(destination_id, True, "Accepted by radio; delivery unconfirmed")
        self._submit(send, destination_id)

    def disconnect(self):
        self.is_running = False
        self._stop_requested.set()
