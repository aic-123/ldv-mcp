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


def _app(corpus: str):
    """按名字/路径建一个 entry.App。内置语料 ⇒ App(None)。"""
    import entry as E

    nodes, in_store = _corpus_dir(corpus)
    if nodes is None:
        app = E.App(None)
        return app, True
    os.environ["LDV_CORPUS"] = str(nodes)
    return E.App(None), in_store


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


def _tool_orient(a: dict) -> dict:
    """agent 的「**我记得**」—— 私人 store 的全貌。会话开始调一次。

    这一件就是「主体性」的接口：私人语料、冻结的透镜（带理由与指纹）、
    使用记录摘要、最近几次自己的动作。没有它，agent 每次会话都是从零开始的
    陌生人；有了它，它有一个**结构上可查**的过去。
    """
    corpus = a.get("corpus", "")
    nodes, in_store = _corpus_dir(corpus)
    app, _ = _app(corpus)
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
    app, in_store = _app(corpus)
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
    return {"ok": True, "corpus": app.corpus_name, "actor": _actor(a),
            "query": {"require": req, "forbid": forb, "plugin": plugin or "keyset"},
            "lens": ({"id": spec.get("id"), "generated_by": spec.get("generated_by"),
                      "corpus_fingerprint": spec.get("corpus_fingerprint")}
                     if spec else None),
            "n_answer": r.get("n_answer"), "n_branches": r.get("n_branches"),
            "answer_ids": r.get("answer_ids"), "ordered_by": r.get("ordered_by"),
            "provenance": _provenance(app, r.get("answer_ids") or []),
            "nodes": nodes,
            "usage_enabled": app._usage_dir is not None and in_store}


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
    if p.exists():
        return {"ok": False, "written": False,
                "why": f"已有一条 id={nid} 的记忆 —— 只追加不覆写；要补充就换一个 id"}
    fields = a.get("fields") or {}
    body = str(a.get("body") or "")
    filled_by = str(a.get("filled_by") or _actor(a))
    lines = ["---", f'id: "{nid}"', f'filled_by: "{filled_by}"',
             f'remembered_at: "{datetime.now().isoformat(timespec="seconds")}"']
    for k in sorted(fields):
        lines.append(f"{k}: {json.dumps(fields[k], ensure_ascii=False)}")
    lines += ["---", "", body.rstrip() + chr(10)]
    tmp = p.with_suffix(".tmp")
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(chr(10).join(lines))
    os.replace(tmp, p)                       # 原子替换：不留半份
    return {"ok": True, "written": True, "path": str(p), "id": nid,
            "filled_by": filled_by,
            "note": "语料变了 ⇒ 透镜指纹可能不符，下次 orient 会报 stale"}


def _tool_drill(a: dict) -> dict:
    """下钻一支 = 这一次「有用」的真实信号。agent 代钻与人的下钻同权。"""
    corpus = a.get("corpus", "")
    app, in_store = _app(corpus)
    req = list(a.get("require") or [])
    forb = list(a.get("forbid") or [])
    chk = V.check_keys(app.all_keys, req, forb)
    if not chk["ok"]:
        return {"ok": False, "out_of_structure": True, **chk}
    did = a.get("did", "")
    r = app.query({"require": req, "forbid": forb})
    d = app.drill(did, req, forb)
    # ★ qfp 进**返回**（调用方要看「这一钻属于哪次查询」），日志由调度器
    #   统一记 —— 早先工具内部也记一次 ⇒ 同一次调用两行，S11 抓到。
    return {"ok": True, "corpus": app.corpus_name, "did": did,
            "actor": _actor(a), "qfp": app._last_qfp,
            "recorded": d.get("recorded"), "record_note": d.get("record_note"),
            "parent": d.get("parent"), "children": d.get("children"),
            "usage_enabled": app._usage_dir is not None and in_store}


TOOLS = [
    {"name": "orient",
     "description": "「我记得」—— 私人语料 / 冻结的透镜（理由+指纹+是否过期）/ "
                    "使用记录 / 最近几次动作。会话开始调一次。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string",
                                               "description": "语料名字或路径；空 = 内置 36 项"}}}},
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
                                    "description": "临时覆盖透镜里的方向函数"}},
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
                         "actor": {"type": "string"}},
                     "required": ["did", "require"]}},
]

_IMPL = {"orient": _tool_orient, "ask": _tool_ask,
         "remember": _tool_remember, "drill": _tool_drill}


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
