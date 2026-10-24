---
id: "issue-0002"
type: 议题
title: "GRPO 是否真的不需要 critic"
aliases: ["critic", "value function", "价值函数必要性"]
cues:
  - "我在选 PPO 还是 GRPO，想知道省掉 critic 有没有代价"
  - "GRPO 和 PPO 我该选哪个"
  - "选 GRPO 还是 PPO，区别到底在哪"
  - "GRPO 不用价值网络，是不是意味着它的优势估计更差"
scope: "限于「优势估计是否需要学习出的价值函数」这个问题。不涉及工程侧的内存/吞吐比较——那是可测的，不是议题。"
source:
  ref: "rlhfbook.com/c/06-policy-gradients（Value Functions and PPO；GRPO 小节的两大好处）"
  kind: 教材
evidence_status: 有争议
relations: ["con-0002"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "⚠️ 本节点只记录教材呈现的两侧论据。我没有找到一篇把「有无 critic」做控制变量对照的可查实验，所以不编造结论——如果后续找到，应新建论据节点挂在这里。"
---

**争的表面是"要不要 critic"，实质是"优势估计的偏差与方差怎么换"。**

**支持省掉的一侧（GRPO 的原始动机）**：
① 避免从语言模型主干学价值函数这个难题；② 省掉整份模型副本的内存。
教材把这两条并列为 GRPO 的两大好处。

**需要付代价的一侧**：PPO 的价值函数是**学出来的基线**，配 GAE 使用；
GRPO 用组内均值当基线，基线质量取决于**同组采样的数量与分布**。
教材指出 GRPO **常以远高于其它算法的每 prompt 样本数运行**，原因是
「优势完全取决于完成与其同 prompt 同伴的相对价值」。

→ **这句话本身就是议题的一半**：省掉 critic 省下的，可能要用**更大的组**换回来。
