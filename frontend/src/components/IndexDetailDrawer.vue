<script setup lang="ts">
/**
 * IndexDetailDrawer —— 指数详情抽屉
 * 释义档案（index_profile）+ 成分股列表（index_constituents join stock_info.industry）
 *   + 行业分布（权重加权/等权）
 * 数据源：GET /api/market/index-detail?code=
 *
 * 行业口径是 **stock_info.industry = 证监会行业分类**，不是东财行业（2026-09-24 勘误：
 * 原注释写「join 东财行业」失实）—— 这也是为什么榜单里是「计算机、通信和其他电子设备
 * 制造业」这类长名称，而非东财口径的「电子元件」。
 *
 * ⚠️ 这里有两个语义完全不同的「其他」，不可合并展示（2026-09-24 重构）：
 *   · 「其他 N 个行业」—— 第 11 名及以后的**真实行业**被折叠（显示聚合，通常是最大一块）
 *   · 「未分类 M 只」 —— 后端兜底桶：成分在本地名册查不到 industry（**数据缺口**，通常极小）
 *   改造前两者共用「其他(N)」一个标签，实测中证全指显示「其他(74) 34.99%」，
 *   而其中真正的数据缺口只有 21 只 / 0.10% —— 一个 0.1% 的缺口被伪装成 34.9% 的折叠量。
 */
import { computed, h, ref, watch } from 'vue'
import {
  NDataTable, NDrawer, NDrawerContent, NEmpty, NInput, NSpin, NTabPane, NTabs, NTag,
  type DataTableColumns,
} from 'naive-ui'
import VChart from 'vue-echarts'
import api from '../api'
import { ensureEcharts, BRAND_COLOR, SERIES_PALETTE } from '../echarts'

ensureEcharts()

interface Props {
  show: boolean
  indexCode: string
  indexName: string
}
const props = defineProps<Props>()
const emit = defineEmits<{ (e: 'update:show', v: boolean): void }>()

interface ConRow {
  stock_code: string
  stock_name: string | null
  weight: number | null
  trade_date: string | null
  source: string
  industry: string | null
}
interface IndustryRow {
  industry: string
  count: number
  weight_pct: number
  /** 后端兜底桶标记：该行不是行业，而是「本地名册查不到行业」的成分合计（数据缺口） */
  is_unclassified?: boolean
}
interface Detail {
  profile: {
    index_code: string
    index_name: string
    description: string | null
    base_date: string | null
    base_point: string | null
  }
  derived_note: string | null
  /** 口径不适用说明（债券指数等）：非空时 UI 展示说明，而不是笼统的「暂缺」 */
  cons_note: string | null
  constituents: { total: number; has_weight: boolean; items: ConRow[] }
  industry_dist: IndustryRow[]
  /** 「未分类」桶汇总（本地名册无行业分类的成分）：单独标注只数，不混进「其他」 */
  industry_unclassified: { count: number; weight_pct: number }
  /** 成分在本地行业库的匹配率（%）：过低时 industry_note 给说明，不画假饼图 */
  industry_coverage: number
  industry_note: string | null
}

const loading = ref(false)
const detail = ref<Detail | null>(null)
const errorMsg = ref('')
const keyword = ref('')

