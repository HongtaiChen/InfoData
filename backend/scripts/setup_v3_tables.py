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
        pb DECIMAL(10,4) DEFAULT NULL COMMENT '市净率 PB（整体法；乐咕 legu_pb 源「市净率」）',
        pb_ew DECIMAL(10,4) DEFAULT NULL COMMENT '等权市净率（乐咕 legu_pb 源「等权市净率」）',
        pb_median DECIMAL(10,4) DEFAULT NULL COMMENT '市净率中位数（乐咕 legu_pb 源「市净率中位数」）',
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
    # ----------------------------------------------------------------
    # 投资环境模块（2026-10-18）—— 国内指数页的物化/配置/宏观源表
    # 见 docs/design/投资环境模块_设计方案_v0.2_2026-10-18.md §5.2 / §5.3
    # ----------------------------------------------------------------
    "env_threshold": """
    CREATE TABLE IF NOT EXISTS env_threshold (
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        index_name VARCHAR(50) DEFAULT NULL COMMENT '指数名称',
        drop_threshold DECIMAL(6,3) DEFAULT NULL COMMENT '大幅下跌阈值(%)：当日跌幅绝对值≥此值视为大幅下跌',
        threshold_source VARCHAR(20) NOT NULL DEFAULT 'suggested' COMMENT '阈值来源：manual(手定)/suggested(近3年95分位跌幅建议)',
        threshold_note VARCHAR(255) DEFAULT NULL COMMENT '阈值口径说明（如“按近3年日跌幅95分位统计的建议值，仅供参考”）',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (index_code)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '投资环境·国内指数大幅下跌阈值表（手定+统计建议，2026-10-18）'
    """,
    "env_index_tech_daily": """
    CREATE TABLE IF NOT EXISTS env_index_tech_daily (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        trade_date DATE NOT NULL COMMENT '交易日',
        close DECIMAL(12,4) DEFAULT NULL COMMENT '收盘价（后复权口径，跨日比值指标用）',
        change_pct DECIMAL(8,4) DEFAULT NULL COMMENT '当日涨跌幅(%)',
        -- 多窗口涨跌幅（12 窗口：半月/一月/一个半月/两月/两个半月/三月/六月/一年/本年/本季/本月/本周）
        win_halfm DECIMAL(10,4) DEFAULT NULL, win_1m DECIMAL(10,4) DEFAULT NULL,
        win_15m DECIMAL(10,4) DEFAULT NULL, win_2m DECIMAL(10,4) DEFAULT NULL,
        win_25m DECIMAL(10,4) DEFAULT NULL, win_3m DECIMAL(10,4) DEFAULT NULL,
        win_6m DECIMAL(10,4) DEFAULT NULL, win_1y DECIMAL(10,4) DEFAULT NULL,
        win_ytd DECIMAL(10,4) DEFAULT NULL, win_qtd DECIMAL(10,4) DEFAULT NULL,
        win_mtd DECIMAL(10,4) DEFAULT NULL, win_wtd DECIMAL(10,4) DEFAULT NULL,
        -- 均线位置
        ma60 DECIMAL(12,4) DEFAULT NULL, ma250 DECIMAL(12,4) DEFAULT NULL,
        close_over_ma60_pct DECIMAL(8,4) DEFAULT NULL COMMENT '收盘价相对MA60偏离(%)',
        close_over_ma250_pct DECIMAL(8,4) DEFAULT NULL COMMENT '收盘价相对MA250偏离(%)',
        -- 量价（20 日均量 ±30% 区间）
        vol_ma20 DECIMAL(22,2) DEFAULT NULL, vol_ratio_20 DECIMAL(8,4) DEFAULT NULL COMMENT '当日成交量/20日均量×100%',
        -- 技术指标
        macd_dif DECIMAL(12,4) DEFAULT NULL, macd_dea DECIMAL(12,4) DEFAULT NULL,
        macd_hist DECIMAL(12,4) DEFAULT NULL, rsi14 DECIMAL(8,4) DEFAULT NULL,
        boll_upper DECIMAL(12,4) DEFAULT NULL, boll_mid DECIMAL(12,4) DEFAULT NULL, boll_lower DECIMAL(12,4) DEFAULT NULL,
        -- 支撑阻力
        high_250 DECIMAL(12,4) DEFAULT NULL, low_250 DECIMAL(12,4) DEFAULT NULL,
        high_20 DECIMAL(12,4) DEFAULT NULL, low_20 DECIMAL(12,4) DEFAULT NULL,
        -- 回撤与波动
        max_dd_1y DECIMAL(8,4) DEFAULT NULL, max_dd_3y DECIMAL(8,4) DEFAULT NULL,
        max_dd_all DECIMAL(8,4) DEFAULT NULL, ann_vol_60 DECIMAL(8,4) DEFAULT NULL,
        down_vol_60 DECIMAL(8,4) DEFAULT NULL,
        -- Beta（对沪深300 60日滚动）
        beta_60 DECIMAL(8,4) DEFAULT NULL,
        -- 与沪深300相关性（120日）
        corr_300_120 DECIMAL(8,4) DEFAULT NULL,
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_code_date (index_code, trade_date),
        KEY idx_trade_date (trade_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '投资环境·国内指数技术面日频物化表（2026-10-18）'
    """,
    "env_fundamental_daily": """
    CREATE TABLE IF NOT EXISTS env_fundamental_daily (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        trade_date DATE NOT NULL COMMENT '交易日（估值部分）；盈利部分用最近财报期截面日',
        -- 估值（源 index_valuation_daily 的 legu/legu_pb 长历史）
        pe_ttm DECIMAL(12,4) DEFAULT NULL, pe_ttm_pct DECIMAL(8,4) DEFAULT NULL COMMENT 'PE-TTM 全史分位(0~100)',
        pb DECIMAL(10,4) DEFAULT NULL, pb_pct DECIMAL(8,4) DEFAULT NULL COMMENT 'PB 全史分位(0~100)',
        dividend_yield DECIMAL(10,4) DEFAULT NULL, dividend_yield_pct DECIMAL(8,4) DEFAULT NULL,
        erp DECIMAL(10,4) DEFAULT NULL COMMENT '股权风险溢价 = 1/PE - 10Y国债收益率',
        erp_pct DECIMAL(8,4) DEFAULT NULL,
        -- 盈利质量与成长（成分股聚合，近40季度趋势另见 env_fundamental_quarter）
        roe DECIMAL(10,4) DEFAULT NULL COMMENT '成分股聚合ROE(%)',
        net_profit_yoy DECIMAL(10,4) DEFAULT NULL COMMENT '成分股聚合净利润增速(%)',
        revenue_yoy DECIMAL(10,4) DEFAULT NULL COMMENT '成分股聚合营收增速(%)',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_code_date (index_code, trade_date),
        KEY idx_trade_date (trade_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '投资环境·国内指数基本面日频物化表（估值日频+盈利截面，2026-10-18）'
    """,
    "env_fundamental_quarter": """
    CREATE TABLE IF NOT EXISTS env_fundamental_quarter (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        report_date DATE NOT NULL COMMENT '报告期（如 2026-06-30）',
        roe DECIMAL(10,4) DEFAULT NULL COMMENT '成分股聚合ROE(%)（当季）',
        net_profit_yoy DECIMAL(10,4) DEFAULT NULL COMMENT '成分股聚合净利润同比增速(%)',
        revenue_yoy DECIMAL(10,4) DEFAULT NULL COMMENT '成分股聚合营收同比增速(%)',
        net_profit_sum DECIMAL(20,2) DEFAULT NULL COMMENT '成分股净利润合计（元）',
        revenue_sum DECIMAL(20,2) DEFAULT NULL COMMENT '成分股营收合计（元）',
        constituent_count INT DEFAULT NULL COMMENT '参与聚合的成分股数量',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_code_report (index_code, report_date),
        KEY idx_report_date (report_date)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '投资环境·国内指数成分股财务聚合季频表（近40季度趋势，2026-10-18）'
    """,
    "env_macro_monthly": """
    CREATE TABLE IF NOT EXISTS env_macro_monthly (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stat_month VARCHAR(10) NOT NULL COMMENT '统计月份（YYYY-MM）',
        indicator VARCHAR(20) NOT NULL COMMENT '指标：gdp/pmi/cpi/ppi',
        value DECIMAL(14,4) DEFAULT NULL COMMENT '读数',
        yoy DECIMAL(10,4) DEFAULT NULL COMMENT '同比(%)（GDP为累计同比，CPI/PPI为当月同比）',
        note VARCHAR(100) DEFAULT NULL COMMENT '口径备注（如 GDP 累计值单位亿元）',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_month_ind (stat_month, indicator),
        KEY idx_indicator (indicator)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
      COMMENT = '投资环境·宏观月度序列（GDP/PMI/CPI/PPI，akshare macro_china 系列，2026-10-18）'
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
