"""ldv-mcp 冒烟测试：真起服务器，走一遍 MCP 握手 + 全部工具。

跑法（cwd = ldv-mcp）：python smoke_test.py

判的不是「服务器起来了」，是**每一件产品承诺的事真的发生了**：

    S1  initialize 握手，协议版本/能力/名字都对
    S2  tools/list 七件工具齐
    S3  vocabulary 返回的键，query 只能用这些（下一步验）
    S4  query 正常：支序原样 + 带查询式 + 带 spec 字段
    S5  ★ 未知键 ⇒ isError（第一道闸真的在挡，不是 prompt）
    S6  propose → freeze 全通；重复 freeze 不 force ⇒ 拒（不许静默换透镜）
    S7  冻结后 query 用上声明里的方向函数
    S8  drill ⇒ 使用记录真的落了一条（私人语料在 store 下）
    S9  usage_status 报「启用」+ 记录数 1
    S10 支序对拍：把 nodes 的 did 序列与内核直接跑的逐字相同
    S11 ★ 调用日志：**每次**工具调用都落一行（agent 的审计轨迹要完整）
    S12 ★ 出处摘要：buckets 之和 == n_answer，且内置语料 36/36 是 `filled_by: 模型`
    S13 ★ 演员分解：actor 是调用方**声明**的，照记；usage_status 报 agent 占比
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
        import os

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
        chk("S2", "tools/list 七件齐", len(names) == 7,
            f"{names}")

        v = tool(c, "vocabulary", {"corpus": "mine"})
        keys = v["out"].get("keys") or []
        chk("S3", "vocabulary 返回词汇表", v["out"].get("n_keys", 0) > 0
            and v["out"].get("n_items") == 36, f"{v['out'].get('n_keys')} 键｜{v['out'].get('n_items')} 项")

        q = tool(c, "query", {"corpus": "mine", "require": keys[:1]})
        chk("S4", "query 正常：支序 + 查询式 + spec 字段",
            q["out"].get("ok") and "query" in q["out"] and "spec" in q["out"]
            and q["out"].get("n_branches", 0) > 0,
            f"{q['out'].get('n_answer')} 项 / {q['out'].get('n_branches')} 支｜"
            f"ordered_by={q['out'].get('ordered_by')}")

        bad = tool(c, "query", {"corpus": "mine", "require": ["不存在的键=值"]})
        chk("S5", "★ 未知键 ⇒ isError（闸在挡，不是 prompt）",
            bad["isError"] is True and bad["out"].get("out_of_structure") is True,
            f"{str(bad['out'].get('why'))[:60]}")

        spec = {"插件": "keyset", "参数": {"说明": "按字段交集切分"}}
        p1 = tool(c, "propose_spec", {"corpus": "mine", "spec": spec,
                                      "rationale": "内置语料的字段天然多值，键集切分最直观"})
        cid = p1["out"].get("candidate_id", "")
        chk("S6a", "propose 存候选", bool(cid), f"候选 {cid}")
        f1 = tool(c, "freeze_spec", {"corpus": "mine", "candidate_id": cid})
        chk("S6b", "freeze 冻结", f1["out"].get("ok") and f1["out"].get("id") == cid)
        f2 = tool(c, "freeze_spec", {"corpus": "mine", "candidate_id": cid})
        chk("S6c", "★ 重复 freeze 不 force ⇒ 拒（不许静默换透镜）",
            f2["isError"] is True, str(f2["out"])[:60])

        q2 = tool(c, "query", {"corpus": "mine", "require": keys[:1]})
        chk("S7", "冻结后 query 用上声明的方向函数",
            q2["out"].get("query", {}).get("plugin") == "keyset"
            and (q2["out"].get("spec") or {}).get("id") == cid,
            f"spec={q2['out'].get('spec')}")

        dids = [n["did"] for n in (q2["out"].get("nodes") or [])]
        d = tool(c, "drill", {"corpus": "mine", "did": dids[0], "require": keys[:1]})
        chk("S8", "drill ⇒ 记录真的落了一条", d["out"].get("recorded") is True,
            f"{d['out'].get('record_note')}")

        u = tool(c, "usage_status", {"corpus": "mine"})
        chk("S9", "usage_status 报启用 + 记录数 1",
            u["out"].get("usage_enabled") is True and u["out"].get("记录数") == 1,
            f"记录 {u['out'].get('记录数')}｜缓冲 {u['out'].get('缓冲批次数')}")

        # S10 支序对拍：**同一状态**下比 —— 下钻之后两边都该按倾向排
        # ⚠️ 第一版拿「下钻前的 MCP 结果」对「下钻后的直接结果」⇒ 必然不同，
        #    但那是**使用记录在改次序**（产品该有的行为），不是中转时重排。
        #    要比「原样中转」，得让两边处在同一状态。
        sys.path.insert(0, str(HERE / "core"))
        os.environ["LDV_CORPUS"] = str(corpus)
        import entry as E

        app = E.App(None)                      # 重建：读回刚写的那条使用记录
        q3 = tool(c, "query", {"corpus": "mine", "require": keys[:1]})
        direct2 = app.query({"require": keys[:1]})
        chk("S10", "支序与内核直接跑的逐字相同（同状态：下钻后）",
            [n["did"] for n in (q3["out"].get("nodes") or [])]
            == [n["did"] for n in (direct2.get("nodes") or [])]
            and q3["out"].get("ordered_by") == direct2.get("ordered_by") == "usage",
            f"MCP {[n['did'] for n in (q3['out'].get('nodes') or [])][:4]} vs "
            f"直接 {[n['did'] for n in (direct2.get('nodes') or [])][:4]}｜"
            f"ordered_by {q3['out'].get('ordered_by')}/{direct2.get('ordered_by')}")

        # ── S11 调用日志：每次工具调用都落一行 ─────────────────────────
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

        # ── S12 出处摘要：三类分开数，且和 == n_answer ──────────────────
        # ⚠️ 第一版期望 filled_by == "模型"，实测内置语料的值是
        #    "模型(DeepSeek-V4.1-Flash)"（带模型名）⇒ 判据该判的是
        #    「一类、且那一类以『模型』开头」，不是判死字符串。
        prov = (q3["out"].get("provenance") or {})
        buckets = prov.get("buckets") or {}
        chk("S12", "★ 出处摘要：buckets 之和 == n_answer；内置语料全是模型填的",
            sum(buckets.values()) == q3["out"].get("n_answer")
            and bool(buckets) and all(k.startswith("模型") for k in buckets),
            f"buckets {buckets}｜n_answer {q3['out'].get('n_answer')}")

        # ── S13 演员分解：声明照记，usage_status 报占比 ─────────────────
        d2 = tool(c, "drill", {"corpus": "mine", "did": dids[1],
                               "require": keys[:1], "actor": "human"})
        u2 = tool(c, "usage_status", {"corpus": "mine"})
        split = u2["out"].get("actor_split") or {}
        actors = {r.get("actor") for r in rows} | {"human"}
        chk("S13", "★ 演员分解：agent 下钻算有用，但**谁钻的可分解**",
            split.get("记录数") == 2 and split.get("agent 声明的下钻") == 1
            and d2["out"].get("actor") == "human" and "human" in actors,
            f"split {split}｜drill2 actor={d2['out'].get('actor')}")
    finally:
        c.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'✓ 全过' if not FAIL else f'✗ 红 {FAIL} 条'}（S1–S13）")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
