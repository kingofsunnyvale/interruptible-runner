"""Thin wrappers over the vastai CLI (--raw JSON) and the console REST API.

The CLI is already authenticated and returns JSON with --raw, so shelling out
removes an entire class of auth/HTTP code. The raw API is used only where the
CLI has no command (webhook management, charges).
"""
import json
import subprocess
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
    return json.loads(body) if body else None


def api(method, path, body=None, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + KEY_FILE.read_text().strip(),
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"null")


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
    return cli("destroy", "instance", str(iid))
