<!-- markdownlint-disable MD013 MD033 MD041 -->

<p align="center">
  <img src="./src/assets/app-logo-unified.png" alt="SoundFlow" width="120" />
</p>

<h1 align="center">SoundFlow</h1>

<p align="center">
  本地音乐源分离工作台，<strong>并新增逐字歌词时间轴制作链路</strong>。
</p>

<p align="center">
  基于 <a href="https://github.com/pymss-project/pymss-studio">Pymss Studio</a> 构建的独立桌面项目，扩展了完整的歌词链路。
</p>

<p align="center">
  <img alt="Platform Windows" src="https://img.shields.io/badge/Platform-Windows-0078D4?style=flat-square&logo=windows11&logoColor=white" />
  <img alt="Platform macOS" src="https://img.shields.io/badge/Platform-macOS-111111?style=flat-square&logo=apple&logoColor=white" />
  <img alt="Platform Linux" src="https://img.shields.io/badge/Platform-Linux-FCC624?style=flat-square&logo=linux&logoColor=111111" />
</p>

<p align="center">
  <a href="./README.md">English</a>
  ·
  <a href="https://github.com/fengjay01/SoundFlow">GitHub 仓库</a>
  ·
  <a href="https://github.com/fengjay01/SoundFlow/issues">问题反馈</a>
  ·
  <a href="https://github.com/pymss-project/pymss-studio/releases">上游发行版</a>
  ·
  <a href="https://github.com/pymss-project/pymss">pymss 核心项目</a>
</p>

<p align="center">
  <img alt="Tauri 2" src="https://img.shields.io/badge/Tauri-2-24C8DB?style=for-the-badge&logo=tauri&logoColor=white" />
  <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3-42B883?style=for-the-badge&logo=vuedotjs&logoColor=white" />
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-5-3178C6?style=for-the-badge&logo=typescript&logoColor=white" />
  <img alt="License: AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-111827?style=for-the-badge" />
</p>

<p align="center">
  <img src="./images/screenshots/separate/batch/zh-CN-light.webp" alt="SoundFlow 分离工作台（继承自上游的界面）" width="900" />
</p>

## 项目介绍

