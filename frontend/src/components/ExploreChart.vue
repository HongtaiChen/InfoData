<script setup lang="ts">
/**
 * ExploreChart —— 数据探查结果可视化
 * 输入任意 SQL 结果（columns + rows），启发式映射列 → 轴，
 * 自动推荐图表类型（K线/折线/柱状/散点），支持手动改映射与类型。
 * 零后端依赖：数据已在前端内存。
 */
import { computed, reactive, ref, watch } from 'vue'
import { NAlert, NCheckbox, NRadioButton, NRadioGroup, NSelect, NTag, type SelectOption } from 'naive-ui'
import VChart from 'vue-echarts'
import { ensureEcharts, SERIES_PALETTE, UP_COLOR, DOWN_COLOR, BRAND_COLOR } from '../echarts'

ensureEcharts()

interface ColInfo { name: string; type: string }
interface Props {
  columns: ColInfo[]
  rows: Record<string, unknown>[]
  truncated?: boolean
  limit?: number
}
const props = defineProps<Props>()

type ChartKind = 'kline' | 'line' | 'bar' | 'scatter'

const NUM_TYPES = new Set([
  'TINYINT', 'SMALLINT', 'INT', 'INT24', 'BIGINT',
  'FLOAT', 'DOUBLE', 'DECIMAL', 'NEWDECIMAL',
])
function isNumeric(t: string): boolean {
  return NUM_TYPES.has((t || '').toUpperCase())
}

// ---------------- 启发式列映射 ----------------

const X_PATTERNS = ['trade_date', 'tradedate', 'date', 'dt', 'time', 'period', '日期', '交易日', 'day', 'month', 'week']
const OPEN_PATTERNS = ['open', 'open_price', '开盘']
const HIGH_PATTERNS = ['high', 'high_price', '最高']
const LOW_PATTERNS = ['low', 'low_price', '最低']
const CLOSE_PATTERNS = ['close', 'close_price', '收盘', 'latest', '最新价', 'closeprice']
const VOL_PATTERNS = ['volume', 'vol', '成交量', '成交额', 'amount', 'tradingvalue', 'tradingvol']
const GROUP_PATTERNS = ['index_code', 'stock_code', 'concept_code', 'fund_code', 'code', 'symbol', 'index_name', 'stock_name', 'concept_name', 'name', '名称', '代码']

function norm(s: string): string {
  return (s || '').toLowerCase().replace(/[_\s-]/g, '')
}

/** 在列清单里找第一个命中关键词的列（关键词本身也做归一化包含匹配） */
function matchCol(patterns: string[], cols: ColInfo[]): string | null {
  const pats = patterns.map(norm)
  for (const c of cols) {
    const n = norm(c.name)
    for (const p of pats) {
      if (n === p || n.includes(p)) return c.name
    }
  }
  return null
}

const colNames = computed(() => props.columns.map((c) => c.name))
const numericCols = computed(() => props.columns.filter((c) => isNumeric(c.type)).map((c) => c.name))

/** 映射状态（查询变化时自动重算并覆盖） */
const map = reactive({
  x: '' as string,
  open: '', high: '', low: '', close: '', vol: '', group: '',
  ys: [] as string[],
  scatterX: '', scatterY: '',
})

function autoMap() {
  const cols = props.columns
  // X 轴优先取「非数值的日期类列」；没有日期类则取第一列
  const xHit = matchCol(X_PATTERNS, cols.filter((c) => !isNumeric(c.type)))
  map.x = xHit ?? colNames.value[0] ?? ''
  map.open = matchCol(OPEN_PATTERNS, cols) ?? ''
  map.high = matchCol(HIGH_PATTERNS, cols) ?? ''
  map.low = matchCol(LOW_PATTERNS, cols) ?? ''
  map.close = matchCol(CLOSE_PATTERNS, cols) ?? ''
  // 成交量列不能与 OHLC 重复
  const volHit = matchCol(VOL_PATTERNS, cols)
  map.vol = volHit && ![map.open, map.high, map.low, map.close].includes(volHit) ? volHit : ''
  map.group = matchCol(GROUP_PATTERNS.filter((p) => !['name'].includes(norm(p))), cols) ?? ''
  map.ys = numericCols.value.filter((c) => c !== map.vol && c !== map.group).slice(0, 3)
  map.scatterX = numericCols.value[0] ?? ''
  map.scatterY = numericCols.value[1] ?? ''
}

