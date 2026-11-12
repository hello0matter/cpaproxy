#!/usr/bin/env python3
"""
paykit —— 个人收款码 + 人工核验 的轻量支付台（复刻 8888ok 那套转账流程）

流程：
  1. 用户在 /pay/ 选套餐或填金额，留下 sub2api 邮箱
  2. 拿到收款二维码 + 一个「备注码」(666-XXXXXX) + 应付金额
  3. 用户用支付宝扫码转账，备注里填备注码；回到页面点「我已转账」
  4. 管理员在 /pay/admin 看到待核验列表，核对金额/备注后点「确认到账」
  5. 确认时自动调 sub2api 管理 API：create-and-redeem
       - 套餐单 -> 订阅兑换码（type=subscription, group_id, validity_days）
       - 充值单 -> 余额兑换码（type=balance, value=金额）
     并回落一份兑换码文本，方便人工补发

依赖：仅 Python 3 标准库（sqlite3 / http.server / urllib）。
"""
import base64
import json
import os
import random
import re
import sqlite3
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(BASE_DIR, "paykit.json")
DB_PATH = os.path.join(BASE_DIR, "paykit.db")

DEFAULT_CONF = {
    "listen": "127.0.0.1",
    "port": 18081,
    "base_path": "/pay",
    "base_url": "http://127.0.0.1:18080",
    "admin_email": "admin@hacket.top",
    "admin_password": "",
    "admin_user": "admin",
    "admin_pass": "change-me",
    "qr_image": "qr.png",
    "qr_title": "支付宝收款码",
    "payee_note": "转账时请在备注里填写下方「备注码」，否则无法对账",
    "plans": [
        {"label": "DeepSeek 日卡 (1 天)", "days": 1, "amount": 9.9, "group_id": 5},
        {"label": "DeepSeek 周卡 (7 天)", "days": 7, "amount": 49, "group_id": 5},
        {"label": "DeepSeek 月卡 (30 天)", "days": 30, "amount": 159, "group_id": 5},
    ],
    "currency": "CNY",
}


def load_conf():
    conf = dict(DEFAULT_CONF)
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH, encoding="utf-8") as f:
            conf.update(json.load(f))
    return conf


