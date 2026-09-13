<script setup lang="ts">
/**
 * SizeGradient —— 有序梯度条（超大盘→微盘），市值风格五档对比
 * 通用：任何"有序档位 + 数值"的对比（红涨绿跌）
 */
import { computed } from 'vue'

export interface GradientItem {
  name: string
  desc?: string
  value: number | null
  change_pct?: number | null
}

const props = defineProps<{ items: GradientItem[]; title?: string }>()

const maxAbs = computed(() => Math.max(0.0001, ...props.items.map((i) => Math.abs(i.value ?? 0))))
function pct(v: number | null): number {
  return v == null ? 0 : Math.min(100, (Math.abs(v) / maxAbs.value) * 100)
}
function cls(v: number | null): string {
  return v == null ? 'neutral' : v > 0 ? 'up' : v < 0 ? 'down' : 'neutral'
}
function fmt(v: number | null): string {
  if (v == null) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}
</script>

<template>
  <div class="sg">
    <div class="sg-title" v-if="title">{{ title }}</div>
    <div v-for="it in props.items" :key="it.name" class="sg-row">
      <div class="sg-name">
        {{ it.name }}
        <span class="sg-desc">{{ it.desc }}</span>
      </div>
      <div class="sg-track">
        <div class="sg-bar" :class="cls(it.value)" :style="{ width: pct(it.value) + '%' }"></div>
      </div>
      <div class="sg-value" :class="'v-' + cls(it.value)">
        {{ fmt(it.value) }}
        <span class="sg-day" v-if="it.change_pct != null">日{{ it.change_pct > 0 ? '+' : '' }}{{ it.change_pct.toFixed(2) }}%</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sg { display: flex; flex-direction: column; gap: 10px; }
.sg-title { font-size: 13px; font-weight: 600; color: #1F2937; }
.sg-row { display: grid; grid-template-columns: 120px 1fr 130px; align-items: center; gap: 10px; }
.sg-name { font-size: 13px; color: #1F2937; }
.sg-desc { color: #9CA3AF; font-size: 11px; margin-left: 4px; }
.sg-track { height: 16px; background: #F5F7FA; border-radius: 4px; overflow: hidden; }
.sg-bar { height: 100%; border-radius: 3px; }
.sg-bar.up { background: #EF232A; }
.sg-bar.down { background: #14B143; }
.sg-bar.neutral { background: #D4D7DE; }
.sg-value { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sg-day { color: #9CA3AF; font-weight: 400; font-size: 11px; margin-left: 6px; }
.v-up { color: #EF232A; }
.v-down { color: #14B143; }
.v-neutral { color: #909399; }
</style>
