import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS flights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flight_uid TEXT UNIQUE NOT NULL,
  drone_id TEXT,
  imported_at TEXT NOT NULL,
  has_gps INTEGER NOT NULL DEFAULT 0,
  source_file TEXT
);

CREATE TABLE IF NOT EXISTS telemetry_points (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  flight_uid TEXT NOT NULL,
  ts TEXT NOT NULL,
  lat REAL,
  lon REAL,
  altitude REAL,
  speed REAL,
  battery_pct REAL,
  wind_speed REAL,
  wind_dir_deg REAL,
  agg_status_json TEXT,
  payload_json TEXT,
  FOREIGN KEY(flight_uid) REFERENCES flights(flight_uid)
);

CREATE INDEX IF NOT EXISTS idx_tp_flight_ts ON telemetry_points(flight_uid, ts);
"""

def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    return conn
