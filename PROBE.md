# PROBE.md — measured mechanics the docs don't state

Session 2026-08-27, ~50 min, ≈$0.07. Instruments: a $0.031/hr interruptible
RTX 4060 slice (instances 48926820, 48927380 on machines 101502, 46110), the
controller's single-clock event log (`docs/evidence/probe-2026-08-27/
events.jsonl`), and a 10 s raw-status poll. Three interruptions were observed:
one **organic** (a stranger outbid us within minutes — the cheap-slice market
is genuinely contested), one **self-bid-drop**, one **od-rent self-preempt**.

| # | Question | Measured answer |
|---|---|---|
| 1 | What does an outbid look like in the status fields? | `intended_status` flips `running -> stopped` within ~1.4 s of losing priority; `actual_status` -> **`exited`** ~10-30 s later. Observed identically in all three interruptions. The docs-implied `stopped`-while-`intended=running` signature **never occurred** — intended flips too. `status_msg` keeps saying `success, running ...` throughout. |
| 2 | Is `exited` terminal (the documented "poll trap")? | **No, not for interruptibles.** Outbid-induced `exited` was revived twice: once by explicit `start instance` (running ~30 s later), once fully autonomously. The "destroy and retry on exited" advice would have destroyed a perfectly resumable instance. |
| 3 | Does `client:outbid` fire, and how fast? | Organic outbid: **yes**, delivered before the container even stopped — subject says "will be stopped" (future tense). od-rent self-preempt: **yes, ~15 s** after the on-demand create. Self-bid-drop: **no webhook at all** (observed 45+ min). |
| 4 | Does the webhook identify the instance? | The documented JSON schema has no instance field, but the human text does: subject `Your Interruptible Instance #48927380 Has Been Outbid`, message carries machine id, your bid, and the price to beat ("current minimum bid is $0.80/hr"). Parseable, but unstructured — poll-confirm remains mandatory. |
| 5 | Do `instance_stopped` / `instance_resumed` webhooks fire? | **Never observed** despite being subscribed, across 3 stops and 2 resumes. `instance_started` fired only for the initial boot (~3 s delivery). Do not build on stop/resume notifications. |
| 6 | Does raising the bid alone un-pause the instance? | After self-bid-drop: **no** — 90 s with a winning bid, still `exited`; explicit `start` fixed it. After the od tenant left: **yes** — autonomous resume, no client action (`intended` flipped back ~20 s after the od destroy, running ~40-60 s). The controller keeps a 45 s start-instance fallback for the first case. |
| 7 | What does instance-level `min_bid` mean? | **max(machine floor, your own current bid × 1.2)** — a *minimum-raise increment*, not the market price. It tracked our own bid up (0.0267 → 0.0376 → 0.047 as we bid 0.001 → 0.031 → 0.040; floor per `search offers` stayed 0.028). Chasing it after your own rebid spirals your bid to the ceiling. During an interruption it shows ≈ the floor. |
| 8 | What does bare `vastai change bid` (no --price) set? | Floor 0.028 → it set **0.033** (~floor × 1.18). It **lowered** our existing 0.040 bid — "a winning bid price" is a target, not a raise. Output: `Per gpu bid price changed`, which also resolves the docs' per-machine vs per-GPU contradiction: **per GPU**. |
| 9 | Where is my current bid readable? | `dph_base` on the instance record (`dph_total` = bid + storage $/hr). |
| 10 | Does onstart re-run on resume? | **Yes, on every start**: `/root/boots.log` grew to 3 lines (create, post-bid-drop start, post-od autonomous resume). Disk fully intact each time — 100/100 output files survived all three kills. Crash-only onstart + skip-existing resume is the correct shape. |
| 11 | Webhook test endpoint gotchas | `POST /webhooks/{id}/test/` first probes deliverability: a receiver that answers 401 (wrong secret) yields `400 "Webhook URL is not deliverable"` — misleading; fix the secret, not the URL. With the right secret: signed delivery verified in 0.7 s through a cloudflared quick tunnel. |
| 12 | The resumed-event slug | Enumerated live via `GET /notification-types/`: **`client:instance_resumed`** (24 client keys total; catalog archived in evidence dir). Subscribed but never received (see #5). |
| 13 | CLI misc | `create instance` reports failures (e.g. `410 no_such_ask` — routine, cheap offers churn in seconds) as JSON with **exit code 0**; `change bid` prints plain text despite `--raw`; `destroy` needs `-y`. |

## Demo consequences

- **Primary lever: od-rent** (guaranteed preemption + fires the outbid webhook
  with a quotable message). **Backup: bid-drop** (silent kill — demos the
  poller-is-truth fallback instead).
- Detection must key on `actual_status in (stopped, exited)`, not on
  `intended_status`, and must NOT treat `exited` as dead.
- Rebid policy must ignore instance `min_bid` after its own rebids (×1.2
  self-echo) — the controller gates "market moved" at ×1.3.
- After rebidding, wait ~45 s for autonomous resume, then `start instance`.
