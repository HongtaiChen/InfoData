<script setup lang="ts">
/**
 * DualLineTrend —— 双线时序图（echarts），风格轮动监测的标准形态
 * 适用：剪刀差、风偏分数等有正负摆动的时序指标（自带零轴）
 *
 * bands（2026-09-14 新增）：把「剪刀差正负」渲染成区间底色带，用于一眼看出
 * 「小盘占优 / 大盘占优分别持续了多久」。曲线本身很难数出区间长度，底色带可以。
 * ⚠️ 配色纪律：区间代表「风格占优方」而非涨跌，故**不用红绿**——
 *   小盘占优用金 rgba(201,162,39,.09)（进攻侧）、大盘占优用主色蓝 rgba(24,95,165,.09)（防守侧）。
 *
 * refLines / yMin（P4 · 2026-09-27 新增，R13-2／R13-3）：
 *   · `refLines` —— 水平参考线（如「3M 近一年中位」）。**值是调用方从后端字段透传的**，
 *     本组件不计算任何指标（避免"图上一条线、参数区一个数"两个真源）。
 *   · `yMin` —— 显式纵轴下限，用于**截断轴**（利率水平类图的波动带很窄，0 起会压平信号）。
 *     两者**都是可选**：不传 = 原行为（echarts 自动轴 + 只有零轴虚线）⇒ 另 4 个调用方零影响。
 */
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent, LegendComponent, TooltipComponent, MarkLineComponent, MarkAreaComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ensureEcharts } from '../../echarts'

ensureEcharts()
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent,
     MarkLineComponent, MarkAreaComponent])

export interface TrendSeries {
  name: string
  values: (number | null)[]
  color: string
}
export interface TrendBand {
  side: number        // 1 = 小盘占优，-1 = 大盘占优
  start: string
  end: string
  days?: number
  label?: string
}
/** 水平参考线（P4 · R13-2）。⚠️ `value` 必须取自后端**已算出的字段**（如 `median_1y`），
 *  前端不得为了画线再算一遍中位数 —— 那会造出第二个真源。 */
export interface TrendRefLine {
  value: number
  label?: string
}

const props = defineProps<{
  dates: string[]
  series: TrendSeries[]
  height?: string
  bands?: TrendBand[]
  refLines?: TrendRefLine[]
  /** 显式纵轴下限（P4 · R13-3 截断轴）。**不传即为原来的 echarts 自动值** ⇒ 其他调用方行为不变。 */
  yMin?: number
}>()

const option = computed(() => {
  const bands = props.bands ?? []
  // markArea 挂在第一条线上（同一个坐标系，挂一处即可）
  const markArea = bands.length
    ? {
        silent: true,
        data: bands.map((b) => [
          {
            xAxis: b.start,
            itemStyle: { color: b.side > 0 ? 'rgba(201,162,39,.09)' : 'rgba(24,95,165,.09)' },
            label: {
              show: (b.days ?? 0) >= 20,
              position: 'insideTop',
              formatter: b.label ?? '',
              color: b.side > 0 ? '#8A6D12' : '#185FA5',
              fontSize: 10,
            },
          },
          { xAxis: b.end },
        ]),
      }
    : undefined

  // ── markLine（P4 · 2026-09-27 · R13-2／R13-3）────────────────────────────────
  // ① **去重**：原来**每条序列各挂一份** `[{ yAxis: 0 }]` ⇒ 3 条零轴虚线像素级重叠，
  //    纯属白画（与 markArea 是同一个坐标系，挂一处即可）。现改为只挂第 1 条。
  // ② **参考线并入同一 markLine**：值由调用方从后端字段透传（本组件**不计算任何指标**）。
  // ③ **配色守「蓝骨金魂」**：基准线属**结构**，不用金色（金色专留 AI 洞察 / 策略收益 /
  //    亮点数字）；取蓝 400 `#378ADD` 虚线 —— 与常用三条序列色（#185FA5 深 / #7FA9CD 浅 /
  //    #9CA3AF 灰）都不同相，也与零轴的灰虚线区分得开。
  // ④ **截断轴时不挂零轴**：`yMin > 0` 意味着 0 已在轴外（mc-trend 的利率水平图就是这样），
  //    挂上去要么被裁、要么贴着网格边溢出 ⇒ 干脆不挂。不传 `yMin` 的调用方（剪刀差等
  //    正负摆动型指标）行为完全不变，零轴照旧。
  const refLines = props.refLines ?? []
  const canDrawZero = props.yMin == null || props.yMin <= 0
  const markLine = {
    silent: true,
    symbol: 'none',
    lineStyle: { type: 'dashed' as const, width: 1 },
    label: { show: false },
    data: [
      ...(canDrawZero
        ? [{ yAxis: 0, lineStyle: { color: '#9CA3AF', type: 'dashed' as const, width: 1 } }]
        : []),
      ...refLines.map((rl) => ({
        yAxis: rl.value,
        lineStyle: { color: '#378ADD', type: 'dashed' as const, width: 1 },
        label: {
          show: true,
          // 🔴 `position: 'end'` 会把标签画到**网格右边界之外**被裁掉 —— P4 截图实测：
          //    1450px 宽的图上右端只剩「3M…」，而 `grid.right` 只有 16px。
          //    ⇒ 改用 `insideEndTop`：贴在网格**内侧**右端、线的上方，永不出界。
          //    （其余可选位置如 'start'/'insideStartTop' 同理受 `grid.left: 48` 限制，也不稳。）
          position: 'insideEndTop' as const,
          formatter: rl.label ?? String(rl.value),
          color: '#185FA5',
          fontSize: 10,
        },
      })),
    ],
  }

  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { color: '#6B7280' } },
    grid: { left: 48, right: 16, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: props.dates,
      axisLine: { lineStyle: { color: '#D4D7DE' } },
      axisLabel: { color: '#9CA3AF', formatter: (v: string) => v.slice(5) },
    },
    yAxis: {
      type: 'value',
      // R13-3：显式下限（截断轴）。**不传 = 原自动值** ⇒ 其他调用方零影响。
      ...(props.yMin != null ? { min: props.yMin } : {}),
      axisLabel: { color: '#9CA3AF' },
      splitLine: { lineStyle: { color: '#EDEFF2' } },
    },
    series: props.series.map((s, i) => ({
      name: s.name,
      type: 'line',
      data: s.values,
      symbol: 'none',
      lineStyle: { width: 1.6, color: s.color },
      itemStyle: { color: s.color },
      markArea: i === 0 ? markArea : undefined,
      markLine: i === 0 ? markLine : undefined,
    })),
  }
})
</script>

<template>
  <VChart :option="option" :style="{ height: props.height ?? '300px', width: '100%' }" autoresize />
</template>
