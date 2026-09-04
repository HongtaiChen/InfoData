<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import {
  NCard, NSpace, NDataTable, NInput, NSelect, NEmpty, NTag, NAlert, NSpin,
  type DataTableColumns,
} from 'naive-ui'
import api from '../api'

interface NewsRow {
  id: number
  title: string
  source: string
  published_at: string
  url: string
}

const rows = ref<NewsRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const keyword = ref('')
const source = ref('')
const loading = ref(false)
const detail = ref<any>(null)
const detailLoading = ref(false)

const sourceOptions = [
  { label: '全部来源', value: '' },
  { label: '财联社', value: 'cls' },
  { label: '东方财富', value: 'em' },
]

const timeRender = (v: string) => (v ? String(v).replace('T', ' ').slice(0, 16) : '--')

const columns: DataTableColumns<NewsRow> = [
  {
    title: '标题',
    key: 'title',
    render: (r) =>
      h(
        'a',
        {
          class: 'news-title',
          onClick: () => loadDetail(r.id),
        },
        r.title,
      ),
  },
  {
    title: '来源',
    key: 'source',
    width: 100,
    render: (r) => h(NTag, { size: 'small', type: r.source === 'cls' ? 'error' : 'info', bordered: false }, () =>
      r.source === 'cls' ? '财联社' : r.source === 'em' ? '东财' : r.source || '--',
    ),
  },
  { title: '发布时间', key: 'published_at', width: 160, render: (r) => timeRender(r.published_at) },
]

async function loadList() {
  loading.value = true
  try {
    const resp: any = await api.get('/news', {
      params: { page: page.value, page_size: pageSize.value, keyword: keyword.value, source: source.value },
    })
    rows.value = resp.items || []
    total.value = resp.total || 0
  } catch (e) {
    console.error('[news]', e)
  } finally {
    loading.value = false
  }
}

async function loadDetail(id: number) {
  detailLoading.value = true
  try {
    const resp: any = await api.get(`/news/${id}`)
    detail.value = resp.item
  } catch (e) {
    console.error('[news/detail]', e)
  } finally {
    detailLoading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  loadList()
}
function onPageSizeChange(ps: number) {
  pageSize.value = ps
  page.value = 1
  loadList()
}

onMounted(loadList)
</script>

<template>
  <div>
    <NAlert type="warning" :show-icon="false" closable style="margin-bottom: 12px">
      资讯采集任务尚未接入（news 表暂无数据），配置好财联社/东财采集后此处自动展示。
    </NAlert>

    <NGrid :cols="3" :x-gap="12">
      <!-- 资讯列表 -->
      <NGi :span="2">
        <NCard hoverable>
          <template #header>
            <NSpace align="center" justify="space-between" style="width: 100%">
              <span>资讯列表（{{ total }} 条）</span>
              <NSpace align="center">
                <NSelect
                  v-model:value="source"
                  :options="sourceOptions"
                  size="small"
                  style="width: 110px"
                  @update:value="page = 1; loadList()"
                />
                <NInput
                  v-model:value="keyword"
                  placeholder="搜索标题"
                  clearable
                  size="small"
                  style="width: 170px"
                  @keyup.enter="page = 1; loadList()"
                  @clear="page = 1; loadList()"
                />
              </NSpace>
            </NSpace>
          </template>
          <NDataTable
            :columns="columns"
            :data="rows"
            :loading="loading"
            :row-key="(r: any) => r.id"
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
            size="small"
            :scroll-x="700"
          />
        </NCard>
      </NGi>

      <!-- 详情 -->
      <NGi>
        <NCard title="资讯详情" hoverable style="height: 100%">
          <NSpin :show="detailLoading">
            <NEmpty v-if="!detail" description="点击左侧标题查看详情" style="padding: 60px 0" />
            <div v-else style="max-height: 600px; overflow-y: auto">
              <h3 style="margin: 0 0 8px; font-size: 15px; line-height: 1.6">{{ detail.title }}</h3>
              <div style="color: #999; font-size: 12px; margin-bottom: 12px">
                {{ timeRender(detail.published_at) }} ·
                {{ detail.source === 'cls' ? '财联社' : detail.source === 'em' ? '东方财富' : detail.source }}
              </div>
              <div class="news-content">{{ detail.content || '（正文待采集）' }}</div>
              <a v-if="detail.url" :href="detail.url" target="_blank" rel="noopener" class="news-origin">
                查看原文 ↗
              </a>
            </div>
          </NSpin>
        </NCard>
      </NGi>
    </NGrid>
  </div>
</template>

<script lang="ts">
import { NGrid, NGi } from 'naive-ui'
export default { components: { NGrid, NGi } }
</script>

<style scoped>
.news-title {
  color: #333;
  cursor: pointer;
  text-decoration: none;
  display: block;
  line-height: 1.5;
}
.news-title:hover {
  color: #1e6fff;
}
.news-content {
  font-size: 13px;
  color: #444;
  line-height: 1.8;
  white-space: pre-wrap;
}
.news-origin {
  display: inline-block;
  margin-top: 12px;
  color: #1e6fff;
  font-size: 13px;
}
</style>
