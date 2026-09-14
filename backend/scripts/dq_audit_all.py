"""全库数据质量体检审计（dq_audit_all）。

按业务表汇总 dq_rules 配置与各规则最近一次 dq_report 结果，输出六段：
  ① 规则覆盖矩阵（每表：规则数 / daily·weekly 分布 / 通过·提醒·失败·错误 数 / 最近体检时间）
  ② 未通过明细（status != pass 的规则 + 指标值 + 结论）
  ③ 覆盖缺口（无规则的表 / 有规则但从未产生结果 / 规则指向已不存在的表）
  ④ 近 10 个体检日的通过率
  ⑤ 调度与运行健康（2026-09-14 新增）—— 僵尸 running 记录 / 应跑未跑的任务
  ⑥ table_meta 调度文本漂移（2026-09-14 新增）—— flow_desc 里写的时刻与 task_config 实际 cron 不一致

配套说明（易踩的坑）：
- **规则状态取「每条规则最近一次 run_at」**，不是「最近一个 run_date」——同一天可能有手工补跑批次，
  按 run_date 聚合会把已被覆盖的历史失败算进来。
- **APScheduler 的 day_of_week 是 0=周一、6=周日**（与 Unix cron 的 0=周日相反）。
  判断「计划时段有没有跑」时不要按 Unix cron 语义读 task_config.cron；
  权威口径是 `GET /api/jobs/tasks` 返回的 next_run_time。本脚本的 ⑤⑥ 两段直接用
  `CronTrigger.from_crontab` + `cron_human()` 复算，与调度器同源，故结论权威。
- 业务表口径：排除 dq_* 四张质量表、系统表（table_meta/task_config/task_runs/user_prefs）、
  参考表（trade_calendar）与两张 _bak_ 备份表。

用法：
    python backend/scripts/dq_audit_all.py
"""
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql  # noqa: E402
from app.db import get_db_config  # noqa: E402
from app.scheduler import cron_human  # noqa: E402

SKIP = {"table_meta", "task_config", "task_runs", "user_prefs", "trade_calendar"}
SEV_ORDER = {"error": 0, "fail": 1, "warning": 2}
TZ = ZoneInfo("Asia/Shanghai")
# 僵尸 running 判定阈值（与 dq_rules.task_stale_running 的 hours 对齐）
STALE_HOURS = 3


def is_business(name: str) -> bool:
    if name.startswith("dq_") or name in SKIP:
        return False
    return "_bak_" not in name


