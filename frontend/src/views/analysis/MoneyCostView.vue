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
import { NCard, NCollapse, NCollapseItem, NDatePicker, NSelect, NSpin } from 'naive-ui'
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
  /** 固定小数位（只格式化、不换算）—— 货币总量区用，让「5.80」与「14.75」精度一致 */
  fmt_digits?: number
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

/* ---------------- 货币总量（数量维度，2026-09-26 步骤 1）---------------- */
/** 全部金额已由后端**归一到万亿美元**（D5=A）；前端只格式化，不做任何换算。 */
interface QtyLeg { total: string[]; loans: string[]; bonds: string[] }
interface QtyGlobal {
  period: string | null; total_tn: number | null; total_yoy: number | null
  total_yoy_chg: number | null; loans_tn: number | null; loans_yoy: number | null
  bonds_tn: number | null; bonds_yoy: number | null; bond_share: number | null
  xcheck_pct: number | null; trend: { period: string; total_tn: number | null }[]
  legs?: QtyLeg
}
interface QtyFed {
  as_of: string | null; tn: number | null
  chg_4w_pct: number | null; chg_13w_pct: number | null; chg_52w_pct: number | null
}
interface QtyNet {
  as_of: string | null; tn: number | null; fed_tn: number | null
  tga_tn: number | null; onrrp_tn: number | null
  tga_yi: number | null; onrrp_yi: number | null
  chg_13_pct: number | null; n_days: number
}
interface QtyUsMoney {
  as_of: string | null; m2_tn: number | null; m2_bn: number | null; m2_yoy: number | null
  m2_yoy_d3: number | null; m1_tn: number | null; m1_bn: number | null
  m1_yoy: number | null; base_tn: number | null
}
interface QtyEu {
  as_of: string | null; m3_tn: number | null; m3_yoy: number | null; m3_yoy_d3: number | null
  m2_tn: number | null; m1_tn: number | null; eur_usd: number | null
}
interface QtyStructRow {
  key: string; label: string; last_month: string | null
  value: number | null; as_of: string | null; stale: boolean
}
interface QtyCnMoney {
  as_of: string | null; m2_tn: number | null; m2_yi: number | null; m2_yoy: number | null
  m2_yoy_d3: number | null; m1_tn: number | null; m1_yi: number | null
  m1_yoy: number | null; m0_yi: number | null
  gap: number | null; gap_d3: number | null; gap_word: string | null
  usd_cny: number | null; struct: QtyStructRow[]
}
interface QtyReserve {
  as_of: string | null; fx_tn: number | null; fx_usd_yi: number | null
  fx_yoy: number | null; gold_oz: number | null; gold_chg: number | null
  gold_streak: number | null
}
interface QtyOmo {
  section: string; label: string; as_of: string; days: number; amount_sum_yi: number | null
  rows: { date: string; op_type: string; tenor_days: number | null
    rate: number | null; amount_yi: number | null }[]
}
interface QtyReading {
  headline: string; detail: string; tone: 'loose' | 'tight' | 'mixed'
  expand_n: number; tight_n: number; known_n: number
  layers: { name: string; metric: string; dir: number; delta: number | null
    unit: string; level: number | null }[]
}
interface QuantityBlock {
  items: QtyKpi[]
  global: QtyGlobal
  us: { fed: QtyFed; net_liquidity: QtyNet; money: QtyUsMoney }
  eu: QtyEu
  cn: { money: QtyCnMoney
    reserve: QtyReserve
    cb_balance: { as_of: string | null; fx_yi: number | null; gold_yi: number | null
      foreign_yi: number | null; reserve_money_yi: number | null; total_yi: number | null }
    omo: QtyOmo[] }
  fx: { usd_cny: number | null; eur_usd: number | null; as_of: string | null; source: string }
  reading: QtyReading
  as_of_bis: string | null; as_of_fed: string | null; as_of_net_liq: string | null
  as_of_us_money: string | null; as_of_eu: string | null; as_of_cn_money: string | null
  as_of_cn_reserve: string | null; as_of_cn_cb: string | null; as_of_omo: string | null
  unit_target: string
  note: string
}
/** 数量类 KPI：value 已是「万亿美元」，unit 由后端给 → 前端只需补两位小数 */
type QtyKpi = Kpi

/** 模块级结论（后端 `verdict`）—— 2026-09-26 P1 起渲染到页头。
 *  ⚠️ 接口早已下发该字段（money_cost.py 返回段），但详情页此前 `grep verdict` 零命中、从未渲染 ⇒
 *     「结论得滚到第 5.2 屏才看到」。本视图只是把它搬到页头，**零后端改动**。
 *  tone 与卡片墙判读条同源：normal=蓝 / opportunity=金 / caution=琥珀 / mixed=分化（P2 起有专属蓝）。 */
interface ModuleVerdict {
  headline: string
  detail?: string
  tone?: 'normal' | 'opportunity' | 'caution' | 'mixed'
}

/** 页内锚点（P1 · F2）：四环 + 交叉印证。
 *  🔴 每个 id 必须真实存在于本模板 —— `getElementById(...)?.` 在 id 缺失时**静默无反应、零报错**，
 *     故新增/改名后必须重跑 `_scratch/_verify_anchors_dom.js`（P0 自检）。 */
const SECTIONS: { id: string; label: string }[] = [
  { id: 'mc-lpr', label: '① 央行操作' },
  { id: 'mc-curve', label: '② 银行间定价' },
  { id: 'mc-quantity', label: '③ 信用派生' },
  { id: 'mc-external', label: '④ 对外与资产' },
  { id: 'mc-cross', label: '交叉印证' },
]

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
const quantityKpis = ref<QtyKpi[]>([])
const quantity = ref<QuantityBlock | null>(null)
/** 模块级结论（页头结论条，P1 · 2026-09-26）—— 后端早已下发 `verdict`，此前详情页未渲染 */
const verdict = ref<ModuleVerdict | null>(null)

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
    // 数量维度（步骤 1）：金额已在后端归一为「万亿美元」，此处只补两位小数（fmt_digits）
    quantityKpis.value = (resp.quantity_kpis ?? []).map((k: Kpi) => ({
      ...k, tone: k.tone ?? 'neutral', fmt_digits: 2,
    }))
    quantity.value = resp.quantity ?? null
    verdict.value = resp.verdict ?? null
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

