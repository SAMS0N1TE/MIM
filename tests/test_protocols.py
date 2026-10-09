import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2
from meshcore import EventType
from meshcore_handler import MeshCoreHandler
from meshtastic_handler import MeshtasticHandler
from protobuf_codec import decode_map_report, decode_service_envelope


def test_map_report_binary_and_topic_identity():
    report = mqtt_pb2.MapReport(long_name="Radio One", latitude_i=407000000, longitude_i=-740000000, has_opted_report_location=True)
    envelope = mqtt_pb2.ServiceEnvelope(gateway_id="!ffffffff")
    setattr(envelope.packet, "from", 0x1234abcd)
    envelope.packet.decoded.portnum = portnums_pb2.MAP_REPORT_APP
    envelope.packet.decoded.payload = report.SerializeToString()
    result = decode_map_report(envelope.SerializeToString(), "msh/US/2/map/")
    assert result["user"]["id"] == "!1234abcd"
    assert result["position"]["longitude"] == -74
    assert decode_map_report(b"\xff", "msh/US/2/map/!1234abcd") is None
    assert decode_map_report(report.SerializeToString(), "msh/US/2/map/") is None
    assert decode_map_report(b"", "msh/US/2/map/!1234abcd") is None
    report.has_opted_report_location = False
    envelope.packet.decoded.payload = report.SerializeToString()
    assert decode_map_report(envelope.SerializeToString(), "msh/US/2/map/") is None


def test_service_envelope_plaintext_encrypted_and_invalid():
    envelope = mqtt_pb2.ServiceEnvelope()
    setattr(envelope.packet, "from", 123)
    envelope.packet.decoded.portnum = portnums_pb2.POSITION_APP
    envelope.packet.decoded.payload = mesh_pb2.Position(latitude_i=0, longitude_i=0).SerializeToString()
    assert decode_service_envelope(envelope.SerializeToString())["position"]["latitude"] == 0
    envelope.packet.encrypted = b"ciphertext"
    assert decode_service_envelope(envelope.SerializeToString()) is None
    assert decode_service_envelope(b"\xff") is None


def test_meshcore_channels_and_ambiguous_prefix(app):
    handler = MeshCoreHandler({})
    received = []
    handler.message_received.connect(lambda *args: received.append(args))
    handler._on_channel(SimpleNamespace(payload={"channel_idx": 4, "text": "hi"}))
    assert received[-1] == ("mc-channel:4", "Channel 4", "hi", "channel")
    handler._contacts = {"abcdef111": {"adv_name": "One"}, "abcdef222": {"adv_name": "Two"}}
    handler._on_direct(SimpleNamespace(payload={"pubkey_prefix": "abcdef", "text": "hi"}))
    assert received[-1][0] == "mc:abcdef"


def test_meshcore_send_channel_and_error(app):
    handler = MeshCoreHandler({})
    results = []
    handler.send_result.connect(lambda *args: results.append(args))
    radio = SimpleNamespace(commands=SimpleNamespace(send_chan_msg=AsyncMock(return_value=SimpleNamespace(type=EventType.MSG_SENT))))
    handler._radio = radio
    # Exercise the actual send coroutine synchronously so Qt signal delivery is deterministic.
    handler._submit = lambda operation, destination=None: asyncio.run(operation())
    handler._command_lock = asyncio.Lock()
    handler.send_message("mc-channel:4", "hello", 0)
    radio.commands.send_chan_msg.assert_awaited_once_with(4, "hello")
    assert results[-1][1] is True


def test_meshtastic_utf8_limit_and_channel_routing(app):
    handler = MeshtasticHandler({})
    interface = Mock()
    handler.meshtastic_interface = interface
    handler.is_running = True
    results = []
    handler.send_result.connect(lambda *args: results.append(args))
    handler._send("mt-channel:3", "hello", 0)
    interface.sendText.assert_called_once_with("hello", destinationId="^all", channelIndex=3, wantAck=False)
    handler._send("!12345678", "é" * 117, 0)
    assert results[-1][1] is False
    assert interface.sendText.call_count == 1
    handler.disconnect()


def test_meshtastic_string_enum_and_channel_receive(app):
    handler = MeshtasticHandler({})
    interface = Mock()
    handler.meshtastic_interface = interface
    received = []
    handler.message_received.connect(lambda *args: received.append(args))
    handler._on_receive_packet({"from": 123, "to": 0xffffffff, "channel": 2,
        "decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"hello"}}, interface)
    assert received[-1] == ("mt-channel:2", "!0000007b", "hello", "channel")
    handler.disconnect()


def test_meshcore_connection_failure_is_visible(app, monkeypatch):
    monkeypatch.setattr("meshcore_handler.connect_serial", AsyncMock(return_value=None))
    handler = MeshCoreHandler({"mesh_conn_type": "Serial", "mesh_details": "COM99"})
    results = []
    handler.connection_status.connect(lambda *args: results.append(args))
    asyncio.run(handler._session())
    assert results[-1][0] is False
    assert not handler.is_running
    assert handler._loop is None


def test_meshcore_send_rejection_and_utf8_size(app):
    handler = MeshCoreHandler({})
    results = []
    handler.send_result.connect(lambda *args: results.append(args))
    async def scenario():
        handler._loop = asyncio.get_running_loop()
        handler._command_lock = asyncio.Lock()
        handler.is_running = True
        command = AsyncMock(return_value=SimpleNamespace(type=EventType.ERROR, payload={"reason": "busy"}))
        handler._radio = SimpleNamespace(commands=SimpleNamespace(send_chan_msg=command))
        handler.send_message("^all", "hello", 2)
        await asyncio.sleep(0.03)
        assert results[-1][1] is False
        handler.send_message("^all", "é" * 81, 2)
        await asyncio.sleep(0.03)
        assert "160" in results[-1][2]
        assert command.await_count == 1
    asyncio.run(scenario())
