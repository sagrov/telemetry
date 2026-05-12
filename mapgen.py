from pathlib import Path
from typing import Dict, List, Optional, Tuple
import folium


def _deg_to_compass_ua(deg: float) -> str:
    dirs = ["Пн", "ПнСх", "Сх", "ПдСх", "Пд", "ПдЗх", "Зх", "ПнЗх"]
    i = int((deg % 360) / 45 + 0.5) % 8
    return dirs[i]


def build_map_html(
    out_html: Path,
    gps_points: List[Dict[str, str]],
    ellipse_pts: Optional[List[Tuple[float, float]]] = None,
    wind: Optional[Dict[str, float]] = None
) -> None:
    if not gps_points:
        m = folium.Map(location=[0, 0], zoom_start=2, tiles="OpenStreetMap")
        folium.Marker([0, 0], tooltip="GPS-дані відсутні").add_to(m)
        m.save(str(out_html))
        return

    last = gps_points[-1]
    m = folium.Map(location=[last["lat"], last["lon"]], zoom_start=15, tiles="OpenStreetMap")

    m.get_root().html.add_child(folium.Element("""
    <style>
      .leaflet-tooltip { font-size: 13px; }
    </style>
    """))

    folium.map.CustomPane("ellipse", z_index=200).add_to(m)
    folium.map.CustomPane("track",   z_index=350).add_to(m)
    folium.map.CustomPane("points",  z_index=450).add_to(m)


    if ellipse_pts and len(ellipse_pts) >= 10:
        folium.Polygon(
            locations=ellipse_pts,
            weight=2,
            fill=True,
            fill_opacity=0.25,
            color="#3b82f6",
            # Важливо: щоб НЕ перехоплював hover
            interactive=False,
            pane="ellipse",
        ).add_to(m)

    coords = [(p["lat"], p["lon"]) for p in gps_points]
    folium.PolyLine(
        coords,
        weight=3,
        color="#2563eb",
        pane="track",
    ).add_to(m)

    for p in gps_points:
        html = p.get("tooltip_html", "") or "Дані точки відсутні"

        folium.CircleMarker(
            location=(p["lat"], p["lon"]),
            radius=6,
            weight=2,
            color="#2563eb",
            fill=True,
            fill_color="#60a5fa",
            fill_opacity=0.95,
            tooltip=folium.Tooltip(
                html,
                sticky=True,
                parse_html=True,
                direction="top"
            ),
            pane="points",
        ).add_to(m)

    if wind:
        wind_to = float(wind.get("dir_to_deg", 0.0)) % 360.0
        wind_from = float(wind.get("dir_from_deg", (wind_to + 180.0) % 360.0)) % 360.0
        wind_speed = float(wind.get("speed", 0.0))

        to_comp = _deg_to_compass_ua(wind_to)
        from_comp = _deg_to_compass_ua(wind_from)

        hud_html = f"""
        <div style="
            position: fixed;
            top: 10px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 9999;
            background: rgba(255,255,255,0.92);
            padding: 10px 14px;
            border-radius: 12px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            font-size: 14px;
            line-height: 1.25;
            display: flex;
            gap: 12px;
            align-items: center;
            min-width: 360px;
        ">
            <div style="width: 96px; height: 96px; position: relative; flex: 0 0 auto;">

                <div style="position:absolute; top:-10px; left:50%; transform:translateX(-50%); font-size:12px; font-weight:700;">Пн</div>
                <div style="position:absolute; right:-10px; top:50%; transform:translateY(-50%); font-size:12px; font-weight:700;">Сх</div>
                <div style="position:absolute; bottom:-10px; left:50%; transform:translateX(-50%); font-size:12px; font-weight:700;">Пд</div>
                <div style="position:absolute; left:-10px; top:50%; transform:translateY(-50%); font-size:12px; font-weight:700;">Зх</div>

                <div style="
                    position:absolute; inset: 6px;
                    border-radius: 50%;
                    border: 2px solid rgba(0,0,0,0.25);
                    background: rgba(255,255,255,0.6);
                "></div>

                <div style="position:absolute; top:10px; left:50%; transform:translateX(-50%); font-size:11px; opacity:0.75;">0</div>
                <div style="position:absolute; right:10px; top:50%; transform:translateY(-50%); font-size:11px; opacity:0.75;">90</div>
                <div style="position:absolute; bottom:10px; left:50%; transform:translateX(-50%); font-size:11px; opacity:0.75;">180</div>
                <div style="position:absolute; left:10px; top:50%; transform:translateY(-50%); font-size:11px; opacity:0.75;">270</div>

                <div style="
                    position:absolute;
                    inset: 0;
                    transform: rotate({wind_to:.1f}deg);
                    transform-origin: 50% 50%;
                ">
                    <div style="
                        position:absolute;
                        left:50%;
                        top: 18px;
                        width: 3px;
                        height: 60px;
                        transform: translateX(-50%);
                        background: rgba(37,99,235,0.85);
                        border-radius: 2px;
                    "></div>

                    <div style="
                        position:absolute;
                        top: 10px;
                        left: 50%;
                        transform: translateX(-50%);
                        width: 0; height: 0;
                        border-left: 8px solid transparent;
                        border-right: 8px solid transparent;
                        border-bottom: 14px solid rgba(37,99,235,0.95);
                    "></div>
                </div>

                <div style="
                    position:absolute;
                    top: 50%;
                    left: 50%;
                    width: 8px;
                    height: 8px;
                    background: rgba(0,0,0,0.35);
                    border-radius: 50%;
                    transform: translate(-50%,-50%);
                "></div>
            </div>

            <div style="flex: 1 1 auto;">
                <div style="font-weight: 800; margin-bottom: 4px;">Вітер</div>
                <div>Швидкість: <b>{wind_speed:.1f} м/с</b></div>
                <div>Напрямок (КУДИ): <b>{wind_to:.0f}°</b> ({to_comp})</div>
                <div style="opacity:0.75;">Напрямок (ЗВІДКИ): {wind_from:.0f}° ({from_comp})</div>
            </div>
        </div>
        """
        m.get_root().html.add_child(folium.Element(hud_html))

    m.save(str(out_html))
