<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NEmpty, NModal, NTag } from 'naive-ui'

interface SortPref {
  col: string
  dir: 'asc' | 'desc'
}

const props = defineProps<{
  show: boolean
  prefs: Record<string, SortPref>
  tables: { name: string; comment: string }[]
}>()

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  (e: 'reset', table: string): void
  (e: 'clear-all'): void
}>()

function close() {
  emit('update:show', false)
}

const rows = computed(() =>
  Object.entries(props.prefs).map(([table, p]) => {
    const t = props.tables.find((x) => x.name === table)
    return { table, col: p.col, dir: p.dir, comment: t?.comment || '' }
  }),
)
</script>

<template>
  <NModal
    :show="props.show"
    preset="card"
    style="width:660px;max-width:94vw;"
    size="huge"
    title="排序偏好 · 已存至服务端"
    :bordered="false"
    @update:show="emit('update:show', $event)"
    @mask-click="close"
  >
    <div style="font-size:12px;color:#888;margin-bottom:12px;line-height:1.7;">
      数据中心每张表可自定义「默认排序」，偏好存在服务端 <code>user_prefs</code> ——
      换浏览器 / 换机器 / 清缓存依然生效。打开表时自动按此排序；在表头点击列名可随时
      改为新排序（升 → 降 → 取消，循环）。
    </div>

    <div v-if="rows.length === 0" style="padding:26px 0;">
      <NEmpty description="暂无自定义排序 · 在数据中心点列头即可记住" />
    </div>
    <div
      v-else
      style="border:1px solid #ececec;border-radius:8px;max-height:380px;overflow:auto;"
    >
      <div
        v-for="r in rows"
        :key="r.table"
        style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:13px;"
      >
        <span
          style="flex:0 0 200px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:Consolas,Menlo,monospace;color:#185FA5;"
          :title="r.table"
        >{{ r.table }}</span>
        <NTag v-if="r.comment" size="tiny" :bordered="false" type="default" style="max-width:170px;overflow:hidden;">
          {{ r.comment }}
        </NTag>
        <span style="font-family:Consolas,Menlo,monospace;color:#333;white-space:nowrap;margin-left:auto;">{{ r.col }}</span>
        <NTag size="small" :bordered="false" :type="r.dir === 'asc' ? 'info' : 'warning'">
          {{ r.dir === 'asc' ? '↑ 升序' : '↓ 降序' }}
        </NTag>
        <NButton size="tiny" quaternary @click="emit('reset', r.table)">重置</NButton>
      </div>
    </div>

    <template #footer>
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-size:12px;color:#aaa;">共 {{ rows.length }} 条</span>
        <span style="margin-left:auto;display:flex;gap:8px;align-items:center;">
          <NButton v-if="rows.length" size="small" quaternary @click="emit('clear-all')">全部清空</NButton>
          <NButton size="small" type="primary" @click="close">关闭</NButton>
        </span>
      </div>
    </template>
  </NModal>
</template>
