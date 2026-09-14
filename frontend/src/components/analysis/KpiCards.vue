<script setup lang="ts">
/**
 * KpiCards —— 分析模块结论区（AnalysisShell §3）
 * 结论先行：大数字 + 状态词 + 口径 tooltip；红涨绿跌仅用于行情语义数值（tone='updown'）
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
</script>

<template>
  <div class="kpi-row">
    <NTooltip v-for="k in props.items" :key="k.key" trigger="hover" placement="top" :disabled="!k.hint">
      <template #trigger>
        <div class="kpi-card">
          <div class="kpi-label">
            {{ k.label }}
            <span v-if="k.hint" class="kpi-q">?</span>
          </div>
          <div class="kpi-value" :class="valueClass(k)">{{ fmt(k) }}</div>
          <div class="kpi-status" v-if="k.status">{{ k.status }}</div>
        </div>
      </template>
      {{ k.hint }}
    </NTooltip>
  </div>
</template>

<style scoped>
.kpi-row { display: flex; gap: 12px; flex-wrap: wrap; }
.kpi-card {
  flex: 1; min-width: 180px; background: #fff; border: 1px solid #D4D7DE;
  border-radius: 8px; padding: 14px 16px; cursor: default;
}
.kpi-label { font-size: 13px; color: #6B7280; display: flex; align-items: center; gap: 4px; }
.kpi-q {
  width: 14px; height: 14px; line-height: 14px; text-align: center; border-radius: 50%;
  background: #E6F1FB; color: #185FA5; font-size: 10px; flex: none;
}
.kpi-value { font-size: 26px; font-weight: 700; margin: 6px 0 4px; font-variant-numeric: tabular-nums; }
.kpi-status { font-size: 12px; color: #6B7280; }
.c-up { color: #EF232A; }
.c-down { color: #14B143; }
.c-primary { color: #185FA5; }
.c-neutral { color: #909399; }
</style>
