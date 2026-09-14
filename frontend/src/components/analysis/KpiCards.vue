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
}

const props = defineProps<{ items: Kpi[] }>()

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
        </div>
      </template>
      {{ k.hint }}
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
.c-up { color: #EF232A; }
.c-down { color: #14B143; }
.c-primary { color: #185FA5; }
.c-neutral { color: #909399; }
</style>
