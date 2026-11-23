"""
Coachable Health Age (CHA) engine.

Implements the health-age measure and the feasibility-constrained coaching
optimizer from Lee, Esmaeilpour et al., "Personalized coaching via coachable
health age and counterfactual wearable trajectories" (Google Research,
arXiv:2611.08568, 2026). This is an independent implementation; it is not
affiliated with or endorsed by the authors or Google.

Reproduced as published (Methods 4.1 and 4.3):
  * CHA, Eqs. 1-9: resting HR, daily steps, main sleep and weekly Active Zone
    Minutes mapped to literature log hazard ratios, collinearity weights
    (0.8790 sleep, 0.7066 RHR, 0.6950 AZM, 0.4773 steps), G = 0.1, floor 18.
  * Optional domains, Eqs. 10-13: VO2 max, sleep-midpoint regularity,
    insulin resistance (weight 1 when measured).
  * Weekly aggregation over valid days (Eq. 26); effort (Eq. 55) with the
    paper's floors; score = benefit / (1 + effort) (Eq. 56); 0.1-year bar;
    targets limited to steps, sleep and cardio minutes (action-outcome firewall).

Replaced: SensorWM, the paper's world model, was pretrained on 96,957 people
and is not public. The projection here is a transparent single-person model:
steps and sleep follow the plan (4-week ramp); AZM adds fat-burn minutes from
extra steps (rate fitted on the user's own days) plus 2 per cardio-zone
minute; resting HR closes 20% of the gap each week to a level up to 9% lower
at 150 cardio-zone minutes a week (Reimers, Knapp & Reimers 2018, J Clin Med
7:503, men, endurance training; 4.5% is the pessimistic band).

Extensions, labelled as such in the output: dominance pruning of redundant
targets, movement split into walking / cardio / both, and a sleep-timing lever.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Paper constants
# ---------------------------------------------------------------------------

G = 0.1
W = {"sleep": 0.8790, "rhr": 0.7066, "azm": 0.6950, "steps": 0.4773}
HEALTH_AGE_FLOOR = 18.0
MIN_BENEFIT_YEARS = 0.1
EFFORT_FLOORS = {"steps": 500.0, "sleep_min": 15.0, "cardio_min_day": 10.0}
SLEEP_CAP_H = 9.0
HORIZON_WEEKS = 12

RAMP_WEEKS = 4
RHR_KAPPA = 0.20
RHR_EFFECT_CENTRAL = 0.09
RHR_EFFECT_LOW = 0.045
FULL_DOSE_CARDIO = 150.0

STEPS_MIN_WORN = 100
SLEEP_RANGE_H = (2.0, 16.0)
RHR_RANGE = (35.0, 120.0)

LN = math.log

CORE = ["rhr", "steps", "sleep", "azm"]
LABELS = {
    "rhr": "Resting heart rate", "steps": "Daily steps", "sleep": "Sleep duration",
    "azm": "Active Zone Minutes", "regularity": "Sleep timing",
    "ir": "Insulin resistance", "vo2": "Cardio fitness (VO2 max)",
}
REFERENCE = {
    "rhr": "60 bpm or lower", "steps": "8,000 a day; no gain past 9,000",
    "sleep": "6 to 9 hours", "azm": "under 115 a week counts 0; gains up to 500",
    "regularity": "midpoint spread 30 min or less", "ir": "absent",
    "vo2": "expected for age, sex and weight",
}


# ---------------------------------------------------------------------------
# Per-domain log hazard ratios (Eqs. 3-6, 10-13)
# ---------------------------------------------------------------------------

def loghr_rhr(rhr: float) -> float:
    if rhr <= 60:
        return 0.0
    if rhr <= 70:
        return (rhr - 60) / 10 * LN(1.08)
    return LN(1.08) + (rhr - 70) / 20 * (LN(1.33) - LN(1.08))


def loghr_steps(steps: float) -> float:
    return (min(steps, 9000.0) - 8000.0) / 1000.0 * LN(0.88)


def loghr_sleep(hours: float) -> float:
    if hours < 6.0:
        return (6.0 - hours) * LN(1.20)
    if hours <= 9.0:
        return 0.0
    return (hours - 9.0) * LN(1.34)


def loghr_azm(a: float) -> float:
    if a < 115:
        return 0.0
    if a < 245:
        return (a - 115) / 130 * LN(0.76)
    if a < 418:
        return LN(0.76) + (a - 245) / 173 * (LN(0.71) - LN(0.76))
    if a < 500:
        return LN(0.71) + (a - 418) / 82 * (LN(0.68) - LN(0.71))
    return LN(0.68)


def expected_vo2(age: float, female: bool, weight_kg: float) -> float:
    return 79.9 - 0.39 * age - 13.7 * (1 if female else 0) - 0.127 * weight_kg * 2.20462


def loghr_vo2(vo2: float, age: float, female: bool, weight_kg: float) -> float:
    return (vo2 - expected_vo2(age, female, weight_kg)) / 3.5 * LN(0.88)


def loghr_regularity(sd_min: float) -> float:
    sd = min(sd_min, 120.0)
    if sd <= 30:
        return 0.0
    if sd <= 60:
        return (sd - 30) / 30 * LN(1.16)
    if sd <= 90:
        return LN(1.16) + (sd - 60) / 30 * (LN(1.52) - LN(1.16))
    return LN(1.52) + (sd - 90) / 30 * (LN(2.11) - LN(1.52))


def loghr_ir(flag: bool) -> float:
    return LN(2.0) if flag else 0.0


def cha(age: float, *, rhr=None, steps=None, sleep_h=None, azm_week=None,
        sleep_mid_sd=None, insulin_resistant=None, vo2max=None, weight_kg=None,
        female: bool = False) -> dict:
    """Coachable Health Age (Eqs. 1-2). Missing core domains count 0 and are
    listed in `missing`."""
    parts: Dict[str, float] = {}
    missing: List[str] = []
    for key, value, fn in (("rhr", rhr, loghr_rhr), ("steps", steps, loghr_steps),
                           ("sleep", sleep_h, loghr_sleep), ("azm", azm_week, loghr_azm)):
        if value is None:
            missing.append(key)
        else:
            parts[key] = W[key] * fn(value) / G
    if sleep_mid_sd is not None:
        parts["regularity"] = loghr_regularity(sleep_mid_sd) / G
    if insulin_resistant is not None:
        parts["ir"] = loghr_ir(insulin_resistant) / G
    if vo2max is not None and weight_kg:
        parts["vo2"] = loghr_vo2(vo2max, age, female, weight_kg) / G
    health_age = max(age + sum(parts.values()), HEALTH_AGE_FLOOR)
    return {"age": age, "health_age": health_age, "delta": health_age - age,
            "parts": parts, "missing": missing}


# ---------------------------------------------------------------------------
# Cleaning and aggregation
# ---------------------------------------------------------------------------

def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_midpoint_minutes(sleep: dict) -> Optional[float]:
    """Minutes after local midnight of the main sleep's midpoint, using the
    session's own UTC offset (so travel and DST need no timezone setting)."""
    if not sleep or not sleep.get("start") or not sleep.get("end"):
        return None
    t0, t1 = _ts(sleep["start"]), _ts(sleep["end"])
    if not timedelta(0) < t1 - t0 <= timedelta(hours=16):
        return None
    mid = t0 + (t1 - t0) / 2 + timedelta(seconds=sleep.get("offset_s") or 0)
    return (mid.hour * 60 + mid.minute + mid.second / 60.0) % 1440


