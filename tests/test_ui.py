from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from chat_window import ChatWindow
from login_window import LoginWindow
from settings_window import SettingsWindow
from resources import get_resource_path


def test_assets_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Path(get_resource_path("resources/icons/mim-mark.svg")).is_file()


def test_chat_enter_shift_enter_and_html_escape(app):
    window = ChatWindow("MyName", "!12345678", "Buddy")
    sent = []
    window.message_sent.connect(lambda *args: sent.append(args))
    window.message_input.setPlainText("hello")
    QTest.keyClick(window.message_input, Qt.Key_Return, Qt.ShiftModifier)
    assert not sent
    QTest.keyClick(window.message_input, Qt.Key_Return)
    assert sent
    window.receive_message("<img src=x onerror=bad>", "<b>Buddy</b>")
    assert "<b>Buddy</b>" in window.message_display.toPlainText()
    assert not window.send_button.isEnabled()
    window.close()


def test_login_preserves_sign_on_contract(app):
    window = LoginWindow("TrailRadio")
    sent = []
    window.sign_on_requested.connect(lambda *args: sent.append(args))
    window.on_sign_on_clicked()
    assert sent == [("TrailRadio", "", False)]
    window.close()


def test_settings_protocol_and_tls_roundtrip(app):
    window = SettingsWindow({"screen_name": "TrailRadio", "mesh_protocol": "MeshCore", "mqtt_tls": True})
    values = window.get_settings()
    assert values["mesh_protocol"] == "MeshCore"
    assert values["mqtt_tls"] is True
    window.close()


def test_buddy_network_routing():
    from buddy_list_window import radio_network
    assert radio_network("mc:" + "a" * 64) == "meshtastic"
    assert radio_network("!12345678") == "meshtastic"
    assert radio_network("mc-channel:2") == "meshtastic"
    assert radio_network("TrailRadio") == "mqtt"


def test_invalid_channel_is_rejected(app, monkeypatch):
    window = SettingsWindow({"screen_name": "TrailRadio"})
    warnings = []
    monkeypatch.setattr("settings_window.QMessageBox.warning", lambda *args: warnings.append(args))
    saved = []
    window.settings_saved.connect(saved.append)
    window.mesh_channel_index_input.setText("-1")
    window.accept()
    assert warnings and not saved
    window.close()
