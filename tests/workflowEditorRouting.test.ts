import assert from 'node:assert/strict'
import test from 'node:test'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createWorkflowEditorRouteGuard } from '../src/workflows/editorWindow.ts'

function environment(main = true) {
  const opened: any[] = [], errors: unknown[] = []
  let failure: Error | undefined
  const router = createRouter({ history: createMemoryHistory(), routes: ['/workflows', '/workflow-simple-editor', '/workflow-node-editor'].map(path => ({ path, component: {} })) })
  router.beforeEach(createWorkflowEditorRouteGuard({
    isDesktopMainWindow: () => main,
    openWindow: async (command, args) => { if (failure) throw failure; opened.push({ command, ...args }) },
    onError: error => errors.push(error),
  }))
  return { router, opened, errors, fail: (error: Error) => { failure = error } }
}

test('legacy main-window editor URLs open native windows and retain the workflow overview', async () => {
  for (const kind of ['simple', 'node']) {
    const env = environment()
    await env.router.push(`/workflow-${kind}-editor?workflowId=flow%20one`)
    assert.equal(env.router.currentRoute.value.path, '/workflows')
    assert.deepEqual(env.opened, [{ command: `open_workflow_${kind}_editor_window`, payload: { workflowId: 'flow one', newWorkflow: false } }])
  }
})

test('new main-window editors open blank native windows without rendering a frameless editor page', async () => {
  const env = environment()
  await env.router.push('/workflow-simple-editor?new=1&workflowId=old')
  assert.deepEqual(env.opened[0].payload, { workflowId: '', newWorkflow: true })
  assert.equal(env.router.currentRoute.value.path, '/workflows')
})

test('auxiliary editor windows retain their own routes instead of opening another window', async () => {
  const env = environment(false)
  await env.router.push('/workflow-node-editor?workflowId=flow')
  assert.equal(env.router.currentRoute.value.path, '/workflow-node-editor')
  assert.deepEqual(env.opened, [])
})

test('failed native window creation leaves the main window on a usable overview', async () => {
  const env = environment()
  const failure = new Error('Window creation failed')
  env.fail(failure)
  await env.router.push('/workflow-simple-editor?workflowId=flow')
  assert.equal(env.router.currentRoute.value.path, '/workflows')
  assert.deepEqual(env.errors, [failure])
})
