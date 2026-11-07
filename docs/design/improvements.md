# 时序报告工具改进评估：`path` 可读性与过滤能力

本文评估 `vtdb` 在真实排障中暴露的四个可用性问题，回答三件事：是否真的需要改、改动量多大、
改完之后的接口行为应该是什么。

本文是**评估与设计说明**，不包含实现。评估对象为 `vtdb` 0.1.0，接口规范见
[命令行接口设计](cli.md)。

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

**方案**：在 `cmd_path` 的摘要块里增加两行，并把摘要渲染抽成可单测的函数：

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

**改动量**：`cli.py` 约 20 行（新增 `_print_path_summary(path)` 与 `_wrap()`），
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

**改动量**：`cli.py` 约 15 行，单元测试 +3（短名不变、超长省略、`--wide` 全名）。
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
- 对 `--by group` / `--by clock-pair` 暂不生效（本次只改 module，避免一次铺开）。

**改动量**：`queries.stats_module_sql` 约 25 行（新增三个 WHERE 条件），
`cli.py` 约 10 行（参数与传参），测试 +3。风险中低：需要把"过滤后计数"的语义写进
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
- 匹配仍区分大小写（与 SQLite `LIKE` 默认行为一致，需要大小写不敏感时用 `sql`）；
- 退出码不变（空结果仍为 0）。

**改动量**：`queries.top_sql` 约 5 行，`cli.py` 约 8 行（`--exact` 与空结果提示），
测试 +2。风险：默认行为变化属于不兼容改动，需要在 README 与本文记录，脚本可用
`--exact` 固定旧行为。

## 4. 汇总

| 项 | 是否值得改 | 改动位置 | 代码量 | 兼容性 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| 3.1 `path` 显示 clocks / CDC | 是 | `cli.py` | ~20 行 | 只增输出 | P1 |
| 3.4 `--group` 子串 + 空结果提示 | 是 | `queries.py`、`cli.py` | ~13 行 | 默认行为变化 | P1 |
| 3.2 长名字省略 | 是 | `cli.py` | ~15 行 | 只改显示 | P2 |
| 3.3 `stats --by module` 过滤 | 是 | `queries.py`、`cli.py` | ~35 行 | 只增选项 | P2 |

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

现有测试（27 个）应全部保持通过；新增用例按上表补充。

## 6. 本次不做的项

- **端点数去重**（134 条违例路径 vs 118 个失败端点）：需要先定义口径（按 `dest_pin` 还是
  按 `(dest_pin, path_type)`），不适合和本次改动混在一起。
- **`path` 增加独立 module 行**：3.2 的省略显示已能看出模块，暂不单独加。
- **`import` 摘要增加 WNS/TNS**：低风险但属于另一类需求，留给后续版本。
- **`stats --tns-share`**：需要和 3.3 的过滤语义一起设计，暂缓。

## 7. 复现命令

```bash
# 3.1 缺少 clocks / cdc
PYTHONPATH=src python3 -m vtdb path --db build/vtdb/part0.db 14438 --no-segments

# 3.2 长名字
PYTHONPATH=src python3 -m vtdb path --db build/vtdb/part0.db 10862 --no-segments |
  awk '{ print length($0) }' | sort -n | tail -1

# 3.3 无过滤
PYTHONPATH=src python3 -m vtdb stats --db build/vtdb/part0.db --by module --level 4

# 3.4 部分组名返回空
PYTHONPATH=src python3 -m vtdb top --db build/vtdb/part0.db --group shell_aclk --fail-only
```
