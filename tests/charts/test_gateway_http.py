"""Exercise the rendered Nginx boundary over HTTP against a fake gateway."""

import http.client
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from test_endpoints import render


class AdapterHTTPTests(unittest.TestCase):
    def test_exact_binding_routes_headers_and_path_rejection(self):
        nginx = os.environ.get("DIFY_TEST_NGINX") or shutil.which("nginx") or "/usr/sbin/nginx"
        self.assertTrue(Path(nginx).is_file(), "Nginx is required for HTTP boundary checks")
        received = []

        class Gateway(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, dict(self.headers)))
                body = b'data: {"choices":[]}\n\ndata: [DONE]\n\n'
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        values = {
            "frontendDify": {
                "agentGateway": {"bindingId": "local"},
                "modelSync": {
                    "enabled": True,
                    "projection": {
                        "bindings": [
                            {"id": "company", "api_path": "/custom/company-chat"},
                            {"id": "other", "api_path": "/bindings/other"},
                            {"id": "1company", "api_path": "/custom/one"},
                        ]
                    },
                },
            }
        }
        result = render("plugin-daemon", values)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = next(r for r in yaml.safe_load_all(result.stdout) if r["kind"] == "ConfigMap")[
            "data"
        ]["nginx.conf"]
        process = None
        try:
            with tempfile.TemporaryDirectory() as temp:
                config = (
                    config.replace("127.0.0.1:8080", f"127.0.0.1:{port}")
                    .replace(
                        "infra-agentgateway-gateway.infra-agentgateway.svc.cluster.local:8082",
                        f"127.0.0.1:{upstream.server_port}",
                    )
                    .replace("/tmp/", temp + "/")
                )
                path = Path(temp) / "nginx.conf"
                path.write_text(config)
                process = subprocess.Popen(
                    [nginx, "-p", temp, "-e", "stderr", "-c", str(path), "-g", "daemon off;"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail(process.communicate()[1].decode())
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.02)
                else:
                    self.fail("Nginx did not start")

                def request(path, method="POST", identity="Bearer test-key"):
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                    try:
                        connection.request(
                            method,
                            path,
                            body=b"{}",
                            headers={
                                "Authorization": identity,
                                "X-Neurwerk-Authorization": "spoofed",
                                "Proxy-Authorization": "private-proxy",
                                "Cookie": "private-cookie",
                            },
                        )
                        response = connection.getresponse()
                        return response.status, response.read()
                    finally:
                        connection.close()

                for route, expected in (
                    ("/v1/chat/completions", "/bindings/local/v1/chat/completions"),
                    (
                        "/bindings/company/v1/chat/completions",
                        "/custom/company-chat/v1/chat/completions",
                    ),
                    ("/bindings/other/v1/chat/completions", "/bindings/other/v1/chat/completions"),
                    ("/bindings/1company/v1/chat/completions", "/custom/one/v1/chat/completions"),
                ):
                    status, body = request(route)
                    self.assertEqual(status, 200)
                    self.assertIn(b"[DONE]", body)
                    path_seen, headers = received[-1]
                    self.assertEqual(path_seen, expected)
                    self.assertEqual(headers.get("X-Neurwerk-Authorization"), "Bearer test-key")
                    for header in ("Authorization", "Proxy-Authorization", "Cookie"):
                        self.assertNotIn(header, headers)
                accepted = len(received)
                for path in (
                    "http://unapproved.invalid/bindings/company/v1/chat/completions",
                    "/bindings/company/v1/chat/completions?x=1",
                    "/bindings/company/v1/chat/completions?",
                    "/bindings/company/v1/chat/completions/",
                    "/bindings/company/v1//chat/completions",
                    "/bindings/company/v1/./chat/completions",
                    "/bindings/company/v1/%63hat/completions",
                    "/bindings/unknown/v1/chat/completions",
                    "/bindings/company/v1/models",
                    "/bindings/company/v1/chat/completions;extra",
                ):
                    with self.subTest(path=path):
                        self.assertEqual(request(path)[0], 404)
                route = "/bindings/company/v1/chat/completions"
                self.assertEqual(request(route, method="GET")[0], 405)
                self.assertEqual(request(route, identity="Basic bad")[0], 401)
                self.assertEqual(len(received), accepted)
        finally:
            if process is not None:
                process.terminate()
                process.communicate(timeout=5)
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=5)
