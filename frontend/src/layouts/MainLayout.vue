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

/**
 * 侧栏菜单 = 根路由 children 中通过「进菜单三道闸」（见下方 declaredMenu）的路由，**按声明顺序**排列。
 *
 * 为什么不直接用 router.getRoutes()：它返回的是 vue-router 内部 matcher 列表，按 path score 降序排列，
 * **不是声明顺序**。带参数的路径评分更高，会把 /analysis/:moduleId 插到列表最前面，
 * 于是「分析详情」这个二级下钻页跑到了侧栏第一位（2026-09-14 实测：菜单 idx=0，排在「行情看板」之前）。
 * 改用 router.options.routes 与 router/index.ts 的书写顺序保持一致，新增页面照旧只改 router 一处。
 */
type MenuRoute = { name?: string; path?: string; meta?: Record<string, unknown> }
const rootRoute = router.options.routes.find((r) => r.path === '/' || r.children?.length) as { children?: MenuRoute[] } | undefined
const menuRoutes = (rootRoute?.children ?? []) as MenuRoute[]

/**
 * 进菜单的三道闸：① 有 meta.title ② 未标 meta.hidden ③ path 不含参数占位符 `:`
 * 第③条是机制性防线：带参数的路由必是二级详情页，直接跳会因缺参被拒绝（用户只看到「点了没反应」），
 * 即便将来有人新增详情页忘了写 hidden，也不会污染侧栏。
 */
const declaredMenu = computed(() =>
  menuRoutes.filter((r) => r.meta?.title && r.meta?.hidden !== true && !String(r.path ?? '').includes(':')),
)

const menuOptions = computed<MenuOption[]>(() =>
  declaredMenu.value.map((r) => ({
    key: r.name as string,
    label: () =>
      h('span', { style: 'display:flex;align-items:center;gap:8px;' }, [
        h('span', { style: 'font-size:18px;' }, r.meta!.icon as string),
        h('span', null, r.meta!.title as string),
      ]),
  })),
)

/**
 * 高亮项，三级回退：
 *  ① meta.activeMenu —— 二级页显式声明归属（/analysis/:moduleId → analysis）
 *  ② 匹配链上最后一个「进菜单」的路由 —— 嵌套路由场景
 *  ③ 最长路径前缀 —— 平级声明的二级页（/analysis/market-wind → analysis）
 * 否则二级页会让整条侧栏没有一项高亮，用户失去位置感。
 */
const activeKey = computed(() => {
  const declared = route.meta?.activeMenu as string | undefined
  if (declared && menuRoutes.some((r) => r.name === declared)) return declared

  const hit = [...route.matched].reverse().find((r) => r.meta?.title && r.meta?.hidden !== true)
  if (hit?.name) return hit.name as string

  const p = route.path
  const byPrefix = declaredMenu.value
    .map((r) => ({ name: r.name as string, base: '/' + String(r.path ?? '').split('/')[0] }))
    .filter((x) => x.base !== '/' && (p === x.base || p.startsWith(x.base + '/')))
    .sort((a, b) => b.base.length - a.base.length)[0]
  return byPrefix?.name || (route.name as string) || ''
})

const collapsed = ref(false)

/** 菜单点击：容错跳转。原先没有 catch，缺参路由的 rejection 被静默吞掉，用户只看到「点了没反应」 */
function onMenuSelect(k: string) {
  router.push({ name: k }).catch((e: unknown) => {
    console.error(`[nav] 菜单项「${k}」跳转失败（检查该路由是否缺少必填参数或已被移除）`, e)
  })
}
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
      <div style="height:54px;padding:14px 12px;display:flex;align-items:center;gap:9px;font-weight:700;font-size:15px;color:#185FA5;border-bottom:1px solid #eee;box-sizing:border-box;">
        <img src="/favicon.svg" alt="InvestBuddy" style="width:26px;height:26px;flex:none;display:block;" />
        <span v-if="!collapsed" style="white-space:nowrap;">InvestBuddy</span>
      </div>
      <NMenu
        :value="activeKey"
        :options="menuOptions"
        :collapsed="collapsed"
        :collapsed-width="64"
        :collapsed-icon-size="22"
        @update:value="onMenuSelect"
      />
    </NLayoutSider>
    <NLayout>
      <NLayoutHeader bordered style="padding:12px 24px;height:56px;display:flex;align-items:center;justify-content:space-between">
        <NSpace align="center">
          <NText depth="2" style="font-size:18px;font-weight:600">
            {{ route.meta?.title || 'InvestBuddy 投研平台' }}
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