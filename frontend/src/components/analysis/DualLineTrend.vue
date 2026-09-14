<script setup lang="ts">
/**
 * DualLineTrend —— 双线时序图（echarts），风格轮动监测的标准形态
 * 适用：剪刀差、风偏分数等有正负摆动的时序指标（自带零轴）
 */
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, MarkLineComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ensureEcharts } from '../../echarts'

ensureEcharts()
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent])

export interface TrendSeries {
  name: string
  values: (number | null)[]
  color: string
}

const props = defineProps<{
  dates: string[]
  series: TrendSeries[]
  height?: string
}>()

const option = computed(() => ({
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
    axisLabel: { color: '#9CA3AF' },
    splitLine: { lineStyle: { color: '#EDEFF2' } },
  },
  series: props.series.map((s) => ({
    name: s.name,
    type: 'line',
    data: s.values,
    symbol: 'none',
    lineStyle: { width: 1.6, color: s.color },
    itemStyle: { color: s.color },
    markLine: {
      silent: true,
      symbol: 'none',
      label: { show: false },
      lineStyle: { color: '#9CA3AF', type: 'dashed', width: 1 },
      data: [{ yAxis: 0 }],
    },
  })),
}))
</script>

<template>
  <VChart :option="option" :style="{ height: props.height ?? '300px', width: '100%' }" autoresize />
</template>
