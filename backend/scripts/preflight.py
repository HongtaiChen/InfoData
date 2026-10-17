#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
环境自检 —— 命令行入口（转发壳）

真正的实现放在 `app/preflight.py`（因为后端启动时也要 import 它）。
本文件只做一件事：把 `backend/` 加进 sys.path，然后调用 app.preflight.main()。

用法（在 backend/ 下）：
    python scripts/preflight.py              # 人读输出，有 fail 则退出码 1
    python -m app.preflight                  # 等价
    python scripts/preflight.py --json       # CI 用
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.preflight import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
