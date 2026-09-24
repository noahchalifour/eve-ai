"""Clones, worktrees, and pull requests. Git only - no ACP type reaches
this file, and no git command leaves it.

WHY WORKTREES. One clone per repo, one worktree per repo per session. Two
sessions asked to touch the same repository would otherwise fight over one
checkout's HEAD, and serialising them to avoid that would be a bound with
no reason behind it - they need no shared display, unlike the GUI queue.

WHY THE BOX OPENS THE PR, NOT THE AGENT. `gh` is on PATH and the agent
could run it, but then whether Eve gets a URL depends on whether the agent
remembered. Doing it here is deterministic, and `--fill` means the agent's
own commit messages still become the PR body, so nothing it authored is
lost. A repo with no commits gets no PR rather than an empty one.

WHY A FAILED PR IS NOT AN EXCEPTION. `publish` is called once, at the end
of a session that may have taken half an hour across several repos. A
`gh` failure on the second repo must not discard the first repo's result,
so each repo's outcome is a dict and the failure rides in it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path

from eve_computer.acp import review as review_findings
from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

_SLUG_MAX = 40


class GitError(Exception):
    """A git (or gh) invocation exited non-zero."""


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:_SLUG_MAX].rstrip("-")


def _qualified(repo: str) -> str:
    """`repo` -> `owner/repo`, using the configured owner."""
    if "/" in repo:
        return repo
    owner = get_computer_settings().github_owner
    return f"{owner}/{repo}" if owner else repo


def _clone_url(repo: str) -> str:
    # `gh auth login` installs a credential helper, so https needs no token
    # in the URL and none is ever written to disk by this code.
    return f"https://github.com/{_qualified(repo)}.git"


def clone_path(repo: str) -> Path:
    return Path(get_computer_settings().code_dir) / _qualified(repo)


def worktree_path(session_dir: Path, repo: str) -> Path:
    return Path(session_dir) / _qualified(repo).split("/")[-1]


async def _run(*args: str, cwd: Path | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise GitError(f"{' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def ensure_clone(repo: str) -> Path:
    path = clone_path(repo)
    if (path / ".git").exists():
        await _run("git", "fetch", "--prune", "origin", cwd=path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    await _run("git", "clone", _clone_url(repo), str(path))
    return path


async def _base_ref(clone: Path) -> str:
    """The remote default branch, as a ref this repo can diff against."""
    try:
        return await _run("git", "rev-parse", "--abbrev-ref", "origin/HEAD", cwd=clone)
    except GitError:
        # A bare origin created without a symbolic HEAD, or a clone made
        # before one existed. Ask the remote directly rather than guessing
        # between `main` and `master`.
        head = await _run("git", "remote", "show", "origin", cwd=clone)
        for line in head.splitlines():
            if "HEAD branch:" in line:
                return f"origin/{line.split(':', 1)[1].strip()}"
        raise


async def add_worktree(repo: str, session_dir: Path, branch: str) -> Path:
    clone = await ensure_clone(repo)
    base = await _base_ref(clone)
    tree = worktree_path(session_dir, repo)
    tree.parent.mkdir(parents=True, exist_ok=True)
    await _run("git", "worktree", "add", "-b", branch, str(tree), base, cwd=clone)
    return tree


async def add_review_worktree(
    repo: str,
    session_dir: Path,
    pr_number: int,
    base_ref: str,
    since_sha: str | None = None,
) -> dict:
    """A detached checkout of a pull request's head, plus the merge base to
    diff against.

    WHY DETACHED. A review creates no branch and pushes nothing, so there is
    no branch to name and none to leak. `remove_worktrees` then tears it down
    unchanged.

    WHY THE MERGE BASE IS RETURNED RATHER THAN THE BASE REF. The reviewer has
    to diff three-dot (`merge_base...HEAD`). A pull request opened against a
    branch that has since moved would otherwise show that branch's later
    commits as findings, and a reviewer reporting someone else's commits as
    problems in this change is worse than no reviewer.

    `since_sha` is the commit a previous review covered (EVE-32). It comes
    back as `since` only when it is still an ancestor of the new head: after
    a force-push the old commit describes history the branch no longer has,
    and an incremental diff against it would be meaningless, so the review
    falls back to the whole pull request.
    """
    clone = await ensure_clone(repo)
    head_ref = f"refs/pull/{pr_number}/head"
    # A named local ref, so the fetched commit survives a later `git gc` in
    # the clone while the worktree is still using it.
    local_ref = f"refs/eve-review/{pr_number}"
    await _run("git", "fetch", "origin", f"+{head_ref}:{local_ref}", cwd=clone)

    head_sha = await _run("git", "rev-parse", local_ref, cwd=clone)
    merge_base = await _run(
        "git", "merge-base", f"origin/{base_ref.split('/')[-1]}", head_sha, cwd=clone
    )

    tree = worktree_path(session_dir, repo)
    tree.parent.mkdir(parents=True, exist_ok=True)
    await _run("git", "worktree", "add", "--detach", str(tree), head_sha, cwd=clone)

    since = ""
    if since_sha and since_sha != head_sha:
        try:
            await _run("git", "merge-base", "--is-ancestor", since_sha, head_sha, cwd=clone)
            since = since_sha
        except GitError:
            logger.info(
                "%s is not an ancestor of %s#%s's head; reviewing the whole pull request",
                since_sha[:8], repo, pr_number,
            )
    return {"path": tree, "merge_base": merge_base, "head_sha": head_sha, "since": since}


async def add_pr_worktree(repo: str, session_dir: Path, pr_number: int) -> dict:
    """A checkout of a pull request's OWN branch, for addressing feedback on
    it (EVE-31).

    Unlike a review this is not detached: the fixes are pushed back onto the
    same branch so they land on the same pull request. The branch name comes
    from GitHub rather than from the caller, and a pull request from a fork
    is refused, because Eve can only push to a branch in the repository
    itself and a fork is by definition somebody else's.
    """
    clone = await ensure_clone(repo)
    qualified = _qualified(repo)
    described = json.loads(await _run(
        "gh", "api", f"/repos/{qualified}/pulls/{pr_number}",
        "--jq", "{branch: .head.ref, head_repo: .head.repo.full_name}",
        cwd=clone,
    ))
    branch = described.get("branch") or ""
    if not branch or described.get("head_repo") != qualified:
        raise GitError(f"{qualified}#{pr_number} is not a branch in {qualified}")

    await _run(
        "git", "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        cwd=clone,
    )
    tree = worktree_path(session_dir, repo)
    tree.parent.mkdir(parents=True, exist_ok=True)
    # -B: the local branch may survive from the session that opened the pull
    # request (its worktree was removed, its branch was not); reset it to
    # what is actually on the remote, which may include a human's commits.
    await _run(
        "git", "worktree", "add", "-B", branch, str(tree), f"origin/{branch}", cwd=clone
    )
    head_sha = await _run("git", "rev-parse", "HEAD", cwd=tree)
    return {"path": tree, "branch": branch, "head_sha": head_sha}


async def _gh_list(path: str, cwd: Path) -> list[dict]:
    """A paginated GitHub list as one list. `--jq '.[]'` prints one compact
    object per line across every page, which avoids parsing `--paginate`'s
    concatenated arrays."""
    out = await _run("gh", "api", "--paginate", path, "--jq", ".[]", cwd=cwd)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


async def fetch_feedback(
    repo: str, pr_number: int, cwd: Path, trusted_authors: list[str]
) -> list[dict]:
    """Every review, inline comment, and conversation comment on a pull
    request by a trusted author, oldest first (EVE-31).

    The box fetches this rather than the agent so that the ids the agent
    replies against are ones this box has seen, and so that what the agent
    is told to address is filtered to people Eve works for. A stranger's
    comment on a public repository is attacker-influenced input to an agent
    that pushes; it is dropped here rather than left to the agent's judgement.
    """
    qualified = _qualified(repo)
    trusted = {login.lower() for login in trusted_authors if login}
    items: list[dict] = []
    sources = (
        ("review", f"/repos/{qualified}/pulls/{pr_number}/reviews"),
        ("review_comment", f"/repos/{qualified}/pulls/{pr_number}/comments"),
        ("comment", f"/repos/{qualified}/issues/{pr_number}/comments"),
    )
    for kind, path in sources:
        for entry in await _gh_list(path, cwd):
            author = ((entry.get("user") or {}).get("login") or "")
            body = entry.get("body") or ""
            if author.lower() not in trusted or not (body.strip() or kind == "review"):
                continue
            items.append({
                "kind": kind,
                "id": entry.get("id"),
                "author": author,
                "body": body,
                "state": entry.get("state"),
                "path": entry.get("path"),
                "line": entry.get("line"),
                "in_reply_to_id": entry.get("in_reply_to_id"),
                "at": entry.get("submitted_at") or entry.get("created_at") or "",
            })
    items.sort(key=lambda item: item["at"])
    return items


async def push_followup(session_dir: Path, repo: str, branch: str) -> dict:
    """Push an address session's commits onto the pull request's branch.

    Never forced. If someone pushed to the branch while the agent worked,
    the push is rejected and the error rides in the result: overwriting a
    human's commits to land an agent's is the wrong way round.
    """
    tree = worktree_path(session_dir, repo)
    result: dict = {"repo": _qualified(repo), "commits": 0, "branch": branch}
    try:
        count = await _run(
            "git", "rev-list", "--count", f"origin/{branch}..HEAD", cwd=tree
        )
        result["commits"] = int(count)
        result["head_sha"] = await _run("git", "rev-parse", "HEAD", cwd=tree)
        if result["commits"]:
            await _run("git", "push", "origin", f"HEAD:refs/heads/{branch}", cwd=tree)
    except (GitError, FileNotFoundError, ValueError) as exc:
        logger.warning("pushing follow-up to %s %s failed", repo, branch, exc_info=True)
        result["error"] = f"{exc.__class__.__name__}: {exc}"
    return result


async def _post_json(path: str, payload: dict, cwd: Path) -> None:
    """A POST whose body is a file, never an argument: the text is model
    output, and no part of it may become a flag or a shell word."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=str(cwd)) as handle:
        json.dump(payload, handle)
        body_path = handle.name
    try:
        await _run("gh", "api", "--method", "POST", path, "--input", body_path, cwd=cwd)
    finally:
        Path(body_path).unlink(missing_ok=True)


