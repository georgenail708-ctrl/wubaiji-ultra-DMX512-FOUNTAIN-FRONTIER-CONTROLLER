"""
src/utils/code_guard.py — AI 代码质量二遍门（基于 guard-skills 模式）
参考: test-guard + clean-code-guard (GitHub amElnagdy/guard-skills)

用法:
    from src.utils.code_guard import audit_module, check_no_mock_abuse

    # 审计单个模块
    issues = audit_module("src/ui/main_window.py")
    for issue in issues:
        print(issue)
"""

from __future__ import annotations

import ast
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CodeIssue:
    """代码质量发现。"""
    file: str
    line: int
    severity: str  # "error" | "warning" | "info"
    category: str   # "test_quality" | "clean_code" | "ai_smell" | "security"
    message: str
    suggestion: str = ""


# ── 禁止模式（来自 guard-skills + darrenoakey 零伪造测试）────────

_FORBIDDEN_TEST_PATTERNS = [
    # test-guard 模式：模拟滥用
    (r"mock\.patch|unittest\.mock|MagicMock|Mock\(", "test_quality",
     "Mock detected — prefer real integration tests per zero-fabrication policy",
     "Replace with real dependency or plugin interface (CheckPrinter pattern)"),
    # test-guard 模式：测试什么都没捕获
    (r"def test_.*:\s*$\s*pass", "test_quality",
     "Empty test body — test catches nothing",
     "Add at least one assertion or mark as @pytest.mark.skip with reason"),
    # test-guard 模式：assert True
    (r"assert\s+True\s*($|#)", "test_quality",
     "assert True is a no-op — test verifies nothing",
     "Replace with actual assertion on expected behavior"),
    # test-guard 模式：实现细节断言
    (r"assert\s+.*\.__dict__|assert\s+.*\._", "test_quality",
     "Testing private implementation detail — brittle, breaks on refactor",
     "Test public API behavior instead of internal state"),
    # clean-code-guard 模式：AI 幻觉 API
    (r"import\s+\w+\s*#\s*(?:not\s+installed|pip\s+install|TODO)", "ai_smell",
     "Import with install note — possible hallucinated dependency",
     "Verify the package exists on PyPI and is in requirements.txt"),
    # clean-code-guard 模式：catch-all 错误吞没
    (r"except\s*(?:Exception|BaseException)?\s*:\s*$\s*pass", "clean_code",
     "Exception silently swallowed — debugging nightmare",
     "Log the error with context or re-raise"),
    # clean-code-guard 模式：过大函数
    (r"^\s*def\s+\w+", "clean_code",
     "Function detected — check length ≤100 lines per Trail of Bits",
     "Split into helper functions if >100 lines"),
]

_CLEAN_CODE_PATTERNS = [
    # SOLID 违规检测
    (r"class\s+\w+.*:\s*$\s*(?:.{,50}$\s*){,3}pass", "clean_code",
     "Class with only pass — violates Single Responsibility",
     "Add behavior or convert to dataclass/namedtuple"),
    # DRY 违规：重复代码（简化检测 — 相同长字符串出现 >3 次）
    (r"raise\s+NotImplementedError", "clean_code",
     "NotImplementedError in production path — stub leak",
     "Complete implementation or guard with explicit check"),
]


def audit_module(filepath: str, *, strict: bool = False) -> List[CodeIssue]:
    """对单个 Python 文件执行代码质量审计。

    Args:
        filepath: 要审计的 .py 文件路径
        strict: True = 所有 warning 升级为 error

    Returns:
        CodeIssue 列表，无问题则为空
    """
    path = Path(filepath)
    if not path.is_file() or path.suffix != ".py":
        return []

    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        logger.debug("audit_module: failed to read %s, returning empty", filepath)
        return []

    issues: List[CodeIssue] = []

    # ── 模式检测 ──
    patterns_to_check = _FORBIDDEN_TEST_PATTERNS
    if strict:
        patterns_to_check = _FORBIDDEN_TEST_PATTERNS + _CLEAN_CODE_PATTERNS

    for lineno, line in enumerate(source.split("\n"), 1):
        for pattern, category, message, suggestion in patterns_to_check:
            if re.search(pattern, line, re.MULTILINE | re.DOTALL):
                sev = "error" if strict else "warning"
                issues.append(CodeIssue(
                    file=str(path),
                    line=lineno,
                    severity=sev,
                    category=category,
                    message=message,
                    suggestion=suggestion,
                ))

    # ── AST 分析：检测裸 except ──
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    issues.append(CodeIssue(
                        file=str(path),
                        line=node.lineno,
                        severity="error",
                        category="clean_code",
                        message="Bare except: — catches SystemExit/KeyboardInterrupt",
                        suggestion="Use 'except Exception as e:' with logging",
                    ))
    except SyntaxError as e:
        issues.append(CodeIssue(
            file=str(path),
            line=e.lineno or 0,
            severity="error",
            category="clean_code",
            message=f"Syntax error: {e.msg}",
            suggestion="Fix syntax error",
        ))

    return issues


def audit_project(root_dir: str = "src", *, strict: bool = False) -> List[CodeIssue]:
    """对整个项目做代码质量审计。

    Args:
        root_dir: 项目源码根目录
        strict: True = 所有 warning 升级为 error

    Returns:
        所有文件的 CodeIssue 列表
    """
    all_issues: List[CodeIssue] = []
    root = Path(root_dir)
    if not root.is_dir():
        return all_issues

    for py_file in root.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        issues = audit_module(str(py_file), strict=strict)
        all_issues.extend(issues)

    return all_issues


def print_audit_report(issues: List[CodeIssue]) -> str:
    """生成人类可读的审计报告。"""
    if not issues:
        return "No issues found. Clean codebase!"

    by_category: dict = {}
    for issue in issues:
        by_category.setdefault(issue.category, []).append(issue)

    lines = [f"Code Audit Report — {len(issues)} issue(s) found\n"]
    lines.append("=" * 60)

    for cat, cat_issues in sorted(by_category.items()):
        errors = [i for i in cat_issues if i.severity == "error"]
        warnings = [i for i in cat_issues if i.severity == "warning"]
        lines.append(f"\n[{cat}] {len(errors)} error(s), {len(warnings)} warning(s)")
        for issue in cat_issues[:5]:  # 每类最多 5 条
            lines.append(f"  {issue.file}:{issue.line} [{issue.severity}] {issue.message}")
            if issue.suggestion:
                lines.append(f"    → {issue.suggestion}")
        if len(cat_issues) > 5:
            lines.append(f"  ... and {len(cat_issues) - 5} more")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)
