#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线兼容性测试：启动 mock Alpme 服务，分别运行
  - alpme-get-key.py（跨平台）
  - alpme-get-key.ps1（Windows PowerShell 5.1 / pwsh 7）

默认只跑 Python；加 --powershell 只跑 PowerShell。

用法：
    python tests/test_flows.py
    python tests/test_flows.py --powershell
    python tests/test_flows.py --verbose
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mock_alpme import MockAlpme, Scenario  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_SCRIPT = os.path.join(ROOT, "alpme-get-key.py")
PS1_SCRIPT = os.path.join(ROOT, "alpme-get-key.ps1")


def run_py(scenario, extra=None, timeout=90):
    srv = MockAlpme(scenario).start()
    try:
        base = [sys.executable]
        if os.environ.get("ALPME_COV") == "1":
            base = [sys.executable, "-m", "coverage", "run", "--parallel-mode"]
        cmd = base + [
            PY_SCRIPT,
            "--base-url", srv.base_url,
            "--no-browser",
            "--key-name", "TEST",
            "--json",
            "--poll-interval", "1",
            "--timeout", "20",
        ]
        if extra:
            cmd += extra
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return proc, srv
    finally:
        srv.stop()


def find_powershell():
    for name in ("pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            return path
    return None


def run_ps(scenario, timeout=120):
    exe = find_powershell()
    if not exe:
        return None, None
    srv = MockAlpme(scenario).start()
    try:
        cmd = [exe, "-NoProfile"]
        if os.name == "nt":
            cmd += ["-ExecutionPolicy", "Bypass"]
        cmd += [
            "-File", PS1_SCRIPT,
            "-BaseUrl", srv.base_url,
            "-NoBrowser",
            "-KeyName", "TEST",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return proc, srv
    finally:
        srv.stop()


def parse_json_result(stdout):
    for line in reversed([ln.strip() for ln in stdout.splitlines() if ln.strip()]):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return None


class PythonFlowTests(unittest.TestCase):
    def test_camel_success(self):
        proc, srv = run_py(Scenario(field_style="camel", poll=["pending", "approved"]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = parse_json_result(proc.stdout)
        self.assertIsNotNone(result, proc.stdout)
        self.assertEqual(result["secret"], "sk_live_mock")
        self.assertEqual(result["key_id"], "152851")
        self.assertTrue(result["base_url"].endswith("/v1"))
        paths = [r["path"] for r in srv.requests]
        self.assertIn("/api/vscode/auth/link/start", paths)
        self.assertEqual(paths.count("/api/vscode/keys"), 2)  # GET list + POST create

    def test_snake_success(self):
        proc, _ = run_py(Scenario(field_style="snake", poll=["approved"]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = parse_json_result(proc.stdout)
        self.assertIsNotNone(result)
        self.assertEqual(result["secret"], "sk_live_mock")

    def test_created_shape_nested_secret(self):
        proc, _ = run_py(Scenario(create_shape="created", secret_in="nested"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = parse_json_result(proc.stdout)
        self.assertIsNotNone(result)
        self.assertEqual(result["secret"], "sk_live_mock")
        self.assertEqual(result["key_id"], "152851")

    def test_expired(self):
        proc, _ = run_py(Scenario(poll=["pending", "expired"]))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("expired", (proc.stdout + proc.stderr).lower())

    def test_denied(self):
        proc, _ = run_py(Scenario(poll=["denied"]))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("denied", (proc.stdout + proc.stderr).lower())

    def test_no_key_slot(self):
        err = (409, {"error": {"code": "no_key_slot", "message": "no free slot"}})
        proc, _ = run_py(Scenario(create_error=err))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no_key_slot", (proc.stdout + proc.stderr).lower())

    def test_retry_503_then_success(self):
        proc, srv = run_py(Scenario(create_fail_first=1))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        create_calls = [r for r in srv.requests
                        if r["method"] == "POST" and r["path"] == "/api/vscode/keys"]
        self.assertEqual(len(create_calls), 2)

    def test_me_401_is_non_fatal(self):
        proc, _ = run_py(Scenario(me_status=401))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNotNone(parse_json_result(proc.stdout))

    def test_wrong_client(self):
        payload = {"error": {"code": "wrong_client", "message": "client only"}}
        proc, _ = run_py(Scenario(start_status=403, start_error=payload))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("wrong_client", (proc.stdout + proc.stderr).lower())

    def test_out_file(self):
        fd, path = tempfile.mkstemp(prefix="alpme_key_", suffix=".env")
        os.close(fd)
        try:
            proc, _ = run_py(Scenario(), extra=["--out", path])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            content = open(path, encoding="utf-8").read()
            self.assertIn("ALPME_API_KEY=sk_live_mock", content)
            self.assertIn("ALPME_BASE_URL=", content)
            if os.name != "nt":
                mode = stat.S_IMODE(os.stat(path).st_mode)
                self.assertEqual(mode & 0o077, 0, "file should be 0600")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


class PowerShellFlowTests(unittest.TestCase):
    def test_ps_camel_success(self):
        proc, _ = run_ps(Scenario(field_style="camel", poll=["pending", "approved"]))
        self.assertIsNotNone(proc, "pwsh/powershell not found")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("sk_live_mock", proc.stdout)

    def test_ps_snake_success(self):
        proc, _ = run_ps(Scenario(field_style="snake", poll=["approved"]))
        self.assertIsNotNone(proc)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("sk_live_mock", proc.stdout)

    def test_ps_expired(self):
        proc, _ = run_ps(Scenario(poll=["expired"]))
        self.assertIsNotNone(proc)
        self.assertEqual(proc.returncode, 1)
        text = (proc.stdout + proc.stderr).lower()
        self.assertTrue("expired" in text or "过期" in text, text)

    def test_ps_wrong_client(self):
        payload = {"error": {"code": "wrong_client", "message": "client only"}}
        proc, _ = run_ps(Scenario(start_status=403, start_error=payload))
        self.assertIsNotNone(proc)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("wrong_client", (proc.stdout + proc.stderr).lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--powershell", action="store_true", help="只跑 PowerShell 测试")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    if args.powershell:
        if not find_powershell():
            print("SKIP: pwsh/powershell not found")
            return 0
        suite.addTests(loader.loadTestsFromTestCase(PowerShellFlowTests))
    else:
        suite.addTests(loader.loadTestsFromTestCase(PythonFlowTests))

    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
