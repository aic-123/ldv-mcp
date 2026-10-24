"""内核 —— 设计文档 §4.1。

内核负责（§4.1 逐条）：

    层的存在与秩 · 细化关系的形式 · 见证与支撑锥 · 按需展开 ·
    失效只分叉不覆盖 · 倾向权重的记账位 · 遍历循环 · 三态语义 ·
    §K8 那条契约（假阴禁止 / 假阳计量）

**内核不认识任何方向。** 它只认 `§I1`–`§I7` 七个方法（`core/interfaces.py`）。

---

## 一条必须先说清的约定：**秩越大越细**

    rank 1   = 根（最粗）—— **外生**，人声明的最外层意图
    rank r+1 = 由 rank r 展开出来的**更细**方向
    叶       = 细到不能再细（装不下两项）

于是：

    §K1 严格派生      层 n+1 的见证 ⊆ 层 ≤n     ← 子方向的见证是**父**（更粗）
    §I2 定义细化      `细 ⊑ 粗` ⟺ `合并({细}) = 粗`
    §K3 局部失效      插入 x 的失效范围 ⊆ `Cone(x)`

**`Cone(x)` 在内核里就是「插入 x 时走过的那条路径」** ——
`代价`（§I3）逐层选子方向，选出来的链就是 x 的支撑锥。
这不是实现取巧：GiST 的插入路径本来就同时是「x 属于哪些键」的答案，
所以「失效 ⊆ 支撑锥」是**结构上成立**的，§K3 因此可以真的被检查（`B8`）。

---

## 一条刻意不给内核的能力

内核**没有**「这一项属于哪个方向」的谓词。归属由**内核按 `代价` 分配**得到，
不是问插件问出来的。

理由：`§I1 命中` 允许假阳（§K8），所以 `命中(d, x) = 是` **不能**证明 x ∈ d；
拿它当归属谓词，§I4 要求的「不重不漏」就**无法检查**（重叠永远看不出来）。
把归属交给内核，划分就成了内核的**构造性质**，`B4` 才检查得动。

⇒ **划分是内核的责任，不是插件的责任。** 插件只负责「提出两个候选方向」。
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .direction import (
    EVENT_BORN,
    EVENT_INVALIDATED,
    EVENT_OUT_OF_SCOPE,
    EVENT_STAYED,
    EVENT_UNSPLITTABLE,
    EVENT_USAGE,
    EVENT_WITNESS_UPDATED,
    ORIGIN_EXOGENOUS,
    ORIGIN_SPLIT,
    Direction,
    Ledger,
)
from .interfaces import (
    PluginContractError,
    UsageSignal,
    WeightedRecord,
    call_hit,
    call_penalty,
    call_split,
    validate_plugin,
)
from .tri import Tri, fold
from .direction import did_order as _did_order

#: 曝光曲线的**指数**：`propensity(k) = k ** (-EXPOSURE_ALPHA)`，再夹下界。
#:
#:     α = 1.0    `1/k` —— **垂直排序列表**（越往下越看不见）。**本设计的原取值**
#:     α = 0.0    常数 1 —— **全部同时可见**（位次不产生曝光差）
#:
#: ## ★ 实测锚点（2026-10-07，Open Bandit Dataset，`ldv/MEASUREMENTS.md` 结果十三）
#:
#: 拿 OBD 的 **`random` 策略**（位次**随机分配** ⇒ 位次效应**因果可识别**）
#: 量了真实日志里的曝光曲线：
#:
#:     策略·活动              实测 1 : 2 : 3          1/k 预测           拟合 α
#:     ──────────────────────────────────────────────────────────────────────
#:     random · men       1 : 0.975 : 0.883     1 : 0.5 : 0.333        0.105
#:     random · women     1 : 0.984 : 1.006     1 : 0.5 : 0.333       -0.003
#:
#: ⇒ **`1/k` 在 k=2,3 上把倾向低估 2.0–3.1 倍** ⇒ IPS 权重被**放大**同样倍数
#:   ⇒ 方差白白增大。**「第一位和第十位差一个量级」在*这个*界面上不成立。**
#:
#: ## ⚠️ 但它**量不了本设计的情形** —— 这条必须一起说
#:
#: OBD 的三个位次是 ZOZOTOWN 推荐位的**左 / 中 / 右三个同时可见的槽位**，
#: **不是垂直排序列表**。所以上面那个 α ≈ 0.1 是**横向槽位**的性质，
#: 而本设计的 `show()` 摆的是**一个竖直的方向列表**。
#:
#:     横向槽位（实测）  读法顺序弱 ⇒ 衰减 ≈ 0
#:     竖直列表（未实测） 读法顺序强 ⇒ 衰减可以很大（检索里 k=1..5 全在首屏，CTR 仍陡降）
#:
#: ⇒ **能拿到的公开数据量的是另一种界面几何。** 本设计的几何**还没有数据**，
#:   所以这里**保守地保留 α = 1.0**（沿用设计文档 §8.1.1 的取值），
#:   并把参数**显式化** —— 下游的界面一旦能给出自己的位次日志，改这一个数即可标定。
#:
#: ⚠️ **翻案条件**：拿到「**竖直**列表 + 位次随机化」的点击日志 ⇒ 用它重跑
#:   `ldv/tools/measure_exposure.py` 的做法，按实测值改 `EXPOSURE_ALPHA`。
EXPOSURE_ALPHA = 1.0

#: 倾向权重的下界。`k ** -α` 会随位置衰减，夹一个下界是为了**限住权重上界**
#: （权重 = 结果 / 倾向 ≤ 1/FLOOR）。不夹的话，一个排在第 500 位的方向
#: 会拿到 500 倍的权重，方差大到把自优化掀翻。
#:
#: ⚠️ **这个下界只在 α 较大时才有意义。** 实测（同上）：α ≈ 0.1 时
#: `k ** -0.1 = 0.05` 要 `k ≈ 10^13` —— **永远碰不到**，下界成了死代码。
#: ⇒ 下界与 α **是一对**，改 α 必须回头看一眼这个数还起不起作用。
PROPENSITY_FLOOR = 0.05


def exposure_propensity(position: int) -> float:
    """曝光模型：**位次 → 倾向**。这是**唯一的实现**。

    ⚠️ 检查（`checks/semantics.py` 的 `_p`）与测试（`run_tests.test_exposure_model`）
       原来**各写了一份** `max(1.0 / position, PROPENSITY_FLOOR)`。三份同样的公式，
       改一处就会有两处静默地**报旧值** —— 而「报的是旧公式」与「报的是新公式」
       在输出上**长得一样**（只有一个数，没有对照）。所以抽成一个函数，谁都调它；
       `test_exposure_model` 里那条「改 α 看三处跟不跟着动」就是守这件事的。
    """
    return max(float(position) ** (-EXPOSURE_ALPHA), PROPENSITY_FLOOR)


@dataclass(frozen=True)
class Query:
    """一次查询。

    `ideal` 是**外生声明的 ground truth** —— 「这次查询应该命中哪些项」。
    它**不是**插件算出来的，所以 `B1`（假阴）才有得比。
    dce 的做法一致：合成语料带 ground truth，不拿被测对象自己产出的东西当答案。
    """

    ideal: frozenset[str]
    require: frozenset[str] = frozenset()   # 要求出现的键（方向 A 用）
    forbid: frozenset[str] = frozenset()    # 要求不出现的键
    min_rank: int = 1                       # 要求的分辨率：秩 < 它的方向只能答「未展开」
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.ideal, frozenset):
            raise TypeError("ideal 必须是 frozenset —— 顺序不该影响查询语义")


@dataclass
class QueryResult:
    """`R3` 的产物。**三态分开记** —— 不许把「未展开」并进「否」（§K6 / B10）。"""

    status: Tri
    #: 本次遍历里，**说「是」的方向**存着的、且在 `ideal` 里的项。
    #: ⚠️ 它**不是**「所有应该命中的项」—— 遍历会剪枝（说「否」的分支不往下走），
    #:    所以它是**对这次遍历而言**的答案，不是对 `ideal` 而言的。
    #: ⚠️ 它必须**每个说「是」的方向都收**（含停在**内部**方向的滞留项）。
    #:    只在叶上收会让滞留项**静默地少** —— 父的 `命中` 说「是」，而这里没有它。
    hit_items: frozenset[str] = frozenset()
    yes: tuple[str, ...] = ()           # 说「是」的方向
    no: tuple[str, ...] = ()            # 说「否」的方向
    unexpanded: tuple[str, ...] = ()    # 说「未展开」的方向
    layers_visited: int = 0
    trace: tuple[tuple[str, str], ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {"是": len(self.yes), "否": len(self.no), "未展开": len(self.unexpanded)}

    def render(self) -> str:
        """**输出形态** —— B10 / B6 检查的就是这里。

        三种去向必须长得不一样：
            「确定没有」  vs  「未知，需展开」  vs  「命中」
        合并其中任意两个都是 §K6 违规。
        """
        c = self.counts
        if self.status is Tri.YES:
            head = f"命中 {len(self.hit_items)} 项"
        elif self.status is Tri.NO:
            head = "确定没有"
        else:
            head = "未知，需展开"
        return (
            f"{head}｜是={c['是']} 否={c['否']} 未展开={c['未展开']}"
            f"｜层={self.layers_visited}"
        )


class Kernel:
    """内核。构造时**只**接受一个插件和一批项 —— 没有别的参数。

    「没有别的参数」是 §4.3 的落地：**没有全局目标函数**。
    若这里多出一个 `weights=` 或 `objective=`，§B12 就该红。
    """

    def __init__(self, plugin: Any, items: dict[str, Any]) -> None:
        missing = validate_plugin(plugin)
        if missing:
            raise PluginContractError(
                f"插件缺少方法 {missing} —— §I1–§I7 七个方法一个都不能少"
            )
        self.plugin = plugin
        self.items: dict[str, Any] = dict(items)
        self.ledger = Ledger()
        self._dirs: dict[str, Direction] = {}
        self._children: dict[str, tuple[str, ...]] = {}
        self._members: dict[str, set[str]] = {}
        #: **成功**展开过的方向（= 真的有子层）。叶**不**进这里 ——
        #: 见 `expand`：把叶也写进来就等于宣布「叶是终态」，那是原来那处缺陷的形状。
        self._expanded: set[str] = set()
        #: 展开**失败**过的方向 → **它是对哪一批成员判的**。
        #: 「分不开」只对那一次的成员集成立 —— 成员集变了就重试（见 `expand`）。
        self._tried: dict[str, frozenset[str]] = {}
        #: 每个项的**支撑锥**（§3 / §K3）。⚠️ **不变量**（2026-10-08 起）：
        #:     `set(_path[x])` == {d : x ∈ members(d)}
        #: 也就是说它**不**只是「插入时走过的路径」—— `expand` 重试时把 `x`
        #: 分进新的子方向，那一笔由 `_expand_inner` 追加进来（见那里的注释）。
        #: 破这条不变量的后果是 `remove` 摘不干净 ⇒ 子方向留**幽灵成员**。
        self._path: dict[str, tuple[str, ...]] = {}
        self._counter = 0
        self._root: Direction | None = None
        #: `all_directions()` 的**排序结果缓存**（2026-10-18 优化）。
        #: 实测它在 3000 节点上被调 6081 次、`sorted` 占 cProfile 的 **27%** ——
        #: 而 `_dirs` 只在 `_new` 里增长 ⇒ 两次 `_new` 之间排序结果恒定。
        #: ⚠️ 失效点是 `_new`（唯一增加方向的地方）；漏了会让方向数少报，判据会抓。
        self._all_cache: list[Direction] | None = None
        self._all_ver: tuple[int, int] | None = None
        # 记账位（§8.1）—— 内核知道它**展示过**什么，所以倾向由内核填
        #: **展示**顺序 —— 由 `show()` 显式设。倾向权重的位次从这里来。
        self._shown: tuple[str, ...] = ()
        #: 遍历经过的**最后一层前沿** —— 纯内省用，**不是**展示。
        self._last_frontier: tuple[str, ...] = ()
        self.usage: list[WeightedRecord] = []

    # --- 建 ---------------------------------------------------------------

    def build(self, root_payload: Any) -> Direction:
        """建根。根是**外生**的 —— 它的 payload 由调用方（人）给出。"""
        self._root = self._new(rank=1, payload=root_payload, witness=(),
                               origin=ORIGIN_EXOGENOUS, parent=None)
        self._members[self._root.did] = set(self.items)
        return self._root

    @property
    def root(self) -> Direction:
        if self._root is None:
            raise RuntimeError("还没 build()")
        return self._root

    def _new(self, *, rank: int, payload: Any, witness: tuple[str, ...],
             origin: str, parent: str | None) -> Direction:
        did = f"D{self._counter}"
        self._counter += 1
        d = Direction(did=did, rank=rank, payload=payload, witness=witness,
                      origin=origin, parent=parent)
        self._dirs[did] = d
        self._all_cache = None            # ← 排序缓存失效（快速路径）
        self.ledger.append(EVENT_BORN, did, rank=rank, origin=origin, parent=parent)
        return d

    # --- 并发钩子（§10.2 D 的 P8） -----------------------------------------
    #
    # ⚠️ **内核不知道锁。** 它**没有** `locks=` 构造参数 —— §4.3 说得很死：
    #    构造时只接受一个插件和一批项（多出 `weights=` / `objective=` 就该 `B12` 红）。
    #    并发是**调用方**的事，不是内核的能力。
    #
    # 但**锁的作用域由锥决定**，而锥只有内核知道（`§K3`：失效范围 ⊆ `Cone(x)`）。
    # ⇒ 这里留一个**默认什么都不做**的插入点：写操作每走到一个方向，
    #   就进出一次 `_step_guard(did)`。`core/locking.py` 的 `LockedKernel`
    #   把它换成真的锁。
    #
    # ⚠️ 默认实现是**空操作** ⇒ 单线程路径**逐字不变**
    #    （`B15` / 全部读数一个都不动 —— 见 `MEASUREMENTS` 结果十二）。

    def _step_guard(self, did: str) -> Any:
        """并发钩子 —— 默认**空操作**。见上面的小节。

        入参是**方向 id**（不是方向对象）：锁的粒度就是方向，
        而作用域（哪些方向）由**锥**定，不由这个函数定。
        """
        return _NO_GUARD


    # --- 读 ---------------------------------------------------------------

    def direction(self, did: str) -> Direction:
        return self._dirs[did]

    def all_directions(self) -> list[Direction]:
        """全部方向，按 `_did_order` 排 —— **结果缓存**（`_new` 时失效）。

        ⚠️ 返回的是**同一个 list 对象**（缓存复用）⇒ 调用方**不许就地改它**。
           现有调用方全部只读（`for d in ...` / `len(...)` / 切片），
           判据 `test_split_cost` ㊓ 会用结构签名比对，改坏会当场红。
        """
        # ⚠️ **失效判据用 `(len, _counter)` 而不是「只靠 `_new` 清空」**
        #    （2026-10-18 被判据当场抓到）：`run_tests` 的 `_DeleteDir` 注入版
        #    用 `self._dirs.pop(did)` **直接删**、绕过 `_new` ⇒ 只靠 `_new` 清空
        #    的缓存**读不到这次删除** ⇒ 注入无效 ⇒ 三条反向对照全变成「基线=0 注入=0」
        #    （空转）。⇒ 每读一次就比 `(方向数, 已发号数)`：
        #       增（`_new`）    len+1、counter+1  ⇒ 变
        #       删（pop）       len−1            ⇒ 变
        #       改对象内容      缓存存的是**同一引用** ⇒ 自动跟着变
        ver = (len(self._dirs), self._counter)
        if self._all_cache is None or self._all_ver != ver:
            self._all_cache = [self._dirs[k] for k in sorted(self._dirs, key=_did_order)]
            self._all_ver = ver
        return self._all_cache

    def children_of(self, d: Direction) -> tuple[Direction, ...]:
        return tuple(self._dirs[k] for k in self._children.get(d.did, ()))

    def members_of(self, d: Direction) -> frozenset[str]:
        return frozenset(self._members.get(d.did, ()))

    @property
    def placed(self) -> frozenset[str]:
        """**真的放进了结构**的项 —— 与 `items` 不是一回事。

        `§10.2` 出路 (1) 之后，内核会**认识**一些它**没放进结构**的项
        （落在根覆盖之外的那些，见 `_outside_root`）。两者必须分得开 ——
        合并成一个数就等于把「知道」与「收下了」当成一件事。
        """
        out: set[str] = set()
        for mem in self._members.values():
            out |= mem
        return frozenset(out)

    def is_expanded(self, d: Direction) -> bool:
        return d.did in self._expanded

    def cone(self, item_id: str) -> tuple[str, ...]:
        """§3 的**支撑锥** —— 「见证含 x 的方向」。

        在内核里，这一条落地成 **「当前真的持有 x 的方向」**：

            set(cone(x)) == {d : x ∈ members(d)}          ← `_path` 的不变量

        ⚠️ 它**曾经**只是「插入时走过的那些方向」，而那个口径是**错的**：
           `expand` 会**重试**（叶不是终态），重试时 `x` 被分进一个新的子方向，
           那条路径**没被记下来** ⇒ `remove(x)` 摘不干净 ⇒ 子方向留**幽灵成员**
           ⇒ `B4` 报「子方向多出父没有的项」。实测（4 语料 × 3 方向）：

               批建后 0 项（`B15`：批建时成员集第一次展开就完整 ⇒ 重试不触发）
               维护后 **49 项**（重试发生在这里）

           修法在 `_expand_inner` 与 `insert`（合并而非覆盖）。详见
           `outputs/_findings_real_data.md` 问题 7。
        """
        return tuple(self._path.get(item_id, ()))

    # --- 证明「不收」的方向（§10.2 出路 (1) / (4)） -------------------------

    def _refuses(self, d: Direction, item_id: str) -> bool:
        """d 是否**证明**不收这个项 —— 用 `§I1 命中` 的**单元素查询**问。

        ⚠️ **只有「否」才是否证**：

            否          这是**证明** ⇒ 项按定义不在这个方向的覆盖里
            是          允许是假阳（§K8）⇒ 不作数
            未展开      「不知道」不许被当成「不在」⇒ 不作数

        方向与 §K8 完全一致：**否**是证明，**是**是允许的假阳。

        ## 为什么用 `命中` 而不是新加一个「覆盖谓词」

        `命中` 已经**就是**那个谓词 —— `ideal = {x}` 的单元素查询问的就是
        「这个方向里有没有 x」。所以这条规则**不新增接口方法** ⇒ 不用重验 §I1–§I7。
        （清单里这一项原来是全表唯一可能要重验的；这样落地就不用。）

        ⚠️ **它偏保守**：`是` 与 `未展开` 都放行，所以只有「有证明」才拦。
           这条的不对称与 §K8 同向 —— 拦错了会**少放**，放错了才是**假阴**。

        ⚠️ **它对某些插件是空转的**：`keyset.hit` 对单元素 `ideal` 恒答「是」
           （它的「否」只在 `req`/`forb` 与查询冲突时下，而单元素查询两者都空）
           ⇒ 对 A 这条永远不拦。**空转本身不是缺陷**（A 的健全性本来就 0/328），
           但要**说清**：拦得住拦不住取决于插件敢不敢对单元素查询下「否」。
        """
        try:
            return call_hit(self.plugin, d,
                            Query(ideal=frozenset({item_id}))) is Tri.NO
        except Exception:  # noqa: BLE001 - 插件是外部代码
            return False        # 插件炸了**不许**当成「不在」—— 那是往假阴偏

    def stayed_of(self, d: Direction) -> frozenset[str]:
        """**留在这一层**的项 —— 每个子方向都证明不收它（§10.2 出路 (4)）。

        直接从账本读，不另存一份 —— 单一真相源（§K4 只追加）。
        """
        return frozenset(e.detail.get("item", "")
                         for e in self.ledger
                         if e.kind == EVENT_STAYED and e.did == d.did)

    def _outside_root(self, item_id: str) -> bool:
        """根是否**证明**这个项落在它的覆盖之外 —— 见 `_refuses`。

        ## 它为什么挂在**根**上

        Bε-tree 靠一条不变量把「待处理的项不许丢」钉住：**待处理的项必须落在
        查询路径上**。查询是从根沿 `劈开` 往下走的 ⇒ **根**在**每一条**查询路径上，
        所以这是唯一能保证「记了账就一定会被看见」的位置。

        ## 与出路 (4) 的关系：这是「无处可停」的那一支

        下降时每一步只进入「没有证明不收它」的子方向；**所有**子方向都证明不收 ⇒
        项**留在父方向**（出路 (4)）。根没有父 ⇒ 无处可停 ⇒ 只能**不放进结构**，
        并记一条 `out_of_scope`。⇒ 两条是**同一条规则**的两个分支，
        区别只在「有没有地方可以停」。
        """
        if self._root is None:
            return False
        return self._refuses(self._root, item_id)

    # --- 展开（按需，§M2） -------------------------------------------------

    def expand(self, d: Direction) -> tuple[Direction, ...]:
        """按需展开一个方向。**不预先物化**（§3 名词：展开的定义）。

        返回值 `()` 表示 **d 是叶** —— 分不开，或分开后有一半是多余的（§K2）。

        四情形（§M2）：
            ① 分不开            → 叶（**不建这一层**，见下）
            ② 见证被更新        → 追加 `witness_updated`（**事实**变化，不是判断）
            ④ 长出新的非平凡结构 → 追加 `born`（**涌现**）

        ⚠️ **情形③「标记失效（不删）」由 `remove()` 实现**（§10.2 C）——
        它需要一条**删除/收缩**流程，那条流程建在这里之外（`remove` 直接写账本，
        不经过 `expand`）。`EVENT_INVALIDATED` 由 `remove` 产生，本函数仍不产生它。

        ---

        ## ★ 叶**不是终态** —— 成员集变了就重试（这才是情形④能发生的地方）

        原来的实现把「分不开」也写进 `_expanded`，于是一个方向一旦被判成叶就
        **永远是叶**。后果很具体：树只在**第一批**项上长过层，之后插进来的项
        全都堆进已有叶里 —— **维护永远不会加深结构**。

            实测（同一份 36 项语料，只是建法不同；keyset 方向）。

            ⚠️ 当时**两个缺陷叠在一起**，读表要分开看：
                (i)  `insert()` 从不重试 ⇒ 结构退化（方向数掉、叶容量爆）
                (ii) 判空时物化**两个空壳** ⇒ 方向数里混进 2 × 判空次数

                建法              改前 方向数   其中空壳   改前 最大叶容量
                ────────────────────────────────────────────────────
                一次建完 36        39         14        10
                先建 30，维护  6   35         12        10
                先建 24，维护 12   31         12        10
                先建 12，维护 24   15          8        16
                先建  6，维护 30  **7**        **4**   **31**

            最后一行不是「维护很便宜」，是**检索结构退化成了平表**：
            36 项挤在 3 个真方向里，其中一个叶装 31 项（另外 4 个方向是空壳）。

        **改后**（同一份语料、五种建法，三个方向都量了）：

            方向       建法            方向数   §K2 判空   最大叶容量
            ───────────────────────────────────────────────────────
            keyset    一次建完 36       25       7         10
            keyset    先建  6 维护 30   25       4         10
            reach     一次建完 36       71       0          1
            reach     先建  6 维护 30   71       0          1
            sequence  一次建完 36       21       8         10
            sequence  先建  6 维护 30   21       7         10

        三个后果，都要单独说：

        * **尺度与分辨率不再随建法变化**（方向数、最大叶容量逐项相同）——
          维护不再把结构退化成平表。这是修复要的效果。
          `run_tests.test_emergence` ③ 把这条钉住了，而且**跨三个方向**查，
          免得只是 keyset 的巧合。
        * **空壳消失**（39 → 25 = 25 个真方向 + 0 个空壳）。可验的后果是
          `members(d) ≠ ∅` 恒成立 —— 那条断言在旧实现下会红。
        * ⚠️ **但树不同构**（`_shape()` 实测不相等）。这是**当前实现
          （路径局部增量）的性质**，不是「增量」的固有代价 ——
          Godin 1995 的增量概念格既增量又顺序无关，Naor–Teague 走阈值重建。
          修复要的是「不退化」，不是「维护必须重建成批建的样子」；
          但「不同构」这件事**要在文档里写成选择**，不能写成定理（见 `C8` N4）。
        * `§K2 判空` 是**唯一**还会随建法变的数 —— 因为它是「判过几次」的记账，
          本来就与历史有关（§K2 的时间维度）。它是**度量**，不是判据。

        ---

        ## ★ §K2 的字面是「不建这一层」—— 那就真的**不建**

        原来是「先建出一对子方向，再把它们标成失效」。那不是「不建」，
        是「建了再作废」。两者的区别不是风格：

            建了再作废   方向数里多出 2 个空壳 ⇒ 「空权威层比例」这个
                         **形状信号**被污染成「试了几次」
            不建         形状信号干净了，但**代价没有消失** ——
                         它搬到「叶容量」上：这一层不建，这些项只能留在
                         这个叶里 ⇒ 叶容量变大 ⇒ **分辨率下降**

        ⇒ 判空时只追加一条 `unsplittable`（挂在**父方向**上，因为判定的主体是它，
          而且**没有子方向可以挂**），不建任何方向。
        ⇒ 度量也跟着搬：`stats()` 同时报「§K2 判空」与「叶容量」，
          它们**是同一件事的两种记账**（设计文档 §7.1）。

        ---

        ## 与 `B15` 不冲突（这是改这里之前必须确认的事）

        `B15` 要的是「同一批项、一次建完、换插入顺序 ⇒ 树同构」。它成立靠的是：
        `build()` 把**当时的全集**装进根，所以每个方向第一次被展开时，
        它的成员集**已经是完整的** —— 展开一次就定形，重试逻辑永不触发。

        维护路径正相反：成员集是**后来才长起来的**，重试才会触发。
        两者说的不是一回事：`B15` 守**批建**，维护的历史相关性是**固有**的。

        ---

        ## ★ §I4 的返回是「None | 一组方向」，**不是固定两个**

        三件事同时成立，而且**互相独立**：

        **(1) 分支数由插件定（k 叉）。** 二分是 B-tree 遗产，不是所有方向的最自然形状：
            有序方向（序列前缀）的自然形状是 **trie —— 一个节点按「下一个符号」分出 k 个**。
            被迫二分 ⇒ 中间层造出「两个符号的并」这种**不是前缀**的 payload
            ⇒ 覆盖变大（假阳）且要多花一层。内核现在照插件给的个数建子方向，
            不写死 2。

        **(2) 「分不开」是插件**显式声明**的（返回 `None`），不是内核猜出来的。**
            原来编码成「返回两个相同 payload」，内核从「分配后一侧为空」**反推**
            「插件分不开」。两种东西于是长得一样：

                插件**判**分不开        接口的正常用法 —— 插件的判断
                插件给的候选**切不动**   候选退化 —— 插件与内核之间的摩擦

            ⇒ 现在分开记（账本 `unsplittable` 的 `by` 字段），`stats()` 分两个数报。
              分开记才有信息量：一个**永远返回空组**的插件与一个**诚实声明分不开**的
              插件，在「反推」的读法下产出完全相同的账本 —— 那就是
              「空转与通过长得一模一样」在接口层的形态。

        **(3) 少给（<2）是违约，不是「分不开」。** 由 `call_split` 判。
            内核**不替插件猜**：要么说 `None`，要么给 ≥2 个。

        ⚠️ **判空的两条路径都不建方向**（§K2 的字面），代价仍然搬到**叶容量**上。
            `None` 路径与「切不动」路径的**代价完全一样**，区别只在**归因**。

        ## ⚠️ 它**会改结构**，所以 `query` 的遍历路径上也经过写操作

        `expand` 建子方向、写 `_children` / `_expanded` / `_tried` —— 它不是读。
        而 `query` 在遍历时**调用**它（`§R1` 之后）。⇒ 并发下**查询也会写**，
        所以这一层必须自己守一次（不能只靠 `insert` / `remove` 在锥上守）。

        重复进同一把锁由 `core/locking.py` 的 `RLock` 承担：
        `insert` 已经在锥上握着 `d`，这里再进一次，计数加一而已。
        """
        with self._step_guard(d.did):
            return self._expand_inner(d)

    def _expand_inner(self, d: Direction) -> tuple[Direction, ...]:
        if d.did in self._children:
            return self.children_of(d)

        items = sorted(self._members.get(d.did, ()))
        if len(items) < 2:
            # 情形①：单项，分不开。**不记 `_tried`** ——
            # 它只是「还没到能判的时候」，等长到 2 项再试。
            return ()

        if self._tried.get(d.did) == frozenset(items):
            return ()                       # 这批成员试过了，结论不变

        group = call_split(self.plugin, d, [self.items[i] for i in items])

        if group is None:
            # 路径一：**插件声明**分不开 —— 接口的正常用法，是插件的判断。
            self.ledger.append(EVENT_UNSPLITTABLE, d.did,
                               reason="§K2：插件声明这一层分不开", by="plugin",
                               members=len(items))
            self._tried[d.did] = frozenset(items)
            return ()

        # 先用**探针**算归属：不建方向、不写账本 ⇒ 判成「分不开」时不留残骸。
        # 平手判给**靠前**那个候选 —— 与二分时「`c1.did` 数值上小于 `c2.did`」等价。
        # ⚠️ 探针的 did 必须**互不相同**：插件若按 `did` 定序，同串会让它看到平手。
        probes = [_probe(d, p, j) for j, p in enumerate(group)]
        buckets: list[list[str]] = [[] for _ in probes]
        for iid in items:
            costs = [call_penalty(self.plugin, pr, self.items[iid]) for pr in probes]
            buckets[min(range(len(costs)), key=lambda j: (costs[j], j))].append(iid)

        # §K2 非平凡：**每一侧**都得有东西，否则这一层不建 —— 就照字面来。
        if any(not g for g in buckets):
            # 路径二：候选**切不动**。归因与路径一不同（这是插件与内核之间的摩擦），
            # 但**代价相同**（都不建这一层）。
            self.ledger.append(EVENT_UNSPLITTABLE, d.did,
                               reason="§K2：这一层不建（劈开后一侧为空）", by="kernel",
                               sizes=tuple(len(g) for g in buckets), members=len(items))
            self._tried[d.did] = frozenset(items)
            return ()

        kids = tuple(self._new(rank=d.rank + 1, payload=p, witness=(d.did,),
                               origin=ORIGIN_SPLIT, parent=d.did)
                     for p in group)
        for kid, g in zip(kids, buckets):
            self._members[kid.did] = set(g)
            # ★ 成员写进了子方向 ⇒ **那个项的锥必须跟着长**。
            #
            # `_path[x]` 原来只在 `insert` 结尾写一次（= 当时的下降路径）。
            # 而 `expand` 会**重试**（叶不是终态：成员集变了就重判），重试时
            # `x` 被分桶分进一个**新的**子方向 —— 那一笔**没有记到 `_path[x]` 上**。
            # ⇒ `x` 是子方向的成员，却不在自己的锥上。
            #
            # 后果**只在删除路径上显现**（批建不重试 ⇒ 实测 0 项）：
            # `remove(x)` 只从 `_path[x]` 上摘 ⇒ 子方向留下**幽灵成员**
            # ⇒ `B4` 报「子方向多出父没有的项」。实测（4 份语料 × 3 个方向）：
            #
            #     批建后      0 项     ← 不重试
            #     维护 12 项  49 项    ← 重试发生在这里
            #
            # ⚠️ `_path.get(iid) is None` 时**跳过**是对的：那说明这个项还没有
            #    「自己的锥」（`insert` 尚未走到写锥那一步）。此时它正**在被插入
            #    的路上**，`insert` 会先记下当时的路径、再让这一笔追加进去
            #    （见 `insert` 里「先记路径」那一段）。在这里凭空造一条锥，
            #    会把「还没进结构」的项写成「已经持有」。
            for iid in g:
                p = self._path.get(iid)
                if p is not None and kid.did not in p:
                    self._path[iid] = p + (kid.did,)
        self._children[d.did] = tuple(k.did for k in kids)
        self._expanded.add(d.did)
        self._tried.pop(d.did, None)        # 长出来了 ⇒ 上一次的失败不再成立
        self.ledger.append(EVENT_WITNESS_UPDATED, d.did,
                           witness=tuple(k.did for k in kids),
                           sizes=tuple(len(g) for g in buckets))
        return kids

    # --- 插入（§M0–§M5） --------------------------------------------------

    def insert(self, item_id: str, item: Any = None) -> tuple[str, ...]:
        """插入一个项，返回它走过的路径（= `Cone(x)`）。

        §K3：**失效范围 ⊆ `Cone(x)`** —— 这里「失效范围」就是本函数触碰过的方向集合，
        而它按构造等于路径。`B8` 会把这条再独立查一遍。

        ## `item` 非空 ⇒ 这是一个**新项**（流程 B 的维护路径）

        `build()` 会把**当时的全集**装进根，所以「后添的项」必须能在此刻才纳入 ——
        否则它从第一层就参与分组，那不是维护，是重建。

        ⚠️ **后插的项不参与它之前已经发生过的分组。** 它只走已有的树，走到叶就落在那里。

        这与 `B15`（换插入顺序 ⇒ 树同构）**不冲突**，因为两者说的不是一回事：

            B15    同一批项、**一次建完**，只是插入顺序不同 ⇒ 结构必须同构
            维护   先建一批、**后来再添** ⇒ 历史本来就不同，结构不同是对的

        ⇒ `B15` 守的是**批建**的可复现性。维护的历史相关性是**当前实现的
          性质**，**不是**「增量」的固有代价（`C8` N3 有反例）。

        ## ★ 落在**根覆盖之外**的项：记一条事件，**不塞进结构**

        见 `_outside_root`。要点：只有 `命中(根, {x}) = 否` 才算数 ——
        那是**证明**，不是猜测。这样「最外层意图之外」从一个**静默的错位**
        变成一条**显式、可数**的账（`stats()['根覆盖之外']`）。

        ⚠️ 代价是**范围**，不是正确性：那些项在这条方向上检不出来，
           但它们**在账上**。这正是这一条的全部价值 ——
           把一个静默的状态换成一条显式的记录。

        ## ★ 为什么不把「记成员」**随行写**（这是 P8 里一处真的绊脚石）

        成员那一笔写在**下降结束之后**（`for did in path`），不是每走到一层就写。
        这个顺序**是手递手（crabbing）的绊脚石**：

            末尾写   下降结束之后还要写**整条锥**的成员
                     ⇒ 那一刻**整条锥的锁都得还在** ⇒ 手递手放不掉祖先的锁
            随行写   走到 d 时先 `members(d) += x`，再 `expand(d)`
                     ⇒ 不必回头写祖先 ⇒ 手递手才做得起来

        ⚠️ ★ **2026-10-11 更正**：这一段原来写的是「两者**不是**同一个算法：随行写会让
           『先建 6 维护 30』的结构与批建**不同**」。**这条归因没测出来** ——
           `outputs/_probe_inline_write.py` 用一个**只覆写 `expand`** 的「随行写」对照
           （**不抄** `insert`）在 **36 项**与 **1005 项**上跑：成员写入时机**确实**变了
           （248 / 40897 次），但**结构（方向 / 子边 / 成员集）逐项相同**
           （三方向 × 批建 / 维护，**六组全同**）。⇒ 现在只陈述**实现**事实
           （末尾写要整条锥的锁），**不再声称**「随行写会改语义」。

        ⚠️ 但**也没有实测说随行写安全** —— 要真去动手递手，先补一条能红的判据
           守住 `test_emergence` ③ 那两条不变量（尺度与分辨率不随建法变化），
           并把 `_expanded` / `_tried` 这两个**历史相关**的量一起看住。

        ★ 而**要不要现在做**是**另一件事**：`LockedKernel` **在默认路径上没接**
           （整套单线程跑；可选入口 `cli --locked` 已落地）⇒ 手递手**现在没有消费者**。这是**优先级**判断，
           **不是**「做不到」—— 上面那条归因被推翻之后，这条路是**通的**。

        ⇒ 后果直接落在并发上：本实现**持有整条锥**（`core/locking.py`），
          这与 `§10.2 D` 说的「根是**唯一公共争用点**」一致，
          但「同时持有几把锁」是**另一个读数**（见 `MEASUREMENTS` 结果十二），
          实测 max **7**（Lehman–Yao 的 **3** 是手递手之后的数，**不能混着引**）。
        """
        if item is not None:
            self.items[item_id] = item
        if item_id not in self.items:
            raise KeyError(item_id)
        # 根要**单独**守一次：`_outside_root` 会写账本，而它发生在下降之前。
        with self._step_guard(self.root.did):
            if self._outside_root(item_id):
                self.ledger.append(EVENT_OUT_OF_SCOPE, self.root.did, item=item_id,
                                   reason="§I1：根对单元素查询判「否」⇒ 按证明落在根覆盖之外")
                self._path[item_id] = ()
                return ()
        path: list[str] = []
        # ★ 下降的每一步都进出一次 `_step_guard` ⇒ **拿锁的方向集合 = 走过的路径**。
        #   `ExitStack` 让「进」的顺序 = 下降顺序 = **秩递增** ⇒ 与 `remove` 同序
        #   ⇒ 良序 ⇒ 不死锁（Lehman & Yao 1981 §6.1）。
        with ExitStack() as stack:
            d = self.root
            while True:
                stack.enter_context(self._step_guard(d.did))
                path.append(d.did)
                kids = self.expand(d)
                if not kids:
                    # 叶：**先把项落下来，再看落下来之后分不分得开**（§M2 情形④）。
                    #
                    # ⚠️ 这一步不能省。只在「进入一个方向时」试展开的话，
                    #    刚落地的那一项永远轮不到被考虑 —— 它的叶带着**落它之前**
                    #    的结论。落完再试一次，才闭合。
                    self._members.setdefault(d.did, set()).add(item_id)
                    # ★ **先把这个项当时的路径记上**，再重试展开。
                    #
                    # 下面那次 `expand(d)` 会把 `item_id` 分桶分进某个子方向
                    # （分桶按 `代价`，**不看** `_refuses`）⇒ 它成了那个子方向的成员
                    # ⇒ `_expand_inner` 要把那一笔追加到 `_path[item_id]` 上。
                    # 不先记 ⇒ `_path.get(item_id)` 是 `None` ⇒ 那一笔**丢掉**
                    # ⇒ 幽灵成员（`remove` 摘不干净）。见 `_expand_inner` 那段注释。
                    self._path[item_id] = tuple(path)
                    kids = self.expand(d)
                    if not kids:
                        break
                    # 落下去之后长出了新层 ⇒ 这一项也得往下走。
                    # 每层子方向的成员是父的**真子集**（另一侧非空），所以一定终止。
                # ★ 只进入「没有**证明**不收它」的子方向（§10.2 出路 (4)）。
                #   见 `_refuses`：`是` 与 `未展开` 都放行，只有「否」是证明。
                ok = [k for k in kids if not self._refuses(k, item_id)]
                if not ok:
                    # 每个子方向都证明不收它 ⇒ **停在这一层**，项留在父方向。
                    #
                    # 为什么不拒绝（出路 (3) 的读法）：拒绝会让项从**成员集**里消失，
                    # 而 `B1` 的 ground truth 正是「成员 ∩ 查询」——
                    # 于是「健全性变绿」会**部分来自数据变少**。留在父方向则：
                    # 项仍可检索（父的 `命中` 说「是」）、`B1` 的 ground truth 不动，
                    # 代价只落在**划分**上（`B4` 要收窄成「漏的恰好是账上那些」）。
                    # 外部印证：X-tree 的 supernode ——
                    #   "created during insertion **only if there is no other possibility**"
                    self.ledger.append(EVENT_STAYED, d.did, item=item_id,
                                       reason="§10.2 出路 (4)：每个子方向都证明不收它 ⇒ 留在这一层",
                                       kids=tuple(k.did for k in kids))
                    break
                nxt = min(ok, key=lambda k: (call_penalty(self.plugin, k, self.items[item_id]), k.did))
                d = nxt
            # ⚠️ 这一笔**必须在下降结束之后**写 —— 见下面「为什么不随行写」。
            for did in path:
                self._members.setdefault(did, set()).add(item_id)
        # ⚠️ **合并**，不是覆盖。
        #
        # `_expand_inner` 会把「重试展开时被分桶分进的子方向」追加到
        # `_path[item_id]` 上（那一笔写在 `path` **之外**，因为分桶与下降是两件事：
        # 分桶按 `代价` 选，下降还过一遍 `_refuses`，两者可以选到**不同**的子方向）。
        # 覆盖会丢掉那一笔 ⇒ 幽灵成员回来。
        #
        # 合并的结果就是**支撑锥的定义**：`_path[x]` == 「当前真的持有 x 的方向」。
        # 这一条是 `remove` 摘得干净的前提，也是 §K3「失效范围 ⊆ Cone(x)」的前提。
        merged = list(path)
        for did in self._path.get(item_id, ()):
            if did not in merged:
                merged.append(did)
        self._path[item_id] = tuple(merged)
        return tuple(merged)

    # --- 删除（§10.2 C） --------------------------------------------------

    def bulk_insert(self, item_ids: Iterable[str] = ()) -> None:
        """
        把一批**已在 `items` 里**的项**一次**纳入结构（2026-10-18）。

        ## 前提（**先说前提，再说等价**）

            `build()` 装的那一批 == 这次要批量纳入的那一批

        满足它才等价。**实测**（2026-10-23，`test_bulk_equivalence`，三插件逐字比）：

            前提成立（build 全集 + 批量全集）   keyset 25/25 · reach 71/71 · sequence 16/16 ⇒ 相同
            前提不成立（build 6 项 + 批量 30 项） 方向数 25 vs 3 ⇒ **不同**

        ## 旧结论为什么错（2026-10-18 → 2026-10-23 更正）

        旧文档说「默认 36 项**不等价**」并据此不接线。**那是前提没成立**测出来的：
        当时 build 只装了 6 项，却拿 30 项去批量 —— 那正是**维护路径**的形状，
        而维护路径本来就不该用批量（分裂会看到完整成员集，逐个版看到渐进成员集）。

        ⇒ 前提成立的那条路（`cli._assemble` 的批建：`build(root)` 装的就是
          `init_items`）**可以接**，已接，全套判据仍绿。

        ## 为什么等价

        `build()` 已经把那一批装进根成员，而 `expand(d)` 自己负责把
        `members(d)` 分到子方向 —— 逐个 `insert` 的循环体**只是在沿路径走**，
        真正的分配早就在 `expand` 里做完了。⇒ 批量版只需把整棵树 `expand` 一遍，
        再补 `_path`（项所在的锥）。实测（`ext-coauth` 9875 项）：5.64 s → 1.86 s（3.0×）。

        ⚠️ 旧文档引的「判据 `test_bulk_insert` ㊖」**不存在**（㊖ 是「树快照版本不符」）。
           现在的判据叫 `test_bulk_equivalence`，它同时钉住「前提不成立时**确实**不等价」。
        """
        ids = list(item_ids) if item_ids else sorted(self.items)
        # ① 整棵树展开一遍 —— 分配由 `expand` 自己做
        queue: list[Direction] = [self.root]
        while queue:
            d = queue.pop(0)
            queue.extend(self.expand(d))
        # ② 补 `_path`：一次遍历所有方向建反向索引（O(总成员数)）
        path_map: dict[str, list[str]] = {}
        for d in self.all_directions():
            for iid in self._members.get(d.did, ()):
                path_map.setdefault(iid, []).append(d.did)
        for iid in ids:
            self._path[iid] = tuple(path_map.get(iid, ()))

    def remove(self, item_id: str) -> tuple[str, ...]:
        """删除一个项 —— `§M2 情形③`。流程见设计文档 §10.2 C / 成熟方案 §4.7。

        返回它走过的路径（= `Cone(x)`），与 `insert` 对称。

        ⚠️ **它摘得干净，靠的是 `_path` 的不变量**：`set(_path[x])` ==
           「当前真的持有 x 的方向」（见 `cone`）。这条不变量 2026-10-08 才补齐 ——
           在那之前 `_path[x]` 只是插入路径，`expand` **重试**分进去的新子方向
           没被记下 ⇒ 这里摘不干净 ⇒ 子方向留幽灵成员。实测 49 项 / 4 语料。

        ## 只做两件事，一件都不多

            ① 把 `x` 从 `items` 与 `members(D)`（`D ∈ Cone(x)`）里去掉
            ② 对 `D ∈ Cone(x)` 的每个方向追加一条 `invalidated`（账本**只增不改**）

        **不删方向、不动 `_children`、不压单子、不与兄弟合并、不把项重插。**
        三条菜单的裁决与逐字依据：

            压掉单子方向   单子只在**删掉节点之后**才出现（ART：「**now** has only
                           one child」）；不删 ⇒ 不产生单子
            与兄弟合并     Guttman 逐字排除：「there is no adjacency in the B-tree sense」
            把项重插回树   CT6 的前提是 CT3 压掉了节点；不删 ⇒ **没有孤儿**

        ## ⚠️ 失效的方向**继续答查询**

        `invalidated` **只影响账本，不影响 `命中`** —— 本函数不碰插件、不碰遍历。
        若把它实现成「不参与检索」，那些项还在语料里、查询可能命中 ⇒ **假阴**
        ⇒ §K8 的**唯一**禁令被破。这是本流程最容易写错的一处，而且写错了**不报错**。

        ## ⚠️ 「不再被强制」的准确含义是「**x 不再强制它**」

        所以事件带 `members_after`：**支持集空了**才叫「这个方向已失效」，
        还剩支持者就只是「少了一个支持者」。折叠规则在 `Ledger.status_of()` ——
        它读 `members_after == 0`，**不读**「有没有出现过 `invalidated`」。
        （读后者的话，删任何一项都会把整条路径标成「已失效」，而那些方向**明明还活着**。）

        ## ③「向上收缩父的 payload」**没有做**，理由在 §10.2 C

        设计文档原来把 ③ 写成**硬要求**（Guttman `CondenseTree` CT4 的对应），
        理由是「删掉 x 后 members 变小，**若 payload 跟着收窄**，`∪覆盖(子)` 就变小
        ⇒ 父不跟着收窄就红」。而**本设计里没有任何东西从 members 重算 payload**：

            `payload` 由**插件声明**、`Direction` **不可变**（§K4 的结构保证）
            删一个成员**不改变**任何 `payload`
            ⇒ `覆盖(父)` 与 `∪覆盖(子)` **两边都不动**
            ⇒ `B18` / `B16` 在删除路径上**按构造不变**（实测见 `MEASUREMENTS` 结果十一）

        ⇒ ③ 的前提是 payload 为**派生聚合**（R-tree AT3 / GiST PR2 的
          "E.p is the **Union** of all entries on N"），本设计**不满足** ——
          「改父的键」这条路在 §10.2 A 已经因为同一个理由被排除过
          （根是**外生**的 ⇒ 改它等于替人改声明）。采纳 ③ 得先推翻 §K4 的不可变。
        """
        if item_id not in self.items:
            raise KeyError(item_id)
        path = tuple(self._path.get(item_id, ()))

        # ★ 锁的**作用域 = 锥**（`§10.2 D` 的 P8）。锥在这里是**已知的**
        #   （插入时记下的），所以一次拿齐、按**秩递增**拿 ⇒ 与 `insert` 同序
        #   ⇒ 良序 ⇒ 不死锁。
        with ExitStack() as stack:
            for did in path:
                stack.enter_context(self._step_guard(did))

            # ① 从 `items` 与 `members(D)`（`D ∈ Cone(x)`）里去掉。
            #    只碰路径上的方向 ⇒ 「变动 ⊆ `Cone(x)`」（`D4`）**按构造**成立。
            del self.items[item_id]
            for did in path:
                self._members.get(did, set()).discard(item_id)

            # ② 账本**只增不改**：只 `append`，不修改任何既有条目（`D5` / `B9`）。
            for did in path:
                self.ledger.append(
                    EVENT_INVALIDATED, did, item=item_id,
                    members_after=len(self._members.get(did, ())),
                    reason="§10.2 C：x 不再强制这个方向（支持集少了一个）")

        # 锥是**插入时**的记账，项走了就作废 —— 留着会让 `cone()` 回答一个
        # 已经不在内核里的项。判据要的那一份由**返回值**给出（`D4` 用）。
        self._path.pop(item_id, None)
        return path

    # --- 遍历循环（§R0–§R5） ----------------------------------------------

    def query(self, q: Query, *, skip: frozenset[str] = frozenset()) -> QueryResult:
        """§R0–§R5。

        `R2` 的**三分支不许合并** —— 所以这里把三态分别收进三个元组，
        最后按 `R3` 的三去向给一个 `Tri`。**没有**任何一步把「未展开」当「否」。

        ## ★ `skip` —— 「这一支**已经知道说否**」的入口（`§R1` 的一处省，不改结论）

        调用方（检索器层 `步骤 T3`）把「**已经知道说否**」的方向 did 传进来。依据是
        `§I2` 的一条推论（证明在 `ldv/checks/retrieval.py` 的模块 docstring 里）：

            `覆盖(合并(S)) ⊇ ⋃_{d∈S} 覆盖(d)`  ⇒  合并说「否」 ⇒ **每个成员都说「否」**

        ⇒ 这些方向**照 `Tri.NO` 分支处理**（记 `no`、**不展开**），只**省掉** `call_hit`。

        ⚠️ **它不违反 `§C1` ①「不自立终止依据」**：说「否」的是**插件**（`§K8` 允许的那
           一侧：`否` 必须是**保证**不满足），不是内核自己立的依据；而且 `Tri.NO` 分支
           **本来就不展开** ⇒ 那些方向的子树本来就不跑。省掉的只是重复判定。
        ⚠️ **传错了会造假阴**（`§K8` 禁止的那一侧）—— 所以这一条由判据 `T1` 守：
           候选集必须与**全扫**逐项相同。
        """
        yes: list[str] = []
        no: list[str] = []
        unexp: list[str] = []
        trace: list[tuple[str, str]] = []
        hits: set[str] = set()
        layer = 0
        frontier: list[Direction] = [self.root]

        while frontier:
            layer += 1
            self._last_frontier = tuple(d.did for d in frontier)   # 内省用，**不是**展示
            nxt: list[Direction] = []
            for d in frontier:
                if d.did in skip:                               # ★ 块级预筛：已知说「否」
                    # 与下面 `Tri.NO` 分支**逐字同款**（记 `no`、不展开）——
                    # 唯一的差别是不调 `call_hit`。见 `query` 的 docstring。
                    no.append(d.did)
                    trace.append((d.did, str(Tri.NO)))
                    continue
                v = call_hit(self.plugin, d, q)                 # §R1
                trace.append((d.did, str(v)))
                if v is Tri.YES:                                # §R2 分支一
                    yes.append(d.did)
                    # ★ 成员要**每个说「是」的方向都收**，不能只在叶上收。
                    #   出路 (4) 之后，项可以**停在内部方向**（滞留）⇒ 只在叶上收
                    #   会把它**静默地漏掉**：父的 `命中` 说「是」，而 `hit_items` 里没有它。
                    #   实测（`outputs/_probe_leak_vs_stay.py`）：16 个滞留项
                    #   **一个都没进** `hit_items`，而 `render()` 照样印「命中 0 项」。
                    #   注入验证（`test_query_hit_items`）：改回只在叶上收
                    #   ⇒ **289 条里只有那条完整性断言红**（24/86 项没进）。
                    hits |= {i for i in self._members.get(d.did, ()) if i in q.ideal}
                    kids = self.expand(d)
                    if kids:
                        nxt.extend(kids)
                elif v is Tri.NO:                               # §R2 分支二
                    no.append(d.did)
                else:                                           # §R2 分支三 —— 独立第三值
                    unexp.append(d.did)
            frontier = nxt

        # §R3 三去向
        if yes and not unexp:
            status = Tri.YES
        elif yes:
            status = fold(Tri.YES, Tri.UNEXPANDED)              # 有是有，但不全 —— 不许说「确定」
        elif unexp:
            status = Tri.UNEXPANDED
        else:
            status = Tri.NO
        # ⚠️ 这里原来还有一段 `if hits: status = Tri.YES if not unexp else Tri.UNEXPANDED`。
        #    它是**空操作**：`hits` 非空 ⇒ 至少有一个方向说了「是」⇒ 上面两个分支
        #    给出的正是同一个值（`fold(是, 未展开) == 未展开`，见 `core/tri.py`）。
        #    留着它会让读者以为「有没有命中项」参与了三态判定 —— 而它没有。
        #    （删掉它同时解掉一个副作用：`hits` 改成「每个说「是」的方向都收」之后，
        #      若这段还在，语义会被它悄悄改掉。）

        return QueryResult(status=status, hit_items=frozenset(hits),
                           yes=tuple(yes), no=tuple(no), unexpanded=tuple(unexp),
                           layers_visited=layer, trace=tuple(trace))

    # --- 展示（§8.1 的记账前提） -------------------------------------------

    def show(self, ds: Iterable[Any]) -> tuple[str, ...]:
        """§8.1 的「展示」—— **内核记录它把哪些方向、按什么次序摆给了使用者**。

        ⚠️ **这不是遍历的副作用。遍历经过的方向 ≠ 摆给使用者看的方向。**

            遍历   会走很多层、很多「未展开」、很多「否」—— 那是内核自己的事
            展示   只摆**候选**（说「是」的那些），而且**次序就是曝光位次**

        原来的实现把「展示」设成**最后一层的前沿**，于是多层查询之后它停在最深那层：
        给一个第 1 层的方向记使用，位次会算成「不在展示里 ⇒ 当作最靠后」，
        倾向被压到下界。**曝光模型的前提（位次有意义）在那时是假的。**

        ⇒ 所以「展示」必须是**显式的一步**，由流程 A 在拿到候选之后调用。
          （`C7-流程跑通.md` 记了这条是怎么暴露出来的。）

        入参可以是方向对象，也可以是 id —— 遍历结果里两种都有。
        """
        self._shown = tuple(d.did if hasattr(d, "did") else str(d) for d in ds)
        return self._shown

    @property
    def shown(self) -> tuple[str, ...]:
        """本轮展示过的方向 id（按曝光位次）。"""
        return self._shown

    # --- 使用记录（§I6 / §8.1） -------------------------------------------

    def record_usage(self, d: Direction, interaction: Any) -> WeightedRecord | None:
        """调 §I6，然后**由内核填倾向权重**（§8.1）。

        分工：插件只判定「这次交互算不算信号」；倾向由内核填，
        因为**只有内核知道它展示了什么、展示在第几位**。

        ## 曝光模型：按**位置**，不按个数

            展示在第 k 位  ⇒  propensity = k ** (-EXPOSURE_ALPHA)
                             （并夹在 PROPENSITY_FLOOR 以上；见那个常量的 docstring）

        为什么不能按「展示了几个」：那等于假设**每个位置的曝光概率相同**。
        真实检索里第一位和第十位的曝光概率差一个量级 ——
        按个数算，倾向加权会把偏差**原样带进**自优化，
        而自优化的目的恰恰是修掉这个偏差。

        ⚠️ **`α` 是显式参数**（`EXPOSURE_ALPHA`），不再是写死在这行里的 `1 / k`。
           理由：实测（Open Bandit Dataset）表明这个指数**随界面几何变**
           （横向槽位 ≈ 0.1，竖直列表按设计取 1.0）⇒ 它是个**要标定的量**，
           而一个写死在函数体里的常数**没法标定**，也没法被检查「有没有接线」。
           见 `EXPOSURE_ALPHA` 的 docstring 与 `ldv/MEASUREMENTS.md` 结果十三。

        位置来自**展示顺序**（`show()` 设的），所以插件**无法伪造**它 ——
        这也是 §8.1 把填倾向这件事留给内核的原因。

        ## 没有展示上下文时

        若这一轮**没调用过 `show()`**（比如单元测试直接记一条），
        位次无从谈起。这里**不抛异常**（那会把只想测 §I6 的调用者一起打死），
        而是按「只展示过它自己」算，并在 `extra` 里**标出来** ——
        **不知道就要写「不知道」，不能默默当成知道**（§K6 的同一条纪律）。
        """
        sig: UsageSignal | None = self.plugin.signal(d, interaction)
        if sig is None:
            return None                      # 「没有观测到使用」≠「使用了但为负」
        if self._shown:
            shown = self._shown
            no_context = False
        else:
            shown = (d.did,)
            no_context = True
        try:
            position = shown.index(d.did) + 1        # 1-based，**曝光位次**
        except ValueError:
            position = len(shown) + 1                # 不在本次展示里 ⇒ 当作最靠后
        propensity = exposure_propensity(position)
        rec = WeightedRecord(did=d.did, outcome=float(sig.outcome),
                             propensity=propensity, note=sig.note,
                             extra={"shown": list(shown), "shown_n": len(shown),
                                    "position": position,
                                    "无展示上下文": no_context})
        self.usage.append(rec)
        self.ledger.append(EVENT_USAGE, d.did,
                           outcome=rec.outcome, propensity=rec.propensity,
                           position=position)
        return rec

    # --- 自省 -------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """自省。**代价与形状分开报，而且要知道代价搬到哪儿去了。**

        ## §K2 判「这一层不建」之后，代价**不会消失，只会换地方**

            旧实现（建了再作废）  代价记在「空权威层」上 —— 方向数里多出空壳
            字面实现（不建）      代价记在「**叶容量**」上 —— 这一层不建，
                                  这些项就只能留在这个叶里 ⇒ 叶装得多 ⇒
                                  **分辨率下降**（查询要多扫几项）

        ⇒ 所以两个数一起报，**它们是同一件事的两种记账**：

            `§K2 判空`    有多少**方向**被判「不建」 —— 形状与接口贴合度的信号
            `最大/平均叶容量`  那些项最后堆到哪儿去了 —— **代价**信号

        实测（36 项语料，一次建完）：reach 判空 0 / 最大叶容量 1 / 平均 1.00；
        keyset 判空 7 / 10 / 2.77；sequence 判空 8 / 10 / 3.27。
        形状越自然，两个数都越小。

        ⚠️ `§K2 判空` 数的是**方向**（`len(self._tried)`），不是**判定事件数**。
        成员集每变一次就重判一次（§K2 的时间维度），所以账本里的 `unsplittable`
        事件**可能多于**这个数。两者都别当判据用 —— 它们是**度量**。

        ## §I4 之后多一个数：判空的**归因**

        `§I2 判空` 有两条路径，代价**完全一样**（都不建这一层），但归因不同：

            声明分不开   插件返回 `None` —— 接口的**正常**用法，是插件的判断
            切不动       插件给了候选，但分配后一侧为空 —— 插件与内核之间的摩擦

        分开报的理由：一个**永远返回空组**的插件，与一个**诚实声明分不开**的插件，
        在只报总数的读法下**产出完全相同的数** —— 那就是
        「空转与通过长得一模一样」。分开报之后，「切不动」恒为 0 才说明
        **插件确实在用它自己的判断说话**，而不是内核在替它兜底。

        ## 「方向」和「有子层」要分开报

        `方向 = 有子层 + 叶` 恒成立，所以这两个数可以互相验。
        ⚠️ 这个键原来叫「已展开」，但它把**叶**也算进去了 —— 名不副实。

        ## 「认识」与「纳入」也要分开报（§10.2 出路 (1)）

        `认识 = len(items)`、`纳入 = len(placed)`。两者之差就是**根覆盖之外**的项数 ——
        那些项内核**认识**、但**没有收进结构**。代价落在**范围**上（在这条方向上
        检不出来），所以它必须是个**能被看见的数**：不然「根覆盖之外」就退化成
        一个没人知道的静默状态 —— 那正是这一条要消灭的东西。

        ⚠️ 三者要一起看：`认识 = 纳入 + 根覆盖之外`。任一条不成立就是记账错了。

        ## 再一个数：**滞留**（§10.2 出路 (4)）

        `滞留` = 「每个子方向都**证明**不收它 ⇒ 留在父方向」的项数。
        它与 `根覆盖之外` 是**两条不同的代价**，必须分开报：

            根覆盖之外   项**不在**任何方向的覆盖里（根下「否」）⇒ **范围**损失
            滞留         项**在**父的覆盖里，但**没有子方向**表达得出来 ⇒ **划分**代价
                         （项仍可检索 —— 父的 `命中` 说「是」）

        合并成一个数就把「声称盖不住」与「这一层表达不出来」当成一件事。
        ⇒ 滞留的代价落在**划分**上：`B4` 要收窄成
          「不重，且**漏的恰好是账上那些**」（见 `checks/contract.py`）。

        ## 字面 §K2 还有一个**可验**的后果：不存在空壳方向

        判空**不建** ⇒ 每个方向都是被真的分配过成员的 ⇒ `members(d) ≠ ∅` 恒成立。
        旧实现每判空一次物化**两个**空壳，于是 `方向数 = 活方向数 + 2 × 判空次数`。
        ⇒ 这两条是一组「改前会红」的判据（`run_tests.test_emergence` ② 里查了它们）：
        `每个方向的成员集非空`，以及 `方向 = 有子层 + 叶`（不许多出那两个）。

        ## ★ 分辨率要看「**项停在哪儿**」，不能只看叶（这是 `叶容量` 的一个洞）

        `叶容量` 的定义是「叶里最多几项」—— 它默认**每一项都走到了叶**。
        §10.2 出路 (4) 之后这个前提**不成立**：项可以停在**内部**节点上。

            实测（`sequence`，先建 2 维护 34）：
                叶容量      **2**     ← 看起来分辨率很高
                最大停留数  **34**    ← 真相：34 项堆在**根**上，根是内部节点
            同一份结构，`叶容量` 报 2 而真实扫描量是 34 —— **一个数骗人，一个数不骗**。

        ⇒ 所以补一个 `最大停留数`：

            `停(d) = |members(d) \\ ∪ members(子)|`     ← 停在 d 上、没再往下走的项
            `最大停留数 = max_d 停(d)`

        **它是 `叶容量` 的推广**：项都走到叶时，叶的 `停` 就是它的成员数，
        内部节点的 `停` 是 0 ⇒ 两者**相等**（批建路径上实测相同）。

        ⚠️ **不能用 `Cone(x)` 的末位来算** —— 锥是**插入时**记下的，
           而某个方向**后来**才劈开时，已在它里面的项会被重新分配，
           锥却不会跟着延长 ⇒ 算出来会把「早就走到叶的项」当成「停在内部节点」。
           ⇒ 必须**从成员集现算**（`停(d)` 的定义）。
        ⇒ 两条**恒等式**（可验，见 `run_tests`）：
           `Σ_d 停(d) == 纳入`、`Σ_{d 非叶} 停(d) == 滞留（账上）`。
        """
        dirs = list(self._dirs.values())
        leaves = [d for d in dirs if not self._children.get(d.did)]
        caps = [len(self._members.get(d.did, ())) for d in leaves]
        # ★ 停留数：从**成员集现算**，不用 `Cone(x)`（见上）。
        stop: dict[str, int] = {}
        for d in dirs:
            mine = set(self._members.get(d.did, ()))
            kids = self._children.get(d.did, ())
            if not kids:
                stop[d.did] = len(mine)
            else:
                union: set[str] = set()
                for k in kids:
                    union |= self._members.get(k, set())
                stop[d.did] = len(mine - union)
        fans = [len(k) for k in self._children.values()]
        unsplit = [e for e in self.ledger if e.kind == EVENT_UNSPLITTABLE]
        oos = [e for e in self.ledger if e.kind == EVENT_OUT_OF_SCOPE]
        stayed = [e for e in self.ledger if e.kind == EVENT_STAYED]
        # ★ 删除路径（§10.2 C）两个数，**分开报** —— 理由与「认识 / 纳入」同源：
        #   一个是**结构**事实（支持集空了），一个是**账本**事实（记了没有）。
        #   合并成一个数，就把「方向空了、却没人记账」这个静默状态藏掉了（`D1`）。
        empty = [d.did for d in dirs if not self._members.get(d.did)]
        emptied = {e.did for e in self.ledger
                   if e.kind == EVENT_INVALIDATED
                   and e.detail.get("members_after") == 0}
        return {
            "方向": len(self._dirs),
            "有子层": len(self._children),
            "叶": len(leaves),
            "§K2 判空": len(self._tried),
            "判空·声明": sum(1 for e in unsplit if e.detail.get("by") == "plugin"),
            "判空·切不动": sum(1 for e in unsplit if e.detail.get("by") == "kernel"),
            # ★ 「认识」与「收下了」分开报（§10.2 出路 (1)）。
            #   代价落在**范围**上，所以必须是个能被看见的数 ——
            #   不然「根覆盖之外」就退化成一个没人知道的静默状态。
            "认识": len(self.items),
            "纳入": len(self.placed),
            "根覆盖之外": len(oos),
            # ★ 「范围损失」与「划分代价」也分开报（§10.2 出路 (4)）。
            "滞留": len(stayed),
            # ★ 删除路径（§10.2 C）：**结构**事实与**账本**事实分开报。
            #   健康的内核（没删过）里 `空方向` 恒为 0 —— §K2 判空不建，
            #   每个方向都被真的分配过成员。删除**可以**让它变空，那时
            #   `空方向 == 已失效方向` 才是对的（`D1` 查的就是这条）。
            "空方向": len(empty),
            "已失效方向": len(emptied),
            "最大扇出": max(fans) if fans else 0,
            "最大叶容量": max(caps) if caps else 0,
            "平均叶容量": round(sum(caps) / len(caps), 2) if caps else 0.0,
            # ★ 分辨率的**推广** —— 项停在内部节点时，`叶容量` 看不见（见上）。
            "最大停留数": max(stop.values()) if stop else 0,
            "停在叶上": sum(stop[d.did] for d in leaves),
            "停在内部": sum(stop[d.did] for d in dirs if self._children.get(d.did)),
            "事件": len(self.ledger),
            "使用记录": len(self.usage),
        }


class _NoGuard:
    """`Kernel._step_guard` 的默认实现 —— **什么都不做**。

    它存在的唯一理由是**把「这里有一个可插入点」写出来**：
    一个默认空操作的钩子，与「忘了加钩子」，在单线程读数上**长得一模一样**
    （`MEASUREMENTS` 结果十二把两边都量了 —— 逐项相同）。

    ⚠️ 复用一个实例（不是每次新建）—— 它是**无状态**的，`__exit__` 恒返回 `False`
       （不吞异常）。
    """

    __slots__ = ()

    def __enter__(self) -> "_NoGuard":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


_NO_GUARD = _NoGuard()


def _probe(parent: Direction, payload: Any, slot: int = 0) -> Direction:
    """造一个**探针方向** —— 只用来算 `§I3 代价`，不进 `_dirs`、不写账本。

    为什么需要它：判「这一层分不分得开」必须先把项按代价分配一遍，
    而分配需要候选方向。若直接用真方向，判成「分不开」时就会留下残骸，
    反复重试还会不断累积，把 §K2 的比例信号冲淡。

    `did` 取一个**不会与真方向撞车**的串（真方向都是 `D<数字>`），
    并且**每个候选一个**（`slot`）—— 插件若按 `did` 定序，同串会让它看到平手。
    """
    return Direction(did=f"{parent.did}~probe{slot}", rank=parent.rank + 1, payload=payload,
                     witness=(parent.did,), origin=ORIGIN_SPLIT, parent=parent.did)
