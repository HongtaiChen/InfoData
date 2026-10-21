<script setup lang="ts">
/**
 * CorrelationMatrix —— 相关性热力矩阵（投资环境用）
 * 相关系数：正相关蓝（越深越强）、负相关绿；与「红涨绿跌」无关，用色相区分正负方向。
 */
import { computed } from 'vue'

const props = defineProps<{
  labels: string[]
  items: { x: number; y: number; value: number | null }[]
}>()

const valueMap = computed(() => {
  const m = new Map<string, number | null>()
  for (const it of props.items) m.set(`${it.x}-${it.y}`, it.value)
  return m
})

function cellStyle(v: number | null) {
  if (v == null) return { background: '#F5F7FA', color: '#C0C4CC' }
  const a = 0.1 + 0.8 * Math.min(1, Math.abs(v))
  const rgb = v >= 0 ? '24,95,165' : '20,177,67'
  return {
    background: `rgba(${rgb},${a.toFixed(3)})`,
    color: a > 0.45 ? '#fff' : v >= 0 ? '#185FA5' : '#0B6B29',
  }
}
</script>

<template>
  <div v-if="!labels.length" class="cm-empty">无相关性数据</div>
  <div v-else class="cm">
    <div class="cm-grid" :style="{ gridTemplateColumns: `84px repeat(${labels.length}, minmax(0,1fr))` }">
      <div class="cm-corner"></div>
      <div v-for="l in labels" :key="'c' + l" class="cm-colhead" :title="l">{{ l }}</div>
      <template v-for="(a, i) in labels" :key="'r' + a">
        <div class="cm-rowhead" :title="a">{{ a }}</div>
        <div
          v-for="(b, j) in labels"
          :key="a + '-' + b"
          class="cm-cell"
          :style="cellStyle(valueMap.get(`${i}-${j}`) ?? null)"
          :title="`${a} × ${b}: ${valueMap.get(`${i}-${j}`) == null ? '--' : valueMap.get(`${i}-${j}`)!.toFixed(3)}`"
        >
          {{ valueMap.get(`${i}-${j}`) == null ? '--' : valueMap.get(`${i}-${j}`)!.toFixed(2) }}
        </div>
      </template>
    </div>
    <div class="cm-legend">相关系数：越接近 1 越同涨同跌，接近 -1 反向；深浅表示强度</div>
  </div>
</template>

<style scoped>
.cm-empty { font-size: 13px; color: #9CA3AF; }
.cm-grid { display: grid; gap: 2px; }
.cm-colhead {
  font-size: 10px; color: #6B7280; text-align: center; padding-bottom: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.cm-rowhead {
  font-size: 11px; color: #1F2937; text-align: right; padding-right: 6px;
  display: flex; align-items: center; justify-content: flex-end;
  white-space: nowrap; overflow: hidden;
}
.cm-cell {
  height: 22px; border-radius: 2px; display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-variant-numeric: tabular-nums; cursor: default;
}
.cm-legend { font-size: 11px; color: #9CA3AF; margin-top: 8px; }
</style>
