#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""InvestBuddy 外部告警通道（微信 / 企业微信）

为什么单独成模块
----------------
自愈闭环的第 7 层是**交代** —— 不只要落库、要在页面可见，还要让**人**知道。
本机服务是间歇运行的（实测近 38 天在线率 32%、最长离线 260 小时），
用户不会一直盯着质量页；「有没有需要我处理的事」必须主动送达。

为什么默认静默（三条纪律）
-------------------------
1. 未配置通道 → 只记日志，绝不报错（不能让「没配告警」变成故障）
2. 没有「需要人动手」的结论 → 不发（`problems` 模式，默认）——
   自愈功能自己制造告警疲劳就本末倒置（本轮就修过一个同类问题：后端未升级时
   前端 `loadConverge()` 会弹全局 404 toast）
3. 推送失败 → 只 warn，绝不影响收敛本身（告警是旁路，不是主链路）

配置（环境变量优先；也可写在 `backend/.env.local`，该文件已 gitignore）
--------------------------------------------------------------------
    INFO_DATA_NOTIFY_PROVIDER = wecom | pushplus | wxpusher | serverchan | none
    INFO_DATA_NOTIFY_ON       = problems（默认，仅异常）| always | never
    INFO_DATA_NOTIFY_TIMEOUT  = 10          # 秒

    # ── 企业微信群机器人（最简单：群设置 → 群机器人 → 添加 → 复制 Webhook）
    INFO_DATA_WECOM_WEBHOOK   = https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
    # ── PushPlus（个人微信可用：扫码登录 pushplus.plus 拿 token）
    INFO_DATA_PUSHPLUS_TOKEN  = xxxxxxxx
    # ── WxPusher（个人微信可用：扫码关注公众号拿 appToken / uid）
    INFO_DATA_WXPUSHER_TOKEN  = AT_xxxxxx
    INFO_DATA_WXPUSHER_UID    = UID_xxxxxx
    # ── Server 酱（个人微信可用：扫码登录 sct.ftqq.com 拿 SendKey）
    #    两种 SendKey 都支持：经典 `SCT…` → sctapi.ftqq.com；新版 `sctp<uid>t…` → <uid>.push.ft07.com
    INFO_DATA_SERVERCHAN_KEY  = SCT_xxxxxx

    # 额度与判据提示（选通道前先看）—— 免费额度差异很大，别按「都一样」选
    #   · Server酱      免费 **5 条/天**（订阅 1000 条/天）→ 重启频繁会撞额度
    #   · PushPlus      免费 200 条/天；同一内容 1 小时内限 3 条
    #   · WxPusher      单微信用户 500 条/天；**成功码是 code=1000**，不是 0/200
    #   · 企业微信机器人 20 条/分钟，无每日额度焦虑（但消息落在群，不是私聊）

⚠️ 安全：凭证只放 `.env.local` 或系统环境变量。**不要**写进 `.env.example`、
   `task_config.params`、计划任务命令行 —— 那些位置会进版本库或进程列表。
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/.env.local（app/notifier.py → parents[1] = backend/）
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env.local"
_ENV_CACHE: dict[str, str] | None = None

# 微信侧 markdown 有长度上限（企业微信 4096 字节）。中文按 UTF-8 3 字节算，
# 取 1200 字符 ≈ 3.6KB，留余量。
_MAX_CHARS = 1200


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

def _read_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析（零依赖）：KEY=VALUE，忽略 # 注释，剥掉成对引号"""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:                     # noqa: BLE001 - 文件不存在/无权限都算「没配」
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def _cfg(key: str, default: str = "") -> str:
    """取配置：环境变量优先，其次 backend/.env.local，最后默认值"""
    v = os.getenv(key)
    if v is not None and v.strip():
        return v.strip()
    global _ENV_CACHE
    if _ENV_CACHE is None:
        _ENV_CACHE = _read_env_file(_ENV_FILE)
    return (_ENV_CACHE.get(key) or default).strip()


