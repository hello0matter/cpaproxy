#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
QQ / 手机号 互查工具
====================
数据表 qq_phone(qq, phone) 约 7.14 亿行，分区 InnoDB。

特点
- 自动兼容 phone 末尾带 \r（0x0D）的历史脏数据，查询时自动补 CHAR(13)，输出时自动清洗
- 支持 Windows / Linux
- 无参数运行 = 交互式命令行
- --gui  = 图形界面（tkinter）
- 配置在同目录 qq_lookup.json

用法
  python qq_lookup.py                # 交互式
  python qq_lookup.py --gui          # 图形界面
  python qq_lookup.py 3439591730     # 直接按 QQ 号查
  python qq_lookup.py 13001000087    # 直接按手机号查（自动判断）
  python qq_lookup.py --file nums.txt   # 批量，一行一个
  python qq_lookup.py --config other.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Windows 控制台中文输出修正
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CONFIG_NAME = "qq_lookup.json"

DEFAULT_CONFIG = {
    "mysql": {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "root",
        "password": "",
        "database": "test",
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "read_timeout": 120,
    },
    "table": "qq_phone",
    "limit": 200,
}

# phone 末尾可能残留的 \r
CR = "\r"
QQ_RE = re.compile(r"^\d{5,12}$")
PHONE_RE = re.compile(r"^1\d{10}$")


