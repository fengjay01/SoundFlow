# 环境与路径分层

本文件随仓库分发，是「一切写入都必须留在仓库根内」这条规则的可执行说明。
文中所有路径都相对仓库根 `<repo>/`，**不含盘符或机器路径**；clone 到任何位置、任何操作系统都成立。

> 为什么不写在 `docs/`：`docs/` 在 `.gitignore` 中被整体排除（本地规划文档），
> 而 `setup/` 已有 `!setup/*.md` 白名单例外，环境契约文档放这里才能随仓库分发。

## 1. 唯一的判据

**这个目录里会不会出现模型权重文件？**

- 会 → **模型层** `data/`
- 不会 → **环境层** `.venv/`

由此得到缓存变量的归属：`HF_HOME` / `TORCH_HOME` / `MODELSCOPE_CACHE` 可能落下权重，
归 `data/models/_cache/`；`PIP_CACHE_DIR` / `CARGO_HOME` / `npm_config_cache` / `PNPM_HOME` /
`XDG_CACHE_HOME` / `TMP*` 只放包与编译缓存，归 `.venv/`。

再加一条实现约束：**任何默认落点都由代码位置推导**（Python `__file__`、
Rust `CARGO_MANIFEST_DIR`、Node `import.meta.url`），不把用户目录当默认值。
环境变量只是覆盖手段，不是保证手段。

## 2. 目录树

```
<repo>/                              仓库根：所有写入的边界
├─ src/ src-tauri/src/ python/ tests/ scripts/ setup/   源码（受版本控制）
├─ .venv/                          环境层：可整体删除重建，不含权重
│   ├─ bootstrap/                  引导 venv（dev:setup 生成，几 MB）
│   ├─ python/runtime-envs/        app 下载的 CUDA/CPU 运行环境（唯一）
│   ├─ cache/{pip,cargo,npm,pnpm,xdg}/
│   ├─ tmp/                        TMP / TEMP / TMPDIR
│   └─ node/pnpm-store/            前端依赖内容寻址 store
├─ data/                           运行态 + 模型层：可整体备份/迁移
│   ├─ settings/ logs/ outputs/ temp/ editor-projects/ debug/
│   └─ models/
│       ├─ _tool_models/<tool>/models/   应用下载器写入（歌词 / 通用 ASR）
│       ├─ _cache/{hf,torch,modelscope}/ 第三方库兜底（正常情况为空）
│       └─ legacy_vr/ vocal/             早期人声分离模型（历史布局，见 §6）
├─ node_modules/ src-tauri/target/ dist/    可重建产物
└─ _debug_output/                 调试产物（被 gitignore，不进 commit）
```

`.venv/` 和 `data/` 都不进 git，clone 后由命令生成。二者互不夹带：删掉 `.venv/`
不丢任何模型，备份 `data/` 不会带上环境缓存。

## 3. 生命周期：谁在什么阶段写什么

| 阶段 | 命令 / 触发 | 写入位置 | 由谁决定 |
|---|---|---|---|
| clone | `git clone` | 仅源码 | git |
| 建前端依赖 | `corepack pnpm dev:setup:desktop` | `node_modules/`、`.venv/node/pnpm-store/` | `.npmrc` 的 `store-dir`（相对） |
| 建 Python 引导环境 | 同上 | `.venv/bootstrap/` | `scripts/dev-env.mjs bootstrap()` |
| 注入环境变量 | 每次 `dev:desktop` / worker / `test:*` | 创建 `.venv/cache/*`、`.venv/tmp`、`data/models/_cache/*` | `pinnedEnv()` |
| 编译 | `pnpm dev:desktop` | `src-tauri/target/`、`dist/` | cargo / vite |
| 启动 | Tauri | `data/{settings,logs,outputs,temp,editor-projects,debug}/` | `src-tauri/src/storage.rs`（编译期 `CARGO_MANIFEST_DIR`） |
| 下载运行环境 | app「设置 → 运行环境」 | `.venv/python/runtime-envs/` | `storage.rs::runtime_envs_dir()` |
| 下载模型 | app「模型管理」/ 歌词页 | `data/models/_tool_models/...` | `settings.modelDir` |
| 第三方库自行下载 | 推理时本地未命中 | `data/models/_cache/...` | `HF_HOME` / `TORCH_HOME` / `MODELSCOPE_CACHE` |
| 调试产物 | 开发脚本 | `_debug_output/{screenshots,logs,diag,tmp,...}/` | `scripts/debug_paths.py` |

