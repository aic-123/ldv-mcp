"""认识的**持续维护**入口 —— 把抽象层与检索层之间那条线接上（`§7.4` / `§A8`）。

    跑法（cwd = repo）：
        python -m ldv.recognize                 # 维护到当前结构并落盘
        python -m ldv.recognize --dry-run       # 只印，不写
        python -m ldv.recognize keyset          # 只跑一条方向

---

## 它补的是哪一段（2026-10-10 之前**一个生产入口都没有**）

    `core/views.propagate`（`E′`）  生产路径 **0 次**（只在 `§A7` 判据里被调过）
    `core/view_persist`            生产路径 **0 次**（只在 `§A6` 判据里被调过）
    `views.json`                   **从来没人写过**（`run_checks` 是拿当前结构**现造**）

⇒ 检索层 `T1` 每次读到的都是 `None` ⇒ **现算** ⇒ `T2`（「指纹相符」）**只有一半在判**，
  而「抽象层持续影响检索层」这句话**没有实体**。

## 它做什么（四步，每步可复核）

    ① 取**当前**外生声明（`spec_paths`：语料旁优先、仓内兜底）
    ② 读**盘上旧的**认识（`view_persist.read`）—— 只当它是**读数**（见下）
    ③ **维护**（`core/views.maintain`）：交付**总是** `csr(spec)`
    ④ **落盘** + 印出「交付了什么、只传播会是什么、两者同不同构、为什么」

## ★★ 为什么第 ③ 步是「总是 `csr`」而不是「能传播就传播」

**实测**（`run_tests.test_maintain` ③、`MEASUREMENTS` 结果二十四）：

    `§A3`（视图最粗）要枚举**更粗的**候选划分，候选数随块数**组合爆炸**：

        `csr`  8 块 ⇒ 候选 **4140**    ⇒ `§A3` **判得了**（枚举完，已是最粗）
        传播  12 块 ⇒ 候选 **4213597** ⇒ `§A3` **判不了**（超上限）⇒ **跳过**

⇒ 交付一个**比 `csr` 更细**的划分，会让「最粗」这条承重性质
  **从可判退化成判不了**，而本仓库的规矩是 **「跳过 ≠ 通过」**。
⇒ 所以这里没有「预算」这个旋钮 —— 那条折中**不存在**（上一版设过，已被实测否掉）。

⚠️ 「维护」这件事因此**便宜**：`csr` 只依赖 `spec`，而真语料上它是**毫秒级**
   （全量 79 个方向：`(视图)` 组整组 3.4 s）。**贵的从来不是维护，是别的。**

## 两条边界

    **只读结构，不写结构。** 不 `expand`、不 `insert` —— 那分别是流程 A 的 `R3a`
      与流程 B 的事（`§1` 表）。它只把**已建成的结构**重新编码成认识。
    **不改外生项。** `P` / `E` 仍只能由人声明（`§K9`）；本入口**消费**声明，不生产它。

## ⚠️ 汇总行与退出码（2026-10-10 改）

一条方向的结局有**三种**（`OUTCOMES`），汇总行**分开印**，退出码看**有没有落地**：

    写入 / 只印（dry-run）  ⇒ **落地了**（维护跑了；dry-run 只是没写文件）
    跳过                    ⇒ 外生声明判不了 ⇒ 本方向**没跑**

    **一条都没落地 ⇒ 退出码 1**（「跳过 ≠ 通过」）。
    有落地但也有跳过 ⇒ 退出码 0，而汇总行**明说跳了几条**。

⚠️ 改之前这里是：`run_one` 两种结局都返回 `True`，汇总行印**命令行传的条数**
  （`len(which)`），退出码恒 0 ⇒ 三份新语料上实测「3 条里只成功 1 条」，
  汇总行照样印「维护了 3 条方向」。
  ⇒ 「跳过与通过长得一模一样」在**汇总行**上的又一例：跳过的细节印在中间，
    而**汇总是人看的那一行**。

## 为什么它是个**独立入口**（而不是塞进 `cli`）

`cli` 每条方向**当场**建内核、跑 A/B/D，**不落盘结构**（`core/persist.py` 也没接线）
⇒ 「哪一份结构是权威」在 `cli` 里**还没有答案**。
而增量建与一次建**不是同一棵树**（实测：`先建 6、维护 30` ⇒ 3 个方向；
`一次建 36` ⇒ 25 个方向）⇒ 挂在 `cli` 上的认识，下次读时指纹必然不符。

⇒ 所以本入口按 **`spec` 认**（外生声明、进版本库、确定性），
  认识的**版本就是 `spec` 的指纹**。**结构落盘是另一件事，要人拍。**
"""
from __future__ import annotations

