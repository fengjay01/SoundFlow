<!-- markdownlint-disable MD013 MD033 MD041 -->

<p align="center">
  <img src="./src/assets/app-logo-unified.png" alt="SoundFlow" width="120" />
</p>

<h1 align="center">SoundFlow</h1>

<p align="center">
  A local desktop workspace for music source separation <strong>and word-level lyric timeline production</strong>.
</p>

<p align="center">
  A standalone desktop project built on <a href="https://github.com/pymss-project/pymss-studio">Pymss Studio</a>, extended with a complete lyrics pipeline.
</p>

<p align="center">
  <img alt="Platform Windows" src="https://img.shields.io/badge/Platform-Windows-0078D4?style=flat-square&logo=windows11&logoColor=white" />
  <img alt="Platform macOS" src="https://img.shields.io/badge/Platform-macOS-111111?style=flat-square&logo=apple&logoColor=white" />
  <img alt="Platform Linux" src="https://img.shields.io/badge/Platform-Linux-FCC624?style=flat-square&logo=linux&logoColor=111111" />
</p>

<p align="center">
  <a href="./README.zh-CN.md">简体中文</a>
  ·
  <a href="https://github.com/fengjay01/SoundFlow">GitHub</a>
  ·
  <a href="https://github.com/fengjay01/SoundFlow/issues">Issues</a>
  ·
  <a href="https://github.com/pymss-project/pymss-studio/releases">Upstream releases</a>
  ·
  <a href="https://github.com/pymss-project/pymss">pymss core</a>
</p>

<p align="center">
  <img alt="Tauri 2" src="https://img.shields.io/badge/Tauri-2-24C8DB?style=for-the-badge&logo=tauri&logoColor=white" />
  <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3-42B883?style=for-the-badge&logo=vuedotjs&logoColor=white" />
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-5-3178C6?style=for-the-badge&logo=typescript&logoColor=white" />
  <img alt="License: AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-111827?style=for-the-badge" />
</p>

<p align="center">
  <img src="./images/screenshots/separate/batch/en-light.webp" alt="SoundFlow separation workspace (inherited upstream UI)" width="900" />
</p>

## About

