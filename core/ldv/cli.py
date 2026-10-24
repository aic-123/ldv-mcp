"""四条流程的入口 —— 工作流程 §1–§4。

    python -m ldv.cli all                  # B → A → D（默认；三个插件都跑）
    python -m ldv.cli all --plugin reach   # 只跑一个方向
    python -m ldv.cli a                    # 只跑流程 A（运行）
    python -m ldv.cli b                    # 只跑流程 B（维护）
    python -m ldv.cli d                    # 只跑流程 D（自优化）
    python -m ldv.cli r --remove 12        # 只跑流程 B′（删除，§10.2 C）
    python -m ldv.cli all --holdout 12     # 留 12 项当「维护」阶段的新项（默认 12）
    python -m ldv.cli all --remove 12      # 末尾再删 12 项（默认 0 = **不跑**）

**为什么顺序是 B → A → D**：维护把结构建全，运行才在完整结构上发生，
自优化用的就是运行攒下的记录。这个顺序也让「查询的 ground truth 覆盖全语料」
与「结构里真的有那些项」一致 —— 否则会看到「漏项」，而那只是项还没插进去。

⚠️ **流程 B′（删除）排在最后**，理由同源：删掉项之后查询的 ground truth
就不在结构里了，那时再跑 A 会看到**假的漏项**。
⚠️ **默认 `--remove 0` ⇒ B′ 整段不印**（不是印一行「删除 0 项」）：
「没跑」与「跑了但没删」必须分得开，而**不印**是最不容易读错的那种分法。
要跑就显式给 `--remove N`。

退出码：**只看护栏** —— `M3`（锥外变动）/ `F3`（外生项被改）/ **空转** / 未捕获异常。
方向数、记录数、位次这类**度量走输出，不进退出码**（继承 dce）。

⚠️ **空转也进退出码** —— 这是刻意的，也是本项目的中心纪律：
「空转与通过长得一模一样」是这里最贵的错。`python -m ldv.cli d` 单独跑时
没有使用记录，自优化**什么都没学到**；那种运行必须红，不能印个「成功」。
⚠️ **B′ 的 `D2` / `D3` 默认「未展开」**（要外生覆盖 oracle，`flow.remove_items`
的 `cover` 参数）—— 报告里印成 `**未展开**`，**不是** `0`。
"""

from __future__ import annotations

import sys

from .checks._fixtures import (
    coverage_of,
    keyset_queries,
    load,
    items as make_items,
    reach_queries,
    reusable_build,
    sequences,
)
from .core.kernel import Kernel
from .core.locking import LockedKernel
from .flow import (
    insert_items,
    make_params,
    remove_items,
    render_chain,
    run_optimize,
    run_query,
)
from .plugins.keyset import KeysetPlugin
from .plugins.reach import ReachPlugin
from .plugins.sequence import SequencePlugin
from .plugins import DIRECTIONS

#: 每个方向：插件工厂 · 根 payload · 查询集 · 最外层意图（**外生，人声明**）

INTENTS = {
    "keyset": "键集包含 —— 最外层意图不约束任何键",
    "reach": "锚点可达 —— 最外层意图 = 覆盖全部项",
    "sequence": "序列前缀 —— 最外层意图 = 空前缀，不约束序列",
}


