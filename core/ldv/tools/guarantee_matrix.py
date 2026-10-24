# -*- coding: utf-8 -*-
"""**保证矩阵** —— 每条承诺 × 哪些判据在守它 × 有没有零判据的。

## 它治的是什么

外部审查的原话：「需要把这些局部保证整理成一份明确的**保证矩阵**，
而不是让读者从大量文档、断言编号和探针输出中自行拼接。」

本工具做三件事：

    ① 抽出**承诺清单**（断言集的 §K/§T/§A/§D/§S + 验收的 B1–B20）
    ② 抽出**判据清单**（`run_tests` 的 `ok(...)` 标签 + `checks/` 的 `rep.add(...)`）
    ③ 按**编号引用**把两边连起来 ⇒ 谁在守、谁没人守

## 为什么用「编号引用」而不是人工映射表

人工映射表有 51 行，**会漂**（改判据名时没人回去改表）。而判据标签里
本来就写着 `§K2` / `B17` —— 扫一遍就是索引，且**改名字会自动跟着变**。

⚠️ 它的**边界**（必须说清）：它判的是「**有没有判据提到这条承诺**」，
**不是**「这条判据判得对」。后者只有反向对照能回答。

## 用法

    python -m ldv.tools.guarantee_matrix           # 打表（静态）
    python -m ldv.tools.guarantee_matrix --check    # 与冻结基线比（新增/已修缺口都红）
    python -m ldv.tools.guarantee_matrix --write    # 重写基线（人确认后才用）
    python -m ldv.tools.guarantee_matrix --run      # 跑一遍判据，把状态贴上来（慢）

退出码：`--check` 有缺口变动 ⇒ 1；否则 0。
"""
from __future__ import annotations

import ast
import io
import json
import re
import sys
from pathlib import Path

from ._paths import REPO

#: 冻结基线（缺口清单）—— **两头都守**：新增缺口红，缺口被修好而不更新基线也红
BASELINE = REPO / "evidence" / "guarantee_gaps.json"

_SPEC_DOC = REPO / "docs" / "形式化断言集.md"
_DESIGN_DOC = REPO / "docs" / "分层方向视图-设计文档.md"


