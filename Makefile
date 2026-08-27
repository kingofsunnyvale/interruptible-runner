# The demo keyboard. Every target is safe to re-run.
PY      := /opt/homebrew/bin/python3.13
PAYLOAD ?= sdxl
GPU     ?= 4090
LEVER   ?= bid-drop

tunnel:                ## start cloudflared quick tunnel -> run/tunnel_url
	bash scripts/tunnel.sh

webhook:               ## create/update Vast webhook to the tunnel URL + fire signed test
	$(PY) -m scripts.webhook_setup

webhook-test:          ## just fire the built-in test delivery (pre-demo green light)
	$(PY) -m scripts.webhook_setup --test-only

controller:            ## run the controller (webhook receiver + poller + dashboard)
	$(PY) -m controller.app

launch:                ## rent an interruptible instance and start the payload
	$(PY) -m scripts.launch --payload $(PAYLOAD) --gpu $(GPU)

status:
	@curl -s localhost:8080/api/status | $(PY) -m json.tool

interrupt:             ## pull the plug (through the controller, so it's timestamped)
	curl -s -X POST localhost:8080/api/control/interrupt -d '{"lever":"$(LEVER)"}'

restore:               ## destroy the od-preempt instance -> frees GPU for our bid
	$(PY) -m scripts.interrupt --lever restore

rebid:                 ## manual rebid: make rebid PRICE=0.25
	curl -s -X POST localhost:8080/api/control/rebid -d '{"price":$(PRICE)}'

pull:                  ## keepsake pull: latest status.json + thumbnails
	$(PY) -c "import json,sys;sys.path.insert(0,'.');from controller import puller;\
	st=json.load(open('run/state.json'));print(puller.pull_once(st))"

probe-report:          ## distill events.jsonl -> PROBE.md
	$(PY) -m probe.report

receipt:               ## exact charges + latency table -> RECEIPT.md (run next morning)
	$(PY) -m scripts.receipt

teardown:              ## destroy ONLY instances we created (asks first without YES=1)
	$(PY) -m scripts.teardown $(if $(YES),--yes,)

.PHONY: tunnel webhook webhook-test controller launch status interrupt restore \
        rebid pull probe-report receipt teardown
