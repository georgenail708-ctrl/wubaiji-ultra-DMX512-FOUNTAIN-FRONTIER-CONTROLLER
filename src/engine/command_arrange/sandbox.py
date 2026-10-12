"""
command_arrange.sandbox — 命令行代码编曲的「安全沙箱执行器」

人/AI 写的 effect 代码（scalar 速写或 def 全式）在这里被**安全地**编译与执行，
算出每台设备每个通道的 DMX 值。命令文件可能是 AI 生成或外部导入，因此**禁止裸 exec**：

  · AST 白名单：在 utils.safe_expression 的纯表达式白名单之上放宽，额外允许
    def / if / for / while / FunctionDef / Assign / AugAssign / Return / Pass /
    比较 / 增量赋值 / lambda；
  · 仍然禁止：import / ImportFrom、以 `_` 开头的名称与属性（`__class__` 等魔术属性）、
    eval / exec / open / compile / globals / locals / __import__ 等危险内建；
  · 受限 globals：只暴露 safe_namespace() + 极小的安全内建子集（range/len/enumerate…），
    并强制 `__builtins__` 为该子集，杜绝 eval 注入真实内建。

确定性是硬性要求：noise/rand 都是输入的纯函数，不依赖 time/datetime/random。

纯 core，无任何 PyQt 依赖，也不 import EffectEngine（避免 core→ui 反向依赖）。
只产出一个符合引擎契约 `fn(ef, t, sorted_fx, fdefs, p, fps)` 的可调用对象，
由 renderer 自行插入引擎运行。
"""
from __future__ import annotations

import ast
import math
import sys
from typing import Any, Callable, Dict, List, Tuple

from . import ir
from .ir import ChannelBody, BODY_SCALAR, BODY_DEF


class UnsafeExpressionError(ValueError):
    """沙箱拒绝执行（命中白名单/黑名单/结构校验）时抛出。"""
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  AST 白名单 / 黑名单
# ─────────────────────────────────────────────────────────────────────────────
# 允许的节点（在纯表达式之上放宽到「函数 + 控制流 + 赋值」）。
_ALLOWED_NODES: Tuple[type, ...] = (
    ast.Module, ast.Expression, ast.Interactive,
    # 定义 / 函数
    ast.FunctionDef, ast.arguments, ast.arg, ast.Lambda,
    # 简单语句
    ast.Return, ast.Pass, ast.Break, ast.Continue, ast.Expr,
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr,
    # 控制流
    ast.If, ast.For, ast.While,
    ast.Try, ast.ExceptHandler, ast.Raise,
    # 表达式
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.keyword, ast.Name, ast.Constant, ast.Starred,
    ast.Tuple, ast.List, ast.Dict, ast.Set,
    ast.Subscript, ast.Slice,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.FormattedValue, ast.JoinedStr,
    # 上下文
    ast.Load, ast.Store,
    # 运算符
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub, ast.Not, ast.Invert,
    ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
)

# 明确禁止的节点（给出清晰的中文报错，而非泛化「不支持」）。
_FORBIDDEN_NODES: Tuple[type, ...] = (
    ast.Import, ast.ImportFrom,
    ast.Global, ast.Nonlocal,
    ast.ClassDef,
    ast.With, ast.Delete,
    ast.Await, ast.Yield, ast.YieldFrom,
    ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith,
)

_FORBIDDEN_NODE_NAMES = {
    "Import": "import 语句",
    "ImportFrom": "from … import 语句",
    "Global": "global 声明",
    "Nonlocal": "nonlocal 声明",
    "ClassDef": "class 定义",
    "With": "with 语句",
    "Delete": "del 语句",
    "Await": "await",
    "Yield": "yield",
    "YieldFrom": "yield from",
    "AsyncFunctionDef": "async 函数",
    "AsyncFor": "async for",
    "AsyncWith": "async with",
}

# 即便 __builtins__ 已清空也显式拦截的危险名（用于在校验期给出明确报错）。
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "open", "compile", "input",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr", "hasattr",
    "type", "object", "super", "memoryview",
    "breakpoint", "exit", "quit", "help",
    "classmethod", "staticmethod", "property",
    "bytes", "bytearray", "__import__",
})