def circular_sd_minutes(mins: List[float]) -> float:
    """SD of clock times as deviations from their circular mean."""
    ang = [m / 1440 * 2 * math.pi for m in mins]
    mean = math.atan2(sum(math.sin(a) for a in ang), sum(math.cos(a) for a in ang))
    devs = [((a - mean + math.pi) % (2 * math.pi) - math.pi) / (2 * math.pi) * 1440 for a in ang]
    return statistics.pstdev(devs)


def clean_days(data: dict, today: date) -> List[dict]:
    """Normalize stored days. Steps and AZM for today or later are partial."""
    out = []
    for ds in sorted(data.get("days", {})):
        r = data["days"][ds]
        d = date.fromisoformat(ds)
        steps = r.get("steps") or 0
        steps_ok = steps >= STEPS_MIN_WORN and d < today
        sl = r.get("sleep") or {}
        sh = sl.get("hours")
        sleep_ok = sh is not None and SLEEP_RANGE_H[0] <= sh <= SLEEP_RANGE_H[1]
        rhr = r.get("rhr")
        rhr_ok = rhr is not None and RHR_RANGE[0] <= rhr <= RHR_RANGE[1]
        azm = r.get("azm") or {}
        has_azm = steps_ok and azm.get("total") is not None
        out.append({
            "date": d,
            "steps": float(steps) if steps_ok else None,
            "sleep_h": float(sh) if sleep_ok else None,
            "sleep_mid": local_midpoint_minutes(sl) if sleep_ok else None,
            "rhr": float(rhr) if rhr_ok else None,
            "rhr_source": r.get("rhr_source"),
            "azm": float(azm["total"]) if has_azm else None,
            "cardio": float((azm.get("cardio_min") or 0) + (azm.get("peak_min") or 0)) if has_azm else None,
            "fatburn": float(azm.get("fat_burn_min") or 0) if has_azm else None,
            "weight_kg": r.get("weight_kg"),
            "vo2max": r.get("vo2max"),
        })
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def weekly(days: List[dict]) -> List[dict]:
    buckets: Dict[date, List[dict]] = {}
    for d in days:
        buckets.setdefault(d["date"] - timedelta(days=d["date"].weekday()), []).append(d)
    out = []
    for monday in sorted(buckets):
        ds = buckets[monday]
        azm_day = _mean([d["azm"] for d in ds])
        mids = [d["sleep_mid"] for d in ds if d["sleep_mid"] is not None]
        out.append({
            "week": monday,
            "complete_days": sum(1 for d in ds if d["steps"] is not None and d["sleep_h"] is not None
                                 and d["rhr"] is not None),
            "steps": _mean([d["steps"] for d in ds]),
            "sleep_h": _mean([d["sleep_h"] for d in ds]),
            "rhr": _mean([d["rhr"] for d in ds]),
            "azm_week": azm_day * 7 if azm_day is not None else None,
            "cardio_day": _mean([d["cardio"] for d in ds]),
            "sleep_mid_sd": circular_sd_minutes(mids) if len(mids) >= 4 else None,
        })
    return out


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