/* ---------------- 货币总量（数量维度）辅助 ----------------
 * 🔴 本区所有金额**已经在后端归一到「万亿美元」**（D5=A 定稿：跨区统一折美元）。
 *    前端的职责只有两件：**格式化**（补小数位 / 加千分位）与**透传口径文字**，
 *    **绝不做任何换算** —— 在这里再乘一次汇率，就会出现「后端折了、前端又折」的双重折算。
 */

/** 万亿美元读数：统一两位小数（后端给的是 number，「5.8」要显示成「5.80」） */
function fmtTn(v: number | null | undefined): string {
  return v == null ? '--' : Number(v).toFixed(2)
}
/** 带符号百分比（同比 / 方向变化） */
function pct(v: number | null | undefined): string {
  return v == null ? '--' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}
/** 带符号 pp（同比的方向变化） */
function pp(v: number | null | undefined): string {
  return v == null ? '--' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}pp`
}
/** 千分位数字（原生单位读数，如「23,342.8 十亿美元」「7,673 万盎司」） */
function num(v: number | null | undefined, d = 2): string {
  if (v == null) return '--'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d })
}
/** BIS 官维组合串：由后端 `legs` 下发，前端只拼展示文本（**不猜维度取值**） */
function legText(k: 'total' | 'loans' | 'bonds'): string {
  const l = quantity.value?.global?.legs?.[k]
  return l ? `3P × N × ${l[0]} × I × ${l[1]} × USD` : '--'
}
/** 子项占比（只在同口径内部算，不跨口径） */
function shareOf(v: number | null | undefined, tot: number | null | undefined): string {
  if (v == null || tot == null || !tot) return '--'
  return `${((v / tot) * 100).toFixed(1)}%`
}

/** 判读条配色：同向扩张 → 金（机会侧）；同向收缩 → 琥珀；背离 → 蓝（中性） */
const qtyToneClass = computed(() => {
  const t = quantity.value?.reading?.tone
  return t === 'loose' ? 'mc-cross--gold' : t === 'tight' ? 'mc-cross--warn' : ''
})

/** 央行 OMO 操作类型中文（源是英文枚举） */
const OP_LABEL: Record<string, string> = {
  reverse_repo: '逆回购',
  cbb: '央行票据',
  outright_reverse_repo: '买断式逆回购',
  other: '其它工具',
}
function opLabel(t: string): string {
  return OP_LABEL[t] ?? t
}

/** 数量维度各组的实际截至日（四个源不同轴 —— 口径必须随数字一起显示） */
const qtyAsOfText = computed(() => {
  const q = quantity.value
  if (!q) return ''
  const bits: string[] = []
  if (q.as_of_bis) bits.push(`BIS ${q.as_of_bis}`)
  if (q.as_of_fed) bits.push(`美联储 ${q.as_of_fed}`)
  if (q.as_of_net_liq) bits.push(`A5 ${q.as_of_net_liq}`)
  if (q.as_of_us_money) bits.push(`美国 M2 ${q.as_of_us_money}`)
  if (q.as_of_eu) bits.push(`欧元区 ${q.as_of_eu}`)
  if (q.as_of_cn_money) bits.push(`中国 M2 ${q.as_of_cn_money}`)
  if (q.as_of_cn_reserve) bits.push(`外储 ${q.as_of_cn_reserve}`)
  if (q.as_of_omo) bits.push(`OMO ${q.as_of_omo}`)
  return bits.join(' · ')
})

/* ---------------- P1（2026-09-26）：页头结论条 + 页内锚点 ---------------- */

/** 结论条配色：蓝骨金魂纪律 —— 金只给「机会侧」、琥珀给提醒、蓝为常态；
 *  ⚠️ 刻意不出现红绿（红涨绿跌只属于行情数字与 K 线）。mixed（四层方向分化）走蓝，
 *     与卡片墙 `.ao-verdict--mixed`（P2 新增）同义。 */
const headToneClass = computed(() => {
  const t = verdict.value?.tone
  return t === 'opportunity' ? 'mc-headline--gold'
    : t === 'caution' ? 'mc-headline--warn'
      : 'mc-headline--blue'
})

/** 页内跳转：✅ 用 `scrollIntoView`。
 *  2026-09-26 实测：点击前目标 `top=3632` → 点击后 `top=0`，滚动宿主
 *  `.n-layout-scroll-container` 的 `scrollTop` 0→3632 ⇒ **完全有效**，且 `.mc-card` 上的
 *  `scroll-margin-top:12px` 顶栏补偿一并生效。
 *  🔴 反面警示：早期工程附录写「`scrollIntoView()` 对它无效、必须自己算 `scrollTop`」是
 *     **被实测推翻的错结论**（已在该文档就地加勘误）—— 照做会丢掉顶栏补偿、标题被顶栏遮住。 */
function goSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <NSpin :show="loading">
    <!-- 结论条（P1 · 2026-09-26）：把接口**早已下发、却从未被本页渲染**的 `verdict` 搬到页头。
         「结论先行」不是设计偏好 —— 晨会速览场景只有 30 秒（使用场景 S1），
         而它原来埋在 y≈5830（第 5.2 屏）⇒ 30 秒全花在滚动上。
         ⚠️ 不得与底部 `mc-cross` 重复全文：`mc-cross` 只做「交叉印证」，此处只放模块级结论。
         🔴 零后端改动 —— 数据一直在接口里（money_cost.py 返回段的 `verdict`）。 -->
    <div v-if="verdict" id="mc-headline" class="mc-headline" :class="headToneClass">
      <span class="mc-headline-badge">结论</span>
      <span class="mc-headline-text">
        <b class="mc-headline-title">{{ verdict.headline }}</b>
        <span v-if="verdict.detail" class="mc-headline-detail"><RichText :text="verdict.detail" /></span>
      </span>
    </div>

    <!-- 页内锚点导航（P1 · F2）：5.5 屏的页面必须能直达某一环（使用场景 S2「按环节定位」）。
         ⚠️ 每个 id 都必须真实存在 —— `getElementById(...)?.scrollIntoView(...)` 在 id 缺失时
            静默无反应、零报错，故改动后必须重跑 `_scratch/_verify_anchors_dom.js`（P0 自检）。 -->
    <nav class="mc-nav">
      <span class="mc-nav-label">直达</span>
      <button v-for="s in SECTIONS" :key="s.id" type="button" class="mc-nav-item"
              @click="goSection(s.id)">{{ s.label }}</button>
    </nav>

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

    <!-- 口径与更新（P1 · 2026-09-26）：页头 `desc` 从 ~150 字功能清单瘦身为一句判断句后，
         完整口径**移到这里而非删除** —— 金融需求 R7 的硬要求是「瘦身但可查性不得丢失」，
         使用场景 S3（月度策略，10 分钟）需要能复核口径与数据源。 -->
    <NCollapse class="mc-scope">
      <NCollapseItem title="口径与更新（四环 / 五个切面 / 数据源 / 单位）" name="scope">
        <div class="mc-scope-body">
          <p>
            <b>这页回答什么</b>：<b>现在钱松还是紧</b>（模块级结论见页头），并能沿
            <b>① 央行操作 → ② 银行间定价 → ③ 信用派生 → ④ 对外与资产</b> 逐环下钻，
            定位「哪个环节出问题」；底部「交叉印证」回答「政策与市场、钱与权益是否互相印证」。
          </p>
          <p>
            <b>五个切面与数据源</b>：<b>价格</b>（Shibor 期限结构 / <code>interbank_rate_daily</code>）、
            <b>预期</b>（同表的期限利差形状，已并入价格卡）、<b>政策</b>（LPR / <code>lpr</code>）、
            <b>外部约束</b>（美债曲线 · 中美 10Y 利差 · 美元指数 / <code>bond_profit_daily</code>
            · <code>global_usd_index_daily</code>）、<b>数量</b>（BIS 境外美元信贷 / 美联储总资产与
            美元净流动性 A5 / 美国 M2 / 欧元区 M3 / 中国 M2、外储与黄金 / 央行 OMO 操作量）。
          </p>
          <p>
            <b>单位与口径</b>：跨国金额<b>已由后端统一折美元（万亿美元）</b>，前端只做格式化、不做任何换算；
            <b>红涨绿跌只属于行情数字</b>，本页管理 UI 不用红绿；存量水位类指标<b>不做近一年分位</b>
            （单调上行 ⇒ 伪信号），改用同比与同比的方向。
          </p>
          <p>
            <b>更新</b>：每工作日 19:35（银行间市场收盘后）。⚠️ 外部约束与数量维度<b>各自不同轴</b>
            （美债滞后 1~2 个交易日、美元指数滞后 1 日、BIS 为季度 T+1 季、M2 家族为月度）
            ⇒ 各区块自带截至日，<b>绝不套用页头「数据截至」</b>。
          </p>
        </div>
      </NCollapseItem>
    </NCollapse>

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
      <details class="mc-fold">
        <summary>期限结构明细表 · ON / 1W / 1M / 3M 的当前值 · 一年前 · 近一年区间 · 相对隔夜利差</summary>
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
      </details>
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
      <details class="mc-fold">
        <summary>LPR 历次变动明细 · 生效日 / 1Y / 5Y / 相对上一次（共 {{ lprEventsDesc.length }} 次）</summary>
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
      </details>
      <div class="mc-foot">
        ⚠️ LPR 与 Shibor 报价机制不同（政策报价 vs 市场成交），故不画在同一张图里，也不参与期限利差计算。
      </div>
    </NCard>

    <!-- ④ 外部约束（批次 1，2026-09-25；P3 · 2026-09-26 上卡）
         为什么这些数属于「货币流动性」而不属于「资金温度」（因果位置划法）：
           本区放的都是**钱的价格**（因）—— 美债收益率是全球贴现率、中美利差决定跨境资金方向、
           美元指数是全球美元总闸门；而「钱有没有真的进到市场里」（汇率背后的外资流向、
           基金发行、产业资本回购）归「资金温度」，那是**果**。
         ⚠️ **变更说明**（原「不拆大卡 · 不标 card_rank」的做法已由决策 D6=(a) 取代）：
           本区三个代表读数**已上卡片墙**（新增「外部约束」卡 q5 =
           cn_us_10y / us_10y / dxy ⇒ 利差吸引力 + 全球贴现率 + 美元总闸门）。 -->
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

      <!-- 美债曲线：四腿 + 各自在近一年区间里的位置
           D7=(b′)（2026-09-26）：F4 原文指定的「美债曲线全表」默认折叠。
           折叠的只是**逐列明细数据**，本块的子标题 + KPI 读数（美债 10Y / 中美 10Y 利差）
           + 下方口径说明仍然可见 ⇒ 锚点 `mc-ext-curve` 跳过来不是空白（红线 8）。 -->
      <div id="mc-ext-curve" class="mc-ext-block">
        <div class="card-sub">美债曲线 · 四个期限（长端是全市场的贴现基准）</div>
        <details class="mc-fold">
          <summary>美债曲线全表 · 四期限 × 7 列（当前 / 一年前 / 20 日变化 / 近一年区间 / 区间位置 / 相对 2Y）</summary>
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
        </details>
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
        <details class="mc-fold">
          <summary>三口径对账明细 · 官方日线 / 自算交叉汇率 / 实时快照（数值 · 截至日 · 一年前 · 偏差）</summary>
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
        </details>
        <!-- P4：三条口径的来源与互比结论属「证据层」，默认折叠。
             D7=(b′)：三口径对账**表**本身也已并入折叠（原「表格仍可见」的写法已改）。 -->
        <details class="mc-fold">
          <summary>三条口径的来源与互比结论（自算 / 实时快照 / 官方日线）</summary>
        <div class="card-cap">
          主口径已从「自算」切到 <b>官方日线</b>：新浪 `NewForexService.getDayKLine` 返回 ICE 口径日线，
          1985-11-08 起 10,573 行（实测与官方实时快照偏差 <b>−0.004%</b>）。
          自算与实时快照保留作<b>独立交叉校验</b> —— 自算与官方日线在 2,380 个共有日互比：
          |偏差| 中位 0.28%、p90 0.74%、最大 2.29%（<b>带符号均值 +0.074% 会低估离散度，勿当精度看</b>）。
          偏差来自机制差（中间价每日 9:15 定盘、基于前一交易日篮子 ⇒ 趋势日滞后约 1 天），
          而单位错是「全体平移」型的 3%+ 偏离 —— 两条口径互比正是为把后者抓出来。
          偏差标签按 |偏差| 分档：≤0.3% 蓝、≤1.5% 琥珀、&gt;1.5% 警示。
        </div>
        </details>
      </div>

      <!-- 全球央行方向：⚠️ 上游停更，只能读历史方向。
           P4（2026-09-26）：本块（6 行决议表 + 停更说明）属「证据层」，且**不是任何 anchor
           目标**（没有 KPI 指向它）⇒ 默认折叠最安全。 -->
      <details class="mc-fold">
        <summary>全球央行方向 · 最新有效决议（6 家央行，含上游停更提示）</summary>
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
      </details>

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
      <details class="mc-fold">
        <summary>为什么外部约束算「货币流动性」· 单位与口径备注（原文）</summary>
      <div class="mc-note"><RichText :text="external?.note ?? ''" /></div>
      </details>
    </NCard>

    <!-- ⑤ 货币总量（数量维度，2026-09-26 步骤 1；P3 / P4 · 2026-09-26 结构调整）
         为什么单独成为一区：上面四个切面答的是「钱**贵不贵**」（价格 / 预期 / 政策 / 外部约束），
         本区答「钱**多不多**」（水位与存量）—— 价格是边际、数量是水位，两者是同一枚硬币的两面。
         ⚠️ **变更说明**（原「不拆大卡 · KPI 不标 card_rank」的做法已由决策 D6=(a) 取代）：
           本区三个代表读数**已上卡片墙** —— 新增的「数量」卡（q4）取
           us_m2 / eu_m3 / cn_m2（三经济体横向对比，服务目标 T3）；其余低频明细仍只在详情页。
           各分组子标题已按四环**打标**（① 央行操作 / ③ 信用派生 / ④ 对外与资产）。
         🔴 单位纪律（D5=A）：本区所有金额**已由后端统一折美元（万亿美元）**，
           前端只格式化、**不做任何换算**；不可比的量（黄金实物量 / 人民币表内外汇 / OMO 操作量）
           由后端留原生单位下发。 -->
    <NCard id="mc-quantity" size="small" class="mc-card"
           title="货币总量：钱在变多，还是在变少？">
      <template #header-extra>
        <DrillLink :items="[{ label: '跨市场对照看海外', to: '/analysis/cross-market' }]" />
      </template>
      <div class="mc-hint">
        上面四个切面答的是「钱<b>贵不贵</b>」（价格 / 预期 / 政策 / 外部约束），本区答「钱<b>多不多</b>」。
        <b>价格是边际、数量是水位</b>：只看价格会把「没人借钱」误读成「钱变多了」，
        只看数量会把「存量高企但边际收紧」误读成「还在放水」—— 两者必须配着看。
        ⚠️ 本区是<b>低频存量读数</b>（BIS 季度、美联储周度、其余月度），<b>天然不该天天看</b>。
        跨区金额已<b>统一折美元</b>（换算集中在后端一处），前端只做格式化。
      </div>

      <KpiCards v-if="quantityKpis.length" :items="quantityKpis" />

      <!-- 判读条：口径在后端生成，前端只透传（设计 §四「判读条给判断、KPI 给读数」） -->
      <div v-if="quantity?.reading" class="mc-cross" :class="qtyToneClass">
        <span class="mc-cross-badge">数量维度 · 四层同向？</span>
        <span class="mc-cross-text">
          <b>{{ quantity.reading.headline }}</b>
          <br /><RichText :text="quantity.reading.detail" />
        </span>
      </div>

      <!-- ③ 信用派生 · 三经济体横向对比（P4 · 2026-09-26 ── 四环重排的新增块）
           为什么单独一块：中国 M2 52.87 万亿的专业价值**不在它自己**，而在
           「52.87 vs 23.34 vs 20.00」这个**分化叙事**。原先三地读数分散在三个按数据源分的
           分组里、中国块独大 1273px ⇒ 是三个孤立读数，读不出信息。
           🔴 折算汇率与各自 as_of 必须明示：跨国比较**随汇率漂移而失真**，
              不标日期的折算率不可审计（目标 T3，也是上一轮已踩实的口径纪律）。 -->
      <div id="mc-qty-cross" class="mc-ext-block">
        <div class="card-sub">
          ③ 信用派生 · 美 / 欧 / 中 货币总量横向对比
          <span class="mc-dim">
            折算 USD/CNY {{ quantity?.fx?.usd_cny ?? '--' }} · EUR/USD {{ quantity?.fx?.eur_usd ?? '--' }}
            <template v-if="quantity?.fx?.as_of">（{{ quantity.fx.as_of }}）</template>
            ｜单位：万亿美元
          </span>
        </div>
        <table class="mc-table">
          <thead>
            <tr>
              <th>经济体</th><th>口径</th><th>规模（万亿美元）</th><th>同比</th><th>原生单位</th><th>截至</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term"><b>美国</b></td>
              <td class="mc-dim">M2</td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.us?.money?.m2_tn) }}</td>
              <td class="mc-num">{{ pct(quantity?.us?.money?.m2_yoy) }}</td>
              <td class="mc-num mc-dim">{{ num(quantity?.us?.money?.m2_bn, 1) }} 十亿美元</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_us_money ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term"><b>欧元区</b></td>
              <td class="mc-dim">M3</td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.eu?.m3_tn) }}</td>
              <td class="mc-num">{{ pct(quantity?.eu?.m3_yoy) }}</td>
              <td class="mc-dim">百万欧元（原币）</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_eu ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term"><b>中国</b></td>
              <td class="mc-dim">M2</td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.cn?.money?.m2_tn) }}</td>
              <td class="mc-num">{{ pct(quantity?.cn?.money?.m2_yoy) }}</td>
              <td class="mc-num mc-dim">{{ num(quantity?.cn?.money?.m2_yi, 1) }} 亿元</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_cn_money ?? '--' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="mc-foot">
          ⚠️ <b>三个原生单位全不同</b>（美国「十亿美元」/ 欧元区「百万欧元」/ 中国「亿元」），
          本表已由后端统一折美元。🔴 <b>不折美元直接比大小会得到相反结论</b>：
          实测中国 M2 原生 3,568,083.6 亿元 vs 美国 23,342.8 十亿美元（数字差 150 倍、方向也相反），
          折美元后是 <b>52.87 vs 23.34 vs 20.00</b>（中国约为美国的 2.3 倍）。
          🔴 <b>折算率随汇率漂移</b>：上方的 USD/CNY 与 EUR/USD 各带 <code>as_of</code>，
          汇率变动时本表的跨期比较会失真 —— 这也是「单位归一只在后端做一次」的原因。
          ⚠️ <b>三地 M2/M3 的定义本身不同</b>（美国 M2 含零售货币基金、欧元区 M3 含回购、
          中国 M2 含单位定期存款）⇒ 只读<b>方向与规模量级</b>，不做精确倍率比较。
        </div>
      </div>

      <!-- 分组 1｜全球层：境外美元信贷（BIS 官方口径，季度） -->
      <div id="mc-qty-global" class="mc-ext-block">
        <div class="card-sub">
          <span class="mc-qty-ring">④ 对外与资产</span>
          全球层 · 境外美元信贷（BIS 官方口径 · 季度）
          <span class="mc-dim">截至 {{ quantity?.global?.period ?? '--' }}</span>
        </div>
        <details class="mc-fold">
          <summary>BIS 分腿明细 · 总量 / 银行贷款 / 国际债券 IDS（含官维组合与占比）</summary>
        <table class="mc-table">
          <thead>
            <tr>
              <th>口径</th><th>BIS 官维组合</th><th>金额（万亿美元）</th><th>同比</th><th>占比</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term"><b>境外美元信贷（总量）</b></td>
              <td class="mc-dim">{{ legText('total') }}</td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.global?.total_tn) }}</td>
              <td class="mc-num">{{ pct(quantity?.global?.total_yoy) }}</td>
              <td class="mc-num mc-dim">100%</td>
            </tr>
            <tr>
              <td class="mc-term">├ 银行贷款（子项）</td>
              <td class="mc-dim">{{ legText('loans') }}</td>
              <td class="mc-num">{{ fmtTn(quantity?.global?.loans_tn) }}</td>
              <td class="mc-num">{{ pct(quantity?.global?.loans_yoy) }}</td>
              <td class="mc-num mc-dim">
                {{ shareOf(quantity?.global?.loans_tn, quantity?.global?.total_tn) }}</td>
            </tr>
            <tr>
              <td class="mc-term">└ 国际债券 IDS（子项）</td>
              <td class="mc-dim">{{ legText('bonds') }}</td>
              <td class="mc-num">{{ fmtTn(quantity?.global?.bonds_tn) }}</td>
              <td class="mc-num">{{ pct(quantity?.global?.bonds_yoy) }}</td>
              <td class="mc-num mc-dim">
                {{ shareOf(quantity?.global?.bonds_tn, quantity?.global?.total_tn) }}</td>
            </tr>
          </tbody>
        </table>
        </details>
        <div class="mc-foot">
          🔴 <b>三者不可相加</b>：总量（<code>l_instr=B</code>）<b>已包含</b>银行贷款（<code>G</code>）
          与国际债券（<code>D</code>）—— 相加即三重重复，实测错写法会把 14.75 万亿放大到 45.95 万亿
          （真值的 <b>3.12 倍</b>）。官维组合由后端下发，前端不猜维度取值。
          口径自证：贷款 + 债券 − 总量 = <b>{{ quantity?.global?.xcheck_pct }}%</b>（逐位吻合）。
          ⚠️ 中文口径是「<b>境外美元信贷</b>」（跨境 + 借款人本地外币），不要简化成「跨境美元信贷」；
          本表<b>只含美元计价</b>，不含欧元 / 日元等其它币种。
          ⚠️ <b>季度、T+1 季发布</b> ⇒ 不可当「当前」读数。
        </div>
      </div>

      <!-- 分组 2｜美国层：美联储总资产（anchor）+ 美元净流动性 A5（派生亮点） + M2/M1 -->
      <div id="mc-qty-us" class="mc-ext-block">
        <div class="card-sub"><span class="mc-qty-ring">① 央行操作</span>美国层 · 美联储 = 全球美元总闸门</div>
        <details class="mc-fold">
          <summary>美联储总资产与美元净流动性 A5 合成明细 · 4/13/52 周变化 · 三腿逐项（含单位与「三腿同日」校验）</summary>
        <table class="mc-table">
          <thead>
            <tr>
              <th>读数</th><th>金额（万亿美元）</th><th>4 周</th><th>13 周</th><th>52 周</th><th>截至</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term"><b>美联储总资产</b>（扩表=放水）</td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.us?.fed?.tn) }}</td>
              <td class="mc-num mc-dim">{{ pct(quantity?.us?.fed?.chg_4w_pct) }}</td>
              <td class="mc-num">{{ pct(quantity?.us?.fed?.chg_13w_pct) }}</td>
              <td class="mc-num mc-dim">{{ pct(quantity?.us?.fed?.chg_52w_pct) }}</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_fed ?? '--' }}</td>
            </tr>
          </tbody>
        </table>
        <!-- A5 的构成必须显式列出：它是「合成口径、非官方」，且三腿须同日 -->
        <table class="mc-table" style="margin-top: 8px">
          <thead>
            <tr><th>A5 合成（= 总资产 − TGA − ON RRP）</th><th>金额</th><th>单位</th><th>截至</th></tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term">美联储总资产</td>
              <td class="mc-num">{{ num(quantity?.us?.net_liquidity?.fed_tn, 2) }}</td>
              <td class="mc-dim">万亿美元</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_net_liq ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term">− 财政部 TGA 余额</td>
              <td class="mc-num">{{ num(quantity?.us?.net_liquidity?.tga_yi, 0) }}</td>
              <td class="mc-dim">亿美元</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_net_liq ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term">− 隔夜逆回购 ON RRP</td>
              <td class="mc-num">{{ num(quantity?.us?.net_liquidity?.onrrp_yi, 2) }}</td>
              <td class="mc-dim">亿美元</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_net_liq ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term"><b>= 美元净流动性（合成 A5）</b></td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.us?.net_liquidity?.tn) }}</td>
              <td class="mc-dim">万亿美元</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_net_liq ?? '--' }}</td>
            </tr>
          </tbody>
        </table>
        </details>
        <div class="mc-foot">
          🔴 <b>合成口径、非官方</b>：A5 是市场最常用的「美元真实流动性」口径，由本项目自合成
          ⇒ KPI 用金色强调是因为它是<b>派生亮点</b>，<b>不代表官方背书</b>。
          ⚠️ 三腿<b>必须取同一日期</b>：美联储总资产是周度（全为周三）、ON RRP 只有 249 天有值、
          TGA 1,248 天 ⇒ 实测全史仅 <b>52 个「三腿同日」</b>（当前取到
          {{ quantity?.us?.net_liquidity?.n_days ?? '--' }} 个可用同日期）。若退化成「各自最近值」，
          等于拿三个不同日期相减（TGA 日波动可达 ±10 万百万美元，足以改变结论）。
          ⚠️ TGA / ON RRP 量级可低到「亿美元」级 ⇒ 折成万亿只剩 0.000x，故这两项按<b>亿美元</b>列示。
          <br />「13 周 / 52 周」为周度行数直接取值（该表全部是周三数据，无需按自然日折算）。
        </div>

        <details class="mc-fold">
          <summary>美国货币供应明细 · M2 / M1 / 基础货币（原生值 · 折美元 · 同比 · 截至）</summary>
        <table class="mc-table" style="margin-top: 10px">
          <thead>
            <tr><th>美国货币供应（月度 H6）</th><th>原生</th><th>折美元</th><th>同比</th><th>截至</th></tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term">M2</td>
              <td class="mc-num mc-dim">{{ num(quantity?.us?.money?.m2_bn, 1) }} 十亿美元</td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.us?.money?.m2_tn) }} 万亿美元</td>
              <td class="mc-num">{{ pct(quantity?.us?.money?.m2_yoy) }}</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_us_money ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term">M1</td>
              <td class="mc-num mc-dim">{{ num(quantity?.us?.money?.m1_bn, 1) }} 十亿美元</td>
              <td class="mc-num">{{ fmtTn(quantity?.us?.money?.m1_tn) }} 万亿美元</td>
              <td class="mc-num">{{ pct(quantity?.us?.money?.m1_yoy) }}</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_us_money ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term">基础货币</td>
              <td class="mc-num mc-dim">--</td>
              <td class="mc-num">{{ fmtTn(quantity?.us?.money?.base_tn) }} 万亿美元</td>
              <td class="mc-num mc-dim">--</td>
              <td class="mc-num mc-dim">{{ quantity?.as_of_us_money ?? '--' }}</td>
            </tr>
          </tbody>
        </table>
        </details>
        <div class="mc-foot">
          ⚠️ 美国 M2 原生单位是 <b>十亿美元</b>（23,342.8 bn），与欧元区 M3（<b>百万欧元</b>）、
          中国 M2（<b>亿元</b>）<b>单位全不同</b> ⇒ 本表同时给出原生值与折美元值。
          <b>拿原值直接比大小会得到相反结论</b>：中美 M2 原值差 150 倍，折美元后是 2.3 倍。
        </div>
      </div>

      <!-- 分组 3｜欧元区：第二大货币区（单卡看 M3 同比） -->
      <div id="mc-qty-eu" class="mc-ext-block">
        <div class="card-sub">
          <span class="mc-qty-ring">③ 信用派生</span>
          欧元区 · 第二大货币区
          <span class="mc-dim">截至 {{ quantity?.as_of_eu ?? '--' }}</span>
        </div>
        <details class="mc-fold">
          <summary>欧元区货币总量明细 · M3 / M2 / M1（折美元 · 同比 · 汇率口径）</summary>
        <table class="mc-table">
          <thead>
            <tr><th>口径</th><th>折美元（万亿美元）</th><th>同比</th><th>汇率口径</th></tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term"><b>M3</b></td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.eu?.m3_tn) }}</td>
              <td class="mc-num">{{ pct(quantity?.eu?.m3_yoy) }}</td>
              <td class="mc-num mc-dim">EUR/USD {{ quantity?.eu?.eur_usd ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term">M2</td>
              <td class="mc-num">{{ fmtTn(quantity?.eu?.m2_tn) }}</td>
              <td class="mc-num mc-dim">--</td>
              <td class="mc-num mc-dim">同上</td>
            </tr>
            <tr>
              <td class="mc-term">M1</td>
              <td class="mc-num">{{ fmtTn(quantity?.eu?.m1_tn) }}</td>
              <td class="mc-num mc-dim">--</td>
              <td class="mc-num mc-dim">同上</td>
            </tr>
          </tbody>
        </table>
        </details>
        <div class="mc-warn">
          ⚠️ <b>唯一通道，失效即告警</b>：欧元区数据只有 DBnomics 一条通道（ECB 直连三域全断），
          它失效则本组断供 —— DQ freshness 是唯一防线。
          原生单位是 <b>百万欧元</b>（不是美元！），折美元走人民币中间价交叉汇率
          （EUR/CNY ÷ USD/CNY），已由后端完成。
        </div>
      </div>

      <!-- 分组 4｜中国层：M2 结构 + 短端流量 + 官方储备（三套口径分列） -->
      <div id="mc-qty-cn" class="mc-ext-block">
        <div class="card-sub">
          <span class="mc-qty-ring">③ 信用派生 · 跨 ①④</span>
          中国层 · M2 结构 + 央行操作 + 官方储备
          <span class="mc-dim">截至 {{ quantity?.as_of_cn_money ?? '--' }}</span>
        </div>
        <details class="mc-fold">
          <summary>中国 M2 / M1 与 M1−M2 剪刀差明细 · 原生值 · 折美元 · 同比 · 近 3 月变化</summary>
        <table class="mc-table">
          <thead>
            <tr><th>口径</th><th>原生</th><th>折美元</th><th>同比</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term"><b>M2</b></td>
              <td class="mc-num mc-dim">{{ num(quantity?.cn?.money?.m2_yi, 1) }} 亿元</td>
              <td class="mc-num-strong">{{ fmtTn(quantity?.cn?.money?.m2_tn) }} 万亿美元</td>
              <td class="mc-num">{{ pct(quantity?.cn?.money?.m2_yoy) }}</td>
              <td class="mc-num mc-dim">USD/CNY {{ quantity?.cn?.money?.usd_cny ?? '--' }}</td>
            </tr>
            <tr>
              <td class="mc-term">M1</td>
              <td class="mc-num mc-dim">{{ num(quantity?.cn?.money?.m1_yi, 1) }} 亿元</td>
              <td class="mc-num">{{ fmtTn(quantity?.cn?.money?.m1_tn) }} 万亿美元</td>
              <td class="mc-num">{{ pct(quantity?.cn?.money?.m1_yoy) }}</td>
              <td class="mc-num mc-dim">同上</td>
            </tr>
            <tr>
              <td class="mc-term">M1 − M2 剪刀差</td>
              <td class="mc-num mc-dim">--</td>
              <td class="mc-num">{{ pct(quantity?.cn?.money?.gap) }}</td>
              <td class="mc-num">
                <span class="mc-tag"
                      :class="(quantity?.cn?.money?.gap_d3 ?? 0) >= 0 ? 'mc-tag--ok' : 'mc-tag--warn'">
                  {{ quantity?.cn?.money?.gap_word ?? '--' }}
                </span>
              </td>
              <td class="mc-num mc-dim">近 3 月 {{ pp(quantity?.cn?.money?.gap_d3) }}</td>
            </tr>
          </tbody>
        </table>
        </details>
        <div class="mc-foot">
          ⚠️ 折美元需 USD/CNY 中间价 —— <b>不折美元就与美国 M2 比大小是错的</b>。
          M1 − M2 剪刀差衡量<b>资金活化度</b>（活钱 vs 死钱）：上升 = 活化度回升，
          回落 = 钱在往定期/储蓄里沉。
        </div>

        <!-- M2 结构：⚠️ 源侧停更时点不齐，必须标注而不是当缺数。
             P4（2026-09-26）：默认**折叠**（F4「明细折叠」）—— 逐列明细属证据层，
             使用场景 S1（30 秒速览）用不到；S3（月度策略，10 分钟）需要时一键展开，
             **内容零删减**（折叠 ≠ 删除，这是 R5 的硬要求）。
             ⚠️ 用原生 `<details>` 而非 NCollapse：无需 JS 状态，且 anchor 目标在它**外部**，
                不会出现「点了锚点跳进一个收起的容器」（红线 8）。 -->
        <details class="mc-fold">
          <summary>M2 结构明细（活期 / 定期 / 储蓄 / 准货币 / 其他存款）</summary>
        <table class="mc-table" style="margin-top: 10px">
          <thead>
            <tr><th>M2 结构</th><th>数值（亿元）</th><th>数据截至</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in quantity?.cn?.money?.struct ?? []" :key="s.key">
              <td class="mc-term">{{ s.label }}</td>
              <td class="mc-num">{{ s.value == null ? '--' : num(s.value, 2) }}</td>
              <td class="mc-num mc-dim">{{ s.as_of ?? '--' }}</td>
              <td>
                <span v-if="s.stale" class="mc-tag mc-tag--warn">源口径调整 · 已停更</span>
                <span v-else class="mc-tag mc-tag--ok">正常更新</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="mc-foot">
          ⚠️ 结构列<b>停更时点不齐</b>（实测：活期/定期停 2025-05、储蓄停 2024-12，
          而准货币 / 其他存款仍更新到 {{ quantity?.as_of_cn_money ?? '--' }}）
          ⇒ 属<b>源侧口径调整</b>，<b>不是采集失败</b>，不得当缺数处理。
        </div>
        </details>

        <!-- 中国「外储」三套口径：🔴 单位不同、折算率非市场汇率 ⇒ 严禁相加 -->
        <details class="mc-fold">
          <summary>「外储」三套口径明细 · 官方外汇储备 / 黄金实物量 / 人民币表内外汇（含各自来源与同比）</summary>
        <table class="mc-table" style="margin-top: 10px">
          <thead>
            <tr><th>「外储」口径（⚠️ 三套，不可相加）</th><th>数值</th><th>单位</th><th>来源 / 口径</th></tr>
          </thead>
          <tbody>
            <tr>
              <td class="mc-term"><b>① 官方外汇储备</b>（国际可比）</td>
              <td class="mc-num-strong">{{ num(quantity?.cn?.reserve?.fx_usd_yi, 2) }}</td>
              <td class="mc-dim">亿美元</td>
              <td class="mc-num mc-dim">
                cn_reserve_monthly｜同比 {{ pct(quantity?.cn?.reserve?.fx_yoy) }}</td>
            </tr>
            <tr>
              <td class="mc-term">② 黄金储备（<b>实物量</b>）</td>
              <td class="mc-num">{{ num(quantity?.cn?.reserve?.gold_oz, 0) }}</td>
              <td class="mc-dim">万盎司</td>
              <td class="mc-num mc-dim">
                月度环比 +{{ num(quantity?.cn?.reserve?.gold_chg, 0) }} 万盎司 ·
                <b>连续增持 {{ quantity?.cn?.reserve?.gold_streak ?? '--' }} 个月</b></td>
            </tr>
            <tr>
              <td class="mc-term">③ 表内外汇（<b>人民币口径</b>）</td>
              <td class="mc-num">{{ num(quantity?.cn?.cb_balance?.fx_yi, 2) }}</td>
              <td class="mc-dim">亿元</td>
              <td class="mc-num mc-dim">
                cn_cb_balance_monthly｜货币黄金 {{ num(quantity?.cn?.cb_balance?.gold_yi, 2) }} 亿元</td>
            </tr>
          </tbody>
        </table>
        </details>
        <div class="mc-warn">
          🔴 <b>三套口径严禁相加、严禁用市场汇率互校</b>：它们的单位互不相同，且人民币表内口径 ÷
          美元官方口径的<b>隐含折算率不是市场汇率</b>（实测 24 期由 <b>6.886 单调降至 6.318</b>，
          同期市场汇率约 7.0~7.3）—— 用市场汇率做校验会<b>稳定误报</b>。
          ⇒ 本表只分列并各标口径。⚠️ 黄金是<b>实物量（万盎司）不是金额</b>，故不折美元。
        </div>

        <!-- 央行 OMO：只给「操作量事实」，不做净投放（D4=B 主动决策）。
             P4：逐笔明细默认**折叠**（F4 指定项之一）—— 操作量逐笔属证据层，展开后与原来完全一致。 -->
        <details class="mc-fold">
          <summary>央行公开市场操作逐笔（按栏目分列，含操作量合计）</summary>
        <div v-for="o in quantity?.cn?.omo ?? []" :key="o.section" class="mc-qty-omo">
          <div class="card-sub">
            央行公开市场操作 · {{ o.label }}
            <span class="mc-dim">最近 {{ o.days }} 个操作日（截至 {{ o.as_of }}）</span>
          </div>
          <table class="mc-table">
            <thead>
              <tr><th>操作日</th><th>工具</th><th>期限</th><th>中标利率</th><th>操作量（亿元）</th></tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in o.rows" :key="o.section + i">
                <td class="mc-num mc-dim">{{ r.date }}</td>
                <td class="mc-term">{{ opLabel(r.op_type) }}</td>
                <td class="mc-num mc-dim">{{ r.tenor_days == null ? '--' : r.tenor_days + ' 天' }}</td>
                <td class="mc-num">{{ r.rate == null ? '--' : r.rate + '%' }}</td>
                <td class="mc-num">{{ num(r.amount_yi, 0) }}</td>
              </tr>
            </tbody>
          </table>
          <div class="mc-foot">
            栏目内合计 {{ num(o.amount_sum_yi, 0) }} 亿元（<b>仅本栏目</b>，不含其它栏目）。
          </div>
        </div>
        <div class="mc-foot">
          🔴 <b>两个栏目不可混加</b>：<code>omo_trade</code>（逆回购 / 央票）与
          <code>outright_repo</code>（买断式逆回购）各自独立编号、量级差一个数量级 ——
          直接求和会把「7 天期逆回购 515 亿」与「买断式 5,000 亿」混成一个数。
          🔴 <b>本期不做「净投放」派生</b>（主动决策、非遗漏）：净投放须按期限滚动推算到期日
          （7 天期遇节假日顺延）、买断式期限不固定、人行另有国债买卖 / 国库现金定存等工具 ⇒ 会系统性低估。
          ⚠️ <b>零操作日也是有效数据</b>（操作量 0）；买断式是招标预告，一律按<b>正文操作日</b>列示。
        </div>
        </details>
      </div>

      <div class="card-cap">
        数据截至：{{ qtyAsOfText || '--' }}。
        ⚠️ 本区四条源<b>不同轴</b>（BIS 季度 / 美联储周度 / 其余月度 / OMO 日度），
        故各组各自标注截至日，<b>不套用页头的「数据截至」</b>。
        汇率口径：{{ quantity?.fx?.source ?? '--' }}（USD/CNY {{ quantity?.fx?.usd_cny ?? '--' }} ·
        EUR/USD {{ quantity?.fx?.eur_usd ?? '--' }}）。
      </div>
      <details class="mc-fold">
        <summary>为什么「价格」与「数量」必须一起看 · 单位与口径备注（原文）</summary>
      <div class="mc-note"><RichText :text="quantity?.note ?? ''" /></div>
      </details>
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
      <details class="mc-fold">
        <summary>交叉印证的数据来源与口径备注（原文）</summary>
      <div class="mc-note"><RichText :text="note" /></div>
      </details>
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

/* ---------------- P1（2026-09-26）：页头结论条 + 页内锚点 + 口径折叠 ---------------- */
/* 结论条三态：蓝=常态 / 金=机会侧 / 琥珀=提醒（蓝骨金魂；**刻意不出现红绿** —— 红涨绿跌只属于行情数字） */
.mc-headline {
  display: flex; gap: 10px; align-items: flex-start;
  border-left: 3px solid #185FA5; background: #EAF2FB;
  border-radius: 0 6px 6px 0; padding: 10px 12px; margin-bottom: 10px;
}
.mc-headline--gold { border-left-color: #C9A227; background: #FAF3DF; }
.mc-headline--warn { border-left-color: #B45309; background: #FAEEDA; }
.mc-headline-badge {
  flex: 0 0 auto; font-size: 11px; color: #185FA5; background: #FFFFFF;
  border: 1px solid #C7DDF2; border-radius: 3px; padding: 1px 6px; margin-top: 1px;
}
.mc-headline--gold .mc-headline-badge { color: #7A5E12; border-color: #E7D9A8; }
.mc-headline--warn .mc-headline-badge { color: #B45309; border-color: #F0D6B8; }
.mc-headline-title { font-size: 15px; color: #1F2937; line-height: 1.5; }
.mc-headline--gold .mc-headline-title { color: #7A5E12; }
.mc-headline--warn .mc-headline-title { color: #7C2D12; }
.mc-headline-detail { display: block; font-size: 12px; color: #6B7280; margin-top: 3px; line-height: 1.6; }
/* 锚点导航：一行 5 个（窄屏自动换行） */
.mc-nav { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.mc-nav-label { font-size: 11px; color: #9CA3AF; }
.mc-nav-item {
  font-size: 12px; color: #185FA5; background: #F3F4F5; border: 1px solid #E5E7EB;
  border-radius: 12px; padding: 2px 10px; cursor: pointer; font-family: inherit;
}
.mc-nav-item:hover { background: #EAF2FB; border-color: #C7DDF2; }
/* 「口径与更新」折叠区：承接被瘦身的页头 desc（不删除，只换位置） */
.mc-scope { margin-bottom: 12px; }
.mc-scope-body { font-size: 12px; color: #6B7280; line-height: 1.8; }
.mc-scope-body p { margin: 0 0 8px; }
.mc-scope-body p:last-child { margin-bottom: 0; }
.mc-scope-body b { color: #1F2937; }
.mc-scope-body code {
  background: #F3F4F5; border-radius: 3px; padding: 0 4px; font-size: 11px; color: #185FA5;
}
/* 四环标记（P4 · 2026-09-26）：给每个分组标出它服务四环的哪一环 ——
   把「按数据源分的块」重新锚到「按因果位置分的环」上（registry 契约⑩ 的页内应用）。
   ⚠️ 这只是**打标**，不搬区块（D2 = 保守方案 B），故风险远低于物理重排。 */
.mc-qty-ring {
  display: inline-block; font-size: 11px; color: #185FA5; background: #EAF2FB;
  border: 1px solid #C7DDF2; border-radius: 3px; padding: 0 5px; margin-right: 6px;
}
/* 明细折叠（P4 · 2026-09-26）：默认收起「证据层」—— S1（30 秒速览）不被它挤，
   S3（月度策略，10 分钟）一键展开且**内容零删减**。
   🔴 用原生 `<details>` 而非 NCollapse，两个原因：① 无需 JS 状态；
      ② 所有 anchor 目标（mc-qty-* / mc-ext-*）都在它**外部**，故不会出现
         「点了锚点跳进一个收起的容器」（契约红线 8）。 */
.mc-fold { margin-top: 10px; }
.mc-fold > summary {
  font-size: 12px; color: #185FA5; cursor: pointer; padding: 3px 0;
  list-style: none; user-select: none;
}
.mc-fold > summary::-webkit-details-marker { display: none; }
.mc-fold > summary::before { content: '▸ '; color: #9CA3AF; }
.mc-fold[open] > summary::before { content: '▾ '; }
.mc-fold > summary:hover { color: #1E6FFF; }
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
/* 货币总量分区（2026-09-26 步骤 1）：OMO 按栏目分列，各栏目再套一层浅底分隔，
   让「逆回购」与「买断式」在视觉上就是两块、不会被误读成同一张表的两段。 */
.mc-qty-omo {
  margin-top: 10px; padding: 8px 10px; border-radius: 6px;
  background: #FAFBFC; border: 1px solid #EDEFF2;
}
.mc-qty-omo .mc-table { margin-top: 4px; }
.mc-ext-block code {
  font-size: 11px; background: #F5F7FA; color: #185FA5; border-radius: 3px; padding: 0 4px;
}
/* 预警块（上游停更 / 口径风险）：琥珀系，与 .mc-tag--warn 同族；
   ⚠️ 不用亮红 —— 项目配色铁律：失败用深红棕、警告用琥珀（见颜色体系规范）。 */
.mc-warn {
  font-size: 11px; line-height: 1.7; color: #7A4A0B; background: #FAEEDA;
  border-left: 3px solid #B45309; border-radius: 4px; padding: 8px 10px; margin-top: 10px;
}
.mc-warn :deep(strong) { color: #7A2E0B; }
</style>
