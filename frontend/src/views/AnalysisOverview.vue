<script setup lang="ts">
/**
 * AnalysisOverview —— 分析研究总览页（框架 v0.2）
 * 跟踪型模块 = 摘要卡片墙（关键信号 + 状态词，每天扫一眼）；研究型模块 = 分组目录
 * 模块来源：GET /api/analysis/registry（配置文件版注册表）
 */
import { computed, onMounted, ref } from 'vue'
import { NCard, NEmpty, NSkeleton, NSpin, NTooltip } from 'naive-ui'
import { useRouter } from 'vue-router'
import KpiHint, { kpiHintTheme } from '../components/analysis/KpiHint.vue'
import RichText from '../components/analysis/RichText.vue'
import api from '../api'

interface RegistryItem {
  module_id: string
  /** 卡片短名（**不含领域前缀**，如「位置」）—— 领域名由组标题承担，见 registry 契约 ⑧ */
  name: string
  group: string
  /** track=卡片墙 / research=研究目录 / detail=详情型元数据（不上墙，2026-09-19 拆卡新增） */
  kind: 'track' | 'research' | 'detail'
  icon?: string
  desc: string
  /**
   * 卡片宽度档（2026-09-25 新增，按上墙论据数选）：
   *   narrow = 1 个论据 / half = 2~3 个 / full = ≥4 个（整行）。
   * 新档 narrow 之前只有 'full'，缺省为半宽；仍由后端声明，前端不硬编码模块名。
   */
  card_span?: 'full' | 'half' | 'narrow'
  /**
   * 本卡问的那句话（2026-09-25 新增，registry 契约 ⑧）—— 卡片副标题。
   * 卡片名只留短名，用户需要「这张卡到底在回答什么」时才看得懂，问句就是这个锚。
   */
  question_text?: string
  /**
   * 拆卡字段（2026-09-19，registry 卡片墙契约 ⑦）：本卡回答第几件事 / 取数端点名 /
   * 点击跳转的详情页。三张拆卡共用同一份模块响应（后端 ttl_cache），前端按 URL 去重。
   */
  question?: 'q1' | 'q2' | 'q3'
  data?: string
  detail?: string
}
/** 分位刻度：把「这个数在 0~100 轴上排第几」画出来。
 *  ⚠️ label（窗口口径）必须由后端下发 —— ERP 的分位窗口是「近 250 个月末」而非「近一年」，
 *  前端写死会把月频分位说成日频。详见 backend/app/analysis/registry.py 卡片墙契约第 ⑤ 条 */
interface KpiScale {
  pct: number
  label: string
}
interface CardKpi {
  key?: string
  label: string
  value: number | null
  unit?: string
  status?: string
  /** updown=行情涨跌（红涨绿跌）/ diff=组间收益差（主色蓝+带符号）/ neutral=无量纲量（主色蓝） */
  tone?: 'updown' | 'diff' | 'neutral'
  /** 后端声明「上卡片的优先级」（升序，1 起）。总览页据此挑，不按数组顺序截断 */
  card_rank?: number | null
  /** 分位刻度；没有历史序列的模块不下发 → 不渲染刻度条 */
  scale?: KpiScale | null
  /** 分位进极值区（<=10 / >=90）→ 刻度圆点染金（亮得少才算信号） */
  highlight?: boolean
  /**
   * 口径说明（后端下发，含 `**强调**`）。此前本接口没声明它 → 卡片墙看不到入口，
   * 但 `pickCardKpis` 只做 filter/sort/slice、**不剥字段**，所以运行时其实一直带着
   * hint（实测 18/18 全覆盖）——纯前端接线即可，无需改后端。
   */
  hint?: string
  /**
   * 拆卡归属（后端下发，registry 契约 ⑦）：本 KPI 服务哪些「事」。一个 KPI 可属于多件
   * （如「行业中位」既是流向读数也是互证一腿）；卡片墙按本卡 question 过滤后再套 card_rank
   */
  questions?: string[]
}
/**
 * 卡片顶部的一句话结论。**由后端生成**——口径随响应下发，前端只透传不手抄；
 * 前端拼结论句会立刻产生第二份口径，日后必然漂移（见 backend/app/analysis/registry.py 卡片墙契约）
 */
interface CardVerdict {
  headline: string
  detail?: string
  /** 判读条 tone（**与 KPI 的 tone 是两个独立枚举**，勿混用）。
   *  `mixed` = 方向分化（蓝，P2 起有专属类 `.ao-verdict--mixed`）；
   *  接口侧一律经 `_to_card_tone()` 收口，保证不会产出这里没有类的值。 */
  tone?: 'normal' | 'opportunity' | 'caution' | 'mixed'
}

/**
 * 研究目录的分区顺序 = registry 里领域的出现顺序（detail 模块自身即领域，见 registry 契约的 group）。
 * ⚠️ 改前这里是硬编码的旧「固定五类」（含「个股基本面」「跟踪清单」两个从未落地的分类），
 * 与卡片墙按 detail 聚成的 5 组**不同源**（2026-09-25 收敛为一套）。
 * 派生而非硬编码：将来新增研究模块时，新领域自动出现在目录里，无需回来改这一行。
 */
const GROUP_ORDER = computed<string[]>(() => {
  const seen: string[] = []
  for (const m of modules.value) if (m.group && !seen.includes(m.group)) seen.push(m.group)
  return seen
})