@dataclass
class Baseline:
    as_of: date
    age: float
    steps: Optional[float]
    sleep_h: Optional[float]
    rhr: Optional[float]
    azm_week: Optional[float]
    cardio_week: float
    sleep_mid_sd: Optional[float]
    weight_kg: Optional[float]
    vo2max: Optional[float]
    vo2_date: Optional[str]
    n: dict
    window: dict
    sigma: dict
    sigma_floor: dict
    fatburn_per_1k: float
    fatburn_n: int


def age_on(dob: date, d: date) -> float:
    return (d - dob).days / 365.2425


def build_baseline(days: List[dict], weeks: List[dict], as_of: date, dob: date) -> Baseline:
    past = [d for d in days if d["date"] <= as_of]

    def window_mean(key, min_n=5):
        for span in (28, 90):
            vals = [d[key] for d in past if d["date"] > as_of - timedelta(days=span) and d[key] is not None]
            if len(vals) >= min_n:
                return sum(vals) / len(vals), len(vals), span
        return None, 0, None

    steps, n_steps, w_steps = window_mean("steps")
    sleep_h, n_sleep, w_sleep = window_mean("sleep_h")
    rhr, n_rhr, w_rhr = window_mean("rhr")
    azm_day, n_azm, w_azm = window_mean("azm")
    cardio_day, _, _ = window_mean("cardio")
    mids = [d["sleep_mid"] for d in past
            if d["date"] > as_of - timedelta(days=28) and d["sleep_mid"] is not None]
    weights = [d["weight_kg"] for d in past if d["weight_kg"]]
    vo2 = [(d["date"], d["vo2max"]) for d in past
           if d["vo2max"] and d["date"] > as_of - timedelta(days=180)]

    recent = [w for w in weeks if as_of - timedelta(weeks=24) < w["week"] <= as_of]

    def sd(key, scale=1.0):
        vals = [w[key] * scale for w in recent if w[key] is not None]
        return statistics.stdev(vals) if len(vals) >= 3 else 0.0

    raw = {"steps": sd("steps"), "sleep_min": sd("sleep_h", 60.0), "cardio_min_day": sd("cardio_day")}
    sigma = {k: max(v, EFFORT_FLOORS[k]) for k, v in raw.items()}

    pairs = [(d["steps"], d["fatburn"]) for d in past if d["steps"] is not None and d["fatburn"] is not None]
    slope = 0.0
    if len(pairs) >= 14:
        mx = sum(p[0] for p in pairs) / len(pairs)
        my = sum(p[1] for p in pairs) / len(pairs)
        sxx = sum((p[0] - mx) ** 2 for p in pairs)
        if sxx > 0:
            slope = max(0.0, sum((p[0] - mx) * (p[1] - my) for p in pairs) / sxx * 1000)

    return Baseline(
        as_of=as_of, age=age_on(dob, as_of), steps=steps, sleep_h=sleep_h, rhr=rhr,
        azm_week=azm_day * 7 if azm_day is not None else None,
        cardio_week=(cardio_day or 0.0) * 7,
        sleep_mid_sd=circular_sd_minutes(mids) if len(mids) >= 7 else None,
        weight_kg=weights[-1] if weights else None,
        vo2max=vo2[-1][1] if vo2 else None, vo2_date=vo2[-1][0].isoformat() if vo2 else None,
        n={"steps": n_steps, "sleep": n_sleep, "rhr": n_rhr, "azm": n_azm, "sleep_mid": len(mids)},
        window={"steps": w_steps, "sleep": w_sleep, "rhr": w_rhr, "azm": w_azm},
        sigma=sigma, sigma_floor={k: raw[k] < EFFORT_FLOORS[k] for k in raw},
        fatburn_per_1k=slope, fatburn_n=len(pairs),
    )


