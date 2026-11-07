PRAGMA foreign_keys = OFF;

CREATE TABLE report (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    source_file     TEXT    NOT NULL,
    file_sha256     TEXT    NOT NULL UNIQUE,
    file_size       INTEGER,
    stage           TEXT,
    tool_version    TEXT,
    run_date        TEXT,
    host            TEXT,
    command         TEXT,
    design_name     TEXT,
    device          TEXT,
    speed_file      TEXT,
    design_state    TEXT,
    temp_grade      TEXT,
    delay_type      TEXT,
    max_paths       INTEGER,
    wns             REAL,
    tns             REAL,
    tns_failing     INTEGER,
    tns_total       INTEGER,
    wpws            REAL,
    tpws            REAL,
    tpws_failing    INTEGER,
    tpws_total      INTEGER,
    constraints_met INTEGER,
    n_paths         INTEGER,
    n_segments      INTEGER,
    n_pw_checks     INTEGER,
    imported_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE timer_setting (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE check_timing (
    seq      INTEGER PRIMARY KEY,
    category TEXT    NOT NULL,
    count    INTEGER,
    severity TEXT,
    detail   TEXT
);

CREATE TABLE clock (
    name      TEXT PRIMARY KEY,
    parent    TEXT,
    indent    INTEGER,
    waveform  TEXT,
    period_ns REAL,
    freq_mhz  REAL
);

CREATE TABLE timing_summary (
    id                INTEGER PRIMARY KEY,
    kind              TEXT    NOT NULL
                      CHECK (kind IN ('design', 'intra', 'inter', 'other')),
    path_group        TEXT,
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
    section_seq   INTEGER PRIMARY KEY,
    from_clock    TEXT,
    to_clock      TEXT,
    setup_failing INTEGER, setup_wns REAL, setup_tns REAL,
    hold_failing  INTEGER, hold_wns  REAL, hold_tns  REAL,
    pw_failing    INTEGER, pw_wns    REAL, pw_tns    REAL
);

CREATE TABLE path (
    seq                INTEGER PRIMARY KEY,
    line_start         INTEGER,
    line_end           INTEGER,
    section_seq        INTEGER REFERENCES clock_pair(section_seq),
    section_from_clock TEXT,
    section_to_clock   TEXT,

    path_group         TEXT,
    path_type          TEXT,
    max_min            TEXT,
    corner             TEXT,

    slack              REAL,
    slack_status       TEXT,
    requirement_ns     REAL,
    requirement_text   TEXT,

    data_delay_ns      REAL,
    logic_ns           REAL,
    route_ns           REAL,
    logic_pct          REAL,
    route_pct          REAL,
    logic_levels       INTEGER,
    logic_breakdown    TEXT,

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
    source_cell        TEXT,
    source_cell_type   TEXT,
    source_clock       TEXT,
    dest_pin           TEXT,
    dest_cell          TEXT,
    dest_cell_type     TEXT,
    dest_clock         TEXT,
    src_hidden         INTEGER NOT NULL DEFAULT 0,
    dst_hidden         INTEGER NOT NULL DEFAULT 0,
    src_site           TEXT,
    dst_site           TEXT,

    timing_exception   TEXT,
    cdc_note           TEXT,

    arrival_ns         REAL,
    required_ns        REAL,

    path_key           TEXT,
    fanout_max         INTEGER,
    n_seg              INTEGER,
    n_data_seg         INTEGER,
    keep_detail        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE resource (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,
    leaf   TEXT,
    hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE path_segment (
    path_seq    INTEGER NOT NULL REFERENCES path(seq) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    block       TEXT    NOT NULL
                CHECK (block IN ('launch_clock', 'data', 'capture_clock')),
    is_net      INTEGER NOT NULL DEFAULT 0,
    site        TEXT,
    cell_type   TEXT,
    pin_func    TEXT,
    delay_type  TEXT,
    fanout      INTEGER,
    incr_ns     REAL,
    cum_ns      REAL,
    transition  TEXT,
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
    level     INTEGER NOT NULL,
    prefix_id INTEGER NOT NULL REFERENCES hier_node(id)
);

CREATE TABLE pw_check (
    id          INTEGER PRIMARY KEY,
    seq         INTEGER NOT NULL,
    section_seq INTEGER,
    clock_name  TEXT,
    check_type  TEXT,
    corner      TEXT,
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
    severity TEXT,
    code     TEXT,
    message  TEXT,
    raw      TEXT
);

CREATE INDEX idx_path_slack ON path(slack);
CREATE INDEX idx_path_group ON path(path_group, path_type, slack);
CREATE INDEX idx_path_src   ON path(source_cell);
CREATE INDEX idx_path_dst   ON path(dest_cell);

CREATE INDEX idx_seg_res    ON path_segment(resource_id, path_seq);
CREATE INDEX idx_seg_cell   ON path_segment(cell_type);

CREATE INDEX idx_pw_slack   ON pw_check(slack_ns);
CREATE INDEX idx_summary    ON timing_summary(kind);
CREATE INDEX idx_hier       ON hier_prefix(side, level, prefix_id);

PRAGMA user_version = 1;
