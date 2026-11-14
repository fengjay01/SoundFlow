import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'

const viewPath = new URL('../src/views/WorkflowNodeEditorView.vue', import.meta.url)
const viewDescriptor = parse(readFileSync(viewPath, 'utf8')).descriptor
const template = viewDescriptor.template?.content || ''
const script = ts.createSourceFile(
  'WorkflowNodeEditorView.ts',
  viewDescriptor.scriptSetup.content,
  ts.ScriptTarget.Latest,
  true,
)
const names = new Set([
  'snapshot',
  'dirty',
  'captureInitialSnapshot',
  'syncCurrentDefinition',
  'closeEditor',
  'destroyWindow',
  'saveAndClose',
  'runPromise',
  'runWorkflow',
  'flushChanges',
])
const selected = script.statements.filter(statement => (
  ts.isFunctionDeclaration(statement) ? names.has(statement.name?.text)
    : ts.isVariableStatement(statement) && statement.declarationList.declarations.some(item => names.has(item.name.getText(script)))
))
assert.equal(selected.length, names.size)
const code = ts.transpileModule(selected.map(statement => statement.getText(script)).join('\n'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText

const editorPath = new URL('../src/components/workflow/WorkflowNodeEditorLite.vue', import.meta.url)
const editorScript = parse(readFileSync(editorPath, 'utf8')).descriptor.scriptSetup.content

function computed(read) {
  return { get value() { return read() } }
}

function ref(value) {
  return { value }
}

function createContext() {
  const navigation = []
  const warnings = []
  let canvasDefinition = { nodes: [{ id: 1 }], links: [] }
  let saveResult = true
  const context = {
    computed,
    name: ref('Workflow'),
    description: ref(''),
    defaultDevice: ref('auto'),
    defaultFormat: ref('wav'),
    definition: ref(canvasDefinition),
    loaded: ref(true),
    initialSnapshot: ref(''),
    editorRef: ref({ snapshotDefinition: () => canvasDefinition }),
    showClosePrompt: ref(false),
    saving: ref(false),
    closing: ref(false), editingId: ref('flow'), showRevisionConflict: ref(false),
    saveQueue: Promise.resolve(), cancelAutosave() {}, scheduleAutosave() {}, finishNameEdit() {},
    formError: ref(''),
    currentWindow: null,
    invoke: async () => {},
    router: { push: async target => navigation.push(target) },
    message: { warning: value => warnings.push(value) },
    save: async () => {
      if (saveResult) result.captureInitialSnapshot()
      return saveResult
    },
    workbench: { run: async () => {} },
  }
  context.canSave = computed(() => !context.formError.value)
  const result = vm.runInNewContext(
    `${code}\n({ dirty, captureInitialSnapshot, closeEditor, saveAndClose, runWorkflow })`,
    context,
  )
  return {
    context,
    result,
    navigation,
    warnings,
    setCanvasDefinition(value) { canvasDefinition = value },
    setSaveResult(value) { saveResult = value },
  }
}

test('advanced editor exposes the same unsaved-close actions as the simple editor', () => {
  assert.ok(template.includes('ref="editorRef"'))
  assert.ok(template.includes('@initialized="initializeEditorBaseline"'))
  assert.ok(template.includes('v-model:show="showClosePrompt"'))
  assert.ok(template.includes("t('workflows.simpleDiscardChanges')"))
  assert.ok(template.includes("t('workflows.simpleSaveAndClose')"))
  assert.ok(editorScript.includes('defineExpose({ snapshotDefinition })'))
  assert.ok(editorScript.includes("emit('initialized')"))
})

test('advanced editor saves the live canvas before closing', async () => {
  const state = createContext()
  state.result.captureInitialSnapshot()
  assert.equal(state.result.dirty.value, false)

  state.setCanvasDefinition({ nodes: [{ id: 1 }, { id: 2 }], links: [] })
  await state.result.closeEditor()

  assert.equal(state.result.dirty.value, false)
  assert.equal(state.context.showClosePrompt.value, false)
  assert.deepEqual(state.navigation, ['/workflows'])
})

test('advanced editor closes only after a successful save and keeps invalid drafts open', async () => {
  const state = createContext()
  state.result.captureInitialSnapshot()
  state.setCanvasDefinition({ nodes: [{ id: 1 }, { id: 2 }], links: [] })
  state.context.showClosePrompt.value = true

  state.setSaveResult(false)
  await state.result.saveAndClose()
  assert.deepEqual(state.navigation, [])

  state.context.showClosePrompt.value = true
  state.context.formError.value = 'invalid workflow'
  await state.result.saveAndClose()
  assert.deepEqual(state.navigation, [])
  assert.deepEqual(state.warnings, ['invalid workflow'])
  assert.equal(state.context.showClosePrompt.value, true)

  state.context.formError.value = ''
  state.setSaveResult(true)
  await state.result.saveAndClose()
  assert.deepEqual(state.navigation, ['/workflows'])
})

test('closing an advanced editor waits for submission to finish and does not submit twice', async () => {
  const state = createContext()
  state.result.captureInitialSnapshot()
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  let submissions = 0
  state.context.workbench.run = () => { submissions++; return pending }
  const first = state.result.runWorkflow()
  const second = state.result.runWorkflow()
  const closing = state.result.closeEditor()
  await Promise.resolve()
  assert.equal(submissions, 1)
  assert.deepEqual(state.navigation, [])
  finish()
  await Promise.all([first, second, closing])
  assert.deepEqual(state.navigation, ['/workflows'])
})
