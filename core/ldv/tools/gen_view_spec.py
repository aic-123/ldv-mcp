"""生成流程 E 的 `view_spec.json` —— **只算机械部分，不替人做决定**。

    跑法（cwd = repo）：
        python -m ldv.tools.gen_view_spec keyset                # 只打印 + 逐字对照
        python -m ldv.tools.gen_view_spec keyset --write        # 落盘

---

## 它算什么、不算什么

`view_spec.json` 里有五样东西。**三样能机械算出来，两样不能**：

| 栏 | 谁给 | 为什么 |
|---|---|---|
| `语料` | **算** | 就是 `corpus_fingerprint(nodes, edges)`，两个数 |
| `方向` | **人给**（命令行） | 「这批 id 属于哪条方向」—— 内核各自从 `D0` 开始编号，不写清就歧义 |
| `universe` `U` | **算** | 那个内核的 `all_directions()` |

⚠️ **`universe` 算的是「维护路径」那棵树**（与 `run_checks.view_report` 同源）——
   方向 id 每个内核各自从 0 开始，而**同一条方向在批建 / 维护两条路径上是两棵树**
   （实测 `reach` 71 / 15、`sequence` 16 / 8）⇒ 两边不同源就会崩，见 `build_spec`。
| `relation` `E` | **算**（候选） | 这棵树自己的树边。它是**结构事实**，冻结在这里、进版本库 |
| `partition` `P` | **候选** | 生成器只给**信息量最低**的那一档 `P = {U}` |
| `读数` | **人给** | 类别是**外生声称**（Gray 三分法有定理，不是能算出来的属性） |

⚠️ **`P` / `E` 仍须外生**（设计稿 §3 / `§K9`）。生成器**只产出候选**：
   它算出来的东西必须被人**看过、认过**才落盘。所以默认**不写文件**，
   只在 stdout 打出来 + 与现有文件逐栏对照；`--write` 才落盘。

⚠️ **`读数` 一律照抄**。它是人写的，生成器**不许**编一条、也**不许**静默丢掉。
   现有文件里 `方向` 与本次不同 ⇒ **拒绝照抄**并说明（那种情况下抄过来就是把
   另一条方向的声称搬到这一条上）。

---

## 为什么默认 dry-run（而不是「写完再看 diff」）

「**空转与通过长得一模一样**」在本工具里的形态是：
    **生成器什么都没改** 与 **生成器改对了** 在输出里是同一句话。

⇒ 所以默认不写，并且把三件事**分别**印出来：
    ① 算出来的 spec 是什么
    ② 与现有文件**逐栏**的差（没差就明说「逐字相同」）
    ③ **没算**的那两栏是什么（`P` 的候选值 + 为什么是它；`读数` 从哪来）

---

## 退出码只看「有没有违规」

    生成出来的 spec 过不了 `ViewSpec.__post_init__`      ⇒ 1（生成器坏了）
    `--write` 且会**丢掉** `读数` 而没给 `--allow-drop-readings`  ⇒ 1
    其余（有没有差、差几行）**走输出**，不进退出码

`--write` 落盘前**先过一遍 `ViewSpec(...)`** —— 坏 spec 不许写进版本库。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..checks._fixtures import find_corpus, load
from ..checks.abstraction import VIEW_SPEC_NAME, VIEW_SPEC_PATH, ViewSpec, spec_paths
from ..checks.coverage import corpus_fingerprint
from ..core.kernel import _did_order

#: 生成器**算**的那三栏 + 照抄的一栏。写在这里，免得 `--write` 时漏栏。
MECHANICAL = ("语料", "方向", "universe", "partition", "relation")
CARRIED = ("读数",)


def spec_target(nodes_dir: Path | None) -> Path:
    """`--write` 的落点 —— **外部语料 ⇒ 语料旁；仓内语料 ⇒ 仓内那份**。

    ## 为什么不能靠「哪份文件已存在」来定落点

    **外部语料第一次生成时，语料旁那份还不存在** ⇒ 若按「谁存在写谁」，
    就会落到仓内那份 ⇒ **把 36 项语料的声明覆盖掉**（那份是仓内默认语料在用的）。

    ⇒ 所以判据是「**语料在哪**」，不是「文件在哪」：
        语料在 `ldv/` 包内（`corpus/nodes`）  ⇒ `ldv/checks/view_spec.json`（现状，不动）
        语料在包外（`_data/<名>/nodes`）      ⇒ `<语料父目录>/view_spec.json`（语料旁）

    ⚠️ 与 `spec_paths` 的**查找**顺序同源：查找是「语料旁优先、仓内兜底」，
       而落点是「按语料归一」。两者一致 ⇒ 「读到的」与「写回的」是同一份。
    """
    if nodes_dir is None:
        return VIEW_SPEC_PATH
    pkg_root = Path(__file__).resolve().parents[1]          # ldv/
    try:
        nodes_dir.resolve().relative_to(pkg_root)
    except ValueError:
        return nodes_dir.parent / VIEW_SPEC_NAME            # 包外 ⇒ 语料旁
    return VIEW_SPEC_PATH


def build_spec(which: str, nodes, edges) -> tuple[dict, list[str]]:
    """算出机械那几栏。返回 `(spec, 说明行)`。

    ⚠️ **必须用 `maintenance_kernel` —— 与 `run_checks.view_report` 同源**（v0.1.21）。

    声明里的 `universe` 是「**判据实际会去建的那些方向 id**」。方向 id 是**每个内核
    各自从 0 开始**编的，而**同一条方向在两条路径上是两棵树** —— 实测（36 项 / 47 边）：

        keyset    批建 25 = 维护 25     ← 只有这一条两条路径同形
        reach     批建 71 ≠ 维护 15
        sequence  批建 16 ≠ 维护  8

    ⇒ 若生成器按**批建**编 `universe`、判据却按**维护**建视图，声明里就会出现
      `D8`..`D15` 这些维护路径上**不存在**的 id ⇒ `build_views` 抛 `KeyError`
      （2026-10-13 实测崩在 `D10`）。**两边必须同源。**

    ⚠️ 它也是「生成器只给候选、人看过才落盘」的一个具体形态：算出来的 `universe`
       只对**一条路径**成立，而「这份声明描述哪条路径」是**外生**的 ——
       生成器不替人挑，只保证自己与判据走的是**同一条**。
    """
    from ..run_checks import maintenance_kernel

    kernel = maintenance_kernel(which, nodes, edges)
    dirs = sorted(kernel.all_directions(), key=lambda d: _did_order(d.did))
    universe = [d.did for d in dirs]
    relation = sorted(
        ([d.parent, d.did] for d in dirs if d.parent),
        key=lambda e: (_did_order(e[0]), _did_order(e[1])),
    )
    spec = {
        "语料": corpus_fingerprint(nodes, edges),
        "方向": which,
        "universe": universe,
        # ⚠️ **候选**：信息量最低的一档。理由见 `view_spec.json` 的 `_note`：
        #    一点先验都不给，答案照样唯一 —— 这才说明「视图集合不用挑」。
        "partition": [list(universe)],
        "relation": relation,
    }
    notes = [
        f"`universe`  {len(universe)} 个方向（内核自己编的号，`{universe[0]}`..`{universe[-1]}`）",
        f"`relation`  {len(relation)} 条树边（`父 → 子`；⚠️ 方向写反**不会报错**，"
        f"只会得到另一个划分 —— 见 `core/views.py::_preimage`）",
        f"`partition` **候选** = 1 块（`P = {{U}}`，信息量最低那一档）",
    ]
    return spec, notes


def carry_readings(doc: dict, which: str,
                   spec_corpus: dict[str, int] | None = None) -> tuple[list | None, str]:
    """照抄现有文件里的 `读数`。返回 `(读数 或 None, 说明)`。

    ⚠️ 三种情形分开说 —— 它们要人做的事**不一样**：
        文件不在           ⇒ 「还没人写过读数」
        方向不同           ⇒ **拒绝照抄**（那是另一条方向的声称）
        有读数且方向相同   ⇒ 照抄，条数报出来
    """
    if not doc:
        return None, "两个位置都没有声明文件 ⇒ 没有可照抄的 `读数`（要人写）"
    if doc.get("方向") != which:
        return None, (f"现有文件的 `方向` 是 {doc.get('方向')!r}，本次是 {which!r} "
                      f"⇒ **拒绝照抄 `读数`**（那是另一条方向的声称，搬过来就是替人改声明）")
    r = doc.get("读数")
    if not r:
        return None, "现有文件里 `读数` 为空 ⇒ 没有可照抄的"

    # ★★ **语料相关的那一部分不许照抄**（2026-10-10 实测踩到，这是本次 A4 红的根因）
    #
    #     `读数` 看起来与语料无关，但 `holistic` 那一条的 `见证.A / 见证.B` 是
    #     **人挑的一组方向**，而它成立与否**取决于语料**（`摘要 = (计数, 和)`，而「和」随语料变）。
    #     实测：36 项上挑的那一对被搬进全量声明 ⇒ 两边 `摘要` 不再相同
    #     ⇒ `§A4` 红（「中位数 / 覆盖计数 的见证不成立」）。
    #     ⇒ 那是**生成器静默搬了一个绑语料的东西**，而它长得像「照抄人写的栏」。
    #
    #  ⇒ 规矩：`见证` 若是**显式 A / B**（绑语料）⇒ **换语料时拒绝照抄**；
    #    若是**构造法**（`构造`，与语料无关）⇒ 照抄没问题。
    #     拒绝之后由人重挑 —— **宁可不替他挑，也不许静默搬一条不成立的见证**。
    bound = [d.get("名") for d in r
             if isinstance(d.get("见证"), dict) and "A" in d["见证"]]
    if bound and doc.get("语料") != spec_corpus:
        return None, (f"现有文件的 `读数` 里有 {len(bound)} 条**绑语料**的显式见证"
                      f"（{bound}）⇒ **拒绝照抄**：语料从 {doc.get('语料')} 变成 "
                      f"{spec_corpus} ⇒ 那对 A / B 的 `摘要` 不再相同（`§A4` 会红）。"
                      f"　两条出路：改成**构造法**（`见证: {{摘要, 构造: 同摘要异读数}}`，"
                      f"与语料无关）或按新语料重挑一对")
    return r, f"照抄现有 `读数` {len(r)} 条（**逐字**，生成器不改它）"


def diff_lines(old: dict, new: dict) -> list[str]:
    """逐栏对照。**逐字相同**要明说 —— 「没改」与「改对了」不许长得一样。"""
    out: list[str] = []
    for k in MECHANICAL:
        a, b = old.get(k), new.get(k)
        if a == b:
            out.append(f"  {k:<11} 逐字相同")
        elif a is None:
            out.append(f"  {k:<11} 新增（原来没有）")
        else:
            na = len(a) if isinstance(a, list) else a
            nb = len(b) if isinstance(b, list) else b
            out.append(f"  {k:<11} **变了**：{na} → {nb}")
    return out


def merge_spec(old: dict, spec: dict, readings: list | None) -> tuple[dict, list[str]]:
    """把算出来的几栏并进现有文件，**保留人写的一切**。返回 `(合并结果, 被丢的键)`。

    ⚠️ **这一条是这份工具最容易写错的地方**（2026-10-08 实测踩到）：
       第一版直接 `json.dumps(spec)` 落盘 ⇒ 把 `_note`（91 行，人写的）**整块抹掉**，
       而且 `--write` 的输出看起来**完全正常** —— 又一处「**空转与通过长得一模一样**」。

    规矩：生成器**只动它拥有的那几栏**（`MECHANICAL` + 照抄来的 `读数`）。
    别的键（`_note`、将来任何人加的东西）**原样搬过去**。

    ⚠️ **方向不符时 `读数` 必须被删掉，不许留着**：那是**另一条方向**的声称，
       留着它比丢掉它更糟 —— 它会被当成这一条方向的声明来判。
       ⇒ 那一条会进「被丢的键」，由调用方决定要不要 `--allow-drop-readings`。
    """
    merged = dict(old)                      # 保序：Python dict 保插入序 ⇒ `_note` 还在最前
    for k in MECHANICAL:
        merged[k] = spec[k]
    if readings is not None:
        merged["读数"] = readings
    else:
        merged.pop("读数", None)            # ← 方向不符 ⇒ 别人的读数不许留
    dropped = [k for k in old if k not in merged]
    return merged, dropped


def main(argv: list[str]) -> int:
    args = argv[1:]
    write = "--write" in args
    reformat = "--reformat" in args
    allow_drop = "--allow-drop-readings" in args
    whichs = [a for a in args if not a.startswith("-")]
    if len(whichs) != 1:
        print("用法：python -m ldv.tools.gen_view_spec <keyset|reach|sequence> "
              "[--write] [--reformat] [--allow-drop-readings]")
        return 2
    which = whichs[0]

    loaded = load()
    if loaded is None:
        print("⚠ 找不到语料 ⇒ 什么都没算（**这不是通过**）。"
              "用 `LDV_CORPUS=<名字或路径>` 指一份。")
        return 1
    nodes, edges, _ = loaded

    spec, notes = build_spec(which, nodes, edges)

    # ★ **落点跟着语料走**（与 `spec_paths` 的查找顺序同源）。
    nodes_dir = find_corpus()
    target = spec_target(nodes_dir)
    # `读数` 的**来源**：先看落点那份；落点不在时看另一处 —— `读数` 是**插件**的
    # 性质声称（`distributive`/`algebraic`/`holistic`），与语料无关；
    # 而 `carry_readings` 自带「方向不符 ⇒ 拒绝照抄」的保护，所以照抄是安全的。
    src = target if target.is_file() else next(
        (q for q in spec_paths(nodes_dir) if q.is_file()), target)
    old = json.loads(src.read_text(encoding="utf-8")) if src.is_file() else {}
    readings, rnote = carry_readings(old, which, spec.get("语料"))
    if src != target and src.is_file():
        rnote += f"（来源：{src}）"
    merged, dropped = merge_spec(old, spec, readings)

    print(f"═══ 生成 `{target.name}` · 方向 {which} ═══")
    print(f"  落点 {target}")
    print(f"  语料 {spec['语料']}")
    for n in notes:
        print(f"  {n}")
    print(f"  `读数`      {rnote}")
    kept = [k for k in old if k in merged and k not in MECHANICAL and k != "读数"]
    if kept:
        print(f"  人写的键**原样保留**：{kept}（生成器不碰它们）")

    print("\n── 与现有文件逐栏对照 ──")
    for line in diff_lines(old, spec):
        print(line)
    if not old:
        print("  （现有文件不在 —— 每一栏都是新增）")

    # ⚠️ 落盘前**先过判据**：坏 spec 不许写进版本库。
    try:
        ViewSpec(universe=tuple(merged["universe"]),
                 partition=tuple(frozenset(b) for b in merged["partition"]),
                 relation=frozenset(tuple(e) for e in merged["relation"]))
    except Exception as exc:  # noqa: BLE001 - `__post_init__` 抛的就是要报的
        print(f"\n‼ 生成出来的 spec 过不了 `ViewSpec`：{type(exc).__name__}: {exc}")
        print("   ⇒ **不落盘**（生成器坏了，不是文件该改）")
        return 1

    if not write:
        print("\n（dry-run —— **没写文件**。要落盘加 `--write`。"
              "落盘前请人先认 `partition` 与 `读数` 那两栏。）")
        return 0

    if dropped and not allow_drop:
        print(f"\n‼ `--write` 会**丢掉** {dropped} —— 那是人写的东西"
              f"（`读数` 方向不符时必须丢，但那是**删声明**，得人点头）。"
              f"要真丢请显式给 `--allow-drop-readings`。")
        return 1

    body = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    raw = target.read_text(encoding="utf-8") if target.is_file() else None

    # ★ **内容没变就不动文件**（2026-10-08 加）。
    #
    # 第一版无条件重写 ⇒ 一次「什么都没变」的 `--write` 会产出 **198 行**的 diff
    # （手写排版的数组被摊平），而**内容一个字都没动**。
    # ⇒ 那正是本项目一直在防的形状：**「改对了」与「只是排版变了」长得一模一样**，
    #   审的人会去逐行看那 198 行，而真正该看的那一行淹在里面。
    #
    # ⇒ 规矩：`--write` 是**幂等**的，而且**内容相同 ⇒ 字节级不动**。
    #   要统一排版是另一件事，得显式说（`--reformat`）。
    if raw is not None and merged == old:
        same_text = raw == body
        if same_text or not reformat:
            print("\n✔ **内容逐栏相同** ⇒ **没动文件**"
                  + ("（字节级也相同）" if same_text
                     else "（磁盘上那份是手写排版；要统一成规范形给 `--reformat`）"))
            return 0
        # `--reformat`：内容不变但要把排版统一成规范形 —— 显式要求才做。

    target.write_text(body, encoding="utf-8")
    print(f"\n✔ 已写入 {target}")
    print("  ⚠️ 生成器只给了**候选** `P` 与**机械算的** `E`；落盘之后它们就算"
          "**人的声明**了 —— 改它们要走 `--write` + 人复核，"
          "不是让它跟着语料自动漂。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
