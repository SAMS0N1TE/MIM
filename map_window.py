import sys
import os
import json
import time
import traceback
import math
import uuid

from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QApplication
from PySide6.QtCore import QUrl, QTimer, QStandardPaths, Signal, QCoreApplication, Slot
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings

NODE_OFFLINE_TIMEOUT_SEC = 600


class MapWindow(QMainWindow):
    closing = Signal(str)

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Node Map View")
        self.setMinimumSize(600, 500)
        self.settings = settings or {}
        self._node_data = {}
        self._map_js_ready = False
        self._pending_node_updates = []
        self.map_loaded_timer = QTimer(self)
        self.map_loaded_timer.setSingleShot(True)
        self.map_loaded_timer.timeout.connect(self._handle_map_js_ready)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        self.map_view = QWebEngineView()
        layout.addWidget(self.map_view)

        view_settings = self.map_view.settings()
        view_settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        view_settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)

        self.map_view.loadFinished.connect(self.on_loaded)
        self.load_initial_map()

    @Slot(dict)
    def handle_mqtt_node_update(self, node_data_dict):
        if node_data_dict:
            self.update_nodes([node_data_dict])

    @Slot(bool)
    def on_loaded(self, ok: bool):
        if ok:
            print("[MapWindow] Map HTML loaded successfully via QWebEngineView. Starting JS ready timer.")
            self.map_loaded_timer.start(500)
        else:
            print("[MapWindow] Map HTML failed to load via QWebEngineView.")
            self.map_view.setHtml("<h1>Map Load failed</h1>")
            self._map_js_ready = False
            self._pending_node_updates = []

    @Slot(list)
    def update_nodes(self, nodes: list):
        now = time.time()

        for n_data_from_input in nodes:
            if not isinstance(n_data_from_input, dict):
                continue

            user_info_from_input = n_data_from_input.get('user', {})
            node_id_from_input = (user_info_from_input.get('id') or
                                  n_data_from_input.get('nodeId') or
                                  str(uuid.uuid4()))

            current_node_entry = self._node_data.get(node_id_from_input, {})
            current_node_entry.update(n_data_from_input)

            last_heard_val = current_node_entry.get('lastHeard')
            sanitized_lh_map = 0.0
            if last_heard_val is not None:
                try:
                    sanitized_lh_map = float(last_heard_val)
                except (ValueError, TypeError):
                    print(
                        f"[MapWindow Warning] Node {node_id_from_input} had unconvertible lastHeard '{last_heard_val}'. Using 0.0.")
            current_node_entry['lastHeard'] = sanitized_lh_map

            if current_node_entry.get("active_report", False):
                current_node_entry["lastHeard"] = now

            user_info_final = current_node_entry.get('user', {})
            if not isinstance(user_info_final, dict):
                user_info_final = {}
            user_info_final['id'] = node_id_from_input
            current_node_entry['user'] = user_info_final

            position_val = current_node_entry.get('position')
            if position_val is not None:
                if isinstance(position_val, dict):
                    lat = position_val.get('latitude')
                    lon = position_val.get('longitude')
                    if lat is None or lon is None:
                        current_node_entry.pop("position", None)
                    else:
                        try:
                            current_node_entry['position']['latitude'] = float(lat)
                            current_node_entry['position']['longitude'] = float(lon)
                        except (ValueError, TypeError):
                            current_node_entry.pop("position", None)
                else:
                    try:
                        lat_obj = float(getattr(position_val, "latitude", math.nan))
                        lon_obj = float(getattr(position_val, "longitude", math.nan))
                        if math.isnan(lat_obj) or math.isnan(lon_obj):
                            current_node_entry.pop("position", None)
                        else:
                            current_node_entry["position"] = {"latitude": lat_obj, "longitude": lon_obj}
                    except (TypeError, AttributeError, ValueError):
                        current_node_entry.pop("position", None)

            self._node_data[node_id_from_input] = current_node_entry

        current_time_for_filter = time.time()
        cutoff = current_time_for_filter - NODE_OFFLINE_TIMEOUT_SEC

        filtered_node_data = {}
        for nid, nd_item in self._node_data.items():
            lh_to_compare = nd_item.get("lastHeard", 0.0)

            if lh_to_compare >= cutoff:
                filtered_node_data[nid] = nd_item
        self._node_data = filtered_node_data

        if self._map_js_ready:
            self._push_nodes(list(self._node_data.values()))
        else:
            self._pending_node_updates = list(self._node_data.values())

    def _push_nodes(self, nodes_to_push):
        clean_nodes_for_json = []
        for n_data in nodes_to_push:
            if not isinstance(n_data, dict):
                continue

            pos = n_data.get("position")
            if not (isinstance(pos, dict) and "latitude" in pos and "longitude" in pos):
                pos = None

            raw_metrics = n_data.get("deviceMetrics") or {}
            metrics_for_js = {}
            if isinstance(raw_metrics, dict):
                for k, v_val in raw_metrics.items():
                    if isinstance(v_val, (str, int, float, bool)) or v_val is None:
                        metrics_for_js[k] = v_val
                    else:
                        metrics_for_js[k] = str(v_val)

            user_info_from_n_data = n_data.get("user", {})
            if not isinstance(user_info_from_n_data, dict):
                user_info_from_n_data = {}

            cleaned = {
                "user": {
                    "id": user_info_from_n_data.get("id", n_data.get("nodeId", "unknown")),
                    "longName": user_info_from_n_data.get("longName", ""),
                    "shortName": user_info_from_n_data.get("shortName", "")
                },
                "position": pos,
                "snr": n_data.get("snr"),
                "deviceMetrics": metrics_for_js,
                "lastHeard": n_data.get("lastHeard")
            }
            clean_nodes_for_json.append(cleaned)

        if not clean_nodes_for_json:
            pass

        js_command = f"updateNodesFromPython({json.dumps(clean_nodes_for_json, default=lambda o: None)})"
        if self.map_view and self.map_view.page():
            try:
                self.map_view.page().runJavaScript(js_command)
            except Exception as e:
                print(f"[MapWindow] Error running JavaScript on map page: {e}")

    @Slot()
    def _handle_map_js_ready(self):
        self._map_js_ready = True
        print("[MapWindow] Map JavaScript is ready according to Python timer.")
        if self._pending_node_updates:
            updates_to_process = list(self._pending_node_updates)
            self._pending_node_updates = []
            if updates_to_process:
                self._push_nodes(updates_to_process)

    def load_initial_map(self):
        u = ""
        off = False
        d = ""
        lat, lon, zoom = 40.0, -100.0, 4
        icon_path = ""

        try:
            u = self.settings.get("map_online_tile_url", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
            off = bool(self.settings.get("map_offline_enabled", False))
            d = self.settings.get("map_offline_directory", "").replace("\\", "/")
            lat = float(self.settings.get("map_default_center_lat", 40.0))
            lon = float(self.settings.get("map_default_center_lon", -100.0))
            zoom = int(self.settings.get("map_default_zoom", 4))

            try:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if not os.path.isdir(
                        os.path.join(base_dir, "resources")):
                    base_dir = os.path.dirname(os.path.abspath(__file__))
                icon_path_temp = os.path.join(base_dir, "resources", "icons", "marker-icon-2x.png")
                if os.path.exists(icon_path_temp):
                    icon_path = icon_path_temp.replace("\\", "/")
                else:
                    print(f"[MapWindow] Warning: Main icon not found at {icon_path_temp}")
            except Exception as e_icon_path:
                print(f"[MapWindow] Error determining main icon path: {e_icon_path}")

            if not icon_path or not os.path.exists(icon_path):
                temp_icon_dir = os.path.join(
                    QStandardPaths.writableLocation(QStandardPaths.TempLocation),
                    QCoreApplication.applicationName() or "MIM", "map"
                )
                os.makedirs(temp_icon_dir, exist_ok=True)
                icon_path = os.path.join(temp_icon_dir, "default-marker.png").replace("\\", "/")
                if not os.path.exists(icon_path):
                    try:
                        with open(icon_path, "wb") as f:
                            f.write(
                                b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
                                b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
                                b'\x00\x00\x00\nIDATx\x9cc\x00\x00\x00\x02\x00\x01\xe5'
                                b"'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
                            )
                        print(f"[MapWindow] Created default marker icon at: {icon_path}")
                    except Exception as e_create_icon:
                        print(f"[MapWindow] Error creating default marker icon: {e_create_icon}")
                        icon_path = ""

            if not icon_path:
                print("[MapWindow] CRITICAL: No marker icon path could be established.")

        except Exception as e_settings:
            print(f"[MapWindow] Error initializing map settings: {e_settings}")
            self.map_view.setHtml("<h1>Error loading map settings</h1>")
            return

        tile_js_content = f"""
    window.nodeMarkers = {{}};
    const styleElement = document.createElement('style');
    styleElement.innerHTML = '.marker-ping {{ transform: scale(1.5); transition: transform 0.2s ease-out; }}';
    document.head.appendChild(styleElement);

    const customIcon = L.icon({{
        iconUrl: 'file:///{icon_path}',
        iconSize: [25, 41],
        iconAnchor: [12, 41],
        popupAnchor: [1, -34]
    }});

    var lastNodeUpdateTimes = {{}};

    window.updateNodesFromPython = function(nodes) {{
        const now = Date.now() / 1000;
        nodes.forEach(n => {{
            if (n.position && n.position.latitude != null && n.position.longitude != null) {{
                const nodeId = n.user.id;
                const coords = [n.position.latitude, n.position.longitude];
                const name = n.user.longName || n.user.shortName || nodeId;
                const snr = n.snr !== undefined && n.snr !== null ? n.snr.toFixed(1) : 'N/A';
                const lat_val = parseFloat(n.position.latitude); // Ensure numbers
                const lon_val = parseFloat(n.position.longitude);
                const lat_str = lat_val.toFixed(5);
                const lon_str = lon_val.toFixed(5);
                const ts = n.lastHeard ? new Date(n.lastHeard*1000).toLocaleString() : 'N/A';

                let metricsHTML = '';
                if (n.deviceMetrics) {{
                    const batt = n.deviceMetrics.batteryLevel;
                    if (batt !== undefined && batt !== null) metricsHTML += '<br/><small>Batt: ' + batt + '%</small>';
                }}

                const popupHTML = '<b>' + name + '</b><br/>Lat: ' + lat_str + ', Lon: ' + lon_str + '<br/>Last Heard: ' + ts + '<br/>SNR: ' + snr + metricsHTML;
                let marker = window.nodeMarkers[nodeId];

                if (marker) {{ 
                    marker.setLatLng(coords).setPopupContent(popupHTML); 
                }} else {{ 
                    marker = L.marker(coords, {{icon: customIcon}}).addTo(mymap).bindPopup(popupHTML); 
                    window.nodeMarkers[nodeId] = marker; 
                }}

                const lastUpdateTime = lastNodeUpdateTimes[nodeId];
                if (n.lastHeard && (lastUpdateTime === undefined || n.lastHeard > lastUpdateTime) && (now - n.lastHeard < 60)) {{
                    if (marker.getElement()) {{
                        marker.getElement().classList.add('marker-ping');
                        setTimeout(() => {{
                            if (marker.getElement()) {{ 
                                marker.getElement().classList.remove('marker-ping'); 
                            }}
                        }}, 300);
                    }}
                }}
                if (n.lastHeard) {{ 
                    lastNodeUpdateTimes[nodeId] = n.lastHeard; 
                }}
            }}
        }});
    }};

    var mymap = L.map('mapid').setView([{str(lat)}, {str(lon)}], {str(zoom)});
    var tileUrl = '{u}';
    var opts = {{maxZoom: 19, tms: false}};

    if ({str(off).lower()}) {{
        var p = encodeURIComponent('{d}').replace(/%2F/g, '/');
        tileUrl = 'file:///' + p + '/{{z}}/{{x}}/{{y}}.png';
    }}
    L.tileLayer(tileUrl, opts).addTo(mymap);
    console.log("Leaflet map initialized with tileUrl:", tileUrl);

    // Simpler ready signal for Python
    if(typeof qt !== 'undefined' && typeof qt.webChannelTransport !== 'undefined') {{
        new QWebChannel(qt.webChannelTransport, function(channel) {{
            window.pyJsBridge = channel.objects.pyJsBridge;
            if(window.pyJsBridge) {{
                console.log('Map JS: pyJsBridge available, signaling mapJsReady');
                window.pyJsBridge.mapJsIsReady();
            }} else {{
                console.error('Map JS: pyJsBridge object not found on channel.');
            }}
        }});
    }} else {{
         // Fallback if QWebChannel is not set up by Python side for this specific call yet
         // This relies on the Python-side timer in on_loaded.
        console.log('Map JS: QWebChannel not immediately available. Python timer will handle readiness.');
    }}
    """

        html_page_string = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8"/>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        html, body, #mapid {{ height: 100%; margin: 0; padding: 0; }}
        .marker-ping {{ transform: scale(1.5); transition: transform 0.2s ease-out; }}
    </style>
    <script>
        window.onerror = function(msg, url, line, col, error) {{
            console.error("JS Error:", msg, "URL:", url, "Line:", line, "Col:", col, "Error Obj:", error);
            if (window.pyJsBridge && window.pyJsBridge.logError) {{
                window.pyJsBridge.logError("JS Error: " + msg + " URL:" + url + " Line:" + line);
            }}
            return false; // Prevent default browser handling
        }};
    </script>
</head>
<body>
    <div id="mapid"></div>
    <script>
        {tile_js_content}
    </script>
</body>
</html>"""

        tmp_dir = os.path.join(
            QStandardPaths.writableLocation(QStandardPaths.TempLocation),
            QCoreApplication.applicationName() or "MIM", "map"
        )
        os.makedirs(tmp_dir, exist_ok=True)
        map_file_path = os.path.join(tmp_dir, "map.html")

        try:
            with open(map_file_path, "w", encoding="utf-8") as f:
                f.write(html_page_string)

            try:
                self.map_view.loadFinished.disconnect(self.on_loaded)
            except (RuntimeError, TypeError):
                pass
            self.map_view.loadFinished.connect(self.on_loaded)
            self.map_view.setUrl(QUrl.fromLocalFile(map_file_path))

        except Exception as e:
            print(f"[MapWindow] Error writing or loading map HTML from {map_file_path}: {e}")
            self.map_view.setHtml(f"<pre>Error generating map file:\n{traceback.format_exc()}</pre>")

    def closeEvent(self, event):
        self.hide()
        event.ignore()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName("MIMMeshtasticMapTest")
    app.setOrganizationName("MIMDevTest")

    dummy_settings = {
        "map_online_tile_url": "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        "map_default_center_lat": 34.05,
        "map_default_center_lon": -118.25,
        "map_default_zoom": 10
    }
    win = MapWindow(settings=dummy_settings)
    win.show()


    def test_update():
        win.update_nodes([
            {{"user": {{"id": "!1a2b3c", "longName": "NodeAlpha"}},
              "position": {{"latitude": 34.0522, "longitude": -118.2437}}, "snr": 10.5, "lastHeard": time.time(),
              "deviceMetrics": {{"batteryLevel": 88}}}},
            {{"user": {{"id": "!4d5e6f", "longName": "NodeBeta"}},
              "position": {{"latitude": 34.0550, "longitude": -118.2450}}, "snr": -3.2, "lastHeard": time.time() - 700,
              "deviceMetrics": {{"batteryLevel": 30}}}}
        ])


    QTimer.singleShot(3000, test_update)  # Increased delay

    sys.exit(app.exec())
