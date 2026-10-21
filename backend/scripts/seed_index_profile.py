#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""指数档案（index_profile）全量播种 —— 21 只指数的「释义/基准 + 核心说明/不足说明」唯一事实来源。

用法：python scripts/seed_index_profile.py            # 只读预览（打印 21 行与变更）
      python scripts/seed_index_profile.py --apply    # 实际写入（幂等 upsert）

为什么要把这个从 setup_index_tables.py 迁出来（2026-10-18 立）：
  此前 index_profile 的唯一写入方是 backend/app/setup_index_tables.py，而该脚本
  - 不在 backend/scripts/（恢复脚本目录）
  - 不在 restore.py 的 SEEDS 列表
  - 不在 MIGRATIONS.md（该台账当时尚不存在）
  后果：干净机器跑完 restore.py，index_profile 是空表，而 table_meta 里它标着
  rebuild='full'（= 可重建）。这违反 AGENTS.md 第 5 条「用有采集器证明可重建」——
  这里连采集器都没有。本脚本把它纳入恢复链路，并顺手补上「核心说明/不足说明」两列。

两列口径（见 docs/design/指数两字段落地方案_设计规范_v4.0_2026-10-18.md）：
  core_note  = 用户对「核心之处」的原话，多句用「、」直连，一字不裁（含句尾「的指数」「之一」）
  limit_note = 用户对「不足之处」的原话，一字不裁
  来源：用户 2026-10-18 对 14 只指数的归纳（40 句核心 + 14 段不足）；其余 7 只两列留空。

importance_rank 列口径（见 docs/design/指数重要程度列_增量补丁_v5.0_2026-10-18.md）：
  1~14 = 用户在 2026-10-18 文本中按出现先后对 14 只指数赋予的重要程度
  99   = 用户未确认排序的 7 只的统一默认最小值（000016/399001/399330/399673/000698/980017/000832）；
         99 是哨兵值、远离已确认区 1~14，后续用户确认排序后逐只改为真实位次即可
  与后端 api/market.py 的 INDEX_ORDER 常量口径一致；下一轮若用 ORDER BY importance_rank
  取代常量，前端零改动。

⚠️ 维护纪律：index_profile 的 description / base_date / base_point 只改本文件再重跑，
   直改 DB 会被下次 seed 覆盖；core_note / limit_note 同理。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402

