<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NButton, NEmpty, NInput, NModal, NPopconfirm, NSelect, NSpin, NTable, NTag,
  type SelectOption,
} from 'naive-ui'
import api from '../api'
import { useRouter } from 'vue-router'
import SqlExploreModal from './SqlExploreModal.vue'
import SortPrefsModal from './SortPrefsModal.vue'

interface TableItem {
  name: string
  rows_estimate: number
  comment: string
  update_time: string | null
  data_bytes: number
}
interface ColMeta {
  name: string
  data_type: string
  column_type: string
  nullable: boolean
  is_primary: boolean
  comment: string
}
interface RowResp {
  rows: Record<string, unknown>[]
  has_more: boolean
  offset: number
  count: number
}
interface FilterCond {
  col: string
  op: string
  val: string
}
interface DqIssue {
  rule_name: string
  severity: string
  status: string
  metric_value: string | null
  message: string | null
}
interface DqTableStatus {
  table_name: string
  worst: 'pass' | 'warning' | 'fail' | 'error'
  counts: { pass: number; warning: number; fail: number; error: number; total: number }
  issues: DqIssue[]
}
interface DqTableResp {
  run_at: string | null
  items: DqTableStatus[]
}
interface RunStep {
  no: number
  name: string
  params?: string | null
  value?: string | null
}
interface FlowLast {
  status: string | null
  started_at: string | null
  finished_at: string | null
  records_written: number | null
  error_message: string | null
  run_detail: {
    run_steps?: RunStep[] | null
    source_stats?: Record<string, number> | null
    duration?: string | null
    cutoff?: string | null
    stale_cutoff?: string | null
    skipped?: number | null
    skipped_stale?: number | null
    skipped_bj?: number | null
    status?: string | null
  } | null
}
interface FlowLineage {
  source: string | null
  cols: string[]
  derived: string[]
  note: string | null
  col_notes: Record<string, string>
}
interface FlowJob {
  task_name: string
  cron: string | null
  cron_human?: string | null
  enabled: boolean | null
  scheduled: boolean | null
  next_run: string | null
  running: boolean | null
  implemented: boolean | null
  last: FlowLast | null
  lineage: FlowLineage | null
  run_steps?: RunStep[] | null
}
interface FlowInfo {
  table_name: string
  category: string | null
  source_desc: string | null
  flow_desc: string | null
  note: string | null
  writers: string[]
  jobs: FlowJob[]
}

const tables = ref<TableItem[]>([])
const tableKeyword = ref('')
const current = ref('')

// 数据探查（SQL EXPLORER）弹窗状态
const showSql = ref(false)

const cols = ref<ColMeta[]>([])
const rows = ref<Record<string, unknown>[]>([])
const meta = ref<{
  comment: string
  update_time: string | null
  estimated_rows: number
  exact_rows: number | null
  is_estimate: boolean
} | null>(null)

const loadingTables = ref(true)
const loadingRows = ref(false)
const pageSize = ref(50)
const offset = ref(0)
const hasMore = ref(false)
const sort = ref<{ col: string; dir: 'asc' | 'desc' } | null>(null)

// ---------- 排序偏好（服务端持久化 + localStorage 镜像） ----------
type SortPref = { col: string; dir: 'asc' | 'desc' }
const sortPrefs = ref<Record<string, SortPref>>({})
const sortPrefsReady = ref(false)
const showSortPrefs = ref(false)
const SORT_PREFS_KEY = 'investbuddy.tableSortPrefs'

function sortPrefsLS(): Record<string, SortPref> {
  try {
    const raw = localStorage.getItem(SORT_PREFS_KEY)
    return raw ? (JSON.parse(raw) as Record<string, SortPref>) : {}
  } catch {
    return {}
  }
}
function saveSortPrefsLS(map: Record<string, SortPref>) {
  try {
    localStorage.setItem(SORT_PREFS_KEY, JSON.stringify(map))
  } catch {
    /* 存储不可用则忽略，不阻塞主流程 */
  }
}

async function loadSortPrefs() {
  // 先以本地镜像兜底（后端未起/首屏快），再拉服务端全量覆盖
  sortPrefs.value = sortPrefsLS()
  sortPrefsReady.value = true
  try {
    const resp: any = await api.get('/prefs/table-sorts', { silent: true })
    const items = (resp?.items || {}) as Record<string, SortPref>
    sortPrefs.value = items
    saveSortPrefsLS(items)
  } catch {
    /* 服务端不可达：保持镜像值，保证离线也能体验记住的排序 */
  }
}

/** 排序偏好服务端 API：单表 upsert / 单表删 / 全清 */
function apiPutSortPref(table: string, p: SortPref) {
  try {
    void api.put(`/prefs/table-sorts/${encodeURIComponent(table)}`, p, { silent: true })
  } catch {
    /* 静默 */
  }
}
function apiDeleteSortPref(table: string) {
  try {
    void api.delete(`/prefs/table-sorts/${encodeURIComponent(table)}`, { silent: true })
  } catch {
    /* 静默 */
  }
}
function apiClearSortPrefs() {
  try {
    void api.delete('/prefs/table-sorts', { silent: true })
  } catch {
    /* 静默 */
  }
}

/** 当前表排序三态切换后：user 明确排序则 upsert，否则删除该表偏好 */
function persistCurrentSort() {
  if (!current.value) return
  if (sort.value && sort.value.col) {
    const p = { col: sort.value.col, dir: sort.value.dir }
    sortPrefs.value = { ...sortPrefs.value, [current.value]: p }
    saveSortPrefsLS(sortPrefs.value)
    apiPutSortPref(current.value, p)
  } else {
    const next = { ...sortPrefs.value }
    delete next[current.value]
    sortPrefs.value = next
    saveSortPrefsLS(next)
    apiDeleteSortPref(current.value)
  }
}

/** 管理弹窗单条重置 */
function resetSortPref(table: string) {
  const next = { ...sortPrefs.value }
  delete next[table]
  sortPrefs.value = next
  saveSortPrefsLS(next)
  apiDeleteSortPref(table)
  // 若当前正看这张表且在用它的已存排序 → 清掉当前排序回到自然序
  if (current.value === table && sort.value) {
    sort.value = null
    loadRows(false)
  }
}

/** 管理弹窗全部清空 */
function clearSortPrefs() {
  sortPrefs.value = {}
  saveSortPrefsLS({})
  apiClearSortPrefs()
  if (sort.value) {
    sort.value = null
    loadRows(false)
  }
}

/** 打开表后应用该表已记住的排序（列须仍存在，否则自然序） */
function applySortPref(name: string) {
  const p = sortPrefs.value[name]
  if (p && cols.value.some((c) => c.name === p.col)) {
    sort.value = { col: p.col, dir: p.dir }
  } else {
    sort.value = null
  }
}

// 过滤器草稿 + 已生效条件
const fCol = ref('')
const fOp = ref('eq')
const fVal = ref('')
const filters = ref<FilterCond[]>([])

interface DqReportItem {
  id: number
  rule_name: string
  table_name: string
  check_type: string
  severity: string
  status: 'pass' | 'warning' | 'fail' | 'error'
  metric_value: string | null
  message: string | null
}
interface DqReportResp {
  run_at: string | null
  items: DqReportItem[]
}
const router = useRouter()
const dqRunAt = ref<string | null>(null)
const dqStatus = ref<Map<string, DqTableStatus>>(new Map())
const flowMap = ref<Map<string, FlowInfo>>(new Map())

// 规则明细弹窗
const dqModalOpen = ref(false)
const dqModalTable = ref('')
const dqModalRunAt = ref<string | null>(null)
const dqModalLoading = ref(false)
const dqModalRows = ref<DqReportItem[]>([])
const dqModalFocusRule = ref('')

// 数据流明细弹窗（点击时重拉 flowMap，保证展示的是最新作业状态）
const flowModalOpen = ref(false)
const flowModalLoading = ref(false)

const NUM_TYPES = ['int', 'bigint', 'smallint', 'tinyint', 'decimal', 'float', 'double', 'numeric']
const DATE_TYPES = ['date', 'datetime', 'timestamp', 'time']
const CHANGE_RE = /(pct|chg|change|涨跌幅|涨跌)/

const filteredTables = computed(() => {
  const kw = tableKeyword.value.trim().toLowerCase()
  if (!kw) return tables.value
  return tables.value.filter(
    (t) => t.name.toLowerCase().includes(kw) || t.comment.toLowerCase().includes(kw),
  )
})

// ---------- 左侧表分组：4 大组 × 17 细分类 二级折叠（2026-09-10 改造） ----------
// 一级大组：业务数据 / 质量与体检 / 系统与配置 / 备份归档
// 二级保留 table_meta 中已有 17 个细分类（行情/资料/质量/...）
// 备份归档 = 表名匹配 _bak_ 后缀（独立于 category）
const GROUPS: { key: string; title: string; icon: string; categories: string[] }[] = [
  {
    key: 'biz',
    title: '业务数据',
    icon: '📊',
    categories: ['行情', '资料', '概念', '日历', '基金', '资金', '财务', '债券', '指数', 'AI', '商品', '资讯', '行业', '调研', '基本面'],
  },
  {
    key: 'dq',
    title: '质量与体检',
    icon: '🛡️',
    categories: ['质量'],
  },
  {
    key: 'sys',
    title: '系统与配置',
    icon: '⚙️',
    categories: ['系统'],
  },
  {
    key: 'bak',
    title: '备份归档',
    icon: '📦',
    categories: ['__bak__'], // 哨兵：表名含 _bak_ 兜底归入
  },
]

function categoryOf(name: string): string | null {
  return flowMap.value.get(name)?.category ?? null
}
function isBackupTable(name: string): boolean {
  return /_bak_/i.test(name)
}
function groupKeyOf(name: string): string {
  if (isBackupTable(name)) return 'bak'
  const cat = categoryOf(name)
  for (const g of GROUPS) {
    if (cat && g.categories.includes(cat)) return g.key
  }
  return 'biz' // 未入库 table_meta 的表兜底归入业务数据
}
function categoryKeyOf(name: string): string {
  if (isBackupTable(name)) return '__bak__'
  return categoryOf(name) || '__未分类__'
}

// ---------- 表分类编辑（PATCH /db/tables/{name}/category → table_meta） ----------
const CATEGORY_OPTIONS: SelectOption[] = [
  ...GROUPS.filter((g) => g.key !== 'bak').flatMap((g) => g.categories),
].map((c) => ({ label: c, value: c }))
CATEGORY_OPTIONS.push({ label: '未分类', value: '' })

const catSaving = ref(false)
const catSavedTip = ref('')
let catTipTimer: ReturnType<typeof setTimeout> | undefined

async function setCategory(v: string) {
  if (!current.value) return
  if ((categoryOf(current.value) || '') === v) return // 未变化
  catSaving.value = true
  catSavedTip.value = ''
  try {
    await api.patch(`/db/tables/${current.value}/category`, { category: v })
    await loadFlow() // 重拉元数据：左侧树/信息栏/数据流卡全部联动更新
    catSavedTip.value = '已更新 ✓'
    if (catTipTimer) clearTimeout(catTipTimer)
    catTipTimer = setTimeout(() => (catSavedTip.value = ''), 2500)
  } catch {
    /* 失败提示由 api 拦截器全局 toast */
  } finally {
    catSaving.value = false
  }
}

interface CategoryBucket {
  key: string
  name: string
  tables: TableItem[]
}
interface GroupBucket {
  key: string
  title: string
  icon: string
  total: number
  categories: CategoryBucket[]
}

