# XPAY 支付接入（易支付协议）

## 目标站点用的是什么

8888ok 的支付不是自建个人收款码，而是接了 **XPAY 免签码支付**（`api.zxspro.cn`）：

| 证据 | 说明 |
| --- | --- |
| 购买后跳转 `https://api.zxspro.cn/pay/<订单号>` | XPAY 收银台 |
| 收银台页面 meta | `XAPAY支付, 免签码支付, 易支付, 彩虹易支付, 码支付, 聚合支付` |
| 收银台内部接口 | `/api/order/info`、`/api/order/status`、`/api/order/qrcode`、`/pay/types` |
| 商户侧接口探测 | `POST /submit.php`、`POST /mapi.php` 均返回易支付标准 JSON；`/api.php` 404 |
| 域名限制 | 直接调用返回 `{"code":502,"message":"请使用授权的域名进行访问"}` —— 必须在 XPAY 后台授权域名 |
| 费率 | 例子：充值 ¥1.00 显示应付 **¥1.01**（1% 手续费独立展示） |

结论：**XPAY 是易支付兼容通道**，可以直接用 sub2api 原生的 `easypay` provider 接，
不用自己写支付代码，也不用人工核验。

## 我们需要什么

在 XPAY 商户后台拿到两项：

1. `pid` —— 商户 ID
2. `pkey` —— 商户密钥

并在 XPAY 后台把 **授权域名** 设为 `www.hacket.top`（否则下单会报「请使用授权的域名进行访问」）。

## 一键接入

```bash
cd /root/cpaproxy/sub2api/custom
PID=你的商户ID PKEY=你的密钥 ADMIN_PASSWORD=管理员密码 bash setup-xpay.sh
```

脚本会做三件事：

1. 用管理员账号登录 sub2api
2. `POST /api/v1/admin/payment/providers` 创建 `provider_key=easypay` 的实例，
   config 为：

   ```json
   {
     "pid": "...", "pkey": "...",
     "apiBase": "https://api.zxspro.cn",
     "notifyUrl": "https://www.hacket.top/api/v1/payment/webhook/easypay",
     "returnUrl": "https://www.hacket.top/payment/result",
     "cid": "", "cidAlipay": "", "cidWxpay": ""
   }
   ```

3. `PUT /api/v1/admin/payment/config` 打开支付总开关，允许 `alipay`/`wxpay`

> 回调地址必须公网可达：`https://www.hacket.top/api/v1/payment/webhook/easypay`
> （XPAY 会对这个地址发异步通知，sub2api 验签后自动到账）

## 校验

```bash
# 通道实例
curl -s -H "Authorization: Bearer <jwt>" http://127.0.0.1:18080/api/v1/admin/payment/providers

# 用户侧可见套餐
curl -s -H "Authorization: Bearer <jwt>" http://127.0.0.1:18080/api/v1/payment/plans
```

然后面板里点一次购买 → 应跳到 XPAY 收银台 → 扫码支付 1 元测试 → 回调后余额/订阅自动到账。

## 备选方案（已写好未部署）

`sub2api/custom/paykit/` 是一个「个人收款码 + 人工核验」的独立小台子（Python 标准库，
sqlite 存订单，管理员核验后调 sub2api 管理 API 自动开卡）。如果 XPAY 谈不下来可以启用它。
