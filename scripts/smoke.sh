#!/bin/sh
# Brings the stack up, sends a short burst of traffic that includes failures, and checks that
# every link saw it: Prometheus recorded the SLI, Loki holds the error logs under the same
# service label, Grafana has the dashboard. Tears everything down at the end.
set -eu

COMPOSE=${COMPOSE:-docker compose}
PROM=http://localhost:${PROMETHEUS_PORT:-9090}
LOKI=http://localhost:${LOKI_PORT:-3100}
GRAFANA=http://localhost:${GRAFANA_PORT:-3000}
AM=http://localhost:${ALERTMANAGER_PORT:-9093}

trap '$COMPOSE down -v' EXIT

# retry <attempts> <description> <command...>, one attempt per second
retry() {
  limit=$1; what=$2; shift 2
  i=0
  until "$@" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "$limit" ]; then
      echo "FAIL: $what"
      exit 1
    fi
    sleep 1
  done
  echo "ok: $what"
}

prom_value_above_zero() {
  curl -sf -G "$PROM/api/v1/query" --data-urlencode "query=$1" | python3 -c "
import json, sys
r = json.load(sys.stdin)['data']['result']
sys.exit(0 if r and float(r[0]['value'][1]) > 0 else 1)"
}

loki_has_errors() {
  curl -sf -G "$LOKI/loki/api/v1/query_range" --data-urlencode 'query={service="demo-api", level="error"}' | python3 -c "
import json, sys
sys.exit(0 if json.load(sys.stdin)['data']['result'] else 1)"
}

$COMPOSE up -d --build

retry 90 "prometheus ready" curl -sf "$PROM/-/ready"
retry 90 "alertmanager ready" curl -sf "$AM/-/ready"
retry 90 "loki ready" curl -sf "$LOKI/ready"
retry 90 "grafana has the dashboard" curl -sf -u admin:admin "$GRAFANA/api/dashboards/uid/slo-error-budget"

$COMPOSE run --rm loadgen --rps 20 --duration 30 --error-rate 0.2 --report-every 10

retry 60 "SLI recorded: 5xx ratio is above zero" prom_value_above_zero 'service:sli_errors:ratio_rate5m'
retry 60 "error logs are in Loki under service=demo-api" loki_has_errors
echo "smoke test passed"
