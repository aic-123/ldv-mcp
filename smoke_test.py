"""ldv-mcp 冒烟测试：真起服务器，走一遍 MCP 握手 + 四件工具。

跑法（cwd = ldv-mcp）：python smoke_test.py

判的不是「服务器起来了」，是**每一件产品承诺的事真的发生了**：

    S1  initialize 握手，协议版本/能力/名字都对
    S2  工具面 = **四件动作 + 三件能力** —— 真人零动作，能力不缩水
    S3  orient 报词汇表 + 项数（「我记得」）
    S4  ask 正常：支序 + 查询式 + 透镜字段 + 出处摘要
    S5  ★ 未知键 ⇒ isError（第一道闸真的在挡，不是 prompt）
    S6  ★ ask 带 lens ⇒ **自动冻结**（没有 propose/freeze 两个动作）；
        内部重复冻结仍然拒（不 force 不许覆盖）
    S7  冻结后 ask 用上透镜里的方向函数
    S8  drill ⇒ 使用记录真的落了一条
    S9  orient 报使用记录 + 最近动作（recent_calls）
    S10 支序对拍：与内核直接跑的逐字相同（同一状态下）
    S11 ★ 调用日志：**每次**工具调用都落一行
    S12 ★ 出处摘要：buckets 之和 == n_answer；内置语料全是模型填的
    S13 ★ 演员分解：agent 下钻算有用，但**谁钻的可分解**
    S14 ★ remember：写节点 / 同 id 不覆写 / 写完透镜报 stale（材料变 ⇒ 认识作废）
    S15 ★ derive：agent 敲定派生维度 ⇒ 能查、标为派生；删 sidecar ⇒ 逐字回退
    S16 ★ undo：反事实 ⇒ 答案丢它；恢复后答案与指纹逐字相同；用内核 impact 预测并与实测对照
    S17 ★ next：反思三档（必须/可延后/可忽略）都有读数
    S18 ★ 跨进程写锁：别人持锁 ⇒ StoreBusy（不静默写）；S18b 被杀进程留下陈旧锁
    S19 ★ derive 同名维度不许静默覆盖（要换必须显式 force）
    S20 ★ ask(with_body)：连正文一起返回，正文与文件逐字相同
    S21 ★ with_body 截断必须明说（truncated + note 报字数和条数）
    S22 ★ 会话缓冲：曝光进缓冲 / 下钻标 promoted / 两个会话互不可见
    S23 ★ 缓冲有界：挤掉记账 / 漏检率可读（挤掉后再提到 ⇒ 计数+）
    S24 ★ 多个 ldv：不给名字 ⇒ 列出全部（只数文件不建内核）
    S25 ★ 使用记录隔离（可选）：isolate_usage ⇒ 两个会话的倾向互不影响
    S26 ★ 等价消费者：agent 填的内容在出处里单独一桶（标记真的分得开）
    S27 ★ 空库能起来：基石自动放 ⇒ 能直接 remember ⇒ 能查
"""
from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
FAIL = 0
CALLS = 0


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global FAIL
    if not good:
        FAIL += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}" + (f"\n        {detail}" if detail else ""))


class MCP:
    """一个最小 MCP 客户端：写一行、读一行。"""

    def __init__(self, env_extra: dict | None = None):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(env_extra or {})
        self.p = subprocess.Popen([PY, str(HERE / "server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  env=env, cwd=str(HERE))
        self.n = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        rid = self.n
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                       "method": method,
                                       "params": params or {}},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            err = self.p.stderr.read() if self.p.stderr else ""
            raise RuntimeError(f"服务器没应答（挂了？）stderr={err[:300]}")
        return json.loads(line)

    def notify(self, method: str) -> None:
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method},
                                      ensure_ascii=False) + "\n")
        self.p.stdin.flush()

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.p.kill()