/** 列结构变化时由下方「配置记忆」watcher 统一触发 autoMap + 记忆恢复（见 applySavedCfg） */

/** 结果集里分组列的去重值数（>1 才值得分组） */
const groupValues = computed<string[]>(() => {
  if (!map.group) return []
  const s = new Set<string>()
  for (const r of props.rows) {
    const v = r[map.group]
    if (v !== null && v !== undefined) s.add(String(v))
  }
  return [...s]
})
const groupable = computed(() => map.group !== '' && groupValues.value.length > 1 && groupValues.value.length <= 30)
const groupValueOptions = computed<SelectOption[]>(() => groupValues.value.map((v) => ({ label: v, value: v })))

/** K 线模式下当前选中的标的（分组列多值时生效） */
const klineGroup = ref('')
watch(groupValues, (gv) => {
  if (gv.length && !gv.includes(klineGroup.value)) klineGroup.value = gv[0]
  if (!gv.length) klineGroup.value = ''
})

// ---------------- 图表类型推荐 ----------------

const hasOHLC = computed(() => !!(map.open && map.high && map.low && map.close))
const canLine = computed(() => !!(map.x && map.ys.length > 0))
const canScatter = computed(() => !!(map.scatterX && map.scatterY))

const resolvedKind = computed<ChartKind | null>(() => {
  if (hasOHLC.value) return 'kline'
  if (canLine.value) return 'line'
  if (map.x && map.ys.length > 0) return 'bar'
  if (canScatter.value) return 'scatter'
  return null
})

/** 用户手动选择；undefined = 跟随推荐 */
const manualKind = ref<ChartKind | undefined>(undefined)
const kind = computed<ChartKind | null>(() => manualKind.value ?? resolvedKind.value)
/** 折线「收益率%」归一化模式：每组以首个有效点为基准 */
const pctMode = ref(false)
/** tooltip 按累计收益率降序排列（默认开，记忆偏好） */
const sortDesc = ref(true)

const kindOptions = computed<SelectOption[]>(() => {
  const opts: SelectOption[] = []
  if (hasOHLC.value) opts.push({ label: 'K 线', value: 'kline' })
  if (canLine.value) opts.push({ label: '折线', value: 'line' })
  if (map.x && map.ys.length > 0) opts.push({ label: '柱状', value: 'bar' })
  if (canScatter.value) opts.push({ label: '散点', value: 'scatter' })
  return opts.length ? opts : [{ label: '（无可画字段）', value: 'none' }]
})

// ---------------- 下拉选项 ----------------

const colOptions = computed<SelectOption[]>(() => colNames.value.map((n) => ({ label: n, value: n })))
const numOptions = computed<SelectOption[]>(() => numericCols.value.map((n) => ({ label: n, value: n })))
const groupOptions = computed<SelectOption[]>(() => [
  { label: '（不分组）', value: '' },
  ...colNames.value.map((n) => ({ label: n, value: n })),
])
const yOptions = computed<SelectOption[]>(() => numOptions.value)

// ---------------- 数据变换 ----------------

function toNum(v: unknown): number {
  if (v === null || v === undefined || v === '') return NaN
  const n = Number(v)
  return Number.isFinite(n) ? n : NaN
}

/** x 值规整：日期取前 10 位（兼容 datetime ISO），其余转字符串 */
function toX(v: unknown): string {
  if (v === null || v === undefined) return ''
  const s = String(v)
  return s.length > 10 && /^\d{4}-\d{2}-\d{2}T/.test(s) ? s.slice(0, 10) : s
}

/** 探查模板常是 ORDER BY 1 DESC，K 线/时序必须旧→新：按 x 升序稳定排序 */
const sortedRows = computed<Record<string, unknown>[]>(() => {
  if (!map.x) return props.rows
  const x = map.x
  return [...props.rows].sort((a, b) => {
    const av = toX(a[x]); const bv = toX(b[x])
    if (av === bv) return 0
    return av < bv ? -1 : 1
  })
})

