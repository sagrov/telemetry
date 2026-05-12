import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

EARTH_R = 6371000.0  # метри


@dataclass
class HealthAssessment:
    can_continue_flight: bool
    can_next_flight: bool
    risk_level: str
    reasons: List[str]


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def _get_num(d: Dict[str, Any], key: str) -> Optional[float]:
    v = d.get(key)
    return float(v) if _is_num(v) else None


def _get_bool(d: Dict[str, Any], key: str) -> Optional[bool]:
    v = d.get(key)
    return bool(v) if isinstance(v, bool) else None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def assess_aggregates(
    latest_agg: Optional[Dict[str, Any]],
    battery_pct: Optional[float]
) -> HealthAssessment:

    reasons: List[str] = []
    risk = 0

    def flag(condition: bool, weight: int, message: str):
        nonlocal risk
        if condition:
            risk += weight
            reasons.append(message)

    # --- Батарея (простий відсоток) ---
    if _is_num(battery_pct):
        bp = float(battery_pct)
        flag(bp < 12, 5, "Батарея критично низька (<12%).")
        flag(bp < 20, 3, "Низький заряд батареї (<20%).")
        flag(bp < 30, 2, "Заряд батареї нижче рекомендованого рівня (<30%).")
    else:
        risk += 1
        reasons.append("Відсутні дані про відсоток заряду батареї.")

    # --- Якщо агрегати відсутні ---
    if not latest_agg:
        risk += 3
        reasons.append("Відсутні агреговані дані (немає сигналів стану).")

        # резервні рішення
        can_continue = risk < 6
        can_next = risk < 3 and _is_num(battery_pct) and float(battery_pct) >= 40
        level = "HIGH" if risk >= 9 else ("MEDIUM" if risk >= 5 else "LOW")

        if not can_continue:
            reasons.append("Рекомендація: перервати поточний політ.")
        if not can_next:
            reasons.append("Рекомендація: не починати новий політ.")
        return HealthAssessment(can_continue, can_next, level, reasons)

    # --- Базові прапорці ---
    m_ok = latest_agg.get("motors_ok")
    i_ok = latest_agg.get("imu_ok")
    g_ok = latest_agg.get("gps_ok")
    c_ok = latest_agg.get("comm_ok")

    if isinstance(m_ok, bool):
        flag(not m_ok, 6, "Виявлено несправність двигунів (motors_ok=false).")
    if isinstance(i_ok, bool):
        flag(not i_ok, 5, "Виявлено збій/нестабільність IMU (imu_ok=false).")
    if isinstance(g_ok, bool):
        flag(not g_ok, 2, "Відсутній сигнал GPS (gps_ok=false).")
    if isinstance(c_ok, bool):
        flag(not c_ok, 2, "Нестабільний канал зв’язку (comm_ok=false).")

    # --- Температура (legacy) ---
    legacy_temp = latest_agg.get("temp_c")
    if _is_num(legacy_temp):
        t = float(legacy_temp)
        flag(t > 75, 2, "Підвищена температура на борту (>75°C).")
        flag(t > 85, 4, "Критична температура на борту (>85°C).")

    battery = latest_agg.get("battery") if isinstance(latest_agg.get("battery"), dict) else {}
    nav = latest_agg.get("navigation") if isinstance(latest_agg.get("navigation"), dict) else {}
    comm = latest_agg.get("comm") if isinstance(latest_agg.get("comm"), dict) else {}
    env = latest_agg.get("env") if isinstance(latest_agg.get("env"), dict) else {}

    # =========================
    # Battery (детально)
    # =========================
    if isinstance(battery, dict) and battery:
        batt_ok = _get_bool(battery, "health_ok")
        if batt_ok is not None:
            flag(not batt_ok, 5, "Прапорець стану батареї вказує на несправність (battery.health_ok=false).")

        v = _get_num(battery, "voltage_v")
        i = _get_num(battery, "current_a")
        temp = _get_num(battery, "temp_c")
        sag = _get_num(battery, "sag_ratio")  # 0.0..1.0 (чим більше — тим гірше)
        cell_min = _get_num(battery, "cell_min_v")
        cell_max = _get_num(battery, "cell_max_v")
        ir = _get_num(battery, "ir_mohm")

        if temp is not None:
            flag(temp > 65, 2, f"Температура батареї підвищена ({temp:.1f}°C > 65°C).")
            flag(temp > 75, 4, f"Температура батареї критична ({temp:.1f}°C > 75°C).")

        if sag is not None:
            flag(sag > 0.12, 2, f"Підвищене просідання напруги батареї (sag_ratio={sag:.3f}).")
            flag(sag > 0.20, 4, f"Критичне просідання напруги батареї (sag_ratio={sag:.3f}).")

        if cell_min is not None and cell_max is not None:
            delta = cell_max - cell_min
            flag(delta > 0.15, 2, f"Підвищений дисбаланс комірок (Δ={delta:.2f}В).")
            flag(delta > 0.25, 4, f"Критичний дисбаланс комірок (Δ={delta:.2f}В).")

        if ir is not None:
            flag(ir > 35, 2, f"Підвищений внутрішній опір батареї ({ir:.1f} мОм).")
            flag(ir > 55, 4, f"Критичний внутрішній опір батареї ({ir:.1f} мОм).")

        if i is not None:
            flag(i > 60, 2, f"Підвищений струм споживання батареї ({i:.1f}A).")
            flag(i > 90, 4, f"Критичний струм споживання батареї ({i:.1f}A).")

        if v is not None:
            flag(v < 13.0, 2, f"Низька напруга батареї ({v:.1f}В).")
            flag(v < 12.2, 4, f"Критично низька напруга батареї ({v:.1f}В).")
    else:
        risk += 1
        reasons.append("Відсутня детальна телеметрія батареї.")

    # =========================
    # Навігація
    # =========================
    if isinstance(nav, dict) and nav:
        gps_ok = _get_bool(nav, "gps_ok")
        imu_ok = _get_bool(nav, "imu_ok")
        baro_ok = _get_bool(nav, "baro_ok")

        if gps_ok is not None:
            flag(not gps_ok, 3, "Статус GPS вказує на збій (navigation.gps_ok=false).")
        if imu_ok is not None:
            flag(not imu_ok, 5, "Статус IMU вказує на збій (navigation.imu_ok=false).")
        if baro_ok is not None:
            flag(not baro_ok, 2, "Статус барометра вказує на збій (navigation.baro_ok=false).")

        sats = _get_num(nav, "sats")
        hdop = _get_num(nav, "hdop")
        imu_vibe = _get_num(nav, "imu_vibe")

        if sats is not None:
            flag(sats < 6, 2, f"Мало супутників GPS ({sats:.0f} < 6).")
            flag(sats < 4, 4, f"Критично мало супутників GPS ({sats:.0f} < 4).")

        if hdop is not None:
            flag(hdop > 2.0, 2, f"Погіршена точність GPS (HDOP={hdop:.2f} > 2.0).")
            flag(hdop > 3.5, 4, f"Дуже погана точність GPS (HDOP={hdop:.2f} > 3.5).")

        if imu_vibe is not None:
            flag(imu_vibe > 1.2, 2, f"Підвищена вібрація IMU (imu_vibe={imu_vibe:.2f}).")
            flag(imu_vibe > 2.0, 4, f"Критична вібрація IMU (imu_vibe={imu_vibe:.2f}).")
    else:
        risk += 1
        reasons.append("Відсутня навігаційна телеметрія.")

    # =========================
    # Звʼязок
    # =========================
    if isinstance(comm, dict) and comm:
        comm_ok = _get_bool(comm, "comm_ok")
        if comm_ok is not None:
            flag(not comm_ok, 3, "Статус зв’язку вказує на збій (comm.comm_ok=false).")

        rssi = _get_num(comm, "rssi_dbm")
        snr = _get_num(comm, "snr_db")
        lq = _get_num(comm, "link_quality")  # 0..1 або 0..100
        loss = _get_num(comm, "loss_rate")   # 0..1

        if rssi is not None:
            flag(rssi < -85, 2, f"Низький RSSI ({rssi:.0f} dBm).")
            flag(rssi < -95, 4, f"Критично низький RSSI ({rssi:.0f} dBm).")
        if snr is not None:
            flag(snr < 6, 2, f"Низький SNR ({snr:.1f} dB).")
            flag(snr < 2, 4, f"Критично низький SNR ({snr:.1f} dB).")

        if lq is not None:
            lq_norm = lq / 100.0 if lq > 1.5 else lq
            flag(lq_norm < 0.45, 2, f"Низька якість каналу ({lq_norm:.2f}).")
            flag(lq_norm < 0.25, 4, f"Критично низька якість каналу ({lq_norm:.2f}).")

        if loss is not None:
            flag(loss > 0.10, 2, f"Підвищені втрати пакетів ({loss:.2f}).")
            flag(loss > 0.20, 4, f"Критичні втрати пакетів ({loss:.2f}).")
    else:
        risk += 1
        reasons.append("Відсутня телеметрія звʼязку.")

    # =========================
    # Середовище / температура на борту
    # =========================
    if isinstance(env, dict) and env:
        temp = _get_num(env, "temp_c")
        if temp is not None:
            flag(temp > 75, 2, f"Підвищена температура на борту ({temp:.1f}°C).")
            flag(temp > 85, 4, f"Критична температура на борту ({temp:.1f}°C).")

    # =========================
    # Ротори (по кожному)
    # =========================
    rotors = latest_agg.get("rotors")
    if isinstance(rotors, list) and rotors:
        rpms: List[float] = []
        currents: List[float] = []
        vibes: List[float] = []

        for idx, r in enumerate(rotors):
            if not isinstance(r, dict):
                continue
            rid = r.get("id", idx + 1)

            ok = _get_bool(r, "health_ok")
            if ok is not None:
                flag(not ok, 5, f"Ротор {rid}: прапорець стану вказує на несправність (health_ok=false).")

            rpm = _get_num(r, "rpm")
            temp = _get_num(r, "temp_c")
            esc_temp = _get_num(r, "esc_temp_c")
            cur = _get_num(r, "current_a")
            vibe = _get_num(r, "vibration_rms_g")

            if rpm is not None:
                rpms.append(rpm)
                flag(rpm < 200, 3, f"Ротор {rid}: занадто низькі оберти (RPM={rpm:.0f}).")
                flag(rpm < 120, 5, f"Ротор {rid}: критично низькі оберти (RPM={rpm:.0f}).")
            else:
                reasons.append(f"Ротор {rid}: відсутні дані RPM.")

            if temp is not None:
                flag(temp > 80, 2, f"Ротор {rid}: висока температура двигуна ({temp:.1f}°C).")
                flag(temp > 95, 4, f"Ротор {rid}: критична температура двигуна ({temp:.1f}°C).")

            if esc_temp is not None:
                flag(esc_temp > 85, 2, f"Ротор {rid}: висока температура ESC ({esc_temp:.1f}°C).")
                flag(esc_temp > 100, 4, f"Ротор {rid}: критична температура ESC ({esc_temp:.1f}°C).")

            if cur is not None:
                currents.append(cur)
                flag(cur > 18, 2, f"Ротор {rid}: підвищений струм ({cur:.1f}A).")
                flag(cur > 28, 4, f"Ротор {rid}: критичний струм ({cur:.1f}A).")

            if vibe is not None:
                vibes.append(vibe)
                flag(vibe > 1.2, 2, f"Ротор {rid}: підвищена вібрація ({vibe:.2f} g).")
                flag(vibe > 2.0, 4, f"Ротор {rid}: критична вібрація ({vibe:.2f} g).")

        # Перевірка розбалансу між роторами
        if len(rpms) >= 3:
            rmin = min(rpms)
            rmax = max(rpms)
            if rmax > 0:
                spread = (rmax - rmin) / max(1.0, rmax)
                flag(spread > 0.18, 2, f"Підвищений розбаланс RPM між роторами (spread={spread:.2f}).")
                flag(spread > 0.28, 4, f"Критичний розбаланс RPM між роторами (spread={spread:.2f}).")

        if len(currents) >= 3:
            cmin = min(currents)
            cmax = max(currents)
            if cmax > 0:
                spread = (cmax - cmin) / max(1e-6, cmax)
                flag(spread > 0.30, 2, f"Підвищений розбаланс струму між роторами (spread={spread:.2f}).")
                flag(spread > 0.45, 4, f"Критичний розбаланс струму між роторами (spread={spread:.2f}).")

    else:
        # якщо motors_ok існує і true — не штрафуємо сильно
        if not isinstance(latest_agg.get("motors_ok"), bool):
            risk += 2
            reasons.append("Відсутня телеметрія роторів (немає датчиків по кожному ротору).")

    # ---------------------------
    # Рішення
    # ---------------------------
    can_continue = risk < 7
    can_next = (risk < 4) and _is_num(battery_pct) and float(battery_pct) >= 45

    if risk >= 12:
        level = "HIGH"
    elif risk >= 7:
        level = "MEDIUM"
    else:
        level = "LOW"

    if not can_continue:
        reasons.append("Рекомендація: перервати поточний політ (ризик занадто високий).")
    if not can_next:
        reasons.append("Рекомендація: не починати новий політ (потрібне обслуговування/заряд).")

    return HealthAssessment(can_continue, can_next, level, reasons)


