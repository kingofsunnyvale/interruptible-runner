"""SDXL batch render, crash-only.

Interruption kills us with zero warning, so there is no shutdown handler and
no in-memory state worth protecting: the manifest is derived deterministically
from a fixed seed, each image's filename is its index, and "resume" is simply
skipping outputs that already exist. Every write is tmp -> os.replace, so a
mid-write kill leaves only a .tmp file that the next boot overwrites.

PAYLOAD=noop turns this into a one-file-per-second dummy loop — the full
kill/rebid/resume rehearsal for pennies on a cheap instance.
"""
import json
import os
import random
import time
from pathlib import Path

JOB = Path("/root/job")
OUT = JOB / "out"
THUMBS = JOB / "thumbs"

PAYLOAD = os.environ.get("PAYLOAD", "sdxl")
N = int(os.environ.get("N_IMAGES", "100"))
STEPS = int(os.environ.get("STEPS", "25"))

SUBJECTS = ["a lighthouse", "a red panda", "a steam locomotive", "a koi pond",
            "an observatory", "a street market", "a paper crane", "a tidal wave",
            "a mossy robot", "a hot air balloon", "a violin", "a desert caravan"]
STYLES = ["ukiyo-e woodblock print", "vaporwave poster", "oil on canvas",
          "isometric voxel art", "long-exposure photograph", "art nouveau mural",
          "technical blueprint", "claymation still"]


def boot_count():
    log = Path("/root/boots.log")
    return len(log.read_text().splitlines()) if log.exists() else 0


def atomic_write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data if isinstance(data, bytes) else data.encode())
    os.replace(tmp, path)


def manifest():
    """(prompt, seed) pairs — same fixed RNG every boot => same work list."""
    rng = random.Random(1234)
    return [("%s, %s, highly detailed" % (rng.choice(SUBJECTS), rng.choice(STYLES)),
             rng.randrange(2 ** 31)) for _ in range(N)]


def write_status(done, timings, recent):
    avg = round(sum(timings) / len(timings), 2) if timings else None
    atomic_write(JOB / "status.json", json.dumps({
        "payload": PAYLOAD, "boot_count": boot_count(), "images_done": done,
        "total": N, "avg_s_per_image": avg,
        "imgs_per_hr": round(3600 / avg, 1) if avg else None,
        "recent": recent[-12:], "updated_ts": round(time.time(), 1)}))


def run_noop():
    for d in (OUT, THUMBS):
        d.mkdir(exist_ok=True)
    while True:
        done = len(list(OUT.glob("*.txt")))
        if done >= N:
            break
        atomic_write(OUT / ("%05d.txt" % done), "tick %d boot %d\n" % (done, boot_count()))
        write_status(done + 1, [1.0], [])
        time.sleep(1)
    write_status(N, [1.0], [])


def run_sdxl():
    import torch
    from diffusers import StableDiffusionXLPipeline
    for d in (OUT, THUMBS):
        d.mkdir(exist_ok=True)
    todo = [(i, p, s) for i, (p, s) in enumerate(manifest())
            if not (OUT / ("%05d.png" % i)).exists()]
    done = N - len(todo)
    write_status(done, [], [t.name for t in sorted(THUMBS.glob("*.webp"))])
    if not todo:
        return
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0",
        torch_dtype=torch.float16, variant="fp16", use_safetensors=True).to("cuda")
    timings, recent = [], [t.name for t in sorted(THUMBS.glob("*.webp"))]
    for i, prompt, seed in todo:
        t0 = time.time()
        image = pipe(prompt, num_inference_steps=STEPS,
                     generator=torch.Generator("cuda").manual_seed(seed)).images[0]
        final = OUT / ("%05d.png" % i)
        tmp = final.with_suffix(".png.tmp")
        image.save(tmp, format="PNG")
        os.replace(tmp, final)                       # atomic: kill leaves only .tmp
        thumb = THUMBS / ("%05d.webp" % i)
        tmp_t = thumb.with_suffix(".webp.tmp")
        image.resize((128, 128)).save(tmp_t, format="WEBP")
        os.replace(tmp_t, thumb)
        secs = round(time.time() - t0, 2)
        timings.append(secs)
        recent.append(thumb.name)
        with (JOB / "timings.jsonl").open("a") as f:
            f.write(json.dumps({"i": i, "seconds": secs, "boot": boot_count(),
                                "ts": round(time.time(), 1)}) + "\n")
        done += 1
        write_status(done, timings, recent)


if __name__ == "__main__":
    run_noop() if PAYLOAD == "noop" else run_sdxl()
