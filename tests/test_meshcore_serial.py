import asyncio
from unittest.mock import AsyncMock

import pytest

from meshcore_serial import CompanionSerialConnection, connect_serial


def test_disconnect_waits_for_native_port_close():
    async def scenario():
        connection = CompanionSerialConnection('COM99', 115200)
        protocol = connection.MCSerialClientProtocol(connection)
        class Transport:
            def close(self):
                asyncio.get_running_loop().call_later(0.02, protocol.connection_lost, None)
        connection.transport = Transport()
        await connection.disconnect()
        assert connection.closed.is_set()
        assert connection.transport is None
    asyncio.run(scenario())


def test_console_reply_across_serial_chunks():
    connection = CompanionSerialConnection('COM99', 115200)
    protocol = connection.MCSerialClientProtocol(connection)
    protocol.data_received(b'\r\n  -> Unk')
    protocol.data_received(b'nown command\r\n')
    assert connection.console_reply


def test_console_failure_closes_port_without_retry(monkeypatch):
    connection = CompanionSerialConnection('COM99', 115200)
    connection.console_reply = True
    connection.disconnect = AsyncMock()
    factory = AsyncMock(return_value=None)
    class Radio:
        connect = factory
        disconnect = AsyncMock()
    monkeypatch.setattr('meshcore_serial.CompanionSerialConnection', lambda *a, **k: connection)
    monkeypatch.setattr('meshcore_serial.MeshCore', lambda *a, **k: Radio())
    with pytest.raises(ConnectionError, match='text console'):
        asyncio.run(connect_serial('COM99'))
    assert factory.await_count == 1
    connection.disconnect.assert_awaited_once()
