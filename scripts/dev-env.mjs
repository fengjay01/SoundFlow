#!/usr/bin/env node
/**
 * Cross-platform, zero-dependency developer environment helper.
 *
 * Design rule (mirrors upstream pymss-studio): the DEVELOPER entry point is a
 * platform-neutral `pnpm` command. The only real differences between Windows,
 * macOS and Linux are the venv interpreter path and the system packages needed
 * by Tauri; both are absorbed here instead of in .ps1 / .bat files.
 *
 * Nothing is downloaded: system Node / Python / Rust are *borrowed*, while every
 * cache / temp / runtime WRITE is pinned inside the checkout (project rule).
 * Environment caches -> <repo>/.venv/cache; caches that can hold model weights
 * (HF_HOME / TORCH_HOME / MODELSCOPE_CACHE) -> <repo>/data/models/_cache.
 *
 * Versions come from setup/toolchain.json (single source of truth, shared with
 * package.json engines / packageManager and the CI workflows).
 *
 * Usage:
 *   node scripts/dev-env.mjs setup [--desktop] [--dry]
 *   node scripts/dev-env.mjs check [--tier frontend|desktop|full] [--json]
 *   node scripts/dev-env.mjs bootstrap [--python <path>] [--no-pip] [--allow-mismatch]
 *   node scripts/dev-env.mjs env [--cn] [--isolated]
 *   node scripts/dev-env.mjs paths            # assert every pinned path is inside the checkout
 *   node scripts/dev-env.mjs python -- <args...>
 *   node scripts/dev-env.mjs run [--cn] [--isolated] -- <command> [args...]
 *
 * CN network policy is opt-in: --cn or SOUNDFLOW_CN_NETWORK=1.
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { formatReport } from './paths-check.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const ENV_ROOT = path.join(ROOT, '.venv')
const VENV = path.join(ENV_ROOT, 'bootstrap')
const IS_WIN = process.platform === 'win32'
const VENV_PY = path.join(VENV, IS_WIN ? 'Scripts/python.exe' : 'bin/python')
const LEGACY_PY = path.join(ENV_ROOT, 'python', 'asr', IS_WIN ? 'Scripts/python.exe' : 'bin/python')
const BOOTSTRAP_PIP = 'requests[socks]>=2.32,<3'
const TOOLCHAIN = JSON.parse(fs.readFileSync(path.join(ROOT, 'setup', 'toolchain.json'), 'utf8'))
const NODE_MAJOR = Number(TOOLCHAIN.node)
const PY_VERSION = TOOLCHAIN.python
const PY_RE = new RegExp('^Python ' + PY_VERSION.replace('.', '\\.') + '\\.')
const TAURI_HINTS = TOOLCHAIN.tauriSystemDeps

const argv = process.argv.slice(2)
const command = argv.find(a => !a.startsWith('--')) || 'check'
const flags = new Set(argv.filter(a => a.startsWith('--')))
const dash = argv.indexOf('--')
const rest = dash >= 0 ? argv.slice(dash + 1) : []
const CN_NETWORK = flags.has('--cn') || process.env.SOUNDFLOW_CN_NETWORK === '1'
const opt = name => {
  const i = argv.indexOf(name)
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : undefined
}

function run(cmd, args = [], extra = {}) {
  return spawnSync(cmd, args, {
    cwd: ROOT,
    encoding: 'utf8',
    shell: IS_WIN,
    windowsHide: true,
    env: { ...process.env, ...extra },
  })
}
function versionOf(cmd, args = ['--version'], extraEnv) {
  const r = run(cmd, args, extraEnv || {})
  if (r.error || r.status !== 0) return null
  return `${r.stdout || ''}${r.stderr || ''}`.trim().split(/\r?\n/)[0] || null
}
function pythonCandidates() {
  return IS_WIN ? ['python3.12', 'python', 'py'] : ['python3.12', 'python3', 'python']
}
function firstWorkingPython() {
  for (const c of pythonCandidates()) {
    const v = versionOf(c)
    if (v && /^Python 3\./.test(v)) return { command: c, version: v }
  }
  return null
}
/** Prefer the pinned Python version: CI and upstream build against 3.12. */
function findPinnedPython() {
  for (const c of pythonCandidates()) {
    const v = versionOf(c)
    if (v && PY_RE.test(v)) return { command: c, version: v }
  }
  return null
}
/** Resolve the interpreter used by Tauri / Worker, never guessing a platform path. */
function resolvePython() {
  const explicit = process.env.PYMSS_STUDIO_PYTHON
  if (explicit && fs.existsSync(explicit)) return { command: explicit, version: versionOf(explicit), source: 'PYMSS_STUDIO_PYTHON' }
  if (fs.existsSync(VENV_PY)) return { command: VENV_PY, version: versionOf(VENV_PY), source: '.venv/bootstrap' }
  if (fs.existsSync(LEGACY_PY)) return { command: LEGACY_PY, version: versionOf(LEGACY_PY), source: '.venv/python/asr (legacy Windows layout)' }
  const sys = firstWorkingPython()
  return sys ? { ...sys, source: 'system' } : null
}

