---
id: "con-0003"
type: 概念
title: "参考模型与 KL 惩罚"
aliases: ["reference model", "KL penalty", "KL 正则", "参考策略"]
cues:
  - "我不知道那个一直不更新的模型是干什么用的"
  - "我在纠结 KL 系数设多少"
scope: "适用于 RLHF / RLVR 的 RL 阶段。不适用于纯 SFT 或 DPO——那些阶段没有在线采样，也就没有这一步的 KL 项；也不适用于显式关掉了 KL 的推理向 RL 改编版。"
source:
  ref: "rlhfbook.com/c/06-policy-gradients（The Role of Reinforcement Learning in RLHF；Double Regularization）"
  kind: 教材
evidence_status: 未验证
relations: ["con-0002"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "教材给了两种施加位置（损失层 vs 奖励层）与「推理向改编版常完全关闭 KL」这两条，都已写进正文。"
---

参考模型 = 策略进入 RL 之前的**冻结副本**，不更新。它只提供一件事：**当前策略离出发点有多远**。

**两种施加位置（这是常被混淆的地方）：**
- **损失层**：`L = L_policy_gradient + β · D_KL` —— GRPO 的规范实现这么做
- **奖励层**：`r = r_θ − β · D_KL` —— RLOO 与传统策略梯度的推导这么做

差别在于**信用分配的粒度**：PPO 在算优势之前就逐 token 减掉 KL，是 token 级的；
GRPO 保留序列级优势，再在损失里加一个逐 token 项。

**一个趋势**：随着 RLHF 转向推理与可验证奖励（RLVR），KL 惩罚的普遍性整体下降，
许多推理向的改编版把它**完全关掉**。
