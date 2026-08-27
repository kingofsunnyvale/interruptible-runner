"""The two self-interruption levers.

bid-drop: set our own bid to $0.001 — below any floor, so we lose priority.
od-rent: rent the SAME machine on-demand from our own account — documented to
always preempt interruptibles. Note the od rental blocks our resume until
`--lever restore` destroys it (that IS the STARVED demo beat).

Invoked via the controller's /api/control/interrupt so kill.initiated gets
timestamped on the controller's clock; runnable standalone for recovery.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from controller import events, vast  # noqa: E402

RUN = ROOT / "run"
CREATED = RUN / "created_instances.jsonl"


def state():
    return json.loads((RUN / "state.json").read_text())


def record(iid, **fields):
    with CREATED.open("a") as f:
        f.write(json.dumps({"id": iid, "ts": round(time.time(), 1), **fields}) + "\n")


def bid_drop():
    st = state()
    vast.change_bid(st["instance_id"], 0.001)
    print("bid on %s dropped to $0.001" % st["instance_id"])


def od_rent():
    st = state()
    offers = vast.search_offers("machine_id=%s rentable=true" % st["machine_id"],
                                type_="on-demand", order="dph_total")
    if not offers:
        sys.exit("no on-demand offer on machine %s right now" % st["machine_id"])
    o = offers[0]
    r = vast.cli("create", "instance", str(o["id"]),
                 "--image", "vastai/pytorch:2.6.0-cuda-12.6.3-py312",  # cached on host
                 "--disk", "10", "--ssh", "--label", "ir-od-preempt")
    iid = r["new_contract"]
    record(iid, purpose="od-preempt", machine_id=st["machine_id"], dph=o["dph_total"])
    events.emit("od.rented", instance_id=iid, dph=o["dph_total"])
    print("on-demand instance %s created at $%.3f/hr — it now owns the GPU" % (iid, o["dph_total"]))


def restore():
    live = {i["id"] for i in vast.show_instances()}
    victims = []
    if CREATED.exists():
        for line in CREATED.read_text().splitlines():
            rec = json.loads(line)
            if rec.get("purpose") == "od-preempt" and rec["id"] in live:
                victims.append(rec["id"])
    for iid in victims:
        vast.destroy_instance(iid)
        events.emit("od.destroyed", instance_id=iid)
        print("destroyed od-preempt instance %s — GPU freed for our standing bid" % iid)
    if not victims:
        print("no live od-preempt instances")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lever", choices=["bid-drop", "od-rent", "restore"], required=True)
    a = ap.parse_args()
    {"bid-drop": bid_drop, "od-rent": od_rent, "restore": restore}[a.lever]()
