import type { GlobalThemeOverrides } from 'naive-ui'

/**
 * InfoData 全站配色（蓝骨金魂 v2）
 * 规范文档：docs/design/颜色体系设计规范.md
 * - primary / info = 深空蓝 #185FA5（hover 亮蓝 #1E6FFF）
 * - success 也映射为蓝：双保险，杜绝「绿=成功」与「绿=行情跌」同屏混淆
 * - error = 深红棕 #791F1F（区别于行情涨红 #EF232A）
 * - warning = 琥珀 #B45309
 * 红涨绿跌仅保留在行情数字与 K 线（style.css / 各行情组件自定义 class）
 */
export const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#185FA5',
    primaryColorHover: '#1E6FFF',
    primaryColorPressed: '#0C447C',
    primaryColorSuppl: '#1E6FFF',
    infoColor: '#185FA5',
    infoColorHover: '#1E6FFF',
    infoColorPressed: '#0C447C',
    successColor: '#185FA5',
    successColorHover: '#1E6FFF',
    successColorPressed: '#0C447C',
    warningColor: '#B45309',
    warningColorHover: '#C26014',
    warningColorPressed: '#92400E',
    errorColor: '#791F1F',
    errorColorHover: '#8F2626',
    errorColorPressed: '#5E1616',
    bodyColor: '#F5F7FA',
    cardColor: '#FFFFFF',
    modalColor: '#FFFFFF',
    popoverColor: '#FFFFFF',
    tableColor: '#FFFFFF',
    borderColor: '#D4D7DE',
    dividerColor: '#E7E9EE',
    textColor1: '#1F2937',
    textColor2: '#6B7280',
    textColor3: '#9CA3AF',
  },
  Menu: {
    itemTextColorActive: '#185FA5',
    itemIconColorActive: '#185FA5',
    itemTextColorActiveHover: '#1E6FFF',
    itemIconColorActiveHover: '#1E6FFF',
  },
  Button: {
    fontWeight: '500',
    fontWeightStrong: '600',
  },
  Switch: {
    railColorActive: '#185FA5',
  },
  Tag: {
    borderRadius: '4px',
  },
}
