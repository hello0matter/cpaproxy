#!/usr/bin/env bash
# =============================================================================
# 接 XPAY（易支付协议）作为 sub2api 的支付通道
# -----------------------------------------------------------------------------
# 用法：
#   PID=你的商户ID PKEY=你的商户密钥 bash setup-xpay.sh
#   # 可选：API_BASE（默认 https://api.zxspro.cn）、PUBLIC（默认 https://www.hacket.top）
#
# 前置（在 XPAY 商户后台做完）：
#   1. 开通「支付宝/微信」通道
#   2. 授权域名填 www.hacket.top  ← XPAY 会校验来源，未授权会报「请使用授权的域名进行访问」
#   3. 记下商户 ID(pid) 和商户密钥(pkey)
#
# 做完后回调地址是：https://www.hacket.top/api/v1/payment/webhook/easypay
# =============================================================================
set -euo pipefail

PID="${PID:?需要 PID（XPAY 商户ID）}"
PKEY="${PKEY:?需要 PKEY（XPAY 商户密钥）}"
API_BASE="${API_BASE:-https://api.zxspro.cn}"
PUBLIC="${PUBLIC:-https://www.hacket.top}"
S2A="${S2A:-http://127.0.0.1:18080}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@hacket.top}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:?需要 ADMIN_PASSWORD（sub2api 管理员密码）}"

API="$S2A/api/v1"

jqp() { python3 -c "import sys,json;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }

echo "[1/4] 登录 sub2api"
TOKEN=$(curl -s -X POST "$API/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" | jqp "d['data']['access_token']")

echo "[2/4] 创建 EasyPay 通道实例（provider_key=easypay）"
PAYLOAD=$(python3 - "$PID" "$PKEY" "$API_BASE" "$PUBLIC" <<'PY'
import json,sys
pid,pkey,base,pub = sys.argv[1:5]
print(json.dumps({
  "provider_key": "easypay",
  "name": "XPAY",
  "config": {
      "pid": pid,
      "pkey": pkey,
      "apiBase": base,
      "notifyUrl": f"{pub}/api/v1/payment/webhook/easypay",
      "returnUrl": f"{pub}/payment/result",
      "cid": "",
      "cidAlipay": "",
      "cidWxpay": ""
  },
  "supported_types": ["alipay", "wxpay"],
  "enabled": True,
  "payment_mode": "qrcode",
  "sort_order": 1
}, ensure_ascii=False))
PY
)
RESP=$(curl -s -X POST "$API/admin/payment/providers" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "$PAYLOAD")
echo "$RESP" | head -c 400; echo
echo "$RESP" | jqp "d.get('data',{}).get('id') or d" >/dev/null 2>&1 || { echo "创建失败，见上面的返回"; exit 1; }

echo "[3/4] 打开支付总开关 + 允许支付宝/微信"
curl -s -X PUT "$API/admin/payment/config" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled":true,"min_amount":1,"max_amount":0,"daily_limit":0,"order_timeout_minutes":30,"max_pending_orders":3,"balance_disabled":false,"enabled_payment_types":["alipay","wxpay"]}' \
  | head -c 200; echo

echo "[4/4] 当前通道实例"
curl -s -H "Authorization: Bearer $TOKEN" "$API/admin/payment/providers" | head -c 600; echo
echo
echo "完成。下一步：面板里点一次购买，走到收银台确认能拉起 XPAY；"
echo "若返回「请使用授权的域名进行访问」，去 XPAY 后台把 $PUBLIC 的域名加入授权。"
