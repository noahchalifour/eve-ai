"""`calculate` (ENG-372): exact answers, and nothing that reaches Python."""
from __future__ import annotations

import pytest

from eve.general.calc import CalcError, calculate, evaluate


@pytest.mark.parametrize("expression, expected", [
    ("86.40 * 1.18", "101.952"),
    ("18% of 86.40", "15.552"),
    ("100 - 15%", "99.85"),
    ("2^10", "1024"),
    ("sqrt(16) + abs(-2)", "6"),
    ("1,234 + 1", "1235"),
    ("round(pi, 4)", "3.1416"),
    ("7 // 2", "3"),
])
def test_arithmetic(expression, expected):
    assert evaluate(expression) == expected


@pytest.mark.parametrize("expression, expected", [
    ("5 mi to km", "8.04672 km"),
    ("350F in C", "176.666667 °C"),
    ("72 deg f to c", "22.222222 °C"),
    ("2 cups in ml", "473.176473 ml"),
    ("3 hours in minutes", "180 min"),
    ("10 kg in lb", "22.046226 lb"),
])
def test_unit_conversion(expression, expected):
    assert evaluate(expression) == expected


@pytest.mark.parametrize("expression", [
    "__import__('os').system('id')",
    "(1).__class__",
    "().__class__.__bases__",
    "open('/etc/passwd')",
    "lambda: 1",
    "[x for x in range(10)]",
    "a + 1",
    "'abc' * 3",
    "True + 1",
])
def test_rejects_anything_but_arithmetic(expression):
    with pytest.raises(CalcError):
        evaluate(expression)


def test_bounds_hold():
    with pytest.raises(CalcError, match="exponents"):
        evaluate("2 ** 99999")
    with pytest.raises(CalcError, match="factorial"):
        evaluate("factorial(100000)")
    with pytest.raises(CalcError, match="characters"):
        evaluate("1+" * 300 + "1")
    with pytest.raises(CalcError, match="zero"):
        evaluate("1/0")


def test_incompatible_units_are_an_error_not_a_guess():
    with pytest.raises(CalcError, match="cannot convert"):
        evaluate("5 kg to km")


def test_the_tool_returns_errors_as_text():
    assert calculate.invoke({"expression": "2 + 2"}) == "2 + 2 = 4"
    assert calculate.invoke({"expression": "1/0"}).startswith("error:")
