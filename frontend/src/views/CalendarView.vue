<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { NCard, NSpace, NTag, NButton, NAlert, NEmpty, NList, NListItem, NSpin, NDivider } from 'naive-ui'
import api from '../api'

const year = ref(2026)
const month = ref(1) // 1-12
const selectedDate = ref('')
const events = ref<any[]>([])
const tradeDays = ref<Set<string>>(new Set())
const loading = ref(false)
const dataTip = ref('')

// AI 概念分析状态
const analyzingId = ref<number | null>(null)
const aiResults = ref<Record<number, any>>({}) // event_id -> {status, concepts, hint, loading}
const aiErrors = ref<Record<number, string>>({})

const MONTHS = ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月']
const WEEK_HEADERS = ['日', '一', '二', '三', '四', '五', '六']

// ---------- 数据加载 ----------
async function loadMonth() {
  loading.value = true
  selectedDate.value = ''
  events.value = []
  tradeDays.value = new Set()
  try {
    const [evtResp, tradeResp]: any = await Promise.all([
      api.get('/calendar/events', { params: { year: year.value, month: month.value } }),
      api.get('/calendar/trade-days', { params: { year: year.value, month: month.value } }),
    ])
    events.value = evtResp.items || []
    tradeDays.value = new Set(
      (tradeResp.items || []).filter((d: any) => d.is_trading_day === 1).map((d: any) => String(d.trade_date).slice(0, 10)),
    )
    if (!events.value.length && tradeResp.items && tradeResp.items.length === 0) {
      dataTip.value = '该月暂无交易日与事件数据（数据范围以实际覆盖为准）'
    } else if (!events.value.length) {
      dataTip.value = '该月暂无财经事件'
    } else {
      dataTip.value = ''
    }
    // 默认选中第一个有事件的日期
    if (events.value.length) {
      selectedDate.value = String(events.value[0].event_date).slice(0, 10)
    }
  } catch (e) {
    console.error('[calendar]', e)
    dataTip.value = '加载失败'
  } finally {
    loading.value = false
  }
}

function gotoLatestDataMonth() {
  // 数据最新到 2026-01
  year.value = 2026
  month.value = 1
  loadMonth()
}

