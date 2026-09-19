<script setup lang="ts">
/**
 * FundingTemperatureView —— 资金温度模块（分析研究·资金与情绪）
 * 数据：GET /api/analysis/funding-temperature
 *
 * 本模块是本项目里少数**跨三个数据源合成**的分析模块，落的是设计规范 §1.0 的
 * ⑤ **结构分解**（新发基金的权益/固收构成、回购的进度构成）、② **自身纵比**
 * （三条线各自在近 36 个月的什么位置），核心是 ④ **交叉印证**：
 *   ① 外部资金环境 → 美元兑人民币中间价（央行口径）
 *   ② 增量资金（居民端）→ 新基金发行（公募口径）
 *   ③ 产业资本（公司端）→ 上市公司回购（公告口径）
 * 三条同向才算信号，**背离本身就是最有价值的读数**。
 *
 * ⚠️ 四条口径纪律（后端已下发，前端只透传，任何一条改写都会产生错误结论）：
 * 1. **每个 KPI 的分位窗口长度不同**（汇率近一年 / 基金与回购近 36 个月），
 *    窗口文案来自 `scale.label`，**不得写死"近一年"**，也不得跨指标比数字大小。
 * 2. **回购按"回购起始时间"统计**，不是"最新公告日"—— 后者会被后续公告覆盖、
 *    历史月份被抽空，实测该口径分位恒为 100%（零区分度）。
 * 3. **新发基金只统计"完整月"**：最新月往往只覆盖到月中，不剔除会让近 3 月合计断崖。
 * 4. **进度构成与累计金额是"近 36 个月窗口内累计"**，与 headline 的"近 90 天"差 12 倍，
 *    故本视图在各处显式标注周期，绝不省略。
 */
import { computed, onMounted, ref } from 'vue'
import { NCard, NDatePicker, NSpin } from 'naive-ui'
import KpiCards from '../../components/analysis/KpiCards.vue'
import DualLineTrend from '../../components/analysis/DualLineTrend.vue'
import DrillLink from '../../components/analysis/DrillLink.vue'
import RichText from '../../components/analysis/RichText.vue'
import api from '../../api'

interface Kpi {
  key: string; label: string; value: number | null; unit?: string
  status?: string; hint?: string
  tone?: 'updown' | 'diff' | 'neutral'
  card_rank?: number | null
  scale?: { pct: number; label: string } | null
  pct?: number | null; highlight?: boolean; anchor?: string
}
interface Fx {
  as_of: string | null; base_date_1y: string | null; mid: number | null
  min_1y: number | null; max_1y: number | null; pct: number | null; chg20: number | null
  series: { dates: string[]; values: (number | null)[] }
}
interface FundMonth {
  ym: string; n: number; shares: number; eq: number; fi: number; other: number; eq_pct: number | null
}
interface Fund {
  window: { start: string; end: string; months: number } | null
  monthly: FundMonth[]
  last3: { months: string[]; shares: number; eq: number; fi: number; eq_pct: number } | null
  avg3: number | null; pct: number | null; min: number | null; max: number | null
  total_months: number
}
interface RepMonth { ym: string; n: number; done: number; running: number; amount_yi: number | null }
interface Rep {
  last90: number | null; pct: number | null; min: number | null; max: number | null
  window_days: number
  window: { start: string; end: string; months: number } | null
  series: { dates: string[]; values: (number | null)[] }
  progress: { progress: string; n: number; amount_yi: number | null }[]
  done_amount_yi: number | null
  monthly: RepMonth[]
}
interface Clue {
  key: string; name: string; value: number | null; pct: number | null
  window: string; supportive: boolean | null; label: string; reading: string
}
interface Temperature {
  score: number; total: number; level: string
  tone: 'normal' | 'opportunity' | 'caution'
  reading: string; supportive: string[]; against: string[]
}

