---
id: "con-0005"
type: 概念
title: "损失聚合粒度：样本级与 token 级"
aliases: ["loss aggregation", "token-level loss", "sample-level loss"]
cues:
  - "我在看 loss 是怎么算的，不确定是对每个样本还是每个 token"
  - "长回答里的 token 是不是被稀释了"
scope: "适用于策略梯度类算法在实现时选择「先按样本平均再按 token 平均」还是「直接按 token 平均」。不适用于奖励模型训练与 SFT——那两处的聚合口径是另一回事，不能照搬这里的结论。"
source:
  ref: "rlhfbook.com/c/06-policy-gradients（Loss Aggregation Tradeoffs）；arXiv:2503.14476（DAPO）§3.3"
  kind: 教材
evidence_status: 未验证
relations: ["con-0001"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "教材把它单列为一节并标注「Why Does This Matter?」，DAPO 把它列为四项关键技术之一。两条来源指向同一个问题，但措辞不同——本节点只记「两种口径存在差异」这一事实。"
---

训练一个 batch 里有长短不一的响应。聚合方式有两种：

- **样本级**：先算每条响应的平均损失，再对样本求平均 → **每条响应权重相同**
- **token 级**：把所有 token 的损失直接求平均 → **长响应里的 token 权重更大**

**这不是等价变换。** 样本级下，长响应里单个 token 对总损失的贡献被不成比例地压低。

DAPO 把它列为四项关键技术之一，理由是它会影响两件事：
① 长样本里的推理模式学不进去；② 长样本里的乱码、重复等低质模式罚不动。
