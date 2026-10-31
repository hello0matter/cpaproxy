#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
cliproxy 对话日志监控
=====================
只提取 用户提问 与 AI 输出，图片/文件附件保存到磁盘，其余都存档为结构化数据。

数据目录（可由 config.json 或 --data-dir 覆盖）：
  chats.db        SQLite 数据库：turns（每轮对话）+ turns_fts（全文检索）+ attachments 表
  attachments/    图片/文件附件本体（按内容哈希命名，重复附件只存一份）
  chats.jsonl     追加式 JSONL，每行一轮对话，适合直接丢给 AI / 做检索

配置文件 config.json（放在脚本同目录）：
  { "log_dir": "日志目录", "data_dir": "数据目录", "interval": 20 }
  命令行参数优先级高于配置文件；Windows / Linux 均适用。

用法:
  python cliproxy_monitor.py               # 常驻监控，每 20 秒检查新增日志
  python cliproxy_monitor.py --once        # 立即处理现有全部日志后退出
  python cliproxy_monitor.py --file <log>  # 只处理指定日志文件（自测用）
  python cliproxy_monitor.py --interval 60 # 自定义轮询间隔（秒）
  python cliproxy_monitor.py --data-dir <dir>  # 自定义数据目录
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

def _platform_defaults():
    home = Path.home()
    return {
        "log_dir": home / ".cli-proxy-api" / "logs",
        "data_dir": home / "cliproxy_monitor",
        "interval": 20,
    }

def load_config():
    """读取脚本同目录的 config.json；缺失或出错时用平台默认值。"""
    cfg = _platform_defaults()
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k in ("log_dir", "data_dir", "interval"):
                    if user.get(k) not in (None, ""):
                        cfg[k] = user[k]
        except Exception as e:
            print("警告: 读取 %s 失败(%s)，使用默认配置" % (CONFIG_PATH, e))
    return cfg

# 系统注入内容：不算“用户提问”，提取时剔除
SYSTEM_BLOCK_RE = re.compile(
    r"<skills_instructions>.*?</skills_instructions>"
    r"|<permissions instructions>.*?</permissions instructions>"
    r"|<environment_context>.*?</environment_context>"
    r"|<system_reminder>.*?</system_reminder>"
    r"|<image name=\[Image[^>]*>.*?</image>"
    r"|<file name=\[File[^>]*>.*?</file>"
    r"|# AGENTS\.md instructions\s*<INSTRUCTIONS>.*?</INSTRUCTIONS>"
    r"|<INSTRUCTIONS>.*?</INSTRUCTIONS>",
    re.S,
)

# data URI:  data:image/png;base64,xxxx
DATA_URI_RE = re.compile(r"data:([a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=]+)")

MIME_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
    "image/svg+xml": "svg", "image/bmp": "bmp", "image/x-icon": "ico", "image/tiff": "tiff",
    "image/avif": "avif", "text/plain": "txt", "text/html": "html", "text/markdown": "md",
    "text/csv": "csv", "application/pdf": "pdf", "application/json": "json",
    "application/zip": "zip", "application/x-7z-compressed": "7z", "application/gzip": "gz",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "message/rfc822": "eml", "application/x-mhtml": "mhtml", "text/x-mhtml": "mhtml",
    "application/octet-stream": "bin",
}


def ext_for_mime(mime):
    m = (mime or "").split(";")[0].strip().lower()
    return MIME_EXT.get(m, "bin")


