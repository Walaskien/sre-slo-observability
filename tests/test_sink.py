import json
import threading
import urllib.request
from http.server import HTTPServer

import sink


def test_summarize_keeps_labels_and_annotations():
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "X", "service": "demo-api"},
                "annotations": {"summary": "s"},
                "startsAt": "2026-01-01T00:00:00Z",
                "generatorURL": "http://noise",
            }
        ]
    }
    (line,) = sink.summarize("/page", payload)
    out = json.loads(line)
    assert out["alertname"] == "X"
    assert out["labels"]["service"] == "demo-api"
    assert out["receiver_path"] == "/page"
    assert "generatorURL" not in out


def test_post_returns_200_and_prints(capsys):
    httpd = HTTPServer(("127.0.0.1", 0), sink.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    body = json.dumps({"alerts": [{"status": "firing", "labels": {"alertname": "Y"}}]}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/ticket" % httpd.server_address[1], body)
    try:
        assert urllib.request.urlopen(req, timeout=5).status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert '"alertname":"Y"' in capsys.readouterr().out
