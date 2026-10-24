"""长上下文 × 三种给法：真的会退化吗？代价差多少？

跑法：
    python probes/real_scale.py          # key 从环境变量或 ~/.workbuddy-ai/secrets/deepseek.key

## 为什么要有**对照**（否则绝对数没有意义）

上一轮 2400 tokens 以内「9 格全中」——但那只是**无插件侧**一个数，
没有可比的东西。所以这一轮三种给法、同一批笔记、同一个问题：

    WITHOUT-长上下文   把 N 条笔记**全塞进 prompt**，直接问
    WITH-窄查询         N 条进插件，用一个**唯一键**查（答案集 = 1 条）
    WITH-宽查询        N 条进插件，用宽键查 + limit=50（答案集大）

⇒ 三列一比，才看得出「拉长上下文」到底会不会输、输在哪。

## 规模（按 token 计，用 API 回传的 usage，不用估算）

    500 条 ≈ 6k tokens｜2000 条 ≈ 24k｜4000 条 ≈ 48k
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PY = sys.executable
SECRET = Path.home() / ".workbuddy-ai" / "secrets" / "deepseek.key"
API = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
NEEDLE_VAL = "7.77"
SIZES = (500, 2000, 4000)


def load_key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    return k or (SECRET.read_text(encoding="utf-8").strip()
                 if SECRET.is_file() else "")


def ask_model(key: str, system: str, user: str) -> tuple[str, dict]:
    body = json.dumps({"model": MODEL, "temperature": 0,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=180) as r:
        doc = json.loads(r.read().decode("utf-8"))
    return doc["choices"][0]["message"]["content"], (doc.get("usage") or {})


class MCP:
    def __init__(self, store: Path):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LDV_MCP_STORE"] = str(store)
        self.p = subprocess.Popen([PY, str(HERE / "server.py")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  env=env, cwd=str(HERE))
        self.n = 0
        self.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "scale", "version": "0"}})

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, args):
        r = self.call("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        txt = "".join(i.get("text", "") for i in (res.get("content") or []))
        try:
            return json.loads(txt) if txt else {}
        except ValueError:
            return {"_raw": txt}

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def nid_of(i: int) -> str:
    return "n" + str(i).zfill(5)


def make_notes(n: int):
    """N 条 filler + 1 条针（带唯一键 `type=特殊`，供窄查询用）。"""
    out = []
    for i in range(n):
        # ⚠️ 值必须**唯一且永不为 7.77**：第一版用 i%97.i%89 ⇒ 有的 filler
        #    也会生成 7.77 ⇒ 两个能回答同一问题的东西（同一个坑第三次）。
        out.append((nid_of(i),
                    f"filler {nid_of(i)}：指标 {i + 100}.00，"
                    f"batch={16 * (1 + i % 3)}"))
    return out


def main() -> int:
    key = load_key()
    if not key:
        print("⊘ 没有 key —— 跳过 ≠ 通过")
        return 0

    print("=" * 78)
    print(f"长上下文 × 三种给法｜{MODEL}｜针 {NEEDLE_VAL}｜规模 {list(SIZES)}")
    print("=" * 78)
    Q = "哪一条笔记的指标是 7.77？只回它的编号（形如 n01234）。"

    tmp = Path(tempfile.mkdtemp(prefix="ldv-scale-"))
    store = tmp / "store"
    try:
        for n in SIZES:
            notes = make_notes(n)
            # 针：唯一键 type=特殊，id 唯一
            needle_id = "nZZZZZ"
            # ⚠️ 针的正文里**必须带它自己的编号** —— 问题问的是编号，
            #   不给它，模型只能猜（第一版就是这样，58 tokens 全取回了仍答错）。
            needle_body = f"alpha 特殊条 {needle_id}：指标 {NEEDLE_VAL}，batch=64"
            nodes = store / "_data" / "s" / "nodes"
            nodes.mkdir(parents=True, exist_ok=True)
            for nid, body in notes:
                (nodes / f"{nid}.md").write_text(
                    f'---\nid: "{nid}"\ntype: 普通\nproject: filler\n---\n\n{body}\n',
                    encoding="utf-8")
            (nodes / "needle.md").write_text(
                f'---\nid: "{needle_id}"\ntype: 特殊\nproject: alpha\n---\n\n{needle_body}\n',
                encoding="utf-8")

            c = MCP(store)
            try:
                # ★ 先 derive project —— 否则 project=alpha 是未知键，
                #   窄查询会「假漏」（第一版就这样，取回 0 条）。
                rd = c.tool("ask", {"corpus": "s", "require": [], "structure": True,
                                     "limit": n + 10})
                pv = {nid: (it.get("fields") or {}).get("project")
                      for nid, it in zip(rd.get("answer_ids") or [],
                                         rd.get("items") or [])}
                c.tool("derive", {"corpus": "s", "name": "project",
                                   "values": {k: v for k, v in pv.items() if v},
                                   "rationale": "按项目看", "derived_from": "材料字段 project"})
                # WITHOUT：全塞 prompt
                all_txt = "\n".join(b for _i, b in notes)
                user_wo = f"笔记（共 {n + 1} 条）：\n{all_txt}\n\n{needle_body}\n\n问题：{Q}"
                try:
                    txt_wo, u_wo = ask_model(
                        key, "你只能依据上面的笔记回答；找不到就说「没有」。", user_wo)
                    hit_wo = needle_id in txt_wo or NEEDLE_VAL in txt_wo
                    p_wo = u_wo.get("prompt_tokens", 0)
                except (urllib.error.URLError, KeyError, TimeoutError) as e:
                    hit_wo, p_wo, txt_wo = None, 0, f"（失败 {type(e).__name__}）"

                # WITH-窄：唯一键 ⇒ 答案集 1 条
                r1 = c.tool("ask", {"corpus": "s", "require": ["type=特殊"]})
                items1 = r1.get("items") or []
                user_n = f"笔记：\n" + "\n".join(
                    it.get("text", "") for it in items1) + f"\n\n问题：{Q}"
                try:
                    txt_n, u_n = ask_model(
                        key, "你只能依据上面的笔记回答；找不到就说「没有」。", user_n)
                    hit_n = needle_id in txt_n or NEEDLE_VAL in txt_n
                    p_n = u_n.get("prompt_tokens", 0)
                except (urllib.error.URLError, KeyError, TimeoutError) as e:
                    hit_n, p_n, txt_n = None, 0, f"（失败 {type(e).__name__}）"

                # WITH-宽：宽键 + limit=50（答案集大 ⇒ 贵，且针可能不在前 50）
                # ★ 宽查询：type=普通 ⇒ 答案集 = 全部 N 条，针排在最后
                #   ⇒ limit=50 时**取不到它**；把 limit 放大才取到，但成本随之上来
                r2 = c.tool("ask", {"corpus": "s", "require": ["type=普通"],
                                    "limit": 50})
                items2 = r2.get("items") or []
                user_w = f"笔记：\n" + "\n".join(
                    it.get("text", "") for it in items2) + f"\n\n问题：{Q}"
                try:
                    txt_w, u_w = ask_model(
                        key, "你只能依据上面的笔记回答；找不到就说「没有」。", user_w)
                    hit_w = needle_id in txt_w or NEEDLE_VAL in txt_w
                    p_w = u_w.get("prompt_tokens", 0)
                except (urllib.error.URLError, KeyError, TimeoutError) as e:
                    hit_w, p_w, txt_w = None, 0, f"（失败 {type(e).__name__}）"
            finally:
                c.close()

            def mark(h):
                return "中" if h else ("**漏**" if h is False else "失败")

            print(f"\n── {n} 条笔记 ─────────────────────────────")
            print(f"   WITHOUT 全塞    {mark(hit_wo)}    prompt {p_wo:>6} tokens"
                  f"   {str(txt_wo)[:26]}")
            print(f"   WITH 窄查询     {mark(hit_n)}    prompt {p_n:>6} tokens"
                  f"   {str(txt_n)[:26]}")
            print(f"   WITH 宽 limit50 {mark(hit_w)}    prompt {p_w:>6} tokens"
                  f"（取回 {len(items2)} 条）   {str(txt_w)[:26]}")
            # 清掉，换下一档
            shutil.rmtree(nodes.parent.parent.parent.parent, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n说明")
    print("  · prompt tokens 是 **API 回传的真实用量**，不是估算")
    print("  · 窄查询（唯一键）答案集=1 ⇒ 最省")
    print("  · 宽查询那列**不是产品的漏**：查询覆盖不上时，agent 会去要方向/节点")
    print("    （derive / out_of_structure → can_agent_fill）。这里测的只是")
    print("    『limit 卡住时不许当成不存在』——现在 ask 会明说还有几条没给。")
    print("  · WITHOUT 在最大那档可能撞上下文上限 —— 那是真实的失败模式，如实记")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
