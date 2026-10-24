# -*- coding: utf-8 -*-
"""自我语料：把系统**自己的产物**变成可查的项（`我` 维度，2026-10-22）。

用户 2026-10-22：「加入一个『我』的维度，让系统能有自己的认识」。

## 它抽取什么（机械，**不生成内容**）

    docs/形式化断言集.md 的总表  → 每条断言一个节点（编号 / 级 / 含定理 / 来源）
    docs/*.md + README.md        → 每个文档一个节点（标题 / 种类 / 来源）

字段全部来自**已有文字**（表格列、标题行）；没有一处是模型写的（§S1）。

## `我: 是` 的锚在哪

锚在**仓库里已提交的文件** —— 谁算「我的产物」由人决定（提交动作），
**不按使用频次生成**（§S5 / §K9 同族理由）。删掉输出目录即回到原状。

## 用法

    python -m ldv.tools.make_self_corpus --out <目录>
    LDV_CORPUS=<目录>/nodes python entry.py

退出码：0 = 抽取成功且自描述一致（总表行数 == 断言集引言声称的条数）；1 = 不一致。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

#: 总表一行：`| **K1** | 断言 | 级 | 脱离系统？ | 证明用什么 |`
_ROW = re.compile(r"^\| \*\*([A-Z]\d+)\*\* \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$")
#: 引言里的条数：`让 31 条断言脱离 ldv 系统存在`
_CLAIM = re.compile(r"让 (\d+) 条断言脱离")
_TIERS = ("Ⅰ", "Ⅱ", "Ⅲ")


def _clean(s: str) -> str:
    return s.replace("**", "").strip()


def assertion_doc(repo: Path) -> Path:
    return Path(repo) / "docs" / "形式化断言集.md"


def parse_assertions(repo: Path) -> list[dict[str, Any]]:
    """从断言集的总表抽每条断言：编号 / 断言 / 级 / 含定理。**只读文字，不生成。**"""
    text = io.open(assertion_doc(repo), encoding="utf-8").read()
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        no, title, tier = m.group(1), _clean(m.group(2)), _clean(m.group(3))
        out.append({"编号": no, "断言": title, "级": tier,
                    "含定理": "是" if "Ⅰ" in tier else "否"})
    return out


def claimed_count(repo: Path) -> int:
    """断言集引言**声称**的条数（用来核自描述一致）。"""
    text = io.open(assertion_doc(repo), encoding="utf-8").read()
    m = _CLAIM.search(text)
    return int(m.group(1)) if m else -1


def parse_docs(repo: Path) -> list[dict[str, Any]]:
    """docs/*.md + README.md ⇒ 每个文档一条（标题取第一个 `# ` 行）。"""
    repo = Path(repo)
    files = sorted((repo / "docs").glob("*.md")) + [repo / "README.md"]
    out: list[dict[str, Any]] = []
    for f in files:
        if not f.is_file():
            continue
        title = ""
        for line in io.open(f, encoding="utf-8").read().splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        out.append({"路径": f.relative_to(repo).as_posix(),
                    "标题": title or f.stem, "种类": "文档"})
    return out


def _fm(fields: dict[str, str], source_ref: str) -> str:
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f'{k}: "{v}"')
    lines += ["source:", f'  ref: "{source_ref}"', '  kind: "仓库文档"', "---", "",
              "机械抽取的自我项：字段全部来自仓库里已有的文字（见 source）。", ""]
    return "\n".join(lines)


def build(repo: Path, out: Path) -> dict[str, Any]:
    """生成自我语料（nodes/*.md）。返回统计；不写任何别的东西。"""
    repo, out = Path(repo), Path(out)
    nodes = out / "nodes"
    nodes.mkdir(parents=True, exist_ok=True)
    for old in nodes.glob("*.md"):
        old.unlink()                      # 重跑保护：只清自己生成的那一类

    rows = parse_assertions(repo)
    for r in rows:
        nid = f"asrt-{r['编号']}"
        fields = {"id": nid, "title": f"{r['编号']} {r['断言']}", "种类": "断言",
                  "编号": r["编号"], "级": r["级"], "含定理": r["含定理"], "我": "是"}
        (nodes / f"{nid}.md").write_text(_fm(fields, "docs/形式化断言集.md"),
                                         encoding="utf-8")

    docs = parse_docs(repo)
    for d in docs:
        nid = "doc-" + re.sub(r"[^0-9A-Za-z\u4e00-\u9fff-]+", "_", d["路径"])[:-3]
        fields = {"id": nid, "title": d["标题"], "种类": "文档", "我": "是"}
        (nodes / f"{nid}.md").write_text(_fm(fields, d["路径"]), encoding="utf-8")

    tiers: dict[str, int] = {}
    for r in rows:
        tiers[r["级"]] = tiers.get(r["级"], 0) + 1
    return {"断言": len(rows), "文档": len(docs), "节点": len(rows) + len(docs),
            "声称条数": claimed_count(repo), "分级": dict(sorted(tiers.items())),
            "目录": str(nodes)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成自我语料（`我` 维度）")
    ap.add_argument("--out", required=True, help="输出目录（会在其下建 nodes/）")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]),
                    help="仓库根（默认本文件上两级）")
    a = ap.parse_args(argv)
    st = build(Path(a.repo), Path(a.out))
    print(json.dumps(st, ensure_ascii=False, indent=1))
    okk = st["断言"] == st["声称条数"]
    print(("✓ 自描述一致：总表 %d 行 == 引言声称 %d 条" if okk
           else "✗ 自描述不一致：总表 %d 行 ≠ 引言声称 %d 条")
          % (st["断言"], st["声称条数"]))
    return 0 if okk else 1


if __name__ == "__main__":
    raise SystemExit(main())