def tool(c: MCP, name: str, args: dict) -> dict:
    global CALLS
    CALLS += 1
    r = c.call("tools/call", {"name": name, "arguments": args})
    res = r.get("result") or {}
    txt = ""
    for item in res.get("content") or []:
        txt += item.get("text", "")
    try:
        out = json.loads(txt) if txt else {}
    except ValueError:
        out = {"_raw": txt}
    return {"isError": res.get("isError"), "out": out}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ldv-mcp-smoke-"))
    store = tmp / "store"
    corpus = store / "_data" / "mine" / "nodes"
    corpus.mkdir(parents=True)
    # 私人语料：从内置 36 项复制一份（真实形状，不是合成夹具）
    builtin = HERE / "core" / "ldv" / "corpus" / "nodes"
    for f in sorted(builtin.glob("*.md")):
        shutil.copy2(f, corpus / f.name)

    c = MCP({"LDV_MCP_STORE": str(store)})
    try:
        r = c.call("initialize", {"protocolVersion": "2024-11-05",
                                  "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}})
        res = r.get("result") or {}
        chk("S1", "initialize 握手", res.get("serverInfo", {}).get("name") == "ldv-mcp"
            and "tools" in (res.get("capabilities") or {}),
            f"{res.get('serverInfo')}｜协议 {res.get('protocolVersion')}")
        c.notify("notifications/initialized")

        r = c.call("tools/list")
        names = [t["name"] for t in (r.get("result") or {}).get("tools", [])]
        chk("S2", "★ 工具面 = 四件动作 + 三件能力（真人零动作，能力不缩水）",
            names == ["orient", "ask", "remember", "drill",
                      "derive", "undo", "next"], f"{names}")

        o = tool(c, "orient", {"corpus": "mine"})
        keys = o["out"].get("keys") or []
        chk("S3", "orient 报词汇表 + 项数（「我记得」）",
            o["out"].get("n_keys", 0) > 0 and o["out"].get("n_items") == 36
            and isinstance(o["out"].get("recent_calls"), list),
            f"{o['out'].get('n_keys')} 键｜{o['out'].get('n_items')} 项｜"
            f"透镜 frozen={((o['out'].get('lens') or {}).get('frozen'))}")

        # ★ 2026-10-24：默认不再返回结构 ⇒ 要看支序/透镜必须显式 structure
        q = tool(c, "ask", {"corpus": "mine", "require": keys[:1],
                             "structure": True})
        qd0 = tool(c, "ask", {"corpus": "mine", "require": keys[:1]})
        chk("S4", "ask 正常：支序 + 查询式 + 透镜字段 + 出处摘要；**默认不给结构**",
            q["out"].get("ok") and "query" in q["out"] and "lens" in q["out"]
            and q["out"].get("n_branches", 0) > 0
            and "provenance" in q["out"]
            and "nodes" not in qd0["out"] and "answer_ids" not in qd0["out"]
            and bool(qd0["out"].get("items")),
            f"{q['out'].get('n_answer')} 项 / {q['out'].get('n_branches')} 支｜"
            f"ordered_by={q['out'].get('ordered_by')}")

        bad = tool(c, "ask", {"corpus": "mine", "require": ["不存在的键=值"]})
        chk("S5", "★ 未知键 ⇒ isError（闸在挡，不是 prompt）",
            bad["isError"] is True and bad["out"].get("out_of_structure") is True,
            f"{str(bad['out'].get('why'))[:60]}")

        # S6 自动冻结：ask 带 lens ⇒ 一步到位；内部重复冻结仍拒
        lens = {"插件": "keyset", "参数": {"说明": "按字段交集切分"}}
        q2 = tool(c, "ask", {"corpus": "mine", "require": keys[:1], "lens": lens,
                             "structure": True,
                             "rationale": "字段天然多值，键集切分最直观"})
        st = (q2["out"].get("lens") or {})
        chk("S6a", "★ ask 带 lens ⇒ 自动冻结（没有 propose/freeze 两个动作）",
            bool(st.get("id")) and st.get("generated_by") == "agent",
            f"透镜 {st}")
        sys.path.insert(0, str(HERE))
        from spec_store import store as S

        cid = st.get("id", "")
        try:
            S.freeze(store / "specs" / "mine", cid, corpus_dir=corpus)
            chk("S6b", "内部重复冻结仍然拒（不 force 不许覆盖）", False, "居然没拒")
        except S.SpecError as e:
            chk("S6b", "内部重复冻结仍然拒（不 force 不许覆盖）",
                "已经冻结过" in str(e), f"{str(e)[:60]}")

        chk("S7", "冻结后 ask 用上透镜里的方向函数",
            q2["out"].get("query", {}).get("plugin") == "keyset",
            f"query {q2['out'].get('query')}")

        dids = [n["did"] for n in (q2["out"].get("nodes") or [])]
        d = tool(c, "drill", {"corpus": "mine", "did": dids[0], "require": keys[:1]})
        chk("S8", "drill ⇒ 使用记录真的落了一条", d["out"].get("recorded") is True,
            f"{d['out'].get('record_note')}")

        o2 = tool(c, "orient", {"corpus": "mine"})
        chk("S9", "orient 报使用记录 + 最近动作",
            (o2["out"].get("usage") or {}).get("记录数") == 1
            and len(o2["out"].get("recent_calls") or []) >= 3,
            f"usage {o2['out'].get('usage')}｜recent {len(o2['out'].get('recent_calls') or [])} 条")

        # S10 支序对拍：**同一状态**下比（下钻之后两边都该按倾向排）
        sys.path.insert(0, str(HERE / "core"))
        os.environ["LDV_CORPUS"] = str(corpus)
        import entry as E

        app = E.App(None)                      # 重建：读回刚写的那条使用记录
        q3 = tool(c, "ask", {"corpus": "mine", "require": keys[:1], "structure": True})
        direct2 = app.query({"require": keys[:1]})
        chk("S10", "支序与内核直接跑的逐字相同（同状态：下钻后）",
            [n["did"] for n in (q3["out"].get("nodes") or [])]
            == [n["did"] for n in (direct2.get("nodes") or [])]
            and q3["out"].get("ordered_by") == direct2.get("ordered_by") == "usage",
            f"MCP {[n['did'] for n in (q3['out'].get('nodes') or [])][:4]} vs "
            f"直接 {[n['did'] for n in (direct2.get('nodes') or [])][:4]}｜"
            f"ordered_by {q3['out'].get('ordered_by')}/{direct2.get('ordered_by')}")

        # ── S11 调用日志 ───────────────────────────────────────────────
        log = store / "log" / "mcp-calls.jsonl"
        rows = []
        if log.is_file():
            for line in io.open(log, encoding="utf-8"):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        chk("S11", "★ 调用日志：每次工具调用一行，条数对得上、字段齐",
            len(rows) == CALLS and all(
                r.get("tool") and r.get("actor") and r.get("at") and "summary" in r
                for r in rows),
            f"日志 {len(rows)} 行｜本趟调用 {CALLS} 次")

        # ── S12 出处摘要 ───────────────────────────────────────────────
        prov = (q3["out"].get("provenance") or {})
        buckets = prov.get("buckets") or {}
        chk("S12", "★ 出处摘要：buckets 之和 == n_answer；内置语料全是模型填的",
            sum(buckets.values()) == q3["out"].get("n_answer")
            and bool(buckets) and all(k.startswith("模型") for k in buckets),
            f"buckets {buckets}｜n_answer {q3['out'].get('n_answer')}")

        # ── S13 演员分解 ───────────────────────────────────────────────
        d2 = tool(c, "drill", {"corpus": "mine", "did": dids[1],
                               "require": keys[:1], "actor": "human"})
        o3 = tool(c, "orient", {"corpus": "mine"})
        split = o3["out"].get("actor_split") or {}
        actors = {r.get("actor") for r in rows} | {"human"}
        chk("S13", "★ 演员分解：agent 下钻算有用，但**谁钻的可分解**",
            split.get("记录数") == 2 and split.get("agent 声明的下钻") == 1
            and d2["out"].get("actor") == "human" and "human" in actors,
            f"split {split}｜drill2 actor={d2['out'].get('actor')}")

        # ── S14 remember：写节点 / 不覆写 / 透镜报 stale ────────────────
        m1 = tool(c, "remember", {"corpus": "mine", "id": "memo-001",
                                  "fields": {"type": "笔记", "topic": "测试"},
                                  "body": "这是我的一条私人记忆。"})
        m2 = tool(c, "remember", {"corpus": "mine", "id": "memo-001",
                                  "fields": {"type": "笔记"}, "body": "第二条。"})
        o4 = tool(c, "orient", {"corpus": "mine"})
        chk("S14", "★ remember：写节点 / 同 id 不覆写 / 写完透镜报 stale",
            m1["out"].get("written") is True and m2["out"].get("written") is False
            and (o4["out"].get("lens") or {}).get("stale") is True
            and o4["out"].get("n_items") == 37,
            f"写1={m1['out'].get('written')}｜写2={m2['out'].get('written')}"
            f"｜透镜 stale={(o4['out'].get('lens') or {}).get('stale')}"
            f"｜项数 {o4['out'].get('n_items')}")
        # ── S15 derive：agent 敲定派生维度 ⇒ 能查；删 sidecar ⇒ 逐字回退 ──
        d = tool(c, "derive", {"corpus": "mine", "name": "topic",
                               "values": {"stance-0001": "DAPO", "stance-0002": "DrGRPO",
                                          "stance-0003": "GRPO"},
                               "rationale": "这三条立场分别对着三篇论文，按论文归组能切开",
                               "derived_from": "模型推断（读 title/scope 后归的组）"})
        qd = tool(c, "ask", {"corpus": "mine", "require": ["topic=DAPO"], "structure": True})
        ok_derive = (d["out"].get("ok") is True
                     and "topic=DAPO" in (d["out"].get("keys_added") or [])
                     and qd["out"].get("ok") is True
                     and qd["out"].get("answer_ids") == ["stance-0001"]
                     and "topic=DAPO" in (qd["out"].get("derived_used") or []))
        # 删掉 sidecar ⇒ 键消失（材料一个字节没动）
        side = corpus.parent / "derived" / "topic.json"
        side.unlink()
        qd2 = tool(c, "ask", {"corpus": "mine", "require": ["topic=DAPO"]})
        chk("S15", "★ derive：派生维度能查、被标为派生；删 sidecar ⇒ 逐字回退",
            ok_derive and qd2["isError"] is True,
            f"derive={d['out'].get('ok')}｜键 {d['out'].get('keys_added')}"
            f"｜查到 {qd['out'].get('answer_ids')}｜派生标记 {qd['out'].get('derived_used')}"
            f"｜删后 {('拒收' if qd2['isError'] else '仍可查')}")

        # ── S16 undo：反事实 —— 删了会怎样，然后原样恢复 ────────────────
        u = tool(c, "undo", {"corpus": "mine", "id": "stance-0001",
                             "require": ["type=立场"]})
        pc = u["out"].get("prediction_check") or {}
        im = u["out"].get("impact") or {}
        chk("S16", "★ undo：删项 ⇒ 答案丢它；恢复后答案与指纹逐字相同；"
                   "且**用内核 impact 做预测**并与实测对照",
            u["out"].get("ok") is True
            and "stance-0001" in (u["out"].get("answer_lost") or [])
            and u["out"].get("restored") is True
            # ★ 2026-10-24 同步：不再手写成员 diff，改用内核 impact；
            #   预测与实测必须一致（会空 / 缩 两条）
            and bool(im.get("说明")) and pc.get("会空一致") is True
            and pc.get("缩一致") is True,
            f"丢 {u['out'].get('answer_lost')}｜锥 {len(u['out'].get('cone') or [])} 层"
            f"｜恢复 {u['out'].get('restored')}"
            f"｜impact: {im.get('说明')}｜预测一致 会空={pc.get('会空一致')} 缩={pc.get('缩一致')}")

        # ── S17 next：反思三档 ─────────────────────────────────────────
        n = tool(c, "next", {"corpus": "mine"})
        chk("S17", "★ next：后果三档都有读数（不是分数）",
            n["out"].get("ok") is True
            and all(isinstance(n["out"].get(t), int) for t in ("必须", "可延后", "可忽略")),
            f"必须 {n['out'].get('必须')}｜可延后 {n['out'].get('可延后')}"
            f"｜可忽略 {n['out'].get('可忽略')}")

        # ── S18 跨进程写锁：另一个进程拿着锁时，必须报错而不是静默写 ────
        # 子进程拿住锁 3 秒；本进程用 wait=0.2 去抢 ⇒ 必须 StoreBusy。
        holder = (
            "import sys, time; sys.path.insert(0, sys.argv[1]);"
            "from checks import lock as L;"
            "ctx = L.store_lock(sys.argv[2]); ctx.__enter__();"
            "print('HELD', flush=True); time.sleep(3)"
        )
        hp = subprocess.Popen([PY, "-c", holder, str(HERE), str(store)],
                              stdout=subprocess.PIPE, text=True, encoding="utf-8")
        assert hp.stdout.readline().strip() == "HELD"
        sys.path.insert(0, str(HERE))
        from checks import lock as L

        try:
            with L.store_lock(store, wait=0.2):
                busy = False
        except L.StoreBusy:
            busy = True
        hp.kill()
        hp.wait()
        chk("S18", "★ 跨进程写锁：别人持锁时 ⇒ StoreBusy（不静默写）",
            busy is True, f"StoreBusy={busy}")
        # ⚠️ 被 kill 的进程**不会**清掉 .lock —— 那正是「陈旧锁」的真实形状
        #   （文档写的 120 秒接管就是为它准备的）。测试自己收拾现场，
        #   否则后面的写会一直撞 StoreBusy（实测：S19 第一次就这么红的）。
        stale = store / ".lock"
        left_behind = stale.is_file()
        try:
            stale.unlink()
        except OSError:
            pass
        chk("S18b", "★ 被杀进程留下陈旧锁（文档写的接管场景，测试自己清场）",
            left_behind is True, f"遗留 .lock={left_behind}")

        # ── S19 derive 不许静默覆盖（同名维度第二次必须拒；force 才覆盖）──
        # ⚠️ S15 结尾把 sidecar 删了 ⇒ 这里得先建一次，再试第二次。
        d1 = tool(c, "derive", {"corpus": "mine", "name": "topic",
                                "values": {"stance-0001": "DAPO"},
                                "rationale": "先建一个", "derived_from": "模型推断"})
        d2 = tool(c, "derive", {"corpus": "mine", "name": "topic",
                                "values": {"stance-0001": "DAPO"},
                                "rationale": "再建一次同名维度",
                                "derived_from": "模型推断"})
        d3 = tool(c, "derive", {"corpus": "mine", "name": "topic",
                                "values": {"stance-0001": "DAPO"},
                                "rationale": "显式覆盖", "derived_from": "模型推断",
                                "force": True})
        chk("S19", "★ derive 同名维度不许静默覆盖（要换必须显式 force）",
            d1["out"].get("ok") is True and d2["isError"] is True
            and "force" in str(d2["out"].get("_raw", ""))
            and d3["out"].get("ok") is True,
            f"建={d1['out'].get('ok')}｜再建 {'拒' if d2['isError'] else '居然过了'}"
            f"｜force 后 {d3['out'].get('ok')}")

        # ── S20 ask(with_body)：正文与文件逐字相同（读内容 = 查询的一个选项）──
        qr = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"]})
        first = (qr["out"].get("items") or [{}])[0]
        # ⚠️ id 与文件名不同（stance-0001 vs stance-0001-dapo-attribution.md）
        fpath = sorted(corpus.glob("stance-0001*.md"))[0]
        file_body = fpath.read_text(encoding="utf-8").split("---", 2)[2].strip()
        nb = {"n_matched": len(qr["out"].get("items") or []),
              "n_returned": len(qr["out"].get("items") or [])}
        chk("S20", "★ ask(with_body)：连正文一起返回，且与文件**逐字相同**",
            first.get("n") == 1 and bool(first.get("title"))
            and first.get("text") == file_body
            and nb.get("n_matched") == qr["out"].get("n_answer"),
            f"{nb.get('n_matched')} 项 / 返回 {nb.get('n_returned')}"
            f"｜标题 {str(first.get('title'))[:24]}"
            f"｜正文 {len(first.get('text') or '')} 字 vs 文件 {len(file_body)} 字"
            f"｜逐字相同 {first.get('text') == file_body}")

        # ── S21 截断必须明说 ──────────────────────────────────────────
        rt = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"],
                              "max_chars": 10, "limit": 1})
        it = (rt["out"].get("items") or [{}])[0]
        nb2 = {"note": ("" if not it.get("truncated") else "截到")}
        chk("S21", "★ with_body 截断必须明说（truncated + note 报字数和条数）",
            it.get("truncated") is True and len(it.get("text") or "") == 10
            and bool(nb2.get("note")),
            f"truncated={it.get('truncated')}｜{len(it.get('text') or '')} 字"
            f"｜{str(it.get('title'))[:24]}")

        # ── S22 会话缓冲：曝光进缓冲、下钻标 promoted、会话之间不串 ──────
        sA = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"], "session": "sA",
                             "structure": True})
        sB = tool(c, "ask", {"corpus": "mine", "require": ["type=议题"], "session": "sB"})
        didA = (sA["out"].get("nodes") or [{}])[0].get("did", "")
        dA = tool(c, "drill", {"corpus": "mine", "did": didA,
                               "require": ["type=立场"], "session": "sA"})
        oA = tool(c, "orient", {"corpus": "mine", "session": "sA"})
        oB = tool(c, "orient", {"corpus": "mine", "session": "sB"})
        sessA, sessB = oA["out"].get("session") or {}, oB["out"].get("session") or {}
        chk("S22", "★ 会话缓冲：曝光进缓冲、下钻标 promoted、两个会话互不可见",
            (sA["out"].get("session") or {}).get("n_buffer") == 1
            and (sB["out"].get("session") or {}).get("n_buffer") == 1
            and dA["out"].get("session_promoted") is True
            and sessA.get("n_buffer") == 1 and sessA.get("promoted") == 1
            and sessB.get("n_buffer") == 1 and sessB.get("promoted") == 0,
            f"sA 缓冲 {sessA.get('n_buffer')} 条 / promoted {sessA.get('promoted')}"
            f"｜sB 缓冲 {sessB.get('n_buffer')} / promoted {sessB.get('promoted')}")

        # ── S23 缓冲有界 + 挤出记账 + 漏检率读数 ─────────────────────
        cfg = corpus.parent / "config.json"
        cfg.write_text('{"buffer_cap": 2}', encoding="utf-8")
        for q in ("type=立场", "type=议题", "type=案例"):
            tool(c, "ask", {"corpus": "mine", "require": [q], "session": "sC"})
        oC = tool(c, "orient", {"corpus": "mine", "session": "sC"})
        sc = oC["out"].get("session") or {}
        # 再问一次被挤掉的支所在的题 ⇒ 记一次「漏检」
        tool(c, "ask", {"corpus": "mine", "require": ["type=立场"], "session": "sC"})
        oC2 = tool(c, "orient", {"corpus": "mine", "session": "sC"})
        sc2 = oC2["out"].get("session") or {}
        cfg.write_text('{"buffer_cap": 50}', encoding="utf-8")
        chk("S23", "★ 缓冲有界：挤掉记账、漏检率可读（挤掉后再提到 ⇒ 计数+）",
            sc.get("n_buffer") == 2 and sc.get("evicted", 0) >= 1
            and sc.get("漏检率") is not None
            and sc2.get("remention", 0) > sc.get("remention", 0),
            f"cap 2 ⇒ 缓冲 {sc.get('n_buffer')}｜挤出 {sc.get('evicted')}"
            f"｜漏检率 {sc.get('漏检率')} → {sc2.get('漏检率')}"
            f"（remention {sc.get('remention')} → {sc2.get('remention')}）")

        # ── S24 多个 ldv：不给名字 ⇒ 列出全部 ───────────────────────
        oAll = tool(c, "orient", {})
        names = [r["ldv"] for r in (oAll["out"].get("ldvs") or [])]
        chk("S24", "★ 多个 ldv：不给名字 ⇒ 列出全部（只数文件不建内核）",
            oAll["out"].get("ldv_count", 0) >= 1 and "mine" in names
            and all("n_items" in r for r in oAll["out"].get("ldvs") or []),
            f"{oAll['out'].get('ldv_count')} 个 ldv："
            f"{[(r['ldv'], r['n_items'], r['sessions']) for r in oAll['out'].get('ldvs') or []]}")

        # ── S25 使用记录隔离（可选）：默认跨会话，开了隔离就互不影响 ────
        cfg.write_text('{"buffer_cap": 50, "isolate_usage": true}', encoding="utf-8")
        tool(c, "ask", {"corpus": "mine", "require": ["type=概念"], "session": "iso1"})
        n1 = (tool(c, "ask", {"corpus": "mine", "require": ["type=概念"], "structure": True,
                              "session": "iso1"})["out"].get("nodes") or [{}])[0].get("did")
        tool(c, "drill", {"corpus": "mine", "did": n1, "require": ["type=概念"],
                          "session": "iso1"})
        iso1 = tool(c, "ask", {"corpus": "mine", "require": ["type=概念"], "session": "iso1",
                               "structure": True})
        iso2 = tool(c, "ask", {"corpus": "mine", "require": ["type=概念"], "session": "iso2",
                               "structure": True})
        cfg.unlink()
        chk("S25", "★ 使用记录隔离（可选）：开了 isolate_usage ⇒ 两个会话的倾向互不影响",
            iso1["out"].get("ordered_by") == "usage"
            and iso2["out"].get("ordered_by") == "default",
            f"iso1 {iso1['out'].get('ordered_by')}｜iso2 {iso2['out'].get('ordered_by')}")

        # ── S26 agent 与人是等价消费者：agent 填的内容在出处里**单独一桶** ──
        # 用户 2026-10-24 定：三种缺口 agent 都能补，人只复核 ⇒ 唯一硬要求是
        # **标记如实**。这条判据验的就是「标记真的分得开」。
        qa = tool(c, "ask", {"corpus": "mine", "require": ["type=笔记"]})
        b = (qa["out"].get("provenance") or {}).get("buckets") or {}
        qb = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"]})
        b2 = (qb["out"].get("provenance") or {}).get("buckets") or {}
        chk("S26", "★ 等价消费者：agent 填的在出处里单独一桶，与语料原有的分开",
            b.get("agent") == 1 and all(k.startswith("模型") for k in b2),
            f"type=笔记 ⇒ {b}（remember 写的，filled_by=agent）"
            f"｜type=立场 ⇒ {b2}（语料原有的）")

        # ── S27 空库能起来：基石自动放，之后能直接 remember ────────────
        # 内核把空目录当「找不到语料」（空目录与路径写错长得一样）
        # ⇒ 插件在空库时自动放一条基石。这条判据验「从零开始」真的可用。
        (store / "_data" / "fresh" / "nodes").mkdir(parents=True, exist_ok=True)
        of = tool(c, "orient", {"corpus": "fresh"})
        rf = tool(c, "remember", {"corpus": "fresh", "id": "first",
                                  "fields": {"type": "笔记"}, "body": "第一条。"})
        qf = tool(c, "ask", {"corpus": "fresh", "require": ["type=笔记"]})
        chk("S27", "★ 空库能起来：基石自动放（1 项）⇒ 能直接 remember ⇒ 能查",
            of["out"].get("n_items") == 1 and rf["out"].get("written") is True
            and qf["out"].get("n_answer") == 1,
            f"空库 orient {of['out'].get('n_items')} 项"
            f"（键 {of['out'].get('n_keys')}）｜remember={rf['out'].get('written')}"
            f"｜查到 {qf['out'].get('n_answer')} 项")

        # ── S28 句柄：agent 只看「第 n 条」，id 留在本地 ─────────────────
        # 用户 2026-10-24 定：正文交给 agent，id 之类的记录留在 ldv 里。
        # 这条验三件事：默认不给 id / 动作能用句柄 / 句柄对不上查询要拒。
        h1 = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"]})
        it1 = (h1["out"].get("items") or [{}])[0]
        d1 = tool(c, "drill", {"corpus": "mine", "n": 1, "require": ["type=立场"]})
        u1 = tool(c, "undo", {"corpus": "mine", "n": 1, "require": ["type=立场"]})
        # 换一次查询 ⇒ 句柄表被覆盖 ⇒ 旧 require 的句柄必须被拒
        tool(c, "ask", {"corpus": "mine", "require": ["type=议题"]})
        d2 = tool(c, "drill", {"corpus": "mine", "n": 1, "require": ["type=立场"]})
        chk("S28", "★ 句柄：默认只给正文+句柄（无 id）；drill/undo 用句柄能动作；"
                   "句柄对不上查询 ⇒ 拒",
            "answer_ids" not in h1["out"] and "nodes" not in h1["out"]
            and it1.get("n") == 1 and bool(it1.get("text"))
            # ⚠️ 不断言 recorded=True：同一 (qfp,did) 在 S16 已经记过 ⇒ 这里是去重
            #   （recorded=False 是**对的**，不是失败）
            and d1["out"].get("ok") is True and d1["out"].get("did")
            and u1["out"].get("ok") is True and u1["out"].get("restored") is True
            and d2["out"].get("ok") is False and "另一次查询" in str(d2["out"].get("why")),
            f"第1条「{str(it1.get('title'))[:16]}」｜drill={d1['out'].get('ok')}"
            f"/{d1['out'].get('recorded')}｜undo 恢复={u1['out'].get('restored')}"
            f"｜换查询后再 drill ⇒ {str(d2['out'].get('why'))[:26]}")

        # ── S29 按支读：structure 拿 must/mustnot ⇒ 再 ask 就是那一支的成员 ──
        # 原 `read(did=)` 并进 ask 后，「只看某一支」靠**分支自己的定义**：
        # 拿 `must`/`mustnot` 再问一次，答案集就是那一支（不必知道 did）。
        st = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"],
                             "structure": True})
        br = (st["out"].get("nodes") or [{}])[0]
        same = tool(c, "ask", {"corpus": "mine",
                               "require": br.get("must") or [],
                               "forbid": br.get("mustnot") or []})
        chk("S29", "★ 按支读（替代 read(did=)）：拿分支的 must/mustnot 再问一次 "
                   "⇒ 答案就是那一支",
            br.get("must") is not None
            and same["out"].get("n_answer") == br.get("n_members"),
            f"支 {br.get('did')} 的 must={br.get('must')}｜n_members={br.get('n_members')}"
            f"｜再问得 {same['out'].get('n_answer')} 项")

        # ── S30 密钥不进仓库：扫**已跟踪文件**，命中就红 ────────────────
        # 用户 2026-10-24：「别让它进仓库了」。.gitignore 挡的是「以后误 add」，
        # 这条扫的是「已经在里面的」—— 两层，缺一层都可能漏。
        import re as _re
        import subprocess as _sp

        repo_root = HERE
        try:
            tracked = _sp.run(["git", "ls-files"], cwd=str(repo_root), capture_output=True,
                              text=True, encoding="utf-8").stdout.splitlines()
        except OSError:
            tracked = []
        pat = _re.compile(r"sk-[0-9a-zA-Z]{24,}")
        leaks = []
        for rel in tracked:
            p = repo_root / rel
            if not p.is_file() or p.suffix in (".pyc", ".png", ".jpg"):
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pat.search(txt):
                leaks.append(rel)
        chk("S30", "★ 密钥不进仓库：已跟踪文件里扫不到 sk- 开头的 key",
            not leaks, f"扫了 {len(tracked)} 个已跟踪文件｜泄漏：{leaks or '无'}")

        # ── S31 「不许猜」三条：多选都列 / 空要说明 / 超出说不记 ────────
        # 用户 2026-10-24：同时能回答就交给 agent 兼并，猜肯定不行；
        # 缓冲只记顺序编号，超出回复「不记顺序了」；查询覆盖不上是去要方向/节点，
        # 不是拿别的顶上。
        q1 = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"]})
        # ⚠️ 要**已知键、零命中**（type=立场 与 source.kind=教材 都是词表里的键，
        #   但两者没有交集）⇒ 走 ok=True + n_answer=0 那条路，
        #   而不是 out_of_structure（那是未知键，另一条路）。
        q2 = tool(c, "ask", {"corpus": "mine",
                              "require": ["type=立场", "source.kind=教材"]})
        d3 = tool(c, "drill", {"corpus": "mine", "n": 999, "require": ["type=立场"]})
        chk("S31", "★ 三条不许猜：多选都列出来 / 空答案说明去哪要 / 顺序超出明说",
            "都列出来" in str(q1["out"].get("note"))
            and "不许猜" in str(q2["out"].get("items_note"))
            and "顺序不记了" in str(d3["out"].get("why")),
            f"note 含「都列出来」={('都列出来' in str(q1['out'].get('note')))}"
            f"｜空答案：{str(q2['out'].get('items_note'))[:30]}"
            f"｜超界：{str(d3['out'].get('why'))[:26]}")

    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（S1–S31）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
