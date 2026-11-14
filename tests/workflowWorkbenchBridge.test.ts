import assert from 'node:assert/strict'
import test from 'node:test'
import { reactive, watch, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkbenchStore } from '../src/stores/workbench.ts'
import { createWorkflowBindings } from '../src/workflows/workbench.ts'
import { createWorkbenchBridge, WORKBENCH_STATE } from '../src/workflows/workbenchBridge.ts'

function bus() {
  const handlers = new Map<string, Set<(payload: any) => void>>()
  const messages: any[] = []
  let responseGate: Promise<void> | undefined
  return {
    messages,
    holdResponses() {
      let release!: () => void
      responseGate = new Promise<void>(resolve => { release = resolve })
      return () => { responseGate = undefined; release() }
    },
    transport(label: string) {
      return {
        async listen(event: string, handler: (payload: any) => void) {
          const key = `${label}:${event}`
          const callbacks = handlers.get(key) || new Set()
          handlers.set(key, callbacks)
          callbacks.add(handler)
          return () => { callbacks.delete(handler) }
        },
        async emitTo(target: string, event: string, payload: unknown) {
          const captured = JSON.parse(JSON.stringify(payload))
          messages.push({ label, target, event, payload: captured })
          if (label === 'main' && event === WORKBENCH_STATE && responseGate) await responseGate
          for (const callback of handlers.get(`${target}:${event}`) || []) void callback(captured)
        },
      }
    },
  }
}

async function environment(t: any, preserveInputs = false) {
  setActivePinia(createPinia())
  const mirror = useWorkbenchStore()
  const channel = bus()
  const state = reactive({ jobs: [] as any[], session: { bindings: createWorkflowBindings(), selectedJobId: '', selectedTaskId: '', viewMode: 'edit' as 'edit' | 'execution' } })
  state.session.bindings.inputs.input = { files: ['D:/song.wav'] }
  const performed: any[] = []
  const host = await createWorkbenchBridge({ label: 'main', transport: channel.transport('main'), host: {
    snapshot: (key, includeSession) => JSON.parse(JSON.stringify({ workflowKey: key, jobs: state.jobs.filter(job => job.workflowId === key), ...(includeSession ? { session: state.session } : {}) })),
    remember: (_key, value) => { state.session = value },
    watchTasks: notify => watch(() => state.jobs, notify, { deep: true }),
    perform: async action => {
      performed.push(action)
      if (action.action === 'run') {
        if (!action.bindings?.inputs.input?.files.length) throw new Error('workbench.missingInput')
        state.jobs.push({ id: 'job-1', workflowId: action.workflowKey, primary: { id: 'task-1' }, tasks: [{ id: 'task-1', status: 'queued', nodeStates: {}, outputs: [] }] })
        return { jobId: 'job-1', total: 1 }
      }
      if (action.action === 'cancel') state.jobs[0].tasks[0].status = 'cancelled'
      if (action.action === 'remove-run') state.jobs = state.jobs.filter(job => job.id !== action.jobId)
      return { jobId: action.jobId || 'retry-1', taskId: action.taskId }
    },
  } })
  const editor = await createWorkbenchBridge({ label: 'workflow-simple-editor-a', transport: channel.transport('workflow-simple-editor-a'), applyState: state => mirror.applyRemoteState(state, preserveInputs),
    bindingFingerprint: key => JSON.stringify(mirror.session(key).bindings),
  })
  t.after(async () => { await editor.dispose(); await host.dispose() })
  return { host, editor, mirror, channel, state, performed }
}

test('an editor receives session bindings and queues only through the main window', async t => {
  const env = await environment(t)
  await env.editor.attach('a')
  assert.deepEqual(env.mirror.session('a').bindings.inputs.input.files, ['D:/song.wav'])
  const result = await env.editor.request({ action: 'run', workflowKey: 'a', bindings: createWorkflowBindings() }).catch(error => error)
  assert.equal(result.message, 'workbench.missingInput')
  assert.equal(env.state.jobs.length, 0)
  const queued = await env.editor.request({ action: 'run', workflowKey: 'a', bindings: env.mirror.session('a').bindings })
  assert.equal(queued.jobId, 'job-1')
  assert.equal(env.mirror.remoteJobs.a[0].tasks[0].status, 'queued')
  assert.equal(env.performed.filter(action => action.action === 'run').length, 2)
})

test('node progress and output folders update the mirror while preserving the next-run draft', async t => {
  const env = await environment(t)
  await env.editor.attach('a')
  await env.editor.request({ action: 'run', workflowKey: 'a', bindings: env.mirror.session('a').bindings })
  env.mirror.session('a').bindings.inputs.input.files = ['D:/next.wav']
  env.state.jobs[0].tasks[0] = { id: 'task-1', status: 'done', nodeStates: { '1': { status: 'done', progress: 100 } }, nodeExecutionOrder: ['10', '1'], outputs: [{ path: 'D:/out/song.wav' }], outputDirectories: ['D:/out'] }
  await nextTick()
  await new Promise(resolve => setTimeout(resolve, 130))
  const mirrored = env.mirror.remoteJobs.a[0].tasks[0]
  assert.equal(mirrored.status, 'done')
  assert.equal(mirrored.nodeStates['1'].progress, 100)
  assert.deepEqual(mirrored.nodeExecutionOrder, ['10', '1'])
  assert.deepEqual(mirrored.outputDirectories, ['D:/out'])
  assert.deepEqual(env.mirror.session('a').bindings.inputs.input.files, ['D:/next.wav'])
  await env.channel.transport('main').emitTo('workflow-simple-editor-a', WORKBENCH_STATE, { state: { workflowKey: 'other', jobs: [] } })
  assert.equal(env.mirror.remoteJobs.other, undefined)
})

