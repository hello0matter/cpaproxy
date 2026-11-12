#!/usr/bin/env python3
"""
paykit v3 —— 个人收款码 + 人工核验（666ok 扫码充值）
  * 用户端：仿 8888ok 的套餐卡片风格
  * 管理端：账号密码登录（cookie 会话），可上传/启用/停用/删除多张支付宝收款码
  * 确认到账后自动调 sub2api 管理 API 开通订阅/加余额
仅依赖 Python 3 标准库。
"""
import base64, binascii, hashlib, hmac, json, os, random, re, sqlite3, string, sys, time
import urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(BASE_DIR, "paykit.json")
DB_PATH = os.path.join(BASE_DIR, "paykit.db")
QR_DIR = os.path.join(BASE_DIR, "qrcodes")

DEFAULT_CONF = {
    "listen": "127.0.0.1", "port": 18081, "base_path": "/pay",
    "base_url": "http://127.0.0.1:18080",
    "admin_email": "admin@hacket.top", "admin_password": "",
    "admin_user": "admin", "admin_pass": "change-me",
    "site_name": "666ok", "qr_title": "支付宝收款码",
    "payee_note": "转账时请在备注里填写下方「备注码」，否则无法对账",
    "notice": "", "currency": "CNY",
    "amount_presets": [10, 50, 100, 200],
    # true: 用户可以自己填金额；false: 只能选 amount_presets 里的固定档位
    "allow_custom_amount": True,
    "plans": [
        {"label": "DeepSeek 日卡", "badge": "日卡", "days": 1, "amount": 9.9, "group_id": 5,
         "desc": "1 天无限量 · 支持 v4 / v4.1 flash / pro", "features": ["无限流量", "全部分组"]},
        {"label": "DeepSeek 周卡", "badge": "周卡", "days": 7, "amount": 49, "group_id": 5,
         "desc": "7 天无限量 · 适合连续开发", "features": ["无限流量", "优先线路"]},
        {"label": "DeepSeek 月卡", "badge": "月卡", "days": 30, "amount": 159, "group_id": 5,
         "desc": "30 天无限量 · 最划算", "features": ["无限流量", "优先线路", "专属客服"]},
    ],
}


def load_conf():
    c = dict(DEFAULT_CONF)
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH, encoding="utf-8") as f:
            c.update(json.load(f))
    return c


CONF = load_conf()
os.makedirs(QR_DIR, exist_ok=True)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, email TEXT NOT NULL,
        amount REAL NOT NULL, days INTEGER NOT NULL DEFAULT 0, group_id INTEGER NOT NULL DEFAULT 0,
        label TEXT NOT NULL DEFAULT '', qr_id INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending', payer TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '', redeem TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL, claimed_at REAL, paid_at REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS qrcodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL DEFAULT '',
        file TEXT NOT NULL, mime TEXT NOT NULL DEFAULT 'image/png',
        enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS kv (
        key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')""")
    # 老库补列：收款码可绑定固定金额（0 = 通用/不限额）
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(qrcodes)").fetchall()}
    if "amount" not in cols:
        conn.execute("ALTER TABLE qrcodes ADD COLUMN amount REAL NOT NULL DEFAULT 0")
    conn.commit()
    return conn


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(conn, key, value):
    conn.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))
    conn.commit()


def flag(conn, key, conf_default):
    """后台开关优先，其次读 paykit.json"""
    v = kv_get(conn, key)
    if v is None:
        return bool(conf_default)
    return v.strip() in ("1", "true", "True", "yes")


def allow_custom_amount(conn):
    return flag(conn, "allow_custom_amount", CONF.get("allow_custom_amount", True))


def presets(conn):
    v = kv_get(conn, "amount_presets")
    if v:
        try:
            arr = [float(x) for x in json.loads(v)]
            if arr:
                return arr
        except Exception:  # noqa
            pass
    return [float(x) for x in (CONF.get("amount_presets") or [])]


def new_code(conn):
    ab = string.ascii_uppercase + string.digits
    for _ in range(80):
        code = "666-" + "".join(random.choice(ab) for _ in range(6))
        if not conn.execute("SELECT 1 FROM orders WHERE code=?", (code,)).fetchone():
            return code
    raise RuntimeError("code alloc failed")


def pick_qr(conn, amount=0.0):
    """按金额挑收款码：先找绑定金额完全匹配的，再找通用码（0），都不行就退回负载最小的启用码。
    返回 (qr_id, matched)  matched=False 表示没有合适额度，属于兜底。"""
    rows = conn.execute("SELECT id, amount FROM qrcodes WHERE enabled=1").fetchall()
    if not rows:
        return 0, False

    def load(qid):
        return conn.execute("SELECT COUNT(*) c FROM orders WHERE qr_id=? AND status IN ('pending','claimed')",
                            (qid,)).fetchone()["c"]

    try:
        target = round(float(amount), 2)
    except (TypeError, ValueError):
        target = 0.0

    exact = [r for r in rows if abs(float(r["amount"]) - target) < 0.005]
    if exact:
        best = min(exact, key=lambda r: load(r["id"]))
        return best["id"], True
    generic = [r for r in rows if float(r["amount"]) == 0]
    if generic:
        best = min(generic, key=lambda r: load(r["id"]))
        return best["id"], True
    best = min(rows, key=lambda r: load(r["id"]))
    return best["id"], False


# ---------------- sub2api API ----------------
_tok = {"v": None, "exp": 0}