/** 分组索引：key = `${group}|${x}` → row（折线/散点分组取数用） */
const groupIndex = computed<Map<string, Record<string, unknown>>>(() => {
  const m = new Map<string, Record<string, unknown>>()
  if (!groupable.value) return m
  for (const r of sortedRows.value) {
    const g = r[map.group]
    if (g === null || g === undefined) continue
    m.set(`${g}|${toX(r[map.x])}`, r)
  }
  return m
})

/** K 线实际使用的数据：分组列多值时仅取当前选中标的，避免多标的日期交错画出错乱蜡烛 */
const klineRows = computed<Record<string, unknown>[]>(() => {
  if (kind.value !== 'kline' || !groupable.value) return sortedRows.value
  const g = groupValues.value.includes(klineGroup.value) ? klineGroup.value : groupValues.value[0]
  return sortedRows.value.filter((r) => String(r[map.group]) === g)
})
/** 蜡烛数据 [open, close, low, high]，tooltip 涨跌计算复用 */
const klineData = computed<number[][]>(() =>
  klineRows.value.map((r) => [toNum(r[map.open]), toNum(r[map.close]), toNum(r[map.low]), toNum(r[map.high])]),
)

// ---------------- option 构建 ----------------

const GRID_SINGLE = { left: 60, right: 20, top: 36, bottom: 60 }

// ---------------- MA 均线 ----------------

const MA_DEFS = [
  { key: 'ma5', n: 5, color: '#B45309' },
  { key: 'ma10', n: 10, color: '#7C3AED' },
  { key: 'ma20', n: 20, color: '#185FA5' },
  { key: 'ma60', n: 60, color: '#667085' },
] as const
/** 默认展示 MA5/10/20 */
const mas = ref<string[]>(['ma5', 'ma10', 'ma20'])

// ---------------- 配置记忆（localStorage，按列结构签名恢复） ----------------

const CFG_STORE_KEY = 'infodata.explorechart.v1'
/** 签名 = 列名:类型 拼接 —— 同结构查询（同表/同 SELECT）共享一份记忆 */
const colSig = computed(() => props.columns.map((c) => `${c.name}:${c.type}`).join('|'))

type ChartCfg = { k?: string; m?: Record<string, unknown>; mas?: string[]; p?: boolean; s?: boolean }

function loadCfgStore(): Record<string, ChartCfg> {
  try {
    return JSON.parse(localStorage.getItem(CFG_STORE_KEY) || '{}') as Record<string, ChartCfg>
  } catch { return {} }
}

function saveCfgStore(store: Record<string, ChartCfg>) {
  try { localStorage.setItem(CFG_STORE_KEY, JSON.stringify(store)) } catch { /* ignore */ }
}

/** 在启发式映射之上叠加记忆配置（仅接受当前结果集中真实存在的列，防脏数据） */
function applySavedCfg() {
  const cfg = loadCfgStore()[colSig.value]
  if (!cfg) return
  const valid = new Set(colNames.value)
  const numSet = new Set(numericCols.value)
  const m = (cfg.m || {}) as Record<string, unknown>
  const pick = (v: unknown, allowed?: Set<string>): string =>
    typeof v === 'string' && v && (allowed ? allowed.has(v) : valid.has(v)) ? v : ''
  map.x = pick(m.x) || map.x
  map.open = pick(m.open, numSet)
  map.high = pick(m.high, numSet)
  map.low = pick(m.low, numSet)
  map.close = pick(m.close, numSet)
  map.vol = pick(m.vol, numSet)
  map.group = pick(m.group)
  const ys = Array.isArray(m.ys)
    ? (m.ys as unknown[]).filter((y): y is string => typeof y === 'string' && numSet.has(y))
    : []
  if (ys.length) map.ys = ys
  map.scatterX = pick(m.scatterX, numSet) || map.scatterX
  map.scatterY = pick(m.scatterY, numSet) || map.scatterY
  if (cfg.k === 'kline' || cfg.k === 'line' || cfg.k === 'bar' || cfg.k === 'scatter') {
    manualKind.value = cfg.k
  }
  if (Array.isArray(cfg.mas)) mas.value = cfg.mas.filter((k) => MA_DEFS.some((d) => d.key === k))
  pctMode.value = !!cfg.p
  if (typeof cfg.s === 'boolean') sortDesc.value = cfg.s
}

