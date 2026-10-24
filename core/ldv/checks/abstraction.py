"""流程 E · 抽象层 —— `§A1`–`§A7`（`docs/分层方向视图-抽象层.md` §9）。

    python -m ldv.run_checks            # 判据在 `(视图)` 那一组里

算法在 `core/views.py`（纯的）。本模块只做两件事：**把结构装成视图**、
**把七条判据变成会红的断言**。

---

## 七条判据各守什么

    §A1 健全性   `具体化(v) ⊇ ∪{v 覆盖的原始项}`      —— 视图**不许丢东西**
    §A2 稳定     `B₁ ⊆ E⁻¹(B₂)` 或 `B₁ ∩ E⁻¹(B₂) = φ` —— 「稳定」是对 `E` 说的
    §A3 最粗     不存在更粗的稳定划分                  —— `Q` 就是那个**唯一**的解
    §A4 类别     `distributive` 要给出 `G` 且实测对得上；`holistic` 要附见证
    §A5 账       视图答不出的那些必须**逐条指得出来**
    §A6 落盘     读回之后 `§A1` 仍然成立，且存档**不许带派生边**
    §A7 传播     `E′` 之后仍稳定，且**只许细分**（不许用重算冒充）

⚠️ **`§A3` 在谓词上包含 `§A2`**（不稳定的 `Q` 不可能是那个唯一解 ⇒ `§A3` 也红）。
   两条都留着，理由与各自的覆盖面写在 `a3_coarsest` 的 docstring 里 ——
   **不是**「多一层保险」，是「`§A2` 无条件判、`§A3` 会因为枚举上限跳过」。

`§A1` 是 **§K8 那条不对称契约**（假阴禁止 / 假阳计量）的视图侧对应物 ——
Cousot & Cousot 1977 §6 的 `Co ⊑ C_A` **必须**、`C_A ⊑ Co` **不必须**，
逐字就是「视图不许丢东西、但可以更粗」。见设计稿 §2。

`§A4`–`§A7` 各自的前置件（读数声明 / 账 / 视图存档 / 一次结构变动）在
`core/view_persist.py`、`core/views.py` 与 `view_spec.json` 里，
落地范围与**没做的部分**写在 §9.1 那张表上。

---

## ★ 视图的派生 payload 用**已有的** `§I2 合并` —— 不新增接口方法

`§A1` 里的「具体化」不是「把成员的覆盖并起来」（那样它就**只是 `B16` 换个写法**，
恒同真、不提供独立信息）。它是：

    具体化(v)  =  覆盖( 合并({ d : d ∈ v }) )        ← 视图**自己**答得到的原始项
    v 覆盖的原始项 =  ∪{ members(d) : d ∈ v }        ← 下层**声明**它收着的项

    §A1:  具体化(v)  ⊇  ∪{ members(d) }             「多了合法，少了即违规」

「合并」正是 `§I2` 那一个方法（`插件.合并(ds)`），三个插件都实现过，
而且它的契约本来就写着**必须覆盖所有输入**（`plugins/keyset.py::merge` 的
docstring 里那条证明）。⇒ 这一步**不新增接口方法**，也不用重验 `§I1`–`§I7`。

⚠️ **所以 `§A1` 与 `B16` 不是同一个东西，虽然它们长得很像**：

    `B16`   逐**方向**：`members(d) ⊆ 覆盖(d.payload)`
    `§A1`   逐**视图**：`∪members(d) ⊆ 覆盖(合并(整块的 payload))`

   `B16` 全绿**推不出** `§A1` 绿：合并是**另一个函数**（`keyset` 上是 `req` 取交、
   `forb` 取交），它可能把覆盖**收窄**到装不下某个成员。实测：见
   `run_tests.test_view_a1_not_implied_by_b16`（构造一个「合并会丢项」的插件，
   `B16` 绿而 `§A1` 红）。

---

## 外生项从哪来：**没声明就跳过，不许猜**

`P` 与 `E` 必须外生（设计稿 §3 两条边界 / `§K9` / `B14`）。所以：

    人把它们写在 `ldv/checks/view_spec.json` 里（带**语料指纹**）
    没写 / 指纹对不上 / 写的是别的方向 ⇒ 七条判据**全部报「跳过」**并印原因

⚠️ **「跳过」不是「通过」**。这正是设计稿 §10 停止条件第 1 条：
   「初始划分 `P` 或关系 `E` 没声明 —— 它们必须外生。猜一个就是替人做 `§K9` 的决定。」

⚠️ 而**声明了却坏了**（JSON 写错、`P` 不是划分、方向名不在内核里）是**另一回事**：
   那是**报错**，不是跳过。两条路的区别与 `_fixtures.find_corpus` 里
   「显式指定却找不到 ⇒ 报错」完全同源。

---

## 已知答案的对照组：**合成图上先跑三态，再上真语料**

设计稿 §9 的 ⚠️ 逐字：`§A1`–`§A7` 都必须先在**已知答案的合成图上**跑一遍
（三态：过 / 红 / 跳过各一例），再上真语料。

⇒ `known_answer_controls()` 就是那一批，`run_tests.test_view_known_answers` 跑它。
   理由是本项目的老教训：**「空转与通过长得一模一样」**，
   而合成图上的已知答案是对照组里**最便宜**的那一档。

---

## 本趟**没有**做的（写在明处，不留给读者猜）

设计稿 §9 的 `§A1`–`§A7` **七条都已落地**，但四条各有**边界**，写在 §9.1：

    `§A4` 只判**已在注册表里**的读数（`READINGS`）。「能不能自动发现某个读数的类别」
          没做，也不该做 —— 类别是**外生声称**（§10 停止条件 2：定不下来就停）。
    `§A5` 只判**可指认**（每条账指得到方向与项）。**完整性**（漏的恰好是账上那些）
          是 `B4` 的活，不在这里重复 —— 两条判同一件事会让「红」分不清是谁的。
    `§A6` 判「读回后 `§A1` 仍成立」+「存档不许带派生边」。**只判一次落盘-读回**，
          **不做「落盘后结构又动、再读回」的多轮往返**。
    `§A7` 判传播后「仍稳定 + 只许细分」。**不判「传播 ≡ 重算」** ——
          设计稿 §7 逐字说那**不成立**，且**不成立不破坏任何检查** ⇒ 它是**读数**。

**没做就是没做**：`VIEW_CODES` 里**没有**任何未实现的编号，
所以「套件全绿」这句话的范围与它声明的范围**恰好相等**
（`test_injections` 的注册表自检**双向**守这件事）。
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..core.tri import Tri
from ..core.views import (
    MAX_COARSENING_CANDIDATES,
    MIN_SHRINK_RATIO,
    ViewSpec,
    coarser_stable_exists,
    coarsest_stable_refinement,
    coarsest_stable_refinement_signature,
    n_coarsening_candidates,
    extend_q,
    maintain,
    partition_of,
    propagate,
    propagate_naive,
    refines,
    refines_partition,
    restrict_spec,
    stable,
    view_parts,
)
from ._framework import Report, result_of as _result

#: 视图侧判据的编号 —— 与 `run_checks.PLUGIN_CODES` / `KERNEL_CODES` **并列**，
#: 不是它们的一部分（视图层与插件无关，见模块开头）。
#: ⚠️ **只列**已经实现了的。没实现的不许写进来 —— 写进来就等于声称
#:    「套件全绿」覆盖了它，而它根本没跑（注册表自检会红）。
VIEW_CODES = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8")

#: 外生声明放**代码旁边**，与 `cover_leak_baseline.json` 同一个理由：
#: 它要跟着代码走、进版本库，换了语料或换了方向就该一起改。
VIEW_SPEC_PATH = Path(__file__).resolve().parent / "view_spec.json"

#: 声明文件的**名字**（两个位置同名，靠所在目录区分）。
VIEW_SPEC_NAME = VIEW_SPEC_PATH.name


def spec_paths(nodes_dir: Path | None = None) -> tuple[Path, ...]:
    """外生声明的**两个位置**，按优先级：

        ① **语料旁**  `<nodes 的父目录>/view_spec.json` —— 外部语料用
        ② **仓内**    `ldv/checks/view_spec.json`        —— 仓内默认语料用（原来那一份）

    ## 为什么要按语料找 —— 2026-10-10 的全量跑暴露的

    声明里的 `语料` 指纹把它**绑死在一份语料**上（`spec_for` 拿它比对）。
    而文件**只有一份** ⇒ 无论写哪一份语料，**另一份的 `§A`/`§M`/`§T` 三组必然整组跳过**
    —— 实测：全量 3907 项那趟（`outputs/_intake_full_v0.1.15.txt`，断言 96 · 跳过 26），
    **21 条**（`A1`–`A7` / `M0`–`M6` / `T1`–`T7`）因为「声明是 36 项语料上写的」而跳过
    ⇒ **「全量闸门通过」不覆盖这两层**。
    ⚠️ **这是那一趟的快照**：当时 `M4` 还是判据、`A8` 与 `T8` 还没落地
      （今天这三组的编号集合已经变了）⇒ **别拿今天的编号去核这个数**。

    ## 为什么放在语料旁 —— 有先例，不是新布局

    `expected_reds.json` 就是这么放的（`test_intake.red_baseline_path`：
    `nodes_dir.parent / "expected_reds.json"`），理由也印在那里：
    **外部数据不进仓库，它的读数也不进** ⇒ 它的**声明**同理。

    ⚠️ **2026-10-10 用户裁定的一处例外**：`openalex-citations` 的**声明与认识**
       （`view_spec.json` + `views.json`）**进版本库**（`.gitignore` 里两条 `!` 例外），
       语料本身照旧不进。理由：仓内那一对（`ldv/checks/` 下同名两份）**本来就在库里**
       ⇒ 外部那份跟着进，两条链才对称。**`expected_reds.json` 仍不进** ——
       它是**读数**，一条命令就能重冻（`--write-red-baseline`）。

    ⚠️ 路径**不由环境变量决定**：它跟着 `nodes_dir` 走 ⇒ 「跑的是哪份语料」与
       「读的是哪份声明」**不可能分叉**（`find_corpus` 已经为语料保证了同一件事）。
    """
    out: list[Path] = []
    if nodes_dir is not None:
        out.append(nodes_dir.parent / VIEW_SPEC_NAME)
    if VIEW_SPEC_PATH not in out:
        out.append(VIEW_SPEC_PATH)
    return tuple(out)


# --- 视图对象 -----------------------------------------------------------------

@dataclass(frozen=True)
class View:
    """一张视图 —— `Q` 的一块 + 它**答得到的原始项**。

        `block`           这块覆盖哪些**方向**（`Q` 的一块）
        `payload`         派生 payload = `插件.合并({那些方向})`（§I2，**不新增方法**）
        `concretization`  **具体化** = `覆盖(payload)` —— 这张视图答得到的原始项
        `covered`         它**声明**收着的原始项 = `∪ members(d)`
        `category`        §5 的三类之一（`§A4` 用；**本趟只留字段不判**）
        `ledger`          §6 的账（`§A5` 用；**本趟只留字段不判**）

    ⚠️ `concretization` 与 `covered` **必须分开存**，不许用一个算另一个：
       `§A1` 判的就是这两者之间的包含关系。若 `concretization` 是
       `covered` 现场算出来的，那条判据**恒真** —— 它就不再是判据了。
    """

    vid: str
    block: frozenset[str]
    payload: Any = None
    concretization: frozenset[str] = frozenset()
    covered: frozenset[str] = frozenset()
    category: str = ""
    ledger: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_dirs(self) -> int:
        return len(self.block)


@dataclass(frozen=True)
class ViewSet:
    """一组视图 + 它由来的那个 spec（判据要拿 spec 复算稳定性）。"""

    spec: ViewSpec
    q: tuple[frozenset[str], ...]
    views: tuple[View, ...]

    def render(self) -> str:
        return (f"{len(self.views)} 张视图（{len(self.spec.universe)} 个方向，"
                f"{len(self.spec.partition)} 个初始块，{len(self.spec.relation)} 条边）")


# --- 装配 ---------------------------------------------------------------------

def build_views(kernel: Any, spec: ViewSpec, cover: Callable[[Any], frozenset[str]],
                plugin: Any) -> ViewSet:
    """把内核里的结构按 `spec` 装成视图集合。

    `Q = 最粗稳定细化(spec)`（**唯一**，见 `core/views.py`），每块一张视图。

    ⚠️ **`合并` 可能抛异常**（payload 形状由插件产出）。抛了就**不让它静默**：
       记成一张 `concretization` 算不出来的视图，`§A1` 会把它报成违规。
       悄悄跳过会让「合并坏了」与「这块没有成员」长得一模一样。
    """
    q = coarsest_stable_refinement(spec)
    views: list[View] = []
    for i, block in enumerate(q):
        dirs = [kernel.direction(d) for d in sorted(block)]
        covered: set[str] = set()
        for d in dirs:
            covered |= set(kernel.members_of(d))
        try:
            payload = plugin.merge(dirs)
            conc = frozenset(cover(payload))
            bad = ""
        except Exception as exc:  # noqa: BLE001 - payload 由插件产出
            payload, conc = None, frozenset()
            bad = f"合并/覆盖算不出来：{type(exc).__name__}: {exc}"
        v = View(vid=f"V{i}", block=block, payload=payload,
                 concretization=conc, covered=frozenset(covered))
        views.append(v if not bad else replace(v, category=bad))
    return ViewSet(spec=spec, q=q, views=tuple(views))


# --- 外生 spec 的加载 ----------------------------------------------------------

def load_spec_file(nodes_dir: Path | None = None,
                   corpus: dict[str, int] | None = None) -> dict[str, Any]:
    """读外生声明。**按语料找**（`spec_paths`），并把 `语料` 匹配的那一份优先。

    ## 两条规矩（都为了「跳过」与「坏了」分得开）

        两份都**不存在**        ⇒ 返回 `{}` ⇒ 上层报「**未声明**」（还没人写）
        有存在的，但**都不匹配**  ⇒ 返回**第一份存在的** ⇒ 上层报
                                  「声明是在**另一份语料**上写的（{它写的} vs 现在 {现在}）」
                                  ★ 这样印出来的是**它到底写了哪份语料**，而不是笼统一句「没声明」
        **有语法错**            ⇒ **抛**（不 catch）—— 写坏的声明与没写的声明必须分得开
                                  ⇒ 所以**不**为了「跳到下一份」而吞掉异常

    ⚠️ `corpus` 给了才做匹配；不给就只按优先级取第一份（那等于「只找位置、不认语料」，
       只有调用方明确不需要匹配时才该这么用）。
    """
    found = [p for p in spec_paths(nodes_dir) if p.is_file()]
    if not found:
        return {}
    if corpus is not None:
        for p in found:
            doc = json.loads(p.read_text(encoding="utf-8"))   # 语法错 ⇒ 抛，见上
            if doc.get("语料") == corpus:
                return doc
    return json.loads(found[0].read_text(encoding="utf-8"))


def spec_for(doc: dict[str, Any], which: str, corpus: dict[str, int]) -> tuple[ViewSpec | None, str]:
    """把外生声明解成一个 `ViewSpec`。返回 `(spec, 说明)`；`spec is None` ⇒ 报**跳过**。

    三种「跳过」各有各的话，因为它们要人做的事**不一样**：

        文件不在                 ⇒ 「还没人声明」
        指纹对不上               ⇒ 「声明是**别的语料**上写的」
        `方向` 不是这一条        ⇒ 「声明是**别的方向**的」

    ⚠️ **声明了却坏了 ⇒ 抛异常**（不是跳过）。`P` 不是划分、`universe` 里有
       内核不认识的方向 id、JSON 语法错 —— 这些都是**写错了**，
       与「还没写」是两件事。混成一条「跳过」会让写错的声明**看起来像没写**，
       于是没人去修它。
    """
    if not doc:
        return None, (f"外生项**未声明**（`{VIEW_SPEC_PATH.name}` 不在）—— "
                      f"`P` / `E` 必须外生（设计稿 §3 / §K9），"
                      f"猜一个就是替人做决定（§10 停止条件 1）")
    frozen = doc.get("语料") or {}
    if frozen != corpus:
        return None, (f"外生声明是在**另一份语料**上写的（{frozen} vs 现在 {corpus}）"
                      f"⇒ 本语料上**判不了**（跳过 ≠ 通过）")
    if doc.get("方向") != which:
        return None, (f"外生声明写的是**另一条方向**（{doc.get('方向')!r} vs {which!r}）"
                      f"⇒ 本方向**判不了**（跳过 ≠ 通过）")
    universe = tuple(doc.get("universe") or ())
    partition = tuple(frozenset(b) for b in (doc.get("partition") or ()))
    relation = frozenset(tuple(e) for e in (doc.get("relation") or ()))
    # ⚠️ `ViewSpec.__post_init__` 会抛 —— **故意不让它被吞**。见上面那段。
    spec = ViewSpec(universe=universe, partition=partition, relation=relation)
    return spec, f"外生声明已读到（{VIEW_SPEC_PATH.name}，语料 {frozen}）"


# --- §A1 ----------------------------------------------------------------------

def a1_soundness(vs: ViewSet, rep: Report) -> None:
    """**具体化不许比它声明收着的少** —— `§K8` 的视图侧，`§2` 那条不对称。

        具体化(v)  ⊇  ∪{ members(d) : d ∈ v }

    `⊇` **不是** `=`：多了合法（视图可以更粗，那是**假阳**，计量不进退出码），
    少了即违规（那是**假阴**，§K8 唯一禁止的那一侧）。

    ## 为什么这条必须能红（它的注入是什么）

    在**规范构造**（`build_views` 用 `插件.合并` 派生 payload）下它是绿的，
    而绿的**理由**可能是「合并真的覆盖得住」，也可能是「这条判据根本没在判」
    —— **两者长得一模一样**。所以注入直接**掐掉一项**：
    把某张视图的 `concretization` 删掉一个成员，看它红不红
    （`test_injections.inj_a1`）。**红的形状就是这条判据的全部内容。**

    ⚠️ 视图集合为空 ⇒ **跳过**，不是过（设计稿 §9）。
    """
    if not vs.views:
        rep.add("A1", "视图健全性：具体化 ⊇ ∪成员", Tri.UNEXPANDED,
                "视图集合为空 —— 判不了，不是通过")
        return
    bad: list[str] = []
    checked = 0
    for v in vs.views:
        checked += len(v.covered)
        miss = sorted(v.covered - v.concretization)
        if miss:
            why = v.category or ""
            bad.append(f"{v.vid}（{v.n_dirs} 个方向）的具体化少了 {len(miss)} 项"
                       f"{miss[:3]}" + (f"；{why}" if why else ""))
    rep.add("A1", "视图健全性：具体化(v) ⊇ ∪{v 覆盖的原始项}（§K8 的视图侧）",
            Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 张视图丢了东西：{bad[:2]}") if bad
            else f"{len(vs.views)} 张视图、{checked} 个成员项全部落在各自的具体化里"
                 f"（多出来的部分算假阳，只计量不进退出码）")


# --- §A2 ----------------------------------------------------------------------

def a2_stable(spec: ViewSpec, q: Sequence[frozenset[str]], rep: Report) -> None:
    """**稳定** —— 逐字按 Paige–Tarjan 的定义（设计稿 §3）：

        对每对块 `B₁, B₂`：`B₁ ⊆ E⁻¹(B₂)` 或 `B₁ ∩ E⁻¹(B₂) = φ`

    ⇒ 「存在一块**跨**在 `E⁻¹(B₂)` 内外」就是红。反例里印出**是哪两个元素**
      （一个走得到、一个走不到），因为「`B₁` 不稳定」这句话**指不出**该改哪里。

    ⚠️ **`E` 为空 ⇒ 跳过。** 空关系下**任何**划分都稳定 —— 这条判据在那时
       **没有内容**。判据的适用范围由「它在这条方向上有没有内容」决定，
       不由偏好决定（`B17`(b) 在 `reach` 上降级是同一条）。
    """
    if not spec.relation:
        rep.add("A2", "视图稳定：B₁ ⊆ E⁻¹(B₂) 或 B₁ ∩ E⁻¹(B₂) = φ", Tri.UNEXPANDED,
                "外生关系 `E` 为空 ⇒ 任何划分都稳定 ⇒ 这条判据**没有内容**（跳过 ≠ 通过）")
        return
    ok, bad = stable(spec, q)
    rep.add("A2", "视图稳定：对每对块，要么整块走得到、要么整块走不到",
            Tri.YES if ok else Tri.NO,
            f"{len(q)} 块全部稳定（{len(spec.relation)} 条外生边）" if ok
            else f"{len(bad)} 对块不稳定：{bad[:2]}")


# --- §A3 ----------------------------------------------------------------------

def a3_coarsest(spec: ViewSpec, q: Sequence[frozenset[str]], rep: Report) -> None:
    """**最粗** —— 「不存在更粗的稳定划分」。

    oracle 是 `core/views.coarser_stable_exists`：**暴力枚举** `Q` 的粗化
    （只在同一个 `P` 块内部合并），逐个验稳定性。它与
    `coarsest_stable_refinement` **不共用一行代码** —— 用「再算一遍比一比」
    当 oracle 会与被判对象**共享盲点**（两边同时错、判据永远绿，
    `false-green` 形状 3）。

    ⚠️ **撞上枚举上限 ⇒ 跳过**，不是过。`Bell(k)` 长得快，而「搜不完」
       与「搜完了没有」在只看布尔值时**长得一模一样** —— 所以
       `coarser_stable_exists` 返回的是三态（`None` = 没搜完）。

    ⚠️ **`Q` 不是 `P` 的细化**也在这里报 —— 设计稿 §3 边界 ① 说视图只会
       比初始划分**更细**。少了这一条，一个「把两个初始块并起来」的 `Q`
       照样可以稳定，于是 `§A2` 绿而它根本不是 `P` 的细化。

    ⚠️ **`Q` 自己不稳定 ⇒ 也红。** 这一条是**补出来的**，设计稿 §9 那张表
       没写：`§A3` 的声称是「`Q` 就是那个**唯一**的解」，而不稳定的划分
       **不可能**是那个解 ⇒ 声称是**假的**，该红。
       （早先的写法只查「有没有更粗的稳定划分」，于是喂一个不稳定的 `Q`
         会**报绿** —— 实测：`Q = P` 时唯一的粗化是恒等，被跳过，结论
         「没有更粗的稳定划分」⇒ 过。**一个不稳的划分被报成「已是最粗」**，
         这正是本项目最忌的那种假绿。）

    ## ★ 与 `§A2` 的**重叠**，写在这里不藏着

    按谓词，`§A3` **包含** `§A2`（不稳定 ⇒ 红）。两条都留着，理由**不是**「多一层保险」：

        `§A2`  **无条件**判稳定（只要 `E` 非空）—— 它永远不会因为别的原因跳过
        `§A3`  会因为**枚举上限**跳过（`Bell(k)` 长得快）⇒ 它可能**判不了**

    ⇒ 「`§A2` 绿」这句话的覆盖面**严格宽于**「`§A3` 绿」。
      而两条的**反例**也不同：`§A2` 指得出**哪一对块、哪两个元素**；
      `§A3` 分开报三种红（不是 `P` 的细化 / 未到不动点 / `P` 选粗了），
      最后一种对应**停止条件 3**（`P` 是外生的，只能由人改）。
    """
    if not spec.relation:
        rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.UNEXPANDED,
                "外生关系 `E` 为空 ⇒ 稳定是空条件 ⇒ 这条判据**没有内容**（跳过 ≠ 通过）")
        return
    if not refines(spec, q):
        rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.NO,
                f"`Q` **不是** `P` 的细化：有块跨出了初始块"
                f"（{len(q)} 块 / {len(spec.partition)} 个初始块）—— 设计稿 §3 边界 ①")
        return
    ok, bad = stable(spec, q)
    if not ok:
        rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.NO,
                f"`Q` 自己就**不稳定**（{len(bad)} 对块）⇒ 它不可能是那个唯一解；"
                f"反例见 `§A2`：{bad[:1]}")
        return
    has, why, seen, oracle = a3_oracle(spec, q)
    if has is None:
        rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.UNEXPANDED,
                f"{why} —— 判不了，不是通过")
        return
    rep.add("A3", "视图最粗：不存在更粗的稳定划分（`Q` 就是那个**唯一**的解）",
            Tri.NO if has else Tri.YES,
            f"存在更粗的稳定划分：{why} —— `P` 本身选粗了（停止条件 3：`P` 外生，"
            f"只能由人改）｜oracle：{oracle}" if has
            else f"{len(q)} 块已是最粗（{oracle}）")


def a3_oracle(
    spec: ViewSpec, q: Sequence[frozenset[str]],
) -> tuple[bool | None, str, int, str]:
    """`§A3` 的**判定** —— 返回 `(有没有更粗的稳定划分, 说明, 枚举到的候选数, 用的哪条 oracle)`。

    ## 两条 oracle，**按规模选**（2026-10-10 加第二条）

        ① **暴力枚举**（`coarser_stable_exists`）—— 候选数 `≤ MAX_COARSENING_CANDIDATES` 时用。
           它**一行都不走** `_refine`，是最强的独立性 ⇒ **能跑就跑它**。
        ② **多项式（签名）** —— 候选数超上限时用。
           `coarsest_stable_refinement_signature` 是 `csr(P)` 的**第二条实现**
           （按「可达块签名」分块，而不是按 `E⁻¹` 劈），实测全量上 **0.5 ms**。

    ## ★★ 为什么必须有 ② —— 2026-10-10 全量语料实测

        候选数是 `∏ Bell(组内块数)`：36 项（8 块）**4140** ⇒ 搜得完；
        **全量（19 块）5.83e12** ⇒ 搜不完 ⇒ ① 只能**报跳过**。
        而**跳过 ≠ 通过** ⇒ 「视图最粗」这条**承重性质在全量上瞎了**。

    ## ② 的判据与前提取自那条**唯一性**定理

        `Q` **稳定**且 `⊑ P`（调用方已在前面**重算**过这两件 —— 不是信 `§A2` 的结论）
        ⇒ `csr(P) ⊑ Q`（最粗的定义）⇒

            `Q == csr(P)`  ⟺  **没有更粗的稳定划分**

        而没有稳定性时这个等价**不成立** ⇒ 所以调用方必须在前面挡住不稳定
        （它会红，且与 `§A2` **同源** —— 那段重叠写在 `a3_coarsest` 的 docstring 里）。

    ⚠️ **不许改成「两块能不能合并」那种局部判据。** 实测反例（三环，
       `E = {(a,c),(c,b),(b,a)}`、`P = {U}`）：`{{a},{b},{c}}` 里**任意两块合并都不稳定**，
       而**三块合成一块稳定** ⇒ 局部判据会把「有更粗的」**判成「没有」**（假绿）。
       见 `run_tests.test_views` 的那条对照。
    """
    n_cand = n_coarsening_candidates(spec, q)
    if n_cand <= MAX_COARSENING_CANDIDATES:
        has, why, seen = coarser_stable_exists(spec, q)
        tag = f"暴力枚举（{seen} 个粗化，上限 {MAX_COARSENING_CANDIDATES}）"
        if has is None:
            return None, why, seen, tag
        return has, why, seen, tag
    q_star = coarsest_stable_refinement_signature(spec)
    same = partition_of(q) == partition_of(q_star)
    why = (f"`Q` 逐块等于**独立**算出的最粗稳定细化（{len(q_star)} 块）" if same
           else f"独立算出的最粗稳定细化**更粗**（{len(q)} ⇒ {len(q_star)} 块）")
    tag = (f"**多项式（签名）**—— 候选数 {n_cand} 超上限 "
           f"{MAX_COARSENING_CANDIDATES}，暴力枚举跑不完；"
           f"改用独立实现，实测 0.5 ms")
    return (not same), why, n_cand, tag


# --- 度量（不进退出码） --------------------------------------------------------

def view_profile(vs: ViewSet) -> dict[str, Any]:
    """读数。**进输出不进退出码**（设计稿 §6 的 `§A6` 那一行说得很清楚：
    账的条数、视图的块数是**度量**）。

    报三个数，缺一个都看不出来：

        `方向数 / 视图数`    压下去多少 —— 「什么都没压」在这里看得见
        `最大块`             最大的一张视图装了几个方向 —— **代价**信号
        `具体化/成员`        假阳有多少 —— 视图答得出的比它声明收着的多多少

    ⚠️ **「视图数 == 方向数」不是红，是读数**（设计稿 §4 E3）。
       那说明这一层**没有可合并的东西** —— 是一个**结论**（该层的视图 = 该层本身），
       不是失败。把它做成红就会出现一条**常驻的红**，
       而「一条常驻的红等于没人再看红」。
    """
    if not vs.views:
        return {"方向数": len(vs.spec.universe), "视图数": 0, "最大块": 0,
                "具体化": 0, "成员": 0, "没压下去": False}
    sizes = [v.n_dirs for v in vs.views]
    conc = sum(len(v.concretization) for v in vs.views)
    cov = sum(len(v.covered) for v in vs.views)
    return {
        "方向数": len(vs.spec.universe),
        "视图数": len(vs.views),
        "最大块": max(sizes),
        "具体化": conc,
        "成员": cov,
        "没压下去": len(vs.views) == len(vs.spec.universe),
    }


def render_views(vs: ViewSet | None, prof: dict[str, Any], which: str = "") -> str:
    head = f"视图（{which}）" if which else "视图"
    if vs is None or not vs.views:
        return f"{head}：没有视图（未展开，**不是**通过）"
    return (f"{head}：{prof['方向数']} 个方向 ⇒ {prof['视图数']} 张视图"
            f"（最大一块 {prof['最大块']} 个方向）"
            f"｜具体化 {prof['具体化']} 项 vs 声明收着 {prof['成员']} 项"
            + ("｜⚠️ **一个方向都没压下去**（该层的视图 = 该层本身，"
               "这是**结论**不是失败 —— §4 E3）" if prof["没压下去"] else "")
            + "（度量，不进退出码）")


# --- §A4 类别不许说错 ---------------------------------------------------------
#
# 「视图能不能**只从下层算出来**」不是设计选择，它有**定理**（Gray 等 1997 的
# 三分法，设计稿 §5）。`§A4` 判的是**声称与实测一致**：
#
#     distributive  ⇒  必须给出 `G`，且  读数(reach) == G(各部件读数)     逐块成立
#     algebraic     ⇒  必须给出 `S`（定长摘要）与 `H`，且
#                      读数(reach) == H(Σ S(各部件))
#     holistic      ⇒  必须附一个**见证**：两组方向的 `S` **相同**而读数**不同**
#                      ⇒ 那个定长摘要**分不开**它们 ⇒ 「没有常数额摘要」有支撑
#
# ⚠️ **「下层」= 部件，不是「块 ⊆ reach 的块」。** 视图划分**会横跨树**
#    （实测：块 `{D10,D15,D22,D3}` 里 `D22` 不在 `D2` 的子树里）⇒ 按后者定义
#    **不构成嵌套**，恒等式根本不成立（实测 `|block| = 1` 而 `Σ|子块| = 24`）。
#    部件（`core/views.view_parts`）恰好**划分** `reach`，恒等式按构造成立。
#
# ⚠️ **恒等式本身不能当判据用** —— 它按构造恒真，不提供任何信息。
#    判的是**声称的那个 `G` / `S` / `H` 对不对**。实测（36 项语料）：
#    `最大成员数` 配 `G=取最大` ⇒ 8/8 块过；配 `G=取和` ⇒ 7/8 块红。
#    ⇒ 这条判据**能红**，而且红的形状就是「声称的那个合成函数错了」。


@dataclass(frozen=True)
class ReadingCtx:
    """读数要的两张表：每个方向的**数值**、以及它的**覆盖**。

    两者都**从结构读出来**（不是声明），所以由调用方给。
    ⚠️ 覆盖是**集合**而不是数 —— `覆盖计数` 之所以是 holistic，正是因为它要的是
       那个集合本身，而集合没有常数额摘要（`§A4` 的见证就建在这上面）。
    """

    value_of: dict[str, float]
    cover_of: dict[str, frozenset[str]]


def _vals(S: frozenset[str], ctx: ReadingCtx) -> list[float]:
    return [ctx.value_of[d] for d in sorted(S)]


def subtree_of(kernel: Any, did: str) -> frozenset[str]:
    """`did` **及其全部后代** —— `_children` 是权威边（`§10.2 D`）。

    ⚠️ 用 `_children`（父→子），**不是** `parent` / `witness`。理由与持久化那边
       同一条：入度按 `parent` **不常数**（实测 max 7），按 `_children` 恒为 1。
    """
    out: set[str] = set()
    stack = [did]
    while stack:
        x = stack.pop()
        if x in out:
            continue
        out.add(x)
        for c in kernel.children_of(kernel.direction(x)):
            stack.append(c.did)
    return frozenset(out)


def reading_ctx(kernel: Any, spec: ViewSpec) -> ReadingCtx:
    """真链路上的读数上下文。

        每个方向的**数值** = `|members(d)|`（成员数）
        每个方向的**覆盖** = `members(d)`

    ⚠️ 两者都是**从结构读出来的**，不是声明。声明的是**读数怎么合成**（`§A4`）。
    """
    return ReadingCtx(
        value_of={d: float(len(kernel.members_of(kernel.direction(d))))
                  for d in spec.universe},
        cover_of={d: kernel.members_of(kernel.direction(d)) for d in spec.universe},
    )


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2


#: 读数注册表：`名 → (在方向集合上算, 定长摘要)`。
#: ⚠️ **类别不在这里** —— 类别是**外生声称**，写在 `view_spec.json` 里。
#:    注册表只提供「这个读数怎么算」与「它的候选定长摘要是什么」。
READINGS: dict[str, tuple[Callable, Callable]] = {
    "计数": (lambda S, c: len(S), lambda S, c: (len(S),)),
    "和": (lambda S, c: sum(_vals(S, c)), lambda S, c: (len(S), sum(_vals(S, c)))),
    "平均": (lambda S, c: (sum(_vals(S, c)) / len(S)) if S else 0.0,
             lambda S, c: (len(S), sum(_vals(S, c)))),
    "中位数": (lambda S, c: _median(_vals(S, c)), lambda S, c: (len(S), sum(_vals(S, c)))),
    "最大": (lambda S, c: max(_vals(S, c), default=0.0),
             lambda S, c: (len(S), sum(_vals(S, c)))),
    "覆盖计数": (lambda S, c: len(set().union(*[c.cover_of[d] for d in S]) if S else set()),
                 lambda S, c: (len(S), sum(_vals(S, c)))),
}

#: `distributive` 的 `G`：吃「各部件读数」这个列表，吐一个读数。
COMBINERS: dict[str, Callable[[list], Any]] = {
    "取和": lambda vs_: sum(vs_),
    "取最大": lambda vs_: max(vs_),
    "取最小": lambda vs_: min(vs_),
    "取平均": lambda vs_: (sum(vs_) / len(vs_)) if vs_ else 0.0,
}

#: `algebraic` 的 `H`：吃「各部件摘要的**逐分量和**」，吐一个读数。
H_FUNCS: dict[str, Callable[[tuple], Any]] = {
    "和除计数": lambda t: (t[1] / t[0]) if t and t[0] else 0.0,
    "取第二项": lambda t: t[1] if len(t) > 1 else 0,
    "取第一项": lambda t: t[0] if t else 0,
}

#: 定长摘要注册表。`§A4` **只认这里有的名字** —— 声明里现造一个名字会被判红。
SUMMARIES: dict[str, Callable] = {
    "计数": lambda S, c: (len(S),),
    "计数与和": lambda S, c: (len(S), sum(_vals(S, c))),
}


#: `holistic` 见证的**构造法**注册表 —— `名 → 在语料上找出 (A, B) 的函数`。
#
#  ★★ 为什么不是「声明里写死 A / B」（2026-10-10 改，全量语料实测逼出来的）
#
#      写死的 A/B 是**语料相关**的：`摘要` 是 `(计数, 和)`，而「和」**随语料变**。
#      实测：`gen_view_spec --write` 换语料时按既定规矩把 `读数` **逐字照抄**，
#      于是那份**为 36 项挑的**见证被搬到 3907 项上 ⇒ 两边 `摘要` 不再相同 ⇒ `§A4` 红。
#      ⇒ 根因不是「那两条声称错了」（**实测是对的**：同摘要异读数确实存在），
#        而是**见证被写死在一个语料上**。
#      ⇒ 改成**构造法**：`§A4` 在**当前**语料上搜一对「同摘要、异读数」。
#        这样声明**与语料无关**，而证据**每次都是当场量出来的**。
#
#  ⚠️ 这个函数**只当构造法用**（找一对）；「判不判得了」是 `decide_digest_witness`
#     的事（2026-10-13：旧 `§A4` 的恒红 = 拿它的「搜不到」当「不存在」判）。

def witness_same_digest_diff_reading(f: Callable, sf: Callable, ctx: "ReadingCtx",
                                     ks: Sequence[int] = (2, 3, 4),
                                     ) -> tuple[frozenset[str], frozenset[str]] | None:
    """在语料上搜一对 `(A, B)`：`摘要` 相同，而**读数不同**。

    ## 为什么这个搜索是**可判**的（不用枚举全部子集）

        只要一对。⇒ 按 `摘要` 分桶，**每桶只留第一个**：
            再来一个同摘要的候选，若读数**不同** ⇒ **立刻命中**
            读数相同 ⇒ 不留（留第一个就够）⇒ 内存 `O(桶数)`，不是 `O(子集数)`

    ⚠️ **`k` 从 2 起**（2 元集合里「中位数 = 均值」由和决定 ⇒ 它对 `计数与和`
       必然**搜不到** —— 那一档自动跳过，由更大的 `k` 接手）。
    ⚠️ **顺序完全确定**（`sorted(universe)` + `combinations` 的字典序）
       ⇒ 同一份语料上**每次搜到同一对**，输出逐字可复现（§8.2）。
    ⚠️ **`universe` 从 `ctx.value_of` 的键取**（那就是 `spec.universe`）——
       这样构造法**不必**多接一个 `spec` 参数，`§A4` 的调用点一处不改。
    """
    universe = sorted(ctx.value_of)
    for k in ks:
        if k > len(universe):
            continue
        seen: dict[Any, tuple[Any, frozenset[str]]] = {}
        for combo in itertools.combinations(universe, k):
            S = frozenset(combo)
            dg = sf(S, ctx)
            val = f(S, ctx)
            prev = seen.get(dg)
            if prev is None:
                seen[dg] = (val, S)
            elif not _close(prev[0], val):
                return prev[1], S
    return None


WITNESS_BUILDERS: dict[str, Callable[..., Any]] = {
    "同摘要异读数": witness_same_digest_diff_reading,
}


def _close(a: Any, b: Any, tol: float = 1e-9) -> bool:
    """读数相等吗 —— 数是**容差比**，别的一律 `==`。

    ⚠️ 不能一律 `==`：`平均` 这类要除，浮点会差最后一位，
       于是**一条永远红的判据** —— 而一条常驻的红等于没人再看红。
       也不能一律用容差：整数与集合上的「差一点」**没有意义**。
    """
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))
    return a == b


# --- `§A4` 的两级证据：**函数级**（语料无关）+ **语料级**（本论域） -------------
#
# ★★★ 2026-10-13：`§A4` 恒红的真因 = 把**两个 scope** 混成了一条判据。
#
#      Scope-M（函数级，语料无关）  「这个读数**能被定长摘要决定吗**」
#           —— Gray 等 1997 三分法里 holistic 的**本义**：median 这类函数
#              不能由各部件的一个数算出。它是**读数**的性质。
#      Scope-C（语料级，本论域）    「这份语料上**存不存在**一对同摘要异读数」
#           —— 旧判据只测这一档，还用**有界搜索（k≤4）当判定**。
#
#      后果：在**摘要单射**的论域上（openalex × `sequence`：9 个方向、穷举
#      512 个子集 ⇒ 512 桶两两不同、0 对 ⇒ 见证**不存在**，不是没搜到），
#      `§A4` **恒红** —— 与声称真假无关；两条出路还都堵死（补见证已证不可能；
#      降 `algebraic` 会让块恒等式当场红）⇒ **分类学空洞**。
#
# ⇒ 修法 = 两级分开判、各自的结论各自报：
#      Scope-M  构造论域上穷举：0 对 ⇒ 红「读数本就被摘要决定」；
#                有对 ⇒ 「函数级有支撑」（随后才谈语料级）。
#      Scope-C  本论域：2^|U| ≤ 预算 ⇒ 穷举 —— 找到 ⇒ 过 + 印对；
#                0 对 ⇒ **过 + 披露**「论域的性质，不是声称不成立」；
#                超预算 ⇒ 过 + 「语料级未判」（不拿有界搜索的失败冒充判定）。

#: 穷举预算：`2^|U| ≤ 1<<16`（`|U| ≤ 16` ⇒ 65536 个子集）担当得起。
WITNESS_ENUM_BUDGET = 1 << 16


def decide_digest_witness(f: Callable, sf: Callable, ctx: ReadingCtx, *,
                          budget: int = WITNESS_ENUM_BUDGET) -> dict[str, Any]:
    """在本论域上判「同摘要异读数」这对东西**存不存在**，而不是「搜不搜得到」。

    ## 为什么不复用 `witness_same_digest_diff_reading`

    那个是**构造法**：给它 `ks`，它找**一对**。`§A4` 要的却是**判定**：
    「这对东西在这份语料上到底有没有」。两者的差别全在**搜不到时的含义**上 ——
    构造法搜不到只说明「这个 `ks` 里没有」，判定要回答「是不是根本没有」。

    ## `mode = exhaustive` ⇒ 0 对是**结论**，不是「没搜到」

        2^|U| ≤ budget ⇒ `k = 2..|U|` **全部**枚举（与构造法同序：k 升序、
        字典序；同一份语料上两次跑给出同一对）⇒ `found=False` 是**证明**：
        这份论域上 `摘要` 对**读数**单射。

    ## `mode = bounded` ⇒ `suffices=False`（搜不到**什么都不证明**）

        超出预算 ⇒ 退回构造法（`k ≤ 4`）并在返回值里如实标出来 ——
        「没判」与「判过了」不许长得一样（`§A5` 那对教训的同一条纪律）。

    ⚠️ `k` 从 **2** 起（与构造法共用这条约定）：1 元集合里摘要碰撞只可能是
       「同值不同方向」，而构造论域的六个值两两不同（见 `_A4_SYNTH_VALUES`）
       ⇒ 这条约定在构造论域上不漏。
    """
    universe = sorted(ctx.value_of)
    n = len(universe)
    if (1 << n) > budget:
        pair = WITNESS_BUILDERS["同摘要异读数"](f, sf, ctx)
        return {"found": pair is not None, "pair": pair, "mode": "bounded",
                "subsets": None, "buckets": None, "suffices": pair is not None}
    seen: dict[Any, tuple[Any, frozenset[str]]] = {}
    n_seen = 0
    for k in range(2, n + 1):
        for combo in itertools.combinations(universe, k):
            S = frozenset(combo)
            dg = sf(S, ctx)
            val = f(S, ctx)
            n_seen += 1
            prev = seen.get(dg)
            if prev is None:
                seen[dg] = (val, S)
            elif not _close(prev[0], val):
                return {"found": True, "pair": (prev[1], S), "mode": "exhaustive",
                        "subsets": n_seen, "buckets": len(seen), "suffices": True}
    return {"found": False, "pair": None, "mode": "exhaustive",
            "subsets": n_seen, "buckets": len(seen), "suffices": True}


#: `§A4` Scope-M 的**构造论域**（2026-10-13）—— 挑法是**手推**的：
#:
#:     ① 六个值两两不同 ⇒ 1 元集合不可能同摘要 ⇒ `k=2` 起的约定在此域上不漏
#:     ② 值里含 `{1,2,6}` 与 `{1,3,5}`（和都 9、中位数 2 ≠ 3）⇒ `中位数`
#:        **不是** `(计数, 和)` 的函数（k=2 上同和对的中位数都等于均值 ⇒ 见证在 k=3）
#:     ③ 覆盖含 `{a}∪{a..f}` 与 `{a}∪{a..e}`（和都 7、覆盖 6 ≠ 5）⇒ `覆盖计数`
#:        **不是**摘要的函数
#:     ④ `计数 == 取第一项`、`和 == 取第二项`、`平均 == 和除计数` ⇒ 这三个
#:        在**任何**论域上都一对找不出（恒等式）；本域把它们逐个坐实
_A4_SYNTH_VALUES = {"s1": 1.0, "s2": 2.0, "s3": 6.0, "s4": 3.0, "s5": 5.0, "s6": 10.0}
_A4_SYNTH_COVERS = {
    "s1": frozenset({"a"}),
    "s2": frozenset({"a"}),
    "s3": frozenset({"a", "b", "c", "d", "e", "f"}),
    "s4": frozenset({"a", "b", "c"}),
    "s5": frozenset({"a", "b", "c", "d", "e"}),
    "s6": frozenset({"a", "b", "c", "d", "e", "f", "g", "h", "i", "j"}),
}


def synthetic_reading_ctx() -> ReadingCtx:
    """Scope-M 的构造论域 —— **深拷贝**，免得调用方改坏共享常量。"""
    return ReadingCtx(value_of=dict(_A4_SYNTH_VALUES),
                      cover_of=dict(_A4_SYNTH_COVERS))


def _match_h(f: Callable, sf: Callable, ctx: ReadingCtx) -> str | None:
    """构造论域上逐个登记 `H` 试：哪个让 `f ≡ H∘摘要`（⇒ f 由摘要决定）。"""
    universe = sorted(ctx.value_of)
    subsets = [frozenset(c)
               for k in range(2, len(universe) + 1)
               for c in itertools.combinations(universe, k)]
    for hname, h in H_FUNCS.items():
        if all(_close(f(S, ctx), h(sf(S, ctx))) for S in subsets):
            return hname
    return None


def scope_m_reading(f: Callable, sf: Callable) -> dict[str, Any]:
    """**函数级**（语料无关）：这个读数在构造论域上能被 `摘要` 决定吗？

    返回 `{determined, pair, H, subsets, note}`。`note` 是给人读的一行。

    ⚠️ 这不是「在语料上搜见证」—— 语料一个节点都没进来。holistic 是**读数**
       的性质（Gray 等 1997 的三分法），该由读数自己回答；旧实现拿语料上的
       搜索结果判它 ⇒ 摘要单射的论域上恒红（openalex × `sequence`）。

    ⚠️ 边界（如实记）：构造论域对当前注册表六个读数**够用** —— 三个「被决定」
       的在此一对都找不出（且 `H` 点名了恒等式），三个「不被决定」的各有一个
       当场见证（见域的定义）。**将来**若注册一个「在此域上碰巧恒定、别处不然」
       的读数，这条判据会误判 ⇒ 新增读数时必须回来重审这个域。
    """
    ctx = synthetic_reading_ctx()
    d = decide_digest_witness(f, sf, ctx)
    if d["found"]:
        A, B = d["pair"]
        return {
            "determined": False, "pair": d["pair"], "H": None, "subsets": d["subsets"],
            "note": (f"函数级：构造论域上枚举 {d['subsets']} 个子集，当场找到见证 "
                     f"A={sorted(A)} B={sorted(B)}（`摘要` 同为 {sf(A, ctx)}，"
                     f"读数 {f(A, ctx)} ≠ {f(B, ctx)}）⇒ 这个摘要**分不开**"
                     f"所有论域"),
        }
    hname = _match_h(f, sf, ctx)
    return {
        "determined": True, "pair": None, "H": hname, "subsets": d["subsets"],
        "note": (f"函数级：构造论域上穷举 {d['subsets']} 个子集、**0 对**同摘要异读数"
                 f" ⇒ 该论域上 `读数` 与 `摘要` 恒定一致"
                 + (f"（== `H={hname}`）" if hname else "")),
    }

def judge_reading(
    name: str, category: str, decl: dict[str, Any],
    parts: dict[frozenset[str], tuple[frozenset[str], ...]], ctx: ReadingCtx,
) -> tuple[bool, str]:
    """判**一条**读数声明。返回 `(过?, 说明)`。`§A4` 与它的已知答案对照共用它。"""
    if name not in READINGS:
        return False, (f"读数 {name!r} 不在注册表里（只有 {sorted(READINGS)}）"
                       f"⇒ 声明**指不到实现**")
    f, summary = READINGS[name]

    if category in ("distributive", "algebraic"):
        if category == "distributive":
            gname = decl.get("G")
            if gname not in COMBINERS:
                return False, (f"`{name}` 声称 distributive，却没给出可用的 `G`"
                               f"（{gname!r}；只有 {sorted(COMBINERS)}）")
        else:
            sname, hname = decl.get("摘要"), decl.get("H")
            if sname not in SUMMARIES or hname not in H_FUNCS:
                return False, (f"`{name}` 声称 algebraic，却没给出可用的 `摘要` / `H`"
                               f"（{sname!r} / {hname!r}）")
        bad: list[str] = []
        judged = 0
        for block, ps in parts.items():
            if len(ps) < 2:
                continue                      # 叶：没有下层 ⇒ 这一块不判
            judged += 1
            lhs = f(frozenset().union(*ps), ctx)
            if category == "distributive":
                rhs = COMBINERS[str(gname)]([f(p, ctx) for p in ps])
                why = f"G={gname}"
            else:
                sf = SUMMARIES[str(sname)]
                acc = [0.0] * len(sf(frozenset(), ctx))
                for p in ps:
                    for i, x in enumerate(sf(p, ctx)):
                        acc[i] += x
                rhs = H_FUNCS[str(hname)](tuple(acc))
                why = f"摘要={sname}、H={hname}"
            if not _close(lhs, rhs):
                bad.append(f"{sorted(block)[:3]}…：实测 {lhs} ≠ 声称 {rhs}")
        if not judged:
            return True, "没有一块有下层 ⇒ 这条读数**没有内容**"
        return ((not bad), (f"{judged} 块逐块对上（{why}）" if not bad
                            else f"{len(bad)} 块对不上：{bad[:2]}"))

    if category == "holistic":
        w = decl.get("见证") or {}
        sname = w.get("摘要")
        if sname not in SUMMARIES:
            return False, (f"`{name}` 声称 holistic，却没附可用的**见证摘要**"
                           f"（{sname!r}；只有 {sorted(SUMMARIES)}）")
        sf = SUMMARIES[str(sname)]
        # ── ① **函数级**（语料无关）────────────────────────────────────────
        #    holistic 是**读数**的性质 ⇒ 先在构造论域上判它与摘要的关系。
        #    这一步的结论与当前语料**无关** —— 所以它**不该**由「当前语料上
        #    搜不到见证」来红（2026-10-13 修；旧实现的恒红就在这儿）。
        sm = scope_m_reading(f, sf)
        if sm["determined"]:
            hint = (f" —— 它**就是** `摘要` 经 `H={sm['H']}` 算出来的，"
                    f"该往 `algebraic` 挑（不是 holistic）"
                    if sm["H"] else "")
            return False, (f"`{name}` 声称 holistic，但这个读数**本就被 "
                           f"`{sname}` 决定**（{sm['note']}）⇒ 「无常数额摘要」"
                           f"在**函数级**上就不成立 —— 与这份语料无关{hint}")
        # ── ② **语料级**（本论域）──────────────────────────────────────────
        #    见证有**两种**给法（2026-10-10）：
        #      构造法 `{摘要, 构造}` —— 与语料无关，`§A4` 当场在语料上判一对
        #      显式   `{摘要, A, B}` —— 人挑的一对，绑语料（换语料必须重挑）
        builder = w.get("构造")
        d: dict[str, Any] | None = None
        if builder is not None:
            if builder not in WITNESS_BUILDERS:
                return False, (f"`{name}` 的见证构造法 {builder!r} 不在注册表里"
                               f"（只有 {sorted(WITNESS_BUILDERS)}）")
            d = decide_digest_witness(f, sf, ctx)
            if d["found"]:
                A, B = d["pair"]
            else:
                A = B = None
        else:
            A = frozenset(w.get("A") or ())
            B = frozenset(w.get("B") or ())
            if not A or not B:
                return False, (f"`{name}` 声称 holistic，却既没给**构造法**（`构造`）"
                               f"也没给见证的**两组方向**（`A` / `B`）")
        if A is None:
            # 函数级有支撑，而这份语料给不出语料级见证 ⇒ 按口径分别报。
            # ⚠️ 两种都**不是红**：函数级已过；语料级「0 对」是论域的性质，
            #    「超预算」是**没判** —— 拿它们当红，就是 openalex 那条恒红。
            n = len(ctx.value_of)
            if d["mode"] == "exhaustive":
                return True, (
                    f"{sm['note']}；语料级：本论域 {n} 个方向，穷举 {d['subsets']} "
                    f"个子集（2..{n} 元、{d['buckets']} 桶）、**0 对**同摘要异读数 "
                    f"⇒ 这份语料上 `{sname}` 对读数是**单射**的 ⇒ 「搜不到」是"
                    f"**论域的性质**，不是声称不成立")
            return True, (
                f"{sm['note']}；语料级**未判**：本论域 {n} 个方向 ⇒ 2^{n} 个子集"
                f"超出穷举预算 {WITNESS_ENUM_BUDGET} ⇒ 构造法只搜到 4 元为止、"
                f"无见证 ⇒ 按**函数级**结论判过，语料级证据未取得（如实披露）")
        unknown = sorted((A | B) - set(ctx.value_of))[:3]
        if unknown:
            return False, f"见证里的方向不在 `universe` 里：{unknown}"
        sa, sb = sf(A, ctx), sf(B, ctx)
        fa, fb = f(A, ctx), f(B, ctx)
        if sa != sb:
            return False, (f"见证**不成立**：`{sname}` 在 A / B 上本来就不同"
                           f"（{sa} vs {sb}）⇒ 它证明不了「这个摘要不够用」"
                           + ("　⚠️ 像是**从别的语料搬来的** —— 显式见证绑语料，"
                              "建议改成构造法 `同摘要异读数`" if builder is None else ""))
        if _close(fa, fb):
            return False, (f"见证**不成立**：`{sname}` 相同（{sa}）而读数**也相同**"
                           f"（{fa}）⇒ 这个摘要**够用** ⇒ 「holistic」没有支撑")
        how = (f"构造法 `{builder}` 当场搜到" if builder is not None else "声明的显式一对")
        return True, (f"{sm['note']}；语料级：见证成立（{how}）：A={sorted(A)} "
                      f"B={sorted(B)}；`{sname}` 两边都是 {sa}，而读数 {fa} ≠ {fb} "
                      f"⇒ 定长摘要分不开它们 ⇒ 「无常数额摘要」有支撑")

    return False, f"未知类别 {category!r}（只认 distributive / algebraic / holistic）"


def a4_category(
    parts: dict[frozenset[str], tuple[frozenset[str], ...]],
    ctx: ReadingCtx, decls: Sequence[dict[str, Any]], rep: Report,
) -> None:
    """`§A4` —— 逐条判 `view_spec.json` 里声明的读数。

    ⚠️ **两条「跳过」，都不许折成「过」**：

        没有一块有下层（全是叶）  ⇒ 判不了（设计稿 §9 的「视图没有子视图 ⇒ 跳过」）
        一条读数都没声明          ⇒ 判不了。**不许默认成 distributive** ——
                                    设计稿 §10 停止条件 2 逐字要求「停」。

    ⚠️ 一条读数声明都**指不到实现**（名字不在 `READINGS` 里）也判红：
       那说明声明是**写错了**，与「还没写」是两件事（同 `spec_for` 的纪律）。
    """
    title = "视图类别：distributive 的 `G` / holistic 的见证都要实测成立"
    if not any(len(ps) >= 2 for ps in parts.values()):
        rep.add("A4", title, Tri.UNEXPANDED,
                "没有一块有下层（全是叶）⇒ 判不了，**不是通过**（设计稿 §9）")
        return
    if not decls:
        rep.add("A4", title, Tri.UNEXPANDED,
                "**没有任何读数声明** ⇒ 判不了。不许默认成 distributive"
                "（设计稿 §10 停止条件 2：定不下来就**停**）")
        return
    bad: list[str] = []
    okd: list[str] = []
    for decl in decls:
        name = str(decl.get("名") or "")
        ok, why = judge_reading(name, str(decl.get("类别") or ""), decl, parts, ctx)
        (okd if ok else bad).append(f"{name}：{why}")
    rep.add("A4", title, Tri.NO if bad else Tri.YES,
            (f"{len(bad)}/{len(decls)} 条读数声明与实测不符：{bad[:2]}") if bad
            else f"{len(decls)} 条读数声明全部实测成立：{okd}")


# --- §A5 账逐条可指认 ---------------------------------------------------------
#
# Navlakha 的「摘要 `S` + 修正项 `C`」= 本设计的「视图 + 账」（设计稿 §6）。
# 本设计把「**有界**误差」读成「**账必须是真的**」：账**不许**是「剩下的那些」
# 这种含糊说法 —— 它必须**逐条可指认**。
#
# ⚠️ `§A5` **只判可指认**，不判完整性。完整性（「漏的恰好是账上那些」）
#    是 `B4` 已经在做的事；两条判同一件事会让「红」分不清是谁的。
#    ⇒ 这里判三件事，每一件都能单独红：
#
#        ① 每条账的方向**指得到某张视图**（`did ∈ ∪ block`）
#        ② 每条账的项**是语料里的项**
#        ③ 每条账**成立**：项真的在 `members(did)` 里，而**不在任何子方向的**里面
#           —— ③ 用的是**独立重算**的 oracle（从成员集现算），不是读账本自己的说法
#
# ⚠️ 账为空 ⇒ **跳过**（设计稿 §9）。判据的适用范围由「它在这条方向上有没有内容」
#    决定：批建路径上 `滞留 == 0`、`根覆盖之外 == 0` ⇒ 账**本来就该是空的**。
#    那不是「通过」，是「这一层没有东西要记账」。
#
# ★ 2026-10-12：这段原来**没写清是永久**，于是它读起来像「这趟没内容」——
#    而实测它在当前配置下**永远**为空（`a5_empty_reason()` 的 docstring 有表）。
#    跳过理由现在由 `a5_empty_reason()` 一处生成，把「为什么永久」印出来。


#: 账的两**类** —— 判定必须**按类分**，不许拿一类的话去判另一类。
#: ⚠️ 两句**互斥**、成立条件也不同：
#:    滞留         「项**留在这一层**」（在父的成员里，子方向都不收）
#:    根覆盖之外   「项**根本没进结构**」（在任何方向的成员里都不出现）
LEDGER_STAYED = "stayed"
LEDGER_OUT_OF_SCOPE = "out_of_scope"
LEDGER_KINDS = (LEDGER_STAYED, LEDGER_OUT_OF_SCOPE)


def warranted_of(kernel: Any, did: str, item: str,
                 kind: str = LEDGER_STAYED) -> bool:
    """**独立重算**的 oracle —— **按 `kind` 分两支**。

        类 `LEDGER_STAYED`        项真的落在 `did` 上，而且**没有**子方向收它
        类 `LEDGER_OUT_OF_SCOPE`  项**根本没进结构** ⇒ 它在**任何**方向的成员集里都不出现

    ⚠️ **从成员集现算**，不读账本自己的说法。拿账本当 oracle 会与被判对象
       共享盲点（两边同时错、判据永远绿 —— `false-green` 形状 3）。

    ⚠️ ★ **为什么必须分两支**（2026-10-12 查出的真缺陷）：原来**只有第一支**，
       而 `ledger_entries` 收**两类** ⇒ 「根覆盖之外」那类的项按定义**不在**
       `members(did)` 里（它就是「没进结构」）⇒ 第一支**恒判 False**
       ⇒ 那一类**恒红**。它一直没露，因为 ① 批建路径上两类都空（`§A5` 跳过）、
       ② 注入的基线账**只造了滞留类**（`test_injections._a5_account`）。

    ⚠️ **未知 `kind` 一律 False** —— 「写错了」与「还没写」是两件事，
       未知的类**不许**被当成通过（同 `spec_for` 的纪律）。
    """
    if kind == LEDGER_OUT_OF_SCOPE:
        # 声称「根本没进结构」⇒ 独立重算：**整个结构**里都不该找得到它。
        return all(item not in kernel.members_of(d)
                   for d in kernel.all_directions())
    if kind != LEDGER_STAYED:
        return False
    d = kernel.direction(did)
    if item not in kernel.members_of(d):
        return False
    return all(item not in kernel.members_of(c) for c in kernel.children_of(d))


def ledger_entries(kernel: Any, spec: ViewSpec) -> list[tuple[str, str, str, str]]:
    """把内核账本里「**视图答不出的那些**」抽成 `(方向, 项, 类, 理由)`。

    只取两类，因为它们才**带着一个具体的项**：

        滞留（`§10.2` 出路 (4)）  项在父的覆盖里，但**每个子方向都证明不收它**
        根覆盖之外（出路 (1)）    项按证明落在根的覆盖之外，**根本没进结构**

    ⚠️ ★ **第三栏「类」不是装饰**（2026-10-12）：两类的**成立条件不同**，
       oracle 必须按类分（`warranted_of` 的 `kind` 参数）。原来这一栏不存在，
       于是两类共用一条「项在 `members(did)` 里」的判法 ⇒
       「根覆盖之外」那类**恒红**。见 `warranted_of` 的 docstring。

    ⚠️ `born` / `witness_updated` / `unsplittable` **不取** —— 它们是**结构事件**，
       不带项。把它们混进来会让账里出现一堆「指不出项」的行，
       于是「账不可指认」这条判据**从第一天起就常驻红** ——
       而一条常驻的红等于没人再看红。

    ⚠️ 批建路径上这两类**本来就该是空的**（`滞留 == 0`、`根覆盖之外 == 0`），
       所以 `§A5` 在生产上会报**跳过**。那不是「通过」，是「这一层没有东西要记账」
       —— 它的红路由注入单独验（`test_injections.inj_a5` / `inj_a5_out_of_scope`）。
    """
    from ..core.direction import EVENT_OUT_OF_SCOPE

    out: list[tuple[str, str, str, str]] = []
    for did in spec.universe:
        for item in sorted(kernel.stayed_of(kernel.direction(did))):
            out.append((did, item, LEDGER_STAYED,
                        "滞留：每个子方向都证明不收它（§10.2 出路 (4)）"))
    for ev in kernel.ledger:
        if ev.kind == EVENT_OUT_OF_SCOPE:
            out.append((ev.did, str(ev.detail.get("item", "")), LEDGER_OUT_OF_SCOPE,
                        "根覆盖之外：按证明落在根的覆盖之外（§10.2 出路 (1)）"))
    return out


def a5_empty_reason() -> str:
    """账为空时 `§A5` 的**跳过理由** —— 必须说清这是**永久**的，不是本趟没内容。

    ⚠️ 为什么非说清不可（2026-10-12）：原来的理由只写「批建路径上账为空」，
       读起来像**这一趟**没查到东西。而实测（`outputs/_probe_skip_solvable.out.txt`，
       6 份语料 × 3 方向 × 2 路径）它在**当前配置下永远为空**：

           keyset    批建 0/0   维护 0/0     ← 精确切分 ⇒ 恒空（跨 6 份语料无一例外）
           reach     批建 0/0   维护 0/28    ← 有内容，但只在**维护路径**上
           sequence  批建 0/0   维护 16/0    ← 同上

       而视图层跑的是**批建**路径（`view_report` → `batch_kernel`），声明方向又是
       `keyset`（14 份 `view_spec.json` 全是）⇒ **两头都空**。

    ⚠️ 「跳过」本身不是错 —— 没有内容就该跳过。错的是**它一直跳过而没人发现**：
       一条能红的判据放着一辈子不判，与**没有这条判据**在效果上一样，
       而它在输出里长得像「守住了」。所以理由里**不许**只写「账为空」四个字就完事。
       （守它的断言在 `run_tests.test_views` ⑦；跨语料统计见
        `outputs/_恒定的过有多少条.md`。）
    """
    return (
        "账为空 ⇒ 判不了，**不是通过**（设计稿 §9）。⚠️ 而这不是「本趟没查到东西」——"
        "账的两类来源（滞留（§10.2 出路 (4)）/ 根覆盖之外（出路 (1)））"
        "**只在维护路径上产生**（批建的根声明为「覆盖一切」⇒ 根覆盖之外由构造为 0；"
        "实测 3 个方向 × 6 份语料批建都是 0/0），而视图层跑的是**批建**路径"
        "（`view_report` → `batch_kernel`）；本仓库 14 份 `view_spec.json` 声明的方向"
        "又都是**精确切分**（`keyset`）⇒ 账**恒空**（连维护路径上也是 0/0，实测）。"
        "⇒ 这条「跳过」在当前配置下是**永久的**（换语料也一样），不是本趟的。"
        "账非空的只有 `reach` / `sequence` 的**维护路径**（实测 28 / 50 / 68 / 23 / 130 条）；"
        "要让它真判得改视图层跑的方向或路径，而那是**外生决定**（设计稿 §10 停止条件 1），"
        "不由本判据代劳。红路由 `test_injections.inj_a5` 单独验。"
    )


def a5_ledger(
    entries: Sequence[tuple[str, str, str, str]],
    vs: ViewSet,
    warranted: Callable[[str, str, str], bool],
    corpus_items: frozenset[str],
    rep: Report,
    skip_reason: str = "",
) -> None:
    """`§A5` —— 账里的每一条都要**指得到**具体方向 + 项，且**成立**。

    `entries` 每条是 `(did, item, 类, 理由)`；`warranted(did, item, 类)` 是
    **独立重算**的 oracle（从成员集现算，不读账本）。理由字符串只用于**报告**，
    **不参与判定** —— 判定只认那三件事，因为「理由」是自由文本，判它等于判措辞。

    ⚠️ **「类」进判定，但不进「理由」**（2026-10-12）：两类账的成立条件不同
       （见 `warranted_of`），所以类必须**显式**传下去；而它由 `ledger_entries`
       产出、**不是**从理由字符串里解析出来的。

    `skip_reason` 账为空时报的理由；留空 ⇒ `a5_empty_reason()`（说清是**永久**跳过）。
    """
    title = "视图账：每条都要指得到具体方向 + 项，且独立重算下成立"
    if not entries:
        rep.add("A5", title, Tri.UNEXPANDED, skip_reason or a5_empty_reason())
        return
    in_views: set[str] = set()
    for v in vs.views:
        in_views |= set(v.block)
    bad: list[str] = []
    for did, item, kind, _why in entries:
        if did not in in_views:
            bad.append(f"{did}/{item}：方向 `{did}` **不在任何视图的块里** ⇒ 指不到视图")
        elif item not in corpus_items:
            bad.append(f"{did}/{item}：项 `{item}` **不是语料里的项**")
        elif not warranted(did, item, kind):
            why = (f"项不在 `members({did})` 里，或在某个子方向的成员里"
                   if kind == LEDGER_STAYED else
                   "项**进了结构**（出现在某个方向的成员里），"
                   "而这一类声称它**根本没进结构**")
            bad.append(f"{did}/{item}（类 `{kind}`）：独立重算**不成立** —— {why}")
    rep.add("A5", title, Tri.NO if bad else Tri.YES,
            (f"{len(bad)}/{len(entries)} 条指不到：{bad[:2]}") if bad
            else f"{len(entries)} 条账全部指得到方向与项，且独立重算下条条成立")


# --- §A6 落盘-读回 ------------------------------------------------------------


def a6_roundtrip(
    vs: ViewSet, kernel: Any, cover: Callable[[Any], frozenset[str]], plugin: Any,
    rep: Report, path: Any = None,
    to_d: Callable[[Any], dict] | None = None,
    from_d: Callable[..., Any] | None = None,
) -> None:
    """`§A6` —— 落盘 → 读回 → **再跑 `§A1`**；而且存档**不许带派生边**。

    三件事各能单独红：

        ① 读回的**权威边**（`spec` / `q` / `block`）必须与原来逐字相同
        ② 存档**不许带派生边**（`payload` / `concretization` / `covered`）
           —— 带了就是「各存一份」（`§10.2 D`），而**各存一份正是漂移唯一可能的来源**
        ③ 读回之后 `§A1` 仍然成立

    ⚠️ ②**不是洁癖**。派生边在盘上 ⇒ 「盘上的值」与「按 `§I2` 现算的值」变成
       **两个可以不一致的东西**，而它们本可以只有一个。
       ⇒ 这一条判的就是**「漂移不可能发生」这件事本身**，而不是等漂移了再去抓。
       与内核那边同形：`core/persist.py` 的 `_to_dict_storing_derived` 是**对照**，
       不是备选方案。

    ## `to_d` / `from_d` 是给**对照**用的口子，不是配置项

    默认走真实现（`view_persist.to_dict` / `from_dict`）。留这两个参数是为了让
    「**② 与 ③ 是两个分支**」这件事**能被实测**，而不是靠说 ——
    见 `a6_branch_split`：四条路各跑一次，逐条看哪个分支亮。

    ⚠️ 没有落盘 ⇒ **跳过**（设计稿 §9）。`run_checks` 会传一个临时路径，
       所以生产路径上这条**是跑的**；跳过那条路由测试单独验。
    """
    title = "视图落盘-读回：权威边逐字相同、存档不带派生边、读回后 §A1 仍成立"
    if path is None:
        rep.add("A6", title, Tri.UNEXPANDED,
                "没有落盘 ⇒ 判不了，**不是通过**（设计稿 §9）")
        return
    br = a6_branches(vs, kernel, cover, plugin, path, to_d=to_d, from_d=from_d)
    bad = br["①"] + br["②"] + br["③"]
    rep.add("A6", title, Tri.NO if bad else Tri.YES,
            (f"{len(bad)} 处：{bad[:2]}") if bad
            else f"{len(vs.views)} 张视图落盘-读回：权威边逐字相同、"
                 f"存档 {br['条数']} 条**无派生栏**、读回后 `§A1` 仍绿")


def a6_branches(
    vs: ViewSet, kernel: Any, cover: Callable[[Any], frozenset[str]], plugin: Any,
    path: Any, to_d: Callable[[Any], dict] | None = None,
    from_d: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """`§A6` 的三个分支**分开**报 —— 返回 `{"①": [...], "②": [...], "③": [...], "条数": n}`。

    ⚠️ 为什么要拆开而不是拼成一句话：`a6_branch_split` 要**逐分支**断言
       「只上 `to_d` 时 ② 亮而 ③ 不亮」。从**散文**里认分支是靠不住的
       （改一个措辞就悄悄失效）—— 判据的形状必须是**结构**，不是措辞。
    """
    from ..core import view_persist as vp

    to_d = to_d or vp.to_dict
    from_d = from_d or vp.from_dict
    path.write_text(json.dumps(to_d(vs), ensure_ascii=False, sort_keys=True, indent=2),
                    encoding="utf-8")
    stored = json.loads(path.read_text(encoding="utf-8"))
    read = from_d(stored, kernel, cover, plugin)

    b1: list[str] = []
    if read.spec != vs.spec or read.q != vs.q:
        b1.append("读回的 `spec` / `q` 与原来不同")
    got = {v.vid: v.block for v in read.views}
    want = {v.vid: v.block for v in vs.views}
    if got != want:
        b1.append(f"读回的块不同：{[k for k in want if got.get(k) != want[k]][:2]}")

    b2: list[str] = []
    derived = sorted({k for row in stored.get("视图") or ()
                      for k in row if k in ("payload", "concretization", "covered")})
    if derived:
        b2.append(f"存档带了**派生边** {derived} ⇒ 「各存一份」的形态 "
                  f"（漂移唯一可能的来源，`§10.2 D`）")

    b3: list[str] = []
    rep_a1 = Report(plugin="(视图·读回)", expects=("A1",))
    a1_soundness(read, rep_a1)
    a1_ok = next((a.result for a in rep_a1.assertions if a.code == "A1"), Tri.UNEXPANDED)
    if a1_ok is not Tri.YES:
        b3.append(f"读回后 `§A1` 不是绿的（{a1_ok}）—— 盘上的派生值与现算值**漂移了**")

    return {"①": b1, "②": b2, "③": b3, "条数": len(stored.get("视图") or ())}


# --- §A7 `E′` 增量传播 ---------------------------------------------------------
#
# 设计稿 §7：结构一动（流程 B），视图要么重算、要么**传播**。`E′` 的**定义**
# （本文档定下来，之前只有「只定接口不定实现」那句话）：
#
#     Q_ext = 旧 Q ∪ { 新方向各自成块 }
#     Q′    = csr(Q_ext)        ← `core/views.propagate`
#
# ⚠️ **`§A7` 判的是「划分」那两件事，不重复判 `§A1`。**
#    `§A7` 的两条都能红而 `§A1` 一条都判不到（`§A1` 只看见具体化与成员）；
#    反过来 `§A1` 判的健全性 `§A7` 也不判 —— 两条判同一件事会让「红」分不清是谁的
#    （`§A5` 的边界那条纪律，逐字同款）。
#
# ⚠️ **「传播 ≡ 重算」不在这里判。** 设计稿 §7 逐字：那是一句**会被实测打脸**的话，
#    而**打脸不破坏任何一条检查**（§10.2 B 已裁定）⇒ 它是**读数**
#    （`a7_reading`，进输出不进退出码），不是判据。

def a7_branches(
    old_q: Sequence[frozenset[str]],
    spec: ViewSpec,
    *,
    new_q: Sequence[frozenset[str]] | None = None,
) -> dict[str, Any]:
    """`§A7` 的**分支**（结构化返回）—— 判据从散文里认分支，改个措辞就失效。

    与 `a6_branches` 同一条理由：**分支要能被逐条断言**，所以它必须先是**结构**。

        ① 传播后不稳定        ② 传播后不细化 `Q_ext`
    """
    q_new = tuple(new_q) if new_q is not None else propagate(old_q, spec)
    q_ext = extend_q(old_q, spec)
    b1: list[str] = []
    b2: list[str] = []
    if spec.relation:
        ok, why = stable(spec, q_new)
        if not ok:
            b1.append(f"传播后**不稳定**（{len(why)} 对块）⇒ 新边改变了 `E⁻¹`，"
                      f"而受影响的块**没被重新检查**；反例：{why[:1]}")
    if not refines_partition(q_ext, q_new):
        b2.append(f"传播后**不细化 `Q_ext`**（{len(q_ext)} 块 ⇒ {len(q_new)} 块）"
                  f"⇒ 旧块被**重新合并**了 —— 那不是传播，是重算")
    return {"①": b1, "②": b2, "块数": len(q_new), "Q_ext": len(q_ext)}


# ⚠️ **标题只写一遍**（`A8_TITLE` 同一条理由）：判据的**标题**是「跑过」与「跳过」
#    之间的第一道可分性 —— 两处各写一遍的话，写着写着就会不一样，而
#    「跳过的标题与跑过的标题不同」正是让读者把「跳过」读成「另一条判据」的形状。
A7_TITLE = "视图传播 E′：传播后仍稳定，且只许细分（不许用重算冒充）"


def a7_skip(rep: Report, why: str) -> None:
    """`§A7` 报**跳过** —— 用**同一个标题**，只有 `why` 说明为什么。

    ⚠️ 与 `a8_skip` 同形，理由也一样：语料小到构造不出判据要的那个**前提**
    时，必须报「跳过（附理由）」而不是崩、也不是悄悄按别的东西判。
    """
    rep.add("A7", A7_TITLE, Tri.UNEXPANDED, why)


def a7_old_q(spec: ViewSpec) -> tuple[frozenset[str], ...] | None:
    """`§A7` 要的那次**结构变动**：把 `spec.universe` 里**最后长出来**的方向拿掉
    ⇒ 那就是「变动前」的划分。

    ⚠️ 选哪个方向是**任意**的（只要确定）—— 判据不依赖这个选择。
       实测四种选法（单个 / 最大块 / 全部叶）下「传播稳定」全为真、
       「重算 ⊑ `Q_ext`」全为假（`outputs/_probe_a7b.py`）。

    ⚠️ **返回 `None` ⇒ 这次结构变动不存在**，调用方要报**跳过**，不许硬凑：

        `restrict_spec` 是**拿掉** ⇒ `universe` 只有一个方向时，拿掉任何一个都会
        拿光整个 `universe` ⇒ `ValueError`。而「拿掉一个方向」正是这条判据
        要的那个**前提**：没有「变动前」，就没有「从旧结果出发」，也就没有传播。

    ⚠️ **生产路径与 `test_injections` 必须调这**一个**函数** —— 抄一份的做法
       实测已经漂过（`§A8` 那份副本原样保留了写死的 `universe[-13:]`，
       而生产那边已经改成按比例缩放了）。
    """
    if len(spec.universe) < 2:
        return None
    return coarsest_stable_refinement(
        restrict_spec(spec, frozenset({spec.universe[-1]})))


def a7_propagate(
    old_q: Sequence[frozenset[str]],
    spec: ViewSpec,
    rep: Report,
    *,
    new_q: Sequence[frozenset[str]] | None = None,
) -> None:
    """`§A7` —— `E′` 传播后的划分必须**仍稳定**、且**只许细分**。

    ## 两个红分支，各自指得出东西

        ① 传播后**不稳定**          ⇒ 红
           形状：把新方向**挂到父方向所在的那一块**上，然后**不重跑不动点**。
           新边 `(父, 新)` 把 `父` 塞进了 `E⁻¹({新})` ⇒ `{新}` 那一块
           **跨在内外**（`父` 在里面、同块的兄弟在外面）。`propagate_naive` 就是它。

        ② 传播后**不细化 `Q_ext`**  ⇒ 红
           形状：**用全量重算冒充传播**。重算从 `P′` 出发，会把「旧 Q 已经劈开、
           而 `P′` 层面看不出要劈」的那些块**重新合并回去** —— 实测四例全中
           （`重算 ⊑ Q_ext` 一律为假）。
           ⚠️ 这一条**不是**「重算不好」：重算是**另一个**合法对象（它就是 `§A3`
           要的那个唯一解）。红的是**把它叫做「传播」** —— 那是把
           「从旧结果出发」这个定义**换掉了**而没改文档。

    ⚠️ **② 的措辞是「细化 `Q_ext`」，不是「细化旧 `Q`」** —— 差的那一格正是
       ②唯一能红的地方。实测：`重算 ⊑ 旧 Q` 四例**全为真**（重算不合并旧块），
       而 `重算 ⊑ Q_ext` 四例**全为假**。用「细化旧 `Q`」当谓词，
       这条注入**一次都红不了**，判据会与空转长得一模一样。

    ⚠️ **`E′` 只在这里判「划分」；传播后视图的健全性仍归 `§A1`。**
       传 `new_q` 是给**对照**用的口子（`a7_known_answer` / `test_injections`），
       生产路径不传。

    ⚠️ **旧 `Q` 为空 ⇒ 跳过**（没有「从旧结果出发」这回事，那是重算）。
    """
    title = A7_TITLE
    if not old_q:
        rep.add("A7", title, Tri.UNEXPANDED,
                "没有旧视图 ⇒ 没有「传播」这回事（那是重算）—— 判不了，不是通过")
        return
    br = a7_branches(old_q, spec, new_q=new_q)
    bad = br["①"] + br["②"]
    rep.add("A7", title, Tri.NO if bad else Tri.YES,
            f"{len(bad)} 处：{bad[:2]}" if bad
            else f"从 {len(old_q)} 张旧视图出发 ⇒ 传播后 {br['块数']} 块，"
                 f"稳定、且逐块细化 `Q_ext`（{br['Q_ext']} 块）")


def a7_reading(old_q: Sequence[frozenset[str]], spec: ViewSpec) -> dict[str, Any]:
    """`E′` 的**读数** —— 进输出不进退出码（设计稿 §7：不同构是读数）。

    报三个数 + 两条布尔，缺一个都看不出「传播到底做了什么」：

        `旧/传播/重算`  三档块数 —— 「传播比重算细多少」在这里看得见
        `不同构`        传播 ≠ 重算 —— §7 说它**不成立**，这里把它**量出来**
        `传播⊑重算`     **定理的自检**（`core/views.propagate` 的 docstring 有证明）
                        —— 这条**不该为假**；为假说明 `propagate` 的实现错了，
                        而 `§A7` 的两条判据**都抓不到**它（两条都只看传播自己）
    """
    q_new = propagate(old_q, spec)
    q_rec = coarsest_stable_refinement(spec)
    return {
        "旧": len(old_q),
        "传播": len(q_new),
        "重算": len(q_rec),
        "不同构": partition_of(q_new) != partition_of(q_rec),
        "传播⊑重算": refines_partition(q_rec, q_new),
    }


def render_propagation(prof: dict[str, Any], spec: ViewSpec) -> str:
    return (f"E′ 传播（把 `{spec.universe[-1]}` 拿掉再长回来）："
            f"旧 {prof['旧']} 张 ⇒ 传播 {prof['传播']} 张 vs 重算 {prof['重算']} 张"
            f"｜不同构 {prof['不同构']}｜传播 ⊑ 重算 {prof['传播⊑重算']}"
            f"（**不同构是读数不是红** —— 设计稿 §7；定理保证传播只会更细）")


# --- §A8 认识的**持续**维护 ----------------------------------------------------
#
# 设计稿 §7.4：结构一动，**认识**也要跟上 —— 否则结构层与检索层之间就只有
# **一次快照**，而不是「持续影响」。`§A7` 判的是**一次**传播播得对不对；
# `§A8` 判的是**连着维护**时会不会把粗粒度磨光（`maintain` 的政策对不对）。
#
# ⚠️ **为什么这条必须有**（`基线§7.3` 自己写的 + 2026-10-10 实测）：
#    `E′` 传播**只细化、不重新合并** ⇒ 反复传播一路细化到「一个方向一张视图」：
#
#        真语料逐项插入、每步只传播：末态 |Q| = 25 = |U| ⇒ shrink = **1.000**
#        同一条轨迹上重算始终 8 块 ⇒ 0.320
#
#    ⇒ 而那时 **`T1` 照样绿**（候选集本来就与 `Q` 无关）⇒
#      **退化与「正常」在只看判据时长得一模一样** —— 这正是本仓库的中心形状。
#      ⇒ 所以它必须**有一条自己的判据**，而不是指望别处能看出来。

def a8_branches(
    spec: ViewSpec,
    *,
    seed: Sequence[frozenset[str]] | None = None,
    result: Any = None,
) -> dict[str, Any]:
    """`§A8` 的分支（结构化返回）—— 与 `a6_branches` / `a7_branches` 同一条理由。

        ① **交付物不是 `csr`**        ⇒ 红（★ 它会让 `§A3` 的可判性退化，见下）
        ② **交付物不稳定 / 不细化 `P`** ⇒ 红（`§K8` 那一侧）
        ③ **动作与实不符**（报「传播」而两条路并不同构）⇒ 红（那是**谎报**）
    """
    m = result if result is not None else maintain(seed, spec)
    b1: list[str] = []
    b2: list[str] = []
    b3: list[str] = []
    q = list(m.q)
    q_csr = coarsest_stable_refinement(spec)
    # ① 交付物必须**逐块等于** `csr`
    #     ⚠️ 这一条**不是**在重复 `§A3`：`§A3` 判「有没有更粗的稳定划分」，
    #       而本条的**红形态**恰恰是「`§A3` 变成判不了」——
    #       实测：12 块 ⇒ 候选 4213597 > 上限 ⇒ `§A3` 报**跳过**，
    #       而「跳过 ≠ 通过」⇒ 那条承重判据**瞎了**。
    if partition_of(q) != partition_of(q_csr):
        b1.append(f"交付物**不是 `csr`**（{len(q)} 块 vs 最粗 {len(q_csr)} 块）"
                  f"⇒ `§A3` 的候选数随块数**组合爆炸** ⇒ 从**可判**退化成**判不了**"
                  f"（实测 8 块 4140 个候选 ⇒ 判得了；12 块 4213597 ⇒ 跳过）")
    if spec.relation:
        ok, why = stable(spec, q)
        if not ok:
            b2.append(f"交付物**不稳定**（{len(why)} 对块）：{why[:1]}")
    if not refines(spec, q):
        b2.append("交付物**不细化 `P`** ⇒ 比声明还粗 ⇒ 不在 `csr` 上升链上")
    if m.action == "传播" and not m.propagate_equal:
        b3.append("报**传播**，但两条路**并不同构**（`propagate ≠ csr`）")
    if m.action == "重建" and m.propagate_equal:
        b3.append("报**重建**，但两条路**同构** ⇒ 这条记录与实不符（读数会误导）")
    return {"①": b1, "②": b2, "③": b3, "动作": m.action, "块数": len(q),
            "最粗": len(q_csr), "shrink": m.shrink,
            "只传播会": m.shrink_if_propagated, "同构": m.propagate_equal}


#: `§A8` 那条断言的标题 —— **单独提出来**，因为「判不了 ⇒ 跳过」那条路也要用同一句。
#: 两处各写一遍会漂，而漂了之后「跳过」与「判过」在输出里就分不清了。
A8_TITLE = "认识维护：交付物必须是 `csr`（不然 `§A3` 的可判性会退化——实测 12 块即超上限）"

#: 两个夹具各「拿掉几个方向」的**比例** —— 见 `a8_fixture_drops`。
A8_LIGHT_FRACTION = 0.08
A8_HEAVY_FRACTION = 0.52


def a8_fixture_drops(n_universe: int) -> tuple[int, int] | None:
    """`§A8` 两个夹具各拿掉几个方向。返回 `None` = **构造不出来**（语料太小）。

    ## 为什么不能写死 2 / 13

    那两个数是在 **25 个方向**的语料上挑的。`restrict_spec(spec, drop)` 是**拿掉**
    `drop`，所以 `universe[-13:]` 在 `|U| <= 13` 的语料上会把**整个** `universe`
    都拿掉 ⇒ 抛 `ValueError("投影之后 `universe` 空了 —— 那不是一份声明")`
    ⇒ `run_checks` **整趟崩** —— 而崩比红更糟：红是判据说话，崩是**判据没了**
    （`(视图)` 之后的四组与汇总行一个都不打印）。实测：`arxiv-taxonomy`(13) /
    `gh-langs`(13) / `edge-zero`(11) / `edge-tiny`(5) / `edge-single`(1)
    五份语料**全部**崩在那一行。

    ⇒ 改成按比例缩放。**25 个方向上仍然是 2 与 13**（逐字不变）：

        0.08 × 25 = 2.0 → 2        0.52 × 25 = 13.0 → 13

    ⚠️ 语料小到两个夹具**退化成同一个**时返回 `None` —— 那时「甲/乙」是同一个种子，
       而这条判据的**全部意义**就是「不论传播看起来多划算，交付物都得是 `csr`」；
       只配一个种子会把它读成「超预算才要重建」（那是**上一版错的那个政策**，
       见 `a8_maintenance` 的 docstring）。⇒ 那种语料上报**跳过**，不报通过。
    """
    light = max(1, round(n_universe * A8_LIGHT_FRACTION))
    heavy = max(light + 1, round(n_universe * A8_HEAVY_FRACTION))
    heavy = min(heavy, n_universe - 1)          # 至少留一个方向
    if light >= heavy:
        return None
    return light, heavy


def a8_skip(rep: Report, why: str) -> None:
    """`§A8` 判不了时**报跳过** —— 与「通过」分开（跳过 ≠ 通过）。"""
    rep.add("A8", A8_TITLE, Tri.UNEXPANDED, why)


def a8_maintenance(
    fixtures: Sequence[tuple[str, Sequence[frozenset[str]] | None]],
    spec: ViewSpec,
    rep: Report,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> None:
    """`§A8` —— **交付的认识必须是 `csr`**（否则「最粗」这条承重性质变成判不了）。

    ## 它判什么、为什么它不重复 `§A3`

        `§A3` 判「**这一份 `q`** 有没有更粗的稳定划分」。
        `§A8` 判「**交给下一层的**那一份是不是 `csr`」——
        两者的**红形态不同**：`§A3` 红了是「有更粗的」，
        而本条的典型红形态是「**`§A3` 变成跳过**」（候选数爆炸）。

    ## 两个夹具

        甲  `seed` 来自「只拿掉 2 个方向」⇒ 传播在预算内，但**仍交付 `csr`**
        乙  `seed` 来自「拿掉 13 个方向」⇒ 传播严重退化（shrink 0.920）

    ⚠️ **两个都要**：命题是「**不论传播看起来多划算，交付物都得是 `csr`**」，
       只配乙会把它读成「超预算才要重建」—— 那是**上一版错的那个政策**。

    ⚠️ **`overrides` 是对照口子**（`test_injections` 用），生产路径不传。
    """
    title = A8_TITLE
    ov = dict(overrides or {})
    rows: list[str] = []
    bad: list[str] = []
    for name, seed in fixtures:
        r = ov.get(name)
        br = a8_branches(spec, seed=seed, result=r)
        hits = br["①"] + br["②"] + br["③"]
        bad += [f"[{name}] {x}" for x in hits]
        rows.append(f"{name} {br['块数']} 块 shrink {br['shrink']:.3f}"
                    f"（只传播 {br['只传播会']:.3f}／同构 {br['同构']}）")
    rep.add("A8", title, Tri.NO if bad else Tri.YES,
            f"{len(bad)} 处：{bad[:2]}" if bad
            else f"{'｜'.join(rows)}｜交付物 == `csr` ⇒ `§A3` 保持可判")


def a8_reading(fixtures: Sequence[tuple[str, Any]], spec: ViewSpec) -> dict[str, Any]:
    """`§A8` 的**读数**（进输出，不进退出码）—— 传播这条路**这一趟值不值**。"""
    out: list[str] = []
    n_same = 0
    for name, seed in fixtures:
        m = maintain(seed, spec)
        if m.propagate_equal:
            n_same += 1
        out.append(f"{name}：只传播 {m.shrink_if_propagated:.3f}"
                   f"／交付 {m.shrink:.3f}{'（同构）' if m.propagate_equal else ''}")
    return {"明细": out, "同构次数": n_same}


def render_maintenance(prof: dict[str, Any], spec: ViewSpec) -> str:
    return ("认识的持续维护（`§A8`）：" + "｜".join(prof["明细"])
            + f"｜两条路**同构** {prof['同构次数']} 次"
            + "（⚠️ 交付物**总是** `csr` —— 传播结果更细，会让 `§A3` 判不了）")


# --- 已知答案的对照组（合成图，三态各一例） ------------------------------------

def known_answer_specs() -> list[dict[str, Any]]:
    """合成图上的**已知答案** —— 设计稿 §9 要求的「先跑三态，再上真语料」。

    每一条都写清**答案是什么**（`want`），因为「跑一遍看看」不构成对照：

        过      `P = {{a,b},{c,d}}`、`E = {(a,c)}` ⇒ `Q = {{a},{b},{c,d}}`
                —— 手推得到，而且 `stable` 与 `coarsest` 都成立
        红·不粗  同一个 spec，喂**离散** `Q` ⇒ 稳定但**不是最粗** ⇒ `§A3` 红
        红·不稳  同一个 spec，喂 `P` 自己 ⇒ `{a,b}` 跨在 `E⁻¹({c,d})` 内外 ⇒ `§A2` 红
        跳过    `E = ∅` ⇒ 两条判据都**没有内容** ⇒ 跳过（不是过）

    ⚠️ 「离散划分**总是**稳定」是个小定理：单元素块 `{x}` 对任何 `E⁻¹(B)`，
       要么整个在里面（`x` 在里面）、要么整个在外面。⇒ 它是「稳定但通常不是最粗」
       的**最便宜**的那一档，`§A3` 的注入就用它。
    """
    U = ("a", "b", "c", "d")
    p2 = (frozenset({"a", "b"}), frozenset({"c", "d"}))
    e = frozenset({("a", "c")})
    disc = tuple(frozenset({x}) for x in U)
    return [
        {"label": "过·真最粗稳定细化",
         "spec": ViewSpec(universe=U, partition=p2, relation=e),
         "q": coarsest_stable_refinement(ViewSpec(universe=U, partition=p2, relation=e)),
         "want": {"A2": Tri.YES, "A3": Tri.YES}},
        {"label": "红·不是最粗（离散划分：稳定但更细）",
         "spec": ViewSpec(universe=U, partition=p2, relation=e),
         "q": disc,
         "want": {"A2": Tri.YES, "A3": Tri.NO}},
        {"label": "红·不稳定（喂 `P` 自己）",
         "spec": ViewSpec(universe=U, partition=p2, relation=e),
         "q": partition_of(p2),
         "want": {"A2": Tri.NO, "A3": Tri.NO}},
        {"label": "跳过·`E` 为空（两条判据都没有内容）",
         "spec": ViewSpec(universe=U, partition=p2, relation=frozenset()),
         "q": partition_of(p2),
         "want": {"A2": Tri.UNEXPANDED, "A3": Tri.UNEXPANDED}},
    ]


def known_answer_controls() -> list[str]:
    """跑合成对照组，返回**失败清单**（空 = 全对）。给 `run_tests` 用。"""
    fails: list[str] = []
    for case in known_answer_specs():
        rep = Report(plugin="(合成)")
        a2_stable(case["spec"], case["q"], rep)
        a3_coarsest(case["spec"], case["q"], rep)
        for code, want in case["want"].items():
            got = _result(rep, code)
            if got is not want:
                fails.append(f"{case['label']}：`{code}` 期望 {want}，实测 {got}")
    return fails


def a1_known_answer(dropped: bool) -> Tri:
    """`§A1` 的合成对照 —— 手工造两张视图，`dropped=True` 时掐掉一项。

    ⚠️ 这里**不碰内核**：`§A1` 判的是「具体化 ⊇ 声明收着的」，
       与内核无关，所以对照也不该依赖内核（依赖了就不是最便宜的那一档）。
    """
    spec = ViewSpec(universe=("d1", "d2"), partition=(frozenset({"d1", "d2"}),),
                    relation=frozenset({("d1", "d2")}))
    v = View(vid="V0", block=frozenset({"d1", "d2"}),
             concretization=frozenset({"i1", "i2", "i3"}),
             covered=frozenset({"i1", "i2"}))
    if dropped:
        v = replace(v, concretization=frozenset({"i1"}))
    rep = Report(plugin="(合成)")
    a1_soundness(ViewSet(spec=spec, q=(frozenset({"d1", "d2"}),), views=(v,)), rep)
    return _result(rep, "A1")


# --- `§A4`–`§A6` 的合成对照 --------------------------------------------------
#
# ⚠️ 与 `a1_known_answer` 同一条纪律：**不碰内核、不碰语料**。
#    这里要对照的是「判据会不会红」，不是「真语料上是什么数」——
#    后者由 `run_tests.test_view_*` 读真语料跑。两件事分开，各自说清。
#
# ⚠️ **合成图上的读数必须是手推得出来的**（写在 `want` 旁边）。
#    「跑一遍看看」不构成对照：它只会把实现现在的行为固化成「期望」。

#: `§A4` 合成对照用的方向 → 数值 / 覆盖。挑这五个数是为了**手推得动**：
#:
#:     {a,b,c} = {1,2,5}   和 8   计数 3   最大 5   平均 8/3   中位数 2
#:     {a,b}   = {1,2}     和 3   计数 2   最大 2   平均 1.5   中位数 1.5
#:     {c}     = {5}       和 5   计数 1   最大 5   平均 5     中位数 5
#:
#: ⇒ `计数` / `最大` / `平均` 三条**手推就该过**；而
#:   `中位数` 若被标成 `distributive`（`G=取平均`）⇒ `2 ≠ (1.5+5)/2 = 3.25` ⇒ **该红**。
_A4_VALUES = {"a": 1.0, "b": 2.0, "c": 5.0, "d": 3.0, "e": 4.0}
_A4_COVERS = {
    "a": frozenset({"i1"}), "b": frozenset({"i2"}), "c": frozenset({"i2", "i3"}),
    "d": frozenset({"i3"}), "e": frozenset({"i4"}),
}


def _a4_parts() -> tuple[dict[frozenset[str], tuple[frozenset[str], ...]], ReadingCtx]:
    ctx = ReadingCtx(value_of=dict(_A4_VALUES), cover_of=dict(_A4_COVERS))
    block = frozenset({"a", "b", "c"})
    parts = {block: (frozenset({"a", "b"}), frozenset({"c"}))}
    return parts, ctx


def _a4_inj_parts() -> tuple[dict[frozenset[str], tuple[frozenset[str], ...]], ReadingCtx]:
    """「摘要**单射**」的论域 —— 值取 2 的幂。

    任两个不同子集的和都不同（二进制唯一）⇒ `(计数, 和)` 对**任何**读数都单射
    ⇒ 这里**搜不到**任何 holistic 见证 —— 正是 openalex × `sequence`（9 个方向、
    穷举 512 个子集、0 对）的**微型复现**。旧实现在这里判红；新实现按两级口径
    判：函数级有支撑 ⇒ 过 + 披露「论域的性质」。
    """
    values = {"p1": 1.0, "p2": 2.0, "p4": 4.0, "p8": 8.0, "p16": 16.0}
    covers = {d: frozenset({d, "z"}) for d in values}
    ctx = ReadingCtx(value_of=values, cover_of=covers)
    block = frozenset(values)
    parts = {block: (frozenset({"p1", "p2", "p4"}), frozenset({"p8", "p16"}))}
    return parts, ctx


def a4_inj_ctx() -> ReadingCtx:
    """`_a4_inj_parts()` 的**只取 ctx** 版 —— 已知答案对照直接调 `judge_reading`。"""
    return _a4_inj_parts()[1]


#: `(声明, 域, 手推的答案)` —— `want` 是**手推**出来的，不是跑出来抄的。
#: 域：`prod` = `_A4_VALUES` 那张表；`inj` = `_a4_inj_parts()`（摘要单射）。
_A4_CASES: list[tuple[dict[str, Any], str, bool]] = [
    ({"名": "计数", "类别": "distributive", "G": "取和"}, "prod", True),
    ({"名": "最大", "类别": "distributive", "G": "取最大"}, "prod", True),
    ({"名": "平均", "类别": "algebraic", "摘要": "计数与和", "H": "和除计数"}, "prod", True),
    # ↓ 这两条是**红的对照**：「类别说错了」与「指不到实现」各一例。
    ({"名": "中位数", "类别": "distributive", "G": "取平均"}, "prod", False),
    ({"名": "不存在的读数", "类别": "distributive", "G": "取和"}, "prod", False),
    # ↓ holistic **函数级有支撑**（构造论域上见证存在）⇒ 过
    ({"名": "中位数", "类别": "holistic", "摘要": "计数与和",
      "见证": {"摘要": "计数与和", "A": ["a", "b", "c"], "B": ["a", "d", "e"]}}, "prod", True),
    # ↓ 同一个见证换成一个集合：摘要相同、读数**也**相同 ⇒ 证不了什么 ⇒ 红
    ({"名": "中位数", "类别": "holistic", "摘要": "计数与和",
      "见证": {"摘要": "计数与和", "A": ["a", "b", "c"], "B": ["b", "a", "c"]}}, "prod", False),
    # ↓ holistic **函数级被反驳**：读数本就被摘要决定 ⇒ 红（与语料无关）。
    #   故意放在 `inj` 域（那是「语料级搜不到」的论域）—— 旧实现只会在那里
    #   因为搜不到而红；新实现必须在**任何**论域上都红。
    ({"名": "平均", "类别": "holistic", "摘要": "计数与和",
      "见证": {"摘要": "计数与和", "构造": "同摘要异读数"}}, "inj", False),
    ({"名": "计数", "类别": "holistic", "摘要": "计数与和",
      "见证": {"摘要": "计数与和", "构造": "同摘要异读数"}}, "inj", False),
    # ↓ 摘要单射的论域（openalex 恒红的微型复现）：函数级有支撑 ⇒ **过**
    ({"名": "中位数", "类别": "holistic", "摘要": "计数与和",
      "见证": {"摘要": "计数与和", "构造": "同摘要异读数"}}, "inj", True),
    ({"名": "覆盖计数", "类别": "holistic", "摘要": "计数与和",
      "见证": {"摘要": "计数与和", "构造": "同摘要异读数"}}, "inj", True),
]


def a4_known_answer() -> list[str]:
    """`§A4` 的合成对照。返回**失败清单**（空 = 全对）。

    手推依据（`_A4_VALUES` 那张表）：`{a,b,c} = {1,2,5}`。

        `计数`+`取和`       3 == 2 + 1                        ⇒ 过
        `最大`+`取最大`     5 == max(2, 5)                    ⇒ 过
        `平均`+`和除计数`   8/3 == (3+5)/(2+1)                ⇒ 过
        `中位数`+`取平均`   2 ≠ (1.5+5)/2 = 3.25              ⇒ 红（类别说错了）
        `中位数` holistic   见证 `{a,b,c}` vs `{a,d,e}`：
                            摘要都是 `(3, 8)`，中位数 2 ≠ 3    ⇒ 过
        同一个见证换成 `{a,b,c}` vs `{b,a,c}`：
                            摘要相同、读数**也**相同 ⇒ 证明不了什么 ⇒ 红

    **函数级**（2026-10-13 新增；构造论域 `_A4_SYNTH_VALUES` 六个值）：

        `中位数`    `{s1,s2,s3}={1,2,6}` vs `{s1,s4,s5}={1,3,5}`：
                    摘要同为 `(3,9)`，中位数 2 ≠ 3            ⇒ 不被摘要决定
        `覆盖计数`  `{s1,s3}` vs `{s2,s5}`：摘要同为 `(2,7)`，
                    覆盖 `{a..f}`=6 ≠ `{a..e}`=5             ⇒ 不被摘要决定
        `平均`      == `H=和除计数`（57 个子集逐一对上）      ⇒ **被决定** ⇒ 红
        `计数`      == `H=取第一项`                           ⇒ **被决定** ⇒ 红

    **语料级**（`inj` 域，五个 2 的幂）：任意两个不同子集的和都不同
        ⇒ `(计数,和)` 单射 ⇒ 0 对 ⇒ 中位数 / 覆盖计数 holistic **过**
        （旧实现在此判红 —— openalex 恒红的微型复现）。
    """
    fails: list[str] = []
    for decl, dom, want in _A4_CASES:
        parts, ctx = _a4_inj_parts() if dom == "inj" else _a4_parts()
        got, why = judge_reading(str(decl["名"]), str(decl["类别"]), decl, parts, ctx)
        if got is not want:
            fails.append(f"{decl['名']}/{decl['类别']}@{dom}：期望 {'过' if want else '红'}，"
                         f"实测 {'过' if got else '红'} —— {why}")
    # ★ 还有两条**跳过**（设计稿 §9）：没有下层、以及一条读数都没声明。
    #   跳过**不是过** —— 这里用 `Tri` 直接比，比布尔更严。
    parts, ctx = _a4_parts()
    rep = Report(plugin="(合成)")
    a4_category({frozenset({"a"}): (frozenset({"a"}),)}, ctx,
                [d for d, _, _ in _A4_CASES], rep)
    if _result(rep, "A4") is not Tri.UNEXPANDED:
        fails.append(f"全是叶 ⇒ `§A4` 该跳过，实测 {_result(rep, 'A4')}")
    rep = Report(plugin="(合成)")
    a4_category(parts, ctx, [], rep)
    if _result(rep, "A4") is not Tri.UNEXPANDED:
        fails.append(f"一条读数都没声明 ⇒ `§A4` 该跳过，实测 {_result(rep, 'A4')}")
    return fails


def a5_known_answer(dropped: bool) -> Tri:
    """`§A5` 的合成对照 —— 一条**手推该成立**的账；`dropped=True` 时方向指不到。

    手推：`members(D1) = {x1, x2}`、`members(D2) = {x1}` ⇒
        账 `(D1, x2)`：`x2` 在 `members(D1)` 里、**不在任何子方向的**里面 ⇒ 成立
        账 `(ZZ, x2)`：`ZZ` 不在任何视图的块里 ⇒ 指不到 ⇒ 红
    """
    spec = ViewSpec(universe=("D1", "D2"), partition=(frozenset({"D1", "D2"}),),
                    relation=frozenset({("D1", "D2")}))
    vs = ViewSet(spec=spec, q=(frozenset({"D1", "D2"}),),
                 views=(View(vid="V0", block=frozenset({"D1", "D2"})),))
    members = {"D1": frozenset({"x1", "x2"}), "D2": frozenset({"x1"})}

    def warranted(did: str, item: str, kind: str = LEDGER_STAYED) -> bool:
        if kind != LEDGER_STAYED:
            return False
        kids: set[str] = set()
        for d, ms in members.items():
            if d != did:
                kids |= set(ms)
        return item in members.get(did, frozenset()) and item not in kids

    entries = [(("ZZ" if dropped else "D1"), "x2", LEDGER_STAYED, "手推：D2 不收它")]
    rep = Report(plugin="(合成)")
    a5_ledger(entries, vs, warranted, frozenset({"x1", "x2", "x3"}), rep)
    return _result(rep, "A5")


class _MiniDir:
    """最小替身：`warranted_of` 只读 `.did`。"""

    def __init__(self, did: str) -> None:
        self.did = did


class _MiniKernel:
    """最小替身：只提供 `warranted_of` 在**根覆盖之外**那一支真正调的两个访问器。

    ⚠️ 不冒充内核 —— 只给这一条对照用得着的东西（同 `§A6` 两个替身的纪律）。
    """

    def __init__(self, members: dict[str, frozenset[str]]) -> None:
        self._m = members
        self._dirs = tuple(_MiniDir(did) for did in members)

    def all_directions(self) -> tuple[_MiniDir, ...]:
        return self._dirs

    def members_of(self, d: _MiniDir) -> frozenset[str]:
        return self._m.get(d.did, frozenset())


def a5_out_of_scope_known_answer(dropped: bool) -> Tri:
    """`§A5` 的**第二类**合成对照 —— 账里一条「根覆盖之外」。

    手推：结构里只有 `x1`（`members(D1) = {x1}`）⇒

        账 `(D1, x2, 根覆盖之外)`：`x2` 在**任何**方向的成员里都不出现 ⇒ 成立 ⇒ 绿
        账 `(D1, x1, 根覆盖之外)`：`x1` **进了结构**，与「根本没进结构」矛盾 ⇒ 红

    ⚠️ ★ **为什么非要单独一条**（2026-10-12）：`a5_known_answer` 只覆盖**滞留**类，
       而「根覆盖之外」那一类**恒判 False** 的缺陷，正是「只验一类」漏掉的。
       **两类各一条，缺一条就会重犯。**
    """
    spec = ViewSpec(universe=("D1",), partition=(frozenset({"D1"}),),
                    relation=frozenset())
    vs = ViewSet(spec=spec, q=(frozenset({"D1"}),),
                 views=(View(vid="V0", block=frozenset({"D1"})),))
    kernel = _MiniKernel({"D1": frozenset({"x1"})})
    entries = [("D1", "x1" if dropped else "x2", LEDGER_OUT_OF_SCOPE,
                "手推：根本没进结构")]
    rep = Report(plugin="(合成)")
    a5_ledger(entries, vs,
              lambda did, item, kind: warranted_of(kernel, did, item, kind),
              frozenset({"x1", "x2"}), rep)
    return _result(rep, "A5")


# --- `§A6` 合成对照要的两个最小替身 ------------------------------------------
#
# ⚠️ 它们**只**提供 `§A6` 真正用到的那几个访问器，不冒充内核。
#    用真内核要建一棵树、跑一遍语料，而这里要对照的是「落盘-读回这条判据会不会红」。
#    ⇒ 与 `a1_known_answer` 同一条纪律：**最便宜的那一档**。

class _StubPlugin:
    """最小插件：`合并` = 把各方向的 payload 并起来（`§I2` 的契约里最弱的一种）。"""

    def merge(self, dirs: Any) -> frozenset[str]:
        out: set[str] = set()
        for d in dirs:
            out |= set(d.payload)
        return frozenset(out)


class _StubKernel:
    """最小内核：只给 `from_dict` / `§A1` 要的 `direction` / `members_of`。"""

    def __init__(self, dirs: tuple[Any, ...], members: dict[str, frozenset[str]]) -> None:
        self._d = {d.did: d for d in dirs}
        self._m = members

    def direction(self, did: str) -> Any:
        return self._d[did]

    def members_of(self, d: Any) -> frozenset[str]:
        return self._m[d.did]

    def children_of(self, d: Any) -> tuple[Any, ...]:
        return ()


def _a6_fixture() -> tuple[Any, Any, Any, Any]:
    """`§A6` 合成对照的**坏** `vs` + 最小替身。返回 `(vs, kernel, cover, plugin)`。

    ⚠️ `vs` 是**故意坏的**：`block = {d1, d2}`、`covered = {i1, i2}`，
       而 `concretization = {i1}` —— 它自己就违反 `§A1`（`{i1,i2} ⊄ {i1}`）。
       坏输入是这里的关键：**真实现会把它修好**（读回时按 `§I2` 现算），
       而「存了派生边又信任它」的那条路会把它**原样搬回来**。
    """
    from ..core.direction import ORIGIN_EXOGENOUS, Direction

    d1 = Direction(did="d1", rank=1, payload=frozenset({"i1"}), witness=(),
                   origin=ORIGIN_EXOGENOUS, parent=None)
    d2 = Direction(did="d2", rank=2, payload=frozenset({"i2"}), witness=("d1",),
                   origin="split", parent="d1")
    kernel = _StubKernel((d1, d2), {"d1": frozenset({"i1", "i2"}),
                                    "d2": frozenset({"i2"})})
    spec = ViewSpec(universe=("d1", "d2"), partition=(frozenset({"d1", "d2"}),),
                    relation=frozenset({("d1", "d2")}))
    vs = ViewSet(spec=spec, q=(frozenset({"d1", "d2"}),),
                 views=(View(vid="V0", block=frozenset({"d1", "d2"}),
                             concretization=frozenset({"i1"}),
                             covered=frozenset({"i1", "i2"})),))
    return vs, kernel, (lambda p: frozenset(p)), _StubPlugin()


def _a6_br(**kw: Any) -> dict[str, Any]:
    import tempfile

    vs, kernel, cover, plugin = _a6_fixture()
    with tempfile.TemporaryDirectory() as td:
        return a6_branches(vs, kernel, cover, plugin, Path(td) / "views.json", **kw)


def a6_known_answer(drifted: bool) -> Tri:
    """`§A6` 的合成对照 —— 同一个 `vs`，真实现 ⇒ 过，两个对照实现一起上 ⇒ 红。

    手推（`_a6_fixture` 那个坏 `vs`）：

        真实现            盘上**没有** `concretization` ⇒ 读回时按 `§I2` 现算
                          ⇒ 算出来是 `{i1,i2}` ⇒ `§A1` 绿 ⇒ **过**
                          （「落盘只存权威边」买到的东西：**盘上的错东西修得回来**）
        两个对照都上      盘上存了 `{i1}`、读回时信任它 ⇒ 算出来还是 `{i1}`
                          ⇒ `§A1` 红 ⇒ **红**

    ⚠️ 分支的分工由 `a6_branch_split` 逐条实测 —— 这一条只判**红不红**。
    """
    from ..core import view_persist as vp

    kw: dict[str, Any] = ({"to_d": vp._to_dict_storing_derived,
                           "from_d": vp._from_dict_trusting_derived} if drifted else {})
    br = _a6_br(**kw)
    return Tri.NO if (br["①"] + br["②"] + br["③"]) else Tri.YES


def a6_branch_split() -> list[str]:
    """验「② 与 ③ 是**两个**分支」—— 也就是「两个对照缺一不可」这句话本身。

    四条路各跑一次，**逐分支**看谁亮（实测，见下表的注释）：

        真实现          ② 不亮、③ 不亮               ⇒ 过
        只 `from_d`     ② 不亮、③ 不亮（**空转**）     ⇒ 过
        只 `to_d`       ② **亮**、③ 不亮              ⇒ 红
        两个都上        ② **亮**、③ **亮**            ⇒ 红

    ⇒ 「只上 `to_d`」那一行正是要证的东西：**它抓得到 ②，但 ③ 永远不亮**。
      于是**只用 `to_d` 的注入会让「读回后 `§A1` 仍成立」这一条永远没被验过** ——
      这就是两个对照都必须上的理由。少了这一条，「③ 会红」有两种解释
      （漂移真的发生了 / 随便上个对照都会红），分不开就等于没验。
    """
    from ..core import view_persist as vp

    rows = [
        ("真实现", {}, (False, False), True),
        ("只 `from_d`（空转：盘上没有派生栏，无从信任）",
         {"from_d": vp._from_dict_trusting_derived}, (False, False), True),
        ("只 `to_d`（抓得到 ②，但 ③ 不亮）",
         {"to_d": vp._to_dict_storing_derived}, (True, False), False),
        ("两个都上", {"to_d": vp._to_dict_storing_derived,
                      "from_d": vp._from_dict_trusting_derived}, (True, True), False),
    ]
    fails: list[str] = []
    for label, kw, (want2, want3), want_pass in rows:
        br = _a6_br(**kw)
        got2, got3 = bool(br["②"]), bool(br["③"])
        if (got2, got3) != (want2, want3):
            fails.append(f"{label}：分支期望 ②={want2} ③={want3}，"
                         f"实测 ②={got2} ③={got3}")
        if bool(br["①"]):
            fails.append(f"{label}：分支 ① 不该亮（权威边没被动过），实测亮了")
        if (not (got2 or got3)) is not want_pass:
            fails.append(f"{label}：期望{'过' if want_pass else '红'}，"
                         f"实测{'过' if not (got2 or got3) else '红'}")
    return fails


# --- `§A7` 合成对照 ------------------------------------------------------------
#
# 手推的那张小图（`U = a,b,c,d`、`P = {U}`、`E = {(a,b), (c,d)}`）：
#
#     旧结构（把 `d` 拿掉）  P_old = {a,b,c}、E_old = {(a,b)}
#                            E_old⁻¹({a,b,c}) = {a} ⇒ 劈成 {a} / {b,c}
#                            ⇒ Q_old = {{a}, {b,c}}       ← 手推
#     Q_ext                  {{a}, {b,c}, {d}}
#
#     传播（真实现）          E⁻¹({d}) = {c} ⇒ {b,c} 再劈 ⇒ {{a},{b},{c},{d}}
#                            稳定、细化 Q_ext ⇒ **过**
#     挂到父块（naive）       新方向 `d` 的父是 `c`，`c` 在 {b,c} 里
#                            ⇒ {{a}, {b,c,d}}；而 E⁻¹({b,c,d}) = {a,c}
#                            跨在内外 ⇒ **① 红**
#     全量重算               csr({U}, {(a,b),(c,d)}) = {{a,c},{b,d}}
#                            `{a}` 被并进 `{a,c}` ⇒ 不细化 Q_ext ⇒ **② 红**
#
# ⚠️ **② 的那一例是这张图存在的理由。** 只配 naive 一条的话，② 从没被验过，
#    而套件照样全绿 —— 「全绿」这句话的范围会悄悄缩掉（`§A3` / `§A5` 已各栽过一次）。

_A7_U = ("a", "b", "c", "d")
_A7_P = (frozenset({"a", "b", "c", "d"}),)
_A7_E = frozenset({("a", "b"), ("c", "d")})


def _a7_fixture() -> tuple[Any, ViewSpec, Any, Any]:
    """`(old_q, spec_new, propagate, propagate_naive)` —— 合成图，手推得动。"""
    spec_new = ViewSpec(universe=_A7_U, partition=_A7_P, relation=_A7_E)
    sub = restrict_spec(spec_new, frozenset({"d"}))
    old_q = coarsest_stable_refinement(sub)
    return old_q, spec_new, propagate, propagate_naive


def a7_known_answer() -> list[str]:
    """`§A7` 的合成对照。返回**失败清单**（空 = 全对）。

    四条路各跑一次，**逐分支**断言（见文件里那张手推表）：

        真 `propagate`      ① 不亮、② 不亮  ⇒ 过
        `propagate_naive`   ① **亮**、② 亮  ⇒ 红
        全量重算            ① 不亮、② **亮**  ⇒ 红
        （反向）真 `propagate` 细化旧 `Q`    ⇒ 真 —— 「传播只细分」这条**它满足**
    """
    old_q, spec_new, prop, naive = _a7_fixture()
    fails: list[str] = []
    rows = [
        ("真 `propagate`", prop(old_q, spec_new), (False, False)),
        ("`propagate_naive`（挂到父块）", naive(old_q, spec_new), (True, True)),
        ("全量重算（冒充传播）", coarsest_stable_refinement(spec_new), (False, True)),
    ]
    for label, q, (want1, want2) in rows:
        br = a7_branches(old_q, spec_new, new_q=q)
        got = (bool(br["①"]), bool(br["②"]))
        if got != (want1, want2):
            fails.append(f"{label}：分支期望 ①={want1} ②={want2}，"
                         f"实测 ①={got[0]} ②={got[1]}")
    # ★ 反向判据：真传播**必须**细化旧 `Q`（不合并旧块）——
    #   少了它，「传播只细分」这句话可以被一个**全量重算**的实现蒙混过关
    #   （重算 ⊑ 旧 Q 在合成图上恰好为真，所以这一条只能当**正向**判据用，
    #     它抓不到重算 —— 抓重算的是 ②「细化 Q_ext」）。
    if not refines_partition(old_q, prop(old_q, spec_new)):
        fails.append("真 `propagate` 居然不细化旧 `Q` —— 「只细分」这条它该满足")
    # ★ 定理的自检：传播 ⊑ 重算。**这条不该为假**（`core/views.propagate` 有证明）。
    if not refines_partition(coarsest_stable_refinement(spec_new), prop(old_q, spec_new)):
        fails.append("传播**不**细化重算 —— `core/views.propagate` 的定理被推翻了")
    return fails
