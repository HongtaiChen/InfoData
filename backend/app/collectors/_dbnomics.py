#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DBnomics 客户端（美国 / 欧元区货币总量主口径）

DBnomics（`api.db.nomics.world/v22`）是本域**唯一免注册、免 Key、本机直连可达**的
跨国统计聚合层：93 个供应商 / 47,062 数据集 / 17.2 亿条序列。
它在本域承担两件事：

  1. **欧元区**（`ECB` 供应商）—— ECB 直连三域实测全部阻断，这是**唯一免费通道**；
  2. **美国历史回填**（`FED` 供应商）—— 美联储官网当期页只含最近数周，
     官网无法回填 20 年历史，故历史必须走 DBnomics；官网做「当期交叉校验」。

⚠️ 四条已实测的工程约束（全部踩过，勿回退）：

  1. 🔴 **`-999999.0` 是缺失哨兵、不是数值**。FED/H41 的**已停报细分子项**（`_F01`~`_F12`）
     有 341/1241 期为哨兵，核心汇总序列 0 哨兵。哨兵是**合法数字**（不是 NULL），
     SQL 层过滤不掉，若直接入库会被当成 −10 亿级真实读数，并在同比里产生极端异常值。
     ⇒ 本模块**在解析层即剔除**并计数，调用方须把 `sentinel_count` 记入日志/DQ。

  2. `limit` 上限 **1000**（传 2000 → HTTP 400）。本模块走**单序列端点**
     （`/series/{provider}/{dataset}/{code}`），一次拿全该序列的观测值，天然绕过该限制。

  3. 端点慢：ECB 数据集列表 **58s**、FED/H41 全量 **30s**。故超时默认 **120s** + 重试 3 次。

  4. **`indexed_at`（元数据索引时间）≠ 数据末端日期**。实测 FED 索引 2026-07-26 而
     observations 已到 2026-09-23 ⇒ 增量水位**必须以观测值末端为准**。

  5. **「数据集存在」≠「目标序列存在」**。实测 `IMF/IFS` 有 193,984 条序列，
     但美/英/欧的 Broad Money `num_found=0`、日/巴/韩止于 2025 ⇒ 必须**实取目标序列**，
     拿「数据集列表」当证据会翻车。故本域不依赖 IMF/IFS 兜全球层（改用 BIS GLI）。
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from ._http import get_json

logger = logging.getLogger(__name__)

BASE = "https://api.db.nomics.world/v22"

# DBnomics 的缺失哨兵（合法数字，必须显式剔除，见模块头 ①）
SENTINEL = -999999.0


@dataclass
class SeriesData:
    """一条序列的观测值（哨兵已置 None 并计数）"""
    code: str
    name: str
    points: list[tuple[date, float | None]] = field(default_factory=list)
    sentinel_count: int = 0
    raw_len: int = 0
    error: str | None = None

    @property
    def d0(self):
        return str(self.points[0][0]) if self.points else None

    @property
    def d1(self):
        return str(self.points[-1][0]) if self.points else None

    def value_at(self, d: date):
        for dd, v in self.points:
            if dd == d:
                return v
        return None


_PERIOD_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def period_to_date(p: str) -> date | None:
    """DBnomics 的 period 字符串 → date。

    实测出现过的四种形态（必须全支持，否则会静默丢行）：
      · `2026-09-23`  → 具体日期（H41 周度，值是**周三水平日**）
      · `2026-08-31`  → 月末日（H6 月度，源侧给的就是月末）
      · `2026-07`     → 只有年月（ECB 月度）→ 取当月 1 日
      · `2026-Q1`     → 季度（BIS 走这条；H41 不用）→ 取**季末月最后一日**
      · `2026-W36`    → 周（H6 的 WM 腿）→ 取该年该周的周三（ISO 周）
    """
    if not p:
        return None
    s = str(p).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})-Q([1-4])", s, re.I)
    if m:
        mo, dy = _PERIOD_END[int(m.group(2))]
        return date(int(m.group(1)), mo, dy)
    m = re.fullmatch(r"(\d{4})-W(\d{1,2})", s, re.I)
    if m:
        try:
            # ISO 周 → 该周周一 + 2 天 = 周三（与 H41 的「周三水平」口径一致）
            return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 3)
        except ValueError:
            return None
    return None


def _flat_num(v) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:                    # NaN
        return None
    if x == SENTINEL:             # 缺失哨兵 → 置 None 并在调用方计数
        return None
    return x


def fetch_series(provider: str, dataset: str, code: str, *,
                 timeout: float = 120, attempts: int = 3) -> SeriesData:
    """取单条序列的全部观测值（哨兵已剔除并计数）。

    端点：`/series/{provider}/{dataset}/{code}?observations=1`
    ⇒ 一次请求拿全历史，不受 `limit=1000` 限制（见模块头 ②）。

    失败时**不抛异常**，而是返回带 `error` 的 SeriesData —— 让调用方决定
    「整轮失败」还是「该序列降级、其余照写」（本域多数场景是后者更好）。
    """
    url = f"{BASE}/series/{provider}/{dataset}/{code}?observations=1"
    try:
        raw = get_json(url, timeout=timeout, attempts=attempts)
    except Exception as e:                       # noqa: BLE001
        msg = f"{type(e).__name__}: {str(e)[:120]}"
        logger.warning("DBnomics 取数失败 %s/%s/%s → %s", provider, dataset, code, msg)
        return SeriesData(code=code, name="", error=msg)

    try:
        docs = raw["series"]["docs"]
    except (KeyError, TypeError):
        return SeriesData(code=code, name="", error=f"响应结构异常：{str(raw)[:120]}")
    if not docs:
        return SeriesData(code=code, name="", error="docs 为空")

    s = docs[0]
    periods = s.get("period") or []
    values = s.get("value") or []
    out = SeriesData(code=s.get("series_code") or code,
                     name=s.get("series_name") or "", raw_len=len(periods))
    n = min(len(periods), len(values))
    if n == 0:
        out.error = "period/value 为空"
        return out
    for i in range(n):
        d = period_to_date(periods[i])
        if d is None:
            continue
        v = values[i]
        if v is not None and float(v) == SENTINEL:
            out.sentinel_count += 1
        out.points.append((d, _flat_num(v)))
    out.points.sort(key=lambda x: x[0])
    return out


def to_map(sd: SeriesData) -> dict[date, float]:
    """只取非空值 → {date: value}（入库用）"""
    return {d: v for d, v in sd.points if v is not None}


def yoy_pct(points: list[tuple[date, float | None]], span_days: int = 365) -> dict[date, float]:
    """按「日期 − span_days 内最近一个有效同/前值」算同比增长（%）。

    ⚠️ 为什么不用「同月同日」严格匹配：各国发布日历与月末对齐不一致（实测 H6 是月末日、
    ECB 是当月 1 日），严格匹配会大面积配不上 → 同比全是 NULL。故用「目标日 ±15 天内
    最近一个有效值」做容差匹配，差 1~2 天对同比的影响在小数点后两位，可接受。
    """
    pts = [(d, v) for d, v in points if v is not None]
    if len(pts) < 2:
        return {}
    out: dict[date, float] = {}
    vals = sorted(pts, key=lambda x: x[0])
    for d, v in vals:
        target = d - timedelta(days=span_days)
        best = None
        for dd, vv in vals:
            if abs((dd - target).days) <= 15:
                if best is None or abs((dd - target).days) < abs((best[0] - target).days):
                    best = (dd, vv)
        if best and best[1]:
            out[d] = round((v / best[1] - 1) * 100, 2)
    return out
