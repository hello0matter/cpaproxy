# paykit —— 个人收款码 + 人工核验（666ok 扫码充值）

> 当前版本 **v3**：用户端仿 8888ok 的套餐卡片风格；管理端改成**账号密码登录页**（不再弹浏览器 Basic 框），收款码在网页上直接上传/切换。

## 线上地址

| 用途 | 地址 | 登录 |
| --- | --- | --- |
| 用户下单 | https://www.hacket.top/pay/ | 无需登录 |
| 管理后台 | https://www.hacket.top/pay/admin | 账号 `admin`，口令见下方 |

**管理员口令**存放在服务器 `/root/cpaproxy/sub2api/paykit/paykit.json` 的 `admin_pass` 字段（本文件不入库）。

## 站内导航入口（已配置）

sub2api 的 `settings.custom_menu_items` 里加了两项，按角色显示：

| 菜单 | 谁看得到 | 指向 |
| --- | --- | --- |
| **充值** | 普通用户 | `https://www.hacket.top/pay/` |
| **收款配置** | 管理员 | `https://www.hacket.top/pay/admin` |

> 用数据库直接改 `custom_menu_items`（JSON 数组），**不要**走 `PUT /api/v1/admin/settings` ——
> 那个接口的 `UpdateSettingsRequest` 里很多字段是非指针 bool，局部提交会把注册开关等一起冲成 false。

## 实测记录（2026-11-12）

| 场景 | 结果 |
| --- | --- |
| 普通用户 `gcsdfaksh@gmail.com` 充值 ¥5 | 余额 `0.00 → 5.00` ✅ |
| 重复点「确认到账」 | `该订单已到账，未重复处理`（幂等）✅ |
| 管理员余额 | 不受影响 ✅ |
| 游客看到的自定义菜单 | 只有「充值」；「收款配置」仅管理员可见 ✅ |

## 使用流程

### 管理员（一次性配置）

1. 打开 https://www.hacket.top/pay/admin → 输入账号口令登录
2. 「支付宝收款码」区块 → 选择图片（支付宝 App → 收钱 → 保存收款码）→ 填备注名 → 点「上传」
3. 可重复上传多张；每张都能**停用/启用/删除**
4. 新订单会自动分配给「当前待处理订单最少」的那张启用码

### 日常（每笔五分钟内）

1. 用户下单 → 拿到备注码 `666-XXXXXX` → 扫码转账并在备注里写这个码
2. 你在支付宝看到到账 → 打开 `/pay/admin` → 找到对应订单 → 点「**确认到账**」
3. 系统自动调 sub2api `create-and-redeem`：套餐单开通订阅、充值单加余额（**幂等**，重复点不会重复加）
4. 邮箱匹配不到用户时，自动生成兑换码显示在结果页，复制发给用户即可

## 为什么不是全自动

个人支付宝收款码**没有回调能力** —— 钱进的是你个人账户，支付宝不会通知任何服务器。
所以「确认到账」必须人工点一次；点完之后的开通是全自动的。

想要真回调只有三条路：支付宝官方当面付（需个体户/营业执照）、第三方聚合（XPAY 等）、
挂手机监听（V免签类，掉单/封号风险）。详见 `../XPAY.md`。

## 配置

编辑 `/root/cpaproxy/sub2api/paykit/paykit.json` 后 `systemctl restart paykit`：

```json
{
  "site_name": "666ok",
  "admin_email": "admin@hacket.top",        // sub2api 管理员邮箱（用于调管理 API）
  "admin_password": "<sub2api 管理员密码>",
  "admin_user": "admin",                     // 本台登录账号
  "admin_pass": "<本台登录口令>",
  "payee_note": "转账时请在备注里填写下方「备注码」",
  "amount_presets": [10, 50, 100, 200],
  "plans": [
    { "label": "DeepSeek 日卡", "badge": "日卡", "days": 1,  "amount": 9.9,  "group_id": 5,
      "desc": "1 天无限量 · 支持 v4 / v4.1 flash / pro", "features": ["无限流量", "全部分组"] },
    { "label": "DeepSeek 周卡", "badge": "周卡", "days": 7,  "amount": 49,   "group_id": 5 },
    { "label": "DeepSeek 月卡", "badge": "月卡", "days": 30, "amount": 159,  "group_id": 5 }
  ]
}
```

- `days > 0` → 开通订阅（`subscription` 兑换码，绑定 `group_id`）
- `days = 0` → 加余额（`balance` 兑换码，值 = 订单金额）
- `features` 会渲染成卡片上的 ✓ 列表，`desc` 是卡片描述

## 运维

```bash
systemctl status paykit
journalctl -u paykit -f
systemctl restart paykit

# 订单速查
python3 -c "import sqlite3;c=sqlite3.connect('/root/cpaproxy/sub2api/paykit/paykit.db');\
print(*c.execute('select code,email,amount,status from orders order by id desc limit 20').fetchall(),sep='\n')"

# 备份（订单 + 收款码）
tar czf /root/paykit-$(date +%F).tgz -C /root/cpaproxy/sub2api paykit
```

nginx（已配在 `www.hacket.top` 的 vhost 中）：

```nginx
location = /pay { return 301 /pay/; }
location /pay/ {
    proxy_pass http://127.0.0.1:18081;   # 不能带尾部斜杠，否则会剥掉 /pay 前缀
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
}
```

## 与 sub2api 自带「充值/购买」页的关系

sub2api 自带的 `/purchase` 页显示 *Top-up is currently unavailable*，是因为它没有配置**支付通道 provider**
（需要 EasyPay/支付宝/Stripe 等真实通道）。我们用的是 paykit，所以那个页面对用户没有意义，
充值入口请直接指向 `https://www.hacket.top/pay/`。
