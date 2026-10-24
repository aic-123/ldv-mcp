---
id: "case-0002"
type: 案例
title: "Dr. GRPO：7B 基座做到 AIME 2024 的 43.3%"
aliases: ["Dr. GRPO 实验", "Oat-Zero-7B", "minimalist R1-Zero recipe"]
cues:
  - "我想用很小的模型复现 R1-Zero，有可查的例子吗"
  - "有人说去掉优势公式里的标准差项就够了，有实验吗"
  - "只用少量算力能跑到什么程度"
scope: "情境特征：Qwen2.5-Math-7B base 基座、MATH level 3-5 题目、Qwen-Math 模板、AIME24 / AMC / MATH500 / Minerva / OlympiadBench 五个基准、8×A100 约 27 小时。不适用于更大规模、不适用于非推理任务、不适用于 Qwen2.5 之外的基座（论文自己说基座差异很大）。"
source:
  ref: "arXiv:2503.20783（Dr. GRPO）§1、§2.1–§2.3、§3.3–§3.4、附录 B Table 4（HTML 版正文）；代码 github.com/sail-sg/understand-r1-zero"
  kind: 论文
evidence_status: 未验证
relations: ["stance-0002", "arg-0002"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "摘要原话是「establishes a new state-of-the-art」——那是作者在 2025-03 的自述。⚠️ 读正文后要加一句限定：**43.3% 这个数字是 AIME24 单列最高，但五个基准平均（51.4）不是最高**。本节点照实标出，不替它背书「整体 SOTA」。"
---

**情境特征**：想验证"R1-Zero 式的纯 RL 到底靠什么起作用"。作者没有直接接受
"RL 让模型涌现出推理"这个说法，而是**把基座和算法分开检验**。

**考虑过的选项**：两条线并行——
① 换基座（Qwen2.5-Math、Qwen2.5、Llama-3.1、DeepSeek-Math、DeepSeek-V3-Base），
看预训练特征如何影响 RL 表现；② 查算法本身，看 GRPO 的优势估计有没有问题。

**理由**：如果"涌现"一部分来自基座而非 RL，那么把功劳全记在 RL 上就是错的归因。

**结果——先给配方和数字**：

- **配方**：Qwen2.5-Math-7B + Dr. GRPO + **MATH level 3-5** + Qwen-Math 模板
- **成本**：**8 块 A100 约 27 小时**
- **AIME 2024：43.3%**（模型名 Oat-Zero-7B，出处是附录 B **Table 4**）
- **五基准平均：51.4**

**同表对照**（AIME24 列 / Avg 列）：R1-Distill-Qwen-7B @ 8k = 33.3 / 54.7；
SimpleRL-Zero-7B = 26.7 / 46.6；OpenReasoner-Zero-7B @ 8k = 13.3 / 45.9。

→ **⚠️ 一个必须自己看出来的限定**：43.3 是 **AIME24 单列最高**，
但 **Avg（51.4）低于 R1-Distill-Qwen-7B @ 8k 的 54.7**。
所以"新 SOTA"**只在 AIME24 这一列上成立**。论文的摘要没加这个限定，这里加上。

**算法侧的产出**：发现 GRPO 目标里有**两个**归一化偏差（不是摘要说的一个），
提出 Dr. GRPO 把两项都移除——见 `stance-0002`。

**基座侧的产出——这是本案例更值钱的部分**：

| 发现 | 数字 |
|---|---|
| Qwen2.5-Math **不加模板**最好 | 1.5B：4-shot 19.7 → **33.1**；7B：4-shot 23.8 → **38.2** |
| 加 R1 模板反而崩 | 1.5B **7.9**；7B **0.0** |
| 不用模板相对 4-shot | 约 **+60%** |
| DeepSeek-V3-Base 在 RL 前 | **已有自我反思**（"Aha"、"wait"） |
| 自我反思 vs 准确率 | **无正相关**（约一半带自省的响应未取得更高准确率） |
| 弱基座 + 领域继续预训练 | Llama-3.2-3B：原始 Avg **3.3** → RL 后 **6.8** → FineMath **14.8** → NuminaQA **20.7** |

论文据此的警告是：Qwen2.5 的基座**可能已经是"SFT-like"的**，
用它复现 R1-Zero 要**更谨慎**；并且应当**更保守地**声称"纯 RL 带来了巨大提升"。

**反事实**：如果只测"RL 之后分数涨了多少"而不换基座做对照，
就分不清涨的是 RL 的功劳还是基座本来就有——**这正是论文标题里 "Critical Perspective" 的含义**。

**这条案例的额外价值**：它示范了一种**诊断姿态**——先质疑归因，再动手调参。
而且它把**算力成本**（8×A100 / 27 小时）写出来了，
这在本产品收录的来源里很少见——多数论文只报分数不报成本。
