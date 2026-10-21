import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      children: [
        { path: '', redirect: '/environment/domestic' },
        // 投资环境（章节化，2026-10-20 定稿：主侧栏「投资环境」可展开 7 个子章节，各章独立路由；
        // 章节内锚点导航由各章节视图自己负责，两级职责：主侧栏=换章节，页内锚点=章节内跳转）
        {
          path: 'environment',
          name: 'environment',
          component: () => import('@/layouts/EnvLayout.vue'),
          redirect: '/environment/domestic',
          meta: { title: '投资环境', icon: '🌍' },
          children: [
            { path: 'domestic', name: 'env-domestic', component: () => import('@/views/DomesticIndexView.vue'), meta: { title: '国内指数', icon: '🇨🇳' } },
            { path: 'global-indices', name: 'env-global-indices', component: () => import('@/views/GlobalIndexView.vue'), meta: { title: '国际指数', icon: '🌐' } },
            { path: 'sentiment', name: 'env-sentiment', component: () => import('@/views/MarketMoodView.vue'), meta: { title: '市场氛围', icon: '🌡️' } },
            { path: 'cn-policy', name: 'env-cn-policy', component: () => import('@/views/EnvPlaceholderView.vue'), meta: { title: '国内政策', icon: '🏛️' } },
            { path: 'global-env', name: 'env-global-env', component: () => import('@/views/GlobalEnvironmentView.vue'), meta: { title: '国际环境', icon: '🗺️' } },
            { path: 'global-money', name: 'env-global-money', component: () => import('@/views/GlobalMoneyView.vue'), meta: { title: '全球货币', icon: '💱' } },
            { path: 'cn-money', name: 'env-cn-money', component: () => import('@/views/DomesticMoneyView.vue'), meta: { title: '国内货币', icon: '🏦' } },
          ],
        },
        { path: 'concept', name: 'concept', component: () => import('@/views/ConceptView.vue'), meta: { title: '概念板块', icon: '🧩' } },
        { path: 'calendar', name: 'calendar', component: () => import('@/views/CalendarView.vue'), meta: { title: '投资日历', icon: '📅' } },
        { path: 'news', name: 'news', component: () => import('@/views/NewsView.vue'), meta: { title: '资讯浏览', icon: '📰' } },
        { path: 'analysis', name: 'analysis', component: () => import('@/views/AnalysisOverview.vue'), meta: { title: '分析研究', icon: '🔬' } },
        // 分析模块详情（注册式扩展：后端 app/analysis/registry.py + 前端模块视图映射）
        // ⚠️ hidden:true = 二级下钻页，不进侧栏菜单（它需要 :moduleId 参数，直接跳会因缺参被静默拒绝）
        //    activeMenu:'analysis' = 进入本页时侧栏高亮「分析研究」；title 与父级一致，模块名由页面内 h2 呈现
        { path: 'analysis/:moduleId', name: 'analysis-module', component: () => import('@/views/AnalysisModuleView.vue'), meta: { title: '分析研究', icon: '🔬', hidden: true, activeMenu: 'analysis' } },
        { path: 'jobs', name: 'jobs', component: () => import('@/views/JobsView.vue'), meta: { title: '作业监控', icon: '⚙️' } },
        { path: 'data', name: 'data', component: () => import('@/views/DataView.vue'), meta: { title: '数据中心', icon: '🗄️' } },
        { path: 'quality', name: 'quality', component: () => import('@/views/QualityView.vue'), meta: { title: '数据质量', icon: '🩺' } },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '系统设置', icon: '🔧' } },
      ],
    },
  ],
})

export default router