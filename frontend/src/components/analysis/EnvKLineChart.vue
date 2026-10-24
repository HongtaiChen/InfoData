<script setup lang="ts">
/**
 * EnvKLineChart —— 国内指数的大 K 线主图
 * 复用 klinecharts（与数据探查 K 线同引擎、同花顺风格、红涨绿跌、十字光标 OHLC）。
 * - 副图可勾选：VOL / MACD / RSI（主图 MA + BOLL 叠加）
 * - 窗口快切：1月/3月/6月/1年/3年/全史 + 自定义日期区间
 * - 平移：允许左右拖拽，用 setMaxOffsetLeft/RightDistance 钳制在数据范围内——
 *   否则拖到数据末尾之后会出现大片「未来空白」（2026-10-18 用户实测反馈）。
 * - 容器高度随副图数量动态增长，避免多指标时 pane 挤压不可读。
 * 数据源：GET /market/kline?is_index=true（dc_index_market 原始 OHLCV，全史 1990 年起）。
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { init, dispose, registerIndicator } from 'klinecharts'
import type { Chart, KLineData } from 'klinecharts'
import { NDatePicker } from 'naive-ui'
import dayjs from 'dayjs'
import api from '../../api'

const props = withDefaults(defineProps<{
  code: string
  name?: string
}>(), {})

// 自定义区间变化时抛给父组件：涨日占比趋势图的「分位样本池」跟随 K 线自定义区间
// （2026-10-24：此前 K 线内部区间不外抛，趋势图分位只认总览表区间，用户自定义后趋势图"没反应"）
const emit = defineEmits<{
  (e: 'range-change', v: [number, number] | null): void
}>()

const containerRef = ref<HTMLDivElement>()
let chart: Chart | null = null
let resizeObserver: ResizeObserver | null = null

const loadingMsg = ref('加载中...')
const latest = ref<any>(null)
let latestApiPct: number | null = null

// 区间涨跌幅：默认 区间首根收盘 → 末根收盘；
// 十字光标悬停时以悬停的那根为起点、末根为终点实时重算（光标离开恢复默认）
const rangePct = ref<number | null>(null)
const rangeLabel = ref('')
let barCache: KLineData[] = []

function updateRange(startIdx: number | null) {
  const list = barCache
  if (list.length < 2 || !list[0].close) { rangePct.value = null; rangeLabel.value = ''; return }
  const last = list[list.length - 1]
  const start = startIdx != null && startIdx >= 0 && startIdx < list.length - 1 ? list[startIdx] : list[0]
  rangePct.value = ((last.close - start.close) / start.close) * 100
  rangeLabel.value = `${dayjs(start.timestamp).format('YYYY-MM-DD')} → ${dayjs(last.timestamp).format('YYYY-MM-DD')}`
}

// 副图开关（BOLL 因 v10 主图 pane 只允许一个叠加指标——MA 与 BOLL 互斥顶替——归入副图）
const showVol = ref(true)
const showMacd = ref(true)
const showRsi = ref(false)
const showBoll = ref(true)
// 蜡烛实体显示开关：关掉后只留均线（2026-10-19 用户需求）
const showCandles = ref(true)
// 两市成交额副图开关（全市场个股 amount 求和，即 market_style_daily.market_amount，金色实线，2026-10-19 用户需求）
const showTwoAmount = ref(false)
// 支撑/阻力位开关（分形枢轴点 + 整数关口，2026-10-20 需求）
const showSr = ref(false)

function toggleCandles() {
  showCandles.value = !showCandles.value
  if (!chart) return
  if (!showCandles.value) {
    // 蜡烛本体/影线/边框全透明 + 隐藏最高最低价标记（v10 无 candle.show 开关，用透明色实现）
    const t = 'rgba(0,0,0,0)'
    chart.setStyles({
      candle: {
        bar: {
          upColor: t, downColor: t, noChangeColor: t,
          upBorderColor: t, downBorderColor: t, noChangeBorderColor: t,
          upWickColor: t, downWickColor: t, noChangeWickColor: t,
        },
        priceMark: { show: false },
      },
    } as any)
  } else {
    // 恢复主题样式；priceMark 需显式设回 show:true（setStyles 是合并语义，不会自动还原）
    applyTonghuashunStyle(chart)
    chart.setStyles({ candle: { priceMark: { show: true } } } as any)
  }
}

// 周期：日K / 周K / 月K / 年K（周/月/年由日线在前端聚合，数据源同一份全史日线）
const period = ref<'day' | 'week' | 'month' | 'year'>('day')

// 窗口快切（全史 = 10000 根，覆盖 1990 年起的完整历史）——按「交易日天数」定义，
// 聚合周期按天数折算成对应周期根数（周≈天/5，月≈天/21）
// ⚠️ 根数 = 总览表窗口天数 + 1：区间涨跌幅首根 = N 个交易日前，与总览表 win_*（今收÷N日前收−1）口径对齐
// （2026-10-23 用户实测发现 K线「1年」-4.52% vs 总览 近1年 -6.19% 不一致，根因即此错位）
const WINDOWS = [
  { label: '1月', days: 22 },
  { label: '3月', days: 64 },
  { label: '6月', days: 127 },
  { label: '1年', days: 253 },
  { label: '3年', days: 750 },
  { label: '全史', days: 10000 },
]
const activeWin = ref<number | null>(253)
// 自定义日期区间（时间戳 ms）；一旦设置优先于窗口快切
const customRange = ref<[number, number] | null>(null)

const subCount = computed(() => [showVol.value, showMacd.value, showRsi.value, showBoll.value, showTwoAmount.value].filter(Boolean).length)
const chartHeight = computed(() => 400 + subCount.value * 115)

function applyTonghuashunStyle(c: Chart) {
  c.setStyles({
    grid: {
      horizontal: { color: '#eceff3', size: 1 },
      vertical: { color: '#eceff3', size: 1 },
    },
    crosshair: {
      horizontal: {
        line: { color: '#b8b8b8', style: 'dashed' },
        text: { backgroundColor: '#EF232A', color: '#ffffff', borderRadius: 2 },
      },
      vertical: {
        line: { color: '#b8b8b8', style: 'dashed' },
        text: { backgroundColor: '#EF232A', color: '#ffffff', borderRadius: 2 },
      },
    },
    candle: {
      bar: {
        upColor: '#EF232A', downColor: '#14B143', noChangeColor: '#999999',
        upBorderColor: '#EF232A', downBorderColor: '#14B143',
        upWickColor: '#EF232A', downWickColor: '#14B143',
      },
      priceMark: {
        high: { color: '#EF232A' }, low: { color: '#14B143' },
        last: { upColor: '#EF232A', downColor: '#14B143', noChangeColor: '#999999' },
      },
      tooltip: {
        // 内部标题行（如「000001 · 1D」）与组件头部的指数名/周期重复，隐藏以减少拥挤
        title: { show: false },
        legend: {
          template: (data: any) => {
            const d = data.current
            if (!d) return []
            return [
              { title: '时间', value: dayjs(d.timestamp).format('YYYY-MM-DD') },
              { title: '开盘', value: d.open.toFixed(2) },
              { title: '最高', value: d.high.toFixed(2) },
              { title: '最低', value: d.low.toFixed(2) },
              { title: '收盘', value: d.close.toFixed(2) },
              { title: '涨跌幅', value: `${d.changePct ?? ''}` },
              { title: '成交量(手)', value: formatVol(d.volume) },
              // 成交额：当日盘后未写入时为 0/null，显示 -- 而非「0」（2026-10-24）
              { title: '成交额', value: (d as any).amount ? formatVol((d as any).amount) : '--' },
            ]
          },
        },
      },
    },
    xAxis: { axisLine: { color: '#d4d7de' }, tickText: { color: '#666666' } },
    yAxis: { axisLine: { color: '#d4d7de' }, tickText: { color: '#666666' } },
    separator: { color: '#e6e8ec' },
  })
}

function formatVol(v?: number): string {
  if (!v && v !== 0) return '-'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return String(v)
}

// ---------- MA(5/10/30/60/250) 自定义指标 ----------
// 为什么自定义：MA250 需要 250 根前置 K 线做预热，而窗口快切按窗口取数（如「1年」只有 250 根），
// 内置 MA 直接算会让 MA250 几乎整条为空。方案：取数时多取 250 根预热，MA 全序列基于
// 全量数据预计算成 maMap（timestamp → 各周期值），经 extendData 注入指标；
// 展示仍只显示窗口内 K 线，窗口语义不变且 MA250 全程有效。
const MA_PARAMS = [5, 10, 30, 40, 60, 250]
// 各周期线色（2026-10-24 优化可辨识度：六色相全部拉开，避开 K 线红绿主色混淆区）
// 5橙/10蓝/30紫/40青(策略线,加粗)/60红/250灰(长期线弱化)；原 40 暗金褐与 5 橙太近、250 粉与 60 红难分
const MA_COLORS: Record<number, string> = { 5: '#F5A623', 10: '#378ADD', 30: '#8452D6', 40: '#0E9E9E', 60: '#EF232A', 250: '#6B7280' }
// MA40 为策略主线加粗，其余 1.3 提升浅底可读性（默认 1 太细）
const MA_LINE_SIZE: Record<number, number> = { 5: 1.3, 10: 1.3, 30: 1.3, 40: 2, 60: 1.3, 250: 1.3 }
type MaMap = Record<string, Record<number, number>>
let maMap: MaMap = {}

function computeMaMap(list: KLineData[]): MaMap {
  const map: MaMap = {}
  for (let i = 0; i < list.length; i++) {
    const row: Record<number, number> = {}
    for (const p of MA_PARAMS) {
      if (i >= p - 1) {
        let s = 0
        for (let j = i - p + 1; j <= i; j++) s += list[j].close
        row[p] = s / p
      }
    }
    map[list[i].timestamp] = row
  }
  return map
}

let maRegistered = false
function registerCustomMA() {
  if (maRegistered) return
  maRegistered = true
  registerIndicator({
    name: 'MA',
    shortName: 'MA',
    calcParams: MA_PARAMS,
    figures: MA_PARAMS.map((p) => ({ key: `ma${p}`, title: `MA${p}: `, type: 'line' as const })),
    calc: (dataList: KLineData[], indicator: any) => {
      const ext = indicator.extendData as MaMap | undefined
      const params = (indicator.calcParams as number[]) || MA_PARAMS
      return dataList.map((b, i) => {
        const pre = ext?.[b.timestamp]
        const out: Record<string, number | null> = {}
        for (const p of params) {
          const key = `ma${p}`
          if (pre && pre[p] != null) { out[key] = pre[p]; continue }
          if (i < p - 1) { out[key] = null; continue }
          let s = 0
          for (let j = i - p + 1; j <= i; j++) s += dataList[j].close ?? 0
          out[key] = s / p
        }
        return out
      })
    },
  })
}

// ---------- VOL 副图覆盖内置指标 ----------
// 为什么要覆盖：① 库内 dc_index_market.volume 单位是「手」(采集时 tradingVol/100)，
//   内置 VOL tooltip 显示 417.791M，K/M 格式读不出手/股，且曾误标「股」（2026-10-19
//   用 amount/volume 反推实锤：÷100 后每股 16.4 元合理，按股算 1644 元荒谬）；
// ② 改为「万手」原值 + 关掉 shouldFormatBigNumber，Y 轴与 tooltip 直接可读。
// 覆盖方式：registerIndicator 同名替换内置 VOL，calc 内自算量能 MA5/MA10（万手）。
let volOverrideRegistered = false

function registerVolOverride() {
  if (volOverrideRegistered) return
  volOverrideRegistered = true
  registerIndicator({
    name: 'VOL',
    shortName: 'VOL',
    shouldOhlc: false,
    shouldFormatBigNumber: false,
    precision: 2,
    calcParams: [5, 10],
    figures: [
      {
        key: 'vol', title: '量(万手): ', type: 'bar' as const, baseValue: 0,
        // 红涨绿跌按 close vs open（与内置 getVolumeFigure 同款逻辑，内置的搬不来自带）
        styles: ({ data, indicator, defaultStyles }: any) => {
          const cur = data?.current
          const b = (indicator?.styles?.bars?.[0]) ?? defaultStyles?.bars?.[0] ?? {}
          let color = b.noChangeColor
          if (cur) {
            if (cur.close > cur.open) color = b.upColor
            else if (cur.close < cur.open) color = b.downColor
          }
          return { color }
        },
      },
      { key: 'ma5', title: 'MA5: ', type: 'line' as const },
      { key: 'ma10', title: 'MA10: ', type: 'line' as const },
    ],
    calc: (dataList: KLineData[], indicator: any) => {
      const vols = dataList.map((b) => (b.volume ?? 0) / 1e4)
      const params: number[] = indicator.calcParams ?? [5, 10]
      // ⚠️ figure.styles 拿到的 data.current 是本 calc 的「结果行」而非 K 线数据，
      // 红绿判断需要 open/close，必须一起塞进行里（2026-10-19 实测踩坑）
      return dataList.map((b, i) => {
        const out: Record<string, number | null> = {
          vol: vols[i],
          open: b.open,
          close: b.close,
          // 成交额(亿)：日线与周/月/年聚合根的 amount 均为「元」，统一 ÷1e8
          amount: (b as any).amount != null ? (b as any).amount / 1e8 : null,
        }
        for (const p of params) {
          const key = `ma${p}`
          if (i >= p - 1) {
            let s = 0
            for (let j = i - p + 1; j <= i; j++) s += vols[j]
            out[key] = s / p
          } else {
            out[key] = null
          }
        }
        return out
      })
    },
    // 自定义图例：在「量/MA5/MA10」后追加「额(亿)」（2026-10-24 用户需求）。
    // 为什么不用 figure：klinecharts 单窗格仅一条 Y 轴，金额(亿,千级)与量(万手,万级)量纲差
    // 10 倍+，画实线会贴地失真；且 legend 文字色 = figure 色，透明 figure 会让图例隐身。
    // createTooltipDataSource 会整体替换默认 legends，故 4 项全部自建（含原 3 项的红绿/线色）。
    createTooltipDataSource: ({ chart, indicator, crosshair }: any) => {
      const idx = crosshair?.dataIndex ?? -1
      const row = indicator.result?.[idx] ?? {}
      const fmt = (v: number | null | undefined) =>
        v == null || !isFinite(v) ? '--' : v.toFixed(2)
      // 量能柱红涨绿跌（与 figure.styles 同逻辑）
      let volColor = '#909399'
      if (row.close != null && row.open != null) {
        volColor = row.close > row.open ? '#EF232A' : row.close < row.open ? '#14B143' : '#909399'
      }
      // MA 线色：取全局 indicator.lines 样式（与图上实际画线一致），兜底库默认
      const lines = chart?.getStyles?.()?.indicator?.lines ?? []
      const maColor = (i: number, fallback: string) => lines[i]?.color ?? fallback
      // 成交额金色：资金语义，与「两市额」副图同一强调色（蓝骨金魂纪律）
      const amt = row.amount
      return {
        name: 'VOL',
        calcParamsText: '(5,10)',
        features: [],
        legends: [
          { title: { text: '量(万手): ', color: volColor }, value: { text: fmt(row.vol), color: volColor } },
          { title: { text: 'MA5: ', color: maColor(0, '#FF9600') }, value: { text: fmt(row.ma5), color: maColor(0, '#FF9600') } },
          { title: { text: 'MA10: ', color: maColor(1, '#935EBD') }, value: { text: fmt(row.ma10), color: maColor(1, '#935EBD') } },
          { title: { text: '额(亿): ', color: TWO_AMOUNT_COLOR }, value: { text: amt != null && amt > 0 ? fmt(amt) : '--', color: TWO_AMOUNT_COLOR } },
        ],
      }
    },
  })
}

// ---------- 两市成交额（全市场个股 amount 求和，亿元）自定义副图指标 ----------
// 为什么自定义：成交额量纲（千亿级）与主图指数点位（千点级）冲突，且 v10 主图 pane 只允许
// 一个叠加指标（MA），故做独立副图；时间轴与主图对齐，垂直对照即「量价对比」。
// extendData 注入「交易日字符串 → 两市成交额(元)」映射，calc 按当前 dataList 逐个取。
const TWO_AMOUNT_COLOR = '#D4AF37' // 金色：资金语义 + 项目「金色只做强调」纪律
let twoAmountMap: Record<string, number> = {}
let twoAmountRegistered = false

function registerTwoAmountIndicator() {
  if (twoAmountRegistered) return
  twoAmountRegistered = true
  registerIndicator({
    name: 'TWO_AMOUNT',
    shortName: '两市额',
    shouldOhlc: false,
    // 单位已是「亿」，直接展示原值；不开 shouldFormatBigNumber（那会把
    // tooltip 与 Y 轴一起格式化成 K/M/B，如 14.459K，读不出亿）
    shouldFormatBigNumber: false,
    precision: 0,
    figures: [{ key: 'amount', title: '两市成交额(亿): ', type: 'line' as const }],
    calc: (dataList: KLineData[], indicator: any) => {
      const ext = indicator.extendData as Record<string, number> | undefined
      return dataList.map((b) => {
        const d = dayjs(b.timestamp).format('YYYY-MM-DD')
        const amt = ext?.[d]
        // 映射已是「亿元」原值（market_amount），直接透传
        return { amount: amt != null ? amt : null }
      })
    },
  })
}

// ---------- 支撑/阻力位（分形枢轴点 + 整数关口）自定义指标 ----------
// 算法（A+C 组合，2026-10-20 定稿）：
//   A. 分形枢轴：一根 K 线的 high 高于前后 N(=10) 根 = 潜在阻力；low 低于前后 N 根 = 潜在支撑。
//      聚类去重：同一侧价位按 0.5% 容差合并成一条「位」，命中次数 ≥2 才算有效（被反复触碰过才有意义）。
//   C. 整数关口：叠加最近整数千位关口（如 3700/4000）作灰色参考线。
// 渲染：registerIndicator('SUPPORT_RESIST') + extendData 注入静态价位数组（复用 MA/TWO_AMOUNT 模式），
//   叠加到主图 candle_pane；每条线一个价位，支撑绿/阻力红/关口灰，tooltip 显示「价位 + 触碰次数 + 最近日期」。
type SrLevel = { price: number; type: 'support' | 'resistance' | 'round'; touches: number; lastDate: string }
const SR_N = 10 // 分形检测窗口：前后各 N 根
const SR_CLUSTER_TOL = 0.005 // 聚类容差 0.5%
const SR_MIN_TOUCHES = 2 // 有效位最少命中次数
const SR_MAX_EACH = 3 // 支撑/阻力各最多显示条数（取离现价最近的）
const SR_SUPPORT_COLOR = '#14B143' // 绿 = 支撑
const SR_RESISTANCE_COLOR = '#EF232A' // 红 = 阻力
const SR_ROUND_COLOR = '#9AA4B2' // 灰 = 整数关口

function computeSrLevels(list: KLineData[]): SrLevel[] {
  if (list.length < SR_N * 2 + 1) return []
  const supports: number[] = []
  const resistances: number[] = []
  for (let i = SR_N; i < list.length - SR_N; i++) {
    const b = list[i]
    let isHigh = true
    let isLow = true
    for (let j = i - SR_N; j <= i + SR_N; j++) {
      if (j === i) continue
      if (list[j].high >= b.high) isHigh = false
      if (list[j].low <= b.low) isLow = false
      if (!isHigh && !isLow) break
    }
    if (isHigh) resistances.push(b.high)
    if (isLow) supports.push(b.low)
  }
  // 聚类：同侧价位按容差合并，记录触碰次数与最近日期
  function cluster(prices: number[], type: 'support' | 'resistance'): SrLevel[] {
    const sorted = prices.slice().sort((a, b) => type === 'resistance' ? b - a : a - b)
    const groups: { center: number; sum: number; touches: number }[] = []
    for (const p of sorted) {
      const g = groups.find((x) => Math.abs(x.center - p) / x.center <= SR_CLUSTER_TOL)
      if (g) { g.sum += p; g.touches++; g.center = g.sum / g.touches }
      else groups.push({ center: p, sum: p, touches: 1 })
    }
    return groups
      .filter((g) => g.touches >= SR_MIN_TOUCHES)
      .map((g) => ({ price: g.center, type, touches: g.touches, lastDate: '' }))
  }
  const sLevels = cluster(supports, 'support')
  const rLevels = cluster(resistances, 'resistance')
  // 回填最近触碰日期
  const fillDate = (lv: SrLevel) => {
    let last = 0
    for (const b of list) {
      if (lv.type === 'support') { if (b.low <= lv.price * (1 + SR_CLUSTER_TOL) && b.low >= lv.price * (1 - SR_CLUSTER_TOL)) last = b.timestamp }
      else { if (b.high >= lv.price * (1 - SR_CLUSTER_TOL) && b.high <= lv.price * (1 + SR_CLUSTER_TOL)) last = b.timestamp }
    }
    lv.lastDate = last ? dayjs(last).format('YYYY-MM-DD') : ''
  }
  sLevels.forEach(fillDate)
  rLevels.forEach(fillDate)
  // 取离现价最近的 N 条
  const lastClose = list[list.length - 1].close
  const near = (arr: SrLevel[], dir: 'support' | 'resistance') => {
    return arr
      .filter((l) => (dir === 'support' ? l.price < lastClose : l.price > lastClose))
      .sort((a, b) => (dir === 'support' ? b.price - a.price : a.price - b.price))
      .slice(0, SR_MAX_EACH)
  }
  // 整数关口：现价上下各 2 个整数千位（如现价 3500 → 3000/4000 等）
  const step = Math.pow(10, Math.max(1, Math.floor(Math.log10(lastClose)) - 2)) // 按量级取整（千点级）
  const base = Math.floor(lastClose / step) * step
  const roundLevels: SrLevel[] = []
  for (let k = -2; k <= 2; k++) {
    const p = base + k * step
    if (p <= 0 || Math.abs(p - lastClose) / lastClose < SR_CLUSTER_TOL) continue
    // 关口与已有关键位太近则跳过，避免重叠
    const dup = [...sLevels, ...rLevels].some((l) => Math.abs(l.price - p) / p <= SR_CLUSTER_TOL)
    if (!dup) roundLevels.push({ price: p, type: 'round', touches: 0, lastDate: '' })
  }
  return [...near(sLevels, 'support'), ...near(rLevels, 'resistance'), ...roundLevels].sort((a, b) => b.price - a.price)
}

// 渲染用 overlay（水平直线），不用 indicator：v10 主图 pane 只允许一个叠加指标，
// SR 若 createIndicator 到 candle_pane 会顶掉常驻 MA（2026-10-20 实测：MA 从指标列表消失）。
// overlay 是画布注释层，不占指标位；删除时 removeOverlay() 空参 = 清空全部 overlay（本页无其他 overlay，安全）。
let srMap: SrLevel[] = []

function drawSrOverlays() {
  if (!chart) return
  try { chart.removeOverlay() } catch { /* 忽略 */ }
  if (!showSr.value || !srMap.length) return
  for (const l of srMap) {
    const color = l.type === 'support' ? SR_SUPPORT_COLOR
      : l.type === 'resistance' ? SR_RESISTANCE_COLOR : SR_ROUND_COLOR
    try {
      chart.createOverlay({
        name: 'horizontalStraightLine',
        points: [{ value: l.price }],
        styles: { line: { color, style: 'dashed', size: 1 } },
      } as any)
    } catch { /* 单条失败不影响其余 */ }
  }
}