/**
 * All writes stay inside the checkout (project rule: never write to the system
 * drive). Cache/temp variables are FORCED: inheriting a shell's HF_HOME or TEMP
 * pointing at C: or another project would silently break that rule.
 * Interpreter / toolchain selection stays user-overridable.
 */
function pinnedEnv(options = {}) {
  const createDirs = options.createDirs !== false
  const env = { ...process.env }
  const set = (key, value) => { if (!env[key]) env[key] = value }
  const force = (key, value) => { env[key] = value }
  const cache = p => path.join(ENV_ROOT, 'cache', p)
  const tmp = path.join(ENV_ROOT, 'tmp')
  // Reuse the existing Windows self-contained layout when present, so this
  // helper never breaks `dev-local.bat` / its cargo GNU linker config.
  const legacyCargo = path.join(ENV_ROOT, 'rust', 'cargo')
  const legacyRustup = path.join(ENV_ROOT, 'rust', 'rustup')
  const legacyPip = path.join(ENV_ROOT, 'python', 'pip-cache')
  force('PIP_CACHE_DIR', fs.existsSync(legacyPip) ? legacyPip : cache('pip'))
  force('CARGO_HOME', fs.existsSync(legacyCargo) ? legacyCargo : cache('cargo'))
  force('npm_config_cache', cache('npm'))
  force('PNPM_HOME', cache('pnpm'))
  // Cache layering: environment caches stay in .venv, but HF / torch.hub /
  // ModelScope can materialise weight files, so those belong to the model root.
  const modelCache = p => path.join(ROOT, 'data', 'models', '_cache', p)
  force('HF_HOME', modelCache('hf'))
  force('TORCH_HOME', modelCache('torch'))
  force('XDG_CACHE_HOME', cache('xdg'))
  force('MODELSCOPE_CACHE', modelCache('modelscope'))
  force('PYTHONDONTWRITEBYTECODE', '1')
  force('TMP', tmp); force('TEMP', tmp); force('TMPDIR', tmp)
  // Pointing RUSTUP_HOME at an empty dir makes rustup shims report "no default
  // toolchain", so only pin it when a project-local toolchain already exists
  // or when the caller explicitly asks for full isolation.
  if (fs.existsSync(legacyRustup)) force('RUSTUP_HOME', legacyRustup)
  else if (flags.has('--isolated')) force('RUSTUP_HOME', cache('rustup'))
  // Upstream-native interpreter override: respect an explicit choice.
  const py = resolvePython()
  if (py) set('PYMSS_STUDIO_PYTHON', py.command)
  // Keep the L2 inference runtimes at the same project-local path on all OSes.
  force('PYMSS_STUDIO_RUNTIME_ENVS_OVERRIDE', path.join(ENV_ROOT, 'python', 'runtime-envs'))
  // Read-only plugin discovery has no equivalent Rust injection. `set` (not
  // force) keeps an explicit portable/CI override; the Python default in
  // python/_project_paths.py already points here when nothing is injected.
  set('PYMSS_PLUGINS_DIR', path.join(ROOT, 'data', 'plugins'))
  if (CN_NETWORK) {
    set('HF_ENDPOINT', 'https://hf-mirror.com')
    const domains = 'modelscope.cn,.modelscope.cn,modelscope.org,.aliyuncs.com,hf-mirror.com,huggingface.co,.hf.co'
    for (const key of ['NO_PROXY', 'no_proxy']) {
      env[key] = env[key] ? `${env[key]},${domains}` : domains
    }
  }
  if (createDirs) {
    for (const dir of ['cache/pip', 'cache/cargo', 'cache/npm', 'cache/pnpm', 'cache/xdg', 'tmp', 'python/runtime-envs']) {
      fs.mkdirSync(path.join(ENV_ROOT, dir), { recursive: true })
    }
    for (const dir of ['hf', 'torch', 'modelscope']) {
      fs.mkdirSync(modelCache(dir), { recursive: true })
    }
  }
  // If an existing Windows self-contained layout is present, put its toolchain
  // first so `pnpm dev:desktop` works without dev-local.bat. Missing dirs are
  // skipped, so macOS/Linux (system toolchains) are unaffected.
  const bins = [
    ['node', 'runtime'], ['node', 'npm-global'], ['mingw64', 'bin'],
    ['rust', 'cargo', 'bin'],
    ['rust', 'rustup', 'toolchains', 'stable-x86_64-pc-windows-gnu', 'bin'],
  ].map(parts => path.join(ENV_ROOT, ...parts)).filter(p => fs.existsSync(p))
  if (bins.length) {
    force('PATH', [...bins, env.PATH || env.Path || ''].filter(Boolean).join(path.delimiter))
  }
  return env
}

