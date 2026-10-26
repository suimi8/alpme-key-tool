#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpme 账号 API 的离线 mock 服务。

用途：在 Windows / macOS / Linux 上离线验证 alpme-get-key.py 与 alpme-get-key.ps1
的完整设备码登录 + 创建 Key 流程，不访问真实平台、不使用真实账号。

单独运行：
    python tests/mock_alpme.py --port 18080
然后：
    python alpme-get-key.py --base-url http://127.0.0.1:18080 --no-browser --key-name TEST
"""

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class Scenario:
    """一次 mock 会话的配置。"""

    def __init__(self,
                 field_style="camel",
                 poll=None,
                 me_status=200,
                 keys_status=200,
                 create_shape="key",
                 secret_in="top",
                 create_fail_first=0,
                 create_error=None,
                 start_status=200,
                 start_error=None,
                 verbose=False):
        self.field_style = field_style          # camel | snake
        self.poll = list(poll or ["approved"])  # 轮询状态序列
        self.me_status = me_status
        self.keys_status = keys_status
        self.create_shape = create_shape        # key | created
        self.secret_in = secret_in              # top | nested
        self.create_fail_first = create_fail_first
        self.create_error = create_error        # (status, payload)
        self.start_status = start_status
        self.start_error = start_error          # payload
        self.verbose = verbose


class _QuietServer(ThreadingHTTPServer):
    """忽略客户端提前断开产生的 ConnectionReset/BrokenPipe 噪声。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockAlpme/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _read_json(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _send(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self, method, path, body):
        self.server.app.requests.append({
            "method": method,
            "path": path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        })

    def do_POST(self):
        app = self.server.app
        path = urlparse(self.path).path
        body = self._read_json()
        self._record("POST", path, body)

        if path == "/api/vscode/auth/link/start":
            sc = app.scenario
            if sc.verbose:
                print("[mock] POST /auth/link/start", flush=True)
            if sc.start_status != 200:
                return self._send(sc.start_status, sc.start_error or {
                    "error": {"code": "wrong_client", "message": "This endpoint answers the Alpme editor extension only."}
                })
            camel = sc.field_style == "camel"
            payload = {
                "code": app.code,
                ("userCode" if camel else "user_code"): "MOCK-CODE",
                ("verifyUrlComplete" if camel else "verify_url_complete"): app.base_url + "/link?code=MOCK-CODE",
                ("verifyUrl" if camel else "verify_url"): app.base_url + "/link",
                ("intervalSeconds" if camel else "interval_seconds"): 1,
                ("expiresInSeconds" if camel else "expires_in_seconds"): 30,
            }
            return self._send(200, payload)

        if path == "/api/vscode/keys":
            return self._create_key(app, body)

        return self._send(404, {"error": {"code": "not_found", "message": "not found"}})

    def do_GET(self):
        app = self.server.app
        path = urlparse(self.path).path
        self._record("GET", path, None)

        if path.startswith("/api/vscode/auth/link/"):
            status = app.scenario.poll.pop(0) if app.scenario.poll else "approved"
            if app.scenario.verbose:
                print("[mock] poll -> %s" % status, flush=True)
            if status == "approved":
                return self._send(200, {
                    "status": "approved",
                    "token": "mock-session-token",
                    "user": {"id": 1, "email": "mock@example.com"},
                })
            return self._send(200, {"status": status})

        if path == "/api/vscode/me":
            if app.scenario.me_status != 200:
                return self._send(app.scenario.me_status, {
                    "error": {"code": "no_token" if app.scenario.me_status == 401 else "server_error",
                              "message": "mock /me error"}
                })
            return self._send(200, {
                "user": {"id": 1, "email": "mock@example.com"},
                "plan": {"slug": "mock", "name": "Mock"},
            })

        if path == "/api/vscode/keys":
            if app.scenario.keys_status != 200:
                return self._send(app.scenario.keys_status, {
                    "error": {"code": "no_token", "message": "mock /keys error"}
                })
            return self._send(200, {
                "keys": [{"id": "1", "name": "existing", "status": "active"}],
                "quota": {"active": 1, "max": 10, "canCreate": True},
            })

        return self._send(404, {"error": {"code": "not_found", "message": "not found"}})

    def _create_key(self, app, body):
        sc = app.scenario

        if sc.create_fail_first > 0:
            sc.create_fail_first -= 1
            return self._send(503, {
                "error": {"code": "server_overloaded", "message": "mock overloaded"}
            })

        if sc.create_error:
            return self._send(sc.create_error[0], sc.create_error[1])

        name = (body or {}).get("name", "?")
        key_obj = {"id": "152851", "name": name, "status": "active"}
        if sc.create_shape == "created":
            payload = {"created": key_obj}
        else:
            payload = {"key": key_obj}

        if sc.secret_in == "nested":
            key_obj["secret"] = "sk_live_mock"
        else:
            payload["secret"] = "sk_live_mock"

        return self._send(200, payload)


class MockAlpme:
    def __init__(self, scenario=None):
        self.scenario = scenario or Scenario()
        self.requests = []
        self.code = "mock-code"
        self.httpd = _QuietServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.app = self
        self.port = self.httpd.server_address[1]
        self._thread = None

    @property
    def base_url(self):
        return "http://127.0.0.1:%d" % self.port

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()
            if self._thread:
                self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop()


def main():
    ap = argparse.ArgumentParser(description="Alpme offline mock server")
    ap.add_argument("--port", type=int, default=0, help="0 表示随机端口")
    ap.add_argument("--style", choices=["camel", "snake"], default="camel")
    ap.add_argument("--polls", default="pending,approved", help="轮询状态序列，逗号分隔")
    ap.add_argument("--verbose", action="store_true", help="打印每次请求/轮询状态")
    args = ap.parse_args()

    sc = Scenario(field_style=args.style, poll=[x for x in args.polls.split(",") if x], verbose=args.verbose)
    srv = MockAlpme(sc)
    if args.port:
        srv.httpd.server_close()
        srv.httpd = _QuietServer(("127.0.0.1", args.port), _Handler)
        srv.httpd.daemon_threads = True
        srv.httpd.app = srv
        srv.port = args.port
    srv.start()
    print("mock base URL: %s" % srv.base_url, flush=True)
    print("press Ctrl+C to stop", flush=True)
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
