---
id: "exc-0002"
type: 例外
title: "换张卡就好了：精度问题的触发条件"
aliases: ["非 Hopper GPU", "vLLM issue 22103", "disable_cascade_attn", "A100 精度问题"]
cues:
  - "同样的配置别人跑不崩我跑就崩，是不是卡的问题"
  - "我的 grad_norm 在涨，但换了个 GPU 型号就没事"
  - "我要不要为这个精度问题改我的训练脚本"
scope: "适用于用 vLLM 做 rollout、且训推精度不一致的场景。**不适用于 Hopper 架构 GPU**——A100 / L20 / B200 等非 Hopper 卡才容易触发。也**不适用于 vLLM 已发布修复的版本**：官方文档说 bug 已修，但 v0.10.2 尚未包含。"
source:
  ref: "verl 官方文档 FAQ「Missmatch between inference and training sequence」（文档版 2025-09-24；其中引 vLLM issue 22103 与 flash-attention PR 87）"
  kind: 官方文档
evidence_status: 未验证
relations: ["judge-0006"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "⚠️ 这是一条**会过期的例外**——vLLM 发布含修复的版本后它就失效。收它的理由：它决定了 judge-0006 的判据**在什么硬件上成立**，属于判据的一部分，不是配置项。scope 里已写清版本边界。"
---

**例外是什么**：`judge-0006` 的判据（`rollout_probs_diff_mean` 高于 0.01 即精度问题）
**不是在任何硬件上都同等容易触发**。官方文档列了三条**同时满足**才会撞上的条件：

1. 非 Hopper 架构 GPU——点名 **A100、L20、B200**
2. 推理引擎是 vLLM，且命中 issue 22103
3. 输入输出文本长（多轮场景、Qwen3 这类推理模型）

**为什么这算例外而不是通则**：Hopper 卡上同样配置不会撞上。
也就是说**同一条判据在不同硬件上成立程度不同**——
报出「你的 grad_norm 在涨」之前，先看这三条。

**根因**：vLLM 使用的 flash attention 有 bug。官方文档说**已修，
但修复尚未在 vLLM v0.10.2 发布**。修复在 flash-attention 的 PR 87。

**这条的保质期**：vLLM 发布含该修复的版本后，本节点失效。

**写在 `notes` 里给后来人的话**：如果有一天你发现这条已经过期，
那不是它写错了，是环境变了——**这正是本产品不收录配置项、却收这条的理由**：
它约束的是判据的**适用范围**，而不是一个旋钮该拧到几。
