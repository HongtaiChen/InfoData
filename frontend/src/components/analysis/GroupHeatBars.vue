<script setup lang="ts">
/**
 * GroupHeatBars —— 分组水平热力条（红涨绿跌，零轴居中）
 * 适用：六组等权收益、行业/板块对比等"空间对比"场景
 *
 * clickable（2026-09-14 新增）：点某一行 emit select(label)，
 * 用于「结论 → 论据」的下钻（如点「科技成长」→ 明细表过滤到该组指数）。
 * pctLabel：该行自身的近 250 日分位文案（让 "+3.5%" 有可比基准）。
 */
import { computed } from 'vue'

export interface HeatRow {
  label: string
  value: number | null
  sub?: string        // 次级数值展示（如 60 日收益）
  pctLabel?: string   // 分位副标（如 "近一年 82% 分位"）
  desc?: string       // 说明（tooltip）
}

const props = defineProps<{
  rows: HeatRow[]
  title?: string
  unit?: string       // 数值后缀，默认 %
  clickable?: boolean
}>()

const emit = defineEmits<{ (e: 'select', label: string): void }>()

const maxAbs = computed(() =>
  Math.max(0.0001, ...props.rows.map((r) => Math.abs(r.value ?? 0)))
)
function pct(v: number | null): number {
  return v == null ? 0 : Math.min(100, (Math.abs(v) / maxAbs.value) * 100)
}
function cls(v: number | null): string {
  return v == null ? 'neutral' : v > 0 ? 'up' : v < 0 ? 'down' : 'neutral'
}
function fmt(v: number | null): string {
  if (v == null) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}${props.unit ?? '%'}`
}
</script>

<template>
  <div class="ghb">
    <div class="ghb-title" v-if="title">{{ title }}</div>
    <div v-for="r in props.rows" :key="r.label"
         class="ghb-row" :class="{ 'ghb-row--link': props.clickable }"
         :title="r.desc ?? r.label"
         @click="props.clickable && emit('select', r.label)">
      <div class="ghb-label">{{ r.label }}</div>
      <div class="ghb-track">
        <div class="ghb-zero"></div>
        <div class="ghb-bar" :class="cls(r.value)"
             :style="r.value != null && r.value > 0
               ? { left: '50%', width: pct(r.value) / 2 + '%' }
               : { right: '50%', width: pct(r.value) / 2 + '%' }"></div>
      </div>
      <div class="ghb-value" :class="'v-' + cls(r.value)">
        {{ fmt(r.value) }}
        <span class="ghb-sub" v-if="r.sub">{{ r.sub }}</span>
        <span class="ghb-sub" v-if="r.pctLabel">{{ r.pctLabel }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ghb { display: flex; flex-direction: column; gap: 10px; }
.ghb-title { font-size: 13px; font-weight: 600; color: #1F2937; }
.ghb-row { display: grid; grid-template-columns: 76px 1fr 200px; align-items: center; gap: 10px; }
.ghb-row--link { cursor: pointer; border-radius: 4px; }
.ghb-row--link:hover { background: #F5F8FC; }
.ghb-label { font-size: 13px; color: #1F2937; text-align: right; }
.ghb-track {
  position: relative; height: 16px; background: #F5F7FA;
  border-radius: 4px; overflow: hidden;
}
.ghb-zero { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: #D4D7DE; }
.ghb-bar { position: absolute; top: 2px; bottom: 2px; border-radius: 3px; }
.ghb-bar.up { background: #EF232A; }
.ghb-bar.down { background: #14B143; }
.ghb-bar.neutral { background: #D4D7DE; }
.ghb-value { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.ghb-sub { color: #9CA3AF; font-weight: 400; font-size: 11px; margin-left: 6px; }
.v-up { color: #EF232A; }
.v-down { color: #14B143; }
.v-neutral { color: #909399; }
</style>
