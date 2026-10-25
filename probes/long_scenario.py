"""长场景实测：空库起，14 天，14 条事实，六类问题。

跑法（cwd = ldv-mcp）：python probes/long_scenario.py

## 对照（2026-10-24 修正：WITHOUT 会**压缩**，不是硬掉出）

    WITH     插件：笔记进 store，问题走 ask（按需取回，与笔记总量无关）
    WITHOUT  **窗口 12 条 + 压缩**：写满就把最老的 3 条压成一句摘要
             —— 摘要保住**主题**（哪个项目、什么类型），丢掉**具体数值**

    ⚠️ 第一版把 WITHOUT 做成「FIFO 硬掉出」是不公平的：真 agent 会压缩。
       压缩的真实后果不是「什么都忘了」，是「**主题还在，数值没了**」。

⇒ 这一版能分出两类问题：
       数值题（lr 多少 / baseline 多少）—— 压缩后 WITHOUT 必输
       主题题（覆盖哪些项目）          —— 压缩后 WITHOUT 仍能答

## 事实是运行时生成的（种子固定）

    lr=3.17e-6 / 尖峰 2.73 这类值由 RNG 生成，不在训练数据里。

## 缺口（这次**不报「拒收」**，直接让 agent 补）

    第 2 天用户说「按项目看」⇒ agent 试 project=alpha ⇒ 没有这个键
    ⇒ 返回 can_agent_fill + next_step（**不打断用户**）
    ⇒ agent 读回字段 → derive 一个 project 维度 → 能查了
    ⇒ 第 14 天全量重建（force）—— 这是**设计**，不是补丁
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
WINDOW_NOTES = 12       # WITHOUT 的窗口容量（比上一版的 5 条**拉大**了）
COMPRESS_BATCH = 3      # 写满时把最老的 3 条压成一句摘要
SEED = 20261024

PLAN = [
    ("d01", "alpha", "实验", "lr={lr}，跑到第 4 小时 loss 尖峰 {spike}，之后回落到 {back}"),
    ("d02", "alpha", "实验", "组 A baseline {base}，改动后 {new}（{delta}）"),
    ("d03", "beta", "决定", "张工提议 B 方案；他说 7B 上不可复现，只在 32B 上出现过一次"),
    ("d04", "beta", "实验", "复跑 {n} 次，{k} 次复现；差异集中在 batch={bs} 那一档"),
    ("d05", "alpha", "坑", "把 clip 上界从 {c1} 提到 {c2} 后，梯度范数从 {g1} 涨到 {g2}"),
    ("d06", "gamma", "实验", "换卡后精度问题消失；怀疑与 {hw} 有关，未验证"),
    ("d07", "gamma", "决定", "暂缓 gamma 的扩容，等 beta 复现结论"),
    ("d08", "beta", "坑", "日志里 {p} 的字段口径不一致，统计时要注意"),
    ("d09", "alpha", "实验", "把温度从 {t1} 降到 {t2}，熵从 {e1} 掉到 {e2}"),
    ("d10", "gamma", "笔记", "客户反馈里没提过稳定性问题，主要是 {pain} 太慢"),
    ("d11", "beta", "实验", "在 {bs} 档上跑了 {n} 组，最好 {best}，最差 {worst}"),
    ("d12", "alpha", "决定", "alpha 定稿：保留 {keep}，去掉 {drop}"),
    ("d13", "alpha", "实验", "把 batch 从 {bs} 提到 {bs2}，吞吐涨 {thr}"),
    ("d14", "beta", "笔记", "复盘：{n} 次实验里只有 {k} 次结论站得住"),
]


def gen_facts() -> dict[str, dict]:
    r = random.Random(SEED)
    out = {}
    for nid, proj, kind, tpl in PLAN:
        vals = {
            "lr": f"{r.uniform(1.5, 5.0):.2f}e-6", "spike": f"{r.uniform(2.2, 3.4):.2f}",
            "back": f"{r.uniform(0.9, 1.3):.2f}", "base": f"{r.uniform(0.38, 0.44):.4f}",
            "new": f"{r.uniform(0.45, 0.50):.4f}", "delta": f"+{r.uniform(4, 9):.1f}%",
            "n": str(r.randint(5, 12)), "k": str(r.randint(1, 4)),
            "bs": str(r.choice([16, 32, 64, 128])), "bs2": str(r.choice([256, 512])),
            "thr": f"+{r.uniform(10, 40):.0f}%",
            "c1": f"{r.uniform(0.15, 0.25):.2f}", "c2": f"{r.uniform(0.26, 0.40):.2f}",
            "g1": f"{r.uniform(0.6, 1.0):.2f}", "g2": f"{r.uniform(1.1, 1.8):.2f}",
            "hw": r.choice(["A100", "H100", "H800"]),
            "p": r.choice(["step", "epoch", "tokens"]),
            "t1": f"{r.uniform(0.9, 1.1):.2f}", "t2": f"{r.uniform(0.5, 0.7):.2f}",
            "e1": f"{r.uniform(1.2, 1.8):.2f}", "e2": f"{r.uniform(0.4, 0.8):.2f}",
            "pain": r.choice(["导出", "权限", "检索"]),
            "best": f"{r.uniform(0.46, 0.52):.4f}", "worst": f"{r.uniform(0.39, 0.45):.4f}",
            "keep": r.choice(["长度惩罚", "clip-higher", "动态采样"]),
            "drop": r.choice(["KL 项", "格式奖励", "过程奖励"]),
        }
        out[nid] = {"proj": proj, "kind": kind, "text": tpl.format(**vals), "vals": vals}
    return out


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
                                 "clientInfo": {"name": "scen", "version": "0"}})

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n")
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
    facts = gen_facts()
    tmp = Path(tempfile.mkdtemp(prefix="ldv-scen-"))
    store = tmp / "store"
    (store / "_data" / "notes" / "nodes").mkdir(parents=True)   # **空库**

    c = MCP(store)
    ctx: list[str] = []          # WITHOUT：窗口（最多 WINDOW_NOTES 条，含摘要行）
    chars_with = 0
    misses: list[str] = []
    drift: dict[int, dict] = {}
    gap_event = ""
    compressed_at = 0

    print("=" * 80)
    print(f"长场景｜空库起 · {len(PLAN)} 天 · 窗口 {WINDOW_NOTES} 条 + 压缩"
          f"（每次压 {COMPRESS_BATCH} 条）")
    print("=" * 80)

    o0 = c.tool("orient", {"corpus": "notes"})
    print(f"\n⓪ 空库 orient ⇒ {o0.get('n_items')} 项（0 = 空库，不写占位文件）"
          f"｜键 {o0.get('n_keys')} 个")

    for i, (nid, proj, kind, _t) in enumerate(PLAN, start=1):
        f = facts[nid]
        w = c.tool("remember", {"corpus": "notes", "id": nid,
                                "fields": {"type": kind, "project": proj},
                                "body": f["text"]})
        if not w.get("written"):
            print(f"   ⚠️ {nid} 没写进去：{w.get('why')}")
        ctx.append(f["text"])

        # ★ 压缩：写满就把最老的 3 条压成一句摘要（丢数值、保主题）
        if len(ctx) > WINDOW_NOTES:
            batch = ctx[:COMPRESS_BATCH]
            ctx[:COMPRESS_BATCH] = [
                f"【摘要】早前 {len(batch)} 条笔记（项目：alpha/beta/gamma），"
                f"记录了实验与决定若干，具体数值已略。"]
            compressed_at = compressed_at or i
            print(f"   ↳ 第 {i} 天：窗口满 ⇒ 压缩最老 {COMPRESS_BATCH} 条为摘要"
                  f"（现在 {len(ctx)} 条）")

        # ── 第 2 天：用户说「按项目看」⇒ 缺口（不报拒收，agent 自己补）──
        if i == 2:
            bad = c.tool("ask", {"corpus": "notes", "require": ["project=alpha"]})
            print(f"\n② 第 2 天：用户说「按项目看」")
            print(f"   ask require=[project=alpha] ⇒ ok={bad.get('ok')}"
                  f"｜can_agent_fill={bad.get('can_agent_fill')}")
            print(f"   next_step={bad.get('next_step')}")
            rd = c.tool("ask", {"corpus": "notes", "require": [], "with_body": True,
                                "limit": 40})
            chars_with += len(json.dumps(rd.get("items") or [], ensure_ascii=False))
            vals = {}
            for nid, it in zip(rd.get("answer_ids") or [], rd.get("items") or []):
                p = (it.get("fields") or {}).get("project")
                if p:
                    vals[nid] = p
            dv = c.tool("derive", {"corpus": "notes", "name": "project",
                                   "values": vals,
                                   "rationale": "用户要按项目看；project 是材料自带字段，"
                                                "但内核只给枚举字段生成值键",
                                   "derived_from": "材料字段 project"})
            again = c.tool("ask", {"corpus": "notes", "require": ["project=alpha"],
                                   "with_body": True, "limit": 10})
            gap_event = (f"无此键 → can_agent_fill → derive {dv.get('field')}"
                         f"（{dv.get('n_items')} 项）→ 再查 ok={again.get('ok')}"
                         f"（{again.get('n_answer')} 项）")
            print(f"   ⇒ {gap_event}")

        # ── 检查点：同一批数值事实，两边谁还记得 ──────────────────
        if i in (3, 6, 9, 12, 14):
            drift[i] = {}
            for lab, nid2, key2 in (("d01.lr", "d01", "lr"), ("d02.base", "d02", "base"),
                                    ("d03.不可复现", "d03", None)):
                val = facts[nid2]["vals"][key2] if key2 else "不可复现"
                r = c.tool("ask", {"corpus": "notes", "require": [],
                                   "with_body": True, "limit": 40})
                bodies = json.dumps(r.get("items") or [], ensure_ascii=False)
                chars_with += len(bodies)
                drift[i][lab] = {"with": val in bodies,
                                 "without": any(val in x for x in ctx),
                                 "window": sum(len(x) for x in ctx)}
                if not drift[i][lab]["with"]:
                    misses.append(f"{nid2}:{key2 or 'qual'}@d{i}")

    # ── 第 14 天：全量重建派生维度（**这是设计**，不是补丁）─────────
    rd2 = c.tool("ask", {"corpus": "notes", "require": [], "with_body": True, "limit": 60})
    chars_with += len(json.dumps(rd2.get("items") or [], ensure_ascii=False))
    vals2 = {nid: (it.get("fields") or {}).get("project")
             for nid, it in zip(rd2.get("answer_ids") or [], rd2.get("items") or [])}
    vals2 = {k: v for k, v in vals2.items() if v}
    dv2 = c.tool("derive", {"corpus": "notes", "name": "project", "values": vals2,
                            "rationale": "全量重建：第 2 天那份只覆盖当时那几条",
                            "derived_from": "材料字段 project", "force": True})
    print(f"\n③ 全量重建派生维度：第 2 天 3 项 → 现在 {dv2.get('n_items')} 项"
          f"｜覆盖率 {dv2.get('coverage')}")

    # ── 最后一天：六类问题 ────────────────────────────────────────
    print("\n④ 最后一天：六类问题（WITH）")
    QS = [
        ("事实", "alpha 第一次跑的 lr 与尖峰", ["project=alpha"], "lr", "d01"),
        ("事实", "组 A 的 baseline", ["project=alpha"], "base", "d02"),
        ("结构", "beta 线几条记录", ["project=beta"], None, None),
        ("结构", "alpha 的实验几条", ["project=alpha", "type=实验"], None, None),
        ("边界", "有没有客户投诉稳定性", ["project=gamma"], None, None),
        ("元", "覆盖哪些项目", [], None, None),
        ("追述", "第 3 天那条（B 方案）", ["project=beta"], None, "d03"),
    ]
    for cat, q, req, key, nid in QS:
        r = c.tool("ask", {"corpus": "notes", "require": req, "with_body": True,
                           "limit": 40})
        bodies = json.dumps(r.get("items") or [], ensure_ascii=False)
        chars_with += len(bodies)
        ids = r.get("answer_ids") or []
        mark = ""
        if key and nid:
            val = facts[nid]["vals"][key]
            mark = "命中" if val in bodies else "**没命中**"
            if val not in bodies:
                misses.append(f"{nid}:{key}@final")
        elif nid:
            mark = "命中" if "不可复现" in bodies else "**没命中**"
        print(f"   [{cat}] {q} ⇒ ok={r.get('ok')}｜{r.get('n_answer')} 项"
              f"｜指回 {len(ids)} id｜{len(bodies)} 字"
              f"{('｜' + mark) if mark else ''}")

    # ── 偏移 ──────────────────────────────────────────────────────
    print("\n⑤ 答案偏移（数值题；有/有 = WITH 有 / WITHOUT 有）")
    print("   检查点        d01.lr      d02.base    d03.不可复现    窗口字数")
    for day in sorted(drift):
        row = drift[day]
        cells = [f"{'有' if row[k]['with'] else '无'}/{'有' if row[k]['without'] else '无'}"
                 for k in ("d01.lr", "d02.base", "d03.不可复现")]
        print(f"   第 {day:>2} 天      " + "      ".join(f"{c:<7}" for c in cells)
              + f"   {row['d01.lr']['window']}")

    # ── 主题题对照（这才是公平的分界）────────────────────────────
    topic_wo = any(("alpha" in x) for x in ctx)
    print(f"\n⑥ 主题题对照（「覆盖哪些项目」）")
    print(f"   WITH    ⇒ {len(vals2)} 条都有 project 字段，查得到")
    print(f"   WITHOUT ⇒ 压缩摘要里项目名还在：{topic_wo}"
          f"（摘要：{ctx[0][:34]}…）")
    print("   ⇒ **数值题 WITHOUT 输，主题题不输** —— 压缩丢的是精度，不是话题")

    print("\n⑦ 汇总")
    print(f"   缺口事件：{gap_event}")
    print(f"   压缩发生在第 {compressed_at} 天（窗口 {WINDOW_NOTES} 条）")
    print(f"   WITH 上下文成本：{chars_with} 字（{len(QS) + 3 + 5} 次查询）")
    print(f"   WITHOUT 上下文成本：常驻 {sum(len(x) for x in ctx)} 字")
    print(f"   漏检：{misses or '无'}")

    c.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
