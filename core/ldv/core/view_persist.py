"""视图集合的落盘 / 读回 —— `§A6`（`docs/分层方向视图-抽象层.md` §9 / `§4 E7`）。

    python -m ldv.run_checks            # 判据在 `(视图)` 那一组里

纪律**照抄** `core/persist.py`（`§10.2 D` 那一节），一个字都不新发明：

    落盘只认**一条权威边**；派生边**读回现算**
    ⇒ 派生边漂移**不可能发生**（它根本不在盘上）
    ⇒ 而「不可能发生」这件事要**验**，不能靠说 —— 见 `a6_roundtrip` 与
      `_to_dict_storing_derived` / `_from_dict_trusting_derived` 这一对**对照实现**

---

## 视图的权威边 / 派生边

    权威   `spec`（`U` / `P` / `E`）+ `q` + 每张视图的 `vid` / `block`
    派生   `payload`（= `插件.合并(block)`）· `concretization`（= `覆盖(payload)`）· `covered`

⚠️ **`payload` 也是派生的**，这一点和内核那边不同（内核的 `payload` 由插件声明、
   `§K4` 说方向不可变）。视图的 `payload` 是**从块现算**出来的，所以它**不该进盘**：
   进盘就意味着「盘上的合并结果」与「按 `§I2` 现算的结果」是**两个可以不一致的东西**，
   而它们本可以只有一个。⇒ 盘上只留 `block`，`payload` 读回时重算。

⚠️ **不落 `payload` 还有一个实际理由**：它是**插件定义的不透明内容**，
   内核从不解释它（`Direction` 的 docstring）。视图层没有资格替插件定它的序列化格式。

---

## 为什么 `q` 要单独落

`q` 由 `spec` 决定（`coarsest_stable_refinement`，**唯一**），所以理论上可以读回时重算。
但它同时是 `§A3` 的被判对象 —— 若读回时重算，`§A6` 就**验不到**「盘上的 `q` 是不是那个解」。
⇒ 落盘存 `q`，读回时**先比**（比不出来才红），再拿它去跑 `§A1`。
「存一份再比」在这里是**判据的一部分**，不是冗余。

---

## ★ 跨进程落盘：**原子替换 + 重试**（2026-10-16，并发接口设计 §7 阶段 2）

上面那些说的是**格式**（盘上存什么）。这一节说的是**怎么把字节放上去**。

⚠️ 两件事**不是一回事**，而且**进程内的锁管不到后一件**：

    进程内（`§10.2 D` 的 P8）  锥锁守的是**同一个 `Kernel` 实例**的共享状态
    跨进程（面 B）             两个**独立进程**写同一个路径 —— 没有锁能覆盖它们

⇒ 落盘走 `commit()`：**临时文件 + `os.replace`**（同卷改名 = 原子）。
  `write()` 那一步的语义因此从「**清空再写**」变成「**要么旧整份、要么新整份**」。
  ⚠️ 而 Windows 上「目标正被别的进程持着」会让改名被拒 ⇒ 配**重试**（实测见 `commit`）。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .views import ViewSpec, partition_of

__all__ = [
    "FORMAT",
    "FORMAT_KEY",
    "ViewPersistenceError",
    "default_path",
    "commit",
    "RETRY_ATTEMPTS",
    "to_dict",
    "from_dict",
    "read",
    "write",
]

#: 格式串 —— 与 `core/persist.py::FORMAT` 同一个用途：换了格式要能一眼看出来。
FORMAT = "ldv-views/1"

#: 格式串在字典里的**键名**。★ 一处定义：`to_dict` / `from_dict` / `write` 都取它 ——
#:  2026-10-10 实测踩到：`write` 里写死成 `"format"`（真键名是 `"格式"`）⇒ 一写就抛。
FORMAT_KEY = "格式"

#: 认识的落盘文件名。与 `checks.abstraction.VIEW_SPEC_NAME` / `expected_reds.json`
#: 同一套规矩：**外部语料的数据不进版本库**，就放在语料旁。
#:
#: ⚠️ **2026-10-10 用户裁定的一处例外**：`openalex-citations` 的**外生声明与认识**
#:    （`view_spec.json` + `views.json`）**进版本库**（`.gitignore` 里两条 `!` 例外）。
#:    语料本身（`nodes/` / `raw/`）照旧不进。理由：仓内语料那一对
#:    （`ldv/checks/view_spec.json` + `ldv/checks/views.json`）**本来就在库里** ——
#:    外部那份跟着进，两条链才对称；否则新 clone 上 `T2` 没有「盘上那份」可判。
VIEWS_NAME = "views.json"


def default_path(nodes_dir: Any) -> Any:
    """认识的默认落点 —— **与 `abstraction.spec_paths` 同一条规则**。

        语料在 `ldv/` 包内（仓内默认语料） ⇒ `ldv/checks/views.json`
        语料在包外（`_data/<名>/nodes`）        ⇒ `<语料父目录>/views.json`

    ⚠️ **与 `spec_paths` 必须同源**：认识是**跟着声明**走的（`spec` 是它的版本）——
       声明在一处、认识在另一处，两者就会「看着都对、其实配错」。
       这条规则由 `run_tests.test_spec_paths` 里那条 `[S4]`/`[S5]` 的同族对照守着。
    """
    from pathlib import Path as _P

    nd = _P(nodes_dir)
    pkg_root = _P(__file__).resolve().parents[1]          # ldv/
    try:
        nd.resolve().relative_to(pkg_root)
    except ValueError:
        return nd.parent / VIEWS_NAME                     # 包外 ⇒ 语料旁
    return _P(__file__).resolve().parent.parent / "checks" / VIEWS_NAME


#: 落盘 / 读盘遇到「**别人正拿着这个文件**」时的重试参数。
#:
#: ⚠️ 这三个数**不是拍脑袋**，三条实测与两条成熟做法都写在下面：
#:
#:   `outputs/_probe_win_replace_vs_read.py`（win32）
#:       写者 400 次 `os.replace` ⇒ **350 次被拒**（读者持着目标句柄）
#:       读者 44812 次读        ⇒ 42 次被拒，**撕裂 0 次**
#:   `outputs/_probe_persist_reader_side.py`（win32，**加了写者重试之后**）
#:       写者 300 次 `commit`   ⇒ 失败 **0**（299/300 需要重试 >1 次，**max 15**）
#:       读者 7266 次 `read`    ⇒ **18 次被拒** ⇒ 光有写者重试**只补了一半**
#:
#:   成熟做法（两条独立来源，结论一致 —— **有界重试 + 指数退避 + 只重试瞬时错误**）：
#:       Los Lab「Windows 原子文件发布」：8 次，退避 1·2·4·8·16·16·16 ms（上限 63 ms）
#:       Codex 同步工具：20 × 100 ms（上限 ~2 s），`winerror ∈ {5, 32}` 才重试，其余立刻抛
#:
#: ⇒ 取 **12 次 × 1 ms 起、翻倍、封顶 16 ms**（预算 ≈ **143 ms**）——
#:   比上面两家都宽一点：本仓库的读者要读的是一份**完整存档**（不是一行），持柄时间更长。
RETRY_ATTEMPTS = 12
RETRY_BASE_PAUSE = 0.001
RETRY_MAX_PAUSE = 0.016


def _transient(e: OSError) -> bool:
    """这个 `OSError` 是「**别人正拿着这个文件**」那种**瞬时**冲突吗？

    ⚠️ **只在 Windows 上判为真**，理由是一条实测事实：

        POSIX 的 `os.replace` 是**原子改名**，读者持的是**旧 inode**
        ⇒ 它**不会**因为「有人读」而失败；那里的 `PermissionError` 是
        **真·权限不足**（目录只读之类）⇒ 重试只会白等 143 ms。

        Windows 的 `MoveFileEx(MOVEFILE_REPLACE_EXISTING)` 要求对**目标**有删除权，
        而 Python 的 `open()` 走 CRT、**不含 `FILE_SHARE_DELETE`**
        ⇒ 有人读着就拒（实测 350/400）。

    ⚠️ Windows 上**不**细看 `winerror`：CPython 把 `ERROR_ACCESS_DENIED`(5) /
        `ERROR_SHARING_VIOLATION`(32) / `ERROR_LOCK_VIOLATION`(33) **都**映射成 `EACCES`
        ⇒ 到了 Python 这一层已经分不开；而成熟做法（Codex）对这三个也都是重试。
        ⚠️ 代价如实记着：Windows 上「真的没有权限」也会被重试满 143 ms 才抛。
    """
    import os

    return os.name == "nt" and isinstance(e, PermissionError)


def _pause(attempt: int) -> float:
    """第 `attempt` 次失败之后等多久 —— **指数退避**，封顶 `RETRY_MAX_PAUSE`。"""
    return min(RETRY_BASE_PAUSE * (1 << (attempt - 1)), RETRY_MAX_PAUSE)


def commit(p: Any, body: str) -> tuple[Any, int]:
    """把 `body` **原子地**放到 `p` 上 —— 并发接口设计 §7 阶段 2 的落点。

    返回 `(目标路径, 实际尝试次数)`。⚠️ **次数是读数**：POSIX 上恒为 **1**，
    Windows 上会 > 1 —— 它把「这台机器上有没有并发读者」变成一个能看的数。

    ## 为什么不能是 `p.write_text(body)`

    `open(p, "w")` 返回的那一刻**目标路径已经被截断**（长度 0）。于是盘上
    存在一个**确定的中间态**：`p` 既不是旧整份、也不是新整份。这个窗口里

        另一个进程读   ⇒ 读到半份（JSON 解析抛，或字段缺一半）
        写入中途失败   ⇒ 盘上**永久**停在半份（截断已经生效了）

    ⇒ 这两条**不是概率问题**，是那条路径上的**必然状态**。两个进程同时落盘也归到这里
      （「后写者覆盖前者」只是**最轻**的那一种后果）。

    ## 取法：临时文件 + `os.replace`

    `os.replace` 是**同一卷内**的原子改名 ⇒ 读者看到的 `p` **要么是旧整份、
    要么是新整份**，没有第三种。⚠️ 所以临时文件必须建在**目标同目录**
    （`dir=target.parent`）—— 跨卷时 `os.replace` 会退化成「复制 + 删」，就不再原子。

    ## ⚠️ Windows 上必须重试（实测逼出来的一条）

    Windows 的文件共享模型与 POSIX 不同：`MoveFileEx(MOVEFILE_REPLACE_EXISTING)`
    要求对**目标**有删除权，而 Python 的 `open()` 走 CRT、**不含 `FILE_SHARE_DELETE`**
    ⇒ **只要另一个进程正读着这个文件，改名就会被拒**（`PermissionError [WinError 5]`）。

        实测（win32，`_probe_win_replace_vs_read.py`）：
            写者 400 次 `os.replace`   ⇒ **350 次被拒**
            读者 44812 次读           ⇒ 42 次被拒，**撕裂 0 次**
        （POSIX 上不会：读者持的是**旧 inode**，改名永远成功。）

    ⇒ 重试**不改变原子性**（每一次 `os.replace` 都是原子的），它只是
      「上一次被拒了，再来一次」。**不重试**的后果是「有读者在读 = 写不进去」，
      而那在 Windows 上是**常态**。

    ## 失败时的行为（这一条**本身就是判据**）

    写临时文件那一步抛（编码错 / 盘满 / 权限）⇒ 临时文件被删、`os.replace`
    **没被调到** ⇒ `p` **原封不动**。⚠️ 这与非原子实现**正好相反**：
    `p.write_text(body)` 在 `body` 编不出来时**也会先把 `p` 清空**。
    """
    from pathlib import Path as _P
    import os
    import tempfile
    import time

    target = _P(p)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent,
                               prefix=target.name + ".", suffix=".tmp")
    attempt = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())          # 先落盘，再改名 —— 顺序反了等于没做
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                os.replace(tmp, target)    # ★ 原子：同卷改名
                break
            except PermissionError as e:   # ← Windows：目标正被别的进程持着
                if not _transient(e) or attempt == RETRY_ATTEMPTS:
                    raise
                time.sleep(_pause(attempt))
    except BaseException:
        try:
            os.unlink(tmp)                 # 目标**没被动过**，这里只收拾临时文件
        except OSError:
            pass
        raise
    return target, attempt


def write(path: Any, vs: Any) -> Any:
    """落盘。**只写权威边**（`to_dict` 的规矩），并**先过一遍读回**再返回。

    ⚠️ 「先读回」不是洁癖：写坏的存档与写对的存档在**只看文件存在**时长得一样。
       ⇒ 写完当场用 `from_dict` 的**构造部分**验一次结构（不重建视图，那要内核）。

    ★ 2026-10-16：落盘改走 `commit()`（**原子替换 + 重试**）—— 见它的 docstring。
      ⚠️ 这是**跨进程**那一面（面 B），与进程内的锥锁（P8）**不是一回事**。
    """
    from pathlib import Path as _P

    p = _P(path)
    body = dumps(vs)
    commit(p, body)
    # ⚠️ 键名从 `to_dict` **一处取**（原来在这里写死了一个 `"format"` —— 而真键名是
    #    `"格式"` ⇒ 写一次就抛 `ViewPersistenceError`）。**两处各写一份就会这样。**
    back = json.loads(p.read_text(encoding="utf-8"))
    if back.get(FORMAT_KEY) != FORMAT:
        raise ViewPersistenceError(
            f"落盘后格式串不对：{back.get(FORMAT_KEY)!r} ≠ {FORMAT!r}")
    return p


def read(path: Any) -> dict:
    """读盘（返回**原始字典**）—— 重建成 `ViewSet` 要内核，那是调用方的事。

        文件不在 ⇒ `{}`（与 `abstraction.load_spec_file` 同一个约定：
                     「还没写」与「写坏了」必须分得开 —— 后者由 JSON 解析抛异常）

    ★ 2026-10-16：**读者侧也要重试** —— 这是「读者 vs 替换」的另一半。
      ⚠️ 为什么不能只靠写者重试：`os.replace` 在 Windows 上把目标短暂置成
      **「删除挂起」**，那一瞬间别的进程 `open` 它拿到的是 `ERROR_ACCESS_DENIED`
      —— **写者重试救不了读者**（实测读者 7266 次里 18 次被拒，而同期写者 300/300 成功）。

    ⚠️ **重试的边界**：只重试 `PermissionError`（且只在 Windows 上）。
      `json.JSONDecodeError` **不重试** —— 「读的时候被撞了」与「盘上那份本来就是坏的」
      必须分得开，后者重试多少次都一样。
    """
    import time
    from pathlib import Path as _P

    p = _P(path)
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            if not p.is_file():
                return {}
            return json.loads(p.read_text(encoding="utf-8"))
        except PermissionError as e:
            if not _transient(e) or attempt == RETRY_ATTEMPTS:
                raise
            time.sleep(_pause(attempt))
    raise AssertionError("unreachable")          # pragma: no cover


class ViewPersistenceError(RuntimeError):
    """存档不合法（缺字段 / 格式串不对 / `P` 不是划分）。"""


def to_dict(vs: Any) -> dict:
    """视图集合 → 可 JSON 的字典。**只存权威边**（见模块开头）。

    ⚠️ 这里**没有** `payload` / `concretization` / `covered` 三栏 —— 那是**故意**的。
       要造「存了派生边」的形态，用 `_to_dict_storing_derived`。
    """
    return {
        FORMAT_KEY: FORMAT,
        "spec": vs.spec.as_dict(),
        "q": [sorted(b) for b in sorted(vs.q, key=min)],
        "视图": [{"vid": v.vid, "block": sorted(v.block)} for v in vs.views],
    }


def from_dict(
    data: dict,
    kernel: Any,
    cover: Callable[[Any], frozenset[str]],
    plugin: Any,
) -> Any:
    """字典 → 视图集合。**派生边一律现算**（`payload` / `concretization` / `covered`）。

    ⚠️ `spec` 由 `ViewSpec(...)` 重建 ⇒ `__post_init__` 的三条校验**照跑**：
       盘上写坏的 `P` 会**抛**，不会静默变成一个别的划分。
    """
    from ..checks.abstraction import View, ViewSet  # 局部 import：避免 core→checks 的常态依赖

    if data.get(FORMAT_KEY) != FORMAT:
        raise ViewPersistenceError(
            f"格式串是 {data.get(FORMAT_KEY)!r}，期望 {FORMAT!r}")
    s = data.get("spec") or {}
    spec = ViewSpec(
        universe=tuple(s.get("universe") or ()),
        partition=tuple(frozenset(b) for b in (s.get("partition") or ())),
        relation=frozenset(tuple(e) for e in (s.get("relation") or ())),
    )
    q = partition_of([frozenset(b) for b in (data.get("q") or ())])
    views: list[Any] = []
    for row in data.get("视图") or ():
        block = frozenset(row["block"])
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
        v = View(vid=row["vid"], block=block, payload=payload,
                 concretization=conc, covered=frozenset(covered))
        if bad:
            from dataclasses import replace

            v = replace(v, category=bad)
        views.append(v)
    return ViewSet(spec=spec, q=q, views=tuple(views))


def dumps(vs: Any) -> str:
    """落盘用的文本。`ensure_ascii=False` + `sort_keys` ⇒ 同一份视图只有一个字节形态。"""
    return json.dumps(to_dict(vs), ensure_ascii=False, sort_keys=True, indent=2)


# ═══ 两个「错一处」的对照实现 ═════════════════════════════════════════════════
#
# ⚠️ 它们是**对照**，不是备选方案。作用：让 `§A6` 的每条判据都能被证明「会红」——
#    空转与通过长得一模一样，不配对照就分不开。
#
# 与 `core/persist.py` 那一对**同形**：一个把派生边也存进盘、一个读回时信任盘上的派生边。
# 只有**两个都上**，漂移才可能发生 —— 所以注入必须**同时**用这两个，
# 只用其中一个的注入是**造不出漂移的**（那会得到一条永远绿的判据）。


def _to_dict_storing_derived(vs: Any) -> dict:
    """对照①：落盘时**也存一份派生边** —— `§10.2 D` 说的「各存一份」。

    只多这一件事。它使**漂移成为可能**（不是必然 —— 一份没漂移的双份存档
    读回来照样对，所以「读回来对」这一条**单独不足以**证明只存了一份）。
    """
    data = to_dict(vs)
    by_vid = {v.vid: v for v in vs.views}
    for row in data["视图"]:
        v = by_vid[row["vid"]]
        row["concretization"] = sorted(v.concretization)
        row["covered"] = sorted(v.covered)
    return data


def _from_dict_trusting_derived(
    data: dict,
    kernel: Any,
    cover: Callable[[Any], frozenset[str]],
    plugin: Any,
) -> Any:
    """对照②：读回时**信任存档里的派生边**，不从 `block` 重算。

    只多这一件事。存档里没有那两栏时退回重算 ⇒ 与真实现**同输出**
    （所以它只在**带派生边**的存档上才分得开 —— 这正是注入实验要造的形态）。
    """
    from dataclasses import replace

    vs = from_dict(data, kernel, cover, plugin)
    by_vid = {v.vid: v for v in vs.views}
    out = []
    for row in data["视图"]:
        v = by_vid[row["vid"]]
        if "concretization" in row or "covered" in row:
            v = replace(v,
                        concretization=frozenset(row.get("concretization") or ()),
                        covered=frozenset(row.get("covered") or ()))
        out.append(v)
    return replace(vs, views=tuple(out))
