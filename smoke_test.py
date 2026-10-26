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
    S27 ★ 空库能起来：**0 项、不写占位文件** ⇒ 能直接 remember ⇒ 能查
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

    def __init__(self, env_extra: dict | None = None, server_path=None):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(env_extra or {})
        self.p = subprocess.Popen([PY, str(server_path or HERE / "server.py")],
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
        # ★ 2026-10-24 加 remember_answer（答案编进树）⇒ 四件动作 + **四件**能力
        chk("S2", "★ 工具面 = 四件动作 + 四件能力（真人零动作，能力不缩水）",
            names == ["orient", "ask", "remember", "drill",
                      "derive", "remember_answer", "undo", "next"], f"{names}")

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
        chk("S16", "★ undo：删项 ⇒ 答案丢它（**算出来的，不是删出来的**）；"
                   "副本上真删、**什么都不改**；用内核 impact 预测并与实测对照",
            u["out"].get("ok") is True
            and "stance-0001" in (u["out"].get("answer_lost") or [])
            and u["out"].get("untouched") is True
            and bool(u["out"].get("answer_note"))
            # ★ 2026-10-24 同步：不再手写成员 diff，改用内核 impact；
            #   预测与实测必须一致（会空 / 缩 两条）
            and bool(im.get("说明")) and pc.get("会空一致") is True
            and pc.get("缩一致") is True,
            f"丢 {u['out'].get('answer_lost')}｜锥 {len(u['out'].get('cone') or [])} 层"
            f"｜untouched={u['out'].get('untouched')}"
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

        # ── S27 空库能起来：**0 项**，不写任何占位文件，之后能直接 remember ──
        # 2026-10-25 用户定：空库不该靠一个占位文件才能起来。
        # 内核现在接受空语料（`load_nodes` 返回 `{}`、`find_corpus` 用 `is_dir()` 判
        # 「目录存在」而不是「有 md」）⇒ 空库就是 0 项。
        fresh = store / "_data" / "fresh" / "nodes"
        fresh.mkdir(parents=True, exist_ok=True)
        of = tool(c, "orient", {"corpus": "fresh"})
        # ★ 断言「**没写占位文件**」——这是这一条的重点，不是附带
        no_files = not any(fresh.glob("*.md"))
        rf = tool(c, "remember", {"corpus": "fresh", "id": "first",
                                  "fields": {"type": "笔记"}, "body": "第一条。"})
        qf = tool(c, "ask", {"corpus": "fresh", "require": ["type=笔记"]})
        chk("S27", "★ 空库能起来：0 项、**不写占位文件** ⇒ 能直接 remember ⇒ 能查",
            of["out"].get("n_items") == 0 and no_files
            and rf["out"].get("written") is True
            and qf["out"].get("n_answer") == 1,
            f"空库 orient {of['out'].get('n_items')} 项"
            f"｜目录里没写文件={no_files}"
            f"｜remember={rf['out'].get('written')}"
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
            # ★ 2026-10-24 重写后不再有「恢复」这一步：untouched=True 即证明
            and u1["out"].get("ok") is True and u1["out"].get("untouched") is True
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

        # ── S32 答案缓存：编进树 ⇒ 命中不用再问 agent；语料变 ⇒ 作废 ────
        # 用户 2026-10-24：先查树，找不到再问 agent，答完编回树。
        ra = tool(c, "remember_answer", {
            "corpus": "mine", "require": ["type=立场"],
            "answer": "三条立场：DAPO 的问题在损失聚合与裁剪上界；"
                      "长度惩罚与 clip-higher 是候选处置。",
            "source": ["stance-0001"]})
        hit = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"]})
        ca = hit["out"].get("cached_answer") or {}
        # 语料一变 ⇒ 旧缓存必须作废（不是静默沿用）
        tool(c, "remember", {"corpus": "mine", "id": "cache-probe",
                             "fields": {"type": "笔记"}, "body": "加一条，让指纹变。"})
        stale = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"]})
        chk("S32", "★ 答案缓存：编进树后同类问题**先命中**（from_cache + filled_by）；"
                   "语料指纹一变 ⇒ 明说过期、不采用",
            ra["out"].get("written") is True
            and ca.get("from_cache") is True and ca.get("filled_by") == "agent"
            and "长度惩罚" in str(ca.get("answer"))
            and "过期" in str(stale["out"].get("cache_note"))
            and stale["out"].get("cached_answer") is None,
            f"写入 {ra['out'].get('key')}｜命中 {ca.get('from_cache')}"
            f"｜filled_by={ca.get('filled_by')}"
            f"｜语料变后：{str(stale['out'].get('cache_note'))[:30]}")

        # ── S33：支序闸门**会开火**（外部评审 A1，2026-10-25）──────────
        # 旧写法两个入参是同一个列表推导 ⇒ 恒真 ⇒ 闸门结构上不可能开火。
        # 新写法两个来源（内核序 vs 曝光序）。这里做**反向对照**：
        # 把曝光序强行反转的 server 副本必须抛 CheckError；原版不抛。
        src = io.open(HERE / "server.py", encoding="utf-8").read()
        line = next(l for l in src.splitlines()
                    if "V.check_order(raw_dids, exposed)" in l)
        a1, a2 = [x.strip() for x in
                  line.split("check_order(", 1)[1].split(")", 1)[0].split(",", 1)]
        chk("S33a", "支序闸门：两个入参来自不同来源（防退回「自已比自已」）",
            a1 != a2, f"实参 {a1} vs {a2}")
        rev = src.replace('exposed = [n["did"] for n in nodes]',
                          'exposed = list(reversed([n["did"] for n in nodes]))', 1)
        assert rev != src, "S33 反转锚点没匹配上 —— 服务器那段变了，判据要跟着改"
        # ⚠️ require 要用**缓存里没有的组合**：S32 刚记了 `type=立场` 单键，
        #   用同一个 require 会命中缓存**提前返回**，expose 根本不走 ⇒ 反转无效
        #   （第一版就这样，红得莫名其妙）。双键组合缓存里没有。
        rev_req = keys[:2]
        rp = HERE / "_srv_reverse.py"
        io.open(rp, "w", encoding="utf-8", newline="\n").write(rev)
        try:
            corpus2 = store / "_data" / "rev" / "nodes"
            corpus2.mkdir(parents=True)
            for f in sorted(builtin.glob("*.md")):
                shutil.copy2(f, corpus2 / f.name)
            c2 = MCP({"LDV_MCP_STORE": str(store)}, server_path=rp)
            rv = tool(c2, "ask", {"corpus": "rev", "require": rev_req,
                                  "structure": True, "session": "x"})
            # ⚠️ CheckError 的返回形状是 `{"_raw": 原文}`（isError 路径），
            #   没有 `ok` 键 —— 断言要认这两种形状，别只认 ok=False。
            ok_bad = ("支序" in json.dumps(rv["out"], ensure_ascii=False)
                      and (rv["out"].get("ok") is False
                           or "_raw" in rv["out"]))
            c2.close()
        finally:
            rp.unlink(missing_ok=True)
        chk("S33b", "支序闸门：曝光序被反转 ⇒ ask 当场抛 CheckError（会开火）", ok_bad,
            json.dumps(rv.get("out") or {}, ensure_ascii=False)[:90])
        qok = tool(c, "ask", {"corpus": "mine", "require": rev_req,
                              "structure": True, "session": "x"})
        chk("S33c", "支序闸门：同一个查询在原版上不抛（正常路径不受影响）",
            qok["out"].get("ok") is True and len(qok["out"].get("nodes") or []) > 0)

        # ── S34：max_branches 截断且账平（外部评审 A2，2026-10-25）────
        # 空需求支数最多（内置语料 30+ 支）⇒ max_branches=3 才真的截。
        q3 = tool(c, "ask", {"corpus": "mine", "require": [],
                             "structure": True, "max_branches": 3})
        o3 = q3["out"]
        tot, shown, hid = (o3.get("branches_total"), o3.get("branches_shown"),
                           o3.get("branches_hidden"))
        chk("S34a", "max_branches=3 ⇒ 只给 3 支，total/shown/hidden 三者账平",
            o3.get("ok") is True and len(o3.get("nodes") or []) == 3
            and shown == 3 and tot > 3 and shown + hid == tot,
            f"total={tot} shown={shown} hidden={hid}"
            f"｜实际 {len(o3.get('nodes') or [])} 支")
        q4 = tool(c, "ask", {"corpus": "mine", "require": [],
                             "structure": True})
        q5 = tool(c, "ask", {"corpus": "mine", "require": [],
                             "structure": True, "max_branches": 0})
        same = (json.dumps(q4["out"], ensure_ascii=False, sort_keys=True)
                == json.dumps(q5["out"], ensure_ascii=False, sort_keys=True))
        chk("S34b", "max_branches=0（默认）与不传该参数逐字相同", same)

        # ── S35：orient 倒读日志（外部评审 A4，2026-10-25）────────────
        import time as _t
        log = store / "log" / "mcp-calls.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        row = json.dumps({"tool": "ask", "actor": "agent", "summary": {}})
        with io.open(log, "w", encoding="utf-8", newline="\n") as f:
            for _ in range(10000):
                f.write(row + "\n")
        c3 = MCP({"LDV_MCP_STORE": str(store)})
        t0 = _t.perf_counter()
        r1 = tool(c3, "orient", {"corpus": "mine"})
        t1 = _t.perf_counter() - t0
        with io.open(log, "a", encoding="utf-8", newline="\n") as f:
            for _ in range(90000):
                f.write(row + "\n")
        t0 = _t.perf_counter()
        r2 = tool(c3, "orient", {"corpus": "mine"})
        t2 = _t.perf_counter() - t0
        c3.close()
        chk("S35", "orient 倒读日志：10 万行耗时 < 1 万行的 1.5 倍（且绝对 < 1s）",
            r1["out"].get("ok") is True and r2["out"].get("ok") is True
            and t2 < max(t1 * 1.5, t1 + 0.2) and t2 < 1.0,
            f"1 万行 {t1 * 1000:.1f}ms｜10 万行 {t2 * 1000:.1f}ms"
            f"｜比值 {t2 / max(t1, 1e-9):.2f}")

        # ── S36：否定闭环（外部评审 C1/C2/D1，2026-10-25）──────────────
        # ⚠️ 必须用**新建的干净语料**：`mine` 被 S10/S16 下钻过很多次，
        #   同一支的倾向已累计到 +8 ⇒ 一笔 -1 拉不动，读数会是「没跳」
        #   （第一版就这样，红得让人以为跳过逻辑坏了）。干净起点才测得准。
        neg_nodes = store / "_data" / "neg" / "nodes"
        neg_nodes.mkdir(parents=True)
        for f in sorted(builtin.glob("*.md")):
            shutil.copy2(f, neg_nodes / f.name)
        q0 = tool(c, "ask", {"corpus": "neg", "require": [], "structure": True})
        nodes0 = q0["out"].get("nodes") or []
        n_all = q0["out"].get("n_answer")
        d0, must0 = nodes0[0]["did"], list(nodes0[0].get("must") or [])
        bad = tool(c, "drill", {"corpus": "neg", "did": d0, "require": [],
                                "verdict": "wrong", "actor": "agent"})
        good = tool(c, "drill", {"corpus": "neg", "did": d0, "require": [],
                                 "verdict": "wrong", "actor": "human"})
        hit = tool(c, "ask", {"corpus": "neg", "require": [], "structure": True,
                              "reject_below": 0.0})
        asked = tool(c, "ask", {"corpus": "neg", "require": must0,
                                "reject_below": 0.0})
        dflt = tool(c, "ask", {"corpus": "neg", "require": []})
        chk("S36a", "★ 否定闭环：agent 判错被拒、人判错记 -1（倾向当场变负）",
            bad["out"].get("ok") is False
            and "human" in json.dumps(bad["out"], ensure_ascii=False)
            and good["out"].get("ok") is True
            and good["out"].get("outcome") == -1.0
            and (good["out"].get("tendency_after") or 0) < 0,
            f"agent 拒={'human' in json.dumps(bad['out'], ensure_ascii=False)}"
            f"｜outcome={good['out'].get('outcome')}"
            f"｜tend={good['out'].get('tendency_after')}")
        chk("S36b", "★ 否定闭环：带阈值 ⇒ 该支不摆出来，且差额可算",
            d0 in (hit["out"].get("skipped_rejected") or [])
            and d0 not in [n["did"] for n in (hit["out"].get("nodes") or [])]
            and hit["out"]["n_answer"] + hit["out"]["rejected_items"] == n_all,
            f"skipped={hit['out'].get('skipped_rejected')}"
            f"｜{hit['out']['n_answer']}+{hit['out']['rejected_items']}=={n_all}")
        chk("S36c", "★ 否定闭环：require 说全（含该支 must）⇒ 那支回来",
            (asked["out"].get("skipped_rejected") or []) == [],
            f"require={must0}｜skipped={asked['out'].get('skipped_rejected')}")
        chk("S36d", "★ 否定闭环：不传 reject_below ⇒ 一条都不跳（默认逐字不变）",
            (dflt["out"].get("skipped_rejected") or []) == []
            and dflt["out"].get("n_answer") == n_all,
            f"skipped={dflt['out'].get('skipped_rejected')}｜n_answer={dflt['out'].get('n_answer')}")

        # ── S37：derive 的**失败路径**（外部评审 D2）──────────────────
        # 「什么算问不出来」已经有结构判定（未知键 / 零命中）；这里守另一半：
        # derive **不许凭空造 id** —— agent 编一个不存在的节点，必须当场拒。
        # ⚠️ 这条是「失败路径判据」：S15 只验了 happy path（能查、能回退），
        #   而「编造 id」正是这个能力最容易被滥用的方式（坑 11e 同一条纪律）。
        dz = tool(c, "derive", {"corpus": "mine", "name": "编的维度",
                                "values": {"不存在-9999": "x"},
                                "rationale": "试一下", "derived_from": "编的"})
        # ⚠️ 错误返回有**两种形状**：业务错 `{"ok": False, "error": …}`、
        #   异常错 `{"_raw": 原文}`（SpecError 走这条）。判据必须两种都认 ——
        #   只认 ok=False 会把「真的拒了」判成「没拒」（S33b 踩过同一个坑）。
        dz_doc = dz["out"]
        chk("S37", "★ derive 不许凭空造 id：不在语料里的 id ⇒ 当场拒",
            (dz_doc.get("ok") is False or "_raw" in dz_doc)
            and "不在语料里" in json.dumps(dz_doc, ensure_ascii=False),
            json.dumps(dz_doc, ensure_ascii=False)[:90])

        # ── S38：自己的产物进结构（2026-10-25，评估补的那一环）──────────
        # `认知原型.md` 第三句承诺：「它能**包含自己** —— 自己的产物标成 `我=是`
        # 进结构，于是反思是可查询的项」。repo 侧靠 make_self_corpus 抽**仓库文档**；
        # MCP 侧的「自己」是 **AI 的运行时回答** ⇒ remember_answer 顺手写一条节点。
        # 四条：写进去 / 查得到 / 同键写新版不覆写 / 删了它缓存作废。
        own = store / "_data" / "own" / "nodes"
        own.mkdir(parents=True)
        (own / "m1.md").write_text('---\nid: "m1"\ntype: 事实\n---\n\n材料一。\n',
                                   encoding="utf-8", newline="\n")
        sa = tool(c, "remember_answer", {"corpus": "own", "require": ["type=事实"],
                                         "answer": "我的回答：材料一说明 X。",
                                         "source": ["m1"]})
        nid = sa["out"].get("node_id")
        q_self = tool(c, "ask", {"corpus": "own", "require": ["我=是"]})
        q_ans = tool(c, "ask", {"corpus": "own", "require": ["type=回答"]})
        chk("S38a", "★ 自己的产物进结构：答完 ⇒ `我=是` / `type=回答` 都查得到",
            bool(nid) and q_self["out"].get("n_answer") == 1
            and q_ans["out"].get("n_answer") == 1
            and "我的回答" in str((q_ans["out"].get("items") or [{}])[0].get("text")),
            f"node_id={nid}｜我=是 {q_self['out'].get('n_answer')} 项"
            f"｜type=回答 {q_ans['out'].get('n_answer')} 项")
        hit1 = tool(c, "ask", {"corpus": "own", "require": ["type=事实"]})
        chk("S38b", "★ 写自己的记录**不**让答案缓存失效（它是对材料的回答，不是材料）",
            (hit1["out"].get("cached_answer") or {}).get("from_cache") is True,
            f"from_cache={(hit1['out'].get('cached_answer') or {}).get('from_cache')}")
        sa2 = tool(c, "remember_answer", {"corpus": "own", "require": ["type=事实"],
                                          "answer": "我的第二版回答。",
                                          "source": ["m1"]})
        q_self2 = tool(c, "ask", {"corpus": "own", "require": ["我=是"]})
        chk("S38c", "★ 同键再答 ⇒ 写新版（-2），**不覆写**旧记录（我改过想法要留痕）",
            sa2["out"].get("node_id", "").endswith("-2")
            and q_self2["out"].get("n_answer") == 2,
            f"第二版={sa2['out'].get('node_id')}"
            f"｜我=是 现在 {q_self2['out'].get('n_answer')} 项")
        # 手工删掉**缓存当前指向的**那条（MCP 没有删节点的工具 —— undo 是「只算不改」）。
        # ⚠️ 必须是第二版：`S40b` 之后缓存指向最新的那次回答（`-2`），
        #    删第一版不会让缓存失效 —— 删错对象会让这条判据「看着红其实是测错了」。
        cur_nid = sa2["out"].get("node_id") or nid
        (own / (str(cur_nid) + ".md")).unlink(missing_ok=True)
        hit2 = tool(c, "ask", {"corpus": "own", "require": ["type=事实"]})
        chk("S38d", "★ 删掉那条记录 ⇒ 缓存**作废并说明**（记忆删了，答案不能还在）",
            not (hit2["out"].get("cached_answer") or {}).get("from_cache")
            and "已被删除" in str(hit2["out"].get("cache_note")),
            f"删的是 {cur_nid}｜note={str(hit2['out'].get('cache_note'))[:46]}")

        # ── S39：**推理闭环**（失配 → 改结构 → 自洽，2026-10-25）──────────
        # 定位文档说「它是结构主义那一支的认知模型：推理 = 失配驱动的结构修复」。
        # 这句话在 2026-10-25 之前**没有判据守着** —— 三种失配各有判据、改结构有账，
        # 但「改完失配真的没了吗」没人守。这一条补的就是**闭环的终点**。
        #   ⚠️ ⑤ 是**反向对照**：不 derive 再问必须仍然报超出结构 ——
        #      否则 ②④ 读的是同一个来源，判据是空转。
        loop = store / "_data" / "loop" / "nodes"
        loop.mkdir(parents=True)
        for i in range(3):
            (loop / f"l{i}.md").write_text(
                f'---\nid: "l{i}"\ntype: 实验\n---\n\n第 {i} 条。\n',
                encoding="utf-8", newline="\n")
        need = ["project=alpha"]
        miss = tool(c, "ask", {"corpus": "loop", "require": need})
        again = tool(c, "ask", {"corpus": "loop", "require": need})
        chk("S39a", "★ 推理闭环①：需求与结构失配 ⇒ 报「超出结构」+ 说清能自己补",
            miss["out"].get("ok") is False
            and miss["out"].get("out_of_structure") is True
            and miss["out"].get("can_agent_fill") is True,
            f"out_of_structure={miss['out'].get('out_of_structure')}"
            f"｜can_agent_fill={miss['out'].get('can_agent_fill')}")
        chk("S39b", "★ 推理闭环⑤（**反向对照**）：不改结构再问 ⇒ 必须**仍然**超出结构",
            again["out"].get("out_of_structure") is True,
            f"第二次 out_of_structure={again['out'].get('out_of_structure')}")
        dv2 = tool(c, "derive", {"corpus": "loop", "name": "project",
                                 "values": {"l0": "alpha", "l1": "alpha",
                                            "l2": "beta"},
                                 "rationale": "按项目分",
                                 "derived_from": "材料字段 project"})
        chk("S39c", "★ 推理闭环②：改结构（derive）⇒ 结构多了这个维度",
            dv2["out"].get("ok") is True
            and bool(dv2["out"].get("keys_added")),
            f"keys_added={dv2['out'].get('keys_added')}")
        fixed = tool(c, "ask", {"corpus": "loop", "require": need})
        chk("S39d", "★ 推理闭环③：需求满足（同一个 ask 不再超出结构，且答得出）",
            fixed["out"].get("ok") is True
            and fixed["out"].get("out_of_structure") is not True
            and (fixed["out"].get("n_answer") or 0) > 0,
            f"ok={fixed['out'].get('ok')}｜n_answer={fixed['out'].get('n_answer')}"
            f"｜out_of_structure={fixed['out'].get('out_of_structure')}")
        nx2 = tool(c, "next", {"corpus": "loop", "require": []})
        dis = nx2["out"].get("失配") or {}
        chk("S39e", "★ 推理闭环④：**改完自洽** —— 结构没被这次修复弄出矛盾",
            dis.get("结构坏了") == 0 and dis.get("合计") == dis.get("表达力缺口"),
            f"失配={ {k: v for k, v in dis.items() if k != 'note'} }")

        # ── S40：真实使用时报的五条（2026-10-25，全部复现后修掉）─────────
        # 来源：用户在 DeepSeekHarness 里跑 `_probe/probe*.py` 报出来的。
        # 共同特征 —— **都是「静默」**：静默忽略参数、静默报成功、静默少给东西。
        real = store / "_data" / "real" / "nodes"
        real.mkdir(parents=True)
        for i in range(3):
            (real / f"r{i}.md").write_text(
                f'---\nid: "r{i}"\ntype: 立场\n---\n\n第 {i} 条。\n',
                encoding="utf-8", newline="\n")

        # ① 换 lens 不 force ⇒ 原来**静默用旧插件**（调用方以为换了切分方式）
        tool(c, "ask", {"corpus": "real", "require": ["type=立场"],
                        "lens": {"插件": "keyset"}, "rationale": "先冻一个"})
        lc = tool(c, "ask", {"corpus": "real", "require": ["type=立场"],
                             "lens": {"插件": "reach"}, "rationale": "想换"})
        chk("S40a", "★ 换透镜不 force ⇒ **拒**（不许静默用旧的那份）",
            lc["out"].get("ok") is False
            and lc["out"].get("lens_conflict") is True
            and "force" in str(lc["out"].get("error")),
            f"ok={lc['out'].get('ok')}｜error={str(lc['out'].get('error'))[:60]}")

        # ② 同键再答 ⇒ 原来报 written=true 但缓存**还是旧答案**（静默失败）
        tool(c, "remember_answer", {"corpus": "real", "require": ["type=立场"],
                                    "answer": "FIRST"})
        w2 = tool(c, "remember_answer", {"corpus": "real",
                                         "require": ["type=立场"],
                                         "answer": "SECOND"})
        hit3 = tool(c, "ask", {"corpus": "real", "require": ["type=立场"]})
        chk("S40b", "★ 同键再答 ⇒ 缓存**换成新答案**，并报 replaced=true",
            (hit3["out"].get("cached_answer") or {}).get("answer") == "SECOND"
            and w2["out"].get("replaced") is True,
            f"命中={(hit3['out'].get('cached_answer') or {}).get('answer')}"
            f"｜replaced={w2['out'].get('replaced')}")

        # ③ undo 传 did（方向 id）⇒ 原来报「语料里没有这一项：D2」（误导）
        sd = tool(c, "ask", {"corpus": "real", "require": [], "structure": True})
        did2 = (sd["out"].get("nodes") or [{}])[0].get("did")
        u2 = tool(c, "undo", {"corpus": "real", "did": did2, "require": []})
        u2txt = json.dumps(u2["out"], ensure_ascii=False)
        chk("S40c", "★ undo 传 did ⇒ 说清「方向 id 不是项 id」（不报「没有这一项」）",
            "方向 id" in u2txt and "项 id" in u2txt,
            u2txt[:90])

        # ④ next 的清单 ⇒ 原来只列两档，而 S17 的 docstring 说三个桶都列
        n3 = tool(c, "next", {"corpus": "real", "require": []})
        keys3 = set((n3["out"].get("清单") or {}).keys())
        chk("S40d", "★ next 的清单列**三档**（必须/可延后/可忽略）",
            {"必须", "可延后", "可忽略"} <= keys3, f"清单键={sorted(keys3)}")

        # ⑤ limit 截断 ⇒ 原来只在 items 为空时才报（3 条只给 1 条静默通过）
        lm = tool(c, "ask", {"corpus": "real", "require": ["type=立场"],
                             "limit": 1})
        chk("S40e", "★ limit 截断 ⇒ `items_truncated` + 说清共几条",
            lm["out"].get("items_truncated") is True
            and bool(lm["out"].get("items_note")),
            f"items={len(lm['out'].get('items') or [])}"
            f"｜n_answer={lm['out'].get('n_answer')}"
            f"｜note={str(lm['out'].get('items_note'))[:50]}")

        # ── S41 缓冲命中率：读数必须算得对，且「没记」≠「没命中」 ──────
        # 2026-10-26 用户要「缓冲命中率」时才发现：有 `from_cache` 字段，
        # 但**没有聚合读数**（有字段 ≠ 有读数，第三次踩）。
        # 现在 `orient.cache` 从日志算；这里守三件事，重点是 b：
        # 老日志没有 `cached` 字段，拿它当「没命中」会把命中率算低。
        (store / "_data" / "hit" / "nodes").mkdir(parents=True, exist_ok=True)
        (store / "_data" / "hit" / "nodes" / "h1.md").write_text(
            '---\nid: "h1"\ntype: 事实\n---\n\n一条。\n',
            encoding="utf-8", newline="\n")
        c0 = tool(c, "orient", {"corpus": "hit"})
        chk("S41a", "一次都没问 ⇒ 命中率是 **None**（不是 0）",
            (c0["out"].get("cache") or {}).get("答案缓存命中率") is None
            and (c0["out"].get("cache") or {}).get("ask 数") == 0,
            f"cache={ {k: v for k, v in (c0['out'].get('cache') or {}).items() if k != 'note'} }")
        tool(c, "ask", {"corpus": "hit", "require": ["type=事实"]})
        tool(c, "remember_answer", {"corpus": "hit", "require": ["type=事实"],
                                    "answer": "答一。", "source": ["h1"]})
        tool(c, "ask", {"corpus": "hit", "require": ["type=事实"]})
        c1 = (tool(c, "orient", {"corpus": "hit"})["out"].get("cache") or {})
        chk("S41b", "★ 问了 2 次、命中 1 次 ⇒ 报 ask=2 命中=1 率=0.5",
            c1.get("ask 数") == 2 and c1.get("命中数") == 1
            and c1.get("答案缓存命中率") == 0.5,
            f"ask={c1.get('ask 数')}｜命中={c1.get('命中数')}"
            f"｜率={c1.get('答案缓存命中率')}")
        # ★ 反向对照：往日志里塞一条**老格式**（没有 cached 字段）的 ask
        logp = store / "log" / "mcp-calls.jsonl"
        with io.open(logp, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"at": "2026-01-01T00:00:00", "tool": "ask",
                                "actor": "agent",
                                "args": {"corpus": "hit", "require": []},
                                "summary": {"ok": True, "n_answer": 0}},
                               ensure_ascii=False) + "\n")
        c2 = (tool(c, "orient", {"corpus": "hit"})["out"].get("cache") or {})
        chk("S41c", "★ 老日志没 `cached` 字段 ⇒ **不计入分母**（没记 ≠ 没命中）",
            c2.get("ask 数") == 2 and c2.get("答案缓存命中率") == 0.5,
            f"塞一条老格式后：ask={c2.get('ask 数')}"
            f"｜率={c2.get('答案缓存命中率')}（若算成 3 次 ⇒ 率被压低）")

        # ── S42 资格闸：agent 没资格回报「已验证」/「我写的」 ────────────
        # 2026-10-26 实测挖到的洞：`remember(fields={"evidence_status":"已验证"})`
        # 和 `remember(filled_by="human")` 都能写成，出处分桶与「已验证」那条红线
        # 同时失效。而**已有判据只扫系统源码**（证明「系统不会自己写」），
        # 走 `fields` 数据通道时源码里没有那个字面量 ⇒ 判据照样绿。
        # 这一组补的正是那一半：**走数据通道**喂进去，必须红。
        qd = store / "_data" / "qual" / "nodes"
        qd.mkdir(parents=True, exist_ok=True)
        (qd / "q1.md").write_text('---\nid: "q1"\ntype: 事实\n---\n\n一条。\n',
                                  encoding="utf-8", newline="\n")
        v1 = tool(c, "remember", {"corpus": "qual", "id": "fake1", "body": "x",
                                  "fields": {"type": "事实",
                                             "evidence_status": "已验证"}})
        chk("S42a", "★ agent 声称「已验证」⇒ 拒，且**一个字节都没落盘**",
            (v1["out"].get("ok") is False or "_raw" in v1["out"])
            and "人的档" in json.dumps(v1["out"], ensure_ascii=False)
            and not (qd / "fake1.md").exists(),
            f"ok={v1['out'].get('ok')}｜落盘={(qd / 'fake1.md').exists()}")
        v2 = tool(c, "remember", {"corpus": "qual", "id": "fake2", "body": "x",
                                  "fields": {"type": "事实"}, "filled_by": "human"})
        chk("S42b", "★ agent 写 `filled_by=human` ⇒ 拒"
                    "（**不能替别人声明来源** —— 不是「没资格写」）",
            (v2["out"].get("ok") is False or "_raw" in v2["out"])
            and not (qd / "fake2.md").exists(),
            f"ok={v2['out'].get('ok')}｜落盘={(qd / 'fake2.md').exists()}")
        # ★ 闸不许误伤：写自己的来源（等于 actor）必须允许
        v2b = tool(c, "remember", {"corpus": "qual", "id": "own1", "body": "x",
                                   "fields": {"type": "事实"}, "filled_by": "agent"})
        chk("S42b2", "★ agent 写 `filled_by=agent`（等于自己的 actor）⇒ 允许",
            v2b["out"].get("written") is True
            and v2b["out"].get("filled_by") == "agent",
            f"written={v2b['out'].get('written')}"
            f"｜filled_by={v2b['out'].get('filled_by')}")
        # ★ 反向对照：闸不能把人一起拦死 —— actor=human 必须写得进去
        v3 = tool(c, "remember", {"corpus": "qual", "id": "real1", "body": "x",
                                  "fields": {"type": "事实",
                                             "evidence_status": "已验证"},
                                  "actor": "human"})
        chk("S42c", "★ 反向对照：`actor=human` 声称「已验证」⇒ **接受**"
                    "（只拦没资格的人，不拦有资格的）",
            v3["out"].get("written") is True
            and (qd / "real1.md").is_file()
            and "已验证" in (qd / "real1.md").read_text(encoding="utf-8"),
            f"written={v3['out'].get('written')}｜落盘={(qd / 'real1.md').exists()}")
        # ★ 也不能拦正常写：agent 写「未验证」必须允许（否则闸做过头了）
        v4 = tool(c, "remember", {"corpus": "qual", "id": "ok1", "body": "x",
                                  "fields": {"type": "事实",
                                             "evidence_status": "未验证"}})
        chk("S42d", "★ 闸不许做过头：agent 写「未验证」⇒ 允许",
            v4["out"].get("written") is True
            and v4["out"].get("filled_by") == "agent",
            f"written={v4['out'].get('written')}"
            f"｜filled_by={v4['out'].get('filled_by')}")

        # ── S43 变更回执：本会话改了哪几处（2026-10-26） ──────────────
        # 为什么要有它：`recent_calls` 只取最近 5 行，而且**混着读操作**
        # （orient / ask 把写稀释掉）⇒ agent 做完一串事，人看不出它动了什么。
        # 回执只收写、按会话归属。★ S43c 是反向对照：读操作一条都不许进。
        rd = store / "_data" / "rcpt" / "nodes"
        rd.mkdir(parents=True, exist_ok=True)
        for i in range(4):
            (rd / f"r{i}.md").write_text(
                f'---\nid: "r{i}"\ntype: 事实\n---\n\n第 {i} 条。\n',
                encoding="utf-8", newline="\n")
        rsid = "rcpt-1"
        tool(c, "orient", {"corpus": "rcpt", "session": rsid})
        tool(c, "remember", {"corpus": "rcpt", "id": "c1", "body": "一",
                             "fields": {"type": "事实"}, "session": rsid})
        tool(c, "remember", {"corpus": "rcpt", "id": "c2", "body": "二",
                             "fields": {"type": "事实"}, "session": rsid})
        # 另一个会话写的 —— **不许**混进本会话的回执
        tool(c, "remember", {"corpus": "rcpt", "id": "zz", "body": "别人的",
                             "fields": {"type": "事实"}, "session": "rcpt-other"})
        tool(c, "derive", {"corpus": "rcpt", "name": "proj",
                           "values": {"r0": "p", "r1": "q"},
                           "rationale": "测", "derived_from": "材料",
                           "session": rsid})
        tool(c, "ask", {"corpus": "rcpt", "require": [], "session": rsid})
        tool(c, "drill", {"corpus": "rcpt", "n": 1, "require": [],
                          "session": rsid, "verdict": "wrong", "actor": "human"})
        chg = (tool(c, "orient", {"corpus": "rcpt", "session": rsid})["out"]
               .get("changes") or {})
        chk("S43a", "★ 本会话的写**点得出名**，别的会话的不混进来",
            chg.get("新写") == ["c1", "c2"],
            f"新写={chg.get('新写')}（若含 zz ⇒ 会话归属错了）")
        chk("S43b", "★ 造维度与**降权支**也进回执（降权算变更，不是读）",
            chg.get("造维度") == ["proj"] and bool(chg.get("降权支")),
            f"造维度={chg.get('造维度')}｜降权支={chg.get('降权支')}")
        chk("S43c", "★ 反向对照：读操作（ask / orient）**一条都不进回执**"
                    " —— 否则回执就是 recent_calls 换了个名字",
            chg.get("合计") == 4,
            f"合计={chg.get('合计')}（应 = 2 写 + 1 维度 + 1 降权 = 4）")
        chg2 = (tool(c, "orient", {"corpus": "rcpt"})["out"].get("changes") or {})
        chk("S43d", "不带 session ⇒ 汇总全部，但**明说这不是「本次」**",
            "zz" in (chg2.get("新写") or [])
            and "不是「本次」" in str(chg2.get("scope")),
            f"新写={chg2.get('新写')}｜scope={chg2.get('scope')}")

        # ── S44 派生维度的 `_meta` 必须**读得出来**（2026-10-26） ────────
        # 缺口原状：`entry.py` 加载 sidecar 时只取 `_meta["field"]` 与 `values`
        # ⇒ `derive` 强制要求的 `rationale` / `derived_from` 写进去后**没人读**。
        # `derive` 的 docstring 说「`derived_from` 决定答案里敢不敢把它当事实」，
        # 而代码从没读过它 —— **收了税没修路**。
        # ★ S44c 用一条**自定义字符串**做反向对照：硬编码「原材料」也会过 a/b，
        #   但过不了「传什么就报什么」。
        dv = store / "_data" / "dmeta" / "nodes"
        dv.mkdir(parents=True, exist_ok=True)
        for i in range(4):
            (dv / f"m{i}.md").write_text(
                f'---\nid: "m{i}"\ntype: 事实\n---\n\n第 {i} 条。\n',
                encoding="utf-8", newline="\n")
        MARK = "测-原材料-abc"          # 故意长得不像任何内置值
        tool(c, "derive", {"corpus": "dmeta", "name": "proj",
                           "values": {"m0": "p", "m1": "p", "m2": "q"},
                           "rationale": "按项目分得开：p/q 两簇",
                           "derived_from": MARK, "what": "项目归属"})
        dm = (tool(c, "orient", {"corpus": "dmeta"})["out"].get("derived_meta") or {})
        chk("S44a", "★ `orient.derived_meta` 把 `derive` 必填的那些**读出来**了",
            bool(dm.get("proj"))
            and dm["proj"].get("why") == "按项目分得开：p/q 两簇"
            and dm["proj"].get("what") == "项目归属"
            and dm["proj"].get("generated_by") == "agent",
            f"derived_meta.proj={json.dumps(dm.get('proj'), ensure_ascii=False)[:110]}")
        dp = (tool(c, "ask", {"corpus": "dmeta", "require": ["proj=p"]})["out"]
              .get("derived_provenance") or {})
        chk("S44b", "★ `ask` 对**用到的**派生维度给出 `derived_from`（**只这一个值**）",
            dp.get("proj") == MARK,
            f"derived_provenance={json.dumps(dp, ensure_ascii=False)[:110]}")
        chk("S44c", "★ 反向对照：**传什么就报什么**（不是硬编码「原材料」）",
            dp.get("proj") == MARK,
            f"写入 {MARK}｜报出 {dp.get('proj')}")

        # ── S45 类型错要报成类型错（2026-10-26 盲测 agent 踩出来的）────────
        # 传裸字符串 `require="type=事实"` ⇒ `list()` 按**字符**拆开 ⇒
        # 报「这些键不在词汇表里」——那是**误诊**：真实原因是参数类型错。
        bad = tool(c, "ask", {"corpus": "dmeta", "require": "type=事实"})
        badtxt = json.dumps(bad["out"], ensure_ascii=False)
        chk("S45a", "★ `require` 传字符串 ⇒ 报「要传列表」，**不是**「键不在词汇表」",
            ("要传**列表**" in badtxt or "要传列表" in badtxt)
            and "不在词汇表" not in badtxt,
            f"返回={badtxt[:120]}")
        good = tool(c, "ask", {"corpus": "dmeta", "require": ["type=事实"]})
        chk("S45b", "★ 反向对照：传列表 ⇒ 正常（类型检查不许误伤）",
            good["out"].get("ok") is True
            and good["out"].get("out_of_structure") in (None, False),
            f"ok={good['out'].get('ok')}"
            f"｜n_answer={good['out'].get('n_answer')}")

        # ── S46 「报告不知道」是一条正式出口（2026-10-26） ────────────────
        # 原来 agent 只有两条路：给个答案，或者沉默 —— 沉默在结构里不留痕，
        # 于是它倾向于编一个。给了这条出口，弃答才能被记录、被复核、被查询。
        WHY = "材料里没有「下载量」这个字段（字段只有 type/scope/notes/source/…）"
        u1 = tool(c, "remember_answer", {"corpus": "dmeta", "require": ["type=事实"],
                                         "unknown": True, "why": WHY,
                                         "session": "abst"})
        chk("S46a", "★ 弃答能写进去，且**节点记成 `type=未答`**（不是「回答」）",
            u1["out"].get("written") is True
            and (u1["out"].get("unknown") is True),
            f"written={u1['out'].get('written')}"
            f"｜node={u1['out'].get('node_id')}")
        hit = tool(c, "ask", {"corpus": "dmeta", "require": ["type=事实"],
                              "session": "abst"})
        ca = hit["out"].get("cached_answer") or {}
        chk("S46b", "★★ 再问同一问题 ⇒ 命中，且**明确报 `unknown: true` + `why`**"
                    "（不会把「上次判定答不出」当成答案）",
            ca.get("unknown") is True and ca.get("why") == WHY,
            f"unknown={ca.get('unknown')}｜why={str(ca.get('why'))[:60]}")
        # ★ 反向对照：弃答必须说清「缺什么」，否则那是「哑」不是「弃答」
        #   ⚠️ 键必须**有效**（`proj=p` 是 derive 出来的）—— 第一版用了语料里没有的
        #   `type=判断点`，结果先报失配，测不到 why 那条校验（判据自己抓到了）。
        bad2 = tool(c, "remember_answer", {"corpus": "dmeta",
                                           "require": ["proj=p"],
                                           "unknown": True})
        chk("S46c", "★ 反向对照：`unknown=true` 但**不给 why** ⇒ 拒"
                    "（不说清缺什么，就是哑不是弃答）",
            "_raw" in bad2["out"] and "why" in json.dumps(bad2["out"],
                                                          ensure_ascii=False),
            f"返回={json.dumps(bad2['out'], ensure_ascii=False)[:110]}")
        # ★ 弃答成为**可查的项**：`type=未答` 能筛出来
        allu = tool(c, "ask", {"corpus": "dmeta", "require": ["type=未答"],
                               "limit": 20, "session": "abst"})
        chk("S46d", "★ 弃答变成**可查的项**：`ask(require=['type=未答'])` 筛得出来",
            (allu["out"].get("n_answer") or 0) >= 1,
            f"n_answer={allu['out'].get('n_answer')}（弃答从「心里知道」变成"
            "「结构里记着」）")

    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（S1–S46）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
