"""
Google Health API v4 client (health.googleapis.com), standard library only.

Schema notes, verified against live responses from a Fitbit Air (2026-11):
  * dailyRollUp accepts a multi-day range with windowSizeDays=1 and returns one
    rollupDataPoint per day that has data, keyed by civilStartTime.date. One
    request covers 30 days, so a 120-day sync is a handful of calls.
  * activeZoneMinutes.sumInFatBurnHeartZone is MINUTES; sumInCardioHeartZone and
    sumInPeakHeartZone are AZM CREDITS, already doubled (checked against
    minute-level time-in-heart-rate-zone records). Total AZM is the plain sum.
  * List endpoints (sleep, daily-resting-heart-rate, vo2-max) cap page size
    and must be paginated with page_token.
  * Sleep sessions carry metadata.mainSleep / metadata.nap and UTC offsets.
    Several apps (Health Connect, HealthKit, a watch) can each report the same
    night, so a day's sleep is its main episode, never a sum of sessions.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

BASE = "https://health.googleapis.com/v4/users/me"
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]
CHUNK_DAYS = 30


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status


class HealthClient:
    def __init__(self, access_token: Callable[[bool], str]):
        """access_token(force_refresh) returns a valid bearer token."""
        self._token = access_token

    def _call(self, method: str, path: str, params: Optional[dict] = None,
              body: Optional[dict] = None) -> Optional[dict]:
        url = f"{BASE}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (0, 1):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {self._token(attempt == 1)}",
                "Accept": "application/json", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    continue
                if e.code == 404:
                    return None
                detail = e.read()[:300].decode(errors="replace")
                raise ApiError(e.code, detail) from None
        return None

    def rollup(self, dtype: str, start: date, end: date) -> Dict[str, dict]:
        """Daily rollups between start and end inclusive, keyed by ISO date."""
        out: Dict[str, dict] = {}
        lo = start
        while lo <= end:
            hi = min(lo + timedelta(days=CHUNK_DAYS - 1), end)
            body = {"range": {
                "start": {"date": {"year": lo.year, "month": lo.month, "day": lo.day},
                          "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0}},
                "end": {"date": {"year": hi.year, "month": hi.month, "day": hi.day},
                        "time": {"hours": 23, "minutes": 59, "seconds": 59, "nanos": 0}}},
                "windowSizeDays": 1}
            resp = self._call("POST", f"dataTypes/{dtype}/dataPoints:dailyRollUp", body=body) or {}
            for p in resp.get("rollupDataPoints", []):
                ds = _date_struct(p.get("civilStartTime", {}).get("date"))
                if ds:
                    out[ds] = p
            lo = hi + timedelta(days=1)
        return out

    def list_points(self, dtype: str, since: date, date_of: Callable[[dict], Optional[str]],
                    max_pages: int = 60) -> List[dict]:
        """All points of a list-type data type back to `since` (paginated)."""
        points: List[dict] = []
        token = None
        for _ in range(max_pages):
            params = {"page_size": 500}
            if token:
                params["page_token"] = token
            resp = self._call("GET", f"dataTypes/{dtype}/dataPoints", params=params) or {}
            page = resp.get("dataPoints", [])
            points.extend(page)
            token = resp.get("nextPageToken")
            dates = [d for d in (date_of(p) for p in page) if d]
            if not token or (dates and min(dates) < since.isoformat()):
                break
        return points

    def identity(self) -> Optional[dict]:
        return self._call("GET", "identity")


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_struct(d: Optional[dict]) -> Optional[str]:
    if not d or not all(k in d for k in ("year", "month", "day")):
        return None
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"


def _offset_s(s: Optional[str]) -> float:
    if isinstance(s, str) and s.endswith("s"):
        return _num(s[:-1]) or 0.0
    return 0.0


def steps_of(p: dict) -> Optional[int]:
    n = _num(p.get("steps", {}).get("countSum"))
    return int(n) if n is not None else None


def azm_of(p: dict) -> Optional[dict]:
    node = p.get("activeZoneMinutes")
    if not isinstance(node, dict):
        return None
    fat = _num(node.get("sumInFatBurnHeartZone")) or 0.0
    cardio = _num(node.get("sumInCardioHeartZone")) or 0.0
    peak = _num(node.get("sumInPeakHeartZone")) or 0.0
    return {"total": fat + cardio + peak, "fat_burn_min": fat,
            "cardio_min": cardio / 2, "peak_min": peak / 2}


def weight_of(p: dict) -> Optional[float]:
    g = _num(p.get("weight", {}).get("weightGramsAvg"))
    return round(g / 1000, 2) if g else None


def rhr_date(p: dict) -> Optional[str]:
    return _date_struct(p.get("dailyRestingHeartRate", {}).get("date"))


def source_of(p: dict) -> str:
    src = p.get("dataSource", {})
    return src.get("device", {}).get("displayName") or src.get("platform") or "unknown"


def rhr_by_date(points: List[dict]) -> Dict[str, tuple]:
    """One resting HR per day. When several sources report the same day, take
    the source that reports most often overall, so a device switch shows up as
    a switch, not as day-to-day noise."""
    rank = Counter(source_of(p) for p in points)
    per_day: Dict[str, List[tuple]] = {}
    for p in points:
        ds, v = rhr_date(p), _num(p.get("dailyRestingHeartRate", {}).get("beatsPerMinute"))
        if ds and v is not None:
            per_day.setdefault(ds, []).append((rank[source_of(p)], v, source_of(p)))
    return {ds: (max(rows)[1], max(rows)[2]) for ds, rows in per_day.items()}


def sleep_wake_date(p: dict) -> Optional[str]:
    ivl = p.get("sleep", {}).get("interval", {})
    end = ivl.get("endTime")
    if not end:
        return None
    try:
        t = datetime.fromisoformat(end.replace("Z", "+00:00")) + timedelta(seconds=_offset_s(ivl.get("endUtcOffset")))
        return t.date().isoformat()
    except ValueError:
        return end[:10]


def _session_minutes(s: dict) -> float:
    summary = s.get("summary") or {}
    asleep = _num(summary.get("minutesAsleep"))
    if asleep is not None:
        return asleep
    total = 0.0
    for seg in s.get("stages", []) or []:
        if str(seg.get("type", "")).upper() in ("AWAKE", "OUT_OF_BED", "RESTLESS"):
            continue
        try:
            t0 = datetime.fromisoformat(seg["startTime"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(seg["endTime"].replace("Z", "+00:00"))
            total += (t1 - t0).total_seconds() / 60
        except (KeyError, ValueError):
            continue
    return total


def sleep_by_date(points: List[dict]) -> Dict[str, dict]:
    """Main sleep episode per local wake date: the longest session flagged
    mainSleep and not nap, else the longest non-nap session. Other sleep and
    naps are kept separately."""
    groups: Dict[str, List[dict]] = {}
    for p in points:
        ds = sleep_wake_date(p)
        if ds:
            groups.setdefault(ds, []).append(p)
    out = {}
    for ds, pts in groups.items():
        sessions = []
        for p in pts:
            s = p.get("sleep", {})
            md = s.get("metadata", {})
            ivl = s.get("interval", {})
            sessions.append({"min": _session_minutes(s), "nap": bool(md.get("nap")),
                             "main": bool(md.get("mainSleep")), "start": ivl.get("startTime"),
                             "end": ivl.get("endTime"), "offset": _offset_s(ivl.get("startUtcOffset"))})
        non_nap = [x for x in sessions if not x["nap"]]
        main = max([x for x in non_nap if x["main"]] or non_nap, key=lambda x: x["min"], default=None)
        nap = sum(x["min"] for x in sessions if x["nap"])
        if not main or main["min"] < 10:
            continue
        rec = {"hours": round(main["min"] / 60, 2), "start": main["start"], "end": main["end"],
               "offset_s": main["offset"]}
        if nap >= 1:
            rec["nap_hours"] = round(nap / 60, 2)
        other = sum(x["min"] for x in non_nap if x is not main)
        if other >= 1:
            rec["other_hours"] = round(other / 60, 2)
        out[ds] = rec
    return out


def vo2_date(p: dict) -> Optional[str]:
    return _date_struct(p.get("vo2Max", {}).get("sampleTime", {}).get("civilTime", {}).get("date"))


def vo2_by_date(points: List[dict]) -> Dict[str, float]:
    out = {}
    for p in points:
        ds, v = vo2_date(p), _num(p.get("vo2Max", {}).get("vo2Max"))
        if ds and v is not None:
            out[ds] = round(v, 1)
    return out


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def sync(client: HealthClient, existing: dict, days: int,
         progress: Callable[[float, str], None] = lambda f, m: None) -> dict:
    """Fetch the last `days` days and merge into `existing` (the stored data
    dict). Fresh values win; stored values survive where the API returned
    nothing, except sleep on days the fetched sessions cover."""
    today = datetime.now().astimezone().date()
    start = today - timedelta(days=days - 1)
    fresh: Dict[str, dict] = {}

    def put(ds, key, value):
        if value is not None and start.isoformat() <= ds <= today.isoformat():
            fresh.setdefault(ds, {})[key] = value

    progress(0.05, "steps")
    for ds, p in client.rollup("steps", start, today).items():
        put(ds, "steps", steps_of(p))
    progress(0.20, "active zone minutes")
    for ds, p in client.rollup("active-zone-minutes", start, today).items():
        put(ds, "azm", azm_of(p))
    progress(0.35, "weight")
    for ds, p in client.rollup("weight", start, today).items():
        put(ds, "weight_kg", weight_of(p))
    progress(0.50, "resting heart rate")
    for ds, (v, src) in rhr_by_date(client.list_points("daily-resting-heart-rate", start, rhr_date)).items():
        put(ds, "rhr", v)
        put(ds, "rhr_source", src)
    progress(0.70, "sleep")
    sleep_pts = client.list_points("sleep", start, sleep_wake_date)
    sleep = sleep_by_date(sleep_pts)
    for ds, rec in sleep.items():
        put(ds, "sleep", rec)
    progress(0.90, "VO2 max")
    for ds, v in vo2_by_date(client.list_points("vo2-max", start, vo2_date, max_pages=5)).items():
        put(ds, "vo2max", v)

    sleep_cover = min(sleep) if sleep else None
    merged = dict(existing.get("days", {}))
    d = start
    while d <= today:
        ds = d.isoformat()
        new = fresh.get(ds, {})
        old = merged.get(ds, {})
        rec = dict(old)
        rec.update(new)
        if sleep_cover and ds >= sleep_cover and "sleep" not in new:
            rec.pop("sleep", None)
        if rec:
            merged[ds] = rec
        d += timedelta(days=1)
    progress(1.0, "done")
    return {"version": 1, "days": merged,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "range": {"start": min(merged) if merged else None, "end": max(merged) if merged else None}}
