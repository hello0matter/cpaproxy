# 相对上游的改动（补丁说明）

上游快照：`Wei-Shaw/sub2api` `v0.2.21`（commit `10ba8f9`）。
为让本仓库能公开推送（GitHub 推送保护 / secret scanning 会拦截），
对 **4 处硬编码的 Google 公共 OAuth 凭据**做了最小改造：**清空字面量，改为只从环境变量读取**。

改造后功能逻辑保持上游原样：这些凭据本来就是"内置客户端"的可选默认值，
未配置时上游自身就会返回明确的缺失错误（提示改用自建 OAuth 客户端）。

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `backend/internal/pkg/antigravity/oauth.go` | 删除 `ClientID` 常量里的 Google client_id 字面量；改为 `var ClientID = os.Getenv("ANTIGRAVITY_OAUTH_CLIENT_ID")`；新增常量 `AntigravityOAuthClientIDEnv` |
| 2 | 同上 | `defaultClientSecret` 由硬编码改为空串，仍支持 `ANTIGRAVITY_OAUTH_CLIENT_SECRET` 覆盖（上游 `init()` 逻辑不变） |
| 3 | `backend/internal/pkg/geminicli/constants.go` | 删除 `GeminiCLIOAuthClientID` / `GeminiCLIOAuthClientSecret` 的字面量；改为从 `GEMINI_CLI_OAUTH_CLIENT_ID` / `GEMINI_CLI_OAUTH_CLIENT_SECRET` 读取；新增常量 `GeminiCLIOAuthClientIDEnv` |
| 4 | `backend/internal/pkg/antigravity/oauth_test.go` | 断言由"等于硬编码值"改为"等于对应环境变量"，并对 secret 为空的情况断言 `getClientSecret()` 报错 |

## 使用 Gemini CLI / Antigravity 时必读

如果你的部署需要用到 **Gemini CLI 内置客户端**或 **Antigravity OAuth** 登录，
必须在容器环境里补上这 4 个变量（用你自己在 Google Cloud 创建的项目凭据）：

```bash
GEMINI_CLI_OAUTH_CLIENT_ID=...
GEMINI_CLI_OAUTH_CLIENT_SECRET=...
ANTIGRAVITY_OAUTH_CLIENT_ID=...
ANTIGRAVITY_OAUTH_CLIENT_SECRET=...
```

不配置时：只影响这两条 OAuth 登录链路，其它网关功能（API Key 转发、Claude/OpenAI 等）不受影响。

## 与上游对齐 / 升级

```bash
# 看本仓库相对上游的完整差异（源码快照本身只有上面 4 处）
git -C sub2api diff  # 需自行 init 一个指向上游的 remote 后对比
```

升级建议：用新版本覆盖 `sub2api/`（保留 `custom/`），然后重新套用本文件列出的 4 处改动。
