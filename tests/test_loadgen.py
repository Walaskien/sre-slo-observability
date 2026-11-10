import random
import threading

import loadgen
import server


def args(**kw):
    base = ["--url", "http://x", "--rps", "200", "--duration", "0.5", "--seed", "1"]
    for k, v in kw.items():
        base += ["--" + k.replace("_", "-"), str(v)]
    return loadgen.parse_args(base)


def test_error_rate_zero_and_one():
    rng = random.Random(3)
    assert not any("fail" in loadgen.build_path(rng, 0.0) for _ in range(200))
    assert all("fail=1" in loadgen.build_path(rng, 1.0) for _ in range(200))


def test_error_share_is_roughly_the_requested_one():
    rng = random.Random(7)
    paths = [loadgen.build_path(rng, 0.3) for _ in range(5000)]
    share = sum("fail=1" in p for p in paths) / len(paths)
    assert 0.27 < share < 0.33


def test_delay_only_goes_to_orders():
    rng = random.Random(5)
    paths = [loadgen.build_path(rng, delay_ms=300) for _ in range(500)]
    assert all(("delay_ms=300" in p) == p.startswith("/api/orders") for p in paths)


def test_delay_rate_limits_the_slow_share():
    rng = random.Random(9)
    paths = [loadgen.build_path(rng, delay_ms=300, delay_rate=0.2) for _ in range(5000)]
    orders = [p for p in paths if p.startswith("/api/orders")]
    share = sum("delay_ms" in p for p in orders) / len(orders)
    assert 0.17 < share < 0.23


def test_run_counts_by_status_class():
    codes = iter([200, 200, 404, 503, None] * 100)
    lock = threading.Lock()

    def fake_send(url):
        with lock:
            return next(codes), 0.001

    total = loadgen.run(args(), send=fake_send, report=lambda _: None)
    assert total["sent"] == 100
    assert total["2xx"] == 40 and total["4xx"] == 20 and total["5xx"] == 20
    assert total["conn_err"] == 20


def test_run_against_real_service_with_all_requests_failing():
    httpd = server.make_server("127.0.0.1", 0, server.Config(faults_enabled=True))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        a = loadgen.parse_args(["--url", url, "--rps", "50", "--duration", "1",
                                "--error-rate", "1", "--seed", "2"])
        total = loadgen.run(a, report=lambda _: None)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert total["sent"] == 50
    assert total["5xx"] == 50


def test_p95():
    assert loadgen.p95_ms([]) == 0
    assert loadgen.p95_ms([0.01] * 99 + [1.0]) == 10
