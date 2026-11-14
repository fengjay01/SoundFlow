import { getCurrentWindow } from '@tauri-apps/api/window'

export function isMainTaskOwner(): boolean {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return true
  try { return getCurrentWindow().label === 'main' } catch { return false }
}
