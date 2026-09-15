<script lang="ts">
/**
 * 类型定义放在普通 <script> 块：<script setup> 不允许 ES 模块导出（类型也不行），
 * 但父组件（MarketWindView）需要 import 此类型，故用双 script 块模式（同 BreadthPanel）。
 */
export interface CrossDim {
  name: string
  value: number | null
  unit: string
  tone: 'updown' | 'neutral'
  pct: number | null
  delta: number | null
  delta_label: string
  delta_unit: string
  as_of: string | null
  sub: string | null
}
export interface CrossEvidence { label: string; value: string | number | null }
export interface CrossItem {
  key: string
  label: string
  pair: string
  level: 'agree' | 'diverge' | 'neutral' | 'nodata'
  verdict: string
  reading: string
  dims: CrossDim[]
  evidence: CrossEvidence[]
  hint: string
  as_of: string | null
  stale: number
}
export interface CrossSummary { agree: number; diverge: number; neutral: number; nodata: number }
</script>

<script setup lang="ts">
/**
 * CrossCheckPanel —— 交叉印证面板（分析研究·市场风向 §4.4 / 设计规范 §1.0 参照系 ④）
 *
 * **为什么需要它**：市场风向原有的全部指标（六组收益差、宽度、量能、pct/adj）都在
 * 「股票市场内部」打转，从没跟利率、商品、杠杆资金、概念口径比过。而**所有背离信号
 * 都来自这一类**——两个本该同向的维度不同向，才是可判断的信息。
 *
 * 每项固定四段（结论先行）：
 *   ① 维度对（两个独立维度各自读数：值 / 20日变化 / 分位 / 截至日）
 *   ② 判定徽标（一致 / 背离 / 中性 / 数据缺失）
 *   ③ 读法（后端下发的一句话结论，前端不自己编）
 *   ④ 证据（相关系数、样本数、分位等可复核的统计量）
 *
 * 配色纪律（「蓝骨金魂」）：
 * - **背离用琥珀 #B45309**，不用红绿 —— 背离不是涨跌语义，也不是错误（失败用深红棕 #791F1F），
 *   它是最有价值的信号，但必须与「金色 = 系统亮点/AI 洞察」区分开，否则金色会泛滥。
 * - 一致用主色蓝 #185FA5（中性确认），中性与缺失用灰。
 * - 维度数值仍严格红涨绿跌（tone='updown'）；分位、z、换手率等非涨跌语义一律主色/灰。
 */
import { NTooltip } from 'naive-ui'
import RichText from './RichText.vue'

const props = defineProps<{ items: CrossItem[]; note?: string }>()

const LEVEL_CLASS: Record<string, string> = {
  diverge: 'lv-diverge', agree: 'lv-agree', neutral: 'lv-neutral', nodata: 'lv-nodata',
}

/** 汇总计数（背离 / 一致 / 中性 / 缺失） */
function count(level: string): number {
  return props.items.filter((i) => i.level === level).length
}

function dimValue(d: { value: number | null; unit: string; tone: string }): string {
  if (d.value == null) return '--'
  const sign = d.tone === 'updown' && d.value > 0 ? '+' : ''
  return `${sign}${d.value}${d.unit}`
}
function dimValueClass(d: { value: number | null; tone: string }): string {
  if (d.value == null) return 'c-gray'
  if (d.tone !== 'updown') return 'c-primary'
  return d.value > 0 ? 'c-up' : d.value < 0 ? 'c-down' : 'c-gray'
}
function dimDelta(d: { delta: number | null; delta_unit: string }): string {
  if (d.delta == null) return ''
  return `${d.delta > 0 ? '+' : ''}${d.delta}${d.delta_unit}`
}
function dimDeltaClass(d: { delta: number | null }): string {
  if (d.delta == null || d.delta === 0) return 'c-gray'
  // 变化方向不是涨跌语义（利率上行、换手率抬升都不该染红绿）→ 统一次要灰
  return 'c-gray'
}
function fmtEv(v: string | number | null): string {
  return v == null ? '--' : String(v)
}
</script>

