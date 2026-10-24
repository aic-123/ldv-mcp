#!/usr/bin/env python3
"""ldv-mcp —— 把 ldv 内核包成一个 MCP 服务器（stdio，零第三方依赖）。

    python server.py            # 宿主（Claude / WorkBuddy / 其他 MCP 客户端）拉起它

## 这个服务器是什么

一层**薄适配**：MCP 的 JSON-RPC ⇄ `entry.py` 已经输出的 JSON。
不碰内核的任何保证 —— 树性、划分、覆盖、假阴，全部由内核自己的判据守。

## 它比「随便包一层」多什么

    ① 查询键先过 `checks.validate` —— 未知键当场拒，不猜
    ② 支序原样中转 —— 重排即推荐，那是红线
    ③ 声明走 `spec_store` —— agent 生成、首次使用即冻结、带理由与语料指纹
    ④ 使用记录照旧两段通道 —— 查询只进缓冲，下钻才 promote

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


# ── 工具实现（每个都返回 dict，由 _call 包成 MCP 的 content） ─────────────────

def _tool_vocabulary(a: dict) -> dict:
    app, in_store = _app(a.get("corpus", ""))
    keys = sorted(app.all_keys)
    return {"ok": True, "corpus": app.corpus_name, "n_items": len(app.nodes),
            "n_keys": len(keys), "keys": keys,
            "usage_enabled": app._usage_dir is not None and in_store,
            "note": "查询只能用这些键 —— 它们就是词汇表的全部"}


def _tool_propose_spec(a: dict) -> dict:
    corpus = a.get("corpus", "")
    spec = a.get("spec") or {}
    rationale = (a.get("rationale") or "").strip()
    nodes, _ = _corpus_dir(corpus)
    if nodes is None:
        raise ValueError("内置语料是只读的，不给它提声明 —— 先指一个私人语料")
    out = S.propose(_spec_dir(corpus), spec, rationale=rationale,
                    generated_by=a.get("generated_by", "agent"),
                    corpus_dir=nodes)
    out["note"] = "候选已存。它还不是系统的输入 —— freeze_spec 之后才生效"
    return out


def _tool_freeze_spec(a: dict) -> dict:
    corpus = a.get("corpus", "")
    nodes, _ = _corpus_dir(corpus)
    if nodes is None:
        raise ValueError("内置语料只读，不冻结声明")
    return S.freeze(_spec_dir(corpus), a.get("candidate_id", ""),
                    corpus_dir=nodes, force=bool(a.get("force")))


def _tool_spec_status(a: dict) -> dict:
    corpus = a.get("corpus", "")
    nodes, _ = _corpus_dir(corpus)
    if nodes is None:
        return {"ok": True, "frozen": False, "why": "内置语料只读，无声明"}
    return {"ok": True, **S.status(_spec_dir(corpus), nodes)}


def _tool_query(a: dict) -> dict:
    corpus = a.get("corpus", "")
    app, in_store = _app(corpus)
    req = list(a.get("require") or [])
    forb = list(a.get("forbid") or [])

    # ① 键校验 —— 产品的第一道闸，在入口之前
    chk = V.check_keys(app.all_keys, req, forb)
    if not chk["ok"]:
        return {"ok": False, "out_of_structure": True, **chk}

    # ③ 冻结的声明 ⇒ 用哪个方向函数（查询参数可覆盖，但白名单先过）
    plugin = a.get("plugin") or ""
    spec = S.load(_spec_dir(corpus))
    if not plugin and spec:
        plugin = (spec.get("spec") or {}).get("插件", "")
    if plugin:
        V.check_plugin(plugin)

    spec_q = {"require": req, "forbid": forb}
    if plugin:
        spec_q["plugin"] = plugin
    r = app.query(spec_q)
    if not r.get("ok"):
        return r

    # ② 支序原样：把 nodes 的 did 序列与内核返回的对一遍再发出去
    nodes = r.get("nodes") or []
    V.check_order([n["did"] for n in nodes], [n["did"] for n in nodes])
    return {"ok": True, "corpus": app.corpus_name,
            "actor": _actor(a),
            "query": {"require": req, "forbid": forb, "plugin": plugin or "keyset"},
            "spec": ({"id": spec.get("id"), "generated_by": spec.get("generated_by"),
                      "corpus_fingerprint": spec.get("corpus_fingerprint")}
                     if spec else None),
            "n_answer": r.get("n_answer"), "n_branches": r.get("n_branches"),
            "answer_ids": r.get("answer_ids"),
            "ordered_by": r.get("ordered_by"),
            # ★ 出处摘要：agent 被允许起草内容 ⇒ 混合必须**报出来**，不藏
            "provenance": _provenance(app, r.get("answer_ids") or []),
            "nodes": nodes,                      # 顺序即内核顺序，原样
            "usage_enabled": app._usage_dir is not None and in_store}


def _tool_drill(a: dict) -> dict:
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
    # ★ agent 的下钻**算**「有用」（用户 2026-10-24 定）—— 与人的下钻同权。
    #   代价：倾向会带上 agent 的翻译口味。兜底不是「不算」，是**可分解**：
    #   谁钻的、钻了什么，落在调用日志里，`usage_status` 报占比。
    #   ⇒ 人能看见「我的透镜什么时候被换成了 agent 的」，并能接管翻译层。
    _log_call("drill", a, _actor(a),
              {"did": did, "qfp": app._last_qfp, "recorded": d.get("recorded")})
    return {"ok": True, "corpus": app.corpus_name, "did": did,
            "actor": _actor(a),
            "recorded": d.get("recorded"), "record_note": d.get("record_note"),
            "parent": d.get("parent"), "children": d.get("children"),
            "usage_enabled": app._usage_dir is not None and in_store}


def _tool_usage_status(a: dict) -> dict:
    corpus = a.get("corpus", "")
    app, in_store = _app(corpus)
    if app._usage_dir is None or not in_store:
        return {"ok": True, "usage_enabled": False,
                "why": "这个语料不在产品 store 下（或不在 _data 形状里）——"
                       "使用记录不启用。内置语料只读，同样不写"}
    from ldv.tools import usage_store as US

    out = {"ok": True, "usage_enabled": True,
           "corpus": app.corpus_name, **US.status(app._usage_dir)}
    # ★ 演员分解：正式记录里多少条来自 agent 声明的下钻。
    #   join 键 = (qfp, did) —— 调用日志与内核 usage 记录两边都有。
    #   ⚠️ 这是**读数**不是判据：没有阈值、不打分、不拦截。它只回答
    #      「我的次序有多少是 agent 的口味」—— 一个必须能问的问题。
    agent_q = set()
    if CALL_LOG.is_file():
        for line in io.open(CALL_LOG, encoding="utf-8"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            # ★ 只收 **actor 声明为 agent** 的下钻 —— 第一版忘了过滤，
            #   结果「人声明的下钻」也被算成 agent 的（分解失去意义）。
            if row.get("tool") == "drill" and row.get("actor") == "agent":
                agent_q.add((str(row.get("summary", {}).get("qfp", "")),
                             str(row.get("summary", {}).get("did", ""))))
    rows = US.load(app._usage_dir)
    n_agent = sum(1 for r in rows if (r.get("qfp", ""), r.get("did", "")) in agent_q)
    out["actor_split"] = {"记录数": len(rows), "agent 声明的下钻": n_agent,
                          "其余（人 / 未声明）": len(rows) - n_agent,
                          "note": "actor 是调用方**声明**的，服务器不验（同 filled_by）"}
    return out


TOOLS = [
    {"name": "vocabulary", "description": "语料的词汇表（全部可用键）。查询只能用这些键。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string",
                                               "description": "语料名字或路径；空 = 内置 36 项"}}}},
    {"name": "propose_spec", "description": "存一个候选声明（方向函数 + 参数 + 理由）。未冻结，不是系统输入。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string"},
                                    "spec": {"type": "object"},
                                    "rationale": {"type": "string"},
                                    "generated_by": {"type": "string"}},
                     "required": ["spec", "rationale"]}},
    {"name": "freeze_spec", "description": "把候选冻结成唯一声明。首次使用即冻结；已冻结需显式 force。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string"},
                                    "candidate_id": {"type": "string"},
                                    "force": {"type": "boolean"}},
                     "required": ["candidate_id"]}},
    {"name": "spec_status", "description": "冻结状态 + 是否过期（语料指纹不符）+ 候选数。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string"}}}},
    {"name": "query", "description": "在划分树上查一支。键必须先过词汇表；支序原样返回。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string"},
                                    "require": {"type": "array", "items": {"type": "string"}},
                                    "forbid": {"type": "array", "items": {"type": "string"}},
                                    "plugin": {"type": "string",
                                               "description": "覆盖声明里的方向函数"}}}},
    {"name": "drill", "description": "下钻一支 = 这一次「有用」的真实信号（进使用记录）。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string"},
                                    "did": {"type": "string"},
                                    "require": {"type": "array", "items": {"type": "string"}},
                                    "forbid": {"type": "array", "items": {"type": "string"}}},
                     "required": ["did"]}},
    {"name": "usage_status", "description": "两层记忆现状：正式记录 / 缓冲批次 / 覆盖方向。",
     "inputSchema": {"type": "object",
                     "properties": {"corpus": {"type": "string"}}}},
]

_IMPL = {"vocabulary": _tool_vocabulary, "propose_spec": _tool_propose_spec,
         "freeze_spec": _tool_freeze_spec, "spec_status": _tool_spec_status,
         "query": _tool_query, "drill": _tool_drill, "usage_status": _tool_usage_status}


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
            # ★ 每次调用都落日志（不只是 drill）—— agent 的审计轨迹要完整，
            #   不能只记「有副作用的那几次」。summary 取**结果的形状**，不取全文。
            _log_call(name, args, _actor(args),
                      {"ok": out.get("ok"), "n_answer": out.get("n_answer"),
                       "n_branches": out.get("n_branches"),
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