def reload_config() -> None:
    """清空 .env.local 缓存（改完配置不用重启进程即可生效，供 /api 或测试调用）"""
    global _ENV_CACHE
    _ENV_CACHE = None


def current_provider() -> str:
    """当前生效的通道名（未配置返回 'none'）"""
    p = (_cfg("INFO_DATA_NOTIFY_PROVIDER") or "none").lower()
    return p if p else "none"


# ---------------------------------------------------------------------------
# 发送原语
# ---------------------------------------------------------------------------

def _clip(s: str, limit: int = _MAX_CHARS) -> str:
    return s if len(s) <= limit else s[:limit] + "…"


# 各家的「成功」状态码**并不一致** —— 统一按 0/200 判会把成功误判成失败：
#   · 企业微信  errcode = 0
#   · Server酱  code    = 0
#   · PushPlus  code    = 200
#   · WxPusher  code    = 1000   ← 实测文档口径，漏了它会「消息已发出但报失败」
_OK_CODES: dict[str, tuple[int, ...]] = {
    "wecom": (0,),
    "serverchan": (0,),
    "pushplus": (200,),
    "wxpusher": (1000,),
}
_DEFAULT_OK_CODES = (0, 200, 1000)


def _judge(provider: str, body: str) -> tuple[bool, str]:
    """把响应体判成 `(是否成功, 摘要)`。抽成纯函数是为了**可离线单测**（无需凭证）。"""
    try:
        j = json.loads(body)
    except Exception:                     # noqa: BLE001 - 非 JSON 一律按「发出去了」处理
        return True, body[:200]
    codes = _OK_CODES.get(provider, _DEFAULT_OK_CODES)
    for k in ("errcode", "code"):
        if k in j:
            return (j.get(k) in codes), json.dumps(j, ensure_ascii=False)[:200]
    return True, json.dumps(j, ensure_ascii=False)[:200]


