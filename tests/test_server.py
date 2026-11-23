"""Server tests on a real socket: same-origin rules and the demo flow."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["HEALTHAGE_HOME"] = cls.tmp.name
        from healthage.server import Handler
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.verbose = False
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, path, body=None, headers=None, host=None):
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h,
                                   method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read(), dict(e.headers)

    def test_index_has_csp_and_no_cors(self):
        status, body, headers = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn(b"healthage", body)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_foreign_host_rejected(self):
        self.assertEqual(self.req("/api/state", host="attacker.example")[0], 421)

    def test_post_needs_custom_header(self):
        self.assertEqual(self.req("/api/mode", {"demo": True})[0], 403)

    def test_demo_flow(self):
        hdr = {"X-Healthage": "1"}
        self.assertEqual(self.req("/api/mode", {"demo": True}, hdr)[0], 200)
        status, body, _ = self.req("/api/report")
        r = json.loads(body)
        self.assertTrue(r["ok"] and r["demo"])
        status, body, _ = self.req("/api/simulate", {"steps": 9000, "cardio_week": 60, "sleep_h": 7}, hdr)
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["core_path"]), 13)

    def test_bad_client_json(self):
        status, body, _ = self.req("/api/client", {"json": "{\"nope\": 1}"}, {"X-Healthage": "1"})
        self.assertEqual(status, 400)

    def test_static_whitelist(self):
        self.assertEqual(self.req("/static/../server.py")[0], 404)
        self.assertEqual(self.req("/static/app.js")[0], 200)


if __name__ == "__main__":
    unittest.main()