const router = useRouter()
// loading 只表示「注册表本身」的加载——它是最轻的一次请求（实测 ~70ms）。
// 卡片区的 KPI 摘要另用 kpiLoading 逐卡标记，**不再阻塞整页**：
// 原先 loading 覆盖全页且串行 await 各模块数据（market-wind 4.6s），
// 用户要盯着转圈近 5 秒才看到页面结构。现在结构秒出、KPI 区各自补位。
const loading = ref(false)
const modules = ref<RegistryItem[]>([])
const cardKpis = ref<Record<string, CardKpi[]>>({})
const cardVerdicts = ref<Record<string, CardVerdict>>({})
const kpiLoading = ref<Record<string, boolean>>({})
/** 各卡的数据截止日（后端响应里的 as_of，MM-DD）—— 右上角那一格本该放它，而不是重复的「跟踪」 */
const cardAsOf = ref<Record<string, string>>({})

/**
 * 卡片放几个 KPI 由**卡片宽度档**决定：窄卡 1 个、半宽卡 3 个、整行卡 6 个。
 * ⚠️ 整行卡的真正意义是「放下了更多 KPI」——只跨列不加内容会显得空。
 * 挑选由后端 card_rank 指定，不再取数组前 3 个：原先的 slice(0, 3) 按声明顺序截断，
 * 市场风向恰好把带分位的「大势位置」「股债性价比 ERP」截掉、只留三个同类的 20 日动量
 * （实测 2026-09-19）。口径属于后端，前端只透传。
 * ⚠️ 二级挑选的**副作用**（2026-09-25 实测）：只要有任一 KPI 带 card_rank，就整体只从
 *    ranked 里取，**未标 card_rank 的论据会被整体丢掉**（「板块轮动 · 互证」曾因此丢掉概念中位）。
 *    修法是让该上墙的论据都标上 card_rank，而不是放宽这里的逻辑。
 * 未标 card_rank 的模块回退为数组前 N 个，保证既有模块不受影响。
 */
/* 卡内论据上限：窄 2 / 半宽 3 / 整行 6（2026-09-26 卡片墙 v2：窄卡由 1 → 2）
   —— 一卡一环的窄卡若只放 1 个读数，则「这个环在往哪动」缺一半证据：
      实测每环本就备了 3 个候选（价格环 4 个），卡上只显示 card_rank=1 的那个，
      而 rank2 恰是环内最该补的一项（政策→美联储总资产 / 价格→期限利差 /
      数量→欧元区 M3 / 外部→美债 10Y）。
   ⚠️ 提高到 2 只影响货币流动性 4 卡：其余 5 张 narrow 卡（market-wind-valuation /
      cross-market-conduction / funding-temperature-fx·fund·rep）候选只有 1 个，
      `slice(0, 2)` 仍返回 1 个 ⇒ 零连带影响（改前已按候选数逐卡核查，非假设）。
   代价实测：该组行高 262 → 284px（+8.4%），KPI 盒 62 → 84~111px、宽 319 → 156px；
      仍走「竖排式」而非紧凑两行网格 —— 后者只 +6px，但标签会被值挤到 ~71px 而断成
      两行（如「Shibor 3M（资金价 格）」），而括号里常是**口径警示**
      （「美元净流动性（合成 A5）」「境外美元信贷（离岸美元）」），断行会削掉它。 */
const CARD_KPI_LIMIT: Record<string, number> = { narrow: 2, half: 3, full: 6 }

type CardSpan = 'full' | 'half' | 'narrow'

function cardSpan(m: RegistryItem): CardSpan {
  if (m.card_span === 'full') return 'full'
  if (m.card_span === 'narrow') return 'narrow'
  return 'half'
}

function pickCardKpis(kpis: CardKpi[], span: CardSpan): CardKpi[] {
  const limit = CARD_KPI_LIMIT[span] ?? 3
  const ranked = kpis
    .filter((k) => k.card_rank != null)
    .sort((a, b) => Number(a.card_rank) - Number(b.card_rank))
  return (ranked.length ? ranked : kpis).slice(0, limit)
}

/**
 * 组 = 该卡 `detail` 指向的详情模块（16 张卡指向 5 个详情页 ⇒ 天然 5 组，零新增字段）。
 * ⚠️ 组内卡数**不必相等**：货币流动性组 4 卡（2026-09-26 按四环扩卡），其余 3 卡。
 *    `--ao-cols` = 组内 span 之和 ⇒ 每行必定铺满，卡数只改变**每张卡的宽度**（见 cardSpan/pickCardKpis）。
 * 组标题承担领域名，卡片只留短名 —— 原先卡片名写成「市场风向 · 位置」，而 group 字段
 * （市场风向/板块与概念/资金与情绪）只用于研究目录、卡片墙根本不渲染 ⇒ 领域名被重复 15 次、
 * 且与组标题两套分类并存（实测 3 个 group 名 vs 5 个卡片前缀）。
 */
interface TrackGroup {
  key: string
  title: string
  icon: string
  /** 组内卡片 span 之和 = 栅格列数，令每行必定铺满（避免右侧留白） */
  cols: number
  items: RegistryItem[]
}

const detailMeta = computed(() => {
  const map: Record<string, RegistryItem> = {}
  for (const m of modules.value) {
    if (m.kind === 'detail') map[`/analysis/${m.module_id}`] = m
  }
  return map
})

const SPAN_COLS: Record<CardSpan, number> = { narrow: 3, half: 4, full: 12 }

/** 刻度圆点位置：夹在 5~95% 内，避免圆点在两端被轨道裁掉半个（不改变读数，只改绘制） */
function dotLeft(pct: number): string {
  return `${Math.min(95, Math.max(5, pct))}%`
}

