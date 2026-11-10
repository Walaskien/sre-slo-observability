"""Load generator with a controllable error share and delay.

Errors and delay are requested through query parameters, which the service honours only
when it runs with FAULTS_ENABLED=1. The delay is applied to /api/orders only: it stands
for a slow query that keeps a pooled connection busy for longer.
"""
import argparse
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Orders hit the pool, search does not. A mix makes "which route broke" a real question.
ROUTE_MIX = (("/api/orders", 0.7), ("/api/search", 0.3))


def build_path(rng, error_rate=0.0, delay_ms=0, delay_rate=1.0):
    route = rng.choices([r for r, _ in ROUTE_MIX], weights=[w for _, w in ROUTE_MIX])[0]
    params = []
    if error_rate and rng.random() < error_rate:
        params.append("fail=1")
    if delay_ms and route == "/api/orders" and rng.random() < delay_rate:
        params.append("delay_ms=%d" % delay_ms)
    return route + ("?" + "&".join(params) if params else "")


def http_get(url, timeout=10):
    """Return (status, seconds). Status is None when no HTTP response came back."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as err:
        status = err.code
    except (urllib.error.URLError, OSError):
        status = None
    return status, time.monotonic() - started


class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.total = {"sent": 0, "2xx": 0, "4xx": 0, "5xx": 0, "other": 0, "conn_err": 0}
        self._window = []

    def record(self, status, seconds):
        if status is None:
            key = "conn_err"
        else:
            key = {2: "2xx", 4: "4xx", 5: "5xx"}.get(status // 100, "other")
        with self._lock:
            self.total["sent"] += 1
            self.total[key] += 1
            self._window.append(seconds)

    def snapshot(self):
        with self._lock:
            return dict(self.total)

    def take_window(self):
        with self._lock:
            window, self._window = self._window, []
        return window


def p95_ms(samples):
    if not samples:
        return 0
    ordered = sorted(samples)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000)


def run(args, send=http_get, report=print):
    rng = random.Random(args.seed)
    stats = Stats()
    started = time.monotonic()
    stop = threading.Event()

    def reporter():
        last = stats.snapshot()
        while not stop.wait(args.report_every):
            now = stats.snapshot()
            delta = {k: now[k] - last[k] for k in now}
            last = now
            report(
                "t=%3ds sent=%d 2xx=%d 4xx=%d 5xx=%d conn_err=%d p95=%dms"
                % (
                    time.monotonic() - started,
                    delta["sent"],
                    delta["2xx"],
                    delta["4xx"],
                    delta["5xx"],
                    delta["conn_err"],
                    p95_ms(stats.take_window()),
                )
            )

    thread = threading.Thread(target=reporter, daemon=True)
    thread.start()
    interval = 1.0 / args.rps
    count = int(args.duration * args.rps)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i in range(count):
            path = build_path(rng, args.error_rate, args.delay_ms, args.delay_rate)
            pool.submit(lambda p=path: stats.record(*send(args.url + p)))
            # Pace against the start time, not the previous request, so slow sleeps do not drift.
            wait = started + (i + 1) * interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
    stop.set()
    thread.join(timeout=1)
    final = stats.snapshot()
    report("done: " + " ".join("%s=%d" % kv for kv in final.items()))
    return final


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://demo-api:8000")
    p.add_argument("--rps", type=float, default=20)
    p.add_argument("--duration", type=float, default=60, help="seconds")
    p.add_argument("--error-rate", type=float, default=0.0, help="share sent with fail=1")
    p.add_argument("--delay-ms", type=int, default=0, help="extra time inside the DB call")
    p.add_argument("--delay-rate", type=float, default=1.0, help="share of orders that get it")
    p.add_argument("--workers", type=int, default=64, help="max requests in flight")
    p.add_argument("--report-every", type=float, default=5)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args(argv)
    for name in ("error_rate", "delay_rate"):
        if not 0 <= getattr(args, name) <= 1:
            p.error("--%s must be between 0 and 1" % name.replace("_", "-"))
    if args.rps <= 0:
        p.error("--rps must be positive")
    return args


if __name__ == "__main__":
    run(parse_args())
