#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""InvestBuddy v3 结构落地（幂等，可重复执行）—— 新增采集表 + 补关键唯一索引 + 成分快照留档改造

用法：python scripts/setup_v3_tables.py            # 只读预览（打印将要做的变更）
      python scripts/setup_v3_tables.py --apply    # 实际执行

对应文档：docs/市场风向数据蓝图落地审计_2026-09-19.md §5「建议下一步」
  1) 美债断供修复          → bond_profit_daily 补 UNIQUE KEY (trade_date)（Upsert 前置条件）
  2) P2 估值采集器 → ERP   → index_valuation_daily（中证官网 + 乐咕 + 全A 三源）
  3) 蓝图 A 收口（钱贵不贵）→ interbank_rate_daily（Shibor + LPR）
  4) 蓝图 E 跨市场          → overseas_index_daily（恒生 + 道指/标普/纳指）
  5) 蓝图 E 汇率            → currency_boc_daily（人民币中间价）
  6) 蓝图 E 新基金发行       → fund_new_issue
  7) 蓝图 D 股票回购         → stock_repurchase
  8) §7-⑧ 成分股留档         → index_constituents 唯一键加入 trade_date（快照日）+ 补 sample_date，改为按日留档

⚠️ 纪律：本脚本与各 seed_*.py 一样是「唯一事实来源」的载体——结构性变更必须写在这里，
   而不是只手工在客户端执行；否则换机/重建库时这些表会凭空消失。
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

from app.db import get_db_config  # noqa: E402

# ---------------------------------------------------------------- 新表 DDL

