<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, h, watch } from 'vue'
import {
  NCard, NDataTable, NTag, NSelect, NStatistic, NGrid, NGi, NSpace, NButton,
  NSwitch, NModal, NForm, NFormItem, NInput, NPopconfirm, NDescriptions,
  NDescriptionsItem, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { CronExpressionParser } from 'cron-parser'
import dayjs from 'dayjs'
import api from '../api'

const message = useMessage()

// ---------- 统计概况 ----------
const stats = ref<any[]>([])
async function loadStats() {
  try {
    const resp: any = await api.get('/jobs/stats')
    stats.value = resp.items || []
  } catch (e) {
    console.error('[stats]', e)
  }
}

// ---------- 任务配置（含调度状态） ----------
const tasks = ref<any[]>([])
const schedulerRunning = ref(false)
const scheduledCount = ref(0)

async function loadTasks() {
  try {
    const resp: any = await api.get('/jobs/tasks')
    tasks.value = resp.items || []
    schedulerRunning.value = !!resp.scheduler_running
    scheduledCount.value = resp.scheduled_count || 0
  } catch (e) {
    console.error('[tasks]', e)
  }
}

/** 切换启用开关（热同步调度器） */
async function toggleEnabled(r: any, val: boolean) {
  if (!r.implemented) return
  try {
    await api.put(`/jobs/tasks/${r.task_name}`, { enabled: val })
    message.success(val ? `已启用 ${r.task_name}（自动调度）` : `已停用 ${r.task_name}（仅手动触发）`)
    loadTasks()
  } catch (e: any) {
    message.error(e?.detail || `更新失败：${r.task_name}`)
  }
}

/** 立即执行 */
async function triggerNow(r: any) {
  try {
    const resp: any = await api.post(`/jobs/tasks/${r.task_name}/trigger`)
    message.success(resp.message || '已提交执行，请到下方运行记录查看进度')
    setTimeout(() => loadTasks(), 1500)
  } catch (e: any) {
    message.error(e?.detail || '触发失败，请稍后再试')
  }
}

// ---------- 编辑弹窗 ----------
const showEdit = ref(false)
const editSaving = ref(false)
const editingTask = ref<any>(null)
const editCron = ref('')
const editEnabled = ref(true)

// cron 实时预览（接下来 5 次运行时间）
const weekCn = ['日', '一', '二', '三', '四', '五', '六']
type CronPreviewTime = { date: string; week: string; time: string }
const cronPreview = ref<{ mode: 'idle' | 'manual' | 'invalid' | 'ok'; times: CronPreviewTime[]; error?: string }>({
  mode: 'idle',
  times: [],
})
let previewTimer: any = null

function recomputePreview(expr: string) {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(() => {
    const v = (expr || '').trim()
    if (!v || v === '手动') {
      cronPreview.value = { mode: 'manual', times: [] }
      return
    }
    try {
      const interval = CronExpressionParser.parse(v)
      const times: CronPreviewTime[] = []
      for (let i = 0; i < 5; i++) {
        const d = interval.next().toDate()
        const dt = dayjs(d)
        times.push({
          date: dt.format('YYYY-MM-DD'),
          week: weekCn[dt.day()],
          time: dt.format('HH:mm'),
        })
      }
      cronPreview.value = { mode: 'ok', times }
    } catch (e: any) {
      cronPreview.value = {
        mode: 'invalid',
        times: [],
        error: `cron 格式无效：${e?.message || e}`,
      }
    }
  }, 300)
}

watch(editCron, (v) => recomputePreview(v))

function openEdit(r: any) {
  editingTask.value = r
  editCron.value = r.cron && r.cron !== '手动' ? r.cron : ''
  editEnabled.value = !!r.enabled
  showEdit.value = true
  recomputePreview(editCron.value)
}

async function saveEdit() {
  if (!editingTask.value) return
  editSaving.value = true
  try {
    await api.put(`/jobs/tasks/${editingTask.value.task_name}`, {
      enabled: editEnabled.value,
      cron: editCron.value.trim() || '手动',
    })
    message.success('已保存并热生效（调度器已同步）')
    showEdit.value = false
    loadTasks()
  } catch (e: any) {
    message.error(e?.detail || '保存失败')
  } finally {
    editSaving.value = false
  }
}

const taskColumns: DataTableColumns<any> = [
  { title: '任务名', key: 'task_name', width: 150 },
  {
    title: '实现',
    key: 'implemented',
    width: 70,
    render: (r) => h(NTag, { size: 'small', type: r.implemented ? 'success' : 'default', bordered: false }, () => (r.implemented ? '已实现' : '未实现')),
  },
  {
    title: '启用',
    key: 'enabled',
    width: 70,
    render: (r) =>
      h(NSwitch, {
        size: 'small',
        value: !!r.enabled,
        disabled: !r.implemented,
        'onUpdate:value': (v: boolean) => toggleEnabled(r, v),
      }),
  },
  {
    title: '调度 (cron)',
    key: 'cron',
    width: 130,
    render: (r) =>
      r.cron === '手动'
        ? h(NTag, { size: 'small', type: 'warning', bordered: false }, () => '手动')
        : r.cron,
  },
  {
    title: '下次运行',
    key: 'next_run',
    width: 150,
    render: (r) => (r.next_run ? r.next_run : '--'),
  },
  {
    title: '状态',
    key: 'running',
    width: 70,
    render: (r) =>
      r.running
        ? h(NTag, { size: 'small', type: 'info', bordered: false }, () => '运行中')
        : '--',
  },
  { title: '参数', key: 'params', ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'actions',
    width: 170,
    render: (r) =>
      h(NSpace, { size: 6 }, () => [
        h(
          NPopconfirm,
          { onPositiveClick: () => triggerNow(r) },
          {
            trigger: () =>
              h(
                NButton,
                {
                  size: 'tiny',
                  type: 'primary',
                  secondary: true,
                  disabled: !r.implemented || r.running,
                },
                { default: () => '立即执行' }
              ),
            default: () => `确认立即执行 ${r.task_name}？`,
          }
        ),
        h(
          NButton,
          {
            size: 'tiny',
            secondary: true,
            disabled: !r.implemented,
            onClick: () => openEdit(r),
          },
          { default: () => '编辑' }
        ),
      ]),
  },
]

// ---------- 运行记录 ----------
const runs = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const taskFilter = ref('')
const statusFilter = ref('')
const runsLoading = ref(false)

const statusTag = (s: string) => {
  const map: Record<string, { type: 'info' | 'error' | 'default' | 'warning'; text: string }> = {
    success: { type: 'info', text: '成功' },
    failed: { type: 'error', text: '失败' },
    running: { type: 'info', text: '运行中' },
    partial: { type: 'warning', text: '部分成功' },
  }
  const m = map[s] || { type: 'default' as const, text: s || '--' }
  return h(NTag, { size: 'small', type: m.type, bordered: false }, () => m.text)
}

const runColumns: DataTableColumns<any> = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '任务名', key: 'task_name', width: 160 },
  { title: '状态', key: 'status', width: 90, render: (r) => statusTag(r.status) },
  { title: '写入行数', key: 'records_written', width: 100 },
  {
    title: '开始时间',
    key: 'started_at',
    width: 160,
    render: (r) => (r.started_at ? String(r.started_at).replace('T', ' ').slice(0, 16) : '--'),
  },
  {
    title: '结束时间',
    key: 'finished_at',
    width: 160,
    render: (r) => (r.finished_at ? String(r.finished_at).replace('T', ' ').slice(0, 16) : '--'),
  },
  { title: '错误信息', key: 'error_message', ellipsis: { tooltip: true } },
]

