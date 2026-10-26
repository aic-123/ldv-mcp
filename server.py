#!/usr/bin/env python3
"""ldv-mcp —— 把 ldv 内核包成一个 MCP 服务器（stdio，零第三方依赖）。

    python server.py            # 宿主（Claude / WorkBuddy / 其他 MCP 客户端）拉起它

## 它是什么（2026-10-24 砍过一次面）

**真人像平时一样用 agent，插件只提供两样东西：对 agent 的主体性，和一棵私人方向树。**

⇒ 四件工具，对应 agent 的四种动作，没有一步是给真人设计的：

    orient     「我记得」—— 私人语料 / 透镜 / 使用记录 / 最近动作
    ask        翻译后的查询（键校验 / 透镜自动冻结 / 支序原样 / 出处摘要）
    remember   往自己的私人语料写一个节点（只追加不覆写，filled_by 照记）
    drill      下钻 = 有用（agent 代钻与人的同权）

原先的七件（vocabulary / propose_spec / freeze_spec / spec_status / query /
usage_status）沉成内部机制：**透镜首次使用即自动冻结**，没有「提议 → 冻结」
两个动作；词汇表、声明状态、使用记录全并进 `orient`。
判据一条没少 —— 只是不再占工具面。

## 它比「随便包一层」多什么

    ① 查询键先过 `checks.validate` —— 未知键当场拒，不猜
    ② 支序原样中转 —— 重排即推荐，那是红线
    ③ 透镜走 `spec_store` —— agent 生成、**首次使用即自动冻结**、带理由与语料指纹
    ④ 使用记录照旧两段通道 —— 查询只进缓冲，下钻才 promote
    ⑤ 每次调用落日志 —— 隐形只在对话里，不在日志里；翻译层可接管

## 语料布局（产品说了算的那一半）

    <store>/_data/<名字>/nodes/*.md      私人语料
    <store>/_data/<名字>/usage/          使用记录（内核只在 `_data` 下启用，
                                          那是它防「把运行时产物写进仓库」的守卫；
                                          产品把自己的 store 做成那个形状，就不用改内核）
    <store>/specs/<名字>/                冻结的声明 + 候选 + 账本

`corpus` 参数给**名字**（在 store 下找）或**路径**（指向别处的语料；
那种语料不在 store 下 ⇒ 使用记录不启用，产品如实报「不启用」，不假装）。
不给 ⇒ 内置 36 项（只读，不写使用记录）。
"""
from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "core"))          # 钉住的内核
sys.path.insert(0, str(HERE))                   # 产品自己的包

from checks import lock as L                    # noqa: E402
from checks import session as SESS             # noqa: E402
from checks import validate as V                # noqa: E402
from spec_store import store as S               # noqa: E402

#: 产品自己的 store（可用环境变量覆盖）
STORE = Path(os.environ.get("LDV_MCP_STORE", str(HERE / "store")))
#: ★ 协议版本要**协商**，不能写死（2026-10-25，官方 SDK 实测发现）：
#: 原来写死 `2024-11-05`，新版客户端发 `2025-06-18` ⇒ 收到不支持的版本就**断开**
#: （MCP 规范：client 不支持 server 回的版本时必须断开）。实测现象是
#: 「握手时 Connection closed」，而 server 本身没崩（手工喂同一消息能正常回）。
#:
#: 规范的正确做法：client 发它支持的版本，**server 回 client 发来的那个**
#: （前提是自己也支持）；都不支持时回自己最高的，让 client 决定要不要继续。
PROTOCOL_VERSION = "2025-06-18"          # 自己支持的最高
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "ldv-mcp"


def _server_version() -> str:
    """版本号**现算**（读 `core/PINNED`），不手写 —— 手写的迟早会漂。

    （这条是本项目踩过的：README 里「21 份文档」漂成 23、`SERVER_VERSION`
    还停在 `0.1.0` 而产品已经到 v0.1.44。）
    """
    try:
        for line in (Path(__file__).resolve().parent / "core" / "PINNED").read_text(
                encoding="utf-8").splitlines():
            if line.strip().startswith("版本"):
                return line.split()[-1].lstrip("v")
    except OSError:
        pass
    return "0"

#: 调用日志（agent 的审计轨迹）—— 一行一次工具调用，**只追加**。
#:
#: ★ 为什么要它：agent 是「第三方隐形使用者」（用户 2026-10-24 定的），
#:   隐形只体现在**对话里**，不体现在**日志里**。人随时能查「你替我过了哪些招」，
#:   并且能随时接管翻译层 —— 看不见的翻译 + 可接管的日志，才既快又不锁死。
#: ★ `actor` 是**调用方声明的**，不是服务器验的（服务器没有身份可验）——
#:   按 `filled_by` 的同一条纪律：**声明照记，不当证明**。
CALL_LOG = STORE / "log" / "mcp-calls.jsonl"


def _tail(path, n: int = 5) -> list[str]:
    """从文件**尾**读最后 n 行，不整篇读进内存。

    ⚠️ 旧写法 `list(io.open(path))[-n:]` 为了 5 行把整个日志读进来 ——
       日志只追加、无轮转 ⇒ 越用越慢，而 `orient` 是每问都要调的
       （外部评审 A4，2026-10-25 修）。
    """
    block, data = 4096, b""
    try:
        with io.open(path, "rb") as f:
            f.seek(0, 2)                      # 2 = 文件尾
            size = f.tell()
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[-n:]


def _atomic_write(path: Path, data: str, retries: int = 20) -> None:
    """原子覆写：写临时文件 + `os.replace`，**带重试**。

    ⚠️ **Windows 上 `os.replace` 会因读者持着目标句柄被拒**（`PermissionError
    [WinError 5]`）—— 实测写者 400 次有 350 次被拒（`DEAD-ENDS §1.1` 的裁定，
    证据 `outputs/_probe_win_replace_vs_read.py`）。所以**必须配重试**，
    「一次 replace 就完事」在 Windows 上是错的。
    ⚠️ 只有**缓存**用它（覆写是缓存的正常语义）；记忆节点仍走 `create_exclusive`
    （只追加不覆写 —— 覆写记忆就是改写历史）。
    """
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
        f.flush()
    import time

    for i in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == retries - 1:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.02 * (i + 1))


