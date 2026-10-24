"""插件：按方向换的那一侧。内核只认 §I1–§I7 七个方法。"""

#: 三个方向函数的名字 —— **只在这里定义一次**（原来 `cli` 与 `recognize` 各一份）。
#: ⚠️ 加插件时改这一处，`cli` / `recognize` / `retriever` 都跟着走。
DIRECTIONS = ("keyset", "reach", "sequence")
