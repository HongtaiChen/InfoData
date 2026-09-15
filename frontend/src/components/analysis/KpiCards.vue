<script setup lang="ts">
/**
 * KpiCards —— 分析模块结论区（AnalysisShell §3）
 * 结论先行：大数字 + 状态词 + 口径 tooltip；红涨绿跌仅用于行情语义数值（tone='updown'）
 *
 * 分位与极值（2026-09-14 新增）：
 * - pct = 当前值在近 250 个交易日中的分位（0=区间最低，100=最高）。
 *   绝对 pp 跨期不可比（2005 年的 5pp 与现在的 5pp 意义不同），只有分位才能回答「这是不是极端」。
 * - highlight = 分位进入极值区（<=10 / >=90）→ 左侧金色竖条 + 分位文字金色。
 *   ⚠️ 金色只做「极值标记」，数值颜色仍严格走红涨绿跌——不拿金色去染涨跌数字，避免破坏 A 股铁律。
 * - anchor = 页内锚点 id：点卡片平滑滚到对应图表（结论 → 论据的下钻）。
 *
 * 风险调整（2026-09-15 新增）：
 * - adj = 收益差 ÷ 其自身近 250 日滚动标准差（σ 倍数）。与 pct 分工不同：
 *   pct 答「在近一年排第几」（纯相对排位，受区间选择影响），adj 答「偏离自身风险尺度几个单位」
 *   （含幅度、可跨期比较）。两者并列展示，互为参照。
 * - 配色：adj 不是涨跌语义 → 用次要文字色，绝不走红绿。
 */
import { NTooltip } from 'naive-ui'

interface Kpi {
  key: string
  label: string
  value: number | null
  unit?: string
  status?: string
  hint?: string
  // 后端必须显式给出 tone：updown=红正绿负（行情语义），neutral=主色（分位/占比等非涨跌语义）
  // 缺失时按 updown 兜底，避免老接口把涨跌指标渲染成主色
  tone?: 'updown' | 'neutral'
  pct?: number | null
  highlight?: boolean
  anchor?: string
  // 风险调整值（σ 倍数）：仅风偏分数与大小盘剪刀差有，其余为 undefined
  adj?: number | null
}

const props = defineProps<{ items: Kpi[]; adjNote?: string }>()

function valueClass(k: Kpi): string {
  if (k.value == null) return 'c-neutral'
  if (isNeutral(k)) return 'c-primary'
  return k.value > 0 ? 'c-up' : k.value < 0 ? 'c-down' : 'c-neutral'
}
function isNeutral(k: Kpi): boolean {
  return k.tone === 'neutral'
}
function fmt(k: Kpi): string {
  if (k.value == null) return '--'
  const sign = !isNeutral(k) && k.value > 0 ? '+' : ''
  return `${sign}${k.value}${k.unit ?? ''}`
}
/** 风险调整值：以「σ（标准差）」为单位，正负号显式给出，便于与主值对照方向 */
function fmtAdj(v: number): string {
  return `${v > 0 ? '+' : ''}${v}σ`
}
function goAnchor(k: Kpi) {
  if (!k.anchor) return
  document.getElementById(k.anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <div class="kpi-row">
    <NTooltip v-for="k in props.items" :key="k.key" trigger="hover" placement="top" :disabled="!k.hint">
      <template #trigger>
        <div
          class="kpi-card"
          :class="{ 'kpi-card--hl': k.highlight, 'kpi-card--link': !!k.anchor }"
          @click="goAnchor(k)"
        >
          <div class="kpi-label">
            {{ k.label }}
            <span v-if="k.hint" class="kpi-q">?</span>
          </div>
          <div class="kpi-value" :class="valueClass(k)">{{ fmt(k) }}</div>
          <div class="kpi-status" v-if="k.status">{{ k.status }}</div>
          <div v-if="k.pct != null" class="kpi-pct" :class="{ hl: k.highlight }">
            近一年 {{ k.pct }}% 分位
          </div>
          <div v-if="k.adj != null" class="kpi-adj">风险调整 {{ fmtAdj(k.adj) }}</div>
        </div>
      </template>
      {{ k.hint }}
      <template v-if="k.adj != null && props.adjNote">
        <br /><br />{{ props.adjNote }}
      </template>
    </NTooltip>
  </div>
</template>

<style scoped>
.kpi-row { display: flex; gap: 12px; flex-wrap: wrap; }
.kpi-card {
  flex: 1; min-width: 170px; background: #fff; border: 1px solid #D4D7DE;
  border-radius: 8px; padding: 14px 16px; cursor: default; position: relative;
}
.kpi-card--link { cursor: pointer; }
.kpi-card--link:hover { border-color: #185FA5; }
/* 极值标记：仅左侧 3px 金条 + 分位文字金色，金色占比极小（「蓝骨金魂」：金色 ≤10% 强调） */
.kpi-card--hl::before {
  content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px;
  border-radius: 0 2px 2px 0; background: #C9A227;
}
.kpi-label { font-size: 13px; color: #6B7280; display: flex; align-items: center; gap: 4px; }
.kpi-q {
  width: 14px; height: 14px; line-height: 14px; text-align: center; border-radius: 50%;
  background: #E6F1FB; color: #185FA5; font-size: 10px; flex: none;
}
.kpi-value { font-size: 26px; font-weight: 700; margin: 6px 0 4px; font-variant-numeric: tabular-nums; }
.kpi-status { font-size: 12px; color: #6B7280; }
.kpi-pct { font-size: 11px; color: #9CA3AF; margin-top: 4px; }
.kpi-pct.hl { color: #C9A227; font-weight: 500; }
/* 风险调整（σ 倍数）：非涨跌语义 → 次要文字色，不用红绿；与分位同为辅助读数 */
.kpi-adj { font-size: 11px; color: #6B7280; margin-top: 2px; font-variant-numeric: tabular-nums; }
.c-up { color: #EF232A; }
.c-down { color: #14B143; }
.c-primary { color: #185FA5; }
.c-neutral { color: #909399; }
</style>
