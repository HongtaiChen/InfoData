<script lang="ts">
/**
 * 白底信息卡主题（导出）：全站一切自定义浮窗都应复用它，而不是各写一份 ——
 * 否则将来调阴影/圆角会出现两种浮窗观感。naive-ui Tooltip 的 self 变量只有这几个
 * 可覆盖（padding / borderRadius / boxShadow / color / textColor，见 naive-ui 2.45.3
 * es/tooltip/styles/light.mjs 与 _common.mjs），其余靠 .kh 内的局部样式。
 * 刻意不加 1px 描边而只用阴影：naive-ui 的箭头是个不带边框的旋转方块，
 * 若用 box-shadow 模拟描边，箭头根部会缺一条线。
 */
export const kpiHintTheme = {
  color: '#FFFFFF',
  textColor: '#1F2937',
  boxShadow: '0 6px 20px rgba(15, 23, 42, 0.14)',
  borderRadius: '8px',
  padding: '10px 12px',
}
</script>

<script setup lang="ts">
/**
 * KpiHint —— 指标口径说明浮窗（总览页卡片墙 + 详情页 KpiCards 共用）
 *
 * 为什么要有这个组件（2026-09-19）：
 * 交互设计规范 §1.1 第 4 条「口径透明」早已要求「每个指标旁边可查计算口径」，
 * 后端 28 个 KPI 的 `hint` 也**全部随响应下发**（实测覆盖率 100%，最长 243 字），
 * 详情页 `KpiCards.vue` 也早就消费了；但**总览页卡片墙一直没有入口** —— 用户看得到
 * 「风偏分数 +0.42」，看不到它是怎么算的。本组件把「入口 + 浮窗」抽成共用件，
 * 两处只差触发元素：
 *   - 总览页：触发元素 = ⓘ 圆标（卡片本身是跳转按钮，整卡触发会与「点击进详情」冲突）
 *   - 详情页：触发元素 = 整张卡片（卡片不可跳转，整卡触发更顺手，且保留既有习惯）
 *
 * ⚠️ 两个已实测的坑（改动本组件前请先读）：
 * 1. **naive-ui Tooltip 默认深色**。本项目口径文案最长 243 字（ERP），深色大段正文
 *    可读性明显劣于白底 —— 故用 `theme-overrides` 换成白底信息卡。
 *    实测对比见 docs/design/指标口径说明_交互原型_v0.1.html。
 * 2. **`hint` 里含 `**强调**`**（后端既定风格，18 条里 7 条命中）。若用 `{{ hint }}`
 *    直出会把 `**` 原样显示 —— 必须走 RichText（本项目的既有漏点之一，详情页
 *    tooltip 此前正是漏的）。
 */
import { computed } from 'vue'
import { NTooltip } from 'naive-ui'
import RichText from './RichText.vue'

const props = withDefaults(
  defineProps<{
    /** 浮窗标题（指标名）—— 卡片上的标签可能被省略号截断，浮窗里给全称 */
    label?: string
    /** 口径说明正文（后端下发，含 `**强调**`）。⚠️ 有 `parts` 时它是**回落路径**，不是主路径 */
    hint?: string
    /** R12 五段化的**有序段数组**（后端 `hint_parts`）。⑤ 缺省时后端**整键省略** ⇒ 此处
     *  天然少一段，前端**不做任何补位**（无占位行、无灰字"无"、无禁用标签）。 */
    parts?: { key: string; text: string }[]
    /** R12 段定义（后端 `hint_segments`）：提供标签、顺序与**分组**。
     *  🔴 前端**不内联词表**：标签按 `order` 排、分组线按 `group` 画，
     *     一律从这份下发数据读 —— 将来加段/改序/改组，前端零改动。 */
    segments?: { key: string; label: string; order: number; required: boolean; group?: string }[]
    /** 当前值文案（可选，与卡片上显示的一致，便于对着口径看数） */
    value?: string
    /** 附加说明（可选，如详情页的「风险调整」口径 ADJ_NOTE） */
    note?: string
    /** 浮窗落款（可选覆盖；传空串隐藏。默认口径措辞只适合 KPI 场景，
     *  模块定位说明等其它场景传自己的落款） */
    footer?: string
    placement?: 'top' | 'bottom'
  }>(),
  { placement: 'top', footer: '口径由后端随响应下发，前端只透传' },
)

/**
 * 把 `parts` 与 `segments` 按 key 合并成有序渲染数组。
 * - 顺序、标签、分组**全部来自 `segments`**；`parts` 只提供文本 ⇒ 单源。
 * - `segments` 缺失（旧接口 / 另 4 个模块）时按数组本身顺序、标签回落成 key，仍可渲染。
 */
