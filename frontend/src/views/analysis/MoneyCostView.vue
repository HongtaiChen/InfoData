<script setup lang="ts">
/**
 * MoneyCostView —— 钱贵不贵模块（分析研究·资金与情绪）
 * 数据：GET /api/analysis/money-cost
 *
 * 落的是设计规范 §1.0 的 ② **自身纵比**（3M 现在处于近一年什么位置）、
 * ① **同类横比**（ON→1W→1M→3M 的利率阶梯）、⑤ **结构分解**（曲线陡/平/倒挂 ——
 * 同一个 3M 利率，曲线形状不同含义完全不同），末尾做 ④ **交叉印证**
 * （政策利率按兵不动 ↔ 市场利率在动；资金价格 ↔ 权益位置）。
 *
 * ⚠️ 三条口径纪律（后端已下发，前端只透传，不要自作主张改写）：
 * 1. **利率绝对值跨期不可比** → 所有"贵不贵"的判断一律走近一年分位，
 *    分位窗口文案由 `scale.label` 下发（见 registry.py 卡片墙契约第 ⑤ 条）。
 * 2. **LPR 与 Shibor 不同轴**：LPR 1Y 在 3.0% 而 Shibor 在 1.4% 附近，
 *    画在一张图里会把三条 Shibor 压成底部一坨 —— 故后端下发两条独立纵轴的序列
 *    （`history` 给 Shibor，`lpr.step` 给政策利率），本视图分两张图渲染。
 * 3. **政策利率与市场利率分属两个观察窗**，它们的差（政策不动而市场在动）本身就是信息，
 *    故单独做成一张"政策 vs 市场"面板，而不是把两个数并排放下就完事。
 */
import { computed, onMounted, ref } from 'vue'
import { NCard, NDatePicker, NSelect, NSpin } from 'naive-ui'
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
interface CurveRow {
  term: string; cur: number | null; prev_year: number | null
  min_1y: number | null; max_1y: number | null; spread_bp: number | null
}
interface LprEvent { date: string; lpr_1y: number | null; lpr_5y: number | null }

const loading = ref(false)
const asOf = ref('')
const baseDate1y = ref('')
const isReplay = ref(false)
const kpis = ref<Kpi[]>([])
const curve = ref<CurveRow[]>([])
const history = ref<{ dates: string[]; series: { name: string; values: (number | null)[]; color: string }[] }>(
  { dates: [], series: [] })
const lpr = ref<{ lpr_1y: number | null; lpr_5y: number | null; since: string | null
  prev: { date: string; lpr_1y: number | null; lpr_5y: number | null } | null
  idle_months: number | null; events: LprEvent[]
  step: { dates: string[]; values: (number | null)[]; values_5y: (number | null)[] } } | null>(null)
const policyMarket = ref<{ lpr_idle_months: number | null; lpr_since: string | null
  market_amplitude_bp: number | null; reading: string | null } | null>(null)
const equityCross = ref<{ bench_pos_pct: number | null; bench_date: string | null
  reading: string | null; level?: string } | null>(null)
const median1y = ref<number | null>(null)
const note = ref('')

