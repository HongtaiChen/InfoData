<script setup lang="ts">
/**
 * IndicatorBlock —— 投资环境「内容块」原子组件（本模块核心）
 * 三要素固定布局：指标名 + 当前值[分位] ｜ 主图形 ｜ 口径说明
 * 结论由观看者判断：只呈现客观数值与分位，不配「低估/高估/偏强/偏弱」等定性词。
 * 分位中性化：只显示「历史第 X% 分位」，颜色只在极值区（<10% 或 >90%）高亮提示「值得多看一眼」。
 */
import { computed } from 'vue'
import { NCard, NTooltip } from 'naive-ui'
import { kpiHintTheme } from './KpiHint.vue'

const props = withDefaults(defineProps<{
  /** 指标名（中性词，如「滚动市盈率 PE-TTM」） */
  title: string
  /** 当前值（可含单位，由父组件格式化好） */
  value?: string | null
  /** 历史分位 0~100；null 表示无分位 */
  pct?: number | null
  /** 分位窗口口径（如「全史」），随分位一起展示 */
  pctLabel?: string
  /** 口径说明（悬浮「口径」入口 + 底部折叠） */
  note?: string
  /** 是否进入极值区高亮（<10% 或 >90%）—— 由父组件传入，避免本组件自己定义阈值 */
  highlight?: boolean
}>(), {
  value: null,
  pct: null,
  note: '',
  highlight: false,
})

/** 分位圆点位置（夹在 5~95% 内避免被裁） */
const dotLeft = computed(() => {
  if (props.pct == null) return '0%'
  return `${Math.min(95, Math.max(5, props.pct))}%`
})

/** 分位文字（中性：只陈述事实） */
const pctText = computed(() => {
  if (props.pct == null) return ''
  return `${props.pctLabel ?? '历史'}第 ${Number(props.pct).toFixed(1)}% 分位`
})
</script>

<template>
  <NCard class="ib" size="small">
    <!-- 头部：指标名 + 当前值[分位] -->
    <div class="ib-head">
      <div class="ib-title">
        <span class="ib-title-txt">{{ title }}</span>
        <NTooltip
          v-if="note"
          :theme-overrides="kpiHintTheme"
          placement="bottom"
        >
          <template #trigger>
            <span class="ib-q" @click.stop>口径</span>
          </template>
          <div class="ib-note-pop">{{ note }}</div>
        </NTooltip>
      </div>
      <div class="ib-head-right">
        <span v-if="value != null" class="ib-value" :class="{ hl: highlight }">{{ value }}</span>
        <span v-if="pct != null" class="ib-pct" :class="{ hl: highlight }">
          {{ pctText }}
        </span>
      </div>
    </div>

    <!-- 分位刻度条（有分位时展示，中性位置参考） -->
    <div v-if="pct != null" class="ib-track-row">
      <span class="ib-track">
        <span class="ib-dot" :class="{ hl: highlight }" :style="{ left: dotLeft }" />
      </span>
    </div>

    <!-- 主图形（插槽） -->
    <div class="ib-body">
      <slot />
    </div>

    <!-- 口径说明（底部） -->
    <div v-if="note" class="ib-note">
      <span class="ib-note-mark">📖</span>{{ note }}
    </div>
  </NCard>
</template>

<style scoped>
.ib { margin-bottom: 12px; }
.ib-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 6px;
}
.ib-title { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
.ib-title-txt {
  font-size: 14px; font-weight: 600; color: #1F2937;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ib-q {
  font-size: 11px; line-height: 1; color: #8D97A5; border: 1px solid #DDE3EA;
  border-radius: 3px; padding: 2px 4px; flex: none; white-space: nowrap; cursor: help;
}
.ib-q:hover { color: #185FA5; border-color: #185FA5; }
.ib-head-right { display: flex; align-items: baseline; gap: 10px; flex: none; }
.ib-value {
  font-size: 22px; font-weight: 700; color: #1F2937;
  font-variant-numeric: tabular-nums;
}
.ib-value.hl { color: #C9A227; }
.ib-pct { font-size: 12px; color: #6B7280; }
.ib-pct.hl { color: #C9A227; font-weight: 500; }
.ib-track-row { margin: 2px 0 10px; }
.ib-track { position: relative; display: block; height: 4px; border-radius: 2px; background: #E3E8EF; }
.ib-dot {
  position: absolute; top: 50%; width: 8px; height: 8px; margin-left: -4px;
  border-radius: 50%; background: #185FA5; transform: translateY(-50%);
  box-shadow: 0 0 0 2px #fff;
}
.ib-dot.hl { background: #C9A227; }
.ib-body { }
.ib-note {
  margin-top: 8px; padding-top: 8px; border-top: 1px dashed #EEF1F5;
  font-size: 12px; line-height: 1.6; color: #6B7280;
}
.ib-note-mark { margin-right: 2px; }
.ib-note-pop { max-width: 320px; font-size: 12.5px; line-height: 1.7; color: #374151; }
</style>
