"""tools 共用的路径常量（原来四个工具各自算一遍）。"""
from pathlib import Path

#: 仓库根 —— `ldv/tools/_paths.py` 往上三层
REPO = Path(__file__).resolve().parent.parent.parent

#: 暂存目录名（两个 staging 工具共用）
STAGING = "_staging"
