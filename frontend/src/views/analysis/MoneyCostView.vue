<script setup lang="ts">
/**
 * MoneyCostView —— 货币流动性模块（分析研究·资金与情绪）
 *   2026-09-25 领域更名：「钱贵不贵」→「货币流动性」（原名为蓝图A 代号，只覆盖价格一维）
 * 数据：GET /api/analysis/money-cost
 *
 * 落的是设计规范 §1.0 的 ② **自身纵比**（3M 现在处于近一年什么位置）、
 * ① **同类横比**（ON→1W→1M→3M 的利率阶梯）、⑤ **结构分解**（曲线陡/平/倒挂 ——
 * 同一个 3M 利率，曲线形状不同含义完全不同），末尾做 ④ **交叉印证**
 * （政策利率按兵不动 ↔ 市场利率在动；资金价格 ↔ 权益位置）。
 *
 * 2026-09-25 批次 1 新增第四面 **外部约束**（`mc-external`）：美债曲线 / 中美 10Y 利差 /
 * 美元指数 / 全球央行方向。落的是设计规范 §1.0 的 ④ **交叉印证**里最新的一对
 * 「国内松紧 ↔ 外部约束」（内松外紧 / 内外同向，四种组合各有含义）。
 *
 * ⚠️ 三条口径纪律（后端已下发，前端只透传，不要自作主张改写）：
 * 1. **利率绝对值跨期不可比** → 所有"贵不贵"的判断一律走近一年分位，
 *    分位窗口文案由 `scale.label` 下发（见 registry.py 卡片墙契约第 ⑤ 条）。
 * 2. **LPR 与 Shibor 不同轴**：LPR 1Y 在 3.0% 而 Shibor 在 1.4% 附近，
 *    画在一张图里会把三条 Shibor 压成底部一坨 —— 故后端下发两条独立纵轴的序列
 *    （`history` 给 Shibor，`lpr.step` 给政策利率），本视图分两张图渲染。
 * 3. **政策利率与市场利率分属两个观察窗**，它们的差（政策不动而市场在动）本身就是信息，
 *    故单独做成一张"政策 vs 市场"面板，而不是把两个数并排放下就完事。
 * 4. **外部约束的时点与主模块不同轴**（批次 1 实测）：主数据截至日来自 `interbank_rate_daily`
 *    （同步 A 股交易日），而美债/中债走 `bond_profit_daily`（滞后 1~2 个交易日）、
 *    美元指数走 `global_usd_index_daily`（滞后 1 日）。⇒ 本区**自带截至日**（`extAsOfText`），
 *    绝不套用页头的"数据截至"，否则就是把昨收价挂在今天的标题下。
 * 5. **美元指数主口径 = 新浪官方日线**（ICE 口径，1985-11-08 起 10,573 行，与官方实时快照
 *    实测偏差 −0.004%）；自算（人民币中间价交叉汇率）与实时快照降为**两条独立交叉校验口径**。
 *    卡片标题必须写明当前主口径（`dxy.source`），三口径对账表逐行标出各自口径与偏差 ——
 *    ⚠️ 旧文案「美元指数是自算值、不是 ICE 官方 DXY」已被实测推翻，勿回退。
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

/** 外部约束 KPI（后端 external_kpis）—— 与主 KPI 同构，多带 as_of / 区间 / 倾向 */
interface ExtKpi {
  key: string; label: string; value: number | null; unit?: string
  tone?: 'updown' | 'diff' | 'neutral'
  status?: string; hint?: string
  pct?: number | null; scale?: { pct: number; label: string } | null
  highlight?: boolean; anchor?: string
  as_of: string | null; prev_year: number | null
  min_1y: number | null; max_1y: number | null
  chg20: number | null
  /** 该项是否指向「外部收紧」（美债/期差/美元越强、利差越窄越紧） */
  tight: boolean | null
  snapshot?: { date: string; value: number } | null
  deviation_pct?: number | null
  /** 交叉校验口径：最近一个「官方日线 + 自算」同时有值的日期与互比偏差 */
  calc?: { date: string; value: number; dev_pct: number | null } | null
  /** 主口径来源：sina_official = 官方日线；calc_fallback = 官方缺失、回落自算 */
  source?: string
}
interface UsCurveRow {
  term: string; cur: number | null; as_of: string | null; prev_year: number | null
  chg20_bp: number | null; min_1y: number | null; max_1y: number | null
  spread_2y_bp: number | null
}
interface CbRow {
  code: string; name: string; bank: string; date: string; rate: number | null
  change_bp: number | null; last_move_date: string | null; last_move_bp: number | null
  stale_months: number | null
}
interface ExternalBlock {
  items: ExtKpi[]; dxy: ExtKpi | null; curve: UsCurveRow[]
  central_banks: CbRow[]; cb_note: string | null
  reading: { headline: string; detail: string; tone: string
    tight_n: number; total_n: number; combo?: string | null } | null
  as_of_bond: string | null; as_of_us: string | null; as_of_dxy: string | null
  note: string
}

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
const externalKpis = ref<ExtKpi[]>([])
const external = ref<ExternalBlock | null>(null)

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
    externalKpis.value = resp.external_kpis ?? []
    external.value = resp.external ?? null
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

