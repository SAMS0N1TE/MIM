"""Decode official Meshtastic MQTT ServiceEnvelope packets using SDK protobufs."""
from google.protobuf.message import DecodeError
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2


def decode_map_report(payload: bytes, topic: str = "") -> dict | None:
    """/map/ contains ServiceEnvelope > MeshPacket > MAP_REPORT_APP > MapReport.

    Identity comes from packet.from, never from the MQTT gateway/topic suffix.
    """
    return decode_service_envelope(payload, map_only=True)


def decode_service_envelope(payload: bytes, *, map_only=False) -> dict | None:
    """Read plaintext map/position/user packets; encrypted packets stay opaque."""
    try:
        envelope = mqtt_pb2.ServiceEnvelope.FromString(payload)
        packet = envelope.packet
        if not packet.HasField("decoded") or not getattr(packet, "from"):
            return None
        port = packet.decoded.portnum
        if map_only and port != portnums_pb2.MAP_REPORT_APP:
            return None
        node_id = f"!{getattr(packet, 'from'):08x}"
        result = {"nodeId": node_id, "user": {"id": node_id},
                  "lastHeard": packet.rx_time, "snr": packet.rx_snr,
                  "source": "mqtt_protobuf"}
        if port == portnums_pb2.MAP_REPORT_APP:
            report = mqtt_pb2.MapReport.FromString(packet.decoded.payload)
            if not report.has_opted_report_location:
                return None
            lat, lon = report.latitude_i / 1e7, report.longitude_i / 1e7
            result["user"].update(longName=report.long_name, shortName=report.short_name)
            result["position"] = {"latitude": lat, "longitude": lon, "altitude": report.altitude}
            result["active_report"] = True
        elif port == portnums_pb2.POSITION_APP:
            position = mesh_pb2.Position.FromString(packet.decoded.payload)
            if not position.HasField("latitude_i") or not position.HasField("longitude_i"):
                return None
            lat, lon = position.latitude_i / 1e7, position.longitude_i / 1e7
            result["position"] = {"latitude": lat, "longitude": lon, "altitude": position.altitude}
        elif port == portnums_pb2.NODEINFO_APP:
            user = mesh_pb2.User.FromString(packet.decoded.payload)
            result["user"].update(longName=user.long_name, shortName=user.short_name)
        else:
            return None
        position = result.get("position")
        if position and not (-90 <= position["latitude"] <= 90 and -180 <= position["longitude"] <= 180):
            return None
        return result
    except DecodeError:
        return None