def _log_call(tool: str, args: dict, actor: str, summary: dict) -> None:
    """一次工具调用一行。写不进去**不炸**（日志是审计件，不是判据）——
    但也不静默：返回写没写成，调用方可以报。"""
    CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now().isoformat(timespec="seconds"),
           "tool": tool, "actor": actor,
           # ★ 会话要**单独成列**（2026-10-26）：变更回执要按会话聚合，
           #   而原来只有 `args.session`，写操作根本不收这个参数 ⇒ 归不了。
           #   单独成列之后，即使某个工具将来漏了 session，也一眼看得出来（空串）。
           "session": str(args.get("session") or ""),
           "args": {k: v for k, v in args.items() if k != "actor"},
           "summary": summary}
    try:
        with io.open(CALL_LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def _change_receipt(sid: str) -> dict:
    """**本会话改了哪几处** —— 变更回执（2026-10-26）。

    ⚠️ 与 `recent_calls` 的分工（这正是它存在的理由）：
       `recent_calls` 只取最近 **5 行**，而且**混着读操作**（`orient` / `ask` 把写稀释掉）。
       回执**只收写**，并按会话归属 —— 让人一眼看见 agent 动了什么，不必自己拼日志。

    ⚠️ **读操作不进回执**：`orient` / `ask` 不改任何东西，混进来就是噪声。
    ⚠️ **降权算变更**：`drill(verdict="wrong")` 改的是倾向，不是读。
    ⚠️ 只收**成功**的写（`written` / `ok`），失败的尝试不进 ——
       否则「写失败三次」会和「写成功一次」长得一样。
    """
    out: dict = {"新写": [], "造维度": [], "编译答案": [], "降权支": [],
                 "合计": 0}
    if not CALL_LOG.is_file():
        return out
    try:
        lines = io.open(CALL_LOG, encoding="utf-8", errors="replace").readlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if sid and (row.get("session") or "") != sid:
            continue
        tool = row.get("tool")
        a = row.get("args") or {}
        sm = row.get("summary") or {}
        if tool == "remember" and sm.get("written") is True:
            v = a.get("id")
            if v and v not in out["新写"]:
                out["新写"].append(v)
        elif tool == "derive" and sm.get("ok") is True:
            v = a.get("name")
            if v and v not in out["造维度"]:
                out["造维度"].append(v)
        elif tool == "remember_answer" and sm.get("written") is True:
            v = sm.get("key")
            if v and v not in out["编译答案"]:
                out["编译答案"].append(v)
        elif tool == "drill" and a.get("verdict") == "wrong" and sm.get("ok") is True:
            v = sm.get("did")
            if v and v not in out["降权支"]:
                out["降权支"].append(v)
    out["合计"] = sum(len(v) for k, v in out.items() if isinstance(v, list))
    return out


def _actor(args: dict) -> str:
    """调用方声明的演员。默认 `agent`（MCP 的调用方按定义就是 agent 宿主）。"""
    return str(args.get("actor") or "agent")


#: 只有「人」有资格授予的**状态**（2026-10-26 用户定）。
#:
#: ⚠️ 注意这里**只有 `evidence_status`，没有 `filled_by`** —— 这是用户纠正过一次的分界：
#:   `filled_by` 是**来源标注**（谁填的），`evidence_status` 是**状态结论**（验没验过）。
#:   「使用」的含义是**不看来源**：agent 填的数据照样进、照样查、照样用，
#:   不因为它不是人填的就降级或拒收。
#:   而「人的填充给特殊地位」体现在：**`human` 是唯一能承载「已验证」的来源**。
#:   ⇒ 标注不设闸，**特权设闸**。把标注也做成闸，等于「使用」前先查资格 —— 那就不是使用了。
HUMAN_ONLY_VALUES: dict[str, frozenset[str]] = {
    "evidence_status": frozenset({"已验证"}),
}


def _stamp_provenance(actor: str, fields: dict, filled_by: str) -> str:
    """把 `filled_by` 当**通道盖的章**，不是调用方填的表。

    三条，一条比一条重要：

    1. **`filled_by` 只能记成你自己的 `actor`。** 传一个跟 `actor` 不一样的来源 ⇒ 拒
       （不是「你没资格写 human」，是「你不能替别人声明来源」）。
    2. **`evidence_status=已验证` 只有 `actor=human` 能给。** 这是那条「人的填充给特殊地位」
       的落点：`human` 是**唯一能承载「已验证」的来源**。agent 的产物永远是「未验证」。
    3. **其余一律放行** —— agent 写什么内容、标什么来源（自己的），都不设闸。
       「使用」不看来源，所以这里不做任何「先审后用」。

    ⚠️ 为什么 2 不能靠「源码里没有那个字面量」守：那条判据（`[外部授予]`）只证明
       **系统不会自己写**；走 `fields` 数据通道时源码里一个该字面量都没有 ⇒ 判据照样绿。
       这个闸补的正是那一半（`S42a`）。
    """
    if isinstance(filled_by, str) and filled_by and filled_by != actor:
        raise ValueError(
            f"filled_by 是**通道盖的章**，不能自己指定 —— 你声明的是 actor={actor}，"
            f"却写 filled_by={filled_by}。不传就记成 {actor}；"
            "要标成人的来源，得由人（actor=human）自己调")
    ev = (fields or {}).get("evidence_status")
    if isinstance(ev, str) and ev in HUMAN_ONLY_VALUES["evidence_status"]:
        if actor != "human":
            raise ValueError(
                f"「{ev}」是**人的档** —— 你声明的是 actor={actor}，给不了这个状态。"
                "你的结论照样能写、能查、能用（不传 evidence_status 即可，默认「未验证」）；"
                "要人授予：请调用方以 actor=human 重新调用")
    return filled_by or actor


def _cache_stats(corpus: str) -> dict:
    """「命中率」有三个口径 —— 分开报，混着说就是含糊（2026-10-26 用户要的）。

    | 口径 | 算法 | 它回答什么 |
    |---|---|---|
    | **答案缓存命中率** | 命中数 / `ask` 数 | 省了多少次问 agent |
    | **缓冲曝光命中率** | `promoted` / 摆出的支数 | 摆出来的支多少**被证明有用** |
    | **漏检率** | 重新提到 / 挤出数 | 会话缓冲挤错了多少 |

    前两个从 `log/mcp-calls.jsonl` 算，第三个在 `sessions` 里（不重复算）。

    ⚠️ **老日志没有 `cached` 字段 ⇒ 只统计有该字段的行。**
       拿「没记」当「没命中」会把命中率算低 —— 与「有字段 ≠ 有读数」同一类坑。
    ⚠️ **它是复用率，不是智能指标**：命中率完全取决于**问题重复率**
       （同一问两遍 = 100%，每问都不同 = 0%）。别拿它当能力读数。
    """
    asks = hits = 0
    if CALL_LOG.is_file():
        try:
            for line in io.open(CALL_LOG, encoding="utf-8", errors="replace"):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("tool") != "ask":
                    continue
                a = row.get("args") or {}
                if corpus and a.get("corpus") != corpus:
                    continue
                s = row.get("summary") or {}
                if "cached" not in s:
                    continue                     # 老日志：不记 ≠ 没命中
                asks += 1
                if s.get("cached"):
                    hits += 1
        except OSError:
            pass
    return {"ask 数": asks, "命中数": hits,
            "答案缓存命中率": (round(hits / asks, 3) if asks else None),
            "note": ("命中率 = 命中 / ask。**None = 还没有带 `cached` 字段的 ask**"
                     "（不是 0，老日志不计）。这是**复用率**，取决于问题重复率")}


def _provenance(app, answer_ids) -> dict:
    """答案的**出处摘要** —— 三类分开数，不藏混合物。

    节点 front matter 的 `filled_by` 决定它属于哪类：
    人写的 / 模型填的 / 外部授予的 / 没标（**没标就是没标，不猜**）。
    ⚠️ 这不是装饰：agent 被允许起草内容（用户放开了「禁止模型代填」），
       所以「查到的 36 条里几条是模型填的」必须是**每次都报**的数。
    """
    buckets: dict[str, int] = {}
    items: list[dict] = []
    for nid in answer_ids:
        node = app.nodes.get(nid)
        fb = ""
        if node is not None:
            fb = str((node.fields or {}).get("filled_by", "") or "")
        key = fb or "未标"
        buckets[key] = buckets.get(key, 0) + 1
        items.append({"id": nid, "filled_by": key})
    return {"buckets": buckets, "n": len(items), "items": items,
            "note": "三类分开可筛；「未标」= front matter 里没有 filled_by，不猜"}


# ── 语料解析 ────────────────────────────────────────────────────────────────

def _corpus_dir(name_or_path: str) -> tuple[Path | None, bool]:
    """返回 (nodes 目录, 是否在产品 store 下)。None ⇒ 用内置语料。"""
    if not name_or_path:
        return None, False
    p = Path(name_or_path)
    if p.is_absolute() or (p / "nodes").is_dir() or p.is_dir():
        cand = p if p.is_dir() else None
        if cand is not None:
            if (cand / "nodes").is_dir() and cand.name != "nodes":
                cand = cand / "nodes"
            return cand, "_data" in cand.resolve().parts
    in_store = STORE / "_data" / name_or_path / "nodes"
    if in_store.is_dir():
        return in_store, True
    raise ValueError(f"语料 {name_or_path!r} 不在产品 store 下，也不是一个目录"
                     f"（试过 {in_store}）")


#: ★ 空库**不需要占位文件**（2026-10-25 用户定）。
#:
#: 原来这里放一条 `_keystone.md`（内核当时把空目录当「找不到语料」）。
#: 现在内核 `load_nodes` / `find_corpus` 都接受空语料 —— **目录存在就是空库，
#: 目录不存在才是路径错**。所以：
#:   · 不再往用户的语料目录里写任何占位文件；
#:   · 空库就是 0 项，`ask` 回 `n_answer=0` / `stopped=empty`（说清「这一格是空的」）；
#:   · 老 store 里如果已经有 `_keystone.md`，**不删**（它不参与任何查询，留着无害）——
#:     删用户数据不是这一层该做的决定。


def _app(corpus: str, session: str = ""):
    """按名字/路径建一个 entry.App。内置语料 ⇒ App(None)。

    ★ `session` 只在 `config.json` 写了 `isolate_usage: true` 时才动使用记录
      —— 默认**跨会话累积**（保持已有行为不变）。改默认值等于悄悄改变
      老用户看到的结果，所以隔离是**选项**，不是默认。
    """
    import entry as E

    nodes, in_store = _corpus_dir(corpus)
    if nodes is None:
        app = E.App(None)
        return app, True
    # ★ 空库直接起（2026-10-25）：不再写占位文件 —— 内核现在接受 0 项语料，
    #   而「目录不存在」仍会报错（`find_corpus` 用 `is_dir()` 判）。
    os.environ["LDV_CORPUS"] = str(nodes)
    app = E.App(None)
    if session and in_store and SESS.config(nodes.parent)["isolate_usage"]:
        # 把使用记录改到会话目录：__init__ 已经装过一遍，先清再重装
        app._usage_dir = SESS.sdir(nodes.parent, session) / "usage"
        app._tendency = {}
        app.usage_loaded = 0
        for k in app.kernels.values():
            try:
                k.usage.clear()
            except AttributeError:
                pass
        app._load_usage()
    return app, in_store


def _spec_dir(corpus: str) -> Path:
    """声明放在 <store>/specs/<名字>/。内置语料也有名字（builtin）。"""
    nodes, _ = _corpus_dir(corpus)
    name = nodes.parent.name if nodes is not None else "builtin"
    return STORE / "specs" / name


# ── 工具实现：四件，对应 agent 的四种动作 ─────────────────────────────────
#
# ★ 2026-10-24 用户砍过一次面：「真人唯一要做的就是像正常一样用 agent，
#   插件只提供两样东西 —— 对 agent 的**主体性**，和一棵**私人方向树**。」
#   ⇒ 真人对插件零动作；agent 也不需要仪式。原先七件工具里的
#     「提议 → 冻结 → 查 → 钻 → 状态」沉成内部机制：
#     · 透镜**首次使用即自动冻结**（没有 propose/freeze 两个动作）
#     · 词汇表 / 声明状态 / 使用记录 / 最近轨迹 全并进 `orient`
#     · 判据一条没少（键校验 / 支序原样 / 出处 / 日志），只是不再占工具面

def _safe_name(s: str) -> str:
    """文件名安全化（节点 id 可能有斜杠、空格、中文）。"""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(s))


def _value_counts(app) -> dict:
    """枚举字段的**取值分布**：`type` / `evidence_status` / `source.kind` 各有几个值、各多少节点。

    ★ 为什么 orient 要给这个（2026-10-24 有无插件对比实验的直接产物）：
      无插件时，agent 把语料里的「判断点」说成「议题」「立场」—— 词都对、
      分类错。根因是它**不知道这份语料有自己的类型体系**（`type` 有 7 个取值）。
      键名（`type`）不等于体系（`type=议题` / `type=判断点` / …）。
      ⇒ 把取值分布摆出来，agent 提问时才知道该用哪个桶。
    """
    fields = ("type", "evidence_status", "source")
    out: dict[str, dict[str, int]] = {}
    for f in fields:
        counts: dict[str, int] = {}
        for node in app.nodes.values():
            v = (node.fields or {}).get(f)
            if isinstance(v, dict):
                v = v.get("kind")
            if isinstance(v, str) and v:
                counts[v] = counts.get(v, 0) + 1
        if counts:
            out[f] = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return out


def _orient_all() -> dict:
    """不给 ldv 名 ⇒ 列出这个 store 下**所有** ldv。

    ★ 多个 ldv 是允许的：一个 ldv = 一套独立的语料 / 透镜 / 记忆（一个「认识」），
      一个 ldv 下可以有多会话。这里只**数文件不建内核** —— 列目录不该付建树的代价。
    """
    base = STORE / "_data"
    rows: list[dict] = []
    if base.is_dir():
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            nodes = d / "nodes"
            if not nodes.is_dir():
                continue
            rows.append({"ldv": d.name,
                         "n_items": len(list(nodes.glob("*.md"))),
                         "sessions": len(SESS.sessions(d)),
                         "has_lens": (STORE / "specs" / d.name / "view_spec.json").is_file(),
                         "derived": sorted(p.stem for p in (d / "derived").glob("*.json"))
                         if (d / "derived").is_dir() else []})
    return {"ok": True, "ldv_count": len(rows), "ldvs": rows,
            "note": "多个 ldv = 多套独立的语料/透镜/记忆；一个 ldv 下可有多个会话。"
                    "给 corpus 名字再看某个 ldv 的细节"}


