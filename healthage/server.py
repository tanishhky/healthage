"""
Local web server: serves the UI and a small JSON API on 127.0.0.1 only.

Hardening for a localhost app that holds health data:
  * binds to loopback only;
  * rejects requests whose Host header is not this server (DNS rebinding);
  * every POST needs the X-Healthage header and a JSON body, which a page on
    another origin cannot send without a CORS preflight this server never
    approves (CSRF);
  * no CORS headers, a strict Content-Security-Policy, no third-party assets.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import traceback
import urllib.parse
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, demo, engine, oauth, store
from .google_health import ApiError, HealthClient, sync as gh_sync

WEB = Path(__file__).parent / "web"
STATIC = {"app.js", "charts.js", "app.css", "favicon.svg"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class SyncState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.progress = 0.0
        self.stage = ""
        self.error = None
        self.finished_at = None

    def snapshot(self):
        with self.lock:
            return {"running": self.running, "progress": round(self.progress, 3), "stage": self.stage,
                    "error": self.error, "finished_at": self.finished_at}


SYNC = SyncState()


def _tilde(p: Path) -> str:
    """Show the data folder with ~ so screenshots never carry a username."""
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h) else s


def settings() -> dict:
    return store.read("settings.json", {}) or {}


def demo_mode() -> bool:
    return bool(settings().get("demo", False))


def run_sync(days: int):
    def progress(f, stage):
        with SYNC.lock:
            SYNC.progress, SYNC.stage = f, stage

    try:
        client = HealthClient(lambda force: oauth.access_token(force))
        new = gh_sync(client, store.read("data.json", {}) or {}, days, progress)
        store.write("data.json", new)
        s = settings()
        s["demo"] = False
        store.write("settings.json", s)
        err = None
    except (oauth.AuthError, ApiError) as e:
        err = str(e)
        if isinstance(e, ApiError) and e.status == 403:
            err = ("Google refused access (403). Check that the Google Health API is enabled in your "
                   "Cloud project and that you granted every permission. " + str(e)[:160])
    except Exception as e:                      # surface, never crash the server
        traceback.print_exc()
        err = f"Sync failed: {e}"
    with SYNC.lock:
        SYNC.running = False
        SYNC.error = err
        SYNC.finished_at = datetime.now(timezone.utc).isoformat()


class Handler(BaseHTTPRequestHandler):
    server_version = f"healthage/{__version__}"

    def log_message(self, fmt, *args):         # keep the terminal quiet
        if self.server.verbose:
            super().log_message(fmt, *args)

    # -- helpers -----------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status, body: bytes, ctype: str, extra: dict = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("Request too large")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    def _redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/oauth/callback"

    # -- GET -----------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"Bad host", "text/plain")
        url = urllib.parse.urlparse(self.path)
        path = url.path
        try:
            if path in ("/", "/index.html"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/static/") and path[8:] in STATIC:
                f = WEB / path[8:]
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype)
            if path == "/oauth/callback":
                q = urllib.parse.parse_qs(url.query)
                if "error" in q:
                    return self._redirect(f"/#error={urllib.parse.quote(q['error'][0])}")
                oauth.finish(q.get("state", [""])[0], q.get("code", [""])[0])
                return self._redirect("/#connected")
            if path == "/api/state":
                return self._json(self._state())
            if path == "/api/report":
                return self._json(self._report())
            if path == "/api/sync":
                return self._json(SYNC.snapshot())
            if path == "/api/profile":
                return self._json(store.read("profile.json", {}) or {})
            return self._send(404, b"Not found", "text/plain")
        except oauth.AuthError as e:
            if path == "/oauth/callback":
                return self._redirect(f"/#error={urllib.parse.quote(str(e))}")
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _redirect(self, to: str):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- POST ----------------------------------------------------------------
    def do_POST(self):
        if not self._host_ok():
            return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"Bad host", "text/plain")
        if self.headers.get("X-Healthage") != "1" or "json" not in (self.headers.get("Content-Type") or ""):
            return self._json({"error": "Missing X-Healthage header"}, 403)
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/client":
                c = oauth.save_client(body.get("json") or "")
                return self._json({"ok": True, "type": c["type"]})
            if path == "/api/connect":
                return self._json({"url": oauth.auth_url(self._redirect_uri())})
            if path == "/api/disconnect":
                oauth.disconnect()
                return self._json({"ok": True})
            if path == "/api/sync":
                days = max(30, min(int(body.get("days") or 120), 365))
                with SYNC.lock:
                    if SYNC.running:
                        return self._json({"ok": True, "already": True})
                    SYNC.running, SYNC.progress, SYNC.stage, SYNC.error = True, 0.0, "starting", None
                threading.Thread(target=run_sync, args=(days,), daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/profile":
                return self._json(self._save_profile(body))
            if path == "/api/mode":
                s = settings()
                if "demo" in body:
                    s["demo"] = bool(body["demo"])
                if body.get("theme") in ("hud", "terminal"):
                    s["theme"] = body["theme"]
                store.write("settings.json", s)
                return self._json({"ok": True})
            if path == "/api/simulate":
                data, prof, today = self._source()
                return self._json(engine.simulate(data, prof, body, today=today))
            if path == "/api/delete":
                for name in ("data.json", "token.json", "profile.json", "client.json", "settings.json"):
                    store.delete(name)
                return self._json({"ok": True})
            return self._json({"error": "Not found"}, 404)
        except (oauth.AuthError, ValueError) as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # -- logic ---------------------------------------------------------------
    def _source(self):
        today = date.today()
        if demo_mode():
            return demo.data(today), demo.profile(today), today
        return store.read("data.json", {}) or {}, store.read("profile.json", {}) or {}, today

    def _report(self):
        data, prof, today = self._source()
        r = engine.report(data, prof, today=today)
        r["demo"] = demo_mode()
        return r

    def _state(self):
        data = store.read("data.json") or {}
        prof = store.read("profile.json") or {}
        return {"version": __version__, "demo": demo_mode(), "theme": settings().get("theme", "hud"),
                "has_client": bool(oauth.client()), "connected": oauth.connected(),
                "has_data": bool(data.get("days")), "synced_at": data.get("synced_at"),
                "range": data.get("range"), "profile_ok": bool(prof.get("dob")),
                "redirect_uri": self._redirect_uri(), "home": _tilde(store.home()), "sync": SYNC.snapshot()}

    @staticmethod
    def _save_profile(body: dict) -> dict:
        prof = store.read("profile.json", {}) or {}
        if body.get("dob"):
            d = date.fromisoformat(body["dob"])
            if not (date(1900, 1, 1) < d < date.today()):
                raise ValueError("Date of birth looks wrong.")
            prof["dob"] = d.isoformat()
        if body.get("sex") in ("male", "female"):
            prof["sex"] = body["sex"]
        labs = {}
        for k in ("hba1c_pct", "triglycerides_mg_dl", "hdl_mg_dl"):
            v = body.get(k)
            if v not in (None, ""):
                labs[k] = float(v)
        if body.get("labs_date"):
            labs["date"] = date.fromisoformat(body["labs_date"]).isoformat()
        prof["labs"] = labs
        ir = body.get("insulin_resistant")
        prof["insulin_resistant"] = None if ir in (None, "", "auto") else (ir in (True, "true", "yes"))
        if body.get("max_cardio_min_week"):
            prof["max_cardio_min_week"] = max(30, min(float(body["max_cardio_min_week"]), 300))
        store.write("profile.json", prof)
        return {"ok": True, "profile": prof}


def serve(port: int = 8765, verbose: bool = False, open_browser: bool = True):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.verbose = verbose
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"\n  healthage {__version__}  ->  {url}\n  data: {store.home()}\n  Ctrl+C to stop\n")
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        httpd.server_close()
