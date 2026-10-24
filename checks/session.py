"""会话缓冲：一个 ldv 下的**多会话**，每个会话有自己的曝光缓冲。

## 三区（对话范围的三层，别混）

    工作集   本轮推理的现场          **不归插件管**（留在宿主上下文里）
    缓冲     这次会话「看到过什么」   插件管：有界、可丢弃、按会话隔离
    正式     明确「这条算数」的       插件管：语料 / remember / derive / 使用记录

## 缓冲里放什么（插件只看得到工具调用，看不到对话原文）

    ask  ⇒ 一条 **曝光**：这次摆出了哪几支、答案多少（**不是「有用」**）
    drill ⇒ 把对应曝光标成 **promoted**（这才是真实动作）

★ 这与内核的两段通道**同构**：查询只进缓冲，下钻才 promote。
  区别只在作用域 —— 内核按查询指纹，这里按会话。

## 有界与漏检（这条是本模块存在的理由）

    缓冲有界（默认 50 条），写满就挤掉最老的。
    **挤掉的条目后来又被提到** ⇒ 记一次「漏检」（remention）。
    读数：`漏检率 = remention / evicted` —— 高，说明「拿插件替代上下文」在该场景不成立。

⚠️ 挤掉 ≠ 删除：被挤掉的条目**进 `evicted.json` 记账**，不是静默消失。
   「不知道漏了什么」比「漏了」更糟。
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

#: 缓冲容量默认值（条）。可在 `<ldv>/config.json` 里覆盖：{"buffer_cap": 50}
CAP_DEFAULT = 50


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sdir(ldv_root: str | Path, sid: str) -> Path:
    """`<ldv>/sessions/<sid>/` —— sid 里不许有路径分隔符（会写到别处去）。"""
    s = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(sid)) or "default"
    return Path(ldv_root) / "sessions" / s


def _read(p: Path, default: Any) -> Any:
    if not p.is_file():
        return default
    try:
        return json.loads(io.open(p, encoding="utf-8").read())
    except (OSError, ValueError):
        return default


def _write(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(
        json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, p)                      # 原子替换：不留半份


def config(ldv_root: str | Path) -> dict:
    """ldv 级配置：缓冲容量 / 使用记录是否按会话隔离。"""
    c = _read(Path(ldv_root) / "config.json", {})
    return {"buffer_cap": int(c.get("buffer_cap") or CAP_DEFAULT),
            "isolate_usage": bool(c.get("isolate_usage", False))}


def expose(ldv_root: str | Path, sid: str, *, qfp: str, dids: list[str],
           n_answer: int) -> dict:
    """记一条**曝光**（这次会话看到了哪几支）。满了就挤掉最老的，并记账。"""
    d = sdir(ldv_root, sid)
    cap = config(ldv_root)["buffer_cap"]
    buf = _read(d / "buffer.json", [])
    ev = _read(d / "evicted.json", {"total": 0, "dids": {}, "remention": 0})
    # ★ 漏检：这次看到的支里，有没有以前被挤掉过的
    for did in dids:
        if did in ev.get("dids", {}):
            ev["remention"] = int(ev.get("remention", 0)) + 1
            ev["dids"][did] = int(ev["dids"][did]) + 1
    row = {"at": _now(), "qfp": qfp, "dids": list(dids), "n_answer": int(n_answer),
           "promoted": False}
    buf.append(row)
    while len(buf) > cap:
        old = buf.pop(0)
        ev["total"] = int(ev.get("total", 0)) + 1
        for did in old.get("dids") or []:
            ev.setdefault("dids", {})
            ev["dids"][did] = int(ev["dids"].get(did, 0)) + 1
    _write(d / "buffer.json", buf)
    _write(d / "evicted.json", ev)
    return {"n_buffer": len(buf), "cap": cap, "evicted": ev["total"],
            "remention": ev.get("remention", 0)}


def mark_promoted(ldv_root: str | Path, sid: str, did: str) -> bool:
    """下钻 ⇒ 把含这一支的**最近一条**曝光标成 promoted（真实动作）。"""
    d = sdir(ldv_root, sid)
    buf = _read(d / "buffer.json", [])
    for row in reversed(buf):
        if did in (row.get("dids") or []):
            row["promoted"] = True
            _write(d / "buffer.json", buf)
            return True
    return False


def status(ldv_root: str | Path, sid: str) -> dict:
    """一个会话的缓冲读数 —— **报数不判断**。"""
    d = sdir(ldv_root, sid)
    buf = _read(d / "buffer.json", [])
    ev = _read(d / "evicted.json", {"total": 0, "dids": {}, "remention": 0})
    n_ev = int(ev.get("total", 0))
    n_re = int(ev.get("remention", 0))
    return {"session": d.name, "n_buffer": len(buf),
            "cap": config(ldv_root)["buffer_cap"],
            "promoted": sum(1 for r in buf if r.get("promoted")),
            "evicted": n_ev, "remention": n_re,
            # ⚠️ 读数不是分数：它是「挤掉的里面有多少后来又需要」的比例
            "漏检率": (round(n_re / n_ev, 3) if n_ev else None),
            "note": "漏检率 = 被挤出后又被重新提到的次数 / 挤出总数；"
                    "None = 还没挤掉过（不是 0）"}


def sessions(ldv_root: str | Path) -> list[str]:
    """这个 ldv 下有哪些会话（目录名）。"""
    base = Path(ldv_root) / "sessions"
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


# ── 句柄表：agent 只看「第几条」，id 留在这里 ────────────────────────────
def handles_path(ldv_root: str | Path) -> Path:
    """**按 ldv 一份**（不是按会话）—— 句柄是「刚才那批」的编号，与谁问的无关。"""
    return Path(ldv_root) / "handles.json"


def save_handles(ldv_root: str | Path, items: list[dict], *, qfp: str = "",
                 require: list[str] | None = None,
                 forbid: list[str] | None = None) -> None:
    """记下「句柄 n → 条目」的映射。**只在本地** —— agent 只看到 n。

    ★ 2026-10-24 用户定：正文交给 agent，id 之类的记录留在 ldv 里。
      但动作（下钻 / 反事实）总得指到东西 ⇒ 用**句柄**：agent 说「第 3 条」，
      服务端在这张表里查它到底是哪一条。
    ⚠️ 每次 `ask` **覆盖** —— 它是「刚才那批」的编号，不是长期标识。
      所以表里记下这次查询的 `require/forbid`：动作来时对不上 ⇒ **拒**，不猜。
    """
    doc = {"qfp": qfp,
           "require": sorted(require or []), "forbid": sorted(forbid or []),
           "items": {str(i + 1): {"id": it["id"], "title": it.get("title", "")}
                     for i, it in enumerate(items)}}
    _write(handles_path(ldv_root), doc)


def resolve_handle(ldv_root: str | Path, n: int | str, *,
                   require: list[str] | None = None,
                   forbid: list[str] | None = None) -> tuple[dict | None, str]:
    """句柄 → 条目记录。返回 `(记录 or None, 原因)`。

    ⚠️ 查询对不上（`require/forbid` 不同）⇒ 拒 —— 否则「第 3 条」会指到
      另一次查询的第 3 条，那是**静默指错**，比报错糟。
    ⚠️ 超出缓冲记的范围 ⇒ 明说「顺序不记了」（2026-10-24 用户定）——
      缓冲只保存下来的顺序，不保存「本该有多少」。
    """
    doc = _read(handles_path(ldv_root), {})
    if not doc:
        return None, "还没有句柄表（先 ask 一次）"
    want_r, want_f = sorted(require or []), sorted(forbid or [])
    if doc.get("require") != want_r or doc.get("forbid") != want_f:
        return None, (f"句柄属于另一次查询（require={doc.get('require')}、"
                      f"forbid={doc.get('forbid')}），与这次不符 —— 请重新 ask")
    items = doc.get("items") or {}
    ks = str(n)
    if ks not in items:
        got = len(items)
        if ks.isdigit() and int(ks) > got:
            return None, (f"顺序不记了 —— 缓冲只记到第 {got} 条（第 {ks} 条超出了）")
        return None, f"没有第 {ks} 条（这次查询给了 {got} 条）"
    return items[ks], ""


def handles(ldv_root: str | Path) -> dict:
    """整张句柄表 —— 给 `orient` 做审计用（人看「第 3 条」到底是什么）。"""
    return _read(handles_path(ldv_root), {})
