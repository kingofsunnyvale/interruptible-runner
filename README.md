# interruptible-runner

A batch job on Vast.ai's cheapest tier — an interruptible RTX 4090 — killed on
purpose and resumed with zero lost work, with the receipts to prove it.

An SDXL render (100 images) runs on a bid-priced GPU. A laptop-side controller
receives Vast's outbid webhook through a cloudflared tunnel, confirms via
polling, re-raises the bid under a cost ceiling, and the job resumes from the
instance's surviving disk. Interruptions are triggered on demand, two ways:
rent your own machine on-demand (guaranteed preemption that fires the outbid
webhook), or drop your own bid (a silent kill only the poller catches).

## Measured (RTX 4090, machine 31129, 2026-08-27)

- **$0.144/hr bid vs $0.321/hr on-demand on the same machine — 55% saved.**
  Exact per-instance charges: `make receipt`.
- Kill → outbid webhook on the laptop: **2.8 s** (it arrives *before* the
  container stops). Kill → poller confirmation: 14.1 s. Kill → new bid
  accepted: **14.6 s**.
- GPU freed → autonomous resume by Vast's scheduler: **~20 s**, onstart re-ran,
  render continued at the next missing image. 100/100 images completed across
  3 boots, 2 staged kills, and 2 organic outbids by real bidders. No gaps, no
  duplicates.
- Throughput: ~3.5–4.1 s/image (SDXL base, fp16, 1024², 25 steps).

## What the docs don't tell you (measured, not assumed)

- An outbid flips `intended_status` to `stopped` within seconds, then
  `actual_status` goes **`exited`** — not `stopped`, and despite the documented
  "exited is terminal, destroy and retry" advice, it's fully recoverable.
- Instance-level `min_bid` is **your own bid × 1.2** (a minimum raise
  increment). Chase it after your own rebid and you'll spiral to your ceiling;
  the true floor is on the offer, via `search offers`.
- Resume is autonomous when the preempting tenant leaves, but after a
  self-bid-drop it needs an explicit `start instance` — the controller does
  both (45 s start fallback, warm retries while starved).
- Dropping your bid releases the GPU to the market instantly; on a contested
  machine someone else grabs it in seconds. On-demand self-rent keeps the GPU
  under your control — that's why it's the demo's primary kill switch.
- The webhook payload's JSON has no instance id, but the subject/message text
  carries the id, machine, your bid, and the price to beat.

## Design rules that fall out of this

- **Poller is the source of truth; the webhook just triggers an off-cycle
  poll.** (The payload can't be trusted to identify the instance, and the
  silent-kill path never fires one.)
- **The worker is crash-only**: deterministic work list, skip-what-exists
  resume, atomic tmp→rename writes, idempotent `onstart` that re-runs on every
  boot. There is no shutdown handler because there is no shutdown warning.
- **Bid policy is deterministic**: ceiling = min(MAX_HOURLY, 90% of the same
  machine's on-demand price); target = clamp(max(floor × 1.05, bid + $0.02)).

## Run it

```
brew install cloudflared && cp .env.example .env   # once
make tunnel        # terminal 1: HTTPS tunnel for webhook delivery
make controller    # terminal 2: dashboard at localhost:8080
make webhook       # register tunnel URL with Vast + signed test delivery
make launch PAYLOAD=sdxl GPU=4090
make interrupt LEVER=od-rent    # pull the plug (webhook path)
make restore                    # free the GPU; watch it resume on its own
make interrupt LEVER=bid-drop   # silent kill (poller path)
make teardown YES=1             # ALWAYS at session end
make receipt                    # next morning: audited charges -> RECEIPT.md
```

Laptop side is stdlib-only Python (~500 lines across `controller/` and
`scripts/`); GPU-side deps install once on first boot. Every event is a
timestamped line in `run/events.jsonl` — the dashboard timeline, the latency
numbers above, and the receipt all derive from that one file.