# (index_code, index_name, description, base_date, base_point, core_note, limit_note, importance_rank)
#   base_date / base_point 无把握的留空（None）防误导；core_note / limit_note 原话未提的留空；
#   importance_rank = 用户文本中的先后顺序，1=上证 ... 14=中证全指房地产；用户未提的留 None。
SEED = [
    ("000001", "上证指数",
     "上证综合指数由在上海证券交易所上市的全部股票计算而成，反映沪市整体走势，是发布最早、认知度最高的 A 股宽基指数。因覆盖全市场，成分股数量随沪市扩容持续增长。",
     "1990-12-19", "100",
     "最早最权威的指数、最深入人心的指数、A股投资人都盯着的指数、最能影响大众情绪的指数、最能反映大众情绪的指数",
     "上证指数只包含了上交所，未包含深交所，理论上并不能反映A股的全貌",
     1),
    ("000300", "沪深300",
     "由沪深两市中规模大、流动性好的 300 只股票组成，覆盖 A 股核心资产，是境内股票市场最具代表性的跨市场基准指数，也是基金业绩比较的主要基准之一。",
     "2004-12-31", "1000",
     "第二能反映A股整体趋势的指数、新进的权威的指数、第二深入人心的指数、最能反映大票的指数",
     "沪深300只包含了大权重股票，只能反映整体大盘和大票的情况，不能反映更细的微盘股、行业等",
     2),
    ("399006", "创业板指",
     "选取创业板市场中市值大、流动性好的 100 只股票组成样本，反映成长型创新创业企业在创业板的核心表现。",
     "2010-05-31", "1000",
     "最能反映创业板股票整体情况的指数、新进的权威的指数、第三深入人心的指数、代表业绩好科技公司的指数",
     "创业板指只代表了在创业板上市股票的指数，不包含主板，所以其更多是表征新兴公司或者是包含行业的一些情况",
     3),
    ("000688", "科创50",
     "选取上海证券交易所科创板中市值大、流动性好的 50 只证券组成样本，反映科创板最具代表性的一批科技创新龙头企业的整体表现。",
     "2019-12-31", "1000",
     "最能反映科创板股票整体情况的指数、新进的权威的指数、并列第三深入人心的指数、代表国产科技尤其是半导体相关的指数、最能反映市场风险偏好的指数",
     "与创业板指数类似，只代表了在科创板上市股票的指数，不包含主板，所以其更多是表征新兴公司或者是包含行业的一些情况",
     4),
    ("000985", "中证全指",
     "由剔除 ST、*ST 股票及上市时间不足 3 个月等证券后的沪深市场剩余证券构成，覆盖面接近全市场，是中证规模指数与行业指数的选样空间，反映 A 股全市场整体走势。",
     "2004-12-31", "1000",
     "最能反映A股全市场整体趋势的指数",
     "中证全指代表了整体趋势，但是又不像上证指数和沪深300那样深入人心，所以对公众情绪影响和反映没那么大。同时，因为包含了所有股票，所以并不能更细分地看出A股内在结构的情况和变化",
     5),
    ("000905", "中证500",
     "全部 A 股中剔除沪深300 指数样本及总市值排名靠前的股票后，选取市值排名靠前的 500 只股票组成，综合反映 A 股中盘股的整体表现。",
     "2004-12-31", "1000",
     "综合反映A股中盘股的整体情况和表现",
     "中盘股市值涨跌对整体市场冲击没有像大盘股那么大，同时也不像微盘股那样会有很多小资金散户在关注，所以关注中证500指数的投资人较少，该指数也较少能反映整体股市情绪、趋势",
     6),
    ("000852", "中证1000",
     "在中证800（沪深300+中证500）样本之外，选取规模偏小且流动性好的 1000 只证券组成，综合反映 A 股小市值公司的整体表现，与沪深300、中证500 形成规模梯度互补。",
     "2004-12-31", "1000",
     "最能反映A股微盘股的指数、最能反映A股散户群体情绪的指数、第四深入人心的指数",
     "更多反映散户和小机构情绪的指数，不能反映整体A股情绪和指数",
     7),
    ("932000", "中证2000",
     "在中证800、中证1000 指数样本之外，选取规模偏小且流动性较好的 2000 只证券组成，刻画 A 股更小市值证券的整体表现，是中证规模梯度（300→500→1000→2000）的下沿延伸。",
     "2013-12-31", "1000",
     "更能反映A股更小的微盘股的指数、较能反映A股散户群体情绪的指数",
     "更多反映散户和小机构情绪的指数，不能反映整体A股情绪和指数，与中证1000定位差别没那么明显，大家可能更关注中证1000",
     8),
    ("000922", "中证红利",
     "选取沪深市场现金股息率高、分红比较稳定、具有一定规模及流动性的 100 只证券作为样本，反映 A 股高股息证券的整体表现，属于策略加权（红利）指数。",
     "2004-12-31", "1000",
     "最能反映A股市场避险情绪指数之一、最能反映A股市场高股息股票情况指数",
     "仅能代表高股息股票情况",
     9),
    ("399986", "中证银行",
     "从中证全指指数样本中选取银行业证券组成，反映 A 股银行行业上市公司的整体表现，属于行业主题指数。",
     "2004-12-31", "1000",
     "最能反映A股市场避险情绪指数之一、最能反映A股市场银行大盘股情况指数、最能反映国家队动作的指数之一、是A股市场的压舱石",
     "仅能代表银行类股票情况",
     10),
    ("399975", "证券公司",
     "选取证券与资本市场服务行业的上市公司证券构成样本，反映证券公司（券商）行业整体表现，是券商板块行情的代表性指数。",
     "2004-12-31", "1000",
     "较能反映A股市场亢奋情绪指数之一、最能反映A股市场证券股情况指数、是A股市场的压舱石",
     "仅能代表证券类股票情况",
     11),
    ("399997", "中证白酒",
     "从中证全指指数样本中选取白酒生产相关证券组成，反映 A 股白酒行业上市公司的整体表现，属于消费行业主题指数。",
     "2004-12-31", "1000",
     "第二能反映A股市场避险情绪指数之一、最能反映A股市场老一派价值投资者的情绪之一、最能反映A股市场保守情绪指数之一",
     "仅能代表白酒类股票情况",
     12),
    ("899050", "北证50",
     "选取北京证券市场中市值大、流动性好的 50 只证券组成样本，反映北交所最具代表性的核心企业的整体表现。",
     None, None,
     "最能反映北交所上市公司指数",
     "北交所都是一些小股票，且参与门槛较高，投资群体关注度较少，同时是新进的指数",
     13),
    ("931775", "中证全指房地产指数",
     "从中证全指指数样本中选取房地产行业的证券组成，反映 A 股房地产行业上市公司的整体表现，属于行业主题指数。",
     None, None,
     "最能反映房地产类股票指数、当它在跌或者是震荡说明整体市场资金情绪正常，当它突然涨了很多在当日涨幅的主题行业或者指数中领先，那说明市场处于资金无人买卖极其冷清的情况",
     "仅能代表房地产类股票情况",
     14),
    # ---------- 以下 7 只：用户原话未提及，core_note / limit_note 留空；importance_rank=99 默认最小值 ----------
    ("000016", "上证50",
     "挑选上海证券市场规模大、流动性好的最具代表性的 50 只股票组成样本股，综合反映沪市最具影响力的一批龙头大盘股的整体表现。",
     "2003-12-31", "1000", None, None, 99),
    ("399001", "深证成指",
     "由深圳证券市场中具有市场代表性的 500 只上市股票组成，反映深市整体走势，是深市旗舰型宽基指数。",
     "1994-07-20", "1000", None, None, 99),
    ("399330", "深证100",
     "选取深圳市场中市值大、流动性好的 100 只股票组成，覆盖深市核心优质资产，是深市大盘蓝筹的代表指数。",
     None, None, None, None, 99),
    ("399673", "创业板50",
     "从创业板指数样本股中选取 50 只流动性好的龙头股票组成，聚焦创业板中交易活跃的核心标的。",
     None, None, None, None, 99),
    ("000698", "科创100",
     "选取科创板中市值中等、流动性较好的 100 只证券组成，反映科创板中等市值证券的整体表现，与科创50 形成市值梯度互补。",
     None, None, None, None, 99),
    ("980017", "国证芯片",
     "选取芯片产业相关上市公司证券构成样本，覆盖设计、制造、封装测试、设备与材料等环节，反映 A 股芯片产业的整体表现，属于科技主题指数。",
     None, None, None, None, 99),
    ("000832", "中证转债",
     "由沪深市场剩余期限一年及以上、可交易的可转换公司债券组成样本，反映可转债市场整体价格走势，是转债类基金常用的业绩基准。",
     "2002-12-31", "100", None, None, 99),
]

