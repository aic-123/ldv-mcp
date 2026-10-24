"""外部语料（公开数据集切片）接进来之后的**验收**。

    跑法：LDV_CORPUS=openalex-citations python -m ldv.tests.test_intake
    或：  python -m ldv.tests.test_intake          （自动按名字找 `_data/`）
    冻结红基线：python -m ldv.tests.test_intake --write-red-baseline

数据不在 ⇒ **报「跳过」、单独计数**，退出码 0。**跳过 ≠ 通过。**

---

## 这份料要证明什么（**判据先写、再跑** —— 与 `tools/intake_openalex.py` 同一句话）

    ① 两条**互不相干**的数法给出同一组 `(节点数, 图边数)`；
       且 `节点数 >= MIN_NODES`、`图边数 >= 节点数`（内部引用密度 ≥ 1.0）
    ② `run_checks`（**判据全跑、贵的探针不跑**）的红集合 == **本语料上冻结的**基线
       （新增红 ⇒ 失败；**基线条目在本次没红 ⇒ 也失败**，说明基线过期）
       ＋ **解析自检**：抽出的红条数 == 汇总报的红数
       ＋ **不是整趟跳过**（全跳过 ⇒ 红集合必然是空的，那种空什么都没说明）
    ③ `B17` 若报「跳过」，必须同时给出「等价类全是单点」的**实测计数**

## ⚠️ 这里**判的是语料**（与 `run_checks` 判**实现**不同，别混）

本文件是**语料验收**：它问「这份料能不能用」（规模够不够、枚举轴是不是真的多值、
红集合有没有变化）。⇒ 判「语料的结构性质」在这里是**正当**的。
⚠️ 反过来，`run_checks` 里**不许**判语料 —— `§M1` 刚踩过这个坑（拿收缩比当判据，
而那个比值的分子由结构决定、分母由键集决定 ⇒ 系统没有自由度）⇒ 2026-10-10 改判
`Q₀ == csr`。两处的正当性不同，判据的**主语**也不同：这里主语是**料**，那里是**实现**。

## ⚠️ 「判不了」的两种来源（2026-10-10 加）—— 都**报跳过**，不许崩、不许算红

    **路 2（原始 JSON）认不出格式**   SNAP 的原始件是 `.gz` ⇒ 一个 `*.json` 都没有。
      改之前 `count_via_raw` 返回 `(0, 0, …)` ⇒ 判据 ① **必然红**，
      而且红得**指向语料**（看起来像「这份料是空的」），其实指向**本函数认不出的格式**。
    **MANIFEST 认不出枚举轴**        轴名按接料工具而变：OpenAlex 是 `type 取值`、
      Cora 是 `类别取值`、SNAP 是 `标签取值`。改之前**写死了**三个 OpenAlex 键名
      ⇒ 在 SNAP 上直接 `KeyError: 'type 取值'` **崩掉**。

⇒ 两处都改成：**按约定认，认不出就报「判不了（跳过）」**，并逐字印出为什么。
  本文件叫「**外部**语料验收」—— 它**不许**假设所有外部语料都是同一个工具接的。

⚠️ 接料工具自己在 MANIFEST 里记的 `两条数法`（markdown 往返 / 直接读原始文件）
  在路 2 跑不了时**是唯一能看到的旁证** ⇒ 本文件把它**印出来**，
  但**不拿它当判据** —— 那是「工具声称」，不是「本趟独立复算」，
  而两者在只看绿红时**长得一模一样**。

⚠️ **2026-10-07 改**：原来 ② 里还有一条「**基线为空 ⇒ 也失败**」。
   它在当时的语料上是对的，但它让机制**无法表示一份健康语料** ——
   红集合真的空了之后，两条路各得一条**常驻的红**：

       不重冻 ⇒ 「② 没有失效的基线条目」红（缺口被修了却没更新基线）
       重冻成空 ⇒ 「② 基线不是空的」红

   而**一条常驻的红等于没人再看红**。⇒ 把「不许空」换成「**空必须是验出来的**」：
   新增的两条（解析自检 / 不是全跳过）在红集合**空与非空**时**都有内容**，
   而它们合起来正是原来那条想防的东西（**解析器瞎了却报「全过」**）。
   「0 旁边要写它匹配上了多少」—— 这里那个陪衬就是汇总里的**红数**。

拿不准的量（假阳率 / 叶容量 / 细化量 / 耗时）**只披露、不判过**。

⚠️ 全量（3907 项）跑 ② 实测 **48 分 10 秒**（`--no-probes`，`2026-10-07`）——
   那是**整趟**的量级，不是某一条的量级。**它一度跑不完**：`b3` 一块就要
   **109.6 分**（30,521,484 次调用），超过当时 `71 分钟` 的停表；
   插件的缓存键换对之后（见 `ldv/plugins/reach.py`）那一块降到 **38.85 s**，
   整趟才第一次跑完。所以默认只在 ≤ `CHEAP_N` 项上跑，全量要 `LDV_INTAKE_CHECKS=1`。
   小切片上跑时记得同时下调 ① 的门槛：`LDV_INTAKE_MIN_NODES=200`。

---

## ★ 原判据 ② 是「红数为 0」—— **实测被证伪**，而且证伪得有价值

在 281 项子切片上 `run_checks` 报**红 1**：`B17`（`reach`）。
这**不是**新缺陷，是设计文档 `§10 留白` 早就记下的那一条在**真实语料上**露了出来。
设计文档原话（§10 留白表 + §7.2）：

> `reach` 在**合环图**上会把互相可达的两项劈成两个**覆盖完全相同**的子方向。
> **真实语料是 DAG，所以这个缺口在语料上不露**

⇒ 公开数据集**证伪了最后半句**：OpenAlex 的引用图**有环**（论文互引），
   281 项切片里有 **6** 个大小 ≥2 的强连通分量 ⇒ 缺口露了，
   而 `B17` 报的「被拆开的等价类」**正好 6 个**。

⇒ 所以判据 ② 改成**基线守卫**（与 `B18` 同一形状）：

    基线红集合（冻结）  本次红集合
    ────────────────────────────────
    新增的               ⇒ **红**（真出了新问题）
    基线里有、本次没有的  ⇒ **红**（基线过期 —— 缺口被修了却没更新基线）

⚠️ **2026-10-07：原来这里还有第三行「两边都是空的 ⇒ 红（守卫空转）」—— 已删。**
   它当时的意图是对的（防「冻一条空基线让一切通过」），但它**用错了手段**：
   「不许空」让机制**无法表示一份健康语料**。红集合真的清空之后（`B17`(b) 在 `reach`
   上降为度量，见设计文档 `§7.2`），两条路各得一条**常驻的红**：

       不重冻 ⇒ 「没有失效的基线条目」红
       重冻成空 ⇒ 「基线不是空的」红

   而**一条常驻的红等于没人再看红** —— 它训练所有人忽略那一列。
   ⇒ 换成两条**在红集合空与非空时都有内容**的判据（见下），并给它们配**已知答案的对照**。
   原来那条想防的东西（**解析器瞎了却报「全过」**）现在被正面堵住：

       ② 解析自检：抽出的红条数 == 汇总报的红数（`selftest_red_parse` 里三个对照）
       ② 不是整趟跳过（全跳过 ⇒ 红集合必然是空的，那种空什么都没说明）

   「0 旁边要写它匹配上了多少」—— 这里那个陪衬就是汇总里的**红数**。

把「红数为 0」写成判据，等于**要求一条已知的缺口消失**；
而把基线写死不再核对，等于**让缺口悄悄变成常态**。两边都不行。
⚠️ 第三条纪律补上：**也不许把一条常驻的红留在判据列** —— 见上。

⚠️ **基线按语料指纹分开**（与 `B18` 同一条纪律，那个坑刚踩过）：
   `openalex-small` 上 `sequence` 维护路径漏 **183**，而 `B18` 的基线是在 36 项上冻的
   **16** —— 不按语料分开的话，「换了语料」会被报成「新增违规」。

---

## 两条数法为什么算「互不相干」

    路 1  走 **markdown 往返**：`load_nodes()` 读 `nodes/*.md`，`load_edges()` 数边
    路 2  走 **原始 JSON**：直接读 `raw/*.json`（OpenAlex 的原始响应），自己数

两条路**取的源不同**（一份是转换器写出来的文本，一份是数据源本身）。
转换器漏写一个节点、漏写一条边、或者把 `relations` 写成别的形状 ——
两条路立刻对不上。**共用同一个表达式**的数法做不到这件事。

## 耗时：为什么 ② 用 `--no-probes` 而**不用** `--cap`

先分清两类东西（`run_checks` 的模块 docstring 有完整推导）：

    判据（进退出码）   B1–B20。贵的只有 `B15`（3 次整建）与 `B8`（n 次插入）
    探针（只报不判）   十个度量。贵的是 `规范重建`（O(n) 次重建 × 每次 O(n)）

实测（281 项语料）：

    方向       整趟      其中 `规范重建`   `--no-probes` 之后
    ────────────────────────────────────────────────────
    keyset     27.4 s      26.96 s             ~0.4 s
    reach     450.8 s     446.54 s             ~4.3 s
    三个方向合计 ~780 s     ~774 s            **6.05 s**

⇒ 所以 ② 用 `--no-probes`：**判据一条不少地全跑**（`B17` 那条红照样露），
   只把贵的**探针**关掉。**关探针是安全的** —— 度量不进退出码，
   关掉它**不可能把红变成绿**；报告里会写明哪几行未跑。

⚠️ **`--cap` 在这里用不了**，因为它**连判据一起截**：
   实测 `openalex-citations` 的环**全落在第 400 项之后**（前 400 项 SCC ≥2 的组 = **0**），
   所以 `--cap 160` 上 `B17` 那条红**根本不会出现** ——
   而基线若是照那次冻的，就会是空集，守卫**空转**。
   ⇒ 见 `red_baseline_path`：**基线为空就报红**（「空转不许算通过」）。

⚠️ `--cap` 换的是范围，不是结论：本趟没覆盖到的项，本趟什么也没说。
   基线文件里**记着 cap**，换了 cap 必须重冻。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ldv.corpus.loader import load_edges, load_nodes  # noqa: E402

NAME = "openalex-citations"

#: ① 的规模门槛。**全量切片是 3000**；跑**子切片**（`--cocite-k 5` 那种）时按需下调 ——
#: 门槛写在环境变量里而不是写死，是为了让「我这次跑的是子切片」**看得见**。
MIN_NODES = int(os.environ.get("LDV_INTAKE_MIN_NODES", "3000"))

#: ② 在多大的语料上**默认**跑。`--no-probes` 之后，281 项只要 6 s；
#: 但 3907 项上整趟实测 **48 分 10 秒** —— 所以全量要显式打开（`LDV_INTAKE_CHECKS=1`）。
#: ⚠️ **别把整趟归给某一条**：要指名道姓得先逐块计时
#:    （`outputs/_profile_runone_prod.py` —— 它包生产 `run_one`，不抄序列）。
#:    已量到的一块：`b3` 曾经 **109.6 分**（`outputs/_measure_b3_progress.py`）——
#:    它一块就超过 `71 分钟` 的停表，那两轮「没跑完」的原因在此；
#:    缓存键换对之后降到 **38.85 s**，整趟才第一次跑完。
#: ⚠️ 这里**不用** `--cap`：它连判据一起截，会把 `B17` 那条红藏掉（见模块开头）。
CHEAP_N = int(os.environ.get("LDV_INTAKE_CHEAP_N", "400"))

PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []
INFO: list[str] = []


def ok(what: str, cond: bool, why: str = "") -> None:
    (PASS if cond else FAIL).append(what + ("" if cond else f"  ← {why}"))


def corpus_dir() -> Path | None:
    """找外部语料目录。

        `LDV_CORPUS`（**路径或名字**）→ `_data/<名字>/nodes`（仓库旁，再上一级）
        没设 → `_data/<NAME>/nodes`

    ⚠️ 与 `_fixtures.find_corpus` **同一套优先级**：外部语料既可以指路径也可以指名字，
       两边必须一样，否则「测试跑的是哪份料」和「检查跑的是哪份料」会悄悄分叉。
    """
    root = Path(__file__).resolve().parents[2]
    names = [os.environ["LDV_CORPUS"]] if os.environ.get("LDV_CORPUS") else [NAME]
    for name in names:
        p = Path(name)
        if p.is_dir() and any(p.glob("*.md")):
            return p
        for base in (root, root.parent):
            d = base / "_data" / name / "nodes"
            if d.is_dir() and any(d.glob("*.md")):
                return d
    return None


def count_via_markdown(nodes_dir: Path) -> tuple[int, int]:
    """**路 1**：走仓库自己的装载器（markdown 往返）。"""
    nodes = load_nodes(nodes_dir)
    edges, _ = load_edges(nodes)
    return len(nodes), sum(len(v) for v in edges.values())


#: 路 2 认得的唯一形状：`list[{id, referenced_works}]`（OpenAlex 的原始响应）。
#: ⚠️ **认不出就是「判不了」，不是崩、也不是红** —— 见 `count_via_raw`。
RAW_SHAPE_HINT = "`[{id, referenced_works}]`（OpenAlex 原始响应的形状）"


def count_via_raw(raw_dir: Path) -> tuple[int, int, dict] | None | str:
    """**路 2**：直接读原始 JSON，自己数。**不碰 markdown，也不碰 `loader`。**

    三种返回，**三种不同的话**（混起来就是「空转与通过长得一模一样」）：

        `(节点数, 边数, 附加读数)`   数出来了
        `None`                      `raw/` 里**一个 `*.json` 都没有** ⇒ 判不了
        `str`                       **有 JSON，但形状不是** `RAW_SHAPE_HINT`
                                    ⇒ 判不了，**字符串是原因**

    ⚠️ **`None` = 这份语料的原始件不是 JSON ⇒ 路 2 判不了**（2026-10-10 加）。
       三份 SNAP 语料的原始件是 `.gz`（`email-Eu-core.txt.gz` / `cit-HepTh.txt.gz`）
       ⇒ 这里**一个 `*.json` 都找不到** ⇒ 改之前返回 `(0, 0, …)`，
       而 `0` 与「真的没有节点」在只看数字时**长得一模一样** ⇒ 判据 ① 必然红，
       而且红得**指向语料**（看起来像「这份语料是空的」），其实指向**本函数认不出的格式**。

    ⚠️ **`str` = 有 JSON 但形状不认识**（2026-10-11 加）。2026-10-10 那条只堵住了
       「一个 `*.json` 都没有」，**没堵住「有 JSON、形状是另一个工具的」**：实测
       喂 GitHub Search 的响应（`dict`）或 Crossref 的响应（`dict`）会
       **`TypeError: string indices must be integers`** —— 那是**崩**，
       而崩比红更糟（红是判据说话，崩是判据没了：这一条之后的 ①② 全都不跑）。
       本文件叫「**外部**语料验收」，它**不许**假设所有外部语料都是同一个工具接的
       （见模块开头）—— 所以这里把「形状不认识」也收成**判不了**。

    ★ **「判不了」不是「红」** —— 它走 `SKIP`，与 `PASS` 分开计。
      ⚠️ 那也**不是通过**：本模块开头那句「跳过 ≠ 通过」照样管着它，
        所以 `main` 会把它**逐字印出来**并说明「这一条本趟没有内容」。
    """
    files = sorted(raw_dir.glob("*.json"))
    if not files:
        return None
    recs: dict[str, dict] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return (f"① 路 2（原始 JSON）：`{f.name}` **读不成 JSON**"
                    f"（{type(exc).__name__}）⇒ 形状无从判断 ⇒ **判不了**")
        if not isinstance(data, list) or not all(
                isinstance(r, dict) and "id" in r for r in data):
            return (f"① 路 2（原始 JSON）：`{f.name}` 的形状不是 {RAW_SHAPE_HINT}"
                    f"（顶层是 {type(data).__name__}）⇒ 本函数认不出 ⇒ **判不了**。"
                    f"⚠️ 「认不出」不是「这份料是空的」—— 别把它读成 ① 红")
        for r in data:
            recs[str(r["id"]).rsplit("/", 1)[-1]] = r
    ids = set(recs)
    n_edges = 0
    dropped = 0
    for r in recs.values():
        refs = {str(x).rsplit("/", 1)[-1] for x in (r.get("referenced_works") or [])}
        n_edges += len(refs & ids)
        dropped += len(refs) - len(refs & ids)
    return len(recs), n_edges, {"原始 JSON 文件数": len(files), "丢弃外部引用": dropped}


#: MANIFEST 里**枚举轴**的命名约定：接料工具把「某个轴的取值 → 出现次数」写成
#: `<轴名>取值` 这么一个 **dict**。⚠️ **轴名按工具而变**，实测三种：
#:
#:     openalex-*      `type 取值` / `source.kind 取值` / `evidence_status 取值`
#:     cora-labeled    `类别取值`
#:     snap-*          `标签取值`
#:
#: ⇒ 这里**不许写死任何一个**：写死就等于假设「所有语料都是 OpenAlex 接的」，
#:   而本文件的名字正是「**外部**语料验收」。2026-10-10 之前写死了三个 OpenAlex
#:   键名，实测在 SNAP 语料上直接 `KeyError: 'type 取值'` **崩掉**。
AXIS_SUFFIX = "取值"


def manifest_axes(man: dict) -> dict[str, dict]:
    """从 MANIFEST 里**认出**枚举轴：键以 `取值` 结尾、值是 dict。**不猜、不写死。**"""
    return {k: v for k, v in man.items()
            if k.endswith(AXIS_SUFFIX) and isinstance(v, dict)}


def manifest_dropped(man: dict) -> dict[str, int]:
    """接料工具记的「丢弃了多少」——键名**同样**按工具而变 ⇒ 按 `丢弃` 认。"""
    return {k: v for k, v in man.items() if "丢弃" in k and isinstance(v, int)}


def parse_reds(out: str) -> list[str]:
    """从 `run_checks` 的输出里抽**红集合**，形如 `B17|reach`。

    ⚠️ 必须带**方向**：同一条编号在不同方向上可以一红一绿
       （`B17` 在 `keyset` 上绿、在 `reach` 上红）—— 只记编号会把两者混成一个。

    ★★ **编号范围必须覆盖全部判据组，不只是 `B`**（2026-10-10 修）：
       原来只匹配「`B` + 数字」，而输出里还有 `(视图)` 的 `A1`–`A8`、`(L0)` 的 `M0`–`M6`、
       `(检索)` 的 `T1`–`T8`、`(cli)` 的 `CL1`–`CL7`。
       ⇒ 那些组的红**根本进不了 `reds`** ⇒ 下面那条「没有**新增**红（基线守卫）」
         **对它们视而不见** —— 而症状正是「**守卫报绿**」。
       这也解释了「红条数 == 汇总红数」那条自检**为什么必须存在**：
       它一旦不等就说明取数器瞎了（本次实测：`A4`/`M4` 两条红，取数器抽出 0 条）。
    """
    reds: list[str] = []
    plugin = "?"
    for line in out.splitlines():
        m = re.match(r"═══ 插件 (\S+) ═══", line)
        if m:
            plugin = m.group(1)
            continue
        # `CL` 两个字母 ⇒ 用 `[A-Z]+` 而不是 `[A-Z]`
        m = re.match(r"\s*\[红\] ([A-Z]+\d+)\b", line)
        if m:
            reds.append(f"{m.group(1)}|{plugin}")
    return sorted(reds)


def red_baseline_path(nodes_dir: Path) -> Path:
    """红基线**跟着外部语料走**（不进仓库 —— 外部数据不进仓库，它的读数也不进）。"""
    return nodes_dir.parent / "expected_reds.json"


def selftest_red_parse() -> None:
    """**解析器自检** —— 喂**四个**已知答案的合成输出，证明「红条数 == 汇总红数」判得对。

    ⚠️ 为什么必须有这条：`parse_reds` 一旦瞎了（输出格式改了、正则不再匹配），
       它返回 `[]`；而 `[] == 汇总红数 0` **也成立** ⇒ 上面那两条新判据会**一起空转**，
       报出「本语料无红」。**「0 出现得太干净」的解法就是给 0 配一个已知非 0 的对照。**

    ⚠️ 放在 `main()` **最前面**（语料检查之前）—— 语料不在时整趟会提前返回，
       而这条自检与语料无关，不该跟着一起被跳过。
    """
    real = "  [过] B13 良基树 ……\n═══ 汇总 ═══\n  断言 59 条：红 0，跳过 3\n"
    cases = [
        ("真输出（红 0）", real, True),
        ("汇总说红 1、正文没有 `[红]` 行", real.replace("红 0", "红 1"), False),
        ("正文有 `[红]` 行、汇总说红 0", real.replace("  [过] B13", "  [红] B13"), False),
        # ★ **跨组**对照（2026-10-10 加）：原来三个样例全是 `B` 编号 ⇒
        #   取数器漏掉 `A`/`M`/`T`/`CL` 也照样过（那正是它漏了两条红却没报的原因）。
        #   ⇒ 必须有一个样例的红**不在 `B` 组**。
        ("跨组红（`A4`/`M4`/`T7`）",
         "═══ 插件 (视图) ═══\n  [红] A4 视图类别 ……\n"
         "═══ 插件 (L0) ═══\n  [红] M4 每层 ……\n"
         "═══ 插件 (检索) ═══\n  [红] T7 深化 ……\n"
         "═══ 汇总 ═══\n  断言 96 条：红 3，跳过 7\n", True),
    ]
    rows = []
    good = True
    for name, out, want in cases:
        m = re.search(r"断言 (\d+) 条：红 (\d+)，跳过 (\d+)", out)
        n = int(m.group(2)) if m else -1
        hit = (len(parse_reds(out)) == n)
        good = good and (hit == want)
        rows.append(f"{name}：抽出 {len(parse_reds(out))} / 汇总 {n}"
                    f" ⇒ {'一致' if hit else '不一致'}（期望{'一致' if want else '不一致'}）")
    ok("② 解析器自检：红条数 == 汇总红数 —— 在**四个已知答案的对照**上都判对"
       "（含一个**跨组**样例：红在 `A`/`M`/`T` 组里也必须抽得到）",
       good, "；".join(rows))


def run_checks_red(nodes_dir: Path,
                   cap: int = 0) -> tuple[list[str] | None, dict[str, int], str]:
    """**②** 在新语料上真跑一遍流程 C（子进程 —— 验的是**真入口**，不是它的替身）。

    带 `--no-probes`：**判据全跑，贵的探针不跑**（见模块开头「耗时」）。
    探针不进退出码 ⇒ 关掉它不可能把红变成绿。

    ⚠️ 返回值里**必须带上汇总的三个计数**（断言 / 红 / 跳过）。
       只返回红集合时，「一条都没抽到」与「真的没有红」在调用方长得一模一样 ——
       2026-10-07 之前就是这么写的，而它把「红集合为空」当成了错误状态（见 `main` ②）。
    """
    env = dict(os.environ)
    env["LDV_CORPUS"] = str(nodes_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cmd = [sys.executable, "-m", "ldv.run_checks", "--no-probes"]
    if cap > 0:
        cmd += ["--cap", str(cap)]
    r = subprocess.run(cmd, capture_output=True, env=env,
                       cwd=str(Path(__file__).resolve().parents[2]))
    out = r.stdout.decode("utf-8", "replace")
    m = re.search(r"断言 (\d+) 条：红 (\d+)，跳过 (\d+)", out)
    if not m:
        return None, {}, out
    counts = {"断言": int(m.group(1)), "红": int(m.group(2)), "跳过": int(m.group(3))}
    return parse_reds(out), counts, out


def corpus_fingerprint(nodes_dir: Path) -> dict[str, int]:
    """与 `checks/coverage.py: corpus_fingerprint` 同口径 —— 基线**必须按语料分开**。

    同一条红在不同语料上可以是「已知缺口」也可以是「新缺陷」；
    基线不按语料分开，两者就长得一模一样（`B18` 刚踩过这个坑，见 `coverage.py`）。
    """
    nodes = load_nodes(nodes_dir)
    edges, _ = load_edges(nodes)
    return {"项数": len(nodes), "边数": sum(len(v) for v in edges.values())}


def main() -> int:
    print("═══ 外部语料验收：公开数据集切片 ═══")
    selftest_red_parse()
    d = corpus_dir()
    if d is None:
        SKIP.append(f"外部语料 `_data/{NAME}/`（按 `tools/intake_openalex.py` 取）")
        print(f"  ⊘ 跳过：没找到 `_data/{NAME}/nodes`")
        print("     —— **跳过不等于通过**。取法见 ldv/tools/intake_openalex.py")
        print(f"\n═══ 过 {len(PASS)} · 红 {len(FAIL)} · **跳过 {len(SKIP)}** ═══")
        return 0

    print(f"  语料 {d}")
    raw = d.parent / "raw"

    # ── ① 两条独立数法 ────────────────────────────────────────────────────
    n1, e1 = count_via_markdown(d)
    got2 = count_via_raw(raw)
    print(f"  路 1（markdown 往返）：节点 {n1} · 边 {e1}")
    if isinstance(got2, str):
        # ⚠️ **有 JSON 但形状不是本函数认得的那种** ⇒ 判不了（**不是红、不是崩**）。
        #    与下面的 `None`（一个 `*.json` 都没有）**分开报** —— 两种「判不了」的
        #    原因不同，共用一句话就等于没说原因。
        SKIP.append(got2)
        print(f"  ⊘ {got2}")
        print("     ⚠️ 于是「两条独立数法」这一条**本趟没有内容** —— "
              "**别把这一趟读成「① 过了」，也别读成「① 红了」**")
    elif got2 is None:
        # ⚠️ **判不了 ≠ 红，也 ≠ 通过。** 它单独计数（`SKIP`），并且逐字印出**为什么** ——
        #    否则「这一条本趟没有内容」与「这一条过了」在只看最后那行汇总时长得一模一样。
        why2 = (f"① 路 2（原始 JSON）：`{raw}` 里**一个 `*.json` 都没有** ⇒ "
                f"这份语料的原始件不是 JSON（SNAP 是 `.gz`）⇒ **判不了**")
        SKIP.append(why2)
        print(f"  ⊘ {why2}")
        print("     ⚠️ 于是「两条独立数法」这一条**本趟没有内容** —— "
              "**别把这一趟读成「① 过了」**")
    else:
        n2, e2, extra = got2
        print(f"  路 2（原始 JSON）    ：节点 {n2} · 边 {e2}   {extra}")
        ok("① 两条独立数法给出同一个节点数", n1 == n2, f"{n1} vs {n2}")
        ok("① 两条独立数法给出同一个边数", e1 == e2, f"{e1} vs {e2}")
    ok(f"① 节点数 ≥ {MIN_NODES}", n1 >= MIN_NODES,
       f"只有 {n1} —— 门槛写在 `LDV_INTAKE_MIN_NODES` 里"
       f"（跑子切片/别的语料时按需下调，见模块开头）")
    ok("① 图边数 ≥ 节点数（内部引用密度 ≥ 1.0）", e1 >= n1, f"{e1} < {n1}")

    # ── 反空跑自检：先把**各分支的原始计数**摆出来，再谈别的 ────────────────
    mf = d.parent / "MANIFEST.json"
    if not mf.exists():
        SKIP.append(f"① MANIFEST（{mf} 不在）—— 枚举轴与「丢弃」计数**没得看**（跳过 ≠ 通过）")
        print(f"  ⊘ 跳过：{mf} 不在 ⇒ 枚举轴无从核对")
    else:
        man = json.loads(mf.read_text(encoding="utf-8"))
        axes = manifest_axes(man)
        dropped = manifest_dropped(man)
        # ⚠️ 枚举轴**按约定认**（键以 `取值` 结尾、值是 dict），**不写死键名** —— 见 `AXIS_SUFFIX`。
        print("  MANIFEST：枚举轴 "
              + ("、".join(f"`{k}` {len(v)} 值" for k, v in axes.items()) if axes else "**0 条**")
              + ("｜丢弃计数 " + "、".join(f"`{k}` {v}" for k, v in dropped.items())
                 if dropped else ""))
        if not axes:
            SKIP.append(f"① MANIFEST 里**一条枚举轴都没有**（约定：键以 `{AXIS_SUFFIX}` 结尾、"
                        f"值是 dict）—— 这份 MANIFEST 的键是 {sorted(man)} ⇒ **判不了**"
                        f"（跳过 ≠ 通过）")
            print("     ⊘ 认不出枚举轴 ⇒ 这一条**判不了**（不是红，也不是通过）")
        for k, v in axes.items():
            ok(f"① 枚举轴 `{k}` 至少两个取值（一个取值的轴不是轴，是噪声）",
               len(v) >= 2, f"{v}")
        # ★ 接料工具自己记的**两条数法** —— 路 2 跑不了时它是**唯一**能看到的旁证。
        #   ⚠️ 但它是**工具自己写的**（不是本趟独立复算）⇒ 只作**读数印出来**，
        #       **不拿它当判据**：拿它当判据等于让「工具声称」冒充「独立复算」，
        #       而两者在只看绿红时长得一模一样（本仓库记过四次的形状）。
        two = man.get("两条数法")
        if isinstance(two, dict):
            print(f"     读数（**工具自己记的**，不是本趟独立复算）："
                  f"{json.dumps(two, ensure_ascii=False)}"
                  f"｜两条数法一致 {man.get('两条数法一致')}")
            INFO.append(f"接料工具自记的两条数法：{two}｜一致 {man.get('两条数法一致')}")

    # ── ③ 键集方向的**分辨率下界**（只披露，不判过）──────────────────────
    nodes = load_nodes(d)
    classes = {n.keys for n in nodes.values()}
    biggest = max(sum(1 for m in nodes.values() if m.keys == k) for k in classes)
    INFO.append(f"键词表 {len(set().union(*(n.keys for n in nodes.values())))} · "
                f"等价类 {len(classes)} · 最大等价类 **{biggest}**"
                f"（占语料 {biggest / len(nodes):.1%}）")
    print(f"  {INFO[-1]}")
    print("     ⚠️ 这是**披露**：它说明这个键词表在这份语料上最多只能分到这一层。")

    # ── ② `run_checks` 红集合 vs 冻结基线 ─────────────────────────────────
    if not (os.environ.get("LDV_INTAKE_CHECKS") == "1" or n1 <= CHEAP_N):
        SKIP.append(f"② `run_checks` 红集合（{n1} 项 —— 整趟实测 48 分 10 秒；"
                    f"`LDV_INTAKE_CHECKS=1` 打开）")
        print(f"  ⊘ 跳过：② 在 {n1} 项上整趟实测 48 分 10 秒"
              f"（曾经跑不完：光 `b3` 一块 109.6 分；281 项上 6.05 s），"
              f"`LDV_INTAKE_CHECKS=1` 打开")
    else:
        print("  跑 `run_checks --no-probes`（判据全跑，贵的探针不跑）…", flush=True)
        reds, counts, out = run_checks_red(d)
        if reds is None:
            ok("② `run_checks` 跑得出汇总", False, out[-400:])
        else:
            for line in out.splitlines():
                if line.startswith("  [红]") or line.startswith("  [跳]") \
                        or line.startswith("  断言") or "跳过的是" in line:
                    print("    " + line.strip())
            print(f"    红集合：{reds or '（空）'}")

            # ★ 解析自检 —— **「红 0」必须是被解析出来的，不是解析器瞎了。**
            #   2026-10-07 之前这里只有一条「基线不是空的」，它把**空基线**当成错误状态，
            #   于是机制**无法表示一份健康语料**（要么留一条失效的旧基线，要么冻一条空基线，
            #   两条路各得一条**常驻的红**）。而「一条常驻的红等于没人再看红」。
            #   ⇒ 改成：**空不空无所谓，但空必须是**验出来的**。
            ok("② 解析自检：抽出的红条数 == 汇总报的红数（红 0 时同样要过这一关）",
               len(reds) == counts["红"],
               f"抽出 {len(reds)} 条、汇总说 {counts['红']} 条 —— 对不上是**解析器瞎了**，"
               f"不是真没有红")
            ok("② 不是整趟跳过（全跳过 ⇒ 红集合必然是空的，那种空什么都没说明）",
               counts["跳过"] < counts["断言"],
               f"断言 {counts['断言']} 条里跳过 {counts['跳过']} 条")
            if not reds:
                INFO.append(f"② 本语料当前**无红**（汇总：红 {counts['红']}、"
                            f"跳过 {counts['跳过']}）—— 空基线**合法**，"
                            f"前提是上面那条解析自检过了")

            fp = corpus_fingerprint(d)
            bp = red_baseline_path(d)
            if "--write-red-baseline" in sys.argv:
                bp.write_text(json.dumps(
                    {"语料": fp, "cap": 0, "no_probes": True, "reds": reds},
                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"    已冻结红基线 → {bp}")
                INFO.append(f"红基线已写入 {bp}（{fp}，{len(reds)} 条红）")
            elif not bp.exists():
                ok("② 红基线存在（先跑一次 `--write-red-baseline`）", False,
                   f"{bp} 不存在 —— 没有基线就没有「新增」这个概念")
            else:
                base = json.loads(bp.read_text(encoding="utf-8"))
                if base.get("语料") != fp:
                    ok("② 基线是**本语料**上冻的（换了语料必须重冻）", False,
                       f"基线 {base.get('语料')} vs 现在 {fp}")
                else:
                    new = sorted(set(reds) - set(base["reds"]))
                    gone = sorted(set(base["reds"]) - set(reds))
                    ok("② 没有**新增**红（基线守卫）", not new, f"新增 {new}")
                    ok("② 没有**失效**的基线条目（缺口被修了却没更新基线）",
                       not gone, f"基线里有、本次没红：{gone}")

        # ③ `B17` 若跳过，必须给出实测计数
        if "跳过的是" in out:
            ok("③ `B17` 报「跳过」时，同一次输出里有「等价类全是单点」的实测计数",
               ("单点" in out) or ("等价类" in out),
               "报了跳过但没说为什么 —— 「跳过」与「通过」就分不开了")

    print(f"\n═══ 过 {len(PASS)} · 红 {len(FAIL)} · **跳过 {len(SKIP)}** ═══")
    for f in FAIL:
        print(f"  ✗ {f}")
    for s in SKIP:
        print(f"  ⊘ {s}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
