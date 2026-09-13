#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集器共享工具（模块名带下划线前缀，避免与任务模块混淆）

with_steps：把模块级 RUN_STEPS 模板与当轮实录合并进 result["run_detail"]，
供前端「数据流·整链拓扑」做 模板 + 实录 对照；run_task 结束时整包写入 task_runs.run_detail。

call_with_timeout：给 akshare 等「裸 requests 调用」加超时兜底。

  ⚠️ 背景（2026-09-13 事故）：akshare 各接口内部使用 `requests.get(...)` 且
  **不显式传 timeout**，一旦对端不响应（TCP 建连后迟迟不返回），调用会永久阻塞。
  实测 `margin_sync` 在深交所接口上卡死 17 分钟无任何日志、进程无法自愈，
  既拖垮任务又让调度器「同任务 running 保护」长期占位。

  故所有采集器的外部网络调用统一经本函数包裹：超时即抛 CollectorTimeout，
  由调用方按「跳过该日 / 记 error / 下轮自动补」的容错策略处理，绝不无限等待。
"""
import threading
from datetime import date, datetime, timedelta

__all__ = ["with_steps", "CollectorTimeout", "call_with_timeout",
           "is_a_share", "latest_expected_report_period",
           "load_universe", "load_refresh_targets"]

# 排序哨兵：让「从未采集过」（NULL）的股票排在最前
_MIN_TS = datetime(1900, 1, 1)
_MIN_DATE = date(1900, 1, 1)


def _as_dt(v):
    """date / datetime 统一成 datetime（date 补 00:00:00），便于跨类型比较。

    必要性：update_time 是 TIMESTAMP（pymysql 返回 datetime），而 cutoff 由 date 减天数
    得到 date —— 直接 `datetime < date` 会抛 TypeError（2026-09-13 实测踩坑）。
    """
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time())
    return v


class CollectorTimeout(TimeoutError):
    """采集器外部调用超时（akshare 底层 requests 无超时，需外层兜底）"""


def call_with_timeout(fn, timeout: float, *args, **kwargs):
    """在守护线程中执行 fn(*args, **kwargs)，超过 timeout 秒仍未返回则抛 CollectorTimeout。

    说明：
    - 用守护线程而非 signal.alarm，兼容 Windows（无 SIGALRM）且可在非主线程调用；
    - 超时后不阻塞等待——底层 socket 会随进程退出回收；守护线程不阻止进程结束；
    - 原调用抛出的异常原样透传（含 requests/akshare 的各类异常）。
    """
    if timeout is None or timeout <= 0:
        return fn(*args, **kwargs)

    box: dict = {}
    done = threading.Event()

    def _target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 - 原样透传给调用方
            box["error"] = e
        finally:
            done.set()

    th = threading.Thread(target=_target, daemon=True, name="collector-io")
    th.start()
    if not done.wait(timeout):
        name = getattr(fn, "__name__", str(fn))
        raise CollectorTimeout(f"{name} 调用超时（>{timeout:g}s），已放弃等待")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def with_steps(result: dict, run_steps: list[dict], values: dict[int, str]) -> dict:
    """result 附加 run_detail.run_steps（每步含模板 name/params + 当轮实录 value）。

    约定：run_steps 为模块级 RUN_STEPS（[{no,name,params}]）；
    values 为 {步骤号: 当轮实测文本}，缺失步骤 value 为 None（前端不渲染右侧 chip）。
    """
    result["run_detail"] = {
        "run_steps": [
            {
                "no": s["no"],
                "name": s["name"],
                "params": s.get("params"),
                "value": values.get(s["no"]),
            }
            for s in run_steps
        ]
    }
    return result


# ---------------------------------------------------------------------------
# 逐股型采集器共享工具（2026-09-13 第 4 批落地时抽出）
#
# 背景：`stock_financial_abstract_ths` / `stock_shares` / `stock_capital_flow`
# 三张表都是「一个接口只给一只股票」的逐股型源，单轮需 5000+ 次请求，
# 故统一约定：候选池自愈式合并 + 只补滞后股票 + 限流 + 分批提交 + 单只超时不中断。
# ---------------------------------------------------------------------------

# 非 A 股代码前缀：B 股（沪 900/901、深 200/201）、北交所（43/82/83/87/88/89/92）
_B_SHARE_PREFIX = ("200", "201", "900", "901")
_BSE_PREFIX = ("43", "82", "83", "87", "88", "89", "92")


def is_a_share(code, include_bse: bool = True) -> bool:
    """是否纳入逐股采集的 A 股候选（默认含北交所，排除 B 股）"""
    if code is None:
        return False
    c = str(code).strip()
    if len(c) != 6 or not c.isdigit():
        return False
    if c[:3] in _B_SHARE_PREFIX:
        return False
    if not include_bse and c[:2] in _BSE_PREFIX:
        return False
    return True


def latest_expected_report_period(today: date | None = None) -> date:
    """按法定披露截止日推算「此刻应已全部披露」的最近报告期。

    披露截止：一季报 04-30、中报 08-31、三季报 10-31、年报次年 04-30。
    故：≥10-31 → 三季报(09-30)；≥08-31 → 中报(06-30)；≥04-30 → 一季报(03-31)；
        其余 → 上一年年报(12-31)。

    用途：解决「用全局 MAX(报告期) 判滞后」在跨期时会失灵的问题 ——
    若本地表整体停在旧期，全局 MAX 也停在旧期，滞后判定会得到 0 只、永远不补。
    与全局 MAX 取较大者后即可：既能在整体落后时触发全量，也能在完成后再收敛。
    """
    d = today or date.today()
    y = d.year
    md = (d.month, d.day)
    if md >= (10, 31):
        return date(y, 9, 30)
    if md >= (8, 31):
        return date(y, 6, 30)
    if md >= (4, 30):
        return date(y, 3, 31)
    return date(y - 1, 12, 31)


def load_universe(conn, table: str, include_bse: bool = True,
                  max_stocks: int = 0) -> list[tuple[str, str | None]]:
    """逐股型采集器的候选池 = 本地该表已有股票 ∪ stock_info 在册 A 股。

    取并集而非只用 stock_info 的理由：这些表的历史覆盖（如 5,739 只）比当前在册
    （5,134 只）更广，含已退市/北交所标的；只用在册名单会让历史缺口永远补不上。
    → 返回 [(stock_code, stock_name|None)]，按代码排序。
    """
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stock_code FROM `%s`" % table)
        codes = {str(r[0]) for r in cur.fetchall() if r[0] is not None}
        cur.execute("SELECT stock_code, short_name FROM stock_info WHERE list_status = '上市'")
        names: dict[str, str | None] = {}
        for c, n in cur.fetchall():
            if c is None:
                continue
            c = str(c)
            names[c] = n
            codes.add(c)
    out = [(c, names.get(c)) for c in sorted(codes) if is_a_share(c, include_bse)]
    return out[:max_stocks] if max_stocks and max_stocks > 0 else out


def load_refresh_targets(conn, table: str, *, data_col: str | None = None,
                         floor_date=None, refresh_days: int = 0,
                         name_col: str | None = None,
                         include_bse: bool = True, today=None,
                         max_stocks: int = 0,
                         skip_delisted: bool = False) -> tuple[list[tuple[str, str | None]], int]:
    """逐股型采集器的「待刷新名单」：候选池 = 本地已有股票 ∪ 在册 A 股，按最久未刷新排序。

    入选规则（满足任一即可；两者都不传则全选）：
      · data_col + floor_date —— 该股 MAX(data_col) < floor_date（数据滞后，如报告期/交易日）
      · refresh_days         —— MAX(update_time) < today - refresh_days（久未尝试刷新）
    规则二的意义：事件型表（如股本变动）的 data_col 常年不变，只靠规则一会误判为滞后，
    导致每轮都全量重扫；用 update_time 记录「上次成功触碰时间」才能真正收敛。

    name_col：表内已有的名称列（如 stock_name / short_name），用于在 stock_info 里
    查不到时复用历史名称，避免退市股被写成「名称=代码」。

    skip_delisted（2026-09-13 新增）：排除 stock_info 中 list_status='退市' 的股票。
    必要性：退市股的源数据已冻结（不会再产生新事件/新报告期），但它们的
    MAX(update_time) / MAX(报告期) 永远停留在退市时刻 → 若按「久未刷新 / 数据滞后」
    排序，会**永久占据 max_stocks 名额榜首位**，使在册股票永远轮不到刷新。
    实测 stock_shares 本地 5,744 只中有 331 只退市股、stock_financial_abstract_ths
    5,854 只中同样有 331 只，占比 ~6%。逐股型增量采集器应传 True。

    返回 (targets, candidate_total)；targets 已按 (update_time 最早 → data_col 最早) 排序，
    使 max_stocks 截断时优先处理最陈旧的股票（与 stock_company_sync 的 max_count 思路一致）。
    """
    t = today or date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stock_code FROM `%s`" % table)
        local_codes = {str(r[0]) for r in cur.fetchall() if r[0] is not None}
        cur.execute("SELECT stock_code, short_name, list_status FROM stock_info")
        names: dict[str, str | None] = {}
        status: dict[str, str | None] = {}
        for c, n, s in cur.fetchall():
            if c is None:
                continue
            c = str(c)
            status[c] = s
            if s == "上市":
                names[c] = n
                local_codes.add(c)

        if name_col:
            cur.execute("SELECT stock_code, MAX(`%s`) AS nm FROM `%s` GROUP BY stock_code"
                        % (name_col, table))
            for c, n in cur.fetchall():
                if c is None or not n:
                    continue
                if not names.get(str(c)):
                    names[str(c)] = str(n)

        agg_col = "MAX(`%s`) AS dmax" % data_col if data_col else "NULL AS dmax"
        cur.execute("SELECT stock_code, %s, MAX(update_time) AS umax FROM `%s` GROUP BY stock_code"
                    % (agg_col, table))
        agg: dict[str, tuple] = {str(r[0]): (r[1], r[2]) for r in cur.fetchall() if r[0] is not None}

    floor_cmp = _as_dt(floor_date) if floor_date is not None else None
    cutoff = _as_dt(t - timedelta(days=refresh_days)) if refresh_days and refresh_days > 0 else None
    # 两条规则都未启用 → 全选（对应 full_sweep：不做滞后判定，整池重扫一遍）。
    # ⚠️ 2026-09-13 修正：原实现是「未命中就跳过」，与本文档「两者都不传则全选」相矛盾，
    # 导致 full_sweep=True 时选出 0 只、全量回补静默变成空跑（实测 1s 返回 0 条）。
    no_filter = floor_cmp is None and cutoff is None

    picked: list[tuple] = []
    eligible = [c for c in local_codes
                if is_a_share(c, include_bse)
                and not (skip_delisted and status.get(c) == "退市")]
    for code in eligible:
        dmax, umax = agg.get(code, (None, None))
        if not no_filter:
            hit = False
            if data_col and floor_cmp is not None and (dmax is None or _as_dt(dmax) < floor_cmp):
                hit = True
            if cutoff is not None and (umax is None or _as_dt(umax) < cutoff):
                hit = True
            if not hit:
                continue
        picked.append((dmax, umax, code, names.get(code)))

    # 最久未刷新优先；其次数据最旧优先（None 视为最旧）
    def _key(x):
        dmax, umax, code, _ = x
        return (umax is not None, umax or _MIN_TS, dmax is not None, dmax or _MIN_DATE, code)

    picked.sort(key=_key)
    total_candidates = len(eligible)
    out = [(code, name) for _, _, code, name in picked]
    return (out[:max_stocks] if max_stocks and max_stocks > 0 else out), total_candidates
