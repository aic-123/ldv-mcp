"""两个 ldv 系统之间的**交流协议**（内容层）—— `ldv-msg/1`，2026-10-21。

用户 2026-10-21：「设计两个 ldv 系统之间的交流协议，不必将其转化成自然语言，
允许直接对 ldv 的输出进行解析」。

## 为什么不做自然语言

ldv 的输出**本来就是结构**（节点 / 支 / 覆盖 / 账 / 指纹）。翻成自然语言是**双重有损**：

    - 丢掉「到底是哪 1888 项、哪些方向说『是』、账在哪」
    - 丢掉**可核验性** —— 接收方收到一句话只能选择信或不信；
      收到结构则能**在本地自己复算**（`verify_result` 就是给这个用的）

⇒ 协议 = 现有输出的**信封化**（身份 + 指纹 + 类型）+ 接收方自检。
   不新增语义、不新增计算、不新增任何「理解」。

## 两条红线（写进判据，不只写在这里）

1. **只换结构，不换语义** —— 可以回「D27 / 1888 项 / 精确 1.0」，
   不许回「这些人很有影响力」。后者是模型代填（`§8 停止条件 4`）。
2. **两个系统之间也不排序** —— `E-1` / `E-2` 的协议延伸：
   消息里出现任何跨系统可比的标量字段（`score` / `rank` / `order` / `weight`）
   一律**拒收**。一个系统不给另一个系统打分，正如系统不给项打分。

## 当前范围（最小可用，2026-10-21）

    QUERY   需求（require/forbid 键约束）→ 对方在自己树上跑
    RESULT  节点/支 + answer_ids + 精确/召回 → 接收方可本地复算

`STRUCTURE` / `VIEW` / `SPEC` / `GAP` **不在本版** —— 先把查询对等做扎实。
"""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any

#: 协议版本。**不认识就必须拒收，不许静默忽略** —— 静默忽略一条看不懂的消息，
#: 与「查询静默返回 0 条」是同一种退化：对端以为送到了，这边以为没有。
PROTOCOL_VERSION = "ldv-msg/1"

#: 消息类型（本版只有两个）
MSG_QUERY = "QUERY"
MSG_RESULT = "RESULT"
MSG_ERROR = "ERROR"
KNOWN_TYPES = (MSG_QUERY, MSG_RESULT, MSG_ERROR)

#: **协议级禁词** —— 出现即拒收。理由见模块 docstring 红线 2。
#:
#: ⚠️ 这是**字段名**级的检查（消息的任何一层都不许出现这些键），不是内容检查：
#:   内容是不可判的（那是一段自由文本），字段名是协议的形状，形状必须干净。
#:
#: ★ 2026-10-21 **实测收窄（判据当场抓到的）**：第一版含 `rank` / `order`，
#:   结果 `App.query()` 自己的结构字段 `nodes[].rank`（**第几层**）撞表
#:   ⇒ 每一条合法 RESULT 都被拒。原因：`rank` 在 ldv 里是**骨架**字段
#:   （`rank(d) = 1 + rank(parent(d))`，见 `docs/形式化断言集.md` §1.2），
#:   不是排序分。⇒ **字段名检查分不清「名字像排序」与「真是排序」** ——
#:   这是这种方法的天花板，如实记在这里，不装作能分清。
#:   保留的四个都是**只在评分语境出现的词**；要拿 ldv 结构字段伪装评分，
#:   靠字段名拦不住 —— 那要靠**人不发**（外生纪律），不是靠这个名字表。
BANNED_FIELDS = ("score", "weight", "priority", "rating")

#: 信封必填的键。缺一个 ⇒ 拒收。
REQUIRED_KEYS = ("proto", "type", "from", "fp", "ts", "payload")


# ── 信封 ────────────────────────────────────────────────────────────────────

def envelope(mtype: str, payload: dict, *, frm: str, fp: str) -> dict:
    """造一条消息。`from` 是发送方身份，`fp` 是**结构指纹**（见下）。"""
    if mtype not in KNOWN_TYPES:
        raise ValueError(f"未知消息类型 {mtype!r}（已知 {list(KNOWN_TYPES)}）")
    return {"proto": PROTOCOL_VERSION, "type": mtype, "from": str(frm),
            "fp": str(fp), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "payload": payload}


