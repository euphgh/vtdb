# 打包与分发

`vtdb` 是纯 Python 项目，提供四种给用户的使用方式：包管理器安装、`uv`/`pipx`
工具安装、单文件 zipapp、以及不依赖 Python 的单文件二进制。本文说明每种方式的
构建命令、适用场景和发布检查清单。

## 1. 形态对比

| 形式 | 构建/安装命令 | 目标环境 | 说明 |
| --- | --- | --- | --- |
| wheel + sdist | `uv build` | 有 Python 3.9+ | 发布到 PyPI 的标准形态 |
| `uv tool` | `uv tool install .` | 有 Python + uv | 隔离环境，推荐给使用者 |
| `pipx` | `pipx install .` | 有 Python + pipx | 与 `uv tool` 等价的隔离安装 |
| `pip` | `python -m pip install .` | 有 Python | 装入当前环境 |
| zipapp | `make zipapp` | 有 Python 3.9+ | 单个 `.pyz`，无需安装，可放 PATH |
| PyInstaller | `make binary` | 无 Python | 单个可执行文件，体积大 |

## 2. 本地开发

```bash
uv sync                      # 创建 .venv 并安装运行依赖（无第三方依赖）
uv run vtdb --version
uv run pytest tests -q
```

不装 uv 时：

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install -e .
python -m pytest tests -q
```

## 3. 构建与安装

```bash
uv build                     # dist/vtdb-0.1.0-py3-none-any.whl 与 .tar.gz

uv tool install .            # 从当前目录安装，之后可直接 vtdb ...
uvx --from . vtdb --help     # 临时运行，不落盘

pipx install .
python -m pip install .
```

安装后自检：

```bash
vtdb --version
vtdb import tests/data/mini_timing.rpt -o /tmp/mini.db --force
vtdb top --db /tmp/mini.db --fail-only
```

## 4. 发布到 PyPI

元数据已经包含 `license = MIT`、`LICENSE` 文件、`classifiers` 和
`[project.urls]`。发布前只需确认包名 `vtdb` 在 PyPI 上可用。

```bash
uv build
uv run twine check dist/*          # 或 uv tool run twine check dist/*
uv publish                          # 需要 PYPI_TOKEN
```

发布后在一个干净环境验证：

```bash
uv tool install vtdb
vtdb --version
```

## 5. 单文件 zipapp

适合"有 Python、但不想装包"的场景：

```bash
make zipapp
# 等价于：
# mkdir -p dist && python -m zipapp src -m "vtdb.cli:main" \
#     -p "/usr/bin/env python3" -o dist/vtdb.pyz

./dist/vtdb.pyz --version
install -m 755 dist/vtdb.pyz ~/.local/bin/vtdb
```

说明：

- 目标机器需要 Python 3.9+，不需要 `pip`；
- `schema.sql` 通过 `importlib.resources` 读取，已支持从 zip 内加载；
- 构建产物是普通 zip，可直接用 `unzip -l` 检查内容。

## 6. 单文件二进制（PyInstaller）

入口脚本见 `packaging/pyinstaller_entry.py`，它避免以包内 `__main__.py` 作为入口时
相对导入失效的问题。

```bash
make binary
# 等价于：
# uv tool run pyinstaller --onefile --name vtdb --paths src \
#   --add-data "src/vtdb/schema.sql:vtdb" packaging/pyinstaller_entry.py
```

Windows 上 `--add-data` 的分隔符是分号：

```powershell
uv tool run pyinstaller --onefile --name vtdb --paths src `
  --add-data "src\vtdb\schema.sql;vtdb" packaging\pyinstaller_entry.py
```

产物是 `dist/vtdb(.exe)`，放到 PATH 即可：

```bash
install -m 755 dist/vtdb ~/.local/bin/vtdb
```

注意事项：

- 体积通常 10–20 MB，因为打包了 CPython 运行时；不是静态链接的二进制；
- onefile 首次启动会解压到临时目录，启动比 wheel 安装稍慢；
- 部分杀毒软件会误报 PyInstaller 产物，发布前应做签名或说明；
- `schema.sql` 必须通过 `--add-data` 打进包，否则启动时报缺少 schema。

需要更小的原生二进制时，可以把 `make binary` 换成 Nuitka
（`uv tool run nuitka --onefile ...`），构建时间更长但启动更快。

## 7. 发布检查清单

1. 同步 `pyproject.toml` 与 `src/vtdb/__init__.py` 的版本号；
2. `uv run pytest tests -q` 全绿；
3. `uv build` 成功，`twine check` 通过；
4. 在干净虚拟环境安装 wheel 并跑通一次 `import` + `top`；
5. `make zipapp` 后跑一次 `--version` 与 `import`；
6. 需要二进制时，在 Linux / macOS / Windows 各构建一次并冒烟；
7. 打 tag、推送，再 `uv publish`；
8. 用 `uv tool install vtdb` 做最终验证。

## 8. CI 建议

一个最小的 GitHub Actions 矩阵即可覆盖常见需求：

- `test`：三平台 × Python 3.9/3.12，跑 `pytest`；
- `build`：`uv build` 并上传 wheel/sdist；
- `binary`：Linux / macOS / Windows 各跑一次 PyInstaller，上传产物；
- `release`：打 tag 时发布 PyPI 并附加二进制。
