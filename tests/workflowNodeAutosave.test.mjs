import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'

const descriptor = parse(readFileSync(new URL('../src/views/WorkflowNodeEditorView.vue', import.meta.url), 'utf8')).descriptor
const source = ts.createSourceFile('editor.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
const names = new Set(['snapshot', 'dirty', 'canSave', 'cancelAutosave', 'scheduleAutosave', 'persistDefinition',
  'save', 'syncCurrentDefinition', 'flushChanges', 'closeEditor', 'destroyWindow', 'runWorkflow',
  'autosaveTimer', 'saveQueue', 'pendingSaves', 'saveGeneration', 'disposed', 'runPromise', 'confirmedClose'])
const statements = source.statements.filter(statement => ts.isFunctionDeclaration(statement)
  ? names.has(statement.name?.text)
  : ts.isVariableStatement(statement) && statement.declarationList.declarations.some(item => names.has(item.name.getText(source))))
assert.equal(statements.length, names.size)
const unmount = source.statements.find(statement => ts.isExpressionStatement(statement)
  && ts.isCallExpression(statement.expression) && statement.expression.expression.getText(source) === 'onBeforeUnmount')
const code = ts.transpileModule([...statements, unmount].map(statement => statement.getText(source)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText
const plain = value => JSON.parse(JSON.stringify(value))
const ref = value => ({ value })
class ConflictError extends Error {}
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

function environment({ fresh = false } = {}) {
  const calls = [], navigation = [], timers = new Map()
  let version = 1, nextTimer = 0, handler, unmount
  const graph = { nodes: [{ id: 1, type: 'pymss_load_audio', widgets_values: ['Original'] }], links: [] }
  const state = {
    Error, ref, computed: read => ({ get value() { return read() } }), cloneWorkflow: plain,
    name: ref('Workflow'), description: ref(''), defaultDevice: ref('auto'), defaultFormat: ref('wav'),
    definition: ref(graph), editingId: ref(fresh ? '' : 'flow'), loadedUpdatedAt: ref(fresh ? undefined : 1),
    loaded: ref(true), canvasInitialized: ref(true), initialSnapshot: ref(''), saving: ref(false), saveError: ref(''),
    closing: ref(false), editingName: ref(false), submitting: ref(false), showClosePrompt: ref(false),
    pendingDefinition: ref(null), showRevisionConflict: ref(false), formatError: ref(''), canvasError: ref(''),
    editorRef: ref({ snapshotDefinition: () => plain(state.definition.value) }),
    workflows: ref(fresh ? [] : [{ id: 'flow', updatedAt: 1, definition: plain(graph) }]),
    workflow: { async saveWorkflow(input) { calls.push(plain(input)); return handler ? handler(input) : accept(input) } },
    workbench: { adoptWorkflowKey() {}, async run() {} }, currentWindow: null, invoke: async () => {},
    router: { async push(path) { navigation.push(path) } },
    message: { success() {}, error() {} }, t: key => key,
    WorkflowRevisionConflictError: ConflictError, WorkflowEditorInUseError: class extends Error {},
    storeGraphDefaults: plain, finishNameEdit: () => { state.editingName.value = false },
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, delay }); return id },
    clearTimeout(id) { timers.delete(id) }, onBeforeUnmount(callback) { unmount = callback },
    unlistenResize: undefined, unlistenCloseRequested: undefined,
  }
  function accept(input) {
    const entry = { ...plain(input), id: input.id || 'created', updatedAt: ++version }
    state.workflows.value = [entry]
    return entry
  }
  const editor = vm.runInNewContext(`${code}\n({ snapshot, dirty, save, scheduleAutosave, closeEditor, runWorkflow, queue:()=>saveQueue })`, state)
  state.initialSnapshot.value = editor.snapshot()
  return {
    state, editor, calls, timers, navigation, accept, dispose: () => unmount(),
    handler(value) { handler = value },
    change(value) { state.definition.value.nodes[0].widgets_values[0] = value },
    async fire() {
      assert.equal(timers.size, 1)
      const [id, timer] = timers.entries().next().value
      assert.equal(timer.delay, 600)
      timers.delete(id)
      timer.callback()
      await editor.queue()
      await Promise.resolve()
    },
  }
}

test('advanced autosave debounces edits and retains the current canvas snapshot', async () => {
  const env = environment()
  env.change('First'); env.editor.scheduleAutosave()
  env.change('Final'); env.editor.scheduleAutosave()
  await env.fire()
  assert.equal(env.calls.length, 1)
  assert.equal(env.calls[0].definition.nodes[0].widgets_values[0], 'Final')
  assert.equal(env.editor.dirty.value, false)
})

test('a new graph is saved automatically and queued saves reuse its ID and revision', async () => {
  const env = environment({ fresh: true }), waiting = deferred()
  env.handler(input => env.calls.length === 1 ? waiting.promise.then(() => env.accept(input)) : env.accept(input))
  env.editor.scheduleAutosave()
  const first = env.fire()
  await Promise.resolve()
  env.change('Next')
  const second = env.editor.save()
  waiting.resolve()
  await Promise.all([first, second])
  assert.equal(env.calls.length, 2)
  assert.equal(env.calls[0].id, undefined)
  assert.equal(env.calls[1].id, 'created')
  assert.equal(env.calls[1].expectedUpdatedAt, 2)
  assert.equal(env.editor.dirty.value, false)
})