# ---------------------------------------------------------------------------
# Projection and optimization
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Plan:
    steps: float
    sleep_h: float
    cardio_week: float


def project(b: Baseline, plan: Plan, extras: dict, rhr_effect: float = RHR_EFFECT_CENTRAL,
            weeks: int = HORIZON_WEEKS, sleep_mid_sd_target: Optional[float] = None) -> List[dict]:
    """Weekly path under a plan. Age is held at the as-of age so futures
    differ only by behaviour (the paper compares future against future)."""
    azm0 = b.azm_week if b.azm_week is not None else 0.0
    rhr = b.rhr
    path = []
    for t in range(weeks + 1):
        f = min(t / RAMP_WEEKS, 1.0)
        steps = b.steps + f * (plan.steps - b.steps)
        sleep_h = b.sleep_h + f * (plan.sleep_h - b.sleep_h)
        cardio = b.cardio_week + f * (plan.cardio_week - b.cardio_week)
        azm = max(0.0, azm0 + 7 * b.fatburn_per_1k * (steps - b.steps) / 1000 + 2 * (cardio - b.cardio_week))
        if t > 0:
            dose = (min(cardio, FULL_DOSE_CARDIO) - min(b.cardio_week, FULL_DOSE_CARDIO)) / FULL_DOSE_CARDIO
            rhr = rhr + RHR_KAPPA * (b.rhr * (1 - rhr_effect * dose) - rhr)
        kw = dict(extras)
        if sleep_mid_sd_target is not None and b.sleep_mid_sd is not None:
            kw["sleep_mid_sd"] = b.sleep_mid_sd + f * (sleep_mid_sd_target - b.sleep_mid_sd)
        c = cha(b.age, rhr=rhr, steps=steps, sleep_h=sleep_h, azm_week=azm, **kw)
        path.append({"week": t, "steps": steps, "sleep_h": sleep_h, "cardio_week": cardio,
                     "azm_week": azm, "rhr": rhr, "health_age": c["health_age"], "delta": c["delta"]})
    return path


