---
id: "judge-0006"
type: 判断点
title: "grad_norm 一直往上走，是算法问题还是引擎问题"
aliases: ["grad_norm 排查", "rollout_probs_diff_mean", "训推不匹配判据"]
cues:
  - "actor/grad_norm 从训练一开始就单调往上爬，调学习率也没用"
  - "我想知道训练崩之前有没有一个能提前看到的指标"
  - "同样的配置别人跑不崩我跑就崩，不知道该先查哪里"
scope: "适用于 rollout 与训练用不同引擎（或同引擎但 kernel 配置不同）的 RL 训练。不适用于单引擎同 kernel 的简化设置——那里这个偏差不存在，grad_norm 上升要另找原因。也不适用于把 grad_norm 当根因：它是**症状**，根因在引擎的数值不一致上。"
source:
  ref: "verl 官方文档 FAQ「Missmatch between inference and training sequence (high actor/grad_norm)」（文档版 2025-09-24）；arXiv:2605.14220（HTML 版 §2 表 1、§3.2、§4.1）"
  kind: 官方文档
evidence_status: 未验证
relations: ["con-0007"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "⚠️ 判据数值（0.005 / 0.01）来自 verl 官方文档的**单一版本**，是经验线不是定理，框架升级后要复核。2605.14220 给了机制与崩溃过程，但**没有**给这个阈值——阈值只在官方 FAQ 里。"
---

**现象**：`actor/grad_norm` 在训练中持续上升。

变量：grad_norm 的走向、rollout_probs_diff_mean 的读数、KL 估计量、GPU 架构

**判据**——这条最有价值的地方是它给了数值：

- 开 `actor_rollout_ref.rollout.calculate_log_probs=True`，日志里会多出 `training/rollout_probs_diff_mean`
- **正常应低于 0.005；高于 0.01 就是推理引擎侧的精度问题**

**触发条件**（官方文档列了三条，同时满足才容易撞上）：

1. 非 Hopper 架构 GPU——点名 **A100、L20、B200**
2. 推理引擎是 vLLM，且命中 issue 22103
3. 输入输出文本长（多轮场景、Qwen3 这类推理模型）

**修法**：加 `+actor_rollout_ref.rollout.engine_kwargs.vllm.disable_cascade_attn=True`

**根因**：vLLM 使用的 flash attention 有 bug。文档说已修，但**尚未在 vLLM v0.10.2 发布**。
→ 所以这属于**版本相关的临时例外**，见 `exc-0002`。

**为什么别只看 KL**：2605.14220 测到——在 recomputation 模式下，**前 700 步已经出现失败征兆，
KL 估计量却仍贴着零不匹配基线**。所以「KL 没涨」不等于安全。
更早暴露它的是**优势加权损失贡献的偏斜**，而那个量不是现成指标，得自己算。

**崩溃长什么样**（同论文的 GRPO 实验，以零不匹配引擎作参照）：

| 配置 | 训练 reward 走向 |
|---|---|
| 零不匹配（参照） | 稳在约 0.93 |
| vLLM recomputation | 前 650 步从约 0.87 退到约 0.40，部分恢复后约 step 1610 再次快降，**约 step 1665 后崩到接近零** |
| vLLM bypass | 单阶段退化到约 0.4，**但不崩到零**，也没有同等量级的 loss 尖峰 |

→ **两种失败形态不一样**：一种崩到零，一种只是静默退化。所以「没崩」不等于「没坏」。

## 怎么确认

1. 开 `actor_rollout_ref.rollout.calculate_log_probs=True`，日志里会多出 `training/rollout_probs_diff_mean`
2. 读这个数，对照上面的**判据线**（两个数值在上面，这里不重复）
3. 逐条核对**触发条件**（GPU 架构 / 推理引擎 / 输入长度）——三条同时满足才容易撞上
4. **别只看 KL**——KL 没涨不等于安全

**第 4 步有实测依据**：2605.14220 测到，在 recomputation 模式下
**前 700 步已经出现失败征兆，而 KL 估计量仍贴着零不匹配基线**。
更早暴露它的是**优势加权损失贡献的偏斜**——而那个量**不是现成指标，得自己算**。

⚠️ 第 2 步的判据线来自 verl 官方文档的**单一版本**，是经验线不是定理。
**框架升级后要复核。**