onMounted(async () => {
  loading.value = true
  try {
    const resp: any = await api.get('/analysis/registry')
    modules.value = resp.items ?? []
    // 跟踪型模块各自拉模块数据取 KPI 摘要（模块多了可改为专用 summary 接口）
    const tracks = modules.value.filter((x) => x.kind === 'track')
    tracks.forEach((m) => {
      kpiLoading.value[m.module_id] = true
    })
    // 拆卡去重（2026-09-19，契约 ⑦）：同一家族三张卡共用同一个 data 端点 ——
    // 后端 ttl_cache 命中虽然 <1ms，但省掉 2 次重复 HTTP 往返更干净。
    // 按 URL 分组、每组只发一次请求，结果分发给组内各卡。
    const byUrl = new Map<string, RegistryItem[]>()
    for (const m of tracks) {
      const url = `/analysis/${m.data ?? m.module_id}`
      const list = byUrl.get(url)
      if (list) list.push(m)
      else byUrl.set(url, [m])
    }
    // 并行发起、互不阻塞：耗时 = 最慢的一个，而非各模块之和。
    // silent：单卡取数失败只降级为「不显示 KPI」，不弹全局错误提示。
    void Promise.allSettled(
      [...byUrl.entries()].map(async ([url, cards]) => {
        let r: any = null
        try {
          r = await api.get(url, { silent: true })
        } catch {
          /* 整组降级：各卡不显示 KPI，finally 里收尾 */
        }
        for (const m of cards) {
          try {
            // 卡片墙取 `card_kpis`（后端下发的三组并集，P2 · 2026-09-26）——
            // 原先只读 `kpis`，而 `external_kpis` / `quantity_kpis` 是**独立数组、根本不读**
            // ⇒ 新增的「数量」「外部约束」卡会是空的。`?? r?.kpis` 是对其余 4 个模块的回退兼容。
            const kpis: CardKpi[] = r?.card_kpis ?? r?.kpis ?? []
            // 本卡只看自己那件事的 KPI（过滤口径在后端 questions，前端只透传）；
            // 过滤后为空 = 后端尚未发布 questions 字段 → 回退全量 pick，优雅降级
            const scoped = m.question
              ? kpis.filter((k) => (k.questions ?? []).includes(m.question!))
              : kpis
            cardKpis.value[m.module_id] = pickCardKpis(scoped.length ? scoped : kpis, cardSpan(m))
            // 数据截止日（as_of）：组内各卡同源、值相同；取不到就不显示（不编造「最新」）
            const asOf = r?.as_of ? String(r.as_of).slice(5) : ''
            if (asOf) cardAsOf.value[m.module_id] = asOf
            else delete cardAsOf.value[m.module_id]
            // 判读条：拆卡优先取自己那件事的子判读；后端未发布 verdicts 时回退模块级 verdict
            const v = (m.question ? r?.verdicts?.[m.question] : null) ?? r?.verdict
            if (v?.headline) cardVerdicts.value[m.module_id] = v
            else delete cardVerdicts.value[m.module_id]
          } catch {
            cardKpis.value[m.module_id] = []
            delete cardVerdicts.value[m.module_id]
          } finally {
            kpiLoading.value[m.module_id] = false
          }
        }
      }),
    )
  } catch (e) {
    console.error('[analysis-registry]', e)
  } finally {
    loading.value = false
  }
})

const trackModules = computed(() => modules.value.filter((m) => m.kind === 'track'))

/** track 卡按 detail 分组的组名/图标取自该详情模块；detail 缺失时回退公共 group（不静默丢卡） */
const trackGroups = computed<TrackGroup[]>(() => {
  const out: TrackGroup[] = []
  for (const m of trackModules.value) {
    const key = m.detail ?? `/analysis/${m.module_id}`
    let g = out.find((x) => x.key === key)
    if (!g) {
      const meta = detailMeta.value[key]
      g = { key, title: meta?.name ?? m.group, icon: meta?.icon ?? m.icon ?? '', cols: 0, items: [] }
      out.push(g)
    }
    g.items.push(m)
    g.cols += SPAN_COLS[cardSpan(m)]
  }
  return out
})

const researchByGroup = computed(() => {
  const map: Record<string, RegistryItem[]> = {}
  for (const m of modules.value.filter((x) => x.kind === 'research')) {
    ;(map[m.group] ??= []).push(m)
  }
  return map
})

/** 骨架数量与完成态一致（窄 2 / 半宽 3 / 整行 6），否则数据到达时布局会跳 */
function kpiSkeletonCount(m: RegistryItem): number {
  return CARD_KPI_LIMIT[cardSpan(m)] ?? 3
}

function open(m: RegistryItem) {
  // 拆卡跳详情：同一领域的各张分卡同进一个聚合详情页（registry 契约 ⑦ detail 字段）
  router.push(m.detail ?? `/analysis/${m.module_id}`)
}
// 与 KpiCards 一致：只有 tone='updown' 才是行情涨跌语义（红涨绿跌）。
// tone='diff'（组间收益差，如风偏分数）与 'neutral'（分位/离散度）一律主色蓝 ——
// 「防守占优」染绿会看起来像一条独立警报，且卡片上一蓝一绿会被误读成两类指标。
// 卡片墙的颜色只表达**异常程度**（极值染金），方向交给符号、status 与刻度条。
function kpiCls(k: CardKpi): string {
  if (k.value == null) return 'color:#909399'
  if (k.tone === 'diff' || k.tone === 'neutral') return 'color:#185FA5'
  return k.value > 0 ? 'color:#EF232A' : k.value < 0 ? 'color:#14B143' : 'color:#909399'
}
function kpiText(k: CardKpi): string {
  if (k.value == null) return '--'
  const sign = k.tone !== 'neutral' && k.value > 0 ? '+' : ''
  return `${sign}${k.value}${k.unit ?? ''}`
}
/**
 * 判读条 `title` 悬浮提示用的纯文本：剥掉后端文案里的 `**强调**` 标记
 * （原生 title 不渲染任何标记，留着会露出星号）。
 * 判读条默认只显示前几行（见 <style> 的 line-clamp），全文靠它兜底 ——
 * 卡片墙是摘要视图，详情页才有完整版，二者内容同源不产生第二份口径。
 */
