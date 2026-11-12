# 时序报告工具改进记录：`path` 可读性与过滤能力

本文记录 `vtdb` 0.1.0 阶段暴露并已实现的四项改进，以及它们当前的接口行为。
原始评估过程不再保留；命令、选项、输出格式的权威说明见
[命令行接口设计](cli.md)。

## 1. 背景

2026-11-05 用 `vtdb` 分析 `build/vivado_flow/part0/post_route_timing.rpt`
（22,834 条路径，WNS −0.319 ns）时，四个问题影响排障效率，现已全部解决：

1. `path` 不显示 launch/capture 时钟和 `Clock Domain Crossing` 说明；
2. `path` 整行打印完整 pin 名，单行最长 205 字符；
3. `stats --by module` 只能聚合全量路径，无法按时钟组再过滤；
4. `top --group` 只接受精确组名，输入片段得到空结果且无提示。

## 2. 已实现的行为

### 2.1 `path` 显示 clocks 与 CDC

- 摘要新增 `clocks` 行，格式为 `from_clock -> to_clock`；任一侧缺失时显示 `-`。
- 有 `Clock Domain Crossing` 说明时，在 `exception` 之后输出 `cdc` 行，按 76 列折行，
  续行缩进 16 列。
- `--format json` 不变，仍输出完整字段。
- 实现：`commands/path.py` 的 `path_summary_lines()` 和 `_wrap_labeled()`。

### 2.2 `top --group` 子串匹配

- `--group NAME` 默认使用 `path_group LIKE %NAME%`，支持组名片段。
- `--exact` 恢复精确匹配（`path_group = ?`），供脚本固定行为。
- 空结果时 stderr 附带过滤条件，例如 `no rows (slack<0 AND group~'shell_aclk')`。
- SQLite `LIKE` 对 ASCII 默认不区分大小写；需要严格区分时用 `sql` 命令。
- 实现：`queries.top_sql()`、`commands/top.py` 的 `_filter_hint()`、`commands/args.py`。

### 2.3 `path` 长名字省略

- `source` / `destination` 默认省略中间层级，保留前 2 段和末尾 3 段，中间用 `…`。
- 按标签和站点扣除宽度预算，默认 source/destination 行不超过 80 列。
- `--wide` 输出完整 pin 名；JSON 始终输出完整名字。
- 实现：`commands/path.py` 的 `shorten_name()` 和 `_endpoint_line()`。

### 2.4 `stats --by module` 过滤

- 新增 `--fail-only`、`--group TEXT`、`--prefix TEXT` 三个选项。
- 过滤先于聚合：`PATHS` / `FAIL` / `WNS` / `TNS(SAMPLE)` 都是过滤后集合上的值。
- `--min-paths` 在过滤后生效；三个条件之间是 AND。
- 这三个选项只对 `--by module` 生效，与其他 `--by` 组合返回退出码 2。
- 实现：`queries.stats_module_sql()`、`commands/stats.py`、`commands/args.py`。

## 3. 兼容性与影响面

- 2.2 项（`top --group` 子串）是唯一的默认行为变化；脚本可用 `--exact` 固定旧行为。
- 四项都不涉及 `schema.sql`、`SCHEMA_VERSION` 或解析器，旧库不需要重新导入。
- `--format json` 的键名没有变化，`path` 仍输出完整字段。

## 4. 未纳入（口径未定）

以下项仍没有稳定口径，暂不实现：

- 违例端点数去重（报告按端点计数，工具按路径计数）；
- `stats --tns-share`（与 2.4 的过滤语义需要一起设计）；
- `import` 摘要增强（WNS/TNS）；
- `path` 独立 `module` 行（2.3 的省略显示已能看出模块）。

## 5. 验证

```text
python -m pytest tests -q    # 73 passed
python -m compileall -q src
make zipapp                  # dist/vtdb.pyz --version 正常
```

实现记录见 git 提交历史；相关测试见 `tests/test_commands.py`、`tests/test_cli.py`、
`tests/test_cli_contract.py`。

## 6. 相关文档

- [命令行接口设计](cli.md)：命令、选项、输出格式与退出码；
- [使用指南](../guides/usage.md)：排障配方与常见问题；
- [数据模型](database.md)：字段与派生字段的权威说明；
- `AGENTS.md`：代码地图、约束与当前状态。
