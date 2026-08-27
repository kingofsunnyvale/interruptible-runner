"""Generate RECEIPT.md: exact charges from Vast's billing API, the on-demand
counterfactual, and the interruption latency table from events.jsonl.

Charges lag by hours — run this the morning after; the dashboard's ticker is
the live estimate, this is the audited version.
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from controller import events, vast  # noqa: E402

RUN = ROOT / "run"


def our_ids():
    f = RUN / "created_instances.jsonl"
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text().splitlines():
        rec = json.loads(line)
        out[rec["id"]] = rec
    return out


def charges(day0, day1):
    filt = {"day": {"gte": day0, "lte": day1}, "type": {"in": ["instance"]}}
    resp = vast.api("GET", "/charges",
                    params={"select_filters": json.dumps(filt), "limit": 500})
    return resp.get("results", [])


def latency_table(evs):
    """Each kill.initiated opens an episode; collect the milestones after it."""
    rows = []
    for i, e in enumerate(evs):
        if e["type"] != "kill.initiated":
            continue
        row = {"lever": e.get("lever"), "kill_ts": e["ts"], "webhook": None,
               "detected": None, "rebid": None, "resume": None}
        for later in evs[i + 1:]:
            if later["type"] == "kill.initiated":
                break
            d = round(later["ts"] - e["ts"], 1)
            if later["type"] == "webhook.received" and row["webhook"] is None \
                    and later.get("notif_type") != "webhook_test":
                row["webhook"] = (later.get("notif_type"), d)
            elif later["type"] == "interruption.detected" and row["detected"] is None:
                row["detected"] = (later.get("source"), d)
            elif later["type"] == "rebid.response" and later.get("ok") and row["rebid"] is None:
                row["rebid"] = d
            elif later["type"] == "resume.detected" and row["resume"] is None:
                row["resume"] = d
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="lookback window")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    day0 = int((now - dt.timedelta(days=args.days)).timestamp())
    ids = our_ids()

    lines = ["# Cost receipt", "",
             "Generated %s over the last %d day(s). Sources: Vast charges API "
             "(exact), run/events.jsonl (latencies)." % (now.strftime("%Y-%m-%d %H:%M UTC"),
                                                         args.days), ""]

    total = 0.0
    gpu_hours_by_id = {}
    lines += ["## Charges (exact, per Vast billing)", ""]
    for c in charges(day0, int(now.timestamp())):
        m = re.match(r"instance-(\d+)", c.get("source", ""))
        if not m or int(m.group(1)) not in ids:
            continue
        iid = int(m.group(1))
        lines.append("**Instance %s** (%s) — $%.3f" %
                     (iid, ids[iid].get("purpose", "?"), c.get("amount", 0)))
        total += c.get("amount", 0)
        for item in c.get("items", []):
            lines.append("  - %s: %s — $%.3f" %
                         (item.get("type"), item.get("description"), item.get("amount", 0)))
            if item.get("type") == "gpu":
                h = re.match(r"([\d.]+) hours", item.get("description", ""))
                if h:
                    gpu_hours_by_id[iid] = gpu_hours_by_id.get(iid, 0) + float(h.group(1))
        lines.append("")
    lines += ["**Total paid: $%.3f**" % total, ""]

    st = json.loads((RUN / "state.json").read_text()) if (RUN / "state.json").exists() else {}
    od = st.get("on_demand_dph")
    if od and gpu_hours_by_id:
        gh = sum(gpu_hours_by_id.values())
        counter = gh * od
        lines += ["## vs on-demand on the same machine", "",
                  "%.2f GPU-hours x $%.3f/hr on-demand = **$%.3f**; we paid "
                  "**$%.3f** -> **%.0f%% saved**." %
                  (gh, od, counter, total, 100 * (counter - total) / counter if counter else 0), ""]

    lines += ["## Interruption episodes (one clock: the controller's)", "",
              "| lever | ->webhook | ->detected | ->rebid ok | ->resumed |",
              "|---|---|---|---|---|"]
    for r in latency_table(events.read()):
        wb = "%s @ %ss" % r["webhook"] if r["webhook"] else "—"
        det = "%s @ %ss" % r["detected"] if r["detected"] else "—"
        lines.append("| %s | %s | %s | %ss | %ss |" %
                     (r["lever"], wb, det, r["rebid"] or "—", r["resume"] or "—"))

    status = RUN / "pulled" / "status.json"
    if status.exists():
        w = json.loads(status.read_text())
        lines += ["", "## Throughput", "",
                  "%s: %s/%s images, %.1f s/image avg, boots=%s" %
                  (w.get("payload"), w.get("images_done"), w.get("total"),
                   w.get("avg_s_per_image") or 0, w.get("boot_count"))]

    (ROOT / "RECEIPT.md").write_text("\n".join(lines) + "\n")
    print("wrote RECEIPT.md (total paid across ours: $%.3f)" % total)


if __name__ == "__main__":
    main()
