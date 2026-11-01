# QQ / 手机号 互查工具

`qq_phone` 表约 7.14 亿行（数据 45.64 GB / 索引 33.62 GB，16 个 KEY 分区）。

## 文件

- `qq_lookup.py`：查询工具，图形界面 / 交互式 / 命令行三种模式
- `qq_lookup.json`：MySQL 连接配置

## 首次使用

```bash
pip install pymysql
```

编辑 `qq_lookup.json`：

```json
{
  "mysql": {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "test",
    "charset": "utf8mb4",
    "connect_timeout": 10,
    "read_timeout": 120
  },
  "table": "qq_phone",
  "limit": 200
}
```

## 用法

```bash
python qq_lookup.py                  # 交互式（无参数默认）
python qq_lookup.py --gui            # 图形界面
python qq_lookup.py 3439591730       # 按 QQ 号查手机号
python qq_lookup.py 13001000087      # 按手机号查 QQ（自动识别）
python qq_lookup.py 3439591730 13001000087   # 一次查多个
python qq_lookup.py --file nums.txt  # 批量，一行一个
python qq_lookup.py 3439591730 --json  # JSON 输出，方便程序调用
python qq_lookup.py --config other.json  # 指定配置文件
```

Linux 把 `python` 换成 `python3` 即可。

## GUI 说明

- 支持多行输入、逗号/空格分隔
- 从文件导入号码
- 导出结果为 CSV
- 一键测试数据库连接
- `Ctrl+Enter` 查询

## 重要：手机号带 `\r`

原始文件是 Windows CRLF 换行，导入时用了 `LINES TERMINATED BY '\n'`，
导致 **每一行的 phone 末尾都残留一个 `\r`（0x0D）**。

本工具已自动兼容：
- 按手机号查询时会自动补 `\r`，所以直接输入 `13001000087` 就能查到
- 输出结果会自动去掉 `\r`，显示干净

如果绕过本工具直接用 SQL 查，必须写成：

```sql
SELECT qq, TRIM(TRAILING CHAR(13) FROM phone) AS phone
FROM qq_phone
WHERE phone = CONCAT('13001000087', CHAR(13));
```

## 查询性能

| 查询方式 | 执行计划 | 实测耗时 |
|---|---|---|
| `WHERE qq = '...'` | 主键 const，仅命中 1 个分区 | ~60 ms |
| `WHERE phone = '...'` | idx_phone 覆盖索引，不回表 | ~60 ms |

包含进程启动开销，数据库内部耗时远小于此。

## 可选：清洗脏数据

若要彻底去掉 phone 的 `\r`，建议重建表而不是 UPDATE 原地改（7 亿行会让表长时间不可用）：

```sql
CREATE TABLE test.qq_phone_new LIKE test.qq_phone;
INSERT INTO test.qq_phone_new
SELECT qq, TRIM(TRAILING CHAR(13) FROM phone) FROM test.qq_phone;
RENAME TABLE test.qq_phone TO test.qq_phone_bak, test.qq_phone_new TO test.qq_phone;
```

需要额外约 80 GB 空间，耗时为小时级。清洗后本工具仍然兼容（会回退到无 `\r` 的查询分支）。
