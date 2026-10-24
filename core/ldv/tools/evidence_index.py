# -*- coding: utf-8 -*-
"""证据索引：**仓库里每一处「实测/留档」引用都必须有实体**（2026-10-20）。

## 为什么有这个文件

2026-10-20 用户：「现在测试和引用之类的内容只有宣称而没有实体」。

实测：仓库文档里引用了 **130+** 个 `outputs/...` 的留档，而那些文件全在工作区根的
`outputs/` 下 —— **`outputs/` 不在仓库里** ⇒ 远端读者一个都看不到，
「实测见 `outputs/_probe_x.py`」在 GitHub 上是**悬空引用**。

⇒ 把被引用的留档搬进 `repo/evidence/`，文档里的路径一起改成 `evidence/...`，
再由这个工具守两件事：

    check    ① 每处 `evidence/X` 引用都能解析到一个**存在的文件**
             ② `evidence/` 里**没有孤儿**（每个文件至少被一处引用）
             ③ `repo/` 文档里**不再出现 `outputs/`**（那是工作区路径，远端没有）
    write    重新生成 `evidence/INDEX.md`（文件 → 被谁引用）

## 解析规则

`evidence/` 后面的 token 会一直取到空白 / 括号 / 引号 / 竖线为止，然后**从右往左截尾**
直到命中一个真实文件 —— 因为中文正文里文件名后面常紧跟「（逐字）」这类说明，
不截尾就会把说明当成文件名的一部分（实测踩到 3 次）。

另外支持两种写法：`a-{x,y}_b.txt`（花括号展开）与 `a_*.py`（通配）。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from ._paths import REPO

EV = REPO / "evidence"

#: 取 token：允许 `,` `*` `{` `}`（花括号/通配要用），遇到空白与引号类就停
_TOKEN = re.compile(r"evidence/([^\s\)\|（）：:`'\"」』】\]]+)")
#: 工作区路径 —— 仓库文档里**不许出现**
_WS_PATH = re.compile(r"outputs/[^\s\)\|（）：:`'\"」』】\]]+")


def docs() -> list[Path]:
    return ([REPO / "README.md", REPO / "ldv" / "MEASUREMENTS.md"]
            + sorted((REPO / "docs").glob("*.md")))


def resolve(name: str) -> str | None:
    """从右往左截尾，返回**命中的真实相对路径**；命中不了返回 `None`。"""
    s = name
    while s:
        if (EV / s).is_file():
            return s
        s = s[:-1]
    return None


def scan() -> dict:
    """扫全仓文档，返回 `{used, unresolved, orphans, ws_refs, files}`。"""
    used: dict[str, set[str]] = {}
    unresolved: list[tuple[str, str]] = []
    ws_refs: list[tuple[str, str]] = []
    for p in docs():
        if not p.is_file():
            continue
        rel = p.relative_to(REPO).as_posix()
        text = io.open(p, encoding="utf-8").read()
        for m in _WS_PATH.finditer(text):
            ws_refs.append((rel, m.group(0)))
        for m in _TOKEN.finditer(text):
            raw = m.group(1)
            if "{" in raw and "}" in raw:
                head, body = raw.split("{", 1)
                inner, tail = body.split("}", 1)
                for item in inner.split(","):
                    fn = resolve(head + item + tail)
                    if fn:
                        used.setdefault(fn, set()).add(rel)
                continue
            if "*" in raw:
                for f2 in EV.glob(raw):
                    used.setdefault(f2.relative_to(EV).as_posix(), set()).add(rel)
                continue
            fn = resolve(raw)
            if fn:
                used.setdefault(fn, set()).add(rel)
            else:
                unresolved.append((rel, raw))
    files = sorted(p.relative_to(EV).as_posix() for p in EV.rglob("*") if p.is_file())
    #: 两个**索引**不是证据本身（它们指路，不被指）—— 排除掉，否则自己就成了孤儿
    _INDEXES = {"INDEX.md", "quotes-index.md"}
    orphans = [f for f in files if f not in used and f not in _INDEXES]
    return {"used": used, "unresolved": unresolved, "orphans": orphans,
            "ws_refs": ws_refs, "files": files}


def check() -> tuple[bool, dict]:
    r = scan()
    okk = not r["unresolved"] and not r["orphans"] and not r["ws_refs"]
    return okk, r


def write_index() -> Path:
    r = scan()
    lines = ["# 证据索引（`evidence/`）", "",
             "> 仓库里每一处「实测 / 留档」的引用都指向这里的文件。**不是宣称，是实体。**", "",
             f"共 **{len(r['files'])}** 个文件。下表按文件名排；「被谁引用」是仓库内引用它的文档。", "",
             "| 文件 | 大小 | 被谁引用 |", "|---|---|---|"]
    for f in r["files"]:
        if f in ("INDEX.md", "quotes-index.md"):
            continue
        kb = (EV / f).stat().st_size / 1024
        refs = sorted(r["used"].get(f, []))
        lines.append(f"| `{f}` | {kb:.0f} KB | {'、'.join(refs) if refs else '**（无）**'} |")
    if r["orphans"]:
        lines += ["", "## 没被正文引用、但同趟产出的伴生件", "",
                  "主脚本被引用了，这些是它跑出来的输出，跟着一起进仓库：", ""]
        lines += [f"- `{f}`" for f in r["orphans"]]
    lines += ["", "## 怎么重跑", "",
              "这些脚本原本在**工作区根**（`outputs/`）下跑，头部写着「cwd = repo」之类的跑法。",
              "搬进 `repo/evidence/` 后**路径前提变了** —— 它们在这里是**留档**，不是开箱即用的工具。",
              "要重跑：复制回工作区根的 `outputs/`，或按脚本头部把 cwd 调成 `repo/`。", ""]
    p = EV / "INDEX.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "write":
        p = write_index()
        print(f"✓ 已写 {p.relative_to(REPO)}（{len(scan()['files'])} 个文件）")
        return 0
    if cmd == "check":
        okk, r = check()
        print(f"文件 {len(r['files'])} 个｜被引用 {len(r['used'])} 个｜"
              f"解析不了 {len(r['unresolved'])}｜孤儿 {len(r['orphans'])}｜"
              f"残留 outputs/ {len(r['ws_refs'])}")
        for rel, n in r["unresolved"][:5]:
            print(f"  ✗ 引用不到：{rel} → evidence/{n}")
        for f in r["orphans"][:5]:
            print(f"  ✗ 孤儿：evidence/{f}")
        for rel, s in r["ws_refs"][:5]:
            print(f"  ✗ 工作区路径：{rel} → {s}")
        print("✓ 全部有实体" if okk else "✗ 有缺口")
        return 0 if okk else 1
    print(f"未知命令 {cmd!r}（check / write）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
