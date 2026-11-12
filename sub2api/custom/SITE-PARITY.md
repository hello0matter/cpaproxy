# 与目标站点（8888ok.cc）的差异对照

目标站点的技术底座就是上游 [Sub2API](https://github.com/Wei-Shaw/sub2api)（基线约 `v0.2.21`），
但做了两类改动。下面逐项列出，并说明在本仓库里「已对齐 / 可配置对齐 / 无法复刻」。

## 一、配置层（本套件通过配置实现，不需要改代码）

| # | 站点表现 | 取值 | 在本实例怎么设置 |
| --- | --- | --- | --- |
| 1 | 站点名 | `8888ok`（标题 `8888ok - AI API Gateway`） | 后台 → 系统设置 → 站点名称；`.env` 里 `SITE_NAME` 只是备忘 |
| 2 | 站点副标题 | `Ctrl+D收藏站点` | 系统设置 → 站点副标题 |
| 3 | Logo / favicon | 仍用上游默认 `logo.svg`，内联成 `data:image/svg+xml;base64,…` | 系统设置 → Logo，直接贴 data URI 或 URL |
| 4 | 客服信息 | `QQ群：695753762` | 系统设置 → 联系方式 |
| 5 | 文档地址 | `https://api.aiportx.com/v1` | 系统设置 → 文档地址 |
| 6 | 自定义端点 | `[{name:"OpenAI", endpoint:"https://<域名>/v1", description:"OpenAI 格式请求"}]` | 系统设置 → 自定义端点 |
| 7 | 紧凑首页 | `compact_home_enabled=true` | 系统设置 → 首页 |
| 8 | 订阅购买入口 | `purchase_subscription_enabled=false` | 系统设置 → 订阅 |
| 9 | 支付 | 开启：Stripe / Airwallex / 易支付 / 微信 | 系统设置 → 支付（本实例按需开启） |
| 10 | 人机验证 | Cloudflare Turnstile 开启（另有腾讯/阿里云验证码的开关，站点关着） | 系统设置 → 安全；填 Turnstile site key |
| 11 | 渠道监控 | 开启，`mode=v1`，间隔 60s，隐藏吞吐 | 系统设置 → 渠道监控 |
| 12 | 模型广场 | 开启，不要求登录（`require_auth=false`） | 系统设置 → 模型广场 |
| 13 | 推广返利 | `affiliate_enabled=true` | 系统设置 → 推广 |
| 14 | 风控 | `risk_control_enabled=true` | 系统设置 → 风控 |
| 15 | 注册 | 开放，关闭邮箱验证，不限后缀 | 系统设置 → 注册 |
| 16 | 登录协议 | 关闭（但文档模板仍在：服务条款 / 使用政策 / 支持的国家和地区 / 服务特定条款） | 系统设置 → 登录协议 |
| 17 | 第三方登录 | GitHub / Google / LinuxDo / 钉钉 / 微信 / OIDC 全关 | 系统设置 → 登录方式 |
| 18 | 插件管理 | 关闭 | 系统设置 → 插件 |
| 19 | 列表默认每页 | 100 条，可选 10/20/50/100 | 系统设置 → 表格 |
| 20 | 时区 | `Asia/Shanghai`（`+08:00`） | 本套件已在 `.env` 固定 `TZ=Asia/Shanghai` |

> 说明：以上配置项存放在数据库里（由后台写），不在 `config.yaml` 中，
> 所以无法随代码一起提交；升级或迁移时用后台的「数据导入导出」或数据库备份带走。

## 二、私有二开（上游源码里没有，无法复刻）

把这些前端 chunk 名拿去对上游 `main` 全量文件树逐条比对，
**均不存在对应源文件**（159 个懒加载 chunk 里有 33 个查无此源）：

| 功能 | 站点里的产物 | 上游是否有 |
| --- | --- | --- |
| 工单 / 客服工作台 | `SupportView`、`SupportWorkbenchView`、`SupportAccountDialog` | ❌ |
| 右下角「咨询助手」 | `HelpChatPanel` | ❌ |
| 成长福利 / 抽奖 | `GrowthBenefitsView`、`OpsGrowthBenefitsPanel`（「至少配置一项抽奖奖励」） | ❌ |
| 知识库 | `KnowledgeBaseView`、`KnowledgeImage` | ❌ |
| 订阅监控看板 | `SubscriptionMonitorView` | ❌ |
| GPT 智能面板 | `GPTIntelligencePanel` | ❌ |
| 微信 JSAPI 前端 | `wxpay`（上游只有后端 provider）、`WechatPaymentCallbackView` | 部分 |
| 其它 | `entitlements`、`publicOnline`、`businessRevenue`、`businessDashboardFast`、`redeemError`、`tableFiltersPersistence` | ❌ |

同时它们的构建产物文件名带有 `-html-ref-r6-` 缓存戳，而上游 `frontend/vite.config.ts`
里没有任何 `assetFileNames / chunkFileNames` 定制 —— 说明对方改过构建配置并自行打包。

**结论**：这些是对方的私有/商业二开代码，没有公开仓库（已核对 `sub2api-plus`、
`lieeew/sub2api`(CRS2)、`tickernelz/sub2api`、`DR-lin-eng/sub2api`、`AuuCoder/sub2api-relay`
等公开 fork，均无这些文件）。本套件不复刻，只做标注。
