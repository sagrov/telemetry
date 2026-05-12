import json
from pathlib import Path

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Range Planner</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body, #map { height: 100%; margin: 0; }
    .hud{
      position:absolute; z-index:9999; top:10px; left:10px;
      background:rgba(255,255,255,0.92); padding:10px 12px;
      border-radius:10px; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;
      font-size:14px; max-width:420px;
    }
    .hud b{font-weight:650;}
  </style>
</head>
<body>
<div class="hud" id="hud"></div>
<div id="map"></div>

<script>
const cfg = {CFG_JSON};

const map = L.map('map').setView([cfg.center.lat, cfg.center.lon], cfg.center.zoom);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap'
}).addTo(map);

let startMarker=null, oneCircle=null, rtCircle=null, dirLine=null;

function setHud(html){ document.getElementById("hud").innerHTML = html; }

function draw(lat, lon){
  if(startMarker) map.removeLayer(startMarker);
  if(oneCircle) map.removeLayer(oneCircle);
  if(rtCircle) map.removeLayer(rtCircle);
  if(dirLine) map.removeLayer(dirLine);

  startMarker = L.marker([lat, lon]).addTo(map).bindPopup("Start").openPopup();

  if(cfg.ranges.one_way_m > 0){
    oneCircle = L.circle([lat, lon], { radius: cfg.ranges.one_way_m }).addTo(map);
  }
  if(cfg.ranges.round_trip_m > 0){
    rtCircle = L.circle([lat, lon], { radius: cfg.ranges.round_trip_m }).addTo(map);
  }

  // коротка лінія напрямку (bearing)
  const br = cfg.inputs.bearing_deg * Math.PI/180.0;
  const dist = 1200; // 1.2 км
  const R = 6378137;
  const lat1 = lat*Math.PI/180.0;
  const lon1 = lon*Math.PI/180.0;
  const lat2 = Math.asin(Math.sin(lat1)*Math.cos(dist/R) + Math.cos(lat1)*Math.sin(dist/R)*Math.cos(br));
  const lon2 = lon1 + Math.atan2(Math.sin(br)*Math.sin(dist/R)*Math.cos(lat1), Math.cos(dist/R)-Math.sin(lat1)*Math.sin(lat2));
  dirLine = L.polyline([[lat,lon],[lat2*180/Math.PI, lon2*180/Math.PI]]).addTo(map);

  setHud(
    "<b>Planner</b><br/>Click map to pick start.<br/><br/>" +
    "<b>Inputs</b><br/>" +
    "Battery: " + cfg.inputs.battery_percent.toFixed(1) + "%<br/>" +
    "Endurance@100%: " + cfg.inputs.endurance_minutes_100.toFixed(1) + " min<br/>" +
    "Airspeed: " + cfg.inputs.airspeed_mps.toFixed(1) + " m/s<br/>" +
    "Wind: " + cfg.inputs.wind_speed_mps.toFixed(1) + " m/s, FROM " + cfg.inputs.wind_dir_from_deg.toFixed(0) + "°<br/>" +
    "Bearing TO: " + cfg.inputs.bearing_deg.toFixed(0) + "°<br/><br/>" +
    "<b>Results</b><br/>" +
    "One-way: " + (cfg.ranges.one_way_m/1000).toFixed(2) + " km<br/>" +
    "Round-trip max offset: " + (cfg.ranges.round_trip_m/1000).toFixed(2) + " km<br/>" +
    (cfg.notes ? ("<br/><b>Note:</b> " + cfg.notes) : "")
  );
}

draw(cfg.center.lat, cfg.center.lon);

map.on('click', function(e){
  const payload = {lat: e.latlng.lat, lon: e.latlng.lng};
  fetch(cfg.pick_url, {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  }).then(r => r.json()).then(_ => draw(payload.lat, payload.lon))
    .catch(err => setHud("Failed to send pick: " + err));
});
</script>
</body>
</html>
"""

def write_planner_map_html(
    out_path: str,
    pick_url: str,
    center_lat: float,
    center_lon: float,
    zoom: int,
    inputs: dict,
    one_way_m: float,
    round_trip_m: float,
    notes: str = ""
) -> str:
    cfg = {
        "pick_url": pick_url,
        "center": {"lat": center_lat, "lon": center_lon, "zoom": zoom},
        "inputs": inputs,
        "ranges": {"one_way_m": float(one_way_m), "round_trip_m": float(round_trip_m)},
        "notes": notes or ""
    }
    html = HTML_TEMPLATE.replace("{CFG_JSON}", json.dumps(cfg, ensure_ascii=False))
    p = Path(out_path)
    p.write_text(html, encoding="utf-8")
    return str(p.resolve())
