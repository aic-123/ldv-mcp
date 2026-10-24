"""蜕变测试：不判「答案对不对」，判「输入变了之后答案该怎么变」。

跑法：python probes/metamorphic.py

## 为什么用这一族（而不是再写更多例子）

例子驱动的测试（S1–S29）回答的是「这个输入下输出是什么」。
但检索系统最要紧的性质是**关系**：加一条会怎样、删一条会怎样、换个键序会不会变。
这些「预期值」根本写不出来（语料一换就全变），但**关系是稳定的**。
—— 这就是蜕变测试（Metamorphic Testing, Chen 1998）解决的问题：**测试准则问题**。

## 八条蜕变关系（MR）

    MR1 键序无关        require=[a,b] 与 [b,a] 的答案集相同
    MR2 加无关项不变    加一条**不满足**约束的项 ⇒ 答案集不变
    MR3 加相关项只增    加一条**满足**约束的项 ⇒ 答案集恰好 +1
    MR4 加约束只缩      多加一个键 ⇒ 答案集是原来的子集
    MR5 改正文不变      只改 body、不动 front matter ⇒ 答案集不变（结构只由键决定）
    MR6 换文件名不变    重命名节点文件（id 在 front matter 里）⇒ 答案集不变
    MR7 加维度不扰      加一个派生维度 ⇒ **不涉及该维度**的查询答案不变
    MR8 删一项只减      删一条 ⇒ 答案集恰好 −1

⚠️ 每条都写清**它抓什么**：MR 的价值在于「违反了说明哪里错了」，不是「过了说明对」。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PY = sys.executable
FAIL = 0


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}" + (f"\n        {detail}" if detail else ""))


class MCP:
    def __init__(self, store: Path):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LDV_MCP_STORE"] = str(store)
        self.p = subprocess.Popen([PY, str(HERE / "server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  env=env, cwd=str(HERE))
        self.n = 0
        self.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "mr", "version": "0"}})

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        r = self.call("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        txt = "".join(i.get("text", "") for i in (res.get("content") or []))
        try:
            return json.loads(txt) if txt else {}
        except ValueError:
            return {"_raw": txt}

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def node_md(nid: str, kind: str, body: str, project: str = "x") -> str:
    return f'---\nid: "{nid}"\ntype: {kind}\nproject: {project}\n---\n\n{body}\n'


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ldv-mr-"))
    store = tmp / "store"
    nodes = store / "_data" / "m" / "nodes"
    nodes.mkdir(parents=True)
    src = HERE / "core" / "ldv" / "corpus" / "nodes"
    for f in sorted(src.glob("*.md")):
        shutil.copy2(f, nodes / f.name)

    c = MCP(store)

    def ans(req, forb=None):
        r = c.tool("ask", {"corpus": "m", "require": req, "forbid": forb or [],
                           "structure": True, "limit": 999})
        return tuple(r.get("answer_ids") or ())

    try:
        print("=" * 78)
        print("蜕变测试（8 条关系）｜内置 36 项语料")
        print("=" * 78)
        base = ans(["type=立场"])
        print(f"\n基线：type=立场 ⇒ {len(base)} 项")

        # MR1 键序无关
        a = ans(["type=立场", "source.kind=论文"])
        b = ans(["source.kind=论文", "type=立场"])
        chk("MR1", "键序无关：require 的先后不影响答案集", a == b, f"{len(a)} vs {len(b)}")

        # MR2 加无关项不变（type=案例 不满足 type=立场）
        (nodes / "mr2.md").write_text(node_md("mr-2", "案例", "一条无关的案例"),
                                      encoding="utf-8")
        after2 = ans(["type=立场"])
        chk("MR2", "加一条**不满足**约束的项 ⇒ 答案集不变", after2 == base,
            f"{len(base)} → {len(after2)}")
        (nodes / "mr2.md").unlink()

        # MR3 加相关项只增（恰好 +1）
        (nodes / "mr3.md").write_text(node_md("mr-3", "立场", "一条新立场"),
                                      encoding="utf-8")
        after3 = ans(["type=立场"])
        chk("MR3", "加一条**满足**约束的项 ⇒ 答案集恰好 +1",
            len(after3) == len(base) + 1 and set(base) <= set(after3),
            f"{len(base)} → {len(after3)}")
        (nodes / "mr3.md").unlink()

        # MR4 加约束只缩 —— ⚠️ 必须**严格**变小，否则这条关系是空转
        #   第一版用 type=立场 + source.kind=论文 ⇒ 3 vs 3（立场全是论文）
        #   ⇒ `a ⊆ base` 恒真，**等于没测**。换成真能缩的一对：概念 8 → 教材 3。
        wide = ans(["type=概念"])
        narrow = ans(["type=概念", "source.kind=教材"])
        chk("MR4", "多加一个键 ⇒ 答案集**严格**变小（不是「不变」冒充「子集」）",
            set(narrow) < set(wide) and 0 < len(narrow),
            f"{len(wide)} → {len(narrow)}（严格子集：{set(narrow) < set(wide)}）")

        # MR5 改正文不变（结构只由 front matter 的键决定）
        target = next(iter(base))
        f = next(p for p in nodes.glob("*.md")
                 if f'id: "{target}"' in p.read_text(encoding="utf-8"))
        old = f.read_text(encoding="utf-8")
        head, _sep, _body = old.partition("---\n\n")
        f.write_text(head + "---\n\n" + "正文被整段替换（键一个没动）。\n",
                     encoding="utf-8")
        after5 = ans(["type=立场"])
        chk("MR5", "只改正文、不动 front matter ⇒ 答案集不变（结构只由键决定）",
            after5 == base, f"{len(base)} → {len(after5)}")
        f.write_text(old, encoding="utf-8")

        # MR6 换文件名不变（id 在 front matter 里）
        f2 = next(p for p in nodes.glob("*.md")
                  if f'id: "{target}"' in p.read_text(encoding="utf-8"))
        newp = f2.with_name("zz-renamed.md")
        f2.rename(newp)
        after6 = ans(["type=立场"])
        chk("MR6", "重命名节点文件（id 在 front matter 里）⇒ 答案集不变",
            after6 == base, f"{len(base)} → {len(after6)}")
        newp.rename(f2)

        # MR7 加派生维度不扰（不涉及该维度的查询答案不变）
        vals = {}
        rd = c.tool("ask", {"corpus": "m", "require": [], "structure": True,
                            "limit": 999})
        for nid, it in zip(rd.get("answer_ids") or [], rd.get("items") or []):
            p = (it.get("fields") or {}).get("project")
            if p:
                vals[nid] = p
        c.tool("derive", {"corpus": "m", "name": "project", "values": vals,
                          "rationale": "MR7 用", "derived_from": "材料字段 project"})
        after7 = ans(["type=立场"])
        chk("MR7", "加一个派生维度 ⇒ **不涉及该维度**的查询答案不变",
            after7 == base, f"{len(base)} → {len(after7)}")

        # MR8 删一项只减（恰好 −1）
        f3 = next(p for p in nodes.glob("*.md")
                  if f'id: "{target}"' in p.read_text(encoding="utf-8"))
        keep = f3.read_text(encoding="utf-8")
        f3.unlink()
        after8 = ans(["type=立场"])
        chk("MR8", "删一条 ⇒ 答案集恰好 −1（只减，不会连带别的）",
            len(after8) == len(base) - 1 and set(after8) == set(base) - {target},
            f"{len(base)} → {len(after8)}")
        f3.write_text(keep, encoding="utf-8")

        print("\n每条 MR 抓什么：")
        print("  MR1 键序 ⇒ 哈希/集合用错（把 require 当有序）")
        print("  MR2 无关项 ⇒ 谓词过宽（把不该进来的放进来）")
        print("  MR3 相关项 ⇒ 插入漏、或键没生效")
        print("  MR4 加约束 ⇒ 交集算错（越加越大）")
        print("  MR5 改正文 ⇒ 结构偷偷依赖了 body")
        print("  MR6 换文件名 ⇒ 拿文件名当 id")
        print("  MR7 加维度 ⇒ 派生维度污染了无关查询")
        print("  MR8 删一项 ⇒ 删不干净（留幽灵成员）")
    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（MR1–MR8）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
