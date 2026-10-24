"""ldv-mcp 场景实跑（逐字贴进文档用）。四件工具面。

跑法（cwd = ldv-mcp）：python ../outputs/_ldv_mcp_demo.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent        # = ldv-mcp/
PY = sys.executable


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

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n,
                                       "method": method, "params": params or {}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        # ★ 探针验的是**结构**（支序 / 答案集 / 计数）⇒ 统一要 structure=True。
        #   2026-10-24 起 ask 默认只交正文 + 句柄（id 留在本地）。
        if name == "ask":
            args = {"structure": True, **args}
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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ldv-mcp-demo-"))
    store = tmp / "store"
    mine = store / "_data" / "mine" / "nodes"
    mine.mkdir(parents=True)
    builtin = HERE / "core" / "ldv" / "corpus" / "nodes"
    for f in sorted(builtin.glob("*.md")):
        shutil.copy2(f, mine / f.name)

    c = MCP(store)
    try:
        c.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "demo", "version": "0"}})

        print("=" * 72)
        print("场景 A｜真人只说一句话，agent 自己 orient + ask")
        print("=" * 72)
        o = c.tool("orient", {})
        print(f"$ orient（内置 36 项，会话开始）")
        print(f"  → 语料 {o.get('corpus')}｜{o.get('n_items')} 项｜{o.get('n_keys')} 个可用键"
              f"｜透镜 frozen={((o.get('lens') or {}).get('frozen'))}")
        print(f"  → 前 6 个键：{(o.get('keys') or [])[:6]}")
        q = c.tool("ask", {"require": ["type=概念"]})
        print(f"\n$ ask require=[\"type=概念\"]")
        print(f"  → {q.get('n_answer')} 项 / {q.get('n_branches')} 支"
              f"｜ordered_by={q.get('ordered_by')}")
        print(f"  → 支序：{[n['did'] for n in (q.get('nodes') or [])]}")
        print(f"  → 出处摘要：{(q.get('provenance') or {}).get('buckets')}")

        print("\n" + "=" * 72)
        print("场景 B｜私人语料：第一次 ask 带透镜 ⇒ 自动冻结；钻；再 orient")
        print("=" * 72)
        q2 = c.tool("ask", {"corpus": "mine", "require": ["type=概念"],
                            "lens": {"插件": "keyset", "参数": {"说明": "按字段交集切分"}},
                            "rationale": "这份材料的字段天然多值，键集切分最直观"})
        print(f"$ ask（带 lens + rationale —— 一步，没有 propose/freeze 两个动作）")
        print(f"  → 透镜 {q2.get('lens')}")
        print(f"  → 用的方向函数：{(q2.get('query') or {}).get('plugin')}")
        did = (q2.get("nodes") or [{}])[0].get("did", "")
        d = c.tool("drill", {"corpus": "mine", "did": did, "require": ["type=概念"]})
        print(f"\n$ drill {did}（agent 代钻，actor 默认 agent）")
        print(f"  → recorded={d.get('recorded')}｜{d.get('record_note')}")
        m = c.tool("remember", {"corpus": "mine", "id": "memo-001",
                                "fields": {"type": "笔记", "topic": "这个项目"},
                                "body": "我记得第一次用它是为了搞清划分树怎么分层。"})
        print(f"\n$ remember（往自己的私人语料写一条）")
        print(f"  → written={m.get('written')}｜filled_by={m.get('filled_by')}")
        o2 = c.tool("orient", {"corpus": "mine"})
        print(f"\n$ orient（私人语料）")
        print(f"  → 项数 {o2.get('n_items')}｜使用记录 {(o2.get('usage') or {}).get('记录数')} 条"
              f"｜演员分解 {(o2.get('actor_split') or {}).get('agent 声明的下钻')}"
              f"/{(o2.get('actor_split') or {}).get('记录数')}")
        print(f"  → 透镜 stale={((o2.get('lens') or {}).get('stale'))}"
              f"（语料多了我写的那条 ⇒ 指纹不符，该重提一个）")
        print(f"  → 最近动作 {len(o2.get('recent_calls') or [])} 条，"
              f"第一个是 {(o2.get('recent_calls') or [{}])[0].get('tool')}")

        print("\n" + "=" * 72)
        print("场景 C｜问一个语料里没有的维度")
        print("=" * 72)
        bad = c.tool("ask", {"corpus": "mine", "require": ["作者国籍=中国"]})
        print(f"$ ask require=[\"作者国籍=中国\"]")
        print(f"  → ok={bad.get('ok')}｜out_of_structure={bad.get('out_of_structure')}")
        print(f"  → {str(bad.get('why'))[:70]}")
        print(f"  → 建议：{bad.get('suggest')}")
    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
