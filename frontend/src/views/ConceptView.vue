<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import {
  NCard, NSpace, NSelect, NDataTable, NInput, NEmpty,
  type DataTableColumns,
} from 'naive-ui'
import KLineChart from '../components/KLineChart.vue'
import api from '../api'

// ---------- 概念列表 ----------
interface ConceptRow {
  index_code: string
  concept_code: string
  concept_name: string
  trade_date: string
  close: number
  change_pct: number
  change_amount: number
  amount: number
}

const concepts = ref<ConceptRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const sortField = ref('change_pct')
const sortOrder = ref('desc')
const keyword = ref('')
const loading = ref(false)

const currentConcept = ref<ConceptRow | null>(null)

const pctRender = (v: number) =>
  v == null
    ? '--'
    : h('span', { class: v > 0 ? 'c-up' : v < 0 ? 'c-down' : '' }, `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`)

const conceptColumns: DataTableColumns<ConceptRow> = [
  {
    title: '概念名称',
    key: 'concept_name',
    width: 160,
    fixed: 'left',
    render: (r) =>
      h(
        'a',
        {
          class: 'link',
          onClick: () => selectConcept(r),
        },
        r.concept_name,
      ),
  },
  { title: '指数代码', key: 'index_code', width: 90 },
  { title: '最新价', key: 'close', width: 90, render: (r) => (r.close == null ? '--' : Number(r.close).toFixed(2)) },
  { title: '涨跌幅', key: 'change_pct', width: 95, sorter: true, render: (r) => pctRender(r.change_pct) },
  { title: '涨跌额', key: 'change_amount', width: 90, render: (r) => (r.change_amount == null ? '--' : Number(r.change_amount).toFixed(2)) },
  {
    title: '成交额(亿)',
    key: 'amount',
    width: 110,
    sorter: true,
    render: (r) => (r.amount == null ? '--' : (r.amount / 1e8).toFixed(2)),
  },
]

async function loadConcepts() {
  loading.value = true
  try {
    const resp: any = await api.get('/concept/list', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        sort: sortField.value,
        order: sortOrder.value,
        keyword: keyword.value,
      },
    })
    concepts.value = resp.items || []
    total.value = resp.total || 0
    if (!currentConcept.value && concepts.value.length) selectConcept(concepts.value[0])
  } catch (e) {
    console.error('[concept/list]', e)
  } finally {
    loading.value = false
  }
}

function selectConcept(r: ConceptRow) {
  currentConcept.value = r
}

// ---------- 成分股 ----------
interface Constituent {
  stock_code: string
  stock_name: string
  reason: string
  new: number
  change_pct: number
  ytd_change_pct: number
}

const constituents = ref<Constituent[]>([])
const constLoading = ref(false)

async function loadConstituents() {
  if (!currentConcept.value) return
  constLoading.value = true
  try {
    const resp: any = await api.get('/concept/constituents', {
      params: { code: currentConcept.value.index_code, page_size: 200 },
    })
    constituents.value = resp.items || []
  } catch (e) {
    console.error('[constituents]', e)
    constituents.value = []
  } finally {
    constLoading.value = false
  }
}

const constColumns: DataTableColumns<Constituent> = [
  { title: '代码', key: 'stock_code', width: 90, fixed: 'left' },
  { title: '名称', key: 'stock_name', width: 110, fixed: 'left' },
  { title: '最新价', key: 'new', width: 90, render: (r) => (r.new == null ? '--' : Number(r.new).toFixed(2)) },
  { title: '涨跌幅', key: 'change_pct', width: 95, render: (r) => pctRender(r.change_pct) },
  { title: '年初至今', key: 'ytd_change_pct', width: 100, render: (r) => pctRender(r.ytd_change_pct) },
  { title: '入选原因', key: 'reason', render: (r) => r.reason || '--' },
]