def _tool_orient(a: dict) -> dict:
    """agent 的「**我记得**」—— 私人 store 的全貌。会话开始调一次。

    这一件就是「主体性」的接口：私人语料、冻结的透镜（带理由与指纹）、
    使用记录摘要、最近几次自己的动作。没有它，agent 每次会话都是从零开始的
    陌生人；有了它，它有一个**结构上可查**的过去。
    """
    corpus = a.get("corpus", "")
    sid = str(a.get("session") or "")
    if not corpus:
        return _orient_all()
    nodes, in_store = _corpus_dir(corpus)
    app, _ = _app(corpus, sid)
    out: dict = {"ok": True, "corpus": app.corpus_name,
                 "n_items": len(app.nodes), "n_keys": len(app.all_keys),
                 "keys": sorted(app.all_keys),
                 # ★ 取之分布（2026-10-24 有无插件对比实验发现的缺口）：
                 #   无插件时 agent 把「判断点」说成「议题/立场」—— 词都对、
                 #   分类错，因为它不知道**这份语料有自己的类型体系**。
                 #   ⇒ orient 不只给键名，还给「这个键有哪几个取值、各多少节点」。
                 "value_counts": _value_counts(app),
                 "usage_enabled": app._usage_dir is not None and in_store}
    if nodes is not None:
        st = S.status(_spec_dir(corpus), nodes)
        out["derived"] = dict(app.derived or {})   # agent 敲定的派生维度（field → N 项）
        # ★ 派生维度的**元数据**也报（2026-10-26）：`derive` 强制要求
        #   `rationale` / `derived_from`，原来它们写进 sidecar 后没人读 ——
        #   「这一栏决定答案里敢不敢把它当事实」这句话当时没有代码兑现。
        #   这里原样透传，不加工、不判断（判据 `S44` 守这一点）。
        out["derived_meta"] = dict(getattr(app, "derived_meta", {}) or {})
        out["lens"] = {"frozen": st.get("frozen"), "id": st.get("id"),
                       "generated_by": st.get("generated_by"),
                       "rationale": st.get("rationale"),
                       "stale": st.get("stale"),
                       "corpus_fingerprint": st.get("corpus_fingerprint")}
        if st.get("stale"):
            out["lens_note"] = ("语料变过，这份透镜是对着旧指纹提的 —— 它**仍可用**"
                                "（结构保证不依赖作者），但该重新提一个")
        if app._usage_dir is not None and in_store:
            from ldv.tools import usage_store as US

            us = US.status(app._usage_dir)
            out["usage"] = {k: us.get(k) for k in
                            ("记录数", "覆盖方向数", "缓冲批次数", "最多的方向")}
            out["actor_split"] = _actor_split(app)
        # ★ 会话：缓冲 / 已 promote / 挤出 / 漏检率（报数不判断）
        out["sessions"] = SESS.sessions(nodes.parent)
        if sid:
            out["session"] = SESS.status(nodes.parent, sid)
    recent: list[dict] = []
    if CALL_LOG.is_file():
        for line in _tail(CALL_LOG, 5):
            try:
                recent.append(json.loads(line))
            except ValueError:
                continue
    out["recent_calls"] = recent
    # ★ 变更回执：**本会话改了哪几处**（2026-10-26）。
    #   与 `recent_calls` 分工：那个只取 5 行且混着读操作，这个只收写、按会话归属。
    ch = _change_receipt(sid)
    ch["scope"] = (f"session:{sid}" if sid
                   else "全部历史（**没带 session ⇒ 这不是「本次」**）")
    out["changes"] = ch
    # ★ 命中率（三个口径之一，另两个在 sessions 里）—— 2026-10-26
    out["cache"] = _cache_stats(app.corpus_name)
    out["note"] = ("这是你的私人状态：语料、透镜、记得什么、最近做了什么。"
                   "真人不需要看它 —— 但你可以随时把它讲给人听")
    return out


_ACTOR_CACHE: dict = {"sig": None, "val": None}   # (size, mtime) → 分解读数


