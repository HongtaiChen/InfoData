/**
 * echarts 按需注册（全站共享，use() 幂等可重复调用）。
 * 探查图表（ExploreChart）与后续新图表页统一走这里，避免每页重复罗列。
 */
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, CandlestickChart, LineChart, PieChart, ScatterChart } from 'echarts/charts'
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
    PieChart,
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
 * 分类色板（折线/散点多系列，最多 12 组后循环）。
 * 设计原则：
 * 1. 刻意避开纯红/纯绿（防止与「红涨绿跌」语义混淆）
 * 2. 色相 × 明度双维度拉开：蓝/橙/紫/青/粉/金/棕/深navy/品红/石板灰/深紫/深青/橄榄
 *    相邻两色在色相或明度至少一维上差异显著，11 组同图仍可分辨
 */
export const SERIES_PALETTE = [
  '#2D6CDF', // 蓝
  '#F28C28', // 橙
  '#8452D6', // 紫
  '#0FA3C6', // 青
  '#E8618C', // 粉
  '#D9A50F', // 金
  '#8C6239', // 棕
  '#22335C', // 深navy
  '#C33FA5', // 品红
  '#8A99AD', // 石板灰蓝
  '#5B2C8D', // 深紫
  '#0E7C7B', // 深青
  '#9BA82C', // 橄榄
]
