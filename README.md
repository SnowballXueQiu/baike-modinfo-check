# baike-modinfo-check

百科模组信息检查

一个用于扫描并分析 `mods/` 目录下 Minecraft Mod JAR 的多语言实现集合（TypeScript / Python / Rust）。项目以 pnpm monorepo 组织，三种实现输出一致的 JSON 结果与摘要，便于对比性能与跨语言行为一致性。

## 目录结构

```
.
├── impls/
│   ├── py/                 # Python 实现
│   │   ├── mcmod_env_checker.py
│   │   └── requirements.txt
│   ├── rs/                 # Rust 实现
│   │   ├── Cargo.toml
│   │   └── src/main.rs
│   └── ts/                 # TypeScript 实现
│       ├── package.json
│       ├── src/index.ts
│       └── tsconfig.json
├── mods/                   # 放置待扫描的 Mod JAR 文件
├── package.json            # 根脚本，统一入口
├── pnpm-workspace.yaml     # 工作空间定义
├── requirements.txt        # 根 Python 依赖聚合（引用 impls/py/requirements.txt）
├── tsconfig.json           # 根 TS 配置（include 指向 impls/ts/src）
└── .gitignore
```

## 功能概览

- 扫描 `mods/` 目录中的 JAR 文件
- 解析基础信息（如 `modId`、`displayName`）
- 基于内容与约定推断运行环境文本 `envText`（例如 client/server）
- 生成便于查询的链接字段（如 `classUrl`、`searchUrl`，视实现而定）
- 输出 JSON 数组及简要统计摘要到标准输出

提示：当前实现以通用启发式为主，Forge/Fabric 的具体元数据解析覆盖度会随实现演进而提升。

## 环境要求

- Node.js `>= 18` 与 pnpm `>= 8`
- Python `>= 3.9`（已兼容 3.9 的类型标注）
- Rust 稳定版（安装 `cargo`）

## 安装

- 安装 Node 依赖
  - 在仓库根执行：
    - `pnpm install`
- 安装 Python 依赖（使用根聚合文件）
  - 在仓库根执行：
    - `pip install -r requirements.txt`
- Rust 依赖由 `cargo` 在首次编译时自动拉取

## 运行

- TypeScript（默认扫描根 `mods/`）
  - `pnpm run ts`
- Python（默认扫描当前工作目录下的 `mods/`）
  - `pnpm run py`
- Rust（默认扫描当前工作目录下的 `mods/`）
  - `pnpm run rs`

执行后会在终端输出 JSON 数组以及简要的统计摘要。

## 自定义 mods 路径

- TypeScript：
  - 推荐直接调用子包脚本并指定目录：
  - `pnpm -C impls/ts start -- <your-mods-dir>`
- Python：
  - 支持参数 `--mods-dir`：
  - `python3 impls/py/mcmod_env_checker.py --mods-dir <your-mods-dir>`
- Rust：
  - 通过 `pnpm run rs -- <your-mods-dir>` 传参，或：
  - `cargo run --manifest-path impls/rs/Cargo.toml -- <your-mods-dir>`

## 输出示例（字段随实现演进可能调整）

```json
[
  {
    "modId": "examplemod",
    "displayName": "Example Mod",
    "envText": "client",
    "classUrl": "https://example.com/class/net.minecraft.client.Main",
    "searchUrl": "https://example.com/search?q=Example%20Mod"
  }
]
```

摘要示例：`Found 3 mods. client: 2, server: 1.`

## 开发与脚本

- 根脚本（见 `package.json`）：
  - `ts`：`pnpm -C impls/ts start -- ../../mods`
  - `py`：`python3 impls/py/mcmod_env_checker.py`
  - `rs`：`cargo run --manifest-path impls/rs/Cargo.toml`
  - `start` / `dev`：别名，运行 TypeScript 实现
- 工作区定义：`pnpm-workspace.yaml`
- 根 `tsconfig.json` 的 `include` 指向 `impls/ts/src`，用于 IDE 与工具链识别 monorepo 下的 TS 源码

## 常见问题

- TypeScript 报告“未找到输入（No inputs were found）”：
  - 根 `tsconfig.json` 已将 `include` 更新为 `impls/ts/src`，通常为编辑器误读，可忽略；子包 `impls/ts/tsconfig.json` 是编译生效的配置。
- Python 在 3.9 上类型错误：
  - 已将 `str | None` 等改为 `Optional[str]`，并在脚本中 `from typing import Optional` 兼容 3.9。
- 路径解析差异：
  - 三种实现的默认目录均为当前工作目录下的 `mods/`；根脚本已统一行为，但在复杂工作流中建议显式传入自定义路径。

## 贡献

- 欢迎提交 PR 改进解析覆盖度、性能与一致性
- 建议保持输出字段与含义一致，以便跨语言对比与测试

## 许可

- 暂未指定许可证。若需要开源发布，请在根目录添加合适的 LICENSE 文件。