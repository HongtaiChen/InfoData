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
      <span>${it.seriesName}：涨${up} 跌${down} 平${flat} · 占比 ${(it.value * 100).toFixed(1)}%</span>
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
    // 计算各窗口滚动占比
    const map: Record<string, (number | null)[]> = {}
    for (const w of WINDOWS) map[w.key] = calcRolling(daily, w.days)
    ratioByWin.value = map
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
        <span class="udr-cal-title">自然区间快照</span>
        <span v-for="c in calSnap" :key="c.label" class="udr-cal-item">
          {{ c.label }}：涨{{ c.up }} 跌{{ c.down }}<template v-if="c.flat"> 平{{ c.flat }}</template>
          · 占比 {{ (c.ratio * 100).toFixed(1) }}%
        </span>
      </div>
      <div class="udr-foot">纵轴（左）= 区间内涨日占比（0~100%），50% 为多空分界；纵轴（右）= 指数价格。悬停查看该日「涨/跌/平」明细与日K线开/收/涨跌。折线用中性色区分窗口长度，日K线红涨绿跌。</div>
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
.udr-cal-item { white-space: nowrap; }
.udr-foot { font-size: 11px; color: #C0C4CC; margin-top: 6px; line-height: 1.5; }
</style>
