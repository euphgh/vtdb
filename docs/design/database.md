# Vivado 时序报告数据库设计（草案）

本文给出把 Vivado `report_timing_summary` 文本报告导入 SQLite、用于时序查询与优化
分析的数据模型，面向后续实现者和维护者。

本文是**设计草案**，不是当前已实现能力的说明。对应实现在 `src/vtdb/`。

**范围约定：一次导入只对应一份报告，一个 SQLite 文件就是一份报告的完整快照。**
工具不管理多个报告，也不做跨报告对比；需要对照两次运行时，直接用两份 `.db` 文件。

文中的规模、耗时和分布数据来自两份真实报告（REMUS 仓库的构建产物，不随本文档发布）：

- `build/synth_timing.rpt`：36.9 MB，310,399 行，`role_top` / `Synthesized`；
- `build/post_route_timing.rpt`：208.7 MB，1,577,594 行，`xiangshan_wrapper` /
  `Physopt postRoute`。

两份报告由 REMUS 仓库的 `fpga/**/scripts/flow/*.tcl` 以同一条命令生成：

```tcl
report_timing_summary -file <dir>/<name>_timing.rpt -delay_type max -max_paths 1000
```

## 1. 目标与非目标

### 1.1 目标

把一份报告变成可反复查询的结构化数据，支撑三类问题：

1. **按属性筛路径**：最差 slack、指定时钟组、指定 path type、逻辑级数或 route 占比；
2. **按拓扑筛路径**：经过某个 cell、某个 net 或某个站点的路径有哪些；
3. **单报告内聚合**：按 path group、clock pair 或层次模块看违例分布。

### 1.2 非目标

- 不重算时序：数据库只保存报告已经给出的结论，不替代 Vivado 的时序引擎。
- 不解析 DCP、网表或 XDC：约束语义只以报告文本中的 `Timing Exception`、
  `Clock Domain Crossing` 等字段为准。
- 不管理多份报告，不提供 diff、回归、趋势跟踪；这些属于上层工具或后续版本。
- 暂不支持 `report_timing`（单路径详细报告）等其它格式，输入必须是 `report_timing_summary`。
- 不追求"还原全设计"：`-max_paths` 是采样，明细表永远不等于完整时序空间。

## 2. 报告结构

### 2.1 文件级章节

两份报告的顶层章节顺序一致，可用下面的命令定位：

```bash
grep -n -E '^\| (Timer Settings|Design Timing Summary|Clock Summary|Intra Clock Table|Inter Clock Table|Other Path Groups Table|Timing Details)' report.rpt
```

| 章节 | 内容 | 入库表 |
| --- | --- | --- |
| 头部 9 项元数据 | 工具版本、日期、主机、命令行、design、device、speed file、design state、温度等级 | `report` |
| Timer Settings | 多角分析、pessimism removal 等开关 | `timer_setting` |
| check_timing | 12 类约束检查（no_clock、no_input_delay…） | `check_timing` |
| Design Timing Summary | 全设计 WNS/TNS/WPWS/TPWS 与失败端点数 | `report`（汇总列） |
| Clock Summary | 时钟名、波形、周期、频率、生成关系（缩进） | `clock` |
| Intra Clock Table | 每个时钟内部的 WNS/TNS | `timing_summary`（kind=intra） |
| Inter Clock Table | 时钟对之间的 WNS/TNS | `timing_summary`（kind=inter） |
| Other Path Groups Table | `**async_default**` 等特殊组 | `timing_summary`（kind=other） |
| Timing Details | 按 clock pair 分节，每节含 Setup/Hold/PW 汇总 + 路径明细 + 脉宽表 | `clock_pair` / `path` / `path_segment` / `pw_check` |

### 2.2 路径块结构

每个 clock pair 节内部结构固定：

