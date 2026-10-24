# -*- coding: utf-8 -*-
"""一次交流记录 → 缓冲区里的**一次性树**（`ldv-msg/1` 的沉淀路径，2026-10-21）。

用户 2026-10-21 三句裁定，逐条变成这里的设计：

    「把一次交换当成完成……记录一次完整的交流」      ⇒ 记录本身就是成果
    「每段当成一次缓冲区域的新建树，使用完成就可以抛弃」⇒ 一次交流一棵树，默认抛弃
    「抛弃机制遵守缓冲区域的规则，不去特意设计」        ⇒ 抛弃 = 显式删目录；
                                                          容量只报不淘汰；`evict` 一行不加

## 目录形状（与 `_staging/nodes/` 平级，**两种默认命运分开**）

    <语料>/_staging/
    ├── nodes/                    待提交候选（默认：**沉淀**，走既有 commit）
    ├── rejected/                 拒收 + 理由（**永不淘汰** —— 审计链）
    └── exchanges/<id>/nodes/     一次交流 = 一棵一次性树（默认：**抛弃**）

## 五条设计纪律（每条都有出处，不是偏好）

1. **一条消息一节点** —— 粒度再粗（整段交流一个节点）键就退化成常量
   （`§K2 判空`：`type=`/`who=`/`round=` 会全部失去区分力）。
2. **内容 = 消息原文的 JSON，逐字不改** —— 本工具**不生成任何内容**，
   只搬字节 + 数数字。这守住「禁止模型代填」：记录是真实发生过的交换，
   不是模型写的故事。
3. **字段全部从消息里数出来**（`influence` 的手法）：`type` / `who` / `fp` /
   `round` / `reply_to`。`title` 是字段拼接，不是生成。
4. **不进正式结构** —— `load_nodes()` 只读 `nodes/`，这条是既有事实；
   想让一次性树被查询看到，只能**显式指向它**（`LDV_CORPUS=<该目录>`，
   `find_corpus()` 支持路径，实测可用）。
5. **抛弃是显式动作，沉淀也是显式动作** —— 两个动作分开，没有一个开关
   同时做两件事（否则又是「看起来一样、其实不同」）。

## 边界（两条，都是实测撞出来的，如实记）

1. **一次性树没有 `view_spec.json`** ⇒ 抽象层判据（`§A1–A8`，要外生 `P`/`E`）
   跑不了 —— 它是**能查的一次性认识**，不是完整五层树。系统不替它造声明（`§K9`）。
2. **`who` / `round` / `exchange` / `reply_to` 是字段，不是键。**
   `keys_of` 只认三个来源：字段名（零区分力）、`ENUM_FIELDS` 的 `字段=值`、
   `source.kind=`。⇒ 一次性树**可筛的维度只有 `type=`**（QUERY/RESULT/ERROR 三档）。
   ★ 这不是缺陷，是形状：**想按「谁问的」筛，得 promote 后由人声明新维度**
     （`已批准` 流程）——「沉淀是有代价的，代价是人来拍」。
   附带一条实测（`[缓冲] ㊲` 抓到过）：`load_nodes` 用 `rglob`，
   **传语料根目录**（而不是 `<语料>/nodes`）会把 `_staging/` 一起读进来，
   而且静默。真实调用链（`find_corpus`）从不传根 ⇒ 实践中安全；
   但别拿根去调。
"""

from __future__ import annotations

import io
import json
import shutil
import time
from pathlib import Path
from typing import Any
from ._paths import STAGING

#: 缓冲目录名 —— 与 `ldv/tools/staging.py` 的 `STAGING` **故意同名**：
#: 缓冲区是一个概念，不是一个模块的私有财产。改一处不改另一处会漂。
#: 一次性树的父目录（与 `nodes/` / `rejected/` 平级）
EXCHANGES = "exchanges"


def _base(corpus_dir: Path) -> Path:
    return Path(corpus_dir) / STAGING / EXCHANGES