def effort(b: Baseline, p: Plan) -> float:
    return (((p.steps - b.steps) / b.sigma["steps"]) ** 2
            + ((p.sleep_h - b.sleep_h) * 60 / b.sigma["sleep_min"]) ** 2
            + ((p.cardio_week - b.cardio_week) / 7 / b.sigma["cardio_min_day"]) ** 2)


def candidate_grid(b: Baseline, max_cardio: float) -> List[Plan]:
    steps = {float(round(b.steps))} | {float(s) for s in range(6000, 12001, 1000) if s > b.steps + 250}
    if b.sleep_h < 6.0:
        sleep = {b.sleep_h} | {h for h in (6.0, 6.5, 7.0, 7.5, 8.0) if h > b.sleep_h + 0.1}
    elif b.sleep_h > SLEEP_CAP_H:
        sleep = {b.sleep_h, 9.0, 8.5, 8.0}
    else:
        sleep = {b.sleep_h} | {h for h in (7.0, 7.5, 8.0) if h > b.sleep_h + 0.1}
    cardio = {b.cardio_week} | {float(c) for c in (30, 60, 90, 120, 150, 180)
                                if b.cardio_week + 10 < c <= max_cardio}
    return [Plan(s, h, c) for s in sorted(steps) for h in sorted(sleep) for c in sorted(cardio)]


def pathway(b: Baseline, p: Plan) -> str:
    walked = abs(p.steps - round(b.steps)) > 1
    trained = abs(p.cardio_week - b.cardio_week) > 1
    slept = abs(p.sleep_h - b.sleep_h) > 1e-6
    if (walked or trained) and slept:
        return "combined"
    if slept:
        return "sleep"
    if walked and trained:
        return "walking + cardio"
    if walked:
        return "walking"
    if trained:
        return "cardio"
    return "status quo"


def optimize(b: Baseline, extras: dict, max_cardio: float) -> dict:
    sq = project(b, Plan(b.steps, b.sleep_h, b.cardio_week), extras)
    sq_end = sq[-1]["health_age"]
    scored = []
    for p in candidate_grid(b, max_cardio):
        path = project(b, p, extras)
        benefit = sq_end - path[-1]["health_age"]
        e = effort(b, p)
        scored.append({"plan": p, "pathway": pathway(b, p), "benefit": benefit, "effort": e,
                       "score": benefit / (1 + e), "path": path})
    by_plan = {s["plan"]: s["benefit"] for s in scored}
    base_vals = {"steps": float(round(b.steps)), "sleep_h": b.sleep_h, "cardio_week": b.cardio_week}

    def redundant(s):
        for field, bv in base_vals.items():
            if abs(getattr(s["plan"], field) - bv) > 1e-6:
                rev = by_plan.get(replace(s["plan"], **{field: bv}))
                if rev is not None and s["benefit"] - rev < 0.02:
                    return True
        return False

    feasible = sorted((s for s in scored if s["benefit"] > MIN_BENEFIT_YEARS and not redundant(s)),
                      key=lambda s: s["score"], reverse=True)
    best: Dict[str, dict] = {}
    for s in feasible:
        best.setdefault(s["pathway"], s)
    options = sorted(best.values(), key=lambda s: s["score"], reverse=True)[:3]
    for o in options:
        o["benefit_low"] = sq_end - project(b, o["plan"], extras, rhr_effect=RHR_EFFECT_LOW)[-1]["health_age"]
    return {"status_quo": sq, "options": options,
            "max_reduction": max(scored, key=lambda s: s["benefit"]) if scored else None,
            "feasible": feasible, "n_candidates": len(scored)}


