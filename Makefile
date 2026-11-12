PROM_IMAGE ?= prom/prometheus:v2.55.1
AM_IMAGE   ?= prom/alertmanager:v0.27.0
PROMTOOL    = docker run --rm -v $(CURDIR)/prometheus:/p:ro --entrypoint promtool $(PROM_IMAGE)
AMTOOL      = docker run --rm -v $(CURDIR)/alertmanager:/a:ro --entrypoint amtool $(AM_IMAGE)
COMPOSE    ?= docker compose
SHELLCHECK ?= docker run --rm -v $(CURDIR):/mnt koalaman/shellcheck:v0.10.0
LOADGEN     = $(COMPOSE) run --rm loadgen
PROMETHEUS_PORT   ?= 9090
ALERTMANAGER_PORT ?= 9093
LOKI_PORT         ?= 3100

.PHONY: up down test lint check smoke baseline slow-queries errors alerts logs

up:
	$(COMPOSE) up -d --build
	@echo "Grafana :$${GRAFANA_PORT:-3000}  Prometheus :$(PROMETHEUS_PORT)  Alertmanager :$(ALERTMANAGER_PORT)  Loki :$(LOKI_PORT)"

down:
	$(COMPOSE) down -v

test:
	python3 -m pytest -q

lint:
	ruff check .
	$(SHELLCHECK) -x scripts/smoke.sh

check:
	$(PROMTOOL) check config /p/prometheus.yml
	$(PROMTOOL) check rules /p/rules/slo.yml
	$(PROMTOOL) test rules /p/tests.yml
	$(AMTOOL) check-config /a/alertmanager.yml
	@test "$$($(AMTOOL) config routes test --config.file=/a/alertmanager.yml severity=page service=demo-api)" = page
	@test "$$($(AMTOOL) config routes test --config.file=/a/alertmanager.yml severity=ticket service=demo-api)" = ticket
	@echo "alertmanager routing: page and ticket go where they should"
	$(COMPOSE) config -q

smoke:
	sh scripts/smoke.sh

# Healthy traffic: 20 req/s for 5 minutes.
baseline:
	$(LOADGEN) --rps 20 --duration 300

# Every /api/orders call holds its DB connection 400 ms longer. At 40 req/s the pool of 4
# cannot keep up and requests start timing out while waiting for a connection.
slow-queries:
	$(LOADGEN) --rps 40 --duration 600 --delay-ms 400

# A third of requests fail outright.
errors:
	$(LOADGEN) --rps 20 --duration 600 --error-rate 0.3

alerts:
	@curl -s 'http://localhost:$(ALERTMANAGER_PORT)/api/v2/alerts' | python3 -c "import json,sys; [print(a['status']['state'], a['labels']['alertname'], a['labels'].get('service','')) for a in json.load(sys.stdin)]"

logs:
	@curl -s -G 'http://localhost:$(LOKI_PORT)/loki/api/v1/query_range' --data-urlencode 'query={service="demo-api", level="error"}' --data-urlencode 'limit=10' | python3 -c "import json,sys; [print(v[1]) for s in json.load(sys.stdin)['data']['result'] for v in s['values']]"
