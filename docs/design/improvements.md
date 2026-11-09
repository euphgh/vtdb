# 时序报告工具改进评估：`path` 可读性与过滤能力

本文评估 `vtdb` 在真实排障中暴露的四个可用性问题，回答三件事：是否真的需要改、改动量多大、
改完之后的接口行为应该是什么。

本文是**评估与实现记录**：第 1–7 节保留原始评估，第 8 节记录落地接口，末尾记录实现结果。
评估对象为 `vtdb` 0.1.0，接口规范见[命令行接口设计](cli.md)。

## 1. 背景

2026-11-05 用 `vtdb` 分析 `build/vivado_flow/part0/post_route_timing.rpt`（22,834 条路径，
WNS −0.319 ns）时，四个问题直接影响了排障效率：

1. `vtdb path` 不显示 launch/capture 时钟和 `Clock Domain Crossing` 说明，
   而这次判断"WNS 是跨域约束问题"恰恰依赖这两项；
2. `vtdb path` 把完整 pin 名整行打印，单行最长 205 字符，终端需要横向滚动；
3. `vtdb stats --by module` 只能聚合全量路径，无法回答"某个时钟组里的违例按模块怎么分布"；
4. `vtdb top --group` 只接受精确组名，输入 `shell_aclk` 得到空结果且退出码为 0，
   没有提示"是匹配方式的问题"。

## 2. 评估方法

- 数据集：`build/vtdb/part0.db`（由上述报告导入，22,834 路径 / 538,364 明细行）。
- 复现命令见第 7 节。
- 结论适用范围：四项改动都在 **CLI 与查询层**，不涉及 `schema.sql` 和解析器，
  因此**已有数据库不需要重新导入**，这是本次评估最重要的前提。

## 3. 逐项评估

> 本节保留原始评估。重构后的具体落点、接口和测试要求见第 8 节。

### 3.1 `path` 显示时钟域与 CDC 说明

**现状**（`vtdb path 14438 --no-segments`）：

```text
path 14438
  slack         -0.319 ns   VIOLATED
  group         mmcm_clkout0
  requirement   0.250 ns
  ...
  exception     -
```

数据库里 `path.cdc_note` 和 `section_from_clock` / `section_to_clock` 两列都有值，
只是文本输出没有渲染，要判断跨域必须再写一条 `sql`。

**是否需要改：需要。** CDC 判断是时序分诊的第一步，而且这条信息的缺失导致多执行一次
SQL 查询。JSON 输出已经包含这些字段，不存在数据缺口。

**方案**：在 `commands/path.py` 的 `path_summary_lines()` 中增加两行（该函数已经可单测）：

```text
  clocks        xdma_ep_axi_aclk -> mmcm_clkout0
  cdc           Inter clock paths are considered valid unless explicitly
                excluded by timing constraints such as set_clock_groups ...
```

**规约**：

- `clocks` 行始终输出；`section_from_clock` 或 `section_to_clock` 为空时该侧显示 `-`；
- `cdc` 行仅在 `cdc_note` 非空时输出，按 76 列折行并缩进续行；
- `--format json` 不变，仍输出完整字段；
- `#` 列语义不变，退出码不变。

**改动量**：`commands/path.py` 约 20 行（扩展 `path_summary_lines()` 并新增 `_wrap()`），
测试 +2（摘要含 clocks 行、含 CDC 折行）。风险低。

### 3.2 `path` 长名字折行或省略

**现状**：`vtdb path 10862` 的 `source` / `destination` 行长度分别为 164 和 205 字符：

```text
  source        xiangshan_i/u_role/inst/Part0FabricRouterWra_0/inst/router/core/gen_ingress[3].ingress/gen_direction[1].queue/head/out_data_reg[38]/C ...
```

`--wide` 目前是"关闭截断"，默认却是"完全不截断"，两者没有区别。

**是否需要改：需要。** 排障时最先看的是"这是哪个模块的哪个寄存器"，205 字符的行在
80–120 列终端上无法阅读；而 `--wide` 的存在说明本来就想区分"简写/全名"，目前没做。

**方案**：新增

```python
def shorten_name(name: str, limit: int = 80, head: int = 2, tail: int = 3) -> str:
    """保留前 head 段和末尾 tail 段，中间用 … 省略。"""
```

默认输出：

```text
  source        xiangshan_i/u_role/…/queue/head/out_data_reg[38]/C   SLICE_X49Y59
```

