<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NButton, NEmpty, NInput, NSelect, NSpin, NTag,
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
interface FlowLast {
  status: string | null
  started_at: string | null
  finished_at: string | null
  records_written: number | null
  error_message: string | null
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

const router = useRouter()
const dqRunAt = ref<string | null>(null)
const dqStatus = ref<Map<string, DqTableStatus>>(new Map())
const flowMap = ref<Map<string, FlowInfo>>(new Map())
const flowOpen = ref(true)

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

      <!-- 数据质量摘要 -->
      <div v-if="dqRunAt" style="padding:8px 14px;border-bottom:1px solid #f0f0f0;background:#FAFBFC;">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <span style="font-size:12px;font-weight:500;color:#444;">数据质量</span>
          <span style="font-size:11px;color:#aaa;">体检 {{ fmtDqTime(dqRunAt) }}</span>
          <NTag v-if="currentDq" size="tiny" :bordered="false" :type="dqTagType(currentDq.worst)">
            {{ WORST_LABEL[currentDq.worst] }}
          </NTag>
          <NTag v-else size="tiny" :bordered="false" type="default">未纳入体检</NTag>
          <span style="margin-left:auto;display:flex;gap:6px;align-items:center;">
            <NButton size="tiny" quaternary @click="loadDqStatus()">刷新状态</NButton>
            <NButton size="tiny" type="primary" secondary @click="router.push('/quality')">查看质量报告</NButton>
          </span>
        </div>
        <div v-if="currentDq" style="margin-top:6px;display:flex;flex-direction:column;gap:4px;">
          <div v-if="currentDq.issues.length === 0" style="font-size:12px;color:#18A058;">
            共 {{ currentDq.counts.total }} 条规则全部通过
          </div>
          <div
            v-for="iss in currentDq.issues"
            :key="iss.rule_name"
            :title="iss.message || ''"
            style="display:flex;align-items:center;gap:6px;font-size:12px;line-height:1.4;min-width:0;"
          >
            <span style="width:6px;height:6px;border-radius:50%;flex-shrink:0;" :style="{ background: dqIssueColor(iss.status) }"></span>
            <span style="font-family:Consolas,Menlo,monospace;color:#333;flex-shrink:0;">{{ iss.rule_name }}</span>
            <span style="color:#888;flex-shrink:0;">{{ dqIssueStatusLabel(iss.status) }} · {{ iss.severity }}</span>
            <span style="color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;">{{ iss.message }}</span>
          </div>
        </div>
        <div v-else style="margin-top:6px;font-size:12px;color:#999;">
          静态/历史表未纳入自动体检，可在数据质量栏目「规则配置」中补充该表规则
        </div>
      </div>

      <!-- 数据流（数据源 / 清洗口径 / 维护作业调度） -->
      <div v-if="flowOf" style="padding:8px 14px;border-bottom:1px solid #f0f0f0;">
        <div
          style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;"
          @click="flowOpen = !flowOpen"
        >
          <span style="font-size:12px;font-weight:600;color:#185FA5;">数据流</span>
          <NTag v-if="flowOf.category" size="tiny" :bordered="false" type="info">{{ flowOf.category }}</NTag>
          <span style="font-size:11px;color:#999;">
            {{ flowOf.writers.length ? `${flowOf.writers.length} 个维护任务` : '无采集任务' }}
          </span>
          <span style="margin-left:auto;display:flex;align-items:center;gap:10px;">
            <NButton size="tiny" quaternary @click.stop="router.push('/jobs')">作业监控</NButton>
            <span style="font-size:11px;color:#bbb;">{{ flowOpen ? '收起 ▴' : '展开 ▾' }}</span>
          </span>
        </div>
        <template v-if="flowOpen">
          <div v-if="flowSources.length" style="margin-top:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <span style="font-size:11px;color:#888;">数据源</span>
            <NTag v-for="src in flowSources" :key="src" size="tiny" :bordered="false" type="info">{{ src }}</NTag>
          </div>
          <div v-if="flowOf.flow_desc" style="margin-top:6px;font-size:12px;line-height:1.6;color:#555;overflow-wrap:anywhere;">
            {{ flowOf.flow_desc }}
          </div>
          <div v-if="flowOf.note" style="margin-top:4px;font-size:11px;color:#B45309;">备注：{{ flowOf.note }}</div>
          <div v-if="flowOf.jobs.length" style="margin-top:8px;display:flex;flex-direction:column;gap:6px;">
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
          <div v-else style="margin-top:8px;font-size:12px;color:#999;">
            静态/历史表：无自动采集作业（{{ flowOf.source_desc || '人工或一次性导入' }}）
          </div>
        </template>
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