def _actor_split(app) -> dict:
    """正式记录里多少条来自 agent 声明的下钻（读数，不打分不拦截）。

    ⚠️ 这一处**语义上要全量**（分解是整个历史，不是最近几条）⇒ 不能倒读。
       外部评审 A4 顺带查到它：日志线性增长时它也线性变慢。缓解 = 按
       (size, mtime) 缓存 —— 同一份日志重复调用不重算；跨调用仍随日志增长，
       这是已知的线性点，写在这里不假装没有。
    """
    from ldv.tools import usage_store as US

    agent_q: set[tuple[str, str]] = set()
    try:
        st = CALL_LOG.stat()
        sig = (st.st_size, st.st_mtime_ns)
    except OSError:
        sig = None
    if sig is not None and _ACTOR_CACHE.get("sig") == sig:
        return _ACTOR_CACHE["val"]
    if CALL_LOG.is_file():
        for line in io.open(CALL_LOG, encoding="utf-8"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            # ★ 只收 actor 声明为 agent 的下钻（第一版忘过滤 ⇒ 分解失去意义）
            if row.get("tool") == "drill" and row.get("actor") == "agent":
                agent_q.add((str(row.get("summary", {}).get("qfp", "")),
                             str(row.get("summary", {}).get("did", ""))))
    rows = US.load(app._usage_dir)
    n_agent = sum(1 for r in rows if (r.get("qfp", ""), r.get("did", "")) in agent_q)
    val = {"记录数": len(rows), "agent 声明的下钻": n_agent,
           "其余（人 / 未声明）": len(rows) - n_agent,
           "note": "actor 是调用方**声明**的，服务器不验（同 filled_by）"}
    if sig is not None:
        _ACTOR_CACHE["sig"], _ACTOR_CACHE["val"] = sig, val
    return val


def _tool_ask(a: dict) -> dict:
    """把人的问题翻过来，在树上查一支。

    内部顺序（全是机制，不是流程）：
        ① 键校验    未知键当场拒 —— 在入口**之前**
        ② 透镜      没有冻结的 ⇒ 本次带了 `lens` 就**自动冻结**（首次使用即冻结），
                    之后只读文件；带了的插件名先过白名单
        ③ 支序原样  重排即推荐，可判
        ④ 出处摘要  {语料/agent填/已验证} 三类分开数
    """
    corpus = a.get("corpus", "")
    sid = str(a.get("session") or "")
    app, in_store = _app(corpus, sid)
    req = list(a.get("require") or [])
    forb = list(a.get("forbid") or [])

    chk = V.check_keys(app.all_keys, req, forb)
    if not chk["ok"]:
        return {"ok": False, "out_of_structure": True, **chk}

    nodes_dir, _ = _corpus_dir(corpus)
    spec = S.load(_spec_dir(corpus)) if nodes_dir is not None else None
    lens = a.get("lens") or {}
    if spec is None and lens and nodes_dir is not None:
        # ★ 自动冻结：agent 给了透镜就冻，**没有第二步动作**。
        #   理由必填 —— 说不清为什么这么分，就不算一个透镜。
        #   ⚠️ 跨进程：propose+freeze 是一个「先查再写」的复合动作，必须持锁
        #      —— 否则两个会话各冻一份，后写的赢且没人知道。
        with L.store_lock(STORE):
            if S.load(_spec_dir(corpus)) is None:
                cand = S.propose(_spec_dir(corpus), lens,
                                 rationale=str(a.get("rationale") or ""),
                                 generated_by=_actor(a), corpus_dir=nodes_dir)
                S.freeze(_spec_dir(corpus), cand["candidate_id"], corpus_dir=nodes_dir)
        spec = S.load(_spec_dir(corpus))
    # ★ 已有冻结透镜、又传了**不同**的 lens、还没 force ⇒ **必须报错**
    #   （2026-10-25，真实使用发现的：原来直接忽略，静默用旧插件 ——
    #    调用方以为换了切分方式，其实答案来自旧的那份。静默改语义 = 最坏的一种。）
    if spec is not None and lens and not a.get("force"):
        cur = spec.get("spec") or {}
        diff = {k: v for k, v in lens.items() if cur.get(k) != v}
        if diff:
            return {"ok": False, "lens_conflict": True,
                    "frozen_lens_id": spec.get("id"),
                    "diff": diff,
                    "error": (f"已有冻结透镜（id={spec.get('id')}），这次传的 {sorted(diff)} "
                              "与它不同 —— 要换请显式 force=true"
                              "（换透镜会让切分变，新旧答案不可比）"),
                    "next_step": ["force=true 换透镜（旧的作废）",
                                  "或去掉 lens 参数，用现有的那份"]}

    plugin = ""
    if spec:
        plugin = (spec.get("spec") or {}).get("插件", "")
    if a.get("plugin"):
        plugin = str(a["plugin"])
    if plugin:
        V.check_plugin(plugin)

    q = {"require": req, "forbid": forb}
    if plugin:
        q["plugin"] = plugin
    # ★ 接口截断（外部评审 A2，2026-10-25 接上）：0 = 不截（默认，与从前逐字相同）。
    #   截断时 entry 一并给 branches_total/shown/hidden 让账平 ——
    #   只截不给账，客户端会把「Σ各支 < n_answer」读成切分漏了。
    if a.get("max_branches"):
        q["max_branches"] = int(a["max_branches"])
    # ★ 支级跳过（外部评审 C2，2026-10-25 接上）：`reject_below` = 倾向低于它就
    #   不摆出来（人用 drill(verdict=wrong) 判错的支）。**阈值外生**（调用方声明），
    #   不传 ⇒ 不跳。跳过必带差额（`skipped_rejected` / `rejected_items`）——
    #   不许静默改变候选集；查询 require 含该支 must 时**不跳**（明确问到）。
    if a.get("reject_below") is not None:
        q["reject_below"] = float(a["reject_below"])
    r = app.query(q)
    if not r.get("ok"):
        return r

    nodes = r.get("nodes") or []
    # ★ 支序原样的**运行时闸门**（2026-10-25 修，外部评审 A1）：
    #   两个入参必须来自**不同来源** —— 内核原始序列 vs 中转层实际交给下一环的序列。
    #   旧写法是同一个列表推导跟自己比 ⇒ 恒真 ⇒ 这道闸**结构上不可能开火**，
    #   README 里「支序原样」因此只剩测试层（S10）保证，运行时那一层是空的。
    raw_dids = [n["did"] for n in (r.get("nodes") or [])]
    # ★ 本次查询用到了哪些**派生维度**（agent 建的轴，不是材料写的）——
    #   必须报：查 `topic=DAPO` 与查 `type=立场` 的证据等级不同。
    dfields = set(app.derived or {})
    derived_used = sorted({k for k in (req + forb)
                           if k.split("=", 1)[0] in dfields})
    # ★ 会话缓冲：这次**摆出了哪几支**（曝光，不是「有用」）——
    #   与内核两段通道同构：查询进缓冲，下钻才 promote。作用域按会话。
    sess_info: dict = {}
    if sid and nodes_dir is not None:
        # 中转点①：曝光给会话缓冲的序列，必须与内核逐字相同
        exposed = [n["did"] for n in nodes]
        V.check_order(raw_dids, exposed)
        sess_info = SESS.expose(nodes_dir.parent, sid, qfp=app._last_qfp,
                                dids=exposed,
                                n_answer=r.get("n_answer") or 0)
    # ★ 读正文（2026-10-24 并进 ask：读内容是查询的一个参数，不是新工具）
    # ★★ 默认只交**正文 + 句柄**，id 与结构留在本地（用户 2026-10-24 定：
    #    「只是减少重复，记录依旧保存在 ldv 之中，agent 拿到这些也没用」）。
    #    ⇒ 默认**不给** answer_ids / nodes / must / mustnot / coverage / precision。
    #    要看结构（调试、判断「该不该换个问法」）显式 `structure=true`。
    limit = max(1, int(a.get("limit") or 5))
    # ⚠️ `max_chars=0` 不能走 `or 2000` —— 0 是 falsy，会被**静默换成默认值**，
    #   于是「我只要 0 字」变成「给我 2000 字」（真实流程探针 E05 抓到）。
    #   显式给 0 ⇒ 正文空（没截，是没要），与「截断必报」是两件事。
    max_chars = (max(0, int(a["max_chars"])) if a.get("max_chars") is not None
                 else 2000)
    with_body = a.get("with_body", True)          # 正文是主产物 ⇒ 默认给
    ids_sorted = sorted(r.get("answer_ids") or [])
    items: list[dict] = []
    meta: list[dict] = []
    for i, nid in enumerate(ids_sorted[:limit]):
        node = app.nodes[nid]
        f = node.fields or {}
        body = (node.body or "").strip()
        title = str(f.get("title", "") or "")
        items.append({"n": i + 1, "title": title,
                      "text": body[:max_chars] if with_body else "",
                      "source": f.get("source", ""),
                      "filled_by": str(f.get("filled_by", "") or ""),
                      # ★ 节点的 front matter（**除了 id 与 title**）：
                      #   不给它，agent 读不出 project 这类自定义字段，
                      #   也就没法把「按项目分组」翻成一个可查的维度。
                      "fields": {k: v for k, v in f.items()
                                 if k not in ("id", "title")},
                      # ⚠️ `max_chars=0` 是「我只要 0 字」，不是「截断了」——
                      #   没少给东西，是没要。`truncated` 只在**真截掉内容**时为真。
                      "truncated": bool(with_body and max_chars > 0
                                        and len(body) > max_chars)})
        meta.append({"id": nid, "title": title})
    if nodes_dir is not None and meta:
        # 句柄表：本地记「第 n 条是哪一条」，agent 只看到 n
        SESS.save_handles(nodes_dir.parent, meta, qfp=app._last_qfp,
                          require=req, forbid=forb)
    out = {"ok": True, "corpus": app.corpus_name, "actor": _actor(a),
           "session": sess_info,
           "n_answer": r.get("n_answer"),
           # ★ 支级跳过 + 项级否定的读数（默认就给 —— 「答案少了」必须在默认路径可见）
           "skipped_rejected": r.get("skipped_rejected") or [],
           "rejected_items": r.get("rejected_items") or 0,
           "answer_verdicts": r.get("answer_verdicts") or {},
           # ★ 平账三字段（默认也给）：`branches_hidden > 0` 时说明**还有支没摆出来**，
           #   调用方据此知道「不是只有这些」—— 与 items 的「还有 K 条没给」同一条规矩。
           "branches_total": r.get("branches_total"),
           "branches_shown": r.get("branches_shown"),
           "branches_hidden": r.get("branches_hidden"),
           "items": items,
           # ★ 三条「不许猜」的规矩（2026-10-24 用户定）
           #   ① 多条同时能回答 ⇒ **都列出来**，让 agent 兼并后给出回答，
           #      不许挑一条当「真的」（挑 = 猜）
           #   ② 缓冲只记顺序编号 ⇒ 超出的就说「顺序不记了」
           #   ③ 这一格是空的 ⇒ 可能是材料里没有，也可能是**维度不存在**；
           #      后者交给 agent 补（derive / 换问法），不许拿别的维度顶上
           "note": ("id 与结构留在本地：动作说「第 n 条」（drill/undo 的 n）即可；"
                    "要看分支与谓词用 structure=true。"
                    "**多条同时能回答时，把它们都列出来** —— 不许挑一条当真的"),
           "query": {"require": req, "forbid": forb, "plugin": plugin or "keyset"},
           "derived_used": derived_used,
           # ★ 每个用到的派生维度**从哪来**（2026-10-26）：`derived_note` 原来只会
           #   笼统说「当线索用，别当事实」—— 而 `derived_from` 里早就写着
           #   「原材料」还是「模型推断」，这两者该不该当事实**完全不同**。
           #   ⚠️ **只给 `derived_from` 一个值**：完整 `_meta`（what/why/generated_by/
           #      derived_at/file）在 `orient.derived_meta` 里，那是**一次一调**的地方。
           #      第一版在这里塞了整份 `_meta` ⇒ 4 问多 266 tokens（实测），
           #      而判断「敢不敢当事实」只需要这一个字段。
           "derived_provenance": {
               f: ((getattr(app, "derived_meta", {}) or {}).get(f) or {})
                  .get("derived_from")
               for f in sorted({k.split("=", 1)[0] for k in derived_used})},
           "derived_note": ("本次用到的维度里，这些是 **agent 派生的**"
                            "（不是材料里写的）—— 看 `derived_provenance` 的 "
                            "`derived_from`：写「原材料」的可当材料，写「模型推断」的"
                            "只能当线索"
                            if derived_used else ""),
           "provenance": _provenance(app, r.get("answer_ids") or []),
           "usage_enabled": app._usage_dir is not None and in_store}
    # ★ `limit` 截断必须**明说**（2026-10-25，真实使用发现的）。
    #   原来条件是 `not items and n_answer` ⇒ **只有 items 为空时才报**，
    #   于是「3 条答案只给 1 条」静默通过 —— 调用方以为只有 1 条。
    #   与 `max_chars` 的 `truncated` 是同一条纪律：**少给东西必须说**。
    n_ans = int(r.get("n_answer") or 0)
    if len(items) < n_ans:
        out["items_note"] = (f"答案有 {n_ans} 条，这里只给了 {len(items)} 条"
                             "（limit 卡住了）—— 要更多就放大 limit，"
                             "**别拿没给的当不存在**")
        out["items_truncated"] = True
    elif not items:
        out["items_note"] = ("这一格是空的 —— 要么材料里真没有，要么**这个维度不存在**；"
                             "后者交给 agent 补（derive 一个维度 / 换问法），不许猜")
    # ★ 答案缓存：先查树（级联的「便宜那一级」）。命中 ⇒ 不必再问 agent。
    #   ⚠️ 命中必须**明说**：from_cache=true + filled_by: agent，
    #      不混在材料答案里 —— 那是语义缓存的已知坑（相似问题误命中）。
    if nodes_dir is not None:
        cached, why = _cache_lookup(app, nodes_dir, req, forb,
                                    plugin or "keyset")
        if cached:
            out["cached_answer"] = {
                "from_cache": True, "answer": cached.get("answer"),
                "filled_by": cached.get("filled_by"),
                "generated_by": cached.get("generated_by"),
                "derived_from": cached.get("derived_from"),
                "source": cached.get("source") or [],
                "key": cached.get("qfp"),
                "note": ("这是**上次编进树的回答**，不是新材料 —— "
                         "要更新就重新 remember_answer（同键覆写）")}
        elif why:
            out["cache_note"] = why
    if a.get("structure"):
        out.update({"nodes": nodes, "answer_ids": ids_sorted,
                    "n_branches": r.get("n_branches"),
                    "branches_total": r.get("branches_total"),
                    "branches_shown": r.get("branches_shown"),
                    "branches_hidden": r.get("branches_hidden"),
                    "ordered_by": r.get("ordered_by"),
                    # ★ 支级跳过的读数（外部评审 C2）：跳了哪些支、因此少了几项。
                    #   `rejected_items > 0` 时答案比不跳时少这么多 —— 差额可算。
                    "skipped_rejected": r.get("skipped_rejected") or [],
                    "rejected_items": r.get("rejected_items") or 0,
                    # ★ 项级否定（外部评审 C1）：只标记、不改候选集的项。
                    "answer_verdicts": r.get("answer_verdicts") or {},
                    "lens": ({"id": spec.get("id"),
                              "generated_by": spec.get("generated_by"),
                              "corpus_fingerprint": spec.get("corpus_fingerprint")}
                             if spec else None)})
        # 中转点②：**最终交付**的支序与内核逐字相同
        #   （放在 update 之后 —— 比的是真的交出去的那份，不是中间变量）
        V.check_order(raw_dids, [n["did"] for n in out["nodes"]])
    return out


def _tool_remember(a: dict) -> dict:
    """agent 往**自己的**私人语料写一个节点 —— 主体性的落点。

    ⚠️ 只追加不覆写：同 id 已存在 ⇒ 不写、报原因（覆写就是改写自己的记忆）。
    ⚠️ `filled_by` 照记：三类（人 / 模型 / 外部）分开可筛的纪律在这里落地。
    ⚠️ 写完语料就变了 ⇒ 冻结透镜的指纹不符 ⇒ `orient` 报 stale。
       **这是该有的行为**：材料一变，上一份认识作废。
    """
    corpus = a.get("corpus", "")
    nodes, in_store = _corpus_dir(corpus)
    if nodes is None or not in_store:
        raise ValueError("只能往产品 store 下的私人语料写（内置语料只读）")
    nid = str(a.get("id") or "").strip()
    if not nid:
        raise ValueError("缺 id —— 记忆要有个能指回来的名字")
    p = nodes / (_safe_name(nid) + ".md")
    fields = a.get("fields") or {}
    body = str(a.get("body") or "")
    # ★ 来源标注是**通道盖的章**，不是调用方填的表；「已验证」是人的档。
    #   在**落盘之前**处理 —— 不是写完再改，那会留下一条脏记录。
    #   ⚠️ 除此之外**不设任何闸**：agent 写什么内容都直接进、直接能用（「使用」不看来源）。
    filled_by = _stamp_provenance(_actor(a), fields,
                                  str(a.get("filled_by") or ""))
    lines = ["---", f'id: "{nid}"', f'filled_by: "{filled_by}"',
             f'remembered_at: "{datetime.now().isoformat(timespec="seconds")}"']
    for k in sorted(fields):
        lines.append(f"{k}: {json.dumps(fields[k], ensure_ascii=False)}")
    lines += ["---", "", body.rstrip() + chr(10)]
    # ★ 跨进程：`create_exclusive` 是原子的（O_CREAT|O_EXCL），
    #   不是「先 exists() 再写」—— 后者在两个会话之间是假检查。
    with L.store_lock(STORE):
        wrote = L.create_exclusive(p, chr(10).join(lines))
    if not wrote:
        return {"ok": False, "written": False,
                "why": f"已有一条 id={nid} 的记忆 —— 只追加不覆写；要补充就换一个 id"}
    return {"ok": True, "written": True, "path": str(p), "id": nid,
            "filled_by": filled_by,
            "note": "语料变了 ⇒ 透镜指纹可能不符，下次 orient 会报 stale"}


def _tool_drill(a: dict) -> dict:
    """下钻一支 = 这一次「有用」的真实信号。agent 代钻与人的下钻同权。"""
    corpus = a.get("corpus", "")
    sid = str(a.get("session") or "")
    app, in_store = _app(corpus, sid)
    req = list(a.get("require") or [])
    forb = list(a.get("forbid") or [])
    plugin = str(a.get("plugin") or "keyset")
    V.check_plugin(plugin)
    chk = V.check_keys(app.all_keys, req, forb)
    if not chk["ok"]:
        return {"ok": False, "out_of_structure": True, **chk}
    did = a.get("did", "")
    # ⚠️ 顺序很重要：`_last_shown` 属于**这一次的 App**，只有 query 之后才有内容。
    #   第一版在 query 之前解析句柄 ⇒ 候选恒空 ⇒ 一直报「第 n 条不在这几支里」。
    r = app.query({"require": req, "forbid": forb})
    # ★ 句柄（2026-10-24）：agent 说「第 n 条有用」，id 由本地表换回来。
    #   语义上还更对：**有用的是这一条，不是这一支**。
    if not did and a.get("n") is not None:
        nodes_dir, _ = _corpus_dir(corpus)
        if nodes_dir is None:
            return {"ok": False, "why": "句柄只在 store 里的语料上可用"}
        hit, why = SESS.resolve_handle(nodes_dir.parent, a["n"],
                                       require=req, forbid=forb)
        if hit is None:
            return {"ok": False, "why": why}
        # 找**含这一条的最深一支**，且必须是这次摆出来的那几支
        k0 = app.ensure(plugin)
        shown = set(app._last_shown or [])
        cands = [d for d in k0.all_directions()
                 if d.did in shown and hit["id"] in k0.members_of(d)]
        if not cands:
            return {"ok": False,
                    "why": f"第 {a['n']} 条不在这几支里（也许这次查询没摆它）"}
        did = max(cands, key=lambda d: d.rank).did
    # ★ 负事件（外部评审 D1，2026-10-25）：`verdict="wrong"` = 「这一支被推翻」。
    #   **判错只能外部授予** —— `actor` 不是 `human` 一律由内核拒
    #   （agent 只能提议待核，否则它会把自己读不懂的标成错的）。
    #   ⚠️ `actor` 与 `filled_by` 同款：调用方声明，服务器不验身份 ——
    #      这里守的是「接口不发给 agent」，不是「能证明是人」。
    d = app.drill(did, req, forb, verdict=str(a.get("verdict") or ""),
                  actor=_actor(a))
    # ⚠️ 内核说「不」（判错被拒 / 没有这个方向）必须**原样透出** ——
    #   下面那个 return 写死 ok=True，会把「被拒」吞成「成功」（第一版就这样）。
    if not d.get("ok"):
        return {"ok": False, **{k: v for k, v in d.items() if k != "ok"}}
    # ★ 会话缓冲里把对应曝光标成 promoted（真实动作，与内核两段通道同构）
    promoted = False
    if sid:
        nodes_dir, _ = _corpus_dir(corpus)
        if nodes_dir is not None:
            promoted = SESS.mark_promoted(nodes_dir.parent, sid, did)
    # ★ qfp 进**返回**（调用方要看「这一钻属于哪次查询」），日志由调度器
    #   统一记 —— 早先工具内部也记一次 ⇒ 同一次调用两行，S11 抓到。
    return {"ok": True, "corpus": app.corpus_name, "did": did,
            "actor": _actor(a), "qfp": app._last_qfp,
            "session_promoted": promoted,
            # ★ 负事件三件套（外部评审 D1）：这次记的是什么、这支现在的倾向。
            #   ⚠️ 与「成功与失败共用一句话」同源的纪律：判错被拒时 `ok=False`
            #      且 `error` 说清为什么（agent 不能判错）。
            "verdict": d.get("verdict"), "outcome": d.get("outcome"),
            "tendency_after": d.get("tendency_after"),
            "recorded": d.get("recorded"), "record_note": d.get("record_note"),
            "parent": d.get("parent"), "children": d.get("children"),
            "usage_enabled": app._usage_dir is not None and in_store}


def _tool_derive(a: dict) -> dict:
    """agent **敲定一个派生维度** —— 词汇表里没有的轴，它自己建。

    ★ 不是新能力，是把内核已有的机制暴露给 agent：`influence` 维度
      （v0.1.36）就是这么来的 —— 算出来，叠加成 `field=值` 键，
      **不写回材料**，删掉 sidecar 即逐字回到原状（D7 判据守这条）。
    ★ 三条纪律（沿用内核 `_meta` 的约定）：
        `what` 这维度是什么 · `why` 为什么它切得开（理由原文，必填）
        `derived_from` 从哪来（原材料 / 模型推断 —— **必填**：这一栏决定
        答案里敢不敢把它当事实）
    ⚠️ 派生维度**不改材料** ⇒ 语料指纹不变 ⇒ 透镜仍有效；但**词汇表变了**
      ⇒ 答案可能变。两者都报，不藏。
    """
    corpus = a.get("corpus", "")
    nodes, in_store = _corpus_dir(corpus)
    if nodes is None or not in_store:
        raise ValueError("只能给产品 store 下的私人语料加派生维度（内置语料只读）")
    field = str(a.get("name") or "").strip()
    values = a.get("values") or {}
    why = str(a.get("rationale") or "").strip()
    frm = str(a.get("derived_from") or "").strip()
    if not field or not isinstance(values, dict) or not values:
        raise ValueError("缺 name 或 values（{id: 值}）")
    if not why:
        raise ValueError("缺 rationale —— 说不清为什么这个维度切得开，就不许建")
    if not frm:
        raise ValueError("缺 derived_from —— 「从哪来」决定答案里敢不敢把它当事实"
                         "（原材料 / 模型推断，必填）")

    app, _ = _app(corpus)
    # ★ 键可以是 id，也可以是**句柄**（"1"/"2"…）—— agent 手里只有句柄。
    #   句柄查不到就当成 id 原样用（id 也可能是纯数字，别乱猜）。
    nodes_dir, _ = _corpus_dir(corpus)
    if nodes_dir is not None:
        fixed: dict = {}
        for k, v in values.items():
            ks = str(k)
            hit = None
            if ks.isdigit():
                hit, _why = SESS.resolve_handle(nodes_dir.parent, ks)
            fixed[hit["id"] if hit else ks] = v
        values = fixed
    unknown = sorted(set(str(k) for k in values) - set(app.nodes))
    if unknown:
        raise S.SpecError(f"这些 id 不在语料里：{unknown[:5]}"
                          + (f"…共 {len(unknown)} 个" if len(unknown) > 5 else ""))
    ddir = nodes.parent / "derived"
    ddir.mkdir(parents=True, exist_ok=True)
    doc = {"_meta": {"field": field,
                     "what": str(a.get("what") or f"agent 派生维度 {field}"),
                     "why": why,
                     "derived_from": frm,
                     "generated_by": _actor(a),
                     "derived_at": datetime.now().isoformat(timespec="seconds")},
           "values": {str(k): str(v) for k, v in sorted(values.items())}}
    p = ddir / f"{field}.json"
    force = bool(a.get("force"))
    # ★ 跨进程：同名维度**不许静默覆盖**（第一版直接 replace ⇒ 两个会话
    #   各建一个 topic，后写的赢且没人知道）。要换必须显式 force。
    with L.store_lock(STORE):
        if p.exists() and not force:
            old = {}
            try:
                old = json.loads(io.open(p, encoding="utf-8").read()).get("_meta", {})
            except (OSError, ValueError):
                pass
            raise S.SpecError(
                f"派生维度 {field!r} 已经有了（{old.get('generated_by', '?')}"
                f" @ {old.get('derived_at', '?')}）。要换请显式 force —— "
                "「悄悄换了一个维度」与「主动换了」在看答案时长得一样")
        tmp = p.with_suffix(".tmp")
        io.open(tmp, "w", encoding="utf-8", newline="\n").write(
            json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True))
        os.replace(tmp, p)                   # 原子替换：不留半份
    keys = sorted({f"{field}={v}" for v in doc["values"].values()})
    # ★ 覆盖率（2026-10-24 长场景实验暴露的）：派生维度是**快照** ——
    #   建完之后新写进语料的节点**不会**自动进它。不报覆盖率，用户会以为
    #   全库都按这个维度分好了，实际只有建那一刻的那批。
    covered = len(doc["values"])
    uncovered = max(0, len(app.nodes) - covered)
    return {"ok": True, "field": field, "n_items": len(doc["values"]),
            "keys_added": keys, "path": str(p),
            "derived_from": frm, "generated_by": _actor(a),
            "coverage": {"covered": covered, "uncovered": uncovered,
                         "note": ("全部节点都有值" if uncovered == 0 else
                                  f"**还有 {uncovered} 项没有这个维度的值** —— "
                                  "派生维度是快照，后来写入的节点不在里面；"
                                  "要覆盖全库请重新 derive（force）")},
            "note": "材料没动（指纹不变、透镜仍有效），但词汇表多了这些键 ⇒ "
                    "答案可能变。删掉这个文件即逐字回到原状"}


