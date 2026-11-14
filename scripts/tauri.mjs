import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

// Tauri pipes Cargo/app stderr. Capture the launching terminal's color capability
// before that pipe is created, while respecting explicit color preferences.
const command = process.argv.slice(2).find(arg => !arg.startsWith('-'))
if (command === 'dev' && process.stderr.isTTY
  && process.env.NO_COLOR === undefined && process.env.FORCE_COLOR === undefined
  && process.env.PYMSS_STUDIO_TERMINAL_COLOR === undefined) {
  // A private hint avoids forcing ANSI in Python/pip subprocesses and their log files.
  process.env.PYMSS_STUDIO_TERMINAL_COLOR = '1'
}

// A bare `python` does not exist on most macOS / Linux systems (they ship
// `python3` only). Honour the upstream interpreter override first, then probe
// platform-specific names. Keeps `pnpm tauri dev` working on all three OSes.
function resolvePython() {
  const candidates = [
    ...(process.env.PYMSS_STUDIO_PYTHON ? [process.env.PYMSS_STUDIO_PYTHON] : []),
    ...(process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python']),
  ]
  for (const candidate of candidates) {
    const probe = spawnSync(candidate, ['--version'], { stdio: 'ignore' })
    if (!probe.error && probe.status === 0) return candidate
  }
  return candidates[0]
}

if ((command === 'dev' || command === 'build') && !process.argv.some(arg => ['--help', '-h', '--version', '-V'].includes(arg))) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const release = command === 'build' && !process.argv.includes('--debug')
  const result = spawnSync(resolvePython(), ['-X', 'utf8', path.join(root, 'scripts/prepare-preinstalled-plugins.py'), ...(release ? ['--release'] : [])], { cwd: root, stdio: 'inherit' })
  if (result.error) { console.error(result.error.message); process.exit(1) }
  if (result.status !== 0) process.exit(result.status || 1)
}

// Use the official entry point for argument handling, errors, and process lifetime.
await import('@tauri-apps/cli/tauri.js')
