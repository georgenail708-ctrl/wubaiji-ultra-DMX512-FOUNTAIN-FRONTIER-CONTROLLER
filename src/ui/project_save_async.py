# -*- coding: utf-8 -*-
"""R139：把「保存工程」的**序列化与写盘**搬到后台线程。

用户实报（2026-09-21）::

    "还有一个问题，播放时间线的时候点击保存项目，会卡死。"

## 真因（实测，62MB 真工程 `E:\\水仙十字.dmxt`）

保存全程在 **UI 线程**跑：

| 阶段 | 实测 | 说明 |
|------|------|------|
| `_collect_project_state_for_save` | 0.24s | 各面板状态回写工程 |
| `_bundle_timeline_audio_for_save` | 0.25s | 音频打包（无音频时也走判空） |
| `Project.save`（旧 `indent=2`） | 1.23s | 序列化 + 写盘 |

合计 **≈1.7s 一次性阻塞**。播放中这段时间里 UI 侧（走带、监视器、各面板、
时间线重绘）**全部停摆** —— 用户看到的就是"卡死"。
（`gen_thread_active()` 虽为真，但 UI 停摆本身已足够造成可感的卡死。）

另实测：`json.dump(..., indent=2)` 比紧凑输出 **慢 2.75×**（0.99s vs 0.36s）
且体积 +43%（61MB vs 42.7MB）—— 工程文件本就非人读，美化纯亏。

## 修法（两步）

1. **紧凑 JSON**：`Project.save` 去掉 `indent=2`（内容逐字节等价，读回结果不变）。
2. **后台写盘**（本模块）：`to_dict()` 留在 UI 线程取（0.03s，且必须与面板/引擎
   实时状态一致），`json.dumps` + 42MB 写盘放 `ProjectSaver` 线程。
   保存完成的收尾（路径/脏标记/标题/状态栏）用信号回 UI 线程。

★ 为什么单独成模块：`MainWindow` 是上帝类（基线方法数 255 顶格），
  `store_handlers.py` 这类文件又受 500 行上限约束 —— 放这里两个都不碰。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

log = logging.getLogger(__name__)


class ProjectSaver(QThread):
    """后台线程：把已冻结的工程字典序列化并原子落盘。

    信号：
      saved(bool)   是否成功
      failed(str)   失败原因（中文，直接可展示）
    """

    saved = pyqtSignal(bool)
    failed = pyqtSignal(str)

    def __init__(self, payload: dict, path: str, parent=None):
        super().__init__(parent)
        self._payload = payload
        self._path = str(path)

    def run(self) -> None:                       # noqa: D102 - QThread 契约
        from ..core.models import Project
        try:
            # 复用 `Project.save` 的**原子写**实现（.tmp + os.replace），
            # 只把"写哪个 dict"换成 UI 线程已冻结的那份 —— 不新造第二套写盘逻辑。
            Project._write_dict_to_path(self._payload, self._path)
            self.saved.emit(True)
        except Exception as e:                    # noqa: BLE001 - 必须报回 UI
            log.exception("ProjectSaver 保存失败")
            self.failed.emit(str(e))
            self.saved.emit(False)


def save_project_async(win, path: str) -> None:
    """Freeze a save request and reuse the window's serial writer session."""
    from .project_save_session import save_session
    try:
        save_session(win).enqueue(path)
    except Exception as error:
        log.exception("保存快照失败")
        parent = win if isinstance(win, QObject) else None
        QMessageBox.critical(parent, "保存失败", f"工程序列化失败：{error}")
