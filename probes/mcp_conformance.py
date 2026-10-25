# -*- coding: utf-8 -*-
"""用**官方 MCP Python SDK** 真连 ldv-mcp —— 这才是「是不是可用 MCP 格式」的证据。

自写的 smoke 只验「我发什么它回什么」；SDK 会验：
  · 握手字段是否合规（protocolVersion / capabilities / serverInfo）
  · `notifications/initialized` 的时序
  · `tools/list` 的每个 tool 能否被 Pydantic 模型解析（name/description/inputSchema）
  · `tools/call` 的 content / isError 是否合规
  · 未知工具、非法参数时是否按协议报错、连接是否还活着

跑法（需要官方 SDK）：
    <venv>/python -m pip install mcp
    <venv>/python probes/mcp_conformance.py
"""
import asyncio
import os
import sys
import tempfile
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent          # probes/
ROOT = HERE.parent                              # ldv-mcp/（server.py 在这）
PY = sys.executable
OK = 0
BAD = 0


def _err(r) -> bool:
    """SDK 的  用 snake_case（），线上是 camelCase。"""
    return bool(getattr(r, "isError", None) or getattr(r, "is_error", None))


def chk(tag: str, what: str, good: bool, detail: str = "") -> None:
    global OK, BAD
    if good:
        OK += 1
    else:
        BAD += 1
    print(f"  [{'过' if good else '红'}] {tag} {what}"
          + (f"\n        {detail}" if detail else ""))


async def run() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    tmp = Path(tempfile.mkdtemp(prefix="ldv-sdk-"))
    store = tmp / "store"
    nodes = store / "_data" / "mine" / "nodes"
    nodes.mkdir(parents=True)
    (nodes / "a.md").write_text(
        '---\nid: "a"\ntype: 事实\n---\n\n材料一。\n', encoding="utf-8", newline="\n")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["LDV_MCP_STORE"] = str(store)
    params = StdioServerParameters(command=PY, args=[str(ROOT / "server.py")],
                                   env=env, cwd=str(ROOT))
    errlog_path = tmp / "server.err.txt"
    errlog = open(errlog_path, "w", encoding="utf-8")     # noqa: SIM115

    try:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as s:
                # ① 真实握手（SDK 会校验返回结构）
                # ⚠️ SDK 的模型字段是 snake_case（`protocol_version` / `server_info`），
                #    与线上的 camelCase 不同 —— 直接点 `protocolVersion` 会 AttributeError。
                init = await s.initialize()
                ver = getattr(init, "protocolVersion", None) or getattr(
                    init, "protocol_version", None)
                info = getattr(init, "serverInfo", None) or getattr(
                    init, "server_info", None)
                chk("M1", "官方 SDK 握手成功，协议字段合规",
                    bool(ver) and info is not None,
                    f"protocolVersion={ver}"
                    f"｜server={getattr(info, 'name', '?')} "
                    f"{getattr(info, 'version', '?')}"
                    f"｜capabilities={getattr(init, 'capabilities', '?')}")

                # ② tools/list：每个 tool 都要能被 SDK 解析
                tl = await s.list_tools()
                names = [t.name for t in tl.tools]
                # ⚠️ 工具面是 **8 个**（四件动作 + 四件能力），不是 12 —— 我第一版记错了
                chk("M2", f"tools/list 返回 {len(names)} 个工具，全部可解析",
                    len(names) == 8 and len(set(names)) == len(names),
                    "｜".join(names))

                # ③ inputSchema 是否合法（required ⊆ properties、type=object）
                bad_schema = []
                for t in tl.tools:
                    # ⚠️ 同样 snake_case（）
                    sch = (getattr(t, "inputSchema", None)
                           or getattr(t, "input_schema", None) or {})
                    props = sch.get("properties") or {}
                    req = sch.get("required") or []
                    miss = [r for r in req if r not in props]
                    if miss or sch.get("type") != "object":
                        bad_schema.append((t.name, miss or sch.get("type")))
                chk("M3", "每个 inputSchema 都是合法 JSON Schema（required ⊆ properties）",
                    not bad_schema,
                    f"不合法的：{bad_schema}" if bad_schema else f"{len(names)} 个全部合法")

                # ④ 正常调用
                r = await s.call_tool("orient", {"corpus": "mine"})
                txt = "".join(getattr(c, "text", "") for c in r.content)
                chk("M4", "tools/call 正常返回（content 里是 JSON 文本）",
                    (not _err(r)) and '"n_items"' in txt,
                    f"isError={_err(r)}｜前 60 字={txt[:60]}")

                # ⑤ 未知工具 ⇒ 明确报错（不能静默成功）
                try:
                    r2 = await s.call_tool("没有这个工具", {})
                    chk("M5", "未知工具 ⇒ 明确报错（不静默成功）",
                        _err(r2), f"isError={_err(r2)}")
                except Exception as e:                       # noqa: BLE001
                    chk("M5", "未知工具 ⇒ 明确报错（SDK 层异常也算）",
                        True, f"{type(e).__name__}: {str(e)[:60]}")

                # ⑥ 参数导致业务错 ⇒ isError + 说明，连接不崩
                r3 = await s.call_tool("ask", {"corpus": "mine",
                                               "require": ["不存在的键=x"]})
                t3 = "".join(getattr(c, "text", "") for c in r3.content)
                chk("M6", "参数导致业务错 ⇒ isError + 说明，连接不崩",
                    _err(r3) and ("结构" in t3 or "键" in t3),
                    f"isError={_err(r3)}｜{t3[:60]}")

                # ⑦ 连接仍然可用（⑥ 之后还能正常调）
                r4 = await s.call_tool("ask", {"corpus": "mine", "require": []})
                chk("M7", "出错之后连接仍可用（同一会话继续调）",
                    not _err(r4), f"isError={_err(r4)}")

                # ⑧ 不返回 structuredContent（无 outputSchema 时返回它是不合规的）
                has_sc = getattr(r, "structuredContent", None)
                chk("M8", "不返回 structuredContent（无 outputSchema 时返回它反而违规）",
                    has_sc is None,
                    f"structuredContent={'有' if has_sc else '无'}"
                    f"｜带 outputSchema 的 tool："
                    f"{sum(1 for t in tl.tools if getattr(t, 'outputSchema', None))} 个")
    except BaseException as e:                               # noqa: BLE001
        # ⚠️ anyio 的 TaskGroup 会把内层异常包成 ExceptionGroup —— 只看最外层
        #    永远只看到「unhandled errors in a TaskGroup」，得往下展开两层。
        msgs: list[str] = []

        def _unroll(x, depth=0):
            if depth > 3:
                return
            subs = getattr(x, "exceptions", None)
            if subs:
                for s in subs:
                    _unroll(s, depth + 1)
            else:
                msgs.append(f"{type(x).__name__}: {str(x)[:300]}")

        _unroll(e)
        chk("M0", "SDK 全流程未抛异常", False, " ｜ ".join(msgs[:4]))
    finally:
        errlog.close()
        err = errlog_path.read_text(encoding="utf-8", errors="replace").strip()
        if err:
            print(f"\n  server stderr（最后 800 字）：\n{err[-800:]}")
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'✓ 全过' if not BAD else f'✗ 红 {BAD} 条'}（M1–M8）")
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
