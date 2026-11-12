#!/usr/bin/env bash
# =============================================================================
# Sub2API 一键部署 / 更新（复用现有 postgres + redis）
# -----------------------------------------------------------------------------
#   bash deploy.sh           部署或更新（幂等，可重复执行）
#   bash deploy.sh status    查看状态
#   bash deploy.sh logs      跟踪日志
#   bash deploy.sh stop      停止并删除容器（数据保留）
#
# 前置：
#   1. 同目录下有 .env（从 .env.example 复制并填好）
#   2. 已有 docker 网络 ai-gateway，以及 postgres / redis 容器
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.reuse.yaml"

die() { echo "错误: $*" >&2; exit 1; }

need_env() {
  [ -f .env ] || die "缺少 .env，先执行: cp .env.example .env && vi .env"
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
  [ -n "${DATABASE_PASSWORD:-}" ] || die ".env 里 DATABASE_PASSWORD 为空"
  [ -n "${ADMIN_EMAIL:-}" ] || die ".env 里 ADMIN_EMAIL 为空"
  [ -n "${ADMIN_PASSWORD:-}" ] || die ".env 里 ADMIN_PASSWORD 为空"
  case "$ADMIN_PASSWORD" in
    CHANGE_ME*) die ".env 里 ADMIN_PASSWORD 还是占位符，请改成真实口令（8-72 位）" ;;
  esac
}

preflight() {
  command -v docker >/dev/null 2>&1 || die "未安装 docker"
  docker network inspect ai-gateway >/dev/null 2>&1 \
    || die "找不到 docker 网络 ai-gateway（现有 new-api 那套网络）"
  docker inspect postgres >/dev/null 2>&1 || die "找不到 postgres 容器"
  docker inspect redis >/dev/null 2>&1 || die "找不到 redis 容器"
}

ensure_database() {
  local db="${DATABASE_DBNAME:-sub2api}" user="${DATABASE_USER:-root}"
  local exists
  exists="$(docker exec postgres psql -U "$user" -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='${db}'" | tr -d '[:space:]')"
  if [ "$exists" = "1" ]; then
    echo "[db] 数据库 ${db} 已存在，跳过创建"
  else
    echo "[db] 创建数据库 ${db}"
    docker exec postgres psql -U "$user" -d postgres -c \
      "CREATE DATABASE \"${db}\"" >/dev/null
    docker exec postgres psql -U "$user" -d postgres -c \
      "GRANT ALL PRIVILEGES ON DATABASE \"${db}\" TO \"${user}\"" >/dev/null || true
  fi
}

wait_ready() {
  local port="${HOST_PORT:-18080}" i code
  echo "[wait] 等待服务就绪 http://127.0.0.1:${port}/"
  for i in $(seq 1 60); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 \
      "http://127.0.0.1:${port}/" || true)"
    if [ "$code" = "200" ] || [ "$code" = "302" ]; then
      echo "[ok] 服务已就绪（HTTP ${code}）"
      return 0
    fi
    sleep 2
  done
  echo "[warn] 90 秒内未就绪，最近的日志：" >&2
  docker logs --tail 40 sub2api >&2 || true
  return 1
}

case "${1:-up}" in
  up)
    need_env
    preflight
    mkdir -p "${DATA_DIR:-/root/cpaproxy/sub2api/data}"
    ensure_database
    echo "[docker] 拉取镜像 ${SUB2API_IMAGE:-weishaw/sub2api:0.2.21}"
    docker compose -f "$COMPOSE_FILE" --env-file .env pull
    echo "[docker] 启动容器"
    docker compose -f "$COMPOSE_FILE" --env-file .env up -d
    wait_ready || true
    echo
    echo "---------------------------------------------"
    echo " 访问地址 : http://127.0.0.1:${HOST_PORT:-18080}/"
    echo " 管理员   : ${ADMIN_EMAIL}"
    echo " 初始化   : 若密码是自动生成的，执行"
    echo "            docker logs sub2api 2>&1 | grep -i 'admin'"
    echo " 日志     : docker logs -f sub2api"
    echo "---------------------------------------------"
    ;;
  status)
    docker ps --filter "name=${CONTAINER_NAME:-sub2api}" \
      --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
    echo
    docker stats --no-stream "${CONTAINER_NAME:-sub2api}" 2>/dev/null || true
    ;;
  logs)
    docker logs -f --tail 100 "${CONTAINER_NAME:-sub2api}"
    ;;
  stop)
    need_env
    docker compose -f "$COMPOSE_FILE" --env-file .env down
    echo "容器已停止，数据目录保留：${DATA_DIR:-/root/cpaproxy/sub2api/data}"
    ;;
  *)
    echo "用法: bash deploy.sh [up|status|logs|stop]" >&2
    exit 1
    ;;
esac
