# cliproxy 对话日志监控

## 这个目录是干嘛的
把 cliproxy 的原始请求/响应日志，精简成“用户提问 + AI 输出 + 图片附件”，存成 SQLite 数据库、JSONL 和图片文件，方便以后搜索或喂给 AI。

- `cliproxy_monitor.py`：监控脚本，Windows / Linux 都可用（需要 Python 3.8+，SQLite 自带 FTS5 时搜索可用）。
- `config.json`：配置文件，修改日志目录 / 数据目录 / 轮询间隔。
- `data/`：运行产生的数据。
  - `chats.db`：SQLite 数据库，含 `turns`、`attachments`、`turns_fts` 全文搜索表。
  - `chats.jsonl`：每行一轮对话，可直接给 AI 读取。
  - `attachments/`：图片/文件附件，按月分目录，重复附件只存一份。

## 配置说明
编辑 `config.json`：

```json
{
  "log_dir": "C:/Users/Administrator/.cli-proxy-api/logs",
  "data_dir": "D:/tmp/anjian/pj/st/tmp/miansha/cpaproxy/data",
  "interval": 20
}
```

- `log_dir`：cliproxy 的请求日志目录，也就是本脚本监控的目录。
- `data_dir`：本脚本数据输出目录。
- `interval`：常驻监控每次检查日志的时间间隔（秒）。
- Windows 里路径用 `/` 或 `\\` 都可以；Linux 用 `/`。
- 命令行参数 `--logs-dir`、`--data-dir`、`--interval` 优先级高于配置文件。

## 使用
```bash
# 常驻监控：每 20 秒自动处理新增日志
python3 cliproxy_monitor.py

# 手动把现有日志全部处理一遍后退出
python3 cliproxy_monitor.py --once

# 自定义间隔（秒）
python3 cliproxy_monitor.py --interval 60

# 指定日志目录 / 数据目录
python3 cliproxy_monitor.py --logs-dir /path/to/logs --data-dir /path/to/data
```

Windows 用 `python cliproxy_monitor.py`，Linux 用 `python3 cliproxy_monitor.py`。

## 怎么改 cliproxy 自己的日志位置
cliproxy 的日志目录通常由软件自身配置决定：
- Windows 默认：`C:\Users\Administrator\.cli-proxy-api\logs`
- Linux 默认一般是：`$HOME/.cli-proxy-api/logs`

如果改了 cliproxy 日志目录，就把新路径同步填到本目录的 `config.json` 的 `log_dir`。

## 查询示例
```sql
-- 按关键词搜索用户提问或 AI 输出
SELECT user_text, assistant_text, ts
FROM turns
WHERE turns_fts MATCH '关键词'
LIMIT 20;

-- 查看某轮对话的附件
SELECT name, path, size, mime
FROM attachments
WHERE turn_id = 1;
```
直接看 SQLite 可以用 DBeaver、Navicat、DB Browser for SQLite 等工具打开 `data/chats.db`。

## 说明
- `status = error` 表示原日志里就是上游失败（额度不足 / 503 等），不是脚本报错。
- `status = empty` 表示原日志里没有可提取的用户/回答内容。
- 旧数据迁移后，再次扫描会按日志文件名+时间判断，不会重复入库；同一轮被重写时会更新并删除旧附件记录。

---

## Linux 装成系统服务（systemd）

目录里已经放好三个文件：

- `cpaproxy-monitor.service`：systemd 服务定义
- `install.sh`：一键安装 / 卸载 / 查看状态
- `config.linux.json`：Linux 用的配置模板（首次安装会复制成 `config.json`）

把整个 `cpaproxy` 目录传到服务器 `/root/cpaproxy`（你已用 SFTP 上传好），然后：

```bash
cd /root/cpaproxy
chmod +x install.sh
sudo bash install.sh
```

安装完成后，服务已设为开机自启并正在运行。

### 手动启停

```bash
sudo systemctl start   cpaproxy-monitor   # 启动
sudo systemctl stop    cpaproxy-monitor   # 停止
sudo systemctl restart cpaproxy-monitor   # 重启（改完 config.json 后执行）
sudo systemctl status  cpaproxy-monitor   # 查看状态
sudo systemctl enable  cpaproxy-monitor   # 开机自启
sudo systemctl disable cpaproxy-monitor   # 取消开机自启
```