function plain(t?: string | null): string {
  return String(t ?? '').replace(/\*\*/g, '')
}
</script>

<template>
  <NSpin :show="loading">
    <!-- 页面标题刻意没有：侧边栏导航已标明当前位置，页内再放「分析研究」h2 纯属重复
         （全站其余页面也都没有页内大标题，此处曾是唯一特例）。首行直接是「📊 跟踪」分节。 -->

    <!-- 跟踪卡片区 -->
    <div class="ao-section">
      <span>📊 跟踪</span>
      <!-- 配色图例（2026-09-19）：用户反馈「浅黄/浅蓝判读条不知道什么寓意」。
           判读条三态与 KPI 染金的语义是设计体系约定（蓝骨金魂），不是逐模块数据，
           所以前端静态图例是正解（不违背「口径随响应下发」——那约束的是指标计算口径）。
           浮窗复用 kpiHintTheme，全站浮窗观感一致。 -->
      <NTooltip trigger="hover" placement="bottom-start" :theme-overrides="kpiHintTheme" :delay="150">
        <template #trigger>
          <span
            class="ao-card-q"
            role="button"
            tabindex="0"
            aria-label="卡片配色图例：判读条三色与 KPI 染金分别代表什么"
          >i</span>
        </template>
        <div class="ao-lg">
          <div class="ao-lg-t">判读条配色</div>
          <div class="ao-lg-row">
            <span class="ao-lg-sw" style="border-left-color: #8A919C; background: #F3F4F5" />
            <div><b>灰色 · 常态</b>中性结论，按口径陈述现状——退后当背景，不抢注意力。</div>
          </div>
          <div class="ao-lg-row">
            <span class="ao-lg-sw" style="border-left-color: #C9A227; background: #FAF3DF" />
            <div><b>金黄 ✦ · 机会</b>出现值得关注的低位/背离信号；全站金色只用于亮点强调（≤10% 场景）。</div>
          </div>
          <div class="ao-lg-row">
            <span class="ao-lg-sw" style="border-left-color: #C2410C; background: #FBEAE2" />
            <div><b>赭橙 ▲ · 提醒</b>指标处于高位或值得谨慎的状态——三态靠色相、明度、符号三重区分。</div>
          </div>
          <div class="ao-lg-t" style="margin-top: 9px">KPI 刻度条</div>
          <div class="ao-lg-row">
            <span class="ao-lg-dot" />
            <div><b>圆点染金</b>该值在统计窗口里排进前/后 10%（分位 ≤10 或 ≥90）—— 亮得少才算信号。</div>
          </div>
          <div class="ao-lg-ft">判读条与结论文案由后端随响应下发，前端只透传；管理界面刻意不用红绿 —— 红涨绿跌只属于行情数字与 K 线。</div>
        </div>
      </NTooltip>
    </div>
    <div v-if="trackGroups.length" class="ao-groups">
      <section v-for="g in trackGroups" :key="g.key" class="ao-track-group">
        <!-- 组标题承担领域名（registry 契约 ⑧）：卡片名只留短名，领域名全页只出现 5 次 -->
        <div class="ao-track-group-title">{{ g.icon }} {{ g.title }}</div>
        <!-- 列数 = 组内各卡 span 之和 ⇒ 每行必定铺满（否则窄卡会留下右侧空档）。
             用 CSS 变量（而非写死列数）是为了让断点能整组降级：见 <style> 里的 @media -->
        <div class="ao-cards" :style="{ '--ao-cols': String(g.cols) }">
          <NCard
            v-for="m in g.items"
            :key="m.module_id"
            size="small"
            hoverable
            class="ao-card"
            :class="`ao-card--${cardSpan(m)}`"
            @click="open(m)"
          >
            <div class="ao-card-head">
              <span class="ao-card-name">
                {{ m.name }}
                <!-- 本卡口径入口（2026-09-19）：原「回答三件事：…」整行独占卡片高度，
                     收进标题旁 ⓘ 悬浮说明（与 KPI 口径同一款 KpiHint 白底信息卡），
                     卡片更整洁、判读条上移成为首屏信息。
                     ⚠️ 点击必须 .stop —— 卡片本身是 @click=open(m) 的跳转按钮。 -->
                <KpiHint :label="`${g.title} · ${m.name}`" :hint="m.desc" footer="本卡口径由后端统一下发">
                  <template #trigger>
                    <span
                      class="ao-card-q"
                      role="button"
                      tabindex="0"
                      :aria-label="`「${g.title} · ${m.name}」的卡片口径说明`"
                      @click.stop
                    >i</span>
                  </template>
                </KpiHint>
              </span>
              <!-- 右上角那一格：数据截止日。原先放的是「跟踪」标签 —— 与分节标题「📊 跟踪」
                   重复 15 次，而按规范 §2.1 它本该放数据新鲜度（as_of 由后端随响应下发，
                   取不到就不显示，前端不编造「最新」） -->
              <span v-if="cardAsOf[m.module_id]" class="ao-card-asof">截至 {{ cardAsOf[m.module_id] }}</span>
            </div>

            <!-- 本卡问的那句话（registry 契约 ⑧）：短名 + 问句，才读得懂这张卡在答什么 -->
            <div v-if="m.question_text" class="ao-card-ask">{{ m.question_text }}</div>

            <!-- 判读条（2026-09-19）：卡片最前的一句话结论。**文案由后端生成**——
                 口径随响应下发、前端只透传不手抄（见 backend/app/analysis/registry.py 卡片墙契约）。
                 ⚠️ 2026-09-25 修：headline/detail 原先用 `{{ }}` 直出，后端写在文案里的
                    `**强调**` 会原样渲染（实测「钱贵不贵 · 预期」的 detail 显示成
                    「…；**倒挂**（3M 低于隔夜）= …」）。凡后端下发的散文类文案一律走 RichText。
                 ⚠️ 骨架必须单独占位：它与 KPI 是同一个请求返回，若只给 KPI 占位，
                    判读条插入时会把下面整排盒子顶下去，产生可见的布局跳动。 -->
            <div v-if="kpiLoading[m.module_id]" class="ao-verdict-skel">
              <NSkeleton height="54px" :sharp="false" />
            </div>
            <div
              v-else-if="cardVerdicts[m.module_id]"
              class="ao-verdict"
              :class="`ao-verdict--${cardVerdicts[m.module_id].tone || 'normal'}`"
            >
              <!-- title = 全文兜底：判读条默认只显示前 3 / 2 行（见 <style> 的 line-clamp），
                   悬停即读全文。卡片墙是摘要视图，长文案不该把整行卡片顶高 ——
                   实测「数量」卡 128 字 headline + 298 字 detail 曾把该行撑到 453px，
                   而同组其它卡内容只有 199px（56% 空白）。 -->
              <div class="ao-verdict-headline" :title="plain(cardVerdicts[m.module_id].headline)">
                <RichText :text="cardVerdicts[m.module_id].headline" />
              </div>
              <div
                v-if="cardVerdicts[m.module_id].detail"
                class="ao-verdict-detail"
                :title="plain(cardVerdicts[m.module_id].detail)"
              >
                <RichText :text="cardVerdicts[m.module_id].detail" />
              </div>
            </div>

            <!-- KPI 区独立占位：骨架高度对齐 .ao-kpi 的**实测高度**，避免数据到达时布局跳动。
                 实测（1440px、24 个盒子）：84px×7 / 97px×2 / 111px×9 / 126px×6 ——
                   84  = .ao-kpi 的 min-height（无刻度条且标签不折行）
                   111 = 有刻度条（众数，占 9/24）
                   126 = 刻度条文字在窄列里折成 2 行 / 标签折行 2 行
                 改前固定 84px ⇒ 加载态比完成态矮 0~42px，5 组累计位移 138px；
                 取众数 111px 后累计位移降到 57px（残留：纯短标签行 -27px、折行标签行 +15px）。
                 取众数而非最大值：取 126 会让 3 个 84px 的组反向塌缩 42px，总位移更大。 -->
            <div class="ao-card-kpis" v-if="kpiLoading[m.module_id] || cardKpis[m.module_id]?.length">
              <!-- 骨架数量按卡片宽度档推定（窄 1 / 半宽 3 / 整行 6），
                   使加载态与完成态的盒子数量、高度一致，数据到达时不产生布局跳动 -->
              <template v-if="kpiLoading[m.module_id]">
                <NSkeleton
                  v-for="i in kpiSkeletonCount(m)"
                  :key="i"
                  height="111px"
                  :sharp="false"
                />
              </template>
              <template v-else>
                <div v-for="k in cardKpis[m.module_id]" :key="k.key || k.label" class="ao-kpi">
                  <!-- 口径入口（2026-09-19）：规范 §1.1 第 4 条「口径透明」在卡片墙落地。
                       ⚠️ 触发元素必须是 ⓘ 本身而非整卡：卡片是 @click=open(m) 的跳转按钮，
                          整卡悬浮会与「点击进详情」互相干扰；且点击 ⓘ 必须 .stop，
                          否则冒泡到卡片 → 想看口径却被跳进详情页。 -->
                  <div class="ao-kpi-label">
                    <span class="ao-kpi-label-txt" :title="k.label">{{ k.label }}</span>
                    <KpiHint v-if="k.hint" :label="k.label" :value="kpiText(k)" :hint="k.hint">
                      <template #trigger>
                        <span
                          class="ao-kpi-q"
                          role="button"
                          tabindex="0"
                          :aria-label="`查看「${k.label}」的口径说明`"
                          @click.stop
                        >i</span>
                      </template>
                    </KpiHint>
                  </div>
                  <div class="ao-kpi-value" :style="kpiCls(k)">{{ kpiText(k) }}</div>
                  <!-- status 同为后端下发散文（如「偏便宜｜10Y 1.67%」），可能带 `**强调**`，故走 RichText -->
                  <div class="ao-kpi-status"><RichText :text="k.status" /></div>
                  <!-- 分位刻度（2026-09-19）：绝对 pp 跨期不可比，刻度回答「这个数在近一年排第几」。
                       窗口口径（label）由后端下发，前端不写死「近一年」——ERP 是「近 250 个月末」。
                       极值（分位 <=10 或 >=90）圆点染金：亮得少才算信号。 -->
                  <div
                    v-if="k.scale"
                    class="ao-kpi-scale"
                    :title="`分位刻度：该值在「${k.scale.label}」区间中的排位（0=区间最低，100=最高）`"
                  >
                    <span class="ao-kpi-track">
                      <span
                        class="ao-kpi-dot"
                        :class="{ 'is-hl': k.highlight }"
                        :style="{ left: dotLeft(k.scale.pct) }"
                      />
                    </span>
                    <span class="ao-kpi-scale-text" :class="{ 'is-hl': k.highlight }">
                      {{ k.scale.label }} {{ k.scale.pct }}%
                    </span>
                  </div>
                </div>
              </template>
            </div>
          </NCard>
        </div>
      </section>
    </div>
    <NEmpty v-else description="暂无跟踪型模块" size="small" />

    <!-- 研究目录区 -->
    <div class="ao-section">🔬 研究</div>
    <template v-for="g in GROUP_ORDER" :key="g">
      <div v-if="researchByGroup[g]?.length" class="ao-group">
        <div class="ao-group-name">{{ g }}</div>
        <div class="ao-group-items">
          <NCard v-for="m in researchByGroup[g]" :key="m.module_id" size="small" hoverable class="ao-item" @click="open(m)">
            <!-- 与跟踪卡同一处理：desc 收进 ⓘ，目录条目只留一行名称 -->
            <span class="ao-item-name">
              <b>{{ m.icon }} {{ m.name }}</b>
              <KpiHint :label="m.name" :hint="m.desc" footer="模块定位说明由后端统一下发">
                <template #trigger>
                  <span
                    class="ao-card-q"
                    role="button"
                    tabindex="0"
                    :aria-label="`「${m.name}」的模块说明`"
                    @click.stop
                  >i</span>
                </template>
              </KpiHint>
            </span>
          </NCard>
        </div>
      </div>
    </template>
    <NEmpty v-if="!modules.some((m) => m.kind === 'research')" description="研究型模块陆续接入中…" size="small" />
  </NSpin>
