import assert from 'node:assert/strict'
import test from 'node:test'
import { appendWorkflowNodeLogs, visibleWorkflowNodeLogs } from '../src/workflows/nodeLogs.ts'
import { visibleWorkflowNodeStates } from '../src/workflows/workbench.ts'

const definition = { version: 1, steps: [{ id: 'first', stems: ['Vocals', 'Other'], save: { Vocals: 'Default', Other: 'Default' } }], ensembles: [{ id: 'mix', save: 'Default', output_stem: 'Mixed' }] }

test('compiler helper logs are grouped under their visible simple node', () => {
  assert.deepEqual(visibleWorkflowNodeLogs(definition, {
    input_audio: ['Input'], 'step:first': ['Model'], 'save:first:Vocals': ['Save vocals'],
    'studio:ensemble:mix:mix:1': ['Mix'], 'studio:ensemble-save:mix': ['Save mix'], other: ['Internal'],
  }), { input: ['Input'], first: ['Model'], save: ['Save vocals', 'Save mix'], mix: ['Mix'] })
  assert.deepEqual(visibleWorkflowNodeLogs({ nodes: [], links: [] }, { '4': ['Native log'] }), { '4': ['Native log'] })
  assert.deepEqual(visibleWorkflowNodeLogs(definition), {})
})

test('node logs have bounded retention across long workflows and repeated messages', () => {
  const task: { nodeLogs?: Record<string, string[]> } = {}
  for (let index = 0; index < 10; index++) appendWorkflowNodeLogs(task, String(index), Array.from({ length: 200 }, (_, line) => 'line ' + line))
  assert.ok(Object.values(task.nodeLogs!).reduce((sum, lines) => sum + lines.length, 0) <= 600)
  assert.equal(task.nodeLogs!['9'].length, 120)
  assert.equal(task.nodeLogs!['9'].at(-1), 'line 199')
})

test('node ids that match object prototype names remain ordinary log keys', () => {
  const task: { nodeLogs?: Record<string, string[]> } = {}
  appendWorkflowNodeLogs(task, '__proto__', 'Log')
  assert.equal(Object.hasOwn(task.nodeLogs!, '__proto__'), true)
  assert.deepEqual(task.nodeLogs!.__proto__, ['Log'])
  assert.equal(Object.getPrototypeOf(task.nodeLogs), Object.prototype)
})

test('numeric id ordering cannot evict the current node error when the log budget is full', () => {
  const task: { nodeLogs?: Record<string, string[]> } = {}
  for (const id of ['10', '11', '12', '13', '14']) appendWorkflowNodeLogs(task, id, Array.from({ length: 120 }, () => 'Previous node log'))
  appendWorkflowNodeLogs(task, '1', '[error] Current node failure')
  assert.deepEqual(task.nodeLogs!['1'], ['[error] Current node failure'])
  assert.equal(Object.values(task.nodeLogs!).reduce((total, lines) => total + lines.length, 0), 600)
  assert.equal(task.nodeLogs!['10'].length, 119)
})

test('a large append keeps the newest current-node lines within both retention limits', () => {
  const task: { nodeLogs?: Record<string, string[]> } = {}
  for (const id of ['10', '11', '12', '13', '14']) appendWorkflowNodeLogs(task, id, Array.from({ length: 120 }, () => 'Previous node log'))
  appendWorkflowNodeLogs(task, '1', Array.from({ length: 200 }, (_, index) => 'Current log ' + index))
  assert.equal(task.nodeLogs!['1'].length, 120)
  assert.equal(task.nodeLogs!['1'][0], 'Current log 80')
  assert.equal(task.nodeLogs!['1'].at(-1), 'Current log 199')
  assert.equal(Object.values(task.nodeLogs!).reduce((total, lines) => total + lines.length, 0), 600)
})

test('the save summary waits for all connected outputs and reports aggregate progress', () => {
  const states = { 'save:first:Vocals': { status: 'done' as const, progress: 100 } }
  assert.deepEqual(visibleWorkflowNodeStates(definition, states).save, { status: 'running', progress: 100 / 3 })
  const failed = { ...states, 'save:first:Other': { status: 'failed' as const, progress: 50 } }
  assert.deepEqual(visibleWorkflowNodeStates(definition, failed).save, { status: 'failed', progress: 50 })
})
