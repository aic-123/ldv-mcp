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
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ldv-mcp"
SERVER_VERSION = "0.1.0"

#: 调用日志（agent 的审计轨迹）—— 一行一次工具调用，**只追加**。
#:
#: ★ 为什么要它：agent 是「第三方隐形使用者」（用户 2026-10-24 定的），
#:   隐形只体现在**对话里**，不体现在**日志里**。人随时能查「你替我过了哪些招」，
#:   并且能随时接管翻译层 —— 看不见的翻译 + 可接管的日志，才既快又不锁死。
#: ★ `actor` 是**调用方声明的**，不是服务器验的（服务器没有身份可验）——
#:   按 `filled_by` 的同一条纪律：**声明照记，不当证明**。
CALL_LOG = STORE / "log" / "mcp-calls.jsonl"


def _log_call(tool: str, args: dict, actor: str, summary: dict) -> None:
    """一次工具调用一行。写不进去**不炸**（日志是审计件，不是判据）——
    但也不静默：返回写没写成，调用方可以报。"""
    CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now().isoformat(timespec="seconds"),
           "tool": tool, "actor": actor,
           "args": {k: v for k, v in args.items() if k != "actor"},
           "summary": summary}
    try:
        with io.open(CALL_LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def _actor(args: dict) -> str:
    """调用方声明的演员。默认 `agent`（MCP 的调用方按定义就是 agent 宿主）。"""
    return str(args.get("actor") or "agent")


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


#: 空库的基石（2026-10-24 用户定）：内核把空目录当「找不到语料」——
#: 而空目录与路径写错长得一样，所以**空库需要一个占位项才能起来**。
#: 真开始记东西后它不参与任何查询（没有 type/键可查），可以留着也可以删。
KEYSTONE = """---
id: "_keystone"
type: 基石
title: "空库的基石（占位用，无内容）"
filled_by: "系统"
---
这一条没有内容。它存在只是因为内核把「空目录」当「找不到语料」，
而空目录与路径写错长得一样 —— 空库需要一个占位项才能起来。
"""


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
    # ★ 空库自动放基石：否则内核报 CorpusError（空目录 = 找不到语料）。
    #   只在**空**的时候放，之后不管它（不参与任何查询）。
    if in_store and nodes.is_dir() and not any(nodes.glob("*.md")):
        try:
            L.create_exclusive(nodes / "_keystone.md", KEYSTONE)
        except OSError:
            pass
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
        for line in list(io.open(CALL_LOG, encoding="utf-8"))[-5:]:
            try:
                recent.append(json.loads(line))
            except ValueError:
                continue
    out["recent_calls"] = recent
    out["note"] = ("这是你的私人状态：语料、透镜、记得什么、最近做了什么。"
                   "真人不需要看它 —— 但你可以随时把它讲给人听")
    return out


def _actor_split(app) -> dict:
    """正式记录里多少条来自 agent 声明的下钻（读数，不打分不拦截）。"""
    from ldv.tools import usage_store as US

    agent_q: set[tuple[str, str]] = set()
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
    return {"记录数": len(rows), "agent 声明的下钻": n_agent,
            "其余（人 / 未声明）": len(rows) - n_agent,
            "note": "actor 是调用方**声明**的，服务器不验（同 filled_by）"}


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
    r = app.query(q)
    if not r.get("ok"):
        return r

    nodes = r.get("nodes") or []
    V.check_order([n["did"] for n in nodes], [n["did"] for n in nodes])
    # ★ 本次查询用到了哪些**派生维度**（agent 建的轴，不是材料写的）——
    #   必须报：查 `topic=DAPO` 与查 `type=立场` 的证据等级不同。
    dfields = set(app.derived or {})
    derived_used = sorted({k for k in (req + forb)
                           if k.split("=", 1)[0] in dfields})
    # ★ 会话缓冲：这次**摆出了哪几支**（曝光，不是「有用」）——
    #   与内核两段通道同构：查询进缓冲，下钻才 promote。作用域按会话。
    sess_info: dict = {}
    if sid and nodes_dir is not None:
        sess_info = SESS.expose(nodes_dir.parent, sid, qfp=app._last_qfp,
                                dids=[n["did"] for n in nodes],
                                n_answer=r.get("n_answer") or 0)
    # ★ 读正文（2026-10-24 并进 ask：读内容是查询的一个参数，不是新工具）
    # ★★ 默认只交**正文 + 句柄**，id 与结构留在本地（用户 2026-10-24 定：
    #    「只是减少重复，记录依旧保存在 ldv 之中，agent 拿到这些也没用」）。
    #    ⇒ 默认**不给** answer_ids / nodes / must / mustnot / coverage / precision。
    #    要看结构（调试、判断「该不该换个问法」）显式 `structure=true`。
    limit = max(1, int(a.get("limit") or 5))
    max_chars = max(1, int(a.get("max_chars") or 2000))
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
                      "truncated": bool(with_body and len(body) > max_chars)})
        meta.append({"id": nid, "title": title})
    if nodes_dir is not None and meta:
        # 句柄表：本地记「第 n 条是哪一条」，agent 只看到 n
        SESS.save_handles(nodes_dir.parent, meta, qfp=app._last_qfp,
                          require=req, forbid=forb)
    out = {"ok": True, "corpus": app.corpus_name, "actor": _actor(a),
           "session": sess_info,
           "n_answer": r.get("n_answer"),
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
           "derived_note": ("本次用到的维度里，这些是 **agent 派生的**"
                            "（不是材料里写的）—— 当线索用，别当事实"
                            if derived_used else ""),
           "provenance": _provenance(app, r.get("answer_ids") or []),
           "usage_enabled": app._usage_dir is not None and in_store}
    if not items and r.get("n_answer"):
        out["items_note"] = (f"答案有 {r.get('n_answer')} 条，这里只给了 {limit} 条"
                             "（limit 卡住了）—— 要更多就放大 limit，"
                             "**别拿没给的当不存在**")
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
                    "ordered_by": r.get("ordered_by"),
                    "lens": ({"id": spec.get("id"),
                              "generated_by": spec.get("generated_by"),
                              "corpus_fingerprint": spec.get("corpus_fingerprint")}
                             if spec else None)})
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
    filled_by = str(a.get("filled_by") or _actor(a))
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
    d = app.drill(did, req, forb)
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

    fp = app.fingerprint()
    key = _cache_key(req, forb, plugin)
    doc = {"qfp": key, "corpus_fp": fp, "plugin": plugin,
           "require": sorted(req), "forbid": sorted(forb),
           "answer": answer, "source": src,
           "filled_by": "agent", "generated_by": _actor(a),
           "derived_from": ("agent 的一次回答（不是材料）；来源条目见 source —— "
                            "语料指纹一变，这条整体作废"),
           "created": datetime.now().isoformat(timespec="seconds")}
    p = _answers_dir(nodes_dir) / f"{key}.json"
    L.create_exclusive(p, json.dumps(doc, ensure_ascii=False, indent=1))
    return {"ok": True, "written": True, "key": key, "path": str(p),
            "corpus_fingerprint": fp,
            "note": ("答案已编进树：语料指纹不变时，同类问题先命中这里，"
                     "不必再问 agent；**指纹一变整体作废**；删掉这个文件即回退。"
                     "它带 filled_by: agent，**永不进「已验证」**")}