# ── 答案缓存（2026-10-24 用户定：先查树，不命中才问 agent，答完编回树）────
#
# ★ 这是**级联 + 编译**：FrugalGPT(2023) 的 cascade（便宜先上，不行才升级）
#   + Soar 的 chunking（把求解经验编进长期记忆，避免重解）。
# ★ 模型代填**允许**（用户 2026-10-24 重申，README 第 75 行早有），但必须标记：
#   filled_by: agent + derived_from，永不进「已验证」那一桶。
# ★ 过期失效：键里带**语料指纹** —— 指纹一变，缓存整体作废（与 derive 的透镜同款）。
# ★ 命中要明说：from_cache=true，不混在材料答案里。
# ★ 可回滚：删掉那个 json 文件即逐字回到原状（与 derive 的 sidecar 同款）。
def _answers_dir(nodes: Path) -> Path:
    d = nodes.parent / "answers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(require, forbid, plugin: str) -> str:
    """键 = **问题本身**（plugin + require + forbid），**不含语料指纹**。

    ⚠️ 为什么不含：含了的话，语料一变就变成「找不到文件」——
      那与「从来没有过缓存」长得一样，用户看不出「有缓存但过期了」。
      不含 ⇒ 同一个问题永远落到同一个文件，过期写在**文件里**，
      查找时比对 ⇒ 明说「已过期，不采用」（与 derive 的透镜 stale 同款）。
    """
    import hashlib

    raw = "|".join([plugin, ",".join(sorted(require)), ",".join(sorted(forbid))])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _tool_remember_answer(a: dict) -> dict:
    """把 agent 的一次回答**编进树**（下次同类问题先命中这里，不再问 agent）。

    ★ 用户 2026-10-24：「先查树，找不到再去问 agent 要」+「将过程编入进方向树中」。
    ★ 键 = 语料指纹 + require/forbid + 插件 ⇒ 语料一变，旧缓存整体作废
      （不是逐条判断，是一把失效 —— 与 `derive` 的透镜 stale 同款）。
    ★ `answer` 是 agent 写的文本，**不是材料**：带 filled_by: agent +
      derived_from，永不标「已验证」。
    """
    corpus = a.get("corpus", "")
    app, _ = _app(corpus)
    nodes_dir, _ = _corpus_dir(corpus)
    if nodes_dir is None:
        return {"ok": False, "why": "只在 store 里的语料上可用"}
    req = list(a.get("require") or [])
    forb = list(a.get("forbid") or [])
    plugin = str(a.get("plugin") or "keyset")
    V.check_plugin(plugin)
    chk = V.check_keys(app.all_keys, req, forb)
    if not chk["ok"]:
        return {"ok": False, "out_of_structure": True, **chk}
    answer = str(a.get("answer") or "").strip()
    if not answer:
        raise ValueError("缺 answer —— 要编进树的那段回答")
    src = [str(x) for x in (a.get("source") or [])]
    for s in src:
        if s not in app.nodes:
            raise S.SpecError(f"来源里没有这一项：{s}")

    # ★ 记的是**内容指纹**（与 `_cache_lookup` 同一口径）—— 用结构票据的话，
    #   正文改了缓存不会失效（真实流程探针 F09 抓到过）。
    fp = _content_fp(nodes_dir)
    key = _cache_key(req, forb, plugin)
    # ★★ 自己的产物进结构（2026-10-25，评估补的那一环）——
    #   `认知原型.md` 的第三句承诺是「它能**包含自己**：自己的产物标成 `我=是`
    #   进结构，于是反思是可查询的项」。repo 侧靠 `make_self_corpus` 抽的是
    #   **仓库里的文档与断言**；MCP 侧的「自己」是**AI 的运行时回答**，
    #   原来只写进 `answers/*.json` 缓存 —— 查不到、不参与 next、不参与反思。
    #   ⇒ 顺手写一条**节点**：`type=回答` + `我=是` + `filled_by=agent` +
    #     `source` 指回材料。于是「我上个月对这个答过什么」变成一条可查的项。
    #   ⚠️ **同键再答不覆写**（覆写就是改写自己的记忆）⇒ 写新版本 `-2`/`-3`，
    #      历史留着 —— 「我改过想法」本身是反思的材料。
    node_id = _write_self_node(nodes_dir, key, req, forb, answer, src, a)
    doc = {"qfp": key, "corpus_fp": fp, "plugin": plugin,
           "require": sorted(req), "forbid": sorted(forb),
           "answer": answer, "source": src,
           # ★ 与节点**同源**：缓存命中时校验这条节点还在（被 undo 删掉 ⇒ 作废）
           "node_id": node_id,
           "filled_by": "agent", "generated_by": _actor(a),
           "derived_from": ("agent 的一次回答（不是材料）；来源条目见 source —— "
                            "语料指纹一变，这条整体作废"),
           "created": datetime.now().isoformat(timespec="seconds")}
    p = _answers_dir(nodes_dir) / f"{key}.json"
    # ★ 同键再答 ⇒ **覆写**缓存（2026-10-25，真实使用发现的 bug）。
    #   原来用 `create_exclusive` ⇒ 文件已存在就写不进去，而返回值写死 `written: true`
    #   ⇒ **报成功但缓存还是旧答案**（静默失败，最坏的一种）。
    #   缓存**本来就该覆写**：「同一个问题的答案」以最新一次为准；
    #   历史留在节点里（`ans-<key>-2` 那套），不在缓存里。
    #   ⚠️ 用带重试的原子写（Windows 上 os.replace 会因读者持句柄被拒）。
    replaced = p.is_file()
    _atomic_write(p, json.dumps(doc, ensure_ascii=False, indent=1))
    return {"ok": True, "written": True, "replaced": replaced,
            "key": key, "path": str(p),
            "node_id": node_id,
            "corpus_fingerprint": fp,
            "note": ("答案已编进树：语料指纹不变时，同类问题先命中这里，"
                     "不必再问 agent；**指纹一变整体作废**；删掉这个文件即回退。"
                     "它带 filled_by: agent，**永不进「已验证」**。"
                     f"同时写了一条自己的记录（id={node_id}，我=是）—— "
                     "`ask(require=['我=是'])` 查得到，undo 它即回退")}


