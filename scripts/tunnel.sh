#!/bin/bash
# Start a cloudflared quick tunnel to the controller's public webhook port and
# capture the https URL. Quick-tunnel URLs change on every start, which is fine:
# `make webhook` re-registers the current URL with Vast each session.
set -e
cd "$(dirname "$0")/.."
mkdir -p run
pkill -f "cloudflared tunnel --url http://localhost:8081" 2>/dev/null || true
sleep 1
nohup cloudflared tunnel --url http://localhost:8081 > run/tunnel.log 2>&1 &
for i in $(seq 1 30); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' run/tunnel.log | head -1 || true)
  if [ -n "$url" ]; then break; fi
  sleep 1
done
if [ -z "$url" ]; then echo "tunnel failed — see run/tunnel.log" >&2; exit 1; fi
echo "$url" > run/tunnel_url
echo "tunnel up: $url  (-> localhost:8081)"
