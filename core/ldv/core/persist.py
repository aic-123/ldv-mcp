"""持久化 —— `§10.2 D` 的 **P6**：落盘**只认 `_children` 为权威边**。

---

## 这一层要守的那一句话

    `parent` / `witness` **从 `_children` 重算，不独立存**。

内核里**同时物化了同一个关系的两条边**（`§10.2 D` 的表）：

    `_children[父] = (子, …)`    语义边：**父 → 子**，入度 **1，常数**
    `Direction.parent` / `.witness`   存储边：**子 → 父**（反指针），入度 = **扇出**

两条边**互为转置**。只要两者一致，存哪条都行；**一旦各存一份，两者就可能漂移** ——
而漂移之后的症状正是「node-copying 悄悄按错了那条边」：检查还在、还绿，代价已经换了
（`§10.2 D` 的 ⚠️ 原文）。

⇒ 所以落盘格式里**只有一个方向的边**（`_children`），另一条**读回时现算**。
这不是省空间，是**消灭一个可以不一致的状态**。

---

## 定位：**内部快照**，不是长期数据格式

这一句要写在最前面，因为它决定下面所有取舍：

- **字段是语义的，不是实现的**：存档里只有 `did` / `rank` / `payload` / `origin`
  + `子` / `成员` / `已展开` / `判过` / `锥` / `项` / `账本` / `使用` ——
  **没有类名、没有模块名、没有 Python 字段布局**。重命名一个类、拆一个数据类，
  都打不乱旧存档（类的形状变了，字段的语义没变）。
- **但字段语义是本实现的内部概念**（`锥` = 项所在的锥、`判过` = 试过的成员集），
  不是跨系统交换格式。改这些概念的含义，旧存档就该拒读。
- ⇒ 它是**可从语料 + 建树过程重建的内部快照**，**不承诺跨大版本可读**。
  `FORMAT` 号是那道门：不符即拒，不猜、不迁移。
- 如果哪天它要升级成**正式数据格式**，那时才需要：独立 schema、版本迁移规则、
  未知字段策略、兼容性测试。**现在没有这些，也不该有** ——
  为不存在的需求先建兼容层，就是把内部缓存供成协议。

⚠️ 「存档能读回」与「设计完整」是两件事：读回只证明**这一份**自洽，
   防不住不可信来源（见下面 `_load` 对「不是本格式写出来的」的处理）。

---

## 「不落盘」这条纪律在这里有三处，形态不同、理由同源

    1. `parent` / `witness`    可从 `_children` 重算          ⇒ 不存（**本模块的主语**）
    2. `_shown` / `_last_frontier`  **会话态**，不是结构态      ⇒ 不存（见下）
    3. `reach` 插件的距离/可达缓存  **派生状态**，可从图重算      ⇒ 不存（插件自己的事）

第 3 处特别值得点出来：它是**同一个纪律在另一层**上的实例 ——
`§10.2 D` 说「派生数据不许有两份真相」，那条规矩**不因为是缓存就失效**。
读回之后缓存是**冷的**，但 `代价` 读数必须**逐项相同**（`test_persistence` 查了这条）。

---

## ⚠️ 落盘的是**结构**，不是**会话**

`_shown`（本轮展示了什么、什么次序）与 `_last_frontier`（遍历经过的最后一层）
是**会话态** —— 一个刚读回来的内核**这一轮什么都没展示过**，
把上一轮的展示次序灌进去等于**替它伪造曝光位次**，而倾向权重正是从位次算的（§8.1）。

⇒ 两者**不进存档**。往返比对也因此**显式排除**它们（不是忘了，是裁定过）。

---

## 为什么读回时也要查「恰好一个父」

`§10.2 D` 的 P4（树性）**在内存里**由 `B13` 守着。但落盘/读回是**第二个入口**：
一份**权威边自相矛盾**的存档（同一个子出现在两个父的子列表里）若被静默接受，
读回来的就是一个 DAG —— 而 `B13` 只在下一次被调用时才看得见它。

⇒ `derive_parents` **当场报错**（`PersistenceError`），不静默取一个。
「静默取一个」与「这份存档是好的」在读回来的结构上**长得一模一样**。
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .direction import (
    Event,
    Ledger,
    ORIGIN_EXOGENOUS,
    Direction,
)
from .interfaces import WeightedRecord
from .kernel import Kernel

#: 存档格式号。**读回时要核** —— 一份没带格式号的存档与一份格式号写错的存档，
#: 在「读回来是什么」上长得一样；核一下就把它们分开了。
FORMAT = "ldv-kernel/1"


class PersistenceError(RuntimeError):
    """存档不能用 —— 格式不符，或**权威边不是树**。"""


# ═══ 编解码：**保形**，不是保「能读」 ══════════════════════════════════════════
#
# `payload` 是**插件定义的不透明内容**（A 是 `(frozenset, frozenset)`、
# B 是 `frozenset[str]`、C 是 `frozenset[tuple]`）。`frozenset` 与 `list`、
# `tuple` 与 `list` 在 `==` 下**不相等**，所以「编成 JSON 再读回来」若把类型丢了，
# 往返比对会红 —— 而那个红是**编解码器的**，不是持久化设计的。
#
# ⇒ 打标记编码：`{"$t": [...]}` = 元组、`{"$fs": [...]}` = frozenset、
#   `{"$s": [...]}` = set、`{"$d": [[k, v], ...]}` = 字典。**四种容器都保住**。
#
# ⚠️ **尺度**：本编解码器覆盖的是**三个真实插件 + 语料项**用到的类型
#   （`str` / `int` / `float` / `bool` / `None` / `list` / `tuple` / `set` /
#    `frozenset` / `dict` / **冻结数据类**）。别的类型（打开的文件、句柄、
#    自定义类且没加 `@dataclass`）**会报错**，不静默降级
#   —— 「编不进去」与「编进去了但读回来不一样」必须分得开。
#
# ⚠️ **冻结数据类按 `模块.类名` 重建**（`Node` 走的就是这条）。
#   ⇒ 存档字符串**能决定**读回过程 import 哪个模块、构造哪个类 ——
#   所以**必须**有白名单（见 `ALLOWED_DATACLASSES`），不能「导入得了就算」。
#   格式号（`FORMAT`）是那道门，但它**不是沙箱**：不可信来源的存档要走别的路，
#   本模块不声称能处理那种输入 —— 但现在这条边界**是机制，不只是文档**。

#: 解码允许的**资源上限**（外部审计 2026-10-23 点名的缺口）：不可信存档哪怕
#: 不执行任何代码，也能用「无限嵌套 / 海量小容器」把解释器拖死。
#: ⇒ 深度与容器数各设一道门，**超了当场抛**，不静默截断。
#:
#: ⚠️ 两个数都是**暂定未标定**（与 `STAGE_CAP` 同一条纪律）：真实存档最深约 8 层
#:    （Node → fields → 列表 → 字符串），容器数上界由语料规模定。
#:    取值刻意留头 —— 它们是**防拖死**的门，不是精度边界。
MAX_DECODE_DEPTH = 64
MAX_DECODE_NODES = 2_000_000

#: 允许按 `模块.类名` 重建的数据类**白名单**。
#:
#: ★ 为什么必须是白名单（而不是「能 import 就重建」）：那等于让**存档字符串**
#:   决定 import 哪个模块、构造哪个类、传什么 kwargs。docstring 一直写着
#:   「存档是可信输入」，但**边界不能只是文档** —— 调用边界一扩大
#:   （服务接口、租户交换、上传恢复），这就是一个代码执行形状的原语。
#: ⇒ 显式列名：目前只有 `Node`（语料项经 `as_item()` 进 `items`）；
#:   方向 payload / 账本 detail / 使用记录 extra 走的都是内置容器与标量。
#:   要加新类型 ⇒ 显式加进来，并配往返判据 —— 不许靠「导入得了」。
ALLOWED_DATACLASSES = frozenset({
    "ldv.corpus.loader.Node",
})

_SCALARS = (bool, int, float, str)


class _Budget:
    """解码的**资源账** —— 容器个数上限（`_dec` 递归时一路带着）。"""

    __slots__ = ("left",)

    def __init__(self, cap: int = MAX_DECODE_NODES) -> None:
        self.left = cap

    def spend(self) -> None:
        self.left -= 1
        if self.left < 0:
            raise PersistenceError(
                f"存档里的容器超过 {MAX_DECODE_NODES} 个 —— 拒收（资源上限，"
                "「读得慢」与「读得死」必须分得开）")


def canon(v: Any) -> str:
    """**规范形式** —— 用来比对，也用来定序（**不**是存档格式本身）。

    ⚠️ `repr(frozenset(...))` **不是规范形式**：它的迭代次序取决于
    **插入历史**，两个元素完全相同的 frozenset 可以印出**不同的串**。

        实测：同一份 keyset 结构，往返之后 `repr(payload)` 与原件**逐字不同** ——
        差异只是 `{'type=概念', 'source.kind=论文', …}` 里两个元素的**先后**。
        而 `payload` 本身 `==` 相等。

    ⇒ 拿 `repr` 当指纹，会把「顺序不同」读成「内容不同」——**假红**。
      而它更坏的一面是**假绿**：`_enc` 里若用 `repr` 定序，
      **同一份结构会产出不同的存档字节**，于是「存档稳定」这件事就没人验得了。

    ⇒ 所以：容器一律**先规范再比**（集合按内容排序、字典按键排序）。
    """
    if isinstance(v, (set, frozenset)):
        return "{" + ",".join(sorted(canon(x) for x in v)) + "}"
    if isinstance(v, tuple):
        return "(" + ",".join(canon(x) for x in v) + ")"
    if isinstance(v, list):
        return "[" + ",".join(canon(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(f"{canon(k)}:{canon(x)}"
                              for k, x in sorted(v.items(), key=lambda kv: canon(kv[0]))) + "}"
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return (type(v).__name__ + "("
                + ",".join(f"{f.name}={canon(getattr(v, f.name))}"
                           for f in dataclasses.fields(v)) + ")")
    return repr(v)


def _dc_name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _dc_lookup(name: str) -> type:
    """按名字取数据类 —— **先过白名单，再 import**。

    ⚠️ 顺序不能反：先 import 再判断，等于已经执行了存档选中的模块。
    """
    if name not in ALLOWED_DATACLASSES:
        raise PersistenceError(
            f"存档要重建数据类 {name!r} —— 不在白名单里"
            f"（当前允许：{sorted(ALLOWED_DATACLASSES)}）。"
            "「不可信存档不许决定 import 哪个模块」：要支持新类型，"
            "请显式加进 `ALLOWED_DATACLASSES` 并配往返判据")
    import importlib
    mod, _, qual = name.rpartition(".")
    try:
        return getattr(importlib.import_module(mod), qual)
    except (ImportError, AttributeError) as e:
        raise PersistenceError(
            f"白名单里的数据类 {name!r} 居然导入不了：{e} —— "
            "白名单条目与实现不同步，改代码改白名单，两处一起改"
        ) from e


def _enc(v: Any) -> Any:
    if v is None or isinstance(v, _SCALARS):
        return v
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {"$dc": _dc_name(type(v)),
                "$f": {f.name: _enc(getattr(v, f.name))
                       for f in dataclasses.fields(v)}}
    if isinstance(v, tuple):
        return {"$t": [_enc(x) for x in v]}
    if isinstance(v, frozenset):
        return {"$fs": sorted((_enc(x) for x in v), key=canon)}
    if isinstance(v, set):
        return {"$s": sorted((_enc(x) for x in v), key=canon)}
    if isinstance(v, list):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {"$d": [[_enc(k), _enc(x)] for k, x in sorted(v.items(), key=lambda kv: canon(kv[0]))]}
    raise TypeError(
        f"持久化层编不了 {type(v).__name__} —— 覆盖范围见模块 docstring 的「尺度」段；"
        "「编不进去」与「读回来不一样」必须分得开，所以这里报错而不是降级"
    )


def _dec(v: Any, depth: int = 0, budget: "_Budget | None" = None) -> Any:
    if budget is None:
        budget = _Budget()
    if depth > MAX_DECODE_DEPTH:
        raise PersistenceError(
            f"存档嵌套深于 {MAX_DECODE_DEPTH} 层 —— 拒收（资源上限）")
    if isinstance(v, list):
        budget.spend()
        return [_dec(x, depth + 1, budget) for x in v]
    if not isinstance(v, dict):
        return v
    budget.spend()
    if "$dc" in v:
        cls = _dc_lookup(v["$dc"])
        if not dataclasses.is_dataclass(cls):
            raise PersistenceError(
                f"白名单条目 {v['$dc']!r} 不是数据类 —— 白名单写错了")
        return cls(**{k: _dec(x, depth + 1, budget) for k, x in v["$f"].items()})
    if "$t" in v:
        return tuple(_dec(x, depth + 1, budget) for x in v["$t"])
    if "$fs" in v:
        return frozenset(_dec(x, depth + 1, budget) for x in v["$fs"])
    if "$s" in v:
        return set(_dec(x, depth + 1, budget) for x in v["$s"])
    if "$d" in v:
        return {_dec(k, depth + 1, budget): _dec(x, depth + 1, budget)
                for k, x in v["$d"]}
    raise ValueError(f"存档里出现未标记的字典 {sorted(v)[:3]} —— 不是本格式写出来的")


# ═══ 权威边 → 派生边 ══════════════════════════════════════════════════════════

def derive_parents(children: dict[str, tuple[str, ...]],
                   root: str) -> dict[str, str | None]:
    """**从权威边算派生边** —— 本模块唯一的那个重算函数。

    这就是 `§10.2 D` 裁决里那句「`parent` / `witness` 是**可从 `_children`
    重算的派生索引**」的落地。`witness` 不用单独算：`§6.1` 定死了
    `子.witness == (父,)`、根 `.witness == ()` ⇒ 它是 `parent` 的函数。

    ## 两条当场报错（**不静默取一个**）

        同一个子出现在两个父的子列表里   ⇒ 语义入度 2 ⇒ 这份存档是 DAG，不是树
        `_children` 里出现没在 `方向` 表里的编号  ⇒ 悬空引用

    ⚠️ 报错而不是「取第一个」：**静默取一个**与**这份存档本来是好的**
    在读回来的结构上长得一模一样 —— 而 `§10.2 D` 的持久化路线**押在入度上**。
    """
    parent: dict[str, str | None] = {root: None}
    for p, kids in children.items():
        for c in kids:
            if c in parent:
                raise PersistenceError(
                    f"{c} 有两个父（{parent[c]} 与 {p}）⇒ 权威边不是树 —— "
                    "§10.2 D 的持久化路线押在入度常数上，所以这里当场报错而不是取一个"
                )
            parent[c] = p
    return parent


# ═══ 落盘 ════════════════════════════════════════════════════════════════════

def to_dict(kernel: Kernel) -> dict:
    """把内核写成一份**只带权威边**的存档。

    ⚠️ `方向` 的每一项**只有** `did` / `rank` / `payload` / `origin` ——
    **没有 `parent`、没有 `witness`**。这两条是**算出来的**，落盘一份就多一个
    可以不一致的状态（模块 docstring 开头那一段）。
    """
    k = kernel
    dirs = k.all_directions()
    return {
        "格式": FORMAT,
        "根": k.root.did,
        "计数": k._counter,                                  # noqa: SLF001
        "方向": [{"did": d.did, "rank": d.rank, "payload": _enc(d.payload),
                  "origin": d.origin} for d in dirs],
        "子": {p: list(kids) for p, kids in k._children.items()},      # noqa: SLF001
        "成员": {d.did: sorted(k._members.get(d.did, ()))              # noqa: SLF001
                 for d in dirs},
        "已展开": sorted(k._expanded),                       # noqa: SLF001
        "判过": {did: sorted(m) for did, m in k._tried.items()},        # noqa: SLF001
        "锥": {iid: list(p) for iid, p in k._path.items()},            # noqa: SLF001
        "项": {iid: _enc(v) for iid, v in k.items.items()},
        "账本": [{"seq": e.seq, "kind": e.kind, "did": e.did,
                  "detail": _enc(e.detail)} for e in k.ledger],
        "使用": [{"did": r.did, "outcome": r.outcome, "propensity": r.propensity,
                  "note": r.note, "extra": _enc(r.extra)} for r in k.usage],
    }


# ═══ 读回 ════════════════════════════════════════════════════════════════════

def from_dict(data: dict, plugin: Any) -> Kernel:
    """从存档读回一个内核。**派生边在这里现算。**

    ⚠️ **不读存档里的 `parent` / `witness`** —— 就算有也不读。这一条是本模块的
    全部要点：一份**带派生边且已漂移**的存档，读回来必须**逐字等于原件**
    （`test_persistence` 的注入实验查的就是这条）。
    """
    if data.get("格式") != FORMAT:
        raise PersistenceError(
            f"格式号不符：存档是 {data.get('格式')!r}，本实现认 {FORMAT!r}"
        )

    root_did = data["根"]
    children = {p: tuple(kids) for p, kids in data["子"].items()}
    parent_of = derive_parents(children, root_did)          # ★ 派生边在这里现算
    budget = _Budget()          # 一整份存档共用一份资源账（深度 + 容器数）

    k = Kernel(plugin, {})
    k._counter = data["计数"]                                 # noqa: SLF001

    for row in data["方向"]:
        did = row["did"]
        if did not in parent_of:
            raise PersistenceError(
                f"{did} 在 `方向` 表里，但按权威边（`子`）够不着 —— "
                "存档自相矛盾（悬空方向）"
            )
        p = parent_of[did]
        if row["origin"] == ORIGIN_EXOGENOUS and p is not None:
            raise PersistenceError(f"外生方向 {did} 不许有父，权威边说它有 {p}")
        if row["origin"] != ORIGIN_EXOGENOUS and p is None and did != root_did:
            raise PersistenceError(f"内生方向 {did} 没有父 —— 权威边不是树")
        # ★ 这两行就是「从 `_children` 重算」：`witness` 由 `parent` 定（§6.1）。
        k._dirs[did] = Direction(                            # noqa: SLF001
            did=did, rank=row["rank"], payload=_dec(row["payload"], 0, budget),
            witness=() if p is None else (p,), origin=row["origin"], parent=p)

    k._children = children                                   # noqa: SLF001
    k._root = k._dirs[root_did]                              # noqa: SLF001
    k._members = {d: set(m) for d, m in data["成员"].items()}  # noqa: SLF001
    k._expanded = set(data["已展开"])                         # noqa: SLF001
    k._tried = {d: frozenset(m) for d, m in data["判过"].items()}   # noqa: SLF001
    k._path = {i: tuple(p) for i, p in data["锥"].items()}     # noqa: SLF001
    k.items = {iid: _dec(v, 0, budget) for iid, v in data["项"].items()}

    # 账本**逐条**重放 —— `seq` 由 `Ledger.append` 按序发，所以顺序就是 seq。
    k.ledger = Ledger()
    for i, row in enumerate(data["账本"]):
        if row["seq"] != i:
            raise PersistenceError(f"账本第 {i} 条的 seq 是 {row['seq']} —— 不是按序的")
        k.ledger.append(row["kind"], row["did"], **_dec(row["detail"], 0, budget))

    k.usage = [WeightedRecord(did=r["did"], outcome=r["outcome"],
                              propensity=r["propensity"], note=r["note"],
                              extra=_dec(r["extra"], 0, budget))
                   for r in data["使用"]]
    # ⚠️ `_shown` / `_last_frontier` **不恢复** —— 它们是会话态（见模块 docstring）。
    return k


# ═══ 两个「错一处」的对照实现 ═════════════════════════════════════════════════
#
# ⚠️ 它们是**对照**，不是备选方案。作用：让 `test_persistence` 的每条判据
#    都能被证明「会红」—— 空转与通过长得一模一样，不配对照就分不开。

def _to_dict_storing_derived(kernel: Kernel) -> dict:
    """对照①：落盘时**也存一份派生边** —— `§10.2 D` 说的「各存一份」。

    只多这一件事。它使**漂移成为可能**（不是必然 —— 一份没漂移的双份存档
    读回来照样对，所以「读回来对」这一条**单独不足以**证明只存了一份）。
    """
    data = to_dict(kernel)
    by_id = {d.did: d for d in kernel.all_directions()}
    for row in data["方向"]:
        d = by_id[row["did"]]
        row["parent"] = d.parent
        row["witness"] = list(d.witness)
    return data


def _from_dict_trusting_derived(data: dict, plugin: Any) -> Kernel:
    """对照②：读回时**信任存档里的派生边**，不从 `_children` 重算。

    只多这一件事。存档里没有那两栏时退回重算 ⇒ 与真实现**同输出**
    （所以它只在**带派生边**的存档上才分得开 —— 这正是注入实验要造的形态）。
    """
    k = from_dict(data, plugin)
    for row in data["方向"]:
        if "parent" not in row and "witness" not in row:
            continue
        p = row.get("parent")
        w = tuple(row.get("witness") or ())
        old = k._dirs[row["did"]]                                # noqa: SLF001
        k._dirs[row["did"]] = Direction(                          # noqa: SLF001
            did=old.did, rank=old.rank, payload=old.payload,
            witness=w, origin=old.origin, parent=p)
    return k


def _fingerprint(kernel: Kernel) -> tuple:
    """内核的**外部**指纹 —— 比对用。

    刻意**不用** `to_dict`：拿存档跟自己比会漏掉「两边一起错」的形状。
    这里把**能观察到的结构**摊平成一串可比的元组。

    ## ★ 为什么**不需要**再比「语义」（2026-10-23）

    答案是**结构的函数** —— `kernel.query` 只读下面这些，加上插件代码：

        dirs（did / rank / payload / witness / origin / parent）· children
        members · `_expanded` · `items`

    而指纹**全部覆盖了**（还多出 `_tried` / `_path` / `ledger` / `usage` / `_counter`）
    ⇒ **指纹逐字相同 ⇒ 同查询的答案逐字相同**。这是构造性的，不是取舍。

    ⇒ 再跑一遍 `query` 去比答案，是**同义反复**
      （`docs/评估标准.md` R4：读数不许与别的读数互为函数）。

    ⚠️ 真正该判的是「**指纹有没有覆盖 query 读的那些字段**」——
       判据 `test_fingerprint_sensitivity` 逐类扰动（成员集 / 已展开 / 使用记录 /
       方向 payload），指纹**必须变**；某一类不变，就说明那一类漏了。

    ⚠️ 每一格都走 `canon`，**不走 `repr`** —— 理由见 `canon` 的 docstring
    （`repr(frozenset)` 依赖插入历史 ⇒ 假红，还会让存档字节不稳）。
    """
    dirs = tuple(sorted(
        (d.did, d.rank, canon(d.payload), d.witness, d.origin, d.parent)
        for d in kernel.all_directions()))
    kids = tuple(sorted((p, tuple(v)) for p, v in kernel._children.items()))  # noqa: SLF001
    mem = tuple(sorted((d.did, tuple(sorted(kernel.members_of(d))))
                       for d in kernel.all_directions()))
    return (dirs, kids, mem,
            tuple(sorted(kernel._expanded)),                     # noqa: SLF001
            tuple(sorted((d, tuple(sorted(m))) for d, m in kernel._tried.items())),  # noqa: SLF001
            tuple(sorted((i, p) for i, p in kernel._path.items())),  # noqa: SLF001
            tuple(sorted((i, canon(v)) for i, v in kernel.items.items())),
            tuple((e.seq, e.kind, e.did, canon(e.detail)) for e in kernel.ledger),
            tuple((r.did, r.outcome, r.propensity, r.note, canon(r.extra))
                  for r in kernel.usage),
            kernel._counter)                                     # noqa: SLF001


__all__ = ["FORMAT", "PersistenceError", "canon", "derive_parents", "from_dict", "to_dict"]