// ------------------------------------------------------------------ check
function check(tierOverride) {
  const tier = tierOverride || opt('--tier') || 'frontend'
  const rows = []
  const add = (id, status, detail, hint = '') => rows.push({ id, status, detail, hint })
  // Probe with the SAME environment `run` / `dev:desktop` will use. Otherwise
  // project-local cargo/pnpm are invisible here and `env:doctor` reports false
  // FAILs even though the desktop build actually works.
  const probeEnv = pinnedEnv({ createDirs: false })
  const probe = (cmd, args) => versionOf(cmd, args, probeEnv)

  const node = process.version
  const nodeMajor = Number(node.slice(1).split('.')[0])
  add('node', nodeMajor >= NODE_MAJOR ? 'OK' : 'FAIL', node,
    nodeMajor >= NODE_MAJOR ? '' : `Install Node.js ${TOOLCHAIN.node} (see .nvmrc / setup/toolchain.json); CI uses ${TOOLCHAIN.node}.`)

  const pnpm = probe('pnpm')
  const localPnpm = path.join(ENV_ROOT, 'node', 'npm-global', IS_WIN ? 'pnpm.cmd' : 'pnpm')
  if (pnpm) add('pnpm', 'OK', pnpm)
  else if (fs.existsSync(localPnpm)) {
    add('pnpm', 'WARN', 'installed in project but not on PATH',
      'Add .venv/node/npm-global to PATH (or use dev-local.bat); CI uses pnpm/action-setup.')
  } else {
    add('pnpm', 'FAIL', 'not on PATH',
      `Run \`corepack enable\` (Node ${TOOLCHAIN.node} ships corepack), or \`npm i -g pnpm@${TOOLCHAIN.pnpm}\`.`)
  }

  const lock = fs.existsSync(path.join(ROOT, 'pnpm-lock.yaml'))
  add('lockfile', lock ? 'OK' : 'WARN', lock ? 'pnpm-lock.yaml' : 'missing',
    lock ? '' : 'Run `pnpm install --frozen-lockfile` after cloning.')

  const py = resolvePython()
  if (tier === 'frontend') {
    add('python', 'SKIP', 'not needed for Tier 1 (pnpm dev / test:frontend / build)')
    add('cargo', 'SKIP', 'not needed for Tier 1')
  } else {
    add('python', py ? 'OK' : 'FAIL', py ? `${py.version || '?'}  [${py.source}]` : 'not found',
      py ? '' : `Install Python ${PY_VERSION} (setup/toolchain.json), then \`pnpm env:bootstrap\`.`)
    if (py && !PY_RE.test(py.version || '')) {
      add(`python-${PY_VERSION.replace('.', '-')}`, 'WARN',
        `CI / upstream / setup/toolchain.json pin Python ${PY_VERSION}`,
        `${TOOLCHAIN.pythonInstallHints[process.platform] || ''} — new venvs must be ${PY_VERSION}; an existing Windows .venv on Python 3.13.x is only a local legacy exception. Use bootstrap --allow-mismatch only for that environment.`)
    }
    const cargo = probe('cargo')
    add('cargo', cargo ? 'OK' : 'FAIL', cargo || 'not on PATH',
      cargo ? '' : `Install Rust ${TOOLCHAIN.rust} via https://rustup.rs (setup/toolchain.json).`)
    add('tauri-system-deps', 'INFO', TAURI_HINTS[process.platform] || TAURI_HINTS.linux)
    if (tier === 'full') {
      for (const tool of ['ffmpeg', 'ffprobe']) {
        const v = probe(tool)
        add(tool, v ? 'OK' : 'WARN', v || 'not on PATH',
          v ? '' : 'Only needed for audio tools; release packages bundle FFmpeg.')
      }
    }
  }

  // Executable path contract: no pinned cache/temp/runtime path may escape the
  // checkout, no matter which shell variables the developer happens to have.
  const strays = formatReport(probeEnv, ROOT).strays
  add('paths-local', strays.length ? 'FAIL' : 'OK',
    strays.length ? `${strays.length} pinned path(s) outside the checkout` : 'all pinned paths inside the checkout',
    strays.length ? strays.map(s => `${s.name}=${s.value}`).join(' | ') : '')

  if (flags.has('--json')) {
    console.log(JSON.stringify({
      platform: process.platform, tier, node,
      toolchain: { node: TOOLCHAIN.node, pnpm: TOOLCHAIN.pnpm, python: PY_VERSION, rust: TOOLCHAIN.rust },
      cnNetwork: CN_NETWORK, results: rows,
    }, null, 2))
  } else {
    console.log(`\n  SoundFlow env check  |  ${process.platform}  |  tier=${tier}  |  ${ROOT}\n`)
    for (const r of rows) {
      console.log(`  [${r.status.padEnd(4)}] ${r.id.padEnd(20)} ${r.detail}`)
      if (r.hint) console.log(`           -> ${r.hint}`)
    }
    console.log('')
    if (tier === 'frontend') console.log('  Tier 1 needs nothing else: pnpm dev / pnpm test:frontend / pnpm build\n')
  }
  const failed = rows.filter(r => r.status === 'FAIL')
  return failed.length ? 1 : 0
}