def _assemble(name: str, nodes, edges, holdout: int, remove: int = 0,
              locked: bool = False, predicates: bool = False,
              reuse: bool = True, use_snapshot: bool = False):
    """造 `(内核, 查询集, 待插入的新项, 待删除的项 id, 覆盖 oracle)`。

    `holdout` 项**不放进 `build()` 的全集** —— 它们是流程 B 的「新项」。
    这样维护才真的在「先建一批、后来再添」，而不是把已声明的项重走一遍。

    `remove` 项从**已进结构**的那些里挑（`init_items` 的尾部），
    这样删除才真的碰到结构；挑「没进结构的项」等于删空气。

    `locked=True` ⇒ 内核换成 `core/locking.py` 的 **`LockedKernel`**（并发壳）。
    ★ 它**不进构建缓存** —— 见下面那一段的理由。

    `use_snapshot=True` ⇒ 走 `core/snapshot`：先试**恢复已建好的树**，恢复不了才建、
    建完落盘（2026-10-19）。**默认关** —— 开了只改「耗时」这条读数，不改结构。
    ⚠️ 指纹对不上 ⇒ **重建 + 如实报**，绝不静默复用（那是它要防的假绿）。
    ⚠️ 它省的是**下一次**的整棵树；与进程内 signature 记忆化（死路表 §3）不是同一件事。
    """
    ids = sorted(nodes)
    hold = set(ids[-holdout:]) if holdout > 0 else set()
    all_items = make_items(nodes)
    init_items = {k: v for k, v in all_items.items() if k not in hold}

    if name == "keyset":
        queries = keyset_queries(nodes)
    elif name in ("reach", "sequence"):
        queries = reach_queries(nodes, edges)
    else:
        raise ValueError(name)

    # ★ **同一份装配只建一次**（2026-10-11）：`run_checks` 的 `(cli)` 组会把
    #   `_assemble` 跑 24 遍，而其中 **15 遍入参逐字相同**（`holdout=12` 那一档：
    #   `CL1`/`CL2`/`CL3`/`CL5`/`CL7` 的差别只在 `only=`，而 `only=` **不进装配**；
    #   `remove` 只影响建完**之后**挑哪几项待删）。实测这一家占墙钟 **55.7%**。
    #   ⇒ 建一次，每个调用者各拿一份**深拷贝**。
    #   ⚠️ **不许共享同一个对象**：流程 A–E 会**就地改内核**，而
    #      `CL3`（`only="d"`）**必须红**，理由正是「A 没先跑 ⇒ 没有带权记录」——
    #      共享会让这条护栏**静默变绿**。判据见 `_fixtures.reusable_build` 那段与
    #      `run_tests.test_reusable_build`。
    def _plugin() -> tuple:
        """插件 + 根 payload。**拆出来**是为了让快照路径也能拿到插件：

        快照要用 `plugin.encode/decode` 存/还原 payload（§I5），
        而插件只在 `_build` 里造 ⇒ 拆成一个函数，两边同源。
        """
        if name == "keyset":
            plug = KeysetPlugin()
            root = plug.merge([])
        elif name == "reach":
            plug = ReachPlugin(edges)
            # ⚠️ **根 payload 只许引用「当时进树的那批项」** —— `init_items`，不是 `ids`。
            #
            # 这里原先写的是 `frozenset(ids)`（全语料当锚点），而 `holdout` 那批项
            # **压根没进树**（它们是流程 B 的「新项」）。于是根声明了它引用不到的锚点 ⇒
            # 覆盖的项集比树里的多 ⇒ **B′ 的 `D2 覆盖漏` 在每个真实语料上必红**。
            # 实测（`openalex-n100` / `n150` / `b280` / `small`）：全语料根 ⇒ 每次都 `D2 漏`；
            # 换成「当时已知的那批」⇒ 漏 0。单变量对照见 `outputs/_probe_root_ab.py`。
            #
            # 口径与 `checks/_fixtures.make_builder` **逐字同一条**（那里的注释写着
            # 「用全集就等于作弊」）：那一侧是「增量 vs 全量」要比较的对照实现，
            # 它一直用的是 `frozenset(sub)`。这里原先与它不一致，是**接线漏了**，
            # 不是两种正当口径 —— 所以修的是这一处，不是把对照实现改成全集。
            root = frozenset(init_items)
        else:                                       # name == "sequence"
            plug = SequencePlugin(sequences(nodes, edges))
            root = frozenset({()})                  # 外生：空前缀覆盖一切
        return plug, root

    def _build() -> tuple:
        plug, root = _plugin()
        kernel = (LockedKernel if locked else Kernel)(plug, init_items)
        kernel.build(root)
        # ★ **批量纳入**（2026-10-23）：`build(root)` 装的就是 `init_items` 这一批，
        #   正好是 `bulk_insert` 的前提（「build 已装全集」）⇒ 等价。
        #   判据 `test_bulk_equivalence` 三插件 × 逐字比；实测 coauth 5.64 s → 1.86 s。
        #   ⚠️ **只有这一处能用**：维护路径（后添的 holdout 项）**前提不成立**
        #      —— 那时 build 的全集比要插的少，批量会让分裂看到完整成员集 ⇒ 不等价。
        kernel.bulk_insert(sorted(init_items))
        return kernel, plug

    def _build_once() -> tuple:
        # ★ **锁壳不进构建缓存**（2026-10-15）：`reusable_build` 每次给一份
        #   `copy.deepcopy`，而 `LockedKernel` 持有 `threading.Lock` / `RLock`
        #   ⇒ **深拷贝会抛** `TypeError: cannot pickle '_thread.lock' object`。
        #   ⇒ 这条路径**直接建**。代价可控：它是**可选入口**（默认路径不锁），
        #      而且在随仓库那份 36 项语料上重建很便宜。
        #   ⚠️ 这**不是**「缓存坏了」—— 是 `LockedKernel` **不可深拷贝**这条性质。
        #      它是一条**组合性边界**，写在 `docs/并发接口设计.md` §4。
        if locked or not reuse:
            # `locked`：`LockedKernel` 不可深拷贝（见上）。
            # `not reuse`：**免掉 `reusable_build` 的 `deepcopy`**（2026-10-18）。
            #   实测 3000 节点上 `deepcopy` 占 cProfile 的 **13%**（23.9 万次递归调用）。
            #   ⚠️ 只给**探针/一次性调用**用：生产路径必须留深拷贝 ——
            #      共享同一对象会让 `CL3`（`only="d"`，必须红）**静默变绿**（见上面那段）。
            return _build()
        # ⚠️ **`predicates` 必须进 tag** —— 否则「开谓词」与「不开」两种装配
        #    会撞同一份缓存（`reusable_build` 的 docstring 明说：tag 必须把
        #    「哪份装配」说全，否则两种装配会撞）。
        return reusable_build(("assemble", name, holdout, predicates),
                              nodes, edges, _build)

    # ★ **跨进程快照**（2026-10-19，`--snapshot` 才开）
    #
    #   为什么：每次调用全量重造，而建树是**超线性**的 ——
    #     7115 项 6.7 s / 30k 216.6 s / 100k 3239.2 s（每项 0.94 → 7.22 → 32.39 ms）。
    #     进程内的 `reusable_build` 只活在内存里，**重启即归零**。
    #
    #   ⚠️ **默认关**：开了会改变「耗时」这条读数（虽然不影响结构/护栏结论）。
    #   ⚠️ 指纹对不上 ⇒ **重建 + 如实报**，绝不静默复用。
    #   ⚠️ 与死路表 §3 那条（进程内 signature 记忆化是**算法的一部分**）不是一回事：
    #      那个省「同一次建造里的重复计算」，这个省「下一次的整棵树」。
    snap_rep: dict = {}
    if use_snapshot:
        from .core import snapshot as _snap
        plug, _root_pl = _plugin()
        kernel, snap_rep = _snap.with_snapshot(
            (LockedKernel if locked else Kernel), plug, lambda: _build()[0],
            init_items, name, edges,
            {"holdout": holdout, "predicates": predicates})
    else:
        kernel, _plug = _build_once()

    new = [(i, all_items[i]) for i in sorted(hold)]
    removable = sorted(init_items)[-remove:] if remove > 0 else []
    if snap_rep:
        # 快照读数：命中 / 新建 / 重建（重建必带原因）。**不许静默。**
        _why = snap_rep.get("原因", "")
        _t = (f"｜建树 {snap_rep['建树秒']}s" if snap_rep.get("建树秒") is not None else "")
        print(f"  [树快照] {snap_rep.get('结果', '?')}{_t}"
              + (f"（{_why}）" if _why else ""))
    return kernel, queries, new, removable, coverage_of(name, nodes, edges)


