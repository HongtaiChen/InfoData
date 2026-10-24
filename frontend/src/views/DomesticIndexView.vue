<script setup lang="ts">
/**
 * DomesticIndexView —— 投资环境·国内指数（单页，v1.7）
 * 页面结构（自上而下）：
 *   ① 指数趋势与价格结构（2026-10-23 合并章节，内含两个小节：多指数对比与总览 / 大 K 线主图深钻）
 *   ② 技术面统计 + 基本面分析
 * 指数在每处内容自由多选/单选；不做 AI 结论（E3），分位中性展示。
 */
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { NCard, NCollapse, NCollapseItem, NSpin, NEmpty, NDatePicker, NDataTable, NSelect } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { useRoute } from 'vue-router'
import dayjs from 'dayjs'
import api from '../api'
import { SERIES_PALETTE } from '../echarts'
import type { IndexItem } from '../components/analysis/IndexSwitcher.vue'
import MultiIndexSelect from '../components/analysis/MultiIndexSelect.vue'
import IndicatorBlock from '../components/analysis/IndicatorBlock.vue'
import HistoryCurve from '../components/analysis/HistoryCurve.vue'
import EnvKLineChart from '../components/analysis/EnvKLineChart.vue'
import UpDownRatioTrend from '../components/analysis/UpDownRatioTrend.vue'
import CorrelationMatrix from '../components/analysis/CorrelationMatrix.vue'

const route = useRoute()

// ---------- 指数清单 ----------
const indices = ref<IndexItem[]>([])
const currentCode = ref('000001')
const currentName = ref('上证指数')

const tech = ref<any>(null)
const fund = ref<any>(null)
const macro = ref<any>(null)
const policy = ref<any>(null)
const corr = ref<any>(null)
const scope = ref<any>(null)
const sentiment = ref<any>(null)
const leverage = ref<any>(null)
const techLoading = ref(false)
const fundLoading = ref(false)
const sentimentLoading = ref(false)
const leverageLoading = ref(false)

// ---------- 风险章节窗口（1年/3年/全史，默认 3年；前端裁剪，不重拉） ----------
const RISK_WINDOWS = [
  { key: '1y', label: '1年', days: 250 },
  { key: '3y', label: '3年', days: 750 },
  { key: 'all', label: '全史', days: 10000 },
] as const
const riskWin = ref<'1y' | '3y' | 'all'>('3y')

// ---------- 对比图状态 ----------
const compareCodes = ref<string[]>(['000001', '000300', '399006', '000905'])
const compareWindow = ref<'3m' | '6m' | '1y' | '3y'>('1y')
const compareRange = ref<[number, number] | null>(null)
const compareData = ref<any>(null)
const compareLoading = ref(false)
// 对比模式：normal = 归一化净值；rs = 相对强弱（各指数 ÷ 基准指数，基准恒 100）
type CompareMode = 'normal' | 'rs'
const compareMode = ref<CompareMode>('normal')
// 相对强弱基准指数（默认沪深300）
const rsBase = ref<string>('000300')

// ---------- 总览表状态 ----------
interface OverviewRow {
  index_code: string; index_name: string; trade_date: string | null
  close: number | null; change_pct: number | null
  drop_threshold: number | null; threshold_source: string | null
  win_halfm: number | null; win_1m: number | null; win_15m: number | null
  win_2m: number | null; win_25m: number | null; win_3m: number | null
  win_6m: number | null; win_1y: number | null
  win_ytd: number | null; win_qtd: number | null; win_mtd: number | null; win_wtd: number | null
  win_3y: number | null; win_all: number | null
  // 「区间回撤」列：五档 K 线在所选区间内的最大回撤（+峰/谷日期）
  range_mdd_d: number | null; range_mdd_w: number | null; range_mdd_m: number | null
  range_mdd_q: number | null; range_mdd_y: number | null
  range_mdd_d_pk?: string | null; range_mdd_d_tr?: string | null
  range_mdd_w_pk?: string | null; range_mdd_w_tr?: string | null
  range_mdd_m_pk?: string | null; range_mdd_m_tr?: string | null
  range_mdd_q_pk?: string | null; range_mdd_q_tr?: string | null
  range_mdd_y_pk?: string | null; range_mdd_y_tr?: string | null
  // 12 窗口分位 + 区间最大回撤（后端现算，副行展示）
  win_halfm_pctile: number | null; win_1m_pctile: number | null; win_15m_pctile: number | null
  win_2m_pctile: number | null; win_25m_pctile: number | null; win_3m_pctile: number | null
  win_6m_pctile: number | null; win_1y_pctile: number | null
  win_ytd_pctile: number | null; win_qtd_pctile: number | null; win_mtd_pctile: number | null; win_wtd_pctile: number | null
  // 12 窗口五档最大回撤（日K/周K/月K/季K/年K，后端现算）
  win_halfm_mdd_d: number | null; win_halfm_mdd_w: number | null; win_halfm_mdd_m: number | null; win_halfm_mdd_q: number | null; win_halfm_mdd_y: number | null
  win_1m_mdd_d: number | null; win_1m_mdd_w: number | null; win_1m_mdd_m: number | null; win_1m_mdd_q: number | null; win_1m_mdd_y: number | null
  win_15m_mdd_d: number | null; win_15m_mdd_w: number | null; win_15m_mdd_m: number | null; win_15m_mdd_q: number | null; win_15m_mdd_y: number | null
  win_2m_mdd_d: number | null; win_2m_mdd_w: number | null; win_2m_mdd_m: number | null; win_2m_mdd_q: number | null; win_2m_mdd_y: number | null
  win_25m_mdd_d: number | null; win_25m_mdd_w: number | null; win_25m_mdd_m: number | null; win_25m_mdd_q: number | null; win_25m_mdd_y: number | null
  win_3m_mdd_d: number | null; win_3m_mdd_w: number | null; win_3m_mdd_m: number | null; win_3m_mdd_q: number | null; win_3m_mdd_y: number | null
  win_6m_mdd_d: number | null; win_6m_mdd_w: number | null; win_6m_mdd_m: number | null; win_6m_mdd_q: number | null; win_6m_mdd_y: number | null
  win_1y_mdd_d: number | null; win_1y_mdd_w: number | null; win_1y_mdd_m: number | null; win_1y_mdd_q: number | null; win_1y_mdd_y: number | null
  win_ytd_mdd_d: number | null; win_ytd_mdd_w: number | null; win_ytd_mdd_m: number | null; win_ytd_mdd_q: number | null; win_ytd_mdd_y: number | null
  win_qtd_mdd_d: number | null; win_qtd_mdd_w: number | null; win_qtd_mdd_m: number | null; win_qtd_mdd_q: number | null; win_qtd_mdd_y: number | null
  win_mtd_mdd_d: number | null; win_mtd_mdd_w: number | null; win_mtd_mdd_m: number | null; win_mtd_mdd_q: number | null; win_mtd_mdd_y: number | null
  win_wtd_mdd_d: number | null; win_wtd_mdd_w: number | null; win_wtd_mdd_m: number | null; win_wtd_mdd_q: number | null; win_wtd_mdd_y: number | null
  // 所选区间三合一（区间选择器启用时）
  range_stats?: { pct: number | null; pctile: number | null; mdd: number | null } | null
  // 「当日」列副行：涨跌幅分位（样本池内）+ 当日盘中最大回撤（low/high−1）
  change_pctile: number | null; day_mdd: number | null
  ann_vol_60: number | null; max_dd_1y: number | null
  pe_ttm: number | null; pe_ttm_pct: number | null
  pb: number | null; pb_pct: number | null
  dividend_yield: number | null; dividend_yield_pct: number | null
}
const overview = ref<OverviewRow[]>([])
const overviewLoading = ref(false)

// ---------- 总览表区间选择器（独立于对比图窗口） ----------
const ovRangeWin = ref<'3m' | '6m' | '1y' | '3y' | 'all' | ''>('3y')  // 默认「近3年」，'' = 关闭（回落近3年全局口径）
const ovRangeCustom = ref<[number, number] | null>(null)
const ovRangeLabel = computed(() => {
  if (ovRangeCustom.value) {
    return `${dayjs(ovRangeCustom.value[0]).format('YYYY-MM-DD')}~${dayjs(ovRangeCustom.value[1]).format('YYYY-MM-DD')}`
  }
  const m: Record<string, string> = { '3m': '近3月', '6m': '近6月', '1y': '近1年', '3y': '近3年', 'all': '全史' }
  return m[ovRangeWin.value] || ''
})

// 趋势图「涨日占比」分位样本池的区间边界（自然日，与后端 sample_start/sample_end 同口径）
// window→自然日数 与后端 env_service._COMPARE_WINDOW_DAYS 一致（3m=95/6m=190/1y=380/3y=1160）
const UPDOWN_WIN_DAYS: Record<string, number> = { '3m': 95, '6m': 190, '1y': 380, '3y': 1160 }
// K 线图内部的自定义区间（EnvKLineChart @range-change 外抛）：趋势图分位池优先跟随它，清空回落总览表区间
const klCustomRange = ref<[number, number] | null>(null)
const updownRange = computed<{ start: string; end: string; label: string }>(() => {
  if (klCustomRange.value) {
    const s = dayjs(klCustomRange.value[0]).format('YYYY-MM-DD')
    const e = dayjs(klCustomRange.value[1]).format('YYYY-MM-DD')
    return { start: s, end: e, label: `${s}~${e}（K线自定义）` }
  }
  if (ovRangeCustom.value) {
    const s = dayjs(ovRangeCustom.value[0]).format('YYYY-MM-DD')
    const e = dayjs(ovRangeCustom.value[1]).format('YYYY-MM-DD')
    return { start: s, end: e, label: `${s}~${e}` }
  }
  const w = ovRangeWin.value
  if (w && w !== 'all') {
    const end = dayjs()
    const start = end.subtract(UPDOWN_WIN_DAYS[w] ?? 1160, 'day')
    return { start: start.format('YYYY-MM-DD'), end: end.format('YYYY-MM-DD'), label: ovRangeLabel.value }
  }
  // 'all' 或 ''：全史（不限制区间）
  return { start: '', end: '', label: ovRangeLabel.value || '全史' }
})

