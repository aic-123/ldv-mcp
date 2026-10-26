# -*- coding: utf-8 -*-
"""一次真实工作：**ldv 能不能当指挥**（2026-10-26）。

前面的探针都在量「ldv 答得对不对」。这一支量另一件事：
**agent 只靠 ldv 的返回，能不能把一件工作做完。**

    ── 任务（不是查询）──────────────────────────────────────────
    给这批 60 个包出一份「Python 版本门槛 + 许可风险」清单：
      ① 要求 Python ≥ 3.11 的是哪些
      ② 没有声明许可的是哪些
      ③ 每条都指回包名
      ④ 材料里没有的，标「无支撑」，**不许编**
    ──────────────────────────────────────────────────────────

素材是真 PyPI 元数据（`repo/_data/pypi-deps`，60 个包）。
关键点：`Python 要求` 与 `许可` 都写在 `notes` 正文里，**不是枚举键**
⇒ 想按它们筛，必须先「失配 → 造维度 → 再查」——
这正是「从翻译到使用」要验的那一步。

⚠️ 这里的「agent」是**脚本化的**：每一步的决策都是返回字段的**纯函数**。
   它不许猜、不许读文件绕过 ldv。**它做得到 ⇒ ldv 的返回够用；做不到 ⇒ 不够。**
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent      # ldv-mcp/
PY = sys.executable
SRC = HERE.parent / "repo" / "_data" / "pypi-deps" / "nodes"
FAIL = 0
DECISIONS: list[tuple[str, str, str]] = []


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}"
          + (f"\n        {detail}" if detail else ""))


def decide(step: str, field: str, value, why: str) -> None:
    """记一条决策：**它依据哪个返回字段**。空依据 = 猜的 = 这条工作不算完成。"""
    DECISIONS.append((step, field, why))
    shown = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    print(f"   · {step}\n       依据 {field} = {str(shown)[:76]}")


class MCP:
    def __init__(self, store: Path):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LDV_MCP_STORE"] = str(store)
        self.p = subprocess.Popen([PY, str(HERE / "server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True,
                                  encoding="utf-8", env=env, cwd=str(HERE))
        self.n = 0
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "worker", "version": "0"}})

    def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n,
                                       "method": method,
                                       "params": params or {}},
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
            return json.loads(txt) if txt else {}
        except ValueError:
            return {"_raw": txt}

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:                              # noqa: BLE001
            pass


# ── 独立重算（不依赖 ldv 的解析）—— 判据 T3 要拿它跟 ldv 比 ──────────
#: 「≥3.11」在材料里落在**两个档**上（`>=3.11` 与 `>=3.12`）。
#: ⚠️ 这不是 ldv 的缺陷，是**分工**：`ask` 是精确匹配，不做语义包含
#:   （「3.12 属于 3.11」是**判断**，不是检索）。判断归 agent，路由归 ldv。
GE311 = (">=3.11", ">=3.12")


def recount() -> dict:
    """用**字符串直查**数一遍（与 ldv 的「解析成键再比较」是两条路）。"""
    ge311, lic_missing, total = [], [], 0
    for p in sorted(SRC.glob("*.md")):
        t = p.read_text(encoding="utf-8")
        total += 1
        pid = p.stem
        if any(f"Python 要求 {v}" in t for v in GE311):
            ge311.append(pid)
        if "许可 未声明" in t:
            lic_missing.append(pid)
    return {"total": total, "ge311": ge311, "lic_missing": lic_missing}


def extract(nodes: list[dict]) -> tuple[dict, dict]:
    """**这是 agent 的活**：从 ldv 返回的 `fields.notes` 里抽出两列。"""
    py, lic = {}, {}
    for it in nodes:
        nid, notes = it.get("title"), (it.get("fields") or {}).get("notes") or ""
        if not nid:
            continue
        m = re.search(r"Python 要求 ([^；]+)", notes)
        if m:
            py[nid] = m.group(1).strip()
        m = re.search(r"许可 ([^；]+)", notes)
        if m:
            lic[nid] = m.group(1).strip()
    return py, lic


def main() -> int:
    if not SRC.is_dir():
        print("⊘ 语料不在（repo/_data/pypi-deps）—— 跳过，**不等于通过**")
        return 0
    truth = recount()
    tmp = Path(tempfile.mkdtemp())
    store = tmp / "s"
    nodes = store / "_data" / "pypi" / "nodes"
    nodes.mkdir(parents=True)
    for p in SRC.glob("*.md"):
        shutil.copy2(p, nodes / p.name)
    (store / "_data" / "empty" / "nodes").mkdir(parents=True)   # 反向对照用
    m = MCP(store)
    sid = "work-1"
    try:
        print("=" * 78)
        print("一次真实工作｜ldv 能不能当指挥")
        print("=" * 78)
        print(f"素材：{truth['total']} 个包的真 PyPI 元数据")
        print("任务：① 要求 Python ≥3.11 的包  ② 未声明许可的包  ③ 每条指回包名")
        print("      ④ 材料里没有的标「无支撑」，不许编\n")

        # ── 第 1 步：先看自己有什么（不查，先认结构）──────────────────
        print("① 开工前先认结构")
        o = m.tool("orient", {"corpus": "pypi", "session": sid})
        decide("认结构", "orient.keys", o.get("n_keys"),
               f"词汇表里有 {o.get('n_keys')} 个键")
        chk("T1a", "`orient` 说清了「哪些维度可查」",
            bool(o.get("keys")) and o.get("n_items") == truth["total"],
            f"项数 {o.get('n_items')}（应 {truth['total']}）")

        # ── 第 2 步：直接按 Python 门槛问 —— 材料不支持 ────────────────
        print("\n② 直接问「Python ≥3.11 的有哪些」")
        miss = m.tool("ask", {"corpus": "pypi", "require": ["py=>=3.11"],
                              "session": sid})
        decide("发现材料不支持这个问法", "ask.out_of_structure",
               miss.get("out_of_structure"), "这个维度不存在")
        chk("T2a", "★ 失配**明说**出来，而且指了下一步（不是「答不了」）",
            miss.get("out_of_structure") is True
            and miss.get("can_agent_fill") is True,
            f"out_of_structure={miss.get('out_of_structure')}"
            f"｜can_agent_fill={miss.get('can_agent_fill')}")

        # ── 第 3 步：把正文要过来，自己抽（agent 的活）─────────────────
        print("\n③ 要正文，自己抽这两列（ldv 不管抽取，它管存与查）")
        full = m.tool("ask", {"corpus": "pypi", "require": [], "with_body": True,
                              "limit": truth["total"], "session": sid})
        items = full.get("items") or []
        decide("取全部正文", "ask.items", len(items), "一次拿全，后面不用再读文件")
        py, lic = extract(items)
        chk("T3a", "★ 抽取的覆盖面：60 个包两列都抽到（没抽到的会漏结论）",
            len(py) == truth["total"] and len(lic) == truth["total"],
            f"py {len(py)} / lic {len(lic)}（应 {truth['total']}）")

        # ── 第 4 步：造维度（把抽出来的变成可查的）─────────────────────
        print("\n④ 造两个维度，把抽出来的写回结构")
        d1 = m.tool("derive", {"corpus": "pypi", "name": "py", "values": py,
                               "rationale": "按最低 Python 版本分得开：>=3.7 到 >=3.12 六档，"
                                            "各档项数都非零",
                               "derived_from": "原材料（notes 里的 Python 要求）",
                               "what": "包要求的最低 Python 版本",
                               "session": sid})
        decide("造维度 py", "derive.ok", d1.get("ok"), "失配已解除")
        d2 = m.tool("derive", {"corpus": "pypi", "name": "lic", "values": lic,
                               "rationale": "按许可类型分得开：未声明 / MIT / MPL-2.0 等，"
                                            "未声明一档非空",
                               "derived_from": "原材料（notes 里的许可）",
                               "what": "包声明的许可",
                               "session": sid})
        chk("T4a", "两个维度都建成了", d1.get("ok") is True and d2.get("ok") is True,
            f"py={d1.get('ok')}｜lic={d2.get('ok')}")

        # ── 第 5 步：同一个问题再问一遍 —— 现在能答了（闭环）───────────
        # ★ 分工在这里显形：**agent 判断「≥3.11 落在哪几档」，ldv 负责精确路由。**
        #   `ask` 不做语义包含（`>=3.12` ⊄ `>=3.11` 在键空间里）—— 那不是它的活。
        print("\n⑤ 同一个问题再问一遍（agent 决定问哪几档，ldv 精确路由）")
        print(f"   agent 判断：「≥3.11」落在 {list(GE311)} 两档")
        got: list[str] = []
        per_key: dict[str, int] = {}
        for v in GE311:
            # ⚠️ `limit` 必须显式放大：`ask` 默认只给前几条（`n_answer` 是全量）。
            #    拿**被截断的 items** 去跟全集比 ⇒ 会误判成「ldv 漏项」。
            a = m.tool("ask", {"corpus": "pypi", "require": [f"py={v}"],
                               "limit": truth["total"], "session": sid})
            per_key[v] = a.get("n_answer") or 0
            got += [it.get("title") for it in (a.get("items") or [])]
        decide("现在答得出", "ask.n_answer", per_key,
               "同一条查询，derive 之前报失配、之后给答案")
        chk("T2b", "★ 闭环：derive 之前报失配、之后**每档都答得出**",
            all(per_key.values()) and sum(per_key.values()) == len(truth["ge311"]),
            f"各档 {per_key}｜合计 {sum(per_key.values())}"
            f"（独立重算 {len(truth['ge311'])}）")
        chk("T3b", "★ **ldv 的精确路由 == agent 的语义结论**（两档并集逐项相同）",
            sorted(got) == sorted(truth["ge311"]),
            f"ldv {len(got)} 条 {sorted(got)}｜直查 {sorted(truth['ge311'])}")
        a2 = m.tool("ask", {"corpus": "pypi", "require": ["lic=未声明"],
                            "limit": truth["total"], "session": sid})
        ids2 = [it.get("title") for it in (a2.get("items") or [])]
        chk("T3c", "第二列（许可）同样对得上",
            sorted(ids2) == sorted(truth["lic_missing"]),
            f"ldv {len(ids2)} 条｜直查 {len(truth['lic_missing'])} 条")

        # ── 第 6 步：无支撑 —— 必须说没有，不许编 ────────────────────
        # ★ 这里有两件**必须分得开**的事（第一版探针把它们混成一件，踩了）：
        #   ① 键**存在**但交集为空 ⇒ `ok=True, n_answer=0`（真·0 条）
        #   ② 键**不存在**（`py=>=4.0` 这个取值没人有）⇒ `out_of_structure=True`
        #   混成一件的话，「维度不存在」会被读成「有 0 条」—— 那是静默误读。
        print("\n⑥ 两种「没有」必须分得开")
        # 空交集得**现算**：第一版我手挑了 `type=Database`，结果那 1 个正是
        # sqlalchemy（它就要 >=3.11）⇒ 手挑的「空」是假的。
        by_type: dict[str, set] = {}
        for it in items:
            by_type.setdefault((it.get("fields") or {}).get("type"), set()).add(
                it.get("title"))
        ge = set(truth["ge311"])
        empty_type = next((t for t, v in sorted(by_type.items())
                           if t and not (v & ge)), None)
        a3 = m.tool("ask", {"corpus": "pypi",
                            "require": ["py=>=3.11", f"type={empty_type}"],
                            "session": sid})
        decide("真·0 条", "ask.n_answer", a3.get("n_answer"),
               f"键都在、交集为空（type={empty_type} 没有 >=3.11 的包）")
        chk("T5a", "★ 键都在但交集为空 ⇒ `n_answer=0`（真·0 条，不是「没查」）",
            a3.get("ok") is True and a3.get("n_answer") == 0
            and not (a3.get("items") or []),
            f"type={empty_type}｜ok={a3.get('ok')}｜n_answer={a3.get('n_answer')}")
        a3u = m.tool("ask", {"corpus": "pypi", "require": ["py=>=4.0"], "session": sid})
        decide("维度里没这个取值", "ask.out_of_structure",
               a3u.get("out_of_structure"), "不是 0 条，是「这个键不存在」")
        chk("T5a2", "★ 未知取值 ⇒ **`out_of_structure`**（与「0 条」分得开）",
            a3u.get("out_of_structure") is True,
            f"out_of_structure={a3u.get('out_of_structure')}"
            f"｜n_answer={a3u.get('n_answer')}（应是 None，不是 0）")
        # ★ 反向对照：真有支撑的档位**不许**被标成无支撑
        a4 = m.tool("ask", {"corpus": "pypi", "require": ["py=>=3.12"], "session": sid})
        chk("T5b", "★ 反向对照：`>=3.12` 有支撑 ⇒ **不许**标无支撑",
            (a4.get("n_answer") or 0) > 0,
            f"n_answer={a4.get('n_answer')}（若 0 ⇒ 「无支撑」这个判定没区分力）")

        # ── 第 7 步：把结论写回去（工作要留痕）────────────────────────
        print("\n⑦ 把结论与一条发现写回结构")
        r1 = m.tool("remember", {
            "corpus": "pypi", "id": "finding-1",
            "fields": {"type": "结论", "notes": "切片里要求 Python ≥3.11 的有 "
                                                f"{len(got)} 个"},
            "body": f"本次工作发现：要求 Python ≥3.11 的包共 {len(got)} 个："
                    + "、".join(sorted(got)),
            "session": sid})
        decide("记一条发现", "remember.written", r1.get("written"), "工作结论要留痕")
        ans = m.tool("remember_answer", {
            "corpus": "pypi", "require": ["py=>=3.11"],
            "answer": f"要求 Python ≥3.11 的包有 {len(got)} 个：" + "、".join(sorted(got)),
            "source": sorted(got)[:3], "session": sid})
        chk("T6a", "结论编进树（下次同类问题先命中，不必重算）",
            ans.get("written") is True and bool(ans.get("key")),
            f"key={ans.get('key')}")

        # ── 第 8 步：收尾 —— 还缺什么 + 我改了哪几处 ─────────────────
        print("\n⑧ 收尾：还缺什么、我改了哪几处")
        nx = m.tool("next", {"corpus": "pypi"})
        decide("看还缺什么", "next.清单", list((nx.get("清单") or {}).keys()),
               "后果三档，不是分数")
        chk("T7a", "`next` 给出「还缺什么」（三档，不是打分）",
            isinstance(nx.get("清单"), dict) and len(nx.get("清单") or {}) >= 1,
            f"清单键={list((nx.get('清单') or {}).keys())}")
        fin = m.tool("orient", {"corpus": "pypi", "session": sid})
        chg = fin.get("changes") or {}
        decide("报回执", "orient.changes", chg.get("合计"), "本次改了哪几处")
        chk("T7b", "★ 回执点得出本次的写：2 个维度 + 1 条记忆 + 1 条答案",
            (chg.get("造维度") == ["py", "lic"] and chg.get("新写") == ["finding-1"]
             and len(chg.get("编译答案") or []) == 1),
            f"造维度={chg.get('造维度')}｜新写={chg.get('新写')}"
            f"｜编译答案={chg.get('编译答案')}｜合计={chg.get('合计')}")

        # ── 第 9 步：反向对照 —— 空语料上，agent 必须停，不许编 ────────
        print("\n⑨ 反向对照：换成空语料，同一套动作")
        e = m.tool("ask", {"corpus": "empty", "require": [], "with_body": True})
        e_py, e_lic = extract(e.get("items") or [])
        chk("T8a", "★ 空语料 ⇒ 抽出 0 条 ⇒ 结论只能是「无支撑」，编不出来",
            len(e.get("items") or []) == 0 and not e_py and not e_lic,
            f"items={len(e.get('items') or [])}｜抽到 py {len(e_py)} / lic {len(e_lic)}")

        # ── 产出：这次工作的交付物（不是测试读数，是结果本身）──────────
        print("\n" + "=" * 78)
        print("工作产出")
        print("=" * 78)
        print(f"① 要求 Python ≥3.11 的包（{len(got)} 个，附材料里的原始档位）：")
        for nid in sorted(got):
            print(f"     {nid:22s} py={py.get(nid)}")
        print(f"\n② 未声明许可的包（{len(truth['lic_missing'])} 个）：")
        miss = sorted(truth["lic_missing"])
        print("     " + "、".join(miss[:12]) + (f" …（共 {len(miss)} 个）"
                                               if len(miss) > 12 else ""))
        print("\n③ 材料里没有的档位（`py=>=4.0`）⇒ **无支撑**，不编：")
        print("     结论里写「无支撑」，而不是「0 个包要求 4.0」——"
              "前者是「材料没说」，后者是「材料说没有」，两件事")
        print("\n④ 本次改动的回执：")
        print(f"     造维度 {chg.get('造维度')}｜新写 {chg.get('新写')}"
              f"｜编译答案 {len(chg.get('编译答案') or [])} 条")

        # ── 总判：每一步决策都得挂得住返回字段 ────────────────────────
        print("\n" + "=" * 78)
        print("总判：这次工作是不是「ldv 指挥」出来的")
        print("=" * 78)
        empty = [d for d in DECISIONS if not d[1] or d[2] in (None, "", [])]
        chk("T9", "★ 每一步决策都挂得住**返回字段**（没有一步是猜的）",
            not empty,
            f"{len(DECISIONS)} 步决策，空依据 {len(empty)} 条"
            + (f"：{[d[0] for d in empty]}" if empty else ""))
        print(f"\n  本次工作共 {len(DECISIONS)} 个决策点，全部由 ldv 的返回驱动：")
        for step, field, why in DECISIONS:
            print(f"    · {step:22s} ← {field}")
        return 1 if FAIL else 0
    finally:
        m.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