# ---------------------------------------------------------------------------
# Insulin resistance from the profile
# ---------------------------------------------------------------------------

def insulin_resistance(profile: dict):
    if profile.get("insulin_resistant") is not None:
        return bool(profile["insulin_resistant"]), "set in profile (measured)"
    labs = profile.get("labs") or {}
    tg, hdl, a1c = labs.get("triglycerides_mg_dl"), labs.get("hdl_mg_dl"), labs.get("hba1c_pct")
    if tg and hdl:
        ratio = tg / hdl
        flag = ratio >= 3.0 or (a1c is not None and a1c >= 5.7)
        a1c_txt = f", HbA1c {a1c}%" if a1c is not None else ""
        return flag, (f"lab surrogate{', ' + labs['date'] if labs.get('date') else ''}: "
                      f"TG/HDL {ratio:.1f}{a1c_txt}. The paper uses a HOMA-IR predictor; "
                      "fasting insulin would measure it directly")
    return None, "no labs entered"


# ---------------------------------------------------------------------------
# Full report for the UI
# ---------------------------------------------------------------------------

def _round(x, nd=2):
    return None if x is None else round(x, nd)


def _plan_json(o: dict, with_path=True) -> dict:
    out = {"pathway": o["pathway"],
           "targets": {"steps": round(o["plan"].steps), "sleep_h": round(o["plan"].sleep_h, 2),
                       "cardio_week": round(o["plan"].cardio_week)},
           "benefit": round(o["benefit"], 3), "effort": round(o["effort"], 2),
           "score": round(o["score"], 4)}
    if "benefit_low" in o:
        out["benefit_low"] = round(o["benefit_low"], 3)
    if with_path:
        out["path"] = [{k: round(v, 3) for k, v in p.items()} for p in o["path"]]
    return out


