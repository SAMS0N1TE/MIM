import json
from types import SimpleNamespace
from unittest.mock import Mock
import main


def test_config_never_persists_password(app, tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    monkeypatch.setattr(main, "get_config_path", lambda: str(config))
    assert main.save_config({"screen_name": "One", "password": "secret", "mqtt_tls": True})
    assert json.loads(config.read_text()) == {"screen_name": "One", "mqtt_tls": True}


def test_topic_matching_respects_boundaries():
    controller = SimpleNamespace(current_config={})
    assert main.ApplicationController._is_map_topic(controller, "msh/US/2/map/!12345678") == "proto"
    assert main.ApplicationController._is_map_topic(controller, "msh/US/2/maple/chat") is None


def test_backend_factory_selects_meshcore(app, monkeypatch):
    handler = Mock()
    factory = Mock(return_value=handler)
    monkeypatch.setattr(main, "MeshCoreHandler", factory)
    controller = Mock()
    controller._signing_off = False
    controller._quitting = False
    controller.meshtastic_handler = None
    main.ApplicationController._create_and_connect_meshtastic(controller, {"mesh_protocol": "MeshCore"})
    factory.assert_called_once()
    handler.connect_to_device.assert_called_once()


def test_selected_channel_is_used_for_broadcast():
    controller = Mock()
    controller.connection_settings = {"meshtastic_channel_index": 3}
    main.ApplicationController.handle_send_request(controller, "^all", "hello", "meshtastic")
    controller.meshtastic_handler.send_message.assert_called_once_with("^all", "hello", 3)


def test_incoming_channels_have_stable_titles():
    controller = Mock()
    chat = Mock()
    controller.buddy_list_window.chat_windows = {"mc-channel:3": chat}
    main.ApplicationController.route_incoming_message_from_mesh(controller, "mc-channel:3", "Sender", "hi", "channel")
    controller.buddy_list_window.open_chat_window.assert_called_once_with("mc-channel:3", "Channel 3", "meshtastic")


def test_mqtt_v2_disconnect_uses_reason_code():
    controller = Mock()
    controller._signing_off = False
    controller._quitting = False
    main.ApplicationController._on_mqtt_disconnect(controller, None, None, object(), 0)
    controller.mqtt_connection_updated.emit.assert_called_once_with(False, "Disconnected")


def test_mqtt_subscribes_to_direct_inbox():
    controller = Mock()
    controller.connection_settings = {"screen_name": "TrailRadio"}
    controller.current_config = {}
    controller._subscribed_mqtt_groups = set()
    controller.mqtt_client.subscribe.return_value = (0, 1)
    main.ApplicationController._on_mqtt_connect(controller, controller.mqtt_client, None, None, 0)
    controller.mqtt_client.subscribe.assert_any_call("TrailRadio", 1)


def test_controller_launch_and_cleanup(app, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_config_path", lambda: str(tmp_path / "mim.json"))
    controller = main.ApplicationController(app)
    assert controller.login_window.isVisible()
    assert controller.mqtt_client is None
    assert controller.update_mqtt_client is None
    controller.cleanup()
    app.aboutToQuit.disconnect(controller.cleanup)