前置条件见 `setup/toolchain.json`：Node 22 + pnpm 10.33.2、Rust stable + Tauri 平台依赖、
Python 3.12（桌面/worker 开发）。`dev:setup:desktop` 只生成几 MB 的 bootstrap venv，
**不会**自动安装 torch/CUDA；那一步在 app 内完成。

### 3.1 新 clone 首次启动到底会生成什么

前提：clone 后 `.venv/`、`data/`、`node_modules/`、`src-tauri/target/`、`python/preinstalled_plugins/` **都不存在**。

**阶段 A — `corepack pnpm dev:setup:desktop`**

| 生成物 | 内容 | 由谁创建 |
|---|---|---|
| `node_modules/` | `.pnpm/` 实体包、`.bin/` shim、`.modules.yaml` 等元数据 | `pnpm install --frozen-lockfile` |
| `.venv/node/pnpm-store/v10/` | 内容寻址 store（文件硬链接到 `node_modules/.pnpm`） | `.npmrc` 的 `store-dir` |
| `.venv/bootstrap/` | `pyvenv.cfg`、`Scripts/python.exe`（macOS/Linux 为 `bin/python`）、pip、`requests[socks]` | `bootstrap()` → `python3.12 -m venv` + `pip install` |
| `.venv/cache/{pip,cargo,npm,pnpm,xdg}/`、`.venv/tmp/`、`.venv/python/runtime-envs/` | **空目录**，先建好路径 | `pinnedEnv({createDirs:true})` |
| `data/models/_cache/{hf,torch,modelscope}/` | **空目录**，第三方库兜底位 | 同上 |

末尾的 `check` 阶段只读，不写文件。

**阶段 B — `corepack pnpm dev:desktop` 首次启动**

| 生成物 | 内容 | 触发点 |
|---|---|---|
| `python/preinstalled_plugins/` | `manifest.json` + 4 个预装插件目录（约 2.5 MB，被 gitignore） | `scripts/tauri.mjs` → `prepare-preinstalled-plugins.py`；按 `python/plugin-manifest.json` 的 repository/ref 拉取，**首次会联网** |
| `.cache/preinstalled-plugins/` | 插件仓库下载缓存 | 同上 |
| `node_modules/.vite/` | Vite 预构建缓存 | `pnpm dev`（devUrl 1420） |
| `src-tauri/target/debug/` | Rust 编译产物，含 `incremental/`（GB 级） | cargo |
| `data/{settings,models,outputs,editor-projects,logs,temp}/` | 空目录骨架 | `storage.rs::ensure_app_directories()` |
| `data/logs/current-session.log`、`pymss-studio.log` | 会话日志 | `session_log.rs` |

dev 模式用 `devUrl`，**不会生成仓库根 `dist/`**；`dist/` 只在 `pnpm build` 时出现。

**阶段 C — 首次使用某功能才生成（启动时不会出现）**

- `data/settings/*.json`（`app.json`、`model-cache.json`、`tasks.json` …）：首次保存设置 / 下载模型时按 `store_file_name()` 写入；只读且文件不存在时返回空值，**不会主动创建**。
- `.venv/python/runtime-envs/{cuda,cpu}/`：app「设置 → 运行环境」安装（GB 级）。
- `data/models/_tool_models/<tool>/models/`：app「模型管理」下载（GB 级）。
- `data/debug/`、`data/plugins/`、`data/logs/latest-diagnostic-report.md`、`.venv/tmp/` 下的临时 wav/json：按需。
- `_debug_output/`：开发调试脚本首次写入时由 `scripts/debug_paths.py` 创建。

**明确不会自动生成**：任何模型权重、torch/CUDA 运行环境、Windows 本机历史工具链
（`.venv/python/asr/`、`.venv/rust/`、`.venv/mingw64/`、`.venv/node/runtime/`）、
`python-runtime/`、`data-vst-lab/`、`data/dist/`（旧残留）、仓库根 `dist/`。

**本机例外**：若存在 2026-11-08 的历史自包含工具链目录，`pinnedEnv()` 会把
`PIP_CACHE_DIR` 指到 `.venv/python/pip-cache`、`CARGO_HOME` 指到 `.venv/rust/cargo`、
`RUSTUP_HOME` 指到 `.venv/rust/rustup`，并把 `mingw64` / `rust` / `node` 前置到 PATH；
新 clone 没有这些目录，一律使用 `.venv/cache/*` 与系统工具链。

验证方式：`pnpm env:paths` 打印所有注入路径；首次启动后 `git status --short` 应保持干净
（上表所有目录都在 `.gitignore` 覆盖范围内）。

## 4. 三层防护

