# -*- coding: utf-8 -*-
"""方向树的**跨进程**快照（2026-10-19）—— **可选项，默认关**。

## 为什么需要它

每次调用都是全量重造（`build` + 逐项 `insert`）。实测（`--index` 之后）：

    7 115 项   建树   6.7 s   ⇒ 0.94 ms/项
    30 000 项  建树 216.6 s   ⇒ 7.22 ms/项
    100 000 项 建树 3239.2 s  ⇒ 32.39 ms/项

**项数涨 14 倍，每项成本涨 34 倍** —— 超线性。这不是「慢一点」，是规模一上去就不可用。
进程内的 `reusable_build` 只活在内存字典里，**重启即归零**；做成熟产品撑不住。

## 它是什么、不是什么

    ✓ 一棵**已建好**的方向树，连同结构状态一起落盘，下次原样恢复
    ✓ **默认关**：`cli --snapshot` 才启用。默认关 ⇒ 既有基线一个字不动
    ✗ **不是缓存加速器** —— 它不是「同样的东西算两遍」（见死路表 §3 那条：
      进程内 signature 记忆化是**算法的一部分**）。这里省的是**下一次**的整棵树。
    ✗ **不是「旧树一定对」**：指纹对不上 ⇒ **重建 + 如实报**，绝不静默复用

## 指纹覆盖什么（少一样就可能用错树）

    ① 每个项的键集（`sorted(keys)`）     —— 材料内容
    ② 边（`id -> sorted(targets)`）       —— reach / sequence 的输入
    ③ 方向函数名                        —— keyset / reach / sequence
    ④ `holdout` / `predicates`           —— 进装配的参数
    ⑤ 快照格式版本号                    —— 结构变了要判得出来

⚠️ 用的是**内容哈希**，不是 mtime —— 摄入侧两次假绿的教训：格式变了、字节数没变，
   mtime 也照样对得上，于是拿旧结构算新材料。

## 坏快照的处理

文件缺失 / 版本不符 / 指纹不符 / JSON 坏 / payload 解不开 / 恢复后不变量不符
⇒ **一律重建**，并在报告里写清原因。**不许抛**（那会让可用性开关变成可用性炸弹），
也**不许静默复用**（那正是它要防的假绿）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

#: 快照格式版本。**改结构就 +1** —— 老版本一律判「不符」重建，不猜兼容。
SNAPSHOT_VERSION = 1


def snapshot_root() -> Path:
    """与语料索引同一个缓存根（`LDV_INDEX_DIR` 可覆盖）。"""
    from ..corpus.index import cache_root
    return cache_root()


def tree_path(name: str, fingerprint: str, root: Path | None = None) -> Path:
    """快照路径 —— 用**方向函数名 + 内容前缀**命名（不同方向/不同材料不互相覆盖）。"""
    return (root or snapshot_root()) / f"{name}-{fingerprint[:16]}.tree.json"


def fingerprint(items: dict[str, Any], edges: dict[str, frozenset[str]] | None = None,
                extra: dict[str, Any] | None = None) -> str:
    """材料与参数的**内容**指纹（sha256 十六进制）。

    ⚠️ 只覆盖「影响树形状」的输入。没覆盖到的输入若变了，树会不一样而指纹相同
       —— 所以 `extra` 必须把进装配的参数**全部**列进去（调用方负责）。
    """
    h = hashlib.sha256()
    for iid in sorted(items):
        it = items[iid]
        h.update(iid.encode("utf-8"))
        h.update(b"\x1f")
        h.update(",".join(sorted(it.get("keys", ()))).encode("utf-8"))
        h.update(b"\x1e")
    if edges is not None:
        for iid in sorted(edges):
            h.update(iid.encode("utf-8"))
            h.update(b"\x1d")
            h.update(",".join(sorted(edges[iid])).encode("utf-8"))
            h.update(b"\x1e")
    if extra:
        h.update(json.dumps(extra, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


# --- 写 -------------------------------------------------------------------

def save(kernel: Any, plugin: Any, direction: str, fp: str,
         path: Path | None = None, root: Path | None = None,
         extra: dict[str, Any] | None = None) -> Path:
    """把**已建好**的树写盘。返回落盘路径。

    `extra` 是**调用方自己的附加段**（原样存、原样读回），`_verify` **不查它** ——
    它不参与内核不变量。用途：`--plan --snapshot` 把「试过展开但分不开的方向」存下来
    （否则每次续建都会重试那几支，占掉每轮名额 ⇒ **剩余不收敛**，实测两次都是 5）。

    ⚠️ 写**临时文件后 `os.replace`**（`core/view_persist.commit` 的同一套做法）：
       半份快照比没有快照更坏 —— 它会被读成「有快照」。
    """
    from ..core.view_persist import commit

    dirs: dict[str, Any] = {}
    for d in kernel.all_directions():
        dirs[d.did] = {
            "秩": d.rank,
            "payload": base64.b64encode(plugin.encode(d)).decode("ascii"),
            "见证": list(d.witness),
            "来源": d.origin,
            "父": d.parent,
        }
    doc = {
        "版本": SNAPSHOT_VERSION,
        "指纹": fp,
        "方向函数": direction,
        "插件": type(plugin).__name__,
        "方向": dirs,
        "子": {k: list(v) for k, v in kernel._children.items()},
        "成员": {k: sorted(v) for k, v in kernel._members.items()},
        "已展开": sorted(kernel._expanded),
        "试过": {k: sorted(v) for k, v in kernel._tried.items()},
        "路径": {k: list(v) for k, v in kernel._path.items()},
        "根": kernel._root.did if kernel._root is not None else None,
        "计数": kernel._counter,
        # ⚠️ **账本一起存**（2026-10-19 实测发现）：第一次实现漏了它，
        #    恢复后「事件 N 条」从 34 掉到 0 —— 结构没事，但**读数变了**。
        #    账本是内核状态的一部分，不还原就不是同一棵树。
        "账本": [[e.seq, e.kind, e.did, dict(e.detail)] for e in kernel.ledger],
    }
    # 调用方附加段（原样存、原样读回，**不参与** `_verify` 的内核不变量）
    if extra:
        doc["extra"] = extra
    p = path if path is not None else tree_path(direction, fp, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    commit(p, body)
    return p


# --- 读 -------------------------------------------------------------------

def _verify(kernel: Any) -> list[str]:
    """恢复后的**自检**。对不上 ⇒ 重建（快照可能坏，也可能被手改过）。

    查的是两条**内核不变量**（与 `reflect.contradictions` 前两类同一条口径）：
        ① `父/子` 对称：`d.parent` 在 `p` 的子列表里
        ② `union(子的成员) ⊆ 父的成员`
        ③ `_path` 不变量：`set(_path[x]) == {d : x ∈ members(d)}`
       （第三条破 ⇒ 删除会摘不干净 ⇒ 子方向留幽灵成员）
    """
    bad: list[str] = []
    for d in kernel.all_directions():
        if d.parent:
            if d.did not in kernel._children.get(d.parent, ()):
                bad.append(f"{d.did} 声明父 {d.parent} 但不在其子列表里")
        kids = kernel._children.get(d.did, ())
        if kids:
            union: set[str] = set()
            for k in kids:
                union |= set(kernel._members.get(k, set()))
            if not union <= set(kernel._members.get(d.did, set())):
                bad.append(f"{d.did} 的子成员并集越出父成员 {len(union - set(kernel._members.get(d.did, set())))} 项")
    for iid, path in kernel._path.items():
        want = {d.did for d in kernel.all_directions() if iid in kernel._members.get(d.did, set())}
        if set(path) != want:
            bad.append(f"{iid} 的支撑锥与成员归属不符")
            break
    return bad


def restore(KernelCls: Any, plugin: Any, items: dict[str, Any],
            path: Path, fp: str) -> tuple[Any | None, dict[str, Any]]:
    """按快照恢复一棵树。**任何不符 ⇒ `None` + 写明原因**（调用方重建）。

    返回 `(内核 或 None, 报告)`。报告一定有 `结果`（`命中` / 新建）与 `原因`。
    """
    rep: dict[str, Any] = {"路径": str(path)}
    if not path.is_file():
        rep.update(结果="新建", 原因="还没有快照")
        return None, rep

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                                     # noqa: BLE001
        rep.update(结果="重建", 原因=f"快照读不开（{type(exc).__name__}）")
        return None, rep

    if doc.get("版本") != SNAPSHOT_VERSION:
        rep.update(结果="重建",
                   原因=f"版本不符：盘上 {doc.get('版本')} ≠ 现在 {SNAPSHOT_VERSION}")
        return None, rep
    if doc.get("指纹") != fp:
        rep.update(结果="重建",
                   原因="**指纹不符：材料或参数变了**（内容哈希，不是 mtime）")
        return None, rep
    # ⚠️ 这里只比**插件类名**：方向函数名进了 `fingerprint` 的 `extra`，
    #    换了函数名 ⇒ 指纹先不符 ⇒ 早就重建了。比类名是防同名不同实现。
    if doc.get("插件") != type(plugin).__name__:
        rep.update(结果="重建",
                   原因=f"插件不符：盘上 {doc.get('插件')} ≠ 现在 {type(plugin).__name__}")
        return None, rep

    dirs = doc.get("方向")
    kids = doc.get("子")
    mem = doc.get("成员")
    if not isinstance(dirs, dict) or not isinstance(kids, dict) or not isinstance(mem, dict):
        rep.update(结果="重建", 原因="快照缺段（方向/子/成员）")
        return None, rep

    # 探针里**不存在的类**不做 isinstance 检查（插件自己），用鸭子类型。
    from ..core.direction import Direction, Event, _fingerprint

    k = KernelCls(plugin, items)
    try:
        k._dirs = {did: Direction(did=did, rank=int(r["秩"]),
                                  payload=plugin.decode(base64.b64decode(r["payload"])),
                                  witness=tuple(r.get("见证", ())),
                                  origin=str(r.get("来源", "")),
                                  parent=r.get("父"))
                   for did, r in dirs.items()}
        k._children = {did: tuple(v) for did, v in kids.items()}
        k._members = {did: set(v) for did, v in mem.items()}
        k._expanded = set(doc.get("已展开", []))
        k._tried = {did: frozenset(v) for did, v in doc.get("试过", {}).items()}
        k._path = {iid: tuple(v) for iid, v in doc.get("路径", {}).items()}
        k._counter = int(doc.get("计数", 0))
        root_id = doc.get("根")
        k._root = k._dirs.get(root_id) if root_id else None
        k._all_cache = None
        k._all_ver = None
        # 账本原样还原（`_digest` 由 `_fingerprint` 重算 —— 与 `append` 同一条口径）
        for row in doc.get("账本", []):
            seq, kind, did, detail = row
            k.ledger._events.append(Event(seq=int(seq), kind=str(kind),
                                          did=str(did), detail=dict(detail)))
            k.ledger._digest.append(_fingerprint(k.ledger._events[-1]))
        k.ledger._seq = (k.ledger._events[-1].seq + 1) if k.ledger._events else 0
    except Exception as exc:                                     # noqa: BLE001
        rep.update(结果="重建", 原因=f"payload 解不开（{type(exc).__name__}）")
        return None, rep

    problems = _verify(k)
    if problems:
        rep.update(结果="重建", 原因="恢复后不变量不符：" + "；".join(problems[:2]))
        return None, rep

    rep.update(结果="命中", 原因="", 方向数=len(k._dirs))
    # 调用方附加段（`save(extra=...)` 存的）—— 原样带出，**不参与**不变量校验
    rep["extra"] = doc.get("extra") or {}
    return k, rep


# --- 一条龙：有就用，没有就建完写盘 ---------------------------------------

def with_snapshot(KernelCls: Any, plugin: Any, build: Any,
                  items: dict[str, Any], direction: str,
                  edges: dict[str, frozenset[str]] | None,
                  extra: dict[str, Any], root: Path | None = None,
                  ) -> tuple[Any, dict[str, Any]]:
    """`build()` 的替身：先试恢复；不行才建，**建完落盘**。

    `build` 是 `() -> kernel` 的回调（深拷贝成本由调用方决定）。
    返回 `(内核, 报告)`，报告至少含 `结果`（`命中` / `新建` / `重建`）与 `原因`。
    """
    fp = fingerprint(items, edges, {"方向函数": direction, **extra})
    p = tree_path(direction, fp, root)
    k, rep = restore(KernelCls, plugin, items, p, fp)
    if k is not None:
        rep["秒"] = None
        return k, rep
    # ⚠️ **「从没有过」与「变了」必须分得开**（2026-10-19 实测发现）：
    #    指纹变了 ⇒ 路径也变 ⇒ 自然「找不到这个文件」。如果照实报「还没有快照」，
    #    材料已经变过一次之后每次都读成**首次**，人就看不到「指纹正在变化」。
    #    ⇒ 看看**同方向**还有没有别的快照：有 ⇒ 这是**重建**，不是新建。
    if rep.get("结果") == "新建":
        same_dir = sorted((root or snapshot_root()).glob(f"{direction}-*.tree.json"))
        if same_dir:
            rep.update(结果="重建",
                       原因=(f"**指纹不符**：{direction} 已有 {len(same_dir)} 份快照，"
                             f"但没有一份对得上当前的材料/参数"
                             f"（内容哈希，不是 mtime）"))
    t0 = time.time()
    k = build()
    try:
        save(k, plugin, direction, fp, p)
        rep["落盘"] = str(p)
        rep["写盘秒"] = round(time.time() - t0, 3)
    except Exception as exc:                                     # noqa: BLE001
        # ⚠️ 写失败**不许**让整趟崩 —— 快照是可选优化，不是正确性依赖。
        rep["落盘失败"] = f"{type(exc).__name__}: {exc}"
    rep["建树秒"] = round(time.time() - t0, 3)
    return k, rep
