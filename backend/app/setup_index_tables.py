"""一次性脚本：创建 index_constituents / index_profile 表并 seed 21 指数释义。

用法：python setup_index_tables.py
释义与基准信息为人工整理的公开常识（source='seed'），基准信息无把握的留空。
⚠️ 维护约定：采集侧每新增一个要上市场页的指数，必须同步在此扩 seed ——
否则指数详情抽屉会显示「暂无指数释义」（2026-09-19 曾因此缺 8 个）。
后续如需官网自动抓取释义，可扩展为采集任务。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql

from app.db import get_db_config

DDL = [
    """
    CREATE TABLE IF NOT EXISTS index_constituents (
        id INT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        stock_code VARCHAR(10) NOT NULL COMMENT '成分股代码',
        stock_name VARCHAR(50) DEFAULT NULL COMMENT '成分股名称',
        weight DECIMAL(10, 4) DEFAULT NULL COMMENT '权重(%)，无权重源时为 NULL',
        trade_date DATE DEFAULT NULL COMMENT '样本日期',
        source VARCHAR(50) NOT NULL COMMENT 'csindex/cni/stock_info_members/derived_sh',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        data_source VARCHAR(100) DEFAULT NULL,
        UNIQUE KEY uk_index_stock (index_code, stock_code),
        KEY idx_index_code (index_code)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '指数成分股快照'
    """,
    """
    CREATE TABLE IF NOT EXISTS index_profile (
        id INT AUTO_INCREMENT PRIMARY KEY,
        index_code VARCHAR(20) NOT NULL COMMENT '指数代码',
        index_name VARCHAR(100) NOT NULL COMMENT '指数名称',
        description TEXT COMMENT '指数释义',
        base_date VARCHAR(20) DEFAULT NULL COMMENT '基日',
        base_point VARCHAR(20) DEFAULT NULL COMMENT '基点',
        source VARCHAR(50) NOT NULL DEFAULT 'seed' COMMENT 'seed/cnindex/csindex',
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_index_code (index_code)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '指数档案（释义/基准）'
    """,
]

# 释义为公开常识整理；base 信息仅收录高把握数据，无把握留空防误导
SEED = [
    ("000001", "上证指数",
     "上证综合指数由在上海证券交易所上市的全部股票计算而成，反映沪市整体走势，是发布最早、认知度最高的 A 股宽基指数。因覆盖全市场，成分股数量随沪市扩容持续增长。",
     "1990-12-19", "100"),
    ("000016", "上证50",
     "挑选上海证券市场规模大、流动性好的最具代表性的 50 只股票组成样本股，综合反映沪市最具影响力的一批龙头大盘股的整体表现。",
     "2003-12-31", "1000"),
    ("000300", "沪深300",
     "由沪深两市中规模大、流动性好的 300 只股票组成，覆盖 A 股核心资产，是境内股票市场最具代表性的跨市场基准指数，也是基金业绩比较的主要基准之一。",
     "2004-12-31", "1000"),
    ("000905", "中证500",
     "全部 A 股中剔除沪深300 指数样本及总市值排名靠前的股票后，选取市值排名靠前的 500 只股票组成，综合反映 A 股中盘股的整体表现。",
     "2004-12-31", "1000"),
    ("000852", "中证1000",
     "在中证800（沪深300+中证500）样本之外，选取规模偏小且流动性好的 1000 只证券组成，综合反映 A 股小市值公司的整体表现，与沪深300、中证500 形成规模梯度互补。",
     "2004-12-31", "1000"),
    ("000688", "科创50",
     "选取上海证券交易所科创板中市值大、流动性好的 50 只证券组成样本，反映科创板最具代表性的一批科技创新龙头企业的整体表现。",
     "2019-12-31", "1000"),
    ("000698", "科创100",
     "选取科创板中市值中等、流动性较好的 100 只证券组成，反映科创板中等市值证券的整体表现，与科创50 形成市值梯度互补。",
     None, None),
    ("399001", "深证成指",
     "由深圳证券市场中具有市场代表性的 500 只上市股票组成，反映深市整体走势，是深市旗舰型宽基指数。",
     "1994-07-20", "1000"),
    ("399006", "创业板指",
     "选取创业板市场中市值大、流动性好的 100 只股票组成样本，反映成长型创新创业企业在创业板的核心表现。",
     "2010-05-31", "1000"),
    ("399330", "深证100",
     "选取深圳市场中市值大、流动性好的 100 只股票组成，覆盖深市核心优质资产，是深市大盘蓝筹的代表指数。",
     None, None),
    ("399673", "创业板50",
     "从创业板指数样本股中选取 50 只流动性好的龙头股票组成，聚焦创业板中交易活跃的核心标的。",
     None, None),
    ("899050", "北证50",
     "选取北京证券市场中市值大、流动性好的 50 只证券组成样本，反映北交所最具代表性的核心企业的整体表现。",
     None, None),
    ("931775", "中证全指房地产指数",
     "从中证全指指数样本中选取房地产行业的证券组成，反映 A 股房地产行业上市公司的整体表现，属于行业主题指数。",
     None, None),
    # ---------- 2026-09-19 补齐：采集侧新增的 8 个指数（此前档案缺失 → 详情抽屉显示「暂无指数释义」） ----------
    ("000832", "中证转债",
     "由沪深市场剩余期限一年及以上、可交易的可转换公司债券组成样本，反映可转债市场整体价格走势，是转债类基金常用的业绩基准。",
     "2002-12-31", "100"),
    ("000922", "中证红利",
     "选取沪深市场现金股息率高、分红比较稳定、具有一定规模及流动性的 100 只证券作为样本，反映 A 股高股息证券的整体表现，属于策略加权（红利）指数。",
     "2004-12-31", "1000"),
    ("000985", "中证全指",
     "由剔除 ST、*ST 股票及上市时间不足 3 个月等证券后的沪深市场剩余证券构成，覆盖面接近全市场，是中证规模指数与行业指数的选样空间，反映 A 股全市场整体走势。",
     "2004-12-31", "1000"),
    ("399975", "证券公司",
     "选取证券与资本市场服务行业的上市公司证券构成样本，反映证券公司（券商）行业整体表现，是券商板块行情的代表性指数。",
     "2004-12-31", "1000"),
    ("399986", "中证银行",
     "从中证全指指数样本中选取银行业证券组成，反映 A 股银行行业上市公司的整体表现，属于行业主题指数。",
     "2004-12-31", "1000"),
    ("399997", "中证白酒",
     "从中证全指指数样本中选取白酒生产相关证券组成，反映 A 股白酒行业上市公司的整体表现，属于消费行业主题指数。",
     "2004-12-31", "1000"),
    ("932000", "中证2000",
     "在中证800、中证1000 指数样本之外，选取规模偏小且流动性较好的 2000 只证券组成，刻画 A 股更小市值证券的整体表现，是中证规模梯度（300→500→1000→2000）的下沿延伸。",
     "2013-12-31", "1000"),
    ("980017", "国证芯片",
     "选取芯片产业相关上市公司证券构成样本，覆盖设计、制造、封装测试、设备与材料等环节，反映 A 股芯片产业的整体表现，属于科技主题指数。",
     None, None),
]


def main() -> None:
    cfg = get_db_config().to_dict()
    conn = pymysql.connect(**cfg)
    try:
        cur = conn.cursor()
        for ddl in DDL:
            cur.execute(ddl)
        conn.commit()
        cur.executemany(
            """
            INSERT INTO index_profile (index_code, index_name, description, base_date, base_point, source)
            VALUES (%s, %s, %s, %s, %s, 'seed')
            ON DUPLICATE KEY UPDATE
                index_name = VALUES(index_name),
                description = VALUES(description),
                base_date = VALUES(base_date),
                base_point = VALUES(base_point),
                source = 'seed'
            """,
            SEED,
        )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM index_profile")
        print("index_profile seeded:", cur.fetchone()[0])
        cur.execute("SHOW TABLES LIKE 'index_%'")
        print("tables:", [r[0] for r in cur.fetchall()])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
