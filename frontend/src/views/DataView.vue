<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NButton, NEmpty, NInput, NModal, NSelect, NSpin, NTable, NTag,
  type SelectOption,
} from 'naive-ui'
import api from '../api'
import { useRouter } from 'vue-router'

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
  loadColumns(name).then(() => loadRows(false))
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
// 直采列（随行源写入）与推算列（derived，清洗口径标注）
function flowDirectCols(j: FlowJob): string[] {
  return (j.lineage?.cols || []).filter((c) => !(j.lineage?.derived || []).includes(c))
}
function flowDerivedCols(j: FlowJob): string[] {
  return (j.lineage?.cols || []).filter((c) => (j.lineage?.derived || []).includes(c))
}

const DOW_CN: Record<string, string> = {
  '0': '周日', '1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六', '7': '周日',
}

/** 极简 5 段 cron → 中文（覆盖本平台 15 任务所用表达式） */
function cronToText(cron: string | null): string {
  const s = (cron || '').trim()
  if (!s) return ''
  if (s === '手动') return '手动触发'
  const p = s.split(/\s+/)
  if (p.length !== 5) return s
  const [min, hour, dom, , dow] = p
  if (min === '*/30' && hour === '*') return '每30分钟'
  let when: string
  if (dow === '1-5') when = '工作日'
  else if (dow !== '*') when = DOW_CN[dow] || `周${dow}`
  else if (dom !== '*') when = `每月${dom}日`
  else when = '每天'
  if (hour === '*') return when
  const mm = min === '0' ? '00' : min
  return `${when} ${hour.padStart(2, '0')}:${mm}`
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
  if (st === 'running') return { text: '运行中', color: '#185FA5' }
  return { text: '从未运行', color: '#999' }
}

function dqDotColor(name: string): string {
  const s = dqStatus.value.get(name)
  return s ? DOT_COLORS[s.worst] : DOT_COLORS.none
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

function dqIssueStatusLabel(status: string): string {
  if (status === 'error') return '错误'
  if (status === 'fail') return '异常'
  return '提醒'
}

function dqIssueColor(status: string): string {
  if (status === 'error' || status === 'fail') return '#d03050'
  return '#d48806'
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

onMounted(loadTables)
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
      </div>
      <div style="flex:1;overflow:auto;padding:6px;">
        <NSpin :show="loadingTables" size="small">
          <div v-if="!loadingTables && filteredTables.length === 0" style="padding:24px 0;">
            <NEmpty description="无匹配表" />
          </div>
          <div
            v-for="t in filteredTables"
            :key="t.name"
            :title="dqRowTitle(t)"
            style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;padding:7px 10px;border-radius:6px;cursor:pointer;font-size:13px;"
            :style="current === t.name
              ? 'background:#E6F1FB;color:#185FA5;font-weight:600;'
              : 'color:#333;'"
            @click="selectTable(t.name)"
          >
            <span
              v-if="dqRunAt"
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
            <span
              style="font-size:11px;flex-shrink:0;align-self:flex-start;margin-top:1px;"
              :style="current === t.name ? 'color:#7BA7D4;' : 'color:#b0b0b0;'"
            >{{ fmtWan(t.rows_estimate) }}</span>
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
        <span style="font-size:12px;color:#888;">{{ totalText }}</span>
        <span v-if="meta?.update_time" style="font-size:12px;color:#bbb;">更新 {{ meta.update_time.replace('T', ' ').slice(0, 19) }}</span>
        <span style="margin-left:auto;display:flex;gap:6px;align-items:center;">
          <NButton size="tiny" quaternary @click="reload()">刷新</NButton>
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
            <NButton size="small" type="primary" secondary @click="router.push('/quality')">查看质量报告栏目 ▸</NButton>
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
                <span v-if="flowMainJob.cron" style="font-size:11px;color:#555;background:#F5F8FC;border:1px solid #e0e6ed;border-radius:4px;padding:1px 6px;">{{ cronToText(flowMainJob.cron) }}</span>
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
                  <span style="font-size:12px;color:#333;min-width:0;">{{ s.name }}</span>
                  <span v-if="s.value" style="margin-left:auto;font-size:10.5px;color:#185FA5;background:#E6F1FB;border-radius:4px;padding:1px 5px;white-space:nowrap;">{{ s.value }}</span>
                  <span v-else style="margin-left:auto;font-size:10.5px;color:#c0c6cc;">—</span>
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
                  <span style="color:#c3c9d0;margin-left:4px;">命中源为整行来源，价格按 qfq 前复权</span>
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
              <div v-if="flowDerivedCols(flowMainJob).length">
                <div style="font-size:11px;color:#8a6d3b;margin-bottom:3px;">
                  B 推算列 *
                  <span style="color:#b6a887;margin-left:4px;">源缺失时按口径本地补齐</span>
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
                  <span style="color:#777;font-size:11.5px;">{{ cronToText(j.cron) }}</span>
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
                <span style="color:#555;">{{ cronToText(j.cron) }}</span>
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
</template>

<style scoped>
.c-num {
  font-variant-numeric: tabular-nums;
}
.c-date {
  color: #185FA5;
}
</style>