def run_one(name: str, holdout: int, only: str | None,
            remove: int = 0, loaded=None, locked: bool = False,
            predicates: bool = False, reflect: bool = False,
            indexed: bool = False, apply_approved: bool = False,
            write_requests: bool = False,
            use_snapshot: bool = False) -> tuple[bool, str]:
    """跑一个方向。返回 `(护栏是否全过, 追踪文本)`。

    `loaded` 为 `None` 时自己 `load()`（**生产路径**：`python -m ldv.cli`）。
    `run_checks` 把已经读进来的那份传进来 —— 这样 `--cap` 也作用到它，
    而且不必为每条路径把语料重读一遍。
    ⚠️ 传进来的那份必须与 `load()` 同源（同一个 `LDV_CORPUS`），否则
    「判的是这一份、跑的是另一份」会**静默**成立 —— 那正是本仓库最忌的形状。

    `locked=True` ⇒ 走**并发壳**（`LockedKernel`）。⚠️ **它不改输出**（`P8-5`）——
    它是一条**可选入口**，给将来真的并发调用方用；单线程跑它只多付加解锁的代价。
    """
    if loaded is None:
        loaded = load(predicates=predicates, indexed=indexed)
    if loaded is None:
        return False, "⚠ 找不到语料 —— 四条流程都跑不起来（这不是通过）"
    nodes, edges, _dangling = loaded
    # ⚠️ **`load()` 的第三个是悬挂边集合，不是目录**（2026-10-19 踩到：
    #    直接把 frozenset 当 `corpus_dir` 传 ⇒ `TypeError: not 'frozenset'`）。
    #    `directions.json` 在**语料根**（`nodes/` 的父目录）。
    from .checks._fixtures import find_corpus as _find_corpus
    _root = _find_corpus()
    corpus_dir = _root.parent if _root is not None else None

    # ★ **闭环：把 `directions.json` 的 `已批准` 维度真的用上**（2026-10-19）
    #
    #   在它之前，`已批准` 只用来**防重复请求**（`already_handled`）——
    #   人批了，系统不会真去做 ⇒ 「报告 → 人拍板」是**开环**。
    #
    #   ⚠️ **默认关**（`--apply-approved` 才开）：键集变大 ⇒ 方向数变多
    #      ⇒ 会**静默改变所有既有读数**。默认关 = 既有基线一个字不动。
    #   ⚠️ 红线不变：只执行**人写进 `已批准`** 的，系统不自己加维度。
    ledger_ap: dict = {}
    if apply_approved and corpus_dir is not None:
        from ldv.core import request as RQ
        # ★ 谓词路径要 `edges` + 推导出的单谓词 —— 只在开了 `--predicates` 时推导。
        #   ⚠️ 没开时**不猜**：`apply_approved` 会记一条「未生效：没推导谓词」。
        _preds = ()
        if predicates:
            from .corpus import predicates as PR
            _rules, _budget = PR.load_rules(corpus_dir)
            _preds, _dropped, _params = PR.derive(nodes, edges, _rules, _budget)
        # ⚠️ 传 **used + dropped**：预算管的是「自动选谁」，不是「人能声明什么」。
        #    只传 `used` ⇒ 人引用预算外的谓词会被 `apply_basic` 判「缺项」**静默跳过**
        #    （实测：加键 0 项、无提示）。
        nodes, ledger_ap = RQ.apply_approved(nodes, corpus_dir,
                                             edges=edges,
                                             preds=list(_preds) + list(_dropped))

    kernel, queries, new, removable, cover = _assemble(
        name, nodes, edges, holdout, remove, locked=locked, predicates=predicates,
        use_snapshot=use_snapshot)
    st0 = kernel.stats()
    head = (f"═══ 方向 {name} ═══\n"
            f"  初始：{st0['方向']} 个方向（§K2 判空 {st0['§K2 判空']}），"
            f"装进 {len(kernel.items)} 项，事件 {st0['事件']} 条；"
            f"待维护 {len(new)} 项"
            + (f"；待删除 {len(removable)} 项" if removable else ""))

    b = a = d = r = None
    # 账本逐段快照 —— 只在段落**之间**取，所以每段增量是真的可核对的
    n = [len(kernel.ledger)]
    if only in (None, "b"):
        b = insert_items(kernel, new)
    n.append(len(kernel.ledger))
    # ★ **可选入口：反思层**（2026-10-17）—— 只调**展开顺序**，不改结构。
    #   前提已实测：正序 / 反序跑完 ⇒ 方向数与结构签名**逐字相同**。
    #   ⇒ 预展开只是「先做哪几个」，最终方向树不变。
    if reflect:
        from .core.reflect import apply as _reflect_apply
        _refl = _reflect_apply(kernel)
        # ★ **请求落盘**（2026-10-19）：在这之前 `write_requests` **只在测试里被调用**，
        #   磁盘上从来没有 `directions.json` ⇒ 请求不落盘 ⇒ 人看不到 ⇒
        #   「报告 → 人拍板 → 系统真的做」这条闭环**入口就是断的**。
        #
        #   ⚠️ 只在 `--write-requests` 时写：它会**改语料目录**（副作用），
        #      默认不写，免得跑一次测试就往语料里丢文件。
        if write_requests and corpus_dir is not None:
            from .core.reflect import contradiction_requests, contradictions
            from .core.request import (LEAF_CAP, candidate_dimensions,
                                       direction_requests, write_requests as _wr)
            # ★ **两路信号**并成一个请求清单（都写进 `directions.json` 的 `请求` 段）：
            #   ① B19（连续 N 次展开没变细）
            #   ② 矛盾 —— **两类**：结构矛盾（边打架 / 子覆盖越出父 / 孤立方向）
            #      + **切不开**（叶容量超阈值，2026-10-19 并入：它才是「加新谓词」
            #      对应的那种矛盾；结构那三类是内核不变量，恒 0 是正常的）
            req = direction_requests(kernel, cover)
            contra = contradictions(kernel, cap=LEAF_CAP)
            creq = contradiction_requests(kernel, contra)
            merged = dict(req)
            merged["请求"] = (list(req.get("请求", []))
                              + list(creq.get("请求", [])))
            grp = contra.get("分组", {})
            merged["说明"] = (
                f"B19 {len(req.get('请求', []))} 条 + "
                f"矛盾 {len(creq.get('请求', []))} 条"
                f"（结构 {grp.get('结构矛盾', 0)} / 切不开 {grp.get('切不开', 0)}）—— "
                + str(req.get("说明", "")))
            merged["本趟查了"] = {
                "B19": req.get("本趟查了", {}),
                "矛盾": creq.get("本趟查了", {}),
            }
            # ★ **候选维度清单**（2026-10-19）：把「材料里有什么可批」摆在请求旁边 ——
            #   否则人看到「要一个新维度」只能自己猜（实测 enron/coauth/brightkite
            #   都是「批了也白批」，因为可用字段早就用完了）。
            cand = candidate_dimensions(nodes)
            merged["候选维度"] = cand.get("候选维度", [])
            merged["候选维度说明"] = cand.get("说明", "")
            merged["说明"] += f"｜候选维度：{cand.get('说明', '')}"
            path_w = _wr(corpus_dir, merged)
            print(f"  请求已写：{path_w}（{len(merged['请求'])} 条："
                  f"B19 {len(req.get('请求', []))} / 矛盾 {len(creq.get('请求', []))}"
                  f"（结构 {grp.get('结构矛盾', 0)} / 切不开 {grp.get('切不开', 0)}））")
    if only in (None, "a"):
        # 跑三条查询 —— 记录累积下来给流程 D 用
        for q in queries[:3]:
            a = run_query(kernel, q)
    n.append(len(kernel.ledger))
    if only in (None, "d"):
        d = run_optimize(kernel, make_params(name, INTENTS[name]))
    n.append(len(kernel.ledger))
    # ★ 删除**排在最后** —— 删掉项之后查询的 ground truth 就不在结构里了。
    #   那两条（`D2`/`D3`）要外生覆盖 oracle，这里把 `cover` 传进去 ⇒ 它们是**量过**的。
    #   ⚠️ `only == "r"` 且没给 `--remove` ⇒ 设成**空表**（而不是 `None`）：
    #      那样「B′ 空转」这条护栏才会开火 —— 「没跑」与「跑了但没删」不能共用一行。
    if only in (None, "r"):
        if removable or only == "r":
            r = remove_items(kernel, removable, cover=cover)
    n.append(len(kernel.ledger))

    st1 = kernel.stats()
    usage = st1["使用记录"]
    grew = sum(x.events for x in (b or ()))
    # ⚠️ 「A 段 = 展示 + 展开」必须分开报。查询**也会按需展开**（§R1 会调 `expand`），
    #    所以 A 段的账本增量**不等于**记录数 —— 把两者当成一回事，
    #    「账本可核对」这句话就是假的（原来那版就是）。
    tail = (f"  终态：{st1['方向']} 个方向（§K2 判空 {st1['§K2 判空']}，"
            f"叶容量 最大 {st1['最大叶容量']} / 平均 {st1['平均叶容量']}）"
            f"，使用记录 {usage} 条；空方向 {st1['空方向']}"
            f"（已失效 {st1['已失效方向']}）\n"
            f"  账本：初始 {n[0]} → B {n[1]}（结构 +{n[1] - n[0]}）"
            f" → A {n[2]}（展示 +{usage}、**查询时展开** +{n[2] - n[1] - usage}）"
            f" → D {n[3]}（自优化**不写账本**）"
            + (f" → B′ {n[4]}（删除 +{n[4] - n[3]}）" if r is not None else ""))

    # ── 护栏 ───────────────────────────────────────────────────────────────
    # ★ 空转也要拦。理由：**空转与通过长得一模一样**（本项目从头到尾在防这个）。
    #   `cli d` 不先跑 A ⇒ 没有记录 ⇒ 旧版照样印「内生量变了」＋退出码 0。
    guards: list[str] = []
    if b is not None:
        if not b:
            guards.append("B 空转：没有可插入的项（--holdout 0？）")
        elif any(x.out_of_cone for x in b):
            guards.append("M3 违规：变动跑到 Cone(x) 之外")
        elif n[1] - n[0] != grew:
            guards.append(f"报告与账本对不上：报告说结构动了 {grew} 次，"
                          f"账本只长了 {n[1] - n[0]} 条")
    if a is not None and a.空转:
        guards.append("A 空转：一条候选都没摆出来")
    if d is not None:
        if not d.学到东西:
            guards.append("D 空转：没有带权记录 ⇒ 内生量只动了常数项，不是学到了东西"
                          "（先跑 A 攒记录，或直接 `python -m ldv.cli all`）")
        elif n[3] != n[2]:
            guards.append("D 动了结构：自优化只许改内生量，不许写账本")
    if r is not None:
        if not r:
            guards.append("B′ 空转：没有可删除的项（--remove 0？）")
        else:
            bad = [x for x in r if x.违规]
            for x in bad:
                guards.append("B′ " + "；".join(x.违规))

    body = render_chain(a, b, d, r)
    if guards:
        body += "\n  ‼ 护栏：" + "；".join(guards)
    return not guards, "\n".join([head, body, tail])


