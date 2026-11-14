import type { NavigationGuard, RouteLocationNormalized } from 'vue-router'

export function createWorkflowEditorRouteGuard(options: {
  isDesktopMainWindow: () => boolean
  openWindow: (command: string, args: { payload: { workflowId: string; newWorkflow: boolean } }) => Promise<unknown>
  onError: (error: unknown) => void
}): NavigationGuard {
  return async (to: RouteLocationNormalized) => {
    if (!options.isDesktopMainWindow()) return
    const kind = to.path === '/workflow-simple-editor' ? 'simple' : to.path === '/workflow-node-editor' ? 'node' : ''
    if (!kind) return
    const workflowId = typeof to.query.workflowId === 'string' ? to.query.workflowId.trim() : ''
    const newWorkflow = to.query.new === '1' || !workflowId
    try {
      await options.openWindow(`open_workflow_${kind}_editor_window`, { payload: { workflowId: newWorkflow ? '' : workflowId, newWorkflow } })
    } catch (error) {
      options.onError(error)
    }
    return { path: '/workflows', replace: true }
  }
}