# 幂运算的静态上限：指数过大或常量幂结果过大都视为「幂炸弹」。
_POW_MAX_EXP = 64
_POW_MAX_RESULT = 1e12
# 单次 range 可构造的最大长度（防 list(range(10**8)) 撑爆内存）。
_RANGE_MAX_LEN = 1_000_000
# 单次 compute fn 调用允许的最大「行事件」步数（settrace 计数，防死循环）。
# 取足够大以容纳数千台设备 × 合理的逐台计算量，但能在秒级内自爆。
_MAX_TRACE_STEPS = 5_000_000


def _const_value(node: ast.AST):
    """尝试把一个 AST 节点静态折叠为数值常量；折不出（或可能很大的幂）返回 None。

    仅折叠数值字面量与它们之间的 +,-,*,/,Pow，且对中间结果做幅度上限，
    避免在校验期自己触发 9**(9**9) 这种 C 层幂炸弹。
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, (int, float)) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _const_value(node.operand)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            # 幂单独处理：指数过大时不展开（返回 None），交给 visit_BinOp 拦截。
            base = _const_value(node.left)
            exp = _const_value(node.right)
            if base is None or exp is None:
                return None
            if abs(exp) > _POW_MAX_EXP:
                return None
            try:
                r = base ** exp
            except Exception:
                return None
            if isinstance(r, (int, float)) and abs(r) > 1e15:
                return None
            return r
        a = _const_value(node.left)
        b = _const_value(node.right)
        if a is None or b is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                r = a + b
            elif isinstance(node.op, ast.Sub):
                r = a - b
            elif isinstance(node.op, ast.Mult):
                r = a * b
            elif isinstance(node.op, ast.Div):
                r = a / b
            else:
                return None
        except Exception:
            return None
        if isinstance(r, (int, float)) and abs(r) > 1e15:
            return None
        return r
    return None


class _SafetyChecker(ast.NodeVisitor):
    """遍历 AST，收集所有违规（不抛异常，便于 UI 一次性展示多条）。"""

    def __init__(self) -> None:
        self.errors: List[str] = []

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, _FORBIDDEN_NODES):
            label = _FORBIDDEN_NODE_NAMES.get(type(node).__name__, type(node).__name__)
            self.errors.append(f"禁止使用 {label}")
            # 仍继续下探，尽量多收集
        elif not isinstance(node, _ALLOWED_NODES):
            self.errors.append(f"不支持的语法元素: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        nid = node.id
        if nid.startswith("_"):
            self.errors.append(f"禁止使用下划线开头的名称: {nid}")
        elif nid in _FORBIDDEN_NAMES:
            self.errors.append(f"禁止使用危险内建: {nid}")
        # Name 仅含 ctx 子节点（Load/Store），无需继续下探

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if isinstance(attr, str) and attr.startswith("_"):
            self.errors.append(f"禁止访问私有/魔术属性: .{attr}")
        elif attr == "format":
            # str.format 可经 {0.__class__} / {0.__globals__[...]} 反射逃逸。
            self.errors.append("禁止字符串 format/% 模板（可能用于反射逃逸）")
        # 属性访问本身允许（如 c.set / c.t），仅校验承载对象
        self.visit(node.value)

    def visit_Constant(self, node: ast.Constant) -> None:
        # 字符串字面量内夹带 "__"（如 {0.__class__}）一律拒绝，堵住 format/% 反射面。
        if isinstance(node.value, str) and "__" in node.value:
            self.errors.append("禁止字符串 format/% 模板（可能用于反射逃逸）")

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Pow):
            exp = _const_value(node.right)
            base = _const_value(node.left)
            bomb = False
            if exp is not None and abs(exp) > _POW_MAX_EXP:
                bomb = True
            elif base is not None and exp is not None:
                try:
                    if abs(base) ** abs(exp) > _POW_MAX_RESULT:
                        bomb = True
                except Exception:
                    bomb = True
            if bomb:
                self.errors.append("禁止超大幂运算（可能导致内存/CPU耗尽）")
        elif isinstance(node.op, ast.Mod):
            # 字符串 % 模板同样可反射逃逸（"%(x)s" % ...）。
            if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                self.errors.append("禁止字符串 format/% 模板（可能用于反射逃逸）")
        self.generic_visit(node)


def _walk_collect(tree: ast.AST) -> List[str]:
    chk = _SafetyChecker()
    chk.visit(tree)
    return chk.errors


# ─────────────────────────────────────────────────────────────────────────────
#  安全命名空间（暴露给用户代码的函数与常量）
# ─────────────────────────────────────────────────────────────────────────────
def _clamp_num(v: Any, lo: float = 0.0, hi: float = 255.0) -> float:
    try:
        f = float(v)
    except Exception:
        return float(lo)
    if f < lo:
        return float(lo)
    if f > hi:
        return float(hi)
    return f


def _ci(v: Any) -> int:
    """clamp 到 0..255 的整数（DMX 字节）。"""
    try:
        iv = int(round(float(v)))
    except Exception:
        return 0
    if iv < 0:
        return 0
    if iv > 255:
        return 255
    return iv


def _clamp(v: Any, lo: float = 0.0, hi: float = 255.0) -> float:
    return _clamp_num(v, lo, hi)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _smoothstep(a: float, b: float, x: float) -> float:
    if a == b:
        return 0.0 if x < a else 1.0
    t = (x - a) / (b - a)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return t * t * (3.0 - 2.0 * t)


def _hsv(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """h/s/v 均 0..1（h 取小数循环），返回 0..255 的 (r,g,b) 整数。"""
    try:
        h = float(h) % 1.0
        s = _clamp_num(s, 0.0, 1.0)
        v = _clamp_num(v, 0.0, 1.0)
    except Exception:
        return (0, 0, 0)
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i %= 6
    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return (_ci(r * 255.0), _ci(g * 255.0), _ci(b * 255.0))


def _rgb(r: float, g: float, b: float) -> Tuple[int, int, int]:
    return (_ci(r), _ci(g), _ci(b))


def _noise(x: float) -> float:
    """确定性伪噪声 0..1（基于 sin 哈希，无全局状态）。"""
    try:
        xf = float(x)
    except Exception:
        return 0.0
    v = math.sin(xf * 12.9898 + 78.233) * 43758.5453
    return v - math.floor(v)


def _rand(seed: float) -> float:
    """对 int(seed) 做纯整数哈希，返回确定性 0..1（不依赖 random 模块）。"""
    try:
        n = int(seed) & 0xFFFFFFFF
    except Exception:
        n = 0
    n = (n ^ 61) ^ (n >> 16)
    n = (n + (n << 3)) & 0xFFFFFFFF
    n ^= (n >> 4)
    n = (n * 0x27D4EB2D) & 0xFFFFFFFF
    n ^= (n >> 15)
    n &= 0xFFFFFFFF
    return n / 4294967295.0


def _tri(x: float) -> float:
    """三角波，周期 1，范围 0..1（整数点为 0，半点为 1）。"""
    try:
        frac = float(x) - math.floor(float(x))
    except Exception:
        return 0.0
    return 1.0 - abs(2.0 * frac - 1.0)


def _saw(x: float) -> float:
    """锯齿波，周期 1，范围 0..1。"""
    try:
        xf = float(x)
    except Exception:
        return 0.0
    return xf - math.floor(xf)


def _sq(x: float) -> float:
    """方波，周期 1，前半周期 1.0、后半 0.0。"""
    try:
        xf = float(x)
    except Exception:
        return 0.0
    frac = xf - math.floor(xf)
    return 1.0 if frac < 0.5 else 0.0


def safe_namespace() -> Dict[str, Any]:
    """返回暴露给用户代码的安全函数 / 常量字典（每次返回新副本，调用方可自由合并）。"""
    return {
        # 三角 / 反三角
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "asin": math.asin, "acos": math.acos, "atan": math.atan,
        "atan2": math.atan2,
        # 幂 / 对数 / 取整
        "sqrt": math.sqrt, "pow": pow, "exp": math.exp, "log": math.log,
        "floor": math.floor, "ceil": math.ceil, "round": round,
        "abs": abs, "min": min, "max": max,
        # 常量
        "pi": math.pi, "tau": math.tau, "e": math.e,
        # 调色 / 插值工具
        "clamp": _clamp, "lerp": _lerp, "smoothstep": _smoothstep,
        "hsv": _hsv, "rgb": _rgb,
        # 确定性随机 / 波形
        "noise": _noise, "rand": _rand,
        "tri": _tri, "saw": _saw, "sq": _sq,
    }


# 受限内建子集：循环 / 序列构造所必需，但不含任何反射 / IO / 类型逃逸入口。
def _guarded_range(*args: Any):
    """range 包装：先静态算出长度，超上限直接拒绝（防 list(range(10**8)) 撑爆内存）。

    range 本身惰性，len() 为 O(1)，故先构造再量长度即可，正常循环不受影响。
    """
    r = range(*args)
    try:
        length = len(r)
    except (OverflowError, TypeError):
        raise RuntimeError("range 超出长度上限")
    if length > _RANGE_MAX_LEN:
        raise RuntimeError("range 超出长度上限")
    return r


_EXEC_BUILTINS: Dict[str, Any] = {
    "range": _guarded_range, "len": len, "enumerate": enumerate,
    "int": int, "float": float, "bool": bool, "str": str,
    "abs": abs, "min": min, "max": max, "round": round, "pow": pow, "sum": sum,
    "zip": zip, "tuple": tuple, "list": list, "dict": dict, "set": set,
    "sorted": sorted, "reversed": reversed, "map": map, "filter": filter,
    "all": all, "any": any,
    "True": True, "False": False, "None": None,
}


def _make_globals() -> Dict[str, Any]:
    """构造受限执行 globals：safe_namespace + 强制的安全 __builtins__。"""
    g: Dict[str, Any] = dict(safe_namespace())
    # 关键：显式设置 __builtins__，否则 eval/exec 会注入真实内建 → 逃逸。
    g["__builtins__"] = dict(_EXEC_BUILTINS)
    return g


def _run_guarded(call: Callable[[], Any]) -> Any:
    """在 sys.settrace 行事件计数器保护下执行 call()。

    步数超过 _MAX_TRACE_STEPS 时，从正在执行的帧内抛出 RuntimeError 自爆——
    这一点即便在不可强杀的 QThread 内也成立（trace 函数 raise 会中断该帧）。

    计数器是本次调用私有（每次 _run_guarded 重置），不会跨多次渲染累加；
    因此一次渲染数千台设备不会误触上限。settrace 是进程级副作用，
    故严格在 finally 中恢复调用前的 trace。

    ⚠ 性能：settrace 对每个被执行的「行事件」回调一次 Python 函数，在大批量渲染
    (上千设备×多帧)里开销极大。因此**仅在代码确实含循环构造**(while/for/推导式)时
    才用本守卫——直线算术(标量表达式、无循环 def)不可能死循环,直接裸跑(见 _build_fn)。
    """
    counter = [0]
    limit = _MAX_TRACE_STEPS

    def _tracer(frame, event, arg):
        if event == "line":
            counter[0] += 1
            if counter[0] > limit:
                raise RuntimeError("执行超时：代码运行步数超过上限")
        return _tracer

    prev = sys.gettrace()
    sys.settrace(_tracer)
    try:
        return call()
    finally:
        sys.settrace(prev)


# 含循环构造的 AST 节点(只有这些才可能无界循环 → 才需要 settrace 步数守卫)。
_LOOP_NODES = (ast.While, ast.For, ast.AsyncFor,
               ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _has_loop_construct(code: str, mode: str) -> bool:
    """代码里是否含循环/推导式。无则直线执行,必然有界 → 渲染热路径可免 settrace。
    解析失败保守返回 True(让守卫兜底)。"""
    try:
        tree = ast.parse((code or "").strip(), mode=mode)
    except SyntaxError:
        return True
    for node in ast.walk(tree):
        if isinstance(node, _LOOP_NODES):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  EffectContext —— def 全式效果的上下文对象（每台设备每帧一个）
# ─────────────────────────────────────────────────────────────────────────────
class EffectContext:
    """传给 def 形式 effect(c) 的上下文。

    可读属性：t(秒) i(0基有序下标) n(设备数) x y(本台坐标,可能为0) dur fps p(参数dict)。
    方法 c.set(ch, val)：记录某通道值（ch 为通道类型别名，val 自动 clamp 到 0..255 int）。
    下标 c['speed'] 等价 params.get('speed')。
    """

    __slots__ = ("t", "i", "n", "x", "y", "dur", "fps", "p", "_out")

    def __init__(self, t: float, i: int, n: int, x: float, y: float,
                 dur: float, fps: float, p: Dict[str, Any]) -> None:
        self.t = t
        self.i = i
        self.n = n
        self.x = x
        self.y = y
        self.dur = dur
        self.fps = fps
        self.p = p if isinstance(p, dict) else {}
        self._out: Dict[str, int] = {}

    def set(self, ch: Any, val: Any) -> None:
        key = ir.normalize_channel(ch) or str(ch)
        self._out[key] = _ci(val)

    def __getitem__(self, key: Any) -> Any:
        return self.p.get(key)

    def get(self, key: Any, default: Any = None) -> Any:
        return self.p.get(key, default)

    def __getattr__(self, name: str) -> Any:
        # #4 一致性：def 里也能用 c.<参数名> 访问 param(与 scalar 的裸名风格对齐)。
        # 仅在常规属性(slots t/i/n/...)查找失败时触发,故不影响内建属性。
        # 名称以 '_' 开头的一律拒绝(防探测内部),其余去 params 找,缺失抛 AttributeError。
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            p = object.__getattribute__(self, "p")
        except AttributeError:
            raise AttributeError(name)
        if name in p:
            return p[name]
        raise AttributeError(f"无此参数: {name}（用 param {name}=值 定义）")


# ─────────────────────────────────────────────────────────────────────────────
#  校验（不执行，仅 parse + 走 AST）
# ─────────────────────────────────────────────────────────────────────────────
def validate_code(code: str, kind: str) -> List[str]:
    """校验用户代码，返回人类可读错误列表（中文）；通过则返回 []。仅解析，不执行。

    kind='scalar'：code 是单个表达式。
    kind='def'   ：code 必须定义恰好接收一个参数、名为 effect 的函数。
    """
    src = (code or "").strip()
    if not src:
        return ["代码为空"]

    if kind == BODY_SCALAR:
        try:
            tree = ast.parse(src, mode="eval")
        except SyntaxError as exc:
            return [f"语法错误: {exc.msg}（行{exc.lineno or 0}）"]
        return _walk_collect(tree)

    if kind == BODY_DEF:
        try:
            tree = ast.parse(src, mode="exec")
        except SyntaxError as exc:
            return [f"语法错误: {exc.msg}（行{exc.lineno or 0}）"]
        errors = _walk_collect(tree)
        # 结构校验：必须存在名为 effect、恰好一个参数的函数定义
        effect_def = None
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef) and stmt.name == "effect":
                effect_def = stmt
                break
        if effect_def is None:
            errors.append("def 形式必须定义一个名为 effect 的函数")
        else:
            a = effect_def.args
            n_pos = len(getattr(a, "posonlyargs", [])) + len(a.args)
            if n_pos != 1 or a.vararg is not None or a.kwarg is not None or a.kwonlyargs:
                errors.append("effect 函数必须恰好接收一个参数（约定为 c）")
        return errors

    return [f"未知的代码类型: {kind!r}"]


# ─────────────────────────────────────────────────────────────────────────────
#  编译为引擎 compute 函数
# ─────────────────────────────────────────────────────────────────────────────
def _build_fn(body: ChannelBody, params: Dict[str, Any],
              coords: Dict[Any, Tuple[float, float]], strict: bool) -> Callable:
    """内部统一构建器。strict=True 时（probe）首个用户错误直接抛出；
    strict=False 时（正式渲染）逐台 catch，单台出错不影响整帧。"""
    params = dict(params or {})
    coords = dict(coords or {})
    kind = body.kind

    errs = validate_code(body.code, kind)
    if errs:
        raise UnsafeExpressionError("; ".join(errs))

    g = _make_globals()

    if kind == BODY_DEF:
        code_obj = compile(body.code, f"<cmd-def L{body.src_line}>", "exec")
        # 顶层 exec(定义函数)极短,直接守卫(防顶层死循环,一次性开销可忽略)
        _run_guarded(lambda: exec(code_obj, g))
        effect = g.get("effect")
        if not callable(effect):
            raise UnsafeExpressionError("未找到可调用的 effect 函数")
        # 是否需要逐帧步数守卫:仅当 def 体含循环/推导式才需要(直线代码必然有界)。
        needs_guard = _has_loop_construct(body.code, "exec")

        def fn_def(ef, t, sorted_fx, fdefs, p, fps):
            n = len(sorted_fx)
            rp = p if isinstance(p, dict) else {}
            dur = rp.get("_dur", params.get("_dur", 0.0))
            merged = dict(params)
            merged.update(rp)

            def _loop():
                for seq_i, (orig_i, fx) in enumerate(sorted_fx):
                    x, y = coords.get(getattr(fx, "id", None), (0.0, 0.0))
                    c = EffectContext(t=t, i=seq_i, n=n, x=x, y=y,
                                      dur=dur, fps=fps, p=merged)
                    try:
                        effect(c)
                    except Exception:
                        if strict:
                            raise
                        continue
                    tgt = ef.values[orig_i]
                    for k, v in c._out.items():
                        tgt[k] = v

            # 含循环→步数守卫(每次调用重置计数);直线→裸跑(免 settrace 热路径开销)
            if needs_guard:
                _run_guarded(_loop)
            else:
                _loop()

        return fn_def

    # ── scalar ────────────────────────────────────────────────────────────
    channel = body.channel
    code_obj = compile((body.code or "").strip(), f"<cmd-scalar L{body.src_line}>", "eval")
    # 标量是单个表达式:仅当含推导式/生成器才可能拖慢 → 才守卫;纯算术裸跑。
    needs_guard = _has_loop_construct(body.code, "eval")

    def fn_scalar(ef, t, sorted_fx, fdefs, p, fps):
        n = len(sorted_fx)
        rp = p if isinstance(p, dict) else {}
        dur = rp.get("_dur", params.get("_dur", 0.0))

        def _loop():
            for seq_i, (orig_i, fx) in enumerate(sorted_fx):
                x, y = coords.get(getattr(fx, "id", None), (0.0, 0.0))
                loc: Dict[str, Any] = {
                    "t": t, "i": seq_i, "n": n, "x": x, "y": y,
                    "dur": dur, "fps": fps,
                }
                loc.update(params)
                try:
                    val = eval(code_obj, g, loc)
                except Exception:
                    if strict:
                        raise
                    continue
                ef.values[orig_i][channel] = _ci(val)

        if needs_guard:
            _run_guarded(_loop)
        else:
            _loop()

    return fn_scalar


def compile_channel_body(body: ChannelBody, params: Dict[str, Any],
                         coords: Dict[Any, Tuple[float, float]]) -> Callable:
    """把一个 ChannelBody 编译为引擎 compute 函数（一次性编译，返回的 fn 逐台快速运行）。

    返回签名：fn(ef, t, sorted_fx, fdefs, p, fps)，与 EffectEngine._run_compute_fn 完全一致。
    填充 ef.values[orig_i][通道]。单台用户代码异常被 catch，不会拖垮整帧。
    """
    return _build_fn(body, params, coords, strict=False)


# ─────────────────────────────────────────────────────────────────────────────
#  预渲染探针（渲染器的闸门）
# ─────────────────────────────────────────────────────────────────────────────
class _ProbeEF:
    __slots__ = ("values",)

    def __init__(self, nf: int) -> None:
        self.values = [{} for _ in range(nf)]


class _ProbeFX:
    __slots__ = ("id",)

    def __init__(self, fid: str) -> None:
        self.id = fid


def probe_compile(body: ChannelBody, params: Dict[str, Any]) -> List[str]:
    """廉价预检：编译 + 在 t=0 的 3 台合成帧上跑一次，把 NameError/TypeError 等
    运行期错误提前暴露成错误字符串。无误返回 []。确定性、快速。"""
    params = dict(params or {})

    errs = validate_code(body.code, body.kind)
    if errs:
        return errs

    # 合成 3 台设备，坐标各异以触发 x/y 相关分支
    coords = {
        "probe_fx_0": (0.0, 0.0),
        "probe_fx_1": (1.0, 0.5),
        "probe_fx_2": (2.0, 1.0),
    }
    try:
        fn = _build_fn(body, params, coords, strict=True)
    except Exception as exc:  # 编译期（含白名单拒绝）
        return [f"编译失败: {exc}"]

    ef = _ProbeEF(3)
    sorted_fx = [
        (0, _ProbeFX("probe_fx_0")),
        (1, _ProbeFX("probe_fx_1")),
        (2, _ProbeFX("probe_fx_2")),
    ]
    probe_p = dict(params)
    probe_p.setdefault("_dur", 0.0)
    try:
        fn(ef, 0.0, sorted_fx, {}, probe_p, 40.0)
    except Exception as exc:
        return [f"运行错误: {type(exc).__name__}: {exc}"]
    return []


__all__ = [
    "UnsafeExpressionError",
    "EffectContext",
    "safe_namespace",
    "validate_code",
    "compile_channel_body",
    "probe_compile",
]
