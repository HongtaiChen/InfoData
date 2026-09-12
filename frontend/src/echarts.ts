/**
 * echarts 按需注册（全站共享，use() 幂等可重复调用）。
 * 探查图表（ExploreChart）与后续新图表页统一走这里，避免每页重复罗列。
 */
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, CandlestickChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'

let registered = false

export function ensureEcharts(): void {
  if (registered) return
  use([
    CanvasRenderer,
    BarChart,
    CandlestickChart,
    LineChart,
    ScatterChart,
    GridComponent,
    TooltipComponent,
    LegendComponent,
    DataZoomComponent,
    TitleComponent,
  ])
  registered = true
}

/** 《颜色体系设计规范 v2.0》——K 线/行情红涨绿跌，仅用于行情数字与 K 线 */
export const UP_COLOR = '#EF232A'
export const DOWN_COLOR = '#14B143'

/** 主色蓝（结构/UI/折线主色） */
export const BRAND_COLOR = '#185FA5'

/**
 * 分类色板（折线/散点多系列）。刻意避开纯红/纯绿，
 * 防止与「红涨绿跌」语义混淆；蓝系打头贴合主色。
 */
export const SERIES_PALETTE = [
  '#185FA5', '#1E6FFF', '#0E9F8A', '#7C5CD6', '#D97706',
  '#0284C7', '#4F46E5', '#0D9488', '#9333EA', '#DC6803',
  '#334155', '#B45309', '#6366F1',
]
