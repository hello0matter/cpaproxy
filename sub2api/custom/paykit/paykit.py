#!/usr/bin/env python3
"""
paykit v2 —— 个人收款码 + 人工核验 的轻量支付台（复刻 8888ok 那套转账流程）

特点
  * 收款码支持多张：后台随时上传 / 启用 / 停用 / 删除，订单自动轮换分配
  * 用户下单 -> 显示二维码 + 备注码(666-XXXXXX) + 应付金额
  * 用户转账后点「我已转账」，管理员在 /pay/admin 核对收款记录后点「确认到账」
  * 确认时自动调 sub2api 管理 API（create-and-redeem）开通套餐/加余额
  * 单文件、仅 Python 标准库（http.server / sqlite3 / urllib）

数据：同目录 paykit.db，图片存 qrcodes/
配置：同目录 paykit.json（见 paykit.example.json）
"""
import base64
import binascii
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
QR_DIR = os.path.join(BASE_DIR, "qrcodes")

DEFAULT_CONF = {
    "listen": "127.0.0.1",
    "port": 18081,
    "base_path": "/pay",
    "base_url": "http://127.0.0.1:18080",
    "admin_email": "admin@hacket.top",
    "admin_password": "",
    "admin_user": "admin",
    "admin_pass": "change-me",
    "qr_title": "支付宝收款码",
    "payee_note": "转账时请在备注里填写下方「备注码」，否则无法对账",
    "notice": "",
    "currency": "CNY",
    "plans": [
        {"label": "DeepSeek 日卡 (1 天)", "days": 1, "amount": 9.9, "group_id": 5},
        {"label": "DeepSeek 周卡 (7 天)", "days": 7, "amount": 49, "group_id": 5},
        {"label": "DeepSeek 月卡 (30 天)", "days": 30, "amount": 159, "group_id": 5},
    ],
}


def load_conf():
    conf = dict(DEFAULT_CONF)
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH, encoding="utf-8") as f:
            conf.update(json.load(f))
    return conf


