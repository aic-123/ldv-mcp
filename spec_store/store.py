"""声明仓库：agent 生成的 `view_spec` 落在哪、怎么冻结、怎么判过期。

## 为什么要有「冻结」这一步（本产品 vs 内核的唯一差别）

内核的规矩是「声明只能人写」（`§K9` 外生）。本产品把**作者权**下放给 agent，
代价是声明变成随机的（模型每次生成的不同）。补法就一条：

    **首次使用即冻结** —— agent 生成声明后立刻落成文件；之后建树只读文件，
    不重新问模型。agent 是**配置生成器**，不是每次查询的参与者。

⇒ 复现性回到文件层：同一份冻结声明 + 同一份语料 ⇒ 同一棵树（内核的保证原样成立）。

## 三条铁律

1. **不许静默覆盖**：已冻结的声明，再次 freeze 必须显式 `force=True`，否则报错。
   （「悄悄换了一个透镜」与「用户主动换了透镜」在读到的树上长得不一样。）
2. **语料指纹不符 ⇒ 判过期**：声明带着生成时面对的语料指纹；语料一变，
   声明作废（内核同一条纪律：材料一变，上一份认识就作废）。
3. **来历必填**：`generated_by` + `rationale`（理由原文）缺一项就不许冻结。
   「说不清这个方向为什么存在」正是当初不放权的理由 —— 放权可以，理由不能没有。

## 目录形状

    <store>/
      candidates/<id>.json     候选（未冻结，可覆盖可丢弃）
      view_spec.json           冻结的声明（唯一）
      HISTORY.md               每次冻结/作废的一行账（追加，不改）
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

#: 冻结声明的文件名（一个语料一份）
FROZEN = "view_spec.json"
#: 候选目录
CANDIDATES = "candidates"
#: 账本
HISTORY = "HISTORY.md"

#: 本产品认识的声明字段（最小集）：**方向函数名**。
#: ⚠️ 不是内核 view_spec 的「方向」—— 那是视图层的键；产品层的声明回答的是
#:    「用哪个方向函数、按什么理由」，视图层声明（若要用）放在 `view_spec` 子字段里。
_REQUIRED_KEYS = ("插件",)


class SpecError(RuntimeError):
    """声明仓库的错 —— 覆盖、过期、缺来历，都走这个。"""


def corpus_fingerprint(nodes_dir: str | Path) -> str:
    """语料指纹：**节点 id 全集 + 每节点的键数**，排序后取 sha1。

    ⚠️ 刻意不用文件 mtime / 字节数（内核 loader 的缓存键那套）——
       这里要的是「这份料还是不是当初那份」的**内容**判断，
       而 mtime 会因一次 touch 而变（假过期），字节数会因格式调整而变（假过期）。
    ⚠️ 不读 front matter 全文：36 项与 3907 项都快，且**只依赖结构与词汇**，
       不依赖正文内容 —— 正文改了但结构没改，声明仍然成立（这正是想要的）。
    """
    sys_path = Path(nodes_dir)
    if not sys_path.is_dir():
        raise SpecError(f"语料目录不在：{sys_path}")
    rows: list[str] = []
    for p in sorted(sys_path.rglob("*.md")):
        try:
            text = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        head = text.split("---", 2)
        keys = 0
        if len(head) >= 2:
            keys = sum(1 for line in head[1].split("\n")
                       if line.strip() and not line.startswith(" ")
                       and ":" in line and not line.lstrip().startswith("#"))
        rows.append(f"{p.stem}:{keys}")
    h = hashlib.sha1(("\n".join(rows)).encode("utf-8")).hexdigest()
    return f"{len(rows)}:{h[:16]}"


def _store(store_dir: str | Path) -> Path:
    p = Path(store_dir)
    (p / CANDIDATES).mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def propose(store_dir: str | Path, spec: dict, *, rationale: str,
            generated_by: str = "agent", corpus_dir: str | Path = "") -> dict:
    """存一个**候选**声明（未冻结）。返回候选 id 与落盘路径。

    ⚠️ 候选可覆盖、可丢弃 —— 它还不是系统的输入。
       「提议一个透镜」与「换掉现在的透镜」必须分成两步，否则 agent 每次
       生成都等于换树，而复现性就此消失。
    """
    if not rationale or not rationale.strip():
        raise SpecError("候选声明必须带 rationale（理由原文）—— 说不清为什么这么分，"
                        "就不算一个合格的提议")
    missing = [k for k in _REQUIRED_KEYS if k not in spec]
    if missing:
        raise SpecError(f"声明缺字段 {missing} —— 至少要有 {list(_REQUIRED_KEYS)}")
    store = _store(store_dir)
    fp = corpus_fingerprint(corpus_dir) if corpus_dir else ""
    cid = hashlib.sha1(json.dumps(spec, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    row = {"id": cid, "spec": spec, "rationale": rationale.strip(),
           "generated_by": generated_by, "corpus_fingerprint": fp,
           "proposed_at": _now()}
    p = store / CANDIDATES / f"{cid}.json"
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps(row, ensure_ascii=False, indent=1, sort_keys=True))
    return {"ok": True, "candidate_id": cid, "path": str(p),
            "corpus_fingerprint": fp}


def freeze(store_dir: str | Path, candidate_id: str, *, corpus_dir: str | Path,
           force: bool = False) -> dict:
    """把候选**冻结**成唯一声明。已有时必须 `force=True`。"""
    store = _store(store_dir)
    cp = store / CANDIDATES / f"{candidate_id}.json"
    if not cp.is_file():
        raise SpecError(f"没有这个候选：{candidate_id}")
    row = json.loads(io.open(cp, encoding="utf-8").read())
    fp_now = corpus_fingerprint(corpus_dir)
    if row.get("corpus_fingerprint") and row["corpus_fingerprint"] != fp_now:
        raise SpecError(
            f"候选是针对语料指纹 {row['corpus_fingerprint']} 提的，"
            f"现在这份是 {fp_now} —— 语料变过，声明作废（材料一变，上一份认识就作废）")
    target = store / FROZEN
    if target.is_file() and not force:
        old = json.loads(io.open(target, encoding="utf-8").read())
        raise SpecError(
            f"已经冻结过一份（{old.get('id')}，{old.get('frozen_at')}）。"
            "要换透镜请显式 force=True —— 「悄悄换了一个透镜」与「用户主动换了」"
            "在读到的树上长得一样，所以必须分开")
    frozen = {"id": candidate_id, "spec": row["spec"], "rationale": row["rationale"],
              "generated_by": row["generated_by"], "corpus_fingerprint": fp_now,
              "proposed_at": row.get("proposed_at", ""), "frozen_at": _now(),
              "forced": bool(force)}
    tmp = target.with_suffix(".tmp")
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(
        json.dumps(frozen, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, target)                       # 原子替换：不留半份
    _append_history(store, frozen, corpus_dir)
    return {"ok": True, "frozen": str(target), "id": candidate_id,
            "corpus_fingerprint": fp_now, "forced": bool(force)}


def _append_history(store: Path, frozen: dict, corpus_dir: str | Path) -> None:
    line = (f"- {frozen['frozen_at']} 冻结 {frozen['id']}｜生成者 {frozen['generated_by']}"
            f"｜语料指纹 {frozen['corpus_fingerprint']}｜语料 {Path(corpus_dir)}"
            f"｜理由 {frozen['rationale'][:60]}\n")
    with io.open(store / HISTORY, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)


def load(store_dir: str | Path) -> dict | None:
    """读冻结的声明；没有就返回 None（**没有 ≠ 空**，调用方要分开报）。"""
    p = Path(store_dir) / FROZEN
    if not p.is_file():
        return None
    return json.loads(io.open(p, encoding="utf-8").read())


def status(store_dir: str | Path, corpus_dir: str | Path) -> dict:
    """只报不判：冻结状态 + 是否过期 + 候选数。"""
    store = Path(store_dir)
    frozen = load(store)
    fp_now = corpus_fingerprint(corpus_dir)
    cands = sorted((store / CANDIDATES).glob("*.json")) if (store / CANDIDATES).is_dir() else []
    out: dict[str, Any] = {"frozen": frozen is not None, "corpus_fingerprint": fp_now,
                           "candidates": [c.stem for c in cands]}
    if frozen:
        out["id"] = frozen.get("id")
        out["generated_by"] = frozen.get("generated_by")
        out["frozen_at"] = frozen.get("frozen_at")
        out["stale"] = bool(frozen.get("corpus_fingerprint")
                            and frozen["corpus_fingerprint"] != fp_now)
        out["rationale"] = frozen.get("rationale", "")
    return out


def clear(store_dir: str | Path) -> dict:
    """显式清空（冻结 + 候选 + 账本保留）。返回删了几个。"""
    store = Path(store_dir)
    n = 0
    f = store / FROZEN
    if f.is_file():
        f.unlink()
        n += 1
    for c in (store / CANDIDATES).glob("*.json"):
        c.unlink()
        n += 1
    return {"ok": True, "deleted": n}
