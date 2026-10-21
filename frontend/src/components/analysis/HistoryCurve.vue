<script setup lang="ts">
/**
 * HistoryCurve —— 投资环境通用历史曲线（echarts）
 * 一条主曲线（可选叠加第二曲线，如均线），可带分位带（历史分位序列）与水平参考线。
 * 配色守「蓝骨金魂」：主曲线蓝、参考线蓝虚线、分位极值区金色提示；红涨绿跌不在此组件（行情语义单独处理）。
 */
import { computed, nextTick, ref, watch } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart, CandlestickChart } from 'echarts/charts'
import {
  GridComponent, LegendComponent, TooltipComponent, MarkLineComponent, DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ensureEcharts } from '../../echarts'

ensureEcharts()
use([CanvasRenderer, LineChart, CandlestickChart, GridComponent, TooltipComponent, LegendComponent,
     MarkLineComponent, DataZoomComponent])

export interface CurveSeries {
  name: string
  values: (number | null)[]
  color?: string
  /** 是否用右侧轴（用于量纲不同的叠加曲线，如价格 vs 均线偏离） */
  yAxisIndex?: 0 | 1
  /** 虚线（用于量纲/语义不同的叠加线，如累计涨跌幅） */
  dashed?: boolean
}

/** 蜡烛图叠加序列（如日K线）：[open, close, low, high]，echarts candlestick 顺序 */
export interface CandleSeries {
  name: string
  data: [number, number, number, number][]
  yAxisIndex?: 0 | 1
}

const props = withDefaults(defineProps<{
  dates: string[]
  series: CurveSeries[]
  height?: string
  /** 水平参考线（如 RSI 的 70/30、Beta 的 1） */
  refLines?: { value: number; label?: string; color?: string }[]
  /** 是否显示 dataZoom（长历史可缩放） */
  zoom?: boolean
  /** 纵轴格式化（如 '%'） */
  yFormat?: string
  /** 纵轴是否不强制含零（对比曲线等场景，让波动区间占满图面） */
  yScale?: boolean
  /** 是否隐藏图例 */
  hideLegend?: boolean
  /** dataZoom 初始窗口（百分比 0~100），仅 zoom 时生效；默认全区间 */
  zoomStart?: number
  zoomEnd?: number
  /** 自定义悬停提示（html）。参数为 echarts tooltip params 数组；未传时用默认数值格式 */
  tooltipFormatter?: (params: any) => string
  /** 蜡烛图叠加（如日K线，右轴红涨绿跌） */
  candle?: CandleSeries
}>(), {
  height: '260px',
  zoom: false,
  hideLegend: false,
  yScale: false,
  zoomStart: 0,
  zoomEnd: 100,
})

// —— 蜡烛图「可见区间最高/最低」动态标注（随 dataZoom 联动，2026-10-19 用户需求） ——
const chartRef = ref<any>(null)
const candleMark = ref<{ maxIdx: number; maxVal: number; minIdx: number; minVal: number } | null>(null)
// 初始视野：数据（长度+首日）变化后用 dispatchAction 重发一次；同一份数据不重发（避免重置用户缩放）
let lastDataKey = ''

function applyInitialZoom() {
  const ch = chartRef.value?.chart
  if (!ch || !props.zoom || !props.dates.length) return
  const key = `${props.dates.length}:${props.dates[0]}`
  if (key === lastDataKey) return
  lastDataKey = key
  // ⚠️ 必须延迟到 vue-echarts 自身的数据更新 setOption 之后：同一 nextTick 内先 dispatch
  // 会被随后到的新数据 setOption 覆盖回全区间（实测踩坑），100ms 足够稳定
  setTimeout(() => {
    try { ch.dispatchAction({ type: 'dataZoom', start: props.zoomStart, end: props.zoomEnd }) } catch { /* 忽略 */ }
  }, 100)
}

function updateCandleMark() {
  const cd = props.candle
  if (!cd || !cd.data.length) { candleMark.value = null; return }
  const len = cd.data.length
  const ch = chartRef.value?.chart
  let s = 0, e = len - 1
  try {
    const dz = ch?.getOption?.()?.dataZoom?.[0]
    if (dz) {
      // start/end 可能是百分比或 startValue/endValue 索引，两者都兼容
      s = dz.startValue != null ? Number(dz.startValue) : Math.round(((dz.start ?? 0) / 100) * (len - 1))
      e = dz.endValue != null ? Number(dz.endValue) : Math.round(((dz.end ?? 100) / 100) * (len - 1))
    }
  } catch { /* 读不到缩放状态就按全区间算 */ }
  let maxIdx = -1, maxVal = -Infinity, minIdx = -1, minVal = Infinity
  for (let i = Math.max(0, Math.floor(s)); i <= Math.min(len - 1, Math.ceil(e)); i++) {
    const low = cd.data[i][2], high = cd.data[i][3]
    if (high > maxVal) { maxVal = high; maxIdx = i }
    if (low < minVal) { minVal = low; minIdx = i }
  }
  candleMark.value = maxIdx >= 0 ? { maxIdx, maxVal, minIdx, minVal } : null
}

watch(() => props.candle, () => {
  nextTick(() => {
    applyInitialZoom()
    updateCandleMark()
    try { chartRef.value?.chart?.on?.('datazoom', updateCandleMark) } catch { /* 忽略 */ }
  })
}, { immediate: true })
// 数据变化（切指数/加载完成）也要重发初始视野并重算标注
watch(() => props.dates.length, () => {
  nextTick(() => {
    applyInitialZoom()
    updateCandleMark()
  })
})

