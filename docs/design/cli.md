# Vivado 时序报告工具接口设计（草案）

本文定义 `vtdb` 的用户接口：命令、选项、输出格式与退出码。数据模型见
[数据模型](database.md)，本文不重复。

`vtdb` 是**通用工具**，用于把 Vivado `report_timing_summary` 的文本报告转成 SQLite，
不属于 REMUS 构建体系，已迁出到独立仓库。

本文是**设计草案**，命令名与选项在实现前仍可能调整。后续功能规划与讨论见
[GitHub Issues](https://github.com/euphgh/vtdb/issues)。对应实现在 `src/vtdb/`。

## 1. 定位与范围

- 输入：一份 `report_timing_summary` 文本报告（暂不支持 `report_timing` 等其它格式）；
- 输出：一个 SQLite 文件，即这份报告的完整快照；
- 交互：命令行，无守护进程、无配置文件、无服务端。

**一次导入只处理一份报告，一个数据库只描述一份报告。** 不支持把多份报告导入同一个库，
也不提供报告列表、跨报告 diff 等命令。

## 2. 设计原则

1. **单一职责**：工具只做"报告 → 可查询数据"，对比、回归、趋势留给上层脚本。
2. **命令少**：五个命令覆盖全部能力，用户不需要记命令树。
3. **人机双模**：默认表格给人看；`--format json/csv` 给脚本，字段同名同义。
4. **只读查询**：所有查询走只读连接，`sql` 拒绝非查询语句。
5. **零依赖**：只用 Python 标准库（`sqlite3`、`re`、`hashlib`、`argparse`），
   便于打包成单文件或 `pipx install`。

## 3. 安装与入口

独立的 Python 包，与控制台入口：

```text
pyproject.toml
src/vtdb/
    __init__.py
    __main__.py        # python -m vtdb
    cli.py             # main、异常映射（薄入口）
    commands/          # argparse、列定义、五个命令
    parser/            # 流式解析包（core / sections / details / path）
    db.py              # SQLite 写入与 schema 加载
    schema.sql         # DDL
    queries.py         # 各命令的 SQL
    output.py          # table / json / csv / tsv / md 渲染
```

```bash
pipx install vtdb          # 或 pip install -e .
vtdb --version
vtdb --help
vtdb <command> --help      # 每个命令自带 Examples 段
```

本地开发也可以直接用 `python -m vtdb ...`，行为与安装版一致。

## 4. 通用约定

### 4.1 数据库定位

查询类命令按以下顺序确定目标库：

1. `--db PATH`
2. 环境变量 `VTD_DB`
3. 当前目录下恰好存在一个 `*.db` 时，使用它
4. 否则报错，列出找到的候选（若有多个），退出码 4

`import` 的输出路径：

1. `-o/--db PATH`
2. 默认与报告同目录、同文件名、`.db` 后缀（`post_route_timing.rpt` →
   `post_route_timing.db`）

目标文件已存在时，`import` 拒绝覆盖，除非显式 `--force`。

### 4.2 输出格式

| 选项 | 取值 | 说明 |
| --- | --- | --- |
| `--format` | `table`（默认）/ `json` / `csv` / `tsv` / `md` | `md` 生成 Markdown 表格，便于贴进 issue |
| `-n, --limit` | 整数，默认 20（`sql` 默认 200） | 行数上限，`0` 表示不限 |
| `--wide` | 开关 | 不截断长名字 |
| `--color` | `auto`（默认）/ `always` / `never` | `auto` 仅在 TTY 下着色，尊重 `NO_COLOR` |
| `-q, --quiet` | 开关 | 只输出结果，不打印提示 |
| `-v, --verbose` | 开关 | 把执行的 SQL 与耗时打到 stderr |

数据写 stdout，提示和错误写 stderr，保证重定向干净。空结果不是错误：打印
`no rows`（JSON 下是 `[]`），退出码 0。

### 4.3 数字与空值

| 类型 | 格式 |
| --- | --- |
| slack / delay / tns | 3 位小数，单位 ns |
| 百分比 | 1 位小数（列名已含 `%`） |
| 时间 | ISO 8601（`2026-11-03T15:08:49`） |
| 缺失值 | 表格里 `-`；JSON 里 `null`；CSV 里空字段 |

### 4.4 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功（包括空结果） |
| 1 | 导入后的一致性检查未通过（仅 `--strict`），或查询结果自身不一致 |
| 2 | 用法错误（argparse 默认行为） |
| 3 | 导入失败（文件不可读、无法解析） |
| 4 | 数据库不存在、版本不兼容或多候选无法确定 |
| 5 | 文件读写失败 |

## 5. 命令一览

| 命令 | 用途 | 典型调用 |
| --- | --- | --- |
| `import` | 解析报告并生成数据库 | `vtdb import post_route_timing.rpt` |
| `top` | 按属性/拓扑筛路径并排序 | `vtdb top --fail-only -n 20` |
| `path` | 查看单条路径的完整明细 | `vtdb path 42` |
| `stats` | 按 group / clock pair / 模块聚合 | `vtdb stats --by group` |
| `sql` | 执行只读 SQL | `vtdb sql "SELECT ..."` |

## 6. 命令详解

### 6.1 `import`

```text
vtdb import REPORT [options]
```

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `-o, --db PATH` | 同目录同名 `.db` | 输出数据库路径 |
| `--force` | 关 | 覆盖已存在的库文件 |
| `--keep-detail` | `all` | `all`（默认）/ `negative`（只存违例路径明细）/ `none` |
| `--no-hier` | 关 | 不生成 `hier_prefix`，`stats --by module` 不可用 |
| `--dry-run` | 关 | 只解析并打印摘要，不写库 |
| `--strict` | 关 | 一致性检查有失败项时返回退出码 1 |

示例：

```bash
vtdb import build/post_route_timing.rpt
```

```text
imported build/post_route_timing.rpt -> build/post_route_timing.db
  design     xiangshan_wrapper (Physopt postRoute)
  paths      22973 (2248 violated)
  segments   587939
  pw checks  41676
  checks     ok
  elapsed    6.1s
  size       201 MB
```

检查未通过时，摘要末尾追加问题列表（行号 + 原因），并把问题写入 `import_issue` 表：

```text
  checks     FAILED (2 issues)
    line 1042388  unknown_field      unexpected header field "Foo Bar"
    line 1290041  delay_mismatch     sum(incr)=4.576 != data_delay=4.574
```

`--dry-run` 只跑解析和检查，可用于验证新版本对旧报告的兼容性。

#### 关闭明细的代价

`--keep-detail none` 只保存路径级信息，不保存逐段明细。以 post_route 报告为例，库体积
从 186 MB 降到 76 MB，代价是：

| 失去的能力 | 说明 |
| --- | --- |
| `top --net` / `--cell` / `--site` | 拓扑检索依赖 `path_segment`，无明细时直接报错并提示重新导入 |
| `path` 的逐段表 | 只剩摘要，看不到是哪一级 LUT 或哪条 net 贡献了多少延迟 |
| 共享瓶颈分析 | "被最多违例路径经过的 cell/net" 需要明细 |
| 明细级不变量校验 | `sum(incr) == data_delay` 改为只在导入时于内存中校验，不落库 |

仍然保留：

- `logic_cell`（来自头部 `Logic Levels: 39 (CARRY8=12 ...)`），按单元类型筛选仍可用；
- 路径级字段：slack、path group、logic/route 占比、源/目的单元、`fanout_max`、
  `src_site`/`dst_site`（后三者由导入器在内存中计算后写入 `path`）；
- `--raw` 回原文能力（依赖 `path.line_start` / `line_end`）。

需要明细时重新导入即可，一次约 6 秒，因此 `none` 不是单向选择：

```bash
vtdb import build/post_route_timing.rpt --force --keep-detail all
```

如果日常排查经常用拓扑检索，可以用 `negative`：只保存违例路径的明细，体积 91 MB，
覆盖绝大多数调试场景。默认值仍是 `all`，需要小库时显式传 `--keep-detail`。

### 6.2 `top`

```text
vtdb top [filters] [--sort FIELD] [-n N]
```

筛选条件（可组合）：

| 选项 | 说明 |
| --- | --- |
| `--fail-only` | 只显示 `slack < 0` |
| `--max-slack NS` | 只显示 `slack < NS` |
| `--group NAME` | 子串匹配 path group（默认；组名较长时可直接用片段） |
| `--exact` | 配合 `--group`，要求完整组名相等 |
| `--type setup\|hold\|recovery\|removal` | path type |
| `--min-levels N` | 逻辑级数下限 |
| `--net PAT` | 只保留经过名字包含 `PAT` 的 net 的路径 |
| `--cell PAT` | 只保留经过名字包含 `PAT` 的单元/实例的路径 |
| `--site SITE` | 只保留经过指定站点（如 `SLICE_X1Y64`）的路径 |
| `--sort` | `slack`（默认）/ `route` / `logic` / `fanout` |

```bash
vtdb top --fail-only -n 5
```

```text
#     SLACK  GROUP                              LVL  ROUTE%  SOURCE                        DEST
1    -7.202  xiangshan_i/u_role/inst/shell_aclk   31    76.5  router/rx/ack_bitmap_q[2]/C   FSM_state[0]/CE
2    -7.201  xiangshan_i/u_role/inst/shell_aclk   31    76.5  router/rx/ack_bitmap_q[2]/C   FSM_state[0]/CE
3    -7.201  xiangshan_i/u_role/inst/shell_aclk   31    76.5  router/rx/ack_bitmap_q[2]/C   FSM_state[0]/CE
```

规则：

- 默认按 `slack` 升序；`--sort route` 用于找"布线主导"的路径；
- `SOURCE` / `DEST` 默认显示单元名，由终端宽度截断，`--wide` 显示完整 pin 名；
- 带 `Timing Exception` 的路径行尾标 `[exc]`；
- `--group` / `--net` / `--cell` 是子串匹配（`LIKE %PAT%`），`--site` 是精确匹配；
- `--group` 默认子串匹配，`--exact` 恢复完整相等；SQLite `LIKE` 对 ASCII 默认不区分大小写；
- `--net` / `--cell` / `--site` 需要库中存在明细；用 `--keep-detail none` 导入的库执行时会报错，并提示重新导入；
- 找不到匹配时输出 `no rows`，并在 stderr 附带过滤条件提示（如
  `no rows (group~'shell_aclk')`），退出码 0。

### 6.3 `path`

```text
vtdb path SEQ [--raw] [--no-segments] [--wide]
```

```bash
vtdb path 42
```

```text
path 42
  slack         -7.202 ns   VIOLATED
  type          Setup  (Max, Slow corner)
  group         xiangshan_i/u_role/inst/shell_aclk
  clocks        xdma_ep_axi_aclk -> mmcm_clkout0
  requirement   4.000 ns
  data delay    10.544 ns   logic 2.478 (23.5%)  route 8.066 (76.5%)
  logic levels  31          LUT6=18 LUT5=6 CARRY8=7
  source        .../ack_emit_bitmap_q_reg[2]/C        SLICE_X1Y64
  destination   .../FSM_onehot_state_q_reg[0]/CE      SLICE_X2Y10
  clock skew    -0.105 ns   (DCD 2.004 / SCD 2.283 / CPR 0.174)
  uncertainty    0.046 ns
  exception     -

BLOCK        SEQ  SITE           CELL  ARC                INCR    CUM     RESOURCE
launch_clock   1  -              -     -                  0.000   2.283   ...
data           1  SLICE_X1Y64    FDRE  Prop_DFF2_..._Q    0.077   2.363   .../Q
data           2  -              net   fo=529             0.445   2.808   ...
data           3  SLICE_X1Y66    LUT6  Prop_H6LUT_..._O   0.089   2.897   .../O
...
```

- `clocks` 行始终显示 launch/capture 时钟；`cdc` 行仅在报告给出
  `Clock Domain Crossing` 说明时显示，按 76 列折行并缩进续行。
- `source` / `destination` 默认省略中间层级；`--wide` 显示完整 pin 名。JSON 始终是完整名字。
- `--raw` 打印原始报告 `line_start`–`line_end` 的文本，用于与 Vivado 输出逐字对照；
  源文件已移动时提示路径失效并返回退出码 4。
- `--no-segments` 只打印摘要块。
- `SEQ` 不存在时报错并给出范围提示（`path 42: not found (1..22973)`）。

### 6.4 `stats`

```text
vtdb stats [--by group|clock-pair|module] [options]
```

| 选项 | 说明 |
| --- | --- |
| `--by` | `group`（默认）/ `clock-pair` / `module` |
| `--level N` | `module` 模式下按前 N 级组件聚合，默认 3（`xiangshan_i/u_role/inst` 为 3） |
| `--side source\|dest\|both` | `module` 模式下按源端/目的端/两者聚合，默认 `source` |
| `--fail-only` | `module` 模式下只统计 `slack < 0` 的路径 |
| `--group TEXT` | `module` 模式下只统计组名包含 `TEXT` 的路径 |
| `--prefix TEXT` | `module` 模式下只聚合名字包含 `TEXT` 的层次前缀 |
| `--min-paths N` | 过滤样本过少的行（在 `--fail-only` / `--group` / `--prefix` 之后生效） |
| `--sort` | `wns`（默认）/ `tns` / `fail` / `paths` |

```bash
vtdb stats --by group --sort wns
```

```text
GROUP                                PATHS  FAIL     WNS   TNS(SAMPLE)  ROUTE%
xiangshan_i/u_role/inst/shell_aclk    3332  2000   -7.202     -4821.334     82.4
mmcm_clkout0                          1183     2   -0.383       -0.501      86.0
xdma_ep_axi_aclk                      3023   246   -0.379      -12.883      79.0
GTYE4_CHANNEL_TXOUTCLK[3]_3           2003     0    0.002        0.000       -
```

```bash
vtdb stats --by module --level 3 --min-paths 10
vtdb stats --by module --level 4 --fail-only --group shell_aclk
```

```text
PREFIX                       PATHS  FAIL     WNS   TNS(SAMPLE)
xiangshan_i/xdma_ep           7852   246   -0.379      -12.883
xiangshan_i/u_role/inst       4703  1902   -7.202     -4600.120
xiangshan_i/aurora_64b66b_0   1923     0    0.105        0.000
```

`TNS(SAMPLE)` 明确表示明细采样之和，不是报告里的全设计 TNS。全设计口径见
`report.tns`（可用 `vtdb sql "SELECT tns FROM report"` 查看）。

`--fail-only` / `--group` / `--prefix` 只对 `--by module` 生效；与其他 `--by` 组合
时报用法错误（退出码 2）。

`--no-hier` 导入的库执行 `--by module` 时报错，提示重新导入。

### 6.5 `sql`

```text
vtdb sql (QUERY | --file FILE) [--param k=v]... [--explain]
```

```bash
vtdb sql "SELECT COUNT(*) AS n FROM path WHERE slack < 0"
vtdb sql --file q/top_fail.sql --param group=shell_aclk --format csv
```

规则：

- 只读连接（`PRAGMA query_only=1`），语句必须以 `SELECT` / `WITH` / `EXPLAIN` 开头，
  否则拒绝执行（退出码 2）；
- `--param` 以 `:name` 绑定，避免手工拼接；
- 默认只输出 200 行，超出时 stderr 提示 `limit reached, use -n 0 to disable`；
- `--explain` 先打印 `EXPLAIN QUERY PLAN` 再执行。

`sql` 是逃生口，不保证列名稳定；需要稳定列名的场景请用其他命令。

## 7. 输出格式细节

### 7.1 table

- 列宽按内容计算，总宽不超过终端宽度（`shutil.get_terminal_size`）；
- 超宽时优先截断名字列（`SOURCE`、`DEST`、`RESOURCE`），截断标记 `…`；
- 数字列右对齐，文本列左对齐；
- TTY 下负 slack 红色，其余不动色，避免整屏彩色。

### 7.2 json

- 顶层是数组，每行一个对象，键名与表列名一致（`snake_case`）；
- `null` 表示缺失，不省略键；
- 不加包装对象，便于 `jq` 直接迭代；
- 字段名一旦发布视为稳定接口，新增字段只能追加。

### 7.3 csv / tsv

- 首行表头；
- 含逗号、引号、换行的字段按 RFC 4180 转义；
- UTF-8 无 BOM（需要 Excel 时用 `tsv` 或自行转换）。

### 7.4 md

输出标准 Markdown 表格，用于贴到 issue、MR 或文档里；不做截断。

## 8. 典型工作流

### 8.1 导入并看全局

```bash
vtdb import build/post_route_timing.rpt
vtdb stats --by group --sort wns
vtdb top --fail-only -n 20
```

### 8.2 定位一条慢路径

```bash
vtdb top --fail-only -n 5          # 找到最差路径的序号
vtdb path 1                        # 看它的逐段延迟
vtdb top --net ack_emit_bitmap_q__0 --fail-only   # 反查经过这条 net 的路径
```

### 8.3 检查某个模块

```bash
vtdb stats --by module --level 4 --min-paths 5
vtdb top --cell irq_batch --fail-only
```

### 8.4 与另一次 run 对照

工具内不做 diff。需要对照时保留两个库文件，用 `sqlite3` 的 `ATTACH`：

```bash
sqlite3 run_a/timing.db <<'SQL'
ATTACH 'run_b/timing.db' AS b;
SELECT a.path_key, a.slack AS old, b.slack AS new, ROUND(b.slack - a.slack, 3) AS delta
FROM main.path a JOIN b.path b USING (path_key)
WHERE b.slack < a.slack - 0.2
ORDER BY delta LIMIT 50;
SQL
```

## 9. 与 Vivado 流程集成

在生成报告的 tcl 后面追加一段可选调用，失败只告警、不阻断流程：

```tcl
if {[catch {exec vtdb import ${impl_rpt_dir}/post_route_timing.rpt --force --quiet} err]} {
    puts "WARNING: timing database import failed: $err"
}
```

约定：

- 报告与数据库同目录同名，后续查询直接 `cd` 到该目录执行 `vtdb top`；
- CI 中需要强制校验时加 `--strict` 并检查退出码；
- 每次运行覆盖同一个 `.db` 文件，不积累历史库。

## 10. 被移除的能力与理由

相比最初的接口草案，本版本删除了以下命令，以保持"单一职责"：

| 删除 | 理由 | 替代方式 |
| --- | --- | --- |
| `ls` | 一库一报告，没有报告列表 | 直接看目录里的 `.db` |
| `diff` | 报告是采样，跨 run 语义模糊；属于上层分析 | 两个库 + `sqlite3 ATTACH` |
| `verify` | 每次导入已内建一致性检查 | `import` 摘要 + `--strict` |
| `export` | 查询命令已支持 `--format csv/json` | `vtdb top/stats/sql --format csv` |
| `find` | 与 `top` 的筛选语义重复 | `vtdb top --net/--cell/--site` |
| 批量导入 | 一库一报告，批量没有意义 | 循环调用 `vtdb import` |

## 11. 测试与验收

`tests/test_cli.py`（pytest），使用裁剪过的迷你报告 `tests/data/mini_timing.rpt`；
`tests/test_parser.py` 覆盖解析器纯函数与路径级字段，`tests/test_cli_contract.py`
锁定 JSON 键名、帮助面与退出码，`tests/data/edge_timing.rpt` 覆盖 hidden / CDC /
exception 路径。

| 用例 | 断言 |
| --- | --- |
| 导入 | 路径数、明细数、PW 数与夹具一致，`checks` 为 ok |
| 重复导入 | 不加 `--force` 时失败（退出码 3），库文件未被改写 |
| `--force` | 覆盖后计数一致 |
| `--dry-run` | 不生成 `.db` |
| `--keep-detail negative` | 只有违例路径有明细 |
| `top` 过滤 | `--fail-only` / `--group` / `--net` / `--cell` / `--site` 行数符合预期 |
| `top --group` | 默认子串匹配；`--exact` 精确匹配；空结果提示包含过滤条件 |
| `path` | 摘要字段与原始报告一致；clocks / CDC 行；长名字省略与 `--wide`；`SEQ` 越界退出码 2 |
| `stats` | 三种 `--by` 的输出列与聚合值正确 |
| `stats --by module` 过滤 | `--fail-only` / `--group` / `--prefix` 先过滤后聚合；错误组合退出码 2 |
| `sql` | `DELETE` 被拒绝（退出码 2）；`:param` 绑定生效 |
| 输出格式 | `--format json` 可被 `json.loads`；csv 列数一致 |
| 数据库定位 | `VTD_DB`、cwd 单库、多库报错（退出码 4） |

验收标准：两份真实报告导入后 `checks` 全绿；`top` / `path` / `stats` 的结果与
直接写 SQL 一致。

## 12. 未决问题

1. 默认 `none` 后，`top --net/--cell/--site` 的报错是否需要直接给出可执行的重新导入命令？
2. `--format md` 是否保留？
3. 是否需要为资源名建 FTS5 索引（无明细时模糊检索只发生在 `resource.leaf`，成本更低）？
4. 默认值将来是否可能改成 `negative`（只存违例路径明细，约 75 MB）？
