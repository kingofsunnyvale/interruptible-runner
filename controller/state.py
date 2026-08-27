"""Poll loop + rebid state machine.

The poller is the source of truth (the outbid webhook carries no instance id);
a webhook merely sets `wake` to force an immediate off-cycle poll. States:

  IDLE -> LAUNCHING -> RUNNING -> INTERRUPTED -> REBID_WAIT -> RUNNING ...
  plus CEILING_WAIT, STARVED, EXHAUSTED, DEAD, DONE.

Rebid policy: ceiling = min(MAX_HOURLY, CEILING_FRACTION * on-demand price of
the same machine); target = clamp(max(min_bid * MIN_BID_MULT, bid + BID_STEP),
ceiling). Whether a rebid alone un-pauses the instance is undocumented, so
REBID_WAIT fires `start instance` once after START_FALLBACK_SECS — the event
log then records which path actually resumed us (that answer feeds PROBE.md).
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import events, vast

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
STATE_FILE = RUN / "state.json"

# Probe finding (2026-08-27): an outbid manifests as actual_status "exited" —
# and it IS recoverable (explicit start, or autonomous resume when the
# preempting tenant leaves). The docs' poll trap ("exited never returns to
# running") is wrong for interruptibles. Only these two are treated as dead:
TERMINAL = ("unknown", "offline")
INTERRUPTED_STATES = ("stopped", "exited")


def load_env():
    cfg = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


class Controller(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        env = load_env()
        f = lambda k, d: float(env.get(k) or d)
        self.poll_secs = f("POLL_SECS", 10)
        self.max_hourly = f("MAX_HOURLY", 0.60)
        self.ceiling_fraction = f("CEILING_FRACTION", 0.90)
        self.bid_step = f("BID_STEP", 0.02)
        self.min_bid_mult = f("MIN_BID_MULT", 1.05)
        self.debounce = f("REBID_DEBOUNCE_SECS", 15)
        self.max_attempts = int(f("MAX_REBID_ATTEMPTS", 5))
        self.start_fallback_secs = f("START_FALLBACK_SECS", 45)
        self.starved_after_secs = f("STARVED_AFTER_SECS", 90)

        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.state = "IDLE"
        self.job = {}
        self.attempts = 0
        self.last_rebid_ts = 0.0
        self.rebid_wait_since = 0.0
        self.start_fallback_used = False
        self.prev_triple = None
        self.last_tick = time.time()
        self.last_save = 0.0
        self.worker = {}  # latest pulled status.json, set by the puller
        self._load_job()

    # ---------- persistence ----------

    def _load_job(self):
        if STATE_FILE.exists():
            self._job_mtime = STATE_FILE.stat().st_mtime
            new = json.loads(STATE_FILE.read_text())
            if new.get("instance_id") != self.job.get("instance_id"):
                # a (re)launch happened — adopt the new instance from scratch
                self.job = new
                self.job.setdefault("cost", {"gpu": 0.0, "storage": 0.0, "od": 0.0})
                self.state = "LAUNCHING" if new.get("instance_id") else "IDLE"
                self.prev_triple = None
                self.attempts = 0

    def _job_changed_on_disk(self):
        return (STATE_FILE.exists()
                and STATE_FILE.stat().st_mtime > getattr(self, "_job_mtime", 0))

    def _save_job(self):
        RUN.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.job, indent=2))
        self._job_mtime = STATE_FILE.stat().st_mtime
        self.last_save = time.time()

    # ---------- derived ----------

    def ceiling(self):
        od = self.job.get("on_demand_dph")
        return min(self.max_hourly, self.ceiling_fraction * od) if od else self.max_hourly

    def status(self):
        with self.lock:
            cost = dict(self.job.get("cost", {}))
            cost["total"] = round(cost.get("gpu", 0) + cost.get("storage", 0), 4)
            od = cost.get("od", 0)
            cost["saved"] = round(od - cost["total"], 4)
            cost["saved_pct"] = round(100 * cost["saved"] / od, 1) if od > 0.0001 else None
            return {
                "state": self.state,
                "instance_id": self.job.get("instance_id"),
                "payload": self.job.get("payload"),
                "bid": self.job.get("bid"),
                "ceiling": round(self.ceiling(), 3),
                "on_demand_dph": self.job.get("on_demand_dph"),
                "min_bid": self.job.get("last_min_bid"),
                "triple": self.prev_triple,
                "attempts": self.attempts,
                "cost": cost,
                "worker": self.worker,
            }

    # ---------- control actions (called from the local HTTP server) ----------

    def interrupt(self, lever):
        events.emit("kill.initiated", lever=lever, state=self.state)
        r = subprocess.run([sys.executable, "-m", "scripts.interrupt", "--lever", lever],
                           cwd=ROOT, capture_output=True, text=True)
        ok = r.returncode == 0
        events.emit("kill.lever_done", lever=lever, ok=ok,
                    detail=(r.stdout + r.stderr).strip()[-300:])
        if lever == "bid-drop" and ok:
            with self.lock:
                self.job["bid"] = 0.001
                self._save_job()
        self.wake.set()
        return ok

    def manual_rebid(self, price):
        events.emit("rebid.request", target=price, manual=True)
        vast.change_bid(self.job["instance_id"], price)
        with self.lock:
            self.job["bid"] = price
            self._save_job()
        events.emit("rebid.response", target=price, manual=True, ok=True)
        self.wake.set()

    def set_ceiling(self, value):
        self.max_hourly = float(value)
        events.emit("ceiling.raised", max_hourly=self.max_hourly)
        if self.state == "CEILING_WAIT":
            self._to("INTERRUPTED")
        self.wake.set()

    def mark_worker(self, status):
        """Puller hands us the freshest status.json; detect completion here."""
        self.worker = status
        done, total = status.get("images_done"), status.get("total")
        if self.state not in ("DONE", "IDLE") and done is not None and done >= (total or 0) > 0:
            events.emit("done", images=done)
            self._to("DONE")

    # ---------- state machine ----------

    def _to(self, new_state, **fields):
        if new_state != self.state:
            events.emit("state.change", frm=self.state, to=new_state, **fields)
            self.state = new_state

    def run(self):
        while True:
            self.wake.wait(self.poll_secs)
            forced = self.wake.is_set()
            self.wake.clear()
            if self._job_changed_on_disk():
                self._load_job()  # scripts/launch.py rewrote state.json
            if not self.job.get("instance_id"):
                continue
            try:
                inst = vast.show_instance(self.job["instance_id"])
            except Exception as e:  # transient CLI/API failures must not kill the loop
                events.emit("poll.error", error=str(e)[:300])
                continue
            with self.lock:
                self._tick(inst or {}, forced)

    def _tick(self, inst, forced):
        actual = inst.get("actual_status")
        intended = inst.get("intended_status")
        min_bid = inst.get("min_bid")
        now = time.time()
        dt = now - self.last_tick
        self.last_tick = now

        # cost accrual (estimate; the receipt re-derives exact $ from the charges API)
        cost = self.job.setdefault("cost", {"gpu": 0.0, "storage": 0.0, "od": 0.0})
        if actual == "running":
            cost["gpu"] += dt * (self.job.get("bid") or 0) / 3600
            cost["od"] += dt * (self.job.get("on_demand_dph") or 0) / 3600
        if actual not in (None, "offline"):
            cost["storage"] += dt * (self.job.get("storage_dph") or 0) / 3600

        if min_bid is not None:
            self.job["last_min_bid"] = min_bid
        # dph_base is the authoritative live bid (probe: bare change-bid can
        # move it without us knowing) — keep our record in sync.
        if inst.get("dph_base") is not None:
            self.job["bid"] = inst["dph_base"]
        triple = (actual, intended)
        if triple != self.prev_triple:
            events.emit("poll.status", actual=actual, intended=intended,
                        min_bid=min_bid, state=self.state, forced=forced,
                        status_msg=(inst.get("status_msg") or "").strip()[:200])
        self.prev_triple = triple
        if now - self.last_save > 60:
            self._save_job()

        s = self.state
        if actual in TERMINAL:
            self._to("DEAD", actual=actual)
            return

        if s in ("LAUNCHING", "STARVED", "DEAD") and actual == "running":
            if s != "LAUNCHING":
                events.emit("resume.detected", frm=s.lower(), attempts=self.attempts)
                self.attempts = 0
            self._to("RUNNING")

        elif s == "STARVED":
            # A start issued while the GPU is held gets "state change queued",
            # but the scheduler can also knock intended back to stopped — so
            # keep a start request warm every couple of minutes.
            if now - getattr(self, "last_starved_start", 0) > 120:
                self.last_starved_start = now
                try:
                    vast.start_instance(self.job["instance_id"])
                    events.emit("starved.start_retry", ok=True)
                except Exception as e:
                    events.emit("starved.start_retry", ok=False, error=str(e)[:200])

        elif s in ("RUNNING", "DONE", "LAUNCHING") and actual in INTERRUPTED_STATES:
            # Measured outbid signature: intended_status flips to "stopped"
            # within seconds, then actual goes "exited" (~30 s). We record
            # `intended` but don't gate on it.
            events.emit("interruption.detected",
                        source="webhook" if forced else "poll",
                        actual=actual, intended=intended)
            self.attempts = 0
            self.start_fallback_used = False
            self._to("INTERRUPTED")
            self._maybe_rebid(min_bid)

        elif s == "INTERRUPTED":
            if actual == "running":  # resolved without us (competitor left)
                events.emit("resume.detected", frm="interrupted", attempts=self.attempts)
                self._to("RUNNING")
            else:
                self._maybe_rebid(min_bid)

        elif s == "REBID_WAIT":
            if actual == "running":
                events.emit("resume.detected", frm="rebid", attempts=self.attempts,
                            start_fallback_used=self.start_fallback_used)
                self.attempts = 0
                self._to("RUNNING")
                return
            waited = now - self.rebid_wait_since
            # Probe: after a self-bid-drop, rebidding alone did NOT revive the
            # exited container — an explicit start did. After an od tenant
            # left, resume was autonomous. The fallback covers the first case.
            if waited > self.start_fallback_secs and not self.start_fallback_used:
                self.start_fallback_used = True
                try:
                    vast.start_instance(self.job["instance_id"])
                    events.emit("rebid.start_fallback", ok=True)
                except Exception as e:
                    events.emit("rebid.start_fallback", ok=False, error=str(e)[:200])
            elif waited > self.starved_after_secs:
                # Probe: instance min_bid tracks OUR OWN bid x1.2 (a raise
                # increment), so `bid < min_bid` is always true after our own
                # rebid — chasing it would spiral to the ceiling. Only treat
                # the market as moved when min_bid clears the x1.2 self-echo.
                if min_bid and min_bid > (self.job.get("bid") or 0) * 1.3:
                    events.emit("rebid.outbid_again", min_bid=min_bid)
                    self._to("INTERRUPTED")
                else:  # a higher-priority tenant still holds the GPU;
                    # bidding cannot help until they leave.
                    events.emit("starvation.detected", min_bid=min_bid,
                                bid=self.job.get("bid"))
                    self._to("STARVED")

        elif s == "CEILING_WAIT":
            if actual == "running":
                events.emit("resume.detected", frm="ceiling_wait")
                self._to("RUNNING")
            elif min_bid and min_bid * self.min_bid_mult <= self.ceiling():
                events.emit("ceiling.cleared", min_bid=min_bid)
                self._to("INTERRUPTED")
                self._maybe_rebid(min_bid)

    def _maybe_rebid(self, min_bid):
        now = time.time()
        if now - self.last_rebid_ts < self.debounce:
            return
        if self.attempts >= self.max_attempts:
            events.emit("rebid.exhausted", attempts=self.attempts)
            self._to("EXHAUSTED")
            return
        ceiling = self.ceiling()
        bid = self.job.get("bid") or 0
        # right after a bid-drop the ask can be briefly unqueryable — fall back
        # to stepping our own last-known bid.
        needed = min_bid * self.min_bid_mult if min_bid else bid + self.bid_step
        if needed > ceiling:
            events.emit("ceiling.hit", needed=round(needed, 3), ceiling=round(ceiling, 3))
            self._to("CEILING_WAIT")
            return
        target = round(min(max(needed, bid + self.bid_step), ceiling), 3)
        self.attempts += 1
        self.last_rebid_ts = now
        events.emit("rebid.request", target=target, min_bid=min_bid, attempt=self.attempts)
        try:
            vast.change_bid(self.job["instance_id"], target)
            self.job["bid"] = target
            self._save_job()
            events.emit("rebid.response", target=target, ok=True, attempt=self.attempts)
            self.rebid_wait_since = now
            self.start_fallback_used = False
            self._to("REBID_WAIT")
        except Exception as e:
            events.emit("rebid.response", target=target, ok=False, error=str(e)[:200])