function fmtPct(v: any, digits = 2): string {
  if (v == null) return '--'
  return `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}%`
}
function fmtNum(v: any, digits = 2): string {
  if (v == null) return '--'
  return Number(v).toFixed(digits)
}
function fmtYi(v: any): string {
  // 元 → 亿 / 万亿 可读格式（融资余额/流通市值/净流入均为元量级）
  if (v == null || isNaN(Number(v))) return '--'
  const n = Number(v)
  const abs = Math.abs(n)
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)} 万亿`
  if (abs >= 1e8) return `${(n / 1e8).toFixed(1)} 亿`
  if (abs >= 1e4) return `${(n / 1e4).toFixed(1)} 万`
  return `${n.toFixed(0)} 元`
}
function pctClass(v: number | null): string {
  if (v == null) return ''
  return v > 0 ? 'c-up' : v < 0 ? 'c-down' : ''
}

// ---------- 「级别」列：五档分色（狂涨/大涨/小涨/平/小跌/大跌/狂跌） ----------
// 判定基准 = 当日涨跌幅 x，阈值 T = env_threshold.drop_threshold（每指数一个，取整到 0.5）
// 平盘阈值 0.1%（指数点位精度内视为没涨没跌）；狂 = |x| ≥ 2T；大 = T ≤ |x| < 2T；小 = 0.1% < |x| < T
const FLAT_EPS = 0.1
function moveLevel(x: number | null, T: number | null): { label: string; cls: string } {
  if (x == null) return { label: '--', cls: '' }
  const thr = T ?? 1.5 // 阈值缺失兜底 1.5%
  const ax = Math.abs(x)
  if (ax <= FLAT_EPS) return { label: '平', cls: 'lv-flat' }
  const extreme = ax >= thr * 2
  const big = ax >= thr
  if (x > 0) return extreme ? { label: '狂涨', cls: 'lv-surge' } : big ? { label: '大涨', cls: 'lv-up' } : { label: '小涨', cls: 'lv-up-light' }
  return extreme ? { label: '狂跌', cls: 'lv-plunge' } : big ? { label: '大跌', cls: 'lv-down' } : { label: '小跌', cls: 'lv-down-light' }
}
// 分位副行配色：超跌分位（<10%）绿、超涨分位（>90%）红、正常灰
function pctileCls(p: number | null | undefined): string {
  if (p == null) return 'ov-sub-pct'
  if (p < 10) return 'ov-sub-pct ov-sub-pct-low'
  if (p > 90) return 'ov-sub-pct ov-sub-pct-high'
  return 'ov-sub-pct'
}
function isExtreme(pct: number | null | undefined): boolean {
  if (pct == null) return false
  return pct < 10 || pct > 90
}

// ---------- 数据加载 ----------
async function loadIndices() {
  try {
    const resp: any = await api.get('/environment/domestic/indices')
    indices.value = resp.items || []
    const q = String(route.query.code || '')
    if (q && indices.value.some((i) => i.index_code === q)) {
      setCurrent(q)
    } else {
      // onMounted 时 setCurrent 先于清单加载，名字可能被兜底成代码；清单到位后回填
      const it = indices.value.find((i) => i.index_code === currentCode.value)
      if (it?.index_name) currentName.value = it.index_name
    }
  } catch (e) {
    console.error('[env indices]', e)
  }
}

async function loadOverview() {
  overviewLoading.value = true
  try {
    const params: any = {}
    if (ovRangeCustom.value) {
      params.start = dayjs(ovRangeCustom.value[0]).format('YYYYMMDD')
      params.end = dayjs(ovRangeCustom.value[1]).format('YYYYMMDD')
    } else if (ovRangeWin.value) {
      params.window = ovRangeWin.value
    }
    const resp: any = await api.get('/environment/domestic/overview', { params })
    overview.value = resp.items || []
  } catch (e) {
    console.error('[env overview]', e)
  } finally {
    overviewLoading.value = false
  }
}

function onOvRangeWin(w: '3m' | '6m' | '1y' | '3y' | 'all' | '') {
  ovRangeWin.value = w
  ovRangeCustom.value = null
  loadOverview()
}
function onOvRangeCustom(v: [number, number] | null) {
  ovRangeCustom.value = v
  if (v) { ovRangeWin.value = ''; loadOverview() }
}

async function loadCompare() {
  if (!compareCodes.value.length) { compareData.value = null; return }
  compareLoading.value = true
  try {
    const params: any = { codes: compareCodes.value.join(',') }
    if (compareRange.value) {
      params.start = dayjs(compareRange.value[0]).format('YYYYMMDD')
      params.end = dayjs(compareRange.value[1]).format('YYYYMMDD')
    } else {
      params.window = compareWindow.value
    }
    compareData.value = await api.get('/environment/domestic/compare', { params }) as any
  } catch (e) {
    console.error('[env compare]', e)
  } finally {
    compareLoading.value = false
  }
}

function onCompareChange(codes: string[]) {
  compareCodes.value = codes
  loadCompare()
}

function onCompareWindow(w: '3m' | '6m' | '1y' | '3y') {
  compareWindow.value = w
  compareRange.value = null
  loadCompare()
}

function onCompareRange(v: [number, number] | null) {
  compareRange.value = v
  if (v) loadCompare()
}

function onCompareChip(code: string) {
  // 点总览表指数名：联动 K 线主图（对比图选择不变）
  setCurrent(code)
}

function setCurrent(code: string) {
  currentCode.value = code
  const it = indices.value.find((i) => i.index_code === code)
  currentName.value = it?.index_name || code
  loadTech()
  loadFund()
}

async function loadTech() {
  techLoading.value = true
  try {
    tech.value = await api.get(`/environment/domestic/indices/${currentCode.value}/tech`, { params: { limit: 2000 } })
  } catch (e) {
    console.error('[env tech]', e)
    tech.value = { empty: true }
  } finally {
    techLoading.value = false
  }
}

async function loadFund() {
  fundLoading.value = true
  try {
    fund.value = await api.get(`/environment/domestic/indices/${currentCode.value}/fundamental`)
  } catch (e) {
    console.error('[env fund]', e)
    fund.value = { empty: true }
  } finally {
    fundLoading.value = false
  }
}

async function loadStatic() {
  try {
    const [m, c, s, p] = await Promise.all([
      api.get('/environment/macro'),
      api.get('/environment/domestic/correlation'),
      api.get('/environment/scope-note'),
      api.get('/environment/domestic/policy'),
    ])
    macro.value = m
    corr.value = c
    scope.value = s
    policy.value = p
  } catch (e) {
    console.error('[env static]', e)
  }
}

async function loadSentiment() {
  sentimentLoading.value = true
  try {
    sentiment.value = await api.get('/analysis/market-sentiment')
  } catch (e) {
    console.error('[env sentiment]', e)
    sentiment.value = { empty: true }
  } finally {
    sentimentLoading.value = false
  }
}

async function loadLeverage() {
  leverageLoading.value = true
  try {
    leverage.value = await api.get('/analysis/leverage-funding')
  } catch (e) {
    console.error('[env leverage]', e)
    leverage.value = { empty: true }
  } finally {
    leverageLoading.value = false
  }
}

// ---------- 对比图派生 ----------
// 曲线画归一化净值（起点=100，不同点位量级可比强弱）；悬停展示真实收盘 + 区间涨跌幅（红绿）
// 相对强弱模式：RS = 该指数净值 ÷ 基准指数净值 × 100（均以区间起点=100 归一），
// 窗口起点恒 100：上穿 100 = 跑赢基准，下穿 = 跑输（直接用原始价格比会引入历史量级偏移——实测踩坑）
const compareSeries = computed(() => {
  const d = compareData.value
  if (!d?.series) return []
  if (compareMode.value === 'rs') {
    const base = d.series.find((s: any) => s.index_code === rsBase.value)
    if (!base?.values) return []
    const baseVals: (number | null)[] = base.values
    const out: any[] = []
    for (const s of d.series) {
      if (!s.values) continue
      if (s.index_code === rsBase.value) {
        out.push({
          name: s.index_name, values: baseVals.map(() => 100),
          color: SERIES_PALETTE[out.length % SERIES_PALETTE.length],
        })
      } else {
        const vals = s.values.map((v: number | null, i: number) => {
          const b = baseVals[i]
          if (v == null || b == null || b === 0) return null
          return round2((v / b) * 100)
        })
        out.push({
          name: s.index_name, values: vals,
          color: SERIES_PALETTE[out.length % SERIES_PALETTE.length],
        })
      }
    }
    return out
  }
  return d.series.map((s: any, i: number) => ({
    name: s.index_name,
    values: s.values,
    color: SERIES_PALETTE[i % SERIES_PALETTE.length],
  }))
})

function round2(v: number): number {
  return Math.round(v * 100) / 100
}

// 区间统计（三合一指标）：涨跌幅 + 近3年分位 + 最大回撤（后端 domestic_compare 实时派生）
interface CompareStat {
  name: string; code: string; pct: number
  pctile: number | null; maxDrawdown: number | null; color: string
}
const compareStats = computed<CompareStat[]>(() => {
  const d = compareData.value
  if (!d?.series) return []
  const out: CompareStat[] = []
  d.series.forEach((s: any, i: number) => {
    // 后端返回 pct（区间涨跌幅）；旧数据兜底：前端相对首值计算
    let pct: number | null = s.pct != null ? Number(s.pct) : null
    if (pct == null) {
      const closes: (number | null)[] = s.closes
      if (closes?.length) {
        const first = closes.find((c: number | null) => c != null)
        const last = [...closes].reverse().find((c: number | null) => c != null)
        if (first != null && last != null && first !== 0) pct = ((last - first) / first) * 100
      }
    }
    if (pct == null) return
    out.push({
      name: s.index_name,
      code: s.index_code,
      pct: round2(pct),
      pctile: s.pctile == null ? null : Number(s.pctile),
      maxDrawdown: s.max_drawdown == null ? null : Number(s.max_drawdown),
      color: SERIES_PALETTE[i % SERIES_PALETTE.length],
    })
  })
  return out.sort((a, b) => b.pct - a.pct)
})

// 相对强弱基准指数下拉选项（默认沪深300 排前）
const rsBaseOptions = computed(() => {
  const list = (indices.value || []).map((i) => ({ label: i.index_name, value: i.index_code }))
  const b = list.find((x) => x.value === rsBase.value)
  if (b) return [b, ...list.filter((x) => x.value !== rsBase.value)]
  return list
})
const rsBaseName = computed(() => {
  return indices.value.find((i) => i.index_code === rsBase.value)?.index_name || rsBase.value
})
// 区间统计条形最大值（用于归一化条宽）
const maxAbsStat = computed(() => {
  const m = Math.max(...compareStats.value.map((s) => Math.abs(s.pct)), 0.01)
  return m || 0.01
})
// 区间统计标题的窗口描述（与曲线窗口一致）
const WIN_LABELS: Record<string, string> = { '3m': '近3月', '6m': '近6月', '1y': '近1年', '3y': '近3年' }
const statsRangeLabel = computed(() => {
  if (compareRange.value) {
    return `${dayjs(compareRange.value[0]).format('YYYY-MM-DD')} ~ ${dayjs(compareRange.value[1]).format('YYYY-MM-DD')}`
  }
  return WIN_LABELS[compareWindow.value] || compareWindow.value
})

// 相对强弱模式的悬停提示：显示 RS 值 + 真实收盘
const compareRsTooltipFormatter = (params: any) => {
  const d = compareData.value
  if (!d?.series || !params?.length) return ''
  const idx = params[0]?.dataIndex ?? 0
  let html = `<div style="font-weight:600;margin-bottom:4px">${d.dates?.[idx] ?? ''}</div>`
  for (const p of params) {
    const s = d.series[p.seriesIndex]
    if (!s?.closes) continue
    const close = s.closes[idx]
    const rs = p.value == null ? null : Number(p.value)
    const rsStr = rs == null ? '--' : rs.toFixed(2)
    const dot = p.color ?? '#999'
    html += `<div style="display:flex;justify-content:space-between;gap:16px;line-height:1.7">`
      + `<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dot};margin-right:6px"></span>${p.seriesName}</span>`
      + `<span style="font-variant-numeric:tabular-nums">RS ${rsStr} · 收盘 ${close == null ? '--' : Number(close).toFixed(2)}</span></div>`
  }
  return html
}