```text
-------------------------------------------------------------------------------
From Clock:  shell_aclk
  To Clock:  shell_aclk

Setup :  0  Failing Endpoints,  Worst Slack  ...
Hold  : NA  Failing Endpoints,  Worst Slack  ...
PW    :  0  Failing Endpoints,  Worst Slack  ...
-------------------------------------------------------------------------------

Max Delay Paths
-------------------------------------------------------------------------------
Slack (VIOLATED) :  -4.035ns  (required time - arrival time)
  Source:      ...
  Destination: ...
  Path Group / Path Type / Requirement / Data Path Delay / Logic Levels
  Clock Path Skew:  ...（含 DCD / SCD / CPR 子项）
  Clock Uncertainty: ...（含 TSJ / TIJ / DJ / PE 子项）

    Location      Delay type       Incr(ns)  Path(ns)  [PBlock]  Netlist Resource(s)
  -------------------------------------------------------------------  -----------
                         (clock shell_aclk rise edge)
                                         0.000     0.000 r
                         net (fo=1961, unset)   0.028  0.028   ...
                         FDRE                   r  .../C
  -------------------------------------------------------------------  -----------
                         FDRE (Prop_FDRE_C_Q)   0.077  0.105 r ...
                         net (fo=7, unplaced)   0.179  0.284   ...
                         ...
                         FDRE                   r  ...
  -------------------------------------------------------------------  -----------
                         (clock shell_aclk rise edge)
                                         4.000     4.000 r
                         ...  clock pessimism / clock uncertainty / Setup_FDRE_C_D
  -------------------------------------------------------------------
                         required time      4.010
                         arrival time      -0.992
  -------------------------------------------------------------------
                         slack              3.018
```

要点：

- 明细表被**两种分隔线**切分：双列分隔线划分 launch / data / capture 三段；单列分隔线
  包围 `required time`、`arrival time`、`slack` 三行。
- `capture` 段第一行的 `Incr` 等于时钟周期（如 `6.206`），是时钟轴上的绝对时间，
  不是数据路径延迟，聚合时必须按 `block` 区分。
- `arrival time` 打印为**负值**（Vivado 的书写习惯）。真实到达时间需取反，否则
  `required - arrival ≠ slack`。

### 2.3 明细表列

synthesis 报告：

```text
Location | Delay type | Incr(ns) | Path(ns) | r/f | Netlist Resource(s)
```

post-route 报告多一列，列顺序固定：

```text
Location | Delay type | Incr(ns) | Path(ns) | r/f | PBlock | Netlist Resource(s)
```

列语义容易混淆，必须按实际含义建模：

| 列 | 实际含义 | 示例 |
| --- | --- | --- |
| Location | 站点（cell 行）或时钟根 | `SLICE_X197Y60`、`GTYE4_CHANNEL_X1Y15`、`X6Y1 (CLOCK_ROOT)`；net 行为空 |
| Delay type | 单元类型 + 引脚功能，或 `net (fo=N, ...)` | `FDRE (Prop_FDRE_C_Q)`、`LUT6 (Prop_LUT6_I0_O)`、`net (fo=529, routed)` |
| Incr / Path | 增量延迟 / 累计时间 | `0.077` / `0.105` |
| r/f | 信号跳变方向，不是"Setup/Hold 极性" | `r`、`f`；net 行为空 |
| PBlock | 物理区域（仅 post-route） | `shell_region` |
| Resource | 完整 pin / net 名，或 `<hidden>` | `.../ack_emit_bitmap_q_reg[2]/Q` |

行的四种形态：

| 形态 | 特征 | 处理 |
| --- | --- | --- |
| 完整行 | Location + Delay type + 数值 + 资源 | 直接解析 |
| 折行 | Location + Delay type 单独一行，数值在下一行 | pending 合并（任何超宽行都会折，不限于 CARRY8） |
| pin 行 | 有 Location/Delay type 与 r/f、无 Incr/Path | 记为 `incr_ns = NULL` 的段 |
| 时钟沿行 | 只有数值与 r/f，无 Location/Delay type | 依赖 `block` 上下文 |

### 2.4 实测规模

| 指标 | synth | post_route |
| --- | --- | --- |
| 文件大小 / 行数 | 36.9 MB / 310,399 | 208.7 MB / 1,577,594 |
| 最长行 | 339 字符 | 3,455 字符（被最长时钟名撑开的表格） |
| 时钟数 / clock pair 节 | 3 / 9 | 77 / 109 |
| 详细路径 | 5,616（1,000 违例） | 22,973（2,248 违例） |
| 明细行 | 124,438 | 587,939 |
| Pulse Width 行 | 9,001 | 41,676 |
| Timing Exception 路径 | 2,611 | 2,575 |
| source / dest 为 `<hidden>` | 387 / 1,309 | 4,795 / 5,942 |
| WNS / TNS | -4.035 / -11,234.204 | -7.202 / -252,393.750 |
| 失败端点 / 总端点 | 4,191 / 212,876 | 49,613 / 605,794 |

> 明细是采样：synth 报告 1,000 条违例明细的 slack 合计为 -3,256 ns，而 TNS 是
> -11,234 ns。任何"全设计"口径的统计必须用汇总表，不能用明细求和代替。

## 3. 解析模型

### 3.1 状态机

