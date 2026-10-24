<script setup lang="ts">
/**
 * UpDownRatioTrend —— 涨日占比滚动趋势（多窗口折线 + 日历档快照）
 *
 * 同时回答两个问题：
 *  1. 单窗口随时间的趋势：每条线 = 该窗口「涨日占比」沿交易日的滚动演变
 *     （昨天的近一月、前天的近一月…逐日连成线）。
 *  2. 多窗口横向对比：多条不同长度窗口的线叠在同一图，一眼看出短窗口灵敏、长窗口稳健，
 *     以及哪个窗口当前相对更强。
 *
 * 口径：
 *  - 涨/跌/平按当日 change_pct 符号判定（>0 涨、<0 跌、=0 平，库内权威口径）。
 *  - 涨日占比 = 区间内涨天数 ÷ 区间总交易日数（含平盘），范围 0~1，50% 为多空分界。
 *  - 滚动窗口为「交易日」数（10/20/40/60/120/250 日），非自然日。
 *  - 日历档（本周/本月/本季/本年）为自然区间快照，单独一行文字，不混入滚动图。
 *
 * 配色守「蓝骨金魂」：折线用 SERIES_PALETTE 中性色（避开红绿，避免与涨跌语义混淆）；
 * 红涨绿跌仅出现在「涨/跌天数明细」文字。
 * 数据源：GET /market/kline?is_index=true&limit=10000（全史日线，前端聚合）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
import api from '../../api'

dayjs.extend(quarterOfYear)
import { SERIES_PALETTE } from '../../echarts'
import HistoryCurve, { type CurveSeries, type CandleSeries } from './HistoryCurve.vue'

const props = withDefaults(defineProps<{
  code: string
  name?: string
  // 「所选区间」边界（自然日 YYYY-MM-DD），分位样本池 = 该区间内每日滚动样本（与总览表口径一致）
  rangeStart?: string
  rangeEnd?: string
  rangeLabel?: string
}>(), {})

// 滚动窗口档（交易日数），按短→长排序
const WINDOWS = [
  { key: '10d', label: '近10日', days: 10 },
  { key: '20d', label: '近20日', days: 20 },
  { key: '40d', label: '近40日', days: 40 },
  { key: '60d', label: '近60日', days: 60 },
  { key: '120d', label: '近120日', days: 120 },
  { key: '250d', label: '近250日', days: 250 },
]
const activeKeys = ref<string[]>(['10d', '20d', '40d', '60d'])

const loading = ref(false)
const note = ref('')
const dates = ref<string[]>([])
// 每个窗口的滚动涨日占比序列（0~1，前端用 change_pct 符号聚合）
const ratioByWin = ref<Record<string, (number | null)[]>>({})
// 每个窗口的「区间内滚动样本」排序数组（升序），供分位二分查找（O(log n)/次，避免逐日 O(N²)）
// 口径与总览表一致：样本 = 窗口终点落在所选区间内 且 窗口起点也落在区间内的每日滚动占比
const sortedSamples = ref<Record<string, number[]>>({})

// 分位：v 在升序样本中的排位（(less + 0.5*equal)/n，与后端 pct_rank 同口径）
function pctOf(sorted: number[], v: number): number {
  if (!sorted.length) return NaN
  let lo = 0, hi = sorted.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (sorted[mid] < v) lo = mid + 1; else hi = mid }
  const less = lo
  let eq = lo
  while (eq < sorted.length && sorted[eq] === v) eq++
  return ((less + 0.5 * (eq - less)) / sorted.length) * 100
}

// 某交易日 index 是否落在「所选区间」内（按自然日；未传区间视为全史，恒 true）
function inRange(idx: number): boolean {
  if (!props.rangeStart && !props.rangeEnd) return true
  const d = daily[idx]?.date
  if (!d) return false
  if (props.rangeStart && d < props.rangeStart) return false
  if (props.rangeEnd && d > props.rangeEnd) return false
  return true
}

// 从 daily + 区间重建各窗口分位样本池（区间变化时调用，不重新拉数据）
function rebuildSamples() {
  const smp: Record<string, number[]> = {}
  for (const w of WINDOWS) {
    const arr = ratioByWin.value[w.key] ?? []
    const filtered: number[] = []
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i]
      if (v == null) continue
      // 窗口终点 index=i；窗口起点 index=i-w.days+1（须 >=0 才是有完整窗口的样本）
      const startIdx = i - w.days + 1
      if (startIdx < 0) continue
      if (!inRange(i) || !inRange(startIdx)) continue
      filtered.push(v)
    }
    smp[w.key] = filtered.sort((a, b) => a - b)
  }
  sortedSamples.value = smp
}

// 当前分位样本池标签（快照行展示「区间」用）
const rangeLabelTxt = computed(() => props.rangeLabel || '全史')
// 日历档快照：{ label, up, down, flat, ratio }
const calSnap = ref<{ label: string; up: number; down: number; flat: number; ratio: number }[]>([])

// 原始日线（trade_date 升序 + change_pct + OHLC），供滚动计算、日历档快照与日K线叠加复用
interface DailyBar { date: string; changePct: number; open: number; close: number; low: number; high: number }
let daily: DailyBar[] = []

// 日K线叠加开关（右轴蜡烛图，红涨绿跌，与占比左轴量纲分离，作价格趋势对比）
const showCandle = ref(false)
const candleSeries = ref<CandleSeries | null>(null)

function toggleWin(key: string) {
  const i = activeKeys.value.indexOf(key)
  if (i >= 0) activeKeys.value = activeKeys.value.filter((k) => k !== key)
  else activeKeys.value = [...activeKeys.value, key]
}

function toggleCandle() {
  showCandle.value = !showCandle.value
}

function calcRolling(bars: DailyBar[], days: number): (number | null)[] {
  const out: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = days - 1; i < bars.length; i++) {
    let up = 0
    for (let j = i - days + 1; j <= i; j++) if (bars[j].changePct > 0) up++
    out[i] = up / days
  }
  return out
}

function calcCalSnap(bars: DailyBar[]) {
  if (!bars.length) { calSnap.value = []; return }
  const last = dayjs(bars[bars.length - 1].date)
  const defs: { label: string; start: dayjs.Dayjs }[] = [
    { label: '本周', start: last.startOf('week').add(1, 'day') }, // 周一
    { label: '本月', start: last.startOf('month') },
    { label: '本季', start: last.startOf('quarter') },
    { label: '本年', start: last.startOf('year') },
  ]
  calSnap.value = defs.map((d) => {
    let up = 0, down = 0, flat = 0
    for (const b of bars) {
      if (dayjs(b.date).isBefore(d.start, 'day')) continue
      if (b.changePct > 0) up++
      else if (b.changePct < 0) down++
      else flat++
    }
    const total = up + down + flat
    return { label: d.label, up, down, flat, ratio: total ? up / total : 0 }
  })
}

// 当前（最新交易日）各激活窗口的占比+分位快照（分位=所选区间内同窗口滚动样本排位）
const curSnap = computed(() =>
  activeKeys.value.map((key) => {
    const w = WINDOWS.find((x) => x.key === key)!
    const arr = ratioByWin.value[key] ?? []
    let cur: number | null = null
    let curIdx = -1
    for (let i = arr.length - 1; i >= 0; i--) if (arr[i] != null) { cur = arr[i]!; curIdx = i; break }
    const s = sortedSamples.value[key] ?? []
    // 当前窗口起点须落在区间内，分位才有意义（与总览表口径一致，否则显示 --）
    const startIdx = curIdx - w.days + 1
    const inPool = curIdx >= 0 && startIdx >= 0 && inRange(curIdx) && inRange(startIdx)
    const pct = cur != null && inPool && s.length ? pctOf(s, cur) : NaN
    return { label: w.label, ratio: cur, pct }
  }).filter((x) => x.ratio != null),
)

// 分位配色（与总览表分位规则一致）：<10% 超跌绿 / >90% 超涨红 / 正常灰
function snapCls(p: number): string {
  if (isNaN(p)) return ''
  return p < 10 ? 'udr-snap-low' : p > 90 ? 'udr-snap-high' : ''
}

const series = computed<CurveSeries[]>(() =>
  activeKeys.value.map((key, i) => {
    const w = WINDOWS.find((x) => x.key === key)!
    return {
      name: w.label,
      values: ratioByWin.value[key] ?? [],
      color: SERIES_PALETTE[i % SERIES_PALETTE.length],
    }
  }),
)

const candleProp = computed(() => (showCandle.value && candleSeries.value ? candleSeries.value : undefined))

// tooltip 分位文案：悬停日占比在所选区间同窗口滚动样本中的排位；<10% 超跌绿 / >90% 超涨红
// 悬停日窗口起点落在区间内才显示分位（与总览表口径一致），否则显示 --
function pctTxt(v: number, key: string, dataIndex: number): string {
  const s = sortedSamples.value[key]
  const w = WINDOWS.find((x) => x.key === key)
  if (!s || !s.length || v == null || !w) return ''
  const startIdx = dataIndex - w.days + 1
  const inPool = startIdx >= 0 && inRange(dataIndex) && inRange(startIdx)
  if (!inPool) return ' · <span style="color:#C0C4CC;">分位 --</span>'
  const p = pctOf(s, v)
  const color = p < 10 ? '#14B143' : p > 90 ? '#EF232A' : '#6B7280'
  return ` · <span style="color:${color};font-weight:600;">分位 ${p.toFixed(0)}%</span>`
}

const tooltipFormatter = (params: any) => {
  const p = Array.isArray(params) ? params[0] : params
  if (!p) return ''
  const date = p.axisValueLabel ?? ''
  let head = `<div style="font-weight:600;margin-bottom:4px;">${date}</div>`
  for (const it of (Array.isArray(params) ? params : [params])) {
    if (it.value == null) continue
    // 日K线（蜡烛图）序列：echarts candlestick 的 tooltip value = [dataIndex, open, close, low, high]
    if (it.seriesName === '日K线') {
      const v = it.value as number[]
      const day = daily[it.dataIndex]
      const open = v[1] ?? day?.open ?? 0
      const close = v[2] ?? day?.close ?? 0
      head += `<div style="display:flex;align-items:center;gap:6px;">
        <span style="width:8px;height:8px;border-radius:2px;background:${day && day.changePct >= 0 ? '#EF232A' : '#14B143'};display:inline-block;"></span>
        <span>日K线：开 ${Number(open).toFixed(2)} 收 ${Number(close).toFixed(2)} · 日涨跌 ${day ? `${day.changePct >= 0 ? '+' : ''}${day.changePct.toFixed(2)}%` : '--'}</span>
      </div>`
      continue
    }
    const key = activeKeys.value[it.seriesIndex]
    const w = WINDOWS.find((x) => x.key === key)
    if (!w) continue
    // 从当日往前数 w.days 个交易日，统计涨/跌/平
    const startIdx = it.dataIndex - w.days + 1
    let up = 0, down = 0, flat = 0
    for (let j = Math.max(0, startIdx); j <= it.dataIndex; j++) {
      const b = daily[j]
      if (!b) continue
      if (b.changePct > 0) up++
      else if (b.changePct < 0) down++
      else flat++
    }
    head += `<div style="display:flex;align-items:center;gap:6px;">
      <span style="width:8px;height:8px;border-radius:2px;background:${it.color};display:inline-block;"></span>
      <span>${it.seriesName}：涨${up} 跌${down} 平${flat} · 占比 ${(it.value * 100).toFixed(1)}%${pctTxt(it.value, key, it.dataIndex)}</span>
    </div>`
  }
  return head
}

async function load() {
  loading.value = true
  note.value = ''
  try {
    const resp: any = await api.get('/market/kline', {
      params: { code: props.code, is_index: true, limit: 10000 },
    })
    const items: any[] = resp.items || []
    daily = items.map((it) => ({
      date: String(it.trade_date),
      changePct: Number(it.change_pct ?? 0),
      open: Number(it.open ?? 0), close: Number(it.close ?? 0),
      low: Number(it.low ?? 0), high: Number(it.high ?? 0),
    }))
    if (!daily.length) { note.value = '该指数暂无涨跌数据'; return }
    dates.value = daily.map((b) => b.date)
    // 计算各窗口滚动占比（样本池由 rebuildSamples 按区间过滤后构建）
    const map: Record<string, (number | null)[]> = {}
    for (const w of WINDOWS) map[w.key] = calcRolling(daily, w.days)
    ratioByWin.value = map
    rebuildSamples()
    calcCalSnap(daily)
    // 日K线蜡烛数据（[open, close, low, high]）
    candleSeries.value = daily.every((b) => b.open && b.close)
      ? { name: '日K线', data: daily.map((b) => [b.open, b.close, b.low, b.high] as [number, number, number, number]) }
      : null
  } catch (e: any) {
    note.value = '加载涨跌天数趋势失败'
  } finally {
    loading.value = false
  }
}

watch(() => props.code, load)
// 区间变化时只重建分位样本池（daily 已全量，不重新拉数据）
watch(() => [props.rangeStart, props.rangeEnd, props.rangeLabel], rebuildSamples)
onMounted(load)
</script>

<template>
  <div class="udr-wrap">
    <div class="udr-toolbar">
      <span class="udr-title">涨日占比滚动趋势<span v-if="props.name" class="udr-sub"> · {{ props.name }}</span></span>
      <div class="udr-chips">
        <span
          v-for="w in WINDOWS"
          :key="w.key"
          class="udr-chip"
          :class="{ on: activeKeys.includes(w.key) }"
          @click="toggleWin(w.key)"
        >{{ w.label }}</span>
        <span class="udr-sep" />
        <span class="udr-chip udr-chip-cum" :class="{ on: showCandle }" @click="toggleCandle">日K线</span>
      </div>
    </div>

    <div v-if="loading" class="udr-loading">加载中...</div>
    <div v-else-if="note" class="udr-note">{{ note }}</div>
    <template v-else>
      <HistoryCurve
        :dates="dates"
        :series="series"
        height="280px"
        zoom
        :zoom-start="97"
        :zoom-end="100"
        :ref-lines="[{ value: 0.5, label: '50%', color: '#B45309' }]"
        :y-format="''"
        :tooltip-formatter="tooltipFormatter"
        :candle="candleProp"
      />
      <div class="udr-cal">
        <span class="udr-cal-title">滚动窗口快照</span>
        <span class="udr-cal-pool">分位样本池 = 总览表「分位样本池区间」（{{ rangeLabelTxt }}）</span>
        <span v-for="c in curSnap" :key="c.label" class="udr-cal-item">
          {{ c.label }}：占比 {{ (c.ratio! * 100).toFixed(1) }}%
          · <span :class="snapCls(c.pct)" class="udr-snap-pct">分位 {{ isNaN(c.pct) ? '--' : c.pct.toFixed(0) + '%' }}</span>
        </span>
      </div>
      <div class="udr-cal">
        <span class="udr-cal-title">自然区间快照</span>
        <span v-for="c in calSnap" :key="c.label" class="udr-cal-item">
          {{ c.label }}：涨{{ c.up }} 跌{{ c.down }}<template v-if="c.flat"> 平{{ c.flat }}</template>
          · 占比 {{ (c.ratio * 100).toFixed(1) }}%
        </span>
      </div>
      <div class="udr-foot">纵轴（左）= 区间内涨日占比（0~100%），50% 为多空分界；分位 = 该日占比在所选区间内同窗口滚动样本中的排位（样本=窗口起点与终点均落在区间内的每日滚动占比，&lt;10% 绿=超跌、&gt;90% 红=超涨，区间装不下窗口或样本不足时显示 --）；纵轴（右）= 指数价格。悬停查看该日「涨/跌/平」明细、占比分位与日K线开/收/涨跌。折线用中性色区分窗口长度，日K线红涨绿跌。</div>
    </template>
  </div>
</template>

<style scoped>
.udr-wrap { width: 100%; }
.udr-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }
.udr-title { font-size: 14px; font-weight: 600; color: #1F2937; }
.udr-sub { font-weight: 600; color: #1F2937; margin-left: 4px; }
.udr-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.udr-chip {
  font-size: 12px; padding: 3px 10px; border-radius: 4px; cursor: pointer;
  border: 1px solid #DDE3EA; color: #909399; user-select: none;
}
.udr-chip.on { background: #E3EDF9; border-color: #185FA5; color: #185FA5; }
.udr-sep { width: 1px; height: 18px; background: #DDE3EA; margin: 0 2px; }
.udr-chip-cum { border-color: #F09595; color: #A32D2D; }
.udr-chip-cum.on { background: #FCEBEB; border-color: #EF232A; color: #EF232A; }
.udr-loading, .udr-note { padding: 24px 0; text-align: center; color: #909399; font-size: 13px; }
.udr-cal { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 10px; font-size: 12px; color: #374151; }
.udr-cal-title { font-weight: 600; color: #1F2937; }
.udr-cal-pool { font-size: 11px; color: #9CA3AF; }
.udr-cal-item { white-space: nowrap; }
.udr-snap-pct { font-weight: 600; }
.udr-snap-pct.udr-snap-low { color: #14B143; }
.udr-snap-pct.udr-snap-high { color: #EF232A; }
.udr-foot { font-size: 11px; color: #C0C4CC; margin-top: 6px; line-height: 1.5; }
</style>
