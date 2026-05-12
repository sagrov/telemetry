import json
import math
from pathlib import Path
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QUrl, QObject, Slot
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QMessageBox,
    QVBoxLayout, QPushButton, QListWidget, QTextEdit,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QLabel,
    QFormLayout, QDoubleSpinBox, QSpinBox, QHBoxLayout
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PySide6.QtWebChannel import QWebChannel

from db import connect
from importer import import_json_to_db
from analysis import assess_aggregates, estimate_endpoint_ellipse, ellipse_polygon
from mapgen import build_map_html

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "telemetry.sqlite"
MAP_HTML = APP_DIR / "map.html"
PLANNER_HTML = APP_DIR / "planner.html"


def iso_to_epoch_s(iso_ts: str) -> float:
    s = iso_ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class DebugWebPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        print(f"[JS] {sourceID}:{lineNumber}: {message}")


def _norm_deg(x: float) -> float:
    x = x % 360.0
    return x + 360.0 if x < 0 else x


def wind_to_from(wind_dir_from_deg: float) -> float:
    """Перетворити напрямок вітру FROM (звідки) у TO (куди)."""
    return _norm_deg(float(wind_dir_from_deg) + 180.0)


def move_point_m(lat: float, lon: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """
    Зсунути точку на dist_m метрів по азимуту bearing_deg.
    Достатньо точно для візуалізації планувальника.
    """
    R = 6378137.0
    br = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)

    d = dist_m / R
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(br))
    lon2 = lon1 + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)


def ground_speed_along_track(
    airspeed_mps: float,
    wind_speed_mps: float,
    track_deg: float,
    wind_to_deg: float
) -> float:
    """
    Розрахунок швидкості по землі ВЗДОВЖ заданого напрямку (ground track) з урахуванням вітру.

    Нехай напрямок треку = phi.
    Розкладемо вітер на компоненти відносно треку:
      W_par  = W*cos(d)   (вздовж треку)
      W_perp = W*sin(d)   (поперек треку)
      де d = phi - wind_to

    Щоб утримувати трек, дрон має компенсувати W_perp за рахунок своєї повітряної швидкості (Va).
    Тоді швидкість вздовж треку:
      Vg = W_par + sqrt(Va^2 - W_perp^2)

    Якщо |W_perp| > Va => утримати такий трек неможливо => Vg = 0.
    """
    Va = max(0.1, float(airspeed_mps))
    W = max(0.0, float(wind_speed_mps))
    phi = _norm_deg(float(track_deg))
    wind_to = _norm_deg(float(wind_to_deg))

    d = math.radians(_norm_deg(phi - wind_to))
    W_par = W * math.cos(d)
    W_perp = W * math.sin(d)

    if abs(W_perp) > Va:
        return 0.0

    Vg = W_par + math.sqrt(max(0.0, Va * Va - W_perp * W_perp))
    return max(0.0, Vg)


def time_budget_from_battery_air_distance(
    battery_pct: float,
    burn_pct_per_meter_air: float,
    airspeed_mps: float
) -> float:
    """
    Модель витрат батареї:
      burn = % на 1 метр ПОВІТРЯНОЇ дистанції (скільки батареї йде на 1м польоту в повітрі).
    Якщо повітряна швидкість Va (м/с), то витрата за секунду = burn_per_meter * Va.

    Бюджет часу:
      T = battery / (burn_per_meter * Va)
    """
    batt = max(0.0, min(100.0, float(battery_pct)))
    burn_m = max(1e-9, float(burn_pct_per_meter_air))
    Va = max(0.1, float(airspeed_mps))
    return batt / (burn_m * Va)


def one_way_reach_polygon(
    start_lat: float,
    start_lon: float,
    battery_pct: float,
    burn_pct_per_meter_air: float,
    airspeed_mps: float,
    wind_speed_mps: float,
    wind_to_deg: float,
    n: int = 240
) -> list[tuple[float, float]]:
    """
    Контур "куди можна долетіти" (в один бік) із точки старту.

    Батарея задає БЮДЖЕТ ЧАСУ через витрату на 1м у повітрі:
      T = batt / (burn_m * Va)

    Для кожного азимуту phi:
      dist(phi) = Vg(phi) * T
    """
    T = time_budget_from_battery_air_distance(battery_pct, burn_pct_per_meter_air, airspeed_mps)
    poly: list[tuple[float, float]] = []
    for i in range(n):
        phi = (360.0 * i) / n
        Vg = ground_speed_along_track(airspeed_mps, wind_speed_mps, phi, wind_to_deg)
        dist_m = Vg * T
        poly.append(move_point_m(start_lat, start_lon, phi, dist_m))
    return poly


