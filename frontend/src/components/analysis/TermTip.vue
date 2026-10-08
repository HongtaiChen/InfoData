<script setup lang="ts">
/**
 * TermTip —— 术语 / 量纲的**行内**提示入口（R9 ③「首次出现处就地展开，不用离开当前视线」）
 *
 * 视觉：被包裹的词带虚线下划线（`cursor: help`），浮窗复用 `KpiHint` 导出的 `kpiHintTheme`
 * —— 全站自定义浮窗只有一套观感，将来调阴影/圆角不会出现两种样式。
 *
 * 🔴 **`trigger` 为什么必须可切**（本组件唯一不显然的地方，实测自 naive-ui 源码）：
 * naive-ui 的 Popover **内建嵌套浮窗协调**，但只对「**直接以 popover 作为 trigger**」生效
 * （`Popover.mjs`：`if (triggerVNode.type?.__popover__)` → 设 `internalSyncTargetWithParent`
 * + `internalInheritedEventHandlers` 把事件转发给父链）。而本组件在 KPI 卡里的落点是卡片内
 * 的一个 `<span>` —— 触发 vnode 是普通元素，走不到那条协调逻辑；且原生 `mouseenter`
 * **不冒泡**，所以进入该 span 时：内层弹出（自身 mouseenter），外层**不会关闭**
 * （它只在离开卡片时 mouseleave，而指针并未离开卡片）⇒ **两个浮窗同时弹出**。
 * ⇒ 结论：**嵌在其它浮窗触发区内时必须用 `trigger="click"`**（click 不冲突：外层是 hover 触发）。
 *    语义仍然是"就地展开、不用跳页"，只是由悬停改为点一下。
 *
 * 未命中术语表时渲染 `.tt-miss`（普通文字 + 标记类）—— 这个类名是给断言用的：
 * 页面上出现 `.tt-miss` 就说明有个词被当成术语挂了提示却没定义（静默漏定义是本项最容易的失败态）。
 */
import { computed } from 'vue'
import { NTooltip } from 'naive-ui'
import { kpiHintTheme } from './KpiHint.vue'
import { GLOSSARY_MAP, KIND_LABEL } from '../../data/glossary'

const props = withDefaults(
  defineProps<{
    /** 词条原文；必须与 `glossary.ts` 里的 `term` 逐字一致 */
    term: string
    /** hover（默认，用于无外层浮窗处）| click（**嵌在其它浮窗触发区内时必用**，原因见顶部注释） */
    trigger?: 'hover' | 'click'
  }>(),
  { trigger: 'hover' },
)

const entry = computed(() => GLOSSARY_MAP.get(props.term) ?? null)
</script>

<template>
  <NTooltip
    v-if="entry"
    :trigger="props.trigger"
    :theme-overrides="kpiHintTheme"
    :delay="120"
    placement="top"
  >
    <template #trigger>
      <!-- `.stop` 是必需的：本组件常嵌在 KPI 卡里，而卡片自身绑了 `@click` 跳锚点 ——
           不拦就会「点一下术语、页面同时滚走」。naive-ui 的 click 处理器绑在**同一个**元素上，
           `stopPropagation` 只阻止向祖先冒泡，故不影响浮窗开启。 -->
      <span class="tt" @click.stop>{{ props.term }}</span>
    </template>
    <div class="tt-card">
      <div class="tt-hd">
        <span class="tt-name">{{ entry.term }}</span>
        <span class="tt-kind">{{ KIND_LABEL[entry.kind] }}</span>
      </div>
      <div class="tt-full">{{ entry.full }}</div>
      <div v-if="entry.note" class="tt-note">{{ entry.note }}</div>
    </div>
  </NTooltip>
  <!-- 未定义：原样显示文字，但打标记类 —— 断言扫 `.tt-miss` 就知道漏了谁 -->
  <span v-else class="tt-miss">{{ props.term }}</span>
</template>

<style scoped>
.tt {
  border-bottom: 1px dashed #A9B4C2;
  cursor: help;
  /* 虚线要贴着字底，不能在行高里乱飘 */
  padding-bottom: 0.5px;
}
.tt:hover { border-bottom-color: #185FA5; color: #185FA5; }
.tt-miss { /* 故意不加视觉样式：漏定义不该看起来"没问题"，由断言抓 */ }
.tt-card { max-width: 280px; text-align: left; }
.tt-hd {
  display: flex; align-items: baseline; gap: 6px;
  padding-bottom: 5px; margin-bottom: 5px; border-bottom: 1px solid #EEF1F5;
}
.tt-name { font-size: 12.5px; font-weight: 600; color: #1F2937; }
.tt-kind { font-size: 10.5px; color: #9CA3AF; }
.tt-full { font-size: 12.5px; line-height: 1.6; color: #374151; }
.tt-note { font-size: 11.5px; line-height: 1.6; color: #6B7280; margin-top: 5px; }
</style>