def _cache_lookup(app, nodes_dir: Path, req, forb, plugin: str):
    """命中 ⇒ (doc, path)；没命中或过期 ⇒ (None, 原因)。"""
    if nodes_dir is None:
        return None, ""
    fp = app.fingerprint()
    key = _cache_key(req, forb, plugin)
    p = _answers_dir(nodes_dir) / f"{key}.json"
    if p.is_file():
        try:
            doc = json.loads(io.open(p, encoding="utf-8").read())
        except (OSError, ValueError):
            return None, "缓存文件读不出（当没命中）"
        if doc.get("corpus_fp") == fp:
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
        raise ValueError("缺 id 或 n —— 要问「删了谁会怎样」")
    if iid not in app.nodes:
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
    out["清单"] = {tier: g.get(tier, [])[:limit] for tier in ("必须", "可延后")}
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
                         "with_body": {"type": "boolean",
                                       "description": "连正文一起返回（读内容是查询的一个选项，"
                                                      "不是单独的工具）"},
                         "limit": {"type": "integer",
                                   "description": "with_body 时最多几项（默认 5）"},
                         "max_chars": {"type": "integer",
                                       "description": "with_body 时每项正文最多几字（默认 2000）"}},
                     "required": ["require"]}},
    {"name": "remember",
     "description": "往自己的私人语料写一个节点（只追加不覆写，filled_by 照记）。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "id": {"type": "string"},
                         "fields": {"type": "object"},
                         "body": {"type": "string"},
                         "filled_by": {"type": "string"}},
                     "required": ["id", "fields", "body"]}},
    {"name": "drill",
     "description": "下钻一支 = 「这一支有用」。agent 代钻与人的下钻同权，"
                    "actor 默认 agent（可声明 human）。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "did": {"type": "string"},
                         "require": {"type": "array", "items": {"type": "string"}},
                         "forbid": {"type": "array", "items": {"type": "string"}},
                         "actor": {"type": "string"}, "session": {"type": "string"},
                         "n": {"type": "integer",
                               "description": "缓冲顺序编号：第 n 条有用（不必给 did；超出则回「顺序不记了」）"}},
                     "required": ["did", "require"]}},
    {"name": "derive",
     "description": "agent 敲定一个**派生维度**（词汇表里没有的轴，它自己建）—— "
                    "不写回材料，删 sidecar 即逐字回退。必填理由与「从哪来」。",
     "inputSchema": {"type": "object",
                     "properties": {
                         "corpus": {"type": "string"},
                         "name": {"type": "string", "description": "维度名，如 topic"},
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
                         "actor": {"type": "string"}},
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
        _result(mid, {"protocolVersion": PROTOCOL_VERSION,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
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
                       "isError": not out.get("ok", True)})
            _result(mid, {"content": [{"type": "text",
                                       "text": json.dumps(out, ensure_ascii=False,
                                                          indent=1)}],
                          "isError": not out.get("ok", True),
                          "structuredContent": out})
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
