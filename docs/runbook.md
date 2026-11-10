# Runbook

Every alert links to its section here. Format: symptom, what to check, what to do. Queries run in
Prometheus or Grafana's Explore; log queries use Loki. The `service` label is the same on the
metrics and on the log streams, so the label from the alert goes straight into the log selector.

Before anything else: is there traffic? `service:http_requests:rate5m`. A burn alert at a few requests
per minute can be one unlucky request, and at zero traffic these alerts say nothing at all.

## SLOAvailabilityFastBurn

**Symptom:** page. The 5xx ratio is over 7.2% for both 1h and 5m. The 30 day budget is going at
14.4x or faster.

**Check**
1. Which route and which code? `sum by (route, code) (rate(http_requests_total{service="demo-api", code=~"5.."}[5m]))`
2. What do the errors say? The alert carries `logs_query`; paste it into Explore (Loki) or use the logs panel
   on the dashboard: `{service="demo-api", level="error"}`. Count by message:
   `sum by (msg) (count_over_time({service="demo-api", level="error"} [5m]))`
   (add `| json` if you want to group by a field inside the line).
3. One route or all of them? If `/api/search` is fine and `/api/orders` is not, look at what only orders uses (the DB pool).
4. Pool: `db_pool_in_use` against `db_pool_size`, `rate(db_pool_acquire_timeouts_total[5m])`. In the logs, `db_wait_ms`
   is time spent waiting for a connection and `db_ms` is time holding one.
5. What changed: a deploy, a config change, a jump in traffic (`service:http_requests:rate5m`)?

**Do**
- Stop the cause first: roll back the change, or stop the source of the extra load.
- Pool exhaustion with a normal `db_wait_ms` on the few requests that get through and a long `db_ms`
  means connections are held too long. Find what makes the query slow. Raising the pool size only
  helps if the database has room, otherwise it moves the queue.
- Watch `service:sli_errors:ratio_rate5m` fall. The page clears when the 5m window is under the line.
- Afterwards write a postmortem ([template](postmortem-template.md)) if budget was spent.

## SLOAvailabilitySlowBurn

**Symptom:** ticket. The 5xx ratio is over 3% for both 6h and 30m. Not urgent, but the budget
will not last the month at this rate.

**Check:** the same queries as above, over longer ranges. `topk(5, sum by (route, code) (increase(http_requests_total{code=~"5.."}[6h])))`.
Is it a steady trickle from one route, a periodic job, or one bad client? Compare with the past
week on the dashboard.

**Do:** assign it to the service owner for the next working day. If it is a known bug, link the ticket to the alert and decide
whether to silence until the fix ships. A silence without an expiry is how budgets get forgotten.

## SLOLatencyFastBurn

**Symptom:** page. More than 72% of successful requests took over 300 ms, for both 1h and 5m.

**Check**
1. p95 per route on the dashboard, or `histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket{service="demo-api", code!~"5.."}[5m])))`.
2. Is the time spent waiting or working? `{service="demo-api"} | json | duration_ms > 300` and compare `db_wait_ms`
   with `db_ms`. Mostly wait: the pool is saturated. Mostly `db_ms`: the query itself got slower.
3. Is it every request or a share? A 72% threshold means something is broadly wrong.

**Do:** same as for availability. A saturated pool often turns into 503s soon after, so expect the
availability alert too.

## SLOLatencySlowBurn

**Symptom:** ticket. More than 30% of successful requests were slower than 300 ms over 6h and 30m.

**Check:** which route (`p95 latency by route` panel), whether it follows traffic (daily peak) or is constant, whether a
dependency changed.

**Do:** ticket for the owner. If it follows the daily peak, the pool or the downstream is sized too tight for it.

## ServiceDown

**Symptom:** page. Prometheus cannot scrape `demo-api` for a minute. No requests are counted
while it is down, so no burn-rate alert will fire.

**Check:** `docker compose ps` and `docker compose logs demo-api`; `curl http://localhost:8000/healthz`; the
Prometheus targets page for the scrape error (refused, timeout, DNS).

**Do:** restart or roll back. If the process is alive but not reachable, check the network path. Remember that
this alert is the only one watching a service that serves nothing.
