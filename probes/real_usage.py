# -*- coding: utf-8 -*-
"""真实使用流程 + 边缘矩阵 —— 检验各功能在实际用法里是否**真的能用**。

与 `smoke_test.py` 的分工：
    smoke   每条判据守**一个**承诺（点）
    real_usage  模拟**一个 agent 的完整轨迹**（线），再加**边缘矩阵**（面）

    轨迹 = 空库 → 写三条 → 查 → 下钻 → 派生维度 → 反事实 → 编译答案 →
           语料变 → 缓存过期 → 判错 → 支级跳过 → 收尾对账
    边缘 = 26 条「用户真会这么用」的怪输入（空/越界/编造/超长/跨查询/多语料）

⚠️ **三轮**：整个轨迹在三个**独立 store** 上各跑一遍，读数必须逐字相同 ——
   否则「今天能用」只是运气（确定性是产品承诺的一部分，不是测试技巧）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent      # ldv-mcp/
PY = sys.executable
FAIL = 0
ROWS: list[str] = []


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global FAIL
    if not good:
        FAIL += 1
    line = f"  [{'过' if good else '红'}] {tag} {what}" + (f"\n        {detail}" if detail else "")
    print(line)
    ROWS.append(line)


class MCP:
    """最小 MCP 客户端（与 smoke 同款：写一行、读一行）。"""

    def __init__(self, store: Path):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LDV_MCP_STORE"] = str(store)
        self.p = subprocess.Popen([PY, str(HERE / "server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True,
                                  encoding="utf-8", env=env, cwd=str(HERE))
        self.n = 0
        self.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "real", "version": "0"}})

    def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n,
                                       "method": method, "params": params or {}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name: str, args: dict) -> dict:
        r = self.call("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        txt = ""
        for item in res.get("content") or []:
            if item.get("type") == "text":
                txt += item.get("text", "")
        try:
            out = json.loads(txt) if txt else {}
        except ValueError:
            out = {"_raw": txt}
        return {"out": out, "isError": bool(res.get("isError"))}

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:                              # noqa: BLE001
            pass


def seed(nodes: Path) -> None:
    """三条起始笔记（真实形状：有 type / project / source）。"""
    docs = [
        ("n1", "立场", "alpha", "官方文档",
         "DAPO 的问题出在损失聚合与裁剪上界：token 级平均会把长回答的梯度摊薄。"),
        ("n2", "判断点", "alpha", "论文",
         "长度惩罚与 clip-higher 是候选处置，但两者一起用会互相抵消。"),
        ("n3", "例外", "beta", "个人笔记",
         "在 batch=64 以上时，熵崩得更快，这条在 alpha 的结论上不成立。"),
    ]
    for nid, typ, proj, src, body in docs:
        (nodes / f"{nid}.md").write_text(
            f'---\nid: "{nid}"\ntype: {typ}\nproject: {proj}\nsource.kind: {src}\n'
            f'---\n\n{body}\n', encoding="utf-8", newline="\n")


def run_flow(store: Path) -> list[str]:
    """一个 agent 的真实轨迹。返回**读数行**（用于三轮逐字比较）。"""
    print("\n── 真实轨迹：空库 → … → 收尾对账 ──")
    nodes = store / "_data" / "flow" / "nodes"
    nodes.mkdir(parents=True)
    c = MCP(store)
    seen: list[str] = []
    try:
        # ① 空库先 orient（真人第一步：看看这有什么）
        o0 = c.tool("orient", {"corpus": "flow"})
        seen.append(f"空库项数={o0['out'].get('n_items')}")
        chk("F01", "空库 orient 能起来（**0 项**，不报错、不写占位文件）",
            o0["out"].get("ok") is not False and o0["out"].get("n_items") is not None,
            f"n_items={o0['out'].get('n_items')}｜keys={o0['out'].get('keys')}")

        # ② 空库直接 ask（边缘：还没有材料）
        e0 = c.tool("ask", {"corpus": "flow", "require": []})
        # ★ 空库就是 **0 项**（2026-10-25 用户定：不再塞占位文件）。
        #   ⚠️ 这里原先写的是「基石 1 项」—— 那个设计已经删了。
        chk("F02", "空库 ask ⇒ 不崩、0 项、且**目录里没被写进任何文件**",
            e0["out"].get("ok") is True
            and len(e0["out"].get("items") or []) == 0
            and e0["out"].get("n_answer") == 0
            and not any(nodes.glob("*.md")),
            f"items={len(e0['out'].get('items') or [])}"
            f"｜n_answer={e0['out'].get('n_answer')}"
            f"｜目录文件数={len(list(nodes.glob('*.md')))}")

        # ③ 写三条
        for nid, typ, proj, src, body in [
            ("n1", "立场", "alpha", "官方文档", "DAPO 的问题出在损失聚合与裁剪上界。"),
            ("n2", "判断点", "alpha", "论文", "长度惩罚与 clip-higher 会互相抵消。"),
            ("n3", "例外", "beta", "个人笔记", "batch=64 以上熵崩得更快。")]:
            c.tool("remember", {"corpus": "flow", "id": nid, "body": body,
                                "fields": {"type": typ, "project": proj,
                                           "source.kind": src}})
        o1 = c.tool("orient", {"corpus": "flow"})
        seen.append(f"写入后项数={o1['out'].get('n_items')}")
        chk("F03", "写三条后 orient 看到 3 项（空库就是 0 项，没有占位文件）",
            o1["out"].get("n_items") == 3
            and any(str(k).startswith("type=") for k in (o1["out"].get("keys") or [])),
            f"n_items={o1['out'].get('n_items')}｜keys={o1['out'].get('keys')}")

        # ④ 查全部 / 查一支
        a1 = c.tool("ask", {"corpus": "flow", "require": []})
        a2 = c.tool("ask", {"corpus": "flow", "require": ["type=立场"]})
        seen.append(f"全库={a1['out'].get('n_answer')}｜立场={a2['out'].get('n_answer')}")
        chk("F04", "全库 3 项、type=立场 1 项，默认只给正文 + 句柄（无 id）",
            a1["out"].get("n_answer") == 3 and a2["out"].get("n_answer") == 1
            and "answer_ids" not in a1["out"]
            and (a1["out"].get("items") or [{}])[0].get("n") == 1,
            f"全库={a1['out'].get('n_answer')}｜立场={a2['out'].get('n_answer')}"
            f"｜有 answer_ids={'answer_ids' in a1['out']}")

        # ⑤ 下钻（用句柄，不给 did）
        # ⚠️ **句柄表是每次 ask 覆盖的**：F04 最后那次 ask 查的是 type=立场（1 条），
        #   直接 drill 会报「句柄属于另一次查询」—— 那是**正确**行为，但不是这条要测的。
        #   ⇒ 先按同样的 require 再 ask 一次，把句柄表刷回来（真实用法也是这样）。
        #   （这也是个真实陷阱：连问两问之后用旧编号会被拒。E08 专门守它。）
        c.tool("ask", {"corpus": "flow", "require": []})
        d1 = c.tool("drill", {"corpus": "flow", "n": 2, "require": []})
        chk("F05", "drill(n=2) 能用句柄动作（不必知道 did）",
            d1["out"].get("ok") is True and d1["out"].get("did"),
            f"did={d1['out'].get('did')}｜记={d1['out'].get('recorded')}"
            f"｜note={str(d1['out'].get('record_note'))[:34]}")

        # ⑥ 派生维度（值来自材料字段）
        dv = c.tool("derive", {"corpus": "flow", "name": "project",
                               "values": {"n1": "alpha", "n2": "alpha", "n3": "beta"},
                               "rationale": "按项目看", "derived_from": "材料字段 project"})
        a3 = c.tool("ask", {"corpus": "flow", "require": ["project=alpha"]})
        chk("F06", "derive 建维度 ⇒ 立刻能查（project=alpha ⇒ 2 项）",
            dv["out"].get("ok") is True and a3["out"].get("n_answer") == 2,
            f"derive={dv['out'].get('ok')}｜alpha={a3['out'].get('n_answer')}"
            f"｜派生标记={a3['out'].get('derived_used')}")

        # ⑦ 反事实（只算不改）
        u1 = c.tool("undo", {"corpus": "flow", "id": "n1", "require": []})
        a4 = c.tool("ask", {"corpus": "flow", "require": []})
        chk("F07", "undo 只算不改：报「会丢 n1」，材料一个字节没动",
            "n1" in (u1["out"].get("answer_lost") or [])
            and u1["out"].get("untouched") is True
            and a4["out"].get("n_answer") == 3,
            f"lost={u1['out'].get('answer_lost')}｜untouched={u1['out'].get('untouched')}"
            f"｜事后仍 {a4['out'].get('n_answer')} 项")

        # ⑧ 编译答案（把 agent 的回答编进树）
        ra = c.tool("remember_answer", {"corpus": "flow",
                                        "require": ["type=立场"],
                                        "answer": "DAPO 的问题在损失聚合与裁剪上界。",
                                        "source": ["n1"]})
        a5 = c.tool("ask", {"corpus": "flow", "require": ["type=立场"]})
        chk("F08", "remember_answer 后同类问题命中缓存（单独一栏 from_cache）",
            ra["out"].get("ok") is True
            and (a5["out"].get("cached_answer") or {}).get("from_cache") is True,
            f"写入={ra['out'].get('ok')}"
            f"｜from_cache={(a5['out'].get('cached_answer') or {}).get('from_cache')}")

        # ⑧-b 自己的产物进结构（2026-10-25）：答完之后「我答过什么」查得到。
        #   这是「外挂主体性」里最像主体的那一环 —— 不然 AI 的产物只是缓存。
        mine = c.tool("ask", {"corpus": "flow", "require": ["我=是"]})
        mine_ans = c.tool("ask", {"corpus": "flow", "require": ["type=回答"]})
        chk("F13", "自己的产物进结构：答完 ⇒ `我=是` / `type=回答` 查得到（正文是我写的）",
            mine["out"].get("n_answer") == 1
            and mine_ans["out"].get("n_answer") == 1
            and "DAPO" in str((mine_ans["out"].get("items") or [{}])[0].get("text")),
            f"node_id={ra['out'].get('node_id')}"
            f"｜我=是 {mine['out'].get('n_answer')} 项"
            f"｜type=回答 {mine_ans['out'].get('n_answer')} 项")

        # ⑧-c 推理闭环（2026-10-25）：失配 → 改结构 → 自洽。
        #   这是「结构主义那一支的认知模型」的核心机制（断言 §S6、判据 S39）。
        #   轨迹里也要走一遍 —— 不然它只活在 smoke 里。
        miss2 = c.tool("ask", {"corpus": "flow", "require": ["status=open"]})
        again2 = c.tool("ask", {"corpus": "flow", "require": ["status=open"]})
        dv2 = c.tool("derive", {"corpus": "flow", "name": "status",
                                "values": {"n1": "open", "n2": "open", "n3": "closed"},
                                "rationale": "按处理状态分",
                                "derived_from": "材料字段 status"})
        fixed2 = c.tool("ask", {"corpus": "flow", "require": ["status=open"]})
        nx2 = c.tool("next", {"corpus": "flow", "require": []})
        dis2 = nx2["out"].get("失配") or {}
        chk("F14", "推理闭环：失配 → 反向对照（仍失配）→ derive → 满足 → 结构自洽",
            miss2["out"].get("out_of_structure") is True
            and again2["out"].get("out_of_structure") is True
            and dv2["out"].get("ok") is True
            and fixed2["out"].get("ok") is True
            and (fixed2["out"].get("n_answer") or 0) > 0
            and dis2.get("结构坏了") == 0,
            f"失配={miss2['out'].get('out_of_structure')}"
            f"｜反向仍失配={again2['out'].get('out_of_structure')}"
            f"｜derive={dv2['out'].get('ok')}"
            f"｜满足={fixed2['out'].get('n_answer')} 项"
            f"｜结构坏了={dis2.get('结构坏了')}")

        # ⑨ 语料变 ⇒ 缓存明说过期（不是静默沿用）
        (nodes / "n1.md").write_text(
            (nodes / "n1.md").read_text(encoding="utf-8") + "\n补一句：实测复现。\n",
            encoding="utf-8", newline="\n")
        a6 = c.tool("ask", {"corpus": "flow", "require": ["type=立场"]})
        chk("F09", "语料一变 ⇒ 缓存**明说**过期，不静默沿用",
            not (a6["out"].get("cached_answer") or {}).get("from_cache")
            and bool(a6["out"].get("cache_note")),
            # ⚠️ 不打哈希值：它含 mtime，每跑一次都不同 ⇒ 留档会抖
            #   （同 token_cost 去掉 derive 的 path 那条纪律）。
            f"有 cache_note={bool(a6['out'].get('cache_note'))}"
            f"｜from_cache={(a6['out'].get('cached_answer') or {}).get('from_cache')}")

        # ⑩ 判错（人）⇒ 支级跳过 ⇒ 精确问回来
        q0 = c.tool("ask", {"corpus": "flow", "require": [], "structure": True})
        # ⚠️ 用**最后一支**（F05 钻的是第 2 条）—— 倾向是**累计**的：
        #   一支被下钻过 +1、再判错 -1 ⇒ 净 0 ⇒  不跳。
        #   这是**产品语义**（3 次有用 vs 1 次判错，多数说了算），不是 bug；
        #   但探针要测的是「闭环本身」，所以挑一支没被钻过的。
        last = (q0["out"].get("nodes") or [{}])[-1]
        did0 = last.get("did")
        must0 = list(last.get("must") or [])
        bad = c.tool("drill", {"corpus": "flow", "did": did0, "require": [],
                               "verdict": "wrong", "actor": "agent"})
        good = c.tool("drill", {"corpus": "flow", "did": did0, "require": [],
                                "verdict": "wrong", "actor": "human"})
        rej = c.tool("ask", {"corpus": "flow", "require": [], "structure": True,
                             "reject_below": 0.0})
        back = c.tool("ask", {"corpus": "flow", "require": must0,
                              "reject_below": 0.0})
        seen.append(f"判错后跳过={rej['out'].get('skipped_rejected')}"
                    f"｜少={rej['out'].get('rejected_items')}")
        chk("F10", "判错闭环：agent 拒 / 人记 -1 / 支不摆出来 / 精确问回来",
            bad["out"].get("ok") is False
            and good["out"].get("outcome") == -1.0
            and did0 in (rej["out"].get("skipped_rejected") or [])
            and (back["out"].get("skipped_rejected") or []) == [],
            f"agent 拒={bad['out'].get('ok') is False}"
            f"｜outcome={good['out'].get('outcome')}"
            f"｜跳过={rej['out'].get('skipped_rejected')}"
            f"｜精确问回={back['out'].get('skipped_rejected')}")

        # ⑪ next（接下来看什么）
        nx = c.tool("next", {"corpus": "flow", "require": []})
        # ⚠️ next 返回的是**档位分组**（必须 / 可延后 / 可忽略），不是 `suggest`。
        #    第一版按 suggest 断言 ⇒ 红得像是功能缺失，其实是我读错了形状。
        chk("F11", "next 给得出下一步（按档位分组：必须 / 可延后 / 可忽略）",
            nx["out"].get("ok") is not False
            and any(k in nx["out"] for k in ("必须", "可延后", "可忽略")),
            f"keys={sorted((nx['out'] or {}).keys())[:8]}")

        # ⑫ 收尾对账：orient 说清「记得什么、查过什么」
        o2 = c.tool("orient", {"corpus": "flow"})
        # ★ 4 项 = 3 条材料 + 1 条**自己的回答**（F08 的 remember_answer 现在写节点）
        #   —— 这个 4 本身就是「主体性」该有的样子：库里有我写的东西。
        chk("F12", "收尾 orient 与写入一致（4 项 = 3 材料 + 1 我自己的回答）",
            o2["out"].get("n_items") == 4
            and len(o2["out"].get("recent_calls") or []) > 0,
            f"n_items={o2['out'].get('n_items')}"
            f"｜recent={len(o2['out'].get('recent_calls') or [])} 条")
    finally:
        c.close()
    return seen


def edge_cases(store: Path) -> None:
    """26 条边缘：用户真会这么用（空 / 越界 / 编造 / 超长 / 跨查询 / 多语料）。"""
    print("\n── 边缘矩阵（26 条）──")
    nodes = store / "_data" / "edge" / "nodes"
    nodes.mkdir(parents=True)
    seed(nodes)
    c = MCP(store)
    try:
        def E(tag, what, good, detail=""):
            chk(f"E{tag}", what, good, detail)

        # 1 未知键（结构信号，不是「查不到」）
        r = c.tool("ask", {"corpus": "edge", "require": ["不存在的键=x"]})
        E("01", "未知键 ⇒ 报「超出结构」并给相近建议（不静默返 0）",
          r["out"].get("out_of_structure") is True
          and bool(r["out"].get("suggest") is not None),
          f"out_of_structure={r['out'].get('out_of_structure')}"
          f"｜suggest={list((r['out'].get('suggest') or {}).keys())[:2]}")

        # 2 已知键零命中
        # ⚠️ 要用**两个都存在的键**、只是不相容（同一字段两个值必然不交）。
        #    第一版用了 `source.kind=个人笔记` —— 那个**值**不在词汇表里，
        #    走的是「未知键」那条路（另一种情形，E01 守），不是零命中。
        r = c.tool("ask", {"corpus": "edge",
                           "require": ["type=立场", "type=判断点"]})
        E("02", "已知键零命中 ⇒ ok + 0 项 + 说明（不是错）",
          r["out"].get("ok") is True and r["out"].get("n_answer") == 0
          and bool(r["out"].get("items_note")),
          f"n={r['out'].get('n_answer')}｜note={str(r['out'].get('items_note'))[:30]}")

        # 3 limit=0 / 4 limit 超大
        r0 = c.tool("ask", {"corpus": "edge", "require": [], "limit": 0})
        r9 = c.tool("ask", {"corpus": "edge", "require": [], "limit": 99999})
        E("03", "limit=0 ⇒ 至少给 1 条（不崩、不空转）",
          len(r0["out"].get("items") or []) >= 1,
          f"items={len(r0['out'].get('items') or [])}")
        E("04", "limit=99999 ⇒ 全给（3 项）",
          len(r9["out"].get("items") or []) == 3,
          f"items={len(r9['out'].get('items') or [])}")

        # 5 max_chars=0 / 6 max_chars=1（截断必报）
        r5 = c.tool("ask", {"corpus": "edge", "require": [], "max_chars": 0})
        r6 = c.tool("ask", {"corpus": "edge", "require": [], "max_chars": 1})
        E("05", "max_chars=0 ⇒ 正文空但**不报** truncated（没截，是没要）",
          all(it.get("text") == "" and it.get("truncated") is False
              for it in (r5["out"].get("items") or [])),
          f"text 长度={[len(it.get('text') or '') for it in (r5['out'].get('items') or [])]}")
        E("06", "max_chars=1 ⇒ 只给 1 字且 truncated=True",
          all(len(it.get("text") or "") <= 1 and it.get("truncated") is True
              for it in (r6["out"].get("items") or [])),
          f"truncated={[it.get('truncated') for it in (r6['out'].get('items') or [])]}")

        # 7 句柄越界 / 8 跨查询用旧句柄
        c.tool("ask", {"corpus": "edge", "require": []})
        r7 = c.tool("drill", {"corpus": "edge", "n": 999, "require": []})
        E("07", "句柄越界 ⇒ 说清「顺序不记了」（不是报错崩）",
          r7["out"].get("ok") is False
          and "顺序" in json.dumps(r7["out"], ensure_ascii=False),
          json.dumps(r7["out"], ensure_ascii=False)[:70])
        c.tool("ask", {"corpus": "edge", "require": ["type=立场"]})
        r8 = c.tool("drill", {"corpus": "edge", "n": 1, "require": ["type=例外"]})
        E("08", "跨查询用旧句柄 ⇒ 拒（句柄属于另一次查询）",
          r8["out"].get("ok") is False,
          json.dumps(r8["out"], ensure_ascii=False)[:70])

        # 9 不存在的 did / 10 坏 verdict / 11 agent 判错
        r9b = c.tool("drill", {"corpus": "edge", "did": "D9999", "require": []})
        r10 = c.tool("drill", {"corpus": "edge", "did": "D1", "require": [],
                               "verdict": "bogus", "actor": "human"})
        r11 = c.tool("drill", {"corpus": "edge", "did": "D1", "require": [],
                               "verdict": "wrong", "actor": "agent"})
        E("09", "不存在的 did ⇒ ok=False（不崩）", r9b["out"].get("ok") is False,
          json.dumps(r9b["out"], ensure_ascii=False)[:60])
        E("10", "verdict=bogus ⇒ 拒并列出合法值",
          r10["out"].get("ok") is False
          and "good" in json.dumps(r10["out"], ensure_ascii=False),
          json.dumps(r10["out"], ensure_ascii=False)[:60])
        E("11", "verdict=wrong + agent ⇒ 拒（判错只能外部授予）",
          r11["out"].get("ok") is False, json.dumps(r11["out"], ensure_ascii=False)[:60])

        # 12 undo 不存在的 id / 13 undo 用越界句柄
        r12 = c.tool("undo", {"corpus": "edge", "id": "nope", "require": []})
        # ⚠️ 先 ask 一次把句柄表刷成 require=[]（E08 那次查的是 type=例外，
        #   不刷的话 undo 会报「句柄属于另一次查询」—— 那是**正确**行为，
        #   但不是这条要测的东西）。
        c.tool("ask", {"corpus": "edge", "require": []})
        r13 = c.tool("undo", {"corpus": "edge", "n": 999, "require": []})
        E("12", "undo 不存在的 id ⇒ 拒（不崩、不假装成功）",
          r12["out"].get("ok") is False or bool(r12.get("isError")),
          json.dumps(r12["out"], ensure_ascii=False)[:60])
        # ⚠️ 双形状（第三次踩到）：业务错 ok=False、异常错 {"_raw": …}
        E("13", "undo 句柄越界 ⇒ 拒",
          r13["out"].get("ok") is False or "_raw" in r13["out"],
          json.dumps(r13["out"], ensure_ascii=False)[:60])

        # 14 derive 空 values / 15 编造 id / 16 同 name 重建
        r14 = c.tool("derive", {"corpus": "edge", "name": "空的", "values": {},
                                "rationale": "x", "derived_from": "y"})
        r15 = c.tool("derive", {"corpus": "edge", "name": "编的",
                                "values": {"不存在-1": "x"},
                                "rationale": "x", "derived_from": "y"})
        c.tool("derive", {"corpus": "edge", "name": "proj",
                          "values": {"n1": "alpha", "n2": "alpha"},
                          "rationale": "x", "derived_from": "y"})
        r16 = c.tool("derive", {"corpus": "edge", "name": "proj",
                                "values": {"n1": "beta"},
                                "rationale": "x", "derived_from": "y"})
        E("14", "derive 空 values ⇒ 拒（不建一个空维度）",
          r14["out"].get("ok") is False or bool(r14.get("isError")),
          json.dumps(r14["out"], ensure_ascii=False)[:60])
        E("15", "derive 编造 id ⇒ 当场拒",
          "不在语料里" in json.dumps(r15["out"], ensure_ascii=False),
          json.dumps(r15["out"], ensure_ascii=False)[:60])
        # ⚠️ 双形状：SpecError 走 `{"_raw": …}`（没有 ok 键）
        E("16", "derive 同名重建（不给 force）⇒ 拒（不许悄悄换轴）",
          (r16["out"].get("ok") is False or "_raw" in r16["out"])
          and ("force" in json.dumps(r16["out"], ensure_ascii=False)
               or "已经" in json.dumps(r16["out"], ensure_ascii=False)),
          # ⚠️ 时间戳去掉 —— 每次跑都不一样（留档要可复现）
          f"拒={r16['out'].get('ok') is False or '_raw' in r16['out']}"
          f"｜提到 force={'force' in json.dumps(r16['out'], ensure_ascii=False)}")

        # 17 remember 同 id 两次 / 18 空正文 / 19 超长正文
        w1 = c.tool("remember", {"corpus": "edge", "id": "n1",
                                 "body": "改过的正文", "fields": {"type": "立场"}})
        w2 = c.tool("remember", {"corpus": "edge", "id": "empty1",
                                 "body": "", "fields": {"type": "空白"}})
        w3 = c.tool("remember", {"corpus": "edge", "id": "long1",
                                 "body": "长" * 5000, "fields": {"type": "长文"}})
        E("17", "同 id 再 remember ⇒ 不覆写（报已存在）",
          w1["out"].get("ok") is False or "存在" in json.dumps(w1["out"], ensure_ascii=False),
          json.dumps(w1["out"], ensure_ascii=False)[:60])
        E("18", "空正文合法（空值合法是产品立场），能写能查",
          w2["out"].get("ok") is True,
          json.dumps(w2["out"], ensure_ascii=False)[:60])
        E("19", "5000 字正文能写；查时 max_chars=100 截断并报 truncated",
          w3["out"].get("ok") is True
          and all(it.get("truncated") is True
                  for it in (c.tool("ask", {"corpus": "edge",
                                            "require": ["type=长文"],
                                            "max_chars": 100})["out"].get("items") or [])),
          f"写入={w3['out'].get('ok')}")

        # 20 中文与全角 / 21 forbid 生效 / 22 max_branches
        r20 = c.tool("ask", {"corpus": "edge", "require": ["type=立场"]})
        r21 = c.tool("ask", {"corpus": "edge", "require": [],
                             "forbid": ["type=例外"]})
        r22 = c.tool("ask", {"corpus": "edge", "require": [], "structure": True,
                             "max_branches": 1})
        E("20", "中文/全角正文原样返回（不转义、不截断丢字）",
          any("DAPO" in (it.get("text") or "")
              for it in (r20["out"].get("items") or [])),
          f"首条={str((r20['out'].get('items') or [{}])[0].get('text'))[:24]}")
        E("21", "forbid 生效：排除 type=例外 ⇒ 答案里没有 n3",
          r21["out"].get("n_answer") == 4,      # 共 5 条（n1/n2/n3/empty1/long1），去 n3
          f"n_answer={r21['out'].get('n_answer')}")
        E("22", "max_branches=1 ⇒ 1 支 + branches_hidden>0",
          len(r22["out"].get("nodes") or []) == 1
          and (r22["out"].get("branches_hidden") or 0) > 0,
          f"支={len(r22['out'].get('nodes') or [])}"
          f"｜hidden={r22['out'].get('branches_hidden')}")

        # 23 多语料隔离 / 24 不存在的 corpus / 25 reject_below 无负事件 / 26 空 require 的元查询
        r23 = c.tool("ask", {"corpus": "flow", "require": []})
        r24 = c.tool("ask", {"corpus": "没有这个", "require": []})
        r25 = c.tool("ask", {"corpus": "edge", "require": [], "reject_below": 0.0})
        r26 = c.tool("ask", {"corpus": "edge", "require": []})
        E("23", "两个语料互不可见（edge 的 n1 不在 flow 里）",
          r23["out"].get("n_answer") == 4,       # flow = 3 条材料 + 1 条自己的回答
          f"flow n_answer={r23['out'].get('n_answer')}")
        E("24", "不存在的语料 ⇒ 拒并说清（不建一个空库）",
          r24["out"].get("ok") is False or bool(r24.get("isError")),
          json.dumps(r24["out"], ensure_ascii=False)[:60])
        E("25", "reject_below 传了但**没有负事件** ⇒ 一条都不跳",
          (r25["out"].get("skipped_rejected") or []) == []
          and r25["out"].get("rejected_items") == 0,
          f"skipped={r25['out'].get('skipped_rejected')}")
        E("26", "元查询（空 require）给全部，且 n_branches == 支数",
          r26["out"].get("n_answer") == 5
          and r26["out"].get("branches_shown") == r26["out"].get("branches_total"),
          f"n_answer={r26['out'].get('n_answer')}"
          f"｜shown={r26['out'].get('branches_shown')}"
          f"／total={r26['out'].get('branches_total')}")
    finally:
        c.close()


def main() -> int:
    runs: list[list[str]] = []
    for i in range(3):
        tmp = Path(tempfile.mkdtemp(prefix=f"ldv-real-{i}-"))
        store = tmp / "store"
        try:
            print(f"\n═══ 第 {i + 1} 轮 ═══")
            runs.append(run_flow(store))
            if i == 0:
                edge_cases(store)          # 边缘只跑一轮（它要一个干净语料）
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n── 三轮确定性（同一轨迹、独立 store，读数必须逐字相同）──")
    same = runs[0] == runs[1] == runs[2]
    chk("D01", "三轮读数逐字相同 ⇒ 轨迹是确定性的（不是运气）", same,
        "｜".join(runs[0]) + ("" if same else f"｜第2轮={runs[1]}｜第3轮={runs[2]}"))

    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（F01–F14 / E01–E26 / D01）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