def commitments() -> list[str]:
    """承诺清单 —— 从**两处声明**抽，不从正文猜。

    ① `docs/形式化断言集.md` 第 1 行的范围声明（`§K1–§K9 · T1 · …`）
    ② `docs/分层方向视图-设计文档.md` 里 `**Bxx**` 的表格项

    ⚠️ 为什么不扫 `### §K1` 标题：§T1/§T2 没有这种标题（它们写在 §2 里），
       扫标题会**静默漏掉**两条 —— 而漏掉的那两条正好是「传输层」的承重墙。
    """
    head = io.open(_SPEC_DOC, encoding="utf-8").readline()
    out: list[str] = []
    for pre, lo, hi in re.findall(r"§([KATDS])(\d+)–§?\1(\d+)", head):
        out += [f"{pre}{i}" for i in range(int(lo), int(hi) + 1)]
    out += re.findall(r"(?<![A-Za-z])T\d+(?!\d)", head)
    des = io.open(_DESIGN_DOC, encoding="utf-8").read()
    b_ids = {m for m in re.findall(r"\*\*(B\d+)\*\*", des)}
    out += sorted(b_ids, key=lambda x: int(x[1:]))
    # 去重保序
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def judgments() -> list[str]:
    """判据清单 —— 两处来源，**都是判据自己的名字**。

    ① `ldv/tests/run_tests.py` 里 `ok("…")` 的第一个参数（判据标签）
    ② `ldv/checks/*.py` 里 `rep.add("Bxx", "标题")` 的编号 + 标题

    ⚠️ 只扫 `ok()` 的第一个参数，不扫 docstring —— docstring 里到处引用 §K8，
       那不是「这条判据在守 §K8」，只是「它讨论过 §K8」。

    ★★ **两种写法会漏，必须显式报出来**（`unresolved`）：
       - 标题是 **f-string**（`rep.add("B4", f"劈开成划分（{path}）")`）——
         实测 B4 就是这么写的，第一版正则漏了它 ⇒ 被误报成「零判据」
       - 编号是**变量**（`rep.add(code, …)`）—— 静态拿不到值
    """
    out: list[str] = []
    tree = ast.parse(io.open(REPO / "ldv" / "tests" / "run_tests.py",
                             encoding="utf-8").read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "ok" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.append(node.args[0].value)
    for f in sorted((REPO / "ldv" / "checks").glob("*.py")):
        src = io.open(f, encoding="utf-8").read()
        # ⚠️ `f?` 必须留着：标题可能是 f-string
        for code, title in re.findall(
                r'rep\.add\(\s*"([A-Z]+\d+)"\s*,\s*f?"([^"]*)"', src):
            out.append(f"[{code}] {title}")
    return out


def unresolved() -> list[str]:
    """**抓不到的注册写法** —— 静态拿不到编号的那些。

    ⚠️ 不静默：它们可能正是「零判据」里的某几条。工具报出来，人去看。

    目前两类：

        `rep.add(code, …)`   编号是变量，静态拿不到值
        `ok(f"…")`           标签是 **f-string** ⇒ 里面的编号静态看不见
                             （实测：6 条合成对照第一版就是这么写的，
                              全部没进覆盖率读数 —— 第二次栽在同一个坑）
    """
    bad: list[str] = []
    for f in sorted((REPO / "ldv" / "checks").glob("*.py")):
        src = io.open(f, encoding="utf-8").read()
        for arg in re.findall(r"rep\.add\(\s*([^,]+),", src):
            if not re.match(r'^"[A-Z]+\d+"$', arg.strip()):
                bad.append(f"{f.name}: rep.add({arg.strip()[:20]}, …)")
    tree = ast.parse(io.open(REPO / "ldv" / "tests" / "run_tests.py",
                             encoding="utf-8").read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "ok" and node.args
                and isinstance(node.args[0], ast.JoinedStr)):
            bad.append("run_tests.py: ok(f\"…\") 动态标签")
    return bad


def coverage(labels: list[str] | None = None) -> dict[str, list[str]]:
    """承诺 ⇒ 提到它的判据标签（**只按编号引用**，不做语义匹配）。"""
    labels = judgments() if labels is None else labels
    cov: dict[str, list[str]] = {}
    for cid in commitments():
        pat = re.compile(r"(?<![A-Za-z0-9])§?" + re.escape(cid) + r"(?![0-9])")
        cov[cid] = [s for s in labels if pat.search(s)]
    return cov


def gaps(cov: dict[str, list[str]] | None = None) -> list[str]:
    cov = coverage() if cov is None else cov
    return [c for c, hits in cov.items() if not hits]


#: 「这条判据**自称**带对照」的标记 —— 只认这三种写法
#: （「对照」单独太宽：56 条里有一半是「对照实现」「对照读」这类别的意思）
CONTROL_MARKS = ("反向对照", "合成对照", "已知答案")


def controls(cov: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """承诺 ⇒ **直接**带对照的判据（**读数，不进退出码**）。

    ★★ 为什么只是读数：它读的是**标签里有没有写「对照」**，
       **不是**「那条对照真的会红」。后者只有真去回退源码才知道。
       ⇒ 它可以指出「哪几条只有正着判」，**不能**证明「有对照的那几条就对了」。

    ⚠️ 它**低估**：一条判据带了对照却没在标签里写，这里看不见 ——
       和「判了但没写编号」是同一个毛病，修法也一样（把话说进标签）。

    ★ **B 编码额外看符号**（2026-10-23）：`bN_known_answer` 函数存在 ⇒ 也算有对照。
      理由：B18 的对照函数明明在，只是 `run_tests` 那条标签没写「对照」两个字 ——
      「标签没写」与「对照不存在」是**两件事**，不能混。
    """
    cov = coverage() if cov is None else cov
    out = {cid: [s for s in hits if any(m in s for m in CONTROL_MARKS)]
           for cid, hits in cov.items()}
    for f in sorted((REPO / "ldv").rglob("*.py")):
        for m in re.finditer(r"^def (b\d+)_known_answer\b",
                             io.open(f, encoding="utf-8").read(), re.M):
            cid = m.group(1).upper()
            if cid in out and not out[cid]:
                out[cid] = [f"[{cid}] 合成对照（符号：{m.group(1)}_known_answer）"]
    return out


def landings() -> dict[str, list[str]]:
    r"""承诺 ⇒ 断言集「**系统落点**」一栏里提到的检查编号。

    ★ 为什么用这一栏：`§K/§A/§D/§S` 大多是**承诺**而不是独立代码路径 ——
      它们落在某个检查上（`§K5` 落在 `B13`、`§K8` 落在 `B1`）。
      所以「给 § 造一条对照」在多数情况下**等于给那个检查造**，
      而那个检查可能已经有了 ⇒ 记成「**间接**有对照」，别重复造。

    ⚠️ 只认 `B\d+ / A\d+ / T\d+` 三种编号（`§I` 是接口契约，不在 B 体系里）。
    """
    doc = io.open(_SPEC_DOC, encoding="utf-8").read()
    parts = re.split(r"^### §?([KATDS]\d+)\b", doc, flags=re.M)
    out: dict[str, list[str]] = {}
    for i in range(1, len(parts), 2):
        cid, body = parts[i], parts[i + 1]
        m = re.search(r"系统落点\**：(.*)", body)
        out[cid] = sorted(set(re.findall(r"(?<![A-Za-z])([BAT]\d+)(?![0-9])",
                                         m.group(1) if m else "")))
    return out


def control_status(cov: dict[str, list[str]] | None = None
                   ) -> dict[str, tuple[str, str]]:
    """承诺 ⇒ `(状态, 经谁)`。**三态**：`直接` / `间接` / `缺`。

    ★ 「间接」不是「有」—— 它靠的是**另一个**检查的对照。文档里必须分开数，
      否则读者会把「间接」读成「自己反着判过」。
    """
    cov = coverage() if cov is None else cov
    ctl = controls(cov)
    land = landings()
    out: dict[str, tuple[str, str]] = {}
    for cid in cov:
        if ctl.get(cid):
            out[cid] = ("直接", "；".join(ctl[cid][:1]))
            continue
        via = [c for c in land.get(cid, []) if ctl.get(c)]
        if not via and cid in MANUAL_VIA:
            out[cid] = ("间接", MANUAL_VIA[cid][0] + "（手工映射）")
            continue
        out[cid] = ("间接", "、".join(via)) if via else ("缺", "")
    return out


def table(cov: dict[str, list[str]], status: dict[str, str] | None = None
          ) -> str:
    status = status or {}
    rows = ["| 承诺 | 判据数 | 状态 | 判据（前两条） |", "|---|---|---|---|"]
    for cid, hits in cov.items():
        st = status.get(cid) or ("**零判据**" if not hits else "有判据")
        sample = "；".join(h[:34] for h in hits[:2]) or "—"
        rows.append(f"| `{cid}` | {len(hits)} | {st} | {sample} |")
    n_gap = sum(1 for h in cov.values() if not h)
    rows.append("")
    rows.append(f"合计 **{len(cov)}** 条承诺｜有判据 **{len(cov) - n_gap}**"
                f"｜**零判据 {n_gap}**")
    return "\n".join(rows)


def b_without_control(ctl: dict[str, list[str]] | None = None) -> list[str]:
    """**还没有合成对照的 `B` 判据** —— 这就是「下一步该造哪条反例」的清单。

    ★ 为什么只盯 `B`：`§K/§A/§D/§S` 这些承诺在实现上由 `B` 检查守
      （断言集的「系统落点」一栏写的就是 `Bxx`）⇒ **给 B 造出对照，
      § 那一层就跟着有了**。反过来不成立：只给 § 造，B 仍然没人验。

    ★★ **判定看符号，不看标签**（2026-10-23 修）：第一版只看标签里的
       「合成对照 / 已知答案」，于是把 **B18 误报成「没有对照」** ——
       它的对照函数 `b18_known_answer` 明明在 `coverage.py` 里，
       只是 `run_tests` 那一条的标签没写这几个字。
       ⇒ 「标签没写」和「对照不存在」是**两件事**，这里必须查**函数在不在**。

    ⚠️ 这不等于「剩下的都造得出」：`B8` 要 `make_kernel`、
       `B19` 要一份**连续没变细**的 profile —— 能造，但成本不同。
    """
    ctl = controls() if ctl is None else ctl
    have = {c for c, v in ctl.items() if v}
    # 符号层证据：`def bN_known_answer(...)` 出现在任何检查文件里
    for f in sorted((REPO / "ldv").rglob("*.py")):
        for m in re.finditer(r"^def (b\d+)_known_answer\b",
                             io.open(f, encoding="utf-8").read(), re.M):
            have.add(m.group(1).upper())
    return [f"B{i}" for i in range(1, 21) if f"B{i}" not in have]


#: 还没对照的 `B` ⇒ **怎么造反例 / 卡在哪**（人判，2026-10-23）。
#:
#: ★ 结论先说：**这 10 条都造得出**，只是成本不同 —— 没有一条是「天生造不出」。
#:   （我原以为「有些断言的反例等于改实现」，逐条看下来这个判断不成立：
#:    它们的输入都是**可构造的假插件 / 假 profile / 假 make_kernel**。）
#:
#: ⚠️ 这张表是**人判**，不是读数 —— 判据只守「表里覆盖了所有缺对照的 B」，
#:    不守「你判得对」。
B_CONTROL_PLAN = {
    "B2": ("已造", "假插件 `merge` 丢键"),
    "B3": ("已造", "假插件 `penalty` 抛异常"),
    "B4": ("已造", "子方向重叠 / 漏项未记账"),
    "B5": ("已造", "假插件 `decode` 丢键"),
    "B6": ("已造", "真内核 + 把 None 折成 0 分记录"),
    "B7": ("已造", "非根方向无见证"),
    "B8": ("已造", "`_LeakyKernel`：插入后顺手动路径外方向"),
    "B9": ("已造", "改写一条已记录事件"),
    "B10": ("已造", "真内核 + `query` 返回「否」却带 unexpanded"),
    "B11": ("已造", "真内核 + `show` 只回 1 个方向"),
    "B12": ("已造", "源码里出现 `total_score`"),
    "B13": ("已造", "父链成环 / 一个方向两个父"),
    "B14": ("已造", "自优化侧 import 插件"),
    "B15": ("已造", "`_LeakyKernel`：失效落点依赖插入顺序"),
    "B16": ("已造", "覆盖少给一个成员"),
    "B19": ("已造", "手造 `prof`：连续 10 次没变细"),
}


#: 还没对照的承诺 ⇒ **怎么造 / 卡在哪**（人判，2026-10-23）。
#:
#: ★ 分两类：
#:   「映射缺」  已经有检查覆盖它，只是「系统落点」那栏没写编号 ⇒ **补映射，不另造**
#:   「要造」    真的没有任何对照碰过它
S_CONTROL_PLAN = {
    "K2": ("要造·中", "假插件 `split` 返回两个**相同**子方向 ⇒ 展开没产出新东西"),
    "K7": ("映射缺", "`B11` 已经查 `0 < propensity <= 1` ⇒ 在落点里补 `B11`"),
    "A2": ("要造·中", "假 spec：让视图不稳定（B₁ ⊄ E⁻¹(B₂)）"),
    "A3": ("要造·中", "假 spec：让它**不是最粗**的稳定细化"),
    "D1": ("要造·易", "提议一个**没声明为外生**的字段当维度 ⇒ 必须拒"),
    "D2": ("要造·易", "空谓词 / 全谓词 ⇒ 零贡献（B4 的同一形状）"),
    "D3": ("要造·易", "预算共享：单谓词 + 组合一起排序，总数超 N ⇒ 红"),
    "D4": ("要造·易", "独立性两档：构造 `inter == base`（精确蕴含）的反例"),
    "D5": ("要造·易", "改名不改划分：换一个**同扩展**的键名，划分必须逐字相同"),
    "D6": ("要造·易", "解析范围：`used + dropped` 才算全集，漏掉 dropped ⇒ 红"),
    "D7": ("映射缺", "`[D7]` 判据本身就是对照（加了必须变 / 删了必须回）⇒ 落点补它"),
    "D8": ("要造·易", "候选维度四类：造一个 98% 同值的字段 ⇒ 必须判「几乎唯一」"),
    "S1": ("要造·中", "不产内容：塞一个模型生成的字符串进输出 ⇒ 逐字符串找出处必须红"),
    "S2": ("映射缺", "`[协议] 65` 禁词判据已守 ⇒ 落点补 `[协议] 65`"),
    "S3": ("要造·难", "反思显性：**定位承诺**，能验的只是它的形状（见 §S3 的「只判到半」）"),
    "S4": ("要造·难", "重构显性：同上，只判到「重构有记录」这一半"),
    "S5": ("要造·难", "自我标签一致：同上"),
}


#: **手工补的映射**（人判，2026-10-23）—— 用于「落点那一栏写不成 `Bxx` 编号」的情形。
#:
#: ★ 只补**已经核实过**的：那条判据真的在守这条承诺，且它自己带对照。
#: ⚠️ 这不是「把缺的写成有」——每一条都写了**为什么**，人可以逐条复核；
#:    判据 `[保证]` ⑦ 守「缺的都在 `S_CONTROL_PLAN` 里」，本表只影响 间接 的计数。
MANUAL_VIA = {
    "K7": ("B11", "`B11` 查「倾向完备：0 < propensity <= 1」＋位次敏感 = §K7 的两半"),
    "D7": ("`[D7]` 判据", "`test_derived_rollback` 是**双向**断言：加了必须变、删了必须回"),
    "S2": ("`[协议] 65`", "禁词判据：`score/weight/priority/rating` 一律拒收"),
    "K2": ("B4", "「展开产出两个**相同**子方向」= 子方向重叠 ⇒ `B4` 判红"
                  "（实测：内核不拒这种插件，但 B4 会抓 —— 所以 K2 由 B4 守）"),
}


def load_baseline() -> list[str]:
    if not BASELINE.exists():
        return []
    return json.loads(io.open(BASELINE, encoding="utf-8").read()).get("零判据", [])


def write_baseline(g: list[str]) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    io.open(BASELINE, "w", encoding="utf-8").write(
        json.dumps({"零判据": g,
                    "_note": "冻结的缺口清单。新增缺口 ⇒ 红；缺口被修好却没更新这里 ⇒ 也红。"},
                   ensure_ascii=False, indent=2) + "\n")


def run_status() -> dict[str, str]:
    """跑一遍 `run_tests`，把每条判据的**真实状态**抓回来（`过` / `红` / `跳过`）。

    ⚠️ 跳过在 `run_tests` 里是**记成 PASS 的**（只是文案里带「跳过」）——
       所以必须看文案，不能只看 PASS/FAIL。这正是「跳过 ≠ 通过」那一节的落地。
    """
    import contextlib
    import os

    sys.path.insert(0, str(REPO))
    os.chdir(REPO)                      # 判据依赖 cwd = repo
    from ldv.tests import run_tests as R

    with contextlib.redirect_stdout(io.StringIO()):
        R.main()
    status: dict[str, str] = {}
    for s in R.PASS:
        status[s] = "跳过" if "跳过" in s else "过"
    for s in R.FAIL:
        status[s] = "红"
    return status


DOC = REPO / "docs" / "保证矩阵.md"


def render(cov: dict[str, list[str]] | None = None) -> str:
    """整份 `docs/保证矩阵.md` 的内容（**生成器**，不手写）。

    ⚠️ 不写日期/提交号 —— 那会让每次重生成都变，核验就变成噪声。
    """
    cov = coverage() if cov is None else cov
    g = gaps(cov)
    head = [
        "# 保证矩阵（生成物 · 勿手改）",
        "",
        "> 由 `python -m ldv.tools.guarantee_matrix --write-doc` 生成。",
        "> 判据 `[保证] ④` 会比对「生成出来的」与「磁盘上的」—— **手改会让它红**。",
        "",
        "## 它回答什么",
        "",
        "每条**承诺**（断言集的 §K/§T/§A/§D/§S + 验收的 B1–B20）分别有哪些**判据**在守它。",
        "",
        "    ✓ 有判据   判据标签里**提到了这个编号**",
        "    ✗ 零判据   没有任何判据提到它 —— 见文末「零判据」一节",
        "",
        "## 它**不**回答什么（边界，必须说清）",
        "",
        "它判的是「**有没有判据提到这条承诺**」，**不是**「提到的那条判得对」。",
        "后者只有**反向对照**（把真源码回退、看判据是否红）能回答。",
        "",
        "⚠️ 反过来也不成立：**判了但标签里没写编号 ⇒ 读者查不到 ⇒ 在这里等价于没判**。",
        "这不是工具的毛病，是标签的毛病 —— 要让人查得到，就把编号写进标签。",
        "",
        "## 保证分三层（外部审查的问法；本矩阵只覆盖前两层）",
        "",
        "| 层 | 回答什么 | 本项目 |",
        "|---|---|---|",
        "| 结构正确性 | 数据结构有没有违反自身约束 | **判** —— `§K1–K9` / `B1–B20` |",
        "| 算法正确性 | 计算结果是否符合**独立定义**的算法语义 | **判** —— 增量 ≡ 全量（比答案不比结构）· 持久化往返 + 指纹敏感性 · `§A` · `§D` · `§T` |",
        "| 任务正确性 | 结果是否满足上层使用者的需求 | **不声称** —— 见 README「它不是什么」与 `docs/外生声明标准.md` |",
        "",
        "前两层的「独立定义」在**断言集**里（31 条脱离系统存在，拿纸就能验）；",
        "任务层的基线是**外生声明**（`view_spec.json` 人写）—— 系统不许自己编。",
        "⚠️ 内部一致性全部成立，**不等于**外部语义正确：解析器把 `\"a,b\"` 拆错，",
        "下面每一层都会「正确地」重建、维护、持久化这个错误。拦住它的不是本矩阵，",
        "是外生声明与入站检查。",
        "",
        "## 矩阵",
        "",
        table(cov),
        "",
        "## 零判据（已知缺口，冻结在 `evidence/guarantee_gaps.json`）",
        "",
    ]
    if g:
        head += [f"- `{c}` —— 没有任何判据标签提到它" for c in g]
        head += ["",
                 "⚠️ 这几条**可能是真缺口**，也可能是「判了但标签没写编号」。",
                 "两种情况都要人看一遍：前者要补判据，后者要补标签 —— **两种都不许留在暗处**。"]
    else:
        head.append("（无）")

    ctl = controls(cov)
    n_with = sum(1 for v in ctl.values() if v)
    head += [
        "",
        "## 反向对照覆盖（**读数**，不进退出码）",
        "",
        "「有判据」只说明**正着判过**。要说明判据**会红**，得有一条**反着判**的对照",
        "（回退真源码 / 塞一个已知答案 / 人造一个坏输入）。",
        "",
        f"合计 **{len(cov)}** 条承诺｜**至少一条判据自称带对照 {n_with}**"
        f"｜**只有正着判 {len(cov) - n_with}**",
        "",
        "| 承诺 | 自称带对照的判据 |",
        "|---|---|",
    ]
    for cid, hits in ctl.items():
        head.append(f"| `{cid}` | "
                    f"{'；'.join(h[:30] for h in hits[:2]) if hits else '—'} |")
    head += [
        "",
        "⚠️ **这是声明层的读数**：它读的是「标签里写没写『对照』」，",
        "**不是**「那条对照真的会红」。后者只有真去回退源码才知道 ——",
        "本仓库做过这件事的有 `[动机]` ⑫′/⑬、`[反思]` ㉔、服务壳 ⑯–⑲、`[增量]` ①′、`[D7]`。",
        "",
        "⚠️ 它也**低估**：带了对照却没在标签里写的，这里看不见。",
        "和「判了但没写编号」是同一个毛病 —— **把话说进标签**。",
        "",
        f"⚠️ **低估的量化**：静态索引抓不到的注册写法 **{len(unresolved())}** 条",
        "（`rep.add(code, …)` 变量编号 + `ok(f\"…\")` 动态标签）—— 这些判据的编号",
        "在矩阵里**完全看不见**，所以上面两个数都只会**偏小**。",
        "",
        "### 下一步该造哪条反例",
        "",
        "`§K/§A/§D/§S` 这些承诺在实现上由 `B` 检查守（断言集的「系统落点」一栏写的",
        "就是 `Bxx`）⇒ **给 `B` 造出对照，`§` 那一层就跟着有了**；反过来不成立。",
        "",
        "还没有对照的 `B`：",
        "",
    ]
    _noctl = b_without_control(ctl)
    head += ([f"- `{c}`" for c in _noctl] or ["（无）"])
    if _noctl:
        head += [
            "",
            f"⇒ **{len(_noctl)} / 20** 条还缺。**逐条看下来没有一条是「天生造不出」**，"
            "只是成本不同：",
            "",
            "| B | 能不能造 | 怎么造 / 卡在哪 |",
            "|---|---|---|",
        ]
        for c in _noctl:
            how, why = B_CONTROL_PLAN.get(c, ("未评估", "**表里没有它 —— 补上**"))
            head.append(f"| `{c}` | {how} | {why} |")
        head += [
            "",
            "⚠️ 这张表是**人判**，不是读数。判据只守「表里覆盖了所有缺对照的 `B`」，",
            "不守「判得对」—— 真正的检验是把它造出来、看它红。",
        ]
    else:
        head += [
            "",
            "⇒ **`B1`–`B20` 全部有合成对照**（2026-10-23 补齐）。",
            "⚠️ 但 `§K/§A/§D/§S` 那 25 条仍只算「间接覆盖」——",
            "它们靠 B 的对照**间接**有，不是自己有一条。**间接 ≠ 直接**，别读成「全部反着判过」。",
        ]

    st = control_status(cov)
    from collections import Counter
    n_st = Counter(v[0] for v in st.values())
    head += [
        "",
        "## 三态：直接 / 间接 / 缺",
        "",
        "    **直接**  这条承诺自己的判据里有对照（标签写了「对照」，或 `bN_known_answer` 存在）",
        "    **间接**  它落在**另一个**检查上（断言集的「系统落点」写的 `Bxx`），而那个检查有对照",
        "    **缺**    没有任何对照碰过它 —— 见下面的「怎么造」",
        "",
        f"合计 直接 **{n_st['直接']}**｜间接 **{n_st['间接']}**｜缺 **{n_st['缺']}**",
        "",
        "⚠️ **间接 ≠ 直接**：间接只说明「守它的那条检查反着判过」，",
        "不说明这条承诺**自己的措辞**被反着判过。别把两个数加成一个。",
        "",
    ]
    _miss = [c for c, (k, _v) in st.items() if k == "缺"]
    if _miss:
        head += ["| 缺的承诺 | 怎么造 / 卡在哪 |", "|---|---|"]
        for c in _miss:
            how, why = S_CONTROL_PLAN.get(c, ("未评估", "**表里没有它 —— 补上**"))
            head.append(f"| `{c}` | {how}：{why} |")
        head += [
            "",
            "⚠️ 这张表是**人判**，不是读数。判据 `[保证]` ⑦ 只守「每条承诺都在三态之一、",
            "且缺的都在表里」，不守「判得对」。",
        ]
    return "\n".join(head) + "\n"


def main(argv: list[str]) -> int:
    cov = coverage()
    g = gaps(cov)
    if "--write-doc" in argv:
        DOC.parent.mkdir(parents=True, exist_ok=True)
        io.open(DOC, "w", encoding="utf-8", newline="").write(render(cov))
        print(f"已写 {DOC.name}（承诺 {len(cov)} 条｜零判据 {len(g)}）")
        return 0

    if "--write" in argv:
        write_baseline(g)
        print(f"基线已写：{len(g)} 条零判据 → {BASELINE.name}")
        return 0
    if "--run" in argv:
        st = run_status()
        # 把每条判据的状态归到承诺上：只要有一条判据是「过」就算「判过」
        per: dict[str, str] = {}
        for cid, hits in cov.items():
            states = [st.get(h, "未跑") for h in hits]
            per[cid] = ("**零判据**" if not hits
                        else "过" if "过" in states
                        else "红" if "红" in states
                        else "跳过")
        print(table(cov, per))
        return 0
    if "--check" in argv:
        base = set(load_baseline())
        now = set(g)
        added = sorted(now - base)
        fixed = sorted(base - now)
        print(table(cov))
        print()
        if added:
            print(f"✗ **新增零判据 {len(added)} 条**：{added}")
        if fixed:
            print(f"✗ **缺口被修好但基线没更新 {len(fixed)} 条**：{fixed}"
                  f" —— 跑 `--write` 并说明")
        if not added and not fixed:
            print(f"✓ 与基线一致：零判据 {len(now)} 条（冻结）")
        return 1 if (added or fixed) else 0
    print(table(cov))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