def main() -> None:
    conn = pymysql.connect(**get_db_config().to_dict())
    cur = conn.cursor(pymysql.cursors.DictCursor)

    cur.execute("SHOW TABLES")
    key = list(cur.fetchone().keys())[0]
    cur.execute("SHOW TABLES")
    all_tables = sorted(r[key] for r in cur.fetchall())
    biz = [t for t in all_tables if is_business(t)]

    cur.execute(
        "SELECT id, rule_name, table_name, check_type, rule_group, severity, enabled, description "
        "FROM dq_rules ORDER BY table_name, rule_group"
    )
    rules = cur.fetchall()
    rules_by_table = defaultdict(list)
    for r in rules:
        rules_by_table[r["table_name"]].append(r)

    cur.execute(
        "SELECT r.rule_id, r.run_at, r.status, r.metric_value, r.message "
        "FROM dq_report r JOIN (SELECT rule_id, MAX(run_at) mx FROM dq_report GROUP BY rule_id) m "
        "  ON m.rule_id = r.rule_id AND m.mx = r.run_at"
    )
    latest = {r["rule_id"]: r for r in cur.fetchall()}

    print("=" * 118)
    print("① 规则覆盖矩阵（每条规则取最近一次 dq_report）")
    print("=" * 118)
    print(f"{'表名':<32}{'规则':>4}{'daily':>6}{'wk':>4}{'通过':>5}{'提醒':>5}{'失败':>5}{'错误':>5}  最近体检")
    print("-" * 118)
    totals = defaultdict(int)
    for t in biz:
        rs = rules_by_table.get(t, [])
        cnt = defaultdict(int)
        last_at = ""
        for r in rs:
            lr = latest.get(r["id"])
            if not lr:
                cnt["norun"] += 1
                continue
            cnt[lr["status"]] += 1
            last_at = max(last_at, str(lr["run_at"]))
        off = sum(1 for r in rs if not r["enabled"])
        note = "" if rs else "  ← 无规则"
        if off:
            note += f"  (停用{off})"
        if cnt["norun"]:
            note += f"  ({cnt['norun']}条未执行)"
        print(
            f"{t:<32}{len(rs):>4}"
            f"{sum(1 for r in rs if r['rule_group'] == 'daily'):>6}"
            f"{sum(1 for r in rs if r['rule_group'] == 'weekly'):>4}"
            f"{cnt['pass']:>5}{cnt['warning']:>5}{cnt['fail']:>5}{cnt['error']:>5}"
            f"  {last_at[:16]}{note}"
        )
        for k, v in cnt.items():
            totals[k] += v
        totals["rules"] += len(rs)
    print("-" * 118)
    print(
        f"{'合计':<32}{totals['rules']:>4}{'':>10}"
        f"{totals['pass']:>5}{totals['warning']:>5}{totals['fail']:>5}{totals['error']:>5}"
        f"  (未执行 {totals['norun']})"
    )

    print()
    print("=" * 118)
    print("② 未通过明细（status != pass）")
    print("=" * 118)
    bad = [(r, latest[r["id"]]) for r in rules if r["id"] in latest and latest[r["id"]]["status"] != "pass"]
    bad.sort(key=lambda x: (SEV_ORDER.get(x[1]["status"], 3), x[0]["table_name"]))
    if not bad:
        print("无")
    for rule, lr in bad:
        print(f"[{lr['status'].upper():<7}] {rule['table_name']:<30} {rule['rule_name']:<34} sev={rule['severity']}")
        print(f"          {str(lr['run_at'])[:16]}  指标={lr['metric_value']}")
        if lr["message"]:
            print(f"          {str(lr['message'])[:190]}")

    print()
    print("=" * 118)
    print("③ 覆盖缺口")
    print("=" * 118)
    print(f"有规则的表 {len(biz) - len([t for t in biz if not rules_by_table.get(t)])} / 业务表 {len(biz)}；"
          f"无任何规则：{[t for t in biz if not rules_by_table.get(t)] or '无'}")
    print(f"有规则但从未产生结果：{[r['rule_name'] for r in rules if r['id'] not in latest] or '无'}")
    print(f"停用规则：{[r['rule_name'] for r in rules if not r['enabled']] or '无'}")
    print(f"规则指向的表已不存在：{sorted({r['table_name'] for r in rules if r['table_name'] not in all_tables}) or '无'}")
    print(f"全部表 {len(all_tables)}；业务表 {len(biz)}；跳过 {sorted(set(all_tables) - set(biz))}")

    print()
    print("=" * 118)
    print("④ 近 10 个 run_date 计数（按 run_date 聚合，含同日多批次）")
    print("=" * 118)
    cur.execute(
        "SELECT run_date, COUNT(*) n, SUM(status='pass') p, SUM(status='warning') w, "
        "       SUM(status='fail') f, SUM(status='error') e "
        "FROM dq_report GROUP BY run_date ORDER BY run_date DESC LIMIT 10"
    )
    print(f"{'日期':<12}{'规则数':>7}{'通过':>7}{'提醒':>7}{'失败':>7}{'错误':>7}")
    for r in cur.fetchall():
        print(f"{str(r['run_date']):<12}{r['n']:>7}{int(r['p'] or 0):>7}{int(r['w'] or 0):>7}"
              f"{int(r['f'] or 0):>7}{int(r['e'] or 0):>7}")

    # ---------- ⑤ 调度与运行健康 ----------
    print()
    print("=" * 118)
    print("⑤ 调度与运行健康")
    print("=" * 118)
    cur.execute(
        "SELECT id, task_name, status, started_at, TIMESTAMPDIFF(MINUTE, started_at, NOW()) AS mins "
        "FROM task_runs WHERE status='running' ORDER BY started_at"
    )
    running = cur.fetchall()
    zombies = [r for r in running if (r["mins"] or 0) >= STALE_HOURS * 60]
    print(f"进行中 running {len(running)} 条；其中疑似僵尸（>{STALE_HOURS}h）{len(zombies)} 条")
    for r in zombies:
        print(f"  ⚠️ id={r['id']} {r['task_name']} 始于 {str(r['started_at'])[:16]}（已 {r['mins']} 分钟）")
    if zombies:
        print("  → 处置：重启后端（SchedulerManager.start() 会自动回收），或手工 python scripts/fix_stale_running.py --apply")

    cur.execute("SELECT task_name, enabled, cron FROM task_config ORDER BY task_name")
    cfg = cur.fetchall()
    cur.execute(
        "SELECT task_name, MAX(finished_at) mx FROM task_runs WHERE status='success' GROUP BY task_name"
    )
    last_ok = {r["task_name"]: r["mx"] for r in cur.fetchall()}
    now = datetime.now(TZ)
    now_naive = now.replace(tzinfo=None)
    missed = []
    for c in cfg:
        cron = (c["cron"] or "").strip()
        if not c["enabled"] or not cron or cron in ("手动", "none", "-"):
            continue
        try:
            from apscheduler.triggers.cron import CronTrigger

            trig = CronTrigger.from_crontab(cron, timezone=TZ)
            prev, cur_t = None, now - timedelta(days=45)
            fires = []
            while True:
                nxt = trig.get_next_fire_time(prev, cur_t)
                if nxt is None or nxt > now:
                    break
                fires.append(nxt)
                prev, cur_t = nxt, nxt + timedelta(seconds=1)
            if not fires:
                continue
            last_fire = fires[-1].replace(tzinfo=None)
            interval = (fires[-1] - fires[-2]) if len(fires) >= 2 else timedelta(days=999)
        except Exception:  # noqa: BLE001 - cron 解析失败单独提示
            print(f"  ⚠️ {c['task_name']}: cron 非法无法解析 → {cron}")
            continue
        # 高频任务（<2h）不参与「应跑未跑」判定，与 scheduler.CATCHUP_MIN_INTERVAL 对齐
        if interval < timedelta(hours=2):
            continue
        mx = last_ok.get(c["task_name"])
        if mx is None or mx < last_fire:
            missed.append((c["task_name"], cron, cron_human(cron), last_fire, mx))
    print(f"应跑未跑（最近一个 cron 班次晚于最近一次 success）{len(missed)} 个：")
    for name, cron, human, fire, mx in missed:
        print(f"  ⚠️ {name:<26} {cron:<16} {human:<20} 应收于 {fire:%Y-%m-%d %H:%M}；最近成功 {str(mx)[:16] if mx else '从未'}")

    # ---------- ⑥ table_meta 调度文本漂移 ----------
    print()
    print("=" * 118)
    print("⑥ table_meta 调度文本漂移（flow_desc 写的时刻 ≠ 实际 cron）")
    print("=" * 118)
    cur.execute("SELECT table_name, flow_desc, writers FROM table_meta")
    metas = cur.fetchall()
    cron_by_task = {c["task_name"]: (c["cron"] or "") for c in cfg}
    hhmm_re = re.compile(r"(\d{1,2}):(\d{2})")
    week_re = re.compile(r"周([一二三四五六日])")
    drift = []
    for m in metas:
        w = m["writers"]
        try:
            import json as _json

            w = _json.loads(w) if isinstance(w, str) else (w or [])
        except Exception:  # noqa: BLE001
            w = []
        if len(w) != 1:
            continue  # 仅检查单一写表任务，且需与 cron 一一对应
        cron = cron_by_task.get(w[0], "")
        if not cron or cron in ("手动", "none", "-"):
            continue
        parts = cron.split()
        if len(parts) != 5 or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        exp_hhmm = f"{int(parts[1]):02d}:{int(parts[0]):02d}"
        desc = m["flow_desc"] or ""
        found = {f"{int(a):02d}:{int(b):02d}" for a, b in hhmm_re.findall(desc)}
        if found and exp_hhmm not in found:
            drift.append((m["table_name"], w[0], sorted(found), exp_hhmm))
        # 星期一致性：文本里出现「周X」时应与 cron 的中文语义一致
        text_weeks = set(week_re.findall(desc))
        if text_weeks:
            human = cron_human(cron)
            cron_weeks = set(re.findall(r"周([一二三四五六日])", human))
            if not text_weeks <= cron_weeks:
                drift.append((m["table_name"], w[0], sorted(text_weeks), human))
    if not drift:
        print("无漂移（或该表未在 flow_desc 中写调度时刻）")
    for t, task, got, exp in drift:
        print(f"  ⚠️ {t:<28} writer={task:<24} flow_desc 写 {got}，实际应为 {exp}")

    conn.close()


if __name__ == "__main__":
    main()