const compareTooltipFormatter = (params: any) => {
  const d = compareData.value
  if (!d?.series || !params?.length) return ''
  const idx = params[0]?.dataIndex ?? 0
  let html = `<div style="font-weight:600;margin-bottom:4px">${d.dates?.[idx] ?? ''}</div>`
  for (const p of params) {
    const s = d.series[p.seriesIndex]
    if (!s?.closes) continue
    const close = s.closes[idx]
    // 区间涨跌幅 = 相对该指数在区间内首个有效收盘（与归一化基准同口径）
    let pct: number | null = null
    for (let i = 0; i < s.closes.length; i++) {
      if (s.closes[i] != null) {
        if (i <= idx && s.closes[idx] != null) pct = ((s.closes[idx] - s.closes[i]) / s.closes[i]) * 100
        break
      }
    }
    const pctStr = pct == null ? '--' : `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`
    const pctColor = pct == null ? '#909399' : pct > 0 ? '#EF232A' : pct < 0 ? '#14B143' : '#909399'
    const dot = p.color ?? '#999'
    html += `<div style="display:flex;justify-content:space-between;gap:16px;line-height:1.7">`
      + `<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dot};margin-right:6px"></span>${p.seriesName}</span>`
      + `<span style="font-variant-numeric:tabular-nums">${close == null ? '--' : Number(close).toFixed(2)} `
      + `<b style="color:${pctColor};margin-left:6px">${pctStr}</b></span></div>`
  }
  return html
}

