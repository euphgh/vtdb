# 迁移到独立仓库

`vtdb` 目前放在 REMUS 仓库的 `tools/vtdb/` 下，但不依赖 REMUS 的任何代码，
只使用 Python 标准库。本文给出把它拆成独立仓库的步骤。

## 1. 前提检查

迁移前确认：

- `src/vtdb/` 不 import 任何 REMUS 模块（`grep -rn "remu" src/` 只应命中注释）；
- `uv run pytest tests -q` 全绿；
- 文档已经全部在 `tools/vtdb/docs/` 下，且是纯 Markdown（不依赖 Sphinx）；
- 已选定开源许可证，并确认 PyPI 包名可用。

## 2. 迁移内容

```text
tools/vtdb/
├── src/vtdb/          → 迁移
├── tests/             → 迁移
├── docs/              → 迁移
├── packaging/         → 迁移
├── pyproject.toml     → 迁移
├── README.md          → 迁移
├── Makefile           → 迁移
├── .gitignore         → 迁移
└── uv.lock            → 迁移（建议保留，锁定开发依赖）
```

不迁移：`build/`、`dist/`、`__pycache__/`、`.pytest_cache/`、`*.egg-info/`。

## 3. 保留提交历史

推荐用 `git subtree split`，不需要额外工具：

```bash
# 在 REMUS 仓库里创建一条只包含 tools/vtdb 历史的分支
cd /path/to/remu
git subtree split -P tools/vtdb -b vtdb-standalone

# 在新仓库里接收这条分支
mkdir /path/to/vtdb && cd /path/to/vtdb
git init
git pull /path/to/remu vtdb-standalone
```

如果更在意清理历史（去掉误提交的大文件），用 `git filter-repo`：

```bash
git clone /path/to/remu vtdb-tmp
cd vtdb-tmp
git filter-repo --subdirectory-filter tools/vtdb --to-subdirectory-filter .
```

放弃历史、直接复制目录也可以，但会丢失 `git log` 中的设计演进记录。

## 4. 新仓库初始化

1. 补 `LICENSE`，并在 `pyproject.toml` 中加 `license`、`classifiers`、`[project.urls]`；
2. 把 `README.md` 调整为面向新用户（安装、三行示例、指向 `docs/`）；
3. 增加 `.github/workflows/ci.yml`：三平台 × Python 3.9/3.12，跑 `uv run pytest`；
4. 增加 release workflow：`uv build` + PyPI 发布 + 可选 PyInstaller 二进制；
5. 打第一个 tag（例如 `v0.1.0`）；
6. 在仓库设置里把默认分支设为 `main`，开启分支保护。

## 5. REMUS 侧清理

1. 删除 `tools/vtdb/` 目录（`git rm -r tools/vtdb`）；
2. 在删除提交的信息里写明新仓库地址；
3. 确认没有其他构建、CI 或文档引用该目录：

   ```bash
   grep -rn "tools/vtdb" --include=*.md --include=*.py --include=*.yml --include=*.toml .
   ```

   本轮已经移除 `docs/source/project-notes/` 下的三篇文档和索引项，正常情况下
   不会再命中。

4. 如需过渡期，可保留一个 `tools/vtdb/README.md`，内容只有一行指向新仓库，
   下一个发布周期再删除。

## 6. 迁移后验证

在新仓库里执行：

```bash
uv sync
uv run pytest tests -q
uv build
uv tool install .
vtdb --version
```

再检查文档：

- `docs/index.md` 的相对链接是否都能打开；
- 示例命令是否仍以仓库根为工作目录；
- 是否还有“REMUS”“tools/vtdb”之类的旧路径。

## 7. 回滚方案

- 迁移是一次普通提交，记录下 REMUS 侧的 commit hash；
- 如需回滚，`git revert` 该提交即可恢复目录；
- 新仓库可以归档，不影响 REMUS。

## 8. 检查清单

- [ ] 代码不依赖 REMUS
- [ ] 测试全绿
- [ ] 文档已迁入 `docs/`，无 Sphinx 语法残留
- [ ] `LICENSE`、`pyproject.toml` 元数据完整
- [ ] 历史已保留（subtree / filter-repo）
- [ ] 新仓库 CI 可运行
- [ ] PyPI 包名与密钥就绪
- [ ] REMUS 侧目录已删除，引用已清理
- [ ] 首个 tag 与 release 已发布