test('cancel and retry return acknowledgements, and reopening does not cancel the owner task', async t => {
  const env = await environment(t)
  await env.editor.attach('a')
  await env.editor.request({ action: 'run', workflowKey: 'a', bindings: env.mirror.session('a').bindings })
  await env.editor.request({ action: 'cancel', workflowKey: 'a', jobId: 'job-1' })
  assert.equal(env.mirror.remoteJobs.a[0].tasks[0].status, 'cancelled')
  const retry = await env.editor.request({ action: 'retry', workflowKey: 'a', taskId: 'task-1' })
  assert.equal(retry.jobId, 'retry-1')
  env.state.jobs[0].tasks[0].status = 'separating'
  await env.editor.remember('a', env.mirror.session('a'))
  await env.editor.dispose()
  const reopened = await createWorkbenchBridge({ label: 'workflow-simple-editor-a', transport: env.channel.transport('workflow-simple-editor-a'), applyState: env.mirror.applyRemoteState })
  t.after(() => reopened.dispose())
  await reopened.attach('a')
  assert.equal(env.mirror.remoteJobs.a[0].tasks[0].status, 'separating')
  assert.equal(env.performed.filter(action => action.action === 'cancel').length, 1)
})

test('a missing main window rejects the action without reporting submission', async t => {
  const channel = bus()
  const editor = await createWorkbenchBridge({ label: 'workflow-node-editor-a', transport: channel.transport('workflow-node-editor-a'), timeoutMs: 10 })
  t.after(() => editor.dispose())
  await assert.rejects(editor.attach('a'), /hostUnavailable/)
})

test('a delayed session reply preserves files chosen while the editor is connecting', async t => {
  const env = await environment(t)
  const release = env.channel.holdResponses()
  const attaching = env.editor.attach('a')
  env.mirror.session('a').bindings.inputs.input = { files: ['D:/new-selection.wav'] }
  release()
  await attaching
  assert.deepEqual(env.mirror.session('a').bindings.inputs.input.files, ['D:/new-selection.wav'])
  await env.channel.transport('main').emitTo('workflow-simple-editor-a', WORKBENCH_STATE, {
    requestId: 'closed-editor:old-request', state: { workflowKey: 'a', jobs: [], session: { ...env.state.session, bindings: createWorkflowBindings() } },
  })
  assert.deepEqual(env.mirror.session('a').bindings.inputs.input.files, ['D:/new-selection.wav'])
})

test('removing a run updates an open workflow editor without altering its next-run inputs', async t => {
  const env = await environment(t)
  await env.editor.attach('a')
  await env.editor.request({ action: 'run', workflowKey: 'a', bindings: env.mirror.session('a').bindings })
  env.state.jobs[0].tasks[0].status = 'failed'
  env.mirror.session('a').bindings.inputs.input.files = ['D:/next.wav']
  await env.editor.request({ action: 'remove-run', workflowKey: 'a', jobId: 'job-1' })
  assert.equal(env.mirror.remoteJobs.a.length, 0)
  assert.deepEqual(env.mirror.session('a').bindings.inputs.input.files, ['D:/next.wav'])
  assert.equal(env.performed.at(-1).action, 'remove-run')
})

test('reopening a simple editor leaves inputs empty without changing an active task snapshot', async t => {
  const env = await environment(t, true)
  await env.editor.attach('a')
  assert.deepEqual(env.mirror.session('a').bindings.inputs, {})
  env.mirror.session('a').bindings.inputs.input = { files: ['D:/current.wav'] }
  env.mirror.session('a').bindings.primaryInput = 'input'
  await env.editor.request({ action: 'run', workflowKey: 'a', bindings: env.mirror.session('a').bindings })
  env.state.jobs[0].tasks[0].status = 'separating'
  env.state.jobs[0].tasks[0].runConfig = { workbench: { bindings: JSON.parse(JSON.stringify(env.mirror.session('a').bindings)) } }
  await env.editor.remember('a', env.mirror.session('a'))
  await env.editor.dispose()
  env.mirror.session('a').bindings.inputs = {}
  env.mirror.session('a').bindings.primaryInput = ''
  const reopened = await createWorkbenchBridge({ label: 'workflow-simple-editor-a', transport: env.channel.transport('workflow-simple-editor-a'), applyState: state => env.mirror.applyRemoteState(state, true) })
  t.after(() => reopened.dispose())
  await reopened.attach('a')
  assert.deepEqual(env.mirror.session('a').bindings.inputs, {})
  assert.equal(env.mirror.remoteJobs.a[0].tasks[0].status, 'separating')
  assert.deepEqual(env.mirror.remoteJobs.a[0].tasks[0].runConfig.workbench.bindings.inputs.input.files, ['D:/current.wav'])
  assert.equal(env.performed.filter(action => action.action === 'cancel').length, 0)
})
