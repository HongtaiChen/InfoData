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
import { NTooltip } from 'naive-ui'
import RichText from './RichText.vue'

const props = withDefaults(
  defineProps<{
    /** 浮窗标题（指标名）—— 卡片上的标签可能被省略号截断，浮窗里给全称 */
    label?: string
    /** 口径说明正文（后端下发，含 `**强调**`） */
    hint?: string
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
 * 白底信息卡。Tooltip 的 self 变量只有这几个可覆盖
 * （padding / borderRadius / boxShadow / color / textColor，见 naive-ui 2.45.3
 *  es/tooltip/styles/light.mjs 与 _common.mjs），其余靠 .kh 内的局部样式。
 * 刻意不加 1px 描边而只用阴影：naive-ui 的箭头是个不带边框的旋转方块，
 * 若用 box-shadow 模拟描边，箭头根部会缺一条线。
 */
const tipTheme = {
  color: '#FFFFFF',
  textColor: '#1F2937',
  boxShadow: '0 6px 20px rgba(15, 23, 42, 0.14)',
  borderRadius: '8px',
  padding: '10px 12px',
}
</script>

<template>
  <NTooltip
    trigger="hover"
    :placement="props.placement"
    :disabled="!props.hint"
    :theme-overrides="tipTheme"
    :delay="150"
  >
    <template #trigger>
      <slot name="trigger" />
    </template>
    <!-- 无 hint 时 NTooltip 被 disabled，此内容不会渲染 -->
    <div class="kh">
      <div v-if="props.label || props.value" class="kh-hd">
        <span v-if="props.label" class="kh-name">{{ props.label }}</span>
        <span v-if="props.value" class="kh-val">{{ props.value }}</span>
      </div>
      <div class="kh-bd"><RichText :text="props.hint" /></div>
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
.kh-note { font-size: 11.5px; line-height: 1.6; color: #6B7280; margin-top: 7px; }
.kh-ft {
  font-size: 10.5px; color: #9CA3AF; margin-top: 7px; padding-top: 6px;
  border-top: 1px solid #F1F4F8;
}
</style>
