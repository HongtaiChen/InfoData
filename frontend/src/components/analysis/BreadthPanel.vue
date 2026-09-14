<script lang="ts">
/**
 * 类型定义放在普通 <script> 块：<script setup> 不允许 ES 模块导出（类型也不行），
 * 但父组件（MarketWindView）需要 import 此类型，故用双 script 块模式。
 */
export interface Breadth {
  total: number | null
  up: number | null
  down: number | null
  up_ratio: number | null
  adl: number | null
  limit_up: number | null
  limit_down: number | null
  above_ma20_pct: number | null
  above_ma60_pct: number | null
  new_high60: number | null
  new_low60: number | null
  hl_diff60: number | null
  status: string
  score: number | null
  pct: number | null
  highlight?: boolean
  hint?: string
}
</script>

<script setup lang="ts">
/**
 * BreadthPanel —— 市场宽度面板（分析研究·市场风向 §4.3）
 *
 * 为什么需要它：市场风向原有的 19 个指标全是「指数之间比收益」，测不到「上涨是否普遍」。
 * 指数由权重股主导 —— 指数微涨而 3000 只个股下跌（虚涨），或指数微跌而普涨（抵抗），
 * 只看指数收益差会把这两种截然不同的市场读成同一件事。宽度就是补这一维。
 *
 * 配色纪律（「蓝骨金魂」体系）：
 * - 涨=红 #EF232A / 跌=绿 #14B143，只用于涨跌家数这类行情语义数字
 * - 占比/健康度分是非涨跌语义 → 一律主色 #185FA5，禁止红绿（否则「站上MA20占比高」会被误读成涨）
 * - 金色只用于极值标记（近一年分位 <=10 / >=90）
 */
import { NTooltip } from 'naive-ui'

const props = defineProps<{ data: Breadth | null }>()

function barWidth(v: number | null): string {
  const d = props.data
  if (!d || !d.total || v == null) return '0%'
  return `${Math.min(100, Math.max(0, (v / d.total) * 100))}%`
}
function fmtPct(v: number | null): string {
  return v == null ? '--' : `${v.toFixed(1)}%`
}
function fmtInt(v: number | null): string {
  return v == null ? '--' : String(v)
}
/** 新高−新低差：>0 红（多头情绪占优）/ <0 绿，语义即「多空」，可用涨跌色 */
function hlClass(v: number | null): string {
  if (v == null || v === 0) return 'c-neutral'
  return v > 0 ? 'c-up' : 'c-down'
}
</script>

<template>
  <div v-if="!data" class="bp-empty">宽度数据尚未生成（等待 market_style_sync 首次跑完）</div>
  <div v-else class="bp">
    <!-- ① 涨跌分布条（红跌绿按 A 股铁律：涨=红） -->
    <div class="bp-head">
      <div class="bp-title">
        全市场涨跌分布
        <NTooltip trigger="hover" placement="top" :disabled="!data.hint">
          <template #trigger><span class="bp-q">?</span></template>
          {{ data.hint }}
        </NTooltip>
        <span class="bp-status">{{ data.status }}</span>
      </div>
      <div class="bp-score" :class="{ hl: data.highlight }">
        宽度健康度 <b>{{ data.score ?? '--' }}</b>
        <span v-if="data.pct != null" class="bp-pct">上涨占比近一年 {{ data.pct }}% 分位</span>
      </div>
    </div>

    <div class="bp-bar">
      <div class="bp-bar-up" :style="{ width: barWidth(data.up) }"></div>
      <div class="bp-bar-down" :style="{ width: barWidth(data.down) }"></div>
    </div>
    <div class="bp-bar-legend">
      <span class="c-up">上涨 {{ fmtInt(data.up) }} 只（{{ fmtPct(data.up_ratio) }}）</span>
      <span class="bp-total">共 {{ fmtInt(data.total) }} 只</span>
      <span class="c-down">下跌 {{ fmtInt(data.down) }} 只</span>
    </div>

    <!-- ② 宽度明细四格 -->
    <div class="bp-grid">
      <div class="bp-cell">
        <div class="bp-cell-label">涨停 / 跌停</div>
        <div class="bp-cell-value">
          <span class="c-up">{{ fmtInt(data.limit_up) }}</span>
          <span class="bp-slash">/</span>
          <span class="c-down">{{ fmtInt(data.limit_down) }}</span>
        </div>
      </div>
      <div class="bp-cell">
        <div class="bp-cell-label">站上 MA20 占比</div>
        <div class="bp-cell-value c-primary">{{ fmtPct(data.above_ma20_pct) }}</div>
      </div>
      <div class="bp-cell">
        <div class="bp-cell-label">站上 MA60 占比</div>
        <div class="bp-cell-value c-primary">{{ fmtPct(data.above_ma60_pct) }}</div>
      </div>
      <div class="bp-cell">
        <div class="bp-cell-label">60日新高 − 新低</div>
        <div class="bp-cell-value" :class="hlClass(data.hl_diff60)">
          {{ data.hl_diff60 != null && data.hl_diff60 > 0 ? '+' : '' }}{{ fmtInt(data.hl_diff60) }}
        </div>
        <div class="bp-cell-sub">新高 {{ fmtInt(data.new_high60) }} · 新低 {{ fmtInt(data.new_low60) }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bp-empty { font-size: 13px; color: #9CA3AF; padding: 8px 0; }
.bp-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.bp-title { font-size: 13px; color: #374151; display: flex; align-items: center; gap: 6px; }
.bp-q {
  width: 14px; height: 14px; line-height: 14px; text-align: center; border-radius: 50%;
  background: #E6F1FB; color: #185FA5; font-size: 10px; display: inline-block;
}
.bp-status {
  font-size: 12px; color: #185FA5; background: #E6F1FB; border-radius: 4px; padding: 1px 6px;
}
.bp-score { font-size: 12px; color: #6B7280; }
.bp-score b { font-size: 16px; color: #185FA5; font-variant-numeric: tabular-nums; }
.bp-score.hl b { color: #C9A227; }
.bp-pct { margin-left: 6px; color: #9CA3AF; }

.bp-bar { display: flex; height: 14px; border-radius: 3px; overflow: hidden; background: #F3F4F6; }
.bp-bar-up { background: #EF232A; transition: width .3s; }
.bp-bar-down { background: #14B143; transition: width .3s; }
.bp-bar-legend {
  display: flex; justify-content: space-between; font-size: 12px; margin: 6px 0 12px;
}
.bp-total { color: #9CA3AF; }

.bp-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.bp-cell { background: #F9FAFB; border: 1px solid #EDEFF2; border-radius: 6px; padding: 8px 10px; }
.bp-cell-label { font-size: 12px; color: #6B7280; }
.bp-cell-value { font-size: 18px; font-weight: 700; margin-top: 2px; font-variant-numeric: tabular-nums; }
.bp-cell-sub { font-size: 11px; color: #9CA3AF; margin-top: 2px; }
.bp-slash { color: #D1D5DB; font-weight: 400; margin: 0 2px; }

.c-up { color: #EF232A; }
.c-down { color: #14B143; }
.c-primary { color: #185FA5; }
.c-neutral { color: #909399; }
@media (max-width: 900px) { .bp-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