async function loadRuns(silent = false) {
  if (!silent) runsLoading.value = true
  try {
    const resp: any = await api.get('/jobs/runs', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        task_name: taskFilter.value,
        status: statusFilter.value,
      },
    })
    runs.value = resp.items || []
    total.value = resp.total || 0
  } catch (e) {
    console.error('[runs]', e)
  } finally {
    if (!silent) runsLoading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  loadRuns()
}
function onPageSizeChange(ps: number) {
  pageSize.value = ps
  page.value = 1
  loadRuns()
}

const taskOptions = computed(() => [
  { label: '全部任务', value: '' },
  ...tasks.value.map((t) => ({ label: t.task_name, value: t.task_name })),
])
const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '运行中', value: 'running' },
]

// ---------- 统计卡片 ----------
const totalOk = computed(() => stats.value.reduce((s, x) => s + (Number(x.ok_cnt) || 0), 0))
const totalFail = computed(() => stats.value.reduce((s, x) => s + (Number(x.fail_cnt) || 0), 0))
const totalRunning = computed(() => stats.value.reduce((s, x) => s + (Number(x.running_cnt) || 0), 0))
const totalTasks = computed(() => tasks.value.length)

// ---------- 自动轮询（10s） ----------
const POLL_INTERVAL = 10000
let pollTimer: number | null = null
const lastUpdate = ref('')

