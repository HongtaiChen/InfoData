#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy table_meta 元数据播种脚本（数据中心「数据流卡」数据源）

背景（2026-09-06 用户需求：数据中心按表联动展示 数据源/清洗口径/维护任务调度/质量）：
- table_meta 把散落在「数据体系设计规范 v1.5」与各采集器 docstring 的表级数据流元数据结构化入库；
- DataView 右侧「数据流」折叠卡 = table_meta(源/口径/writers) + task_config/cron + task_runs(最近运行) + dq/table-status；
- 内容基线同步自设计文档 §2 表清单 / §3.2 任务清单 / §6 健康结论（2026-09-05 实测快照）。

字段：
- category    域分类（行情/指数/概念/日历/资讯/资料/基金/系统/质量/静态…）
- source_desc 数据源构成（分号分隔，前端拆标签展示）
- flow_desc   数据流与清洗口径说明（写表方式/频率/降级/护栏）
- writers     维护该表的采集任务名数组（对应 task_config.task_name；无任务=[]）
- note        备注（停更/备份/缺口状态等）

幂等：CREATE TABLE IF NOT EXISTS + 按主键 UPSERT，可安全重复执行。
"""
import logging
import sys

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])  # backend 根

import pymysql

from app.db import get_db_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS table_meta (
  table_name varchar(64) NOT NULL COMMENT '目标表名',
  category varchar(20) NULL COMMENT '域分类（行情/指数/概念/日历/资讯/资料/基金/系统/质量等）',
  source_desc varchar(255) NULL COMMENT '数据源构成（分号分隔，前端拆标签）',
  flow_desc varchar(1000) NULL COMMENT '数据流与清洗口径说明',
  writers json NULL COMMENT '维护该表的采集任务名数组（对应 task_config.task_name，无任务为空）',
  note varchar(255) NULL COMMENT '备注（停更/备份/缺口）',
  update_time timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '元数据维护时间',
  PRIMARY KEY (table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据中心表级元数据：数据源/清洗口径/维护任务（数据流卡数据源）'
"""

