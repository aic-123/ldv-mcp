# probes —— 可复跑的实验，不是宣称

三个探针，每个都**真起 MCP 会话**跑一遍，读数逐字留在 `out/`。

| 探针 | 回答什么问题 | 跑法 |
|---|---|---|
| `plugin_gap.py` | 有插件 vs 没插件的回答差在哪 | `python probes/plugin_gap.py` |
| `subjectivity.py` | 那个差异能不能表示**主体性**（短流程） | `python probes/subjectivity.py` |
| `subjectivity_long.py` | 放大到 **1200 项 / 15 轮 / 10 条查询**还成不成立 | `python probes/subjectivity_long.py` |

结论与读数见：

- [`../docs/有插件和没插件的差距.md`](../docs/有插件和没插件的差距.md)
- [`../docs/主体性判据.md`](../docs/主体性判据.md)

## 依赖

- `plugin_gap.py` / `subjectivity.py`：只用内置 36 项语料，**clone 下来直接能跑**
- `subjectivity_long.py`：要 `snap-hepth`（1200 项，**不进仓库**）。
  用 `LDV_DATA` 指向你的 `_data` 目录：
  ```bash
  LDV_DATA=/path/to/_data python probes/subjectivity_long.py
  ```
  语料不在 ⇒ 打印「⊘ 语料不在」并以 0 退出（**跳过 ≠ 通过**，报告里写明）

## ⚠️ 两条纪律

1. **`out/` 里的输出是逐字留档**。改了探针就重跑、覆盖、在提交信息里说明改了什么 ——
   不许手改输出。
2. **WITHOUT 侧的答案（`plugin_gap.py` 里的 `WITHOUT_ANSWERS`）是实验记录**，
   改它就是改实验。要换问题就换一批、重新逐字记录。
