---
id: "stance-0003"
type: 立场
title: "GRPO 原文：省掉 critic 是为了同时解决两件事"
aliases: ["GRPO 原始动机"]
cues:
  - "GRPO 当初为什么要去掉 critic"
  - "我想知道 GRPO 的动机是省内存还是别的"
scope: "这是 DeepSeekMath 提出 GRPO 时给出的动机，只代表它自己的表述。不覆盖后续论文对「省掉 critic 的代价」的补充——那些见 issue-0002 的另一侧。"
source:
  ref: "arXiv:2402.03300（DeepSeekMath）§4.1.1、§1、摘要（HTML 版正文）；rlhfbook.com/c/06-policy-gradients"
  kind: 论文
evidence_status: 未验证
relations: ["issue-0002"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "⚠️ 论文对「省了多少」**只有定性表述**（significantly reducing training resources / substantial burden），**没有百分比或绝对数字**。本节点照实写，不替它估。摘要原话是 while concurrently——两个目标并列，不是主从关系，本节点保留这个并列。"
---

**主张**：GRPO 是 PPO 的一个变体，它同时做两件事——**提升数学推理能力**与**优化 PPO 的显存占用**。
摘要用 `while concurrently` 把这两件事**并列**，不是"为了省内存牺牲一点效果"。

## 论文给的两条具体理由（§4.1.1）

1. **value function 通常是与 policy 同规模的另一个模型**，带来
   **substantial memory and computational burden**。
2. **在 LLM 语境下，通常只有最后一个 token 被奖励模型打分**——
   这让「**在每个 token 上都准确**」的价值函数**很难训**。

→ 第二条比第一条更锋利：它说的不是"贵"，是**"这个位置本来就不好训"**。

## 替代方案与它的合理性论证

论文用**同一问题下多个采样输出的平均奖励**当基线，并给了一条常被忽略的论证：

> 奖励模型通常是在**同一问题的输出之间的比较数据**上训练的，
> 所以用**组内相对**的方式算优势，**与奖励模型本身的比较性质对齐**。

这句话的含义是：组内相对**不只是个便宜的近似**，它在结构上与奖励模型的训练方式同源。

## ⚠️ 边界

- **论文没有给内存/算力节省的具体数字**。只有 `substantial`、`significantly reducing training resources`
  这类定性词。唯一定量线索是「去掉了一个与 policy 同规模的模型」。
- **论文没有做严格隔离的 GRPO vs PPO 对照实验**。常被引的对比（如 Math-Shepherd-Mistral 7B
  用 PPO 得 MATH 33.0% vs DeepSeekMath-RL 7B 用 GRPO 得 51.7%）来自 Table 5 的**跨模型汇总**，
  基座、数据、训练细节都不同，**不能归因于算法差异**。论文中最接近算法消融的是
  §5.2.1 的 GRPO vs Online RFT。

**所以严格说，这个立场回答的不是"要不要 critic"，而是"critic 值不值得它的代价"。**
原文的答案是"不值得"，但它在 2024-02 给出时，面对的还不是后来那套长链式推理 + 规则奖励的场景。
