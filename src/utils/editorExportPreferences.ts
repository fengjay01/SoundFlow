import { emitTo, listen, type UnlistenFn } from '@tauri-apps/api/event'
import { getCurrentWindow } from '@tauri-apps/api/window'

export type EditorExportPreference = { format: 'wav' | 'flac'; bitDepth: string }
type Request = EditorExportPreference & { requestId: string; windowLabel: string }
type Response = { requestId: string; error?: string }
const REQUEST_EVENT = 'pymss://editor-export-preference-request'
const RESPONSE_EVENT = 'pymss://editor-export-preference-result'

export function listenForEditorExportPreferences(save: (preference: EditorExportPreference) => Promise<void>): Promise<UnlistenFn> {
  return listen<Request>(REQUEST_EVENT, async ({ payload }) => {
    if (!payload || typeof payload.requestId !== 'string' || typeof payload.windowLabel !== 'string'
      || !payload.windowLabel.startsWith('editor-')) return
    const response: Response = { requestId: payload.requestId }
    try {
      await save({ format: payload.format, bitDepth: payload.bitDepth })
    } catch (error) {
      response.error = error instanceof Error ? error.message : String(error)
    }
    // The exporting window may have closed while persistence was pending.
    await emitTo(payload.windowLabel, RESPONSE_EVENT, response).catch(() => undefined)
  })
}

export async function requestEditorExportPreference(preference: EditorExportPreference): Promise<void> {
  const windowLabel = getCurrentWindow().label
  const requestId = crypto.randomUUID()
  let finish: ((error?: string) => void) | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  const unlisten = await listen<Response>(RESPONSE_EVENT, ({ payload }) => {
    if (payload?.requestId === requestId) finish?.(payload.error)
  })
  try {
    await new Promise<void>((resolve, reject) => {
      finish = error => error ? reject(new Error(error)) : resolve()
      timer = setTimeout(() => reject(new Error('Export preference save timed out')), 10000)
      void emitTo('main', REQUEST_EVENT, { ...preference, requestId, windowLabel }).catch(reject)
    })
  } finally {
    if (timer) clearTimeout(timer)
    await unlisten()
  }
}