#: `--plan-all` 最多循环几次 —— **外生参数、暂定值，未经标定**。
#:
#: ⚠️ 20 次 × 每次 `rounds` 轮 × 每轮 `per_round` 个 = 上限约 480 次展开。
#:    这个数是「先让功能能用」的暂定，不是测出来的。要动它得先有刻度：
#:    「一份语料建树允许跑多久」—— 那是人的判断，不是这里能算的。
#: ⚠️ 撞上限时**必须如实报**（`剩余待展开 > 0` + `未建完`），不许印成「建完了」。
PLAN_MAX_CALLS = 20


def plan_report(*, rounds: int = 8, per_round: int = 3,
                snapshot: bool = False, until_done: bool = False) -> tuple[bool, str]:
    """`--plan`：从「只建根」开始报**建树计划**。

    不带 `--snapshot` ⇒ 纯诊断：**不落盘、不改结构**。
    带 `--snapshot` ⇒ **续建**：先试恢复上次的半成品快照，接着展开，再存回去。
       ⇒ 反复跑到 `剩余待展开 0` 就是「分几次建完」。

    ★★ **半成品树是能查的**（实测 2026-10-22）：`kernel.query` 对每个说「是」的方向
       **无条件 `expand`**（`core/kernel.py` 的 `v is Tri.YES` 分支）⇒ **边查边建**，
       答案与完整树**逐字相同**（内置 36 项：查询前 1 个方向 → 后 25 个，命中 36 vs 36）。
       而且 `persist`/`snapshot` **都存了** `已展开` 与 `试过` ⇒ 状态能精确读回。
       ⚠️ 真正的代价是「首次查询会边建边答（慢）」，**不是「会漏」**。

    ⚠️ `plan` 只改**展开顺序**，`insert` 的工作量一样 ⇒ 续建把工作**分批**，
       **不减少**总量。
    ⚠️ 快照指纹里带 `用途=plan-续建` ⇒ 与 `--snapshot` 建完整树的快照**互不覆盖**。
    """
    from .checks._fixtures import items, load, make_keyset_root
    from .core import snapshot as _snap
    from .core.kernel import Kernel
    from .core.reflect import plan as _plan
    from .plugins.keyset import KeysetPlugin

    loaded = load()
    if loaded is None:
        return False, "语料读不到（`_data/` 不在？）—— 建树计划需要能加载的材料"
    nodes, edges, _d = loaded
    plug = KeysetPlugin()
    its = items(nodes)

    fp = _snap.fingerprint(its, edges, {"方向函数": "keyset", "用途": "plan-续建"})
    p_path = _snap.tree_path("keyset", fp)
    k, rep = None, {}
    if snapshot:
        k, rep = _snap.restore(Kernel, plug, its, p_path, fp)
    if k is None:
        k = make_keyset_root(nodes)()
        if snapshot:
            rep = {"结果": "新建（只建根）", "原因": rep.get("原因") or "没有可用的半成品快照"}

    # ⚠️ 「起点」必须在 `plan()` **之前**取 —— 它会把 k 展开到底，
    #    之后再取 `len(k.all_directions())` 拿到的是**终态**（实测：起点被打成 25）。
    # 续建：上次「试过但分不开」的方向要从快照带回来 ——
    # 否则每轮都重试它们、占掉每轮名额 ⇒ **剩余不收敛**（实测：4 次都停在剩余 6）。
    tried_saved = set((rep.get("extra") or {}).get("plan_tried") or ())

    calls: list[dict] = []
    while True:
        n_before = len(k.all_directions())
        p = _plan(k, rounds=rounds, per_round=per_round, tried=tried_saved)
        tried_saved = set(p["试过方向"])
        calls.append({"起点": n_before, "终态": len(k.all_directions()), **p})
        if snapshot:
            _snap.save(k, plug, "keyset", fp, path=p_path,
                       extra={"plan_tried": sorted(tried_saved)})
        if not until_done or p["剩余待展开"] == 0 or len(calls) >= PLAN_MAX_CALLS:
            break

    last = calls[-1]
    hit_cap = until_done and last["剩余待展开"] > 0 and len(calls) >= PLAN_MAX_CALLS
    lines = [("建树计划（**续建**·跑到完｜落盘到快照）" if until_done
              else "建树计划（**续建**｜落盘到快照）" if snapshot
              else "建树计划（诊断｜**不落盘、不改结构**）"),
             f"  起点 {calls[0]['起点']} 个方向｜每轮上限 {per_round}"
             f"｜每次最多 {rounds} 轮"
             + (f"｜最多 {PLAN_MAX_CALLS} 次（暂定值）" if until_done else "")
             + f"｜材料 {len(nodes)} 项"]
    if snapshot:
        lines.append(f"  快照：{rep.get('结果', '?')}"
                     + (f"（{rep['原因']}）" if rep.get("原因") else ""))
    if until_done:
        for i, c in enumerate(calls, 1):
            lines.append(f"  第 {i:2d} 次 起点 {c['起点']:3d} → {c['终态']:3d}"
                         f"　展开 {c['总展开']:2d}　剩余 {c['剩余待展开']}")
    else:
        for r in last["轮次"]:
            lines.append(f"  第 {r['第几轮']:2d} 轮 展开 {r['展开'] or ['（无可展开）']}"
                         f"　剩余待展开 {r['剩余待展开']}")
    lines.append(f"  总展开 {sum(c['总展开'] for c in calls)}"
                 f"｜本次试过分不开 {sum(c['试过分不开'] for c in calls)}"
                 f"｜剩余待展开 {last['剩余待展开']}"
                 f"｜终态 {len(k.all_directions())} 个方向")
    lines.append("  ⚠️ 「剩余 0」有两种含义：全建完了 / 剩下的都试过且分不开"
                 "—— 看「试过分不开」那一格。")
    if snapshot:
        lines.append(f"  已写快照 {p_path.name}"
                     + ("　✅ 建完了（剩余 0）" if last["剩余待展开"] == 0
                        else (f"　‼ 撞上限 {PLAN_MAX_CALLS} 次，还剩 "
                              f"{last['剩余待展开']} 支 —— **没建完**"
                              if hit_cap else
                              f"　⏭ 还有 {last['剩余待展开']} 支待展开，再跑一次接着建")))
    return (not hit_cap), "\n".join(lines)