| 层 | 实现 | 覆盖 | 作用 |
|---|---|---|---|
| **L1 代码默认值** | `python/_project_paths.py`（全部基于 `__file__`） | **任何** Python 进程，含直接运行、pytest | 根治上游 `Path.home()` 回退 |
| **L2 启动注入** | `scripts/dev-env.mjs::pinnedEnv()` 用 `force()` 钉死缓存/临时/模型缓存变量 | 官方 `dev:desktop` / worker / 测试入口 | 约束只看环境变量的第三方库 |
| **L3 自检护栏** | `scripts/paths-check.mjs` + `pnpm env:paths` + `env:doctor` 的 `paths-local` 项 | clone 后、CI、发布前 | 越界立即失败，防回归 |

`storage.rs` 是 Rust 侧唯一路径权威，并在启动 worker 时注入 `PYMSS_*` 变量
（`src-tauri/src/python/worker.rs`）。Python 与 Node 侧只做同契约的本地推导，
不重复定义第二套权威。显式 override（portable 构建、CI、自定义布局）仍然生效。

## 5. 自检命令

```bash
corepack pnpm env:doctor          # 环境体检，含 paths-local 断言
corepack pnpm env:paths           # 打印所有注入/默认路径并断言都在 <repo> 内
node scripts/dev-env.mjs env      # 查看实际注入的环境变量
git check-ignore -v _debug_output/README.md   # 调试目录必须被忽略
```

`env:paths` 若有任意路径越界，退出码为 1；`env:doctor` 会显示 `paths-local FAIL`。
`.github/workflows/ci.yml` 里已有的 `node scripts/dev-env.mjs check --tier ... --json`
步骤会自动执行同一断言（`check()` 内置 `paths-local` 项），因此 CI 无需额外改工作流。

校验「绕过启动脚本」的场景：

```bash
python -m unittest discover -s tests/python -p "test_project_paths.py" -v
```

该单测会清空所有 `PYMSS_*` 变量，断言每个默认路径仍在仓库内、显式 override 仍生效、
worker 各模块的回退函数都不再指向用户目录。

## 6. 例外与历史布局

- `python/preinstalled_plugins/` 是**运行时生成的离线插件快照**（被 `.gitignore` 排除，
  只有 `python/plugin-manifest.json` 受跟踪）。`verify_generated()` 要求该目录的文件集合与
  manifest 精确相等，多一个 `.pyc` 都会让快照失效、触发 dev 启动时联网重拉；因此它固定在
  `python/` 下、不迁进 `data/`。运行时下载的市场插件仍落在
  `.venv/python/runtime-envs/.../plugins/` 与 `data/plugins/`。
- `data/models/legacy_vr/` 与 `data/models/vocal/` 是早期人声分离模型，属于模型层但未归入
  `_tool_models/`。当前没有代码按旧路径硬编码读取，**保持原位**，只在文档中标注；
  未来归位前需重新确认插件是否按 `modelDir` 相对路径引用它们。
- `python-runtime/`（打包脚本产物）与 `data-vst-lab/`（vst-lab 专用）属于可选工具产物层，
  均被 gitignore、都留在仓库内，与 `.venv/python/runtime-envs/`（dev 运行环境）不是同一套。
- Windows 本机可能存在 2026-11-08 的自包含工具链遗留层（`.venv/python/asr/`、
  `.venv/rust/`、`.venv/mingw64/`、`.venv/node/runtime/`）。`dev-env.mjs` 会兼容它，
  但**新 clone 不会有**，也不应依赖它。
- 调试产物统一走 `scripts/debug_paths.py` 的 `debug_out()`，详见 `_debug_output/README.md`
  （该目录被忽略，clone 后由工具自动创建）。

## 7. 清理与重建

| 想做什么 | 安全做法 |
|---|---|
| 释放前端依赖空间 | 删 `node_modules/` 后 `pnpm install --offline --frozen-lockfile`（store 在项目内） |
| 释放编译空间 | 删 `src-tauri/target/` 或只删 `target/debug/incremental/`，下次构建自动重建 |
| 重置整个环境 | 删 `.venv/` 后重跑 `dev:setup:desktop`；**模型不会丢** |
| 迁移模型库 | 整体复制 `data/models/`，路径由 `settings.modelDir` 记录 |
| 排查越界写入 | 跑 `env:paths`；若报 `PYMSS_STUDIO_DATA_ROOT` 等外部变量，清理该 shell 变量后重试 |

提交代码时仍遵循项目铁律：只提交主要源码，禁用 `git add -A`；上表所有目录都在
`.gitignore` 覆盖范围内，不应出现在 `git status` 里。