### 看日志 / 排错

```bash
journalctl -u cpaproxy-monitor -f          # 实时跟踪
journalctl -u cpaproxy-monitor -n 100      # 最近 100 行
journalctl -u cpaproxy-monitor --since today
```

### 改配置后

```bash
sudo nano /root/cpaproxy/config.json    # 改 log_dir / data_dir / interval
sudo systemctl restart cpaproxy-monitor
```

### 卸载

```bash
cd /root/cpaproxy
sudo bash install.sh uninstall      # 只删服务，数据目录保留
```

### 说明

- 服务用 `/usr/bin/python3` 运行；如果服务器 python3 不在此路径，`install.sh` 会自动用 `command -v python3` 找到实际路径。
- 服务日志走 journald，不再单独写文件；脚本自己的数据仍在 `data_dir`。
- 脚本退出会自动重启（`Restart=always`，间隔 10 秒）。
- 确认 `/root/.cli-proxy-api/logs` 存在且 cliproxy 在往里面写日志；不存在时脚本会正常空转，不会报错退出。

---

## Windows 启动项小工具 set_run.ps1

`set_run.ps1` 是一个可在 Windows 上操作的启动项小工具，专门处理当前用户登录自启键：

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

它的主要用途：**在没有远端目标机 shell 的情况下，直接生成一条可以粘贴到对方机器上执行的单行命令**，或者把某个 exe 写进本机 Run 键实现登录自启。

### 命令一览

| 命令 | 作用 |
| --- | --- |
| `copy [名字]` | **默认命令**。生成单行命令并复制到剪贴板，**不检查文件是否存在**，可以对任意名字凭空生成 |
| `write [名字]` | 真正写入 Run 键（登录自启），要求目标文件真实存在，写完会回读校验 |
| `delete [名字]` | 删除 Run 项，会二次确认 |
| `list` | 列出 Cache 目录里的 exe（**含隐藏文件**）和 Run 键里现有的项 |
| `help` | 显示内置帮助 |

### 参数

- `-User <用户名>`：指定 `C:\Users\<用户名>`，用于操作**目标机不是本机**的情况。
- `-Path <目录>`：直接指定 exe 所在目录，跳过用户名询问。

只给 exe 名字（如 `LocaleMgr`）时，会交互询问用户名，**直接回车 = 当前登录用户**。也可以输入序号（`2`）或完整路径。

### 用户名与大小写

- 本机真实存在的目录，会自动还原成磁盘上的**真实大小写**；
- 本机不存在的目录（多为操作远端目标机），按你输入的**大小写原样保留**；
- Windows 文件系统本身不区分大小写，`cache` 与 `Cache` 是同一个目录。

### 典型用法

```powershell
# 交互选择（等价 copy），复制到剪贴板
.\set_run.ps1

# 凭空生成 Cache\SvcMonitor.exe 的启动命令（即使文件不存在）
.\set_run.ps1 copy SvcMonitor

# 复制真实文件的启动命令
.\set_run.ps1 copy LocaleMgr

# 指定目标机的用户名
.\set_run.ps1 copy LocaleMgr -User WDKRemoteUser

# 直接指定目录
.\set_run.ps1 copy LocaleMgr -Path "C:\Users\X\AppData\Local\Cache"

# 真正写入本机 Run 键（要求文件存在）
.\set_run.ps1 write LocaleMgr

# 删除 Run 项
.\set_run.ps1 delete LocaleMgr

# 查看候选与现有项
.\set_run.ps1 list
```

### 生成的命令长什么样

`copy` 复制到剪贴板的内容是形如下面的一行（可直接在目标机 CMD/PowerShell 里粘贴执行）：

```powershell
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name LocaleMgr -Value '"C:\Users\WDKRemoteUser\AppData\Local\Cache\LocaleMgr.exe"'
```

### 注意事项

- 候选列表用 `-Force` 枚举，**包含隐藏文件**（Hidden/System 也能看到）。
- `write` 会校验：路径长度、是否含换行、路径是否存在，写完回读比对。
- 脚本文件请保持 **UTF-8 with BOM** 保存，否则 Windows PowerShell 5.1 按 GBK 读取会报 `Missing closing '}'`。
