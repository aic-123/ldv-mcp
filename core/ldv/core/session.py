# -*- coding: utf-8 -*-
"""持续增长语料的**热启动会话**（2026-10-20）—— 方案 A：快照 + 追加日志。

## 它解决什么

`--snapshot` 的粒度是**整棵树 + 全局内容指纹** ⇒ 加一个节点，指纹就变 ⇒ 全量重建。
实测（30k）：建树 194.5 s / 恢复 0.26 s，但**每加一个节点**就要重写 2.56 MB 快照
（0.13 s）+ 重算全局指纹（0.15 s）⇒ **≈0.3 s/节点，且随 n 线性涨**。

这个模块把「每节点」的成本压成**常数**：

    快照 = 某一刻的整棵树（写一次）
    日志 = 之后的每次 insert / remove **追加一行**（O(1)，不随 n 涨）
    打开 = restore(0.26 s) + replay(k 行 × 22.7 ms)

## ⚠️ 它给的树**不是**「全量重造」那棵

本仓库实测过：**增量 ≠ 全量**（三个方向都无法在后添 ≥2 项时同构）。
⇒ 热启动得到的是一棵**增量树**，方向数/叶容量可能与冷建不同。
**它不能与既有基线并列比较** —— 这是使用前提，不是缺陷。

## ⚠️ 它不适合「固定实验流程」

`cli` 的 A+B+D+B′ 里，B 插 12 项、B′ 删 12 项 —— **那些变更本身就是被测对象**。
把它们记进日志再 replay，下一次就会重复应用 ⇒ 实验失去意义。
⇒ 本模块是给**服务 / 持续填充**用的：调用方决定加什么，加完就落盘。

## 一致性怎么守

    快照头部：`指纹` = 那一刻**整个项集**的内容指纹
    日志头部：`base`（快照指纹）· `ops`（行数）· `after`（密封时整个项集的新指纹）
    打开时：① 快照读不出 / 版本不符 ⇒ 重建
            ② 日志头 `ops` ≠ 实际行数 ⇒ **日志不完整**（写一半崩了）⇒ 丢掉日志，用快照
            ③ replay 每条：`insert` 的 id 必须在当前项集里、`remove` 的必须在树里
            ④ replay 后重算整个项集指纹，必须 == 日志头的 `after`（无日志时 == 快照 `base`）
               ⇒ 对不上 = **材料变了** ⇒ 全丢、重建

★ 第 ④ 步是 O(n)（30k 约 0.15 s），但它**只在打开时算一次**，
  而且比重建（194 s）便宜三个数量级 —— 用一次 O(n) 换掉每次插入的 O(n)。
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from . import snapshot as SN

LOG_VERSION = 1


# --- 路径 -----------------------------------------------------------------

def _dir(base_dir: Path | None) -> Path:
    return Path(base_dir) if base_dir is not None else SN.snapshot_root()


def _stem(direction: str, scope: str = "") -> str:
    s = f"{direction}"
    if scope:
        s += "-" + "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in scope)[:40]
    return s + ".state"


def state_path(direction: str, base_dir: Path | None = None, scope: str = "") -> Path:
    return _dir(base_dir) / f"{_stem(direction, scope)}.tree.json"


def log_path(direction: str, base_dir: Path | None = None, scope: str = "") -> Path:
    return _dir(base_dir) / f"{_stem(direction, scope)}.log.jsonl"


# --- 日志 -----------------------------------------------------------------

class DeltaLog:
    """追加式操作日志。**一行一条 JSON**，头部单独一行。"""

    def __init__(self, path: Path, base_fp: str) -> None:
        self.path = Path(path)
        self.base_fp = base_fp
        self._n = 0
        self._sealed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 新日志：**只有头**（把可能存在的旧日志整份换掉）
        self._write_header(ops=0, after=None, body=[])

    def _write_header(self, ops: int, after: str | None,
                      body: list[str] | None = None) -> None:
        """重写**第一行**，正文原样保留。

        ⚠️ 第一版直接 `os.replace(tmp, path)` 写「只有头」的新文件 ——
           `seal()` 时把**已追加的条目全冲掉了**（实测：4 条日志变 1 行）。
           头与正文必须一起写：`[头] + body`。
        """
        if body is None:
            body = []
            if self.path.is_file():
                body = self.path.read_text(encoding="utf-8").splitlines()[1:]
        head = {"版本": LOG_VERSION, "base": self.base_fp, "ops": int(ops), "after": after}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text("\n".join([json.dumps(head, ensure_ascii=False)] + body) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def append(self, op: str, item_id: str, keys: Any = None) -> None:
        assert op in ("insert", "remove"), op
        row: dict[str, Any] = {"op": op, "id": item_id}
        if keys is not None:
            row["keys"] = sorted(keys)
        with io.open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._n += 1

    @property
    def ops(self) -> int:
        return self._n

    @classmethod
    def resume(cls, path: Path, base_fp: str, n: int) -> "DeltaLog":
        """接着已有的日志追加 —— **不重写头**（头由 `seal()` 在收尾时补）。

        ⚠️ **日志文件不存在时必须先写头**（2026-10-20 实测踩到）：
           否则第一条 `append` 会创建一个**没有头**的文件，而 `seal()` 把
           `splitlines()[1:]` 当正文 ⇒ **第一条被当成头吃掉**（实测 12 条变 11 条，
           打开时报「日志不完整」）。这种情况在「只有快照、还没有日志」时必然发生。
        """
        obj = cls.__new__(cls)
        obj.path = Path(path)
        obj.base_fp = base_fp
        obj._n = int(n)
        obj._sealed = False
        if not obj.path.is_file() or obj.path.stat().st_size == 0:
            obj._n = 0
            obj._write_header(ops=0, after=None, body=[])
        return obj

    def seal(self, after_fp: str) -> None:
        """**密封**：补上 `ops` 与 `after`。不密封的日志会被判「不完整」而丢弃。"""
        self._write_header(ops=self._n, after=after_fp)
        self._sealed = True

    def clear(self) -> None:
        if self.path.is_file():
            self.path.unlink()
        self._n = 0


def _read_log(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """读日志 → `(头, 条目, 问题)`。问题非空 ⇒ 调用方丢日志。"""
    problems: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return {}, [], ["日志为空（连头都没有）"]
    try:
        head = json.loads(lines[0])
    except Exception:                                            # noqa: BLE001
        return {}, [], ["日志头读不开"]
    entries: list[dict[str, Any]] = []
    for i, ln in enumerate(lines[1:], 1):
        if not ln.strip():
            continue
        try:
            entries.append(json.loads(ln))
        except Exception:                                        # noqa: BLE001
            problems.append(f"第 {i} 行读不开")
    if head.get("版本") != LOG_VERSION:
        problems.append(f"日志版本不符：{head.get('版本')} ≠ {LOG_VERSION}")
    if int(head.get("ops", -1)) != len(entries):
        problems.append(f"**日志不完整**：头说 {head.get('ops')} 条，实际 {len(entries)} 条")
    return head, entries, problems


# --- 打开 / 提交 -----------------------------------------------------------

class TreeSession:
    """一棵树 + 一份快照 + 一份追加日志。

    用法（服务 / 持续填充）：

        s = TreeSession.open(name="keyset", plugin=KeysetPlugin(), items=items,
                             build=lambda: build_tree(), scope="ext-wiki-vote")
        s.insert("x-000123", items["x-000123"])      # 只 append 一行，不重写快照
        s.remove("x-000999")
        s.close()                                     # 密封日志（写 after 指纹）

    `close()` 之后，下一次 `open()` 会 restore + replay，**不会重建**。
    """

    def __init__(self, kernel: Any, plugin: Any, direction: str, items: dict[str, Any],
                 edges: Any, extra: dict[str, Any], base_dir: Path | None,
                 scope: str, log: DeltaLog | None, report: dict[str, Any]) -> None:
        self.kernel = kernel
        self.plugin = plugin
        self.direction = direction
        self.items = items
        self.edges = edges
        self.extra = extra
        self.base_dir = base_dir
        self.scope = scope
        self.log = log
        self.report = report
        self._closed = False

    # -- 打开 --------------------------------------------------------------
    @classmethod
    def open(cls, *, name: str, plugin: Any, items: dict[str, Any],
             build: Callable[[], Any], edges: Any = None,
             extra: dict[str, Any] | None = None,
             KernelCls: Any = None, base_dir: Path | None = None,
             scope: str = "") -> "TreeSession":
        from .kernel import Kernel

        K = KernelCls or Kernel
        extra = dict(extra or {})
        sp = state_path(name, base_dir, scope)
        lp = log_path(name, base_dir, scope)
        rep: dict[str, Any] = {"快照": str(sp), "日志": str(lp)}

        fp_now = SN.fingerprint(items, edges, {"方向函数": name, **extra})
        t0 = time.time()

        kernel = None
        base_fp: str | None = None
        doc: dict[str, Any] = {}
        if sp.is_file():
            try:
                doc = json.loads(sp.read_text(encoding="utf-8"))
            except Exception:                                    # noqa: BLE001
                doc = {}
            base_fp = doc.get("指纹")
            if base_fp:
                k, r = SN.restore(K, plugin, items, sp, base_fp)
                if k is not None:
                    kernel = k
                    rep["快照"] = r.get("结果", "命中")
                else:
                    rep["快照"] = f"重建（{r.get('原因')}）"
            else:
                rep["快照"] = "重建（快照没有指纹）"
        else:
            rep["快照"] = "新建"

        head: dict[str, Any] = {}
        n_replay = 0
        if kernel is not None:
            if lp.is_file():
                head, entries, problems = _read_log(lp)
                if problems:
                    rep["日志"] = "丢弃（" + "；".join(problems[:2]) + "）"
                    lp.unlink()
                    head = {}
                elif head.get("base") != base_fp:
                    rep["日志"] = "丢弃（base 与快照不符）"
                    lp.unlink()
                    head = {}
                else:
                    n_replay, rp = _replay(kernel, items, entries)
                    if rp:
                        rep["日志"] = "丢弃（" + "；".join(rp[:2]) + "）"
                        lp.unlink()
                        head = {}
                        kernel = None        # 回放失败 ⇒ 整个状态不可信
                    else:
                        rep["日志"] = f"回放 {n_replay} 条"
            else:
                rep["日志"] = "无"

            # ④ **材料一致性**：回放之后的项集必须**就是**当前项集
            if kernel is not None:
                expect = head.get("after") if head else base_fp
                if expect and expect != fp_now:
                    # ⚠️ **追加，不覆盖**：前一步可能已经报了「日志不完整」——
                    #    那是**根因**，被后一条冲掉的话，人只看得到「材料变了」。
                    prev = str(rep.get("日志", "") or "")
                    tail = (f"材料变了（期望 {str(expect)[:12]}… ≠ 现在 {fp_now[:12]}…）"
                            "⇒ 重建")
                    rep["日志"] = (prev + "；" + tail) if prev and prev != "无" else tail
                    kernel = None

        if kernel is None:
            kernel = build()
            # 建完就落盘（用**当前**项集指纹当 base）
            SN.save(kernel, plugin, name, fp_now, sp)
            rep["结果"] = "新建"
            rep["秒"] = round(time.time() - t0, 3)
            log = DeltaLog(lp, fp_now)
            log.seal(fp_now)
            return cls(kernel, plugin, name, items, edges, extra, base_dir, scope,
                       log, rep)

        rep["结果"] = "命中"
        rep["秒"] = round(time.time() - t0, 3)
        log = DeltaLog.resume(lp, base_fp or "", n_replay)
        return cls(kernel, plugin, name, items, edges, extra, base_dir, scope, log, rep)

    # -- 变更（**只 append，不重写快照**） ----------------------------------
    def insert(self, item_id: str, item: Any = None) -> None:
        self.kernel.insert(item_id, item)
        if self.log is not None:
            self.log.append("insert", item_id)

    def remove(self, item_id: str) -> None:
        # ⚠️ **先把键集记下来**（2026-10-20 实测逼出来）：快照里 `_members` 仍引用
        #    这个项，而调用方传进来的「当前项集」**已经不含它** ⇒ 回放期间若先
        #    发生 `expand`，内核会拿一个不在 `items` 里的 id 去取项 ⇒ `KeyError`。
        #    ⇒ 日志带上键集，打开时先补一个**桩项**，等这条 `remove` 把它摘掉。
        item = self.kernel.items.get(item_id)
        keys = item.get("keys") if isinstance(item, dict) else None
        self.kernel.remove(item_id)
        if self.log is not None:
            self.log.append("remove", item_id, keys)

    # -- 收尾 --------------------------------------------------------------
    def close(self) -> dict[str, Any]:
        """**密封日志**：写 `ops` 与 `after`（整个项集的新指纹）。

        ⚠️ 只有密封过的日志才会被下一次 `open()` 接受 —— 写一半崩掉的日志
           会被判「不完整」而丢弃（宁可重建，不用半份状态）。
        """
        if self._closed or self.log is None:
            return self.report
        fp_now = SN.fingerprint(self.kernel.items, self.edges,
                                {"方向函数": self.direction, **self.extra})
        self.log.seal(fp_now)
        self._closed = True
        self.report["密封"] = f"{self.log.ops} 条 ⇒ after {fp_now[:12]}…"
        return self.report

    def commit(self) -> Path:
        """把**当前**状态压成新快照并清空日志（日志太长时用）。"""
        fp_now = SN.fingerprint(self.kernel.items, self.edges,
                                {"方向函数": self.direction, **self.extra})
        p = SN.save(self.kernel, self.plugin, self.direction, fp_now,
                    state_path(self.direction, self.base_dir, self.scope))
        if self.log is not None:
            self.log.clear()
            self.log.base_fp = fp_now
            self.log._n = 0
            self.log.seal(fp_now)
        self.report["提交"] = str(p)
        return p


def _replay(kernel: Any, items: dict[str, Any],
            entries: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """按顺序回放。任何一条不可行 ⇒ 返回问题（调用方丢日志）。

    ★ **先补桩项**：快照的 `_members` 里还挂着后来被删掉的项，而调用方传来的
      「当前项集」已经没有它们 ⇒ 回放期间只要碰到 `expand`，内核就会拿一个
      不在 `items` 里的 id 去取项 ⇒ `KeyError`（2026-10-20 实测踩到）。
      ⇒ 用日志里记下的键集补一个**桩项**，让 base 状态自洽；那条 `remove`
      回放时再把它摘掉。
    """
    problems: list[str] = []
    for e in entries:
        if e.get("op") != "remove":
            continue
        iid = e.get("id")
        if iid and iid not in items:
            kernel.items[iid] = {"id": iid,
                                 "keys": frozenset(e.get("keys") or ()),
                                 "node": None}
    n = 0
    for e in entries:
        op, iid = e.get("op"), e.get("id")
        if op == "insert":
            if iid not in items:
                problems.append(f"insert 的 {iid} 不在当前项集里")
                break
            try:
                kernel.insert(iid, items[iid])
            except Exception as exc:                             # noqa: BLE001
                problems.append(f"insert {iid} 回放失败：{type(exc).__name__}")
                break
        elif op == "remove":
            try:
                kernel.remove(iid)
            except Exception as exc:                             # noqa: BLE001
                problems.append(f"remove {iid} 回放失败：{type(exc).__name__}")
                break
        else:
            problems.append(f"未知 op {op!r}")
            break
        n += 1
    if not problems:
        problems.extend(SN._verify(kernel))
    return n, problems
