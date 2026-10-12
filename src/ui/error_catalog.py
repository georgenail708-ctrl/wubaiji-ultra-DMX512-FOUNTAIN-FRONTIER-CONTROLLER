"""错误消息目录 — 预配置的常见错误消息及帮助链接。

提供标准化的错误消息，自动关联到故障排除文档。
"""
from typing import Optional
from src.ui.error_message import ErrorMessage, Severity


# 文档锚点常量
DOC__IMPORT = "#-import-failed"
DOC_PATCH_IMPORT = "#patch-import-failed"
DOC_MVR_EXPORT = "#mvr-export-failed"
DOC_EXCEL_MISSING = "#excel-missing"
DOC_RECORDING_NO_TRACKS = "#recording-no-tracks"
DOC_RECORDING_ENGINE = "#recording-engine-error"
DOC_TOPVIEW_NO_SELECTION = "#topview-no-selection"
DOC_TOPVIEW_INSUFFICIENT = "#topview-insufficient-selection"
DOC_XY_REFERENCE_NO_DEVICES = "#xy-reference-no-devices"
DOC_PROJECT_SAVE_FAILED = "#project-save-failed"
DOC_PROJECT_LOAD_FAILED = "#project-load-failed"
DOC_PROJECT_NO_DEVICES = "#project-no-devices"
DOC_TOPVIEW_MIN_PAGES = "#topview-minimum-pages"
DOC_TOPVIEW_MERGE_TYPES = "#topview-merge-insufficient-types"
DOC_TOPVIEW_NO_SHAPES = "#topview-no-shapes"
DOC_AUTO_GROUP_NO_CANDIDATES = "#auto-group-no-candidates"
DOC_NETWORK_PORT_CONFLICT = "#network-port-conflict"
DOC_FIREWALL_BLOCKING = "#firewall-blocking"
DOC_CROSS_DEVICE_OUTPUT = "#cross-device-output"
DOC_EFFECT_NO_DEVICES = "#effect-no-devices"
DOC_EFFECT_INCOMPATIBLE = "#effect-incompatible-type"
DOC_TIMELINE_CLIP_OVERLAP = "#timeline-clip-overlap"
DOC_TIMELINE_AUDIO_SYNC = "#timeline-audio-sync"
DOC_PERFORMANCE_LAG = "#performance-lag"
DOC_LICENSE_INVALID = "#license-invalid"
DOC_LICENSE_HARDWARE = "#license-hardware-change"


def _import_failed(detail: str) -> ErrorMessage:
    """工程导入失败（ERR-7001；文案真源在 src/core/error_codes.py）"""
    return ErrorMessage.from_code("ERR-7001", additional_context=detail)


def patch_import_failed(detail: str) -> ErrorMessage:
    """配接表导入失败（ERR-7002；文案真源在 src/core/error_codes.py）"""
    return ErrorMessage.from_code("ERR-7002", additional_context=detail)


def mvr_export_failed(reason: str) -> ErrorMessage:
    """MVR 导出失败（ERR-7004；文案真源在 src/core/error_codes.py）"""
    return ErrorMessage.from_code("ERR-7004", additional_context=reason)


def excel_support_missing() -> ErrorMessage:
    """缺少 Excel 支持库"""
    return ErrorMessage(
        title="缺少 Excel 支持",
        body="无法加载 openpyxl，不能读写 .xlsx 表格。",
        severity=Severity.ERROR,
        help_link=DOC_EXCEL_MISSING,
        recovery="请运行命令安装：venv\\Scripts\\python -m pip install openpyxl"
    )


def recording_no_tracks() -> ErrorMessage:
    """录制失败：无可录制轨道"""
    return ErrorMessage(
        title="录制失败",
        body="当前项目中没有可录制的普通轨道。",
        severity=Severity.WARNING,
        help_link=DOC_RECORDING_NO_TRACKS,
        recovery="请先添加普通轨道（非音频参考轨道）。"
    )


def recording_engine_error(detail: str) -> ErrorMessage:
    """录制引擎初始化失败"""
    return ErrorMessage(
        title="录制失败",
        body=f"录制引擎初始化失败：{detail}",
        severity=Severity.ERROR,
        help_link=DOC_RECORDING_ENGINE,
        recovery="请检查网络配置和输出域设置，或重启应用程序。"
    )