# ===============================
#  Гео-допоміжні функції
# ===============================

def _dest_point(lat_deg: float, lon_deg: float, bearing_deg: float, dist_m: float) -> Tuple[float, float]:
    """Точка призначення від lat/lon, курс (0=Пн), відстань у метрах."""
    lat1 = math.radians(lat_deg)
    lon1 = math.radians(lon_deg)
    brng = math.radians(bearing_deg)
    ang = dist_m / EARTH_R

    lat2 = math.asin(math.sin(lat1) * math.cos(ang) + math.cos(lat1) * math.sin(ang) * math.cos(brng))
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(ang) * math.cos(lat1),
        math.cos(ang) - math.sin(lat1) * math.sin(lat2)
    )

    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180  # нормалізація lon


# ===============================
#  Оцінка еліпса кінцевої зони (final)
# ===============================

def estimate_endpoint_ellipse(points: List[Dict[str, Any]], window: int = 20) -> Dict[str, Any]:
    assumptions: List[str] = []

    if not points:
        return {"ok": False, "assumptions": ["Немає точок."]}

    gps = [p for p in points if isinstance(p.get("lat"), (int, float)) and isinstance(p.get("lon"), (int, float))]
    if not gps:
        return {"ok": False, "assumptions": ["Немає GPS-точок."]}

    last_gps = gps[-1]
    last_lat = float(last_gps["lat"])
    last_lon = float(last_gps["lon"])

    tail = points[-window:] if len(points) >= window else points

    # швидкість витрати за останнє вікно
    tb = [(p.get("t_sec"), p.get("battery_pct")) for p in tail
          if isinstance(p.get("t_sec"), (int, float)) and isinstance(p.get("battery_pct"), (int, float))]

    burn_rate = None
    if len(tb) >= 2:
        t0, b0 = tb[0]
        t1, b1 = tb[-1]
        dt = max(1.0, float(t1 - t0))
        db = max(0.0, float(b0 - b1))
        if db > 0:
            burn_rate = db / dt
        else:
            assumptions.append("Заряд у вікні не зменшився; використано типову витрату 0.03%/с.")
    else:
        assumptions.append("Недостатньо вимірів батареї; використано типову витрату 0.03%/с.")

    if burn_rate is None:
        burn_rate = 0.03  # % за секунду

    last_batt = points[-1].get("battery_pct")
    if not isinstance(last_batt, (int, float)):
        assumptions.append("Останній заряд відсутній; прийнято 20%.")
        last_batt = 20.0

    remaining_time_s = max(0.0, float(last_batt) / burn_rate)
    remaining_time_s = min(remaining_time_s, 4 * 3600.0)

    sp = [p.get("speed") for p in tail if isinstance(p.get("speed"), (int, float))]
    if sp:
        avg_speed = sum(map(float, sp)) / len(sp)
    else:
        avg_speed = 10.0
        assumptions.append("Швидкість у вікні відсутня; використано типове значення 10 м/с.")
    avg_speed = max(1.0, float(avg_speed))

    remaining_distance_m = avg_speed * remaining_time_s

    wind_speed = points[-1].get("wind_speed")
    wind_from = points[-1].get("wind_dir_deg")
    if not isinstance(wind_speed, (int, float)) or not isinstance(wind_from, (int, float)):
        assumptions.append("Дані вітру відсутні/неповні; прийнято 0 м/с.")
        wind_speed = 0.0
        wind_from = 0.0

    wind_speed = max(0.0, float(wind_speed))
    wind_to = (float(wind_from) + 180.0) % 360.0

    drift_m = wind_speed * remaining_time_s

    # центр еліпса зміщений за вітром
    center_lat, center_lon = _dest_point(last_lat, last_lon, wind_to, drift_m)

    # півосі еліпса
    a_m = remaining_distance_m + drift_m
    k = 0.75
    b_m = remaining_distance_m * k

    # обмеження
    a_m = min(a_m, 60000.0)
    b_m = min(b_m, 60000.0)

    return {
        "ok": True,
        "last_lat": last_lat,
        "last_lon": last_lon,
        "center_lat": center_lat,
        "center_lon": center_lon,
        "a_m": a_m,
        "b_m": b_m,
        "rot_deg": wind_to,
        "remaining_time_s": remaining_time_s,
        "remaining_distance_m": remaining_distance_m,
        "drift_m": drift_m,
        "avg_speed_mps": avg_speed,
        "burn_rate_pct_per_s": burn_rate,
        "assumptions": assumptions,
    }


def ellipse_polygon(center_lat: float, center_lon: float, a_m: float, b_m: float, rot_deg: float, n: int = 72) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    rot = math.radians(rot_deg)

    for i in range(n):
        t = 2 * math.pi * i / n

        x = a_m * math.cos(t)
        y = b_m * math.sin(t)

        # поворот
        xr = x * math.cos(rot) - y * math.sin(rot)
        yr = x * math.sin(rot) + y * math.cos(rot)

        bearing = (math.degrees(math.atan2(yr, xr)) + 360.0) % 360.0
        dist = math.hypot(xr, yr)

        lat, lon = _dest_point(center_lat, center_lon, bearing, dist)
        pts.append((lat, lon))

    return pts
