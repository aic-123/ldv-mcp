"""使用记录的落盘通道 —— 一条记录一文件，**只追加、不改、不覆写**。

⚠️ 与一次性树（`exchange_staging`）的区别，这条不能含糊：

    一次性树装的是 **消息**（外部内容），所以可以 `promote` 进 `nodes/`；
    使用记录是**系统对自己的记账**，**永不进 `nodes/`** ——
    一旦进去就是「使用记录驱动结构」，直接撞 `§K9`
    （可复现性与可解释性同时丢，而且是很久以后才发现）。

目录：`<语料>/usage/<qfp8>-<did>.md`，front matter 字段：

    id          记录 id（= 文件名去扩展名）
    did         方向 id
    outcome     +1 = 有用（被下钻）；**不记负信号**（没被下钻 ≠ 没用）
    propensity  曝光位次折算的倾向权重（内核填，不由本模块算）
    qfp         查询指纹（哪一次查询）
    at          时间戳（遗忘的输入；缺 ⇒ 不衰减，不猜）
    note        人可读的一句话
    snapshot    §I7 `record(方向)` 的 JSON —— **可空**，见下

**去重键 = `(qfp, did)`**：同一查询对同一方向只记一次 ——
重复下钻不叠加（叠加等于把「多看了一眼」当成「更有用」）。

---

## `snapshot` 是什么，为什么要有（§I7 的消费者）

一条使用记录以前只知道「**哪个方向**被下钻过」。缺的那一半是：
**那一刻这个方向长什么样**（键集 / 锚点 / 模式是什么）。没有它，

    倾向说「D5 有用」     ⇒  D5 现在的内容可能与记这条时**已经不是一回事**
      （维护会往方向里加项、劈开会造新方向）

⇒ `snapshot` = 下钻那一刻 `plugin.record(d)` 的输出（§I7），
   随记录一起落盘。它是**可空**的：没接 §I7 的调用方（测试、合成记录）
   不写这一行，`load()` 读到旧文件也照常解析（`snapshot` 为 `""`）。

⚠️ snapshot 是**留痕**，不是**输入**：倾向计算只读 `outcome / propensity / at`，
   不读它。它改变不了任何次序 —— 改了就是「使用记录驱动结构」（§K9）。
⚠️ `record()` 抛异常**直接传播**（与 §I3 / §I4 同策）：这一层不许静默。
   契约全文见 `docs/插件契约.md`。
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

DIRNAME = "usage"
#: 缓冲区子目录 —— 查询先落这里，**不参与倾向**（`load` 不递归进来）
STAGE = "_staging"
#: 缓冲区容量上限（批次数）—— **外生参数、暂定值，未经标定**。
#:
#: ⚠️ 这个 50 是「先让功能能用」的暂定数，不是测出来的。要动它得先有刻度：
#:    多少次查询之内还该记得住？—— 那是人的判断，不是这里能算的。
#: ⚠️ **只淘汰缓冲，正式记录永不淘汰**（正式是账本，缓冲是状态）。
STAGE_CAP = 50

#: 长期隐性记忆的**遗忘半衰期**（天）—— **外生参数、暂定值，未经标定**。
#:
#: ★★ **遗忘发生在计算上，不发生在存储上**：
#:    记录**永不删**（账本只追加），只在算倾向时乘衰减因子
#:    `w = 0.5 ** (年龄天数 / HALF_LIFE_DAYS)`。
#:    ⇒ 老经验影响力变小，但任何时候都能回答「它曾经学过什么」；
#:      改这个数就能「重新记起来」（可回退）。
#: ⚠️ `0` = 不衰减（与加衰减之前逐字相同）—— 判据用它做「回退对照」。
HALF_LIFE_DAYS = 30.0

#: 两层记忆的**名字**（用户 2026-10-22 要求「给它们名字」）。
#: 短期 = 缓冲（显式：入口会报「还剩 N 支待看」）；长期 = 正式（隐性：不再逐条呈现）。
SHORT_TERM = "短期显性记忆"
LONG_TERM = "长期隐性记忆（稳定区）"
FIELDS = ("id", "did", "outcome", "propensity", "qfp", "note", "at", "snapshot")


def _dir(corpus_dir: Any) -> Path:
    return Path(corpus_dir) / DIRNAME


def _qfp8(qfp: str) -> str:
    return hashlib.sha1(str(qfp).encode("utf-8")).hexdigest()[:8]


def _safe(s: str) -> str:
    """文件名里只留安全字符（did 形如 D0 / K=a|b，竖线要换掉）。"""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(s))


def append(corpus_dir: Any, *, did: str, outcome: float, propensity: float,
           qfp: str, note: str = "", at: str | None = None,
           snapshot: str = "") -> dict:
    """追加一条记录。**同 `(qfp, did)` 已存在 ⇒ 不写、不覆写、不报错。**

    `snapshot`（可空）= §I7 `record(方向)` 的 JSON，一行写完
    （`sort_keys` ⇒ 确定性；**不许含换行**，否则 front matter 会断）。
    """
    d = _dir(corpus_dir)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{_qfp8(qfp)}-{_safe(did)}.md"
    p = d / name
    if p.exists():
        return {"ok": True, "written": False, "path": str(p),
                "why": "已有同一 (qfp, did) 的记录 —— 只追加，不覆写"}
    # `at` 是**遗忘的输入**：没有它就只能不衰减（见 decay_weight）。
    # 判据要固定 now ⇒ 允许注入。
    at = at or datetime.now().isoformat(timespec="microseconds")
    snap_line = f"snapshot: {snapshot}\n" if snapshot else ""
    body = (
        "---\n"
        f"id: {p.stem}\n"
        f"did: {did}\n"
        f"outcome: {float(outcome)}\n"
        f"propensity: {float(propensity)}\n"
        f"qfp: {qfp}\n"
        f"at: {at}\n"
        f"note: {note}\n"
        f"{snap_line}"
        "---\n"
        f"一次使用记录：查询 {qfp} 展示了方向 {did}，随后它被下钻（outcome = 有用）。\n"
        "⚠️ 没被下钻的方向**不产记录** —— 「没看见」与「看见了不好」不是一回事。\n"
        + ("⚠️ `snapshot` 是这一刻的方向状态（§I7）—— 只留痕，不参与倾向计算。\n"
           if snapshot else "")
    )
    # ⚠️ 显式 newline="\n"：Windows 上默认会把 \n 写成 \r\n，而仓库规定 LF。
    io.open(p, "w", encoding="utf-8", newline="\n").write(body)
    return {"ok": True, "written": True, "path": str(p)}


def load(corpus_dir: Any) -> list[dict]:
    """读回全部记录（按文件名排序 ⇒ 确定性）。解析不了的**跳过并计数**，不静默。"""
    d = _dir(corpus_dir)
    out: list[dict] = []
    bad: list[str] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        try:
            t = io.open(p, encoding="utf-8", errors="replace").read()
            head = t.split("---", 2)[1]
            row = {}
            for line in head.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    row[k.strip()] = v.strip()
            if not (row.get("did") and row.get("outcome")):
                raise ValueError("缺 did 或 outcome")
            out.append({"did": row["did"], "outcome": float(row["outcome"]),
                        "propensity": float(row.get("propensity") or 1.0),
                        "note": row.get("note", ""), "qfp": row.get("qfp", ""),
                        "at": row.get("at", ""), "file": p.name,
                        # §I7 快照：**可空** —— 旧文件没有这一行 ⇒ `""`
                        "snapshot": row.get("snapshot", "")})
        except Exception:                                    # noqa: BLE001
            bad.append(p.name)
    if bad:
        print(f"  ⚠️ usage 里有 {len(bad)} 个文件解析不了（已跳过）：{bad[:3]}")
    return out


# ── 遗忘：只在**计算**上发生，不在存储上 ────────────────────────────────────
#
# ★★ 用户 2026-10-22 定：「只降权、不删」。
#    理由：账本只追加是这仓库的纪律 —— 删了正式记录，它就不是账本了，
#    而且不可审计（答不出「它曾经学过什么」）、不可回退（删了就没了）。
#    ⇒ 遗忘 = 算倾向时乘衰减因子；记录本身**永久保留**。


def decay_weight(at: str, *, now: datetime | None = None,
                 half_life_days: float = HALF_LIFE_DAYS) -> float:
    """一条记录的衰减因子 `0.5 ** (年龄天数 / 半衰期)`。

    ⚠️ **缺时间戳 ⇒ 1.0（不衰减），并如实报数** —— 不猜。
       「不知道时间」既不能当「很老」（w→0，等于凭空抹掉证据），
       也不能当「很新」；1.0 的意思是「不施加惩罚」，不是「假设它刚发生」。
    ⚠️ `half_life_days <= 0` ⇒ 1.0（不衰减）。判据拿它做「回退对照」：
       把半衰期关掉，倾向必须与加衰减之前**逐字相同**。
    """
    if not at or half_life_days <= 0:
        return 1.0
    try:
        t = datetime.fromisoformat(at)
    except ValueError:
        return 1.0
    now = now or datetime.now()
    age_days = max(0.0, (now - t).total_seconds() / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def aged_tendency(corpus_dir: Any, *, now: datetime | None = None,
                  half_life_days: float = HALF_LIFE_DAYS) -> dict[str, float]:
    """每个方向的**衰减后累计证据** `Σ (outcome / propensity × w)` —— 局部量。

    ★★ 为什么是**和**、不是**平均**（这条是踩出来的）：
       加权平均里 `w` 会被约掉 ⇒ **单条记录看不出衰减** ——
       一条 300 天前的记录和一条刚写的记录，平均值都是 1.0。
       实测（`evidence/_probe_forgetting.py` F6 第一版）就是「新 1.0000 → 旧 1.0000」。
       ⇒ 记忆强度本来就该是**累计**的：重复证据累加（巩固），老证据衰减（遗忘）。
         这同时符合「越用越顺手」的语义。

    ⚠️ 与 `selfopt.tendency_by_direction` **不是同一个量**：那边是**平均**（`flow D` 用），
       这边是**累计证据**（入口排序用）。两者只在「单条记录」时数值相同。
       **不要**把这边改成平均 —— 一改，遗忘就对单条记录失效。
    ⚠️ 只收合法倾向（`§K7` / `B11`）：`propensity` 必须在 `(0, 1]`。
    ⚠️ 没有任何合法记录的方向**不出现**在结果里（`§K6`：没有 ≠ 0）。
    ⚠️ 跨方向**不求和、不求平均** —— 那是全局评分（`§B12`）。
    """
    per: dict[str, list[tuple[float, float]]] = {}
    for r in load(corpus_dir):
        p = float(r.get("propensity") or 0.0)
        if not (0.0 < p <= 1.0):
            continue
        w = decay_weight(r.get("at", ""), now=now, half_life_days=half_life_days)
        per.setdefault(r["did"], []).append((float(r["outcome"]) / p, w))
    return {did: sum(v * w for v, w in pairs) for did, pairs in sorted(per.items())}


# ── 缓冲区：查询先落这里，**不算有用** ──────────────────────────────────────
#
# ★★ 为什么要有缓冲：如果 `query` 直接记 outcome=+1，一次查询就把 16 支全记一遍
#    ⇒ 倾向被刷成「谁被摆出来谁有用」。而「被摆出来」只是**曝光**，不是**有用**。
#    所以：query 写「这次摆了哪些支」（一批一条，不算分），
#    drill 才 `promote` 成 outcome=+1（这一支真的被往下看了）。
#
# ⚠️ 缓冲**不参与倾向计算** —— `load()` 只 glob `usage/*.md`，不递归进 `_staging/`。
#    这是「装上但不算有用」的兑现点：判据 `[接入] ⑤` 守它。


def _stage_dir(corpus_dir: Any) -> Path:
    return _dir(corpus_dir) / STAGE


def _evict(corpus_dir: Any, cap: int = STAGE_CAP) -> list[str]:
    """缓冲有界：超过 `cap` 就删最旧的（按 `at`，再按文件名 —— 确定性）。

    ⚠️ **只动缓冲区**。正式记录是账本，**永不淘汰** —— 删正式只能 `clear`。
    """
    rows = stage_load(corpus_dir)
    if len(rows) <= cap:
        return []
    rows.sort(key=lambda r: (r.get("at") or "", r["file"]))
    drop = rows[: len(rows) - cap]
    d = _stage_dir(corpus_dir)
    for r in drop:
        try:
            (d / r["file"]).unlink()
        except OSError:
            pass
    return [r["file"] for r in drop]


def stage(corpus_dir: Any, *, qfp: str, shown: list[str], note: str = "") -> dict:
    """把「这次展示了哪些支」写进缓冲区 —— **一次查询一批，同 qfp 覆盖**。

    ⚠️ 覆盖是**刻意**的：缓冲区存的是「这次查询摆了什么」这个**状态**，
       不是账本。同一次查询重跑 ⇒ 展示列表一样 ⇒ 覆盖成同一份。
    ⚠️ 有界（`STAGE_CAP`）：超了淘汰最旧的**缓冲**，正式记录一条不动。
    """
    d = _stage_dir(corpus_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{_qfp8(qfp)}.md"
    existed = p.exists()
    at = datetime.now().isoformat(timespec="microseconds")
    body = (
        "---\n"
        f"id: {p.stem}\n"
        f"qfp: {qfp}\n"
        f"at: {at}\n"
        f"shown: {','.join(str(x) for x in shown)}\n"
        f"n_shown: {len(shown)}\n"
        f"note: {note}\n"
        "---\n"
        "一次查询的**展示批次**：这次摆了哪些支。\n"
        "⚠️ 它还不是「使用记录」—— 被摆出来只是曝光，不是有用。\n"
        "只有被下钻的那一支才 promote 成 outcome = +1。\n"
        "⚠️ 缓冲不参与倾向计算（load 只读正式目录）。\n"
    )
    io.open(p, "w", encoding="utf-8", newline="\n").write(body)
    # 有界：**只列文件名**判超没超，超了才解析 —— 常态是 O(1)
    evicted = _evict(corpus_dir) if len(list(d.glob("*.md"))) > STAGE_CAP else []
    return {"ok": True, "path": str(p), "overwrote": existed,
            "n_shown": len(shown), "evicted": evicted}


def stage_load(corpus_dir: Any) -> list[dict]:
    """读缓冲区（一次查询一条）。解析不了的跳过，不静默。"""
    d = _stage_dir(corpus_dir)
    out: list[dict] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        try:
            t = io.open(p, encoding="utf-8", errors="replace").read()
            head = t.split("---", 2)[1]
            row: dict[str, str] = {}
            for line in head.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    row[k.strip()] = v.strip()
            out.append({"qfp": row.get("qfp", ""),
                        "at": row.get("at", ""),
                        "shown": [x for x in (row.get("shown") or "").split(",") if x],
                        "file": p.name})
        except Exception:                                    # noqa: BLE001
            continue
    return out


def promote(corpus_dir: Any, *, qfp: str, did: str, propensity: float,
            note: str = "drill", snapshot: str = "") -> dict:
    """缓冲 → 正式：把 `did` 标成 outcome = +1，并从这批的 `shown` 里移除。

    ★★ **不在缓冲里的 did 不许 promote** —— 否则就是把「翻到了」当成「用了」。
       这是与「不合成反馈」同一条线：信号必须来自真实动作。

    `snapshot` 透传给 `append`（§I7 的方向状态留痕）。
    """
    sp = _stage_dir(corpus_dir) / f"{_qfp8(qfp)}.md"
    if not sp.exists():
        return {"ok": False, "written": False,
                "why": "缓冲区里没有这次查询的展示批次（先 query 再 drill）"}
    cur = next((r for r in stage_load(corpus_dir) if r["file"] == sp.name), None)
    if cur is None or did not in cur["shown"]:
        return {"ok": False, "written": False,
                "why": "这一支不在这次查询的展示里，不记"}
    out = append(corpus_dir, did=did, outcome=1.0, propensity=propensity,
                 qfp=qfp, note=note, snapshot=snapshot)
    rest = [x for x in cur["shown"] if x != did]
    if rest:
        stage(corpus_dir, qfp=qfp, shown=rest, note="（已 promote 一支，剩余）")
    else:
        sp.unlink()
    return {**out, "remaining": len(rest)}


def clear(corpus_dir: Any) -> dict:
    """显式清空 —— 返回删了几个（**正式 + 缓冲都清**，两笔分开报）。

    ★ 这是唯一的删除路径，不做自动淘汰。
    """
    d = _dir(corpus_dir)
    n = s = 0
    if d.is_dir():
        for p in sorted(d.glob("*.md")):
            p.unlink()
            n += 1
    sd = _stage_dir(corpus_dir)
    if sd.is_dir():
        for p in sorted(sd.glob("*.md")):
            p.unlink()
            s += 1
    return {"ok": True, "deleted": n, "staged": s}


def status(corpus_dir: Any) -> dict:
    """只报不淘汰（沿用 `capacity_status` 的纪律）。**两层记忆分开报。**"""
    rows = load(corpus_dir)
    staged = stage_load(corpus_dir)
    per: dict[str, int] = {}
    for r in rows:
        per[r["did"]] = per.get(r["did"], 0) + 1
    no_at = sum(1 for r in rows if not r.get("at"))
    return {"记录数": len(rows), "覆盖方向数": len(per),
            "缓冲批次数": len(staged),
            "缓冲上限": STAGE_CAP,
            "缓冲里待 promote 的支": sum(len(r["shown"]) for r in staged),
            # ★ 两层记忆的名字（用户要求「给它们名字」）
            SHORT_TERM: {"批次数": len(staged), "上限": STAGE_CAP,
                         "待 promote 的支": sum(len(r["shown"]) for r in staged),
                         "遗忘方式": "容量淘汰最旧（自动）"},
            LONG_TERM: {"记录数": len(rows), "覆盖方向数": len(per),
                        "无时间戳（不衰减）": no_at,
                        "遗忘方式": f"半衰期 {HALF_LIFE_DAYS:g} 天**降权**，"
                                    "记录永不删（删除只能 clear）"},
            "目录": str(_dir(corpus_dir)),
            "最多的方向": sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))[:5]}


def main(argv: list[str] | None = None) -> int:
    import sys

    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) < 2:
        print("用法：python -m ldv.tools.usage_store <status|clear|list> <语料目录>")
        return 2
    cmd, corpus = a[0], a[1]
    if cmd == "status":
        print(json.dumps(status(corpus), ensure_ascii=False, indent=1))
    elif cmd == "clear":
        print(json.dumps(clear(corpus), ensure_ascii=False))
    elif cmd == "list":
        for r in load(corpus):
            print(f"  {r['file']:24s} {r['did']:10s} outcome={r['outcome']:+.1f} "
                  f"propensity={r['propensity']:.3f} {r['note']}")
    else:
        print(f"不认识的命令 {cmd!r}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