/** 列结构变化（新查询/换表）→ 重算启发式映射，再叠加该结构的历史记忆 */
watch(() => props.columns, () => {
  autoMap()
  manualKind.value = undefined
  pctMode.value = false
  sortDesc.value = true
  klineGroup.value = ''
  applySavedCfg()
}, { immediate: true })

// 任意映射/类型/MA/收益率变化 → 持久化到当前列结构签名（保留最近 30 条防膨胀）
watch(
  () => JSON.stringify({
    k: manualKind.value,
    m: {
      x: map.x, open: map.open, high: map.high, low: map.low, close: map.close,
      vol: map.vol, group: map.group, ys: map.ys, sx: map.scatterX, sy: map.scatterY,
    },
    mas: mas.value,
    p: pctMode.value,
    s: sortDesc.value,
  }),
  (snap) => {
    const store = loadCfgStore()
    store[colSig.value] = JSON.parse(snap) as ChartCfg
    const keys = Object.keys(store)
    if (keys.length > 30) delete store[keys[0]]
    saveCfgStore(store)
  },
)

function maValues(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i]
    if (i >= n) sum -= closes[i - n]
    out.push(i >= n - 1 ? Math.round((sum / n) * 100) / 100 : null)
  }
  return out
}

function upDownColor(v: number): string {
  return v > 0 ? UP_COLOR : v < 0 ? DOWN_COLOR : '#475467'
}

