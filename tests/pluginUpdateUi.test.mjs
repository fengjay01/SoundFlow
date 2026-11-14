import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'
import { computed, ref } from 'vue'

const descriptor = parse(readFileSync(new URL('../src/views/PluginsView.vue', import.meta.url), 'utf8')).descriptor
const source = ts.createSourceFile('plugins.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
const code = ts.transpileModule(source.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(source)).join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText

function page({ failNames = [], switchRuntimeAfterFirstUpdate = false, cancelled = false } = {}) {
  const calls = [], warnings = [], confirmations = [], infos = [], successes = [], errors = []
  let inFlight = 0, overlapping = false
  const market = {
    snapshot: ref({ runtime: { identity: 'cpu' }, catalog: [], installed: [{ name: 'hello', version: '1.0' }] }),
    updates: ref({}), loading: ref(false), catalogLoading: ref(false), error: ref(''), busy: ref(false), logs: ref([]), activity: ref(null),
    exclusiveBusy: ref(false), activities: ref([]), logRecords: ref([]),
    openMarketplace() {}, refresh() {}, refreshInventory() {}, ensureInventory() {}, cancel() {},
    manage: async (...args) => {
      calls.push(args)
      if (inFlight) overlapping = true
      inFlight++
      try {
        await Promise.resolve()
        if (failNames.includes(args[1]?.name)) {
          market.activity.value = { status: cancelled ? 'cancelled' : 'failed' }
          throw new Error('update failed')
        }
        if (switchRuntimeAfterFirstUpdate && args[0] === 'update') market.snapshot.value.runtime.identity = 'cuda'
      } finally { inFlight-- }
    },
  }
  const scope = { computed, ref, nextTick: async callback => callback?.(), watch() {}, onMounted() {},
    useI18n: () => ({ t: key => key, locale: ref('zh-CN') }), usePluginMarketplace: () => market,
    pluginLocale: () => ({ title: '', description: '', tags: [], operations: {} }),
    useRouter: () => ({ push() {} }), useAppStore: () => ({}), useSettingsStore: () => ({ pluginDefaultsPolicy: 'review' }),
    useDialog: () => ({ warning: options => confirmations.push(options) }),
    useMessage: () => ({ success: value => successes.push(value), warning: value => warnings.push(value),
      error: value => errors.push(value), info: value => infos.push(value) }),
  }
  const controller = vm.runInNewContext(`${code}\n({ selectMore, confirmUpdate, showUpdate, installDependencies, updateRuntime, showLogs,
    checkUpdates, openBatchUpdate, runBatchUpdate, showBatchUpdate, batchItems, batchSelected, batchDependencies, batchRunning,
    toggleBatchItem, toggleBatchAll })`, scope)
  return { controller, calls, warnings, confirmations, infos, successes, errors, market,
    get overlapping() { return overlapping } }
}

function withUpdates(market) {
  market.snapshot.value.installed = [
    { name: 'alpha', version: '1.0', managed: true, url: 'https://example.invalid/alpha' },
    { name: 'beta', version: '1.0', managed: true, url: 'https://example.invalid/beta' },
    { name: 'gamma', version: '2.0', managed: true, url: 'https://example.invalid/gamma' },
    { name: 'manual', version: '1.0', managed: false, url: null },
  ]
  market.updates.value = {
    alpha: { up_to_date: false, remote_version: '2.0' },
    beta: { up_to_date: false, remote_version: '2.0' },
    gamma: { up_to_date: true, remote_version: '2.0' },
    manual: { up_to_date: false, remote_version: '3.0' },
  }
}

test('plugin updates default to preserving dependencies and bind the reviewed runtime', async () => {
  const { controller: c, calls } = page()
  c.selectMore('update', { name: 'hello' })
  assert.equal(c.showUpdate.value, true)
  assert.equal(c.installDependencies.value, false)
  await c.confirmUpdate()
  assert.equal(c.showUpdate.value, false)
  assert.equal(c.showLogs.value, false)
  assert.equal(calls[0][0], 'update')
  assert.equal(calls[0][1].installDependencies, false)
  assert.equal(calls[0][1].expectedRuntime, 'cpu')
})

test('dependency installation is explicit and is not remembered for the next update', async () => {
  const { controller: c, calls } = page()
  c.selectMore('update', { name: 'hello' })
  c.installDependencies.value = true
  await c.confirmUpdate()
  assert.equal(calls[0][1].installDependencies, true)
  c.selectMore('update', { name: 'hello' })
  assert.equal(c.installDependencies.value, false)
})

test('runtime replacement rejects a stale update confirmation before starting work', async () => {
  const { controller: c, calls, market, warnings } = page()
  c.selectMore('update', { name: 'hello' })
  market.snapshot.value.runtime.identity = 'cuda'
  await c.confirmUpdate()
  assert.equal(calls.length, 0)
  assert.equal(warnings[0], 'plugins.updateRuntimeChanged')
})

test('repairing dependencies requires its own confirmation', async () => {
  const { controller: c, calls, confirmations } = page()
  c.selectMore('repair', { name: 'hello' })
  assert.equal(calls.length, 0)
  assert.equal(confirmations[0].content, 'plugins.repairConfirm')
  await confirmations[0].onPositiveClick()
  assert.equal(calls[0][0], 'repair')
})

test('checking for updates opens a list of updatable, managed plugins selected by default', async () => {
  const { controller: c, calls, market } = page()
  withUpdates(market)
  await c.checkUpdates()
  assert.equal(calls[0][0], 'check_updates')
  assert.equal(c.showBatchUpdate.value, true)
  assert.equal(c.batchItems.value.length, 2)
  assert.deepEqual(c.batchSelected.value, ['alpha', 'beta'])
  assert.equal(c.batchDependencies.value, false)
})

test('selected updates run sequentially and carry the dependency option', async () => {
  const { controller: c, calls, market, overlapping } = page()
  withUpdates(market)
  await c.checkUpdates()
  c.toggleBatchItem('alpha', false)
  c.batchDependencies.value = true
  await c.runBatchUpdate(c.batchSelected.value)
  assert.equal(c.showBatchUpdate.value, false)
  assert.equal(c.batchRunning.value, false)
  assert.equal(overlapping, false)
  assert.deepEqual(calls.slice(1).map(call => [call[0], call[1].name]), [['update', 'beta']])
  assert.equal(calls[1][1].installDependencies, true)
  assert.equal(calls[1][1].expectedRuntime, 'cpu')
})

test('update all attempts every plugin and reports partial failures', async () => {
  const { controller: c, calls, market, errors } = page({ failNames: ['beta'] })
  withUpdates(market)
  await c.checkUpdates()
  await c.runBatchUpdate(c.batchItems.value.map(item => item.name))
  assert.deepEqual(calls.slice(1).map(call => call[1].name), ['alpha', 'beta'])
  assert.deepEqual(errors, ['plugins.batchUpdatePartial'])
})

test('a runtime switch or cancellation stops the remaining batch updates', async () => {
  for (const [options, warning] of [
    [{ switchRuntimeAfterFirstUpdate: true }, 'plugins.batchUpdateRuntimeChanged'],
    [{ failNames: ['alpha'], cancelled: true }, 'plugins.batchUpdateCancelled'],
  ]) {
    const { controller: c, calls, market, warnings } = page(options)
    withUpdates(market)
    await c.checkUpdates()
    await c.runBatchUpdate(c.batchItems.value.map(item => item.name))
    assert.deepEqual(calls.slice(1).map(call => call[1].name), ['alpha'])
    assert.equal(warnings.at(-1), warning)
  }
})
