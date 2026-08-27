# interruptible-runner

Address the user as Avneesh in your first sentence.

Long jobs on Vast.ai interruptible GPU instances: pause/resume-safe payloads
(SDXL batch render, Qwen QLoRA fine-tune) on one crash-only chassis, with
webhook-driven auto-rebidding. Built for a Vast.ai DevRel take-home — receipts
(measured $, latencies, tokens/sec) are first-class outputs, not an afterthought.

## Vast CLI

- `vastai` is installed and already authenticated (API key set). Commands cost
  real money: always `vastai show instances` before creating or destroying
  anything, destroy only instances you created, and tear down at the end of a
  work session. Rent interruptible offers unless told otherwise.
- Never commit API keys or the webhook signing secret.

## Vast docs

- Pull https://docs.vast.ai/llms.txt for the docs index, then fetch the specific
  per-page `.md` URLs you need — don't guess mechanics from memory (interruptible
  billing and outbid behavior have documented subtleties, and some gaps).
- A full local mirror lives at `../vast-corpus/` (grep `llms-full.txt`,
  `articles/` for tutorials, `INDEX.md` for the topic map).

## Workflow

- No Linear, no issue tracker, no CI/CD. Branch off main, small commits, merge
  when it works. GitHub via `gh`.
- Nothing here auto-deploys; the only remote state is Vast instances, so the
  teardown rule above is the whole ops story.