function fmtPrice(v: number): string {
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** K 线 tooltip：中文字段 + 涨跌/涨跌幅红涨绿跌 + MA 值；成交量子图单独走简化分支 */
function klineTooltipFormatter(params: unknown): string {
  const ps = params as { seriesType: string; seriesName: string; dataIndex: number; axisValue: string; value: unknown }[]
  const k = ps.find((p) => p.seriesType === 'candlestick')
  if (!k) {
    const v = ps[0]
    if (v && v.seriesName === '成交量') {
      const vol = Array.isArray(v.value) ? Number(v.value[1]) : Number(v.value)
      return `<b>${v.axisValue}</b><br/>成交量：<b>${compactNum(vol)}</b>`
    }
    return ''
  }
  const i = k.dataIndex
  const [o, c, lo, hi] = (k.value as number[]).map(Number)
  const prev = i > 0 ? klineData.value[i - 1][1] : o
  const chg = c - prev
  const pct = prev ? (chg / prev) * 100 : 0
  const cl = upDownColor(chg)
  const sign = (v: number) => `${v > 0 ? '+' : ''}${Number.isFinite(v) ? v.toFixed(2) : '--'}`
  let html = `<b>${k.axisValue}</b><br/>`
  html += `开 <b>${fmtPrice(o)}</b>　收 <b style="color:${cl}">${fmtPrice(c)}</b><br/>`
  html += `高 <b>${fmtPrice(hi)}</b>　低 <b>${fmtPrice(lo)}</b><br/>`
  html += `涨跌 <b style="color:${cl}">${sign(chg)}</b>　涨幅 <b style="color:${cl}">${sign(pct)}%</b>`
  const closes = klineData.value.map((d) => d[1])
  for (const d of MA_DEFS) {
    if (!mas.value.includes(d.key)) continue
    const v = maValues(closes, d.n)[i]
    if (v != null) html += `<br/><span style="color:${d.color}">MA${d.n} ${fmtPrice(v)}</span>`
  }
  return html
}

function signed(v: number): string {
  return `${v > 0 ? '+' : ''}${Number.isFinite(v) ? v.toFixed(2) : '--'}`
}

/** 折线 tooltip 用：seriesName → 原始值数组（归一化前的真实价格等） */
let lineRawMap: Record<string, (number | null)[]> = {}
/** 收益率归一化的基准日期（首个有效点的 x） */
const baseDate = ref('')

function firstFinite(arr: (number | null)[]): number {
  for (const v of arr) {
    if (v != null && Number.isFinite(v)) return v
  }
  return NaN
}

function fmtVal(v: number): string {
  return Number.isFinite(v) ? v.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '--'
}

/** 折线 tooltip：原值 + 较首日累计收益率（红涨绿跌）；归一化模式下主显收益率%；可按收益率降序 */
function lineTooltipFormatter(params: unknown): string {
  const ps = params as { seriesName: string; marker: string; dataIndex: number; axisValue: string; value: unknown }[]
  if (!ps.length) return ''
  let html = `<b>${ps[0].axisValue}</b>`
  if (pctMode.value && baseDate.value) {
    html += `　<span style="color:#98A2B3;">基准 ${baseDate.value} = 0%</span>`
  }
  interface Row { marker: string; name: string; rawStr: string; rankVal: number; dispHtml: string }
  const rows: Row[] = []
  for (const p of ps) {
    const rawArr = lineRawMap[p.seriesName]
    const raw = rawArr ? rawArr[p.dataIndex] : null
    const rawStr = raw != null ? fmtVal(raw) : '--'
    const base = rawArr ? firstFinite(rawArr) : NaN
    const pct = Number.isFinite(base) && base !== 0 && raw != null ? (raw / base - 1) * 100 : NaN
    if (pctMode.value && Number.isFinite(Number(p.value))) {
      const nv = Number(p.value)
      const cl = upDownColor(nv)
      rows.push({
        marker: p.marker, name: p.seriesName, rawStr, rankVal: nv,
        dispHtml: `<b style="color:${cl}">${signed(nv)}%</b>　<span style="color:#98A2B3;">(${rawStr})</span>`,
      })
    } else {
      const cl = upDownColor(pct)
      const ret = Number.isFinite(pct) ? `　<b style="color:${cl}">${signed(pct)}%</b>` : ''
      rows.push({
        marker: p.marker, name: p.seriesName, rawStr,
        rankVal: Number.isFinite(pct) ? pct : -Infinity,
        dispHtml: `<b>${rawStr}</b>${ret}`,
      })
    }
  }
  if (sortDesc.value) rows.sort((a, b) => b.rankVal - a.rankVal)
  for (const r of rows) html += `<br/>${r.marker} ${r.name} ${r.dispHtml}`
  return html
}

function buildKline() {
  const dates = klineRows.value.map((r) => toX(r[map.x]))
  // echarts candlestick 数据顺序：[open, close, low, high]
  const kdata = klineData.value
  const useVol = !!map.vol
  const series: Record<string, unknown>[] = [
    {
      name: 'K线', type: 'candlestick', data: kdata,
      itemStyle: { color: UP_COLOR, color0: DOWN_COLOR, borderColor: UP_COLOR, borderColor0: DOWN_COLOR },
    },
  ]
  // MA 均线叠加（主图）
  const closes = kdata.map((d) => d[1])
  for (const d of MA_DEFS) {
    if (!mas.value.includes(d.key)) continue
    series.push({
      name: `MA${d.n}`, type: 'line', data: maValues(closes, d.n),
      showSymbol: false, symbolSize: 0, silent: true, z: 3,
      itemStyle: { color: d.color }, lineStyle: { width: 1, color: d.color },
    })
  }
  if (useVol) {
    const vols = kdata.map((d, i) => ({
      value: toNum(klineRows.value[i][map.vol]),
      itemStyle: { color: d[1] >= d[0] ? UP_COLOR : DOWN_COLOR, opacity: 0.75 },
    }))
    series.push({ name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vols })
  }
  const n = dates.length
  const tail = Math.min(120, n)
  const start = n > tail ? ((n - tail) / n) * 100 : 0
  // 高度用百分比，随容器（clamp 52vh）自适应
  const grids = useVol
    ? [
        { left: 60, right: 20, top: 36, height: '54%' },
        { left: 60, right: 20, top: '72%', height: '15%' },
      ]
    : [{ left: 60, right: 20, top: 36, bottom: '13%' }]
  // 图例只放 MA 与成交量（蜡烛红绿双色，单色图例语义错误故不放）
  const legendData = MA_DEFS.filter((d) => mas.value.includes(d.key)).map((d) => `MA${d.n}`)
  if (useVol) legendData.push('成交量')
  const dataZoom = [
    { type: 'inside', xAxisIndex: useVol ? [0, 1] : 0, start, end: 100 },
    { type: 'slider', xAxisIndex: useVol ? [0, 1] : 0, start, end: 100, height: 22, bottom: 8 },
  ]
  const yAxis = useVol
    ? [{ scale: true }, { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { formatter: (v: number) => compactNum(v) } }]
    : [{ scale: true }]
  const xAxisStyle = { axisLine: { lineStyle: { color: '#d0d5dd' } }, axisLabel: { color: '#667085' } }
  const xAxis = useVol
    ? [
        { type: 'category', data: dates, boundaryGap: true, ...xAxisStyle },
        { type: 'category', gridIndex: 1, data: dates, axisLabel: { show: false }, axisTick: { show: false } },
      ]
    : [{ type: 'category', data: dates, boundaryGap: true, ...xAxisStyle }]
  return {
    backgroundColor: '#fff',
    animation: false,
    legend: legendData.length ? { data: legendData, top: 6, textStyle: { color: '#475467' } } : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: klineTooltipFormatter,
      backgroundColor: '#fff', borderColor: '#e4e7ec', textStyle: { color: '#101828', fontSize: 12 },
    },
    grid: grids, xAxis, yAxis, dataZoom, series,
  }
}

