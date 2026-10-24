"""三个 intake 工具共用的**字段清洗**。

⚠️ 为什么单独一个模块（2026-10-22 审计）：`_scalar` 原来在 `intake_cora` 与
   `intake_snap` 里**逐字相同**、在 `intake_openalex` 里只多一行 docstring ——
   **三份实现**。改一处漏两处 ⇒ 三个语料的字段清洗规则会**悄悄分叉**，
   而分叉了没有任何东西看得出来（都是「看起来对的字符串」）。
"""
from __future__ import annotations

from typing import Any


def scalar(text: Any, limit: int = 120) -> str:
    """压成**单行标量**：语料解析器只认单行，且值里的引号会破坏 front-matter。

    - 连续空白压成一个空格
    - 双引号换成单引号（否则 YAML 值被截断）
    - 去掉 NUL（写进 md 会让下游读成二进制）
    - 截到 `limit`
    """
    s = " ".join(str(text or "").split())
    s = s.replace('"', "'").replace("\x00", "")
    return s[:limit]
