# -*- coding: utf-8 -*-
"""谓词推导 —— **从材料算参数，规则由人声明**（`docs/多层涌现-模块化边界.md` §5）。

## 这件事在边界内的哪一侧

    §5 的表：  什么算一个方向        → 外生，必须人声明
               层内局部权衡 / 收益   → 内生，可自我优化

本模块做的是**中间那层**：

    规则（外生，人写）  「按 out_degree 的切分点分档：零值单列 + 非零三分」
    参数（内生，算出来）切分点 = [0, P33=2, P67=5]   ← 从这份材料算，可复现
    谓词（确定）        `出度>0` / `出度>2` / `出度>5` —— 三个**二分**谓词

⇒ **规则没变，值从材料来**。同一份材料 ⇒ 分位数唯一 ⇒ 谓词唯一 ⇒ 划分唯一。

## ★ 为什么谓词必须是「二分」而不是「档位标签」

第一版按「档位标签」产出（`出度档=低` / `=中` / `=高`），**错了**：

- 同一条规则的两个档是**互斥**的（一个节点不可能同时在「低」和「中」）
- 预算截断按区分度挑，很可能**同时选中两个互斥档** ⇒ 组合起来是空方向
  （`§K2` 判空会报红，但报的是症状不是病）

改成**二分切分点**（`出度>2`）后：任意两个谓词可组合（合取有意义），
零值单列 = 切分点 0（`出度>0`）。这也是 FCA 里「布尔属性」的正确形状。

## 为什么参数不能由系统自己调

「让 B19 不报红的最大预算」这种自适应 N 会让 **N 依赖历史** ⇒ 外生项自我优化 ⇒
同材料不同历史得到不同结构。⇒ 预算 `N` **外生定死**，超了如实报「预算用尽」。

## 预算截断在哪一层

在**谓词清单层**截断（按区分度降序取前 N 个），**不是**在每个节点的键上截断 ——
这样 `keyset` 插件一行都不用改（它只读 `item["keys"]`）。

⚠️ 「区分度」**只用于排序**，不用于判断好坏（`docs/评估标准.md` 的「不作分」）——
排序选 N 个是合法的；一旦变成「区分度低于 X 的删掉」就是系统在当裁判。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

#: 谓词预算（**外生定死**，2026-10-17 用户拍板 N=3）
#: N 个二分谓词 ⇒ 子方向上界 2^N = 8
BUDGET = 3

#: 组合谓词的**复杂度阈值**（外生，2026-10-17 用户拍板 K=2）
#: 复杂度 = 合取项数；`复杂度 > K`（即 **3 项及以上**）⇒ 进「待命名」
#: ⚠️ 为什么是「报给人命名」而不是「自动起名」：命名是**定义基础词汇**，
#:    属于外生项（`§5`：什么算一个方向必须人声明）。系统只负责发现
#:    「这个组合太复杂、人该给它起个名」。
COMPLEXITY_K = 2


# ── 规则声明（外生）──────────────────────────────────────────────────
@dataclass(frozen=True)
class Rule:
    """一条**人声明**的谓词规则：按哪个量、怎么取切分点。"""

    名: str            # 「出度档」
    量: str            # 「out_degree」/「in_degree」
    分档: str = "零值单列+非零三分"

    def to_json(self) -> dict[str, Any]:
        return {"名": self.名, "量": self.量, "分档": self.分档}


#: 默认规则集（两条：出度、入度）—— 只做**数值分布**这一类来源
DEFAULT_RULES: tuple[Rule, ...] = (
    Rule(名="出度档", 量="out_degree"),
    Rule(名="入度档", 量="in_degree"),
)


@dataclass(frozen=True)
class Predicate:
    """一个**推导出来**的谓词（二分；可审计：能被点名）。"""

    谓词: str                # 「出度>2」
    规则: str                # 「出度档」
    量: str                  # 「out_degree」
    阈值: float              # 2.0
    满足数: int              # 有多少节点满足 `量 > 阈值`
    区分度: float            # min(满足, 不满足) / 总数 —— **只用于排序**

    def to_json(self) -> dict[str, Any]:
        return {"谓词": self.谓词, "规则": self.规则, "量": self.量,
                "阈值": self.阈值, "满足数": self.满足数,
                "区分度": round(self.区分度, 6)}


# ── 量的计算（从结构算，不从 front matter 读）────────────────────────
def measure(nodes: dict[str, Any], edges: dict[str, frozenset[str]],
            which: str) -> dict[str, float]:
    """算每个节点的一个数值量。目前只支持出度 / 入度。

    ⚠️ 这两个量**必须从结构算**（`edges`），不能从 front matter 读 ——
       语料里的 `type: 出度0` 那类是**摄入时手写死的**，正是本模块要取代的东西。
    """
    if which == "out_degree":
        return {nid: float(len(edges.get(nid, frozenset()))) for nid in nodes}
    if which == "in_degree":
        indeg: dict[str, int] = {nid: 0 for nid in nodes}
        for _src, outs in edges.items():
            for dst in outs:
                if dst in indeg:
                    indeg[dst] += 1
        return {nid: float(v) for nid, v in indeg.items()}
    raise ValueError(f"不支持的量：{which!r}（只支持 out_degree / in_degree）")


def _quantile_nearest_rank(sorted_vals: Sequence[float], k: int, q: int) -> float:
    """最近秩分位数（**确定性**，不依赖插值实现）。

    k/q 处：取第 `ceil(k/q * n)` 个（1-based），即下标 `ceil(...) - 1`。
    """
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    rank = max(1, math.ceil(k / q * n))
    return sorted_vals[min(rank, n) - 1]


def cut_points(vals: dict[str, float]) -> tuple[list[float], dict[str, Any]]:
    """「零值单列 + 非零三分」⇒ **切分点** + 参数。

    切分点 = `[0] + [P33, P67]`（非零部分），每个切分点产出一个二分谓词
    `量 > 切分点` ⇒ 4 个区间（=0 / (0,P33] / (P33,P67] / (P67,∞)）。

    ⚠️ 为什么不能纯四分位：出度这类量**零值常占大头**（实测 30%–60%），
       P25 会等于 0 ⇒ 切分点退化（两个相同）⇒ 去掉重复，如实记。
    """
    zero_n = sum(1 for v in vals.values() if v == 0)
    nz = sorted(v for v in vals.values() if v > 0)
    params: dict[str, Any] = {"零值数": zero_n, "非零数": len(nz), "切分点": [0.0]}
    cuts = [0.0]
    if not nz:
        params["退化"] = "全零 —— 只有切分点 0"
        return cuts, params
    p33 = _quantile_nearest_rank(nz, 1, 3)
    p67 = _quantile_nearest_rank(nz, 2, 3)
    params.update({"P33": p33, "P67": p67})
    for c in (p33, p67):
        if c in cuts:                     # 切分点重合 ⇒ 去重，如实记
            params.setdefault("退化切分点", []).append(c)
            continue
        cuts.append(c)
    params["切分点"] = cuts
    return cuts, params


# ── 推导 ─────────────────────────────────────────────────────────────
def derive(nodes: dict[str, Any], edges: dict[str, frozenset[str]],
           rules: Iterable[Rule] = DEFAULT_RULES,
           budget: int = BUDGET) -> tuple[list[Predicate], list[Predicate], dict[str, Any]]:
    """推导谓词。返回 `(采用的, 超预算的, 参数)`（**兼容旧签名**）。

    内部走 `derive_buffered`（候选 / 采用 / 拒收三段）。
    """
    _cand, used, _rej, params = derive_buffered(nodes, edges, rules, budget)
    # 超预算的 = 候选里既没采用也没拒收的
    used_set = {p.谓词 for p in used}
    rej_set = {x["谓词"] for x in _rej}
    dropped = [p for p in _cand if p.谓词 not in used_set and p.谓词 not in rej_set]
    return used, dropped, params


def derive_buffered(nodes: dict[str, Any], edges: dict[str, frozenset[str]],
                    rules: Iterable[Rule] = DEFAULT_RULES,
                    budget: int = BUDGET
                    ) -> tuple[list[Predicate], list[Predicate], list[dict[str, Any]],
                               dict[str, Any]]:
    """★ 带**缓冲区**的推导（2026-10-17）。

    三段（`predicates.json` 里各一段，可审计）：

        候选  所有推导出来的谓词（不筛）
        采用  过判据 + 预算的（**进划分**）
        拒收  判据不过的 + **理由**（留痕，不静默丢弃）

    ### 提交判据（**自动筛选**，可复现）

        ① **真二分**：`0 < 满足数 < 总数` —— 切出两个非空子集
           （`满足数 == 0` ⇒ 没有节点满足，谓词是空的；`== 总数` ⇒ 一个都切不开）
           ★ 这条同时是 `§K2 判空` 与 `B19 变细守卫` 在**谓词层**的前置检查：
             切不出两个非空子集，就谈不上「变细」
        ② **预算**：过 ① 的按区分度降序取前 `budget` 个（并列按谓词名）
           ⚠️ 区分度**只排序**，不当阈值 —— 那是「不作分」红线的要求

    ⇒ 「候选 → 采用」这一步是**判据驱动**的，不是「推导出来就用」。
    """
    total = len(nodes)
    cand: list[Predicate] = []
    rejected: list[dict[str, Any]] = []
    params: dict[str, Any] = {"预算": budget, "规则": []}

    for rule in rules:
        vals = measure(nodes, edges, rule.量)
        cuts, p = cut_points(vals)
        params["规则"].append({"名": rule.名, "量": rule.量, "分档": rule.分档, **p})
        for c in cuts:
            hit = sum(1 for v in vals.values() if v > c)
            pred = Predicate(谓词=f"{rule.名}>{c:g}", 规则=rule.名, 量=rule.量,
                             阈值=c, 满足数=hit,
                             区分度=(min(hit, total - hit) / total) if total else 0.0)
            cand.append(pred)
            # ① 真二分（谓词层的 §K2 / B19 前置检查）
            if total == 0 or hit == 0 or hit == total:
                rejected.append({
                    "谓词": pred.谓词, "满足数": hit, "总数": total,
                    "理由": ("没有节点满足（空谓词）" if hit == 0
                             else "所有节点都满足（切不开）"),
                })

    rej_set = {x["谓词"] for x in rejected}
    usable = sorted([p for p in cand if p.谓词 not in rej_set],
                    key=lambda p: (-p.区分度, p.谓词))
    used = usable[:budget]
    return cand, used, rejected, params


# ── 组合谓词（2026-10-17）────────────────────────────────────────────
@dataclass(frozen=True)
class Combination:
    """一个**组合谓词**（若干单谓词的合取）。

    ⚠️ **2026-10-20 起它进划分**（用户拍板：共用预算）。`复杂度 <= K` 的组合
       与单谓词一起按区分度排序、抢同一份预算；`复杂度 > K` 的进「待命名」
       （**提示**，不阻塞 —— 人想改名再改，键名默认就是表达式）。
       进划分的只有单谓词（预算内）与**人命名后的基础谓词**。
       理由：预算 N 管的是「进划分的谓词数」；组合也进划分会让预算失控，
       而且会加剧细化（用户 2026-10-17 明确担心过）。
    """

    项: tuple[str, ...]          # ("出度档>2", "入度档>5")
    满足数: int
    区分度: float

    @property
    def 复杂度(self) -> int:
        return len(self.项)

    @property
    def 谓词(self) -> str:
        return " ∧ ".join(self.项)

    def to_json(self) -> dict[str, Any]:
        return {"谓词": self.谓词, "项": list(self.项), "复杂度": self.复杂度,
                "满足数": self.满足数, "区分度": round(self.区分度, 6)}


#: **组合谓词的独立性阈值**（外生，2026-10-20）——
#: `|A∩B| / min(|A|,|B|) ≥ 0.9` ⇒ 两项**近似蕴含** ⇒ 组合与单项几乎同一个切分。
#: ⚠️ 这是**取舍**不是定理：0.9 这个数人给的，与 `LEAF_CAP` / `DEFAULT_N` 同性质。
#: 精确蕴含（交集 == 最小项）不靠它 —— 那条是**定理**，与阈值无关。
INDEPENDENCE_RATIO = 0.9


def combination_verdict(inter: int, base: int,
                        ratio_threshold: float = INDEPENDENCE_RATIO) -> str | None:
    """判断一个组合是否**该拒收**（冗余）。返回理由字符串，或 `None`（可留）。

    ## 两档，性质不同

        ① **精确蕴含**：`交集 == 最小项` ⇒ 一项 ⊆ 其余项 ⇒ `A∧B ≡ A`（**定理**）
        ② **近似蕴含**：`交集/最小项 ≥ 阈值` ⇒ 几乎同一个切分（**外生取舍**）

    ⚠️ 为什么必须挡：组合占的是**同一份预算**（共用 N）。一个与单项等价的组合
       赢了预算 ⇒ **顶掉一个真的能切开的单谓词**，而结构一点没变
       （实测 bitcoin：`入度档>1 ∧ 出度档>1` 0.948，赢了预算，方向数/叶容量都没动）。

    ⚠️ 阈值**只用于拒收，不用于打分**（`docs/评估标准.md` 的「不作分」）：
       输出只有「拒收 + 理由」与「留」两种，没有分数。
    """
    if base <= 0:
        return None
    if inter >= base:
        return "**精确蕴含**（交集 == 最小项）⇒ 组合与单项等价，切分完全相同"
    ratio = inter / base
    if ratio >= ratio_threshold:
        return (f"**跨量强相关**（交集/最小项 = {ratio:.3f} ≥ 阈值 {ratio_threshold}）"
                f"—— 一项几乎蕴含另一项，切分与单项几乎相同")
    return None


def combine(nodes: dict[str, Any], edges: dict[str, frozenset[str]],
            used: Sequence[Predicate],
            k: int = COMPLEXITY_K) -> tuple[list[Combination], list[Combination],
                                            list[dict[str, Any]]]:
    """从**已采用的单谓词**做合取，返回 `(待命名, 组合候选, 拒收)`。

    - **待命名**：`复杂度 > k`（3 项及以上）—— 报给人**提示**（不阻塞）
    - **组合候选**：`复杂度 <= k` —— 可读、不需要命名（**进划分，与单谓词抢预算**）
    - **拒收**：切不开的（满足数 0 或 = 总数）—— 与单谓词同一条判据

    ⚠️ 组合只从 `used`（采用的）里取 —— 不从被拒的里取（那些本身就切不开）。
    """
    from itertools import combinations

    if len(used) < 2:
        return [], [], []
    # 每个单谓词 → 满足它的节点集
    vals = {w: measure(nodes, edges, w) for w in {p.量 for p in used}}
    sat = {p.谓词: {nid for nid in nodes if vals[p.量][nid] > p.阈值} for p in used}
    spec_w = {p.谓词: p.量 for p in used}      # 项 → 量（判「同量冗余」用）
    total = len(nodes)
    to_name: list[Combination] = []
    candidates: list[Combination] = []
    rejected: list[dict[str, Any]] = []

    for size in range(2, len(used) + 1):
        for terms in combinations(sorted(sat), size):
            # ⚠️ **同量冗余**：`出度>0 ∧ 出度>2` —— 后者蕴含前者 ⇒ 组合退化成单项。
            #    这类不是「组合」，必须滤掉（否则待命名里全是假组合）。
            same = {}
            for t in terms:
                w = spec_w[t]
                same.setdefault(w, []).append(t)
            if any(len(v) > 1 for v in same.values()):
                dup = next(v for v in same.values() if len(v) > 1)
                rejected.append({"谓词": " ∧ ".join(terms), "满足数": -1, "总数": total,
                                 "理由": f"同量冗余（{' 与 '.join(dup)} 互相蕴含）"})
                continue
            hit = set.intersection(*[sat[t] for t in terms])
            n = len(hit)
            # ★ **独立性检查**（2026-10-20）：与单项等价的组合会白占预算。
            _verdict = combination_verdict(n, min(len(sat[t]) for t in terms))
            if _verdict:
                rejected.append({"谓词": " ∧ ".join(terms), "满足数": n,
                                 "总数": total, "理由": _verdict})
                continue
            c = Combination(项=tuple(terms), 满足数=n,
                            区分度=(min(n, total - n) / total) if total else 0.0)
            if total == 0 or n == 0 or n == total:
                rejected.append({"谓词": c.谓词, "满足数": n, "总数": total,
                                 "理由": ("没有节点满足（空谓词）" if n == 0
                                          else "所有节点都满足（切不开）")})
            elif c.复杂度 > k:
                to_name.append(c)
            else:
                candidates.append(c)
    return to_name, candidates, rejected


def choose_partition(used: Sequence[Predicate],
                     combos: Sequence[Combination],
                     budget: int = BUDGET
                     ) -> tuple[list[Predicate], list[Combination], list[Any]]:
    """从「单谓词 + 组合谓词」里挑**进划分**的那几个 —— **共用预算**（2026-10-20）。

    规则只有一条：**按区分度降序取前 `budget` 个**（同分按谓词名，保证可复现）。

    ⚠️ 「共用」的含义是**竞争**，不是各给一份：
       组合赢了就**顶掉**一个单谓词 ⇒ 进划分的**总数恒 ≤ budget**。
       （实测：mini / bitcoin 上组合赢；默认 / pypi / wiki 上输。）
    ⚠️ 区分度**只用于排序**，不用于判断好坏（`docs/评估标准.md` 的「不作分」）。
    """
    pool: list[tuple[float, int, str, Any]] = (
        [(p.区分度, 0, p.谓词, p) for p in used]
        + [(c.区分度, 1, c.谓词, c) for c in combos])
    pool.sort(key=lambda x: (-x[0], x[2]))
    chosen = pool[:max(0, budget)]
    singles = [x for _d, k, _s, x in chosen if k == 0]
    picked = [x for _d, k, _s, x in chosen if k == 1]
    return singles, picked, [x for _d, _k, _s, x in pool[max(0, budget):]]


def apply_combinations(nodes: dict[str, Any], edges: dict[str, frozenset[str]],
                       combos: Sequence[Combination],
                       singles: Sequence[Predicate]) -> int:
    """把**组合谓词**追加进 `keys`（键名 = 合取表达式）—— 2026-10-20。

    ## 为什么键名就是表达式

    键必须是**字符串**，而组合谓词要进划分就得有个表示。合取表达式本身就是
    可读的（`出度档>2 ∧ 入度档>5`）⇒ **自动命名**，人想改成「活跃作者」再改，
    **不阻塞**。这是用户 2026-10-20 的裁定：验证过就该能用，不等命名。

    ## 与 `apply_to_nodes` 的关系

    同一个量只算一次（`measure` 会遍历全图）；组合的满足集 = 各项满足集的**交集**，
    复用同一份 `vals` ⇒ 不额外遍历。

    ⚠️ `singles` 传**全部已采用的单谓词**（`used`），不是「进了预算的那几个」——
       组合的项可能引用没进预算的单谓词（它仍是组合的一项）。
    """
    if not combos:
        return 0
    vals = {w: measure(nodes, edges, w) for w in {p.量 for p in singles}}
    sat = {p.谓词: {nid for nid in nodes if vals[p.量][nid] > p.阈值}
           for p in singles}
    touched = 0
    for c in combos:
        if not c.项:
            continue
        try:
            hit = set.intersection(*[sat[term] for term in c.项])
        except KeyError:
            continue                      # 项不在 `singles` 里 ⇒ 跳过（不猜）
        for nid in hit:
            node = nodes[nid]
            node.keys = frozenset(set(node.keys) | {c.谓词})
            touched += 1
    return touched


def apply_to_nodes(nodes: dict[str, Any], edges: dict[str, frozenset[str]],
                   preds: Sequence[Predicate]) -> int:
    """把谓词追加进每个节点的 `keys`（**就地**改 `Node.keys`）。

    ⚠️ `keyset` 插件只读 `item["keys"]`（来自 `Node.keys`）⇒ 追加即生效，
       **插件一行都不用改**。这也是为什么预算要在**清单层**截断。
    ⚠️ 每个量**只算一次**（入度要遍历全图 —— 逐节点算会退化成 O(n·m)）。
    """
    if not preds:
        return 0
    needed = {p.量 for p in preds}
    vals = {w: measure(nodes, edges, w) for w in needed}
    touched = 0
    for nid, node in nodes.items():
        hit = {p.谓词 for p in preds if vals[p.量][nid] > p.阈值}
        if hit:
            node.keys = frozenset(set(node.keys) | hit)
            touched += 1
    return touched


# ── 注册（可审计的显式清单）──────────────────────────────────────────
def write_registry(corpus_dir: Path, corpus: str, cand: Sequence[Predicate],
                   used: Sequence[Predicate], rejected: Sequence[dict[str, Any]],
                   params: dict[str, Any],
                   to_name: Sequence[Combination] = (),
                   combos_cand: Sequence[Combination] = (),
                   combos_rej: Sequence[dict[str, Any]] = ()) -> Path:
    """把**七段**写进 `predicates.json`（缓冲区 + 显式清单）。

        候选      所有推导出来的单谓词（不筛）
        采用      过判据 + 预算的（**进划分**）
        超预算    过判据、但排在预算外的
        拒收      判据不过的 + **理由**（留痕，不静默丢弃）
        待命名    组合谓词，复杂度 > K ⇒ **报给人命名**
        组合候选  组合谓词，复杂度 <= K（可读，**仍不进划分**）
        基础谓词  **人手写**的名字 + 合取定义（外生）—— 本函数**保留原样**

    ★ `基础谓词` 是**人写的**：本函数重写文件时必须原样保留，不能覆盖。
      （程序只生成「候选」，命名权在人 —— `§5` 的外生边界。）
    """
    path = Path(corpus_dir) / "predicates.json"
    # ⚠️ 先读旧文件，把**人手写**的段保住
    manual: dict[str, Any] = {}
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            manual = {k: old[k] for k in ("基础谓词",) if k in old}
        except Exception:
            manual = {}
    used_set = {p.谓词 for p in used}
    rej_set = {x["谓词"] for x in rejected}
    dropped = [p for p in cand if p.谓词 not in used_set and p.谓词 not in rej_set]
    doc = {
        "_note": [
            "谓词推导的**外生声明 + 内生参数 + 缓冲区**（`docs/多层涌现-模块化边界.md` §5）。",
            "",
            "规则（人写）：按 out_degree / in_degree 取切分点，零值单列 + 非零三分。",
            "参数（算出来）：切分点 [0, P33, P67] —— 从这份材料算，同一份材料唯一。",
            "谓词（确定）：`量 > 切分点` 的**二分**谓词 ⇒ 可组合、可复现。",
            "",
            "── 单谓词三段（`候选` → `采用` 由判据自动筛选，可复现）──",
            "`候选`    所有推导出来的谓词（不筛）",
            "`采用`    过判据 + 预算的（**只有这一段进划分**）",
            "`超预算`  过判据、但排在预算外的",
            "`拒收`    判据不过的 + 理由（切不出两个非空子集）",
            "",
            "── 组合谓词两段（**都不进划分**）──",
            "`待命名`   复杂度 > K（默认 2 ⇒ 三项及以上）⇒ **请人给它起个名**",
            "`组合候选` 复杂度 <= K —— 可读，不需要命名，但仍不进划分",
            "",
            "── 基础谓词（**人写**）──",
            "`基础谓词` 人命名后的名字 + 合取定义（外生）。本段由**人**维护，",
            "           程序重写本文件时**原样保留**。命名后它才作为键进划分。",
            "",
            "⚠️ 预算 N 是**外生定死**的（不随材料自适应 —— 那会让 N 依赖历史）。",
            "⚠️ 区分度**只排序**，不当阈值（`docs/评估标准.md` 的「不作分」）。",
            "⚠️ 本文件由 `ldv/corpus/predicates.py` 生成；规则改了要重跑。",
        ],
        "语料": corpus,
        "预算": params.get("预算"),
        "复杂度阈值": COMPLEXITY_K,
        "参数": params,
        "候选": [p.to_json() for p in cand],
        "采用": [p.to_json() for p in used],
        "超预算": [p.to_json() for p in dropped],
        "拒收": list(rejected),
        "待命名": [c.to_json() for c in to_name],
        "组合候选": [c.to_json() for c in combos_cand],
        "组合拒收": list(combos_rej),
    }
    if manual:
        doc.update(manual)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_rules(corpus_dir: Path) -> tuple[list[Rule], int]:
    """从 `predicates.json` 读规则与预算（没有就返回默认）。"""
    path = Path(corpus_dir) / "predicates.json"
    if not path.exists():
        return list(DEFAULT_RULES), BUDGET
    doc = json.loads(path.read_text(encoding="utf-8"))
    rules = [Rule(名=r["名"], 量=r["量"], 分档=r.get("分档", "零值单列+非零三分"))
             for r in doc.get("参数", {}).get("规则", [])]
    return (rules or list(DEFAULT_RULES)), int(doc.get("预算") or BUDGET)


def load_basic(corpus_dir: Path) -> list[dict[str, Any]]:
    """读**人命名**的基础谓词（`基础谓词` 段）。没有就返回空表。"""
    path = Path(corpus_dir) / "predicates.json"
    if not path.exists():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [b for b in doc.get("基础谓词", [])
            if isinstance(b, dict) and b.get("名") and b.get("定义")]


def apply_basic(nodes: dict[str, Any], edges: dict[str, frozenset[str]],
                basic: Sequence[dict[str, Any]],
                preds: Sequence[Predicate],
                ledger: dict[str, Any] | None = None) -> int:
    """把**基础谓词**作为键追加进 `Node.keys`（**就地**）。

    基础谓词 = 人起的名字 + 合取定义（`["出度档>2", "入度档>5"]`）。
    ⇒ 节点满足**定义里所有项** ⇒ 加键 `名`。

    ## ★★ 三件 2026-10-20 修的事

    **① 解析范围 = 所有推导出来的谓词，不只是预算内那 3 个。**
      之前只传 `used` ⇒ 人想用一个**预算外**的谓词（它推导出来了、只是没进 top-3）
      就查不到 ⇒ **静默跳过**（实测：加键 0 项、无任何提示）。
      ⇒ 调用方要传 `used + dropped`。预算管的是「自动选谁」，**不是**「人能声明什么」。

    **② 改名不是新增维度。**
      若 `名` 的满足集与某个**已有派生键**完全相同 ⇒ 那是**改名**：
      删旧键、加新名。实测（默认 36 项）：同扩展改名后**树形逐字相同**（41 方向、
      成员分布、父子关系全同）—— 所以改名**不需要重构**，但**不能让同一个划分算两次**。

    **③ 不静默。** 跳过/改名都写进 `ledger`（调用方可以并进自己的账本）。
    """
    if not basic:
        return 0
    # 项 → (量, 阈值)：**所有**推导出来的谓词（预算内 + 预算外）
    spec = {p.谓词: (p.量, p.阈值) for p in preds}
    vals = {w: measure(nodes, edges, w) for w in {v[0] for v in spec.values()}}
    applied: set[str] = set()           # 本次已加的名字（第二条起可被改名覆盖）
    touched = 0
    for b in basic:
        name = str(b["名"])
        terms = [str(t) for t in b["定义"]]
        missing = [t for t in terms if t not in spec]
        if missing:                     # 真的没推导出来 ⇒ 跳过，但**要报**
            if ledger is not None:
                ledger.setdefault("未生效", []).append({
                    "名": name, "缺项": missing,
                    "原因": ("定义里的项**没有推导出来**（不是预算问题 —— 预算外的项也能用；"
                             "这是材料里没有这个量/档）")})
            continue
        hit = {nid for nid in nodes
               if all(vals[spec[t][0]][nid] > spec[t][1] for t in terms)}
        # ★ 查重：同扩展的**派生键** ⇒ 这是改名
        rename_from = [k for k in (set(spec) | applied)
                       if k != name
                       and {nid for nid in nodes if k in nodes[nid].keys} == hit]
        for nid in nodes:
            keys = set(nodes[nid].keys) - set(rename_from)
            if nid in hit:
                keys.add(name)
            nodes[nid].keys = frozenset(keys)
        applied.add(name)
        applied -= set(rename_from)
        if rename_from and ledger is not None:
            ledger.setdefault("改名", []).append({"名": name, "替换": rename_from})
        touched += len(hit)
    return touched
