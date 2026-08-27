"""Qwen QLoRA fine-tune, crash-only (payload 2).

The interruption-safety story here is checkpoint discipline: save every
SAVE_STEPS, resume from the newest VALID checkpoint. HF Trainer's checkpoint
save is not atomic, and an outbid kill has zero grace period — so a kill can
land mid-save. We therefore validate the latest checkpoint (trainer_state.json
must parse) and fall back to the previous one; save_total_limit=3 guarantees a
predecessor exists. Proof of zero lost work = loss.csv continuing across a
boot_count bump.
"""
import json
import os
import time
from pathlib import Path

JOB = Path("/root/job")
OUT = JOB / "ckpt"
STEPS = int(os.environ.get("TRAIN_STEPS", "300"))
SAVE_STEPS = int(os.environ.get("SAVE_STEPS", "10"))
MODEL = os.environ.get("MODEL", "Qwen/Qwen2.5-0.5B-Instruct")


def boot_count():
    log = Path("/root/boots.log")
    return len(log.read_text().splitlines()) if log.exists() else 0


def atomic_write(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def valid_checkpoint():
    """Newest checkpoint whose trainer_state.json parses; else its predecessor."""
    ckpts = sorted(OUT.glob("checkpoint-*"),
                   key=lambda p: int(p.name.split("-")[1]), reverse=True)
    for c in ckpts:
        try:
            json.loads((c / "trainer_state.json").read_text())
            return str(c)
        except (OSError, ValueError):
            print("checkpoint %s failed validation (killed mid-save?) — trying older" % c.name)
    return None


def main():
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer,
                              BitsAndBytesConfig, Trainer, TrainerCallback,
                              TrainingArguments, DataCollatorForLanguageModeling)

    tok = AutoTokenizer.from_pretrained(MODEL)
    tok.pad_token = tok.pad_token or tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        MODEL, device_map="auto",
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16))
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]))

    data = load_dataset("Abirate/english_quotes", split="train")
    data = data.map(lambda b: tok(b["quote"], truncation=True, max_length=512),
                    batched=True, remove_columns=data.column_names)

    class LossLogger(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                with (JOB / "loss.csv").open("a") as f:  # append -> survives restarts
                    f.write("%d,%.4f,%.1f,%d,%.1f\n" % (
                        state.global_step, logs["loss"],
                        logs.get("train_tokens_per_second", 0),
                        boot_count(), time.time()))
                atomic_write(JOB / "status.json", json.dumps({
                    "payload": "qlora", "boot_count": boot_count(),
                    "images_done": state.global_step, "total": STEPS,
                    "avg_s_per_image": None, "recent": [],
                    "loss": logs["loss"], "updated_ts": round(time.time(), 1)}))

    trainer = Trainer(
        model=model, train_dataset=data,
        data_collator=DataCollatorForLanguageModeling(tok, mlm=False),
        callbacks=[LossLogger()],
        args=TrainingArguments(
            output_dir=str(OUT), max_steps=STEPS,
            per_device_train_batch_size=4, gradient_accumulation_steps=2,
            learning_rate=2e-4, logging_steps=1, save_steps=SAVE_STEPS,
            save_total_limit=3, bf16=True, report_to=[],
            include_tokens_per_second=True))
    trainer.train(resume_from_checkpoint=valid_checkpoint())


if __name__ == "__main__":
    main()