class Monitor:
    def __init__(self, log_dir, data_dir):
        self.log_dir = Path(log_dir)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.attach_dir = self.data_dir / "attachments"
        self.attach_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "chats.db"
        self.jsonl_path = self.data_dir / "chats.jsonl"
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    # ---------- schema ----------
    def _init_schema(self):
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS turns(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              log_file TEXT NOT NULL,
              turn_id TEXT,
              session_id TEXT,
              thread_id TEXT,
              request_kind TEXT,
              thread_source TEXT,
              status TEXT DEFAULT 'ok',
              error TEXT,
              model TEXT,
              ts TEXT,
              user_text TEXT DEFAULT '',
              assistant_text TEXT DEFAULT '',
              attachments TEXT DEFAULT '[]',
              created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_turn_file ON turns(log_file, turn_id);
            CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);
            CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);

            CREATE TABLE IF NOT EXISTS processed_files(
              path TEXT PRIMARY KEY,
              size INTEGER DEFAULT 0,
              mtime TEXT,
              status TEXT DEFAULT 'ok',
              processed_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS text_hashes(
              h TEXT PRIMARY KEY,
              first_seen TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS attachments(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              turn_id INTEGER,
              log_file TEXT,
              kind TEXT,
              name TEXT,
              path TEXT,
              mime TEXT,
              size INTEGER,
              sha1 TEXT,
              created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_attach_turn ON attachments(turn_id);
            CREATE INDEX IF NOT EXISTS idx_attach_sha1 ON attachments(sha1);

            CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
              user_text, assistant_text,
              content='turns', content_rowid='id', tokenize='trigram'
            );
            CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
              INSERT INTO turns_fts(rowid, user_text, assistant_text)
              VALUES (new.id, new.user_text, new.assistant_text);
            END;
            """
        )
        self.conn.commit()

    # ---------- helpers ----------
    def is_processed(self, path, size, mtime):
        row = self.conn.execute(
            "SELECT size, mtime FROM processed_files WHERE path=?", (str(path),)
        ).fetchone()
        if row is None:
            return False
        return str(row[0]) == str(size) and str(row[1]) == str(mtime)

    def mark_processed(self, path, size, mtime, status="ok"):
        self.conn.execute(
            "INSERT OR REPLACE INTO processed_files(path,size,mtime,status,processed_at) VALUES(?,?,?,?,datetime('now','localtime'))",
            (str(path), size, mtime, status),
        )
        self.conn.commit()

    def is_new_text(self, text):
        h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        if self.conn.execute("SELECT 1 FROM text_hashes WHERE h=?", (h,)).fetchone():
            return False, h
        self.conn.execute("INSERT INTO text_hashes(h) VALUES(?)", (h,))
        return True, h

    def _clean_user_text(self, text):
        text = SYSTEM_BLOCK_RE.sub("\n", text or "")
        kept = []
        for ln in text.split("\n"):
            st = ln.strip()
            if (st.startswith("<image name=") or st == "</image>"
                    or st.startswith("<file name=") or st == "</file>"):
                continue
            kept.append(ln)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

    # ---------- attachment ----------
    def _save_attachment(self, raw_part, watch_path):
        """从 content part 里提取 data URI 并保存为文件。返回 (attachment 描述列表)"""
        out = []
        part_json = json.dumps(raw_part, ensure_ascii=False)
        seen = set()
        for m in DATA_URI_RE.finditer(part_json):
            mime, b64 = m.group(1), m.group(2)
            try:
                data = base64.b64decode(b64, validate=True)
            except Exception:
                continue
            sha = hashlib.sha1(data).hexdigest()
            if sha in seen:
                continue
            seen.add(sha)
            ext = ext_for_mime(mime)
            sub = datetime.now().strftime("%Y-%m")
            folder = self.attach_dir / sub
            folder.mkdir(parents=True, exist_ok=True)
            fname = "%s.%s" % (sha[:16], ext)
            dest = folder / fname
            if not dest.exists():
                dest.write_bytes(data)
            kind = "image" if mime.startswith("image/") else "file"
            name = None
            if isinstance(raw_part, dict):
                name = raw_part.get("filename") or raw_part.get("name")
            out.append(
                {
                    "kind": kind,
                    "name": name or fname,
                    "path": str(dest.relative_to(self.data_dir)),
                    "abs_path": str(dest),
                    "mime": mime,
                    "size": len(data),
                    "sha1": sha,
                }
            )
        return out

    # ---------- parse one log file ----------
    def parse_file(self, path):
        p = Path(path)
        stat = p.stat()
        size, mtime = stat.st_size, str(stat.st_mtime)
        if self.is_processed(p, size, mtime):
            return None
        try:
            return self._parse_file(p)
        except Exception as e:
            self.mark_processed(p, size, mtime, "parse_error")
            raise
        finally:
            if not self.is_processed(p, size, mtime):
                self.mark_processed(p, size, mtime)

    def _parse_file(self, p):
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        # ---- 元信息 ----
        ts = None
        metadata = {}
        body = None
        api_error = ""
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("Timestamp:") and ts is None:
                ts = s.split("Timestamp:", 1)[1].strip()
            elif s.startswith("X-Codex-Turn-Metadata:"):
                md = s.split(":", 1)[1].strip()
                try:
                    metadata = json.loads(md)
                except Exception:
                    pass
            elif s == "=== REQUEST BODY ===" and body is None:
                for j in range(i + 1, len(lines)):
                    nxt = lines[j].strip()
                    if nxt and not nxt.startswith("==="):
                        body = nxt
                        break
                    if nxt.startswith("==="):
                        break
            elif s.startswith("auth_unavailable:") or "insufficient_user_quota" in s:
                api_error = s[:2000]
            elif s.startswith("HTTP Status:"):
                api_error = api_error or (s + " " + (lines[i + 1].strip() if i + 1 < len(lines) else ""))[:2000]

        # ---- 请求 JSON ----
        model = None
        input_items = []
        if body:
            try:
                req = json.loads(body)
                model = req.get("model")
                input_items = req.get("input")
                if input_items is None:
                    input_items = req.get("messages") or []
            except Exception:
                pass

        # ---- 用户提问与附件 ----
        user_parts = []
        attachments = []
        for item in input_items:
            if not isinstance(item, dict):
                continue
            if item.get("role") != "user":
                continue
            content = item.get("content")
            if isinstance(content, list):
                parts = content
            else:
                parts = [content] if content else []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "input_text":
                    user_parts.append(part.get("text", ""))
                else:
                    attachments.extend(self._save_attachment(part, p.name))

        user_list = []
        for up in user_parts:
            cleaned = self._clean_user_text(up)
            if not cleaned:
                continue
            for chunk in cleaned.split("\n"):
                chunk = chunk.strip()
                if chunk:
                    user_list.append(chunk)
        # 同一请求里重复出现的内容只保留第一次，跨轮重复提问仍保留
        user_new = []
        seen_u = set()
        for chunk in user_list:
            h = hashlib.sha1(chunk.encode("utf-8", "replace")).hexdigest()
            if h in seen_u:
                continue
            seen_u.add(h)
            user_new.append(chunk)
        user_text = "\n".join(user_new)

        # ---- AI 输出（只取最后一次完成的 response） ----
        assistant_text = ""
        completed = None
        deltas = []
        for ln in lines:
            if ln.startswith("data: "):
                try:
                    obj = json.loads(ln[6:])
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                t = obj.get("type")
                if t == "response.completed":
                    completed = obj
                elif t == "response.output_text.delta":
                    deltas.append(str(obj.get("delta", "")))
        if completed is not None:
            resp = completed.get("response") or completed
            texts = []
            for item in resp.get("output", []):
                if isinstance(item, dict) and item.get("type") == "message":
                    for c in item.get("content", []):
                        if isinstance(c, dict) and c.get("type") == "output_text":
                            texts.append(str(c.get("text", "")))
            assistant_text = "\n".join(texts).strip()
        if not assistant_text and deltas:
            assistant_text = "".join(deltas).strip()

        # ---- 状态 ----
        status = "ok"
        error = ""
        if api_error:
            status = "error"
            error = api_error
        if completed is None and not assistant_text and status == "ok":
            status = "empty"
        resp_m = re.search(r"=== RESPONSE ===\s*\nStatus: (\d{3})", text)
        if resp_m and resp_m.group(1).startswith(("4", "5")):
            status = "error"
            error = error or ("HTTP Status: " + resp_m.group(1))

        turn_id = metadata.get("turn_id") or p.stem
        session_id = metadata.get("session_id") or ""
        thread_id = metadata.get("thread_id") or ""
        request_kind = metadata.get("request_kind") or ""
        thread_source = metadata.get("thread_source") or ""

        return {
            "log_file": p.name,
            "turn_id": str(turn_id),
            "session_id": str(session_id),
            "thread_id": str(thread_id),
            "request_kind": str(request_kind),
            "thread_source": str(thread_source),
            "status": status,
            "error": (error or "")[:4000],
            "model": model or "",
            "ts": ts or "",
            "user_text": user_text,
            "assistant_text": assistant_text,
            "attachments": attachments,
        }

    # ---------- store ----------
    def store(self, rec):
        try:
            cur = self.conn.execute(
                "INSERT INTO turns(log_file,turn_id,session_id,thread_id,request_kind,thread_source,status,error,model,ts,user_text,assistant_text,attachments) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(log_file,turn_id) DO UPDATE SET "
                "session_id=excluded.session_id, thread_id=excluded.thread_id, "
                "request_kind=excluded.request_kind, thread_source=excluded.thread_source, "
                "status=excluded.status, error=excluded.error, model=excluded.model, "
                "ts=excluded.ts, user_text=excluded.user_text, assistant_text=excluded.assistant_text, "
                "attachments=excluded.attachments",
                (
                    rec["log_file"], rec["turn_id"], rec["session_id"], rec["thread_id"],
                    rec["request_kind"], rec["thread_source"], rec["status"], rec["error"],
                    rec["model"], rec["ts"], rec["user_text"], rec["assistant_text"],
                    json.dumps(rec["attachments"], ensure_ascii=False),
                ),
            )
            self.conn.commit()
            if cur.rowcount == 0:
                return False
            rowid = cur.lastrowid
            # 同一轮重跑时先清附件，避免 append 重复
            self.conn.execute("DELETE FROM attachments WHERE turn_id=?", (rowid,))
            for a in rec["attachments"]:
                self.conn.execute(
                    "INSERT INTO attachments(turn_id,log_file,kind,name,path,mime,size,sha1) VALUES(?,?,?,?,?,?,?,?)",
                    (rowid, rec["log_file"], a["kind"], a["name"], a["path"], a["mime"], a["size"], a["sha1"]),
                )
            self.conn.commit()
            self.conn.execute("DELETE FROM turns_fts WHERE rowid=?", (rowid,))
            self.conn.execute(
                "INSERT INTO turns_fts(rowid,user_text,assistant_text) VALUES(?,?,?)",
                (rowid, rec["user_text"], rec["assistant_text"]),
            )
            self.conn.commit()
            # JSONL 追加
            line = {
                "ts": rec["ts"], "model": rec["model"], "session_id": rec["session_id"],
                "thread_id": rec["thread_id"], "turn_id": rec["turn_id"],
                "request_kind": rec["request_kind"], "thread_source": rec["thread_source"],
                "status": rec["status"], "error": rec["error"] or "",
                "user_text": rec["user_text"], "assistant_text": rec["assistant_text"],
                "attachments": rec["attachments"],
            }
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            return True
        except Exception:
            self.conn.rollback()
            raise

    # ---------- scan ----------
    def candidate_files(self):
        if not self.log_dir.exists():
            return []
        files = list(self.log_dir.glob("v1-responses-*.log"))
        files += list(self.log_dir.glob("v1-chat-completions-*.log"))
        return sorted(set(files), key=lambda f: f.name)

    def run_once(self, progress=True):
        files = self.candidate_files()
        done = 0
        for idx, f in enumerate(files, 1):
            try:
                rec = self.parse_file(f)
            except Exception as e:
                if progress:
                    print("[%d/%d] ERROR %s : %s" % (idx, len(files), f.name, e))
                continue
            if rec is not None:
                try:
                    self.store(rec)
                    done += 1
                    if progress:
                        print(
                            "[%d/%d] %s : %s user=%dchars out=%dchars att=%d"
                            % (idx, len(files), f.name, rec["status"],
                               len(rec["user_text"]), len(rec["assistant_text"]),
                               len(rec["attachments"]))
                        )
                except Exception as e:
                    print("[%d/%d] STORE_ERROR %s : %s" % (idx, len(files), f.name, e))
            elif progress and idx % 100 == 0:
                print("[%d/%d] ... (skip %s)" % (idx, len(files), f.name))
        return done

    def loop(self, interval):
        print("监控启动: %s -> %s (每 %ds 扫描)" % (self.log_dir, self.data_dir, interval))
        first = True
        while True:
            try:
                n = self.run_once(progress=first)
                if first:
                    print("首次扫描完毕，新处理 %d 个文件。进入增量监控..." % n)
                    first = False
            except Exception as e:
                print("扫描异常: %s" % e)
            time.sleep(interval)


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description="clip proxy 对话日志监控")
    ap.add_argument("--once", action="store_true", help="处理现有日志后退出")
    ap.add_argument("--file", help="只处理指定日志文件")
    ap.add_argument("--logs-dir", default=str(cfg["log_dir"]), help="日志目录")
    ap.add_argument("--data-dir", default=str(cfg["data_dir"]), help="数据目录")
    ap.add_argument("--interval", type=int, default=int(cfg.get("interval", 20)), help="轮询间隔(秒)")
    args = ap.parse_args()

    if args.file:
        m = Monitor(args.logs_dir, args.data_dir)
        rec = m.parse_file(Path(args.file))
        if rec is None:
            print("无变化/未识别，跳过")
        else:
            m.store(rec)
            print("已写入: turn=%s status=%s user=%d out=%d att=%d"
                  % (rec["turn_id"], rec["status"], len(rec["user_text"]),
                     len(rec["assistant_text"]), len(rec["attachments"])))
        return

    m = Monitor(args.logs_dir, args.data_dir)
    if args.once:
        n = m.run_once()
        print("完成，本次新处理 %d 个日志文件。数据在 %s" % (n, m.data_dir))
    else:
        m.loop(args.interval)


if __name__ == "__main__":
    main()
