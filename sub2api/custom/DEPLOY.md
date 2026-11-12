# 部署手册（38.55.194.201）

## 一、宿主机现状（部署前确认过）

| 项 | 值 |
| --- | --- |
| 系统 | Debian GNU/Linux 13 (trixie), kernel 6.12 |
| 规格 | **1 核 vCPU / 967 MB 内存 / 9.7 GB 磁盘（空闲仅约 1.5 GB）** |
| Docker | 29.9.2，Compose v5.6.1 |
| 已有容器 | `new-api`(calciumion/new-api, 127.0.0.1:13000)、`cli-proxy-api`(eceasy/cli-proxy-api, 0.0.0.0:8317)、`postgres`(15-alpine)、`redis`(7-alpine) |
| Docker 网络 | `ai-gateway`（172.18.0.0/16），postgres/redis 的容器别名即 `postgres` / `redis` |
| 其它服务 | nginx(80/443/3000)、yudao(java 48080)、yudao-admin(node 3030)、iframe-host(node 3001)、card-server(py 3003)、cpaproxy-monitor(systemd) |

> ⚠️ 资源很紧：内存 967 MB、磁盘余量 1.5 GB。本套件因此选择
> **复用现有 postgres/redis + 只绑 127.0.0.1 + 384 MB 内存上限**。

## 二、部署步骤

```bash
# 0) 目录（所有 sub2api 相关文件都在 cpaproxy 目录下一个子目录里，不碰监控脚本）
mkdir -p /root/cpaproxy/sub2api
cd /root/cpaproxy/sub2api

# 1) 把仓库里的 custom/ 拉下来（或直接 scp 上传）
#    这里假设代码已经通过 git 放在 /root/cpaproxy/sub2api/
cd custom
cp .env.example .env
chmod 600 .env

# 2) 填 .env：数据库口令必须与现有 postgres 一致
#    DATABASE_PASSWORD / REDIS_PASSWORD = postgres 容器的 POSTGRES_PASSWORD
#    ADMIN_EMAIL / ADMIN_PASSWORD = 你要的初始管理员
vi .env

# 3) 部署
bash deploy.sh
```

`deploy.sh` 会依次：检查 `ai-gateway` 网络与 postgres/redis 容器 → 建库 `sub2api`
→ 拉镜像 → 起容器 → 等到 HTTP 200 → 打印访问地址。

## 三、访问

默认只监听本机：`http://127.0.0.1:18080/`。

服务器上验证：

```bash
curl -I http://127.0.0.1:18080/
docker logs sub2api --tail 50
```

### 需要外网访问时（nginx 反代）

已有 nginx 站点：`hacket.top`、`38.55.194.201`。加一个子域名即可：

```nginx
server {
    listen 443 ssl http2;
    server_name sub.example.com;

    ssl_certificate     /etc/letsencrypt/live/sub.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sub.example.com/privkey.pem;

    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_read_timeout 300s;
    }
}
```

> 没有域名时也可以临时在 `routes` 里把 `BIND_HOST` 改成 `0.0.0.0` 用
> `http://38.55.194.201:18080` 访问，但**不建议长期裸奔**（后台登录会被扫）。

## 四、日常运维

```bash
bash deploy.sh status      # 容器状态 + 实时资源占用
bash deploy.sh logs        # 跟踪日志
bash deploy.sh stop        # 停止（数据保留在 DATA_DIR）
bash deploy.sh up          # 重新部署 / 升级（改完 .env 里的镜像 tag 即可）
```

数据目录：`/root/cpaproxy/sub2api/data`（容器内 `/app/data`，
包含自动生成的 `config.yaml`、日志、上传文件）。

备份：

```bash
tar czf /root/sub2api-data-$(date +%F).tgz /root/cpaproxy/sub2api/data
docker exec postgres pg_dump -U root sub2api | gzip > /root/sub2api-db-$(date +%F).sql.gz
```

## 五、已知限制与注意事项

1. **磁盘**：镜像解压后约 250 MB，宿主机只剩 1.5 GB，建议先清理
   （`docker system prune -f`、旧日志、`/root/ruoyi-dump.sql.gz` 等）。
2. **内存**：容器上限 384 MB。Go 程序在 migration 阶段峰值会高一些；
   若启动即被 OOM kill，把 `MEMORY_LIMIT` 调到 512m 并观察
   `dmesg | tail` / `journalctl -k | grep -i oom`。
3. **数据库连接数**：现有 postgres 默认 `max_connections=100`，new-api 已占用一部分，
   所以 `.env` 里把 sub2api 的池子压到 20/10，不要再调大。
4. **Redis 隔离**：复用同一 redis 实例，用 `REDIS_DB=3` 与 new-api 隔离；
   该实例设了 `maxmemory 64mb` + `allkeys-lru`，属正常配置，不影响会话正确性。
5. **管理接口**：上游默认管理端路径 `/api/v1/admin/*`，部署完记得先改管理员密码，
   并开启 Turnstile（系统设置 → 安全）防止后台被爆破。
