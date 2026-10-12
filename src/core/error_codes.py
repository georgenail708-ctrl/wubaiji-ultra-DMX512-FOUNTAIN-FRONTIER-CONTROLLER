"""错误代码注册表 — 统一的错误标识与分类系统。

错误代码格式: ERR-XYYY
  X    = 类别代码 (1位)
  YYY  = 具体错误编号 (3位)

类别分类:
  1xxx = UI/界面错误
  2xxx = 引擎/渲染错误
  3xxx = 网络/协议错误
  4xxx = 文件I/O错误
  5xxx = 授权/许可错误
  6xxx = 音频处理错误
  7xxx = 导入/导出错误
  8xxx = 配置/设置错误
  9xxx = 系统/环境错误
"""
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorCodeDefinition:
    """错误代码定义"""
    code: str
    title: str
    description: str
    user_message: str
    recovery_suggestion: Optional[str] = None
    help_link: Optional[str] = None

    @property
    def category(self) -> str:
        """获取错误类别"""
        cat_map = {
            "1": "UI",
            "2": "Engine",
            "3": "Network",
            "4": "FileIO",
            "5": "License",
            "6": "Audio",
            "7": "ImportExport",
            "8": "Configuration",
            "9": "System"
        }
        return cat_map.get(self.code.split("-")[1][0], "Unknown")


# ============================================================
# 错误代码注册表
# ============================================================