def topview_no_selection() -> ErrorMessage:
    """TopView 未选择设备"""
    return ErrorMessage(
        title="TopView",
        body="请先在 TopView 中框选已经拖入画板的设备。",
        severity=Severity.INFO,
        help_link=DOC_TOPVIEW_NO_SELECTION,
        recovery="在画板中框选设备，或使用 Ctrl+点击 选择多个。"
    )


def topview_insufficient_selection(required: int, actual: int) -> ErrorMessage:
    """TopView 选择设备数量不足"""
    return ErrorMessage(
        title="TopView",
        body=f"当前操作需要至少 {required} 台设备，但只选中了 {actual} 台。",
        severity=Severity.INFO,
        help_link=DOC_TOPVIEW_INSUFFICIENT,
        recovery="请增加选中的设备数量。"
    )


def xy_reference_no_devices() -> ErrorMessage:
    """XY 基准模式需要 XY 设备"""
    return ErrorMessage(
        title="XY 基准模式",
        body="请先在 TopView 中框选已经拖入画板的 XY 二维设备。",
        severity=Severity.INFO,
        help_link=DOC_XY_REFERENCE_NO_DEVICES,
        recovery="确认设备定义包含 X/Y 通道（Pan/Tilt 或 motor_x/motor_y），并在 TopView 中选中。"
    )


def project_save_failed(detail: str) -> ErrorMessage:
    """项目保存失败"""
    return ErrorMessage(
        title="保存失败",
        body=f"无法保存项目：{detail}",
        severity=Severity.CRITICAL,
        help_link=DOC_PROJECT_SAVE_FAILED,
        recovery="请检查磁盘空间和写入权限，或尝试保存到不同位置。"
    )


def project_load_failed(detail: str) -> ErrorMessage:
    """项目打开失败"""
    return ErrorMessage(
        title="打开失败",
        body=f"无法打开项目：{detail}",
        severity=Severity.CRITICAL,
        help_link=DOC_PROJECT_LOAD_FAILED,
        recovery="请检查自动保存备份（项目文件夹 _autosave/），或使用工程修复工具。"
    )


def project_no_devices() -> ErrorMessage:
    """项目中无设备"""
    return ErrorMessage(
        title="操作失败",
        body="请先新建或打开一个项目。",
        severity=Severity.INFO,
        help_link=DOC_PROJECT_NO_DEVICES,
        recovery="导入设备或手动添加设备组。"
    )


def topview_minimum_pages() -> ErrorMessage:
    """TopView 最少页面限制"""
    return ErrorMessage(
        title="TopView",
        body="至少保留一个页面。",
        severity=Severity.INFO,
        help_link=DOC_TOPVIEW_MIN_PAGES
    )


def topview_merge_insufficient_types(count: int) -> ErrorMessage:
    """合并需要多种设备类型"""
    return ErrorMessage(
        title="按类型合并",
        body=f"项目中只有 {count} 种设备类型，至少需要 2 种已配接的设备类型。",
        severity=Severity.INFO,
        help_link=DOC_TOPVIEW_MERGE_TYPES,
        recovery="导入更多种类的设备，或使用其他合并方式。"
    )


def topview_no_shapes() -> ErrorMessage:
    """TopView 无图形或设备"""
    return ErrorMessage(
        title="TopView",
        body="请先绘制直线/圆/椭圆/曲线或生成花样矩阵。",
        severity=Severity.INFO,
        help_link=DOC_TOPVIEW_NO_SHAPES,
        recovery="使用绘图工具绘制图形，或生成花样矩阵。"
    )


def auto_group_no_candidates() -> ErrorMessage:
    """自动编组无候选方案"""
    return ErrorMessage(
        title="自动编组",
        body="未能从当前多圆布局中提取有效编组，请尝试「换一组」。",
        severity=Severity.INFO,
        help_link=DOC_AUTO_GROUP_NO_CANDIDATES,
        recovery="确保设备按多个圆形/环形排布（每圆至少 6 台），或手动创建设备组。"
    )


def network_port_conflict(port: int) -> ErrorMessage:
    """网络端口冲突"""
    return ErrorMessage(
        title="网络配置错误",
        body=f"端口 {port} 被其他程序占用。",
        severity=Severity.ERROR,
        help_link=DOC_NETWORK_PORT_CONFLICT,
        recovery="关闭可能占用端口的程序（其他灯控软件，或使用诊断工具检查。"
    )