// -------------------------------------------------------------- bootstrap
function bootstrap() {
  if (fs.existsSync(VENV_PY)) {
    console.log(`  [env] SKIP: bootstrap venv already exists at ${VENV_PY}`)
    return 0
  }
  fs.mkdirSync(ENV_ROOT, { recursive: true })
  const allowMismatch = flags.has('--allow-mismatch')
  const requested = opt('--python') || process.env.PYMSS_STUDIO_PYTHON
  const hint = TOOLCHAIN.pythonInstallHints[process.platform] || TOOLCHAIN.pythonInstallHints.linux
  const base = requested || findPinnedPython()?.command
  if (!base) {
    console.error(`  [env] Python ${PY_VERSION} not found. This repo pins ${PY_VERSION} (setup/toolchain.json, CI, upstream).`)
    console.error(`  [env] Install: ${hint}`)
    console.error('  [env] Or point at an interpreter: pnpm env:bootstrap --python <path>  (and check PYMSS_STUDIO_PYTHON).')
    console.error('  [env] Legacy Windows 3.13.x setups only: add --allow-mismatch.')
    return 1
  }
  const baseVersion = versionOf(base)
  if (!baseVersion || !/^Python 3\./.test(baseVersion)) {
    console.error(`  [env] ${base} is not a usable Python 3: ${baseVersion}`)
    return 1
  }
  if (!PY_RE.test(baseVersion)) {
    if (!allowMismatch) {
      console.error(`  [env] ${baseVersion} does not match the pinned Python ${PY_VERSION}.`)
      console.error(`  [env] Install: ${hint}`)
      console.error(`  [env] If PYMSS_STUDIO_PYTHON or --python points at an old interpreter, unset it or pass --python <${PY_VERSION} path>.`)
      console.error('  [env] Existing non-3.12 setups may opt out once with --allow-mismatch.')
      return 1
    }
    console.log(`  [env] WARN: --allow-mismatch accepted ${baseVersion} (pinned: ${PY_VERSION}). Not CI-equivalent.`)
  }
  console.log(`  [env] creating minimal venv: ${base} -m venv ${VENV}`)
  const created = run(base, ['-m', 'venv', VENV], pinnedEnv())
  if (created.error || created.status !== 0) {
    console.error(`  [env] venv creation failed: ${created.error?.message || created.stderr}`)
    return 1
  }
  if (!fs.existsSync(VENV_PY)) {
    console.error('  [env] venv created but interpreter missing: ' + VENV_PY)
    return 1
  }
  if (!flags.has('--no-pip')) {
    console.log(`  [env] installing bootstrap dependency: ${BOOTSTRAP_PIP}`)
    const pip = run(VENV_PY, ['-m', 'pip', 'install', '--disable-pip-version-check', BOOTSTRAP_PIP], pinnedEnv())
    if (pip.error || pip.status !== 0) {
      console.error(`  [env] pip install failed: ${pip.error?.message || pip.stderr}`)
      return 1
    }
  }
  console.log(`  [env] done. Python: ${VENV_PY}`)
  console.log('  [env] next: pnpm env:check --tier desktop   /   pnpm dev:desktop\n')
  return 0
}

