<script setup lang="ts">
/**
 * AnalysisOverview —— 分析研究总览页（框架 v0.2）
 * 跟踪型模块 = 摘要卡片墙（关键信号 + 状态词，每天扫一眼）；研究型模块 = 分组目录
 * 模块来源：GET /api/analysis/registry（配置文件版注册表）
 */
import { computed, onMounted, ref } from 'vue'
import { NCard, NEmpty, NSkeleton, NSpin, NTag, NTooltip } from 'naive-ui'
import { useRouter } from 'vue-router'
import KpiHint, { kpiHintTheme } from '../components/analysis/KpiHint.vue'
import api from '../api'

interface RegistryItem {
  module_id: string
  name: string
  group: string
  kind: 'track' | 'research'
  icon?: string
  desc: string
  /** 'full' = 主卡占满整行（放下更多 KPI）；缺省为半宽卡。由后端声明，前端不硬编码模块名 */
  card_span?: 'full'
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
}
/**
 * 卡片顶部的一句话结论。**由后端生成**——口径随响应下发，前端只透传不手抄；
 * 前端拼结论句会立刻产生第二份口径，日后必然漂移（见 backend/app/analysis/registry.py 卡片墙契约）
 */
interface CardVerdict {
  headline: string
  detail?: string
  tone?: 'normal' | 'opportunity' | 'caution'
}

const GROUP_ORDER = ['市场风向', '板块与概念', '个股基本面', '资金与情绪', '跟踪清单']

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

/**
 * 卡片放几个 KPI 由**卡片宽度**决定：半宽卡 3 个、整行卡 6 个。
 * ⚠️ 整行卡的真正意义是「放下了更多 KPI」——只跨列不加内容会显得空。
 * 挑选由后端 card_rank 指定，不再取数组前 3 个：原先的 slice(0, 3) 按声明顺序截断，
 * 市场风向恰好把带分位的「大势位置」「股债性价比 ERP」截掉、只留三个同类的 20 日动量
 * （实测 2026-09-19）。口径属于后端，前端只透传。
 * 未标 card_rank 的模块回退为数组前 N 个，保证既有模块不受影响。
 */
const CARD_KPI_LIMIT: Record<string, number> = { half: 3, full: 6 }

function cardSpan(m: RegistryItem): 'full' | 'half' {
  return m.card_span === 'full' ? 'full' : 'half'
}

function pickCardKpis(kpis: CardKpi[], span: 'full' | 'half'): CardKpi[] {
  const limit = CARD_KPI_LIMIT[span] ?? 3
  const ranked = kpis
    .filter((k) => k.card_rank != null)
    .sort((a, b) => Number(a.card_rank) - Number(b.card_rank))
  return (ranked.length ? ranked : kpis).slice(0, limit)
}

/** 刻度圆点位置：夹在 5~95% 内，避免圆点在两端被轨道裁掉半个（不改变读数，只改绘制） */
function dotLeft(pct: number): string {
  return `${Math.min(95, Math.max(5, pct))}%`
}