按"章节 → clock pair → 块 → 行"四层状态推进，单遍流式处理，不加载整文件：

```text
FILE
 ├─ 头部元数据 ───────────────► report
 ├─ Timer Settings ───────────► timer_setting
 ├─ check_timing ─────────────► check_timing
 ├─ Design/Clock/Intra/Inter/Other ──► report / clock / timing_summary
 └─ Timing Details
     ├─ "From Clock:" / "  To Clock:" ──► 新 clock_pair
     ├─ "Setup :" / "Hold  :" / "PW    :" ──► clock_pair 汇总列
     ├─ "Max Delay Paths" / "Min Delay Paths" ──► 路径模式
     │    ├─ "Slack (...)" ──────────► 新 path
     │    ├─ 头字段 ─────────────────► path 列
     │    ├─ "    Location ..." ─────► 明细表开始
     │    ├─ 双列分隔线 #1 / #2 ─────► block = data / capture_clock
     │    ├─ "required/arrival time" ─► path.required_ns / arrival_ns
     │    └─ "slack" ────────────────► 路径结束，写库
     └─ "Pulse Width Checks" ────────► pw_check
```

注意：`(clock ...)` 行在 launch 与 capture 段各出现一次，**不能**用它作为状态切换
依据；应使用双列分隔线计数。

### 3.2 行语法规则

| 目标 | 规则 |
| --- | --- |
| 头字段 | `^  ([A-Za-z][A-Za-z0-9 ()/^.,+-]*?)\s*:\s+(.*)$`，字段名固定 2 空格缩进 |
| 续行 | 头字段后紧跟的 `(` 开头行，归属上一个字段（Source/Destination 的 cell 类型） |
| 子项 | `^    (\S.*?)\s*:\s+(-?[\d.]+)ns`，字段名内部可能有多空格（`Source Clock Delay      (SCD)`） |
| 明细数值 | 在行前 72 列内匹配 `(-?\d+\.\d{3})\s+(-?\d+\.\d{3})`，避开资源名中的数字 |
| 站点/延迟类型 | 固定列：`line[0:21]` 为 Location，`line[21:num_start]` 为 Delay type |
| 尾部三列 | 数值之后依次为 r/f、可选 PBlock、资源名；用 2 个以上空格切分 |
| 汇总表行 | 用 2 个以上空格切分（列宽会被最长时钟名撑开，不能用固定列） |
| `From/To Clock` | `^From Clock:\s*(.*)$`、`^  To Clock:\s*(.*)$` |

### 3.3 必须处理的格式陷阱

| 陷阱 | 现象 | 处理 |
| --- | --- | --- |
| 汇总表超宽 | post 报告存在 3,455 字符的行 | 只按 2+ 空格切分，不做定宽假设 |
| 明细折行 | 长 `Delay type` 使数值行下移 | pending 机制：保存上一行的 Location/Delay type |
| PBlock 列 | post-route 多一列，可为空 | 数值后按 2+ 空格切分，两段即 PBlock+资源，一段为资源 |
| `arrival time` 为负 | `required + arrival = slack` 而非相减 | 入库时取反，保留原值到 `import_issue` 或原始字段 |
| `<hidden>` | 源/目的/资源名被隐藏 | 记录 `src_hidden`/`dst_hidden`，禁止用名字做唯一标识 |
| 采样 | `-max_paths 1000` 是每组上限 | 汇总表独立入库，明细标注 `sample` 语义 |
| 例外路径 | `Data Path Delay` ≠ 逐段增量之和 | 存 `timing_exception`；校验时排除 |
| 编号重复 | 时钟名含 `[ ] / .`，路径组名可能是完整实例路径 | 一律按字符串处理，不做标识符假设 |

### 3.4 容错与回执

解析器不因单个未知行中断：

- 未知章节、未知字段：跳过并写 `import_issue`（`code='unknown_section'` / `'unknown_field'`）；
- 解析失败的行：写 `import_issue`，保留行号与原文；
- 导入结束后跑不变量校验，失败项同样写 `import_issue`：

| 校验 | 期望 |
| --- | --- |
| `slack == required + printed_arrival` | 全部路径通过（实测 100%） |
| `sum(data 段 incr) == data_path_delay` | 无 exception 路径通过（差异仅 3 位小数舍入） |
| 路径数 == `grep -c '^Slack ('` | 完全一致 |
| `path.seq` 连续 | 1..N |

即使 `keep_detail=0`，上述校验仍在导入过程中于内存里执行，只是不写入 `path_segment`。

## 4. 数据库设计

