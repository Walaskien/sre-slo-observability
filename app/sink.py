"""Webhook receiver for Alertmanager. Prints one JSON line per alert so the delivery is visible."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def summarize(path, payload):
    """One line per alert, keeping only what a reader of the log needs."""
    return [
        json.dumps(
            {
                "receiver_path": path,
                "status": alert.get("status"),
                "alertname": alert.get("labels", {}).get("alertname"),
                "labels": alert.get("labels", {}),
                "annotations": alert.get("annotations", {}),
                "startsAt": alert.get("startsAt"),
            },
            separators=(",", ":"),
        )
        for alert in payload.get("alerts", [])
    ]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            lines = summarize(self.path, json.loads(body))
        except ValueError:
            lines = ['{"error":"not json"}']
        for line in lines:
            print(line, file=sys.stdout, flush=True)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8081"))), Handler).serve_forever()
