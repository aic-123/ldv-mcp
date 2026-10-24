"""B12 / B14 —— **源码文本**检查（§4.3 / §K9）。

这两条为什么查源码而不是查运行时：

    B12 要防的是「实现的时候悄悄加了一个东西」——
        一个全局评分**加进去之后**，运行时结果**照样是对的**，
        只是「按哪个方向都会不满意」这个毛病又回来了。
    B14 要防的是「某天有人让自优化去改方向函数」——
        那一下不会报错，只会让结构**很久以后**变得不可复现。

⇒ 都是「加了也不报错」的东西，所以只能查**它有没有被写出来**。

---

## ⚠️ 但不能拿关键词表去扫

dce 的 `checks/scope.py` 开头记过这个坑：朴素词扫描**一定会误报**，
而且误报的恰恰是设计**要求**出现的东西。这里同样：

    `propensity` 是 §K7 **要求**的     → 不许被当成「权重」
    `penalty`    是 §I3 **要求**的     → 不许被当成「评分」
    `_last_shown` 是内核记账用的       → 不许被当成「全局量」

所以判据是三条**收窄**：

    一、走 **AST**，只看**代码** —— 注释和文档字符串里写多少次都不算
    二、只看**定义与赋值**（函数名 / 字段名），不看引用
    三、名字必须**整体**匹配 `(^|_)(score|objective|utility|global|importance|total)(_|$)`
        —— `propensity` / `penalty` 都不匹配
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from ..core import selfopt
from ..core.tri import Tri
from ._framework import Report

HERE = Path(__file__).resolve().parent
LDV = HERE.parent
CORE = LDV / "core"

# 收窄到「整体词」—— 见模块开头第三条
FORBIDDEN_NAME = re.compile(r"(^|_)(score|objective|utility|global|importance|total)(_|$)")


def _target_names(node: ast.AST) -> list[str]:
    """把赋值目标摊平成名字：`self.X` → `X`，`a.b` → `b`，`x` → `x`。"""
    out: list[str] = []
    if isinstance(node, ast.Attribute):
        out.append(node.attr)
    elif isinstance(node, ast.Name):
        out.append(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            out.extend(_target_names(e))
    return out


def _scan_global_scalars(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if FORBIDDEN_NAME.search(node.name):
                found.append(f"{path.name}:{node.lineno} 函数 {node.name}()")
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for name in _target_names(t):
                    if FORBIDDEN_NAME.search(name):
                        found.append(f"{path.name}:{node.lineno} 字段 {name}")
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            for name in _target_names(node.target):
                if FORBIDDEN_NAME.search(name):
                    found.append(f"{path.name}:{node.lineno} 字段 {name}")
    return found


def b12_no_global_scalar(rep: Report, extra_sources: list[Path] | None = None) -> None:
    """内核侧不许出现跨方向的全局评分字段。"""
    paths = sorted(CORE.glob("*.py")) + list(extra_sources or [])
    found: list[str] = []
    for p in paths:
        found.extend(_scan_global_scalars(p))
    # 顺带把「该被放过的」也报出来 —— 免得下一轮有人把 propensity 误伤掉
    allowed = sorted({n for p in paths for n in _scan_allowlisted(p)})
    rep.add("B12", "无全局标量分：源码里不许有跨方向全局评分",
            Tri_NO(found),
            f"{len(found)} 处：{found[:3]}" if found
            else f"{len(paths)} 个内核模块干净（放过的：{', '.join(allowed) or '无'}）")


def _scan_allowlisted(path: Path) -> list[str]:
    """把**合法**的权重类名字捞出来，只用于报告，不参与判定。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    keep: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in tgts:
                for name in _target_names(t):
                    if name in ("propensity", "penalty", "penalty_scale"):
                        keep.append(name)
    return keep