test('editing during a delayed write retains the edit and schedules another save', async () => {
  const env = environment(), waiting = deferred()
  env.handler(input => waiting.promise.then(() => env.accept(input)))
  env.change('Submitted')
  const writing = env.editor.save()
  await Promise.resolve()
  env.change('Future')
  waiting.resolve()
  await writing
  assert.equal(env.editor.dirty.value, true)
  assert.equal(env.state.definition.value.nodes[0].widgets_values[0], 'Future')
  await env.fire()
  assert.equal(env.calls[1].definition.nodes[0].widgets_values[0], 'Future')
  assert.equal(env.calls[1].expectedUpdatedAt, 2)
})

test('closing flushes pending edits and waits until their write completes', async () => {
  const env = environment(), waiting = deferred()
  env.handler(input => waiting.promise.then(() => env.accept(input)))
  env.change('Final')
  env.editor.scheduleAutosave()
  const closing = env.editor.closeEditor()
  await Promise.resolve(); await Promise.resolve()
  assert.deepEqual(env.navigation, [])
  waiting.resolve()
  await closing
  assert.deepEqual(env.navigation, ['/workflows'])
  assert.equal(env.calls[0].definition.nodes[0].widgets_values[0], 'Final')
  assert.equal(env.timers.size, 0)
})

test('failed autosaves preserve drafts and pause retries; closing keeps the editor open', async () => {
  const env = environment()
  env.handler(async () => { throw new Error('Disk write failed') })
  env.change('Unsaved')
  await env.editor.closeEditor()
  assert.deepEqual(env.navigation, [])
  assert.equal(env.state.showClosePrompt.value, true)
  assert.equal(env.state.saveError.value, 'Disk write failed')
  assert.equal(env.editor.dirty.value, true)
  env.editor.scheduleAutosave()
  assert.equal(env.timers.size, 0)
})

test('revision conflicts stop queued writes and preserve the newer local draft', async () => {
  const env = environment(), waiting = deferred()
  env.handler(async () => { await waiting.promise; throw new ConflictError('Newer revision') })
  env.change('Submitted'); const first = env.editor.save()
  await Promise.resolve()
  env.change('Newer local'); const second = env.editor.save()
  waiting.resolve()
  await Promise.all([first, second])
  assert.equal(env.calls.length, 1)
  assert.equal(env.state.showRevisionConflict.value, true)
  assert.equal(env.state.definition.value.nodes[0].widgets_values[0], 'Newer local')
  assert.equal(env.timers.size, 0)
})

test('unmount cancels scheduled writes and ignores a late acknowledgement', async () => {
  const env = environment(), waiting = deferred()
  env.handler(input => waiting.promise.then(() => env.accept(input)))
  env.change('Pending'); const writing = env.editor.save()
  await Promise.resolve()
  env.dispose(); waiting.resolve()
  assert.equal(await writing, null)
  assert.equal(env.state.loadedUpdatedAt.value, 1)
  assert.equal(env.timers.size, 0)
})

test('name editing pauses autosave, incomplete drafts are saved, load failures are blocked', async () => {
  const env = environment()
  env.state.editingName.value = true
  env.state.name.value = 'New name'
  env.editor.scheduleAutosave()
  assert.equal(env.timers.size, 0)
  env.state.editingName.value = false
  env.state.definition.value = { nodes: [], links: [] }
  env.editor.scheduleAutosave()
  await env.fire()
  assert.deepEqual(env.calls[0].definition.nodes, [])
  env.state.canvasError.value = 'Unable to load graph'
  env.state.description.value = 'Preserve original'
  assert.equal(await env.editor.save(), null)
  assert.equal(env.calls.length, 1)
})

test('running during an autosave submits the click snapshot and later saves subsequent edits', async () => {
  const env = environment(), writing = deferred(), submitting = deferred(), submitted = deferred()
  let runEntry
  env.handler(input => env.calls.length === 1 ? writing.promise.then(() => env.accept(input)) : env.accept(input))
  env.state.workbench.run = async () => {
    env.state.submitting.value = true
    runEntry = await env.editor.save()
    submitted.resolve()
    await submitting.promise
    env.state.submitting.value = false
  }
  env.change('Before'); const oldWrite = env.editor.save()
  await Promise.resolve()
  env.change('At click'); const running = env.editor.runWorkflow()
  env.change('Next run'); env.editor.scheduleAutosave()
  writing.resolve(); await oldWrite; await submitted.promise
  assert.equal(runEntry.definition.nodes[0].widgets_values[0], 'At click')
  assert.equal(env.timers.size, 0)
  submitting.resolve(); await running; await env.fire()
  assert.equal(env.calls.at(-1).definition.nodes[0].widgets_values[0], 'Next run')
})

test('snapshot failures surface as save errors and prevent closing without writing partial data', async () => {
  const env = environment()
  env.change('Unsaved')
  env.state.editorRef.value.snapshotDefinition = () => { throw new Error('Unable to serialize node') }
  assert.equal(await env.editor.save(), null)
  assert.equal(env.state.saveError.value, 'Unable to serialize node')
  await env.editor.closeEditor()
  assert.deepEqual(env.navigation, [])
  assert.equal(env.state.showClosePrompt.value, true)
  assert.equal(env.calls.length, 0)
})
