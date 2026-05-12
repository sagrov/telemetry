import math
from dataclasses import dataclass

def _norm_deg(x: float) -> float:
    x = x % 360.0
    return x + 360.0 if x < 0 else x

def _deg2rad(x: float) -> float:
    return x * math.pi / 180.0

@dataclass
class RangeInputs:
    battery_percent: float           # 0..100
    endurance_minutes_100: float     # хв на 100% батареї (крейсер)
    airspeed_mps: float              # Va: швидкість по повітрю
    wind_speed_mps: float            # W
    wind_dir_from_deg: float         # FROM
    bearing_deg: float               # TO

@dataclass
class RangeOutputs:
    v_out_mps: float
    v_back_mps: float
    one_way_m: float
    round_trip_max_m: float
    notes: str

def estimate_ranges(inp: RangeInputs) -> RangeOutputs:
    batt = max(0.0, min(100.0, float(inp.battery_percent)))
    endurance_sec = max(1.0, float(inp.endurance_minutes_100) * 60.0)
    T = endurance_sec * (batt / 100.0)

    Va = max(0.1, float(inp.airspeed_mps))
    W = max(0.0, float(inp.wind_speed_mps))

    bearing = _norm_deg(float(inp.bearing_deg))
    wind_to = _norm_deg(float(inp.wind_dir_from_deg) + 180.0)  # FROM -> TO

    # проєкція вітру на курс (поздовжня компонента)
    delta = _deg2rad(_norm_deg(bearing - wind_to))
    w_par = W * math.cos(delta)

    v_out = Va + w_par
    v_back = Va - w_par

    notes = []
    if v_out <= 0.1:
        notes.append("Неможливо летіти 'туди' (зустрічний вітер занадто сильний для цього курсу).")
    if v_back <= 0.1:
        notes.append("Неможливо повернутися (зустрічний вітер на звороті занадто сильний).")

    one_way = max(0.0, v_out) * T

    round_trip = 0.0
    if v_out > 0.1 and v_back > 0.1:
        # max віддалення, щоб вистачило на туди+назад (гармонічне середнє швидкостей)
        round_trip = T * (v_out * v_back) / (v_out + v_back)

    return RangeOutputs(
        v_out_mps=v_out,
        v_back_mps=v_back,
        one_way_m=one_way,
        round_trip_max_m=round_trip,
        notes=" ".join(notes)
    )
