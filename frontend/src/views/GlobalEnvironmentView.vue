<script setup lang="ts">
/**
 * GlobalEnvironmentView —— 投资环境·国际环境（v1.0，2026-10-21）
 * 参照国内/国际指数/市场氛围风格，分两大块 + 左侧两级锚点导航：
 *   A. 技术面指标：
 *      ① 市场波动与风险偏好  ② 资本流动与汇率  ③ 避险资产与跨市场表现
 *      ④ 情绪与不确定性      ⑤ 技术趋势与市场结构
 *   B. 基本面分析：
 *      ① 地缘政治与安全  ② 国际经济与贸易  ③ 国际政治与治理  ④ 政策与制度  ⑤ 精英共识与舆论
 * 数据源：新采集 ovx_daily/gpr_daily/epu_daily/wui_monthly/tpu_monthly/fsi_monthly +
 *   复用 vix_daily/global_usd_index_daily/bond_profit_daily/cb_policy_rate/cn_reserve_monthly。
 * MOVE/主权CDS/EMBI/新兴资本流/GCRI/GPI/恐怖主义/军费等免费源不可得，诚实标注缺口。
 * 结论由观看者判断：只呈现客观数值，不配定性词。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NCard, NSpin, NEmpty } from 'naive-ui'
import api from '../api'
import IndicatorBlock from '../components/analysis/IndicatorBlock.vue'
import HistoryCurve from '../components/analysis/HistoryCurve.vue'

const data = ref<any>(null)
const loading = ref(true)

// ---------- 左侧锚点导航 ----------
const NAV_ITEMS = [
  {
    id: 'sec-vol', label: '技术面指标',
    children: [
      { id: 'sec-vol', label: '市场波动与风险偏好' },
      { id: 'sec-capflow', label: '资本流动与汇率' },
      { id: 'sec-safe', label: '避险资产与跨市场表现' },
      { id: 'sec-uncertainty', label: '情绪与不确定性' },
      { id: 'sec-trend', label: '技术趋势与市场结构' },
    ],
  },
  {
    id: 'sec-fund', label: '基本面分析',
    children: [
      { id: 'sec-geo', label: '地缘政治与安全' },
      { id: 'sec-trade', label: '国际经济与贸易' },
      { id: 'sec-gov', label: '国际政治与治理' },
      { id: 'sec-policy', label: '政策与制度' },
      { id: 'sec-consensus', label: '精英共识与舆论' },
    ],
  },
]
const activeNav = ref<string>('sec-vol')

function scrollTo(id: string) {
  activeNav.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const FLAT_NAV: { id: string; parent: string }[] = NAV_ITEMS.flatMap(
  (g) => g.children.map((c) => ({ id: c.id, parent: g.id })),
)
function onDocScroll() {
  const line = 120
  let cur = FLAT_NAV[0].id
  for (const n of FLAT_NAV) {
    const el = document.getElementById(n.id)
    if (el && el.getBoundingClientRect().top <= line) cur = n.id
  }
  activeNav.value = cur
  updateNavPin()
}

const navInnerRef = ref<HTMLElement | null>(null)
const navAsideRef = ref<HTMLElement | null>(null)
function updateNavPin() {
  const aside = navAsideRef.value
  const inner = navInnerRef.value
  if (!aside || !inner) return
  const maxShift = Math.max(0, aside.offsetHeight - inner.offsetHeight)
  const shift = Math.min(Math.max(8 - aside.getBoundingClientRect().top, 0), maxShift)
  inner.style.transform = `translateY(${shift}px)`
}
onMounted(() => {
  document.addEventListener('scroll', onDocScroll, { capture: true, passive: true })
  window.addEventListener('resize', updateNavPin, { passive: true })
  updateNavPin()
  load()
})
onBeforeUnmount(() => {
  document.removeEventListener('scroll', onDocScroll, { capture: true } as any)
  window.removeEventListener('resize', updateNavPin)
})

// ---------- 工具 ----------
function fmtNum(v: any, digits = 2): string {
  if (v == null) return '--'
  return Number(v).toFixed(digits)
}
/** 截至日期 → 「截至 YYYY-MM-DD」；无则空串 */
function asOfLabel(d: any): string {
  return d ? `截至 ${String(d).slice(0, 10)}` : ''
}