规则：

- 总长不超过 80 字符时原样输出；
- 超长时保留前 2 段与末尾 3 段，中间以 `…` 连接；
- `--wide` 输出完整名字（现有语义保持不变）；
- JSON 始终是完整名字。

**改动量**：`commands/path.py` 约 15 行，单元测试 +3（短名不变、超长省略、`--wide` 全名）。
风险低，仅显示层变化。

### 3.3 `stats --by module` 增加过滤

**现状**：`vtdb stats --by module --level 4` 只能聚合全部路径。实际排障时想知道的是
"`shell_aclk` 组里违例落在哪些模块"，目前只能先用 `top --group` 拉路径、再手工按模块归类，
或者写 SQL。

**是否需要改：需要。** 这是四个问题里唯一影响"能否直接回答问题"的一项，
其余三项是显示与输入体验。

**方案**：为 `stats` 增加三个过滤选项：

| 选项 | 语义 |
| --- | --- |
| `--fail-only` | 只统计 `slack < 0` 的路径 |
| `--group TEXT` | 只统计该时钟组（子串匹配，与 3.4 一致） |
| `--prefix TEXT` | 只聚合名字包含该子串的层次前缀 |

示例：

```bash
vtdb stats --by module --level 4 --fail-only --group shell_aclk
```

```text
PREFIX                                          PATHS  FAIL  WNS     TNS(SAMPLE)
----------------------------------------------  -----  ----  ------  -----------
xiangshan_i/u_role/inst/Part0FabricRouterWra_0    132   132  -0.086       -3.623
```

**规约**：

- 过滤先于聚合：`PATHS`、`FAIL`、`WNS`、`TNS(SAMPLE)` 都是过滤后集合上的统计量；
- `--min-paths` 作用于过滤后的计数；
- 三个选项组合时是 AND 关系；
- 这三个选项只对 `--by module` 生效；与 `--by group` / `--by clock-pair` 组合时报用法错误
  （退出码 2），不静默忽略。

**改动量**：`queries.stats_module_sql` 约 25 行（新增三个 WHERE 条件），
`commands/stats.py` 与 `commands/args.py` 约 10 行（参数与传参），测试 +3。风险中低：需要把"过滤后计数"的语义写进
`--help` 和文档，避免误读。

### 3.4 `top --group` 支持子串匹配

**现状**：

```bash
$ vtdb top --group shell_aclk --fail-only
no rows
$ echo $?
0
```

组名实际是 `xiangshan_i/u_role/inst/shell_aclk`。工具静默返回空结果，用户无法区分
"没有违例"和"名字没对上"。

**是否需要改：需要，但要做兼容性取舍。** 组名由 Vivado 生成且很长，精确匹配几乎不可用；
同时 `--net` / `--cell` 已经是子串匹配，`--group` 精确匹配反而破坏了内部一致性。

**方案**：

- 默认改为子串匹配（`LIKE %TEXT%`），与 `--net` / `--cell` 一致；
- 新增 `--exact` 恢复精确匹配，供脚本固定行为；
- 空结果时在 stderr 提示过滤条件，例如
  `no rows (group~'shell_aclk')`，让匹配方式可见。

**规约**：

- `--group shell_aclk` 等价于 `path_group LIKE '%shell_aclk%'`；
- `--group shell_aclk --exact` 等价于 `path_group = 'shell_aclk'`；
- SQLite `LIKE` 对 ASCII 默认不区分大小写；需要严格区分大小写时用 `sql`；
- 退出码不变（空结果仍为 0）。

**改动量**：`queries.top_sql` 约 5 行，`commands/top.py` 与 `commands/args.py` 约 8 行
（`--exact` 与空结果提示），
测试 +2。风险：默认行为变化属于不兼容改动，需要在 README 与本文记录，脚本可用
`--exact` 固定旧行为。

## 4. 汇总

| 项 | 是否值得改 | 改动位置 | 代码量 | 兼容性 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| 3.1 `path` 显示 clocks / CDC | 是 | `commands/path.py` | ~20 行 | 只增输出 | P1 |
| 3.4 `--group` 子串 + 空结果提示 | 是 | `queries.py`、`commands/top.py`、`commands/args.py` | ~13 行 | 默认行为变化 | P1 |
| 3.2 长名字省略 | 是 | `commands/path.py` | ~15 行 | 只改显示 | P2 |
| 3.3 `stats --by module` 过滤 | 是 | `queries.py`、`commands/stats.py`、`commands/args.py` | ~35 行 | 只增选项 | P2 |

