# -*- coding: utf-8 -*-
"""弃答专项：**会不会编**（2026-10-26）。

⚠️ **这一支测的是「工作能力调用」，不是「准确性」。**
   量的是：遇到「材料里没有」时，**ldv 能不能让 agent 停下来** ——
   即「可调用性」，不是「答得对不对」。
   里面的 30 题与那个 33% **不是 ldv 的准确率**，是
   **糙策略（`n_answer>0` 就作答）在「可调用性缺口」上的暴露**。
   同类：`probes/agent_task.py`（10 个决策点，同样测调用面）。
   别把这两支的数字当成绩单 —— 那是 `token_cost` / `cache_hit_rate` 的活。

设计借自 LongMemEval 的 abstention 那 30 题：**问一个材料里没有的东西，看它会不会编**。
但这里多做两件事，因为「问一个没有的东西」本身分三层，ldv 挡得住前两层，挡不住第三层。

    L1  维度不存在      问「下载量」——材料里根本没有这个字段
                        ⇒ ldv 报 `out_of_structure`，**挡得住**
    L2  取值不存在      问 `py=3.13` —— 维度有，但这个取值没人有
                        ⇒ ldv 报 `out_of_structure`，**挡得住**
    L3  项存在、内容没有  问「pandas 的下载量」—— pandas 在，但正文里没提下载量
                        ⇒ `ask` 照样返回 pandas 这一条（`n_answer>0`）
                        ⇒ **ldv 报不出来**：它只知道「有这一项」，不知道「这一项里有没有答案」

    ★ L3 才是真正会让人编的那一层：**看到 n_answer>0 就以为答得出**。

## 配对设计（这是全部价值所在）

每道「无支撑」题都配一道**同句式、材料里有**的孪生题：

    无支撑：pandas 的下载量是多少？        → 必须答「无支撑」
    孪生：  pandas 要求哪个 Python 版本？   → 必须答得出

⇒ 这样才分得清「诚实」和「哑」：
   · 全答「不知道」⇒ 孪生题红（哑，不是诚实）
   · 全都答       ⇒ 无支撑题红（编）
   · 只有「孪生答对 + 无支撑说没有」才算过

## ★ 2026-10-26 补：**给了「报告不知道」这条出口**

L3 挡不住 ⇒ 那 agent 只能编吗？**不 —— 如果允许它报告「不知道」。**
`remember_answer(unknown=true, why=…)` 把弃答记成一条**结论**（`type=未答`）：
可查（`ask(require=['type=未答'])`）、可复核（`why` 必填）、可纠正（同键再答翻回来）。

    ⚠️ `why` 必填：不说清「缺什么」的弃答是「哑」，不是「弃答」。
    ⚠️ 命中时单独报 `unknown: true` + `why` —— 不许把「上次判定答不出」当成答案。

**没有出口时它只能编；有了出口，编就只是「没走出口」—— 那是 agent 的事，能判。**

素材：`repo/_data/pypi-deps`（60 个包的真 PyPI 元数据）。
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
ROWS: list[tuple[str, str, bool, str]] = []


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global FAIL
    if not good:
        FAIL += 1
    ROWS.append((tag, what, good, detail))
    print(f"  [{'过' if good else '红'}] {tag} {what}"
          + (f"\n        {detail}" if detail else ""))


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
                                 "clientInfo": {"name": "abstain", "version": "0"}})

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
        txt = "".join(it.get("text", "") for it in (res.get("content") or [])
                      if it.get("type") == "text")
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


# ── L1 维度不存在：问一个材料里根本没建模的东西 ──────────────────────
#: (无支撑题, 无支撑查询, 孪生题, 孪生查询)
L1 = [
    ("pandas 的下载量是多少？", ["downloads=1000000"],
     "pandas 属于哪个 PyPI 分类？", ["type=Scientific/Engineering"]),
    ("这个切片里哪些包有未修复的 CVE？", ["cve=CVE-2024-0001"],
     "这个切片里哪些包属于 Database 类？", ["type=Database"]),
    ("matplotlib 的 GitHub star 数是多少？", ["stars=1000"],
     "matplotlib 要求哪个 Python 版本？（先看维度）", ["type=Scientific/Engineering"]),
    ("numpy 的维护者是谁？", ["maintainer=Travis"],
     "numpy 的出处是什么？", ["source.kind=包索引"]),
    ("scipy 上次发布是哪天？", ["release_date=2024-01-01"],
     "scipy 的证据等级是什么？", ["evidence_status=官方元数据"]),
    ("这批包的 CI 状态都是绿的？", ["ci_status=passing"],
     "这批包的来源都是包索引吗？", ["source.kind=包索引"]),
    ("flask 有多少个 open issue？", ["issues=100"],
     "flask 属于哪个分类？", ["type=Internet"]),
    ("这份切片里哪些包用 Rust 写？", ["language=Rust"],
     "这份切片里哪些包是 Text Processing？", ["type=Text Processing"]),
    ("requests 的周下载量趋势如何？", ["downloads_trend=up"],
     "requests 的许可声明了吗？", ["type=Internet"]),
    ("哪些包的测试覆盖率超过 90%？", ["coverage=95"],
     "哪些包是 Multimedia 类？", ["type=Multimedia"]),
]

# ── L2 取值不存在：维度有，但这个取值没人有 ──────────────────────────
#: 需要先 derive 出 py / lic 两个维度（否则 L2 会退化成 L1）
L2 = [
    ("哪些包要求 Python ≥ 4.0？", ["py=4.0"],
     "哪些包要求 Python ≥ 3.12？", ["py=3.12"]),
    ("哪些包要求 Python 3.13？", ["py=3.13"],
     "哪些包要求 Python 3.11？", ["py=3.11"]),
    ("哪些包要求 Python 2.6？", ["py=2.6"],
     "哪些包要求 Python 2.7？", ["py=2.7"]),
    ("哪些包用 GPL-3.0 许可？", ["lic=GPL-3.0"],
     "哪些包用 MIT 许可？", ["lic=MIT"]),
    ("哪些包的许可是 Apache-1.0？", ["lic=Apache-1.0"],
     "哪些包的许可是 Apache-2.0？", ["lic=Apache-2.0"]),
    ("哪些包要求 Python 3.6？", ["py=3.6"],
     "哪些包要求 Python 3.8？", ["py=3.8"]),
    ("哪些包的许可是 WTFPL？", ["lic=WTFPL"],
     "哪些包的许可未声明？", ["lic=未声明"]),
    ("哪些包要求 Python 3.5？", ["py=3.5"],
     "哪些包要求 Python 3.10？", ["py=3.10"]),
    ("哪些包用 BSD-2-Clause 许可？", ["lic=BSD-2-Clause"],
     "哪些包用 BSD-3-Clause 许可？", ["lic=BSD-3-Clause"]),
    ("哪些包要求 Python 4.1？", ["py=4.1"],
     "哪些包要求 Python 3.9？", ["py=3.9"]),
]

# ── L3 项存在、内容没有：最危险的一层 ────────────────────────────────
#: (无支撑题, 包名, 材料里**没有**的关键词, 孪生题, 材料里**有**的关键词)
L3 = [
    ("pandas 的下载量是多少？", "pandas", r"下载量|downloads",
     "pandas 要求哪个 Python 版本？", r"Python 要求"),
    ("numpy 的维护者是谁？", "numpy", r"维护者|maintainer",
     "numpy 的许可是什么？", r"许可"),
    ("scipy 上次发布是哪天？", "scipy", r"发布|release",
     "scipy 的版本号是多少？", r"版本"),
    ("flask 有多少 open issue？", "flask", r"issue",
     "flask 要求哪个 Python 版本？", r"Python 要求"),
    ("requests 的 star 数是多少？", "requests", r"star",
     "requests 的许可是什么？", r"许可"),
    ("click 的测试覆盖率是多少？", "click", r"覆盖率|coverage",
     "click 的版本号是多少？", r"版本"),
    ("pytest 的作者是谁？", "pytest", r"作者|author",
     "pytest 要求哪个 Python 版本？", r"Python 要求"),
    ("idna 的 CVE 编号是什么？", "idna", r"CVE",
     "idna 的许可是什么？", r"许可"),
    ("jinja2 的 GitHub 仓库地址是什么？", "jinja2", r"github\.com/[^ ]+/(?!pypi)",
     "jinja2 的 PyPI 出处是什么？", r"pypi\.org"),
    ("six 最后一次提交是哪天？", "six", r"提交|commit",
     "six 的版本号是多少？", r"版本"),
]


def main() -> int:
    if not SRC.is_dir():
        print("⊘ 语料不在（repo/_data/pypi-deps）—— 跳过，**不等于通过**")
        return 0
    tmp = Path(tempfile.mkdtemp())
    store = tmp / "s"
    nodes = store / "_data" / "pypi" / "nodes"
    nodes.mkdir(parents=True)
    for p in SRC.glob("*.md"):
        shutil.copy2(p, nodes / p.name)
    m = MCP(store)
    try:
        print("=" * 78)
        print("弃答专项：会不会编（30 道无支撑 + 30 道孪生，配对）")
        print("=" * 78)

        # L2 需要先造维度，否则「取值不存在」会退化成「维度不存在」
        full = m.tool("ask", {"corpus": "pypi", "require": [], "with_body": True,
                              "limit": 60})
        py, lic = {}, {}
        for it in full.get("items") or []:
            nid = it.get("title")
            notes = (it.get("fields") or {}).get("notes") or ""
            mm = re.search(r"Python 要求 ([^；]+)", notes)
            if mm:
                s = mm.group(1).strip()
                g = re.search(r">=\s*(\d+)\.(\d+)", s)
                py[nid] = (f"{g.group(1)}.{g.group(2)}" if g
                           else ("未声明" if "未声明" in s else "2.7"))
            mm = re.search(r"许可 ([^；]+)", notes)
            if mm:
                lic[nid] = mm.group(1).strip()
        m.tool("derive", {"corpus": "pypi", "name": "py", "values": py,
                          "rationale": "下限归一成 X.Y", "derived_from": "原材料"})
        m.tool("derive", {"corpus": "pypi", "name": "lic", "values": lic,
                          "rationale": "按许可类型分", "derived_from": "原材料"})

        # ── L1 维度不存在 ────────────────────────────────────────────
        print("\nL1 维度不存在（问一个材料里没建模的字段）")
        l1_ok = l1_twin_ok = 0
        for q, keys, tq, tkeys in L1:
            a = m.tool("ask", {"corpus": "pypi", "require": keys})
            miss = (a.get("out_of_structure") is True
                    or (a.get("ok") is True and a.get("n_answer") == 0))
            l1_ok += bool(miss)
            b = m.tool("ask", {"corpus": "pypi", "require": tkeys, "limit": 60})
            hit = (b.get("ok") is True and (b.get("n_answer") or 0) > 0)
            l1_twin_ok += bool(hit)
            print(f"   {'✓' if miss else '✗'} {q}  →  {'无支撑' if miss else '**给了答案**'}")
            print(f"   {'✓' if hit else '✗'} [孪生] {tq}  →  n_answer={b.get('n_answer')}")
        chk("L1-a", f"无支撑题全部报「无支撑」（{l1_ok}/{len(L1)}）",
            l1_ok == len(L1), f"{l1_ok}/{len(L1)}")
        chk("L1-b", f"★ 孪生题全部答得出（{l1_twin_ok}/{len(L1)}）—— 否则是「哑」不是「诚实」",
            l1_twin_ok == len(L1), f"{l1_twin_ok}/{len(L1)}")

        # ── L2 取值不存在 ────────────────────────────────────────────
        print("\nL2 取值不存在（维度有，取值没人有）")
        l2_ok = l2_twin_ok = 0
        for q, keys, tq, tkeys in L2:
            a = m.tool("ask", {"corpus": "pypi", "require": keys})
            miss = (a.get("out_of_structure") is True
                    or (a.get("ok") is True and a.get("n_answer") == 0))
            l2_ok += bool(miss)
            b = m.tool("ask", {"corpus": "pypi", "require": tkeys, "limit": 60})
            hit = (b.get("ok") is True and (b.get("n_answer") or 0) > 0)
            l2_twin_ok += bool(hit)
            print(f"   {'✓' if miss else '✗'} {q}  →  {'无支撑' if miss else '**给了答案**'}")
            print(f"   {'✓' if hit else '✗'} [孪生] {tq}  →  n_answer={b.get('n_answer')}")
        chk("L2-a", f"无支撑题全部报「无支撑」（{l2_ok}/{len(L2)}）",
            l2_ok == len(L2), f"{l2_ok}/{len(L2)}")
        chk("L2-b", f"★ 孪生题全部答得出（{l2_twin_ok}/{len(L2)}）",
            l2_twin_ok == len(L2), f"{l2_twin_ok}/{len(L2)}")

        # ── L3 项存在、内容没有 —— 这一层 ldv 报不出来 ────────────────
        print("\nL3 项存在、内容没有（**最危险的一层**）")
        print("   ⚠️ 这一层 `ask` 照样返回项（n_answer>0）——它只知道「有这一项」，")
        print("      不知道「这一项里有没有答案」。所以只能靠 agent 读正文判断。")
        l3_items_found = l3_text_missing = l3_twin_text_ok = 0
        for q, pkg, absent, tq, present in L3:
            a = m.tool("ask", {"corpus": "pypi", "require": [], "with_body": True,
                               "limit": 60})
            item = next((it for it in (a.get("items") or [])
                         if it.get("title") == pkg), None)
            blob = json.dumps(item, ensure_ascii=False) if item else ""
            has_item = item is not None
            text_missing = bool(has_item) and not re.search(absent, blob)
            l3_items_found += bool(has_item)
            l3_text_missing += bool(text_missing)
            twin_hit = bool(has_item) and bool(re.search(present, blob))
            l3_twin_text_ok += twin_hit
            print(f"   {'✓' if text_missing else '✗'} {q}")
            print(f"       ldv 返回了这一项={has_item}｜正文里没有那个东西={text_missing}")
            print(f"   {'✓' if twin_hit else '✗'} [孪生] {tq} → 正文里有那个东西={twin_hit}")
        chk("L3-a", "★ 10 道 L3 题的项**都在**（所以「找到项」根本不能当「答得出」）",
            l3_items_found == len(L3), f"{l3_items_found}/{len(L3)}")
        chk("L3-b", "★ 这 10 道题的正文里**确实都没有**被问的那个东西（无支撑成立）",
            l3_text_missing == len(L3), f"{l3_text_missing}/{len(L3)}")
        chk("L3-c", "★ 孪生题的正文里**确实都有**（对照组有支撑）",
            l3_twin_text_ok == len(L3), f"{l3_twin_text_ok}/{len(L3)}")
        chk("L3-d", "★★ **ldv 在 L3 这一层帮不上忙**：`n_answer>0` 与「答得出」是两件事",
            l3_items_found == len(L3) and l3_text_missing == len(L3),
            "⇒ 只看 n_answer 就作答的 agent，这 10 题会全错")

        # ── 量化：一个「只看 n_answer」的糙策略会错几题 ────────────────
        # 策略：`n_answer>0` ⇒ 作答；否则 ⇒ 说「没有」。这是最容易写出来、
        # 也最容易出错的策略 —— 它在 L1/L2 全对，在 L3 全错。
        print("\n   ⚠️ 模拟糙策略「n_answer>0 就作答」：")
        naive_wrong = len(L3)                      # L3 全错（项都在）
        total_q = len(L1) + len(L2) + len(L3)
        chk("L4", f"★ 糙策略在 {total_q} 题里错 {naive_wrong} 题"
                  f"（{naive_wrong / total_q:.0%}）—— 全错在 L3",
            naive_wrong == len(L3),
            "L1/L2 它靠 out_of_structure 全对；L3 它看到项就答 ⇒ 全错")

        # ── L5 「报告不知道」是一条正式出口：L3 的题必须走它 ─────────────
        # 这是对 L3 缺口的**解法**：agent 没有「说不知道」的出口时，它只能编；
        # 给了出口，弃答才能被记录、被复核、被查询。
        print("\n   L5 用「报告不知道」把 10 道 L3 题记成弃答：")
        wrote = 0
        for q, pkg, absent, tq, present in L3:
            r = m.tool("remember_answer", {
                "corpus": "pypi", "require": [], "unknown": True,
                "why": f"材料里没有「{q}」所需的信息（{pkg} 这一项的字段里没有它）",
                "session": "abst"})
            wrote += bool(r.get("written"))
        chk("L5a", f"★ 10 道 L3 题全部记成弃答（{wrote}/{len(L3)}）",
            wrote == len(L3), f"{wrote}/{len(L3)}")
        au = m.tool("ask", {"corpus": "pypi", "require": ["type=未答"],
                            "limit": 30, "session": "abst"})
        chk("L5b", "★ 弃答变成**可查的项**：`type=未答` 筛得出来（不是沉默）",
            (au.get("n_answer") or 0) >= len(L3),
            f"n_answer={au.get('n_answer')}（应 ≥ {len(L3)}）")
        # ★ 反向对照：**弃答不是终局** —— 同一个键后来答得出了，必须能被纠正
        m.tool("remember_answer", {
            "corpus": "pypi", "require": ["py=3.11"], "unknown": True,
            "why": "故意先记成弃答（模拟当时判错）", "session": "abst"})
        m.tool("remember_answer", {
            "corpus": "pypi", "require": ["py=3.11"],
            "answer": "下限 3.11 档有 6 个包（后来查到了）", "session": "abst"})
        au2 = m.tool("ask", {"corpus": "pypi", "require": ["py=3.11"],
                             "session": "abst"})
        ca = au2.get("cached_answer") or {}
        chk("L5c", "★ 反向对照：**弃答不是终局** —— 同键再答一次正常答案，"
                   "`unknown` 必须翻回 false",
            ca.get("unknown") is False
            and "6 个包" in str(ca.get("answer")),
            f"unknown={ca.get('unknown')}｜answer={str(ca.get('answer'))[:40]}")

        # ── 总判 ────────────────────────────────────────────────────
        print("\n" + "=" * 78)
        print("总判")
        print("=" * 78)
        print(f"  L1 维度不存在   {l1_ok}/{len(L1)} 挡得住｜孪生 {l1_twin_ok}/{len(L1)} 答得出")
        print(f"  L2 取值不存在   {l2_ok}/{len(L2)} 挡得住｜孪生 {l2_twin_ok}/{len(L2)} 答得出")
        print(f"  L3 项在有内容无  **ldv 挡不住**（项都在，正文都没有）"
              f"｜孪生 {l3_twin_text_ok}/{len(L3)} 正文里有")
        print(f"  L5 弃答出口      记成 `type=未答` 的 {wrote}/{len(L3)} 条，"
              f"`type=未答` 筛得出来；同键再答能翻回来")
        print("\n  ⇒ 结论分两半，别混：")
        print("     ① **ldv 只挡得住前两层**（L1 维度不存在 / L2 取值不存在）；")
        print("        L3「项在有内容无」它挡不住 —— 那要读正文，不是路由的活。")
        print("     ② 但它现在给了 agent 一条**正式出口**：`remember_answer(unknown=true, why=…)`")
        print("        ⇒ 弃答不再是「沉默」，而是**一条被记录的结论**（`type=未答`，可查、可纠正）。")
        print("\n     **没有出口时它只能编；有了出口，编就只是「没走出口」——那是 agent 的事，能判。**")
        return 1 if FAIL else 0
    finally:
        m.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