async function fetchTwoAmountMap(): Promise<Record<string, number>> {
  // 两市成交额：market_style_daily.market_amount（全市场个股 amount 求和，亿元）。
  // ⚠️ 2026-10-19 修正：原用「上证指数 + 深证成指」两个指数 amount 求和，系统性偏低
  // 约 8000 亿（指数只覆盖成分/沪主板，远小于全市场），导致 6 月破 3 万亿被显示成 2.9 万亿。
  const map: Record<string, number> = {}
  const resp: any = await api.get('/market/market-amount', { params: { limit: 10000 } })
  const items: any[] = resp.items || []
  for (const it of items) {
    const d = String(it.trade_date)
    const amt = Number(it.market_amount ?? 0)
    // 后端已按全市场求和，直接取亿元值（无值跳过）
    if (amt) map[d] = amt
  }
  return map
}

async function fetchBars(): Promise<KLineData[]> {
  const params: any = { code: props.code, is_index: true }
  // MA250 预热：多取 250 根前置 K 线（窗口快切时 +250；全史/自定义区间本就是全量，无需额外）
  const isFull = customRange.value || (activeWin.value ?? 0) >= 10000
  if (customRange.value) {
    params.start = dayjs(customRange.value[0]).format('YYYYMMDD')
    params.end = dayjs(customRange.value[1]).format('YYYYMMDD')
    params.limit = 10000
  } else {
    params.limit = Math.min((activeWin.value ?? 250) + 250, 10000)
  }
  const resp: any = await api.get('/market/kline', { params })
  const items = resp.items || []
  const lastRaw = items[items.length - 1]
  latestApiPct = lastRaw && lastRaw.change_pct != null ? Number(lastRaw.change_pct) : null
  const dailyFull: KLineData[] = items.map((it: any) => ({
    timestamp: dayjs(it.trade_date).valueOf(),
    open: Number(it.open), high: Number(it.high),
    low: Number(it.low), close: Number(it.close),
    volume: Number(it.volume),
    changePct: it.change_pct != null ? `${Number(it.change_pct) > 0 ? '+' : ''}${Number(it.change_pct).toFixed(2)}%` : '',
  }))
  // 成交额（元）挂在 bar 上（KLineData 无此字段，用扩展属性；图例显示「成交额」）
  dailyFull.forEach((b, i) => { (b as any).amount = Number(items[i].amount ?? 0) })
  // 先在「全量（含预热）」上聚合出 MA 预计算表，再截出展示窗口
  const agg = period.value === 'day' ? dailyFull : aggregateBars(dailyFull, period.value)
  maMap = computeMaMap(agg)
  srMap = computeSrLevels(agg)
  if (isFull) return agg
  const winDays = activeWin.value ?? 253
  if (period.value === 'day') return agg.slice(-winDays)
  // 周/月/年：展示根数 ≈ 窗口天数折算（周≈/5，月≈/21，年≈/250），与既有窗口语义一致
  const div = period.value === 'week' ? 5 : period.value === 'month' ? 21 : 250
  return agg.slice(-Math.ceil(winDays / div))
}