const option = computed(() => {
  const hasRightAxis = props.series.some((s) => s.yAxisIndex === 1) || !!props.candle
  // 跨年时 X 轴标签带年份（YYYY-MM），单年内保持 MM-DD（2026-10-18 用户反馈）；
  // 相邻刻度同月/同日时去重（formatter 按序回调，跳过与前一个相同的标签）
  const years = new Set(props.dates.map((d) => d.slice(0, 4)))
  const crossYear = years.size > 1
  let lastLabel = ''
  const xFmt = (v: string) => {
    const label = crossYear ? v.slice(0, 7) : v.slice(5)
    if (label === lastLabel) return ''
    lastLabel = label
    return label
  }
  return {
    tooltip: {
      trigger: 'axis',
      ...(props.tooltipFormatter
        ? { formatter: props.tooltipFormatter }
        : { valueFormatter: (v: any) => (v == null ? '--' : props.yFormat ? `${Number(v).toFixed(2)}${props.yFormat}` : v) }),
    },
    legend: props.hideLegend
      ? undefined
      : { top: 0, textStyle: { color: '#6B7280' }, data: [...props.series.map((s) => s.name), ...(props.candle ? [props.candle.name] : [])] },
    grid: { left: 52, right: hasRightAxis ? 52 : 20, top: props.hideLegend ? 12 : 32, bottom: props.zoom ? 46 : 28 },
    xAxis: {
      type: 'category',
      data: props.dates,
      axisLine: { lineStyle: { color: '#D4D7DE' } },
      axisLabel: { color: '#9CA3AF', formatter: xFmt },
    },
    yAxis: [
      {
        type: 'value',
        scale: props.yScale,
        axisLabel: { color: '#9CA3AF', formatter: (v: number) => (props.yFormat ? `${v}${props.yFormat}` : String(v)) },
        splitLine: { lineStyle: { color: '#EDEFF2' } },
      },
      ...(hasRightAxis
        ? [{
            type: 'value' as const,
            scale: true,
            axisLabel: { color: '#9CA3AF', formatter: (v: number) => (props.yFormat ? `${v}${props.yFormat}` : String(v)) },
            splitLine: { show: false },
          }]
        : []),
    ],
    dataZoom: props.zoom
      ? [
          // 🔴 初始视野不用 option 携带 start/end（setOption 合并 + 数据换长后内部 startValue
          // 越界被钳制成全区间，且每次 setOption 携带会重置用户缩放）——改用 dispatchAction
          // 在数据就绪后下发（见 watch props.candle），实测必生效
          { type: 'inside' as const },
          { type: 'slider' as const, height: 18, bottom: 6 },
        ]
      : undefined,
    series: [
      ...props.series.map((s, i) => ({
      name: s.name,
      type: 'line' as const,
      data: s.values,
      symbol: 'none',
      smooth: false,
      yAxisIndex: s.yAxisIndex ?? 0,
      lineStyle: { width: s.dashed ? 1.6 : 1.6, color: s.color ?? '#185FA5', type: s.dashed ? 'dashed' as const : 'solid' as const },
      itemStyle: { color: s.color ?? '#185FA5' },
      connectNulls: true,
      markLine: i === 0 && props.refLines?.length
        ? {
            silent: true,
            symbol: 'none',
            lineStyle: { type: 'dashed' as const, width: 1 },
            label: { show: true, position: 'insideEndTop' as const, fontSize: 10, color: '#185FA5' },
            data: props.refLines.map((rl) => ({
              yAxis: rl.value,
              lineStyle: { color: rl.color ?? '#378ADD' },
              label: { formatter: rl.label ?? String(rl.value) },
            })),
          }
        : undefined,
      })),
      // 蜡烛图叠加（如日K线）：红涨绿跌（A股口径），右轴
      ...(props.candle
        ? [{
            name: props.candle.name,
            type: 'candlestick' as const,
            data: props.candle.data,
            yAxisIndex: props.candle.yAxisIndex ?? 1,
            barWidth: '70%' as const,
            itemStyle: {
              color: '#EF232A', color0: '#14B143',
              borderColor: '#EF232A', borderColor0: '#14B143',
            },
          }]
        : []),
      // 可见区间最高/最低点标注（随缩放联动）：独立 scatter 序列（pin 造型）
      // ⚠️ 不用 markPoint：dataZoom filterMode=filter 会过滤窗外数据并重排索引，
      // markPoint 用索引 coord 会错位丢失（2026-10-19 实测踩坑）；coord 一律用日期字符串
      ...(props.candle && candleMark.value
        ? [{
            name: '__extremes__',
            type: 'scatter' as const,
            yAxisIndex: props.candle.yAxisIndex ?? 1,
            silent: true,
            z: 20,
            tooltip: { show: false },
            data: [
              {
                value: [props.dates[candleMark.value.maxIdx], candleMark.value.maxVal],
                symbol: 'pin', symbolSize: 46,
                itemStyle: { color: '#EF232A' },
                label: { show: true, formatter: () => String(Math.round(candleMark.value!.maxVal)), color: '#fff', fontSize: 12, fontWeight: 'bold' as const },
              },
              {
                value: [props.dates[candleMark.value.minIdx], candleMark.value.minVal],
                symbol: 'pin', symbolSize: 46,
                itemStyle: { color: '#14B143' },
                label: { show: true, formatter: () => String(Math.round(candleMark.value!.minVal)), color: '#fff', fontSize: 12, fontWeight: 'bold' as const },
              },
            ],
          }]
        : []),
    ],
  }
})
</script>

<template>
  <VChart ref="chartRef" :option="option" :style="{ height: props.height, width: '100%' }" autoresize @datazoom="updateCandleMark" />
</template>