/* ---------------- 外部约束（批次 1，2026-09-25）辅助 ---------------- */

/** 央行利率变动语义：降息=宽松（蓝）/ 加息=收紧（琥珀）/ 维持（灰）。
 *  ⚠️ 刻意不走红绿 —— 红涨绿跌是**行情数字**的约定（规范 §契约④），
 *     利率决议的变动不是「某个资产在涨跌」，染红绿会把它误读成涨跌。 */
function cbTag(bp: number | null): { text: string; cls: string } {
  if (bp == null) return { text: '--', cls: 'mc-tag--na' }
  if (bp < 0) return { text: `降息 ${Math.abs(bp)}bp`, cls: 'mc-tag--ok' }
  if (bp > 0) return { text: `加息 ${bp}bp`, cls: 'mc-tag--warn' }
  return { text: '维持不变', cls: 'mc-tag--flat' }
}

/** 当前值在「近一年高低区间」里的位置（0=区间最低，100=最高）。
 *  ⚠️ 这是**区间位置**而非分位（分位看 KPI 区的刻度条）—— 它只用来一眼看出
 *     「四个期限是不是一起被抬到了近一年高位」。 */
function rangePos(c: UsCurveRow): number {
  if (c.cur == null || c.min_1y == null || c.max_1y == null || c.max_1y === c.min_1y) return 50
  return Math.min(100, Math.max(0, ((c.cur - c.min_1y) / (c.max_1y - c.min_1y)) * 100))
}

/** 口径偏差标签：|偏差| ≤0.3% 视为口径一致（蓝）；≤1.5% 提醒（琥珀）；>1.5% 警示（深红棕）
 *  ⚠️ 三档而非两档的必要：官方日线 vs 自算实测单日最大 2.29%（自算偶发跳点），
 *     若只分两档，这 2.29% 会和 0.1% 的正常波动一起被涂成同一个「提醒」色，看不出严重程度。 */
function devTag(dev: number | null | undefined): { text: string; cls: string } {
  if (dev == null) return { text: '--', cls: 'mc-tag--na' }
  const a = Math.abs(dev)
  const cls = a <= 0.3 ? 'mc-tag--ok' : a <= 1.5 ? 'mc-tag--warn' : 'mc-tag--bad'
  return { text: `偏差 ${dev >= 0 ? '+' : ''}${dev}%`, cls }
}

/** 外部数据的实际截至日（美债/中债与美元指数不同轴 —— 口径必须随数字一起显示） */
const extAsOfText = computed(() => {
  const e = external.value
  if (!e) return ''
  const bits: string[] = []
  if (e.as_of_bond) bits.push(`美债/中债 ${e.as_of_bond}`)
  if (e.as_of_dxy) bits.push(`美元指数 ${e.as_of_dxy}`)
  return bits.join(' · ')
})

