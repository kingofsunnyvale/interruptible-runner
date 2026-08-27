# RUNBOOK — live demo script + fallbacks

## T-1 day
- [ ] Probe/rehearsal done on a cheap slice (see "Live block 1"), PROBE.md committed.
- [ ] Primary lever chosen from probe results (default: bid-drop).
- [ ] `.env` has WEBHOOK_SECRET; `make webhook-test` prints PASS.

## Session bring-up (any session, ~5 min before instances)
Four terminals, in order:
1. `make tunnel` — quick-tunnel URL changes each start; that's expected.
2. `make controller` — leave running (dashboard at http://localhost:8080).
3. `make webhook` — re-points Vast at the new tunnel URL + fires signed test;
   wait for PASS in the controller timeline.
4. `make launch PAYLOAD=... GPU=...`

## Live block 1 — probe + rehearsal (~20 min, cheap slice, <$0.05)
- `make launch PAYLOAD=noop GPU=cheap`
- Watch RUNNING + worker.progress ticks on the dashboard.
- `make interrupt` (bid-drop) → watch: interruption.detected → webhook? →
  rebid.request/response → resume path (rebid alone vs start_fallback) →
  boot_count bump.
- Optional second kill to double-check timings.
- `make teardown YES=1` → `make probe-report` → commit PROBE.md.

## Live block 2 — the real demo (~30 min, 4090)
Probe-informed lever choice: **od-rent is the primary lever** (guaranteed
preemption AND fires the outbid webhook, ~15 s, with a quotable message);
bid-drop kills silently (no webhook) — use it to demo the poller fallback.
Expect `actual_status: exited` on the dashboard triple — that's the measured
outbid signature, not a crash.

- `make launch PAYLOAD=sdxl GPU=4090` (cold start ≈5–7 min incl. weights).
- **Beat 1 — tour**: gallery filling, cost ticker, "our bid $X vs on-demand $Y
  on this same machine".
- **Beat 2 — pull the plug**: dashboard button with lever=od-rent (or `make
  interrupt LEVER=od-rent`). Narrate the chips: kill → outbid webhook (+~15s,
  quoting "current minimum bid is $0.33/hr") → auto-rebid → STARVED. Talk
  track: "no bid beats on-demand; work is parked, not lost."
- **Beat 3 — resume**: `make restore` → Vast resumes the container on its own
  (~40-60 s), gallery continues at image N+1, boot #2 divider, no gaps, no
  duplicates. Zero lost work.
- **Beat 4 (optional encore) — silent kill**: `make interrupt LEVER=bid-drop`
  → no webhook arrives (measured) → the 10 s poller catches it anyway →
  auto-rebid above the floor → 45 s start-fallback revives it. Talk track:
  "webhook is an accelerant; polling is truth."
- **Beat 5 — receipt preview**: cost pane; exact charges tomorrow via
  `make receipt`.
- Wrap: `make pull`, screenshot dashboard, `make teardown YES=1`, verify EMPTY.

## Fallback matrix
| Symptom | Detection | Action | Talk track |
|---|---|---|---|
| Tunnel dies / venue wifi blocks it | no webhook chip | nothing — poller detects ≤10 s | "webhook is an accelerant; polling is truth" |
| Webhook never fires for the lever | same | same | same |
| Resume starves (GPU taken) | STARVED state | narrate economics; optionally `make launch` on another machine | "new machine = new disk; the render restarts — that's why long jobs also checkpoint off-box" |
| Instance hits unknown/offline | DEAD state | `make teardown YES=1`; relaunch | host vanished; only these two are truly dead (`exited` is just an outbid — measured) |
| SDXL slower than expected | avg s/image pane | lower STEPS in .env before launch | knob, not bug |
| Controller crashed | dashboard gone | restart `make controller` — events.jsonl replays, dedupe intact | crash-only applies to us too |

## Money checklist (every session end)
`make teardown YES=1` → verify prints EMPTY → `vastai show instances` empty.
Next morning: `make receipt` → commit RECEIPT.md + curated evidence.