import sys

from .checks._fixtures import find_corpus, load
from .checks._fixtures import coverage_of
from .checks.abstraction import build_views, load_spec_file, spec_for
from .checks.coverage import corpus_fingerprint
from .core import view_persist
from .core.views import maintain, shrink_ratio
from .run_checks import batch_kernel, maintenance_kernel
from .plugins import DIRECTIONS


#: 视图列表在落盘字典里的**键名**。一处定义（`to_dict` 用的就是它）。
VIEWS_KEY = "视图"

#: `run_one` 的**三种结局**。**不许压成布尔** —— 「跳过」与「写成」在只看
#: `True/False` 时**长得一模一样**，而这正是本仓库记过四次的形状
#: （`Tri` 就是为它立的）。⚠️ 这里原来就压成了布尔，后果见 `run_one` 的 docstring。
#:
#:     `写入`  真的落了盘（`views.json`）
#:     `只印`  `--dry-run`：维护跑了、**没写文件** —— 它**不是跳过**
#:     `跳过`  外生声明判不了（方向不符 / 声明不在）⇒ 本方向**没跑**
OUTCOMES = ("写入", "只印", "跳过")


def run_one(which: str, nodes, edges, *, dry_run: bool) -> tuple[str, str]:
    """跑一条方向 —— 返回 `(结局, 文本)`，结局取 `OUTCOMES` 里的一个。

    ⚠️ **结局不许压成布尔**（2026-10-10 改）：这里原来两种结局都返回 `True`，
       而汇总行印的是**命令行传的条数** ⇒ 实测三份新语料上「3 条里只成功 1 条」
       照样印「维护了 3 条方向」且**退出码 0**。
       「跳过」与「写成」在只看布尔值时**长得一模一样** —— 本仓库记过四次的形状，
       这次它出在**汇总行**上（跳过的细节印在中间，而汇总是人看的那一行）。
    """
    nodes_dir = find_corpus()
    corpus = corpus_fingerprint(nodes, edges)
    doc = load_spec_file(nodes_dir, corpus)
    spec, why = spec_for(doc, which, corpus)
    if spec is None:
        return "跳过", (f"═══ 方向 {which} ═══\n  ⊘ {why}\n"
                        f"     ⇒ **本方向没跑**（跳过 ≠ 通过）")
    # ★★ 2026-10-13（v0.1.21）：**与消费者同源** —— 视图层跑**维护路径**
    #   （`run_checks.view_report` / `multilevel_report` / `retrieval_report` 三处
    #   都用 `maintenance_kernel`）。本模块是 `views.json` 的**唯一生产者**，
    #   而那份文件正是那三处**读回来的东西** ⇒ 两边必须建**同一棵树**，
    #   否则「写进去的」与「读出来的」按各自的方向 id 解释 ⇒ 盘上那份
    #   可能**看着指纹相符、内容却是另一棵树的**（`T2` 只比 `spec` 的指纹，
    #   比不出这件事）。
    #   ⚠️ 实测（`sequence`）：两种路径的 `to_dict` 输出**逐字节相同** ⇒
    #     这一改动**零行为变化**；但它是**潜伏**的 —— 换到 `reach`
    #     （批建 71 ≠ 维护 15）就会分岔。`plugin` 与路径无关，仍取批建那份。
    kernel = maintenance_kernel(which, nodes, edges)
    plugin = batch_kernel(which, nodes, edges)[1]
    cover = coverage_of(which, nodes, edges)
    path = view_persist.default_path(nodes_dir)

    disk = view_persist.read(path)
    seed_q = None
    seed_why = "没读"
    if disk:
        from .core.view_persist import from_dict
        try:
            vs_old = from_dict(disk, kernel, cover, plugin)
        except Exception as exc:  # noqa: BLE001 —— 坏存档要**报出来**
            seed_why = f"盘上那份**读不回来**（{type(exc).__name__}: {exc}）⇒ 重建"
        else:
            if set(vs_old.spec.universe) <= set(spec.universe):
                seed_q, seed_why = list(vs_old.q), "盘上那份可作种子（只影响读数）"
            else:
                seed_why = "盘上那份的 `universe` 超出当前 ⇒ 不能当种子"
    else:
        seed_why = f"盘上没有认识（首轮）—— {path}"

    m = maintain(seed_q, spec)
    q_csr = len(m.q)
    lines = [
        f"═══ 方向 {which} ═══",
        f"  声明 {why}",
        f"  落点 {path}",
        f"  种子 {seed_why}",
        f"  交付 **{len(m.q)} 块**（最粗 `csr`）｜shrink {m.shrink:.3f}"
        f"｜动作「{m.action}」",
        f"        {m.reason}",
    ]
    if seed_q is not None:
        lines.append(
            f"  读数：只传播会是 {len(_propagated(seed_q, spec))} 块"
            f"（shrink {m.shrink_if_propagated:.3f}）｜两条路**同构** {m.propagate_equal}"
            f"　⚠️ 无论如何**交付 `csr`** —— 更细的划分会让 `§A3` 判不了")
    if dry_run:
        lines.append("  （`--dry-run` —— **没写文件**）")
        return "只印", "\n".join(lines)

    vs = build_views(kernel, spec, cover, plugin)
    assert len(vs.q) == q_csr, (
        f"`build_views` 算出来的 {len(vs.q)} 块 ≠ 维护交付的 {q_csr} 块 —— 两处分岔了")
    p = view_persist.write(path, vs)
    back = view_persist.read(p)
    # ⚠️ 键名**从 `to_dict` 那一处取**（`FORMAT_KEY` / `VIEWS_KEY`）——
    #    这里原来写死了 `"format"` / `"views"`（真键名是 `"格式"` / `"视图"`）⇒
    #    打印出来是 `None` 与 `0`，而**文件其实是好的** ⇒ 又是一次「两处各写一份」。
    lines.append(f"  ✔ 已写入 {p}（读回格式串 {back.get(view_persist.FORMAT_KEY)!r}｜"
                 f"{len(back.get(VIEWS_KEY) or [])} 张视图｜q {len(back.get('q') or [])} 块）")
    return "写入", "\n".join(lines)


