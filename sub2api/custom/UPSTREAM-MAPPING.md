# 上游配置映射：new-api → sub2api

> 目的：让 sub2api 跑起和这台机器上 new-api 一样的上游。
> new-api 原本的 3 个渠道全部指向本机 CPA（`cli-proxy-api:8317`），这里按新模型重建了对应关系。

## 一、原来的 new-api 渠道（只读记录）

| id | 名称 | base_url | 分组 | 模型 |
| --- | --- | --- | --- | --- |
| 4 | cpa-grok | `http://cli-proxy-api:8317` | grok 官方号池 | grok-4.5 / 4.6 / 4.7 |
| 5 | cpa-usable | `http://cli-proxy-api:8317` | gpt plus | GPT-5.6 系列、gemini-3.x、gpt-image-2.x、mimo 等 25 个 |
| 6 | DeepSeek | `http://cli-proxy-api:8317` | deepseek官转 | deepseek-v4-flash / v4-pro / v4.1-flash |

三条渠道共用同一个 CPA 访问 key（`sk-BtNK…`，全文见服务器 `/opt/ai-gateway/config.yaml` 的 `api-keys` 段，不入库）。

## 二、sub2api 现在的对应配置

| sub2api 分组 (id) | 平台 | 对应账号 (id) | 上游 base_url | 说明 |
| --- | --- | --- | --- | --- |
| grok 官方号池 (2) | grok | cpa-grok (1) | `http://cli-proxy-api:8317/v1` | 复用 CPA |
| gpt plus (3) | openai | cpa-usable (2) | `http://cli-proxy-api:8317/v1` | 复用 CPA，**实测可用** |
| deepseek官转 (4) | deepseek | cpa-deepseek (3) | `http://cli-proxy-api:8317/v1` | 复用 CPA，**上游本身不可用**（见下） |
| 8888ok官转 (5) | openai | cpa-8888ok (4) | `https://8888ok.cc/v1` | 新增第三方上游，**实测可用** |
| default (1) | anthropic | — | — | 初始化自动创建 |

创建方式全部走后台 API（`POST /api/v1/admin/groups`、`POST /api/v1/admin/accounts`），
账号类型 `apikey`，凭据字段 `{"api_key": "...", "base_url": "..."}`。

## 三、实测结论（2026-11-12）

| 链路 | 模型 | 结果 |
| --- | --- | --- |
| sub2api → CPA | `gpt-5.6` | ✅ 200，正常返回 |
| sub2api → CPA | `grok-4.5` | ❌ CPA 侧 `auth_unavailable`：xAI 上游没有可用授权（返回的是 HTML 错误页） |
| sub2api → CPA | `deepseek-v4-flash` | ❌ CPA 上游返回 `SUBSCRIPTION_NOT_FOUND`（对方要求先买分组套餐） |
| sub2api → 8888ok | `deepseek-v4.1-flash` | ✅ 200，正常返回 |

> 也就是说：**能用的只有 gpt plus（经 CPA）和 8888ok（直连第三方）两条**。
> grok 和 deepseek 的失败点在 CPA 上游，不在 sub2api，修 CPA 的账号/订阅即可恢复。

## 四、注意

1. **计费**：sub2api 默认按余额扣费（`run_mode=standard`）。管理员初始余额为 0 时网关会返回
   `INSUFFICIENT_BALANCE`；本机已给 `admin@hacket.top` 充了 50 美元额度做验证。
   改余额：`docker exec postgres psql -U root -d sub2api -c "update users set balance=… where id=1"`
   改完需 `docker restart sub2api`（余额有缓存）。
2. **密钥**：用户 API Key 由 `POST /api/v1/keys` 生成，绑到分组后即可用 `https://www.hacket.top/v1` 调用。
3. **管理员合规**：首次调用后台接口会返回 `ADMIN_COMPLIANCE_ACK_REQUIRED`，需先
   `POST /api/v1/admin/compliance/accept`（正文是那句合规承诺），本机已确认过。