### 4.1 设计原则

1. **一报告一库**：一次导入生成一个 `.db`，所有表描述同一份报告，不出现 `report_id`。
2. **三层拆分**：1 条路径摘要（`path`）+ N 条明细（`path_segment`）+ 1 组逻辑级数
   （`logic_cell`）。日常筛选只查 `path` 单表。
3. **汇总与明细分离**：TNS/WNS 以汇总表为准，明细只作为样本来分析。
4. **名字维度化**：pin/net 全名重复度极高（实测 80,306 个不同资源对应 587,939 条
   明细），用 `resource` 维表 + 整数 id，控制库体积。
5. **派生字段入库时算**：层次前缀、`path_key`、route 占比等，避免查询时递归或现场计算。
6. **可追溯**：每条路径保存 `line_start` / `line_end`，可回查原始报告。

### 4.2 实体关系

```text
report（单行）
  ├── clock
  ├── timing_summary
  ├── clock_pair
  ├── pw_check
  ├── check_timing
  ├── timer_setting
  ├── import_issue
  └── path ──< path_segment >── resource
          ├── logic_cell
          └── hier_prefix
```

`path.seq` 是路径在报告中的出现顺序，直接作为主键；明细通过 `path_seq` 关联。

### 4.3 表清单

| 表 | 粒度 | 用途 |
| --- | --- | --- |
| `report` | 1 行 | 报告元数据 + Design Timing Summary |
| `timer_setting` | 设置项 | 多角分析等开关 |
| `check_timing` | 检查类别 | 约束完整性背景 |
| `clock` | 时钟 | 周期、频率、生成关系 |
| `timing_summary` | 汇总行 | intra / inter / other 与全设计汇总 |
| `clock_pair` | 路径节 | 每节的 Setup/Hold/PW 汇总 |
| `path` | 路径 | 主搜索表 |
| `path_segment` | 明细行 | 拓扑钻取、延迟分解 |
| `logic_cell` | 路径 × 单元类型 | 逻辑级数构成 |
| `pw_check` | 脉宽检查行 | 时钟质量 |
| `resource` | pin/net 名字 | 维度表 |
| `hier_node` | 层次前缀 | 前缀字典，避免重复存储 |
| `hier_prefix` | 路径 × 侧 × 层级 | 按模块聚合 |
| `import_issue` | 问题 | 容错回执 |

### 4.4 DDL

