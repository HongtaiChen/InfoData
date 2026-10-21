<script setup lang="ts">
/**
 * DomesticIndexView —— 投资环境·国内指数（单页，v1.7）
 * 页面结构（自上而下）：
 *   ① 多指数对比（归一化净值曲线 + 悬停显示真实收盘与区间涨跌幅，chips 多选 + 窗口/自定义区间，曲线各配色）
 *   ② 国内指数总览表（21 指数关键指标横向对比，涨跌红绿字，点指数名联动 K 线）
 *   ③ 大 K 线主图（单指数深钻：K线+MA+BOLL、VOL/MACD/RSI 副图勾选、窗口/自定义区间）
 *   ④ 技术面统计 + 基本面分析
 * 指数在每处内容自由多选/单选；不做 AI 结论（E3），分位中性展示。
 */
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { NCard, NCollapse, NCollapseItem, NSpin, NEmpty, NDatePicker, NDataTable } from 'naive-ui'
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

// ---------- 总览表状态 ----------
interface OverviewRow {
  index_code: string; index_name: string; trade_date: string | null
  close: number | null; change_pct: number | null
  win_1m: number | null; win_1y: number | null
  ann_vol_60: number | null; max_dd_1y: number | null
  pe_ttm: number | null; pe_ttm_pct: number | null
  pb: number | null; pb_pct: number | null
  dividend_yield: number | null; dividend_yield_pct: number | null
}
const overview = ref<OverviewRow[]>([])
const overviewLoading = ref(false)

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
    const resp: any = await api.get('/environment/domestic/overview')
    overview.value = resp.items || []
  } catch (e) {
    console.error('[env overview]', e)
  } finally {
    overviewLoading.value = false
  }
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
const compareSeries = computed(() => {
  const d = compareData.value
  if (!d?.series) return []
  return d.series.map((s: any, i: number) => ({
    name: s.index_name,
    values: s.values,
    color: SERIES_PALETTE[i % SERIES_PALETTE.length],
  }))
})

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
const columns: DataTableColumns<OverviewRow> = [
  { title: '指数', key: 'index_name', width: 150, fixed: 'left',
    render: (r) => h('a', {
      class: 'ov-link' + (r.index_code === currentCode.value ? ' cur' : ''),
      onClick: () => onCompareChip(r.index_code),
    }, [
      h('span', { class: 'ov-name' }, r.index_name),
      h('span', { class: 'ov-code' }, r.index_code),
    ]) },
  { title: '收盘', key: 'close', width: 90, align: 'right',
    render: (r) => h('span', { class: 'ov-num' }, fmtNum(r.close)) },
  { title: '当日', key: 'change_pct', width: 85, align: 'right',
    render: (r) => h('span', { class: `ov-num ${pctClass(r.change_pct)}` }, fmtPct(r.change_pct)) },
  { title: '近1月', key: 'win_1m', width: 85, align: 'right',
    render: (r) => h('span', { class: `ov-num ${pctClass(r.win_1m)}` }, fmtPct(r.win_1m)) },
  { title: '近1年', key: 'win_1y', width: 85, align: 'right',
    render: (r) => h('span', { class: `ov-num ${pctClass(r.win_1y)}` }, fmtPct(r.win_1y)) },
  { title: 'PE', key: 'pe_ttm', width: 70, align: 'right',
    render: (r) => h('span', { class: 'ov-num' }, fmtNum(r.pe_ttm)) },
  { title: 'PE分位', key: 'pe_ttm_pct', width: 80, align: 'right',
    render: (r) => r.pe_ttm_pct == null
      ? h('span', { class: 'ov-num dim' }, '--')
      : h('span', { class: `ov-num ${isExtreme(r.pe_ttm_pct) ? 'hl' : ''}` }, `${fmtNum(r.pe_ttm_pct, 1)}%`) },
  { title: '股息率', key: 'dividend_yield', width: 75, align: 'right',
    render: (r) => h('span', { class: 'ov-num' }, r.dividend_yield == null ? '--' : `${fmtNum(r.dividend_yield)}%`) },
  { title: '年化波动', key: 'ann_vol_60', width: 85, align: 'right',
    render: (r) => h('span', { class: 'ov-num' }, fmtPct(r.ann_vol_60)) },
  { title: '回撤1Y', key: 'max_dd_1y', width: 85, align: 'right',
    render: (r) => h('span', { class: 'ov-num' }, fmtPct(r.max_dd_1y)) },
]

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

