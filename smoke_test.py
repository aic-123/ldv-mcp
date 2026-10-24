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
    S16 ★ undo：反事实（删了会怎样）⇒ 答案丢它；恢复后答案与指纹逐字相同
    S17 ★ next：反思三档（必须/可延后/可忽略）都有读数
    S18 ★ 跨进程写锁：别人持锁 ⇒ StoreBusy（不静默写）；S18b 被杀进程留下陈旧锁
    S19 ★ derive 同名维度不许静默覆盖（要换必须显式 force）
    S20 ★ read：读得到标题与正文，正文与文件逐字相同
    S21 ★ read 截断必须明说（truncated + note 报字数和条数）
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
                      "read", "derive", "undo", "next"], f"{names}")

        o = tool(c, "orient", {"corpus": "mine"})
        keys = o["out"].get("keys") or []
        chk("S3", "orient 报词汇表 + 项数（「我记得」）",
            o["out"].get("n_keys", 0) > 0 and o["out"].get("n_items") == 36
            and isinstance(o["out"].get("recent_calls"), list),
            f"{o['out'].get('n_keys')} 键｜{o['out'].get('n_items')} 项｜"
            f"透镜 frozen={((o['out'].get('lens') or {}).get('frozen'))}")

        q = tool(c, "ask", {"corpus": "mine", "require": keys[:1]})
        chk("S4", "ask 正常：支序 + 查询式 + 透镜字段 + 出处摘要",
            q["out"].get("ok") and "query" in q["out"] and "lens" in q["out"]
            and q["out"].get("n_branches", 0) > 0
            and "provenance" in q["out"],
            f"{q['out'].get('n_answer')} 项 / {q['out'].get('n_branches')} 支｜"
            f"ordered_by={q['out'].get('ordered_by')}")

        bad = tool(c, "ask", {"corpus": "mine", "require": ["不存在的键=值"]})
        chk("S5", "★ 未知键 ⇒ isError（闸在挡，不是 prompt）",
            bad["isError"] is True and bad["out"].get("out_of_structure") is True,
            f"{str(bad['out'].get('why'))[:60]}")

        # S6 自动冻结：ask 带 lens ⇒ 一步到位；内部重复冻结仍拒
        lens = {"插件": "keyset", "参数": {"说明": "按字段交集切分"}}
        q2 = tool(c, "ask", {"corpus": "mine", "require": keys[:1], "lens": lens,
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
        q3 = tool(c, "ask", {"corpus": "mine", "require": keys[:1]})
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
        qd = tool(c, "ask", {"corpus": "mine", "require": ["topic=DAPO"]})
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
        chk("S16", "★ undo：删项 ⇒ 答案丢它；恢复后答案与指纹逐字相同",
            u["out"].get("ok") is True
            and "stance-0001" in (u["out"].get("answer_lost") or [])
            and u["out"].get("restored") is True,
            f"丢 {u['out'].get('answer_lost')}｜锥 {len(u['out'].get('cone') or [])} 层"
            f"｜恢复 {u['out'].get('restored')}")

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

        # ── S20 read：读内容 —— 与文件逐字相同，且 id 与那一支的成员一致 ──
        qr = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"]})
        did_r = (qr["out"].get("nodes") or [{}])[0].get("did", "")
        rd = tool(c, "read", {"corpus": "mine", "did": did_r})
        first = (rd["out"].get("items") or [{}])[0]
        # ⚠️ id 与文件名不同（`stance-0001` vs `stance-0001-dapo-attribution.md`）
        #   —— 按前缀找，别硬拼 `.md`
        fpath = sorted(corpus.glob("stance-0001*.md"))[0]
        raw = fpath.read_text(encoding="utf-8")
        file_body = raw.split("---", 2)[2].strip()
        chk("S20", "★ read：读得到标题与正文，且正文与文件**逐字相同**",
            rd["out"].get("ok") is True
            and first.get("id") == "stance-0001"
            and bool(first.get("title"))
            and first.get("body") == file_body
            # ⚠️ 比的是**那一支的成员数**，不是整题答案数 ——
            #   分支是答案的划分（D9 只有 1 项，整题 3 项）
            and rd["out"].get("n_matched")
            == (qr["out"].get("nodes") or [{}])[0].get("n_members"),
            f"{rd['out'].get('n_matched')} 项 / 返回 {rd['out'].get('n_returned')}"
            f"｜标题 {str(first.get('title'))[:24]}"
            f"｜正文 {len(first.get('body') or '')} 字 vs 文件 {len(file_body)} 字"
            f"｜逐字相同 {first.get('body') == file_body}")

        # ── S21 截断必须明说 ──────────────────────────────────────────
        rt = tool(c, "read", {"corpus": "mine", "ids": ["stance-0001"],
                              "max_chars": 10})
        it = (rt["out"].get("items") or [{}])[0]
        chk("S21", "★ read 截断必须明说（truncated + note 里报字数和条数）",
            it.get("truncated") is True and len(it.get("body") or "") == 10
            and "截到" in str(rt["out"].get("note")),
            f"truncated={it.get('truncated')}｜{len(it.get('body') or '')} 字"
            f"｜原 {it.get('body_chars')} 字｜{str(rt['out'].get('note'))[:40]}")

        # ── S22 会话缓冲：曝光进缓冲、下钻标 promoted、会话之间不串 ──────
        sA = tool(c, "ask", {"corpus": "mine", "require": ["type=立场"], "session": "sA"})
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
        n1 = (tool(c, "ask", {"corpus": "mine", "require": ["type=概念"],
                              "session": "iso1"})["out"].get("nodes") or [{}])[0].get("did")
        tool(c, "drill", {"corpus": "mine", "did": n1, "require": ["type=概念"],
                          "session": "iso1"})
        iso1 = tool(c, "ask", {"corpus": "mine", "require": ["type=概念"], "session": "iso1"})
        iso2 = tool(c, "ask", {"corpus": "mine", "require": ["type=概念"], "session": "iso2"})
        cfg.unlink()
        chk("S25", "★ 使用记录隔离（可选）：开了 isolate_usage ⇒ 两个会话的倾向互不影响",
            iso1["out"].get("ordered_by") == "usage"
            and iso2["out"].get("ordered_by") == "default",
            f"iso1 {iso1['out'].get('ordered_by')}｜iso2 {iso2['out'].get('ordered_by')}")

    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（S1–S25）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