function buildCategorySeriesSeries(kind: 'line' | 'bar') {
  const dates = [...new Set(sortedRows.value.map((r) => toX(r[map.x])))]
  const series: Record<string, unknown>[] = []
  const rawMap: Record<string, (number | null)[]> = {}
  const colorOf = (i: number) => SERIES_PALETTE[i % SERIES_PALETTE.length]

  if (groupable.value) {
    const ys = map.ys.length ? map.ys : ['']
    let ci = 0
    for (const g of groupValues.value) {
      for (const y of ys) {
        const raw = dates.map((d) => {
          const row = groupIndex.value.get(`${g}|${d}`)
          return row ? toNum(row[y]) : NaN
        })
        const name = ys.length > 1 ? `${g}·${y}` : String(g)
        rawMap[name] = raw
        series.push({
          name,
          type: kind, data: raw, connectNulls: true,
          itemStyle: { color: colorOf(ci) }, lineStyle: { width: kind === 'line' ? 1.6 : 1, color: colorOf(ci) },
          symbolSize: 3, barMaxWidth: 22,
        })
        ci++
      }
    }
  } else {
    for (const y of map.ys) {
      const raw = sortedRows.value.map((r) => toNum(r[y]))
      rawMap[y] = raw
      series.push({
        name: y, type: kind,
        data: raw,
        connectNulls: true, symbolSize: 3, barMaxWidth: 26,
        lineStyle: { width: 1.8 },
      })
    }
  }
  return { dates, series, rawMap }
}

function buildLineBar(kind: 'line' | 'bar') {
  const { dates, series, rawMap } = buildCategorySeriesSeries(kind)
  lineRawMap = rawMap

  // 收益率归一化（仅折线）：每组以首个有效点为基准 → (v/base - 1) * 100
  const pctOn = kind === 'line' && pctMode.value
  if (pctOn) {
    let firstIdx = -1
    for (const s of series) {
      const raw = rawMap[s.name as string]
      let base = NaN
      for (let i = 0; i < raw.length; i++) {
        const v = raw[i]
        if (v != null && Number.isFinite(v)) { base = v; if (firstIdx < 0 || i < firstIdx) firstIdx = i; break }
      }
      s.data = raw.map((v) => (v != null && Number.isFinite(v) && base !== 0 ? (v / base - 1) * 100 : null))
      // x 升序下首个有效点即全局最早基准
    }
    baseDate.value = firstIdx >= 0 ? dates[firstIdx] : ''
    // y=0 基准虚线挂在首条线上
    if (series.length) {
      series[0].markLine = {
        silent: true, symbol: 'none',
        lineStyle: { color: '#98A2B3', type: 'dashed', width: 1 },
        label: { show: false },
        data: [{ yAxis: 0 }],
      }
    }
  }

  const yAxisFormatter: (v: number) => string = pctOn ? (v) => `${v}%` : (v) => compactNum(v)
  return {
    backgroundColor: '#fff',
    animation: false,
    color: SERIES_PALETTE,
    legend: { top: 4, type: 'scroll', textStyle: { color: '#475467' } },
    tooltip: {
      trigger: 'axis',
      ...(kind === 'line' ? { formatter: lineTooltipFormatter } : {}),
      backgroundColor: '#fff', borderColor: '#e4e7ec', textStyle: { color: '#101828', fontSize: 12 },
    },
    grid: GRID_SINGLE,
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#d0d5dd' } }, axisLabel: { color: '#667085' } },
    yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { formatter: yAxisFormatter } },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', start: 0, end: 100, height: 22, bottom: 8 },
    ],
    series,
  }
}

