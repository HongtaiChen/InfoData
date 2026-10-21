<script setup lang="ts">
/**
 * MultiIndexSelect —— 指数多选器（chips 式，不限数量）
 * 用于内容块顶部：选中的指数决定该块展示/叠加哪几个指数。
 * 不做「全局唯一选中」，每个块独立多选，符合「指数在每处内容自由多选/单选」的设计。
 */
import { computed } from 'vue'

export interface IndexOption {
  index_code: string
  index_name: string
}

const props = withDefaults(defineProps<{
  indices: IndexOption[]
  modelValue: string[]
  /** 单选模式：点击即选中该指数（替换而非增删），用于 K 线主图等单标的场景 */
  single?: boolean
  /** 左侧标签文案（默认「指数」；父容器已有说明时可传入更具体的文案避免双重标签） */
  label?: string
}>(), {
  single: false,
  label: '指数',
})

const emit = defineEmits<{ (e: 'update:modelValue', codes: string[]): void }>()

function toggle(code: string) {
  if (props.single) {
    emit('update:modelValue', [code])
    return
  }
  const cur = props.modelValue
  const next = cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code]
  emit('update:modelValue', next)
}

function isOn(code: string): boolean {
  return props.modelValue.includes(code)
}

const hint = computed(() =>
  props.modelValue.length ? `已选 ${props.modelValue.length} 个` : '未选择（点击标签选择指数）',
)
</script>

<template>
  <div class="mis">
    <span class="mis-label">{{ props.label }}</span>
    <div class="mis-chips">
      <span
        v-for="it in indices"
        :key="it.index_code"
        class="mis-chip"
        :class="{ on: isOn(it.index_code) }"
        @click="toggle(it.index_code)"
      >{{ it.index_name }}</span>
    </div>
    <span class="mis-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.mis { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mis-label { font-size: 12px; color: #909399; flex: none; }
.mis-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.mis-chip {
  font-size: 12px; line-height: 1; padding: 5px 10px;
  border: 1px solid #DDE3EA; border-radius: 14px; color: #6B7280;
  cursor: pointer; user-select: none; transition: all 0.15s;
  background: #fff;
}
.mis-chip:hover { border-color: #185FA5; color: #185FA5; }
.mis-chip.on { background: #185FA5; border-color: #185FA5; color: #fff; }
.mis-hint { font-size: 12px; color: #C0C4CC; }
</style>
