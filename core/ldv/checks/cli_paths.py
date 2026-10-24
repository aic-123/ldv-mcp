"""流程 C 的**第三条覆盖缺口**：`cli` 的**入口本身**没有被判过。

    python -m ldv.run_checks 绿  ⇒  蕴含  `cli a` / `cli b` / `cli d` / `cli r` 绿 ？

**不蕴含。** `run_checks` 走的是「一次建完 / 先建 6 维护 n−6 / `cli` 切分」三条**装配**，
而 `cli` 的**四条入口**（`only=` 参数）与 `--holdout N` / `--remove N` 的**非默认值**
从来没有被跑过。实测过的后果就是这个仓库最贵的那种错：

    缺陷 1（`outputs/_findings_real_data.md`）：`cli._assemble` 的 reach 根用了全语料，
    而 `run_checks` 用的是诚实子集 ⇒ **`run_checks` 全绿而 `cli all --remove 12` 退出码 1**。
    两处「长得一样」的代码各自演化 —— 症状不是红，是**检查报绿**。

⇒ 这一组的做法与 `run_checks.cli_split_kernel` **同一条纪律**：
   **直接调 `cli.run_one`，不复制**。它改了，这一组跟着改。

## ★ 三条断言的是「**必须红**」—— 那是已知答案对照

`cli` 的**空转护栏**是它退出码的一部分（`cli.py` 模块 docstring：空转也进退出码）。
所以下面三条的**正确答案就是红**：

    `cli d` 单独跑      ⇒ 没有使用记录 ⇒ 自优化**什么都没学到** ⇒ 必须红
    `cli b --holdout 0` ⇒ 没有可插入的项 ⇒ **B 空转** ⇒ 必须红
    `cli r` 不给 --remove ⇒ 没有可删除的项 ⇒ **B′ 空转** ⇒ 必须红

⚠️ **断言「它红了」与断言「它绿了」是两件事**，而且只有前者能证明护栏**在**。
   若把这三条写成「必须绿」，那这一组会**永远绿**（护栏拆掉也绿）——
   「空转与通过长得一模一样」，正是本项目从头到尾在防的那个形状。

⚠️ 这一组的注入就打在这一点上：**把护栏拆掉 ⇒ 三条「必须红」变绿 ⇒ 本组红**。
"""

from __future__ import annotations

from typing import Callable

from ..core.tri import Tri
from ._framework import Report

#: 本组的编号 —— `test_injections` 第 0 条把它并进「声明过的编号」里双向核对。
CLI_PATH_CODES = ("CL1", "CL2", "CL3", "CL4", "CL5", "CL6", "CL7")

#: `(编号, 标题, `run_one` 的关键字, 已知答案)`
#:   已知答案 `True` = 护栏必须**全过**；`False` = 护栏必须**开火**（这一条是红的正确答案）。
PATHS: tuple[tuple[str, str, dict, bool], ...] = (
    ("CL1", "`cli a` 单独跑（只跑流程 A）",
     dict(only="a", holdout=12, remove=0), True),
    ("CL2", "`cli b` 单独跑（只跑流程 B）",
     dict(only="b", holdout=12, remove=0), True),
    ("CL3", "`cli d` 单独跑 ⇒ **必须红**（D 空转：没有记录，自优化什么都没学到）",
     dict(only="d", holdout=12, remove=0), False),
    ("CL4", "`cli b --holdout 0` ⇒ **必须红**（B 空转：没有可插入的项）",
     dict(only="b", holdout=0, remove=0), False),
    ("CL5", "`cli r` 不给 `--remove` ⇒ **必须红**（B′ 空转：没有可删除的项）",
     dict(only="r", holdout=12, remove=0), False),
    ("CL6", "`cli all --holdout 7` ⇒ 护栏全过（**N ≠ 12**）",
     dict(only=None, holdout=7, remove=0), True),
    ("CL7", "`cli all --remove 5` ⇒ 护栏全过（**--remove N ≠ 12**）",
     dict(only=None, holdout=12, remove=5), True),
)


def run_cli_paths(loaded, targets, rep: Report, *,
                  run_one: Callable[..., tuple[bool, str]] | None = None) -> None:
    """把 `cli` 的四条入口与两个非默认参数**各跑一遍**，逐条对**已知答案**。

    `run_one` 默认是 `cli.run_one`（**直接调，不复制**）；注入时换成一个
    「护栏永远不开火」的对照实现 —— 那样三条「必须红」的断言必须开火。
    """
    if run_one is None:
        from ..cli import run_one as run_one          # 生产路径：直接调

    if loaded is None:
        for code, title, _kw, _exp in PATHS:
            rep.add(code, title, Tri.UNEXPANDED, "没有语料 ⇒ 判不了（跳过 ≠ 通过）")
        return

    for code, title, kw, expected in PATHS:
        got: list[tuple[str, bool]] = []
        for w in targets:
            ok, _text = run_one(w, kw["holdout"], kw["only"], kw["remove"],
                                loaded=loaded)
            got.append((w, ok))
        wrong = [(w, ok) for w, ok in got if ok is not expected]
        if not wrong:
            rep.add(code, title, Tri.YES,
                    f"{len(got)} 个方向逐一 {'护栏全过' if expected else '护栏开火'}"
                    f"（{'、'.join(w for w, _ in got)}）")
        else:
            rep.add(code, title, Tri.NO,
                    f"已知答案 = {'护栏全过' if expected else '护栏必须开火'}，"
                    f"实际不符的方向：{wrong}")


def skip_all(rep: Report, why: str) -> None:
    """整组跳过（与 `view_report` / `multilevel_report` 的三条跳过路径同形）。"""
    for code, title, _kw, _exp in PATHS:
        rep.add(code, title, Tri.UNEXPANDED, why)
