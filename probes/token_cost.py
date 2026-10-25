"""回答问题的 token 成本：插件取回 vs 窗口常驻。

跑法（需要 tiktoken）：python probes/token_cost.py

    C:/Users/19253/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe probes/token_cost.py

## 成本的形状（这是这个探针要说清的一件事）

    WITHOUT  窗口常驻：**每问都要把整个窗口放进上下文** ⇒ 成本 = 窗口 × 问题数
    WITH     按需取回：**每问只把取回的那部分放进上下文** ⇒ 成本 = 答案集 × 问题数

⇒ 所以「插件省不省 token」**没有统一答案**，取决于两件事：
      ① 答案集有多大（取回多少）
      ② 窗口有多大（要常驻多少）
   在语料 ≈ 窗口容量的规模上，插件**更贵**（取回的比整个窗口还大）。
   只有当**答案集 ≪ 窗口**时插件才省。

## 两档规模，同一批问题

    小  14 条笔记（窗口 12 条 ≈ 装得下全部）
    大  140 条笔记（窗口 12 条 ≈ 只装得下 8.6%）

## 计数

    tiktoken `cl100k_base`（真 tokenizer，不是估算）。
    ⚠️ 它**不是** DeepSeek 的 tokenizer —— 绝对值会有偏差，但两边同口径，
       比的是**比例**，不是绝对数。
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # = ldv-mcp/
PY = sys.executable
WINDOW_NOTES = 12

try:
    import tiktoken

    ENC = tiktoken.get_encoding("cl100k_base")

    def tok(s: str) -> int:
        return len(ENC.encode(s))
except ImportError:                     # 没装就退化成估算，并**明说**
    def tok(s: str) -> int:
        cjk = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
        return int(cjk + (len(s) - cjk) / 4)


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
                                 "clientInfo": {"name": "tok", "version": "0"}})

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        # ⚠️ 2026-10-25（外部评审 B1）：**不再自动注入 structure** ——
        #   本探针要量的正是「三种给法各花多少」，注入会让三列退化成同一列。
        #   要结构（answer_ids 等）的调用方自己显式传。
        r = self.call("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        return "".join(i.get("text", "") for i in (res.get("content") or []))

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


PROJS = ["alpha", "beta", "gamma"]
KINDS = ["实验", "决定", "坑", "笔记"]
TEMPLATES = [
    "lr={v1}，第 {v2} 小时 loss 尖峰 {v3}，之后回落到 {v4}",
    "组 {v1} baseline {v2}，改动后 {v3}（+{v4}%）",
    "复跑 {v1} 次，{v2} 次复现；差异在 batch={v3} 那一档",
    "把 clip 上界从 {v1} 提到 {v2}，梯度范数从 {v3} 涨到 {v4}",
    "把温度从 {v1} 降到 {v2}，熵从 {v3} 掉到 {v4}",
]


def make_notes(n: int, seed: int = 7) -> list[tuple[str, str, str, str]]:
    """生成 n 条笔记：(id, project, type, body)。正文里的数值都是随机的。"""
    r = random.Random(seed)
    out = []
    for i in range(1, n + 1):
        proj = PROJS[i % len(PROJS)]
        kind = KINDS[i % len(KINDS)]
        tpl = TEMPLATES[i % len(TEMPLATES)]
        vals = [f"{r.uniform(0.1, 9.9):.2f}" if k % 2 else str(r.randint(2, 99))
                for k in range(4)]
        out.append((f"n{i:04d}", proj, kind, tpl.format(v1=vals[0], v2=vals[1],
                                                       v3=vals[2], v4=vals[3])))
    return out


def write_corpus(nodes: Path, notes) -> None:
    """直接落盘（这一趟测的是**查询**成本，写入成本另说）。"""
    for nid, proj, kind, body in notes:
        (nodes / f"{nid}.md").write_text(
            f'---\nid: "{nid}"\ntype: {kind}\nproject: {proj}\n---\n\n{body}\n',
            encoding="utf-8")


def run_scale(c: MCP, corpus: str, notes, label: str) -> list[tuple[str, int, int, int]]:
    """返回 [(问题, WITH tokens, WITHOUT tokens, 答案项数)]。"""
    # ★ 先 derive 一个 project 维度 —— 自定义字段只进「字段名」键，
    #   不 derive 的话 project=alpha 会**报错**（那不是省，是没答）。
    rd = c.tool("ask", {"corpus": corpus, "require": [], "with_body": True,
                        "structure": True, "limit": len(notes) + 10})
    try:
        doc0 = json.loads(rd)
    except ValueError:
        doc0 = {}
    # ⚠️ bodies 已并入 items（2026-10-24 ask 改形状）；id 走 answer_ids，
    #   与 items 按位置 zip —— 少了这一步，values 是空的 ⇒ derive 没建成
    #   ⇒ project=alpha 变成未知键 ⇒ 后面每一行量的都是**错误回包**。
    vals = {nid: (it.get("fields") or {}).get("project")
            for nid, it in zip(doc0.get("answer_ids") or [],
                               doc0.get("items") or [])}
    vals = {k: v for k, v in vals.items() if v}
    dv = c.tool("derive", {"corpus": corpus, "name": "project", "values": vals,
                           "rationale": "按项目看", "derived_from": "材料字段 project"})
    # ⚠️ 读数里**去掉机器相关字段**：derive 的返回带 `path`（临时目录的绝对路径），
    #   每次跑长度都不同 ⇒ 同一个读数会抖 ±2 tokens。量成本量的是 agent 要读的内容，
    #   不是本机路径。去掉它，读数才复现（两次跑逐字相同）。
    #   ⚠️ `dv` 是**原始 JSON 文本**（本探针的 tool() 不解析）—— 先 json.loads。
    try:
        dv_doc = json.loads(dv)
    except ValueError:
        dv_doc = {}
    dv_clean = {k: v for k, v in dv_doc.items() if k not in ("path", "corpus")}
    one_time = tok(rd) + tok(json.dumps(dv_clean, ensure_ascii=False))
    # 窗口：最近 WINDOW_NOTES 条（+ 一句摘要代表更早的）
    window = [n[3] for n in notes[-WINDOW_NOTES:]]
    earlier = len(notes) - len(window)
    window_text = ("【摘要】早前 %d 条笔记，记录了实验与决定若干，具体数值已略。\n"
                   % earlier) + "\n".join(window) if earlier else "\n".join(window)
    wo_base = tok(window_text)

    QS = [
        # ⚠️ 这两个键必须**可能同时成立**：合成语料里 project 由 i%3 定、
        #   type 也由 i%3 定 ⇒ project=alpha(i%3==0) 与 type=坑(i%3==1)
        #   永远不相交 ⇒ 那一行量的其实是**空响应**（曾印成「插件省 0.42×」）。
        #   换成 type=实验（i%3==0）才与 alpha 相容。
        ("窄：alpha 的实验", ["project=alpha", "type=实验"], 5),
        ("中：beta 全部", ["project=beta"], 10),
        ("宽：所有实验", ["type=实验"], 20),
        ("元：整个库", [], 40),
    ]
    rows = []
    covered: set[str] = set()          # 去重口径：四问一共覆盖哪些节点
    for q, req, limit in QS:
        # ★ 三种给法各问一遍（外部评审 B1：默认瘦身路径一直没有读数）——
        #   默认（正文 + 句柄）｜无正文（with_body=false）｜含结构（structure=true）
        #   ⚠️ 不设「with_body=true」列：它**就是默认**（正文是主产物，默认给）。
        #      旧探针传 with_body=true 又靠 tool() 注入 structure ⇒ 量的一直是
        #      「最全」那一列，瘦身省了多少从没被测到。
        d_def = c.tool("ask", {"corpus": corpus, "require": req})
        d_nb = c.tool("ask", {"corpus": corpus, "require": req,
                              "with_body": False})
        d_str = c.tool("ask", {"corpus": corpus, "require": req,
                               "structure": True})
        try:
            doc = json.loads(d_str)
        except ValueError:
            doc = {"_raw": d_str}
        n_ans = doc.get("n_answer") or 0
        ids = list(doc.get("answer_ids") or [])
        covered |= set(ids)
        t_def = tok(d_def) + tok(q)
        t_nb = tok(d_nb) + tok(q)
        t_str = tok(d_str) + tok(q)
        wo_tok = wo_base + tok(q)
        rows.append((f"{q}", t_def, t_nb, t_str, wo_tok, n_ans))
    print(f"\n=== {label}（{len(notes)} 条笔记，窗口 {WINDOW_NOTES} 条 = "
          f"{len(window)} 条在窗内）===")
    print(f"   窗口常驻 {wo_base} tokens（每问都要付一遍）")
    print(f"   一次性成本（读全库 + derive project）：{one_time} tokens")
    print(f"   {'问题':<18}{'默认':>7}{'无正文':>7}{'含结构':>7}"
          f"{'WITHOUT':>9}{'答案项':>7}   结论")
    for q, d, nb, st, wo, n in rows:
        if n == 0:
            verdict = "**没答到**（0 项）"
        else:
            verdict = ("插件省" if d < wo else "插件贵") + f"（{d / max(1, wo):.2f}×）"
        print(f"   {q:<18}{d:>7}{nb:>7}{st:>7}{wo:>9}{n:>7}   {verdict}")
    return rows, len(covered)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ldv-tok-"))
    store = tmp / "store"
    c = MCP(store)
    try:
        for n, label in ((14, "小规模"), (140, "大规模")):
            notes = make_notes(n)
            corpus = f"c{n}"
            nodes = store / "_data" / corpus / "nodes"
            nodes.mkdir(parents=True, exist_ok=True)
            write_corpus(nodes, notes)
            rows, n_dedup = run_scale(c, corpus, notes, label)
            # 累计：N 个问题下的总成本（三种给法各一列）
            tot_def = sum(r[1] for r in rows)
            tot_nb = sum(r[2] for r in rows)
            tot_str = sum(r[3] for r in rows)
            tot_wo = sum(r[4] for r in rows)
            n_items = sum(r[5] for r in rows)          # 原口径：四问项数**累加**
            window_items = min(WINDOW_NOTES, len(notes))
            print(f"   ⇒ {len(rows)} 问累计：默认 {tot_def}｜无正文 {tot_nb}"
                  f"｜含结构 {tot_str}｜WITHOUT {tot_wo}")
            print(f"     含结构比默认贵 {tot_str / max(1, tot_def):.2f}×"
                  f"｜默认比 WITHOUT 贵 {tot_def / max(1, tot_wo):.2f}×")
            # ★ 每条答案（外部评审 A3：原口径分母在**重复计数**）——
            #   四问的答案互为子集，累加项数会超过语料条数（14 条的库印出 23 项）。
            #   两个口径都印：累加口径是历史读数（可比过去），去重口径是**正确**的。
            print(f"   ⇒ 每条答案（累加口径，历史写法）：默认"
                  f" {tot_def / max(1, n_items):.0f}（{n_items} 项）"
                  f" vs WITHOUT {tot_wo / max(1, window_items):.0f}"
                  f"（只能看见 {window_items} 条）")
            assert n_dedup <= len(notes), f"去重覆盖 {n_dedup} > 语料 {len(notes)}"
            print(f"   ⇒ 每条答案（**去重口径**）：默认"
                  f" {tot_def / max(1, n_dedup):.0f}（{n_dedup} 项，语料共 "
                  f"{len(notes)} 条） vs WITHOUT {tot_wo / max(1, window_items):.0f}"
                  f"（只能看见 {window_items} 条）")

        # ── 写入成本：一条笔记经插件写 vs 直接说在对话里 ─────────────
        one = "把温度从 0.95 降到 0.60，熵从 1.42 掉到 0.61"
        args = json.dumps({"corpus": "c14", "id": "w1",
                           "fields": {"type": "实验", "project": "alpha"},
                           "body": one}, ensure_ascii=False)
        print(f"\n=== 写入成本（一条笔记）===")
        print(f"   经插件（工具调用参数）：{tok(args)} tokens")
        print(f"   直接说在对话里：      {tok(one)} tokens")
        print(f"   ⇒ 写入几乎等价（插件多的是字段和 id，不是正文）")

        print(f"\n=== 结论 ===")
        print("   ① 每问比：默认路径仍比 WITHOUT 贵（1.8×～5×）—— WITHOUT 只带窗口")
        print("      12 条、**答不全**，拿它当「便宜」是拿答不了当便宜。")
        print("   ② 每条答案比（**去重口径**，外部评审 A3 修正后的分母）：")
        print("      小规模贵（库比窗口还小，取回的诚意不如窗内常驻）｜")
        print("      大规模省（库远大于窗口，答案集仍远小于全库）。")
        print("   ③ 瘦身的账（外部评审 B1 要的读数）：默认 vs 含结构 =")
        print("      小规模省 47%、大规模省 32% —— 默认不给结构，这一半是实打实的。")
        print("   ④ 无正文（with_body=false）只再省 8%～15%：正文才是主产物。")
    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
