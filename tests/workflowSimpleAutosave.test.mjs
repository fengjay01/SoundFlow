import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'
import { WorkflowEditorInUseError } from '../src/utils/workflowEditorState.ts'

const descriptor = parse(readFileSync(new URL('../src/views/WorkflowSimpleEditorView.vue', import.meta.url), 'utf8')).descriptor
const source = ts.createSourceFile('WorkflowSimpleEditorView.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
const names = new Set([
  'clone', 'snapshot', 'dirty', 'cancelAutosave', 'scheduleAutosave', 'persist', 'flushChanges',
  'closeEditor', 'destroyWindow', 'runWorkflow', 'autosaveTimer', 'saveQueue', 'pendingSaves',
  'saveGeneration', 'disposed', 'runPromise', 'confirmedClose', 'starterSnapshot', 'saveConflictCopy',
])
const statements = source.statements.filter(statement => (
  ts.isFunctionDeclaration(statement) ? names.has(statement.name?.text)
    : ts.isVariableStatement(statement) && statement.declarationList.declarations.some(item => names.has(item.name.getText(source)))
))
assert.equal(statements.length, names.size)
const unmount = source.statements.find(statement => ts.isExpressionStatement(statement)
  && ts.isCallExpression(statement.expression) && statement.expression.expression.getText(source) === 'onBeforeUnmount')
assert.ok(unmount)
const modelWatcher = source.statements.find(statement => ts.isExpressionStatement(statement)
  && ts.isCallExpression(statement.expression) && statement.expression.expression.getText(source) === 'watch'
  && statement.expression.arguments[0]?.getText(source) === 'downloadedModels')
assert.ok(modelWatcher)
const code = ts.transpileModule([...statements, modelWatcher, unmount].map(statement => statement.getText(source)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText

const plain = value => JSON.parse(JSON.stringify(value))
const ref = value => ({ value })
const computed = read => ({ get value() { return read() } })
function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
class ConflictError extends Error {}

function createState({ fresh = false } = {}) {
  const timers = new Map()
  const calls = []
  const navigation = []
  let timerId = 0
  let version = 1
  let saveHandler
  let dispose
  let modelsChanged
  const state = {
    Error, computed, WorkflowEditorInUseError, t: (_key, data) => data?.name ? data.name + ' copy' : _key,
    name: ref('Workflow'), description: ref('Description'),
    draft: ref({ steps: [{ id: 'step1', model: 'Original' }], ensembles: [], ui: {} }),
    loaded: ref(true), loadError: ref(''), initialSnapshot: ref(''),
    editingId: ref(fresh ? '' : 'flow'), expectedUpdatedAt: ref(fresh ? undefined : version),
    saving: ref(false), saveError: ref(''), nameEditing: ref(false), closing: ref(false),
    showRevisionConflict: ref(false), pendingConflict: ref(null), showClosePrompt: ref(false),
    submitting: ref(false), editorRef: ref({ finishNameEdit() {} }),
    formError: ref(''), saveBlocked: ref(false), currentWindow: null, isStandaloneWindow: ref(false),
    workflow: { async saveWorkflow(input) { calls.push(plain(input)); return saveHandler ? saveHandler(input) : accept(input) } },
    workflows: ref(fresh ? [] : [{ id: 'flow', updatedAt: version }]),
    workbench: { adoptWorkflowKey() {}, async run() {} },
    downloadedModels: ref([]), editorRenderKey: ref(0), hydrateStepModelTypes() {},
    createExampleDraft: () => ({ steps: [{ id: 'step1', model: 'Late model' }], ui: {} }),
    watch(_source, callback) { modelsChanged = callback },
    cleanupSimpleDraft() {}, buildSimpleWorkflowDefinition: value => plain(value),
    WorkflowRevisionConflictError: ConflictError,
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    onBeforeUnmount(callback) { dispose = callback }, unlistenResize: undefined, unlistenCloseRequested: undefined,
    router: { async push(target) { navigation.push(target) } }, invoke: async () => {},
  }
  state.canSave = computed(() => state.loaded.value && !state.saveBlocked.value)
  const result = vm.runInNewContext(`${code}\n({ snapshot, dirty, scheduleAutosave, persist, flushChanges, closeEditor, runWorkflow, saveConflictCopy, queue:()=>saveQueue, startTemplate:()=>{starterSnapshot=snapshot()} })`, state)
  state.initialSnapshot.value = result.snapshot()
  function accept(input) {
    const entry = { ...plain(input), id: input.id || 'created-flow', updatedAt: ++version }
    state.workflows.value = [entry]
    return entry
  }
  return {
    state, result, timers, calls, navigation, accept,
    change(model) { state.draft.value.steps[0].model = model },
    setSaveHandler(handler) { saveHandler = handler },
    modelsChanged() { modelsChanged() },
    dispose() { dispose() },
    async drain() { await result.queue(); await Promise.resolve() },
    async fireTimer() {
      assert.equal(timers.size, 1)
      const [id, timer] = timers.entries().next().value
      assert.equal(timer.delay, 600)
      timers.delete(id)
      timer.callback()
      await result.queue()
      await Promise.resolve()
    },
  }
}

test('rapid edits debounce into one save of the final snapshot', async () => {
  const env = createState()
  env.change('First')
  env.result.scheduleAutosave()
  env.change('Final')
  env.result.scheduleAutosave()
  assert.equal(env.calls.length, 0)
  assert.equal(env.timers.size, 1)
  await env.fireTimer()
  assert.equal(env.calls.length, 1)
  assert.equal(env.calls[0].definition.steps[0].model, 'Final')
  assert.equal(env.result.dirty.value, false)
})

test('a delayed save cannot mark edits made during the write as saved', async () => {
  const env = createState()
  const waiting = deferred()
  env.setSaveHandler(input => waiting.promise.then(() => env.accept(input)))
  env.change('Submitted')
  const saving = env.result.persist()
  await Promise.resolve()
  env.change('Next')
  waiting.resolve()
  await saving
  assert.equal(env.calls[0].definition.steps[0].model, 'Submitted')
  assert.equal(env.result.dirty.value, true)
  assert.equal(env.timers.size, 1)
  await env.fireTimer()
  assert.equal(env.calls[1].definition.steps[0].model, 'Next')
  assert.equal(env.calls[1].expectedUpdatedAt, 2)
  assert.equal(env.result.dirty.value, false)
})

test('queued first saves reuse the created workflow id and fresh revision', async () => {
  const env = createState({ fresh: true })
  const waiting = deferred()
  env.setSaveHandler(input => env.calls.length === 1 ? waiting.promise.then(() => env.accept(input)) : env.accept(input))
  env.change('First')
  const first = env.result.persist()
  await Promise.resolve()
  env.change('Second')
  const second = env.result.persist()
  assert.equal(env.calls.length, 1)
  waiting.resolve()
  await Promise.all([first, second])
  assert.equal(env.calls[0].id, undefined)
  assert.equal(env.calls[1].id, 'created-flow')
  assert.equal(env.calls[1].expectedUpdatedAt, 2)
  assert.equal(env.state.workflows.value.length, 1)
  assert.equal(env.result.dirty.value, false)
})

test('an untouched new workflow receives its first automatic save', async () => {
  const env = createState({ fresh: true })
  env.result.scheduleAutosave()
  await env.fireTimer()
  assert.equal(env.calls.length, 1)
  assert.equal(env.state.editingId.value, 'created-flow')
})

test('models arriving after the first autosave fill the untouched starter and update the same workflow', async () => {
  const env = createState({ fresh: true })
  env.change('')
  env.state.initialSnapshot.value = env.result.snapshot()
  env.result.startTemplate()
  env.result.scheduleAutosave()
  await env.fireTimer()
  assert.equal(env.calls[0].definition.steps[0].model, '')
  env.state.downloadedModels.value = [{ name: 'Late model' }]
  env.modelsChanged()
  assert.equal(env.state.draft.value.steps[0].model, 'Late model')
  assert.equal(env.state.editorRenderKey.value, 1)
  assert.equal(env.result.dirty.value, true)
  env.result.scheduleAutosave()
  await env.fireTimer()
  assert.equal(env.calls[1].id, 'created-flow')
  assert.equal(env.calls[1].definition.steps[0].model, 'Late model')
  assert.equal(env.state.workflows.value.length, 1)
  assert.equal(env.result.dirty.value, false)
})

test('closing immediately flushes changes and waits for their write', async () => {
  const env = createState()
  const waiting = deferred()
  env.setSaveHandler(input => waiting.promise.then(() => env.accept(input)))
  env.change('Latest')
  env.result.scheduleAutosave()
  const closing = env.result.closeEditor()
  await Promise.resolve()
  await Promise.resolve()
  assert.equal(env.navigation.length, 0)
  assert.equal(env.state.closing.value, true)
  waiting.resolve()
  await closing
  assert.deepEqual(env.navigation, ['/workflows'])
  assert.equal(env.timers.size, 0)
  assert.equal(env.result.dirty.value, false)
})

test('failed or invalid saves keep the editor open and retain its dirty state', async () => {
  const env = createState()
  env.change('Latest')
  env.setSaveHandler(async () => { throw new Error('Disk write failed') })
  await env.result.closeEditor()
  assert.deepEqual(env.navigation, [])
  assert.equal(env.state.showClosePrompt.value, true)
  assert.equal(env.state.saveError.value, 'Disk write failed')
  assert.equal(env.result.dirty.value, true)
  env.result.scheduleAutosave()
  assert.equal(env.timers.size, 0)
  const invalid = createState()
  invalid.change('Incomplete')
  invalid.state.formError.value = 'Invalid connection'
  invalid.state.saveBlocked.value = true
  await invalid.result.closeEditor()
  assert.equal(invalid.calls.length, 0)
  assert.equal(invalid.state.showClosePrompt.value, true)
})

test('a revision conflict stops queued saves instead of overwriting a newer workflow', async () => {
  const env = createState()
  const waiting = deferred()
  env.setSaveHandler(async () => { await waiting.promise; throw new ConflictError('Newer revision') })
  env.change('First')
  const first = env.result.persist()
  await Promise.resolve()
  env.change('Second')
  const second = env.result.persist()
  waiting.resolve()
  await Promise.all([first, second])
  assert.equal(env.calls.length, 1)
  assert.equal(env.state.showRevisionConflict.value, true)
  assert.equal(env.state.pendingConflict.value.definition.steps[0].model, 'First')
  assert.equal(env.result.dirty.value, true)
})

test('running while saving uses the click snapshot and postpones future autosaves until submission settles', async () => {
  const env = createState()
  const firstWrite = deferred()
  const submit = deferred()
  const submittedReady = deferred()
  const submitted = []
  env.setSaveHandler(input => env.calls.length === 1 ? firstWrite.promise.then(() => env.accept(input)) : env.accept(input))
  env.state.workbench.run = async () => {
    env.state.submitting.value = true
    const entry = await env.result.persist()
    submitted.push(plain(entry))
    submittedReady.resolve()
    await submit.promise
    env.state.submitting.value = false
  }
  env.change('Older')
  const oldWrite = env.result.persist()
  await Promise.resolve()
  env.change('At click')
  const running = env.result.runWorkflow()
  env.change('Future edit')
  env.result.scheduleAutosave()
  assert.equal(env.timers.size, 0)
  firstWrite.resolve()
  await oldWrite
  await submittedReady.promise
  assert.equal(submitted[0].definition.steps[0].model, 'At click')
  assert.equal(env.timers.size, 0)
  submit.resolve()
  await running
  assert.equal(env.timers.size, 1)
  await env.fireTimer()
  assert.equal(env.calls.at(-1).definition.steps[0].model, 'Future edit')
  assert.equal(submitted[0].definition.steps[0].model, 'At click')
})

test('unmount cancels timers and ignores acknowledgements for the disposed editor', async () => {
  const env = createState()
  const waiting = deferred()
  env.setSaveHandler(input => waiting.promise.then(() => env.accept(input)))
  env.change('Submitted')
  const saving = env.result.persist()
  await Promise.resolve()
  env.change('Future')
  env.dispose()
  waiting.resolve()
  assert.equal(await saving, null)
  assert.equal(env.state.expectedUpdatedAt.value, 1)
  assert.equal(env.timers.size, 0)
})

test('name editing pauses autosave and resumes after the name is committed', async () => {
  const env = createState()
  env.state.nameEditing.value = true
  env.state.name.value = 'Renamed'
  env.result.scheduleAutosave()
  assert.equal(env.timers.size, 0)
  env.state.nameEditing.value = false
  env.result.scheduleAutosave()
  await env.fireTimer()
  assert.equal(env.calls[0].name, 'Renamed')
})

test('serialization failures are reported without writing or losing the draft', async () => {
  const env = createState()
  env.change('Latest')
  env.state.buildSimpleWorkflowDefinition = () => { throw new Error('Invalid parameter') }
  assert.equal(await env.result.persist(), null)
  assert.equal(env.calls.length, 0)
  assert.equal(env.state.saveError.value, 'Invalid parameter')
  assert.equal(env.result.dirty.value, true)
  assert.equal(env.timers.size, 0)
})

test('run validation errors do not prevent saving a recoverable draft', async () => {
  const env = createState()
  env.change('Unfinished')
  env.state.formError.value = 'Missing model'
  await env.result.persist()
  assert.equal(env.calls.length, 1)
  assert.equal(env.result.dirty.value, false)
})

test('a conflict copy uses the latest name and definition from the live draft', async () => {
  const env = createState()
  const waiting = deferred()
  env.setSaveHandler(async () => { await waiting.promise; throw new ConflictError('Newer revision') })
  env.state.name.value = 'Submitted name'
  env.change('Submitted model')
  const saving = env.result.persist()
  await Promise.resolve()
  env.state.name.value = 'Latest name'
  env.change('Latest model')
  waiting.resolve()
  await saving
  env.setSaveHandler(undefined)
  env.result.saveConflictCopy()
  await env.drain()
  assert.equal(env.calls.at(-1).name, 'Latest name copy')
  assert.equal(env.calls.at(-1).definition.steps[0].model, 'Latest model')
  assert.equal(env.result.dirty.value, false)
})