```sql
PRAGMA foreign_keys = ON;

-- ── 报告级（单行）────────────────────────────────────────────────────
CREATE TABLE report (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    source_file     TEXT    NOT NULL,
    file_sha256     TEXT    NOT NULL UNIQUE,
    file_size       INTEGER,
    stage           TEXT,        -- synth / post_place / post_route / manual
    tool_version    TEXT,
    run_date        TEXT,        -- ISO8601
    host            TEXT,
    command         TEXT,
    design_name     TEXT,
    device          TEXT,
    speed_file      TEXT,
    design_state    TEXT,        -- Synthesized / Physopt postRoute
    temp_grade      TEXT,
    delay_type      TEXT,        -- max / min（从 command 解析）
    max_paths       INTEGER,     -- 从 command 解析
    wns             REAL,
    tns             REAL,
    tns_failing     INTEGER,
    tns_total       INTEGER,
    wpws            REAL,
    tpws            REAL,
    tpws_failing    INTEGER,
    tpws_total      INTEGER,
    constraints_met INTEGER,     -- 1 / 0
    n_paths         INTEGER,
    n_pw_checks     INTEGER,
    imported_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE timer_setting (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE check_timing (
    seq      INTEGER PRIMARY KEY,
    category TEXT    NOT NULL,   -- no_clock / no_input_delay ...
    count    INTEGER,
    severity TEXT,               -- HIGH / MEDIUM / LOW
    detail   TEXT
);

CREATE TABLE clock (
    name      TEXT PRIMARY KEY,
    parent    TEXT,              -- 由缩进推断，报告未显式给出
    indent    INTEGER,
    waveform  TEXT,              -- "{0.000 2.000}"
    period_ns REAL,
    freq_mhz  REAL
);

CREATE TABLE timing_summary (
    id                INTEGER PRIMARY KEY,
    kind              TEXT    NOT NULL
                      CHECK (kind IN ('design', 'intra', 'inter', 'other')),
    path_group        TEXT,       -- other 表才有
    from_clock        TEXT,
    to_clock          TEXT,
    wns               REAL,
    tns               REAL,
    failing_endpoints INTEGER,
    total_endpoints   INTEGER,
    wpws              REAL,
    tpws              REAL,
    tpws_failing      INTEGER,
    tpws_total        INTEGER
);

CREATE TABLE clock_pair (
    section_seq   INTEGER PRIMARY KEY,   -- Timing Details 内第几个节
    from_clock    TEXT,
    to_clock      TEXT,
    setup_failing INTEGER, setup_wns REAL, setup_tns REAL,
    hold_failing  INTEGER, hold_wns  REAL, hold_tns  REAL,
    pw_failing    INTEGER, pw_wns    REAL, pw_tns    REAL
);

-- ── 路径与明细 ────────────────────────────────────────────────────────
CREATE TABLE path (
    seq                INTEGER PRIMARY KEY,    -- 报告内序号 1..N
    line_start         INTEGER,
    line_end           INTEGER,
    section_seq        INTEGER REFERENCES clock_pair(section_seq),
    section_from_clock TEXT,
    section_to_clock   TEXT,

    path_group         TEXT,
    path_type          TEXT,                   -- Setup / Hold / Recovery / Removal
    max_min            TEXT,                   -- Max / Min
    corner             TEXT,                   -- Slow / Fast

    slack              REAL,
    slack_status       TEXT,                   -- MET / VIOLATED
    requirement_ns     REAL,
    requirement_text   TEXT,

    data_delay_ns      REAL,
    logic_ns           REAL,
    route_ns           REAL,
    logic_pct          REAL,
    route_pct          REAL,
    logic_levels       INTEGER,
    logic_breakdown    TEXT,                   -- 原始 "CARRY8=12 LUT2=1 ..."

    skew_ns            REAL,
    dcd_ns             REAL,
    scd_ns             REAL,
    cpr_ns             REAL,
    uncertainty_ns     REAL,
    tsj_ns             REAL,
    tij_ns             REAL,
    dj_ns              REAL,
    pe_ns              REAL,
    clk_net_src_ns        REAL,
    clk_net_src_route_ns  REAL,
    clk_net_src_dist_ns   REAL,
    clk_net_dst_ns        REAL,
    clk_net_dst_route_ns  REAL,
    clk_net_dst_dist_ns   REAL,

    source_pin         TEXT,
    source_cell        TEXT,                   -- 最后一级实例名
    source_cell_type   TEXT,                   -- FDRE / RAMD32 ...
    source_clock       TEXT,
    dest_pin           TEXT,
    dest_cell          TEXT,
    dest_cell_type     TEXT,
    dest_clock         TEXT,
    src_hidden         INTEGER NOT NULL DEFAULT 0,
    dst_hidden         INTEGER NOT NULL DEFAULT 0,
    src_site           TEXT,                   -- 首段 cell 的 Location
    dst_site           TEXT,                   -- 末段 cell 的 Location

    timing_exception   TEXT,                   -- "MaxDelay Path 4.000ns -datapath_only"
    cdc_note           TEXT,

    arrival_ns         REAL,                   -- 已取反的到达时间
    required_ns        REAL,

    path_key           TEXT,                   -- 非唯一，仅用于人工对照
    fanout_max         INTEGER,                -- 路径上最大 net 扇出
    n_seg              INTEGER,
    n_data_seg         INTEGER,
    keep_detail        INTEGER NOT NULL DEFAULT 0   -- 0 = 不保存明细
);

CREATE TABLE resource (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,   -- 完整 pin/net 名，或 '<hidden>'
    leaf   TEXT,                   -- 最后一段，便于按叶名检索
    hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE path_segment (
    path_seq    INTEGER NOT NULL REFERENCES path(seq) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    block       TEXT    NOT NULL
                CHECK (block IN ('launch_clock', 'data', 'capture_clock')),
    is_net      INTEGER NOT NULL DEFAULT 0,
    site        TEXT,              -- Location 列
    cell_type   TEXT,              -- FDRE / LUT6 / CARRY8 / NULL(net)
    pin_func    TEXT,              -- Prop_FDRE_C_Q / Setup_FDRE_C_D / NULL
    fanout      INTEGER,           -- net (fo=N)
    incr_ns     REAL,
    cum_ns      REAL,
    transition  TEXT,              -- r / f
    pblock      TEXT,
    resource_id INTEGER REFERENCES resource(id),
    PRIMARY KEY (path_seq, seq)
);

CREATE TABLE logic_cell (
    path_seq  INTEGER NOT NULL REFERENCES path(seq) ON DELETE CASCADE,
    cell_type TEXT    NOT NULL,
    count     INTEGER NOT NULL,
    PRIMARY KEY (path_seq, cell_type)
);

CREATE TABLE hier_node (
    id     INTEGER PRIMARY KEY,
    prefix TEXT NOT NULL UNIQUE
);

CREATE TABLE hier_prefix (
    path_seq  INTEGER NOT NULL REFERENCES path(seq) ON DELETE CASCADE,
    side      TEXT    NOT NULL CHECK (side IN ('source', 'dest')),
    level     INTEGER NOT NULL,     -- 1 表示第一级组件
    prefix_id INTEGER NOT NULL REFERENCES hier_node(id)
);

-- ── 其余报告内容 ──────────────────────────────────────────────────────
CREATE TABLE pw_check (
    id          INTEGER PRIMARY KEY,
    seq         INTEGER NOT NULL,
    section_seq INTEGER,
    clock_name  TEXT,
    check_type  TEXT,          -- Min Period / Low Pulse Width / High Pulse Width
    corner      TEXT,          -- n/a / Slow / Fast
    lib_pin     TEXT,
    ref_pin     TEXT,
    required_ns REAL,
    actual_ns   REAL,
    slack_ns    REAL,
    site        TEXT,
    pin_id      INTEGER REFERENCES resource(id)
);

CREATE TABLE import_issue (
    id       INTEGER PRIMARY KEY,
    line_no  INTEGER,
    severity TEXT,            -- warning / error
    code     TEXT,            -- unknown_section / unknown_field / parse_failed / invariant
    message  TEXT,
    raw      TEXT
);
```