function buildScatter() {
  const series: Record<string, unknown>[] = []
  if (groupable.value) {
    let ci = 0
    for (const g of groupValues.value) {
      const data: [number, number][] = []
      for (const r of sortedRows.value) {
        if (String(r[map.group]) !== g) continue
        const xv = toNum(r[map.scatterX]); const yv = toNum(r[map.scatterY])
        if (Number.isFinite(xv) && Number.isFinite(yv)) data.push([xv, yv])
      }
      series.push({ name: g, type: 'scatter', data, symbolSize: 5, itemStyle: { color: SERIES_PALETTE[ci % SERIES_PALETTE.length], opacity: 0.75 } })
      ci++
    }
  } else {
    const data: [number, number][] = []
    for (const r of sortedRows.value) {
      const xv = toNum(r[map.scatterX]); const yv = toNum(r[map.scatterY])
      if (Number.isFinite(xv) && Number.isFinite(yv)) data.push([xv, yv])
    }
    series.push({ name: `${map.scatterY} ~ ${map.scatterX}`, type: 'scatter', data, symbolSize: 5, itemStyle: { color: BRAND_COLOR, opacity: 0.7 } })
  }
  return {
    backgroundColor: '#fff',
    animation: false,
    legend: { top: 4, type: 'scroll', textStyle: { color: '#475467' } },
    tooltip: {
      trigger: 'item',
      backgroundColor: '#fff', borderColor: '#e4e7ec', textStyle: { color: '#101828', fontSize: 12 },
    },
    grid: GRID_SINGLE,
    xAxis: { type: 'value', scale: true, name: map.scatterX, nameTextStyle: { color: '#667085' }, splitLine: { lineStyle: { color: '#f0f2f5' } } },
    yAxis: { type: 'value', scale: true, name: map.scatterY, nameTextStyle: { color: '#667085' }, splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { formatter: (v: number) => compactNum(v) } },
    series,
  }
}

function compactNum(v: number): string {
  const a = Math.abs(v)
  if (a >= 1e8) return `${(v / 1e8).toFixed(1)}亿`
  if (a >= 1e4) return `${(v / 1e4).toFixed(1)}万`
  return String(v)
}

const option = computed<Record<string, unknown> | null>(() => {
  if (props.rows.length === 0) return null
  switch (kind.value) {
    case 'kline': return buildKline()
    case 'line': return buildLineBar('line')
    case 'bar': return buildLineBar('bar')
    case 'scatter': return buildScatter()
    default: return null
  }
})

// ---------------- UI 小件 ----------------

const selW = 'width:128px;'
const KIND_LABEL: Record<string, string> = { kline: 'K 线', line: '折线', bar: '柱状', scatter: '散点' }
const recommendText = computed(() => {
  if (!resolvedKind.value) return ''
  return manualKind.value && manualKind.value !== resolvedKind.value
    ? `自动推荐：${KIND_LABEL[resolvedKind.value]}`
    : ''
})
</script>