def round_trip_reach_polygon(
    start_lat: float,
    start_lon: float,
    battery_pct: float,
    burn_pct_per_meter_air: float,
    airspeed_mps: float,
    wind_speed_mps: float,
    wind_to_deg: float,
    n: int = 240
) -> list[tuple[float, float]]:
    """
    Контур "безпечного повернення" (туди-назад).

    Доступний час T від батареї (такий самий, як для one-way):
      T = batt / (burn_m * Va)

    Для напрямку phi:
      v_out  = Vg(phi)
      v_back = Vg(phi+180)

    Час на дистанцію d туди і d назад:
      t = d/v_out + d/v_back

    d_max = T / (1/v_out + 1/v_back)
    """
    T = time_budget_from_battery_air_distance(battery_pct, burn_pct_per_meter_air, airspeed_mps)
    poly: list[tuple[float, float]] = []

    for i in range(n):
        phi = (360.0 * i) / n
        v_out = ground_speed_along_track(airspeed_mps, wind_speed_mps, phi, wind_to_deg)
        v_back = ground_speed_along_track(airspeed_mps, wind_speed_mps, phi + 180.0, wind_to_deg)

        if v_out <= 0.0 or v_back <= 0.0:
            d_max = 0.0
        else:
            d_max = T / (1.0 / v_out + 1.0 / v_back)

        poly.append(move_point_m(start_lat, start_lon, phi, d_max))

    return poly


def estimate_planner_ranges(
    battery_pct: float,
    burn_pct_per_meter_air: float,
    airspeed_mps: float,
    wind_speed_mps: float,
    wind_dir_from_deg: float,
) -> dict:
    wind_to = wind_to_from(wind_dir_from_deg)

    batt = max(0.0, min(100.0, float(battery_pct)))
    burn_m = max(1e-9, float(burn_pct_per_meter_air))
    Va = max(0.1, float(airspeed_mps))
    W = max(0.0, float(wind_speed_mps))

    T = time_budget_from_battery_air_distance(batt, burn_m, Va)

    phi_dw = wind_to
    phi_up = (wind_to + 180.0) % 360.0
    phi_cr = (wind_to + 90.0) % 360.0

    v_dw = ground_speed_along_track(Va, W, phi_dw, wind_to)
    v_up = ground_speed_along_track(Va, W, phi_up, wind_to)
    v_cr = ground_speed_along_track(Va, W, phi_cr, wind_to)

    one_dw = v_dw * T
    one_up = v_up * T
    one_cr = v_cr * T

    v_dw_out = ground_speed_along_track(Va, W, phi_dw, wind_to)
    v_dw_back = ground_speed_along_track(Va, W, phi_up, wind_to)
    rt_dw = 0.0 if (v_dw_out <= 0 or v_dw_back <= 0) else T / (1.0 / v_dw_out + 1.0 / v_dw_back)

    v_cr_out = ground_speed_along_track(Va, W, phi_cr, wind_to)
    v_cr_back = ground_speed_along_track(Va, W, (phi_cr + 180.0) % 360.0, wind_to)
    rt_cr = 0.0 if (v_cr_out <= 0 or v_cr_back <= 0) else T / (1.0 / v_cr_out + 1.0 / v_cr_back)

    notes = []
    if W >= Va:
        notes.append("Вітер ≥ Va: деякі напрями (поперечні) можуть бути недосяжні.")
    notes.append("Модель витрат: батарея витрачається на секунду швидкості.")

    return {
        "ok": True,
        "wind_to_deg": wind_to,
        "T_total_s": T,
        "one": {"downwind_m": one_dw, "upwind_m": one_up, "cross_m": one_cr},
        "rt": {"downwind_m": rt_dw, "cross_m": rt_cr},
        "notes": " ".join(notes)
    }


def write_planner_html(
    out_path: Path,
    center_lat: float,
    center_lon: float,
    zoom: int,
    one_poly: list | None,
    rt_poly: list | None,
):
    def js_array(poly):
        return "[" + ",".join(f"[{p[0]},{p[1]}]" for p in poly) + "]"

    one_js = "null" if not one_poly else js_array(one_poly)
    rt_js = "null" if not rt_poly else js_array(rt_poly)

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Планувальник</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    .hud {{
      position: absolute; z-index: 9999;
      top: 10px; right: 10px;
      background: rgba(255,255,255,0.92);
      padding: 10px 12px; border-radius: 10px;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
      font-size: 14px; line-height: 1.35;
      max-width: 420px;
      pointer-events: none;
    }}
    .legend {{ margin-top: 8px; font-size: 12px; opacity: 0.9; }}
    .swatch {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:middle; }}
  </style>