SoundFlow 是在 [Pymss Studio](https://github.com/pymss-project/pymss-studio) 基础上构建的独立桌面项目，核心新增是一条**端到端的歌词时间轴制作链路**。上游桌面应用（Tauri + Vue + Python Worker，围绕 [pymss](https://github.com/pymss-project/pymss) 分离引擎）完整保留：模型库、分离任务、工作流、音轨编辑器、音频工具与插件市场。新增的**歌词**页面把音频与 LRC / 纯文本 / ASR 听写结果送入强制对齐，支持逐字编辑，并导出 LRC、TTML、SRT、KRC、ASS、JSON 与独立 HTML 播放器。

分离与对齐均在本机进行。首次准备运行环境、模型或可选组件时需要联网；准备完成后可使用已下载的模型处理本地音频。

> [!NOTE]
> 本项目**不是上游官方发行版**，目前也没有 SoundFlow 安装包，请按[开发者快速开始](#开发者快速开始)从源码运行。为兼容升级，以下历史标识保持不变：`PYMSS_STUDIO_*` 环境变量、`pymss-studio:` 设置前缀、Tauri 标识符，以及 `pymss` / `pymss-core` 包名。

[开发者快速开始](#开发者快速开始) · [功能](#核心功能) · [歌词工作台](#歌词工作台) · [获取与安装](#获取与安装) · [快速上手](#快速上手) · [预览](#界面预览) · [常见问题](#常见问题) · [开发](#开发与验证)

## 开发者快速开始

SoundFlow 只从源码运行，面向开发者，不提供安装包。**Windows / macOS / Linux 命令完全相同**，唯一入口是 `pnpm`；平台差异只有 Tauri 系统依赖和 Python 解释器路径，由 [`scripts/dev-env.mjs`](./scripts/dev-env.mjs) 吸收。

下面按实际执行顺序排列；只改前端可直接跳到[第 3 步](#3-只改界面--文案--i18n轻量路径)。

### 1. 准备工具链

所需版本与上游一致，统一以 [`setup/toolchain.json`](./setup/toolchain.json) 为准；[`setup/README.md`](./setup/README.md) 说明环境与模型分层：

- Node.js 22 与 `pnpm@10.33.2`（Node 22 自带 `corepack`，无需全局安装）
- Rust stable / Cargo 与 [Tauri 平台构建依赖](https://v2.tauri.app/start/prerequisites/)
- Python 3.12
- FFmpeg / FFprobe 仅在开发音频工具时需要

按自己系统复制对应的一段，安装 Tauri 系统依赖与 Python 3.12：

**Windows**（PowerShell / CMD）

```bash
# Tauri：先安装 WebView2 Runtime 与 C++ 构建工具（Visual Studio Build Tools）
winget install --id Python.Python.3.12 -e       # Python 3.12（也可从 python.org 安装）
```

**macOS**

```bash
xcode-select --install                          # Tauri 所需 Xcode 命令行工具
brew install python@3.12                        # Python 3.12
```

**Linux（Ubuntu）**

```bash
sudo apt-get install -y --no-install-recommends \
  build-essential pkg-config libasound2-dev \
  libwebkit2gtk-4.1-dev libssl-dev libxdo-dev \
  libayatana-appindicator3-dev librsvg2-dev   # Tauri 系统依赖
sudo apt-get install -y python3.12 python3.12-venv  # Python 3.12（Ubuntu 22.04 可能需要 deadsnakes PPA）
```

### 2. 克隆、启动与验证

```bash
git clone https://github.com/fengjay01/SoundFlow.git && cd SoundFlow
corepack pnpm dev:setup:desktop   # 装依赖 + 建 .venv/bootstrap（Python 3.12 + requests[socks]）+ 自检
corepack pnpm dev:desktop         # Tauri 窗口 + 前端热更新 + Rust / Python Worker
corepack pnpm test:all            # 一次跑完三层验证
```

### 3. 只改界面 / 文案 / i18n（轻量路径）

这条路径只需要 Node 22 + pnpm，跳过第 1 步里的 Rust、Python 与 Tauri 系统依赖：

```bash
corepack pnpm dev:setup                         # 装前端依赖 + 只读自检
corepack pnpm dev                               # 浏览器打开 http://localhost:1420
corepack pnpm test:frontend && corepack pnpm build  # 前端测试 + 构建
```

### 4. Windows 一键启动（可选）

双击根目录 [`dev-local.bat`](./dev-local.bat)，等价于 `pnpm dev:desktop`；`dev-local.bat --check` 只检查 Node / pnpm，不启动应用。

**其余约定**：命令与验证见 [开发与验证](#开发与验证)。推理环境（torch / CUDA / `pymss`）不用本地准备，在应用内「设置 → 运行环境」安装；模型权重在模型库下载。卡住了跑 `corepack pnpm env:doctor`。

## 核心功能

| 模块 | 功能 |
| --- | --- |
| 歌词时间轴（新增） | 导入 LRC / 纯文本或音频听写，选用 Qwen3-FA / WhisperX+DP / Paraformer / MMS / TIFA 对齐，逐字编辑后导出 LRC、TTML、SRT、KRC、ASS、JSON 与独立 HTML 播放器。 |
| 歌词模型管理（新增） | 15 个模型按 `asr` / `alignment` / `diarization` / `separation` 分类，支持下载源切换（ModelScope / HF 镜像 / HTTP）、校验、清理与卡片 / 列表视图。 |
| 音频分离 | 导入音频、视频或文件夹，批量选择输入；按模型选择输出音轨，配置运行设备、输出格式、保存方式和命名规则。 |
| 多模型融合 | 选择多个模型及对应音轨，配置融合算法和权重，并统一输出结果。 |
| 模型库 | 按名称、用途、架构和来源检索，切换卡片与列表视图；支持收藏、备注、下载续传、空间管理和默认推理参数。 |
| 自定义模型 | 导入受支持架构的模型权重与配置，将已有模型纳入模型库和推理流程。 |
| 工作流 | 简易编辑器编排分离、音频处理、融合和保存步骤；高级节点编辑器提供更灵活的连接、参数配置与导入导出。 |
| 任务队列 | 查看排队和执行中的任务，跟踪进度和日志，取消或重试任务，整理已结束的会话记录。 |
| 结果与工程 | 查看输出音轨和处理参数，试听结果、打开输出目录，并进入编辑器继续处理。 |
| 音轨编辑器 | 多轨波形、片段编辑、播放控制、音量与声像、静音与独奏、淡入淡出、音轨效果、录音、工程保存、素材重连和混音导出。 |
| 音频工具 | 格式转换、音频信息、SDR / SI-SDR、ASR、歌声转 MIDI、静音切片和音频合并。 |
| 运行环境与设置 | 管理 CPU、CUDA、ROCm 和 MLX 等平台适用环境；配置下载源、代理、并发数、存储目录、语言、缩放及主题。 |

前两行为 SoundFlow 新增能力，其余模块继承自上游 Pymss Studio。

不同模型的可用音轨、参数和运行设备可能不同，请以模型详情和当前运行环境为准。显卡加速效果取决于模型、硬件、驱动和任务配置。

## 歌词工作台

在侧边栏打开**歌词**，包含三个标签：**工作台**、**编辑台**、**模型管理**。这是 SoundFlow 的主要新增能力，应用内其余模块均继承自上游 Pymss Studio。

### 处理流程

1. 选择音频文件。可开启**人声分离串联**并指定分离后的人声音轨——直接在原曲混合音轨上对齐，是字级精度差的最常见原因。
2. 选择歌词来源：
   - **导入 LRC** —— 行位置由 LRC 自带时间轴提供，引擎只做字级精修；输入归档为 `<stem>.source.lrc`。
   - **纯文本** —— 粘贴或导入 `.txt`，也支持网易云链接拉取与在线歌词搜索。
   - **无歌词（ASR 听写）** —— 用 Qwen3-ASR 或 faster-whisper 转写后再对齐。
3. 选择对齐引擎：`qwen` / `qwen-ultra` / `qwen-ultra-head`（Qwen3-ForcedAligner，最后一项额外启用 Local Head 边界精修）、`whisper`（WhisperX + DP）、`paraformer`（FunASR）、`mms`（MMS-FA）或 `tifa`（可选引擎，复用当前歌词运行环境，失败自动回退 WhisperX）。`stars` 目前仅在列表中占位，尚未接入。Qwen 系列单次处理上限约 290 秒，更长的歌曲需选择识别模型辅助定位切片，或改用无时长限制的引擎。
4. 开始处理。前置条件未满足时按钮保持禁用：目录模式需扫描出配对文件，单曲模式需音频文件加对应歌词。任务日志会逐阶段输出：音频就绪、加载对齐模型、完成行数/字数、以及每种格式的导出结果。
5. 在工作台查看逐字时间轴，再到**编辑台**微调行文本、翻译与起止时间（播放同步高亮；所有行一次渲染在随窗口高度滚动的表格中，表头吸顶，不再分页）。

可选开关：尾音延长、拖音重叠、分层歌词、自动补充、下句预显，以及**均摊估计模式**（额外产出 `.even.*` 文件，行内字均分，便于与真实对齐结果对照）。

**批量、导入与清理**：工作台支持单曲与整目录两种模式。目录模式按文件名（忽略大小写，`.lrc` 优先）配对音视频与歌词，为每首歌单独建立产物目录，并提供队列进度与取消；音频与歌词文件也可直接拖入页面。删除已完成任务（单条或批量）会按产物文件夹整体清理，覆盖自定义输出目录，回收站失败时自动兜底永久删除。所有文件 / 目录输入框都带一键清除与紧凑的选择按钮。

**分离输出设置**：人声分离串联可配置输出目录、格式、布局（扁平 / 每首独立文件夹）、命名模板与变量、音轨勾选、TTA、MP3 码率与高级推理参数；重复分离会覆盖旧结果，不再生成 `_2` / `_3` 副本。

### 导出产物

产物统一写入任务输出目录，以音频文件名作为前缀。当前桌面界面只导出歌词 / 字幕文件，不生成烧录视频。

| 文件 | 内容 |
| --- | --- |
| `<stem>.lyrics.json` | 主时间轴（逐字，编辑台也读取此文件）。 |
| `<stem>.ass`、`<stem>.lyric.ass` | 逐字 KTV ASS 与逐行 ASS。 |
| `<stem>.lrc` / `<stem>.enhanced.lrc` | 标准 / 增强 LRC。 |
| `<stem>.ttml`、`<stem>.furigana.ttml` | Apple Music / AMLL 风格 TTML 及注音版本。 |
| `<stem>.srt`、`<stem>.krc` | SRT 字幕与酷狗 KRC 歌词。 |
| `<stem>.player.html`、`<stem>.lyric.player.html`、`<stem>.source.player.html` | 独立播放器：逐字、逐行、源 LRC 原始时间轴。 |
| `<stem>.even.lyrics.json`、`.even.ass`、`.even.player.html` | 均摊估计对照版（开启开关后才有）。 |
| `<stem>.source.lrc` / `.source.txt` | 输入歌词归档。 |

### 模型

歌词模型在**歌词 → 模型管理**中单独管理，与主界面「模型库」互不影响。[`python/lyrics/l2t_model_manifest.json`](./python/lyrics/l2t_model_manifest.json) 声明 15 个模型，逐个指定下载源（Hugging Face、ModelScope、HTTP、GitHub Release、内置）。物理目录位于数据根目录下 `models/_tool_models/lyrics/models/{asr,alignment,diarization,separation}/`，各读取端仍兼容旧的扁平布局。下载支持断点续传与校验，页面提供状态刷新、搜索、分类 / 来源筛选、排序、下载 / 取消、验证与清理删除。两套登记位置（HF 快照与扁平 `asr/` 目录）现在互相兜底：任一份存在即视为已下载，下载会复用另一布局的副本，删除时两份一起清理。

对齐与导出链路参考 [6hdj/lrc-ttml](https://github.com/6hdj/lrc-ttml)（MIT）的方案：双对齐引擎（faster-whisper + DP）、LRC 锚点切片并以歌词作提示词、逐字时间轴、分层歌词，以及 Apple/AMLL 风格 TTML 导出。

> [!WARNING]
> TIFA 默认复用当前歌词运行环境，无需配置：选择该引擎时会自动把 `LYRICS_TIFA_PYTHON` 指向 Worker 解释器，并自动识别 `alignment/tifa/` 下的模型。仍可用 `LYRICS_TIFA_PYTHON` / `LYRICS_TIFA_MODEL` 指定外部解释器与模型目录；不可用时自动回退 WhisperX。TIFA 模型为 CC BY-NC-SA（不可商用），Meta MMS-FA 对齐权重为 CC BY-NC 4.0，同样不可商用。其余模型权重各自遵循原有许可证。

## 获取与安装

SoundFlow 目前**仅提供源码**：本项目尚未发布安装包或便携版，请按[开发者快速开始](#开发者快速开始)本地克隆运行。若需要现成安装包来使用继承自上游的分离功能，可前往 [上游 Pymss Studio Releases](https://github.com/pymss-project/pymss-studio/releases)，阅读发行说明并在 **Assets** 中选择文件（无法访问 GitHub 时，发行说明中提供镜像入口）。

> [!NOTE]
> 下面的表格描述的是**上游**发布包，包内程序名仍为 `Pymss Studio`；本地构建出的 SoundFlow 使用新名称。上游项目仍在积极迭代，当前发布包含预发布版本——升级前请阅读对应发行说明，并备份重要工程及设置。

### 平台与运行环境

| 平台 / 设备 | 环境或包标识 | 说明 |
| --- | --- | --- |
| Windows x64，NVIDIA 显卡 | `windows-x64-cuda` | 使用 CUDA；需要兼容的显卡和驱动。 |
| Windows x64，CPU 推理 | `windows-x64-cpu` | 不确定显卡兼容性时，可先选择此版本。 |
| Windows x64，受支持的 AMD 显卡 | `windows-x64-rocm` | ROCm 支持范围更具体，请核对所选版本的显卡及驱动要求。 |
| Windows x64，希望减小首次下载体积 | `windows-x64-online` | 包含基础运行时，首次启动后安装需要的推理环境。 |
| macOS 14+，Apple Silicon | `macos-arm64-mlx` | 使用 MLX，下载 `.dmg` 后将应用拖入 `/Applications`。 |
| Linux x64 | `linux-x64-appimage` | 发布流程提供 AppImage 构建；请以对应发行页的 Assets 为准。 |
| Intel Mac | 暂无专用发布包 | 现有 MLX 包面向 Apple Silicon；自行构建时仍需核验推理依赖的兼容性。 |

发布包包含 Python，普通用户无需单独安装系统 Python。macOS DMG 已包含固定的 Python、PyTorch、MLX 和出厂核心，首次启动无需重新下载整套 Python 环境。可在 **设置 → 运行环境** 中查看当前环境并更新内核；应用内安装、切换、重装或删除整套环境适用于 Windows 和 Linux。

### 选择文件类型

| 文件 | 用途 |
| --- | --- |
| Windows `*-setup.exe` | 安装版，适合常规安装；默认数据目录位于用户文件夹。 |
| Windows `*.7z` | 便携版，解压完整目录后运行 `Pymss Studio.exe`；保留内置运行时和工具目录。 |
| macOS `*.dmg` | Apple Silicon 安装镜像。 |
| Windows `*-update.zip` | 已安装用户的更新包，不作为首次安装包使用；按对应发行说明操作。 |
| `*.part-000` 等分卷文件 | 下载全部分卷，按发行页的 `SPLIT-ASSETS-README.txt` 合并后再解压。 |

若可信来源的 macOS 应用因隔离属性无法打开，可针对已安装的应用执行：

```bash
xattr -cr '/Applications/Pymss Studio.app'
```

## 快速上手

第 1–5 步为继承自上游的分离流程，第 6 步为本项目新增的歌词流程。

1. **完成启动引导。** 选择界面语言、主题、数据目录和推理环境；在线版需等待运行环境安装完成。
2. **准备模型。** 打开“模型”，下载适合目标音源的模型，或导入受支持的自定义模型。
3. **配置分离。** 返回“分离”，导入音频 / 视频文件或文件夹，选择模型、输出音轨、输出目录和格式。首次使用可保留模型默认参数。
4. **执行与查看。** 点击“开始分离”，在任务队列查看进度和日志；完成后试听音轨，或前往“结果”查看输出。
5. **编辑与导出。** 从结果进入音轨编辑器，调整片段、音量、声像及效果，保存工程，再通过“导出混音”生成音频。
6. **制作歌词时间轴（SoundFlow 新增）。** 打开“歌词”，选择音频与歌词来源（LRC / 纯文本 / ASR），选定对齐引擎，然后在[歌词工作台](#歌词工作台)查看结果并导出。

需要重复执行同一条处理链时，在“工作流”打开或创建流程，进入独立编辑窗口，在输入节点选择文件或文件夹、输出节点选择目录，然后直接在编辑器中保存和运行。可在画布跟踪节点进度，运行期间的修改用于下一次执行。修改参数前，可先用一个短音频确认模型、设备和输出配置。

## 界面预览

以下截图来自继承自上游的界面，因此画面中仍显示旧名称 **Pymss Studio**；新增的歌词页暂无截图。中英双语、浅色与深色预览统一为 1920 × 1080，点击图片可查看完整尺寸。

| 功能 | 浅色主题 | 深色主题 |
| --- | --- | --- |
| 批量分离与输出配置 | <a href="./images/screenshots/separate/batch/zh-CN-light.webp"><img src="./images/screenshots/separate/batch/zh-CN-light.webp" alt="批量分离与输出配置" width="420" /></a> | <a href="./images/screenshots/separate/batch/zh-CN-dark.webp"><img src="./images/screenshots/separate/batch/zh-CN-dark.webp" alt="批量分离与输出配置" width="420" /></a> |
| 模型库与分类管理 | <a href="./images/screenshots/models/library/zh-CN-light.webp"><img src="./images/screenshots/models/library/zh-CN-light.webp" alt="模型库与分类管理" width="420" /></a> | <a href="./images/screenshots/models/library/zh-CN-dark.webp"><img src="./images/screenshots/models/library/zh-CN-dark.webp" alt="模型库与分类管理" width="420" /></a> |
| 简易工作流编辑器 | <a href="./images/screenshots/workflows/simple/zh-CN-light.webp"><img src="./images/screenshots/workflows/simple/zh-CN-light.webp" alt="简易工作流编辑器" width="420" /></a> | <a href="./images/screenshots/workflows/simple/zh-CN-dark.webp"><img src="./images/screenshots/workflows/simple/zh-CN-dark.webp" alt="简易工作流编辑器" width="420" /></a> |
| 高级节点工作流 | <a href="./images/screenshots/workflows/advanced/zh-CN-light.webp"><img src="./images/screenshots/workflows/advanced/zh-CN-light.webp" alt="高级节点工作流" width="420" /></a> | <a href="./images/screenshots/workflows/advanced/zh-CN-dark.webp"><img src="./images/screenshots/workflows/advanced/zh-CN-dark.webp" alt="高级节点工作流" width="420" /></a> |
| 结果与音轨管理 | <a href="./images/screenshots/results/zh-CN-light.webp"><img src="./images/screenshots/results/zh-CN-light.webp" alt="结果与音轨管理" width="420" /></a> | <a href="./images/screenshots/results/zh-CN-dark.webp"><img src="./images/screenshots/results/zh-CN-dark.webp" alt="结果与音轨管理" width="420" /></a> |
| 音轨编辑与混音 | <a href="./images/screenshots/editor/workspace/zh-CN-light.webp"><img src="./images/screenshots/editor/workspace/zh-CN-light.webp" alt="音轨编辑与混音" width="420" /></a> | <a href="./images/screenshots/editor/workspace/zh-CN-dark.webp"><img src="./images/screenshots/editor/workspace/zh-CN-dark.webp" alt="音轨编辑与混音" width="420" /></a> |

<details>
<summary>音频工具、设置和操作弹窗</summary>

| 功能 | 浅色主题 | 深色主题 |
| --- | --- | --- |
| 多模型融合配置 | <a href="./images/screenshots/separate/ensemble/zh-CN-light.webp"><img src="./images/screenshots/separate/ensemble/zh-CN-light.webp" alt="多模型融合配置" width="420" /></a> | <a href="./images/screenshots/separate/ensemble/zh-CN-dark.webp"><img src="./images/screenshots/separate/ensemble/zh-CN-dark.webp" alt="多模型融合配置" width="420" /></a> |
| 自定义模型导入 | <a href="./images/screenshots/models/import/zh-CN-light.webp"><img src="./images/screenshots/models/import/zh-CN-light.webp" alt="自定义模型导入" width="420" /></a> | <a href="./images/screenshots/models/import/zh-CN-dark.webp"><img src="./images/screenshots/models/import/zh-CN-dark.webp" alt="自定义模型导入" width="420" /></a> |
| 混音导出 | <a href="./images/screenshots/editor/export/zh-CN-light.webp"><img src="./images/screenshots/editor/export/zh-CN-light.webp" alt="混音导出" width="420" /></a> | <a href="./images/screenshots/editor/export/zh-CN-dark.webp"><img src="./images/screenshots/editor/export/zh-CN-dark.webp" alt="混音导出" width="420" /></a> |
| ASR 语音识别 | <a href="./images/screenshots/tools/asr/zh-CN-light.webp"><img src="./images/screenshots/tools/asr/zh-CN-light.webp" alt="ASR 语音识别" width="420" /></a> | <a href="./images/screenshots/tools/asr/zh-CN-dark.webp"><img src="./images/screenshots/tools/asr/zh-CN-dark.webp" alt="ASR 语音识别" width="420" /></a> |
| 人声转 MIDI | <a href="./images/screenshots/tools/midi/zh-CN-light.webp"><img src="./images/screenshots/tools/midi/zh-CN-light.webp" alt="人声转 MIDI" width="420" /></a> | <a href="./images/screenshots/tools/midi/zh-CN-dark.webp"><img src="./images/screenshots/tools/midi/zh-CN-dark.webp" alt="人声转 MIDI" width="420" /></a> |

</details>

截图由当前 Vue 页面实际渲染，使用隔离的展示数据。任务进度、下载状态和音轨波形用于呈现界面，不代表性能测试结果。

## 音频工具

工具集中于“小工具”页面，可独立于分离流程使用。

| 工具 | 能力与输入要求 |
| --- | --- |
| 音频格式转换 | 批量输出 WAV、FLAC、MP3 或 OGG，并配置采样率、声道及编码质量。 |
| 音频详细信息 | 使用 FFprobe 查看容器、音频流、编码参数和元数据标签。 |
| 计算 SDR | 对齐参考音频与估计音频，计算逐声道 SDR 和 SI-SDR；需要对应的参考音频。 |
| ASR 语音识别 | 使用 FunASR 预设或本地模型，生成 TXT、JSON 和 SRT；预设支持的语言不同。 |
| 歌声转 MIDI | 使用 GAME 原生 Torch 模型提取音高与音符时值；解压完整 GAME 模型包后选择其中的 `model.pt`。 |
| 音频切片 | 按静音区间分析并导出片段，配置最短片段、静音阈值和边界保留时长。 |
| 合并音频 | 按文件名、修改时间或正则规则排列目录中的音频，输出统一规格的 WAV。 |

ASR 的 FunASR 运行组件按需安装，模型首次使用时下载并缓存；macOS 当前未开放 ASR 工具。歌声转 MIDI 可通过工具页提供的 [GAME 发布页](https://github.com/openvpi/GAME/releases/tag/v1.0.0) 下载模型压缩包。**解压后保留完整目录：`model.pt` 同级必须有 `config.yaml`，启用语言条件的模型还需要 `lang_map.json`。** 不要单独移动权重文件。输出语言、格式和参数以所选工具与模型为准。

## 插件市场

在侧栏打开**插件**，即可浏览和搜索官方目录。使用前请先在设置中安装并启用 Python 运行环境。通过**从来源安装**也可安装插件名称、Git 仓库或本地插件文件夹；仓库子目录使用 `URL#path/to/plugin`。

“已安装”页展示加载错误、提供的操作及更新检查结果。市场插件支持更新、修复依赖和卸载。项目内 `data/plugins/`（或 `PYMSS_PLUGINS_DIR`）中的插件也会以只读方式列出，其文件和依赖继续在市场外管理；默认不再回退到 `~/.pymss/plugins`。插件将以你的用户权限运行 Python 代码。

市场插件源码及额外依赖保存在应用数据目录的 `runtime-envs/plugins/<运行环境标识>/installed/` 下。安装依赖时以当前运行环境为基础约束核心包版本；不兼容的依赖会导致安装失败，并保留原来可用的插件。更新经过暂存和加载验证后才会生效。每个运行环境有各自的市场安装记录。

“浏览”页的**刷新目录**更新可安装列表，“已安装”页的**检查更新**比较已安装版本；本地开发源还会比较文件内容，识别同版本的组件更新。各卡片的**打开工具**直接进入插件提供的页面，更多菜单中的重新验证仅检查加载状态。普通更新保留现有依赖，需要重新安装依赖时可在更新窗口明确勾选。**检查更新**发现新版本后会列出所有可更新的插件，可勾选需要的插件逐个更新，也可一键**全部更新**；批量更新窗口同样提供依赖安装选项，更新按顺序执行，进度与结果可在日志中查看。

分类由插件的 `[tool.pymss.studio] category` 声明，支持 `convert`、`analyze`、`recognize`、`edit`；未声明的插件归入“其他”。市场与工具页使用相同元数据，并根据插件声明的 `locales` 切换名称、说明与操作标签。插件需要通过 `setup(plugin)` 提供操作，且运行环境中的 pymss 需要支持 operations。仅注册旧式 capability 或图节点的插件可能没有通用操作；安装插件不会自动向 SoundFlow 工作流编辑器添加动态节点。

## 数据、运行环境与更新

### 数据保存位置

应用将设置、模型、输出、工程、日志和临时文件归集到数据根目录。实际路径可在 **设置 → 数据目录** 查看；模型和输出目录可按需要调整。下表描述安装版发布包，源码 checkout 的布局见 [`setup/README.md`](./setup/README.md)。

| 使用方式 | 默认数据根目录 |
| --- | --- |
| 设置 `PYMSS_STUDIO_DATA_ROOT` | 优先使用指定路径。 |
| 从 Cargo `target/debug` 或 `target/release` 直接运行开发产物 | 项目根目录下的 `data/`。 |
| Windows 便携版，程序同级有 `pymss-studio.portable` 标记 | 程序同级的 `data/`。 |
| 普通安装版 / 其他发布包 | 用户目录下的 `.pymss-studio/`，例如 Windows 的 `%USERPROFILE%\.pymss-studio`。 |

数据根目录默认包含 `settings/`、`models/`、`outputs/`、`editor-projects/`、`logs/` 和 `temp/`。macOS 和 Linux 的可写运行状态保存在该根目录下的 `runtime-envs/`：`active-runtime.json` 记录当前核心，`.overlays/` 保存核心更新及其依赖，`optional-packages/` 保存独立组件，`pymss-runtime-state.json` 记录各后端的 `lyricsReady` 歌词就绪标志。Windows 继续使用所选运行时根目录下的 `runtime-envs/`，通常位于安装资源旁的 `python-runtime/runtime-envs/`。macOS 的基础 Python 环境保留在应用包内。

**歌词模型使用独立目录树。** SoundFlow 将其存放在数据根目录下的 `models/_tool_models/lyrics/models/{asr,alignment,diarization,separation}/`，与分离模型使用的 `models/` 目录互不干扰，可在**歌词 → 模型管理**中维护。`PYMSS_STUDIO_*` 变量名与 `pymss-studio:` 设置前缀是为升级兼容保留的历史标识。

**编辑器工程会引用音频素材的文件路径。** 备份工程时也应保留对应音频；移动或删除原始输出后，可在编辑器中重连缺失素材。调整模型目录时优先使用应用提供的目录迁移流程。

### 更新与环境管理

在 **设置 → 关于** 查看版本与更新状态。发布通道支持应用内更新的 Windows 和 macOS 构建可在此检查并安装更新；不支持自动更新的构建通过发行页手动更新。macOS 应用更新会整体替换 `.app` 并保留用户数据，写入权限不足时请求管理员授权。更新前应将应用移入 `/Applications`；从只读 DMG 或 App Translocation 位置运行时会阻止更新。固定基础环境契约发生变化时，需要手动安装新版。

在 **设置 → 运行环境** 中，可独立更新 `pymss`、`pymss-core` 及其兼容依赖，无需更新桌面软件。确认前会展示版本和依赖计划，软件包先安装到新的核心覆盖层，验证成功后才切换；存在兼容的上一版时可回退。需要替换固定加速组件的更新会被拒绝，当前环境保持可用。依赖组合由 [`python/runtime-manifest.json`](./python/runtime-manifest.json) 定义。

**歌词依赖共用这套运行环境。** [`python/runtime-manifest.json`](./python/runtime-manifest.json) 新增 `lyrics` / `lyricsPackages` / `lyricsOptional` / `lyricsOptionalPackages` / `lyricsOptionalWheels` 分组（whisperx、faster-whisper、ctranslate2、pypinyin、demucs、可选对齐引擎，以及 Qwen3-FA Ultra companion wheel 双镜像）。通过「设置 → 运行环境 → 安装 / 修复此环境」会与 `pymss` 一起装进当前运行环境，共用同一份 Torch。该环境的 `pymss-runtime-state.json` 记录 `lyricsReady` 后，歌词命令直接复用它；此前安装的旧环境在重新安装前仍自动回退到 bootstrap 解释器。**检测环境**会实时探测当前解释器并列出缺失的歌词核心 / 可选依赖，缺少可选引擎不影响核心链路。不要再单独创建 `.venv-asr`；音频工具 ASR 的 FunASR 组件仍按独立可选组件管理。

FunASR 等可选组件使用独立目录，与核心覆盖层分别管理。安装时校验与当前 PyTorch 版本及加速后端的兼容性。Windows 和 Linux 卸载 FunASR 时，会删除独立组件目录，并卸载直接安装在当前环境中的副本，保留已下载的模型。macOS 的组件管理不卸载内置基础环境中的包，ASR 工具仍未开放。pip 和 Numba 缓存保存在数据根目录的 `temp/` 下（dev 构建由 `dev-env.mjs` 把 pip 缓存注入到 `.venv/cache/pip`）。

在 macOS 上删除 `SoundFlow.app`（或上游的 `Pymss Studio.app`）会移除应用及内置环境，用户数据根目录中的设置、模型、输出和核心覆盖层继续保留，因此重新安装后会复用原有数据。自定义模型或输出目录，以及应用同级的 `.pymss-studio-previous-*.app` 更新备份，需要单独管理。

## 常见问题

| 问题 | 排查方式 |
| --- | --- |
| 分离页找不到模型 | 分离列表仅显示已下载且支持推理的模型。检查下载状态、模型支持信息和运行环境，再刷新模型列表。 |
| GPU 未识别或模型推理报错 | 确认已激活对应后端，检查显卡、驱动和所选模型的兼容性；可切换 CPU 环境做对照，并查看任务日志。 |
| 显存或内存不足 | 先保持任务并发数为 1，按模型支持范围减小批量大小或分块大小。不同架构的参数含义可能不同。 |
| 环境或模型下载失败 | 检查“设置 → 默认参数”中的下载源、下载方式和代理；环境安装还可选择 PyPI 镜像。确认连接后重试。 |
| ASR 或 MIDI 无法启动 | 检查 FunASR 组件安装状态、ASR 模型来源；GAME 应使用完整解压目录，检查权重旁的 `config.yaml` 和需要时的 `lang_map.json`。 |
| 工程提示素材缺失 | 检查对应音频是否移动或删除，使用编辑器的素材重连功能，重新保存工程。 |
| 歌词：0/N 行对齐成功，或字间隔完全均匀 | LRC 时间轴可能与音频版本不一致，或对齐引擎听到的是伴奏。开启人声分离串联后重跑，并结合任务日志排查。 |
| 歌词：Qwen 引擎处理长音频失败 | Qwen 系列单次处理上限约 290 秒。选择识别模型辅助定位切片，或改用无时长限制的引擎（Whisper / MMS / Paraformer）。 |
| 歌词模型下载失败或一直卡住 | 刷新模型状态，切换下载源（ModelScope / HF 镜像），检查代理设置。下载完成后会做校验；失败残留的文件需先清理再重试。 |
| 更新失败或升级后异常 | 记录版本、包类型和错误日志，阅读发行说明；需要手动更新时先备份工程与设置，再使用完整发布包。 |

启用开发者模式后，可在“诊断 → 日志”按来源、等级和任务筛选日志。SoundFlow、Worker、pymss 和第三方工具使用不同的来源标签；警告与错误分别突出显示，异常堆栈、代码位置及上下文可展开查看。任务、工作流节点、下载和安装日志采用相同的展示规则。

从命令提示窗口或终端启动应用时，日志也会以可读格式输出；支持颜色的终端会突出等级和来源，重定向输出或设置 `NO_COLOR` 时不含颜色控制码。应用日志文件使用逐条 JSON 记录，累计日志仍可读取旧版文本，无需手动转换。

“诊断 → 生成诊断报告”输出的 `latest-diagnostic-report.md` 包含版本与会话、近期页面和工具选择、操作请求及状态、分离参数、工作流批次/节点关联、任务历史与工作流结构、编辑器工程及导出摘要、模型下载、运行环境探测和更新记录。请求在校验和 Worker 启动前记录，因此环境缺失或启动失败也能保留所用功能及参数。

报告区分本次运行的观测与已保存的历史快照，附带采集或保存时间；生成时不会重新启动环境探测或扫描音频文件。近期操作最多 24 项、任务历史最多 12 项，优先保留运行中和失败任务；字段、集合、文件读取和报告段落均有限制，并注明截断数量或不可用原因。请求和配置摘要只保留白名单字段，不复制识别文本、音频内容、任意工作流文本或代理地址。错误信息与日志尾部仍可能包含自定义名称和路径，分享前请检查。

自动错误报告会合并 5 秒内的连续异常并补写最新错误和堆栈，正常退出前会保存尚未写入的异常。清空日志时会取消旧的待写报告。

如果启动后没有窗口，先退出已有的 SoundFlow 进程，在安装或构建目录打开 CMD，使用 `--debug` 在界面加载前开启本次运行的详细日志（源码构建运行 `pnpm dev:desktop` 并查看终端输出）。下面的命令使用上游发布包名称 `Pymss Studio.exe`，本地构建产物名为 `SoundFlow.exe`：

```cmd
start "" /b /wait "Pymss Studio.exe" --debug
```

需要保存终端输出时，执行 `start "" /b /wait "Pymss Studio.exe" --debug > "%TEMP%\pymss-startup.log" 2>&1`。该参数会同步开启 SoundFlow 和 Worker 的详细日志，不修改已保存的开发者模式设置；退出后再次正常启动即恢复原有日志等级。已有进程需要退出后重新带参数启动。macOS 和 Linux 可直接向应用可执行文件传入 `--debug`。

问题仍存在时，请在 [GitHub Issues](https://github.com/fengjay01/SoundFlow/issues) 提供版本与包类型、系统版本、GPU 型号、模型名称、复现步骤和相关日志片段。提交前移除日志中的个人路径、代理凭据等敏感信息。

## 开发与验证

### 环境准备

命令见顶部[开发者快速开始](#开发者快速开始)。版本契约：[`setup/toolchain.json`](./setup/toolchain.json)（Node 22 / pnpm 10.33.2 / Python 3.12 / Rust stable），`package.json` 的 `engines` / `packageManager` 与 `.nvmrc` 均与之对齐；`pnpm test:all` 用同样版本跑完三层验证。完整路径与缓存契约见 [`setup/README.md`](./setup/README.md)。

三端差异只有两处：Tauri 系统依赖的安装命令、venv 解释器路径（`.venv/bootstrap/Scripts/python.exe` vs `.venv/bootstrap/bin/python`），后者由 `dev-env.mjs` 按 `process.platform` 处理。`pnpm dev:desktop` 把环境类缓存（pip / cargo / npm / pnpm / XDG / 临时目录）pin 进项目内 `.venv/`，而可能存放模型权重的缓存（HF / torch.hub / ModelScope）落到 `data/models/_cache/`，全部都在仓库内，**不写系统盘**；解释器可用 `PYMSS_STUDIO_PYTHON` 覆盖。中国网络策略是可选的：`SOUNDFLOW_CN_NETWORK=1` 才启用镜像，默认不改动开发者的代理设置。

**路径契约。** 所有默认落点都由代码位置推导 —— [`python/_project_paths.py`](./python/_project_paths.py) 用 `__file__`、Rust 用 `CARGO_MANIFEST_DIR`、Node 用 `import.meta.url` —— 不把用户目录当默认值，环境变量只作覆盖。分层判据只有一条「这个目录会不会出现模型权重」：会 → `data/models/_cache/`，不会 → `.venv/`。调试脚本与产物经 [`scripts/debug_paths.py`](./scripts/debug_paths.py) 的 `debug_out()` 统一写入仓库根内 `_debug_output/`，且不会进提交。`pnpm env:paths` 可查看并断言所有注入路径；`pnpm env:paths` 与 `pnpm env:doctor` 都包含 `paths-local` 断言，出现越界即失败。

推理环境（torch / CUDA / `pymss`）在应用内「设置 → 运行环境」安装，dev 构建下落到 `.venv/python/runtime-envs/`（由 `src-tauri/src/storage.rs` 按 `CARGO_MANIFEST_DIR` 推导，三端一致，且不会被 `cargo clean` 清掉）；模型权重在模型库下载。同一次安装也会把歌词依赖装进该运行环境，无需再建独立的歌词环境。FFmpeg / FFprobe 只在开发音频工具时需要。

卡住了跑 `corepack pnpm env:doctor` 看逐项诊断。只改前端运行 `pnpm test:frontend` 与 `pnpm build`，改 Worker 或后端运行对应层的测试。`pnpm test:all` 由 `dev-env.mjs` 注入同一份环境后依次执行三层，前端 `node --test` 与 Rust `cargo test` 的临时文件、cargo 缓存也落在 `.venv/`，**不写系统盘**。

### 常用命令

`python/plugin-manifest.json` 配置官方插件仓库和可选预装清单。默认清单为空，SoundFlow 构建不获取插件源码；用户从插件市场选择安装工具。尚未安装工具时，工具页显示安装入口。现有用户已安装和禁用的插件均保留。

本机开发插件时，先在插件仓库构建，再通过诊断页的“插件来源”选择本地仓库，在市场安装或更新所需插件。可选离线发行包可填写预装清单并执行 `pnpm plugins:prepare --release`，构建 `ref` 应固定到提交。生成的插件资源不纳入 Studio 源码管理；`pnpm plugins:prepare --check` 可离线校验资源。

项目默认使用配置的远端仓库，开发启动也不会自动选择同级本地仓库。需要在某台机器上调试插件时，启用开发者模式，在“诊断 → 插件来源”选择本地仓库或修改远端地址、目录分支，再读取目录并获取/更新所选插件。覆盖配置只保存在该机器设置目录的 `plugin-source.json` 中，关闭开发者模式后不应用；恢复默认来源会清除覆盖值。显式的环境变量覆盖仍生效，其中 `PYMSS_PLUGINS_REGISTRY` 优先。

| 命令 | 用途 |
| --- | --- |
| `pnpm dev:setup` | **新开发者第一步**：装前端依赖 + 只读自检；不安装 Python / Rust。 |
| `pnpm dev:setup:desktop` | 桌面开发第一步：在上面基础上创建 Python 3.12 `.venv/bootstrap`。 |
| `pnpm env:check` | 只读检查环境与工具链（`dev-env.mjs check`）。 |
| `pnpm env:bootstrap` | 创建项目内 Python bootstrap venv（`dev-env.mjs bootstrap`）。 |
| `pnpm env:doctor` | 逐项诊断（含 FFmpeg / FFprobe 与 `paths-local` 路径断言），按平台给出安装提示。 |
| `pnpm env:paths` | 打印所有注入/默认路径并断言都在仓库内（`dev-env.mjs paths`），越界返回退出码 1。 |
| `pnpm dev` | 前端开发服务器，默认端口 `1420`；用于界面开发和浏览器预览。 |
| `pnpm dev:desktop` | 三端通用的完整桌面应用：注入项目内环境变量后跑 `pnpm tauri dev`。 |
| `pnpm tauri dev` | 直接启动桌面应用，不注入项目内环境变量（高级用法）。 |
| `pnpm worker -- env_info` | 用解析出的 venv Python 调试 Worker（`--help` / `env_info` / `list_models`）。 |
| `pnpm build` | TypeScript 类型检查与前端生产构建。 |
| `pnpm test` / `pnpm test:frontend` | 前端逻辑测试。 |
| `pnpm test:python` | Python Worker 模块测试；走 `dev-env.mjs`，三端均不依赖裸 `python`。 |
| `pnpm test:python:local` | 等价于 `pnpm test:python`，保留给本地习惯用法。 |
| `pnpm test:rust` | Rust 后端测试。 |
| `pnpm test:all` | 三层共享同一份 `dev-env.mjs` 注入环境，依次跑前端 / Python / Rust；`node scripts/dev-env.mjs test:all --dry` 只预览。需先准备各层依赖。 |
| `pnpm plugins:prepare` | 按配置准备随包分发的预装插件（同样跨平台解析 Python）。 |
| `pnpm tauri build` | 桌面构建，会先执行前端构建；完整推理运行时需另行准备。 |
| `python -X utf8 -m unittest discover -s python/lyrics -t python -p "test_*.py"` | 歌词链路单元测试（`python/lyrics/test_*.py`）。 |
| `python -m unittest discover -s tests/python -p "test_project_paths.py" -v` | 路径契约单元测试（`tests/python/test_project_paths.py`）。 |
| `dev-local.bat`（Windows，可选） | 双击启动桌面应用，等价于 `pnpm dev:desktop`；`--check` 只检查 Node / pnpm。 |

只修改文档时检查内容和链接即可；前端修改应运行相应测试与 `pnpm build`，Worker 或后端修改应执行对应层测试。工具链契约固定 Node 22 / Python 3.12 / Rust stable，`pnpm test:all` 会在同一份 `dev-env.mjs` 环境下本地跑完三层验证。

<details>
<summary>Python Worker 调试与环境变量</summary>

独立调试 Worker 时建议使用专用 Python 3.12 环境。先按所需后端准备兼容的 Torch 与 `pymss` 依赖；[`python/requirements.txt`](./python/requirements.txt) 描述 Worker 开发依赖，[`python/requirements-ci.txt`](./python/requirements-ci.txt) 描述 CI 测试依赖。应用内受管理环境以运行时清单为准；[`python/lyrics/requirements.txt`](./python/lyrics/requirements.txt) 只是歌词依赖的手动排障说明，不要据此再建独立环境。

```bash
pnpm worker -- --help
pnpm worker -- env_info
pnpm worker -- list_models --payload '{}'
```

`pnpm worker` 走 `dev-env.mjs` 解析出的解释器（优先 `PYMSS_STUDIO_PYTHON` → `.venv/bootstrap` → 历史 Windows 布局 → 系统 Python），三端一致；也可直接 `node scripts/dev-env.mjs python -- <参数>`。检查发布包时应改用包内或已激活环境的 Python 路径。

| 变量 | 说明 |
| --- | --- |
| `PYMSS_STUDIO_DATA_ROOT` | 覆盖应用数据根目录。 |
| `PYMSS_STUDIO_PYTHON` | 覆盖引导 Python 解释器；`dev-env.mjs` 未命中覆盖时依次查找 `.venv/bootstrap`、历史 Windows 布局、系统 `python3.12` / `python3` / `python`（Windows 另有 `py`）。推理仍可使用已激活环境。 |
| `PYMSS_STUDIO_DEFAULT_OUTPUT_DIR` | Worker 未显式指定输出时的默认目录；桌面应用会将当前配置的输出目录传给 Worker。 |

</details>

<details>
<summary>架构、项目结构与发布流程</summary>

| 层级 | 技术与职责 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Pinia、Vue Router、Vue I18n、Naive UI；工作流节点编辑使用 LiteGraph。 |
| 桌面后端 | Tauri v2 与 Rust，负责文件对话框、进程调度、持久化、更新和桌面窗口。 |
| Worker | Python JSON 协议，提供模型、推理、工作流及音频处理操作。 |
| 歌词链路（SoundFlow 新增） | `python/worker_lyrics.py`、`python/worker_lyrics_models.py` 与 `python/lyrics/*`：ASR、强制对齐、DP 匹配、ASS/TTML/LRC/SRT/KRC/播放器导出与模型仓库。 |
| 分离引擎 | 外部 `pymss` / `pymss-core` 包；本仓库负责桌面集成，不实现核心分离算法。 |

```mermaid
flowchart LR
  UI[Vue / Naive UI] --> API[Tauri / Rust]
  API --> Worker[Python Worker]
  Worker --> Core[pymss / pymss-core]
  Core --> Audio[分离音轨]
  Audio --> Editor
  UI --> Editor[音轨编辑与混音导出]
  UI --> Lyrics[歌词工作台]
  Lyrics --> LyWorker[worker_lyrics + python/lyrics]
  LyWorker --> Files[LRC / TTML / ASS / SRT / KRC / player.html]
```

```text
.
├── src/                   # 页面、组件、状态、工作流、音频工具与国际化
├── src-tauri/             # Rust 后端、桌面配置与资源打包
├── python/                # Worker、音频模块与运行时依赖清单
├── python/lyrics/         # SoundFlow 歌词链路：ASR、对齐、导出器与模型仓库
├── tests/                 # 前端、Python、手动检查与固定输入
├── native/                # 原生音频宿主（Rust）与 VST 实验源码
├── setup/                 # 工具链契约与环境分层说明（toolchain.json、README.md）
├── scripts/               # 开发环境注入、测试编排、路径校验与发布工具
├── _debug_output/         # 调试产物（已忽略，位于仓库根内）
├── .venv/                 # 仅本机的缓存、bootstrap venv 与运行时环境（已忽略）
├── data/                  # 仅本机的模型库与缓存（已忽略）
├── installer/             # Windows 安装器资源
└── images/screenshots/    # 中英文浅色 / 深色截图
```

Windows 发布流程准备基础 Python 和指定后端环境，构建 Tauri 可执行文件，暂存 Worker、运行时和媒体工具，再生成安装包与便携压缩包。macOS 流程准备 arm64 MLX 环境及媒体工具后生成 DMG。Linux 流程构建 AppImage。

```powershell
./scripts/prepare-python-runtime.ps1 -Variant cuda -InitialBackend cuda
./scripts/prepare-python-runtime.ps1 -Variant default -InitialBackend cpu
```

以上是独立的环境准备示例，应选择其中一项，而非依次执行。准备脚本会写入 `python-runtime/`，需要联网且下载量较大。当前仓库不包含 CI 与发布工作流，这些命令仅作为手动打包时的参考。

</details>

## 后续方向

- 完成 STARS 对齐引擎接入，稳定可选的 TIFA 适配器，扩展多语种强制对齐能力。
- 补充歌词页截图、正式发布包，以及“导入 → 对齐 → 编辑 → 再导出”的应用内闭环。
- 上游路线（Intel / AMD 兼容性、运行环境校验、移动端 GPU / NPU 研究）继续由 [Pymss Studio](https://github.com/pymss-project/pymss-studio) 推进，本项目不提供移动端构建。

## 交流与贡献

SoundFlow 自身的问题（歌词链路、本地启动脚本、文档）请提交到 [GitHub Issues](https://github.com/fengjay01/SoundFlow/issues)。继承自上游的分离 / 运行环境问题，建议先查[上游 Issues](https://github.com/pymss-project/pymss-studio/issues)；通用使用讨论可见[上游 Discussions](https://github.com/pymss-project/pymss-studio/discussions) 与中文[上游交流群](https://qm.qq.com/q/YLLou4NucE)。

本项目目前以问题反馈、复现报告和文档修正为主，暂不承接常态化的外部 Pull Request；贡献方式请直接参考上面的 Issues 入口。

## 许可证

SoundFlow 源自 Pymss Studio，沿用 GNU Affero General Public License v3.0，详见 [LICENSE](./LICENSE)。歌词链路记录了参考 [6hdj/lrc-ttml](https://github.com/6hdj/lrc-ttml)（MIT）的迁移映射；依赖、模型权重和第三方工具遵循各自的许可证与使用条件（TIFA 模型为 CC BY-NC-SA，Meta MMS-FA 权重为 CC BY-NC 4.0，两者均不可商用）。
