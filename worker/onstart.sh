#!/bin/bash
# Vast re-runs /root/onstart.sh on EVERY container start — including the
# auto-resume after an outbid — so everything here must be idempotent.
date -u +%s >> /root/boots.log            # line count == boot count (proof onstart re-ran)
# make -e vars visible in ssh sessions (Vast FAQ recipe, filtered: raw `env`
# can contain exported bash functions that corrupt /etc/environment)
env | grep -E '^[A-Za-z_][A-Za-z0-9_]*=' | grep -v '^BASH_FUNC' >> /etc/environment
mkdir -p /root/job
export HF_HOME=/root/job/hf               # model weights on instance disk -> survive the pause
echo "HF_HOME=/root/job/hf" >> /etc/environment

# Backgrounded so onstart returns immediately (never blocks container startup).
# First boot: waits for launch.py to scp the payload in, installs deps once.
# Resume boots: file + deps already on disk; flock guarantees a single runner.
nohup bash -c '
  until [ -f /root/job/job.py ]; do sleep 2; done
  if [ "$PAYLOAD" != noop ] && [ ! -f /root/job/.deps-ok ]; then
    P=/venv/main/bin/pip
    $P install -q diffusers transformers accelerate safetensors pillow || exit 1
    if [ "$PAYLOAD" = qlora ]; then $P install -q peft bitsandbytes datasets; fi
    touch /root/job/.deps-ok
  fi
  cd /root/job
  exec flock -n /root/job/.lock /venv/main/bin/python /root/job/job.py
' >> /root/job/boot.log 2>&1 &