合计约 **120 行代码 + 80 行测试 + 文档更新**，不涉及 `schema.sql`、解析器和数据库迁移。

## 5. 实施顺序与验收

建议顺序：3.1 → 3.4 → 3.2 → 3.3。前两项改动小、收益直接；3.3 需要先确定过滤语义。

验收方式：

| 项 | 验收断言 |
| --- | --- |
| 3.1 | `path 14438` 输出包含 `clocks` 行与折行的 `cdc` 行；JSON 字段不变 |
| 3.2 | 默认输出中 `source` / `destination` 行不超过 80 字符；`--wide` 输出完整名字 |
| 3.3 | `stats --by module --fail-only --group shell_aclk` 的 `PATHS` 等于 `FAIL`；不加过滤时数值与改动前一致 |
| 3.4 | `top --group shell_aclk` 能命中；`--exact` 时仍返回空；空结果带过滤提示 |

现有测试应全部保持通过；新增用例按上表补充。

## 6. 本次不做的项

- **端点数去重**（134 条违例路径 vs 118 个失败端点）：需要先定义口径（按 `dest_pin` 还是
  按 `(dest_pin, path_type)`），不适合和本次改动混在一起。
- **`path` 增加独立 module 行**：3.2 的省略显示已能看出模块，暂不单独加。
- **`import` 摘要增加 WNS/TNS**：低风险但属于另一类需求，留给后续版本。
- **`stats --tns-share`**：需要和 3.3 的过滤语义一起设计，暂缓。

## 7. 复现命令

以下命令保留为修复前的复现记录；3.4 现在默认子串匹配，要复现旧行为需显式加 `--exact`。

```bash
# 3.1 缺少 clocks / cdc
PYTHONPATH=src python3 -m vtdb path --db build/vtdb/part0.db 14438 --no-segments

# 3.2 长名字
PYTHONPATH=src python3 -m vtdb path --db build/vtdb/part0.db 10862 --no-segments |
  awk '{ print length($0) }' | sort -n | tail -1

# 3.3 无过滤
PYTHONPATH=src python3 -m vtdb stats --db build/vtdb/part0.db --by module --level 4

# 3.4 修复前：部分组名返回空
PYTHONPATH=src python3 -m vtdb top --db build/vtdb/part0.db --group shell_aclk --fail-only
```

## 8. 重构后的实现方案

本节基于 Stage 0–5 之后的 `parser/` + `commands/` 结构，给出四项改动的最终落点、
接口、SQL 和测试要求。四项已在 2026-11-09 按此规约实现；以下保留规约便于回溯。

四项改动都不涉及 `schema.sql`、解析器和数据库迁移：所有需要的字段（`cdc_note`、
`section_from_clock`、`section_to_clock`、`path_group`）已经在现有库中，旧库不需要重导。

### 8.1 落点总表

| 项 | 主要落点 | 辅助改动 |
| --- | --- | --- |
| 3.1 `path` 显示 clocks / CDC | `commands/path.py::path_summary_lines()` | `commands/path.py::_wrap_labeled()` |
| 3.4 `top --group` 子串匹配 | `queries.py::top_sql()` | `commands/top.py`、`commands/args.py` |
| 3.2 `path` 长名字省略 | `commands/path.py::shorten_name()` | `commands/path.py::path_summary_lines()` |
| 3.3 `stats --by module` 过滤 | `queries.py::stats_module_sql()` | `commands/stats.py`、`commands/args.py` |

建议按 3.1 → 3.4 → 3.2 → 3.3 的顺序实施。3.1 和 3.2 都改 `path_summary_lines()`，
3.2 在 3.1 的基础上增加 `wide` 参数即可；3.4 和 3.3 的 `--group` 采用同一套子串语义。

### 8.2 3.1 `path` 显示 clocks 与 CDC

**最终接口**（3.2 完成后）：

```python
def path_summary_lines(path: dict, *, wide: bool = False) -> list[str]:
    ...
```

**输出位置**：

- `clocks` 行放在 `group` 之后、`requirement` 之前；
- `cdc` 行放在 `exception` 之后，仅当 `path["cdc_note"]` 非空时输出。

**格式**：

