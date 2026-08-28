import pytest

from eve.tools_authoring.inspect import ALLOWED_IMPORTS, check

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


def test_a_pure_function_passes():
    result = check(PURE)
    assert result.ok and result.problems == []


@pytest.mark.parametrize("module", sorted(ALLOWED_IMPORTS))
def test_every_allowlisted_import_is_accepted(module):
    source = f"import {module}\n\ndef run(arguments):\n    return {{}}\n"
    assert check(source).ok, check(source).problems


@pytest.mark.parametrize(
    "module", ["os", "sys", "subprocess", "socket", "http", "importlib", "shutil"]
)
def test_denied_imports_are_rejected(module):
    source = f"import {module}\n\ndef run(arguments):\n    return {{}}\n"
    result = check(source)
    assert not result.ok
    assert any(module in p for p in result.problems)


def test_urllib_parse_is_allowed_and_urllib_request_is_not():
    """Parsing a URL is computation; fetching one is not."""
    assert check(
        "from urllib.parse import urlparse\n\ndef run(arguments):\n    return {}\n"
    ).ok
    assert not check(
        "from urllib.request import urlopen\n\ndef run(arguments):\n    return {}\n"
    ).ok


@pytest.mark.parametrize(
    "name", ["eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars"]
)
def test_denied_builtins_are_rejected(name):
    source = f"def run(arguments):\n    return {name}('x')\n"
    result = check(source)
    assert not result.ok
    assert any(name in p for p in result.problems)


def test_dunder_attribute_access_is_rejected():
    source = "def run(arguments):\n    return {}.__class__.__bases__\n"
    result = check(source)
    assert not result.ok
    assert any("__class__" in p or "dunder" in p.lower() for p in result.problems)


def test_a_syntax_error_is_a_problem_not_a_crash():
    result = check("def run(:\n")
    assert not result.ok
    assert any("syntax" in p.lower() for p in result.problems)


def test_a_missing_run_function_is_rejected():
    result = check("def other(arguments):\n    return {}\n")
    assert not result.ok
    assert any("run" in p for p in result.problems)


def test_run_must_take_exactly_one_parameter():
    result = check("def run(a, b):\n    return {}\n")
    assert not result.ok


def test_imports_are_reported_for_the_approver():
    source = "import json\nimport math\n\ndef run(arguments):\n    return {}\n"
    assert sorted(check(source).imports) == ["json", "math"]