CONF = load_conf()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               code TEXT UNIQUE NOT NULL,
               email TEXT NOT NULL,
               amount REAL NOT NULL,
               days INTEGER NOT NULL DEFAULT 0,
               group_id INTEGER NOT NULL DEFAULT 0,
               label TEXT NOT NULL DEFAULT '',
               status TEXT NOT NULL DEFAULT 'pending',
               payer TEXT NOT NULL DEFAULT '',
               note TEXT NOT NULL DEFAULT '',
               redeem TEXT NOT NULL DEFAULT '',
               created_at REAL NOT NULL,
               claimed_at REAL,
               paid_at REAL
           )"""
    )
    conn.commit()
    return conn


def new_code(conn):
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "666-" + "".join(random.choice(alphabet) for _ in range(6))
        if not conn.execute("SELECT 1 FROM orders WHERE code=?", (code,)).fetchone():
            return code
    raise RuntimeError("cannot allocate order code")


# ---------------- sub2api 管理 API ----------------

_token = {"v": None, "exp": 0}


def api(path, method="GET", payload=None):
    url = CONF["base_url"].rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if _token["v"]:
        req.add_header("Authorization", "Bearer " + _token["v"])
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def ensure_token():
    if _token["v"] and time.time() < _token["exp"]:
        return
    res = api(
        "/api/v1/auth/login",
        "POST",
        {"email": CONF["admin_email"], "password": CONF["admin_password"]},
    )
    _token["v"] = res["data"]["access_token"]
    _token["exp"] = time.time() + 1800


def find_user(email):
    ensure_token()
    q = urllib.parse.quote(email)
    res = api(f"/api/v1/admin/users?search={q}&page=1&page_size=10")
    items = (res.get("data") or {}).get("items") or res.get("data") or []
    for u in items:
        if (u.get("email") or "").lower() == email.lower():
            return u.get("id")
    return None


def fulfil(order):
    """返回 (是否自动到账, 兑换码文本)"""
    ensure_token()
    uid = find_user(order["email"])
    if not uid:
        return False, ""
    if order["days"] > 0:
        payload = {
            "code": order["code"],
            "type": "subscription",
            "value": 0,
            "user_id": uid,
            "group_id": order["group_id"],
            "validity_days": order["days"],
        }
    else:
        payload = {
            "code": order["code"],
            "type": "balance",
            "value": order["amount"],
            "user_id": uid,
        }
    try:
        api("/api/v1/admin/redeem-codes/create-and-redeem", "POST", payload)
        return True, ""
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return False, f"HTTP {e.code}: {body[:200]}"


# ---------------- HTML ----------------

CSS = """
*{box-sizing:border-box}body{margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif;background:#f6f8fb;color:#1f2937}
.wrap{max-width:680px;margin:0 auto;padding:24px 16px 60px}
.card{background:#fff;border-radius:16px;padding:22px;box-shadow:0 1px 3px rgba(16,24,40,.08),0 8px 24px -12px rgba(16,24,40,.12);margin-bottom:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}
.muted{color:#6b7280;font-size:13px}
label{display:block;font-size:13px;color:#374151;margin:14px 0 6px;font-weight:600}
input,select{width:100%;padding:11px 12px;border:1px solid #d1d5db;border-radius:10px;font-size:15px;background:#fff}
button{width:100%;margin-top:18px;padding:13px;border:0;border-radius:10px;background:#12b886;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button.ghost{background:#eef2f7;color:#111827}button.danger{background:#ef4444}
.qr{display:block;margin:14px auto;width:220px;height:220px;object-fit:contain;border:1px solid #e5e7eb;border-radius:12px;background:#fff}
.code{font:700 26px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:2px;text-align:center;background:#ecfdf5;color:#065f46;border:1px dashed #34d399;border-radius:12px;padding:14px;margin:12px 0}
.amount{font:700 30px/1.2 ui-monospace,monospace;text-align:center;color:#dc2626;margin:6px 0}
.row{display:flex;justify-content:space-between;gap:12px;font-size:14px;padding:8px 0;border-bottom:1px solid #f1f5f9}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
.t-pending{background:#fef3c7;color:#92400e}.t-claimed{background:#dbeafe;color:#1e40af}
.t-paid{background:#dcfce7;color:#166534}.t-rejected{background:#fee2e2;color:#991b1b}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px 6px;border-bottom:1px solid #eef2f7}
a{color:#0ea5e9;text-decoration:none}
"""


def page(title, body):
    base = CONF["base_path"].rstrip("/")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - 666ok</title><style>{CSS}</style></head>
<body><div class="wrap">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
  <div><h1>666ok 扫码充值</h1><div class="muted">个人收款码 · 人工核验 · 到账自动开通</div></div>
  <a href="{base}/" class="muted">首页</a>
</div>
{body}
<div class="muted" style="text-align:center;margin-top:28px">有问题请联系客服，转账时务必填写备注码</div>
</div></body></html>"""


def h(page_name):
    return urllib.parse.quote(page_name)


class Handler(BaseHTTPRequestHandler):
    server_version = "paykit/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ----
    def send_html(self, html, status=200):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_form(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def rel(self, path):
        return CONF["base_path"].rstrip("/") + path

    def admin_ok(self):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except Exception:
            return False
        return user == CONF["admin_user"] and pw == CONF["admin_pass"]

    def require_admin(self):
        if self.admin_ok():
            return True
        body = base64.b64encode(b"admin:please-login").decode()
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Basic realm="paykit", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("需要管理员登录".encode())
        return False

    # ---- routes ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        base = CONF["base_path"].rstrip("/")
        if path in (base, base + "/", "/"):
            return self.home()
        if path == base + "/qr.png" or path == "/qr.png":
            return self.qr()
        if path.startswith(base + "/o/"):
            return self.order_page(path.split("/")[-1])
        if path == base + "/admin":
            return self.admin_list()
        self.send_html(page("404", "<div class='card'>页面不存在</div>"), 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        base = CONF["base_path"].rstrip("/")
        if path == base + "/create":
            return self.create()
        if path.startswith(base + "/claim/"):
            return self.claim(path.split("/")[-1])
        if path == base + "/admin/confirm":
            return self.admin_confirm()
        if path == base + "/admin/reject":
            return self.admin_reject()
        if path == base + "/admin/qr":
            return self.admin_upload_qr()
        self.send_html(page("404", "<div class='card'>页面不存在</div>"), 404)

    # ---- views ----
    def home(self):
        opts = "".join(
            f'<option value="{i}">{p["label"]} — {CONF["currency"]} {p["amount"]}</option>'
            for i, p in enumerate(CONF["plans"])
        )
        body = f"""<div class="card"><h2>选择套餐</h2>
<form method="post" action="{self.rel('/create')}">
  <label>套餐</label>
  <select name="plan"><option value="-1">自定义金额（余额充值）</option>{opts}</select>
  <label>自定义金额（选套餐时忽略）</label>
  <input name="amount" inputmode="decimal" placeholder="例如 10">
  <label>你的登录邮箱（必填，用于自动到账）</label>
  <input name="email" type="email" required placeholder="你在 666ok 注册的邮箱">
  <button type="submit">生成收款码</button>
</form>
<div class="muted" style="margin-top:12px">下单后会给你一个备注码，转账时填在备注里，管理员核对后自动开通。</div>
</div>"""
        self.send_html(page("扫码充值", body))

    def qr(self):
        qr = CONF["qr_image"]
        path = qr if os.path.isabs(qr) else os.path.join(BASE_DIR, qr)
        if qr.startswith("http"):
            self.send_response(302)
            self.send_header("Location", qr)
            self.end_headers()
            return
        if not os.path.exists(path):
            self.send_html(page("缺少收款码", "<div class='card'>管理员还没上传收款码。</div>"), 404)
            return
        data = open(path, "rb").read()
        self.send_response(200)
        ctype = "image/png" if data[1:4] == b"PNG" else "image/jpeg"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def create(self):
        form = self.read_form()
        email = (form.get("email") or "").strip()
        if "@" not in email:
            return self.send_html(page("邮箱无效", "<div class='card'>请填写正确的邮箱</div>"), 400)
        plan_idx = int(form.get("plan") or -1)
        if plan_idx >= 0 and plan_idx < len(CONF["plans"]):
            p = CONF["plans"][plan_idx]
            amount, days, gid, label = float(p["amount"]), int(p.get("days", 0)), int(p.get("group_id", 0)), p["label"]
        else:
            try:
                amount = round(float(form.get("amount") or 0), 2)
            except ValueError:
                amount = 0
            days, gid, label = 0, 0, "余额充值"
            if amount <= 0:
                return self.send_html(page("金额无效", "<div class='card'>请填写大于 0 的金额</div>"), 400)
        conn = db()
        code = new_code(conn)
        conn.execute(
            "INSERT INTO orders(code,email,amount,days,group_id,label,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (code, email, amount, days, gid, label, "pending", time.time()),
        )
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/o/" + code))
        self.end_headers()

    def order_page(self, code):
        conn = db()
        o = conn.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
        if not o:
            return self.send_html(page("订单不存在", "<div class='card'>订单不存在</div>"), 404)
        status_html = {
            "pending": "<span class='tag t-pending'>等待转账</span>",
            "claimed": "<span class='tag t-claimed'>已提交，等待核验</span>",
            "paid": "<span class='tag t-paid'>已到账</span>",
            "rejected": "<span class='tag t-rejected'>已驳回</span>",
        }.get(o["status"], o["status"])
        extra = ""
        if o["status"] == "paid":
            extra = (
                "<div class='card'><h2>已开通</h2>"
                f"<div class='row'><span>权益</span><b>{o['label']}</b></div>"
                f"<div class='row'><span>金额</span><b>{CONF['currency']} {o['amount']:.2f}</b></div>"
                + (f"<div class='row'><span>兑换码</span><b>{o['redeem']}</b></div>" if o["redeem"] else "")
                + "<div class='muted' style='margin-top:12px'>已自动到账，刷新面板即可看到余额/订阅。</div></div>"
            )
        claim_form = ""
        if o["status"] in ("pending", "rejected"):
            claim_form = f"""<form method="post" action="{self.rel('/claim/' + o['code'])}">
  <label>付款人姓名 / 转账单号（选填，便于核对）</label>
  <input name="payer" placeholder="例如 张* / 2026111222001...">
  <label>备注（选填）</label>
  <input name="note" placeholder="转账时间等">
  <button type="submit">我已转账，提交核验</button>
</form>"""
        body = f"""<div class="card">
<h2>扫码支付</h2>
<div class="muted">{CONF['payee_note']}</div>
<img class="qr" src="{self.rel('/qr.png')}?t={int(time.time())}" alt="{CONF['qr_title']}">
<div class="muted" style="text-align:center">{CONF['qr_title']}</div>
<div class="amount">{CONF['currency']} {o['amount']:.2f}</div>
<div class="code">{o['code']}</div>
<div class="row"><span>权益</span><b>{o['label']}</b></div>
<div class="row"><span>到账邮箱</span><b>{o['email']}</b></div>
<div class="row"><span>状态</span>{status_html}</div>
</div>
<div class="card">{claim_form}</div>
{extra}"""
        self.send_html(page("订单 " + o["code"], body))

    def claim(self, code):
        form = self.read_form()
        conn = db()
        conn.execute(
            "UPDATE orders SET status='claimed', claimed_at=?, payer=?, note=? WHERE code=? AND status IN ('pending','rejected')",
            (time.time(), (form.get("payer") or "")[:120], (form.get("note") or "")[:300], code),
        )
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/o/" + code))
        self.end_headers()

    def admin_list(self):
        if not self.require_admin():
            return
        conn = db()
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY CASE status WHEN 'claimed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, id DESC LIMIT 200"
        ).fetchall()
        trs = []
        for o in rows:
            tag = {
                "pending": "<span class='tag t-pending'>待转账</span>",
                "claimed": "<span class='tag t-claimed'>待核验</span>",
                "paid": "<span class='tag t-paid'>已到账</span>",
                "rejected": "<span class='tag t-rejected'>已驳回</span>",
            }.get(o["status"], o["status"])
            actions = ""
            if o["status"] in ("pending", "claimed"):
                actions = (
                    f"<form method='post' action='{self.rel('/admin/confirm')}' style='display:inline'>"
                    f"<input type='hidden' name='code' value='{o['code']}'>"
                    f"<button style='width:auto;margin:0;padding:6px 10px;font-size:13px'>确认到账</button></form> "
                    f"<form method='post' action='{self.rel('/admin/reject')}' style='display:inline'>"
                    f"<input type='hidden' name='code' value='{o['code']}'>"
                    f"<button class='danger' style='width:auto;margin:0;padding:6px 10px;font-size:13px'>驳回</button></form>"
                )
            trs.append(
                f"<tr><td>{o['code']}<div class='muted'>{time.strftime('%m-%d %H:%M', time.localtime(o['created_at']))}</div></td>"
                f"<td>{o['email']}<div class='muted'>{o['label']}</div></td>"
                f"<td><b>{CONF['currency']} {o['amount']:.2f}</b></td>"
                f"<td>{tag}<div class='muted'>{o['payer']}</div></td>"
                f"<td>{actions}</td></tr>"
            )
        has_qr = os.path.exists(os.path.join(BASE_DIR, CONF["qr_image"]))
        body = f"""<div class="card"><h2>支付核验</h2>
<div class="muted">核对收款记录后点「确认到账」，系统会调用 sub2api 自动开通。</div>
<table><tr><th>备注码</th><th>用户</th><th>金额</th><th>状态</th><th>操作</th></tr>
{''.join(trs) or "<tr><td colspan=5 class='muted'>暂无订单</td></tr>"}</table></div>
<div class="card"><h2>收款码</h2>
<div class="muted">{'已配置：' + CONF['qr_image'] if has_qr else '⚠️ 还没有收款码，用下面的方式上传'}</div>
<pre style="background:#f8fafc;padding:12px;border-radius:10px;overflow:auto">curl -u {CONF['admin_user']}:*** -X POST --data-binary @alipay-qr.png {CONF['base_path']}/admin/qr</pre>
<div class="muted">或直接把图片放到服务器的 {os.path.join(BASE_DIR, CONF['qr_image'])}</div></div>"""
        self.send_html(page("支付核验", body))

    def admin_confirm(self):
        if not self.require_admin():
            return
        form = self.read_form()
        code = form.get("code") or ""
        conn = db()
        o = conn.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
        if not o:
            return self.send_html(page("订单不存在", "<div class='card'>订单不存在</div>"), 404)
        ok, redeem = fulfil(o)
        if ok:
            conn.execute(
                "UPDATE orders SET status='paid', paid_at=? WHERE code=?", (time.time(), code)
            )
            conn.commit()
            msg = "已自动到账（订阅/余额已开通）"
        else:
            try:
                ensure_token()
                res = api("/api/v1/admin/redeem-codes/generate", "POST", {
                    "count": 1,
                    "type": "subscription" if o["days"] > 0 else "balance",
                    "value": 0 if o["days"] > 0 else o["amount"],
                    **({"group_id": o["group_id"], "validity_days": o["days"]} if o["days"] > 0 else {}),
                })
                items = (res.get("data") or {}).get("items") or res.get("data") or []
                redeem = items[0]["code"] if items and isinstance(items[0], dict) else ""
            except Exception as e:  # noqa: BLE001
                redeem = ""
            conn.execute(
                "UPDATE orders SET status='paid', paid_at=?, redeem=? WHERE code=?",
                (time.time(), redeem, code),
            )
            conn.commit()
            msg = "已标记到账；未能自动下发，请把兑换码发给用户" if redeem else "已标记到账；自动下发失败，请手动处理"
        body = f"<div class='card'><h2>处理完成</h2><div class='row'><span>订单</span><b>{code}</b></div><div class='row'><span>结果</span><b>{msg}</b></div><div class='card'>{redeem and ('兑换码：<b>' + redeem + '</b>') or ''}</div><p><a href='{self.rel('/admin')}'>返回核验台</a></p></div>"
        self.send_html(page("处理完成", body))

    def admin_reject(self):
        if not self.require_admin():
            return
        form = self.read_form()
        code = form.get("code") or ""
        conn = db()
        conn.execute("UPDATE orders SET status='rejected' WHERE code=?", (code,))
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/admin"))
        self.end_headers()

    def admin_upload_qr(self):
        if not self.require_admin():
            return
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length)
        if len(data) < 100:
            return self.send_json({"ok": False, "error": "empty"}, 400)
        with open(os.path.join(BASE_DIR, CONF["qr_image"]), "wb") as f:
            f.write(data)
        self.send_json({"ok": True, "bytes": len(data)})


def main():
    srv = ThreadingHTTPServer((CONF["listen"], int(CONF["port"])), Handler)
    print(f"paykit on http://{CONF['listen']}:{CONF['port']}{CONF['base_path']}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