# (table_name, category, source_desc, flow_desc, writers, note)
_META: list[tuple[str, str, str, str, list[str], str]] = [
    # ---- 健康自更新 12 张业务表 + dq_report ----
    ("stock_market_daily", "行情",
     "东财;腾讯;新浪;Tushare",
     "股票日线历史（1990-12-19~）。stock_daily_incr 按每只股票本地最新日起增量补齐（前复权），四级降级：东财→腾讯→新浪→Tushare；退市/停牌股失败不阻断全量；每工作日 19:00。",
     ["stock_daily_incr"], ""),
    ("stock_market_current", "行情",
     "本地聚合（无外部源）",
     "每日行情快照：由 stock_market_daily 最新交易日聚合出全市场当日行情（TRUNCATE+全量重建 ~5,400 行，<1,000 行护栏拒写）；每工作日 18:30。",
     ["market_current_sync"], ""),
    ("dc_index_market", "指数",
     "东财;腾讯",
     "指数日线（13 个主流指数）：东财主源含全字段，腾讯降级仅 OHLCV+自算涨跌（amount/turnover 为空）；每工作日 18:30 增量。",
     ["index_market_sync"], "931775 需生产东财首跑"),
    ("bond_profit_daily", "债券",
     "中债;美债(akshare bond_zh_us_rate)",
     "中美国债 2/5/10/30y 收益率 + spread 日线，自本地 max 日起增量补齐；每工作日 08:00。",
     ["bond_profit_sync"], ""),
    ("ths_concept_info", "概念",
     "同花顺",
     "概念板块清单：concept_market_sync 增量中发现新概念自动注册（309xxx）；与概念指数同轮（每工作日 20:00）。",
     ["concept_market_sync"], ""),
    ("ths_concept_market", "概念",
     "同花顺",
     "概念指数日线（2018-04-12~，398 概念）：每工作日 20:00 增量；失败单概念不阻断整轮。",
     ["concept_market_sync"], ""),
    ("ths_stock_concepts", "概念",
     "同花顺;新浪",
     "股票↔概念关系表：逐概念重建（DELETE+INSERT 小事务）；同花顺反爬/失败自动切新浪同名概念，双失败保留旧数据；每周一 02:00。",
     ["concept_sync"], "生产同花顺通道首跑后转健康"),
    ("news", "资讯",
     "东财快讯;财联社",
     "财经资讯：每 30 分钟双源拉取，财联社失败仅告警；INSERT IGNORE 兜底；保留窗口有限（观察滚动清理）。",
     ["news_fetch"], ""),
    ("trade_calendar", "日历",
     "新浪交易日历(akshare)",
     "A 股交易日历：补齐当年+次年；每周日 02:30。",
     ["trade_calendar_sync"], ""),
    ("finance_calendar", "日历",
     "东财 RPT_CPH_FECALENDAR;JY(历史)",
     "财经日历事件：每日拉未来 60 天窗口，窗口内 DELETE+重插幂等；EM-CAL 与历史 JY 源并存（data_source 区分）；每日 08:00。",
     ["finance_calendar_sync"], ""),
    ("fund_info", "基金",
     "东财基金列表(akshare)",
     "基金基本信息：每月 1 日全量重建 ~27,800 行；<20,000 行护栏拒绝覆盖。",
     ["fund_info_sync"], ""),
    ("stock_info", "资料",
     "东财全A名单;Baostock;巨潮资讯",
     "证券主表+公司档案宽表（34 列）：东财周更名单（短名/exchange，list_date 本地 MIN 推断）；Baostock 周更上市/退市状态+退市日（ipoDate 仅补空）；巨潮日更档案 24 列（列级 UPDATE）。data_source=EM;BAOSTOCK;CNINFO。",
     ["stock_info_sync", "stock_status_sync", "stock_company_sync"], "三源构成见列注释"),
    ("stock_info_ex", "资料",
     "东财全A名单",
     "股票信息扩展：随 stock_info 周更同步全市场名单；人工 is_gxlstock（高股息）标记保留。",
     ["stock_info_sync"], ""),
    ("finance_concept_analysis", "AI",
     "豆包方舟(ARK)",
     "AI 概念分析：分析投资日历事件→关联概念（评分 1-10）；无 ARK_API_KEY 时降级写占位结果；手动触发（当前 enabled=0）。",
     ["ai_concept_analysis"], "当前为占位降级态"),
    ("dq_report", "质量",
     "自产(data_quality_check)",
     "数据质量体检结果快照：每日 20:30 读 dq_rules（31 条规则）逐条执行写入；大表限定最新时间切片秒级完成；同轮运行中保护。",
     ["data_quality_check"], ""),
    # ---- 无采集任务（历史导入/静态/系统） ----
    ("stock_market_daily_ex", "行情",
     "历史导入",
     "日K线除权扩展表；2025-09-19 后无采集任务喂养（停更，数据保留）。",
     [], "停更"),
    ("stock_market_daily_bak_20250802", "行情",
     "备份",
     "2025-08-02 日线数据备份（一次性），只读归档勿写入。",
     [], "备份表待归档"),
    ("futures_spot_price", "商品",
     "历史导入",
     "现货/期货价格（136k 行）；2025-09-15 后无任务。",
     [], "停更"),
    ("stock_capital_flow", "资金",
     "历史导入",
     "日度资金流向（706k 行）；2025-09-19 后无任务。",
     [], "停更"),
    ("securities_margin", "资金",
     "历史导入",
     "融资融券余额（3.8k 行）；2025-09-18 后无任务。",
     [], "停更"),
    ("stock_financial_abstract_ths", "财务",
     "历史导入",
     "股票关键指标（322k 行）；2025-06-30 后无任务。",
     [], "停更"),
    ("ths_stock_dividend", "财务",
     "历史导入",
     "分红派息信息（138k 行）；2025-08-21 后无任务。",
     [], "停更"),
    ("stock_shares", "基本面",
     "历史导入",
     "股本信息（140k 行）；2025-08-16 后无任务。",
     [], "停更"),
    ("stock_industry_sw", "行业",
     "历史导入",
     "申万一二级行业分类（6.9k 行）；2025-08-16 后无任务。",
     [], "停更"),
    ("stock_jgdy_detail", "调研",
     "历史导入",
     "机构调研明细（22k 行）；2025-09-20 后无任务。",
     [], "停更"),
    ("stock_hold_by_fund", "基金",
     "历史导入",
     "基金重仓持股（113k 行）；2025-08-16 后无任务。",
     [], "停更"),
    # ---- 系统表 ----
    ("task_config", "系统",
     "手动维护;作业监控前端",
     "采集任务配置（15 任务：enabled/cron/params）；作业监控栏目可热改，调度器实时同步。",
     [], ""),
    ("task_runs", "系统",
     "自产(TaskRecorder)",
     "作业运行记录：每次执行 running→success/failed + 写入条数/错误信息；保留最近 ~100 条。",
     [], ""),
    ("dq_rules", "质量",
     "手动维护;seed_dq_rules",
     "数据质量规则配置（31 条 / 10 类检查器）；数据质量栏目可维护。",
     [], ""),
    ("stock_company_profile_bak_20260905", "资料",
     "备份(巨潮)",
     "v1.4 并入 stock_info 前的旧公司档案宽表（回滚点）；确认稳定后可 DROP。",
     [], "备份表待归档"),
]


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            logger.info("table_meta 表就绪")
            import json as _json

            cur.execute("SELECT table_name FROM table_meta")
            exist = {r[0] for r in cur.fetchall()}
            upserted = 0
            for name, cat, src, flow, writers, note in _META:
                cur.execute(
                    """INSERT INTO table_meta (table_name, category, source_desc, flow_desc, writers, note, update_time)
                       VALUES (%s, %s, %s, %s, %s, %s, NOW())
                       ON DUPLICATE KEY UPDATE category=VALUES(category), source_desc=VALUES(source_desc),
                         flow_desc=VALUES(flow_desc), writers=VALUES(writers), note=VALUES(note),
                         update_time=NOW()""",
                    (name, cat, src, flow, _json.dumps(writers, ensure_ascii=False), note),
                )
                upserted += cur.rowcount
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM table_meta")
            total = cur.fetchone()[0]
            print(f"播种完成：upsert {upserted} 行（先有 {len(exist)} → 现有 {total} 行）")
            cur.execute("SELECT table_name, category FROM table_meta ORDER BY table_name")
            print("== table_meta 清单 ==")
            for r in cur.fetchall():
                print(f"  {r[0]:<36} [{r[1] or ''}]")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
