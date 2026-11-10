"""A small HTTP service that exists to be measured.

Two routes under /api. /api/orders takes a connection from a small "database" pool, so
the service can be driven into real saturation by traffic alone. /api/search does not
touch the pool. /metrics and /healthz are not counted as traffic.
"""
import json
import logging
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# 0.3 is the latency SLO threshold. The SLI reads the le="0.3" bucket, so it has to be an
# exact bucket edge; with histograms there is no interpolation to save you.
BUCKETS = (0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.5)

ROUTES = ("/api/orders", "/api/search")
MAX_INJECTED_DELAY_MS = 10_000


@dataclass
class Config:
    pool_size: int = 4
    acquire_timeout_ms: int = 250
    base_query_ms: int = 20
    faults_enabled: bool = False
    log_file: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            pool_size=int(os.environ.get("DB_POOL_SIZE", cls.pool_size)),
            acquire_timeout_ms=int(os.environ.get("DB_ACQUIRE_TIMEOUT_MS", cls.acquire_timeout_ms)),
            base_query_ms=int(os.environ.get("DB_BASE_MS", cls.base_query_ms)),
            faults_enabled=os.environ.get("FAULTS_ENABLED", "0") == "1",
            log_file=os.environ.get("LOG_FILE", ""),
        )


class JsonFormatter(logging.Formatter):
    def format(self, record):
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        line = {
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (ts.microsecond // 1000),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
        }
        line.update(getattr(record, "fields", {}))
        return json.dumps(line, separators=(",", ":"))


def build_logger(name="demo-api", log_file="", stream=None):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handlers = [logging.StreamHandler(stream or sys.stdout)]
    if log_file:
        # The shipper tails this file from a shared volume, so it does not need the Docker socket.
        handlers.append(logging.FileHandler(log_file))
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


class DbPool:
    """A bounded pool. Waiting for a free slot is where the saturation shows up."""

    def __init__(self, size, acquire_timeout_s, in_use_gauge):
        self.size = size
        self._timeout = acquire_timeout_s
        self._slots = threading.BoundedSemaphore(size)
        self._lock = threading.Lock()
        self._in_use = 0
        self._gauge = in_use_gauge

    @property
    def in_use(self):
        return self._in_use

    def acquire(self):
        if not self._slots.acquire(timeout=self._timeout):
            return False
        with self._lock:
            self._in_use += 1
            self._gauge.set(self._in_use)
        return True

    def release(self):
        with self._lock:
            self._in_use -= 1
            self._gauge.set(self._in_use)
        self._slots.release()


class Metrics:
    def __init__(self, registry, pool_size):
        self.requests = Counter(
            "http_requests", "Requests by route and code", ["route", "code"], registry=registry
        )
        self.duration = Histogram(
            "http_request_duration_seconds",
            "Request duration by route and status code",
            ["route", "code"],
            buckets=BUCKETS,
            registry=registry,
        )
        self.pool_in_use = Gauge("db_pool_in_use", "Connections currently held", registry=registry)
        self.pool_timeouts = Counter(
            "db_pool_acquire_timeouts", "Requests that gave up waiting for a connection",
            registry=registry,
        )
        Gauge("db_pool_size", "Configured pool size", registry=registry).set(pool_size)


@dataclass
class Result:
    status: int
    body: dict
    route: str
    fields: dict
    level: int
    msg: str


class App:
    def __init__(self, config, registry=None, logger=None):
        self.config = config
        self.registry = registry or CollectorRegistry()
        self.metrics = Metrics(self.registry, config.pool_size)
        self.pool = DbPool(
            config.pool_size, config.acquire_timeout_ms / 1000, self.metrics.pool_in_use
        )
        self.log = logger or build_logger(log_file=config.log_file)

    def _fault_params(self, query):
        if not self.config.faults_enabled:
            return 0, False
        params = parse_qs(query)
        try:
            delay_ms = int(params.get("delay_ms", ["0"])[0])
        except ValueError:
            delay_ms = 0
        delay_ms = max(0, min(delay_ms, MAX_INJECTED_DELAY_MS))
        return delay_ms, params.get("fail", ["0"])[0] == "1"

    def _query_db(self, extra_delay_ms, fields):
        waited = time.monotonic()
        got_slot = self.pool.acquire()
        fields["db_wait_ms"] = round((time.monotonic() - waited) * 1000)
        fields["pool_in_use"] = self.pool.in_use
        fields["pool_size"] = self.pool.size
        if not got_slot:
            self.metrics.pool_timeouts.inc()
            return False
        started = time.monotonic()
        try:
            time.sleep((self.config.base_query_ms + random.uniform(0, 10) + extra_delay_ms) / 1000)
        finally:
            self.pool.release()
        fields["db_ms"] = round((time.monotonic() - started) * 1000)
        return True

    def handle(self, path, query=""):
        route = path if path in ROUTES else "other"
        fields = {"route": route}
        started = time.monotonic()
        delay_ms, fail = self._fault_params(query)

        if route == "other":
            status, body, level, msg = 404, {"error": "not found"}, logging.INFO, "request"
        elif fail:
            status, body = 500, {"error": "internal error"}
            level, msg = logging.ERROR, "handler failed"
            fields["error"] = "injected failure"
        elif route == "/api/orders":
            if self._query_db(delay_ms, fields):
                status, body, level, msg = 200, {"orders": []}, logging.INFO, "request"
            else:
                status, body = 503, {"error": "busy"}
                level, msg = logging.ERROR, "db pool exhausted"
                fields["error"] = "no free connection before timeout"
        else:
            time.sleep((5 + delay_ms) / 1000)
            status, body, level, msg = 200, {"results": []}, logging.INFO, "request"

        elapsed = time.monotonic() - started
        code = str(status)
        self.metrics.requests.labels(route, code).inc()
        self.metrics.duration.labels(route, code).observe(elapsed)
        fields["status"] = status
        fields["duration_ms"] = round(elapsed * 1000)
        return Result(status, body, route, fields, level, msg)

    def render_metrics(self):
        return generate_latest(self.registry)


class Handler(BaseHTTPRequestHandler):
    server_version = "demo-api"

    def _send(self, status, payload, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        app = self.server.app
        if url.path == "/metrics":
            self._send(200, app.render_metrics(), CONTENT_TYPE_LATEST)
        elif url.path == "/healthz":
            self._send(200, {"ok": True})
        else:
            result = app.handle(url.path, url.query)
            app.log.log(result.level, result.msg, extra={"fields": result.fields})
            self._send(result.status, result.body)

    def log_message(self, *args):
        # Access lines are written by App.handle as JSON, with timings.
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Default is 5. A burst from the load generator would otherwise show up as connection resets
    # that have nothing to do with the pool being measured.
    request_queue_size = 128


def make_server(host, port, config, logger=None):
    httpd = Server((host, port), Handler)
    httpd.app = App(config, logger=logger)
    return httpd


def main():
    config = Config.from_env()
    httpd = make_server("0.0.0.0", int(os.environ.get("PORT", "8000")), config)
    started = {"pool_size": config.pool_size, "faults": config.faults_enabled}
    httpd.app.log.info("started", extra={"fields": started})
    httpd.serve_forever()


if __name__ == "__main__":
    main()
