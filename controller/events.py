"""Single-writer JSONL event log.

Everything downstream — dashboard timeline, latency claims in the writeup,
the cost receipt — derives from this one file, stamped by this one clock.
"""
import json
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
LOG = RUN / "events.jsonl"
_lock = threading.Lock()


def emit(etype, **fields):
    rec = {"ts": round(time.time(), 3), "type": etype, **fields}
    with _lock:
        RUN.mkdir(exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


def read(since=0.0):
    if not LOG.exists():
        return []
    out = []
    with LOG.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("ts", 0) > since:
                out.append(rec)
    return out


def seen_webhook_ids():
    """Replayed at boot so webhook dedupe survives controller restarts."""
    return {r["event_id"] for r in read()
            if r["type"] == "webhook.received" and "event_id" in r}
