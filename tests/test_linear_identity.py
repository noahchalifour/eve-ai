import pytest

from eve.family import get_family
from eve_linear.identity import resolve_member, resolve_repos


@pytest.fixture(autouse=True)
def roster(tmp_path, monkeypatch):
    path = tmp_path / "family.yaml"
    path.write_text(
        "members:\n"
        "  - sub: 'noah-sub'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_noah'\n"
        "    permissions: ['code.delegate']\n"
        "  - sub: 'kendra-sub'\n"
        "    name: 'Kendra'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_kendra'\n"
        "    permissions: ['health']\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(path))
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    yield
    get_settings.cache_clear()
    get_family.cache_clear()


def test_a_mapped_member_with_the_permission_resolves():
    member, refusal = resolve_member("lin_noah")
    assert refusal is None
    assert member.sub == "noah-sub"


def test_an_unmapped_linear_user_is_refused():
    member, refusal = resolve_member("lin_stranger")
    assert member is None
    assert refusal.kind == "unmapped"
    # The message is shown to a human in Linear, so it must say what to do.
    assert "family.yaml" in refusal.message


def test_a_mapped_member_without_code_delegate_is_refused():
    member, refusal = resolve_member("lin_kendra")
    assert member is None
    assert refusal.kind == "unpermitted"
    assert "Kendra" in refusal.message


def test_an_empty_linear_id_is_refused_as_unmapped():
    member, refusal = resolve_member("")
    assert member is None
    assert refusal.kind == "unmapped"


def test_guidance_naming_an_allowed_repo_resolves_it():
    repos, refusal = resolve_repos(
        "Work in owner/repo for this team.", ["owner/repo", "owner/other"]
    )
    assert refusal is None
    assert repos == ["owner/repo"]


def test_guidance_naming_several_allowed_repos_resolves_all_of_them():
    repos, refusal = resolve_repos(
        "Repos: owner/other and owner/repo.", ["owner/repo", "owner/other"]
    )
    assert refusal is None
    # Allowlist order, so the result is deterministic regardless of how the
    # guidance was written.
    assert repos == ["owner/repo", "owner/other"]


def test_guidance_naming_a_repo_outside_the_allowlist_resolves_nothing():
    # The injection case: guidance is workspace-editable text, and naming a
    # repo there must never be enough to reach it.
    repos, refusal = resolve_repos("Use evil/backdoor.", ["owner/repo"])
    assert repos == []
    assert refusal.kind == "no_repo"


def test_empty_or_missing_guidance_is_refused_rather_than_guessed():
    for guidance in (None, "", "   "):
        repos, refusal = resolve_repos(guidance, ["owner/repo"])
        assert repos == []
        assert refusal.kind == "no_repo"
        assert "which repo" in refusal.message


def test_a_substring_of_an_allowed_repo_does_not_match():
    # "owner/rep" must not match "owner/repo", or a near-miss silently
    # widens the boundary.
    repos, refusal = resolve_repos("Use owner/rep.", ["owner/repo"])
    assert repos == []
    assert refusal.kind == "no_repo"


def test_an_allowed_repo_as_a_substring_of_a_longer_token_does_not_match():
    # The dangerous direction: "owner/repo" must not match inside
    # "owner/repo-evil" or "owner/repository" or "notowner/repo" - an
    # allowed repo name appearing as a substring of a DIFFERENT token must
    # never resolve, or the injection boundary is defeated by construction.
    for guidance in (
        "Use owner/repo-evil for this.",
        "See owner/repository docs.",
        "use notowner/repo now",
    ):
        repos, refusal = resolve_repos(guidance, ["owner/repo"])
        assert repos == []
        assert refusal.kind == "no_repo"
