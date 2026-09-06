#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集器共享工具（模块名带下划线前缀，避免与任务模块混淆）

with_steps：把模块级 RUN_STEPS 模板与当轮实录合并进 result["run_detail"]，
供前端「数据流·整链拓扑」做 模板 + 实录 对照；run_task 结束时整包写入 task_runs.run_detail。
"""


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