const groupedTables = computed<GroupBucket[]>(() => {
  // filteredTables 已经按搜索词过滤；此处按 group -> category 聚合
  const list = filteredTables.value
  const out: GroupBucket[] = GROUPS.map((g) => ({
    key: g.key, title: g.title, icon: g.icon, total: 0, categories: [],
  }))
  const groupMap = new Map(out.map((g) => [g.key, g]))
  const catMapByGroup = new Map<string, Map<string, CategoryBucket>>()
  out.forEach((g) => catMapByGroup.set(g.key, new Map()))
  // 固定二级分类顺序：按 GROUPS 声明顺序，未分类/备份哨兵放末尾（去重：__bak__ 同时是
  // 声明值与哨兵值，不去重会重复计入导致「单分类组」被误判为多分类 → 错误显示折叠箭头）
  const orderFor = (g: typeof GROUPS[number]) =>
    Array.from(new Set([...g.categories, '__未分类__', '__bak__']))
  list.forEach((t) => {
    const gk = groupKeyOf(t.name)
    const ck = categoryKeyOf(t.name)
    const gBucket = groupMap.get(gk)!
    gBucket.total += 1
    let catMap = catMapByGroup.get(gk)!
    if (!catMap.has(ck)) {
      const label = ck === '__未分类__' ? '未分类' : ck === '__bak__' ? '备份表' : ck
      catMap.set(ck, { key: ck, name: label, tables: [] })
    }
    catMap.get(ck)!.tables.push(t)
  })
  out.forEach((g, i) => {
    const declared = orderFor(GROUPS[i])
    const m = catMapByGroup.get(g.key)!
    // 二级分类：声明顺序 → 套用用户自定义顺序（增量覆盖，未记录项追加在后）
    const visibleCatKeys = declared.filter((k) => m.has(k))
    const orderedCatKeys = applyOrder(visibleCatKeys, sidebarOrder.value.cats[g.key])
    g.categories = orderedCatKeys.map((k) => {
      const c = m.get(k)!
      // 表行：默认按表名字母序 → 套用用户自定义顺序
      const byName = new Map(c.tables.map((t) => [t.name, t]))
      const defaultNames = [...c.tables]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((t) => t.name)
      const orderedNames = applyOrder(defaultNames, sidebarOrder.value.tables[`${g.key}::${k}`])
      return { ...c, tables: orderedNames.map((n) => byName.get(n)!) }
    })
  })
  // 过滤掉空组（搜索时可能所有组都空）
  return out.filter((g) => g.total > 0)
})

// 默认展开：一级仅「业务数据」展开；质量/系统/备份三个单分类组默认折叠（2026-09-12 用户要求）
// 二级：业务数据下**全部二级分类默认展开**（2026-09-12 用户要求）——首屏即可看到表行与其红绿灯，
//       不必再逐类点击；其余组无二级头。搜索时全部强制展开；选中表时自动展开其所属组与分类。
const BIZ_CAT_KEYS = GROUPS.filter((g) => g.key === 'biz').flatMap((g) =>
  g.categories.map((c) => `${g.key}::${c}`),
)
const expandedGroups = ref<Set<string>>(new Set(['biz']))
const expandedCategories = ref<Set<string>>(new Set(BIZ_CAT_KEYS))

function ensureCurrentExpanded() {
  if (!current.value) return
  const gk = groupKeyOf(current.value)
  const ck = categoryKeyOf(current.value)
  expandedGroups.value = new Set([...expandedGroups.value, gk])
  expandedCategories.value = new Set([...expandedCategories.value, `${gk}::${ck}`])
}

watch(current, ensureCurrentExpanded, { immediate: false })

function toggleGroup(k: string) {
  const s = new Set(expandedGroups.value)
  s.has(k) ? s.delete(k) : s.add(k)
  expandedGroups.value = s
}
function toggleCategory(gk: string, ck: string) {
  const key = `${gk}::${ck}`
  const s = new Set(expandedCategories.value)
  s.has(key) ? s.delete(key) : s.add(key)
  expandedCategories.value = s
}

// 表行样式：无二级分类头的单分类组（质量/系统/备份）缩进浅一级，视觉上与组名对齐
function rowStyle(colCount: number, name: string): string {
  const indent = colCount === 1 ? 26 : 38
  const base = `display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin:0 6px;padding:6px 6px 6px ${indent}px;border-radius:6px;cursor:pointer;font-size:13px;`
  return (
    base +
    (current.value === name
      ? 'background:#E6F1FB;color:#185FA5;font-weight:600;'
      : 'color:#333;')
  )
}

// ---------- 左侧清单手工排序（2026-09-12） ----------
// 语义：**增量覆盖** —— 只记录用户动过的容器（组内分类 / 容器内表行），
// 读取时把未记录的表追加到末尾，因此数据库新增表永远不会因为排序而"消失"。
// 持久化：localStorage 镜像（首屏秒出）+ user_prefs 服务端（跨浏览器/重启有效）。
type SidebarOrder = {
  cats: Record<string, string[]> // 组内二级分类顺序：{ 组key: [分类key...] }
  tables: Record<string, string[]> // 容器内表行顺序：{ '组key::分类key': [表名...] }
}
const sidebarOrder = ref<SidebarOrder>({ cats: {}, tables: {} })
const SIDEBAR_ORDER_LS = 'infodata.sidebarOrder'

function loadSidebarOrderLS(): SidebarOrder {
  try {
    const raw = localStorage.getItem(SIDEBAR_ORDER_LS)
    if (!raw) return { cats: {}, tables: {} }
    const o = JSON.parse(raw) as Partial<SidebarOrder>
    return { cats: o?.cats || {}, tables: o?.tables || {} }
  } catch {
    return { cats: {}, tables: {} }
  }
}
function saveSidebarOrderLS(o: SidebarOrder) {
  try {
    localStorage.setItem(SIDEBAR_ORDER_LS, JSON.stringify(o))
  } catch {
    /* 存储不可用则忽略 */
  }
}

async function loadSidebarOrder() {
  // 先上本地镜像兜底（后端未起也能记住顺序），再拉服务端全量覆盖
  sidebarOrder.value = loadSidebarOrderLS()
  try {
    const resp: any = await api.get('/prefs/sidebar-order', { silent: true })
    const o: SidebarOrder = { cats: resp?.cats || {}, tables: resp?.tables || {} }
    sidebarOrder.value = o
    saveSidebarOrderLS(o)
  } catch {
    /* 服务端不可达：保持镜像值 */
  }
}

let orderTimer: ReturnType<typeof setTimeout> | undefined
function persistSidebarOrder() {
  saveSidebarOrderLS(sidebarOrder.value)
  if (orderTimer) clearTimeout(orderTimer)
  orderTimer = setTimeout(() => {
    void api.put('/prefs/sidebar-order', sidebarOrder.value, { silent: true })
  }, 400)
}

const hasCustomOrder = computed(
  () =>
    Object.keys(sidebarOrder.value.cats).length > 0 ||
    Object.keys(sidebarOrder.value.tables).length > 0,
)

/** 把用户自定义顺序套到默认顺序上：手动项优先按手动序，未记录项保持默认序追加在后 */
function applyOrder(defaultList: string[], saved?: string[]): string[] {
  if (!saved || saved.length === 0) return defaultList
  const allowed = new Set(defaultList)
  const head = saved.filter((x) => allowed.has(x))
  const seen = new Set(head)
  return [...head, ...defaultList.filter((x) => !seen.has(x))]
}

function commitOrder(kind: DragKind, container: string, order: string[]) {
  const bucket = kind === 'table' ? 'tables' : 'cats'
  sidebarOrder.value = {
    ...sidebarOrder.value,
    [bucket]: { ...sidebarOrder.value[bucket], [container]: order },
  }
  persistSidebarOrder()
}

/** 悬停 ↑↓ 按钮：在 list 内把第 index 项上/下移一位 */
function moveItem(kind: DragKind, container: string, list: string[], index: number, delta: number) {
  const to = index + delta
  if (to < 0 || to >= list.length) return
  const next = [...list]
  const [picked] = next.splice(index, 1)
  next.splice(to, 0, picked)
  commitOrder(kind, container, next)
}
function tableNames(list: TableItem[]): string[] {
  return list.map((t) => t.name)
}
function catKeys(list: CategoryBucket[]): string[] {
  return list.map((c) => c.key)
}

function resetSidebarOrder() {
  sidebarOrder.value = { cats: {}, tables: {} }
  saveSidebarOrderLS(sidebarOrder.value)
  try {
    void api.delete('/prefs/sidebar-order', { silent: true })
  } catch {
    /* 静默 */
  }
}

// ---------- 拖拽排序（原生 HTML5 DnD，无新依赖） ----------
type DragKind = 'table' | 'cat'
const dragCtx = ref<{ kind: DragKind; container: string; name: string } | null>(null)
// 插入位置：{ 容器, 下标 }，下标 0..len，len 表示插到末尾
const dropHint = ref<{ container: string; index: number } | null>(null)

function onDragStart(kind: DragKind, container: string, name: string, e: DragEvent) {
  dragCtx.value = { kind, container, name }
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    try {
      e.dataTransfer.setData('text/plain', name)
    } catch {
      /* 某些浏览器在合成事件下会抛，忽略 */
    }
  }
}
function onDragEnd() {
  dragCtx.value = null
  dropHint.value = null
}
function onDragOver(kind: DragKind, container: string, list: string[], overName: string, e: DragEvent) {
  const d = dragCtx.value
  // 只允许同容器内排序：跨容器不 preventDefault，浏览器显示禁止光标
  if (!d || d.kind !== kind || d.container !== container) return
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  const idx = list.indexOf(overName)
  if (idx < 0) return
  const el = e.currentTarget as HTMLElement | null
  const rect = el?.getBoundingClientRect()
  const after = rect ? e.clientY > rect.top + rect.height / 2 : false
  dropHint.value = { container, index: after ? idx + 1 : idx }
}
function onDrop(kind: DragKind, container: string, list: string[], e: DragEvent) {
  e.preventDefault()
  const d = dragCtx.value
  const hint = dropHint.value
  dragCtx.value = null
  dropHint.value = null
  if (!d || !hint || d.kind !== kind || d.container !== container) return
  const from = list.indexOf(d.name)
  if (from < 0) return
  // 插入位下标是"移除前"的，移除后位于其后的目标点要左移一位
  const to = from < hint.index ? hint.index - 1 : hint.index
  if (to === from) return
  const next = [...list]
  next.splice(from, 1)
  next.splice(to, 0, d.name)
  commitOrder(kind, container, next)
}

/** 给行绑定插入指示线样式类 */
function hintClass(container: string, index: number, len: number): Record<string, boolean> {
  const h = dropHint.value
  if (!h || h.container !== container) return {}
  return {
    'drop-before': h.index === index,
    'drop-after': h.index === len && index === len - 1,
  }
}

// 搜索时：强制展开所有组和分类，便于看到结果
const _kw = computed(() => tableKeyword.value.trim())
watch(_kw, (kw) => {
  if (kw) {
    expandedGroups.value = new Set(GROUPS.map((g) => g.key))
    expandedCategories.value = new Set(
      groupedTables.value.flatMap((g) => g.categories.map((c) => `${g.key}::${c.key}`)),
    )
  }
})


const colOptions = computed<SelectOption[]>(() =>
  cols.value.map((c) => ({
    label: c.comment ? `${c.name} · ${c.comment}` : c.name,
    value: c.name,
  })),
)

const opOptions = computed<SelectOption[]>(() => {
  const c = cols.value.find((x) => x.name === fCol.value)
  if (!c) return []
  const basic = [
    { label: '等于', value: 'eq' },
    { label: '不等于', value: 'ne' },
  ]
  if (NUM_TYPES.includes(c.data_type) || DATE_TYPES.includes(c.data_type)) {
    return [
      ...basic,
      { label: '大于', value: 'gt' },
      { label: '大于等于', value: 'gte' },
      { label: '小于', value: 'lt' },
      { label: '小于等于', value: 'lte' },
    ]
  }
  return [...basic, { label: '包含', value: 'contains' }]
})

const valPlaceholder = computed(() => {
  const c = cols.value.find((x) => x.name === fCol.value)
  if (!c) return '输入过滤值'
  if (c.data_type === 'date') return '如 2026-09-04'
  if (c.data_type === 'datetime') return '如 2026-09-04 15:30'
  return c.comment || c.data_type
})

const colMap = computed(() => {
  const m = new Map<string, ColMeta>()
  cols.value.forEach((c) => m.set(c.name, c))
  return m
})

// ---------- 数据加载 ----------

