---
id: "con-0002"
type: 概念
title: "GRPO 相比 PPO 省掉的那一份模型"
aliases: ["value network", "critic", "价值网络", "critic model"]
cues:
  - "显存不够，想知道 GRPO 到底比 PPO 省了哪一块"
  - "有人问我 GRPO 为什么不需要 critic"
scope: "适用于按标准实现跑的 PPO / GRPO。不适用于你自己给 PPO 加了其它省内存手段、或给 GRPO 加回了价值头的改造版本——那就不在标准实现的比较范围内了。"
source:
  ref: "rlhfbook.com/c/06-policy-gradients（GRPO 小节明确列出两大好处）"
  kind: 教材
evidence_status: 未验证
relations: ["con-0001"]
filled_by: 模型(DeepSeek-V4.1-Flash)
notes: "教材只给了「省内存」和「避免学价值函数」两条好处，没有给具体省了多少显存的比例数字——本节点也不编。"
---

PPO 在 RL 阶段同时驻留**三份**模型：当前策略、参考策略、价值网络（critic）。
GRPO 去掉第三份，只留前两份。

省下来的不是"一点点显存"，而是**整份主干模型的副本**——因为价值网络通常是主干规模，
不是小头。这也是教材把"省内存"和"避免学价值函数"并列为两大好处的原因：
后者不只是工程问题，学价值函数本身就是 RLHF 里的难点。