NEW_TABLES = {
    "index_valuation_daily": """
    CREATE TABLE IF NOT EXISTS index_valuation_daily (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码（全A用 ALL_A）',
        index_name VARCHAR(50) DEFAULT NULL COMMENT '指数名称',
        trade_date DATE NOT NULL COMMENT '估值日期',
        source VARCHAR(20) NOT NULL COMMENT 'csindex(中证官网)/legu(乐咕)/all_a(全A等权)',
        pe_lyr DECIMAL(12,4) DEFAULT NULL COMMENT '静态市盈率（整体法；中证「市盈率1」/乐咕「静态市盈率」）',
        pe_ttm DECIMAL(12,4) DEFAULT NULL COMMENT '滚动市盈率 TTM（整体法；中证「市盈率2」/乐咕「滚动市盈率」）',
        pe_ttm_median DECIMAL(12,4) DEFAULT NULL COMMENT '滚动市盈率中位数（仅乐咕/全A 源提供）',
        pe_lyr_median DECIMAL(12,4) DEFAULT NULL COMMENT '静态市盈率中位数（仅乐咕/全A 源提供）',
        dividend_yield DECIMAL(10,4) DEFAULT NULL COMMENT '股息率(%)',
        dividend_yield2 DECIMAL(10,4) DEFAULT NULL COMMENT '股息率2（中证口径：近12月 vs 近3年分红）',
        pe_ttm_pct10y DECIMAL(8,4) DEFAULT NULL COMMENT '滚动PE近十年分位（0~1，仅 all_a 源直接给）',
        close_point DECIMAL(16,4) DEFAULT NULL COMMENT '指数点位（乐咕/全A 源提供，供勾稽）',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_index_date_source (index_code, trade_date, source),
        KEY idx_trade_date (trade_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '指数估值日频（股债性价比 ERP 的估值分母，2026-09-19 P2 落地）'
    """,
    "interbank_rate_daily": """
    CREATE TABLE IF NOT EXISTS interbank_rate_daily (
        trade_date DATE NOT NULL COMMENT '交易日',
        shibor_on DECIMAL(8,4) DEFAULT NULL COMMENT 'Shibor 隔夜(%)',
        shibor_1w DECIMAL(8,4) DEFAULT NULL COMMENT 'Shibor 1周(%)',
        shibor_1m DECIMAL(8,4) DEFAULT NULL COMMENT 'Shibor 1月(%)',
        shibor_3m DECIMAL(8,4) DEFAULT NULL COMMENT 'Shibor 3月(%)',
        lpr_1y DECIMAL(8,4) DEFAULT NULL COMMENT 'LPR 1年(%)',
        lpr_5y DECIMAL(8,4) DEFAULT NULL COMMENT 'LPR 5年(%)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        PRIMARY KEY (trade_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '银行间拆借利率 + LPR（蓝图A「钱贵不贵」维度，2026-09-19 落地）'
    """,
    "overseas_index_daily": """
    CREATE TABLE IF NOT EXISTS overseas_index_daily (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT 'HSI/DJI/SPX/IXIC',
        index_name VARCHAR(50) DEFAULT NULL COMMENT '恒生指数/道琼斯/标普500/纳斯达克',
        trade_date DATE NOT NULL COMMENT '交易日期（当地）',
        open DECIMAL(16,4) DEFAULT NULL,
        high DECIMAL(16,4) DEFAULT NULL,
        low DECIMAL(16,4) DEFAULT NULL,
        close DECIMAL(16,4) DEFAULT NULL,
        volume DECIMAL(24,2) DEFAULT NULL,
        amount DECIMAL(28,2) DEFAULT NULL,
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_code_date (index_code, trade_date),
        KEY idx_trade_date (trade_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '海外/港股指数日线（蓝图E 跨市场，2026-09-19 落地）'
    """,
    "currency_boc_daily": """
    CREATE TABLE IF NOT EXISTS currency_boc_daily (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        currency VARCHAR(10) NOT NULL DEFAULT 'USD' COMMENT '币种代码（USD/EUR/JPY…）',
        currency_name VARCHAR(20) DEFAULT NULL COMMENT '币种中文名',
        trade_date DATE NOT NULL COMMENT '交易日期',
        mid_price DECIMAL(12,6) DEFAULT NULL COMMENT '央行中间价（**元/1 外币**，源值 100 外币价已除 100 归一）',
        spot_buy DECIMAL(12,6) DEFAULT NULL COMMENT '中行汇买价（元/1 外币）',
        spot_sell DECIMAL(12,6) DEFAULT NULL COMMENT '中行钞卖价/汇卖价（元/1 外币）',
        ref_price DECIMAL(12,6) DEFAULT NULL COMMENT '中行折算价（元/1 外币）',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_currency_date (currency, trade_date),
        KEY idx_trade_date (trade_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '人民币外汇牌价与中间价（蓝图E，2026-09-19 落地）'
    """,
    "fund_new_issue": """
    CREATE TABLE IF NOT EXISTS fund_new_issue (
        fund_code VARCHAR(12) NOT NULL COMMENT '基金代码',
        fund_name VARCHAR(100) DEFAULT NULL COMMENT '基金简称',
        company VARCHAR(60) DEFAULT NULL COMMENT '发行公司',
        fund_type VARCHAR(40) DEFAULT NULL COMMENT '基金类型（混合型-偏股/债券型…）',
        subs_period VARCHAR(40) DEFAULT NULL COMMENT '集中认购期（源格式 YY/MM/DD～YY/MM/DD）',
        raise_share DECIMAL(16,4) DEFAULT NULL COMMENT '募集份额（亿份）',
        establish_date DATE DEFAULT NULL COMMENT '成立日期',
        manager VARCHAR(80) DEFAULT NULL COMMENT '基金经理',
        purchase_status VARCHAR(20) DEFAULT NULL COMMENT '申购状态',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        PRIMARY KEY (fund_code),
        KEY idx_establish (establish_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '新基金发行与成立（蓝影E 基金发行冰点=反向底部信号，2026-09-19 落地）'
    """,
    "stock_repurchase": """
    CREATE TABLE IF NOT EXISTS stock_repurchase (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
        stock_name VARCHAR(50) DEFAULT NULL COMMENT '股票简称',
        start_date DATE NOT NULL COMMENT '回购起始时间',
        announce_date DATE DEFAULT NULL COMMENT '最新公告日期',
        progress VARCHAR(20) DEFAULT NULL COMMENT '实施进度（完成实施/股东大会通过/董事会预案…）',
        plan_price_low DECIMAL(12,4) DEFAULT NULL COMMENT '计划回购价格区间-下限',
        plan_price_high DECIMAL(12,4) DEFAULT NULL COMMENT '计划回购价格区间-上限',
        plan_amount_low DECIMAL(22,2) DEFAULT NULL COMMENT '计划回购金额区间-下限（元）',
        plan_amount_high DECIMAL(22,2) DEFAULT NULL COMMENT '计划回购金额区间-上限（元）',
        plan_pct_low DECIMAL(12,6) DEFAULT NULL COMMENT '占总股本比例-下限(%)',
        plan_pct_high DECIMAL(12,6) DEFAULT NULL COMMENT '占总股本比例-上限(%)',
        done_amount DECIMAL(22,2) DEFAULT NULL COMMENT '已回购金额（元）',
        done_shares DECIMAL(22,2) DEFAULT NULL COMMENT '已回购股份数量',
        done_price_low DECIMAL(12,4) DEFAULT NULL COMMENT '已回购价格区间-下限',
        done_price_high DECIMAL(12,4) DEFAULT NULL COMMENT '已回购价格区间-上限',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_code_start (stock_code, start_date),
        KEY idx_announce (announce_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '股票回购（蓝图D 产业资本态度，2026-09-19 落地）'
    """,
}