# 与 setup_index_tables.py 的 SEED 对齐检查：index_code 必须完全相同（21 只），否则报错。
EXPECT_CODES = [
    "000001", "000016", "000300", "000905", "000852", "000688", "000698",
    "399001", "399006", "399330", "399673", "899050", "931775", "000832",
    "000922", "000985", "399975", "399986", "399997", "932000", "980017",
]

# 14 只用户清单 → 重要程度 1~14；其余 7 只 = 99（用户未确认排序的默认最小值，后续确认后调整）。
# 代码与 SEED 一一对应、且 1~14 无重无缺。
EXPECT_RANK = {
    "000001": 1, "000300": 2, "399006": 3, "000688": 4, "000985": 5,
    "000905": 6, "000852": 7, "932000": 8, "000922": 9, "399986": 10,
    "399975": 11, "399997": 12, "899050": 13, "931775": 14,
    "000016": 99, "399001": 99, "399330": 99, "399673": 99,
    "000698": 99, "980017": 99, "000832": 99,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只读预览）")
    args = ap.parse_args()

    codes = [s[0] for s in SEED]
    ranks = [s[7] for s in SEED]
    assert set(codes) == set(EXPECT_CODES) and len(codes) == len(set(codes)) == 21, (
        "SEED 的 index_code 集合与预期 21 只不一致，请核对")
    assert dict(zip(codes, ranks)) == EXPECT_RANK, (
        "SEED 的 importance_rank 与 EXPECT_RANK 不一致；1~14 无重无缺，7 只用户未提的必须为 None")

    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            # 确认三列已存在（应由迁移脚本加好），不存在则明确报错而不是静默写坏
            cur.execute("SHOW COLUMNS FROM index_profile LIKE 'core_note'")
            has_core = cur.fetchone() is not None
            cur.execute("SHOW COLUMNS FROM index_profile LIKE 'limit_note'")
            has_limit = cur.fetchone() is not None
            cur.execute("SHOW COLUMNS FROM index_profile LIKE 'importance_rank'")
            has_rank = cur.fetchone() is not None
            if not (has_core and has_limit):
                print("❌ index_profile 缺少 core_note / limit_note 列，请先跑迁移脚本 "
                      "(scripts/migrate_index_profile_two_columns.py)")
                return 2
            if not has_rank:
                print("❌ index_profile 缺少 importance_rank 列，请先跑迁移脚本 "
                      "(scripts/migrate_index_profile_importance_rank.py)")
                return 2

            cur.execute("SELECT index_code, index_name, description, base_date, base_point, "
                        "core_note, limit_note, importance_rank FROM index_profile")
            cur_rows = {r[0]: r for r in cur.fetchall()}

        print(f"本脚本 {len(SEED)} 只；数据库现有 {len(cur_rows)} 只")
        missing = [c for c in codes if c not in cur_rows]
        if missing:
            print(f"⚠️ 数据库缺以下指数（将新增）：{missing}")

        changed = []
        print()
        print(f"{'代码':<8}{'名称':<14}{'核心说明字数':<8}{'不足说明字数':<8}{'重要度':<6}状态")
        print("-" * 64)
        for code, name, desc, bd, bp, core, lim, rank in SEED:
            cur_v = cur_rows.get(code)
            if cur_v is None:
                state = "新增"
            else:
                same = (cur_v[1] == name and cur_v[2] == desc
                        and cur_v[3] == bd and cur_v[4] == bp
                        and cur_v[5] == core and cur_v[6] == lim
                        and cur_v[7] == rank)
                state = "一致" if same else "变更"
            if state != "一致":
                changed.append(code)
            print(f"{code:<8}{name:<14}{len(core or '') if core else '-':<8}"
                  f"{len(lim or '') if lim else '-':<8}{rank if rank else '-':<6}{state}")

        if not args.apply:
            print()
            print(f"[只读预览] 需变更 {len(changed)} 只：{changed or '无'}")
            print("加 --apply 才会写入。")
            return 0

        with conn.cursor() as cur:
            for code, name, desc, bd, bp, core, lim, rank in SEED:
                cur.execute(
                    """INSERT INTO index_profile
                         (index_code, index_name, description, base_date, base_point,
                          source, core_note, limit_note, importance_rank)
                       VALUES (%s, %s, %s, %s, %s, 'seed', %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                         index_name = VALUES(index_name),
                         description = VALUES(description),
                         base_date = VALUES(base_date),
                         base_point = VALUES(base_point),
                         source = 'seed',
                         core_note = VALUES(core_note),
                         limit_note = VALUES(limit_note),
                         importance_rank = VALUES(importance_rank)""",
                    (code, name, desc, bd, bp, core, lim, rank),
                )
        conn.commit()
        print()
        print(f"✅ 已写入 {len(SEED)} 只（变更 {len(changed)} 只：{changed or '无'}）")
        print("   复核请另起连接跑 _scratch/p22_verify_land.py")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
