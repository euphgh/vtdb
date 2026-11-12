# 使用指南

本文面向使用者：如何安装 `vtdb`、导入第一份报告、用几条命令定位时序问题。
完整的命令与选项表见[命令行接口](../design/cli.md)。

## 1. 安装

```bash
# uv（推荐）
uv tool install .

# 或 pipx
pipx install .

# 或 pip
python -m pip install .
```

不安装也可以直接源码运行：

```bash
PYTHONPATH=src python -m vtdb --help
```

单文件可执行程序（zipapp 或 PyInstaller 二进制）的做法见
[打包与分发](../development/packaging.md)。

## 2. 快速开始

```bash
# 1) 导入一份报告，默认在同目录生成同名 .db
vtdb import build/post_route_timing.rpt

# 2) 看最差路径
vtdb top --fail-only -n 20

# 3) 看某条路径的细节
vtdb path 14438

# 4) 按时钟组、模块聚合
vtdb stats --by group
vtdb stats --by module --level 3

# 5) 看报告级摘要（元数据 / 报告结果 / 导入证据）
vtdb summary

# 6) 需要自定义统计时直接写 SQL
vtdb sql "SELECT COUNT(*) FROM path WHERE slack < 0"
```

导入摘要会给出路径数、明细数、脉宽检查数、一致性检查和库体积：

```text
imported build/post_route_timing.rpt -> build/post_route_timing.db
  design     xiangshan_wrapper (Physopt postRoute)
  paths      22834 (134 violated)
  segments   538364
  pw checks  41644
  checks     ok
  elapsed    8.7s
  size       191.2 MB
```

`checks` 不为 `ok` 时摘要会列出具体行号和原因，同时写入 `import_issue` 表；
加 `--strict` 可让导入在检查失败时返回退出码 1。

## 3. 数据库位置

查询命令按以下顺序找库：

1. `--db PATH`
2. 环境变量 `VTD_DB`
3. 当前目录下恰好一个 `*.db`
4. 否则报错（退出码 4）

`import` 的输出路径由 `-o/--db` 指定，默认是报告同目录的同名 `.db`。
目标已存在时拒绝覆盖，需要 `--force`。

## 4. 输出格式

| 选项 | 用途 |
| --- | --- |
| `--format table` | 默认，给人看 |
| `--format json` | 给脚本；键名与表列名一致 |
| `--format csv` / `tsv` | 给表格软件 |
| `--format md` | 贴进 issue 或文档 |
| `--wide` | 不截断长名字 |
| `-n, --limit` | 行数上限，`0` 为不限 |

数据写 stdout，提示写 stderr，重定向不会被提示污染：

```bash
vtdb top --fail-only --format csv > failing.csv
```

## 5. 排障配方：定位 WNS 来自哪里

以下流程在一次真实分析中验证过（WNS −0.319 ns）：

```bash
# 1) 全局指标：WNS / TNS / 失败端点数
vtdb sql "SELECT wns, tns, tns_failing, tns_total FROM report"

# 2) 哪个时钟组最差
vtdb stats --by group --sort wns

# 3) 最差路径列表
vtdb top --fail-only -n 10

# 4) 看最差路径的细节：requirement、logic/route、源/目的、时钟
vtdb path 14438

# 5) 是哪一坨逻辑：按源端或目的端模块聚合
vtdb stats --by module --level 3 --side dest
```

判断依据：

- `requirement` 远小于时钟周期、逻辑级数很少、路径跨两个不相关时钟 → 优先怀疑约束
  （CDC / 时钟组），而不是 RTL 优化。
- `logic levels` 大、`ROUTE%` 高、组内违例多 → 真实的时序收敛问题，继续看模块聚合。

## 6. 常见问题

**`top --net/--cell/--site` 报 "no detail rows"**

导入时用了 `--keep-detail none`。重新导入并保留明细：

```bash
vtdb import <report> --force --keep-detail all
```

**"multiple databases in current directory"**

当前目录有多个 `.db`。加 `--db` 指定，或设置 `VTD_DB`。

**`path --raw` 提示 raw report not available**

`--raw` 需要原始报告文件仍在导入时记录的路径上。报告被移动过时该功能不可用，
其余查询不受影响。

**违例路径数比报告里的失败端点数多**

一份报告可能对同一个终点给出多条路径。工具按路径计数，报告按终点计数，
两者不必相等。

**`--group` 匹配不到**

`--group` 默认是子串匹配，可以直接用组名片段（如 `shell_aclk`）；需要完整相等时加
`--exact`。空结果会在 stderr 给出过滤条件，例如 `no rows (group~'shell_aclk')`，
便于区分“没有违例”和“名字没对上”。

## 7. 继续阅读

- 命令与选项细节：[命令行接口](../design/cli.md)
- 表结构与字段含义：[数据模型](../design/database.md)
- 后续功能规划与讨论：[GitHub Issues](https://github.com/euphgh/vtdb/issues)
