import io
import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from prometheus_client.parser import text_string_to_metric_families

import server


def make_app(**overrides):
    stream = io.StringIO()
    config = server.Config(base_query_ms=0, **overrides)
    app = server.App(config, logger=server.build_logger("test-" + str(id(stream)), stream=stream))
    return app, stream


def sample(app, name, **labels):
    for family in text_string_to_metric_families(app.render_metrics().decode()):
        for s in family.samples:
            if s.name == name and all(s.labels.get(k) == v for k, v in labels.items()):
                return s.value
    return None


def test_orders_ok_is_counted_by_route_and_code():
    app, _ = make_app()
    result = app.handle("/api/orders")
    assert result.status == 200
    assert sample(app, "http_requests_total", route="/api/orders", code="200") == 1
    assert sample(app, "http_request_duration_seconds_count", route="/api/orders", code="200") == 1


def test_unknown_path_is_404_with_bounded_route_label():
    app, _ = make_app()
    result = app.handle("/some/random/path/123")
    assert result.status == 404
    assert sample(app, "http_requests_total", route="other", code="404") == 1


def test_latency_threshold_is_an_exact_bucket_edge():
    # The recording rules read le="0.3"; if this edge moves, the latency SLI silently breaks.
    assert 0.3 in server.BUCKETS


def test_fault_params_are_ignored_unless_enabled():
    app, _ = make_app(faults_enabled=False)
    assert app.handle("/api/orders", "fail=1&delay_ms=5000").status == 200


def test_injected_failure_is_500_and_logged_as_error():
    app, stream = make_app(faults_enabled=True)
    result = app.handle("/api/search", "fail=1")
    app.log.log(result.level, result.msg, extra={"fields": result.fields})
    assert result.status == 500
    line = json.loads(stream.getvalue().strip())
    assert line["level"] == "error"
    assert line["route"] == "/api/search"
    assert line["status"] == 500


def test_injected_delay_holds_the_connection_and_is_reported():
    app, _ = make_app(faults_enabled=True)
    result = app.handle("/api/orders", "delay_ms=120")
    assert result.status == 200
    assert result.fields["db_ms"] >= 120


def test_bad_delay_value_is_ignored():
    app, _ = make_app(faults_enabled=True)
    assert app.handle("/api/orders", "delay_ms=abc").status == 200


def test_pool_exhaustion_returns_503_and_counts_timeouts():
    app, _ = make_app(pool_size=1, acquire_timeout_ms=60, faults_enabled=True)
    holder = threading.Thread(target=app.handle, args=("/api/orders", "delay_ms=400"))
    holder.start()
    while app.pool.in_use == 0:
        time.sleep(0.005)
    result = app.handle("/api/orders")
    holder.join()
    assert result.status == 503
    assert result.fields["db_wait_ms"] >= 60
    assert result.fields["pool_in_use"] == 1
    assert sample(app, "db_pool_acquire_timeouts_total") == 1
    assert sample(app, "http_requests_total", route="/api/orders", code="503") == 1
    assert app.pool.in_use == 0


def test_search_does_not_use_the_pool():
    app, _ = make_app(pool_size=1, acquire_timeout_ms=10, faults_enabled=True)
    holder = threading.Thread(target=app.handle, args=("/api/orders", "delay_ms=300"))
    holder.start()
    while app.pool.in_use == 0:
        time.sleep(0.005)
    assert app.handle("/api/search").status == 200
    holder.join()


@pytest.fixture
def running():
    stream = io.StringIO()
    logger = server.build_logger("test-http", stream=stream)
    httpd = server.make_server("127.0.0.1", 0, server.Config(base_query_ms=0), logger=logger)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, "http://127.0.0.1:%d" % httpd.server_address[1], stream
    httpd.shutdown()
    httpd.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def test_http_metrics_and_health_are_not_traffic(running):
    httpd, base, stream = running
    assert get(base + "/api/orders")[0] == 200
    assert get(base + "/healthz")[0] == 200
    assert get(base + "/nope")[0] == 404
    status, text = get(base + "/metrics")
    assert status == 200
    assert 'http_requests_total{code="200",route="/api/orders"} 1.0' in text
    assert 'route="/healthz"' not in text
    assert 'route="/metrics"' not in text
    assert 'http_requests_total{code="404",route="other"} 1.0' in text


def test_every_request_leaves_one_json_log_line(running):
    _, base, stream = running
    get(base + "/api/search")
    get(base + "/api/orders")
    lines = [json.loads(x) for x in stream.getvalue().splitlines()]
    assert [x["route"] for x in lines] == ["/api/search", "/api/orders"]
    assert all(x["ts"].endswith("Z") and x["level"] == "info" for x in lines)


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "9")
    monkeypatch.setenv("FAULTS_ENABLED", "1")
    config = server.Config.from_env()
    assert config.pool_size == 9
    assert config.faults_enabled is True
    assert config.acquire_timeout_ms == 250