def firewall_blocking() -> ErrorMessage:
    """防火墙阻止 DMX 输出"""
    return ErrorMessage(
        title="网络配置",
        body="Windows 防火墙可能阻止了 DMX 输出。",
        severity=Severity.WARNING,
        help_link=DOC_FIREWALL_BLOCKING,
        recovery="使用「网络面板 → 一键放行防火墙」，或手动配置防火墙允许 UDP 端口 6454/5568。"
    )


def cross_device_output_info() -> ErrorMessage:
    """跨设备输出配置说明"""
    return ErrorMessage(
        title="跨设备输出",
        body="配置跨设备 DMX 输出需要正确的网络设置。",
        severity=Severity.INFO,
        help_link=DOC_CROSS_DEVICE_OUTPUT,
        recovery="确认目标 IP 地址，配置输出目标，并放行防火墙。"
    )


def effect_no_devices() -> ErrorMessage:
    """效果需要选择设备"""
    return ErrorMessage(
        title="效果",
        body="当前效果需要在 TopView 或 Programmer 中选中设备。",
        severity=Severity.INFO,
        help_link=DOC_EFFECT_NO_DEVICES,
        recovery="在 TopView 画板中框选设备，或在 Programmer 调度台中选择设备组。"
    )


def effect_incompatible_type(effect_type: str, required: str) -> ErrorMessage:
    """效果与设备类型不兼容"""
    return ErrorMessage(
        title="效果不兼容",
        body=f"{effect_type} 效果需要 {required} 类型的设备。",
        severity=Severity.WARNING,
        help_link=DOC_EFFECT_INCOMPATIBLE,
        recovery="检查效果说明中的设备要求，选择兼容的设备类型。"
    )


def timeline_clip_overlap() -> ErrorMessage:
    """Clip 重叠冲突"""
    return ErrorMessage(
        title="时间线",
        body="同一轨道上的 Clip 不能重叠（除非使用图层模式）。",
        severity=Severity.WARNING,
        help_link=DOC_TIMELINE_CLIP_OVERLAP,
        recovery="移动 Clip 避免重叠，或启用图层模式。"
    )


def timeline_audio_sync_failed(detail: str) -> ErrorMessage:
    """音频同步失败"""
    return ErrorMessage(
        title="音频同步",
        body=f"音频同步失败：{detail}",
        severity=Severity.ERROR,
        help_link=DOC_TIMELINE_AUDIO_SYNC,
        recovery="确认音频格式（支持 WAV/MP3/FLAC/OGG）和采样率（建议 44.1kHz 或 48kHz）。"
    )


def performance_lag_warning() -> ErrorMessage:
    """性能卡顿警告"""
    return ErrorMessage(
        title="性能警告",
        body="检测到操作卡顿或延迟，可能影响实时输出。",
        severity=Severity.WARNING,
        help_link=DOC_PERFORMANCE_LAG,
        recovery="减少同时播放的 Clip 数量，降低刷新率，或使用离线渲染。"
    )


def license_invalid() -> ErrorMessage:
    """许可证无效或过期"""
    return ErrorMessage(
        title="许可证错误",
        body="许可证无效或已过期。",
        severity=Severity.CRITICAL,
        help_link=DOC_LICENSE_INVALID,
        recovery="请联系技术支持续期或激活，并确认系统时间正确。"
    )


def license_hardware_change() -> ErrorMessage:
    """硬件变更导致许可证失效"""
    return ErrorMessage(
        title="许可证错误",
        body="硬件变更导致许可证失效。",
        severity=Severity.ERROR,
        help_link=DOC_LICENSE_HARDWARE,
        recovery="记录机器码（帮助 → 关于 → 机器码）并联系技术支持。"
    )


# 便捷别名（向后兼容）
def import__failed(detail: str) -> ErrorMessage:
    """别名：导入失败"""
    return _import_failed(detail)


def import_patch_failed(detail: str) -> ErrorMessage:
    """别名：配接表导入失败"""
    return patch_import_failed(detail)


def export_mvr_failed(reason: str) -> ErrorMessage:
    """别名：MVR 导出失败"""
    return mvr_export_failed(reason)