<template>
  <div class="explore-chart">
    <!-- 截断提示 -->
    <NAlert
      v-if="props.truncated"
      type="warning" :show-icon="true" size="small" style="margin-bottom:8px;"
      title="结果可能不完整"
    >
      命中 LIMIT {{ props.limit }} 行上限，图表仅基于已返回数据；需要更长历史请调大 LIMIT 重新执行。
    </NAlert>

    <!-- 映射工具条 -->
    <div class="mapbar">
      <span class="lbl">图表</span>
      <NRadioGroup
        :value="kind ?? 'none'" size="small" name="chart-kind"
        @update:value="(v: string) => (manualKind = (v === 'none' ? undefined : v as ChartKind))"
      >
        <NRadioButton
          v-for="o in kindOptions" :key="String(o.value)" :value="String(o.value)"
          :label="String(o.label)"
        />
      </NRadioGroup>
      <NTag v-if="recommendText" size="tiny" :bordered="false" type="info" style="flex:none;">{{ recommendText }}</NTag>

      <span class="sep" />
      <template v-if="kind === 'kline'">
        <span class="lbl">MA</span>
        <NCheckboxGroup v-model:value="mas" size="small">
          <NCheckbox v-for="d in MA_DEFS" :key="d.key" :value="d.key" :label="`MA${d.n}`" style="margin-right:6px;" />
        </NCheckboxGroup>
        <span class="sep" />
        <span class="lbl">X</span><NSelect v-model:value="map.x" size="small" filterable :options="colOptions" :style="selW" />
        <span class="lbl">开</span><NSelect v-model:value="map.open" size="small" filterable :options="numOptions" :style="selW" />
        <span class="lbl">高</span><NSelect v-model:value="map.high" size="small" filterable :options="numOptions" :style="selW" />
        <span class="lbl">低</span><NSelect v-model:value="map.low" size="small" filterable :options="numOptions" :style="selW" />
        <span class="lbl">收</span><NSelect v-model:value="map.close" size="small" filterable :options="numOptions" :style="selW" />
        <span class="lbl">量</span><NSelect v-model:value="map.vol" size="small" filterable clearable :options="numOptions" :style="selW" />
        <template v-if="groupable">
          <span class="lbl">标的</span>
          <NSelect
            v-model:value="klineGroup" size="small" filterable :options="groupValueOptions"
            :style="'width:150px;'" title="分组列有多个值时，K 线仅绘制选中标的，避免多标的交错"
          />
        </template>
        <span class="lbl">组</span><NSelect v-model:value="map.group" size="small" filterable clearable :options="groupOptions" :style="selW" title="分组后可在多个标的间切换" />
      </template>
      <template v-else-if="kind === 'line' || kind === 'bar'">
        <span class="lbl">X</span><NSelect v-model:value="map.x" size="small" filterable :options="colOptions" :style="selW" />
        <span class="lbl">Y</span><NSelect v-model:value="map.ys" size="small" filterable multiple :options="yOptions" :style="'width:220px;'" placeholder="数值列（可多选）" />
        <span class="lbl">组</span><NSelect v-model:value="map.group" size="small" filterable clearable :options="groupOptions" :style="selW" title="按该列拆分多条线" />
        <template v-if="kind === 'line'">
          <NCheckbox
            v-model:checked="pctMode" size="small" style="font-size:12px;margin-left:4px;"
            title="以每组首个有效点为基准归一化为累计收益率(%)；tooltip 同时显示原值"
          >收益率%</NCheckbox>
          <NCheckbox
            v-model:checked="sortDesc" size="small" style="font-size:12px;"
            title="tooltip 各组按累计收益率从高到低排列（取消勾选则按查询原始顺序）"
          >降序</NCheckbox>
          <NTag v-if="pctMode && baseDate" size="tiny" :bordered="false" type="info" style="flex:none;">
            基准 {{ baseDate }} = 0%
          </NTag>
        </template>
      </template>
      <template v-else-if="kind === 'scatter'">
        <span class="lbl">X</span><NSelect v-model:value="map.scatterX" size="small" filterable :options="numOptions" :style="selW" />
        <span class="lbl">Y</span><NSelect v-model:value="map.scatterY" size="small" filterable :options="numOptions" :style="selW" />
        <span class="lbl">组</span><NSelect v-model:value="map.group" size="small" filterable clearable :options="groupOptions" :style="selW" />
      </template>
    </div>

    <!-- 图表本体 -->
    <div class="chart-box">
      <VChart v-if="option" :option="option" autoresize class="chart" />
      <div v-else class="empty">无法映射出图表：需要 1 个非数值 X 列 + 数值列（K 线需开/高/低/收齐全）</div>
    </div>
  </div>
</template>

<style scoped>
.explore-chart { display: flex; flex-direction: column; }
.mapbar {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 6px 8px; margin-bottom: 8px;
  background: #F8FAFC; border: 1px solid #E6EDF5; border-radius: 6px;
}
.mapbar .lbl { font-size: 12px; color: #475467; flex: none; }
.mapbar .sep { width: 1px; height: 16px; background: #d0d5dd; margin: 0 2px; flex: none; }
.chart-box {
  height: clamp(380px, 52vh, 640px); border: 1px solid #ececec; border-radius: 6px;
  overflow: hidden; background: #fff;
}
.chart { width: 100%; height: 100%; }
.empty {
  height: 100%; display: flex; align-items: center; justify-content: center;
  color: #888; font-size: 13px; padding: 0 40px; text-align: center; line-height: 1.7;
}
</style>