const thresholdLine = computed(() => {
  const it = indices.value.find((i) => i.index_code === currentCode.value)
  return it?.drop_threshold ?? null
})

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
    id: 'sec-compare', label: '技术面分析',
    children: [
      { id: 'sec-compare', label: '多指数对比' },
      { id: 'sec-overview', label: '指数总览' },
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
const activeNav = ref<string>('sec-compare')

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

    <!-- ② 多指数对比 -->
    <div class="env-section" id="sec-compare">
      <h2 class="env-h2">多指数对比</h2>
      <NCard size="small">
        <div class="ov-toolbar">
          <MultiIndexSelect :indices="indices" :model-value="compareCodes" @update:model-value="onCompareChange" />
          <div class="ov-right">
            <div class="ov-win">
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
            :tooltip-formatter="compareTooltipFormatter"
          />
        </div>
        <div class="ov-note">📖 曲线为归一化净值（区间起点 = 100），便于不同点位量级的指数同框比强弱；鼠标悬停显示各指数<b>真实收盘点位</b>及<b>相对区间起点的涨跌幅</b>（红涨绿跌）。曲线颜色按选择顺序取自系统调色板。</div>
      </NCard>
    </div>

    <!-- ③ 国内指数总览表 -->
    <div class="env-section" id="sec-overview">
      <h2 class="env-h2">国内指数总览</h2>
      <NCard size="small">
        <NSpin v-if="overviewLoading" />
        <NDataTable
          v-else
          :columns="columns"
          :data="overview"
          :bordered="false"
          :single-line="false"
          size="small"
          :scroll-x="1240"
          :max-height="640"
          :row-class-name="(r: OverviewRow) => r.index_code === currentCode ? 'cur-row' : ''"
        />
        <div class="ov-note">📖 21 个指数关键指标横向对比；点击指数名联动下方 K 线主图。红 = 涨、绿 = 跌；金色 = 分位进入极值区（&lt;10% 或 &gt;90%）。</div>
      </NCard>
    </div>

    <!-- ④ 大 K 线主图 -->
    <div class="env-section" id="sec-kline">
      <h2 class="env-h2">指数趋势与价格结构</h2>
      <NCard size="small" class="env-kline">
        <MultiIndexSelect
          :indices="indices"
          :model-value="[currentCode]"
          :single="true"
          label="主图指数"
          @update:model-value="(codes: string[]) => { if (codes[0]) setCurrent(codes[0]) }"
        />
        <div class="env-kline-body">
          <EnvKLineChart :code="currentCode" :name="currentName" />
        </div>
        <div class="ov-note">📖 K 线（日频，同花顺风格）· 主图叠加 MA/布林带，副图可勾选 VOL/MACD/RSI；窗口快切或自定义日期区间；全史覆盖 1990 年起。滚轮缩放，十字光标查看 OHLC。</div>
        <NCard size="small" class="env-udr">
          <UpDownRatioTrend :code="currentCode" :name="currentName" />
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
        <IndicatorBlock
          :title="`当日涨跌幅 · ${currentName}`"
          :value="fmtPct(trend.change_pct?.value)"
          :note="trend.change_pct?.note"
        >
          <div class="ib-inline">
            <span class="ib-big" :class="trend.change_pct?.value > 0 ? 'c-up' : trend.change_pct?.value < 0 ? 'c-down' : ''">
              {{ fmtPct(trend.change_pct?.value) }}
            </span>
            <span v-if="thresholdLine != null" class="ib-thr">阈值参考线：{{ thresholdLine }}%</span>
          </div>
        </IndicatorBlock>

        <IndicatorBlock :title="`多窗口涨跌幅 · ${currentName}`" note="12 个时间窗口（半月/一月/…/本年/本季/本月/本周）的当前涨跌幅，横向对比近期各周期表现">
          <div class="win-matrix">
            <div v-for="w in trend.windows" :key="w.key" class="win-cell" :class="w.value > 0 ? 'c-up-bg' : w.value < 0 ? 'c-down-bg' : ''">
              <span class="win-label">{{ w.label }}</span>
              <span class="win-val">{{ fmtPct(w.value) }}</span>
            </div>
          </div>
        </IndicatorBlock>

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
            <span class="ib-thr">{{ leverage.etf?.empty ? '自采集启动日逐日积累' : ('总份额 ' + fmtYi(leverage.etf.total_share)) }}</span>
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
.ib-inline { display: flex; align-items: baseline; gap: 14px; }
.ib-big { font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; }
.ib-thr { font-size: 12px; color: #6B7280; }
/* 抱团/过热预警高亮（金色，与「蓝骨金魂」亮点信号一致；非涨跌红绿语义） */
.ib-big.c-hot { color: #C9A227; }
.ib-sr { display: flex; flex-direction: column; gap: 6px; }
.sr-row { display: flex; justify-content: space-between; font-size: 13px; color: #374151; }
.sr-row b { font-variant-numeric: tabular-nums; color: #1F2937; }
.win-matrix { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
.win-cell {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  padding: 10px 6px; border-radius: 6px; background: #F5F7FA;
}
.win-cell.c-up-bg { background: #FDECEC; }
.win-cell.c-down-bg { background: #E7F6EC; }
.win-label { font-size: 12px; color: #6B7280; }
.win-val { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.win-cell.c-up-bg .win-val { color: #EF232A; }
.win-cell.c-down-bg .win-val { color: #14B143; }
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
