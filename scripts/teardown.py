"""Destroy ONLY instances this repo created (run/created_instances.jsonl),
then verify the account is empty of them. The whole ops story."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from controller import events, vast  # noqa: E402

CREATED = ROOT / "run" / "created_instances.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    ours = set()
    if CREATED.exists():
        ours = {json.loads(l)["id"] for l in CREATED.read_text().splitlines()}
    live = vast.show_instances()
    targets = [i for i in live if i["id"] in ours]
    strangers = [i for i in live if i["id"] not in ours]
    for i in strangers:
        print("NOT touching %s (label=%r) — not created by this repo" % (i["id"], i.get("label")))
    if not targets:
        print("nothing of ours is running.")
        return
    for i in targets:
        print("will destroy %s label=%r status=%s" % (i["id"], i.get("label"), i.get("actual_status")))
    if not args.yes:
        sys.exit("dry run — rerun with --yes to destroy")
    for i in targets:
        vast.destroy_instance(i["id"])
        events.emit("teardown.destroyed", instance_id=i["id"])
        print("destroyed %s" % i["id"])
    remaining = [i["id"] for i in vast.show_instances() if i["id"] in ours]
    print("verify: %s" % ("EMPTY — all ours gone" if not remaining else
                          "STILL ALIVE: %s" % remaining))


if __name__ == "__main__":
    main()