def _find_banned(obj: Any, path: str = "") -> list[str]:
    """递归找禁词字段。返回 `路径` 列表（空 = 干净）。"""
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if str(k).lower() in BANNED_FIELDS:
                hits.append(here)
            hits += _find_banned(v, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _find_banned(v, f"{path}[{i}]")
    return hits


def check_envelope(msg: Any) -> tuple[bool, str]:
    """**接收方第一关**：这条消息收不收？

    五查（任一不过 ⇒ 拒收 + 理由）：

        ① 是 dict                      ② `proto` 是 `ldv-msg/1`
        ③ `type` 已知                  ④ 六个必填键都在
        ⑤ 没有禁词字段（跨系统标量）

    ⚠️ 拒收**必须带理由并回报**（`MSG_ERROR`），不许静默丢弃 ——
       静默丢弃时对端以为送到了，与「查询静默返回 0」同一种退化。
    """
    if not isinstance(msg, dict):
        return False, "消息不是 JSON 对象"
    if str(msg.get("proto")) != PROTOCOL_VERSION:
        return False, (f"协议版本不认识：{msg.get('proto')!r}"
                       f"（本端只处理 {PROTOCOL_VERSION}）—— 不许静默忽略")
    if str(msg.get("type")) not in KNOWN_TYPES:
        return False, f"消息类型不认识：{msg.get('type')!r}（已知 {list(KNOWN_TYPES)}）"
    missing = [k for k in REQUIRED_KEYS if k not in msg]
    if missing:
        return False, f"信封缺必填键：{missing}（缺 `fp` 尤其危险 —— 见 build_result 的说明）"
    banned = _find_banned(msg.get("payload"))
    if banned:
        return False, (f"协议禁词字段：{banned} —— 两个系统之间也不排序 / 不打分"
                       f"（E-1/E-2 的协议延伸）")
    return True, ""


def error_envelope(why: str, *, frm: str = "", fp: str = "") -> dict:
    """拒收时回报的口信。`ok: False` + 理由，**不回声对方的 payload**。"""
    return envelope(MSG_ERROR, {"ok": False, "error": why}, frm=frm or "unknown", fp=fp)


# ── 两类消息 ────────────────────────────────────────────────────────────────

def build_query(require=(), forbid=(), *, frm: str, fp: str) -> dict:
    """QUERY = 一条需求（键约束）。**没有自然语言字段**，一个都没有。"""
    return envelope(MSG_QUERY,
                    {"require": sorted(require), "forbid": sorted(forbid)},
                    frm=frm, fp=fp)


def build_result(query_msg: dict, result: dict, *, frm: str, fp: str) -> dict:
    """RESULT = 对方树上的一次查询结果 + **原查询回声**。

    ⚠️ 回声原查询（`query` 段）是刻意的：**没有它，一条 RESULT 无法自证**
       —— 接收方不知道这条结果是回答哪条需求的。

    ★ `frm` / `fp` 必须是**回答方**的（2026-10-21 真实问题验收抓到的真 bug）：
      第一版从 `query_msg` 拷贝（= 提问方的身份与指纹）⇒ 结构不同的两个
      系统之间，B 回答 A 的 RESULT 自称 `from=A`、`fp=A 的指纹`，
      而答案明明是在 **B 的树**上跑出来的 —— 指纹从「票据」变成「误导」
      （§T2「看着都对、其实配错」的协议版）。
      **同结构时两边指纹相同 ⇒ 旧判据（66/70，同语料两个实例）看不见它**；
      跨结构一走协议就现形。
      语义：`from` = 发送方身份（RESULT 的发送方是回答方）；
            `fp`   = 「这是在**哪份结构**上跑出来的」（= 回答方的结构）。
    """
    payload = dict(result)
    payload["query"] = query_msg.get("payload") or {}
    return envelope(MSG_RESULT, payload, frm=str(frm), fp=str(fp))


# ── 接收方自检：**不依赖发送方诚信** ────────────────────────────────────────

def verify_result(msg: dict) -> tuple[bool, list[str]]:
    """RESULT 的三条自检（`_check_partition.py` 的同一条纪律，做到协议级）。

    ① `n_answer` 与 `answer_ids` 的**长度**必须相等 —— 谎报项数当场露馅
    ② `Σ 各支含答案 == n_answer` —— 不重不漏（支可以给全，接口不许截断）
    ③ 每支都有 `did` —— 指得到节点（「这一带」必须落在具体节点上）

    ⇒ 传递抓不到这三条，**本地也能抓到**。这就是「结构可传」相对
      「一句话可传」的全部差别。
    """
    ok0, why = check_envelope(msg)
    if not ok0:
        return False, [f"信封不过关：{why}"]
    p = msg.get("payload") or {}
    if not p.get("ok"):
        # ★ **④ 防「谎报超出结构」**（2026-10-22）：`ok=False` 有两种含义 ——
        #    「键拼错了」与「这个问题超出本结构的词汇」。后者必须给出 `unknown_keys`
        #    作为**凭证**，否则可以拿它当**逃避回答**的借口（键明明在，硬说超出）。
        #    ⚠️ 这条必须在 `return` **之前**查 —— 第一版写在后面，`ok=False`
        #       早就 return 了，永远走不到。
        if p.get("out_of_structure") and not (p.get("unknown_keys") or []):
            return False, ["声称「超出结构」却没给 `unknown_keys` —— 不能空口说超出"]
        return True, []                      # 对方如实报错（如键不存在）—— 不是违约
    bad: list[str] = []
    ids = p.get("answer_ids") or []
    if int(p.get("n_answer", -1)) != len(ids):
        bad.append(f"n_answer={p.get('n_answer')} 而 answer_ids 给了 {len(ids)} 个"
                   f" —— 对不上（谎报项数）")
    nodes = p.get("nodes") or []
    total = sum(int(n.get("n_answer", 0)) for n in nodes)
    if total != len(ids):
        bad.append(f"Σ各支含答案 = {total} ≠ n_answer = {len(ids)}（不重不漏破了）")
    for n in nodes:
        if not n.get("did"):
            bad.append(f"有一支没有 did：{str(n)[:60]}")
    return (not bad), bad


# ── 文件传输（`.jsonl`，一行一条消息） ─────────────────────────────────────

_lock = threading.Lock()


def exchange_file(app, inp: Path, outp: Path) -> dict:
    """读 `inp`（一行一条消息）⇒ 本地跑 ⇒ 写 `outp`（一行一条消息）。

    每条独立：一条坏（JSON 坏 / 信封不过关 / 抛异常）**不拖垮整批**，
    但必须**留痕**（`MSG_ERROR`）—— 与 `App.batch` 同一条纪律。

    ⚠️ 只处理 `QUERY`。收到 `RESULT`（对端角色反了）⇒ 报错，不递归跑。
    """
    lines = [ln for ln in io.open(inp, encoding="utf-8").read().splitlines() if ln.strip()]
    out: list[str] = []
    stats = {"in": len(lines), "ok": 0, "rejected": 0}
    for ln in lines:
        try:
            msg = json.loads(ln)
        except Exception as e:
            out.append(json.dumps(error_envelope(f"JSON 解析失败：{e}"), ensure_ascii=False))
            stats["rejected"] += 1
            continue
        ok, why = check_envelope(msg)
        if not ok:
            out.append(json.dumps(error_envelope(why), ensure_ascii=False))
            stats["rejected"] += 1
            continue
        if msg.get("type") != MSG_QUERY:
            out.append(json.dumps(
                error_envelope(f"只处理 {MSG_QUERY}，收到 {msg.get('type')!r}"
                               f"（对端角色可能反了）"),
                ensure_ascii=False))
            stats["rejected"] += 1
            continue
        try:
            with _lock:                       # 与 App.query 同一把纪律：串行
                res = app.query(msg.get("payload") or {})
            # ★ from/fp 是**回答方**的（本端）—— 2026-10-21 验收抓到的 bug：
            #   曾经从 query_msg 拷贝 ⇒ RESULT 自称来自提问方的结构。
            #   app 需有 query/who/fingerprint 三样（entry.App）。
            r = build_result(msg, res, frm=app.who(), fp=app.fingerprint())
            v_ok, v_bad = verify_result(r)
            if not v_ok:                      # 自己产出的结果必须过自己的自检
                r = error_envelope(f"本端结果自检未过：{v_bad[:2]}")
                stats["rejected"] += 1
            else:
                stats["ok"] += 1
        except Exception as e:                # 一条坏不拖垮整批
            r = error_envelope(f"{type(e).__name__}: {e}")
            stats["rejected"] += 1
        out.append(json.dumps(r, ensure_ascii=False))
    outp.parent.mkdir(parents=True, exist_ok=True)
    io.open(outp, "w", encoding="utf-8").write("\n".join(out) + "\n")
    return stats


# ── HTTP 传输（客户端；标准库，零依赖） ────────────────────────────────────

def send_msg(url: str, msg: dict, timeout: float = 60.0) -> dict:
    """把一条消息发到对端的 `POST /api/msg`，回一条信封。

    标准库 `urllib`（壳可依赖、内核零依赖 —— 这里是**壳侧**，用什么都行，
    但没必要引第三方）。网络错误 ⇒ 抛异常，由调用方决定怎么报。
    """
    import urllib.request
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310（对端地址由调用方给，不是外部输入）
        url, data=body, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    """`python -m ldv.protocol --in in.jsonl --out out.jsonl [--corpus NAME]`"""
    import argparse
    ap = argparse.ArgumentParser(description="ldv-msg/1 文件交换（一行一条消息）")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="outp", required=True)
    ap.add_argument("--corpus", default=None)
    a = ap.parse_args(argv)
    import sys as _sys
    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    if a.corpus:
        import os
        os.environ["LDV_CORPUS"] = a.corpus
    import importlib.util as _u
    _spec = _u.spec_from_file_location("_ldv_entry_app", _root / "entry.py")
    _mod = _u.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    app = _mod.App(a.corpus)
    st = exchange_file(app, Path(a.inp), Path(a.outp))
    print(f"收到 {st['in']} 条｜跑成 {st['ok']}｜拒收 {st['rejected']} ⇒ {a.outp}")
    return 0 if st["rejected"] == 0 else 1
