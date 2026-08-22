"""tests/test_specialists_permissions.py"""
from eve.specialists.permissions import permission_denial


def test_holding_the_single_required_permission_is_allowed():
    assert permission_denial(["home.control"], "home.control") is None


def test_missing_the_single_required_permission_is_denied():
    denial = permission_denial([], "home.control")
    assert denial is not None
    assert "home.control" in denial


def test_any_of_a_list_of_permissions_is_enough():
    assert permission_denial(["mail.read"], ["mail.read", "mail.send"]) is None


def test_holding_none_of_a_list_of_permissions_is_denied():
    denial = permission_denial([], ["mail.read", "mail.send"])
    assert denial is not None
    assert "mail.read" in denial and "mail.send" in denial
