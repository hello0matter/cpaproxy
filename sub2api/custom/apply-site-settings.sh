#!/usr/bin/env bash
# =============================================================================
# 站点品牌 / 基础配置初始化（对齐 8888ok 的配置层）
# -----------------------------------------------------------------------------
# 目的：第一次登录后台之前，就把站点名、副标题、联系方式、渠道监控等写进
#       settings 表，省得手工点后台。
#
# 用法（容器已启动过一次、AUTO_SETUP 建表完成之后）：
#   bash apply-site-settings.sh
#   PUBLIC_BASE_URL=https://sub.example.com bash apply-site-settings.sh   # 同时写自定义端点
#
# 说明：
#   - settings 表结构：settings(key UNIQUE, value TEXT, updated_at)
#   - value 一律文本；custom_endpoints 存 JSON 字符串
#   - 幂等：已存在的 key 会被覆盖更新，可重复执行
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "缺少 .env"; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a

DB="${DATABASE_DBNAME:-sub2api}"
DBU="${DATABASE_USER:-root}"

# ---- 按需修改 --------------------------------------------------------------
SITE_NAME="${SITE_NAME:-CPAPROXY}"
SITE_SUBTITLE="${SITE_SUBTITLE:-Ctrl+D收藏站点}"
CONTACT_INFO="${CONTACT_INFO:-}"
DOC_URL="${DOC_URL:-}"
# 示例：https://sub.example.com；留空则不写 custom_endpoints
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}"

COMPACT_HOME="${COMPACT_HOME:-true}"
CHANNEL_MONITOR_ENABLED="${CHANNEL_MONITOR_ENABLED:-true}"
CHANNEL_MONITOR_MODE="${CHANNEL_MONITOR_MODE:-v1}"
CHANNEL_MONITOR_INTERVAL="${CHANNEL_MONITOR_INTERVAL:-60}"
CHANNEL_MONITOR_HIDE_THROUGHPUT="${CHANNEL_MONITOR_HIDE_THROUGHPUT:-true}"
MODEL_PLAZA_ENABLED="${MODEL_PLAZA_ENABLED:-true}"
MODEL_PLAZA_REQUIRE_AUTH="${MODEL_PLAZA_REQUIRE_AUTH:-false}"
# ---------------------------------------------------------------------------

sqlq() { printf "%s" "$1" | sed "s/'/''/g"; }

rows=""
add_row() { # key value
  local line="('$(sqlq "$1")', '$(sqlq "$2")', NOW())"
  if [ -z "$rows" ]; then rows="$line"; else rows="$rows,$line"; fi
}

add_row site_name                                 "$SITE_NAME"
add_row site_subtitle                             "$SITE_SUBTITLE"
add_row contact_info                              "$CONTACT_INFO"
add_row doc_url                                   "$DOC_URL"
add_row compact_home_enabled                      "$COMPACT_HOME"
add_row channel_monitor_enabled                   "$CHANNEL_MONITOR_ENABLED"
add_row channel_monitor_mode                      "$CHANNEL_MONITOR_MODE"
add_row channel_monitor_default_interval_seconds  "$CHANNEL_MONITOR_INTERVAL"
add_row channel_monitor_hide_throughput           "$CHANNEL_MONITOR_HIDE_THROUGHPUT"
add_row model_plaza_enabled                       "$MODEL_PLAZA_ENABLED"
add_row model_plaza_require_auth                  "$MODEL_PLAZA_REQUIRE_AUTH"

if [ -n "$PUBLIC_BASE_URL" ]; then
  ENDPOINT_JSON="[{\"name\":\"OpenAI\",\"endpoint\":\"${PUBLIC_BASE_URL%/}/v1\",\"description\":\"OpenAI 格式请求\"}]"
  add_row custom_endpoints "$ENDPOINT_JSON"
else
  echo "[site] 未设置 PUBLIC_BASE_URL，跳过 custom_endpoints"
fi

echo "[site] 写入站点配置到数据库 ${DB}"
docker exec -i postgres psql -U "$DBU" -d "$DB" -v ON_ERROR_STOP=1 -q <<SQL
INSERT INTO settings (key, value, updated_at) VALUES
  ${rows}
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
SQL

echo "[site] 当前值："
docker exec -i postgres psql -U "$DBU" -d "$DB" -c \
  "SELECT key, left(value, 60) AS value FROM settings
    WHERE key IN ('site_name','site_subtitle','contact_info','doc_url',
                  'compact_home_enabled','channel_monitor_mode','model_plaza_enabled',
                  'custom_endpoints')
    ORDER BY key;"

echo
echo "改完建议重启一次容器，让前端拿到的注入配置立即刷新："
echo "  docker restart ${CONTAINER_NAME:-sub2api}"