const rows = computed(() => {
  const list = props.parts ?? []
  if (!list.length) return []
  const segMap = new Map((props.segments ?? []).map((s) => [s.key, s]))
  return list
    .map((p, i) => {
      const s = segMap.get(p.key)
      return {
        key: p.key,
        text: p.text,
        label: s?.label ?? p.key,
        order: s?.order ?? i + 1,
        group: s?.group ?? '',
      }
    })
    .sort((a, b) => a.order - b.order)
})

/** 分组线：某段的 `group` 与上一段不同时，段前插 1px 分隔线（D6-a：前端认 group、不认 key） */
function isGroupBreak(i: number): boolean {
  if (i === 0) return false
  return rows.value[i].group !== rows.value[i - 1].group
}

// 白底主题复用导出的 kpiHintTheme（见文件顶部的普通 <script> 块）
</script>

<template>
  <NTooltip
    trigger="hover"
    :placement="props.placement"
    :disabled="!props.hint && !rows.length"
    :theme-overrides="kpiHintTheme"
    :delay="150"
  >
    <template #trigger>
      <slot name="trigger" />
    </template>
    <!-- 无 hint 且无 parts 时 NTooltip 被 disabled，此内容不会渲染。
         ⚠️ 判据必须带 `rows.length` —— 否则「有 parts、无 hint」的卡浮窗被**静默禁用**
            （卡片上 ⓘ 还在、悬浮却什么都不弹，零报错）。另 4 个模块只有 hint，故旧路径不变。 -->
    <div class="kh">
      <div v-if="props.label || props.value" class="kh-hd">
        <span v-if="props.label" class="kh-name">{{ props.label }}</span>
        <span v-if="props.value" class="kh-val">{{ props.value }}</span>
      </div>
      <!-- R12 五段（P2）：有 parts 走**结构化段**渲染；无 parts 回落单段旧 `hint`（另 4 个模块）。
           `v-if / v-else` 而非两个 v-if —— 二者必须**互斥**，否则双语料同时显示。 -->
      <div v-if="rows.length" class="kh-segs">
        <div
          v-for="(p, i) in rows"
          :key="p.key"
          class="kh-seg"
          :class="{ 'kh-seg--brk': isGroupBreak(i) }"
        >
          <span class="kh-seg-label">{{ p.label }}</span><RichText :text="p.text" />
        </div>
      </div>
      <div v-else class="kh-bd"><RichText :text="props.hint" /></div>
      <div v-if="props.note" class="kh-note"><RichText :text="props.note" /></div>
      <div v-if="props.footer" class="kh-ft">{{ props.footer }}</div>
    </div>
  </NTooltip>
</template>

<style scoped>
/* max-width 必须给：ERP 那条 243 字，不限宽会拉成一条横贯屏幕的长条 */
.kh { max-width: 320px; text-align: left; }
.kh-hd {
  display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
  padding-bottom: 6px; margin-bottom: 6px; border-bottom: 1px solid #EEF1F5;
}
.kh-name { font-size: 12.5px; font-weight: 600; color: #1F2937; }
/* 值用主色蓝而非红绿：浮窗是「管理 UI」语境，红涨绿跌只属于行情数字与 K 线 */
.kh-val { font-size: 12px; font-weight: 600; color: #185FA5; font-variant-numeric: tabular-nums; }
.kh-bd { font-size: 12.5px; line-height: 1.65; color: #374151; word-break: break-word; }
/* R12 五段（P2）：标签**段首行内加粗**（`是什么　3M Shibor…`），**不独立成行** ——
   5 个标签各占一行会白吃约 100px 高度，而浮窗宽只有 320px（浮窗高度不做 max-height 是有意的，
   但那是为了不压缩 ④⑤，不是给标签留的行高预算）。 */
.kh-segs { font-size: 12.5px; line-height: 1.65; color: #374151; word-break: break-word; }
.kh-seg + .kh-seg { margin-top: 4px; }
.kh-seg-label {
  font-weight: 600; color: #1F2937; margin-right: 6px;
  /* 标签自身不参与断行：`异常情况` 被拆成两行会削弱"五段"这个结构感 */
  white-space: nowrap;
}
/* 分组线（D6-a）：① ② ③ = understand（看懂它是什么）／ ④ ⑤ = operate（知道怎么用）。
   由后端 `group` 驱动、前端不认 key ⇒ 将来重新分组不必改这里。 */
.kh-seg--brk { border-top: 1px solid #EEF1F5; padding-top: 6px; }
/* 必须放在上一行之后：与 `.kh-seg + .kh-seg` 同为 (0,2,0) 特异度，靠源码顺序覆盖其 4px */
.kh-seg + .kh-seg--brk { margin-top: 6px; }
.kh-note { font-size: 11.5px; line-height: 1.6; color: #6B7280; margin-top: 7px; }
.kh-ft {
  font-size: 10.5px; color: #9CA3AF; margin-top: 7px; padding-top: 6px;
  border-top: 1px solid #F1F4F8;
}
</style>