ERROR_REGISTRY: Dict[str, ErrorCodeDefinition] = {

    # ==================== 1xxx: UI/界面错误 ====================
    "ERR-1001": ErrorCodeDefinition(
        code="ERR-1001",
        title="面板初始化失败",
        description="UI panel failed to initialize",
        user_message="界面面板初始化失败，部分功能可能无法使用。",
        recovery_suggestion="请重启应用程序。如果问题持续，请联系技术支持。"
    ),

    "ERR-1002": ErrorCodeDefinition(
        code="ERR-1002",
        title="无效的用户输入",
        description="User input validation failed",
        user_message="输入的数据格式不正确。",
        recovery_suggestion="请检查输入格式并重试。"
    ),

    "ERR-1003": ErrorCodeDefinition(
        code="ERR-1003",
        title="窗口创建失败",
        description="Failed to create window or dialog",
        user_message="无法创建窗口或对话框。",
        recovery_suggestion="请检查系统资源是否充足，或尝试重启应用程序。"
    ),

    "ERR-1004": ErrorCodeDefinition(
        code="ERR-1004",
        title="UI状态不一致",
        description="UI state inconsistency detected",
        user_message="界面状态异常，显示可能不准确。",
        recovery_suggestion="请尝试刷新当前页面或重新加载项目。"
    ),

    # ==================== 2xxx: 引擎/渲染错误 ====================
    "ERR-2001": ErrorCodeDefinition(
        code="ERR-2001",
        title="渲染引擎初始化失败",
        description="Rendering engine failed to initialize",
        user_message="渲染引擎启动失败，无法输出DMX信号。",
        recovery_suggestion="请检查系统配置，确保没有其他程序占用DMX设备。"
    ),

    "ERR-2002": ErrorCodeDefinition(
        code="ERR-2002",
        title="帧渲染超时",
        description="Frame rendering timeout",
        user_message="渲染帧超时，可能导致输出延迟。",
        recovery_suggestion="请降低场景复杂度或关闭不必要的效果。"
    ),

    "ERR-2003": ErrorCodeDefinition(
        code="ERR-2003",
        title="效果计算失败",
        description="Effect computation failed",
        user_message="效果计算失败，该效果无法正常播放。",
        recovery_suggestion="请检查效果参数设置，或尝试重新创建该效果。"
    ),

    "ERR-2004": ErrorCodeDefinition(
        code="ERR-2004",
        title="Clip播放错误",
        description="Clip playback error",
        user_message="Clip播放失败，时间线可能无法正常运行。",
        recovery_suggestion="请检查Clip设置，确保所有参数有效。"
    ),

    "ERR-2005": ErrorCodeDefinition(
        code="ERR-2005",
        title="内存不足",
        description="Insufficient memory for rendering",
        user_message="渲染内存不足，可能导致性能下降或崩溃。",
        recovery_suggestion="请关闭其他应用程序或减少项目规模。"
    ),

    # ==================== 3xxx: 网络/协议错误 ====================
    "ERR-3001": ErrorCodeDefinition(
        code="ERR-3001",
        title="Art-Net初始化失败",
        description="Art-Net protocol initialization failed",
        user_message="Art-Net网络初始化失败，无法发送DMX数据。",
        recovery_suggestion="请检查网络连接和防火墙设置。"
    ),

    "ERR-3002": ErrorCodeDefinition(
        code="ERR-3002",
        title="sACN初始化失败",
        description="sACN protocol initialization failed",
        user_message="sACN网络初始化失败，无法发送DMX数据。",
        recovery_suggestion="请检查网络连接和防火墙设置。"
    ),

    "ERR-3003": ErrorCodeDefinition(
        code="ERR-3003",
        title="网络发送失败",
        description="Network transmission failed",
        user_message="网络数据发送失败，设备可能无法接收信号。",
        recovery_suggestion="请检查网络连接状态和目标设备是否在线。"
    ),

    "ERR-3004": ErrorCodeDefinition(
        code="ERR-3004",
        title="端口占用",
        description="Network port already in use",
        user_message="网络端口已被占用，无法启动网络服务。",
        recovery_suggestion="请关闭其他使用该端口的程序，或更改端口设置。"
    ),

    # ==================== 4xxx: 文件I/O错误 ====================
    "ERR-4001": ErrorCodeDefinition(
        code="ERR-4001",
        title="文件无法打开",
        description="Failed to open file",
        user_message="无法打开指定的文件。",
        recovery_suggestion="请检查文件是否存在，以及是否有读取权限。"
    ),

    "ERR-4002": ErrorCodeDefinition(
        code="ERR-4002",
        title="文件保存失败",
        description="Failed to save file",
        user_message="文件保存失败。",
        recovery_suggestion="请检查磁盘空间是否充足，以及是否有写入权限。"
    ),

    "ERR-4003": ErrorCodeDefinition(
        code="ERR-4003",
        title="文件格式错误",
        description="Invalid file format",
        user_message="文件格式不正确或已损坏。",
        recovery_suggestion="请确认文件格式正确，或尝试使用备份文件。"
    ),

    "ERR-4004": ErrorCodeDefinition(
        code="ERR-4004",
        title="文件版本不兼容",
        description="Incompatible file version",
        user_message="文件版本与当前程序不兼容。",
        recovery_suggestion="请使用相应版本的程序打开文件，或升级文件格式。"
    ),

    "ERR-4005": ErrorCodeDefinition(
        code="ERR-4005",
        title="磁盘空间不足",
        description="Insufficient disk space",
        user_message="磁盘空间不足，无法完成操作。",
        recovery_suggestion="请清理磁盘空间后重试。"
    ),

    # ==================== 5xxx: 授权/许可错误 ====================
    "ERR-5001": ErrorCodeDefinition(
        code="ERR-5001",
        title="许可证无效",
        description="Invalid license",
        user_message="许可证无效或已过期。",
        recovery_suggestion="请联系供应商获取有效的许可证。"
    ),

    "ERR-5002": ErrorCodeDefinition(
        code="ERR-5002",
        title="许可证验证失败",
        description="License verification failed",
        user_message="许可证验证失败。",
        recovery_suggestion="请检查网络连接，或联系技术支持。"
    ),

    "ERR-5003": ErrorCodeDefinition(
        code="ERR-5003",
        title="功能未授权",
        description="Feature not licensed",
        user_message="该功能未获得授权。",
        recovery_suggestion="请升级许可证以使用此功能。"
    ),

    # ==================== 6xxx: 音频处理错误 ====================
    "ERR-6001": ErrorCodeDefinition(
        code="ERR-6001",
        title="音频设备初始化失败",
        description="Audio device initialization failed",
        user_message="音频设备初始化失败，无法进行音频分析。",
        recovery_suggestion="请检查音频设备连接和驱动程序。"
    ),

    "ERR-6002": ErrorCodeDefinition(
        code="ERR-6002",
        title="音频文件加载失败",
        description="Failed to load audio file",
        user_message="无法加载音频文件。",
        recovery_suggestion="请检查文件格式是否支持（WAV、MP3、FLAC等）。"
    ),

    "ERR-6003": ErrorCodeDefinition(
        code="ERR-6003",
        title="音频分析失败",
        description="Audio analysis failed",
        user_message="音频分析失败，无法生成效果。",
        recovery_suggestion="请尝试其他音频文件，或检查文件是否损坏。"
    ),

    # ==================== 7xxx: 导入/导出错误 ====================
    "ERR-7001": ErrorCodeDefinition(
        code="ERR-7001",
        title="导入 工程",
        description="project import failed",
        user_message="导入失败。",
        recovery_suggestion="请确认文件完整性，或在 R4 中另存为新文件后重试。",
        help_link="#-import-failed"
    ),

    "ERR-7002": ErrorCodeDefinition(
        code="ERR-7002",
        title="导入配接表",
        description="DMX patch table import failed",
        user_message="导入失败。",
        recovery_suggestion="请检查 CSV 格式、编码（UTF-8）和必需字段。",
        help_link="#patch-import-failed"
    ),

    "ERR-7003": ErrorCodeDefinition(
        code="ERR-7003",
        title="设备定义解析失败",
        description="Fixture definition parsing failed",
        user_message="设备定义文件解析失败。",
        recovery_suggestion="请确认设备定义文件格式正确。"
    ),

    "ERR-7004": ErrorCodeDefinition(
        code="ERR-7004",
        title="导出 MVR",
        description="Export operation failed",
        user_message="导出失败。",
        recovery_suggestion="请确认项目中有已配接的设备，且目标路径可写入。",
        help_link="#mvr-export-failed"
    ),

    # ==================== 8xxx: 配置/设置错误 ====================
    "ERR-8001": ErrorCodeDefinition(
        code="ERR-8001",
        title="配置文件损坏",
        description="Configuration file corrupted",
        user_message="配置文件损坏，已使用默认设置。",
        recovery_suggestion="请在设置中重新配置，或删除配置文件后重启。"
    ),

    "ERR-8002": ErrorCodeDefinition(
        code="ERR-8002",
        title="配置项无效",
        description="Invalid configuration value",
        user_message="配置项包含无效值。",
        recovery_suggestion="请检查设置并输入有效值。"
    ),

    "ERR-8003": ErrorCodeDefinition(
        code="ERR-8003",
        title="设置保存失败",
        description="Failed to save settings",
        user_message="设置保存失败。",
        recovery_suggestion="请检查文件权限，或尝试以管理员身份运行。"
    ),

    # ==================== 9xxx: 系统/环境错误 ====================
    "ERR-9001": ErrorCodeDefinition(
        code="ERR-9001",
        title="Python环境错误",
        description="Python environment error",
        user_message="Python运行环境异常。",
        recovery_suggestion="请重新安装程序或检查Python版本。"
    ),

    "ERR-9002": ErrorCodeDefinition(
        code="ERR-9002",
        title="依赖库缺失",
        description="Missing dependency",
        user_message="缺少必要的依赖库。",
        recovery_suggestion="请重新安装程序以恢复缺失的组件。"
    ),

    "ERR-9003": ErrorCodeDefinition(
        code="ERR-9003",
        title="操作系统不兼容",
        description="Operating system incompatibility",
        user_message="当前操作系统不完全支持此功能。",
        recovery_suggestion="请升级操作系统或使用兼容的系统版本。"
    ),

    "ERR-9004": ErrorCodeDefinition(
        code="ERR-9004",
        title="未知系统错误",
        description="Unknown system error",
        user_message="发生未知的系统错误。",
        recovery_suggestion="请记录错误代码并联系技术支持。"
    ),
}


def get_error_definition(code: str) -> Optional[ErrorCodeDefinition]:
    """获取错误代码定义

    Args:
        code: 错误代码（如 "ERR-1001"）

    Returns:
        错误定义对象，如果代码不存在则返回 None
    """
    return ERROR_REGISTRY.get(code)


def list_errors_by_category(category_code: str) -> Dict[str, ErrorCodeDefinition]:
    """按类别列出所有错误代码

    Args:
        category_code: 类别代码（如 "1" 表示UI错误）

    Returns:
        该类别下的所有错误定义字典
    """
    return {
        code: definition
        for code, definition in ERROR_REGISTRY.items()
        if code.split("-")[1].startswith(category_code)
    }


def validate_error_code(code: str) -> bool:
    """验证错误代码格式是否正确

    Args:
        code: 待验证的错误代码

    Returns:
        格式是否有效
    """
    if not code.startswith("ERR-"):
        return False
    try:
        num_part = code[4:]
        return len(num_part) == 4 and num_part.isdigit()
    except (IndexError, AttributeError):
        return False