# ---------------------------------------------------------------- 配置
def config_path(explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(__file__).resolve().parent / CONFIG_NAME


def load_config(explicit=None):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    p = config_path(explicit)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k, v in user.items():
                    if k == "mysql" and isinstance(v, dict):
                        cfg["mysql"].update(v)
                    else:
                        cfg[k] = v
        except Exception as e:
            print("警告: 读取配置 %s 失败: %s" % (p, e))
    return cfg, p


def save_config(cfg, p):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 数据库
def connect(cfg):
    import pymysql

    m = cfg["mysql"]
    return pymysql.connect(
        host=m.get("host", "127.0.0.1"),
        port=int(m.get("port", 3306)),
        user=m.get("user", "root"),
        password=m.get("password", ""),
        database=m.get("database", "test"),
        charset=m.get("charset", "utf8mb4"),
        connect_timeout=int(m.get("connect_timeout", 10)),
        read_timeout=int(m.get("read_timeout", 120)),
        write_timeout=int(m.get("read_timeout", 120)),
        autocommit=True,
    )


def clean_phone(v):
    if v is None:
        return ""
    return str(v).strip().rstrip("\r\n")


def clean_qq(v):
    if v is None:
        return ""
    return str(v).strip()


def classify(text):
    """判断输入是 QQ 号还是手机号。返回 ('qq'|'phone'|'unknown', 值)"""
    t = (text or "").strip().replace(" ", "").replace("-", "")
    if not t:
        return "unknown", ""
    if PHONE_RE.match(t):
        return "phone", t
    if QQ_RE.match(t):
        return "qq", t
    return "unknown", t


def lookup(cur, table, kind, value, limit=200):
    """按类型查询，返回 [(qq, phone), ...]"""
    if kind == "qq":
        sql = "SELECT qq, phone FROM `%s` WHERE qq=%%s LIMIT %%s" % table
        cur.execute(sql, (value, limit))
    elif kind == "phone":
        # 兼容带 \r 的脏数据
        sql = "SELECT qq, phone FROM `%s` WHERE phone=%%s LIMIT %%s" % table
        cur.execute(sql, (value + CR, limit))
        rows = cur.fetchall()
        if rows:
            return [(clean_qq(a), clean_phone(b)) for a, b in rows]
        # 兜底：万一以后数据清洗过了
        cur.execute(sql, (value, limit))
    else:
        return []
    return [(clean_qq(a), clean_phone(b)) for a, b in cur.fetchall()]


def lookup_many(cur, table, items, limit=200):
    """批量查询，返回 [(输入, 类型, [(qq, phone)...]), ...]"""
    out = []
    for raw in items:
        kind, val = classify(raw)
        if kind == "unknown":
            out.append((raw, "unknown", []))
            continue
        out.append((raw, kind, lookup(cur, table, kind, val, limit)))
    return out


def test_connection(cfg):
    try:
        c = connect(cfg)
        cur = c.cursor()
        cur.execute("SELECT 1")
        cur.close()
        c.close()
        return True, "连接成功"
    except Exception as e:
        return False, str(e)


def table_info(cur, table, database):
    cur.execute(
        "SELECT TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
        (database, table),
    )
    r = cur.fetchone()
    if not r:
        return None
    rows, data, idx = r
    return {
        "rows": int(rows or 0),
        "data_gb": round((data or 0) / 1024 / 1024 / 1024, 2),
        "idx_gb": round((idx or 0) / 1024 / 1024 / 1024, 2),
    }


# ---------------------------------------------------------------- 交互式
def fmt_rows(rows):
    if not rows:
        return "  (无结果)"
    lines = []
    for qq, phone in rows:
        lines.append("  QQ: %-14s 手机号: %s" % (qq, phone))
    return "\n".join(lines)


def interactive(cfg, cfg_file):
    print("=" * 62)
    print(" QQ / 手机号 互查")
    print("=" * 62)
    ok, msg = test_connection(cfg)
    m = cfg["mysql"]
    print(" 数据库: %s@%s:%s/%s  表: %s" % (m["user"], m["host"], m["port"], m["database"], cfg["table"]))
    print(" 状态  : %s" % msg)
    if not ok:
        print("\n请检查同目录 %s 里的 mysql 配置。" % cfg_file.name)
        return 1
    try:
        c = connect(cfg)
        cur = c.cursor()
        info = table_info(cur, cfg["table"], m["database"])
        if info:
            print(" 数据量: %s 行 / 数据 %.2f GB / 索引 %.2f GB"
                  % (format(info["rows"], ","), info["data_gb"], info["idx_gb"]))
        cur.close()
        c.close()
    except Exception:
        pass

    print("\n输入 QQ 号或手机号查询；支持逗号/空格分隔多个；")
    print("输入 t 查看表信息，输入 q 退出。\n")

    while True:
        try:
            line = input("查询> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        low = line.lower()
        if low in ("q", "quit", "exit"):
            break
        if low == "t":
            try:
                c = connect(cfg)
                cur = c.cursor()
                info = table_info(cur, cfg["table"], cfg["mysql"]["database"])
                print("  " + json.dumps(info, ensure_ascii=False))
                cur.close()
                c.close()
            except Exception as e:
                print("  查询失败: %s" % e)
            continue

        items = [x for x in re.split(r"[,;\s]+", line) if x]
        try:
            c = connect(cfg)
            cur = c.cursor()
            results = lookup_many(cur, cfg["table"], items, cfg["limit"])
            cur.close()
            c.close()
        except Exception as e:
            print("  查询失败: %s" % e)
            continue

        for raw, kind, rows in results:
            label = {"qq": "QQ号", "phone": "手机号", "unknown": "无法识别"}[kind]
            print("\n[%s] %s" % (label, raw))
            if kind == "unknown":
                print("  (请输入 5-12 位 QQ 号，或 1 开头的 11 位手机号)")
            else:
                print(fmt_rows(rows))
    print("已退出。")
    return 0


# ---------------------------------------------------------------- GUI
def run_gui(cfg, cfg_file):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    root = tk.Tk()
    root.title("QQ / 手机号 互查")
    root.geometry("820x620")

    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")

    ttk.Label(top, text="输入 QQ 号或手机号（可多个，逗号/空格分隔）：").pack(anchor="w")
    entry = tk.Text(top, height=3, wrap="word")
    entry.pack(fill="x", pady=4)

    btns = ttk.Frame(top)
    btns.pack(fill="x", pady=4)

    out_frame = ttk.Frame(root, padding=(8, 0, 8, 8))
    out_frame.pack(fill="both", expand=True)
    cols = ("input", "type", "qq", "phone")
    tree = ttk.Treeview(out_frame, columns=cols, show="headings", height=18)
    for c, t, w in zip(cols, ("输入", "类型", "QQ", "手机号"), (160, 80, 140, 150)):
        tree.heading(c, text=t)
        tree.column(c, width=w, anchor="w")
    vs = ttk.Scrollbar(out_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vs.set)
    tree.pack(side="left", fill="both", expand=True)
    vs.pack(side="right", fill="y")

    status = ttk.Label(root, text="", padding=(8, 2))
    status.pack(fill="x")

    conn = {"c": None}

    def get_conn():
        if conn["c"] is None:
            conn["c"] = connect(cfg)
        return conn["c"]

    def do_query():
        text = entry.get("1.0", "end").strip()
        items = [x for x in re.split(r"[,;\s]+", text) if x]
        if not items:
            messagebox.showinfo("提示", "请输入 QQ 号或手机号")
            return
        tree.delete(*tree.get_children())
        status.config(text="查询中…")
        root.update_idletasks()
        try:
            c = get_conn()
            cur = c.cursor()
            results = lookup_many(cur, cfg["table"], items, cfg["limit"])
            cur.close()
        except Exception as e:
            conn["c"] = None
            status.config(text="查询失败")
            messagebox.showerror("查询失败", str(e))
            return
        found = 0
        for raw, kind, rows in results:
            label = {"qq": "QQ号", "phone": "手机号", "unknown": "无法识别"}[kind]
            if not rows:
                tree.insert("", "end", values=(raw, label, "", "(无结果)"))
            for qq, phone in rows:
                tree.insert("", "end", values=(raw, label, qq, phone))
                found += 1
        status.config(text="完成：输入 %d 条，命中 %d 条" % (len(items), found))

    def do_file():
        p = filedialog.askopenfilename(
            title="选择号码文件（一行一个）",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("读取失败", str(e))
            return
        entry.delete("1.0", "end")
        entry.insert("1.0", content.strip())

    def do_test():
        ok, msg = test_connection(cfg)
        if ok:
            try:
                c = connect(cfg)
                cur = c.cursor()
                info = table_info(cur, cfg["table"], cfg["mysql"]["database"])
                cur.close()
                c.close()
                if info:
                    msg += "\n数据量: %s 行\n数据 %.2f GB / 索引 %.2f GB" % (
                        format(info["rows"], ","), info["data_gb"], info["idx_gb"])
            except Exception:
                pass
            messagebox.showinfo("连接测试", msg)
        else:
            messagebox.showerror("连接测试", msg)

    def do_export():
        rows = [tree.item(i, "values") for i in tree.get_children()]
        if not rows:
            messagebox.showinfo("提示", "没有可导出的结果")
            return
        p = filedialog.asksaveasfilename(
            title="导出结果", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not p:
            return
        try:
            import csv
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["输入", "类型", "QQ", "手机号"])
                w.writerows(rows)
            messagebox.showinfo("完成", "已导出到\n%s" % p)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    ttk.Button(btns, text="查询 (Enter)", command=do_query).pack(side="left", padx=4)
    ttk.Button(btns, text="从文件导入", command=do_file).pack(side="left", padx=4)
    ttk.Button(btns, text="导出 CSV", command=do_export).pack(side="left", padx=4)
    ttk.Button(btns, text="测试连接", command=do_test).pack(side="left", padx=4)

    entry.bind("<Control-Return>", lambda e: (do_query(), "break")[1])

    do_test()
    root.mainloop()
    return 0


# ---------------------------------------------------------------- 命令行
def run_cli(cfg, items, as_json=False):
    try:
        c = connect(cfg)
        cur = c.cursor()
        results = lookup_many(cur, cfg["table"], items, cfg["limit"])
        cur.close()
        c.close()
    except Exception as e:
        print("查询失败: %s" % e, file=sys.stderr)
        return 2

    if as_json:
        out = []
        for raw, kind, rows in results:
            out.append({
                "input": raw, "type": kind,
                "results": [{"qq": q, "phone": p} for q, p in rows],
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    for raw, kind, rows in results:
        label = {"qq": "QQ号", "phone": "手机号", "unknown": "无法识别"}[kind]
        print("\n[%s] %s" % (label, raw))
        if kind == "unknown":
            print("  (请输入 5-12 位 QQ 号，或 1 开头的 11 位手机号)")
        else:
            print(fmt_rows(rows))
    return 0


def main():
    ap = argparse.ArgumentParser(description="QQ / 手机号 互查")
    ap.add_argument("numbers", nargs="*", help="QQ 号或手机号，可多个")
    ap.add_argument("--gui", action="store_true", help="打开图形界面")
    ap.add_argument("--file", help="批量查询：一行一个号码")
    ap.add_argument("--config", help="指定配置文件（默认同目录 qq_lookup.json）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（适合程序调用）")
    ap.add_argument("--init-config", action="store_true", help="生成默认配置文件后退出")
    args = ap.parse_args()

    cfg, cfg_file = load_config(args.config)

    if args.init_config:
        save_config(cfg, cfg_file)
        print("已生成配置: %s" % cfg_file)
        return 0

    if not cfg_file.exists():
        save_config(cfg, cfg_file)
        print("提示: 已生成默认配置 %s，请按需修改 MySQL 连接信息。" % cfg_file)

    if args.gui:
        return run_gui(cfg, cfg_file)

    items = list(args.numbers)
    if args.file:
        p = Path(args.file).expanduser()
        if not p.exists():
            print("文件不存在: %s" % p, file=sys.stderr)
            return 1
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                for x in re.split(r"[,;\s]+", line.strip()):
                    if x:
                        items.append(x)

    if not items:
        return interactive(cfg, cfg_file)

    return run_cli(cfg, items, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