// ---------- 数据加载 ----------
async function load() {
  loading.value = true
  try {
    data.value = await api.get('/environment/global-env') as any
  } catch (e) {
    console.error('[global env]', e)
  } finally {
    loading.value = false
  }
}

// ---------- 派生 ----------
const volatility = computed(() => data.value?.volatility)
const capitalFlow = computed(() => data.value?.capital_flow)
const safeHaven = computed(() => data.value?.safe_haven)
const uncertainty = computed(() => data.value?.uncertainty)
const techTrend = computed(() => data.value?.tech_trend)
const geopolitics = computed(() => data.value?.geopolitics)
const trade = computed(() => data.value?.trade)
const governance = computed(() => data.value?.governance)
const policy = computed(() => data.value?.policy)
const consensus = computed(() => data.value?.consensus)
</script>

<template>
  <div class="env-layout">
    <!-- 左侧页内锚点导航 -->
    <aside ref="navAsideRef" class="env-nav">
      <div ref="navInnerRef" class="env-nav-inner">
        <div v-for="g in NAV_ITEMS" :key="g.id" class="env-nav-group">
          <div
            class="env-nav-item env-nav-parent"
            :class="{ on: g.children.some((c) => c.id === activeNav) }"
            @click="scrollTo(g.children[0].id)"
          >{{ g.label }}</div>
          <div
            v-for="c in g.children"
            :key="c.id"
            class="env-nav-item env-nav-child"
            :class="{ on: activeNav === c.id }"
            @click="scrollTo(c.id)"
          >{{ c.label }}</div>
        </div>
      </div>
    </aside>

    <div class="env">
      <!-- 顶部说明 -->
      <NCard size="small" class="env-scope">
        <p class="scope-p"><b>口径</b>：全球市场口径。技术面读新采集的风险/不确定性指数（OVX/GPR/EPU/WUI/TPU/FSI）+ 复用 VIX/美元/美债/政策利率/外储既有表。</p>
        <p class="scope-p"><b>呈现</b>：只呈现客观数值与历史走势，不配「安全/恐慌」等定性结论——极端与否由观看者结合当前位置自行判断。红 = 涨、绿 = 跌（仅行情数字）。</p>
      </NCard>

      <NSpin :show="loading">
        <!-- ============ A. 技术面指标 ============ -->
        <!-- ① 市场波动与风险偏好 -->
        <div class="env-section" id="sec-vol">
          <h2 class="env-h2">技术面指标</h2>
          <h3 class="env-h3">① 市场波动与风险偏好</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="VIX（标普500 隐含波动率）"
              :as-of="asOfLabel(volatility?.vix?.latest_date)"
              :value="volatility?.vix?.latest != null ? fmtNum(volatility.vix.latest, 2) : '--'"
              :note="'恐慌指数：<15 平稳，>25-30 风险信号。标普500 期权隐含波动率。'"
            >
              <HistoryCurve
                :dates="volatility?.vix?.dates || []"
                :series="[{ name: 'VIX', values: volatility?.vix?.values || [], color: '#185FA5' }]"
                :ref-lines="[{ value: 25, label: '25 恐慌', color: '#EF232A' }, { value: 15, label: '15 平稳', color: '#14B143' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="OVX（原油波动率）"
              :as-of="asOfLabel(volatility?.ovx?.latest_date)"
              :value="volatility?.ovx?.latest != null ? fmtNum(volatility.ovx.latest, 2) : '--'"
              :note="'原油 ETF 隐含波动率（Cboe，2009 起）。与 VIX 同步共振上行 = 风险事件驱动。'"
            >
              <HistoryCurve
                :dates="volatility?.ovx?.dates || []"
                :series="[{ name: 'OVX', values: volatility?.ovx?.values || [], color: '#C9A227' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="金融压力指数 FSI（Püttmann）"
              :as-of="asOfLabel(volatility?.fsi?.latest_date)"
              :value="volatility?.fsi?.latest != null ? fmtNum(volatility.fsi.latest, 2) : '--'"
              :note="'报纸文本金融压力指数（1889-2016，均值 100）。>100 = 压力高于历史均值。⚠️ 2016 年后停更。'"
            >
              <HistoryCurve
                :dates="volatility?.fsi?.dates || []"
                :series="[{ name: 'FSI', values: volatility?.fsi?.values || [], color: '#185FA5' }]"
                :ref-lines="[{ value: 100, label: '均值 100' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock title="MOVE（美债波动率）" value="数据源待接入" :note="volatility?.move?.note">
              <NEmpty description="ICE 商业授权，无免费全历史" />
            </IndicatorBlock>
            <IndicatorBlock title="主权 CDS 利差" value="数据源待接入" :note="volatility?.cds?.note">
              <NEmpty description="Markit/Bloomberg 商业数据" />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ volatility?.note }}</div>
        </div>

        <!-- ② 资本流动与汇率 -->
        <div class="env-section" id="sec-capflow">
          <h3 class="env-h3">② 资本流动与汇率</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="美元指数 DXY"
              :as-of="asOfLabel(capitalFlow?.dxy?.latest_date)"
              :value="capitalFlow?.dxy?.latest != null ? fmtNum(capitalFlow.dxy.latest, 2) : '--'"
              :note="'全球避险与流动性风向：快速走强 = 避险，急跌 = 风险偏好回升。'"
            >
              <HistoryCurve
                :dates="capitalFlow?.dxy?.dates || []"
                :series="[{ name: 'DXY', values: capitalFlow?.dxy?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="美元/日元（避险货币代理）"
              :as-of="asOfLabel(capitalFlow?.cny?.latest_date)"
              :value="capitalFlow?.cny?.latest != null ? fmtNum(capitalFlow.cny.latest, 2) : '--'"
              :note="'USD/JPY：日元走强（数值下行）= 避险。传统避险货币信号。'"
            >
              <HistoryCurve
                :dates="capitalFlow?.cny?.dates || []"
                :series="[{ name: 'USD/JPY', values: capitalFlow?.cny?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="中国外汇储备（亿美元）"
              :as-of="asOfLabel(capitalFlow?.reserve?.latest_date)"
              :value="capitalFlow?.reserve?.latest != null ? fmtNum(capitalFlow.reserve.latest, 0) : '--'"
              :note="'外储规模（1978 起）：人民币汇率的外部缓冲，快速下降 = 贬值压力。'"
            >
              <HistoryCurve
                :dates="capitalFlow?.reserve?.dates || []"
                :series="[{ name: '外储(亿美元)', values: capitalFlow?.reserve?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>

          <div class="block-grid">
            <IndicatorBlock title="新兴市场资本流动" value="数据源待接入" :note="capitalFlow?.em_flow?.note">
              <NEmpty description="IIF 订阅 / EMBI 商业产品" />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ capitalFlow?.note }}</div>
        </div>

        <!-- ③ 避险资产与跨市场表现 -->
        <div class="env-section" id="sec-safe">
          <h3 class="env-h3">③ 避险资产与跨市场表现</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="黄金（沪金主力）"
              :as-of="asOfLabel(safeHaven?.gold?.latest_date)"
              :value="safeHaven?.gold?.latest != null ? fmtNum(safeHaven.gold.latest, 1) : '--'"
              :note="'避险需求：金价上行 = 避险买盘。上期所沪金主力合约价格。'"
            >
              <HistoryCurve
                :dates="safeHaven?.gold?.dates || []"
                :series="[{ name: '黄金', values: safeHaven?.gold?.values || [], color: '#C9A227' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="美债 10Y 收益率"
              :as-of="asOfLabel(safeHaven?.us_bond_10y?.latest_date)"
              :value="safeHaven?.us_bond_10y?.latest != null ? fmtNum(safeHaven.us_bond_10y.latest, 2) + '%' : '--'"
              :note="'全球利率锚：收益率飙升 = 紧缩/抛售，避险抢购 = 收益率急跌。'"
            >
              <HistoryCurve
                :dates="safeHaven?.us_bond_10y?.dates || []"
                :series="[{ name: '10Y', values: safeHaven?.us_bond_10y?.values || [], color: '#185FA5' }]"
                y-format="%" height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="美债 10Y-2Y 利差"
              :as-of="asOfLabel(safeHaven?.us_bond_spread?.latest_date)"
              :value="safeHaven?.us_bond_spread?.latest != null ? fmtNum(safeHaven.us_bond_spread.latest, 2) + '%' : '--'"
              :note="'期限利差：倒挂（负值）= 衰退预警，陡峭化 = 复苏/通胀预期。'"
            >
              <HistoryCurve
                :dates="safeHaven?.us_bond_spread?.dates || []"
                :series="[{ name: '10Y-2Y', values: safeHaven?.us_bond_spread?.values || [], color: '#EF232A' }]"
                :ref-lines="[{ value: 0, label: '0 倒挂线' }]"
                y-format="%" height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ safeHaven?.note }}</div>
        </div>

        <!-- ④ 情绪与不确定性 -->
        <div class="env-section" id="sec-uncertainty">
          <h3 class="env-h3">④ 情绪与不确定性（高频文本指标）</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="EPU 美国经济政策不确定性"
              :as-of="asOfLabel(uncertainty?.epu?.latest_date)"
              :value="uncertainty?.epu?.latest != null ? fmtNum(uncertainty.epu.latest, 1) : '--'"
              :note="'Baker/Bloom/Davis 报纸文本指数（1985 起日频）。持续攀升 = 政策不确定性上升。'"
            >
              <HistoryCurve
                :dates="uncertainty?.epu?.dates || []"
                :series="[{ name: 'EPU', values: uncertainty?.epu?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="WUI 世界不确定性指数"
              :as-of="asOfLabel(uncertainty?.wui?.latest_date)"
              :value="uncertainty?.wui?.latest != null ? fmtNum(uncertainty.wui.latest, 0) : '--'"
              :note="'IMF 编制（EIU 国别报告文本，全球 GDP 加权，2008 起月频）。高位 = 全球不确定性高。'"
            >
              <HistoryCurve
                :dates="uncertainty?.wui?.dates || []"
                :series="[{ name: 'WUI', values: uncertainty?.wui?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>

            <IndicatorBlock
              title="TPU 贸易政策不确定性"
              :as-of="asOfLabel(uncertainty?.tpu?.latest_date)"
              :value="uncertainty?.tpu?.latest != null ? fmtNum(uncertainty.tpu.latest, 1) : '--'"
              :note="'Caldara et al. 指数（1960 起月频，100=1% 文章占比）。贸易政策不确定性历史高位 = 风险。'"
            >
              <HistoryCurve
                :dates="uncertainty?.tpu?.dates || []"
                :series="[{ name: 'TPU', values: uncertainty?.tpu?.values || [], color: '#C9A227' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ uncertainty?.note }}</div>
        </div>

        <!-- ⑤ 技术趋势与市场结构 -->
        <div class="env-section" id="sec-trend">
          <h3 class="env-h3">⑤ 技术趋势与市场结构</h3>
          <NCard size="small" class="ov-card">
            <div class="gap-box">
              <div class="gap-title">⚠️ 本块数据源说明</div>
              <p class="gap-p">{{ techTrend?.note || '暂无数据' }}</p>
            </div>
          </NCard>
          <div class="block-grid">
            <IndicatorBlock title="跨市场相关性" value="见国际指数章节" :note="'60 日相关性矩阵（国际指数章节已有），衡量风险传染程度。'">
              <NEmpty description="见「国际指数·跨市场联动」块" />
            </IndicatorBlock>
            <IndicatorBlock title="期权 PCR / 股指基差" value="数据源待接入" :note="'认沽/认购比率与股指期货基差的国际数据免费源不可得。'">
              <NEmpty description="免费源不可得" />
            </IndicatorBlock>
            <IndicatorBlock title="全球资金流向" value="数据源待接入" :note="'流入风险资产 vs 现金/黄金/美债的全球资金流数据属 EPFR/IIF 付费。'">
              <NEmpty description="EPFR/IIF 付费" />
            </IndicatorBlock>
          </div>
        </div>

        <!-- ============ B. 基本面分析 ============ -->
        <div class="env-section" id="sec-fund">
          <h2 class="env-h2">基本面分析</h2>

          <!-- ① 地缘政治与安全 -->
          <h3 class="env-h3" id="sec-geo">① 地缘政治与安全</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="GPR 地缘政治风险指数"
              :as-of="asOfLabel(geopolitics?.gpr?.latest_date)"
              :value="geopolitics?.gpr?.latest != null ? fmtNum(geopolitics.gpr.latest, 1) : '--'"
              :note="'Caldara & Iacoviello（AER 2022，10 报日频 1985 起）。高位 = 地缘风险上升。'"
            >
              <HistoryCurve
                :dates="geopolitics?.gpr?.dates || []"
                :series="[{ name: 'GPR', values: geopolitics?.gpr?.values || [], color: '#185FA5' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>
          <!-- 定性判读卡 -->
          <div class="policy-grid">
            <div v-for="j in (geopolitics?.judgements || [])" :key="j.topic" class="policy-card">
              <div class="policy-title">{{ j.title }}</div>
              <div class="policy-options">
                <div v-for="s in j.states" :key="s" class="policy-opt-static">
                  <span class="policy-dot" />
                  <span class="policy-label">{{ s }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="ov-note">📖 {{ geopolitics?.note }}</div>

          <!-- ② 国际经济与贸易 -->
          <h3 class="env-h3" id="sec-trade">② 国际经济与贸易</h3>
          <div class="block-grid">
            <IndicatorBlock
              title="TPU 贸易政策不确定性"
              :as-of="asOfLabel(trade?.tpu?.latest_date)"
              :value="trade?.tpu?.latest != null ? fmtNum(trade.tpu.latest, 1) : '--'"
              :note="'月频 1960 起：快速攀升至历史高位 = 关税战/贸易政策不确定性上升。'"
            >
              <HistoryCurve
                :dates="trade?.tpu?.dates || []"
                :series="[{ name: 'TPU', values: trade?.tpu?.values || [], color: '#C9A227' }]"
                height="240px" :zoom="true"
              />
            </IndicatorBlock>
          </div>
          <div class="ov-note">📖 {{ trade?.note }}</div>

          <!-- ③ 国际政治与治理 -->
          <h3 class="env-h3" id="sec-gov">③ 国际政治与治理</h3>
          <div class="policy-grid">
            <div v-for="j in (governance?.judgements || [])" :key="j.topic" class="policy-card">
              <div class="policy-title">{{ j.title }}</div>
              <div class="policy-options">
                <div v-for="s in j.states" :key="s" class="policy-opt-static">
                  <span class="policy-dot" />
                  <span class="policy-label">{{ s }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="ov-note">📖 {{ governance?.note }}</div>

          <!-- ④ 政策与制度 -->
          <h3 class="env-h3" id="sec-policy">④ 政策与制度</h3>
          <NCard size="small" class="ov-card">
            <div class="val-table">
              <div class="val-row val-head">
                <span class="val-name">央行</span>
                <span class="val-cell">政策利率</span>
                <span class="val-cell">变动(bp)</span>
                <span class="val-cell">决议日期</span>
              </div>
              <div v-for="r in (policy?.policy_rate || [])" :key="r.country" class="val-row">
                <span class="val-name">{{ r.country }}</span>
                <span class="val-cell">{{ r.rate != null ? fmtNum(r.rate, 2) + '%' : '--' }}</span>
                <span class="val-cell" :style="{ color: r.change_bp > 0 ? '#EF232A' : (r.change_bp < 0 ? '#14B143' : '#1F2937') }">
                  {{ r.change_bp != null ? (r.change_bp > 0 ? '+' : '') + r.change_bp : '--' }}
                </span>
                <span class="val-cell">{{ r.event_date }}</span>
              </div>
            </div>
            <div class="ov-note">📖 {{ policy?.note }}</div>
          </NCard>

          <!-- ⑤ 精英共识与舆论 -->
          <h3 class="env-h3" id="sec-consensus">⑤ 精英共识与舆论</h3>
          <NCard size="small" class="ov-card">
            <div class="gap-box">
              <div class="gap-title">⚠️ 本块数据源缺口</div>
              <p class="gap-p">{{ consensus?.note || '暂无数据' }}</p>
            </div>
          </NCard>
        </div>
      </NSpin>
    </div>
  </div>
</template>

<style scoped>
/* 页内导航 + 内容两栏布局；与国内/国际指数/市场氛围页同款 */
.env-layout { display: flex; gap: 14px; align-items: flex-start; max-width: 1440px; margin: 0 auto; }
.env-nav { flex: none; width: 150px; align-self: stretch; }
.env-nav-inner {
  display: flex; flex-direction: column; gap: 2px;
  padding: 8px 6px; background: #FFFFFF; border: 1px solid #EDEFF2; border-radius: 8px;
  will-change: transform;
}
.env-nav-item {
  font-size: 12px; color: #6B7280; padding: 6px 8px; border-radius: 5px; cursor: pointer;
  white-space: normal; word-break: break-all; user-select: none; line-height: 1.45;
}
.env-nav-parent { font-weight: 600; color: #1F2937; }
.env-nav-parent.on { color: #185FA5; }
.env-nav-child { padding-left: 20px; font-size: 12px; }
.env-nav-item:hover { color: #185FA5; background: #F0F6FD; }
.env-nav-item.on { color: #185FA5; background: #E3EDF9; font-weight: 600; }
.env { flex: 1; min-width: 0; max-width: 1280px; }
.env-scope { margin-bottom: 12px; }
.scope-p { font-size: 13px; line-height: 1.7; color: #374151; margin: 6px 0; }
.scope-p b { color: #1F2937; }
.env-section { margin-bottom: 8px; }
.env-h2 {
  font-size: 17px; font-weight: 700; color: #185FA5; margin: 18px 0 12px;
  padding-left: 8px; border-left: 3px solid #185FA5;
}
.env-h3 { font-size: 14px; font-weight: 600; color: #374151; margin: 16px 0 10px; }
.ov-card { margin-bottom: 12px; }
.ov-note { margin-top: 10px; font-size: 12px; line-height: 1.6; color: #6B7280; }
.block-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-items: start; }
/* 数据缺口说明框 */
.gap-box { padding: 4px 2px; }
.gap-title { font-size: 14px; font-weight: 600; color: #791F1F; margin-bottom: 8px; }
.gap-p { font-size: 13px; line-height: 1.7; color: #374151; margin: 0 0 12px; }
/* 定性判读卡（展示型两态，无落库交互） */
.policy-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 10px; }
.policy-card { border: 1px solid #EDEFF2; border-radius: 6px; padding: 12px; }
.policy-title { font-size: 13px; font-weight: 600; color: #1F2937; margin-bottom: 8px; }
.policy-options { display: flex; flex-direction: column; gap: 6px; }
.policy-opt-static { display: flex; align-items: center; gap: 8px; padding: 7px 9px; font-size: 12.5px; color: #374151; }
.policy-dot { width: 8px; height: 8px; border-radius: 50%; background: #D1D5DB; flex: none; }
.policy-label { line-height: 1.45; }
/* 政策利率表 */
.val-table { width: 100%; }
.val-row { display: flex; align-items: center; border-bottom: 1px solid #F0F2F5; padding: 8px 6px; }
.val-row.val-head { border-bottom: 2px solid #E5E9EF; font-weight: 600; color: #6B7280; font-size: 12px; }
.val-name { flex: 1; font-size: 13px; color: #374151; }
.val-head .val-name { color: #6B7280; }
.val-cell { flex: 0 0 110px; text-align: right; font-size: 13px; color: #1F2937; font-variant-numeric: tabular-nums; }
.val-head .val-cell { color: #6B7280; }
@media (max-width: 1100px) {
  .block-grid, .policy-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .block-grid, .policy-grid { grid-template-columns: 1fr; }
}
</style>
