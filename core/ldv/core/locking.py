"""并发 —— `§10.2 D` 的 **P8**：锁沿**路径**局部化，**根**是公共争用点。

---

## 这一层要守的那一句话

    **树性给的是锁的作用域**：改一个方向只需接触**它那一个父**
    ⇒ 锁沿着**路径**局部化，**不会级联到无界个前驱**。

反面（`§10.2 D` 逐字）：若按「子 → 父」那条边加锁，改一个父就要锁住**扇出个**子，
而扇出不常数 ⇒ 锁的集合**无界**，并发写会级联 ——
这与 node-copying 断掉是**同一个原因**（Driscoll 等 1989 §6 未解决问题之四）。

外部印证（Lehman & Yao 1981，*Efficient Locking for Concurrent Operations on B-Trees*）：

> Deadlock freedom is guaranteed by the **well-ordering of the locking scheme**, as shown below.

> The solution given above for insertion uses **at most a constant number of locks** (three)
> for any process at any time.

⇒ 两条可执行的后果，本模块各给一条判据：

    **良序**      下降拿锁的顺序 = **秩递增**，与 `remove` 一次性拿锥的顺序**同一个**
                  ⇒ 不死锁（`hold()` 显式按 `_did_order` 排序）
    **有界**      一个写操作拿的锁 = **它自己的锥**（含根），
                  **不**随扇出变 ⇒ 对照「扇出锁」会红

---

## ⚠️ 「作用域 = 锥」**不蕴含**「线程安全」

这两件事必须分开，否则又是「空转与通过长得一模一样」：

    作用域    哪些**方向**要锁 —— 由锥决定，**本模块的判据守的就是这个**
    线程安全  所有**共享可变状态**都进了某个临界区 —— **更大的**一件事

⇒ 结构锁（作用域 = 锥）之外，还有两处**粒度 O(1)** 的短临界区，它们
**不是结构锁**、也**不随扇出变**：

    `Kernel._new`      方向编号的分配（`_counter += 1` + `_dirs[did] = d`）
    `Ledger.append`    账本只追加（`_seq += 1` + 两次 `list.append`）

    ⚠️ 它们**没有**被「锥」覆盖 —— 两个互不相交的锥**也**会在这里相遇。
       把这两处算进「根是唯一公共争用点」里就**说错了**：根不是唯一的相遇点，
       编号分配与账本追加**也是**。⇒ 结构锁的判据只守前者；
       后者**单独列出来**，并另配一条**链路**判据（见下）。

★ **2026-10-11 更正**：这两处**不是**「没做」—— `LockedKernel` 里它们
**已经各有一把短锁**（`_new` 的 `_alloc`、`_LockedLedger.append`）。
★ 同日**补了链路判据**（`test_lock_scope` ⑦）：把锁换成**计数锁**跑一遍真装配，
要求它**真的被进过** —— 实测 `_alloc` 进过 **25** 次、`_lk` 进过 **44** 次；
两个「错一处」对照（`_new` 绕过 `_alloc` / 账本不进临界区）⇒ **0** ⇒ 判据会红。
⚠️ 但 barrier **互斥**探针（④）只覆盖 `PathLocks` —— 这两处**不该**做 barrier 探针：
各是一把普通 `threading.Lock`，探「互斥吗」是**同义反复**。真正没做的是**接线**：
`LockedKernel` 是一层**可选的壳**，生产路径（`run_checks` / `cli` / `flow`）没接。

⚠️ **仍未覆盖的**（不声称）：插件自身的可变状态（`reach` 的缓存是**同一个实例**共享的）、
   `Kernel.items` / `_path` 的单键写（CPython 下单次 dict 写是原子的，但**不是**契约）。
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Sequence

from .direction import Ledger
from .kernel import Kernel, _did_order


class PathLocks:
    """按**方向 id** 一把一把的锁。作用域由**锥**决定。

    ⚠️ 锁是 `RLock`，不是 `Lock`：`insert` 在锥上已经握着 `d`，
    而 `expand(d)` 会**再进一次**同一把（`expand` 自己也守 —— 因为 `query` 会调它）。
    重入计数由 `RLock` 承担；用 `Lock` 会**自死锁**。
    """

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        #: **只报不判**：真的拿过哪些锁（`(线程, 方向)`）。
        #: 作用域判据与读数都从它读 —— 读**实际拿的**，不读「应该拿的」。
        self.taken: list[tuple[int, str]] = []

    # --- 锁本身 -----------------------------------------------------------

    def _lock_for(self, did: str) -> threading.RLock:
        with self._mu:
            lk = self._locks.get(did)
            if lk is None:
                lk = self._locks[did] = threading.RLock()
            return lk

    def guard(self, did: str) -> "_Guard":
        """进出一个方向的锁。`Kernel._step_guard` 的落地形态。"""
        return _Guard(self, did)

    def hold(self, dids: Iterable[str]) -> "_ManyGuard":
        """一次拿一批，**按固定全局次序**（`_did_order`）⇒ 良序 ⇒ 不死锁。

        `remove` 走这条：锥是**已知的**（插入时记下的），不必一个个拿。
        """
        return _ManyGuard(self, sorted(set(dids), key=_did_order))

    # --- 作用域 -----------------------------------------------------------

    @staticmethod
    def scope(cone: Iterable[str]) -> frozenset[str]:
        """★ **作用域 = 锥** —— `§10.2 D` 的 P8 那句话，就是这一行。

        它这么短，是因为**结构**已经替它做了事：树性（每方向恰好一个父）保证
        「改一个方向只需接触它那一个父」，所以作用域里**只有**路径上的方向，
        不会有「扇出个兄弟」。换成 DAG，这一行就得变成 `锥 ∪ 所有前驱的锥`。
        """
        return frozenset(cone)


class _Guard:
    """`with locks.guard(did):` —— 拿一把、放一把。"""

    __slots__ = ("_mgr", "_did", "_lock")

    def __init__(self, mgr: PathLocks, did: str) -> None:
        self._mgr, self._did, self._lock = mgr, did, None

    def __enter__(self) -> "_Guard":
        self._lock = self._mgr._lock_for(self._did)          # noqa: SLF001
        self._lock.acquire()
        self._mgr.taken.append((threading.get_ident(), self._did))   # noqa: SLF001
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._lock.release()
        return False


class _ManyGuard:
    """`with locks.hold([...]):` —— 一次拿一批（**已排好序**）。"""

    __slots__ = ("_mgr", "_dids", "_held")

    def __init__(self, mgr: PathLocks, dids: Sequence[str]) -> None:
        self._mgr, self._dids, self._held = mgr, dids, []

    def __enter__(self) -> "_ManyGuard":
        for did in self._dids:
            lk = self._mgr._lock_for(did)                    # noqa: SLF001
            lk.acquire()
            self._mgr.taken.append((threading.get_ident(), did))     # noqa: SLF001
            self._held.append(lk)
        return self

    def __exit__(self, *exc: Any) -> bool:
        for lk in reversed(self._held):
            lk.release()
        self._held.clear()
        return False


# ═══ 内核的那层壳 ════════════════════════════════════════════════════════════

class _LockedLedger(Ledger):
    """账本只追加 —— `_seq += 1` 与两次 `list.append` 要在一个临界区里。"""

    def __init__(self) -> None:
        super().__init__()
        self._lk = threading.Lock()

    def append(self, kind: str, did: str, **detail: Any):  # type: ignore[no-untyped-def]
        with self._lk:
            return super().append(kind, did, **detail)


class LockedKernel(Kernel):
    """把 `Kernel._step_guard` 换成真的锁。**只多这一件事**（外加两处 O(1) 短临界区）。

    ⚠️ **构造参数多一个 `locks=`，这不算违反 §4.3**：那条管的是 `Kernel` **本体**
    （「构造时只接受一个插件和一批项」，多出 `weights=` / `objective=` 才该 `B12` 红）。
    并发是**调用方**的事 —— 内核不知道锁，这里只是调用方套的一层壳。
    `B12` 扫的是 `core/*.py` 里的**字段名**，不是构造签名。
    """

    def __init__(self, plugin: Any, items: dict[str, Any],
                 locks: PathLocks | None = None) -> None:
        super().__init__(plugin, items)
        self.locks: PathLocks = locks if locks is not None else PathLocks()
        #: 方向编号分配的锁 —— 见模块 docstring 的 ⚠️ 那一节（**不是**结构锁）。
        self._alloc = threading.Lock()
        self.ledger = _LockedLedger()

    def _step_guard(self, did: str) -> Any:
        return self.locks.guard(did)

    def _new(self, **kw: Any):  # type: ignore[no-untyped-def]
        with self._alloc:
            return super()._new(**kw)


# ═══ 三个「错一处」的对照实现 ═════════════════════════════════════════════════
#
# ⚠️ 它们是**对照**，不是备选方案。作用：让 `test_lock_scope` 的每条判据
#    都能被证明「会红」—— 空转与通过长得一模一样，不配对照就分不开。

class GlobalLocks(PathLocks):
    """对照①：**全局一把锁**。作用域不再是锥，而是「所有方向」。"""

    def guard(self, did: str) -> "_Guard":
        return _Guard(self, "*")

    def hold(self, dids: Iterable[str]) -> "_ManyGuard":
        return _ManyGuard(self, ["*"])


class FanoutLocks(PathLocks):
    """对照②：锁 `d` 时**连带锁住它所有的子**。

    这正是 `§10.2 D` 说的「按**子 → 父**那条边加锁」的形状：
    改一个父要锁住**扇出个**子 ⇒ 锁集合**随扇出变** ⇒ 无界。
    """

    def __init__(self, kids_of) -> None:  # noqa: ANN001
        super().__init__()
        self._kids_of = kids_of

    def guard(self, did: str) -> "_ManyGuard":
        return _ManyGuard(self, sorted({did, *self._kids_of(did)}, key=_did_order))

    def hold(self, dids: Iterable[str]) -> "_ManyGuard":
        want: set[str] = set()
        for did in dids:
            want |= {did, *self._kids_of(did)}
        return _ManyGuard(self, sorted(want, key=_did_order))


class NoLocks(PathLocks):
    """对照③：**什么都不锁** —— 互斥探针的已知答案（它必须红）。"""

    def guard(self, did: str) -> "_Noop":
        return _NOOP

    def hold(self, dids: Iterable[str]) -> "_Noop":
        return _NOOP


class _Noop:
    __slots__ = ()

    def __enter__(self) -> "_Noop":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


_NOOP = _Noop()


__all__ = ["FanoutLocks", "GlobalLocks", "LockedKernel", "NoLocks", "PathLocks"]