def _write_self_node(nodes_dir: Path, key: str, req, forb, answer: str,
                     src: list[str], a: dict) -> str:
    """把这次回答写成一条**结构里的项**（`我=是`）。返回节点 id。

    与 `remember` 同一条纪律：**只追加不覆写** ⇒ 同键再答写 `-2` / `-3`，
    历史留着（「我改过想法」是反思的材料，不是垃圾）。

    ⚠️ `evidence_status: 未验证` —— agent 自己写的回答**永远**是这个状态，
       要「已验证」只能外部授予（`§S5` / `README` 的能力表）。
    """
    base = f"ans-{key}"
    nid = base
    n = 1
    while (nodes_dir / (_safe_name(nid) + ".md")).exists():
        n += 1
        nid = f"{base}-{n}"
    fields = {
        "type": "回答",
        "我": "是",
        "evidence_status": "未验证",
        "generated_by": _actor(a),
        "derived_from": (f"对 require={sorted(req)} forbid={sorted(forb)} 的回答"
                         "（不是材料；来源见 source）"),
        "source": list(src),
    }
    lines = ["---", f'id: "{nid}"', 'filled_by: "agent"',
             f'remembered_at: "{datetime.now().isoformat(timespec="seconds")}"']
    for k in sorted(fields):
        lines.append(f"{k}: {json.dumps(fields[k], ensure_ascii=False)}")
    lines += ["---", "", answer.rstrip() + chr(10)]
    with L.store_lock(STORE):
        L.create_exclusive(nodes_dir / (_safe_name(nid) + ".md"),
                           chr(10).join(lines))
    return nid


def _content_fp(nodes_dir: Path) -> str:
    """答案缓存的**内容指纹**：相对路径 + 字节数 + mtime（不读正文）。

    ⚠️ 为什么不用 `app.fingerprint()`：那是**结构票据**（项数 / 键数 / 方向数），
       **正文改了它不变** ⇒ 缓存会静默沿用旧答案。实测（真实流程探针 F09）：
       改一条笔记的正文后 `from_cache` 仍是 True、`cache_note` 是 None ——
       正是「过期缓存静默赢过新答案」这个失效模式。
    ⚠️ 也不用 `spec_store.corpus_fingerprint`（透镜那个）：它刻意**不含正文**
       （「正文改了但结构没改，声明仍然成立」—— 对透镜是对的）。
       **两个用途要两个指纹**：透镜问「切分还成立吗」，缓存问「答案还是这个吗」。
    ⚠️ mtime 会造成「touch 一下假过期」—— 对缓存这是**安全**的一侧：
       宁可多问一次 agent，不可拿旧答案当新的。
    """
    import hashlib

    h = hashlib.sha1()
    for p in sorted(nodes_dir.rglob("*.md")):
        # ★ 跳过**自己的记录**（`ans-*`，`remember_answer` 写的那条）。
        #   它们不是「材料」，是「我对材料的回答」⇒ 写它不该让答案缓存失效
        #   （否则刚写完缓存就过期，缓存等于没有）。实测踩到过。
        if p.name.startswith("ans-"):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.name}|{st.st_size}|{st.st_mtime_ns};".encode("utf-8"))
    return h.hexdigest()[:16]