// --------------------------------------------------------------- setup
/** Install dependencies through whichever pnpm entry point is available. */
function installDeps() {
  const args = ['install', '--frozen-lockfile']
  const env = pinnedEnv()
  const attempts = []
  const npmExec = process.env.npm_execpath
  // `corepack pnpm run ...` may leave pnpm off PATH; npm_execpath is reliable then.
  if (npmExec && fs.existsSync(npmExec)) attempts.push([process.execPath, [npmExec, ...args]])
  attempts.push(['pnpm', args], ['corepack', ['pnpm', ...args]])
  for (const [cmd, cmdArgs] of attempts) {
    const r = spawnSync(cmd, cmdArgs, { cwd: ROOT, stdio: 'inherit', shell: IS_WIN, env })
    if (!r.error && r.status === 0) return true
  }
  return false
}

/**
 * One-command onboarding. Default is Tier 1 (Node only, no Python required);
 * --desktop additionally creates the pinned Python bootstrap venv.
 */
function setup() {
  const desktop = flags.has('--desktop')
  const tier = desktop ? 'desktop' : 'frontend'
  console.log(`\n  SoundFlow dev:setup  |  ${process.platform}  |  tier=${tier}\n`)
  console.log('  1) pnpm install --frozen-lockfile')
  if (desktop) console.log(`  2) create .venv/bootstrap (Python ${PY_VERSION}) + ${BOOTSTRAP_PIP}`)
  console.log(`  ${desktop ? '3' : '2'}) env check (${tier})\n`)
  if (flags.has('--dry')) { console.log('  [setup] DRY RUN - nothing executed\n'); return 0 }
  if (!installDeps()) {
    console.error(`  [setup] Could not run pnpm install. Install Node ${TOOLCHAIN.node} + \`corepack enable\`, then retry.\n`)
    return 1
  }
  if (desktop) {
    const rc = bootstrap()
    if (rc !== 0) return rc
  }
  const code = check(tier)
  if (code === 0) {
    console.log(desktop ? '  Ready: pnpm dev:desktop\n' : '  Ready: pnpm dev  (browser http://localhost:1420)\n')
  }
  return code
}

