#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
轻量 .env 加载器 —— 让「复制 .env.example 为 .env」真的生效。

为什么需要（2026-10-17 可移植性审计）
================================================================================
在此之前后端**只读 `os.getenv`（进程环境变量）**，从来不读 `.env` 文件；
而 `.env.example` 与 `backend/.env.example` 的注释却写着「复制为 .env 并填入真实值」。
后果是一个**静默错配**：换机器的同学照着示例建了 `.env`、改了口令，
启动时却仍走代码里的默认值 `root/root` —— 要么连不上库（报错，还好），
要么**恰好连上了另一个同名的默认库**（不报错，最坏）。

设计取舍
================================================================================
1. **不引入 python-dotenv**：整个文件不到 80 行，标准库足够；
   少一个依赖，就少一个「新机器 pip install 失败」的可能。
   若项目以后真的装了 python-dotenv，本模块行为与其保持一致（不覆盖已有环境变量）。
2. **不覆盖已存在的环境变量**：进程环境变量优先级最高（与 Docker、systemd、
   以及 `INFO_DATA_PYTHON=... bash start-dev.sh` 这类临时覆盖的直觉一致）。
3. **加载顺序：仓库根 `.env` → `backend/.env` → `backend/.env.local`**，
   后者依次覆盖前者，便于「根 .env 放通用配置、backend/.env 放本机调试覆盖、
   .env.local 放私有凭证（README 历史上就约定放这里）」。
4. **必须完全不生效时给出可执行提示**：文件存在却一个键都没解析出来、
   或全部被环境变量压过，都会打印 WARNING —— 不允许静默。
5. 关闭开关：`INFO_DATA_SKIP_DOTENV=1`（容器里通常已用 environment 注入，无需读文件）。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def _candidate_files() -> list[Path]:
    """按优先级从低到高返回候选文件（后加载的覆盖先加载的）。

    顺序有意如此：
        <repo>/.env          → 通用配置（应提交模板、实际文件不入库）
        <repo>/backend/.env  → 本机覆盖
        <repo>/backend/.env.local → 最高优先级的本机私有凭证
                                    （README 历史上就让人把告警凭证放这里，保持兼容）
    """
    here = Path(__file__).resolve()           # <repo>/backend/app/env.py
    backend = here.parent.parent              # <repo>/backend
    root = backend.parent                     # <repo>
    return [root / ".env", backend / ".env", backend / ".env.local"]


def _parse(text: str) -> dict[str, str]:
    """解析 KEY=VALUE。支持 # 注释、export 前缀、单/双引号、行内 # 注释。"""
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]          # 引号包裹：原样取值，不截断 # 之后
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()   # 未加引号：行内注释
        result[key] = value
    return result


def load_env_files() -> dict[str, str]:
    """加载 .env 文件到 os.environ（不覆盖已有值）。返回**实际生效**的键值。"""
    if os.getenv("INFO_DATA_SKIP_DOTENV", "").strip().lower() in _TRUTHY:
        logger.info("已按 INFO_DATA_SKIP_DOTENV 跳过 .env 加载")
        return {}

    applied: dict[str, str] = {}
    seen_any_file = False

    for path in _candidate_files():
        if not path.is_file():
            continue
        seen_any_file = True
        try:
            pairs = _parse(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            logger.warning(f"⚠️ 读取 {path} 失败，已忽略：{exc}")
            continue

        if not pairs:
            logger.warning(
                f"⚠️ {path} 存在，但没有解析出任何 KEY=VALUE —— "
                f"检查是否写成了 ``KEY: VALUE``（应为等号）或整行被注释。"
            )
            continue

        kept = []
        for key, value in pairs.items():
            if key in os.environ:
                # 环境变量优先：记录但不覆盖
                kept.append(key)
                continue
            os.environ[key] = value
            applied[key] = value

        if applied:
            logger.info(
                f"📄 已从 {path} 载入 {len(applied)} 个环境变量"
                + (f"（{len(kept)} 个已由进程环境变量提供，未覆盖：{', '.join(sorted(kept)[:5])}"
                   f"{' …' if len(kept) > 5 else ''}）" if kept else "")
            )
        elif kept:
            logger.warning(
                f"⚠️ {path} 存在，但其中 {len(kept)} 个变量全部已被进程环境变量覆盖 —— "
                f".env 里的值**没有生效**。若非有意，请清理 shell 里的同名 export。"
            )

    if not seen_any_file and not applied:
        logger.info("未发现 .env 文件，全部使用默认值 / 进程环境变量")
    return applied