// ---------- 总览表列 ----------
// 排序：默认按后端顺序（importance_rank）展示，点表头手工排序；null 值恒排最后
function numSorter(key: keyof OverviewRow) {
  return (a: OverviewRow, b: OverviewRow) => {
    const av = a[key] as number | null, bv = b[key] as number | null
    if (av == null && bv == null) return 0
    if (av == null) return -1
    if (bv == null) return 1
    return av - bv
  }
}
// 级别列排序键 = 档位序为主（狂跌-3 … 狂涨+3），同档内按 |涨跌|/T 相对强度细分
// 修复：此前只用 |涨跌|/T，导致小跌被夹进小涨堆（档位直觉错乱）
function levelRank(r: OverviewRow): { ord: number; ratio: number } {
  if (r.change_pct == null) return { ord: -99, ratio: 0 }
  const x = r.change_pct
  const thr = r.drop_threshold ?? 1.5
  const ratio = Math.abs(x) / thr
  let ord: number
  if (Math.abs(x) <= 0.1) ord = 0
  else if (ratio >= 2) ord = x > 0 ? 3 : -3
  else if (ratio >= 1) ord = x > 0 ? 2 : -2
  else ord = x > 0 ? 1 : -1
  return { ord, ratio }
}
// 多窗口涨跌幅列配置（label → OverviewRow 字段），全部红涨绿跌 + 可排序
const WIN_COLS: { label: string; key: keyof OverviewRow }[] = [
  { label: '本周', key: 'win_wtd' },
  { label: '本月', key: 'win_mtd' },
  { label: '本季', key: 'win_qtd' },
  { label: '本年', key: 'win_ytd' },
  { label: '半月', key: 'win_halfm' },
  { label: '近1月', key: 'win_1m' },
  { label: '近1.5月', key: 'win_15m' },
  { label: '近2月', key: 'win_2m' },
  { label: '近2.5月', key: 'win_25m' },
  { label: '近3月', key: 'win_3m' },
  { label: '近6月', key: 'win_6m' },
  { label: '近1年', key: 'win_1y' },
  { label: '近3年', key: 'win_3y' },
  { label: '全史', key: 'win_all' },
]
// 窗口单元格 title 文案（涨跌幅 + 分位 + 五档回撤完整口径）
function winCellTitle(label: string, v: number | null, p: number | null, dd: (number | null)[] | number | null, ddPk?: (string | null)[], ddTr?: (string | null)[]): string {
  const pool = ovRangeLabel.value || '近3年'
  let ddTxt = ''
  if (Array.isArray(dd)) {
    const lb = ['日K', '周K', '月K', '季K', '年K']
    ddTxt = lb.map((l, i) => {
      if (dd[i] == null) return `${l}--`
      const range = ddPk?.[i] && ddTr?.[i] ? `（${ddPk[i]}→${ddTr[i]}）` : ''
      return `${l}${fmtNum(dd[i], 2)}%${range}`
    }).join(' · ')
  } else if (dd != null) {
    ddTxt = `${fmtNum(dd, 2)}%`
  }
  const ddPart = ddTxt ? ` · 最大回撤 ${ddTxt}` : ''
  return `${label}涨跌幅 ${v == null ? '--' : fmtPct(v)} · 分位 ${p == null ? '--' : fmtNum(p, 1) + '%'}（样本=「${pool}」内同窗口；<10%绿=超跌、>90%红=超涨）${ddPart}`
}
const columns = computed<DataTableColumns<OverviewRow>>(() => [
  { title: '指数', key: 'index_name', width: 150, fixed: 'left',
    sorter: (a, b) => a.index_name.localeCompare(b.index_name, 'zh'),
    render: (r) => h('a', {
      class: 'ov-link' + (r.index_code === currentCode.value ? ' cur' : ''),
      onClick: () => onCompareChip(r.index_code),
    }, [
      h('span', { class: 'ov-name' }, r.index_name),
      h('span', { class: 'ov-code' }, r.index_code),
    ]) },
  { title: '数据日期', key: 'trade_date', width: 100, align: 'center',
    sorter: (a, b) => String(a.trade_date ?? '').localeCompare(String(b.trade_date ?? '')),
    render: (r) => h('span', { class: 'ov-num dim' }, r.trade_date ? String(r.trade_date).slice(0, 10) : '--') },
  { title: '收盘', key: 'close', width: 90, align: 'right', sorter: numSorter('close'),
    render: (r) => h('span', { class: 'ov-num' }, fmtNum(r.close)) },
  { title: '当日', key: 'change_pct', width: 96, align: 'right', sorter: numSorter('change_pct'),
    render: (r) => h('div', { class: 'ov-win-cell', title: winCellTitle('当日', r.change_pct, r.change_pctile, null) }, [
      h('span', { class: `ov-num ${pctClass(r.change_pct)}` }, fmtPct(r.change_pct)),
      h('span', { class: 'ov-win-sub' }, [
        h('span', { class: pctileCls(r.change_pctile) }, r.change_pctile == null ? '分位--' : `分位${fmtNum(r.change_pctile, 0)}%`),
      ]),
    ]) },
  { title: '级别', key: 'level', width: 72, align: 'center',
    sorter: (a, b) => {
      const A = levelRank(a), B = levelRank(b)
      return A.ord !== B.ord ? A.ord - B.ord : A.ratio - B.ratio
    },
    render: (r) => {
      const { label, cls } = moveLevel(r.change_pct, r.drop_threshold)
      const thrTxt = r.drop_threshold != null
        ? `T=${fmtNum(r.drop_threshold, 1)}%（${r.threshold_source === 'manual' ? '手定' : '95分位建议'}）`
        : '阈值缺失，兜底 T=1.5%'
      return h('span', {
        class: `ov-level ${cls}`,
        title: `当日 ${fmtPct(r.change_pct)} · ${thrTxt}`,
      }, label)
    } },
  // 「区间回撤」列：五档 K 线在「所选区间」内的最大回撤（跟随区间选择器，默认 3y）
  (() => {
    const RF = ['d', 'w', 'm', 'q', 'y'] as const
    const RL = ['日', '周', '月', '季', '年'] as const
    return {
      title: '区间回撤', key: 'range_mdd_d', width: 150, align: 'right' as const,
      sorter: (a: OverviewRow, b: OverviewRow) => (a.range_mdd_d ?? 0) - (b.range_mdd_d ?? 0),
      render: (r: OverviewRow) => {
        const dd = RF.map((f) => (r as any)[`range_mdd_${f}`] as number | null)
        const pks = RF.map((f) => (r as any)[`range_mdd_${f}_pk`] as string | null)
        const trs = RF.map((f) => (r as any)[`range_mdd_${f}_tr`] as string | null)
        const chips = dd.map((x, i) => x == null ? null : h('span', { class: 'ov-dd-chip' }, [
          h('i', { class: 'ov-dd-chip-lb' }, RL[i]),
          h('b', { class: 'ov-dd-chip-v' }, fmtNum(x, 1)),
        ])).filter(Boolean) as any[]
        const pool = ovRangeLabel.value || '近3年'
        const tip = RL.map((l, i) => dd[i] == null ? `${l}K--` : `${l}K${fmtNum(dd[i], 2)}%${pks[i] && trs[i] ? `（${pks[i]}→${trs[i]}）` : ''}`).join(' · ')
        return h('div', { class: 'ov-win-cell', title: `「${pool}」区间内五档最大回撤：${tip}` }, [
          h('span', { class: 'ov-sub-dd' }, `区间 ${pool}`),
          chips.length ? h('span', { class: 'ov-dd-chips' }, chips) : h('span', { class: 'ov-sub-dd' }, '撤--'),
        ])
      },
    }
  })(),
  // 多窗口涨跌幅（12 列，红涨绿跌 + 可排序）；单元格双行：涨跌幅主行 + 分位/回撤副行
  // 注：区间选择器仅控制各列分位的样本池，不再单独插「所选区间」列
  ...WIN_COLS.map(({ label, key }) => ({
    title: label, key, width: 132, align: 'right' as const, sorter: numSorter(key),
    render: (r: OverviewRow) => {
      const v = r[key] as number | null
      const pkey = (key + '_pctile') as keyof OverviewRow
      const p = r[pkey] as number | null
      const dd = ['d', 'w', 'm', 'q', 'y'].map((f) => (r as any)[`${key}_mdd_${f}`] as number | null)
      const ddPk = ['d', 'w', 'm', 'q', 'y'].map((f) => (r as any)[`${key}_mdd_${f}_pk`] as string | null)
      const ddTr = ['d', 'w', 'm', 'q', 'y'].map((f) => (r as any)[`${key}_mdd_${f}_tr`] as string | null)
      const ddLabels = ['日', '周', '月', '季', '年']
      const chips = dd.map((x, i) => x == null ? null : h('span', { class: 'ov-dd-chip' }, [
        h('i', { class: 'ov-dd-chip-lb' }, ddLabels[i]),
        h('b', { class: 'ov-dd-chip-v' }, fmtNum(x, 1)),
      ])).filter(Boolean) as any[]
      return h('div', { class: 'ov-win-cell', title: winCellTitle(label, v, p, dd, ddPk, ddTr) }, [
        h('span', { class: `ov-num ${pctClass(v)}` }, fmtPct(v)),
        h('span', { class: 'ov-win-sub' }, [
          h('span', { class: pctileCls(p) }, p == null ? '分位--' : `分位${fmtNum(p, 0)}%`),
          chips.length
            ? h('span', { class: 'ov-dd-chips' }, chips)
            : h('span', { class: 'ov-sub-dd' }, '撤--'),
        ]),
      ])
    },
  })),
  { title: 'PE', key: 'pe_ttm', width: 70, align: 'right', sorter: numSorter('pe_ttm'),
    render: (r) => h('span', { class: 'ov-num' }, fmtNum(r.pe_ttm)) },
  { title: 'PE分位', key: 'pe_ttm_pct', width: 80, align: 'right', sorter: numSorter('pe_ttm_pct'),
    render: (r) => r.pe_ttm_pct == null
      ? h('span', { class: 'ov-num dim' }, '--')
      : h('span', { class: `ov-num ${isExtreme(r.pe_ttm_pct) ? 'hl' : ''}` }, `${fmtNum(r.pe_ttm_pct, 1)}%`) },
  { title: '股息率', key: 'dividend_yield', width: 75, align: 'right', sorter: numSorter('dividend_yield'),
    render: (r) => h('span', { class: 'ov-num' }, r.dividend_yield == null ? '--' : `${fmtNum(r.dividend_yield)}%`) },
  { title: '年化波动', key: 'ann_vol_60', width: 85, align: 'right', sorter: numSorter('ann_vol_60'),
    render: (r) => h('span', { class: 'ov-num' }, fmtPct(r.ann_vol_60)) },
])

// ---------- 技术面派生 ----------
const trend = computed(() => tech.value?.trend ?? null)
const risk = computed(() => tech.value?.risk ?? null)

// 按风险章节窗口天数裁剪序列尾部（dates 与各序列等长）
function riskSlice(arr?: (number | null)[], dates?: string[]): { dates: string[]; values: (number | null)[] } {
  const vals = arr ?? []
  const ds = dates ?? []
  if (!vals.length) return { dates: [], values: [] }
  const days = RISK_WINDOWS.find((w) => w.key === riskWin.value)?.days ?? 750
  const n = Math.min(days, vals.length)
  return { dates: ds.slice(ds.length - n), values: vals.slice(vals.length - n) }
}

// 现价距 250 日高点的偏离(%)：负=仍在高点下方，0=刚创新高
const srDist250 = computed(() => {
  const close = tech.value?.close
  const high = trend.value?.support_resist?.high_250
  if (close == null || high == null || high === 0) return null
  return (close / high - 1) * 100
})

// ---------- 基本面派生 ----------
const valBlocks = computed(() => {
  const val = fund.value?.valuation
  if (!val) return []
  const defs: [string, string, string][] = [
    ['pe_ttm', '滚动市盈率 PE-TTM', '倍'],
    ['pb', '市净率 PB', '倍'],
    ['dividend_yield', '股息率', '%'],
    ['pb_dispersion', 'PB 离散度', 'σ'],
  ]
  return defs.map(([key, label, unit]) => {
    const b = val[key]
    if (!b) return null
    return { key, label, unit, current: b.current, pct: b.current_pct, dates: b.dates, values: b.value, note: b.note }
  }).filter(Boolean)
})

const profitBlocks = computed(() => {
  const p = fund.value?.profitability
  if (!p) return []
  const defs: [string, string, string][] = [
    ['roe', '成分股聚合 ROE', '%'],
    ['net_profit_yoy', '净利润同比增速', '%'],
    ['revenue_yoy', '营收同比增速', '%'],
    ['ocf_ratio', '现金流含金量', '倍'],
    ['ocf_sum', '经营现金流净额', '元'],
  ]
  return defs.map(([key, label, unit]) => {
    const b = p[key]
    if (!b) return null
    return { key, label, unit, dates: b.dates, values: b.value, note: b.note }
  }).filter(Boolean)
})

const macroBlocks = computed(() => {
  const items = macro.value?.items
  if (!items) return []
  const defs: [string, string][] = [
    ['gdp', 'GDP（累计同比）'],
    ['pmi', 'PMI（制造业）'],
    ['cpi', 'CPI（当月同比）'],
    ['ppi', 'PPI（当月同比）'],
  ]
  return defs.map(([key, label]) => {
    const b = items[key]
    if (!b) return null
    return { key, label, dates: b.dates, values: b.yoy, latest: b.latest, latest_yoy: b.latest_yoy, note: b.note }
  }).filter(Boolean)
})

const corrMatrix = computed(() => ({
  labels: corr.value?.labels ?? [],
  items: corr.value?.items ?? [],
}))

// 政策与外部环境判读：点击切换两态（落库持久化），hover 展示判读含义
const policyItems = computed(() => policy.value?.items ?? [])
const policyBusy = ref<string>('')
async function setPolicy(topic: string, state: string) {
  const item = policyItems.value.find((t: any) => t.topic === topic)
  if (!item || item.state === state) return
  policyBusy.value = topic
  try {
    const r: any = await api.post(`/environment/domestic/policy?topic=${topic}&state=${state}`)
    if (r?.ok) item.state = state
  } catch (e) {
    console.error('[env policy]', e)
  } finally {
    policyBusy.value = ''
  }
}

