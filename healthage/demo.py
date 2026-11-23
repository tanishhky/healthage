"""Synthetic demo person, so the app can be explored without connecting
anything. Seeded, so the shape is the same on every run; dates move with the
calendar so the demo never looks stale. No real person's data is used."""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

WEEKDAY_STEPS = [5200, 7800, 8600, 5600, 7200, 9800, 6400]   # Mon..Sun
OFFSET_S = -18000                                             # UTC-5


def profile(today: date) -> dict:
    return {
        "dob": (today - timedelta(days=int(34.4 * 365.2425))).isoformat(),
        "sex": "male",
        "labs": {"date": (today - timedelta(days=120)).isoformat(), "hba1c_pct": 5.5,
                 "triglycerides_mg_dl": 165, "hdl_mg_dl": 42},
        "insulin_resistant": None,
        "max_cardio_min_week": 180,
        "name": "Demo person",
    }


def data(today: date, n_days: int = 150) -> dict:
    rng = random.Random(20261105)
    days = {}
    weight = 87.4
    for i in range(n_days, 0, -1):
        d = today - timedelta(days=i)
        wd = d.weekday()
        t = n_days - i
        rec = {}
        steps = int(WEEKDAY_STEPS[wd] * math.exp(rng.gauss(0, 0.28)))
        if rng.random() < 0.04:
            steps = int(rng.uniform(13000, 19000))          # long walk days
        rec["steps"] = steps
        cardio = 0.0
        if wd in (1, 5) and rng.random() < 0.55:
            cardio = round(rng.uniform(8, 24))
        peak = round(rng.uniform(0, 3)) if cardio else 0
        fat = max(0.0, (steps - 2500) / 1000 * 4.6 + rng.gauss(0, 4))
        rec["azm"] = {"total": round(fat + 2 * cardio + 2 * peak, 1), "fat_burn_min": round(fat, 1),
                      "cardio_min": float(cardio), "peak_min": float(peak)}
        rec["rhr"] = round(66.0 + 0.012 * t + rng.gauss(0, 1.5) - (0.8 if cardio else 0))
        rec["rhr_source"] = "Demo band"
        if rng.random() < 0.93:
            weekend = wd in (4, 5)                          # Fri and Sat nights run late
            onset_min = (60 if weekend else 15) + rng.gauss(0, 55 if weekend else 38)
            hours = min(9.6, max(4.2, rng.gauss(7.2 if weekend else 6.45, 0.65)))
            start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(
                minutes=onset_min, seconds=-OFFSET_S)
            end = start + timedelta(hours=hours + rng.uniform(0.2, 0.6))
            rec_sleep = {"hours": round(hours, 2),
                         "start": start.isoformat().replace("+00:00", "Z"),
                         "end": end.isoformat().replace("+00:00", "Z"), "offset_s": OFFSET_S}
            if rng.random() < 0.12:
                rec_sleep["nap_hours"] = round(rng.uniform(0.3, 1.2), 2)
            rec["sleep"] = rec_sleep
        weight += -0.018 + rng.gauss(0, 0.05)
        if wd in (0, 3, 6):
            rec["weight_kg"] = round(weight + rng.gauss(0, 0.3), 2)
        if i == 45:
            rec["vo2max"] = 41.6
        days[d.isoformat()] = rec
    return {"version": 1, "days": days,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "range": {"start": min(days), "end": max(days)}}