/** 美元指数近 20 日变化（带符号；空值给 '--'） */
const dxyChgText = computed(() => {
  const v = external.value?.dxy?.chg20
  return v == null ? '--' : `${v >= 0 ? '+' : ''}${v}`
})
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
        隔夜波动最大、3M 最稳。⚠️ 三条线都是<b>利率水平</b>而非涨跌，
        故不用红绿着色 —— 利率下行不等于"跌"，它意味着资金变便宜。
      </div>
    </NCard>

    <!-- 政策利率：独立纵轴的阶梯图 + 变动点
         原标题「政策利率阶梯（LPR 1Y 3.00% · 已连续 16 个月未动）」把两个读数写进标题，
         而卡内 mc-lpr-sum 一字不差地又显示了一遍 ⇒ 标题只留问题，数字归卡内。 -->
    <NCard id="mc-lpr" size="small" class="mc-card" title="政策利率阶梯：政策动没动？">
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

    <!-- ④ 外部约束（批次 1，2026-09-25）
         为什么这些数属于「货币流动性」而不属于「资金温度」（D6 因果位置划法）：
           本区放的都是**钱的价格**（因）—— 美债收益率是全球贴现率、中美利差决定跨境资金方向、
           美元指数是全球美元总闸门；而「钱有没有真的进到市场里」（汇率背后的外资流向、
           基金发行、产业资本回购）归「资金温度」，那是**果**。
         ⚠️ 遵守上一轮已拍板的「不拆大卡」原则：本区是**大卡内的分区**，卡片墙仍是 3 张 track 卡
           （《货币流动性观测体系设计》§6 批次 3），故这几个 KPI **不标 card_rank**。 -->
    <NCard id="mc-external" size="small" class="mc-card"
           title="外部约束：美元和美债，在收紧还是在放松？">
      <template #header-extra>
        <DrillLink :items="[{ label: '跨市场对照看海外', to: '/analysis/cross-market' }]" />
      </template>
      <div class="mc-hint">
        开放经济下国内流动性受外部硬约束：<b>美债收益率</b>是全球风险资产的贴现率、
        <b>中美利差</b>决定跨境资金的方向、<b>美元指数</b>是全球美元的总闸门。
        只盯 Shibor 会把「外部在收、国内在放」误读成纯宽松。
      </div>

      <KpiCards v-if="externalKpis.length" :items="externalKpis" />

      <!-- 美债曲线：四腿 + 各自在近一年区间里的位置 -->
      <div id="mc-ext-curve" class="mc-ext-block">
        <div class="card-sub">美债曲线 · 四个期限（长端是全市场的贴现基准）</div>
        <table class="mc-table">
          <thead>
            <tr>
              <th>期限</th><th>当前（%）</th><th>一年前（%）</th><th>20 日变化</th>
              <th>近一年区间（%）</th><th>在区间的位置</th><th>相对 2Y</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in external?.curve ?? []" :key="c.term">
              <td class="mc-term">{{ c.term }}</td>
              <td class="mc-num">{{ c.cur ?? '--' }}</td>
              <td class="mc-num mc-dim">{{ c.prev_year ?? '--' }}</td>
              <td class="mc-num mc-dim">
                {{ c.chg20_bp == null ? '--' : (c.chg20_bp >= 0 ? '+' : '') + c.chg20_bp + 'bp' }}
              </td>
              <td class="mc-num mc-dim">{{ c.min_1y ?? '--' }} ~ {{ c.max_1y ?? '--' }}</td>
              <td>
                <div class="mc-bar-wrap">
                  <span class="mc-bar-track">
                    <span class="mc-bar" :style="{ width: rangePos(c) + '%' }" />
                  </span>
                </div>
              </td>
              <td class="mc-num mc-dim">
                {{ c.spread_2y_bp == null ? '--' : (c.spread_2y_bp >= 0 ? '+' : '') + c.spread_2y_bp + 'bp' }}
              </td>
            </tr>
          </tbody>
        </table>
        <div class="card-cap">
          四条腿一起被抬到近一年高位、而 10Y−2Y 却在近一年最平 —— 「收益率高位 + 曲线走平」，
          是紧缩中后段的典型形态。⚠️ 「在区间的位置」是当前值在近一年高低区间里的相对位置，
          <b>不是分位</b>（分位看上方 KPI 的刻度条）。
        </div>
      </div>

      <!-- 美元指数：★官方日线主口径 + 自算/实时快照两条交叉校验 -->
      <div id="mc-ext-dxy" class="mc-ext-block">
        <div class="card-sub">
          美元指数（三口径对账）·
          <span v-if="external?.dxy?.source === 'sina_official'">主口径 = 新浪官方日线（ICE 口径）</span>
          <span v-else>主口径 = 自算回落（官方日线未就绪）</span>
        </div>
        <table class="mc-table">
          <thead>
            <tr><th>口径</th><th>数值</th><th>截至日</th><th>一年前</th><th>20 日变化 / 偏差</th></tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term">
                <b>官方日线</b>（新浪 DINIW · ICE 口径）
              </td>
              <td class="mc-num">{{ external?.dxy?.value ?? '--' }}</td>
              <td class="mc-num mc-dim">{{ external?.as_of_dxy ?? '--' }}</td>
              <td class="mc-num mc-dim">{{ external?.dxy?.prev_year ?? '--' }}</td>
              <td class="mc-num mc-dim">{{ dxyChgText }}</td>
            </tr>
            <tr v-if="external?.dxy?.calc">
              <td class="mc-term">自算（人民币中间价交叉汇率 · 校验口径）</td>
              <td class="mc-num">{{ external.dxy.calc.value }}</td>
              <td class="mc-num mc-dim">{{ external.dxy.calc.date }}</td>
              <td class="mc-num mc-dim">--</td>
              <td>
                <span class="mc-tag" :class="devTag(external.dxy.calc.dev_pct).cls">
                  {{ devTag(external.dxy.calc.dev_pct).text }}
                </span>
              </td>
            </tr>
            <tr v-if="external?.dxy?.snapshot">
              <td class="mc-term">实时快照（标定锚 · 仅当日）</td>
              <td class="mc-num">{{ external.dxy.snapshot.value }}</td>
              <td class="mc-num mc-dim">{{ external.dxy.snapshot.date }}</td>
              <td class="mc-num mc-dim">--</td>
              <td>
                <span class="mc-tag" :class="devTag(external.dxy.deviation_pct).cls">
                  {{ devTag(external.dxy.deviation_pct).text }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="card-cap">
          主口径已从「自算」切到 <b>官方日线</b>：新浪 `NewForexService.getDayKLine` 返回 ICE 口径日线，
          1985-11-08 起 10,573 行（实测与官方实时快照偏差 <b>−0.004%</b>）。
          自算与实时快照保留作<b>独立交叉校验</b> —— 自算与官方日线在 2,380 个共有日互比：
          |偏差| 中位 0.28%、p90 0.74%、最大 2.29%（<b>带符号均值 +0.074% 会低估离散度，勿当精度看</b>）。
          偏差来自机制差（中间价每日 9:15 定盘、基于前一交易日篮子 ⇒ 趋势日滞后约 1 天），
          而单位错是「全体平移」型的 3%+ 偏离 —— 两条口径互比正是为把后者抓出来。
          偏差标签按 |偏差| 分档：≤0.3% 蓝、≤1.5% 琥珀、&gt;1.5% 警示。
        </div>
      </div>

      <!-- 全球央行方向：⚠️ 上游停更，只能读历史方向 -->
      <div class="mc-ext-block">
        <div class="card-sub">全球央行方向 · 最新有效决议</div>
        <table class="mc-table">
          <thead>
            <tr>
              <th>央行</th><th>最新决议日</th><th>利率（%）</th><th>本次决议</th>
              <th>最后一次实际变动</th><th>距今</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in external?.central_banks ?? []" :key="c.code">
              <td class="mc-term">{{ c.bank }}</td>
              <td class="mc-num mc-dim">{{ c.date }}</td>
              <td class="mc-num">{{ c.rate ?? '--' }}</td>
              <td>
                <span class="mc-tag" :class="cbTag(c.change_bp).cls">{{ cbTag(c.change_bp).text }}</span>
              </td>
              <td class="mc-num mc-dim">
                {{ c.last_move_date ?? '--' }}<template v-if="c.last_move_bp != null">
                  （{{ c.last_move_bp > 0 ? '+' : '' }}{{ c.last_move_bp }}bp）</template>
              </td>
              <td class="mc-num mc-dim">{{ c.stale_months == null ? '--' : c.stale_months + ' 个月' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="external?.cb_note" class="mc-warn"><RichText :text="external.cb_note" /></div>
      </div>

      <!-- 内外组合：这才是本区的结论（四项读数 + 与国内松紧的组合判定） -->
      <div v-if="external?.reading" class="mc-cross"
           :class="external.reading.tone === 'caution' ? 'mc-cross--warn' : ''">
        <span class="mc-cross-badge">外部约束 × 国内松紧</span>
        <span class="mc-cross-text">
          <b>{{ external.reading.headline }}</b>
          <template v-if="external.reading.detail">
            <br /><RichText :text="external.reading.detail" />
          </template>
        </span>
      </div>
      <div class="card-cap">
        数据截至：{{ extAsOfText || '--' }}。⚠️ 美债/中债走 `bond_profit_daily`，
        比 A 股日线滞后 1~2 个交易日 —— 故本区单独标注截至日，不套用页头的「数据截至」。
      </div>
      <div class="mc-note"><RichText :text="external?.note ?? ''" /></div>
    </NCard>

    <!-- ④ 交叉印证 ×2
         原标题「交叉印证（政策 ↔ 市场 · 资金价格 ↔ 权益位置）」是两张子卡的清单（功能清单进标题），
         改为一句话问题，清单下沉为 caption。 -->
    <NCard id="mc-cross" size="small" class="mc-card" title="政策与市场、钱与权益，是否互相印证？">
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
      <div class="card-cap">对照的两组：政策利率 ↔ 市场资金价格 · 资金价格 ↔ 权益位置</div>
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
.mc-tag--bad { background: #F6E8E8; color: #791F1F; font-weight: 600; }
.mc-tag--na { background: #F5F7FA; color: #9CA3AF; }
.mc-foot { font-size: 11px; color: #9CA3AF; margin-top: 10px; line-height: 1.65; }
/* 页脚为**模板内静态文案**（非后端下发），故用 <b> 而非 `**`：`**` 只由 RichText 解析，
   写在模板里会原样渲染（2026-09-25 全站扫描实测漏点）。 */
.mc-foot b { color: #1F2937; font-weight: 600; }
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
/* 外部约束分区（2026-09-25 批次 1）：同一张大卡内的子块，用虚线分隔。
   卡内 KpiCards 自带白底描边，在 NCard 白底上仍可见（KpiCards 的 .kpi-card 有边框）。 */
.mc-ext-block { margin-top: 14px; padding-top: 12px; border-top: 1px dashed #EDEFF2; }
.mc-ext-block .mc-table { margin-top: 2px; }
.mc-ext-block .card-cap b { color: #1F2937; font-weight: 600; }
/* 预警块（上游停更 / 口径风险）：琥珀系，与 .mc-tag--warn 同族；
   ⚠️ 不用亮红 —— 项目配色铁律：失败用深红棕、警告用琥珀（见颜色体系规范）。 */
.mc-warn {
  font-size: 11px; line-height: 1.7; color: #7A4A0B; background: #FAEEDA;
  border-left: 3px solid #B45309; border-radius: 4px; padding: 8px 10px; margin-top: 10px;
}
.mc-warn :deep(strong) { color: #7A2E0B; }
</style>