def _cache_lookup(app, nodes_dir: Path, req, forb, plugin: str):
    """命中 ⇒ (doc, path)；没命中或过期 ⇒ (None, 原因)。"""
    if nodes_dir is None:
        return None, ""
    fp = _content_fp(nodes_dir)          # ★ 内容指纹（不是结构票据）
    key = _cache_key(req, forb, plugin)
    p = _answers_dir(nodes_dir) / f"{key}.json"
    if p.is_file():
        try:
            doc = json.loads(io.open(p, encoding="utf-8").read())
        except (OSError, ValueError):
            return None, "缓存文件读不出（当没命中）"
        if doc.get("corpus_fp") == fp:
            # ★ 与节点**同源**（2026-10-25）：这条缓存对应的「自己的记录」
            #   如果被 undo 删掉了，缓存也必须作废 —— 否则就是「记忆删了、
            #   答案还在」。老缓存没有 `node_id`（升级前写的）⇒ 不校验，按原样放行。
            nid = doc.get("node_id")
            if nid and not (nodes_dir / (_safe_name(str(nid)) + ".md")).is_file():
                return None, (f"这条答案对应的记录（{nid}）已被删除 ⇒ 缓存作废，"
                              "重新问一次 agent")
            return doc, ""
        return None, (f"语料指纹变了（缓存记的是 {doc.get('corpus_fp')}、"
                      f"现在是 {fp}）⇒ 这条**已过期**，不采用")
    # 旧指纹的缓存还在但键不同 ⇒ 找不到，就报「没有」
    return None, ""


def _tool_undo(a: dict) -> dict:
    """反事实：**删掉一个项，看什么会变**（不改任何东西）。

    ★ 这是「推理」四件事里的反事实那一件（删了会怎样）。`entry.py` 本身只读
      （不 insert / remove / 落盘）—— 插件继承只读就把这件能力丢了。
      2026-10-24 按「打包成插件只舍弃自由度、不牺牲能力」补回来；
      同日晚些时候按用户点出的「有更好函数可以替代」重写。

    ★★ 为什么不真删：旧做法是「快照 → 真删 → 恢复」，它有一个**本可以不存在的
      失败态：恢复失败**。而答案集只由「项各有哪些键」决定（`App._resolve`
      走 `self.answer_set`）⇒ 删一项，答案集的变化只能是丢掉它，算术上就知道；
      结构侧的内核反事实（`impact.compare_prediction`）又明确要求「给一份深拷贝」。
      ⇒ 在**副本**上删，副本即弃：不需要恢复，也就不可能恢复失败。
    """
    corpus = a.get("corpus", "")
    app, in_store = _app(corpus)
    iid = str(a.get("id") or "").strip()
    req = list(a.get("require") or [])
    forb = list(a.get("forbid") or [])
    # ★ 句柄：agent 说「第 n 条」，id 从本地表换回来（2026-10-24）
    if not iid and a.get("n") is not None:
        nodes_dir, _ = _corpus_dir(corpus)
        if nodes_dir is None:
            raise ValueError("句柄只在 store 里的语料上可用")
        hit, why = SESS.resolve_handle(nodes_dir.parent, a["n"],
                                       require=req, forbid=forb)
        if hit is None:
            raise S.SpecError(why)
        iid = hit["id"]
    if not iid:
        # ★ `did` 是**方向 id**（D2），不是项 id —— 真实使用里有人这么传，
        #   而原来的报错「缺 id 或 n」让人以为系统坏了（2026-10-25 修）。
        if a.get("did"):
            raise S.SpecError(
                f"`did` 是**方向 id**（{a['did']}），不是项 id —— undo 要的是"
                "「删掉哪一项」，用 `id`（项 id）或 `n`（上一次查询里的句柄）")
        raise ValueError("缺 id 或 n —— 要问「删了谁会怎样」")
    if iid not in app.nodes:
        # 传了个方向 id 当项 id：也说清（同一个坑的第二种表现）
        if iid in {d.did for d in app.ensure(str(a.get("plugin") or "keyset")).all_directions()}:
            raise S.SpecError(
                f"`{iid}` 是**方向 id**，不是项 id —— 用 `id`（项）或 `n`（句柄）；"
                "要看「删掉这一项会怎样」，先用 ask 拿到它")
        raise S.SpecError(f"语料里没有这一项：{iid}")
    plugin = str(a.get("plugin") or "keyset")
    V.check_plugin(plugin)
    req = list(a.get("require") or [])
    forb = list(a.get("forbid") or [])
    chk = V.check_keys(app.all_keys, req, forb)
    if not chk["ok"]:
        return {"ok": False, "out_of_structure": True, **chk}

    from ldv.core import impact as IM

    k = app.ensure(plugin)
    # ★★ 2026-10-24 用户点出「有更好函数可以替代」，查证后整个重写：
    #
    #   旧做法：指纹 → 快照 → 真删 → diff → 读回恢复 → 指纹比过。
    #           它有一个**本可以不存在的失败态**：恢复失败。
    #
    #   为什么可以拆掉 —— 两件事放一起就够：
    #     ① `App._resolve` 的答案集来自 `self.answer_set(req, forb)`，
    #        而答案集**只由「项各有哪些键」决定** ⇒ 删掉一项，
    #        答案集的变化**只能是丢掉那一项**。这是算术上就知道的，不用真删。
    #     ② `impact.compare_prediction` 明确要求「给一份**深拷贝**」——
    #        内核自己的反事实就规定在副本上做。
    #
    #   ⇒ 删除发生在**深拷贝**上，副本即弃：不需要快照、不需要恢复、
    #     **也就不可能「恢复失败」**。那个红状态直接消失。
    # ⚠️ answer_set 收 **frozenset**（传 list 会 TypeError: '<=' not supported）
    ans_before = set(app.answer_set(frozenset(req), frozenset(forb)))
    # ① 预测（只算不改）
    pred = IM.removal_impact(k, [iid])
    # ② 实测 + 路径：都在副本上
    import copy

    try:
        kc = copy.deepcopy(k)
        path = kc.remove(iid)
        cmp = IM.compare_prediction(copy.deepcopy(k), [iid])
    except Exception as e:                         # noqa: BLE001
        # 深拷贝失败（如 `LockedKernel` 持锁不可拷）⇒ 如实报，**不退回真删那条路**
        return {"ok": False, "removed": iid,
                "why": f"深拷贝失败，没法在副本上做反事实：{type(e).__name__}: {e}",
                "note": "本工具**不真删**（删了就要恢复，而恢复是会失败的）—— 所以这里只能报错"}

    return {"ok": True, "corpus": app.corpus_name, "removed": iid,
            "cone": list(path),
            # ★ 预测（内核 impact 给的，不是我手算的）
            "impact": {"说明": pred.get("说明"),
                       "会变空": pred.get("会变空") or [],
                       "成员减少数": pred.get("成员减少数"),
                       "不在结构里": pred.get("不在结构里") or []},
            # ★ 预测 vs 实测（在深拷贝上真删，不改这棵树）
            "prediction_check": cmp,
            # ★ 答案集的变化是**算出来的，不是删出来的**
            "answer_lost": [iid] if iid in ans_before else [],
            "answer_gained": [],
            "answer_note": ("答案集只由项各有哪些键决定 ⇒ 删一项，变化只能是丢掉它"
                            "（算术上就知道，不必真删）"),
            "untouched": True,
            "note": ("这一趟什么都没改 —— 删除发生在**深拷贝**上，副本即弃："
                     "不需要恢复，也就不可能恢复失败。材料一个字节没动")}


def _tool_next(a: dict) -> dict:
    """反思：**此刻该干什么** —— 后果三档，不是分数。

    档内按受影响成员数降序（影响面定序）；分档管「什么时候做」，排序管「先做哪个」。
    ⚠️ 三个桶都是清单（`可忽略` 也列），由调用方决定显不显示。
    ★ 也是能力补齐：反思层（动机三档）内核早就有，插件原来够不着。
    """
    corpus = a.get("corpus", "")
    app, _ = _app(corpus)
    plugin = str(a.get("plugin") or "keyset")
    V.check_plugin(plugin)
    from ldv.core import reflect as R

    k = app.ensure(plugin)
    g = R.graded(k, cap=a.get("cap"))
    out: dict = {"ok": True, "corpus": app.corpus_name, "plugin": plugin,
                 "必须": len(g.get("必须", [])), "可延后": len(g.get("可延后", [])),
                 "可忽略": len(g.get("可忽略", []))}
    limit = int(a.get("limit") or 5)
    # ★ 三个桶都列（S17 的 docstring 一直这么说，实现漏了「可忽略」——
    #   真实使用发现的，2026-10-25 补上）。调用方自己决定显不显示。
    out["清单"] = {tier: g.get(tier, [])[:limit]
                    for tier in ("必须", "可延后", "可忽略")}
    # ★ 失配汇总（2026-10-25）：把「**结构自己**哪里不平」收成一个视图，
    #   不必让调用方知道三档各叫什么才知道该看哪。
    #   ⚠️ 三类失配里**只有两类**在这里 —— 「需求与结构失配」（问的键不在词汇表）
    #      是 `ask` 的**即时结果**，不是结构状态。混进来就是拿两次不同的东西
    #      当一回事（「这次问不到」≠「结构有毛病」）。
    n_must = len(g.get("必须", []))
    n_late = len(g.get("可延后", []))
    out["失配"] = {
        "结构坏了": n_must,          # 覆盖不变量被破坏（`MUST_TYPES`）
        "表达力缺口": n_late,        # 切不开 / 滞留（试过但分不开）
        "合计": n_must + n_late,
        "note": ("这里只算**结构自己**的不平；「需求与结构失配」（问的键不在词汇表）"
                 "是 ask 的即时结果 `out_of_structure`，不在这一栏"),
    }
    out["note"] = ("按**后果**分档：填错会让结论错 ⇒ 必须；只是不全 ⇒ 可延后；"
                   "无差别 ⇒ 可忽略。不是分数，没有置信度")
    return out

