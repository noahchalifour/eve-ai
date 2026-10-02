"""`calculate`: exact arithmetic and unit conversion, locally.

No `eval`, ever. Plain arithmetic goes through a whitelisted AST walk; a
conversion ("5 mi to km", "350F in C") goes through pint's own parser, which
is a unit grammar, not Python. Currency is deliberately absent: it needs live
rates, and a confidently wrong exchange rate is worse than none.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from functools import lru_cache

from langchain_core.tools import tool

MAX_CHARS = 500
MAX_EXPONENT = 1000
MAX_MAGNITUDE = 1e300

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor,
    "ceil": math.ceil, "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "radians": math.radians, "degrees": math.degrees, "min": min, "max": max,
    "factorial": math.factorial,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

# "18% of 86.40", "15 percent of 40"
_PERCENT_OF = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+", re.IGNORECASE)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_CONVERSION = re.compile(r"^(?P<value>.+?)\s+(?:to|in|into|as)\s+(?P<unit>[^\d].*)$", re.IGNORECASE)
# Temperature shorthands pint does not read: 350F, 20 °C, 72 deg f.
_TEMPERATURE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:°|deg(?:rees?)?\s*)?\s*([CFK])\b", re.IGNORECASE)
_UNIT_ALIASES = {"c": "degC", "f": "degF", "k": "kelvin", "celsius": "degC",
                 "fahrenheit": "degF", "°c": "degC", "°f": "degF"}


class CalcError(ValueError):
    pass


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalcError("only numbers are allowed")
    if isinstance(value, float) and (math.isnan(value) or abs(value) > MAX_MAGNITUDE):
        raise CalcError("the result is out of range")
    if isinstance(value, int) and value.bit_length() > 4000:
        raise CalcError("the result is out of range")
    return value


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        return _number(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalcError(f"exponents are limited to {MAX_EXPONENT}")
        try:
            return _number(_BINARY[type(node.op)](left, right))
        except ZeroDivisionError as exc:
            raise CalcError("division by zero") from exc
        except OverflowError as exc:
            raise CalcError("the result is out of range") from exc
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _number(_UNARY[type(node.op)](_eval(node.operand)))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
    ):
        args = [_eval(arg) for arg in node.args]
        if node.func.id == "factorial" and (not args or args[0] > 500):
            raise CalcError("factorial is limited to 500")
        try:
            return _number(_FUNCTIONS[node.func.id](*args))
        except (ValueError, TypeError, OverflowError) as exc:
            raise CalcError(f"{node.func.id}: {exc}") from exc
    raise CalcError("only arithmetic and the common math functions are allowed")


def _format(value) -> str:
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.10g}"
    return str(value)


def _arithmetic(expression: str) -> str:
    text = _PERCENT_OF.sub(lambda m: f"({m.group(1)}/100)*", expression)
    text = _PERCENT.sub(lambda m: f"({m.group(1)}/100)", text)
    text = text.replace("^", "**").replace("×", "*").replace("÷", "/")
    # Thousands separators only ("1,234"), never the comma between arguments.
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalcError("that is not an expression I can read") from exc
    return _format(_eval(tree))


@lru_cache(maxsize=1)
def _units():
    import pint

    return pint.UnitRegistry(autoconvert_offset_to_baseunit=True)


def _unit_name(raw: str) -> str:
    cleaned = raw.strip().rstrip(".?")
    return _UNIT_ALIASES.get(cleaned.lower(), cleaned)


def _conversion(value: str, unit: str) -> str:
    registry = _units()
    source = _TEMPERATURE.sub(
        lambda m: f"{m.group(1)} {_UNIT_ALIASES[m.group(2).lower()]}", value.strip()
    )
    try:
        quantity = registry.Quantity(source)
        converted = quantity.to(_unit_name(unit))
    except Exception as exc:
        raise CalcError(f"cannot convert {value.strip()} to {unit.strip()}: {exc}") from exc
    magnitude = converted.magnitude
    if not isinstance(magnitude, (int, float)):
        raise CalcError("that conversion did not produce a number")
    return f"{_format(round(float(magnitude), 6))} {converted.units:~P}".strip()


def evaluate(expression: str) -> str:
    expression = (expression or "").strip()
    if not expression:
        raise CalcError("the expression is empty")
    if len(expression) > MAX_CHARS:
        raise CalcError(f"expressions are limited to {MAX_CHARS} characters")
    if "__" in expression or "lambda" in expression:
        raise CalcError("only arithmetic and the common math functions are allowed")
    match = _CONVERSION.match(expression)
    if match:
        return _conversion(match.group("value"), match.group("unit"))
    return _arithmetic(expression)


@tool
def calculate(expression: str) -> str:
    """Work out exact arithmetic or a unit conversion instead of doing it in
    your head. Examples: "86.40 * 1.18", "18% of 86.40", "sqrt(2) ^ 3",
    "5 mi to km", "350F in C", "2 cups in ml", "3 hours in minutes".
    Not for currency conversion."""
    try:
        return f"{expression.strip()} = {evaluate(expression)}"
    except CalcError as exc:
        return f"error: {exc}"