const loading = ref(false)
const asOf = ref('')
const isReplay = ref(false)
const kpis = ref<Kpi[]>([])
const fx = ref<Fx | null>(null)
const fund = ref<Fund | null>(null)
const repurchase = ref<Rep | null>(null)
const clues = ref<Clue[]>([])
const temperature = ref<Temperature | null>(null)
const note = ref('')
const replayTs = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {}
    if (replayTs.value) {
      const d = new Date(replayTs.value)
      params.as_of = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    }
    const resp: any = await api.get('/analysis/funding-temperature', { params })
    asOf.value = resp.as_of ?? ''
    isReplay.value = !!resp.is_replay
    note.value = resp.note ?? ''
    kpis.value = (resp.kpis ?? []).map((k: Kpi) => ({ ...k, tone: k.tone ?? 'neutral' }))
    fx.value = resp.fx ?? null
    fund.value = resp.fund ?? null
    repurchase.value = resp.repurchase ?? null
    clues.value = resp.clues ?? []
    temperature.value = resp.temperature ?? null
  } catch (e) {
    console.error('[funding-temperature]', e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

const fxSeries = computed(() => {
  const s = fx.value?.series
  if (!s) return []
  return [{ name: '美元/人民币中间价', values: s.values, color: '#185FA5' }]
})
const repSeries = computed(() => {
  const s = repurchase.value?.series
  if (!s) return []
  return [{ name: `滚动 ${repurchase.value?.window_days ?? 90} 天新启动计划数`, values: s.values, color: '#185FA5' }]
})

/** 月度堆叠柱：按窗口内最大月合计归一，内部再按权益/固收/其他拆分 */
const fundMax = computed(() =>
  Math.max(1, ...(fund.value?.monthly ?? []).map((m) => m.shares)))
const repMax = computed(() =>
  Math.max(1, ...(repurchase.value?.monthly ?? []).map((m) => m.n)))

function w(v: number | null, max: number): string {
  return `${Math.min(100, ((v ?? 0) / max) * 100)}%`
}
function part(v: number, total: number): string {
  return `${total > 0 ? (v / total) * 100 : 0}%`
}
/** 线索顺风与否的徽标：顺风=主色蓝、不顺风=琥珀。
 *  ⚠️ 刻意不用绿色表示"顺风"——绿色在本项目是「跌」的专用色（A 股铁律），
 *     管理 UI 里禁用绿作"成功"，否则会与行情语义冲突。 */
function clueCls(c: Clue): string {
  if (c.supportive === null) return 'ft-clue--na'
  return c.supportive ? 'ft-clue--ok' : 'ft-clue--warn'
}
function clueText(c: Clue): string {
  if (c.supportive === null) return '数据未就绪'
  return c.supportive ? '顺风' : '不顺风'
}
</script>

<template>
  <NSpin :show="loading">
    <div class="ft-params">
      <span class="ft-asof">
        数据截至 <b>{{ asOf || '--' }}</b>
        <span class="ft-sub">
          （月度窗口 {{ fund?.window?.start ?? '--' }} ~ {{ fund?.window?.end ?? '--' }}，
          共 {{ fund?.window?.months ?? '--' }} 个完整月）
        </span>
        <span v-if="isReplay" class="ft-replay">历史回放</span>
      </span>
      <div class="ft-params-right">
        <NDatePicker v-model:value="replayTs" type="date" size="small" clearable
                     placeholder="回放某交易日" style="width: 148px" @update:value="load" />
      </div>
    </div>

    <KpiCards :items="kpis" />

    <!-- ④ 交叉印证：三条线索 -->
    <NCard id="ft-cross" size="small" class="ft-card"
           :title="`三条独立资金线索的交叉印证（顺风 ${temperature?.score ?? '--'}/${temperature?.total ?? 3}）`">
      <div class="ft-temp" :class="`ft-temp--${temperature?.tone ?? 'normal'}`">
        <span class="ft-temp-badge">{{ temperature?.level ?? '--' }}</span>
        <span class="ft-temp-text"><RichText :text="temperature?.reading" /></span>
      </div>
      <div class="ft-clues">
        <div v-for="c in clues" :key="c.key" class="ft-clue" :class="clueCls(c)">
          <div class="ft-clue-head">
            <b>{{ c.name }}</b>
            <span class="ft-clue-tag">{{ clueText(c) }}</span>
            <span class="ft-clue-pct" v-if="c.pct != null">{{ c.pct }}% 分位（{{ c.window }}）</span>
          </div>
          <div class="ft-clue-read"><RichText :text="c.reading" /></div>
        </div>
      </div>
      <div class="ft-note"><RichText :text="note" /></div>
    </NCard>

    <!-- ① 汇率 -->
    <NCard id="ft-fx" size="small" class="ft-card"
           title="外部资金环境：美元兑人民币中间价（央行口径）">
      <template #header-extra>
        <DrillLink :items="[{ label: '跨市场对照看外部', to: '/analysis/cross-market' }]" />
      </template>
      <div class="ft-stats">
        <span>当前 <b class="ft-strong">{{ fx?.mid ?? '--' }}</b></span>
        <span>近一年区间 <b>{{ fx?.min_1y ?? '--' }} ~ {{ fx?.max_1y ?? '--' }}</b></span>
        <span>近一年分位 <b class="ft-strong">{{ fx?.pct ?? '--' }}%</b></span>
        <span>20 日变化 <b>{{ fx?.chg20 == null ? '--' : (fx.chg20 > 0 ? '+' : '') + fx.chg20 + '%' }}</b></span>
      </div>
      <DualLineTrend v-if="fx?.series" :dates="fx.series.dates" :series="fxSeries" height="260px" />
      <div class="ft-foot">
        ⚠️ 分位越低 = 美元越便宜 = 人民币越强，方向与直觉相反：
        看刻度条时以「区间最低 = 人民币最强」为准。区间最值见上方数字。
      </div>
    </NCard>

    <!-- ② 增量资金：新发基金（用户端） -->
    <NCard id="ft-fund" size="small" class="ft-card"
           :title="`增量资金：新基金发行（近 ${fund?.window?.months ?? '--'} 个完整月，按成立日）`">
      <div class="ft-stats">
        <span>近 3 完整月月均 <b class="ft-strong">{{ fund?.avg3 ?? '--' }} 亿份</b></span>
        <span>近 3 月合计 <b>{{ fund?.last3?.shares ?? '--' }} 亿份</b></span>
        <span>权益占比 <b class="ft-strong">{{ fund?.last3?.eq_pct ?? '--' }}%</b></span>
        <span>月度分位 <b>{{ fund?.pct ?? '--' }}%</b></span>
        <span class="ft-dim">区间 {{ fund?.min ?? '--' }} ~ {{ fund?.max ?? '--' }} 亿份</span>
      </div>
      <div class="ft-legend">
        <span class="ft-legend-item"><i class="ft-dot ft-dot--eq" />权益类</span>
        <span class="ft-legend-item"><i class="ft-dot ft-dot--fi" />固收类</span>
        <span class="ft-legend-item"><i class="ft-dot ft-dot--ot" />其他</span>
        <span class="ft-dim">（柱长 = 该月合计募集份额，按窗口内最大月归一）</span>
      </div>
      <div class="ft-bars">
        <div v-for="m in fund?.monthly ?? []" :key="m.ym" class="ft-bar-row">
          <span class="ft-bar-ym">{{ m.ym }}</span>
          <span class="ft-bar-track">
            <span class="ft-bar" :style="{ width: w(m.shares, fundMax) }">
              <span class="ft-seg ft-seg--eq" :style="{ width: part(m.eq, m.shares) }" />
              <span class="ft-seg ft-seg--fi" :style="{ width: part(m.fi, m.shares) }" />
              <span class="ft-seg ft-seg--ot" :style="{ width: part(m.other, m.shares) }" />
            </span>
          </span>
          <span class="ft-bar-val">{{ m.shares.toFixed(0) }} 亿份</span>
          <span class="ft-bar-sub">{{ m.n }} 只 · 权益 {{ m.eq_pct ?? '--' }}%</span>
        </div>
      </div>
      <div class="ft-foot">
        ⚠️ 只统计**完整月**（最新月往往只覆盖到月中，不剔除会让近 3 月合计断崖）。
        权益/固收按基金类型子串归并（债券/固收/货币 → 固收；股票/偏股/灵活/平衡 → 权益；其余入其他）。
      </div>
    </NCard>

    <!-- ③ 产业资本：回购 -->
    <NCard id="ft-rep" size="small" class="ft-card"
           :title="`产业资本：上市公司回购（滚动 ${repurchase?.window_days ?? 90} 天新启动计划数）`">
      <div class="ft-stats">
        <span>当前滚动 <b class="ft-strong">{{ repurchase?.last90 ?? '--' }} 个</b></span>
        <span>近 {{ repurchase?.window?.months ?? '--' }} 个月分位 <b>{{ repurchase?.pct ?? '--' }}%</b></span>
        <span class="ft-dim">区间 {{ repurchase?.min ?? '--' }} ~ {{ repurchase?.max ?? '--' }} 个</span>
      </div>
      <DualLineTrend v-if="repurchase?.series" :dates="repurchase.series.dates"
                     :series="repSeries" height="240px" />
      <div class="ft-foot">
        ⚠️ 按**回购起始时间**统计 —— 不能用"最新公告日"：同一计划的公告日会被后续公告覆盖，
        历史月份被抽空、近期永远处于最高分位（实测该口径分位恒为 100%，无区分度）。
      </div>

      <div class="ft-two-col">
        <div>
          <div class="ft-sub-title">
            进度构成（近 {{ repurchase?.window?.months ?? '--' }} 个月窗口内累计，
            不是近 {{ repurchase?.window_days ?? 90 }} 天）
          </div>
          <table class="ft-table">
            <thead><tr><th>进度</th><th>计划数</th><th>已回购金额</th></tr></thead>
            <tbody>
              <tr v-for="p in repurchase?.progress ?? []" :key="p.progress">
                <td>{{ p.progress }}</td>
                <td class="ft-num">{{ p.n }}</td>
                <td class="ft-num ft-dim">{{ p.amount_yi == null ? '--' : p.amount_yi + ' 亿' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div>
          <div class="ft-sub-title">逐月新启动计划数（近 {{ repurchase?.window?.months ?? '--' }} 个月）</div>
          <div class="ft-bars ft-bars--short">
            <div v-for="m in repurchase?.monthly ?? []" :key="m.ym" class="ft-bar-row">
              <span class="ft-bar-ym">{{ m.ym }}</span>
              <span class="ft-bar-track">
                <span class="ft-bar ft-bar--rep" :style="{ width: w(m.n, repMax) }" />
              </span>
              <span class="ft-bar-val">{{ m.n }}</span>
              <span class="ft-bar-sub">完成 {{ m.done }} · 实施中 {{ m.running }}</span>
            </div>
          </div>
        </div>
      </div>
    </NCard>
  </NSpin>
</template>

<style scoped>
.ft-params { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.ft-asof { font-size: 13px; color: #6B7280; }
.ft-asof b { color: #1F2937; }
.ft-sub { color: #9CA3AF; }
.ft-replay {
  margin-left: 6px; font-size: 11px; color: #185FA5;
  background: #E6F1FB; border-radius: 3px; padding: 1px 5px;
}
.ft-params-right { display: flex; gap: 8px; align-items: center; }
.ft-card { margin-bottom: 12px; scroll-margin-top: 12px; }
.ft-stats {
  display: flex; gap: 18px; flex-wrap: wrap; align-items: baseline;
  font-size: 12px; color: #6B7280; margin-bottom: 12px;
  padding-bottom: 10px; border-bottom: 1px dashed #EDEFF2;
}
.ft-strong { color: #185FA5; font-variant-numeric: tabular-nums; font-size: 14px; }
.ft-dim { color: #9CA3AF; }
.ft-num { font-variant-numeric: tabular-nums; }
.ft-foot { font-size: 11px; color: #9CA3AF; margin-top: 10px; line-height: 1.65; }
.ft-note { font-size: 11px; color: #9CA3AF; margin-top: 10px; line-height: 1.65; }
.ft-sub-title { font-size: 12px; color: #6B7280; margin: 14px 0 8px; }
.ft-two-col { display: grid; grid-template-columns: 1fr 1.1fr; gap: 16px; }
@media (max-width: 1100px) { .ft-two-col { grid-template-columns: 1fr; } }

/* 合成结论条 —— 三态配色与总览页判读条（ao-verdict）同语义同色（方案二，2026-09-19）：
   常态 = 中性灰 / 机会 = 金 / 提醒 = 赭橙 #C2410C。
   徽标自带语义文字（非纯靠颜色），故不另加 ✦/▲ 符号。 */
.ft-temp {
  display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: 6px;
  border: 1px solid #EDEFF2; border-left: 3px solid #8A919C; background: #F3F4F5; margin-bottom: 12px;
}
.ft-temp--opportunity { border-left-color: #C9A227; background: #FAF3DF; }
.ft-temp--caution { border-left-color: #C2410C; background: #FBEAE2; }
.ft-temp-badge {
  font-size: 12px; font-weight: 600; border-radius: 3px; padding: 2px 8px; flex: none;
  background: #E6F1FB; color: #185FA5;
}
.ft-temp--opportunity .ft-temp-badge { background: #F5EBC8; color: #7A5E12; }
.ft-temp--caution .ft-temp-badge { background: #F9DCCB; color: #9A3412; }
.ft-temp-text { font-size: 12px; color: #374151; line-height: 1.7; }

/* 三条线索 */
.ft-clues { display: flex; flex-direction: column; gap: 8px; }
.ft-clue {
  padding: 9px 12px; border-radius: 6px; border: 1px solid #EDEFF2;
  border-left: 3px solid #D4D7DE;
}
.ft-clue--ok { border-left-color: #185FA5; }
.ft-clue--warn { border-left-color: #B45309; background: #FFFBF3; }
.ft-clue--na { border-left-color: #D4D7DE; }
.ft-clue-head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; font-size: 12px; }
.ft-clue-head b { color: #1F2937; }
.ft-clue-tag { font-size: 11px; border-radius: 3px; padding: 1px 6px; }
.ft-clue--ok .ft-clue-tag { background: #E6F1FB; color: #185FA5; }
.ft-clue--warn .ft-clue-tag { background: #FEF3C7; color: #B45309; font-weight: 600; }
.ft-clue--na .ft-clue-tag { background: #F5F7FA; color: #9CA3AF; }
.ft-clue-pct { font-size: 11px; color: #9CA3AF; font-variant-numeric: tabular-nums; }
.ft-clue-read { font-size: 12px; color: #374151; line-height: 1.7; margin-top: 5px; }

/* 月度堆叠柱 */
.ft-bars { max-height: 340px; overflow-y: auto; }
.ft-bars--short { max-height: 300px; }
.ft-bar-row {
  display: grid; grid-template-columns: 62px 1fr 90px 150px;
  align-items: center; gap: 8px; padding: 2px 0;
}
.ft-bar-ym { font-size: 11px; color: #9CA3AF; font-variant-numeric: tabular-nums; }
.ft-bar-track { position: relative; height: 14px; background: #F5F7FA; border-radius: 3px; display: block; }
.ft-bar { display: flex; height: 100%; border-radius: 3px; overflow: hidden; min-width: 2px; }
.ft-seg { display: block; height: 100%; }
/* 权益=主色蓝（关注主体）/ 固收=灰 / 其他=浅灰：管理 UI 不用红绿，方向不再此表达 */
.ft-seg--eq { background: #185FA5; }
.ft-seg--fi { background: #9CA3AF; }
.ft-seg--ot { background: #D4D7DE; }
.ft-bar--rep { background: #185FA5; }
.ft-bar-val { font-size: 11px; color: #374151; font-variant-numeric: tabular-nums; text-align: right; }
.ft-bar-sub { font-size: 11px; color: #9CA3AF; font-variant-numeric: tabular-nums; }
.ft-legend { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; font-size: 12px; color: #6B7280; margin-bottom: 8px; }
.ft-legend-item { display: inline-flex; align-items: center; gap: 5px; }
.ft-dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.ft-dot--eq { background: #185FA5; }
.ft-dot--fi { background: #9CA3AF; }
.ft-dot--ot { background: #D4D7DE; }

.ft-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.ft-table th {
  text-align: left; font-weight: 500; color: #9CA3AF; padding: 6px 8px;
  border-bottom: 1px solid #EDEFF2;
}
.ft-table td { padding: 6px 8px; border-bottom: 1px solid #F5F7FA; color: #374151; }
</style>
