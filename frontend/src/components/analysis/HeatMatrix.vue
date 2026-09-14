<script setup lang="ts">
/**
 * HeatMatrix —— 六组 × 时间 热力矩阵（分析研究·市场风向 §4.4）
 *
 * 为什么需要：只有单时点的六条横条，看不出「哪一组在持续走强 / 走弱」——
 * 一眼看到的是当下快照，而不是趋势。把回看窗口等分成若干时段取组内均值，
 * 横轴是时间、纵轴是分组，就能读出持续性（「这一个季度股息防守一路走强」）。
 *
 * 配色纪律：单元格是「收益」语义 → 可用红涨绿跌；强度按 |值| 归一化，
 * 金色不参与（金色只留给极值标记，避免与涨跌语义混用）。
 */
import { computed } from 'vue'

export interface MatrixRow {
  group: string
  values: (number | null)[]
}

const props = defineProps<{
  cols: string[]
  rows: MatrixRow[]
  unit?: string
}>()

/** 点某一行 → 下钻（与 GroupHeatBars 一致，用于「结论 → 论据」） */
const emit = defineEmits<{ (e: 'select', group: string): void }>()

const maxAbs = computed(() => {
  const all = props.rows.flatMap((r) => r.values.filter((v): v is number => v != null))
  return Math.max(0.0001, ...all.map(Math.abs))
})

function cellStyle(v: number | null) {
  if (v == null) return { background: '#F5F7FA', color: '#C0C4CC' }
  const a = 0.12 + 0.68 * Math.min(1, Math.abs(v) / maxAbs.value)
  const rgb = v > 0 ? '239,35,42' : '20,177,67'
  return {
    background: `rgba(${rgb},${a.toFixed(3)})`,
    color: a > 0.5 ? '#fff' : v > 0 ? '#8C1216' : '#0B6B29',
  }
}
function fmt(v: number | null): string {
  return v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`
}
</script>

<template>
  <div v-if="!props.rows.length || !props.cols.length" class="hm-empty">
    数据不足以构建热力矩阵（需要更多历史交易日）
  </div>
  <div v-else class="hm">
    <div class="hm-grid" :style="{ gridTemplateColumns: `76px repeat(${props.cols.length}, minmax(0, 1fr))` }">
      <div class="hm-corner"></div>
      <div v-for="(c, i) in props.cols" :key="'c' + i" class="hm-colhead" :title="c">
        {{ c.slice(5).replace('-', '/') }}
      </div>
      <template v-for="r in props.rows" :key="r.group">
        <div class="hm-rowhead" @click="emit('select', r.group)">{{ r.group }}</div>
        <div
          v-for="(v, i) in r.values"
          :key="r.group + i"
          class="hm-cell"
          :style="cellStyle(v)"
          :title="`${r.group} · ${props.cols[i]} · ${fmt(v)}${props.unit ?? '%'}`"
        >
          {{ fmt(v) }}
        </div>
      </template>
    </div>
    <div class="hm-legend">
      每格 = 该时段内组均 20 日收益的均值（%）；<b class="hm-up">红 = 正收益</b>、<b class="hm-down">绿 = 负收益</b>，深浅表示幅度；横轴自左向右为由远及近
    </div>
  </div>
</template>

<style scoped>
.hm-empty { font-size: 13px; color: #9CA3AF; padding: 8px 0; }
.hm-grid { display: grid; gap: 3px; }
.hm-corner { }
.hm-colhead {
  font-size: 11px; color: #6B7280; text-align: center; padding-bottom: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.hm-rowhead {
  font-size: 12px; color: #1F2937; text-align: right; padding-right: 6px;
  display: flex; align-items: center; justify-content: flex-end;
  cursor: pointer;
}
.hm-rowhead:hover { color: #185FA5; }
.hm-cell {
  height: 26px; border-radius: 3px; display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-variant-numeric: tabular-nums; cursor: default;
}
.hm-legend { font-size: 11px; color: #9CA3AF; margin-top: 8px; }
.hm-legend b { font-weight: 600; }
.hm-up { color: #EF232A; }
.hm-down { color: #14B143; }
</style>
