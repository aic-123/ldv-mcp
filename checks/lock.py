"""store 的**跨进程写锁** —— 只在写的时候拿，拿不到就明确报错。

## 为什么需要（2026-10-24 能力对账里唯一标红的那条）

    MCP 服务器**单进程内是串行的**（`main()` 一行一行读 stdin）⇒ 同一会话不争。
    但**两个宿主会话可以同时指向同一个 store** ⇒ 那时
    `remember` / `derive` / `freeze` 的「先查再写」会互相踩：
    两边都看到「没有」，两边都写，后写的赢，**没有任何信号**。

## 这不是数据库事务，别当它用

    · 拿不到锁 ⇒ **报错**（`StoreBusy`），不无限排队 —— 默认等 5 秒
    · **陈旧锁**（进程崩了留下的）按 mtime 超过 120 秒视为可抢，并**记一笔**
      （`_stale.txt` 追加一行）—— 抢锁这件事不许静默
    · 锁只护**写**；读（`ask` / `orient`）不拿锁 —— 写是原子的（`os.replace`），
      读到的要么是旧的要么是新的，不会是半份

## 与内核「单写者约定」的关系

    内核那条约定说的是**落盘文件**（`persist`）：同一份存档只许一个写者。
    这里是**产品 store** 的写者协调 —— 两者正交，不要互相引用当证明。
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

#: 拿不到锁等多久（秒）—— 短：这是本机写，不是分布式队列
WAIT = 5.0
#: 陈旧锁判定：mtime 超过这个秒数 ⇒ 视为进程已死，可抢
STALE_AFTER = 120.0


class StoreBusy(RuntimeError):
    """store 正被另一个进程写 —— 明确报错，不静默排队也不静默跳过。"""


@contextlib.contextmanager
def store_lock(store: str | Path, *, wait: float = WAIT):
    """独占一个 store 的写权。拿不到 ⇒ `StoreBusy`。"""
    p = Path(store) / ".lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + wait
    while True:
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()} {time.time():.3f}\n".encode("ascii"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - p.stat().st_mtime
            except OSError:
                age = 0.0
            if age > STALE_AFTER:
                # 陈旧锁：抢过来，**记一笔**（抢锁不许静默）
                try:
                    p.unlink()
                    with open(p.parent / "_stale.txt", "a", encoding="utf-8") as f:
                        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} "
                                f"抢走陈旧锁（age {age:.0f}s）\n")
                except OSError:
                    pass
                continue
            if time.time() > end:
                holder = ""
                try:
                    holder = p.read_text(encoding="ascii", errors="replace").strip()
                except OSError:
                    pass
                raise StoreBusy(
                    f"store 正被另一个进程写（{p}，持有者 {holder or '未知'}）。"
                    "这不是错误配置 —— 两个会话同时指向同一个 store 时会这样。"
                    "稍后重试；若确认没有别的会话在跑，删掉这个 .lock 文件")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            p.unlink()
        except OSError:
            pass


def create_exclusive(path: str | Path, data: str) -> bool:
    """**原子地**创建文件：已存在 ⇒ 返回 False（不覆盖、不 TOCTOU）。

    ⚠️ 「先 `exists()` 再写」在两个进程之间是假检查 —— 两边都能看到「没有」。
       这里用 `O_CREAT|O_EXCL`：内核保证只有一个创建者成功。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)
    return True