const replayTs = ref<number | null>(null)
const trendDays = ref(500)
const trendOptions = [
  { label: '近 250 日', value: 250 },
  { label: '近 500 日', value: 500 },
  { label: '近 1000 日', value: 1000 },
]

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = { trend_days: trendDays.value }
    if (replayTs.value) {
      const d = new Date(replayTs.value)
      params.as_of = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    }
    const resp: any = await api.get('/analysis/money-cost', { params })
    asOf.value = resp.as_of ?? ''
    baseDate1y.value = resp.base_date_1y ?? ''
    isReplay.value = !!resp.is_replay
    note.value = resp.note ?? ''
    median1y.value = resp.median_1y ?? null
    kpis.value = (resp.kpis ?? []).map((k: Kpi) => ({ ...k, tone: k.tone ?? 'neutral' }))
    curve.value = resp.curve ?? []
    history.value = resp.history ?? { dates: [], series: [] }
    lpr.value = resp.lpr ?? null
    policyMarket.value = resp.policy_vs_market ?? null
    equityCross.value = resp.equity_cross ?? null
  } catch (e) {
    console.error('[money-cost]', e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

/** LPR 阶梯图的单序列（政策利率是台阶状，画成折线天然呈阶梯） */
const lprSeries = computed(() => {
  const st = lpr.value?.step
  if (!st) return []
  return [
    { name: 'LPR 1Y', values: st.values, color: '#185FA5' },
    { name: 'LPR 5Y', values: st.values_5y, color: '#9CA3AF' },
  ]
})

/** 期限结构：相对隔夜的利差条宽（上限取最大利差，避免极端值把其它行压没） */
const maxSpread = computed(() =>
  Math.max(0.1, ...curve.value.map((c) => Math.abs(c.spread_bp ?? 0))))

function spreadWidth(v: number | null): string {
  if (v == null) return '0%'
  return `${Math.min(100, (Math.abs(v) / maxSpread.value) * 100)}%`
}
/** 期限结构形状的定性（倒挂是流动性紧张信号，必须在表格里立刻可见） */
function shapeTag(v: number | null): { text: string; cls: string } {
  if (v == null) return { text: '--', cls: 'mc-tag--na' }
  if (v < 0) return { text: '倒挂', cls: 'mc-tag--warn' }
  if (v <= 2) return { text: '极平', cls: 'mc-tag--flat' }
  return { text: '正常', cls: 'mc-tag--ok' }
}
const lprEventsDesc = computed(() => [...(lpr.value?.events ?? [])].reverse())
</script>

<template>
  <NSpin :show="loading">
    <!-- ② 参数区 -->
    <div class="mc-params">
      <span class="mc-asof">
        数据截至 <b>{{ asOf || '--' }}</b>
        <span class="mc-sub">（一年前基准日 {{ baseDate1y || '--' }}，3M 近一年中位
          <b>{{ median1y ?? '--' }}%</b>）</span>
        <span v-if="isReplay" class="mc-replay">历史回放</span>
      </span>
      <div class="mc-params-right">
        <NDatePicker v-model:value="replayTs" type="date" size="small" clearable
                     placeholder="回放某交易日" style="width: 148px" @update:value="load" />
        <NSelect v-model:value="trendDays" :options="trendOptions" size="tiny"
                 style="width: 130px" @update:value="load" />
      </div>
    </div>

    <!-- ③ 结论区 -->
    <KpiCards :items="kpis" />

    <!-- ① + ⑤ 期限结构：同类横比 + 结构分解 -->
    <NCard id="mc-curve" size="small" class="mc-card"
           title="银行间资金价格期限结构（ON → 1W → 1M → 3M，相对隔夜利差）">
      <template #header-extra>
        <DrillLink :items="[{ label: '市场风向看股债性价比', to: '/analysis/market-wind' }]" />
      </template>
      <div class="mc-hint">
        同样一个 3M 利率，曲线形状不同含义完全不同：陡说明短端资金充裕、机构愿意拉久期；
        极平说明市场预期资金持续宽松、短端没有溢价要求；倒挂（3M 比隔夜还便宜）是流动性紧张的信号。
      </div>
      <table class="mc-table">
        <thead>
          <tr>
            <th>期限</th><th>当前（%）</th><th>一年前（%）</th><th>近一年区间（%）</th>
            <th>相对隔夜</th><th>形状</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in curve" :key="c.term">
            <td class="mc-term">{{ c.term }}</td>
            <td class="mc-num">{{ c.cur ?? '--' }}</td>
            <td class="mc-num mc-dim">{{ c.prev_year ?? '--' }}</td>
            <td class="mc-num mc-dim">{{ c.min_1y ?? '--' }} ~ {{ c.max_1y ?? '--' }}</td>
            <td>
              <div class="mc-bar-wrap">
                <span class="mc-bar-track">
                  <span class="mc-bar" :class="{ 'is-neg': (c.spread_bp ?? 0) < 0 }"
                        :style="{ width: spreadWidth(c.spread_bp) }" />
                </span>
                <span class="mc-bar-val">{{ c.spread_bp == null ? '--' : (c.spread_bp >= 0 ? '+' : '') + c.spread_bp + 'bp' }}</span>
              </div>
            </td>
            <td><span class="mc-tag" :class="shapeTag(c.spread_bp).cls">{{ shapeTag(c.spread_bp).text }}</span></td>
          </tr>
        </tbody>
      </table>
      <div class="mc-foot">
        「相对隔夜」以 ON 为锚。⚠️ ON 自身的利差恒为 0，作锚位；期限越靠后利差越能说明资金的长短偏好。
      </div>
    </NCard>

    <!-- 主时序：三条 Shibor（同轴可比，均为 %） -->
    <NCard id="mc-trend" size="small" class="mc-card"
           :title="`Shibor 期限利率走势（截至 ${asOf || '--'}）`">
      <DualLineTrend :dates="history.dates" :series="history.series" height="300px" />
      <div class="mc-foot">
        隔夜波动最大、3M 最稳。⚠️ 三条线都是**利率水平**而非涨跌，
        故不用红绿着色 —— 利率下行不等于"跌"，它意味着资金变便宜。
      </div>
    </NCard>

    <!-- 政策利率：独立纵轴的阶梯图 + 变动点 -->
    <NCard id="mc-lpr" size="small" class="mc-card"
           :title="`政策利率阶梯（LPR 1Y ${lpr?.lpr_1y ?? '--'}% · 已连续 ${lpr?.idle_months ?? '--'} 个月未动）`">
      <DualLineTrend v-if="lpr?.step" :dates="lpr.step.dates" :series="lprSeries" height="220px" />
      <div class="mc-lpr-sum">
        <span>当前 1Y <b class="mc-num-strong">{{ lpr?.lpr_1y ?? '--' }}%</b></span>
        <span>5Y <b class="mc-num-strong">{{ lpr?.lpr_5y ?? '--' }}%</b></span>
        <span v-if="lpr?.since">本轮自 <b>{{ lpr.since }}</b> 起未动</span>
        <!-- ⚠️ 口径坑：lpr.prev.date 是「旧取值的最后一日」而非「变动生效日」（LPR 在库里按交易日
             前值填充，故前一日仍记为旧值 2025-05-19）。此处一律用 lpr.since（变动生效日）作日期，
             以便与下方「生效日」变动表逐行对齐，不再出现 05-19 / 05-20 同日两名。 -->
        <span v-if="lpr?.prev && lpr?.since" class="mc-dim">
          上次调整 {{ lpr.since }}：1Y {{ lpr.prev.lpr_1y }}% → {{ lpr.lpr_1y }}%<template
            v-if="lpr.prev.lpr_5y != null && lpr.lpr_5y != null">｜5Y {{ lpr.prev.lpr_5y }}% → {{ lpr.lpr_5y }}%</template>
        </span>
      </div>
      <table class="mc-table mc-table--events">
        <thead>
          <tr><th>生效日</th><th>LPR 1Y（%）</th><th>LPR 5Y（%）</th><th>相对上一次</th></tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in lprEventsDesc" :key="e.date">
            <td>{{ e.date }}</td>
            <td class="mc-num">{{ e.lpr_1y ?? '--' }}</td>
            <td class="mc-num">{{ e.lpr_5y ?? '--' }}</td>
            <td class="mc-num"
                :class="i === lprEventsDesc.length - 1 ? 'mc-dim' : ''">
              {{ i === lprEventsDesc.length - 1 ? '—' :
                ((e.lpr_1y ?? 0) - (lprEventsDesc[i + 1]?.lpr_1y ?? e.lpr_1y ?? 0)).toFixed(2) + ' pp' }}
            </td>
          </tr>
        </tbody>
      </table>
      <div class="mc-foot">
        ⚠️ LPR 与 Shibor 报价机制不同（政策报价 vs 市场成交），故不画在同一张图里，也不参与期限利差计算。
      </div>
    </NCard>

    <!-- ④ 交叉印证 ×2 -->
    <NCard id="mc-cross" size="small" class="mc-card"
           title="交叉印证（政策 ↔ 市场 · 资金价格 ↔ 权益位置）">
      <div v-if="policyMarket?.reading" class="mc-cross">
        <span class="mc-cross-badge">政策 vs 市场</span>
        <span class="mc-cross-text">
          <RichText :text="policyMarket.reading" />
          <span class="mc-dim">
            （政策未动 {{ policyMarket.lpr_idle_months }} 个月，同期 3M 振幅
            {{ policyMarket.market_amplitude_bp }}bp）
          </span>
        </span>
      </div>
      <div v-if="equityCross?.reading" class="mc-cross"
           :class="equityCross.level === 'opportunity' ? 'mc-cross--gold' :
                   equityCross.level === 'caution' ? 'mc-cross--warn' : ''">
        <span class="mc-cross-badge">资金 × 权益位置</span>
        <span class="mc-cross-text">
          <RichText :text="equityCross.reading" />
          <span class="mc-dim">（中证全指位置 {{ equityCross.bench_pos_pct }}% 分位，{{ equityCross.bench_date }}）</span>
        </span>
      </div>
      <div class="mc-note"><RichText :text="note" /></div>
    </NCard>
  </NSpin>
</template>

<style scoped>
.mc-params { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.mc-asof { font-size: 13px; color: #6B7280; }
.mc-asof b { color: #1F2937; }
.mc-sub { color: #9CA3AF; }
.mc-replay {
  margin-left: 6px; font-size: 11px; color: #185FA5;
  background: #E6F1FB; border-radius: 3px; padding: 1px 5px;
}
.mc-params-right { display: flex; gap: 8px; align-items: center; }
.mc-card { margin-bottom: 12px; scroll-margin-top: 12px; }
.mc-hint { font-size: 12px; color: #6B7280; line-height: 1.7; margin-bottom: 10px; }
.mc-hint :deep(strong) { color: #1F2937; }
.mc-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mc-table th {
  text-align: left; font-weight: 500; color: #9CA3AF; padding: 6px 8px;
  border-bottom: 1px solid #EDEFF2;
}
.mc-table td { padding: 7px 8px; border-bottom: 1px solid #F5F7FA; color: #374151; }
.mc-term { font-weight: 600; color: #185FA5; }
.mc-num { font-variant-numeric: tabular-nums; }
.mc-dim { color: #9CA3AF; }
.mc-num-strong { color: #185FA5; font-variant-numeric: tabular-nums; }
.mc-bar-wrap { display: flex; align-items: center; gap: 8px; min-width: 130px; }
/* ⚠️ 柱子必须放在独立 track（flex:1 + min-width:0）里，不能直接当 flex item 用百分比宽度：
   否则「最大利差」那行柱宽吃满 100%，会把右侧数值标签挤到 8px 宽，文字逐字竖排、
   行高从 34px 撑到 121px 并越界压到「形状」列（2026-09-19 实测 1W 行 +4.5bp 复现）。 */
.mc-bar-track { flex: 1 1 auto; min-width: 40px; }
.mc-bar {
  display: block; height: 8px; border-radius: 2px; background: #185FA5; min-width: 2px;
}
.mc-bar.is-neg { background: #B45309; }
.mc-bar-val {
  flex: 0 0 auto; white-space: nowrap; font-size: 11px; color: #6B7280;
  font-variant-numeric: tabular-nums; text-align: right;
}
.mc-tag { font-size: 11px; border-radius: 3px; padding: 1px 6px; }
.mc-tag--ok { background: #E6F1FB; color: #185FA5; }
.mc-tag--flat { background: #F5F7FA; color: #6B7280; }
.mc-tag--warn { background: #FAEEDA; color: #B45309; font-weight: 600; }
.mc-tag--na { background: #F5F7FA; color: #9CA3AF; }
.mc-foot { font-size: 11px; color: #9CA3AF; margin-top: 10px; line-height: 1.65; }
.mc-lpr-sum {
  display: flex; gap: 18px; flex-wrap: wrap; align-items: baseline;
  font-size: 12px; color: #6B7280; margin: 12px 0 10px;
  padding-bottom: 10px; border-bottom: 1px dashed #EDEFF2;
}
.mc-table--events td, .mc-table--events th { padding: 5px 8px; }
.mc-cross {
  display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: 6px;
  border: 1px solid #EDEFF2; border-left: 3px solid #185FA5; margin-bottom: 8px;
}
.mc-cross--gold { border-left-color: #C9A227; background: #FAF3DF; }
.mc-cross--warn { border-left-color: #B45309; background: #FAEEDA; }
.mc-cross-badge {
  font-size: 11px; border-radius: 3px; padding: 1px 6px; flex: none; margin-top: 1px;
  background: #E6F1FB; color: #185FA5;
}
.mc-cross--gold .mc-cross-badge { background: #F5EBC8; color: #7A5E12; }
.mc-cross--warn .mc-cross-badge { background: #FEF3C7; color: #B45309; }
.mc-cross-text { font-size: 12px; color: #374151; line-height: 1.7; }
.mc-note { font-size: 11px; color: #9CA3AF; margin-top: 10px; line-height: 1.65; }
</style>
