<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import {
  NButton, NInput, NModal, NSelect, NSpin, NTag,
  type SelectOption,
} from 'naive-ui'
import api from '../api'

interface Props {
  show: boolean
  /** 当前在数据中心选中的表，为空时只展示一个简单的 SELECT 占位 */
  currentTable?: string
  /** 已知业务表清单（用于顶部下拉切换） */
  tables?: { name: string; comment?: string }[]
}
const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
}>()

interface ExploreResp {
  columns: { name: string; type: string }[]
  rows: Record<string, unknown>[]
  row_count: number
  elapsed_ms: number
  truncated: boolean
  limit: number
}

const DEFAULT_SQL = 'SELECT 1 AS hello'
const LIMIT_DEFAULT = 500
const LIMIT_MAX = 2000

// 顶部下拉的表名（可选）
const selectedTable = ref<string | null>(null)
const sqlText = ref(DEFAULT_SQL)
// LIMIT：用 string 存（原 NInput 不支持 type=number），转 int 时夹紧上限
const limitInputStr = ref<string>(String(LIMIT_DEFAULT))
const limitEffective = computed<number>(() => {
  const n = parseInt(limitInputStr.value, 10)
  if (!Number.isFinite(n) || n < 1) return 1
  return Math.min(LIMIT_MAX, n)
})
const running = ref(false)
const result = ref<ExploreResp | null>(null)
const errorMsg = ref('')
const errorKind = ref<'safety' | 'timeout' | 'syntax' | 'other'>('other')

const tableOptions = computed<SelectOption[]>(() => {
  const base: SelectOption[] = [{ label: '（不指定表）', value: '' }]
  if (props.tables?.length) {
    for (const t of props.tables) {
      const label = t.comment ? `${t.name}  ·  ${t.comment.slice(0, 22)}` : t.name
      base.push({ label, value: t.name })
    }
  }
  return base
})

watch(selectedTable, (v) => {
  if (v) {
    sqlText.value = `SELECT *\nFROM \`${v}\`\nORDER BY 1 DESC\nLIMIT 100`
  }
})

watch(() => props.show, async (v) => {
  if (v) {
    if (props.currentTable && !sqlText.value.trim().startsWith(props.currentTable)) {
      selectedTable.value = props.currentTable
      sqlText.value = `SELECT *\nFROM \`${props.currentTable}\`\nORDER BY 1 DESC\nLIMIT 100`
    }
    errorMsg.value = ''
    // 不再自动执行：modal 首次挂载未稳定，依赖时序易抖；让用户按 Ctrl+Enter 或点「执行」
    await nextTick()
  }
})

async function runSql() {
  errorMsg.value = ''
  errorKind.value = 'other'
  if (!sqlText.value.trim()) {
    errorMsg.value = '请输入 SQL'
    return
  }
  const lim = limitEffective.value
  running.value = true
  try {
    const resp = (await api.post(
      '/sql/explore',
      { sql: sqlText.value, limit: lim },
      { silent: true, timeout: 20000 },
    )) as ExploreResp
    result.value = resp
  } catch (e: any) {
    const text = _eText(e)
    errorMsg.value = text
    if (text.includes('SQL_SAFETY_BLOCKED')) errorKind.value = 'safety'
    else if (text.includes('SQL_TIMEOUT') || e?.response?.status === 408) errorKind.value = 'timeout'
    else if (text.includes('SQL_SYNTAX_ERROR')) errorKind.value = 'syntax'
  } finally {
    running.value = false
  }
}

function _eText(e: any): string {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d) return JSON.stringify(d)
  return e?.message || '执行失败'
}

function copySql() {
  navigator.clipboard.writeText(sqlText.value).catch(() => {})
}

function clearAll() {
  sqlText.value = DEFAULT_SQL
  result.value = null
  errorMsg.value = ''
}

function close() {
  emit('update:show', false)
}

const errorColor = computed(() => {
  if (errorKind.value === 'safety' || errorKind.value === 'timeout') return '#791F1F'
  if (errorKind.value === 'syntax') return '#B45309'
  return '#791F1F'
})

const errorTitle = computed(() => {
  if (errorKind.value === 'safety') return '安全拦截'
  if (errorKind.value === 'timeout') return '查询超时'
  if (errorKind.value === 'syntax') return '语法错误'
  return '执行错误'
})

function onKey(e: KeyboardEvent) {
  if (!props.show) return
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    runSql()
  }
}

function _eCode(msg: string): string {
  // 从「SQL_XXX: 描述」中提取 code；无前缀返回 'ERROR'
  const m = msg.match(/^([A-Z_]+):/)
  return m ? m[1] : 'ERROR'
}
</script>

