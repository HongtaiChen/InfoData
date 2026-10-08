/**
 * 术语与量纲表（R9 · D7-a：落点为**前端一份常量**，不随响应下发）
 *
 * 为什么放在前端常量、而不是后端下发：
 * - `bp` / `pp` / `分位` 是**跨模块通用量纲**（5 个分析模块都用），不随数据变；
 * - 契约⑤「口径随响应下发」管的是**窗口口径**（如「近一年」vs「近 250 个月末」，会随数据变），
 *   术语定义**不随数据变** ⇒ 放后端会让 5 个模块的每个响应都背一份常量；
 * - 仍满足 R9 的一致性判据「全站统一一份」—— 全站只有本文件一份，没有第二份副本。
 *
 * `kind` 只用于术语表里的分组显示（量纲 / 缩写 / 口径档名），不参与任何判定。
 */

export type TermKind = 'unit' | 'abbr' | 'scope'

export interface Term {
  /** 出现的原文（必须与页面上的字面完全一致，TermTip 用它做命中查找） */
  term: string
  /** 中文全称 / 定义 */
  full: string
  /** 补充说明（可选）：量纲的换算、口径的边界 */
  note?: string
  kind: TermKind
}

export const KIND_LABEL: Record<TermKind, string> = {
  unit: '量纲',
  abbr: '缩写',
  scope: '口径档名',
}