### 4.5 索引

```sql
CREATE INDEX idx_path_slack ON path(slack);
CREATE INDEX idx_path_group ON path(path_group, path_type, slack);
CREATE INDEX idx_path_src   ON path(source_cell);
CREATE INDEX idx_path_dst   ON path(dest_cell);

CREATE INDEX idx_seg_res    ON path_segment(resource_id, path_seq);
CREATE INDEX idx_seg_cell   ON path_segment(cell_type);

CREATE INDEX idx_pw_slack   ON pw_check(slack_ns);
CREATE INDEX idx_summary    ON timing_summary(kind);
CREATE INDEX idx_hier       ON hier_prefix(side, level, prefix_id);
```

说明：

- `path.seq`、`path_segment(path_seq, seq)`、`logic_cell(path_seq, cell_type)` 的主键
  已自带索引，不再重复建单列索引。
- 不对 `source_pin` / `dest_pin` / `resource.name` 建普通索引去支撑 `LIKE '%x%'`：
  SQLite 用不上。按资源检索走 `resource_id`，按名字模糊检索走 `resource.leaf`
  或后续 FTS5。
- 单库对应单报告，所有索引都不需要报告维度前缀。

### 4.6 视图

```sql
CREATE VIEW v_path_fail AS
SELECT * FROM path WHERE slack < 0;

CREATE VIEW v_segment AS
SELECT s.*, r.name AS resource
FROM path_segment s
LEFT JOIN resource r ON r.id = s.resource_id;

CREATE VIEW v_group_stats AS
SELECT path_group,
       COUNT(*)                                   AS n_paths,
       SUM(slack < 0)                             AS n_fail,
       MIN(slack)                                 AS wns,
       SUM(CASE WHEN slack < 0 THEN slack END)    AS tns_sample,
       AVG(route_pct)                             AS avg_route_pct
FROM path
GROUP BY path_group;

CREATE VIEW v_module_stats AS
SELECT h.side,
       h.level,
       h.prefix,
       COUNT(*)                                    AS n_paths,
       MIN(p.slack)                                AS wns,
       SUM(CASE WHEN p.slack < 0 THEN p.slack END) AS tns_sample
FROM path p
JOIN hier_prefix h ON h.path_seq = p.seq
GROUP BY h.side, h.level, h.prefix;
```

`tns_sample` 明确表示"采样明细的负 slack 之和"，与 `report.tns` 不是同一口径。

## 5. 派生字段