function refreshAll(silent = true) {
  Promise.all([loadStats(), loadTasks(), loadRuns(silent)]).then(() => {
    lastUpdate.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  })
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(() => refreshAll(true), POLL_INTERVAL)
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function onVisibilityChange() {
  if (document.hidden) {
    stopPolling()
  } else {
    refreshAll(true)
    startPolling()
  }
}

onMounted(() => {
  refreshAll(false)
  startPolling()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<style scoped>
/* ------------------ cron 实时预览区样式 ------------------ */
.cron-form-stack {
  display: block;
  width: 100%;
}

.cron-form-stack > .n-input {
  width: 100%;
}

.cron-preview-taskname {
  line-height: 32px;
  font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace;
  font-weight: 600;
  color: #1f2937;
}

.cron-preview-box {
  margin-top: 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  width: 100%;
  box-sizing: border-box;
}

.cron-preview-ok {
  background: #EFF6FF;
  border: 1px solid #B5D4F4;
  padding: 14px 16px;
  color: #0C447C;
  box-shadow: 0 1px 2px rgba(24, 95, 165, 0.06);
}

.cron-preview-err {
  background: #fef7ec;
  border: 1px solid #f5dfb5;
  padding: 10px 14px;
  color: #a05e03;
  display: flex;
  align-items: center;
  gap: 8px;
}

.cron-preview-icon {
  font-size: 16px;
  margin-right: 4px;
  vertical-align: middle;
}

.cron-preview-head {
  display: flex;
  align-items: center;
  margin-bottom: 10px;
  font-weight: 600;
}

.cron-preview-title {
  flex: 1;
}

.cron-preview-tag {
  margin-left: auto;
  border: none !important;
  background: rgba(24, 95, 165, 0.12) !important;
  color: #185FA5 !important;
}

.cron-preview-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.cron-preview-row {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) 64px 80px;
  align-items: center;
  column-gap: 12px;
  padding: 8px 14px;
  background: rgba(255, 255, 255, 0.6);
  border-radius: 6px;
  font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace;
  transition: background 0.15s;
}

.cron-preview-row:hover {
  background: rgba(255, 255, 255, 0.95);
}

.cron-preview-idx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #185FA5;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}

.cron-preview-date {
  color: #1f2937;
  font-weight: 600;
  font-size: 13px;
  min-width: 0;
}

.cron-preview-week {
  color: #4a6f96;
  font-size: 12px;
  text-align: center;
  background: rgba(24, 95, 165, 0.09);
  padding: 2px 6px;
  border-radius: 4px;
  white-space: nowrap;
}

.cron-preview-time {
  color: #185FA5;
  font-weight: 600;
  font-size: 15px;
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.cron-preview-hint {
  margin-top: 10px;
  padding: 8px 12px;
  background: #f6f8fa;
  border-radius: 6px;
  font-size: 12px;
  color: #6b7280;
}

.cron-preview-fmt {
  background: #f6f8fa;
  padding: 6px 0;
  border-radius: 6px;
  border-left: 3px solid #d1d5db;
  width: 100%;
  box-sizing: border-box;
  font-size: 13px;
}

.cron-preview-fmt :deep(.n-descriptions .n-descriptions-item) {
  padding: 8px 16px;
}

.cron-preview-fmt :deep(.n-descriptions .n-descriptions-item:not(:last-child)) {
  border-bottom: 1px dashed rgba(0, 0, 0, 0.06);
}

.cron-preview-fmt code {
  background: #e5e7eb;
  padding: 1px 6px;
  border-radius: 3px;
  font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  color: #1f2937;
  margin: 0 2px;
}
</style><template>
  <div>
    <!-- 统计卡片 -->
    <NGrid :cols="4" :x-gap="12" style="margin-bottom: 12px">
      <NGi>
        <NCard size="small" hoverable>
          <NStatistic label="任务配置数" :value="totalTasks">
            <template #suffix>个</template>
          </NStatistic>
        </NCard>
      </NGi>
      <NGi>
        <NCard size="small" hoverable>
          <NStatistic label="累计成功" :value="totalOk" style="--n-value-text-color: #185FA5">
            <template #suffix>次</template>
          </NStatistic>
        </NCard>
      </NGi>
      <NGi>
        <NCard size="small" hoverable>
          <NStatistic label="累计失败" :value="totalFail" style="--n-value-text-color: #791F1F">
            <template #suffix>次</template>
          </NStatistic>
        </NCard>
      </NGi>
      <NGi>
        <NCard size="small" hoverable>
          <NStatistic label="运行中" :value="totalRunning" style="--n-value-text-color: #185FA5">
            <template #suffix>个</template>
          </NStatistic>
        </NCard>
      </NGi>
    </NGrid>

    <!-- 任务配置（可管理调度） -->
    <NCard hoverable style="margin-bottom: 12px">
      <template #header>
        <NSpace align="center" justify="space-between" style="width: 100%">
          <NSpace align="center">
            <span>任务配置（task_config）</span>
            <NTag v-if="schedulerRunning" size="small" type="info" :bordered="false">
              🕒 调度器运行中 · {{ scheduledCount }} 个定时任务
            </NTag>
            <NTag v-else size="small" type="error" :bordered="false">⚠️ 调度器未运行</NTag>
          </NSpace>
          <NTag size="small" :bordered="false" style="color: #999">
            5 段 cron（分 时 日 月 周）· 点「编辑」修改调度，保存后立即生效
          </NTag>
        </NSpace>
      </template>
      <NDataTable
        :columns="taskColumns"
        :data="tasks"
        size="small"
        :scroll-x="1200"
        :row-key="(r: any) => r.task_name"
      />
    </NCard>

    <!-- 编辑调度弹窗 -->
    <NModal v-model:show="showEdit" preset="card" title="编辑任务调度" style="width: 680px">
      <NForm label-placement="top" v-if="editingTask">
        <NFormItem label="任务名">
          <span class="cron-preview-taskname">{{ editingTask.task_name }}</span>
        </NFormItem>
        <NFormItem label="启用">
          <NSwitch v-model:value="editEnabled" />
          <span v-if="!editEnabled" style="margin-left: 10px; color: #999; font-size: 12px">停用后仅保留配置，不参与自动调度</span>
        </NFormItem>
        <NFormItem label="Cron 表达式">
          <div class="cron-form-stack">
            <NInput
              v-model:value="editCron"
              placeholder="如 */30 * * * *（每 30 分钟）或 0 19 * * 1-5（工作日 19:00）"
              size="large"
              clearable
            />
            <!-- 预览：有效 -->
            <div v-if="cronPreview.mode === 'ok'" class="cron-preview-box cron-preview-ok">
              <div class="cron-preview-head">
                <span class="cron-preview-icon">📅</span>
                <span class="cron-preview-title">接下来 5 次实际运行</span>
                <NTag size="small" :bordered="false" type="info" class="cron-preview-tag">实时预览</NTag>
              </div>
              <div class="cron-preview-list">
                <div v-for="(t, i) in cronPreview.times" :key="i" class="cron-preview-row">
                  <span class="cron-preview-idx">{{ i + 1 }}</span>
                  <span class="cron-preview-date">{{ t.date }}</span>
                  <span class="cron-preview-week">周{{ t.week }}</span>
                  <span class="cron-preview-time">{{ t.time }}</span>
                </div>
              </div>
            </div>
            <!-- 预览：格式错误 -->
            <div v-else-if="cronPreview.mode === 'invalid'" class="cron-preview-box cron-preview-err">
              <span class="cron-preview-icon">⚠️</span>
              <span>{{ cronPreview.error }}</span>
            </div>
            <!-- 预览：手动 -->
            <div v-else-if="cronPreview.mode === 'manual'" class="cron-preview-hint">
              <span class="cron-preview-icon">💡</span>
              留空或填「手动」= 仅手动触发，不参与自动调度
            </div>
          </div>
        </NFormItem>
        <NFormItem label="格式说明">
          <div class="cron-preview-fmt">
            <NDescriptions
              :column="1"
              size="small"
              label-placement="left"
              :label-style="{ width: '70px', color: '#6b7280', fontWeight: 500 }"
              :content-style="{ color: '#1f2937' }"
            >
              <NDescriptionsItem label="5 段">
                <code>分 时 日 月 周</code>
              </NDescriptionsItem>
              <NDescriptionsItem label="取值">
                分 0-59 · 时 0-23 · 日 1-31 · 月 1-12 · 周 0-6（0=周日，1-5=周一至周五）
              </NDescriptionsItem>
              <NDescriptionsItem label="常用">
                <code>*</code> 任意 · <code>*/n</code> 每 n · <code>a-b</code> 区间 · <code>a,b,c</code> 离散
              </NDescriptionsItem>
              <NDescriptionsItem label="示例">
                <code>0 19 * * 1-5</code> = 工作日 19:00 整点
              </NDescriptionsItem>
              <NDescriptionsItem label="手动">
                留空或填「手动」= 仅手动触发，不自动调度
              </NDescriptionsItem>
            </NDescriptions>
          </div>
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEdit = false">取消</NButton>
          <NButton type="primary" :loading="editSaving" @click="saveEdit">保存并生效</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 运行记录 -->
    <NCard hoverable>
      <template #header>
        <NSpace align="center" justify="space-between" style="width: 100%">
          <NSpace align="center" :size="10">
            <span>运行记录（{{ total }} 条）</span>
            <NTag size="tiny" type="info" :bordered="false" style="margin-left: 4px">每 10 秒自动刷新</NTag>
            <span v-if="lastUpdate" style="color: #999; font-size: 12px">更新于 {{ lastUpdate }}</span>
          </NSpace>
          <NSpace align="center">
            <NSelect v-model:value="taskFilter" :options="taskOptions" size="small" style="width: 200px" @update:value="page = 1; loadRuns()" />
            <NSelect v-model:value="statusFilter" :options="statusOptions" size="small" style="width: 120px" @update:value="page = 1; loadRuns()" />
            <NButton size="small" secondary type="primary" @click="refreshAll(false)">刷新</NButton>
          </NSpace>
        </NSpace>
      </template>
      <NDataTable
        :columns="runColumns"
        :data="runs"
        :loading="runsLoading"
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
        :scroll-x="1000"
      />
    </NCard>
  </div>
</template>