async def post_followup(
    repo: str, pr_number: int, replies: list[dict], summary: str, cwd: Path
) -> dict:
    """Reply in each inline thread, then one conversation comment.

    `repo` and `pr_number` are the caller's, never the agent's, for
    `post_review`'s reason. `replies` has already been validated against the
    ids `fetch_feedback` saw. One failed reply does not stop the rest: each
    is a separate thread, and a reviewer should hear about the ones that did
    post.
    """
    qualified = _qualified(repo)
    posted = 0
    errors: list[str] = []
    for reply in replies:
        try:
            await _post_json(
                f"/repos/{qualified}/pulls/{pr_number}/comments/{reply['comment_id']}/replies",
                {"body": reply["body"]},
                cwd,
            )
            posted += 1
        except (GitError, FileNotFoundError, OSError) as exc:
            errors.append(f"reply to {reply['comment_id']}: {exc}")
    commented = False
    if summary.strip():
        try:
            await _post_json(
                f"/repos/{qualified}/issues/{pr_number}/comments", {"body": summary}, cwd
            )
            commented = True
        except (GitError, FileNotFoundError, OSError) as exc:
            errors.append(f"summary comment: {exc}")
    return {
        "replies": posted,
        "commented": commented,
        "error": "; ".join(errors) or None,
        "url": f"https://github.com/{qualified}/pull/{pr_number}",
    }