onMounted(async () => {
  loadIndices()
  setCurrent('000001')
  loadOverview()
  loadCompare()
  loadStatic()
  loadSentiment()
  loadLeverage()
})

// ---------- 页内锚点导航（两级：技术面/基本面为一级组，其余为二级项；口径说明不进导航） ----------
const NAV_ITEMS = [
  {
    id: 'sec-tech', label: '技术面分析',
    children: [
      { id: 'sec-kline', label: '指数趋势与价格结构' },
      { id: 'sec-tech-risk', label: '指数波动与下行风险' },
      { id: 'sec-sentiment', label: '市场情绪与交易结构' },
      { id: 'sec-leverage', label: '杠杆与资金行为' },
    ],
  },
  {
    id: 'sec-fund', label: '基本面分析',
    children: [
      { id: 'sec-valuation', label: '估值水平' },
      { id: 'sec-profit', label: '盈利质量与成长' },
      { id: 'sec-fund-macro', label: '宏观与政策环境' },
    ],
  },
]
const activeNav = ref<string>('sec-kline')

function scrollTo(id: string) {
  activeNav.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// 滚动时高亮当前区块（取视口顶部 120px 线以上最近的一个锚点）
const FLAT_NAV: { id: string; parent: string }[] = NAV_ITEMS.flatMap(
  (g) => g.children.map((c) => ({ id: c.id, parent: g.id })),
)
function onDocScroll() {
  // 高亮当前区块（取视口顶部 120px 线以上最近的一个锚点）
  const line = 120
  let cur = FLAT_NAV[0].id
  for (const n of FLAT_NAV) {
    const el = document.getElementById(n.id)
    if (el && el.getBoundingClientRect().top <= line) cur = n.id
  }
  activeNav.value = cur
  // 导航吸顶：本页滚动发生在嵌套 layout 的外层滚动容器，CSS sticky 相对内层计算会失效
  // （最小探针实测不吸附），改用 JS translateY 模拟 sticky，钳制在 aside 列内
  updateNavPin()
}

// ---------- 导航吸顶（JS 模拟 sticky） ----------
// 彻底自校正方案：每次滚动实时读 aside 可视位置 + 实时算行程，
// 不做任何一次性快照测量（旧方案在 mounted 时量行程，此时异步内容未渲染、
// 量出的行程≈0，导致滚到下面导航跟着滚走——实测踩坑）
const navInnerRef = ref<HTMLElement | null>(null)
const navAsideRef = ref<HTMLElement | null>(null)

function updateNavPin() {
  const aside = navAsideRef.value
  const inner = navInnerRef.value
  if (!aside || !inner) return
  // 行程 = aside 列高度 - 导航卡高度（aside 通过 align-self:stretch 撑满整列）
  const maxShift = Math.max(0, aside.offsetHeight - inner.offsetHeight)
  const shift = Math.min(Math.max(8 - aside.getBoundingClientRect().top, 0), maxShift)
  inner.style.transform = `translateY(${shift}px)`
}
onMounted(() => {
  // scroll 不冒泡，用捕获在 document 上统一接收所有滚动容器的 scroll 事件
  document.addEventListener('scroll', onDocScroll, { capture: true, passive: true })
  window.addEventListener('resize', updateNavPin, { passive: true })
  updateNavPin()
})
onBeforeUnmount(() => {
  document.removeEventListener('scroll', onDocScroll, { capture: true } as any)
  window.removeEventListener('resize', updateNavPin)
})
</script>

<template>
  <div class="env-layout">
    <!-- 左侧页内锚点导航（两级：技术面/基本面分组；口径说明不进导航） -->
    <aside ref="navAsideRef" class="env-nav">
      <!-- 内层吸顶：外层 stretch 撑满整列，JS translateY 模拟 sticky（嵌套 layout 下 CSS sticky 失效，实测踩坑） -->
      <div ref="navInnerRef" class="env-nav-inner">
        <div v-for="g in NAV_ITEMS" :key="g.id" class="env-nav-group">
          <div
            class="env-nav-item env-nav-parent"
            :class="{ on: g.children.some((c) => c.id === activeNav) }"
            @click="scrollTo(g.children[0].id)"
          >{{ g.label }}</div>
          <div
            v-for="c in g.children"
            :key="c.id"
            class="env-nav-item env-nav-child"
            :class="{ on: activeNav === c.id }"
            @click="scrollTo(c.id)"
          >{{ c.label }}</div>
        </div>
      </div>
    </aside>

    <div class="env">
    <!-- ① 指数范围说明（折叠，默认收起） -->
    <NCard size="small" class="env-scope" id="sec-scope">
      <NCollapse :default-expanded-names="[]">
        <NCollapseItem title="指数范围与口径说明" name="scope">
          <div v-if="scope">
            <p class="scope-p"><b>样本指数</b>：{{ scope.sample }}</p>
            <p class="scope-p"><b>阈值口径</b>：{{ scope.threshold }}</p>
            <p class="scope-p"><b>指标口径</b>：{{ scope.indicator }}</p>
          </div>
        </NCollapseItem>
      </NCollapse>
    </NCard>

    <!-- ② 指数趋势与价格结构（2026-10-23：原「多指数对比」「指数总览」合并为「多指数对比与总览」，作为小节） -->
    <div class="env-section" id="sec-kline">
      <h2 class="env-h2">指数趋势与价格结构</h2>

      <!-- 小节 1：多指数对比与总览（原「多指数对比」+「指数总览」合并） -->
      <h3 class="env-h3">多指数对比与总览</h3>
      <NCard size="small">
        <div class="ov-toolbar">
          <MultiIndexSelect :indices="indices" :model-value="compareCodes" @update:model-value="onCompareChange" />
          <div class="ov-right">
            <!-- 对比模式切换：净值 / 相对强弱 -->
            <div class="ov-win">
              <span
                class="ov-win-btn"
                :class="{ on: compareMode === 'normal' }"
                @click="compareMode = 'normal'"
              >净值</span>
              <span
                class="ov-win-btn"
                :class="{ on: compareMode === 'rs' }"
                @click="compareMode = 'rs'"
              >相对强弱</span>
            </div>
            <!-- 相对强弱基准指数（仅 rs 模式显示） -->
            <NSelect
              v-if="compareMode === 'rs'"
              :value="rsBase"
              :options="rsBaseOptions"
              size="small"
              style="width: 140px"
              placeholder="基准指数"
              @update:value="(v: string) => { rsBase = v }"
            />
            <div class="ov-win">
              <span class="ov-range-label" title="只控制下方净值曲线与区间涨跌幅条形图的取景长度，不影响任何分位">曲线区间</span>
              <span
                v-for="w in ([['3m', '3月'], ['6m', '6月'], ['1y', '1年'], ['3y', '3年']] as const)"
                :key="w[0]"
                class="ov-win-btn"
                :class="{ on: !compareRange && compareWindow === w[0] }"
                @click="onCompareWindow(w[0])"
              >{{ w[1] }}</span>
            </div>
            <NDatePicker
              :value="compareRange"
              type="daterange"
              clearable
              size="small"
              style="width: 240px"
              placeholder="自定义区间"
              @update:value="onCompareRange"
            />
          </div>
        </div>
        <div class="ov-body">
          <NSpin v-if="compareLoading" />
          <NEmpty v-else-if="!compareSeries.length" description="请选择至少一个指数" style="padding: 40px 0" />
          <HistoryCurve
            v-else
            :dates="compareData?.dates || []"
            :series="compareSeries"
            height="380px"
            :zoom="true"
            :y-scale="true"
            :tooltip-formatter="compareMode === 'rs' ? compareRsTooltipFormatter : compareTooltipFormatter"
          />
        </div>
        <!-- 区间统计条形（所选指数当前窗口涨跌幅，零轴居中，按涨跌幅排序；分位/回撤已移至总览表） -->
        <div v-if="compareStats.length" class="compare-stats">
          <div class="cs-head">
            <span class="cs-title">区间涨跌幅 · 按大小排序</span>
            <span class="cs-sub">{{ statsRangeLabel }} · 条长 = 涨跌幅绝对值（以 0 为中心，右正左负）</span>
          </div>
          <div v-for="st in compareStats" :key="st.code" class="cs-row" :title="`${st.name} ${statsRangeLabel}涨跌幅 ${st.pct >= 0 ? '+' : ''}${st.pct.toFixed(2)}%`">
            <span class="cs-name">
              <i class="cs-dot" :style="{ background: st.color }"></i>{{ st.name }}
            </span>
            <div class="cs-bar-track">
              <i class="cs-zero"></i>
              <i
                class="cs-bar"
                :class="st.pct >= 0 ? 'cs-up' : 'cs-down'"
                :style="st.pct >= 0
                  ? { left: '50%', width: (Math.abs(st.pct) / maxAbsStat * 50) + '%', background: st.color }
                  : { right: '50%', width: (Math.abs(st.pct) / maxAbsStat * 50) + '%', background: st.color }"
              ></i>
            </div>
            <span class="cs-val" :class="st.pct >= 0 ? 'c-up' : 'c-down'">{{ st.pct >= 0 ? '+' : '' }}{{ st.pct.toFixed(2) }}%</span>
          </div>
        </div>
        <div v-if="compareMode === 'rs'" class="ov-note">📖 相对强弱模式：每条曲线 = 该指数收盘 ÷ 基准指数（{{ rsBaseName }}）收盘 × 100，基准自身恒为 100 水平线。曲线上穿 100 = 跑赢基准，下穿 = 跑输；斜率向上 = 相对走强。适合看风格/宽基指数的轮动与相对强弱演变。</div>
        <div v-else class="ov-note">📖 曲线为归一化净值（区间起点 = 100），便于不同点位量级的指数同框比强弱；鼠标悬停显示各指数真实收盘点位及相对区间起点的涨跌幅（红涨绿跌）。曲线颜色按选择顺序取自系统调色板。</div>
      </NCard>

      <!-- 指数总览表（并入「多指数对比与总览」小节，紧跟对比图之后） -->
      <NCard size="small" style="margin-top: 12px">
        <div class="ov-toolbar" style="margin-bottom: 12px">
          <div class="ov-range-box">
            <span class="ov-range-label">分位样本池区间</span>
            <div class="ov-win">
              <span
                class="ov-win-btn"
                :class="{ on: !ovRangeWin && !ovRangeCustom }"
                @click="onOvRangeWin('')"
              >关闭</span>
              <span
                v-for="w in ([['3m', '3月'], ['6m', '6月'], ['1y', '1年'], ['3y', '3年'], ['all', '全史']] as const)"
                :key="w[0]"
                class="ov-win-btn"
                :class="{ on: !ovRangeCustom && ovRangeWin === w[0] }"
                @click="onOvRangeWin(w[0])"
              >{{ w[1] }}</span>
            </div>
            <NDatePicker
              :value="ovRangeCustom"
              type="daterange"
              clearable
              size="small"
              style="width: 240px"
              placeholder="自定义区间"
              @update:value="onOvRangeCustom"
            />
            <span class="ov-range-tip">本区间 = 各窗口列「分位」+「五档回撤」+ 下方趋势图「涨日占比分位」的样本池（固定窗口=区间内滚动、本周/月/季/年=区间内完整周期）；关闭则回落近3年</span>
          </div>
        </div>
        <NSpin v-if="overviewLoading" />
        <NDataTable
          v-else
          :columns="columns"
          :data="overview"
          :bordered="false"
          :single-line="false"
          size="small"
          :scroll-x="2910"
          :max-height="640"
          :row-class-name="(r: OverviewRow) => r.index_code === currentCode ? 'cur-row' : ''"
        />
        <div class="ov-note">📖 21 个指数关键指标横向对比；点击指数名联动下方 K 线主图。红 = 涨、绿 = 跌；分位配色：绿 = 超跌分位（&lt;10%）、红 = 超涨分位（&gt;90%）、灰 = 正常。每个窗口单元格上行涨跌幅、下行「分位 + 撤（区间最大回撤，绿）」；**分位的样本池 = 上方「所选区间」**；每个窗口单元格的撤为五档（日K/周K/月K/季K/年K 各自在该窗口区间内的最大回撤，浅灰小字）（固定窗口列取区间内每日滚动样本、本周/月/季/年列取区间内完整周期切片，样本&lt;5 显示 --），关闭则回落近 3 年。「级别」列按当日涨跌幅分五档（阈值 T 各指数不同，取整到 0.5）；本周/本月/本季/本年按自然周期、半月~近1年按固定交易日数（10/21/32/42/53/63/126/252）。</div>
      </NCard>

      <!-- 小节 2：大 K 线主图 -->
      <h3 class="env-h3">主图 K 线</h3>
      <NCard size="small" class="env-kline">
        <MultiIndexSelect
          :indices="indices"
          :model-value="[currentCode]"
          :single="true"
          label="主图指数"
          @update:model-value="(codes: string[]) => { if (codes[0]) setCurrent(codes[0]) }"
        />
        <div class="env-kline-body">
          <EnvKLineChart :code="currentCode" :name="currentName" @range-change="(v: [number, number] | null) => { klCustomRange = v }" />
        </div>
        <div class="ov-note">📖 K 线（日频，同花顺风格）· 主图叠加 MA/布林带，副图可勾选 VOL/MACD/RSI；窗口快切或自定义日期区间；全史覆盖 1990 年起。滚轮缩放，十字光标查看 OHLC。</div>
        <NCard size="small" class="env-udr">
          <UpDownRatioTrend :code="currentCode" :name="currentName" :range-start="updownRange.start" :range-end="updownRange.end" :range-label="updownRange.label" />
        </NCard>
      </NCard>

      <!-- 涨跌与关键位（原独立章节，2026-10-20 合并入本章节） -->
      <template v-if="techLoading">
        <NSpin />
      </template>
      <template v-else-if="tech?.empty">
        <NEmpty :description="tech?.note || '该指数暂无技术面数据'" />
      </template>
      <template v-else-if="trend">
        <!-- 「当日涨跌幅」「多窗口涨跌幅」两块已移除（2026-10-23）：总览表已有 当日 + 级别 + 12 窗口列，避免重复展示 -->

        <IndicatorBlock
          :title="`支撑 / 阻力位 · ${currentName}`"
          :value="srDist250 != null ? `距250日高点 ${fmtPct(srDist250)}` : `近250日 ${fmtNum(trend.support_resist?.low_250)} ~ ${fmtNum(trend.support_resist?.high_250)}`"
          :note="'详细关键位（分形枢轴/整数关口）请在上方 K 线图开启「支撑」开关'"
        >
          <div class="ib-sr">
            <div class="sr-row"><span>近 250 日高点</span><b>{{ fmtNum(trend.support_resist?.high_250) }}</b></div>
            <div class="sr-row"><span>近 250 日低点</span><b>{{ fmtNum(trend.support_resist?.low_250) }}</b></div>
          </div>
        </IndicatorBlock>
      </template>
    </div>

    <!-- ⑤ 波动与下行风险（原「涨跌与关键位」的子节，2026-10-20 升级为独立章节） -->
    <div class="env-section" id="sec-tech-risk">
      <div class="env-head-row">
        <h2 class="env-h2">指数波动与下行风险</h2>
        <div class="ov-win">
          <span
            v-for="w in RISK_WINDOWS"
            :key="w.key"
            class="ov-win-btn"
            :class="{ on: riskWin === w.key }"
            @click="riskWin = w.key"
          >{{ w.label }}</span>
        </div>
      </div>

      <template v-if="techLoading">
        <NSpin />
      </template>
      <template v-else-if="tech?.empty">
        <NEmpty :description="tech?.note || '该指数暂无技术面数据'" />
      </template>
      <template v-else-if="trend">
        <IndicatorBlock
          :title="`最大回撤 · ${currentName}`"
          :value="`1Y ${fmtPct(risk.drawdown?.max_dd_1y)} · 3Y ${fmtPct(risk.drawdown?.max_dd_3y)} · 全史 ${fmtPct(risk.drawdown?.max_dd_all)}`"
          :note="risk.drawdown?.note"
        >
          <HistoryCurve
            :dates="riskSlice(risk.drawdown?.dd_1y, risk.drawdown?.dates).dates"
            :series="[{ name: '回撤(1Y)', values: riskSlice(risk.drawdown?.dd_1y, risk.drawdown?.dates).values, color: '#185FA5' }]"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>

        <IndicatorBlock
          :title="`波动率（60 日滚动） · ${currentName}`"
          :value="`年化 ${fmtPct(risk.volatility?.ann_vol_60)}`"
          :note="risk.volatility?.note"
        >
          <HistoryCurve
            :dates="riskSlice(risk.volatility?.ann_vol, risk.volatility?.dates).dates"
            :series="[
              { name: '年化波动率', values: riskSlice(risk.volatility?.ann_vol, risk.volatility?.dates).values, color: '#185FA5' },
              { name: '下行波动率', values: riskSlice(risk.volatility?.down_vol, risk.volatility?.dates).values, color: '#14B143' },
            ]"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>

        <IndicatorBlock
          :title="`Beta（对沪深300，60日） · ${currentName}`"
          :value="fmtNum(risk.beta?.value)"
          :note="risk.beta?.note"
        >
          <HistoryCurve
            :dates="riskSlice(risk.beta?.beta, risk.beta?.dates).dates"
            :series="[{ name: 'Beta', values: riskSlice(risk.beta?.beta, risk.beta?.dates).values, color: '#185FA5' }]"
            :ref-lines="[{ value: 1, label: 'Beta=1' }]"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>

        <h3 class="env-h3">指数间相关性（120 日）</h3>
        <IndicatorBlock title="相关性矩阵" note="21 指数间 120 日收益率相关性：越接近 1 越同涨同跌">
          <CorrelationMatrix :labels="corrMatrix.labels" :items="corrMatrix.items" />
        </IndicatorBlock>
      </template>
    </div>

    <!-- ⑤b 市场情绪与交易结构（全市场口径，2026-10-20 新增） -->
    <div class="env-section" id="sec-sentiment">
      <h2 class="env-h2">市场情绪与交易结构</h2>

      <template v-if="sentimentLoading">
        <NSpin />
      </template>
      <template v-else-if="sentiment?.empty">
        <NEmpty :description="sentiment?.note || '暂无市场情绪数据'" />
      </template>
      <template v-else-if="sentiment">
        <IndicatorBlock
          title="成交集中度"
          :value="sentiment.concentration?.value != null ? fmtNum(sentiment.concentration.value) + '%' : '--'"
          :highlight="sentiment.concentration?.value >= sentiment.concentration?.threshold"
          :note="sentiment.concentration?.note"
        >
          <div class="ib-inline">
            <span class="ib-big" :class="sentiment.concentration?.value >= sentiment.concentration?.threshold ? 'c-hot' : ''">
              {{ sentiment.concentration?.value != null ? fmtNum(sentiment.concentration.value) + '%' : '--' }}
            </span>
            <span class="ib-thr">抱团风险线：{{ sentiment.concentration?.threshold }}%</span>
          </div>
          <HistoryCurve
            :dates="sentiment.history?.dates || []"
            :series="[{ name: '成交集中度', values: sentiment.history?.concentration || [], color: '#185FA5' }]"
            :ref-lines="[{ value: sentiment.concentration?.threshold, label: '45% 抱团线' }]"
            y-format="%"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>

        <IndicatorBlock
          title="股价偏高个股占比"
          :value="sentiment.overheat_ratio?.value != null ? fmtNum(sentiment.overheat_ratio.value) + '%' : '--'"
          :highlight="sentiment.overheat_ratio?.value >= sentiment.overheat_ratio?.threshold"
          :note="sentiment.overheat_ratio?.note"
        >
          <div class="ib-inline">
            <span class="ib-big" :class="sentiment.overheat_ratio?.value >= sentiment.overheat_ratio?.threshold ? 'c-hot' : ''">
              {{ sentiment.overheat_ratio?.value != null ? fmtNum(sentiment.overheat_ratio.value) + '%' : '--' }}
            </span>
            <span class="ib-thr">过热预警线：{{ sentiment.overheat_ratio?.threshold }}%</span>
          </div>
          <HistoryCurve
            :dates="sentiment.history?.dates || []"
            :series="[{ name: '偏高占比', values: sentiment.history?.overheat_ratio || [], color: '#185FA5' }]"
            :ref-lines="[{ value: sentiment.overheat_ratio?.threshold, label: '15% 过热线' }]"
            y-format="%"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>

        <IndicatorBlock
          title="换手率与涨停家数"
          :value="`换手率 ${sentiment.turnover?.value != null ? fmtNum(sentiment.turnover.value, 2) + '%' : '--'} · 涨停 ${sentiment.limit_up?.value ?? '--'} 家`"
          :note="sentiment.turnover?.note"
        >
          <HistoryCurve
            :dates="sentiment.history?.dates || []"
            :series="[
              { name: '换手率中位数', values: sentiment.history?.turnover || [], color: '#185FA5' },
              { name: '涨停家数', values: sentiment.history?.limit_up || [], color: '#C9A227', yAxisIndex: 1 },
            ]"
            y-format="%"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>
      </template>
    </div>

    <!-- ⑤c 杠杆与资金行为（全市场口径，2026-10-20 新增） -->
    <div class="env-section" id="sec-leverage">
      <h2 class="env-h2">杠杆与资金行为</h2>

      <template v-if="leverageLoading">
        <NSpin />
      </template>
      <template v-else-if="leverage?.empty">
        <NEmpty :description="leverage?.note || '暂无杠杆与资金数据'" />
      </template>
      <template v-else-if="leverage">
        <IndicatorBlock
          title="融资余额占比"
          :value="leverage.margin?.ratio != null ? fmtNum(leverage.margin.ratio, 2) + '%' : '--'"
          :note="leverage.margin?.note"
        >
          <div class="ib-inline">
            <span class="ib-big">{{ leverage.margin?.ratio != null ? fmtNum(leverage.margin.ratio, 2) + '%' : '--' }}</span>
            <span class="ib-thr">融资余额 {{ leverage.margin?.value != null ? fmtYi(leverage.margin.value) : '--' }} · 流通市值 {{ leverage.margin?.float_mv != null ? fmtYi(leverage.margin.float_mv) : '--' }}</span>
          </div>
          <HistoryCurve
            :dates="leverage.history?.dates || []"
            :series="[{ name: '融资余额占比', values: leverage.history?.ratio || [], color: '#185FA5' }]"
            y-format="%"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>

        <IndicatorBlock
          title="ETF 资金流向"
          :value="leverage.etf?.net_inflow != null ? fmtYi(leverage.etf.net_inflow) : (leverage.etf?.empty ? '采集积累中' : '--')"
          :note="leverage.etf?.note"
        >
          <div class="ib-inline">
            <span class="ib-big">{{ leverage.etf?.net_inflow != null ? fmtYi(leverage.etf.net_inflow) : '--' }}</span>
            <span class="ib-thr">{{ leverage.etf?.empty ? '暂无数据' : ('总份额 ' + fmtYi(leverage.etf.total_share)) }}</span>
          </div>
          <HistoryCurve
            :dates="leverage.etf?.history?.dates || []"
            :series="[
              { name: '净流入', values: leverage.etf?.history?.inflow || [], color: '#185FA5' },
              { name: '总份额', values: leverage.etf?.history?.share || [], color: '#C9A227', yAxisIndex: 1 },
            ]"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>

        <IndicatorBlock
          title="新增开户"
          :value="leverage.account?.a_person != null ? fmtNum(leverage.account.a_person, 2) + ' 万户' : '--'"
          :note="leverage.account?.note"
        >
          <div class="ib-inline">
            <span class="ib-big">{{ leverage.account?.a_person != null ? fmtNum(leverage.account.a_person, 2) + ' 万户' : '--' }}</span>
            <span class="ib-thr">个人 A 股新开户 · {{ leverage.account?.as_of || '' }}</span>
          </div>
          <HistoryCurve
            :dates="leverage.account?.history?.months || []"
            :series="[{ name: '个人 A 股新开户', values: leverage.account?.history?.a_person || [], color: '#185FA5' }]"
            height="240px"
            :zoom="true"
          />
        </IndicatorBlock>
      </template>
    </div>

    <!-- ⑥ 估值水平 -->
    <div class="env-section" id="sec-valuation">
      <h2 class="env-h2">估值水平</h2>

      <template v-if="fundLoading">
        <NSpin />
      </template>
      <template v-else-if="fund?.empty">
        <NEmpty :description="fund?.note || '该指数暂无估值数据'" />
      </template>
      <template v-else-if="fund">
        <IndicatorBlock
          v-for="b in valBlocks"
          :key="'val-' + b!.key"
          :title="b!.label"
          :value="b!.current != null ? `${fmtNum(b!.current)} ${b!.unit}` : '--'"
          :pct="b!.pct"
          pct-label="全史"
          :highlight="isExtreme(b!.pct)"
          :note="b!.note"
        >
          <HistoryCurve
            :dates="b!.dates"
            :series="[{ name: b!.label, values: b!.values, color: '#185FA5' }]"
            height="260px"
          />
        </IndicatorBlock>

        <IndicatorBlock
          v-if="fund.valuation?.erp"
          title="股债收益差 / 风险溢价 ERP"
          :value="fmtNum(fund.valuation.erp.current) + '%'"
          :note="fund.valuation.erp.note"
        >
          <HistoryCurve
            :dates="fund.valuation.erp.dates"
            :series="[{ name: 'ERP', values: fund.valuation.erp.value, color: '#185FA5' }]"
            height="260px"
          />
        </IndicatorBlock>
      </template>
    </div>

    <!-- ⑦ 盈利质量与成长 -->
    <div class="env-section" id="sec-profit">
      <h2 class="env-h2">盈利质量与成长</h2>

      <template v-if="fundLoading">
        <NSpin />
      </template>
      <template v-else-if="fund?.empty">
        <NEmpty :description="fund?.note || '该指数暂无盈利数据'" />
      </template>
      <template v-else-if="fund">
        <IndicatorBlock
          v-for="b in profitBlocks"
          :key="'profit-' + b!.key"
          :title="b!.label"
          :note="b!.note"
        >
          <HistoryCurve
            :dates="b!.dates"
            :series="[{ name: b!.label, values: b!.values, color: '#2D6CDF' }]"
            :y-format="b!.unit"
            height="260px"
          />
        </IndicatorBlock>
      </template>
    </div>

    <!-- ⑧ 宏观与政策环境 -->
    <div class="env-section" id="sec-fund-macro">
      <h2 class="env-h2">宏观与政策环境</h2>
      <div v-if="!macroBlocks.length" class="env-macro-empty">暂无宏观序列数据（GDP/PMI/CPI/PPI），待采集后可见</div>
      <IndicatorBlock
        v-for="b in macroBlocks"
        :key="'macro-' + b!.key"
        :title="b!.label"
        :value="b!.latest_yoy != null ? fmtPct(b!.latest_yoy) + '（' + b!.latest + '）' : '--'"
        :note="b!.note"
      >
        <HistoryCurve
          :dates="b!.dates"
          :series="[{ name: b!.label, values: b!.values, color: '#185FA5' }]"
          y-format="%"
          height="240px"
        />
      </IndicatorBlock>

      <!-- 政策与外部环境判读（人工两态，落库） -->
      <div class="policy-grid">
        <div v-for="t in policyItems" :key="'policy-' + t.topic" class="policy-card">
          <div class="policy-title">{{ t.title }}</div>
          <div class="policy-options">
            <div
              v-for="o in t.options"
              :key="o.key"
              class="policy-opt"
              :class="{ on: t.state === o.key }"
              :title="o.hint"
              @click="setPolicy(t.topic, o.key)"
            >
              <span class="policy-dot" />
              <span class="policy-label">{{ o.label }}</span>
            </div>
          </div>
          <div class="policy-updated" v-if="t.updated_at">判读于 {{ t.updated_at.slice(0, 16) }}</div>
        </div>
      </div>
      <div class="policy-note">政策与外部环境为人工定性判读（两态互斥，点击切换即落库），供复盘时与行情对照；悬停可见各状态的判读含义。</div>
    </div>
    </div><!-- /.env -->
  </div><!-- /.env-layout -->
