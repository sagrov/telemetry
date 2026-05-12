import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from datetime import datetime, timezone
import uuid

def _parse_ts(ts: Any) -> str:
    if ts is None:
        raise ValueError("Telemetry point missing 'ts'")

    if isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.replace(microsecond=0).isoformat()

    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.replace(microsecond=0).isoformat()

    raise ValueError(f"Unsupported ts type: {type(ts)}")

@dataclass
class ImportResult:
    flight_uid: str
    drone_id: str
    has_gps: bool
    points_count: int

def import_json_to_db(conn, json_path: Path) -> ImportResult:
    data = json.loads(json_path.read_text(encoding="utf-8"))

    drone_id = str(data.get("drone_id") or "UNKNOWN")
    flight_uid = str(data.get("flight_id") or uuid.uuid4().hex)

    telemetry: List[Dict[str, Any]] = data.get("telemetry") or []
    if not isinstance(telemetry, list) or len(telemetry) == 0:
        raise ValueError()

    rows = []
    has_gps = False

    for p in telemetry:
        ts = _parse_ts(p.get("ts"))

        lat = p.get("lat")
        lon = p.get("lon")
        alt = p.get("altitude")

        speed = p.get("speed")
        battery = p.get("battery")

        wind_speed = p.get("wind_speed")
        wind_dir = p.get("wind_dir_deg")

        agg_status = p.get("aggregates")

        # any extra fields -> payload_json
        payload = {k: v for k, v in p.items() if k not in {
            "ts", "lat", "lon", "altitude", "speed", "battery",
            "wind_speed", "wind_dir_deg", "aggregates"
        }}

        if lat is not None and lon is not None:
            has_gps = True

        rows.append((
            flight_uid, ts,
            lat, lon, alt,
            speed, battery,
            wind_speed, wind_dir,
            json.dumps(agg_status, ensure_ascii=False) if agg_status is not None else None,
            json.dumps(payload, ensure_ascii=False) if payload else None
        ))

    conn.execute(
        "INSERT OR REPLACE INTO flights (flight_uid, drone_id, imported_at, has_gps, source_file) VALUES (?,?,?,?,?)",
        (flight_uid, drone_id, datetime.utcnow().replace(microsecond=0).isoformat() + "Z", 1 if has_gps else 0, str(json_path))
    )

    conn.executemany(
        """INSERT INTO telemetry_points
           (flight_uid, ts, lat, lon, altitude, speed, battery_pct, wind_speed, wind_dir_deg, agg_status_json, payload_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        rows
    )

    conn.commit()
    return ImportResult(flight_uid=flight_uid, drone_id=drone_id, has_gps=has_gps, points_count=len(rows))
