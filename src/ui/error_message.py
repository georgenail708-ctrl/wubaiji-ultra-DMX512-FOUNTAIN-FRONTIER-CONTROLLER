"""统一错误消息类 — 标准化的用户反馈对话框。

提供标题、正文、帮助链接、恢复建议的结构化错误展示，
与 QMessageBox 无缝集成，支持多级严重性。

版本 2.0 新增功能:
- 错误代码系统支持 (ERR-XYYY 格式)
- 开发/生产模式自动切换
- 开发模式显示完整堆栈跟踪
- 生产模式显示友好消息+错误代码
"""
from enum import Enum
from typing import Optional
import sys
import traceback
from PyQt6.QtWidgets import QMessageBox, QWidget, QLabel
from PyQt6.QtCore import Qt
import os


class Severity(Enum):
    """消息严重性级别"""
    INFO = "information"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    QUESTION = "question"


def _is_dev_mode() -> bool:
    """检测是否为开发模式

    Returns:
        True = 开发模式（显示堆栈跟踪）
        False = 生产模式（显示友好消息）
    """
    # PyInstaller打包后 sys.frozen 为 True
    return not getattr(sys, "frozen", False)


class ErrorMessage:
    """统一错误消息类

    用法示例 (传统方式):
        msg = ErrorMessage(
            title="导入失败",
            body="无法解析文件格式",
            severity=Severity.ERROR,
            help_link="https://docs.example.com/import",
            recovery="请检查文件是否完整，或尝试重新导出"
        )
        msg.show(parent_widget)

    用法示例 (使用错误代码):
        msg = ErrorMessage.from_code(
            "ERR-7001",
            exception=e,  # 可选，会在开发模式显示堆栈
            parent=self
        )
        msg.show()

    用法示例 (带异常的错误代码):
        try:
            risky_operation()
        except Exception as e:
            ErrorMessage.from_code("ERR-2003", exception=e).show(self)
    """

    def __init__(
        self,
        title: str,
        body: str,
        severity: Severity = Severity.ERROR,
        *,
        help_link: Optional[str] = None,
        recovery: Optional[str] = None,
        error_code: Optional[str] = None,
        exception: Optional[Exception] = None
    ):
        """初始化错误消息

        Args:
            title: 对话框标题（简短概括）
            body: 主要错误描述
            severity: 严重性级别
            help_link: 帮助文档链接（可选）
            recovery: 恢复建议（可选）
            error_code: 错误代码（如 "ERR-1001"）
            exception: 异常对象（开发模式会显示堆栈跟踪）
        """
        self.title = title
        self.body = body
        self.severity = severity
        self.help_link = help_link
        self.recovery = recovery
        self.error_code = error_code
        self.exception = exception

    @classmethod
    def from_code(
        cls,
        error_code: str,
        exception: Optional[Exception] = None,
        severity: Optional[Severity] = None,
        additional_context: Optional[str] = None
    ) -> "ErrorMessage":
        """从错误代码创建错误消息

        Args:
            error_code: 错误代码（如 "ERR-1001"）
            exception: 异常对象（可选）
            severity: 严重性级别（可选，默认使用 ERROR）
            additional_context: 额外的上下文信息（可选）

        Returns:
            ErrorMessage 实例
        """
        try:
            from src.core.error_codes import get_error_definition
        except ImportError:
            from core.error_codes import get_error_definition

        definition = get_error_definition(error_code)

        if definition is None:
            return cls(
                title="未知错误",
                body=f"发生错误（代码: {error_code}）",
                severity=severity or Severity.ERROR,
                error_code=error_code,
                exception=exception
            )

        body = definition.user_message
        if additional_context:
            body = f"{body}\n\n{additional_context}"

        return cls(
            title=definition.title,
            body=body,
            severity=severity or Severity.ERROR,
            help_link=definition.help_link,
            recovery=definition.recovery_suggestion,
            error_code=error_code,
            exception=exception
        )

    def _format_text(self) -> str:
        """格式化完整消息文本（根据开发/生产模式自动调整）"""
        parts = []

        # 开发模式：显示完整堆栈跟踪
        if _is_dev_mode() and self.exception:
            parts.append("【开发模式 - 详细错误信息】")
            parts.append(self.body)
            parts.append("\n【异常类型】")
            parts.append(f"{type(self.exception).__name__}: {str(self.exception)}")
            parts.append("\n【堆栈跟踪】")
            parts.append("".join(traceback.format_exception(
                type(self.exception),
                self.exception,
                self.exception.__traceback__
            )))
        else:
            # 生产模式：友好消息
            parts.append(self.body)

        # 错误代码
        if self.error_code:
            if _is_dev_mode():
                parts.append(f"\n【错误代码】{self.error_code}")
            else:
                parts.append(f"\n错误代码: {self.error_code}")
                parts.append("请记录此代码以便技术支持人员诊断。")

        if self.recovery:
            parts.append(f"\n【恢复建议】\n{self.recovery}")

        if self.help_link:
            # 如果是本地文档锚点，转换为完整路径
            if self.help_link.startswith("#"):
                doc_path = self._get_doc_path()
                parts.append(f"\n【帮助文档】\n{doc_path}{self.help_link}")
            else:
                parts.append(f"\n【帮助文档】\n{self.help_link}")

        return "\n".join(parts)

    def _get_doc_path(self) -> str:
        """获取本地文档路径"""
        # 查找 troubleshooting.md 文档
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        doc_path = os.path.join(base_dir, "docs", "troubleshooting.md")
        if os.path.exists(doc_path):
            return f"file:///{doc_path.replace(os.sep, '/')}"
        return "docs/troubleshooting.md"

    # severity → QMessageBox 静态方法（分发表，替代 if/elif 链压嵌套）
    _SEVERITY_BOXES = {
        Severity.INFO: QMessageBox.information,
        Severity.WARNING: QMessageBox.warning,
        Severity.ERROR: QMessageBox.critical,
        Severity.CRITICAL: QMessageBox.critical,
        Severity.QUESTION: QMessageBox.question,
    }

    def show(self, parent: Optional[QWidget] = None) -> QMessageBox.StandardButton:
        """显示消息对话框

        Args:
            parent: 父窗口（可选）

        Returns:
            用户点击的按钮
        """
        text = self._format_text()
        box_fn = self._SEVERITY_BOXES.get(self.severity, QMessageBox.information)
        return box_fn(parent, self.title, text)

    def show_with_buttons(
        self,
        parent: Optional[QWidget] = None,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok
    ) -> QMessageBox.StandardButton:
        """显示带自定义按钮的对话框

        Args:
            parent: 父窗口
            buttons: 按钮组合（用 | 连接多个按钮）

        Returns:
            用户点击的按钮
        """
        msg_box = QMessageBox(parent)
        msg_box.setWindowTitle(self.title)

        # 使用富文本格式，使链接可点击
        formatted_text = self._format_rich_text()
        msg_box.setText(formatted_text)
        msg_box.setTextFormat(Qt.TextFormat.RichText)
        msg_box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )

        msg_box.setStandardButtons(buttons)

        # 根据严重性设置图标
        icon_map = {
            Severity.INFO: QMessageBox.Icon.Information,
            Severity.WARNING: QMessageBox.Icon.Warning,
            Severity.ERROR: QMessageBox.Icon.Critical,
            Severity.CRITICAL: QMessageBox.Icon.Critical,
            Severity.QUESTION: QMessageBox.Icon.Question,
        }
        msg_box.setIcon(icon_map.get(self.severity, QMessageBox.Icon.Information))

        return msg_box.exec()

    def _format_rich_text(self) -> str:
        """格式化为富文本（支持可点击链接，根据开发/生产模式自动调整）"""
        parts = []

        # 开发模式：显示完整堆栈跟踪
        if _is_dev_mode() and self.exception:
            parts.append("<b>【开发模式 - 详细错误信息】</b><br>")
            parts.append(self.body.replace("\n", "<br>"))
            parts.append("<br><br><b>【异常类型】</b><br>")
            parts.append(f"{type(self.exception).__name__}: {str(self.exception)}")
            parts.append("<br><br><b>【堆栈跟踪】</b><br>")
            tb_lines = traceback.format_exception(
                type(self.exception),
                self.exception,
                self.exception.__traceback__
            )
            parts.append("<pre>" + "".join(tb_lines).replace("<", "&lt;").replace(">", "&gt;") + "</pre>")
        else:
            # 生产模式：友好消息
            parts.append(self.body.replace("\n", "<br>"))

        # 错误代码
        if self.error_code:
            if _is_dev_mode():
                parts.append(f"<br><br><b>【错误代码】</b>{self.error_code}")
            else:
                parts.append(f"<br><br>错误代码: <b>{self.error_code}</b>")
                parts.append("<br>请记录此代码以便技术支持人员诊断。")

        if self.recovery:
            recovery_html = self.recovery.replace("\n", "<br>")
            parts.append(f"<br><br><b>【恢复建议】</b><br>{recovery_html}")

        if self.help_link:
            # 处理本地文档锚点
            if self.help_link.startswith("#"):
                doc_path = self._get_doc_path()
                link_url = f"{doc_path}{self.help_link}"
                parts.append(f'<br><br><b>【帮助文档】</b><br><a href="{link_url}">点击查看详细说明</a>')
            else:
                parts.append(f'<br><br><b>【帮助文档】</b><br><a href="{self.help_link}">点击查看详细说明</a>')

        return "".join(parts)