async def publish(session_dir: Path, repos: list[str], branch: str) -> list[dict]:
    results: list[dict] = []
    for repo in repos:
        tree = worktree_path(session_dir, repo)
        result: dict = {"repo": _qualified(repo), "commits": 0, "pr_url": None}
        try:
            clone = clone_path(repo)
            base = await _base_ref(clone)
            count = await _run("git", "rev-list", "--count", f"{base}..HEAD", cwd=tree)
            result["commits"] = int(count)
            result["head_sha"] = await _run("git", "rev-parse", "HEAD", cwd=tree)
            if result["commits"] == 0:
                results.append(result)
                continue
            await _run("git", "push", "-u", "origin", branch, cwd=tree)
            try:
                result["pr_url"] = await _run(
                    "gh", "pr", "create", "--fill", "--head", branch, cwd=tree
                ) or None
            except GitError as exc:
                # The agent opened it itself despite the hint (EVE-47). The
                # PR is real; reporting none would tell the member there
                # were no changes and leave the Linear issue in progress.
                if "already exists" not in str(exc):
                    raise
                result["pr_url"] = await _run(
                    "gh", "pr", "view", branch, "--json", "url", "--jq", ".url", cwd=tree
                ) or None
        except (GitError, FileNotFoundError, ValueError) as exc:
            logger.warning("publishing %s failed", repo, exc_info=True)
            result["error"] = f"{exc.__class__.__name__}: {exc}"
        results.append(result)
    return results