<template>
  <NModal
    :show="props.show"
    preset="card"
    style="width:1100px;max-width:96vw;"
    :style="{ height: '720px' }"
    size="huge"
    title="数据探查 · 只读"
    :on-mask-click="close"
    :on-close="close"
    :bordered="false"
    role="dialog"
    aria-modal="true"
  >
    <template #header>
      <div style="display:flex;align-items:center;gap:10px;">
        <span style="font-size:16px;font-weight:600;color:#185FA5;">数据探查</span>
        <NTag size="tiny" :bordered="false" type="info" style="font-weight:500;">只读 · 15s 超时 · 强制 LIMIT</NTag>
      </div>
    </template>

    <div style="display:flex;flex-direction:column;height:100%;" @keydown="onKey">

      <!-- 风险条 -->
      <div
        style="padding:7px 12px;border-radius:6px;background:#FFF4F4;border:1px solid #F4C9C9;color:#791F1F;font-size:12px;line-height:1.55;margin-bottom:10px;"
      >
        <strong>⚠ 仅支持 SELECT / WITH / EXPLAIN / SHOW / DESCRIBE；</strong>
        写语句将被<strong>关键字黑名单拦截</strong>，会话强制 <code>READ ONLY</code>，默认自动追加 <code>LIMIT 500</code>，单次最长 15 秒。
        <kbd style="padding:0 4px;border:1px solid #791F1F;border-radius:3px;font-size:11px;background:#fff;margin-left:6px;">Ctrl+Enter</kbd> 执行
      </div>

      <!-- 工具栏：表名切换 + LIMIT -->
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
        <NSelect
          v-model:value="selectedTable"
          :options="tableOptions"
          size="small"
          filterable
          placeholder="选择表名（可选，自动生成查询模板）"
          style="width:280px;"
          clearable
        />
        <span style="font-size:12px;color:#888;">LIMIT</span>
        <NInput
          :value="limitInputStr"
          @update:value="(v: string) => (limitInputStr = v.replace(/\D+/g, '').slice(0, 4))"
          size="small"
          :input-props="{ inputmode: 'numeric', pattern: '[0-9]*', maxlength: 4 }"
          style="width:90px;"
          :title="`上限 ${LIMIT_MAX}`"
        />
        <NButton size="small" type="primary" :loading="running" @click="runSql()">执行</NButton>
        <NButton size="small" quaternary @click="clearAll()">清空</NButton>
        <NButton size="small" quaternary @click="copySql()">复制 SQL</NButton>
        <span style="margin-left:auto;font-size:11px;color:#aaa;">{{ sqlText.length }} 字符</span>
      </div>

      <!-- SQL 编辑器 -->
      <NInput
        v-model:value="sqlText"
        type="textarea"
        placeholder="SELECT * FROM stock_info WHERE list_status = 'L' LIMIT 100"
        :autosize="{ minRows: 5, maxRows: 12 }"
        style="font-family:Consolas,Menlo,monospace;font-size:13px;margin-bottom:10px;"
      />

      <!-- 状态条 -->
      <div style="display:flex;align-items:center;gap:10px;min-height:24px;margin-bottom:6px;font-size:12px;">
        <NSpin v-if="running" :size="14" />
        <span v-if="running" style="color:#185FA5;">执行中…</span>
        <span v-else-if="result" style="color:#14B143;">
          ✓ {{ result.row_count }} 行 · {{ result.elapsed_ms }} ms
          <span v-if="result.truncated" style="color:#B45309;">
            （已截断：表内可能超过 {{ result.limit }} 行；调大 LIMIT 重新跑）
          </span>
        </span>
        <span v-else style="color:#888;">点击「执行」或按 Ctrl+Enter 跑查询</span>
      </div>

      <!-- 错误条 -->
      <div
        v-if="errorMsg"
        :style="`padding:8px 12px;border-radius:6px;background:#FFF4F4;border:1px solid #F4C9C9;color:${errorColor};font-size:12px;line-height:1.5;margin-bottom:10px;font-family:Consolas,Menlo,monospace;word-break:break-all;`"
      >
        <strong>{{ errorTitle }}：</strong>
        <span
          style="display:inline-block;font-size:10px;font-weight:600;color:#fff;background:#791F1F;border-radius:3px;padding:1px 5px;margin-right:6px;font-family:Consolas,Menlo,monospace;letter-spacing:0.5px;"
        >{{ _eCode(errorMsg) }}</span>
        {{ errorMsg.replace(/^[A-Z_]+:\s*/, '') }}
      </div>

      <!-- 结果区 -->
      <div style="flex:1;min-height:0;border:1px solid #ececec;border-radius:6px;overflow:auto;background:#FAFBFC;">
        <div v-if="!result && !running" style="padding:30px;text-align:center;color:#888;font-size:13px;">
          尚无结果
        </div>
        <div v-else-if="result && result.rows.length === 0" style="padding:30px;text-align:center;color:#888;font-size:13px;">
          ✓ 查询成功，0 行结果
        </div>
        <table v-else-if="result" style="width:100%;border-collapse:collapse;font-size:12.5px;font-family:Consolas,Menlo,monospace;">
          <thead style="position:sticky;top:0;background:#fff;z-index:1;">
            <tr>
              <th
                v-for="c in result.columns"
                :key="c.name"
                :title="`类型: ${c.type}`"
                style="padding:6px 10px;border-bottom:2px solid #185FA5;text-align:left;color:#185FA5;font-weight:600;white-space:nowrap;"
              >
                {{ c.name }}
                <span style="display:block;font-size:10px;color:#aaa;font-weight:400;">{{ c.type }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in result.rows" :key="i" :style="`background:${i % 2 ? '#fff' : '#F7F9FC'};`">
              <td
                v-for="c in result.columns"
                :key="c.name"
                style="padding:5px 10px;border-bottom:1px solid #f0f0f0;color:#333;max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                :title="String(row[c.name] ?? '')"
              >
                {{ row[c.name] === null || row[c.name] === undefined ? '—' : String(row[c.name]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </NModal>
</template>