def _cols(cur, table: str) -> dict:
    cur.execute(
        "SELECT column_name, is_nullable, column_type FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = %s", (table,))
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def _indexes(cur, table: str) -> dict:
    cur.execute(
        "SELECT index_name, non_unique, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols "
        "FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = %s "
        "GROUP BY index_name, non_unique", (table,))
    return {r[0]: {"unique": r[1] == 0, "cols": (r[2] or "").split(",")} for r in cur.fetchall()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行（默认只读预览）")
    args = ap.parse_args()

    conn = pymysql.connect(**get_db_config().to_dict())
    plan: list[str] = []
    try:
        cur = conn.cursor()

        # ---------- 1) 新表 ----------
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        existing = {r[0] for r in cur.fetchall()}
        for t in NEW_TABLES:
            plan.append(("建表" if t not in existing else "已存在") + f"  {t}")

        # ---------- 2) bond_profit_daily 补唯一索引 ----------
        bidx = _indexes(cur, "bond_profit_daily") if "bond_profit_daily" in existing else {}
        has_uk_date = any(v["unique"] and v["cols"] == ["trade_date"] for v in bidx.values())
        if has_uk_date:
            plan.append("已存在  UNIQUE(bond_profit_daily.trade_date)")
        else:
            cur.execute("SELECT trade_date, COUNT(*) AS n FROM bond_profit_daily "
                        "GROUP BY trade_date HAVING n > 1 LIMIT 5")
            dup = cur.fetchall()
            if dup:
                plan.append(f"❌ 阻断  bond_profit_daily 存在重复 trade_date（{dup}），需先去重")
            else:
                plan.append("新增    UNIQUE(bond_profit_daily.trade_date)  ← Upsert 回填的前置条件")

        # ---------- 3) index_constituents 快照留档改造 ----------
        cidx = _indexes(cur, "index_constituents")
        need_uk_change = "uk_index_stock_date" not in cidx
        icols = _cols(cur, "index_constituents")
        need_notnull = icols.get("trade_date", ("YES", ""))[0] == "YES"
        need_sample_date = "sample_date" not in icols
        if need_uk_change:
            plan.append("改造    index_constituents: DROP uk_index_stock → ADD uk_index_stock_date"
                        "(index_code, stock_code, trade_date)  ← §7-⑧ 快照留档")
        else:
            plan.append("已存在  index_constituents.uk_index_stock_date")
        if need_notnull:
            plan.append("改造    index_constituents.trade_date → NOT NULL（唯一键需要，否则 NULL 不去重）")
        if need_sample_date:
            plan.append("新增    index_constituents.sample_date  ← 源侧样本日期（与快照日区分，供核对调样生效时点）")

        print("=" * 78)
        print(f"{'变更计划' if not args.apply else '执行结果'}（{len(plan)} 项）")
        print("=" * 78)
        for p in plan:
            print("  " + p)
        print()

        if not args.apply:
            print("[只读预览] 加 --apply 才会执行。")
            return 0

        # ================= 实际执行 =================
        for t, ddl in NEW_TABLES.items():
            cur.execute(ddl)
        print(f"✅ 新表就绪 {len(NEW_TABLES)} 张")

        if not has_uk_date:
            cur.execute("SELECT trade_date FROM bond_profit_daily GROUP BY trade_date HAVING COUNT(*) > 1 LIMIT 1")
            if cur.fetchone():
                print("❌ bond_profit_daily 有重复 trade_date，跳过唯一索引")
            else:
                cur.execute("ALTER TABLE bond_profit_daily ADD UNIQUE KEY uk_trade_date (trade_date)")
                print("✅ bond_profit_daily 已加 UNIQUE KEY uk_trade_date")

        if need_sample_date:
            cur.execute("ALTER TABLE index_constituents ADD COLUMN sample_date DATE NULL "
                        "COMMENT '源侧样本日期（中证官网「日期」列；国证源无则 NULL）' AFTER trade_date")
            print("✅ index_constituents 已加 sample_date 列")
        if need_notnull:
            cur.execute("ALTER TABLE index_constituents MODIFY trade_date DATE NOT NULL COMMENT '快照日（本轮采集日）'")
            print("✅ index_constituents.trade_date → NOT NULL")
        if need_uk_change:
            if "uk_index_stock" in cidx:
                cur.execute("ALTER TABLE index_constituents DROP INDEX uk_index_stock")
            cur.execute("ALTER TABLE index_constituents "
                        "ADD UNIQUE KEY uk_index_stock_date (index_code, stock_code, trade_date)")
            print("✅ index_constituents 唯一键 → uk_index_stock_date(index_code, stock_code, trade_date)")
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()")
        print(f"\n📦 全库表数：{cur.fetchone()[0]}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
