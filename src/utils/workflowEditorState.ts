export function isWorkflowLockedByNodeEditor(openWorkflowId: string, workflowId: string) {
  const openId = openWorkflowId.trim()
  const currentId = workflowId.trim()
  return Boolean(openId && currentId && openId === currentId)
}

export function isWorkflowEditorSurfaceLocked(
  openWorkflowId: string,
  workflowId: string,
  editingNewWorkflow: boolean,
) {
  const openId = openWorkflowId.trim()
  if (editingNewWorkflow) return openId === '__new__'
  return isWorkflowLockedByNodeEditor(openId, workflowId)
}

export type WorkflowEditorRecord = { label: string; workflowId: string; kind: 'simple' | 'advanced' }
export type WorkflowEditorSnapshot = { revision: number; editors: WorkflowEditorRecord[] }

export function isWorkflowEditorSnapshot(value: unknown): value is WorkflowEditorSnapshot {
  if (!value || typeof value !== 'object') return false
  const snapshot = value as WorkflowEditorSnapshot
  return Number.isSafeInteger(snapshot.revision) && snapshot.revision >= 0 && Array.isArray(snapshot.editors)
    && snapshot.editors.every(editor => editor && typeof editor.label === 'string' && typeof editor.workflowId === 'string'
      && (editor.kind === 'simple' || editor.kind === 'advanced'))
}

export function workflowEditorFor(editors: readonly WorkflowEditorRecord[], workflowId: string) {
  return workflowId ? editors.find(editor => editor.workflowId === workflowId) : undefined
}

export class WorkflowEditorInUseError extends Error {
  readonly code = 'WORKFLOW_EDITOR_IN_USE'
  constructor() {
    super('Workflow is already being edited')
    this.name = 'WorkflowEditorInUseError'
  }
}