SoundFlow is a standalone desktop project built on [Pymss Studio](https://github.com/pymss-project/pymss-studio) around one addition: an **end-to-end lyric timeline workspace**. The upstream desktop app (Tauri + Vue + Python Worker around the [pymss](https://github.com/pymss-project/pymss) separation engine) stays intact — model library, separation jobs, workflows, stem editor, audio tools and plugin marketplace — and a new **Lyrics** page takes an audio file plus LRC / plain text / ASR transcription through forced alignment, word-level editing and multi-format export (LRC, TTML, SRT, KRC, ASS, JSON, standalone HTML players).

Separation and alignment run locally. Preparing runtimes, models, or optional components requires a network connection; downloaded models can then process local audio.

> [!NOTE]
> This is **not an official upstream release** and there is no SoundFlow installer yet — run it from source (see [Developer quick start](#developer-quick-start)). Legacy identifiers are kept on purpose for upgrade compatibility: the `PYMSS_STUDIO_*` environment variables, the `pymss-studio:` settings prefix, the Tauri identifier, and the `pymss` / `pymss-core` package names.

[Developer quick start](#developer-quick-start) · [Features](#features) · [Lyrics workspace](#lyrics-workspace) · [Getting the app](#getting-the-app) · [Quick start](#quick-start) · [Preview](#preview) · [Troubleshooting](#troubleshooting) · [Development](#development-and-verification)

## Developer quick start

SoundFlow runs from source and targets developers; no installers are distributed. **The commands are identical on Windows, macOS and Linux.** `pnpm` is the only entry point — the only real platform differences are Tauri's system packages and the venv interpreter path, both absorbed by [`scripts/dev-env.mjs`](./scripts/dev-env.mjs).

The steps below follow the order you actually run them; for frontend-only work jump straight to [step 3](#3-ui--copy--i18n-only-lightweight-path).

### 1. Prepare the toolchain

Required versions match upstream; [`setup/toolchain.json`](./setup/toolchain.json) is the single source of truth, and [`setup/README.md`](./setup/README.md) documents the environment/model layering:

- Node.js 22 and `pnpm@10.33.2` (Node 22 ships `corepack`, so no global install is needed)
- Rust stable / Cargo and [Tauri's platform prerequisites](https://v2.tauri.app/start/prerequisites/)
- Python 3.12
- FFmpeg / FFprobe only when developing the audio tools

Install the Tauri system packages and Python 3.12 for your OS — copy just the matching block:

**Windows** (PowerShell / CMD)

```bash
# Tauri: install the WebView2 Runtime and the C++ build tools (Visual Studio Build Tools)
winget install --id Python.Python.3.12 -e       # Python 3.12 (or install from python.org)
```

**macOS**

```bash
xcode-select --install                          # Xcode command line tools for Tauri
brew install python@3.12                        # Python 3.12
```

**Linux (Ubuntu)**

```bash
sudo apt-get install -y --no-install-recommends \
  build-essential pkg-config libasound2-dev \
  libwebkit2gtk-4.1-dev libssl-dev libxdo-dev \
  libayatana-appindicator3-dev librsvg2-dev   # Tauri system packages
sudo apt-get install -y python3.12 python3.12-venv  # Python 3.12 (Ubuntu 22.04 may need the deadsnakes PPA)
```

### 2. Clone, launch and verify

```bash
git clone https://github.com/fengjay01/SoundFlow.git && cd SoundFlow
corepack pnpm dev:setup:desktop   # install deps + create .venv/bootstrap (Python 3.12 + requests[socks]) + check
corepack pnpm dev:desktop         # Tauri window + frontend hot reload + Rust / Python worker
corepack pnpm test:all            # same three-layer verification as CI
```

### 3. UI / copy / i18n only (lightweight path)

This path needs only Node 22 + pnpm — skip Rust, Python and the Tauri system packages in step 1:

```bash
corepack pnpm dev:setup                         # install frontend deps + read-only check
corepack pnpm dev                               # browser at http://localhost:1420
corepack pnpm test:frontend && corepack pnpm build  # frontend tests + production build
```

### 4. Windows one-click launch (optional)

Double-click the root [`dev-local.bat`](./dev-local.bat) (equivalent to `pnpm dev:desktop`); `dev-local.bat --check` verifies Node / pnpm without launching the app.

**Elsewhere:** commands and verification in [Development and verification](#development-and-verification). Inference runtimes (torch / CUDA / `pymss`) are installed inside the app via **Settings → Runtime**, not locally; model weights are downloaded in the model library. Stuck? Run `corepack pnpm env:doctor`.

## Features

| Area | Capabilities |
| --- | --- |
| Lyric timeline (new) | Import LRC or plain text, or transcribe audio; align with Qwen3-ForcedAligner / WhisperX+DP / Paraformer / MMS / TIFA; edit word-level timings; export LRC, TTML, SRT, KRC, ASS, JSON and standalone HTML players. |
| Lyrics model manager (new) | 15 models grouped as `asr` / `alignment` / `diarization` / `separation`, with download-source selection (ModelScope / HF mirror / HTTP), verification, cleanup and card/list views. |
| Source separation | Import audio, video, or folders; process batches; select model outputs, device, format, folder layout, and naming rules. |
| Model ensembles | Select multiple models and output stems, configure an ensemble algorithm and weights, and save the combined output. |
| Model library | Search by name, purpose, architecture, or source; switch card/list views; manage favorites, notes, resumable downloads, storage, and default inference parameters. |
| Custom models | Import weights and configurations for supported architectures and use existing models in the separation workflow. |
| Workflows | Arrange separation, audio processing, ensemble, and save steps in the simple editor; use the advanced node editor for flexible connections, parameters, and import/export. |
| Task queue | Track queued and running jobs, progress and logs; cancel or retry jobs and organize completed session entries. |
| Results and projects | Review output stems and processing parameters, audition audio, open output folders, and continue in the editor. |
| Stem editor | Multitrack waveforms, clip editing, transport, volume/pan, mute/solo, fades, track effects, recording, project persistence, asset relinking, and mix export. |
| Audio tools | Format conversion, audio inspection, SDR / SI-SDR, ASR, vocal-to-MIDI conversion, silence slicing, and audio merging. |
| Runtimes and settings | Manage platform-appropriate CPU, CUDA, ROCm, and MLX environments; configure download sources, proxies, concurrency, paths, language, zoom, and themes. |

The first two rows are SoundFlow additions; the remaining capabilities are inherited from upstream Pymss Studio.

Available stems, parameters, and devices vary by model and runtime. Acceleration depends on the model, hardware, drivers, and task configuration.

## Lyrics workspace

Open **Lyrics** in the sidebar. It has three tabs: **Workbench**, **Editor**, and **Model manager**. This is the main addition in SoundFlow; everything else in the app is inherited from upstream Pymss Studio.

### Pipeline

1. Choose the audio file. Optionally enable **vocal separation chaining** and pick a separated vocal track — aligning on the original mix is the most common cause of poor word-level accuracy.
2. Choose a lyric source:
   - **Import LRC** — line positions come from the LRC timestamps; the engine only refines word/character timings. The input is archived as `<stem>.source.lrc`.
   - **Plain text** — paste or import `.txt`; lyrics can also be pulled from a Netease Cloud link or an online lyric search.
   - **No lyrics (ASR)** — transcribe with Qwen3-ASR or faster-whisper, then align.
3. Pick an alignment engine: `qwen` / `qwen-ultra` / `qwen-ultra-head` (Qwen3-ForcedAligner; the last one adds a local head for boundary refinement), `whisper` (WhisperX + DP), `paraformer` (FunASR), `mms` (MMS-FA), or `tifa` (optional engine, reuses the active lyrics runtime and falls back to WhisperX). `stars` is listed but not wired up yet. Qwen engines handle a single segment up to ~290 s; for longer tracks select an ASR model so segments can be located, or use an engine without that limit.
4. Start the job. The button stays disabled until the prerequisites are met: a scanned directory with paired files in directory mode, or an audio file plus its lyrics for a single song. The task log reports each stage — audio ready, aligner loaded, aligned lines/characters, and every exported file.
5. Review the word-level timeline in the Workbench, then fine-tune line text, translation and start/end times in the **Editor** (playback-synced highlighting; all rows render at once in a viewport-height scrolling table with a sticky header — there is no pagination).

Switches: tail extension, drag overlap, layered lyrics, auto supplement, next-line preview, and **even-spread estimate** (extra `.even.*` files where characters are evenly distributed inside each line — useful as a baseline comparison).

**Batch, import and cleanup:** the Workbench processes either a single song or a whole directory. Directory mode pairs each media file with a same-stem `.lrc`/`.txt` file (case-insensitive, LRC preferred), writes one output folder per song, and reports queue progress with cancel; audio and lyric files can also be dragged onto the page. Deleting finished jobs (singly or in bulk) removes the whole product folder, including custom output directories, with a permanent-delete fallback when the recycle bin refuses. Every file or directory input has a built-in clear button and a compact picker.

**Separation output settings:** vocal separation chaining exposes the output directory, format, layout (flat / per-song folders), a naming template with tokens, stem selection, TTA, MP3 bitrate and advanced inference parameters; repeated runs overwrite instead of producing `_2`/`_3` copies.

### Outputs

All files are written to the task output directory, prefixed with the audio stem. The desktop UI currently exports lyric/subtitle files only; it does not burn a video.

| File | Contents |
| --- | --- |
| `<stem>.lyrics.json` | Canonical word-level timeline (also used by the Editor). |
| `<stem>.ass`, `<stem>.lyric.ass` | Word-by-word KTV ASS and line-by-line ASS. |
| `<stem>.lrc` / `<stem>.enhanced.lrc` | Standard / enhanced LRC. |
| `<stem>.ttml`, `<stem>.furigana.ttml` | Apple Music / AMLL-style TTML and the furigana variant. |
| `<stem>.srt`, `<stem>.krc` | SRT subtitles and KRC (Kugou) lyrics. |
| `<stem>.player.html`, `<stem>.lyric.player.html`, `<stem>.source.player.html` | Standalone players: word-level, line-level, original LRC timeline. |
| `<stem>.even.lyrics.json`, `.even.ass`, `.even.player.html` | Even-spread baseline (only when enabled). |
| `<stem>.source.lrc` / `.source.txt` | Archived input lyrics. |

### Models

Lyrics models are managed from **Lyrics → Model manager**, separately from the separation model library. [`python/lyrics/l2t_model_manifest.json`](./python/lyrics/l2t_model_manifest.json) declares 15 models with per-model sources (Hugging Face, ModelScope, HTTP, GitHub release, bundled). They are stored under the data root in `models/_tool_models/lyrics/models/{asr,alignment,diarization,separation}/`; all readers still fall back to the older flat layout. Downloads are resumable and verified, and the page offers refresh, search, category/source filters, sorting, download/cancel, verify and delete. The two registries (HF snapshots and flat `asr/` directories) cross-check each other: either layout counts as installed, downloads reuse the other layout's copy, and deletion clears both.

The alignment and export chain follows the approach of [6hdj/lrc-ttml](https://github.com/6hdj/lrc-ttml) (MIT): dual alignment (faster-whisper + DP), LRC-anchored slices with lyric prompts, word-level timelines, layered lyrics and Apple/AMLL TTML export.

> [!WARNING]
> TIFA runs in the active lyrics runtime by default (zero configuration): selecting the engine points `LYRICS_TIFA_PYTHON` at the Worker interpreter, and a model under `alignment/tifa/` is picked up automatically. `LYRICS_TIFA_PYTHON` / `LYRICS_TIFA_MODEL` can still point to an external interpreter and model directory; TIFA falls back to WhisperX when unavailable. Its model is CC BY-NC-SA (non-commercial), and the Meta MMS-FA aligner weights are CC BY-NC 4.0 (also non-commercial). All other model weights keep their own licenses.

## Getting the app

SoundFlow is currently **source-only**: no installer or portable package is published for this project. Clone and run it locally as described in [Developer quick start](#developer-quick-start). If you need a ready-to-use installer for the inherited separation features, use the [upstream Pymss Studio releases](https://github.com/pymss-project/pymss-studio/releases), read its release notes and check **Assets** (mirrors are linked there when GitHub is inaccessible).

> [!NOTE]
> The tables below describe **upstream** release packages, whose bundled binary is still named `Pymss Studio`; a locally built SoundFlow binary uses the new name. The upstream project is under active development and current releases include prereleases — read the relevant release notes and back up important projects and settings before upgrading.

### Platforms and runtimes

| Platform / hardware | Runtime or package identifier | Notes |
| --- | --- | --- |
| Windows x64, NVIDIA GPU | `windows-x64-cuda` | CUDA requires a compatible GPU and driver. |
| Windows x64, CPU inference | `windows-x64-cpu` | A starting point when GPU compatibility is uncertain. |
| Windows x64, supported AMD GPU | `windows-x64-rocm` | Check the selected release's GPU and driver requirements; ROCm has a narrower support range. |
| Windows x64, smaller initial download | `windows-x64-online` | Includes the bootstrap runtime; install the required inference environment after first launch. |
| macOS 14+, Apple Silicon | `macos-arm64-mlx` | Uses MLX. Download the DMG and drag the application into `/Applications`. |
| Linux x64 | `linux-x64-appimage` | The release workflow builds an AppImage; check the selected release's Assets for availability. |
| Intel Mac | No dedicated release package | The existing MLX package targets Apple Silicon; source builds still require checking inference dependency compatibility. |

Release packages include Python, so end users do not need to install system Python separately. The macOS DMG includes the fixed Python, PyTorch, MLX, and bundled core packages; first launch does not download another Python environment. Open **Settings → Runtime** to inspect the active runtime and update its core. Installing, switching, reinstalling, or deleting an entire environment in the application is available on Windows and Linux.

### File types

| File | Purpose |
| --- | --- |
| Windows `*-setup.exe` | Installer; application data defaults to the user directory. |
| Windows `*.7z` | Portable package. Extract the complete directory and run `Pymss Studio.exe`; keep its runtime and tools directories. |
| macOS `*.dmg` | Apple Silicon installation image. |
| Windows `*-update.zip` | Update package for an existing installation, rather than a first-time installer. Follow the corresponding release notes. |
| Split files such as `*.part-000` | Download every part and follow the release's `SPLIT-ASSETS-README.txt` to merge them before extraction. |

If quarantine attributes prevent a macOS application from a trusted source from opening, apply this command to the installed application:

```bash
xattr -cr '/Applications/Pymss Studio.app'
```

## Quick start

Steps 1-5 are the inherited separation flow; step 6 is the added lyrics flow.

1. **Finish onboarding.** Choose language, theme, data directory, and runtime. The Online package needs to finish installing an inference environment.
2. **Prepare a model.** Open **Models**, download a model for the target source, or import a supported custom model.
3. **Configure separation.** Open **Separate**, import audio/video files or a folder, then choose the model, output stems, directory, and format. Start with the model's default parameters.
4. **Run and review.** Select **Start Separation**, monitor progress and logs in the task queue, and audition the finished stems or open **Results**.
5. **Edit and export.** Open a result in the stem editor, adjust clips, volume, pan, and effects, save the project, and use **Export Mix** to create an audio file.
6. **Build a lyric timeline (SoundFlow).** Open **Lyrics**, pick the audio and a lyric source (LRC / plain text / ASR), choose an alignment engine, then review and export from the [Lyrics workspace](#lyrics-workspace).

For a repeatable processing chain, open or create a workflow under **Workflows** in a separate editor window, choose files or a folder on its input nodes and directories on its output nodes, then save and run directly in the editor. Follow node progress on the canvas; edits made during execution apply to the next run. A short input can help confirm the model, device, and outputs before a larger batch.

## Preview

These previews come from the inherited upstream UI, so the screenshots still show the old **Pymss Studio** name. The new Lyrics page has no screenshots yet. Both languages and light/dark themes are shown at 1920 × 1080; click an image to view it at full size.

| Feature | Light theme | Dark theme |
| --- | --- | --- |
| Batch separation and output configuration | <a href="./images/screenshots/separate/batch/en-light.webp"><img src="./images/screenshots/separate/batch/en-light.webp" alt="Batch separation and output configuration" width="420" /></a> | <a href="./images/screenshots/separate/batch/en-dark.webp"><img src="./images/screenshots/separate/batch/en-dark.webp" alt="Batch separation and output configuration" width="420" /></a> |
| Model library and categories | <a href="./images/screenshots/models/library/en-light.webp"><img src="./images/screenshots/models/library/en-light.webp" alt="Model library and categories" width="420" /></a> | <a href="./images/screenshots/models/library/en-dark.webp"><img src="./images/screenshots/models/library/en-dark.webp" alt="Model library and categories" width="420" /></a> |
| Simple workflow editor | <a href="./images/screenshots/workflows/simple/en-light.webp"><img src="./images/screenshots/workflows/simple/en-light.webp" alt="Simple workflow editor" width="420" /></a> | <a href="./images/screenshots/workflows/simple/en-dark.webp"><img src="./images/screenshots/workflows/simple/en-dark.webp" alt="Simple workflow editor" width="420" /></a> |
| Advanced node workflows | <a href="./images/screenshots/workflows/advanced/en-light.webp"><img src="./images/screenshots/workflows/advanced/en-light.webp" alt="Advanced node workflows" width="420" /></a> | <a href="./images/screenshots/workflows/advanced/en-dark.webp"><img src="./images/screenshots/workflows/advanced/en-dark.webp" alt="Advanced node workflows" width="420" /></a> |
| Results and audio stems | <a href="./images/screenshots/results/en-light.webp"><img src="./images/screenshots/results/en-light.webp" alt="Results and audio stems" width="420" /></a> | <a href="./images/screenshots/results/en-dark.webp"><img src="./images/screenshots/results/en-dark.webp" alt="Results and audio stems" width="420" /></a> |
| Stem editing and mixing | <a href="./images/screenshots/editor/workspace/en-light.webp"><img src="./images/screenshots/editor/workspace/en-light.webp" alt="Stem editing and mixing" width="420" /></a> | <a href="./images/screenshots/editor/workspace/en-dark.webp"><img src="./images/screenshots/editor/workspace/en-dark.webp" alt="Stem editing and mixing" width="420" /></a> |

<details>
<summary>Audio tools, settings, and operation dialogs</summary>

| Feature | Light theme | Dark theme |
| --- | --- | --- |
| Multi-model ensemble configuration | <a href="./images/screenshots/separate/ensemble/en-light.webp"><img src="./images/screenshots/separate/ensemble/en-light.webp" alt="Multi-model ensemble configuration" width="420" /></a> | <a href="./images/screenshots/separate/ensemble/en-dark.webp"><img src="./images/screenshots/separate/ensemble/en-dark.webp" alt="Multi-model ensemble configuration" width="420" /></a> |
| Custom model import | <a href="./images/screenshots/models/import/en-light.webp"><img src="./images/screenshots/models/import/en-light.webp" alt="Custom model import" width="420" /></a> | <a href="./images/screenshots/models/import/en-dark.webp"><img src="./images/screenshots/models/import/en-dark.webp" alt="Custom model import" width="420" /></a> |
| Mix export | <a href="./images/screenshots/editor/export/en-light.webp"><img src="./images/screenshots/editor/export/en-light.webp" alt="Mix export" width="420" /></a> | <a href="./images/screenshots/editor/export/en-dark.webp"><img src="./images/screenshots/editor/export/en-dark.webp" alt="Mix export" width="420" /></a> |
| ASR speech recognition | <a href="./images/screenshots/tools/asr/en-light.webp"><img src="./images/screenshots/tools/asr/en-light.webp" alt="ASR speech recognition" width="420" /></a> | <a href="./images/screenshots/tools/asr/en-dark.webp"><img src="./images/screenshots/tools/asr/en-dark.webp" alt="ASR speech recognition" width="420" /></a> |
| Vocal-to-MIDI conversion | <a href="./images/screenshots/tools/midi/en-light.webp"><img src="./images/screenshots/tools/midi/en-light.webp" alt="Vocal-to-MIDI conversion" width="420" /></a> | <a href="./images/screenshots/tools/midi/en-dark.webp"><img src="./images/screenshots/tools/midi/en-dark.webp" alt="Vocal-to-MIDI conversion" width="420" /></a> |

</details>

Screenshots render the current Vue pages with isolated presentation data. Task progress, download states, and waveforms illustrate the interface and do not represent performance measurements.

## Audio tools

Open **Tools** to use these utilities independently of the separation workflow.

| Tool | Capabilities and input requirements |
| --- | --- |
| Audio conversion | Batch output to WAV, FLAC, MP3, or OGG, with sample rate, channel, and encoding settings. |
| Audio inspection | Read containers, audio streams, codec parameters, and metadata tags with FFprobe. |
| SDR evaluation | Align reference and estimated audio, then calculate per-channel SDR and SI-SDR. Requires corresponding reference audio. |
| ASR | Use FunASR presets or local models to generate TXT, JSON, and SRT. Supported languages vary by preset. |
| Vocal to MIDI | Extract pitch and note timing with GAME's native Torch model. Extract a complete GAME model archive, then select its `model.pt`. |
| Audio slicing | Analyze silence and export clips, with minimum-length, threshold, and boundary-silence settings. |
| Audio merging | Order a directory's files by name, modification time, or a regular expression, then export a WAV with consistent audio settings. |

The optional FunASR component installs on demand; ASR models download and cache on first use. The ASR tool is currently unavailable on macOS. Download a model archive from the [GAME release](https://github.com/openvpi/GAME/releases/tag/v1.0.0) linked in the MIDI tool. **Keep the complete extracted directory: `model.pt` requires `config.yaml` beside it, and language-conditioned models also require `lang_map.json`.** Do not move the weights alone. Language, output, and parameter options depend on the selected tool and model.

## Plugin marketplace

Open **Plugins** in the sidebar to browse and search the official catalog. Install and activate a Python runtime in Settings first. **Install from source** accepts a plugin name, a Git repository URL, or a local folder; repository subdirectories use `URL#path/to/plugin`.

The Installed tab shows loading errors, operations, and update checks. Marketplace plugins support update, dependency repair, and uninstall. Plugins in the project-local `data/plugins/` directory (or `PYMSS_PLUGINS_DIR`) are also listed read-only, with files and dependencies managed outside the marketplace. The default no longer falls back to `~/.pymss/plugins`. Plugins run Python code with your user permissions.

Sources and additional dependencies are stored in `runtime-envs/plugins/<runtime-identity>/installed/` under the application data directory. Dependencies resolve against the active runtime with host versions constrained. Incompatible requirements fail without replacing the working plugin. Updates are staged and verified before activation. Each runtime has its own marketplace installations.

**Refresh catalog** on Browse retrieves available plugins; **Check for updates** on Installed compares installed versions. Local development sources also compare package contents to detect same-version component changes. **Open tool** on each card opens that plugin's page. Reverification in the overflow menu only checks loading status. File updates retain installed dependencies unless dependency installation is explicitly selected in the update dialog. When **Check for updates** finds newer versions, an update list lets you select which plugins to update or use **Update all** for one-click batch updating. The batch dialog offers the same dependency option; updates run sequentially and their progress and results are available in the log.

Plugins declare their category in `[tool.pymss.studio] category`: `convert`, `analyze`, `recognize`, or `edit`. Missing categories appear under Other. The marketplace and Tools use the same metadata, with package-owned `locales` for titles, descriptions and operation labels. Plugins contribute operations through `setup(plugin)` and require pymss operations support. Legacy capability or graph-node plugins may have no generic operations. Installation does not add dynamic nodes to the SoundFlow workflow editor.

## Data, runtimes, and updates

### Data locations

Settings, models, outputs, projects, logs, and temporary files share a data root. Inspect actual paths under **Settings → Data Directory**; model and output directories can be configured separately. The table below describes installed release packages; a source checkout uses the project-local layout in [`setup/README.md`](./setup/README.md).

| Distribution | Default data root |
| --- | --- |
| `PYMSS_STUDIO_DATA_ROOT` is set | The supplied path takes priority. |
| Development binary run directly from Cargo `target/debug` or `target/release` | `data/` in the project root. |
| Windows portable package with a `pymss-studio.portable` marker beside the executable | `data/` beside the executable. |
| Regular installed / other release packages | `.pymss-studio/` in the user directory, such as `%USERPROFILE%\.pymss-studio` on Windows. |

The root defaults to `settings/`, `models/`, `outputs/`, `editor-projects/`, `logs/`, and `temp/`. On macOS and Linux, writable runtime state is stored in `runtime-envs/` under this root: `active-runtime.json` selects the current core, `.overlays/` contains core updates and their dependencies, and `optional-packages/` contains separately installed components, and `pymss-runtime-state.json` records each backend's `lyricsReady` flag. On Windows, these paths remain under the selected runtime root's `runtime-envs/`, generally in `python-runtime/runtime-envs/` beside the installation resources. The macOS base Python environment stays inside the application bundle.

**Lyrics models live in a separate tree.** SoundFlow stores them under `models/_tool_models/lyrics/models/{asr,alignment,diarization,separation}/` in the data root, independent of the separation `models/` directory; manage them in **Lyrics → Model manager**. The `PYMSS_STUDIO_*` variable names and the `pymss-studio:` settings prefix are legacy identifiers kept for upgrade compatibility.

**Editor projects reference audio files by path.** Back up the associated audio as well as the project. Relink missing assets in the editor after moving or deleting original outputs. Use the application's migration workflow when changing the model directory.

### Updates and environment management

Open **Settings → About** for version and update information. Windows and macOS builds whose release channel supports in-app updates can check and install updates there. Use the release page for builds without automatic update support. On macOS, an application update replaces the entire `.app`, preserving user data; administrator authorization is requested if write permissions are insufficient. Move the app to `/Applications` before updating: updates are blocked from a read-only DMG or App Translocation location. A change to the fixed base runtime contract requires a manual installation.

Use **Settings → Runtime** to update `pymss`, `pymss-core`, and their compatible dependencies independently of the desktop application. Review the prepared version and dependency plan before confirming. Packages are installed into a new core overlay and activated only after validation; a compatible previous core can be restored when available. Updates that require replacing fixed accelerator components are rejected, preserving the active runtime. Dependency combinations are defined in [`python/runtime-manifest.json`](./python/runtime-manifest.json).

**Lyrics dependencies share this runtime.** [`python/runtime-manifest.json`](./python/runtime-manifest.json) now defines `lyrics` / `lyricsPackages` / `lyricsOptional` / `lyricsOptionalPackages` / `lyricsOptionalWheels` groups (whisperx, faster-whisper, ctranslate2, pypinyin, demucs, optional alignment engines, and the Qwen3-FA Ultra companion wheel mirrors). **Settings → Runtime → Install / Repair this environment** installs them into the active runtime alongside `pymss`, sharing one Torch. Lyrics commands reuse that runtime once its `pymss-runtime-state.json` records `lyricsReady`; environments built before this change keep falling back to the bootstrap interpreter until they are reinstalled. **Detect environment** probes the actual interpreter and lists missing core and optional lyrics dependencies — a missing optional engine does not break the core pipeline. Do not create a separate `.venv-asr`; the FunASR component for the audio tools is still managed as its own optional component.

Optional components such as FunASR use their own directories, separate from core overlays. Installation checks compatibility with the current PyTorch version and accelerator backend. On Windows and Linux, uninstalling FunASR removes its separate component directory and any copy installed directly in the active environment; downloaded models are kept. On macOS, component operations preserve packages in the bundled base runtime; the ASR tool remains unavailable. Pip and Numba caches are stored under the data root's `temp/` directory (in dev builds, `dev-env.mjs` pins pip to `.venv/cache/pip` instead).

On macOS, deleting `SoundFlow.app` (or the upstream `Pymss Studio.app`) removes the application and bundled runtime while retaining the user data root, including settings, models, outputs, and core overlays. Removing and reinstalling the app therefore reuses retained data. Custom model/output directories and `.pymss-studio-previous-*.app` update backups beside the application must be managed separately.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Model absent from the separation page | Only downloaded models that support inference appear. Check download status, model support, and runtime readiness, then refresh the model list. |
| GPU missing or inference fails | Confirm the active backend, GPU/driver support, and model compatibility. Compare with CPU inference and inspect task logs. |
| Insufficient GPU or system memory | Keep task concurrency at 1 initially. Reduce batch or chunk size where supported; parameter meanings vary between architectures. |
| Runtime or model download fails | Check download source, method, and proxy under **Settings → Defaults**. Runtime installation also offers a PyPI mirror selector. Retry after confirming connectivity. |
| ASR or MIDI cannot start | Check FunASR installation and ASR model source. For GAME, use the complete extracted directory and check for `config.yaml` and, where needed, `lang_map.json` beside the weights. |
| Missing project assets | Check whether audio files moved or were deleted, relink them in the editor, and save the project again. |
| Lyrics: 0/N lines aligned, or evenly spaced characters | The LRC timeline may not match the audio version, or the aligner is hearing the backing track. Enable vocal separation chaining and re-run; check the task log. |
| Lyrics: Qwen aligner fails on a long song | A single Qwen segment is limited to ~290 s. Select an ASR model so segments can be located, or use an engine without that limit (Whisper / MMS / Paraformer). |
| Lyrics model download fails or stays pending | Refresh model status, switch the download source (ModelScope / HF mirror), and check proxy settings. Downloads are verified after completion; a failed file must be deleted before retrying. |
| Update fails or a new version behaves unexpectedly | Record version, package type, and errors; read the release notes. Back up projects and settings before manually installing a complete package. |

With developer mode enabled, open **Diagnostics → Logs** to filter by source, level, or task. SoundFlow, Worker, pymss, and external tools have separate source badges. Warnings and errors are highlighted, with expandable tracebacks, code locations, and context. Task, workflow node, download, and installation logs use the same presentation.

Launching the application from a command prompt or terminal also produces readable console logs. Supported terminals use colors for levels and sources; redirected output and `NO_COLOR` omit color codes. Application log files contain one JSON record per line, while historical text in cumulative logs remains readable without conversion.

**Diagnostics → Generate diagnostic report** writes `latest-diagnostic-report.md` with build/session details, recent page and tool visits, operation requests and outcomes, separation parameters, workflow batch/node correlations, task history and graph summaries, editor project/export summaries, model downloads, cached runtime probes, and update events. Requests are captured before validation or worker startup, preserving the selected feature and parameters when an environment is missing or process creation fails.

The report distinguishes observations from this process and saved snapshots, including their observation/save times. Generation does not rerun environment probes or scan media files. It retains at most 24 recent operations and 12 historical tasks, prioritizing active and failed tasks; fields, collections, store reads, and report sections have explicit limits and omission/unavailability notes. Request and configuration summaries use allow-lists and omit transcript text, audio contents, arbitrary graph text, and proxy addresses. Errors and log tails can still contain custom names and paths; review them before sharing.

Automatic error reports coalesce errors within a five-second interval and write the latest error and traceback afterward. Pending errors are flushed before normal application exit. Clearing logs cancels older pending reports.

If no window appears at startup, exit any existing SoundFlow process, open CMD in the installation or build directory, and enable verbose logging before the interface loads (for a source build, run `pnpm dev:desktop` and read its terminal output). The command below uses the upstream package name `Pymss Studio.exe`; a locally built binary is `SoundFlow.exe`:

```cmd
start "" /b /wait "Pymss Studio.exe" --debug
```

To save console output, run `start "" /b /wait "Pymss Studio.exe" --debug > "%TEMP%\pymss-startup.log" 2>&1`. The flag enables verbose SoundFlow and Worker logs for this process without changing the saved developer mode setting. A subsequent launch without the flag uses the original logging level. Exit an existing process before relaunching with the flag. On macOS and Linux, pass `--debug` directly to the application executable.

For unresolved issues, include application version and package type, OS version, GPU model, separation model, reproduction steps, and relevant log excerpts in [GitHub Issues](https://github.com/fengjay01/SoundFlow/issues). Remove personal paths, proxy credentials, and other sensitive information from logs before sharing.

## Development and verification

### Prerequisites

Commands are in [Developer quick start](#developer-quick-start) above. The toolchain contract lives in [`setup/toolchain.json`](./setup/toolchain.json) (Node 22 / pnpm 10.33.2 / Python 3.12 / Rust stable); `package.json` (`engines` / `packageManager`), `.nvmrc` and CI all align with it, and CI runs `pnpm test:all` plus the frontend build with the same versions. The full path and cache contract is documented in [`setup/README.md`](./setup/README.md).

There are only two real platform differences: the commands that install Tauri's system packages, and the venv interpreter path (`.venv/bootstrap/Scripts/python.exe` vs `.venv/bootstrap/bin/python`), handled by `dev-env.mjs` via `process.platform`. `pnpm dev:desktop` pins environment caches (pip / cargo / npm / pnpm / XDG / temp) inside `.venv/`, while caches that can hold model weights (HF / torch.hub / ModelScope) go to `data/models/_cache/` — all inside the checkout, so **nothing is written to the system drive**; override the interpreter with `PYMSS_STUDIO_PYTHON`. The CN network policy is opt-in: only `SOUNDFLOW_CN_NETWORK=1` enables mirrors; the default never touches a developer's proxy settings.

**Path contract.** Every default location is derived from code position — `__file__` in [`python/_project_paths.py`](./python/_project_paths.py), `CARGO_MANIFEST_DIR` in Rust, `import.meta.url` in Node — never from the user's home directory; environment variables only override it. The single layering rule is "can model weights appear here?": yes → `data/models/_cache/`, no → `.venv/`. Debug scripts and artifacts go to `_debug_output/` inside the repo root via `debug_out()` from [`scripts/debug_paths.py`](./scripts/debug_paths.py) and are excluded from commits. Run `pnpm env:paths` to review every pinned path; `pnpm env:doctor` and CI also assert `paths-local`, so a stray path fails the check.

Inference runtimes (torch / CUDA / `pymss`) are installed through **Settings → Runtime**. In dev builds they land in `.venv/python/runtime-envs/` (derived from `CARGO_MANIFEST_DIR` by `src-tauri/src/storage.rs`, identical on all three OSes, and safe from `cargo clean`); model weights are downloaded in the model library. The same action installs the lyrics dependencies into that runtime — no separate lyrics environment is needed. FFmpeg / FFprobe are only needed for audio-tool development.

Stuck? `corepack pnpm env:doctor` prints a per-item report. Frontend changes need `pnpm test:frontend` and `pnpm build`; Worker or backend changes need the corresponding layer's tests. `pnpm test:all` runs all three layers under one environment injected by `dev-env.mjs`, so the temporary files and cargo cache of `node --test` and `cargo test` also stay inside `.venv/` — **nothing is written to the system drive**, matching CI.

### Commands

`python/plugin-manifest.json` declares the official repository and an optional preinstallation list. The default list is empty: SoundFlow builds do not fetch plugin sources, and users choose tools from the marketplace. Opening Tools without installed plugins shows an installation entry. Existing installed and disabled plugins are preserved.

For local plugin development, build the plugin checkout and select its source through Diagnostics → Plugin source. Install or update the desired packages through the marketplace. Optional offline distributions may fill the preinstallation list and run `pnpm plugins:prepare --release`; pin the build `ref` to a commit. Prepared plugin files are ignored build resources. `pnpm plugins:prepare --check` verifies resource integrity without network access.

The project defaults to its configured remote repository, including during desktop development. For temporary debugging on one machine, enable developer mode and use Diagnostics → Plugin sources to select a local checkout or edit the remote URL and catalog branch. Overrides are stored only in that machine's application settings as `plugin-source.json` and apply while developer mode is enabled. Restoring defaults clears the saved override. Explicit environment overrides still apply, with `PYMSS_PLUGINS_REGISTRY` taking precedence.

| Command | Purpose |
| --- | --- |
| `pnpm dev:setup` | **First command for new developers**: install frontend deps + read-only check; installs no Python / Rust. |
| `pnpm dev:setup:desktop` | Desktop onboarding: also creates the Python 3.12 `.venv/bootstrap`. |
| `pnpm env:check` | Read-only environment and toolchain check (`dev-env.mjs check`). |
| `pnpm env:bootstrap` | Create the project-local Python bootstrap venv (`dev-env.mjs bootstrap`). |
| `pnpm env:doctor` | Per-item diagnosis (including FFmpeg / FFprobe and the `paths-local` path assertion) with platform-specific hints. |
| `pnpm env:paths` | Print every pinned path and assert none escapes the checkout (`dev-env.mjs paths`); exits 1 on a stray path. |
| `pnpm dev` | Frontend server on port `1420` for UI development and browser previews. |
| `pnpm dev:desktop` | Cross-platform full desktop app: injects project-local env, then runs `pnpm tauri dev`. |
| `pnpm tauri dev` | Start the desktop app directly without the project-local env injection (advanced). |
| `pnpm worker -- env_info` | Debug the Worker with the resolved venv Python (`--help` / `env_info` / `list_models`). |
| `pnpm build` | TypeScript checks and the frontend production build. |
| `pnpm test` / `pnpm test:frontend` | Frontend logic tests. |
| `pnpm test:python` | Python Worker module tests; routed through `dev-env.mjs`, so no bare `python` on any OS. |
| `pnpm test:python:local` | Alias of `pnpm test:python`, kept for local habits. |
| `pnpm test:rust` | Rust backend tests. |
| `pnpm test:all` | Run frontend / Python / Rust sequentially under one shared `dev-env.mjs` environment; `node scripts/dev-env.mjs test:all --dry` previews only. Prepare each layer's dependencies first. |
| `pnpm plugins:prepare` | Prepare configured offline plugin resources (Python resolved cross-platform too). |
| `pnpm tauri build` | Desktop build; runs the frontend build first. A complete inference runtime must be prepared separately. |
| `python -X utf8 -m unittest discover -s python/lyrics -t python -p "test_*.py"` | Lyrics pipeline unit tests (`python/lyrics/test_*.py`). |
| `python -m unittest discover -s tests/python -p "test_project_paths.py" -v` | Path-contract unit tests (`tests/python/test_project_paths.py`). |
| `dev-local.bat` (Windows, optional) | Double-click to launch the desktop app (equivalent to `pnpm dev:desktop`); `--check` only verifies Node / pnpm. |

For documentation changes, verify content and links. Frontend changes should run relevant tests and `pnpm build`; Worker/backend changes should run the corresponding layer's tests. CI uses Node 22 / Python 3.12 / Rust stable for `pnpm test:all` plus the frontend build, and runs the frontend tests/build on ubuntu, macos and windows (see [ci.yml](./.github/workflows/ci.yml)).

<details>
<summary>Python Worker debugging and environment variables</summary>

Use a dedicated Python 3.12 environment for standalone Worker debugging. Prepare compatible Torch and `pymss` dependencies for the selected backend first. [`python/requirements.txt`](./python/requirements.txt) describes Worker development dependencies; [`python/requirements-ci.txt`](./python/requirements-ci.txt) describes CI test dependencies. Managed application environments follow the runtime manifest; [`python/lyrics/requirements.txt`](./python/lyrics/requirements.txt) is only a manual troubleshooting note for the lyrics dependencies, not a second environment to create.

```bash
pnpm worker -- --help
pnpm worker -- env_info
pnpm worker -- list_models --payload '{}'
```

`pnpm worker` uses the interpreter resolved by `dev-env.mjs` (`PYMSS_STUDIO_PYTHON` → `.venv/bootstrap` → legacy Windows layout → system Python), identically on all three OSes; `node scripts/dev-env.mjs python -- <args>` works too. Use the bundled or active environment's Python path when verifying a release package.

| Variable | Purpose |
| --- | --- |
| `PYMSS_STUDIO_DATA_ROOT` | Override the application data root. |
| `PYMSS_STUDIO_PYTHON` | Override bootstrap Python. Without an override, `dev-env.mjs` probes `.venv/bootstrap`, the legacy Windows layout, then system `python3.12` / `python3` / `python` (`py` on Windows). Inference may still use an activated environment. |
| `PYMSS_STUDIO_DEFAULT_OUTPUT_DIR` | Worker output fallback when none is specified; the desktop application passes its configured output directory to the Worker. |

</details>

<details>
<summary>Architecture, repository layout, and releases</summary>

| Layer | Stack and responsibilities |
| --- | --- |
| Frontend | Vue 3, TypeScript, Vite, Pinia, Vue Router, Vue I18n, and Naive UI; workflow node editing uses LiteGraph. |
| Desktop backend | Tauri v2 and Rust for dialogs, process orchestration, persistence, updates, and windows. |
| Worker | Python JSON protocol for models, inference, workflows, and audio processing. |
| Lyrics pipeline (SoundFlow) | `python/worker_lyrics.py`, `python/worker_lyrics_models.py` and `python/lyrics/*`: ASR, forced alignment, DP matching, ASS/TTML/LRC/SRT/KRC/player exporters and the model store. |
| Separation engine | External `pymss` / `pymss-core` packages. This repository integrates the desktop application rather than implementing core separation algorithms. |

```mermaid
flowchart LR
  UI[Vue / Naive UI] --> API[Tauri / Rust]
  API --> Worker[Python Worker]
  Worker --> Core[pymss / pymss-core]
  Core --> Audio[Separated stems]
  Audio --> Editor
  UI --> Editor[Stem editing and mix export]
  UI --> Lyrics[Lyrics workbench]
  Lyrics --> LyWorker[worker_lyrics + python/lyrics]
  LyWorker --> Files[LRC / TTML / ASS / SRT / KRC / player.html]
```

```text
.
├── src/                   # Views, components, stores, workflows, tools, and i18n
├── src-tauri/             # Rust backend, desktop configuration, and bundled resources
├── python/                # Worker, audio modules, and runtime manifest
├── python/lyrics/         # SoundFlow lyrics pipeline: ASR, alignment, exporters, model store
├── tests/                 # Frontend/Python tests, manual checks, and fixtures
├── native/                # Native audio host (Rust) and VST lab sources
├── setup/                 # Toolchain contract + environment layering (toolchain.json, README.md)
├── scripts/               # Dev env injection, test orchestration, path checks, release tools
├── _debug_output/         # Local debug artifacts (gitignored, inside the repo root)
├── .venv/                 # Local-only caches, bootstrap venv, runtime envs (gitignored)
├── data/                  # Local-only model library and caches (gitignored)
├── .github/workflows/     # CI and Windows/macOS/Linux release pipelines (adapted from upstream)
├── installer/             # Windows installer resources
└── images/screenshots/    # English/Chinese light/dark screenshots
```

Windows releases prepare bootstrap Python and a backend environment, build the Tauri executable, stage the Worker/runtime/media tools, and create installers and portable archives. macOS releases prepare arm64 MLX and media tools before creating a DMG. Linux releases build an AppImage.

```powershell
./scripts/prepare-python-runtime.ps1 -Variant cuda -InitialBackend cuda
./scripts/prepare-python-runtime.ps1 -Variant default -InitialBackend cpu
```

These are alternative runtime preparation examples; select one rather than running them sequentially. Preparation writes `python-runtime/`, requires network access, and involves large downloads. See the [Windows](./.github/workflows/release-windows.yml), [macOS](./.github/workflows/release-macos.yml) and [Linux](./.github/workflows/release-linux.yml) workflows for complete staging, signing, validation, and asset splitting. The pipelines are adapted from upstream Pymss Studio: publishing needs the `HF_TOKEN`, `MODELSCOPE_API_TOKEN` and `TAURI_SIGNING_*` repository secrets, and artifact names keep the legacy `Pymss-Studio-` prefix used by the installer.

</details>

## Future directions

- Finish wiring the STARS aligner, stabilise the optional TIFA adapter, and broaden multilingual forced alignment.
- Add Lyrics-page screenshots, packaged builds, and an in-app import → align → edit → re-export round trip.
- The upstream roadmap (Intel / AMD compatibility, runtime validation, mobile GPU/NPU research) continues in [Pymss Studio](https://github.com/pymss-project/pymss-studio); SoundFlow ships no mobile build.

## Community and contributing

Use [GitHub Issues](https://github.com/fengjay01/SoundFlow/issues) for SoundFlow-specific problems (lyrics pipeline, local launcher, docs). For issues in the inherited separation/runtime features, check the [upstream issues](https://github.com/pymss-project/pymss-studio/issues) first; the [upstream discussions](https://github.com/pymss-project/pymss-studio/discussions) and the Chinese [upstream community group](https://qm.qq.com/q/YLLou4NucE) cover general usage.

SoundFlow currently focuses on issues, reproduction reports and documentation fixes rather than a steady stream of external pull requests; see the Issues link above for how to contribute.

## License

SoundFlow is derived from Pymss Studio and keeps the GNU Affero General Public License v3.0. See [LICENSE](./LICENSE). The lyrics pipeline documents mappings derived from [6hdj/lrc-ttml](https://github.com/6hdj/lrc-ttml) (MIT); dependencies, model weights and third-party tools retain their own licenses and usage terms (TIFA's model is CC BY-NC-SA and Meta's MMS-FA weights are CC BY-NC 4.0, both non-commercial).
