<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import { loadAppStore, saveAppStore } from '@/utils/appStore'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { useMessage } from 'naive-ui'
import { useSettingsStore } from '@/stores/settings'
import { useModelStore } from '@/stores/model'
import { useAppStore } from '@/stores/app'
import {
  DocumentTextOutline,
  FolderOpenOutline,
  GridOutline,
  ListOutline,
  PlayOutline,
  RefreshOutline,
  SearchOutline,
  StopOutline,
} from '@vicons/ionicons5'

defineOptions({ name: 'LyricsView' })

interface CharWord { char: string; start: number; end: number; confidence: number }
interface LyricLine { id: string; text: string; start: number; end: number; words: CharWord[]; agent?: string; is_background?: boolean; translation?: string }
interface SearchResult { songId: number; name: string; artist: string; album: string; durationMs: number }
interface EditRow { id: string; text: string; start: number; end: number; isBackground: boolean; agent: string; translation: string }
interface LyricsJson {
  version: number
  language: string
  source: string
  alignment_model: string
  lines: LyricLine[]
}
interface WorkerEvent { type?: string; taskId?: string; payload?: Record<string, any> }

const { t } = useI18n()
const message = useMessage()
const appStore = useAppStore()

// ---- 输入与选项 ----
const input = ref('')
const output = ref('')
// 默认 large-v3（最准）；语言默认自动检测；视频高度默认原视频清晰度（用户偏好）
const modelSize = ref<string | null>(null)
const language = ref<'auto' | 'zh' | 'en' | 'multi'>('auto')  // 'auto' → 后端传 null；'multi' → 各模型混语模式

// ---- 歌词来源：自动识别 / 导入歌词（时间轴由模型对齐） ----
const lyricsSource = ref<'lrc' | 'text' | 'none'>('lrc')
// ---- 工作流模式：video = 生成 KTV 视频；sync = 纯文本打轴（导出 LRC/SRT/TTML，不烧视频） ----
// ---- 网易云歌曲链接接线（粘贴 URL → 抓歌词填入） ----
const neteaseUrl = ref('')
const fetchingLink = ref(false)
const importedLyrics = ref<{ name: string; text: string; format: 'lrc' | 'text' } | null>(null)
// 在线搜索歌词的本地落盘路径（保存到歌曲同目录 <歌名>.lrc）
const importedLyricsPath = ref('')
const lyricsFileInput = ref<HTMLInputElement | null>(null)

// ---- 联网搜索歌词（网易云公开接口，走 worker search_lyrics/fetch_lyrics） ----
const searchOpen = ref(false)
const searchQuery = ref('')
const searching = ref(false)
const searchResults = ref<SearchResult[]>([])
const searchTaskId = ref('')

// ---- 人工编辑模式（行级：文本 / 开始 / 结束；编辑后重生成 ASS/TTML/LRC/视频） ----
const editRows = ref<EditRow[]>([])
const deletedLineIds = ref<Set<string>>(new Set())
// 编辑台一次渲染全部行，表格容器跟随窗口高度并自行滚动，不再分页。

// ---- 工作台新参数（lrc-ttml 同款） ----
const engine = ref<'whisper' | 'tifa' | 'qwen' | 'qwen-ultra' | 'qwen-ultra-head' | 'mms' | 'paraformer' | null>(null)
// TIFA 外部 Python 路径持久化（每次处理随 payload 传给 worker）
const tifaPython = ref(localStorage.getItem('lyrics.tifaPython') || '')
watch(tifaPython, (v) => localStorage.setItem('lyrics.tifaPython', v))
const tifaOk = ref<boolean | null>(null)
const useVocals = ref(false)
const vocalsPath = ref('')
// 自动分离：用主应用分离页已下载的模型（MDX23C/UVR/Demucs 等）串联
const modelStore = useModelStore()
const selectedSeparationModel = ref('')
const separating = ref(false)
// ---- 分离串联输出设置（完整复刻分离页「02 输出设置」） ----
const sepOutputDir = ref('')  // 空 = 默认 <产物文件夹>/separated
const sepFormat = ref(String(useSettingsStore().defaultFormat || 'wav').toLowerCase())
const sepLayout = ref<'flat' | 'folders'>('flat')
const sepNamingEnabled = ref(true)
const sepNamingTemplate = ref('%index%_%filename%_%stem%')
const sepStemVocals = ref(true)
const sepStemInstrumental = ref(false)
const sepUseTta = ref(false)
const sepFormatOptions = ['wav', 'mp3', 'flac', 'm4a'].map((f) => ({ label: f.toUpperCase(), value: f }))
const sepNamingConfig = computed(() => ({
  enabled: Boolean(sepNamingTemplate.value.trim()),
  template: sepNamingTemplate.value.trim() || '%index%_%filename%_%stem%',
  stemOrder: sepOrderedStems.value,
  overwrite: true,  // 歌词链路重复分离直接覆盖，不产生 _2/_3 副本
}))
// 高级推理参数：仅透传用户显式设置的项（其余沿用 worker/模型默认）
const sepInferenceParams = computed(() => {
  const params: Record<string, number | boolean> = {}
  if (sepBatchSize.value != null) params.batch_size = sepBatchSize.value
  if (sepOverlapSize.value != null) params.overlap_size = sepOverlapSize.value
  if (sepNumOverlap.value != null) params.num_overlap = sepNumOverlap.value
  if (sepChunkSize.value != null) params.chunk_size = sepChunkSize.value
  if (sepStandardize.value) params.standardize = true
  if (sepNormalize.value) params.normalize = true
  return Object.keys(params).length ? params : undefined
})
const sepSelectedStems = computed(() => {
  const stems: string[] = []
  if (sepStemVocals.value) stems.push('vocals')
  if (sepStemInstrumental.value) stems.push('instrumental')
  return stems
})
// 高级设置弹窗（复刻分离页）：TTA / 输出编码 / 高级推理参数
const sepMp3Bitrate = ref('320k')
const sepNormalize = ref(false)          // 输出归一化
const sepStandardize = ref(false)        // 输入标准化
const sepBatchSize = ref<number | null>(null)
const sepOverlapSize = ref<number | null>(null)
const sepNumOverlap = ref<number | null>(null)
const sepChunkSize = ref<number | null>(null)
const showSepAdvanced = ref(false)
const sepMp3BitrateOptions = ['320k', '256k', '192k', '128k', '96k'].map((v) => ({ label: v, value: v }))
// 命名规则编辑弹窗（复刻分离页）：模板 / 预览 / 变量 / 编号顺序
const sepStemOrder = ref<string[]>(['vocals', 'instrumental'])
const showSepNaming = ref(false)
const sepNamingInputRef = ref<{ inputElRef: HTMLInputElement } | null>(null)
const sepNamingTokens = [
  { value: '%index%', labelKey: 'separate.namingTokenIndex' },
  { value: '%input_number%', labelKey: 'separate.namingTokenInputNumber' },
  { value: '%filename%', labelKey: 'separate.namingTokenFilename' },
  { value: '%stem%', labelKey: 'separate.namingTokenStem' },
  { value: '%model%', labelKey: 'separate.namingTokenModel' },
  { value: '%yyyyMMdd%', labelKey: 'separate.namingTokenDate' },
  { value: '%hhmmss%', labelKey: 'separate.namingTokenTime' },
  { value: '%ddmmss%', labelKey: 'separate.namingTokenLegacyTime' },
]
const sepOrderedStems = computed(() => {
  // 编号顺序作用于已勾选音轨（顺序表里未勾选的剔除，新勾选的追加到尾部）
  const byKey = new Map(sepSelectedStems.value.map((s) => [s.toLowerCase(), s]))
  const out: string[] = []
  for (const stem of sepStemOrder.value) {
    const hit = byKey.get(stem.toLowerCase())
    if (hit && !out.includes(hit)) out.push(hit)
  }
  for (const stem of sepSelectedStems.value) {
    if (!out.includes(stem)) out.push(stem)
  }
  return out
})
function sepNamingPreviewFile(stem: string, index: number): string {
  const map: Record<string, string> = {
    '%index%': String(index + 1).padStart(2, '0'),
    '%input_number%': '01',
    '%filename%': '输入文件名',
    '%stem%': stem.charAt(0).toUpperCase() + stem.slice(1),
    '%model%': selectedSeparationModel.value.split(/[\\/]/).pop() || 'Model',
    '%yyyyMMdd%': new Date().toISOString().slice(0, 10).replace(/-/g, ''),
    '%hhmmss%': new Date().toTimeString().slice(0, 8).replace(/:/g, ''),
    '%ddmmss%': new Date().toTimeString().slice(0, 8).replace(/:/g, ''),
  }
  const base = sepNamingTemplate.value.trim().replace(/%(\w+)%/g, (m) => map[m] ?? m)
  return `${base}.${(sepFormat.value || 'wav').toLowerCase()}`
}
function insertSepToken(token: string) {
  const input = sepNamingInputRef.value?.inputElRef as HTMLInputElement | undefined
  if (input && typeof input.selectionStart === 'number' && typeof input.selectionEnd === 'number') {
    const start = input.selectionStart
    const end = input.selectionEnd
    sepNamingTemplate.value = sepNamingTemplate.value.slice(0, start) + token + sepNamingTemplate.value.slice(end)
    void nextTick(() => { input.focus(); input.setSelectionRange(start + token.length, start + token.length) })
  } else {
    sepNamingTemplate.value += token
  }
}
function moveSepStem(index: number, dir: -1 | 1) {
  const target = index + dir
  if (target < 0 || target >= sepStemOrder.value.length) return
  const next = [...sepStemOrder.value]
  ;[next[index], next[target]] = [next[target], next[index]]
  sepStemOrder.value = next
}
function resetSepNaming() {
  sepNamingTemplate.value = '%index%_%filename%_%stem%'
  sepStemOrder.value = ['vocals', 'instrumental']
}
async function pickSepOutputDir() {
  try {
    const dir = await invoke<string | null>('pick_output_folder')
    if (dir) sepOutputDir.value = dir
  } catch { /* 用户取消 */ }
}
const dragOverlap = ref(false)
const layeredLyrics = ref(false)

// 分离页同款列表：已下载且可用的分离模型
const separationModelOptions = computed(() =>
  modelStore.downloadedModels.map((m) => ({
    label: `${m.name}${m.modelType ? ` · ${m.modelType}` : ''}`,
    value: m.name,
  })),
)

async function ensureSeparationModels() {
  if (!modelStore.modelsLoaded && !modelStore.isLoading) {
    try { await modelStore.loadModels() } catch { /* 列表加载失败不阻断 */ }
  }
  if (!selectedSeparationModel.value) {
    selectedSeparationModel.value = modelStore.selectedModel
      || modelStore.downloadedModels[0]?.name || ''
  }
}

function vocalsPathFromOutputs(outputs: unknown): string {
  if (!Array.isArray(outputs)) return ''
  for (const item of outputs as Array<{ stem?: string; path?: string }>) {
    if (String(item?.stem || '').toLowerCase().includes('vocal') && item?.path) {
      return String(item.path)
    }
  }
  return ''
}

// 自动分离（主应用分离引擎）→ 完成后把人声轨填入并继续歌词流水线
async function startAutoSeparationThenPipeline() {
  const model = selectedSeparationModel.value
  if (!model) {
    message.warning(t('lyrics.noSeparationModels'))
    return
  }
  // 复用检查：<产物>/separated/ 已有人声轨 → 跳过分离直接对齐
  const reused = await reuseExistingVocals(input.value)
  if (reused) {
    vocalsPath.value = reused
    message.success(t('lyrics.vocalsReused'))
    pushLog(`[复用] 已有分离产物，跳过分离：${reused}`)
    await startTask()  // vocalsPath 已填，直接走流水线
    return
  }
  if (!sepSelectedStems.value.length) {
    message.warning(t('separate.noStemsSelected'))
    return
  }
  if (!sepStemVocals.value) {
    message.warning(t('lyrics.sepNeedsVocals'))
    return
  }
  taskId.value = `lyr_${Date.now()}`  // pipeline 任务 id 预生成，job 记录一并建立
  upsertJob({
    id: taskId.value, time: Date.now(), input: input.value,
    model: `分离串联 · ${model}`, source: 'lrc',
    status: 'running', percent: 0, stage: 'separating', outputs: {}, error: '',
  })
  const sepTaskId = `lyr_sep_${Date.now()}`
  separating.value = true
  running.value = true  // 分离期间保持开始按钮 loading
  await ensureListener()
  try {
    await invoke('start_separation', {
      payload: {
        taskId: sepTaskId,
        model,
        input: input.value,
        // 分离产物默认放产物文件夹内（<输入目录>/<歌名>/separated）；「输出设置」里可覆盖
        output: sepOutputDir.value.trim()
          || `${output.value || `${input.value.replace(/[\\/][^\\/]+$/, '')}/${(input.value.split(/[\\/]/).pop() || '').replace(/\.[^.]+$/, '')}`}/separated`,
        // 以下完整复刻分离页「输出设置」：格式 / 保存方式 / 命名规则 / 音轨 / TTA
        outputLayout: sepLayout.value,
        outputFormat: sepFormat.value || String(settings.defaultFormat || 'wav').toLowerCase(),
        selectedStems: sepSelectedStems.value,
        outputNaming: sepNamingConfig.value,
        useTta: sepUseTta.value,
        inferenceParams: sepInferenceParams.value,
        audioParams: sepFormat.value === 'mp3' ? { mp3_bit_rate: sepMp3Bitrate.value } : undefined,
        modelDir: settings.modelDir || null,
        download: false,
      },
    })
  } catch (e) {
    separating.value = false
    running.value = false
    patchJob(taskId.value, { status: 'failed', error: String(e) })
    message.error(String(e))
  }
}
const tailExtend = ref(true)
const autoSupplement = ref(false)
const logLines = ref<string[]>([])
const lastProgressLog = ref('')  // 进度消息去重（连续相同只记一条）

// ---- 编辑台播放（asset 协议 + HTML audio，播放驱动行高亮） ----
const audioEl = ref<HTMLAudioElement | null>(null)
const audioPlaying = ref(false)
const audioTime = ref(0)
const activeEditId = ref('')

// ---- 任务队列（参考分离页：本次会话 + 历史记录，localStorage 持久化） ----
interface LyricsJob {
  id: string
  time: number
  input: string          // 音频完整路径
  model: string          // 引擎/来源摘要（如 whisper small / 导入歌词·TIFA）
  source: 'lrc' | 'text' | 'none'
  status: 'running' | 'done' | 'failed' | 'cancelled'
  percent: number
  stage: string
  outputs: Record<string, string>
  error: string
  preview?: string[]     // 打轴歌词预览（前 3 行）
  lineCount?: number     // 歌词行数
}
const LYRICS_JOBS_KEY = 'lyrics.jobs'
const lyricsJobs = ref<LyricsJob[]>(loadLyricsJobs())
const showQueueModal = ref(false)
const queueScope = ref<'session' | 'history'>('session')

function loadLyricsJobs(): LyricsJob[] {
  try {
    const raw = JSON.parse(localStorage.getItem(LYRICS_JOBS_KEY) || '[]')
    // 注意：WorkBuddy 切换工具页会整页重载（组件重挂载），但后台 worker
    // 进程不受影响——running 必须原样保留，由 resumeRunningJob 判定真实状态
    return Array.isArray(raw) ? (raw as LyricsJob[]).slice(0, 50) : []
  } catch { return [] }
}

function saveLyricsJobs() {
  try { localStorage.setItem(LYRICS_JOBS_KEY, JSON.stringify(lyricsJobs.value.slice(0, 50))) } catch { /* 忽略 */ }
}

function upsertJob(job: LyricsJob) {
  const idx = lyricsJobs.value.findIndex((j) => j.id === job.id)
  if (idx >= 0) lyricsJobs.value[idx] = job
  else lyricsJobs.value.unshift(job)
  if (lyricsJobs.value.length > 50) lyricsJobs.value.length = 50
  saveLyricsJobs()
}

function patchJob(id: string, fields: Partial<LyricsJob>) {
  const job = lyricsJobs.value.find((j) => j.id === id)
  if (!job) return
  Object.assign(job, fields)
  saveLyricsJobs()
}

const sessionJobs = computed(() => lyricsJobs.value.filter((j) => j.status === 'running'))
const historyJobs = computed(() => lyricsJobs.value.filter((j) => j.status !== 'running'))
const queueJobs = computed(() => queueScope.value === 'session' ? sessionJobs.value : historyJobs.value)
const finishedJobCount = computed(() => lyricsJobs.value.filter((j) => j.status === 'done' || j.status === 'failed' || j.status === 'cancelled').length)

// ---- 批量清除已完成（右上角）：可选仅清记录 / 连同产物移入回收站 ----
const clearAsk = ref(false)
const clearWithFiles = ref(false)

