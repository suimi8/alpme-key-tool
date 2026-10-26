#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpme API Key 一键获取（设备码登录流程）

跨平台：Windows / macOS / Linux，Python 3.8+，仅使用标准库，无第三方依赖。

流程：
  POST /api/vscode/auth/link/start
  -> 浏览器打开验证页，用户确认
  -> 轮询 GET /api/vscode/auth/link/{code}
  -> GET /api/vscode/me、/keys
  -> POST /api/vscode/keys  创建 Key，secret 只返回一次

安全提示：
  - 仅用于你自己的 Alpme 账号或已获授权的团队账号；不要用于批量注册、转售 Key。
  - 官方 VS Code 扩展（alpme.alpme）已被确认会静默下发未签名木马；本脚本只调用 HTTP API，
    不安装、不运行该扩展。
  - 本脚本不收集账号密码；登录确认由用户在浏览器中完成。
  - secret 只显示一次，请立即保存到安全位置。
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import webbrowser

DEFAULT_BASE = "https://alpme.com"


def eprint(*args):
    print(*args, file=sys.stderr)


def build_opener(args):
    handlers = []
    if args.proxy:
        handlers.append(urllib.request.ProxyHandler({"http": args.proxy, "https": args.proxy}))
    if args.ca_bundle:
        ctx = ssl.create_default_context(cafile=args.ca_bundle)
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def request(opener, method, url, headers, body=None, timeout=30):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except Exception:
                return resp.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = raw.decode("utf-8", "replace")
        return e.code, payload
    except urllib.error.URLError as e:
        return 0, {"error": {"code": "network", "message": str(getattr(e, "reason", e))}}
    except Exception as e:
        return 0, {"error": {"code": "exception", "message": str(e)}}


def err_text(payload):
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            code = err.get("code", "")
            msg = err.get("message", "")
            return ("%s: %s" % (code, msg)).strip(": ") if (code or msg) else json.dumps(payload, ensure_ascii=False)
        if err:
            return str(err)
        return json.dumps(payload, ensure_ascii=False)
    return str(payload)


def pick(d, *keys, **kw):
    default = kw.get("default")
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
    return default