```text
path 14438
  slack         -0.319 ns   VIOLATED
  type          Setup  (Max, Slow corner)
  group         mmcm_clkout0
  clocks        xdma_ep_axi_aclk -> mmcm_clkout0
  requirement   0.250 ns
  ...
  exception     -
  cdc           Inter clock paths are considered valid unless explicitly
                excluded by timing constraints such as set_clock_groups ...
```

**规约**：

- `clocks` 行始终输出；`section_from_clock` 或 `section_to_clock` 为 `None`/空串时该侧显示 `-`，
  例如 `- -> clk_x`；
- `cdc` 行按总宽 76 列折行，首行前缀为 `"  cdc           "`（16 列），续行缩进 16 列；
- 折行用标准库 `textwrap.wrap()`，内容宽度为 `76 - 16 = 60`；
- `--format json` 不变，仍输出完整 `cdc_note` / `section_*_clock`；
- 现有的 `--wide` 不改变 clocks / cdc 的显示规则。

**参考实现骨架**：

```python
import textwrap

def _wrap_labeled(label: str, text: str, width: int = 76,
                  label_width: int = 14) -> list[str]:
    prefix = f"  {label:<{label_width}}"
    chunks = textwrap.wrap(
        text, width=width - len(prefix),
        break_long_words=True, break_on_hyphens=False)
    if not chunks:
        return []
    return [prefix + chunks[0]] + [" " * len(prefix) + chunk for chunk in chunks[1:]]
```

`path_summary_lines()` 中：

```python
clocks = f"{path['section_from_clock'] or '-'} -> {path['section_to_clock'] or '-'}"
lines.append(f"  clocks        {clocks}")
...
if path.get("cdc_note"):
    lines.extend(_wrap_labeled("cdc", path["cdc_note"]))
```

**测试**：

- `mini_timing` 的 path 1 输出包含 `clocks        clk_a -> clk_a`，不包含 `cdc`；
- `edge_timing` 的 path 1 输出包含 `clocks        clk_x -> clk_y`，`cdc` 行的首段包含
  `Inter clock paths`，续行以 16 个空格开头；
- 所有 `cdc` 行长度不超过 76；
- 增加一个 clocks 缺失的用例（可在临时库 `UPDATE path SET section_from_clock=NULL`），
  断言输出 `- -> ...`；
- `path --format json` 的键集合不变。

**文档**：更新 `docs/design/cli.md` 的 `path` 示例和输出说明。

### 8.3 3.4 `top --group` 子串匹配与空结果提示

**CLI**（`commands/args.py`）：

```python
top.add_argument("--group", metavar="NAME",
                 help="path group name (substring; use --exact for exact match)")
top.add_argument("--exact", action="store_true",
                 help="require an exact --group match")
```

**SQL**（`queries.top_sql`）：`filters` 增加 `group_exact`。

```python
if filters.get("group"):
    if filters.get("group_exact"):
        where.append("p.path_group = ?")
        params.append(filters["group"])
    else:
        where.append("p.path_group LIKE ?")
        params.append(f"%{filters['group']}%")
```

**空结果提示**：`cmd_top` 在 `rows` 为空时输出可读的过滤条件，而不只是 `no rows`：

```text
no rows (slack<0 AND group~'shell_aclk')
```

建议在 `commands/top.py` 增加一个纯函数：

```python
def _filter_hint(filters: dict) -> str:
    """Return a compact description of the active top filters."""
```

规则：

- `fail_only` → `slack<0`；
- `max_slack` → `slack<NS`；
- `group` → 默认 `group~'TEXT'`，`--exact` 时 `group='TEXT'`；
- `type` → `type=Setup`；
- `min_levels` → `levels>=N`；
- `net` / `cell` → `net~'TEXT'` / `cell~'TEXT'`；
- `site` → `site='SITE'`；
- 多个条件用 ` AND ` 连接；没有过滤条件时保持原来的 `no rows`；
- 提示仍写 stderr，退出码仍为 0。

**大小写说明**：SQLite `LIKE` 对 ASCII 默认不区分大小写；需要严格区分时走 `sql` 命令。
`--exact` 是精确的 `path_group = ?`，不受此影响。

**兼容性**：默认从精确匹配变为子串匹配，属于不兼容的默认行为变化。脚本可用
`--exact` 固定旧行为；需要在 `docs/design/cli.md` 和 `docs/guides/usage.md` 中注明。

**测试**：

