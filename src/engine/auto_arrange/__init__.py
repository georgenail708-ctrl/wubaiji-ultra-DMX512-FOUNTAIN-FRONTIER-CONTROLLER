"""
auto_arrange — 自动编曲（一键托管）核心包

纯逻辑层，不依赖 PyQt / UI。被 deep_choreography 引擎与"自动编曲"面板共用。

模块：
  project_profile     — 从项目读出"画像"（设备类型/能力、每组几何、整体布局拓扑）
  experience_table    — 段落×设备×布局 → 专业参数/preset 的经验知识（从真实 .dmxt 提炼）
  arrangement_director— 编曲总指挥：画像+段落+音乐 → 每个子系统的角色(主奏/陪衬/留白) + 决策解释
"""