</template>

<style scoped>
/* 页内导航 + 内容两栏布局；导航 sticky 吸顶跟随滚动 */
.env-layout { display: flex; gap: 14px; align-items: flex-start; max-width: 1440px; margin: 0 auto; }
.env-nav {
  flex: none; width: 150px; align-self: stretch;
  /* ⚠️ sticky 放在内层 .env-nav-inner：外层必须 stretch 撑满整列提供行程；
     若外层自身 sticky（stretch 后高度=整列），行程为 0 会跟着滚走（实测踩坑） */
}
.env-nav-inner {
  display: flex; flex-direction: column; gap: 2px;
  padding: 8px 6px; background: #FFFFFF; border: 1px solid #EDEFF2; border-radius: 8px;
  will-change: transform;
}
.env-nav-item {
  font-size: 12px; color: #6B7280; padding: 6px 8px; border-radius: 5px; cursor: pointer;
  /* 允许换行兜底：长标题（如「市场情绪与交易结构」）不溢出边界 */
  white-space: normal; word-break: break-all; user-select: none; line-height: 1.45;
}
.env-nav-parent { font-weight: 600; color: #1F2937; }
.env-nav-parent.on { color: #185FA5; }
.env-nav-child { padding-left: 20px; font-size: 12px; }
.env-nav-item:hover { color: #185FA5; background: #F0F6FD; }
.env-nav-item.on { color: #185FA5; background: #E3EDF9; font-weight: 600; }
.env { flex: 1; min-width: 0; max-width: 1280px; }
.env-scope { margin-bottom: 12px; }
.scope-p { font-size: 13px; line-height: 1.7; color: #374151; margin: 6px 0; }
.scope-p b { color: #1F2937; }
.env-section { margin-bottom: 8px; }
.env-h2 {
  font-size: 17px; font-weight: 700; color: #185FA5; margin: 18px 0 12px;
  padding-left: 8px; border-left: 3px solid #185FA5;
}
.env-h3 { font-size: 14px; font-weight: 600; color: #374151; margin: 16px 0 10px; }
.env-head-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.env-head-row .env-h2 { margin: 18px 0 12px; }
.env-kline { margin-bottom: 12px; }
.env-udr { margin-top: 12px; }
.ek-selector { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.ek-selector-label { font-size: 12px; color: #909399; flex: none; }
.env-kline-body { }
.ov-toolbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.ov-right { display: flex; align-items: center; gap: 10px; flex: none; }
.ov-win { display: flex; gap: 4px; }
.ov-win-btn {
  font-size: 12px; padding: 3px 10px; border-radius: 4px; cursor: pointer;
  border: 1px solid #DDE3EA; color: #6B7280; user-select: none;
}
.ov-win-btn.on { background: #185FA5; border-color: #185FA5; color: #fff; }
.ov-body { margin-top: 12px; }
.ov-note { margin-top: 10px; font-size: 12px; line-height: 1.6; color: #6B7280; }
/* 区间统计条形（多指数对比图下方，零轴居中发散式） */
.compare-stats { margin-top: 16px; padding: 12px 14px 10px; background: #FAFBFC; border: 1px solid #EFF2F6; border-radius: 8px; }
.cs-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.cs-title { font-size: 13px; font-weight: 700; color: #1F2937; }
.cs-sub { font-size: 11px; color: #9CA3AF; }
.cs-row { display: flex; align-items: center; gap: 10px; font-size: 12px; padding: 3px 0; }
.cs-name { flex: 0 0 130px; display: flex; align-items: center; gap: 6px; justify-content: flex-end; font-weight: 600; color: #374151; white-space: nowrap; overflow: hidden; }
.cs-dot { flex: 0 0 8px; width: 8px; height: 8px; border-radius: 50%; }
.cs-bar-track { flex: 1 1 auto; min-width: 0; height: 12px; position: relative; background: linear-gradient(to right, #F6F8FA 0 50%, #FBFDFC 50% 100%); border-radius: 3px; }
.cs-zero { position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: #C9CFD8; }
.cs-bar { position: absolute; top: 0; bottom: 0; border-radius: 2px; opacity: 0.85; }
.cs-val { flex: 0 0 72px; text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }

/* 总览表区间选择器 */
.ov-range-box { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
/* 区间说明标签：灰色软 chip，与胶囊按钮族同语言（灰=说明、蓝=选中），避免裸文本突兀 */
.ov-range-label {
  font-size: 12px; font-weight: 500; color: #6B7280; line-height: 1;
  padding: 5px 10px; border-radius: 4px; background: #F3F4F6;
  white-space: nowrap; user-select: none;
}
.ov-range-tip { font-size: 11px; color: #9CA3AF; }

/* 窗口单元格双行（涨跌幅主行 + 分位/回撤副行） */
:deep(.ov-win-cell) { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; line-height: 1.35; }
:deep(.ov-win-cell .ov-num) { font-size: 13px; }
:deep(.ov-win-sub) { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; font-size: 10px; line-height: 1.3; }
:deep(.ov-win-sub .ov-sub-dd) { font-size: 9px; color: #9CA3AF; white-space: nowrap; }
:deep(.ov-dd-chips) { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 2px; }
:deep(.ov-dd-chip) { display: inline-flex; align-items: center; gap: 1px; padding: 0 4px; height: 14px; border-radius: 3px; background: #F3F4F6; }
:deep(.ov-dd-chip-lb) { font-style: normal; font-size: 9px; color: #9CA3AF; }
:deep(.ov-dd-chip-v) { font-weight: 600; font-size: 9px; color: #4B5563; font-variant-numeric: tabular-nums; }
:deep(.ov-sub-pct) { color: #909399; }
:deep(.ov-sub-pct.ov-sub-pct-low) { color: #14B143; font-weight: 700; }
:deep(.ov-sub-pct.ov-sub-pct-high) { color: #EF232A; font-weight: 700; }
:deep(.ov-sub-dd) { color: #6B7280; }

:deep(.ov-link) { display: inline-flex; align-items: baseline; gap: 6px; cursor: pointer; text-decoration: none; }
:deep(.ov-link .ov-name) { font-weight: 600; color: #185FA5; }
:deep(.ov-link:hover .ov-name) { color: #1E6FFF; }
:deep(.ov-link.cur .ov-name) { color: #C9A227; }
:deep(.ov-link .ov-code) { font-size: 11px; color: #909399; }
:deep(.ov-num) { font-variant-numeric: tabular-nums; font-size: 13px; color: #374151; }
:deep(.ov-num.dim) { color: #C0C4CC; }
:deep(.ov-num.hl) { color: #C9A227; font-weight: 600; }
:deep(.cur-row) { background: #F0F6FD; }
/* NDataTable 列 render 产出的节点不带本组件 scoped 属性，必须 :deep() 才能命中 */
:deep(.c-up) { color: #EF232A; }
:deep(.c-down) { color: #14B143; }
/* 「级别」列：五档分色 chip（红涨绿跌 + 深浅分级，狂档加底色） */
:deep(.ov-level) {
  display: inline-block; min-width: 40px; padding: 2px 8px; border-radius: 4px;
  font-size: 12px; font-weight: 600; line-height: 1.5; text-align: center;
}
:deep(.ov-level.lv-surge)   { color: #B91C1C; background: #FDE2E2; } /* 狂涨：深红 + 浅红底 */
:deep(.ov-level.lv-up)      { color: #EF232A; }                     /* 大涨：标准红 */
:deep(.ov-level.lv-up-light){ color: #F58A8A; font-weight: 500; }   /* 小涨：浅红 */
:deep(.ov-level.lv-flat)    { color: #9CA3AF; font-weight: 500; }   /* 平：灰 */
:deep(.ov-level.lv-down-light){ color: #5FBE79; font-weight: 500; } /* 小跌：浅绿 */
:deep(.ov-level.lv-down)    { color: #14B143; }                     /* 大跌：标准绿 */
:deep(.ov-level.lv-plunge)  { color: #0B7A31; background: #D9F2E1; } /* 狂跌：深绿 + 浅绿底 */
.ib-inline { display: flex; align-items: baseline; gap: 14px; }
.ib-big { font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; }
.ib-thr { font-size: 12px; color: #6B7280; }
/* 抱团/过热预警高亮（金色，与「蓝骨金魂」亮点信号一致；非涨跌红绿语义） */
.ib-big.c-hot { color: #C9A227; }
.ib-sr { display: flex; flex-direction: column; gap: 6px; }
.sr-row { display: flex; justify-content: space-between; font-size: 13px; color: #374151; }
.sr-row b { font-variant-numeric: tabular-nums; color: #1F2937; }
.env-macro-empty { font-size: 13px; color: #909399; padding: 12px 0; }
/* 政策与外部环境判读卡（人工两态） */
.policy-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.policy-card {
  background: #FFFFFF; border: 1px solid #EDEFF2; border-radius: 8px; padding: 12px 14px;
  display: flex; flex-direction: column; gap: 10px;
}
.policy-title { font-size: 14px; font-weight: 600; color: #1F2937; }
.policy-options { display: flex; flex-direction: column; gap: 6px; }
.policy-opt {
  display: flex; align-items: flex-start; gap: 8px; padding: 8px 10px; border-radius: 6px;
  border: 1px solid #E5E9EF; background: #F7F9FB; cursor: pointer; user-select: none;
  font-size: 13px; line-height: 1.5; color: #6B7280; transition: all 0.15s;
}
.policy-opt:hover { border-color: #185FA5; color: #185FA5; }
.policy-opt.on {
  background: #E3EDF9; border-color: #185FA5; color: #185FA5; font-weight: 600;
}
.policy-dot {
  flex: none; width: 8px; height: 8px; border-radius: 50%; margin-top: 6px;
  background: #C0C4CC;
}
.policy-opt.on .policy-dot { background: #185FA5; }
.policy-updated { font-size: 11px; color: #909399; }
.policy-note { margin-top: 10px; font-size: 12px; color: #6B7280; line-height: 1.6; }
</style>