def main(argv: list[str]) -> int:
    args = argv[1:]
    # ★ **可选入口：建树计划**（2026-10-22，`--plan [轮数]`）。
    #   只报「建树要几步」，**不落盘、不改结构**（诊断）。放在最前面 ——
    #   它不走 A/B/D/R 任何一条流程，也不该打印「本轮跑：A+B+D」那些行。
    if "--plan" in args:
        _i = args.index("--plan")
        _rounds = 8
        if _i + 1 < len(args) and args[_i + 1].isdigit():
            _rounds = int(args[_i + 1])
        _ok, _text = plan_report(rounds=_rounds, snapshot="--snapshot" in args,
                                 until_done="--plan-all" in args)
        print(_text)
        print(f"\n退出码 {0 if _ok else 1}")
        return 0 if _ok else 1
    which = [a for a in args if not a.startswith("-")]
    plugin = "all"
    if "--plugin" in args:
        plugin = args[args.index("--plugin") + 1]
    holdout = 12
    if "--holdout" in args:
        holdout = int(args[args.index("--holdout") + 1])
    # ⚠️ 默认 **0 = 不跑 B′**，而且**整段不印**（见模块 docstring）——
    #    「没跑」与「跑了但没删」不能共用一行，而**不印**是最不容易读错的分法。
    remove = 0
    if "--remove" in args:
        remove = int(args[args.index("--remove") + 1])
    # ★ 闭环开关（2026-10-19）：把 `directions.json` 的 `已批准` 维度真的用上。
    #   默认**关** —— 开了会改变键集 ⇒ 改变方向数 ⇒ 改变全部读数。
    do_apply_approved = "--apply-approved" in args
    # ★ 请求落盘（2026-10-19）：`--reflect --write-requests` 才写 `directions.json`。
    do_write_requests = "--write-requests" in args
    # ★ **方向树落盘**（2026-10-19，`--snapshot` 才开）：下次调用恢复已建好的树。
    #   默认关 —— 开了只改「耗时」这条读数，不改结构/护栏；指纹不符一律重建。
    use_snapshot = "--snapshot" in args
    # ★ **可选入口：并发壳**（2026-10-15，`docs/并发接口设计.md` §7 阶段 1）。
    #   默认**不锁** —— 接线本身不产生收益（当前没有并发调用方），
    #   而默认路径每一步都要付一次加解锁。⇒ 做成**显式**入口，
    #   并用判据证明「锁壳与裸内核**逐字相同**」（`run_tests.test_lock_scope` ⑧）。
    locked = "--locked" in args
    # ★ **可选入口：推导谓词**（2026-10-17）。默认**不带** ——
    #   谓词会加宽键集 ⇒ 方向变细；默认路径的 97 条断言 / 624 条测试
    #   都建立在**不带谓词**的键集上，一个数都不能动。
    predicates = "--predicates" in args
    # ★ **可选入口：反思层**（2026-10-17）。只调展开顺序，不改结构 ——
    #   默认**不带**，因为它会改变「展开次数」这类读数。
    reflect = "--reflect" in args
    # ★ **可选入口：语料索引**（2026-10-17）—— 跨进程派生索引（长期缓冲）。
    #   默认**不带**：索引会把 `Node.body` 留空（主流程不用正文，但对照工具用）。
    indexed = "--index" in args

    steps = [a for a in which if a in ("a", "b", "d", "r")]
    only = steps[0] if len(steps) == 1 else None
    targets = DIRECTIONS if plugin == "all" else (plugin,)

    print(f"四条流程 · 流程 C（构建验收）在 `python -m ldv.run_checks`")
    print(f"本轮跑：{'A+B+D' if only is None else only.upper()}｜方向：{list(targets)}"
          f"｜维护新项 {holdout}"
          + (f"｜删除 {remove}" if remove else "｜**不删**（`--remove N` 才跑 B′）")
          + ("｜**并发壳**（`--locked`）" if locked else "")
          + ("｜**推导谓词**（`--predicates`）" if predicates else "")
          + ("｜**反思层**（`--reflect`，只调顺序）" if reflect else "")
          + ("｜**语料索引**（`--index`，跨进程）" if indexed else "")
          + ("｜**应用已批准维度**（`--apply-approved`）" if do_apply_approved else "")
          + ("｜**方向树落盘**（`--snapshot`）" if use_snapshot else ""))
    print()

    all_ok = True
    for name in targets:
        ok, text = run_one(name, holdout, only, remove, locked=locked,
                           predicates=predicates, reflect=reflect, indexed=indexed,
                           apply_approved=do_apply_approved,
                           write_requests=do_write_requests,
                           use_snapshot=use_snapshot)
        all_ok &= ok
        print(text)
        print()

    print("═══ 汇总 ═══")
    print(f"  护栏（M3 锥外变动 / F3 外生项被改 / 空转 / D1–D6）：{'全过' if all_ok else '‼ 有违规'}")
    print(f"  退出码 {0 if all_ok else 1}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
