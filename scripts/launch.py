"""Search offers, create an interruptible instance, wait, push the payload.

Money rules honored here: `vastai show instances` is printed before creating
anything, every created id is appended to run/created_instances.jsonl (the
only ids teardown will ever touch), and --cancel-unavail ensures a losing bid
errors out instead of silently creating a stopped, storage-billing instance.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from controller import events, vast  # noqa: E402
from controller.state import load_env  # noqa: E402

RUN = ROOT / "run"
CREATED = RUN / "created_instances.jsonl"
IMAGE = "vastai/pytorch:2.6.0-cuda-12.6.3-py312"

QUERIES = {
    "4090": ("gpu_name=RTX_4090 num_gpus=1 gpu_frac=1 rentable=true "
             "reliability>0.98 inet_down>200 disk_space>40"),
    "cheap": "num_gpus=1 gpu_frac=1 rentable=true reliability>0.98 inet_down>100",
}
def record_created(iid, **fields):
    RUN.mkdir(exist_ok=True)
    with CREATED.open("a") as f:
        f.write(json.dumps({"id": iid, "ts": round(time.time(), 1), **fields}) + "\n")


def on_demand_price(machine_id, num_gpus):
    """The same machine's on-demand $/hr — the receipt's counterfactual."""
    for q in ("machine_id=%s num_gpus=%s gpu_frac=1" % (machine_id, num_gpus),
              "machine_id=%s" % machine_id):
        offers = vast.search_offers(q, type_="on-demand", order="dph_total")
        if offers:
            return round(offers[0]["dph_total"], 4)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", choices=["sdxl", "qlora", "noop"], default="sdxl")
    ap.add_argument("--gpu", choices=["4090", "cheap"], default="4090")
    args = ap.parse_args()

    existing = vast.show_instances()
    print("existing instances: %s" % ([(i["id"], i.get("label"), i.get("actual_status"))
                                       for i in existing] or "none"))

    offers = vast.search_offers(QUERIES[args.gpu], type_="bid", order="min_bid")
    offers = [o for o in offers if o.get("min_bid")]
    if not offers:
        sys.exit("no interruptible offers matched")
    disk = 40 if args.payload in ("sdxl", "qlora") else 12
    env = load_env()
    envstr = "-e PAYLOAD=%s -e N_IMAGES=%s -e STEPS=%s" % (
        args.payload, env.get("N_IMAGES", "100"), env.get("STEPS", "25"))

    # Cheap offers churn fast (410 no_such_ask is routine) and the CLI reports
    # such errors as JSON with exit code 0 — walk down the list until one takes.
    iid = None
    for offer in offers[:6]:
        machine, gpus = offer["machine_id"], offer.get("num_gpus", 1)
        min_bid = offer["min_bid"]
        bid = round(min_bid * 1.15, 3)
        print("offer %s machine %s %s: min_bid $%.3f -> bidding $%.3f"
              % (offer["id"], machine, offer.get("gpu_name"), min_bid, bid))
        created = vast.cli("create", "instance", str(offer["id"]),
                           "--image", IMAGE, "--disk", str(disk),
                           "--bid_price", str(bid), "--ssh", "--direct",
                           "--onstart", str(ROOT / "worker" / "onstart.sh"),
                           "--env", envstr, "--label", "ir-" + args.payload,
                           "--cancel-unavail")
        if created and created.get("new_contract"):  # id lives here, not in "id"
            iid = created["new_contract"]
            break
        print("  offer gone (%s) — trying next" % (created or {}).get("msg", "no response"))
    if not iid:
        sys.exit("all candidate offers refused — rerun")
    od = on_demand_price(machine, gpus)
    # offer storage_cost is $/GB/month -> convert to $/hr for our disk size
    storage_dph = round((offer.get("storage_cost") or 0.15) * disk / 730, 5)
    print("created %s on machine %s (on-demand there: $%s/hr)" % (iid, machine, od))
    record_created(iid, purpose=args.payload, machine_id=machine, offer_id=offer["id"], bid=bid)
    t0 = time.time()
    events.emit("launch.created", instance_id=iid, machine_id=machine, bid=bid,
                min_bid=min_bid, on_demand_dph=od, gpu_name=offer.get("gpu_name"),
                payload=args.payload)
    print("created instance %s — waiting for running" % iid)

    inst = None
    while time.time() - t0 < 600:
        time.sleep(5)
        inst = vast.show_instance(iid) or {}
        st = inst.get("actual_status")
        print("  %5.0fs %s %s" % (time.time() - t0, st, (inst.get("status_msg") or "").strip()[:70]))
        if st == "running":
            break
        if st in ("exited", "unknown", "offline"):  # documented poll trap
            vast.destroy_instance(iid)
            sys.exit("instance hit terminal state %r — destroyed; rerun to try the next offer" % st)
    else:
        sys.exit("timed out waiting for running (instance %s left up — inspect or teardown)" % iid)

    cold = round(time.time() - t0, 1)
    host, port = vast.ssh_target(inst)
    payload_file = "train_qlora.py" if args.payload == "qlora" else "render_sdxl.py"
    r = None
    for _ in range(10):  # sshd may lag a few seconds behind "running"
        r = subprocess.run(["scp", "-P", str(port)] + vast.ssh_opts() +
                           [str(ROOT / "worker" / payload_file),
                            "root@%s:/root/job/job.py" % host],
                           capture_output=True, text=True)
        if r.returncode == 0:
            break
        time.sleep(8)
    else:
        sys.exit("could not scp payload to %s:%s — %s"
                 % (host, port, (r.stderr.strip()[:200] if r else "")))

    RUN.mkdir(exist_ok=True)
    (RUN / "state.json").write_text(json.dumps({
        "instance_id": iid, "machine_id": machine, "payload": args.payload,
        "bid": bid, "on_demand_dph": od, "storage_dph": storage_dph,
        "ssh_host": host, "ssh_port": port, "disk": disk,
        "gpu_name": offer.get("gpu_name"), "created_ts": round(t0, 1)}, indent=2))
    events.emit("launch.ready", instance_id=iid, cold_start_secs=cold)
    print("ready in %.0fs — ssh root@%s -p %s ; payload pushed" % (cold, host, port))


if __name__ == "__main__":
    main()
