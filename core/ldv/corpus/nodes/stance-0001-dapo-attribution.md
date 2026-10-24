---
id: "stance-0001"
type: 立场
title: "DAPO：长度与熵的问题出在损失聚合和裁剪上界"
aliases: ["DAPO 归因"]
cues:
  - "我想知道 DAPO 认为长度增长的根因在哪"
  - "DAPO 那四项技术分别打的是哪个问题"
scope: "这是 DAPO 论文对 GRPO 训练不稳定问题的归因与对策，只覆盖它自己报告的设置（Qwen2.5-32B base、AIME 2024）。不覆盖其它规模或其它任务的复现结论。"
source:
  ref: "arXiv:2503.14476（DAPO）§3.1–§3.4；arXiv:2503.14476v2 HTML 版四项技术小节"
  kind: 论文
evidence_status: 未验证
relations: ["issue-0001"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "四项技术的名称与「各自解决什么问题」是从论文 HTML 版逐条提取的，不是凭记忆写的。消融数字另建 case-0001。"
---

**主张**：GRPO 的规模上不去，是因为四个具体机制没处理，而不是因为优势公式本身有问题。
论文称这四项为「使大规模 LLM RL 成功的关键技术」，并开源了训练代码（基于 verl）。

**四项技术各自打的问题：**

| 技术 | 针对的问题 |
|---|---|
| **Clip-Higher** | 熵坍缩：策略熵快速下降、组内采样趋于相同。解耦裁剪上下界，**只放大上界**，因为上界会限制低概率（探索）token 的抬升 |
| **Dynamic Sampling** | 全对/全错组优势为 0、产生零梯度，导致有效提示数减少、梯度方差增大 |
| **Token-Level Policy Gradient Loss** | GRPO 原用样本级损失，长响应的 token 贡献被不成比例压低 → 长样本里的推理模式学不到、乱码重复罚不动 |
| **Overlong Reward Shaping** | 对截断样本统一惩罚引入的奖励噪声；做法是 Overlong Filtering + Soft Overlong Punishment |

**注意**：四项里没有一项是"改优势公式的归一化"。这是它与 Dr. GRPO 的分歧点。
