"""Create or update the Vast notification webhook.

Must go through the API (not the console): the HMAC signing secret is returned
only by webhook create / rotate-secret, never by list or the console UI.
`--test-only` just fires the built-in test delivery and checks the controller
logged a verified webhook.received — the pre-demo green light.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from controller import events, vast  # noqa: E402

RUN = ROOT / "run"
NAME = "interruptible-runner"
WANTED = {"client:outbid", "client:instance_stopped", "client:instance_started"}


def write_secret(secret):
    envf = ROOT / ".env"
    lines = envf.read_text().splitlines() if envf.exists() else \
        (ROOT / ".env.example").read_text().splitlines()
    lines = [l for l in lines if not l.startswith("WEBHOOK_SECRET=")]
    lines.append("WEBHOOK_SECRET=" + secret)
    envf.write_text("\n".join(lines) + "\n")
    print("wrote WEBHOOK_SECRET to .env — (re)start the controller to pick it up")


def event_types():
    """Enumerate live (the docs never publish the full key list) and keep what
    we want plus any resume-ish key — its slug is undocumented."""
    catalog = vast.api("GET", "/notification-types/")
    RUN.mkdir(exist_ok=True)
    (RUN / "notification_types.json").write_text(json.dumps(catalog, indent=2))
    keys = set(re.findall(r"client:[a-z0-9_]+", json.dumps(catalog)))
    chosen = sorted((keys & WANTED) | {k for k in keys if "resum" in k})
    print("catalog has %d client keys; subscribing: %s" % (len(keys), chosen))
    return chosen or sorted(WANTED)


def find_mine():
    resp = vast.api("GET", "/webhooks/")
    hooks = resp.get("webhooks", resp) if isinstance(resp, dict) else resp
    for h in hooks or []:
        if h.get("name") == NAME:
            return h
    return None


def fire_test(hook_id):
    t0 = time.time()
    vast.api("POST", "/webhooks/%s/test/" % hook_id)
    print("test event fired — watching events.jsonl for a verified delivery...")
    while time.time() - t0 < 20:
        for rec in events.read(t0):
            if rec["type"] == "webhook.received" and rec.get("notif_type") == "webhook_test":
                print("PASS: signed test delivery verified in %.1fs" % (rec["ts"] - t0))
                return True
        time.sleep(1)
    print("FAIL: no verified test delivery in 20s.\n"
          "  - controller running?  make controller\n"
          "  - tunnel up + URL current?  make tunnel && make webhook\n"
          "  - secret stale (PUT may not return it)? rerun with --rotate")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-only", action="store_true")
    ap.add_argument("--rotate", action="store_true", help="rotate the secret and store it")
    args = ap.parse_args()

    mine = find_mine()
    if args.test_only:
        if not mine:
            sys.exit("no webhook named %r yet — run without --test-only first" % NAME)
        sys.exit(0 if fire_test(mine["id"]) else 1)

    url = (RUN / "tunnel_url").read_text().strip().rstrip("/") + "/webhook"
    types = event_types()
    if mine:
        vast.api("PUT", "/webhooks/%s/" % mine["id"],
                 {"webhook_url": url, "event_types": types})
        print("updated webhook %s -> %s" % (mine["id"], url))
        if args.rotate:
            r = vast.api("POST", "/webhooks/%s/rotate-secret/" % mine["id"])
            write_secret(r["webhook"]["webhook_secret"])
    else:
        r = vast.api("POST", "/webhooks/",
                     {"name": NAME, "webhook_url": url, "event_types": types})
        mine = r["webhook"]
        print("created webhook %s -> %s" % (mine["id"], url))
        write_secret(mine["webhook_secret"])  # only moment it's ever visible
    fire_test(mine["id"])


if __name__ == "__main__":
    main()