async function load() {
  loading.value = true
  errorMsg.value = ''
  keyword.value = ''
  detail.value = null
  try {
    detail.value = await api.get('/market/index-detail', { params: { code: props.indexCode } }) as unknown as Detail
  } catch (e) {
    console.error('[index-detail]', e)
    errorMsg.value = '指数详情加载失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.show, props.indexCode] as const,
  ([s]) => {
    if (s && props.indexCode) load()
  },
  { immediate: true },
)

// ---------- 成分股表 ----------
const filtered = computed(() => {
  const items = detail.value?.constituents.items ?? []
  const k = keyword.value.trim()
  if (!k) return items
  return items.filter(
    (r) => r.stock_code.includes(k) || (r.stock_name || '').includes(k) || (r.industry || '').includes(k),
  )
})

const consColumns = computed<DataTableColumns<ConRow>>(() => [
  {
    title: '代码',
    key: 'stock_code',
    width: 90,
    render: (r) => h('span', { style: { fontFamily: 'Consolas,Menlo,monospace', fontSize: '12.5px' } }, r.stock_code),
  },
  { title: '名称', key: 'stock_name', width: 110, ellipsis: { tooltip: true } },
  {
    title: '权重',
    key: 'weight',
    width: 84,
    align: 'right',
    render: (r) =>
      r.weight != null
        ? h('span', { style: { fontVariantNumeric: 'tabular-nums' } }, `${Number(r.weight).toFixed(2)}%`)
        : h('span', { style: { color: '#c0c4cc' } }, '--'),
  },
  { title: '行业', key: 'industry', width: 110, ellipsis: { tooltip: true } },
])

const rowProps = () => ({ style: 'cursor: default;' })

// ---------- 行业分布（饼图 + 榜单） ----------
const pieOption = computed(() => {
  const dist = detail.value?.industry_dist ?? []
  if (!dist.length) return null
  // 「未分类」不是行业，先摘出来单独成片 —— 否则它会混进「其他 N 个行业」，
  // 把「数据缺口」和「行业被折叠」两件事糊成一个数字（改造前实测正是如此）
  const real = dist.filter((d) => !d.is_unclassified)
  const unc = dist.find((d) => d.is_unclassified)
  const top = real.slice(0, 10)
  const rest = real.slice(10)
  const data = top.map((d, i) => ({
    name: d.industry,
    value: d.weight_pct,
    itemStyle: { color: SERIES_PALETTE[i % SERIES_PALETTE.length] },
  }))
  if (rest.length) {
    const restPct = rest.reduce((s, d) => s + d.weight_pct, 0)
    // 名字里带上是「几个行业」，而不是那几个行业里的几只股票 —— 原实现写作「其他(74)」，
    // 极易被读成「74 只成分股」
    data.push({
      name: `其他 ${rest.length} 个行业`,
      value: Number(restPct.toFixed(2)),
      itemStyle: { color: '#D5DBE3' },
    })
  }
  if (unc) {
    data.push({
      name: `未分类 ${unc.count} 只`,
      value: unc.weight_pct,
      // 中性信息灰（同 .cons-note 边框色）：与「行业被折叠」的浅灰底 `#D5DBE3` 明显区分，
      // 又不抢占色板位置（色板刻意避开红/绿，防止与红涨绿跌混淆）
      itemStyle: { color: '#8A919C' },
    })
  }
  return {
    backgroundColor: '#fff',
    animation: false,
    tooltip: {
      trigger: 'item',
      formatter: (p: { name: string; value: number; percent: number }) =>
        `${p.name}<br/>占比 <b>${p.value}%</b>`,
      backgroundColor: '#fff', borderColor: '#e4e7ec', textStyle: { color: '#101828', fontSize: 12 },
    },
    legend: { type: 'scroll', orient: 'vertical', right: 4, top: 8, bottom: 8, textStyle: { color: '#475467', fontSize: 11 } },
    series: [
      {
        type: 'pie',
        radius: ['42%', '72%'],
        center: ['36%', '50%'],
        data,
        label: {
          formatter: (p: { percent: number }) => (p.percent >= 6 ? `${p.percent}%` : ''),
          color: '#475467', fontSize: 11,
        },
        labelLine: { show: false },
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
      },
    ],
  }
})

// ⚠️ 必须是 computed：原实现是普通常量，在 setup 阶段求值一次 —— 那时 detail 还是 null，
//    has_weight 恒为 undefined，列标题永远显示「只数占比」（含官方权重的指数也显示错）。
const distColumns = computed<DataTableColumns<IndustryRow>>(() => [
  {
    title: '行业',
    key: 'industry',
    ellipsis: { tooltip: true },
    // 兜底桶行显式标注「非行业」：与饼图口径一致，避免把数据缺口当成一个行业来读
    render: (r) =>
      r.is_unclassified
        ? h('span', { style: { color: '#B45309' } }, `${r.industry}（本地名册无行业数据）`)
        : r.industry,
  },
  { title: '只数', key: 'count', width: 70, align: 'right' },
  {
    title: detail.value?.constituents.has_weight ? '权重占比' : '只数占比',
    key: 'weight_pct',
    width: 100,
    align: 'right',
    render: (r) =>
      h('span', { style: { fontVariantNumeric: 'tabular-nums' } }, `${r.weight_pct.toFixed(2)}%`),
  },
])
</script>

<template>
  <NDrawer
    :show="props.show"
    :width="720"
    placement="right"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <NDrawerContent :title="`${props.indexName}（${props.indexCode}）`" closable>
      <NSpin :show="loading">
        <div v-if="errorMsg" class="err">{{ errorMsg }}</div>
        <template v-else-if="detail">
          <!-- 释义档案 -->
          <div class="profile">
            <div class="desc">{{ detail.profile.description || '暂无指数释义' }}</div>
            <div class="tags">
              <NTag v-if="detail.profile.base_date" size="small" :bordered="false" type="info">
                基日 {{ detail.profile.base_date }}
              </NTag>
              <NTag v-if="detail.profile.base_point" size="small" :bordered="false" type="info">
                基点 {{ detail.profile.base_point }}
              </NTag>
              <NTag size="small" :bordered="false">
                成分股 {{ detail.constituents.total }} 只
              </NTag>
              <NTag size="small" :bordered="false" :color="{ color: '#F0F5FB', textColor: BRAND_COLOR }">
                {{ detail.constituents.has_weight ? '含官方权重' : '无权重源（等权口径）' }}
              </NTag>
            </div>
            <div v-if="detail.derived_note" class="derived">{{ detail.derived_note }}</div>
            <!-- 口径不适用（非股票指数）：说清「为什么没有」，而不是让 UI 报「暂缺」
                 —— 「暂缺」暗示我们采漏了，「口径不适用」是概念本身没有股票成分 -->
            <div v-if="detail.cons_note" class="cons-note">{{ detail.cons_note }}</div>
          </div>

          <NTabs type="line" size="small" animated style="margin-top: 4px">
            <!-- 成分股 -->
            <NTabPane name="cons" :tab="`成分股 ${detail.constituents.total}`">
              <div v-if="!detail.constituents.total" style="padding: 30px">
                <NEmpty :description="detail.cons_note ? '该指数无股票成分（口径不适用）' : '成分数据暂缺'" />
              </div>
              <template v-else>
                <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 8px">
                  <NInput
                    v-model:value="keyword"
                    size="small"
                    clearable
                    placeholder="搜索代码 / 名称 / 行业"
                    style="width: 240px"
                  />
                  <span style="font-size: 12px; color: #98a2b3">
                    {{ filtered.length }} / {{ detail.constituents.total }} 只
                  </span>
                </div>
                <NDataTable
                  :columns="consColumns"
                  :data="filtered"
                  :bordered="false"
                  size="small"
                  :max-height="480"
                  :virtual-scroll="true"
                  :row-props="rowProps"
                />
              </template>
            </NTabPane>

            <!-- 行业分布 -->
            <NTabPane name="ind" tab="行业分布">
              <!-- 行业库覆盖不足（后端 industry_coverage < 50 时给 industry_note）——
                   此时 industry_dist 只剩「其他 100%」，画出来是假信息，改给说明。
                   典型触发场景：新纳入指数的成分采集排期未跑到，而非北交所（已于 2026-09-19 补齐） -->
              <div v-if="detail.industry_note" class="cons-note" style="margin-top: 10px">
                {{ detail.industry_note }}
              </div>
              <div v-else-if="!detail.industry_dist.length" style="padding: 30px">
                <NEmpty :description="detail.cons_note ? '该指数无行业分布口径' : '行业数据暂缺'" />
              </div>
              <template v-else>
                <VChart
                  v-if="pieOption"
                  :option="pieOption"
                  autoresize
                  style="width: 100%; height: 300px"
                />
                <NDataTable
                  :columns="distColumns"
                  :data="detail.industry_dist"
                  :bordered="false"
                  size="small"
                  :max-height="260"
                  style="margin-top: 8px"
                />
                <div v-if="!detail.constituents.has_weight" class="equal-note">
                  成分快照未含官方权重（深证系兜底源），行业占比按等权只数口径统计
                </div>
                <!-- 两个「其他」的释义：不解释的话，「其他 74 个行业」与「未分类 21 只」
                     仍容易被当成一回事（这正是改造前误读的来源） -->
                <div v-if="detail.industry_unclassified?.count" class="equal-note">
                  「未分类 {{ detail.industry_unclassified.count }} 只」= 这些成分在本地名册中查不到
                  行业分类（占 {{ detail.industry_unclassified.weight_pct.toFixed(2) }}%）；
                  「其他 N 个行业」是真实行业按权重排序后被折叠展示，两者含义不同。
                </div>
              </template>
            </NTabPane>
          </NTabs>
        </template>
        <div v-else style="padding: 40px; text-align: center; color: #888; font-size: 13px">
          加载中…
        </div>
      </NSpin>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.profile { padding: 2px 0 6px; border-bottom: 1px solid #f0f2f5; }
.desc { font-size: 13px; line-height: 1.75; color: #344054; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.derived { margin-top: 8px; font-size: 12px; color: #b45309; }
/* 口径不适用说明：中性信息色（非琥珀）—— 这不是异常，是口径本身不适用于该指数 */
.cons-note {
  margin-top: 8px; font-size: 12px; line-height: 1.6; color: #475467;
  background: #f8fafc; border-left: 3px solid #8a919c; border-radius: 0 4px 4px 0; padding: 6px 10px;
}
.err { padding: 20px; text-align: center; color: #791f1f; font-size: 13px; }
.equal-note { margin-top: 8px; font-size: 12px; color: #98a2b3; }
</style>