export const GLOSSARY: Term[] = [
  // ─────────────────────────── 量纲（3 项，R9 第 1 条硬要求）───────────────────────────
  {
    term: 'bp',
    full: '基点',
    note: '万分之一 = 0.01%，用于利率与利差的微小变动。例：+3.6bp = +0.036%。',
    kind: 'unit',
  },
  {
    term: 'pp',
    full: '百分点',
    note: '1pp = 1%，用于两条利率**水平**之间的差。例：−3.51pp 指相差 3.51 个百分点。',
    kind: 'unit',
  },
  {
    term: '分位',
    full: '在窗口内排第几',
    note: '0 = 窗口内最低，100 = 最高。本页默认窗口为「近一年」（约 250 个交易日）；'
      + '存量水位类指标不做分位（单调上行 ⇒ 分位恒贴近 100，是伪信号）。',
    kind: 'unit',
  },

  // ─────────────────── 两轴口径档名（12 项，R9 第 4 条 · v2.4 新增）───────────────────
  {
    term: '中国 · 在岸',
    full: '中国大陆在岸市场',
    note: '境内人民币口径（银行间 / 贷款报价 / 货币总量 / 外汇储备）。',
    kind: 'scope',
  },
  {
    term: '美国',
    full: '美国本土口径',
    note: '美联储与联邦机构发布的美元口径数据（H41 / H6 / 国债收益率）。',
    kind: 'scope',
  },
  {
    term: '欧元区',
    full: '欧元区（ECB 口径）',
    note: '欧洲央行统计的欧元区货币总量，按 EUR/USD 折美元后与其它经济体可比。',
    kind: 'scope',
  },
  {
    term: '全球 · 离岸',
    full: '离岸 / 跨境口径',
    note: '不隶属单一经济体：跨境债券利差（中美 10Y）、美元指数、离岸美元信贷。',
    kind: 'scope',
  },
  {
    term: '银行间',
    full: '银行间同业拆借市场',
    note: '机构之间的批发资金市场，Shibor 报价即出于此。',
    kind: 'scope',
  },
  {
    term: '贷款报价',
    full: '贷款市场报价利率市场',
    note: '报价行每月 20 日报出的贷款基准利率（LPR），面向实体融资。',
    kind: 'scope',
  },
  {
    term: '货币总量',
    full: '广义货币供应量',
    note: 'M2 / M3 一类的**存量水位**口径，看同比与拐点、不做分位。',
    kind: 'scope',
  },
  {
    term: '央行资产',
    full: '央行资产负债表',
    note: '美联储总资产；含财政部 TGA 与隔夜逆回购 ON RRP 的合成口径（美元净流动性 A5）。',
    kind: 'scope',
  },
  {
    term: '国债',
    full: '国债市场收益率',
    note: '中债 10Y、美债 10Y 及其利差与期限结构。',
    kind: 'scope',
  },
  {
    term: '外汇',
    full: '外汇市场汇率',
    note: '美元指数等汇率读数。',
    kind: 'scope',
  },
  {
    term: '外汇储备',
    full: '官方外汇储备',
    note: '央行持有的对外资产（配套读数为黄金储备实物量）。',
    kind: 'scope',
  },
  {
    term: '境外信贷',
    full: '离岸美元信贷',
    note: 'BIS 官方口径：全体非美借款人持有的美元计价信贷（跨境 + 借款人本地外币）。',
    kind: 'scope',
  },

  // ─────────────────────────── 缩写（27 项，R9 第 2 条）───────────────────────────
  {
    term: 'Shibor',
    full: '上海银行间同业拆放利率',
    note: '在岸人民币银行间的报价利率，不是成交利率。',
    kind: 'abbr',
  },
  {
    term: 'LPR',
    full: '贷款市场报价利率',
    note: '由报价行每月 20 日报出。⚠️ 它是**报价**利率，不等于实际贷款利率。',
    kind: 'abbr',
  },
  {
    term: 'OMO',
    full: '公开市场操作',
    note: '央行的日常资金投放/回笼（逆回购、MLF 等），看的是**数量**。',
    kind: 'abbr',
  },
  {
    term: 'SLF',
    full: '常备借贷便利',
    note: '利率走廊的上沿；隔夜利率持续贴近它 ⇒ 资金面极度紧张。',
    kind: 'abbr',
  },
  {
    term: 'R007',
    full: '银行间 7 天质押式回购利率',
    note: '7 天资金的**成交**利率，与 Shibor（报价）互为对照。',
    kind: 'abbr',
  },
  {
    term: 'BIS',
    full: '国际清算银行',
    note: '离岸美元信贷（IDS）与全球流动性指标（GLI）的官方来源。',
    kind: 'abbr',
  },
  {
    term: 'IDS',
    full: '国际债券统计',
    note: 'BIS 的债券口径数据集，本页用于离岸美元信贷的结构拆解。',
    kind: 'abbr',
  },
  {
    term: 'WS_GLI',
    full: 'BIS 全球流动性指标数据集',
    note: 'BIS 的数据表编号，本页「境外美元信贷」即取自该表。',
    kind: 'abbr',
  },
  {
    term: 'TGA',
    full: '美国财政部一般账户',
    note: '财政部在美联储的存款；余额上升 = 从市场抽走流动性。',
    kind: 'abbr',
  },
  {
    term: 'ON RRP',
    full: '隔夜逆回购（美联储）',
    note: '货币基金把多余现金存回美联储的通道；余额上升 = 锁住流动性。',
    kind: 'abbr',
  },
  {
    term: 'A5',
    full: '美元净流动性（本项目合成口径，非官方）',
    note: '＝ 美联储总资产 − TGA − ON RRP。⚠️ 不是官方公布值。',
    kind: 'abbr',
  },
  {
    term: 'M0',
    full: '流通中现金',
    kind: 'abbr',
  },
  {
    term: 'M1',
    full: '狭义货币供应量',
    note: '＝ M0 + 单位活期存款，反映**活化度**。',
    kind: 'abbr',
  },
  {
    term: 'M2',
    full: '广义货币供应量',
    note: '各国原生单位不同（美国十亿美元 / 中国亿元），本页已统一折美元。',
    kind: 'abbr',
  },
  {
    term: 'M3',
    full: '欧元区广义货币（ECB 口径）',
    note: '原生单位为百万欧元，本页按 EUR/USD 中间价折美元。',
    kind: 'abbr',
  },
  {
    term: '剪刀差',
    full: 'M1 同比 − M2 同比',
    note: '为负 = 钱停在定期/理财；由负转正并走阔 = 增量资金去找风险资产。',
    kind: 'abbr',
  },
  {
    term: 'H41',
    full: '美联储周度资产负债表（H.4.1）',
    note: '固定**周三**发布口径，月中看到的可能滞后一周。',
    kind: 'abbr',
  },
  {
    term: 'H6',
    full: '美联储货币供应量统计（H.6）',
    note: '美国 M2 的月度来源，有事后修订。',
    kind: 'abbr',
  },
  {
    term: 'TIPS',
    full: '通胀保值国债',
    note: '其收益率 = 实际利率；名义收益率高通胀期要与它对照才有意义。',
    kind: 'abbr',
  },
  {
    term: 'HY OAS',
    full: '高收益债期权调整利差',
    note: '信用风险的定价；判断「会不会真衰退」需与美债倒挂配合。',
    kind: 'abbr',
  },
  {
    term: 'MPA',
    full: '宏观审慎评估',
    note: '季末考核会推高特定期限的资金需求 —— 是期限结构的**日历扰动**来源。',
    kind: 'abbr',
  },
  {
    term: 'DXY',
    full: '美元指数',
    note: '对一篮子货币（欧元权重约 58%）的加权汇率，只反映**相对**强弱。',
    kind: 'abbr',
  },
  {
    term: 'DINIW',
    full: '美元指数实时快照（新浪口径）',
    note: '与主口径（官方日线）互为交叉校验。',
    kind: 'abbr',
  },
  {
    term: 'ECB',
    full: '欧洲中央银行',
    kind: 'abbr',
  },
  {
    term: 'ON / 1W / 1M / 3M',
    full: '隔夜 / 1 周 / 1 月 / 3 个月',
    note: 'Shibor 的四个期限（ON = overnight）。',
    kind: 'abbr',
  },
  {
    term: 'EUR/USD',
    full: '欧元兑美元汇率（中间价）',
    note: '用于把欧元区 M3 折成美元；汇率变动会污染同比的可比性。',
    kind: 'abbr',
  },
  {
    term: 'USD/CNY',
    full: '美元兑人民币汇率（中间价）',
    note: '用于把中国 M2 折成美元。⚠️ 不折美元就与美国 M2 比大小会得到相反结论。',
    kind: 'abbr',
  },
]

/** 命中查找表 —— TermTip 用它判断一个词是否已定义（未定义则渲染 `.tt-miss` 供断言抓出） */
export const GLOSSARY_MAP: Map<string, Term> = new Map(GLOSSARY.map((g) => [g.term, g]))