def main():
    ap = argparse.ArgumentParser(
        description="Alpme API Key 一键获取（设备码登录，跨平台，无第三方依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python alpme-get-key.py --key-name CLI\n"
            "  python alpme-get-key.py --no-browser --json\n"
            "  python alpme-get-key.py --test-key sk_live_xxx\n"
            "  python alpme-get-key.py --base-url https://your-host --client-token xxx\n"
        ),
    )
    ap.add_argument("--base-url", default=os.environ.get("ALPME_BASE_URL", DEFAULT_BASE),
                    help="平台地址，默认 %s（也可用环境变量 ALPME_BASE_URL）" % DEFAULT_BASE)
    ap.add_argument("--key-name", default=os.environ.get("ALPME_KEY_NAME", "CLI"),
                    help="新建 Key 的名称，默认 CLI")
    ap.add_argument("--client", default="vscode", help="x-alpme-client 头，默认 vscode")
    ap.add_argument("--client-token", default=os.environ.get("ALPME_CLIENT_TOKEN", ""),
                    help="自建部署的 x-alpme-client-token（可选）")
    ap.add_argument("--session-token", default="", help="跳过登录，直接用已有会话 token（CI 场景）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器，只打印验证地址")
    ap.add_argument("--out", default="", help="把 ALPME_API_KEY/ALPME_BASE_URL 写入指定文件（0600）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果，便于集成")
    ap.add_argument("--test-key", default="", help="只测试已有 Key：/v1/models + 一次最小 chat")
    ap.add_argument("--chat-model", default="deepseek-v4.1-flash", help="测试 chat 使用的模型")
    ap.add_argument("--timeout", type=int, default=600, help="等待确认的最长秒数，默认 600")
    ap.add_argument("--poll-interval", type=int, default=0, help="覆盖轮询间隔（秒）")
    ap.add_argument("--proxy", default="", help="HTTP(S) 代理，例如 http://127.0.0.1:7897")
    ap.add_argument("--ca-bundle", default="", help="自定义 CA 证书文件（可选）")
    ap.add_argument("--retries", type=int, default=3, help="创建 Key 时对 5xx/429 的重试次数")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    opener = build_opener(args)

    common = {"accept": "application/json", "x-alpme-client": args.client}
    if args.client_token:
        common["x-alpme-client-token"] = args.client_token

    # ---------- 仅测试已有 Key ----------
    if args.test_key:
        h = {"accept": "application/json", "authorization": "Bearer " + args.test_key}
        st, payload = request(opener, "GET", base + "/v1/models", h, timeout=30)
        if st != 200:
            eprint("[!] /v1/models 失败: HTTP %s %s" % (st, err_text(payload)))
            return 1
        models = payload.get("data", []) if isinstance(payload, dict) else []
        print("[+] /v1/models OK, models=%d" % len(models))

        chat = {"model": args.chat_model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 8}
        st, payload = request(opener, "POST", base + "/v1/chat/completions", h, chat, timeout=60)
        if st == 200:
            content = ""
            try:
                content = payload["choices"][0]["message"]["content"]
            except Exception:
                pass
            print("[+] chat OK, model=%s, content=%r" % (payload.get("model", "?"), content))
            return 0
        eprint("[!] chat 失败: HTTP %s %s" % (st, err_text(payload)))
        return 1

    # ---------- 登录 / 会话 ----------
    token = args.session_token
    if not token:
        st, payload = request(opener, "POST", base + "/api/vscode/auth/link/start", common, {}, timeout=30)
        if st != 200:
            eprint("[!] 创建登录会话失败: HTTP %s %s" % (st, err_text(payload)))
            return 1

        code = pick(payload, "code")
        user_code = pick(payload, "userCode", "user_code")
        url = pick(payload, "verifyUrlComplete", "verify_url_complete", "verifyUrl", "verify_url")
        interval = int(pick(payload, "intervalSeconds", "interval_seconds", default=3) or 3)
        expires = int(pick(payload, "expiresInSeconds", "expires_in_seconds", default=600) or 600)
        if args.poll_interval:
            interval = args.poll_interval
        if not code or not url:
            eprint("[!] 平台未返回 code 或 verifyUrl")
            return 1

        print("\n  用户码: %s\n  验证页: %s\n" % (user_code, url))
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                eprint("[!] 无法自动打开浏览器，请手动访问上面的地址")

        wait_limit = min(expires, args.timeout)
        print("[*] 等待浏览器确认（最长 %d 秒，每 %d 秒轮询）..." % (wait_limit, interval))
        deadline = time.time() + wait_limit
        while time.time() < deadline:
            time.sleep(interval)
            st, payload = request(opener, "GET", "%s/api/vscode/auth/link/%s" % (base, code), common, timeout=20)
            if st != 200:
                if 400 <= st < 500 and st != 429:
                    eprint("[!] 轮询失败: HTTP %s %s" % (st, err_text(payload)))
                    return 1
                continue
            status = str(pick(payload, "status", default=""))
            if status == "approved":
                token = pick(payload, "token")
                if not token:
                    eprint("[!] 已批准但平台未返回 token")
                    return 1
                break
            if status in ("expired", "denied", "not_found"):
                eprint("[!] 登录结束: %s" % status)
                return 1
        if not token:
            eprint("[!] 等待超时，未获得会话 token")
            return 1
        print("[+] 登录成功")

    auth = {"accept": "application/json", "authorization": "Bearer " + token, "x-alpme-client": args.client}
    if args.client_token:
        auth["x-alpme-client-token"] = args.client_token

    # ---------- 账号信息 ----------
    st, me = request(opener, "GET", base + "/api/vscode/me", auth, timeout=30)
    if st == 200 and isinstance(me, dict):
        user = me.get("user") or {}
        plan = me.get("plan") or {}
        print("[+] 账号: %s，套餐: %s" % (user.get("email", "?"), plan.get("slug", "?")))
    else:
        eprint("[!] 读取 /me 失败: HTTP %s %s" % (st, err_text(me)))

    st, keys = request(opener, "GET", base + "/api/vscode/keys", auth, timeout=30)
    if st == 200 and isinstance(keys, dict):
        quota = keys.get("quota") or {}
        print("[+] 已有 Key: %d，配额: %s/%s" % (
            len(keys.get("keys") or []), quota.get("active", "?"), quota.get("max", "?")))

    # ---------- 创建 Key ----------
    last_payload = None
    last_status = 0
    for attempt in range(1, max(1, args.retries) + 1):
        st, payload = request(opener, "POST", base + "/api/vscode/keys", auth, {"name": args.key_name}, timeout=30)
        last_status, last_payload = st, payload
        if st == 200:
            break
        if st == 429 or 500 <= st < 600:
            time.sleep(min(2 ** attempt, 10))
            continue
        break

    if last_status != 200:
        eprint("[!] 创建 Key 失败: HTTP %s %s" % (last_status, err_text(last_payload)))
        return 1

    key_obj = pick(last_payload, "key", "created", default={}) or {}
    secret = pick(last_payload, "secret") or pick(key_obj, "secret")
    kid = pick(key_obj, "id", default="?")
    if not secret:
        eprint("[!] 平台未返回 secret")
        return 1

    if args.json:
        print(json.dumps({
            "base_url": base + "/v1",
            "key_id": kid,
            "name": args.key_name,
            "secret": secret,
        }, ensure_ascii=False))
    else:
        print("\n================ API KEY ================")
        print("Key ID : %s" % kid)
        print("Name   : %s" % args.key_name)
        print("Secret : %s" % secret)
        print("Base   : %s/v1" % base)
        print("=========================================")
        print("secret 只显示这一次，请立即保存。")

    if args.out:
        lines = ["ALPME_API_KEY=%s" % secret, "ALPME_BASE_URL=%s/v1" % base]
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        try:
            os.chmod(args.out, 0o600)
        except Exception:
            pass
        print("[+] 已写入 %s（权限 0600）" % args.out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