def _post(url: str, payload: dict, timeout: float,
          provider: str = "") -> tuple[bool, str]:
    """POST JSON；显式绕开本机 HTTP 代理（国内 webhook 直连更稳）"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200]
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:                # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:200]
    return _judge(provider, body)


def _send_wecom(hook: str, title: str, content: str, timeout: float):
    md = _clip(f"**{title}**\n{content}")
    return _post(hook, {"msgtype": "markdown", "markdown": {"content": md}},
                 timeout, "wecom")


def _send_pushplus(token: str, title: str, content: str, timeout: float):
    return _post("https://www.pushplus.plus/send",
                 {"token": token, "title": title[:100], "content": _clip(content),
                  "template": "markdown"}, timeout, "pushplus")


def _send_wxpusher(app_token: str, uid: str, title: str, content: str, timeout: float):
    payload = {"appToken": app_token, "content": _clip(content),
               "summary": title[:100], "contentType": 3}   # 3 = markdown
    if uid:
        payload["uids"] = [uid]
    return _post("https://wxpusher.zjiecode.com/api/send/message", payload,
                 timeout, "wxpusher")


def _serverchan_url(key: str) -> tuple[str, str]:
    """Server酱有**两代端点**，必须按 SendKey 前缀分流（返回 `(url, 错误说明)`）

    · `sctp<uid>t…`（新版 / Server酱³）→ `https://<uid>.push.ft07.com/send/<key>.send`
    · 其余（经典 Turbo）              → `https://sctapi.ftqq.com/<key>.send`

    依据官方 `serverchan-demo/python/send.py`；把旧端点写死会让新版 key 直接失败。
    """
    k = (key or "").strip()
    if k.startswith("sctp"):
        m = re.match(r"sctp(\d+)t", k)
        if not m:
            return "", f"SendKey 形如 sctp… 但取不到 uid 段：{k[:10]}…"
        return f"https://{m.group(1)}.push.ft07.com/send/{k}.send", ""
    return f"https://sctapi.ftqq.com/{k}.send", ""


def _send_serverchan(key: str, title: str, content: str, timeout: float):
    url, err = _serverchan_url(key)
    if err:
        return False, err
    # Server酱的 title **不能含换行**（会报「包含特殊字符」），先压成单行
    return _post(url, {"title": " ".join(title.split())[:100], "desp": _clip(content)},
                 timeout, "serverchan")


def send(title: str, content: str) -> dict:
    """按配置推一条消息

    返回 `{"sent": bool, "provider": str, "detail": str}`。
    **任何异常都在内部消化** —— 调用方不需要 try（告警是旁路）。
    """
    provider = current_provider()
    if provider in ("", "none", "off", "disable"):
        logger.debug("外部告警未配置（INFO_DATA_NOTIFY_PROVIDER 未设），跳过推送")
        return {"sent": False, "provider": "none", "detail": "未配置通道"}

    try:
        timeout = float(_cfg("INFO_DATA_NOTIFY_TIMEOUT", "10") or 10)
    except ValueError:
        timeout = 10.0

    try:
        if provider == "wecom":
            hook = _cfg("INFO_DATA_WECOM_WEBHOOK")
            if not hook:
                return {"sent": False, "provider": provider,
                        "detail": "缺少 INFO_DATA_WECOM_WEBHOOK"}
            ok, detail = _send_wecom(hook, title, content, timeout)
        elif provider == "pushplus":
            token = _cfg("INFO_DATA_PUSHPLUS_TOKEN")
            if not token:
                return {"sent": False, "provider": provider,
                        "detail": "缺少 INFO_DATA_PUSHPLUS_TOKEN"}
            ok, detail = _send_pushplus(token, title, content, timeout)
        elif provider == "wxpusher":
            token = _cfg("INFO_DATA_WXPUSHER_TOKEN")
            if not token:
                return {"sent": False, "provider": provider,
                        "detail": "缺少 INFO_DATA_WXPUSHER_TOKEN"}
            ok, detail = _send_wxpusher(token, _cfg("INFO_DATA_WXPUSHER_UID"),
                                        title, content, timeout)
        elif provider == "serverchan":
            key = _cfg("INFO_DATA_SERVERCHAN_KEY")
            if not key:
                return {"sent": False, "provider": provider,
                        "detail": "缺少 INFO_DATA_SERVERCHAN_KEY"}
            ok, detail = _send_serverchan(key, title, content, timeout)
        else:
            return {"sent": False, "provider": provider,
                    "detail": f"未知 provider：{provider}（可选 wecom/pushplus/wxpusher/serverchan）"}
    except Exception as e:                # noqa: BLE001
        logger.warning(f"外部告警推送异常（provider={provider}）: {e}")
        return {"sent": False, "provider": provider, "detail": str(e)[:200]}

    if ok:
        logger.info(f"📮 外部告警已推送（{provider}）")
    else:
        logger.warning(f"📮 外部告警推送失败（{provider}）：{detail}")
    return {"sent": bool(ok), "provider": provider, "detail": detail}


def notify_test() -> dict:
    """配置自检：推一条测试消息，返回结果（给用户确认通道是否打通）"""
    provider = current_provider()
    if provider in ("", "none"):
        return {"sent": False, "provider": "none",
                "detail": "未配置通道：请在 backend/.env.local 设置 "
                          "INFO_DATA_NOTIFY_PROVIDER 及对应凭证"}
    return send("🩺 InfoData 告警通道自检",
                "如果你收到这条消息，说明外部告警通道已打通。\n"
                "此后只在**需要你动手**时才会收到推送。")


# ---------------------------------------------------------------------------
# 收敛报告 → 人话消息
# ---------------------------------------------------------------------------

def build_converge_message(report: dict) -> tuple[str, str] | None:
    """把收敛报告转成 `(标题, 正文)`；返回 `None` 表示**不值得打扰人**

    判定「值得打扰」的依据（`INFO_DATA_NOTIFY_ON`）：
      · problems（默认）：有「尝试修复但没修好」或「需人工」→ 推；只是正常修好 → 不推
      · always：只要有动作就推（首次接通道时可临时打开，确认链路）
      · never ：一律不推
    **「待发布」不计入打扰依据** —— 那是正常现象（当日数据还没到发的时候），
      若把它算成问题，会每天推一条「concept 待发布」，几周就把人训练到无视告警。
    """
    if not report:
        return None
    mode = (_cfg("INFO_DATA_NOTIFY_ON", "problems") or "problems").lower()
    if mode in ("never", "off", "none"):
        return None

    repair = report.get("repair") or {}
    n_repaired = int(repair.get("repaired_tasks") or 0)
    n_tried_fail = int(report.get("still_failing_attempted_count") or 0)
    n_manual = len(report.get("manual_todo") or []) + len(report.get("unmapped") or [])
    n_deferred = len(repair.get("deferred") or [])
    n_human = n_manual + n_deferred
    n_pending = int(report.get("pending_count") or 0)
    n_healed = int(report.get("healed_count") or 0)

    if mode != "always" and n_human == 0 and n_tried_fail == 0:
        logger.debug(f"启动收敛无「需要人动手」的结论（修复 {n_repaired} / 待发布 {n_pending}），"
                     "按 problems 模式静默")
        return None

    ts = (report.get("converge_started_at") or "")[5:16]      # MM-DD HH:MM
    head_bits = [f"修复 {n_repaired}"]
    if n_healed:
        head_bits.append(f"复检通过 {n_healed}")
    if n_tried_fail:
        head_bits.append(f"未修好 {n_tried_fail}")
    if n_human:
        head_bits.append(f"待人工 {n_human}")
    title = f"🩺 启动收敛 {ts} · " + " / ".join(head_bits)

    lines = [
        f"**结果**：可自愈 {len(report.get('plan') or [])} 个 → 执行 {n_repaired} 个"
        f"；待发布 {n_pending} 条",
        f"**复检**：已修复 {n_healed} / 尝试未修好 {n_tried_fail} / "
        f"未尝试 {int(report.get('still_failing_unattempted_count') or 0)}",
    ]
    if n_human:
        lines.append(f"\n**需人工 {n_human} 项**")
        for it in (report.get("manual_todo") or [])[:5]:
            lines.append(f"· {it.get('table_name')}.{it.get('rule_name')}：{it.get('why')}")
        for it in (report.get("unmapped") or [])[:3]:
            lines.append(f"· {it.get('table_name')}.{it.get('rule_name')}：{it.get('why')}")
        for it in (repair.get("deferred") or [])[:3]:
            lines.append(f"· {it.get('task')}：{it.get('why')}")
    if n_tried_fail:
        lines.append("\n**尝试修复但复检仍未通过**（多半不是「数据缺失」型问题）")
        for x in (report.get("verify", {}).get("still_failing") or []):
            if x.get("attempted"):
                lines.append(f"· {x.get('table_name')}.{x.get('rule_name')}：{x.get('message')}")
    if n_pending:
        pend = report.get("pending_todo") or []
        names = "、".join(sorted({str(p.get("task")) for p in pend})[:6])
        lines.append(f"\n（另有 {n_pending} 条属「待发布」，等它们自己的班次"
                     + (f"：{names}" if names else "") + "）")

    return title, "\n".join(lines)


def notify_converge(report: dict) -> dict:
    """收敛完成后调用：按需推送到已配置的通道（未配置/不值得推则静默）"""
    msg = build_converge_message(report)
    if msg is None:
        return {"sent": False, "provider": current_provider(), "detail": "无需推送"}
    title, content = msg
    return send(title, content)
