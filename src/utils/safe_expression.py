from __future__ import annotations

import ast
import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np


class UnsafeExpressionError(ValueError):
    pass


_ALLOWED_NODE_TYPES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Tuple,
    ast.List,
    ast.Subscript,
    ast.Slice,
    ast.Attribute,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)

_FORBIDDEN_NODE_TYPES = (
    ast.Assign,
    ast.AugAssign,
    ast.NamedExpr,
    ast.Lambda,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.Import,
    ast.ImportFrom,
)

_ALLOWED_NUMPY_ATTRS = frozenset(
    {
        "abs",
        "absolute",
        "arange",
        "array",
        "asarray",
        "ceil",
        "clip",
        "cos",
        "deg2rad",
        "exp",
        "float32",
        "float64",
        "floor",
        "linspace",
        "log",
        "maximum",
        "mean",
        "minimum",
        "pi",
        "power",
        "rad2deg",
        "round",
        "sin",
        "sqrt",
        "tan",
        "tile",
        "uint8",
        "where",
        "zeros",
        "ones",
    }
)

_ALLOWED_MATH_ATTRS = frozenset(
    {
        "acos",
        "asin",
        "atan",
        "atan2",
        "ceil",
        "cos",
        "degrees",
        "e",
        "exp",
        "floor",
        "log",
        "pi",
        "pow",
        "radians",
        "sin",
        "sqrt",
        "tan",
    }
)

_SAFE_BUILTINS = MappingProxyType(
    {
        "abs": abs,
        "max": max,
        "min": min,
        "pow": pow,
        "round": round,
    }
)


def clip_value(value: Any, lo: float, hi: float) -> Any:
    return np.clip(value, lo, hi)


class _ExpressionValidator(ast.NodeVisitor):
    def __init__(self, allowed_names: set[str]):
        self._allowed_names = allowed_names

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, _FORBIDDEN_NODE_TYPES):
            raise UnsafeExpressionError(f"Unsupported expression element: {type(node).__name__}")
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise UnsafeExpressionError(f"Unsupported expression element: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("_") or node.id not in self._allowed_names:
            raise UnsafeExpressionError(f"Name is not allowed: {node.id}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise UnsafeExpressionError(f"Private attribute is not allowed: {node.attr}")
        if isinstance(node.value, ast.Name):
            if node.value.id == "np":
                if node.attr not in _ALLOWED_NUMPY_ATTRS:
                    raise UnsafeExpressionError(f"numpy attribute is not allowed: {node.attr}")
            elif node.value.id == "math":
                if node.attr not in _ALLOWED_MATH_ATTRS:
                    raise UnsafeExpressionError(f"math attribute is not allowed: {node.attr}")
            else:
                raise UnsafeExpressionError(f"Attribute access is not allowed: {node.value.id}.{node.attr}")
        elif node.attr == "astype":
            self.visit(node.value)
        else:
            raise UnsafeExpressionError("Nested attribute access is not allowed")
        if not isinstance(node.value, ast.Call):
            self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if node.keywords:
            raise UnsafeExpressionError("Keyword arguments are not allowed")
        if isinstance(node.func, ast.Name):
            if node.func.id not in self._allowed_names:
                raise UnsafeExpressionError(f"Function is not allowed: {node.func.id}")
        elif isinstance(node.func, ast.Attribute):
            self.visit(node.func)
        else:
            raise UnsafeExpressionError("Only named functions are allowed")
        for arg in node.args:
            self.visit(arg)


def safe_eval_expression(expr: str, variables: Mapping[str, Any]) -> Any:
    src = expr.strip()
    if not src:
        raise UnsafeExpressionError("Expression is empty")

    env: dict[str, Any] = {
        **_SAFE_BUILTINS,
        "clip": clip_value,
        "np": np,
        "math": math,
        "pi": math.pi,
        "e": math.e,
        "sin": np.sin,
        "cos": np.cos,
        "sqrt": np.sqrt,
        "log": np.log,
        "exp": np.exp,
    }
    env.update(variables)

    tree = ast.parse(src, mode="eval")
    _ExpressionValidator(set(env)).visit(tree)
    code = compile(tree, "<safe_expression>", "eval")
    return eval(code, {"__builtins__": {}}, env)
