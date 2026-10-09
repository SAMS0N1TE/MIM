"""Wait for native serial close before a MeshCore handshake retry."""
import asyncio
from meshcore import MeshCore, SerialConnection


class CompanionSerialConnection(SerialConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = asyncio.Event()
        self.console_reply = False
        self._probe_tail = b""

    def inspect_console_reply(self, data):
        probe = self._probe_tail + bytes(data)
        if b"-> Unknown command" in probe:
            self.console_reply = True
        self._probe_tail = probe[-32:]

    class MCSerialClientProtocol(SerialConnection.MCSerialClientProtocol):
        def data_received(self, data):
            self.cx.inspect_console_reply(data)
            super().data_received(data)

        def connection_lost(self, exc):
            super().connection_lost(exc)
            self.cx.closed.set()

    async def connect(self, timeout=10.0):
        self.closed.clear()
        result = await super().connect(timeout)
        # USB companions can reset when DTR changes. Let firmware boot before
        # the SDK sends its app-start handshake.
        await asyncio.sleep(1)
        return result

    async def disconnect(self):
        had_transport = self.transport is not None
        await super().disconnect()
        if had_transport:
            await asyncio.wait_for(self.closed.wait(), timeout=5)


async def connect_serial(port, default_timeout=10):
    for dtr in (True, False):
        connection = CompanionSerialConnection(port, 115200, dtr=dtr)
        radio = MeshCore(connection, default_timeout=default_timeout)
        try:
            if await radio.connect() is not None:
                return radio
        except BaseException:
            await radio.disconnect()
            await connection.disconnect()
            raise
        await radio.disconnect()
        await connection.disconnect()
        if connection.console_reply:
            raise ConnectionError(
                f"{port} answered as a MeshCore text console, not a serial companion. "
                "Check that the node is running USB/serial companion firmware. "
                "MIM has not changed the node's firmware or settings."
            )
    return None