CONF = load_conf()
os.makedirs(QR_DIR, exist_ok=True)


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
               qr_id INTEGER NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'pending',
               payer TEXT NOT NULL DEFAULT '',
               note TEXT NOT NULL DEFAULT '',
               redeem TEXT NOT NULL DEFAULT '',
               created_at REAL NOT NULL,
               claimed_at REAL,
               paid_at REAL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS qrcodes (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               label TEXT NOT NULL DEFAULT '',
               file TEXT NOT NULL,
               mime TEXT NOT NULL DEFAULT 'image/png',
               enabled INTEGER NOT NULL DEFAULT 1,
               created_at REAL NOT NULL
           )"""
    )
    conn.commit()
    return conn


def new_code(conn):
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(80):
        code = "666-" + "".join(random.choice(alphabet) for _ in range(6))
        if not conn.execute("SELECT 1 FROM orders WHERE code=?", (code,)).fetchone():
            return code
    raise RuntimeError("cannot allocate order code")


def pick_qr(conn):
    """在启用中的收款码里挑一张：优先当前待处理订单最少的那张"""
    rows = conn.execute("SELECT id FROM qrcodes WHERE enabled=1").fetchall()
    if not rows:
        return 0
    best, best_load = 0, None
    for r in rows:
        load = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE qr_id=? AND status IN ('pending','claimed')", (r["id"],)
        ).fetchone()["c"]
        if best_load is None or load < best_load:
            best, best_load = r["id"], load
    return best


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
    if not CONF.get("admin_password"):
        raise RuntimeError("paykit.json 未配置 admin_password")
    res = api("/api/v1/auth/login", "POST",
              {"email": CONF["admin_email"], "password": CONF["admin_password"]})
    _token["v"] = res["data"]["access_token"]
    _token["exp"] = time.time() + 1800


def find_user(email):
    ensure_token()
    res = api("/api/v1/admin/users?search=" + urllib.parse.quote(email) + "&page=1&page_size=10")
    data = res.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else data
    for u in items or []:
        if (u.get("email") or "").lower() == email.lower():
            return u.get("id")
    return None


def fulfil(order):
    """自动到账。返回 (是否成功, 兑换码或错误)"""
    try:
        ensure_token()
        uid = find_user(order["email"])
        if not uid:
            return False, ""
        if order["days"] > 0:
            payload = {"code": order["code"], "type": "subscription", "value": 0,
                       "user_id": uid, "group_id": order["group_id"],
                       "validity_days": order["days"]}
        else:
            payload = {"code": order["code"], "type": "balance",
                       "value": order["amount"], "user_id": uid}
        api("/api/v1/admin/redeem-codes/create-and-redeem", "POST", payload)
        return True, ""
    except urllib.error.HTTPError as e:
        return False, (e.read().decode("utf-8", "replace")[:160])
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:160]


def gen_redeem(order):
    """自动下发失败时的兜底：生成一个兑换码由人工发给用户"""
    try:
        ensure_token()
        if order["days"] > 0:
            payload = {"count": 1, "type": "subscription", "value": 0,
                       "group_id": order["group_id"], "validity_days": order["days"]}
        else:
            payload = {"count": 1, "type": "balance", "value": order["amount"]}
        res = api("/api/v1/admin/redeem-codes/generate", "POST", payload)
        data = res.get("data") or {}
        items = data.get("items") if isinstance(data, dict) else data
        if items and isinstance(items[0], dict):
            return items[0].get("code", "")
        if items and isinstance(items[0], str):
            return items[0]
        return ""
    except Exception:  # noqa: BLE001
        return ""


CSS = """
*{box-sizing:border-box}body{margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif;background:#f6f8fb;color:#1f2937}
.wrap{max-width:760px;margin:0 auto;padding:24px 16px 60px}
.card{background:#fff;border-radius:16px;padding:22px;box-shadow:0 1px 3px rgba(16,24,40,.08),0 8px 24px -12px rgba(16,24,40,.12);margin-bottom:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}
.muted{color:#6b7280;font-size:13px}
label{display:block;font-size:13px;color:#374151;margin:14px 0 6px;font-weight:600}
input,select,textarea{width:100%;padding:11px 12px;border:1px solid #d1d5db;border-radius:10px;font-size:15px;background:#fff}
button{width:100%;margin-top:18px;padding:13px;border:0;border-radius:10px;background:#12b886;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button.sm{width:auto;margin:0;padding:6px 10px;font-size:13px}
button.ghost{background:#eef2f7;color:#111827}button.danger{background:#ef4444}
.qr{display:block;margin:14px auto;width:230px;height:230px;object-fit:contain;border:1px solid #e5e7eb;border-radius:12px;background:#fff}
.code{font:700 26px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:2px;text-align:center;background:#ecfdf5;color:#065f46;border:1px dashed #34d399;border-radius:12px;padding:14px;margin:12px 0}
.amount{font:700 30px/1.2 ui-monospace,monospace;text-align:center;color:#dc2626;margin:6px 0}
.row{display:flex;justify-content:space-between;gap:12px;font-size:14px;padding:8px 0;border-bottom:1px solid #f1f5f9}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
.t-pending{background:#fef3c7;color:#92400e}.t-claimed{background:#dbeafe;color:#1e40af}
.t-paid{background:#dcfce7;color:#166534}.t-rejected{background:#fee2e2;color:#991b1b}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px 6px;border-bottom:1px solid #eef2f7;vertical-align:top}
a{color:#0ea5e9;text-decoration:none}
.thumbs{display:flex;gap:12px;flex-wrap:wrap}
.thumb{border:1px solid #e5e7eb;border-radius:12px;padding:10px;width:170px;background:#fbfdff}
.thumb img{width:100%;height:130px;object-fit:contain;display:block;margin-bottom:8px}
.flash{background:#ecfdf5;border:1px solid #6ee7b7;color:#065f46;padding:10px 12px;border-radius:10px;font-size:14px;margin-bottom:12px}
"""


def page(title, body):
    base = CONF["base_path"].rstrip("/")
    notice = CONF.get("notice") or ""
    notice_html = f"<div class='flash'>{notice}</div>" if notice else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - 666ok</title><style>{CSS}</style></head>
<body><div class="wrap">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
  <div><h1>666ok 扫码充值</h1><div class="muted">个人收款码 · 人工核验 · 到账自动开通</div></div>
  <a href="{base}/" class="muted">首页</a>
</div>
{notice_html}{body}
<div class="muted" style="text-align:center;margin-top:28px">有问题请联系客服，转账时务必填写备注码</div>
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "paykit/2.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- io helpers ----
    def send_bytes(self, data, ctype, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html, status=200):
        self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", status)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def read_form(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        out = {}
        for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items():
            out[k] = v[0]
        return out

    def rel(self, path):
        return CONF["base_path"].rstrip("/") + path

    def admin_ok(self):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        return user == CONF["admin_user"] and pw == CONF["admin_pass"]

    def require_admin(self):
        if self.admin_ok():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="paykit", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("需要管理员登录".encode())
        return False

    # ---- routing ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        base = CONF["base_path"].rstrip("/")
        if path in (base, base + "/", "/"):
            return self.home()
        if path.startswith(base + "/qr/"):
            return self.serve_qr(path.rsplit("/", 1)[-1])
        if path.startswith(base + "/o/"):
            return self.order_page(path.rsplit("/", 1)[-1])
        if path == base + "/admin":
            return self.admin_page()
        self.send_html(page("404", "<div class='card'>页面不存在</div>"), 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        base = CONF["base_path"].rstrip("/")
        if path == base + "/create":
            return self.create()
        if path.startswith(base + "/claim/"):
            return self.claim(path.rsplit("/", 1)[-1])
        if path == base + "/admin/qr-upload":
            return self.admin_qr_upload()
        if path == base + "/admin/qr-toggle":
            return self.admin_qr_toggle()
        if path == base + "/admin/qr-delete":
            return self.admin_qr_delete()
        if path == base + "/admin/confirm":
            return self.admin_confirm()
        if path == base + "/admin/reject":
            return self.admin_reject()
        self.send_html(page("404", "<div class='card'>页面不存在</div>"), 404)

    # ---- user pages ----
    def home(self):
        opts = "".join(
            f'<option value="{i}">{p["label"]} — {CONF["currency"]} {p["amount"]}</option>'
            for i, p in enumerate(CONF["plans"])
        )
        conn = db()
        has_qr = conn.execute("SELECT COUNT(*) c FROM qrcodes WHERE enabled=1").fetchone()["c"] > 0
        warn = "" if has_qr else (
            "<div class='card'><b>⚠️ 还没有可用的收款码</b>"
            "<div class='muted'>管理员请先登录 /pay/admin 上传一张支付宝收款码。</div></div>"
        )
        body = f"""{warn}<div class="card"><h2>选择套餐</h2>
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

    def serve_qr(self, qid):
        try:
            qid = int(qid)
        except ValueError:
            return self.send_html(page("404", "<div class='card'>没有这张收款码</div>"), 404)
        conn = db()
        row = conn.execute("SELECT * FROM qrcodes WHERE id=?", (qid,)).fetchone()
        if not row:
            return self.send_html(page("404", "<div class='card'>没有这张收款码</div>"), 404)
        path = os.path.join(QR_DIR, row["file"])
        if not os.path.exists(path):
            return self.send_html(page("404", "<div class='card'>收款码文件丢失</div>"), 404)
        self.send_bytes(open(path, "rb").read(), row["mime"], extra={"Cache-Control": "no-store"})

    def create(self):
        form = self.read_form()
        email = (form.get("email") or "").strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return self.send_html(page("邮箱无效", "<div class='card'>请填写正确的邮箱</div>"), 400)
        try:
            plan_idx = int(form.get("plan") or -1)
        except ValueError:
            plan_idx = -1
        if 0 <= plan_idx < len(CONF["plans"]):
            p = CONF["plans"][plan_idx]
            amount, days = float(p["amount"]), int(p.get("days", 0))
            gid, label = int(p.get("group_id", 0)), p["label"]
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
        qr_id = pick_qr(conn)
        conn.execute(
            "INSERT INTO orders(code,email,amount,days,group_id,label,qr_id,status,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (code, email, amount, days, gid, label, qr_id, "pending", time.time()),
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
        tag = {
            "pending": "<span class='tag t-pending'>等待转账</span>",
            "claimed": "<span class='tag t-claimed'>已提交，等待核验</span>",
            "paid": "<span class='tag t-paid'>已到账</span>",
            "rejected": "<span class='tag t-rejected'>已驳回</span>",
        }.get(o["status"], o["status"])
        qr_html = "<div class='muted' style='text-align:center'>暂无可用的收款码，请联系客服</div>"
        if o["qr_id"]:
            qr_html = (f"<img class='qr' src='{self.rel('/qr/' + str(o['qr_id']))}' alt='收款码'>"
                       f"<div class='muted' style='text-align:center'>{CONF['qr_title']}</div>")
        claim_form = ""
        if o["status"] in ("pending", "rejected"):
            claim_form = f"""<form method="post" action="{self.rel('/claim/' + o['code'])}">
  <label>付款人姓名 / 转账单号（选填，便于核对）</label>
  <input name="payer" placeholder="例如 张* / 2026111222001...">
  <label>备注（选填）</label>
  <input name="note" placeholder="转账时间等">
  <button type="submit">我已转账，提交核验</button>
</form>"""
        extra = ""
        if o["status"] == "paid":
            extra = ("<div class='card'><h2>已开通</h2>"
                     f"<div class='row'><span>权益</span><b>{o['label']}</b></div>"
                     f"<div class='row'><span>金额</span><b>{CONF['currency']} {o['amount']:.2f}</b></div>"
                     + (f"<div class='row'><span>兑换码</span><b>{o['redeem']}</b></div>" if o["redeem"] else "")
                     + "<div class='muted' style='margin-top:12px'>刷新面板即可看到余额/订阅。</div></div>")
        body = f"""<div class="card">
<h2>扫码支付</h2>
<div class="muted">{CONF['payee_note']}</div>
{qr_html}
<div class="amount">{CONF['currency']} {o['amount']:.2f}</div>
<div class="code">{o['code']}</div>
<div class="row"><span>权益</span><b>{o['label']}</b></div>
<div class="row"><span>到账邮箱</span><b>{o['email']}</b></div>
<div class="row"><span>状态</span>{tag}</div>
</div>
{('<div class="card">' + claim_form + '</div>') if claim_form else ''}
{extra}"""
        self.send_html(page("订单 " + o["code"], body))

    def claim(self, code):
        form = self.read_form()
        conn = db()
        conn.execute(
            "UPDATE orders SET status='claimed', claimed_at=?, payer=?, note=?"
            " WHERE code=? AND status IN ('pending','rejected')",
            (time.time(), (form.get("payer") or "")[:120], (form.get("note") or "")[:300], code),
        )
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/o/" + code))
        self.end_headers()

    # ---- admin ----
    def admin_page(self):
        if not self.require_admin():
            return
        conn = db()
        qrs = conn.execute("SELECT * FROM qrcodes ORDER BY enabled DESC, id DESC").fetchall()
        thumbs = []
        for q in qrs:
            state = "<span class='tag t-paid'>启用</span>" if q["enabled"] else "<span class='tag t-rejected'>停用</span>"
            thumbs.append(f"""<div class="thumb">
  <img src="{self.rel('/qr/' + str(q['id']))}?t={int(time.time())}" alt="">
  <div style="font-size:13px;font-weight:600">{q['label'] or ('#' + str(q['id']))}</div>
  <div class="muted" style="margin-bottom:6px">{state} · {time.strftime('%m-%d %H:%M', time.localtime(q['created_at']))}</div>
  <div style="display:flex;gap:6px">
    <form method="post" action="{self.rel('/admin/qr-toggle')}">
      <input type="hidden" name="id" value="{q['id']}">
      <button class="sm ghost">{'停用' if q['enabled'] else '启用'}</button></form>
    <form method="post" action="{self.rel('/admin/qr-delete')}"
          onsubmit="return confirm('确定删除这张收款码？已发的订单仍会显示它')">
      <input type="hidden" name="id" value="{q['id']}">
      <button class="sm danger">删除</button></form>
  </div>
</div>""")
        orders = conn.execute(
            "SELECT * FROM orders ORDER BY CASE status WHEN 'claimed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, id DESC LIMIT 300"
        ).fetchall()
        trs = []
        for o in orders:
            t = {
                "pending": "<span class='tag t-pending'>待转账</span>",
                "claimed": "<span class='tag t-claimed'>待核验</span>",
                "paid": "<span class='tag t-paid'>已到账</span>",
                "rejected": "<span class='tag t-rejected'>已驳回</span>",
            }.get(o["status"], o["status"])
            acts = ""
            if o["status"] in ("pending", "claimed"):
                acts = (f"<form method='post' action='{self.rel('/admin/confirm')}' style='display:inline'>"
                        f"<input type='hidden' name='code' value='{o['code']}'>"
                        f"<button class='sm'>确认到账</button></form> "
                        f"<form method='post' action='{self.rel('/admin/reject')}' style='display:inline'>"
                        f"<input type='hidden' name='code' value='{o['code']}'>"
                        f"<button class='sm danger'>驳回</button></form>")
            trs.append(
                f"<tr><td><b>{o['code']}</b><div class='muted'>{time.strftime('%m-%d %H:%M', time.localtime(o['created_at']))}"
                f"{(' · 码#' + str(o['qr_id'])) if o['qr_id'] else ''}</div></td>"
                f"<td>{o['email']}<div class='muted'>{o['label']}</div></td>"
                f"<td><b>{CONF['currency']} {o['amount']:.2f}</b></td>"
                f"<td>{t}<div class='muted'>{o['payer']}</div></td><td>{acts}</td></tr>"
            )
        body = f"""<div class="card"><h2>收款码管理（可多张，自动轮换）</h2>
<div class="muted">在启用中的收款码之间，系统会把新订单分给「当前待处理订单最少」的那张。</div>
<div class="thumbs" id="thumbs">{''.join(thumbs) or "<div class='muted'>还没有收款码</div>"}</div>
<label>上传新收款码（支付宝 → 收钱 → 保存收款码）</label>
<input type="file" id="qrfile" accept="image/png,image/jpeg">
<input id="qrlabel" placeholder="备注名，例如「支付宝主号」">
<button onclick="uploadQR()">上传</button>
<div class="muted" id="msg"></div>
<script>
async function uploadQR(){{
  const f=document.getElementById('qrfile').files[0];
  const msg=document.getElementById('msg');
  if(!f){{msg.textContent='先选择图片';return;}}
  if(f.size>3*1024*1024){{msg.textContent='图片太大（>3MB）';return;}}
  msg.textContent='上传中…';
  const b64=await new Promise(r=>{{const fr=new FileReader();fr.onload=()=>r(String(fr.result).split(',')[1]);fr.readAsDataURL(f);}});
  const res=await fetch('{self.rel("/admin/qr-upload")}',{{method:'POST',headers:{{'Content-Type':'application/json'}},
     body:JSON.stringify({{label:document.getElementById('qrlabel').value,b64:b64}})}});
  const j=await res.json();
  msg.textContent=j.ok?('已上传：'+j.label+'（刷新页面查看）'):('失败：'+(j.error||''));
  if(j.ok) setTimeout(()=>location.reload(),600);
}}
</script>
</div>

<div class="card"><h2>支付核验</h2>
<div class="muted">核对支付宝收款记录后点「确认到账」，系统会调 sub2api 自动开通套餐/加余额。</div>
<table><tr><th>备注码</th><th>用户</th><th>金额</th><th>状态</th><th>操作</th></tr>
{''.join(trs) or "<tr><td colspan=5 class='muted'>暂无订单</td></tr>"}</table></div>"""
        self.send_html(page("支付核验", body))

    def admin_qr_upload(self):
        if not self.require_admin():
            return
        data = self.read_json()
        raw = (data.get("b64") or "").strip()
        if "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw, validate=False)
        except (binascii.Error, ValueError):
            return self.send_json({"ok": False, "error": "图片解码失败"}, 400)
        if len(blob) < 200 or len(blob) > 3 * 1024 * 1024:
            return self.send_json({"ok": False, "error": "图片大小异常（需 <3MB）"}, 400)
        if blob[1:4] == b"PNG":
            ext, mime = "png", "image/png"
        elif blob[:2] == b"\xff\xd8":
            ext, mime = "jpg", "image/jpeg"
        else:
            return self.send_json({"ok": False, "error": "只支持 PNG / JPG"}, 400)
        conn = db()
        label = (data.get("label") or "").strip()[:40]
        cur = conn.execute(
            "INSERT INTO qrcodes(label,file,mime,enabled,created_at) VALUES(?,?,?,1,?)",
            (label, "pending", mime, time.time()),
        )
        qid = cur.lastrowid
        fname = f"qr-{qid}.{ext}"
        with open(os.path.join(QR_DIR, fname), "wb") as f:
            f.write(blob)
        conn.execute("UPDATE qrcodes SET file=? WHERE id=?", (fname, qid))
        conn.commit()
        return self.send_json({"ok": True, "id": qid, "label": label or ("#" + str(qid))})

    def admin_qr_toggle(self):
        if not self.require_admin():
            return
        form = self.read_form()
        try:
            qid = int(form.get("id") or 0)
        except ValueError:
            qid = 0
        conn = db()
        conn.execute("UPDATE qrcodes SET enabled = 1 - enabled WHERE id=?", (qid,))
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/admin"))
        self.end_headers()

    def admin_qr_delete(self):
        if not self.require_admin():
            return
        form = self.read_form()
        try:
            qid = int(form.get("id") or 0)
        except ValueError:
            qid = 0
        conn = db()
        row = conn.execute("SELECT file FROM qrcodes WHERE id=?", (qid,)).fetchone()
        conn.execute("DELETE FROM qrcodes WHERE id=?", (qid,))
        conn.commit()
        if row:
            try:
                os.remove(os.path.join(QR_DIR, row["file"]))
            except OSError:
                pass
        self.send_response(302)
        self.send_header("Location", self.rel("/admin"))
        self.end_headers()

    def admin_confirm(self):
        if not self.require_admin():
            return
        form = self.read_form()
        code = form.get("code") or ""
        conn = db()
        o = conn.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
        if not o:
            return self.send_html(page("订单不存在", "<div class='card'>订单不存在</div>"), 404)
        ok, detail = fulfil(o)
        redeem = ""
        if ok:
            msg = "已自动到账：套餐/余额已开通"
        else:
            redeem = gen_redeem(o)
            msg = ("未能自动下发（" + (detail or "用户邮箱未匹配到账号") + "），已生成兑换码，请发给用户"
                   if redeem else "标记到账失败，请手动处理")
        conn.execute("UPDATE orders SET status='paid', paid_at=?, redeem=? WHERE code=?",
                     (time.time(), redeem, code))
        conn.commit()
        body = (f"<div class='card'><h2>处理完成</h2>"
                f"<div class='row'><span>订单</span><b>{code}</b></div>"
                f"<div class='row'><span>结果</span><b>{msg}</b></div>"
                + (f"<div class='code' style='margin-top:14px'>{redeem}</div>" if redeem else "")
                + f"<p><a href='{self.rel('/admin')}'>返回核验台</a></p></div>")
        self.send_html(page("处理完成", body))

    def admin_reject(self):
        if not self.require_admin():
            return
        form = self.read_form()
        conn = db()
        conn.execute("UPDATE orders SET status='rejected' WHERE code=?", (form.get("code") or "",))
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/admin"))
        self.end_headers()


def main():
    srv = ThreadingHTTPServer((CONF["listen"], int(CONF["port"])), Handler)
    print(f"paykit v2 on http://{CONF['listen']}:{CONF['port']}{CONF['base_path']}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
