#!/usr/bin/env bash
# cliproxy 监控 —— 安装 / 卸载 / 启停 一键脚本
# 用法:
#   sudo bash install.sh            安装并启动
#   sudo bash install.sh uninstall  停止并移除服务
#   sudo bash install.sh status     查看状态
set -euo pipefail

APP_DIR="/root/cpaproxy"
SVC_NAME="cpaproxy-monitor"
UNIT_DST="/etc/systemd/system/${SVC_NAME}.service"
PY="$(command -v python3 || true)"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 运行: sudo bash $0 $*" >&2
    exit 1
  fi
}

case "${1:-install}" in
  uninstall)
    need_root
    systemctl stop "$SVC_NAME" 2>/dev/null || true
    systemctl disable "$SVC_NAME" 2>/dev/null || true
    rm -f "$UNIT_DST"
    systemctl daemon-reload
    echo "已卸载服务 $SVC_NAME（数据目录 $APP_DIR/data 保留未删）"
    ;;

  status)
    systemctl --no-pager status "$SVC_NAME" || true
    echo
    echo "--- 最近 20 行日志 ---"
    journalctl -u "$SVC_NAME" -n 20 --no-pager || true
    ;;

  install)
    need_root
    if [ -z "$PY" ]; then
      echo "未找到 python3，请先安装: apt install -y python3" >&2
      exit 1
    fi

    # 首次安装时，把 config.linux.json 落成正式 config.json
    if [ -f "$APP_DIR/config.linux.json" ] && [ ! -f "$APP_DIR/config.json" ]; then
      cp "$APP_DIR/config.linux.json" "$APP_DIR/config.json"
      echo "已生成 $APP_DIR/config.json"
    fi

    mkdir -p "$APP_DIR/data"

    # 校验配置与 sqlite FTS5 支持
    "$PY" - <<'PYEOF'
import json, sqlite3, sys, pathlib
cfg = pathlib.Path("/root/cpaproxy/config.json")
data = json.loads(cfg.read_text(encoding="utf-8"))
print("log_dir :", data.get("log_dir"))
print("data_dir:", data.get("data_dir"))
c = sqlite3.connect(":memory:")
try:
    c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    print("sqlite fts5: OK")
except Exception as e:
    print("sqlite fts5: 不可用 ->", e)
PYEOF

    # 服务文件优先用同目录的，其次内联生成
    if [ -f "$APP_DIR/${SVC_NAME}.service" ]; then
      cp "$APP_DIR/${SVC_NAME}.service" "$UNIT_DST"
    else
      cat > "$UNIT_DST" <<EOF
[Unit]
Description=CLIProxy chat log monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$PY $APP_DIR/cliproxy_monitor.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
EOF
    fi

    systemctl daemon-reload
    systemctl enable "$SVC_NAME"
    systemctl restart "$SVC_NAME"
    sleep 2
    systemctl --no-pager --lines=15 status "$SVC_NAME" || true
    echo
    echo "安装完成。常用命令："
    echo "  systemctl start   $SVC_NAME"
    echo "  systemctl stop    $SVC_NAME"
    echo "  systemctl restart $SVC_NAME"
    echo "  systemctl status  $SVC_NAME"
    echo "  journalctl -u $SVC_NAME -f"
    ;;

  *)
    echo "未知参数: $1（可用: install / uninstall / status）" >&2
    exit 1
    ;;
esac