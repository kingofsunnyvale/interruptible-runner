"""Thin wrappers over the vastai CLI (--raw JSON) and the console REST API.

The CLI is already authenticated and returns JSON with --raw, so shelling out
removes an entire class of auth/HTTP code. The raw API is used only where the
CLI has no command (webhook management, charges).
"""
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://console.vast.ai/api/v0"
KEY_FILE = Path.home() / ".config/vastai/vast_api_key"


def cli(*args, timeout=90):
    proc = subprocess.run(("vastai",) + args + ("--raw",),
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("vastai %s failed: %s" %
                           (" ".join(args), (proc.stderr or proc.stdout).strip()[:500]))
    body = proc.stdout.strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:  # some commands (e.g. change bid) print text despite --raw
        return {"raw_text": body}


def api(method, path, body=None, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": "Bearer " + KEY_FILE.read_text().strip()}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:  # surface the API's error body
        raise RuntimeError("%s %s -> %s: %s"
                           % (method, path, e.code, e.read().decode()[:300])) from None


def ssh_opts():
    """Shared ssh/scp options. The account's registered key is the dedicated
    ~/.ssh/vastai_ed25519, which BatchMode won't offer unless told to."""
    opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=6", "-o", "LogLevel=ERROR"]
    key = Path.home() / ".ssh" / "vastai_ed25519"
    if key.exists():
        opts += ["-i", str(key)]
    return opts


def ssh_target(inst):
    """Direct ssh: public IP + the external port mapped to container port 22.
    The ssh_host/ssh_port fields point at Vast's proxy — slower, last resort."""
    ports = inst.get("ports") or {}
    mapped = ports.get("22/tcp")
    if inst.get("public_ipaddr") and mapped:
        return inst["public_ipaddr"], int(mapped[0]["HostPort"])
    return inst.get("ssh_host"), inst.get("ssh_port")


def show_instance(iid):
    return cli("show", "instance", str(iid))


def show_instances():
    return cli("show", "instances") or []


def search_offers(query, type_="bid", order="min_bid", storage=None):
    args = ["search", "offers", query, "-t", type_, "-o", order]
    if storage:
        args += ["--storage", str(storage)]
    return cli(*args) or []


def change_bid(iid, price):
    return cli("change", "bid", str(iid), "--price", "%.3f" % price)


def start_instance(iid):
    return cli("start", "instance", str(iid))


def destroy_instance(iid):
    return cli("destroy", "instance", str(iid), "-y")
