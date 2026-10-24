# -*- coding: utf-8 -*-
"""引文核对记录：把「310 条引文全对上」这句**宣称**变成**可查的清单**（2026-10-20）。

## 为什么

`sources/` 里是 45 MB 的**版权正文**（论文 PDF/抽取文本），**不能进仓库**。
但仓库文档里写着「引文 310 条｜全覆盖 310｜缺口 0」—— 远端读者只能**相信**，
不能**核对**：既看不到正文，也看不到核对记录。

⇒ 这个工具生成 `evidence/quotes-index.md`：

    每条引文 → 出自**哪个源档** → 覆盖率 / 未覆盖片段

它**不含正文**（只有引文本身，而引文本来就写在仓库文档里），
所以不涉版权；但它让「哪一句从哪来」变成**逐条可查**。

## 用法

    python -m ldv.tools.quote_index write    # 需要工作区根有 sources/ 与 .prior-art/
    python -m ldv.tools.quote_index check    # **不需要正文**：只核对「文档里的引文都在索引里」

⚠️ `write` 只能在**有正文的机器**上跑；`check` 可以在 CI 上跑 ——
   两者分工是「生成」与「守同步」。
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
from pathlib import Path
from ._paths import REPO

WS = REPO.parent
OUT = REPO / "evidence" / "quotes-index.md"

_DOCS = ([REPO / "README.md", REPO / "ldv" / "MEASUREMENTS.md"]
         + sorted((REPO / "docs").glob("*.md")))


def _load_vq():
    """导入 `docs/prior-art/verify_quotes.py`（它按脚本方式写的，用 spec 加载）。"""
    p = REPO / "docs" / "prior-art" / "verify_quotes.py"
    spec = importlib.util.spec_from_file_location("_vq", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _quote_rows(vq) -> list[tuple[str, int, str]]:
    """`[(文档相对路径, 行号, 引文)]`。"""
    rows = []
    for p in _DOCS:
        if not p.is_file():
            continue
        rel = p.relative_to(REPO).as_posix()
        for ln, q in vq.extract_quotes(io.open(p, encoding="utf-8").read()):
            rows.append((rel, ln, q))
    return rows


def _best_source(vq, q: str, srcs: dict[str, str],
                 pooled) -> tuple[str, float, list[str]]:
    """找**逐字包含**这条引文的那个源档。

    先做**逐源精确包含**（快、可指认到具体文件）；一个都对不上再退回
    **合并语料上的覆盖率**（`pooled = (grams, flat)`），并标「未定位」——
    「哪一句从哪来」必须能落到**具体文件**，落不到就如实说落不到。
    """
    frags = [vq.norm(f) for f in vq.fragments(q)]
    for name, text in srcs.items():
        if frags and all(f in text for f in frags):
            return name, 1.0, []
    grams, flat = pooled
    total, gaps, n = 0.0, [], 0
    for f in frags:
        ratio, g = vq.coverage(f, grams, flat)
        total += ratio
        gaps += g
        n += 1
    return "（未定位）", (total / n if n else 0.0), gaps


def write() -> Path:
    """按**官方口径**（`verify_quotes` 的 K / COVER_MIN / GAP_MIN / MIN_FRAG）逐条判，
    再给每条标一个**源档**。

    ⚠️ 判定**必须与 `verify_quotes` 逐字同一条**：第一版我用「逐源精确包含」当判据，
       得出「缺口 9」—— 而官方是 310/310/0。那不是文档有问题，是**我换了一把尺子**
       （官方是**合并语料上的 k-gram 覆盖**，容忍 OCR/旧排版归一化）。
       ⇒ 判据照抄官方；「源档」只是**附加信息**，落不到具体文件就写「（合并语料）」。
    """
    vq = _load_vq()
    srcs = vq.load_sources()
    grams: set = set()
    for s in srcs.values():
        grams |= vq.kgrams(s)
    flat = "".join(srcs.values())

    rows = []
    for rel, ln, body in _quote_rows(vq):
        q = vq.norm(body)
        if len(q) < 8:
            continue
        worst, okk = 1.0, True
        for frag in vq.fragments(q):
            if len(frag) < vq.MIN_FRAG:
                continue
            ratio, g = vq.coverage(frag, grams, flat)
            worst = min(worst, ratio)
            if ratio < vq.COVER_MIN or g:
                okk = False
        src = "（合并语料）"
        for name, text in srcs.items():
            if q in text:
                src = name
                break
        rows.append((rel, ln, q, src, worst, okk))

    full = sum(1 for r in rows if r[5])
    head = ["# 引文核对记录（`evidence/quotes-index.md`）", "",
            "> 仓库文档里每条英文引文**出自哪个源档、覆盖率多少**，逐条列出。", "",
            "⚠️ **不含正文**：`sources/` 里是版权论文，不进仓库。这里只有**引文本身**"
            "（它们本来就写在仓库文档里）+ **源档名** + **覆盖率**。",
            "⇒ 读者拿不到正文也能核对「这句话是不是从某篇论文里逐字抄来的」；",
            "   要复核全文，按源档名去取原文即可。", "",
            f"**引文 {len(rows)} 条｜全覆盖 {full}｜缺口 {len(rows) - full}**；源档 {len(srcs)} 个。", "",
            "口径与 `docs/prior-art/verify_quotes.py` **逐字同一条**"
            "（合并语料上的 k-gram 覆盖，`K=12` / `COVER_MIN=0.90` / `GAP_MIN=25`）。", "",
            "| 文档 | 行 | 引文（截断） | 源档 | 最低覆盖比 |",
            "|---|---|---|---|---|"]
    for rel, ln, q, src, worst, okk in rows:
        flag = "" if okk else " **⚠️缺口**"
        head.append(f"| `{rel}` | {ln} | {q[:44].replace('|', chr(92) + '|')} | "
                    f"`{src}` | {worst:.2f}{flag} |")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(head) + "\n", encoding="utf-8")
    return OUT


def check() -> tuple[bool, dict]:
    """**不需要正文**：索引里的引文集合必须与仓库文档的引文集合**逐条对齐**。

    比的是「**归一化后的前 40 字**」的多重集（不是行号 —— 行号会随文档改动漂，
    那正是需要重生成索引的时候）。`|` 会被转义，比较前先还原。
    """
    from collections import Counter

    if not OUT.is_file():
        return False, {"原因": "evidence/quotes-index.md 不存在", "文档引文": 0,
                       "索引记": 0, "多": [], "缺": []}
    vq = _load_vq()

    def key(s: str) -> str:
        return re.sub(r"\s+", "", s.replace("\\|", "|"))[:40]

    docs_c: Counter = Counter()
    for _rel, _ln, body in _quote_rows(vq):
        q = vq.norm(body)
        if len(q) >= 8:
            docs_c[key(q)] += 1
    idx_c: Counter = Counter()
    for line in io.open(OUT, encoding="utf-8").read().splitlines():
        if not line.startswith("| `"):
            continue
        # ⚠️ 单元格里的 `|` 被转义成 `\|` —— **先换成占位符再 split**，
        #    否则数学式里的竖线会把一个格子劈成两个（实测缺了 1 条）。
        safe = line.replace("\\|", "\x00")
        cells = [c.strip().replace("\x00", "|") for c in safe.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        idx_c[key(cells[2])] += 1
    missing = list((docs_c - idx_c).elements())
    extra = list((idx_c - docs_c).elements())
    return (not missing and not extra,
            {"文档引文": sum(docs_c.values()), "索引记": sum(idx_c.values()),
             "缺": missing[:5], "多": extra[:5]})

def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "write":
        p = write()
        print(f"✓ 已写 {p.relative_to(REPO)}")
        return 0
    if cmd == "check":
        okk, r = check()
        print(f"文档引文 {r.get('文档引文')} 条｜索引记 {r.get('索引记')}｜"
              f"缺 {len(r.get('缺', []))}")
        for x in r.get("缺", [])[:5]:
            print(f"  ✗ {x[0]}:{x[1]} {x[2][:40]}")
        if r.get("原因"):
            print(f"  ✗ {r['原因']}")
        print("✓ 索引与文档同步" if okk else "✗ 索引缺条目")
        return 0 if okk else 1
    print(f"未知命令 {cmd!r}（write / check）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