// -------------------------------------------------------------------- env
function printEnv() {
  const env = pinnedEnv()
  const keys = ['PYMSS_STUDIO_PYTHON', 'PYMSS_STUDIO_RUNTIME_ENVS_OVERRIDE', 'PYMSS_PLUGINS_DIR',
    'PIP_CACHE_DIR', 'CARGO_HOME', 'RUSTUP_HOME', 'npm_config_cache', 'PNPM_HOME', 'HF_HOME', 'TORCH_HOME',
    'XDG_CACHE_HOME', 'MODELSCOPE_CACHE', 'HF_ENDPOINT', 'TMP', 'TEMP', 'TMPDIR']
  for (const k of keys) {
    if (!env[k]) continue
    if (IS_WIN) console.log(`$env:${k} = '${env[k]}'`)
    else console.log(`export ${k}="${env[k]}"`)
  }
  return 0
}

// ------------------------------------------------------------------ paths
function printPaths() {
  const env = pinnedEnv({ createDirs: false })
  const { lines, strays } = formatReport(env, ROOT)
  console.log(`\n  SoundFlow path contract  |  ${ROOT}\n`)
  for (const line of lines) console.log(line)
  console.log(`\n  RESULT: ${strays.length ? `FAIL - ${strays.length} path(s) outside the checkout` : 'OK - all paths inside the checkout'}\n`)
  return strays.length ? 1 : 0
}

// ------------------------------------------------------------ python / run
function execWithEnv() {
  if (!rest.length) {
    console.error('  [env] usage: node scripts/dev-env.mjs ' + (command === 'python' ? 'python -- <args>' : 'run [--cn] -- <command> [args]'))
    return 1
  }
  const env = pinnedEnv()
  let cmd = rest[0]
  let args = rest.slice(1)
  if (command === 'python') {
    const py = resolvePython()
    if (!py) {
      console.error('  [env] No Python found. Run `pnpm env:bootstrap` first.')
      return 1
    }
    args = [cmd, ...args]
    cmd = py.command
  }
  const r = spawnSync(cmd, args, { cwd: ROOT, stdio: 'inherit', shell: IS_WIN, env })
  if (r.error) { console.error(`  [env] ${r.error.message}`); return 1 }
  return r.status ?? 1
}

// --------------------------------------------------------------- test:all
/**
 * Run all three test layers under ONE pinned environment. Previously only the
 * Python layer went through this helper; `node --test` and `cargo test`
 * inherited the shell's TEMP / CARGO_HOME and could write to the system drive.
 * Sharing one env keeps local verification CI-equivalent and honours the rule
 * that every cache/temp WRITE stays inside the checkout.
 */
function testAll() {
  const env = pinnedEnv()
  const layers = [
    ['frontend', 'pnpm', ['test:frontend']],
    ['python', 'pnpm', ['test:python']],
    ['rust', 'pnpm', ['test:rust']],
  ]
  if (flags.has('--dry')) {
    console.log('  [env] test:all DRY RUN - would run:')
    for (const [name, cmd, args] of layers) {
      console.log(`    ${name.padEnd(9)} ${cmd} ${args.join(' ')}`)
    }
    console.log(`    CARGO_HOME = ${env.CARGO_HOME}`)
    console.log(`    TMP        = ${env.TMP}`)
    return 0
  }
  for (const [name, cmd, args] of layers) {
    console.log(`  [env] test:all -> ${name}`)
    const r = spawnSync(cmd, args, { cwd: ROOT, stdio: 'inherit', shell: IS_WIN, env })
    if (r.error) { console.error(`  [env] ${name} failed to start: ${r.error.message}`); return 1 }
    if (r.status !== 0) { console.error(`  [env] ${name} failed with code ${r.status}`); return r.status ?? 1 }
  }
  return 0
}

const handlers = { setup, check, bootstrap, env: printEnv, paths: printPaths, python: execWithEnv, run: execWithEnv, 'test:all': testAll }
if (!handlers[command]) {
  console.error(`  [env] unknown command: ${command}\n  available: ${Object.keys(handlers).join(', ')}`)
  process.exit(2)
}
process.exit(handlers[command]())