- `top --group clk` 命中 `clk_a` / `clk_a_div2`；
- `top --group clk --exact` 返回空；
- `top --group clk_a --exact` 命中 path 1；
- 空结果 stderr 为 `no rows (group~'...')`，`--exact` 时为 `group='...'`；
- 现有 `tests/test_cli_contract.py::test_top_group_is_exact` 必须改写为
  “默认子串 + `--exact` 精确”的用例；
- `--help` 的选项测试加入 `--exact`。

### 8.4 3.2 `path` 长名字省略

**函数**（`commands/path.py`）：

```python
def shorten_name(name: str, limit: int = 80,
                 head: int = 2, tail: int = 3) -> str:
    """Keep the first ``head`` and last ``tail`` segments, eliding the middle."""
```

规则：

- `name` 为空、`<hidden>`、或总长不超过 `limit` 时原样返回；
- 按 `/` 分段；段数不超过 `head + tail` 时没有中间段可省；若原样仍超过 `limit`，
  直接按字符兜底；
- 否则输出 `"/".join(parts[:head]) + "/…/" + "/".join(parts[-tail:])`；
- 如果结果仍超过 `limit`，逐步减少 `head`/`tail`，最后按 `limit - 1` 字符加 `…` 兜底，
  保证返回值不超过 `limit`；
- `--wide` 时跳过 `shorten_name()`，输出完整名字；
- JSON 始终输出完整名字。

**调用处**：`path_summary_lines(path, *, wide=False)` 在渲染 source/destination 时：

```python
site = path["src_site"] or ""
prefix = "  source        "
site_part = f"   {site}" if site else ""
budget = 80 - len(prefix) - len(site_part)
if budget < 8:
    site_part = ""
    budget = 80 - len(prefix)
name = path["source_pin"] or "-"
if not wide:
    name = shorten_name(name, limit=max(1, budget))
line = f"{prefix}{name}{site_part}".rstrip()
```

`destination` 同理。预算不足时省略站点列，因此默认输出中 source/destination 行
不超过 80 列；`--wide` 不保证行宽。

**测试**：

- `shorten_name`：短名不变、`<hidden>` 不变、超长多段名含 `…` 且前 2/后 3 段保留；
- 段数不足时原样返回；
- 结果长度不超过 `limit`；
- `path_summary_lines(path, wide=False)` 的 source/destination 行不超过 80；
- `path_summary_lines(path, wide=True)` 使用完整 pin 名；
- `path --format json` 仍是完整名字；
- 现有 `path` 输出测试不因省略而失败（短名不受影响）。

**文档**：更新 `docs/design/cli.md` 的 `path` 示例与 `--wide` 说明。

### 8.5 3.3 `stats --by module` 增加过滤

**CLI**（`commands/args.py`）：

```python
stats.add_argument("--fail-only", action="store_true",
                   help="only count slack < 0 (module)")
stats.add_argument("--group", metavar="TEXT",
                   help="only paths whose group contains TEXT (module)")
stats.add_argument("--prefix", metavar="TEXT",
                   help="only prefixes containing TEXT (module)")
```

**SQL**（`queries.stats_module_sql`）：

```python
def stats_module_sql(level: int, side: str, min_paths: int, sort: str,
                     limit: int, *, fail_only: bool = False,
                     group: str | None = None,
                     prefix: str | None = None) -> tuple[str, list]:
    ...
    if fail_only:
        where.append("p.slack < 0")
    if group:
        where.append("p.path_group LIKE ?")
        params.append(f"%{group}%")
    if prefix:
        where.append("n.prefix LIKE ?")
        params.append(f"%{prefix}%")
    ...
    " HAVING COUNT(*) >= ?"
    params.append(int(min_paths))
```

要点：

- 三个过滤条件先于聚合，`PATHS` / `FAIL` / `WNS` / `TNS(SAMPLE)` 都是过滤后集合上的值；
- `--min-paths` 作用于过滤后的计数，即 HAVING 在 WHERE 之后；
- 三个条件之间是 AND；
- `--prefix` 匹配的是当前 `--level` 下的 `hier_node.prefix`，不是完整 pin 名；
- `--group` 与 3.4 一样使用子串 `LIKE`，ASCII 默认不区分大小写；
- `--by group` / `--by clock-pair` 与这三个选项组合时应报用法错误（退出码 2），
  而不是静默忽略：

  ```python
  if args.by != "module" and (args.fail_only or args.group or args.prefix):
      raise CliError("--fail-only/--group/--prefix require --by module", EXIT_USAGE)
  ```

**示例**：