// ---- 产物文件夹级删除：outputs 推导优先（反映真实位置/自定义输出目录），
// ---- job.input 推导兜底（<输入目录>/<歌名>/，outputs 为空的任务也删得到）
function deriveProductFolders(job: LyricsJob): string[] {
  const norm = (v: string) => v.replace(/\//g, '\\')
  const cands: string[] = []
  const outs = Object.values(job.outputs).filter((v): v is string => !!v)
  const pick = outs.find((p) => !/[\\/]separated[\\/]/i.test(p)) || outs[0]
  if (pick) {
    const parts = norm(pick).split('\\').filter(Boolean)
    parts.pop()
    if (parts[parts.length - 1]?.toLowerCase() === 'separated') parts.pop()
    if (parts.length) cands.push(parts.join('\\'))
  }
  const inp = norm(job.input || '')
  const idx = inp.lastIndexOf('\\')
  if (idx > 3) {
    const dir = inp.slice(0, idx)
    const stem = inp.slice(idx + 1).replace(/\.[^.]+$/, '')
    if (dir && stem) cands.push(`${dir}\\${stem}`)
  }
  const inputNorm = norm(job.input || input.value || '').toLowerCase()
  const seen = new Set<string>()
  const out: string[] = []
  for (const c of cands) {
    const k = c.toLowerCase()
    // 安全护栏：绝对路径、非盘根、输入文件不在待删夹内、待删夹不是输入文件所在目录
    if (!/^[A-Za-z]:\\/.test(c) || c.length <= 3) continue
    if (inputNorm.startsWith(k + '\\')) continue
    const dirOfInput = inputNorm.slice(0, inputNorm.lastIndexOf('\\'))
    if (dirOfInput && k === dirOfInput) continue
    if (!seen.has(k)) { seen.add(k); out.push(c) }
  }
  return out
}

async function trashThenPurge(paths: string[]): Promise<{ trashed: number; failed: number }> {
  if (!paths.length) return { trashed: 0, failed: 0 }
  let failed: string[] = paths
  try {
    const res = await invoke<{ trashed: string[]; failed: string[] }>(
      'move_paths_to_trash', { paths, emptyDirs: [] })
    failed = res.failed
  } catch {
    failed = paths
  }
  let purged = 0
  if (failed.length) {
    // 回收站失败（大文件夹/配额/占用）→ 永久删除兜底，保证删干净
    try {
      const res2 = await invoke<{ trashed: string[]; failed: string[] }>(
        'delete_paths_permanent', { paths: failed })
      failed = res2.failed
      purged = res2.trashed.length
    } catch {
      /* 保持 failed 原样 */
    }
  }
  return { trashed: paths.length - failed.length, failed: failed.length }
}

async function confirmClearFinished() {
  clearAsk.value = false
  const finished = lyricsJobs.value.filter((j) => j.status !== 'running')
  if (clearWithFiles.value) {
    // 文件夹级删除（含 separated 与未登记产物），夹外散文件一并清
    const folders = [...new Set(finished.flatMap(deriveProductFolders))]
    const files = finished
      .flatMap((j) => Object.values(j.outputs))
      .filter((v): v is string => !!v)
      .filter((f) => {
        const fl = f.replace(/\//g, '\\').toLowerCase()
        return !folders.some((d) => fl.startsWith(d.toLowerCase() + '\\'))
      })
    const res = await trashThenPurge([...folders, ...files])
    if (res.failed) message.warning(`部分产物删除失败：${res.failed} 项`)
    else if (res.trashed) message.success(t('lyrics.jobFilesTrashed'))
  }
  lyricsJobs.value = lyricsJobs.value.filter((j) => j.status === 'running')
  saveLyricsJobs()
}

function clearFinishedJobs() {
  clearAsk.value = true
  clearWithFiles.value = false
}

// ---- 删除历史记录：可选仅清记录 / 连同生成的文件一起移入回收站 ----
const deleteAsk = ref<LyricsJob | null>(null)
const deleteWithFiles = ref(false)

function askDeleteJob(job: LyricsJob) {
  deleteWithFiles.value = false
  deleteAsk.value = job
}

async function confirmDeleteJob() {
  const job = deleteAsk.value
  if (!job) return
  deleteAsk.value = null
  // 删除的是当前正在跟踪的任务 → 先取消后台 worker 并复位主界面状态
  if (job.id === taskId.value && running.value) {
    void invoke<boolean>('cancel_task', { taskId: job.id }).catch(() => { /* 忽略 */ })
    running.value = false
    separating.value = false
    percent.value = 0
    stage.value = ''
    stageMessage.value = ''
    taskId.value = ''
    errorText.value = ''
  }
  // 连带删除整个产物文件夹（<输入目录>/<歌名>/，含 LRC/SRT/TTML/KRC/播放器/
  // separated 分离产物/源副本），移入系统回收站可恢复；输入音频在上级目录不受影响
  if (deleteWithFiles.value) {
    const folders = deriveProductFolders(job)
    if (!folders.length) {
      message.warning('未能定位产物文件夹，仅清除记录（文件保留）')
    } else {
      const res = await trashThenPurge(folders)
      if (res.failed) message.warning(`部分产物文件夹删除失败（已保留）：${res.failed} 项`)
      else message.success('产物文件夹已删除')
    }
  }
  // 删除的是当前展示中的任务 → 主界面结果区一并清空
  if (job.id === taskId.value) {
    outputs.value = {}
    lyrics.value = null
    percent.value = 0
    markResultsCleared()
  }
  lyricsJobs.value = lyricsJobs.value.filter((j) => j.id !== job.id)
  saveLyricsJobs()
}

// ---- 应用内播放器（音乐按钮）：原生实现，媒体/歌词走 asset 协议 ----
const playerModal = ref(false)
const playerJob = ref<LyricsJob | null>(null)
const playerLines = ref<Array<{ start: number; end: number; bg: boolean; words: Array<{ char: string; start: number; end: number }> }>>([])
const playerAudioEl = ref<HTMLAudioElement | null>(null)
const mpScrollEl = ref<HTMLElement | null>(null)
const playerNow = ref(0)
const playerDur = ref(0)
const playerPlaying = ref(false)
const playerActive = ref(-1)
const playerTitle = computed(() => {
  const name = playerJob.value?.input.split(/[\\/]/).pop() || ''
  return name.replace(/\.[^.]+$/, '')
})
const playerMediaSrc = computed(() => {
  const j = playerJob.value
  if (!j) return ''
  // 优先人声轨，其次输入文件本体（asset 协议，编辑台同款）
  const media = j.outputs.player ? j.input : j.input
  return media ? convertFileSrc(media) : ''
})
const mpCharEls = ref<HTMLElement[]>([])

async function openPlayer(job: LyricsJob) {
  // 优先默认浏览器打开 player.html（自包含滚动歌词播放器）；失败则软件内播放
  const pp = job.outputs.player || Object.values(job.outputs).find((v) => v.endsWith('.player.html'))
  if (pp) {
    try {
      await invoke('open_path', { path: pp })
      return
    } catch { /* 打开失败 → 软件内播放器兜底 */ }
  }
  const jsonPath = job.outputs.lyricsJson || Object.values(job.outputs).find((v) => v.endsWith('.lyrics.json'))
  if (!jsonPath) { message.warning(t('lyrics.noPlayer')); return }
  try {
    const text = await (await fetch(convertFileSrc(jsonPath))).text()
    const data = JSON.parse(text)
    playerLines.value = (data.lines || [])
      .filter((l: { words?: unknown[] }) => Array.isArray(l.words) && l.words.length)
      .map((l: { start: number; end: number; is_background?: boolean; words: Array<{ char: string; start: number; end: number }> }) => ({
        start: l.start, end: l.end, bg: !!l.is_background, words: l.words,
      }))
  } catch {
    message.warning(t('lyrics.noPlayer'))
    return
  }
  playerJob.value = job
  playerModal.value = true
  playerActive.value = -1
  await nextTick()
  if (mpScrollEl.value) {
    mpCharEls.value = Array.from(mpScrollEl.value.querySelectorAll('.mp-line')) as HTMLElement[]
  }
}

function onPlayerTime() {
  const el = playerAudioEl.value
  if (!el) return
  playerNow.value = el.currentTime * 1000
  // 行定位 + 字级渐变填充（\kf 同语义，直接操作 DOM 保证 60fps）
  const t = playerNow.value
  let idx = -1
  playerLines.value.forEach((ln, i) => { if (t >= ln.start && t < ln.end + 300) idx = i })
  if (idx !== playerActive.value && idx >= 0 && mpCharEls.value[idx]) {
    mpCharEls.value[idx].scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
  playerActive.value = idx
  const lineEl = idx >= 0 ? mpCharEls.value[idx] : null
  if (lineEl) {
    const chars = lineEl.querySelectorAll('.mp-char')
    playerLines.value[idx].words.forEach((w, k) => {
      const c = chars[k] as HTMLElement | null
      if (!c) return
      let p = 0
      if (t >= w.end) p = 100
      else if (t >= w.start) p = ((t - w.start) / Math.max(w.end - w.start, 1)) * 100
      c.style.setProperty('--p', p.toFixed(1) + '%')
    })
  }
}
function onPlayerMeta() {
  const el = playerAudioEl.value
  if (el) playerDur.value = el.duration * 1000
}
function togglePlayerPlay() {
  const el = playerAudioEl.value
  if (!el) return
  if (el.paused) el.play()
  else el.pause()
}
function onPlayerSeek(ev: Event) {
  const el = playerAudioEl.value
  if (!el || !el.duration) return
  el.currentTime = Number((ev.target as HTMLInputElement).value) / 1000 * el.duration
}
function revealOutput(job: LyricsJob, key: string) {
  const path = job.outputs[key]
  if (path) reveal(path)
}

function fmtJobTime(ts: number): string {
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}

function jobStatusLabel(status: LyricsJob['status']): string {
  return status === 'running' ? t('lyrics.jobRunning')
    : status === 'done' ? t('lyrics.jobDone')
    : status === 'failed' ? t('lyrics.jobFailed')
    : t('lyrics.jobCancelled')
}

function pushLog(message: string) {  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  logLines.value.push(`[${ts}] ${message}`)
  if (logLines.value.length > 300) logLines.value.splice(0, logLines.value.length - 300)
  if (logOpen.value) scrollLogToBottom()
}

// ---- 结果区：日志折叠面板 + 清空结果 ----
const logOpen = ref(false)
const outputsOpen = ref(true)  // 输出文件卡片默认展开，点击标题折叠
const logBodyEl = ref<HTMLElement | null>(null)

function scrollLogToBottom() {
  void nextTick(() => {
    if (logBodyEl.value) logBodyEl.value.scrollTop = logBodyEl.value.scrollHeight
  })
}

function toggleLog() {
  logOpen.value = !logOpen.value
  if (logOpen.value) scrollLogToBottom()
}

function clearResults() {
  if (running.value) return
  errorText.value = ''
  errorCode.value = ''
  lyrics.value = null
  outputs.value = {}
  percent.value = 0
  stage.value = ''
  stageMessage.value = ''
  logLines.value = []
  lastProgressLog.value = ''
  logOpen.value = false
  markResultsCleared()  // 此刻起只恢复清空之后完成的新任务
  // 连同导入歌词一并清空（重新开始一次完整流程）
  clearImportedLyrics()
}

// ---- 字块拖动微调：水平拖动 → 该字起止时间平移（clamp 行内），自动写回 json ----
const LYR_SAVE_DEBOUNCE = 800
let lyrSaveTimer: ReturnType<typeof setTimeout> | null = null
function persistLyricsEdits() {
  if (lyrSaveTimer) clearTimeout(lyrSaveTimer)
  lyrSaveTimer = setTimeout(async () => {
    const path = loadedLyricsPath.value || outputs.value.lyricsJson
    if (!path || !lyrics.value) return
    try {
      await invoke('start_lyrics', {
        payload: {
          taskId: `lyr_save_${Date.now()}`,
          action: 'save_lyrics_json',
          path,
          data: lyrics.value,
        },
      })
      pushLog('[已保存] 字级时间调整写入 ' + path.split(/[\\/]/).pop())
    } catch (e) {
      message.error(String(e))
    }
  }, LYR_SAVE_DEBOUNCE)
}

function onCharPointerDown(e: PointerEvent, line: { words: Array<{ start: number; end: number }> }, wi: number) {
  const startX = e.clientX
  const w = line.words[wi]
  const origS = w.start
  const origE = w.end
  const span = origE - origS
  const target = e.currentTarget as HTMLElement
  target.setPointerCapture(e.pointerId)
  target.style.cursor = 'grabbing'
  const prevEnd = wi > 0 ? line.words[wi - 1].end : origS - 3000
  const nextStart = wi < line.words.length - 1 ? line.words[wi + 1].start : origE + 3000
  const move = (ev: PointerEvent) => {
    const dt = Math.round((ev.clientX - startX) * 8)  // 8ms/px
    let ns = origS + dt
    ns = Math.max(prevEnd, Math.min(ns, nextStart - span))
    w.start = ns
    w.end = ns + span
  }
  const up = () => {
    target.removeEventListener('pointermove', move)
    target.removeEventListener('pointerup', up)
    target.style.cursor = 'grab'
    persistLyricsEdits()
  }
  target.addEventListener('pointermove', move)
  target.addEventListener('pointerup', up)
}

// ---- 工作台左右占比拖拽（参数栏宽度，持久化 localStorage） ----
const LYRICS_LEFT_W_KEY = 'lyrics.leftWidth'
const leftWidth = ref(Math.min(720, Math.max(260, Number(localStorage.getItem(LYRICS_LEFT_W_KEY)) || 340)))
const splitting = ref(false)

function onSplitDown(e: PointerEvent) {
  e.preventDefault()
  splitting.value = true
  const startX = e.clientX
  const startW = leftWidth.value
  document.body.style.userSelect = 'none'
  const move = (ev: PointerEvent) => {
    ev.preventDefault()
    leftWidth.value = Math.min(720, Math.max(260, Math.round(startW + ev.clientX - startX)))
  }
  const up = () => {
    splitting.value = false
    document.body.style.userSelect = ''
    localStorage.setItem(LYRICS_LEFT_W_KEY, String(leftWidth.value))
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

function resetSplit() {
  leftWidth.value = 340
  localStorage.removeItem(LYRICS_LEFT_W_KEY)
}

let unlistenTifa: (() => void) | null = null
async function checkTifa() {
  try {
    const tid = `lyr_search_${Date.now()}`
    searchTaskId.value = tid
    await ensureListener()
    const off = await listen<WorkerEvent>('pymss://worker-event', (evt) => {
      const msg = evt.payload
      if (!msg || msg.taskId !== tid) return
      if (msg.type === 'task_done') {
        tifaOk.value = !!msg.payload?.available
        off()
      } else if (msg.type === 'error') {
        tifaOk.value = false
        off()
      }
    })
    await invoke('start_lyrics', { payload: { taskId: tid, action: 'tifa_status', modelDir: settings.modelDir || null } })
  } catch {
    tifaOk.value = false
  }
}
const lyricsSourceDesc = computed(() => ({
  lrc: '含时间戳行锚：引擎逐字精对齐，精度最高，无时长限制',
  text: '引擎对齐产出 LRC：选了识别模型 → 先定位每行再分段精对齐；未选识别模型仅支持 ≤290s（超长直接报错，不自动兜底）',
  none: '纯识别转写：识别模型直接听写音频，产出识别文本的逐字时间轴（无需任何歌词文本）',
}[lyricsSource.value] ?? ''))

const lyricsFileHint = computed(() => ({
  lrc: '选择 LRC 歌词文件（含行时间戳，精度最高）',
  text: '选择 TXT 纯文本歌词（无时间戳，逐字时间由模型对齐）',
  none: '',
}[lyricsSource.value] ?? ''))
const lyricsFilePlaceholder = computed(() => lyricsSource.value === 'text'
  ? '选择 TXT 纯文本歌词文件…'
  : '选择 LRC 歌词文件…')

function triggerLyricsPick() {
  void onLyricsFileChange()
}

function clearImportedLyrics() {
  importedLyrics.value = null
  importedLyricsPath.value = ''
  if (lyricsFileInput.value) (lyricsFileInput.value as HTMLInputElement).value = ''
}

async function onLyricsFileChange() {
  // 用 Tauri 对话框选择（拿到完整路径，供 sourceLyricsPath 归档/源 LRC 播放器使用）
  try {
    const path = await invoke<string | null>('pick_lyrics_text_file', {
      kind: lyricsSource.value === 'text' ? 'txt' : 'lrc',
    })
    if (!path) return
    await loadLyricsFromPath(path)
  } catch (e) {
    message.error(String(e))
  }
}
const template = ref('classic_ktv')
// 均摊估计模式：开启后额外产出一份每行字级均摊时间轴的 player/ASS/JSON
const evenSpread = ref(false)
const nextPreview = ref(true)
const start = ref<number | null>(null)
const duration = ref<number | null>(null)
const height = ref(0)  // 0 = 原视频清晰度（默认）

// ---- 模型管理（lyrics_model_status / start_lyrics_model_download / delete_tool_model） ----
interface L2tModel {
  id: string
  name: string
  category: string
  license: string
  note: string
  approxSize: number
  complete: boolean
  problems: string[]
  cacheDir: string
  licenseShort: string
  source: string
  target: string
}
const l2tModels = ref<L2tModel[]>([])

// 模型仓库分类（类别 key → 中文名 + 排序）
const L2T_CATEGORY_LABELS: Record<string, string> = {
  asr: '🎙️ 语音识别',
  alignment: '🎯 歌词对齐',
  diarization: '👥 分声部',
  separation: '🎚️ 人声分离',
}
const groupedL2tModels = computed(() => {
  const order = ['asr', 'alignment', 'diarization', 'separation']
  const groups: Array<{ key: string; label: string; items: L2tModel[] }> = []
  for (const key of order) {
    const items = l2tModels.value
      .filter((m) => m.category === key)
      .sort((a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }))
    if (items.length) groups.push({ key, label: L2T_CATEGORY_LABELS[key], items })
  }
  return groups
})

// ---- 模型管理筛选工具栏（仿主界面模型库：搜索/分类/来源/排序/仅显示已下载） ----
const l2tSearch = ref('')
const l2tCategory = ref('all')
const l2tSourceFilter = ref('all')
const l2tSort = ref<'default' | 'name' | 'size'>('default')
const l2tDownloadedOnly = ref(false)
// 视图模式：list=行式 / card=卡片网格（与主界面模型库一致，持久化）
const l2tViewMode = ref<'list' | 'card'>(
  (localStorage.getItem('lyrics.l2tViewMode') as 'list' | 'card') || 'list')
watch(l2tViewMode, (v) => { try { localStorage.setItem('lyrics.l2tViewMode', v) } catch { /* 忽略 */ } })
const l2tCategoryOptions = [
  { label: t('lyrics.l2tCatAll'), value: 'all' },
  ...Object.entries(L2T_CATEGORY_LABELS).map(([k, label]) => ({ label: String(label), value: k })),
]
const l2tSourceOptions = computed(() => [
  { label: t('lyrics.l2tSourceAll'), value: 'all' },
  ...[...new Set(l2tModels.value.map((m) => m.source))].filter(Boolean).map((s) => ({ label: String(s), value: String(s) })),
])
const l2tSortOptions = [
  { label: t('lyrics.l2tSortDefault'), value: 'default' },
  { label: t('lyrics.l2tSortName'), value: 'name' },
  { label: t('lyrics.l2tSortSize'), value: 'size' },
]
const filteredL2tModels = computed(() => {
  const q = l2tSearch.value.trim().toLowerCase()
  let list = l2tModels.value.filter((m) => {
    if (l2tCategory.value !== 'all' && m.category !== l2tCategory.value) return false
    if (l2tSourceFilter.value !== 'all' && m.source !== l2tSourceFilter.value) return false
    if (l2tDownloadedOnly.value && !m.complete) return false
    if (q) {
      const hay = `${m.name} ${m.note || ''} ${m.id}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
  if (l2tSort.value === 'name') list = [...list].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  else if (l2tSort.value === 'size') list = [...list].sort((a, b) => (b.approxSize || 0) - (a.approxSize || 0))
  return list
})
const groupedFilteredL2t = computed(() => {
  const list = filteredL2tModels.value
  // 按名称/大小排序时展示为单一平铺列表；默认排序按分类分组
  if (l2tSort.value !== 'default') return [{ key: 'flat', label: '', items: list }]
  const order = ['asr', 'alignment', 'diarization', 'separation']
  const groups: Array<{ key: string; label: string; items: L2tModel[] }> = []
  for (const key of order) {
    const items = list
      .filter((m) => m.category === key)
      .sort((a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }))
    if (items.length) groups.push({ key, label: L2T_CATEGORY_LABELS[key], items })
  }
  return groups
})
const l2tRoot = ref('')
const l2tBusy = ref(false)
const l2tDownloads = ref<Record<string, { message: string; status: string; percent: number }>>({})

// 正在下载中的模型 target 目录集合：共享同一目录的模型（如 Ultra/Ultra+Head）
// 不能同时下载——会锁同一个 .part 文件（WinError 32）
const downloadingTargets = computed(() => {
  const set = new Set<string>()
  for (const [id, st] of Object.entries(l2tDownloads.value)) {
    if (st?.status === 'downloading') {
      const t = l2tModels.value.find((m) => m.id === id)?.target
      if (t) set.add(t)
    }
  }
  return set
})

// 各模型手动选择的下载源（'' = 清单默认优先级；与设置页全局下载源互不影响）
const l2tSource = ref<Record<string, string>>({})
const DOWNLOAD_SOURCES = [
  { label: '默认', value: '' },
  { label: 'ModelScope 魔搭', value: 'modelscope' },
  { label: 'HuggingFace 官方', value: 'hf' },
  { label: 'HF-Mirror 镜像', value: 'hf-mirror' },
  { label: 'GitHub', value: 'github' },
]
// 各源的中文名（行内标签显示用）
const SOURCE_LABELS: Record<string, string> = {
  '': '默认', modelscope: 'ModelScope', hf: 'HF 官方', 'hf-mirror': 'HF-Mirror', github: 'GitHub',
}
const l2tTaskId = ref('')
// 进行中的下载任务：modelId → taskId（取消按钮用）
const l2tTaskIds = ref<Record<string, string>>({})
const settings = useSettingsStore()
interface LyricsModelStatus {
  id: string
  key: string
  name: string
  tool: string
  role: string
  language: string | null
  path: string
  complete: boolean
  sizeBytes: number
  expectedSizeBytes: number
  fileCount: number
  missingFiles: string[]
}
interface DownloadTaskState {
  taskId: string
  status: 'downloading' | 'error'
  percent: number
  message: string
  downloadedBytes: number
  totalBytes: number
  speed: number
  completedFiles: number
  totalFiles: number
  currentFile: string
  endpoint: string
  logs: string[]
  errorText: string
}

function emptyTask(taskId: string): DownloadTaskState {
  return {
    taskId,
    status: 'downloading',
    percent: 0,
    message: '',
    downloadedBytes: 0,
    totalBytes: 0,
    speed: 0,
    completedFiles: 0,
    totalFiles: 0,
    currentFile: '',
    endpoint: '',
    logs: [],
    errorText: '',
  }
}

const modelStatuses = ref<LyricsModelStatus[]>([])
const downloadTasks = ref<Record<string, DownloadTaskState>>({})
const deleting = ref<Record<string, boolean>>({})
const modelDir = ref('')
const detailModelId = ref<string>('')
const detailShow = computed({
  get: () => !!detailModelId.value && !!downloadTasks.value[detailModelId.value],
  set: (value: boolean) => { if (!value) detailModelId.value = '' },
})

const modelCached = computed<Record<string, boolean>>(() => {
  const out: Record<string, boolean> = {}
  for (const m of modelStatuses.value) {
    const mm = /^faster-whisper-(.+)$/.exec(m.key)
    if (mm) out[mm[1]] = m.complete
  }
  return out
})
// 对齐模型行（whisperx 强制对齐必需，lrc-ttml 清单没有，单独管理）
const alignModelRows = computed(() =>
  modelStatuses.value.filter((m) => m.key.startsWith('align')),
)

const modelOptions = computed(() => {
  // 与模型仓库「语音识别」分类完全一致：Whisper×3 + Qwen3-ASR-1.7B
  // 与模型仓库「语音识别」分组逐项对应，下载状态自动从模型管理匹配
  const asrEntries: Array<[string, string]> = [
    ['whisper-small', 'Whisper small'],
    ['whisper-medium', 'Whisper medium'],
    ['whisper-large-v3', 'Whisper large-v3'],
    ['qwen3-asr', 'Qwen3-ASR-1.7B'],
  ]
  const opts = asrEntries.map(([id, name]) => {
    const ok = !!l2tModels.value.find((m) => m.id === id)?.complete
    return { label: ok ? name : `${name} · 未下载`, value: id === 'qwen3-asr' ? 'qwen3-asr' : name.split(' ')[1] }
  })
  return opts
})

// ---- lrc-ttml 模型仓库（8 模型：whisper 大模型 / CAM++ 分声部 / Demucs 分离 / TIFA） ----
// l2tBusy 看门狗：状态/删除任务的事件万一丢失，按钮会永久灰掉——25s 后强制解锁
let l2tBusyWatchdog: ReturnType<typeof setTimeout> | null = null
function armL2tWatchdog() {
  if (l2tBusyWatchdog) clearTimeout(l2tBusyWatchdog)
  l2tBusyWatchdog = setTimeout(() => { l2tBusy.value = false; l2tBusyWatchdog = null }, 25_000)
}
function clearL2tWatchdog() {
  if (l2tBusyWatchdog) { clearTimeout(l2tBusyWatchdog); l2tBusyWatchdog = null }
}
async function refreshL2t() {
  l2tBusy.value = true
  armL2tWatchdog()
  const tid = `lyr_l2t_s_${Date.now()}`
  l2tTaskId.value = tid
  await ensureListener()
  try {
    await invoke('start_lyrics', { payload: { taskId: tid, action: 'l2t_status', modelDir: settings.modelDir || null } })
  } catch (e) {
    l2tBusy.value = false
    clearL2tWatchdog()
    message.error(String(e))
  }
}

function l2tDownload(m: L2tModel) {
  const tid = `lyr_l2t_d_${m.id}_${Date.now()}`
  l2tTaskId.value = tid
  l2tTaskIds.value = { ...l2tTaskIds.value, [m.id]: tid }
  l2tDownloads.value = { ...l2tDownloads.value, [m.id]: { message: '开始下载…', status: 'downloading', percent: 0 } }
  ensureListener().then(() =>
    invoke('start_lyrics', {
      payload: { taskId: tid, action: 'l2t_download', modelId: m.id,
                 modelDir: settings.modelDir || null, source: l2tSource.value[m.id] || null },
    }).catch((e) => {
      l2tDownloads.value = { ...l2tDownloads.value, [m.id]: { message: String(e), status: 'error', percent: 0 } }
    }),
  )
}

async function l2tCancel(m: L2tModel) {
  const tid = l2tTaskIds.value[m.id]
  try {
    if (tid) {
      await invoke('cancel_task', { taskId: tid })
    } else {
      // 页面刷新后前端丢失 taskId：按模型 id 前缀在后台任务表查找并终止
      const n = await invoke<number>('cancel_model_download', { modelId: m.id })
      if (!n) {
        message.info('没有找到该模型正在进行的下载任务')
        delete l2tTaskIds.value[m.id]
        l2tTaskIds.value = { ...l2tTaskIds.value }
        l2tDownloads.value = { ...l2tDownloads.value, [m.id]: { message: '', status: 'cancelled', percent: 0 } }
        void refreshL2t()
        return
      }
    }
  } catch (e) {
    message.error(String(e))
    return
  }
  delete l2tTaskIds.value[m.id]
  l2tTaskIds.value = { ...l2tTaskIds.value }
  l2tDownloads.value = { ...l2tDownloads.value, [m.id]: { message: '已取消下载（可重新开始）', status: 'cancelled', percent: 0 } }
  void refreshL2t()
}

async function l2tDelete(m: L2tModel) {
  l2tBusy.value = true
  armL2tWatchdog()
  try {
    await invoke('start_lyrics', {
      payload: { taskId: `lyr_l2t_x_${Date.now()}`, action: 'l2t_delete', modelId: m.id, modelDir: settings.modelDir || null },
    })
  } catch (e) {
    message.error(String(e))
  }
  await refreshL2t()
}

async function refreshModelStatus() {
  try {
    const res = await invoke<{ modelDir: string; models: LyricsModelStatus[] }>(
      'lyrics_model_status', { payload: { modelDir: settings.modelDir || null } })
    modelStatuses.value = res.models || []
    modelDir.value = res.modelDir || ''
  } catch { /* 探测失败则全部标注需下载 */ }
}

function findDownloadModelByTask(tid: string): string | null {
  for (const [id, task] of Object.entries(downloadTasks.value)) {
    if (task.taskId === tid) return id
  }
  return null
}

async function downloadModel(item: LyricsModelStatus) {
  const existing = downloadTasks.value[item.id]
  if (existing && existing.status === 'downloading') return
  try {
    const res = await invoke<{ taskId: string; started: boolean }>('start_lyrics_model_download', {
      payload: { model: item.key, modelDir: settings.modelDir || null },
    })
    // 重试时保留原日志便于排查，其余状态重置
    downloadTasks.value = {
      ...downloadTasks.value,
      [item.id]: { ...emptyTask(res.taskId), logs: existing?.logs ?? [] },
    }
    detailModelId.value = item.id
  } catch (e) {
    message.error(String(e))
  }
}

async function cancelDownload(item: LyricsModelStatus) {
  const task = downloadTasks.value[item.id]
  if (!task) return
  try { await invoke<boolean>('cancel_task', { taskId: task.taskId }) } catch { /* 忽略 */ }
  const next = { ...downloadTasks.value }
  delete next[item.id]
  downloadTasks.value = next
}

async function deleteModel(item: LyricsModelStatus) {
  deleting.value = { ...deleting.value, [item.id]: true }
  try {
    await invoke('delete_tool_model', {
      payload: { id: item.id, tool: 'lyrics', modelDir: settings.modelDir || null },
    })
    message.success(t('lyrics.modelDeleted'))
    await refreshModelStatus()
  } catch (e) {
    message.error(String(e))
  } finally {
    const next = { ...deleting.value }
    delete next[item.id]
    deleting.value = next
  }
}

// 应用重启 vs 应用内切页的区分标记（sessionStorage 随进程消失）
const SESSION_ALIVE_KEY = 'lyrics.sessionAlive'

onMounted(async () => {
  const freshStart = !sessionStorage.getItem(SESSION_ALIVE_KEY)
  await loadForm()               // 先恢复表单（文件/歌词/参数）
  await ensureListener()
  await refreshModelStatus()
  await refreshL2t()
  void ensureSeparationModels()  // 分离模型下拉（主应用已下载模型）
  await resumeRunningJob()       // 离开页面后回来：恢复进行中任务的监听/状态
  if (freshStart) {
    // 应用重启：结果区保持干净——把恢复阈值推进到当前时刻，
    // 重启前完成的任务（含队列里的旧任务）切页回来也不再自动加载
    markResultsCleared()
  } else {
    resumeFinishedJob()          // 应用内切页回来：已完成任务的产物展示照常恢复
  }
  sessionStorage.setItem(SESSION_ALIVE_KEY, '1')
  // 全页拖拽导入（复刻分离页 onDragDropEvent 模式）
  try {
    unlistenDragDrop = await getCurrentWebview().onDragDropEvent((event) => {
      const type = event.payload.type
      if (type === 'over' || type === 'enter') {
        isDraggingFile.value = true
        const pos = (event.payload as { position?: { x: number; y: number } }).position
        if (pos) hitTestDragTarget(pos.x, pos.y)
      } else if (type === 'drop') {
        isDraggingFile.value = false
        const paths = (event.payload as { paths?: string[] }).paths || []
        if (paths.length) handleDroppedPaths(paths)
        dragTarget.value = null
      } else {
        isDraggingFile.value = false
        dragTarget.value = null
      }
    })
  } catch { /* 非 Tauri 环境静默降级 */ }
})

// ---- 已完成任务的产物展示恢复（切页重载后 lyrics/outputs 被重置） ----
// 清空时间点（localStorage 持久，跨应用重启）：resume 只恢复「清空之后」
// 完成的新任务——队列里更早的任务一律不再复活
const CLEARED_AT_KEY = 'lyrics.resultsClearedAt'
function resultsClearedAt(): number {
  return Number(localStorage.getItem(CLEARED_AT_KEY) || 0)
}
function markResultsCleared() {
  try { localStorage.setItem(CLEARED_AT_KEY, String(Date.now())) } catch { /* 忽略 */ }
}

function resumeFinishedJob() {
  if (lyrics.value || Object.keys(outputs.value).length) return  // 已有展示不覆盖
  const clearedAt = resultsClearedAt()
  const job = [...lyricsJobs.value]
    .sort((a, b) => b.time - a.time)
    .find((j) => j.status === 'done' && j.time > clearedAt
                 && (j.outputs.lyricsJson || j.outputs.lrc))
  if (!job) return
  taskId.value = job.id
  outputs.value = { ...job.outputs } as Record<string, string | null>
  percent.value = 100
  const jsonPath = job.outputs.lyricsJson
  if (jsonPath) {
    void (async () => {
      try {
        lyrics.value = JSON.parse(await (await fetch(convertFileSrc(jsonPath))).text())
        pushLog('[恢复] 已载入最近完成的打轴结果：' + job.input.split(/[\\/]/).pop())
      } catch { /* 读取失败仅恢复产物列表 */ }
    })()
  }
}

// ---- 表单状态：sessionStorage（应用内切页保留，应用重启后自动清空） ----
const FORM_KEY = 'lyrics.form.session'
// 工作台处理方式：单曲 / 整目录（目录模式见下方批量段）
const workMode = ref<'single' | 'dir'>('single')
const batchMediaDir = ref('')
const batchLyricsDir = ref('')
const batchVocalsDir = ref('')
const formRefs = {
  input, output, modelSize, language, lyricsSource, neteaseUrl,
  importedLyrics, importedLyricsPath, engine, useVocals, vocalsPath,
  selectedSeparationModel, dragOverlap, layeredLyrics, tailExtend,
  autoSupplement, template, nextPreview, start, duration, height, evenSpread,
  sepOutputDir, sepFormat, sepLayout, sepNamingEnabled, sepNamingTemplate,
  sepStemVocals, sepStemInstrumental, sepUseTta,
  sepMp3Bitrate, sepNormalize, sepStandardize,
  sepBatchSize, sepOverlapSize, sepNumOverlap, sepChunkSize, sepStemOrder,
  workMode, batchMediaDir, batchLyricsDir, batchVocalsDir,
}
type FormShape = Record<string, unknown>
function formSnapshot(): FormShape {
  const data: FormShape = {}
  for (const [k, r] of Object.entries(formRefs)) data[k] = r.value
  return data
}
let formLoaded = false
function saveForm() {
  if (!formLoaded) return  // 恢复完成前不写入，避免默认值覆盖存档
  try {
    sessionStorage.setItem(FORM_KEY, JSON.stringify(formSnapshot()))
  } catch { /* 忽略 */ }
}
function loadForm() {
  try {
    const data = JSON.parse(sessionStorage.getItem(FORM_KEY) || '{}')
    if (data && typeof data === 'object') {
      for (const [k, r] of Object.entries(formRefs)) {
        if (data[k] !== undefined) (r as { value: unknown }).value = data[k]
      }
    }
  } catch { /* 损坏数据：忽略 */ }
  // 历史会话清洗：引擎/识别模型的空字符串是旧版默认值残留，
  // n-select 视其为「已选值」导致占位符不显示，统一归 null
  if ((engine.value as unknown) === '') engine.value = null
  if ((modelSize.value as unknown) === '') modelSize.value = null
  formLoaded = true
}
watch(Object.values(formRefs), saveForm, { deep: true })

// ---- 离开页面期间任务照跑，回来时恢复监听与展示 ----
async function resumeRunningJob() {
  const job = lyricsJobs.value.find((j) => j.status === 'running')
  if (!job) return
  taskId.value = job.id
  running.value = true
  percent.value = job.percent
  stage.value = job.stage || 'running'
  stageMessage.value = job.stage || '任务进行中（已恢复监听）'
  await ensureListener()  // 重新挂事件监听（组件卸载时会自动解除）
  // 离开期间任务可能已完成：产物探测（running 时 outputs 尚未写入，
  // 从输入文件推断默认输出：<目录>/<文件名>.lrc）
  const sep = job.input.includes('\\') ? '\\' : '/'
  const dir = job.input.replace(/[\\/][^\\/]+$/, '')
  const stem = (job.input.split(/[\\/]/).pop() || '').replace(/\.[^.]+$/, '')
  const probe = job.outputs.lrc || job.outputs.lyricsJson || (stem ? `${dir}${sep}${stem}.lrc` : '')
  if (!probe) return

  // 分离串联卡住续接：分离完成事件在切页重载时丢失，没人继续 pipeline。
  // 探测分离产物（flat 布局 <stem>_Vocals.wav）→ 已存在则直接续接打轴；
  // 不存在则重新发起分离（分离 worker 对已有产物自动跳过，代价小）。
  if (job.stage === 'separating' && job.model.startsWith('分离串联 · ')) {
    // 分离产物新位置：<输入目录>/<歌名>/separated（产物文件夹内）；旧位置 <输入目录>/separated 兼容探测
    const sepDirs = [`${dir}${sep}${stem}${sep}separated`, `${dir}${sep}separated`]
    let vocals = ''
    outer: for (const sepDir of sepDirs) {
      for (const cand of [`${sepDir}${sep}${stem}_Vocals.wav`, `${sepDir}${sep}${stem}_vocals.wav`]) {
        try {
          const head = await fetch(convertFileSrc(cand), { method: 'GET' })
          if (head.ok) { vocals = cand; break outer }
        } catch { /* 下一个候选 */ }
      }
    }
    if (vocals) {
      vocalsPath.value = vocals
      useVocals.value = true
      lyricsSource.value = job.source === 'text' ? 'text' : 'lrc'
      patchJob(job.id, { stage: 'vocals_ready', percent: 10 })
      stage.value = 'vocals_ready'
      stageMessage.value = '人声分离已完成（离开期间），正在续接打轴…'
      pushLog('[恢复] 人声分离产物已存在，续接歌词流水线')
      if (importedLyrics.value) {
        await startTask()  // useVocals && vocalsPath → 直接进 pipeline
      } else {
        message.info('人声分离已完成：请重新选择歌词文件后点击「生成词轴对齐」，分离会自动跳过')
        running.value = false
      }
    } else {
      message.info('人声分离在离开页面期间中断，正在重新启动分离（已有产物会自动跳过）…')
      const model = job.model.slice('分离串联 · '.length)
      taskId.value = job.id
      const sepTaskId = `lyr_sep_${Date.now()}`
      separating.value = true
      await ensureListener()
      try {
        await invoke('start_separation', {
          payload: {
            taskId: sepTaskId,
            model,
            input: job.input,
            output: `${dir}${sep}${stem}${sep}separated`,
            outputLayout: 'flat',
            outputFormat: 'wav',
            selectedStems: ['vocals'],
            modelDir: settings.modelDir || null,
            download: false,
          },
        })
      } catch (e) {
        separating.value = false
        running.value = false
        patchJob(job.id, { status: 'failed', error: String(e) })
        message.error(String(e))
      }
    }
    return  // 分离串联分支处理完毕，跳过通用产物探测
  }
  try {
    const resp = await fetch(convertFileSrc(probe))
    if (!resp.ok) return
    if (resp.headers.get('content-length') === '0') return
    // 产物已存在 → 任务已完成：读取结果并恢复展示
    patchJob(job.id, { status: 'done', percent: 100, stage: 'done' })
    running.value = false
    percent.value = 100
    stage.value = 'done'
    stageMessage.value = t('common.success', '完成')
    pushLog('[恢复] 任务在离开期间已完成，已恢复产物展示')
    const jsonPath = job.outputs.lyricsJson
    if (jsonPath) {
      try {
        const text = await (await fetch(convertFileSrc(jsonPath))).text()
        lyrics.value = JSON.parse(text)
      } catch { /* JSON 读取失败不影响恢复 */ }
    }
  } catch { /* 文件未生成 → 任务仍在跑，保持监听 */ }
  // 看门狗：真重启（应用退出）时 worker 已死、事件永不再来——7 分钟无任何
  // 阶段/进度更新则判定中断（分离/对齐阶段均有周期事件，不会误伤）
  setTimeout(() => {
    if (running.value && taskId.value === job.id && stage.value === job.stage) {
      patchJob(job.id, { status: 'cancelled' })
      running.value = false
      stageMessage.value = '任务可能已中断（长时间无进展）——产物可在输出目录查看'
      pushLog('[看门狗] 长时间无进展，判定任务已中断')
      message.warning(t('lyrics.jobInterrupted'))
    }
  }, 420_000)
}
const languageOptions = computed(() => [
  { label: t('lyrics.languageAuto'), value: 'auto' },
  { label: '中文 (zh)', value: 'zh' },
  { label: 'English (en)', value: 'en' },
  { label: '多语言混合 (multi)', value: 'multi' },
])
const templateOptions = [
  'classic_ktv', 'apple_music', 'simple_white',
  'gradient_highlight', 'two_line', 'single_line',
].map((v) => ({ label: v, value: v }))

async function pickInput() {
  try {
    const files = await invoke<string[]>('pick_media_files')
    if (files && files.length) input.value = files[0]
  } catch { /* 用户取消 */ }
}

async function pickOutputDir() {
  try {
    const dir = await invoke<string | null>('pick_output_folder')
    if (dir) output.value = dir
  } catch { /* 用户取消 */ }
}

async function pickVocals() {
  try {
    const files = await invoke<string[]>('pick_audio_files')
    if (files && files.length) vocalsPath.value = files[0]
  } catch { /* 用户取消 */ }
}

// ---- 目录批量模式：音视频与歌词按同名配对，逐个串行跑流水线 ----
const batchScan = ref<{
  matched: Array<{ media: string; lyrics: string | null; format: string }>
  unmatched_media: string[]
  unmatched_lyrics: string[]
} | null>(null)
const batchScanning = ref(false)
const scanLogLines = ref<string[]>([])
const scanLogOpen = ref(true)
const batchQueue = ref<Array<{ media: string; lyrics: string | null; format: string }>>([])
const batchRunning = ref(false)
const batchCancelled = ref(false)
const batchDone = ref(0)
const batchFailed = ref(0)
const batchCurrent = ref<{ media: string; lyrics: string | null; format: string } | null>(null)
const batchCurrentText = ref('')
const batchSepTaskId = ref('')

async function pickBatchDir(which: 'media' | 'lyrics') {
  try {
    const dir = await invoke<string | null>('pick_input_folder')
    if (!dir) return
    if (which === 'media') batchMediaDir.value = dir
    else batchLyricsDir.value = dir
  } catch { /* 用户取消 */ }
}

function clearBatchDir(which: 'media' | 'lyrics') {
  if (which === 'media') batchMediaDir.value = ''
  else batchLyricsDir.value = ''
  // A previous pairing belongs to the directory that was just cleared.
  batchScan.value = null
  scanLogLines.value = []
}

async function scanBatchDir() {
  if (!batchMediaDir.value.trim()) return
  batchScanning.value = true
  try {
    const kind = lyricsSource.value
    batchScan.value = await invoke('scan_lyrics_dir', {
      mediaDir: batchMediaDir.value.trim(),
      lyricsDir: kind === 'none' ? null : (batchLyricsDir.value.trim() || batchMediaDir.value.trim()),
      kind,
    })
    // 扫描过程与结果写独立日志（每次扫描清空重写，不与任务日志混叠）
    const scan = batchScan.value
    if (!scan) return
    const stamp = () => new Date().toTimeString().slice(0, 8)
    const lines: string[] = []
    lines.push(`[${stamp()}] 目录模式（${kind === 'lrc' ? 'LRC' : kind === 'text' ? '纯文本' : '无歌词'}）`)
    lines.push(`[${stamp()}] 匹配 ${scan.matched.length} 对；未匹配音视频 ${scan.unmatched_media.length} 个；未匹配歌词 ${scan.unmatched_lyrics.length} 个`)
    for (const pair of scan.matched) {
      const name = pair.media.split(/[\\/]/).pop() || pair.media
      lines.push(`[${stamp()}] ✓ ${name}${pair.lyrics ? ' ↔ ' + (pair.lyrics.split(/[\\/]/).pop() || '') : '（无歌词模式）'}`)
    }
    for (const m of scan.unmatched_media) {
      lines.push(`[${stamp()}] ✗ 音视频无同名歌词：${m.split(/[\\/]/).pop() || m}`)
    }
    for (const l of scan.unmatched_lyrics) {
      lines.push(`[${stamp()}] ✗ 歌词无同名音视频：${l.split(/[\\/]/).pop() || l}`)
    }
    scanLogLines.value = lines
  } catch (e) {
    batchScan.value = null
    message.error(String(e))
  } finally {
    batchScanning.value = false
  }
}

const batchSummary = computed(() => {
  const scan = batchScan.value
  if (!scan) return ''
  return t('lyrics.batchScanSummary', {
    n: scan.matched.length,
    m: scan.unmatched_media.length,
    l: scan.unmatched_lyrics.length,
  })
})

// 取当前目录模式的任务参数（不改表单 refs）
function batchPayload(item: { media: string; lyrics: string | null; format: string }, tid: string, lyricsText: string, vocalsPath: string | null = null) {
  return {
    taskId: tid,
    action: 'pipeline',
    input: item.media,
    output: output.value?.trim() || null,  // 留空 = 每首歌默认产物文件夹 <输入目录>/<歌名>/
    modelSize: modelSize.value,
    language: language.value === 'auto' ? null : language.value,
    device: settings.getRuntimeDeviceConfig(appStore.envInfo).device || 'auto',
    deviceIds: settings.getRuntimeDeviceConfig(appStore.envInfo).deviceIds || [],
    lyricsSource: item.format === 'none' ? 'asr' : 'import',
    lyricsMode: item.format,
    lyricsText,
    sourceLyricsPath: item.lyrics,
    burnVideo: false,
    lyricsFormat: null,
    template: template.value,
    nextPreview: nextPreview.value,
    start: start.value,
    duration: duration.value,
    height: height.value,
    modelDir: settings.modelDir || null,
    engine: engine.value,
    tifaPython: tifaPython.value || null,
    useVocals: !!vocalsPath,
    vocalsPath,
    tailExtendMs: tailExtend.value ? 400 : 0,
    dragOverlap: dragOverlap.value,
    layeredLyrics: layeredLyrics.value,
    autoHarmony: autoSupplement.value,
    evenSpread: evenSpread.value,
  }
}

function batchSettled(success: boolean) {
  if (!batchRunning.value) return
  if (success) batchDone.value += 1
  else batchFailed.value += 1
  if (batchCancelled.value || (!batchQueue.value.length)) {
    batchRunning.value = false
    running.value = false
    if (batchCancelled.value) {
      pushLog(`[批量] 已取消：完成 ${batchDone.value} · 失败 ${batchFailed.value}`)
      message.info(t('lyrics.batchCancelled', { d: batchDone.value, f: batchFailed.value }))
    } else {
      pushLog(`[批量] 全部完成：成功 ${batchDone.value} · 失败 ${batchFailed.value}`)
      message.success(t('lyrics.batchFinished', { d: batchDone.value, f: batchFailed.value }))
    }
    return
  }
  void runNextBatchItem()
}

// 批量当前项：启动歌词流水线（vocals = 分离出的人声轨，null = 用原曲）
async function launchBatchPipeline(vocals: string | null) {
  const item = batchCurrent.value
  if (!item) return
  batchSepTaskId.value = ''
  const tid = `lyr_${Date.now()}`
  taskId.value = tid
  const name = item.media.split(/[\\/]/).pop() || item.media
  upsertJob({
    id: tid, time: Date.now(), input: item.media,
    model: `目录批量 · ${item.format === 'lrc' ? 'LRC' : item.format === 'text' ? 'TXT' : '无歌词'}${vocals ? ' · 分离串联' : ''}`,
    source: item.format as 'lrc' | 'text', status: 'running', percent: 0,
    stage: 'validating_input', outputs: {}, error: '',
  })
  try {
    await invoke('start_lyrics', { payload: batchPayload(item, tid, batchCurrentText.value, vocals) })
  } catch (e) {
    patchJob(tid, { status: 'failed', error: String(e) })
    message.error(String(e))
    batchSettled(false)
  }
}

async function runNextBatchItem() {
  const item = batchQueue.value[0]
  if (!item) {
    batchRunning.value = false
    running.value = false
    return
  }
  batchQueue.value = batchQueue.value.slice(1)
  batchCurrent.value = item
  const name = item.media.split(/[\\/]/).pop() || item.media
  stageMessage.value = t('lyrics.batchItemProgress', {
    done: batchDone.value + batchFailed.value + 1, total: batchDone.value + batchFailed.value + batchQueue.value.length + 1,
  })
  pushLog(`[批量] (${batchDone.value + batchFailed.value + 1}/${batchDone.value + batchFailed.value + batchQueue.value.length + 1}) ${name}`)
  // 与单曲模式同源：歌词文本由前端读出后随 payload 传给 worker
  let lyricsText = ''
  if (item.format !== 'none' && item.lyrics) {
    lyricsText = await (await fetch(convertFileSrc(item.lyrics))).text()
    if (!lyricsText.trim()) {
      message.warning(`${name} 歌词文件为空，已跳过`)
      pushLog(`[批量] ✗ ${name}：歌词文件为空，已跳过`)
      batchSettled(false)
      return
    }
  }
  batchCurrentText.value = lyricsText

  // 人声分离串联：先查复用（各自产物文件夹 separated/ 已有人声轨则跳过分离）
  if (useVocals.value && selectedSeparationModel.value) {
    const reused = await reuseExistingVocals(item.media, batchVocalsDir.value)
    if (reused) {
      pushLog(`[批量] 复用已有人声（${batchVocalsDir.value.trim() ? '指定目录' : '产物文件夹'}）：${reused.split(/[\\/]/).pop() || reused}`)
      await launchBatchPipeline(reused)
      return
    }
  }
  if (useVocals.value && selectedSeparationModel.value) {
    separating.value = true
    running.value = true
    await ensureListener()
    const stem = name.replace(/\.[^.]+$/, '')
    const dirOf = item.media.replace(/[\\/][^\\/]+$/, '')
    const sepTaskId = `lyr_sep_${Date.now()}`
    batchSepTaskId.value = sepTaskId
    pushLog(`[批量] 分离人声（${selectedSeparationModel.value}）→ ${stem}/separated`)
    try {
      await invoke('start_separation', {
        payload: {
          taskId: sepTaskId,
          model: selectedSeparationModel.value,
          input: item.media,
          output: `${dirOf}/${stem}/separated`,
          outputLayout: sepLayout.value,
          outputFormat: sepFormat.value,
          selectedStems: sepSelectedStems.value,
          outputNaming: sepNamingConfig.value,
          useTta: sepUseTta.value,
          inferenceParams: sepInferenceParams.value,
          audioParams: sepFormat.value === 'mp3' ? { mp3_bit_rate: sepMp3Bitrate.value } : undefined,
          modelDir: settings.modelDir || null,
          download: false,
        },
      })
    } catch (e) {
      separating.value = false
      message.error(String(e))
      batchSettled(false)
    }
    return
  }
  await launchBatchPipeline(null)
}

function startBatchTask() {
  const scan = batchScan.value
  if (!scan || !scan.matched.length) {
    message.warning(t('lyrics.batchNoMatches'))
    return
  }
  if (!engine.value) {
    message.warning(t('lyrics.selectEngineFirst'))
    return
  }
  errorText.value = ''
  errorCode.value = ''
  lyrics.value = null
  outputs.value = {}
  percent.value = 0
  logLines.value = []
  lastProgressLog.value = ''
  logOpen.value = false
  stage.value = 'validating_input'
  batchQueue.value = [...scan.matched]
  batchDone.value = 0
  batchFailed.value = 0
  batchCancelled.value = false
  batchRunning.value = true
  running.value = true
  pushLog(`[批量] 共 ${scan.matched.length} 对（未匹配音视频 ${scan.unmatched_media.length} 个、歌词 ${scan.unmatched_lyrics.length} 个已跳过）`)
  void runNextBatchItem()
}

// ---- 分离产物复用：<产物>/separated/ 已有人声轨则跳过重新分离 ----
async function reuseExistingVocals(mediaPath: string, searchDir?: string): Promise<string | null> {
  try {
    return await invoke<string | null>('find_existing_vocals', {
      mediaPath,
      searchDir: searchDir?.trim() ? searchDir.trim() : null,
    })
  } catch {
    return null
  }
}

// ---- 文件拖拽导入（复刻分离页）：音频/视频 → 输入文件；LRC/TXT → 歌词 ----
const isDraggingFile = ref(false)
const dragTarget = ref<'input' | 'lyrics' | null>(null)
const inputFieldEl = ref<{ $el: HTMLElement } | null>(null)
const lyricsFieldEl = ref<HTMLElement | null>(null)
let unlistenDragDrop: UnlistenFn | null = null
function hitTestDragTarget(physicalX: number, physicalY: number) {
  // Tauri 坐标为物理像素，getBoundingClientRect 为逻辑像素
  const scale = window.devicePixelRatio || 1
  const lx = physicalX / scale
  const ly = physicalY / scale
  const inputEl = (inputFieldEl.value?.$el ?? inputFieldEl.value) as HTMLElement | null
  const zones: Array<['input' | 'lyrics', HTMLElement | null]> = [
    ['input', inputEl],
    ['lyrics', lyricsFieldEl.value],
  ]
  dragTarget.value = null
  for (const [name, el] of zones) {
    if (!el) continue
    const r = el.getBoundingClientRect()
    if (lx >= r.left && lx <= r.right && ly >= r.top && ly <= r.top + r.height + 8) {
      dragTarget.value = name
      break
    }
  }
}
const DRAG_AUDIO_EXTS = ['wav', 'mp3', 'flac', 'm4a', 'aac', 'ogg', 'opus', 'mp4', 'mkv', 'mov', 'avi', 'webm', 'flv']

async function loadLyricsFromPath(path: string) {
  try {
    const text = await (await fetch(convertFileSrc(path))).text()
    if (!text.trim()) {
      message.warning(t('lyrics.noLyricsFile'))
      return
    }
    // .lrc → LRC；.txt → 纯文本；其它扩展名（经「所有文件」选择）跟随当前模式的预期格式
    const ext = path.toLowerCase().split('.').pop()
    const format: 'lrc' | 'text' = ext === 'lrc' ? 'lrc'
      : ext === 'txt' ? 'text'
      : (lyricsSource.value === 'lrc' ? 'lrc' : 'text')
    importedLyrics.value = { name: path.split(/[\\/]/).pop() || path, text, format }
    importedLyricsPath.value = path
  } catch (e) {
    message.error(String(e))
  }
}

function handleDroppedPaths(paths: string[]) {
  let audioCount = 0
  let lyricsCount = 0
  for (const raw of paths) {
    const path = String(raw || '')
    const ext = path.split('.').pop()?.toLowerCase() || ''
    if (ext === 'lrc' || ext === 'txt') {
      lyricsCount += 1
      void loadLyricsFromPath(path)
    } else if (DRAG_AUDIO_EXTS.includes(ext)) {
      audioCount += 1
      if (!input.value) input.value = path  // 多个音频只取第一个，避免静默覆盖
    }
  }
  if (audioCount) message.success(t('lyrics.dragInputAdded'))
  else if (lyricsCount) message.success(t('lyrics.dragLyricsAdded'))
  else if (paths.length) message.warning(t('lyrics.dragUnsupported'))
}

// ---- 任务状态 ----
const taskId = ref('')
const running = ref(false)
const stage = ref('')
const stageMessage = ref('')
const percent = ref(0)
const lyrics = ref<LyricsJson | null>(null)
const outputs = ref<Record<string, string | null>>({})
const errorText = ref('')
const errorCode = ref('')
const ERROR_KEY_MAP: Record<string, string> = {
  INPUT_NOT_FOUND: 'lyrics.errors.INPUT_NOT_FOUND',
  OUTPUT_DIR_FAILED: 'lyrics.errors.OUTPUT_DIR_FAILED',
  LYRICS_INVALID: 'lyrics.errors.LYRICS_INVALID',
  ASR_FAILED: 'lyrics.errors.ASR_FAILED',
  COMPOSE_FAILED: 'lyrics.errors.COMPOSE_FAILED',
  LYRICS_MODEL_MISSING: 'lyrics.errors.LYRICS_MODEL_MISSING',
  LYRICS_FAILED: 'lyrics.errors.LYRICS_FAILED',
}
const errorTitle = computed(() => t(ERROR_KEY_MAP[errorCode.value] || 'lyrics.errors.LYRICS_FAILED'))
let unlisten: UnlistenFn | null = null
let listening = false
let disposed = false

async function ensureListener() {
  if (listening) return
  listening = true
  const fn = await listen<WorkerEvent>('pymss://worker-event', (ev) => {
    const msg = ev.payload
    if (!msg) return
    // 歌词模型下载事件（taskId 以 lyrics_model_ 开头）
    if (msg.taskId && msg.taskId.startsWith('lyrics_model_')) {
      const modelId = findDownloadModelByTask(msg.taskId)
      if (!modelId) return
      const dp = msg.payload || {}
      const prev = downloadTasks.value[modelId]
      const patch = (fields: Partial<DownloadTaskState>) => {
        if (!downloadTasks.value[modelId]) return
        downloadTasks.value = {
          ...downloadTasks.value,
          [modelId]: { ...downloadTasks.value[modelId]!, ...fields },
        }
      }
      switch (msg.type) {
        case 'download_started': {
          patch({
            endpoint: String(dp.endpoint ?? prev?.endpoint ?? ''),
            totalFiles: Number(dp.totalFiles ?? prev?.totalFiles ?? 0),
            message: t('lyrics.preparing'),
          })
          break
        }
        case 'download_stage': {
          patch({ message: String(dp.message ?? dp.stage ?? prev?.message ?? '') })
          break
        }
        case 'download_file': {
          patch({ currentFile: String(dp.path ?? '') })
          break
        }
        case 'download_progress': {
          patch({
            percent: Number(dp.progress ?? prev?.percent ?? 0),
            downloadedBytes: Number(dp.aggregateDownloadedBytes ?? prev?.downloadedBytes ?? 0),
            totalBytes: Number(dp.aggregateTotalBytes ?? prev?.totalBytes ?? 0),
            speed: Number(dp.speedBytesPerSecond ?? prev?.speed ?? 0),
            completedFiles: Number(dp.completedFiles ?? prev?.completedFiles ?? 0),
            totalFiles: Number(dp.totalFiles ?? prev?.totalFiles ?? 0),
          })
          break
        }
        case 'download_log': {
          const line = `[${String(dp.level ?? 'info').toUpperCase()}] ${String(dp.message ?? '')}`
          const logs = [...(prev?.logs ?? []), line]
          patch({ logs: logs.length > 300 ? logs.slice(-300) : logs })
          break
        }
        case 'download_done': {
          const next = { ...downloadTasks.value }
          delete next[modelId]
          downloadTasks.value = next
          if (detailModelId.value === modelId) detailModelId.value = ''
          message.success(t('lyrics.modelDownloaded'))
          void refreshModelStatus()
          break
        }
        case 'task_cancelled': {
          const next = { ...downloadTasks.value }
          delete next[modelId]
          downloadTasks.value = next
          break
        }
        case 'error': {
          const reason = String(dp.message ?? '')
          patch({ status: 'error', errorText: reason, message: t('lyrics.modelDownloadFailed') })
          message.error(`${t('lyrics.modelDownloadFailed')}: ${reason}`)
          break
        }
      }
      return
    }
    // lrc-ttml 模型仓库任务事件（taskId 通道：lyr_l2t_*）
    if (msg.taskId && msg.taskId.startsWith('lyr_l2t_')) {
      const p = msg.payload || {}
      if (msg.taskId.startsWith('lyr_l2t_s_') || msg.taskId.startsWith('lyr_l2t_x_')) {
        if (msg.type === 'task_done') {
          if (p.models) {
            const models = p.models as L2tModel[]
            l2tModels.value = models
            l2tRoot.value = String(p.root || '')
            message.success(t('lyrics.l2tVerified', {
              ready: models.filter((m) => m.complete).length,
              total: models.length,
            }))
          }
          l2tBusy.value = false
          clearL2tWatchdog()
        } else if (msg.type === 'error') {
          l2tBusy.value = false
          clearL2tWatchdog()
          message.error(String(p.message ?? ''))
        }
      } else if (msg.taskId.startsWith('lyr_l2t_d_')) {
        // 模型 id 不含下划线：taskId = lyr_l2t_d_<modelId>_<时间戳>
        const key = msg.taskId.replace(/^lyr_l2t_d_/, '').replace(/_\d+$/, '')
        if (msg.type === 'task_stage' && p.message) {
          const prev = l2tDownloads.value[key] || { message: '', status: 'downloading', percent: 0 }
          l2tDownloads.value = { ...l2tDownloads.value, [key]: { ...prev, message: String(p.message), status: 'downloading' } }
        } else if (msg.type === 'task_progress') {
          const prev = l2tDownloads.value[key] || { message: '', status: 'downloading', percent: 0 }
          l2tDownloads.value = {
            ...l2tDownloads.value,
            [key]: { ...prev, percent: Number(p.percent ?? 0), message: String(p.message || prev.message), status: 'downloading' },
          }
        } else if (msg.type === 'task_done') {
          l2tDownloads.value = { ...l2tDownloads.value, [key]: { message: t('lyrics.modelReady'), status: 'done', percent: 100 } }
          message.success(t('lyrics.l2tDownloadDone', { name: key }))
          void refreshL2t()
        } else if (msg.type === 'error') {
          l2tDownloads.value = { ...l2tDownloads.value, [key]: { message: String(p.message ?? ''), status: 'error', percent: 0 } }
          message.error(`${t('lyrics.modelDownloadFailed')}: ${p.message ?? ''}`)
        }
      }
      return
    }
    // 人声自动分离事件（主应用分离引擎；taskId 通道：lyr_sep_*）
    if (msg.taskId && msg.taskId.startsWith('lyr_sep_')) {
      const p = msg.payload || {}
      if (msg.type === 'task_stage' || msg.type === 'task_log') {
        if (p.message) pushLog(String(p.message))
        if (p.message) stageMessage.value = String(p.message)
      } else if (msg.type === 'task_progress') {
        percent.value = Math.min(49, Math.round(Number(p.progress ?? 0) * 0.5))
        if (p.message) stageMessage.value = String(p.message)
      } else if (msg.type === 'task_done') {
        separating.value = false
        const vocals = vocalsPathFromOutputs(p.outputs)
        if (batchRunning.value) {
          // 目录批量：分离完成 → 继续当前项的歌词流水线
          if (vocals) {
            pushLog(`[批量] 人声就绪：${vocals}`)
            void launchBatchPipeline(vocals)
          } else {
            message.error(t('lyrics.vocalsNotFound'))
            batchSettled(false)
          }
          return
        }
        if (vocals) {
          vocalsPath.value = vocals
          pushLog(`[人声就绪] ${vocals}`)
          message.success(t('lyrics.vocalsReady'))
          void startTask()  // 分离完成，继续歌词流水线
        } else {
          message.error(t('lyrics.vocalsNotFound'))
        }
      } else if (msg.type === 'error') {
        separating.value = false
        running.value = false
        pushLog(`[错误] ${p.message ?? ''}`)
        if (batchRunning.value) {
          message.error(`${t('lyrics.separationFailed')}: ${p.message ?? ''}`)
          batchSettled(false)
          return
        }
        message.error(`${t('lyrics.separationFailed')}: ${p.message ?? ''}`)
      }
      return
    }
    // 联网搜索任务事件（独立 taskId 通道：lyr_search_*）
    if (msg.taskId === searchTaskId.value && searchTaskId.value) {
      const p = msg.payload || {}
      if (msg.type === 'task_done') {
        searching.value = false
        fetchingLink.value = false
        if (Array.isArray(p.results)) {
          searchResults.value = p.results as SearchResult[]
          if (!p.results.length) message.info(t('lyrics.noResults'))
        } else if (p.lyricsText) {
          importedLyrics.value = {
            name: String(p.songName || '在线歌词'),
            text: String(p.lyricsText),
            format: 'lrc',
          }
          importedLyricsPath.value = String(p.savedPath || '')
          // 在线歌词是 LRC：切到 LRC 模式确保开始处理时生效
          lyricsSource.value = 'lrc'
          if (p.wordLevel) message.info(t('lyrics.platformWordLevel'))
          else message.success(t('lyrics.lyricsFetched'))
        }
      } else if (msg.type === 'error') {
        searching.value = false
        fetchingLink.value = false
        message.error(String(p.message ?? ''))
      }
      return
    }
    if (msg.taskId !== taskId.value) return
    const p = msg.payload || {}
    switch (msg.type) {
      case 'task_stage':
        stage.value = p.stage || ''
        stageMessage.value = p.message || ''
        patchJob(taskId.value, { stage: stage.value })
        if (p.message) pushLog(String(p.message))
        break
      case 'task_progress': {
        percent.value = Number(p.percent ?? 0)
        const pmsg = p.message ? String(p.message) : ''
        // 细分进度（如「歌词对齐中 12/53 行」）也进日志：连续重复才去重
        if (pmsg && pmsg !== lastProgressLog.value) {
          lastProgressLog.value = pmsg
          pushLog(pmsg)
        }
        if (pmsg) stageMessage.value = pmsg
        patchJob(taskId.value, { percent: Math.round(percent.value) })
        break
      }
      case 'lyrics_ready':
        lyrics.value = (p.lyrics as LyricsJson) ?? null
        pushLog('[歌词就绪] ' + String(p.path ?? ''))
        break
      case 'task_done':
        running.value = false
        percent.value = 100
        {
          const out: Record<string, string> = {}
          for (const [k, v] of Object.entries(p.outputs || {})) {
            if (typeof v === 'string' && v) out[k] = v
          }
          outputs.value = out
          pushLog('[完成] ' + ((p.files || []) as string[]).map((f) => f.split('\\').pop()).join('、'))
          patchJob(taskId.value, {
            status: 'done', percent: 100, outputs: out,
            preview: Array.isArray(p.preview) ? p.preview.map(String) : undefined,
            lineCount: typeof p.lineCount === 'number' ? p.lineCount : undefined,
          })
        }
        stageMessage.value = t('common.success', '完成')
        batchSettled(true)
        break
      case 'error':
        running.value = false
        errorCode.value = String(p.code ?? '')
        errorText.value = `${p.code ?? 'ERROR'}: ${p.message ?? ''}`
        pushLog(`[错误] ${p.code ?? ''}: ${p.message ?? ''}`)
        patchJob(taskId.value, { status: 'failed', error: errorText.value })
        batchSettled(false)
        break
    }
  })
  if (disposed) fn()
  else unlisten = fn
}

// 对齐引擎 ↔ 模型管理条目映射（自动匹配下载状态）
const ENGINE_MODEL_IDS: Record<string, string> = {
  qwen: 'qwen3-fa', 'qwen-ultra': 'qwen3-fa-ultra',
  'qwen-ultra-head': 'qwen3-fa-ultra-head', paraformer: 'paraformer-zh',
  mms: 'mms-fa', tifa: 'tifa-aligner', stars: 'stars',
}
const engineOpts = computed(() => {
  const items: Array<{ label: string; value: string; disabled?: boolean }> = [
    { label: t('lyrics.engineQwen'), value: 'qwen' },
    { label: t('lyrics.engineQwenUltra'), value: 'qwen-ultra' },
    { label: t('lyrics.engineQwenUltraHead'), value: 'qwen-ultra-head' },
    { label: t('lyrics.engineParaformer'), value: 'paraformer' },
    { label: t('lyrics.engineMms'), value: 'mms' },
    { label: t('lyrics.engineWhisper'), value: 'whisper' },
    { label: t('lyrics.engineTifa'), value: 'tifa', disabled: tifaOk.value === false },
    { label: t('lyrics.engineStars'), value: 'stars', disabled: true },
  ]
  return items.map((o) => {
    const mid = ENGINE_MODEL_IDS[o.value]
    const ok = mid ? !!l2tModels.value.find((m) => m.id === mid)?.complete : true
    return ok || o.value === 'whisper' || !mid ? o : { ...o, label: `${o.label} · 未下载` }
  })
})

// 引擎说明（随选择切换：直接对齐 or 先识别再对齐）
// 引擎一句话说明（≤1 行）；详细数据放悬停提示 engineDescTitle
const engineDesc = computed(() => ({
  '': '选择对齐引擎后显示说明',
  stars: 'STARS 歌声对齐（准备中）',
  none: '无歌词模式：识别模型直接产出时间轴',
  qwen: '直接对齐：歌词 + 音频 → 逐字时间轴（≤290s）',
  'qwen-ultra': '直接对齐：官方底座 + 歌声精修（≤290s）',
  'qwen-ultra-head': '直接对齐：Ultra + 边界精修头（≤290s）',
  paraformer: '转写 + 匹配：字级时间戳（无时长限制）',
  mms: '直接对齐：Meta MMS 多语言（无时长限制）',
  whisper: '先识别再对齐：定位 + 动态规划逐字（无时长限制）',
  tifa: '直接对齐：音素级（需 TIFA 外部环境）',
}[engine.value ?? ''] ?? t('lyrics.selectEngineHint')))

// 引擎详细说明（悬停显示；Ultra+Head 引用 HF 官方对比数据）
const engineDescTitle = computed(() => engine.value === 'qwen-ultra-head'
  ? '官方实测（huggingface.co/17slever17/Qwen3-ForcedAligner-Ultra）：'
    + '≤10ms 字级准确率 47%（Ultra 26%，官方版 22%）；'
    + '平均误差 26.6ms（Ultra 34.9ms，官方版 124.9ms）；速度约 95× 实时'
  : '')

// 识别模型说明（何时被用到）——按引擎实际情况区分：
// 290s 上限是 Qwen3-FA 架构特有；Paraformer/MMS/Whisper 自身无时长限制
const asrModelDesc = computed(() => {
  if (lyricsSource.value === 'none') return '无歌词模式必选：直接听写产出时间轴'
  if (engine.value === 'whisper') return 'Whisper 定位文字，再逐字对齐'
  if (lyricsSource.value === 'lrc') return 'LRC 已提供位置：仅 Whisper 引擎参与'
  if (!modelSize.value) return '未选 = 引擎直接对齐（纯文本 ≤290s）'
  if (engine.value === 'qwen' || engine.value === 'qwen-ultra' || engine.value === 'qwen-ultra-head')
    return '仅超 290 秒时用于行定位'
  return '当前引擎无时长限制，不使用识别模型'
})

// 识别模型显隐：Qwen3-FA 全家族（原版/Ultra/Ultra+Head）纯文本 >290s 需要定位器；
// Whisper 引擎核心即识别；其余引擎（Paraformer/MMS）无时长限制不需要
const isQwenFaEngine = computed(() =>
  engine.value === 'qwen' || engine.value === 'qwen-ultra' || engine.value === 'qwen-ultra-head')
const showAsrModel = computed(() =>
  engine.value === 'whisper' || lyricsSource.value === 'none'
  || (isQwenFaEngine.value && lyricsSource.value === 'text'))

// 各参数字段的 ✕ 重置（恢复默认值）
function resetField(key: string) {
  const defaults: Record<string, unknown> = {
    output: '', modelSize: 'large-v3', language: 'auto', template: 'classic_ktv',
    engine: 'whisper', selectedSeparationModel: '', start: null, duration: null,
    height: 0, neteaseUrl: '',
  }
  if (key in defaults) {
    const r = (formRefs as Record<string, { value: unknown }>)[key]
    if (r) r.value = defaults[key]
  }
}

// 「生成词轴对齐」前置条件：整目录需扫描出配对；单曲（LRC / 纯文本）需输入文件 + 对应歌词。
// 与 startTask 的运行时校验保持一致，避免按钮可点、点了才提示。
const canStart = computed(() => {
  if (workMode.value === 'dir') return !!batchScan.value?.matched.length
  if (!input.value.trim()) return false
  return lyricsSource.value === 'none' || !!importedLyrics.value
})

async function startTask() {
  // 目录批量模式：音视频与歌词同名配对，逐个串行处理
  if (workMode.value === 'dir') {
    await startBatchTask()
    return
  }
  if (!input.value) {
    message.warning(t('lyrics.inputPlaceholder'))
    return
  }
  if (!engine.value) {
    message.warning(t('lyrics.selectEngineFirst'))
    return
  }
  if (lyricsSource.value === 'none') {
    if (!modelSize.value) {
      message.warning('无歌词模式必须选择识别模型（用于听写转写）')
      return
    }
  } else if (!importedLyrics.value) {
    message.warning(t('lyrics.noLyricsFile'))
    return
  }
  // 点击「生成」即重置右栏：上一次的产物/日志/进度/歌词预览全部清空重新显示
  errorText.value = ''
  errorCode.value = ''
  lyrics.value = null
  outputs.value = {}
  percent.value = 0
  stage.value = 'validating_input'
  stageMessage.value = t('lyrics.stageIdle')
  logLines.value = []
  lastProgressLog.value = ''
  logOpen.value = false
  // 人声分离串联：未提供人声文件时先自动分离（主应用分离引擎 + 已下载模型），
  // 完成后由 lyr_sep_ 事件回调继续走 pipeline（ vocalsPath 已填好）
  if (useVocals.value && !vocalsPath.value) {
    await startAutoSeparationThenPipeline()
    return
  }
  // 自动分离回调复用已建的任务记录（job id = 预生成的 pipeline taskId）；
  // 判定不依赖 separating（分离回调里它已被置 false），只看同 id 记录在跑
  const reuseJob = !!taskId.value
    && lyricsJobs.value.some((j) => j.id === taskId.value && j.status === 'running')
  if (!reuseJob) taskId.value = `lyr_${Date.now()}`
  const asrName = modelSize.value === 'qwen3-asr' ? 'Qwen3-ASR-1.7B'
    : (modelSize.value ? 'Whisper ' + modelSize.value : '')
  const jobModel = lyricsSource.value === 'none'
    ? '无歌词 · ' + asrName
    : (lyricsSource.value === 'lrc' ? '导入 LRC · ' : '纯文本 · ')
      + (engine.value === 'tifa' ? 'TIFA' : engine.value === 'mms' ? 'MMS'
        : engine.value === 'qwen-ultra' ? 'Qwen3-Ultra'
        : engine.value === 'qwen-ultra-head' ? 'Qwen3-Ultra+Head' : 'Qwen3')
  if (reuseJob) {
    patchJob(taskId.value, { model: jobModel, source: lyricsSource.value })
  } else {
    upsertJob({
      id: taskId.value, time: Date.now(), input: input.value, model: jobModel,
      source: lyricsSource.value, status: 'running', percent: 0,
      stage: 'validating_input', outputs: {}, error: '',
    })
  }
  await ensureListener()
  try {
    const res = await invoke<{ taskId: string; started: boolean }>('start_lyrics', {
      payload: {
        taskId: taskId.value,
        action: 'pipeline',
        input: input.value,
        output: output.value || null,
        modelSize: modelSize.value,
        language: language.value === 'auto' ? null : language.value,
        // 设置页"运行设备和任务执行"选择的设备（auto = 有 CUDA 用 GPU）
        device: settings.getRuntimeDeviceConfig(appStore.envInfo).device || 'auto',
        deviceIds: settings.getRuntimeDeviceConfig(appStore.envInfo).deviceIds || [],
        lyricsSource: lyricsSource.value === 'none' ? 'asr' : 'import',
        lyricsMode: lyricsSource.value,
        lyricsText: lyricsSource.value === 'none' ? '' : (importedLyrics.value?.text || ''),
        sourceLyricsPath: importedLyricsPath.value || null,
        // 打轴：只产出 LRC/SRT/TTML/JSON/ASS，不烧视频
        burnVideo: false,
        lyricsFormat: null,  // 后端 looks_like_lrc 自动判断 LRC/纯文本
        template: template.value,
        nextPreview: nextPreview.value,
        start: start.value,
        duration: duration.value,
        height: height.value,
        modelDir: settings.modelDir || null,
        // lrc-ttml 同款新参数
        engine: engine.value,
        tifaPython: tifaPython.value || null,
        useVocals: useVocals.value,
        vocalsPath: vocalsPath.value || null,
        tailExtendMs: tailExtend.value ? 400 : 0,
        dragOverlap: dragOverlap.value,
        layeredLyrics: layeredLyrics.value,
        autoHarmony: autoSupplement.value,
        evenSpread: evenSpread.value,
      },
    })
    running.value = res.started
  } catch (e) {
    errorText.value = String(e)
  }
}

async function cancelTask() {
  if (!taskId.value && !batchRunning.value) return
  if (batchRunning.value) batchCancelled.value = true
  try {
    // 批量取消：流水线 id 与分离 id 都要取消（分离阶段流水线 id 还未产生）
    const ids = new Set<string>()
    if (taskId.value) ids.add(taskId.value)
    if (batchRunning.value && batchSepTaskId.value) ids.add(batchSepTaskId.value)
    for (const id of ids) {
      try { await invoke<boolean>('cancel_task', { taskId: id }) } catch { /* 单个失败继续 */ }
    }
    running.value = false
    if (batchRunning.value) {
      // 兜底：若 worker 事件 5 秒内未结算，强制停止批量
      window.setTimeout(() => {
        if (batchCancelled.value && batchRunning.value) {
          batchRunning.value = false
          running.value = false
          batchSepTaskId.value = ''
          pushLog(`[批量] 已强制停止：完成 ${batchDone.value} · 失败 ${batchFailed.value}`)
          message.info(t('lyrics.batchCancelled', { d: batchDone.value, f: batchFailed.value }))
        }
      }, 5000)
    } else {
      patchJob(taskId.value, { status: 'cancelled' })
    }
  } catch (e) {
    errorText.value = String(e)
  }
}

// ---- 联网搜索歌词 ----
async function runSearch() {
  const q = searchQuery.value.trim()
  if (!q) return
  searchTaskId.value = `lyr_search_${Date.now()}`
  searching.value = true
  searchResults.value = []
  await ensureListener()
  try {
    await invoke('start_lyrics', {
      payload: { taskId: searchTaskId.value, action: 'search_lyrics', query: q, limit: 10 },
    })
  } catch (e) {
    searching.value = false
    message.error(String(e))
  }
}

async function useSearchResult(r: SearchResult) {
  searchTaskId.value = `lyr_search_${Date.now()}`
  searching.value = true
  await ensureListener()
  try {
    await invoke('start_lyrics', {
      payload: {
        taskId: searchTaskId.value,
        action: 'fetch_lyrics',
        songId: r.songId,
        songName: `${r.name} - ${r.artist}`,
        // 有歌曲时把歌词直接存到歌曲同目录，用户能找到 .lrc 文件
        saveDir: input.value ? input.value.replace(/[\\/][^\\/]+$/, '') : null,
      },
    })
  } catch (e) {
    searching.value = false
    message.error(String(e))
  }
}

// ---- 网易云歌曲链接接线：粘贴 URL → 提取 songId → 抓歌词填入导入区 ----
async function useNeteaseLink() {
  const url = neteaseUrl.value.trim()
  if (!url) return
  const m = url.match(/id[=/](\d{4,})/) || url.match(/^(1[0-9]{9})$/)
  if (!m) {
    message.warning(t('lyrics.neteaseLinkInvalid'))
    return
  }
  fetchingLink.value = true
  searchTaskId.value = `lyr_search_${Date.now()}`
  await ensureListener()
  try {
    await invoke('start_lyrics', {
      payload: {
        taskId: searchTaskId.value,
        action: 'fetch_lyrics',
        songId: m[1],
        songName: `网易云 ${m[1]}`,
      },
    })
  } catch (e) {
    fetchingLink.value = false
    message.error(String(e))
  }
}

// ---- 人工编辑模式（独立编辑台页签） ----
const activeTab = ref<'workbench' | 'editor' | 'models'>('workbench')

function showEditor() {
  if (!lyrics.value) return
  editRows.value = lyrics.value.lines.map((l) => ({
    id: l.id,
    text: l.text,
    start: l.start,
    end: l.end,
    isBackground: !!l.is_background,
    agent: l.agent ?? '',
    translation: l.translation ?? '',
  }))
  deletedLineIds.value = new Set()
  activeTab.value = 'editor'
}

function resetEditor() {
  showEditor()
}

// ---- 编辑台播放（音频 asset 协议播放 + 行级高亮） ----
const audioSrc = computed(() => (input.value ? convertFileSrc(input.value) : ''))

function togglePlay() {
  const el = audioEl.value
  if (!el) return
  if (el.paused) {
    el.play().catch(() => message.warning(t('lyrics.playFailed')))
  } else {
    el.pause()
  }
}

function seekTo() {
  const el = audioEl.value
  if (!el) return
  el.currentTime = audioTime.value
}

function onTimeUpdate() {
  const el = audioEl.value
  if (!el) return
  audioTime.value = el.currentTime
  const t = el.currentTime * 1000
  const row = editRows.value.find((r) => t >= r.start && t < r.end)
  activeEditId.value = row?.id ?? ''
}

function fmtAudioTime(s: number) {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}.${String(Math.floor((s % 1) * 10))}`
}

// ---- 读取歌词文件（.json 任务产物）进编辑台 ----
const loadedLyricsPath = ref('')

async function pickLyricsJson() {
  try {
    const path = await invoke<string | null>('pick_lyrics_data_file')
    if (!path) return
    const resp = await fetch(convertFileSrc(path))
    const parsed = JSON.parse(await resp.text())
    lyrics.value = parsed as LyricsJson
    loadedLyricsPath.value = path
    showEditor()
    message.success(t('lyrics.editorLoaded'))
  } catch (e) {
    message.error(`${t('lyrics.editorLoadFailed')}: ${String(e)}`)
  }
}

function deleteLine(r: EditRow) {
  editRows.value = editRows.value.filter((x) => x.id !== r.id)
  deletedLineIds.value.add(r.id)
}

function nudge(row: EditRow, field: 'start' | 'end', delta: number) {
  if (field === 'start') {
    row.start = Math.max(0, row.start + delta)
    if (row.end < row.start + 1) row.end = row.start + 1
  } else {
    row.end = Math.max(row.end + delta, row.start + 1)
  }
}

function rowTag(r: EditRow): string {
  const tags: string[] = []
  if (r.isBackground) tags.push(t('lyrics.tagBacking'))
  if (r.agent) tags.push(r.agent.toUpperCase())
  if (r.translation) tags.push(t('lyrics.tagTranslation'))
  return tags.join(' · ')
}

async function applyEdits() {
  if (!lyrics.value || !editRows.value.length) return
  const target = outputs.value || {}
  taskId.value = `lyr_edit_${Date.now()}`
  errorText.value = ''
  percent.value = 0
  stage.value = 'generating_ass'
  stageMessage.value = t('lyrics.applyingEdits')
  await ensureListener()
  try {
    const res = await invoke<{ taskId: string; started: boolean }>('start_lyrics', {
      payload: {
        taskId: taskId.value,
        action: 'edit_apply',
        lyrics: lyrics.value,
        edits: [
          ...[...deletedLineIds.value].map((id) => ({ id, delete: true })),
          ...editRows.value.map((r) => ({ id: r.id, text: r.text, start: r.start, end: r.end, translation: r.translation })),
        ],
        template: template.value,
        nextPreview: nextPreview.value,
        modelDir: settings.modelDir || null,
        target: {
          lyricsJson: loadedLyricsPath.value || target.lyricsJson || null,
          assPath: target.ass || null,
          ttmlPath: target.ttml || null,
          lrcPath: target.lrc || null,
          videoSource: input.value || null,
          videoOut: target.video || null,
          height: height.value,
          start: start.value,
          duration: duration.value,
        },
      },
    })
    running.value = res.started
  } catch (e) {
    errorText.value = String(e)
  }
}

async function reveal(path: string) {
  try { await invoke('reveal_path', { path }) } catch { /* 忽略 */ }
}

onBeforeUnmount(() => {
  disposed = true
  unlisten?.()
  unlisten = null
  unlistenDragDrop?.()
  unlistenDragDrop = null
})

// ---- 展示辅助 ----
const outputList = computed(() =>
  Object.entries(outputs.value).filter(([, v]) => !!v) as [string, string][])

const totalChars = computed(() =>
  lyrics.value ? lyrics.value.lines.reduce((n, l) => n + l.words.length, 0) : 0)

const totalMs = computed(() => {
  if (!lyrics.value?.lines.length) return 1
  return Math.max(...lyrics.value.lines.map((l) => l.end - l.start), 1)
})

function charStyle(w: CharWord, line: LyricLine) {
  const span = Math.max(line.end - line.start, 1)
  const dur = Math.max(w.end - w.start, 1)
  const alpha = 0.15 + 0.75 * Math.min(Math.max(w.confidence ?? 0, 0), 1)
  return {
    flexGrow: String(dur),
    flexBasis: '0',
    background: `rgba(99, 102, 241, ${alpha.toFixed(2)})`,
  }
}
function fmtMs(ms: number) { return `${(ms / 1000).toFixed(2)}s` }
function fmtBytes(n: number) {
  if (!n || n <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}
function fmtSpeed(n: number) {
  if (!n || n <= 0) return '—'
  return `${fmtBytes(n)}/s`
}

async function copyDownloadLogs() {
  const task = detailModelId.value ? downloadTasks.value[detailModelId.value] : null
  if (!task) return
  try {
    await navigator.clipboard.writeText(task.logs.join('\n'))
    message.success(t('lyrics.logsCopied'))
  } catch {
    message.error(t('lyrics.logCopyFailed'))
  }
}

async function cancelDownloadById(modelId: string) {
  const task = downloadTasks.value[modelId]
  if (!task) return
  try { await invoke<boolean>('cancel_task', { taskId: task.taskId }) } catch { /* 忽略 */ }
  const next = { ...downloadTasks.value }
  delete next[modelId]
  downloadTasks.value = next
  if (detailModelId.value === modelId) detailModelId.value = ''
}
function charTitle(w: CharWord) {
  return `${w.char}  ${t('lyrics.startMs')} ${fmtMs(w.start)} → ${t('lyrics.endMs')} ${fmtMs(w.end)}  ${t('lyrics.confidence')} ${(w.confidence ?? 0).toFixed(2)}`
}
</script>

<template>
  <div class="page lyrics-page">
    <div class="page-header-compact">
      <div>
        <h1><n-icon :component="DocumentTextOutline" :size="22" style="margin-right: 8px" />{{ t('lyrics.title') }}</h1>
        <p>{{ t('lyrics.subtitle') }}</p>
      </div>
      <n-button size="small" secondary data-guide="lyrics-task-queue" @click="showQueueModal = true">
        {{ t('lyrics.taskQueue') }} ({{ lyricsJobs.length }})
      </n-button>
    </div>

    <n-tabs v-model:value="activeTab" type="line" class="lyrics-tabs">
      <n-tab-pane name="workbench" :tab="t('lyrics.tabWorkbench')">
      <div class="lyrics-layout" :style="{ gridTemplateColumns: `${leftWidth}px 6px minmax(0, 1fr)` }">
      <div class="lyrics-options">
        <h3 class="options-title">{{ t('lyrics.options') }}</h3>
        <h4 class="section-title">1 · {{ t('lyrics.secFiles') }}</h4>
        <div class="field">
          <label>{{ t('lyrics.workMode') }}</label>
          <div class="sep-seg">
            <button type="button" :class="{ on: workMode === 'single' }" @click="workMode = 'single'">{{ t('lyrics.workModeSingle') }}</button>
            <button type="button" :class="{ on: workMode === 'dir' }" @click="workMode = 'dir'">{{ t('lyrics.workModeDir') }}</button>
          </div>
        </div>
        <template v-if="workMode === 'single'">
        <div class="field">
          <label>{{ t('lyrics.input') }}</label>
          <div class="sep-dirrow">
            <n-input
              ref="inputFieldEl"
              v-model:value="input"
              :placeholder="t('lyrics.inputPlaceholder')"
              clearable
              class="drag-zone"
              :class="{ 'drag-zone--active': isDraggingFile && dragTarget === 'input' }"
            />
            <n-button size="small" :title="t('lyrics.pickFile')" @click="pickInput">📄</n-button>
          </div>
        </div>
        </template>
        <template v-else>
        <div class="field">
          <label>{{ t('lyrics.batchMediaDir') }}</label>
          <div class="sep-dirrow">
            <n-input v-model:value="batchMediaDir" size="small" :placeholder="t('lyrics.batchDirPlaceholder')" clearable @clear="clearBatchDir('media')" />
            <n-button size="small" :title="t('lyrics.pickDir')" @click="pickBatchDir('media')">📁</n-button>
          </div>
        </div>
        <div v-if="lyricsSource !== 'none'" class="field">
          <label>{{ t('lyrics.batchLyricsDir') }}</label>
          <div class="sep-dirrow">
            <n-input v-model:value="batchLyricsDir" size="small" :placeholder="t('lyrics.batchLyricsDirPlaceholder')" clearable @clear="clearBatchDir('lyrics')" />
            <n-button size="small" :title="t('lyrics.pickDir')" @click="pickBatchDir('lyrics')">📁</n-button>
          </div>
        </div>
        <div class="field">
          <div class="sep-dirrow">
            <n-button size="small" type="primary" :loading="batchScanning" :disabled="!batchMediaDir" @click="scanBatchDir">
              {{ t('lyrics.batchScan') }}
            </n-button>
            <n-text v-if="batchSummary" depth="3" style="font-size: 11px">{{ batchSummary }}</n-text>
          </div>
          <n-text depth="3" style="font-size: 11px">{{ t('lyrics.batchRuleHint') }}</n-text>
          <div v-if="scanLogLines.length" class="scan-log">
            <button type="button" class="scan-log-head" @click="scanLogOpen = !scanLogOpen">
              <span>扫描结果 · {{ scanLogLines.length - 2 }}</span>
              <span>{{ scanLogOpen ? '−' : '+' }}</span>
            </button>
            <div v-show="scanLogOpen" class="scan-log-body">
              <div v-for="(line, i) in scanLogLines" :key="i" class="scan-log-line" :class="{ warn: line.includes('✗') }">{{ line }}</div>
            </div>
          </div>
        </div>
        </template>
        <div class="field">
          <label>{{ t('lyrics.outputDir') }}</label>
          <div class="sep-dirrow">
            <n-input
              v-model:value="output"
              :placeholder="workMode === 'dir' ? t('lyrics.outputDirPlaceholderDir') : t('lyrics.outputDirPlaceholder')"
              clearable
            />
            <n-button size="small" :title="t('lyrics.pickDir')" @click="pickOutputDir">📁</n-button>
          </div>
        </div>
        <h4 class="section-title">2 · {{ t('lyrics.secParams') }}</h4>

        <div
          class="field"
          :title="workMode === 'dir' ? `${lyricsSourceDesc}\n${t('lyrics.batchFormatHint')}` : lyricsSourceDesc"
        >
          <label>{{ t('lyrics.source') }}</label>
          <n-radio-group v-model:value="lyricsSource" size="small">
            <n-radio-button value="lrc">📄 导入 LRC</n-radio-button>
            <n-radio-button value="text">📝 纯文本</n-radio-button>
            <n-radio-button value="none">🎤 无歌词</n-radio-button>
          </n-radio-group>
        </div>
        <div v-if="workMode === 'single' && lyricsSource !== 'none'" class="field">
          <label>{{ t('lyrics.neteaseLinkLabel') }}</label>
          <n-input
            v-model:value="neteaseUrl"
            size="small"
            :placeholder="t('lyrics.neteaseLinkPlaceholder')"
            clearable
          >
            <template #suffix>
              <n-button size="tiny" quaternary :loading="fetchingLink" @click="useNeteaseLink">
                {{ t('lyrics.neteaseLinkFetch') }}
              </n-button>
            </template>
          </n-input>
        </div>
        <div class="field">
          <n-button size="tiny" quaternary @click="searchOpen = !searchOpen">
            {{ t('lyrics.searchOnline') }}
          </n-button>
          <div v-if="searchOpen" class="search-panel">
            <div class="search-row">
              <n-input
                v-model:value="searchQuery"
                size="small"
                :placeholder="t('lyrics.searchPlaceholder')"
                clearable
                @keyup.enter="runSearch"
              />
              <n-button size="small" type="primary" :loading="searching" @click="runSearch">
                {{ t('lyrics.searchBtn') }}
              </n-button>
            </div>
            <div v-for="r in searchResults" :key="r.songId" class="search-result">
              <div class="search-meta">
                <span class="search-name">{{ r.name }}</span>
                <span class="search-artist">{{ r.artist }}<template v-if="r.album"> · {{ r.album }}</template></span>
              </div>
              <n-button size="tiny" :disabled="searching" @click="useSearchResult(r)">
                {{ t('lyrics.useResult') }}
              </n-button>
            </div>
          </div>
        </div>
        <div
          v-if="workMode === 'single' && lyricsSource !== 'none'"
          ref="lyricsFieldEl"
          class="field drag-zone"
          :class="{ 'drag-zone--active': isDraggingFile && dragTarget === 'lyrics' }"
        >
          <label>{{ lyricsFileHint }}</label>
          <div class="sep-dirrow">
            <n-input :value="importedLyrics?.name || ''" :placeholder="lyricsFilePlaceholder" clearable @clear="clearImportedLyrics" />
            <n-button size="tiny" :title="t('lyrics.pickFile')" @click="triggerLyricsPick">📄</n-button>
          </div>
          <input ref="lyricsFileInput" type="file" accept=".lrc,.txt" style="display: none" @change="onLyricsFileChange" />
          <n-text v-if="importedLyricsPath" depth="3" style="font-size: 11px; display: flex; align-items: center; gap: 6px; margin-top: 4px">
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap" :title="importedLyricsPath">
              {{ importedLyricsPath }}
            </span>
            <n-button size="tiny" quaternary @click="reveal(importedLyricsPath)">{{ t('lyrics.openLocation') }}</n-button>
          </n-text>
        </div>
        <div class="flow-title">🎤 语音识别 · 定位 / 转写</div>
        <div class="field-row">
          <div class="field">
            <label>{{ t('lyrics.modelSize') }}</label>
            <n-select v-model:value="modelSize" :options="modelOptions" clearable placeholder="选择识别模型" />
            <n-text depth="3" style="font-size: 11px">{{ asrModelDesc }}</n-text>
          </div>
          <div class="field">
            <label>{{ t('lyrics.language') }}</label>
            <n-select v-model:value="language" :options="languageOptions" />
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>{{ t('lyrics.template') }}</label>
            <n-select v-model:value="template" :options="templateOptions" />
          </div>
          <div class="field">
            <label>{{ t('lyrics.height') }}</label>
            <n-select v-model:value="height" :options="[
              { label: t('lyrics.heightOriginal'), value: 0 },
              { label: '720p', value: 720 }, { label: '1080p', value: 1080 }]" />
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>{{ t('lyrics.start') }}</label>
            <n-input-number v-model:value="start" :min="0" :show-button="false" :placeholder="t('lyrics.startHint')" />
          </div>
          <div class="field">
            <label>{{ t('lyrics.duration') }}</label>
            <n-input-number v-model:value="duration" :min="1" :show-button="false" />
          </div>
        </div>
        <div class="switch-chip" :title="t('lyrics.nextPreview')">
          <n-switch v-model:value="nextPreview" size="small" />
          <span class="switch-label">{{ t('lyrics.nextPreview') }}</span>
        </div>
        <div class="flow-title">🎯 歌词对齐 · 逐字时间轴</div>
        <div class="field">
          <label>{{ t('lyrics.alignEngine') }}</label>
          <n-select
            v-model:value="engine"
            :options="engineOpts"
            clearable
            :placeholder="t('lyrics.selectAlignModel')"
            @update:value="(v: string) => v === 'tifa' && checkTifa()"
          />
          <n-text depth="3" style="font-size: 11px" :title="engineDescTitle">{{ engineDesc }}</n-text>
          <n-text v-if="engine === 'tifa'" depth="3" style="font-size: 11px">
            {{ t('lyrics.tifaNeedLrc') }}
          </n-text>
        </div>
        <div class="switch-chip" :title="t('lyrics.useVocalsHint')">
          <n-switch v-model:value="useVocals" size="small" @update:value="(v: boolean) => v && ensureSeparationModels()" />
          <span class="switch-label">{{ t('lyrics.useVocals') }}</span>
        </div>
        <template v-if="useVocals">
            <div class="field" style="margin-top: 6px">
              <label>{{ t('lyrics.separateModel') }}</label>
              <n-select
                v-model:value="selectedSeparationModel"
                size="small"
                filterable
                clearable
                :options="separationModelOptions"
                :placeholder="t('lyrics.selectModel')"
              />
            </div>
            <div class="field" style="margin-top: 6px">
              <label>{{ t('lyrics.existingVocals') }}</label>
              <div v-if="workMode === 'single'" class="sep-dirrow">
                <n-input v-model:value="vocalsPath" size="tiny" :placeholder="t('lyrics.vocalsPick')" clearable />
                <n-button size="tiny" :title="t('lyrics.pickFile')" @click="pickVocals">📄</n-button>
              </div>
              <div v-else class="sep-dirrow">
                <n-input v-model:value="batchVocalsDir" size="tiny" :placeholder="t('lyrics.batchVocalsDirPlaceholder')" clearable />
                <n-button size="tiny" :title="t('lyrics.pickDir')" @click="async () => { const d = await invoke<string | null>('pick_input_folder'); if (d) batchVocalsDir = d }">📁</n-button>
              </div>
            </div>
            <!-- 输出设置：完整复刻分离页「02 输出设置」（目录/格式/保存方式/命名/音轨/高级设置弹窗） -->
            <div class="sep-outset">
              <div class="sep-outset-head">
                <span class="sep-outset-badge">02</span>
                <span class="sep-outset-title">{{ t('lyrics.sepOutsetTitle') }}</span>
                <span class="sep-outset-hint">{{ t('separate.outputSummaryHint') }}</span>
                <n-button size="tiny" quaternary class="sep-outset-advanced" @click="showSepAdvanced = true">
                  ⚙ {{ t('separate.configParams') }}
                </n-button>
              </div>
              <div class="field">
                <label>{{ t('separate.temporaryOutputDir') }}</label>
                <div class="sep-dirrow">
                  <n-input v-model:value="sepOutputDir" size="tiny" :placeholder="t('lyrics.sepOutputDirPlaceholder')" clearable />
                  <n-button size="tiny" :title="t('separate.chooseOutput')" @click="pickSepOutputDir">📁</n-button>
                </div>
              </div>
              <div class="sep-two">
                <div class="field">
                  <label>{{ t('settings.defaultFormat') }}</label>
                  <n-select v-model:value="sepFormat" size="tiny" :options="sepFormatOptions" />
                </div>
                <div class="field">
                  <label>{{ t('separate.saveMode') }}</label>
                  <div class="sep-seg">
                    <button type="button" :class="{ on: sepLayout === 'flat' }" @click="sepLayout = 'flat'">
                      {{ t('separate.saveModeFlatName') }}
                    </button>
                    <button type="button" :class="{ on: sepLayout === 'folders' }" @click="sepLayout = 'folders'">
                      {{ t('separate.saveModeFolderName') }}
                    </button>
                  </div>
                </div>
              </div>
              <div class="field">
                <label>{{ t('separate.namingRule') }}</label>
                <div class="sep-naming">
                  <n-switch v-model:value="sepNamingEnabled" size="small" />
                  <n-input
                    v-model:value="sepNamingTemplate"
                    size="tiny"
                    :disabled="!sepNamingEnabled"
                    placeholder="%index%_%filename%_%stem%"
                  />
                  <n-button size="tiny" quaternary type="primary" :disabled="!sepNamingEnabled" @click="showSepNaming = true">
                    {{ t('separate.namingRuleAction') }}
                  </n-button>
                </div>
              </div>
              <div class="field">
                <label>{{ t('separate.outputStems') }}</label>
                <div class="sep-stems">
                  <n-checkbox v-model:checked="sepStemVocals">Vocals</n-checkbox>
                  <n-checkbox v-model:checked="sepStemInstrumental">Instrumental</n-checkbox>
                </div>
              </div>
            </div>
          </template>
        <!-- 开关两列并排：说明文字放悬停提示（title），省掉说明行 -->
        <div class="switch-grid">
          <div class="switch-chip" :title="t('lyrics.tailExtend')">
            <n-switch v-model:value="tailExtend" size="small" />
            <span class="switch-label">{{ t('lyrics.tailExtend') }}</span>
          </div>
          <div class="switch-chip" :title="t('lyrics.dragOverlapHint')">
            <n-switch v-model:value="dragOverlap" size="small" />
            <span class="switch-label">{{ t('lyrics.dragOverlap') }}</span>
          </div>
          <div class="switch-chip" :title="t('lyrics.layeredLyricsHint')">
            <n-switch v-model:value="layeredLyrics" size="small" />
            <span class="switch-label">{{ t('lyrics.layeredLyrics') }}</span>
          </div>
          <div class="switch-chip" :title="t('lyrics.autoSupplementHint')">
            <n-switch v-model:value="autoSupplement" size="small" />
            <span class="switch-label">{{ t('lyrics.autoSupplement') }}</span>
          </div>
          <div class="switch-chip" :title="t('lyrics.evenSpreadHint')">
            <n-switch v-model:value="evenSpread" size="small" />
            <span class="switch-label">{{ t('lyrics.evenSpread') }}</span>
          </div>
        </div>
        <h4 class="section-title">3 · {{ t('lyrics.secRun') }}</h4>
        <div class="actions">
          <n-button
            type="primary"
            :disabled="running || !canStart"
            :loading="running || batchRunning"
            @click="startTask"
          >
            <template #icon><n-icon :component="PlayOutline" /></template>
            {{ t('lyrics.startSync') }}
          </n-button>
          <n-button :disabled="!running && !batchRunning" @click="cancelTask">
            <template #icon><n-icon :component="StopOutline" /></template>
            {{ t('lyrics.cancel') }}
          </n-button>
        </div>
        <div class="field ttml-btns">
          <n-button size="tiny" :disabled="!outputs.ttml" @click="outputs.ttml && reveal(outputs.ttml)">
            {{ t('lyrics.openTtml') }}
          </n-button>
          <n-button size="tiny" :disabled="!outputList.length" @click="outputs.lyricsJson ? reveal(outputs.lyricsJson) : undefined">
            {{ t('lyrics.openFolder') }}
          </n-button>
        </div>
      </div>

      <!-- 左右占比拖拽分隔条：拖动调整参数栏宽度，双击恢复默认 -->
      <div
        class="layout-splitter"
        :class="{ splitting }"
        title="拖动调整左右占比 · 双击恢复默认"
        @pointerdown="onSplitDown"
        @dblclick="resetSplit"
      ></div>

      <div class="lyrics-results">
        <div class="results-toolbar">
          <span class="results-title">{{ t('lyrics.resultsTitle') }}</span>
          <n-button
            size="tiny" quaternary type="error"
            :disabled="running || (!logLines.length && !lyrics && !outputList.length && percent === 0 && !errorText && !importedLyrics)"
            @click="clearResults"
          >{{ t('lyrics.clearResults') }}</n-button>
        </div>

        <n-alert v-if="errorText" type="error" :title="errorTitle" closable @close="errorText = ''">
          {{ errorText }}
        </n-alert>

        <!-- 导入歌词原文（对齐前的原始文本，LRC 含时间戳原样显示） -->
        <div v-if="importedLyrics" class="imported-lyrics-panel">
          <h3>
            {{ t('lyrics.importedOriginal') }}
            <n-text depth="3" style="font-weight: normal; font-size: 12px">
              · {{ importedLyrics.name }}
              · {{ importedLyrics.text.split('\n').filter((l) => l.trim()).length }} {{ t('lyrics.line') }}
            </n-text>
          </h3>
          <n-text depth="3" style="font-size: 11px; display: block; margin-bottom: 6px">
            {{ t('lyrics.importedOriginalHint') }}
          </n-text>
          <div class="imported-lyrics-body">{{ importedLyrics.text }}</div>
        </div>

        <div v-if="running || percent > 0" class="progress-block">
          <div class="stage-line">
            <span class="progress-label">{{ t('lyrics.stage') }}</span>
            <span class="stage-key">{{ stage }}</span>
            <span>{{ stageMessage }}</span>
          </div>
          <div class="progress-line">
            <span class="progress-label">{{ t('lyrics.progress') }}</span>
            <n-progress type="line" :percentage="Math.round(percent)" :height="14" indicator-placement="inside" processing style="flex: 1" />
          </div>
        </div>

        <!-- 后端实时日志（可折叠：处理时点开查看，展开自动滚到底部） -->
        <div v-if="logLines.length" class="log-panel">
          <button class="log-head" type="button" @click="toggleLog">
            <span>{{ t('lyrics.backendLog') }} · {{ logLines.length }}</span>
            <span class="log-toggle">{{ logOpen ? '−' : '+' }} {{ logOpen ? t('lyrics.hideLog') : t('lyrics.showLog') }}</span>
          </button>
          <div v-show="logOpen" ref="logBodyEl" class="log-body">
            <div v-for="(l, i) in logLines" :key="i" class="log-line">{{ l }}</div>
          </div>
        </div>

        <div v-if="outputs && outputList.length" class="outputs-block">
          <button class="outputs-head" type="button" @click="outputsOpen = !outputsOpen">
            <span>{{ t('lyrics.outputs') }} · {{ outputList.length }}</span>
            <span class="log-toggle">{{ outputsOpen ? '−' : '+' }}</span>
          </button>
          <div v-show="outputsOpen" class="outputs-body">
            <div v-for="[key, path] in outputList" :key="key" class="output-row">
              <span class="output-key">{{ key }}</span>
              <n-text code>{{ path }}</n-text>
              <n-button size="tiny" quaternary :title="t('lyrics.openDir')" @click="reveal(path)">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
              </n-button>
            </div>
          </div>
        </div>
        <div v-else-if="percent >= 100 && !running" class="outputs-block">
          <h3>{{ t('lyrics.outputs') }}</h3>
          <n-empty size="small" :description="t('lyrics.noOutputs')" />
        </div>

        <!-- 时间轴预览：位置不变（结果流程末尾），内容超高一屏时内部滚动 -->
        <div v-if="lyrics" class="lyrics-preview">
          <h3>
            {{ t('lyrics.lyricsPreview') }}
            <n-text depth="3" style="font-weight: normal; font-size: 12px">
              · {{ lyrics.lines.length }} {{ t('lyrics.line') }}
              · {{ totalChars }} {{ t('lyrics.chars') }}
              · {{ lyrics.alignment_model }}
            </n-text>
            <n-button size="tiny" style="margin-left: 8px" @click="showEditor">
              {{ t('lyrics.openEditor') }}
            </n-button>
          </h3>
          <div class="preview-scroll">
            <div v-for="line in lyrics.lines" :key="line.id" class="line-block">
              <div class="line-head">
                <span class="line-id">{{ line.id }}</span>
                <span class="line-text">{{ line.text }}</span>
                <span class="line-time">{{ fmtMs(line.start) }} → {{ fmtMs(line.end) }}</span>
              </div>
              <div class="char-strip">
                <div
                  v-for="(w, wi) in line.words"
                  :key="wi"
                  class="char-box"
                  style="cursor: grab; touch-action: none"
                  :style="charStyle(w, line)"
                  :title="charTitle(w) + '（左右拖动微调时间）'"
                  @pointerdown="onCharPointerDown($event, line, wi)"
                >{{ w.char }}</div>
              </div>
            </div>
          </div>
        </div>
        <n-empty v-else :description="t('lyrics.noLyrics')" />
      </div>
      </div>
      </n-tab-pane>

      <!-- 编辑台：行级文本/时间编辑 + ±10ms 微调（lrc-ttml 编辑台同款交互） -->
      <n-tab-pane name="editor" :tab="t('lyrics.tabEditor')">
        <div v-if="lyrics && editRows.length" class="editor-page">
          <div class="editor-toolbar">
            <span class="editor-hint">{{ t('lyrics.editorHint') }}</span>
            <div style="display: flex; gap: 6px">
              <n-button size="tiny" @click="pickLyricsJson">{{ t('lyrics.loadLyricsFile') }}</n-button>
              <n-button size="tiny" quaternary @click="resetEditor">{{ t('lyrics.resetEditor') }}</n-button>
            </div>
          </div>
          <!-- 播放条：播放驱动行高亮（lrc-ttml 编辑台同款交互） -->
          <div class="playback-bar">
            <n-button size="tiny" @click="togglePlay">{{ audioPlaying ? t('lyrics.pause') : t('lyrics.play') }}</n-button>
            <span class="playback-time">{{ fmtAudioTime(audioTime) }}</span>
            <audio
              ref="audioEl"
              :src="audioSrc"
              style="display: none"
              @timeupdate="onTimeUpdate"
              @play="audioPlaying = true"
              @pause="audioPlaying = false"
            ></audio>
          </div>
          <div class="editor-table">
            <div class="editor-head">
              <span class="editor-id">#</span>
              <span class="editor-cell-time">{{ t('lyrics.colStart') }}</span>
              <span class="editor-cell-time">{{ t('lyrics.colEnd') }}</span>
              <span class="editor-cell-text">{{ t('lyrics.colText') }}</span>
              <span class="editor-cell-trans">{{ t('lyrics.colTranslation') }}</span>
              <span style="width: 60px"></span>
            </div>
            <div
              v-for="r in editRows"
              :key="r.id"
              class="editor-row"
              :class="{ 'editor-row-active': r.id === activeEditId }"
            >
              <span class="editor-id" :title="rowTag(r)">
                {{ r.id }}<template v-if="rowTag(r)"> · {{ rowTag(r) }}</template>
              </span>
              <div class="editor-cell-time">
                <n-input-number v-model:value="r.start" size="tiny" :min="0" :step="100" :show-button="false" style="width: 92px" />
                <n-button size="tiny" quaternary @click="nudge(r, 'start', -10)">−10</n-button>
                <n-button size="tiny" quaternary @click="nudge(r, 'start', 10)">+10</n-button>
              </div>
              <div class="editor-cell-time">
                <n-input-number v-model:value="r.end" size="tiny" :min="0" :step="100" :show-button="false" style="width: 92px" />
                <n-button size="tiny" quaternary @click="nudge(r, 'end', -10)">−10</n-button>
                <n-button size="tiny" quaternary @click="nudge(r, 'end', 10)">+10</n-button>
              </div>
              <n-input v-model:value="r.text" size="tiny" class="editor-cell-text" />
              <n-input v-model:value="r.translation" size="tiny" class="editor-cell-trans" :placeholder="'—'" />
              <n-button size="tiny" quaternary type="error" style="width: 60px" @click="deleteLine(r)">
                {{ t('lyrics.deleteLine') }}
              </n-button>
            </div>
          </div>
          <n-text depth="3" style="font-size: 12px">{{ editRows.length }} {{ t('lyrics.line') }}</n-text>
          <div class="editor-actions">
            <n-button size="small" type="primary" :disabled="running" :loading="running" @click="applyEdits">
              {{ t('lyrics.applyEdits') }}
            </n-button>
            <n-button size="small" @click="resetEditor">{{ t('lyrics.resetEditor') }}</n-button>
            <n-text depth="3" style="font-size: 12px">{{ t('lyrics.applyHint') }}</n-text>
          </div>
        </div>
        <n-empty v-else size="small" :description="t('lyrics.editorEmpty')">
          <template #extra>
            <n-button size="small" @click="pickLyricsJson">{{ t('lyrics.loadLyricsFile') }}</n-button>
          </template>
        </n-empty>
      </n-tab-pane>

      <!-- 模型管理：仿主界面模型库（大页头 + 筛选工具栏 + 分组列表） -->
      <n-tab-pane name="models" :tab="t('lyrics.tabModels')">
        <div class="page-header-compact">
          <div>
            <div class="models-title-row">
              <h1 class="models-tab-title">{{ t('lyrics.l2tTitle') }}</h1>
              <n-tag size="small" :bordered="false" type="info" round>
                {{ t('lyrics.l2tCount', { count: l2tModels.length }) }}
              </n-tag>
            </div>
            <p>{{ t('lyrics.l2tSubtitle') }}</p>
          </div>
          <div class="header-actions">
            <n-button secondary :loading="l2tBusy" @click="refreshL2t">
              <template #icon><n-icon :component="RefreshOutline" /></template>
              {{ t('lyrics.modelRefresh') }}
            </n-button>
          </div>
        </div>

        <!-- 筛选工具栏 -->
        <div class="toolbar">
          <div class="toolbar-row">
            <n-input
              v-model:value="l2tSearch"
              :placeholder="t('lyrics.l2tSearch')"
              clearable
              class="search-input"
            >
              <template #prefix><n-icon :component="SearchOutline" /></template>
            </n-input>
            <n-select
              v-model:value="l2tCategory"
              :options="l2tCategoryOptions"
              size="small"
              class="category-select"
              :consistent-menu-width="false"
            />
            <n-select
              v-model:value="l2tSourceFilter"
              :options="l2tSourceOptions"
              size="small"
              class="source-select"
              :consistent-menu-width="false"
            />
            <n-select
              v-model:value="l2tSort"
              :options="l2tSortOptions"
              size="small"
              class="sort-select"
              :consistent-menu-width="false"
            />
            <div class="toolbar-actions">
              <n-switch v-model:value="l2tDownloadedOnly" size="small" />
              <span class="text-sm text-muted">{{ t('lyrics.l2tDownloadedOnly') }}</span>
              <div class="view-toggle" role="group">
                <button
                  type="button"
                  class="view-toggle__button"
                  :class="{ 'view-toggle__button--active': l2tViewMode === 'list' }"
                  :aria-pressed="l2tViewMode === 'list'"
                  :title="t('models.viewList')"
                  @click="l2tViewMode = 'list'"
                >
                  <n-icon :component="ListOutline" />
                </button>
                <button
                  type="button"
                  class="view-toggle__button"
                  :class="{ 'view-toggle__button--active': l2tViewMode === 'card' }"
                  :aria-pressed="l2tViewMode === 'card'"
                  :title="t('models.viewCard')"
                  @click="l2tViewMode = 'card'"
                >
                  <n-icon :component="GridOutline" />
                </button>
              </div>
            </div>
          </div>
        </div>

        <div class="models-block">
          <n-alert
            v-if="l2tModels.length && l2tModels.some((m) => !m.complete)"
            type="warning" style="margin-bottom: 8px"
          >
            {{ t('lyrics.l2tMissing', { n: l2tModels.filter((m) => !m.complete).length, total: l2tModels.length }) }}
          </n-alert>
          <n-text depth="3" style="font-size: 11px; display: block; margin-bottom: 8px">
            {{ t('lyrics.l2tCacheDir') }}: {{ l2tRoot }}
          </n-text>
          <div v-for="grp in groupedFilteredL2t" :key="grp.key" class="model-group">
            <div v-if="grp.label" class="model-group-title">{{ grp.label }}<span class="model-group-count">{{ grp.items.length }}</span></div>
            <div :class="['model-grid', `model-grid--${l2tViewMode}`]">
            <div v-for="m in grp.items" :key="m.id" class="model-row">
            <div class="model-info">
              <div class="model-name"
                   :title="`${m.name}\n用途: ${m.note || '—'}\n协议: ${m.license || '—'}\n来源: ${m.source}\n存放位置: ${m.cacheDir}`">
                {{ m.complete ? '✓' : '⚠' }} {{ m.name }}
              </div>
              <div class="model-meta">
                <n-tag size="tiny" round :type="m.complete ? 'success' : 'warning'" :bordered="false">
                  {{ m.complete ? t('lyrics.l2tReady') : t('lyrics.l2tMissingOne') }}
                </n-tag>
                <n-tag v-if="l2tSource[m.id]" size="tiny" round :bordered="false" type="info">
                  源: {{ SOURCE_LABELS[l2tSource[m.id]] || l2tSource[m.id] }}
                </n-tag>
                <span class="model-size">约 {{ fmtBytes(m.approxSize) }} · {{ m.licenseShort }} · {{ m.source }}</span>
              </div>
              <div v-if="l2tDownloads[m.id]" class="model-meta" style="color: inherit">
                {{ l2tDownloads[m.id]!.message }}
              </div>
              <n-progress
                v-if="l2tDownloads[m.id]?.status === 'downloading'"
                type="line"
                :percentage="l2tDownloads[m.id]!.percent"
                :height="6"
                indicator-placement="inside"
                processing
                style="margin-top: 4px"
              />
              <div v-else-if="!m.complete && m.problems.length" class="model-meta"
                   :title="m.problems.join('\n')">
                {{ t('lyrics.l2tProblems') }}: {{ m.problems.slice(0, 2).join('; ') }}<template v-if="m.problems.length > 2"> 等 {{ m.problems.length }} 项</template>
              </div>
            </div>
            <div class="model-actions">
              <n-select
                size="tiny"
                style="width: 172px"
                :value="l2tSource[m.id] ?? ''"
                :options="DOWNLOAD_SOURCES"
                :title="'选择该模型的下载源；默认 = ModelScope 优先（国内直连），HF-Mirror 镜像、HuggingFace 官方依次回退'"
                placeholder="下载源"
                @update:value="(v: string) => { l2tSource[m.id] = v; l2tSource = { ...l2tSource } }"
              />
              <n-button
                v-if="l2tDownloads[m.id]?.status === 'downloading'"
                size="tiny" type="error" secondary
                @click="l2tCancel(m)"
              >取消下载</n-button>
              <n-button
                v-else
                size="tiny" type="primary"
                :disabled="m.complete
                  || (!!m.target && downloadingTargets.has(m.target) && l2tDownloads[m.id]?.status !== 'downloading')"
                :title="m.complete ? '本地已就绪' : (m.target && downloadingTargets.has(m.target) ? '与另一模型共用下载目录，请等它完成或取消后再下载' : '本地缺失，可下载')"
                @click="l2tDownload(m)"
              >{{ t('lyrics.download') }}</n-button>
              <n-button size="tiny" quaternary @click="refreshL2t">{{ t('lyrics.verifyModel') }}</n-button>
              <n-button
                size="tiny" quaternary type="error"
                :disabled="!m.complete || l2tBusy"
                @click="l2tDelete(m)"
              >{{ t('lyrics.l2tClean') }}</n-button>
              <n-button size="tiny" quaternary @click="reveal(m.cacheDir)">{{ t('lyrics.openLocation') }}</n-button>
            </div>
            </div>
          </div>
          </div>

        </div>

        <div class="models-block" style="margin-top: 16px">
          <div class="models-head">
            <h3 class="options-title">{{ t('lyrics.alignModels') }}</h3>
          </div>
          <div v-for="a in alignModelRows" :key="a.id" class="model-row">
            <div class="model-info">
              <div class="model-name">{{ a.complete ? '✓' : '⚠' }} {{ a.key }}</div>
              <div class="model-meta">
                <n-tag size="tiny" round :type="a.complete ? 'success' : 'warning'" :bordered="false">
                  {{ a.complete ? t('lyrics.l2tReady') : t('lyrics.l2tMissingOne') }}
                </n-tag>
                <span class="model-size">align · {{ fmtBytes(a.sizeBytes) }}<template v-if="a.expectedSizeBytes"> / {{ fmtBytes(a.expectedSizeBytes) }}</template></span>
              </div>
              <div v-if="downloadTasks[a.id]" class="model-meta">
                {{ downloadTasks[a.id]!.message || '' }} · {{ Math.round(downloadTasks[a.id]!.percent) }}%
              </div>
            </div>
            <div class="model-actions">
              <n-button v-if="!a.complete" size="tiny" type="primary" @click="downloadModel(a)">{{ t('lyrics.download') }}</n-button>
              <n-button v-else size="tiny" quaternary type="error" :loading="!!deleting[a.id]" @click="deleteModel(a)">{{ t('lyrics.deleteModel') }}</n-button>
              <n-button size="tiny" quaternary @click="reveal(a.path)">{{ t('lyrics.openLocation') }}</n-button>
            </div>
          </div>
        </div>
      </n-tab-pane>
    </n-tabs>

    <!-- 下载详情弹窗（对齐模型页下载详情的交互） -->
    <n-modal v-model:show="detailShow" preset="card" style="width: 640px" :title="t('lyrics.downloadDetailTitle')">
      <template v-if="detailModelId && downloadTasks[detailModelId]">
        <div class="detail-head">
          <div>
            <div class="detail-model">{{ detailModelId }}</div>
            <div class="detail-sub">
              <n-tag v-if="downloadTasks[detailModelId]!.endpoint" size="tiny" round :bordered="false" type="info">
                {{ downloadTasks[detailModelId]!.endpoint }}
              </n-tag>
              <span class="detail-file" :title="downloadTasks[detailModelId]!.currentFile">
                {{ downloadTasks[detailModelId]!.currentFile || downloadTasks[detailModelId]!.message }}
              </span>
            </div>
          </div>
          <div class="detail-percent">{{ Math.round(downloadTasks[detailModelId]!.percent) }}<span>%</span></div>
        </div>

        <n-progress
          type="line"
          :percentage="Math.round(downloadTasks[detailModelId]!.percent)"
          :height="12"
          indicator-placement="inside"
          processing
        />

        <div class="detail-grid">
          <div class="detail-cell">
            <div class="detail-label">{{ t('lyrics.transferred') }}</div>
            <div class="detail-value">
              {{ fmtBytes(downloadTasks[detailModelId]!.downloadedBytes) }} / {{ fmtBytes(downloadTasks[detailModelId]!.totalBytes) }}
            </div>
          </div>
          <div class="detail-cell">
            <div class="detail-label">{{ t('lyrics.downloadSpeed') }}</div>
            <div class="detail-value">{{ fmtSpeed(downloadTasks[detailModelId]!.speed) }}</div>
          </div>
          <div class="detail-cell">
            <div class="detail-label">{{ t('lyrics.fileProgress') }}</div>
            <div class="detail-value">
              {{ downloadTasks[detailModelId]!.completedFiles }} / {{ downloadTasks[detailModelId]!.totalFiles }}
            </div>
          </div>
        </div>

        <n-alert v-if="downloadTasks[detailModelId]!.status === 'error'" type="error" style="margin-top: 10px">
          {{ downloadTasks[detailModelId]!.errorText }}
        </n-alert>

        <div class="detail-logs-head">
          <span>{{ t('lyrics.downloadLog') }}</span>
          <n-button size="tiny" quaternary @click="copyDownloadLogs">{{ t('lyrics.copyLogs') }}</n-button>
        </div>
        <div class="detail-logs">
          <div v-for="(line, idx) in downloadTasks[detailModelId]!.logs" :key="idx" class="detail-log-line">
            <span class="detail-log-idx">{{ String(idx + 1).padStart(3, '0') }}</span>
            <span>{{ line }}</span>
          </div>
          <div v-if="!downloadTasks[detailModelId]!.logs.length" class="detail-log-empty">…</div>
        </div>
      </template>
      <template #footer>
        <div class="detail-footer">
          <span class="detail-hint">{{ t('lyrics.backgroundHint') }}</span>
          <n-button
            v-if="detailModelId && downloadTasks[detailModelId]?.status === 'downloading'"
            type="error" secondary
            @click="cancelDownloadById(detailModelId)"
          >{{ t('lyrics.cancel') }}</n-button>
          <n-button v-else quaternary @click="detailShow = false">{{ t('lyrics.close') }}</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 任务队列（参考分离页：本次任务 / 历史记录） -->
    <!-- 命名规则编辑弹窗（复刻分离页）：模板 / 预览 / 变量 / 编号顺序 -->
    <n-modal v-model:show="showSepNaming" style="width: var(--modal-width-md)">
      <n-card :title="t('separate.namingModalTitle')" :bordered="false" size="small">
        <div class="sep-modal-sec">
          <div class="sep-modal-label">{{ t('separate.namingTemplate') }}</div>
          <n-input
            ref="sepNamingInputRef"
            v-model:value="sepNamingTemplate"
            size="small"
            :placeholder="t('separate.namingTemplateHint')"
          />
          <div class="sep-modal-hint">{{ t('separate.namingTemplateHint') }}</div>
          <div class="sep-modal-label">{{ t('separate.namingPreview') }}</div>
          <div class="sep-naming-preview">
            <div v-for="(p, i) in sepOrderedStems" :key="p" class="sep-naming-preview-row">
              <span class="sep-naming-preview-idx">{{ String(i + 1).padStart(2, '0') }}</span>
              {{ sepNamingPreviewFile(p, i) }}
            </div>
          </div>
        </div>
        <div class="sep-modal-sec">
          <div class="sep-modal-label">{{ t('separate.namingVariables') }}</div>
          <div class="sep-modal-hint">{{ t('separate.namingVariablesHint') }}</div>
          <div class="sep-token-grid">
            <button
              v-for="tok in sepNamingTokens"
              :key="tok.value"
              type="button"
              class="sep-token-chip"
              @click="insertSepToken(tok.value)"
            >
              <span>{{ t(tok.labelKey) }}</span>
              <code>{{ tok.value }}</code>
            </button>
          </div>
        </div>
        <div class="sep-modal-sec">
          <div class="sep-modal-label">{{ t('separate.namingNumberOrder') }}</div>
          <div class="sep-modal-hint">{{ t('separate.namingNumberOrderHint') }}</div>
          <div class="sep-order-list">
            <div v-for="(stem, i) in sepStemOrder" :key="stem" class="sep-order-row">
              <span class="sep-order-idx">{{ String(i + 1).padStart(2, '0') }}</span>
              <span class="sep-order-name">{{ stem.charAt(0).toUpperCase() + stem.slice(1) }}</span>
              <span class="sep-order-btns">
                <n-button size="tiny" quaternary :disabled="i === 0" @click="moveSepStem(i, -1)">↑</n-button>
                <n-button size="tiny" quaternary :disabled="i === sepStemOrder.length - 1" @click="moveSepStem(i, 1)">↓</n-button>
              </span>
            </div>
          </div>
        </div>
        <template #footer>
          <div class="sep-modal-footer">
            <n-button size="small" @click="resetSepNaming">{{ t('separate.namingReset') }}</n-button>
            <n-button size="small" type="primary" @click="showSepNaming = false">{{ t('common.close') }}</n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

    <!-- 高级设置弹窗（复刻分离页）：运行选项 / 输出编码 / 高级推理参数 -->
    <n-modal v-model:show="showSepAdvanced" style="width: var(--modal-width-md)">
      <n-card :title="t('separate.configParams')" :bordered="false" size="small">
        <div class="sep-modal-sec">
          <div class="sep-modal-label">{{ t('separate.runOptionsTitle') }}</div>
          <div class="sep-modal-hint">{{ t('separate.runOptionsHint') }}</div>
          <n-checkbox v-model:checked="sepUseTta">{{ t('separate.tta') }}</n-checkbox>
        </div>
        <div class="sep-modal-sec">
          <div class="sep-modal-label">{{ t('separate.audioQualityTitle') }} · {{ (sepFormat || 'wav').toUpperCase() }}</div>
          <div class="sep-modal-hint">{{ t('separate.audioQualityEditable') }}</div>
          <div v-if="sepFormat === 'mp3'" class="sep-adv-row">
            <span class="sep-adv-label">{{ t('audio.mp3BitRate') }}</span>
            <n-select v-model:value="sepMp3Bitrate" size="small" style="width: 160px" :options="sepMp3BitrateOptions" />
          </div>
          <n-checkbox v-model:checked="sepNormalize">{{ t('inference.normalize') }}</n-checkbox>
        </div>
        <div class="sep-modal-sec">
          <div class="sep-modal-label">{{ t('inference.advancedParams') }}</div>
          <div class="sep-modal-hint">{{ t('separate.advancedPanelHint') }}</div>
          <div class="sep-adv-grid">
            <div class="sep-adv-field">
              <span class="sep-adv-label">{{ t('inference.batchSize') }}</span>
              <n-input-number v-model:value="sepBatchSize" size="small" :min="1" placeholder="默认" clearable />
            </div>
            <div class="sep-adv-field">
              <span class="sep-adv-label">{{ t('inference.overlapSize') }}</span>
              <n-input-number v-model:value="sepOverlapSize" size="small" :min="0" placeholder="默认" clearable />
            </div>
            <div class="sep-adv-field">
              <span class="sep-adv-label">{{ t('inference.numOverlap') }}</span>
              <n-input-number v-model:value="sepNumOverlap" size="small" :min="0" placeholder="默认" clearable />
            </div>
            <div class="sep-adv-field">
              <span class="sep-adv-label">{{ t('inference.chunkSize') }}</span>
              <n-input-number v-model:value="sepChunkSize" size="small" :min="0" placeholder="默认" clearable />
            </div>
          </div>
          <n-checkbox v-model:checked="sepStandardize">{{ t('inference.standardize') }}</n-checkbox>
        </div>
        <template #footer>
          <div class="sep-modal-footer">
            <n-button size="small" type="primary" @click="showSepAdvanced = false">{{ t('common.close') }}</n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showQueueModal" style="width: var(--modal-width-md)">
      <n-card
        class="lyrics-queue-modal"
        :title="t('lyrics.taskQueue')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="showQueueModal = false"
      >
        <template #header-extra>
          <n-button
            v-if="finishedJobCount" size="small" quaternary
            @click="clearFinishedJobs"
          >{{ t('lyrics.clearFinishedJobs') }}</n-button>
        </template>
        <n-radio-group v-model:value="queueScope" size="small" class="queue-scopes">
          <n-radio-button value="session">{{ t('lyrics.queueCurrent') }} · {{ sessionJobs.length }}</n-radio-button>
          <n-radio-button value="history">{{ t('lyrics.queueHistory') }} · {{ historyJobs.length }}</n-radio-button>
        </n-radio-group>
        <div v-if="queueJobs.length" class="queue-list">
          <article
            v-for="job in queueJobs"
            :key="job.id"
            class="queue-row"
            :class="`queue-row--${job.status}`"
          >
            <div class="queue-row__main">
              <span class="queue-row__status">
                <i></i>
                {{ jobStatusLabel(job.status) }}
              </span>
              <strong :title="job.model">{{ job.model }}</strong>
              <span class="queue-row__input" :title="job.input">{{ job.input.split(/[\\/]/).pop() }}</span>
              <n-progress
                type="line"
                :percentage="job.status === 'done' ? 100 : job.percent"
                :show-indicator="false"
                :height="5"
                :border-radius="3"
              />
              <span v-if="job.error" class="queue-row__error" :title="job.error">{{ job.error }}</span>
              <span class="queue-row__time">{{ fmtJobTime(job.time) }}</span>
            </div>
            <div v-if="job.lineCount" class="queue-row__meta">
              <span>{{ job.lineCount }} 行歌词</span>
            </div>
            <div v-if="job.preview?.length" class="queue-row__preview">
              <div v-for="(pl, pi) in job.preview" :key="pi" class="queue-row__preview-line">{{ pl }}</div>
            </div>
            <div class="queue-row__actions">
              <template v-if="job.status === 'done' && Object.keys(job.outputs).length">
                <n-button
                  v-for="key in ['lrc', 'srt', 'ttml']"
                  :key="key"
                  v-show="job.outputs[key]"
                  size="tiny" secondary type="primary"
                  @click="revealOutput(job, key)"
                >下载 {{ key.toUpperCase() }}</n-button>
                <n-button
                  v-if="job.outputs.player || Object.values(job.outputs).some((v) => v.endsWith('.player.html'))"
                  size="tiny" secondary circle title="打开播放器" @click="openPlayer(job)"
                >🎵</n-button>
                <n-button
                  v-if="job.outputs.lrc"
                  size="tiny" secondary circle title="打开 LRC" @click="reveal(job.outputs.lrc)"
                >LRC</n-button>
              </template>
              <n-button
                v-if="job.status === 'done' && Object.keys(job.outputs).length"
                size="tiny" secondary @click="reveal(Object.values(job.outputs)[0])"
              >{{ t('lyrics.openFolder') }}</n-button>
              <n-button size="tiny" quaternary type="error" @click="askDeleteJob(job)">🗑</n-button>
            </div>
          </article>
        </div>
        <n-empty v-else size="small" :description="queueScope === 'session' ? t('lyrics.queueEmptyCurrent') : t('lyrics.queueEmptyHistory')" />
      </n-card>
    </n-modal>
    <!-- 应用内播放器（音乐按钮）：原生实现，媒体走 asset 协议 -->
    <n-modal v-model:show="playerModal" preset="card" style="width: min(1100px, 92vw)" :title="t('lyrics.playerTitle')">
      <div v-if="playerModal && playerLines.length" class="mini-player">
        <div class="mp-left">
          <div class="mp-cover">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/></svg>
          </div>
          <div class="mp-meta">
            <div class="mp-name">{{ playerTitle }}</div>
          </div>
          <div class="mp-controls">
            <span class="mp-time">{{ fmtAudioTime(playerNow / 1000) }}</span>
            <input type="range" min="0" max="1000" value="0" @input="onPlayerSeek"
                   style="flex: 1; accent-color: #e8b4bc">
            <span class="mp-time">{{ fmtAudioTime(playerDur / 1000) }}</span>
            <n-button circle type="primary" @click="togglePlayerPlay">{{ playerPlaying ? '⏸' : '▶' }}</n-button>
          </div>
          <audio
            v-if="playerMediaSrc"
            ref="playerAudioEl"
            :src="playerMediaSrc"
            preload="auto"
            @timeupdate="onPlayerTime"
            @loadedmetadata="onPlayerMeta"
            @play="playerPlaying = true"
            @pause="playerPlaying = false"
          ></audio>
        </div>
        <div class="mp-right">
          <div class="mp-scroll" ref="mpScrollEl">
            <div
              v-for="(ln, i) in playerLines"
              :key="i"
              class="mp-line"
              :class="{ 'mp-active': i === playerActive, 'mp-past': i < playerActive, 'mp-bg': ln.bg }"
            >
              <span v-for="(w, k) in ln.words" :key="k" class="mp-char">{{ w.char }}</span>
            </div>
          </div>
        </div>
      </div>
      <n-empty v-else size="small" :description="t('lyrics.noPlayer')" />
    </n-modal>
    <!-- 删除历史记录：选择是否连带删除产物文件 -->
    <n-modal v-model:show="deleteAsk" preset="dialog" type="warning"
             :title="t('lyrics.deleteAskTitle')"
             :positive-text="t('lyrics.deleteConfirm')"
             :negative-text="t('common.cancel')"
             @positive-click="confirmDeleteJob">
      <div style="display: flex; flex-direction: column; gap: 8px">
        <n-radio v-model:checked="deleteWithFiles" :value="true">
          {{ t('lyrics.deleteWithFiles') }}
        </n-radio>
        <n-radio :checked="!deleteWithFiles" @update:checked="deleteWithFiles = false">
          {{ t('lyrics.deleteRecordOnly') }}
        </n-radio>
        <n-text depth="3" style="font-size: 11px">{{ t('lyrics.deleteWithFilesHint') }}</n-text>
      </div>
    </n-modal>
    <!-- 批量清除已完成：选择是否连带删除产物文件 -->
    <n-modal v-model:show="clearAsk" preset="dialog" type="warning"
             :title="t('lyrics.clearAskTitle')"
             :positive-text="t('lyrics.deleteConfirm')"
             :negative-text="t('common.cancel')"
             @positive-click="confirmClearFinished">
      <div style="display: flex; flex-direction: column; gap: 8px">
        <n-radio v-model:checked="clearWithFiles" :value="true">
          {{ t('lyrics.deleteWithFiles') }}
        </n-radio>
        <n-radio :checked="!clearWithFiles" @update:checked="clearWithFiles = false">
          {{ t('lyrics.deleteRecordOnly') }}
        </n-radio>
        <n-text depth="3" style="font-size: 11px">{{ t('lyrics.clearWithFilesHint') }}</n-text>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.lyrics-layout {
  display: grid;
  grid-template-columns: 340px 6px minmax(0, 1fr);
  gap: 8px;
  align-items: start;  /* 两栏各自自然高度，绝不拉伸 */
}
.layout-splitter {
  width: 6px;
  min-height: 200px;
  align-self: stretch;
  cursor: col-resize;
  border-radius: 3px;
  background: transparent;
  transition: background 0.15s;
  touch-action: none;
}
.layout-splitter:hover,
.layout-splitter.splitting {
  background: rgba(99, 102, 241, 0.35);
}
.lyrics-options {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.25));
}
.field { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.field label { font-size: 12px; opacity: 0.75; }
.field-row { display: flex; gap: 12px; }
.actions { display: flex; gap: 8px; margin-top: 4px; }
.models-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.25));
}
/* 模型管理页头 + 筛选工具栏（仿主界面模型库） */
.models-title-row { display: flex; align-items: center; gap: 8px; }
.models-tab-title { font-size: 22px; margin: 0; }
.header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.toolbar {
  margin-bottom: 12px;
  padding: 10px;
  border: 1px solid rgba(128, 128, 128, 0.25);
  border-radius: 12px;
  background: rgba(128, 128, 128, 0.05);
}
.toolbar-row {
  display: grid;
  grid-template-columns: minmax(200px, 1fr) minmax(130px, 180px) minmax(110px, 150px) minmax(110px, 150px) auto;
  align-items: center;
  gap: 10px;
}
.search-input { min-width: 0; }
.toolbar-actions { display: flex; align-items: center; gap: 7px; white-space: nowrap; }
.text-sm { font-size: 12px; }
.text-muted { opacity: 0.65; }
/* 视图切换（列表/卡片网格），与主界面模型库一致 */
.view-toggle {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.12);
  box-shadow: inset 0 0 0 1px rgba(128, 128, 128, 0.35);
  flex-shrink: 0;
}
.view-toggle__button {
  display: grid;
  place-items: center;
  width: 26px;
  height: 22px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  opacity: 0.65;
  cursor: pointer;
  font-size: 14px;
  transition: background 150ms ease, color 150ms ease;
}
.view-toggle__button:hover { opacity: 1; }
.view-toggle__button--active {
  background: rgba(128, 128, 128, 0.15);
  opacity: 1;
}
/* 模型列表/卡片网格 */
.model-grid { display: grid; align-content: start; gap: 8px; }
.model-grid--list { grid-template-columns: minmax(0, 1fr); }
.model-grid--card {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 270px), 1fr));
  align-items: stretch;
  gap: 12px;
}
.model-grid--card .model-row {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid rgba(128, 128, 128, 0.25);
  border-radius: 12px;
  background: rgba(128, 128, 128, 0.05);
}
.model-grid--card .model-actions { flex-wrap: wrap; }
.model-grid--card .model-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.models-head { display: flex; align-items: center; justify-content: space-between; }
.model-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.18));
}
.model-info { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.model-name {
  font-family: monospace;
  font-size: 12px;
  white-space: normal;   /* 长名换行显示完整，不截断（Ultra / Ultra+Head 尾部才不同） */
  word-break: break-all;
  max-width: 300px;
}
.model-meta { display: flex; align-items: center; gap: 6px; }
.model-size { font-size: 11px; opacity: 0.65; }
.model-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
/* 下载中：纵向堆叠布局 */
.model-row-dl {
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  padding: 8px 10px;
}
.dl-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.dl-name { flex: 1; max-width: none; }
.dl-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.dl-meta { font-size: 11px; opacity: 0.7; font-family: monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.dl-btns { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
/* 下载详情弹窗 */
.detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.detail-model { font-family: monospace; font-size: 14px; font-weight: 600; }
.detail-sub { display: flex; align-items: center; gap: 8px; margin-top: 4px; min-width: 0; }
.detail-file {
  font-size: 12px; opacity: 0.65; font-family: monospace;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 380px;
}
.detail-percent { font-size: 34px; font-weight: 700; line-height: 1; }
.detail-percent span { font-size: 16px; opacity: 0.6; margin-left: 2px; }
.detail-grid { display: grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 10px; margin-top: 14px; }
.detail-label { font-size: 12px; opacity: 0.65; margin-bottom: 2px; }
.detail-value { font-size: 14px; font-family: monospace; }
.detail-logs-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-top: 14px; margin-bottom: 6px; font-size: 13px;
}
.detail-logs {
  background: #10151c; color: #cfe3d4; border-radius: 6px;
  padding: 10px 12px; max-height: 200px; overflow-y: auto;
  font-family: monospace; font-size: 12px; line-height: 1.6;
}
.detail-log-line { display: flex; gap: 10px; white-space: pre-wrap; word-break: break-all; }
.detail-log-idx { opacity: 0.45; flex-shrink: 0; }
.detail-log-empty { opacity: 0.4; text-align: center; }
.detail-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.detail-hint { font-size: 12px; opacity: 0.6; }
.lyrics-results { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.search-panel { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.search-row { display: flex; gap: 6px; }
.search-result { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 4px 8px; border: 1px solid rgba(128,128,128,0.2); border-radius: 6px; }
.search-meta { min-width: 0; display: flex; flex-direction: column; }
.search-name { font-size: 12px; }
.search-artist { font-size: 11px; opacity: 0.65; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lyrics-editor { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; padding: 10px; border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; max-height: 420px; overflow: auto; }
.editor-hint { font-size: 12px; opacity: 0.65; }
.editor-row { display: flex; align-items: center; gap: 6px; }
.editor-id { font-family: monospace; font-size: 11px; opacity: 0.6; width: 32px; flex-shrink: 0; }
.editor-actions { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
.lyrics-tabs { width: 100%; }
.editor-page { display: flex; flex-direction: column; gap: 8px; }
.editor-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.editor-table { display: flex; flex-direction: column; gap: 4px; max-height: calc(100vh - 260px); overflow: auto; padding-right: 4px; }
.editor-head, .editor-row { display: flex; align-items: center; gap: 6px; }
.editor-head { font-size: 11px; opacity: 0.6; position: sticky; top: 0; z-index: 1; background: var(--surface-1, #fff); padding: 4px 0; }
.editor-head .editor-id, .editor-row .editor-id { width: 110px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.editor-cell-time { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
.editor-cell-text { flex: 1; min-width: 160px; }
.editor-cell-trans { width: 180px; flex-shrink: 0; }
.editor-page .editor-actions { padding: 8px 0 2px; background: var(--surface-1, #fff); }
.editor-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.editor-row-active { background: rgba(99, 102, 241, 0.18); border-radius: 6px; padding: 2px 4px; }
.playback-bar { display: flex; align-items: center; gap: 10px; padding: 6px 10px; border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; }
.playback-time { font-family: monospace; font-size: 12px; min-width: 64px; }
.switch-row { display: flex; align-items: center; gap: 8px; }
/* 开关紧凑布局：两列并排 chip，说明文字放悬停提示 */
.switch-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
/* 分离串联「输出设置」卡片（复刻分离页 02 卡片，紧凑版） */
.sep-outset {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(128, 128, 128, 0.25);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sep-outset-head { display: flex; align-items: center; gap: 8px; }
.sep-outset-badge {
  width: 20px; height: 20px; border-radius: 6px;
  background: rgba(99, 102, 241, 0.15); color: #6366f1;
  font-size: 11px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.sep-outset-title { font-size: 12.5px; font-weight: 600; }
.sep-outset-hint { font-size: 11px; opacity: 0.5; }
.sep-two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.sep-seg {
  display: flex; border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 6px; overflow: hidden;
}
.sep-seg button {
  flex: 1; padding: 4px 0; font-size: 12px; border: none;
  background: transparent; color: inherit; cursor: pointer;
}
.sep-seg button + button { border-left: 1px solid rgba(128, 128, 128, 0.3); }
.sep-seg button.on { background: rgba(99, 102, 241, 0.18); color: #6366f1; font-weight: 600; }
.sep-naming { display: flex; align-items: center; gap: 8px; }
.sep-stems { display: flex; gap: 14px; }
.sep-outset-advanced { margin-left: auto; }
.sep-dirrow { display: flex; gap: 6px; align-items: center; }
.sep-dirrow .n-input { flex: 1; }
/* 命名 / 高级设置弹窗（复刻分离页） */
.sep-modal-sec {
  border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 8px;
  padding: 12px 14px; margin-bottom: 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.sep-modal-label { font-size: 13px; font-weight: 600; }
.sep-modal-hint { font-size: 11px; opacity: 0.55; }
.sep-naming-preview { display: flex; flex-direction: column; gap: 6px; }
.sep-naming-preview-row {
  border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 6px;
  padding: 6px 10px; font-size: 12.5px;
}
.sep-naming-preview-idx { opacity: 0.5; margin-right: 8px; }
.sep-token-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.sep-token-chip {
  display: flex; align-items: center; gap: 6px;
  border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 16px;
  padding: 4px 10px; font-size: 12px; background: transparent; color: inherit; cursor: pointer;
}
.sep-token-chip:hover { border-color: #6366f1; }
.sep-token-chip code { color: #6366f1; }
.sep-order-list { display: flex; flex-direction: column; gap: 6px; }
.sep-order-row {
  display: flex; align-items: center; gap: 10px;
  border: 1px solid rgba(128, 128, 128, 0.25); border-radius: 6px; padding: 6px 10px;
}
.sep-order-idx { opacity: 0.5; font-size: 12px; }
.sep-order-name { flex: 1; font-weight: 600; font-size: 13px; }
.sep-order-btns { display: flex; gap: 2px; }
.sep-adv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.sep-adv-field { display: flex; flex-direction: column; gap: 4px; }
.sep-adv-row { display: flex; align-items: center; gap: 12px; }
.sep-adv-label { font-size: 12px; opacity: 0.75; }
.sep-modal-footer { display: flex; justify-content: flex-end; gap: 10px; }
/* 扫描配对日志面板（每次扫描清空重写；头部可点击折叠） */
.scan-log {
  border: 1px solid rgba(128, 128, 128, 0.25); border-radius: 6px;
  display: flex; flex-direction: column; overflow: hidden;
}
.scan-log-head {
  all: unset; display: flex; align-items: center; justify-content: space-between;
  width: 100%; box-sizing: border-box; cursor: pointer; user-select: none;
  font-size: 12px; opacity: 0.75; padding: 6px 10px;
  border-bottom: 1px solid rgba(128,128,128,0.15);
}
.scan-log-head:hover { opacity: 1; background: rgba(128,128,128,0.08); }
.scan-log-body {
  max-height: 220px; overflow-y: auto;
  padding: 6px 10px; font-family: var(--font-mono, monospace); font-size: 11px;
  display: flex; flex-direction: column; gap: 2px;
}
.scan-log-line { white-space: nowrap; }
.scan-log-line.warn { color: #e88080; }
/* 拖拽导入：拖到哪个字段哪个字段高亮（输入文件 / 歌词 各自独立识别） */
.drag-zone { transition: outline-color 0.12s ease, box-shadow 0.12s ease; }
.drag-zone--active {
  outline: 2px dashed #6366f1;
  outline-offset: 3px;
  border-radius: 8px;
  box-shadow: 0 0 0 5px rgba(99, 102, 241, 0.12);
}
.switch-chip { display: flex; align-items: center; gap: 8px; min-width: 0; }
.switch-chip .n-switch { flex-shrink: 0; }
.switch-label { font-size: 12px; opacity: 0.85; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.queue-scopes { margin-bottom: 10px; }
.queue-list { display: flex; flex-direction: column; gap: 8px; max-height: 420px; overflow: auto; }
.queue-row {
  display: flex; flex-direction: column; align-items: stretch; gap: 8px;
  padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--n-border-color, rgba(128,128,128,0.25));
}
.queue-row--running { border-color: rgba(32, 128, 240, 0.45); }
.queue-row--failed { border-color: rgba(208, 48, 79, 0.45); }
.queue-row__main {
  display: flex; flex-direction: row; align-items: center; gap: 10px;
  min-width: 0; flex-wrap: wrap;
}
.queue-row__status { flex-shrink: 0; }
.queue-row__time { margin-left: auto; flex-shrink: 0; }
.queue-row__status { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; opacity: 0.8; }
.queue-row__status i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.queue-row--done .queue-row__status { color: #18a058; }
.queue-row--running .queue-row__status { color: #2080f0; }
.queue-row--failed .queue-row__status { color: #d03050; }
.queue-row--cancelled .queue-row__status { opacity: 0.5; }
.queue-row strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queue-row__input { font-size: 11px; opacity: 0.7; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queue-row__error { font-size: 11px; color: #d03050; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queue-row__time { font-size: 11px; opacity: 0.55; }
.queue-row__actions {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px; flex-shrink: 0;
}
.switch-state { font-size: 12px; opacity: 0.7; }
.switch-state.on { opacity: 1; color: #2080f0; font-weight: 500; }
.log-panel { border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; overflow: hidden; }
.results-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 2px;
}
.results-title { font-size: 13px; font-weight: 500; opacity: 0.85; }
button.log-head, button.outputs-head {
  all: unset; display: flex; align-items: center; justify-content: space-between;
  width: 100%; box-sizing: border-box; cursor: pointer; user-select: none;
  font-size: 12px; opacity: 0.75; padding: 6px 10px;
  border-bottom: 1px solid rgba(128,128,128,0.15);
}
button.log-head:hover, button.outputs-head:hover { opacity: 1; background: rgba(128,128,128,0.08); }
button.outputs-head { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.log-toggle { font-family: var(--font-mono, monospace); font-size: 11px; }
.imported-lyrics-panel {
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.25));
}
.imported-lyrics-body {
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  line-height: 1.7;
  opacity: 0.9;
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(128, 128, 128, 0.08);
}
.log-head { font-size: 12px; opacity: 0.7; padding: 6px 10px; border-bottom: 1px solid rgba(128,128,128,0.15); }
.log-body { max-height: 320px; overflow: auto; padding: 6px 10px; font-family: var(--font-mono, monospace); font-size: 11px; display: flex; flex-direction: column; gap: 2px; }
.log-line { white-space: pre-wrap; word-break: break-all; opacity: 0.85; }
.section-title { margin: 10px 0 6px; font-size: 13px; font-weight: 500; opacity: 0.85; }
.ttml-btns { display: flex; gap: 6px; }
.progress-block, .outputs-block, .lyrics-preview {
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.25));
}
.stage-line { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; font-size: 13px; }
.progress-line { display: flex; gap: 8px; align-items: center; }
.progress-label { font-size: 12px; opacity: 0.7; min-width: 34px; }
.options-title { margin: 0; font-size: 14px; }
.stage-key {
  font-family: monospace;
  font-size: 12px;
  padding: 0 6px;
  border-radius: 4px;
  background: rgba(99, 102, 241, 0.15);
  color: #63e6be;
}
h3 { margin: 0 0 10px; font-size: 14px; }
.preview-scroll {
  max-height: calc(100vh - 260px);
  overflow: auto;
  padding-right: 4px;
}
.outputs-block .output-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 13px;
}
.output-key {
  min-width: 90px;
  font-size: 12px;
  opacity: 0.7;
}
.line-block { margin-bottom: 10px; }
.line-head { display: flex; gap: 10px; align-items: baseline; margin-bottom: 3px; }
.line-id { font-size: 11px; opacity: 0.55; font-family: monospace; }
.line-text { font-size: 14px; }
.line-time { font-size: 11px; opacity: 0.55; font-family: monospace; margin-left: auto; }
.char-strip { display: flex; gap: 2px; }
.char-box {
  min-width: 20px;
  text-align: center;
  padding: 2px 0;
  font-size: 12px;
  border-radius: 3px;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
}
</style>

<style scoped>
/* 应用内迷你播放器 */
.mini-player { display: flex; gap: 0; height: 72vh; border-radius: 8px; overflow: hidden; }
.mp-left {
  width: 36%; min-width: 240px; display: flex; flex-direction: column;
  justify-content: center; align-items: center; gap: 16px; padding: 32px;
  background: #1c1c22; color: #f2f2f7;
}
.mp-cover {
  width: min(200px, 55%); aspect-ratio: 1; background: #6a5c68; border-radius: 20px;
  display: flex; align-items: center; justify-content: center;
}
.mp-cover svg { width: 50%; height: 50%; opacity: 0.55; color: #8a7888; }
.mp-meta { width: min(220px, 70%); }
.mp-name { font-size: 20px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mp-controls { width: min(220px, 70%); display: flex; align-items: center; gap: 8px; }
.mp-time { color: rgba(242,242,247,.6); font-size: 12px; min-width: 46px; text-align: center; white-space: nowrap; }
.mp-right { flex: 1; position: relative; overflow: hidden; background: linear-gradient(135deg, #242028 0%, #1c1c22 100%); }
.mp-scroll { position: absolute; inset: 0; overflow-y: auto; padding: 20vh 40px 60vh; }
.mp-line {
  font-size: clamp(18px, 2.4vw, 32px); font-weight: 700; margin: 26px 0;
  color: rgba(242,242,247,.3); transition: color .3s, transform .3s;
  transform-origin: left center; cursor: default;
}
.mp-line.mp-past { color: rgba(242,242,247,.55); }
.mp-line.mp-active { color: #f2f2f7; transform: scale(1.06); }
.mp-line.mp-bg { font-size: clamp(13px, 1.5vw, 19px); font-weight: 500; }
.mp-char { color: rgba(242,242,247,.3); }
.mp-line.mp-active .mp-char {
  background: linear-gradient(to right, #e8b4bc 0%, #e8b4bc var(--p, 0%), rgba(242,242,247,.3) var(--p, 0%));
  -webkit-background-clip: text; background-clip: text;
  color: transparent; -webkit-text-fill-color: transparent;
}
.mp-line.mp-past .mp-char { color: rgba(242,242,247,.55); }
</style>

/* 字幕预览小窗 */
.pv-stage { display: flex; flex-direction: column; gap: 10px; }
.pv-screen {
  aspect-ratio: 16 / 9; background: linear-gradient(160deg, #2a2430 0%, #141418 70%);
  border-radius: 10px; position: relative; overflow: hidden;
}
.pv-scroll { position: absolute; inset: 0; }
.pv-line {
  display: none; position: absolute; left: 24px; right: 24px; text-align: center;
  font-weight: 700; color: #f2f2f7;
  text-shadow: 0 2px 6px rgba(0, 0, 0, 0.8);
}
.pv-line.pv-active { display: block; font-size: clamp(22px, 3.4vw, 36px); }
.pv-line.pv-next {
  display: block; font-size: clamp(16px, 2.2vw, 24px);
  color: rgba(242, 242, 247, 0.4); bottom: 8%;
}
.pv-line.pv-active.pv-next { font-size: clamp(22px, 3.4vw, 36px); }
.pv-char { color: rgba(242, 242, 247, 0.92); }
.pv-line.pv-active .pv-char {
  background: linear-gradient(to right, #ffd66e 0%, #ffd66e var(--p, 0%), rgba(242,242,247,.92) var(--p, 0%));
  -webkit-background-clip: text; background-clip: text;
  color: transparent; -webkit-text-fill-color: transparent;
}
.pv-mode-line .pv-char { color: #f2f2f7; }
.pv-bar { display: flex; align-items: center; gap: 10px; }
.pv-time { color: rgba(128, 128, 128, 0.8); font-size: 12px; min-width: 46px; text-align: center; white-space: nowrap; }

.model-group { margin-bottom: 14px; }
.model-group-title {
  font-size: 12px; font-weight: 600; opacity: 0.75;
  padding: 4px 2px 6px; border-bottom: 1px dashed rgba(128, 128, 128, 0.25);
  margin-bottom: 8px; display: flex; align-items: center; gap: 6px;
}
.model-group-count {
  font-size: 10px; opacity: 0.6; font-weight: normal;
  background: rgba(128, 128, 128, 0.12); border-radius: 8px; padding: 0 6px;
}

/* 流程分区标题 */
.flow-title {
  font-size: 12px; font-weight: 700; opacity: 0.85;
  padding: 6px 8px; margin: 4px 0 8px; border-left: 3px solid rgba(99, 91, 255, 0.55);
  background: rgba(128, 128, 128, 0.07); border-radius: 0 6px 6px 0;
}