def api(path, method="GET", payload=None):
    req = urllib.request.Request(CONF["base_url"].rstrip("/") + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if _tok["v"]:
        req.add_header("Authorization", "Bearer " + _tok["v"])
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def ensure_token():
    if _tok["v"] and time.time() < _tok["exp"]:
        return
    res = api("/api/v1/auth/login", "POST",
              {"email": CONF["admin_email"], "password": CONF["admin_password"]})
    _tok["v"] = res["data"]["access_token"]
    _tok["exp"] = time.time() + 1800


def find_user(email):
    ensure_token()
    res = api("/api/v1/admin/users?search=" + urllib.parse.quote(email) + "&page=1&page_size=10")
    d = res.get("data") or {}
    items = d.get("items") if isinstance(d, dict) else d
    for u in items or []:
        if (u.get("email") or "").lower() == email.lower():
            return u.get("id")
    return None


def fulfil(o):
    try:
        ensure_token()
        uid = find_user(o["email"])
        if not uid:
            return False, "用户邮箱未匹配到账号"
        if o["days"] > 0:
            p = {"code": o["code"], "type": "subscription", "value": 0, "user_id": uid,
                 "group_id": o["group_id"], "validity_days": o["days"]}
        else:
            p = {"code": o["code"], "type": "balance", "value": o["amount"], "user_id": uid}
        api("/api/v1/admin/redeem-codes/create-and-redeem", "POST", p)
        return True, ""
    except urllib.error.HTTPError as e:
        return False, e.read().decode("utf-8", "replace")[:140]
    except Exception as e:  # noqa
        return False, str(e)[:140]


def gen_redeem(o):
    try:
        ensure_token()
        if o["days"] > 0:
            p = {"count": 1, "type": "subscription", "value": 0,
                 "group_id": o["group_id"], "validity_days": o["days"]}
        else:
            p = {"count": 1, "type": "balance", "value": o["amount"]}
        res = api("/api/v1/admin/redeem-codes/generate", "POST", p)
        d = res.get("data") or {}
        items = d.get("items") if isinstance(d, dict) else d
        if items and isinstance(items[0], dict):
            return items[0].get("code", "")
        if items and isinstance(items[0], str):
            return items[0]
    except Exception:  # noqa
        pass
    return ""


CSS = """
:root{--brand:#0fbf9f;--brand-dark:#0a9c82;--ink:#0f172a;--muted:#6b7280;--line:#e6ebf2}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);background:
  radial-gradient(1200px 600px at 10% -10%,#d9f5ee 0%,rgba(217,245,238,0) 60%),
  radial-gradient(900px 500px at 100% 0%,#e6f0ff 0%,rgba(230,240,255,0) 55%),#f7fafc;
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh}
.wrap{max-width:900px;margin:0 auto;padding:28px 16px 70px}
.brandbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:10px}
.logo{width:38px;height:38px;border-radius:11px;background:linear-gradient(145deg,#0f172a,#0b2a3f);
  color:#4fd1c5;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:19px;box-shadow:0 6px 16px -8px rgba(2,8,23,.7)}
.brand b{font-size:19px;letter-spacing:.3px}
.brand small{display:block;color:var(--muted);font-size:12px;font-weight:400}
.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:22px;
  box-shadow:0 1px 2px rgba(16,24,40,.04),0 18px 40px -28px rgba(16,24,40,.28);margin-bottom:16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 14px}
.muted{color:var(--muted);font-size:13px}
.tabs{display:flex;gap:8px;background:#eef2f7;border-radius:14px;padding:5px;margin-bottom:16px}
.tabs a{flex:1;text-align:center;padding:10px;border-radius:10px;font-weight:600;color:#475569;text-decoration:none;font-size:14px}
.tabs a.on{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(16,24,40,.12)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.plan{border:1px solid var(--line);border-radius:16px;padding:18px;background:linear-gradient(180deg,#fff,#fbfdff);position:relative;transition:.15s}
.plan:hover{border-color:#bde9df;box-shadow:0 14px 34px -24px rgba(15,191,159,.55);transform:translateY(-1px)}
.plan.hero{background:linear-gradient(180deg,#0b3b34,#0a2c27);color:#e7fffa;border-color:transparent}
.plan.hero .muted,.plan.hero .desc{color:#9fd8cd}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;background:#e8fbf5;color:#0a7d68;font-size:12px;font-weight:700}
.plan.hero .badge{background:rgba(79,209,197,.18);color:#8ff0e2}
.price{font:800 30px/1.1 ui-monospace,Menlo,monospace;margin:12px 0 2px}
.price small{font-size:13px;font-weight:500;opacity:.65;margin-left:4px}
.desc{font-size:13px;color:var(--muted);min-height:38px}
ul.feat{list-style:none;padding:0;margin:12px 0 0;font-size:13px}
ul.feat li{margin:5px 0;padding-left:16px;position:relative;opacity:.9}
ul.feat li:before{content:"✓";position:absolute;left:0;color:var(--brand);font-weight:700}
.plan form{margin-top:14px}
button{width:100%;padding:12px;border:0;border-radius:12px;background:var(--brand);color:#fff;font-size:15px;font-weight:700;cursor:pointer}
button:hover{background:var(--brand-dark)}
button.hero-btn{background:#12b886}
button.ghost{background:#eef2f7;color:#111827}
button.danger{background:#ef4444}
button.sm{width:auto;padding:7px 12px;font-size:13px;border-radius:9px}
label{display:block;font-size:13px;font-weight:600;margin:14px 0 6px;color:#334155}
input,select{width:100%;padding:12px 13px;border:1px solid #d7dee8;border-radius:12px;font-size:15px;background:#fff;outline:none}
input:focus,select:focus{border-color:#7fdccb;box-shadow:0 0 0 3px rgba(15,191,159,.14)}
.presets{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 2px}
.presets button{width:auto;padding:9px 16px;background:#f1f5f9;color:#334155;font-weight:600;border:1px solid #e2e8f0}
.presets button.on{background:#e8fbf5;border-color:#7fdccb;color:#0a7d68}
.qr{display:block;width:240px;height:240px;object-fit:contain;margin:16px auto 6px;border:1px solid var(--line);border-radius:16px;background:#fff;padding:8px}
.code{font:800 26px/1.2 ui-monospace,Menlo,monospace;letter-spacing:3px;text-align:center;
  background:#effcf7;color:#075e4f;border:1px dashed #6ee7c8;border-radius:14px;padding:14px;margin:14px 0}
.amount{font:800 32px/1.2 ui-monospace,Menlo,monospace;text-align:center;color:#e11d48;margin:4px 0}
.row{display:flex;justify-content:space-between;gap:12px;font-size:14px;padding:9px 0;border-bottom:1px solid #f1f5f9}
.row:last-child{border-bottom:0}
.tag{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:700}
.t-pending{background:#fef3c7;color:#92400e}.t-claimed{background:#dbeafe;color:#1e40af}
.t-paid{background:#dcfce7;color:#166534}.t-rejected{background:#fee2e2;color:#991b1b}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:11px 8px;border-bottom:1px solid #eef2f7;vertical-align:top}
th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
a{color:var(--brand-dark);text-decoration:none}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.thumb{border:1px solid var(--line);border-radius:14px;padding:10px;background:#fbfdff}
.thumb img{width:100%;height:120px;object-fit:contain;display:block;margin-bottom:8px}
.thumb .nm{font-size:13px;font-weight:700;margin-bottom:2px}
.thumb .ops{display:flex;gap:6px;margin-top:8px}
.flash{background:#effcf7;border:1px solid #7fdccb;color:#075e4f;padding:11px 13px;border-radius:12px;font-size:14px;margin-bottom:14px}
.bad{background:#fff1f2;border-color:#fecdd3;color:#9f1239}
.foot{text-align:center;color:var(--muted);font-size:12px;margin-top:26px}
.login{max-width:400px;margin:8vh auto 0}
"""


def page(title, body, admin=False):
    base = CONF["base_path"].rstrip("/")
    notice = CONF.get("notice") or ""
    nhtml = f"<div class='flash'>{notice}</div>" if notice else ""
    nav = (f"<a class='muted' href='{base}/admin/logout'>退出</a>" if admin else "")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {CONF['site_name']}</title><style>{CSS}</style></head><body>
<div class="wrap">
 <div class="brandbar">
  <div class="brand"><div class="logo">6</div>
   <div><b>{CONF['site_name']}</b><small>扫码充值 · 自动开通</small></div>
  </div>
  <div>{nav}<a class="muted" href="{base}/">下单</a></div>
 </div>
 {nhtml}{body}
 <div class="foot">© {time.strftime('%Y')} {CONF['site_name']} · 收款由本人支付宝账户直接到账</div>
</div></body></html>"""


class H(BaseHTTPRequestHandler):
    server_version = "paykit/3.0"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))

    # ---- io ----
    def snd(self, data, ctype, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def html(self, h, status=200):
        self.snd(h.encode(), "text/html; charset=utf-8", status)

    def js(self, o, status=200):
        self.snd(json.dumps(o, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def jbody(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
        except Exception:  # noqa
            return {}

    def rel(self, p):
        return CONF["base_path"].rstrip("/") + p

    # ---- session ----
    def _sign(self, payload):
        key = (CONF["admin_pass"] + CONF["admin_user"]).encode()
        return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:32]

    def set_session(self):
        exp = int(time.time()) + 12 * 3600
        payload = f"{CONF['admin_user']}|{exp}"
        token = base64.urlsafe_b64encode(f"{payload}|{self._sign(payload)}".encode()).decode()
        self.send_header  # noqa
        return f"pk_session={token}; Path={CONF['base_path'].rstrip('/')}/; HttpOnly; SameSite=Lax; Max-Age=43200" + \
               ("; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else "")

    def logged_in(self):
        if self.headers.get("Authorization", "").startswith("Basic "):
            try:
                u, _, p = base64.b64decode(self.headers["Authorization"][6:]).decode().partition(":")
                if u == CONF["admin_user"] and p == CONF["admin_pass"]:
                    return True
            except Exception:  # noqa
                pass
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k != "pk_session" or not v:
                continue
            try:
                dec = base64.urlsafe_b64decode(v.encode()).decode()
                user, exp, sig = dec.rsplit("|", 2)
                if user != CONF["admin_user"] or int(exp) < time.time():
                    return False
                return hmac.compare_digest(sig, self._sign(f"{user}|{exp}"))
            except Exception:  # noqa
                return False
        return False

    # ---- routing ----
    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        b = CONF["base_path"].rstrip("/")
        if p in (b, b + "/", "/"):
            return self.home()
        if p.startswith(b + "/qr/"):
            return self.qr_img(p.rsplit("/", 1)[-1])
        if p.startswith(b + "/o/"):
            return self.order(p.rsplit("/", 1)[-1])
        if p == b + "/admin":
            return self.admin_home()
        if p == b + "/admin/logout":
            self.send_response(302)
            self.send_header("Set-Cookie", f"pk_session=; Path={b}/; Max-Age=0")
            self.send_header("Location", self.rel("/admin"))
            self.end_headers()
            return
        self.html(page("404", "<div class='card'>页面不存在</div>"), 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        b = CONF["base_path"].rstrip("/")
        if p == b + "/create":
            return self.create()
        if p.startswith(b + "/claim/"):
            return self.claim(p.rsplit("/", 1)[-1])
        if p == b + "/admin/login":
            return self.login()
        if p == b + "/admin/settings":
            return self.admin_settings()
        if p == b + "/admin/qr-upload":
            return self.qr_upload()
        if p == b + "/admin/qr-toggle":
            return self.qr_toggle()
        if p == b + "/admin/qr-bind":
            return self.qr_bind()
        if p == b + "/admin/qr-delete":
            return self.qr_delete()
        if p == b + "/admin/confirm":
            return self.confirm()
        if p == b + "/admin/reject":
            return self.reject()
        self.html(page("404", "<div class='card'>页面不存在</div>"), 404)

    # ---- user ----
    def home(self):
        conn = db()
        nqr = conn.execute("SELECT COUNT(*) c FROM qrcodes WHERE enabled=1").fetchone()["c"]
        warn = "" if nqr else ("<div class='card'><b>⚠️ 还没有可用的收款码</b>"
                               "<div class='muted'>管理员请登录 <a href='" + self.rel('/admin') + "'>/pay/admin</a> 上传支付宝收款码。</div></div>")

        custom_ok = allow_custom_amount(conn)
        tiers = presets(conn)
        show_topup = custom_ok or bool(tiers)

        # ---- 套餐卡片 ----
        plans = ""
        for i, p in enumerate(CONF["plans"]):
            feats = "".join(f"<li>{x}</li>" for x in (p.get("features") or []))
            hero = " hero" if i == len(CONF["plans"]) - 1 else ""
            btn = "hero-btn" if hero else ""
            plans += f"""<div class="plan{hero}">
  <span class="badge">{p.get('badge') or p['label']}</span>
  <div class="price">{CONF['currency']} {p['amount']}<small>/ {p.get('days',0)} 天</small></div>
  <div class="desc">{p.get('desc','')}</div>
  <ul class="feat">{feats}</ul>
  <form method="post" action="{self.rel('/create')}">
    <input type="hidden" name="plan" value="{i}">
    <input type="hidden" name="email" class="js-email" value="">
    <button class="{btn}">立即购买</button>
  </form>
</div>"""

        # ---- 余额充值区 ----
        if not show_topup:
            topup = ""
        elif custom_ok:
            topup = f"""<div class="card" id="topup"><h2>余额充值</h2>
<div class="muted">按实际用量扣费，金额自己填；下单后使用「自定义金额收款码」（不限额）。</div>
<form method="post" action="{self.rel('/create')}">
  <input type="hidden" name="plan" value="-1">
  <label>充值金额（{CONF['currency']}）</label>
  <input id="amt" name="amount" inputmode="decimal" placeholder="输入金额，例如 50" required>
  <input type="hidden" name="email" class="js-email" value="">
  <button>生成收款码</button>
</form></div>"""
        else:
            fixed_btns = "".join(
                f"""<form method="post" action="{self.rel('/create')}" style="margin:0">
  <input type="hidden" name="plan" value="-1">
  <input type="hidden" name="amount" value="{a}">
  <input type="hidden" name="email" class="js-email" value="">
  <button>{CONF['currency']} {int(a) if a == int(a) else a}</button>
</form>""" for a in tiers)
            topup = f"""<div class="card" id="topup"><h2>余额充值</h2>
<div class="muted">请选择充值档位（固定金额）。</div>
<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-top:14px">{fixed_btns}</div></div>"""

        tab_topup = ('  <a href="#topup" onclick="document.getElementById(\'topup\').scrollIntoView({behavior:\'smooth\'});return false;">余额充值</a>'
                     if show_topup else "")

        body = f"""{warn}
<div class="card">
  <label>你的登录邮箱（必填）</label>
  <input id="emailMain" type="email" placeholder="你在 {CONF['site_name']} 注册的邮箱" oninput="syncEmail(this.value)">
  <div class="muted">用于自动到账，请填注册时用的邮箱</div>
</div>
<div class="tabs">
  <a class="on" href="{self.rel('/')}">订阅套餐</a>
{tab_topup}
</div>
<div class="card"><h2>订阅套餐</h2>
<div class="muted" style="margin-bottom:14px">付款后自动开通（管理员核对到账，通常几分钟内）。</div>
<div class="grid">{plans}</div></div>
{topup}
<script>
function syncEmail(v){{document.querySelectorAll('.js-email').forEach(el=>el.value=v);}}
function setAmt(v){{var i=document.getElementById('amt');if(i)i.value=v;
 document.querySelectorAll('.presets button').forEach(b=>b.classList.remove('on'));
 if(window.event&&event.target)event.target.classList.add('on');}}
document.querySelectorAll('form').forEach(f=>f.addEventListener('submit',function(e){{
  var m=document.getElementById('emailMain');
  if(m&&!m.value.trim()){{e.preventDefault();m.focus();alert('请先填写你的登录邮箱');}}
}}));
</script>"""
        self.html(page("扫码充值", body))

    def qr_img(self, qid):
        try:
            qid = int(qid)
        except ValueError:
            return self.html(page("404", "<div class='card'>没有这张收款码</div>"), 404)
        conn = db()
        row = conn.execute("SELECT * FROM qrcodes WHERE id=?", (qid,)).fetchone()
        path = os.path.join(QR_DIR, row["file"]) if row else None
        if not row or not os.path.exists(path):
            return self.html(page("404", "<div class='card'>收款码不存在</div>"), 404)
        self.snd(open(path, "rb").read(), row["mime"], extra={"Cache-Control": "no-store"})

    def create(self):
        f = self.form()
        email = (f.get("email") or "").strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return self.html(page("邮箱有误", "<div class='card'>请填写完整邮箱，例如 you@example.com</div>"), 400)
        try:
            idx = int(f.get("plan") or -1)
        except ValueError:
            idx = -1
        if 0 <= idx < len(CONF["plans"]):
            p = CONF["plans"][idx]
            amount, days, gid, label = float(p["amount"]), int(p.get("days", 0)), int(p.get("group_id", 0)), p["label"]
        else:
            conn0 = db()
            if not allow_custom_amount(conn0):
                # 后台关闭了自定义金额：只接受预设档位（服务端强校验，防绕过）
                allowed = [round(float(x), 2) for x in presets(conn0)]
                try:
                    amount = round(float(f.get("amount") or 0), 2)
                except ValueError:
                    amount = 0
                if amount not in allowed:
                    opts = "、".join(f"{CONF['currency']} {(int(a) if a == int(a) else a)}" for a in allowed) or "无"
                    return self.html(page("金额不支持", f"<div class='card'><b>只支持固定档位</b>"
                                                      f"<div class='muted'>可选：{opts}</div>"
                                                      f"<p><a href='{self.rel('/')}'>← 返回</a></p></div>"), 400)
            else:
                try:
                    amount = round(float(f.get("amount") or 0), 2)
                except ValueError:
                    amount = 0
                if amount <= 0:
                    return self.html(page("金额有误", "<div class='card'>请输入大于 0 的金额</div>"), 400)
            days, gid, label = 0, 0, "余额充值"
        conn = db()
        code = new_code(conn)
        qr_id, _matched = pick_qr(conn, amount)
        conn.execute("INSERT INTO orders(code,email,amount,days,group_id,label,qr_id,status,created_at)"
                     " VALUES(?,?,?,?,?,?,?,'pending',?)",
                     (code, email, amount, days, gid, label, qr_id, time.time()))
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/o/" + code))
        self.end_headers()

    def order(self, code):
        conn = db()
        o = conn.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
        if not o:
            return self.html(page("订单不存在", "<div class='card'>订单不存在</div>"), 404)
        tag = {"pending": "t-pending", "claimed": "t-claimed", "paid": "t-paid", "rejected": "t-rejected"}.get(o["status"], "")
        txt = {"pending": "等待转账", "claimed": "已提交，等待核验", "paid": "已到账", "rejected": "已驳回"}.get(o["status"], o["status"])
        # ---- 选码：金额精确匹配 > 通用码（不限额） > 兜底 ----
        rows = conn.execute("SELECT id,label,amount FROM qrcodes WHERE enabled=1").fetchall()
        amt_o = round(float(o["amount"]), 2)
        exact = [r for r in rows if abs(float(r["amount"]) - amt_o) < 0.005]
        generic = [r for r in rows if float(r["amount"]) == 0]
        cand = exact or generic or list(rows)
        qr_amt = 0.0
        if not cand:
            qr = "<div class='muted'>暂无可用的收款码，请联系客服</div>"
            picker = ""
        else:
            first = cand[0]
            qr_amt = float(first["amount"])
            amt_shown = int(qr_amt) if qr_amt == int(qr_amt) else qr_amt
            if qr_amt > 0:
                tip = (f"<div class='muted' style='text-align:center'>这是 {CONF['currency']} {amt_shown} 的<b>定额收款码</b>，"
                       f"扫码后直接付 {CONF['currency']} {amt_shown} 即可</div>")
            else:
                tip = (f"<div class='muted' style='text-align:center'>这是<b>自定义金额收款码</b>，"
                       f"扫码后请输入 {CONF['currency']} {amt_o:.2f}</div>")
            qr = (f"<img class='qr' id='qrimg' src='{self.rel('/qr/' + str(first['id']))}' alt='收款码'>"
                  f"<div class='muted' style='text-align:center'>{CONF['qr_title']}</div>{tip}")
            picker = ""
            if len(cand) > 1:
                btns = []
                for idx, r in enumerate(cand):
                    lbl = r["label"] or f"码#{r['id']}"
                    a = float(r["amount"])
                    tail = "（自定义金额）" if a == 0 else f"（定额 {int(a) if a == int(a) else a}）"
                    btns.append(f"""<button type="button" class="sm {'hero-btn' if idx == 0 else 'ghost'} selqr"
  data-src="{self.rel('/qr/' + str(r['id']))}">{lbl}{tail}</button>""")
                picker = ("<div class='muted' style='margin-top:12px;text-align:center'>可选收款码（点一下切换）</div>"
                          f"<div style='display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:8px'>{''.join(btns)}</div>"
                          "<script>document.querySelectorAll('.selqr').forEach(function(b){"
                          "b.addEventListener('click',function(){"
                          "document.getElementById('qrimg').src=this.dataset.src;"
                          "document.querySelectorAll('.selqr').forEach(function(o){o.classList.remove('hero-btn');o.classList.add('ghost');});"
                          "this.classList.remove('ghost');this.classList.add('hero-btn');});});</script>")
        mismatch = ""
        if qr_amt > 0 and abs(qr_amt - float(o["amount"])) > 0.005:
            mismatch = (f"<div class='flash bad' style='margin-bottom:14px'>"
                        f"当前只有 {CONF['currency']} {int(qr_amt) if qr_amt == int(qr_amt) else qr_amt} 的定额收款码，"
                        f"与订单金额 {CONF['currency']} {o['amount']:.2f} 不一致。<b>请联系客服</b>确认后再付，"
                        f"否则需要人工对账。</div>")
        claim = ""
        if o["status"] in ("pending", "rejected"):
            claim = f"""<div class="card"><h2>付完了？</h2>
<form method="post" action="{self.rel('/claim/' + o['code'])}">
  <label>付款人姓名 / 转账单号（选填）</label>
  <input name="payer" placeholder="便于核对，可不填">
  <label>备注（选填）</label>
  <input name="note" placeholder="转账时间等">
  <button>我已转账，提交核验</button>
</form></div>"""
        done = ""
        if o["status"] == "paid":
            done = f"""<div class="card"><h2>已开通 ✅</h2>
<div class="row"><span>权益</span><b>{o['label']}</b></div>
<div class="row"><span>金额</span><b>{CONF['currency']} {o['amount']:.2f}</b></div>
{f"<div class='row'><span>兑换码</span><b>{o['redeem']}</b></div>" if o['redeem'] else ""}
<div class="muted" style="margin-top:10px">刷新面板即可看到余额 / 订阅。</div></div>"""
        body = f"""{mismatch}<div class="card">
<h2>扫码支付</h2>
<div class="muted">{CONF['payee_note']}</div>
{qr}
{picker}
<div class="amount">{CONF['currency']} {o['amount']:.2f}</div>
<div class="code">{o['code']}</div>
<div class="row"><span>权益</span><b>{o['label']}</b></div>
<div class="row"><span>到账邮箱</span><b>{o['email']}</b></div>
<div class="row"><span>状态</span><span class="tag {tag}">{txt}</span></div>
</div>{claim}{done}"""
        self.html(page("订单 " + o["code"], body))

    def claim(self, code):
        f = self.form()
        conn = db()
        conn.execute("UPDATE orders SET status='claimed', claimed_at=?, payer=?, note=?"
                     " WHERE code=? AND status IN ('pending','rejected')",
                     (time.time(), (f.get("payer") or "")[:120], (f.get("note") or "")[:300], code))
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/o/" + code))
        self.end_headers()

    # ---- admin ----
    def login(self):
        f = self.form()
        if (f.get("user") or "") == CONF["admin_user"] and (f.get("pass") or "") == CONF["admin_pass"]:
            self.send_response(302)
            self.send_header("Set-Cookie", self.set_session())
            self.send_header("Location", self.rel("/admin"))
            self.end_headers()
            return
        self.html(page("登录失败", f"""<div class="login card"><h2>口令错误</h2>
<div class="muted">请重新输入管理员账号和口令。</div>
<p><a href="{self.rel('/admin')}">返回登录</a></p></div>""", admin=False), 401)

    def admin_home(self):
        if not self.logged_in():
            return self.html(page("管理员登录", f"""<div class="login card">
<h2>管理员登录</h2>
<div class="muted">用于管理收款码与核验订单</div>
<form method="post" action="{self.rel('/admin/login')}">
  <label>账号</label><input name="user" value="{CONF['admin_user']}" autocomplete="username">
  <label>口令</label><input name="pass" type="password" autocomplete="current-password" autofocus>
  <button>登录</button>
</form>
<div class="muted" style="margin-top:12px">忘记口令？服务器 {BASE_DIR}/paykit.json 里的 admin_pass</div>
</div>"""))
        conn = db()
        qrs = conn.execute("SELECT * FROM qrcodes ORDER BY enabled DESC, id DESC").fetchall()
        thumbs = []
        for q in qrs:
            on = q["enabled"]
            amt = float(q["amount"] or 0)
            amt_txt = f"{CONF['currency']} {int(amt) if amt == int(amt) else amt}" if amt > 0 else "通用（不限金额）"
            amt_badge = ("t-claimed" if amt > 0 else "t-paid")
            thumbs.append(f"""<div class="thumb">
 <img src="{self.rel('/qr/' + str(q['id']))}?t={int(time.time())}" alt="">
 <div class="nm">{q['label'] or ('收款码 #' + str(q['id']))}</div>
 <div class="muted" style="margin-bottom:4px">
   <span class="tag {amt_badge}">{amt_txt}</span>
 </div>
 <div class="muted"><span class="tag {'t-paid' if on else 't-rejected'}">{'启用中' if on else '已停用'}</span>
   · {time.strftime('%m-%d %H:%M', time.localtime(q['created_at']))}</div>
 <form method="post" action="{self.rel('/admin/qr-bind')}" style="display:flex;gap:6px;margin-top:8px">
   <input type="hidden" name="id" value="{q['id']}">
   <input name="amount" value="{'' if amt == 0 else (int(amt) if amt == int(amt) else amt)}"
          placeholder="绑定金额，0=通用" style="padding:7px 9px;font-size:13px">
   <button class="sm ghost">绑定</button>
 </form>
 <div class="ops">
  <form method="post" action="{self.rel('/admin/qr-toggle')}"><input type="hidden" name="id" value="{q['id']}">
   <button class="sm ghost">{'停用' if on else '启用'}</button></form>
  <form method="post" action="{self.rel('/admin/qr-delete')}" onsubmit="return confirm('删除这张收款码？')">
   <input type="hidden" name="id" value="{q['id']}"><button class="sm danger">删除</button></form>
 </div></div>""")
        rows = conn.execute("SELECT * FROM orders ORDER BY CASE status WHEN 'claimed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, id DESC LIMIT 300").fetchall()
        trs = []
        for o in rows:
            t = {"pending": "t-pending", "claimed": "t-claimed", "paid": "t-paid", "rejected": "t-rejected"}.get(o["status"], "")
            x = {"pending": "待转账", "claimed": "待核验", "paid": "已到账", "rejected": "已驳回"}.get(o["status"], o["status"])
            acts = ""
            if o["status"] in ("pending", "claimed"):
                acts = (f"<form method='post' action='{self.rel('/admin/confirm')}' style='display:inline'>"
                        f"<input type='hidden' name='code' value='{o['code']}'><button class='sm'>确认到账</button></form> "
                        f"<form method='post' action='{self.rel('/admin/reject')}' style='display:inline'>"
                        f"<input type='hidden' name='code' value='{o['code']}'><button class='sm danger'>驳回</button></form>")
            trs.append(f"<tr><td><b>{o['code']}</b><div class='muted'>{time.strftime('%m-%d %H:%M', time.localtime(o['created_at']))}"
                       f"{(' · 码#' + str(o['qr_id'])) if o['qr_id'] else ''}</div></td>"
                       f"<td>{o['email']}<div class='muted'>{o['label']}</div></td>"
                       f"<td><b>{CONF['currency']} {o['amount']:.2f}</b></td>"
                       f"<td><span class='tag {t}'>{x}</span><div class='muted'>{o['payer']}</div></td><td>{acts}</td></tr>")
        custom_ok = allow_custom_amount(conn)
        tiers = presets(conn)
        tiers_str = ",".join(str(int(t) if t == int(t) else t) for t in tiers)
        sel_yes = " selected" if custom_ok else ""
        sel_no = "" if custom_ok else " selected"
        # 档位覆盖检查：区分「套餐价」与「充值档位」，并按当前模式判断
        def has_code(a):
            if conn.execute("SELECT 1 FROM qrcodes WHERE enabled=1 AND amount=0").fetchone():
                return True
            return bool(conn.execute("SELECT 1 FROM qrcodes WHERE enabled=1 AND abs(amount-?)<0.005", (a,)).fetchone())

        def tag(a, ok, key):
            shown = int(a) if a == int(a) else a
            return (f"<span class='tag {'t-paid' if ok else 't-rejected'}' style='margin:0 6px 6px 0'>"
                    f"{key} {CONF['currency']} {shown} {'✓' if ok else '缺码'}</span>")

        plan_prices = sorted({round(float(p["amount"]), 2) for p in CONF["plans"]})
        plan_tags = "".join(tag(a, has_code(a), "套餐") for a in plan_prices)
        n_generic = conn.execute("SELECT COUNT(*) c FROM qrcodes WHERE enabled=1 AND amount=0").fetchone()["c"]
        if custom_ok:
            topup_line = (f"<div style='margin-top:10px' class='muted'>自定义金额："
                          f"<span class='tag {'t-paid' if n_generic else 't-rejected'}'>"
                          f"通用收款码 {n_generic} 张{'✓' if n_generic else '（缺，需上传不限额码）'}</span></div>")
        else:
            tier_tags = "".join(tag(a, has_code(a), "档位") for a in tiers) or "<span class='muted'>未设置档位</span>"
            topup_line = f"<div style='margin-top:10px' class='muted'>充值档位：</div><div style='margin-top:6px'>{tier_tags}</div>"
        chk_html = ("<div class='muted' style='margin-top:14px'>收款码覆盖检查：</div>"
                    f"<div style='margin-top:6px' class='muted'>套餐价：</div><div style='margin-top:6px'>{plan_tags}</div>"
                    f"{topup_line}")

        tier_opts = sorted({10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0}
                           | set(plan_prices)
                           | {round(float(t), 2) for t in tiers})
        opts_html = "".join(
            f'<option value="{int(a) if a == int(a) else a}"'
            f'{" selected" if any(abs(a - t) < 0.005 for t in tiers) else ""}>'
            f'{CONF["currency"]} {int(a) if a == int(a) else a}</option>'
            for a in tier_opts)

        body = f"""<div class="card"><h2>收款方式</h2>
<div class="muted">控制用户「余额充值」时能不能自己填金额，保存后立即生效（前端展示与后端校验同时改）。</div>
<form method="post" action="{self.rel('/admin/settings')}" onsubmit="return collectTiers()" style="margin-top:12px">
  <label>充值金额模式</label>
  <select name="allow_custom">
    <option value="1"{sel_yes}>允许自定义金额（用户自己填，用不限额收款码）</option>
    <option value="0"{sel_no}>仅固定档位（用户只能选下面金额）</option>
  </select>
  <label>固定档位（下拉可多选，仅「仅固定档位」模式生效）</label>
  <select id="tierSel" multiple size="7" style="padding:8px">
    {opts_html}
  </select>
  <input type="hidden" name="presets" id="tierHidden" value="{tiers_str}">
  <div class="muted">套餐卡片价格不受这里影响，始终按卡片价收款。</div>
  <button>保存</button>
</form></div>
<script>
function collectTiers(){{var s=document.getElementById('tierSel');
 document.getElementById('tierHidden').value=Array.from(s.selectedOptions).map(o=>o.value).join(',');return true;}}
</script>

<div class="card"><h2>支付宝收款码</h2>
<div class="muted">可以放多张。**定额收款码请填对应金额**（如 10），系统会把该金额的订单派给它；留空/填 0 表示通用码。
同金额有多张时，派给「当前待处理订单最少」的那张。</div>
<div class="thumbs" style="margin-top:14px">{''.join(thumbs) or "<div class='muted'>还没有收款码，先在下面上传一张</div>"}</div>
{chk_html}
<label>上传新收款码（支付宝 App → 收钱 → 保存收款码）</label>
<input type="file" id="qrf" accept="image/png,image/jpeg">
<input id="qrl" placeholder="备注名，例如「支付宝 ¥10 定额」">
<input id="qramt" placeholder="绑定金额：定额码填如 10；不限额留空" inputmode="decimal">
<button onclick="up()">上传</button>
<div class="muted" id="msg" style="margin-top:8px"></div>
<script>
async function up(){{
 const f=document.getElementById('qrf').files[0], msg=document.getElementById('msg');
 if(!f){{msg.textContent='请先选择图片';return;}}
 if(f.size>3*1024*1024){{msg.textContent='图片需小于 3MB';return;}}
 msg.textContent='上传中…';
 const b64=await new Promise(r=>{{const fr=new FileReader();fr.onload=()=>r(String(fr.result).split(',')[1]);fr.readAsDataURL(f);}});
 const res=await fetch('{self.rel("/admin/qr-upload")}',{{method:'POST',credentials:'same-origin',
   headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{label:document.getElementById('qrl').value,
   amount:document.getElementById('qramt').value,b64:b64}})}});
 const j=await res.json();
 msg.textContent=j.ok?('✅ 已上传：'+j.label+'，正在刷新…'):('❌ '+(j.error||'失败'));
 if(j.ok)setTimeout(()=>location.reload(),700);
}}
</script></div>

<div class="card"><h2>订单核验</h2>
<div class="muted">核对支付宝到账后点「确认到账」，系统自动开通套餐/加余额（幂等，重复点不会重复加）。</div>
<table style="margin-top:10px"><tr><th>备注码</th><th>用户</th><th>金额</th><th>状态</th><th>操作</th></tr>
{''.join(trs) or "<tr><td colspan=5 class='muted'>暂无订单</td></tr>"}</table></div>"""
        self.html(page("管理后台", body, admin=True))

    def admin_settings(self):
        if not self.logged_in():
            return self.html(page("401", "<div class='card'>未登录</div>"), 401)
        f = self.form()
        conn = db()
        kv_set(conn, "allow_custom_amount", "1" if (f.get("allow_custom") == "1") else "0")
        raw = (f.get("presets") or "").replace("，", ",")
        vals = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                v = round(float(part), 2)
                if v > 0:
                    vals.append(v)
            except ValueError:
                continue
        kv_set(conn, "amount_presets", json.dumps(vals))
        self.send_response(302)
        self.send_header("Location", self.rel("/admin"))
        self.end_headers()

    def qr_upload(self):
        if not self.logged_in():
            return self.js({"ok": False, "error": "未登录"}, 401)
        d = self.jbody()
        raw = (d.get("b64") or "").strip()
        if "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            blob = base64.b64decode(raw)
        except (binascii.Error, ValueError):
            return self.js({"ok": False, "error": "图片解析失败"}, 400)
        if len(blob) < 200 or len(blob) > 3 * 1024 * 1024:
            return self.js({"ok": False, "error": "图片大小异常"}, 400)
        if blob[1:4] == b"PNG":
            ext, mime = "png", "image/png"
        elif blob[:2] == b"\xff\xd8":
            ext, mime = "jpg", "image/jpeg"
        else:
            return self.js({"ok": False, "error": "仅支持 PNG/JPG"}, 400)
        conn = db()
        label = (d.get("label") or "").strip()[:40]
        try:
            qr_amount = round(float(d.get("amount") or 0), 2)
        except (TypeError, ValueError):
            qr_amount = 0.0
        if qr_amount < 0:
            qr_amount = 0.0
        cur = conn.execute("INSERT INTO qrcodes(label,file,mime,enabled,created_at,amount) VALUES(?,?,?,1,?,?)",
                           (label, "pending", mime, time.time(), qr_amount))
        qid = cur.lastrowid
        fn = f"qr-{qid}.{ext}"
        open(os.path.join(QR_DIR, fn), "wb").write(blob)
        conn.execute("UPDATE qrcodes SET file=? WHERE id=?", (fn, qid))
        conn.commit()
        return self.js({"ok": True, "id": qid, "label": label or f"#{qid}"})

    def qr_bind(self):
        if not self.logged_in():
            return self.html(page("401", "<div class='card'>未登录</div>"), 401)
        f = self.form()
        try:
            qid = int(f.get("id") or 0)
        except ValueError:
            qid = 0
        try:
            amt = round(float(f.get("amount") or 0), 2)
        except (TypeError, ValueError):
            amt = 0.0
        if amt < 0:
            amt = 0.0
        conn = db()
        conn.execute("UPDATE qrcodes SET amount=? WHERE id=?", (amt, qid))
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/admin"))
        self.end_headers()

    def qr_toggle(self):
        if not self.logged_in():
            return self.html(page("401", "<div class='card'>未登录</div>"), 401)
        f = self.form()
        conn = db()
        try:
            conn.execute("UPDATE qrcodes SET enabled=1-enabled WHERE id=?", (int(f.get("id") or 0),))
            conn.commit()
        except ValueError:
            pass
        self.send_response(302)
        self.send_header("Location", self.rel("/admin"))
        self.end_headers()

    def qr_delete(self):
        if not self.logged_in():
            return self.html(page("401", "<div class='card'>未登录</div>"), 401)
        f = self.form()
        conn = db()
        try:
            qid = int(f.get("id") or 0)
        except ValueError:
            qid = 0
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

    def confirm(self):
        if not self.logged_in():
            return self.html(page("401", "<div class='card'>未登录</div>"), 401)
        f = self.form()
        code = f.get("code") or ""
        conn = db()
        o = conn.execute("SELECT * FROM orders WHERE code=?", (code,)).fetchone()
        if not o:
            return self.html(page("404", "<div class='card'>订单不存在</div>"), 404)
        if o["status"] == "paid":
            msg, redeem = "该订单已到账，未重复处理", o["redeem"] or ""
        else:
            ok, detail = fulfil(o)
            if ok:
                msg, redeem = "已自动到账：套餐/余额已开通 ✅", ""
            else:
                redeem = gen_redeem(o)
                msg = (f"未能自动下发（{detail}），已生成兑换码，请发给用户" if redeem
                       else "标记到账失败，请手动处理")
            conn.execute("UPDATE orders SET status='paid', paid_at=?, redeem=? WHERE code=?",
                         (time.time(), redeem, code))
            conn.commit()
        body = f"""<div class="card"><h2>处理完成</h2>
<div class="row"><span>订单</span><b>{code}</b></div>
<div class="row"><span>结果</span><b>{msg}</b></div>
{f"<div class='code' style='margin-top:14px'>{redeem}</div>" if redeem else ""}
<p><a href="{self.rel('/admin')}">← 返回核验台</a></p></div>"""
        self.html(page("处理完成", body, admin=True))

    def reject(self):
        if not self.logged_in():
            return self.html(page("401", "<div class='card'>未登录</div>"), 401)
        f = self.form()
        conn = db()
        conn.execute("UPDATE orders SET status='rejected' WHERE code=?", (f.get("code") or "",))
        conn.commit()
        self.send_response(302)
        self.send_header("Location", self.rel("/admin"))
        self.end_headers()


if __name__ == "__main__":
    print(f"paykit v3 on {CONF['listen']}:{CONF['port']}{CONF['base_path']}/", flush=True)
    ThreadingHTTPServer((CONF["listen"], int(CONF["port"])), H).serve_forever()
