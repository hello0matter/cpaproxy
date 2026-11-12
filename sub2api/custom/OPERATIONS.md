# 运营配置：品牌 / 充值 / 渠道状态 / 日志

本文记录 2026-11-12 这一轮为「让站点看起来完整」所做的配置，以及 sub2api 原生的日志能力边界。

## 一、品牌

| 项 | 值 |
| --- | --- |
| 站点名 | `666ok`（`settings.site_name`；页面标题 `666ok - AI API Gateway`，侧边栏同名） |
| 副标题 | `Ctrl+D收藏站点`（`settings.site_subtitle`） |
| Logo | 仍是上游默认的 S 图标（`site_logo` 为 data URI），要换自己传图即可 |

## 二、充值 / 购买

| 项 | 值 |
| --- | --- |
| 支付总开关 | `settings.payment_enabled = true`（管理端 `PUT /admin/payment/config` 也会写这里） |
| 订阅购买入口 | `settings.purchase_subscription_enabled = true` |
| 套餐 | 已建 3 档，绑定分组 `8888ok官转`(id 5)：DeepSeek 日卡 ¥9.9 / 周卡 ¥49 / 月卡 ¥159 |
| 用户侧接口 | `GET /api/v1/payment/plans` 返回这 3 档 |

> ⚠️ **还差支付通道**：`/admin/payment/providers` 里没有任何 provider 实例，
> 用户现在能看到套餐卡片，但点「购买」会创建订单失败/无法支付。
> 需要接一种通道才有真实收入：易支付（EasyPay）、支付宝、微信、Stripe、Airwallex
> —— 每个都要对应商户号/密钥，把参数给我就能配上。

创建套餐的请求格式（管理端）：

```json
POST /api/v1/admin/payment/plans
{"group_id":5,"name":"DeepSeek 日卡","description":"1 天无限量","price":9.9,
 "currency":"CNY","validity_days":1,"validity_unit":"day","for_sale":true,"sort_order":1}
```

## 三、渠道状态（监控）

`settings.channel_monitor_enabled = true`，已建 3 个监控，实测全部 `operational`：

| id | 名称 | provider | endpoint | 主模型 | 间隔 |
| --- | --- | --- | --- | --- | --- |
| 1 | 8888ok DeepSeek | deepseek | `https://8888ok.cc/v1` | deepseek-v4.1-flash | 60s |
| 2 | 666ok 网关 GPT-5.6 | openai | `https://www.hacket.top/v1` | gpt-5.6 | 60s |
| 3 | 666ok 网关 DeepSeek | deepseek | `https://www.hacket.top/v1` | deepseek-v4.1-flash | 60s |

注意：监控要求 **https** endpoint（`http://` 会被拒：`CHANNEL_MONITOR_ENDPOINT_SCHEME`），
所以内网的 `http://cli-proxy-api:8317` 不能直接做监控目标，改成监控公网网关。

另外建了 4 条「渠道」(channels，用于定价/能力配置)：8888ok官转 / gpt plus / grok 官方号池 / deepseek官转。

## 四、AI 请求与响应怎么留痕（调试用）

### sub2api 本身：只记「元数据」，不记正文

| 表 / 机制 | 内容 | 是否含 prompt / 回答原文 |
| --- | --- | --- |
| `usage_logs` | 每次请求的 model、input/output tokens、各项成本、耗时、首 token 耗时、`request_id`、`user_agent` | ❌ 只有计量数据 |
| `ops_error_logs` | 失败请求的 `request_id`、路径、状态码、`error_message`、`error_body`、`upstream_error_message` | ❌ 只有错误体 |
| `audit_logs` | 管理端操作审计 | ❌ |
| `ops_runtime_log_config.persist_access_logs` | 打开后把访问日志落库（方法/路径/状态/耗时） | ❌ 默认关，本轮已置 true |

代码里没有「记录请求正文」的开关（`grep` 过 `LogRequestBody/body_capture/log_prompt` 均无实现），
只有解析失败时才记录错误原因（`request_body_parse_log.go`）。

### 要正文就用 CPA：它有完整请求/响应日志

CPA（`cli-proxy-api`）对每个请求写一个独立日志文件：`/opt/ai-gateway/logs/*.log`

```
=== REQUEST INFO ===   Version / URL / Method / Transport / Timestamp
=== HEADERS ===        含 Authorization（脱敏成 sk-B...X1gh）
=== REQUEST BODY ===   ← 完整 prompt / input
=== API REQUEST 1 ===  上游 URL、auth provider
...                    响应体（含 reasoning 与正文）
```

所以想要「调试用的请求+响应」有两条可行路：

1. **让流量走 CPA**（推荐）：把上游加进 CPA 的 `api-keys.openai-compatibility`（本轮已加 8888ok），
   sub2api 的账号就指向 CPA，日志自动落在 `/opt/ai-gateway/logs`；
   再配本机已有的 `cpaproxy-monitor` 服务，就能把 prompt/回答抽成 SQLite 方便检索。
2. **直连上游**（如现在的 `cpa-8888ok` 账号直连 8888ok.cc）：sub2api 只记计量，正文拿不到。

> 也就是说：**走 CPA 的开销就是多一跳，换来的是全量请求/响应日志**。
> 若需要，我可以把 `8888ok官转` 分组的账号从「直连 8888ok」切换成「指向 CPA」，
> 这样既有日志、配置又只维护一处。