```bash
vtdb stats --by module --level 4 --fail-only --group shell_aclk --min-paths 5
```

**测试**：

- `stats --by module --level 1 --fail-only` 只返回 `u_a`；
- `--group div2` 只统计 `clk_a_div2` 的路径；
- `--prefix u_a` 只返回 `u_a` 前缀；
- 三个条件组合是 AND；
- `--min-paths` 在过滤后生效；
- `--by group` 加 `--fail-only` 返回退出码 2；
- 现有 `stats` JSON 键集合不变。

### 8.6 测试与文档汇总

**新增/调整测试**：

| 测试 | 覆盖 |
| --- | --- |
| `tests/test_commands.py`（建议新增） | `shorten_name()`、`path_summary_lines()` 的 clocks/cdc/wide、`_filter_hint()` |
| `tests/test_cli.py` | `top --group` 子串/exact、stats 三种过滤、错误组合 |
| `tests/test_cli_contract.py` | 更新 `test_top_group_is_exact`，`--help` 增加 `--exact` / stats 过滤项 |
| `tests/test_parser.py` | 不需要改；解析器字段已存在 |

**需要同步的文档**：

- `docs/design/cli.md`：`top --group` / `--exact`、`path` clocks/cdc、`path --wide`、
  `stats` 三个过滤选项、空结果提示；
- `docs/guides/usage.md`：第 6 节“`--group` 匹配不到”改写为子串/`--exact` 说明；
- `README.md`：如果快速开始示例涉及 `--group`，补上子串语义（当前示例未使用）；
- `docs/design/improvements.md`：已在本节末尾 8.8 记录实现结果。

**验收**：

- 全部现有测试通过，新增用例覆盖上表；
- `path` 的 clocks/cdc 行与 JSON 字段同时可用；
- `top --group` 默认子串，`--exact` 精确，空结果提示可见；
- `path` 默认输出不超过 80 列的 source/destination 行，`--wide` 完整；
- `stats --by module` 的过滤结果与手写 SQL 一致；
- 不涉及 `schema.sql`、`SCHEMA_VERSION` 或旧库重导。

### 8.7 实施顺序与提交拆分

建议拆成四个可独立 review 的提交：

1. `feat(path): show launch/capture clocks and CDC notes` — 3.1；
2. `feat(top): match path groups by substring with --exact` — 3.4，并改契约测试与文档；
3. `feat(path): shorten long endpoint names` — 3.2；
4. `feat(stats): filter module aggregation` — 3.3。

每个提交单独跑 `python -m pytest tests -q`，并更新对应文档。3.4 是唯一改变默认行为的项，
建议单独提交并在提交说明中写明兼容性。


### 8.8 实现结果（2026-11-09）

四项均已实现，未改 `schema.sql`、`SCHEMA_VERSION`，旧库不需要重导。

| 项 | 实际落点 | 覆盖测试 |
| --- | --- | --- |
| 3.1 clocks / CDC | `commands/path.py`：`_wrap_labeled()`、`path_summary_lines()` | `tests/test_commands.py` |
| 3.4 `--group` 子串 / `--exact` | `queries.top_sql()`、`commands/top.py`、`commands/args.py` | `tests/test_cli_contract.py` |
| 3.2 长名字省略 | `commands/path.py`：`shorten_name()`、`_endpoint_line()`、`path_summary_lines(wide=)` | `tests/test_commands.py` |
| 3.3 module 过滤 | `queries.stats_module_sql()`、`commands/stats.py`、`commands/args.py` | `tests/test_cli.py` |

实现时的具体行为：

- `path` 摘要新增 `clocks` 行；有 CDC 说明时按 76 列折行；
- `top --group` 默认 `LIKE` 子串匹配，`--exact` 使用 `path_group = ?`；空结果提示包含过滤条件；
- `path` 的 source/destination 默认省略中间层级并保证默认行宽不超过 80 列，`--wide` 输出完整名字；
- `stats --by module` 的 `--fail-only` / `--group` / `--prefix` 在聚合前过滤，`--min-paths`
  在过滤后生效；与其他 `--by` 组合返回退出码 2。

验证结果：`python -m pytest tests -q` 共 73 个用例通过；`python -m compileall -q src`
通过；`make zipapp` 后 `dist/vtdb.pyz --version` 正常。

同步更新的文档：`docs/design/cli.md`、`docs/guides/usage.md`、`AGENTS.md`、本文件。