TOOLS = [
    {"name": "orient",
     "description": "「我记得」—— 私人语料 / 冻结的透镜（理由+指纹+是否过期）/ "
                    "使用记录 / 最近几次动作。会话开始调一次。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string",
                                               "description": "语料名字或路径；空 = 列出所有 ldv"},
                                    "session": {"type": "string",
                                                "description": "会话名（调用方声明，默认 default）"}}}},
    {"name": "ask",
     "description": "把问题翻成键，在划分树上查一支。返回支序（原样）+ 查询式 + "
                    "透镜指纹 + 出处摘要。第一次可带 lens（带理由）自动冻结。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "require": {"type": "array", "items": {"type": "string"}},
                         "forbid": {"type": "array", "items": {"type": "string"}},
                         "lens": {"type": "object",
                                  "description": "方向函数声明，如 {\"插件\": \"keyset\"}；"
                                                 "首次使用自动冻结"},
                         "rationale": {"type": "string",
                                       "description": "为什么选这个透镜（必填才能冻）"},
                         "plugin": {"type": "string",
                                    "description": "临时覆盖透镜里的方向函数"},
                         "session": {"type": "string",
                                     "description": "会话名（调用方声明，默认 default）"},
                         "structure": {"type": "boolean",
                                       "description": "连分支与谓词一起返回（默认不给：agent 用不上）"},
                         "max_branches": {"type": "integer",
                                          "description": ("最多返回几支（0 = 不截，默认）。"
                                                          "截断时一并给 branches_total/shown/hidden "
                                                          "让账平 —— 别拿没给的当不存在")},
                         "reject_below": {"type": "number",
                                          "description": ("支级跳过的阈值（不传 = 不跳）：倾向"
                                                          "低于它的支不摆出来。跳过必带差额"
                                                          "（skipped_rejected / rejected_items）；"
                                                          "require 含该支 must 时不跳")},
                         "with_body": {"type": "boolean",
                                       "description": "连正文一起返回（读内容是查询的一个选项，"
                                                      "不是单独的工具）"},
                         "limit": {"type": "integer",
                                   "description": "with_body 时最多几项（默认 5）"},
                         "max_chars": {"type": "integer",
                                       "description": "with_body 时每项正文最多几字（默认 2000）"}},
                     "required": ["require"]}},
    {"name": "remember",
     "description": "往自己的私人语料写一个节点（只追加不覆写）。"
                    "**写什么都直接进、直接能查能用的** —— 使用不看来源。"
                    "两条限制只跟「标注」有关："
                    "① filled_by 是通道盖的章，只能记成你自己的 actor；"
                    "② evidence_status=已验证 是**人的档**，只有 actor=human 能给。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "id": {"type": "string"},
                         "fields": {"type": "object"},
                         "body": {"type": "string"},
                         "filled_by": {"type": "string",
                                       "description": "来源标注。不传就记成你的 actor；"
                                                      "只能等于 actor（不能替别人声明）"},
                         "actor": {"type": "string",
                                   "description": "默认 agent。声明 human 才能给「已验证」"},
                         "session": {"type": "string",
                                     "description": "会话 id —— 带上它，这次写才会进"
                                                    "「变更回执」（orient 的 changes）"}},
                     "required": ["id", "fields", "body"]}},
    {"name": "drill",
     "description": "下钻一支 = 「这一支有用」。agent 代钻与人的下钻同权，"
                    "actor 默认 agent（可声明 human）。verdict=wrong 只接受 human。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "did": {"type": "string"},
                         "require": {"type": "array", "items": {"type": "string"}},
                         "forbid": {"type": "array", "items": {"type": "string"}},
                         "actor": {"type": "string"}, "session": {"type": "string"},
                         "n": {"type": "integer",
                               "description": "缓冲顺序编号：第 n 条有用（不必给 did；超出则回「顺序不记了」）"},
                         "verdict": {"type": "string",
                                     "description": ("good（默认，这一支有用）｜"
                                                     "wrong（这一支被推翻，记 outcome=-1）"
                                                     "—— wrong 只接受 actor=human，"
                                                     "agent 只能提议不能判错")}},
                     "required": ["did", "require"]}},
    {"name": "derive",
     "description": "agent 敲定一个**派生维度**（词汇表里没有的轴，它自己建）—— "
                    "不写回材料，删 sidecar 即逐字回退。必填理由与「从哪来」。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "name": {"type": "string", "description": "维度名，如 topic"},
                         "session": {"type": "string",
                                     "description": "会话 id —— 带上它才进变更回执"},
                         "values": {"type": "object",
                                    "description": "{节点 id: 取值}"},
                         "rationale": {"type": "string",
                                       "description": "为什么这个维度切得开（必填）"},
                         "derived_from": {"type": "string",
                                          "description": "原材料 / 模型推断（必填）—— "
                                                         "决定答案里敢不敢把它当事实"},
                         "what": {"type": "string"},
                         "force": {"type": "boolean",
                                   "description": "同名维度已存在时，显式覆盖"}},
                     "required": ["name", "values", "rationale", "derived_from"]}},
    {"name": "remember_answer",
     "description": ("把 agent 的一次回答**编进树**（下次同类问题先命中这里，不必再问 "
                     "agent）。键 = 语料指纹 + require/forbid + 插件 ⇒ 语料一变"
                     "整体作废；带 filled_by: agent，永不进「已验证」"),
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "require": {"type": "array", "items": {"type": "string"}},
                         "forbid": {"type": "array", "items": {"type": "string"}},
                         "plugin": {"type": "string"},
                         "answer": {"type": "string",
                                    "description": "要编进树的那段回答（agent 写的）"},
                         "source": {"type": "array", "items": {"type": "string"},
                                    "description": "这次回答依据哪些条目（可空）"},
                         "actor": {"type": "string"},
                         "session": {"type": "string",
                                     "description": "会话 id —— 带上它才进变更回执"}},
                     "required": ["answer"]}},
    {"name": "undo",
     "description": "反事实：删掉一个项，看答案丢什么、哪些方向少成员，然后**原样恢复**"
                    "（指纹逐字比对过才报成功）。材料一个字节没动。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "id": {"type": "string", "description": "要删来看后果的项"},
                         "n": {"type": "integer",
                               "description": "缓冲顺序编号：删第 n 条（与 id 二选一；超出则回「顺序不记了」）"},
                         "require": {"type": "array", "items": {"type": "string"}},
                         "forbid": {"type": "array", "items": {"type": "string"}},
                         "plugin": {"type": "string"}},
                     "required": ["id"]}},
    {"name": "next",
     "description": "反思：此刻该干什么 —— 后果三档（必须/可延后/可忽略），"
                    "不是分数、没有置信度。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "plugin": {"type": "string"},
                         "cap": {"type": "integer"},
                         "limit": {"type": "integer",
                                   "description": "每档最多列几条（默认 5）"}}}},
]

_IMPL = {"orient": _tool_orient, "ask": _tool_ask,
         "remember": _tool_remember, "drill": _tool_drill,
         "derive": _tool_derive, "remember_answer": _tool_remember_answer,
         "undo": _tool_undo, "next": _tool_next}


# ── JSON-RPC 骨架（零依赖：手写，不引第三方） ───────────────────────────────

def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(rid, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _error(rid, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _handle(msg: dict) -> None:
    mid = msg.get("id")
    method = msg.get("method", "")
    if method == "initialize":
        # ★ 版本**协商**：回 client 发来的那个（自己也支持的话），否则回自己最高的
        asked = str((msg.get("params") or {}).get("protocolVersion") or "")
        ver = asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        _result(mid, {"protocolVersion": ver,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": SERVER_NAME,
                                     "version": _server_version()}})
        return
    if method == "ping":
        _result(mid, {})
        return
    if method.startswith("notifications/"):
        return                                  # 通知：不该应答
    if method == "tools/list":
        _result(mid, {"tools": TOOLS})
        return
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        fn = _IMPL.get(name)
        if fn is None:
            _error(mid, -32602, f"没有这个工具 {name!r}（有 {sorted(_IMPL)}）")
            return
        try:
            out = fn(args)
            # ★ 每次调用落**一行**日志（不许重复 —— 早先 drill 内部也记一次，
            #   同一次调用两行，S11 抓到）。summary 取结果的形状，不取全文。
            #   `qfp` / `did` 只有 drill 返回 ⇒ 放进 summary，
            #   `actor_split` 的 join 键 (qfp, did) 才凑得齐。
            _log_call(name, args, _actor(args),
                      {"ok": out.get("ok"), "n_answer": out.get("n_answer"),
                       "n_branches": out.get("n_branches"),
                       "qfp": out.get("qfp"), "did": out.get("did"),
                       # ★ 缓存命中要进日志，否则**算不出命中率**（2026-10-26）。
                       #   `cached_answer` 只在命中时出现 ⇒ True / False 两态够用；
                       #   ⚠️ 老日志**没有这个字段**，统计时必须排除而不是当 False
                       #   （"没记" ≠ "没命中" —— 与「有字段 ≠ 有读数」同一类坑）。
                       "cached": bool(out.get("cached_answer")),
                       # ★ 变更回执要按「成功/失败」分（2026-10-26）：
                       #   `written` 区分「写成了」与「同 id 已存在被拒」，
                       #   `key` 是 remember_answer 的身份（summary 里没有它就没法点名）。
                       "written": out.get("written"),
                       "key": out.get("key"),
                       "isError": not out.get("ok", True)})
            # ★ 不再返回 `structuredContent`（2026-10-25，官方 SDK 实测后删）：
            #   按 2025-06-18 规范，返回 structuredContent 的 tool **应当**声明
            #   `outputSchema`，否则严格客户端会按 schema 校验失败。而 `content[0].text`
            #   里已经是完整 JSON —— 客户端照样能解析，所以这个字段是**纯风险无收益**。
            #   （要给 12 个工具各写一份 outputSchema，维护成本 > 收益。）
            _result(mid, {"content": [{"type": "text",
                                       "text": json.dumps(out, ensure_ascii=False,
                                                          indent=1)}],
                          "isError": not out.get("ok", True)})
        except (V.CheckError, S.SpecError) as e:
            # 判据红了 ⇒ 明确报错，不包成「成功但空」
            _result(mid, {"content": [{"type": "text", "text": str(e)}],
                          "isError": True})
        except Exception as e:  # noqa: BLE001
            _result(mid, {"content": [{"type": "text",
                                       "text": f"{type(e).__name__}: {e}"}],
                          "isError": True})
        return
    if mid is not None:
        _error(mid, -32601, f"不支持的方法 {method!r}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _error(None, -32700, "不是合法 JSON")
            continue
        _handle(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
