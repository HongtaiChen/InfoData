<script setup lang="ts">
/**
 * DualLineTrend —— 双线时序图（echarts），风格轮动监测的标准形态
 * 适用：剪刀差、风偏分数等有正负摆动的时序指标（自带零轴）
 *
 * bands（2026-09-14 新增）：把「剪刀差正负」渲染成区间底色带，用于一眼看出
 * 「小盘占优 / 大盘占优分别持续了多久」。曲线本身很难数出区间长度，底色带可以。
 * ⚠️ 配色纪律：区间代表「风格占优方」而非涨跌，故**不用红绿**——
 *   小盘占优用金 rgba(201,162,39,.09)（进攻侧）、大盘占优用主色蓝 rgba(24,95,165,.09)（防守侧）。
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

const props = defineProps<{
  dates: string[]
  series: TrendSeries[]
  height?: string
  bands?: TrendBand[]
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
      markLine: {
        silent: true,
        symbol: 'none',
        label: { show: false },
        lineStyle: { color: '#9CA3AF', type: 'dashed', width: 1 },
        data: [{ yAxis: 0 }],
      },
    })),
  }
})
</script>

<template>
  <VChart :option="option" :style="{ height: props.height ?? '300px', width: '100%' }" autoresize />
</template>
