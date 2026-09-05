<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { useRoute, useRouter, RouterView } from 'vue-router'
import {
  NLayout, NLayoutHeader, NLayoutSider, NLayoutContent,
  NMenu, NSpace, NText,
  type MenuOption,
} from 'naive-ui'

const route = useRoute()
const router = useRouter()

const menuOptions = computed<MenuOption[]>(() =>
  router.getRoutes()
    .filter((r) => r.meta?.title)
    .map((r) => ({
      key: r.name as string,
      label: () =>
        h('span', { style: 'display:flex;align-items:center;gap:8px;' }, [
          h('span', { style: 'font-size:18px;' }, r.meta!.icon as string),
          h('span', null, r.meta!.title as string),
        ]),
    })),
)

const activeKey = computed(() => (route.name as string) || '')

const collapsed = ref(false)
</script>

<template>
  <NLayout has-sider style="height:100vh">
    <NLayoutSider
      bordered
      :width="200"
      :collapsed-width="64"
      show-trigger
      :collapsed="collapsed"
      @collapse="collapsed = true"
      @expand="collapsed = false"
    >
      <div style="padding:16px 12px;font-weight:600;font-size:16px;color:#185FA5;border-bottom:1px solid #eee;">
        <span v-if="!collapsed">📊 InfoData</span>
        <span v-else>📊</span>
      </div>
      <NMenu
        :value="activeKey"
        :options="menuOptions"
        :collapsed="collapsed"
        :collapsed-width="64"
        :collapsed-icon-size="22"
        @update:value="(k: string) => router.push({ name: k })"
      />
    </NLayoutSider>
    <NLayout>
      <NLayoutHeader bordered style="padding:12px 24px;height:56px;display:flex;align-items:center;justify-content:space-between">
        <NSpace align="center">
          <NText depth="2" style="font-size:18px;font-weight:600">
            {{ route.meta?.title || 'InfoData 投研平台' }}
          </NText>
        </NSpace>
        <NSpace align="center">
          <NText depth="3" style="font-size:12px">v0.5 · 沪深 A 股本地数据中心</NText>
        </NSpace>
      </NLayoutHeader>
      <NLayoutContent style="padding:16px;background:#F5F7FA">
        <RouterView />
      </NLayoutContent>
    </NLayout>
  </NLayout>
</template>