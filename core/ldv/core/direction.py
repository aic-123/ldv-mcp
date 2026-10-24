"""方向记录与事件账本 —— §3 名词 / §K4 只分叉不覆盖 / §I7 记录。

两条设计要点：

**一、`Direction` 是不可变的。** 方向一旦建出来，它的 `payload` 不再改。
要改就是**新的事件**，不是改字段。这样 §K4「只分叉不覆盖」是**结构上成立**的，
而不是靠自觉。

**二、`Ledger` 只追加。** 失效不是删，是**再写一条 `invalidated`**。
于是「曾由 W 强制、现已不成立」与「从未存在过」在账本上长得**不一样** ——
这正是 arena §C10 要的那个区分。

    「这个方向不再被强制」和「这个方向从来没存在过」必须分得开。
    一旦覆盖，这两个就再也分不开了。

**三、账本里还有一类**判定**事件（`unsplittable`）。**
它**不是**失效 —— §K2 判「这一层不建」时，那一层**根本没建出来**，
所以没有东西可失效。它记的是「这里判过、结论是不建」，
用来让「判过分不开」与「从没判过」分得开。
⚠️ 它挂在**父方向**上（判定的主体），不挂在一对不存在的子方向上。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# 事件的种类。**只增不改** —— 加新种类可以，改旧种类的语义不行。
EVENT_BORN = "born"                     # 方向被建出来
EVENT_WITNESS_UPDATED = "witness_updated"   # 见证被缩小（**事实**变了，不是判断）
EVENT_INVALIDATED = "invalidated"       # 不再被强制 —— **标记，不删**（§M2 情形③；
                                        #  由 `Kernel.remove()` 产生，见 §10.2 C。
                                        #  ⚠️ 它的 `detail` 必须带 `members_after`：
                                        #  折 `status_of` 读的是**支持集**，不是事件种类。）
EVENT_UNSPLITTABLE = "unsplittable"     # §K2：判「这一层不建」（**判定**，不是失效）
EVENT_OUT_OF_SCOPE = "out_of_scope"     # 项**按证明**落在根覆盖之外 ⇒ 不塞进结构（§10.2 出路 (1)）
EVENT_STAYED = "stayed"                 # 项**留在父方向**：每个子方向都**证明**不收它
                                        #  （§10.2 出路 (4) —— X-tree 的 supernode 同形：
                                        #   「only if there is no other possibility」）
EVENT_USAGE = "usage"                   # 一次交互的使用记录（含倾向权重）

ALL_EVENT_KINDS = (EVENT_BORN, EVENT_WITNESS_UPDATED, EVENT_INVALIDATED,
                   EVENT_UNSPLITTABLE, EVENT_OUT_OF_SCOPE, EVENT_STAYED, EVENT_USAGE)

# 方向的来源。**外生 / 内生** 是 §K9 的分界，不是注释。
ORIGIN_EXOGENOUS = "exogenous"   # 人声明的最外层意图 —— 不许被 usage 驱动
ORIGIN_SPLIT = "split"           # 由劈开产生（内生）
ORIGIN_EMERGENT = "emergent"     # 由合并涌现（内生）


class LedgerViolation(RuntimeError):
    """账本被改写 —— §K4 / §B9。"""


@dataclass(frozen=True)
class Direction:
    """一个层上的一个可路由单元。

    `payload` 是**插件定义的不透明内容**（A 是键集，B 是锚点集）。
    内核从不解释它 —— 内核只看 `rank` / `witness` / 身份。
    """

    did: str
    rank: int                       # 根为 1；**秩越大越细**（见 README 的方向约定）
    payload: Any
    #: 生成它的那组**更粗**方向 —— ⚠️ 方向是**向上**指的，不是向下。
    #: §6.1：`子.witness = (父,)`，而 `父.rank < 子.rank` ⇒ 循环在结构上不可能。
    #: （`§K1` 的「层 n+1 的见证 ⊆ 层 ≤n」与 `§I2` 的「粗 = 合并(细)」两条一起读
    #:  只有这一种读法；读成「见证 = 子方向」会与 §K1 直接冲突。）
    #: ⚠️ 它是**元组**，但当前构造恒为**一元**（单父）；多父是**具名留白**，
    #:  见 §10.2 D —— 启用它会让持久化从 node-copying 掉到 fat node。
    witness: tuple[str, ...]
    origin: str
    parent: str | None

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError(f"秩必须 ≥ 1，得到 {self.rank}")
        if self.origin not in (ORIGIN_EXOGENOUS, ORIGIN_SPLIT, ORIGIN_EMERGENT):
            raise ValueError(f"未知来源 {self.origin!r}")
        if self.origin == ORIGIN_EXOGENOUS and self.parent is not None:
            raise ValueError("外生方向不许有父 —— 它是人声明的入口")


@dataclass(frozen=True)
class Event:
    """账本里的一行。**不可变** —— 要改就是再追加一行。"""

    seq: int
    kind: str
    did: str
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ALL_EVENT_KINDS:
            raise ValueError(f"未知事件种类 {self.kind!r}")


class Ledger:
    """只追加的事件账本。

    内部留一份 `_digest` 快照 —— 不是为了加速，是为了**让改写可检测**：
    `verify_append_only()` 拿当前内容和快照逐条比，
    任何一条被改过都会被抓到（§B9）。
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._digest: list[tuple] = []
        self._seq = 0

    # --- 写 ---------------------------------------------------------------

    def append(self, kind: str, did: str, **detail: Any) -> Event:
        ev = Event(seq=self._seq, kind=kind, did=did, detail=dict(detail))
        self._events.append(ev)
        self._digest.append(_fingerprint(ev))
        self._seq += 1
        return ev

    # --- 读 ---------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterable[Event]:
        return iter(self._events)

    def events_for(self, did: str) -> list[Event]:
        return [e for e in self._events if e.did == did]

    def kinds_of(self, did: str) -> list[str]:
        return [e.kind for e in self.events_for(did)]

    def status_of(self, did: str) -> str:
        """当前状态 = 把事件折一遍。

        **失效是叠加在历史之上的**，不是替换它 —— 所以「曾被强制」
        和「从未被强制」在这里天然可分：
        前者有 `born` + `invalidated`，后者**一条都没有**。

        ⚠️ **折叠读的是 `members_after == 0`，不是「出现过 `invalidated`」。**
        删除流程（§10.2 C）给 `Cone(x)` 上**每个**方向都追加一条 `invalidated`
        —— 它记的是「**x 不再强制它**」，不是「这个方向死了」。若按「出现过」折，
        删任何一项都会把整条路径标成「已失效」，而那些方向**明明还活着**。
        两种折法在「只删过一次、删的又是唯一成员」时**输出逐字相同** ——
        正是本仓库一直在防的那个形状。所以判据要读**支持集**，不读**事件种类**。
        """
        kinds = self.kinds_of(did)
        if not kinds:
            return "不存在"
        if any(e.kind == EVENT_INVALIDATED and e.detail.get("members_after") == 0
               for e in self.events_for(did)):
            return "已失效（支持集空了）"
        return "生效"

    # --- 校验 -------------------------------------------------------------

    def verify_append_only(self) -> list[str]:
        """§B9 的判据。返回被改写的位置（空 = 干净）。"""
        bad: list[str] = []
        if len(self._events) != len(self._digest):
            bad.append(f"长度不符：事件 {len(self._events)} 条，快照 {len(self._digest)} 条")
            return bad
        for i, ev in enumerate(self._events):
            if _fingerprint(ev) != self._digest[i]:
                bad.append(f"第 {i} 条被改写：seq={ev.seq} kind={ev.kind} did={ev.did}")
        return bad

    def snapshot(self) -> list[tuple]:
        """给「变异检测」用的副本 —— 见 `checks/structure.py` B9。"""
        return list(self._digest)


def _fingerprint(ev: Event) -> tuple:
    """事件的指纹。`detail` 排序后再比，避免字典序影响。"""
    return (ev.seq, ev.kind, ev.did, tuple(sorted((k, repr(v)) for k, v in ev.detail.items())))

def did_order(did: str) -> tuple[int, str]:
    """方向 id 的**规范化**顺序：`D12` 排在 `D3` 后面（按数值，不按字典序）。

    ★ 只有**一处**实现 —— 原来 `kernel` 与 `retriever` 各一份，且**边界不同**：
      `kernel` 用 `did[1:].isdigit()`（严格）；`retriever` 用 `did.lstrip("D")`，
      而 `lstrip` 会剥掉**所有**前导 `D` ⇒ `DD5` 会被当成 `5` 排到中间。
      这里取严格版：非 `D<数字>` 一律排到最后。
    ⚠️ 这只是「同一个集合只有一种写法」（§8.2 可复现性）—— **不是**度量顺序，
       更**不是**遍历顺序。
    """
    if did.startswith("D") and did[1:].isdigit():
        return (int(did[1:]), did)
    return (1 << 30, did)
