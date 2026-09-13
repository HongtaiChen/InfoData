<script setup lang="ts">
/**
 * DrillLink —— 下钻按钮组（分析 → 行情/概念/个股 跳转的统一交互）
 */
import { NButton, NSpace } from 'naive-ui'
import { useRouter } from 'vue-router'

export interface DrillItem {
  label: string
  to: string
  query?: Record<string, string>
}

const props = defineProps<{ items: DrillItem[] }>()
const router = useRouter()

function go(it: DrillItem) {
  router.push({ path: it.to, query: it.query })
}
</script>

<template>
  <NSpace :size="8">
    <NButton v-for="it in props.items" :key="it.label + it.to" size="tiny" secondary @click="go(it)">
      {{ it.label }} →
    </NButton>
  </NSpace>
</template>
