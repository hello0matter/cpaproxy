#!/usr/bin/env bash
# =============================================================================
# 站点品牌 / 基础配置初始化（对齐 8888ok 的配置层）
# -----------------------------------------------------------------------------
# 目的：第一次登录后台之前，就把站点名、副标题、联系方式、文档地址、
#       自定义端点写进 settings 表，省得手工点后台。
#
# 用法（在服务器上，容器已启动过一次、AUTO_SETUP 已完成建表后执行）：
#   bash apply-site-settings.sh
#
# 说明：
#   - settings 表结构：settings(key VARCHAR(100) UNIQUE, value TEXT, updated_at)
#   - value 一律以文本形式存储；JSON 类配置存的是 JSON 字符串
#   - 幂等：已存在的 key 会覆盖更新，可重复执行
#   - 想改站点名/端点，直接改下面这几个变量再跑一次即可
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
SITE_SUBTITLE="Ctrl+D收藏站点"
CONTACT_INFO=""
DOC_URL=""
PUBLIC_BASE_URL="http://127.0.0.1:${HOST_PORT:-18080}"
COMPACT_HOME="true"
# 渠道监控 / 模型广场（与目标站点一致）
CHANNEL_MONITOR_ENABLED="true"
CHANNEL_MONITOR_MODE="v1"
CHANNEL_MONITOR_INTERVAL="60"
CHANNEL_MONITOR_HIDE_THROUGHPUT="true"
MODEL_PLAZA_ENABLED="true"
MODEL_PLAZA_REQUIRE_AUTH="false"
# ---------------------------------------------------------------------------

sql_escape() { printf "%s" "$1" | sed "s/'/''/g"; }

run_sql() {
  docker exec -i postgres psql -U "$DBU" -d "$DB" -v ON_ERROR_STOP=1 -q <<SQL
INSERT INTO settings (key, value, updated_at) VALUES
  ('site_name',                 '$(sql_escape "$SITE_NAME")',              NOW()),
  ('site_subtitle',             '$(sql_escape "$SITE_SUBTITLE")',          NOW()),
  ('contact_info',              '$(sql_escape "$CONTACT_INFO")',           NOW()),
  ('doc_url',                   '$(sql_escape "$DOC_URL")',                NOW()),
  ('compact_home_enabled',      '$(sql_escape "$COMPACT_HOME")',           NOW()),
  ('channel_monitor_enabled',   '$(sql_escape "$CHANNEL_MONITOR_ENABLED")',NOW()),
  ('channel_monitor_mode',      '$(sql_escape "$CHANNEL_MONITOR_MODE")',   NOW()),
  ('channel_monitor_default_interval_seconds', '$(sql_escape "$CHANNEL_MONITOR_INTERVAL")', NOW()),
  ('channel_monitor_hide_throughput', '$(sql_escape "$CHANNEL_MONITOR_HIDE_THROUGHPUT")',    NOW()),
  ('model_plaza_enabled',       '$(sql_escape "$MODEL_PLAZA_ENABLED")',    NOW()),
  ('model_plaza_require_auth',  '$(sql_escape "$MODEL_PLAZA_REQUIRE_AUTH")',NOW()),
  ('custom_endpoints',          '$(sql_escape "[{\"name\":\"OpenAI\",\"endpoint\":\"${PUBLIC_BASE_URL}/v1\",\"description\":\"OpenAI 格式请求\"}]")', NOW())
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
SQL
}

echo "[site] 写入站点配置到数据库 ${DB}"
run_sql
echo "[site] 当前值："
docker exec -i postgres psql -U "$DBU" -d "$DB" -c \
  "SELECT key, left(value, 60) AS value FROM settings
    WHERE key IN ('site_name','site_subtitle','contact_info','doc_url',
                  'compact_home_enabled','custom_endpoints')
    ORDER BY key;"
echo
echo "改完建议重启一次容器让前端缓存刷新：docker restart ${CONTAINER_NAME:-sub2api}"
