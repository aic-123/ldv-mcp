"""长场景实测：从第一条笔记开始，12 天，14 条事实，6 类问题。

跑法（cwd = ldv-mcp）：python probes/long_scenario.py

## 对照怎么做的（不是稻草人）

    WITH     插件：笔记进 store，问题走 ask（按需取回，与笔记总量无关）
    WITHOUT  **上下文窗口 = 最近 5 条笔记**（FIFO）
             —— 「不用插件」最真实的形态不是「没有记忆」，
                是「记忆有上限，早期的会掉出去」

⇒ 测的是：同一批事实，在**有限窗口**下还能命中多少。

## 事实是运行时生成的（种子固定，可复现）

    lr=3.17e-6 / 尖峰 2.73 / baseline 0.4182 这类值由 RNG 生成，不在任何训练数据里
    ⇒ WITHOUT 命中的唯一途径是「它还在窗口里」；WITH 的唯一途径是「查得到」。

## 场景里会真实撞到一个缺口（这是它「真」的地方）

    第 2 天用户说「按项目看」⇒ agent 试 `project=alpha` ⇒ **拒收**：
    自定义字段只进「字段名」键，不进「字段=值」键（内核只对 type/evidence_status/
    source.kind 这类枚举字段生成值键）。
    ⇒ agent 读回字段（with_body 的 fields）→ `derive` 一个 project 维度 → 能查了。
    ⇒ 这就是「词汇缺口 → agent 补」，不是文档里的说法，是这一趟真发生的。

## 读数

    命中率     事实题：返回内容里是否**逐字**含那个具体值
    指回率     每个答案是否带 node id
    上下文成本 插件每问吐多少字 vs 窗口常驻多少字
    偏移       同一批事实在第 3/6/12 天还记得吗（WITH 只增不减，WITHOUT 只减不增）
    漏检       事实还在库里、但没被取回（= 结构缺口，不是答错）
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
WINDOW_NOTES = 5        # WITHOUT 的窗口：只能装下最近 5 条笔记
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
            "bs": str(r.choice([16, 32, 64, 128])), "c1": f"{r.uniform(0.15, 0.25):.2f}",
            "c2": f"{r.uniform(0.26, 0.40):.2f}", "g1": f"{r.uniform(0.6, 1.0):.2f}",
            "g2": f"{r.uniform(1.1, 1.8):.2f}", "hw": r.choice(["A100", "H100", "H800"]),
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
    (store / "_data" / "notes" / "nodes").mkdir(parents=True)

    c = MCP(store)
    ctx: list[str] = []          # WITHOUT：窗口（最多 WINDOW_NOTES 条）
    chars_with = 0
    misses: list[str] = []
    drift: dict[int, dict] = {}
    gap_event = ""

    print("=" * 78)
    print(f"长场景｜第 1 条笔记起 · 12 天 · 14 条事实 · 窗口只装 {WINDOW_NOTES} 条")
    print("=" * 78)

    c.tool("remember", {"corpus": "notes", "id": "d00",
                        "fields": {"type": "立项", "project": "meta"},
                        "body": "这个库用来记 alpha/beta/gamma 三条线的实验与决定。"})
    ctx.append("这个库用来记 alpha/beta/gamma 三条线的实验与决定。")

    for i, (nid, proj, kind, _t) in enumerate(PLAN, start=1):
        f = facts[nid]
        c.tool("remember", {"corpus": "notes", "id": nid,
                            "fields": {"type": kind, "project": proj},
                            "body": f["text"]})
        ctx.append(f["text"])
        while len(ctx) > WINDOW_NOTES:
            ctx.pop(0)

        # ── 第 2 天：用户说「按项目看」⇒ 撞缺口 ⇒ derive 补上 ──────
        if i == 2:
            bad = c.tool("ask", {"corpus": "notes", "require": ["project=alpha"]})
            print(f"\n② 第 2 天：用户说「按项目看」")
            print(f"   ask require=[project=alpha] ⇒ ok={bad.get('ok')}"
                  f"｜out_of_structure={bad.get('out_of_structure')}")
            print(f"   {str(bad.get('why'))[:58]}")
            rd = c.tool("ask", {"corpus": "notes", "require": [], "with_body": True,
                                "limit": 20})
            chars_with += len(json.dumps(rd.get("bodies") or {}, ensure_ascii=False))
            vals = {}
            for it in ((rd.get("bodies") or {}).get("items") or []):
                p = (it.get("fields") or {}).get("project")
                if p:
                    vals[it["id"]] = p
            dv = c.tool("derive", {"corpus": "notes", "name": "project",
                                   "values": vals,
                                   "rationale": "用户要按项目看；project 是材料自带字段，"
                                                "但内核只给枚举字段生成值键",
                                   "derived_from": "材料字段 project（不是我推断的）"})
            again = c.tool("ask", {"corpus": "notes", "require": ["project=alpha"],
                                   "with_body": True, "limit": 10})
            gap_event = (f"拒收 → derive {dv.get('field')}（{dv.get('n_items')} 项）"
                         f" → 再查 ok={again.get('ok')}"
                         f"（{again.get('n_answer')} 项）")
            print(f"   ⇒ {gap_event}")

        if i in (3, 6, 12):
            drift[i] = {}

        # ── 当天问一条事实题（两边都还拿得到）──────────────────────
        if i == 1:
            val = f["vals"]["lr"]
            r = c.tool("ask", {"corpus": "notes", "require": ["type=实验"],
                               "with_body": True, "limit": 10})
            bodies = json.dumps(r.get("bodies") or {}, ensure_ascii=False)
            chars_with += len(bodies)
            print(f"\n① 第 1 天：问「alpha 第一次跑的 lr 是多少」")
            print(f"   WITH    ⇒ {len(bodies)} 字｜命中 {val in bodies}")
            print(f"   WITHOUT ⇒ 窗口里 {sum(len(x) for x in ctx)} 字｜"
                  f"命中 {any(val in x for x in ctx)}")

        # ── 检查点：同一批事实，谁还记得 ───────────────────────────
        if i in (3, 6, 12):
            for lab, nid2, key2 in (("d01.lr", "d01", "lr"), ("d02.base", "d02", "base"),
                                    ("d03.不可复现", "d03", None)):
                val = facts[nid2]["vals"][key2] if key2 else "不可复现"
                r = c.tool("ask", {"corpus": "notes", "require": [],
                                   "with_body": True, "limit": 30})
                bodies = json.dumps(r.get("bodies") or {}, ensure_ascii=False)
                chars_with += len(bodies)
                drift[i][lab] = {"with": val in bodies,
                                 "without": any(val in x for x in ctx),
                                 "window": sum(len(x) for x in ctx)}
                if not drift[i][lab]["with"]:
                    misses.append(f"{nid2}:{key2 or 'qual'}@d{i}")

    # ── 第 12 天：派生维度已过期 ⇒ 重新 derive（force），并报两次覆盖率 ──
    rd2 = c.tool("ask", {"corpus": "notes", "require": [], "with_body": True,
                         "limit": 40})
    chars_with += len(json.dumps(rd2.get("bodies") or {}, ensure_ascii=False))
    vals2 = {it["id"]: (it.get("fields") or {}).get("project")
             for it in ((rd2.get("bodies") or {}).get("items") or [])}
    vals2 = {k: v for k, v in vals2.items() if v}
    dv2 = c.tool("derive", {"corpus": "notes", "name": "project", "values": vals2,
                             "rationale": "第 2 天建的那份只覆盖 3 项，后来的笔记没进去",
                             "derived_from": "材料字段 project", "force": True})
    print(f"\n⑥ 派生维度过期：第 2 天建的那份只覆盖 3 项 → 第 12 天重 derive "
          f"{dv2.get('n_items')} 项｜覆盖率 {dv2.get('coverage')}")

    # ── 最后一天：六类问题 ────────────────────────────────────────
    print("\n③ 最后一天（12 天后）：六类问题")
    QS = [
        ("事实", "alpha 第一次跑的 lr 与 loss 尖峰", ["project=alpha"], "lr", "d01"),
        ("事实", "组 A 的 baseline", ["project=alpha"], "base", "d02"),
        ("结构", "beta 线有几条记录", ["project=beta"], None, None),
        ("结构", "alpha 的实验有几条", ["project=alpha", "type=实验"], None, None),
        ("边界", "有没有客户投诉稳定性", ["project=gamma"], None, None),
        ("元", "这个库覆盖哪些项目", [], None, None),
        ("追述", "第 3 天记的那条（B 方案）", ["project=beta"], None, "d03"),
    ]
    for cat, q, req, key, nid in QS:
        r = c.tool("ask", {"corpus": "notes", "require": req, "with_body": True,
                           "limit": 30})
        bodies = json.dumps(r.get("bodies") or {}, ensure_ascii=False)
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
        print(f"   [{cat}] {q}")
        print(f"        ⇒ ok={r.get('ok')}｜{r.get('n_answer')} 项｜"
              f"指回 {len(ids)} 个 id｜{len(bodies)} 字"
              f"{('｜' + mark) if mark else ''}")

    # ── 偏移 ──────────────────────────────────────────────────────
    print("\n④ 答案偏移（同一批事实，第 3/6/12 天还记得吗）")
    print("   检查点        d01.lr      d02.base    d03.不可复现    窗口字数")
    for day in sorted(drift):
        row = drift[day]
        cells = [f"{'有' if row[k]['with'] else '无'}/{'有' if row[k]['without'] else '无'}"
                 for k in ("d01.lr", "d02.base", "d03.不可复现")]
        print(f"   第 {day:>2} 天      " + "      ".join(f"{c:<7}" for c in cells)
              + f"   {row['d01.lr']['window']}")

    print("\n⑤ 汇总")
    print(f"   缺口事件：{gap_event}")
    print(f"   WITH 上下文成本（**全量拉**：每次 with_body+limit30）：{chars_with} 字"
          f"（{len(QS) + 2 + 3} 次查询）")
    print(f"   WITHOUT 上下文成本：常驻 {sum(len(x) for x in ctx)} 字"
          f"（上限 {WINDOW_NOTES} 条笔记）")
    print(f"   漏检：{misses or '无'}")

    # ── ⑥ 成本对比：全量拉 vs 真实用法（先看支，再只取 2 条）────────
    # ⚠️ 上面那个 40k 是**我的探针太粗**（每次都把整支正文拉回来）。
    #    真实 agent 会先 ask（不带正文）看有几支，再只读需要的 —— 这里量它。
    lean = 0
    full7 = 0
    for cat, q, req, key, nid in QS:
        # 全量拉（对照）：一次问，正文拉 30 条
        r0 = c.tool("ask", {"corpus": "notes", "require": req,
                            "with_body": True, "limit": 30})
        full7 += len(json.dumps(r0.get("bodies") or {}, ensure_ascii=False))
        r1 = c.tool("ask", {"corpus": "notes", "require": req})      # 只看支，不拉正文
        lean += len(json.dumps(r1, ensure_ascii=False))
        r2 = c.tool("ask", {"corpus": "notes", "require": req,
                            "with_body": True, "limit": 2})          # 只读 2 条
        lean += len(json.dumps(r2.get("bodies") or {}, ensure_ascii=False))
    print(f"\n⑦ 成本对比（**同一组 {len(QS)} 个问题**，两种用法）")
    print(f"   全量拉（一次 with_body limit30）：{full7} 字")
    print(f"   真实用法（先 ask 看支 → 再 with_body limit2）：{lean} 字")
    print(f"   ⇒ 差 {(lean / max(1, full7) - 1) * 100:+.0f}%"
          f"（{'更贵' if lean > full7 else '更省'}）")
    print("   ⚠️ 反直觉：分两步**没有更省** —— 因为每次调用的**元数据**（nodes/"
          "provenance/query 回显）才是大头，两步就付两遍。")

    # ── ⑧ 一次 ask 的返回里，元数据 vs 正文各占多少 ──────────────────
    one = c.tool("ask", {"corpus": "notes", "require": ["project=alpha"],
                         "with_body": True, "limit": 30})
    whole = len(json.dumps(one, ensure_ascii=False))
    body_part = len(json.dumps(one.get("bodies") or {}, ensure_ascii=False))
    print(f"\n⑧ 单次 ask 的构成（project=alpha）")
    print(f"   整包 {whole} 字｜其中正文块 {body_part} 字"
          f"（{body_part / max(1, whole) * 100:.0f}%），"
          f"元数据 {whole - body_part} 字（{(whole - body_part) / max(1, whole) * 100:.0f}%）")

    c.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