/** 日线 → 周/月/年 聚合：开=首日开、收=末日收、高=区间最高、低=区间最低、量=求和；涨跌幅相对上一聚合根收盘 */
function aggregateBars(daily: KLineData[], type: 'week' | 'month' | 'year'): KLineData[] {
  if (!daily.length) return []
  const keyOf = (ts: number) => {
    const d = dayjs(ts)
    if (type === 'year') return d.format('YYYY')
    if (type === 'month') return d.format('YYYY-MM')
    // 周：以周一为界（dayjs day() 周日=0；周日归入本周一所在周）
    const day = d.day() === 0 ? 7 : d.day()
    return d.subtract(day - 1, 'day').format('YYYY-MM-DD')
  }
  const groups: { key: string; items: KLineData[] }[] = []
  for (const b of daily) {
    const k = keyOf(b.timestamp)
    const last = groups[groups.length - 1]
    if (last && last.key === k) last.items.push(b)
    else groups.push({ key: k, items: [b] })
  }
  const out: KLineData[] = []
  let prevClose: number | null = null
  for (const g of groups) {
    const first = g.items[0]
    const lastB = g.items[g.items.length - 1]
    const high = Math.max(...g.items.map((b) => b.high))
    const low = Math.min(...g.items.map((b) => b.low))
    const volume = g.items.reduce((s, b) => s + (b.volume || 0), 0)
    const amount = g.items.reduce((s, b) => s + ((b as any).amount || 0), 0)
    let pct = ''
    if (prevClose != null && prevClose !== 0) {
      const v = ((lastB.close - prevClose) / prevClose) * 100
      pct = `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
    }
    const bar: KLineData = { timestamp: lastB.timestamp, open: first.open, high, low, close: lastB.close, volume, changePct: pct }
    ;(bar as any).amount = amount
    out.push(bar)
    prevClose = lastB.close
  }
  return out
}

function setLatest(list: KLineData[]) {
  if (!list.length) { loadingMsg.value = '该区间暂无K线数据'; return }
  const last = list[list.length - 1]
  // 缓存当前区间全量K线，供十字光标联动重算区间涨跌幅；默认起点 = 首根
  barCache = list
  updateRange(null)
  // 周期 > 日 时，聚合根本身已带正确涨跌幅（相对上一聚合根）；日线优先用接口口径
  let pct: number | null = null
  if (period.value !== 'day') {
    pct = last.changePct ? parseFloat(String(last.changePct).replace('%', '')) : null
  } else {
    pct = latestApiPct ?? (list.length > 1 ? ((last.close - list[list.length - 2].close) / list[list.length - 2].close) * 100 : null)
  }
  latest.value = {
    trade_date: dayjs(last.timestamp).format('YYYY-MM-DD'),
    close: last.close,
    changePct: pct,
  }
}

function buildIndicators() {
  if (!chart) return
  ;['MA', 'VOL', 'MACD', 'RSI', 'BOLL', 'TWO_AMOUNT'].forEach((n) => chart!.removeIndicator({ name: n }))
  registerCustomMA()
  // MA 常驻主图（paneId: candle_pane）。注意 v10 主图 pane 只允许一个叠加指标：
  // 若把 BOLL 也放进主图会顶掉 MA（2026-10-18 实测），故 BOLL 走独立副图。
  // per-indicator marginTop=16：拉开 MA 图例与 OHLC 信息行的行距，修复两行文字重叠
  // （全局 indicator.tooltip.marginTop 会波及 BOLL/VOL/MACD 副图，只能按指标单独设）
  chart.createIndicator({
    name: 'MA', paneId: 'candle_pane',
    extendData: maMap,
    styles: {
      // marginTop 需容纳上方 OHLC 图例整行（含「成交量 x,xxx亿」，宽屏单行 ~30px）；
      // 16 是 4 条均线时代的值，MA250 加入后图例更宽，实测与 OHLC 行重叠 → 32
      tooltip: { legend: { marginTop: 32 } },
      lines: MA_PARAMS.map((p) => ({ color: MA_COLORS[p], size: MA_LINE_SIZE[p] })),
    } as any,
  } as any)
  if (showBoll.value) chart.createIndicator('BOLL')
  if (showVol.value) {
    registerVolOverride()
    chart.createIndicator({
      name: 'VOL',
      calcParams: [5, 10],
      styles: { bars: [{ upColor: '#EF232A', downColor: '#14B143' }] } as any,
    } as any)
  }
  if (showMacd.value) chart.createIndicator('MACD')
  if (showRsi.value) chart.createIndicator('RSI')
  // 支撑/阻力位：overlay 水平虚线（不占主图指标位，与 MA 共存）
  drawSrOverlays()
  if (showTwoAmount.value) {
    registerTwoAmountIndicator()
    chart.createIndicator({
      name: 'TWO_AMOUNT',
      extendData: twoAmountMap,
      styles: {
        // 实线：虚线在千根 K 线压缩下会出现「伪缺口」观感（2026-10-19 用户反馈）
        lines: [{ color: TWO_AMOUNT_COLOR, style: 'solid' as any }],
      } as any,
    } as any)
  }
}

function initChart() {
  if (!containerRef.value) return
  if (chart) { dispose(chart); chart = null }
  // barSpaceLimit.min 默认 1px，8746 根全史需 ~0.17px/根，低于默认会被静默拒收 → 放宽到 0.1
  chart = init(containerRef.value, {
    layout: { barSpaceLimit: { min: 0.1 } },
  } as any)
  if (!chart) return
  loadingMsg.value = '加载中...'
  applyTonghuashunStyle(chart)
  // 缩放/平移交互：允许左右拖拽平移（否则缩放后看不到更早的数据），
  // 但用边界限制防止拖出「过去/未来空白」—— v10 的 setMaxOffsetLeft/RightDistance
  // 会把平移钳制在数据范围内（左：0 = 首根K线不能再往左；右：仅留 60px 小边距）。
  try { chart.setScrollEnabled(true) } catch { /* 忽略 */ }
  try {
    // v10 语义：maxOffsetRight 决定「能回看多久」，maxOffsetLeft 决定「能前看多久」。
    // 给足大值（50000px ≈ 数万根K线），实际仍被数据边界钳制，不会拖出大片空白；
    // 若设 0 或小于绘图区宽度，回看会被静默钳住（实测踩坑）。
    chart.setMaxOffsetLeftDistance(50000)
    chart.setMaxOffsetRightDistance(50000)
  } catch { /* 忽略 */ }
  try { chart.setOffsetRightDistance(60) } catch { /* 忽略 */ }
  // 十字光标联动区间涨跌幅：悬停到某根K线 → 以它为起点重算；光标离开 → 恢复默认
  // v10 事件名为 'onCrosshairChange'，但实测 payload 只带 {x,y,paneId}（不带 dataIndex），
  // 故用 convertFromPixel 把 x 反解成 {dataIndex, timestamp}
  chart.subscribeAction('onCrosshairChange', (data: unknown) => {
    const ch = data as { x?: number } | undefined
    const c = chart
    if (ch && typeof ch.x === 'number' && c) {
      try {
        const pt = c.convertFromPixel([{ x: ch.x }]) as { dataIndex?: number } | Array<{ dataIndex?: number }>
        const idx = Array.isArray(pt) ? pt[0]?.dataIndex : pt?.dataIndex
        updateRange(typeof idx === 'number' ? idx : null)
        return
      } catch { /* 反解失败按离开处理 */ }
    }
    updateRange(null)
  })
  ;(window as any).__ekChart = chart
  chart.setDataLoader({
    getBars: ({ type, callback }) => {
      if (type !== 'init' && type !== 'backward' && type !== 'forward') return
      fetchBars().then((list) => {
        setLatest(list); loadingMsg.value = ''
        // MA 预计算表随新数据注入（必须在 callback 触发 applyNewData 重算之前）
        try { chart?.overrideIndicator({ name: 'MA', extendData: maMap } as any) } catch { /* 忽略 */ }
        // 支撑/阻力位随新数据重算并重绘 overlay（若已开启）
        if (showSr.value) {
          srMap = computeSrLevels(list)
          drawSrOverlays()
        }
        // 两市成交额映射随新数据注入（若副图已开启）
        if (showTwoAmount.value) {
          fetchTwoAmountMap().then((m) => {
            twoAmountMap = m
            try { chart?.overrideIndicator({ name: 'TWO_AMOUNT', extendData: m } as any) } catch { /* 忽略 */ }
          }).catch(() => { /* 忽略 */ })
        }
        callback(list, false)
        // ⚠️ fit 必须在 callback（数据进 chart）之后调用：之前会被 applyNewData 重置。
        // 自适应缩放：让所选窗口（含全史 8746 根）一屏可见，而非只显示最后几百根。
        if (type === 'init' && chart && containerRef.value && list.length > 1) {
          const plotW = containerRef.value.clientWidth - 62 - 60 // 预留右侧 y 轴 + offsetRight
          if (plotW > 0) {
            try { chart.setBarSpace(plotW / list.length) } catch { /* 忽略 */ }
          }
        }
      }).catch((e) => {
        loadingMsg.value = `加载失败: ${e?.message || e}`
        console.error('[EnvKLine]', props.code, e)
        callback([], false)
      })
    },
  })
  chart.setSymbol({ ticker: props.code })
  chart.setPeriod({ span: 1, type: period.value })
  // ⚠️ 指标创建必须在 setSymbol/setPeriod 之后：candle_pane 在此之前尚不存在，
  // createIndicator({paneId:'candle_pane'}) 会静默失败（2026-10-18 实测 MA 消失）
  buildIndicators()
  // 容器尺寸变化（含副图增减导致的高度变化）时重算布局
  if (resizeObserver) resizeObserver.disconnect()
  resizeObserver = new ResizeObserver(() => { if (chart) chart.resize() })
  resizeObserver.observe(containerRef.value)
}

function switchWindow(days: number) {
  activeWin.value = days
  if (customRange.value) { customRange.value = null; emit('range-change', null) }
  initChart()
}

function switchPeriod(p: 'day' | 'week' | 'month' | 'year') {
  period.value = p
  initChart()
}

function onCustomRange(v: [number, number] | null) {
  customRange.value = v
  emit('range-change', v)
  if (v) initChart()
}

function toggleIndicator(kind: 'vol' | 'macd' | 'rsi' | 'boll' | 'twoamount' | 'sr') {
  if (kind === 'vol') showVol.value = !showVol.value
  else if (kind === 'macd') showMacd.value = !showMacd.value
  else if (kind === 'rsi') showRsi.value = !showRsi.value
  else if (kind === 'boll') showBoll.value = !showBoll.value
  else if (kind === 'sr') showSr.value = !showSr.value
  else if (kind === 'twoamount') {
    showTwoAmount.value = !showTwoAmount.value
    // 首次开启时若两市额映射为空，先取数再建指标
    if (showTwoAmount.value && Object.keys(twoAmountMap).length === 0) {
      fetchTwoAmountMap().then((m) => { twoAmountMap = m; buildIndicators() }).catch(() => { showTwoAmount.value = false })
      return
    }
  }
  buildIndicators()
}

onMounted(initChart)
onBeforeUnmount(() => {
  if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null }
  if (chart) { dispose(chart); chart = null }
})

watch(
  () => props.code,
  () => { latest.value = null; initChart() },
)
</script>

<template>
  <div class="ek-wrap">
    <div class="ek-toolbar">
      <div class="ek-win">
        <span class="ek-period">
          <span
            v-for="p in (['day', 'week', 'month', 'year'] as const)"
            :key="p"
            class="ek-win-btn"
            :class="{ on: period === p }"
            @click="switchPeriod(p)"
          >{{ { day: '日K', week: '周K', month: '月K', year: '年K' }[p] }}</span>
        </span>
        <span class="ek-sep" />
        <span class="ek-win-label" title="只控制下方 K 线画多少根（取景），不影响任何分位/统计指标">视图取景</span>
        <span
          v-for="w in WINDOWS"
          :key="w.days"
          class="ek-win-btn"
          :class="{ on: !customRange && activeWin === w.days }"
          @click="switchWindow(w.days)"
        >{{ w.label }}</span>
        <NDatePicker
          :value="customRange"
          type="daterange"
          clearable
          size="small"
          style="width: 240px"
          start-placeholder="取景开始"
          end-placeholder="取景结束"
          @update:value="onCustomRange"
        />
      </div>
      <div class="ek-ind">
        <span class="ek-ind-hint">指标</span>
        <span class="ek-ind-tag" :class="{ on: showCandles }" @click="toggleCandles">蜡烛</span>
        <span class="ek-ind-tag" :class="{ on: showBoll }" @click="toggleIndicator('boll')">BOLL</span>
        <span class="ek-ind-tag" :class="{ on: showVol }" @click="toggleIndicator('vol')">VOL</span>
        <span class="ek-ind-tag" :class="{ on: showMacd }" @click="toggleIndicator('macd')">MACD</span>
        <span class="ek-ind-tag" :class="{ on: showRsi }" @click="toggleIndicator('rsi')">RSI</span>
        <span class="ek-ind-tag" :class="{ on: showSr }" @click="toggleIndicator('sr')">支撑</span>
        <span class="ek-ind-tag" :class="{ on: showTwoAmount }" @click="toggleIndicator('twoamount')">两市额</span>
      </div>
    </div>
    <div v-if="loadingMsg" class="ek-loading">{{ loadingMsg }}</div>
    <div v-if="latest && !loadingMsg" class="ek-head">
      <span class="ek-name">{{ name || props.code }}</span>
      <span class="ek-period-tag">{{ { day: '日线', week: '周线', month: '月线', year: '年线' }[period] }}</span>
      <span class="ek-date">{{ latest.trade_date }}</span>
      <span class="ek-close" :class="(latest.changePct ?? 0) >= 0 ? 'up' : 'down'">收 {{ latest.close }}</span>
      <span class="ek-pct" :class="(latest.changePct ?? 0) >= 0 ? 'up' : 'down'">
        {{ latest.changePct != null ? `${latest.changePct >= 0 ? '+' : ''}${latest.changePct.toFixed(2)}%` : '' }}
      </span>
      <span v-if="rangePct != null" class="ek-range" :class="rangePct >= 0 ? 'up' : 'down'">
        区间涨跌幅（{{ rangeLabel }}）：{{ rangePct >= 0 ? '+' : '' }}{{ rangePct.toFixed(2) }}%
      </span>
    </div>
    <div ref="containerRef" class="ek-container" :style="{ height: chartHeight + 'px' }" />
    <div class="ek-foot">滚轮缩放（自动适配全部数据）· 左右拖拽平移（限制在数据范围内）· 十字光标查看 OHLC</div>
  </div>
</template>

<style scoped>
.ek-wrap { position: relative; width: 100%; }
.ek-toolbar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-bottom: 8px; flex-wrap: wrap;
}
.ek-win { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.ek-period { display: inline-flex; gap: 4px; }
.ek-win-label {
  font-size: 12px; font-weight: 500; color: #6B7280; line-height: 1;
  padding: 5px 10px; border-radius: 4px; background: #F3F4F6;
  white-space: nowrap; user-select: none; cursor: help;
}
.ek-sep { width: 1px; height: 16px; background: #DDE3EA; margin: 0 4px; }
.ek-win-btn {
  font-size: 12px; padding: 3px 10px; border-radius: 4px; cursor: pointer;
  border: 1px solid #DDE3EA; color: #6B7280; user-select: none;
}
.ek-win-btn.on { background: #185FA5; border-color: #185FA5; color: #fff; }
.ek-ind { display: flex; gap: 4px; align-items: center; }
.ek-ind-hint { font-size: 12px; color: #909399; }
.ek-ind-tag {
  font-size: 12px; padding: 3px 8px; border-radius: 4px; cursor: pointer;
  border: 1px solid #DDE3EA; color: #909399; user-select: none;
}
.ek-ind-tag.on { background: #E3EDF9; border-color: #185FA5; color: #185FA5; }
.ek-container { width: 100%; }
.ek-loading {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  color: #999; font-size: 13px; z-index: 2; background: rgba(255,255,255,0.85); padding: 8px 16px;
  border-radius: 4px;
}
.ek-head { display: flex; align-items: baseline; gap: 10px; padding: 0 4px 6px; font-size: 13px; flex-wrap: wrap; }
.ek-name { font-size: 15px; font-weight: 600; }
.ek-period-tag { font-size: 11px; color: #185FA5; background: #E3EDF9; padding: 1px 6px; border-radius: 3px; }
.ek-date { color: #888; }
.ek-close { font-weight: 600; }
.ek-range { font-size: 12px; padding: 1px 8px; border-radius: 3px; background: #F5F7FA; }
.ek-tip { font-size: 11px; color: #C0C4CC; margin-left: auto; }
.ek-foot { font-size: 11px; color: #C0C4CC; padding: 4px 4px 0; }
.up { color: #EF232A; }
.down { color: #14B143; }
</style>
