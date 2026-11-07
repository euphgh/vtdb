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

设计上有意保持小：五个命令（`import` / `top` / `path` / `stats` / `sql`）、
纯 Python 标准库、无服务、无配置文件。

## 2. 先读什么

按这个顺序读，不要一上来翻代码：

1. [`README.md`](README.md)：定位、安装、三条命令的快速印象。
2. [`docs/index.md`](docs/index.md)：文档地图和推荐阅读顺序。
3. [`docs/guides/usage.md`](docs/guides/usage.md)：用户实际怎么用、怎么排障。
4. [`docs/design/database.md`](docs/design/database.md)：报告结构、解析规则、SQLite schema。
5. [`docs/design/cli.md`](docs/design/cli.md)：命令、选项、输出格式、退出码（接口契约）。
6. [`docs/design/improvements.md`](docs/design/improvements.md)：已知可用性问题与下一步计划。

## 3. 代码地图

```text
src/vtdb/
├── parser.py     # 报告解析状态机（最复杂，改动风险最高）
├── db.py         # SQLite 写入、schema 加载、resource/hier 字典化
├── schema.sql    # 表结构的唯一权威来源
├── queries.py    # top / stats / path 的 SQL 构造
├── cli.py        # argparse、五个子命令、输出组装
└── output.py     # table / json / csv / tsv / md 渲染
tests/test_cli.py         # 端到端 CLI 测试
tests/data/mini_timing.rpt # 裁剪过的真实格式夹具
```

关键入口与职责：

| 位置 | 职责 |
| --- | --- |
| `ReportParser.run()` | 单遍流式解析整份报告 |
| `ReportParser._line()` | 按章节分发每一行 |
| `ReportParser._details_line()` / `_path_line()` | 路径块、clock pair、PW 表 |
| `ReportParser._split_segment()` | 明细行拆分 + 折行 pending |
| `ReportParser._finish_path()` | 派生字段、层次前缀、一致性检查 |
| `DbWriter` | 批量写入、`resource` / `hier_node` 字典化 |
| `schema_sql()` | 通过 `importlib.resources` 读取内置 schema（兼容 zipapp/单文件二进制） |
| `queries.top_sql()` 等 | 把 CLI 选项翻译成 SQL |
| `cmd_import()` / `cmd_top()` / `cmd_path()` / `cmd_stats()` / `cmd_sql()` | 命令实现 |

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
2. **再改代码**：优先改 `queries.py` / `cli.py`；只有格式确实不同才动 `parser.py`。
3. **补测试**：每个行为变化至少加一个 `tests/test_cli.py` 用例；解析器改动要覆盖
   夹具里的折行、隐藏名、CDC、exception 路径。
4. **跑验证**：`uv run pytest tests -q` 或 `python -m pytest tests -q`。
5. **同步文档**：命令/选项变化改 `docs/design/cli.md`；数据结构变化改
   `docs/design/database.md`；用户可见行为改 `docs/guides/usage.md`。
6. **大文件不要进内存**：200 MB 的报告必须流式处理，测试用迷你夹具。

## 7. 当前待办

`docs/design/improvements.md` 里列了四项已评估的改进，按建议顺序实施：

1. `path` 输出增加 launch/capture 时钟与 CDC 说明；
2. `top --group` 改为子串匹配（保留 `--exact`），空结果给出过滤提示；
3. `path` 长名字默认省略中间层级；
4. `stats --by module` 增加 `--fail-only` / `--group` / `--prefix`。

不要顺手扩大范围（端点数去重、TNS share、import 摘要增强都还没定口径）。

## 8. 交付要求

一次合格的改动至少说明：

- 改了哪些行为、对应哪个文档已更新；
- 实际跑了哪些测试、结果如何；
- 哪些是验证过的事实，哪些是推断；
- 如果改了 schema 或 CLI 契约，是否破坏兼容、旧库是否需要重导。

## 9. 与 REMUS 的关系

`vtdb` 起源于 REMUS 仓库的 `tools/vtdb/`，现在已经推到独立仓库，但 REMUS 侧暂未删除。
两边内容应当保持一致；需要改动时以独立仓库为准，再同步回 REMUS，或等 REMUS 侧删除后
只维护独立仓库。迁移步骤见 [`docs/development/extraction.md`](docs/development/extraction.md)。
