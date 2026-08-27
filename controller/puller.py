"""Pull status.json + new thumbnails off the worker over scp.

Display only — the durability story is the instance disk surviving the pause,
not this. Small files, every 10 s, only while the instance is reachable.
"""
import json
import subprocess
import time
from pathlib import Path

from . import events, vast

ROOT = Path(__file__).resolve().parent.parent
PULLED = ROOT / "run" / "pulled"
THUMBS = PULLED / "thumbs"


def scp(host, port, remote, local):
    r = subprocess.run(["scp", "-P", str(port)] + vast.ssh_opts() +
                       ["root@%s:%s" % (host, remote), str(local)],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def pull_once(job):
    host, port = job.get("ssh_host"), job.get("ssh_port")
    if not host:
        return None
    PULLED.mkdir(parents=True, exist_ok=True)
    THUMBS.mkdir(exist_ok=True)
    if not scp(host, port, "/root/job/status.json", PULLED / "status.json"):
        return None
    try:
        status = json.loads((PULLED / "status.json").read_text())
    except ValueError:
        return None
    for name in status.get("recent", []):
        name = Path(name).name
        if not (THUMBS / name).exists():
            scp(host, port, "/root/job/thumbs/" + name, THUMBS / name)
    if job.get("payload") == "qlora":
        scp(host, port, "/root/job/loss.csv", PULLED / "loss.csv")
    return status


def run_loop(controller):
    last = {}
    while True:
        time.sleep(10)
        if controller.state in ("IDLE", "DEAD") or not controller.job.get("instance_id"):
            continue
        try:
            status = pull_once(controller.job)
        except Exception as e:
            events.emit("pull.error", error=str(e)[:200])
            continue
        if not status:
            continue
        key = (status.get("images_done"), status.get("boot_count"))
        if key != (last.get("images_done"), last.get("boot_count")):
            events.emit("worker.progress", images_done=status.get("images_done"),
                        total=status.get("total"), boot_count=status.get("boot_count"),
                        avg_s=status.get("avg_s_per_image"))
        last = status
        controller.mark_worker(status)