def _ex_dir(corpus_dir: Path, xid: str) -> Path:
    return _base(corpus_dir) / xid


# ── 读消息 ⇒ 造节点 ────────────────────────────────────────────────────────

def _round_of(i: int) -> int:
    """一问一答算一轮（0 基 ⇒ 1 基）。没有 mid/reply_to 时这就够用了。"""
    return i // 2 + 1


def _title(msg: dict, i: int) -> str:
    """从字段**拼**一个标题 —— 不生成任何新内容。"""
    p = msg.get("payload") or {}
    who = str(msg.get("from") or "?")
    mt = str(msg.get("type") or "?")
    if mt == "QUERY":
        req = ",".join(p.get("require") or []) or "（空需求）"
        forb = ",".join(p.get("forbid") or [])
        return f"#{i+1} {who} 问：{req}" + (f"｜不含 {forb}" if forb else "")
    if p.get("ok") is False:
        return f"#{i+1} {who} 拒收/报错：{str(p.get('error') or '')[:40]}"
    n_ans = p.get("n_answer", "?")
    n_br = p.get("n_branches", "?")
    return f"#{i+1} {who} 答：答案 {n_ans} 项／{n_br} 支"


def _node_md(msg: dict, xid: str, i: int, mid: str) -> str:
    """一条消息 ⇒ 一个 Markdown 节点（front matter + 原文 JSON 正文）。"""
    p = msg.get("payload") or {}
    lines = [
        "---",
        f'id: "{xid}-{mid}"',
        f'type: {msg.get("type") or "其他"}',
        f'title: "{_title(msg, i)}"',
        f'who: "{msg.get("from") or ""}"',
        f'fp: "{msg.get("fp") or ""}"',
        f'exchange: "{xid}"',
        f"round: {_round_of(i)}",
    ]
    if msg.get("reply_to") is not None:
        lines.append(f'reply_to: "{msg["reply_to"]}"')
    lines += [
        "---",
        "",
        "<!-- 本节内容 = 消息 payload 的原文 JSON，逐字未改（见 _provenance） -->",
        "",
        "```json",
        json.dumps(p, ensure_ascii=False, indent=1),
        "```",
        "",
        "_provenance：一次交流的原文记录；字段（who/fp/round）从消息信封数出来。_",
        "",
    ]
    return "\n".join(lines)


def ingest(corpus_dir: Path, jsonl: Path, xid: str | None = None) -> dict:
    """`exchange.jsonl` ⇒ `<语料>/_staging/exchanges/<xid>/nodes/*.md`。

    一条消息一个节点；**撞 id ⇒ 进同目录 `rejected/` + 理由文件**（与既有
    缓冲区同形：不覆写、不静默）。已存在同名 xid ⇒ **拒绝**（不混两次交流）。
    """
    corpus_dir = Path(corpus_dir)
    jsonl = Path(jsonl)
    if not jsonl.is_file():
        return {"ok": False, "error": f"找不到 {jsonl}"}
    xid = xid or jsonl.stem
    out_dir = _ex_dir(corpus_dir, xid)
    if out_dir.exists():
        return {"ok": False,
                "error": f"{out_dir} 已存在 —— 一次交流一个目录，不混（要重来得先 abandon）"}
    msgs = [json.loads(ln) for ln in
            io.open(jsonl, encoding="utf-8").read().splitlines() if ln.strip()]
    nodes_dir = out_dir / "nodes"
    rej_dir = out_dir / "rejected"
    nodes_dir.mkdir(parents=True)
    seen: set[str] = set()
    made, rejected = 0, []
    for i, m in enumerate(msgs):
        mid = str(m.get("mid") or f"{i+1:04d}")
        nid = f"{xid}-{mid}"
        if nid in seen:
            rej_dir.mkdir(exist_ok=True)
            (rej_dir / f"{nid}.理由.txt").write_text(
                "id 与本次交流里的另一条消息重复（mid 撞车）⇒ 不进树\n", encoding="utf-8")
            rejected.append(nid)
            continue
        seen.add(nid)
        (nodes_dir / f"{nid}.md").write_text(_node_md(m, xid, i, mid), encoding="utf-8")
        made += 1
    return {"ok": True, "exchange": xid, "目录": str(out_dir),
            "消息": len(msgs), "节点": made, "撞 id 拒收": rejected,
            "树可用": f"LDV_CORPUS={nodes_dir}", "默认命运": "抛弃（显式 abandon）"}


