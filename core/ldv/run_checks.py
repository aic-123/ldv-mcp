"""跑 B1–B20 —— 设计文档 §7。

    python -m ldv.run_checks                # 三个插件都跑
    python -m ldv.run_checks keyset         # 只跑方向 A（键集包含）
    python -m ldv.run_checks reach          # 只跑方向 B（锚点可达）
    python -m ldv.run_checks sequence       # 只跑方向 C（序列前缀）

    python -m ldv.run_checks --write-cover-leak-baseline    # 重新冻结覆盖不漏的基线
    python -m ldv.run_checks --no-probes                    # 不跑贵的**探针**（判据一条不少）
    python -m ldv.run_checks --cap 400                      # 只跑前 400 项（**会把缩了印出来**）

退出码：**只看「红」的条数**。度量（方向数 / 事件数 / 假阳率 / 使用记录数）走输出，
不进退出码 —— 继承 dce：`report()` 承载度量，退出码只承载「有没有违规」。

---
## 先分清「判据」与「探针」—— 这决定了该关哪一个

    判据（进退出码）   B1–B20。**全量上最贵的是 `B3`**（见下），
                       其次是 `B15`（3 次整建）与 `B8`（n 次插入）
    探针（只报不判）   十个度量。贵的是 `规范重建`（O(n) 次重建 × 每次 O(n)）

⚠️ 别把「判据贵」全记在 `B8` / `B15` 头上：实测 281 项上两者合计 **2.9 s**，
   而 `--no-probes` 整趟（三个方向）是 **7.2 s** ⇒ 它们约占 40%，其余分散在
   十几条判据里。**要指名道姓得先逐块计时**（`outputs/_measure_b8_scaling.py`
   与 `outputs/_measure_cover_cost.py` 记了两次「形状对得上但因果不成立」）。

⚠️ **全量上 `B3` 是最大的一块**（3907 项 / reach）：它的**建表**那半是
   `层内方向数 × 项数` 次 `call_penalty` —— 7813 × 3907 = **30.5M 次**，
   而每次 `reach.penalty` 内部跑一遍 BFS。
   ⇒ 它的**三重循环**那半（`O(L²n)`，全量 **379 亿次**迭代）**已经删掉** ——
   那一段证明上不可能命中，见 `ldv/MEASUREMENTS.md` 结果八；
   **剩下的建表那半**是「插件对同一 payload 反复重算」那个病，位置在**插件里**。

⚠️ 覆盖族（健全性 / `B16` / 覆盖不漏 / 细化量）也调同一个 oracle，但它**已经不是大头**：
   3907 项上整族 **0.69 s**（改前 49.71 s —— 那个 oracle 按**正向**闭包逐项扫，
   且 85% 的调用在**重算**）。读数、根因与「**渐近没修**」那条边界见
   `ldv/MEASUREMENTS.md` 结果七。
   ⚠️ 它的**探针**（`覆盖 oracle（度量…）`那一行）报 `调用 / 命中 / 不同 payload` ——
   只报一个「快了 N 倍」不够：一个「从来没命中」的缓存与一个「全命中」的缓存，
   在**判据的结论**上长得一模一样。

⇒ **关探针是安全的**：它们**不进退出码**，关掉它们**不可能把红变成绿**。
   实测（281 项语料，`outputs/_profile_runone_prod.py` —— 它**包生产 `run_one`**，
   不抄一份序列，所以剖面不会跟生产漂）：

    方向       整趟      其中 `规范重建`   关掉探针后
    ──────────────────────────────────────────────────
    keyset     27.4 s      26.96 s           ~0.4 s
    reach     450.8 s     446.54 s           ~4.3 s
    sequence  ~300 s       ~300 s             ~2 s

⇒ 所以 `--no-probes`：**判据一条不少地全跑，只把贵的探针关掉**，并**印出来**。
   这是「**缩小范围**」与「**全过**」必须分得开的那条纪律在开关上的落地 ——
   关掉的东西**出现在报告里**，不出现在退出码里。

## `--cap` 是另一回事

`--cap N` 把**整个语料**截到前 N 项 —— 它连**判据**一起截。
所以它**会挡住结论**：实测 `openalex-citations` 的环**全部落在第 400 项之后**
（前 400 项 SCC ≥2 的组 = **0**），所以 `--cap 160` 上看不到 `B17` 那条红。
⇒ 要判据有效就别用 `--cap`；要快就用 `--no-probes`。

⚠️ 默认 **0 = 不缩** —— 随仓库那 36 项语料的行为一个字节都不变。
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from .checks._fixtures import (
    build_incremental,
    build_keyset,
    build_reach,
    build_sequence,
    coverage_of,
    equiv_classes,
    find_corpus,
    keyset_queries,
    label_bearing,
    labels,
    load,
    make_builder,
    make_keyset_root,
    make_reach_root,
    make_sequence_root,
    reach_queries,
    reusable_build,
)
from .core.tri import Tri
from .checks._framework import Assertion, Report
from .checks.multilevel import (
    MULTILEVEL_CODES,
    render_levels,
    run_multilevel,
    skip_all,
)
from .checks.cli_paths import (
    CLI_PATH_CODES,
    run_cli_paths,
    skip_all as skip_cli_paths,
)
from .checks.abstraction import (
    VIEW_CODES,
    a1_soundness,
    a2_stable,
    a3_coarsest,
    a4_category,
    a5_ledger,
    a6_roundtrip,
    a7_old_q,
    a7_propagate,
    a7_reading,
    a7_skip,
    a8_maintenance,
    a8_reading,
    a8_fixture_drops,
    a8_skip,
    build_views,
    ledger_entries,
    load_spec_file,
    reading_ctx,
    render_maintenance,
    render_propagation,
    render_views,
    spec_for,
    subtree_of,
    view_profile,
    warranted_of,
)
from .core.views import coarsest_stable_refinement, restrict_spec, view_parts
from .core import view_persist
from .core import selfopt
from . import flow
from . import retriever
from .checks.retrieval import (
    RETRIEVER_CODES,
    run_retrieval,
    skip_all as skip_retrieval,
)
from .checks.coverage import (
    b16_members_covered,
    b18_cover_leak_baseline,
    b19_progress_guard,
    baseline_zones,
    corpus_fingerprint,
    corpus_key,
    cover_leak_profile,
    load_baseline,
    progress_profile,
    render_cover_leak,
    render_progress,
    render_soundness,
    soundness_profile,
    write_baseline,
)
from .checks.divergence import divergence_profile, render_divergence
from .checks.equivalence import absorption_profile, b17_leaf_is_equivalence_class, render_absorption
from .checks.rebuild import rebuild_profile, render_rebuild
from .checks.refinement import (
    b20_semantic_refinement,
    refinement_profile,
    render_refinement,
)
from .checks.contract import (
    b1_no_false_negative,
    b2_merge_covers,
    b3_penalty_comparable,
    b4_split_is_partition,
    b5_decode_covers,
    b6_signal_not_collapsed,
    cover_nesting_profile,
    false_positive_profile,
)
from .checks.semantics import b10_three_states_separable, b11_propensity_complete
from .checks.source import b12_no_global_scalar, b14_exogenous_boundary
from .checks.structure import (
    b7_witness_complete,
    b8_invalidation_local,
    b9_ledger_append_only,
    b13_rank_well_founded,
    b15_reproducible,
)


PLUGIN_CODES = ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11",
                "B13", "B15", "B16", "B17", "B18", "B19", "B20")
KERNEL_CODES = ("B12", "B14")

#: 维护路径的初始批大小 —— 与 `C7` / `C10` 的读数口径一致（先建 6、维护 30）。
MAINT_INIT = 6


def batch_kernel(which: str, nodes, edges):
    """一次建完的内核 + 插件 + 查询集 + 「只建根」的构造器。

    ★ **同一份装配只建一次**（2026-10-11）：本函数对同一个 `which` 会被调 **4 次**
    —— `run_one` 一次，`(视图)` / `(L0)` / `(检索)` 各一次。四次是**同一份装配**
    ⇒ 后三次是纯重复（实测 6 次里 3 次）。

    ⚠️ **不能返回同一个对象**：`run_one` 的判据跑查询会经 `kernel.query` → `expand`
       **就地改内核**。共享 = 让 `(视图)` 拿到一个已经被 `run_one` 展开过的树。
       ⇒ 建一次，每个调用者各拿一份**深拷贝**（`_fixtures.reusable_build`）。
    """
    if which not in ("keyset", "reach", "sequence"):
        raise ValueError(which)

    def _produce() -> tuple:
        if which == "keyset":
            return build_keyset(nodes)
        if which == "reach":
            return build_reach(nodes, edges)
        return build_sequence(nodes, edges)

    kernel, plugin = reusable_build(("batch", which), nodes, edges, _produce)
    if which == "keyset":
        return kernel, plugin, keyset_queries(nodes), make_keyset_root(nodes)
    if which == "reach":
        return (kernel, plugin, reach_queries(nodes, edges), make_reach_root(nodes, edges))
    # 同样用「外生挑的项集合」当 ground truth
    return (kernel, plugin, reach_queries(nodes, edges), make_sequence_root(nodes, edges))


def maintenance_kernel(which: str, nodes, edges):
    """「先建 6、再维护 30」的内核 —— **健全性与覆盖不漏都要两条路径**。"""
    ids_all = sorted(nodes)
    mk_build = make_builder(which, nodes, edges)
    return build_incremental(mk_build, nodes, ids_all[:MAINT_INIT], ids_all[MAINT_INIT:])


#: `cli --holdout` 的默认值 —— 第三条路径的切分点。
CLI_HOLDOUT = 12


def cli_split_kernel(which: str, nodes, edges, holdout: int = CLI_HOLDOUT,
                     remove: int = CLI_HOLDOUT):
    """**`cli` 实际用的那条装配** —— 直接调 `cli._assemble`，**不复制**。

    ## 为什么要有第三条路径（2026-10-08）

    `run_checks` 原来两条路径，而 `cli` 用的是**第三条**：

        路径            批/维护比例      谁在用
        ──────────────────────────────────────────────────────────────
        batch           一次建完         `run_checks` 的 `B1` / `B16` 批建那半
        maintenance     **6 : n−6**      `run_checks` 的维护那半（`MAINT_INIT`）
        **cli 切分**     **n−12 : 12**    `cli all`（`--holdout` 默认 12）

    三条**互不相同**，而「跑了哪条」在汇总里长得**一模一样** ⇒
    「`run_checks` 全绿」**不蕴含**「`cli` 绿」。实测（`outputs/_findings_real_data.md`
    问题 1 / 8）：`cli all --remove 12` 在真实语料上退出码 1 的时候，
    `run_checks` 是**绿的**。

    ## ★ 为什么是**调**而不是**抄**

    抄一份装配（`build(root)` + `insert` 每一项 + 根 payload 按 `init_items` 算）
    会与 `cli` **各自演化** —— 而两处「长得一样」的代码里，**只有一处**会被改。
    这正是本仓库记过的形状：`reach` 的根口径在 `_fixtures.make_builder` 与
    `cli._assemble` 两处**不一致**，而症状不是报错，是**检查报绿**。

    ⇒ 所以这里直接调 `cli._assemble`。它改了，这一条**跟着改**，不会漏。

    ⚠️ **`remove`** 只影响 `_assemble` **挑出**哪几项待删（返回值的第 4 项）——
       本函数**不删**。删除那一段由 `run_one` 在**所有用到 `cli_k` 的判据跑完之后**
       才做（`remove_items` 会就地改内核）。
       「`run_checks` 绿」覆盖的是 `cli all`（默认不删），**不**覆盖 `--remove N`；
       这一点在 `run_one` 的读数里**显式印出来**（收窄要印、放宽也要印）。
    """
    from .cli import _assemble
    from .flow import insert_items

    kernel, queries, new, removable, cover = _assemble(
        which, nodes, edges, holdout, remove)
    insert_items(kernel, new)            # ← 维护那 12 项（`cli` 的流程 B）
    return kernel, cover, removable


def leak_obs(which: str, batch_kernel, inc_kernel, cover) -> dict:
    """`B18` 的观测 —— 键是 `<方向>|<路径>`，baseline 与它逐键比。"""
    return {
        f"{which}|batch": cover_leak_profile(batch_kernel, cover),
        f"{which}|maintenance": cover_leak_profile(inc_kernel, cover),
    }


def run_one(which: str, loaded, probes: bool = True) -> Report:
    nodes, edges, dangling = loaded
    cover = coverage_of(which, nodes, edges)
    kernel, plugin, queries, make = batch_kernel(which, nodes, edges)

    rep = Report(plugin=which, expects=PLUGIN_CODES)
    rep.note(f"语料 {len(nodes)} 项；图边 {sum(len(v) for v in edges.values())} 条"
             + (f"；悬挂边 {len(dangling)} 个（{sorted(dangling)[:3]}）" if dangling else ""))

    # ★ **维护路径的内核只建一次，两条判据共用**（`B1` / `B16`）。
    inc = maintenance_kernel(which, nodes, edges)

    # ★★ **第三条路径：`cli` 实际用的那条切分**（2026-10-08）。
    #    直接调 `cli._assemble` ⇒ 它改了这里跟着改，不会两处各自演化。
    #    为什么非有不可：三条路径**互不相同**，而「跑了哪条」在汇总里长得
    #    一模一样 ⇒ 原来「`run_checks` 全绿」**不蕴含**「`cli` 绿」。
    cli_k, cli_cover, cli_removable = cli_split_kernel(which, nodes, edges)

    # ★ `B1` 与 `B16` 都**两条路径各判一次** —— 它们的 ground truth 在两条路径上不同：
    #   `B1` 是「**成员** ∩ 查询」（成员集维护后会变）、`B16` 是「成员 ⊆ 覆盖」
    #   （维护时 payload 已冻结）。**「只跑一条路」与「两条路都跑」在汇总里
    #   长得一模一样** ⇒ `path` 必须印在标题里。
    b1_no_false_negative(kernel, plugin, queries, rep, path="批建")
    b1_no_false_negative(inc, plugin, queries, rep, path="维护")
    b2_merge_covers(plugin, kernel, queries, rep)
    b3_penalty_comparable(kernel, plugin, rep)
    b4_split_is_partition(kernel, rep, path="批建")
    # ★ 第三条：`cli` 切分。⚠️ 这一条**必须与 `cli` 同装配**，所以它用的是
    #   `cli_k`（`cli._assemble` 造出来的那个），不是另建一个「差不多」的。
    b4_split_is_partition(cli_k, rep, path="cli 切分")
    b5_decode_covers(plugin, kernel, queries, rep)
    b6_signal_not_collapsed(kernel, plugin, rep)
    b7_witness_complete(kernel, rep)
    b8_invalidation_local(make, sorted(nodes), rep)
    b9_ledger_append_only(kernel, rep)
    b10_three_states_separable(kernel, plugin, rep)
    b11_propensity_complete(kernel, plugin, rep)
    b13_rank_well_founded(kernel, rep)
    b15_reproducible(make, sorted(nodes), rep)

    # ★ **健全性同样两条路径各判一次**（见 `checks/coverage.py`）。
    #   判据落在哪条路径上，**由那条路径的基线决定**：基线绿 ⇒ 可以当判据；
    #   基线红 ⇒ 只能当度量，或走 baseline 守卫。两条路现在都绿 ⇒ 两条都跑，
    #   **每一行单独判、单独注入验证**。
    b16_members_covered(kernel, cover, rep, path="批建")
    b16_members_covered(inc, cover, rep, path="维护")
    # ★ 第三条：`cli` 切分（同 `B4` 的理由 —— 三条路径互不相同，各占一行）。
    b16_members_covered(cli_k, cli_cover, rep, path="cli 切分")

    # 覆盖不漏（§1 表第四行，⬜ 2026-10-07 已降级）**只在批建路径上是判据**：
    # 维护路径 baseline 里有既存违规（sequence 16 漏 / 2 对）。⚠️ 那**不是**「另一条
    # 机制」—— 实测漏项与滞留项是**同一批**（16 == 16，双向差 0）⇒ 它与「滞留 == 0」
    # 是**同一个条件**，而后者已被出路 (4) 用「代价在划分」换掉 ⇒ 降级为
    # 「度量 + baseline 守卫」。见 §10.2 B 末。
    rep.note(render_soundness(soundness_profile(kernel, cover),
                              soundness_profile(inc, cover), which))
    leak_b, leak_i = cover_leak_profile(kernel, cover), cover_leak_profile(inc, cover)
    rep.note(render_cover_leak(leak_b, leak_i, which))
    # ⚠️ **别在这里再调 `leak_obs`** —— 它会把上面两个 profile 重算一遍。
    #   那正是本仓库在防的形状的一种：多算一遍**看不出**（结果一模一样），
    #   只看得见它慢。`leak_obs` 现在只留给 `--write-cover-leak-baseline` 用。
    b18_cover_leak_baseline({f"{which}|batch": leak_b, f"{which}|maintenance": leak_i},
                            rep, corpus=corpus_fingerprint(nodes, edges))

    # ★ 第三条路径（`cli` 切分）的**覆盖不漏读数** —— **度量**，不进 baseline。
    #   ⚠️ 为什么**不**并进 `b18`：`B18` 的两条规则之一是「baseline 里有、现在没有 ⇒ 红」
    #      （ESLint 的 `--prune-suppressions`）。多一条键会让那条规则拿一份
    #      **没冻过**的 baseline 去比 ⇒ 报「条目不再发生」—— 而真相是**新加的键**。
    #      「新加的键」与「条目失效」长得一模一样，所以这里只印、不比。
    cli_leak = cover_leak_profile(cli_k, cli_cover)
    rep.note(f"覆盖不漏（{which}·cli 切分，**度量**）："
             f"{cli_leak['漏项数']} 漏 / {cli_leak['已展开方向数']} 对"
             f"（装配与 `cli --holdout {CLI_HOLDOUT}` 逐字同一条）")

    # ★ 删除那一段（`cli --remove N`）—— **读数**，不是判据。
    #   ⚠️ `D1–D6` 是 `cli` **退出码**的一部分，但它们不在
    #      `PLUGIN_CODES` / `KERNEL_CODES` / `VIEW_CODES` / `MULTILEVEL_CODES` 里
    #      ⇒ 这一行**不声称**覆盖它们。逐条判据 + 注入在 `run_tests.test_deletion_path`。
    #   ⇒ 「`run_checks` 绿」覆盖 `cli all`（默认不删），**不**覆盖 `--remove N`。
    #     这句话必须印出来 —— 否则「没覆盖」与「覆盖了且是绿的」长得一样。
    #   ⚠️ 必须在**所有用到 `cli_k` 的判据之后**跑：`remove_items` 就地改内核。
    from .flow import remove_items
    _del = remove_items(cli_k, cli_removable, cover=cli_cover)
    _d_bad = sum(len(x.违规) for x in _del)
    rep.note(f"删除路径（{which}·cli 切分，**读数不是判据**）：删 {len(_del)} 项、"
             f"`D1–D6` 违规 {_d_bad} 处"
             + (f" ⇒ 与 `cli all --remove {CLI_HOLDOUT}` 同装配下为 0"
                if not _d_bad else f"：{[x.违规 for x in _del if x.违规][:2]}"))

    # B19 —— 变细守卫。**跨次数**的性质：反复声称能分、却连续 N 次没让覆盖变细。
    # 与 `§K2 判空`（单次性质）不是一回事，不能合并。
    prog = progress_profile(kernel, cover)
    rep.note(render_progress(prog, which))
    b19_progress_guard(prog, rep)

    # B20 —— **语义**变细守卫。`§K2` 第二半的外生判据：内核没有语义，
    # 所以语义**只能从语料的标注来**（`_fixtures.labels`，不调插件）。
    # 与 `B19` 成对：**一条守覆盖，一条守标签**；两条都进退出码，但**判的范围不同**。
    # ⚠️ 判据只在**标签轴**上落（`label_bearing`，由语料决定）：
    #   `reach` / `sequence` 的 payload 与标签无关 ⇒ 只报不判（报「未展开」）。
    labs = labels(nodes)
    # ⚠️ `label_bearing` **先算**（2026-10-13）：不是标签轴 ⇒ 语义增益**没有内容**
    #    ⇒ **不算**。旧代码无条件 `refinement_profile(...)`（openalex × `sequence`
    #    实测 3906 次展开、2569 次空转），再往读数行印对本方向无意义的数字 ——
    #    白烧 CPU，还与 v0.1.18 `judged` 修掉的形状同族。
    # ⚠️ `label_bearing` **只算一次**，读数行与判据共用 —— 否则「读数行说判了」
    #    与「判据说跳过了」有可能分岔，而分岔的两行**各自看着都对**
    #    （`§K4` 单一真相源的同一条道理）。
    _bearing = label_bearing(which, nodes)
    ref = refinement_profile(kernel, labs) if _bearing else None
    rep.note(render_refinement(ref, which, judged=_bearing))
    b20_semantic_refinement(ref, rep, judged=_bearing)

    # ★ 覆盖 oracle 的**代价读数**（度量，不进退出码）。
    #   ⚠️ **只报一个「快了 N 倍」不够**：一个「从来没命中」的缓存与一个「全命中」的
    #   缓存，在**判据的结论**上长得一模一样 —— 那正是本仓库一直在防的形状。
    #   所以两个数都报：去重有没有真的发生、代价落到内存上多少。
    #   为什么这一条重要：覆盖族是本仓库**唯一**随语料超线性的地方（见 `_fixtures`
    #   的「代价的形状」那段），而它贵在**同一个 payload 被反复要**。
    _cs = cover.stats
    rep.note(f"覆盖 oracle（度量，不进退出码）：调用 {_cs['调用']} 次，"
             f"去重命中 {_cs['命中']} 次（不同 payload {_cs['不同 payload']} 个，"
             f"缓存元素 {_cs['缓存元素数']} 个）")

    # B17 —— 「叶 = 不可分等价类」。**外生 oracle**，不调插件（否则共享盲点）。
    # 它同时是 `C8` §7 第 2 步的实测：判空点上有没有可搬的区分信息。
    # ⚠️ 必须把 `which` 传进去：(b) 那一半在 `reach` 上**只报不判**（§7.2）。
    classes = equiv_classes(which, nodes, edges)
    b17_leaf_is_equivalence_class(kernel, classes, rep, which)

    st = kernel.stats()
    by_rank: dict[int, int] = {}
    for d in kernel.all_directions():
        by_rank[d.rank] = by_rank.get(d.rank, 0) + 1
    rep.note(f"结构：{st}")
    rep.note(f"各层方向数 " + "/".join(str(by_rank[r]) for r in sorted(by_rank)))
    # ⚠️ 这里**不报**「空权威层比例」（`方向 − 空权威层`）：§K2 按**字面**
    #    （「否则不建这一层」）实现 ⇒ 被判「分不开」的那一层根本没建出来，
    #    **没有空壳可数**。
    #    代价没有消失，只是**换了地方** —— 从「多出来的空壳」搬到了「叶容量」：
    #    这一层不建 ⇒ 那些项只能留在这个叶里 ⇒ 分辨率下降。
    #    所以这里报两个数：`§K2 判空`（形状与接口贴合度的**信号**）
    #    与 `最大/平均叶容量`（**代价**信号）。两者都进输出、不进退出码。
    rep.note(f"§K2 判空 {st['§K2 判空']} 次（判「这一层不建」）；"
             f"叶容量 最大 {st['最大叶容量']} / 平均 {st['平均叶容量']}"
             f"（{st['叶']} 个叶）")
    rep.note("§K2 判空与叶容量都是**度量**，不是判据（不进退出码）—— "
             "形状越贴合接口，两个数越小")

    # §K8 的另一半：假阳**计量**（不是判据 —— 进输出，不进退出码）
    fp = false_positive_profile(kernel, plugin, queries)
    rep.note(
        f"假阳计量（§K8）：判「是」{fp['判「是」总数']} 次，其中假阳 {fp['其中假阳']} 次，"
        f"假阳率 {fp['假阳率']:.1%}（最差方向 {fp['最差方向']}）"
    )

    # **覆盖嵌套**（`§I4` 带上父方向买到的那个东西）—— 同样只报不判。
    # §1 的硬要求表里**没有**这一条 ⇒ 违反它不破坏正确性（剪枝仍安全），
    # 但它说明「子说的『是』，父接不住」⇒ 遍历白走一趟 ⇒ 纯假阳。
    cn = cover_nesting_profile(kernel, plugin, queries)
    rep.note(
        f"覆盖嵌套（§I4 父方向）：{cn['已展开方向数']} 个已展开方向，"
        f"子覆盖越出父覆盖 {cn['越界次数']} 次"
        + (f"（例：{cn['例子'][0]}）" if cn["例子"] else "（子覆盖 ⊆ 父覆盖）")
        + "（度量，不进退出码）"
    )

    # 「增量 ≡ 全量」—— 第 4 个度量（`C8` §7 第 1 步）。
    # ⚠️ **只报不判**：实测它**不成立**，进退出码会让套件常红、红成噪声。
    #    它是 `C8` §7 第 2 / 3 步的决策依据：哪几个 k 同构、哪几个不同构。
    #
    # ★ 探针可以关（`--no-probes`）。**关掉是安全的**：度量不进退出码，
    #   所以关掉它**不可能把红变成绿** —— 只会让报告少一行读数。
    #   但**必须印出来**：「没跑」与「跑了但没话说」不能长得一样。
    if probes:
        prof = divergence_profile(make_builder(which, nodes, edges), nodes, sorted(nodes))
        rep.note(render_divergence(prof, which))
    else:
        rep.note("增量 ≡ 全量（度量）：**本趟未跑**（`--no-probes`）—— 它是探针，不进退出码")

    # `C8` §7 第 2 步的读数：判空点上**有没有**可搬的区分信息。
    # 实测三个方向都是 0 —— 这不是「还没做」，是**没有东西可搬**（见 equivalence.py）。
    rep.note(render_absorption(absorption_profile(kernel, classes), which))

    # `C8` §7 第 3 步的读数：规范重建**值不值得做**。
    # 结论：同构买得到，但代价是 §K3（接口级不变量）—— 所以**不做**。
    # 与上一条一样：**只报不判**（`B8` 才是那条判据）。
    # ⚠️ 这是**最贵的一条**（O(n) 次重建 × 每次 O(n)）：281 项上 reach 要 446 s，
    #    3907 项上是**小时级**。所以它是 `--no-probes` 关掉的第一条。
    if probes:
        rep.note(render_rebuild(rebuild_profile(which, nodes, edges), which))
    else:
        rep.note("规范重建（度量）：**本趟未跑**（`--no-probes`）—— 它是探针，不进退出码")
    return rep


def cap_corpus(loaded, cap: int):
    """把语料**截到前 `cap` 项**（按 id 升序）—— 见模块开头 `--cap`。

    返回 `(新语料, 被截掉的项数)`。`cap <= 0` 或本来就不够大 ⇒ 原样返回。
    边跟着节点一起收：留下的项之间的边才留。
    """
    nodes, edges, dangling = loaded
    if cap <= 0 or len(nodes) <= cap:
        return loaded, 0
    keep = set(sorted(nodes)[:cap])
    cut = len(nodes) - len(keep)
    new_nodes = {i: n for i, n in nodes.items() if i in keep}
    new_edges = {i: frozenset(d for d in v if d in keep)
                 for i, v in edges.items() if i in keep}
    return (new_nodes, new_edges, dangling), cut


def view_report(loaded, targets: list[str]) -> Report:
    """流程 E 的 `§A1`–`§A7` —— **单独一组**，与插件无关。

    为什么单独一组、且**只跑一次**：

        视图层的输入是「**方向 id**」的集合（`core/views.ViewSpec.universe`），
        而方向 id 是**每个内核各自从 0 开始**的（`D0` 在 keyset 里与在 reach 里
        是**两个不同的方向** —— `_fixtures.Cover` 的 docstring 专门记了这条）。
        ⇒ 外生声明里必须写清它属于哪条方向 ⇒ 判据也就只对那一条方向跑。

    ## 三种「跳过」各有各的话

        文件不在            还没人声明 `P` / `E`
        指纹对不上          声明是**别的语料**上写的
        `方向` 不是这一条    声明是**别的方向**的

    ⚠️ 三条都报「**跳过**」而不是「过」—— 设计稿 §10 停止条件第 1 条：
       `P` / `E` 必须外生，**猜一个就是替人做 `§K9` 的决定**。
       跳过 ≠ 通过：它明说「这一层在本趟**什么都没查**」。
    """
    nodes, edges, _ = loaded
    corpus = corpus_fingerprint(nodes, edges)
    rep = Report(plugin="(视图)", expects=VIEW_CODES)
    # ★ **按语料找**（`spec_paths`：先看语料旁那份，再落回仓内那份）——
    #   不传语料的话，全量语料会读到 36 项那份声明 ⇒ `§A`/`§M`/`§T`
    #   三组**整组跳过**（2026-10-10 全量跑实测：96 条里跳 21 条）。
    doc = load_spec_file(find_corpus(), corpus)

    if not doc:
        _skip_views(rep,
                    "外生项**未声明**（`docs/prior-art/…` 之外的 `ldv/checks/view_spec.json` "
                    "不在）—— `P` / `E` 必须**外生**（设计稿 §3 / §K9 / `B14`），"
                    "猜一个就是替人做决定（§10 停止条件 1）。"
                    "**跳过 ≠ 通过**：这一层本趟什么都没查。")
        return rep

    which = str(doc.get("方向") or "")
    if which not in targets:
        _skip_views(rep,
                    f"外生声明写的是 `{which}`，而本趟跑的是 {targets} "
                    f"⇒ 本方向**判不了**（跳过 ≠ 通过）")
        return rep

    spec, why = spec_for(doc, which, corpus)
    if spec is None:
        _skip_views(rep, why + " —— 判不了，**不是通过**")
        return rep

    # ★ 2026-10-13（v0.1.21）：视图层改跑**维护路径**。
    #   账的两类（滞留 / 根覆盖之外）**只在维护路径上产生** —— 批建路径上两类都是 0/0
    #   （根的声明是「覆盖一切」）⇒ `§A5` 在批建路径上**永远判不了**
    #   （实测 `outputs/_probe_maintenance_tree.out.txt`）。
    #   `plugin` 与路径无关，仍取批建那份（`reusable_build` 有缓存，不会重复建）。
    kernel = maintenance_kernel(which, nodes, edges)
    plugin = batch_kernel(which, nodes, edges)[1]
    cover = coverage_of(which, nodes, edges)
    vs = build_views(kernel, spec, cover, plugin)
    a1_soundness(vs, rep)
    a2_stable(spec, vs.q, rep)
    a3_coarsest(spec, vs.q, rep)

    # ★ `§A4` 的「部件」= `reach(块)` 的一个划分（`view_parts`）。视图划分**会横跨树**，
    #   所以「下层」**不是**「`reach` 里的那些块」—— 那不是嵌套，恒等式根本不成立。
    #   见 `view_parts` 的 docstring 与 `§A4` 那一段注释。
    parts = view_parts(vs.q, lambda d: subtree_of(kernel, d))
    a4_category(parts, reading_ctx(kernel, spec), doc.get("读数") or [], rep)

    a5_ledger(ledger_entries(kernel, spec), vs,
              lambda did, item, kind: warranted_of(kernel, did, item, kind),
              frozenset(kernel.items), rep)

    # ⚠️ `§A6` 要一个**真的落盘**才判得了。这里给一个临时文件 ——
    #   所以生产路径上这条**是跑的**；「没有落盘 ⇒ 跳过」那条路由测试单独验。
    with tempfile.TemporaryDirectory() as _td:
        a6_roundtrip(vs, kernel, cover, plugin, rep, path=Path(_td) / "views.json")

    # ★ `§A7`（`E′` 传播）要一次**结构变动**。这里取一个**确定性的代表**：
    #   把 `spec.universe` 里**最后长出来**的那个方向拿掉 ⇒ 那就是「变动前」的结构，
    #   再把它传播回来。⚠️ 选哪个方向是任意的（只要确定），而判据不依赖这个选择 ——
    #   实测四种选法（单方向 / 最大块 / 全部叶）下「传播稳定」全为真、
    #   「重算 ⊑ Q_ext」全为假（`outputs/_probe_a7b.py`）。
    #
    #  ⚠️ **但「拿掉一个」在 `|U| < 2` 的语料上不存在**（2026-10-11 实测崩过）：
    #     `restrict_spec` 是**拿掉** ⇒ `universe[-1:]` 在只有一个方向的语料上会拿掉
    #     **整个** `universe` ⇒ 抛 `ValueError` ⇒ **整趟崩**（红是判据说话，
    #     崩是判据没了）。⇒ 与 `§A8` 同形的修法：前提不成立就报**跳过**，不构造夹具
    #     （挑法本身收进 `a7_old_q`，与 `test_injections` 共用一份，免得再漂）。
    #     ⚠️ **不要**改成「传空 `old_q` 让 `a7_propagate` 自己报跳过」——那样报出来的
    #     理由是「没有旧视图（那是重算）」，与真正的原因（**这一档语料没有「变动前」**）
    #     不是一回事；两种不同的「判不了」长得一样，就是本仓库一直在防的形状。
    old_q = a7_old_q(spec)
    if old_q is None:
        a7_skip(rep, f"`universe` 只有 {len(spec.universe)} 个方向 ⇒ 拿掉任何一个都会"
                     f"拿光整个 `universe`（`restrict_spec` 是**拿掉**）⇒ 构造不出"
                     f"「变动前」的结构 ⇒ 没有「传播」这回事"
                     f"（§10 停止条件：**跳过 ≠ 通过**）")
    else:
        a7_propagate(old_q, spec, rep)

    # ★ `§A8`（`§A7` §7.4 的**持续**那一半）—— **两个夹具**，两条分支都要跑到：
    #    甲 只拿掉少数几个方向 ⇒ 只传播 shrink ≤ 预算 ⇒ 政策应当**选传播**
    #    乙 拿掉过半方向     ⇒ 只传播 shrink > 预算 ⇒ 政策应当**选重建**
    #  ⚠️ 只配甲的话「重建」那条分支**一次都没被验过**，而它正是这条判据存在的理由。
    #  ⚠️ 夹具的「拿掉几个」是**从实测挑出来的**（`outputs/_probe_recognition_drift.py`
    #     的同族测量）：挑法本身不判，判的是「超预算就必须重建」。
    #
    #  ⚠️ **但「挑法」不许写死 2 / 13**（2026-10-11 实测崩过）：那两个数是在 25 个方向
    #     的语料上挑的，而 `restrict_spec` 是**拿掉** ⇒ `universe[-13:]` 在 `|U| <= 13`
    #     的语料上会拿掉**整个** `universe` ⇒ 抛异常 ⇒ **整趟崩**（红是判据说话，
    #     崩是判据没了）。⇒ 改成按比例缩放，见 `a8_fixture_drops`。
    #     25 个方向上缩放结果仍是 2 与 13 ⇒ **随仓库那份语料的行为逐字不变**。
    drops = a8_fixture_drops(len(spec.universe))
    if drops is None:
        a8_fixtures = None
        a8_skip(rep, f"`universe` 只有 {len(spec.universe)} 个方向 ⇒ 构造不出"
                     f"「甲（拿掉少）/ 乙（拿掉多）」两个**不同**的夹具；"
                     f"只配一个的话「超预算必须重建」那条分支一次都没被验过"
                     f"（§10 停止条件：**跳过 ≠ 通过**）")
    else:
        _light, _heavy = drops
        a8_fixtures = [
            ("甲·传播在预算内", coarsest_stable_refinement(
                restrict_spec(spec, frozenset(spec.universe[-_light:])))),
            ("乙·传播严重退化", coarsest_stable_refinement(
                restrict_spec(spec, frozenset(spec.universe[-_heavy:])))),
        ]
        a8_maintenance(a8_fixtures, spec, rep)

    prof = view_profile(vs)
    rep.note(f"{why}；方向 `{which}`")
    rep.note(render_views(vs, prof, which))
    if old_q is None:
        rep.note("E′ 传播（`§A7`）：**本趟未跑** —— 语料太小，"
                 "构造不出「变动前」的结构（见上面那条跳过）")
    else:
        rep.note(render_propagation(a7_reading(old_q, spec), spec))
    if a8_fixtures is None:
        rep.note("认识的持续维护（`§A8`）：**本趟未跑** —— 语料太小，"
                 "构造不出两个**不同**的夹具（见上面那条跳过）")
    else:
        rep.note(render_maintenance(a8_reading(a8_fixtures, spec), spec))
    return rep


def _skip_views(rep: Report, why: str) -> None:
    """七条一起跳过 —— 用一个函数，免得七条的**理由**各写一遍、写着写着就不一样了。"""
    rep.add("A1", "视图健全性：具体化 ⊇ ∪成员", Tri.UNEXPANDED, why)
    rep.add("A2", "视图稳定：B₁ ⊆ E⁻¹(B₂) 或 B₁ ∩ E⁻¹(B₂) = φ", Tri.UNEXPANDED, why)
    rep.add("A3", "视图最粗：不存在更粗的稳定划分", Tri.UNEXPANDED, why)
    rep.add("A4", "视图类别：distributive 的 `G` / holistic 的见证都要实测成立",
            Tri.UNEXPANDED, why)
    rep.add("A5", "视图账：每条都要指得到具体方向 + 项，且独立重算下成立",
            Tri.UNEXPANDED, why)
    rep.add("A6", "视图落盘-读回：权威边逐字相同、存档不带派生边、读回后 §A1 仍成立",
            Tri.UNEXPANDED, why)
    rep.add("A7", "视图传播 E′：传播后仍稳定，且只许细分（不许用重算冒充）",
            Tri.UNEXPANDED, why)


def multilevel_report(loaded, targets: list[str]) -> Report:
    """`§M0`–`§M6` —— 视图层（L0）的**声明** + 「若继续折」的**契约**，**又一组**。

    ## 为什么单列一组（而不是并进 `(视图)`）

    两组**问的不是同一件事**：`(视图)` 问「这一层**成不成立**」（健全 / 稳定 /
    最粗 / 类别 / 账 / 落盘 / 传播）；`(L0)` 问「这一层的**声明**守不守得住
    （`E` 无自环、`Q₀ == csr(spec)`），以及**若继续折**时刹车与账成不成立」。
    并成一组的话，「七条视图判据全过」这句话会**顺手覆盖**这七条 ——
    而它们对**健全性**一个字节的信息都没有。

    ⚠️ **组名不叫「多层」**（2026-10-08 改）：本设计的产物是**第 0 层**，
       「一层就够」（实测**真折 0 层**，见 `MEASUREMENTS.md` 结果二十）。
       叫「多层」会让「这东西做出来干什么用的」在输出里**读不出来**。

    ⚠️ 外生输入与 `(视图)` **同一份**（`view_spec.json`）⇒ 三种「跳过」的话也一样。
       但**跳过的范围不同**：这一组跳的是 `M0`–`M6`。
    """
    nodes, edges, _ = loaded
    corpus = corpus_fingerprint(nodes, edges)
    rep = Report(plugin="(L0)", expects=MULTILEVEL_CODES)
    # ★ **按语料找**（`spec_paths`：先看语料旁那份，再落回仓内那份）——
    #   不传语料的话，全量语料会读到 36 项那份声明 ⇒ `§A`/`§M`/`§T`
    #   三组**整组跳过**（2026-10-10 全量跑实测：96 条里跳 21 条）。
    doc = load_spec_file(find_corpus(), corpus)

    if not doc:
        skip_all(rep, "外生项**未声明**（`view_spec.json` 不在）—— "
                      "`P` / `E` 必须**外生**（设计稿 §3 / §K9），"
                      "猜一个就是替人做决定（§10 停止条件 1）。**跳过 ≠ 通过**。")
        return rep

    which = str(doc.get("方向") or "")
    if which not in targets:
        skip_all(rep, f"外生声明写的是 `{which}`，而本趟跑的是 {targets} "
                      f"⇒ 本方向**判不了**（跳过 ≠ 通过）")
        return rep

    spec, why = spec_for(doc, which, corpus)
    if spec is None:
        skip_all(rep, why + " —— 判不了，**不是通过**")
        return rep

    # ★ 2026-10-13（v0.1.21）：与 `view_report` **同源** —— 声明的 `universe` 是
    #   **维护路径**那棵树编的号（实测 `sequence`：批建 16 / 维护 8）⇒ 这一层
    #   拿 `q = csr(spec)` 去问内核时，必须问**同一棵树**，否则 `D0` 在两边
    #   指的不是同一个方向（`_fixtures.Cover` 的 docstring 专门记了这条）。
    #   `plugin` 与路径无关，仍取批建那份（`reusable_build` 有缓存，不重复建）。
    kernel = maintenance_kernel(which, nodes, edges)
    plugin = batch_kernel(which, nodes, edges)[1]
    cover = coverage_of(which, nodes, edges)
    levels = run_multilevel(spec, kernel, cover, plugin, rep)
    rep.note(why + f"；方向 `{which}`")
    # ⚠️ 这一句必须印出来：**产物是什么**。只印层数的话，
    #    「这东西做出来干什么用的」在输出里读不出来 —— 而那正是这一层
    #    后引入的原因（`docs/分层方向视图-抽象层.md` §0.0）。
    rep.note("★ 本组**不叫「多层」**，也**不是抽象层的产物**（2026-10-09 定义更正）："
             "抽象层**只对检索器负责** —— 把**结构层**看成一个视图，从它提取一份"
             "**认识**交给**下一层的检索器**。⚠️ **多层是结构层自己就有的**（层 = 秩）"
             "⇒ 折叠是**一条被检验过、判定了不产生第二层的支线**（实测真折 0 层），"
             "价值是**关掉一个选项**。七条按守谁分两拨：`§M0`/`§M1`/`§M4` 守 L0 的"
             "**声明**（`§M1` = `Q₀ == csr(spec)`；`§M4` 2026-10-10 起是**读数**），"
             "`§M2`/`§M3`/`§M5`/`§M6` 守**若继续折**的契约。")
    rep.note(render_levels(levels))
    # ⚠️ 这两句必须印出来：
    #   ① **真折层数** —— `len(levels) == 2` 同时对应「折了一层」与「一层没折」，
    #      只印总层数会让两者**长得一模一样**（本项目的中心反模式）。见 `n_folds`。
    #   ② 第 1 层的商**不缩**（`|Q| == |U|`）⇒ 折叠在此终止。那正是前作核验 §四 (1)
    #      说的「收缩比在 ldv 里**不受任何结构保证**」在真数据上的样子，
    #      而不是「实现坏了」。读数与判据分开：`§M1` **不判收缩比**（它判
    #      `Q₀ == csr`），收缩比只是 `render_levels` 里逐层印出来的读数。
    if len(levels) == 2 and levels[-1].n_blocks == len(levels[-1].spec.universe):
        rep.note("★ 读数：第 1 层的商**不缩**（`|Q| == |U|`）⇒ 折叠在此终止 —— "
                 "**真折 0 层**。这不是红：**一层就够**（膨胀已被第 0 层收住，"
                 "方向数涨 5.7× 而视图数只涨 1.6×，实测见 `MEASUREMENTS.md` 结果二十）。"
                 "要让它**再**折下去，没有**边界安全**的杠杆 —— 同一份结果里那张杠杆表。")
    return rep


def cli_report(loaded, targets: list[str]) -> Report:
    """`cli` 的**四条入口**与两个非默认参数 —— 流程 C 的第三条覆盖缺口。

    ## 为什么单列一组

    `run_checks` 的三条**装配**都是它自己造的（一次建完 / 先建 6 维护 n−6 /
    `cli` 切分）；而 `cli` 的**四条入口**（`only=` 参数）与 `--holdout N` /
    `--remove N` 的**非默认值**从来没有被跑过。
    ⇒ 「`run_checks` 全绿」**不蕴含**「`cli` 绿」，而这件事的症状不是红，是
    **检查报绿** —— 实测过一次：`cli._assemble` 的 reach 根口径与对照实现分岔，
    `run_checks` 全绿而 `cli all --remove 12` 退出码 1（`outputs/_findings_real_data.md` 缺陷 1）。

    ⚠️ 与 `cli_split_kernel` 同一条纪律：**直接调 `cli.run_one`，不复制**。

    ## ⚠️ 三条断言的是「**必须红**」

    `cli` 的**空转护栏**是它退出码的一部分。所以

        `cli d` 单独跑 / `cli b --holdout 0` / `cli r` 不给 `--remove`

    的**正确答案就是红**（空转不许与通过长得一样）。断言它们**开火了**。
    写成「必须绿」的话这一组会**永远绿** —— 护栏拆掉也绿。
    """
    rep = Report(plugin="(cli)", expects=CLI_PATH_CODES)
    if loaded is None:
        skip_cli_paths(rep, "没有语料 ⇒ 四条流程都跑不起来（跳过 ≠ 通过）")
        return rep
    run_cli_paths(loaded, targets, rep)
    rep.note("本组**直接调** `cli.run_one`（不复制）—— 与 `cli_split_kernel` 同一条纪律；"
             f"方向 {list(targets)}，`--holdout` ∈ {{12, 0, 7}}，`--remove` ∈ {{0, 5}}。")
    rep.note("⚠️ `CL3` / `CL4` / `CL5` 的**已知答案是红**（空转护栏必须开火）—— "
             "断言「它红了」才证明护栏在；断言「它绿了」会永远绿。")
    return rep


class _Collector:
    """逐查询的**临时收集器** —— **故意不是 `Report`**。

    ## 为什么必须是另一个类型（2026-10-13 实测踩到）

    `run_tests._assert_state_counts` 把 `Report.add` **挂掉**，用来数
    「每条编号在**生产配置**上判过没有」（`test_never_judged` 与
    `test_skip_evidence_table` 都吃这个数）。⇒ 若逐查询的中间结果也走 `Report`，
    那 14 份中间报告会被**当成生产读数**数进去：

        实测：`T1` / `T3` / `T5` / `T6` / `T7` 被 `空集` 那条查询带出「跳过」
        ⇒ `test_skip_evidence_table` 报「本趟跳过的编号缺 5 个」，
          而 `run_checks` 自己印的是「跳过 3 次 / 2 个编号」—— **两处打架**。

    ⇒ **生产结果只有聚合后的那 8 条**。中间结果不进 `Report`，用这个最小替身
      （`run_retrieval` 只用到 `add` / `note` / `assertions` 三样）。
    """

    def __init__(self) -> None:
        self.assertions: list[Assertion] = []
        self.notes: list[str] = []

    def add(self, code: str, title: str, result: Tri, detail: str = "") -> Assertion:
        a = Assertion(code=code, title=title, result=result, detail=detail)
        self.assertions.append(a)
        return a

    def note(self, text: str) -> None:
        self.notes.append(text)


def retrieval_report(loaded, targets: list[str]) -> Report:
    """`T1`–`T3` —— **检索器层（流程 T）**，**又一组**。

    ## 为什么单列一组（而不是并进 `(视图)` / `(L0)`）

    它问的**不是**「这一层成不成立」（那是 `(视图)` 问的），也不是
    「折叠这条支线的承诺守不守得住」（那是 `(L0)` 问的），而是

        **拿那份认识去检索** —— 会不会**漏**、会不会用到**陈旧的**、解释**指不指得到**。

    并成一组的话，「视图七条全过」会顺手覆盖这三条 —— 而它们对**检索的顺序**
    一个字节的信息都没有。

    ## 外生输入：两份，来源不同

        `view_spec.json`   `P` / `E` —— 与 `(视图)` **同一份** ⇒ 三种「跳过」的话也一样
        `views.json`       **盘上的认识** —— 默认语料上**没有**这份落盘件

    ⚠️ 后者本趟是**现造的**（`view_persist.to_dict`，拿当前结构当场写一份）。
       所以 `T2` 这一趟**只有守卫作用**：结构没动、认识没陈旧 ⇒ 它恒绿。
       它的红形态（**结构动了、认识没跟着变**）只能靠**注入**（`§C3`）——
       那条对照在 `run_tests.test_retriever`。

    ⚠️ 「现造」这件事**必须印出来**：不印的话，「`T2` 绿」读起来像
       「陈旧的识别被挡住了」，而实际上根本**没有陈旧的可能**。

    ## ★★ 本组守的是「**不许漏**」—— 而「顺序」自 2026-10-14 起**真的有了后果**

    `步骤 T3` 现在**跳过「后看」的块**（`retriever.run_in_order(skip=...)`），
    依据是 `§I2` 的推论（**合并说否 ⇒ 每个成员都说否**）
    ⇒ `scanned` 从「全部块」缩到「先看的块」= **少扫**（读数，由 `render_reading` 印）。
    ⚠️ 省的是**重复判定**，**不是结论** ⇒ 这一组守的仍然是
      「**拿那份认识去检索，不会因为它而漏东西**」—— `T1` 拿**全扫**当基准。
      ★ 而**那才是要紧的**：允许省的只是「插件已经说过『否』」的块。
    ★★ **口径（`基线§14.6`，2026-10-14 改）：收益 = 影响**，本层认**两种**：
      ① **判断质量**（块画像 / 建议 / 读法 / 粒度）② **少扫**（上面那一行）。
      ⚠️ 两者都**不是**「省时间」意义上的收益。见 `基线§13 13.0`。
    """
    nodes, edges, _ = loaded
    corpus = corpus_fingerprint(nodes, edges)
    rep = Report(plugin="(检索)", expects=RETRIEVER_CODES)
    # ★ **按语料找**（`spec_paths`：先看语料旁那份，再落回仓内那份）——
    #   不传语料的话，全量语料会读到 36 项那份声明 ⇒ `§A`/`§M`/`§T`
    #   三组**整组跳过**（2026-10-10 全量跑实测：96 条里跳 21 条）。
    doc = load_spec_file(find_corpus(), corpus)

    if not doc:
        skip_retrieval(rep, "外生项**未声明**（`view_spec.json` 不在）—— "
                            "`P` / `E` 必须**外生**（设计稿 §3 / §K9），"
                            "猜一个就是替人做决定（§10 停止条件 1）。**跳过 ≠ 通过**。")
        return rep

    which = str(doc.get("方向") or "")
    if which not in targets:
        skip_retrieval(rep, f"外生声明写的是 `{which}`，而本趟跑的是 {targets} "
                            f"⇒ 本方向**判不了**（跳过 ≠ 通过）")
        return rep

    spec, why = spec_for(doc, which, corpus)
    if spec is None:
        skip_retrieval(rep, why + " —— 判不了，**不是通过**")
        return rep

    # ★ 2026-10-13（v0.1.21）：同 `view_report` / `multilevel_report` ——
    #   声明是**维护路径**那棵树；而且**盘上那份认识**（`python -m ldv.recognize`
    #   产出）本来就是维护路径建的 ⇒ 这一层必须用**同一棵树**，否则候选会落在
    #   `spec.universe` **之外**（实测：`sequence` 下候选出现 `D8`，而声明只到 `D7`）。
    #   `queries` 是**语料级**的（与内核路径无关），仍取批建那份。
    kernel = maintenance_kernel(which, nodes, edges)
    plugin = batch_kernel(which, nodes, edges)[1]
    queries = batch_kernel(which, nodes, edges)[2]
    if not queries:
        skip_retrieval(rep, "这一条方向**没有查询集** ⇒ 没有需求可分派（跳过 ≠ 通过）")
        return rep

    cover = coverage_of(which, nodes, edges)

    # ★★ **读盘上那份认识**（`§7.4` / `§A8`）—— 这是「抽象层持续影响检索层」的**落点**。
    #
    #   在此之前这里**每次都现造**一份（`to_dict(build_views(...))`）⇒
    #   `T2`（「用到的认识的指纹 == 当前结构的指纹」）**只有一半在判**：
    #   现造的那份**按构造**就是当前的 ⇒ 它永远不会不符。
    #
    #   ⇒ 改成：**有盘上那份就用它**（`T2` 这才真的有东西可判）；
    #     没有才现造，并**印明是现造的**（「现造」与「读到盘上那份」不许共用一行
    #     —— 否则读者会以为 `T2` 绿是「陈旧被挡住了」，其实根本没有陈旧的可能）。
    #
    #   ⚠️ 盘上那份由 **`python -m ldv.recognize`** 产生（那是它的唯一生产入口）。
    disk_path = view_persist.default_path(find_corpus())
    disk_on_file = view_persist.read(disk_path)
    bad_why = ""
    from_disk = bool(disk_on_file)
    # ★★ **盘上那份读不回来时不许崩**（2026-10-10 实测踩到）：
    #    造一份陈旧的 `views.json`（`spec` 与当前结构不自洽）⇒ `from_dict` 抛
    #    ⇒ **`(检索)` 组整个消失、`run_checks` 当场崩**（比红更糟：红是判据说话，崩是判据没了）。
    # ⇒ 处置：读不回来 ⇒ **照旧重算**（结果仍对），但**把这件事记在 `Recognition` 上**，
    #    由 `T2` 判红（「坏档/陈旧必须看得见」）。
    try:
        recognition = view_persist.from_dict(
            disk_on_file, kernel, cover, plugin) if from_disk else None
        bad_disk = False
    except Exception as exc:  # noqa: BLE001 —— 坏档要**报出来**，不是崩
        recognition, bad_disk = None, True
        bad_why = f"{type(exc).__name__}: {exc}"
    # ★★ **盘上那份自己的指纹** —— 显式算出来（`None` = 盘上**没有**那份）。
    #    不显式给的话，`current_recognition` 只能从**传进去的对象**推断，
    #    而「读盘」与「现造」传的是同一类型的对象 ⇒ 它分不出来 ⇒
    #    实测：盘上没有 `views.json`，而 `T2` 报「盘上那份的指纹 … == 当前（来源：盘上）」
    #    —— **判据编造了一个来源**，且比「跳过」更坏（跳过是「没得比」，假绿是「比过了、而且对」）。
    disk_fp = (retriever.spec_fingerprint(recognition.spec)
               if (from_disk and recognition is not None) else None)
    if recognition is None:
        disk = view_persist.to_dict(build_views(kernel, spec, cover, plugin))
        recognition = view_persist.from_dict(disk, kernel, cover, plugin)
        disk_fp = disk_fp if bad_disk else None

    # ★ 「按使用细调」（`基线§14.7`）要**使用记录**才有内容 —— 而流程 A 本来就会记
    #   （`R5a` 展示 + `R5b` 逐条 `record_usage`）。⇒ 先跑几条查询攒记录，
    #   否则 `T5` 在真装配上**永远跳过** ⇒ 那条判据等于没有。
    #   ⚠️ 这是**流程 A 的正常动作**，不是为判据造数据（`§T0` 第 5 条：数要现算）。
    for q0 in queries[:3]:
        flow.run_query(kernel, q0)
    tendency = selfopt.tendency_by_direction(kernel)

    # ★★ 2026-10-13（v0.1.21）：跑**整个查询集**，逐编号取**最坏结果**。
    #
    #   原来只跑 `queries[0]` —— 那是 14 条里**最小**的一条（`随机 1 项 #0`）。
    #   实测（`outputs/_probe_t7_queries.out.txt`）：`T7` 只在**候选落进
    #   「值得往下 / 看具体方向」那一档**时才判，而 **14 条里 11 条**能让它判、
    #   `queries[0]` 恰好不能 ⇒ **`T7` 在生产上永远跳过**。
    #   ⚠️ 那是**覆盖不足**，不是「这条判据判不了」：一条「永远跳过」的判据
    #   与「没有这条判据」在效果上一样，而它在输出里长得像「守住了」
    #   （`test_never_judged` 追讨的正是这件事）。
    #
    #   聚合法 = **最坏结果**：任一查询红 ⇒ 红；否则任一判 ⇒ 判；否则跳过。
    #   ⚠️ **不许**取「第一条」或「最后一条」：那会把「14 条里 1 条红」藏起来，
    #      而「全绿」的范围必须与**声明的**范围（本趟 = 整个查询集）恰好相等。
    #
    #   ⚠️ **每条查询一棵新树**：`deepen` 会 `expand`（`§14.9` 的唯一写动作）就地改内核
    #      ⇒ 共用一棵树的话，第 k 条查询看到的是被前几条**改过**的结构，
    #      结果**与查询顺序有关**。⇒ 每条各建一棵，判据之间互不影响。
    #   ⚠️ 认识与倾向也跟着**每条各一份**（`from_dict` 绑内核；倾向来自内核上的使用记录）。

    def _recognition_for(k: Any) -> tuple[Any, Any, bool]:
        """给一个内核配一份认识 —— 与上面那份**同源**（同一批盘上字节）。"""
        if not from_disk:
            d = view_persist.to_dict(build_views(k, spec, cover, plugin))
            return view_persist.from_dict(d, k, cover, plugin), None, False
        try:
            rec = view_persist.from_dict(disk_on_file, k, cover, plugin)
        except Exception:  # noqa: BLE001 —— 坏档要**报出来**，不是崩
            return None, None, True
        return rec, retriever.spec_fingerprint(rec.spec), False

    runs: list[tuple[Any, _Collector]] = []
    for i, q in enumerate(queries):
        if i == 0:
            # 第 1 条沿用上面那套（`kernel` / `recognition` / `disk_fp` / `tendency`）
            k_q, rec_q, fp_q, bad_q, tend_q = (
                kernel, recognition, disk_fp, bad_disk, tendency)
        else:
            k_q = maintenance_kernel(which, nodes, edges)     # ★ 每条一棵新树
            rec_q, fp_q, bad_q = _recognition_for(k_q)
            if rec_q is None and not bad_q:
                d = view_persist.to_dict(build_views(k_q, spec, cover, plugin))
                rec_q = view_persist.from_dict(d, k_q, cover, plugin)
            for q0 in queries[:3]:
                flow.run_query(k_q, q0)
            tend_q = selfopt.tendency_by_direction(k_q)
        sub = _Collector()
        r_q = run_retrieval(k_q, plugin, {which: retriever.as_bar(q)}, spec, sub,
                            recognition=rec_q, tendency=tend_q,
                            disk_fingerprint=fp_q, disk_unreadable=bad_q)
        runs.append((r_q, sub))

    # 逐编号取**最坏结果**；`detail` 取**定结果的那一条**查询（不许张冠李戴）。
    # ⚠️ **`YES` 比 `UNEXPANDED` 好** —— 「跳过」是**那条查询没有需求**
    #   （如 `空集` 查询：`ideal` / `require` / `forbid` 三样都没有 ⇒ 无可判），
    #   不是「这条判据判不了」。⇒ 让跳过压过过，会把**14 条里 13 条判了的判据**
    #   读成「没判」—— 那是把「覆盖不足」这个错法**换个方向再犯一次**。
    #   ⚠️ 但**也不许**反过来把「跳过」当「过」：全趟都跳过的编号仍然是跳过。
    _rank = {Tri.UNEXPANDED: 0, Tri.YES: 1, Tri.NO: 2}
    for code in RETRIEVER_CODES:
        pick: tuple[int, Any] | None = None
        n_judged = 0
        for i, (_rq, sub) in enumerate(runs):
            a = next((x for x in sub.assertions if x.code == code), None)
            if a is None:
                continue
            if a.result is not Tri.UNEXPANDED:
                n_judged += 1
            if pick is None or _rank[a.result] > _rank[pick[1].result]:
                pick = (i, a)
        if pick is None:
            continue
        i, a = pick
        detail = a.detail if len(runs) == 1 else (
            f"{a.detail}（**定这条结果的是第 {i + 1} 条查询**：`{queries[i].label}`；"
            f"本组共跑 {len(runs)} 条，其中 **{n_judged} 条判了**这条，"
            f"逐编号取**最坏结果**）")
        rep.add(code, a.title, a.result, detail)

    # 注记取**第 1 条查询**那一份（读数与它同源），另加一条聚合口径。
    r = runs[0][0]
    for t in runs[0][1].notes:
        rep.note(t)
    rec = r.recognition
    rep.note(f"{why}；方向 `{which}`；需求取自**整个查询集**（{len(queries)} 条："
             f"`{queries[0].label}` … `{queries[-1].label}`）"
             f"—— 逐编号取**最坏结果**（任一红 ⇒ 红；否则任一判 ⇒ 判；否则跳过）")
    # ⚠️ **四种状态各印一行，不许共用**（`§K6` 的同一条纪律）：
    #    盘上那份**当前** / 盘上那份**陈旧** / 盘上那份**读不回来** / 盘上**没有**
    if not from_disk:
        rep.note("⚠️ 盘上没有 `views.json` ⇒ 认识是**现造的**"
                 "（由 `python -m ldv.recognize` 产生）—— 它拿的就是**当前结构**"
                 "⇒ **「持续影响」这一趟不在**，`T2` 报**跳过**（跳过 ≠ 通过）")
    elif not rec.disk_readable:
        rep.note(f"⚠️ 盘上有 `{disk_path.name}`，但它**读不回来**"
                 f"（{bad_why}）⇒ 本趟按**重算**处理（结果仍对），`T2` 报**红**")
    elif rec.disk_fingerprint != retriever.spec_fingerprint(spec):
        rep.note(f"⚠️ 盘上的 `{disk_path.name}` 是**陈旧的**"
                 f"（指纹 {rec.disk_fingerprint} ≠ 当前 {retriever.spec_fingerprint(spec)}）"
                 f"⇒ 本趟按**重算**处理（结果仍对），`T2` 报**红** —— "
                 f"修法：`python -m ldv.recognize`")
    else:
        rep.note(f"盘上的认识：`{disk_path.name}`（{len(disk_on_file.get('q') or [])} 块）——"
                 f"**本趟读的就是它** ⇒ `T2` 这一趟**真的有东西可判**"
                 f"（陈旧的可能**真实存在**，不是守卫）")
    return rep


def _skip_evidence() -> list[tuple[str, str, bool]]:
    """**跳过项的覆盖证据** —— 每条「跳过」配一份**合成对照**：判据在该红时会红。

    ⚠️ 这**不是本趟的读数** —— 本趟那条方向上没有内容（那正是「跳过」）。
       它证明的是**另一件事**：这条判据**会不会红**。
       ⇒ 「没内容」与「不会红」**不许共用一行**（`跳过 ≠ 通过` 的反面）。

    ⚠️ 权威的闸门在 `run_tests`（对照不符 ⇒ 测试红）。这里**只印不判** ——
       与「注册表不符」同一档，**不进退出码**（退出码只看判据的红）。

    ⚠️ **对照崩了要接住**：它跑在 `run_checks` 里，崩了会把整趟输出带走 ——
       而**崩比红更糟**（红是判据说话，崩是判据没了）⇒ 这里逐条 `try`，
       崩了**报成「对照不符」**（可见），不静默、也不带走整趟。
    """
    from .checks.abstraction import a5_known_answer, a5_out_of_scope_known_answer
    from .checks.coverage import b18_known_answer
    from .checks.equivalence import b17_known_answer
    from .checks.refinement import b20_known_answer

    def fails_of(fn: Any) -> list[str]:
        try:
            return list(fn())
        except Exception as exc:  # noqa: BLE001 - 见 docstring
            return [f"对照自身崩了：{exc!r}"]

    def a5_ok() -> bool:
        """`§A5` 的账有**两类**，**两类各一条对照** —— 缺一条就会重犯那个错。

        ⚠️ ★ 2026-10-12：原来**只有滞留类**一条。而「根覆盖之外」那一类的 oracle
           恒判 False（见 `warranted_of`），**只验一类**正是它一直没露的原因。
        ⚠️ ★ 同一趟：**两类必须真的是两个函数**。把第二类别名成第一条
           （`a5_out_of_scope_known_answer = a5_known_answer`）**等价于「只验一类」**，
           而四条断言**照样全过** ⇒ 必须显式钉身份，否则这条对照能被悄悄废掉。
        """
        try:
            return (a5_known_answer(False) is Tri.YES
                    and a5_known_answer(True) is Tri.NO
                    and a5_out_of_scope_known_answer is not a5_known_answer
                    and a5_out_of_scope_known_answer(False) is Tri.YES
                    and a5_out_of_scope_known_answer(True) is Tri.NO)
        except Exception:  # noqa: BLE001 - 见 docstring
            return False

    return [
        ("A5", "合成账**两类各一条**（滞留 / 根覆盖之外；两态：成立 ⇒ 过 / 不成立 ⇒ 红）",
         a5_ok()),
        ("B17", "合成有环图（**三态**：过 / 红 / 跳过）",
         fails_of(b17_known_answer) == []),
        ("B18", "合成 baseline 分区（**三态**：过 / 红 / 跳过）",
         fails_of(b18_known_answer) == []),
        ("B20", "合成标签轴（**三态**：过 / 红 / 跳过）",
         fails_of(b20_known_answer) == []),
    ]


def main(argv: list[str]) -> int:
    # ⚠️ `--cap 400` 里的 `400` **不是**方向名 —— 所以先摘掉带值的选项再取位置参数。
    cap = 0
    probes = True
    args: list[str] = []
    i = 1
    while i < len(argv):
        if argv[i] == "--cap":
            cap = int(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--no-probes":
            probes = False
            i += 1
            continue
        args.append(argv[i])
        i += 1
    which = [a for a in args if not a.startswith("-")]
    targets = which or ["keyset", "reach", "sequence"]

    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 全部检查**未展开**（跳过），这不是通过。")
        print("  设 LDV_CORPUS 或把语料放到 ldv/corpus/nodes/")
        return 0

    loaded, cut = cap_corpus(loaded, cap)
    if cut:
        print(f"⚠ `--cap {cap}`：语料 **{cut + cap} 项 ⇒ 本趟只用前 {cap} 项**，"
              f"其余 {cut} 项**本趟什么也没说**。")
        print("   ⚠️ `--cap` **连判据一起截** —— 它会挡住结论。实测 `openalex-citations`")
        print("      的环全在第 400 项之后 ⇒ 前 400 项上看不到 `B17` 那条红。")
        print("      要判据有效就别用 `--cap`；要快用 `--no-probes`。")
        print()

    if not probes:
        print("⚠ `--no-probes`：**贵的那几条探针本趟未跑**（`增量≡全量` / `规范重建`）。")
        print("   为什么关：`规范重建` 是 O(n) 次重建 × 每次 O(n)，281 项上 reach 要 446 s，")
        print("   3907 项上是小时级 —— 而它**只报不判**，不进退出码。")
        print("   ⇒ **关它不可能把红变成绿**：判据（B1–B20）一条不少地全跑。")
        print("   报告里会写明哪几行未跑 —— 「没跑」与「跑了但没话说」不共用一行。")
        print()

    if "--write-cover-leak-baseline" in args:
        nodes, edges, _ = loaded
        entries: dict[str, dict] = {}
        for w in targets:
            cover = coverage_of(w, nodes, edges)
            entries.update(leak_obs(w, batch_kernel(w, nodes, edges)[0],
                                    maintenance_kernel(w, nodes, edges), cover))
        entries = {k: {"漏项数": v["漏项数"], "漏的对数": v["漏的对数"]}
                   for k, v in sorted(entries.items())}
        write_baseline(entries, corpus=corpus_fingerprint(nodes, edges))
        print("已冻结覆盖不漏基线（**合并**进本语料那一区，别的语料不动）：")
        print(f"  语料指纹 {corpus_fingerprint(nodes, edges)}"
              f" ⇒ 分区 {corpus_key(corpus_fingerprint(nodes, edges))!r}")
        for k, v in entries.items():
            print(f"  {k:24s} {v['漏项数']} 漏 / {v['漏的对数']} 对")
        zones = baseline_zones(load_baseline())
        print(f"  文件里现在有 {len(zones)} 个语料分区：{sorted(zones)}")
        return 0

    reps: list[Report] = []
    for w in targets:
        rep = run_one(w, loaded, probes=probes)
        reps.append(rep)
        print(rep.render())
        print()

    # B12 / B14 是**内核侧**的，与插件无关 —— 只跑一次
    # ★ `B12` 的扫描范围**挂上本层**（`§C2` 的默认值，Q5 拍「E-1 延伸到本层」）：
    #   不挂的话，`retriever.py` 里加一个全局评分**没有任何东西会红** ⇒
    #   「E-1 延伸到本层」在系统层面就是一句空话。`extra_sources` 的签名本来就有。
    kernel_rep = Report(plugin="(内核)", expects=KERNEL_CODES)
    b12_no_global_scalar(kernel_rep, extra_sources=[Path(retriever.__file__)])
    b14_exogenous_boundary(kernel_rep)
    reps.append(kernel_rep)
    print(kernel_rep.render())
    print()

    # ★ 流程 E 的 `§A1`–`§A7` —— **视图侧**，同样与插件无关，只跑一次。
    #   它的外生输入（`P` / `E`）必须由人声明；没声明就三条都**跳过**并印原因
    #   （设计稿 §10 停止条件 1）。见 `view_report` 的 docstring。
    view_rep = view_report(loaded, targets)
    reps.append(view_rep)
    print(view_rep.render())
    print()

    # ★ `§M0`–`§M6` —— **又一组**，与 `(视图)` 分开。
    #   两组问的不是同一件事：`(视图)` 问「这一层成不成立」，
    #   `(L0)` 问「这一层的**声明** + **若继续折**的契约成不成立」。
    #   并成一组的话，「视图七条全过」会顺手覆盖这七条 —— 而它们对**健全性**
    #   一个字节的信息都没有。见 `multilevel_report` 的 docstring。
    #   ⚠️ 组名**不叫「多层」**：产物是第 0 层，「一层就够」（真折 0 层）。
    ml_rep = multilevel_report(loaded, targets)
    reps.append(ml_rep)
    print(ml_rep.render())
    print()

    # ★ `cli` 的**四条入口**与两个非默认参数 —— 流程 C 的第三条覆盖缺口。
    #   三条「必须红」（空转护栏）是**已知答案对照**，见 `cli_report`。
    cli_rep = cli_report(loaded, targets)
    reps.append(cli_rep)
    print(cli_rep.render())
    print()

    # ★ `T1`–`T3` —— **检索器层（流程 T）**，与插件 / 内核 / 视图都无关，只跑一次。
    #   它问的不是「这一层成不成立」，是「**拿那份认识去检索**会不会漏 / 会不会用陈旧的 /
    #   解释指不指得到」。见 `retrieval_report` 的 docstring。
    rt_rep = retrieval_report(loaded, targets)
    reps.append(rt_rep)
    print(rt_rep.render())
    print()

    total_red = sum(len(r.red) for r in reps)
    total = sum(len(r.assertions) for r in reps)
    skipped = sum(len(r.skipped) for r in reps)
    missing = [c for r in reps for c in r.missing_codes() + r.unexpected_codes()]
    print("═══ 汇总 ═══")
    print(f"  断言 {total} 条：红 {total_red}，跳过 {skipped}")
    if skipped:
        occ = [(r.plugin or "?", a.code) for r in reps for a in r.skipped]
        codes = [c for _, c in occ]
        print(f"  跳过的是：{codes}")
        # ⚠️ **「跳过」按次，覆盖证据按编号** —— 两者不总是相等。
        #   同一个编号在两个方向上各跳一次 ⇒ 4 次 / 3 个编号。
        #   不印这行对账的话，「跳过 4」配「3 行证据」读者对不上，
        #   会读成「有一条没覆盖」—— 而它其实被同一个编号那行盖住了。
        uniq = sorted(set(codes))
        if len(uniq) != len(codes):
            dup = []
            for c in uniq:
                groups = [p for p, cc in occ if cc == c]
                if len(groups) > 1:
                    dup.append(f"`{c}` 在 {' 与 '.join(groups)} 上各跳一次")
            print(f"     （**{len(codes)} 次跳过 ⇒ {len(uniq)} 个不同编号** —— "
                  f"{'；'.join(dup)}；覆盖证据按**编号**出，一个编号一行）")
        table = list(_skip_evidence())
        ev = [(c, d, g) for c, d, g in table if c in codes]
        print("  ── 跳过项的**覆盖证据**（合成对照 —— **不是**本趟读数）——")
        for code, desc, good in ev:
            print(f"     {code:4s}{desc} ⇒ {'✓ 全对' if good else '‼ 对照不符'}")
        # ⚠️ **不许静默省略**：本趟跳过、但表里**没有**对照的编号必须**点名**。
        #    不点名的话，「没有这一行」与「这一行全对」在输出里长得一模一样 ——
        #    而 `B18` 的四个跳过分支实测**真的会跳**（`snap-hepth` / `snap-hepth-dag`）。
        naked = sorted(set(codes) - {c for c, _d, _g in table})
        for code in naked:
            print(f"     {code:4s}‼ **这条跳过没有覆盖证据** —— "
                  f"表里没有它的合成对照（**要人补**，别读成「已覆盖」）")
        if naked:
            print("     ‼ 缺证据的编号**必须点名**：跳过了却没有对照，"
                  "与「对照全对」不许长得一样")
        if any(not g for _, _, g in ev):
            print("     ‼ 对照不符 —— 权威闸门在 `run_tests`"
                  "（`test_equivalence` / `test_semantic_guard` / `test_skip_evidence_table`）")
        print("     ⚠️ 「跳过」= 本趟这条方向上**没有内容**；"
              "「对照」= 这条判据**会红**。两件事，不共用一行。")
    if missing:
        print(f"  ‼ 注册表不符：{missing}")
    print(f"  退出码 {1 if total_red else 0}")
    return 1 if total_red else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