<template>
  <div class="xc">
    <div class="xc-head">
      <span class="xc-title">交叉印证 · {{ items.length }} 项</span>
      <span class="xc-sum">
        <span v-if="count('diverge')" class="xc-chip lv-diverge">背离 {{ count('diverge') }}</span>
        <span v-if="count('agree')" class="xc-chip lv-agree">一致 {{ count('agree') }}</span>
        <span v-if="count('neutral')" class="xc-chip lv-neutral">中性 {{ count('neutral') }}</span>
        <span v-if="count('nodata')" class="xc-chip lv-nodata">缺失 {{ count('nodata') }}</span>
      </span>
      <NTooltip trigger="hover" placement="top" :disabled="!props.note">
        <template #trigger><span class="xc-q">?</span></template>
        <span class="xc-tip"><RichText :text="props.note" /></span>
      </NTooltip>
    </div>

    <div class="xc-grid">
      <div v-for="it in props.items" :key="it.key" class="xc-card" :class="LEVEL_CLASS[it.level]">
        <div class="xc-card-head">
          <span class="xc-badge" :class="LEVEL_CLASS[it.level]">{{ it.verdict }}</span>
          <span class="xc-label">{{ it.label }}</span>
          <span class="xc-pair">{{ it.pair }}</span>
          <span v-if="it.stale > 0" class="xc-stale">滞后 {{ it.stale }} 日</span>
          <NTooltip trigger="hover" placement="top" :disabled="!it.hint">
            <template #trigger><span class="xc-q">?</span></template>
            <span class="xc-tip"><RichText :text="it.hint" /></span>
          </NTooltip>
        </div>

        <div class="xc-dims">
          <div v-for="d in it.dims" :key="d.name" class="xc-dim">
            <div class="xc-dim-name">{{ d.name }}</div>
            <div class="xc-dim-value" :class="dimValueClass(d)">{{ dimValue(d) }}</div>
            <div class="xc-dim-sub">
              <span v-if="d.delta != null" :class="dimDeltaClass(d)">
                {{ d.delta_label }} {{ dimDelta(d) }}
              </span>
              <span v-if="d.pct != null" class="xc-dim-pct">· 分位 {{ d.pct }}%</span>
              <span v-if="d.sub" class="xc-dim-pct">· {{ d.sub }}</span>
            </div>
          </div>
        </div>

        <div class="xc-reading"><RichText :text="it.reading" /></div>

        <div class="xc-ev">
          <span v-for="e in it.evidence" :key="e.label" class="xc-ev-item">
            {{ e.label }} <b>{{ fmtEv(e.value) }}</b>
          </span>
        </div>
      </div>
    </div>

    <div v-if="!items.length" class="xc-empty">交叉印证暂无可用数据</div>
  </div>
</template>

<style scoped>
.xc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.xc-title { font-size: 13px; font-weight: 600; color: #1F2937; }
.xc-sum { display: flex; gap: 6px; }
.xc-chip { font-size: 11px; border-radius: 3px; padding: 1px 6px; }
.xc-q {
  width: 14px; height: 14px; line-height: 14px; text-align: center; border-radius: 50%;
  background: #E6F1FB; color: #185FA5; font-size: 10px; display: inline-block; cursor: help;
}

.xc-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.xc-card {
  border: 1px solid #EDEFF2; border-left: 3px solid #D4D7DE; border-radius: 6px;
  padding: 10px 12px; background: #fff;
}
/* 背离 = 最有价值的信号 → 琥珀左边框 + 淡琥珀底；刻意不用红绿（非涨跌语义） */
.xc-card.lv-diverge { border-left-color: #B45309; background: #FFFBF3; }
.xc-card.lv-agree { border-left-color: #185FA5; }

.xc-card-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.xc-badge { font-size: 11px; border-radius: 3px; padding: 1px 6px; flex: none; }
.xc-badge.lv-diverge { background: #FEF3C7; color: #B45309; font-weight: 600; }
.xc-badge.lv-agree { background: #E6F1FB; color: #185FA5; }
.xc-badge.lv-neutral { background: #F3F4F6; color: #6B7280; }
.xc-badge.lv-nodata { background: #F3F4F6; color: #9CA3AF; }
.xc-chip.lv-diverge { background: #FEF3C7; color: #B45309; }
.xc-chip.lv-agree { background: #E6F1FB; color: #185FA5; }
.xc-chip.lv-neutral { background: #F3F4F6; color: #6B7280; }
.xc-chip.lv-nodata { background: #F3F4F6; color: #9CA3AF; }
.xc-label { font-size: 13px; font-weight: 600; color: #1F2937; }
.xc-pair { font-size: 12px; color: #6B7280; }
.xc-stale { font-size: 11px; color: #B45309; background: #FEF3C7; border-radius: 3px; padding: 0 5px; }

.xc-dims { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin: 8px 0; }
.xc-dim { background: #F9FAFB; border: 1px solid #EDEFF2; border-radius: 5px; padding: 6px 8px; }
.xc-dim-name { font-size: 11px; color: #6B7280; }
.xc-dim-value { font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; margin: 1px 0; }
.xc-dim-sub { font-size: 11px; display: flex; gap: 4px; flex-wrap: wrap; }
.xc-dim-pct { color: #9CA3AF; }

.xc-reading { font-size: 12px; color: #374151; line-height: 1.65; }
.xc-tip { display: inline-block; max-width: 420px; font-size: 12px; line-height: 1.6; }
.xc-ev { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 7px; padding-top: 6px; border-top: 1px dashed #EDEFF2; }
.xc-ev-item { font-size: 11px; color: #9CA3AF; }
.xc-ev-item b { color: #6B7280; font-weight: 600; }
.xc-empty { font-size: 13px; color: #9CA3AF; padding: 8px 0; }

.c-up { color: #EF232A; }
.c-down { color: #14B143; }
.c-primary { color: #185FA5; }
.c-gray { color: #6B7280; }
@media (max-width: 1100px) { .xc-grid { grid-template-columns: 1fr; } }
</style>
