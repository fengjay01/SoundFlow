import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { computed, isReactive } from 'vue'
import { useWorkbenchStore } from '../src/stores/workbench.ts'

test('a newly created session remains reactive while switching between editing and execution', () => {
  setActivePinia(createPinia())
  const store = useWorkbenchStore()
  const session = store.session('new-simple')
  assert.equal(isReactive(session), true)
  const mode = computed(() => session.viewMode)
  assert.equal(mode.value, 'edit')
  store.session('new-simple').viewMode = 'execution'
  assert.equal(mode.value, 'execution')
})

test('saving a new document preserves its input bindings and selected execution session', () => {
  setActivePinia(createPinia())
  const store = useWorkbenchStore()
  const source = store.session('new-simple')
  source.bindings.inputs.input = { files: ['D:/song.wav'] }
  source.selectedJobId = 'job-1'
  store.adopt('new-simple', 'saved')
  assert.deepEqual(store.session('saved').bindings.inputs.input.files, ['D:/song.wav'])
  assert.equal(store.session('saved').selectedJobId, 'job-1')
  store.session('another').bindings.inputs.input = { files: ['D:/other.wav'] }
  assert.deepEqual(store.session('saved').bindings.inputs.input.files, ['D:/song.wav'])
})

test('a simple editor restores run history and output settings while keeping its fresh input list empty', () => {
  setActivePinia(createPinia())
  const store = useWorkbenchStore()
  const hostSession = {
    bindings: { inputs: { input: { files: ['D:/previous.wav'], folder: 'D:/previous' } }, primaryInput: 'input', outputs: {}, outputDirectory: 'D:/output', outputLayout: 'flat' },
    viewMode: 'edit', selectedJobId: 'job-1', selectedTaskId: 'task-1',
  }
  const job = { id: 'job-1', tasks: [{ runConfig: { workbench: { bindings: structuredClone(hostSession.bindings) } } }] }
  const original = JSON.stringify({ hostSession, job })
  store.applyRemoteState({ workflowKey: 'simple', jobs: [job], session: hostSession }, true)
  assert.deepEqual(store.session('simple').bindings.inputs, {})
  assert.equal(store.session('simple').bindings.primaryInput, '')
  assert.equal(store.session('simple').bindings.outputDirectory, 'D:/output')
  assert.equal(store.session('simple').selectedJobId, 'job-1')
  assert.deepEqual(store.remoteJobs.simple[0].tasks[0].runConfig.workbench.bindings.inputs.input.files, ['D:/previous.wav'])
  assert.equal(JSON.stringify({ hostSession, job }), original)
})

test('a session reply cannot replace files chosen in the current simple editor', () => {
  setActivePinia(createPinia())
  const store = useWorkbenchStore()
  store.session('simple').bindings.inputs.input = { files: ['D:/current.wav'] }
  store.session('simple').bindings.primaryInput = 'input'
  const session = { bindings: { inputs: { input: { files: ['D:/previous.wav'] } }, primaryInput: 'previous', outputs: {}, outputLayout: 'flat' }, viewMode: 'edit', selectedJobId: '', selectedTaskId: '' }
  store.applyRemoteState({ workflowKey: 'simple', jobs: [], session }, true)
  assert.deepEqual(store.session('simple').bindings.inputs.input.files, ['D:/current.wav'])
  assert.equal(store.session('simple').bindings.primaryInput, 'input')
})

test('other editors retain their existing session restoration behavior', () => {
  setActivePinia(createPinia())
  const store = useWorkbenchStore()
  const session = { bindings: { inputs: { '1': { files: ['D:/native.wav'] } }, primaryInput: '1', outputs: {}, outputLayout: 'flat' }, viewMode: 'edit', selectedJobId: '', selectedTaskId: '' }
  store.applyRemoteState({ workflowKey: 'advanced', jobs: [], session })
  assert.deepEqual(store.session('advanced').bindings.inputs['1'].files, ['D:/native.wav'])
})
