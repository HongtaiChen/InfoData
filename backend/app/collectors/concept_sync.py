#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InvestBuddy 概念成分股同步（ths_stock_concepts 表，周更）
- 通道 1（主源）：同花顺概念成分页（q.10jqka.com.cn gn/detail ajax，需 UA+session 预热 cookie）
  与 concept_market_sync 维护的 ths_concept_info（886xxx/309xxx）严格对齐，source='同花顺'
- 通道 2（降级）：新浪概念（stock_sector_spot + stock_sector_detail，按「概念名」匹配新浪 gn_ 板块）
  source='新浪'；仅覆盖新浪概念目录中的同名概念
- 策略：逐概念「DELETE 旧成分 → INSERT 新成分」小事务；两通道均失败则该概念跳过、保留库内旧数据
- 注意：同花顺对数据中心 IP 有风控，住宅/服务器 IP 下通道 1 可用性更高；失败自动走通道 2
"""
import io
import logging
import time

import pandas as pd
import pymysql
import requests
import akshare as ak

from ..db import get_db_config

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
THS_HOME = "https://q.10jqka.com.cn/"
THS_AJAX = "https://q.10jqka.com.cn/gn/detail/field/199112/order/desc/page/{page}/ajax/1/code/{code}"
THS_DETAIL = "https://q.10jqka.com.cn/gn/detail/code/{code}/"


def _th_session() -> requests.Session:
    """预热 session：访问同花顺首页/详情页以获取访问 cookie"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    try:
        s.get(THS_HOME, timeout=10)
    except Exception:
        pass
    return s


def _parse_ths_comps(html: str) -> list[tuple[str, str]]:
    """从同花顺成分页 HTML 解析 (代码, 名称) 列表；失败返回 []"""
    try:
        tables = pd.read_html(io.StringIO(html))
    except Exception:
        return []
    for tb in tables:
        if tb is None or tb.empty:
            continue
        cols = [str(c).strip() for c in tb.columns]
        if "代码" in cols and "名称" in cols:
            out = []
            for _, r in tb.iterrows():
                code = str(r["代码"]).strip()
                name = str(r["名称"]).strip()
                if code.isdigit() and len(code) == 6:
                    out.append((code, name))
            return out
    return []


class ConceptSyncCollector:
    """概念成分周更同步"""

    def __init__(self, max_pages: int = 30, sleep_s: float = 0.2):
        self.max_pages = max_pages
        self.sleep_s = sleep_s

    # ---------- 通道 1：同花顺 ----------
    def _fetch_ths(self, code: str, session: requests.Session) -> list[tuple[str, str]] | None:
        try:
            session.get(THS_DETAIL.format(code=code), timeout=12)  # 详情页换取 cookie
            comps: list[tuple[str, str]] = []
            for page in range(1, self.max_pages + 1):
                resp = session.get(
                    THS_AJAX.format(page=page, code=code), timeout=12,
                    headers={"Referer": THS_DETAIL.format(code=code)},
                )
                if resp.status_code != 200 or not resp.text.strip():
                    break
                batch = _parse_ths_comps(resp.text)
                if not batch:
                    break
                comps.extend(batch)
                if len(batch) < 10:  # 末页
                    break
                time.sleep(self.sleep_s)
            return comps
        except Exception as e:
            logger.warning(f"概念 {code} 同花顺通道异常: {str(e)[:80]}")
            return None

    # ---------- 通道 2：新浪 ----------
    def _sina_map(self) -> dict[str, str]:
        df = ak.stock_sector_spot(indicator="概念")
        m: dict[str, str] = {}
        for _, r in df.iterrows():
            label = str(r.get("label") or "").strip()
            name = str(r.get("板块") or "").strip()
            if label.startswith("gn_") and name and name not in m:
                m[name] = label
        return m

    def _fetch_sina(self, concept_name: str, sina_map: dict[str, str]) -> list[tuple[str, str]] | None:
        label = sina_map.get(concept_name)
        if not label:
            return None
        try:
            df = ak.stock_sector_detail(sector=label)
            if df is None or df.empty or "code" not in df.columns:
                return None
            out = []
            for _, r in df.iterrows():
                c = str(r["code"]).strip()
                n = str(r.get("name") or "").strip()
                if c.isdigit() and len(c) == 6:
                    out.append((c, n))
            return out
        except Exception as e:
            logger.warning(f"概念 {concept_name} 新浪通道异常: {str(e)[:80]}")
            return None

    # ---------- 主流程 ----------
    def run(self) -> dict:
        conn = pymysql.connect(**get_db_config().to_dict())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT index_code, concept_name FROM ths_concept_info ORDER BY concept_name")
                concepts = [(str(c), str(n)) for c, n in cur.fetchall()]
        except Exception:
            conn.close()
            raise

        if not concepts:
            conn.close()
            raise RuntimeError("ths_concept_info 为空，概念成分同步无对象")

        session = _th_session()
        sina_map: dict[str, str] | None = None
        ok = ths_n = sina_n = fail = 0
        failed_codes = []
        for code, name in concepts:
            comps = self._fetch_ths(code, session)
            source, data_source = "同花顺", "ths"
            if comps is None:  # 同花顺通道失败 → 新浪
                if sina_map is None:
                    sina_map = self._sina_map()
                comps = self._fetch_sina(name, sina_map)
                source, data_source = "新浪", "sina"
            if not comps:
                fail += 1
                failed_codes.append(f"{code}({name[:12]})")
                continue

            try:
                with conn.cursor() as cur:
                    # 兼容历史 'ADATA' 与新口径 'ths'/'sina'，全清该概念旧成分再重写
                    cur.execute(
                        "DELETE FROM ths_stock_concepts WHERE index_code=%s AND data_source IN ('ths','sina','ADATA')",
                        (code,),
                    )
                    cur.executemany(
                        "INSERT INTO ths_stock_concepts "
                        "(stock_code, short_name, index_code, concept_name, source, reason, update_time, data_source) "
                        "VALUES (%s, %s, %s, %s, %s, NULL, NOW(), %s)",
                        [(c_, n_, code, name, source, data_source) for c_, n_ in comps],
                    )
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.warning(f"概念 {code} 写库失败: {str(e)[:80]}")
                fail += 1
                failed_codes.append(f"{code}({name[:12]})")
                continue

            ok += 1
            if data_source == "ths":
                ths_n += 1
            else:
                sina_n += 1
            time.sleep(self.sleep_s)
        conn.close()

        msg = (f"概念成分同步：成功 {ok}/{len(concepts)} 概念"
               f"（同花顺 {ths_n} / 新浪 {sina_n}）"
               + (f"，失败 {fail}: {failed_codes[:5]}" if failed_codes else ""))
        logger.info(f"✅ {msg}")
        return {
            "records_written": ok,
            "error_count": fail,
            "errors": failed_codes[:20],
            "note": msg,
        }
