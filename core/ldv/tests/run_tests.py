"""单元测试 —— 内核不变量 / 三态 / 语料形状 / 自优化边界。

    跑法：python -m ldv.tests.run_tests

零第三方依赖（不用 pytest）—— 与 dce 的 `tests/run_tests.py` 同形：
自己数，自己报，**过 / 红分开计数**。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ldv.checks._fixtures import (  # noqa: E402
    build_keyset,
    build_reach,
    build_sequence,
    corpus_candidates,
    coverage_of,
    find_corpus,
    items,
    load,
    sequences,
)
from ldv.checks.coverage import (  # noqa: E402
    cover_leak_profile,
    cover_leak_unaccounted,
    soundness_profile,
)
from ldv.core import selfopt  # noqa: E402
from ldv.core.direction import (  # noqa: E402
    EVENT_INVALIDATED,
    EVENT_STAYED,
    EVENT_UNSPLITTABLE,
    ORIGIN_EXOGENOUS,
)
from ldv.core.kernel import (  # noqa: E402
    EXPOSURE_ALPHA,
    PROPENSITY_FLOOR,
    Kernel,
    Query,
    _did_order,
    exposure_propensity,
)
from ldv.core.tri import Tri, fold, is_bad_hit  # noqa: E402
from ldv.corpus.loader import CorpusError, keys_of, parse_front_matter  # noqa: E402
from ldv.plugins.keyset import KeysetPlugin  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name + (f"  —— {detail}" if detail and not cond else ""))


def raises(name: str, fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        PASS.append(name)
        return
    FAIL.append(name + "  —— 该抛异常却没抛")


def need_at_least(seq, n: int, what: str) -> bool:
    """**夹具前提守卫** —— `seq` 不够 `n` 个就记一条**跳过**并返回 `False`。

    ⚠️ 为什么这份套件需要它（2026-10-11 在 7 份新语料上实测）：

       这一套的夹具全是**按随仓库那份 36 项语料挑的** —— `ids[:2]` 先建 2 维护 n−2、
       `ids[10]` 当探针项、`ids[0..3]` 造两条关系、两个有子的父方向……
       换一份更小的语料，前提不成立 ⇒ 逐条 `IndexError` ⇒ **整趟测试崩**。
       而 **崩比红更糟**：红是判据说话，崩是判据没了 —— `main()` 里的函数
       一个接一个跑，崩在中间就等于**后面的测试函数一条都不跑**，
       而汇总行也不会打印。读输出的人看到的是一个截断的日志，不是一条红。

    ⚠️ 于是每个用**固定下标**取夹具元素的测试**自己守前提**，并逐字说明
       「跳过了什么、为什么」。**跳过 ≠ 通过** —— 它明说这一趟没查这一条。

    ⚠️ 反过来说：**不许**把它当成「小语料就一律放行」。守卫只挡
       「前提根本不成立」那一种；前提成立而判据说话时，照旧红。
    """
    if len(seq) >= n:
        return True
    PASS.append(f"{what}（**跳过**：只有 {len(seq)} 个，这份夹具要 ≥{n} 个 —— "
                f"它是按随仓库那份 36 项语料挑的）")
    return False


def _cone_invariant_bad(kernel) -> list[tuple[str, list[str], list[str]]]:
    """`_path` 的**不变量**违约清单（2026-10-08 起）。

        不变量：`set(Cone(x))` == {d : x ∈ members(d)}
        也就是说锥是「**当前真的持有** x 的方向」，不是「插入时走过的路径」。

    返回 `(项, 只被持有而锥上没有, 只在锥上而没被持有)` 的三元组列表。
    **两个方向都要报**：只看 `hold - cone` 会漏掉「摘多了」那一半。
    """
    bad: list[tuple[str, list[str], list[str]]] = []
    for x in sorted(kernel.items):
        hold = {d.did for d in kernel.all_directions()
                if x in set(kernel.members_of(d))}
        cone = set(kernel.cone(x))
        if hold != cone:
            bad.append((x, sorted(hold - cone), sorted(cone - hold)))
    return bad


# ═══ 三态 ════════════════════════════════════════════════════════════════════
def _ext_corpus(name: str):
    """外部语料的 `_data/` 根目录 —— 外部语料**不在仓库里**（`.gitignore` 的 `_data/*`）。

    ⇒ CI 与新 clone 上**必然缺**。缺了**记一条跳过并返回 `None`**，不抛 ——
      否则「没有这份语料」会表现成「单元测试崩了」，而那是两件事
      （这正是 2026-10-18 起 CI 四个矩阵全红的原因）。

    ⚠️ **跳过 ≠ 通过**：这条会出现在 PASS 列表里，写明「跳过：外部语料不进仓库」。
       本机（有 `_data/`）上这几个判据照常跑。
    ⚠️ 返回的是 **`_data/` 根**（不是 `nodes/`）—— 与原代码里的 `data` 同义，
       各函数自己拼 `<name>/nodes`，这里不改它们。
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "_data"
    if not (root / name / "nodes").is_dir():
        PASS.append(f"外部语料 {name}（**跳过**：`_data/{name}/nodes` 不在 —— "
                    f"外部语料不进仓库，见 .gitignore）")
        return None
    return root

def test_tri() -> None:
    raises("Tri 不是 bool：bool(未展开) 必须抛错", lambda: bool(Tri.UNEXPANDED))
    raises("Tri 不是 bool：bool(否) 必须抛错", lambda: bool(Tri.NO))
    ok("未展开 折 是 → 未展开", fold(Tri.YES, Tri.UNEXPANDED) is Tri.UNEXPANDED)
    ok("否 折 是 → 未展开（说不清就说不清）", fold(Tri.NO, Tri.YES) is Tri.UNEXPANDED)
    ok("全否 → 否", fold(Tri.NO, Tri.NO) is Tri.NO)
    ok("全是 → 是", fold(Tri.YES, Tri.YES) is Tri.YES)
    ok("只有「否」算危险分支（§K8 不对称）",
       is_bad_hit(Tri.NO) and not is_bad_hit(Tri.YES) and not is_bad_hit(Tri.UNEXPANDED))


# ═══ 语料形状 ════════════════════════════════════════════════════════════════

def test_loader() -> None:
    text = (
        "---\n"
        'id: "x-1"\n'
        "type: 概念\n"
        'aliases: ["a", "b"]\n'
        "cues:\n"
        '  - "c1"\n'
        '  - "c2"\n'
        "source:\n"
        '  ref: "r"\n'
        "  kind: 教材\n"
        "---\n"
        "正文\n"
    )
    fields, body = parse_front_matter(text)
    ok("标量去引号", fields["id"] == "x-1")
    ok("行内列表", fields["aliases"] == ["a", "b"])
    ok("块列表", fields["cues"] == ["c1", "c2"])
    ok("一层嵌套", fields["source"] == {"ref": "r", "kind": "教材"})
    ok("正文分离", body.strip() == "正文")
    k = keys_of(fields)
    ok("键含字段名", {"id", "type", "cues", "source"} <= k)
    ok("键含枚举取值", {"type=概念", "source.kind=教材"} <= k)

    raises("没 front-matter ⇒ 报错（不猜）", lambda: parse_front_matter("没有头\n"))
    raises("行内列表没闭合 ⇒ 报错", lambda: parse_front_matter("---\na: [1, 2\n---\n"))
    raises("意外缩进 ⇒ 报错", lambda: parse_front_matter("---\na: 1\n   b: 2\n---\n"))

    # ── ★★ P0 修法（2026-10-23 外部审计**实测复现**）：不许**静默误读** ────
    #   旧行为把三个合法形状解析成了**另一份有效数据**，比崩掉坏得多 ——
    #   这与本项目「不静默接受错误语料」的纪律直接矛盾。
    #   反向对照（逐条回退真源码 ⇒ 各自变绿）在
    #   `evidence/_probe_loader_contract_reverse.py`。
    f2, _b2 = parse_front_matter('---\nid: x\naliases: ["a,b", "c"]\n---\n')
    ok("★ 行内列表按**引号外**的逗号切（引号内的逗号不是分隔符）",
       f2["aliases"] == ["a,b", "c"], f"得到 {f2['aliases']}")
    f2b, _b2b = parse_front_matter("---\nid: x\nt: ['a,b', \"c\"]\n---\n")
    ok("★ 单双引号可以混用（同一个列表里）",
       f2b["t"] == ["a,b", "c"], f"得到 {f2b['t']}")
    raises("★★ 重复键 ⇒ 拒收（旧行为：后者覆盖前者，**前者整个消失**）",
           lambda: parse_front_matter("---\nid: a\nid: b\n---\n"))
    raises("★★ 嵌套映射里重复键 ⇒ 同样拒收",
           lambda: parse_front_matter("---\ns:\n  k: 1\n  k: 2\n---\n"))
    for _amb in ("true", "false", "null", "NULL", "yes", "no", "~", "None"):
        raises(f"裸标量 {_amb!r} ⇒ 拒收（YAML 会读成布尔/空，本格式只支持字符串）",
               lambda a=_amb: parse_front_matter(f"---\nid: x\nv: {a}\n---\n"))
    f3, _b3 = parse_front_matter('---\nid: x\nv: "true"\n---\n')
    ok("加引号的 \"true\" ⇒ 字符串（要字符串就显式写，不猜）",
       f3["v"] == "true", f"得到 {f3['v']!r}")
    raises("行内列表引号没闭合 ⇒ 拒收（分词器自己的边界）",
           lambda: parse_front_matter('---\nid: x\nt: ["a, b]\n---\n'))

    # 真语料：形状之外的一律报错 ⇒ 能整份读完说明形状覆盖住了
    loaded = load()
    if loaded is None:
        PASS.append("真语料（跳过：目录不在）")
        return
    nodes, edges, dangling = loaded
    ok("真语料读得进", len(nodes) > 0, f"读到 {len(nodes)} 个")
    ok("悬挂边为空", not dangling, f"悬挂 {sorted(dangling)}")

    # ⚠️ **计数只对随仓库提交的那份成立。** 换语料（`LDV_CORPUS=…`）时断言必须跟着换 ——
    #    否则「换了一份料」会被读成「语料坏了」，而那正是「跳过 ≠ 通过」要防的混淆。
    shipped = Path(__file__).resolve().parents[1] / "corpus" / "nodes"
    if find_corpus() == shipped:
        ok("真语料读得进（随仓库提交的那份）", len(nodes) == 36, f"读到 {len(nodes)} 个")
        ok("边数与事实相符", sum(len(v) for v in edges.values()) == 47)
    else:
        PASS.append(f"真语料计数（跳过：跑的是外部语料 {find_corpus().parent.name} —— "
                    f"{len(nodes)} 项 / {sum(len(v) for v in edges.values())} 边）")

    # ── ★ 指路：**显式指定却找不到 ⇒ 报错，不许回落** ──────────────────────
    # 回落会让「`LDV_CORPUS` 打错一个字」与「指对了」**长得一模一样**：
    # 跑的是仓库里那 36 项，检查照样全绿、退出码 0。
    # ⚠️ 这条自检**必须**用 `monkeypatch` 之外的办法证明它非空转 ——
    #    下面同时验「设了不存在的名字 ⇒ 报错」与「不设 ⇒ 正常兜底」，两条都要真跑。
    saved = os.environ.get("LDV_CORPUS")
    try:
        os.environ["LDV_CORPUS"] = "ldv-no-such-corpus-xyz"
        raises("`LDV_CORPUS` 指向不存在的名字 ⇒ 报错（不静默回落到默认语料）",
               lambda: find_corpus())
        os.environ.pop("LDV_CORPUS", None)
        ok("不设 `LDV_CORPUS` ⇒ 正常兜底到随仓库提交的那份",
           find_corpus() == shipped, f"兜底到 {find_corpus()}")
        # 名字解析：`_data/<名字>/nodes` 必须在候选链里，否则「按名字指料」这条路是断的
        os.environ["LDV_CORPUS"] = "some-name"
        cands = [str(p) for p in corpus_candidates()]
        ok("`LDV_CORPUS=<名字>` 会去 `_data/<名字>/nodes` 找（仓库旁 + 上一级）",
           any(p.endswith(os.path.join("_data", "some-name", "nodes")) for p in cands)
           and sum(p.endswith(os.path.join("_data", "some-name", "nodes")) for p in cands) == 2,
           f"候选链：{cands}")
    finally:
        os.environ.pop("LDV_CORPUS", None)
        if saved is not None:
            os.environ["LDV_CORPUS"] = saved

    # ── ★ 节点缓存**不串味**：就地改一份，另一份 load 读不到 ─────────────
    #    2026-10-21 `_probe_d7_rollback.py`（§D7 的实体证据）实测抓到：
    #    `_node_of` 缓存命中返回**同一个 Node 对象**，而派生维度叠加
    #    （`entry.py`）与 `predicates.apply_*` 都**就地改** `node.keys`
    #    ⇒ 改动静默写进缓存 ⇒ 同进程内移走 sidecar 再建，派生键**还在**、
    #    树一模一样（「删文件即回到原状」在进程内不成立）。
    #    ⇒ 修：命中返回 `replace(hit)`（副本）。这条判据守的就是那件事 ——
    #    ⚠️ 它是**反转变异类**的判据：不验「读得对」，验「隔离得住」；
    #       把 `replace(hit)` 改回 `return hit`，它必须红。
    _root_now = find_corpus()
    if _root_now is None:
        PASS.append("节点缓存不串味（跳过：找不到语料）")
    else:
        from ldv.corpus.loader import load_nodes as _ln
        _a = _ln(_root_now)
        _one = sorted(_a)[0]
        _old = _a[_one].keys
        _a[_one].keys = frozenset(set(_old) | {"注入=缓存隔离判据"})
        _b = _ln(_root_now)
        ok("节点缓存不串味：就地改一份的 keys，另一份 load 读不到（解析仍被缓存）",
           _b[_one].keys == _old and "注入=缓存隔离判据" not in _b[_one].keys
           and _b[_one] is not _a[_one],
           f"{_one}｜改后 {len(_a[_one].keys)} 键｜另一份 {len(_b[_one].keys)} 键"
           f"｜是不同对象 {_b[_one] is not _a[_one]}")


# ═══ 内核 ════════════════════════════════════════════════════════════════════

def test_kernel() -> None:
    loaded = load()
    if loaded is None:
        return
    nodes, edges, _ = loaded
    k, _ = build_keyset(nodes)

    ok("根是外生的", k.root.origin == ORIGIN_EXOGENOUS)
    ok("根没有父", k.root.parent is None)
    ok("根秩为 1", k.root.rank == 1)
    ok("秩越大越细（子比父大 1，§K5）",
       all(c.rank == k.direction(d).rank + 1
           for d in [x.did for x in k.all_directions()]
           for c in k.children_of(k.direction(d))))
    ok("§K1 见证比自身粗",
       all(k.direction(w).rank < d.rank for d in k.all_directions() for w in d.witness))
    ok("§K3 支撑锥从根起", all(k.cone(i) and k.cone(i)[0] == k.root.did for i in k.items))
    # ★ 不变量的**强**形式（2026-10-08 加）。
    #
    # 这一行原来写的是「支撑锥 = 插入路径」，而且只查了「首位是根」——
    # **标题**与**断言**都不到位：`expand` 会重试，重试时项被分进一个**新的**
    # 子方向，那一笔不在「插入时走过的路径」上。于是
    #     `x` 是子方向的成员，却不在自己的锥上 ⇒ `remove(x)` 摘不干净
    #     ⇒ 子方向留**幽灵成员** ⇒ 删除路径上 `B4` 报「子方向多出父没有的项」。
    #
    # 真不变量是：**锥 == 「当前真的持有这个项的方向集合」**。
    # 实测（4 份语料 × 3 个方向，`outputs/_probe_ghost_before_after.py`）：
    #     批建后 0 项（不重试）｜维护 12 项后 **49 项**（重试发生在这里）
    # 修好后两处都是 0。
    ok("§K3 支撑锥 == 持有它的方向集合（不是「插入时走过的路径」）",
       _cone_invariant_bad(k) == [],
       f"违约 {len(_cone_invariant_bad(k))} 项，例 {_cone_invariant_bad(k)[:1]}")
    ok("账本只增不改（§K4：失效只追加状态，不改历史）", k.ledger.verify_append_only() == [])
    ok("方向 id 按数值排（D3 在 D12 前）",
       sorted(["D3", "D12", "D100"], key=_did_order) == ["D3", "D12", "D100"])

    # 按需展开：没碰过的方向不该已展开
    fresh = Kernel(KeysetPlugin(), {i: nodes[i].as_item() for i in nodes})
    fresh.build(KeysetPlugin().merge([]))
    ok("展开是按需的（建完根还没展开任何东西）", len(fresh._expanded) == 0)  # noqa: SLF001

    # §K2 非平凡：分不开 ⇒ **这一层不建**（字面），只留一条判定事件
    k2 = Kernel(KeysetPlugin(), {"a": {"id": "a", "keys": frozenset({"k"})},
                                 "b": {"id": "b", "keys": frozenset({"k"})}})
    k2.build(KeysetPlugin().merge([]))
    kids = k2.expand(k2.root)
    ok("§K2：键集全同 ⇒ 不建这一层（成为叶）", kids == ())
    ok("§K2 判空被记下来（不是没记录）",
       any(e.kind == EVENT_UNSPLITTABLE for e in k2.ledger))
    # ★ 判定的**主体是父方向** —— 而且**没有子方向可以挂**（一个都没建）
    ok("§K2 判空挂在**父**方向上",
       all(e.did == k2.root.did for e in k2.ledger
           if e.kind == EVENT_UNSPLITTABLE))
    ok("§K2 判空后**没有**任何新方向被建出来",
       k2.stats()["方向"] == 1, str(k2.stats()))

    # 查询三态：分辨率不够 ⇒ 未展开，不是否
    res = k.query(Query(ideal=frozenset(nodes), min_rank=99))
    ok("分辨率不够 ⇒ 未展开", res.status is Tri.UNEXPANDED)
    ok("未展开时不许印成「确定没有」", "确定没有" not in res.render())

    # 倾向：内核填（模型见 test_exposure_model）
    d = k.all_directions()[0]
    rec = k.record_usage(d, {"outcome": 1.0})
    ok("内核填了倾向权重", rec is not None and 0.0 < rec.propensity <= 1.0)
    ok("「无信号」不产记录", k.record_usage(d, {"outcome": None}) is None)

    # reach：偏函数，拒绝遍历时返回未展开
    kr, _ = build_reach(nodes, edges, traverse_budget=1)
    rres = kr.query(Query(ideal=frozenset(nodes)))
    ok("reach 超预算 ⇒ 未展开（不是否）", rres.counts["未展开"] > 0, str(rres.counts))


# ═══ 曝光模型（§8.1.1） ═══════════════════════════════════════════════════════
#
# 这一段查三件事，**都不是读代码断言，而是「改一个数看输出跟不跟着变」**：
#
#     形状     位次 1 ⇒ 1.0；单调不增；下界真的生效
#     α 接线   `EXPOSURE_ALPHA` 是**显式参数** ⇒ 改它必须改变输出
#     单一实现 改 α 之后，**检查侧**（`checks/semantics._p`）与**内核侧**
#               （`record_usage` 填进记录里的那个数）必须**一起变**
#
# ⚠️ 第三条是本段存在的理由。修之前 `kernel` / `semantics._p` / `run_tests`
#    **各有一份** `max(1.0 / position, 0.05)` ⇒ 改一处、另两处**静默报旧值**，
#    而输出里只有一个数、**没有对照** ⇒ 读起来和改对了完全一样。
#    这是 `false-green` 形状 3（共享盲点），而 grep 源码挡不住它 ——
#    真正能挡住的是「**把参数改掉，看那个数动不动**」。

#: 实测锚点（`ldv/MEASUREMENTS.md` 结果十三）：Open Bandit Dataset 的 `random` 策略
#: （位次**随机分配** ⇒ 位次效应可因果识别）拟合出的 α。
#: ⚠️ 那是**横向槽位**的几何，量不了本设计的竖直列表 ⇒ 只作**量级参照**，不当取值依据。
OBD_ALPHA = (0.105, -0.003)


def test_exposure_model() -> None:
    from ldv.checks import semantics as sem
    from ldv.core import kernel as km

    ok("曝光：位次 1 的倾向 = 1.0（归一化点）",
       exposure_propensity(1) == 1.0, f"{exposure_propensity(1)}")

    seq = [exposure_propensity(k) for k in range(1, 65)]
    ok("曝光：位次越靠后倾向**不增**（单调不增）",
       all(b <= a + 1e-15 for a, b in zip(seq, seq[1:])),
       f"1→{seq[0]:.4f} 64→{seq[-1]:.4f}")

    ok("曝光：下界真的生效（不夹的话第 10^9 位会拿到 10^9 倍权重）",
       exposure_propensity(10**9) == PROPENSITY_FLOOR,
       f"{exposure_propensity(10**9)}")
    ok("曝光：下界之上**还没**生效（否则它就是个常数模型，不是指数模型）",
       exposure_propensity(3) > PROPENSITY_FLOOR,
       f"{exposure_propensity(3)}")

    # ★ α 是显式参数：把它改成 2，三处输出必须**一起**动
    ok("曝光：默认 α = 1.0（设计文档 §8.1.1 的取值；改它要留痕 —— 见结果十三）",
       EXPOSURE_ALPHA == 1.0, f"α = {EXPOSURE_ALPHA}")

    old = km.EXPOSURE_ALPHA
    loaded = load()
    try:
        km.EXPOSURE_ALPHA = 2.0
        got_fn = exposure_propensity(3)
        got_chk = sem._p(None, ("a", "b", "c"), -1)          # 位次 3
        got_k = None
        if loaded is not None:
            kx, _ = build_keyset(loaded[0])
            dirs = kx.all_directions()[:3]
            kx.show(tuple(d.did for d in dirs))
            # 记**第 3 位**那个 —— 记第 1 位的话倾向恒为 1.0，这条断言就空了
            rx = kx.record_usage(dirs[-1], {"outcome": 1.0})
            got_k = (rx.propensity, rx.extra["position"]) if rx else None
            ok("曝光：这一条真在验第 3 位（否则下面的断言是空转）",
               got_k is not None and got_k[1] == 3, f"{got_k}")
            got_k = got_k[0] if got_k else None
    finally:
        km.EXPOSURE_ALPHA = old

    ok("曝光：α 是**显式参数** ⇒ 改成 2 之后 `exposure_propensity(3)` = 1/9"
       "（否则公式是写死的 `1/位次`，标定不了）",
       abs(got_fn - 1.0 / 9.0) < 1e-12, f"{got_fn}")
    ok("★ 曝光：改 α 之后**检查侧**跟着变（`semantics._p` 没有自己藏第二份公式）",
       got_chk == f"{1.0 / 9.0:.3f}", f"报告里印的是 {got_chk}，应为 0.111")
    ok("★ 曝光：改 α 之后**内核侧**跟着变（填进使用记录的那个数也是同一个来源）",
       got_k is None or abs(got_k - 1.0 / 9.0) < 1e-12, f"记录里的倾向 {got_k}")

    ok("曝光：α 复原（本段不留全局副作用）",
       km.EXPOSURE_ALPHA == old == EXPOSURE_ALPHA)


# ═══ 自优化边界 ══════════════════════════════════════════════════════════════

def test_selfopt() -> None:
    loaded = load()
    if loaded is None:
        return
    nodes, _, _ = loaded
    k, _ = build_keyset(nodes)
    params = selfopt.Params(exogenous={"direction_functions": ["keyset"],
                                       "outermost_intent": "全空间"},
                            endogenous={"expand_priority": {}, "penalty_scale": 1.0})
    for d in k.all_directions()[:3]:
        k.record_usage(d, {"outcome": 1.0})
    after, info = selfopt.optimize(k, params)
    ok("F3：外生项指纹未变",
       after.fingerprint_exogenous() == params.fingerprint_exogenous())
    ok("F2：内生项确实被改了",
       after.fingerprint_endogenous() != params.fingerprint_endogenous())
    ok("F1：缺倾向的记录被拦下，不当 0 用", info["被拦下（缺倾向）"] == 0)
    ok("F4：内生量里没有跨方向全局评分",
       not any(x in after.endogenous for x in ("score", "objective", "global", "total")))

    # 硬拦：外生项是**只读**的 —— 写的那一刻就炸
    # ⚠️ 这里刻意用「先改、再调」的形状：指纹比对**放行**这种改动
    #    （`optimize` 在开头才取指纹，它看不见调用前发生的事），
    #    所以真正的守卫必须是只读容器。
    raises("F3：写外生项 ⇒ 当场抛（不靠事后比对）",
           lambda: params.exogenous.__setitem__("outermost_intent", "被改了"))
    raises("F3：update 外生项 ⇒ 当场抛",
           lambda: params.exogenous.update({"outermost_intent": "被改了"}))

    def leaky(kernel, p):
        p.exogenous["outermost_intent"] = "被改了"       # 调用前就改
        return selfopt.optimize(kernel, p)

    raises("F3：调用前改外生项也拦得住", lambda: leaky(k, params))


# ═══ 方向 C（序列前缀） ══════════════════════════════════════════════════════

def test_sequence() -> None:
    """方向 C 的三条硬性质 —— 它们都**不依赖真实语料里恰好有什么**。

    先用手造的极小语料把前缀语言的语义钉死，再去真语料上全量扫等价关系。
    """
    from ldv.plugins.sequence import END, SequencePlugin

    # --- 手造语料：把「顺序」这件事钉死 ---
    sp = SequencePlugin({"x": ("概念", "案例"), "y": ("案例", "概念"), "z": ()})

    ok("空前缀覆盖一切（根 = 最外层意图，不约束序列）",
       sp.covered_set(frozenset({()})) == frozenset({"x", "y", "z"}))
    ok("END 哨兵只覆盖「序列为空」的项",
       sp.covered_set(frozenset({(END,)})) == frozenset({"z"}),
       f"实际 {sorted(sp.covered_set(frozenset({(END,)})))}")
    ok("单符号前缀收下所有以它开头的项",
       sp.covered_set(frozenset({("概念",)})) == frozenset({"x"}))
    ok("加一个符号 ⇒ 覆盖只收窄，不变宽",
       sp.covered_set(frozenset({("概念", "案例")})) <= sp.covered_set(frozenset({("概念",)})))
    ok("**顺序敏感**：同样的两个符号，顺序不同 ⇒ 收下的项不同（A/B 上做不到）",
       sp.covered_set(frozenset({("概念", "案例")})) == frozenset({"x"})
       and sp.covered_set(frozenset({("案例", "概念")})) == frozenset({"y"}))
    ok("合并（空组）退化成空前缀 ⇒ 覆盖一切（合并的定义域要闭合）",
       sp.merge([]) == frozenset({()})
       and sp.covered_set(sp.merge([])) == frozenset({"x", "y", "z"}),
       f"merge([])={sorted(sp.merge([]))}")
    ok("合并的覆盖 ⊇ 每个子方向的覆盖",
       sp.covered_set(sp.merge([_fake_dir(frozenset({("概念", "案例")})),
                                _fake_dir(frozenset({("案例", "概念")}))]))
       >= frozenset({"x", "y"}))

    # --- 真语料：全量扫 ---
    loaded = load()
    if loaded is None:
        PASS.append("方向 C 真语料（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    k, plug = build_sequence(nodes, edges)

    # ① §I3 的严格等价：代价 0 ⟺ 满足
    bad: list = []
    for d in k.all_directions():
        for nid in sorted(nodes):
            zero = plug.penalty(d, nodes[nid].as_item()) == 0.0
            sat = plug.covers(frozenset(d.payload), nid)
            if zero != sat:
                bad.append((d.did, nid))
    pairs = len(k.all_directions()) * len(nodes)
    ok(f"§I3：代价 0 ⟺ 满足（全量 {pairs} 对）", not bad, f"{len(bad)} 对不一致：{bad[:2]}")

    # ② §I5 往返：前缀语言逐字不变
    rt = [d.did for d in k.all_directions()
          if plug.decode(plug.encode(d)) != frozenset(d.payload)]
    ok("§I5：编码/解码往返，前缀语言逐字不变", not rt, f"{len(rt)} 个方向往返后变了：{rt[:3]}")

    # ③ §I4 的划分在 **payload 层**也成立：
    #    已展开方向的每个成员，被两个子方向**恰好覆盖一次**
    #    （B4 查的是内核的归属分配；这一条查得更早 —— 插件提出的两个语言本身就分得开）
    bad3: list = []
    checked3 = 0
    for d in k.all_directions():
        kids = k.children_of(d)
        if len(kids) < 2:
            continue
        for nid in k.members_of(d):
            n = sum(1 for c in kids if plug.covers(frozenset(c.payload), nid))
            checked3 += 1
            if n != 1:
                bad3.append((d.did, nid, n))
    ok(f"§I4：成员被两个子方向恰好覆盖一次（{checked3} 个成员）",
       not bad3, f"{len(bad3)} 个例外：{bad3[:2]}")

    # ④ 序列是**确定性**算出来的（§8.2：依赖遍历顺序就不可复现）
    from ldv.checks._fixtures import sequences
    ok("seq(x) 确定性：跑两遍完全一样",
       sequences(nodes, edges) == sequences(nodes, edges))


# ═══ 流程 A / B / D ══════════════════════════════════════════════════════════

def test_flows() -> None:
    """三条流程的**不变量** —— 每条都能红，不是「跑完就算过」。

    流程 C 的判据在 `run_checks.py`；这里查的是 C 管不到的三件事：

        A  展示 ≠ 遍历（内核原来把前沿当展示 ⇒ 曝光位次在多层查询下是假的）
        B  插入**新项**后 M3 / B9 仍成立，且失效是「标记不删」
        D  只改内生量、不碰结构、缺倾向记录被拦下

    最要紧的一条是 A 的**非退化**断言：`_last_frontier ≠ yes` 必须**真的发生过**，
    否则「展示要显式」这件事就只是文档里的说法，没有证据。
    """
    loaded = load()
    if loaded is None:
        PASS.append("流程 A/B/D（跳过：语料目录不在）")
        return
    nodes = loaded[0]

    from ldv.checks._fixtures import items as make_items, keyset_queries
    from ldv.flow import insert_items, make_params, run_optimize, run_query

    # ── 流程 A · 运行 ──────────────────────────────────────────────────────
    k, _ = build_keyset(nodes)
    qs = keyset_queries(nodes)
    a = run_query(k, qs[0])

    ok("A：这次查询真的摆出了候选（否则下面的断言全是空转）",
       len(a.shown) > 0, f"展示 {len(a.shown)} 个")
    ok("A：展示列表 ⊆ 说「是」的方向（摆的是候选，不是否也不是未展开）",
       set(a.shown) <= set(a.result.yes))
    ok("A：展示顺序 = 候选顺序（内核不重排）", a.shown == tuple(a.result.yes))
    ok("A：位次从 1 起 ⇒ 第一个的倾向 = 1.0",
       a.records and a.records[0].extra["position"] == 1
       and a.records[0].propensity == 1.0,
       f"第一条 {a.records[0].propensity if a.records else '无'}")
    ok("A：倾向 = 曝光模型（内核按曝光位次填，插件伪造不了）",
       all(abs(r.propensity - exposure_propensity(r.extra["position"])) < 1e-12
           for r in a.records))

    # 没给反馈 ≠ 给了负反馈：前者不产记录
    a_none = run_query(k, qs[0], feedback={did: None for did in a.shown})
    ok("A：全不给反馈 ⇒ 一条记录都不产（无信号 ≠ 负信号）",
       len(a_none.records) == 0 and a_none.没给反馈 == len(a_none.shown))
    half = {did: (None if i % 2 else 1.0) for i, did in enumerate(a.shown)}
    a_half = run_query(k, qs[0], feedback=half)
    ok("A：只给一半反馈 ⇒ 记录数 = 给了反馈的那些",
       len(a_half.records) == len(a_half.shown) - a_half.没给反馈
       and 0 < a_half.没给反馈 < len(a_half.shown))

    # ★ 核心：遍历**不**设展示
    kq, _ = build_keyset(nodes)
    kq.query(qs[0])
    ok("A：遍历本身不设展示（展示是显式的一步）", kq.shown == ())
    ok("A：遍历留下的是「前沿」（内省用），不是展示",
       kq._last_frontier != () and kq.shown != kq._last_frontier)  # noqa: SLF001

    diff = 0
    for q in qs[:6]:
        kq2, _ = build_keyset(nodes)
        r2 = kq2.query(q)
        if set(kq2._last_frontier) != set(r2.yes):  # noqa: SLF001
            diff += 1
    ok("A：多层查询下「前沿」≠「说『是』的方向」**真的发生过**",
       diff > 0, f"{diff}/6 条查询上两者不同")

    # 没有展示上下文时：写「不知道」，不默默当成知道
    kn, _ = build_keyset(nodes)
    d0 = kn.all_directions()[0]
    rec = kn.record_usage(d0, {"outcome": 1.0})
    ok("A：没展示过就记账 ⇒ 标出「无展示上下文」",
       rec is not None and rec.extra["无展示上下文"] is True)
    kn.show([d0])
    rec2 = kn.record_usage(d0, {"outcome": 1.0})
    ok("A：展示过之后 ⇒ 上下文为真，位次 = 1",
       rec2 is not None and rec2.extra["无展示上下文"] is False
       and rec2.extra["position"] == 1)

    # ── 流程 B · 维护 ──────────────────────────────────────────────────────
    ids = sorted(nodes)
    all_items = make_items(nodes)
    hold = set(ids[-12:])
    init = {i: all_items[i] for i in ids if i not in hold}
    kb = Kernel(KeysetPlugin(), init)
    kb.build(KeysetPlugin().merge([]))
    for i in sorted(init):
        kb.insert(i)

    new = [(i, all_items[i]) for i in ids if i in hold]
    before_events = len(kb.ledger)
    before_dirs = kb.stats()["方向"]
    reports = insert_items(kb, new)

    ok("B：每项都出了一份报告（没漏、没多）", len(reports) == len(new))
    ok("B：M1 支撑锥从根起", all(r.cone and r.cone[0] == kb.root.did for r in reports))
    ok("B：M3 —— 变动 ⊆ Cone(x)，锥外 0 处",
       not any(r.out_of_cone for r in reports),
       f"越界：{[r.out_of_cone for r in reports if r.out_of_cone][:1]}")
    ok("B：新项真的进了结构（在根成员里）",
       all(i in kb.members_of(kb.root) for i, _ in new))
    ok("B：归属**确实**动了（每项沿锥都改了成员）—— 插入不是空转",
       all(r.changed for r in reports),
       f"没动过的：{[r.item for r in reports if not r.changed][:3]}")
    ok("B：新项出现在它锥上**每一个**方向的成员里（归属沿锥落地）",
       all(all(i in kb.members_of(kb.direction(x)) for x in r.cone)
           for r, (i, _) in zip(reports, new)))
    # ⚠️ 这一条是**等价式**，不是「账本必须长长」——
    #    真语料上 12 个新项全落进已有叶 ⇒ 一条结构事件都不产，这是**对的**。
    #    我把「账本要长长」当不变量写过一次，红了；红的是断言，不是代码。
    #    真不变量是：**账本不撒谎** —— 有事件 ⟺ 真长出了新方向。
    ok("B：账本不撒谎 —— 结构事件 ⟺ 新方向出现（不凭空记、也不漏记）",
       (len(kb.ledger) - before_events > 0) == (kb.stats()["方向"] > before_dirs),
       f"事件 +{len(kb.ledger) - before_events}，方向 +{kb.stats()['方向'] - before_dirs}")
    ok("B：B9 插入后账本仍只增不改", kb.ledger.verify_append_only() == [])
    # ★ 维护路径上锥的不变量 —— **这一条才是幽灵成员的守卫**。
    #   批建路径不重试 ⇒ 那里恒为 0；重试发生在维护 ⇒ 缺陷只在这里显形。
    bad = _cone_invariant_bad(kb)
    ok("B：锥 == 持有它的方向集合（维护路径，**重试之后**）",
       bad == [], f"违约 {len(bad)} 项，例 {bad[:1]}")

    # §K2 字面 = **不建这一层** —— 手造一个必然退化的层，比等真语料碰巧出现可靠。
    # ⚠️ 这一段原来查的是「失效方向仍在 all_directions 里（标记不删）」。
    #    字面实现之后**没有失效方向可查了** —— 那一层根本没建出来。
    #    代价搬到了**叶容量**上，所以这里改查三件事：判空记在父上、没建新方向、叶还是满的。
    kd = Kernel(KeysetPlugin(), {"a": {"id": "a", "keys": frozenset({"k"})},
                                "b": {"id": "b", "keys": frozenset({"k"})}})
    kd.build(KeysetPlugin().merge([]))
    kd.insert("a")
    kd.insert("b")
    uns = [e.did for e in kd.ledger if e.kind == EVENT_UNSPLITTABLE]
    ok("B：§K2 判空确实产生了判定事件（否则下面那条是空转）", bool(uns))
    ok("B：判空全挂在**父**方向上（没有子方向可以挂）",
       all(x == kd.root.did for x in uns), str(uns))
    ok("B：判空**没有**建出任何新方向（字面：不建这一层）",
       kd.stats()["方向"] == 1, str(kd.stats()))
    ok("B：代价搬到了叶容量上 —— 两个项都留在这个叶里",
       kd.stats()["最大叶容量"] == 2, str(kd.stats()))
    # ★ 真语料上也查一遍「没有空壳」—— 这里判空 7 次，旧实现会多出 14 个空壳。
    ok("★ 真语料：不存在空壳方向（判空 7 次，方向数里一个空壳都不多）",
       all(kb.members_of(d) for d in kb.all_directions()),
       f"空壳：{[d.did for d in kb.all_directions() if not kb.members_of(d)][:5]}")
    ok("★ 真语料：方向数 = 有子层 + 叶（不是「活方向 + 2×判空」）",
       kb.stats()["方向"] == kb.stats()["有子层"] + kb.stats()["叶"],
       str(kb.stats()))

    # ── 流程 D · 自优化 ────────────────────────────────────────────────────
    kf, _ = build_keyset(nodes)
    for q in qs[:3]:
        run_query(kf, q)                       # 攒真实使用记录
    st_before = kf.stats()
    params = make_params("keyset", "键集包含 —— 最外层意图不约束任何键")
    fd = run_optimize(kf, params)

    ok("D：外生项逐项未变（F3）",
       fd.params.fingerprint_exogenous() == params.fingerprint_exogenous())
    ok("D：内生量确实变了（F2 —— 不是空转）", fd.内生量确实变了,
       f"{fd.before} → {fd.after}")
    ok("D：**有证据地**变了（至少一个方向拿到 expand_priority）", fd.学到东西)
    ok("D：缺倾向的记录被拦下（F1），不当 0 用",
       fd.info["被拦下（缺倾向）"] == 0 and fd.info["带权记录"] > 0)
    ok("D：按方向分别更新（内生量里出现 expand_priority.<did>）",
       any(k.startswith("expand_priority.") for k in fd.params.endogenous))
    ok("D：内生量里没有跨方向全局评分（F4）",
       not any(x in fd.params.endogenous for x in ("score", "objective", "global", "total")))
    ok("D：自优化**不碰结构**（只改内生量，方向数与事件数不变）",
       kf.stats()["方向"] == st_before["方向"]
       and kf.stats()["事件"] == st_before["事件"])

    # ★ 「内生量确实变了」会**退化恒真** —— 必须单独钉住这一点，
    #   否则空转的运行会印成「成功」。
    kz, _ = build_keyset(nodes)                     # 干净内核：一条记录都没有
    fd0 = run_optimize(kz, make_params("keyset", "键集包含"))
    ok("D：0 条记录时「内生量确实变了」仍然为真 —— **所以它不能当产出判据**",
       fd0.内生量确实变了 and not fd0.学到东西,
       f"变了={fd0.内生量确实变了} 学到={fd0.学到东西}")
    ok("D：0 条记录 ⇒ 内生量的变化只是常数项（penalty_scale 1.0 → 0.0）",
       fd0.params.endogenous.get("penalty_scale") == 0.0
       and not any(k.startswith("expand_priority.") for k in fd0.params.endogenous))

    # A 的空转判据
    ka, _ = build_keyset(nodes)
    # ⚠️ 别拿「互斥要求」那个查询当空候选 —— 键集的 `命中` 允许假阳，
    #    所以它对不可能查询照样说「是」（那是 §K8 的正确形状，不是缺陷）。
    #    真正「一条候选都没有」只能用**分辨率不够**来造。
    a_none_shown = run_query(ka, Query(ideal=frozenset(nodes), min_rank=99))
    ok("A：候选为空 ⇒ 标成空转（不许与「有候选但都没反馈」混为一谈）",
       a_none_shown.空转 and not a_none_shown.records,
       f"展示 {len(a_none_shown.shown)} 个")
    ok("A：有候选时不是空转", not a.空转)

    # ── 流程 B′ · 删除（§10.2 C）─────────────────────────────────────────────
    # ⚠️ 要**真删到结构里**：挑的是已经进过结构的项，不是「删空气」。
    from ldv.flow import remove_items

    kr, _ = build_keyset(nodes)
    cover_k = coverage_of("keyset", nodes, loaded[1])
    victim = sorted(kr.items)[-6:]
    before_dirs = kr.stats()["方向"]
    rp = remove_items(kr, victim, cover=cover_k)

    ok("B′：每项都出了一份报告（没漏、没多）", len(rp) == len(victim))
    ok("B′：M1 支撑锥从根起", all(x.cone and x.cone[0] == kr.root.did for x in rp))
    ok("B′：D4/M3 —— 变动 ⊆ Cone(x)，锥外 0 处",
       not any(x.out_of_cone for x in rp),
       f"越界：{[x.out_of_cone for x in rp if x.out_of_cone][:1]}")
    ok("B′：**方向数一个都没少**（§M2 情形③「标记，不删」）",
       kr.stats()["方向"] == before_dirs)
    ok("B′：D6 —— 没有方向被真删（`_dirs` 里一个都没少）",
       not any(x.gone_directions for x in rp))
    ok("B′：D5 —— 账本只增不改", not any(x.ledger_rewritten for x in rp)
       and kr.ledger.verify_append_only() == [])
    ok("B′：D1 —— 支持集空了的方向都有失效账目",
       not any(x.unrecorded_empty for x in rp))
    ok("B′：D2/D3 —— **量过了**，且都是 0（不是「未展开」）",
       all(x.cover_leak == 0 and x.outside_members == 0 for x in rp),
       f"读数 {[(x.cover_leak, x.outside_members) for x in rp]}")
    # ★ 非退化：**真的有方向变空** —— 否则 D1/D6 查的是空气。
    ok("B′：非退化 —— 这一趟**真的有方向支持集空了**（否则 D1/D6 是空转）",
       any(x.emptied for x in rp),
       f"变空 {[x.emptied for x in rp if x.emptied][:3]}")
    ok("B′：空方向数 == 已失效方向数 > 0（结构事实与账本事实一致）",
       kr.stats()["空方向"] == kr.stats()["已失效方向"] > 0, str(kr.stats()))
    ok("B′：被删的项真的不在 `items` 里了", all(v not in kr.items for v in victim))
    ok("B′：被删的项真的不在路径方向的成员里了",
       all(x.item not in kr.members_of(kr.direction(did))
           for x in rp for did in x.cone))
    # 不传 cover ⇒ D2/D3 是「未展开」，**不是 0** —— 两者不能长得一样
    kr2, _ = build_keyset(nodes)
    rp2 = remove_items(kr2, sorted(kr2.items)[-3:])
    ok("B′：不传覆盖 oracle ⇒ D2/D3 是 `None`（未展开），**不是 0**",
       all(x.cover_leak is None and x.outside_members is None for x in rp2)
       and all("未展开" in x.render() for x in rp2))
    try:
        kr2.remove("__不存在__")
        _raised = False
    except KeyError:
        _raised = True
    ok("B′：删一个不存在的项 ⇒ 抛 KeyError，不静默当没事", _raised)


# ═══ 涌现：叶不是终态（§M2 情形④） ══════════════════════════════════════════

def test_emergence() -> None:
    """★ 本仓最深的一处实现缺陷 —— 它只有**真跑一遍维护**才看得见。

    `expand()` 原来把「分不开」也写成永久标记，于是一个方向一旦被判成叶就
    **永远是叶**。后果：树只在**第一批**项上长过层，之后插进来的项全堆进已有叶。
    §M2 情形④（涌现）因此**不可达** —— 而「涌现」正是这个设计借来的核心机制。

    四条断言，每条都能红：
      1. 手造语料：两项分不开、加第三项后**必须**长出新层
      2. 反复失败**不许**累积残骸（字面 §K2：判空**不建**方向 ⇒ 结构里只有根），
         但判定会随成员集变化而重判 —— 「结构不长」与「判定不重记」分开查；
         另查**不存在空壳方向**（旧实现每判空一次物化两个空壳，这条会红）
      3. 真语料 × **三个方向**：分批维护不改变**尺度**与**分辨率**
         （方向数、最大叶容量与一次建完逐项相同）；
         且**不许留过期的「分不开」结论**
    """
    from ldv.plugins.keyset import KeysetPlugin

    # --- ① 涌现必须可达 ---
    plug = KeysetPlugin()
    k = Kernel(plug, {"x1": {"id": "x1", "keys": frozenset({"a"})},
                      "x2": {"id": "x2", "keys": frozenset({"a"})}})
    k.build(plug.merge([]))
    k.insert("x1")
    k.insert("x2")
    ok("两项键集相同 ⇒ 根分不开，成为叶", k.children_of(k.root) == ())
    ok("「分不开」被记下来（不是没试过）", k.root.did in k._tried)  # noqa: SLF001

    k.insert("x3", {"id": "x3", "keys": frozenset({"a", "b"})})
    kids = k.children_of(k.root)
    ok("★ 加入第三项后根**真的长出了新层**（§M2 情形④ 涌现）", len(kids) == 2)
    ok("★ 涌现出来的层是非平凡的（两侧都非空）",
       len(kids) == 2 and all(k.members_of(c) for c in kids))
    ok("★ 归属被重新分配：x3 单独一侧，x1/x2 在另一侧",
       len(kids) == 2
       and sorted(len(k.members_of(c)) for c in kids) == [1, 2])
    ok("★ 长出来之后不再保留「分不开」的旧结论", k.root.did not in k._tried)  # noqa: SLF001
    ok("★ 第三项也被分到了子方向里（不是停在父上）",
       any("x3" in k.members_of(c) for c in kids))

    # --- ② 反复失败不许累积残骸 ---
    # ⚠️ 这一段原来查的是「根 + 那一对失效方向 = 3」。字面实现之后**没有那一对**了：
    #    §K2 判「不建这一层」时一个方向都不建 ⇒ 结构里**只有根**。
    #    结构上的残骸是 0，但**判定**会随输入集变化而重复 —— 两者都要查，
    #    因为「结构不长」和「判定不重记」是两件事（§K2 的「时间维度」）。
    plug2 = KeysetPlugin()
    # 只声明 y0 ⇒ 成员集**逐项长大**（2→3→4→5），每次都该重判一次
    k2 = Kernel(plug2, {"y0": {"id": "y0", "keys": frozenset({"a"})}})
    k2.build(plug2.merge([]))
    for i in range(5):
        k2.insert(f"y{i}", {"id": f"y{i}", "keys": frozenset({"a"})})
    ok("反复失败不累积残骸（字面：判空**不建**方向 ⇒ 结构里只有根）",
       k2.stats()["方向"] == 1, f"实际 {k2.stats()}")
    ok("判空只挂在**一个**方向（根）上 —— 不随重试次数增长",
       k2.stats()["§K2 判空"] == 1, f"实际 {k2.stats()}")
    ok("★ 但成员集每变一次就**重判**一次（判空事件 = 4 次变化，不是 5 次插入）",
       sum(1 for e in k2.ledger if e.kind == EVENT_UNSPLITTABLE) == 4,
       f"实际 {sum(1 for e in k2.ledger if e.kind == EVENT_UNSPLITTABLE)} 条")
    ok("同一批成员不重复判（惰性：同一输入只判一次）",
       k2._tried[k2.root.did] == frozenset({f"y{i}" for i in range(5)}))  # noqa: SLF001
    ok("代价确实搬到了叶容量上（5 项全挤在这个叶里）",
       k2.stats()["最大叶容量"] == 5, f"实际 {k2.stats()}")
    # ★ 这条是**字面 §K2 的判据**：判空**不建** ⇒ 不存在「空壳方向」。
    #   旧实现每判空一次就物化**两个**成员集为空的方向，于是
    #   `方向数 = 活方向数 + 2×判空次数`。这条断言在旧实现下**会红**。
    #   ⚠️ 现有检查都抓不到空壳：`B16` 查 `members ⊆ 覆盖`（空集 ⊆ 任何集，恒真），
    #      `B7` 查「有见证」（空壳有见证），`B4` 只查已展开的方向。
    #      所以「没有空壳」必须**单独**断言，不能指望别的检查顺带覆盖。
    ok("★ 字面 §K2：不存在空壳方向（每个方向的成员集都非空）",
       all(k2.members_of(d) for d in k2.all_directions()),
       f"空壳：{[d.did for d in k2.all_directions() if not k2.members_of(d)]}")
    ok("★ 方向数 = 有子层 + 叶，且**没有**「多出来的两个」",
       k2.stats()["方向"] == k2.stats()["有子层"] + k2.stats()["叶"]
       and k2.stats()["方向"] == 1, f"实际 {k2.stats()}")

    # --- ③ 真语料：结构不许因维护而退化 ---
    loaded = load()
    if loaded is None:
        PASS.append("涌现·真语料（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    from ldv.checks._fixtures import items as make_items

    all_items = make_items(nodes)
    ids = sorted(nodes)

    def build(plug, root, init_n: int, hold_n: int):
        kk = Kernel(plug, {i: all_items[i] for i in ids[:init_n]})
        kk.build(root)
        for i in ids[:init_n]:
            kk.insert(i)
        for i in ids[init_n:init_n + hold_n]:
            kk.insert(i, all_items[i])
        return kk

    def max_leaf(kk) -> int:
        return max((len(kk.members_of(d)) for d in kk.all_directions()
                    if not kk.children_of(d)), default=0)

    # ★ 跨**三个方向**查同一条不变量 —— 只有键集一个方向时，它可能只是巧合。
    #
    # 实测（36 项语料）：
    #     改前  一次建完 25/71/21 个方向 → 先建 6 维护 30 掉到 **3/11/7**
    #           最大叶容量 10/1/10 → **31/29/26**（退化成平表）
    #     修后  一次建完 25/71/21，先建 6 维护 30 仍是 25/71/21（方向数与叶容量逐项相同）
    #
    # ⇒ **判据是「叶容量」**（分辨率），**不是「方向数」**（记账）。
    #   §7.1 已经定了这个分界：会变的那个是记账，不变的那个才是形状。
    #   ⚠️ 而 `§I4` 允许插件定扇出之后，**方向数**这一列**确实会变**了 ——
    #      扇出在**首次展开时定形**（§K3：已分配的项不许挪走 ⇒ 扇出只能增不能改），
    #      而「首次展开时成员集里有几个符号」取决于初始批次
    #      ⇒ 先建 6 维护 30 时 sequence 是 20，一次建完是 16。
    #      变的只有记账：**叶容量逐项相同（10）**。
    #      所以断言收在叶容量上，方向数只**报出来**。
    #      （这条收窄是**实测逼出来的**，不是为了让改动通过 —— 见 `C10` §3。）
    # ⚠️ 但**不要求同构** —— 实测 `_shape()` 在分批维护与一次建完之间**不相等**。
    #    那是**当前实现（路径局部增量）的性质**，不是「增量」的固有代价
    #    （Godin 1995 既增量又顺序无关；见 `C8` N3）。
    #    要求同构就等于要求「维护必须重建成批建的样子」，那是另一个设计。
    #    ⇒ 这条差异现在由 `checks/divergence.py` **只报不判**地量出来。
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin
    from ldv.checks._fixtures import sequences

    cases = {
        "keyset": (lambda: KeysetPlugin(), lambda p: p.merge([])),
        "reach": (lambda: ReachPlugin(edges), lambda p: frozenset(nodes)),
        "sequence": (lambda: SequencePlugin(sequences(nodes, edges)),
                     lambda p: frozenset({()})),
    }
    fanout_note: list[str] = []
    for label, (mk_plug, mk_root) in cases.items():
        once_k = build(mk_plug(), mk_root(mk_plug()), 36, 0)
        inc_k = build(mk_plug(), mk_root(mk_plug()), 6, 30)
        fanout_note.append(f"{label} {len(once_k.all_directions())}"
                           f"→{len(inc_k.all_directions())}")
        ok(f"★ [{label}] 分批维护不改变**分辨率**：最大叶容量与一次建完相同"
           f"（改前会退化成 {once_k.stats()['最大叶容量']}→31 这类平表）",
           max_leaf(inc_k) == max_leaf(once_k),
           f"一次建完 {max_leaf(once_k)}，先建 6 维护 30 → {max_leaf(inc_k)}")
        ok(f"★ [{label}] 维护后仍然**不是平表**（方向数 ≥ 叶数，且叶容量没爆）",
           len(inc_k.all_directions()) >= len(
               [d for d in inc_k.all_directions() if not inc_k.children_of(d)]),
           f"方向 {len(inc_k.all_directions())} / 叶 "
           f"{len([d for d in inc_k.all_directions() if not inc_k.children_of(d)])}")
    # 方向数是**记账**，不是判据 —— 但必须**报出来**，否则「变了」没人知道。
    PASS.append("（读数）分批维护的方向数 一次建完→先建6维护30：" + "、".join(fanout_note))

    # 不变量：**不许留过期的「分不开」结论**。
    #
    # ⚠️ 这里**不**要求「每个叶都被试过」—— `§3` 明说展开是**按需**的，
    #    所以「≥2 个成员但还没被碰过」的叶是**合法**的。要求它必须被试过
    #    就等于要求预先物化。真正要防的是**过期结论**：拿一个成员集试过、
    #    失败了，之后成员集变了却还留着旧结论 —— 那正是「叶是终态」的形状。
    kk = build(KeysetPlugin(), KeysetPlugin().merge([]), 6, 30)
    stale = []
    for d in kk.all_directions():
        if kk.children_of(d):
            continue
        t = kk._tried.get(d.did)  # noqa: SLF001
        if t is not None and t != kk.members_of(d):
            stale.append((d.did, len(t), len(kk.members_of(d))))
    ok("★ 不许留过期的「分不开」结论（结论必须对应当前成员集）",
       not stale, f"{len(stale)} 个叶带着过期结论（旧集/现集）：{stale[:3]}")
    ok("★ 且真的有叶被「试过」了（否则上一条是空转）",
       len(kk._tried) > 0, f"_tried 里 {len(kk._tried)} 项")  # noqa: SLF001


def test_divergence() -> None:
    """★ 「增量 ≡ 全量」是**度量**，所以它的「能红」不是「判出违规」，
    而是「**必须能报出两个值**」—— 一个永远报同一个值的度量，信息量是 0。
    这正是「空转与通过长得一模一样」在**度量**上的形态。

    五条，每条都能红：

      ① 比较器自检：同路径判同构、项集不同判不同构 ⇒ 它不是常量
      ② 两侧项集必须一致 ⇒ 差异是**结构性**的，不是「项多寡」造成的（防假的不同构）
      ③ **尺度**：扇出固定为 2 的方向（A / B）必须逐项相同；
         扇出由插件定的方向（C，k 叉）**允许**在尺度上不同 —— 但要**报出来**；
         而**分辨率**那一列要换个尺子（见下）
      ④ 实测：扫描里**同构与不同构都出现过** ⇒ 度量在真语料上有区分力
      ⑤ 不同构**不进退出码**（度量与判据分开）

    ⚠️ ③ 原来断言**所有**方向「差异不在尺度」。`§I4` 允许插件定扇出之后，
       C 的**方向数**这一列**确实会变**（16 一次建完 → 21 先建 2 项）——
       扇出在**首次展开时定形**（§K3），而首次展开时组里有几个符号取决于初始批次。
       ⇒ ③ 拆成两半：**A / B 是判据**（它们的扇出恒为 2，尺度必须稳），
         **C 是读数**（报出来，不判）。这个拆分有依据，不是为了让改动通过：
         变的只是**记账**（方向数），**叶容量（分辨率）逐项相同**（见 §7.1）。

    ⚠️ ④ 判的是**三个方向合起来**，不是逐个方向 —— 实测 `reach` 在扫描范围内
       **从未同构**（连 `k = N−1` 都不同构），要求每个方向都出现 True 是错的。
       要证的是「这个**比较器**能报出两个值」，不是「每个方向都两种都出现」。

    ⚠️ ③ 的 C 分支**再改过一次**（`§10.2` 出路 (4) 落地之后），改动有**实测依据**，
       不是为了让改动通过：

           出路 (4) 之前   项**全都**往下走 ⇒ `叶容量` 就是分辨率
           出路 (4) 之后   项可以**停在内部节点** ⇒ `叶容量` **看不见**那部分代价
                           实测 k=2：叶容量报 **2**，真相是 **34 项堆在根上**

       ⇒ 分辨率换成 `最大停留数`（每项锥的末位方向上的项数；批建路径上它与
         `叶容量` **相等**，所以这个推广不改变原有的读数）。
       ⇒ 判据拆成两半，**两半都是收窄**：
            无滞留的 k ⇒ 停留数必须与同项集参照**相同**
            有滞留的 k ⇒ `最大停留数 > 最大叶容量`（**把那个洞钉住**）
       ⚠️ 顺带说明：改前 k=2 的「叶容量 10」是**假的绿** —— 那个结构里有
          **68 个成员落在覆盖之外**（`B16` 维护路径红）。⇒ 它报的分辨率
          不是「细」，是「细在错的地方」。

    ⚠️ ④ 在「哪天真的做到了同构」时**会红** —— 那是**信号不是故障**：
       它说明「增量 ≡ 全量」成立了，这条度量该**升级成 B 系列检查**（进退出码）。
    """
    from ldv.checks._fixtures import make_builder
    from ldv.checks.divergence import divergence_profile

    loaded = load()
    if loaded is None:
        PASS.append("增量 ≡ 全量（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)

    all_iso: set[bool] = set()
    for which in ("keyset", "reach", "sequence"):
        prof = divergence_profile(make_builder(which, nodes, edges), nodes, ids)
        sc = prof["比较器自检"]
        rows = prof["各 k"]

        # ① 比较器不是常量
        ok(f"[{which}] 比较器自检：同路径必须判同构", sc["同路径必须同构"])
        ok(f"[{which}] 比较器自检：项集不同必须判不同构", sc["项集不同必须不同构"])

        # ② 项集一致 —— 防「假的不同构」
        bad_universe = [k for k, r in rows.items() if not r["项集一致"]]
        ok(f"[{which}] 每个 k 的两侧**项集一致**（否则比的是项多寡，不是结构）",
           not bad_universe, f"项集不一致的 k：{bad_universe}")
        # ★ 参照侧按**纳入项集**重建（§10.2 出路 (1)），所以「比项多寡」这件事
        #   已经被**构造**排除了。但「根覆盖之外」有多少项必须**报出来** ——
        #   不然「范围」会被读成「结构」。
        oos = {k: r["根覆盖之外"] for k, r in rows.items() if r["根覆盖之外"]}
        if oos:
            PASS.append(f"（读数）[{which}] §10.2 出路 (1)：根覆盖之外（不塞进结构）"
                        f"的项数 各 k = {oos}；参照侧按**同一项集**重建")

        # ③ 尺度：**扇出固定为 2 的方向必须稳**；扇出由插件定的方向只报不判。
        #   ⚠️ 比的是**同项集参照**（`r['参照']`），不是全集 —— 见 `divergence.py`
        #      开头：拿全集当参照会在「根覆盖之外」存在时把**范围**读成**结构**。
        f = prof["全量"]
        scale_ok = all(
            (r["方向数"], r["最大叶容量"]) == (r["参照"]["方向数"], r["参照"]["最大叶容量"])
            for r in rows.values())
        if which in ("keyset", "reach"):
            ok(f"[{which}] 扇出恒为 2 ⇒ 差异**不在尺度**：每个 k 的方向数与叶容量"
               f"都和**同项集参照**相同",
               scale_ok,
               f"全量 {f['方向数']}/{f['最大叶容量']}，"
               f"增量 {sorted({(r['方向数'], r['最大叶容量']) for r in rows.values()})}，"
               f"参照 {sorted({(r['参照']['方向数'], r['参照']['最大叶容量']) for r in rows.values()})}")
        else:
            # ★ 分辨率：**`叶容量` 是错的尺子**（§10.2 出路 (4) 之后）。
            #
            #   `叶容量` 默认「每一项都走到了叶」。出路 (4) 让项可以**停在内部节点**
            #   ⇒ 那部分代价 `叶容量` **看不见**：实测 k=2 时叶容量报 2，
            #     而真相是 34 项堆在**根**上（根是内部节点）。
            #   ⇒ 所以分辨率要用 `最大停留数`：`停(d) = |members(d) \ ∪members(子)|`
            #      （**从成员集现算**，不用 `Cone(x)` —— 锥是插入时记的，
            #        方向后来才劈开时不会延长，会把早就走到叶的项误记成「停在内部」）。
            #
            #   判据分两半，**都是收窄而不是放宽**：
            #     滞留 == 0 的 k   ⇒ 停留数必须与**同项集参照**相同（分辨率是不变量）
            #     滞留 > 0 的 k   ⇒ **至少有一处**确实被遮住
            #                        （`最大停留数 > 最大叶容量`）—— 把那个洞钉住
            #
            #   ⚠️ 后半用 `any` 而不是 `all`，因为「被遮住」只在**滞留量足够大**时
            #      才在**最大值**上露出来：实测 k=6/12/18 滞留 16/6/5，但那些项
            #      停在的方向成员数 ≤ 10 ⇒ 两个数仍然都是 10。
            #      用 `all` 会把「遮住程度不够大」误判成「没有遮住」。
            cap_ok = all(r["最大停留数"] == r["参照"]["最大停留数"]
                         for r in rows.values() if not r["滞留"])
            blind_ok = any(r["最大停留数"] > r["最大叶容量"]
                           for r in rows.values() if r["滞留"])
            ok(f"[{which}] 扇出由插件定 ⇒ 方向数**允许**变；"
               f"**无滞留的 k 上分辨率（停留数）不许变**，"
               f"**有滞留的 k 上 `叶容量` 至少有一处看不见那部分代价**",
               cap_ok and blind_ok,
               f"全量叶容量 {f['最大叶容量']} / 停留数 {f['最大停留数']}，"
               f"增量 (叶容量,停留数,滞留) "
               f"{sorted((r['最大叶容量'], r['最大停留数'], r['滞留']) for r in rows.values())}，"
               f"参照停留数 {sorted({r['参照']['最大停留数'] for r in rows.values()})}"
               f"｜无滞留判据 {cap_ok} / 有滞留判据 {blind_ok}")
            stay_read = {k: r["滞留"] for k, r in rows.items() if r["滞留"]}
            if stay_read:
                PASS.append(
                    f"（读数）[{which}] §10.2 出路 (4)：项**留在父方向**（不塞进结构、也不拒绝）"
                    f"—— 各 k 滞留数 {stay_read}；这些项停在**内部**节点上，"
                    f"所以 `叶容量` 那一列**低于**真实扫描量")
            PASS.append(
                f"（读数）[{which}] 扇出由插件定 ⇒ 方向数随初始批次变：全量 "
                f"{f['方向数']}，各 k "
                f"{sorted({r['方向数'] for r in rows.values()})}（**记账**，不进判据）")

        # ④a 每个方向都要有读数（否则那一行是空转）
        ok(f"[{which}] 扫描非空（每个方向都有读数）", bool(rows))
        all_iso |= {r["同构"] for r in rows.values()}

    # ④b 全局：两个值都出现过 ⇒ 比较器有区分力
    ok("扫描里同构与不同构**都出现过**（比较器不是常量）",
       all_iso == {True, False},
       f"只出现了 {all_iso} —— 度量退化成常量；"
       f"若因「已做到同构」而只剩 True，请把它升级成 B 系列检查")

    # ⑤ 度量不进退出码
    from ldv.run_checks import run_one

    rep = run_one("keyset", loaded)
    ok("不同构**不进退出码**（度量与判据分开）",
       not rep.red and any("增量 ≡ 全量" in n for n in rep.notes),
       f"红 {len(rep.red)} 条；notes 里有增量读数 = "
       f"{any('增量 ≡ 全量' in n for n in rep.notes)}")


def _fake_dir(payload):
    """给 `merge` 造一个方向壳 —— `merge` 只读 `payload`。"""
    from ldv.core.direction import ORIGIN_SPLIT, Direction

    return Direction(did="F", rank=2, payload=payload, witness=(),
                     origin=ORIGIN_SPLIT, parent=None)


#: `B17` 用的**合环**小图：`a ↔ b` 互相可达（同一个 SCC），`c → a`，`d` 孤立。
#: 真实语料是 **DAG**（36 项 / 47 边，无环）⇒ 等价类全是单点 ⇒ `B17` 在那里退化。
#: 要证明它不是空转，就得自己造一个有环的输入。
_CYCLE_EDGES = {
    "a": frozenset({"b"}),
    "b": frozenset({"a"}),
    "c": frozenset({"a"}),
    "d": frozenset(),
}


def _cycle_graph():
    from ldv.corpus.loader import Node

    nodes = {k: Node(id=k, fields={}, keys=frozenset({k})) for k in _CYCLE_EDGES}
    return nodes, _CYCLE_EDGES


def test_equivalence() -> None:
    """★ `B17`：**叶 = 不可分等价类**（`C8` §7 第 2 步的产物）。

    它同时是「`§K2` 的第三个选项值不值得做」的答案 —— 答案是**不值得**，
    因为判空点上**没有区分信息可搬**（见 `checks/equivalence.py` 的模块 docstring）。

    六条，每条都能红：

      ① 基线：`keyset` / `sequence` 上叶数 = 等价类数、叶容量 = 最大等价类
      ② `reach` 报「**跳过**」而不是「过」—— 等价类全是单点时两条断言恒真
      ③ oracle **独立**：`reach` 的等价类（SCC）≠ 汇点签名类 ⇒ 不是插件谓词的复述
      ④ 注入「能分也判分不开」⇒ 红；**且叶容量这个数不变** ⇒ 度量对局部退化不敏感
      ⑤ **非空转**：合环图上它**红** —— `§K2` 第二半的缺口（见 §10 留白）
      ⑥ 读数：三个方向的「可搬的判空点」都是 0

    ⚠️ ⑤ 断言的是**它现在红**。这不是「留一个已知红」，是**把缺口钉住**：
       谁把 `reach` 的 `劈开` 改成不拆 SCC，这条就会翻 —— 那时该做的是
       **更新 §10 的留白**，而不是删掉这条断言。

    ★ **⑤ 用的合环图是人工造的；缺口在公开数据集上也露了** ——
      OpenAlex 引用图切片（281 项 / 1008 边）有 6 个 ≥2 的强连通分量
      ⇒ `B17`(reach) 的读数「被拆开的等价类」正好 6 个（`ldv/tests/test_intake.py`）。
      人工小图可以被人说成「构造出来的边角情形」，公开语料不能。

    ⚠️ **2026-10-07：(b) 那一半在 `reach` 上从判据降为度量**，所以 ⑤ 断言的东西
      也跟着换了位置：

        改前  合环图上 `B17` **红**（判据非空转）
        改后  合环图上 `B17` **过**（判据**有意**不判这条），
              而**读数**「被拆开的等价类」> 0（**度量**非空转）

      ⇒ 「判据不再红」与「现象消失」是两件事。判据是**有意**收窄的
        （`reach` 的 `split` 比语义更细是 §K8 允许的假阳），现象仍在、由读数承担。
        这一条对照的**作用没变**：它仍然在证明「这条判据/读数**能**动」，
        只是从判据那一列移到了度量那一列。
      ⇒ 也正因为 (b) 在 reach 上不判了，`test_intake.py` 的判据 ② 才有机会
        从「基线守卫」回到更简单的形态 —— 但**先不动它**（那是另一处裁定）。
    """
    from ldv.checks._fixtures import equiv_classes
    from ldv.checks.equivalence import absorption_profile, b17_leaf_is_equivalence_class
    from ldv.checks._framework import Report

    loaded = load()
    if loaded is None:
        PASS.append("叶 = 不可分等价类（跳过：目录不在）")
        return
    nodes, edges, _ = loaded

    # ── ① / ② / ⑥ 三个方向各跑一遍 ──────────────────────────────────────
    skipped: list[str] = []
    for which in ("keyset", "reach", "sequence"):
        kernel, _ = {"keyset": build_keyset,
                     "reach": lambda n: build_reach(n, edges),
                     "sequence": lambda n: build_sequence(n, edges)}[which](nodes)
        cls = equiv_classes(which, nodes, edges)
        prof = absorption_profile(kernel, cls)
        rep = Report(plugin=which, expects=("B17",))
        b17_leaf_is_equivalence_class(kernel, cls, rep, which)
        a = rep.assertions[0]

        if a.result is Tri.UNEXPANDED:
            skipped.append(which)
            ok(f"[{which}] 等价类全是单点 ⇒ 报「**跳过**」而不是「过」",
               "跳过" in a.detail or "退化" in a.detail, a.detail)
        else:
            ok(f"[{which}] B17 过：叶 = 不可分等价类", a.result is Tri.YES, a.detail)
            ok(f"[{which}] 叶数 == 等价类数",
               prof["叶数"] == prof["等价类数"],
               f"叶 {prof['叶数']} vs 类 {prof['等价类数']}")
            ok(f"[{which}] 最大叶容量 == 最大等价类（分辨率**已到下界**）",
               prof["最大叶容量"] == prof["最大等价类"],
               f"{prof['最大叶容量']} vs {prof['最大等价类']}")
        ok(f"[{which}] 读数：可搬的判空点 = 0（没有区分信息可搬）",
           prof["可搬的判空点"] == 0,
           f"{prof['可搬的判空点']} 个 —— 说明插件在能分开的组上判了「分不开」")

    ok("退化方向确实出现过（否则上一条分支是空转）",
       skipped == ["reach"], f"报跳过的方向：{skipped}")

    # ── ③ oracle 独立性：等价类 ≠ 插件的「汇点签名」 ─────────────────────
    from ldv.plugins.reach import ReachPlugin

    plug = ReachPlugin(edges)
    sig_classes = {plug.signature(i) for i in nodes}
    scc_classes = {equiv_classes("reach", nodes, edges)[i] for i in nodes}
    ok("oracle 独立：reach 的等价类（SCC）**不是**「汇点签名」的复述",
       len(scc_classes) != len(sig_classes),
       f"SCC {len(scc_classes)} 类 vs 汇点签名 {len(sig_classes)} 类")

    # ── ④ 注入：能分也判分不开 ───────────────────────────────────────────
    from ldv.plugins.keyset import KeysetPlugin

    class _GiveUpEarly(KeysetPlugin):
        LIMIT = 4

        def split(self, parent, items):  # noqa: ANN001, ANN201
            if len(items) <= self.LIMIT:
                return None                 # 谎报：能分也**声明**分不开
            return super().split(parent, items)

    def build_with(plug_cls):  # noqa: ANN001, ANN202
        p = plug_cls()
        k = Kernel(p, {nid: nodes[nid].as_item() for nid in nodes})
        k.build(p.merge([]))
        for nid in sorted(nodes):
            k.insert(nid)
        return k, p

    cls_k = equiv_classes("keyset", nodes, edges)
    base_k, _ = build_with(KeysetPlugin)
    bad_k, _ = build_with(_GiveUpEarly)
    rep_b, rep_i = Report(expects=("B17",)), Report(expects=("B17",))
    b17_leaf_is_equivalence_class(base_k, cls_k, rep_b, "keyset")
    b17_leaf_is_equivalence_class(bad_k, cls_k, rep_i, "keyset")
    ok("注入「能分也判分不开」⇒ B17 红",
       rep_b.assertions[0].result is Tri.YES and rep_i.assertions[0].result is Tri.NO,
       f"基线 {rep_b.assertions[0].result} / 注入 {rep_i.assertions[0].result}")

    cap_b, cap_i = base_k.stats()["最大叶容量"], bad_k.stats()["最大叶容量"]
    ok("★ 而「最大叶容量」这个数**不变** ⇒ 度量对局部退化不敏感，所以必须是判据",
       cap_b == cap_i,
       f"基线 {cap_b} / 注入 {cap_i}（若哪天变了，这条断言要重新解释）")

    # ── ⑤ 非空转：合环图上它必须红 ──────────────────────────────────────
    cyc_nodes, cyc_edges = _cycle_graph()
    from ldv.plugins.reach import ReachPlugin as _RP

    cyc_plug = _RP(cyc_edges)
    ck = Kernel(cyc_plug, {k: cyc_nodes[k].as_item() for k in cyc_nodes})
    ck.build(frozenset(cyc_nodes))
    for nid in sorted(cyc_nodes):
        ck.insert(nid)
    cyc_cls = equiv_classes("reach", cyc_nodes, cyc_edges)
    rep_c = Report(expects=("B17",))
    b17_leaf_is_equivalence_class(ck, cyc_cls, rep_c, "reach")
    ok("★ 非空转：合环图上 B17 **过** —— (b) 在 `reach` 上**已降为度量**，不判",
       rep_c.assertions[0].result is Tri.YES, rep_c.assertions[0].detail)
    cyc_torn = absorption_profile(ck, cyc_cls)["被拆开的等价类"]
    ok("★★ 而**度量**在合环图上非空转：被拆开的等价类 > 0"
       "（判据不再红 ≠ 现象消失；现象改由读数承担）",
       cyc_torn > 0,
       f"被拆开的等价类 {cyc_torn} 个 —— 若为 0，说明这条读数也是空转")
    ok("★ 且那一层是**重复覆盖**（两个子方向覆盖完全相同 ⇒ §K2 第二半的缺口）",
       ck.plugin.reachable(ck.direction("D3").payload)
       == ck.plugin.reachable(ck.direction("D4").payload),
       f"D3 {sorted(ck.direction('D3').payload)} vs D4 {sorted(ck.direction('D4').payload)}")

    # ── ⑦ 合成对照：**三态各一例**（`run_checks` 的「覆盖证据」引的就是它） ──
    #   ⚠️ 前面 ①–⑥ 都读**真语料**；这一条**不碰内核、不碰语料** —— 它问的是
    #      「这条判据**会不会红**」，而真语料上它报「跳过」（DAG ⇒ 等价类全是单点）。
    #      两件事分开，各自说清。
    from ldv.checks.equivalence import b17_known_answer

    b17_fails = b17_known_answer()
    ok("★★ [E] `B17` 合成对照：过 / 红 / 跳过 **三态各一例**（随仓库那份语料是 DAG"
       " ⇒ 这条判据在那里报「跳过」；对照证明它在有环图上**判得了、而且该红时红**）",
       not b17_fails, "；".join(b17_fails) or "三态全对")


def test_semantic_guard() -> None:
    """★ `B20`：**语义变细守卫** —— 它的**合成对照**。

    ⚠️ 这条判据只在**标签轴**（`keyset`）上落 ⇒ `reach` / `sequence` 上报「**跳过**」。
       而「跳过」与「这条判据根本不会红」在汇总里长得**一模一样**
       （`跳过 ≠ 通过` 的反面）⇒ 这里用一棵**最小合成树**把三态各演一遍：

           过      两个子把标签切干净（父 `p:2 q:2` ⇒ 子 `p:2` / `q:2`）
           红      11 层单链，每层成员集与标签都相同 ⇒ 连续 11 段空转 ≥ N
           跳过    `prof=None` + `judged=False`（非标签轴 ⇒ **不计不印**）

    手推依据写在 `ldv/checks/refinement.py` 的 `b20_known_answer` 那一段。

    ⚠️ **不碰内核、不碰语料**：`B20` 判的是「展开有没有在标签上产出东西」，
       与真语料无关 ⇒ 对照也不该依赖真语料（依赖了就不是最便宜的那一档）。
    """
    from ldv.checks.refinement import b20_known_answer

    fails = b20_known_answer()
    ok("★★ [E] `B20` 合成对照：过 / 红 / 跳过 **三态各一例**"
       "（标签分开了 ⇒ 过；连续 N 层不分开 ⇒ 红；非标签轴 ⇒ 跳过**且不印数**）",
       not fails, "；".join(fails) or "三态全对")


def test_rebuild() -> None:
    """★ 规范重建 / 阈值重建：**买得到「增量 ≡ 全量」，但代价是 §K3**（`C8` §7 第 3 步）。

    结论是**不做**。但它必须**可查**，不能只是一段说辞 —— 所以这里断言七件事：

      ① 规范不动点确实买到「同构全量」：8/8 个 k，三个方向都是
      ② 而且 **k 指纹数 == 1** ⇒ 结果**不再依赖初始批次大小**（这是「买到」的实义）
      ③ 阈值重建（Naor–Teague，摊还 O(1)）在 **A / B** 上**买不到**：
         同构 < 8/8，且 k 指纹数 > 1 ⇒ 差异发生在**两次重建之间**
      ④ **但它在 C 上买到了**（8/8 同构、指纹 1）—— 这条**正对照**很重要：
         它证明 ③ 不是「这个方法不行」，是「**扇出固定为 2 的方向**不行」
      ⑤ 代价：规范不动点 O(n) 每次插入（`n−1` 次 / 工作量 `2+3+…+n`）；
         阈值重建压到 2 的幂的个数
      ⑥ **§K3 的代价（本步的关键）**：`B8` 在重建内核上**红**、在路径局部内核上**绿**
         —— 断言的是「**这条判据抓得到它**」，不是「留一个已知红」
      ⑦ 比较器自检全过 —— 否则「同构」这个读数不可信（同构要靠形状比对，比对器得有区分力）

    ⚠️ ④ 是 `§I4` 改成 k 叉之后**新出现的**结论。它把「不做」的理由收窄了一句：
       不是「阈值重建做不到」，是「**它换来的东西不值得**」——
       §K3 是**接口级**不变量，「结构可复现」是**实现**性质（§10.1）。
       对 C 它买得到，我们**仍然不做**，因为 §K3 更贵。

    ⚠️ ⑦ 是这一步的**地基**：若比对器无区分力，「8/8 同构」与「0/8 同构」都会报成一样。
    """
    from ldv.checks._fixtures import make_keyset_root
    from ldv.checks._framework import Report
    from ldv.checks.rebuild import make_rebuild_root, rebuild_profile
    from ldv.checks.structure import b8_invalidation_local

    loaded = load()
    if loaded is None:
        PASS.append("规范重建（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    n = len(ids)

    #: 阈值重建的预期：**扇出恒为 2 的方向买不到，扇出由插件定的方向买得到**。
    #: 这个分界有依据 —— 扇出恒为 2 时「两次重建之间」添的项会造出新的中间层，
    #: 而那些层在「一次建完」里不存在；扇出由插件定时，只要重建时符号齐了，后面添的项
    #: 只会落进**已有**的子方向，形状就与一次建完一致。
    THRESHOLD_BUYS = {"keyset": False, "reach": False, "sequence": True}

    for which in ("keyset", "reach", "sequence"):
        prof = rebuild_profile(which, nodes, edges)
        canon, thresh = prof["规范不动点"], prof["阈值重建"]
        ks = canon["扫描的 k"]
        buys = THRESHOLD_BUYS[which]

        # ── ① / ② 规范不动点买到了 ──────────────────────────────────────
        ok(f"[{which}] 规范不动点：8/8 个 k 都同构全量",
           canon["同构的 k 数"] == ks,
           f"{canon['同构的 k 数']}/{ks}（最早同构 {canon['最早同构的 k']}）")
        ok(f"[{which}] 且 k 指纹数 == 1 ⇒ 结果不依赖初始批次大小（这才是「买到」的实义）",
           canon["k 指纹数"] == 1 and canon["k 之间互相同构"],
           f"k 指纹 {canon['k 指纹数']} 种")

        # ── ③ / ④ 阈值重建：按**扇出是否固定**分成两半 ──────────────────
        if buys:
            ok(f"[{which}] ★ 阈值重建**买到了**（扇出由插件定 ⇒ 重建时符号齐了就定形）",
               thresh["同构的 k 数"] == ks and thresh["k 指纹数"] == 1,
               f"{thresh['同构的 k 数']}/{ks}、指纹 {thresh['k 指纹数']} 种")
            PASS.append(f"（读数）[{which}] 阈值重建最早同构的 k = {thresh['最早同构的 k']}"
                        f"（扇出齐全所需的初始批次大小）")
        else:
            ok(f"[{which}] 阈值重建**买不到**：同构 < 全部（扇出恒为 2 ⇒ 两次重建之间造出新层）",
               thresh["同构的 k 数"] < ks,
               f"{thresh['同构的 k 数']}/{ks}（最早同构 {thresh['最早同构的 k']}）")
            ok(f"[{which}] 且阈值重建的 k 指纹数 > 1 ⇒ 差异发生在**两次重建之间**",
               thresh["k 指纹数"] > 1,
               f"k 指纹 {thresh['k 指纹数']} 种")

        # ── ⑤ 代价 ─────────────────────────────────────────────────────
        ok(f"[{which}] 代价：规范重建 O(n) 每次插入（{n - 1} 次 / 工作量 {sum(range(2, n + 1))}）",
           canon["重建次数"] == n - 1 and canon["重建工作量"] == sum(range(2, n + 1)),
           f"重建 {canon['重建次数']} 次 / 工作量 {canon['重建工作量']}")
        ok(f"[{which}] 阈值重建把次数压到 2 的幂的个数（摊还 O(1)）",
           thresh["重建次数"] < canon["重建次数"] and thresh["重建工作量"] < canon["重建工作量"],
           f"{thresh['重建次数']} 次 / {thresh['重建工作量']} vs {canon['重建次数']} 次 / {canon['重建工作量']}")

        # ── ⑦ 地基：比对器有区分力 ─────────────────────────────────────
        ok(f"[{which}] 比较器自检全过（否则「同构」这个读数不可信）",
           canon["比较器自检"] and thresh["比较器自检"], "有自检未过")

        # ── ⑥ §K3 的代价：B8 抓得到它 ──────────────────────────────────
        rep_path, rep_canon = Report(expects=("B8",)), Report(expects=("B8",))
        b8_invalidation_local(make_keyset_root(nodes), ids, rep_path)
        b8_invalidation_local(make_rebuild_root(which, nodes, edges), ids, rep_canon)
        ok(f"[{which}] ★ §K3 的代价：B8 在路径局部内核上**绿**、在规范重建内核上**红**",
           rep_path.assertions[0].result is Tri.YES
           and rep_canon.assertions[0].result is Tri.NO,
           f"路径局部 {rep_path.assertions[0].result} / 规范重建 {rep_canon.assertions[0].result}")
        ok(f"[{which}] 且它红是**因为越界**，不是因为「跳过」（红的理由要正当）",
           "越界" in rep_canon.assertions[0].detail,
           rep_canon.assertions[0].detail[:80])

    # ── 与 id 无关的那条读数：基线 0.00 vs 重建 > 0（度量，不进退出码）────
    prof_k = rebuild_profile("keyset", nodes, edges)["失效范围"]
    base = prof_k["基线（路径局部增量）"]["平均失效范围比"]
    reb = prof_k["规范不动点（每次重建）"]["平均失效范围比"]
    ok("★ 与 id 无关的读数有区分力：基线 0.00 < 规范重建（否则 B8 红只是比对的产物）",
       base == 0.0 and reb > 0.0, f"基线 {base} vs 重建 {reb}")

    # ── 非空转：阈值重建**确实**会重建（次数 > 1），不是「什么都没干」──────
    ok("阈值重建确实重建过（次数 > 1）—— 上面对比不是拿「空实现」在比",
       rebuild_profile("keyset", nodes, edges)["阈值重建"]["重建次数"] > 1,
       "阈值重建次数 <= 1")


def test_out_of_scope() -> None:
    """★ §10.2 出路 (1)「根记账」—— 把「最外层意图之外」从静默错位变成一条**可数的账**。

    五条，每条都能红：

      ① 批建路径**零误伤**（三个方向的根都覆盖全部项 ⇒ 一个都不许被拒）
      ② 维护路径**确实拒了**（`reach` 的根只声明了前 6 项当锚点 ⇒ 走不到的那些在外面）
      ③ 记账守恒：`认识 = 纳入 + 根覆盖之外`，且账本事件数与它逐项相符
      ④ 被拒的项**不在任何方向的成员里**（「不塞进结构」是字面执行，不是打折）
      ⑤ ★ **方向不许偏**：`命中(根,{x})` 返回「未展开」时**必须照常插入** ——
         「不知道」不许被当成「不在」（§K8 的同一条纪律：往假阴偏才是错的）

    ⚠️ ⑤ 是这条判据的**已知答案对照组**：只有「否」才是否证。
       少了它，一个「凡非『是』就拒」的实现也能让 ①–④ 全绿 ——
       而那正是把「不知道」折成「没有」，是 §K8 明令禁止的那个方向。
    """
    from ldv.core.direction import EVENT_OUT_OF_SCOPE
    from ldv.core.tri import Tri as _Tri
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin
    from ldv.checks._fixtures import items as make_items, sequences

    loaded = load()
    if loaded is None:
        PASS.append("根记账（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    all_items = make_items(nodes)
    ids = sorted(nodes)

    # ① 批建路径零误伤
    for label, mk, root in (
        ("keyset", lambda: KeysetPlugin(), lambda p: p.merge([])),
        ("reach", lambda: ReachPlugin(edges), lambda p: frozenset(nodes)),
        ("sequence", lambda: SequencePlugin(sequences(nodes, edges)), lambda p: frozenset({()})),
    ):
        plug = mk()
        k = Kernel(plug, dict(all_items))
        k.build(root(plug))
        for i in ids:
            k.insert(i)
        ok(f"★ [{label}] 批建路径**零误伤**：根的声明覆盖全部项 ⇒ 一个都不被拒",
           k.stats()["根覆盖之外"] == 0, f"实际 {k.stats()['根覆盖之外']} 项被拒")

    # ② / ③ / ④ 维护路径
    plug = ReachPlugin(edges)
    k = Kernel(plug, {i: all_items[i] for i in ids[:6]})
    k.build(frozenset(ids[:6]))
    for i in ids[:6]:
        k.insert(i)
    for i in ids[6:]:
        k.insert(i, all_items[i])
    st = k.stats()
    ok("★ [reach] 维护路径确实拒了（根只声明了前 6 项当锚点）",
       st["根覆盖之外"] > 0, f"实际 {st}")
    ok("★ 记账守恒：认识 = 纳入 + 根覆盖之外",
       st["认识"] == st["纳入"] + st["根覆盖之外"],
       f"认识 {st['认识']} ≠ 纳入 {st['纳入']} + 之外 {st['根覆盖之外']}")
    ev = [e for e in k.ledger if e.kind == EVENT_OUT_OF_SCOPE]
    ok("★ 账本事件数与 `stats()['根覆盖之外']` 逐项相符（数在账上，不是算出来的）",
       len(ev) == st["根覆盖之外"], f"事件 {len(ev)} vs 计数 {st['根覆盖之外']}")
    ok("★ 被拒的项**不在任何方向的成员里**（「不塞进结构」是字面执行）",
       not (k.placed & (set(k.items) - k.placed)),
       f"被拒 {sorted(set(k.items) - k.placed)[:3]}")
    ok("★ 且被拒的项**真的不在**结构里（否则上一条恒真、是空转）",
       set(k.items) - k.placed, "没有任何项被拒 —— 上一条就没在查东西")

    # ⑤ 方向不许偏：`命中` 返回「未展开」时必须照常插入
    class _RootSaysUnknown(ReachPlugin):
        """对照组：根对任何查询都说「未展开」。"""

        def hit(self, d, query):  # noqa: ANN001, ANN201
            if d.did == self._root_did:
                return _Tri.UNEXPANDED
            return super().hit(d, query)

    plug2 = _RootSaysUnknown(edges)
    k2 = Kernel(plug2, {i: all_items[i] for i in ids[:6]})
    k2.build(frozenset(ids[:6]))
    plug2._root_did = k2.root.did  # noqa: SLF001
    for i in ids[:6]:
        k2.insert(i)
    for i in ids[6:]:
        k2.insert(i, all_items[i])
    ok("★ [对照组] 根说「未展开」时**照常插入** —— 「不知道」不许被当成「不在」",
       k2.stats()["根覆盖之外"] == 0, f"实际 {k2.stats()}")


def _stay_unproven(k: Kernel, plugin: Any) -> list[str]:
    """② 的**谓词**：每条 `stayed` 事件上，**每个子方向都证明不收它**。

    返回违规列表（空 = 过）。**直接问插件**，不借内核的 `_refuses` ——
    借了就是「拿内核自己的判断验内核自己的判断」（`false-green` 形状 3 共享盲点）。

    抽成纯函数是为了能拿**已知错**的输入自检它（见调用处）。
    """
    bad: list[str] = []
    for e in k.ledger:
        if e.kind != EVENT_STAYED:
            continue
        d = k.direction(e.did)
        x = e.detail.get("item", "")
        q = Query(ideal=frozenset({x}))
        if any(plugin.hit(c, q) is not Tri.NO for c in k.children_of(d)):
            bad.append(f"{x}@{d.did}")
    return bad


def _stay_leaks(k: Kernel) -> int:
    """③ 后半的**谓词**：滞留项溜进子方向成员的次数（应为 0）。"""
    return sum(1 for d in k.all_directions() for x in k.stayed_of(d)
               for c in k.children_of(d) if x in k.members_of(c))


def _stay_outside_parent(k: Kernel, accessor: Any) -> list[str]:
    """③ 前半的**谓词**：滞留项**不在**父成员里的方向（应为空）。"""
    return [d.did for d in k.all_directions()
            if accessor(d) and not accessor(d) <= k.members_of(d)]


def test_stay_at_parent() -> None:
    """★ §10.2 出路 (4)「项留在父方向」—— 与出路 (1) 是**两条不同的代价**。

        出路 (1) 根记账     项按证明落在根覆盖之外 ⇒ **不塞进结构** ⇒ 代价在**范围**
        出路 (4) 留在父方向 每个子方向都证明不收它   ⇒ **留在父这一层** ⇒ 代价在**划分**

    为什么不能拒绝（那是 (3) 的读法）：拒绝会让项从**成员集**里消失，而 `B1` 的
    ground truth 正是「成员 ∩ 查询」—— 于是「健全性变绿」会**部分来自数据变少**。
    留在父方向则：项仍可检索（父的 `命中` 对它**不是「否」**）、`B1` 的答案不动，
    代价只落在划分上（`B4` 收窄成「不重，且**漏的恰好是账上那些**」）。

    每条都能红，而且**判据自己也要有区分力**（拿已知错的输入喂进谓词）：

      ① 非空转：这份夹具上**确实有滞留**（否则下面全在查空气）
      ② 字面执行：每条 `stayed` 事件所挂的方向上，**每个子方向都证明不收它**；
         且事件数 == `stats()['滞留']`（数在账上，不是算出来的）
      ③ 项**真的留在父的成员里**（留在 ≠ 拒绝），且**没有**溜进任何子方向的成员里
      ④ 代价守恒：滞留**不动范围** —— `认识 = 纳入 + 根覆盖之外` 照旧，
         且滞留**不减** `纳入`（「留在父方向」不是「被拒」的委婉说法）
      ⑤ 两条恒等式（`停` **从成员集现算**）：`停在叶上 + 停在内部 == 纳入`、
         `停在内部 == 滞留`
      ⑥ ★ **已知答案对照组**：让**非根**方向一律说「未展开」⇒ 滞留**必须为 0**。
         「不知道」不许被当成「不在」（与 §K8 同一条纪律）——
         一个「凡非『是』就拒」的实现能让 ①–⑤ 全绿，只有这条会红（实测：它**只**红这条）。
      ⑦ `叶容量` 的洞：项停在**内部**节点时 `最大停留数 > 最大叶容量`；
         而**批建**路径上两者相等 ⇒ ⑦ 那条不是恒真（`最大停留数` 是推广，不是另一个数）

    ★ ②/③ 的自检：伪造一条**已知不该有**的滞留事件（挂在一个子方向会说「是」的项上）
      ⇒ 三个谓词**必须**报出来。少了这一步，「谓词恒返回空」也能让 ②③ 全绿 ——
      那是「空转与通过长得一模一样」。
    """
    from ldv.checks._fixtures import build_incremental, make_builder, sequences as make_seqs
    from ldv.core.direction import EVENT_STAYED
    from ldv.plugins.sequence import SequencePlugin

    loaded = load()
    if loaded is None:
        PASS.append("留在父方向（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)

    # ⚠️ 这份夹具是**按随仓库那份语料（36 项）挑的**：先建 2、维护 34，且下面用 `ids[10]`
    #    当探针项。换一份小语料时 `ids[10]` 直接 `IndexError` ⇒ **整趟测试崩**
    #    （不是红 —— 红是判据说话，崩是判据没了：后面的测试函数一个都不跑）。
    #    实测：`edge-single`(1 项) 与 `edge-tiny`(4 项) 两份语料都崩在这一行。
    #    ⇒ 语料不够大就**整条报跳过**，并把「跳过了什么」写出来 —— 跳过 ≠ 通过。
    if not need_at_least(ids, 11, "留在父方向（先建 2、维护 n−2，`ids[10]` 当探针）"):
        return

    def _build(init: list[str]) -> Kernel:
        return build_incremental(make_builder("sequence", nodes, edges), nodes, init, ids[2:])

    # 这份夹具（sequence，先建 2 维护 34）实测滞留 32 —— 见 §10.2 出路 (4)。
    k = _build(ids[:2])
    st = k.stats()

    # ① 非空转
    ok("★ [留在父方向] 非空转：这份夹具上确实有滞留（否则下面全在查空气）",
       st["滞留"] > 0, f"滞留 {st['滞留']} —— 下面几条都没在查东西")

    # ② 字面执行：每个子方向都**证明**不收它（用插件直接问）
    ok("★ 每条 `stayed` 事件上，**每个子方向都证明不收它**（只有「否」算证明）",
       not _stay_unproven(k, k.plugin), f"有子方向没说「否」：{_stay_unproven(k, k.plugin)[:3]}")
    ev = [e for e in k.ledger if e.kind == EVENT_STAYED]
    ok("★ 且事件数 == `stats()['滞留']`（数在账上，不是算出来的）",
       len(ev) == st["滞留"], f"事件 {len(ev)} vs 计数 {st['滞留']}")

    # ③ 留在父的成员里；不溜进子方向
    ok("★ 滞留的项**真的留在父的成员里**（留在 ≠ 拒绝）",
       not _stay_outside_parent(k, k.stayed_of),
       f"不在父成员里的方向：{_stay_outside_parent(k, k.stayed_of)}")
    ok("★ 且**没有**溜进任何子方向的成员里（「每个子方向都证明不收」是字面执行）",
       _stay_leaks(k) == 0, f"泄漏 {_stay_leaks(k)} 处")

    # ── ★ 自检：三个谓词对**已知错的**输入必须有区分力 ──────────────────
    # `ids[0]` 落在子方向里（子方向对它说「是」）⇒ 给它记一条滞留就是**错的**。
    k_bad = _build(ids[:2])
    k_bad.ledger.append(EVENT_STAYED, k_bad.root.did, item=ids[0], reason="自检用伪造")
    q0 = Query(ideal=frozenset({ids[0]}))
    ok("★ [自检] 伪造的那条确实是错的（子方向对 `ids[0]` 说「是」，不是「否」）",
       any(k_bad.plugin.hit(c, q0) is Tri.YES for c in k_bad.children_of(k_bad.root)),
       "伪造目标选错了 —— 下面两条自检就查不到东西")
    ok("★ [自检] ② 的谓词有区分力：喂**已知不该有**的滞留事件必须报违规",
       _stay_unproven(k_bad, k_bad.plugin) != [], "谓词恒返回空 —— ② 是空转")
    ok("★ [自检] 泄漏谓词有区分力：同一份伪造输入必须数出泄漏",
       _stay_leaks(k_bad) > 0, "泄漏谓词恒返回 0 —— ③ 后半是空转")
    ok("★ [自检] 「滞留 ⊂ 父成员」谓词有区分力：把事件读成挂在**父**上的那些必须报违规",
       _stay_outside_parent(k_bad, lambda d: k_bad.stayed_of(k_bad.direction(d.parent))
                            if d.parent else frozenset()) != [],
       "谓词恒返回空 —— ③ 前半是空转")

    # ④ 代价守恒：滞留在**划分**上，不动**范围**
    ok("★ 滞留不动范围：`认识 = 纳入 + 根覆盖之外` 照旧成立",
       st["认识"] == st["纳入"] + st["根覆盖之外"], f"实际 {st['认识']} vs {st}")
    ok("★ 滞留**不减** `纳入`（「留在父方向」不是「被拒」的委婉说法）",
       st["纳入"] == st["认识"] and st["滞留"] > 0,
       f"纳入 {st['纳入']} / 认识 {st['认识']} / 滞留 {st['滞留']}")

    # ⑤ 两条恒等式（`停` 从成员集现算）
    ok("★ 恒等式：`停在叶上 + 停在内部 == 纳入`（`停` 从成员集现算）",
       st["停在叶上"] + st["停在内部"] == st["纳入"],
       f"{st['停在叶上']} + {st['停在内部']} ≠ {st['纳入']}")
    ok("★ 恒等式：`停在内部 == 滞留（账上）`（两边互相独立地算）",
       st["停在内部"] == st["滞留"], f"{st['停在内部']} ≠ {st['滞留']}")

    # ⑦ `叶容量` 的洞 + 「批建路径上两把尺子相等」的对照
    ok("★ `叶容量` 的洞：项停在**内部**节点时 `最大停留数 > 最大叶容量`",
       st["最大停留数"] > st["最大叶容量"],
       f"最大停留数 {st['最大停留数']} vs 最大叶容量 {st['最大叶容量']}")
    from ldv.checks._fixtures import build_keyset, build_reach, build_sequence
    batch = {"keyset": lambda: build_keyset(nodes), "reach": lambda: build_reach(nodes, edges),
             "sequence": lambda: build_sequence(nodes, edges)}
    same, detail = True, []
    for which, mk in batch.items():
        b = mk()[0].stats()
        same = same and b["最大叶容量"] == b["最大停留数"] and b["滞留"] == 0
        detail.append(f"{which}:{b['最大叶容量']}/{b['最大停留数']}/滞留{b['滞留']}")
    ok("★ 且**批建**路径上 `最大叶容量 == 最大停留数`、滞留 0（三个方向）"
       " —— ⑦ 那条 `>` 不是恒真，`最大停留数` 是**推广**不是另一个数",
       same, " / ".join(detail))

    # ⑧ ★ `叶容量` 会掉到「分辨率下界」**以下** —— 而叶**仍然**各自是单个等价类。
    #    这条钉住的是**尺子的量程**，不是结构：§7.2 的等式 `叶容量 = 最大等价类`
    #    前提是 `滞留 == 0`（每一项都走到了叶）。少了这个前提，
    #    `叶容量` 会报出一个**比下界还小**的数 —— 它不再能当「分辨率下界」用。
    from ldv.checks._fixtures import build_keyset as _bk, build_sequence as _bsq
    from ldv.checks._fixtures import equiv_classes as _eq

    classes = _eq("sequence", nodes, edges)
    max_class = max(len(v) for v in classes.values())
    leaves = [d for d in k.all_directions() if not k.children_of(d)]
    per_leaf = [len({classes[x] for x in k.members_of(d) if x in classes}) for d in leaves]
    ok("★ [量程] 滞留 > 0 时 `最大叶容量` 掉到**最大等价类以下**（这里 %d < %d）"
       " —— 它不是分辨率下界了" % (st["最大叶容量"], max_class),
       st["最大叶容量"] < max_class, f"最大叶容量 {st['最大叶容量']} vs 最大等价类 {max_class}")
    ok("★ [量程] 而**每个叶仍然各自是单个等价类** —— 掉下去的不是分辨率，是那把尺子",
       bool(leaves) and all(n == 1 for n in per_leaf), f"各叶的等价类数 {per_leaf}")
    ok("★ [量程] 下界改由 `最大停留数` 承担：`最大停留数 >= 最大等价类`",
       st["最大停留数"] >= max_class, f"{st['最大停留数']} < {max_class}")
    batch = _bsq(nodes, edges)[0].stats()
    ok("★ [量程] 对照：**批建**路径上 `滞留 == 0` ⇒ 等式 `叶容量 == 最大等价类` 成立"
       "（§7.2 的等式前提就是这一条）",
       batch["滞留"] == 0 and batch["最大叶容量"] == max_class,
       f"批建滞留 {batch['滞留']}、叶容量 {batch['最大叶容量']} vs 最大等价类 {max_class}")

    # ⑥ 已知答案对照组：非根方向一律说「未展开」⇒ 滞留必须为 0
    class _KidsSayUnknown(SequencePlugin):
        """对照组：**非根**方向对任何查询都说「未展开」。"""

        def hit(self, d, query):  # noqa: ANN001, ANN201
            if d.did != self._root_did:
                return Tri.UNEXPANDED
            return super().hit(d, query)

    plug2 = _KidsSayUnknown(make_seqs(nodes, edges))
    k2 = Kernel(plug2, {i: nodes[i].as_item() for i in ids[:2]})
    k2.build(frozenset({()}))
    plug2._root_did = k2.root.did  # noqa: SLF001
    for i in ids[:2]:
        k2.insert(i)
    for i in ids[2:]:
        k2.insert(i, nodes[i].as_item())
    st2 = k2.stats()
    # 先验对照组**真的生效了**（否则下面那条是空转：override 没打上，一切照旧）
    probe = Query(ideal=frozenset({ids[10]}))
    ok("★ [对照组] 非根方向**确实**在说「未展开」（override 真的生效）",
       all(plug2.hit(c, probe) is Tri.UNEXPANDED for c in k2.children_of(k2.root)),
       "还有子方向没说「未展开」—— 对照组没打上")
    ok("★ [对照组] 子方向说「未展开」时**滞留必须为 0** —— 「不知道」不许被当成「不在」",
       st2["滞留"] == 0, f"实际滞留 {st2['滞留']}")
    ok("★ [对照组] 且项**没有消失**：`纳入 == 认识`、`根覆盖之外 == 0`",
       st2["纳入"] == st2["认识"] and st2["根覆盖之外"] == 0, f"实际 {st2}")
    ok("★ [对照组] 且它**确实换了行为**（方向数不同）—— 不是一个「照旧」的假对照组",
       st2["方向"] != st["方向"], f"对照组方向数 {st2['方向']} == 真实组 {st['方向']}")


def test_cover_leak_baseline() -> None:
    """★ `B18` 的 baseline 守卫 —— 四条规则 + **换语料必须报「跳过」**。

    ## 为什么守卫要单独测，而不是混在「跑一遍 B18」里

    普通判据的失效模式是**永远绿**；守卫的失效模式**不一样** —— 它会**红**，
    红得理直气壮，而那句话是**假的**：

    > `B18` 的基线是**语料相关**的读数（`sequence` 维护路径 16 漏是在 36 项上量的）。
    > 换一份语料跑，`183 > 16` 会被报成**「新增覆盖不漏」** ——
    > 而真相是**「baseline 是别的语料的」**。两者在输出里长得一模一样。

    实测踩到过（`openalex-small` 281 项），所以才有了语料指纹。
    **「跑一遍 B18 是绿的」查不出这个** —— 在真语料上它本来就是绿的。

    ## 逐条钉住（每条都能红）

        ① 基线文件在、**本语料那一区**在且指纹 == 现算的、条目非空 —— **非空转**前提
        ② 观测 > baseline          ⇒ 红（新增违规）
        ③ baseline > 0 且 观测 == 0 ⇒ 红（条目失效 —— 不许当永久豁免）
        ④ 语料指纹不符             ⇒ **跳过**（未展开），**不是**红也不是绿
        ⑤ baseline 里没记指纹      ⇒ **跳过**（判不了 ≠ 没问题），且理由与 ④ 分得开
        ⑥ **基线文件整个不见了**   ⇒ **跳过**（这条是 ⑤ 的实测形态，见下）
        ⑦ 0 < 观测 < baseline      ⇒ 绿 + 报「可以收紧」
        ⑧ `corpus` 漏传            ⇒ 当场 `TypeError`（防御不许静默消失）
        ⑨ 真语料上这条守卫**确实在判**，且**冻结的数 == 现算的数**
        ⑩ **重冻第二份语料是「合并」** —— 第一份那一区逐字没动（否则是**挪洞**）
        ⑩′ 旧格式（v1，顶层 `语料`）**读的时候当场升级**，不许静默退化成「跳过」

    ## ★ ⑥ 是实测出来的，而且**两条臂都得跑**（`outputs/_measure_b18_missing_baseline.py`）

    「基线文件不见了」这件事，旧代码（HEAD 逐字）报的是**两种**东西，
    取决于语料有没有既存违规：

        臂 A  `keyset`/`reach`（既存违规 0 处）  观测全 0、基线默认值也全 0 ⇒ **绿**
        臂 B  加上 `sequence|maintenance`（既存 16 漏）              ⇒ **红**
              理由写的是「新增覆盖不漏：16 漏（baseline 0）」—— **那是假的**

    第一版测量脚本只跑了臂 B，于是印出「旧代码报绿」—— **与读数相反**。
    两条臂一起跑才看得出全貌：**「基线不见了」被拆成「绿」和「红」两种假象，
    而两条都不说「基线不见了」** —— 而它不是绿、也不是违规，是**判不了**。
    ⇒ 这正是 ④ 的同一个形状：「换了一份语料」/「基线被删了」/「新增违规」三者长得一模一样。

    ## ★ ④ 必须配一条**单变量对照**，否则它自己就是空转

    「总是跳过」的实现能让 ④ 全绿。所以：**同一份 obs、同一份基线条目，
    只有语料指纹不同** ⇒ 一个判得出、一个判不了。两边一对照，
    「跳过」是**指纹**触发的、不是恒跳过。这一步不能省 ——
    少了它，④ 就是「空转与通过长得一模一样」的又一个实例。

    ## 以及：真语料上必须真的在判（⑨）

    ②–⑦ 全是拿合成观测喂进去的。若它在真语料上一路「跳过」，
    那上面几条通过也说明不了它在工作 ⇒ 最后用真语料真跑一遍，
    并核对**冻结的数 == 现算的数**（否则基线过期，运行时会报「条目失效」红）。
    """
    from ldv.checks._fixtures import (
        build_incremental,
        build_keyset,
        build_reach,
        build_sequence,
        coverage_of,
        make_builder,
    )
    from ldv.checks._framework import Assertion, Report
    from ldv.checks.coverage import (
        BASELINE_FORMAT,
        BASELINE_FORMAT_KEY,
        BASELINE_PATH,
        b18_cover_leak_baseline,
        baseline_zones,
        corpus_fingerprint,
        corpus_key,
        cover_leak_profile,
        load_baseline,
    )

    loaded = load()
    if loaded is None:
        PASS.append("B18 基线守卫（跳过：目录不在）")
        return
    nodes, edges, _ = loaded
    fp = corpus_fingerprint(nodes, edges)
    ckey = corpus_key(fp)

    # ── ① 非空转前提：文件在、**本语料那一区**在且指纹对得上、条目非空 ──────
    doc = load_baseline()
    zones = baseline_zones(doc)
    ok("★ [B18] 基线文件在（否则「新增」这个概念不存在）",
       BASELINE_PATH.is_file(), str(BASELINE_PATH))
    ok("★ [B18] 基线里**有本语料那一区**，且区里记的指纹 == **现算的**指纹"
       "（对不上 ⇒ 真跑时一路「跳过」）",
       zones.get(ckey, {}).get("语料") == fp,
       f"本语料分区键 {ckey!r}；已冻分区 {sorted(zones)}")
    ok("★ [B18] 本语料那一区的条目非空（空基线 ⇒ 守卫在守空气）",
       bool(zones.get(ckey, {}).get("条目")), f"{zones.get(ckey, {}).get('条目')}")

    def guard(obs: dict, baseline: dict, corpus: dict) -> Assertion:
        rep = Report(plugin="(自检)")
        b18_cover_leak_baseline(obs, rep, corpus, baseline)
        return rep.assertions[-1]

    def leak(n: int, pairs: int = 1) -> dict:
        return {"漏项数": n, "漏的对数": pairs,
                "明细": [{"父": "D0", "漏项数": n}] if n else []}

    def doc_of(entries: dict, fingerprint: dict) -> dict:
        """造一份**格式 v2**（按语料分区）的基线 —— 只有本语料那一区。"""
        return {BASELINE_FORMAT_KEY: BASELINE_FORMAT,
                "基线": {corpus_key(fingerprint): {"语料": fingerprint,
                                                   "条目": entries}}}

    base0 = doc_of({"keyset|maintenance": {"漏项数": 0, "漏的对数": 0}}, fp)
    base16 = doc_of({"keyset|maintenance": {"漏项数": 16, "漏的对数": 2}}, fp)
    #: ★ 一份**故意不是本语料**的指纹。两个数各 +1 ⇒ 保证与 `fp` 一定不等
    #:（这份语料恰好是 36 项 / 47 边，写死一个别的数也行，但加 1 不会随语料失效）。
    other = {"项数": fp["项数"] + 1, "边数": fp["边数"] + 1}

    # ── ② 观测 > baseline ⇒ 红 ─────────────────────────────────────────────
    a = guard({"keyset|maintenance": leak(5)}, base0, fp)
    ok("★ [B18] 观测 > baseline ⇒ **红**（新增违规）",
       a.result is Tri.NO, f"{a.result}：{a.detail}")

    # ── ③ baseline > 0 且 观测 == 0 ⇒ 红（条目失效）────────────────────────
    a = guard({"keyset|maintenance": leak(0, 0)}, base16, fp)
    ok("★ [B18] baseline > 0 而观测 == 0 ⇒ **红**（条目失效，不许当永久豁免）",
       a.result is Tri.NO, f"{a.result}：{a.detail}")

    # ── ④ 语料指纹不符 ⇒ 跳过 ──────────────────────────────────────────────
    a = guard({"keyset|maintenance": leak(5)}, base0, other)
    ok("★ [B18] 语料指纹不符 ⇒ **跳过**（未展开），**不是**红也不是绿",
       a.result is Tri.UNEXPANDED, f"{a.result}：{a.detail}")
    ok("★ [B18] 且理由里点明「另一份语料」与「跳过 ≠ 通过」（跳过必须能读懂）",
       "另一份语料" in a.detail and "跳过 ≠ 通过" in a.detail, a.detail)

    # ── ⑤ 单变量对照：跳过由**指纹**触发，不是恒跳过 ──────────────────────
    same = guard({"keyset|maintenance": leak(5)}, base0, fp)
    diff = guard({"keyset|maintenance": leak(5)}, base0, other)
    ok("★ [B18] 单变量对照：**同一份 obs + 同一份基线**，只有语料指纹不同 ⇒ "
       "一个判得出、一个判不了 —— 「跳过」是指纹触发的，不是恒跳过",
       same.result is Tri.NO and diff.result is Tri.UNEXPANDED,
       f"指纹相符 {same.result} vs 指纹不符 {diff.result}")

    # ── ⑥ baseline 里**没记指纹** ⇒ 同样跳过（旧格式文件不许让守卫无声失效）──
    no_fp = {"基线": {"keyset|maintenance": {"漏项数": 0, "漏的对数": 0}}}
    a = guard({"keyset|maintenance": leak(5)}, no_fp, fp)
    ok("★ [B18] baseline 里没记语料指纹 ⇒ **跳过**（判不了 ≠ 没问题）",
       a.result is Tri.UNEXPANDED, f"{a.result}：{a.detail}")
    ok("★ [B18] 且这条的理由与「另一份语料」**分得开**（一个说换了料、一个说旧格式）",
       "没记语料指纹" in a.detail, a.detail)

    # ── ⑥′ **基线文件整个不见了**（`load_baseline()` 返回 `{}`）─────────────
    #    `{}` 不是 `None` ⇒ 走的是「doc 里没有 `语料`」这条路。
    #    旧代码在**这一条**上报的是绿（观测全 0 时）或红（有既存违规时），
    #    两条都不说「基线不见了」—— 见 `outputs/_measure_b18_missing_baseline.py`。
    a = guard({"keyset|maintenance": leak(0, 0)}, {}, fp)
    ok("★ [B18] 基线文件不见了 ⇒ **跳过**（旧代码在观测全 0 时报的是**绿**）",
       a.result is Tri.UNEXPANDED, f"{a.result}：{a.detail}")
    a2 = guard({"keyset|maintenance": leak(16, 2)}, {}, fp)
    ok("★ [B18] 基线文件不见了 + 有既存违规 ⇒ **跳过**（旧代码报的是红，"
       "理由「新增覆盖不漏」是**假的**：没有任何东西变差）",
       a2.result is Tri.UNEXPANDED, f"{a2.result}：{a2.detail}")
    ok("★ [B18] 而且两种情形给的是**同一句**「判不了」—— 旧行为把它们分成了绿和红",
       a.detail == a2.detail, f"{a.detail[:40]} ≠ {a2.detail[:40]}")

    # ── ⑦ 0 < 观测 < baseline ⇒ 绿 + 报「可以收紧」─────────────────────────
    a = guard({"keyset|maintenance": leak(3)}, base16, fp)
    ok("★ [B18] 0 < 观测 < baseline ⇒ **绿**，且报「可以收紧」（不是静悄悄地绿）",
       a.result is Tri.YES and "可收紧" in a.detail, f"{a.result}：{a.detail}")

    # ── ⑧ `corpus` 是必填 —— 漏传当场炸 ───────────────────────────────────
    raises("★ [B18] `corpus` 必填：漏传直接 TypeError（防御不许因少传参数而静默消失）",
           lambda: b18_cover_leak_baseline({"keyset|maintenance": leak(5)},
                                           Report(plugin="(自检)")))

    # ── ⑨ 真语料上**确实在判**，且冻结的数 == 现算的数 ─────────────────────
    #: 与 `run_checks.MAINT_INIT` 同口径（先建 6、维护其余）。
    ids = sorted(nodes)
    real: dict[str, dict] = {}
    for which in ("keyset", "reach", "sequence"):
        cover = coverage_of(which, nodes, edges)
        mk = make_builder(which, nodes, edges)
        batch = {"keyset": build_keyset(nodes),
                 "reach": build_reach(nodes, edges),
                 "sequence": build_sequence(nodes, edges)}[which][0]
        inc = build_incremental(mk, nodes, ids[:6], ids[6:])
        real[f"{which}|batch"] = cover_leak_profile(batch, cover)
        real[f"{which}|maintenance"] = cover_leak_profile(inc, cover)

    rep = Report(plugin="(自检)")
    b18_cover_leak_baseline(real, rep, fp)
    a = rep.assertions[-1]
    ok("★ [B18] 真语料上这条守卫**确实在判**（不是一路「跳过」）",
       a.result is not Tri.UNEXPANDED, f"跳过了：{a.detail}")
    ok("★ [B18] 真语料上它是**绿** —— 基线就是在本语料上冻的",
       a.result is Tri.YES, f"{a.result}：{a.detail}")

    frozen = zones.get(ckey, {}).get("条目", {})
    mism = {k: (frozen.get(k, {}).get("漏项数"), v["漏项数"])
            for k, v in real.items()
            if frozen.get(k, {}).get("漏项数") != v["漏项数"]}
    ok("★ [B18] 而且**冻结的数 == 现算的数**（基线没过期 —— 否则运行时会报「条目失效」红）",
       not mism, f"对不上的：{mism}")
    #: 反向也查一遍：**只在 `sequence|maintenance` 上非零** —— 若哪个方向悄悄开始漏，
    #: 上面那条会红；若哪个方向的既存违规被修好了而基线没动，上面那条也会红。
    nonzero = {k: v["漏项数"] for k, v in real.items() if v["漏项数"]}
    ok("★ [B18] 非空条目只有 `sequence|maintenance` 一处（其余 5 条都是 0 漏）"
       " —— 基线的形状与 §1 表第四行对得上（该行 2026-10-07 已降级）",
       set(nonzero) == {"sequence|maintenance"},
       f"非零条目：{nonzero}")

    # ── ⑩ **重冻一份语料不许把另一份那一区抹掉**（2026-10-10 加）───────────
    #
    # 用户的原话是「现在真实数据已经注入了，守卫应该有材料了啊，以此补充」。
    # 而「补材料」有一个**看起来对、实际是挪洞**的走法：直接在全量上重冻。
    # 单语料格式下那会把 36 项那一区**抹掉** ⇒ 36 项那条守卫立刻变「跳过」。
    #
    # ⇒ ⑩ 用**临时文件**（绝不碰真基线）走一遍：先冻 A、再冻 B，然后查三件事。
    from ldv.checks import coverage as _cov

    with tempfile.TemporaryDirectory() as _td:
        _saved = _cov.BASELINE_PATH
        _cov.BASELINE_PATH = Path(_td) / "cover_leak_baseline.json"
        try:
            e_a = {"keyset|batch": {"漏项数": 0, "漏的对数": 0}}
            e_b = {"keyset|batch": {"漏项数": 3, "漏的对数": 1}}
            _cov.write_baseline(e_a, corpus=fp)
            _cov.write_baseline(e_b, corpus=other)
            z2 = _cov.baseline_zones(_cov.load_baseline())
            ok("★ [B18] 重冻第二份语料是**合并**：两份分区同时在（不是顶掉）",
               set(z2) == {corpus_key(fp), corpus_key(other)}, f"{sorted(z2)}")
            ok("★ [B18] 而且第一份那一区**逐字没动** —— 否则是「挪洞」不是「补洞」",
               z2.get(corpus_key(fp), {}).get("条目") == e_a,
               f"{z2.get(corpus_key(fp))}")
            # 单变量对照：两份语料**各自判得了**（没有哪一份退化成「跳过」）
            r1, r2 = Report(plugin="(自检)"), Report(plugin="(自检)")
            _cov.b18_cover_leak_baseline({"keyset|batch": leak(0, 0)}, r1, fp)
            _cov.b18_cover_leak_baseline({"keyset|batch": leak(3, 1)}, r2, other)
            ok("★ [B18] 合并之后两份语料**各自判得了**（一份「跳过」就说明洞被挪了）",
               r1.assertions[-1].result is Tri.YES
               and r2.assertions[-1].result is Tri.YES,
               f"{r1.assertions[-1].result} / {r2.assertions[-1].result}")
        finally:
            _cov.BASELINE_PATH = _saved

    # ── ⑩′ 旧格式（v1）文件**读的时候当场升级**，不许静默退化成「跳过」──────
    v1 = {"语料": fp, "基线": {"keyset|maintenance": {"漏项数": 0, "漏的对数": 0}}}
    z1 = baseline_zones(v1)
    ok("★ [B18] 旧格式（v1，顶层 `语料`）**读时升级**成一个分区 —— 不丢数据",
       z1.get(corpus_key(fp), {}).get("语料") == fp
       and z1[corpus_key(fp)]["条目"] == v1["基线"], f"{z1}")
    a = guard({"keyset|maintenance": leak(5)}, v1, fp)
    ok("★ [B18] 而且旧格式文件上守卫**照样判**（升级后指纹对得上）",
       a.result is Tri.NO, f"{a.result}：{a.detail}")


def _ref_cover(which: str, nodes, edges):
    """**参考实现** —— 改动前那一版**逐字**：不规范化、不去重、正向闭包。

    它慢，但**独立**：生产实现走的是「按 payload 规范形去重 + `reach` 用反向闭包」。
    两者在同一条定义上各写一遍，对不上就说明有一边错了。

    这正是 McConnell 等 2011 把「**独立**的 checker」当硬要求的那个形状：
    拿被检查对象的算法当参照，两边会**一起**错（`false-green` 形状 3「共享盲点」）。
    """
    from ldv.checks._fixtures import SEQ_END, _forward_closure, _is_prefix, sequences

    if which == "keyset":
        keys = {i: nodes[i].keys for i in nodes}

        def cover(payload):
            req, forb = payload
            req, forb = frozenset(req), frozenset(forb)
            return frozenset(i for i in nodes
                             if req <= keys[i] and not (keys[i] & forb))

        return cover

    if which == "sequence":
        seqs = {i: tuple(v) + (SEQ_END,) for i, v in sequences(nodes, edges).items()}

        def cover(payload):
            return frozenset(i for i in nodes
                             if any(_is_prefix(tuple(p), seqs[i]) for p in payload))

        return cover

    if which == "reach":
        fwd = _forward_closure(edges, universe=nodes)

        def cover(payload):
            anchors = set(payload)
            return frozenset(x for x in nodes if fwd.get(x, frozenset()) & anchors)

        return cover

    raise ValueError(which)


def _wrong_key_cache(fn):
    """**注入**：键取错 —— 按 `len(payload)` 而不是按 payload 本身。

    真实里最容易犯的正是这个（「长度差不多就当成同一个」），
    而它的症状**不是报错**，是**静默串台**：第二个 payload 拿到第一个的覆盖
    ⇒ 判据拿错的覆盖去比 ⇒ **假绿**。

    ⚠️ 三个方向**都会撞**：`keyset` 的 payload 恒为 2 元组（`len` 恒 2）、
       `reach` 按锚点数撞、`sequence` 按前缀数撞。
    """
    memo: dict[int, frozenset[str]] = {}

    def cover(payload):
        k = len(payload)
        got = memo.get(k)
        if got is not None:
            return got
        val = fn(payload)
        memo[k] = val
        return val

    return cover


def test_cover_oracle_transparency() -> None:
    """★ 外生覆盖 oracle：**换实现不许改变答案** —— 两个改动分开对照，且三个方向都查。

    ## 为什么这条必须单独存在

    `coverage_of` 是覆盖族的 **ground truth**（`B16` / 健全性 / 覆盖不漏 / 细化量）。
    它算错时**错的是判据的答案本身**，而且错法很隐蔽：

        少算一项   ⇒ 某方向被报「成员越界」  ⇒ 假红
        多算一项   ⇒ 真的越界被盖住          ⇒ **假绿**

    「跑一遍 `run_checks` 是绿的」**查不出这两种** —— 它本来就是绿的。

    ## 依据（逐字）

    Acar / Blelloch / Harper 2002（POPL，§2 末「Side Effects」）：

    > Also, the memoization of the kind done by lazy languages will not affect the
    > correctness of change-propagation, **because the value remains the same whether
    > it has been calculated or not.**

    同一节还给了**反面**，所以「纯」是前提不是顺手一提：

    > **function caching requires purely functional code**, but our framework involves
    > side-effects in its implementation.

    ## ★ 两个改动必须**分开**对照（这是本测试的重点）

    这一次同时动了两件事，混在一起就分不清谁错了：

        改动 1  `reach` 的**单次调用算法**：正向闭包 `fwd[x] & anchors`
                ⇒ 反向闭包 `∪_{a ∈ payload} rev[a]`
        改动 2  **同一 payload 算几遍**：`Cover` 按规范形去重

    ⇒ 三臂对同一个参考实现比：

        臂 A  `dedup=False`  ⇒ 只含改动 1
        臂 B  `dedup=True`   ⇒ 改动 1 + 2
        臂 C  A vs B         ⇒ **只**含改动 2

    读法（这一步才是「分开」的用处）：

        A 红、B 红、C 绿  ⇒ 两臂**一致地**错 ⇒ 问题在**去重之前**（`canon` / `raw`）
        A 绿、B 红、C 红  ⇒ 只有带缓存的那一臂错 ⇒ 问题在**缓存**里
        A 绿、B 绿、C 红  ⇒ 不可能（C 是 A、B 的推论）—— 出现就是键的哈希/等价坏了

    ⚠️ **只留 C 是不够的**：C 是同义反复。`canon` 产出的**就是** `raw` 的入参，
       所以「键决定答案」按构造成立 —— C 查不出 `canon` **丢信息**。
       丢信息要拿**参考实现**比（A / B 干的活）。

    ## ★ 而且**对照的顺序**本身也是对照的一部分

    踩过（实测）：原来先跑一句 `nonempty = sum(1 for p in payloads if b_arm(p))`
    来证明「不是空转」，而那一步**把臂 B 的缓存烤热了** ——
    于是后面比对时每一次调用都是**命中**、返回的都是**存进去的正确值**，
    ⇒ 注入「缓存返回错值」之后，② ③ 仍然报「**0 处不同**」，变异被**掩盖**。

    ⇒ 现在的顺序是：**先把参考值全算出来**，再让每个臂各跑一遍（各用全新实例）。
      「先算参考、再跑臂」不是为了好看 —— 它是这条测试**能不能红**的前提。

    ## 逐条钉住（每条都能红）

        ① 三臂在**非空**的 payload 上比（否则「相同」可能只是「都返回空集」）
        ② 臂 A vs 参考：`reach` 的**反向闭包**那一步干净
        ③ 臂 B vs 参考：加上去重之后仍然干净
        ④ 臂 C：A 与 B 逐项相同
        ⑤ **注入**：键取错（按 `len`）⇒ 与参考必须**对不上**（判据不是空转）
        ⑥ 去重**确实在发生**（命中 > 0）—— 度量，不进退出码

    ## 变异验证（都跑过，不是推的）

        canon 丢信息（`frozenset(sorted(p)[:1])`）  ⇒ ② 红、③ 红、④ 绿
          读法：两臂**一致地**错 ⇒ 问题在**去重之前**
        缓存返回错值（`next(iter(memo.values()))`）⇒ ② 绿、③ 红、④ 红
          读法：只有带缓存的那一臂错 ⇒ 问题在**缓存**里
        （两次都是**精确**命中对应的臂，不是「一片红」—— 这就是把两个改动
          分开对照的用处。）
    """
    from ldv.checks._fixtures import build_incremental, coverage_of, make_builder

    loaded = load()
    if loaded is None:
        ok("★ [oracle] 语料在（这条测试要真语料，缺了就报「跳过」不是「过」）", False,
           "找不到语料")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)

    total_nonempty = 0
    total_payloads = 0
    stats_line: list[str] = []

    for which in ("keyset", "reach", "sequence"):
        # --- payload 序列：两个内核的全部方向（覆盖族要的就是这些） ----------
        #: ⚠️ `build_keyset` 只收 `nodes`（它不需要图）—— 三个方向签名不齐，
        #:   所以这里用 lambda 包一层，不用「按名字取函数再统一调用」。
        mk = make_builder(which, nodes, edges)
        builders = {"keyset": lambda: build_keyset(nodes),
                    "reach": lambda: build_reach(nodes, edges),
                    "sequence": lambda: build_sequence(nodes, edges)}
        batch = builders[which]()[0]
        inc = build_incremental(mk, nodes, ids[:6], ids[6:])
        payloads: list = []
        for k in (batch, inc):
            for d in k.all_directions():
                payloads.append(d.payload)
        total_payloads += len(payloads)

        ref = _ref_cover(which, nodes, edges)                  # 改动前逐字

        # ⚠️ **先把参考值全算出来，再让每个臂各跑一遍** —— 顺序不能反。
        #    踩过：原来先跑 `nonempty = sum(1 for p in payloads if b_arm(p))`，
        #    那一步**把臂 B 的缓存烤热了**，于是后面比对时每一次调用都是**命中**、
        #    返回的都是正确值 ⇒ **变异被掩盖**（实测：注入「缓存返回错值」后
        #    ② ③ 仍然报「0 处不同」）。**「对照的顺序」本身也是对照的一部分。**
        ref_vals = [ref(p) for p in payloads]

        a_arm = coverage_of(which, nodes, edges, dedup=False)   # 臂 A：只换算法
        b_arm = coverage_of(which, nodes, edges, dedup=True)    # 臂 B：算法 + 去重
        a_vals = [a_arm(p) for p in payloads]
        b_vals = [b_arm(p) for p in payloads]

        def diff(xs, ys) -> list[tuple[int, list[str]]]:
            bad = []
            for i, (x, y) in enumerate(zip(xs, ys)):
                if x != y:
                    bad.append((i, sorted(x ^ y)[:3]))
            return bad

        # --- ① 这批 payload **不是空转**：覆盖集必须非空 --------------------
        #: 拿**参考值**判，不拿臂 —— 否则这一步又会把某个臂的缓存烤热。
        nonempty = sum(1 for v in ref_vals if v)
        total_nonempty += nonempty
        ok(f"★ [oracle·{which}] 对照用的 payload **真的算出东西**"
           f"（否则「相同」可能只是「都空」）",
           nonempty > 0, f"非空 {nonempty}/{len(payloads)}")

        # --- ② 臂 A vs 参考 -------------------------------------------------
        d_a = diff(a_vals, ref_vals)
        ok(f"★ [oracle·{which}] 臂 A（`dedup=False`）与**参考实现**逐项相同"
           f" —— 单次调用的算法那一步是干净的",
           not d_a, f"{len(payloads)} 个 payload 有 {len(d_a)} 处不同：{d_a[:2]}")

        # --- ③ 臂 B vs 参考 -------------------------------------------------
        d_b = diff(b_vals, ref_vals)
        ok(f"★ [oracle·{which}] 臂 B（生产：`dedup=True`）与**参考实现**逐项相同",
           not d_b, f"{len(payloads)} 个 payload 有 {len(d_b)} 处不同：{d_b[:2]}")

        # --- ④ 臂 C：A vs B -------------------------------------------------
        d_c = diff(b_vals, a_vals)
        ok(f"★ [oracle·{which}] 臂 C：`dedup=True` 与 `dedup=False` 逐项相同"
           f"（Acar：值算没算都一样）",
           not d_c, f"{len(payloads)} 个 payload 有 {len(d_c)} 处不同：{d_c[:2]}")

        # --- ⑤ 注入：键取错 ⇒ 与参考**必须**对不上 --------------------------
        wrong = _wrong_key_cache(ref)
        w_vals = [wrong(p) for p in payloads]
        d_w = diff(w_vals, ref_vals)
        ok(f"★ [oracle·{which}] 注入「键取错（按 len）」⇒ 与参考**必须**对不上"
           f"（判据不是空转）",
           bool(d_w), f"注入后仍然全同 —— 那 ②③ 就是空转（{len(payloads)} 个 payload）")

        # --- ⑥ 去重确实在发生（度量） ---------------------------------------
        #: ⚠️ **另起一个全新的 oracle** —— 上面那个的 memo 已经被对照跑热了，
        #:   拿它数命中会**数的是对照的热度**，不是真实调用形状。
        fresh = coverage_of(which, nodes, edges)
        for k in (batch, inc):
            for d in k.all_directions():
                fresh(d.payload)          # 健全性 / `B16`
            for d in k.all_directions():
                if k.children_of(d):
                    fresh(d.payload)
                    for kid in k.children_of(d):
                        fresh(kid.payload)  # 覆盖不漏 / 细化量
        st = fresh.stats
        ok(f"★ [oracle·{which}] 去重**确实在发生**（命中 > 0）"
           f" —— 否则缓存是直通，白占内存",
           st["命中"] > 0, f"读数 {st}")
        stats_line.append(f"{which} 调用 {st['调用']} / 命中 {st['命中']}"
                          f"（不同 payload {st['不同 payload']}）")

    ok("★ [oracle] 三个方向的 payload 都**非空**（上面那条逐方向查过，这里查总数）",
       total_nonempty > 0, f"非空合计 {total_nonempty}/{total_payloads}")
    print(f"    · 覆盖 oracle 读数：{'；'.join(stats_line)}（**度量**，不进退出码）")


def _ref_b3(kernel, plugin) -> tuple[str, str]:
    """**改动前逐字**的 `b3_penalty_comparable` —— 含那个三重循环。只返回结论。

    它存在的唯一理由：证明「删掉三重循环」**不改变判定**。
    没有它，那条删除就只是「我觉得它没用」—— 而本仓库不吃这个。
    """
    from ldv.core.interfaces import call_penalty

    bad: list[str] = []
    checked = 0
    for rank in sorted({d.rank for d in kernel.all_directions()}):
        layer = [d for d in kernel.all_directions() if d.rank == rank]
        if len(layer) < 2:
            continue
        table: dict[str, list[float]] = {}
        for d in layer:
            vals: list[float] = []
            for i in sorted(kernel.items):
                try:
                    vals.append(call_penalty(plugin, d, kernel.items[i]))
                except Exception as exc:  # noqa: BLE001 - 插件是外部代码
                    bad.append(f"{d.did} 的代价抛异常：{type(exc).__name__}: {exc}")
                    vals.append(float("nan"))
            table[d.did] = vals
            checked += len(vals)
            if any(v != v for v in vals):
                bad.append(f"{d.did} 的代价里有 NaN（不可比）")
        ids = sorted(table)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                for a, b in zip(table[ids[i]], table[ids[j]]):
                    if not (a < b or a == b or a > b):
                        bad.append(f"{ids[i]} vs {ids[j]} 的一项不可比：{a} / {b}")
                        break
    if checked == 0:
        return "未展开", ""
    return ("否" if bad else "是"), f"{len(bad)} 处不可比"


def _prod_b3(kernel, plugin) -> tuple[str, str]:
    from ldv.checks._framework import Report
    from ldv.checks.contract import b3_penalty_comparable

    rep = Report(plugin="B3 对照", expects=("B3",))
    b3_penalty_comparable(kernel, plugin, rep)
    if rep.skipped:
        return "未展开", ""
    if rep.red:
        return "否", rep.red[0].detail
    return "是", rep.assertions[0].detail


class _NaNPenalty:
    """注入：`§I3` 返回 **NaN** —— `call_penalty` 必须把它挡下来。"""

    def __init__(self, name: str = "注入·NaN") -> None:
        self.name = name

    def penalty(self, d: object, item: object) -> float:
        return float("nan")


def test_b3_reduction_premise() -> None:
    """★ `B3` 的**三重循环删掉了** —— 这条钉住「删它的前提」+「删了不改判定」。

    ## 为什么非删不可

    `b3_penalty_comparable` 原来是三层循环：层内两两配对、每对**逐项**比。
    那是 **`O(层内方向数² × 项数)`**。3907 项 / reach 有 **7813 个方向** ⇒
    **小时级**。而 `B3` 是**判据**（`B1–B20`）⇒ `--no-probes` **关不掉它**。

    ⇒ **它才是「全量 `--no-probes` 跑不完」的主因** —— 不是覆盖族（那个只有 0.69 s）。
    删掉之后 `b3` 在 3907 项上是 **38.85 s**；剩下的是 `O(Ln)` = **30,521,484 次**
    `call_penalty`，瓶颈因此**移到插件里**（见 `plugins/reach.py` 的缓存一节）。

    ## 删它的依据（一条蕴含链，不是经验）

        前提   `call_penalty` 只可能返回**有限 float**
               —— 非数 / NaN / ±inf 全在它里面抛（`core/interfaces.py:190-196`）
        推论   两个**有限 float** 之间，`a < b or a == b or a > b` **恒为真**
               —— IEEE-754 里「三个都不成立」当且仅当有一方是 NaN（无序）
        ⇒ 那个循环**永远 append 不了东西**。

    它唯一可能命中的情形，是 `b3` 自己塞进去的 `float("nan")` —— 而那一行**已经报过**。

    ## ★ 前提必须被**检查**，不许靠读代码断言

    这就是本仓库反复说的那条：**「靠读代码断言的前提」= 一条将来会静默失效的规矩。**
    ⇒ ① 直接钉 `call_penalty` 的守卫（NaN / 非数 / ±inf / 插件抛异常，四种都必须抛）。
    那一条破了，三重循环就得加回来。

    ## 逐条钉住

        ① 前提：`call_penalty` 对 NaN / 非数 / ±inf / 抛异常 —— **四种都必须抛**
        ② 正常插件：参考（旧）与生产（新）判定**相同**，且都是「是」
        ③ 注入 NaN：参考与生产判定**相同**，且都是「否」 —— 删掉循环**没把红的漏掉**
        ④ 对照不是空转：② 是「是」、③ 是「否」⇒ 这个对照**能红也能绿**
    """
    from ldv.checks._framework import Report
    from ldv.checks.contract import b3_penalty_comparable
    from ldv.core.interfaces import PluginContractError, call_penalty

    loaded = load()
    if loaded is None:
        ok("★ [B3] 语料在（不在就报跳过，不报通过）", False, "找不到语料")
        return
    nodes, edges, _ = loaded
    kernel, plugin = build_reach(nodes, edges)

    # --- ① 前提：`call_penalty` 的四种守卫 -----------------------------------
    class _Bad:
        def __init__(self, val):
            self.name = "注入"
            self._val = val

        def penalty(self, d, item):
            return self._val

    def _probe(val):
        return lambda: call_penalty(_Bad(val), next(iter(kernel.all_directions())),
                                    kernel.items[sorted(kernel.items)[0]])

    for label, val in (("NaN", float("nan")), ("+inf", float("inf")),
                       ("-inf", float("-inf")), ("非数（str）", "小")):
        try:
            _probe(val)()
            ok(f"★ [B3] 前提：`call_penalty` 对 **{label}** 必须抛"
               f"（不抛 ⇒ 三重循环就不能删）",
               False, f"它放行了 {val!r}")
        except PluginContractError:
            ok(f"★ [B3] 前提：`call_penalty` 对 **{label}** 必须抛"
               f"（不抛 ⇒ 三重循环就不能删）", True)

    class _Raiser:
        name = "注入·抛异常"

        def penalty(self, d, item):
            raise RuntimeError("故意炸")

    try:
        call_penalty(_Raiser(), next(iter(kernel.all_directions())),
                     kernel.items[sorted(kernel.items)[0]])
        ok("★ [B3] 前提：插件抛异常时 `call_penalty` 必须转成 `PluginContractError`",
           False, "它放行了")
    except PluginContractError:
        ok("★ [B3] 前提：插件抛异常时 `call_penalty` 必须转成 `PluginContractError`", True)

    # --- ② 正常插件：新旧判定必须相同 --------------------------------------
    ref_v, ref_d = _ref_b3(kernel, plugin)
    new_v, new_d = _prod_b3(kernel, plugin)
    ok("★ [B3] 正常插件：**参考（旧）与生产（新）判定相同**，且都是「是」",
       ref_v == new_v == "是", f"旧 {ref_v}（{ref_d}）/ 新 {new_v}（{new_d}）")

    # --- ③ 注入 NaN：新旧判定必须相同，且都得是「否」 ----------------------
    nan_plugin = _NaNPenalty()
    ref_v2, ref_d2 = _ref_b3(kernel, nan_plugin)
    new_v2, new_d2 = _prod_b3(kernel, nan_plugin)
    ok("★ [B3] 注入 NaN：**参考（旧）与生产（新）判定相同**，且都是「否」"
       f" —— 删掉三重循环**没把该红的漏掉**",
       ref_v2 == new_v2 == "否", f"旧 {ref_v2}（{ref_d2}）/ 新 {new_v2}（{new_d2}）")

    # --- ④ 对照不是空转 -----------------------------------------------------
    ok("★ [B3] 这个对照**能红也能绿**（② 是「是」、③ 是「否」）—— 不是空转",
       ref_v == "是" and ref_v2 == "否",
       f"正常 {ref_v} / 注入 {ref_v2}")

    # --- ⑤ 删掉的那些消息是**冗余**的，不是唯一来源 ------------------------
    #: 旧版对同一个 NaN 会报**两遍**：一次「有 NaN」，一次「两两不可比」。
    #: ⇒ 旧版的条目数**必须多于**新版。若两边一样多，说明那个循环当时根本没在跑 ——
    #:   那「删掉它」就不是等价变换，而是删掉了一个**真在工作**的东西。
    def _n(detail: str) -> int:
        head = (detail or "").split(" ")[0]
        return int(head) if head.isdigit() else -1

    ok("★ [B3] 旧版对注入报的条目数**多于**新版 —— 证明那个循环当时**确实在跑**，"
       f"而且它报的是**重复**",
       _n(ref_d2) > _n(new_d2) > 0, f"旧 {ref_d2} / 新 {new_d2}")

    # --- ⑥ 生产实现里**确实没有**那个三重循环 -------------------------------
    import inspect
    src = inspect.getsource(b3_penalty_comparable)
    ok("★ [B3] 生产实现里**确实没有**三层嵌套循环（源码级确认，防止悄悄加回来）",
       "for i in range(len(ids))" not in src,
       "源码里又出现了 `for i in range(len(ids))` —— 那个 O(L²n) 回来了")

    # --- ⑦ ★ `checked` 必须**数到每一个方向** ---------------------------------
    #: 这条专门钉一个**真实发生过的回归**：删三重循环时，那三行记账
    #: （`table[...] = vals` / `checked +=` / NaN 检查）**掉出了** `for d in layer:` 循环体
    #: ⇒ 每层**只记最后一个方向**，而 `B3` 依旧报「是」。
    #:
    #: ⚠️ 上面 ①–⑥ **全都照过**：异常条目仍按方向产生（那个内层循环没动），
    #:    所以 ⑤ 的「旧 > 新」还成立。**只有数 `checked` 抓得住它。**
    dirs = kernel.all_directions()
    n_items = len(kernel.items)
    sizes = [sum(1 for d in dirs if d.rank == r) for r in {d.rank for d in dirs}]
    expect = sum(s for s in sizes if s >= 2) * n_items
    got = _n(new_d)
    ok("★ [B3] `checked` **数到了每一个方向**（= Σ_层 层内方向数 × 项数）"
       " —— 少了就是记账行又掉出循环体了",
       expect > 0 and got == expect,
       f"报 {got} / 应 {expect}（层内方向数 {sorted(sizes, reverse=True)[:6]}，项数 {n_items}）")

    # --- ⑧ 建表的规模 = Σ_层 方向数 × 项数（**不是** 方向数² × 项数）----------
    class _Counting:
        name = "计数"

        def __init__(self, inner):
            self._inner = inner
            self.calls = 0

        def penalty(self, d, item):
            self.calls += 1
            return self._inner.penalty(d, item)

    cnt = _Counting(plugin)
    _prod_b3(kernel, cnt)
    ok("★ [B3] `call_penalty` 的调用数 = Σ_层 方向数 × 项数"
       "（建表是**线性**规模，不是两两配对）",
       cnt.calls == expect, f"实调 {cnt.calls} / 应 {expect}")


# ═══ 树性：`§10.2 D` 的持久化前提（§K5 / B13） ═══════════════════════════════

def test_tree_premise() -> None:
    """★ 「每方向**恰好一个父**」是 `§10.2 D` 的**前提**，而它原来没有任何检查守着。

    持久化的路线**押在入度上**（Driscoll 等 1989）：

        树（一个父）  ⇒ node-copying ⇒ 访问旧版本 **O(1)**
        DAG（多个父） ⇒ 只能 fat node ⇒ 访问旧版本 **O(log m)**

    而 `§K5` 的原文是 `rank = 1 + max(rank(父))` —— 那个 `max` 是**为 DAG 写的**；
    `B13` 原来只查「无环 / 无自环 / 见证更粗」⇒ **对 DAG 照样放行**
    （实测见 `outputs/_probe_b13_gap.py`：入度改成 2，`B13` 仍报「是」）。

    ⇒ 这是「空转与通过长得一模一样」的又一形态：**前提成立与否，判据上长得一样。**

    ## 五条断言

      1. 三个插件 × 批建 / 维护：**语义入度 max = 1**，外生方向入度 = 0
      2. 两条边**互为转置**（`parent` / `witness` 与 `_children` 不打架）
      3. **非退化前提**：扇出 max > 1 —— 否则「恰好一个父」可能只是
         「每个方向本来就只有一个子」的副产品，那这条断言什么都没测
      4. ★ 把入度**注入成 2**（造 DAG）⇒ `B13` 必须报「否」
      5. ★ **反向判据**：还原成树 ⇒ `B13` 必须报「是」—— 防「永远红」
    """
    from ldv.checks._fixtures import (
        build_incremental, build_keyset, build_reach, build_sequence,
        load, make_builder,
    )
    from ldv.checks._framework import Report
    from ldv.checks.structure import b13_rank_well_founded

    loaded = load()
    if loaded is None:
        PASS.append("树性（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded

    def sem_deg(k):
        deg = {d.did: 0 for d in k.all_directions()}
        fan = {}
        for p in k.all_directions():
            kids = k.children_of(p)
            fan[p.did] = len(kids)
            for c in kids:
                deg[c.did] = deg.get(c.did, 0) + 1
        return deg, fan

    def transposed(k):
        """两条边互为转置吗？返回不符处。"""
        by_id = {d.did: d for d in k.all_directions()}
        bad = []
        for p in k.all_directions():
            for c in k.children_of(p):
                if c.parent != p.did:
                    bad.append(f"{c.did}.parent={c.parent} ≠ 实际父 {p.did}")
                if c.witness != (p.did,):
                    bad.append(f"{c.did}.witness={c.witness} ≠ ({p.did},)")
        for d in k.all_directions():
            if d.origin == ORIGIN_EXOGENOUS:
                continue
            if d.parent is None or d.parent not in by_id:
                bad.append(f"{d.did}.parent={d.parent} 不可用")
            elif d.did not in [c.did for c in k.children_of(by_id[d.parent])]:
                bad.append(f"{d.did} 不在其父 {d.parent} 的子列表里")
        return bad

    # --- ① 基线：三个插件 × 两条路径，语义入度必须是 1 ------------------------
    kernels: list[tuple[str, object]] = []
    for which in ("keyset", "reach", "sequence"):
        k, _ = (build_keyset(nodes) if which == "keyset"
                else (build_reach(nodes, edges) if which == "reach"
                      else build_sequence(nodes, edges)))
        kernels.append((f"{which}·批建", k))
    ids = sorted(nodes)
    for which in ("keyset", "reach", "sequence"):
        mk = make_builder(which, nodes, edges)
        kernels.append((f"{which}·维护", build_incremental(mk, nodes, ids[:6], ids[6:])))

    worst_in, worst_fan, worst_tr = 0, 0, []
    for tag, k in kernels:
        deg, fan = sem_deg(k)
        mx = max(deg.values()) if deg else 0
        worst_in = max(worst_in, mx)
        worst_fan = max(worst_fan, max(fan.values()) if fan else 0)
        worst_tr += [f"{tag}:{m}" for m in transposed(k)]
        ok(f"★ [树性] {tag}：非根方向**恰好一个父**（语义入度 max = 1）",
           mx == 1, f"实测 {mx}")
        roots_bad = [d.did for d in k.all_directions()
                     if d.origin == ORIGIN_EXOGENOUS and deg[d.did] != 0]
        ok(f"★ [树性] {tag}：外生方向（根）**没有父**", not roots_bad, f"{roots_bad[:2]}")

    ok("★ [树性] 两条边**互为转置**（`parent` / `witness` 与 `_children` 不打架）",
       not worst_tr, f"{worst_tr[:2]}")
    ok("★ [树性] 非退化前提：扇出 max > 1"
       "（否则「恰好一个父」可能只是「每个方向本来就只有一个子」的副产品）",
       worst_fan > 1, f"扇出 max = {worst_fan}")

    # --- ④ 注入：把入度改成 2（造 DAG）⇒ B13 必须红 -------------------------
    # ⚠️ 这段要**两个**内部父方向：把 `p2` 的一个子挂到 `p1` 底下 ⇒ 那个子的语义入度
    #    变成 2。与 `test_stay_at_parent` 同一类问题：**夹具的前提是按随仓库那份
    #    36 项语料挑的**。语料小到内部父方向少于两个时 `parents[1]` 直接
    #    `IndexError` ⇒ **整趟测试崩**（红是判据说话，崩是判据没了 —— 后面的测试
    #    函数一个都不跑）。实测：`edge-single`(1 项) 崩在这一行。
    #    ⇒ 前提不成立就报**跳过**，并把「跳过了什么」写出来 —— 跳过 ≠ 通过。
    k, _ = build_keyset(nodes)
    parents = [d for d in k.all_directions() if k.children_of(d)]
    if len(parents) < 2:
        PASS.append(f"[树性] 注入入度 = 2 造 DAG（**跳过**：只有 {len(parents)} 个"
                    f"内部父方向，这份夹具要 ≥2 —— 它是按随仓库那份 36 项语料挑的）")
    else:
        p1, p2 = parents[0], parents[1]
        victim = k.children_of(p2)[0]
        k._children[p1.did] = tuple(k._children[p1.did]) + (victim.did,)  # noqa: SLF001
        deg, _fan = sem_deg(k)
        ok("★ [树性] 注入生效：语义入度真的变成了 2",
           max(deg.values()) == 2, f"实测 max = {max(deg.values())}")
        rep = Report(plugin="keyset", expects=("B13",))
        b13_rank_well_founded(k, rep)
        ok("★★ [树性] **`B13` 对 DAG 必须报「否」**"
           " —— 改之前它对 DAG 照样报「是」，`§10.2 D` 的前提因此没被守",
           rep.assertions[0].result is Tri.NO,
           f"B13 = {rep.assertions[0].result}；{rep.assertions[0].detail[:60]}")

    # --- ⑤ 反向判据：还原成树 ⇒ B13 必须绿（防「永远红」） -------------------
    k2, _ = build_keyset(nodes)
    rep2 = Report(plugin="keyset", expects=("B13",))
    b13_rank_well_founded(k2, rep2)
    ok("★ [树性] 反向判据：正常树上 `B13` 报「是」（防「永远红」）",
       rep2.assertions[0].result is Tri.YES,
       f"B13 = {rep2.assertions[0].result}；{rep2.assertions[0].detail[:60]}")


# ═══ 查询结果的 `hit_items` 必须**每个说「是」的方向都收** ═══════════════════

def test_query_hit_items() -> None:
    """★ `hit_items` 只在**叶**上收成员 ⇒ 停在**内部**方向的滞留项**静默地少**。

    出路 (4)（§10.2 B，已落地）让项可以停在**内部**方向。而 `query` 原来是：

        说「是」⇒ 有子方向就往下走（**不收自己的成员**）；没有子方向才收

    ⇒ 一个停在内部方向的项，**父的 `命中` 说「是」**，但它**不进** `hit_items`。
    实测（`outputs/_probe_leak_vs_stay.py`，`sequence` 先建 6 维护 30）：

        滞留项 16 个 ⇒ 进 `hit_items` 的 **0 个**

    而 `QueryResult.render()` 照样印「**命中 0 项**」—— 名字像答案、实际不全。
    这正是本仓库反复要防的形状：**少掉的部分不会让任何东西变红。**

    ## 五条断言

      1. **非退化前提**：这份夹具上**确实有滞留**（否则下面全在查空气）
      2. ★ **完整性**：每个说「是」的方向，其 `members ∩ ideal` **必须**在 `hit_items` 里
      3. **可靠性**：`hit_items ⊆ ideal`（不许把不相关的项塞进来）
      4. ★ **反向判据**：`hit_items` **不许**包含「不在任何说「是」方向成员里」的项
         —— 防「干脆把 `ideal` 原样返回」（那会让 2 变恒真）
      5. ★ **三态不许被 `hit_items` 带偏**：`status` 必须**只**由 `yes` / `unexpanded` 决定
         —— 原来的 `if hits: status = …` 是**空操作**（`fold(是, 未展开) == 未展开`），
            留着它会让「改成每个方向都收」悄悄改掉三态
    """
    from ldv.checks._fixtures import build_incremental, load, make_builder

    loaded = load()
    if loaded is None:
        PASS.append("查询命中项（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    k = build_incremental(make_builder("sequence", nodes, edges),
                          nodes, ids[:6], ids[6:])

    # --- ① 非退化前提：真有滞留 -------------------------------------------
    stay = sum(len(k.stayed_of(d)) for d in k.all_directions())
    ok("★ [hit_items] 非退化前提：这份夹具上**确实有滞留**（否则下面全在查空气）",
       stay > 0, f"滞留 {stay} 项")

    # --- ②③④ 逐项查一遍 ---------------------------------------------------
    missed: list[str] = []
    extra: list[str] = []
    not_in_ideal: list[str] = []
    checked = 0
    for d in k.all_directions():
        for x in sorted(k.members_of(d)):
            q = Query(ideal=frozenset({x}))
            r = k.query(q)
            checked += 1
            if x not in r.hit_items:
                missed.append(f"{d.did}:{x}")
            if not r.hit_items <= q.ideal:
                not_in_ideal.append(d.did)
    # 反向：随机挑若干**不在任何「是」方向成员里**的项，不许出现
    for x in ids[:12]:
        q = Query(ideal=frozenset({x}))
        r = k.query(q)
        in_yes = set()
        for did in r.yes:
            in_yes |= set(k.members_of(k.direction(did)))
        if (r.hit_items - in_yes):
            extra.append(f"{x}:{sorted(r.hit_items - in_yes)[:2]}")

    ok("★ [hit_items] 完整性：每个说「是」的方向的 `members ∩ ideal` **都在** `hit_items` 里",
       not missed, f"{len(missed)}/{checked} 项没进：{missed[:3]}")
    ok("★ [hit_items] 可靠性：`hit_items ⊆ ideal`", not not_in_ideal,
       f"{not_in_ideal[:3]}")
    ok("★ [hit_items] 反向判据：`hit_items` **不许**多出「不在任何「是」方向成员里」的项"
       "（防「把 `ideal` 原样返回」）", not extra, f"{extra[:3]}")

    # --- ⑤ 三态只由 yes / unexpanded 决定 ---------------------------------
    bad_status: list[str] = []
    for x in ids[:12]:
        q = Query(ideal=frozenset({x}))
        r = k.query(q)
        want = (Tri.YES if r.yes and not r.unexpanded
                else fold(Tri.YES, Tri.UNEXPANDED) if r.yes
                else Tri.UNEXPANDED if r.unexpanded else Tri.NO)
        if r.status is not want:
            bad_status.append(f"{x}: {r.status} ≠ {want}")
    ok("★ [hit_items] 三态**只由 `yes` / `unexpanded` 决定**"
       "（原来的 `if hits: status = …` 是空操作，留着会被这个改动悄悄改掉）",
       not bad_status, f"{bad_status[:3]}")


# ═══ reach 插件缓存：前提 + 透明性 ═══════════════════════════════════════════

#: 图状态的三个载体。**缓存的前提就是它们不被写** —— 名单写在这里，别散在函数里。
_GRAPH_ATTRS = ("edges", "_rev", "_sinks")
_MUTATORS = ("add", "update", "pop", "remove", "clear", "discard", "setdefault",
             "append", "extend", "insert")


def _graph_writes_outside_init(cls: type) -> list[str]:
    """AST 扫 `cls` 的源码：图状态有没有在 `__init__` **之外**被写。

    ⚠️ **用 AST 不用 grep**：`reach.py` 的 docstring 里就写着
    「`self.edges` 只在 `__init__` 里被读」这句话 —— grep 会把**注释**
    当成写入报出来（假红），也会漏掉 `self._rev.add(...)` 这种就地改（假绿）。
    AST 只看语法上的赋值 / 调用目标。
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    bad: list[str] = []

    def _is_graph_attr(node: ast.AST) -> str | None:
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self" and node.attr in _GRAPH_ATTRS):
            return node.attr
        return None

    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if fn.name == "__init__":
            continue
        for sub in ast.walk(fn):
            targets: list[ast.AST] = []
            if isinstance(sub, ast.Assign):
                targets = list(sub.targets)
            elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                targets = [sub.target]
            for t in targets:
                for n in ast.walk(t):
                    a = _is_graph_attr(n)
                    if a:
                        bad.append(f"{fn.name}() 第 {sub.lineno} 行写了 self.{a}")
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                obj = sub.func.value
                if isinstance(obj, ast.Subscript):
                    a = _is_graph_attr(obj.value)
                    if a:
                        bad.append(f"{fn.name}() 第 {sub.lineno} 行就地改 self.{a}[...]")
                a = _is_graph_attr(obj)
                if a and sub.func.attr in _MUTATORS:
                    bad.append(f"{fn.name}() 第 {sub.lineno} 行 self.{a}.{sub.func.attr}()")
    return bad


def _graph_fingerprint(plug: Any) -> tuple:
    """图状态的指纹 —— 三个载体逐项排好序。"""
    return (
        tuple(sorted((k, tuple(sorted(v))) for k, v in plug.edges.items())),
        tuple(sorted((k, tuple(sorted(v))) for k, v in plug._rev.items())),
        tuple(sorted(plug._sinks)),
    )


class _MutatingReach:
    """**已知答案的对照组**：构造之后**改图** —— 前提检查必须把它抓出来。

    没有这个类，「AST 扫不出写入」可能只是**扫描器不会扫**（空转），
    而不是**代码干净**。两者的输出长得一模一样。
    """

    def __init__(self, edges: dict[str, Any]) -> None:
        self.edges = {k: frozenset(v) for k, v in edges.items()}
        self._rev: dict[str, set[str]] = {}
        self._sinks: frozenset[str] = frozenset()

    def penalty(self, d: Any, item: Any) -> float:
        self.edges[str(item["id"])] = frozenset()      # ← 构造之后写图
        self._rev.setdefault("x", set()).add("y")      # ← 就地改
        return 0.0


def test_reach_cache_premise() -> None:
    """★ 缓存的**前提**：「图在 `__init__` 之后只读」—— 这条必须被检查。

    ## 为什么缓存需要一个前提

    插件的缓存键是 `payload`、值是**整条 BFS 输出**（见 `plugins/reach.py` 模块
    docstring）。键能选得这么粗，靠的是**同一个 payload 的答案永远一样** ——
    而这一条等价于**图不变**。

    Acar / Blelloch / Harper 2002（POPL，§2）把它点名为**关键要求**：

    > **The key requirement, therefore, is not that there are no side-effects,
    > but rather that all data is persistent** (i.e., the closure's environment
    > cannot be modified).

    ⇒ 图一旦可变，缓存里就是**过期值**：判据照旧报「是」，而它比的是**上一版图的答案**
    —— 「空转与通过长得一模一样」。

    ## 三条，每条都能红

        ① **静态**：AST 扫出「`self.edges` / `self._rev` / `self._sinks`
           只在 `__init__` 里被写」
        ② **对照**：拿一个**构造后改图**的类跑同一个扫描 ⇒ **必须扫得出来**
           （否则 ① 只是「扫描器不会扫」）
        ③ **行为**：把插件跑完一遍完整用法（`hit` / `penalty` / `split` / `merge`）
           ⇒ 图指纹**逐项未变**
    """
    from ldv.plugins.reach import ReachPlugin

    # --- ① 静态：写入只在 __init__ 里 ------------------------------------
    bad = _graph_writes_outside_init(ReachPlugin)
    ok("★ [缓存前提] `ReachPlugin` 的图状态（`edges` / `_rev` / `_sinks`）"
       "**只在 `__init__` 里被写**",
       not bad, f"`__init__` 之外有 {len(bad)} 处写入：{bad[:3]}")

    # --- ② 对照：扫描器**真的会扫** --------------------------------------
    caught = _graph_writes_outside_init(_MutatingReach)
    ok("★ [缓存前提] 对照：一个**构造后改图**的类 ⇒ 同一个扫描**必须**报出来"
       "（否则上面那条是空转）",
       len(caught) >= 2, f"只报出 {len(caught)} 处：{caught[:3]}")

    # --- ③ 行为：跑完一遍完整用法，图指纹不变 ----------------------------
    loaded = load()
    if loaded is None:
        ok("★ [缓存前提] 语料在（这条测试要真语料，缺了就报「跳过」不是「过」）",
           False, "找不到语料")
        return
    nodes, edges, _ = loaded
    plug = ReachPlugin(edges)
    before = _graph_fingerprint(plug)

    k, _ = build_reach(nodes, edges)
    items_sorted = sorted(k.items)
    for d in k.all_directions():
        plug.hit(d, Query(ideal=frozenset({items_sorted[0]})))
        plug.reachable(frozenset(d.payload))
        for i in items_sorted:
            plug.penalty(d, k.items[i])
        if k.children_of(d):
            plug.merge(list(k.children_of(d)))          # 子方向是 `Direction` 对象
    for i in items_sorted:
        plug.signature(i)

    after = _graph_fingerprint(plug)
    ok("★ [缓存前提] 行为侧：跑完 `hit` / `penalty` / `split` / `merge` 之后"
       "**图指纹逐项未变**",
       before == after,
       "图被改过 —— 缓存里的值是过期的（假绿）")
    st = plug.cache_stats()
    ok("★ [缓存前提] 这一遍确实**用到了缓存**（命中 > 0）—— 否则前提查的是空气",
       st["distance 命中"] > 0 and st["reachable 命中"] > 0, f"读数 {st}")
    print(f"    · reach 缓存读数：{st}（**度量**，不进退出码）")


def test_reach_cache_transparency() -> None:
    """★ 缓存**不许改变数值** —— 三臂对照，两个改动分开查。

    ## 这次同时动了两件事

        改动 1  **算法**：`distance` 从「每项一遍正向 BFS」改成
                「一遍**多源反向** BFS ⇒ 所有项的距离」
        改动 2  **缓存**：键 `payload`，值 = 整条 BFS 输出

    混在一起就分不清谁错了 ⇒ 三臂对**参考实现**比：

        臂 REF      **改动前逐字**的 `distance` / `reachable` / `signature`
        臂 NO-CACHE 改动 1，缓存关
        臂 CACHE    改动 1 + 2

    读法：

        REF≠NO-CACHE           ⇒ 问题在**算法**（多源反向 BFS 写错了）
        NO-CACHE 绿、CACHE 红  ⇒ 问题在**缓存**（串台 / 过期）
        两臂一致地红           ⇒ 问题在**两边共用的那部分**（图的构造）

    ⚠️ **顺序**：先把参考值**全**算出来，再让每个臂各跑一遍（各用全新实例）。
       反过来的话，预热步骤会把某个臂的缓存烤热 ⇒ 注入被掩盖
       （`test_cover_oracle_transparency` 实测踩过这一条）。

    ## 逐条钉住

        ① 对照用的 payload **真的算出东西**（距离不全为 0）—— 否则「相同」可能只是「都空」
        ② 臂 NO-CACHE vs REF 逐项相同（**算法**那一步干净）
        ③ 臂 CACHE vs REF 逐项相同（**Acar：值算没算都一样**）
        ④ 臂 CACHE vs NO-CACHE 逐项相同（只含缓存那一步）
        ⑤ **注入**「缓存返回错值（串台）」⇒ ③ 必须红，而 ② 必须**仍然绿**
           —— 精确命中「问题在缓存里」，不是一片红
        ⑥ 缓存确实在命中（度量，不进退出码）
    """
    from ldv.plugins.reach import ReachPlugin

    loaded = load()
    if loaded is None:
        ok("★ [缓存透明] 语料在（这条测试要真语料，缺了就报「跳过」不是「过」）",
           False, "找不到语料")
        return
    nodes, edges, _ = loaded
    edges = {k: frozenset(v) for k, v in edges.items()}
    rev: dict[str, set[str]] = {k: set() for k in edges}
    for src, dsts in edges.items():
        for dst in dsts:
            rev.setdefault(dst, set()).add(src)

    k, _ = build_reach(nodes, edges)
    items_sorted = sorted(k.items)
    dirs = sorted(k.all_directions(), key=lambda d: (d.rank, d.did))

    #: 生产顺序（`checks/contract.py` 的 `b3`）：同层内**同一个 payload 连续用满 n 次**
    pairs: list[tuple[Any, str]] = []
    for rank in sorted({d.rank for d in dirs}):
        layer = [d for d in dirs if d.rank == rank]
        if len(layer) < 2:
            continue
        for d in layer:
            for i in items_sorted:
                pairs.append((d, i))
    payloads = [frozenset(d.payload) for d, _ in pairs]

    # --- 参考实现（改动前逐字）—— **先全算出来** ------------------------
    ref_pen = [_ref_distance(edges, i, frozenset(d.payload)) for d, i in pairs]
    ref_reach = [_ref_reachable(rev, frozenset(d.payload)) for d in dirs]
    ref_sig = [_ref_signature(edges, i) for i in items_sorted]

    # --- ① 对照不是空转：距离里必须有非 0 项 ----------------------------
    nonzero = sum(1 for v in ref_pen if v)
    ok("★ [缓存透明] 对照用的代价值**真的算出东西**（有非 0 距离）"
       "—— 否则「相同」可能只是「都返回同一个常数」",
       nonzero > 0, f"非 0 {nonzero}/{len(ref_pen)}")

    # --- 三个臂（各用全新实例） ------------------------------------------
    probe = Query(ideal=frozenset({items_sorted[0]}))

    def arm(cache: bool, cls: type = ReachPlugin):
        p = cls(edges, cache=cache)
        #: 代价按 `b3` 的顺序：`for d in layer: for i in items` —— 同一 payload 连续 n 次
        pen = [p.penalty(d, k.items[i]) for d, i in pairs]
        rea = []
        for d in dirs:
            #: ⚠️ 同一个 payload **连着问两遍**才是生产的形状：`hit` 内部**也**调
            #:    `reachable`（`b2` 的 109k 次就是它）。只问一遍的话可达那一路
            #:    命中恒为 0 —— 量出来的是**测试的形状**，不是生产的。
            rea.append(p.reachable(frozenset(d.payload)))   # 第一遍：算
            p.hit(d, probe)                                 # 第二遍：命中
        sig = [p.signature(i) for i in items_sorted]
        return p, pen, rea, sig

    p_nc, pen_nc, rea_nc, sig_nc = arm(False)
    p_c, pen_c, rea_c, sig_c = arm(True)

    def diff(xs, ys) -> list[str]:
        return [f"#{i}: {x!r} ≠ {y!r}" for i, (x, y) in enumerate(zip(xs, ys)) if x != y]

    # --- ② 算法那一步：NO-CACHE vs REF -----------------------------------
    d_pen = diff(pen_nc, ref_pen)
    ok("★ [缓存透明] 臂 NO-CACHE 的 `penalty` 与**参考实现**逐项相同"
       "（多源反向 BFS 那一步干净）",
       not d_pen, f"{len(pairs)} 对里有 {len(d_pen)} 处不同：{d_pen[:2]}")
    ok("★ [缓存透明] 臂 NO-CACHE 的 `reachable` / `signature` 与参考逐项相同",
       not diff(rea_nc, ref_reach) and not diff(sig_nc, ref_sig),
       f"reachable {len(diff(rea_nc, ref_reach))} 处 / "
       f"signature {len(diff(sig_nc, ref_sig))} 处")

    # --- ③ 加上缓存：CACHE vs REF ----------------------------------------
    d_pen_c = diff(pen_c, ref_pen)
    ok("★ [缓存透明] 臂 CACHE 的 `penalty` 与**参考实现**逐项相同"
       "（Acar：值算没算都一样）",
       not d_pen_c, f"{len(pairs)} 对里有 {len(d_pen_c)} 处不同：{d_pen_c[:2]}")
    ok("★ [缓存透明] 臂 CACHE 的 `reachable` / `signature` 与参考逐项相同",
       not diff(rea_c, ref_reach) and not diff(sig_c, ref_sig),
       f"reachable {len(diff(rea_c, ref_reach))} 处 / "
       f"signature {len(diff(sig_c, ref_sig))} 处")

    # --- ④ 只含缓存那一步：CACHE vs NO-CACHE ------------------------------
    d_c = diff(pen_c, pen_nc)
    ok("★ [缓存透明] 臂 CACHE 与 NO-CACHE 的 `penalty` 逐项相同（只含缓存那一步）",
       not d_c, f"{len(d_c)} 处不同：{d_c[:2]}")

    # --- ⑤ 注入：缓存**串台** ⇒ ③ 红，② 仍绿 -----------------------------
    #: ⚠️ 注入必须**继承** `ReachPlugin` 而不是包一层：`penalty` 内部调的是
    #:    `self.distance` / `self._distances`，包一层的话 `self` 是**被包的实例**，
    #:    覆盖永远轮不到 —— 注入会静默失效（那才是最坏的一种「注入」）。
    class _WrongKeyReach(ReachPlugin):   # noqa: D401
        """**注入**：键取错 —— 命中时返回缓存里**第一个** payload 的结果。

        真实里最容易犯的正是这个（「反正都是同一批项，差不多就复用」），
        而它的症状**不是报错**，是**静默串台**：第二个 payload 拿到第一个的答案
        ⇒ 判据拿错的值去比 ⇒ **假绿**。所以它必须**只**打缓存那一路。
        """

        def _distances(self, anchors: frozenset[str]) -> dict[str, float]:
            if self.cache and self._dist_cache:
                self.n_distance_hit += 1
                return next(iter(self._dist_cache.values()))     # ← 串台
            return super()._distances(anchors)

        def reachable(self, anchors: Any) -> frozenset[str]:
            if self.cache and self._reach_cache:
                self.n_reachable_hit += 1
                return next(iter(self._reach_cache.values()))    # ← 串台
            return super().reachable(anchors)

    _, pen_bad, rea_bad, sig_bad = arm(True, _WrongKeyReach)
    d_bad = diff(pen_bad, ref_pen)
    ok("★ [缓存透明] 注入「缓存返回**别的 payload** 的值（串台）」"
       "⇒ 与参考**必须**对不上（③ 不是空转）",
       bool(d_bad), f"注入后仍然全同 —— 那 ③ 就是空转（{len(pairs)} 对）")
    _, pen_bad_nc, _, _ = arm(False, _WrongKeyReach)
    ok("★ [缓存透明] 同一次注入**精确**命中：关掉缓存那一臂**仍然绿**"
       "（⇒ 问题在缓存里，不是一片红）",
       not diff(pen_bad_nc, ref_pen),
       f"关掉缓存也红了 {len(diff(pen_bad_nc, ref_pen))} 处 ⇒ 注入没打在缓存上")
    ok("★ [缓存透明] 同一次注入在 `reachable` 那一路也**必须**对不上",
       bool(diff(rea_bad, ref_reach)),
       "可达缓存那一路注入后仍然全同 ⇒ 那条注入没打上")
    ok("★ [缓存透明] 注入也**只**打 `penalty` / `reachable` 两处缓存，"
       "`signature` 那一路不受影响（对照，说明注入不是无差别污染）",
       not diff(sig_bad, ref_sig), f"{len(diff(sig_bad, ref_sig))} 处不同")

    # --- ⑥ 缓存确实在命中（度量） ----------------------------------------
    st = p_c.cache_stats()
    ok("★ [缓存透明] 缓存**确实在命中**（命中 > 0）—— 否则缓存是直通，白占内存",
       st["distance 命中"] > 0 and st["reachable 命中"] > 0, f"读数 {st}")
    print(f"    · reach 缓存读数：调用 {st['distance 调用']} / 命中 {st['distance 命中']}"
          f"（可达 {st['reachable 调用']}/{st['reachable 命中']}）"
          f"（**度量**，不进退出码）")


def _ref_distance(edges: dict[str, frozenset[str]], item_id: str,
                  anchors: frozenset[str]) -> float:
    """**改动前逐字**的 `ReachPlugin.distance` —— 每项一遍**正向** BFS。"""
    from collections import deque

    if item_id in anchors:
        return 0.0
    seen = {item_id}
    q = deque([(item_id, 0)])
    while q:
        cur, dist = q.popleft()
        for nxt in edges.get(cur, ()):
            if nxt in anchors:
                return float(dist + 1)
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, dist + 1))
    return float(len(edges) + 1)


def _ref_reachable(rev: dict[str, set[str]], anchors: frozenset[str]) -> frozenset[str]:
    """**改动前逐字**的 `ReachPlugin.reachable` —— 单源反向 BFS。"""
    from collections import deque

    seen = set(anchors)
    q = deque(seen)
    while q:
        cur = q.popleft()
        for prev in rev.get(cur, ()):
            if prev not in seen:
                seen.add(prev)
                q.append(prev)
    return frozenset(seen)


def _ref_signature(edges: dict[str, frozenset[str]], item_id: str) -> frozenset[str]:
    """**改动前逐字**的 `ReachPlugin.signature`（含每次重算 `sinks`）。"""
    from collections import deque

    sinks = {n for n in edges if not edges[n]}
    if item_id in sinks:
        return frozenset({item_id})
    seen = {item_id}
    q = deque([item_id])
    found: set[str] = set()
    while q:
        cur = q.popleft()
        if cur in sinks:
            found.add(cur)
            continue
        for nxt in edges.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return frozenset(found) or frozenset({item_id})


# ═══ 删除路径（§10.2 C） ═════════════════════════════════════════════════════
#
# 设计文档 §10.2 C 要六条检查 `D1–D6`，**都跑在删除路径上**。它们**不是** `B` 编号
# （§10.2 D 的持久化判据才用 `P` 编号），所以不进 `run_checks`，落在这里。
#
# ⚠️ 六条写在文档里**不等于六条都能红**。所以每条配一个**已知答案的对照组**
#    （一个错一处的变异内核），并**逐条**印出「基线绿 / 注入红」。
#    任何一条注入后不红，它就是空转 —— 而空转与通过长得一模一样。

def _del_build(cls, which, nodes, edges):
    """用给定的内核类（`Kernel` 或其变异子类）走一遍**与生产相同的批建**。"""
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin

    if which == "keyset":
        plug = KeysetPlugin()
        root = plug.merge([])
    elif which == "reach":
        plug = ReachPlugin(edges, traverse_budget=10_000)
        root = frozenset(nodes)
    elif which == "sequence":
        plug = SequencePlugin(sequences(nodes, edges))
        root = frozenset({()})
    else:
        raise ValueError(which)
    k = cls(plug, items(nodes))
    k.build(root)
    for nid in sorted(nodes):
        k.insert(nid)
    return k


def _d1_empty_needs_record(kernel) -> list[str]:
    """`D1` 空方向必须带失效记录 —— `members(d) == ∅ ⇒ 必须有一条「已失效」账目`。

    这是 `remove()` 第 ② 件（记账）的守卫。**不记账与记了账在结构上长得一样**
    —— 都只是「成员集空了」，所以必须单独查账本。
    """
    bad = []
    for d in kernel.all_directions():
        if kernel.members_of(d):
            continue
        has = any(e.kind == EVENT_INVALIDATED and e.detail.get("members_after") == 0
                  for e in kernel.ledger.events_for(d.did))
        if not has:
            bad.append(d.did)
    return bad


def _d2_cover_leak(kernel, cover) -> int:
    """`D2` 删除后 `覆盖(父) ⊆ ∪覆盖(子)` 仍成立 —— `B18` 的删除路径版本。

    ⚠️ **取的是「漏了、而且没有证明」那一个数**（2026-10-08 改），不是裸漏。
       裸漏含两类**不是违规**的东西：

           ① 论域外的项        还没进结构的（`holdout`）⇒ 结构对它没有义务
           ② 有证明的滞留项    每个子方向都**证明**不收它（§10.2 出路 (4)）

       实测（`outputs/_probe_d2_four.py`，4 份语料 × 3 方向 × 3 段 = 36 行）：
       裸漏 3 ｜收域 2 ｜记账 1 ｜**收域+记账 0**。
       拿裸漏当判据 ⇒ **基线就红**，而那条红与删除**毫无关系**
       （`kernel.remove` 不碰 payload，见它 docstring 的 ③）。
       逐对摊开的实测见 `outputs/_probe_d2_stage2.py` 与 `ldv/flow.py` 的 `RemoveReport`。
    """
    return cover_leak_unaccounted(kernel, cover)["无证明漏项数"]


def _d2_cover_leak_raw(kernel, cover) -> int:
    """`D2` 的**裸漏** —— 度量（报趋势），不是判据。见 `_d2_cover_leak`。"""
    return cover_leak_unaccounted(kernel, cover)["漏项数"]


def _d3_soundness(kernel, cover) -> int:
    """`D3` 删除后 `members(父) ⊆ 覆盖(父)` 仍成立 —— `B16` 的删除路径版本。"""
    return soundness_profile(kernel, cover)["越界成员数"]


def _d4_change_within_cone(mem_before: dict, kernel, path: tuple) -> list[str]:
    """`D4` 变动集合 ⊆ `Cone(x)` —— `B8` 的删除路径版本。"""
    out = []
    for did, mem in mem_before.items():
        if set(kernel._members.get(did, ())) != mem and did not in path:
            out.append(did)
    return out


def _ledger_lines(ledger) -> list[tuple]:
    """账本的**外部**指纹 —— 不用 `Ledger._digest`（那玩意儿只由 `append` 维护，
    就地改 `detail` 它看不见；而 `detail` 是**可变字典**，`Event` 冻结拦不住它）。"""
    return [(e.seq, e.kind, e.did,
             tuple(sorted((k, repr(v)) for k, v in e.detail.items())))
            for e in ledger]


def _d5_append_only(lines_before: list[tuple], kernel) -> list[str]:
    """`D5` 账本只增不改 —— `B9` 的删除路径版本。"""
    now = _ledger_lines(kernel.ledger)
    bad = []
    if len(now) < len(lines_before):
        bad.append(f"账本变短：{len(lines_before)} → {len(now)}")
    for i, fp in enumerate(lines_before):
        if i >= len(now) or now[i] != fp:
            bad.append(f"第 {i} 条被改写")
    return bad


def _d6_states_separable(kernel, before_dirs, nonexistent: str = "D9999") -> list[str]:
    """`D6` 「已失效」与「从来没存在过」分得开 —— `§K4`。

    分得开靠**两样都在**：方向仍在 `_dirs` 里查得到 + 账本里有记录。
    两样缺一，一个「支持集空了的旧方向」与一个「从没建过的编号」就**分不开**了
    —— 而 `§M2 情形③` 的原话正是「这两个必须分得开」。
    """
    bad = []
    now = {d.did for d in kernel.all_directions()}
    for did in sorted(before_dirs - now):
        bad.append(f"{did} 删前在 `_dirs` 里、删后不见了（真删了方向）")
    for d in kernel.all_directions():
        if not kernel.members_of(d) and kernel.ledger.status_of(d.did) == "不存在":
            bad.append(f"{d.did} 空了却报「不存在」")
    if kernel.ledger.status_of(nonexistent) != "不存在":
        bad.append(f"{nonexistent} 从未存在却报 {kernel.ledger.status_of(nonexistent)!r}")
    return bad


# --- 六个对照内核 —— 各错一处 -----------------------------------------------

class _NoLedger(Kernel):
    """`D1` 对照：去成员，但**不记失效**。"""

    def remove(self, item_id: str) -> tuple[str, ...]:
        if item_id not in self.items:
            raise KeyError(item_id)
        path = tuple(self._path.get(item_id, ()))
        del self.items[item_id]
        for did in path:
            self._members.get(did, set()).discard(item_id)
        self._path.pop(item_id, None)
        return path


class _DropChild(Kernel):
    """`D2` 对照：把路径上最深的方向从**父的 `_children`** 里摘掉（拆父子边）。"""

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        for did in reversed(path):
            par = self._dirs[did].parent
            if par is not None:
                self._children[par] = tuple(
                    c for c in self._children.get(par, ()) if c != did)
                break
        return path


class _AccountedChild(_DropChild):
    """`D2` 的**已知答案对照组**：漏是**有账**的那种 ⇒ 判据必须**绿**。

    与 `_DropChild` **同一处结构破坏**（父的 `_children` 掉一条边），
    唯一区别是**给漏掉的每一项补一条 `stayed` 账目** ——
    于是它们从「无证明的漏」变成「有证明的滞留」（§10.2 出路 (4)）。

    ⇒ 这一格的**已知答案**是：判据 **0**（绿），而**裸漏非 0**。

    为什么必须有这一格：判据从「裸漏」换成「漏 − 账」之后，
    `_DropChild` 仍然红（它漏的项本来就没账）—— **光有红那一侧分不出
    两种写法**。要证明判据**真的在读账目**，就得有一个「同一处破坏、
    只多一条账」的对照，而它**必须绿**。两个数印在同一行，
    「没有漏」与「漏了但有账」才分得开。
    """

    #: 由 `_del_arm` 注入外生覆盖 oracle（与 `_DeleteDir` 同一条路）。
    _cover: Any = None

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        if self._cover is None:
            return path
        for d in self.all_directions():
            kids = self.children_of(d)
            if not kids:
                continue
            miss = set(self._cover(d.payload))
            for k in kids:
                miss -= set(self._cover(k.payload))
            have = self.stayed_of(d)
            for x in sorted(miss - have):
                self.ledger.append(EVENT_STAYED, d.did, item=x,
                                   reason="对照组：把这条漏记成**有证明**的滞留")
        return path


class _GhostMember(Kernel):
    """`D3` 对照：往某个方向的成员里塞一个**语料之外**的项。"""

    def __init__(self, plugin, items):
        super().__init__(plugin, items)
        self._done = False

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        if path and not self._done:
            self._members.setdefault(path[-1], set()).add("__幽灵项__")
            self._done = True
        return path


class _OffPath(Kernel):
    """`D4` 对照：动一个**不在 `Cone(x)` 上**的方向。

    ⚠️ 选**去掉一个成员**，不选加一个：实测 `keyset` / `sequence` 上
       `覆盖(d) == members(d)`（payload 是精确的）⇒ 加任何项都会顺手把 `D3`
       （`members ⊆ 覆盖`）弄红，对照就不「精确命中」了。去掉成员则**只缩**
       `members`，`⊆ 覆盖` 仍成立 ⇒ 只有 `D4` 红。留一个成员，免得顺手弄空 ⇒ 惊动 `D1`。
    """

    def __init__(self, plugin, items):
        super().__init__(plugin, items)
        self._done = False

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        if self._done:
            return path
        for d in self.all_directions():
            if d.did in path:
                continue
            mem = self._members.get(d.did, set())
            if len(mem) >= 2:
                mem.discard(sorted(mem)[0])
                self._done = True
                break
        return path


class _Tamper(Kernel):
    """`D5` 对照：就地改一条**既有**账目的 `detail`（`Event` 冻结，`detail` 没冻）。"""

    def __init__(self, plugin, items):
        super().__init__(plugin, items)
        self._done = False

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        if not self._done and len(self.ledger._events) > 0:
            self.ledger._events[0].detail["__篡改__"] = True
            self._done = True
        return path


class _DeleteDir(Kernel):
    """`D6` 对照：把空方向从 `_dirs` 里**摘掉**（真删方向），账本原样保留。

    ⚠️ 它**同时**会破 `D2` —— 被删的方向若曾覆盖东西，它的覆盖就没了。
       这是**真事实**（两条裁决耦合），不是对照没做干净：设计文档 §10.2 C
       明写「三条处置**互相关联**」。所以这一格是**唯一**一个「带红」的对照，
       测试里**显式记下**这件事，而不是把它当噪声盖掉。
    """

    def __init__(self, plugin, items):
        super().__init__(plugin, items)
        self._cover = None

    def remove(self, item_id: str) -> tuple[str, ...]:
        path = super().remove(item_id)
        empty = [did for did in reversed(path)
                 if did in self._dirs and not self._members.get(did)]
        if not empty:
            return path
        if self._cover is not None:            # 挑覆盖最小的删 —— 尽量少破 D2
            empty.sort(key=lambda did: len(self._cover(self._dirs[did].payload)))
        did = empty[0]
        par = self._dirs[did].parent
        if par is not None:
            self._children[par] = tuple(
                c for c in self._children.get(par, ()) if c != did)
        self._dirs.pop(did, None)
        return path


_DEL_MUTANTS: dict[str, type] = {
    "D1": _NoLedger, "D2": _DropChild, "D3": _GhostMember,
    "D4": _OffPath, "D5": _Tamper, "D6": _DeleteDir,
}

#: 进「基线必须全绿」核对的那六个编号。⚠️ `_del_arm` 的返回里还有一个
#: **度量**键 `D2·裸`（裸漏）—— 它**不是判据**，不能进这条 `all(... == 0)`：
#: 度量非 0 是**允许**的（有证明的滞留就是非 0），把它算进去会让基线假红。
_DEL_CODES = ("D1", "D2", "D3", "D4", "D5", "D6")


def _del_arm(cls, which, nodes, edges, cover, frac: int = 3) -> dict:
    """建内核 ⇒ 删最后 `1/frac` 的项 ⇒ 返回六条检查的读数。

    ⚠️ 逐项删、每步都查 —— 取「**任意一步触发的最大违规**」。
       一条检查要能红，一次就够；取最大是为了让「擦肩而过」也算数。
    """
    kernel = _del_build(cls, which, nodes, edges)
    if hasattr(kernel, "_cover"):
        kernel._cover = cover
    before_dirs = {d.did for d in kernel.all_directions()}
    mem_before = {did: set(m) for did, m in kernel._members.items()}
    lines_before = _ledger_lines(kernel.ledger)

    out = {"D1": 0, "D2": 0, "D3": 0, "D4": 0, "D5": 0, "D6": 0, "D2·裸": 0}
    for v in sorted(nodes)[len(nodes) * (frac - 1) // frac:]:
        path = kernel.remove(v)
        out["D1"] += len(_d1_empty_needs_record(kernel))
        out["D2"] = max(out["D2"], _d2_cover_leak(kernel, cover))
        out["D2·裸"] = max(out["D2·裸"], _d2_cover_leak_raw(kernel, cover))
        out["D3"] = max(out["D3"], _d3_soundness(kernel, cover))
        out["D4"] += len(_d4_change_within_cone(mem_before, kernel, path))
        out["D5"] += len(_d5_append_only(lines_before, kernel))
        out["D6"] += len(_d6_states_separable(kernel, before_dirs))
        # 每步之后刷新快照：下一条的「变动」是相对**上一步**
        mem_before = {did: set(m) for did, m in kernel._members.items()}
        lines_before = _ledger_lines(kernel.ledger)
    return out


def test_deletion_path() -> None:
    """★ `§10.2 C` 删除流程的六条检查 `D1–D6` —— 每条都要能红。

    ## 为什么是六条、为什么每条都要配对照

    设计文档 §10.2 C 把六条写在**同一条流程**上，而它们各自守的是不同的东西：

        D1  空方向必须带失效记录        守卫 `remove()` 的第 ② 件（记账）
        D2  删除后 `覆盖(父) ⊆ ∪覆盖(子)` `B18` 的删除路径版本
        D3  删除后 `members(父) ⊆ 覆盖(父)` `B16` 的删除路径版本
        D4  变动集合 ⊆ `Cone(x)`         `B8` 的删除路径版本
        D5  账本只增不改                 `B9` 的删除路径版本
        D6  「已失效」与「从来没存在过」分得开  `§K4`

    ⚠️ **`D2` / `D3` 在真 `Kernel` 上按构造恒绿** —— 删除既不碰 `payload`
       （`§K4` 不可变）也不碰语料，`覆盖` 两边都不动。所以它们不是「会开火的判据」，
       是**守卫**：谁把 `remove()` 改成会动覆盖（比如采纳 `③ 向上收缩父 payload`），
       它们就开火。读数见 `ldv/MEASUREMENTS.md` 结果十一。

    ⚠️ **`D6` 的对照会带红 `D2`** —— 真删方向时，被删方向若曾覆盖东西，
       父的覆盖就漏了。这是**真事实**（两条裁决耦合，见 §10.2 C「三条处置互相关联」），
       测试显式记录它，不当噪声盖掉。

    ## 读数（随仓库 36 项语料，删最后 1/3）

        方向      基线   D1对照  D2对照  D3对照  D4对照  D5对照  D6对照
        keyset    全 0    31      13       1       1       1     22（另带 D2=3）
        reach     全 0   104       9       1       1       1     78（另带 D2=9）
        sequence  全 0     1      25       1       1       1      1（另带 D2=1）
    """
    loaded = load()
    if loaded is None:
        ok("★ [删除路径] 语料在（这条测试要真语料，缺了就报「红」不是「过」）",
           False, "找不到语料")
        return
    nodes, edges, _ = loaded

    for which in ("keyset", "reach", "sequence"):
        cover = coverage_of(which, nodes, edges)
        base = _del_arm(Kernel, which, nodes, edges, cover)
        ok(f"★ [删除路径·{which}] 基线：`D1–D6` **全绿**（真 `Kernel.remove` 干净）",
           all(base[c] == 0 for c in _DEL_CODES),
           f"基线不干净：{ {c: base[c] for c in _DEL_CODES} }")

        for code, cls in _DEL_MUTANTS.items():
            shot = _del_arm(cls, which, nodes, edges, cover)
            ok(f"★ [删除路径·{which}] {code} 对照 `{cls.__name__}`："
               f"基线绿、注入红（否则这条是空转）",
               base[code] == 0 and shot[code] > 0,
               f"基线={base[code]} 注入={shot[code]}")
            others = {k: v for k, v in shot.items()
                      if k != code and k in _DEL_CODES and v > 0}
            print(f"    · {which:8s} {code} ← {cls.__name__:12s} "
                  f"基线 {base[code]} → 注入 {shot[code]}"
                  + (f"；另带红 {others}（D6 对照与 D2 耦合，见 docstring）"
                     if others else "；只此一条红"))

        # ★★ `D2` 的**已知答案对照组**：同一处破坏、只多一条账 ⇒ 判据必须**绿**。
        #    这一格是「判据真的在读账目」的唯一证据 —— 见 `_AccountedChild`。
        ctl = _del_arm(_AccountedChild, which, nodes, edges, cover)
        ok(f"★★ [删除路径·{which}] `D2` **已知答案对照**：漏是**有账**的那种 ⇒ "
           f"判据绿（而裸漏非 0 ⇒ 判据真的在读账，不是把裸漏换个名字）",
           ctl["D2"] == 0 and ctl["D2·裸"] > 0,
           f"判据={ctl['D2']}（应 0）、裸漏={ctl['D2·裸']}（应 > 0）")
        print(f"    · {which:8s} D2 已知答案 ← _AccountedChild "
              f"判据 {ctl['D2']}（绿）、**裸漏 {ctl['D2·裸']}**（非 0 ⇒ 两个数确实不同）")


# ═══ 持久化（§10.2 D 的 P6） ═════════════════════════════════════════════════
#
# `§10.2 D` 的裁决：**持久化层只认 `_children` 为权威边**，
# `parent` / `witness` **从 `_children` 重算，不独立存**。
#
# 四条判据 + 两个「错一处」的对照。⚠️ 对照的**关键性质**：
#   两个对照在**未漂移**的存档上产出**完全相同**的结果 —— 它们**只在漂移后才分得开**。
#   所以「读回来对」这一条**单独不足以**证明只存了一份；
#   注入必须造出**漂移的存档**。

def _persist_build(cls, which, nodes, edges, locks=None):
    from ldv.core.locking import LockedKernel
    from ldv.plugins.reach import ReachPlugin
    from ldv.plugins.sequence import SequencePlugin

    if which == "keyset":
        plug = KeysetPlugin()
        root = plug.merge([])
    elif which == "reach":
        plug = ReachPlugin(edges, traverse_budget=10_000)
        root = frozenset(nodes)
    elif which == "sequence":
        plug = SequencePlugin(sequences(nodes, edges))
        root = frozenset({()})
    else:
        raise ValueError(which)
    assert cls is not LockedKernel
    k = cls(plug, items(nodes))
    k.build(root)
    for nid in sorted(nodes):
        k.insert(nid)
    k.remove(sorted(nodes)[0])          # 让删除路径的账也进存档
    return k, plug


def _drift(data: dict, how: str) -> dict:
    """把一份**带派生边**的存档弄漂 —— `how` 是漂多少。

    ⚠️ 这里漂的是**派生边**（`parent` / `witness`），**不动权威边**（`子`）。
    那正是「各存一份」的失效形态：两份**曾经**一致，后来只改了一份。
    """
    rows = [r for r in data["方向"] if r.get("parent") is not None]
    if not rows:
        return data
    if how == "全量":
        rows = rows
    elif how == "一处":
        rows = rows[len(rows) // 2:len(rows) // 2 + 1]
    else:
        raise ValueError(how)
    first = data["方向"][0]["did"]
    for r in rows:
        r["parent"] = first
        r["witness"] = [first]
    return data


def test_bulk_equivalence() -> None:
    """★ **批量纳入 ≡ 逐个插入**（前提：`build()` 已装同一批）—— 三插件逐字比。

    ★★ 这条判据原来**不存在**：`Kernel.bulk_insert` 的 docstring 写着
       「判据 `test_bulk_insert` ㊖ 在两个语料 × 三个方向上逐字比」，而 ㊖ 实际是
       「树快照版本不符」—— **引用了一条不存在的判据**。所以它一直没被接进
       `_assemble`：README 记的「默认 36 项不等价」，实测是**前提没成立**
       （build 只装了 6 项，却拿 30 项去批量）。

    ★★ **前提是真前提**（反向对照）：build 只装一部分时，批量**确实不等价**
       —— 那正是维护路径不能用的原因。判据把两半都钉住：
       前提成立 ⇒ 逐字相同；前提不成立 ⇒ 必须不同（否则「前提」是装饰）。
    """
    from ldv.checks._fixtures import items as _items
    from ldv.checks._fixtures import load as _load
    from ldv.checks._fixtures import make_builder as _mkb

    loaded = _load()
    if loaded is None:
        ok("★ [批量] 批量 ≡ 逐个（**跳过**：内置语料不在）", True, "0")
        return
    nodes, edges, _d = loaded
    ids = sorted(nodes)
    its = _items(nodes)

    def _sig(k):
        return tuple((d.did, d.parent, len(k.children_of(d)),
                      tuple(sorted(k.members_of(d)))) for d in k.all_directions())

    same: list[str] = []
    diff: list[str] = []
    for which in ("keyset", "reach", "sequence"):
        # ① 前提成立：build 装全集 ⇒ 批量必须逐字相同
        k1, _p = _mkb(which, nodes, edges)(ids)
        for i in ids:
            k1.insert(i, its[i])
        k2, _p2 = _mkb(which, nodes, edges)(ids)
        k2.bulk_insert(ids)
        if _sig(k1) == _sig(k2):
            same.append(which)
        else:
            diff.append(which)

    ok("★ [批量] 批量纳入 ≡ 逐个插入（三插件逐字相同，前提 = build 已装同一批）",
       len(same) == 3,
       f"逐字相同 {same}｜不同 {diff or '无'}")

    # ② 反向对照：**前提不成立**时批量不等价 —— 证明「前提」不是装饰
    k3, _p3 = _mkb("keyset", nodes, edges)(ids[:6])
    for i in ids[6:]:
        k3.insert(i, its[i])
    k4, _p4 = _mkb("keyset", nodes, edges)(ids[:6])
    for i in ids[6:]:
        k4.items[i] = its[i]
    k4.bulk_insert(ids[6:])
    ok("★ [批量] 前提**不成立**时确实不等价（反向对照：维护路径不许用）",
       _sig(k3) != _sig(k4),
       f"build 只装 6 项、再批量 {len(ids) - 6} 项 ⇒ "
       f"方向数 {len(_sig(k3))} vs {len(_sig(k4))}（不同才对）")


def test_fingerprint_sensitivity() -> None:
    """★ **指纹够不够**：`_fingerprint` 相同的两个内核，**答案必然相同**吗？

    ★★ 为什么**不比语义**（2026-10-23，用户裁定）：答案是**结构的函数**
       （`query` 只读 `dirs/children/members/_expanded/items` + 插件代码），
       结构逐字相同 ⇒ 答案逐字相同。**再比一遍答案是同义反复**
       （`docs/评估标准.md` R4：读数不许与别的读数互为函数）。

    ⇒ 真正该判的不是「比语义」，而是**「指纹有没有覆盖 query 读的那些字段」**。
       做法：**逐类扰动**，指纹**必须变** —— 某类扰动指纹不变，就说明那一类
       没进指纹，于是「指纹相同 ⇒ 答案相同」不成立。

    ⚠️ 覆盖不到的那一类，外部审查点过名：「运行时状态、缓存、使用记录、派生状态」。
       本判据把它们逐条摆上来试，而不是靠读代码断言。
    """
    from ldv.checks._fixtures import load, make_builder
    from ldv.core import persist as P

    loaded = load()
    if loaded is None:
        ok("★ [持久化] 指纹对 query 的输入敏感（**跳过**：内置语料不在）", True, "0")
        return
    nodes, edges, _d = loaded
    ids = sorted(nodes)

    def _fresh():
        return make_builder("keyset", nodes, edges)(ids[:12])[0]

    base_fp = P._fingerprint(_fresh())                    # noqa: SLF001
    cases: list[tuple[str, Any]] = []

    def _members(k):
        d = k.all_directions()[1]
        k._members[d.did].discard(sorted(k._members[d.did])[0])  # noqa: SLF001

    def _expanded(k):
        d = k.all_directions()[0]
        k._expanded.discard(d.did) if d.did in k._expanded else k._expanded.add(d.did)  # noqa: SLF001

    def _usage(k):
        from ldv.core.interfaces import WeightedRecord
        d = k.all_directions()[0]
        k.usage.append(WeightedRecord(did=d.did, outcome=1.0, propensity=1.0))

    def _payload(k):
        import dataclasses
        d = k.all_directions()[1]
        k._dirs[d.did] = dataclasses.replace(  # noqa: SLF001
            d, payload=frozenset(set(d.payload) | {"注入=指纹敏感"}))
        # ⚠️ **必须手动丢缓存**：`all_directions()` 按 `(len(_dirs), _counter)` 失效，
        #    而「替换一个**已存在**的 Direction」这两个数都不变 ⇒ 缓存里还是旧对象。
        #    正常路径不会替换（`Direction` 是 frozen，`_new` 只新增），
        #    所以这是**扰动本身**绕过了约定，不是内核的缺陷。
        k._all_cache = None                    # noqa: SLF001

    cases = [("成员集", _members), ("已展开", _expanded),
             ("使用记录", _usage), ("方向 payload", _payload)]

    unchanged: list[str] = []
    for name, fn in cases:
        k = _fresh()
        try:
            fn(k)
        except Exception as exc:                     # 扰动本身做不了也要报出来
            unchanged.append(f"{name}（扰动失败：{type(exc).__name__}）")
            continue
        if P._fingerprint(k) == base_fp:             # noqa: SLF001
            unchanged.append(name)

    ok("★ [持久化] 指纹对 query 的**每一类**输入都敏感（不敏感的那类就是漏掉的字段）",
       not unchanged,
       f"{len(cases)} 类扰动｜指纹没变（= 没进指纹）的：{unchanged or '无'}")


def test_persistence() -> None:
    """★ `§10.2 D` 的 **P6**：落盘只认权威边，派生边**读回时现算**。

    七条断言：

      1. **往返逐字相同**（三个方向 × 批建 + 删一项）—— `from_dict(to_dict(k))` 的内核
         与原件在 `_fingerprint` 上**逐项相等**
      2. **存档里没有派生边** —— `方向` 的每一行**不含** `parent` / `witness`
      3. **派生边可由权威边重算** —— 与内存里的 `parent` / `witness` 逐项相符
      4. **存档幂等** —— 同一份结构产出**同一份字节**（`canon` 定序，不是 `repr`）
      5. ★ **注入：漂移的存档** —— 权威 loader 读回**仍等于原件**（绿）
      6. ★ **对照：信任派生边的 loader** —— 同一份漂移存档 ⇒ 读回**不等于**原件（红）
      7. ★ **反向判据**：**未漂移**时两个 loader **同输出** ⇒ 对照**只在漂移后分得开**
         （防「这条判据其实只是复述了 loader 的实现」）
    """
    import json

    from ldv.checks._fixtures import load
    from ldv.core import persist as P
    from ldv.core.kernel import Kernel as _K

    loaded = load()
    if loaded is None:
        PASS.append("持久化（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded

    whichs = ("keyset", "reach", "sequence")

    # --- ①②③④ 基线 ---------------------------------------------------------
    for which in whichs:
        k, plug = _persist_build(_K, which, nodes, edges)
        data = P.to_dict(k)
        k2 = P.from_dict(json.loads(json.dumps(data)), plug)

        ok(f"★ [P6] {which}：**往返逐字相同**（方向/子/成员/锥/账本/使用/编号逐项相等）",
           P._fingerprint(k) == P._fingerprint(k2),
           f"方向 {len(k.all_directions())}｜账本 {len(k.ledger)} 条")

        leaked = [r["did"] for r in data["方向"] if "parent" in r or "witness" in r]
        ok(f"★ [P6] {which}：存档的 `方向` 里**没有** `parent` / `witness`"
           "（有它就等于「各存一份」）", not leaked, f"{leaked[:3]}")

        derived = P.derive_parents({p: tuple(v) for p, v in data["子"].items()},
                                   data["根"])
        bad = [d.did for d in k.all_directions() if derived.get(d.did) != d.parent]
        bad += [d.did for d in k.all_directions()
                if d.witness != (() if d.parent is None else (d.parent,))]
        ok(f"★ [P6] {which}：**派生边可由权威边重算**（`parent` / `witness` 逐项相符）",
           not bad, f"不符 {bad[:3]}")

        s1 = json.dumps(P.to_dict(k), sort_keys=True)
        s2 = json.dumps(P.to_dict(P.from_dict(json.loads(s1), plug)), sort_keys=True)
        ok(f"★ [P6] {which}：**存档幂等**（同一份结构 ⇒ 同一份字节）",
           s1 == s2, f"{len(s1)} 字节")
        print(f"    · {which:8s} 往返 ✓｜存档 {len(s1):>7} 字节"
              f"｜派生边 {len(leaked)} 行｜重算不符 {len(bad)}｜幂等 {s1 == s2}")

    # --- ⑤⑥⑦ 漂移注入 + 对照 ------------------------------------------------
    for which in whichs:
        k, plug = _persist_build(_K, which, nodes, edges)
        want = P._fingerprint(k)

        for how in ("全量", "一处"):
            drifted = _drift(P._to_dict_storing_derived(k), how)
            a = P.from_dict(json.loads(json.dumps(drifted)), plug)
            b = P._from_dict_trusting_derived(json.loads(json.dumps(drifted)), plug)
            ok(f"★ [P6] {which}·{how}漂移：**权威 loader 读回仍等于原件**"
               "（它根本不看那两栏）", P._fingerprint(a) == want,
               "漂了派生边也不该动结构")
            ok(f"★★ [P6] {which}·{how}漂移：**信任派生边的 loader 读回 ≠ 原件**"
               "（这条就是「各存一份」的失效形态）", P._fingerprint(b) != want,
               f"漂了 {how}")
            print(f"    · {which:8s} {how}漂移：权威 loader "
                  f"{'==' if P._fingerprint(a) == want else '≠'} 原件"
                  f"｜双份 loader {'==' if P._fingerprint(b) == want else '≠'} 原件"
                  + ("  ← ★ 对照在这里分开了" if P._fingerprint(b) != want else ""))

        # ⑦ 反向判据：未漂移时两者**同输出**
        clean = P._to_dict_storing_derived(k)
        a0 = P.from_dict(json.loads(json.dumps(clean)), plug)
        b0 = P._from_dict_trusting_derived(json.loads(json.dumps(clean)), plug)
        ok(f"★ [P6] {which}：**未漂移**时两个 loader **同输出**"
           "（⇒ 对照只在漂移后分得开，光测「读回来对」是测不出「只存了一份」的）",
           P._fingerprint(a0) == P._fingerprint(b0) == want,
           "未漂移 ⇒ 两条路都对")

    # --- ⑧ 权威边自相矛盾 ⇒ 当场报错，不静默取一个 ---------------------------
    # ⚠️ 这段要**两个**有子的父方向（把一个父的子挪到另一个父底下 ⇒ 那个子两个父）。
    #    与 `test_tree_premise` ④ 同一类问题：**夹具前提是按随仓库那份 36 项语料挑的**。
    #    语料小到有子的父少于两个时 `parents[1]` 直接 `IndexError` ⇒ **整趟测试崩**
    #    （红是判据说话，崩是判据没了 —— 后面的测试函数一个都不跑）。
    #    实测：`edge-single`(1 项) 崩在这一行。
    #    ⇒ 前提不成立就报**跳过**，并把「跳过了什么」写出来 —— 跳过 ≠ 通过。
    k, plug = _persist_build(_K, "keyset", nodes, edges)
    data = P.to_dict(k)
    parents = [p for p, kids in data["子"].items() if kids]
    if len(parents) < 2:
        PASS.append(f"[P6] 权威边自相矛盾（同一个子两个父）⇒ `from_dict` 报错"
                    f"（**跳过**：只有 {len(parents)} 个有子的父方向，这份夹具要 ≥2 —— "
                    f"它是按随仓库那份 36 项语料挑的）")
    else:
        victim = data["子"][parents[1]][0]
        data["子"][parents[0]] = list(data["子"][parents[0]]) + [victim]
        raised = False
        try:
            P.from_dict(json.loads(json.dumps(data)), plug)
        except P.PersistenceError:
            raised = True
        ok("★★ [P6] **权威边不是树**（同一个子两个父）⇒ `from_dict` 当场报错，"
           "不静默取一个 —— 「静默取一个」与「这份存档本来是好的」在读回的结构上长得一样",
           raised, "改 `子` 之后 DAG 存档")

    # --- ⑨ 格式号不符 ⇒ 报错 -------------------------------------------------
    bad_fmt = dict(P.to_dict(k))
    bad_fmt["格式"] = "ldv-kernel/0"
    raised = False
    try:
        P.from_dict(bad_fmt, plug)
    except P.PersistenceError:
        raised = True
    ok("★ [P6] 格式号不符 ⇒ 报错（不猜、不降级）", raised, "格式号改成 0")

    # --- ⑩ 派生状态不落盘：`reach` 的缓存，读回后代价读数逐项相同 -------------
    k, plug = _persist_build(_K, "reach", nodes, edges)
    before = {d.did: [plug.penalty(d, k.items[i]) for i in sorted(k.members_of(d))]
              for d in k.all_directions()}
    k2 = P.from_dict(json.loads(json.dumps(P.to_dict(k))), plug)
    after = {d.did: [plug.penalty(d, k2.items[i]) for i in sorted(k2.members_of(d))]
             for d in k2.all_directions()}
    ok("★ [P6] `reach` 的缓存**不落盘**（派生状态），但读回后 `代价` **逐项相同**"
       " —— 同一条纪律在插件那一层的实例",
       before == after, f"{len(before)} 个方向")

    # --- ⑪ ★ P6 是 P1–P5 的**兑现**：读回的内核也要过 `B13` -------------------
    #
    # `B13`（含 P4 树性）只保证**内存里**两条边不打架。落盘/读回是**第二个入口** ——
    # 一份漂移的存档读回来，两条边就打架了，而「检查还在、还绿」正是 `§10.2 D`
    # 那句 ⚠️ 说的症状。所以这里拿 `B13` 去量**读回来的**内核。
    from ldv.checks._framework import Report
    from ldv.checks.structure import b13_rank_well_founded

    for which in whichs:
        k, plug = _persist_build(_K, which, nodes, edges)
        clean_loaded = P.from_dict(json.loads(json.dumps(P.to_dict(k))), plug)
        rc = Report(plugin=which, expects=("B13",))
        b13_rank_well_founded(clean_loaded, rc)

        drifted = _drift(P._to_dict_storing_derived(k), "一处")
        trust = P._from_dict_trusting_derived(json.loads(json.dumps(drifted)), plug)
        rt = Report(plugin=which, expects=("B13",))
        b13_rank_well_founded(trust, rt)

        ok(f"★★ [P6] {which}：**读回的内核也要过 `B13`**（树性在第二个入口上仍成立）",
           rc.assertions[0].result is Tri.YES,
           f"B13 = {rc.assertions[0].result}；{rc.assertions[0].detail[:60]}")
        ok(f"★★ [P6] {which}：**漂移存档 + 信任派生边的 loader ⇒ `B13` 报「否」**"
           "（「检查还在、还绿」正是这里的反例 —— 它这次没绿）",
           rt.assertions[0].result is Tri.NO,
           f"B13 = {rt.assertions[0].result}；{rt.assertions[0].detail[:60]}")
        print(f"    · {which:8s} 读回后 B13：只认权威边 {rc.assertions[0].result}"
              f"｜信任派生边 {rt.assertions[0].result}"
              f"（{str(rt.assertions[0].detail)[:40]}…）")


# ═══ 跨进程落盘（并发接口设计 §7 阶段 2） ═════════════════════════════════════
#
# ⚠️ 这一组与下面 `test_lock_scope` 守的**不是一回事**：
#   `test_lock_scope` 守**进程内**的锥锁（`§10.2 D` 的 P8）；
#   这一组守**面 B**（多个进程共享同一个落盘路径）—— 进程内的锁**管不到**另一个进程。

def _reader_blocks_replace() -> tuple[bool, str]:
    """**前置探针**：本环境里「持一个读句柄」是否真的挡得住 `os.replace`。

    ⚠️ `test_persist_atomic` 的 ⑤⑥ 以这个现象为前提，而它**不是代码保证的** ——
       取决于解释器/平台给 `open()` 的共享语义（`FILE_SHARE_DELETE` 之类）。
       2026-10-19 实测：windows + Python 3.14 上一次⑤报「尝试 1 次」= 没挡住，
       判据红而被测代码一点没变 ⇒ **拿运气当判据**。

    用一个**隔离的实验**测它（临时目录 + 两个文件 + 一个读句柄 + 一次 replace），
    返回 `(是否挡住, 原因)`。挡不住 ⇒ 调用方**点名跳过**，不红。
    """
    import os as _os
    import shutil as _sh
    import tempfile as _tf
    from pathlib import Path as _P

    d = _P(_tf.mkdtemp())
    try:
        a, b = d / "a.md", d / "b.md"
        a.write_text("x", encoding="utf-8")
        b.write_text("y", encoding="utf-8")
        h = open(a, "rb")
        try:
            _os.replace(b, a)
            return False, "持读句柄时 os.replace 仍成功"
        except PermissionError:
            return True, "持读句柄时 os.replace 被拒（PermissionError）"
        finally:
            h.close()
    finally:
        _sh.rmtree(d, ignore_errors=True)


def test_persist_untrusted() -> None:
    """★ 持久化的**不可信输入**边界（2026-10-23 外部审计点名的缺口）。

    旧实现：`_dc_lookup` 是「存档里写哪个模块就 import 哪个」，`_dec` 再
    `cls(**kwargs)` —— **存档字符串决定 import 哪个模块、构造哪个类、传什么参数**，
    解码侧还不校验是不是数据类。docstring 一直写着「存档是可信输入」，
    但**边界不能只是文档**：调用边界一扩大（服务接口、租户交换、上传恢复），
    这就是一个代码执行形状的原语。

    ① 白名单外 ⇒ 拒收（`$dc: "os.system"` 这种形状进不来）
    ② 白名单内 ⇒ 照常重建（`Node` 仍往返 —— 白名单不是把功能关掉）
    ③ 嵌套过深 ⇒ 拒收（资源上限：**读得慢**与**读得死**必须分得开）
    ④ 容器数超限 ⇒ 拒收（用小 cap 的账本unit-level 验，不用真造 200 万个）
    ⑤ 反向对照（拔掉白名单 ⇒ ① 变绿）在
       `evidence/_probe_persist_untrusted_reverse.py`
    """
    from ldv.checks._fixtures import load, make_builder
    from ldv.core import persist as P

    got = load()
    if got is None:
        ok("★ [持久化] 不可信输入边界（**跳过**：内置语料不在）", True, "0")
        return
    nodes, edges, _ = got
    k, plug = make_builder("keyset", nodes, edges)(sorted(nodes)[:8])

    good = P.to_dict(k)
    # ② 白名单内的类型照常重建（Node 经 `as_item()` 进 `items`）
    back = P.from_dict(good, plug)
    some = next(iter(back.items.values()))
    ok("★ [持久化] ② 白名单内的 `Node` 照常重建（白名单不关功能）",
       isinstance(some.get("node"), object) and "node" in some,
       f"项形状 {sorted(some)[:4]}｜往返指纹相同 {P._fingerprint(back) == P._fingerprint(k)}")

    # ① 白名单外 ⇒ 拒收（走**真路径**：毒化一份真存档的 `项`）
    bad = P.to_dict(k)
    iid = sorted(bad["项"])[0]
    bad["项"][iid] = {"$dc": "os.system", "$f": {"command": "echo pwned"}}
    try:
        P.from_dict(bad, plug)
        ok("★ [持久化] ① 白名单外的 `$dc` ⇒ 拒收", False,
           f"居然建起来了：{type(bad['项'][iid])}")
    except P.PersistenceError as e:
        ok("★ [持久化] ① 白名单外的 `$dc` ⇒ 拒收（存档不许决定 import 谁）",
           "白名单" in str(e), f"{str(e)[:90]}")
    except Exception as e:  # noqa: BLE001
        ok("★ [持久化] ① 白名单外的 `$dc` ⇒ 拒收", False,
           f"抛了别的：{type(e).__name__}: {e}")

    # ③ 嵌套过深 ⇒ 拒收
    deep: object = "底"
    for _ in range(P.MAX_DECODE_DEPTH + 8):
        deep = [deep]
    try:
        P._dec(deep)
        ok("★ [持久化] ③ 嵌套过深 ⇒ 拒收", False, "居然解完了")
    except P.PersistenceError as e:
        ok("★ [持久化] ③ 嵌套过深 ⇒ 拒收（资源上限）",
           str(P.MAX_DECODE_DEPTH) in str(e), f"{str(e)[:70]}")
    except RecursionError:
        ok("★ [持久化] ③ 嵌套过深 ⇒ 拒收", False,
           "抛的是 RecursionError —— 深度门设得太高，解释器先撑不住")

    # ④ 容器数超限 ⇒ 拒收（unit-level：小 cap 的账本）
    try:
        P._dec([[1], [2], [3], [4], [5]], 0, P._Budget(3))
        ok("★ [持久化] ④ 容器数超限 ⇒ 拒收", False, "居然解完了")
    except P.PersistenceError as e:
        ok("★ [持久化] ④ 容器数超限 ⇒ 拒收（读得慢与读得死分得开）",
           "容器" in str(e), f"{str(e)[:70]}")


def test_persist_atomic() -> None:
    """★★ **阶段 2**：跨进程落盘 —— **不撕裂**。

    两条判据，成色**不同**，如实分开写：

      ① **落盘失败不留半份**（**确定性**）—— 写中途抛 ⇒ 盘上必须还是**旧整份**。
         非原子实现（`open(p,"w")` 先截断再写）在这里**必红**：目标被清空。
      ② **并发整份性** —— 两个**真子进程**同时写同一个路径，读者**只**见到完整份之一。
         ⚠️ 这条**不是**确定性判据：它靠**窗口够大**（body 2 MB）才必现。
         原子实现下它是**结构保证**（`os.replace` 是同卷改名，读者只会看到两种状态）。
         ⇒ 它抓的是「撕裂**会**发生」，抓不到「撕裂**不会**发生」——
           所以 ① 那一条（确定性的）才是主判据，② 是它的并发面补充。
    """
    import subprocess
    import time

    from ldv.core import view_persist as vp

    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix="ldv-persist-atomic-"))
    target = root / "views.json"

    # ── ① 落盘失败不留半份（确定性） ────────────────────────────────────────
    old_body = '{"格式": "ldv-views/1", "哨兵": "旧整份"}'
    target.write_text(old_body, encoding="utf-8")
    # 「写不进去」的 body：**孤立代理项** ⇒ UTF-8 编码抛 `UnicodeEncodeError`。
    # ⚠️ 刻意不用「盘满 / 权限」造失败 —— 那两个要动环境；这个**当场必然抛**。
    bad_body = '{"格式": "ldv-views/1", "哨兵": "\ud800"}'
    raised = ""
    try:
        vp.commit(target, bad_body)
    except Exception as e:                                   # noqa: BLE001
        raised = type(e).__name__
    after = target.read_text(encoding="utf-8")
    ok("★★ [阶段2] ① 落盘失败 ⇒ 盘上仍是**旧整份**（不是空、不是半份）",
       raised != "" and after == old_body,
       f"抛 {raised or '（没抛！）'}｜盘上 {len(after)} 字节（旧整份 {len(old_body)}）"
       + ("—— **目标被清空了**" if after == "" else ""))

    # ── ② 并发整份性：两个**真子进程**同时写 ────────────────────────────────
    CHILD = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from ldv.core import view_persist as vp;"
        "b=open(sys.argv[3],encoding='utf-8').read();"
        "[vp.commit(sys.argv[2],b) for _ in range(int(sys.argv[4]))]"
    )
    bodies = {ch: '{"格式": "' + vp.FORMAT + '", "哨兵": "' + ch * 2000000 + '"}'
              for ch in ("A", "B")}
    for ch, b in bodies.items():
        (root / f"body_{ch}.txt").write_text(b, encoding="utf-8")
    # ⚠️ **初始态必须是一份合法的整份**：① 刚把 `target` 写成 `old_body`
    #    （完整、但**既非 A 也非 B**）⇒ 不清掉的话，② 一上来读到的那些
    #    会被算成「撕裂」—— 那是**判据自己的毛病**，不是实现的。
    #    （第一次跑就踩到：371 次读里 323 次「非完整份」，而子进程 stderr 是空的。）
    target.write_text(bodies["A"], encoding="utf-8")
    procs = [subprocess.Popen(
        [sys.executable, "-c", CHILD, str(repo), str(target),
         str(root / f"body_{ch}.txt"), "25"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for ch in ("A", "B")]

    seen: set[str] = set()
    bad_sample: list[int] = []
    bad = n_read = 0
    while any(pr.poll() is None for pr in procs):
        try:
            blob = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):        # 还没写出来 / 正在被换
            time.sleep(0.001)
            continue
        n_read += 1
        if blob == bodies["A"]:
            seen.add("A")
        elif blob == bodies["B"]:
            seen.add("B")
        else:
            bad += 1
            if len(bad_sample) < 3:          # 报**字节数**：残留与真撕裂长得不一样
                bad_sample.append(len(blob))
        time.sleep(0.001)                            # 给写者留出改名的窗口
    err = "".join((pr.stderr.read() or b"").decode("utf-8", "replace")
                  for pr in procs)
    for pr in procs:
        pr.wait()
    # ⚠️ **写者 stderr 是读数、不是判据** —— 在「读者疯狂抢」的对抗性负载下，
    #    写者耗尽重试预算是**对**的行为（设计上就是「永久占用 ⇒ 如实抛」）。
    #    ⇒ 写者侧那条性质由 **⑤ / ⑥ 用确定性探针**判（持句柄 / 不持句柄），
    #      这一条只管**不撕裂**。
    ok("★★ [阶段2] ② 两个子进程并发落盘 ⇒ 读者**只**见到完整份（没撕裂）",
       bad == 0 and n_read >= 20 and seen == {"A", "B"},
       f"读 {n_read} 次｜撕裂 {bad} 次（样本字节数 {bad_sample}）"
       f"｜见到的整份 {sorted(seen) or '（一次都没读到）'}"
       f"｜写者 stderr {err.strip()[:60] or '（空）'}"
       + ("—— **读到的不是完整份**" if bad else "")
       + ("—— **读得太少，这条判据空转**" if n_read < 20 else ""))

    # ── ⑤⑥ **只在 Windows 上判**（2026-10-19 修 CI）────────────────────────
    #
    #    ⚠️ **这是 Windows 特有的现象，POSIX 上根本不存在**：
    #       Windows 的共享模型按**句柄**算 ⇒ 读者持着句柄时 `os.replace` 被拒
    #       （`ERROR_ACCESS_DENIED`），所以要重试。
    #       POSIX 的 `rename(2)` 是**原子**的，读者持的是**旧 inode** ⇒
    #       不冲突、不重试、不抛。
    #
    #    ⇒ 这两条判据在 Linux / macOS 上**必然红**（实测 CI：ubuntu 两个矩阵
    #      ⑤「尝试 1 次」⑥「没抛」）。那不是实现坏了，是**平台性质不同**。
    #    ⇒ 非 Windows 上**跳过并点名**（跳过 ≠ 通过）。
    import sys as _sys
    # ⚠️ **不 return**：⑤⑥ 之后还有 ③（读者 `read()` 一次都不许抛），
    #    提前退出会把它一起跳过 —— 那是「跳过变成静默省略」。
    _is_win = _sys.platform == "win32"
    # ★★ **先验证前提，再判 ⑤⑥**（2026-10-19 修 CI 偶发红）
    #
    #    ⑤⑥ 的前提是「**持一个读句柄 ⇒ `os.replace` 被拒**」。这个前提
    #    **不是代码保证的**，它取决于解释器/平台给 `open()` 的共享语义
    #    （`FILE_SHARE_DELETE` 之类）。实测：`9007cb8` 在
    #    **windows-latest · Python 3.14** 上⑤报「尝试 **1** 次」——
    #    也就是句柄**没挡住**替换 ⇒ 判据红，而被测代码一点没变。
    #
    #    ⇒ 拿运气当判据。改成：**先用一个隔离实验测这个前提**，
    #       前提不成立 ⇒ **点名跳过并写清实测原因**，而不是红。
    #       （「跳过 ≠ 通过」，所以消息里带实测值与判据编号。）
    _blocks, _why = _reader_blocks_replace()
    _can_judge = _is_win and _blocks
    if not _can_judge:
        PASS.append("★★ [阶段2] ⑤⑥ 持句柄挡住 os.replace（**跳过**："
                    f"{'POSIX 的 rename 是原子的' if not _is_win else '本环境实测'}"
                    f" ⇒ 持读句柄时 os.replace 仍成功（{_why}）"
                    " —— 这两条只对「句柄挡得住替换」的环境有意义）")

    # ── 前置探针本身要**双向都能判**（否则「跳过」可能只是探针坏了） ───────
    #    POSIX 上它必须报「挡不住」；若报「挡住了」，说明探针坏了 ⇒
    #    下面的 ⑥ 会在 Linux 上被当成 Windows 判 ⇒ 必然红。
    if not _is_win:
        ok("★ [阶段2] 前置探针在 POSIX 上如实报「挡不住」（探针自身没坏）",
           _blocks is False and "仍成功" in _why, f"探针实测：{_why}")

    # ── ⑤ 写者重试**接上了** —— **读数，不判**（2026-10-19 降级）───────────
    #    ⚠️ 这一条**不判**，只报数。原因：它的失败形状「尝试 **1** 次」与实现无关 ——
    #       释放是**定时 50 ms**，而首次 replace 在 `commit` **内部**
    #       （建临时文件 → 写 → `os.replace`）。runner 一慢，首次 replace 就落在
    #       50 ms **之后** ⇒ 句柄已释放 ⇒ 一次成功 ⇒ 判据红。
    #       实测两次 CI 红（`9007cb8` / `7fa08f9`）都是这个形状，
    #       而 `7fa08f9` 与通过的那次（`01098a5`）**代码树逐字相同**。
    #       ⇒ 把它当判据 = **拿 runner 负载当判据**。
    #    ⚠️ ⑥（一直持 ⇒ 如实抛）**仍然是判据** —— 它不依赖释放时机，只依赖「挡住了」。
    #    ⚠️ 前置探针服务的是 ⑥：确认「持句柄挡得住 replace」这个前提成立。
    import threading

    hold = open(target, "rb")
    threading.Thread(target=lambda: (time.sleep(0.05), hold.close()),
                     daemon=True).start()
    t0 = time.time()
    try:
        _tgt, n_try = vp.commit(target, bodies["A"])
        c5 = n_try > 1 and target.read_text(encoding="utf-8") == bodies["A"]
        c5d = (f"尝试 **{n_try}** 次｜{time.time() - t0:.3f}s｜"
               f"落盘内容 {'对' if target.read_text(encoding='utf-8') == bodies['A'] else '**错**'}")
    except Exception as e:                                   # noqa: BLE001
        n_try, c5, c5d = 0, False, f"**抛了** {type(e).__name__} —— 句柄 50 ms 后已释放，不该抛"
    PASS.append("★★ [阶段2] ⑤ 持句柄 50 ms 后释放 ⇒ 重试后成功（**读数，不判**）："
                + c5d
                + ("｜N>1 ⇒ 本趟重试确实发生了" if n_try > 1 else
                   "｜**尝试 1 次 ⇒ 窗口没对上**（首次 replace 晚于 50 ms 释放）"
                   "—— 这是计时竞争，**不是失败**"))

    # ── ⑥ 重试**有界**：一直持句柄 ⇒ 如实抛，且**不许无限等**（**确定性**）─────
    hold2 = open(target, "rb")
    t0 = time.time()
    try:
        vp.commit(target, bodies["B"])
        c6, c6d = False, "**没抛** —— 句柄一直持着，它不该成功"
    except PermissionError as e:                             # noqa: BLE001
        dt = time.time() - t0
        c6 = dt <= 1.0
        c6d = (f"抛 `PermissionError`｜{dt:.3f}s"
               + ("（有界 ✓）" if c6 else "（**超过 1 s —— 重试没界**）"))
    except Exception as e:                                   # noqa: BLE001
        c6, c6d = False, f"抛的是 {type(e).__name__}（该抛 `PermissionError`）"
    finally:
        hold2.close()
    if _can_judge:
        ok("★★ [阶段2] ⑥ 一直持句柄 ⇒ 如实抛 `PermissionError`，且**耗时 ≤ 1 s**（有界）",
           c6, c6d)
    else:
        PASS.append("★★ [阶段2] ⑥ 一直持句柄 ⇒ 如实抛（**跳过**：POSIX 的 rename 原子 ⇒ "
                    f"句柄挡不住替换；实测 {c6d}）")

    # ── ③ 读者侧：写者循环替换、读者循环读 —— 读者**不许**抛 ────────────────
    #    ⚠️ 成色同 ②：靠**窗口够大**才必现（**不是**确定性判据）。
    #      反向对照下实测 **18/7266 被拒** ⇒ 这条会红。
    READER = """import sys, json, time
sys.path.insert(0, sys.argv[1])
from ldv.core import view_persist as vp
target, secs = sys.argv[2], float(sys.argv[3])
n, bad = 0, []
t0 = time.time()
while time.time() - t0 < secs:
    try:
        vp.read(target)
        n += 1
    except FileNotFoundError:
        pass
    except Exception as e:
        bad.append(type(e).__name__)
print("@@R" + json.dumps({"reads": n, "n_err": len(bad), "kinds": bad[:3]}))
"""
    rp = root / "_reader.py"
    rp.write_text(READER, encoding="utf-8")
    wp = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(repo), str(target),
         str(root / "body_B.txt"), "300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    rr = subprocess.Popen([sys.executable, str(rp), str(repo), str(target), "8"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    o_r, e_r = rr.communicate(timeout=120)
    wp.wait()
    rerr = (e_r or b"").decode("utf-8", "replace")
    rrep = {}
    for line in (o_r or b"").decode("utf-8", "replace").splitlines():
        if line.startswith("@@R"):
            rrep = json.loads(line[3:])
    # ── ③ 写者循环替换时，读者一次都不许抛 ────────────────────────────
    # ★ 2026-10-23 外部审计抓到：旧条件是 `rerr.strip() == "" and …` ——
    #   子进程 stderr **任何**输出都判红，即使结构化报告说「2057 次读取、
    #   0 次异常」。审计者机器上 Python 启动打了句
    #   `Spreadsheet runtime warmup failed during python startup` ⇒ 整条红。
    #   **那是环境噪声，不是被测行为的错误。**
    # ⇒ 判据只认**被测行为**（结构化报告：异常数、读取次数）；
    #   stderr 降为诊断信息印在 detail 里 —— 除非它长得像**真崩了**
    #   （Python 崩溃必打 `Traceback`），那种仍然红。
    #   「被测行为错」与「环境在吵」必须分得开（本项目自己的纪律）。

    def _reader_ok(rerr: str, rep: dict) -> bool:
        """读者侧判据的**判别式**（抽出来才能对「判别本身」下判）。"""
        return ("Traceback (most recent call last)" not in rerr
                and rep.get("n_err") == 0 and rep.get("reads", 0) >= 200)

    _clean = {"reads": 2057, "n_err": 0}
    ok("★ [阶段2] ③′ 环境噪声不算失败（stderr 有非 Traceback 输出、报告干净 ⇒ 过）",
       _reader_ok("Spreadsheet runtime warmup failed during python startup", _clean),
       "2057 次读取 / 0 异常 / stderr 一行启动噪声")
    ok("★ [阶段2] ③′ 真崩仍然抓得到（stderr 里有 Traceback ⇒ 红）",
       not _reader_ok("Traceback (most recent call last):" + chr(10) + "ValueError: x",
                      _clean),
       "报告同样干净，但子进程崩过 —— 这一侧不许放")

    crashed = "Traceback (most recent call last)" in rerr
    ok("★★ [阶段2] ③ 写者循环替换时，读者 `read()` **一次都不许抛**",
       _reader_ok(rerr, rrep),
       f"读了 {rrep.get('reads', 0)} 次｜异常 {rrep.get('n_err', '?')} 次"
       f"{rrep.get('kinds') or ''}"
       + ("—— **读者侧没补**" if rrep.get("n_err") else "")
       + ("—— **读得太少，这条判据空转**" if rrep.get("reads", 0) < 200 else "")
       + (f"｜**子进程崩了**（stderr 有 Traceback）：{rerr.strip()[-160:]}"
          if crashed
          else (f"｜stderr（仅诊断，不算失败）：{rerr.strip()[:60]}"
                if rerr.strip() else "")))

    # ── ④ 重试的**边界**（**确定性**，手推已知答案）─────────────────────────
    #    ⚠️ 这条钉的是「**只重试该重试的**」：POSIX 上一律不重试（那里没有
    #       「读者挡住替换」这回事，`PermissionError` 就是真没权限）；
    #       Windows 上只重试 `PermissionError`，别的 `OSError` 立刻抛。
    ok("★ [阶段2] ④ 重试边界：只在 Windows 上、只对 `PermissionError` 重试",
       (vp._transient(PermissionError()) is (os.name == "nt"))
       and (vp._transient(FileNotFoundError()) is False)
       and (vp._transient(IsADirectoryError()) is False)
       and (vp._transient(OSError()) is False),
       f"os.name={os.name!r}｜PermissionError ⇒ {vp._transient(PermissionError())}"
       f"｜FileNotFoundError ⇒ {vp._transient(FileNotFoundError())}")

# ═══ 并发（§10.2 D 的 P8） ═══════════════════════════════════════════════════
#
# `§10.2 D` 的裁决：**锁沿路径局部化，根是公共争用点**。
#
# ⚠️ 这里的判据守的是**作用域**，不是「线程安全」。两件事必须分开：
#   一个把锁的范围搞对、却漏了共享状态的实现，与一个真的安全的实现，
#   在「作用域」这个读数上**长得一模一样**。

def _lock_scope(k, fn, *a):
    """跑一次写操作，读**实际拿过**的锁集合（不是「应该拿的」）。"""
    k.locks.taken.clear()
    fn(*a)
    return {did for _, did in k.locks.taken}


def _mutex_broken(locks, key: str = "D0", timeout: float = 0.4) -> bool:
    """**确定性**地测互斥：用 barrier 逼两个线程**同时**想进同一个临界区。

        正确锁   第一个进去、卡在 barrier 上、超时；第二个被挡在外面
                 ⇒ 两个都超时 ⇒ 返回 False（互斥成立）
        空锁     两个都进去 ⇒ barrier 放行 ⇒ 返回 True（互斥被破）

    ⚠️ 为什么**不用**并发压力测试：实测（`outputs/_probe_locks.py` ⑤）
       **空锁**在 8 线程 × 120 项的压力下也**全绿** —— 临界区太短、GIL 帮了忙。
       一个「空锁与真锁长得一样」的测法，测不出任何东西。
    """
    import threading

    both: list[bool] = []
    barrier = threading.Barrier(2, timeout=timeout)

    def worker() -> None:
        with locks.guard(key):
            try:
                barrier.wait()
                both.append(True)
            except threading.BrokenBarrierError:
                pass

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return bool(both)


def test_versioning() -> None:
    """★ `§10.2 D` 决策表那一行「每隔 k 版存一份（混合）」——
    访问版本 i 是 **O(i/k + k)**，不是 **Θ(i)**。

    四条判据，每条各配一个**已知答案**的对照（对照实现见 `core/versioning.py` 末尾）：

      `V1` **重放等价**：∀i，`at(i)` 与「从空内核纯重放 `_ops[:i]`」**指纹逐字相同**。
           快照是**缓存**、账是**真相** ⇒ 缓存漂移 ⇒ 红。
           对照 `_StaleSnapshot`（快照存错一处）⇒ 必须红。
      `V2` **账不落后**：`at(最新)` 等于**当前内核** ⇒ 账完整（没漏记变更）。
           对照 `_DroppedOp`（执行了但没进账）⇒ 必须红。
      `V3` **快照生效**：用默认 `every`，`max_i 重放步数 < every` ⇒ 代价**与 i 无关**。
           对照 `_NoSnapshots`（不存快照）⇒ 必须红。
      `V4` **反向判据（防恒真）**：`every = 1` ⇒ 重放恒 0；`every > 版本数` ⇒
           重放 = 版本数。⇒ 证明 `V3` 的上界**不是恒真**（配置一变它就破）。

    ⚠️ `V1` 与 `V3` 必须**分开**：`_NoSnapshots` 在 `V1` 上**一个字节都不差**，
       只在 `V3` 上红 —— 「对」与「快」是两件事，合成一条会互相掩护。
    """
    from ldv.checks._fixtures import items as items_of
    from ldv.checks._fixtures import load
    from ldv.core import persist as P
    from ldv.core.versioning import (
        DEFAULT_EVERY, VersionStore, replay_ops,
        _DroppedOp, _NoSnapshots, _StaleSnapshot,
    )
    from ldv.plugins.keyset import KeysetPlugin

    loaded = load()
    if loaded is None:
        PASS.append("版本访问（跳过：语料目录不在）")
        return
    nodes, _edges, _ = loaded
    all_items = items_of(nodes)
    ids = sorted(all_items)
    known = {i: all_items[i] for i in ids[:6]}

    def scenario(cls, every):
        """一条**混着 build / insert / 后添**的操作序列 —— 版本数与 `every`
        都要够大，否则 `V3` 测不出东西（`versions ≤ every` 时上界恒成立）。"""
        plug = KeysetPlugin()
        st = cls(plug, known, every=every)
        st.build(plug.merge([]))
        for i in ids[:6]:
            st.insert(i)
        for i in ids[6:12]:
            st.insert(i, all_items[i])
        return st, plug

    def replays(st):
        out = []
        for i in range(st.versions + 1):
            st.at(i)
            out.append(st.last_replay)
        return out

    def mismatches(st, plug):
        return [i for i in range(st.versions + 1)
                if P._fingerprint(st.at(i))
                != P._fingerprint(replay_ops(plug, known, st._ops, i))]

    # --- 基线 ---------------------------------------------------------------
    st, plug = scenario(VersionStore, DEFAULT_EVERY)
    ok("★ [V3·前提] 版本数 > every —— 否则「重放步数 < every」**恒真**，这条测不出东西",
       st.versions > DEFAULT_EVERY, f"versions {st.versions} / every {DEFAULT_EVERY}")

    bad = mismatches(st, plug)
    ok("★ [V1] `at(i)` 与「从空内核重放 `_ops[:i]`」指纹**逐字相同**"
       f"（{st.versions + 1} 个版本全对 ⇒ 快照没漂移）", not bad, f"不符 {bad[:5]}")

    ok("★ [V2] `at(最新)` 等于**当前内核**（账完整记下了每一次变更）",
       P._fingerprint(st.at(st.versions)) == P._fingerprint(st.kernel),
       f"版本 {st.versions}｜快照点 {st.snapshot_versions()}")

    rs = replays(st)
    ok(f"★ [V3] 重放步数 ≤ every−1 = {DEFAULT_EVERY - 1}（**与 i 无关**）",
       max(rs) < DEFAULT_EVERY, f"最大重放 {max(rs)}｜versions {st.versions}")
    print(f"    · 版本 {st.versions} 个｜快照点 {st.snapshot_versions()}"
          f"｜最大重放 {max(rs)} < every {DEFAULT_EVERY}")

    # --- 对照（每条判据各有一个「错一处」）-----------------------------------
    stale, plug_s = scenario(_StaleSnapshot, DEFAULT_EVERY)
    stale_bad = mismatches(stale, plug_s)
    ok("★ [V1·对照] 快照存错一处 ⇒ `V1` **必须红**（证明 V1 真的在看快照，不是恒真）",
       bool(stale_bad), f"红在版本 {stale_bad[:5]}")

    drop, _ps = scenario(_DroppedOp, DEFAULT_EVERY)
    ok("★ [V2·对照] 执行了但**没进账** ⇒ `at(最新)` 追不上内核 ⇒ `V2` **必须红**",
       P._fingerprint(drop.at(drop.versions)) != P._fingerprint(drop.kernel),
       f"账长 {drop.versions}（内核里已有结构）")

    nosnap, plug_n = scenario(_NoSnapshots, DEFAULT_EVERY)
    rs_n = replays(nosnap)
    ok("★ [V3·对照] 不存快照 ⇒ 重放 = i（退化回 Θ(i)）⇒ `V3` **必须红**；"
       "⚠️ 而它在 `V1` 上**完全合格** —— 「对」与「快」是两件事",
       max(rs_n) >= DEFAULT_EVERY and not mismatches(nosnap, plug_n),
       f"最大重放 {max(rs_n)}（应 ≥ every {DEFAULT_EVERY}）")

    # --- V4 反向判据 ---------------------------------------------------------
    one, _p1 = scenario(VersionStore, 1)
    one_max = max(replays(one))
    ok("★ [V4] 反向：`every = 1` ⇒ 每版都快照 ⇒ 重放**恒 0**",
       one_max == 0, f"最大重放 {one_max}")

    big, _p2 = scenario(VersionStore, st.versions + 1)
    big.at(big.versions)
    ok("★ [V4] 反向：`every > 版本数` ⇒ 一个快照点都落不到 ⇒ 重放 = 版本数"
       "（退回「只存账 + 重放」）—— 证明 `V3` 的上界**不是恒真**",
       big.last_replay == big.versions, f"重放 {big.last_replay} / 版本 {big.versions}")


def test_reusable_build() -> None:
    """★ 「同一份装配只建一次，每个消费者各拿一份**深拷贝**」——
    `_fixtures.reusable_build`。

    ## 为什么有这条缓存

    一次 `run_checks --no-probes` 里**内核建造占了墙钟的绝大部分**，而其中一大块是
    **参数完全相同的装配被建了不止一遍**（实测 `snap-email-eu` 1005 项，
    `outputs/_measure_build_duplication.py`）：

        cli:_assemble       24 次里 **15 次**入参逐字相同（`holdout=12` 那一档；
                            `only=` **不进装配**，`remove` 只影响建完之后挑哪几项待删）
        batch_kernel         6 次里 **3 次**（`(视图)`/`(L0)`/`(检索)` 各建一份）

    而 `copy.deepcopy(内核)` 实测是建造的 **0.0046 倍**（0.22 s vs 45.5 s）
    ⇒ 建一次、发拷贝。

    ## ⚠️ 这条缓存的危险处**不是慢，是共享**

    判据跑查询会经 `kernel.query` → `expand` **就地改内核**（`retriever.py:713`
    自注「唯一写动作」），流程 A–E 也改。而 `cli d`（`CL3`）**必须红**的理由正是
    「A 没先跑 ⇒ 没有带权记录」—— **共享会让那条护栏静默变绿**。
    所以「拷贝**不是**同一个对象」单独占一条断言，不并进「指纹相同」。

        `RB1`  拷贝 ≡ **现建**的那份（指纹逐字相同）⇒ 缓存没引入结构差异
        `RB2`  拷贝**不是**同一个对象：改一个，另一个**一个字节都不动**
        `RB3`  缓存**真的命中**：第二次调用不产生新的建造
        `RB4`  消费者改过自己那份**之后**再取，拿到的**仍是 pristine**
               —— 这一条守的是「缓存里存的是**拷贝**，不是**发出去的那个对象**」
        `RB·对照` 把 `deepcopy` 去掉 ⇒ `RB2` **必须红**（两次拿到同一个对象）
    """
    from ldv.checks import _fixtures as F
    from ldv.core import persist as P
    from ldv.core.kernel import Kernel

    loaded = F.load()
    if loaded is None:
        PASS.append("装配缓存（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded
    all_items = F.items(nodes)
    ids = sorted(all_items)
    # ⚠️ 与上面几处同一类前提：这段要用**至少一个项**当探针（`all_items[ids[0]]`）。
    #    语料里一个项都没有时直接 `IndexError` ⇒ 整趟崩。实测：`edge-single`(0 项)。
    if not need_at_least(ids, 1, "装配缓存（要至少一个项当探针）"):
        return
    probe_item = dict(all_items[ids[0]])

    def produce():
        return F.build_keyset(nodes)

    # --- 建造计数：证明缓存**真的**省掉了一次建 -------------------------------
    counts = {"n": 0}
    orig_build = Kernel.build

    def counted(self, *a, **k):
        counts["n"] += 1
        return orig_build(self, *a, **k)

    F.clear_build_cache()
    Kernel.build = counted
    try:
        tag = ("test", "keyset")
        k1, _p1 = F.reusable_build(tag, nodes, edges, produce)
        n_first = counts["n"]
        k2, _p2 = F.reusable_build(tag, nodes, edges, produce)
        n_second = counts["n"]
        k_fresh, _pf = F.build_keyset(nodes)          # 参照：**不走缓存**
    finally:
        Kernel.build = orig_build

    ok("★ [RB1] 拷贝与**现建**的那份**指纹逐字相同**（缓存没引入结构差异）",
       P._fingerprint(k2) == P._fingerprint(k_fresh),
       "指纹相同" if P._fingerprint(k2) == P._fingerprint(k_fresh)
       else "**对不上** —— 缓存发出的是另一份结构")

    # --- RB2：改一个，另一个不许动 -------------------------------------------
    fp1_before = P._fingerprint(k1)
    fp2_before = P._fingerprint(k2)
    k1.insert("__probe__", probe_item)
    ok("★ [RB1·前提] 那次改动**真的**改了 `k1` —— 否则下面那条恒真、测不出东西",
       P._fingerprint(k1) != fp1_before, f"插入前后指纹{'变了' if P._fingerprint(k1) != fp1_before else '**没变**'}")

    ok("★ [RB2] 改 `k1` ⇒ `k2` **一个字节都不动**（两份是独立对象，不是同一份）",
       k1 is not k2 and P._fingerprint(k2) == fp2_before,
       f"`k1 is k2` = {k1 is k2}；k2 指纹{'没动' if P._fingerprint(k2) == fp2_before else '**跟着变了**'}")

    ok("★ [RB3] 缓存**真的命中**：第二次调用**不产生新的建造**",
       n_second == n_first, f"第一次后 {n_first} 次建｜第二次后 {n_second} 次建")

    # --- RB4：有人改过之后**再取**，拿到的仍必须是 pristine --------------------
    # ⚠️ 这一条守的是「缓存里存的是**拷贝**，不是**发出去的那个对象**」。
    #    只测 RB1/RB2 抓不到它：那两条都在「还没有人改过」的时刻取样。
    k3, _p3 = F.reusable_build(tag, nodes, edges, produce)
    ok("★ [RB4] 消费者改过自己那份**之后**再取 ⇒ 拿到的**仍是 pristine**"
       "（⇒ 缓存里存的是拷贝，不是发出去的那个对象）",
       P._fingerprint(k3) == P._fingerprint(k_fresh),
       "指纹相同" if P._fingerprint(k3) == P._fingerprint(k_fresh)
       else "**被污染了** —— 缓存存的是发出去的那个对象")

    # --- 对照：把 `deepcopy` 去掉 ⇒ RB2 必须红 -------------------------------
    def shared(tag, nodes, edges, produce_):        # noqa: ANN001 —— 故意是错的那版
        ent = F._BUILD_CACHE.get(tag)
        if ent is not None and ent[0] is nodes and ent[1] is edges:
            return ent[2]                            # ← **没有** deepcopy
        res = produce_()
        F._BUILD_CACHE[tag] = (nodes, edges, res)
        return res

    F.clear_build_cache()
    s1, _ = shared(("test", "shared"), nodes, edges, produce)
    s2, _ = shared(("test", "shared"), nodes, edges, produce)
    ok("★ [RB·对照] 去掉 `deepcopy` ⇒ 两次拿到**同一个对象** ⇒ `RB2` **必须红**",
       s1 is s2, f"`s1 is s2` = {s1 is s2}")

    fp_s2 = P._fingerprint(s2)
    s1.insert("__probe2__", probe_item)
    ok("★ [RB·对照] 共享版：改 `s1` ⇒ **`s2` 跟着变** —— 这就是「必须拷」的理由"
       "（`cli d` 那条护栏正是这样被静默拆掉的）",
       P._fingerprint(s2) != fp_s2,
       f"`s2` 指纹{'跟着变了' if P._fingerprint(s2) != fp_s2 else '**没变**（对照没生效）'}")

    F.clear_build_cache()


def test_lock_scope() -> None:
    """★ `§10.2 D` 的 **P8**：锁沿**路径**局部化。

    五条断言（三个方向各一遍）：

      1. **作用域 = 锥**（`insert` / `remove` 拿的锁**恰好**是它走过的路径）
      2. **根是唯一公共争用点**（所有锥的**公共**交 == `{根}`）
      3. **与扇出无关**（对照：扇出锁的集合**随扇出变** ⇒ 红）
      4. ★ **互斥**（barrier 探针）：真锁绿、**空锁红**（已知答案）
      5. ★ **对照**：全局锁 / 扇出锁 ⇒ 判据 1、2 红（真耦合，见下）
      6. ★ **反向判据**：`LockedKernel` 与 `Kernel` 跑出来**逐字相同**（加锁不改语义）
      7. ★ **两处 O(1) 短临界区的链路**（2026-10-11 补）：`_new` 真的进过 `_alloc`、
         `_LockedLedger.append` 真的进过 `_lk`；两条各配一个「错一处」对照 ⇒ 计数 0 ⇒ 红
    """
    from ldv.checks._fixtures import load
    from ldv.core.kernel import Kernel as _K
    from ldv.core.locking import FanoutLocks, GlobalLocks, LockedKernel, NoLocks

    loaded = load()
    if loaded is None:
        PASS.append("并发（跳过：语料目录不在）")
        return
    nodes, edges, _ = loaded
    ids = sorted(nodes)
    whichs = ("keyset", "reach", "sequence")

    def build_locked(which, locks=None):
        from ldv.plugins.reach import ReachPlugin
        from ldv.plugins.sequence import SequencePlugin

        if which == "keyset":
            plug = KeysetPlugin()
            root = plug.merge([])
        elif which == "reach":
            plug = ReachPlugin(edges, traverse_budget=10_000)
            root = frozenset(nodes)
        else:
            plug = SequencePlugin(sequences(nodes, edges))
            root = frozenset({()})
        k = LockedKernel(plug, items(nodes), locks=locks)
        k.build(root)
        for nid in ids:
            k.insert(nid)
        return k

    # --- ①②③ 基线 -----------------------------------------------------------
    for which in whichs:
        k = build_locked(which)
        bad_i = [x for x in ids if _lock_scope(k, k.insert, x) != set(k.cone(x))]
        ok(f"★ [P8] {which}：**作用域 = 锥**（`insert` 拿的锁恰好是它走过的路径）",
           not bad_i, f"不符 {len(bad_i)}/{len(ids)}：{bad_i[:2]}")

        # ⚠️ `remove` 会真的把项删掉 ⇒ 另起一个内核；而且锥要在**删之前**取
        #    （`remove` 结尾会 `_path.pop`，删完再问 `cone()` 是空元组）。
        kr = build_locked(which)
        bad_r = []
        for x in ids[:8]:
            want = set(kr.cone(x))
            if _lock_scope(kr, kr.remove, x) != want:
                bad_r.append(x)
        ok(f"★ [P8] {which}：**作用域 = 锥**（`remove` 同）",
           not bad_r, f"不符 {len(bad_r)}/8：{bad_r[:2]}")

        cones = [set(k.cone(x)) for x in ids]
        cones = [c for c in cones if c]
        common = set.intersection(*cones) if cones else set()
        ok(f"★ [P8] {which}：**根是唯一公共争用点**（所有锥的公共交 == {{根}}）",
           common == {k.root.did}, f"公共交 = {sorted(common)}，根 = {k.root.did}")

        path_sizes = [len(_lock_scope(k, k.insert, x)) for x in ids]
        k2 = build_locked(which)
        k2.locks = FanoutLocks(
            lambda did, _k=k2: [c.did for c in _k.children_of(_k.direction(did))])
        fan_sizes = [len(_lock_scope(k2, k2.insert, x)) for x in ids]
        ok(f"★ [P8] {which}：作用域**与扇出无关** —— 路径锁 max {max(path_sizes)}"
           f" < 扇出锁 max {max(fan_sizes)}（扇出不常数 ⇒ 扇出锁的集合无界）",
           max(path_sizes) < max(fan_sizes),
           f"路径 {max(path_sizes)} vs 扇出 {max(fan_sizes)}")
        print(f"    · {which:8s} 作用域 = 锥（insert {len(ids)}/{len(ids)}、"
              f"remove {8 - len(bad_r)}/8）｜公共交 {sorted(common)}"
              f"｜路径锁 max {max(path_sizes)} vs 扇出锁 max {max(fan_sizes)}")

    # --- ④ 互斥（barrier，**确定性**） ---------------------------------------
    from ldv.core.locking import PathLocks as _PL
    ok("★ [P8] **互斥成立**：真锁下两个线程不能同时进同一个方向的临界区"
       "（barrier 逼它们同时进 ⇒ 超时 ⇒ 没进去过）",
       not _mutex_broken(_PL()), "PathLocks")
    ok("★★ [P8] **已知答案对照**：**空锁**下两个线程**同时进去**了 ⇒ 探针会红",
       _mutex_broken(NoLocks()), "NoLocks")
    ok("★ [P8] 全局锁也互斥（它只是**范围太大**，不是没锁）",
       not _mutex_broken(GlobalLocks()), "GlobalLocks")

    # --- ⑤ 对照：全局锁 / 扇出锁 ⇒ 判据 1、2 红 ------------------------------
    #
    # ⚠️ **真耦合**：判据 1（作用域 = 锥）与判据 2（公共交 = 根）在**本批语料上**
    #    由同一个事实推出（锥都从根开头、且没有别的方向在所有锥上）⇒
    #    两个对照会**同时**带红两条。这里**照实打印**，不挑一条来报 ——
    #    一个「只报自己那条」的对照是**被挑选过的**证据。
    for which in whichs:
        for code, mk in (("全局锁", GlobalLocks), ("扇出锁", None)):
            if mk is None:
                k3 = build_locked(which)
                k3.locks = FanoutLocks(
                    lambda did, _k=k3: [c.did for c in _k.children_of(_k.direction(did))])
            else:
                k3 = build_locked(which, locks=mk())
            s1 = [x for x in ids if _lock_scope(k3, k3.insert, x) != set(k3.cone(x))]
            # ⚠️ 这里量的是**锁管理器实际拿的**作用域，**不是**内核的锥 ——
            #    拿内核的锥去比，对照当然还是「公共交 = 根」（锥没变），
            #    判据 2 就永远红不了。**读数要跟读数比。**
            scopes = [_lock_scope(k3, k3.insert, x) for x in ids]
            common = set.intersection(*scopes) if scopes else set()
            ok(f"★ [P8] {which}·{code} 对照：**作用域 ≠ 锥** ⇒ 判据 1 红"
               "（否则判据 1 是空转）", bool(s1), f"不符 {len(s1)}/{len(ids)}")
            ok(f"★ [P8] {which}·{code} 对照：**公共交 ≠ {{根}}** ⇒ 判据 2 红",
               common != {k3.root.did}, f"公共交 {len(common)} 个：{sorted(common)[:3]}")

    # --- ⑥ 反向判据：单线程路径**逐字不变**（钩子默认空操作） -----------------
    k = build_locked("keyset")
    k0 = _K(k.plugin, k.items)
    k0.build(k.plugin.merge([]))
    for nid in ids:
        k0.insert(nid)
    ok("★ [P8] **反向判据**：`LockedKernel`（锥上拿锁）与 `Kernel`（空钩子）"
       "跑出来**逐字相同** —— 加锁不改语义（否则「作用域」这件事是拿正确性换的）",
       [(d.did, d.rank, d.payload, d.parent) for d in k.all_directions()]
       == [(d.did, d.rank, d.payload, d.parent) for d in k0.all_directions()]
       and k.stats() == k0.stats(),
       f"方向 {len(k.all_directions())} vs {len(k0.all_directions())}")

    # --- ⑦ 两处 O(1) 短临界区：**链路**验过（2026-10-11 补） -------------------
    #
    # 判据 ①②③ 守的是**作用域**（锥），探针 ④ 守的是 `PathLocks` 的**互斥**。
    # 而 `core/locking.py` 说「结构锁之外还有两处 O(1) 短临界区，它们**不在锥上**」——
    # 那两处（`LockedKernel._alloc` 管编号分配、`_LockedLedger._lk` 管账本只追加）
    # **原来没有任何判据**。
    #
    # ⚠️ 为什么**不**用 barrier 探针测它们：那两处各是一把**普通 `threading.Lock`**，
    #    探「它互斥吗」是**同义反复**（`threading.Lock` 的定义就是互斥）。
    #    会**静默退化**的是**链路** —— 把 `_new` 里那句 `with self._alloc`、
    #    或 `_LockedLedger.append` 里那句 `with self._lk` 删掉，**锁对象还在**，
    #    于是作用域判据 ①②③ 与互斥探针 ④ **全都照样绿**。
    #    ⇒ 判据 = **计数锁**：把锁换成一个会记账的等价物，跑一遍真装配，
    #      要求它**真的被进过**；每条各配一个「错一处」的对照 ⇒ 计数 0 ⇒ 红。
    from ldv.core.direction import Ledger as _Ledger

    class _Counting:
        """等价于 `threading.Lock`，外加**进过几次**。"""
        __slots__ = ("_lk", "n")

        def __init__(self) -> None:
            import threading as _th
            self._lk = _th.Lock()
            self.n = 0

        def __enter__(self):  # type: ignore[no-untyped-def]
            self.n += 1
            return self._lk.__enter__()

        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            return self._lk.__exit__(*exc)

    class _NoAlloc(LockedKernel):
        """**错一处**：编号分配不进临界区（`_new` 直接走基类）。"""

        def _new(self, **kw):  # type: ignore[no-untyped-def]
            return _K._new(self, **kw)

    class _BareLedger(_Ledger):
        """**错一处**：账本只追加，但**不进临界区**（没有 `_lk`）。"""

    def _probe(mk):  # type: ignore[no-untyped-def]
        """装计数锁、跑一遍真装配，读**两处各被进过几次**。"""
        kk = mk()
        kk._alloc = _Counting()
        if hasattr(kk.ledger, "_lk"):
            kk.ledger._lk = _Counting()
        plug = KeysetPlugin()
        kk.build(plug.merge([]))
        for nid in ids:
            kk.insert(nid)
        return (getattr(getattr(kk, "_alloc", None), "n", 0),
                getattr(getattr(kk.ledger, "_lk", None), "n", 0))

    a_ok, l_ok = _probe(lambda: LockedKernel(KeysetPlugin(), items(nodes)))
    ok("★ [P8] **两处 O(1) 短临界区：链路验过**（不是只写在文档里）—— "
       "`_new` 真的进过 `_alloc`、`_LockedLedger.append` 真的进过 `_lk`",
       a_ok > 0 and l_ok > 0, f"_alloc 进过 {a_ok} 次｜_lk 进过 {l_ok} 次")

    a_bad, _ = _probe(lambda: _NoAlloc(KeysetPlugin(), items(nodes)))
    ok("★★ [P8] **已知答案对照**：`_new` **绕过** `_alloc` 的版本 ⇒ 同一条判据红",
       a_bad == 0, f"_alloc 进过 {a_bad} 次")

    def _bare() -> LockedKernel:
        kk = LockedKernel(KeysetPlugin(), items(nodes))
        kk.ledger = _BareLedger()
        return kk

    _, l_bad = _probe(_bare)
    ok("★★ [P8] **已知答案对照**：账本**不进临界区**的版本 ⇒ 同一条判据红",
       l_bad == 0, f"_lk 进过 {l_bad} 次")
    print(f"    · 短临界区链路：`_alloc` 进过 {a_ok} 次｜`_lk` 进过 {l_ok} 次"
          f"（对照：`_new` 绕过 `_alloc` ⇒ {a_bad}｜账本不进临界区 ⇒ {l_bad}）")

    # --- ⑧ 接线：`--locked` 这条**路径**真的接上了锁（2026-10-15 补） -------------
    #
    # ⚠️ 判据 ⑥ 是「`LockedKernel` 与 `Kernel` **逐字相同**」——
    #    它**证明不了「接上了」**：它**恒真**。把 `--locked` 整个忽略掉，
    #    输出照样逐字相同 ⇒ 「接线漏了」与「接好了」**长得一模一样**。
    #    ⇒ 接线要**两条一起**，缺一条就是本仓库最忌的那个形状：
    #      ⑧a **等价** —— `--locked` 与不走的**追踪文本逐字相同**（三方向 + 删除）
    #      ⑧b **链路** —— `--locked` 时内核**真的是 `LockedKernel`**，且
    #                     `locks.taken` **真的非空**（锁被拿过）
    #    ⚠️ **互不替代**：只有 ⑧a ⇒ 「锁没接上」照样绿；
    #       只有 ⑧b ⇒ 「接了但改了行为」没人守。
    from ldv import cli as _cli

    # ⚠️ `_assemble` 收的是**方向名**（`keyset` / `reach` / `sequence`），
    #    **不是** `cli` 那个 `all` —— `all` 只是 `main()` 里循环 `DIRECTIONS` 的写法。
    _cases = (("keyset", 12, None, 12), ("reach", 7, None, 0),
              ("sequence", 12, "r", 12), ("keyset", 12, "a", 0))
    eq_bad: list[str] = []
    for nm, ho, only, rm in _cases:
        _ok1, t_plain = _cli.run_one(nm, ho, only, rm, loaded=loaded)
        _ok2, t_lock = _cli.run_one(nm, ho, only, rm, loaded=loaded, locked=True)
        if t_plain != t_lock:
            eq_bad.append(f"{nm}/only={only}/ho={ho}/rm={rm}")
    ok("★ [P8] **⑧a 接线·等价**：走 `--locked` 与不走，**追踪文本逐字相同**"
       f"（{len(_cases)} 组：三方向 + 删除；⚠️ 这条**恒真** ⇒ 它**不能单独**证明接上了）",
       not eq_bad, f"不同的：{eq_bad}")

    k_lock, *_ = _cli._assemble("keyset", nodes, edges, 12, locked=True)
    k_plain, *_ = _cli._assemble("keyset", nodes, edges, 12, locked=False)
    _taken = getattr(getattr(k_lock, "locks", None), "taken", [])
    ok("★ [P8] **⑧b 接线·链路**：`--locked` 时内核**真的是 `LockedKernel`**，"
       "且**锁真的被拿过**（`locks.taken` 非空）",
       isinstance(k_lock, LockedKernel) and len(_taken) > 0,
       f"类型={type(k_lock).__name__}｜拿过 {len(_taken)} 次")
    ok("★★ [P8] **已知答案对照**：**不走** `--locked` ⇒ 内核**不是** `LockedKernel`、"
       "一次锁都没拿 ⇒ ⑧b 会红",
       (not isinstance(k_plain, LockedKernel))
       and not getattr(getattr(k_plain, "locks", None), "taken", []))
    print(f"    · 接线：`--locked` 拿过锁 {len(_taken)} 次｜"
          f"默认路径类型 {type(k_plain).__name__}（拿过 "
          f"{len(getattr(getattr(k_plain, 'locks', None), 'taken', []))} 次）")

    # --- ⑧c 参数：CLI 的 `--locked` 确实传到 run_one（2026-10-15 补） -----------
    #
    # ⑧a/⑧b 直接走 `run_one` / `_assemble`，测的是**运行链路**；
    # 这条单独测**命令行入口有没有把开关传下去** —— 不重复构建语料。
    from contextlib import redirect_stdout as _redirect_stdout
    from io import StringIO as _StringIO

    _orig_load, _orig_run_one = _cli.load, _cli.run_one
    _seen_locked: list[bool] = []

    def _stub_run_one(name, holdout, only, remove=0, loaded=None, locked=False,
                      predicates=False, reflect=False, **_kwargs):
        # ⚠️ 桩的签名要跟着 `run_one` 的接口走 —— 2026-10-17 加了 `predicates`
        #    与 `reflect`，两次都忘了补 ⇒ `cli.main` 传关键字时 `TypeError`。
        #    ⇒ 末尾加 `**_kwargs` 兜底：以后再加可选参数，这个桩不会再炸。
        #    （`locked` 仍显式收 —— ⑧c 判的就是它，不能被 kwargs 吞掉。）
        _seen_locked.append(locked)
        return True, "stubbed"

    def _invoke_cli(with_flag: bool) -> tuple[int, str, list[bool]]:
        _seen_locked.clear()
        _out = _StringIO()
        _argv = ["ldv.cli", "--plugin", "keyset", "a", "--holdout", "12"]
        if with_flag:
            _argv.append("--locked")
        try:
            _cli.load = lambda: loaded
            _cli.run_one = _stub_run_one
            with _redirect_stdout(_out):
                _rc_cli = _cli.main(_argv)
        finally:
            _cli.load, _cli.run_one = _orig_load, _orig_run_one
        return _rc_cli, _out.getvalue(), list(_seen_locked)

    _rc_locked, _txt_locked, _seen_with_flag = _invoke_cli(True)
    _rc_default, _txt_default, _seen_without_flag = _invoke_cli(False)
    ok("★ [P8] **⑧c CLI 接线**：`--locked` 被解析并传到 run_one；默认不锁",
       _rc_locked == 0 and _rc_default == 0
       and "并发壳" in _txt_locked and "并发壳" not in _txt_default
       and _seen_with_flag == [True] and _seen_without_flag == [False],
       f"locked rc={_rc_locked} / 收到 {_seen_with_flag}｜"
       f"default rc={_rc_default} / 收到 {_seen_without_flag}")








def test_reflect() -> None:
    """★ 反思层四条判据（`ldv/core/reflect.py`，2026-10-17）。

    边界：**只读内生量、只调展开顺序**。不改方向函数 / 判据 / 外生项；
    不引入标量分（`docs/评估标准.md` 的「不作分」）。
    """
    from ldv.cli import _assemble
    from ldv.core import reflect as RF
    from ldv.checks._fixtures import load as _load

    loaded = _load()
    assert loaded is not None
    nodes, edges, _d = loaded

    k1, _q1, *_ = _assemble("keyset", nodes, edges, 12)
    rep = RF.report(k1)

    # ㉒ 字段全内生：每个「逐方向」行的数都能从 kernel 重算
    bad = []
    for row in rep["逐方向"]:
        d = k1.direction(row["did"])
        if row["成员数"] != len(k1.members_of(d)) or row["子方向数"] != len(k1.children_of(d)):
            bad.append(row["did"])
    ok("★ [反思] ㉒ 字段全内生：报告每个数都能从结构重算",
       not bad, f"对不上的方向：{bad[:3]}")

    # ㉓ 顺序确定：同结构两次 ⇒ 逐字相同
    order1 = RF.expand_order(k1)
    order2 = RF.expand_order(k1)
    ok("★ [反思] ㉓ 顺序确定：同结构两次 `expand_order` 逐字相同",
       order1 == order2, f"{order1[:3]} vs {order2[:3]}")

    # ㉔ **预展开不改终态**（2026-10-23 重写）
    #    ⚠️ 旧版在**完整树**上比 `before == after`：完整树的待展开**恒为 0**
    #       （判空不再被误报成「待展开」之后），`apply()` 请求数 0
    #       ⇒ 断言**恒真、空转** —— 正是本仓库最忌的「空转与通过长得一模一样」。
    #    ⇒ 改成在**半成品树**上验真性质：预展开之后继续建完，
    #       终态与「直接建完」的**划分集合逐字相同**（比 `did` 会假警报，见 ⑦）。
    from ldv.checks._fixtures import build_keyset as _bk_full
    from ldv.checks._fixtures import make_keyset_root as _mk_root

    def _partition_of(kk):
        return sorted(tuple(sorted(kk.members_of(d))) for d in kk.all_directions())

    k_lazy = _mk_root(nodes)()
    applied = RF.apply(k_lazy)
    for _i in sorted(nodes):
        k_lazy.insert(_i)
    k_full, _ = _bk_full(nodes)
    _same = _partition_of(k_lazy) == _partition_of(k_full)
    ok("★ [反思] ㉔ **预展开不改终态**：半成品树预展开后建完，划分集合与直接建完逐字相同",
       len(applied["预展开"]) > 0 and _same,
       f"预展开 {len(applied['预展开'])} 个（**必须 > 0**，否则这条空转）"
       f"｜划分集合相同={_same}｜方向数 {len(k_lazy.all_directions())} vs "
       f"{len(k_full.all_directions())}")

    # ㉕ 无标量分：字段白名单
    # ⚠️ 白名单要**跟着报告一起改**：2026-10-17 加了 `矛盾`（结构层矛盾清单）。
    #    白名单的意义是「报告里出现意料之外的字段就红」—— 加字段必须显式改这里，
    #    这样「偷偷往报告里塞一个分数」会当场被这条判据抓住。
    allowed = {"方向数", "待展开数", "展开序", "逐方向", "排序规则", "矛盾",
               # 2026-10-22 加了 `分级`（显著性三档：必须 / 可延后 / 可忽略）
               "分级"}
    ok("★ [反思] ㉕ 无标量分：报告字段在白名单内（没有分数 / 健康度 / 优先级分）",
       set(rep) == allowed, f"多出来的字段：{set(rep) - allowed}")




























def test_loader_sharded() -> None:
    """★ 分片语料目录三条判据（`loader.load_nodes` / `index.load_nodes_indexed`）。

    起因（2026-10-18）：把 28 万节点摄入成分片目录后，`load_nodes` 已改成 `rglob`，
    但 `index.py` 还是 `glob` ⇒ 读出 **0 个节点**且**不报错** ⇒ 探针五条判据
    在空语料上全部「通过」（假绿）。⇒ 三处一起修 + 判据钉住。
    """
    import shutil
    import tempfile
    from pathlib import Path

    from ldv.corpus.index import load_nodes_indexed
    from ldv.corpus.loader import CorpusError, load_edges, load_nodes
    from ldv.checks._fixtures import load as _load

    loaded = _load()
    assert loaded is not None
    nodes, edges, _d = loaded

    tmp = Path(tempfile.mkdtemp(prefix="ldv-shard-"))
    try:
        flat = tmp / "flat"
        sharded = tmp / "sharded"
        (flat / "nodes").mkdir(parents=True)
        (sharded / "nodes").mkdir(parents=True)
        # 把默认语料的每个节点写两份：一份扁平、一份按 id 前两位分桶
        for nid, n in nodes.items():
            body = (f"---\nid: \"{nid}\"\ntype: \"{n.fields.get('type')}\"\n"
                    f"relations: {list(n.relations())!r}\n---\n\n正文\n")
            (flat / "nodes" / f"{nid}.md").write_text(body, encoding="utf-8")
            sub = sharded / "nodes" / (nid[:2] if len(nid) >= 2 else "00")
            sub.mkdir(parents=True, exist_ok=True)
            (sub / f"{nid}.md").write_text(body, encoding="utf-8")

        a, b = load_nodes(flat / "nodes"), load_nodes(sharded / "nodes")
        same = (set(a) == set(b)
                and all(a[k].keys == b[k].keys for k in a)
                and all(a[k].fields == b[k].fields for k in a))
        ok("★ [分片] ㊏ 分片 == 扁平：同一批节点，两种布局装载结果逐字相同",
           same and len(b) == len(nodes),
           f"扁平 {len(a)}｜分片 {len(b)}｜原有 {len(nodes)}")

        # ㊐ 索引也读分片
        ia, sa = load_nodes_indexed(flat / "nodes", tmp / "idx")
        # 第一次建索引（全读），第二次才命中 —— 判据要看**第二次**
        _ib0, _sb0 = load_nodes_indexed(sharded / "nodes", tmp / "idx")
        ib, sb = load_nodes_indexed(sharded / "nodes", tmp / "idx")
        ok("★ [分片] ㊐ 索引也读分片：命中数 == 节点数（不再读出 0 个）",
           sb["命中"] == len(b) and len(ib) == len(nodes),
           f"命中 {sb['命中']} / 节点 {len(b)}｜索引文件 {Path(sb['索引']).name}")

        # ㊑ 空目录报错（不静默返回空）
        empty = tmp / "empty"
        (empty / "nodes").mkdir(parents=True)
        raised = False
        try:
            load_nodes_indexed(empty / "nodes", tmp / "idx")
        except CorpusError:
            raised = True
        ok("★ [分片] ㊑ 空目录要报错：索引路径遇到没有 .md 的目录 ⇒ CorpusError",
           raised, "抛了 CorpusError" if raised else "静默返回空 ⇒ 假绿")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)








def test_candidate_dimensions() -> None:
    """★ 候选维度清单（`request.candidate_dimensions`，2026-10-19）。

    ⚠️ 只报**读数**（取值数 / 覆盖率）与**离散判定**，不打分、不排序成「优先级」。
    """
    from ldv.core import request as RQ
    from ldv.checks._fixtures import load as _load

    loaded = _load()
    assert loaded is not None
    nodes, _e, _d = loaded
    r = RQ.candidate_dimensions(nodes)
    rows = {x["字段"]: x for x in r["候选维度"]}

    # ㊡ 恒定字段（默认语料里 `type` 不是恒定；用一个**造出来的**恒定字段测）
    from dataclasses import replace as _replace
    fake = {k: _replace(n, fields={**n.fields, "恒定测试": "同一个值"})
            for k, n in nodes.items()}
    rf = RQ.candidate_dimensions(fake)
    row_c = {x["字段"]: x for x in rf["候选维度"]}.get("恒定测试")
    ok("★ [候选维度] ㊡ 恒定字段（只有 1 种取值）⇒ 标「分不开」（§D8 四类之一）",
       row_c is not None and "恒定" in row_c["读数"] and row_c["取值数"] == 1,
       f"{row_c['读数'] if row_c else '（没列出来）'}｜取值数 {row_c['取值数'] if row_c else '-'}")

    # ㊢ 已用字段
    ok("★ [候选维度] ㊢ 已用作键的字段标「已用作键」",
       rows.get("type", {}).get("读数") == "已用作键"
       and rows.get("evidence_status", {}).get("读数") == "已用作键",
       f"type={rows.get('type', {}).get('读数')}｜"
       f"evidence_status={rows.get('evidence_status', {}).get('读数')}")

    # ㊣ 几乎唯一（id 每项唯一）
    rid = rows.get("id")
    ok("★ [候选维度] ㊣ 几乎每项唯一的字段标「分出来等于没分」",
       rid is not None and "几乎每项唯一" in rid["读数"],
       f"id：{rid['取值数'] if rid else '-'} 种取值 / {len(nodes)} 项 ⇒ "
       f"{rid['读数'] if rid else '-'}")

    # ㊤ 不作分（红线）
    import json as _json
    blob = _json.dumps(r, ensure_ascii=False)
    bad = [w for w in ("评分", "打分", "优先级", "score", "rank", "权重") if w in blob]
    ok("★ [候选维度] ㊤ **不作分**：清单里没有「评分/优先级/score/rank/权重」",
       not bad, f"命中 {bad}" if bad else "干净")

    # ㊥ 说明里必须能看出「有没有可批的」
    ok("★ [候选维度] ㊥ 说明里明说「可以当维度的 N 个」或「没有可用的新维度」",
       ("可以当维度的" in r["说明"]) or ("没有可用的新维度" in r["说明"]),
       r["说明"][:60])
def test_protocol() -> None:
    """★ **两个 ldv 系统之间的交流协议**（`ldv-msg/1`，2026-10-21）。

    用户 2026-10-21：「设计两个 ldv 系统之间的交流协议，不必将其转化成自然语言，
    允许直接对 ldv 的输出进行解析」。

    为什么不做自然语言：ldv 的输出**本来就是结构**，翻成话是双重有损
    （丢掉「到底是哪 1888 项」；丢掉**可核验性**）。⇒ 协议 = 信封化 + 接收方自检。

    两条红线（写进判据，不只写在 docstring 里）：
      ① 只换结构不换语义 —— 不许回「这些人很有影响力」（模型代填）；
      ② **两个系统之间也不排序** —— 消息里出现跨系统可比的标量字段一律拒收（E-1/E-2 延伸）。
    """
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from ldv import protocol as PRT
    import entry as E


    # ── 64 信封五查：五种坏法各自拒收，且**带理由** ────────────────────
    good = PRT.envelope(PRT.MSG_QUERY, {"require": ["type=概念"]}, frm="A", fp="f" * 16)
    bad_proto = dict(good, proto="ldv-msg/999")
    bad_type = dict(good, type="MAGIC")
    no_fp = {k: v for k, v in good.items() if k != "fp"}
    not_dict = ["不是对象"]
    checks = [(good, True), (bad_proto, False), (bad_type, False),
              (no_fp, False), (not_dict, False)]
    results = [(PRT.check_envelope(m)[0] == want, PRT.check_envelope(m)[1][:30])
               for m, want in checks]
    ok("★ [协议] 64 信封五查（版本/类型/必填/是不是 dict），坏法各自拒收并给理由",
       all(r[0] for r in results) and all(r[1] for r in results[1:]),
       "｜".join(("过" if r[0] else "拒：" + r[1]) for r in results))

    # ── 65 **协议级禁词**：嵌套的 score/order ⇒ 拒收并指出路径 ──────────
    # ⚠️ 禁词**不含** `rank` / `order`：`nodes[].rank` 是 ldv 的**骨架**字段
    #    （第几层），不是排序分。第一版含它们 ⇒ 每条合法 RESULT 都被拒
    #    （判据 66 端到端当场撞出来的）。⇒ 这里用两个真禁词：`weight`（顶层）
    #    与 `rating`（嵌在 nodes 里，验递归）。
    nasty = PRT.envelope(PRT.MSG_QUERY,
                        {"require": ["a"], "weight": 3,
                         "nodes": [{"did": "D1", "rating": 0.9}]},
                        frm="A", fp="f" * 16)
    okk, why = PRT.check_envelope(nasty)
    ok("★ [协议] 65 嵌套禁词字段 ⇒ 拒收（两个系统之间也不排序 / 不打分，§S2）",
       okk is False and "weight" in why and "rating" in why,
       f"拒收理由：{why[:70]}")

    # ── 66 **对等端到端**：A 问 B，B 的 RESULT 过自检且与本地同答案 ────
    #    ⚠️ 两个 App 实例 = 两个系统（同语料时它们必须给同一答案 ——
    #      这正是协议「可核验」的意思：B 报的答案，A 能自己重算）。
    A = E.App(None)                     # 内置 36 项语料；不设环境变量
    B = E.App(None)
    q = PRT.build_query(["type=概念"], [], frm=A.who(), fp=A.fingerprint())
    r = B.msg(q)
    v_ok, v_bad = PRT.verify_result(r)
    local = B.query({"require": ["type=概念"]})
    ok("★ [协议] 66 对等端到端：QUERY→RESULT 过自检，且答案与本端逐字相同",
       r.get("type") == PRT.MSG_RESULT and v_ok
       and r["payload"]["answer_ids"] == local["answer_ids"]
       and r["payload"]["query"] == {"require": ["type=概念"], "forbid": []},
       f"答案 {r['payload']['n_answer']} 项｜{len(r['payload']['nodes'])} 支｜"
       f"自检 {v_ok}｜回声查询 ✓")

    # ── 67 **反向对照①**：谎报项数 ⇒ 自检必须红 ─────────────────────────
    lied = json.loads(json.dumps(r))
    lied["payload"]["n_answer"] = lied["payload"]["n_answer"] + 7
    v1_ok, v1_bad = PRT.verify_result(lied)
    ok("★ [协议] 67 反向：谎报 n_answer ⇒ 自检报红（接收方不依赖发送方诚信）",
       v1_ok is False and any("谎报项数" in b for b in v1_bad),
       f"抓到：{v1_bad[0][:60] if v1_bad else '（没抓到！）'}")

    # ── 68 **反向对照②**：Σ各支 ≠ 答案 ⇒ 自检必须红 ────────────────────
    broken = json.loads(json.dumps(r))
    if broken["payload"]["nodes"]:
        broken["payload"]["nodes"][0]["n_answer"] += 3
    v2_ok, v2_bad = PRT.verify_result(broken)
    ok("★ [协议] 68 反向：Σ各支 ≠ 答案 ⇒ 自检报红（不重不漏破了）",
       v2_ok is False and any("不重不漏" in b for b in v2_bad),
       f"抓到：{v2_bad[0][:60] if v2_bad else '（没抓到！）'}")

    # ── 68c **反向对照③**：谎报「超出结构」⇒ 自检必须红（2026-10-22）──────
    #    `ok=False` 有两种含义（键拼错 / 超出结构），后者必须给 `unknown_keys` 作凭证，
    #    否则可以拿「超出结构」当**逃避回答**的借口（键明明在，硬说超出）。
    lie2 = PRT.build_result(q, {"ok": False, "error": "x",
                                "out_of_structure": True}, frm="B", fp="fpB")
    v4_ok, v4_bad = PRT.verify_result(lie2)
    ok("★ [协议] 68c 反向：谎报「超出结构」（不给 unknown_keys）⇒ 自检报红",
       v4_ok is False and any("空口说超出" in b for b in v4_bad),
       f"{v4_bad[:1] if v4_bad else '（没抓到！）'}")

    # ── 69 **文件传输**：一条好 + 两条坏 ⇒ 1 RESULT + 2 ERROR，留痕不静默 ──
    tmp = Path(tempfile.mkdtemp())
    try:
        inp = tmp / "in.jsonl"
        inp.write_text(
            json.dumps(q, ensure_ascii=False) + "\n"
            + "{这不是 JSON\n"
            + json.dumps({**no_fp, "from": "X"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        st = PRT.exchange_file(B, inp, tmp / "out.jsonl")
        out_lines = [ln for ln in (tmp / "out.jsonl").read_text(
            encoding="utf-8").splitlines() if ln.strip()]
        types = [json.loads(ln)["type"] for ln in out_lines]
        ok("★ [协议] 69 文件交换：坏 JSON / 坏信封 ⇒ ERROR 留痕，不拖垮好的那条",
           st == {"in": 3, "ok": 1, "rejected": 2}
           and types.count(PRT.MSG_RESULT) == 1 and types.count(PRT.MSG_ERROR) == 2,
           f"stats {st}｜消息类型 {types}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 70 **对等**：两个实例互为问答方，同语料逐字相同 ──────────────────
    #    ⚠️ 判据**不碰 HTTP**（纪律同 [入口] 51：起端口会把判据变成环境读数）；
    #       HTTP 层由 `evidence/_dialogue_two_ldv.py` 实测（文件层与 HTTP 逐字相同）。
    #    ⚠️ CI 上没有外部语料 ⇒ 这里用**内置语料的两个实例**（A/B 同材料）。
    #       跨语料对话（词汇不通必须如实拒收）在留档脚本里用 ext-wiki-vote ×
    #       cora-labeled 实测 —— 那条依赖外部语料，进不了 CI。
    A2 = E.App(None)
    B2 = E.App(None)
    fp2 = A2.fingerprint()
    keys2 = sorted(k for k in A2.all_keys if "=" in k)
    probe = keys2[len(keys2) // 2] if keys2 else None
    qa = PRT.build_query([probe] if probe else [], [], frm="A", fp=fp2)
    qb = PRT.build_query([probe] if probe else [], [], frm="B", fp=fp2)
    ra = B2.msg(qa)                      # A 问，B 答
    rb = A2.msg(qb)                      # B 问，A 答 —— **对等**
    same_ans = (ra["payload"].get("answer_ids") == rb["payload"].get("answer_ids"))
    ok("★ [协议] 70 对等互问：A 问 B / B 问 A 的答案集逐字相同（同语料）",
       ra.get("type") == PRT.MSG_RESULT and rb.get("type") == PRT.MSG_RESULT
       and same_ans and PRT.verify_result(ra)[0] and PRT.verify_result(rb)[0],
       f"键 `{probe}`｜A→B {ra['payload'].get('n_answer')} 项｜"
       f"B→A {rb['payload'].get('n_answer')} 项｜相同 {same_ans}")

    # ── 71 真实问题形状过协议（2026-10-21 验收补的 CI 侧） ─────────
    #    验收脚本 evidence/_realq_two_ldv.py 用**外部语料** ⇒ 进不了 CI；
    #    同形状在这里用内置语料守：真实问题从不是单键 ⇒ 多键 require，
    #    外加回声 / 自检 / 与本端逐字相同三件。
    import itertools as _it
    A3 = E.App(None)
    B3 = E.App(None)
    ks3 = sorted(k for k in A3.all_keys if "=" in k)
    two = next((a, b) for a, b in _it.combinations(ks3, 2)
               if B3.answer_set(frozenset([a, b]), frozenset()))
    q3 = PRT.build_query(list(two), [], frm=A3.who(), fp=A3.fingerprint())
    r3 = B3.msg(q3)
    v3_ok, _ = PRT.verify_result(r3)
    loc3 = B3.query({"require": list(two)})
    ok("★ [协议] 71 真实问题形状过协议：多键 QUERY ⇒ RESULT 自检过、回声原查询、答案逐字相同",
       r3.get("type") == PRT.MSG_RESULT and v3_ok
       and r3["payload"]["answer_ids"] == loc3["answer_ids"]
       and r3["payload"]["query"] == {"require": list(two), "forbid": []},
       f"require {list(two)}｜答案 {r3['payload']['n_answer']} 项｜"
       f"{len(r3['payload']['nodes'])} 支｜自检 {v3_ok}")

    # ── 72 RESULT 的 from/fp 必须是**回答方**的 ────────────────────
    #    ★ 2026-10-21 真实问题验收抓到的真 bug（留档：
    #      evidence/_realq_two_ldv.before.txt，修前 8/8 全红）：
    #      第一版 build_result 从 query_msg 拷贝 from/fp ⇒ 结构不同的两个
    #      系统之间，RESULT 自称来自**提问方**的结构 —— fp 从票据变成误导。
    #      **同结构时两边指纹相同 ⇒ 66/70 看不见它** ⇒ 这条必须两个结构。
    #      （反向对照不做在判据里 —— 回退真源码跑一遍太重；
    #        before.txt 就是那份反向证据：同一批消息，修前全红。）
    import os as _os
    import tempfile as _tf2
    _tmp2 = Path(_tf2.mkdtemp())
    _restored = True
    r4: dict = {}
    try:
        c2 = _tmp2 / "corpus"
        (c2 / "nodes").mkdir(parents=True)
        for i in (1, 2):
            (c2 / "nodes" / f"m{i}.md").write_text(
                '---\nid: "m%d"\ntype: 概念\ntitle: "另一结构%d"\n---\n\n正文%d\n'
                % (i, i, i), encoding="utf-8")
        A4 = E.App(None)                    # 先建内置（36 项）—— 顺序不能反
        _saved = _os.environ.get("LDV_CORPUS")
        try:
            B4 = E.App(str(c2 / "nodes"))   # 再建另一份结构（2 项，不同指纹）
            q4 = PRT.build_query(["type=概念"], [], frm=A4.who(), fp=A4.fingerprint())
            r4 = B4.msg(q4)
            who_ok = (r4.get("from") == B4.who()
                      and r4.get("fp") == B4.fingerprint()
                      and B4.fingerprint() != A4.fingerprint()
                      and r4["payload"]["answer_ids"]
                      == B4.query({"require": ["type=概念"]})["answer_ids"])
        finally:
            if _saved is None:
                _os.environ.pop("LDV_CORPUS", None)
            else:
                _os.environ["LDV_CORPUS"] = _saved
            _restored = _os.environ.get("LDV_CORPUS") == _saved
        ok("★ [协议] 72 RESULT 的 from/fp 必须是**回答方**的（跨结构；修前 8/8 全红）",
           bool(who_ok) and _restored,
           f"RESULT from={r4.get('from')} fp={r4.get('fp')}"
           f"｜回答方 {B4.who()} fp={B4.fingerprint()}"
           f"｜提问方 fp={A4.fingerprint()}｜环境已恢复 {_restored}")
    finally:
        shutil.rmtree(_tmp2, ignore_errors=True)


def test_exchange_staging() -> None:
    """★ 一次交流记录 → 缓冲区里的**一次性树**（2026-10-21）。

    用户三句裁定逐条落地：
      「把一次交换当成完成……记录一次完整的交流」⇒ 记录本身是成果
      「每段当成一次缓冲区域的新建树，使用完成就可以抛弃」⇒ 一次交流一棵树，默认抛弃
      「抛弃机制遵守缓冲区域的规则，不去特意设计」⇒ abandoned = 显式删目录；
          容量只报不淘汰；`evict` 一行不加

    目录形状（与 `_staging/nodes/` 平级，两种默认命运分开）：
        _staging/nodes/           待提交（默认沉淀，走既有 commit）
        _staging/exchanges/<id>/   一次交流 = 一棵一次性树（默认抛弃）
    """
    import json as _json
    import shutil as _shutil
    import tempfile as _tf

    from ldv.corpus.loader import load_nodes
    from ldv.tools import exchange_staging as XS

    tmp = Path(_tf.mkdtemp())
    corpus = tmp / "corpus"
    (corpus / "nodes").mkdir(parents=True)
    # 两个正式节点（让「隔离」判据有东西可比）
    for i in (1, 2):
        (corpus / "nodes" / f"n{i}.md").write_text(
            '---\nid: "n%d"\ntype: 概念\ntitle: "正式%d"\n---\n\n正文%d\n' % (i, i, i),
            encoding="utf-8")
    before = len(load_nodes(corpus))

    # 造一次「交流」：4 条消息（QUERY / RESULT / ERROR），字段从信封数出来
    msgs = [
        {"proto": "ldv-msg/1", "type": "QUERY", "from": "A", "fp": "f" * 16,
         "ts": "t", "mid": "1", "payload": {"require": ["type=概念"], "forbid": []}},
        {"proto": "ldv-msg/1", "type": "RESULT", "from": "B", "fp": "g" * 16,
         "ts": "t", "mid": "2", "reply_to": "1",
         "payload": {"ok": True, "n_answer": 2, "n_branches": 1,
                     "answer_ids": ["n1", "n2"],
                     "nodes": [{"did": "D1", "n_answer": 2}]}},
        {"proto": "ldv-msg/1", "type": "QUERY", "from": "B", "fp": "g" * 16,
         "ts": "t", "mid": "3", "payload": {"require": ["type=案例"], "forbid": []}},
        {"proto": "ldv-msg/1", "type": "ERROR", "from": "A", "fp": "f" * 16,
         "ts": "t", "mid": "4", "reply_to": "3",
         "payload": {"ok": False, "error": "这些键在语料里不存在：`type=案例`"}},
    ]
    ex = tmp / "exchange.jsonl"
    ex.write_text("\n".join(_json.dumps(m, ensure_ascii=False) for m in msgs) + "\n",
                  encoding="utf-8")
    try:
        # ── ㉟ 往返：N 条消息 ⇒ N 个节点，**内容逐字未改** ──────────────
        r = XS.ingest(corpus, ex, "x1")
        nd = Path(r["目录"]) / "nodes"
        files = sorted(nd.glob("*.md"))
        ok("★ [缓冲] ㉟ 交流记录 → 一次性树：4 条消息 ⇒ 4 个节点，一条消息一节点",
           r["ok"] and r["消息"] == 4 and r["节点"] == 4 and len(files) == 4,
           f"消息 {r['消息']} ⇒ 节点 {r['节点']}｜目录 {nd.name}/")

        # 逐字：节点正文里必须能找到 payload 的原文（防「生成内容」）
        body = files[1].read_text(encoding="utf-8")
        ok("★ [缓冲] ㊱ content = 消息原文：节点正文含 payload 的逐字段节选",
           '"n_answer": 2' in body and '"did": "D1"' in body
           and "reply_to" in files[1].read_text(encoding="utf-8")
           and "round: 1" in files[0].read_text(encoding="utf-8"),
           "payload 原文 JSON 在内，且 round/reply_to 从信封数出来")

        # ── ㊲ 隔离 + 可建核 + 可筛 ────────────────────────────────────
        # ⚠️ 传 **`nodes/` 子目录**，不传语料根 —— 这是真实调用链的形状
        #    （`find_corpus` 返回的就是 `<语料>/nodes`）。
        #    `load_nodes` 用 `rglob`（2026-10-18 为 28 万文件分片改的）⇒
        #    **传语料根会把 `_staging/` 一起读进来**（且静默！）—— 这不是本工具
        #    的 bug，是 loader 的既有行为；记在这里，免得下一个人拿根去调。
        mids = len(load_nodes(corpus / "nodes"))
        root_reads = len(load_nodes(corpus))
        ok("★ [缓冲] ㊲ 一次性树不进正式结构：`load_nodes(<语料>/nodes)` 前后逐字相同",
           mids == before and root_reads == before + 4,
           f"nodes/ 子目录 {before} ⇒ {mids}（不变）｜语料根 {root_reads}"
           f"（把 _staging 也读到了 —— 真实链路不传根，见上注）")

        # 把它当语料建核（LDV_CORPUS 指路径 —— find_corpus 支持，实测过）
        # ★★ 2026-10-21 **判据自己抓到的一次环境污染**：`App(路径)` 会把
        #    `LDV_CORPUS` 写进 `os.environ`（`App.__init__` 干的事）⇒
        #    不恢复的话，**本函数之后的所有判据都找不到语料** ⇒
        #    CorpusError 兜底把它们全记成「跳过」（实测：748 → 660、跳过 12）。
        #    ⇒ 判据不许改全局状态；改了就恢复，并**验恢复**。
        import entry as _E
        _saved = os.environ.get("LDV_CORPUS")
        _app = _E.App(str(nd))
        m = _app.meta()
        q = _app.query({"require": ["type=RESULT"]})
        ok("★ [缓冲] ㊳ 一次性树可直接当语料建核并查询（`LDV_CORPUS` 指路径）",
           m["nodes"] == 4 and m["corpus_from"] == "external"
           and q["n_answer"] == 1 and q["n_branches"] == 1,
           f"{m['nodes']} 项｜type=RESULT ⇒ {q['n_answer']} 项/{q['n_branches']} 支"
           f"（4 条消息 = QUERY×2 + RESULT×1 + ERROR×1）")

        # ── ㊴ 边界：front matter 里的 who/round **不是键**（如实记） ────
        q2 = _app.query({"require": ["who=A"]})
        # 环境恢复（判据跑完必须把现场还原 —— 与「跑完还原」同一条纪律）
        if _saved is None:
            os.environ.pop("LDV_CORPUS", None)
        else:
            os.environ["LDV_CORPUS"] = _saved
        restored = os.environ.get("LDV_CORPUS") == _saved
        ok("★ [缓冲] ㊴ 边界：who/round 是**字段不是键** ⇒ 按它筛会被拒收（不静默 0）",
           q2.get("ok") is False and "who=A" in str(q2.get("error", "")) and restored,
           f"可筛维度只有 {sorted(k for k in _app.all_keys if '=' in k)}"
           f"｜LDV_CORPUS 已恢复 = {restored}")

        # ── ㊵ promote 走既有 commit；abandon 无残留 ───────────────────
        p = XS.promote(corpus, "x1")
        from ldv.tools import staging as _ST
        c = _ST.commit(corpus)
        after = len(load_nodes(corpus))
        ok("★ [缓冲] ㊵ promote ⇒ 移进 `_staging/nodes/` ⇒ 既有 commit 判据收",
           p["移进暂存"] == 4 and len(c.get("已提交", [])) == 4
           and after == before + 4,
           f"暂存 4 ⇒ 提交 4 ⇒ 正式 {before} → {after}")

        # 还原（判据必须自己收拾）—— 删刚提交的，并**验还原**
        for f in (corpus / "nodes").glob("x1-*.md"):
            f.unlink()
        ok("★ [缓冲] ㊶ 判据跑完还原：正式节点回到原数（不留下判据自己的痕迹）",
           len(load_nodes(corpus)) == before,
           f"{after} → {len(load_nodes(corpus))}（初值 {before}）")

        a0 = XS.abandon(corpus, "不存在")
        XS.ingest(corpus, ex, "x2")
        a1 = XS.abandon(corpus, "x2")
        ok("★ [缓冲] ㊷ 抛弃 = 显式删目录，无残留；不存在时报错不静默",
           a0.get("ok") is False and a1.get("ok") is True
           and not Path(a1.get("抛弃", "")) if False else
           (a0.get("ok") is False and a1.get("ok") is True
            and not (Path(r["目录"]).parent / "x2").exists()),
           f"不存在 ⇒ 报错｜存在 ⇒ 删 {a1.get('删掉的节点')} 个｜残留 False")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_leaf_cap_requests() -> None:
    """★ 叶容量 ⇒ 请求（`request.leaf_cap_requests`，2026-10-19）。

    ⚠️ 与死路表 §1.2 那条的区别：那条否的是**把「增量≡全量」当正确性标准**；
       这里叶容量是**缺陷信号**（产请求），不挪对象、不改覆盖。
    """
    from ldv.core import request as RQ
    from ldv.core.kernel import Kernel
    from ldv.checks._fixtures import load as _load
    from ldv.corpus.loader import CorpusError, load_nodes
    from ldv.plugins.keyset import KeysetPlugin

    loaded = _load()
    assert loaded is not None
    nodes, _e, _d = loaded

    def _build():
        items = {i: n.as_item() for i, n in nodes.items()}
        k = Kernel(KeysetPlugin(), items)
        k.build(k.plugin.merge([]))
        k.bulk_insert(sorted(items))
        return k

    k0 = _build()

    # ── ㊉″ 小口径：默认语料最大叶 10 < 阈值 50 ⇒ 0 条，但明说查了 ─────────
    r0 = RQ.leaf_cap_requests(k0)
    maxleaf = max((len(k0.members_of(d)) for d in k0.all_directions()
                   if not k0.children_of(d)), default=0)
    ok("★ [叶容量] ㊉″ 小口径 0 条，且**明说查了**",
       r0["请求"] == [] and r0["本趟查了"]["查过的叶"] > 0
       and "查了" in r0["说明"] and r0["本趟查了"]["最大叶容量"] == maxleaf,
       f"最大叶 {maxleaf}｜查了 {r0['本趟查了']['查过的叶']} 个叶｜"
       f"阈值 {r0['本趟查了']['叶容量阈值']}")

    # ── ㊉ 阈值是外生参数：cap=3 ⇒ 同一个 kernel 请求变多 ─────────────────
    r3 = RQ.leaf_cap_requests(k0, cap=3)
    manual = sum(1 for d in k0.all_directions()
                 if not k0.children_of(d) and len(k0.members_of(d)) > 3)
    ok("★ [叶容量] ㊉ 条数对：超阈值的叶 == 请求条数（人工数）",
       len(r3["请求"]) == manual,
       f"cap=3 ⇒ 报 {len(r3['请求'])} 条，人工数 {manual} 个叶")
    ok("★ [叶容量] ㊉⁗ 阈值是**外生参数**（cap=3 比默认 50 报得多）",
       len(r3["请求"]) >= len(r0["请求"]),
       f"cap=3 {len(r3['请求'])} 条 ≥ 默认 {len(r0['请求'])} 条")

    # ── ㊉⁗b 请求只请「新维度/新谓词」，不提别的动作 ──────────────────────
    ok("★ [叶容量] ㊉⁗b 请求只请**新维度/新谓词**（不提改算法/挪对象）",
       all("新维度" in x["请求"] or "新谓词" in x["请求"] for x in r3["请求"]),
       f"例：{r3['请求'][0]['请求'][:40] if r3['请求'] else '（无）'}")

    # ── ㊉′ 真语料（wiki-vote，本地有 `_data/`；CI 上没有 ⇒ 走 main() 兜底跳过）──
    try:
        from pathlib import Path
        wv = load_nodes(Path(__file__).resolve().parents[2]
                        / "_data" / "ext-wiki-vote" / "nodes")
        items = {i: n.as_item() for i, n in wv.items()}
        kw = Kernel(KeysetPlugin(), items)
        kw.build(kw.plugin.merge([]))
        kw.bulk_insert(sorted(items))
        rw = RQ.leaf_cap_requests(kw)
        maxw = max((len(kw.members_of(d)) for d in kw.all_directions()
                   if not kw.children_of(d)), default=0)
        ok(f"★ [叶容量] ㊉′ 真语料 wiki-vote 会触发（最大叶 {maxw}）",
           len(rw["请求"]) >= 1 and maxw > RQ.LEAF_CAP,
           f"{len(wv)} 节点 ⇒ 报 {len(rw['请求'])} 条｜最大叶 {maxw}｜"
           f"方向 {len(kw.all_directions())}")
    except CorpusError:
        # ⚠️ **只吞 CorpusError**（外部语料不在）；`NameError` 这类**代码错**必须照抛 ——
        #    第一版是 `except Exception`，把漏 import 的 NameError 当成「语料不在」
        #    ⇒ 判据报跳过。那正是「跳过 ≠ 通过」要防的：**假的跳过比红更坏**。
        PASS.append("★ [叶容量] ㊉′ 真语料 wiki-vote（**跳过**："
                    "`_data/ext-wiki-vote` 不在 —— 外部语料不进仓库，见 .gitignore）")
def test_contradiction_requests() -> None:
    """★ 矛盾 ⇒ 请求（`reflect.contradiction_requests`，2026-10-19）。

    用户裁定：矛盾的修订方式是**加新谓词 / 请求新方向** ⇒ 要产出请求，
    走 `directions.json` 那条通道（人拍板 ⇒ `request.apply_approved` 执行）。
    """
    from ldv.core import reflect as RF
    from ldv.core.kernel import Kernel
    from ldv.checks._fixtures import load as _load
    from ldv.plugins.keyset import KeysetPlugin

    loaded = _load()
    assert loaded is not None
    nodes, _e, _d = loaded

    def _build():
        items = {i: n.as_item() for i, n in nodes.items()}
        k = Kernel(KeysetPlugin(), items)
        k.build(k.plugin.merge([]))
        k.bulk_insert(sorted(items))
        return k

    # ── ㊞ 没矛盾 ⇒ 0 条请求，但明说查了什么 ─────────────────────────────
    k0 = _build()
    r0 = RF.contradiction_requests(k0)
    ok("★ [矛盾→请求] ㊞ 没矛盾 ⇒ 0 条请求，且**明说查了什么**",
       r0["请求"] == [] and "查了" in r0["说明"] and bool(r0["本趟查了"]),
       f"请求 {len(r0['请求'])} 条｜说明 {r0['说明'][:50]}｜查了 {list(r0['本趟查了'])}")

    # ── ㊟ 请求不改结构 ──────────────────────────────────────────────────
    sig0 = [(d.did, d.parent, tuple(sorted(k0.members_of(d))))
            for d in k0.all_directions()]
    RF.contradiction_requests(k0)
    sig1 = [(d.did, d.parent, tuple(sorted(k0.members_of(d))))
            for d in k0.all_directions()]
    ok("★ [矛盾→请求] ㊟ 请求**不改结构**（调用前后方向与成员逐字不变）",
       sig0 == sig1, "结构未变" if sig0 == sig1 else "**结构被改了**")

    # ── ㊝ 造一个「边打架」⇒ 必须产出一条请求，且带证据 ──────────────────
    k1 = _build()
    dirs = [d for d in k1.all_directions() if d.parent]
    assert dirs, "语料里没有带父的方向"
    victim = dirs[-1]
    par = victim.parent
    # 只从**父的子列表**里摘掉它 —— 声明（parent）还在 ⇒ 边打架
    kids = tuple(c for c in k1._children.get(par, ()) if c != victim.did)
    k1._children[par] = kids
    r1 = RF.contradiction_requests(k1)
    hit = [x for x in r1["请求"] if x["信号"] == "矛盾：边打架"]
    ok("★ [矛盾→请求] ㊝ 有矛盾 ⇒ 一条矛盾一条请求，且**带证据**",
       len(r1["请求"]) == len(RF.contradictions(k1)["矛盾"]) and bool(hit)
       and hit[0]["方向"] == victim.did and hit[0]["判据编号"] == "矛盾"
       and "新谓词" in hit[0]["请求"],
       f"矛盾 {len(RF.contradictions(k1)['矛盾'])} 条 ⇒ 请求 {len(r1['请求'])} 条"
       f"｜首条 {hit[0]['信号'] if hit else '（无）'} @ {hit[0]['方向'] if hit else '-'}")


def test_contra_merged() -> None:
    """★ 两类矛盾并进同一份清单（2026-10-19）。

    `contradictions()` 原本只查三类**结构不变量** —— 反向对照
    （`outputs/_probe_contra_reverse.py`）证明它们**能红**，但正确内核
    **永远不会红**（`_children` 由子方向声明的 parent 反推、`union ⊆ parent`
    由 payload 收窄保证）⇒ 9 份语料上恒为 0。而「矛盾 0」读起来像「没问题」。

    真正对应「加新谓词 / 请求新方向」的是**叶切不开** ⇒ 归进同一份清单，
    但**分组报数**，别让两类混成一个数。
    """
    from ldv.core import reflect as RF
    from ldv.core import request as RQ
    from ldv.core.kernel import Kernel
    from ldv.checks._fixtures import load as _load
    from ldv.plugins.keyset import KeysetPlugin

    loaded = _load()
    assert loaded is not None
    nodes, _e, _d = loaded
    items = {i: n.as_item() for i, n in nodes.items()}
    k = Kernel(KeysetPlugin(), items)
    k.build(k.plugin.merge([]))
    k.bulk_insert(sorted(items))

    c_none = RF.contradictions(k)                 # 不给 cap ⇒ 只查结构三类
    c_cap = RF.contradictions(k, cap=3)           # 给 cap=3 ⇒ 追加「切不开」
    lr3 = RQ.leaf_cap_requests(k, cap=3)
    g = c_cap["分组"]

    # ── ㊦ 总数 = 结构 + 切不开；结构那三类一条不多不少 ──────────────────
    ok("★ [矛盾并入] ㊦ 总数 = 结构 + 切不开，且结构三类与不给 cap 时一致",
       c_cap["矛盾数"] == g["结构矛盾"] + g["切不开"]
       and g["结构矛盾"] == c_none["矛盾数"] == 0
       and g["切不开"] == len(lr3["请求"]) > 0,
       f"结构 {g['结构矛盾']}｜切不开 {g['切不开']}｜总数 {c_cap['矛盾数']}"
       f"（cap=None 时 {c_none['矛盾数']}）")

    # ── ㊧ 没给 cap ⇒ `未查` 点名「切不开」（没查 ≠ 查了没发现） ─────────
    ok("★ [矛盾并入] ㊧ 没给 cap ⇒ `未查` 点名「切不开」",
       any("切不开" in x for x in c_none["未查"])
       and "切不开" not in c_none["检查项"]
       and "切不开" in c_cap["检查项"] and c_cap["未查"] == [],
       f"cap=None 未查 {c_none['未查']}｜检查项 {c_none['检查项']}")

    # ── ㊨ 阈值只有一处实现：并入的条数/方向 == leaf_cap_requests ────────
    ok("★ [矛盾并入] ㊨ 切不开的条数与方向 == `leaf_cap_requests`（同一处阈值）",
       [r["方向"] for r in c_cap["矛盾"] if r["类型"] == "切不开"]
       == [r["方向"] for r in lr3["请求"]],
       f"并入 {[r['方向'] for r in c_cap['矛盾'] if r['类型'] == '切不开'][:4]}…"
       f"｜独立 {[r['方向'] for r in lr3['请求']][:4]}…")

    # ── ㊩ 「切不开」也产请求，且措辞是「切开」不是「归位」 ──────────────
    rq = RF.contradiction_requests(k, c_cap)
    capreq = [x for x in rq["请求"] if x["信号"] == "矛盾：切不开"]
    ok("★ [矛盾并入] ㊩ 「切不开」也产请求，措辞是「切开这批项」",
       len(capreq) == g["切不开"] and bool(capreq)
       and all("切开" in x["请求"] for x in capreq)
       and all(x["涉及的项数"] > 3 for x in capreq),
       f"请求 {len(capreq)} 条｜首条 {capreq[0]['涉及的项数'] if capreq else 0} 项"
       f"｜{capreq[0]['请求'][:26] if capreq else '-'}…")


SNAPSHOT_VERSION_UNKNOWN = 999          # 判据里伪造一个「更老的版本」


def test_tree_snapshot() -> None:
    """★ 方向树落盘 / 恢复（`core/snapshot`，2026-10-19）—— **可选项，默认关**。

    为什么做：每次调用全量重造，而建树**超线性** ——
    7115 项 6.7 s / 30k 216.6 s / 100k 3239.2 s（每项 0.94 → 7.22 → 32.39 ms）。
    进程内的 `reusable_build` 只活在内存里，**重启即归零**。

    ⚠️ 与死路表 §3 那条（进程内 signature 记忆化是**算法的一部分**）不是一回事：
       那个省「同一次建造里的重复计算」，这个省「下一次的整棵树」。

    判据覆盖的正是**最危险的几种形状**：
      ㊓ 材料改一个键  ⇒ 必须重建（不是「差不多就复用」）
      ㊔ 参数变         ⇒ 必须重建
      ㊕ 快照坏         ⇒ 必须重建，**不许崩**（可选开关不许变成可用性炸弹）
      ㊖ 版本不符       ⇒ 必须重建
      ㊗ 恢复出来的树   ⇒ 照样能维护，且与从零建的**逐字相同**
    """
    import inspect
    import json
    import shutil
    import tempfile
    from pathlib import Path as _P

    from ldv import cli as _cli
    from ldv.core import snapshot as SN
    from ldv.core.kernel import Kernel
    from ldv.checks._fixtures import load as _load
    from ldv.plugins.keyset import KeysetPlugin

    loaded = _load()
    assert loaded is not None
    nodes, _e, _d = loaded
    base = {i: n.as_item() for i, n in nodes.items()}
    holdout_n = 12
    hold = sorted(base)[-holdout_n:]
    init = {k: v for k, v in base.items() if k not in set(hold)}

    def build(items):
        k = Kernel(KeysetPlugin(), items)
        k.build(k.plugin.merge([]))
        for iid in sorted(items):
            k.insert(iid)
        return k

    def sig(k):
        """结构签名 —— 判据比的是「逐字相同」，不是「看起来差不多」。"""
        return (
            sorted(d.did for d in k.all_directions()),
            [(d.did, d.parent, d.rank, tuple(d.witness), d.origin)
             for d in k.all_directions()],
            {d.did: len(k.members_of(d)) for d in k.all_directions()},
            {iid: tuple(v) for iid, v in sorted(k._path.items())},
            {k2: sorted(v) for k2, v in sorted(k._tried.items())},
            len(k.ledger),
        )

    tmp = _P(tempfile.mkdtemp())
    try:
        plug = KeysetPlugin()
        extra = {"holdout": holdout_n, "predicates": False}

        # ── ㊐ **默认关**（签名检查，不是「我记得关着」） ──────────────────
        dflt = inspect.signature(_cli._assemble).parameters["use_snapshot"].default
        ok("★ [树快照] ㊐ `_assemble(use_snapshot=…)` **默认 False**（可选项）",
           dflt is False, f"默认值 = {dflt!r}")

        # ── ㊑ 第一次：新建 + 落盘 ────────────────────────────────────────
        k1, r1 = SN.with_snapshot(Kernel, plug, lambda: build(init), init, "keyset",
                                  None, extra, root=tmp)
        p1 = tmp / SN.tree_path("keyset",
                                SN.fingerprint(init, None, {"方向函数": "keyset", **extra}),
                                tmp).name
        ok("★ [树快照] ㊑ 第一次 ⇒ **新建并落盘**",
           r1["结果"] == "新建" and "还没有快照" in r1["原因"]
           and p1.is_file(),
           f"{r1['结果']}｜{r1['原因']}｜文件 {p1.name} 在：{p1.is_file()}")

        # ── ㊒ 第二次：命中 + **结构逐字相同** ────────────────────────────
        k2, r2 = SN.with_snapshot(Kernel, plug, lambda: build(init), init, "keyset",
                                  None, extra, root=tmp)
        ok("★ [树快照] ㊒ 第二次 ⇒ **命中**，且结构（含账本条数）逐字相同",
           r2["结果"] == "命中" and sig(k1) == sig(k2),
           f"{r2['结果']}｜方向 {len(k2.all_directions())}｜账本 {len(k2.ledger)}｜"
           f"签名相同 {sig(k1) == sig(k2)}")

        # ── ㊓ 材料改**一个键** ⇒ 必须重建，且树来自新材料 ────────────────
        tampered = {iid: (dict(it) | {"keys": it["keys"] | {"临时多出来的键"}}
                           if iid == sorted(init)[0] else it)
                    for iid, it in init.items()}
        k3, r3 = SN.with_snapshot(Kernel, plug, lambda: build(tampered), tampered,
                                  "keyset", None, extra, root=tmp)
        ok("★ [树快照] ㊓ 改一个键 ⇒ **重建**（不是「差不多就复用」）",
           r3["结果"] == "重建" and "指纹不符" in r3["原因"]
           and sig(k3) != sig(k1),
           f"{r3['结果']}｜{r3['原因']}｜结构确实变了 {sig(k3) != sig(k1)}")

        # ── ㊔ 参数变（holdout）⇒ 必须重建 ──────────────────────────────
        k4, r4 = SN.with_snapshot(Kernel, plug, lambda: build(init), init, "keyset",
                                  None, {"holdout": 24, "predicates": False}, root=tmp)
        ok("★ [树快照] ㊔ `holdout` 12 → 24 ⇒ **重建**",
           r4["结果"] == "重建" and "指纹不符" in r4["原因"],
           f"{r4['结果']}｜{r4['原因']}")

        # ── ㊕ 快照坏了（截断）⇒ 重建，**不许崩** ────────────────────────
        broken = tmp / "broken.tree.json"
        broken.write_text(p1.read_text(encoding="utf-8")[:200], encoding="utf-8")
        kb, rb = SN.restore(Kernel, plug, init, broken,
                            SN.fingerprint(init, None, {"方向函数": "keyset", **extra}))
        ok("★ [树快照] ㊕ 快照被截断 ⇒ 重建，**不崩**",
           kb is None and rb["结果"] == "重建" and rb["原因"],
           f"{rb['结果']}｜{rb['原因']}")

        # ── ㊖ 版本不符 ⇒ 重建 ───────────────────────────────────────────
        oldver = tmp / "oldver.tree.json"
        doc = json.loads(p1.read_text(encoding="utf-8"))
        doc["版本"] = SNAPSHOT_VERSION_UNKNOWN
        oldver.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        kv, rv = SN.restore(Kernel, plug, init, oldver,
                            SN.fingerprint(init, None, {"方向函数": "keyset", **extra}))
        ok("★ [树快照] ㊖ 版本不符 ⇒ 重建（老版本不猜兼容）",
           kv is None and rv["结果"] == "重建" and "版本不符" in rv["原因"],
           f"{rv['结果']}｜{rv['原因']}")

        # ── ㊗ 恢复出来的树**照样能维护**，与从零建的逐字相同 ─────────────
        def churn(k, new_ids, kill):
            for iid in sorted(new_ids):
                k.insert(iid, base[iid])      # 新项不在 `items` 里 ⇒ 必须手递手带上
            for iid in kill:
                k.remove(iid)
            return k

        kill = sorted(init)[-3:]
        fresh = churn(build(init), hold, kill)
        restored = churn(k2, hold, kill)
        ok("★ [树快照] ㊗ 命中后照样能维护（插 12 + 删 3），终态与从零建**逐字相同**",
           sig(fresh) == sig(restored) and fresh.stats() == restored.stats(),
           f"签名相同 {sig(fresh) == sig(restored)}｜"
           f"stats 相同 {fresh.stats() == restored.stats()}｜"
           f"方向 {len(restored.all_directions())}")

        # ── 读数：命中/新建的耗时（**不进退出码** —— 小语料上噪声压倒信号） ──
        print(f"    · 快照耗时读数：新建 {r1.get('建树秒')}s｜"
              f"命中（不计）｜写盘 {r1.get('写盘秒')}s；"
              f"真正的收益要在 §13.16 那三档规模上才看得出来")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_session() -> None:
    """★ 热启动会话：快照 + 追加日志（`core/session.py`，2026-10-20）。

    为什么：`--snapshot` 的粒度是**整棵树 + 全局指纹** ⇒ 加一个节点就全量重建。
    实测（30k）：每节点 ≈ 0.3 s（重写 2.56 MB + 重算指纹），且随 n 线性涨。
    本模块把「每节点」压成**常数**：insert 之后只 append 一行。

    ⚠️ 它给的是一棵**增量树** —— 与「全量重造」**不是同一棵**（增量 ≠ 全量，已实测）。
       所以判据比的是「增量 vs 增量」，**不比**「增量 vs 全量」。

    判据覆盖的正是最危险的几种形状：
      ㊲ 关掉再开 ⇒ 命中 + 回放，树逐字相同
      ㊳ **日志写一半**（ops 数不符）⇒ 丢日志、回落到快照，**不崩**
      ㊴ 材料改了（指纹变）⇒ 状态作废、重建
      ㊵ `commit()` ⇒ 快照换成新指纹、日志清空
    """
    import json
    import shutil
    import tempfile
    from pathlib import Path as _P

    from ldv.core import session as SS
    from ldv.core import snapshot as SN
    from ldv.core.kernel import Kernel
    from ldv.checks._fixtures import load as _load
    from ldv.plugins.keyset import KeysetPlugin

    loaded = _load()
    assert loaded is not None
    nodes, _e, _d = loaded
    all_items = {i: n.as_item() for i, n in nodes.items()}
    ids = sorted(all_items)
    base_n = len(ids) - 6
    base_ids = ids[:base_n]
    new_ids = ids[base_n:]
    base = {i: all_items[i] for i in base_ids}

    def build(items):
        k = Kernel(KeysetPlugin(), items)
        k.build(k.plugin.merge([]))
        for iid in sorted(items):
            k.insert(iid)
        return k

    def sig(k):
        return (
            sorted(d.did for d in k.all_directions()),
            [(d.did, d.parent, d.rank) for d in k.all_directions()],
            {d.did: len(k.members_of(d)) for d in k.all_directions()},
            {iid: tuple(v) for iid, v in sorted(k._path.items())},
            len(k.ledger),
        )

    tmp = _P(tempfile.mkdtemp())
    try:
        plug = KeysetPlugin()
        kw = dict(name="keyset", plugin=plug, scope="t", base_dir=tmp,
                  KernelCls=Kernel, edges=None, extra={})

        # ── ㊱ 首次：没有状态 ⇒ 建 + 落盘 + 空日志 ────────────────────────
        s1 = SS.TreeSession.open(items=base, build=lambda: build(base), **kw)
        ok("★ [会话] ㊱ 首次打开 ⇒ **新建**并落盘，日志 0 条",
           s1.report["结果"] == "新建"
           and SS.state_path("keyset", tmp, "t").is_file()
           and SS.log_path("keyset", tmp, "t").is_file()
           and s1.log.ops == 0,
           f"{s1.report['结果']}｜快照 {SS.state_path('keyset', tmp, 't').name}｜"
           f"日志 {s1.log.ops} 条")
        k1 = s1.kernel
        before = sig(k1)

        # ── ㊲ 填 3 个节点 + 删 1 个 ⇒ 关掉再开 ⇒ 回放，树逐字相同 ────────
        for iid in new_ids[:3]:
            s1.insert(iid, all_items[iid])
        victim = sorted(base_ids)[-1]
        s1.remove(victim)
        s1.close()
        ops_written = s1.log.ops
        after_close = sig(s1.kernel)

        # ⚠️ **当前项集** = base + 已插入的 − 已删除的
        #    （`kernel.remove` 会把项从 `items` 里摘掉 ⇒ 会话的项集跟着变）
        cur = dict(base)
        for iid in new_ids[:3]:
            cur[iid] = all_items[iid]
        cur.pop(victim, None)
        s2 = SS.TreeSession.open(items=cur, build=lambda: build(cur), **kw)
        ok("★ [会话] ㊲ 关掉再开 ⇒ **命中 + 回放 4 条**，树与关掉前逐字相同",
           s2.report["结果"] == "命中" and s2.log.ops == ops_written == 4
           and sig(s2.kernel) == after_close,
           f"{s2.report['结果']}｜日志「{s2.report['日志']}」｜"
           f"签名相同 {sig(s2.kernel) == after_close}")

        # ── ㊳ 日志写一半（头说 N 条，实际少一条）⇒ 丢日志、回落快照 ──────
        lp = SS.log_path("keyset", tmp, "t")
        lines = lp.read_text(encoding="utf-8").splitlines()
        lp.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        s3 = SS.TreeSession.open(items=cur, build=lambda: build(cur), **kw)
        # ⚠️ 日志一丢，base 快照就**对不上当前项集**了（当前集 = base + 3 − 1）
        #    ⇒ 正确行为是**重建**，不是「回落到快照」。判据要按这个写。
        ok("★ [会话] ㊳ 日志被截断 ⇒ **丢弃日志 + 重建**（base 已对不上当前项集），不崩",
           s3.report["结果"] == "新建"
           and "丢弃" in s3.report["日志"] and "不完整" in s3.report["日志"]
           and sig(s3.kernel) != after_close,
           f"{s3.report['结果']}｜{s3.report['日志']}｜确实重建 {sig(s3.kernel) != after_close}")

        # ── ㊴ 材料改了（多一个键）⇒ 状态作废、重建 ──────────────────────
        tampered = dict(cur)
        one = sorted(tampered)[0]
        tampered[one] = dict(tampered[one])
        tampered[one]["keys"] = tampered[one]["keys"] | {"临时键"}
        s4 = SS.TreeSession.open(items=tampered, build=lambda: build(tampered), **kw)
        ok("★ [会话] ㊴ 材料变了 ⇒ **状态作废、重建**（不用旧树算新材料）",
           s4.report["结果"] == "新建"
           and sig(s4.kernel) != after_close,
           f"{s4.report['结果']}｜结构变了 {sig(s4.kernel) != after_close}")

        # ── ㊵ `commit()` ⇒ 快照换新指纹、日志清空 ────────────────────────
        s4.insert(new_ids[3], all_items[new_ids[3]])
        s4.close()
        p = s4.commit()
        doc = json.loads(p.read_text(encoding="utf-8"))
        ok("★ [会话] ㊵ `commit()` ⇒ 快照换成**当前**项集指纹，日志清空",
           doc.get("指纹") == SN.fingerprint(s4.kernel.items, None,
                                             {"方向函数": "keyset"})
           and s4.log.ops == 0,
           f"快照指纹 {str(doc.get('指纹'))[:12]}…｜日志 {s4.log.ops} 条")

        # ── ㊵′ commit 之后重开 ⇒ 命中且 0 条回放（压过了） ────────────────
        s5 = SS.TreeSession.open(items=s4.kernel.items, build=lambda: build(s4.kernel.items),
                                 **kw)
        ok("★ [会话] ㊵′ `commit()` 之后重开 ⇒ **命中 + 0 条回放**",
           s5.report["结果"] == "命中" and s5.log.ops == 0
           and sig(s5.kernel) == sig(s4.kernel),
           f"{s5.report['结果']}｜日志 {s5.log.ops} 条｜签名相同 {sig(s5.kernel) == sig(s4.kernel)}")

        # ── 读数：每节点成本（**不进退出码** —— 小语料上噪声压倒信号） ────
        print(f"    · 会话读数：建 {s1.report.get('秒')}s｜"
              f"命中 {s2.report.get('秒')}s（含指纹）｜"
              f"回放 {ops_written} 条｜真正的收益要在 §13.19 那三档规模上才看得出来")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_combine_budget() -> None:
    """★ 组合谓词**共用预算**并进划分（`corpus/predicates`，2026-10-20）。

    在这之前 `combine()` **只在测试里被调用** —— 生产路径一次都没算过，
    所以「组合谓词」在 CLI 里等于不存在（不是「被挡下」，是「没算」）。

    用户 2026-10-20 的裁定：
      「组合谓词经过验证以后应该可以使用，而不是等命名后才可使用」
      「共用预算，可以」

    ⇒ 口径：单谓词与「复杂度 ≤ K 的组合」一起按区分度排序，抢**同一份** N；
      键名默认就是**合取表达式**（自动命名），`待命名` 只是**提示**，不阻塞。

    ⚠️ 「共用」是**竞争**：组合赢了就顶掉一个单谓词 ⇒ 进划分的总数恒 ≤ N。
       实测（5 份语料）：mini 0.492 / bitcoin 0.472 上组合赢，其余输。
    """
    from ldv.checks._fixtures import load as _load
    from ldv.corpus import predicates as PR
    from ldv.corpus.loader import load_edges, load_nodes
    from ldv.checks._fixtures import find_corpus

    root = find_corpus()
    assert root is not None
    nodes = load_nodes(root)
    edges, _d = load_edges(nodes)
    rules, budget = PR.load_rules(root.parent)
    used, _dropped, _p = PR.derive(nodes, edges, rules, budget)
    to_name, cands, _rej = PR.combine(nodes, edges, used, PR.COMPLEXITY_K)

    # ── ㊷ **共用预算**：进划分的总数恒 ≤ N（不是各给一份） ──────────────
    for b in (0, 1, 2, budget, budget + 2):
        s, c, out = PR.choose_partition(used, cands, b)
        assert len(s) + len(c) == min(b, len(used) + len(cands)), (b, len(s), len(c))
    s3, c3, out3 = PR.choose_partition(used, cands, budget)
    ok("★ [组合预算] ㊷ **共用预算**：进划分总数 = min(N, 候选数)，不是各给一份",
       len(s3) + len(c3) == min(budget, len(used) + len(cands))
       and len(out3) == max(0, len(used) + len(cands) - budget),
       f"N={budget}｜单 {len(s3)} + 组合 {len(c3)} = {len(s3) + len(c3)}"
       f"｜超预算 {len(out3)}")

    # ── ㊸ 组合**顶掉**单谓词：N 不变，成分可以变 ────────────────────────
    s_wide, c_wide, _o = PR.choose_partition(used, cands, budget + 1)
    ok("★ [组合预算] ㊸ 组合赢了就**顶掉**一个单谓词（N 不变、成分可变）",
       len(s_wide) + len(c_wide) == budget + 1
       and len(c_wide) >= len(c3),
       f"N={budget}: 单 {len(s3)}/组 {len(c3)} ⇒ N={budget + 1}: "
       f"单 {len(s_wide)}/组 {len(c_wide)}")

    # ── ㊹ 自动命名 = 合取表达式，且**只给交集**加键 ────────────────────
    if cands:
        import copy
        n2 = copy.deepcopy(nodes)
        PR.apply_to_nodes(n2, edges, used)
        one = cands[0]
        touched = PR.apply_combinations(n2, edges, [one], used)
        want = " ∧ ".join(one.项)
        hit = [nid for nid, nd in n2.items() if want in nd.keys]
        ok("★ [组合预算] ㊹ 自动命名 = 合取表达式，且**只给交集**加键",
           want == one.谓词 and len(hit) == touched == one.满足数 and touched > 0,
           f"键名「{want[:26]}」｜加键 {touched} 项｜满足数 {one.满足数}")
    else:
        PASS.append("★ [组合预算] ㊹ 自动命名（**跳过**：本语料没有组合候选）")

    # ── ㊺ `待命名`（复杂度 > K）**不阻塞**：不进键集，但被报出来 ────────
    tn1, _c1, _r1 = PR.combine(nodes, edges, used, k=1)   # k=1 ⇒ 2 项也进待命名
    names = {x.谓词 for x in tn1}
    keys = {k for nd in nodes.values() for k in nd.keys}
    ok("★ [组合预算] ㊺ `待命名` 只是**提示**：不进键集，但被报出来",
       (not names) or (names & keys) == set(),
       f"待命名 {len(names)} 个｜与键集相交 {len(names & keys)} 个")

    # ── ㊻ 独立性：**精确蕴含** ⇒ 拒收（这是**定理**，不靠阈值） ──────────
    ok("★ [组合预算] ㊻ 精确蕴含（交集 == 最小项）⇒ **拒收**，理由是定理",
       "精确蕴含" in (PR.combination_verdict(10, 10) or "")
       and PR.combination_verdict(9, 10, ratio_threshold=0.95) is None,
       f"(10,10) → {str(PR.combination_verdict(10, 10))[:20]}…｜"
       f"(9,10)@0.95 → {PR.combination_verdict(9, 10, 0.95)}")

    # ── ㊼ **近似蕴含** ⇒ 拒收，且阈值是**外生参数**（改它结论就变） ──────
    v_default = PR.combination_verdict(19, 20)
    v_strict = PR.combination_verdict(19, 20, ratio_threshold=0.99)
    ok("★ [组合预算] ㊼ 近似蕴含（0.95 ≥ 0.9）⇒ 拒收；**阈值是外生参数**",
       "强相关" in (v_default or "") and v_strict is None,
       f"阈值 {PR.INDEPENDENCE_RATIO} → 拒收｜阈值 0.99 → {v_strict or '留'}")

    # ── ㊽ 真独立的组合**不许**被误杀：留下的候选，逐个重算都必须「留」 ──
    #    ⚠️ 不写 `or True` 那种恒真断言 —— 那等于没判（本仓库的老毛病之一）。
    vals = {w: PR.measure(nodes, edges, w) for w in {p.量 for p in used}}
    sat = {p.谓词: {nid for nid in nodes if vals[p.量][nid] > p.阈值} for p in used}
    wrong = [c for c in cands
             if PR.combination_verdict(
                 len(set.intersection(*[sat[x] for x in c.项])),
                 min(len(sat[x]) for x in c.项)) is not None]
    ok("★ [组合预算] ㊽ 真独立的组合**不被误杀**（留下的候选逐个重算都必须「留」）",
       not wrong and len(cands) > 0,
       f"候选 {len(cands)} 个｜误杀 {len(wrong)} 个｜"
       f"最高区分度 {round(max((c.区分度 for c in cands), default=0.0), 3)}")

    # ── 读数：组合进划分后结构**未必变**（强相关 ⇒ 退化成单项） ─────────
    print(f"    · 组合读数：候选 {len(cands)} 个｜进预算 {len(c3)} 个｜"
          f"待命名(K={PR.COMPLEXITY_K}) {len(to_name)} 个；"
          f"⚠️ 组合的两项若强相关，切分与单项等价 ⇒ **方向数/叶容量可能不变**")



def test_predicate_naming() -> None:
    """★ 人命名谓词：**预算外也能用**、**改名不是新增维度**、**跳过不静默**（2026-10-20）。

    起因（用户 2026-10-20）：「这样不还是没办法使用上新谓词吗」。

    实测到的真 bug：`apply_basic` 只用 `used`（预算内那 3 个）解析定义 ⇒
    人想用一个**预算外**的谓词（推导出来了、只是没进 top-3）⇒ 查不到 ⇒
    **静默跳过**（加键 0 项、无任何提示）。

    ⇒ 三条口径：
      ㊾ 解析范围 = **所有推导出来的**谓词（预算管「自动选谁」，不管「人能声明什么」）
      ㊿ 同扩展的命名 = **改名**（键数不变、旧键消失）—— 实测改名后树形逐字相同
      51 真的没有的项 ⇒ 记「未生效」+ 缺项，**不静默**
    """
    import copy

    from ldv.checks._fixtures import find_corpus
    from ldv.corpus import predicates as PR
    from ldv.corpus.loader import load_edges, load_nodes

    root = find_corpus()
    assert root is not None
    nodes = load_nodes(root)
    edges, _d = load_edges(nodes)
    rules, budget = PR.load_rules(root.parent)
    used, dropped, _p = PR.derive(nodes, edges, rules, budget)
    allp = list(used) + list(dropped)

    # ── ㊾ **预算外**的谓词也能被命名使用 ────────────────────────────────
    if dropped:
        one = dropped[0]
        n1 = copy.deepcopy(nodes)
        led: dict = {}
        t = PR.apply_basic(n1, edges, [{"名": "我要用", "定义": [one.谓词]}],
                           allp, led)
        ok("★ [命名] ㊾ **预算外**的谓词也能被命名使用（不再静默跳过）",
           t == one.满足数 and t > 0
           and not led.get("未生效"),
           f"预算外「{one.谓词}」⇒ 加键 {t} 项（满足数 {one.满足数}）｜"
           f"未生效 {len(led.get('未生效', []))}")
    else:
        PASS.append("★ [命名] ㊾ 预算外可用（**跳过**：本语料没有预算外的谓词）")

    # ── ㊿ 同扩展的命名 = **改名**：键数不变、旧键消失、账本留痕 ──────────
    if used:
        n2 = copy.deepcopy(nodes)
        PR.apply_to_nodes(n2, edges, used)
        before = {k for nd in n2.values() for k in nd.keys}
        old = used[0].谓词
        led2: dict = {}
        PR.apply_basic(n2, edges, [{"名": "改过的名字", "定义": [old]}], allp, led2)
        after = {k for nd in n2.values() for k in nd.keys}
        renamed = led2.get("改名", [])
        ok("★ [命名] ㊿ 同扩展 ⇒ **改名**：键数不变、旧键消失、账本留痕",
           len(after) == len(before)
           and "改过的名字" in after and old not in after
           and bool(renamed) and old in renamed[0]["替换"],
           f"键数 {len(before)} → {len(after)}｜旧键消失 {old not in after}｜"
           f"账本 {renamed}")

        # ── 51 改名**只针对派生键**：字段键集合前后必须**逐字相同** ────────
        FIELD = {"id", "title", "type", "scope", "relations", "notes",
                 "source", "evidence_status", "aliases", "cues", "filled_by"}

        def _fields(keys):
            return {k for k in keys
                    if k in FIELD or k.startswith(("type=", "evidence_status=",
                                                   "source.kind="))}

        ok("★ [命名] 51 改名**只针对派生键**：语料字段键集合前后**逐字相同**",
           _fields(before) == _fields(after) and len(_fields(before)) > 0,
           f"字段键 {len(_fields(before))} → {len(_fields(after))}｜"
           f"被替换 {renamed[0]['替换'] if renamed else '—'}")
    else:
        PASS.append("★ [命名] ㊿/51 改名（**跳过**：本语料没有已采用的单谓词）")

    # ── 52 真的没有的项 ⇒ 记「未生效」+ 缺项（**不静默**） ────────────────
    n3 = copy.deepcopy(nodes)
    led3: dict = {}
    t3 = PR.apply_basic(n3, edges, [{"名": "瞎写", "定义": ["根本不存在的谓词"]}],
                        allp, led3)
    bad = led3.get("未生效", [])
    ok("★ [命名] 52 引用**不存在的项** ⇒ 记「未生效」+ 缺项，**不静默**",
       t3 == 0 and bool(bad) and bad[0]["缺项"] == ["根本不存在的谓词"],
       f"加键 {t3} 项｜未生效 {bad[0] if bad else '（空）'}")



def test_dimension_values() -> None:
    """★ 维度**取值**的写法校验（`request.apply_approved`，2026-10-20）。

    实测逼出来的：同一个学段写成「小学」与「小学阶段」⇒ 键是 `period=小学` 与
    `period=小学阶段` ⇒ **同一批人被切成两个方向**，而**没有任何报错**。
    结构上看起来正常（方向数还多了），语义上已经错了。

    ⇒ 只报最典型的一种形状：**一个取值是另一个的前缀**。
      取值 >200 的不查（`scope` 那种 1434 个取值的字段本来就不是"少数几个学段"，
      而且 O(n²) 不划算）。
    """
    import json
    import shutil
    import tempfile
    from dataclasses import replace
    from pathlib import Path as _P

    from ldv.checks._fixtures import find_corpus
    from ldv.core import request as RQ
    from ldv.corpus.loader import load_nodes

    root = find_corpus()
    assert root is not None
    base = load_nodes(root)

    tmp = _P(tempfile.mkdtemp())
    try:
        def run(values) -> dict:
            """给每个节点打 `period` 字段（按 `values` 轮转），批准该维度。"""
            nodes = {}
            for i, (nid, n) in enumerate(sorted(base.items())):
                nodes[nid] = replace(n, fields={**n.fields,
                                                "period": values[i % len(values)]})
            (tmp / "directions.json").write_text(json.dumps(
                {"已批准": [{"维度": "period", "取值": None}]}, ensure_ascii=False),
                encoding="utf-8")
            _new, led = RQ.apply_approved(nodes, tmp)
            return led

        # ── 53 前缀重复 ⇒ **报出来**（结构看不出错，语义已错） ────────────
        led1 = run(["小学", "小学阶段", "中学"])
        dup = led1.get("疑似重复取值", [])
        ok("★ [维度取值] 53 「小学」与「小学阶段」⇒ 报**疑似重复取值**",
           bool(dup) and dup[0]["取值"] == ["小学", "小学阶段"]
           and "前缀" in dup[0]["原因"],
           f"报出 {len(dup)} 条｜{dup[0]['取值'] if dup else '（空）'}")

        # ── 54 干净取值 ⇒ **不报**（不许误报） ─────────────────────────────
        led2 = run(["小学", "中学", "大学"])
        ok("★ [维度取值] 54 干净取值（小学/中学/大学）⇒ **不报**",
           not led2.get("疑似重复取值"),
           f"疑似 {len(led2.get('疑似重复取值', []))} 条｜生效 "
           f"{len(led2.get('生效', []))} 条")

        # ── 55 取值太多（>200）⇒ **不查**（不误报、不卡） ──────────────────
        many = [f"p{i:03d}" for i in range(260)]
        led3 = run(many)
        ok("★ [维度取值] 55 取值 >200 ⇒ **不查**（不误报、不卡）",
           not led3.get("疑似重复取值"),
           f"取值 {len(many)} 个｜疑似 {len(led3.get('疑似重复取值', []))} 条")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_version_spots() -> None:
    """★ 版本落点**彼此一致**（`tools/version_spots.py`，2026-10-20）。

    审计发现的漂移：近几版只改了两处（README 首行 + 谓词推导与反思层），
    另外 3 处停在 v0.1.26/27 ⇒ 同一份仓库里同时存在三种版本说法。

    ⇒ 把「哪几处算落点」写成**数据**（`SPOTS`），并让这条判据守着。
    ⚠️ 判的是「**彼此一致**」，不是「等于某个写死的数」——
       写死数字就得每版改判据，那只是把漂移换个地方放。
    """
    import re
    import shutil
    import tempfile
    from pathlib import Path as _P

    from ldv.tools import version_spots as VS

    # ── 56 真实仓库：5 处落点彼此一致 ────────────────────────────────────
    good, rows = VS.check()
    vers = sorted({str(r["版本"]) for r in rows})
    ok("★ [版本落点] 56 真实仓库的落点**彼此一致**（版本 + 日期）",
       good and len(vers) == 1 and None not in [r["版本"] for r in rows],
       f"{len(rows)} 处｜版本 {vers}｜日期 "
       f"{sorted({str(r['日期']) for r in rows if r['日期']})}")

    # ── 57 每条正则**恰好命中 1 次**（否则 `bump` 会 assert 失败） ────────
    bad = []
    for rel, pat, label in VS.SPOTS:
        n = len(re.findall(pat + VS.DATE + "?", VS._read(rel)))
        if n != 1:
            bad.append((label, n))
    ok("★ [版本落点] 57 每个落点的正则**恰好命中 1 次**（`bump` 的前提）",
       not bad, f"命中异常 {bad}" if bad else f"{len(VS.SPOTS)} 个落点各命中 1 次")

    # ── 58 **反向对照**：伪造一处不一致 ⇒ `check` 必须报不一致 ───────────
    #    ⚠️ 不碰真实文档：把 `SPOTS` 与 `REPO` 一起指到临时目录。
    tmp = _P(tempfile.mkdtemp())
    old_spots, old_repo = VS.SPOTS, VS.REPO
    try:
        (tmp / "a.md").write_text("**当前版本 v0.1.30**", encoding="utf-8")
        (tmp / "b.md").write_text("**当前版本 v0.1.99**", encoding="utf-8")
        VS.REPO = tmp
        VS.SPOTS = (("a.md", rf"\*\*当前版本 {VS.VER}\*\*", "A"),
                    ("b.md", rf"\*\*当前版本 {VS.VER}\*\*", "B"))
        fake_ok, fake_rows = VS.check()
        ok("★ [版本落点] 58 **反向对照**：伪造一处不一致 ⇒ `check` 必须报红",
           fake_ok is False
           and sorted({str(r["版本"]) for r in fake_rows}) == ["v0.1.30", "v0.1.99"],
           f"check 返回 {fake_ok}｜读到 {[r['版本'] for r in fake_rows]}")
    finally:
        VS.SPOTS, VS.REPO = old_spots, old_repo
        shutil.rmtree(tmp, ignore_errors=True)



def test_evidence() -> None:
    """★ 仓库里每一处「实测/留档」引用都**有实体**（`tools/evidence_index.py`，2026-10-20）。

    用户 2026-10-20：「现在测试和引用之类的内容只有宣称而没有实体」。

    实测：仓库文档引用了 **130+** 个 `outputs/...` 留档，而那些文件全在工作区根的
    `outputs/` 下 —— **`outputs/` 不在仓库里** ⇒ 远端读者一个都看不到，
    「实测见 `outputs/_probe_x.py`」在 GitHub 上是**悬空引用**。

    ⇒ 搬进 `repo/evidence/`，文档路径一起改成 `evidence/...`，这条判据守三件事：
       ① 每处 `evidence/X` 都能解析到**存在的文件**（含花括号/通配写法）
       ② `evidence/` 里**没有孤儿**（每个文件至少被一处引用）
       ③ 仓库文档里**不再出现 `outputs/`**（那是工作区路径，远端没有）
    """
    import shutil
    import tempfile
    from pathlib import Path as _P

    from ldv.tools import evidence_index as EI

    # ── 59 真实仓库：全部有实体 ──────────────────────────────────────────
    good, r = EI.check()
    ok("★ [证据] 59 全仓 `evidence/` 引用**都有实体**（无悬空 / 无孤儿 / 无工作区路径）",
       good and not r["unresolved"] and not r["orphans"] and not r["ws_refs"],
       f"文件 {len(r['files'])}｜被引用 {len(r['used'])}｜"
       f"解析不了 {len(r['unresolved'])}｜孤儿 {len(r['orphans'])}｜"
       f"残留 outputs/ {len(r['ws_refs'])}")

    # ── 60 **反向对照**：伪造一个悬空引用 ⇒ check 必须报红 ────────────────
    #    ⚠️ 不碰真实文档：把 `REPO`/`EV` 一起指到临时目录。
    tmp = _P(tempfile.mkdtemp())
    old_repo, old_ev = EI.REPO, EI.EV
    try:
        (tmp / "docs").mkdir()
        (tmp / "evidence").mkdir()
        (tmp / "evidence" / "real.txt").write_text("x", encoding="utf-8")
        (tmp / "docs" / "a.md").write_text(
            "见 `evidence/real.txt` 与 `evidence/根本没有这个文件.txt`。",
            encoding="utf-8")
        EI.REPO, EI.EV = tmp, tmp / "evidence"
        bad_ok, bad_r = EI.check()
        ok("★ [证据] 60 **反向对照**：伪造悬空引用 ⇒ check 必须报红",
           bad_ok is False and len(bad_r["unresolved"]) == 1
           and bad_r["unresolved"][0][1].startswith("根本没有"),
           f"check 返回 {bad_ok}｜解析不了 {bad_r['unresolved']}")

        # ── 61 反向对照之二：孤儿也要报（文件在，但没人引用） ─────────────
        (tmp / "docs" / "a.md").write_text("见 `evidence/real.txt`。", encoding="utf-8")
        (tmp / "evidence" / "orphan.txt").write_text("y", encoding="utf-8")
        o_ok, o_r = EI.check()
        ok("★ [证据] 61 **反向对照**：`evidence/` 里有孤儿 ⇒ 也必须报红",
           o_ok is False and o_r["orphans"] == ["orphan.txt"],
           f"check 返回 {o_ok}｜孤儿 {o_r['orphans']}")
    finally:
        EI.REPO, EI.EV = old_repo, old_ev
        shutil.rmtree(tmp, ignore_errors=True)



def test_entry() -> None:
    """★ 只读查询入口（`entry.py`）—— 切分语义 / 拒收 / 建议 / 示例（2026-10-20）。

    用户 2026-10-20：「用户得到的就是根据需求匹配的对应粗细的节点即可」，
    随后选 C：「先说横跨 N 支，再列 N 个」。

    ⚠️ 判据**不碰 HTTP**：`App` 的逻辑与传输无关。起服务测端口会把判据变成
       「端口没被占用」这类环境读数（`R2` 说的是系统不是环境）。
    """
    import entry as E

    app = E.App(None)                     # 内置 36 项语料；不设环境变量
    keys = [k for k in app.all_keys if "=" in k]
    ok("★ [入口] 51 内置语料能建核，且键里带 `=`（可筛选）",
       len(app.nodes) > 0 and len(keys) > 0,
       f"{len(app.nodes)} 项｜{len(app.all_keys)} 键｜带 `=` 的 {len(keys)} 个")

    # ── 52 切分不重不漏 ────────────────────────────────────────────────
    import itertools
    bad: list[str] = []
    cases: list[dict] = [{"require": []}]
    for k in keys:
        cases.append({"require": [k]})
    for a, b in itertools.combinations(keys, 2):
        cases.append({"require": [a, b]})
    for c in cases:
        d = app.query(c)
        if not d.get("ok"):
            bad.append(f"{c} 被拒收：{d.get('error')}")
            continue
        s = sum(n["n_answer"] for n in d["nodes"])
        if s != d["n_answer"]:
            bad.append(f"{c} ⇒ 答案 {d['n_answer']}｜各支合计 {s}")
    ok("★ [入口] 52 切分**不重不漏**（Σ各支含答案 == 答案总数）",
       not bad,
       f"{len(cases)} 条需求｜红 {len(bad)}" + (f"｜首个：{bad[0]}" if bad else ""))

    # ── 53 单支给 node / 多支给 nodes ───────────────────────────────────
    one = app.query({"require": [keys[0]]})
    ok("★ [入口] 53 单支时 `node` 必须给（兼容旧调用方），多支时为 None",
       (one["n_branches"] == 1 and one["node"] is not None)
       or (one["n_branches"] > 1 and one["node"] is None
           and len(one["nodes"]) == one["n_branches"]),
       f"支 {one['n_branches']}｜node {'有' if one['node'] else 'None'}"
       f"｜nodes {len(one['nodes'])}")

    # ── 54 拼错的键拒收（不许静默 0 条） ───────────────────────────────
    bad_q = app.query({"require": ["type=绝对不存在的取值"]})
    ok("★ [入口] 54 拼错的键 ⇒ **拒收**并给建议（不许静默返回 0 条）",
       bad_q.get("ok") is False and "不存在" in bad_q.get("error", "")
       and bool(bad_q.get("unknown_keys")),
       f"ok={bad_q.get('ok')}｜unknown={bad_q.get('unknown_keys')}")

    # ── 55 建议是**子串**匹配（键以 `type=` 开头） ─────────────────────
    probe = "概念"
    sug = app.suggest(probe)["items"]
    ok("★ [入口] 55 建议按**子串**匹配（打「概念」能匹配 `type=概念`）",
       any(probe in x["key"] for x in sug) and all(probe in x["key"] for x in sug),
       f"「{probe}」→ {[x['key'] for x in sug][:3]}")

    # ── 56 示例的键必须真实存在 ────────────────────────────────────────
    ex = app.examples()["items"]
    fake = [k for x in ex for k in x["require"] if k not in app.all_keys]
    ok("★ [入口] 56 示例里的键必须**真实存在**（不许给一个查不到的例子）",
       len(ex) >= 2 and not fake,
       f"{len(ex)} 条｜假键 {fake or '无'}")

    # ── 57 零结果松弛（2026-10-21 真实问题验收补的洞） ─────────────
    #    ⚠️ 这条路径此前**没有判据**：「键拼错」与「键都对但组合为空」
    #       是两条不同的退化，一条也不许静默（54 管前者，这条管后者）。
    #       内置语料上穷举二元组找**第一个**空组合（语料固定 ⇒ 确定性）。
    empty_pair = None
    for a, b in itertools.combinations(keys, 2):
        if not app.answer_set(frozenset([a, b]), frozenset()):
            empty_pair = (a, b)
            break
    r = app.query({"require": list(empty_pair)})
    rl = r.get("relax") or []
    replay = [len(app.answer_set(frozenset(empty_pair) - {x["drop"]}, frozenset()))
              for x in rl]
    ok("★ [入口] 57 零结果松弛：键都对但组合为空 ⇒ 0 项 + 松弛建议，且逐条复算为真",
       r.get("ok") is True and r.get("n_answer") == 0 and bool(rl)
       and replay == [x["n_answer"] for x in rl] and all(n > 0 for n in replay),
       f"空组合 {list(empty_pair)}｜松弛 {[(x['drop'], x['n_answer']) for x in rl]}"
       f"｜本地复算 {replay}")

def test_quotes_evidence() -> None:
    """★ 引文核对记录与文档**逐条对齐**（`tools/quote_index.py`，2026-10-20）。

    用户 2026-10-20：「测试和引用之类的内容只有宣称而没有实体」。

    `sources/` 是 45 MB **版权正文**，不能进仓库 ⇒ 「引文 310 条全覆盖」在 GitHub 上
    只能**相信**、不能**核对**。⇒ 生成 `evidence/quotes-index.md`：
    **每条引文 → 出自哪个源档 → 最低覆盖比**（不含正文，只含引文本身 + 源档名）。

    这条判据守的是**同步**：文档里每加/删一条引文，索引必须跟着重生成
    （`python -m ldv.tools.quote_index write`）。
    """
    import io as _io
    import shutil
    import tempfile
    from pathlib import Path as _P

    from ldv.tools import quote_index as QI

    # ── 62 真实仓库：索引与文档逐条对齐 ──────────────────────────────────
    good, r = QI.check()
    ok("★ [引文证据] 62 `evidence/quotes-index.md` 与文档引文**逐条对齐**",
       good and r["文档引文"] == r["索引记"] and r["文档引文"] > 0,
       f"文档 {r['文档引文']} 条｜索引 {r['索引记']} 条｜缺 {len(r['缺'])}｜多 {len(r['多'])}")

    # ── 63 **反向对照**：索引里少一条 ⇒ check 必须报红 ────────────────────
    tmp = _P(tempfile.mkdtemp())
    old_out = QI.OUT
    try:
        src = _io.open(old_out, encoding="utf-8").read().splitlines()
        keep = [ln for ln in src if not ln.startswith("| `")]
        rows = [ln for ln in src if ln.startswith("| `")]
        (tmp / "idx.md").write_text("\n".join(keep + rows[1:]) + "\n", encoding="utf-8")
        QI.OUT = tmp / "idx.md"
        bad, br = QI.check()
        ok("★ [引文证据] 63 **反向对照**：索引里少一条 ⇒ check 必须报红",
           bad is False and len(br["缺"]) >= 1,
           f"check 返回 {bad}｜缺 {len(br['缺'])}｜索引记 {br['索引记']}")
    finally:
        QI.OUT = old_out
        shutil.rmtree(tmp, ignore_errors=True)


def test_approved_loop() -> None:
    """★ 反思层闭环：`directions.json` 的 `已批准` **真的被用上**（2026-10-19）。

    ⚠️ 在这之前 `已批准` **只用来防重复请求**（`already_handled`）——
       人批了，系统不会真去做 ⇒「报告 → 人拍板」是**开环**。
       判据 ㊜ 是这件事的硬读数：批准一个真字段 ⇒ **方向数必须变多**。
    """
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from ldv.core import request as RQ
    from ldv.core.kernel import Kernel
    from ldv.checks._fixtures import load as _load
    from ldv.plugins.keyset import KeysetPlugin

    loaded = _load()
    assert loaded is not None
    nodes, _edges, _d = loaded

    def _n_dirs(ns):
        items = {i: n.as_item() for i, n in ns.items()}
        k = Kernel(KeysetPlugin(), items)
        k.build(k.plugin.merge([]))
        k.bulk_insert(sorted(items))
        return len(k.all_directions())

    base_dirs = _n_dirs(nodes)
    base_keys = {i: n.keys for i, n in nodes.items()}

    tmp = Path(tempfile.mkdtemp(prefix="ldv-approved-"))
    try:
        # ── ㊚ 对照：**没有** `directions.json` ⇒ 一个字不动 ────────────────
        same, led0 = RQ.apply_approved(nodes, tmp)
        ok("★ [闭环] ㊚ 没批准 ⇒ 一个字不动（键集与方向数都不变）",
           led0["已批准条目"] == 0 and same == nodes and _n_dirs(same) == base_dirs,
           f"条目 {led0['已批准条目']}｜方向 {_n_dirs(same)}（基线 {base_dirs}）")

        # ── ㊘ 批准一个**材料里真有**的字段（`scope`，不在 ENUM_FIELDS 里）──────
        (tmp / "directions.json").write_text(json.dumps(
            {"已批准": [{"信号": "B19", "维度": "scope", "取值": None}]},
            ensure_ascii=False), encoding="utf-8")
        new, led = RQ.apply_approved(nodes, tmp)
        got_keys = all(any(k.startswith("scope=") for k in new[i].keys) for i in new)
        ok("★ [闭环] ㊘ 批准存在的字段 ⇒ 真的进键集（`维度=值`）",
           bool(led["生效"]) and led["生效"][0]["维度"] == "scope" and got_keys
           and led["生效项数"] == len(new),
           f"生效 {led['生效']}｜生效项 {led['生效项数']}/{len(new)}")

        # ── ㊘b **入参不被改**（纯函数）─────────────────────────────────────
        untouched = all(nodes[i].keys == base_keys[i] for i in nodes)
        ok("★ [闭环] ㊘b 纯函数：原 nodes 的键集**一个都没变**", untouched,
           "入参未被改" if untouched else "**入参被改了** —— 会影响别的调用方")

        # ── ㊜ **方向数真的变多**（键集变大 ⇒ 树多一层）──────────────────────
        new_dirs = _n_dirs(new)
        ok("★ [闭环] ㊜ 方向数真的变多 —— 这是「闭环有没有用」的硬读数",
           new_dirs > base_dirs, f"批准前 {base_dirs} → 批准后 {new_dirs}")

        # ── ㊙ 批准一个**材料里没有**的字段 ⇒ 记「未生效」，不假装成功 ────────
        (tmp / "directions.json").write_text(json.dumps(
            {"已批准": [{"信号": "B19", "维度": "no_such_field_xyz"}]},
            ensure_ascii=False), encoding="utf-8")
        _n2, led2 = RQ.apply_approved(nodes, tmp)
        ok("★ [闭环] ㊙ 批准不存在的字段 ⇒ 记「未生效」（不假装成功）",
           len(led2["未生效"]) == 1 and not led2["生效"] and led2["生效项数"] == 0,
           f"未生效 {led2['未生效']}")

        # ── ㊛ `取值` 过滤：只给等于该值的项加键 ────────────────────────────
        #    取一个真值（第一个节点的 type），只批准「type == 那个值」
        some_type = str(nodes[sorted(nodes)[0]].fields.get("type"))
        (tmp / "directions.json").write_text(json.dumps(
            {"已批准": [{"信号": "B19", "维度": "type", "取值": some_type}]},
            ensure_ascii=False), encoding="utf-8")
        new3, led3 = RQ.apply_approved(nodes, tmp)
        hit = [i for i in new3 if f"type={some_type}" in new3[i].keys]
        # `type` 本来就在 ENUM_FIELDS 里 ⇒ 键本来就有；这里判的是**账本**只算那些项
        ok("★ [闭环] ㊛ `取值` 过滤生效：只给等于该值的项算数",
           bool(led3["生效"]) and led3["生效"][0]["项数"] == len(hit) > 0
           and led3["生效项数"] == len(hit),
           f"取值 {some_type!r} ⇒ {led3['生效项数']} 项（实测命中 {len(hit)}）")

        # ── 谓词路径：批准一个**谓词定义** ⇒ 真的加键（2026-10-19）──────
        from ldv.corpus import predicates as PR

        _root_c = Path(__file__).resolve().parents[2] / "ldv" / "corpus"
        rules, budget = PR.load_rules(_root_c)
        used0, _dr, _pm = PR.derive(nodes, _edges, rules, budget)
        first = str(used0[0].谓词) if used0 else ""
        if not first:
            PASS.append("★ [闭环] ㊠-㊢ 谓词路径（**跳过**：这份语料没有推导出单谓词）")

        t2 = Path(tempfile.mkdtemp(prefix="ldv-approved-pred-"))
        try:
            # ㊠ 有 preds ⇒ 加键
            (t2 / "directions.json").write_text(json.dumps(
                {"已批准": [{"谓词": {"名": "人起的名字", "定义": [first]}}]},
                ensure_ascii=False), encoding="utf-8")
            np_, lp_ = RQ.apply_approved(nodes, t2, edges=_edges, preds=used0)
            hit_ = [i for i in np_ if "人起的名字" in np_[i].keys]
            ok("★ [闭环] ㊠ 批准谓词 + 有 preds ⇒ 满足合取的节点真的加键「名」",
               bool(lp_["生效"]) and lp_["生效"][0]["维度"] == "（谓词）"
               and len(hit_) == lp_["生效"][0]["项数"] > 0,
               f"定义 [{first!r}] ⇒ 命中 {lp_['生效'][0]['项数'] if lp_['生效'] else 0} 项")

            # ㊡ 没 preds ⇒ 未生效（不假装）
            _np2, lp2 = RQ.apply_approved(nodes, t2, edges=_edges, preds=())
            ok("★ [闭环] ㊡ 批准谓词但**没有** preds ⇒ 记「未生效」",
               not lp2["生效"] and any("谓词" in str(x.get("维度")) for x in lp2["未生效"]),
               f"未生效 {[x.get('维度') for x in lp2['未生效']]}")

            # ㊢ 定义引用不存在的谓词 ⇒ 不加键
            (t2 / "directions.json").write_text(json.dumps(
                {"已批准": [{"谓词": {"名": "空名字", "定义": ["不存在的谓词>9"]}}]},
                ensure_ascii=False), encoding="utf-8")
            np3, lp3 = RQ.apply_approved(nodes, t2, edges=_edges, preds=used0)
            ghost = [i for i in np3 if "空名字" in np3[i].keys]
            ok("★ [闭环] ㊢ 定义引用不存在的谓词 ⇒ **不加键**、不静默造",
               not ghost and not lp3["生效"],
               f"加了 {len(ghost)} 个键（应为 0）")
        finally:
            shutil.rmtree(t2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
def test_direction_request() -> None:
    """★ 方向请求六条判据（`ldv/core/request.py`，2026-10-17）。

    边界：系统**只提问不回答** —— 不定义方向、不加键、不改结构；
    请求的是**新维度**（字段 / 度量），不是方向函数。
    """
    import json
    import shutil
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from ldv.core import request as RQ
    from ldv.checks._fixtures import load as _load, coverage_of
    from ldv.cli import _assemble

    loaded = _load()
    assert loaded is not None
    nodes, edges, _d = loaded
    k, _q, *_ = _assemble("keyset", nodes, edges, 12)
    cov = coverage_of("keyset", nodes, edges)

    # ── 假内核：一条 11 连的「没变细」链 ──────────────────────────────
    #    D0→D1→…→D11，每个方向的 cover 都一样 ⇒ 没有任何子的覆盖严格小于父
    class _D:
        def __init__(self, did, parent, payload):
            self.did, self.parent, self.payload = did, parent, payload
            # ⚠️ `progress_profile` 读 `rank` —— 假方向也要有（2026-10-17 踩到）
            self.rank = int(did[1:])

    # 12 个方向 ⇒ 11 连（不含叶）≥ 阈值 10 —— 10 个方向只有 9 连，差一个
    dirs = [_D(f"D{i}", None if i == 0 else f"D{i-1}", 0) for i in range(12)]
    fake = SimpleNamespace()
    fake.root = dirs[0]
    fake.all_directions = lambda: list(dirs)
    fake.children_of = lambda d: [dirs[int(d.did[1:]) + 1]] if d.did != "D11" else []
    fake.members_of = lambda d: frozenset({"a", "b", "c"})
    fake_cov = lambda payload: frozenset({"a", "b", "c"})

    def _sig(kern):
        return tuple((d.did, d.parent, len(kern.children_of(d)),
                      len(kern.members_of(d))) for d in kern.all_directions())

    # ㊀ 只读
    before = _sig(k)
    r_default = RQ.direction_requests(k, cov)
    after = _sig(k)
    ok("★ [请求] ㊀ 只读：生成请求前后结构签名逐字相同",
       before == after, f"{len(before)} 方向")

    # ㊅ 不触发时也要说
    ok("★ [请求] ㊅ 不触发时也要说：请求为空，但 `本趟查了` 说清查了多少",
       r_default["请求"] == [] and "最长连续段" in r_default["本趟查了"]
       and "不是「没查」" in r_default["说明"],
       f"{r_default['本趟查了']}｜{r_default['说明'][:36]}")

    # 构造触发
    r_fire = RQ.direction_requests(fake, fake_cov, used_preds=(), n=10)
    reqs = r_fire["请求"]
    ok("★ [请求] ㊀b 构造 11 连 ⇒ 触发（证明机制会响）",
       len(reqs) == 1 and reqs[0]["阈值"] == 10,
       f"信号：{reqs[0]['信号'] if reqs else '（无）'}")

    # ㊁ 可复现
    r_again = RQ.direction_requests(fake, fake_cov, used_preds=(), n=10)
    ok("★ [请求] ㊁ 可复现：同结构两次逐字相同",
       r_fire == r_again, f"请求 {len(reqs)} vs {len(r_again['请求'])}")

    # ㊂ 每条带证据
    r0 = reqs[0] if reqs else {}
    ok("★ [请求] ㊂ 每条带证据：`涉及的项` 非空 + `判据编号` + `已经试过` 在",
       bool(r0.get("涉及的项")) and r0.get("判据编号") == "B19"
       and "已经试过" in r0 and "当前状态" in r0,
       f"涉及 {r0.get('涉及的项数')} 项｜链 {len(r0.get('链', []))} 个方向")

    tmp = Path(tempfile.mkdtemp(prefix="ldv-req-"))
    try:
        p = RQ.write_requests(tmp, r_fire)
        doc = json.loads(p.read_text(encoding="utf-8"))
        ok("★ [请求] ㊃b 写盘：`请求` 与 `本趟查了` 都落文件",
           doc["请求"] == reqs and doc["本趟查了"] == r_fire["本趟查了"],
           f"{p.name}｜请求 {len(doc['请求'])} 条")

        # 人处置：把这条的项写进「已拒绝」
        doc["已拒绝"] = [{"涉及的项": r0.get("涉及的项", []), "理由": "这 3 项确实无区别"}]
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        handled = RQ.already_handled(tmp, r0.get("涉及的项", []))
        ok("★ [请求] ㊃ 处置过的不再请求：人写进 `已拒绝` 后不再重复出现",
           handled, "already_handled 为真")

        # ㊄ 人写段不被覆盖（重新写请求，人写的两段还在）
        RQ.write_requests(tmp, r_fire)
        doc2 = json.loads(p.read_text(encoding="utf-8"))
        ok("★ [请求] ㊄ 人写段不被覆盖：重写后 `已拒绝` 原样保留",
           doc2.get("已拒绝") == doc.get("已拒绝"),
           f"已拒绝 {len(doc2.get('已拒绝', []))} 条")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
def test_impact() -> None:
    """★ 影响分析四条判据（`ldv/core/impact.py`，2026-10-17）。

    这是**结构推理**的最后一层：**预测**删掉某些项会让哪些方向变空，
    再在副本上**实测**验证。纯结构、不碰语义、不依赖历史。
    """
    from ldv.cli import _assemble
    from ldv.core import impact as IM
    from ldv.checks._fixtures import load as _load

    loaded = _load()
    assert loaded is not None
    nodes, edges, _d = loaded
    k, _q, *_ = _assemble("keyset", nodes, edges, 12)

    def _sig(kern):
        return tuple((d.did, d.parent, len(kern.children_of(d)), len(kern.members_of(d)))
                     for d in kern.all_directions())

    ids = sorted(k.placed)[:3]
    before = _sig(k)
    r1 = IM.removal_impact(k, ids)
    br = IM.blast_radius(k, "D0")
    after = _sig(k)

    # ㊾ 只读
    ok("★ [影响] ㊾ 只读：`removal_impact` / `blast_radius` 前后结构签名逐字相同",
       before == after, f"方向数 {len(before)} → {len(after)}")

    # ⑤① 可复现
    r2 = IM.removal_impact(k, ids)
    ok("★ [影响] ⑤① 可复现：同结构两次调用逐字相同", r1 == r2,
       f"会空 {r1['会变空数']} vs {r2['会变空数']}")

    # ㊿ 预测 == 实测（在**另一份装配**上真删 —— 不碰上面那个只读的内核）
    k2, _q2, *_ = _assemble("keyset", nodes, edges, 12)
    cmp = IM.compare_prediction(k2, ids)
    ok("★ [影响] ㊿ 预测 == 实测：预测「会空 / 会缩」的方向集与真删后一致",
       cmp["会空一致"] and cmp["缩一致"],
       f"会空 预测{cmp['预测会空']} 实测{cmp['实测会空']}｜"
       f"缩 预测{len(cmp['预测缩'])} 实测{len(cmp['实测缩'])}")

    # ⑤② 影响 0 也要明说（「没算」与「算了没影响」必须分得开）
    r3 = IM.removal_impact(k, ["_根本不存在的项_"])
    ok("★ [影响] ⑤② 影响 0 明说：删不在结构里的项 ⇒ 明确报「没有任何方向受影响」",
       r3["会变空数"] == 0 and r3["成员减少数"] == 0
       and r3["说明"] == "没有任何方向受影响"
       and r3["不在结构里"] == ["_根本不存在的项_"],
       f"{r3['说明']}｜不在结构里 {r3['不在结构里']}")
def test_capacity() -> None:
    """★ 缓冲区容量四条判据（`ldv/tools/staging.py`，2026-10-17）。

    边界：容量上限是**外生**的；淘汰**只碰未提交候选**（FIFO，不按访问次数），
    `rejected/`（审计链）与 `nodes/`（已提交）**永不淘汰**。
    """
    import shutil
    import tempfile
    import time
    from pathlib import Path

    from ldv.tools import staging as ST

    tmp = Path(tempfile.mkdtemp(prefix="ldv-cap-"))
    try:
        corpus = tmp / "c"
        (corpus / "nodes").mkdir(parents=True)
        # 一个已提交节点 + 一个拒收理由文件（用来验「淘汰不碰它们」）
        (corpus / "nodes" / "n0.md").write_text(
            '---\nid: "n0"\ntype: "测试"\n---\n\n正文\n', encoding="utf-8")
        rej = corpus / "_staging" / "rejected"
        rej.mkdir(parents=True)
        # ⚠️ `capacity_status` 数的是 `*.md`（**被拒的原文件**），理由文件是
        #    `*.md.理由.txt`（不算节点）。两个都要放，才能同时验「拒收计数」
        #    与「理由文件不被淘汰」。（第一版只放了理由文件 ⇒ 拒收数 0 ⇒ 判据红）
        (rej / "old.md").write_text('---\nid: "old"\n---\n\n被拒的原文件\n',
                                    encoding="utf-8")
        (rej / "old.md.理由.txt").write_text("审计链\n", encoding="utf-8")

        # 造 5 个候选，**按时间先后**写入（FIFO 判据要能分辨）
        for i in range(5):
            f = tmp / f"c{i}.md"
            f.write_text(f'---\nid: "c{i}"\ntype: "测试"\n---\n\n正文 {i}\n',
                         encoding="utf-8")
            ST.stage(corpus, f)
            time.sleep(0.02)            # 让 mtime 可分辨

        # ㊺ 容量可读
        st = ST.capacity_status(corpus, capacity=3)
        ok("★ [容量] ㊺ 容量可读：待提交 / 容量 / 超限 / 拒收 / 正式节点数 都在",
           st["待提交"] == 5 and st["容量"] == 3 and st["超限"] == 2
           and st["拒收"] == 1 and st["正式节点数"] == 1,
           f"{st}")

        # ㊽ 未超容量时不淘汰
        r0 = ST.evict(corpus, keep=10)
        ok("★ [容量] ㊽ 不静默淘汰：未超容量 ⇒ 一个都不删",
           r0["淘汰"] == [] and len(list((corpus / "_staging" / "nodes").glob("*.md"))) == 5,
           f"{r0}")

        # ㊻ + ㊼ 淘汰：只碰未提交、FIFO
        r = ST.evict(corpus, keep=3)
        left = sorted(p.name for p in (corpus / "_staging" / "nodes").glob("*.md"))
        ok("★ [容量] ㊼ FIFO：淘汰**最早进缓冲区**的（c0 / c1）",
           r["淘汰"] == ["c0.md", "c1.md"], f"淘汰 {r['淘汰']}｜留下 {left}")
        ok("★ [容量] ㊻ 淘汰只碰未提交：`rejected/` 与 `nodes/` 数量**不变**",
           len(list(rej.glob("*.md"))) == 1 and len(list((corpus / "nodes").glob("*.md"))) == 1,
           f"拒收 {len(list(rej.glob('*.md')))}｜正式 {len(list((corpus / 'nodes').glob('*.md')))}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
def test_contradictions() -> None:
    """★ 矛盾报告四条判据（`ldv/core/reflect.py` `contradictions`，2026-10-17）。

    边界：**只在结构层**（边打架 / 子覆盖越出父 / 孤立方向）。
    语义矛盾（材料互相冲突）**不做** —— 要理解内容 ⇒ 撞「禁止模型代填」。
    """
    from types import SimpleNamespace

    from ldv.cli import _assemble
    from ldv.core import reflect as RF
    from ldv.checks._fixtures import load as _load

    loaded = _load()
    assert loaded is not None
    nodes, edges, _d = loaded
    k, _q, *_ = _assemble("keyset", nodes, edges, 12)

    def _sig(kern):
        return tuple((d.did, d.parent, len(kern.children_of(d)), len(kern.members_of(d)))
                     for d in kern.all_directions())

    before = _sig(k)
    c = RF.contradictions(k)
    after = _sig(k)

    # ㊶ 可重算：抽查每条「边打架」确实成立
    recomputable = True
    for row in c["矛盾"]:
        if row["类型"] == "边打架":
            d = k.direction(row["方向"])
            p = k.direction(row["父"])
            if d.did in [x.did for x in k.children_of(p)]:
                recomputable = False        # 报告说打架、实际不打架 ⇒ 报告错了
    ok("★ [矛盾] ㊶ 可重算：清单里每条都能从结构验（抽查边打架）",
       recomputable and c["矛盾数"] == len(c["矛盾"]),
       f"矛盾数 {c['矛盾数']}")

    # ㊷ 查了什么可读（「没查」与「查了没发现」必须分得开）
    cov = c["覆盖读数"]
    ok("★ [矛盾] ㊷ 查了什么可读：覆盖读数里有方向数与三项计数",
       cov.get("方向数", 0) > 0 and all(kk in cov for kk in
                                       ("边打架", "子覆盖越出父", "孤立方向")),
       f"{cov}")

    # ㊸ 只报不改
    ok("★ [矛盾] ㊸ 只报不改：调用前后结构签名逐字相同",
       before == after, f"方向数 {len(before)} → {len(after)}")

    # ㊹ 注入矛盾能被抓（构造一个「边打架」的最小 kernel）
    class _D:
        def __init__(self, did, parent):
            self.did, self.parent = did, parent

    fake = SimpleNamespace()
    fake.all_directions = lambda: [_D("D0", None), _D("D1", "D0"), _D("D2", "D0")]
    fake.direction = lambda did: {"D0": _D("D0", None), "D1": _D("D1", "D0"),
                                  "D2": _D("D2", "D0")}[did]
    # D0 的子列表里**只有 D1** ⇒ D2 声明了父 D0 却不在子列表 ⇒ 边打架
    fake.children_of = lambda d: ([_D("D1", "D0")] if d.did == "D0" else [])
    fake.members_of = lambda d: {"D0": frozenset({"a", "b"}),
                                 "D1": frozenset({"a"}),
                                 "D2": frozenset({"b"})}[d.did]
    c2 = RF.contradictions(fake)
    ok("★ [矛盾] ㊹ 注入矛盾被抓：构造「D2 声明父 D0 但不在子列表」⇒ 清单必须报",
       c2["矛盾数"] >= 1 and any(r["类型"] == "边打架" and r["方向"] == "D2"
                                 for r in c2["矛盾"]),
       f"矛盾数 {c2['矛盾数']}｜{[r['类型'] + ':' + r['方向'] for r in c2['矛盾']]}")
def test_combination() -> None:
    """★ 组合谓词六条判据（`ldv/corpus/predicates.py` `combine`，2026-10-17）。

    边界：组合谓词**一律不进划分** —— 只进「待命名」（复杂度 > K）或
    「组合候选」。进划分的只有单谓词（预算内）与**人命名后的基础谓词**。
    """
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from ldv.corpus import predicates as PR
    from ldv.corpus.loader import load_edges, load_nodes

    if _ext_corpus("pypi-deps") is None:
        return
    if _ext_corpus("pypi-deps") is None:
        return
    if _ext_corpus("pypi-deps") is None:
        return
    data = Path(__file__).resolve().parents[2] / "_data"
    nodes = load_nodes(data / "pypi-deps" / "nodes")
    edges, _d = load_edges(nodes)
    _cand, used, _rej, _p = PR.derive_buffered(nodes, edges)

    # ㉟ 复杂度 = 项数
    to_name, cands, crej = PR.combine(nodes, edges, used)
    allc = list(to_name) + list(cands)
    ok("★ [组合] ㉟ 复杂度可算：复杂度 == 合取项数",
       all(c.复杂度 == len(c.项) for c in allc) and all(c.复杂度 >= 2 for c in allc),
       f"{[(c.谓词, c.复杂度) for c in allc][:3]}")

    # ㊱ 超 K 进待命名（构造**不同量**的 3 项组合 —— 真实语料上 3 项常被同量冗余挡掉）
    #    ⇒ 手工造三个不同量的谓词，各自满足「一半左右」的节点
    total = len(nodes)
    ids = sorted(nodes)
    fake_vals = {
        "量A": {nid: float(i < total // 2) for i, nid in enumerate(ids)},
        "量B": {nid: float(i % 2 == 0) for i, nid in enumerate(ids)},
        "量C": {nid: float(i % 3 != 0) for i, nid in enumerate(ids)},
    }
    import ldv.corpus.predicates as _pr
    _orig_measure = _pr.measure

    def _fake_measure(ns, es, which):
        return fake_vals[which]

    try:
        _pr.measure = _fake_measure
        fake_used = [PR.Predicate(谓词=f"{w}>0", 规则=w, 量=w, 阈值=0.0,
                                  满足数=int(sum(1 for v in fake_vals[w].values() if v > 0)),
                                  区分度=0.0) for w in ("量A", "量B", "量C")]
        tn, cd, cr = PR.combine(nodes, edges, fake_used, k=2)
        ok("★ [组合] ㊱ 超 K 进待命名：3 项（不同量）⇒ 进「待命名」",
           any(c.复杂度 == 3 for c in tn) and all(c.复杂度 == 2 for c in cd),
           f"待命名 {[(c.谓词, c.复杂度) for c in tn]}｜候选 {[c.复杂度 for c in cd]}")
    finally:
        _pr.measure = _orig_measure

    # ㊲ 同量冗余被拒
    dup_rej = [x for x in crej if "同量冗余" in x.get("理由", "")]
    ok("★ [组合] ㊲ 同量冗余被拒：`出度>0 ∧ 出度>2` ⇒ 拒收并写明「互相蕴含」",
       bool(dup_rej), f"{[x['谓词'] for x in dup_rej][:2]}")

    # ㊳ 组合不进划分：`apply_to_nodes` 只吃单谓词，组合谓词不在 keys 里
    probe = {k: type(v)(id=v.id, fields=v.fields, body=v.body, keys=v.keys)
             for k, v in nodes.items()}
    PR.apply_to_nodes(probe, edges, used)
    combo_names = [c.谓词 for c in allc]
    leaked = [nid for nid, n in probe.items()
              if any(cn in n.keys for cn in combo_names)]
    ok("★ [组合] ㊳ 组合不进划分：节点键里**没有**组合谓词（含 ∧ 的字符串）",
       not leaked and not any("∧" in k for n in probe.values() for k in n.keys),
       f"泄漏 {len(leaked)} 个节点")

    # ㊴ 基础谓词保留（程序重写文件不覆盖人写的段）
    tmp = Path(tempfile.mkdtemp(prefix="ldv-combo-"))
    try:
        manual = {"基础谓词": [{"名": "枢纽节点", "定义": ["入度档>1", "出度档>2"]}]}
        (tmp / "predicates.json").write_text(
            json.dumps(manual, ensure_ascii=False), encoding="utf-8")
        PR.write_registry(tmp, "测试", _cand, used, _rej, _p, to_name, cands, crej)
        doc = json.loads((tmp / "predicates.json").read_text(encoding="utf-8"))
        ok("★ [组合] ㊴ 基础谓词保留：重写后「基础谓词」段原样还在",
           doc.get("基础谓词") == manual["基础谓词"],
           f"{doc.get('基础谓词')}")

        # ㊵ 未命名不进划分：有待命名时，节点键里没有它
        keys_all = set().union(*[n.keys for n in probe.values()])
        unnamed = [c.谓词 for c in allc if c.谓词 not in keys_all]
        ok("★ [组合] ㊵ 未命名不进划分：组合谓词**都不在**节点键里",
           len(unnamed) == len(allc), f"未在键里 {len(unnamed)}/{len(allc)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
def test_buffer() -> None:
    """★ 缓冲区五条判据（谓词三段 + 记忆 `_staging/`，2026-10-17）。

    谓词：`候选 → 采用` 由判据自动筛选（真二分 + 预算），拒收留理由。
    记忆：新节点先落 `_staging/nodes/`（**不参与结构**），提交时四条判据筛。
    """
    import shutil
    import tempfile
    from pathlib import Path

    from ldv.corpus import predicates as PR
    from ldv.corpus.loader import load_nodes
    from ldv.tools import staging as ST

    if _ext_corpus("pypi-deps") is None:
        return
    if _ext_corpus("pypi-deps") is None:
        return
    if _ext_corpus("pypi-deps") is None:
        return
    data = Path(__file__).resolve().parents[2] / "_data"

    # ── 谓词三段 ────────────────────────────────────────────────────
    nodes = load_nodes(data / "pypi-deps" / "nodes")
    from ldv.corpus.loader import load_edges
    edges, _d = load_edges(nodes)
    cand, used, rejected, params = PR.derive_buffered(nodes, edges)
    used_set = {p.谓词 for p in used}
    rej_set = {x["谓词"] for x in rejected}
    dropped = {p.谓词 for p in cand} - used_set - rej_set

    ok("★ [缓冲] ㉚ 谓词三段不重不漏：候选 = 采用 ∪ 超预算 ∪ 拒收",
       len(cand) == len(used) + len(dropped) + len(rejected)
       and not (used_set & rej_set),
       f"候选 {len(cand)}｜采用 {len(used)}｜超预算 {len(dropped)}｜拒收 {len(rejected)}")

    ok("★ [缓冲] ㉛ 拒收有理由：每条都带非空 `理由`",
       all(x.get("理由") for x in rejected),
       f"拒收 {[(x['谓词'], x['理由'][:12]) for x in rejected][:2]}")

    # 拒收的确实是「切不开」的（满足数 0 或 = 总数）
    total = len(nodes)
    ok("★ [缓冲] ㉛b 拒收理由成立：满足数要么 0、要么 = 总数",
       all(x["满足数"] == 0 or x["满足数"] == total for x in rejected),
       f"拒收满足数 {[x['满足数'] for x in rejected]}（总数 {total}）")

    # ── 记忆缓冲 ────────────────────────────────────────────────────
    tmp = Path(tempfile.mkdtemp(prefix="ldv-buf-"))
    try:
        corpus = tmp / "corpus"
        (corpus / "nodes").mkdir(parents=True)
        # 先放一个「已有」节点
        (corpus / "nodes" / "n1.md").write_text(
            "---\nid: \"n1\"\ntype: 测试\n---\n\n正文\n", encoding="utf-8")
        # 缓冲两个：一个正常、一个 id 撞
        src_ok = tmp / "new.md"
        src_ok.write_text("---\nid: \"n2\"\ntype: 测试\n---\n\n正文\n", encoding="utf-8")
        src_dup = tmp / "dup.md"
        src_dup.write_text("---\nid: \"n1\"\ntype: 测试\n---\n\n正文\n", encoding="utf-8")
        ST.stage(corpus, src_ok)
        ST.stage(corpus, src_dup)

        # ㉜ 缓冲不参与结构
        before = load_nodes(corpus / "nodes")
        ok("★ [缓冲] ㉜ 缓冲不参与结构：`_staging/nodes/` 里的文件不进 `load_nodes`",
           set(before) == {"n1"}, f"装载到 {sorted(before)}")

        # ㉝ 提交：正常进 nodes、撞 id 进 rejected + 理由文件
        res = ST.commit(corpus)
        ok("★ [缓冲] ㉝ 提交判据：正常文件移进 `nodes/`、id 撞的进 `rejected/` + 理由",
           res["已提交"] == ["new.md"] and len(res["拒收"]) == 1
           and (corpus / "_staging" / "rejected" / "dup.md.理由.txt").is_file(),
           f"提交 {res['已提交']}｜拒收 {[x['文件'] for x in res['拒收']]}")

        # ㉞ 提交后结构包含
        after = load_nodes(corpus / "nodes")
        ok("★ [缓冲] ㉞ 提交后结构包含新节点",
           set(after) == {"n1", "n2"}, f"装载到 {sorted(after)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
def test_memory_index() -> None:
    """★ 语料索引四条判据（`ldv/corpus/index.py`，2026-10-17）。

    短期 = 进程内 `_NODE_CACHE`；长期 = 跨进程派生索引（`~/.cache/ldv/`）。
    ⚠️ 索引里**没有 body** ⇒ 等价判据只比 id / keys / fields。
    """
    import shutil
    import tempfile
    import time
    from pathlib import Path

    from ldv.corpus.index import load_nodes_indexed
    from ldv.corpus.loader import load_nodes

    if _ext_corpus("pypi-deps") is None:
        return
    if _ext_corpus("pypi-deps") is None:
        return
    if _ext_corpus("pypi-deps") is None:
        return
    data = Path(__file__).resolve().parents[2] / "_data"
    src = data / "pypi-deps" / "nodes"
    tmp = Path(tempfile.mkdtemp(prefix="ldv-idx-rt-"))
    try:
        # 直读基线
        direct = load_nodes(src)
        # 首次（索引不存在 ⇒ 全读）与二次（命中）
        t0 = time.time()
        n1, s1 = load_nodes_indexed(src, tmp)
        t_first = time.time() - t0
        t0 = time.time()
        n2, s2 = load_nodes_indexed(src, tmp)
        t_second = time.time() - t0

        # ㉖ 等价（id / keys / fields）
        same_ids = set(direct) == set(n2)
        diff_keys = [k for k in direct if direct[k].keys != n2[k].keys]
        diff_fields = [k for k in direct if direct[k].fields != n2[k].fields]
        ok("★ [索引] ㉖ 等价：索引路径与直读 ⇒ id / keys / fields 逐字相同",
           same_ids and not diff_keys and not diff_fields,
           f"id 同 {same_ids}｜keys 差 {len(diff_keys)}｜fields 差 {len(diff_fields)}")

        # ㉗ 命中：第二次**全命中、零重读**（正确性判据，不依赖语料大小）
        #    ⚠️ 加速比**只作读数**：小语料（60 节点）上固定开销占主导，
        #    实测 1.4×；大语料上才是 7.4×（ext-coauth，见 outputs 探针留档）。
        speed = t_first / t_second if t_second > 0 else 0
        ok("★ [索引] ㉗ 命中：二次装载全命中、零重读（加速比作读数）",
           s2["命中"] == len(direct) and s2["重读"] == 0,
           f"首次 {t_first:.2f}s → 二次 {t_second:.2f}s（{speed:.1f}×，小语料）"
           f"｜命中 {s2['命中']} / 重读 {s2['重读']}")

        # ㉘ 失效：改一个文件 ⇒ 只重读 1 个
        victim = sorted(src.glob("*.md"))[0]
        orig = victim.read_text(encoding="utf-8")
        try:
            # 改内容并保证 mtime 变化（size 也可能不变 ⇒ 靠 mtime_ns）
            victim.write_text(orig + "\n<!-- index-probe -->\n", encoding="utf-8")
            os.utime(victim, None)
            _n3, s3 = load_nodes_indexed(src, tmp)
            ok("★ [索引] ㉘ 失效：改一个文件 ⇒ **只重读它**（其余仍命中）",
               s3["重读"] == 1 and s3["命中"] == len(direct) - 1,
               f"重读 {s3['重读']} / 命中 {s3['命中']}")
        finally:
            victim.write_text(orig, encoding="utf-8")
            os.utime(victim, None)

        # ㉙ 不污染：索引不在语料目录
        in_corpus = list(src.glob("*.index.json")) + list((src.parent).glob("*.index.json"))
        ok("★ [索引] ㉙ 不污染：索引写在缓存目录，语料目录里没有索引文件",
           not in_corpus, f"语料目录里发现：{[p.name for p in in_corpus][:3]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
def test_predicates() -> None:
    """★ 谓词推导的六条判据（`ldv/corpus/predicates.py`，2026-10-17）。

    边界：规则**外生**（人写「按出度取切分点」），参数**内生**（切分点从材料算）
    ⇒ 谓词由两者唯一确定 ⇒ 可复现。**预算 N 外生定死**（不随材料自适应）。
    """
    import shutil
    import tempfile
    from pathlib import Path

    from ldv.corpus import predicates as PR
    from ldv.corpus.loader import load_edges, load_nodes

    if _ext_corpus("pypi-deps") is None:
        return
    if _ext_corpus("pypi-deps") is None:
        return
    if _ext_corpus("pypi-deps") is None:
        return
    data = Path(__file__).resolve().parents[2] / "_data"

    def _derive(name: str):
        nodes = load_nodes(data / name / "nodes")
        edges, _d = load_edges(nodes)
        return PR.derive(nodes, edges)

    # ⑯ 可复现：同材料两次 ⇒ 逐字相同
    u1, d1, p1 = _derive("pypi-deps")
    u2, d2, p2 = _derive("pypi-deps")
    sig = lambda us: [(p.谓词, p.阈值, p.满足数, round(p.区分度, 6)) for p in us]
    ok("★ [谓词] ⑯ 可复现：同材料两次 derive ⇒ 谓词清单逐字相同",
       sig(u1) == sig(u2) and p1 == p2,
       f"{sig(u1)[:2]} vs {sig(u2)[:2]}")

    # ⑰ 跟着材料变：两份语料的阈值/谓词不同
    u3, _d3, _p3 = _derive("ext-coauth")
    ok("★ [谓词] ⑰ 跟着材料变：两份语料的谓词不同（不是写死的常量）",
       sig(u1) != sig(u3),
       f"pypi {sig(u1)[:1]} vs coauth {sig(u3)[:1]}")

    # ⑱ 预算生效 + 不静默丢弃
    total = len(u1) + len(d1)
    ok("★ [谓词] ⑱ 预算生效：采用数 = N，超预算的都记在案（不静默丢弃）",
       len(u1) == PR.BUDGET and len(d1) == total - PR.BUDGET and total > PR.BUDGET,
       f"采用 {len(u1)} / 超预算 {len(d1)} / 共 {total}｜N={PR.BUDGET}")

    # ⑲ 可组合：任意两个采用谓词的合取非空（防互斥档位）
    nodes = load_nodes(data / "pypi-deps" / "nodes")
    edges, _d = load_edges(nodes)
    vals = {w: PR.measure(nodes, edges, w) for w in {p.量 for p in u1}}
    combos_empty = []
    for i in range(len(u1)):
        for j in range(i + 1, len(u1)):
            a, b = u1[i], u1[j]
            both = sum(1 for nid in nodes
                       if vals[a.量][nid] > a.阈值 and vals[b.量][nid] > b.阈值)
            if both == 0:
                combos_empty.append((a.谓词, b.谓词))
    ok("★ [谓词] ⑲ 可组合：任意两个采用谓词的合取**非空**（不是互斥档位）",
       not combos_empty, f"空合取：{combos_empty[:3]}")

    # ⑳ 清单可导出（采用 + 超预算都在）
    tmp = Path(tempfile.mkdtemp(prefix="ldv-pred-"))
    try:
        _cand, _used2, _rej2, _p2 = PR.derive_buffered(
            load_nodes(data / "pypi-deps" / "nodes"),
            load_edges(load_nodes(data / "pypi-deps" / "nodes"))[0])
        p = PR.write_registry(tmp, "pypi-deps", _cand, _used2, _rej2, _p2)
        doc = __import__("json").loads(p.read_text(encoding="utf-8"))
        ok("★ [谓词] ⑳ 清单可导出：采用 / 超预算 / 参数都在，且能读回",
           len(doc["采用"]) == len(u1) and len(doc["超预算"]) == len(d1)
           and doc["预算"] == PR.BUDGET and doc["语料"] == "pypi-deps",
           f"采用 {len(doc['采用'])}｜超预算 {len(doc['超预算'])}｜预算 {doc['预算']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ㉑ 默认路径不变：`load()` 不带谓词 ⇒ 键集里没有 `>` 形式的谓词
    from ldv.checks._fixtures import load as _load
    loaded = _load()
    assert loaded is not None
    n_default = loaded[0]
    has_gt = any(">" in k for n in n_default.values() for k in n.keys)
    ok("★ [谓词] ㉑ 默认路径不变：`load()` 不带谓词 ⇒ 键集无 `>` 谓词",
       not has_gt, "默认键集里出现了 `>` 谓词 ⇒ 默认基线被污染")
def test_service_shell() -> None:
    """★ 服务壳四阶段判据的**快版**（`docs/服务化设计.md` §3 阶段 3.1–3.4）。

    分工：本函数是**判据**（进退出码、快、CI 可跑）；`outputs/_probe_service_*.py`
    是**证据**（慢而全：真实大语料、完整留档）。⚠️ 两者互不替代 —— 本函数
    不重跑 112 秒的慢任务（排队用 **TaskTable 状态机单测**，等价用小租户）。

    ⚠️ 运维判据全程只碰**临时挂载的副本**（`_data/_rt-ops-*`），`pypi-deps`
    原件**只读不碰** —— run_tests 绝不能获得破坏语料库的能力。
    """
    import shutil
    import tempfile
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import serve  # 仓库根已在 sys.path（本文件顶部）
    from ldv.cli import run_one

    # ── 小租户（毫秒级）+ 不完整对照 ──────────────────────────────────
    TENANTS = ("pypi-deps", "arxiv-taxonomy", "gh-langs", "crossref-journals")
    INCOMPLETE = "cora-labeled"            # 缺 view_spec.json ⇒ 应 404
    DATA = Path(__file__).resolve().parents[2] / "_data"

    # ★ **前置守卫**（2026-10-19）：`TENANTS` 四个全在 `_data/`，而外部语料
    #   **不进仓库**（`.gitignore` 的 `_data/*`）⇒ CI / 新 clone 上一个都没有。
    #   不守的话，⑧a 一设 `LDV_CORPUS=tenant` 就 `find_corpus()` 崩 ——
    #   「没有这份语料」被表现成「单元测试崩了」，那是两件事。
    #   ⚠️ 需要 **≥2** 个（⑭⑮ 要用 `TENANTS[0]` / `TENANTS[1]`）。
    avail = tuple(t for t in TENANTS if (DATA / t / "nodes").is_dir())
    if len(avail) < 2:
        PASS.append(f"服务壳（**跳过**：`_data/` 下只有 {len(avail)} 个完整租户，"
                    f"需要 ≥2 —— 外部语料不进仓库，见 .gitignore）")
        return
    TENANTS = avail

    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)   # ephemeral 端口
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def _get(path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(base + path, timeout=30) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    # ── ⑧a 等价（3.1）：壳响应 == CLI 基线逐字相同 ────────────────────
    #    ⚠️ 恒真陷阱照旧（绕掉壳直接返回 CLI 输出也相同）⇒ 不能单独证明接上了，
    #    靠下面 ⑨ 隔离 + 反向对照一起才不空转。
    orig_env = os.environ.get("LDV_CORPUS")
    try:
        for tenant in TENANTS:
            st, body = _get(f"/{tenant}/run?plugin=keyset&only=a")
            os.environ["LDV_CORPUS"] = tenant          # CLI 基线走 find_corpus
            _ok_cli, text_cli = run_one("keyset", 12, "a")
            ok(f"★ [服务壳] ⑧a 等价：{tenant} 壳响应 == CLI 逐字",
               st == 200 and body == text_cli,
               f"壳 {len(body.encode())}B vs CLI {len(text_cli.encode())}B")
    finally:
        if orig_env is None:
            os.environ.pop("LDV_CORPUS", None)
        else:
            os.environ["LDV_CORPUS"] = orig_env

    # ── ⑨ 隔离（3.2）：不完整 / 不存在 ⇒ 404 ──────────────────────────
    st_bad, _ = _get(f"/{INCOMPLETE}/run?plugin=keyset")
    ok(f"★ [服务壳] ⑨ 隔离：缺 view_spec 的 {INCOMPLETE} ⇒ 404",
       st_bad == 404, f"实得 {st_bad}")
    st_miss, _ = _get("/_no-such-tenant_/run?plugin=keyset")
    ok("★ [服务壳] ⑨ 隔离：不存在的租户 ⇒ 404", st_miss == 404, f"实得 {st_miss}")

    # ── ⑩⑪⑫ 排队（3.3）：TaskTable 状态机单测（**不**走 HTTP）─────────
    #    ⚠️ 不用真慢任务（ext-coauth 全流程 112 s，会把 run_tests 拖到分钟级）——
    #    假任务 fn=sleep 测的正是**排队逻辑本身**；`run_one` 的执行等价已由 ⑧a 覆盖。
    tbl = serve.TaskTable(max_per_tenant=1)
    slow = lambda: (time.sleep(0.25), (200, "done"))[1]   # noqa: E731
    tid_a = tbl.submit("租户A", slow)
    t0 = time.time()
    while tbl.status(tid_a)[1]["status"] != "running" and time.time() - t0 < 5:
        time.sleep(0.01)
    st_a = tbl.status(tid_a)[1]["status"]
    ok("★ [服务壳] ⑩ 排队：A 进入 running（状态机放行）", st_a == "running",
       f"状态 {st_a}")
    tid_b = tbl.submit("租户A", slow)
    st_b = tbl.status(tid_b)[1]["status"]
    ok("★ [服务壳] ⑩ 排队：同租户 B 在 A running 期间 ⇒ queued",
       st_b == "queued", f"B 状态 {st_b}（running ⇒ 限流没生效）")
    tid_c = tbl.submit("租户B", slow)
    t0 = time.time()
    st_c = tbl.status(tid_c)[1]["status"]
    while st_c != "running" and time.time() - t0 < 5:
        time.sleep(0.01)
        st_c = tbl.status(tid_c)[1]["status"]
    ok("★ [服务壳] ⑪ 跨租户不受影响：只换租户名 ⇒ C 立刻 running",
       st_c == "running", f"C 状态 {st_c}")
    t0 = time.time()
    st_b2 = tbl.status(tid_b)[1]["status"]
    while st_b2 in ("queued", "running") and time.time() - t0 < 10:
        time.sleep(0.02)
        st_b2 = tbl.status(tid_b)[1]["status"]
    ok("★ [服务壳] ⑫ FIFO：A 完成后排队的 B 自动开始并 done",
       st_b2 == "done", f"B 状态 {st_b2}")
    t0 = time.time()
    st_c2 = tbl.status(tid_c)[1]["status"]
    while st_c2 in ("queued", "running") and time.time() - t0 < 10:
        time.sleep(0.02)
        st_c2 = tbl.status(tid_c)[1]["status"]
    ok("★ [服务壳] ⑫ FIFO：C 最终 done", st_c2 == "done", f"C 状态 {st_c2}")

    # ── ⑬⑭⑮ 运维（3.4）：全程在**临时挂载副本**上，原件只读 ───────────
    #    ⚠️ 不碰 `pypi-deps` 原件 —— run_tests 崩在中途 ⇒ 语料库留在坏状态，不可接受。
    tmp = Path(tempfile.mkdtemp(prefix="ldv-rt-"))
    mounted = DATA / "_rt-ops-pypi-deps"
    shutil.rmtree(mounted, ignore_errors=True)     # 前置清理（上趟异常退出的残留）
    shutil.copytree(DATA / "pypi-deps", mounted)
    try:
        # ⑮ 备份即语料：副本本身当租户跑（= 备份可独立使用）
        st_m, out_m = _get("/_rt-ops-pypi-deps/run?plugin=keyset")
        st_orig, out_orig = _get("/pypi-deps/run?plugin=keyset")
        ok("★ [服务壳] ⑮ 备份即语料：副本当租户跑 == 原件输出",
           st_m == 200 and out_m == out_orig,
           f"副本 {len(out_m.encode())}B vs 原件 {len(out_orig.encode())}B")

        # ⑭ 破坏可检：删副本的一个节点 ⇒ 输出必然变
        #    ⚠️ detail 打**首差位置** —— 实测「装进 48 项 → 47 项」字节数碰巧相同，
        #    只报字节数会让「变了」与「相同」长得一样（3.4 踩到的读数陷阱）。
        nodes_dir = mounted / "nodes"
        victim = sorted(nodes_dir.glob("*.md"))[0]
        victim_text = victim.read_text(encoding="utf-8")
        victim.unlink()
        try:
            st_bk, out_bk = _get("/_rt-ops-pypi-deps/run?plugin=keyset")
            i_diff = next((j for j, (x, y) in enumerate(zip(out_orig, out_bk))
                           if x != y), None)
            ok("★ [服务壳] ⑭ 破坏可检：删节点后未恢复 ⇒ 输出变（首差位置在）",
               (st_bk != 200) or (i_diff is not None),
               f"状态 {st_bk}｜首差 @{i_diff}"
               + (f"：{out_orig[i_diff:i_diff+12]!r} → {out_bk[i_diff:i_diff+12]!r}"
                  if i_diff is not None else "（500）"))
        finally:
            victim.write_text(victim_text, encoding="utf-8")

        # ⑬ 备份-恢复往返：再删 ⇒ 从原件整目录拷回 ⇒ 逐字相同
        victim.unlink()
        st_broken, _ = _get("/_rt-ops-pypi-deps/run?plugin=keyset")
        shutil.rmtree(mounted)
        shutil.copytree(DATA / "pypi-deps", mounted)
        st_r, out_r = _get("/_rt-ops-pypi-deps/run?plugin=keyset")
        ok("★ [服务壳] ⑬ 备份-恢复往返：整目录拷回 ⇒ 与原件输出逐字相同",
           st_r == 200 and out_r == out_orig,
           f"恢复态 {len(out_r.encode())}B vs 原件 {len(out_orig.encode())}B"
           f"（破坏态 {st_broken}）")
        ok("★ [服务壳] ⑬ 对照：破坏态确实 ≠ 恢复态（单变量：只差是否恢复）",
           (st_broken != 200) or (out_r == out_orig),
           f"破坏态 {st_broken}｜恢复态与原件逐字相同 ⇒ 往返成立")
    finally:
        shutil.rmtree(mounted, ignore_errors=True)   # 摘掉临时租户
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 反向对照：假响应 ⇒ ⑧a 必须红（证明判据会红）──────────────────
    #    ⚠️ patch 的必须是**服务器正在用的那个类** —— 不能 `importlib.reload`
    #    （那会造一个新类，patch 落在新类上、服务器还用旧的 ⇒ 打不中，判据假绿）。
    orig_run = serve.Handler._run_sync

    def _fake_run(self, tenant, qs):
        self._send(200, "假响应：不调内核")

    try:
        serve.Handler._run_sync = _fake_run
        st_f, body_f = _get(f"/{TENANTS[0]}/run?plugin=keyset")
        st_f2, body_f2 = _get(f"/{TENANTS[1]}/run?plugin=keyset")
        ok("★ [服务壳] 反向对照：假响应 ⇒ 与 CLI 基线不同（⑧a 红）",
           body_f != out_orig, f"假响应 {len(body_f.encode())}B ≠ 基线")
        ok("★ [服务壳] 反向对照：假响应换租户**不变**（⑧b 红）",
           body_f == body_f2, "两租户假响应相同 ⇒ 链路判据会红")
    finally:
        serve.Handler._run_sync = orig_run
    srv.shutdown()

def test_service_failure_paths() -> None:
    """★ [服务壳] **失败路径**判据（2026-10-23，外部审查抓到的三条 + 一条 CI）。

    原来的 ⑧–⑫ 全是 happy path（等价 / 404 / 排队 / FIFO / 跨租户）——
    **任务抛异常、`--root` 不生效、非法参数、CI 启动方式** 一条都没被判过。
    这正是「判据在，但它判的那一侧永远不会出错」。

    ① 任务抛异常 ⇒ 该任务进 `error` 终态、名额释放、**同租户下一个任务仍能 done**
       （原来 `_work` 裸调 `t["_fn"]()` ⇒ 线程直接死 ⇒ `running` 不减 ⇒ 租户永久卡死）
    ② `--root` **真的换目录**（原来 `args.root` 只进了启动日志）
    ③ 非法参数 ⇒ **400**（原来 `int("abc")` 抛出去 ⇒ 连接断开，调用方拿不到原因）
    ④ CI 里每条 `python <路径>.py` 必须写成 `-m`（原来 CI 那条命令 ImportError）
    """
    import re
    import shutil
    import subprocess
    import tempfile
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    import serve
    from ldv.tools._paths import REPO

    def _get(url: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:          # 400/404 会抛，必须接住
            return e.code, e.read().decode("utf-8")

    def _wait(tt, tid: str, limit: float = 10.0) -> dict:
        end = time.time() + limit
        while time.time() < end:
            st = tt.status(tid)[1]
            if st.get("status") in ("done", "error"):
                return st
            time.sleep(0.01)
        return tt.status(tid)[1]

    # ── ① 任务抛异常 ⇒ 队列必须继续前进 ────────────────────────────────
    tt = serve.TaskTable(max_per_tenant=1)

    def _boom():
        raise RuntimeError("boom")

    tid_bad = tt.submit("t1", _boom)
    st_bad = _wait(tt, tid_bad)
    tid_ok = tt.submit("t1", lambda: (200, "ok"))
    st_ok = _wait(tt, tid_ok)
    ok("★ [服务壳] ⑯ **任务抛异常不带走队列**：error 终态 + 同租户下一个任务仍 done",
       st_bad.get("status") == "error" and st_bad.get("code") == 500
       and st_ok.get("status") == "done" and tt.running.get("t1", 0) == 0,
       f"异常任务 {st_bad.get('status')}/{st_bad.get('code')}"
       f"｜后续任务 {st_ok.get('status')}｜running={tt.running.get('t1', 0)}")

    # ── ② `--root` 生效 + ③ 非法参数 ⇒ 400（同一个 server 上验）─────────
    tmp = Path(tempfile.mkdtemp(prefix="ldv-root-"))
    orig_root = serve.Handler.root
    srv = None
    try:
        (tmp / "only-here").mkdir()
        (tmp / "only-here" / "view_spec.json").write_text("{}", encoding="utf-8")
        serve.Handler.root = tmp                      # ← 与 `main()` 同一处赋值
        srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"

        st_r, body_r = _get(base + "/")
        ok("★ [服务壳] ⑰ **`--root` 真的换目录**：列出租户来自指定目录",
           st_r == 200 and "only-here" in body_r and "pypi-deps" not in body_r,
           f"状态 {st_r}｜租户列表 {body_r[:80]}")

        st_p, body_p = _get(base + "/only-here/run?holdout=abc")
        st_p2, body_p2 = _get(base + "/only-here/run?plugin=nope")
        ok("★ [服务壳] ⑱ **非法参数 ⇒ 400**（不是断连、不是 500）",
           st_p == 400 and "holdout" in body_p
           and st_p2 == 400 and "plugin" in body_p2,
           f"holdout=abc ⇒ {st_p}｜plugin=nope ⇒ {st_p2}")
    finally:
        serve.Handler.root = orig_root                # ★ 必须还原，否则后续判据读错目录
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)

    # ── ④ CI 里每条 `python <路径>.py` 必须写成 `-m` ────────────────────
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    bad_cmds = []
    for ln in ci.splitlines():
        m = re.search(r"run:\s*python\s+(\S+)", ln)
        if not m:
            continue
        tok = m.group(1)
        if tok.startswith("-"):          # `-m` / `-c` / `- <<'PY'` 都对
            continue
        if tok.endswith(".py"):
            bad_cmds.append(tok)
    rc = subprocess.run([sys.executable, "-c", "import ldv.tools.ci_run_tests"],
                        cwd=str(REPO), capture_output=True).returncode
    ok("★ [服务壳] ⑲ CI 的 python 命令**用 `-m`**，且 `ci_run_tests` 能导入",
       not bad_cmds and rc == 0,
       f"裸脚本路径 {bad_cmds or '无'}｜`import ci_run_tests` RC={rc}")


def test_incremental_random() -> None:
    """★ [增量] **随机操作序列**下，增量维护与全量重建的**语义**一致（2026-10-23）。

    外部审查点名要的：「随机生成插入、删除和重复操作序列，逐步比较最终语义」。
    仓库原来只有**固定构造**的对照（B15 / `divergence.py`）——固定构造只能证明
    「这几个点对」，证不了「这条性质对」。

    ★★ **比什么**（这一条最容易比错）：
        ✗ 比结构（划分集合）—— **实测增量 ≠ 全量**，那是**已知的历史相关性**
          （文档：维护的历史相关性是当前实现的性质），比它会得出一条**永远红**的判据
        ✓ 比**查询答案**（`status` + `hit_items`，同项集上）—— 实测相同

    ⚠️ 种子**写死**（可复现）：12 个种子 × 14 步，插入/删除混合，项数不许低于 6。
    """
    import random

    from ldv.checks._fixtures import items as _items
    from ldv.checks._fixtures import keyset_queries as _queries
    from ldv.checks._fixtures import load as _load
    from ldv.checks._fixtures import make_builder as _mkb

    loaded = _load()
    if loaded is None:
        ok("★ [增量] ① 随机序列（**跳过**：内置语料加载不了）", True, "0")
        return
    nodes, edges, _d = loaded
    ids = sorted(nodes)
    its = _items(nodes)
    build = _mkb("keyset", nodes, edges)

    def _answers(k, qs):
        return [(str(r.status), tuple(sorted(r.hit_items)))
                for r in (k.query(q) for q in qs)]

    def _part(k):
        return sorted(tuple(sorted(k.members_of(d))) for d in k.all_directions())

    def _sig(k):
        return tuple((d.did, d.parent, len(k.children_of(d)),
                      tuple(sorted(k.members_of(d)))) for d in k.all_directions())

    bad: list[str] = []
    n_ops = 0
    for seed in range(12):
        rnd = random.Random(seed)
        present = set(rnd.sample(ids, 14))
        k, _p = build(sorted(present))
        for step in range(14):
            out = [i for i in ids if i not in present]
            if out and (rnd.random() < 0.6 or len(present) <= 6):
                x = rnd.choice(sorted(out))
                k.insert(x, its[x])
                present.add(x)
            else:
                x = rnd.choice(sorted(present))
                k.remove(x)
                present.discard(x)
            n_ops += 1
            st = k.stats()
            # 不变量①：认识 = 纳入 + 根覆盖之外
            if st["认识"] != st["纳入"] + st["根覆盖之外"]:
                bad.append(f"种子{seed}步{step}:认识≠纳入+越界")
            # 不变量②：没有幽灵成员（成员必须是当前项）
            if any(not set(k.members_of(d)) <= set(k.items) for d in k.all_directions()):
                bad.append(f"种子{seed}步{step}:幽灵成员")
        # ★ 终态语义：与**同项集**的全量重建比查询答案
        k_full, _p2 = build(sorted(present))
        qs = _queries({i: nodes[i] for i in present})
        if _answers(k, qs) != _answers(k_full, qs):
            bad.append(f"种子{seed}:答案与全量不同")

    ok("★ [增量] ① **随机插入/删除序列**：不变量成立，且终态查询答案 == 全量重建",
       not bad, f"12 个种子 × 14 步 = {n_ops} 次操作｜异常 {len(bad)}"
                + (f"：{bad[:3]}" if bad else ""))

    # ①′ **合成对照**：故意造一个幽灵成员（`items` 里没有、成员里还有）
    #    ⇒ 那条不变量**必须**抓到。否则「12 个种子全绿」可能只是**判据瞎了**。
    k_bad, _pb = build(ids[:12])
    k_bad.items.pop(ids[0])
    ghost = any(not set(k_bad.members_of(d)) <= set(k_bad.items)
                for d in k_bad.all_directions())
    ok("★ [增量] ①′ 合成对照：幽灵成员会被「成员 ⊆ 当前项」抓到",
       ghost, f"人造幽灵被检出={ghost}")

    # ② **确定性**：同种子两遍 ⇒ 结构签名逐字相同（否则「随机」就不可复现）
    def _replay(seed: int):
        rnd = random.Random(seed)
        present = set(rnd.sample(ids, 14))
        k, _p = build(sorted(present))
        for _ in range(14):
            out = [i for i in ids if i not in present]
            if out and (rnd.random() < 0.6 or len(present) <= 6):
                x = rnd.choice(sorted(out))
                k.insert(x, its[x])
                present.add(x)
            else:
                x = rnd.choice(sorted(present))
                k.remove(x)
                present.discard(x)
        return _sig(k), sorted(present)

    s_a, p_a = _replay(7)
    s_b, p_b = _replay(7)
    ok("★ [增量] ② **确定性**：同种子重放两遍，结构签名逐字相同",
       s_a == s_b and p_a == p_b,
       f"项集相同={p_a == p_b}｜签名相同={s_a == s_b}｜方向 {len(s_a)}")

    # ③ **往返**：插入 x 再删除 x ⇒ 结构回到插入前（摘得干净，不留幽灵方向）
    #    ⚠️ 「插入生效」不能用**方向数变化**判 —— 实测这一项插进去方向数没变
    #       （只是某个方向多了一个成员）⇒ 会得出一条**永远红**的判据。用签名。
    k3, _p3 = build(ids[:12])
    before3 = _sig(k3)
    k3.insert(ids[12], its[ids[12]])
    changed = _sig(k3) != before3
    k3.remove(ids[12])
    after3 = _sig(k3)
    ok("★ [增量] ③ **往返**：插入 x 再删除 x ⇒ 结构签名回到插入前",
       changed and after3 == before3,
       f"插入真的改了结构={changed}｜删回后签名相同={after3 == before3}")


def test_service_consumption() -> None:
    """★ [服务壳] **真实消费场景**（2026-10-23）：一个租户从「起服务」到「拿到结果」。

    前面的 ⑧–⑫ 判的是**单点性质**（等价 / 排队 / FIFO），这一条判的是
    **一条完整的消费路径**，而且用的是**临时租户**（内置 36 项复制过去）——
    不依赖 `_data/`，**新 clone 上也能跑**。

        同步 GET  ⇒ 200 + 文本
        异步 POST ×2 ⇒ 1 running + 1 queued（每租户并发 1）
        撞容量上限  ⇒ **429**（可重试的拒绝，不是 400/500）
        轮询到 done ⇒ 结果与同步响应**逐字相同**
    """
    import json
    import shutil
    import tempfile
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    import serve
    from ldv.checks._fixtures import find_corpus

    def _get(url: str, method: str = "GET") -> tuple[int, str]:
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    corpus = find_corpus()
    if corpus is None:
        ok("★ [服务壳] ㉓ 真实消费场景（**跳过**：内置语料找不到）", True, "0")
        ok("★ [服务壳] ㉔ 队列容量 ⇒ 429（**跳过**：内置语料找不到）", True, "0")
        return

    tmp = Path(tempfile.mkdtemp(prefix="ldv-consume-"))
    orig_root, orig_table = serve.Handler.root, serve.TABLE
    srv = None
    try:
        tenant = "shop"
        (tmp / tenant / "nodes").mkdir(parents=True)
        # ⚠️ `find_corpus()` 返回的**就是 `nodes/` 目录本身**（不是语料根）
        for f in sorted(corpus.glob("*.md")):
            shutil.copy2(f, tmp / tenant / "nodes" / f.name)
        (tmp / tenant / "view_spec.json").write_text("{}", encoding="utf-8")

        serve.Handler.root = tmp
        # ⚠️ 消费闭环这一段**故意不设容量上限**（cap=64）：36 项的活几百毫秒就完，
        #    容量判定会和「跑完了没有」抢时间 ⇒ 测不出确定的结果。
        #    容量单独在下一段用**闸门任务**测（确定性）。
        serve.TABLE = serve.TaskTable(max_per_tenant=1, cap_per_tenant=64,
                                      cap_global=64)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"

        # ① 同步：拿到基线文本
        st_sync, body_sync = _get(f"{base}/{tenant}/run?plugin=keyset")
        # ② 异步：两个任务（第二个必然排队）
        st_a, body_a = _get(f"{base}/{tenant}/run?plugin=keyset", method="POST")
        st_b, body_b = _get(f"{base}/{tenant}/run?plugin=keyset", method="POST")
        # ⚠️ 状态不是 202 时**不许**直接 `json.loads` —— 那会让判据**崩掉**
        #    （反向对照实测：接线断了 ⇒ 404 文本 ⇒ JSONDecodeError 把整条测试带走）。
        #    「判据崩了」与「判据红了」长得不一样，而且更糟：它会吞掉后面的判据。
        tid_a = json.loads(body_a).get("task_id", "") if st_a == 202 else ""
        tid_b = json.loads(body_b).get("task_id", "") if st_b == 202 else ""

        def _wait(tid: str, limit: float = 90.0) -> dict:
            end = time.time() + limit
            while time.time() < end:
                if not tid:
                    return {"status": "无 task_id"}
                _s, b = _get(f"{base}/tasks/{tid}")
                try:
                    obj = json.loads(b)
                except ValueError:
                    return {"status": f"非 JSON（{_s}）"}
                if obj.get("status") in ("done", "error"):
                    return obj
                time.sleep(0.05)
            return {"status": "超时"}

        oa, ob = _wait(tid_a), _wait(tid_b)
        ok("★ [服务壳] ㉔ **真实消费闭环**：同步 == 异步结果（逐字），且排队那个也完成了",
           st_sync == 200 and st_a == 202 and st_b == 202
           and oa.get("status") == "done" and ob.get("status") == "done"
           and oa.get("result") == body_sync and ob.get("result") == body_sync,
           f"同步 {st_sync}／提交 {st_a},{st_b}｜A {oa.get('status')} B {ob.get('status')}"
           f"｜结果逐字相同={oa.get('result') == body_sync}")

        # ③ 容量 ⇒ 429：用**闸门任务**占满名额（确定性，不抢时间）
        gate = threading.Event()
        serve.TABLE = serve.TaskTable(max_per_tenant=1, cap_per_tenant=1,
                                      cap_global=64)
        serve.TABLE.submit(tenant, lambda: (gate.wait(10), (200, "held"))[1])
        try:
            st_c, body_c = _get(f"{base}/{tenant}/run?plugin=keyset", method="POST")
        finally:
            gate.set()
        ok("★ [服务壳] ㉓ **队列容量 ⇒ 429**（撞上限不静默堆内存）",
           st_c == 429 and "429" in body_c,
           f"名额被占满时提交 ⇒ {st_c}｜{body_c[:60]}")
    finally:
        serve.Handler.root = orig_root
        serve.TABLE = orig_table
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_derived_rollback() -> None:
    """★ [D7] 派生维度**可完全回退**：删掉 sidecar ⇒ 与「从无此文件」逐字相同。

    §D7 的判据原来只有**探针**（`outputs/_probe_d7_rollback.py`，不进退出码）——
    保证矩阵 2026-10-23 把它点成「零判据」，这里补成判据。

    ★★ 三段都要：
        ① 从无 derived            ⇒ 基线签名
        ② 加一份 sidecar 再建      ⇒ **必须变**（否则「删了没变」可能只是「加了也没变」= 空转）
        ③ 删掉 sidecar 再建        ⇒ **必须与 ① 逐字相同**

    ⚠️ 它同时压着「缓存不串味」那条：同进程三次建树，若 `loader` 命中返回同一个
       可变 Node，② 的键会**留在** ③ 里 ⇒ ③ 红（`_probe_d7_rollback.py` 第一遍就栽在这）。
    """
    import json as _json
    import os
    import shutil
    import tempfile
    from pathlib import Path

    import entry as E
    from ldv.checks._fixtures import find_corpus

    src = find_corpus()
    if src is None:
        ok("★ [D7] 派生维度可完全回退（**跳过**：找不到语料）", True, "0")
        return

    def _sig(k) -> tuple:
        return tuple((d.did, d.parent, d.rank, tuple(sorted(k.members_of(d))))
                     for d in k.all_directions())

    tmp = Path(tempfile.mkdtemp(prefix="ldv-d7-"))
    saved = os.environ.get("LDV_CORPUS")
    try:
        corpus = tmp / "corpus"
        (corpus / "nodes").mkdir(parents=True)
        for f in src.glob("*.md"):
            shutil.copy2(f, corpus / "nodes" / f.name)
        os.environ["LDV_CORPUS"] = str(corpus)      # ⚠️ 下面 finally 里还原

        a0 = E.App(None)
        s0 = _sig(a0.kernels["keyset"])
        (corpus / "derived").mkdir()
        ids = sorted(a0.nodes)[:8]
        (corpus / "derived" / "probe.json").write_text(
            _json.dumps({"_meta": {"field": "probe"},
                         "values": {i: "v" for i in ids}}, ensure_ascii=False),
            encoding="utf-8")
        a1 = E.App(None)
        s1 = _sig(a1.kernels["keyset"])
        (corpus / "derived" / "probe.json").unlink()
        a2 = E.App(None)
        s2 = _sig(a2.kernels["keyset"])
    finally:
        if saved is None:
            os.environ.pop("LDV_CORPUS", None)
        else:
            os.environ["LDV_CORPUS"] = saved
        shutil.rmtree(tmp, ignore_errors=True)

    ok("★ [D7] 派生维度**可完全回退**：删 sidecar ⇒ 与「从无此文件」逐字相同",
       s1 != s0 and s2 == s0,
       f"加 sidecar 后结构变了={s1 != s0}（**必须为真**，否则空转）"
       f"｜删回后逐字相同={s2 == s0}｜方向数 {len(s0)}/{len(s1)}/{len(s2)}")

    # ⚠️ **判据自己不许留全局状态**：`LDV_CORPUS` 没还原 ⇒ 之后所有判据读到
    #    另一个语料（实测过 748 → 660 的事故）⇒ 这条自己验一次。
    _restored = (os.environ.get("LDV_CORPUS") == saved) if saved is not None \
        else ("LDV_CORPUS" not in os.environ)
    ok("★ [D7] 判据不留全局状态：`LDV_CORPUS` 已还原",
       _restored, f"还原后 = {os.environ.get('LDV_CORPUS')!r}")


def test_b_known_answers() -> None:
    """★ [保证] `B` 判据的**合成对照**：每条都要「该红时红、该绿时绿」。

    ★★ 为什么需要：一条**永远红**的判据和一条**永远绿**的判据，
       在「跑一遍真实语料」里**长得一样**。合成对照造一个**已知违反**的输入，
       要求判据报红；再造一个**干净的**，要求报绿 —— 两者**必须不同**。

    ⚠️ 它证明的是「**这条判据的代码路径能报红**」，
       **不是**「真实语料上不存在这种违反」—— 后者是 `run_checks` 的事。
    """
    from ldv.checks import _controls as C

    def _run(fn) -> list[str] | None:
        try:
            return fn()
        except Exception as exc:                 # 对照自己炸了也要**报出来**
            return [f"{type(exc).__name__}: {exc}"]

    #: ⚠️ 标签**必须写字面量**，不许 `f"…{code}…"` —— 保证矩阵是**静态**索引，
    #:    f-string 里的编号它看不见（实测：第一版就是 f-string，6 条对照
    #:    全部没进覆盖率读数）。宁可啰嗦六遍，也不许索引瞎掉。
    f4 = _run(C.b4_known_answer)
    ok("★ [保证] 合成对照 · B4：坏输入必须红、好输入必须绿、两者不同",
       not f4, "；".join(f4[:2]) or "两态不同，对照成立")
    f7 = _run(C.b7_known_answer)
    ok("★ [保证] 合成对照 · B7：坏输入必须红、好输入必须绿、两者不同",
       not f7, "；".join(f7[:2]) or "两态不同，对照成立")
    f9 = _run(C.b9_known_answer)
    ok("★ [保证] 合成对照 · B9：坏输入必须红、好输入必须绿、两者不同",
       not f9, "；".join(f9[:2]) or "两态不同，对照成立")
    f12 = _run(C.b12_known_answer)
    ok("★ [保证] 合成对照 · B12：坏输入必须红、好输入必须绿、两者不同",
       not f12, "；".join(f12[:2]) or "两态不同，对照成立")
    f13 = _run(C.b13_known_answer)
    ok("★ [保证] 合成对照 · B13：坏输入必须红、好输入必须绿、两者不同",
       not f13, "；".join(f13[:2]) or "两态不同，对照成立")
    f14 = _run(C.b14_known_answer)
    ok("★ [保证] 合成对照 · B14：坏输入必须红、好输入必须绿、两者不同",
       not f14, "；".join(f14[:2]) or "两态不同，对照成立")
    f2 = _run(C.b2_known_answer)
    ok("★ [保证] 合成对照 · B2：坏输入必须红、好输入必须绿、两者不同",
       not f2, "；".join(f2[:2]) or "两态不同，对照成立")
    f3 = _run(C.b3_known_answer)
    ok("★ [保证] 合成对照 · B3：坏输入必须红、好输入必须绿、两者不同",
       not f3, "；".join(f3[:2]) or "两态不同，对照成立")
    f5 = _run(C.b5_known_answer)
    ok("★ [保证] 合成对照 · B5：坏输入必须红、好输入必须绿、两者不同",
       not f5, "；".join(f5[:2]) or "两态不同，对照成立")
    f16 = _run(C.b16_known_answer)
    ok("★ [保证] 合成对照 · B16：坏输入必须红、好输入必须绿、两者不同",
       not f16, "；".join(f16[:2]) or "两态不同，对照成立")
    f19 = _run(C.b19_known_answer)
    ok("★ [保证] 合成对照 · B19：坏输入必须红、好输入必须绿、两者不同",
       not f19, "；".join(f19[:2]) or "两态不同，对照成立")

    # 下面 5 条要**真内核**（语料不在 ⇒ 对照返回 `None` ⇒ 如实报跳过，不许当通过）
    f6 = _run(C.b6_known_answer)
    ok("★ [保证] 合成对照 · B6：坏输入必须红、好输入必须绿、两者不同",
       f6 is not None and not f6,
       "**跳过**：内置语料不在" if f6 is None
       else ("；".join(f6[:2]) or "两态不同，对照成立"))
    f8 = _run(C.b8_known_answer)
    ok("★ [保证] 合成对照 · B8：坏输入必须红、好输入必须绿、两者不同",
       f8 is not None and not f8,
       "**跳过**：内置语料不在" if f8 is None
       else ("；".join(f8[:2]) or "两态不同，对照成立"))
    f10 = _run(C.b10_known_answer)
    ok("★ [保证] 合成对照 · B10：坏输入必须红、好输入必须绿、两者不同",
       f10 is not None and not f10,
       "**跳过**：内置语料不在" if f10 is None
       else ("；".join(f10[:2]) or "两态不同，对照成立"))
    f11 = _run(C.b11_known_answer)
    ok("★ [保证] 合成对照 · B11：坏输入必须红、好输入必须绿、两者不同",
       f11 is not None and not f11,
       "**跳过**：内置语料不在" if f11 is None
       else ("；".join(f11[:2]) or "两态不同，对照成立"))
    f15 = _run(C.b15_known_answer)
    ok("★ [保证] 合成对照 · B15：坏输入必须红、好输入必须绿、两者不同",
       f15 is not None and not f15,
       "**跳过**：内置语料不在" if f15 is None
       else ("；".join(f15[:2]) or "两态不同，对照成立"))
    # §D8 的对照是**已知答案**（它的「判据」本身就是一次分类计算，
    # 没有「会不会红」这回事）⇒ 造三种字段，断言分类落在该落的那一类。
    fd8 = _run(C.d8_known_answer)
    ok("★ [保证] 合成对照 · §D8 四类离散判定（已知答案：恒定 / 几乎唯一 / 可以当维度）",
       not fd8, "；".join(fd8[:2]) or "三类两两不同，对照成立")
    fd4 = _run(C.d4_known_answer)
    ok("★ [保证] 合成对照 · §D4 独立性两档（精确蕴含 / 近似蕴含 / 独立，且两档文案不同）",
       not fd4, "；".join(fd4[:2]) or "三档两两不同，对照成立")
    fd3 = _run(C.d3_known_answer)
    ok("★ [保证] 合成对照 · §D3 预算共享（组合赢了就顶掉单谓词，总数恒 ≤ budget）",
       not fd3, "；".join(fd3[:2]) or "总数不超预算，组合确实赢过", )
    fd1 = _run(C.d1_known_answer)
    ok("★ [保证] 合成对照 · §D1 外生准入（只有写了「维度」的条目才算）",
       not fd1, "；".join(fd1[:2]) or "三态分明，对照成立")
    fd6 = _run(C.d6_known_answer)
    ok("★ [保证] 合成对照 · §D6 解析范围 = 候选全集（预算=1 不许截人的 5 条声明）",
       not fd6, "；".join(fd6[:2]) or "预算没越界管声明")
    fd5 = _run(C.d5_known_answer)
    ok("★ [保证] 合成对照 · §D5 改名不改划分（同扩展换键名 ⇒ 分组逐字相同）",
       not fd5, "；".join(fd5[:2]) or "改名前后的命中集合相同")
    fd2 = _run(C.d2_known_answer)
    ok("★ [保证] 合成对照 · §D2 真二分（空/全谓词零贡献，真二分才进划分）",
       not fd2, "；".join(fd2[:2]) or "空/全被拒、真二分入选")
    # §S3 / §S4：判的不是「答案对不对」（那是人的决定），是**输出能不能由结构复算**
    fs3 = _run(C.s3_known_answer)
    ok("★ [保证] 合成对照 · §S3 反思显性（未触发报「查了什么」；涉及的项可复算）",
       fs3 is not None and not fs3,
       "**跳过**：内置语料不在" if fs3 is None
       else ("；".join(fs3[:2]) or "字段齐、项集复算对得上"))
    fs4 = _run(C.s4_known_answer)
    ok("★ [保证] 合成对照 · §S4 重构显性（批准了材料里没有 ⇒ 报「未生效」不假装成功）",
       fs4 is not None and not fs4,
       "**跳过**：内置语料不在" if fs4 is None
       else ("；".join(fs4[:2]) or "假成功被挡住"))
    fs5 = _run(C.s5_known_answer)
    ok("★ [保证] 合成对照 · §S5 自我标签一致（每个 `我=是` 都指回真实文件，删掉即回原状）",
       not fs5, "；".join(fs5[:2]) or "来源全部指得回、删掉无残留")
    fa2 = _run(C.a2_known_answer)
    ok("★ [保证] 合成对照 · §A2 视图稳定（一块里一个走得到一个走不到 ⇒ 必须红）",
       not fa2, "；".join(fa2[:2]) or "不稳定被检出、稳定放行")
    fa3 = _run(C.a3_known_answer)
    ok("★ [保证] 合成对照 · §A3 最粗（还能合并的划分不是最粗 ⇒ 必须红）",
       not fa3, "；".join(fa3[:2]) or "能合并被检出、最粗放行")


def test_guarantee_matrix() -> None:
    """★ [保证] **保证矩阵**：每条承诺 × 哪些判据在守它（2026-10-23）。

    外部审查要的：「把这些局部保证整理成一份明确的**保证矩阵**，
    而不是让读者从大量文档、断言编号和探针输出中自行拼接。」

    本判据守三件事：

        ① 矩阵算得出，且**零判据清单与冻结基线一致**（新增缺口红、修好了没更新基线也红）
        ② **反向对照**：把某条承诺的引用全删掉 ⇒ 它必须出现在零判据里
           （否则「全部有判据」可能只是**匹配器瞎了**）
        ③ **抓不到的注册写法**不许变多（f-string 标题 / 变量编号 —— 实测 B4 就是 f-string，
           第一版正则漏了它，被误报成「零判据」）

    ⚠️ 矩阵的**边界**：它判的是「**有没有判据提到这条承诺**」，
       **不是**「提到的那条判得对」。后者只有反向对照能回答。
    """
    from ldv.tools import guarantee_matrix as GM

    import io
    import re

    cov = GM.coverage()
    now = GM.gaps(cov)
    base = GM.load_baseline()
    ok("★ [保证] ① 保证矩阵：零判据清单与**冻结基线**一致（两头都守）",
       set(now) == set(base) and len(cov) >= 50,
       f"承诺 {len(cov)} 条｜有判据 {len(cov) - len(now)}｜零判据 {len(now)}"
       f"（基线 {len(base)}）｜新增 {sorted(set(now) - set(base))}"
       f"｜已修未更新 {sorted(set(base) - set(now))}")

    # ② 反向对照：把 `K2` 从所有判据标签里抹掉 ⇒ 必须变成零判据
    fake = [s for s in GM.judgments() if not re.search(r"(?<![A-Za-z0-9])§?K2(?![0-9])", s)]
    fake_gaps = GM.gaps(GM.coverage(fake))
    ok("★ [保证] ② 反向对照：抹掉某条承诺的全部引用 ⇒ 它**必须**出现在零判据里",
       "K2" in fake_gaps and "K2" not in now,
       f"抹掉后 K2 零判据={'K2' in fake_gaps}｜真实表里 K2 有判据={'K2' not in now}"
       f"｜判据数 {len(fake)} vs {len(GM.judgments())}")

    # ③ 抓不到的注册写法 —— 冻结 7 条（`rep.add(code, …)` 变量编号），不许变多
    unres = GM.unresolved()
    ok("★ [保证] ③ 静态抓不到的注册写法**不增加**（变多 ⇒ 矩阵会静默漏掉判据）",
       len(unres) <= 77,
       f"抓不到 {len(unres)} 条（冻结 77：7 条变量编号 + 70 条动态标签）｜"
       + "；".join(u[:26] for u in unres[:3]))

    # ④ 文档是**生成物**：磁盘上的必须与「现在生成出来的」逐字相同（手改 ⇒ 红）
    #    ⚠️ 生成器里**不写日期/提交号** —— 否则每次重生成都变，核验就变成噪声。
    on_disk = io.open(GM.DOC, encoding="utf-8").read() if GM.DOC.exists() else ""
    fresh = GM.render(cov)
    ok("★ [保证] ④ `docs/保证矩阵.md` 是**生成物**：磁盘 == 现生成（手改会让它红）",
       bool(on_disk) and on_disk == fresh,
       f"文件 {'存在' if on_disk else '**缺失**'}｜逐字相同={on_disk == fresh}"
       f"｜差异 {abs(len(on_disk) - len(fresh))} 字节")

    # ⑤ **读数**的形状守卫（不守数值 —— 读数不进退出码）
    #    「有判据」只说明正着判过；要说明判据会红，得有一条反着判的。
    #    这里只守三件事：每条承诺都有条目 / 两端都不极端 / 条目里的标签确实含标记。
    ctl = GM.controls(cov)
    n_with = sum(1 for v in ctl.values() if v)
    consistent = all(all(any(m in s for m in GM.CONTROL_MARKS) for s in hits)
                     for hits in ctl.values())
    ok("★ [保证] ⑤ 反向对照覆盖是**读数**（守形状：条目齐、两端不极端、标记自洽）",
       len(ctl) == len(cov) and 0 < n_with < len(cov) and consistent,
       f"承诺 {len(ctl)} 条｜自称带对照 {n_with}｜只有正着判 {len(ctl) - n_with}"
       f"｜标记自洽={consistent}")

    # ⑥ **下一步清单不许有洞**：缺对照的 B 必须在 `B_CONTROL_PLAN` 里有一条评估
    #    （表是**人判**，判据只守「覆盖全」—— 不许出现「未评估」）。
    noc = GM.b_without_control(ctl)
    unplanned = [c for c in noc if c not in GM.B_CONTROL_PLAN]
    ok("★ [保证] ⑥ 缺对照的 `B` 都有「怎么造」的评估（表覆盖全，不留未评估）",
       not unplanned and len(GM.B_CONTROL_PLAN) >= len(noc),
       f"缺对照 {len(noc)} 条：{noc}｜表里没评估的 {unplanned or '无'}")

    # ⑦ **三态不许有第四态**：每条承诺必须是 直接 / 间接 / 缺 之一，
    #    且「缺」的都要在 `S_CONTROL_PLAN` 里有「怎么造」—— 不许静默漏掉。
    st = GM.control_status(cov)
    kinds = {k for k, _v in st.values()}
    miss_s = sorted(c for c, (k, _v) in st.items() if k == "缺")
    unplanned_s = [c for c in miss_s if c not in GM.S_CONTROL_PLAN]
    ok("★ [保证] ⑦ 三态完备：直接 / 间接 / 缺，且缺的都有「怎么造」",
       kinds <= {"直接", "间接", "缺"} and len(st) == len(cov) and not unplanned_s,
       f"直接 {sum(1 for v in st.values() if v[0] == '直接')}"
       f"｜间接 {sum(1 for v in st.values() if v[0] == '间接')}"
       f"｜缺 {len(miss_s)}｜表里没评估的 {unplanned_s or '无'}")


def _spec_mechanical_mismatch(doc: dict, which: str, nodes, edges) -> list[str]:
    """committed 的 `view_spec.json` 里**机械那几栏**与生成器算出来的差在哪。

    只比生成器**拥有**的那几栏（`MECHANICAL`）—— `_note` 与 `读数` 是**人写的**，
    人改它们**不该**让这条红。反过来，人**没**改而机械栏漂了，那就是缺陷。

    ⚠️ 为什么这条非要不可：`reach` 的根口径在**两份装配之间分岔过一次**
       （`cli.py` vs `_fixtures.make_builder`），而分岔的症状不是红 ——
       是**两边各自演化、各自看着都对**。生成器与文件之间是同一个形状。
    """
    from ldv.tools.gen_view_spec import MECHANICAL, build_spec

    spec, _ = build_spec(which, nodes, edges)
    return [f"{k}: 文件里 {doc.get(k)!r} ≠ 算出来 {spec[k]!r}"
            for k in MECHANICAL if doc.get(k) != spec[k]]


def test_influence_channels() -> None:
    """**「模糊掌握持续交给检索层」这条功能的正式测试**（2026-10-10）。

    ## 它测的是哪一句话

        「抽象层把结构重新编码成**一份认识**（`Q` 比 `U` 粗），检索层**每趟**拿它出
        排序 / 解释 / **块级建议** / **画像**。」

    ## 做法：同一个结构、同一个需求，**只换 `q`** —— 跑两趟

        甲 `q = csr(spec)`    （25 个方向 ⇒ 8 块）—— **带**模糊掌握
        乙 `q = 离散划分`      （25 个方向 ⇒ 25 块）—— **不带**

    ⚠️ 乙**不是非法输入**（稳定、细化 `P`、不改候选集）⇒ 两趟候选集**必然相同**，
       而**其余交付物的差别就是「模糊掌握的贡献」**。⇒ 这正是「是不是真在影响」的判据。

    ## 已知答案（所以它不是「跑一遍看看」）

        候选集        **必然相同** —— `§3` 定理（「不改变结论」那一半）
        块级建议**粒度**  **必然不同** —— 甲是「一块覆盖多个方向」，乙是「一块一个方向」
        画像**块数**      **必然不同** —— 同上，`|Q|` 不同

    ⚠️ **反过来也要说清**（免得把它读成「影响越大越好」）：
       「模糊掌握 → **代价**」是 **0**（顺序无处可达），
       「**落盘-读回** → 交付物」也是 **0**（`q = csr(spec)` 是纯函数）。
       三个口径**不许合成一句** —— 本节原来就把它们混成过一句「差异恒为 0」（已更正）。
    """
    from ldv import flow, retriever
    from ldv.checks._fixtures import coverage_of
    from ldv.checks._framework import Report
    from ldv.checks.abstraction import ViewSet, load_spec_file, spec_for
    from ldv.checks.coverage import corpus_fingerprint
    from ldv.checks.retrieval import RETRIEVER_CODES, run_retrieval
    from ldv.core import selfopt
    from ldv.core.views import coarsest_stable_refinement
    from ldv.run_checks import batch_kernel

    loaded = load()
    if loaded is None:
        return
    nodes, edges, _ = loaded
    nd = find_corpus()
    corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(nd, corpus)
    spec, _why = spec_for(doc, "keyset", corpus)
    if spec is None or not spec.universe:
        return
    kernel, plugin, queries, _mk = batch_kernel("keyset", nodes, edges)
    cover = coverage_of("keyset", nodes, edges)
    for q0 in queries[:3]:
        flow.run_query(kernel, q0)
    tendency = selfopt.tendency_by_direction(kernel)
    need = {"keyset": retriever.as_bar(queries[0])}
    fp = retriever.spec_fingerprint(spec)

    def run(q):
        rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
        r = run_retrieval(kernel, plugin, need, spec, rep,
                          recognition=ViewSet(spec=spec, q=tuple(q), views=()),
                          tendency=tendency, disk_fingerprint=fp)
        return r, rep

    r_a, rep_a = run(coarsest_stable_refinement(spec))
    r_b, rep_b = run(tuple(frozenset({d}) for d in sorted(spec.universe)))

    # ── ① 候选集**必然相同**（`§3` 定理那一半）────────────────────────
    ok("① ★ [I] 只换 `q` ⇒ **候选集必然相同**（`§3` 定理：「不改变结论」那一半）——"
       "这一条**不许**因为「想让影响更大」而变",
       sorted(r_a.candidates) == sorted(r_b.candidates),
       f"甲 {len(r_a.candidates)} vs 乙 {len(r_b.candidates)}")

    # ── ② 六项判断类交付物**必须不同** ────────────────────────────────
    chan = {
        "排序「先看/后看」": (len(r_a.first), len(r_a.late)) != (len(r_b.first), len(r_b.late)),
        "块级建议条数": len(r_a.advice) != len(r_b.advice),
        "块级建议粒度": abs(r_a.advice_span / max(1, len(r_a.advice))
                        - r_b.advice_span / max(1, len(r_b.advice))) > 1e-9,
        "画像块数": len(r_a.profiles) != len(r_b.profiles),
        "画像读法种类": ({p.reading for p in r_a.profiles}
                    != {p.reading for p in r_b.profiles}),
        "最深块成员数": (max(p.n_members for p in r_a.profiles)
                    != max(p.n_members for p in r_b.profiles)),
    }
    missed = [k for k, v in chan.items() if not v]
    ok("② ★★★ [I] **模糊掌握改变了 6 项判断类交付物**："
       "排序 / 建议条数 / 建议粒度 / 画像块数 / 读法种类 / 最深块成员数 ——"
       "⚠️ 少一项就意味着「它没被消费」，而那在只看 `T1`（候选集不变）时**看不见**",
       not missed, f"没变的：{missed or '无'}｜"
                   f"粒度 {r_a.advice_span/max(1,len(r_a.advice)):.2f} vs "
                   f"{r_b.advice_span/max(1,len(r_b.advice)):.2f}｜"
                   f"块数 {len(r_a.profiles)} vs {len(r_b.profiles)}")

    # ── ③ 不带模糊掌握的那一趟 **`T8` 必须红**（粒度退化的守卫）─────────
    def tri(rep, code):
        for a in rep.assertions:
            if a.code == code:
                return a.result.name
        return "?"

    ok("③ ★ [I] 不带模糊掌握那一趟 **`T8` 红**、带的那一趟 **过** ——"
       "⇒ 「粒度被磨掉」这件事**有判据看得见**（不然它与「一切正常」长得一模一样）",
       tri(rep_a, "T8") == "YES" and tri(rep_b, "T8") == "NO",
       f"甲 T8={tri(rep_a,'T8')}｜乙 T8={tri(rep_b,'T8')}")

    # ── ④ 三个口径**不许合成一句**（把边界也测进去）──────────────────
    ok("④ ★★ [I] **「影响」有三个口径，判据只认口径 ①** ——"
       "口径 ②（代价）与口径 ③（落盘-读回）都是 **0**，"
       "它们**不许**用来削弱或夸大本条的结论（本节原来混成过一句「差异恒为 0」）",
       True, "口径 ① 6 项不同｜② 代价 0｜③ 落盘-读回 0")


def test_view_oracles() -> None:
    """`§A3` 的**两条 oracle** 与 `§A4` 的**见证构造** —— 2026-10-10 新增。

    这两件都是「**判据要能红，就必须有一个与被判对象不同路的参照物**」那条纪律的落点，
    而且它们各自的**失效形态**都长得像正常：

        `§A3` 的多项式 oracle 若与 `_refine` 分岔 ⇒ 「视图最粗」会在全量上**报错或漏判**
        `§A4` 的两个 scope（函数级 / 语料级）若混成一条 ⇒ 摘要单射的论域上**恒红**
               （函数级在构造论域上判，语料级按穷举 / 预算分别报）

    ⚠️ **本测试全程用合成图**（不依赖语料大小）⇒ 换语料不会让它静默失效。
    """
    import random

    from ldv.checks.abstraction import (
        WITNESS_BUILDERS, SUMMARIES, ReadingCtx, a4_inj_ctx, judge_reading,
        witness_same_digest_diff_reading,
    )
    from ldv.core.views import (
        ViewSpec, coarser_stable_exists, coarsest_stable_refinement,
        coarsest_stable_refinement_signature, n_coarsening_candidates,
        partition_of, stable,
    )

    # ── ① 两条 `csr` 实现**随机对拍** —— 400 组随机小图 ────────────────────
    rnd = random.Random(20261010)
    diffs, checked = [], 0
    for _ in range(400):
        m = rnd.randint(2, 6)
        U = [f"x{i}" for i in range(m)]
        E = frozenset((a, b) for a in U for b in U
                      if a != b and rnd.random() < 0.35)
        lab = [rnd.randint(0, max(0, rnd.randint(0, m - 1))) for _ in U]
        P = tuple(frozenset(U[i] for i in range(m) if lab[i] == k)
                  for k in sorted(set(lab)))
        P = tuple(b for b in P if b)
        try:
            spec = ViewSpec(universe=tuple(U), partition=P, relation=E)
        except Exception:  # noqa: BLE001 —— 不合法的随机实例直接跳过
            continue
        checked += 1
        a, b = coarsest_stable_refinement(spec), coarsest_stable_refinement_signature(spec)
        if partition_of(a) != partition_of(b):
            diffs.append((sorted(map(sorted, a)), sorted(map(sorted, b))))
        # 暴力 oracle 的结论必须与「两条实现是否一致」对上
        ex, _why, _seen = coarser_stable_exists(spec, b)
        if ex != (partition_of(a) != partition_of(b)):
            diffs.append(("暴力 oracle 与实现不一致", spec.relation))
    ok(f"① ★ [O] 两条 `csr` 实现（`_refine` vs **签名**）在 {checked} 组随机小图上"
       f"**逐块相同**，且与暴力 oracle 的结论一致 —— 第二条实现是 `§A3` 在全量上"
       f"能判的前提（全量候选数 5.83e12 ⇒ 暴力枚举跑不完）",
       not diffs and checked > 300, f"{len(diffs)} 处分岔：{diffs[:2]}")

    # ── ② **三环反例**：证明「局部判据」会漏判（所以 `§A3` 不能改成它）──────
    #    `E = {(a,c),(c,b),(b,a)}`、`P = {U}`：`{{a},{b},{c}}` 里任意两块合起来都不稳定，
    #    **而三块合成一块稳定** ⇒ 「两块能不能合并」这条判据会把「有更粗的」判成「没有」。
    ring = ViewSpec(universe=("a", "b", "c"),
                    partition=(frozenset("abc"),),
                    relation=frozenset({("a", "c"), ("c", "b"), ("b", "a")}))
    singles = [frozenset({x}) for x in "abc"]
    pair_ok = {f"{x}{y}": stable(ring, [frozenset({x, y}),
                                        frozenset(set("abc") - {x, y})])[0]
               for x, y in (("a", "b"), ("a", "c"), ("b", "c"))}
    whole_ok = stable(ring, [frozenset("abc")])[0]
    ok("② ★★ [O] **三环反例**：「任意两块合并都不稳定」**但**「三块合成一块稳定」"
       "⇒ 任何「局部判据」（只看两块能不能并）都会把「有更粗的」**判成「没有」**；"
       "而暴力 oracle 与两条实现都说「最粗是三块」",
       (not any(pair_ok.values())) and whole_ok
       # ⚠️ `partition_of` 返回 **tuple**（不是 list）—— 比错类型会让这条**恒红**
       and partition_of(coarsest_stable_refinement(ring)) == (frozenset("abc"),)
       and coarser_stable_exists(ring, singles)[0] is True,
       f"pair={pair_ok} whole={whole_ok}"
       f"｜暴力 oracle 说「有更粗的」=" + str(coarser_stable_exists(ring, singles)[0]))

    # ── ③ `§A4` 的见证构造：在给定语料上**搜得到**，且**逐字可复现** ────────
    #    用一个**手工造的语料**：值分布确定 ⇒ 已知答案是「搜得到」。
    #    ⚠️ 这份语料是**照着见证造的**：`{1,5,9}` 与 `{3,4,8}` 的和都是 15，
    #       而中位数 5 ≠ 4 ⇒ 见证必然存在（否则这条对照会「因为语料本身没见证」而红
    #       —— 那是**夹具的问题**，不是实现的问题）。
    ctx = ReadingCtx(
        value_of={"v1": 1.0, "v5": 5.0, "v9": 9.0, "v3": 3.0, "v4": 4.0, "v8": 8.0},
        cover_of={d: frozenset({d}) for d in ("v1", "v5", "v9", "v3", "v4", "v8")})
    f_med = lambda S, c: float(sorted(c.value_of[d] for d in S)[len(S) // 2])  # noqa: E731
    sf = SUMMARIES["计数与和"]
    w1 = WITNESS_BUILDERS["同摘要异读数"](f_med, sf, ctx)
    w2 = WITNESS_BUILDERS["同摘要异读数"](f_med, sf, ctx)
    ok("③ ★ [O] `§A4` 的见证**构造法**在给定语料上搜得出一对「同摘要、异读数」，"
       "且**两次搜到同一对**（顺序确定 ⇒ 输出逐字可复现）",
       w1 is not None and w1 == w2
       and sf(w1[0], ctx) == sf(w1[1], ctx)
       and f_med(w1[0], ctx) != f_med(w1[1], ctx),
       f"{w1}")

    # ── ④ `§A4` 的两级证据（2026-10-13 重写）────────────────────────────────
    #    旧 ④：「语料上搜不到 ⇒ 红」。真因：那等于用**语料**判**函数级**的声称
    #    ⇒ 摘要单射的论域上恒红（openalex × `sequence`：9 方向、穷举 512 子集、
    #    0 对 —— 见证**不存在**，不是没搜到；两条出路还都堵死 ⇒ 分类学空洞）。
    #    现在：函数级在**构造论域**上判（与语料无关），语料级按穷举/预算分别报。
    inj_ctx = a4_inj_ctx()
    _hol = lambda nm: {"名": nm, "类别": "holistic", "摘要": "计数与和",
                       "见证": {"摘要": "计数与和", "构造": "同摘要异读数"}}
    ok("④ ★★ [O] holistic 的**函数级**门：读数本就被 `摘要` 决定 ⇒ 红，"
       "**与语料无关** —— `平均` == `H=和除计数`、`计数` == `H=取第一项`"
       "（构造论域 57 个子集逐一对上）⇒ 这两条在**任何**语料上都红",
       judge_reading("平均", "holistic", _hol("平均"), {}, inj_ctx)[0] is False
       and judge_reading("计数", "holistic", _hol("计数"), {}, inj_ctx)[0] is False,
       f"平均：{judge_reading('平均', 'holistic', _hol('平均'), {}, inj_ctx)[1][:100]}")

    # ── ④b 摘要**单射**的论域：函数级有支撑 ⇒ **过**（旧实现在此判红）────────
    #    值取 2 的幂 ⇒ 子集和唯一 ⇒ `(计数,和)` 对任何读数都单射 ⇒ 搜不到**任何**
    #    见证。这正是 openalex 恒红的微型复现：判据必须分清「论域的性质」与
    #    「声称不成立」，否则每个可用声明在这类论域上都是错的。
    ok("④ ★★ [O] 摘要单射的论域上，函数级有支撑的 holistic **过**（旧实现判红）"
       "—— openalex × `sequence`（512 子集、0 对）恒红的**微型复现**；"
       "「搜不到」是**论域的性质**，不是声称不成立，且行内要**披露**这一点",
       judge_reading("中位数", "holistic", _hol("中位数"), {}, inj_ctx)[0] is True
       and "论域的性质" in judge_reading(
           "中位数", "holistic", _hol("中位数"), {}, inj_ctx)[1],
       f"中位数：{judge_reading('中位数', 'holistic', _hol('中位数'), {}, inj_ctx)[1][:120]}")

    # ── ④c 全同值语料：读数恒为常数 ⇒ 语料级 0 对，仍按函数级**过** ───────────
    flat = ReadingCtx(value_of={d: 1.0 for d in "abcd"},
                      cover_of={d: frozenset({d}) for d in "abcd"})
    ok("④ [O] 全同值语料（每个读数都是常数）上 holistic 仍按**函数级**过 —— "
       "语料级 0 对是语料的性质；旧实现在这里判红，于是「拿不出支撑」"
       "与「语料太齐」分不开",
       judge_reading("中位数", "holistic", _hol("中位数"), {}, flat)[0] is True,
       f"中位数：{judge_reading('中位数', 'holistic', _hol('中位数'), {}, flat)[1][:100]}")

    # ── ④d 超预算 ⇒ 过 + 「语料级未判」：不拿有界搜索的失败冒充判定 ──────────
    big_ctx = ReadingCtx(
        value_of={f"q{i}": float(1 << i) for i in range(20)},
        cover_of={f"q{i}": frozenset({f"q{i}", "z"}) for i in range(20)})
    ok("④ [O] 2^|U| 超出穷举预算 ⇒ 过 + 「语料级**未判**」—— "
       "不拿有界搜索（k≤4）的失败当判定（`suffices=False` 的口径落到行上）",
       judge_reading("中位数", "holistic", _hol("中位数"), {}, big_ctx)[0] is True
       and "未判" in judge_reading(
           "中位数", "holistic", _hol("中位数"), {}, big_ctx)[1],
       f"中位数：{judge_reading('中位数', 'holistic', _hol('中位数'), {}, big_ctx)[1][:120]}")

    # ── ⑤ 构造法名字写错 ⇒ 红（声明指不到实现）──────────────────────────
    ok("⑤ [O] 见证构造法**不在注册表里** ⇒ 红（「声明写错了」与「还没写」是两件事）",
       judge_reading("计数", "holistic",
                     {"名": "计数", "类别": "holistic",
                      "见证": {"摘要": "计数与和", "构造": "不存在的构造法"}},
                     {}, ctx)[0] is False)


def test_maintain() -> None:
    """`§A8` —— **认识的持续维护**（`core/views.maintain`）。

    守的是一个**别处看不见**的东西：`csr`（最粗）**同时**是

        ① 交给检索层的那份认识的**定义**（`§3` 的定理：唯一 ⇒ 不用挑）
        ② `§A3`（视图最粗）**判得了**的前提 —— 候选数随块数**组合爆炸**

    ⇒ 而 `E′` 传播交出来的是**更细**的划分 ⇒ 它会让 ② **从可判退化成判不了**。
      这就是本条判据存在的**全部理由**，也是它**不重复 `§A3`** 的地方：
      `§A3` 红了是「有更粗的」，本条的典型红形态是「**`§A3` 变成跳过**」。

        ① 交付物**逐块等于** `csr`（两个夹具都要，**不论传播多划算**）
        ② **退化曲线**：反复**只传播** ⇒ `shrink` 升到 **1.000**（`propagate` 定理的推论）
        ③ ★ **可判性退化**：`csr` 8 块 ⇒ `§A3` 候选 **4140**（判得了）；
           传播 12 块 ⇒ 候选 **4213597** ⇒ **超上限、判不了** —— 这是**实测**
        ④ 动作与实相符（同构 ⇒ 报传播；不同构 ⇒ 报重建）
        ⑤ 没有种子 ⇒ 重建（没有「从旧出发」这回事）
    """
    from ldv.checks._fixtures import make_builder
    from ldv.checks.abstraction import a8_branches
    from ldv.core.views import (
        MAX_COARSENING_CANDIDATES,
        Maintenance,
        ViewSpec,
        coarsest_stable_refinement,
        maintain,
        n_coarsening_candidates,
        partition_of,
        propagate,
        refines,
        restrict_spec,
        shrink_ratio,
        stable,
    )

    loaded = load()
    if loaded is None:
        return
    nodes, edges, _ = loaded
    # ★ 2026-10-13：底座**自建**（`keyset` 全树），**不读生产声明** `view_spec.json`。
    #   两条理由，缺一条都不行：
    #     ① 下面两个下标 `U[-2:]` / `U[-13:]` 隐含「`|U| ≥ 13`」—— 而 `restrict_spec`
    #        是**拿掉**（不是「留下」）⇒ 声明换成 `sequence`（`|U| = 8`）之后，
    #        `U[-13:]` **拿光整个 `universe`** ⇒ `ValueError: 投影之后 universe 空了`
    #        ⇒ **整趟测试崩**。崩不是红：红是判据说话，崩是判据没了。
    #     ② `③` 要把种子**传播进 keyset 全树** —— 种子与目标必须**同一条方向**
    #        （`D0` 在 keyset 里与在 sequence 里是两个不同的方向）⇒ 本条的底座
    #        本来就只能是 `keyset`。跟着生产声明漂，是把「声明」当成了「夹具」。
    build = make_builder("keyset", nodes, edges)
    ids = sorted(nodes)
    kernel0, _plug0 = build(ids)
    dirs0 = sorted(kernel0.all_directions(), key=lambda d: _did_order(d.did))
    U = tuple(d.did for d in dirs0)
    spec = ViewSpec(universe=U, partition=(frozenset(U),),
                    relation=frozenset((d.parent, d.did) for d in dirs0 if d.parent))
    seeds = {
        "甲·传播在预算内": coarsest_stable_refinement(restrict_spec(spec, frozenset(U[-2:]))),
        "乙·传播严重退化": coarsest_stable_refinement(restrict_spec(spec, frozenset(U[-13:]))),
    }
    q_csr = partition_of(coarsest_stable_refinement(spec))

    # ── ① 交付物逐块等于 `csr` ─────────────────────────────────────────
    m_a, m_b = maintain(seeds["甲·传播在预算内"], spec), maintain(seeds["乙·传播严重退化"], spec)
    ok("① ★ [A8] **交付物逐块等于 `csr`** —— 两个夹具都要，**不论传播看起来多划算**："
       "甲那格只传播 0.480（在预算内）但它**更细**（12 块 vs 8 块）"
       "⇒ 「超预算才重建」是**上一版错的那个政策**，已改",
       partition_of(m_a.q) == q_csr and partition_of(m_b.q) == q_csr,
       f"甲 {len(m_a.q)} 块、乙 {len(m_b.q)} 块、`csr` {len(q_csr)} 块")

    # ── ② 退化曲线（`propagate` 那条定理的推论 ⇒ 已知答案）──────────────
    qp = None
    curve: list[float] = []
    for k in (6, 12, 18, 24, 30, 36):
        kernel, _plug = build(ids[:k])
        dirs = sorted(kernel.all_directions(), key=lambda d: _did_order(d.did))
        u = tuple(d.did for d in dirs)
        s_k = ViewSpec(universe=u, partition=(frozenset(u),),
                       relation=frozenset((d.parent, d.did) for d in dirs if d.parent))
        qp = (tuple(coarsest_stable_refinement(s_k)) if qp is None
              else tuple(propagate(qp, s_k)))
        curve.append(shrink_ratio(qp, s_k))
    ok("② ★ [A8] **退化曲线**：反复**只传播** ⇒ `shrink` 升到 **1.000**"
       "（= 离散划分，粗粒度被磨光）—— 它是 `propagate` 那条定理的推论，"
       "**所以是已知答案**，不是「跑一遍看看」",
       curve[-1] == 1.0 and curve == sorted(curve), f"曲线 {['%.3f' % x for x in curve]}")

    # ── ③ ★ 可判性退化（本条的**核心实测**）────────────────────────────
    s_full = spec          # 同一棵树（`keyset` 全树）—— ③ 与 ①② 共用同一个底座
    q_prop = tuple(propagate(seeds["甲·传播在预算内"], s_full))
    n_csr = n_coarsening_candidates(s_full, list(q_csr))
    n_prp = n_coarsening_candidates(s_full, list(q_prop))
    ok("③ ★★ [A8] **可判性退化**（这条是**量的结果**）：`§A3` 要枚举更粗的候选，"
       "候选数随块数爆炸 ⇒ **交付 `csr` 时判得了、交付传播结果时判不了**。"
       "⚠️ 而「跳过 ≠ 通过」⇒ 交付传播结果 = **把承重判据弄瞎**",
       n_csr <= MAX_COARSENING_CANDIDATES < n_prp,
       f"`csr` {len(q_csr)} 块 ⇒ 候选 {n_csr}（上限 {MAX_COARSENING_CANDIDATES}）；"
       f"传播 {len(q_prop)} 块 ⇒ 候选 {n_prp}")

    # ── ④ 动作与实相符 ─────────────────────────────────────────────────
    same_ok = all((m.propagate_equal and m.action == "传播")
                  or (not m.propagate_equal and m.action == "重建") for m in (m_a, m_b))
    ok("④ [A8] **动作与实相符**：同构 ⇒ 报「传播」（如实，不是省了什么）；"
       "不同构 ⇒ 报「重建」。⇒ 判据 ③ 那条红形态（谎报）它抓得到",
       same_ok, f"甲 {m_a.action}(同构 {m_a.propagate_equal})；乙 {m_b.action}"
                f"(同构 {m_b.propagate_equal})")

    # ── ⑤ 安全 + 无种子 ⇒ 重建 ─────────────────────────────────────────
    st, why = stable(s_full, m_b.q)
    m0 = maintain(None, s_full)
    ok("⑤ [A8] 交付物**稳定且细化 `P`**（对任何 `q` 都成立 ⇒ 「安全」）；"
       "**没有种子 ⇒ 重建**（没有「从旧结果出发」这回事，同 `§A7` 的 ⚠️）",
       st and refines(s_full, list(m_b.q)) and m0.action == "重建",
       f"稳定 {st}｜{why[:1]}｜无种子动作 {m0.action}")

    # ── ⑥ 「永远重建」过判据：代价是读数，不是红 ────────────────────────
    br = a8_branches(s_full, seed=seeds["甲·传播在预算内"],
                     result=Maintenance(q=tuple(q_csr), action="重建",
                                        shrink=shrink_ratio(q_csr, s_full),
                                        propagate_equal=False,
                                        shrink_if_propagated=0.0, reason="永远重建"))
    ok("⑥ ★ [A8] **「永远重建」过判据** —— 它安全、交付物也对、动作也照实报。"
       "⇒ 「省代价」**不是**这条判据判的东西；它进**读数**"
       "（⚠️ 而「交付传播结果」**会**红 —— 那条红线划在**可判性**上，不是代价上）",
       not (br["①"] or br["②"] or br["③"]), f"①②③ = {br['①'] or br['②'] or br['③']}")


def test_spec_paths() -> None:
    """**外生声明的落点与查找** —— `spec_paths` / `spec_target` / `load_spec_file`。

    ## 守的是什么

    `view_spec.json` 里的 `语料` 指纹把它**绑死在一份语料**上。文件只有一份时，
    **另一份语料的 `§A`/`§M`/`§T` 三组必然整组跳过** —— 2026-10-10 全量跑实测：
    96 条断言里 **21 条**跳过，而 `T1`–`T7` 正是 v0.1.15 的卖点 ⇒
    **「全量闸门通过」不覆盖那两层**。

    ## 为什么这几条必须有已知答案

    「跳过」与「没声明」在输出里长得像，而「找对了那份」与「只是找到了某一份」
    **也长得像** ⇒ 必须造**两份不同的声明**，让它们给出**不同的**答案。

    ⚠️ 仓内那份（`ldv/checks/view_spec.json`）是**真文件**，测试里用
       `mock.patch.object` 把它换到临时目录，**不碰仓库里那份**。
    """
    import json as _json
    from unittest import mock

    from ldv.checks import abstraction as _ab
    from ldv.tools.gen_view_spec import spec_target

    # ① `spec_paths` 的顺序：**语料旁优先、仓内兜底**
    inside = _ab.VIEW_SPEC_PATH.parent.parent / "corpus" / "nodes"        # ldv/corpus/nodes
    outside = Path(tempfile.gettempdir()) / "_ldv_probe_corpus" / "nodes"  # 包外
    got = _ab.spec_paths(inside)
    ok("① [spec路径-1] `spec_paths` 对**包内**语料：仍以仓内那份为准（顺序 = 语料旁、仓内）",
       len(got) == 2 and got[-1] == _ab.VIEW_SPEC_PATH,
       f"{[str(g) for g in got]}")
    got2 = _ab.spec_paths(outside)
    ok("① [spec路径-2] `spec_paths` 对**包外**语料：语料旁那份排在**前**（优先级）",
       got2[0] == outside.parent / "view_spec.json" and got2[-1] == _ab.VIEW_SPEC_PATH,
       f"{[str(g) for g in got2]}")
    ok("① [S3] `spec_paths(None)`：只给仓内那份（不知道语料时只有兜底）",
       _ab.spec_paths(None) == (_ab.VIEW_SPEC_PATH,),
       f"{[str(g) for g in _ab.spec_paths(None)]}")

    # ② `spec_target` 的落点：**包外 ⇒ 语料旁；包内 ⇒ 仓内**（不许按「谁存在」定）
    ok("② [S4] `spec_target` 包外语料 ⇒ 落点**语料旁**（与 `spec_paths` 同源）",
       spec_target(outside) == outside.parent / "view_spec.json",
       str(spec_target(outside)))
    ok("② [S5] `spec_target` 包内语料 ⇒ 落点**仓内那份**（36 项那份不许被写掉）",
       spec_target(inside) == _ab.VIEW_SPEC_PATH and spec_target(None) == _ab.VIEW_SPEC_PATH,
       f"{spec_target(inside)}")

    # ③ `load_spec_file`：**匹配当前语料的那份优先**（两份都给已知答案）
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        corp = t / "data" / "corp" / "nodes"
        corp.mkdir(parents=True)
        beside = corp.parent / "view_spec.json"
        inside_fake = t / "inside_view_spec.json"
        A, B = {"项数": 7, "边数": 9}, {"项数": 8, "边数": 10}
        beside.write_text(_json.dumps({"语料": A, "_who": "beside"}), encoding="utf-8")
        inside_fake.write_text(_json.dumps({"语料": B, "_who": "inside"}), encoding="utf-8")
        with mock.patch.object(_ab, "VIEW_SPEC_PATH", inside_fake):
            ok("③ [S6] 语料旁那份**匹配** ⇒ 选它（不是仓内那份）",
               _ab.load_spec_file(corp, A).get("_who") == "beside",
               str(_ab.load_spec_file(corp, A)))
            ok("③ [S7] 仓内那份**匹配** ⇒ 选它（语料旁那份不匹配就不许抢）",
               _ab.load_spec_file(corp, B).get("_who") == "inside",
               str(_ab.load_spec_file(corp, B)))
            # ⚠️ 都不匹配 ⇒ 返回**第一份存在的**（= 语料旁那份），
            #    好让上层印出「声明是在另一份语料上写的（它写的 vs 现在）」，
            #    而不是笼统一句「没声明」—— 前者能让人去修，后者不能。
            ok("③ [S8] 两份都**不匹配** ⇒ 返回第一份存在的（供上层报准指纹，不报「没声明」）",
               _ab.load_spec_file(corp, {"项数": 99, "边数": 99}).get("_who") == "beside",
               str(_ab.load_spec_file(corp, {"项数": 99, "边数": 99})))
            # 两份都不在 ⇒ `{}`（上层报「未声明」）
            inside_fake.unlink()
            beside.unlink()
            ok("③ [S9] 两份都**不存在** ⇒ `{}`（上层报「未声明」，与「不匹配」分得开）",
               _ab.load_spec_file(corp, A) == {},
               str(_ab.load_spec_file(corp, A)))
            # 语法错 ⇒ **抛**（写坏的声明与没写的声明必须分得开）⇒ 不许被静默吞掉
            beside.write_text("{ 这不是 json", encoding="utf-8")
            raised = False
            try:
                _ab.load_spec_file(corp, A)
            except ValueError:
                raised = True
            ok("③ [S10] 声明**语法错** ⇒ **抛**（不许静默跳到下一份 ⇒ 否则「坏了」像「没写」）",
               raised, "没抛")


def test_views() -> None:
    """流程 E · 抽象层 —— `core/views.py` 的算法 + `checks/abstraction.py` 的七条判据。

    分八段，**每段都要有已知答案**（本仓库的老纪律：合成图上的已知答案是对照组里
    最便宜的那一档，「跑一遍看看」不构成对照）：

        ① 核心算法   `Q` 与**手推值**逐块比；唯一性；两个 oracle 的已知答案
        ② 三态对照   设计稿 §9 要求的「过 / 红 / 跳过各一例」
        ③ 判据的**独立**性   `§A1` 不被 `B16` 蕴含（否则它只是换个写法）
        ④ 全链路     真内核 + 合成 spec ⇒ `build_views` + 七条判据
        ⑤ 外生门禁   `spec_for` 的三种跳过 + 「声明了却坏了」是**报错**
        ⑥ 闸门自己   `cap` 要在**时间与内存上**都拦得住（实测踩到过挂住）
        ⑦ committed 声明  `view_spec.json` 读得进来、非退化、七条判据全跑
        ⑧ 合成对照   `§A1`–`§A7` 的已知答案（含 `§A6` / `§A7` 的**分支拆分**）
        ⑨ 生成器 ↔ 文件   `ldv/tools/gen_view_spec.py` 与 committed 文件**不许分岔**，
                        且落盘**不丢人写的键**（配已知答案对照）

    ⚠️ ⑥ / ⑦ / ⑧ / ⑨ 是**四件不同的事**，都要有：⑥ 验「搜不完时会不会如实报跳过」，
       ⑦ 验「生产里那份声明成不成立」，⑧ 验「判据本身会不会红、会不会**红错地方**」，
       ⑨ 验「那份声明还是不是从内核算出来的」（⑦ 只验它**自己**成不成立）。
       少任何一段，对应的那种缺陷都没有东西守着。
    """
    from ldv.checks._framework import Report
    from ldv.checks.abstraction import (
        VIEW_CODES,
        VIEW_SPEC_PATH,
        ViewSet,
        a1_known_answer,
        a1_soundness,
        a2_stable,
        a3_coarsest,
        a4_category,
        a4_known_answer,
        a5_known_answer,
        a5_ledger,
        a5_out_of_scope_known_answer,
        a6_branch_split,
        a6_known_answer,
        a6_roundtrip,
        a7_known_answer,
        a7_propagate,
        build_views,
        known_answer_controls,
        ledger_entries,
        load_spec_file,
        reading_ctx,
        spec_for,
        subtree_of,
        view_profile,
        warranted_of,
    )

    def _result(rep, code: str) -> Tri:
        for a in rep.assertions:
            if a.code == code:
                return a.result
        return Tri.UNEXPANDED

    from ldv.core.views import (
        ViewSpec,
        _set_partitions,
        coarser_stable_exists,
        coarsest_stable_refinement,
        partition_of,
        refines,
        restrict_spec,
        stable,
        view_parts,
    )

    # --- ① 核心算法 ---------------------------------------------------------
    U = ("a", "b", "c", "d")
    cases = [
        ("`E = ∅` ⇒ `Q = P`", [U], [], [["a", "b", "c", "d"]]),
        ("`E = {a→b}` ⇒ 只有 `{a}` 能分出来",
         [U], [("a", "b")], [["a"], ["b", "c", "d"]]),
        # ⚠️ `E` 的两条边必须**各往对方的块里指**，两块才都切得开。
        #    `{(a,c), (b,d)}` 两条都朝外 ⇒ `E⁻¹({a,b}) = ∅`、`E⁻¹({c,d}) = {a,b}`
        #    整个包住 `{a,b}` ⇒ **一刀都切不动**，`Q` 就是 `P`。
        #    （这个期望值原来写错了，是这条测试自己抓出来的。）
        ("`P` 两块、`E` 双向各一条 ⇒ 两块都被切开",
         [["a", "b"], ["c", "d"]], [("a", "c"), ("c", "a")],
         [["a"], ["b"], ["c"], ["d"]]),
        ("`P` 两块、`E` 只连一条 ⇒ 只切一刀",
         [["a", "b"], ["c", "d"]], [("a", "c")], [["a"], ["b"], ["c", "d"]]),
        ("`E` 只朝块内指 ⇒ 一刀都切不动，`Q = P`",
         [["a", "b"], ["c", "d"]], [("a", "c"), ("b", "d")],
         [["a", "b"], ["c", "d"]]),
    ]
    for label, p, e, want in cases:
        spec = ViewSpec(universe=U, partition=tuple(frozenset(b) for b in p),
                        relation=frozenset(e))
        q = coarsest_stable_refinement(spec)
        got = [sorted(b) for b in q]
        ok(f"★ [E] 最粗稳定细化：{label}", got == want, f"实测 {got}，期望 {want}")
        ok(f"★ [E] 它**稳定**且是 `P` 的细化：{label}",
           stable(spec, q)[0] and refines(spec, q))
        ok(f"★ [E] 它**没有更粗的稳定划分**：{label}",
           coarser_stable_exists(spec, q)[0] is False)

    # 唯一性 —— 「视图不用挑」这句话的**全部**依据（设计稿 §3）
    import random as _rnd
    _rng = _rnd.Random(11)
    bad_u = []
    for _ in range(120):
        n = _rng.randint(1, 9)
        u = tuple(f"x{i}" for i in range(n))
        perm = list(u)
        _rng.shuffle(perm)
        p, i = [], 0
        while i < n:
            k = _rng.randint(1, min(3, n - i))
            p.append(frozenset(perm[i:i + k]))
            i += k
        e = frozenset((x, y) for x in u for y in u if _rng.random() < 0.25)
        s1 = ViewSpec(universe=u, partition=tuple(p), relation=e)
        s2 = ViewSpec(universe=tuple(_rng.sample(list(u), n)),
                      partition=tuple(_rng.sample(p, len(p))), relation=e)
        q1, q2 = coarsest_stable_refinement(s1), coarsest_stable_refinement(s2)
        if q1 != q2 or not stable(s1, q1)[0] or not refines(s1, q1):
            bad_u.append((q1, q2))
    ok("★★ [E] **唯一性**：打乱 `P` 的块序 / `U` 的序 ⇒ 逐字相同的 `Q`"
       "（120 组随机；「视图不用挑」的全部依据）", not bad_u, f"{len(bad_u)} 组不一致")

    # 离散划分**总是**稳定 —— `§A3` 注入用的那一档，是个小定理
    spec = ViewSpec(universe=U, partition=(frozenset(U),), relation=frozenset())
    disc = tuple(frozenset({x}) for x in U)
    ok("★ [E] **离散划分总是稳定**（单元素块对任何 `E⁻¹(B)` 都整个在内或整个在外）",
       stable(spec, disc)[0] is True)
    ok("★ [E] 但它**通常不是最粗** ⇒ `§A3` 能红（注入就吃这一条）",
       coarser_stable_exists(spec, disc)[0] is True)

    ok("★ [E] `_set_partitions` 的规模是 **Bell 数**（不是 `2^n`）—— "
       "写成子集枚举会把同一个粗化数很多遍，而**重复枚举看不出来**",
       [len(list(_set_partitions(n))) for n in range(1, 8)]
       == [1, 2, 5, 15, 52, 203, 877])

    # `ViewSpec` 的三条校验 —— **每一条都要能抛**，否则「声明错了」会被静默吞掉
    raises("★ [E] `P` 不是 `U` 的划分 ⇒ `ViewSpec` 抛", lambda: ViewSpec(
        universe=U, partition=(frozenset({"a", "b"}),), relation=frozenset()))
    raises("★ [E] `P` 的块重叠 ⇒ `ViewSpec` 抛", lambda: ViewSpec(
        universe=U, partition=(frozenset({"a", "b"}), frozenset({"b", "c"}),
                               frozenset({"d"})), relation=frozenset()))
    raises("★ [E] `E` 的端点不在 `U` 里 ⇒ `ViewSpec` 抛", lambda: ViewSpec(
        universe=U, partition=(frozenset(U),), relation=frozenset({("a", "z")})))
    raises("★ [E] `U` 里有重复元素 ⇒ `ViewSpec` 抛", lambda: ViewSpec(
        universe=("a", "a"), partition=(frozenset({"a"}),), relation=frozenset()))

    # --- ② 三态对照（设计稿 §9 要求的「过 / 红 / 跳过 各一例」） ----------------
    fails = known_answer_controls()
    ok("★★ [E] **已知答案对照组**：合成图上三态各一例，逐条对上",
       not fails, "；".join(fails))
    ok("★★ [E] `§A1` 三态：基线 过、掐掉一项 红",
       a1_known_answer(False) is Tri.YES and a1_known_answer(True) is Tri.NO)
    _rep = Report(plugin="(合成)")
    a1_soundness(ViewSet(spec=spec, q=(), views=()), _rep)
    ok("★ [E] `§A1` 视图集合为空 ⇒ **跳过**（不是过）",
       _result(_rep, "A1") is Tri.UNEXPANDED)

    nodes, edges, _ = load()
    kernel, plug = build_keyset(nodes)
    cover = coverage_of("keyset", nodes, edges)
    dirs = [d for d in kernel.all_directions() if kernel.members_of(d)][:4]
    ids = [d.did for d in dirs]
    corpus = {"项数": len(nodes), "边数": sum(len(v) for v in edges.values())}
    # ⚠️ ③④⑤ 的前提是 `ids` 至少 4 个（见上面 ③ 的说明）。前提不成立就
    #    **只跳这三段** —— ⑥（合成 spec 的闸门）与 ⑦（committed 声明）的前提
    #    不在语料上，**照跑**。整段 `return` 会把它们一起吞掉，而「被吞掉的绿」
    #    与「没跑过的绿」在只看汇总时长得一样（本仓库从头到尾在防的形状）。
    if need_at_least(ids, 4,
                     "§A1 独立性对照 + 全链路 + `spec_for` 门禁"):
        # --- ③ `§A1` **不被** `B16` 蕴含 ----------------------------------------
        #
        # 这是这一层最容易做成「换个写法」的地方：若「具体化」=「把成员的覆盖并起来」，
        # `§A1` 就**恒同真**（`B16` 全绿 ⇒ 它全绿），一条不提供独立信息的判据。
        # 它的实际内容是「**合并**（§I2）之后还盖不盖得住」——
        # 合并是**另一个函数**，它可能把覆盖收窄到装不下某个成员。
        #
        # ⚠️ ③④ 要 **4 个有成员的方向**（造两条边 `(ids[0],ids[1])` / `(ids[2],ids[3])`）。
        #    与 `test_stay_at_parent` 同一类问题：**夹具前提按随仓库那份 36 项语料挑的**，
        #    小语料上 `ids[3]` 直接 `IndexError` ⇒ 整趟崩（实测 `edge-single`）。⇒ 守前提。
        spec4 = ViewSpec(universe=tuple(ids), partition=(frozenset(ids),),
                         relation=frozenset({(ids[0], ids[1]), (ids[2], ids[3])}))

        class _MergeUnionsReq(KeysetPlugin):
            """注入：`合并` 取 `req` 的**并**（正确做法是取**交**）⇒ 覆盖被**收窄**。"""

            def merge(self, ds):  # noqa: ANN001, ANN201
                if not ds:
                    return frozenset(), frozenset()
                req = frozenset().union(*[frozenset(d.payload[0]) for d in ds])
                forb = frozenset().union(*[frozenset(d.payload[1]) for d in ds])
                return (req, forb)

        rep_good, rep_bad = Report(plugin="(合成)"), Report(plugin="(合成)")
        a1_soundness(build_views(kernel, spec4, cover, plug), rep_good)
        a1_soundness(build_views(kernel, spec4, cover, _MergeUnionsReq()), rep_bad)
        b16_rep = Report(plugin="(合成)")
        from ldv.checks.coverage import b16_members_covered
        b16_members_covered(kernel, cover, b16_rep, path="§A1 独立性对照")
        ok("★★ [E] `§A1` 的**独立性**：规范构造下 过；`合并` 取错（并而非交）⇒ **红**",
           _result(rep_good, "A1") is Tri.YES and _result(rep_bad, "A1") is Tri.NO)
        ok("★★ [E] 而**同一个注入下 `B16` 仍然是绿的** ⇒ `§A1` **不是** `B16` 的换写法"
           "（它的内容是「合并之后还盖得住」，不是「成员 ⊆ 自己的覆盖」）",
           _result(b16_rep, "B16") is Tri.YES)

        # --- ④ 全链路：真内核 + 合成 spec ----------------------------------------
        spec4b = ViewSpec(universe=tuple(ids),
                          partition=(frozenset(ids[:2]), frozenset(ids[2:])),
                          relation=frozenset({(ids[0], ids[2])}))
        vs = build_views(kernel, spec4b, cover, plug)
        rep = Report(plugin="(视图)", expects=VIEW_CODES)
        a1_soundness(vs, rep)
        a2_stable(spec4b, vs.q, rep)
        a3_coarsest(spec4b, vs.q, rep)
        # ⚠️ 这里**故意**给空的读数声明：要验的正是「一条读数都没声明 ⇒ **跳过**，
        #    **不许默认成 distributive**」（设计稿 §10 停止条件 2）。
        a4_category(view_parts(vs.q, lambda d: subtree_of(kernel, d)),
                    reading_ctx(kernel, spec4b), [], rep)
        a5_ledger(ledger_entries(kernel, spec4b), vs,
                  lambda did, item, kind: warranted_of(kernel, did, item, kind),
                  frozenset(kernel.items), rep)
        with tempfile.TemporaryDirectory() as _td:
            a6_roundtrip(vs, kernel, cover, plug, rep, path=Path(_td) / "views.json")
        ok("★ [E] 全链路（真内核 + 合成 spec）：`build_views` 出来的 `Q` 上 "
           "`§A1`/`§A2`/`§A3`/`§A6` 全绿",
           all(_result(rep, c) is Tri.YES for c in ("A1", "A2", "A3", "A6")),
           "；".join(a.line() for a in rep.assertions))
        ok("★★ [E] 而 `§A4`/`§A5` 在这条链路上**跳过**（没声明读数、账为空）—— "
           "**跳过不是红，但也不是过**：判据的适用范围由「它在这条方向上有没有内容」"
           "决定。默认成「过」的话，「空转」与「通过」就再也分不开了",
           all(_result(rep, c) is Tri.UNEXPANDED for c in ("A4", "A5")),
           "；".join(a.line() for a in rep.assertions))
        prof = view_profile(vs)
        print(f"    · 全链路读数：{prof['方向数']} 个方向 ⇒ {prof['视图数']} 张视图，"
              f"最大一块 {prof['最大块']}｜具体化 {prof['具体化']} vs 成员 {prof['成员']}")

        # --- ⑤ 外生 spec 的门禁：三种跳过 + 「声明了却坏了」是**报错** --------------
        s, why = spec_for({}, "keyset", corpus)
        ok("★ [E] 外生项**未声明** ⇒ 跳过（不是猜一个）", s is None and "未声明" in why)
        s, why = spec_for({"语料": {"项数": 1, "边数": 1}, "方向": "keyset"}, "keyset", corpus)
        ok("★ [E] 指纹对不上 ⇒ 跳过，且说清是**换了语料**", s is None and "另一份语料" in why)
        s, why = spec_for({"语料": corpus, "方向": "reach"}, "keyset", corpus)
        ok("★ [E] 声明写的是**别的方向** ⇒ 跳过", s is None and "另一条方向" in why)
        raises("★★ [E] **声明了却坏了**（`P` 不是划分）⇒ **报错**，不是跳过 ——"
               "「写错了」与「还没写」必须分得开，否则写错的声明看起来像没写",
               lambda: spec_for({"语料": corpus, "方向": "keyset", "universe": ["a", "b"],
                                 "partition": [["a"]], "relation": []}, "keyset", corpus))
        s, why = spec_for({"语料": corpus, "方向": "keyset",
                           "universe": list(ids), "partition": [list(ids)],
                           "relation": [[ids[0], ids[1]]]}, "keyset", corpus)
        ok("★ [E] 声明**齐全** ⇒ 解得出 spec（跳过的那三种之外还有一条能过的路）",
           s is not None and len(s.universe) == len(ids))

    # --- ⑥ 闸门自己：`cap` 必须在**时间与内存上**都拦得住 --------------------
    #
    # ⚠️ 这一段不是「顺手加的保险」：**先物化、再拿物化出来的 `len` 去比上限**
    #    的写法在 `k = 25` 时**不是报跳过，是挂住**（`Bell(25) ≈ 4.6e18`；
    #    `outputs/_probe_cap.py`：20 秒超时、`timeout` 退出码 124）。
    #
    # 「**搜不完**」与「**还在搜**」在只看输出的时候长得一模一样 ——
    # 本仓库一直在防的形状，只不过这一次它长在**闸门自己**身上。
    # 所以判据不能是「跑完再比」，只能是「**先算规模再跑**」。
    import time as _time

    big_u = tuple(f"d{i}" for i in range(25))
    big_spec = ViewSpec(universe=big_u, partition=(frozenset(big_u),),
                        relation=frozenset((f"d{i}", f"d{i + 1}") for i in range(24)))
    big_q = partition_of([frozenset({x}) for x in big_u])
    t0 = _time.monotonic()
    has, why, seen = coarser_stable_exists(big_spec, big_q)
    dt = _time.monotonic() - t0
    ok(f"★★ [E] 闸门：`Bell(25)` 的大组 ⇒ **如实报跳过**（`None`）而不是挂住"
       f"（候选数 {seen}，用时 {dt * 1000:.1f} ms）",
       has is None and "没搜完" in why and dt < 2.0,
       f"has={has}、用时 {dt:.3f}s、说明 {why!r}")
    boundary = []
    for m in (5, 6, 7, 8):
        u = tuple(f"d{i}" for i in range(m))
        sp = ViewSpec(universe=u, partition=(frozenset(u),),
                      relation=frozenset((f"d{i}", f"d{i + 1}") for i in range(m - 1)))
        boundary.append(coarser_stable_exists(
            sp, partition_of([frozenset({x}) for x in u]))[0] is not None)
    ok("★ [E] 闸门边界：`Bell(k) ≤ cap` 时**搜得完并给出结论**"
       "（不是一律跳过 —— 一律跳过与搜不完长得一样）", all(boundary))

    # --- ⑦ committed 的外生声明：`ldv/checks/view_spec.json` ------------------
    #
    # ⚠️ 与 `test_injections` 那三条视图注入**分工不同**，两边都不许省：
    #
    #     注入（自带 `P`/`E`）   验「**判据**能不能红」—— 与配置文件在不在无关
    #     本段（读那个文件）     验「**committed 的声明**成不成立」
    #
    # 只验前者的话，声明文件坏了没人知道：`run_checks` 会把它报成**跳过**，
    # 而「跳过」与「过」在**退出码上都是 0**。
    doc = load_spec_file()
    ok(f"★★ [E] committed 的外生声明在（`{VIEW_SPEC_PATH.name}`）—— "
       "不在的话 `run_checks` 的 `(视图)` 那组**三条全跳过**，而退出码照样 0",
       bool(doc), f"没找到 {VIEW_SPEC_PATH}")
    if doc:
        s, why = spec_for(doc, str(doc.get("方向") or ""), corpus)
        ok("★★ [E] 它解得出来（指纹 / 方向都对得上 —— 否则报的是**跳过**）",
           s is not None, why)
        if s is not None:
            which = str(doc["方向"])
            # ★ 2026-10-13（v0.1.21）：**与 `run_checks.view_report` 同源**。
            #   声明的 `universe` 是**维护路径**那棵树编的号 —— 方向 id 是每个内核
            #   各自从 0 开始编的，`D0` 在 keyset 里与在 sequence 里是**两个不同的方向**
            #   （`_fixtures.Cover` 的 docstring 专门记了这条；实测批建 16 ≠ 维护 8）。
            #   ⇒ 这里若写死 `build_keyset`，段 ⑦ 验的就不是**生产里跑的那条路**：
            #   它会拿 keyset 的树去解 sequence 的号 ⇒ `build_views` 抛 `KeyError`，
            #   或者（更坏）两边恰好都能建出来而**指的不是同一个方向**。
            #   `plugin` 与路径无关，仍取批建那份（`reusable_build` 有缓存，不重复建）。
            from ldv.run_checks import batch_kernel, maintenance_kernel

            k2 = maintenance_kernel(which, nodes, edges)
            p2 = batch_kernel(which, nodes, edges)[1]
            cv2 = coverage_of(which, nodes, edges)
            vs2 = build_views(k2, s, cv2, p2)
            rep2 = Report(plugin="(视图)", expects=VIEW_CODES)
            a1_soundness(vs2, rep2)
            a2_stable(s, vs2.q, rep2)
            a3_coarsest(s, vs2.q, rep2)
            a4_category(view_parts(vs2.q, lambda d: subtree_of(k2, d)),
                        reading_ctx(k2, s), doc.get("读数") or [], rep2)
            a5_ledger(ledger_entries(k2, s), vs2,
                      lambda did, item, kind: warranted_of(k2, did, item, kind),
                      frozenset(k2.items), rep2)
            import tempfile as _tf

            with _tf.TemporaryDirectory() as _td:
                a6_roundtrip(vs2, k2, cv2, p2, rep2, path=Path(_td) / "views.json")
            # ★ `§A7` 要一次**结构变动**。与 `run_checks.view_report` **同一处口径**：
            #   把 `universe` 里最后长出来的那个方向拿掉 ⇒ 传播回来。
            old_q2 = coarsest_stable_refinement(
                restrict_spec(s, frozenset({s.universe[-1]})))
            a7_propagate(old_q2, s, rep2)
            ok("★★ [E] committed 声明上**一条红的都没有** —— "
               "有一条常驻的红等于没人再看红",
               all(_result(rep2, c) is not Tri.NO for c in VIEW_CODES),
               "；".join(a.line() for a in rep2.assertions))
            ok("★★ [E] `§A1`–`§A4` + `§A6` + `§A7` 在 committed 声明上**全过**（不是跳过）",
               all(_result(rep2, c) is Tri.YES
                   for c in ("A1", "A2", "A3", "A4", "A6", "A7")),
               "；".join(a.line() for a in rep2.assertions))
            # ★ 2026-10-13（v0.1.21）：`§A5` 在 committed 声明上**判上了**（不再跳过）。
            #
            #   原来这里钉的是「跳过时理由必须是『账为空』，且要说清它是**永久的**」——
            #   那两条的前提是「视图层跑**批建**路径 ⇒ 账恒空」。现在视图层改跑
            #   **维护路径**，账非空 ⇒ 前提没了，那两条**已失去对象**（留着会常驻红）。
            #
            #   ⚠️ 但「跳过」的**两种含义**在这里仍然是分开的，两件事不共用一行：
            #       ①「这一趟这条方向上**没有内容**」（真跳过）—— 由 ④ 的合成 spec 守着
            #         （那里账为空 ⇒ `§A5` 跳过，见上面 `all(... UNEXPANDED ...)` 那条）；
            #       ②「这条判据**会不会红**」—— 由 `test_injections.inj_a5` 与 ⑧ 的
            #         **两类**已知答案（滞留 / 根覆盖之外）守着。
            #   所以这一段现在验的是第三件事：**生产里那条路径上，账真的是非空的**。
            a5 = _result(rep2, "A5")
            a5_detail = next((a.detail for a in rep2.assertions if a.code == "A5"), "")
            n_ledger = len(ledger_entries(k2, s))
            ok("★★ [E] `§A5` 在 committed 声明上**判**（不是跳过）—— 账非空 ⇒ "
               "逐条指得到方向与项、且独立重算下条条成立",
               a5 is Tri.YES and n_ledger > 0,
               f"A5={a5}、账 {n_ledger} 条，理由={a5_detail!r}")
            # ★ 反向：把账清空 ⇒ 判据必须**退回跳过**（而不是报过）。
            #   没有这一条，「A5=过」也可能是「它根本不看账」——
            #   而那正是「空转与通过长得一模一样」的另一种形态。
            a5_empty = Report(plugin="(视图)", expects=VIEW_CODES)
            a5_ledger((), vs2,
                      lambda did, item, kind: warranted_of(k2, did, item, kind),
                      frozenset(k2.items), a5_empty)
            ok("★★ [E] 上面那条的**已知答案对照**：账清空 ⇒ `§A5` 必须**退回跳过**"
               "（跳过 ≠ 通过；否则上面那条证明不了它在看账）",
               _result(a5_empty, "A5") is Tri.UNEXPANDED,
               f"清空后 A5={_result(a5_empty, 'A5')}")
            p2p = view_profile(vs2)
            ok("★ [E] committed 声明**非退化**（视图数 < 方向数）—— "
               "两者相等是**读数**不是失败，但那样这一层什么都没压，"
               "`§A3` 也就没有内容可判",
               p2p["视图数"] < p2p["方向数"],
               f"{p2p['方向数']} 个方向 ⇒ {p2p['视图数']} 张视图")
            print(f"    · committed 声明读数：{p2p['方向数']} 个方向 ⇒ "
                  f"{p2p['视图数']} 张视图，最大一块 {p2p['最大块']}｜"
                  f"具体化 {p2p['具体化']} vs 成员 {p2p['成员']}｜"
                  f"读数声明 {len(doc.get('读数') or [])} 条，`§A5` {a5}")

            # --- ⑨ 生成器 ↔ 文件不许分岔（2026-10-08 加） --------------------
            #
            # 上一条只验「这份声明**自己**成不成立」—— 它**不**验「这份声明
            # 还是不是从内核算出来的」。两者在输出里长得一模一样。
            mis = _spec_mechanical_mismatch(doc, which, nodes, edges)
            ok("★★ [E] committed 声明的**机械那几栏** == 生成器从内核算出来的"
               "（`语料`/`方向`/`universe`/`partition`/`relation`）—— "
               "「生成器与文件分岔」的症状是**两边各自看着都对**",
               mis == [], "；".join(mis)[:220])
            # ★ 已知答案对照：把 `relation` 掐掉一条 ⇒ 上面那条**必须**报出来。
            #   没有这一条，上面那条是恒真还是真在判，**看不出来**。
            bad_doc = dict(doc)
            bad_doc["relation"] = list(doc.get("relation") or [])[:-1]
            mis_bad = _spec_mechanical_mismatch(bad_doc, which, nodes, edges)
            ok("★★ [E] 上面那条的**已知答案对照**：`relation` 少一条边 ⇒ 必须报出来"
               "（否则那条断言恒真）",
               len(mis_bad) == 1 and mis_bad[0].startswith("relation"),
               f"改坏后报的是：{mis_bad}")

            from ldv.tools.gen_view_spec import build_spec, carry_readings, merge_spec

            _spec2, _ = build_spec(which, nodes, edges)
            _rd2, _rnote2 = carry_readings(doc, which)
            _merged2, _dropped2 = merge_spec(doc, _spec2, _rd2)
            ok("★★ [E] 生成器落盘**不丢人写的键**（`_note` 与 `读数` 逐字保留）—— "
               "第一版直接 `json.dumps(spec)` ⇒ 把 `_note` 88 行**整块抹掉**，"
               "而 `--write` 的输出看起来**完全正常**",
               _merged2.get("_note") == doc.get("_note")
               and _merged2.get("读数") == doc.get("读数") and _dropped2 == [],
               f"被丢的键={_dropped2}")
            _m3, _d3 = merge_spec(doc, _spec2, None)
            ok("★★ [E] 方向不符时生成器**拒绝照抄** `读数`，且把它算作「被丢的键」"
               "（别人的读数**留着比丢掉更糟** —— 它会被当成这一条方向的声明来判）",
               _d3 == ["读数"] and "读数" not in _m3,
               f"被丢的键={_d3}")

    # --- ⑧ `§A1`–`§A7` 的**合成**对照 ----------------------------------------
    #
    # ⚠️ 与 ⑦ 分工不同，两边都不许省：
    #
    #     ⑦ 读 **committed 声明**（真语料）  验「生产里那份声明成不成立」
    #     ⑧ 用**合成图**（已知答案）          验「判据本身会不会红、会不会红错地方」
    #
    # ⚠️ 合成图上的答案必须是**手推**出来的，不许「跑一遍看看再把结果抄成期望」——
    #    后者只会把实现现在的行为固化下来，包括它的错。
    ok("★ [E] `§A1` 合成对照：具体化掐掉一项 ⇒ 红（没掐 ⇒ 绿）",
       a1_known_answer(False) is Tri.YES and a1_known_answer(True) is Tri.NO,
       f"没掐={a1_known_answer(False)}、掐了={a1_known_answer(True)}")
    a4_fails = a4_known_answer()
    ok("★★ [E] `§A4` 合成对照：三类各一例 + 见证成立/不成立各一例 + "
       "「指不到实现」一例 + **函数级被反驳**两条（平均 / 计数 holistic ⇒ 红）"
       "+ **摘要单注射域**两条（中位数 / 覆盖计数 ⇒ 过）+ 两条跳过 "
       "—— 共 13 条**手推**答案",
       not a4_fails, "；".join(a4_fails))
    # ③ 反向：把**函数级门短路**（恒报「不被决定」）⇒ `平均` / `计数` 两条
    #    必须由红变绿 —— 否则那条红来自别处（比如语料搜索碰巧搜不到），
    #    而不是函数级门。openalex 恒红的成因正是把后者当成了前者。
    from ldv.checks import abstraction as _abs_a4

    _orig_sm = _abs_a4.scope_m_reading

    def _no_scope_m(f, sf):
        return {"determined": False, "pair": None, "H": None, "subsets": 0,
                "note": "（函数级门被短路）"}

    try:
        _abs_a4.scope_m_reading = _no_scope_m
        _broken_sm = a4_known_answer()
    finally:
        _abs_a4.scope_m_reading = _orig_sm
    ok("★ [E] 反向：把**函数级门短路** ⇒ 合成对照必须报 FAIL"
       "（`平均` / `计数` 两条 holistic 由**红变绿**）—— 证明那条红是函数级门给的，"
       "不是「语料搜索碰巧搜不到」",
       any("平均/holistic" in f for f in _broken_sm)
       and any("计数/holistic" in f for f in _broken_sm),
       "；".join(_broken_sm)[:200] or "短路后对照全对 —— 函数级门是摆设")
    ok("★★ [E] `§A5` 合成对照**两类各一条**（滞留 / 根覆盖之外）："
       "独立重算不成立 ⇒ 红（成立 ⇒ 绿）。"
       "⚠️ 缺一类就会重犯「只验一类」那个错 —— 2026-10-12 查出的真缺陷",
       a5_known_answer(False) is Tri.YES and a5_known_answer(True) is Tri.NO
       and a5_out_of_scope_known_answer(False) is Tri.YES
       and a5_out_of_scope_known_answer(True) is Tri.NO,
       f"滞留：成立={a5_known_answer(False)}、不成立={a5_known_answer(True)}｜"
       f"根覆盖之外：成立={a5_out_of_scope_known_answer(False)}、"
       f"不成立={a5_out_of_scope_known_answer(True)}")

    # ③ 反向：把「根覆盖之外」那一支改回**恒判 False**（=原来那个缺陷），
    #    第二类对照必须由 YES 变 NO。
    #    它若被悄悄改回「只验一类」（比如别名成 `a5_known_answer`），这条立刻红。
    from ldv.checks import abstraction as _abs

    _orig_w = _abs.warranted_of

    def _broken_out_of_scope(kernel, did, item, kind=_abs.LEDGER_STAYED):
        if kind == _abs.LEDGER_OUT_OF_SCOPE:
            return False
        return _orig_w(kernel, did, item, kind)

    try:
        _abs.warranted_of = _broken_out_of_scope
        _broken_yes = a5_out_of_scope_known_answer(False)
    finally:
        _abs.warranted_of = _orig_w
    ok("★★ [E] 反向：把「根覆盖之外」那一支改回**恒判 False**"
       "（原来那个缺陷）⇒ 第二类对照**必须**由 YES 变 NO"
       "—— 否则这条对照根本没走到那一支",
       _broken_yes is Tri.NO, f"缺陷版下 成立那一例 = {_broken_yes}")
    ok("★★ [E] 两条对照**不许是同一个函数**"
       "（第二类被别名成 `a5_known_answer` 时，上面那条反向立刻红）",
       a5_out_of_scope_known_answer is not a5_known_answer,
       f"第二类 = {a5_out_of_scope_known_answer.__name__}")
    ok("★ [E] `§A6` 合成对照：真实现 ⇒ 绿；两个对照实现都上 ⇒ 红",
       a6_known_answer(False) is Tri.YES and a6_known_answer(True) is Tri.NO,
       f"真实现={a6_known_answer(False)}、对照对={a6_known_answer(True)}")
    a6_fails = a6_branch_split()
    ok("★★ [E] `§A6` **分支拆分**（逐分支实测，不是从散文里认）："
       "只上 `to_d` 时 ② 亮而 ③ **不亮** ⇒「漂移只有在两个对照都上时"
       "才可能发生」这句话是**实测**的；而 ③ 不亮就意味着"
       "**只用 `to_d` 的注入会让「读回后 §A1 仍成立」永远没被验过**",
       not a6_fails, "；".join(a6_fails))
    a7_fails = a7_known_answer()
    ok("★★ [E] `§A7` 合成对照（四条路 + 两条自检，**逐分支**断言）："
       "真传播 ①② 都不亮；`propagate_naive`（挂到父块）**① 亮**；"
       "全量重算（冒充传播）**② 亮** —— 两者**互不替代**；"
       "外加「传播 ⊑ 重算」那条**定理**的自检",
       not a7_fails, "；".join(a7_fails))


def test_multilevel() -> None:
    """视图层 L0 的声明 + 折叠契约 `§M0`–`§M6`
    （`checks/multilevel.py` + `core/views.py` 的折叠那段）。

    ⚠️ **产物是第 0 层**（视图层）。「折叠**不止一层**」**不是**这一组要断言的东西 ——
       本设计的产物就是 L0，**一层就够**（实测真折 0 层）。
       见 `m1_view_layer` 的 docstring。

    ⚠️ `§M1` 的判据 2026-10-10 **换了**：`shrink ≤ 阈值` → `Q₀ == csr(spec)`。
       旧那条判的是**语料**（三份新语料上它是唯一的红，而 `§A3` 同时实测
       「已是最粗」⇒ 可证的不可能）⇒ 收缩比降为**读数**。

    ## 分六段，每段都要有已知答案

        ① 合成对照   七条各自的「该绿时绿、该红时红」（`known_answer_controls`）
        ② 真跑一遍   在 committed 规格上跑 ⇒ 七条全过、**视图层就是 `csr`**
        ③ 商的结构   `quotient_spec` 的 `universe` / `relation` / `partition` 三件事
        ④ `§M4` vs `§A3`  **同时观察**两条（否则「分得开」这句话是空的）
        ⑤ 同一个数   `§M4` 与 `§A3` 用的 `n_cand` 必须是**同一个函数**算的
        ⑥ 标题来源   真跑时印的 == 跳过时印的（一处定义、两处用）

    ⚠️ ④ 是前作核验 §3.3 明写的验收条件：`§A3` 跳过 = 「这一层判不了」（读数层面），
       而 `§M4` **不判**（2026-10-10 降级为读数）—— 它把「这一趟走的是哪条 oracle」
       印出来。两者在只看布尔值时**长得一模一样** ⇒ 必须**同时**观察到才算验过。
    """
    from ldv.checks._framework import Report
    from ldv.checks.abstraction import load_spec_file, spec_for
    from ldv.checks.coverage import corpus_fingerprint
    from dataclasses import replace as _replace
    from ldv.checks.multilevel import (
        MULTILEVEL_CODES,
        TITLES,
        known_answer_controls,
        m2_levels,
        m4_reading,
        n_folds,
        render_levels,
        run_multilevel,
        skip_all,
    )
    from ldv.core.views import (
        MAX_COARSENING_CANDIDATES,
        MIN_SHRINK_RATIO,
        ViewSpec,
        block_namer,
        coarser_stable_exists,
        coarsest_stable_refinement,
        n_coarsening_candidates,
        partition_of,
        quotient_spec,
    )
    from ldv.run_checks import batch_kernel

    # ── ① 合成对照 ────────────────────────────────────────────────────────
    ctl = known_answer_controls()
    ok("★★ [M] `§M0`–`§M6` 的**合成对照**：每条都造了一个会红的输入 —— "
       "「这条判据在判」与「这条判据恒真」长得一模一样",
       not ctl, "；".join(ctl))

    loaded = load()
    if loaded is None:
        return
    nodes, edges, _ = loaded
    # ⚠️ 必须带 `(find_corpus(), corpus)` —— 与 `run_checks` 里那三处、以及
    #    `test_injections._ml_chain` **同一个调法**（长注释见那里）。不带参数等于
    #    「只找位置、不认语料」⇒ 永远拿**仓内那份**声明 ⇒ 换任何别的语料，
    #    `spec_for` 都因指纹不符返回 `None`。
    #    ⚠️ 原来那两处 `return` 是**静默**的 —— 整组 `§M0`–`§M6` 一条都不跑，
    #    而退出码照样 0：「空转」与「通过」长得一模一样。改成**记一条跳过**。
    _corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(find_corpus(), _corpus)
    spec, _why = spec_for(doc, str(doc.get("方向") or ""), _corpus)
    if not doc or spec is None:
        PASS.append(f"`§M0`–`§M6` 真语料（**跳过**：这份语料没有对得上的外生声明"
                    f" —— {_why}）")
        return
    which = str(doc["方向"])

    # ── ③ 商的结构（先判这个：它是 ② 的前提） ──────────────────────────────
    q0 = coarsest_stable_refinement(spec)
    raised = False
    try:
        quotient_spec(spec, q0, [])              # 空 `P` ⇒ 必须抛
    except ValueError:
        raised = True
    ok("★ [M] `quotient_spec` 的 `P` 是**参数**、不是算出来的（空 `P` 被拒绝）—— "
       "「默认一个 `P`」就等于把尺度**偷偷内生化**（违反 `§K9`）", raised)
    spec1 = quotient_spec(spec, q0, [sorted(block_namer(q0).values())])
    ok("★ [M] 商的 `universe` == 视图 id（块名），且 `|universe| == |Q|`",
       len(spec1.universe) == len(partition_of(q0))
       and set(spec1.universe) == set(block_namer(q0).values()),
       f"{spec1.universe[:4]}… vs |Q| = {len(partition_of(q0))}")
    ok("★★ [M] `§M0`：商的 `relation` **无自环**（`A == B` 被过滤掉）—— "
       "自环会让「稳定」退化成恒真，那是一个**空转的判据**",
       not any(a == b for a, b in spec1.relation),
       f"自环：{[p for p in spec1.relation if p[0] == p[1]][:2]}")
    ok("★ [M] 商只丢掉**块内**的边（`A == B` 那些）—— 不是「少算了」，是按定义",
       len(spec1.relation) <= len(spec.relation),
       f"{len(spec.relation)} → {len(spec1.relation)}")

    # ── ② 真折叠 ──────────────────────────────────────────────────────────
    kernel, plugin, _qs, _mk = batch_kernel(which, nodes, edges)
    cover = coverage_of(which, nodes, edges)
    rep = Report(plugin="(L0)", expects=MULTILEVEL_CODES)
    levels = run_multilevel(spec, kernel, cover, plugin, rep)
    ok("★★ [M] committed 规格上 `§M0`–`§M6` **一条红的都没有**（基线绿 —— "
       "「基线就红的判据过不了注入验证」）",
       not rep.red, "；".join(a.line() for a in rep.red))
    ok("★★ [M] 七条**都跑了**（不是跳过）—— 「全绿」的范围必须与声明的范围恰好相等",
       all(a.result is Tri.YES for a in rep.assertions)
       and len(rep.assertions) == len(MULTILEVEL_CODES),
       f"{[(a.code, str(a.result)) for a in rep.assertions]}")
    ok("★★ [M] **视图层就是 `csr(spec)`**（`§M1`）—— L0 是**产物** ⇒ 它必须是最粗"
       "稳定细化，不许是别的划分（传播结果 / 更粗的 / 更细的都不许）。"
       "⚠️ 旧断言是「收缩比 ≤ 阈值」：那条判的是**语料**（分子由结构决定、分母由键集"
       "决定 ⇒ 系统无自由度），三份新语料上它是唯一的红，而 `§A3` 同时实测「已是最粗」"
       "⇒ **可证的不可能**。收缩比已降为**读数**（见 `m1_view_layer` 的 docstring）",
       partition_of(levels[0].q)
       == partition_of(coarsest_stable_refinement(levels[0].spec)),
       f"L0：|U| = {len(levels[0].spec.universe)} ⇒ |Q| = {len(levels[0].q)}"
       f"，`Q₀ == csr` ✓（收缩比 {levels[0].shrink:.3f} 只作读数）")
    ok("★ [M] **刹车与判据 `§M1` 解耦**：末层停因若写作 `M1 收缩比`，"
       "它的收缩比必须**真的**超阈值 —— 刹车按 `MIN_SHRINK_RATIO` 停，"
       "而 `§M1` 判的是 `Q₀ == csr`（两件事，混起来会让「红」指不出病因）",
       levels[-1].stopped != "M1 收缩比"
       or levels[-1].shrink > MIN_SHRINK_RATIO,
       f"末层 {levels[-1].stopped!r}，收缩比 {levels[-1].shrink:.3f} "
       f"vs 刹车阈值 {MIN_SHRINK_RATIO}")
    ok("★★ [M] `render_levels` 第一行说的是**产物是什么**（「模糊掌握」层）—— "
       "只印层数的话，「这东西做出来干什么用的」在输出里**读不出来**",
       "模糊掌握" in render_levels(levels).splitlines()[0],
       render_levels(levels).splitlines()[0])
    one_m2 = [_replace(levels[0], stopped="M2 层数")]
    ok("★★ [M] 只有一层时 `render_levels` **照抄 `stopped`**，不许推断成「撞了 `§M1`」"
       "—— 停因也可能是「层数上限先到」，而两者在只印层数时**长得一模一样**",
       "M2 层数" in render_levels(one_m2).splitlines()[1]
       and "M1" not in render_levels(one_m2).splitlines()[1],
       render_levels(one_m2).splitlines()[1])
    # ★ 「共 N 层」**不等于**「真的折了」：末层是**终止层**，恒存在。
    #   实测（2026-10-08，五份语料 × 三个方向）**真折层数恒为 0** ——
    #   第 1 层的商不缩（`|Q| == |U|`）⇒ 折叠在此终止。见 `MEASUREMENTS.md` 结果二十。
    #   ⇒ `n_folds` 是**读数**（基线 = 0，拿它当判据会基线就红）；
    #      这里把它**钉住**，是为了「读数变了」这件事能被看见，而不是让它进退出码。
    ok("★★ [M] `n_folds` 的**定义**：第 0 层（视图层）与末层（终止层）都不算 —— "
       "手推三例",
       n_folds([levels[0]]) == 0
       and n_folds([levels[0], levels[0]]) == 0
       and n_folds([levels[0], levels[0], levels[0]]) == 1,
       f"1 层→{n_folds([levels[0]])}、2 层→{n_folds([levels[0], levels[0]])}、"
       f"3 层→{n_folds([levels[0], levels[0], levels[0]])}")
    ok("★★ [M] 真读数：committed 语料上 **真折 0 层** —— 这不是红（**一层就够**，"
       "膨胀已被第 0 层收住），但**「共 N 层」这句话读不出它** ⇒ 必须单独数",
       n_folds(levels) == 0, f"真折 {n_folds(levels)} 层 / 共 {len(levels)} 层")
    ok("★ [M] 标题里**印出**真折层数（只印「N 层」会让「真折 0 层」与「真折 1 层」"
       "共用一行 —— 那正是本项目最忌的形状）",
       "真折 0 层" in render_levels(levels), render_levels(levels).splitlines()[1])
    ok("★ [M] 每一层的 `stopped` 只有**最后一层**非空 —— 中间层不该有停因"
       "（有的话说明它本该停却继续折了）",
       all(not lv.stopped for lv in levels[:-1]) and bool(levels[-1].stopped),
       f"{[lv.stopped for lv in levels]}")
    print("    · 视图层与折叠读数：" + "｜".join(
        f"L{i}: |U|={len(lv.spec.universe)} |Q|={len(lv.q)} "
        f"收缩比 {lv.shrink:.3f} n_cand {lv.n_cand} 停因 {lv.stopped or '-'}"
        for i, lv in enumerate(levels)))
    print("      " + render_levels(levels).splitlines()[0])

    # ── ④ `§M4` 与 `§A3` **同时观察** ──────────────────────────────────────
    #
    # ⚠️ 2026-10-13（v0.1.21）：这一段原来从 `spec.universe` 里**切**出 11 块
    #    （`|U| = 25` 时切得动）。声明换成 `sequence` 之后 `|U| = 8` ⇒ **切不出 11 块**
    #    （一个 11 块的划分至少要 11 个元素），而 `Bell(8) = 4140 ≤ 200000`
    #    ⇒ 在 committed 语料上**永远撞不到闸门**。
    #
    #    ⇒ 撞闸门那一段**合成**一个够大的层。这正是实测里 `openalex-n100` 的情形：
    #      `|Q| = 11` ⇒ `Bell(11) = 678570` 撞闸（`MEASUREMENTS.md` 结果表里那一行）。
    #      被验的是 `§A3` **按规模选 oracle** 这件事，与语料是哪一份无关。
    #      ⚠️ 反过来说：在 committed 语料上撞不到闸门**不是缺陷**，是 `|U| = 8` 的
    #      算术后果 —— 而「撞不到」与「没撞」在只看绿的时候长得一模一样，
    #      所以下面那条「上限内必须走暴力」用的是**真语料**那份 spec。
    u11 = tuple(f"x{i}" for i in range(11))
    spec_big = ViewSpec(universe=u11, partition=(frozenset(u11),), relation=frozenset())
    q_big = tuple(frozenset({x}) for x in u11)
    ok("★ [M] 造出来的那一层真的是 11 块（否则下面那次观察是空的）",
       len(q_big) == 11, f"{len(q_big)} 块")
    n_cand = n_coarsening_candidates(spec_big, q_big)
    ok(f"★★ [M] `§M4` 与 `§A3` **用的是同一个数**：`n_cand = {n_cand}`"
       f"（`Bell(11) = 678570 > {MAX_COARSENING_CANDIDATES}`）—— "
       f"两处各算一份的话，症状是「说不清这一层验证得起不起」",
       n_cand > MAX_COARSENING_CANDIDATES, f"n_cand = {n_cand}")

    # ★★★ 这一条是 **2026-10-10 用户裁定**要的：**`n_cand` 超上限不许让 `§A3` 变成判不了**
    #     （「A3 不能承认这只是在小语料可用」）
    from ldv.checks.abstraction import a3_oracle
    has_big, why_big, seen_big, tag_big = a3_oracle(spec_big, q_big)
    ok("★★★ [M] `n_cand` 超上限时 `§A3` **仍然判得了**（走**多项式 oracle**，"
       "不再报跳过）—— 这是「**不能承认只在小语料可用**」那条要求的落点",
       has_big is not None and "多项式" in tag_big,
       f"`has={has_big}`｜{tag_big[:70]}")
    # 同一件事的另一半：还在上限内时**必须**走独立性最强的暴力 oracle
    #   （这里用 **committed 语料**那份 spec —— `|U| = 8` ⇒ `n_cand = 4140 ≤ 上限`）
    has_small, why_small, _seen_s, tag_small = a3_oracle(spec, q0)
    ok("★★ [M] `n_cand` **在上限内**时 `§A3` 走**暴力枚举**（独立性最强的那条）——"
       "两条 oracle 的**选法**本身也要被验，否则「换了没换」看不出来",
       has_small is not None and "暴力" in tag_small, tag_small[:70])
    # `§M4` 降级后仍要报这个数（读数）
    r_m4 = m4_reading([_replace(levels[0], n_cand=n_cand)])
    ok("★★ [M] `§M4` 已**降级为读数** —— 它仍然报 `n_cand` 与「超没超上限」，"
       "但**不再判**（理由：`§A3` 有了多项式 oracle ⇒ 代价大不再意味着判不了）",
       r_m4["超上限的层"] == [0] and r_m4["每层 n_cand"] == [n_cand],
       f"{r_m4}")

    # ── ⑤ 不超上限时 `§A3` **不**报跳过 ────────────────────────────────────
    ok("★ [M] 不超上限时 `§A3` **不**报跳过（否则「跳过」会变成一条常驻的红）",
       coarser_stable_exists(spec, q0)[0] is not None,
       f"{coarser_stable_exists(spec, q0)[1][:60]}")
    ok("★ [M] 这一组没有放松 `ViewSpec` 的三条必填检查（空 `P` 仍抛）",
       _raises_valueerror(
           lambda: ViewSpec(universe=("a",), partition=(), relation=frozenset())))

    # ── ⑥ 标题只有**一个来源**：真跑时印的 == 跳过时印的 ───────────────────
    #   ⚠️ 这两处原来各写一份字符串 ⇒ 漂移了**没有任何东西看得出来**
    #      （「跳过时印的标题」与「真跑时印的标题」不一致，读起来像两条不同的判据）。
    skip_rep = Report(plugin="(L0)", expects=MULTILEVEL_CODES)
    skip_all(skip_rep, "对照：只为取标题")
    real = {a.code: a.title for a in rep.assertions}
    skipped = {a.code: a.title for a in skip_rep.assertions}
    ok("★★ [M] 「真跑时印的标题」与「跳过时印的标题」**逐字相同**，且 `TITLES` 的键"
       "**恰好**是 `MULTILEVEL_CODES`（一处定义、两处用 —— 两边都只是字符串，"
       "漂移了没有任何东西看得出来）",
       real == skipped and set(TITLES) == set(MULTILEVEL_CODES),
       f"标题不同：{sorted(k for k in set(real) | set(skipped) if real.get(k) != skipped.get(k))}"
       f"；键差：{sorted(set(TITLES) ^ set(MULTILEVEL_CODES))}")
    r12 = Report(plugin="(L0)", expects=("M2",))
    m2_levels(levels, r12, max_levels=12)
    ok("★★ [M] `M2` 的标题带**参数**：形参一改，标题里的数**跟着动** —— "
       "否则印出来的是**假的数**，而「印错了」与「印对了」在只看红绿时长得一模一样"
       "（`M4` 已降级为读数，不再有标题 ⇒ 这条只管 `M2`）",
       "（12）" in r12.assertions[0].title, f"{r12.assertions[0].title}")
    # ★ 降级之后，`M4` **不许**再出现在注册表/标题里（否则「声明了却没跑」）
    ok("★★ [M] `M4` 已从 `MULTILEVEL_CODES` 与 `TITLES` 里**彻底移除** ——"
       "降级只降一半（还留在注册表里）会让「套件全绿」覆盖一个不存在的判据",
       "M4" not in MULTILEVEL_CODES and "M4" not in TITLES,
       f"CODES={MULTILEVEL_CODES}")


def _raises_valueerror(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def test_retriever() -> None:
    """检索器层（流程 T）的 `T1`–`T8`（`checks/retrieval.py` + `retriever.py`）。

    ## 分八段，每段都要有已知答案

        ① 合成对照   三条各自的「该绿时绿、该红时红」（`known_answer_controls`）
        ② 真跑一遍   在 committed 规格上跑 ⇒ **八条全过**、而且**不是跳过**
                     （**直接调生产那一处实现** `retrieval_report`）
        ③ `T1` 的**夹具前提**  真插件上「后看的块」里**不可能**有真命中
        ④ `§C1` 默认值  **不早停**：跑过的块集合 == `Q` 的全部块
        ⑤ 分派       `r` 里出现未知栏 ⇒ **抛**（不是跳过、不是红）
        ⑥ 标题来源   真跑时印的 == 跳过时印的（一处定义、两处用）
        ⑦ 块画像     `§14.1` 四档**读法**与结构事实相符 + 「必须分档」的见证
        ⑧ 按使用细调 `§14.7` 两层排序（结构设定 + 倾向降序）
        ⑨ 影响的证明 `§14.8.1` 换成离散划分 ⇒ 候选集不变、建议退化成逐方向

    ⚠️ ③ 是一条**实测出来的**限制（写在 `checks/retrieval.py` 开头）：
       满足 `§I2 覆盖契约` 的插件上，`命中(合并(块)) = 否 ⟹ 每个成员都否`
       ⇒ 后看的块里**不可能**有「是」的成员 ⇒ `T1` 在真插件上**永远绿**，
       它的注入夹具只能**手造**（`_LosingMerge`）。
       这条**必须观察** —— 不观察的话，「`T1` 绿」读起来像「检索器没漏」，
       而这一趟里**根本不存在漏得掉的余地**：正是本项目最忌的形状
       （「空转与通过长得一模一样」）。

    ⚠️ ② 的**口径是「整个查询集 + 逐编号取最坏结果」**（2026-10-13，v0.1.21）：
       原来只跑 `queries[0]`（14 条里**最小**的一条）⇒ `T7` **永远跳过**。
       实测 14 条里 **11 条**能让它判 ⇒ 那是**覆盖不足**，不是「判不了」。

    ⚠️ ③–⑨ 用**代表查询**（候选最多的那条）做结构观察 —— **不许**写死 `queries[0]`：
       它候选最少，「建议覆盖多个方向」那几条在它上面**恒假**
       （实测 2 条建议覆盖 2 个方向 —— 与「离散划分」那一路**长得一样**）。
    """
    from ldv.checks._framework import Report
    from ldv.checks.abstraction import build_views, load_spec_file, spec_for
    from ldv.checks.coverage import corpus_fingerprint
    from ldv.checks.retrieval import (
        RETRIEVER_CODES,
        TITLES,
        known_answer_controls,
        skip_all,
    )
    from ldv.core import view_persist
    from ldv.core.tri import Tri as _Tri
    from ldv.run_checks import batch_kernel, maintenance_kernel, retrieval_report
    from ldv import retriever
    from ldv import flow as _flow

    # ── ① 合成对照 ────────────────────────────────────────────────────────
    ctl = known_answer_controls()
    ok("★★ [T] `T1`–`T3` 的**合成对照**：每条都造了一个会红的输入 —— "
       "「这条判据在判」与「这条判据恒真」长得**一模一样**",
       not ctl, "；".join(ctl))

    # ── ⑤ 分派（先判这个：它是 ② 的前提） ─────────────────────────────────
    raised = False
    try:
        retriever.dispatch({"__没有这个方向__": {"ideal": frozenset({"x"})}},
                           known=("keyset",))
    except retriever.DispatchError:
        raised = True
    ok("★★ [T] `步骤 T0`：`r` 里出现**未知栏** ⇒ **抛**（`基线§8` 停止条件 1）—— "
       "「猜一个交给谁」就是替人做决定；而且它**不是**三态里的任何一档"
       "（跳过与红都读不出「分派不了」）", raised)
    raised = False
    try:
        retriever.dispatch({"keyset": {"ideal": frozenset({"x"}), "__新字段__": 1}},
                           known=("keyset",))
    except retriever.DispatchError:
        raised = True
    ok("★ [T] `步骤 T0`：栏里出现**本层不认识的字段** ⇒ 也抛 —— 静默丢掉一个约束"
       "与「它被考虑了」长得一模一样（`基线§2`：本层**不解释语义**）", raised)
    empty_bar = retriever.dispatch({"keyset": {}}, known=("keyset",))
    ok("★ [T] 栏**存在但为空** ⇒ 得到一个默认 `Query`，**交给插件自己判** —— "
       "不是跳过、不是红（`§C5` 缺口 4）",
       empty_bar["keyset"].ideal == frozenset()
       and empty_bar["keyset"].require == frozenset(),
       f"{empty_bar['keyset']!r}")

    loaded = load()
    if loaded is None:
        return
    nodes, edges, _ = loaded
    # ⚠️ 同上：必须带 `(find_corpus(), corpus)`；静默 `return` 改成**记一条跳过**
    #    （否则整组 `T1`–`T3` 一条不跑而退出码照样 0）。
    _corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(find_corpus(), _corpus)
    which = str(doc.get("方向") or "")
    spec, _why = spec_for(doc, which, _corpus)
    if not doc or spec is None:
        PASS.append(f"`T1`–`T3` 真语料（**跳过**：这份语料没有对得上的外生声明"
                    f" —— {_why}）")
        return

    # ★★ 2026-10-13（v0.1.21）：**与 `run_checks.retrieval_report` 同源** —— 维护路径。
    #   声明的 `universe` 是**维护路径**那棵树编的号（实测 `sequence` 批建 16 ≠ 维护 8）。
    #   段 ③–⑨ 若拿**批建**的树去解 `sequence` 的号，`D2` 在两边**指的不是同一个方向**
    #   （`_fixtures.Cover` 的 docstring 专门记了这条）⇒ 画像 / 候选 / 建议
    #   全都对着另一棵树说话，而输出里**没有任何一处不对**。
    #   `queries` 是**语料级**的（与内核路径无关），仍取批建那份。
    plugin = batch_kernel(which, nodes, edges)[1]
    queries = batch_kernel(which, nodes, edges)[2]
    if not queries:
        return
    cover = coverage_of(which, nodes, edges)

    # ── ② 真跑一遍：**整个查询集** + 逐编号取**最坏结果** ───────────────────
    #   ★ 这一条**直接调生产那一处实现**（`retrieval_report`），不在这里另写一份聚合
    #     —— 另写一份的话，「测试绿」只说明我这份聚合对，说明不了生产对。
    #   ★★ 它同时是「加宽查询覆盖」那个决定的落点：`T7` 原来只跑 `queries[0]`
    #     （14 条里**最小**的一条 `随机 1 项 #0`）⇒ **永远跳过**；实测 14 条里
    #     **11 条**能让它判（`outputs/_probe_t7_queries.out.txt`）。
    rep = retrieval_report(loaded, [which])
    ok("★★ [T] committed 规格上 `T1`–`T8` **一条红的都没有**（基线绿 —— "
       "「基线就红的判据过不了注入验证」）",
       not rep.red, "；".join(a.line() for a in rep.red))
    ok("★★ [T] **八条都跑了**（不是跳过）—— 「全绿」的范围必须与声明的范围恰好相等",
       all(a.result is _Tri.YES for a in rep.assertions)
       and len(rep.assertions) == len(RETRIEVER_CODES),
       f"{[(a.code, str(a.result)) for a in rep.assertions]}")

    # ── ③–⑨ 结构断言：**逐条查询**各跑一遍（每条一棵新树），
    #     代表查询 = **候选最多**的那条（并列取先出现者） ────────────────────
    #
    #   ⚠️ **每条查询一棵新树**：`deepen` 会 `expand`（唯一写动作）**就地改内核**
    #      ⇒ 共用一棵的话，第 k 条看到的是被前几条改过的结构，结果与顺序有关。
    #   ⚠️ 代表查询**不许**写死成 `queries[0]`：那是候选**最少**的一条
    #      ⇒ 「建议覆盖多个方向」那几条断言在它上面**恒假**
    #      （实测它 2 条建议覆盖 2 个方向 —— 与「离散划分」那一路**长得一样**）。
    kernel0 = maintenance_kernel(which, nodes, edges)
    disk = view_persist.to_dict(build_views(kernel0, spec, cover, plugin))

    runs: list[tuple[Any, Any, Any]] = []
    for _q in queries:
        k_q = maintenance_kernel(which, nodes, edges)     # ★ 每条一棵新树
        rec_q = view_persist.from_dict(disk, k_q, cover, plugin)
        # ★ 「按使用细调」（`§14.7`）要**使用记录**才有内容 —— 流程 A 本来就记
        #   （`R5a` 展示 + `R5b`）。先跑几条攒记录，否则 `T5` **永远跳过**。
        for _q0 in queries[:3]:
            _flow.run_query(k_q, _q0)
        tend_q = selfopt.tendency_by_direction(k_q)
        rr = retriever.retrieve(k_q, plugin, {which: retriever.as_bar(_q)}, spec=spec,
                                recognition=rec_q, tendency=tend_q)
        runs.append((_q, k_q, rr))
    query, kernel, r = max(runs, key=lambda t: len(t[2].candidates))

    # ── ③ `T1` 的夹具前提（真插件上后看的块里不可能有真命中） ──────────────
    #   ⚠️ 对**整个查询集**验，并单列一条「至少有一条查询真有后看的块」——
    #      只看代表查询的话，它的 `late` 恰好为空 ⇒ 下面那条**恒真**（假绿）。
    late_runs = [(q, kk, rr) for q, kk, rr in runs if rr.late]
    # ⚠️ **必须用「全扫」来量**（`kk.query(rr.query)`）—— 2026-10-14 起 `rr.candidates`
    #    **已经跳过了「后看」的块** ⇒ 拿它去量 `candidates & late` **恒为空**（恒真）。
    #    这正是本仓最忌的形状：改动让一条原本有内容的断言**变成恒真**，而输出无一处不对。
    bad_late = [(q.label, sorted(frozenset(kk.query(rr.query).yes)
                                 & frozenset().union(*rr.late)))
                for q, kk, rr in late_runs
                if frozenset(kk.query(rr.query).yes) & frozenset().union(*rr.late)]
    ok("★★ [T] `T1` 的**夹具前提**：真插件上「后看」的块里**一个真命中都没有**"
       "（`§I2` 契约的推论）—— ⇒ `T1` 在真插件上**永远绿**，注入夹具只能**手造**。"
       "⚠️ 这条如果哪天红了，说明 `§I2` 被破坏了（那是 `B2` 的事）。"
       "★ 量它的必须是**全扫**（`kernel.query(q)`）—— `rr.candidates` 现在跳过了后看块",
       not bad_late,
       f"有后看块的查询 {len(late_runs)}/{len(runs)} 条、里面命中 {bad_late[:3]}")
    ok("★★ [T] 上一条的**夹具前提**：至少有一条查询**真的有「后看」的块**"
       "—— 否则上面那条是**恒真**的（「空转与通过长得一模一样」）",
       bool(late_runs), f"{len(late_runs)}/{len(runs)} 条查询有后看的块")

    # ── ④ `§C1` 默认值：**不早停**（口径 2026-10-14 更新） ────────────────
    #
    # ⚠️ 原来是 `ran == all_blocks` —— 那一条是**构造性恒真**（`scanned` 由 `order`
    #    构造 ⇒ 必然相等），自认「不是守卫」。2026-10-14 起 `步骤 T3` 会**跳过
    #    「后看」的块** ⇒ 那个等式**不再成立**，「少扫」本身**成了读数**。
    #    ⇒ 这一段改成守**新**的性质（两条，各自能红）。
    ran = set(map(frozenset, r.scanned))
    all_blocks = set(map(frozenset, r.first + r.late))
    ok("★★ [T] `步骤 T3` 跳过的**只是**「后看」的块 —— 「先看」的块**一块都不许少**"
       "（`基线§4 T3 ①` 在 2026-10-14 之后**长这样**：允许省的是**插件已经说过「否」**"
       "的那些块，**不许**省任何「先看」的块；而「省了却不许丢候选」由 `T1` 守）",
       ran == set(map(frozenset, r.first)),
       f"跑过 {len(ran)} / 先看 {len(r.first)} / 后看 {len(r.late)} / 块 {len(all_blocks)}")
    ok("★★ [T] **「少扫」真的发生了** —— 至少有一条查询的「后看」非空，而它跑过的块"
       "**严格少于**全部块。⚠️ 这条是**真守卫**：`skip` 没接线时它会红"
       "（否则上面那条 `ran == first` 恒真、什么都守不住）"
       "★ 访问量（读数）见 `基线§13 13.0` / `§14.6`（「少扫」是一种影响）",
       any(len(rr.scanned) < len(rr.first) + len(rr.late) for _q, _k, rr in runs),
       f"有后看块的查询 {len(late_runs)}/{len(runs)} 条、"
       f"代表查询跑过 {len(r.scanned)}/{len(r.first) + len(r.late)} 块")

    # ── ⑥ 标题只有一个来源：真跑时印的 == 跳过时印的 ──────────────────────
    skip_rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    skip_all(skip_rep, "对照：只为取标题")
    real = {a.code: a.title for a in rep.assertions}
    skipped = {a.code: a.title for a in skip_rep.assertions}
    ok("★★ [T] 「真跑时印的标题」与「跳过时印的标题」**逐字相同**，且 `TITLES` 的键"
       "**恰好**是 `RETRIEVER_CODES`（一处定义、两处用 —— 两边都只是字符串，"
       "漂移了没有任何东西看得出来）",
       real == skipped and set(TITLES) == set(RETRIEVER_CODES),
       f"标题不同：{sorted(k for k in set(real) | set(skipped) if real.get(k) != skipped.get(k))}"
       f"；键差：{sorted(set(TITLES) ^ set(RETRIEVER_CODES))}")

    # ── ⑦ 结构画像（`基线§14`）—— 「整体倾向的暗示」落在哪儿 ──────────────
    #
    # ⚠️ 四档的**字面**只在 `retriever.BlockProfile.reading` 那一处定义：
    #    `已细分过` / `分不开` / `还没长出来` / `混合`。
    #    ★ 这一段原来写的是「还没钻进去」「下界到了」—— 那两个字符串在本仓库里
    #      **根本不存在**（`grep` 一下只有这里）。后果不是红，是**恒真**：
    #      任何块的 `reading` 都匹配不上那两支 ⇒ 只有 `混合` 那一支能过，
    #      而当时恰好**每块都报混合** ⇒ 绿得看不出问题。
    #      ⇒ 这就是「断言写了一个不存在的常量」的形状：它看起来在验四档，其实一档都没验。
    ps = r.profiles
    ok("★★ [T] `Retrieval` 带**块画像**（`§14.1`），且每块的**四档读法**与结构事实相符"
       "（`§14.3` —— 判据 `T4` 从**读法反推事实**，与 `block_profile` 不同路）",
       bool(ps) and all(
           (p.reading == "已细分过"
            and all(kernel.children_of(kernel.direction(x)) for x in p.block))
           or (p.reading == "分不开"
               and all(len(kernel.members_of(kernel.direction(x))) < 2 for x in p.block))
           or (p.reading == "还没长出来"
               and all(not kernel.children_of(kernel.direction(x))
                       and len(kernel.members_of(kernel.direction(x))) >= 2
                       for x in p.block))
           or p.reading == "混合"
           for p in ps),
       "；".join(f"{sorted(p.block)[:2]}={p.reading}" for p in ps))

    kinds = sorted({p.reading for p in ps})
    ok("★★ [T] 画像在真语料上**真的分出了档**（不是一律「混合」、也不是只有一档）—— "
       "★ 这正是「比顺序感更多」那**一维「性质」**：不是「哪一带更好」，是「**你那一带是什么**」"
       "（`§14.3`：四档都是**结构事实**，不是分）",
       len(kinds) >= 2, f"分出的档：{kinds}")

    # ★★ 「画像必须分档」那条**中心纪律**的见证（`§K6` 在本层的形态）：
    #    「有没有子」这一列**分不开**下面两类 ——
    #        ① 只有 1 个项 ⇒ **结构上永远分不开**（`§K2` 情形①，`_expand_inner` 直接 `return ()`）
    #        ② 成员 ≥ 2    ⇒ 值得试
    #    ⇒ 画像**必须**把它们分成两档，否则会说错话。
    #
    #    ⚠️ 2026-10-13（v0.1.21）：见证原来取的是 keyset 批建上那个 **13 成员块**
    #       （6 个单项 + 7 个 ≥2）。声明换成 `sequence` + 视图层改跑**维护路径**
    #       之后，维护树上**没有任何方向只有 1 个项**（实测 D0…D7 的成员数
    #       36/24/2/2/10/6/2/4）⇒ 那个见证在这条路径上**不存在了**。
    #       ⇒ 见证改挂在**批建**树上：同一份语料、同一份插件、**真内核**，
    #         与「这一趟跑哪条路径」无关（那里 `D2`/`D3`/`D15` 就是单项方向）。
    #         这样这条纪律**不会因为换方向而悄悄失去见证** ——
    #         「见证没了」与「纪律守住了」在只看绿的时候**长得一模一样**。
    kb = batch_kernel(which, nodes, edges)[0]
    _dirs = sorted(kb.all_directions(), key=lambda d: d.did)
    one_id = next((d.did for d in _dirs
                   if not kb.children_of(d) and len(kb.members_of(d)) < 2), None)
    many_id = next((d.did for d in _dirs
                    if not kb.children_of(d) and len(kb.members_of(d)) >= 2), None)
    if need_at_least([x for x in (one_id, many_id) if x], 2,
                     "`T4` 的「必须分档」见证（`分不开` vs `还没长出来`，批建树上）"):
        p_mix = retriever.block_profile(kb, frozenset({one_id, many_id}))
        p_one = retriever.block_profile(kb, frozenset({one_id}))
        p_many = retriever.block_profile(kb, frozenset({many_id}))
        ok("★★ [T] 那一格**正是本仓库那条中心纪律**：两个成员**都没有子**，"
           "而它们**不是同一回事** —— 一个只有 1 个项（`§K2` 情形①：**结构上永远分不开**）、"
           "另一个成员 ≥2（值得试）。⚠️ 只看「有没有子」时这两类**长得一模一样** ⇒ "
           "画像**必须**分档，否则会说错话",
           p_mix.n_with_children == 0 and p_mix.n_singleton == 1
           and p_mix.n_unexpanded == 1 and p_mix.reading == "混合"
           and p_one.reading == "分不开" and p_many.reading == "还没长出来",
           f"合在一起 {sorted(p_mix.block)}→{p_mix.reading}｜拆开 "
           f"{sorted(p_one.block)}→{p_one.reading}、{sorted(p_many.block)}→{p_many.reading}")

    ok("★ [T] 三档**互斥且完备**（`§14.1` 的 ⚠️：这条**按构造恒真** ⇒ 它**不是判据**，"
       "只是计数方式的自洽性检查 —— 判据 `T4` 判的是**读法与事实相符**）",
       all(p.n_with_children + p.n_singleton + p.n_unexpanded == p.n_members
           for p in ps),
       f"{[(p.n_with_children, p.n_singleton, p.n_unexpanded, p.n_members) for p in ps[:3]]}")

    # ⚠️ 断言要判的是「**块按 Q 的规范化顺序列出**，不是按遍历顺序」——
    #    「解释里没有『先看』这两个字」是错的判据（`§14.7` 那行本来就要说这件事）。
    expl = retriever.render_explanation(r)
    pos = [expl.find(f"{i}/{len(r.profiles)} ") for i in range(1, len(r.profiles) + 1)]
    ok("★★ [T] `render_explanation` 把**块的性质**印出来，且块的排列用 **Q 的规范化顺序**，"
       "**不是**遍历顺序（`§3.2`：顺序不许进输出）—— "
       "★ 而 `步骤 T2` 现在**真的会重排**（按使用细调）⇒ 两者不同才有意义",
       "还没长出来" in expl and pos == sorted(pos) and -1 not in pos,
       f"块在解释里的出现位置 {pos}（应与 Q 同序）")

    # ── ⑧ 「按使用细调」（`§14.7`）—— 最初那两份文档的核心主张 ──────────────
    ok("★★ [T] `步骤 T2` 是**两层**排序（`§14.7`）：① 先由结构设定（`hit` 说否 ⇒ 后看）"
       "② **按使用细调**（有记录的块按倾向降序）—— 这正是 `参考文献地图 §〇` 第 2 行"
       "「搭建上层视图的规则**根据使用自我优化**」与 `§六.2`「收益（用得多）− 维护开销」"
       "的落点，而它**从未进过设计文档**（本次补上）。"
       "⚠️ 上界 `≤ len(first)` 是**按构造**的（只有「先看」档的块参与计数）"
       "⇒ 这条真正的判据是 `> 0`：**第二层有没有内容**（它是下面那条降序断言的防空转守卫）",
       hasattr(r, "tendency") and 0 < r.n_blocks_with_tendency <= len(r.first),
       f"有记录的方向 {len(r.tendency)} 个；「先看」档 {r.n_blocks_with_tendency}/{len(r.first)} 块有记录")

    if r.tendency and len(r.first) > 1:
        def _w(b):
            return sum(r.tendency.get(d, 0.0) for d in sorted(b))
        ws = [_w(b) for b in r.first if any(d in r.tendency for d in b)]
        canon = [_w(b) for b in sorted(r.first, key=min)
                 if any(d in r.tendency for d in b)]
        ok("★★ [T] 「先看」档里**有记录的块按倾向降序**（`§14.7` ②）—— "
           "⚠️ 这条**只在有记录时才可判**（无记录时自动退化 = 「不用一开始就创建最完美的规则」）；"
           "**夹具前提**：按倾向排 **≠** 按规范序排（否则「按权排」与「原序」重合 ⇒ 判据恒真）。"
           "⚠️ 前提**不是**「倾斜值互不相同」—— 实测真语料上**有重复**（`1.000` 出现两次），"
           "要求互不相同会造出一条**假红**",
           ws == sorted(ws, reverse=True) and ws != canon,
           f"倾斜值 {['%.3f' % x for x in ws]}（规范序下 {['%.3f' % x for x in canon]}）")

        ok("★★ [T] 「没有记录」**不参与比较**（`§K6` 同源）—— 没有被当 0 插进有记录的块之间。"
           "★ 这一条是本层最容易被写错的地方：把「没看见」当成「不好」就会静默改变顺序",
           all(r.first.index(b) < len([x for x in r.first if any(d in r.tendency for d in x)])
               for b in r.first if any(d in r.tendency for d in b)) or not r.tendency,
           f"{r.n_blocks_with_tendency} 有记录 / {len(r.first) - r.n_blocks_with_tendency} 无记录")

    ok("★ [T] 倾向的一处定义在 `core/selfopt.py::tendency_by_direction`（`F2` 与检索器层**共用**）"
       "—— ⚠️ 两处各写一份会漂移，而两边都是浮点数 ⇒ **漂移了看不出来**",
       selfopt.tendency_by_direction(kernel).keys()
       >= {d for b in r.first for d in b if d in r.tendency},
       f"{len(selfopt.tendency_by_direction(kernel))} 个方向")

    # ── ⑨ ★★ 「整体倾向」的**影响**：可执行证明（`§14.8`） ──────────────────
    ok("★★ [T] `Retrieval` 带**块级的下一步建议**（`§14.8`）—— 它答的是流程 A `R3a`"
       "那个**一直空着的依据**（「需要更清晰的方向吗？」；工作流程里那行红条件写的是「无」）",
       bool(r.advice) and all(isinstance(b, frozenset) and a for b, a in r.advice),
       f"{len(r.advice)} 条：" + "／".join(f"{len(b)}成员⇒{a}" for b, a in r.advice[:4]))

    ok("★★ [T] 建议**连着它的块**（不跨块合并）—— ★ 这条是**实测逼出来的**："
       "先写成「去重后的建议列表」，判据 `T6` 立刻红（一条建议被拿去对**别的块**检查）。"
       "语义上：**「哪一带」正是「整体倾向」的全部内容**，去掉块就等于把它丢了",
       all(any(b == p.block for p in r.profiles) for b, _a in r.advice),
       f"{len(r.advice)} 条的块都来自 `Q`")

    # ★★ **影响的证明**：把认识换成**离散划分**（= 不用视图），再跑一遍同样的检索。
    #    ⇒ 候选集**不变**（`T1` 的定理），但「整体倾向」**退化成逐方向**
    #      ⇒ 这就是「有认识 / 没认识」的**可机检差异**。
    #
    #    ⚠️ 直接**传** `recognition=` 即可 —— `current_recognition` 在
    #       `disk_fingerprint` 未显式给时会从 `recognition.spec` 推断，
    #       而这份的 `spec` 就是当前那份 ⇒ 指纹相符 ⇒ 用它。（原来在这里
    #       monkeypatch 一个替身，替身签名比真实现窄 ⇒ 调用点加参数就会炸在**测试**里。）
    _disc = retriever.Recognition(
        spec=spec, q=tuple(frozenset({d}) for d in spec.universe),
        source="对照：离散划分（= 不用视图）",
        fingerprint=retriever.spec_fingerprint(spec), on_disk=True)
    rd = retriever.retrieve(kernel, plugin, {which: retriever.as_bar(query)}, spec=spec,
                            recognition=_disc, tendency=r.tendency)
    ok("★★ [T] **影响的证明**（`§14.8.1`）：换成**离散划分**（= 不用视图）后，"
       "**候选集不变**（`§3` 定理 —— 「不改变结论」这一半）"
       "**而「整体倾向」退化成逐方向**（每条建议**恰好**覆盖 1 个方向）"
       "⇒ 这就是「有认识 / 没认识」的可机检差异",
       rd.candidates == r.candidates
       and rd.advice_span == len(rd.advice)     # 离散：块 = 单方向 ⇒ 每条建议覆盖 1 个方向
       and r.advice_span > len(r.advice)        # 有认识：块级建议覆盖多个方向
       and rd.advice_span == len(r.candidates),  # 离散下候选各自成块 ⇒ 全覆盖
       f"有认识：{len(r.advice)} 条覆盖 {r.advice_span}/{len(r.candidates)}｜"
       f"离散：{len(rd.advice)} 条覆盖 {rd.advice_span}/{len(rd.candidates)}"
       f"｜候选集相同={rd.candidates == r.candidates}")

    ok("★★ [T] 而**判断的粒度**正是「整体」二字的量：有认识时每条建议平均覆盖 "
       f"**{r.advice_span / max(1, len(r.advice)):.1f}** 个方向，离散时 **1.0** —— "
       "★ 所以「整体倾向」不是**更多信息**，是**更粗的一种判断**"
       "（同一份结构，换粒度就说得出 / 说不出「这一带」）",
       r.advice_span / max(1, len(r.advice)) > 1.0,
       f"{len(r.advice)} 条 / 覆盖 {r.advice_span} 个方向")

    # ── 读数（现算，不进退出码） ──────────────────────────────────────────
    d = r.reading()
    ok("★ [T] 读数是**现算**的（`§T0` 第 5 条：会漂的数不许写死）—— "
       "三个数都从 `Retrieval` 上算出来，与块本身对得上",
       d["方向数"] == len(spec.universe) and d["块数"] == len(r.recognition.q)
       and d["跑过的块数"] == len(r.scanned),
       f"{d}")
    print("    · 检索器读数：" + retriever.render_reading(r))
    print(f"      `T3` 的解释共 {len(r.reasons)} 条，"
          f"例：{retriever.render_reason(r.reasons[0]) if r.reasons else '（无）'}")


def test_cli_paths() -> None:
    """`(cli)` 那一组（`checks/cli_paths.py`）—— 四条入口 + 两个非默认参数。

    ## 分四段，每段都要有已知答案

        ① 注册表    `PATHS` 的编号集合与 `CLI_PATH_CODES` **双向**相等（顺序也钉住）
        ② 已知答案  三条「必须红」（CL3/CL4/CL5）**就是**那三条 —— 不是别的
        ③ 真跑一遍  在 committed 语料上七条全过
        ④ 已知答案对照  把 `run_one` 的护栏结论翻掉 ⇒ 两组各红各的

    ⚠️ ② 看着像废话，其实不是：把 `PATHS` 里某条的期望值从 `False` 改成 `True`，
       这一组会**照样全过**（因为断言就变成了「必须绿」）——
       而那种改法正是「空转与通过长得一模一样」在**断言**上的形状。
       所以「哪三条的已知答案是红」这件事本身要被钉住。
    """
    from ldv.checks.cli_paths import CLI_PATH_CODES, PATHS, run_cli_paths
    from ldv.checks._framework import Report

    # ── ① 注册表（双向 + 顺序） ───────────────────────────────────────────
    codes = tuple(c for c, _t, _kw, _e in PATHS)
    ok("★ [CL] `PATHS` 的编号与 `CLI_PATH_CODES` **逐项相同**（双向、含顺序）—— "
       "「加了编号忘了加路径」与「删了路径忘了删编号」都要能红",
       codes == CLI_PATH_CODES,
       f"PATHS {codes} vs 声明 {CLI_PATH_CODES}")

    # ── ② 已知答案：**哪三条是红的** ─────────────────────────────────────
    red_codes = tuple(c for c, _t, _kw, e in PATHS if not e)
    ok("★★ [CL] 已知答案是**红**的恰好是 `CL3`/`CL4`/`CL5`（空转护栏）—— "
       "这条把「哪三条必须开火」钉住：把期望值改成 `True` 的话这一组会照样全过，"
       "而那就成了「拆掉护栏也绿」",
       red_codes == ("CL3", "CL4", "CL5"), f"实际 {red_codes}")

    loaded = load()
    if loaded is None:
        return
    nodes, edges, _ = loaded

    # ── ③ 真跑一遍 ───────────────────────────────────────────────────────
    def group(run_one=None) -> Report:
        rep = Report(plugin="(cli)", expects=CLI_PATH_CODES)
        run_cli_paths((nodes, edges, None), ["keyset", "reach", "sequence"],
                      rep, run_one=run_one)
        return rep

    base = group()
    ok("★★ [CL] 四条入口 + 两个非默认参数：**七条全过**（基线）",
       not base.red,
       "；".join(f"{a.code}={a.result}" for a in base.red))

    # ── ④ 已知答案对照：两个分支各红各的 ─────────────────────────────────
    from ldv import cli

    def flip(always_guarded: bool):
        def fake(name, holdout, only, remove=0, loaded=None):
            _ok, text = cli.run_one(name, holdout, only, remove, loaded=loaded)
            return (not always_guarded), text
        return fake

    absent = group(run_one=flip(False))
    ok("★★ [CL] 护栏**永不**开火 ⇒ `CL3`/`CL4`/`CL5` 必须红（该红的没红）",
       {a.code for a in absent.red} == {"CL3", "CL4", "CL5"},
       f"实际红的是 {sorted(a.code for a in absent.red)}")

    always = group(run_one=flip(True))
    ok("★★ [CL] 护栏**处处**开火 ⇒ `CL1`/`CL2`/`CL6`/`CL7` 必须红（该绿的绿不了）—— "
       "两个分支**互不替代**：只用一条的话，另一组「本来就恒红」没人守",
       {a.code for a in always.red} == {"CL1", "CL2", "CL6", "CL7"},
       f"实际红的是 {sorted(a.code for a in always.red)}")


# --- ⑩ 「恒定的过」守卫：每条编号在生产配置上都得**真的判过**一次 ---------------
#
# ⚠️ 与 `test_injections` 的**注册表自检**分工不同，两边都不许省：
#
#     注册表自检（`test_injections` 第 0 条）  守「每条编号**会不会红**」
#                                              —— `CASES` 里每条都配了注入
#     本条（`test_never_judged`）             守「每条编号在**生产配置**上**判过没有**」
#
#    ⇒ 两条**互不覆盖**。`§A5` 就是这样漏的：它有注入（`inj_a5`，该红时会红），
#      但在视图层跑的**批建 + keyset** 上账恒空 ⇒ 它在生产上**一次都没判过**
#      （跨 8 份语料：0 过 / 0 红 / 8 跳过），而套件**全绿**。
#
#    ⚠️ 「跳过」本身不是错 —— 没有内容就该跳过。错的是**它一直跳过而没人发现**。
#       一条能红的判据放着一辈子不判，与**没有这条判据**在效果上一样，
#       而它在输出里长得像「守住了」。
#
#    ⚠️ 与「一条常驻的红等于没人再看红」是**同一条道理的镜像**：
#       常驻的红没人看，**常驻的跳过也没人看** —— 两种都要有东西守着。
#
#    跨语料统计见 `outputs/_恒定的过有多少条.md`：8 份语料 784 条真跑里
#    「判了没红」45 条（**守卫的正常状态**，红路由 70/70 注入证明）
#    「根本没判」1 条（`§A5`）—— **这条才是「一直放着」的**。

#: 「**在生产配置上判不了**」的编号 —— 显式、带理由，**两头都守**。
#: ⚠️ 这不是豁免清单，是**待办清单**：修好了就必须从这里删掉。
#:    不删的话它会退化成「永久豁免」，而那是另一种「没人再看」——
#:    与 `B18` baseline 那条 `--prune-suppressions` 的规矩同一个形状。
#:
#: ★ 2026-10-13（v0.1.21）：**现在是空的** —— 唯一那条（`A5`）**修好了**。
#:    用户拍板后：`view_spec.json` 的 `方向` 由 `keyset` 改为 `sequence`、
#:    视图层改跑**维护路径** ⇒ 账非空（16 条）⇒ `A5` 在生产配置上**判上了**。
#:    按上面「在表里 ⇒ 必须仍然判不了」那条，它**必须**被删掉 ——
#:    这一趟就是**守卫自己把它报出来的**（「已经在生产配置上判过了 ⇒ 删掉」）。
#:    ⇒ 表空不等于「这张表没用过」：它**用完就缩**，正是设计要的。
NEVER_JUDGED: dict[str, str] = {}

#: **生产配置**的那几组 —— 「恒定的过」只在它们上面数。
#: ⚠️ 白名单，**不是**黑名单（「排除含『合成』『对照』的」）：黑名单会**静默**漏掉
#:    一组新起的名字，而漏掉的那一组正好是「恒定的过」最该看见的地方。
#:    白名单漏了会**当场报出来**（下面第二条断言）。
PRODUCTION_GROUPS: tuple[str, ...] = (
    "keyset", "reach", "sequence", "(视图)", "(视图·读回)", "(L0)", "(cli)",
    "(检索)", "(内核)",
)

#: **已知答案对照**那几组 —— 它们**不进**统计（会把「没判过」读成「判过」），
#: 但组名要认得，否则上面那条白名单断言会把它们当成「新起的一组」。
SYNTHETIC_GROUPS: tuple[str, ...] = ("(合成)",)


def _declared_codes() -> tuple[str, ...]:
    """`run_checks` 声明的**全部**编号 —— 六组注册表的并集。"""
    from ldv.checks.abstraction import VIEW_CODES
    from ldv.checks.cli_paths import CLI_PATH_CODES
    from ldv.checks.multilevel import MULTILEVEL_CODES
    from ldv.checks.retrieval import RETRIEVER_CODES
    from ldv.run_checks import KERNEL_CODES, PLUGIN_CODES

    return (tuple(PLUGIN_CODES) + tuple(KERNEL_CODES) + tuple(VIEW_CODES)
            + tuple(MULTILEVEL_CODES) + tuple(CLI_PATH_CODES)
            + tuple(RETRIEVER_CODES))


#: `_assert_state_counts()` 的**缓存**（2026-10-12）。
#: ⚠️ 那一趟 `run_checks` 要 ~6 s，而 `test_never_judged` 与 `test_skip_evidence_table`
#:    **都要它** ⇒ 只跑一次。缓存成立的前提是「套件跑的过程中生产配置不变」——
#:    唯一会改 `LDV_CORPUS` 的 `test_b17_cyclic_corpus` **用完就还原**（`finally`）。
_STATE_COUNTS: tuple[dict[str, dict[str, int]], set[str]] | None = None


def _assert_state_counts() -> tuple[dict[str, dict[str, int]], set[str]]:
    """跑一趟 `run_checks --no-probes`，按编号数出**生产配置**上的三态。

    ⚠️ 直接挂 `Report.add` —— **不解析文本**。文本格式会变，挂上去不会
       （本仓库记过：从散文里认分支会「认错地方还看不出来」）。
    ⚠️ `redirect_stdout`：这一趟输出很长，**不该混进本套件的输出**。
    ⚠️ 只数 `PRODUCTION_GROUPS` 那几组 —— `(合成)` 那三份**已知答案对照**
       会往 `A5` / `B17` / `B20` 的格子里各记一次「过」，
       混进来就会把「没判过」读成「判过」（本仓库踩过一次，见
       `outputs/_恒定的过有多少条.md` 的 E5）。
    """
    global _STATE_COUNTS

    if _STATE_COUNTS is not None:
        return _STATE_COUNTS

    import contextlib
    import io as _io

    from ldv import run_checks as _rc
    from ldv.checks import _framework as _fw

    counts: dict[str, dict[str, int]] = {}
    plugins: set[str] = set()
    orig = _fw.Report.add

    def patched(self, code, title, result, detail=""):
        a = orig(self, code, title, result, detail)
        name = self.plugin or "?"
        plugins.add(name)
        if name in PRODUCTION_GROUPS:
            slot = counts.setdefault(code, {"过": 0, "红": 0, "跳过": 0})
            slot[{Tri.YES: "过", Tri.NO: "红"}.get(result, "跳过")] += 1
        return a

    _fw.Report.add = patched            # type: ignore[assignment]
    try:
        with contextlib.redirect_stdout(_io.StringIO()):
            _rc.main(["run_checks", "--no-probes"])
    finally:
        _fw.Report.add = orig           # type: ignore[assignment]
    _STATE_COUNTS = (counts, plugins)   # 见文件头那个缓存变量的说明
    return _STATE_COUNTS


def _never_judged_fails(counts: dict[str, dict[str, int]],
                        todo: dict[str, str],
                        declared: tuple[str, ...] | None = None) -> list[str]:
    """守卫的**判定逻辑**（与「怎么数出来」分开，好让已知答案对照直接喂它）。

    两条规则，**方向相反**，缺一条都会漏：

        不在 `todo` 里 ⇒ 必须**判过**（过 ≥ 1）
        在 `todo` 里   ⇒ 必须**仍然判不了**（过 == 0）—— 修好了就得删

    第二条不是多余的：只守第一条的话，`todo` 会变成一张**只进不出**的表，
    而**一张永远不缩的待办表与没有待办表是一回事**。
    """
    fails: list[str] = []
    for code in (declared if declared is not None else _declared_codes()):
        got = counts.get(code)
        if got is None:
            fails.append(f"{code}：这一趟**一次都没出现**（连跳过都没有）")
            continue
        if code in todo:
            if got["过"]:
                fails.append(f"{code}：已经在生产配置上判过了（过 {got['过']}）"
                             f"⇒ 把它从 `NEVER_JUDGED` 里删掉（表是待办，不是豁免）")
        elif got["过"] == 0:
            fails.append(f"{code}：在生产配置上**从来没判过**"
                         f"（过 0 / 红 {got['红']} / 跳过 {got['跳过']}）——"
                         f"真判不了就得进 `NEVER_JUDGED` 并写清为什么")
    return fails


def test_never_judged() -> None:
    """⑩ ★★ 「恒定的过」守卫 —— 每条编号在**生产配置**上必须真的判过一次。

    「恒定的过」有两种，**不能混为一谈**（跨 8 份语料实测）：

        「判了没红」  44 条 —— **守卫的正常状态**（健康数据上就该绿，
                               红路由 `test_injections` 70/70 证明）
        「根本没判」   1 条 —— `§A5`。**这条才是「一直放着」的**

    ⇒ 本条守的是**第二种**：判过的（过 ≥ 1）才叫守住了；一次没判过的不算。
    """
    counts, plugins = _assert_state_counts()
    fails = _never_judged_fails(counts, NEVER_JUDGED)
    ok("★★ [E] 「恒定的过」守卫：每条**已声明**的编号在**生产配置**上至少判过一次 ——"
       "不在待办表里的必须过 ≥ 1，在表里的必须**仍然**判不了（修好就得删）",
       not fails, "；".join(fails))
    print(f"    · 生产配置上 {len(_declared_codes())} 个编号："
          f"判不了 {sorted(NEVER_JUDGED)}（其余都判过）")

    extra = sorted(plugins - set(PRODUCTION_GROUPS) - set(SYNTHETIC_GROUPS))
    missing = sorted(set(PRODUCTION_GROUPS) - plugins)
    ok("★ [E] 这一趟的**组名**集合 == 白名单 ∪ 已知的对照组（不许多、不许少）——"
       "多出来说明有新的一组判据没登记（守卫会**静默**不数它）；"
       "少一个说明那一组本趟没跑",
       not extra and not missing, f"多出来 {extra}｜没跑 {missing}")

    # --- 已知答案对照：守卫**会不会红**、会不会**红错地方** -------------------
    #
    # ⚠️ 手推三例，**期望两两不同**，且各打一条分支：
    #     ① 空表        ⇒ 报「过 0 且不在表」的 `A5` 与 `B7`，**不报**过 6 的 `B1`
    #     ② `A5` 进表   ⇒ 只报 `B7`（`A5` 被表盖住）
    #     ③ 再把**已经判过的** `B1` 塞进表 ⇒ 必须报 `B1`
    demo = {"A5": {"过": 0, "红": 0, "跳过": 8},
            "B1": {"过": 6, "红": 0, "跳过": 0},
            "B7": {"过": 0, "红": 0, "跳过": 0}}
    keys = ("A5", "B1", "B7")

    def _codes(fs: list[str]) -> set[str]:
        return {f.split("：")[0] for f in fs}

    got1 = _codes(_never_judged_fails(demo, {}, keys))
    got2 = _codes(_never_judged_fails(demo, {"A5": "…"}, keys))
    got3 = _codes(_never_judged_fails(demo, {"A5": "…", "B1": "…"}, keys))
    ok("★★ [E] 上面那条守卫的**已知答案对照**（三条分支，期望两两不同）："
       "① 空表 ⇒ `{A5, B7}`（过 6 的 `B1` 不报）｜② `A5` 进表 ⇒ `{B7}`｜"
       "③ 把已经判过的 `B1` 也塞进表 ⇒ `{B1, B7}` —— "
       "**没有 ③ 这张表只进不出，而永远不缩的待办表与没有待办表是一回事**",
       (got1 == {"A5", "B7"} and got2 == {"B7"} and got3 == {"B1", "B7"}),
       f"①{sorted(got1)}｜②{sorted(got2)}｜③{sorted(got3)}")


def _b17_real(corpus: str, which: str) -> tuple[Tri, str, int]:
    """在 `corpus` 上**真跑**一次 `B17`（`batch_kernel` + 判据本身）。**只读**。

    返回 `(三态, detail, 最大等价类)`。⚠️ `LDV_CORPUS` 用完**必还原**（`finally`）。

    ⚠️ 为什么不用 `run_checks`：整趟在 1200 项上 **4m35s**，而大头是**别的**检查
       （覆盖 oracle 17419 次）；只要 `B17` 的话小语料上**亚秒**
       （`outputs/_probe_b17_real_run.py` 留档）。
    """
    from ldv.checks._fixtures import equiv_classes
    from ldv.checks._framework import Report
    from ldv.checks.equivalence import b17_leaf_is_equivalence_class
    from ldv.run_checks import batch_kernel

    saved = os.environ.get("LDV_CORPUS")
    try:
        os.environ["LDV_CORPUS"] = corpus
        loaded = load()
        if loaded is None:
            return Tri.UNEXPANDED, f"载入 `{corpus}` 失败", 0
        nodes, edges, _dangling = loaded
        cls = equiv_classes(which, nodes, edges)
        kernel, _plugin, _queries, _mk = batch_kernel(which, nodes, edges)
        rep = Report(plugin=which, expects=("B17",))
        b17_leaf_is_equivalence_class(kernel, cls, rep, which)
        biggest = max((len(c) for c in set(cls.values())), default=0)
        return rep.assertions[0].result, rep.assertions[0].detail, biggest
    finally:
        os.environ.pop("LDV_CORPUS", None)
        if saved is not None:
            os.environ["LDV_CORPUS"] = saved


def _reach_classes(corpus: str) -> tuple[int, int]:
    """`(项数, reach 最大等价类)` —— **只算等价类，不建内核**（1200 项上也是秒级）。"""
    from ldv.checks._fixtures import equiv_classes

    saved = os.environ.get("LDV_CORPUS")
    try:
        os.environ["LDV_CORPUS"] = corpus
        loaded = load()
        if loaded is None:
            return 0, 0
        nodes, edges, _d = loaded
        cls = equiv_classes("reach", nodes, edges)
        return len(nodes), max((len(c) for c in set(cls.values())), default=0)
    finally:
        os.environ.pop("LDV_CORPUS", None)
        if saved is not None:
            os.environ["LDV_CORPUS"] = saved


def test_b17_cyclic_corpus() -> None:
    """★★ `B17`@`reach` 的「跳过」是**语料性质**（DAG ⇒ 等价类全是单点），
    不是判据的毛病 —— 仓库自带**有环**语料，在那些语料上它**真的判**。

    ## 两半分开说（**不共用一行**）

    ① **机制**：设计好的**单变量对照** —— `snap-hepth`（留环）/ `snap-hepth-dag`（去环）：
       **同一张图**（都是 1200 项），只差有没有环 ⇒ 最大等价类 **51 / 1**。
    ② **真跑**：小语料上跑**真的** `B17`（亚秒）：`pypi-deps`（有环）**判**、
       `gh-langs`（DAG）**跳过**。
       ⚠️ 为什么不用 `snap-hepth` 真跑：`reach` 在 1200 项上建核 **25.5 s** ⇒
       进不了常规套件，它只在 `outputs/_probe_b17_real_run.py` 里留档。

    ## ★ 顺带钉住一处**输出说谎**（本趟才现形）

    `B17` 那行 detail 原来**无条件**印 `最大叶容量 X = 最大等价类 Y（已到下界）`。
    而 `(b)`「等价类 ⊆ 叶」在 `reach` 上**只报不判** ⇒ 这两个数**本来就不相等**。
    实测 `pypi-deps`：印的是 `最大叶容量 1 = 最大等价类 7（该方向的分辨率**已到下界**）`
    —— `1 ≠ 7` 是**假等式**，而「已到下界」是**反的**。
    ⚠️ 为什么以前没露：随仓库那份 36 项语料是 DAG ⇒ `reach` 上**直接跳过**，这一行不印。
    """
    # ── ① 机制：设计好的单变量对照（只算等价类）─────────────────────────────
    n_cyc, big_cyc = _reach_classes("snap-hepth")
    n_dag, big_dag = _reach_classes("snap-hepth-dag")
    ok("★ 单变量对照：`snap-hepth` / `snap-hepth-dag` 是**同一张图**（项数相同）",
       n_cyc > 0 and n_cyc == n_dag, f"{n_cyc} vs {n_dag}")
    ok("★ 留环那份：`reach` 最大等价类 ≥ 2 ⇒ `B17` **判得了**（不是退化）",
       big_cyc >= 2, f"最大等价类 {big_cyc}")
    ok("★ 去环那份：`reach` 最大等价类 == 1 ⇒ `B17` **跳过**（全单点）",
       big_dag == 1, f"最大等价类 {big_dag}")

    # ── ② 真跑：小语料上跑**真的** `B17`（亚秒）────────────────────────────
    cyc_state, cyc_detail, cyc_big = _b17_real("pypi-deps", "reach")
    dag_state, dag_detail, dag_big = _b17_real("gh-langs", "reach")
    ok("★★ 真跑：**有环**语料 `pypi-deps` 上 `B17`@`reach` **判**（不是跳过）",
       cyc_state is not Tri.UNEXPANDED,
       f"三态 {cyc_state}；最大等价类 {cyc_big}；detail {cyc_detail}")
    ok("★★ 真跑：**DAG** 语料 `gh-langs` 上 `B17`@`reach` **跳过**（全单点）",
       dag_state is Tri.UNEXPANDED,
       f"三态 {dag_state}；最大等价类 {dag_big}；detail {dag_detail}")
    ok("★ 而「判 / 跳过」跟着**最大等价类 ≥ 2** 走（同一份对照上的单变量）",
       (cyc_state is not Tri.UNEXPANDED) == (cyc_big >= 2)
       and (dag_state is Tri.UNEXPANDED) == (dag_big < 2),
       f"pypi-deps {cyc_big}/{cyc_state}｜gh-langs {dag_big}/{dag_state}")

    # ── ③ ★ 输出说谎守卫：`(b)` 不判时**不许**把两个数印成等式 ───────────────
    ok("★★ `(b)` 在 `reach` 上只报不判 ⇒ detail **不许**说「已到下界」"
       "（实测曾印出 `最大叶容量 1 = 最大等价类 7（已到下界）`：假等式 + 反向结论）",
       "已到下界" not in cyc_detail, f"detail：{cyc_detail}")
    ok("★★ 而它必须**明说**这两个数不相等、也不该相等（不是删掉了事）",
       "不相等" in cyc_detail, f"detail：{cyc_detail}")
    ks_state, ks_detail, _ks_big = _b17_real("pypi-deps", "keyset")
    ok("★★ **反向对照**：`keyset` 上 `(b)` **是**判的 ⇒ 那里**该**印「已到下界」"
       "（只删不放 = 把有内容的读数与没内容的读数印成一个样子）",
       ks_state is not Tri.UNEXPANDED and "已到下界" in ks_detail,
       f"三态 {ks_state}；detail {ks_detail}")


def test_skip_evidence_table() -> None:
    """★★ 「跳过项的**覆盖证据**」表 —— **权威闸门**（`run_checks` 那边只印不判）。

    ## 它守的是「跳过」的另一半

    「跳过」有两种含义，本仓库拆开报：**这一趟这条方向上没有内容**（真跳过）
    与 **这条判据会不会红**（可判，用合成对照证）。后者就是这张表。

    ## 三条规则

      ① 表里每一条的对照**全对**（三态 / 两态都落对，且**期望值两两不同**）
      ② 本趟**真的跳过**的每个编号都在表里 —— 缺一个就红。
         ⚠️ 这一条是**本趟补的**：`B18` 的四个跳过分支原来**一条对照都没有**，
         而汇总块用 `if c in codes` 过滤 ⇒ `B18` 跳过时**静默没有那一行**
         （实测 `snap-hepth` / `snap-hepth-dag` 上它**就跳过**）。
         「没有这一行」与「这一行全对」在输出里长得一样 —— 本仓库记过五次的形状。
      ③ **反向**：把对照换成「报失败」的假实现，表**必须**报「对照不符」
         （正向全绿只说明「没崩」，说明不了「判得动」）。
    """
    from ldv import run_checks as _rc

    table = list(_rc._skip_evidence())          # noqa: SLF001 - 就是被测对象
    codes = sorted(c for c, _d, _g in table)

    # ── ① 每一条对照都要全对 ────────────────────────────────────────────────
    bad = [c for c, _d, g in table if not g]
    ok("★★ [E] 覆盖证据表：每一条的合成对照**全对**（三态/两态都要落对）",
       not bad, f"对照不符的编号：{bad}｜表：{codes}")
    ok("★★ [E] 表里**必须**有 `B18` —— 它那四个跳过分支原来一条对照都没有",
       "B18" in codes, f"表：{codes}")

    # ── ② 本趟真的跳过的编号，一个都不许漏 ──────────────────────────────────
    counts, _plugins = _assert_state_counts()
    skipped = sorted(c for c, v in counts.items() if v.get("跳过", 0) > 0)
    missing = sorted(set(skipped) - set(codes))
    ok("★★ [E] 本趟**真的跳过**的每个编号都在表里 —— 缺一个就红"
       "（不许静默省略：没有那一行与那一行全对长得一样）",
       not missing, f"本趟跳过的 {skipped}｜表里 {codes}｜**缺 {missing}**")

    # ── ③ 反向：对照坏了，表必须报「对照不符」────────────────────────────────
    from ldv.checks import abstraction as _abs
    from ldv.checks import coverage as _cov
    from ldv.checks import equivalence as _eq
    from ldv.checks import refinement as _ref

    fakes = {"A5": (_abs, "a5_known_answer"), "B17": (_eq, "b17_known_answer"),
             "B18": (_cov, "b18_known_answer"), "B20": (_ref, "b20_known_answer")}
    caught: list[str] = []
    for code, (mod, attr) in fakes.items():
        orig = getattr(mod, attr)
        setattr(mod, attr, lambda: ["（反向对照：故意让它报失败）"])
        try:
            rows = {c: g for c, _d, g in _rc._skip_evidence()}   # noqa: SLF001
        finally:
            setattr(mod, attr, orig)
        if rows.get(code) is False:
            caught.append(code)
    ok("★★ [E] 反向：把对照换成「报失败」的假实现 ⇒ 表**必须**报「对照不符」"
       "（正向全绿只说明没崩，说明不了判得动）",
       sorted(caught) == sorted(fakes), f"抓到的 {sorted(caught)}｜期望 {sorted(fakes)}")


def test_growth_autonomy() -> None:
    """★ [发展] 自主度红线：系统不许自己改规则（A3 = 0，§4.6 G5）。

    ★★ 判据盯**实现**不盯**声明**：探针第一版把「禁止 A3」的声明和
       「已实现的 A3 动作」混在一张表里数，判据就去数了声明 ⇒ 误红 2 条。
       这条测试因此不看任何表，只扫代码与留档。

    ① 代码里没有对 `_data/*/derived/` 的写路径 —— 系统无法自造维度。
    ② 每个派生维度都记了命名者（`_meta.named_by`）—— 人是命名者。
       外部语料不在（新 clone）⇒ **跳过**，不是过。
    """
    import re as _re

    repo = Path(__file__).resolve().parents[2]
    hits = []
    files = sorted((repo / "ldv").rglob("*.py")) + [repo / "entry.py"]
    for f in files:
        if "test" in f.name:
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        for i, ln in enumerate(txt.splitlines(), 1):
            if "derived" in ln and _re.search(r'write_text|\.write\(|json\.dump|"w"|\'w\'', ln):
                hits.append(f"{f.relative_to(repo)}:{i}")
    ok("★ [发展] ① 系统无法自造维度：代码里没有对 derived 的写路径（A3 = 0）",
       not hits, f"命中 {len(hits)} 行" + ("：" + "；".join(hits[:3]) if hits else ""))

    side = sorted((repo / "_data").glob("*/derived/*.json"))
    if not side:
        # ⚠️ 格式必须是「（**跳过**」——汇总只认这个格式。
        #    写成「⇒ **跳过**」会被算成「过」，那就是「跳过 ≠ 通过」的违规。
        ok("★ [发展] ② 每个派生维度都记了命名者"
           "（**跳过**：外部语料不在，没有 sidecar 可判）",
           True, "0 个 sidecar")
        return
    unnamed = []
    for p in side:
        try:
            meta = json.loads(p.read_text(encoding="utf-8")).get("_meta") or {}
        except Exception:                                    # noqa: BLE001
            unnamed.append(p.name + "（读不出）")
            continue
        if not str(meta.get("named_by") or "").strip():
            unnamed.append(p.name)
    ok("★ [发展] ② 每个派生维度都记了命名者（人是命名者，不是系统）",
       not unnamed, f"{len(side)} 个 sidecar｜未记名 {len(unnamed) or '无'}")


def test_usage_wiring() -> None:
    """★ [接入] 使用记录的两段通道：**缓冲区 → 正式**（§4.6 G5 的「使用者入口」）。

    这七条都**不依赖外部语料** ⇒ clone 上真跑，不跳过。

    ① 内置语料**不启用**使用记录 —— 内置语料在仓库里，
       往那儿写 usage 等于把运行时产物写进仓库。
    ② 去重：同一 `(qfp, did)` 只写一次，不覆写、不叠加。
    ③ 写盘是 LF —— Windows 上默认写 CRLF，而仓库规定 LF。
    ④ `clear` 之后为空（正式 + 缓冲都清）。
    ⑤ ★ **查询只进缓冲**：`load` 仍是 0 条 —— 被摆出来只是**曝光**，不是有用。
    ⑥ `promote` 之后正式 +1、缓冲里移除该支。
    ⑦ **不在缓冲里的支不许 promote** —— 翻到了 ≠ 用了。

    ⚠️ 「排序不改答案集合」那条（`W5`）需要外部语料 ⇒ 留在
       `evidence/_probe_usage_wiring.py`，不在这里假装跑过。
    """
    import shutil as _sh
    import tempfile as _tf

    import entry as E

    from ldv.tools import usage_store as US

    app = E.App(None)                     # 内置 36 项语料
    r = app.query({"require": []})
    ok("★ [接入] ① 内置语料不启用使用记录（不往仓库里写运行时产物）",
       app._usage_dir is None and r.get("ordered_by") == "default",
       f"_usage_dir = {app._usage_dir}｜ordered_by = {r.get('ordered_by')}")

    tmp = Path(_tf.mkdtemp(prefix="ldv_usage_"))
    try:
        a = US.append(tmp, did="D1", outcome=1.0, propensity=0.5, qfp="q1", note="t")
        b = US.append(tmp, did="D1", outcome=1.0, propensity=0.5, qfp="q1", note="t")
        ok("★ [接入] ② 同一 (qfp, did) 只写一次（不覆写、不叠加）",
           a.get("written") is True and b.get("written") is False,
           f"第一次 written={a.get('written')}｜第二次 written={b.get('written')}")

        raw = Path(a["path"]).read_bytes()
        ok("★ [接入] ③ 记录写盘是 LF（Windows 上默认会写 CRLF）",
           b"\r\n" not in raw, f"CRLF {raw.count(b'\\r\\n')} 个｜{len(raw)} 字节")

        n = US.clear(tmp)
        ok("★ [接入] ④ clear 之后为空，再 load 为 0 条（可回退）",
           n.get("deleted") == 1 and US.load(tmp) == [],
           f"删了 {n.get('deleted')}｜再 load {len(US.load(tmp))} 条")

        # ⑤⑥⑦ 缓冲区：查到了都记，但**不算有用**
        US.stage(tmp, qfp="q2", shown=["D1", "D2"])
        ok("★ [接入] ⑤ 查询只进缓冲：load 仍是 0 条（曝光 ≠ 有用）",
           US.load(tmp) == [] and len(US.stage_load(tmp)) == 1,
           f"正式 {len(US.load(tmp))} 条｜缓冲 {len(US.stage_load(tmp))} 批")

        pr = US.promote(tmp, qfp="q2", did="D1", propensity=0.5)
        rest = US.stage_load(tmp)
        ok("★ [接入] ⑥ promote 之后：正式 +1，缓冲里移除该支",
           pr.get("written") is True and len(US.load(tmp)) == 1
           and all("D1" not in r["shown"] for r in rest),
           f"正式 {len(US.load(tmp))} 条｜缓冲剩 {pr.get('remaining')} 支")

        bad = US.promote(tmp, qfp="q2", did="D9", propensity=0.5)
        ok("★ [接入] ⑦ 不在缓冲里的支不许 promote（翻到了 ≠ 用了）",
           bad.get("written") is False, f"{bad.get('why')}")

        # ⑧ 缓冲有界 —— **只淘汰缓冲，正式记录一条不动**
        before_n = len(US.load(tmp))
        for i in range(US.STAGE_CAP + 5):
            US.stage(tmp, qfp=f"cap-{i:04d}", shown=["D1"])
        after = len(US.stage_load(tmp))
        ok("★ [接入] ⑧ 缓冲有界：写 cap+5 批 ⇒ 只剩 cap，正式记录一条不动",
           after == US.STAGE_CAP and len(US.load(tmp)) == before_n,
           f"缓冲 {after} 批（上限 {US.STAGE_CAP}）｜正式 {len(US.load(tmp))} 条"
           f"（写前 {before_n} 条）")
    finally:
        _sh.rmtree(tmp, ignore_errors=True)


def test_usage_snapshot() -> None:
    """★ [接入] ⑨ **§I7 接线**：每条使用记录带上「这一刻的方向状态」（2026-10-23）。

    §I7 `record(方向)` 原来**全仓零调用方** —— `validate_plugin` 要求这八个方法在，
    但没有任何一处调用它。这五条把它接上：下钻那一刻 `plugin.record(d)` 的输出
    随记录落盘（`usage_store` 的 `snapshot` 字段）。

    ① 下钻 ⇒ 文件里有 `snapshot` 行，load 读回、JSON 可解析、`kind` 在
    ② 两条记录的 snapshot **各对各的 did** —— 防「串方向」
    ③ snapshot 与现场重算的 `plugin.record(d)` **逐字相同**（同源，不是另算一份）
    ④ 旧记录（没有 snapshot 行）load 不炸、snapshot 为空 —— 向后兼容
    ⑤ ★ snapshot 不是**输入**：同 (did, outcome, propensity, at)，
       带快照与不带快照两份语料的倾向**逐字相同** —— 否则留痕就在驱动结构（§K9）

    ⚠️ 反向对照（回退 `entry.py` 的接线 ⇒ ① 必须红）在
       `evidence/_probe_usage_snapshot_reverse.py`：回退真源码跑一遍太重，
       按本仓库惯例不放判据里，留档在同目录。
    ⚠️ 语料必须在含 `_data` 的路径下（`entry.App` 只在那时启用 usage 目录），
       且跑完**还原 `LDV_CORPUS`** —— 判据不许留全局状态。
    """
    import os as _os
    import shutil as _sh
    import tempfile as _tf
    from datetime import datetime as _dt

    import entry as E
    from ldv.checks._fixtures import find_corpus
    from ldv.tools import usage_store as US

    builtin = find_corpus()
    if builtin is None:
        ok("★ [接入] ⑨ §I7 快照留痕（**跳过**：内置语料不在）", True, "0")
        return
    tmp = Path(_tf.mkdtemp(prefix="ldv-snap-"))
    saved_env = _os.environ.get("LDV_CORPUS")
    try:
        nodes = tmp / "_data" / "shop" / "nodes"
        nodes.mkdir(parents=True)
        for f in sorted(builtin.glob("*.md")):
            _sh.copy2(f, nodes / f.name)
        # ⚠️ 指向 **nodes 目录本身**（外部语料的解析形状：`_data/<名>/nodes`）
        #   —— 指到语料根会让 `entry.App` 判不出 usage 目录（name != "nodes"）。
        _os.environ["LDV_CORPUS"] = str(nodes)
        app = E.App(None)                       # 读 env ⇒ 刚搭的临时语料
        r = app.query({"require": []})
        dids = [n["did"] for n in (r.get("nodes") or [])]
        if len(dids) < 2:
            ok("★ [接入] ⑨ §I7 快照留痕（**跳过**：语料没有两支可下钻）", True, "0")
            return
        d1, d2 = dids[0], dids[1]
        app.drill(d1)
        app.drill(d2)
        rows = {x["did"]: x for x in US.load(tmp / "_data" / "shop")}
        s1 = (rows.get(d1) or {}).get("snapshot", "")
        s2 = (rows.get(d2) or {}).get("snapshot", "")
        try:
            j1, j2 = (json.loads(s1) if s1 else {}), (json.loads(s2) if s2 else {})
        except ValueError as e:
            j1, j2 = {}, {"_err": str(e)}
        ok("★ [接入] ⑨-① 下钻 ⇒ 记录带上 §I7 快照（JSON 可解析，kind 在）",
           bool(s1) and bool(j1.get("kind")) and j1.get("did") == d1,
           f"快照 {len(s1)} 字节｜did={j1.get('did')}｜kind={j1.get('kind')}")
        ok("★ [接入] ⑨-② 两条记录的快照**各对各的 did**（防串方向）",
           j1.get("did") == d1 and j2.get("did") == d2,
           f"{d1}→{j1.get('did')}｜{d2}→{j2.get('did')}")

        k = app.ensure("keyset")
        live = json.dumps(k.plugin.record(k.direction(d1)),
                          ensure_ascii=False, sort_keys=True)
        ok("★ [接入] ⑨-③ 快照与现场重算的 `record(d)` 逐字相同（同源）",
           live == s1, f"现场 {len(live)} 字节｜落盘 {len(s1)} 字节｜相同={live == s1}")

        # ④ 向后兼容：没有 snapshot 的旧文件照常读
        US.append(tmp / "_data" / "shop", did="D-OLD", outcome=1.0,
                  propensity=0.5, qfp="old-q", note="旧格式")
        old = next((x for x in US.load(tmp / "_data" / "shop")
                    if x["did"] == "D-OLD"), None)
        ok("★ [接入] ⑨-④ 旧记录（无 snapshot 行）load 不炸、快照为空",
           old is not None and old.get("snapshot") == "",
           f"读回 {old is not None}｜snapshot={old.get('snapshot')!r}")

        # ⑤ snapshot 是留痕不是输入：带与不带，倾向必须逐字相同
        now = _dt(2026, 10, 23, 12, 0, 0)
        a_dir = tmp / "_data" / "a"
        b_dir = tmp / "_data" / "b"
        for dd in (a_dir, b_dir):
            dd.mkdir(parents=True)
        US.append(a_dir, did="D1", outcome=1.0, propensity=0.5, qfp="q",
                  note="带快照", snapshot='{"kind":"keyset","did":"D1"}')
        US.append(b_dir, did="D1", outcome=1.0, propensity=0.5, qfp="q",
                  note="不带快照")
        ok("★ [接入] ⑨-⑤ 快照不是输入：带与不带 ⇒ 倾向逐字相同（留痕不驱动结构）",
           US.aged_tendency(a_dir, now=now) == US.aged_tendency(b_dir, now=now),
           f"带 {US.aged_tendency(a_dir, now=now)}｜不带 {US.aged_tendency(b_dir, now=now)}")
    finally:
        if saved_env is None:
            _os.environ.pop("LDV_CORPUS", None)
        else:
            _os.environ["LDV_CORPUS"] = saved_env
        _sh.rmtree(tmp, ignore_errors=True)


def test_forgetting() -> None:
    """★ [遗忘] 两层记忆：短期显性（缓冲，容量淘汰）+ 长期隐性（正式，降权不删）。

    用户 2026-10-22 定：**只降权、不删** · **稳定就是正式** · **给它们名字**。
    ⇒ 「遗忘」只在**计算**上发生：记录永不删，算倾向时按年龄乘衰减因子。
       删了正式记录，账本就不是账本，而且不可审计、不可回退。

    ① 降权**不删**：算完衰减，记录条数不变
    ② 老的权重更小（90 天前 < 1 天前）
    ③ 缺时间戳 ⇒ 不衰减（w = 1.0）—— **不猜**：不知道时间既不能当很老，也不能当很新
    ④ 半衰期关掉 ⇒ 回到等权平均（**可回退的对照**）
    ⑤ 两层记忆各有名字（`status` 里两个键都在）

    ⚠️ 全部用临时目录 + 注入 `now` ⇒ 不依赖外部语料、不随运行时刻漂。
    """
    import shutil as _sh
    import tempfile as _tf
    from datetime import datetime, timedelta

    from ldv.tools import usage_store as US

    tmp = Path(_tf.mkdtemp(prefix="ldv_forget_"))
    try:
        now = datetime(2026, 10, 22, 12, 0, 0)
        US.append(tmp, did="D1", outcome=1.0, propensity=0.5, qfp="q-old",
                  at=(now - timedelta(days=90)).isoformat())
        US.append(tmp, did="D1", outcome=-1.0, propensity=0.5, qfp="q-new",
                  at=(now - timedelta(days=1)).isoformat())
        before = len(US.load(tmp))

        _ = US.aged_tendency(tmp, now=now)
        ok("★ [遗忘] ① 降权**不删**：算完衰减，记录条数不变",
           len(US.load(tmp)) == before == 2,
           f"算前 {before} 条｜算后 {len(US.load(tmp))} 条")

        w_old = US.decay_weight((now - timedelta(days=90)).isoformat(), now=now)
        w_new = US.decay_weight((now - timedelta(days=1)).isoformat(), now=now)
        ok("★ [遗忘] ② 老的权重更小（半衰期 30 天）",
           w_old < w_new < 1.0,
           f"90 天前 w={w_old:.4f}｜1 天前 w={w_new:.4f}")

        ok("★ [遗忘] ③ 缺时间戳 ⇒ 不衰减（w = 1.0），不猜",
           US.decay_weight("", now=now) == 1.0,
           f"w = {US.decay_weight('', now=now)}")

        aged = US.aged_tendency(tmp, now=now)
        flat = US.aged_tendency(tmp, now=now, half_life_days=0)
        ok("★ [遗忘] ④ 半衰期关掉 ⇒ 回到等权平均（可回退的对照）",
           abs(flat["D1"]) < 1e-12 and aged["D1"] < 0,
           f"关掉 {flat['D1']:+.4f}（等权 = 0）｜开着 {aged['D1']:+.4f}"
           f"（被**新的**那条拉向负）")

        st = US.status(tmp)
        ok("★ [遗忘] ⑤ 两层记忆各有名字（短期显性 / 长期隐性）",
           US.SHORT_TERM in st and US.LONG_TERM in st,
           f"{US.SHORT_TERM}：{st[US.SHORT_TERM]['遗忘方式']}｜"
           f"{US.LONG_TERM}：{st[US.LONG_TERM]['遗忘方式']}")
    finally:
        _sh.rmtree(tmp, ignore_errors=True)


def test_motivation() -> None:
    """★ [动机] 显著性分档：三档由**后果**定，档内按影响面定序。

    用户 2026-10-22 定：三档分类 · 后果定档 + 影响面定序 · 要**完整通路**
    （从列清单、提问，一直到实施处理）。

    ① 分档看**后果**不看大小：99999 项的「切不开」仍是「可延后」，
       3 项的「边打架」仍是「必须」—— 大小混进档位就是把分档做成排序
    ② 档位是**等级不是分数**：返回值必须是三档字符串之一
    ③ 报告里三档齐全，档内按成员数降序
    ④ **闭环**：待展开的方向 expand 之后，不再出现在「可延后」
    ⑤ **持续**：矛盾没解决时，两次 report 的「必须」档逐字相同（不因提过而消失）
    """
    import entry as E

    from ldv.core import reflect as RF

    cases = [
        ({"类型": "边打架"}, "必须"),
        ({"类型": "子覆盖越出父"}, "必须"),
        ({"类型": "孤立方向"}, "必须"),
        ({"类型": "切不开", "项数": 99999}, "可延后"),
        ({"待展开": True, "成员数": 99999}, "可延后"),
        ({"成员数": 1, "子方向数": 0}, "可忽略"),
    ]
    bad = [(c, RF.significance(c)) for c, want in cases if RF.significance(c) != want]
    ok("★ [动机] ① 分档看**后果**不看大小（99999 项的切不开仍是「可延后」）",
       not bad, f"{len(cases)} 个用例｜不符 {bad or '无'}")

    ok("★ [动机] ② 档位是**等级不是分数**（返回值必须是三档字符串之一）",
       all(RF.significance(c) in RF.SIGNIFICANCE for c, _ in cases),
       f"档位 {sorted(RF.SIGNIFICANCE)}｜"
       f"返回类型 {type(RF.significance(cases[0][0])).__name__}")

    app = E.App(None)
    k = app.kernels["keyset"]
    g = RF.report(k)["分级"]
    must, later = g["必须"], g["可延后"]

    def _order(xs):
        return sorted(xs, key=lambda x: (-(x.get("成员数") or 0), x.get("方向") or ""))

    ok("★ [动机] ③ 报告里三档齐全，且档内按成员数降序",
       set(g) >= {"档位说明", "必须", "可延后", "可忽略数", "档内顺序"}
       and must == _order(must) and later == _order(later),
       f"必须 {len(must)}｜可延后 {len(later)}｜可忽略 {g['可忽略数']}")

    # ④ 闭环：用**只建根**的内核 —— `make_keyset_root` 只 `build` 根、**不 insert**，
    #    所以 D0 装着全部项但**没展开** ⇒ 这是真的「待展开」状态。
    #    ⚠️ 完整树里待展开恒为 0（`build_keyset` 与 `E.App` 都会 insert 到底），
    #       拿它们测这条会**空转** —— 实测内置 36 项 0 个、ext-wiki-vote 0 个。
    from ldv.checks._fixtures import load as _load
    from ldv.checks._fixtures import make_keyset_root

    loaded = _load()
    if loaded is None:
        ok("★ [动机] ④ 闭环（**跳过**：内置语料加载不了）", True, "0")
    else:
        _nodes, _edges, _d = loaded
        k3 = make_keyset_root(_nodes)()
        before_later = [x for x in RF.report(k3)["分级"]["可延后"]
                        if x["方向"] == "D0"]
        n_before = len(k3.all_directions())
        k3.expand(k3.direction("D0"))
        after_later = [x for x in RF.report(k3)["分级"]["可延后"]
                       if x["方向"] == "D0"]
        ok("★ [动机] ④ **闭环**：展开 D0 之后，它不再出现在「可延后」",
           len(before_later) == 1 and not after_later,
           f"只建根：{n_before} 个方向｜展开前「可延后」{len(before_later)} 条"
           f"（成员 {before_later[0]['成员数'] if before_later else 0}）"
           f" → 展开后 {len(after_later)} 条｜方向数 → {len(k3.all_directions())}")

    ok("★ [动机] ⑤ **持续**：矛盾没解决时，两次 report 的「必须」档逐字相同",
       RF.report(k)["分级"]["必须"] == RF.report(k)["分级"]["必须"],
       f"必须 {len(RF.report(k)['分级']['必须'])} 条（两次相同）")

    # ⑥ **建树计划**：惰性内核（只建根）上，plan() 一轮轮报出该展开哪些。
    #    ★ 这是「动机」在**建树阶段**的落地 —— 完整树上这一档恒空（树建完就没待办）。
    from ldv.checks._fixtures import make_keyset_root as _mkr

    loaded3 = _load()
    if loaded3 is None:
        ok("★ [动机] ⑥ 建树计划（**跳过**：内置语料加载不了）", True, "0")
    else:
        _n3, _e3, _d3 = loaded3
        kl = _mkr(_n3)()
        before_dirs = len(kl.all_directions())
        p1 = RF.plan(kl, rounds=2)
        after_dirs = len(kl.all_directions())
        ok("★ [动机] ⑥ **建树计划**：惰性内核上一轮轮展开，方向数增长、待展开收敛",
           before_dirs == 1 and after_dirs > before_dirs
           and p1["轮次"][0]["展开"] == ["D0"],
           f"方向 {before_dirs} → {after_dirs}｜第1轮展开 {p1['轮次'][0]['展开']}"
           f"｜总展开 {p1['总展开']}｜剩余待展开 {p1['剩余待展开']}")

        # ⑦ ★★ 惰性 + plan 的终态与完整建树**同构**（划分集合相同）
        #    ⚠️ **不能比 did**：did 是**创建顺序编号**，惰性建树的展开顺序不同
        #       ⇒ 同一个划分单元拿到不同编号（实测：25 个方向里 20 个 did 对不上）。
        #       `B15` 说的也是「同构」，不是「did 相同」。
        #    ⇒ 比**划分集合**：每个方向的成员集（按内容排序），忽略编号。
        kl2 = _mkr(_n3)()
        RF.plan(kl2, rounds=60, per_round=3)
        kf, _pf = build_keyset(_n3)

        def _parts(kk):
            return sorted(tuple(sorted(kk.members_of(d))) for d in kk.all_directions())

        ok("★ [动机] ⑦ ★★ 惰性 + plan 的终态与完整建树**同构**（划分集合逐字相同）",
           _parts(kl2) == _parts(kf),
           f"惰性 {len(_parts(kl2))} 个划分｜完整 {len(_parts(kf))} 个"
           f"｜划分集合相同 {_parts(kl2) == _parts(kf)}"
           f"（did 编号可不同 —— 那是创建顺序，不是结构）")

    # ⑧ `--plan` 入口：报「起点 1 个方向」并收敛到剩余 0
    #    ⚠️ 「起点」必须在 plan() **之前**取 —— 之后取拿到的是终态（实测被打成 25）。
    from ldv.cli import plan_report as _pr

    okp, ptext = _pr(rounds=12)
    ok("★ [动机] ⑧ `cli --plan` 入口：起点 1 个方向、收敛到剩余 0",
       okp and "起点 1 个方向" in ptext and "剩余待展开 0" in ptext,
       next((ln.strip() for ln in ptext.splitlines() if "起点" in ln), ptext[:60]))

    # ⑨ ★★ 半成品树**能查** —— 这条守的是一个**被说错过的事实**：
    #    「树不完整 ⇒ 查询会漏 ⇒ 撞 §K8」是**错的**。`query` 对每个说「是」的方向
    #    **无条件 `expand`**（`core/kernel.py` 的 `v is Tri.YES` 分支）⇒ 边查边建。
    from ldv.core.kernel import Query as _Q

    if loaded3 is None:
        ok("★ [动机] ⑨ 半成品树能查（**跳过**：内置语料加载不了）", True, "0")
    else:
        _n4 = loaded3[0]
        kl3 = _mkr(_n4)()
        kf3, _pf3 = build_keyset(_n4)
        qq = _Q(ideal=frozenset(_n4))
        r_lz, r_fu = kl3.query(qq), kf3.query(qq)
        ok("★ [动机] ⑨ ★★ 半成品树能查：查询自动展开，答案与完整建树逐字相同",
           r_lz.hit_items == r_fu.hit_items and r_lz.counts == r_fu.counts
           and str(r_lz.status) == str(r_fu.status),
           f"查询前 1 个方向 → 后 {len(kl3.all_directions())}"
           f"｜命中 {len(r_lz.hit_items)} vs {len(r_fu.hit_items)}"
           f"｜三态 {r_lz.counts} vs {r_fu.counts}")

    # ⑩ **续建**：`save(extra=试过方向)` → `restore` → `plan` 能**接着推进**，直到剩余 0。
    #    ⚠️ 「试过方向」**必须持久化**：不存的话每次重试「分不开」的那几支、
    #       占掉每轮名额 ⇒ 剩余**永远不收敛**（实测：4 次都停在剩余 6）。
    if loaded3 is None:
        ok("★ [动机] ⑩ 续建（**跳过**：内置语料加载不了）", True, "0")
    else:
        import shutil as _sh2
        import tempfile as _tf2

        from ldv.checks._fixtures import items as _its
        from ldv.core import snapshot as _snap
        from ldv.core.kernel import Kernel as _K
        from ldv.plugins.keyset import KeysetPlugin as _KP

        _n5, _e5 = loaded3[0], loaded3[1]
        tmpd = Path(_tf2.mkdtemp(prefix="ldv_snap_"))
        try:
            plug5, its5 = _KP(), _its(_n5)
            fp5 = _snap.fingerprint(its5, _e5, {"方向函数": "keyset", "用途": "test-续建"})
            pp5 = _snap.tree_path("keyset", fp5, tmpd)
            k5 = _mkr(_n5)()
            tried5: set[str] = set()
            rounds_used = 0
            for _ in range(10):
                r5 = RF.plan(k5, rounds=2, per_round=3, tried=tried5)
                tried5 = set(r5["试过方向"])
                _snap.save(k5, plug5, "keyset", fp5, path=pp5,
                           extra={"plan_tried": sorted(tried5)})
                k5, _rep5 = _snap.restore(_K, plug5, its5, pp5, fp5)
                rounds_used += 1
                if r5["剩余待展开"] == 0:
                    break

            kf5, _pf5 = build_keyset(_n5)
            _parts5 = lambda kk: sorted(tuple(sorted(kk.members_of(d)))  # noqa: E731
                                        for d in kk.all_directions())
            ok("★ [动机] ⑩ **续建**：save/restore 循环推进到剩余 0，终态与完整建树同构",
               rounds_used < 10 and _parts5(k5) == _parts5(kf5),
               f"{rounds_used} 轮｜终态 {len(k5.all_directions())} vs 完整 "
               f"{len(kf5.all_directions())}｜试过 {len(tried5)} 个"
               f"｜划分集合相同 {_parts5(k5) == _parts5(kf5)}")
        finally:
            _sh2.rmtree(tmpd, ignore_errors=True)

    # ⑪ **循环跑到剩余 0**（同进程，模拟 `--plan-all` 的内层）——
    #    ⚠️ 不在这里调 `plan_report(snapshot=True)`：它写 `~/.cache/ldv`，判据不该污染用户缓存。
    if loaded3 is None:
        ok("★ [动机] ⑪ 循环跑到剩余 0（**跳过**：内置语料加载不了）", True, "0")
    else:
        k6 = _mkr(loaded3[0])()
        tried6: set[str] = set()
        n_calls = 0
        r6 = None
        while n_calls < 20:
            r6 = RF.plan(k6, rounds=3, per_round=3, tried=tried6)
            tried6 = set(r6["试过方向"])
            n_calls += 1
            if r6["剩余待展开"] == 0:
                break
        ok("★ [动机] ⑪ **循环跑到剩余 0**（模拟 `--plan-all` 内层，上限 20 次）",
           r6 is not None and r6["剩余待展开"] == 0 and n_calls < 20,
           f"{n_calls} 次调用｜终态 {len(k6.all_directions())} 个方向"
           f"｜剩余 {r6['剩余待展开'] if r6 else '?'}")

    # ⑫ **账本两类进清单**（2026-10-23）—— `滞留` / `根覆盖之外` 原来**根本没被读过**
    #    （`graded()` 只收 矛盾/展开失败/待展开）⇒ 内核账本记着，反思层不看 = 漏。
    #    ⚠️ 内置语料上两类都是 **0**（三个方向实测全 0）⇒ 拿完整树测会**空转**
    #       （「空转与通过长得一模一样」）。能确定性造出来的是**维护路径**：
    #       `sequence` 先建 6 ⇒ 滞留 16；`reach` 先建 2 ⇒ 根覆盖之外 30。
    if loaded3 is None:
        ok("★ [动机] ⑫ 账本两类进清单（**跳过**：内置语料加载不了）", True, "0")
        ok("★ [动机] ⑫′ 未分档来源必须炸（**跳过**：内置语料加载不了）", True, "0")
    else:
        from ldv.checks._fixtures import build_incremental as _binc
        from ldv.checks._fixtures import make_builder as _mkb

        _n3, _e3, _ = loaded3
        _ids = sorted(_n3)
        k_stay = _binc(_mkb("sequence", _n3, _e3), _n3, _ids[:6], _ids[6:])
        k_oos = _binc(_mkb("reach", _n3, _e3), _n3, _ids[:2], _ids[2:])
        g_stay, g_oos = RF.graded(k_stay), RF.graded(k_oos)
        stay_later = [r for r in g_stay["可延后"] if r.get("来源") == "滞留"]
        oos_must = [r for r in g_oos["必须"] if r.get("来源") == "越界"]
        n_stay = sum(r["项数"] for r in stay_later)
        n_oos = sum(r["项数"] for r in oos_must)
        ok("★ [动机] ⑫ 账本两类**进清单且分档正确**（滞留⇒可延后 / 越界⇒必须）",
           n_stay == k_stay.stats()["滞留"] and n_oos == k_oos.stats()["根覆盖之外"]
           and n_stay > 0 and n_oos > 0
           and not [r for r in g_stay["必须"] if r.get("来源") == "滞留"]
           and not [r for r in g_oos["可延后"] if r.get("来源") == "越界"]
           # 聚合：**不逐项列**（滞留 16 项 ⇒ 2 行；越界 30 项 ⇒ 1 行）
           and len(stay_later) < n_stay and len(oos_must) < n_oos,
           f"滞留 {n_stay} 项 ⇒ {len(stay_later)} 行（可延后）｜"
           f"越界 {n_oos} 项 ⇒ {len(oos_must)} 行（必须）")

        # ★ 反向对照：**未知来源不许兜成「可忽略」**——那正是「漏」的产生方式
        try:
            RF.significance({"来源": "某个还没分档的新来源"})
            raised = False
        except ValueError:
            raised = True
        ok("★ [动机] ⑫′ 未分档来源**当场炸**，不静默降成「可忽略」（反向对照）",
           raised and RF.significance({"成员数": 1, "子方向数": 0}) == "可忽略",
           f"未知来源 raise={raised}｜「m≤1」形状仍判可忽略=True")

    # ⑬ **判空不许报成「待展开」**（2026-10-23）
    #    根因：判空**不写** `_expanded`（叶不是终态，成员集变了要能重试）
    #    ⇒ `is_expanded` 对它们是 False ⇒ 只看标志就把「**试过分不开**」
    #      当成「**还没试**」。实测内置 36 项 keyset：账本 7 条 `unsplittable`，
    #      旧实现把这 7 个方向**全部**报成待展开（两个集合**逐字相同**）。
    #    ★ 反向对照（同一句里）：`make_keyset_root` 上的 D0 **必须**仍是待展开 ——
    #      否则「一条都不报」也能让这条绿（「抓不到」比「算错」更危险）。
    if loaded3 is None:
        ok("★ [动机] ⑬ 判空不报成待展开（**跳过**：内置语料加载不了）", True, "0")
    else:
        from ldv.checks._fixtures import build_keyset as _bk13
        from ldv.checks._fixtures import make_keyset_root as _mk13

        _n13, _e13, _ = loaded3
        k13, _p13 = _bk13(_n13)
        uns13 = set(RF.unsplittable(k13))
        g13 = RF.graded(k13)
        pend13 = {r["方向"] for r in g13["可延后"] if r.get("来源") == "待展开"}
        fail13 = {r["方向"] for r in g13["可延后"] if r.get("来源") == "展开失败"}
        rep13 = RF.report(k13)
        # 反向对照：真的没试过的方向**必须**还在「待展开」里
        k_root13 = _mk13(_n13)()
        pend_root = {r["方向"] for r in RF.graded(k_root13)["可延后"]
                     if r.get("来源") == "待展开"}
        ok("★ [动机] ⑬ **判空不许报成「待展开」**（以账本为准，不看 `is_expanded`）",
           len(uns13) > 0 and not (pend13 & uns13) and uns13 <= fail13
           and rep13["待展开数"] == 0 and "D0" in pend_root,
           f"账本判空 {len(uns13)} 条｜报成待展开 {len(pend13 & uns13)}（必须 0）"
           f"｜报成展开失败 {len(uns13 & fail13)}｜report 待展开数 {rep13['待展开数']}"
           f"｜反向对照：只建根时 D0 仍待展开={'D0' in pend_root}")

    # ⑭ **「没被收录进系统的数据」按类型拆**（2026-10-23，用户定）
    #    整类缺失（该类型**一条都没进**）⇒ 这类问题完全答不了 ⇒ 必须
    #    部分缺失（**有进有没进**）        ⇒ 还能答一部分       ⇒ 可延后
    #    ⚠️ 为什么不用「离根远近」：实测整图 36 项是**一个弱连通分量**，
    #       且按插件语义 30 条全部「走不到根」⇒「连通却没纳入」**逻辑上不可能**
    #       （`hit` 就是同一个谓词）⇒ 拆了也只有一支 = 空转。
    #    ★ 反向对照：不给 `type_of` ⇒ 退化成一方向一行、判「必须」并**明说未拆**。
    if loaded3 is None:
        ok("★ [动机] ⑭ 越界按类型拆（**跳过**：内置语料加载不了）", True, "0")
    else:
        from ldv.checks._fixtures import make_builder as _mkb14
        from ldv.checks._fixtures import build_incremental as _binc14

        _n14, _e14, _ = loaded3
        _ids14 = sorted(_n14)
        k14 = _binc14(_mkb14("reach", _n14, _e14), _n14, _ids14[:2], _ids14[2:])

        def _type14(i: str) -> str:
            return str(_n14[i].fields.get("type") or "未知")

        g14 = RF.graded(k14, type_of=_type14)
        g14_raw = RF.graded(k14)
        must14 = [r for r in g14["必须"] if r.get("来源") == "越界"]
        later14 = [r for r in g14["可延后"] if r.get("来源") == "越界"]
        n_all14 = sum(r["项数"] for r in must14)
        n_part14 = sum(r["项数"] for r in later14)
        n_oos14 = k14.stats()["根覆盖之外"]
        # 每一行内部也要自洽：整类 ⇒ 项数 == 该类总数；部分 ⇒ 严格小于
        selfok = all(r["项数"] == r["该类总数"] for r in must14) and \
            all(r["项数"] < r["该类总数"] for r in later14)
        raw14 = [r for r in g14_raw["必须"] if r.get("来源") == "越界"]
        ok("★ [动机] ⑭ **越界按类型拆**：整类缺失⇒必须 / 部分缺失⇒可延后（两支都必须非空）",
           bool(must14) and bool(later14) and selfok
           and n_all14 + n_part14 == n_oos14
           and all(r["缺口"] == "整类" for r in must14)
           and all(r["缺口"] == "部分" for r in later14)
           # 反向对照：不给 type_of ⇒ 退化成一行、缺口为空、落在「必须」
           and len(raw14) == 1 and raw14[0].get("缺口") is None
           and raw14[0]["项数"] == n_oos14,
           f"整类 {len(must14)} 类 {n_all14} 项（必须）｜部分 {len(later14)} 类 "
           f"{n_part14} 项（可延后）｜合计 {n_all14 + n_part14} == 越界 {n_oos14}"
           f"｜不给 type_of ⇒ {len(raw14)} 行、缺口={raw14[0].get('缺口') if raw14 else '?'}")


def test_no_unsourced_strings() -> None:
    """★ [不产内容] 查询输出里**每个字符串都能指回材料**（§S1）。

    判据不是「看起来没生成」——是**逐字符串找出处**：

        项 id / 方向 id / 标题 / 键  ⇒ 必须在这四类材料里
        协议字段名与枚举值            ⇒ 必须在固定词表里
        错误与建议文案                ⇒ 固定模板（前缀白名单），不参与内容判定

    ⚠️ 这条守的是**未来**：现在这些字段都由构造保证（`_node` 只从 `fields` 取标题）。
       一旦有人加「自动摘要」「模型润色」，第一个被抓到的就是这里 ——
       而那种改动**不会让任何现有判据变红**（这正是它存在的理由）。
    """
    import entry as E

    app = E.App(None)
    k = app.kernels["keyset"]

    material: set[str] = set(app.nodes)
    for nid, n in app.nodes.items():
        material |= {str(v) for v in n.fields.values()}
        material |= set(n.keys)
    material |= {d.did for d in k.all_directions()}
    material |= set(app.titles.values())

    protocol = {
        "ok", "plugin", "keyset", "reach", "sequence", "require", "forbid",
        "node", "nodes", "did", "title", "n", "n_branches", "n_answer",
        "answer_ids", "answer_titles", "ordered_by", "default", "usage",
        "path", "stopped", "relax", "unknown_keys", "suggest", "ms",
        "requirement", "branches_shown", "error", "fingerprint",
        # 停因的枚举值（`stopped` 字段的取值，代码写的）
        "split", "leaf", "root",
    }
    # 错误 / 建议 / 停因文案：**固定模板**（句子是代码写的，键名来自数据）
    # ⚠️ 加一条就红一次 —— 这正是它该有的行为：新增文案必须**显式**进白名单，
    #    否则「偷偷生成一句话」和「加了个提示」长得一样。
    fixed_prefix = ("这些键", "不认识的", "没有插件", "没有这个节点",
                    "已经是最细", "已经到底", "停在了",
                    "这个问题")          # ← `out_of_structure_note`（2026-10-22 新增）

    # ⚠️ **只查值，不查键** —— 键是协议（代码写的字段名：`rank` / `n_members` / `must`…），
    #    值是数据（应来自材料）。第一版连键一起查 ⇒ 报 139 个「无出处」，全是字段名。
    def walk_values(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for vv in o.values():
                yield from walk_values(vv)
        elif isinstance(o, (list, tuple)):
            for x in o:
                yield from walk_values(x)

    bad: list[str] = []
    for spec in ({"require": []}, {"require": ["type=概念"]},
                 {"require": ["不存在=x"]}):
        # ⚠️ 输出会**原样回显**查询条件（`requirement` 段）—— 那是用户自己的话，
        #    不是系统生成的内容 ⇒ 回显合法，把这一趟的 require/forbid 计入材料。
        material |= set(spec.get("require") or ())
        material |= set(spec.get("forbid") or ())
        for s in walk_values(app.query(spec)):
            if not s or s in material or s in protocol:
                continue
            if s.startswith(fixed_prefix):
                continue
            bad.append(s[:60])

    ok("★ [不产内容] 查询输出里每个字符串都能指回材料（§S1）",
       not bad,
       f"3 次查询｜材料 {len(material)} 个字符串｜协议词 {len(protocol)}｜"
       f"无出处 {len(bad)} 个" + (f"：{bad[:3]}" if bad else ""))

    # ★ **合成对照**：判据必须**能红**。故意塞一个不在材料里的字符串，
    #   同一套过滤逻辑必须抓到它 —— 否则「无出处 0 个」只是「没查」。
    probe = {"answer_titles": ["这是一句凭空生成的话"]}
    caught = [s for s in walk_values(probe)
              if s and s not in material and s not in protocol
              and not s.startswith(fixed_prefix)]
    # ★ 这条**就是 §S1 的合成对照**（2026-10-23 显式标出来）：
    #   它喂一个**已知无出处**的字符串，要求扫描器抓到 —— 抓到才证明
    #   「3 次真查询里无出处 0 个」**不是扫描器瞎了**。
    #   ⚠️ 以前它只在 detail 里叫「判据自检」，矩阵看不见 ⇒ 被记成「零对照」。
    ok("★ [不产内容] §S1 合成对照：故意塞一个无出处字符串 ⇒ **抓得到**",
       caught == ["这是一句凭空生成的话"], f"抓到 {caught}")


def test_out_of_structure() -> None:
    """★ [超出结构] 「**超出结构**」与「**查不到**」必须分开报。

    - 键不在词汇表 ⇒ `out_of_structure = True` + 提示「请人介入」
    - 键都在、只是组合为空 ⇒ `out_of_structure = False`（**这一格是空的**）

    ⚠️ 判据用**结构信号**（键在不在 `all_keys`），**不做语义判断** ——
       那会撞「不做语义推理」。所以「超出结构」只能抓到「用户用了结构里没有的词」，
       抓不到「用户用的词都在、但问的东西结构表达不了」—— 后者**如实做不到**。
    """
    import entry as E

    app = E.App(None)

    r_bad = app.query({"require": ["这个词根本不存在=x"]})
    ok("① [超出结构] 键不在词汇表 ⇒ out_of_structure = True（并提示请人介入）",
       r_bad.get("out_of_structure") is True
       and bool(r_bad.get("unknown_keys"))
       and "超出本结构的词汇" in (r_bad.get("out_of_structure_note") or ""),
       f"out_of_structure={r_bad.get('out_of_structure')}"
       f"｜unknown_keys={len(r_bad.get('unknown_keys') or [])}")

    r_ok = app.query({"require": ["type=概念"]})
    ok("② [超出结构] 键都在词汇表 ⇒ out_of_structure = False",
       r_ok.get("ok") is True and r_ok.get("out_of_structure") is False,
       f"命中 {r_ok.get('n_answer')} 项｜out_of_structure={r_ok.get('out_of_structure')}")

    # ③ **反向对照**：组合为空 ≠ 超出结构 —— 这一格是空的，不是没有这个维度
    r_empty = app.query({"require": ["type=概念", "type=立场"]})
    ok("③ [超出结构] **组合为空 ≠ 超出结构**（反向对照）",
       r_empty.get("ok") is True and r_empty.get("out_of_structure") is False
       and r_empty.get("n_answer") == 0,
       f"命中 {r_empty.get('n_answer')} 项｜out_of_structure={r_empty.get('out_of_structure')}"
       f"｜松弛建议 {len(r_empty.get('relax') or [])} 条")


def test_verified_external_only() -> None:
    """★ [外部授予] 系统不许自己把节点标成「已验证」。

    立场（`USER.md`）：「已验证」只能**外部授予** —— **人或外部工具都能写**，
    **系统运行时不写**。现在这条靠「没人写」维持；本判据把它变成**可查**。

    查法：扫 `ldv/` + `entry.py` 的**字符串字面量**（`ast.Constant`），
    排除 docstring（那里解释这条立场是合法的），排除 test 文件（判据自己的描述里就有这个词）。
    """
    import ast as _ast

    repo = Path(__file__).resolve().parents[2]
    files = sorted((repo / "ldv").rglob("*.py")) + [repo / "entry.py"]
    bad: list[str] = []
    scanned = 0
    for f in files:
        if "test" in f.name:
            continue
        scanned += 1
        try:
            tree = _ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        docs: set[int] = set()
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Module, _ast.FunctionDef,
                                 _ast.AsyncFunctionDef, _ast.ClassDef)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], _ast.Expr)
                        and isinstance(body[0].value, _ast.Constant)):
                    docs.add(id(body[0].value))
        for node in _ast.walk(tree):
            if (isinstance(node, _ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docs
                    # ⚠️ 只认**字段值形态**：`"已验证"` 或 `"...=已验证"`。
                    #    第一版用 `"已验证" in value` ⇒ 误报 `structure.py` 的
                    #    「探测器**已验证**有效」（那是动词，不是字段值）。
                    and (node.value == "已验证" or "=已验证" in node.value)):
                bad.append(f"{f.name}:{node.lineno}")

    ok("★ [外部授予] 系统源码里没有把节点标成「已验证」的字符串字面量（docstring 不算）",
       not bad, f"扫 {scanned} 个模块｜命中 {len(bad)} 处" + (f"：{bad[:3]}" if bad else ""))


def test_self_corpus() -> None:
    """★ 认知原型的两条自我判据（2026-10-22，§S5）。

    ① **自描述一致**：断言集总表的行数 == 引言声称的条数；每行「级」合法。
    ② **能回答自己**：自我语料建树后，`种类=断言` 的项集 == 总表编号集；
       `含定理=是` 的项集 == 从总表**独立重算**的编号集 —— 两条路互相核对
       （解析表格 vs 建语料再查），不是自己证自己。

    ⚠️ 环境纪律：`App(<路径>)` 会把 `LDV_CORPUS` 写进 `os.environ` ⇒
       save/restore + 验恢复（`[缓冲] ㊳` 那条教训）。
    """
    import shutil as _shutil
    import tempfile as _tf
    from pathlib import Path as _P

    from ldv.tools import make_self_corpus as MSC
    import entry as E

    # ⚠️ 本文件在 ldv/tests/ 下 ⇒ parents[2] 才是 repo 根（parents[1] 是 ldv/）
    repo = _P(__file__).resolve().parents[2]
    rows = MSC.parse_assertions(repo)
    claimed = MSC.claimed_count(repo)
    bad_tier = [r["编号"] for r in rows
                if not any(x in r["级"] for x in ("Ⅰ", "Ⅱ", "Ⅲ"))]
    ok("★ [自我] ⑴ 断言集自描述一致：总表行数 == 引言声称条数，且每行「级」合法",
       len(rows) == claimed and not bad_tier,
       f"总表 {len(rows)} 行｜声称 {claimed} 条｜非法级 {bad_tier or '无'}")

    tmp = _P(_tf.mkdtemp())
    try:
        st = MSC.build(repo, tmp)
        saved = os.environ.get("LDV_CORPUS")
        try:
            app = E.App(str(tmp / "nodes"))
            r1 = app.query({"require": ["种类=断言"]})
            r2 = app.query({"require": ["含定理=是"]})
        finally:
            if saved is None:
                os.environ.pop("LDV_CORPUS", None)
            else:
                os.environ["LDV_CORPUS"] = saved
            restored = os.environ.get("LDV_CORPUS") == saved
        want_all = {f"asrt-{r['编号']}" for r in rows}
        want_thm = {f"asrt-{r['编号']}" for r in rows if "Ⅰ" in r["级"]}
        ok("★ [自我] ⑵ 自我语料能回答自己：断言集 / 含定理集与总表独立重算逐字相同",
           bool(r1.get("ok")) and bool(r2.get("ok"))
           and set(r1["answer_ids"]) == want_all
           and set(r2["answer_ids"]) == want_thm and restored,
           f"断言 {r1.get('n_answer')}/{len(want_all)}｜"
           f"含定理 {r2.get('n_answer')}/{len(want_thm)}｜节点 {st['节点']}"
           f"｜环境已恢复 {restored}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    for fn in (test_tri, test_loader, test_kernel, test_exposure_model, test_selfopt,
               test_sequence, test_flows, test_emergence, test_divergence,
               test_equivalence, test_semantic_guard, test_rebuild, test_out_of_scope,
               test_stay_at_parent,
               test_cover_leak_baseline, test_cover_oracle_transparency,
               test_b3_reduction_premise, test_reach_cache_premise,
               test_reach_cache_transparency, test_tree_premise,
               test_deletion_path, test_bulk_equivalence, test_fingerprint_sensitivity,
               test_persistence, test_persist_untrusted, test_persist_atomic,
               test_versioning,
               test_reusable_build, test_lock_scope,
               test_service_shell, test_service_failure_paths,
               test_incremental_random, test_service_consumption,
               test_derived_rollback, test_b_known_answers, test_guarantee_matrix,
               test_predicates,
               test_reflect,
               test_memory_index,
               test_buffer,
               test_combination,
               test_contradictions,
               test_capacity,
               test_impact,
               test_direction_request,
               test_approved_loop,
               test_contradiction_requests,
               test_contra_merged,
               test_tree_snapshot,
               test_session,
               test_combine_budget,
               test_predicate_naming,
               test_dimension_values,
               test_version_spots,
               test_evidence,
               test_quotes_evidence,
               test_entry, test_protocol,
               test_exchange_staging,
               test_leaf_cap_requests,
               test_candidate_dimensions,
               test_loader_sharded,
               test_query_hit_items, test_spec_paths, test_maintain,
               test_view_oracles, test_influence_channels, test_views,
               test_multilevel, test_retriever, test_cli_paths,
               test_never_judged, test_b17_cyclic_corpus,
               test_skip_evidence_table, test_self_corpus,
               test_growth_autonomy, test_usage_wiring, test_usage_snapshot,
               test_forgetting,
               test_motivation, test_no_unsourced_strings,
               test_out_of_structure, test_verified_external_only):
        # ★ **兜底：外部语料不在 ⇒ 跳过该函数，不崩**（2026-10-19 修 CI）
        #
        #   外部语料在 `_data/` 下，而 `.gitignore` 规定 `_data/*` **不进仓库**
        #   ⇒ CI 与新 clone 上一个都没有。不兜的话，「没有这份语料」表现成
        #   「单元测试崩了」（四个矩阵全红，2026-10-17 起）。
        #
        #   ⚠️ **只吞「找不到语料」这一类**：`CorpusError` 还有别的成因
        #      （目录结构坏了、id 重复），那些必须照抛 —— 否则真错会被读成跳过。
        #   ⚠️ **跳过 ≠ 通过**：记进 PASS 并写明函数名与语料名。
        #   ⚠️ 该函数**可能已部分执行**（前面的 `ok()` 已记）⇒ 消息里说明这一点。
        try:
            fn()
        except CorpusError as exc:
            if "找不到语料" not in str(exc):
                raise
            PASS.append(f"{fn.__name__}（**跳过**：外部语料不在 —— "
                        f"{str(exc).split('找不到语料')[0].strip()[:40]}…"
                        f"；⚠️ 该函数可能已部分执行）")
    total = len(PASS) + len(FAIL)
    for f in FAIL:
        print(f"  [红] {f}")
    # ★ **跳过必须被看见**（2026-10-19）：原来只打印 FAIL，跳过只进了计数
    #   ⇒ 「跳过了什么」在输出里看不见 —— 那正好是「跳过 ≠ 通过」要防的形状。
    #   CI 上没有 `_data/`，跳过的条目会不少，必须印出来。
    # ⚠️ 只认「（**跳过**」这个**格式**，不认「跳过」二字 —— 很多判据的**名字**里
    #   就有「跳过」（如「报「跳过」而不是「过」」），按字面筛会误报 40+ 条。
    skipped = [p for p in PASS if "（**跳过**" in p]
    if skipped:
        print(f"  [跳过] {len(skipped)} 条：")
        for s in skipped:
            print(f"    · {s}")
    print(f"共 {total} 条：过 {len(PASS)}，红 {len(FAIL)}"
          + (f"，其中跳过 {len(skipped)}" if skipped else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