</template>

<style scoped>
/* 首个分节贴顶：页内无大标题，不需要额外上边距 */
.ao-section:first-of-type { margin-top: 0; }
.ao-section { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 600; color: #185FA5; margin: 18px 0 10px; }
/* 配色图例浮窗内容（触发元素复用 .ao-card-q 样式） */
.ao-lg { max-width: 330px; }
.ao-lg-t { font-size: 11px; font-weight: 600; color: #9CA3AF; letter-spacing: 0.02em; margin-bottom: 6px; }
.ao-lg-row { display: flex; align-items: flex-start; gap: 7px; margin-bottom: 6px; font-size: 12px; color: #374151; line-height: 1.5; }
.ao-lg-row b { color: #1F2937; margin-right: 2px; }
.ao-lg-sw { flex: none; width: 18px; height: 12px; border-radius: 3px; border-left: 3px solid; margin-top: 2px; }
.ao-lg-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: #C9A227; margin-top: 4px; }
.ao-lg-ft { font-size: 10.5px; color: #9CA3AF; margin-top: 7px; padding-top: 6px; border-top: 1px solid #F1F4F8; }
/* 跟踪卡片墙：按 detail 分 5 组，每组一个领域标题（卡片名只留短名，见 registry 契约 ⑧）。
   ⚠️ 类名刻意不叫 .ao-group —— 那是下方「研究目录」分组在用的类（.ao-group-name/.ao-group-items）。 */
.ao-groups { display: flex; flex-direction: column; gap: 14px; }
.ao-track-group-title { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 8px; }
/* 列数由组内卡片 span 之和决定（由 :style 下发 --ao-cols）⇒ 每行必定铺满，右侧不留空档。
   minmax(0,1fr) 而非 1fr：1fr 的最小尺寸是 auto，长内容会把列撑破（本项目已踩过溢出坑）。 */
.ao-cards { display: grid; grid-template-columns: repeat(var(--ao-cols, 12), minmax(0, 1fr)); gap: 12px; }
/* 卡片内容区改纵向 flex：让 KPI 区能被 margin-top:auto 推到底边（见 .ao-card-kpis）。
   display 与栅格的 grid-column 放置互不干扰，改 flex 不影响档宽。
   ⚠️ Naive UI 的内容区类名是 `.n-card-content`（**不是** `.n-card__content`）——
      写错不会报错，只是选择器永不命中、margin-top:auto 静默失效
      （实测留空仍有 30~73px，但断言此前只量了盒高、看不出）。组件内部元素须 :deep() 穿透。 */
.ao-card { cursor: pointer; display: flex; flex-direction: column; }
.ao-card :deep(.n-card-content) { display: flex; flex-direction: column; min-height: 0; }
/* 卡片宽度分档（2026-09-25）：宽度由后端 card_span 声明，前端不硬编码模块名。
   narrow=3（1 个论据）/ half=4（2~3 个）/ full=12（≥4 个，整行） */
.ao-card--narrow { grid-column: span 3; }
.ao-card--half { grid-column: span 4; }
.ao-card--full { grid-column: span 12; }
/* 断点整组降级：容器列数覆盖 --ao-cols，卡片 span 一律压到 1 列 ——
   否则窄卡会被压到放不下一个 KPI 盒（盒子 min-width 110px） */
@media (max-width: 1200px) {
  .ao-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .ao-card--narrow, .ao-card--half, .ao-card--full { grid-column: span 1; }
}
@media (max-width: 760px) {
  .ao-cards { grid-template-columns: minmax(0, 1fr); }
}
.ao-card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px;
  /* 「回答三件事」整行收进 ⓘ 后，判读条/KPI 直接跟随标题 —— 原-desc 行的
     8px 下间距移到这里，首行信息不至于贴着标题 */
  margin-bottom: 2px; }
.ao-card-name { display: inline-flex; align-items: center; gap: 5px; font-size: 15px; font-weight: 600; color: #1F2937; }
/* 数据截止日：右上角那一格（原先放的是与分节标题重复的「跟踪」标签）。
   刻意用灰而非琥珀 —— 它是常态信息，滞后告警由详情页的 .mw-asof b.stale 承担。 */
.ao-card-asof { flex: none; font-size: 11px; color: #9CA3AF; font-variant-numeric: tabular-nums; }
/* 本卡问的那句话：短名 + 问句才读得懂这张卡在答什么（registry 契约 ⑧） */
.ao-card-ask { font-size: 12px; color: #6B7280; margin: 0 0 9px; }
/* 模块定位说明的 ⓘ：与 .ao-kpi-q 同款描边（#8D97A5，实测更淡的 #B8BEC9 不可见），
   但**常显**——每卡只有 1 个图标（不像 KPI 一卡 6 个），不存在「与数字抢注意力」，
   而它是模块说明的唯一入口，常显才有可发现性。略大于 KPI 版以配标题字号。 */
.ao-card-q {
  flex: none; width: 14px; height: 14px; line-height: 12px; text-align: center;
  border-radius: 50%; border: 1px solid #8D97A5; color: #6B7280;
  font-size: 10px; font-weight: 600; font-style: italic;
  cursor: help; user-select: none; opacity: 0.75; transition: opacity 0.15s;
}
.ao-card-q:hover, .ao-card-q:focus-visible { opacity: 1; border-color: #185FA5; color: #185FA5; background: #E6F1FB; outline: none; }
/* 判读条：卡片的一句话结论。三态配色（2026-09-19 评审定稿方案二）——
   常态 = 中性灰（大多数卡的状态，退后当背景，不抢注意力）/
   低位机会 = 金（专用于亮点，≤10% 强调，叠加 ✦ 符号）/
   高位提醒 = 赭橙 #C2410C（比琥珀深两档，与金在色相+明度双维拉开，叠加 ▲ 符号）。
   三态在色相、明度、符号三个维度同时区分，色弱用户也能认读。
   这里**刻意不出现红绿**：红涨绿跌是行情数字与 K 线专用，管理 UI 不参与
   （失败才用深红棕）。注意：通用「警告」token 仍是琥珀 #B45309，
   赭橙仅用于判读条三态语义（ao-verdict / ft-temp），勿混用。 */
.ao-verdict { border-left: 3px solid #8A919C; background: #F3F4F5;
  border-radius: 0 6px 6px 0; padding: 8px 10px; margin-bottom: 10px; }
/* 行数收口（2026-09-26 版面优化）：判读条默认只显示 headline ≤3 行 / detail ≤2 行，
   全文走 `title` 悬浮（见模板）。**不截内容、只截显示行数** ——
   卡片墙是「扫一眼」的摘要视图，若放任文案长度决定卡高，一张长文案卡会把整行顶高：
   实测「数量」卡（128 字 headline + 298 字 detail）判读条 279px ⇒ 该行 453px，
   而同组「政策」卡内容只有 199px（56% 空白）。收口后该行 453→255px。
   RichText 只产出 inline 内容（文本 + <strong>），故 -webkit-line-clamp 可正常工作。 */
.ao-verdict-headline { font-size: 13px; font-weight: 600; color: #4B5563; line-height: 1.45;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }
.ao-verdict-detail { font-size: 11px; color: #6B7280; margin-top: 4px; line-height: 1.5;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
.ao-verdict--opportunity { border-left-color: #C9A227; background: #FAF3DF; }
.ao-verdict--opportunity .ao-verdict-headline { color: #7A5E12; }
.ao-verdict--opportunity .ao-verdict-headline::before { content: '✦ '; }
.ao-verdict--opportunity .ao-verdict-detail { color: #A4841F; }
.ao-verdict--caution { border-left-color: #C2410C; background: #FBEAE2; }
.ao-verdict--caution .ao-verdict-headline { color: #7C2D12; }
.ao-verdict--caution .ao-verdict-headline::before { content: '▲ '; }
.ao-verdict--caution .ao-verdict-detail { color: #9A3412; }
/* 分化（mixed）= 蓝（P2 · 2026-09-26 新增，决策 D5=(b)）：「四个货币区方向背离」是本页
   数量维度的核心叙事，值得一个专属色。⚠️ 新增前接口会下发 `mixed` 却**没有这个类** ⇒
   判读条静默降级为灰、语义被吃掉且不报错；接口侧已同步加 `_to_card_tone()` 收口兜底。 */
.ao-verdict--mixed { border-left-color: #185FA5; background: #EAF2FB; }
.ao-verdict--mixed .ao-verdict-headline { color: #185FA5; }
.ao-verdict--mixed .ao-verdict-headline::before { content: '◆ '; }
.ao-verdict--mixed .ao-verdict-detail { color: #4A7BA7; }
.ao-verdict-skel { margin-bottom: 10px; }
/* auto-fit + 确定最小轨宽：KPI 等宽、自适应个数，且数学上不可能撑破卡片
   （曾因 flex:1 的 min-width:auto 溢出 36px，第三个盒子右侧压到页面底） */
.ao-card-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px;
  /* 吸附底边（2026-09-26 版面优化）：同组卡片被栅格强制等高，内容少的卡会把空白全堆在
     底部，像「内容没写完」。推到底后留白集中到中部，整行卡片的 KPI 区底边齐平。 */
  margin-top: auto; }
/* 单盒横向化（2026-09-26 版面优化）：
   窄卡的论据上限是 1（CARD_KPI_LIMIT.narrow = 1），而 auto-fit 会把唯一一盒拉到 97% 卡宽
   —— 319px 的板子里只放「标签 / 大数 / 状态」，实测 84~111px 高却极空旷。
   单盒时改两行网格：上行「标签 ←→ 大数」、下行「状态 ←→ 刻度」，高度 111→约 62px，
   同时把横向空档用起来（无刻度条时右列 auto 宽为 0，状态自动占满整行）。
   ⚠️ 只在 :only-child 时生效，多盒布局完全不变；:has() 需 Chrome 105+（本项目跑 152）。 */
.ao-card-kpis:has(> .ao-kpi:only-child) .ao-kpi {
  display: grid; grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: 'label value' 'status scale';
  align-items: center; column-gap: 12px; row-gap: 1px;
  min-height: 0; padding: 9px 12px;
}
.ao-card-kpis:has(> .ao-kpi:only-child) .ao-kpi-label { grid-area: label; min-width: 0; }
.ao-card-kpis:has(> .ao-kpi:only-child) .ao-kpi-value {
  grid-area: value; font-size: 22px; line-height: 1.15; text-align: right;
}
.ao-card-kpis:has(> .ao-kpi:only-child) .ao-kpi-status { grid-area: status; min-width: 0; }
.ao-card-kpis:has(> .ao-kpi:only-child) .ao-kpi-scale { grid-area: scale; margin-top: 0; width: 150px; }
/* min-height 统一各盒子高度：有/无刻度条时高度差 27px，若任其自然，
   同一行里带刻度的盒子会把邻居衬托得参差不齐。
   实测高度分布（1440px、24 个盒子）：84（= 本条 min-height）/ 97 / 111（众数）/ 126。
   加载骨架按众数 111px 渲染（见模板注释）—— min-height 只保证下限，骨架另需一次对齐。 */
.ao-kpi { min-width: 0; min-height: 84px; background: #F5F7FA; border-radius: 6px; padding: 8px; }
/* 标签行 = 文字 + ⓘ 口径入口。
   ⚠️ 文字必须 min-width:0 + 显式宽度约束：CJK 可在任意字符处断行，缺约束时会被图标挤成
   「逐/字/竖/排」（本项目已踩过同类坑）。
   2026-09-25：由「nowrap + ellipsis 单行」改为「折行 2 行」——实测装 3 个论据的半宽卡
   每格仅 ~132px、标签可用宽度 91px（11px CJK ≈ 8.3 字/行），4/24 个 label 被截断
   （最重 20px：「大小盘剪刀差（20日）」「政策敏感（20日超额）」）。
   折行而非缩短文案：**口径文案不该为排版让步**（后端下发什么就显示什么）；
   代价是含长标签的那一行卡片高 ~14px（title 仍保留作兜底）。 */
.ao-kpi-label { display: flex; align-items: flex-start; gap: 3px; font-size: 11px; line-height: 1.45; color: #6B7280; }
.ao-kpi-label-txt {
  flex: 1 1 auto; min-width: 0;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
  overflow: hidden; word-break: break-word;
}
/* 描边款 ⓘ（设计原型 v0.1 的推荐形态：信息类通用符号，语义最准、不抢数字）。
   描边取 #8D97A5 —— 实测 #B8BEC9 在 14px 下过淡，缩略图/低分屏几乎不可见。
   刻意不用感叹号：琥珀 #B45309 在项目里已被「数据延迟 / cron 无效」占用，
   会被读成「这个指标出问题了」。 */
.ao-kpi-q {
  flex: none; width: 13px; height: 13px; line-height: 11px; text-align: center;
  border-radius: 50%; border: 1px solid #8D97A5; color: #6B7280;
  font-size: 9px; font-weight: 600; font-style: italic;
  cursor: help; user-select: none;
  /* 悬停显形：卡片信息密集，18 个图标常显会与数字抢注意力；
     鼠标进入卡片时淡入（图标此时已在可视范围内，可发现性不受影响）。
     触摸设备无 hover → 常显，否则等于没有入口。 */
  opacity: 0; transition: opacity 0.15s;
}
.ao-card:hover .ao-kpi-q, .ao-card:focus-within .ao-kpi-q { opacity: 1; }
.ao-kpi-q:hover, .ao-kpi-q:focus-visible { border-color: #185FA5; color: #185FA5; background: #E6F1FB; outline: none; }
@media (hover: none) { .ao-kpi-q { opacity: 1; } }
/* 值：**绝不截断、也绝不压出盒外**（金融数字截断 = 误读风险）。
   原为 `white-space: nowrap` —— 窄卡 2 盒时（实测 1440 视口・货币流动性卡 293px・盒仅 126px）
   「6.75 万亿美元」需 125px 而盒内只有 106px ⇒ 值直接压出盒子、盖到相邻盒上。
   改为 `overflow-wrap: break-word`：盒够宽时不换行；不够宽时**优先断在空格**，
   单位词保持完整（「23.34」/「万亿美元」）。
   ⚠️ 不能用 `anywhere` —— 它允许在任意字符处断行，实测 1680 下把「23.34 万亿美元」
      断成「23.34 万亿美 / 元」（单行只差约 2px），切断单位词是不可接受的；
   ⚠️ 同时把 `.ao-kpi` 的横向 padding 由 10px 收到 8px（内容宽 136 → 140px），
      让「23.34 万亿美元」在 1680 的 156px 盒里**一行放得下**（实测该值约 138px）。
      这是「2 盒并排」的直接代价，只影响 KPI 盒内部留白，不影响卡宽与栅格。
   注意与 `.ao-kpi-status` 的区别：status 带 `text-overflow: ellipsis`，是**刻意的截断**，
   溢出量属预期；值不允许截断。 */
.ao-kpi-value { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; overflow-wrap: break-word; }
.ao-kpi-status { font-size: 11px; color: #6B7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 分位刻度条：轨道 + 圆点 + 窗口文字，竖排。
   竖排而非横排 —— ERP 的窗口文字「近 250 个月末」很长，横排会把轨道压到只剩几像素，
   刻度就失去意义了。 */
.ao-kpi-scale { margin-top: 6px; }
.ao-kpi-track { position: relative; display: block; height: 4px; border-radius: 2px; background: #E3E8EF; }
.ao-kpi-dot {
  position: absolute; top: 50%; width: 8px; height: 8px; margin-left: -4px;
  border-radius: 50%; background: #185FA5; transform: translateY(-50%);
  box-shadow: 0 0 0 2px #F5F7FA;   /* 与 .ao-kpi 底色同色，圆点压在轨道上有描边感 */
}
.ao-kpi-dot.is-hl { background: #C9A227; }
.ao-kpi-scale-text {
  display: block; font-size: 10px; color: #9CA3AF; margin-top: 3px;
  font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ao-kpi-scale-text.is-hl { color: #C9A227; font-weight: 500; }
.ao-group { margin-bottom: 12px; }
.ao-group-name { font-size: 12px; color: #6B7280; margin-bottom: 6px; }
.ao-group-items { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.ao-item { cursor: pointer; }
.ao-item-name { display: flex; align-items: center; gap: 5px; min-width: 0; }
.ao-item-name b { font-size: 13.5px; color: #1F2937; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