// ---------- 日历网格 ----------
const calendarCells = computed(() => {
  const first = new Date(year.value, month.value - 1, 1)
  const startWeekday = first.getDay() // 0=周日
  const daysInMonth = new Date(year.value, month.value, 0).getDate()
  const cells: { day: number; date: string; hasEvent: boolean; count: number; isTrading: boolean; isToday: boolean }[] = []
  for (let i = 0; i < startWeekday; i++) cells.push({ day: 0, date: '', hasEvent: false, count: 0, isTrading: true, isToday: false })
  const todayStr = new Date().toISOString().slice(0, 10)
  const eventsByDate = new Map<string, number>()
  for (const e of events.value) {
    const d = String(e.event_date).slice(0, 10)
    eventsByDate.set(d, (eventsByDate.get(d) || 0) + 1)
  }
  for (let day = 1; day <= daysInMonth; day++) {
    const date = `${year.value}-${String(month.value).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    const wd = new Date(year.value, month.value - 1, day).getDay()
    cells.push({
      day,
      date,
      hasEvent: eventsByDate.has(date),
      count: eventsByDate.get(date) || 0,
      isTrading: tradeDays.value.size > 0 ? tradeDays.value.has(date) : wd !== 0 && wd !== 6,
      isToday: date === todayStr,
    })
  }
  return cells
})

const selectedEvents = computed(() => {
  if (!selectedDate.value) return []
  return events.value.filter((e) => String(e.event_date).slice(0, 10) === selectedDate.value)
})

function prevMonth() {
  month.value -= 1
  if (month.value === 0) {
    month.value = 12
    year.value -= 1
  }
  loadMonth()
}
function nextMonth() {
  month.value += 1
  if (month.value === 13) {
    month.value = 1
    year.value += 1
  }
  loadMonth()
}

// ---------- AI 概念分析 ----------
async function runAiAnalysis(e: any) {
  const id = e.id
  if (!id) return
  analyzingId.value = id
  aiErrors.value[id] = ''
  // 先尝试拉取已有分析
  try {
    const resp: any = await api.get(`/ai/analysis/${id}`, { silent: true })
    if (resp.total > 0) {
      aiResults.value[id] = { status: 'loaded', concepts: resp.items, hint: '', loading: false }
      analyzingId.value = null
      return
    }
  } catch (err) {
    console.warn('[ai-analysis] fetch existing failed', err)
  }
  // 无已有分析 → 触发新分析
  try {
    aiResults.value[id] = { status: 'pending', concepts: [], hint: '', loading: true }
    const resp: any = await api.post(`/ai/analyze-event/${id}`, {}, { silent: true })
    aiResults.value[id] = { ...resp, loading: false }
  } catch (err: any) {
    console.error('[ai-analysis]', err)
    aiErrors.value[id] = err?.response?.data?.detail || 'AI 分析调用失败'
    aiResults.value[id] = { status: 'error', concepts: [], hint: '', loading: false }
  } finally {
    analyzingId.value = null
  }
}

function clearAiResult(id: number) {
  delete aiResults.value[id]
  delete aiErrors.value[id]
}

onMounted(() => {
  // 默认显示最新有数据的月份（2026-01）
  gotoLatestDataMonth()
})

watch([year, month], () => {
  // 切换月份时清空分析状态，避免跨月残留
  aiResults.value = {}
  aiErrors.value = {}
})
</script>

<template>
  <div>
    <NCard style="margin-bottom: 12px">
      <NSpace align="center" justify="space-between" wrap>
        <NSpace align="center" :size="12">
          <NButton size="small" @click="prevMonth">‹ 上月</NButton>
          <span style="font-size: 17px; font-weight: 700; color: #333">
            {{ year }} 年 {{ MONTHS[month - 1] }}
          </span>
          <NButton size="small" @click="nextMonth">下月 ›</NButton>
          <NButton size="tiny" tertiary type="info" @click="gotoLatestDataMonth">
            最新数据月
          </NButton>
        </NSpace>
        <span style="color: #999; font-size: 12px">
          财经事件日历 · 点击日期查看当日事件
        </span>
      </NSpace>
    </NCard>

    <NAlert v-if="dataTip" type="info" :show-icon="false" closable style="margin-bottom: 12px">
      {{ dataTip }}
    </NAlert>

    <NGrid :cols="3" :x-gap="12">
      <!-- 日历表格 -->
      <NGi :span="2">
        <NCard :loading="loading" hoverable>
          <div class="cal-table">
            <div class="cal-header">
              <div v-for="w in WEEK_HEADERS" :key="w" class="cal-header-cell" :class="{ 'cal-weekend': w === '日' || w === '六' }">
                {{ w }}
              </div>
            </div>
            <div class="cal-body">
              <div
                v-for="(cell, i) in calendarCells"
                :key="i"
                class="cal-cell"
                :class="{
                  'cal-empty': !cell.day,
                  'cal-selected': cell.date === selectedDate,
                  'cal-today': cell.isToday,
                  'cal-rest': !cell.isTrading,
                }"
                @click="cell.day && (selectedDate = cell.date)"
              >
                <template v-if="cell.day">
                  <div class="cal-day-row">
                    <span class="cal-day-num">{{ cell.day }}</span>
                    <span v-if="!cell.isTrading" class="cal-rest-tag">休</span>
                  </div>
                  <div v-if="cell.hasEvent" class="cal-event-badge">
                    <span class="cal-dot" /> {{ cell.count }} 件
                  </div>
                </template>
              </div>
            </div>
          </div>
        </NCard>
      </NGi>

      <!-- 当日事件详情 -->
      <NGi>
        <NCard title="当日事件" hoverable style="height: 100%">
          <template #header-extra>
            <span v-if="selectedDate" style="color: #1e6fff; font-weight: 600">{{ selectedDate }}</span>
          </template>
          <NEmpty v-if="!selectedDate || !selectedEvents.length" description="该日无事件" style="padding: 40px 0" />
          <NList v-else size="small" :show-divider="true" style="max-height: 620px; overflow-y: auto">
            <NListItem v-for="(e, idx) in selectedEvents" :key="idx" style="padding: 8px 4px">
              <div class="evt-title">{{ e.title }}</div>
              <div class="evt-content">{{ e.content }}</div>
              <div style="margin-top: 6px; display: flex; align-items: center; justify-content: space-between">
                <NSpace :size="6">
                  <NTag size="tiny" type="info" :bordered="false">{{ e.data_source || '未知' }}</NTag>
                  <NTag
                    v-if="aiResults[e.id]?.status === 'ai'"
                    size="tiny"
                    :bordered="false"
                    :color="{ color: '#FBF6E9', borderColor: '#EEDC9E', textColor: '#7A6410' }"
                  >AI 已分析</NTag>
                  <NTag v-else-if="aiResults[e.id]?.status === 'placeholder'" size="tiny" type="warning" :bordered="false">占位结果</NTag>
                </NSpace>
                <NSpace :size="8">
                  <NButton
                    v-if="aiResults[e.id]"
                    size="tiny"
                    tertiary
                    type="error"
                    @click="clearAiResult(e.id)"
                  >
                    清除
                  </NButton>
                  <NButton
                    size="tiny"
                    type="primary"
                    :loading="analyzingId === e.id"
                    :disabled="analyzingId !== null && analyzingId !== e.id"
                    @click="runAiAnalysis(e)"
                  >
                    {{ aiResults[e.id] ? '重新分析' : 'AI 概念分析' }}
                  </NButton>
                </NSpace>
              </div>

              <!-- AI 分析结果 -->
              <div v-if="aiResults[e.id]" class="ai-result">
                <template v-if="aiResults[e.id].loading">
                  <div class="ai-loading"><NSpin size="small" /> 正在分析事件概念关联…</div>
                </template>
                <template v-else>
                  <NDivider style="margin: 8px 0" />
                  <div v-if="aiErrors[e.id]" class="ai-error">
                    <NTag size="tiny" type="error" :bordered="false">失败</NTag>
                    <span>{{ aiErrors[e.id] }}</span>
                  </div>
                  <div v-else>
                    <div v-if="aiResults[e.id].hint" class="ai-hint">{{ aiResults[e.id].hint }}</div>
                    <div v-for="(c, ci) in aiResults[e.id].concepts || []" :key="ci" class="ai-concept">
                      <div class="ai-concept-head">
                        <span class="ai-concept-name">{{ c.concept_name }}</span>
                        <NTag
                          size="tiny"
                          :bordered="false"
                          :type="c.relation_type === '利好' ? 'success' : c.relation_type === '利空' ? 'error' : 'default'"
                        >
                          {{ c.relation_type }}
                        </NTag>
                        <span class="ai-degree">{{ c.relation_degree }}/10</span>
                      </div>
                      <div class="ai-concept-analysis">{{ c.analysis }}</div>
                    </div>
                    <div v-if="!aiResults[e.id].concepts?.length && !aiResults[e.id].hint" class="ai-hint">
                      该事件暂无概念分析结果
                    </div>
                  </div>
                </template>
              </div>
            </NListItem>
          </NList>
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
.cal-table {
  user-select: none;
}
.cal-header {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  background: #f7f8fa;
  border-radius: 4px 4px 0 0;
}
.cal-header-cell {
  padding: 8px 0;
  text-align: center;
  font-size: 13px;
  color: #333;
  font-weight: 600;
  border-bottom: 1px solid #eceff3;
}
.cal-weekend {
  color: #b45309;
}
.cal-body {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
}
.cal-cell {
  min-height: 72px;
  padding: 6px 8px;
  border: 1px solid #f0f1f4;
  cursor: pointer;
  transition: background 0.15s;
}
.cal-cell:hover {
  background: #f5f8ff;
}
.cal-empty {
  cursor: default;
}
.cal-empty:hover {
  background: none;
}
.cal-selected {
  background: #e8f1ff !important;
  border-color: #1e6fff;
}
.cal-today .cal-day-num {
  color: #1e6fff;
  font-weight: 700;
}
.cal-rest .cal-day-num {
  color: #aaa;
}
.cal-day-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.cal-day-num {
  font-size: 14px;
}
.cal-rest-tag {
  font-size: 10px;
  color: #fff;
  background: #bbb;
  border-radius: 2px;
  padding: 0 3px;
}
.cal-event-badge {
  margin-top: 6px;
  font-size: 11px;
  color: #1e6fff;
  background: #e8f1ff;
  border-radius: 3px;
  padding: 2px 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.cal-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #1e6fff;
  display: inline-block;
}
.evt-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  line-height: 1.5;
}
.evt-content {
  font-size: 12px;
  color: #666;
  margin-top: 3px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ai-result {
  margin-top: 4px;
}
.ai-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #888;
  padding: 8px 0;
}
.ai-hint {
  font-size: 12px;
  color: #b8860b;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 6px;
  line-height: 1.5;
}
.ai-error {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #791f1f;
}
.ai-concept {
  background: #f7f9fc;
  border: 1px solid #eef1f6;
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
}
.ai-concept-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.ai-concept-name {
  font-size: 13px;
  font-weight: 700;
  color: #1e6fff;
}
.ai-degree {
  font-size: 12px;
  color: #666;
  font-weight: 600;
}
.ai-concept-analysis {
  font-size: 12px;
  color: #555;
  line-height: 1.6;
}
</style>