function fmtWan(n: number): string {
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`
  return `${n}`
}

function isNumCol(name: string): boolean {
  const c = colMap.value.get(name)
  return !!c && NUM_TYPES.includes(c.data_type)
}
function isDateCol(name: string): boolean {
  const c = colMap.value.get(name)
  return !!c && DATE_TYPES.includes(c.data_type)
}

async function loadTables() {
  loadingTables.value = true
  try {
    const resp: any = await api.get('/db/tables')
    tables.value = (resp.items || []) as TableItem[]
    // 默认选中一张有代表性的大表
    const prefer = ['stock_market_daily', 'news', 'trade_calendar', 'stock_info']
    const target = prefer.find((n) => tables.value.some((t) => t.name === n))
    selectTable(target || tables.value[0]?.name || '')
  } catch {
    /* 全局拦截器已提示 */
  } finally {
    loadingTables.value = false
  }
  loadDqStatus()
  loadFlow()
}

async function loadDqStatus() {
  try {
    const resp = (await api.get('/dq/table-status')) as unknown as DqTableResp
    dqRunAt.value = resp.run_at
    const m = new Map<string, DqTableStatus>()
    ;(resp.items || []).forEach((it) => m.set(it.table_name, it))
    dqStatus.value = m
  } catch {
    /* 质量标记不可用不阻塞表浏览 */
  }
}

async function loadFlow() {
  try {
    const resp: any = await api.get('/db/tables/flow')
    const items = (resp?.items || {}) as Record<string, FlowInfo>
    const m = new Map<string, FlowInfo>()
    Object.values(items).forEach((it) => m.set(it.table_name, it))
    flowMap.value = m
  } catch {
    /* 数据流不可用不阻塞表浏览 */
  }
}

async function loadMeta(name: string) {
  meta.value = null
  try {
    const resp: any = await api.get(`/db/tables/${name}/meta`)
    meta.value = resp
  } catch {
    /* 忽略 */
  }
}

async function loadColumns(name: string) {
  try {
    const resp: any = await api.get(`/db/tables/${name}/columns`)
    cols.value = (resp.items || []) as ColMeta[]
    // 默认过滤器列跟随第一列
    if (cols.value.length) {
      const first = cols.value[0]
      fCol.value = first.name
      fOp.value = NUM_TYPES.includes(first.data_type) || DATE_TYPES.includes(first.data_type) ? 'eq' : 'contains'
    }
  } catch {
    cols.value = []
  }
}

async function loadRows(append = false) {
  if (!current.value) return
  loadingRows.value = true
  try {
    const params: Record<string, unknown> = {
      page_size: pageSize.value,
      offset: append ? offset.value : 0,
      sort_col: sort.value?.col || '',
      sort_dir: sort.value?.dir || 'asc',
      filters: JSON.stringify(filters.value),
    }
    const resp = (await api.get(`/db/tables/${current.value}/rows`, { params })) as unknown as RowResp
    rows.value = append ? [...rows.value, ...resp.rows] : resp.rows
    offset.value = append ? resp.offset + resp.count : resp.count
    hasMore.value = resp.has_more
  } catch {
    hasMore.value = false
  } finally {
    loadingRows.value = false
  }
}

function selectTable(name: string) {
  if (!name || name === current.value) return
  current.value = name
  rows.value = []
  offset.value = 0
  hasMore.value = false
  sort.value = null
  filters.value = []
  loadMeta(name)
  loadColumns(name).then(() => {
    applySortPref(name)
    loadRows(false)
  })
}

function reload() {
  loadRows(false)
}

function loadMore() {
  loadRows(true)
}

// ---------- 排序 ----------

function sortCol(name: string) {
  if (!sort.value || sort.value.col !== name) {
    sort.value = { col: name, dir: 'asc' }
  } else if (sort.value.dir === 'asc') {
    sort.value = { col: name, dir: 'desc' }
  } else {
    sort.value = null
  }
  persistCurrentSort()
  loadRows(false)
}

function sortIcon(name: string): string {
  if (sort.value?.col !== name) return ''
  return sort.value.dir === 'asc' ? ' ↑' : ' ↓'
}

// ---------- 过滤 ----------

function onColChange(_v: string) {
  const c = cols.value.find((x) => x.name === fCol.value)
  fOp.value = c && (NUM_TYPES.includes(c.data_type) || DATE_TYPES.includes(c.data_type)) ? 'eq' : 'contains'
}

function addFilter() {
  const col = fCol.value
  const val = fVal.value.trim()
  if (!col || !val) return
  filters.value = [...filters.value, { col, op: fOp.value, val }]
  fVal.value = ''
  loadRows(false)
}

function removeFilter(idx: number) {
  filters.value = filters.value.filter((_, i) => i !== idx)
  loadRows(false)
}

function clearFilters() {
  filters.value = []
  loadRows(false)
}

// ---------- 单元格渲染 ----------

function cellValue(v: unknown): string {
  if (v === null || v === undefined) return 'NULL'
  if (typeof v === 'number') {
    return Number.isInteger(v) ? v.toLocaleString('en-US') : v.toLocaleString('en-US')
  }
  return String(v)
}

function cellClass(name: string): string {
  if (isNumCol(name)) return 'c-num'
  if (isDateCol(name)) return 'c-date'
  return ''
}

function cellColor(name: string, v: unknown): string {
  const lower = name.toLowerCase()
  if (!CHANGE_RE.test(lower) || !isNumCol(name) || typeof v !== 'number') return ''
  if (v > 0) return '#EF232A'
  if (v < 0) return '#14B143'
  return ''
}

function isNullCell(v: unknown): boolean {
  return v === null || v === undefined
}

const totalText = computed(() => {
  if (!meta.value) return ''
  const n = meta.value.exact_rows ?? meta.value.estimated_rows
  return `${fmtWan(n)} 行${meta.value.is_estimate ? '（估算）' : ''}`
})

// ---------- 数据质量标记 ----------

const DOT_COLORS: Record<string, string> = {
  pass: '#18A058',
  warning: '#d48806',
  fail: '#d03050',
  error: '#d03050',
  none: '#d0d0d0',
}
const WORST_LABEL: Record<string, string> = {
  pass: '质量通过',
  warning: '有提醒',
  fail: '质量异常',
  error: '质量异常',
}
const currentDq = computed<DqTableStatus | null>(() => dqStatus.value.get(current.value) || null)

// ---------- 数据流（源/口径/维护任务） ----------
const flowOf = computed<FlowInfo | null>(() => flowMap.value.get(current.value) || null)
const flowSources = computed<string[]>(() => {
  const s = flowOf.value?.source_desc
  return s ? s.split(';').map((x) => x.trim()).filter(Boolean) : []
})

// 列级血缘：是否至少一个 writer 声明了写列（有则可渲染血缘拓扑）
const flowHasLineage = computed<boolean>(() =>
  !!flowOf.value?.jobs.some((j) => j.lineage && j.lineage.cols.length > 0),
)
// 血缘车道配色（按出现顺序轮换；与源标签色一致）
const LINEAGE_COLORS = ['#185FA5', '#5B4B8A', '#B45309', '#0F6E56', '#993C1D', '#5F5E5A']
function laneColor(idx: number): string {
  return LINEAGE_COLORS[idx % LINEAGE_COLORS.length]
}
// 未命中任何采集任务的列（系统/本地管理列），前端由当前表列清单与血缘并集差集算出
const flowSystemCols = computed<string[]>(() => {
  const all = new Set(cols.value.map((c) => c.name))
  const written = new Set<string>()
  flowOf.value?.jobs.forEach((j) => {
    ;(j.lineage?.cols || []).forEach((c) => written.add(c))
  })
  return [...all].filter((c) => !written.has(c))
})
const flowHover = ref<number | null>(null)

// ---- 整链拓扑辅助（v2：数据源 → 运行步骤链 → 字段血缘落点） ----
// 合并步骤模板（flow 注入的 RUN_STEPS）与最近一次运行实录（run_detail.run_steps 的 value）
function flowStepRows(j: FlowJob): RunStep[] {
  const tpl = j.run_steps || []
  const real = j.last?.run_detail?.run_steps || []
  return tpl.map((s) => {
    const hit = real.find((r) => r.no === s.no)
    return { no: s.no, name: s.name, params: s.params ?? null, value: hit?.value ?? null }
  })
}
// 判断一个 job 是否能用「单任务三段整链」视图展示（有血缘 + 步骤链模板）
const flowChainReady = computed<boolean>(() => {
  const j = flowOf.value?.jobs[0]
  return !!j && flowOf.value!.jobs.length === 1 && !!j.lineage?.cols.length && (j.run_steps?.length || 0) > 0
})
// 整链模式下的唯一维护任务
const flowMainJob = computed<FlowJob | null>(() =>
  flowOf.value && flowOf.value.jobs.length === 1 ? flowOf.value.jobs[0] : null,
)
// 多 writer 整链：同一张表由多个任务写入时，每个齐备（血缘列 + 步骤模板）的 job 渲染为一条独立车道
const flowMultiLaneJobs = computed<FlowJob[]>(() =>
  (flowOf.value?.jobs || []).filter((j) => (j.lineage?.cols?.length || 0) > 0 && (j.run_steps?.length || 0) > 0),
)
const flowMultiLaneReady = computed<boolean>(() =>
  !!flowOf.value && flowOf.value.jobs.length > 1 && flowMultiLaneJobs.value.length > 0,
)
// 多 writer 表中未达整链要素的 job（无列级血缘或步骤模板）单独兜底列出
const flowPlainJobs = computed<FlowJob[]>(() => {
  if (!flowOf.value || flowOf.value.jobs.length <= 1) return []
  const laneNames = new Set(flowMultiLaneJobs.value.map((j) => j.task_name))
  return flowOf.value.jobs.filter((j) => !laneNames.has(j.task_name))
})
// 直采列（随行源写入）与推算列（derived，清洗口径标注）
function flowDirectCols(j: FlowJob): string[] {
  return (j.lineage?.cols || []).filter((c) => !(j.lineage?.derived || []).includes(c))
}
function flowDerivedCols(j: FlowJob): string[] {
  return (j.lineage?.cols || []).filter((c) => (j.lineage?.derived || []).includes(c))
}

// ⚠️ APScheduler 语义：day_of_week **0=周一**、6=周日（与 Unix crontab 的 0=周日相反）。
// 「周一至周五」是 0-4；写成 1-5 会变成周二至周六（2026-09-14 事故根因）。
const DOW_CN: Record<string, string> = {
  '0': '周一', '1': '周二', '2': '周三', '3': '周四', '4': '周五', '5': '周六', '6': '周日',
}

/** 5 段 cron → 中文。**优先用后端 `cron_human`**（与真正跑的调度器同源），
 *  本函数仅在后端字段缺失时兜底，且必须遵循 APScheduler 星期语义。 */
function cronToText(cron: string | null, human?: string | null): string {
  if (human) return human
  const s = (cron || '').trim()
  if (!s) return ''
  if (s === '手动') return '手动触发'
  const p = s.split(/\s+/)
  if (p.length !== 5) return s
  const [min, hour, dom, mon, dow] = p
  if (min.startsWith('*/') && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    return `每 ${min.slice(2)} 分钟`
  }
  const week = (t: string) => DOW_CN[t] || null
  let when: string
  if (dow !== '*') {
    if (dow.includes('-')) {
      const [a, b] = dow.split('-')
      const wa = week(a)
      const wb = week(b)
      when = wa && wb ? `每${wa}至${wb}` : `周${dow}`
    } else {
      const parts = dow.split(',').map(week)
      when = parts.every(Boolean) ? `每${parts.join('、')}` : `周${dow}`
    }
  } else if (dom !== '*') {
    when = `每月${dom}日`
  } else {
    when = '每天'
  }
  if (hour === '*') return when
  const mm = min === '0' ? '00' : min
  const hhmm = hour
    .split(',')
    .map((h) => `${h.padStart(2, '0')}:${mm}`)
    .join('、')
  return `${when} ${hhmm}`
}

function fmtFlowDt(s: string | null): string {
  if (!s) return ''
  const t = s.replace('T', ' ')
  return `${t.slice(5, 10)} ${t.slice(11, 16)}`
}

function flowLastStatus(j: FlowJob): { text: string; color: string } {
  const st = j.last?.status
  if (st === 'success') return { text: '成功', color: '#18A058' }
  if (st === 'failed') return { text: '失败', color: '#d03050' }
  // blocked：数据未就绪按设计未执行（如快照发现日线只跑了一半），非故障，
  // 用琥珀色「提醒」而非失败色，避免与真实失败混淆
  if (st === 'blocked') return { text: '待数据', color: '#B45309' }
  if (st === 'running') return { text: '运行中', color: '#185FA5' }
  return { text: '从未运行', color: '#999' }
}

function dqDotColor(name: string): string {
  const s = dqStatus.value.get(name)
  return s ? DOT_COLORS[s.worst] : DOT_COLORS.none
}

// ---- 红绿灯聚合（2026-09-12）：一级组头 / 二级分类头也带一个「最差状态」圆点 ----
// 目的：即使该组/该分类折叠着，也能一眼看出下面有没有异常，不必点开逐个看。
const DQ_RANK: Record<string, number> = { none: 0, pass: 1, warning: 2, fail: 3, error: 3 }
interface DqAgg {
  worst: string
  covered: number
  pass: number
  warning: number
  bad: number
}
function dqAggOf(names: string[]): DqAgg | null {
  if (!dqRunAt.value || names.length === 0) return null
  let worst = 'none'
  let covered = 0
  let pass = 0
  let warning = 0
  let bad = 0
  names.forEach((n) => {
    const s = dqStatus.value.get(n)
    if (!s) return
    covered += 1
    pass += s.counts.pass
    warning += s.counts.warning
    bad += s.counts.fail + s.counts.error
    if ((DQ_RANK[s.worst] ?? -1) > (DQ_RANK[worst] ?? -1)) worst = s.worst
  })
  if (!covered) return null
  return { worst, covered, pass, warning, bad }
}
function dqAggColor(names: string[]): string {
  if (!dqRunAt.value) return 'transparent'
  const a = dqAggOf(names)
  // 组内/分类内没有任何表纳入体检 → 灰灯（与表行"未纳入体检"的灰点语义一致，而非"无此指示灯"）
  return DOT_COLORS[a ? a.worst : 'none']
}
function dqAggTitle(label: string, names: string[]): string {
  const a = dqAggOf(names)
  if (!a) return `${label}\n数据质量：组内暂未纳入自动体检`
  return `${label}\n数据质量：${WORST_LABEL[a.worst]} · 通过 ${a.pass} / 异常 ${a.bad} / 提醒 ${a.warning}（${a.covered}/${names.length} 张表纳入体检）`
}
// 组内全部表名 / 分类内全部表名
function dqNamesOfGroup(g: { categories: { tables: TableItem[] }[] }): string[] {
  return g.categories.flatMap((c) => c.tables.map((t) => t.name))
}
function dqNamesOfCat(c: { tables: TableItem[] }): string[] {
  return c.tables.map((t) => t.name)
}

function dqRowTitle(t: TableItem): string {
  const base = t.comment ? `${t.name} · ${t.comment}` : t.name
  if (!dqRunAt.value) return base
  const s = dqStatus.value.get(t.name)
  if (!s) return `${base}\n未纳入自动体检（可到数据质量栏目补充规则）`
  const c = s.counts
  const bad = c.fail + c.error
  return `${base}\n数据质量：${WORST_LABEL[s.worst]} · 通过 ${c.pass} / 异常 ${bad} / 提醒 ${c.warning}（共 ${c.total} 条规则）\n体检时间 ${fmtDqTime(dqRunAt.value)}`
}

function fmtDqTime(s: string | null): string {
  return s ? s.replace('T', ' ').slice(0, 16) : ''
}

function dqTagType(worst: string): 'default' | 'success' | 'warning' | 'error' {
  if (worst === 'pass') return 'success'
  if (worst === 'warning') return 'warning'
  return 'error'
}

// 状态 → 文案 / 颜色（2026-09-12 修正）：
// 原来的实现只判了 error / fail，其余（含 pass）一律落到兜底的「提醒」+ 橙色，
// 导致规则明细弹窗里 pass 的规则被误显示为 warning。这里补全四个状态分支。
// 色值遵循《颜色体系设计规范 v2.0》：通过 = 主色蓝，提醒 = 琥珀，失败/错误 = 深红棕。
function dqIssueStatusLabel(status: string): string {
  if (status === 'error') return '执行错误'
  if (status === 'fail') return '异常'
  if (status === 'warning') return '提醒'
  return '通过'
}

function dqIssueColor(status: string): string {
  if (status === 'error' || status === 'fail') return '#791F1F'
  if (status === 'warning') return '#B45309'
  return '#185FA5'
}

async function openDqModal(focusRule = '') {
  if (!current.value) return
  dqModalTable.value = current.value
  dqModalFocusRule.value = focusRule
  dqModalOpen.value = true
  dqModalLoading.value = true

  // 并行重拉：1) 该表规则明细 2) 全表聚合状态（让信息栏 chip 数字也与弹窗同步）
  const fetchReport = api
    .get('/dq/report', { params: { table: current.value } })
    .then((resp) => {
      const r = resp as unknown as DqReportResp
      dqModalRunAt.value = r.run_at
      const order: Record<string, number> = { error: 0, fail: 1, warning: 2, pass: 3 }
      dqModalRows.value = [...(r.items || [])].sort((a, b) => {
        const oa = order[a.status] ?? 9
        const ob = order[b.status] ?? 9
        if (oa !== ob) return oa - ob
        return a.rule_name.localeCompare(b.rule_name)
      })
    })
    .catch(() => {
      dqModalRows.value = []
    })

  await Promise.all([loadDqStatus(), fetchReport])
  dqModalLoading.value = false

  // 若指定了定位规则，弹窗渲染后 scrollIntoView
  if (focusRule) {
    requestAnimationFrame(() => {
      const el = document.getElementById(`dq-row-${focusRule}`)
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    })
  }
}

async function openFlowModal() {
  if (!current.value) return
  flowModalOpen.value = true
  flowModalLoading.value = true
  // 重拉 flowMap，确保 modal 内的 jobs/上次状态/下次运行是最新
  await loadFlow()
  flowModalLoading.value = false
}

function closeDqModal() {
  dqModalOpen.value = false
  dqModalFocusRule.value = ''
}

onMounted(async () => {
  // 并行预热：排序偏好 + 左侧清单手工排序（都由服务端 → 本地镜像兜底）
  await Promise.all([loadSortPrefs(), loadSidebarOrder()])
  loadTables()
})
</script>

<template>
  <div style="display:flex;gap:12px;height:calc(100vh - 88px);min-height:460px;">
    <!-- 左侧：表清单 -->
    <div style="width:252px;flex-shrink:0;background:#fff;border:1px solid #ececec;border-radius:10px;display:flex;flex-direction:column;overflow:hidden;">
      <div style="padding:12px 12px 10px;border-bottom:1px solid #f0f0f0;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;font-size:13px;font-weight:600;color:#1a1a1a;">
          <span>adata 数据库</span>
          <span style="color:#999;font-weight:400;font-size:12px;">{{ tables.length }} 张表</span>
        </div>
        <NInput v-model:value="tableKeyword" size="small" placeholder="搜索表名 / 注释" clearable style="margin-top:8px" />
        <div v-if="hasCustomOrder" style="margin-top:6px;display:flex;align-items:center;justify-content:space-between;">
          <span style="font-size:11px;color:#C9A227;">已自定义排序</span>
          <NPopconfirm positive-text="确定" negative-text="取消" @positive-click="resetSidebarOrder">
            <template #trigger>
              <NButton size="tiny" quaternary data-testid="btn-reset-order">恢复默认顺序</NButton>
            </template>
            确定清除全部手工排序，恢复按名称排列？
          </NPopconfirm>
        </div>
      </div>
      <div style="flex:1;overflow:auto;padding:6px 0;">
        <NSpin :show="loadingTables" size="small">
          <div v-if="!loadingTables && groupedTables.length === 0" style="padding:24px 0;">
            <NEmpty description="无匹配表" />
          </div>
          <div
            v-for="g in groupedTables"
            :key="g.key"
            style="margin-bottom:4px;"
          >
            <!-- 一级组头（2026-09-11：全部组统一可折叠，含质量/系统/备份三个单分类组） -->
            <div
              :data-testid="`grp-${g.key}`"
              class="grp-head"
              :title="dqAggTitle(`${g.title}（${g.total} 张表）`, dqNamesOfGroup(g))"
              style="display:flex;align-items:center;gap:6px;margin:0 6px;padding:7px 6px;cursor:pointer;user-select:none;border-radius:6px;font-size:12.5px;font-weight:600;color:#1a1a1a;"
              @click="toggleGroup(g.key)"
            >
              <span
                style="font-size:10px;color:#999;width:10px;display:inline-block;transition:transform 0.15s;"
                :style="expandedGroups.has(g.key) ? 'transform:rotate(90deg);' : ''"
              >▶</span>
              <span style="font-size:13px;">{{ g.icon }}</span>
              <span style="flex:1;">{{ g.title }}</span>
              <!-- 组级聚合红绿灯：折叠时也能看出该组有无异常 -->
              <span
                v-if="dqRunAt"
                :data-testid="`dot-grp-${g.key}`"
                style="width:8px;height:8px;border-radius:50%;flex-shrink:0;"
                :style="{ background: dqAggColor(dqNamesOfGroup(g)) }"
              ></span>
              <span style="font-size:11px;color:#999;font-weight:400;">{{ g.total }}</span>
            </div>
            <!-- 二级分类与表 -->
            <div v-show="expandedGroups.has(g.key)" style="padding:0 0 4px 0;">
              <div
                v-for="(c, ci) in g.categories"
                :key="`${g.key}::${c.key}`"
              >
                <!-- 二级分类头（可拖拽排序；仅多分类组显示） -->
                <div
                  v-if="g.categories.length > 1 || c.key === '__未分类__'"
                  :data-testid="`cat-${g.key}-${c.key}`"
                  class="cat-head"
                  draggable="true"
                  title="拖动可调整分类顺序"
                  :class="hintClass(g.key, ci, g.categories.length)"
                  style="display:flex;align-items:center;gap:4px;margin:0 6px;padding:4px 6px 4px 20px;border-radius:6px;cursor:pointer;user-select:none;font-size:11.5px;color:#5F5E5A;"
                  @click="toggleCategory(g.key, c.key)"
                  @dragstart="onDragStart('cat', g.key, c.key, $event)"
                  @dragend="onDragEnd"
                  @dragover="onDragOver('cat', g.key, catKeys(g.categories), c.key, $event)"
                  @drop="onDrop('cat', g.key, catKeys(g.categories), $event)"
                >
                  <span style="font-size:9px;width:9px;display:inline-block;transition:transform 0.15s;color:#bbb;"
                        :style="expandedCategories.has(`${g.key}::${c.key}`) ? 'transform:rotate(90deg);' : ''">▶</span>
                  <span style="flex:1;">{{ c.name }}</span>
                  <!-- 分类级聚合红绿灯 -->
                  <span
                    v-if="dqRunAt"
                    :data-testid="`dot-cat-${g.key}-${c.key}`"
                    :title="dqAggTitle(`${c.name}（${c.tables.length} 张表）`, dqNamesOfCat(c))"
                    style="width:8px;height:8px;border-radius:50%;flex-shrink:0;"
                    :style="{ background: dqAggColor(dqNamesOfCat(c)) }"
                  ></span>
                  <span class="row-move">
                    <button
                      class="mv-btn" :disabled="ci === 0"
                      :data-testid="`up-cat-${g.key}-${c.key}`"
                      title="上移" @click.stop="moveItem('cat', g.key, catKeys(g.categories), ci, -1)"
                    >↑</button>
                    <button
                      class="mv-btn" :disabled="ci === g.categories.length - 1"
                      :data-testid="`down-cat-${g.key}-${c.key}`"
                      title="下移" @click.stop="moveItem('cat', g.key, catKeys(g.categories), ci, 1)"
                    >↓</button>
                  </span>
                  <span class="row-count" style="font-size:10.5px;color:#bbb;">{{ c.tables.length }}</span>
                </div>
                <!-- 表行（可拖拽排序 + 悬停 ↑↓ 微调） -->
                <div
                  v-show="g.categories.length === 1 || c.key === '__未分类__' || expandedCategories.has(`${g.key}::${c.key}`)"
                  v-for="(t, ti) in c.tables"
                  :key="t.name"
                  :data-testid="`tbl-${t.name}`"
                  class="tbl-row"
                  draggable="true"
                  :title="dqRowTitle(t)"
                  :class="hintClass(`${g.key}::${c.key}`, ti, c.tables.length)"
                  :style="rowStyle(g.categories.length, t.name)"
                  @click="selectTable(t.name)"
                  @dragstart="onDragStart('table', `${g.key}::${c.key}`, t.name, $event)"
                  @dragend="onDragEnd"
                  @dragover="onDragOver('table', `${g.key}::${c.key}`, tableNames(c.tables), t.name, $event)"
                  @drop="onDrop('table', `${g.key}::${c.key}`, tableNames(c.tables), $event)"
                >
                  <span
                    v-if="dqRunAt"
                    :data-testid="`dot-tbl-${t.name}`"
                    style="width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:4px;"
                    :style="{ background: dqDotColor(t.name) }"
                  ></span>
                  <div style="display:flex;flex-direction:column;gap:2px;min-width:0;flex:1;overflow:hidden;">
                    <span style="font-family:Consolas,Menlo,monospace;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{{ t.name }}</span>
                    <span
                      v-if="t.comment"
                      style="font-size:11px;font-weight:400;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                      :style="current === t.name ? 'color:#7BA7D4;font-weight:400;' : 'color:#999;font-weight:400;'"
                    >{{ t.comment }}</span>
                  </div>
                  <span class="row-move">
                    <button
                      class="mv-btn" :disabled="ti === 0"
                      :data-testid="`up-tbl-${t.name}`"
                      title="上移" @click.stop="moveItem('table', `${g.key}::${c.key}`, tableNames(c.tables), ti, -1)"
                    >↑</button>
                    <button
                      class="mv-btn" :disabled="ti === c.tables.length - 1"
                      :data-testid="`down-tbl-${t.name}`"
                      title="下移" @click.stop="moveItem('table', `${g.key}::${c.key}`, tableNames(c.tables), ti, 1)"
                    >↓</button>
                  </span>
                  <span
                    class="row-count"
                    style="font-size:11px;flex-shrink:0;align-self:flex-start;margin-top:1px;"
                    :style="current === t.name ? 'color:#7BA7D4;' : 'color:#b0b0b0;'"
                  >{{ fmtWan(t.rows_estimate) }}</span>
                </div>
              </div>
            </div>
          </div>
        </NSpin>
      </div>
      <div style="padding:8px 12px;border-top:1px solid #f0f0f0;font-size:11px;color:#aaa;line-height:1.5;">
        只读浏览 · 数据来源于本地 MySQL（adata）
      </div>
    </div>

    <!-- 右侧：数据网格 -->
    <div style="flex:1;min-width:0;background:#fff;border:1px solid #ececec;border-radius:10px;display:flex;flex-direction:column;overflow:hidden;">
      <!-- 表信息栏 -->
      <div v-if="current" style="padding:10px 14px;border-bottom:1px solid #f0f0f0;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <span style="font-family:Consolas,Menlo,monospace;font-size:15px;font-weight:600;color:#185FA5;">{{ current }}</span>
        <NTag v-if="meta?.comment" size="small" :bordered="false" type="info"
              :title="meta.comment"
              style="white-space:normal;line-height:1.45;align-self:flex-start;overflow-wrap:anywhere;">
          {{ meta.comment }}
        </NTag>
        <!-- 分类编辑：下拉即改，写 table_meta，左侧树/数据流卡联动 -->
        <span style="display:inline-flex;align-items:center;gap:4px;">
          <span style="font-size:12px;color:#888;">分类</span>
          <NSelect
            :value="categoryOf(current) || ''"
            :options="CATEGORY_OPTIONS"
            size="tiny"
            :loading="catSaving"
            :consistent-menu-width="false"
            style="width:100px;min-width:78px;"
            title="选择分类后立即保存；「未分类」= 移出分类"
            @update:value="setCategory"
          />
          <span v-if="catSavedTip" style="font-size:11px;color:#185FA5;">{{ catSavedTip }}</span>
        </span>
        <span style="font-size:12px;color:#888;">{{ totalText }}</span>
        <span v-if="meta?.update_time" style="font-size:12px;color:#bbb;">更新 {{ meta.update_time.replace('T', ' ').slice(0, 19) }}</span>
        <span style="margin-left:auto;display:flex;gap:6px;align-items:center;">
          <NButton size="tiny" quaternary @click="reload()">刷新</NButton>
          <NButton size="tiny" type="primary" ghost @click="showSql = true">数据探查</NButton>
          <NButton
            size="tiny"
            :type="sortPrefs[current] ? 'primary' : 'default'"
            :secondary="!!sortPrefs[current]"
            quaternary
            :title="sortPrefs[current]
              ? `已记住默认排序：${sortPrefs[current].col} ${sortPrefs[current].dir === 'asc' ? '升序' : '降序'}（点列头可改）`
              : '查看 / 管理各表记住的默认排序（存服务端）'"
            @click="showSortPrefs = true"
          >
            排序偏好{{ sortPrefs[current] ? ' ✓' : '' }}
          </NButton>
          <NSelect
            :value="pageSize"
            :options="[{ label: '50 行/页', value: 50 }, { label: '100 行/页', value: 100 }, { label: '200 行/页', value: 200 }]"
            size="tiny"
            style="width:110px;"
            @update:value="(v: number) => { pageSize = v; loadRows(false) }"
          />
        </span>
      </div>

      <!-- 信息栏：数据质量 · 数据流（点击弹窗查看明细） -->
      <div v-if="dqRunAt || flowOf" style="padding:6px 14px;border-bottom:1px solid #f0f0f0;background:#FAFBFC;">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">

          <!-- 数据质量入口（点击弹窗） -->
          <div
            v-if="dqRunAt"
            title="点击查看该表数据质量规则明细"
            style="display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border:1px solid #e6e6e6;border-radius:6px;background:#fff;cursor:pointer;user-select:none;"
            @click="openDqModal('')"
          >
            <span style="font-size:12px;font-weight:600;color:#185FA5;">数据质量</span>
            <NTag v-if="currentDq" size="tiny" :bordered="false" :type="dqTagType(currentDq.worst)">
              {{ WORST_LABEL[currentDq.worst] }}
            </NTag>
            <NTag v-else size="tiny" :bordered="false" type="default">未纳入体检</NTag>
            <span v-if="currentDq" style="font-size:11.5px;color:#888;">
              <template v-if="currentDq.issues.length">
                {{ currentDq.counts.fail + currentDq.counts.error }} 异常 / {{ currentDq.counts.warning }} 提醒
              </template>
              <template v-else>
                {{ currentDq.counts.total }} 条规则通过
              </template>
            </span>
            <span style="font-size:11px;color:#185FA5;">弹窗查看 ▸</span>
          </div>

          <!-- 数据流入口（点击弹窗，弹窗内 fresh fetch 最新作业状态） -->
          <div
            v-if="flowOf"
            title="点击查看该表数据流明细（数据源 / 清洗口径 / 维护作业，每次打开均拉取最新）"
            style="display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border:1px solid #e6e6e6;border-radius:6px;background:#fff;cursor:pointer;user-select:none;"
            @click="openFlowModal()"
          >
            <span style="font-size:12px;font-weight:600;color:#185FA5;">数据流</span>
            <NTag v-if="flowOf.category" size="tiny" :bordered="false" type="info">{{ flowOf.category }}</NTag>
            <span style="font-size:11.5px;color:#888;">
              {{ flowOf.writers.length ? `${flowOf.writers.length} 个维护任务` : '无采集任务' }}
            </span>
            <span v-if="flowOf.note" style="font-size:11px;color:#B45309;">[{{ flowOf.note }}]</span>
            <span style="font-size:11px;color:#185FA5;">弹窗查看 ▸</span>
          </div>
        </div>
      </div>

      <!-- 过滤器 -->
      <div v-if="cols.length" style="padding:8px 14px;border-bottom:1px solid #f0f0f0;">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <NSelect
            v-model:value="fCol"
            :options="colOptions"
            size="small"
            filterable
            placeholder="选择列"
            style="width:190px;"
            @update:value="onColChange"
          />
          <NSelect v-model:value="fOp" :options="opOptions" size="small" style="width:116px;" />
          <NInput v-model:value="fVal" :placeholder="valPlaceholder" size="small" style="width:190px;" @keyup.enter="addFilter()" />
          <NButton size="small" type="primary" secondary :disabled="!fVal.trim()" @click="addFilter()">添加</NButton>
          <NButton v-if="filters.length" size="small" quaternary @click="clearFilters()">清空条件</NButton>
        </div>
        <div v-if="filters.length" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">
          <NTag
            v-for="(f, i) in filters"
            :key="i"
            size="small"
            type="info"
            closable
            :bordered="false"
            @close="removeFilter(i)"
          >
            <span style="font-family:Consolas,Menlo,monospace;">{{ f.col }}</span>
            {{ f.op === 'eq' ? '=' : f.op === 'ne' ? '≠' : f.op === 'gt' ? '>' : f.op === 'gte' ? '≥' : f.op === 'lt' ? '<' : f.op === 'lte' ? '≤' : '包含' }}
            <span style="font-weight:600;">{{ f.val }}</span>
          </NTag>
        </div>
      </div>

      <!-- 网格主体 -->
      <div style="flex:1;overflow:auto;min-height:0;">
        <NSpin :show="loadingRows" style="height:100%;">
          <div v-if="!loadingRows && rows.length === 0" style="padding:60px 0;">
            <NEmpty :description="filters.length ? '无匹配数据，试试调整过滤条件' : '该表暂无数据'">
              <template v-if="filters.length" #extra>
                <NButton size="small" @click="clearFilters()">清空过滤条件</NButton>
              </template>
            </NEmpty>
          </div>
          <table v-if="rows.length" style="border-collapse:collapse;font-size:12px;">
            <thead>
              <tr>
                <th
                  v-for="c in cols"
                  :key="c.name"
                  :title="`${c.column_type}${c.comment ? ' · ' + c.comment : ''}`"
                  style="position:sticky;top:0;z-index:2;background:#F7F8FA;border-bottom:1px solid #ececec;border-right:1px solid #f0f0f0;padding:6px 12px;white-space:nowrap;cursor:pointer;text-align:left;font-weight:500;"
                  @click="sortCol(c.name)"
                >
                  <div style="display:flex;align-items:center;gap:4px;font-family:Consolas,Menlo,monospace;font-size:12px;color:#444;">
                    <span v-if="c.is_primary" style="width:6px;height:6px;border-radius:50%;background:#F5C84C;flex-shrink:0;" :title="'主键：' + c.name"></span>
                    <span>{{ c.name }}</span>
                    <span style="color:#185FA5;font-size:11px;">{{ sortIcon(c.name) }}</span>
                  </div>
                  <div v-if="c.comment" style="font-size:11px;color:#aaa;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ c.comment }}</div>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, ri) in rows" :key="ri" style="background:ri % 2 === 1 ? '#FAFBFC' : '#fff';">
                <td
                  v-for="c in cols"
                  :key="c.name"
                  :title="isNullCell(row[c.name]) ? '' : cellValue(row[c.name])"
                  style="border-bottom:1px solid #f4f4f4;border-right:1px solid #f7f7f7;padding:4px 12px;white-space:nowrap;max-width:420px;overflow:hidden;text-overflow:ellipsis;font-family:Consolas,Menlo,monospace;font-size:12px;"
                  :style="[
                    { textAlign: isNumCol(c.name) ? 'right' : 'left', color: cellColor(c.name, row[c.name]) || (isNullCell(row[c.name]) ? '#c0c0c0' : '#333') },
                  ]"
                >
                  <span :class="cellClass(c.name)">{{ cellValue(row[c.name]) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </NSpin>
      </div>

      <!-- 数据质量规则明细弹窗 -->
      <NModal
        v-model:show="dqModalOpen"
        preset="card"
        :bordered="false"
        size="huge"
        style="width:880px;max-width:92vw;"
        :title="`${dqModalTable} · 数据质量规则（最近一次体检）`"
        :on-after-leave="closeDqModal"
      >
        <div v-if="dqModalRunAt" style="font-size:12px;color:#888;margin-bottom:10px;">
          体检时间 {{ fmtDqTime(dqModalRunAt) }} · 共 {{ dqModalRows.length }} 条规则 · 异常排前
        </div>
        <NSpin :show="dqModalLoading">
          <NTable v-if="!dqModalLoading && dqModalRows.length" size="small" :bordered="false" :single-line="false" style="font-size:12px;">
            <thead>
              <tr>
                <th style="width:90px;">状态</th>
                <th style="width:200px;">规则名</th>
                <th style="width:140px;">检查类型</th>
                <th style="width:80px;">严重度</th>
                <th style="width:120px;">度量值</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="r in dqModalRows"
                :id="`dq-row-${r.rule_name}`"
                :key="r.rule_name"
                :style="dqModalFocusRule === r.rule_name ? 'background:#FFF7E6;' : ''"
              >
                <td>
                  <span style="display:inline-flex;align-items:center;gap:6px;">
                    <span style="width:8px;height:8px;border-radius:50%;" :style="{ background: dqIssueColor(r.status) }"></span>
                    <span :style="{ color: dqIssueColor(r.status), fontWeight: 600 }">{{ dqIssueStatusLabel(r.status) }}</span>
                  </span>
                </td>
                <td style="font-family:Consolas,Menlo,monospace;color:#185FA5;">{{ r.rule_name }}</td>
                <td style="color:#888;">{{ r.check_type }}</td>
                <td style="color:#666;">{{ r.severity }}</td>
                <td style="font-family:Consolas,Menlo,monospace;color:#333;">{{ r.metric_value ?? '—' }}</td>
                <td style="color:#444;line-height:1.5;overflow-wrap:anywhere;">{{ r.message || '—' }}</td>
              </tr>
            </tbody>
          </NTable>
          <NEmpty v-else-if="!dqModalLoading" description="该表暂无规则详情（可能未运行过体检）" />
        </NSpin>
        <template #footer>
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:11px;color:#999;">
              数据来源：本地 MySQL（adata.dq_report）
            </span>
            <NButton size="small" type="primary" secondary @click="router.push({ path: '/quality', query: { table: current } })">查看质量报告栏目 ▸</NButton>
          </div>
        </template>
      </NModal>

      <!-- 数据流明细弹窗 -->
      <NModal
        v-model:show="flowModalOpen"
        preset="card"
        :bordered="false"
        size="huge"
        style="width:1160px;max-width:96vw;"
        :title="`${flowOf?.table_name || current} · 数据流明细`"
      >
        <NSpin :show="flowModalLoading">
          <div v-if="flowOf" style="display:flex;flex-direction:column;gap:10px;font-size:12.5px;">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <NTag v-if="flowOf.category" size="small" :bordered="false" type="info">{{ flowOf.category }}</NTag>
            <span style="color:#555;">{{ flowOf.writers.length ? `维护任务 ${flowOf.writers.length} 个` : '无自动采集任务' }}</span>
            <span v-if="flowOf.note" style="font-size:11px;color:#B45309;">[{{ flowOf.note }}]</span>
          </div>
          <div>
            <div style="font-size:11px;color:#888;margin-bottom:4px;">数据源</div>
            <div v-if="flowSources.length" style="display:flex;gap:6px;flex-wrap:wrap;">
              <NTag v-for="src in flowSources" :key="src" size="small" :bordered="false" type="info">{{ src }}</NTag>
            </div>
            <div v-else style="color:#aaa;">—</div>
          </div>
          <!-- 排期（2026-09-24 新增）：**本区块是排期的唯一权威**，实时读调度器。
               动因：table_meta.flow_desc 是人工维护的散文，改 cron 后极易漏改 →
               实测 8 张表的描述里时刻与 task_config.cron 长期不一致（21:40 写成 20:15 等），
               且 seed 重跑还会把库内已订正的内容回退。故把「时刻」的权威源前移到本区块，
               flow_desc 中出现的时刻一律降级为历史叙述（常含「原 xx:xx 会怎样」的因果说明，不可删）。 -->
          <div v-if="flowOf.jobs.length">
            <div style="font-size:11px;color:#888;margin-bottom:4px;">
              排期
              <span style="color:#aaa;">（实时读调度器 · 权威）</span>
            </div>
            <div style="display:flex;flex-direction:column;gap:4px;">
              <div
                v-for="j in flowOf.jobs"
                :key="j.task_name"
                style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:#FAFBFC;border:1px solid #f0f0f0;border-radius:6px;padding:5px 9px;"
              >
                <span style="font-family:monospace;color:#185FA5;">{{ j.task_name }}</span>
                <NTag v-if="j.cron_human" size="tiny" :bordered="false" type="info">{{ j.cron_human }}</NTag>
                <span v-else style="color:#aaa;">无排期</span>
                <span v-if="j.cron" style="font-size:11px;color:#999;font-family:monospace;">{{ j.cron }}</span>
                <span v-if="j.enabled === false" style="font-size:11px;color:#B45309;">[已停用]</span>
                <span v-if="j.next_run" style="font-size:11px;color:#888;">下次 {{ fmtDqTime(j.next_run) }}</span>
              </div>
            </div>
          </div>
          <div v-if="flowOf.flow_desc">
            <div style="font-size:11px;color:#888;margin-bottom:4px;">清洗口径 / 数据流</div>
            <div style="line-height:1.7;color:#444;overflow-wrap:anywhere;background:#FAFBFC;border:1px solid #f0f0f0;border-radius:6px;padding:8px 10px;">
              {{ flowOf.flow_desc }}
            </div>
          </div>

          <!-- 整链拓扑 v2：数据源 → 运行步骤链 → 字段血缘落点（单任务 + RUN_STEPS 时） -->
          <div
            v-if="flowChainReady && flowMainJob"
            style="display:flex;gap:10px;align-items:stretch;flex-wrap:nowrap;"
          >
            <!-- 左：数据源（主备链） -->
            <div
              style="flex:0 0 178px;display:flex;flex-direction:column;gap:6px;border:1px solid #eceff4;border-radius:10px;padding:10px;background:#FBFCFE;"
            >
              <div style="font-size:11px;color:#888;margin-bottom:2px;">数据源 · 主备链</div>
              <div
                v-for="(src, i) in flowSources"
                :key="src"
                :title="i === 0 ? '主源（优先使用）' : '降级备源（主源失败自动切换）'"
                style="border-radius:8px;padding:6px 8px;font-size:12px;line-height:1.35;"
                :style="i === 0
                  ? { border: '1.5px solid ' + laneColor(0), background: laneColor(0) + '12', color: '#0C447C' }
                  : { border: '1px dashed #C9A227', background: '#FDF9EE', color: '#8a6d3b' }"
              >
                <div :style="{ fontWeight: 500 }">{{ src }}</div>
                <div :style="{ fontSize: '10.5px', opacity: 0.75 }">{{ i === 0 ? '主源' : '备' + i }}</div>
              </div>
              <div style="margin-top:auto;font-size:10.5px;color:#a0a6ad;line-height:1.5;border-top:1px dashed #e5eaf0;padding-top:6px;">
                源失败自动切下一级（见任务盒步骤 4）
              </div>
            </div>

            <div style="flex:0 0 18px;display:flex;align-items:center;justify-content:center;color:#185FA5;font-size:16px;">→</div>

            <!-- 中：任务盒 + 运行步骤链 + 最近实录 -->
            <div
              style="flex:0 0 372px;display:flex;flex-direction:column;gap:6px;border:1px solid #eceff4;border-radius:10px;padding:10px;background:#fff;"
            >
              <div style="font-size:11px;color:#888;">维护任务 · 运行逻辑</div>
              <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                <span style="font-family:Consolas,Menlo,monospace;font-weight:500;color:#185FA5;font-size:13px;">{{ flowMainJob.task_name }}</span>
                <span v-if="flowMainJob.cron" style="font-size:11px;color:#555;background:#F5F8FC;border:1px solid #e0e6ed;border-radius:4px;padding:1px 6px;">{{ cronToText(flowMainJob.cron, flowMainJob.cron_human) }}</span>
                <span v-if="flowMainJob.enabled === false" style="font-size:11px;color:#999;">已停用</span>
                <span v-if="flowMainJob.running" style="font-size:11px;color:#185FA5;">运行中…</span>
              </div>
              <!-- 步骤链：模板 RUN_STEPS + 实录 value -->
              <div style="display:flex;flex-direction:column;gap:4px;">
                <div
                  v-for="s in flowStepRows(flowMainJob)"
                  :key="s.no"
                  :title="s.params || ''"
                  style="display:flex;align-items:center;gap:6px;border:1px solid #eef1f5;border-left:2.5px solid #B5D4F4;border-radius:6px;padding:4px 8px;background:#FAFBFC;"
                >
                  <span style="font-size:10.5px;color:#aaa;font-family:Consolas,Menlo,monospace;flex-shrink:0;">{{ s.no }}</span>
                  <span style="font-size:12px;color:#333;flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{{ s.name }}</span>
                  <span v-if="s.value" :title="s.value" style="margin-left:auto;flex:0 1 auto;max-width:62%;font-size:10.5px;color:#185FA5;background:#E6F1FB;border-radius:4px;padding:1px 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{{ s.value }}</span>
                  <span v-else style="margin-left:auto;flex-shrink:0;font-size:10.5px;color:#c0c6cc;">—</span>
                </div>
              </div>
              <div style="font-size:10.5px;color:#a0a6ad;line-height:1.5;">
                {{ flowMainJob.last ? '最近运行实录 · 步骤数值取自 task_runs 快照' : '该任务尚未运行过，步骤右侧数值将在下次运行后自动填充' }}
              </div>
              <!-- 最近运行条 -->
              <div
                style="border-radius:6px;background:#F5F8FC;border:1px solid #e6ebf1;padding:5px 8px;display:flex;align-items:center;gap:6px;font-size:11px;color:#555;flex-wrap:wrap;"
              >
                <span style="width:6px;height:6px;border-radius:50%;display:inline-block;" :style="{ background: flowLastStatus(flowMainJob).color }"></span>
                <b :style="{ color: flowLastStatus(flowMainJob).color }">{{ flowLastStatus(flowMainJob).text }}</b>
                <template v-if="flowMainJob.last">
                  <template v-if="flowMainJob.last.status === 'success' && flowMainJob.last.records_written !== null">
                    +{{ flowMainJob.last.records_written }} 条
                  </template>
                  <span v-if="flowMainJob.last.finished_at"> · {{ fmtFlowDt(flowMainJob.last.finished_at) }}</span>
                  <span v-if="flowMainJob.last.run_detail?.duration"> · {{ flowMainJob.last.run_detail.duration }}</span>
                </template>
                <span v-if="flowMainJob.next_run" style="margin-left:auto;color:#888;">下次 {{ fmtFlowDt(flowMainJob.next_run) }}</span>
              </div>
            </div>

            <div style="flex:0 0 18px;display:flex;align-items:center;justify-content:center;color:#185FA5;font-size:16px;">→</div>

            <!-- 右：字段血缘落点（A 直采 / B 推算 / C 系统） -->
            <div
              style="flex:1;min-width:0;display:flex;flex-direction:column;gap:8px;border:1px solid #eceff4;border-radius:10px;padding:10px;background:#FBFCFE;"
            >
              <div style="font-size:11px;color:#888;">
                字段血缘落点 · {{ flowMainJob.lineage?.cols.length }} 列
                <span style="color:#c3c9d0;margin-left:4px;">悬停字段查看口径</span>
              </div>
              <!-- A 直采 -->
              <div>
                <div style="font-size:11px;color:#888;margin-bottom:3px;">
                  A 随行源直采
                  <span style="color:#c3c9d0;margin-left:4px;">命中源为整行来源，OHLC 为不复权实际成交价（复权口径由 adj_factor 派生）</span>
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:3px;">
                  <span
                    v-for="c in flowDirectCols(flowMainJob)"
                    :key="c"
                    :title="(flowMainJob.lineage?.col_notes || {})[c] || ''"
                    style="font-family:Consolas,Menlo,monospace;font-size:11px;padding:2px 6px;border:1px solid #d8e2ec;border-radius:4px;color:#3a5a7a;background:#fff;"
                  >{{ c }}</span>
                </div>
              </div>
              <!-- B 推算 -->
              <!-- B 本地加工列（与车道源解耦：不来自外部源） -->
              <div v-if="flowDerivedCols(flowMainJob).length" style="background:#FBF9F4;border:1.5px dashed #C9A227;border-radius:6px;padding:6px 8px;">
                <div style="font-size:11px;color:#633806;margin-bottom:4px;font-weight:500;">
                  B 本地加工列 *
                  <span style="color:#8a6d3b;font-weight:400;margin-left:4px;">不来自外部源 · 基于代码规则/聚合推算（与车道源无关）</span>
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:3px;">
                  <span
                    v-for="c in flowDerivedCols(flowMainJob)"
                    :key="c"
                    :title="(flowMainJob.lineage?.col_notes || {})[c] || '本地加工/推断口径列'"
                    style="font-family:Consolas,Menlo,monospace;font-size:11px;padding:2px 6px;border:1px dashed #C9A227;border-radius:4px;color:#7a5c1e;background:#FDF9EE;cursor:help;"
                  >{{ c }}*</span>
                </div>
              </div>
              <!-- C 系统列 -->
              <div v-if="flowSystemCols.length">
                <div style="font-size:11px;color:#888;margin-bottom:3px;">C 系统 / 本地管理列</div>
                <div style="display:flex;flex-wrap:wrap;gap:3px;">
                  <span
                    v-for="c in flowSystemCols"
                    :key="c"
                    title="该列由数据库/系统维护（自增、ON UPDATE、DEFAULT 等），无采集任务写入"
                    style="font-family:Consolas,Menlo,monospace;font-size:11px;padding:2px 6px;border:1px solid #e5e9ee;border-radius:4px;color:#888;background:#F6F7F9;"
                  >{{ c }}</span>
                </div>
              </div>
              <div v-if="flowMainJob.lineage?.note" style="font-size:10.5px;color:#B45309;line-height:1.5;border-top:1px dashed #ece5d8;padding-top:6px;">
                注：{{ flowMainJob.lineage.note }}
              </div>
              <div style="margin-top:auto;display:flex;align-items:center;gap:10px;font-size:10.5px;color:#a0a6ad;border-top:1px dashed #e5eaf0;padding-top:6px;flex-wrap:wrap;">
                <span><span style="color:#C9A227;">*</span> 本地加工/推算</span>
                <span><span style="color:#185FA5;">主源</span> / <span style="color:#C9A227;">备源</span></span>
                <span style="margin-left:auto;">整链图例</span>
              </div>
            </div>
          </div>

          <!-- 整链拓扑 · 多 writer：同一张表多条数据流，每 job 一条车道（源 → 步骤链 → 字段落点） -->
          <div v-else-if="flowMultiLaneReady" style="display:flex;flex-direction:column;gap:10px;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span style="font-size:11px;color:#888;">多链路写入整链 · {{ flowMultiLaneJobs.length }} 条数据流纵向并列（各车道独立展示 源 → 步骤链 → 字段落点）</span>
              <span style="margin-left:auto;font-size:10.5px;color:#a0a6ad;">下方其余维护任务见末尾清单</span>
            </div>

            <div
              v-for="(j, idx) in flowMultiLaneJobs"
              :key="j.task_name"
              style="display:flex;gap:10px;align-items:stretch;flex-wrap:nowrap;border:1px solid #eceff4;border-left:3px solid transparent;border-radius:10px;padding:10px;background:#fff;"
              :style="{ borderLeftColor: laneColor(idx) }"
            >
              <!-- 左：该 job 的数据源 -->
              <div style="flex:0 0 150px;display:flex;flex-direction:column;gap:5px;">
                <div style="font-size:11px;color:#888;margin-bottom:2px;">
                  数据源
                  <span style="color:#c3c9d0;margin-left:3px;">{{ idx + 1 }}/{{ flowMultiLaneJobs.length }}</span>
                </div>
                <div
                  :title="'该任务专属数据源（多 writer 表，各任务各写各的列）'"
                  style="border-radius:8px;padding:8px 9px;font-size:12px;line-height:1.4;"
                  :style="{ border: '1.5px solid ' + laneColor(idx), background: laneColor(idx) + '10', color: '#333' }"
                >
                  <div :style="{ fontWeight: 500, color: '#0C447C' }">{{ j.lineage?.source || flowSources[0] || '—' }}</div>
                  <div :style="{ fontSize: '10.5px', opacity: 0.72 }">写入 {{ j.lineage?.cols.length }} 列</div>
                </div>
                <div style="margin-top:auto;font-size:10.5px;color:#a0a6ad;line-height:1.5;border-top:1px dashed #e5eaf0;padding-top:5px;">
                  {{ j.lineage?.note || '' }}
                </div>
              </div>

              <div style="flex:0 0 18px;display:flex;align-items:center;justify-content:center;color:#185FA5;font-size:16px;">→</div>

              <!-- 中：任务盒 + 步骤链 + 最近实录 -->
              <div style="flex:0 0 340px;display:flex;flex-direction:column;gap:6px;">
                <div style="font-size:11px;color:#888;">维护任务 · 运行逻辑</div>
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                  <span style="font-family:Consolas,Menlo,monospace;font-weight:500;color:#185FA5;font-size:13px;">{{ j.task_name }}</span>
                  <span v-if="j.cron" style="font-size:11px;color:#555;background:#F5F8FC;border:1px solid #e0e6ed;border-radius:4px;padding:1px 6px;">{{ cronToText(j.cron, j.cron_human) }}</span>
                  <span v-if="j.enabled === false" style="font-size:11px;color:#999;">已停用</span>
                  <span v-if="j.running" style="font-size:11px;color:#185FA5;">运行中…</span>
                </div>
                <div style="display:flex;flex-direction:column;gap:4px;">
                  <div
                    v-for="s in flowStepRows(j)"
                    :key="s.no"
                    :title="s.params || ''"
                    style="display:flex;align-items:center;gap:6px;border:1px solid #eef1f5;border-left:2.5px solid #B5D4F4;border-radius:6px;padding:3px 8px;background:#FAFBFC;"
                  >
                    <span style="font-size:10.5px;color:#aaa;font-family:Consolas,Menlo,monospace;flex-shrink:0;">{{ s.no }}</span>
                    <span style="font-size:12px;color:#333;flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{{ s.name }}</span>
                    <span v-if="s.value" :title="s.value" style="margin-left:auto;flex:0 1 auto;max-width:62%;font-size:10.5px;color:#185FA5;background:#E6F1FB;border-radius:4px;padding:1px 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{{ s.value }}</span>
                    <span v-else style="margin-left:auto;flex-shrink:0;font-size:10.5px;color:#c0c6cc;">—</span>
                  </div>
                </div>
                <div
                  style="border-radius:6px;background:#F5F8FC;border:1px solid #e6ebf1;padding:4px 8px;display:flex;align-items:center;gap:6px;font-size:11px;color:#555;flex-wrap:wrap;"
                >
                  <span style="width:6px;height:6px;border-radius:50%;display:inline-block;" :style="{ background: flowLastStatus(j).color }"></span>
                  <b :style="{ color: flowLastStatus(j).color }">{{ flowLastStatus(j).text }}</b>
                  <template v-if="j.last">
                    <template v-if="j.last.status === 'success' && j.last.records_written !== null">+{{ j.last.records_written }} 条</template>
                    <span v-if="j.last.finished_at"> · {{ fmtFlowDt(j.last.finished_at) }}</span>
                    <span v-if="j.last.run_detail?.duration"> · {{ j.last.run_detail.duration }}</span>
                  </template>
                  <span v-if="j.next_run" style="margin-left:auto;color:#888;">下次 {{ fmtFlowDt(j.next_run) }}</span>
                </div>
                <div style="font-size:10.5px;color:#a0a6ad;line-height:1.5;">
                  {{ j.last ? '最近运行实录 · 步骤数值取自 task_runs 快照' : '该任务尚未运行过，步骤右侧数值将在下次运行后自动填充' }}
                </div>
              </div>

              <div style="flex:0 0 18px;display:flex;align-items:center;justify-content:center;color:#185FA5;font-size:16px;">→</div>

              <!-- 右：该 job 写入的字段落点 -->
              <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:8px;">
                <div style="font-size:11px;color:#888;">
                  字段落点 · {{ j.lineage?.cols.length }} 列
                  <span style="color:#c3c9d0;margin-left:4px;">本任务写入列（悬停查看口径）</span>
                </div>
                <!-- A 直采 -->
                <div>
                  <div style="font-size:11px;color:#888;margin-bottom:3px;">
                    A 随行源直采
                    <span style="color:#c3c9d0;margin-left:4px;">随 {{ j.lineage?.source || '源' }} 写入</span>
                  </div>
                  <div style="display:flex;flex-wrap:wrap;gap:3px;">
                    <span
                      v-for="c in flowDirectCols(j)"
                      :key="c"
                      :title="(j.lineage?.col_notes || {})[c] || ''"
                      style="font-family:Consolas,Menlo,monospace;font-size:11px;padding:2px 6px;border:1px solid #d8e2ec;border-radius:4px;color:#3a5a7a;background:#fff;"
                    >{{ c }}</span>
                  </div>
                </div>
                <!-- B 推算 -->
                <!-- B 本地加工列（与车道源解耦） -->
                <div v-if="flowDerivedCols(j).length" style="background:#FBF9F4;border:1.5px dashed #C9A227;border-radius:6px;padding:6px 8px;">
                  <div style="font-size:11px;color:#633806;margin-bottom:4px;font-weight:500;">
                    B 本地加工列 *
                    <span style="color:#8a6d3b;font-weight:400;margin-left:4px;">不来自外部源 · 基于代码规则/聚合推算（与车道源无关）</span>
                  </div>
                  <div style="display:flex;flex-wrap:wrap;gap:3px;">
                    <span
                      v-for="c in flowDerivedCols(j)"
                      :key="c"
                      :title="(j.lineage?.col_notes || {})[c] || '本地加工/推断口径列'"
                      style="font-family:Consolas,Menlo,monospace;font-size:11px;padding:2px 6px;border:1px dashed #C9A227;border-radius:4px;color:#7a5c1e;background:#FDF9EE;cursor:help;"
                    >{{ c }}*</span>
                  </div>
                </div>
                <div v-if="!flowDirectCols(j).length && !flowDerivedCols(j).length" style="color:#aaa;font-size:11px;">该任务无列级落点（仅表级维护）</div>
              </div>
            </div>

            <!-- 多 writer 表级系统列（各车道差集，仅一次） -->
            <div v-if="flowSystemCols.length" style="display:flex;align-items:flex-start;gap:8px;border:1px dashed #e6e0d2;border-radius:8px;padding:8px 10px;background:#FBF9F4;">
              <span style="font-size:11px;color:#8a7b5c;font-weight:500;flex-shrink:0;">C 系统 / 本地管理列 · {{ flowSystemCols.length }}</span>
              <div style="display:flex;gap:4px;flex-wrap:wrap;">
                <span
                  v-for="c in flowSystemCols"
                  :key="c"
                  title="该列由数据库/系统维护（自增、ON UPDATE、DEFAULT 等），无采集任务写入"
                  style="font-family:Consolas,Menlo,monospace;font-size:11px;line-height:1;padding:2px 6px;border-radius:4px;border:1px dashed #ded8c8;color:#9a8a66;background:#fff;white-space:nowrap;"
                >{{ c }}</span>
              </div>
            </div>

            <!-- 多 writer 表中未达整链要素的兜底清单 -->
            <div v-if="flowPlainJobs.length" style="display:flex;flex-direction:column;gap:4px;">
              <div style="font-size:11px;color:#888;">其余维护任务（未配置列级血缘/步骤模板）</div>
              <div
                v-for="j in flowPlainJobs"
                :key="j.task_name"
                :title="(j.last && j.last.error_message) || ''"
                style="display:flex;align-items:center;gap:8px;font-size:12px;flex-wrap:wrap;background:#FAFBFC;border:1px solid #f0f0f0;border-radius:6px;padding:5px 10px;"
              >
                <span style="font-family:Consolas,Menlo,monospace;font-weight:600;color:#185FA5;">{{ j.task_name }}</span>
                <span style="color:#555;">{{ cronToText(j.cron, j.cron_human) }}</span>
                <span v-if="j.enabled === false" style="font-size:11px;color:#999;">已停用</span>
                <span style="margin-left:auto;display:flex;gap:10px;color:#999;font-size:11px;flex-wrap:wrap;">
                  <span>
                    <b :style="{ color: flowLastStatus(j).color }">{{ flowLastStatus(j).text }}</b>
                    <template v-if="j.last && j.last.status === 'success' && j.last.records_written !== null">+{{ j.last.records_written }} 条</template>
                  </span>
                  <span v-if="j.next_run">下次 {{ fmtFlowDt(j.next_run) }}</span>
                </span>
              </div>
            </div>
          </div>

          <!-- 列级血缘拓扑（writer_cols 已配置时，多任务/未配步骤链表用车道视图） -->
          <div v-else-if="flowHasLineage">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
              <span style="font-size:11px;color:#888;">列级数据血缘</span>
              <span style="font-size:11px;color:#aaa;">数据源 → 采集任务 → 写入列（悬停某车道高亮联动）</span>
            </div>
            <div style="display:flex;flex-direction:column;gap:8px;">
              <div
                v-for="(j, idx) in flowOf.jobs"
                :key="j.task_name"
                @mouseenter="flowHover = idx"
                @mouseleave="flowHover = null"
                style="display:flex;flex-direction:column;gap:6px;border:1px solid #eceff4;border-left:3px solid transparent;border-radius:8px;padding:8px 10px;background:#fff;transition:background .15s,opacity .15s;"
                :style="{
                  borderLeftColor: laneColor(idx),
                  background: flowHover === idx ? '#F5F9FD' : '#fff',
                  opacity: flowHover !== null && flowHover !== idx ? 0.55 : 1,
                }"
              >
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12px;">
                  <!-- 数据源 -->
                  <NTag v-if="j.lineage?.source" size="small" :bordered="false" :style="{ color: laneColor(idx), background: laneColor(idx) + '14', fontWeight: 500 }">
                    {{ j.lineage.source }}
                  </NTag>
                  <!-- 任务 + cron -->
                  <span style="font-family:Consolas,Menlo,monospace;font-weight:600;color:#222;font-size:12px;">{{ j.task_name }}</span>
                  <span style="color:#777;font-size:11.5px;">{{ cronToText(j.cron, j.cron_human) }}</span>
                  <span v-if="j.enabled === false" style="font-size:11px;color:#999;">已停用</span>
                  <span v-if="j.running" style="font-size:11px;color:#185FA5;">运行中…</span>
                  <span style="margin-left:auto;display:flex;gap:10px;color:#999;font-size:11px;flex-wrap:wrap;">
                    <span>
                      <b :style="{ color: flowLastStatus(j).color }">{{ flowLastStatus(j).text }}</b>
                      <template v-if="j.last">
                        <template v-if="j.last.status === 'success' && j.last.records_written !== null">
                          +{{ j.last.records_written }} 条
                        </template>
                        <span v-if="j.last.finished_at"> · {{ fmtFlowDt(j.last.finished_at) }}</span>
                      </template>
                    </span>
                    <span v-if="j.next_run">下次 {{ fmtFlowDt(j.next_run) }}</span>
                  </span>
                </div>
                <!-- 写入列 chips -->
                <div style="display:flex;align-items:flex-start;gap:4px;flex-wrap:wrap;padding-left:2px;">
                  <template v-if="j.lineage?.cols.length">
                    <span
                      v-for="c in j.lineage.cols"
                      :key="c"
                      :title="(j.lineage?.derived || []).includes(c) ? (j.lineage?.col_notes?.[c] || '本地加工/推断口径列') : (j.lineage?.col_notes?.[c] || '')"
                      style="font-family:Consolas,Menlo,monospace;font-size:11px;line-height:1;padding:3px 7px;border-radius:4px;border:1px solid #dfe4ea;color:#444;background:#FAFBFC;white-space:nowrap;"
                      :style="{
                        borderStyle: (j.lineage?.derived || []).includes(c) ? 'dashed' : 'solid',
                        borderColor: (j.lineage?.derived || []).includes(c) ? laneColor(idx) : '#dfe4ea',
                        color: (j.lineage?.derived || []).includes(c) ? laneColor(idx) : '#444',
                      }"
                    >
                      {{ c }}<span v-if="(j.lineage?.derived || []).includes(c)">*</span>
                    </span>
                    <span style="font-size:11px;color:#aaa;align-self:center;">共 {{ j.lineage?.cols.length }} 列</span>
                  </template>
                  <span v-else style="font-size:11px;color:#bbb;">该任务列级映射未配置，仅维护表级</span>
                </div>
                <div v-if="j.lineage?.note" style="font-size:11px;color:#B45309;line-height:1.5;">
                  注：{{ j.lineage.note }}
                </div>
              </div>

              <!-- 系统/本地管理列（未写列差集） -->
              <div
                v-if="flowSystemCols.length"
                @mouseenter="flowHover = -1"
                @mouseleave="flowHover = null"
                style="display:flex;flex-direction:column;gap:6px;border:1px dashed #e6e0d2;border-radius:8px;padding:8px 10px;background:#FBF9F4;transition:opacity .15s;"
                :style="{ opacity: flowHover !== null && flowHover !== -1 ? 0.55 : 1 }"
              >
                <div style="display:flex;align-items:center;gap:8px;font-size:12px;">
                  <span style="font-size:11.5px;color:#8a7b5c;font-weight:500;">系统 / 本地管理列</span>
                  <span style="font-size:11px;color:#bbb;">无采集任务写入</span>
                  <span style="margin-left:auto;font-size:11px;color:#aaa;">{{ flowSystemCols.length }} 列</span>
                </div>
                <div style="display:flex;gap:4px;flex-wrap:wrap;">
                  <span
                    v-for="c in flowSystemCols"
                    :key="c"
                    title="该列由数据库/系统维护（自增、ON UPDATE、DEFAULT 等），无采集任务写入"
                    style="font-family:Consolas,Menlo,monospace;font-size:11px;line-height:1;padding:3px 7px;border-radius:4px;border:1px dashed #ded8c8;color:#9a8a66;background:#fff;white-space:nowrap;"
                  >{{ c }}</span>
                </div>
              </div>
            </div>
            <div style="margin-top:6px;font-size:11px;color:#aaa;">* 本地加工/推断口径列（非外部源直采），悬停查看口径说明</div>
          </div>

          <!-- 降级：无列级血缘时展示任务清单（静态表/未配置） -->
          <div v-else>
            <div style="font-size:11px;color:#888;margin-bottom:6px;">维护作业</div>
            <div v-if="flowOf.jobs.length" style="display:flex;flex-direction:column;gap:6px;">
              <div
                v-for="j in flowOf.jobs"
                :key="j.task_name"
                :title="(j.last && j.last.error_message) || ''"
                style="display:flex;align-items:center;gap:8px;font-size:12px;flex-wrap:wrap;background:#FAFBFC;border:1px solid #f0f0f0;border-radius:6px;padding:6px 10px;"
              >
                <span style="font-family:Consolas,Menlo,monospace;font-weight:600;color:#185FA5;">{{ j.task_name }}</span>
                <span style="color:#555;">{{ cronToText(j.cron, j.cron_human) }}</span>
                <span v-if="j.enabled === false" style="font-size:11px;color:#999;">已停用</span>
                <span v-if="j.running" style="font-size:11px;color:#185FA5;">运行中…</span>
                <span style="margin-left:auto;display:flex;gap:10px;color:#999;font-size:11px;flex-wrap:wrap;">
                  <span>
                    <b :style="{ color: flowLastStatus(j).color }">{{ flowLastStatus(j).text }}</b>
                    <template v-if="j.last">
                      <template v-if="j.last.status === 'success' && j.last.records_written !== null">
                        +{{ j.last.records_written }} 条
                      </template>
                      <span v-if="j.last.finished_at"> · {{ fmtFlowDt(j.last.finished_at) }}</span>
                    </template>
                  </span>
                  <span v-if="j.next_run">下次 {{ fmtFlowDt(j.next_run) }}</span>
                </span>
              </div>
            </div>
            <div v-else style="color:#999;line-height:1.6;">
              静态/历史表：无自动采集作业（{{ flowOf.source_desc || '人工或一次性导入' }}）
            </div>
          </div>
        </div>
        <div v-else>
          <NEmpty description="该表暂无数据流元数据" />
        </div>
        </NSpin>
        <template #footer>
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:11px;color:#999;">数据来源：table_meta 元数据 + task_runs 运行记录</span>
            <NButton size="small" type="primary" secondary @click="router.push('/jobs')">查看作业监控栏目 ▸</NButton>
          </div>
        </template>
      </NModal>

      <!-- 底部状态 -->
      <div style="padding:6px 14px;border-top:1px solid #f0f0f0;background:#FAFBFC;display:flex;justify-content:space-between;align-items:center;">
        <span style="font-size:12px;color:#888;">已加载 {{ rows.length }} 行{{ hasMore ? '' : ' · 已全部加载' }}</span>
        <NButton v-if="hasMore" size="small" :loading="loadingRows" @click="loadMore()">加载更多</NButton>
        <span v-else style="font-size:12px;color:#bbb;">只读视图 · 如需写操作请使用 MySQL 客户端</span>
      </div>
    </div>
  </div>

  <!-- 数据探查（SQL 弹窗）：只读、15s 超时、强制 LIMIT -->
  <SqlExploreModal
    v-if="tables.length || showSql"
    :show="showSql"
    :current-table="current"
    :tables="tables.map(t => ({ name: t.name, comment: t.comment }))"
    @update:show="(v: boolean) => (showSql = v)"
  />

  <!-- 排序偏好（服务端持久化管理弹窗） -->
  <SortPrefsModal
    :show="showSortPrefs"
    :prefs="sortPrefs"
    :tables="tables.map(t => ({ name: t.name, comment: t.comment }))"
    @update:show="(v: boolean) => (showSortPrefs = v)"
    @reset="resetSortPref"
    @clear-all="clearSortPrefs"
  />
</template>

<style scoped>
.c-num {
  font-variant-numeric: tabular-nums;
}
.c-date {
  color: #185FA5;
}
/* 左侧表清单：组头/分类头/表行 hover 反馈，折叠交互可发现 */
.grp-head:hover,
.cat-head:hover {
  background: #f2f6fb;
}
.tbl-row:hover {
  background: #f7f9fc;
}
/* 手工排序：悬停时用 ↑↓ 按钮替换右侧行数，常态布局不变 */
.row-move {
  display: none;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}
.tbl-row:hover .row-move,
.cat-head:hover .row-move {
  display: inline-flex;
}
.tbl-row:hover .row-count,
.cat-head:hover .row-count {
  display: none;
}
.mv-btn {
  width: 17px;
  height: 17px;
  line-height: 1;
  padding: 0;
  border: 1px solid #d9e3ef;
  border-radius: 4px;
  background: #fff;
  color: #185FA5;
  font-size: 11px;
  cursor: pointer;
}
.mv-btn:hover:not(:disabled) {
  background: #e6f1fb;
  border-color: #185fa5;
}
.mv-btn:disabled {
  color: #cccccc;
  border-color: #eeeeee;
  cursor: default;
}
/* 拖拽插入位置指示线（2px 深空蓝） */
.tbl-row.drop-before,
.cat-head.drop-before {
  box-shadow: inset 0 2px 0 #185fa5;
}
.tbl-row.drop-after,
.cat-head.drop-after {
  box-shadow: inset 0 -2px 0 #185fa5;
}
.tbl-row[draggable='true']:active,
.cat-head[draggable='true']:active {
  cursor: grabbing;
}
</style>
