# AGENTS

本文件是 agent 进入 `vtdb` 仓库后的启动说明，目标是让新接手的 agent 在最短时间内
知道：这是什么、先读什么、代码在哪、哪些约束不能破。

细节不在这里重复，本文只负责"指路"和"红线"。

## 1. 项目是什么

`vtdb` 把**一份** Vivado `report_timing_summary` 文本报告转换成**一个** SQLite 数据库，
用于按属性、拓扑、模块查询路径。它不管理多份报告，不做 diff，也不替代 Vivado 时序引擎。

- 当前版本：`0.1.0`，接口在 1.0 前可能调整。
- 上游仓库：<https://github.com/euphgh/vtdb>
- 许可证：MIT。

设计上有意保持小：六个命令（`import` / `top` / `path` / `stats` / `summary` / `sql`）、
纯 Python 标准库、无服务、无配置文件。

## 2. 先读什么

按这个顺序读，不要一上来翻代码：

1. [`README.md`](README.md)：定位、安装、三条命令的快速印象。
2. [`docs/index.md`](docs/index.md)：文档地图和推荐阅读顺序。
3. [`docs/guides/usage.md`](docs/guides/usage.md)：用户实际怎么用、怎么排障。
4. [`docs/design/database.md`](docs/design/database.md)：报告结构、解析规则、SQLite schema。
5. [`docs/design/cli.md`](docs/design/cli.md)：命令、选项、输出格式、退出码（接口契约）。
6. [GitHub Issues](https://github.com/euphgh/vtdb/issues)：后续功能规划与讨论。

## 3. 代码地图

```text
src/vtdb/
├── parser/           # 报告解析包（最复杂，改动风险最高）
│   ├── core.py       # ReportParser：读循环、章节分发
│   ├── lexer.py      # 正则与纯行拆分（split_prefix 等）
│   ├── sections.py   # Timer/Design/Clock/Intra/Inter/Other/check_timing
│   ├── details.py    # clock pair、路径调度、PW 表
│   ├── path_block.py # 单条路径状态机
│   ├── path_fields.py# 路径头字段
│   ├── path_writer.py# 派生字段、写库、不变量校验
│   ├── segments.py   # 明细行拆分、pending 合并、段统计
│   ├── model.py      # ParseResult / PathStats / CheckTimingRow
│   └── report.py     # report 行构造
├── commands/         # 六个子命令与 CLI 公共设施
│   ├── args.py       # argparse 命令树
│   ├── common.py     # 退出码、找库、取数、格式化
│   ├── columns.py    # 输出列定义
│   ├── import_cmd.py # import
│   ├── top.py        # top
│   ├── path.py       # path 与摘要渲染
│   ├── stats.py      # stats
│   ├── summary.py    # summary
│   └── sql.py        # sql
├── cli.py            # main + 异常映射（薄入口）
├── db.py             # SQLite 写入、schema 加载、resource/hier 字典化
├── schema.sql        # 表结构的唯一权威来源
├── queries.py        # top / stats / path 的 SQL 构造
└── output.py         # table / json / csv / tsv / md 渲染
tests/test_cli.py          # 端到端 CLI 测试
tests/test_cli_contract.py # JSON / 退出码 / help 契约
tests/test_parser.py       # 解析器纯函数与路径级测试
tests/test_commands.py     # 命令渲染、名称缩短
tests/data/mini_timing.rpt # 裁剪过的真实格式夹具（折行、PW、汇总表）
tests/data/edge_timing.rpt # hidden / CDC / exception / 折行夹具
```

关键入口与职责：

| 位置 | 职责 |
| --- | --- |
| `ReportParser.run()` | 单遍流式解析整份报告 |
| `ReportParser._line()` | 章节识别与分发 |
| `TopSections.feed()` | Timer / Design / Clock / 汇总表 / check_timing |
| `DetailsParser.feed()` | clock pair、路径模式切换、PW 表 |
| `PathBlock.feed()` | 单条路径的头字段与明细表 |
| `segments.split_segment()` | 明细行拆分 + 折行 pending |
| `path_writer.finish_path()` | 派生字段、层次前缀、写库、一致性检查 |
| `DbWriter` | 批量写入、`resource` / `hier_node` 字典化 |
| `schema_sql()` | 通过 `importlib.resources` 读取内置 schema（兼容 zipapp/单文件二进制） |
| `queries.top_sql()` 等 | 把 CLI 选项翻译成 SQL |
| `commands/*.py` 的 `cmd_*()` | 命令实现 |

数据流：

```text
report.rpt ──ReportParser──> DbWriter ──> SQLite ──queries──> render ──> stdout
```

## 4. 不能破的约束

1. **一报告一库**：不要引入 `report_id`、多报告管理、`ls`、`diff`。需要对比两次运行时，
   用两个 `.db` + `sqlite3 ATTACH`（见 `docs/design/database.md` 第 7 节）。
2. **schema 是契约**：改 `schema.sql` 必须同步 `SCHEMA_VERSION`（`db.py`），并在 PR
   说明"旧库需要重新导入"。当前没有自动迁移。
3. **JSON 字段名稳定**：`--format json` 的键名一旦发布只能追加，不能重命名。
4. **只读 SQL**：`sql` 命令只接受 `SELECT` / `WITH` / `EXPLAIN`，不要放宽。
5. **只用标准库**：新增第三方依赖前必须先征求维护者同意。
6. **Python >= 3.9**：不要使用 3.10+ 语法（如 `match`、`X | Y` 运行时类型）。
7. **每个函数/类都有 Google 风格 docstring**（Args / Returns / Raises）。这是硬要求。

## 5. 解析器必须记住的坑

- `arrival time` 在报告里是**负值**打印，入库时取反，否则 `required - arrival != slack`。
- 明细表列位置：Location 从第 4 列开始，Delay type 固定在第 25 列；站点名过长会右移，
  用 `split_prefix()` 检测而不是硬编码。
- 超宽行会折行：Location + Delay type 一行，数值在下一行，用 `pending` 合并。
- 路径块用**两种分隔线**切分：双列分隔线划分 launch / data / capture，单列分隔线包围
  `required/arrival/slack`；不要用 `(clock ...)` 判断块边界。
- `<hidden>` 是合法名字，不能当作身份标识，也不能参与路径匹配。
- `-max_paths` 是采样：明细求和不等于报告 TNS，汇总表才是权威口径。
- 带 `Timing Exception` 的路径，`Data Path Delay` 不等于逐段增量之和，校验时要排除。

## 6. 修改流程

1. **先复现**：用 `tests/data/mini_timing.rpt` 或手边真实报告导入一个库，确认现象。
2. **再改代码**：优先改 `queries.py` / `commands/`；只有格式确实不同才动 `parser/`。
3. **补测试**：每个行为变化至少加一个测试；解析器改动要覆盖 `tests/data/mini_timing.rpt`
   的折行/PW 与 `tests/data/edge_timing.rpt` 的隐藏名、CDC、exception 路径。
4. **跑验证**：`uv run pytest tests -q` 或 `python -m pytest tests -q`。
5. **同步文档**：命令/选项变化改 `docs/design/cli.md`；数据结构变化改
   `docs/design/database.md`；用户可见行为改 `docs/guides/usage.md`。
6. **大文件不要进内存**：200 MB 的报告必须流式处理，测试用迷你夹具。

## 7. 当前状态

以下四项功能已在 2026-11-09 实现（实现记录见 git 历史）：

1. `path` 输出 launch/capture 时钟与 CDC 说明；
2. `top --group` 子串匹配，`--exact` 恢复精确匹配，空结果给出过滤提示；
3. `path` 长名字默认省略中间层级，`--wide` 显示完整名字；
4. `stats --by module` 支持 `--fail-only` / `--group` / `--prefix`。

后续功能规划与讨论转移到 [GitHub Issues](https://github.com/euphgh/vtdb/issues)。

## 8. 交付要求

一次合格的改动至少说明：

- 改了哪些行为、对应哪个文档已更新；
- 实际跑了哪些测试、结果如何；
- 哪些是验证过的事实，哪些是推断；
- 如果改了 schema 或 CLI 契约，是否破坏兼容、旧库是否需要重导。

## 9. 与 REMUS 的关系

`vtdb` 起源于 REMUS 仓库的 `tools/vtdb/`，现已迁出到独立仓库，REMUS 侧不再保留副本。
**只维护本仓库**；需要查迁移前的历史时，使用 REMUS 迁出前的提交。
