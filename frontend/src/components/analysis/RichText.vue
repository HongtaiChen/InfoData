<script setup lang="ts">
/**
 * RichText —— 极简内联强调渲染（**仅支持 `**粗体**`**，不做任何其它 markdown）
 *
 * 为什么需要：本项目的「读法 / 口径 / 提示」文案一律由**后端随响应下发**
 * （设计规范 §1.0 硬约束：口径不手抄），后端在这些文案里用 `**…**` 标记重点。
 * 引整套 markdown 渲染器对这个体量过重且会带来 XSS 面，故只切分 `**` 并加粗，其余原样文本。
 *
 * 用法：`<RichText :text="item.reading" />`（替代 `{{ item.reading }}`）
 */
import { computed } from 'vue'

const props = defineProps<{ text?: string | null }>()

/** 偶数下标=普通文本，奇数下标=强调文本（split 后天然交替） */
const parts = computed(() => String(props.text ?? '').split('**'))
</script>

<template>
  <template v-for="(seg, i) in parts" :key="i">
    <strong v-if="i % 2 === 1" class="rt-em">{{ seg }}</strong>
    <template v-else>{{ seg }}</template>
  </template>
</template>

<style scoped>
.rt-em { font-weight: 600; color: #1F2937; }
</style>
