<script setup lang="ts">
import { computed, h, ref, watch, nextTick } from 'vue'
import {
  NButton, NDataTable, NInput, NModal, NRadio, NRadioGroup, NSelect, NSpin, NTag,
  type SelectOption,
} from 'naive-ui'
import api from '../api'

interface Props {
  show: boolean
  /** 当前在数据中心选中的表 */
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

const selectedTable = ref<string | null>(null)
const sqlText = ref(DEFAULT_SQL)
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

/** 紧凑数字视图（默认 ON）：15000 → "1.50 万"；OFF 时显示原值 */
const compactNumbers = ref(true)

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

const NUM_TYPES = new Set([
  'TINYINT', 'SMALLINT', 'INT', 'INT24', 'BIGINT',
  'FLOAT', 'DOUBLE', 'DECIMAL', 'NEWDECIMAL',
])
function isNumericType(t: string): boolean {
  return NUM_TYPES.has(t.toUpperCase())
}

watch(selectedTable, (v) => {
  if (v) sqlText.value = `SELECT *\nFROM \`${v}\`\nORDER BY 1 DESC\nLIMIT 100`
})

watch(() => props.show, async (v) => {
  if (v) {
    if (props.currentTable && !sqlText.value.trim().startsWith(props.currentTable)) {
      selectedTable.value = props.currentTable
      sqlText.value = `SELECT *\nFROM \`${props.currentTable}\`\nORDER BY 1 DESC\nLIMIT 100`
    }
    errorMsg.value = ''
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

function _eCode(msg: string): string {
  const m = msg.match(/^([A-Z_]+):/)
  return m ? m[1] : 'ERROR'
}

/**
 * 单元格值渲染：null → "—" 浅灰；ISO datetime → "MM-DD HH:MM"；
 * 数字列按紧凑或原始两种视图。
 */
function fmtText(v: unknown, colType: string): string {
  if (v === null || v === undefined || v === '') return '—'
  const s = String(v)
  const up = colType.toUpperCase()
  const dtMatch = s.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  if (dtMatch && (up.includes('DATETIME') || up.includes('TIMESTAMP') || up === 'DATE')) {
    return `${dtMatch[2]}-${dtMatch[3]} ${dtMatch[4]}:${dtMatch[5]}`
  }
  if (compactNumbers.value && isNumericType(colType)) {
    const m = s.match(/^-?\d+(\.\d+)?$/)
    if (m) {
      const n = Number(s)
      if (Number.isFinite(n)) return _compact(n)
    }
  }
  return s
}

function fmtEmpty(v: unknown): boolean {
  return v === null || v === undefined || v === ''
}

function _compact(n: number): string {
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(2)} 万`
  if (Number.isInteger(n)) return `${sign}${abs.toString()}`
  if (abs < 1) return `${sign}${abs.toFixed(4)}`
  return `${sign}${abs.toFixed(2)}`
}

/** NDataTable 列定义（compactNumbers 切换时重渲染触发） */
const tableColumns = computed(() => {
  if (!result.value) return []
  return result.value.columns.map((c) => {
    const numeric = isNumericType(c.type)
    return {
      title: c.name,
      key: c.name,
      minWidth: numeric ? 90 : 120,
      width: numeric ? 130 : undefined,
      align: numeric ? ('right' as const) : ('left' as const),
      ellipsis: { tooltip: true },
      render(row: Record<string, unknown>) {
        const text = fmtText(row[c.name], c.type)
        const empty = fmtEmpty(row[c.name])
        return h(
          'span',
          {
            style: {
              color: empty ? '#c8c8c8' : '#333',
              fontVariantNumeric: numeric ? 'tabular-nums' : 'normal',
              fontWeight: empty ? 400 : 400,
            },
            title: String(row[c.name] ?? ''),
          },
          text,
        )
      },
    }
  })
})
const tableData = computed(() => result.value?.rows ?? [])
</script>

<template>
  <NModal
    :show="props.show"
    preset="card"
    style="width:1100px;max-width:96vw;"
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

    <div style="display:flex;flex-direction:column;">
      <!-- 风险条 -->
      <div
        style="padding:7px 12px;border-radius:6px;background:#FFF4F4;border:1px solid #F4C9C9;color:#791F1F;font-size:12px;line-height:1.55;margin-bottom:10px;"
      >
        <strong>⚠ 仅支持 SELECT / WITH / EXPLAIN / SHOW / DESCRIBE；</strong>
        写语句将被<strong>关键字黑名单拦截</strong>，会话强制 <code>READ ONLY</code>，默认自动追加 <code>LIMIT 500</code>，单次最长 15 秒。
        <kbd style="padding:0 4px;border:1px solid #791F1F;border-radius:3px;font-size:11px;background:#fff;margin-left:6px;">Ctrl+Enter</kbd> 执行
      </div>

      <!-- 工具栏 -->
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
        <NRadioGroup
          :value="compactNumbers ? '1' : '0'"
          @update:value="(v: string | number | null) => (compactNumbers = String(v) === '1')"
          size="small"
          name="num-format"
          style="margin-left:4px;"
        >
          <NRadio value="1">紧凑</NRadio>
          <NRadio value="0">原始</NRadio>
        </NRadioGroup>
        <span style="margin-left:auto;font-size:11px;color:#aaa;">{{ sqlText.length }} 字符</span>
      </div>

      <!-- SQL 编辑器 -->
      <NInput
        v-model:value="sqlText"
        type="textarea"
        placeholder="SELECT * FROM stock_info WHERE list_status = 'L' LIMIT 100"
        :autosize="{ minRows: 4, maxRows: 8 }"
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
        <span style="font-size:10px;background:#FFE5E5;padding:1px 5px;border-radius:3px;margin-right:6px;color:#791F1F;">{{ _eCode(errorMsg) }}</span>
        {{ errorMsg.replace(/^[A-Z_]+:\s*/, '') }}
      </div>

      <!-- 结果区：NDataTable 自带 sticky thead + scroll -->
      <div style="border:1px solid #ececec;border-radius:6px;overflow:hidden;background:#fff;">
        <div v-if="!result && !running" style="padding:30px;text-align:center;color:#888;font-size:13px;">
          尚无结果
        </div>
        <div v-else-if="result && result.rows.length === 0" style="padding:30px;text-align:center;color:#888;font-size:13px;">
          ✓ 查询成功，0 行结果
        </div>
        <NDataTable
          v-else-if="result"
          :columns="tableColumns"
          :data="tableData"
          :bordered="false"
          :single-line="false"
          size="small"
          :max-height="420"
          :virtual-scroll="true"
          style="font-family:Consolas,Menlo,monospace;font-size:12.5px;"
        />
      </div>
    </div>
  </NModal>
</template>
