<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import { NCard, NSpace, NSelect, NDataTable, NInput, type DataTableColumns } from 'naive-ui'
import KLineChart from '../components/KLineChart.vue'
import api from '../api'

// ---------- 股票搜索 ----------
const stockOptions = ref<{ label: string; value: string }[]>([])
const currentCode = ref('000001')
const currentName = ref('平安银行')

async function searchStocks(keyword: string) {
  if (!keyword) {
    stockOptions.value = []
    return
  }
  try {
    const resp: any = await api.get('/market/stock-search', { params: { keyword, limit: 20 } })
    stockOptions.value = (resp.items || []).map((s: any) => ({
      label: `${s.stock_code} ${s.short_name}`,
      value: s.stock_code,
    }))
  } catch (e) {
    console.error('[search]', e)
    stockOptions.value = []
  }
}

function onSelect(code: string) {
  const hit = stockOptions.value.find((o) => o.value === code)
  currentCode.value = code
  currentName.value = hit ? hit.label.replace(`${code} `, '') : code
}

// ---------- 行情表格 ----------
interface Row {
  stock_code: string
  stock_name: string
  new: number
  change_pct: number
  change_amount: number
  amount: number
  turnover_ratio: number
  dynamic_pe: number
  ytd_change_pct: number
}

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const sortField = ref('change_pct')
const sortOrder = ref('desc')
const keyword = ref('')
const loading = ref(false)

const pctRender = (v: number) =>
  v == null
    ? '--'
    : h(
        'span',
        { class: v > 0 ? 'c-up' : v < 0 ? 'c-down' : '' },
        `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%`,
      )
const numRender = (v: number, digits = 2) => (v == null ? '--' : Number(v).toFixed(digits))

const columns: DataTableColumns<Row> = [
  { title: '代码', key: 'stock_code', width: 90, fixed: 'left' },
  {
    title: '名称',
    key: 'stock_name',
    width: 110,
    fixed: 'left',
    render: (r) =>
      h(
        'a',
        {
          class: 'stock-link',
          onClick: () => {
            currentCode.value = r.stock_code
            currentName.value = r.stock_name
          },
        },
        r.stock_name,
      ),
  },
  { title: '最新价', key: 'new', width: 90, render: (r) => numRender(r.new) },
  { title: '涨跌幅', key: 'change_pct', width: 95, sorter: true, render: (r) => pctRender(r.change_pct) },
  { title: '涨跌额', key: 'change_amount', width: 90, render: (r) => numRender(r.change_amount) },
  { title: '今开', key: 'open', width: 85, render: (r) => numRender((r as any).open) },
  { title: '最高', key: 'high', width: 85, render: (r) => numRender((r as any).high) },
  { title: '最低', key: 'low', width: 85, render: (r) => numRender((r as any).low) },
  { title: '成交量(手)', key: 'volume', width: 100, render: (r) => formatVol((r as any).volume) },
  { title: '成交额(亿)', key: 'amount', width: 100, sorter: true, render: (r) => numRender(r.amount / 1e8, 2) },
  { title: '换手率', key: 'turnover_ratio', width: 90, render: (r) => `${numRender(r.turnover_ratio, 2)}%` },
  { title: '市盈率', key: 'dynamic_pe', width: 90, render: (r) => numRender(r.dynamic_pe, 2) },
  { title: '市净率', key: 'pb', width: 90, render: (r) => numRender((r as any).pb, 2) },
  { title: '年初至今', key: 'ytd_change_pct', width: 100, sorter: true, render: (r) => pctRender(r.ytd_change_pct) },
]

function formatVol(v?: number): string {
  if (!v && v !== 0) return '--'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return String(v)
}

async function loadRows() {
  loading.value = true
  try {
    const resp: any = await api.get('/market/current', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        sort: sortField.value,
        order: sortOrder.value,
        keyword: keyword.value,
      },
    })
    rows.value = resp.items || []
    total.value = resp.total || 0
  } catch (e) {
    console.error('[current]', e)
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  loadRows()
}
function onPageSizeChange(ps: number) {
  pageSize.value = ps
  page.value = 1
  loadRows()
}
function onSortChange(sorter: any) {
  if (!sorter || !sorter.columnKey) return
  sortField.value = sorter.columnKey
  sortOrder.value = sorter.order === 'ascend' ? 'asc' : 'desc'
  loadRows()
}

onMounted(loadRows)
</script>

<template>
  <div>
    <!-- 顶部：搜索 + 当前标的 -->
    <NCard style="margin-bottom: 12px">
      <NSpace align="center" :size="16" wrap>
        <NSelect
          v-model:value="currentCode"
          :options="stockOptions"
          filterable
          clearable
          placeholder="输入代码/名称搜索股票"
          style="width: 320px"
          :on-update:value="onSelect"
          @search="searchStocks"
        />
        <span style="font-size: 15px; font-weight: 600; color: #333">
          当前标的：{{ currentName }}（{{ currentCode }}）
        </span>
      </NSpace>
    </NCard>

    <!-- K 线 -->
    <NCard style="margin-bottom: 12px">
      <KLineChart :code="currentCode" :name="currentName" :limit="250" />
    </NCard>

    <!-- 最新行情表格 -->
    <NCard>
      <template #header>
        <NSpace align="center" justify="space-between" style="width: 100%">
          <span>沪深 A 股最新行情</span>
          <NInput
            v-model:value="keyword"
            placeholder="搜索代码/名称"
            clearable
            style="width: 200px"
            @keyup.enter="page = 1; loadRows()"
            @clear="page = 1; loadRows()"
          />
        </NSpace>
      </template>
      <NDataTable
        :columns="columns"
        :data="rows"
        :loading="loading"
        :row-key="(r: any) => r.stock_code"
        :pagination="{
          page: page,
          pageSize: pageSize,
          itemCount: total,
          pageSizes: [20, 50, 100],
          showSizePicker: true,
          onChange: onPageChange,
          onUpdatePageSize: onPageSizeChange,
        }"
        :remote="true"
        @update:sorter="onSortChange"
        size="small"
        :scroll-x="1200"
      />
    </NCard>
  </div>
</template>

<style scoped>
.stock-link {
  color: #1e6fff;
  cursor: pointer;
  text-decoration: none;
}
.stock-link:hover {
  text-decoration: underline;
}
.c-up {
  color: #EF232A;
}
.c-down {
  color: #14B143;
}
</style>
