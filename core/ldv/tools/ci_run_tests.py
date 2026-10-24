# -*- coding: utf-8 -*-
"""CI 用：跑 `run_tests`，**把每条 `[红]` 转成 GitHub annotation**。

为什么需要：`run_tests` 的失败详情只在 job 日志里，而日志下载要 token。
`::error::` 行会进 **check-run annotations**（公开可读）⇒ 失败内容能从 API 取到。

用法（CI 里）：python ldv/tools/ci_run_tests.py
退出码与 `run_tests` 一致。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from ._paths import REPO



def main() -> int:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([sys.executable, "-m", "ldv.tests.run_tests"],
                       cwd=str(REPO), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print(p.stdout, end="")
    if p.stderr:
        print(p.stderr, file=sys.stderr, end="")

    # 每条失败 → 一条 annotation（最多 20 条，GitHub 有上限）
    fails = [ln.strip() for ln in p.stdout.splitlines() if ln.startswith("  [红]")]
    for ln in fails[:20]:
        # ::error:: 里不能有换行；长度截到 4000（annotation 上限 64KB，够用）
        print(f"::error::{ln[:4000]}")
    skipped = [ln.strip() for ln in p.stdout.splitlines()
               if "（**跳过**" in ln]
    for ln in skipped[:10]:
        print(f"::warning::{ln[:4000]}")
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
