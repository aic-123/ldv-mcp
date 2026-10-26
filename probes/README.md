# probes —— 可复跑的实验，不是宣称

十一个探针，每个都**真起 MCP 会话**跑一遍，读数逐字留在 `out/`。
（`subjectivity_long.py` 要外部语料 `snap-hepth`，语料不在就跳过 —— 跳过 ≠ 通过。）

> ⚠️ **三类读数不许混着说**（2026-10-26 用户定）：
> · **调用面**（`agent_task` / `abstention`）—— 测「ldv 能不能支撑 agent 把工作推进下去」。
>   **不是准确性**：里面的「10 个包 / 36 个许可 / 33%」是**载体**，不是成绩单。
> · **读数与成本**（`token_cost` / `repeat_cost` / `cache_hit_rate` / `eval_rigor`）—— 可以当数字报。
> · **契约**（`smoke_test.py` 的 S 系列）—— 守的是「说了就得做到」。
> 把调用面的数字当准确性报，等于把「能开工」说成「干得好」。

| 探针 | 回答什么问题 | 依赖 |
|---|---|---|
| `demo.py` | 三个场景的走查（内置 36 项） | 无（clone 即跑） |
| `plugin_gap.py` | 有插件 vs 没插件的回答差在哪 | 无 |
| `subjectivity.py` | 那个差异能不能表示**主体性**（短流程） | 无 |
| `subjectivity_long.py` | 放大到 1200 项 / 15 轮还成不成立 | `snap-hepth` 语料 |
| `long_scenario.py` | 空库起 14 天：答案偏移、缺口、压缩 | 无（自造笔记） |
| `token_cost.py` | 每问 / 每条答案的 token 成本 | **tiktoken** |
| `repeat_cost.py` | 重复任务 / 历史检索的成本形状 | **tiktoken** |
| `eval_rigor.py` | 位置扫描 / 多针 / 重复取均值（对齐 RAGAS 检索侧） | **tiktoken** |
| `metamorphic.py` | 八条蜕变关系（加/删/改/换序后答案该怎么变） | 无 |
| `real_usage.py` | **真实轨迹**（F01–F14）+ **边缘矩阵**（E01–E26）+ 三轮确定性（D01） | 无 |
| `cache_hit_rate.py` | **缓冲命中率三口径**（答案缓存 / 曝光 / 漏检）+ 键等价性 + 与 `orient` 对账 | 无 |
| `agent_task.py` | **ldv 能不能当指挥**：脚本化 agent 只靠返回把一件真工作做完（认结构 → 撞失配 → 造维度 → 再查 → 写回 → 报回执），10 个决策点每个都挂得住返回字段 | `repo/_data/pypi-deps` |
| `abstention.py` | **弃答专项**：30 道无支撑 + 30 道孪生（配对）。分三层 —— L1 维度不存在 / L2 取值不存在 / **L3 项在有内容无**（这一层 ldv 挡不住） | `repo/_data/pypi-deps` |
| `mcp_conformance.py` | **协议合规**（M1–M8）：用**官方 MCP SDK** 真握手 / tools-list 解析 / 调用与错误 | 官方 SDK（`pip install mcp`） |
| `real_model.py` | **真模型**：位置扫描 / 长度扫描 / 编造率 | **DeepSeek key**（仓库外） |
| `real_scale.py` | **长上下文 × 三种给法**（全塞 / 窄查询 / 宽 limit）对照 | **DeepSeek key**（仓库外） |

结论与读数见：

- [`../docs/有插件和没插件的差距.md`](../docs/有插件和没插件的差距.md)
- [`../docs/主体性判据.md`](../docs/主体性判据.md)
- [`../docs/长场景实测.md`](../docs/长场景实测.md)（含 token 成本与重复/历史检索）

## 怎么跑

```bash
python probes/demo.py                  # 内置语料，零依赖
python probes/plugin_gap.py
python probes/subjectivity.py
python probes/long_scenario.py

# 要 tiktoken（真 tokenizer，不是估算）
python -m pip install tiktoken
python probes/token_cost.py
python probes/repeat_cost.py

# 要 DeepSeek key（**仓库外**：~/.workbuddy-ai/secrets/deepseek.key，或 DEEPSEEK_API_KEY）
python probes/real_model.py

# 要外部语料（不进仓库）
LDV_DATA=/path/to/_data python probes/subjectivity_long.py
```

- 没装 `tiktoken` ⇒ `token_cost.py` / `repeat_cost.py` 直接 ImportError
  （**不退化**：token 数换成估算就没有比较意义了）
- 语料不在 ⇒ `subjectivity_long.py` 打印「⊘ 语料不在」并以 0 退出（**跳过 ≠ 通过**）

## ⚠️ 三条纪律

1. **`out/` 里的输出是逐字留档**。改了探针就重跑、覆盖、在提交信息里说明改了什么 ——
   不许手改输出。
2. **WITHOUT 侧的答案是实验记录**（`plugin_gap.py` 里的 `WITHOUT_ANSWERS`、
   `long_scenario.py` 的压缩策略）—— 改它就是改实验。要换问题就换一批、重新逐字记录。
3. **成本类读数必须同时报「答案项数」**。踩过：窄查询返回 0 项时，错误回包的
   171 tokens 差点被读成「插件省 0.42×」—— **「没答到」会被当成「很便宜」**。

## 怎么验「留档 == 现在跑出来的」

```bash
python probes/real_usage.py > /tmp/x.txt 2>&1
diff --strip-trailing-cr probes/out/real_usage.out.txt /tmp/x.txt   # 无输出 = 一致
```

⚠️ **必须带 `--strip-trailing-cr`**（2026-10-26 实测踩到）：
仓库里 `.gitattributes` 是 `* text=auto eol=lf` ⇒ **git 里存的是 LF**，
而在 Windows 上重定向输出产生的是 **CRLF**。
⇒ 直接 `diff` 会**每一行都报不同**，但那只是行尾，不是内容。

实测（新 clone）：直接 `diff` 报「Files differ」，加 `--strip-trailing-cr` 后**逐字相同**。
**所以「逐字一致」的准确说法是「除行尾外逐字一致」** —— 别把它写成「字节级相同」。
