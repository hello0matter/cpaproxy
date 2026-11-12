# paykit —— 个人收款码 + 人工核验（666ok 扫码充值）

## 这是什么

一个**独立的小台子**（不在 sub2api 进程里、不改镜像），解决「用个人支付宝收款码收钱，还要自动开通套餐」这件事。

```
用户 /pay/            你 /pay/admin                    sub2api
 ├ 选套餐/填金额  ──▶ 生成订单 666-XXXXXX
 ├ 扫你的收款码
 ├ 转账时在备注填 666-XXXXXX
 ├ 点「我已转账」──▶ 待核验
 │                    ├ 看一眼支付宝到账
 │                    └ 点「确认到账」──────────▶ create-and-redeem
 │                                                 自动开套餐/加余额
 └ 刷新面板看到权益 ◀──────────────────────────────
```

**半自动**：钱进你个人支付宝，支付宝不会通知服务器（个人码没有回调能力），所以"确认到账"必须人工点一下；**点完之后的开通是全自动的**。

## 线上地址

| 用途 | 地址 |
| --- | --- |
| 用户下单 | https://www.hacket.top/pay/ |
| 管理核验台 | https://www.hacket.top/pay/admin （Basic Auth，账号 `admin`） |

## 收款码可多变（重点）

后台「收款码管理」支持**多张**，随时增删：

- **上传**：选图 → 填备注名 → 上传（PNG/JPG，≤3MB），立刻生效
- **停用 / 启用**：停用的码不再分配给新订单，但历史订单仍能看到当时的码
- **删除**：同时删文件
- **轮换规则**：新订单自动分配给**当前待处理订单最少**的那张启用的码（多码轮着用，避免单码被风控）

图片存放：`/root/cpaproxy/sub2api/paykit/qrcodes/qr-<id>.png|jpg`

## 改套餐 / 改文案

编辑 `/root/cpaproxy/sub2api/paykit/paykit.json`，然后 `systemctl restart paykit`：

```json
{
  "base_url": "http://127.0.0.1:18080",
  "admin_email": "admin@hacket.top",
  "admin_password": "<sub2api 管理员密码，用于调管理 API>",
  "admin_user": "admin",
  "admin_pass": "<核验台登录口令>",
  "qr_title": "支付宝收款码",
  "payee_note": "转账时请在备注里填写下方「备注码」",
  "currency": "CNY",
  "plans": [
    { "label": "DeepSeek 日卡 (1 天)",  "days": 1,  "amount": 9.9,  "group_id": 5 },
    { "label": "DeepSeek 周卡 (7 天)",  "days": 7,  "amount": 49,   "group_id": 5 },
    { "label": "DeepSeek 月卡 (30 天)", "days": 30, "amount": 159,  "group_id": 5 }
  ]
}
```

- `days > 0` → 开通**订阅**（`subscription` 兑换码，绑定 `group_id`）
- `days = 0` → 加**余额**（`balance` 兑换码，值 = 订单金额）

## 到账逻辑

1. 用户填的邮箱去 sub2api 里查用户（`/admin/users?search=`）
2. 查到 → `POST /admin/redeem-codes/create-and-redeem`（code 用订单的备注码，幂等）
3. 查不到或接口报错 → 兜底 `POST /admin/redeem-codes/generate`，把兑换码显示在结果页，人工发给用户

## 运维

```bash
systemctl status paykit
journalctl -u paykit -f
systemctl restart paykit          # 改完 paykit.json 后
sqlite3 /root/cpaproxy/sub2api/paykit/paykit.db 'select code,email,amount,status from orders order by id desc limit 20;'
tar czf /root/paykit-$(date +%F).tgz -C /root/cpaproxy/sub2api paykit   # 备份（含订单与收款码）
```

nginx 片段（已加在 `www.hacket.top` 的 vhost 里）：

```nginx
location = /pay { return 301 /pay/; }
location /pay/ {
    proxy_pass http://127.0.0.1:18081;   # 注意：不要写尾部斜杠，否则会剥掉 /pay 前缀
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
}
```

## 已知限制

- **没有自动回调**：个人收款码的物理限制。想要全自动只能上支付宝当面付（个体户资质）或第三方聚合（XPAY 等）。
- 订单不做自动过期清理（可加 cron 把 `pending` 超过 24h 的标记过期）。
- 同一邮箱多笔未确认订单不会合并，各自独立核验。