def _propagated(seed_q: object, spec) -> list:
    from .core.views import propagate

    return list(propagate(seed_q, spec)) if seed_q else []


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    which = [a for a in argv[1:] if a in DIRECTIONS] or list(DIRECTIONS)
    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 —— 什么都没维护（**这不是通过**）。"
              "设 `LDV_CORPUS` 或把语料放到 `ldv/corpus/nodes/`。")
        return 1
    nodes, edges, _ = loaded
    print("认识维护 · 流程 E 的**持续**那一步（`§7.4` / `§A8`）")
    print(f"语料 {find_corpus()}｜项 {len(nodes)}｜方向 {which}"
          + ("｜**dry-run**" if dry else ""))
    print()
    counts = {k: 0 for k in OUTCOMES}
    for name in which:
        outcome, text = run_one(name, nodes, edges, dry_run=dry)
        counts[outcome] += 1
        print(text)
        print()
    landed = counts["写入"] + counts["只印"]
    print("═══ 汇总 ═══")
    print(f"  请求 {len(which)} 条方向 ⇒ 写入 {counts['写入']} · "
          f"只印（dry-run）{counts['只印']} · **跳过 {counts['跳过']}**"
          f"｜交付物一律 `csr`")
    if not landed:
        print(f"  ✗ **一条都没落地**（{counts['跳过']} 条全跳过）—— "
              f"「跳过 ≠ 通过」⇒ **退出码 1**")
        return 1
    if counts["跳过"]:
        print(f"  ⚠️ 上面这行**不是**「{len(which)} 条全过」："
              f"{counts['跳过']} 条**跳过**（原因见各自那一段）")
    print(f"  退出码 0（{landed} 条落地）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