onMounted(async () => {
  loading.value = true
  try {
    const resp: any = await api.get('/analysis/registry')
    modules.value = resp.items ?? []
    // 跟踪型卡片各自拉模块数据取 KPI 摘要（模块多了可改为专用 summary 接口）
    const tracks = modules.value.filter((x) => x.kind === 'track')
    tracks.forEach((m) => {
      kpiLoading.value[m.module_id] = true
    })
    // 并行发起、互不阻塞：耗时 = 最慢的一个，而非各模块之和。
    // silent：单卡取数失败只降级为「不显示 KPI」，不弹全局错误提示。
    void Promise.allSettled(
      tracks.map(async (m) => {
        try {
          const r: any = await api.get(`/analysis/${m.module_id}`, { silent: true })
          cardKpis.value[m.module_id] = pickCardKpis(r.kpis ?? [], cardSpan(m))
          // 判读条与 KPI 同源同请求，一起到达；没有 verdict 的模块只是不显示判读条
          if (r.verdict?.headline) cardVerdicts.value[m.module_id] = r.verdict
          else delete cardVerdicts.value[m.module_id]
        } catch {
          cardKpis.value[m.module_id] = []
          delete cardVerdicts.value[m.module_id]
        } finally {
          kpiLoading.value[m.module_id] = false
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
const researchByGroup = computed(() => {
  const map: Record<string, RegistryItem[]> = {}
  for (const m of modules.value.filter((x) => x.kind === 'research')) {
    ;(map[m.group] ??= []).push(m)
  }
  return map
})

function open(m: RegistryItem) {
  router.push(`/analysis/${m.module_id}`)
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
            <span class="ao-lg-sw" style="border-left-color: #185FA5; background: #E6F1FB" />
            <div><b>蓝色 · 常态</b>中性结论，按口径陈述现状。</div>
          </div>
          <div class="ao-lg-row">
            <span class="ao-lg-sw" style="border-left-color: #C9A227; background: #FAF3DF" />
            <div><b>金黄 · 机会</b>出现值得关注的低位/背离信号；全站金色只用于亮点强调（≤10% 场景）。</div>
          </div>
          <div class="ao-lg-row">
            <span class="ao-lg-sw" style="border-left-color: #B45309; background: #FAEEDA" />
            <div><b>琥珀 · 提醒</b>指标处于高位或值得谨慎的状态。</div>
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
    <div v-if="trackModules.length" class="ao-cards">
      <NCard
        v-for="m in trackModules"
        :key="m.module_id"
        size="small"
        hoverable
        class="ao-card"
        :class="`ao-card--${cardSpan(m)}`"
        @click="open(m)"
      >
        <div class="ao-card-head">
          <span class="ao-card-name">
            {{ m.icon }} {{ m.name }}
            <!-- 模块定位说明入口（2026-09-19）：原「回答三件事：…」整行独占卡片高度，
                 收进标题旁 ⓘ 悬浮说明（与 KPI 口径同一款 KpiHint 白底信息卡），
                 卡片更整洁、判读条上移成为首屏信息。
                 ⚠️ 点击必须 .stop —— 卡片本身是 @click=open(m) 的跳转按钮。 -->
            <KpiHint :label="m.name" :hint="m.desc" footer="模块定位说明由后端统一下发">
              <template #trigger>
                <span
                  class="ao-card-q"
                  role="button"
                  tabindex="0"
                  :aria-label="`「${m.name}」回答哪三件事`"
                  @click.stop
                >i</span>
              </template>
            </KpiHint>
          </span>
          <NTag size="tiny" :bordered="false" type="info">跟踪</NTag>
        </div>

        <!-- 判读条（2026-09-19）：卡片最前的一句话结论。**文案由后端生成**——
             口径随响应下发、前端只透传不手抄（见 backend/app/analysis/registry.py 卡片墙契约）。
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
          <div class="ao-verdict-headline">{{ cardVerdicts[m.module_id].headline }}</div>
          <div v-if="cardVerdicts[m.module_id].detail" class="ao-verdict-detail">
            {{ cardVerdicts[m.module_id].detail }}
          </div>
        </div>

        <!-- KPI 区独立占位：骨架高度对齐 .ao-kpi（8+10+行高×3+8），避免数据到达时布局跳动 -->
        <div class="ao-card-kpis" v-if="kpiLoading[m.module_id] || cardKpis[m.module_id]?.length">
          <!-- 骨架数量与高度按卡片宽度推定（整行=6 个 / 半宽=3 个），
               使加载态与完成态的盒子数量、高度一致，数据到达时不产生布局跳动 -->
          <template v-if="kpiLoading[m.module_id]">
            <NSkeleton
              v-for="i in (cardSpan(m) === 'full' ? 6 : 3)"
              :key="i"
              height="84px"
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
              <div class="ao-kpi-status">{{ k.status }}</div>
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
/* min(440px,100%)：窗口极窄时轨道不小于容器，避免卡片自身撑破内容区 */
.ao-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(440px, 100%), 1fr)); gap: 12px; }
.ao-card { cursor: pointer; }
/* 主卡占满整行（跨所有列）。宽度由后端 card_span 声明，前端不硬编码模块名 */
.ao-card--full { grid-column: 1 / -1; }
.ao-card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px;
  /* 「回答三件事」整行收进 ⓘ 后，判读条/KPI 直接跟随标题 —— 原-desc 行的
     8px 下间距移到这里，首行信息不至于贴着标题 */
  margin-bottom: 10px; }
.ao-card-name { display: inline-flex; align-items: center; gap: 5px; font-size: 15px; font-weight: 600; color: #1F2937; }
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
/* 判读条：卡片的一句话结论。三态配色沿用「蓝骨金魂」——
   常态 = 主色蓝 / 低位机会 = 金（专用于亮点，≤10% 强调）/ 高位提醒 = 琥珀。
   这里**刻意不出现红绿**：红涨绿跌是行情数字与 K 线专用，管理 UI 不参与
   （失败才用深红棕、警告用琥珀）。 */
.ao-verdict { border-left: 3px solid #185FA5; background: #E6F1FB;
  border-radius: 0 6px 6px 0; padding: 8px 10px; margin-bottom: 10px; }
.ao-verdict-headline { font-size: 13px; font-weight: 600; color: #0C447C; line-height: 1.45; }
.ao-verdict-detail { font-size: 11px; color: #185FA5; margin-top: 4px; line-height: 1.5; }
.ao-verdict--opportunity { border-left-color: #C9A227; background: #FAF3DF; }
.ao-verdict--opportunity .ao-verdict-headline { color: #7A5E12; }
.ao-verdict--opportunity .ao-verdict-detail { color: #A4841F; }
.ao-verdict--caution { border-left-color: #B45309; background: #FAEEDA; }
.ao-verdict--caution .ao-verdict-headline { color: #633806; }
.ao-verdict--caution .ao-verdict-detail { color: #854F0B; }
.ao-verdict-skel { margin-bottom: 10px; }
/* auto-fit + 确定最小轨宽：KPI 等宽、自适应个数，且数学上不可能撑破卡片
   （曾因 flex:1 的 min-width:auto 溢出 36px，第三个盒子右侧压到页面底） */
.ao-card-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px; }
/* min-height 统一各盒子高度：有/无刻度条时高度差 27px，若任其自然，
   同一行里带刻度的盒子会把邻居衬托得参差不齐 */
.ao-kpi { min-width: 0; min-height: 84px; background: #F5F7FA; border-radius: 6px; padding: 8px 10px; }
/* 标签行 = 文字 + ⓘ 口径入口。
   ⚠️ 文字必须 nowrap + ellipsis + min-width:0：CJK 可在任意字符处断行，
   缺 nowrap 时会被图标挤成「逐/字/竖/排」（本项目已踩过同类坑）。 */
.ao-kpi-label { display: flex; align-items: center; gap: 3px; font-size: 11px; color: #6B7280; }
.ao-kpi-label-txt { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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
.ao-kpi-value { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; }
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