| 字段 | 计算方式 | 说明 |
| --- | --- | --- |
| `arrival_ns` | `-printed_arrival` | 报告打印为负值 |
| `source_cell` | `source_pin` 去掉最后一段 | `<hidden>` 时置 NULL |
| `source_cell_type` | 从头字段续行 `(rising edge-triggered cell FDRE ...)` 提取 | 隐藏路径同样可用 |
| `path_key` | `source_pin \| dest_pin \| path_group \| path_type \| max_min` | 仅作匹配键，**不设唯一约束** |
| `logic_pct` / `route_pct` | 从 `Data Path Delay` 括号内百分比直接取 | 避免二次除法误差 |
| `fanout_max` | `max(segment.fanout)`，导入时在内存中计算 | 高扇出是布线延迟的常见根因；不依赖明细是否入库 |
| `src_site` / `dst_site` | launch 段最后一个 cell 行 / capture 段第一个 cell 行的 `site`，导入时计算 | 用于跨 SLR、布局分析；不依赖明细是否入库 |
| `hier_prefix` | 按 `/` 逐级拆 `source_pin` / `dest_pin`，前缀存 `hier_node` 字典 | 入库时预计算，避免查询时递归 |

`path_key` 不能设唯一约束：实测 post 报告 22,973 条路径只有 17,128 个不同的
`(source, dest, group, type)` 组合，其中 4,007 条路径的源和目的都是 `<hidden>`。
加 `UNIQUE` 会静默丢掉 5,845 条。

由于一库一报告，`path_key` 只用于同一报告内的人工对照和跨库手工比对，不承担
跨报告 diff 的职责。

## 6. 典型查询

### 6.1 最差路径

```sql
SELECT seq, path_group, ROUND(slack, 3) AS slack, logic_levels, ROUND(route_pct, 1) AS route_pct
FROM path
WHERE slack < 0
ORDER BY slack
LIMIT 100;
```

### 6.2 按 path group 汇总

```sql
SELECT path_group,
       COUNT(*)                                AS n_paths,
       SUM(slack < 0)                          AS n_fail,
       ROUND(MIN(slack), 3)                    AS wns,
       ROUND(AVG(CASE WHEN slack < 0 THEN route_pct END), 1) AS avg_route_pct
FROM path
GROUP BY path_group
ORDER BY wns;
```

### 6.3 按模块聚合（走预计算前缀）

```sql
SELECT prefix, COUNT(*) AS n_paths, ROUND(MIN(slack), 3) AS wns
FROM v_module_stats
WHERE side = 'source' AND level = 4
GROUP BY prefix
HAVING n_paths >= 5
ORDER BY wns
LIMIT 20;
```

### 6.4 经过某条 net 的路径（拓扑检索）

```sql
SELECT p.seq, ROUND(p.slack, 3) AS slack, p.source_cell, p.dest_cell
FROM path p
JOIN path_segment s ON s.path_seq = p.seq
JOIN resource r     ON r.id = s.resource_id
WHERE r.leaf LIKE '%ack_emit_bitmap_q__0%'
ORDER BY p.slack;
```

### 6.5 逻辑级数高且布线占比大

```sql
SELECT seq, ROUND(slack, 3) AS slack, logic_levels, ROUND(route_pct, 1) AS route_pct
FROM path
WHERE logic_levels >= 30
  AND route_pct > 70
ORDER BY slack;
```

### 6.6 高扇出热点

```sql
SELECT r.name, s.fanout, COUNT(DISTINCT s.path_seq) AS n_paths
FROM path_segment s
JOIN resource r ON r.id = s.resource_id
WHERE s.is_net = 1 AND s.fanout >= 100
GROUP BY r.name, s.fanout
ORDER BY n_paths DESC
LIMIT 20;
```

### 6.7 共享瓶颈 cell

```sql
SELECT r.name, COUNT(DISTINCT s.path_seq) AS n_paths
FROM path_segment s
JOIN resource r ON r.id = s.resource_id
JOIN path p     ON p.seq = s.path_seq
WHERE p.slack < 0 AND s.is_net = 0
GROUP BY r.name
ORDER BY n_paths DESC
LIMIT 20;
```

## 7. 单报告范围与跨报告对照

### 7.1 工具内不做对比

一个 `.db` 只描述一份报告，工具不提供 `diff`、`ls`、`verify` 等跨报告命令。
这样做的原因是：

- 报告本身是采样，两次运行的端点集合不同，"没出现"与"变好了"无法区分；
- 多数对比需求可以直接用 SQL 表达，没必要固化成一个语义模糊的命令；
- 工具定位是"把报告变成可查询的数据"，对比属于上层分析。

### 7.2 需要对照两次运行时

保留两个库文件，用 `sqlite3` 直接 `ATTACH`：

```bash
sqlite3 run_a/timing.db <<'SQL'
ATTACH 'run_b/timing.db' AS b;
SELECT a.path_key,
       a.slack AS slack_old,
       b.slack AS slack_new,
       ROUND(b.slack - a.slack, 3) AS delta
FROM main.path a
JOIN b.path b USING (path_key)
WHERE b.slack < a.slack - 0.2
ORDER BY delta
LIMIT 50;
SQL
```

