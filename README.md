# vtdb

Turn one Vivado `report_timing_summary` text report into one SQLite database, so
paths can be filtered, joined and aggregated with SQL instead of `grep`.

One report per database, six commands, no service, no configuration file,
Python standard library only.

## Install

```bash
uv tool install .        # or: pipx install .
# development
uv sync && uv run vtdb --version
```

Without installing: `PYTHONPATH=src python -m vtdb --help`.
Single-file zipapp and standalone binaries are described in
[docs/development/packaging.md](docs/development/packaging.md).

## Quick start

```bash
vtdb import build/post_route_timing.rpt   # writes post_route_timing.db
vtdb top --fail-only -n 20                # worst paths
vtdb path 42                              # one path with its detail rows
vtdb stats --by group                     # aggregate by path group
vtdb stats --by module --level 3          # aggregate by hierarchy prefix
vtdb summary                             # report-level summary
vtdb sql "SELECT COUNT(*) FROM path WHERE slack < 0"
```

Query commands pick the database from `--db`, then `VTD_DB`, then the single
`*.db` file in the current directory.

## Documentation

Start at [docs/index.md](docs/index.md):

- [Usage guide](docs/guides/usage.md) — install, quick start, triage recipes
- [Data model](docs/design/database.md) — report structure, parser, SQLite schema
- [CLI reference](docs/design/cli.md) — commands, options, exit codes
- [GitHub Issues](https://github.com/euphgh/vtdb/issues) — feature planning and discussion
- [Packaging](docs/development/packaging.md) — uv / pip / zipapp / binaries

## Development

```bash
uv run pytest tests -q
make zipapp            # build dist/vtdb.pyz
```

`tests/data/mini_timing.rpt` is a trimmed report with wrapped detail rows,
summary tables and pulse width checks. `tests/data/edge_timing.rpt` covers
hidden endpoints, clock domain crossings and timing exceptions.

## License

MIT. See [LICENSE](LICENSE).
