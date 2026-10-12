#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""雾白纪 发行入口（明文启动器）。

整个发行包里唯一以明文形态存在的第一方 .py（~15 行）：真实入口
main.py 以 PyArmor bcc 机器码件随包（数据文件），由本启动器 import
触发 armor runtime 装载。除启动外不得在本文件添加任何逻辑。

freeze_support 必须最先执行：PyInstaller spawn 子进程重跑本入口时，
在这里被拦截为 worker 入口——main 以普通模块被导入时其
`if __name__ == "__main__"` 块不会执行，拦截点只能在这里。
"""
from __future__ import annotations

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    import main

    main.main()