注意：`path_key` 含 `<hidden>` 的路径无法互相匹配；两次运行的 `design_name` 不同时，
结果没有可比性。

## 8. 导入工具

工具名为 `vtdb`（Vivado Timing DataBase），是独立工具，不属于 REMUS 构建体系，
后续会迁移到独立仓库。

命令行接口——命令划分、选项、输出格式、退出码——见
[命令行接口设计](cli.md)，本文不重复。

导入流程本身：

1. 计算 `file_sha256`，目标库已存在且未加 `--force` 时拒绝覆盖；
2. 单遍流式解析，按表分批 `executemany`，单事务提交；
3. 解析过程同步写 `resource` 维表（名称 → id）；
4. 计算派生字段，展开 `hier_prefix`；
5. 跑第 3.4 节的不变量校验，失败项写 `import_issue` 并在摘要中报告；
6. 输出摘要：路径数、明细数、PW 数、告警数、耗时、库体积。

导入单份 200 MB 报告的目标准入：**10 秒以内、内存 < 200 MB**。

## 9. 性能与容量

### 9.1 实测（`vtdb` 0.1.0）

| 项 | 实测值 |
| --- | --- |
| 解析 `synth_timing.rpt`（36.9 MB） | 2.0 s |
| 解析 `post_route_timing.rpt`（208.7 MB） | 9.2 s |
| 导入峰值内存 | 135 MB |
| 库体积：synth / post（`--keep-detail all`） | 39 MB / 186 MB |
| 库体积：post（`--keep-detail negative`） | 91 MB |
| 库体积：post（`--keep-detail none`） | 76 MB |
| `top --fail-only -n 20` | 约 30 ms |
| `stats --by group` | 约 50 ms |
| `stats --by module --level 3` | 约 50 ms |
| `top --net ... --fail-only` | 约 40 ms |

查询耗时包含 Python 进程启动，实际 SQL 执行在 10 ms 量级。

### 9.2 已实现的优化

| 手段 | 效果 |
| --- | --- |
| 流式写入（每 2 万行提交） | 峰值内存 135 MB，而不是把整份报告读入内存 |
| `resource` 维表 + id | post 报告 72,564 个不同 pin/net 名只存一次 |
| `hier_node` 前缀字典 | 341,834 条层次前缀压到 17,487 个节点 |
| 入库时预计算层次前缀 | 模块聚合 50 ms（临时递归 CTE 方案需约 80 s） |
| 可选的明细策略 | 186 / 91 / 76 MB 三档，按需选择 |

## 10. 兼容性与演进

- `schema_version` 写入 `report` 表或 `PRAGMA user_version`，导入器在版本不匹配时拒绝写入。
- 报告中出现未知章节时，不中断导入，写入 `import_issue` 并继续；这样 Vivado 小版本
  新增章节不会导致整份报告失败。
- 字段名兼容：`report_timing_summary` 的输出在 2023.2 至 2024.2 间基本稳定，但仍应
  维护一张"报告字段 → 列名"的映射表，未知字段进入 `import_issue`。
- 当前两份报告是 `-delay_type max`，没有 hold 明细；schema 已预留 `path_type` 与
  `block`，未来解析 `Min Delay Paths` 不需要改表。
- `Unconstrained Paths` 章节出现在存在未约束路径时；其 slack 可能为 `inf`/`NA`，
  `path.slack` 因此允许 NULL。

## 11. 实施阶段

| 阶段 | 内容 | 产出 |
| --- | --- | --- |
| P0 | 解析器骨架 + `report` / `path` / `path_segment` / 汇总表；不变量校验 | 能完整导入两份报告并通过校验 |
| P1 | `clock` / `pw_check` / `check_timing` / `timing_exception` / `hier_prefix` / `resource` 维表 | 覆盖全部章节 |
| P2 | 查询命令与输出格式、`hier_prefix` 聚合 | 日常分析可用 |
| P3 | 接入 Vivado flow（可选），独立仓库发布 | 作为通用工具分发 |

## 12. 待讨论

1. 默认不保存明细后，`resource` 维表是否仍有必要（此时只剩 `pw_check.pin_id` 在用）？
2. `path_key` 的组成是否加入 `requirement_ns`，以区分同一端点在不同约束下的路径？
3. 是否需要在库里记录 `report_timing_summary` 之外的其他报告（如 `report_design_analysis`）？
4. 库文件命名与放置：默认跟随报告名（`post_route_timing.db`）是否够用？