def report(data: dict, profile: dict, today: Optional[date] = None, as_of: Optional[date] = None) -> dict:
    today = today or date.today()
    if not profile.get("dob"):
        return {"ok": False, "reason": "profile", "message": "Add your date of birth in Settings."}
    dob = date.fromisoformat(profile["dob"])
    female = str(profile.get("sex", "male")).lower().startswith("f")
    days = clean_days(data, today)
    complete = [d["date"] for d in days if d["steps"] is not None and d["rhr"] is not None]
    if not complete:
        return {"ok": False, "reason": "data", "message": "No days with both steps and resting heart rate yet. Sync first."}
    as_of = as_of or max(complete)
    days = [d for d in days if d["date"] <= as_of]
    weeks = weekly(days)
    b = build_baseline(days, weeks, as_of, dob)
    if b.steps is None or b.sleep_h is None or b.rhr is None:
        return {"ok": False, "reason": "data",
                "message": "Need 5 days each of steps, sleep and resting heart rate within 90 days."}

    ir_flag, ir_src = insulin_resistance(profile)
    core = cha(b.age, rhr=b.rhr, steps=b.steps, sleep_h=b.sleep_h, azm_week=b.azm_week, female=female)
    ext_kw = {"sleep_mid_sd": b.sleep_mid_sd, "insulin_resistant": ir_flag, "vo2max": b.vo2max,
              "weight_kg": b.weight_kg, "female": female}
    ext = cha(b.age, rhr=b.rhr, steps=b.steps, sleep_h=b.sleep_h, azm_week=b.azm_week, **ext_kw)
    opt = optimize(b, {"female": female}, float(profile.get("max_cardio_min_week") or 180))

    values = {"rhr": (b.rhr, "bpm"), "steps": (b.steps, "a day"), "sleep": (b.sleep_h, "h"),
              "azm": (b.azm_week, "a week"), "regularity": (b.sleep_mid_sd, "min spread"),
              "ir": (None, ir_src), "vo2": (b.vo2max, "mL/kg/min")}
    domains = []
    for key in CORE + ["regularity", "ir", "vo2"]:
        if key not in ext["parts"] and key not in CORE:
            continue
        v, unit = values[key]
        d = {"key": key, "label": LABELS[key], "value": _round(v, 1), "unit": unit,
             "reference": REFERENCE[key], "years": _round(ext["parts"].get(key), 3),
             "optional": key not in CORE}
        if key == "ir":
            d["flag"] = ir_flag
        if key == "vo2" and b.weight_kg:
            d["expected"] = round(expected_vo2(b.age, female, b.weight_kg), 1)
            d["date"] = b.vo2_date
        domains.append(d)

    levers = []
    if b.sleep_mid_sd is not None:
        for sd in (90, 60, 30):
            if b.sleep_mid_sd > sd:
                alt = cha(b.age, rhr=b.rhr, steps=b.steps, sleep_h=b.sleep_h, azm_week=b.azm_week,
                          **{**ext_kw, "sleep_mid_sd": sd})
                levers.append({"sd": sd, "years": round(alt["parts"]["regularity"], 3),
                               "extended": round(alt["health_age"], 3)})

    # Ranked next actions across the whole picture (extension: the paper ranks
    # only movement and sleep-duration plans)
    actions = []
    if opt["options"]:
        o = opt["options"][0]
        actions.append({"kind": "plan", "years": round(o["benefit"], 3),
                        "title": f"Best plan: {o['pathway']}", "targets": _plan_json(o, False)["targets"]})
    lever60 = next((l for l in levers if l["sd"] == 60), None)
    if lever60:
        actions.append({"kind": "sleep_timing", "years": round(ext["health_age"] - lever60["extended"], 3),
                        "title": "Steady sleep timing", "target_sd": 60})
    if ir_flag:
        actions.append({"kind": "labs", "years": round(ext["parts"]["ir"], 3),
                        "title": "Measure insulin resistance",
                        "detail": "HbA1c, fasting glucose and insulin (HOMA-IR), lipid panel"})
    actions.sort(key=lambda a: a["years"], reverse=True)

    cloud = opt["feasible"]
    step = max(1, len(cloud) // 60)
    hist = []
    for w in weeks[-26:]:
        if w["steps"] is None and w["rhr"] is None:
            continue
        c = cha(age_on(dob, w["week"]), rhr=w["rhr"], steps=w["steps"], sleep_h=w["sleep_h"], azm_week=w["azm_week"])
        hist.append({"week": w["week"].isoformat(), "complete_days": w["complete_days"],
                     "steps": _round(w["steps"], 0), "sleep_h": _round(w["sleep_h"]), "rhr": _round(w["rhr"], 1),
                     "azm_week": _round(w["azm_week"], 0), "sleep_mid_sd": _round(w["sleep_mid_sd"], 0),
                     "delta": round(c["delta"], 3), "partial": bool(c["missing"])})

    daily = [{"date": d["date"].isoformat(), "steps": d["steps"], "rhr": d["rhr"], "azm": d["azm"],
              "sleep_h": d["sleep_h"], "sleep_mid": _round(d["sleep_mid"], 1), "weight_kg": d["weight_kg"]}
             for d in days if d["date"] > as_of - timedelta(days=120)]

    sources = sorted({d["rhr_source"] for d in days if d["rhr_source"]} - {None})
    warnings = []
    stale = (today - as_of).days
    if stale > 3:
        warnings.append(f"Latest complete day is {stale} days old. Sync to refresh.")
    if core["missing"]:
        warnings.append("Missing core domains: " + ", ".join(LABELS[k] for k in core["missing"])
                        + ". They count as 0 years, so the core number is incomplete.")
    if len(sources) > 1:
        warnings.append("Resting heart rate came from more than one device ("
                        + ", ".join(sources) + "). Devices can read a few bpm apart.")

    return {
        "ok": True,
        "as_of": as_of.isoformat(),
        "age": round(b.age, 3),
        "core": {"health_age": round(core["health_age"], 3), "delta": round(core["delta"], 3),
                 "missing": core["missing"]},
        "extended": {"health_age": round(ext["health_age"], 3), "delta": round(ext["delta"], 3)},
        "domains": domains,
        "baseline": {"steps": _round(b.steps, 0), "sleep_h": _round(b.sleep_h), "rhr": _round(b.rhr, 1),
                     "azm_week": _round(b.azm_week, 0), "cardio_week": _round(b.cardio_week, 0),
                     "sleep_mid_sd": _round(b.sleep_mid_sd, 0), "weight_kg": b.weight_kg},
        "coverage": {"n": b.n, "window": b.window},
        "levers": levers,
        "actions": actions,
        "futures": {
            "status_quo": [{k: round(v, 3) for k, v in p.items()} for p in opt["status_quo"]],
            "options": [_plan_json(o) for o in opt["options"]],
            "max_reduction": _plan_json(opt["max_reduction"]) if opt["max_reduction"] else None,
            "cloud": [[round(p["health_age"], 3) for p in s["path"]] for s in cloud[::step]],
            "n_candidates": opt["n_candidates"], "n_feasible": len(opt["feasible"]),
        },
        "model": {"fatburn_per_1k": round(b.fatburn_per_1k, 2), "fatburn_n": b.fatburn_n,
                  "sigma": {k: round(v, 1) for k, v in b.sigma.items()},
                  "sigma_floor": b.sigma_floor, "rhr_effect": RHR_EFFECT_CENTRAL,
                  "rhr_effect_low": RHR_EFFECT_LOW, "ramp_weeks": RAMP_WEEKS},
        "weekly": hist,
        "daily": daily,
        "warnings": warnings,
        "insulin_resistance_source": ir_src,
    }


def simulate(data: dict, profile: dict, targets: dict, today: Optional[date] = None) -> dict:
    """Project one user-chosen plan (the UI's what-if sliders)."""
    today = today or date.today()
    dob = date.fromisoformat(profile["dob"])
    female = str(profile.get("sex", "male")).lower().startswith("f")
    days = clean_days(data, today)
    as_of = max(d["date"] for d in days if d["steps"] is not None and d["rhr"] is not None)
    days = [d for d in days if d["date"] <= as_of]
    b = build_baseline(days, weekly(days), as_of, dob)
    plan = Plan(float(targets.get("steps", b.steps)), float(targets.get("sleep_h", b.sleep_h)),
                float(targets.get("cardio_week", b.cardio_week)))
    ir_flag, _ = insulin_resistance(profile)
    ext_kw = {"sleep_mid_sd": b.sleep_mid_sd, "insulin_resistant": ir_flag, "vo2max": b.vo2max,
              "weight_kg": b.weight_kg, "female": female}
    core_path = project(b, plan, {"female": female})
    sd_t = targets.get("sleep_mid_sd")
    ext_path = project(b, plan, ext_kw, sleep_mid_sd_target=float(sd_t) if sd_t is not None else None)
    sq = project(b, Plan(b.steps, b.sleep_h, b.cardio_week), {"female": female})
    low = project(b, plan, {"female": female}, rhr_effect=RHR_EFFECT_LOW)
    return {
        "targets": {"steps": round(plan.steps), "sleep_h": round(plan.sleep_h, 2),
                    "cardio_week": round(plan.cardio_week), "sleep_mid_sd": sd_t},
        "core_path": [round(p["health_age"], 3) for p in core_path],
        "extended_path": [round(p["health_age"], 3) for p in ext_path],
        "rhr_path": [round(p["rhr"], 2) for p in core_path],
        "benefit": round(sq[-1]["health_age"] - core_path[-1]["health_age"], 3),
        "benefit_low": round(sq[-1]["health_age"] - low[-1]["health_age"], 3),
        "effort": round(effort(b, plan), 2),
    }