# 便捷工厂函数
def info(
    title: str,
    body: str,
    help_link: Optional[str] = None,
    recovery: Optional[str] = None
) -> ErrorMessage:
    """创建信息级别消息"""
    return ErrorMessage(title, body, Severity.INFO, help_link=help_link, recovery=recovery)


def warning(
    title: str,
    body: str,
    help_link: Optional[str] = None,
    recovery: Optional[str] = None
) -> ErrorMessage:
    """创建警告级别消息"""
    return ErrorMessage(title, body, Severity.WARNING, help_link=help_link, recovery=recovery)


def error(
    title: str,
    body: str,
    help_link: Optional[str] = None,
    recovery: Optional[str] = None
) -> ErrorMessage:
    """创建错误级别消息"""
    return ErrorMessage(title, body, Severity.ERROR, help_link=help_link, recovery=recovery)


def critical(
    title: str,
    body: str,
    help_link: Optional[str] = None,
    recovery: Optional[str] = None
) -> ErrorMessage:
    """创建严重错误级别消息"""
    return ErrorMessage(title, body, Severity.CRITICAL, help_link=help_link, recovery=recovery)


def question(
    title: str,
    body: str,
    help_link: Optional[str] = None,
    recovery: Optional[str] = None
) -> ErrorMessage:
    """创建询问级别消息"""
    return ErrorMessage(title, body, Severity.QUESTION, help_link=help_link, recovery=recovery)
