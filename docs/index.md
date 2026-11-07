# vtdb 文档

`vtdb` 把一份 Vivado `report_timing_summary` 文本报告转成一个 SQLite 数据库，
用于按属性、按拓扑、按模块查询路径。一个数据库对应一份报告，不做多报告管理。

当前版本：`0.1.0`。

## 文档地图

| 文档 | 内容 | 主要读者 |
| --- | --- | --- |
| [使用指南](guides/usage.md) | 安装、快速开始、命令配方、排障流程 | 使用者 |
| [数据模型](design/database.md) | 报告结构、解析规则、SQLite schema、派生字段 | 维护者 |
| [命令行接口](design/cli.md) | 命令、选项、输出格式、退出码 | 使用者、维护者 |
| [改进评估](design/improvements.md) | 已知可用性问题、改动量与接口规约 | 维护者 |
| [打包与分发](development/packaging.md) | uv / pip / pipx / zipapp / 单文件二进制 | 发布者 |
| [迁移到独立仓库](development/extraction.md) | 从 REMUS 拆出的步骤与检查清单 | 维护者 |

## 推荐阅读顺序

- 只想用工具：读[使用指南](guides/usage.md)，遇到问题时查[命令行接口](design/cli.md)。
- 想改解析器或 schema：先读[数据模型](design/database.md)，再读[命令行接口](design/cli.md)。
- 想规划下一版：读[改进评估](design/improvements.md)。
- 想发布或拆分仓库：读[打包与分发](development/packaging.md)和
  [迁移到独立仓库](development/extraction.md)。

## 仓库结构

```text
vtdb/
├── src/vtdb/          # 解析器、CLI、SQL 与 schema
├── tests/             # pytest 用例与迷你报告夹具
├── docs/              # 本文档
├── packaging/         # 打单文件二进制用的入口脚本
├── pyproject.toml
└── Makefile
```

## 状态说明

`vtdb` 仍是 0.1.0 草案：接口在 1.0 之前可能调整，文档中标注“草案”的部分以代码为准。
