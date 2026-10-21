<script setup lang="ts">
/**
 * IndexSwitcher —— 投资环境「国内指数」顶部指数切换器
 * 用 index_profile 全量 21 个指数，切换后驱动下方全部内容块重新渲染；
 * 右侧展示当前指数当日涨跌幅与最新点位。
 */
import { computed } from 'vue'
import { NSelect } from 'naive-ui'

export interface IndexItem {
  index_code: string
  index_name: string
  close: number | null
  change_pct: number | null
  trade_date: string | null
  drop_threshold?: number | null
  threshold_source?: string | null
}

const props = defineProps<{
  indices: IndexItem[]
  current: string
}>()

const emit = defineEmits<{ (e: 'change', code: string): void }>()

const options = computed(() =>
  props.indices.map((it) => ({
    label: `${it.index_code} ${it.index_name}`,
    value: it.index_code,
  })),
)

const currentItem = computed(() =>
  props.indices.find((it) => it.index_code === props.current),
)

function pctClass(v: number | null): string {
  if (v == null) return ''
  return v > 0 ? 'c-up' : v < 0 ? 'c-down' : ''
}

function fmtPct(v: number | null): string {
  if (v == null) return '--'
  return `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}
</script>

<template>
  <div class="is">
    <NSelect
      :value="current"
      :options="options"
      filterable
      style="width: 260px"
      @update:value="(v: string) => emit('change', v)"
    />
    <div v-if="currentItem" class="is-meta">
      <span class="is-name">{{ currentItem.index_name }}</span>
      <span class="is-point" :class="pctClass(currentItem.change_pct)">
        {{ currentItem.close != null ? Number(currentItem.close).toFixed(2) : '--' }}
      </span>
      <span class="is-pct" :class="pctClass(currentItem.change_pct)">
        {{ fmtPct(currentItem.change_pct) }}
      </span>
      <span v-if="currentItem.trade_date" class="is-date">
        {{ currentItem.trade_date }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.is { display: flex; align-items: center; gap: 14px; }
.is-meta { display: flex; align-items: baseline; gap: 10px; }
.is-name { font-size: 15px; font-weight: 600; color: #1F2937; }
.is-point { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
.is-pct { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
.is-date { font-size: 12px; color: #909399; }
.c-up { color: #EF232A; }
.c-down { color: #14B143; }
</style>