// ---------- 搜索 ----------
const searchOptions = ref<{ label: string; value: string }[]>([])
async function searchConcept(keywordInput: string) {
  if (!keywordInput) {
    searchOptions.value = []
    return
  }
  try {
    const resp: any = await api.get('/concept/list', { params: { keyword: keywordInput, page_size: 20 } })
    searchOptions.value = (resp.items || []).map((c: any) => ({
      label: `${c.concept_name}（${c.index_code}）`,
      value: c.index_code,
    }))
  } catch (e) {
    searchOptions.value = []
  }
}
function onSearchSelect(code: string) {
  const hit = concepts.value.find((c) => c.index_code === code)
  if (hit) {
    selectConcept(hit)
  } else {
    // 从搜索结果构造
    const opt = searchOptions.value.find((o) => o.value === code)
    if (opt) {
      currentConcept.value = {
        index_code: code,
        concept_code: code,
        concept_name: opt.label.split('（')[0],
        trade_date: '',
        close: 0,
        change_pct: 0,
        change_amount: 0,
        amount: 0,
      }
    }
  }
}

// ---------- 联动 ----------
function onPageChange(p: number) {
  page.value = p
  loadConcepts()
}
function onPageSizeChange(ps: number) {
  pageSize.value = ps
  page.value = 1
  loadConcepts()
}
function onSortChange(sorter: any) {
  if (!sorter || !sorter.columnKey) return
  sortField.value = sorter.columnKey
  sortOrder.value = sorter.order === 'ascend' ? 'asc' : 'desc'
  loadConcepts()
}

onMounted(loadConcepts)
</script>

<template>
  <div>
    <!-- 顶部：概念搜索 -->
    <NCard style="margin-bottom: 12px">
      <NSpace align="center" :size="16">
        <NSelect
          :value="currentConcept?.index_code"
          :options="searchOptions"
          filterable
          clearable
          placeholder="搜索概念板块（如：光刻机）"
          style="width: 320px"
          @update:value="onSearchSelect"
          @search="searchConcept"
        />
        <span v-if="currentConcept" style="font-size: 15px; font-weight: 600; color: #333">
          当前概念：{{ currentConcept.concept_name }}（{{ currentConcept.index_code }}）
        </span>
      </NSpace>
    </NCard>

    <!-- 左列表 + 右详情 -->
    <NSpace vertical :size="12">
      <NCard title="概念板块行情" style="margin-bottom: 0">
        <template #header-extra>
          <NInput
            v-model:value="keyword"
            placeholder="搜索概念名称"
            clearable
            style="width: 180px"
            @keyup.enter="page = 1; loadConcepts()"
            @clear="page = 1; loadConcepts()"
          />
        </template>
        <NDataTable
          :columns="conceptColumns"
          :data="concepts"
          :loading="loading"
          :row-key="(r: any) => r.index_code"
          :pagination="{
            page: page,
            pageSize: pageSize,
            itemCount: total,
            pageSizes: [10, 20, 50],
            showSizePicker: true,
            onChange: onPageChange,
            onUpdatePageSize: onPageSizeChange,
          }"
          :remote="true"
          @update:sorter="onSortChange"
          size="small"
          :scroll-x="700"
          :row-class-name="(r: any) => (currentConcept && r.index_code === currentConcept.index_code ? 'row-active' : '')"
        />
      </NCard>

      <NGrid :cols="2" :x-gap="12">
        <NGi>
          <NCard title="概念 K 线" hoverable>
            <KLineChart
              v-if="currentConcept"
              :code="currentConcept.index_code"
              :name="currentConcept.concept_name"
              :is-concept="true"
              :limit="250"
            />
            <NEmpty v-else description="请选择概念板块" style="padding: 60px 0" />
          </NCard>
        </NGi>
        <NGi>
          <NCard title="成分股（点击概念列表切换）" hoverable>
            <template #header-extra>
              <span style="color: #999; font-size: 12px">
                {{ currentConcept ? `共 ${constituents.length} 只` : '' }}
              </span>
            </template>
            <NDataTable
              v-if="currentConcept"
              :columns="constColumns"
              :data="constituents"
              :loading="constLoading"
              size="small"
              :scroll-x="650"
              :max-height="460"
            />
            <NEmpty v-else description="请选择概念板块" style="padding: 60px 0" />
          </NCard>
        </NGi>
      </NGrid>
    </NSpace>
  </div>
</template>

<script lang="ts">
import { NGrid, NGi } from 'naive-ui'
export default { components: { NGrid, NGi } }
</script>

<style scoped>
.link {
  color: #1e6fff;
  cursor: pointer;
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
.c-up {
  color: #e64a4a;
}
.c-down {
  color: #17a05e;
}
:deep(.row-active td) {
  background: #e8f1ff !important;
}
</style>
