# -*- coding: utf-8 -*-
"""版本落点：**一处声明、多处同步、CI 守着**（2026-10-20）。

## 为什么有这个文件

版本号散在 5 处文档里。2026-10-20 的审计发现：近几版只改了两处
（`README` 首行 + `谓词推导与反思层.md`），另外 3 处**停在两版前** ——
于是同一份仓库里同时存在 v0.1.26 / v0.1.27 / v0.1.30 三种说法。

⇒ 把「哪几处算落点」写成**数据**，配两个动作：

    python -m ldv.tools.version_spots check      # 所有落点是否**彼此一致**（CI 也跑这条）
    python -m ldv.tools.version_spots bump 0.1.31  # 逐处替换（每处 assert 命中恰好 1 次）

⚠️ `check` 判的是「**彼此一致**」，不是「等于某个写死的数」——
   写死数字就得每版改脚本，那等于把漂移换个地方放。

⚠️ 落点里的 `日期` 也要一起改（`bump` 会写当天）。日期不一致同样是漂移。
"""
from __future__ import annotations

import datetime as _dt
import io
import re
import sys
from pathlib import Path
from ._paths import REPO


VER = r"(v\d+\.\d+\.\d+)"
DATE = r"(\d{4}-\d{2}-\d{2})"

#: **版本落点**：`(相对路径, 正则, 人话说明)`。正则里必须恰好一个 `VER` 组。
SPOTS: tuple[tuple[str, str, str], ...] = (
    ("README.md", rf"\*\*当前版本 {VER}\*\*", "README 首行「当前版本」"),
    ("README.md", rf"★★ \*\*本版（{VER}，", "README 状态行「本版」"),
    # 2026-10-22 补：「当前状态」标题行**也带版本**，上一版 bump 没覆盖它
    # ⇒ v0.1.33 已发版而这一行还停在 v0.1.32（同一个数两个落点的老形状）。
    ("README.md", rf"## 当前状态：{VER}", "README 当前状态标题"),
    ("docs/谓词推导与反思层.md", rf"\*\*版本\*\* {VER}", "谓词推导与反思层 版本行"),
    ("docs/分层方向视图-设计文档.md", rf"\*\*版本\*\* {VER}", "设计文档 版本行"),
    ("docs/它是怎么长出来的.md", rf"\| 状态 \| ★★ \*\*本版（{VER}，", "长文 状态行"),
    # 2026-10-20 补：这份文档的版本行停在 v0.1.28 已经漂了三版，就是因为**不在表里**。
    # ⇒ 加进来的意义不是「多一处要改」，是「漂了会被 check 抓到」。
    ("docs/服务化设计.md", rf"\*\*版本\*\* {VER}", "服务化设计 版本行"),
)

#: 带日期的落点（`bump` 时一起改）
DATED = {"README.md|★★", "docs/它是怎么长出来的.md|状态"}


def _read(rel: str) -> str:
    return io.open(REPO / rel, encoding="utf-8", newline="").read()


def scan() -> list[dict]:
    """读出每个落点当前的 `(版本, 日期)`。读不到 ⇒ 版本记 `None`（**不许静默跳过**）。"""
    out: list[dict] = []
    for rel, pat, label in SPOTS:
        text = _read(rel)
        m = re.search(pat + rf"{DATE}?", text)
        if m is None:
            out.append({"文件": rel, "落点": label, "版本": None, "日期": None})
            continue
        out.append({"文件": rel, "落点": label, "版本": m.group(1),
                    "日期": m.group(2) if m.lastindex and m.lastindex >= 2 else None})
    return out


def check() -> tuple[bool, list[dict]]:
    """所有落点**彼此一致**（版本 + 日期）。返回 `(是否一致, 明细)`。"""
    rows = scan()
    vers = {r["版本"] for r in rows}
    dates = {r["日期"] for r in rows if r["日期"]}
    return (len(vers) == 1 and None not in vers and len(dates) <= 1), rows


def bump(new: str, day: str | None = None) -> list[tuple[str, str]]:
    """逐处替换成 `new`（日期写 `day`，默认今天）。返回 `[(文件, 落点)]`。"""
    ver = new if new.startswith("v") else "v" + new
    day = day or _dt.date.today().isoformat()
    touched: list[tuple[str, str]] = []
    cache: dict[str, str] = {}
    for rel, pat, label in SPOTS:
        text = cache.get(rel) or _read(rel)
        full = pat + rf"{DATE}?"
        n = len(re.findall(full, text))
        assert n == 1, f"{rel} 的落点「{label}」命中 {n} 次（应恰好 1 次）—— 先修文档再 bump"
        m = re.search(full, text)
        assert m is not None
        dated = m.lastindex is not None and m.lastindex >= 2 and m.group(2)
        repl = m.group(0).replace(m.group(1), ver)
        if dated:
            repl = repl.replace(m.group(2), day)
        text = text[:m.start()] + repl + text[m.end():]
        cache[rel] = text
        touched.append((rel, label))
    for rel, text in cache.items():
        raw = io.open(REPO / rel, "rb").read()
        nl = "\r\n" if b"\r\n" in raw else "\n"
        # ★★ **先归一化再套目标行尾**（2026-10-20 修）。
        #
        #   原来直接 `text.replace("\n", nl)`：`_read` 用 `newline=""` 读，
        #   所以 CRLF 文件读回来**本身就带 `\r\n`** ⇒ 再 replace 一次就成
        #   `\r\r\n`（**双转换**）。实测：`bump` 一趟把三份文档写成 1671 / 1112 / 334
        #   行 `\r\r\n`，提交里出现 **3000 行行尾噪声**，而版本号只改了一个字符。
        #   ⇒ 先把 `\r\n` 与孤立 `\r` 都归成 `\n`，再按文件自己的行尾展开。
        norm = text.replace("\r\n", "\n").replace("\r", "\n")
        io.open(REPO / rel, "w", encoding="utf-8", newline="").write(
            norm.replace("\n", nl) if nl != "\n" else norm)
    return touched


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "check":
        ok, rows = check()
        for r in rows:
            print(f"  {r['版本'] or '**读不到**':10s} {r['日期'] or '—':12s} {r['落点']}")
        if not ok:
            print("✗ 版本落点**不一致** —— 跑 `bump <版本>` 或手工对齐")
            return 1
        print(f"✓ {len(rows)} 处落点彼此一致：{rows[0]['版本']}（{rows[0]['日期'] or '无日期'}）")
        return 0
    if cmd == "bump":
        if len(argv) < 3:
            print("用法：version_spots bump 0.1.31 [YYYY-MM-DD]")
            return 2
        touched = bump(argv[2], argv[3] if len(argv) > 3 else None)
        print(f"✓ 已改 {len(touched)} 处：")
        for rel, label in touched:
            print(f"  · {rel} — {label}")
        return 0
    print(f"未知命令 {cmd!r}（check / bump）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