</head>
<body>
  <div class="hud">
    <b>Планувальник</b><br/>
    • Клік по мапі: задати <b>Старт</b><br/>
    <div class="legend">
      <div><span class="swatch" style="background:#2563eb;"></span>Досяжність в один бік</div>
      <div><span class="swatch" style="background:#16a34a;"></span>Безпечне повернення</div>
    </div>
  </div>
  <div id="map"></div>

<script>
  const cfg = {{
    center: {{ lat: {center_lat}, lon: {center_lon}, zoom: {zoom} }},
    one: {one_js},
    rt:  {rt_js}
  }};

  const map = L.map('map').setView([cfg.center.lat, cfg.center.lon], cfg.center.zoom);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19, attribution: '&copy; OpenStreetMap'
  }}).addTo(map);

  let bridge = null;
  new QWebChannel(qt.webChannelTransport, function(channel) {{
    bridge = channel.objects.bridge;
  }});

  let startMarker=null, oneLayer=null, rtLayer=null;

  function draw(lat, lon) {{
    if (startMarker) map.removeLayer(startMarker);
    if (oneLayer) map.removeLayer(oneLayer);
    if (rtLayer) map.removeLayer(rtLayer);

    startMarker = L.marker([lat, lon]).addTo(map).bindPopup("Старт").openPopup();

    if (cfg.one) {{
      oneLayer = L.polygon(cfg.one, {{
        color: "#2563eb",
        weight: 3,
        fillColor: "#2563eb",
        fillOpacity: 0.18
      }}).addTo(map);
    }}

    if (cfg.rt) {{
      rtLayer = L.polygon(cfg.rt, {{
        color: "#16a34a",
        weight: 3,
        fillColor: "#16a34a",
        fillOpacity: 0.16
      }}).addTo(map);
    }}
  }}

  draw(cfg.center.lat, cfg.center.lon);

  map.on('click', function(e) {{
    const lat = e.latlng.lat;
    const lon = e.latlng.lng;
    if (bridge && bridge.pickStart) bridge.pickStart(lat, lon);
  }});
</script>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")


class PlannerBridge(QObject):
    def __init__(self, on_pick_start_cb):
        super().__init__()
        self._on_pick_start_cb = on_pick_start_cb

    @Slot(float, float)
    def pickStart(self, lat: float, lon: float):
        self._on_pick_start_cb(lat, lon)


