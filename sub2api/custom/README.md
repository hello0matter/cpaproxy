# sub2api 定制部署套件

本目录是 **上游开源项目 [Sub2API](https://github.com/Wei-Shaw/sub2api) 的快照 + 本项目的定制层**。

## 目录说明

| 路径 | 说明 |
| --- | --- |
| `../`（`sub2api/` 根） | 上游 `v0.2.21`（commit `10ba8f9`, 2026-11-11）源码快照；**仅有 4 处凭据去硬编码的改动**，见 [`PATCHES.md`](./PATCHES.md) |
| `custom/`（本目录） | 我们自己的定制：部署脚本、compose、配置模板、站点设置对照 |

## 关于源码改动

只改了 4 处：上游把 **Google 公共 OAuth 客户端 ID/Secret** 硬编码在源码里
（`antigravity/oauth.go`、`geminicli/constants.go` 及其测试），GitHub 推送保护会直接拒绝推送。
处理方式是**清空字面量、改为只从环境变量读取**，其余逻辑与上游一致，详见 `PATCHES.md`。

除此之外源码零改动，理由：

1. 部署用的是官方镜像 `weishaw/sub2api:<tag>`，改源码必须自己重新编译镜像才会生效，收益低；
2. 保持上游目录基本干净，后续 `git diff` / `git pull` 升级时冲突面最小；
3. 品牌名、站点名、菜单、支付、验证码这些**全部是运行期配置**（存在数据库 + `config.yaml`），改配置就够。

> 如果确实需要改代码（例如复刻某个站点的私有功能），建议在本目录下建 `patches/`，
> 用 `git format-patch` 管理，而不是直接改上游文件。

## 与目标站点（8888ok）的对齐关系

目标站点的可见改动分为两类，详见 [`SITE-PARITY.md`](./SITE-PARITY.md)：

- **配置层**（品牌名、标题、自定义端点、渠道监控、模型广场、支付、Turnstile 等）→ 本套件已给出配置方法，可在后台一键设置；
- **私有二开**（工单客服、知识库、成长福利抽奖、订阅监控、微信 JSAPI 前端等）→ 上游源码中不存在，无公开代码，无法复刻，只能在本套件里标注。

## 部署

见 [`DEPLOY.md`](./DEPLOY.md)。当前这套配置是按服务器 `38.55.194.201`
（1 核 / 967 MB / 磁盘紧张，已跑 new-api、cli-proxy-api、nginx、postgres、redis）设计的：

- 复用现有 `postgres` / `redis` 容器（同一 docker 网络 `ai-gateway`），不额外起数据库；
- 应用只绑定 `127.0.0.1:18080`，不直接对外，需要域名时再加 nginx 反代；
- 数据落在 `/root/cpaproxy/sub2api/data`；
- 容器内存上限 384 MB，避免挤掉宿主机上已有的服务。

## 快速开始（服务器上）

```bash
cd /root/cpaproxy/sub2api/custom
cp .env.example .env && vi .env      # 填数据库口令、管理员账号
bash deploy.sh
```

## 版本

- 上游：`Wei-Shaw/sub2api` `v0.2.21`
- 镜像：`weishaw/sub2api:0.2.21`（amd64，85 MB）
- 许可证：见上游根目录 `LICENSE`