def b14_exogenous_boundary(rep: Report, extra_sources: list[Path] | None = None) -> None:
    """usage 驱动的那一侧不许定义「什么算一个方向」。"""
    bad: list[str] = []

    # 一、源码：自优化模块不许 import 插件，也不许写外生项
    targets = [Path(selfopt.__file__)] + list(extra_sources or [])
    for p in targets:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if "plugins" in mod:
                    bad.append(f"{p.name}:{node.lineno} 自优化侧 import 了插件（{mod}）")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if "plugins" in a.name:
                        bad.append(f"{p.name}:{node.lineno} 自优化侧 import 了插件（{a.name}）")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in tgts:
                    if isinstance(t, ast.Subscript):
                        key = t.slice
                        lit = key.value if isinstance(key, ast.Constant) else None
                        if lit in selfopt.EXOGENOUS_KEYS:
                            bad.append(f"{p.name}:{node.lineno} 自优化侧写了外生项 {lit!r}")
                    for name in _target_names(t):
                        if name in selfopt.EXOGENOUS_KEYS:
                            bad.append(f"{p.name}:{node.lineno} 自优化侧绑定了外生项 {name!r}")

    # 二、行为：真跑一轮，外生项指纹必须**逐字节不变**
    from ..core.kernel import Kernel
    from ._fixtures import build_keyset, load

    loaded = load()
    if loaded is None:
        rep.add("B14", "外生边界", Tri.UNEXPANDED, "没有语料，跑不了行为侧")
        return
    nodes, _, _ = loaded
    kernel, _ = build_keyset(nodes)
    params = selfopt.Params(
        exogenous={"direction_functions": ["keyset"], "outermost_intent": "全空间"},
        endogenous={"expand_priority": {}, "penalty_scale": 1.0},
    )
    for d in kernel.all_directions()[:3]:
        kernel.record_usage(d, {"outcome": 1.0})
    try:
        after, _info = selfopt.optimize(kernel, params)
    except selfopt.ExogenousBoundaryError as exc:
        bad.append(f"自优化自己抛了边界异常：{exc}")
        after = None
    if after is not None and after.fingerprint_exogenous() != params.fingerprint_exogenous():
        bad.append("自优化后外生项指纹变了")
    if after is not None and after.fingerprint_endogenous() == params.fingerprint_endogenous():
        # ⚠️ 这里原来传的是 `Tri_UNEXPANDED`（**函数对象本身**，少了一对括号）——
        #    它不是 `Tri` 成员 ⇒ `Report.counts` 的 `out[str(a.result)] += 1` 抛
        #    `KeyError: '<function Tri_UNEXPANDED at 0x…>'` ⇒ `run_checks` **整趟崩**。
        #    触发条件实测是「语料只有 1 个方向」（自优化没得改 ⇒ 内生指纹不变）——
        #    随仓库那份 36 项语料永远走不到，所以它一直没露。
        #    「红是判据说话，崩是判据没了」—— 见 `run_checks` 里同形的另一处记录。
        rep.add("B14", "外生边界", Tri.UNEXPANDED, "内生项没被改动，这一轮没验到东西")
        return

    rep.add("B14", "外生边界：usage 侧不许改外生项",
            Tri_NO(bad),
            "；".join(bad[:2]) if bad
            else "源码侧未越界；行为侧外生项指纹逐项未变")


# --- 小的三态糖 -------------------------------------------------------------

def Tri_NO(bad: list) -> Any:
    return Tri.NO if bad else Tri.YES


# ⚠️ 这里原来还有一个 `Tri_UNEXPANDED(reason)` 糖函数。**已删** —— 它的名字与
#    `Tri.UNEXPANDED` 只差一个点，而调用点写漏了括号时**不报错**：那个函数对象会被
#    原样塞进 `Assertion.result`，直到 `Report.counts` 才炸（`KeyError`），
#    而那时 `run_checks` 已经跑完三组插件、正要去打印 —— 症状是**整趟崩**、
#    后面的组与汇总行全都不出现。一个只为了少打五个字符、却能造成这种失效的糖，
#    不值得留。直接用 `Tri.UNEXPANDED`，理由走 `rep.add(..., detail=...)`。
