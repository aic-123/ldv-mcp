"""检查框架 —— 设计文档 §7。

**每条检查三态**：`是`（过）/ `否`（红）/ `未展开`（跳过）。
**跳过要显式报出，与「通过」分开计数。**

⚠️ 这条纪律不是形式主义。dce 那边实测踩过：
   一条检查在缺语料时**静默消失**，于是「9 条断言、跳过 0」和
   「8 条断言、跳过 1」在汇总里长得**一模一样** ——
   而汇总写的是「跳过 0」。**空转与通过长得一模一样，是这类检查最危险的失效模式。**

所以这里：

    `Report.counts` 把三个数**分开**报
    `Report.assertions` 是**全部**注册的检查，一条都不能少
    `red` 只数 `否`
    退出码只看 `red`；**度量走 `render()`，不进退出码**（继承 dce）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.tri import Tri

# §7 表格里逐条写死的编号 —— 用来查「有没有漏注册」
EXPECTED_CODES = tuple(f"B{i}" for i in range(1, 20))


@dataclass
class Assertion:
    code: str
    title: str
    result: Tri
    detail: str = ""

    def line(self) -> str:
        mark = {Tri.YES: "过", Tri.NO: "红", Tri.UNEXPANDED: "跳"}[self.result]
        tail = f"  —— {self.detail}" if self.detail else ""
        return f"[{mark}] {self.code} {self.title}{tail}"


@dataclass
class Report:
    plugin: str = ""
    #: 这个报告**应该**包含哪些编号。空元组 = 不设预期。
    #: ⚠️ 不能用全局的 `EXPECTED_CODES` 当默认 —— 插件报告本来就只含
    #:    B1–B11/B13，内核报告只含 B12/B14。拿全集去比会报出一堆假缺失，
    #:    而**假缺失和真缺失在输出里长得一样**（正是这里要防的毛病）。
    expects: tuple[str, ...] = ()
    assertions: list[Assertion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, code: str, title: str, result: Tri, detail: str = "") -> Assertion:
        a = Assertion(code=code, title=title, result=result, detail=detail)
        self.assertions.append(a)
        return a

    def note(self, text: str) -> None:
        self.notes.append(text)

    @property
    def counts(self) -> dict[str, int]:
        out = {"是": 0, "否": 0, "未展开": 0}
        for a in self.assertions:
            out[str(a.result)] += 1
        return out

    @property
    def red(self) -> list[Assertion]:
        return [a for a in self.assertions if a.result is Tri.NO]

    @property
    def skipped(self) -> list[Assertion]:
        return [a for a in self.assertions if a.result is Tri.UNEXPANDED]

    def missing_codes(self) -> list[str]:
        """`expects` 里规定要有、但实际没注册的编号。

        与「跳过」**分开报**：跳过 = 检查跑了但判不了；缺失 = 检查根本没跑。
        这两件事在汇总里必须长得不一样。
        """
        have = {a.code for a in self.assertions}
        return [c for c in self.expects if c not in have]

    def unexpected_codes(self) -> list[str]:
        """不在 `expects` 里却出现了的编号 —— 反向也查一遍。"""
        return [a.code for a in self.assertions if self.expects and a.code not in self.expects]

    def render(self) -> str:
        c = self.counts
        lines = [f"═══ 插件 {self.plugin or '?'} ═══"]
        for a in self.assertions:
            lines.append("  " + a.line())
        missing = self.missing_codes()
        extra = self.unexpected_codes()
        if missing:
            lines.append(f"  ‼ 该跑却没跑：{missing}")
        if extra:
            lines.append(f"  ‼ 不该有却有了：{extra}")
        lines.append(
            f"  ── 断言 {len(self.assertions)} 条：过 {c['是']}，红 {c['否']}，跳过 {c['未展开']}"
        )
        if self.skipped:
            lines.append(f"  ── 跳过的是：{', '.join(a.code for a in self.skipped)}")
        for n in self.notes:
            lines.append(f"  · {n}")
        return "\n".join(lines)


def result_of(rep: Report, code: str) -> Tri:
    """`rep` 里编号为 `code` 的那条断言的三态。**没有那条 ⇒ `UNEXPANDED`。**

    ⚠️ 合成对照（`a1_known_answer` / `b17_known_answer` / `b20_known_answer` …）
       要靠它把「跑出来的三态」取回来比对。**没有 ⇒ 未展开**是**故意**的：
       对照要的正是「该红时红、该绿时绿」—— 一条**没注册**的断言必须被读成
       「判不了」，不能读成「过」（`跳过 ≠ 通过` 的同一形态）。

    ⚠️ 它是**一处**实现：`abstraction` / `equivalence` / `refinement` 三处的对照共用它
       （决策规则 3：「凡『两处各写一份』的地方，第二处必须在写之前就从第一处取」）。
    """
    for a in rep.assertions:
        if a.code == code:
            return a.result
    return Tri.UNEXPANDED
