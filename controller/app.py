"""Two HTTP servers in one process.

Public (:8081, exposed via cloudflared): serves ONLY POST /webhook. Vast signs
each delivery with HMAC-SHA256 over "<X-Vast-Timestamp>.<raw body>"; we verify
against raw bytes, reject stale timestamps, dedupe on event_id, answer 204
fast, and wake the poller. Nothing else lives on this port — the control
endpoints must not be reachable from the internet.

Local (:8080, localhost only): dashboard + status/events JSON + control
endpoints (interrupt / rebid / ceiling).
"""
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import events, puller
from .state import Controller, load_env

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = (ROOT / "controller" / "dashboard.html")
THUMBS = ROOT / "run" / "pulled" / "thumbs"

PUBLIC_PORT, LOCAL_PORT = 8081, 8080
MAX_SIGNATURE_AGE = 300


class PublicHandler(BaseHTTPRequestHandler):
    controller = None
    secret = b""
    seen = set()

    def _reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(404)

    def do_POST(self):
        if self.path.rstrip("/") != "/webhook":
            return self._reply(404)
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        ts = self.headers.get("X-Vast-Timestamp") or ""
        sig = self.headers.get("X-Vast-Signature-256") or ""
        expect = "sha256=" + hmac.new(self.secret, ts.encode() + b"." + raw,
                                      hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            events.emit("webhook.rejected", reason="bad_signature")
            return self._reply(401)
        try:
            if abs(time.time() - int(ts)) > MAX_SIGNATURE_AGE:
                events.emit("webhook.rejected", reason="stale", vast_ts=ts)
                return self._reply(400)
            payload = json.loads(raw)
        except ValueError:
            events.emit("webhook.rejected", reason="bad_body")
            return self._reply(400)
        eid = payload.get("event_id")
        if eid in self.seen:  # at-least-once delivery: retries are expected
            return self._reply(204)
        self.seen.add(eid)
        events.emit("webhook.received", event_id=eid,
                    notif_type=payload.get("notif_type"),
                    attempt=self.headers.get("X-Vast-Delivery-Attempt"),
                    vast_ts=payload.get("timestamp"),
                    subject=payload.get("subject"),
                    message=payload.get("message"))
        self.controller.wake.set()  # the payload has no instance id: go poll
        self._reply(204)

    def log_message(self, *a):
        pass


class LocalHandler(BaseHTTPRequestHandler):
    controller = None

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            body = DASHBOARD.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/status"):
            self._json(self.controller.status())
        elif self.path.startswith("/api/events"):
            since = 0.0
            if "since=" in self.path:
                since = float(self.path.split("since=")[1].split("&")[0])
            self._json(events.read(since)[-400:])
        elif self.path.startswith("/api/thumbs"):
            names = sorted(f.name for f in THUMBS.glob("*.webp")) if THUMBS.exists() else []
            self._json(names)
        elif self.path.startswith("/api/loss"):
            f = ROOT / "run" / "pulled" / "loss.csv"
            pts = []
            if f.exists():
                for line in f.read_text().splitlines():
                    p = line.split(",")  # step,loss,tokens_per_sec,boot_count,ts
                    if len(p) >= 4 and p[0] != "step":
                        pts.append({"step": int(p[0]), "loss": float(p[1]),
                                    "boot_count": int(p[3])})
            self._json(pts)
        elif self.path.startswith("/thumbs/"):
            name = Path(self.path).name  # basename only — no traversal
            f = THUMBS / name
            if f.exists():
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/webp")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        c = self.controller
        if self.path == "/api/control/interrupt":
            lever = body.get("lever", "bid-drop")
            threading.Thread(target=c.interrupt, args=(lever,), daemon=True).start()
            self._json({"ok": True, "lever": lever})
        elif self.path == "/api/control/rebid":
            c.manual_rebid(float(body["price"]))
            self._json({"ok": True})
        elif self.path == "/api/control/ceiling":
            c.set_ceiling(float(body["value"]))
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


def main():
    env = load_env()
    secret = (env.get("WEBHOOK_SECRET") or "").encode()
    if not secret:
        print("WARNING: WEBHOOK_SECRET empty — webhook deliveries will be rejected "
              "until `make webhook` writes it to .env")

    controller = Controller()
    controller.start()
    threading.Thread(target=puller.run_loop, args=(controller,), daemon=True).start()

    PublicHandler.controller = LocalHandler.controller = controller
    PublicHandler.secret = secret
    PublicHandler.seen = events.seen_webhook_ids()  # dedupe survives restarts

    public = ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), PublicHandler)
    local = ThreadingHTTPServer(("127.0.0.1", LOCAL_PORT), LocalHandler)
    threading.Thread(target=public.serve_forever, daemon=True).start()
    events.emit("controller.started", state=controller.state)
    print("dashboard http://localhost:%d  webhook :%d (tunnel it)" % (LOCAL_PORT, PUBLIC_PORT))
    local.serve_forever()


if __name__ == "__main__":
    main()