class PlannerTab(QWidget):
    def __init__(self):
        super().__init__()

        self.start_lat = 50.4502
        self.start_lon = 30.5233

        layout = QHBoxLayout(self)

        controls = QWidget()
        form = QFormLayout(controls)

        self.batt = QDoubleSpinBox()
        self.batt.setRange(0, 100)
        self.batt.setDecimals(1)
        self.batt.setValue(80.0)

        self.burn_per_m = QDoubleSpinBox()
        self.burn_per_m.setRange(0.00000001, 0.1)
        self.burn_per_m.setDecimals(8)
        self.burn_per_m.setSingleStep(0.000001)

        self.burn_per_m.setValue(0.0015)

        self.airspeed = QDoubleSpinBox()
        self.airspeed.setRange(0.1, 80)
        self.airspeed.setDecimals(1)
        self.airspeed.setValue(10.0)

        self.wind_speed = QDoubleSpinBox()
        self.wind_speed.setRange(0, 50)
        self.wind_speed.setDecimals(1)
        self.wind_speed.setValue(6.0)

        self.wind_dir_from = QSpinBox()
        self.wind_dir_from.setRange(0, 360)
        self.wind_dir_from.setValue(270)

        self.lbl_start = QLabel(f"Старт: {self.start_lat:.6f}, {self.start_lon:.6f}")
        self.lbl_budget = QLabel("Бюджет часу: -")
        self.lbl_one = QLabel("Досяжність (в один бік): -")
        self.lbl_rt = QLabel("Безпечне повернення (туди-назад): -")
        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)

        self.btn_recalc = QPushButton("Перерахувати / Оновити мапу")

        form.addRow("Батарея (%)", self.batt)
        form.addRow("Витрата (%/м) @ Va", self.burn_per_m)
        form.addRow("Швидкість Va (м/с)", self.airspeed)
        form.addRow("Швидкість вітру (м/с)", self.wind_speed)
        form.addRow("Напрямок вітру FROM (°)", self.wind_dir_from)
        form.addRow(self.btn_recalc)
        form.addRow(self.lbl_start)
        form.addRow(self.lbl_budget)
        form.addRow(self.lbl_one)
        form.addRow(self.lbl_rt)
        form.addRow(self.lbl_note)

        self.web = QWebEngineView()
        self.web.setPage(DebugWebPage(self.web))
        s = self.web.settings()
        s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)

        self.channel = QWebChannel(self.web.page())
        self.bridge = PlannerBridge(self.on_map_pick_start)
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)

        layout.addWidget(controls, 1)
        layout.addWidget(self.web, 7)

        self.btn_recalc.clicked.connect(self.recalc_and_render)
        self.recalc_and_render()

    def on_map_pick_start(self, lat: float, lon: float):
        self.start_lat = float(lat)
        self.start_lon = float(lon)
        self.lbl_start.setText(f"Старт: {self.start_lat:.6f}, {self.start_lon:.6f}")
        self.recalc_and_render()

    def recalc_and_render(self):
        res = estimate_planner_ranges(
            battery_pct=self.batt.value(),
            burn_pct_per_meter_air=self.burn_per_m.value(),
            airspeed_mps=self.airspeed.value(),
            wind_speed_mps=self.wind_speed.value(),
            wind_dir_from_deg=float(self.wind_dir_from.value()),
        )

        wind_to = float(res["wind_to_deg"])
        T = float(res["T_total_s"])

        one_dw = float(res["one"]["downwind_m"])
        one_up = float(res["one"]["upwind_m"])
        one_cr = float(res["one"]["cross_m"])

        rt_dw = float(res["rt"]["downwind_m"])
        rt_cr = float(res["rt"]["cross_m"])

        self.lbl_budget.setText(f"Бюджет часу: {T/60:.1f} хв")
        self.lbl_one.setText(
            f"В один бік: за вітром={one_dw/1000:.2f} км, проти вітру={one_up/1000:.2f} км, поперек={one_cr/1000:.2f} км"
        )
        self.lbl_rt.setText(
            f"Туди-назад: за вітром≈{rt_dw/1000:.2f} км, поперек≈{rt_cr/1000:.2f} км"
        )
        self.lbl_note.setText(res["notes"] or "")

        one_poly = one_way_reach_polygon(
            self.start_lat, self.start_lon,
            battery_pct=self.batt.value(),
            burn_pct_per_meter_air=self.burn_per_m.value(),
            airspeed_mps=self.airspeed.value(),
            wind_speed_mps=self.wind_speed.value(),
            wind_to_deg=wind_to,
            n=240
        )

        rt_poly = round_trip_reach_polygon(
            self.start_lat, self.start_lon,
            battery_pct=self.batt.value(),
            burn_pct_per_meter_air=self.burn_per_m.value(),
            airspeed_mps=self.airspeed.value(),
            wind_speed_mps=self.wind_speed.value(),
            wind_to_deg=wind_to,
            n=240
        )

        write_planner_html(
            PLANNER_HTML,
            center_lat=self.start_lat,
            center_lon=self.start_lon,
            zoom=12,
            one_poly=one_poly,
            rt_poly=rt_poly,
        )
        self.web.setUrl(QUrl.fromLocalFile(str(PLANNER_HTML.resolve())))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Аналізатор телеметрії")
        self.resize(1200, 800)

        self.conn = connect(DB_PATH)

        self.btn_load = QPushButton("Завантажити JSON")
        self.btn_refresh = QPushButton("Оновити список польотів")
        self.btn_reports = QPushButton("Переглянути звіт")
        self.flights_list = QListWidget()
        self.flights_list.currentItemChanged.connect(self.on_flight_selected)

        left = QWidget()
        l = QVBoxLayout(left)
        l.addWidget(self.btn_load)
        l.addWidget(self.btn_refresh)
        l.addWidget(self.btn_reports)
        l.addWidget(QLabel("Польоти:"))
        l.addWidget(self.flights_list)

        self.btn_load.clicked.connect(self.load_json)
        self.btn_refresh.clicked.connect(self.refresh_flights)
        self.btn_reports.clicked.connect(self.show_report)

        self.tabs = QTabWidget()

        self.web = QWebEngineView()
        self.web.setPage(DebugWebPage(self.web))
        s = self.web.settings()
        s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        self.tabs.addTab(self.web, "Карта")

        self.table = QTableWidget()
        self.tabs.addTab(self.table, "Таблиця")

        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        self.tabs.addTab(self.report_text, "Звіт/Аналіз")

        self.planner_tab = PlannerTab()
        self.tabs.addTab(self.planner_tab, "Планувальник")

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)

        container = QWidget()
        root = QVBoxLayout(container)
        root.addWidget(splitter)
        self.setCentralWidget(container)

        self.current_flight_uid = None
        self.refresh_flights()

    def refresh_flights(self):
        self.flights_list.clear()
        cur = self.conn.execute(
            "SELECT flight_uid, drone_id, imported_at, has_gps FROM flights ORDER BY imported_at DESC"
        )
        for flight_uid, drone_id, imported_at, has_gps in cur.fetchall():
            label = f"{flight_uid} | drone={drone_id} | gps={'yes' if has_gps else 'no'} | {imported_at}"
            self.flights_list.addItem(label)

        if self.flights_list.count() > 0:
            self.flights_list.setCurrentRow(0)

    def load_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Виберіть telemetry JSON", str(APP_DIR), "JSON файли (*.json)"
        )
        if not path:
            return

        try:
            res = import_json_to_db(self.conn, Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Помилка імпорту", str(e))
            return

        QMessageBox.information(
            self, "Імпортовано",
            f"Політ: {res.flight_uid}\nТочки: {res.points_count}\nGPS: {res.has_gps}"
        )
        self.refresh_flights()

    def selected_flight_uid(self):
        item = self.flights_list.currentItem()
        if not item:
            return None
        return item.text().split(" | ", 1)[0].strip()

    def on_flight_selected(self):
        uid = self.selected_flight_uid()
        if not uid:
            return
        self.current_flight_uid = uid
        self.populate_table(uid)
        self.render_map(uid)
        self.generate_report(uid)

    def populate_table(self, uid: str):
        cur = self.conn.execute(
            "SELECT ts, lat, lon, altitude, speed, battery_pct, wind_speed, wind_dir_deg, agg_status_json, payload_json "
            "FROM telemetry_points WHERE flight_uid=? ORDER BY ts ASC",
            (uid,)
        )
        rows = cur.fetchall()
        headers = ["ts", "lat", "lon", "altitude", "speed", "battery%", "wind_speed", "wind_dir",
                   "aggregates", "payload"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))

        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                txt = "" if val is None else str(val)
                item = QTableWidgetItem(txt)
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                self.table.setItem(r, c, item)

        self.table.resizeColumnsToContents()

    def load_points_for_analysis(self, uid: str):
        cur = self.conn.execute(
            "SELECT ts, lat, lon, altitude, speed, battery_pct, wind_speed, wind_dir_deg, agg_status_json "
            "FROM telemetry_points WHERE flight_uid=? ORDER BY ts ASC",
            (uid,)
        )
        pts = []
        for ts, lat, lon, alt, speed, batt, ws, wd, agg_json in cur.fetchall():
            t_sec = iso_to_epoch_s(ts)
            agg = None
            if agg_json:
                try:
                    agg = json.loads(agg_json)
                except Exception:
                    agg = None

            pts.append({
                "ts": ts,
                "t_sec": t_sec,
                "lat": lat, "lon": lon, "altitude": alt,
                "speed": speed, "battery_pct": batt,
                "wind_speed": ws, "wind_dir_deg": wd,
                "aggregates": agg
            })
        return pts

    def render_map(self, uid: str):
        points = self.load_points_for_analysis(uid)

        gps_pts = []
        for p in points:
            if p["lat"] is None or p["lon"] is None:
                continue

            tooltip = (
                f"<b>Час:</b> {p['ts']}<br/>"
                f"<b>Координати:</b> {p['lat']:.5f}, {p['lon']:.5f}<br/>"
                f"<b>Висота:</b> {p.get('altitude', '-') if p.get('altitude') is not None else '-'} м<br/>"
                f"<b>Швидкість:</b> {p.get('speed', '-') if p.get('speed') is not None else '-'} м/с<br/>"
                f"<b>Заряд батареї:</b> {p.get('battery_pct', '-') if p.get('battery_pct') is not None else '-'}%<br/>"
                f"<b>Вітер:</b> {p.get('wind_speed', '-') if p.get('wind_speed') is not None else '-'} м/с<br/>"
                f"<b>Напрямок вітру (звідки):</b> {p.get('wind_dir_deg', '-') if p.get('wind_dir_deg') is not None else '-'}°<br/>"
            )
            gps_pts.append({"lat": p["lat"], "lon": p["lon"], "tooltip_html": tooltip})

        ellipse_pts = None
        wind_info = None

        est = estimate_endpoint_ellipse(points, window=20)
        if est.get("ok"):
            ellipse_pts = ellipse_polygon(
                est["center_lat"], est["center_lon"],
                est["a_m"], est["b_m"],
                est["rot_deg"],
                n=72
            )

            wind_info = {
                "dir_to_deg": est["rot_deg"],
                "dir_from_deg": (est["rot_deg"] + 180.0) % 360.0,
                "speed": float(points[-1].get("wind_speed") or 0.0),
            }

        build_map_html(MAP_HTML, gps_pts, ellipse_pts=ellipse_pts, wind=wind_info)
        self.web.setUrl(QUrl.fromLocalFile(str(MAP_HTML.resolve())))

    def generate_report(self, uid: str):
        pts = self.load_points_for_analysis(uid)
        if not pts:
            self.report_text.setPlainText("Немає телеметричних точок.")
            return

        first_ts, last_ts = pts[0]["ts"], pts[-1]["ts"]
        latest_agg = pts[-1].get("aggregates")
        latest_batt = pts[-1].get("battery_pct")

        health = assess_aggregates(latest_agg, latest_batt)

        batt_vals = [p["battery_pct"] for p in pts if isinstance(p.get("battery_pct"), (int, float))]
        speed_vals = [p["speed"] for p in pts if isinstance(p.get("speed"), (int, float))]

        def stats(vals):
            if not vals:
                return None
            return min(vals), max(vals), sum(vals) / len(vals)

        bs = stats(batt_vals)
        ss = stats(speed_vals)

        out = []
        out.append(f"Політ: {uid}")
        out.append(f"Часовий інтервал: {first_ts}  →  {last_ts}")
        out.append(f"Точок: {len(pts)}")
        out.append(f"Battery% min/max/avg: {bs[0]:.1f}/{bs[1]:.1f}/{bs[2]:.1f}" if bs else "Battery%: немає даних")
        out.append(f"Speed min/max/avg: {ss[0]:.2f}/{ss[1]:.2f}/{ss[2]:.2f} m/s" if ss else "Speed: немає даних")

        out.append("")
        out.append("=== Агрегати та стан системи (оцінка) ===")
        out.append(f"Рівень ризику: {health.risk_level}")
        out.append(
            f"Можливість продовження польоту: "
            f"{'ТАК' if health.can_continue_flight else 'НІ'}"
        )
        out.append(
            f"Можливість виконання наступного польоту: "
            f"{'ТАК' if health.can_next_flight else 'НІ'}"
        )

        if health.reasons:
            out.append("Причини / зауваження:")

        est = estimate_endpoint_ellipse(pts, window=20)
        out.append("")
        out.append("=== можливе місце завершення (еліпс: батарея + вітер) ===")
        if not est.get("ok"):
            out.append("Еліпс: недоступно")
            for a in est.get("assumptions", []):
                out.append(f"- {a}")
        else:
            out.append(f"Середня швидкість: {est['avg_speed_mps']:.2f} m/s")
            out.append(f"Витрата: {est['burn_rate_pct_per_s']:.4f} %/s")
            out.append(f"Залишок часу: {est['remaining_time_s']/60:.1f} хв")
            out.append(f"Залишкова дистанція (без вітру): {est['remaining_distance_m']:.0f} м")
            out.append(f"Знос вітром: {est['drift_m']:.0f} м (у напрямку {est['rot_deg']:.1f}°)")
            out.append(f"Півосі еліпса: a={est['a_m']:.0f} м, b={est['b_m']:.0f} м")
            if est["assumptions"]:
                out.append("Припущення:")
                for a in est["assumptions"]:
                    out.append(f"- {a}")

        self.report_text.setPlainText("\n".join(out))

    def show_report(self):
        if not self.current_flight_uid:
            QMessageBox.information(self, "Звіт", "Спочатку оберіть політ.")
            return
        self.tabs.setCurrentWidget(self.report_text)


def main():
    app = QApplication([])
    w = MainWindow()
    w.show()
    app.exec()


if __name__ == "__main__":
    main()
