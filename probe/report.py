"""Distill events.jsonl into PROBE.md — measured answers to the questions the
Vast docs leave open. Run after the cheap-instance probe/rehearsal session."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from controller import events  # noqa: E402

RUN = ROOT / "run"


def first_after(evs, i, etype, **match):
    for e in evs[i + 1:]:
        if e["type"] == "kill.initiated":
            return None
        if e["type"] == etype and all(e.get(k) == v for k, v in match.items()):
            return e
    return None


def main():
    evs = events.read()
    st = json.loads((RUN / "state.json").read_text()) if (RUN / "state.json").exists() else {}
    iid = str(st.get("instance_id", ""))
    rows = []

    ntf = RUN / "notification_types.json"
    if ntf.exists():
        keys = sorted(set(re.findall(r"(?:client|host):[a-z0-9_]+", ntf.read_text())))
        rows.append(("Full notification-type key list (docs never publish it)",
                     "GET /api/v0/notification-types/", ", ".join(keys)))

    kills = [(i, e) for i, e in enumerate(evs) if e["type"] == "kill.initiated"]
    for i, kill in kills:
        lever = kill.get("lever")
        det = first_after(evs, i, "interruption.detected")
        rows.append(("Does %s pause the instance?" % lever,
                     "kill.initiated -> interruption.detected",
                     "YES, detected (%s) after %.1fs; intended_status=%r"
                     % (det.get("source"), det["ts"] - kill["ts"], det.get("intended"))
                     if det else "no interruption observed"))
        wh = None
        for e in evs[i + 1:]:
            if e["type"] == "kill.initiated":
                break
            if e["type"] == "webhook.received" and e.get("notif_type") != "webhook_test":
                wh = e
                break
        if wh:
            has_id = iid and iid in (wh.get("message") or "") + (wh.get("subject") or "")
            rows.append(("Does the webhook fire for %s, and how fast?" % lever,
                         "webhook.received after kill.initiated",
                         "YES: notif_type=%r after %.1fs" % (wh.get("notif_type"),
                                                             wh["ts"] - kill["ts"])))
            rows.append(("Does the webhook payload identify the instance?",
                         "instance id substring in subject/message",
                         ("YES — %r appears in the text (still undocumented; "
                          "don't build on it)" % iid) if has_id else
                         "NO — payload has no usable instance reference; polling is mandatory"))
        else:
            rows.append(("Does the webhook fire for %s?" % lever,
                         "webhook.received after kill.initiated",
                         "NO webhook observed — poller caught it alone"))
        res = first_after(evs, i, "resume.detected")
        if res:
            fb = res.get("start_fallback_used")
            rows.append(("Does change-bid alone un-pause, or is `start` needed?",
                         "resume.detected.start_fallback_used",
                         "rebid alone was enough" if fb is False else
                         ("needed the explicit start-instance fallback" if fb
                          else "resumed via %s" % res.get("frm"))))
            rows.append(("Kill -> resumed latency (lever=%s)" % lever,
                         "resume.detected - kill.initiated",
                         "%.1fs end-to-end" % (res["ts"] - kill["ts"])))

    boots = sorted({e.get("boot_count") for e in evs
                    if e["type"] == "worker.progress" and e.get("boot_count")})
    if boots:
        rows.append(("Does onstart re-run on resume (crash-only boot path works)?",
                     "worker.progress.boot_count across episodes",
                     "boot_count values observed: %s -> %s" %
                     (boots, "YES, re-runs" if len(boots) > 1 else "only one boot seen so far")))

    lines = ["# PROBE.md — measured mechanics the docs don't state", "",
             "Every row derives from timestamped records in `run/events.jsonl` "
             "(single writer, single clock).", "",
             "| Question | Method | Measured answer |", "|---|---|---|"]
    for q, m, a in rows:
        lines.append("| %s | `%s` | %s |" % (q, m, a))
    (ROOT / "PROBE.md").write_text("\n".join(lines) + "\n")
    print("wrote PROBE.md with %d rows" % len(rows))


if __name__ == "__main__":
    main()
