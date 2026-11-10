# SLIs, SLOs and the alert maths

The service is `demo-api` (`app/server.py`). Two routes carry traffic: `/api/orders` (takes a pooled
DB connection) and `/api/search`. `/healthz` and `/metrics` are not traffic and are not counted.

## What is measured

| | Availability | Latency |
|---|---|---|
| Good event | request answered with anything but 5xx | successful request (non-5xx) that took 300 ms or less |
| Valid event | every counted request, 4xx included | every counted non-5xx request |
| Measured at | the HTTP handler, inside the service | same |
| Metric | `http_requests_total{route,code}` | `http_request_duration_seconds_bucket{le="0.3"}` and `_count` |

4xx count as good: a client asking for something that does not exist is not the service failing.
5xx are left out of the latency ratio, otherwise one failed request would spend budget twice.

Measuring inside the service means a dead load balancer, DNS or TLS problem is invisible to
these SLIs. That is a real gap; see the limits in the README.

## Targets

| SLO | Target | Window | Error budget |
|---|---|---|---|
| Availability | 99.5% of requests are not 5xx | 30 days | 0.5% of requests |
| Latency | 95% of successful requests take 300 ms or less (p95 under 300 ms) | 30 days | 5% of successful requests |

The latency target is written as a ratio on one bucket edge instead of `histogram_quantile`, because
a ratio can be summed across windows and instances and a quantile cannot. The price is that 300 ms
has to be an exact bucket boundary; `tests/test_config.py` checks that it is.

For scale: 0.5% of 30 days is 216 minutes (3 h 36 min) of total outage, or the same amount of
failure spread thin.

## Burn rate

Burn rate = observed bad ratio divided by the allowed bad ratio (the budget). 1 means the budget
lasts exactly the 30 days. 14.4 means it lasts 30 / 14.4, a bit over two days.

| Alert | Long window | Short window | Burn rate | Bad ratio that trips it (availability / latency) | Budget spent in the long window | Severity |
|---|---|---|---|---|---|---|
| fast | 1 h | 5 m | 14.4 | 7.2% / 72% | 2% | page |
| slow | 6 h | 30 m | 6 | 3% / 30% | 5% | ticket |

The percentage spent is burn rate times window over 720 h: 14.4 x 1 / 720 = 2%, 6 x 6 / 720 = 5%.

Both windows of a pair must be over the line. The long one makes the alert proportional (it takes
a real amount of damage to trip it). The short one makes it stop: once the problem is gone, the 5m
window drops within minutes, while the 1h window would stay high for most of an hour.

### How long until it fires

With a clean hour before the incident and a constant bad ratio r, the 1h window crosses 7.2% after
`1 h x 0.072 / r`, for availability:

| Error ratio | Fast alert after about |
|---|---|
| 100% | 4.3 min |
| 30% | 14 min (this one is in the unit tests) |
| 10% | 43 min |
| below 7.2% | never; the slow alert catches it once the 6h window passes 3% |

Add the rule's `for` (1 min fast, 5 min slow), the 15 s evaluation interval and Alertmanager's
`group_wait` (10 s). On a stack with little history the long windows are short too, so it fires
sooner; the incident write-up has the real timings.

### What is deliberately not covered

- Steady burn between 1x and 3x (an error ratio between 0.5% and 3%) trips nothing. The workbook
  adds a third row for it: 1x over 3 days and 6 hours, as a ticket. It is left out because a 3 day
  window cannot be shown on a laptop stand.
- The workbook pages on the 6x alert as well. Here it is a ticket (one label in `slo.yml`).
- No traffic means 0/0, which is empty, not a burn. `ServiceDown` (the `up` metric) covers a dead
  process. It does not cover a service that is alive and receiving nothing.
- At a handful of requests per minute a single failure is a double-digit percentage. Alerts like
  this need enough traffic to mean something.

## Where the numbers live

- Recording rules and alerts: `prometheus/rules/slo.yml` (one series per window, e.g.
  `service:sli_errors:ratio_rate1h`).
- The thresholds are written out as `(14.4 * 0.005)` in the alert expressions instead of being
  pulled from one constant, so the derivation is visible where the alert is. If a target changes,
  the dashboard panels (`/ 0.005`, `/ 0.05`) change with it; `tests/test_config.py` guards the alert side.
- Unit tests: `prometheus/tests.yml`.