async def changed_lines(tree: Path, merge_base: str) -> dict[str, set[int]]:
    """Which lines of which files the pull request actually touches, on the
    right-hand side of the diff. An inline comment on anything else makes
    GitHub reject the entire review."""
    diff = await _run(
        "git", "diff", "--unified=0", f"{merge_base}...HEAD", cwd=tree
    )
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/") :]
            result.setdefault(current, set())
        elif line.startswith("@@") and current is not None:
            # @@ -old,n +new,m @@
            new_part = line.split("+", 1)[1].split("@@")[0].strip()
            start, _, count = new_part.partition(",")
            first = int(start)
            length = int(count) if count else 1
            result[current].update(range(first, first + length))
    return result


async def post_review(
    repo: str,
    pr_number: int,
    review: dict,
    agent: str,
    model: str,
    tree: Path,
    merge_base: str,
) -> dict:
    """One `COMMENT` review on a pull request.

    `repo` and `pr_number` are the CALLER's, taken from the webhook payload.
    They are never read from `review`, so a findings file cannot redirect a
    review onto a repository the agent chose. That is the highest-consequence
    thing the agent could get wrong, and it is not expressible.

    `event` is the literal "COMMENT". Not a parameter and not a setting: a
    wrong REQUEST_CHANGES blocks a human's work and an APPROVE lets a model
    approve its way into main, and neither is reachable if the verb cannot
    vary.
    """
    qualified = _qualified(repo)
    head_sha = await _run("git", "rev-parse", "HEAD", cwd=tree)

    try:
        existing = await _run(
            "gh", "api", "--paginate",
            f"/repos/{qualified}/pulls/{pr_number}/reviews",
            cwd=tree,
        )
        for entry in json.loads(existing or "[]"):
            if entry.get("commit_id") == head_sha:
                logger.info(
                    "a review for %s#%s at %s already exists; not duplicating",
                    qualified, pr_number, head_sha[:8],
                )
                return {"posted": False, "skipped": True, "url": None}
    except (GitError, FileNotFoundError, json.JSONDecodeError):
        # Not fatal. Failing to READ existing reviews must not stop this one
        # from being posted; the worst case is a duplicate, which the caller
        # also guards against with its own row check.
        logger.warning("could not list existing reviews on %s#%s", qualified, pr_number)

    lines = await changed_lines(tree, merge_base)
    comments, demoted = review_findings.split_comments(review, lines)
    payload = {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": review_findings.build_body(review, agent, model, demoted=demoted),
        "comments": comments,
    }

    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, dir=str(tree)
        ) as handle:
            json.dump(payload, handle)
            body_path = handle.name
        try:
            await _run(
                "gh", "api", "--method", "POST",
                f"/repos/{qualified}/pulls/{pr_number}/reviews",
                "--input", body_path,
                cwd=tree,
            )
        finally:
            Path(body_path).unlink(missing_ok=True)
    except (GitError, FileNotFoundError, OSError) as exc:
        logger.warning("posting a review on %s#%s failed", qualified, pr_number,
                       exc_info=True)
        return {
            "posted": False,
            "skipped": False,
            "error": f"{exc.__class__.__name__}: {exc}",
        }

    return {
        "posted": True,
        "skipped": False,
        "url": f"https://github.com/{qualified}/pull/{pr_number}",
        "comments": len(comments),
        "demoted": len(demoted),
    }


async def remove_worktrees(session_dir: Path, repos: list[str]) -> None:
    """Tears down the checkouts, never the branches. A session's work
    survives its worktree - the branch is on the clone and, once pushed, on
    the remote."""
    for repo in repos:
        try:
            await _run(
                "git", "worktree", "remove", "--force",
                str(worktree_path(session_dir, repo)),
                cwd=clone_path(repo),
            )
        except GitError:
            logger.warning("could not remove worktree for %s", repo, exc_info=True)
