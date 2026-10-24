---
id: "arg-0001"
type: 论据
title: "增量消融：四项技术各加一项，分数单调上升"
aliases: ["ablation", "消融链"]
cues:
  - "DAPO 凭什么说这四项都有用，而不是碰巧"
  - "我想看一个能区分「哪一项贡献多少」的实验"
scope: "这是 DAPO 论文自己的消融，任务是 AIME 2024（avg@32），基座是 Qwen2.5-32B。不构成对其它基座或其它任务的结论。"
source:
  ref: "arXiv:2503.14476（DAPO）§4 消融实验"
  kind: 论文
evidence_status: 未验证
relations: ["stance-0001"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "数字取自论文 HTML 版的消融小节，逐个抄录，未做换算。avg@32 是论文使用的口径，本节点不改口径。"
---

**这个论据的形状是"增量消融"**——从朴素 GRPO 出发，一次加一项，看分数怎么动。

| 配置 | AIME24 avg@32 |
|---|---|
| Naive GRPO | 30 |
| + Overlong Filtering | 36 |
| + Clip-Higher | 38 |
| + Soft Overlong Punishment | 41 |
| + Token-level Loss | 42 |
| **+ Dynamic Sampling（= DAPO）** | **50** |

**为什么它比"最终分数高"更有说服力**：每一步都只动一个变量，且**每一步都不掉分**。
如果某一项其实无用，单调上升的链条很难维持。

**但它证明的边界**：它证明的是"在**这个顺序、这个基座、这个任务**下每一项都有正贡献"，
**不证明**这个顺序是最优的、也不证明换个任务还成立。论据的适用范围必须跟结论一起读。
