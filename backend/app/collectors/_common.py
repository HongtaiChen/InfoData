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

__all__ = ["with_steps", "CollectorTimeout", "call_with_timeout"]


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
