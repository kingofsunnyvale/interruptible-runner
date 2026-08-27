# interruptible-runner

Long jobs on Vast.ai's cheapest tier — interruptible instances — killed on
purpose, resumed with zero lost work, receipts included.

**The demo:** an SDXL batch render runs on an interruptible RTX 4090. We
deliberately trigger our own interruption live; Vast's outbid webhook wakes a
laptop-side controller, which re-raises the bid under a cost ceiling; Vast
auto-resumes the container on the same machine; the render continues at image
N+1. A QLoRA fine-tune (payload 2) proves the same chassis holds when resume
actually requires checkpoint discipline: the loss curve continues across the
kill.

## Architecture

```
 laptop                                    Vast.ai
 ┌──────────────────────────────┐
 │ controller (one process)     │   webhook   ┌──────────────────┐
 │  :8081 /webhook ◄────────────┼─────────────┤ notifications     │
 │   (cloudflared quick tunnel) │             └──────────────────┘
 │  poller ────────────────────►│ show instance┌──────────────────┐
 │  rebidder ──────────────────►│ change bid   │ RTX 4090 (bid)   │
 │  :8080 dashboard (local)     │              │  onstart.sh      │
 │  puller ◄────────────────────┼── scp ───────│  render_sdxl.py  │
 └──────────────────────────────┘              └──────────────────┘
```

Design rule the whole thing hangs on: **the webhook payload has no instance
id**, so the poller is the source of truth and the webhook is a latency
optimization that forces an immediate off-cycle poll. And since an outbid
kills processes with **zero grace period**, the worker is crash-only: no
shutdown handlers, deterministic work list, skip-what-exists resume, atomic
writes only.

## Numbers (filled from measured artifacts)

| Figure | Value | Source |
|---|---|---|
| Savings vs on-demand (same machine) | TBD | RECEIPT.md |
| Kill → webhook delivery | TBD | events.jsonl (L1) |
| Kill → resumed | TBD | events.jsonl (L5) |
| Resume → first new image | TBD | timings.jsonl |
| SDXL throughput | TBD | status.json |
| QLoRA tokens/sec, loss continuity | TBD | loss.csv |
| Total spend, whole project | TBD | RECEIPT.md |

## What the docs don't tell you

See [PROBE.md](PROBE.md) — measured answers (with event-log evidence) to the
questions the Vast docs leave open: the outbid status signature, whether
change-bid alone un-pauses, webhook latency, onstart re-run behavior, and
which events actually fire when you interrupt yourself.

## Run it

```
brew install cloudflared      # once
cp .env.example .env          # once; make webhook fills WEBHOOK_SECRET
make tunnel                   # terminal 1
make controller               # terminal 2 (dashboard: localhost:8080)
make webhook                  # register tunnel URL + signed test delivery
make launch PAYLOAD=sdxl GPU=4090
make interrupt                # pull the plug (or the big red button)
make teardown YES=1           # ALWAYS, at session end
make receipt                  # next morning: exact charges -> RECEIPT.md
```

See [RUNBOOK.md](RUNBOOK.md) for the live-demo beats and the fallback matrix.