def abandon(corpus_dir: Path, xid: str) -> dict:
    """显式抛弃：删掉这一次交流的整棵一次性树。

    ⚠️ **没有自动淘汰**（用户裁定「遵守缓冲区域的规则，不去特意设计」）：
       既有 `evict` 只扫 `_staging/nodes/`；一次性树的抛弃是**显式动作**。
    ⚠️ 删除前把消息条数写进返回值 —— 删完就再也数不到了。
    """
    d = _ex_dir(Path(corpus_dir), xid)
    if not d.is_dir():
        return {"ok": False, "error": f"没有这次交流：{xid}"}
    n = len(list((d / "nodes").glob("*.md"))) if (d / "nodes").is_dir() else 0
    shutil.rmtree(d)
    return {"ok": True, "抛弃": xid, "删掉的节点": n, "残留": d.exists()}


def promote(corpus_dir: Path, xid: str) -> dict:
    """显式沉淀：把一次性树的节点**移进 `_staging/nodes/`**（之后走既有 commit）。

    ⚠️ 只搬 `nodes/` 里的；`rejected/` 的理由文件是审计链，**不搬**
       （既有纪律：理由永不淘汰、不消失）。
    ⚠️ 搬完本次目录即删 —— 沉淀与抛弃是同一个体面的两种下场。
    """
    d = _ex_dir(Path(corpus_dir), xid)
    src = d / "nodes"
    if not src.is_dir():
        return {"ok": False, "error": f"没有这次交流：{xid}"}
    dst = Path(corpus_dir) / STAGING / "nodes"
    dst.mkdir(parents=True, exist_ok=True)
    moved, skipped = [], []
    for f in sorted(src.glob("*.md")):
        t = dst / f.name
        if t.exists():
            skipped.append(f.name)           # 不覆写（同 commit 的纪律）
            continue
        shutil.move(str(f), str(t))
        moved.append(f.name)
    shutil.rmtree(d)
    return {"ok": True, "沉淀": xid, "移进暂存": len(moved), "撞名跳过": skipped,
            "下一步": "python -m ldv.tools.staging commit <语料目录>"}


def status(corpus_dir: Path) -> dict:
    """列出所有一次性树 —— **只报不淘汰**（沿用 `capacity_status` 的纪律）。"""
    base = _base(Path(corpus_dir))
    out = []
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            n = d / "nodes"
            out.append({
                "exchange": d.name,
                "节点": len(list(n.glob("*.md"))) if n.is_dir() else 0,
                "最后 touch": time.strftime("%Y-%m-%d %H:%M",
                                           time.localtime(d.stat().st_mtime)),
            })
    return {"ok": True, "一次性树": out, "棵": len(out),
            "说明": "只报不淘汰；抛弃 = 显式 abandon（用户裁定）"}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="交流记录 → 缓冲区一次性树")
    ap.add_argument("cmd", choices=["ingest", "abandon", "promote", "status"])
    ap.add_argument("--corpus", required=True, help="语料目录（或其父目录）")
    ap.add_argument("--in", dest="inp", default=None, help="exchange.jsonl（ingest 用）")
    ap.add_argument("--id", dest="xid", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "ingest":
        if not a.inp:
            print("ingest 需要 --in <exchange.jsonl>")
            return 2
        r = ingest(a.corpus, a.inp, a.xid)
    elif a.cmd == "abandon":
        r = abandon(a.corpus, a.xid or "")
    elif a.cmd == "promote":
        r = promote(a.corpus, a.xid or "")
    else:
        r = status(a.corpus)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
