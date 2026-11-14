#!/usr/bin/env node
/**
 * Executable form of the "everything stays inside the checkout" contract.
 *
 * dev-env.mjs imports formatReport() to check the environment it is about to
 * hand to a child process. Running this file directly checks the *current*
 * process environment instead, which is useful from CI or a shell that was
 * already launched through `dev-env.mjs run -- ...`.
 *
 * Path keys that merely point at borrowed system toolchains (RUSTUP_HOME,
 * PYMSS_STUDIO_PYTHON) are intentionally not checked: they are read-only
 * tool selection, not project writes.
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const DEFAULT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

const ENV_KEYS = [
  'PIP_CACHE_DIR', 'CARGO_HOME', 'npm_config_cache', 'PNPM_HOME',
  'HF_HOME', 'TORCH_HOME', 'XDG_CACHE_HOME', 'MODELSCOPE_CACHE',
  'TMP', 'TEMP', 'TMPDIR',
  'PYMSS_STUDIO_RUNTIME_ENVS_OVERRIDE', 'PYMSS_PLUGINS_DIR',
  'PYMSS_STUDIO_DATA_ROOT', 'PYMSS_MODEL_DIR',
  'PYMSS_USER_MODELS', 'PYMSS_STUDIO_DEBUG_DIR',
  'PYMSS_STUDIO_RUNTIME_ENVS_DIR', 'PYMSS_STUDIO_ACTIVE_RUNTIME_FILE',
  'PYMSS_STUDIO_BUNDLED_RUNTIME_ENVS_DIR',
]

/** Mirror of python/_project_paths.py::default_paths() for display purposes. */
function projectDefaults(root) {
  return {
    'default:data_root': path.join(root, 'data'),
    'default:models_dir': path.join(root, 'data', 'models'),
    'default:settings_dir': path.join(root, 'data', 'settings'),
    'default:debug_dir': path.join(root, 'data', 'debug'),
    'default:plugins_dir': path.join(root, 'data', 'plugins'),
    'default:user_models_file': path.join(root, 'data', 'settings', 'user_models.json'),
    'default:runtime_envs_dir': path.join(root, '.venv', 'python', 'runtime-envs'),
    'default:scratch_dir': path.join(root, 'data', 'temp'),
  }
}

export function isInside(root, value) {
  if (!value) return false
  const rel = path.relative(path.resolve(root), path.resolve(String(value)))
  return rel === '' || (rel !== '..' && !rel.startsWith('..' + path.sep) && !path.isAbsolute(rel))
}

export function collectPaths(env = process.env, root = DEFAULT_ROOT) {
  const rows = []
  for (const key of new Set(ENV_KEYS)) {
    if (env[key]) rows.push({ name: key, value: String(env[key]), source: 'env', inside: isInside(root, env[key]) })
  }
  for (const [name, value] of Object.entries(projectDefaults(root))) {
    rows.push({ name, value, source: 'default', inside: isInside(root, value) })
  }
  return rows
}

export function formatReport(env = process.env, root = DEFAULT_ROOT) {
  const rows = collectPaths(env, root)
  const lines = rows.map(row =>
    `  ${row.inside ? 'OK ' : 'OUT'}  ${row.name.padEnd(38)} ${row.value}`
  )
  return { rows, lines, strays: rows.filter(row => !row.inside) }
}

const invokedDirectly = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (invokedDirectly) {
  const { lines, strays } = formatReport()
  console.log(`\n  SoundFlow path contract  |  ${DEFAULT_ROOT}`)
  console.log('  (checks the current process env; use `pnpm env:paths` for the pinned launcher env)\n')
  for (const line of lines) console.log(line)
  console.log(`\n  RESULT: ${strays.length ? `FAIL - ${strays.length} path(s) outside the checkout` : 'OK - all paths inside the checkout'}\n`)
  process.exit(strays.length ? 1 : 0)
}
