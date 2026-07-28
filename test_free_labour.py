"""Tests for the incremental contribution-search logic in free-labour.py.

All GitHub API interactions are mocked; no network access or secrets required.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import trio

# ---------------------------------------------------------------------------
# Helpers to import the script (it uses hyphens in its filename).
# ---------------------------------------------------------------------------
import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "free_labour", pathlib.Path(__file__).parent / "free-labour.py"
)
_MOD = importlib.util.module_from_spec(_SPEC)
# Register in sys.modules before exec so that @dataclass works correctly.
sys.modules["free_labour"] = _MOD
_SPEC.loader.exec_module(_MOD)

load_contribution_baseline = _MOD.load_contribution_baseline
search_commits_in_window = _MOD.search_commits_in_window
process_new_commits = _MOD.process_new_commits
contribution_details = _MOD.contribution_details
GitHubProject = _MOD.GitHubProject
RecordedContribution = _MOD.RecordedContribution
_COMMIT_SEARCH_MAX_RESULTS = _MOD._COMMIT_SEARCH_MAX_RESULTS


def _run(coro):
    """Run a coroutine synchronously using trio."""
    return trio.run(lambda: coro)


# ---------------------------------------------------------------------------
# Helpers for building fake GitHub API objects
# ---------------------------------------------------------------------------

def _make_gh(side_effects):
    """Return a mock GitHubAPI whose getitem() returns items from *side_effects*.

    *side_effects* may be:
    - a list (consumed in order, raises StopIteration when exhausted)
    - a callable(url, url_vars, **kwargs) -> dict
    """
    gh = MagicMock()
    if callable(side_effects) and not isinstance(side_effects, list):
        gh.getitem = AsyncMock(side_effect=side_effects)
    else:
        gh.getitem = AsyncMock(side_effect=list(side_effects))
    return gh


def _commit_item(sha, owner, name, fork=False, private=False):
    return {
        "sha": sha,
        "repository": {
            "full_name": f"{owner}/{name}",
            "fork": fork,
            "private": private,
        },
    }


def _search_result(total_count, items):
    return {"total_count": total_count, "items": items}


# ---------------------------------------------------------------------------
# 1. load_contribution_baseline – loading from multiple yearly files
# ---------------------------------------------------------------------------

class TestLoadContributionBaseline(unittest.TestCase):

    def _write_jsonl(self, path: pathlib.Path, entries: list[dict]):
        with path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_loads_newest_file_first(self, tmp_path=None):
        """Picks the newest entry from the newest matching file."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            # 2024 file with one contribution
            self._write_jsonl(td / "2024.jsonl", [
                {"contributions": [{"owner": "old", "name": "repo", "commits": 1}]}
            ])
            # 2025 file with a newer contribution
            self._write_jsonl(td / "2025.jsonl", [
                {"contributions": [{"owner": "new", "name": "repo2", "commits": 5}]}
            ])
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                projects, watermark, shas = load_contribution_baseline()
                self.assertIn(("new", "repo2"), projects)
                self.assertNotIn(("old", "repo"), projects)
            finally:
                os.chdir(old_cwd)

    def test_falls_back_to_older_file(self):
        """Falls back to 2024.jsonl when 2025.jsonl has no GitHub contributions."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            self._write_jsonl(td / "2024.jsonl", [
                {"contributions": [{"owner": "old", "name": "repo", "commits": 2}]}
            ])
            # 2025 file: entries only with RecordedContribution (no owner/name)
            self._write_jsonl(td / "2025.jsonl", [
                {"contributions": [{"repo_name": "other/thing", "contributions_url": "http://x", "commits": 1}]}
            ])
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                projects, _, _ = load_contribution_baseline()
                self.assertIn(("old", "repo"), projects)
            finally:
                os.chdir(old_cwd)

    def test_backward_compat_no_watermark(self):
        """Entries without contribution_refresh_watermark return watermark=None."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            self._write_jsonl(td / "2026.jsonl", [
                {"contributions": [{"owner": "a", "name": "b", "commits": 3}]}
            ])
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                _, watermark, _ = load_contribution_baseline()
                self.assertIsNone(watermark)
            finally:
                os.chdir(old_cwd)

    def test_reads_watermark_and_seen_shas(self):
        """Reads contribution_refresh_watermark and contribution_seen_shas."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            ts = "2026-07-01T00:00:00+00:00"
            self._write_jsonl(td / "2026.jsonl", [
                {
                    "contributions": [{"owner": "a", "name": "b", "commits": 1}],
                    "contribution_refresh_watermark": ts,
                    "contribution_seen_shas": ["abc123", "def456"],
                }
            ])
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                _, watermark, seen_shas = load_contribution_baseline()
                self.assertEqual(watermark, datetime.datetime.fromisoformat(ts))
                self.assertEqual(seen_shas, {"abc123", "def456"})
            finally:
                os.chdir(old_cwd)

    def test_empty_dir_returns_empty(self):
        """Returns empty values when no JSONL files exist."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                projects, watermark, seen_shas = load_contribution_baseline()
                self.assertEqual(projects, {})
                self.assertIsNone(watermark)
                self.assertEqual(seen_shas, set())
            finally:
                os.chdir(old_cwd)

    def test_skips_malformed_json(self):
        """Gracefully skips malformed lines and finds the next valid entry."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            path = td / "2026.jsonl"
            with path.open("w") as f:
                f.write("not json\n")
                f.write(json.dumps({"contributions": [{"owner": "a", "name": "b", "commits": 1}]}) + "\n")
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                projects, _, _ = load_contribution_baseline()
                self.assertIn(("a", "b"), projects)
            finally:
                os.chdir(old_cwd)

    def test_newest_entry_in_file_used(self):
        """Within a single file, the last entry with contributions is used."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            self._write_jsonl(td / "2026.jsonl", [
                {"contributions": [{"owner": "old", "name": "r", "commits": 1}]},
                {"contributions": [{"owner": "new", "name": "r", "commits": 5}]},
            ])
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                projects, _, _ = load_contribution_baseline()
                self.assertEqual(projects[("new", "r")].commits, 5)
                self.assertNotIn(("old", "r"), projects)
            finally:
                os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 2. search_commits_in_window – pagination and window splitting
# ---------------------------------------------------------------------------

class TestSearchCommitsInWindow(unittest.TestCase):

    _SINCE = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    _UNTIL = datetime.datetime(2026, 7, 2, tzinfo=datetime.UTC)

    def test_empty_result(self):
        gh = _make_gh([_search_result(0, [])])
        items = _run(search_commits_in_window(gh, "user", self._SINCE, self._UNTIL))
        self.assertEqual(items, [])

    def test_single_page(self):
        item = _commit_item("abc", "owner", "repo")
        gh = _make_gh([_search_result(1, [item])])
        items = _run(search_commits_in_window(gh, "user", self._SINCE, self._UNTIL))
        self.assertEqual(items, [item])

    def test_multi_page_pagination(self):
        """Fetches subsequent pages when total_count > per_page items returned."""
        page1_items = [_commit_item(f"sha{i}", "o", "r") for i in range(100)]
        page2_items = [_commit_item(f"sha{i+100}", "o", "r") for i in range(50)]
        responses = [
            _search_result(150, page1_items),  # page 1
            _search_result(150, page2_items),  # page 2
        ]
        gh = _make_gh(responses)
        items = _run(search_commits_in_window(gh, "user", self._SINCE, self._UNTIL))
        self.assertEqual(len(items), 150)

    def test_splits_when_over_limit(self):
        """Recursively splits window when total_count > 1000."""
        # First call: total=1500 → triggers split
        # Left half: total=750 → single page with 100 items (representative)
        # Right half: total=750 → single page with 100 items
        calls = []
        async def handler(url, url_vars=None, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                return _search_result(1500, [])
            # Sub-windows each have ≤ 1000
            if len(calls) in (2, 3):
                return _search_result(500, [_commit_item(f"sha{len(calls)}", "o", "r")])
            # Further pagination: empty (all consumed in page 1)
            return _search_result(500, [])

        gh = _make_gh(handler)
        items = _run(search_commits_in_window(gh, "user", self._SINCE, self._UNTIL))
        # We got 1 item from each sub-window
        self.assertEqual(len(items), 2)
        self.assertGreater(len(calls), 1)  # Confirmed split happened

    def test_stops_paginating_on_empty_page(self):
        """Stops paginating early if an intermediate page returns no items."""
        responses = [
            _search_result(200, [_commit_item("sha1", "o", "r")]),  # page 1, 1 item
            _search_result(200, []),                                  # page 2, empty
        ]
        gh = _make_gh(responses)
        items = _run(search_commits_in_window(gh, "user", self._SINCE, self._UNTIL))
        self.assertEqual(len(items), 1)


# ---------------------------------------------------------------------------
# 3. process_new_commits – idempotency, deduplication, fork exclusion
# ---------------------------------------------------------------------------

class TestProcessNewCommits(unittest.TestCase):

    def _make_gh(self, meta_responses=None):
        """Return a mock gh with pre-configured getitem for metadata."""
        gh = MagicMock()
        if meta_responses is None:
            meta_responses = {}

        async def getitem(url, url_vars=None, **kwargs):
            if url_vars:
                key = (url_vars.get("owner", ""), url_vars.get("repo", ""))
                return meta_responses.get(key, {"fork": False, "stargazers_count": 0})
            raise ValueError(f"Unexpected getitem call: {url}")

        gh.getitem = AsyncMock(side_effect=getitem)
        return gh

    # 3a. Idempotency: same SHA in seen_shas is skipped
    def test_idempotency_seen_sha_skipped(self):
        gh = self._make_gh()
        commits = [_commit_item("sha1", "owner", "repo")]
        cached = {("owner", "repo"): GitHubProject("owner", "repo", "user", commits=5)}
        seen_shas = {"sha1"}
        updated, new_seen = _run(process_new_commits(gh, "user", commits, cached, seen_shas))
        # sha1 was in seen_shas, so commit count should NOT increase
        self.assertEqual(updated[("owner", "repo")].commits, 5)

    # 3b. New commit increments existing repo
    def test_increments_existing_repo(self):
        gh = self._make_gh()
        commits = [_commit_item("sha2", "owner", "repo")]
        cached = {("owner", "repo"): GitHubProject("owner", "repo", "user", commits=5)}
        updated, new_seen = _run(process_new_commits(gh, "user", commits, cached, set()))
        self.assertEqual(updated[("owner", "repo")].commits, 6)
        self.assertIn("sha2", new_seen)

    # 3c. New repo discovered
    def test_new_repo_added(self):
        gh = self._make_gh()
        commits = [_commit_item("sha3", "newowner", "newrepo")]
        cached = {}
        updated, _ = _run(process_new_commits(gh, "user", commits, cached, set()))
        self.assertIn(("newowner", "newrepo"), updated)
        self.assertEqual(updated[("newowner", "newrepo")].commits, 1)

    # 3d. Fork is excluded
    def test_fork_excluded(self):
        gh = self._make_gh()
        commits = [_commit_item("sha4", "someone", "fork-repo", fork=True)]
        cached = {}
        updated, _ = _run(process_new_commits(gh, "user", commits, cached, set()))
        self.assertNotIn(("someone", "fork-repo"), updated)

    # 3e. Private repo excluded
    def test_private_excluded(self):
        gh = self._make_gh()
        commits = [_commit_item("sha5", "someone", "private-repo", private=True)]
        cached = {}
        updated, _ = _run(process_new_commits(gh, "user", commits, cached, set()))
        self.assertNotIn(("someone", "private-repo"), updated)

    # 3f. Dedup: prefer cached repo when same SHA appears in multiple repos
    def test_dedup_prefers_cached_repo(self):
        """When a SHA appears in both a cached repo and a new one, cached wins."""
        gh = self._make_gh()
        commits = [
            _commit_item("sha_dup", "canonical", "cpython"),
            _commit_item("sha_dup", "mirror", "cpython-mirror"),
        ]
        cached = {
            ("canonical", "cpython"): GitHubProject("canonical", "cpython", "user", commits=10)
        }
        updated, _ = _run(process_new_commits(gh, "user", commits, cached, set()))
        self.assertEqual(updated[("canonical", "cpython")].commits, 11)
        self.assertNotIn(("mirror", "cpython-mirror"), updated)

    # 3g. Dedup: prefer already-selected repo in the same refresh batch
    def test_dedup_prefers_already_selected(self):
        """When a SHA appears in two unknown repos but one was already selected."""
        gh = self._make_gh(
            meta_responses={
                ("owner", "repoA"): {"fork": False, "stargazers_count": 100},
                ("owner", "repoB"): {"fork": False, "stargazers_count": 200},
            }
        )
        commits = [
            # First SHA: unambiguous → repoA gets selected
            _commit_item("sha_first", "owner", "repoA"),
            # Second SHA: ambiguous between repoA and repoB
            # repoA was selected first → it should win even though repoB has more stars
            _commit_item("sha_ambig", "owner", "repoA"),
            _commit_item("sha_ambig", "owner", "repoB"),
        ]
        updated, _ = _run(process_new_commits(gh, "user", commits, {}, set()))
        # sha_ambig should go to repoA (already-selected), not repoB
        self.assertIn(("owner", "repoA"), updated)
        # repoB should not have been created
        self.assertNotIn(("owner", "repoB"), updated)

    # 3h. Dedup: fetch metadata and pick highest-starred non-fork
    def test_dedup_picks_highest_star_non_fork(self):
        """For an ambiguous SHA with no prior context, picks by stars."""
        gh = self._make_gh(
            meta_responses={
                ("alpha", "repo"): {"fork": False, "stargazers_count": 50},
                ("beta", "repo"): {"fork": False, "stargazers_count": 200},
            }
        )
        commits = [
            _commit_item("sha_amb", "alpha", "repo"),
            _commit_item("sha_amb", "beta", "repo"),
        ]
        updated, _ = _run(process_new_commits(gh, "user", commits, {}, set()))
        # beta has more stars → should be selected
        self.assertIn(("beta", "repo"), updated)
        self.assertNotIn(("alpha", "repo"), updated)

    # 3i. Dedup: full-name tiebreaker when stars are equal
    def test_dedup_tiebreaker_fullname(self):
        """Stable tiebreak by full_name when star count is equal."""
        gh = self._make_gh(
            meta_responses={
                ("zzz", "repo"): {"fork": False, "stargazers_count": 100},
                ("aaa", "repo"): {"fork": False, "stargazers_count": 100},
            }
        )
        commits = [
            _commit_item("sha_tie", "zzz", "repo"),
            _commit_item("sha_tie", "aaa", "repo"),
        ]
        updated, _ = _run(process_new_commits(gh, "user", commits, {}, set()))
        # "aaa/repo" < "zzz/repo" lexicographically → aaa wins
        self.assertIn(("aaa", "repo"), updated)
        self.assertNotIn(("zzz", "repo"), updated)

    # 3j. Formal fork excluded via metadata, mirror (fork=False) wins
    def test_dedup_formal_fork_excluded_via_metadata(self):
        """Canonical repo and formal fork: metadata fork=True removes the fork."""
        gh = self._make_gh(
            meta_responses={
                ("canonical", "repo"): {"fork": False, "stargazers_count": 500},
                ("forked", "repo"): {"fork": True, "stargazers_count": 999},
            }
        )
        commits = [
            _commit_item("sha_fk", "canonical", "repo"),
            _commit_item("sha_fk", "forked", "repo"),
        ]
        updated, _ = _run(process_new_commits(gh, "user", commits, {}, set()))
        self.assertIn(("canonical", "repo"), updated)
        self.assertNotIn(("forked", "repo"), updated)

    # 3k. Two same-SHA commits from same repo don't double-count
    def test_same_repo_same_sha_deduped(self):
        """Duplicate search results for the same SHA+repo only count once."""
        gh = self._make_gh()
        commits = [
            _commit_item("sha_dup", "owner", "repo"),
            _commit_item("sha_dup", "owner", "repo"),
        ]
        cached = {}
        updated, _ = _run(process_new_commits(gh, "user", commits, cached, set()))
        self.assertEqual(updated[("owner", "repo")].commits, 1)


# ---------------------------------------------------------------------------
# 4. contribution_details integration – overrides, failure, CPython ranking
# ---------------------------------------------------------------------------

class TestContributionDetails(unittest.TestCase):

    _BASE_OVERRIDES = {
        "github": {
            "remove": [],
            "repos": [],
            "started": [],
        },
        "contributions": [],
    }

    def _make_details(self):
        return {"github_username": "testuser"}

    def test_overrides_remove_applied(self):
        """Repos in overrides.toml github.remove are excluded from the output."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            # Create a JSONL baseline with "toremove/repo"
            baseline = [{"contributions": [
                {"owner": "toremove", "name": "repo", "commits": 3},
                {"owner": "keep", "name": "repo2", "commits": 1},
            ]}]
            with (td / "2026.jsonl").open("w") as f:
                f.write(json.dumps(baseline[0]) + "\n")
            # Create overrides.toml
            overrides_content = """
[github]
remove = ["toremove/repo"]
started = []

[[github.repos]]
owner = "manual"
name = "override"
commits = 10
"""
            (td / "overrides.toml").write_text(overrides_content)
            # Create TEMPLATE.md stub (needed by generate_readme)
            (td / "TEMPLATE.md").write_text("")

            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
                    details = self._make_details()

                    async def run():
                        async with __import__("httpx").AsyncClient() as client:
                            await contribution_details(details, client)

                    _run(run())

                repo_names = [c.repo_name for c in details["contributions"]]
                self.assertNotIn("toremove/repo", repo_names)
                self.assertIn("keep/repo2", repo_names)
                self.assertIn("manual/override", repo_names)
            finally:
                os.chdir(old_cwd)

    def test_failure_preserves_cached_contributions(self):
        """If incremental search fails, cached contributions and old watermark retained."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            old_watermark = "2026-07-01T00:00:00+00:00"
            baseline = [{
                "contributions": [
                    {"owner": "cached", "name": "repo", "commits": 7}
                ],
                "contribution_refresh_watermark": old_watermark,
                "contribution_seen_shas": [],
            }]
            with (td / "2026.jsonl").open("w") as f:
                f.write(json.dumps(baseline[0]) + "\n")
            (td / "overrides.toml").write_text(
                "[github]\nremove = []\nstarted = []\n"
            )

            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                with patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"}, clear=False):
                    # Patch search_commits_in_window to raise an error
                    with patch.object(_MOD, "search_commits_in_window", side_effect=RuntimeError("API down")):
                        details = self._make_details()

                        async def run():
                            async with __import__("httpx").AsyncClient() as client:
                                await contribution_details(details, client)

                        _run(run())

                # Contributions should be the cached ones
                repo_names = [c.repo_name for c in details["contributions"]]
                self.assertIn("cached/repo", repo_names)
                # Watermark should NOT have been advanced
                self.assertEqual(details["contribution_refresh_watermark"], old_watermark)
            finally:
                os.chdir(old_cwd)

    def test_no_token_uses_cached_contributions(self):
        """When GITHUB_TOKEN is absent, cached contributions are used as-is."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            baseline = [{"contributions": [
                {"owner": "a", "name": "b", "commits": 2}
            ]}]
            with (td / "2026.jsonl").open("w") as f:
                f.write(json.dumps(baseline[0]) + "\n")
            (td / "overrides.toml").write_text(
                "[github]\nremove = []\nstarted = []\n"
            )

            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                # Remove GITHUB_TOKEN from environment
                env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
                with patch.dict(os.environ, env, clear=True):
                    details = self._make_details()

                    async def run():
                        async with __import__("httpx").AsyncClient() as client:
                            await contribution_details(details, client)

                    _run(run())

                repo_names = [c.repo_name for c in details["contributions"]]
                self.assertIn("a/b", repo_names)
            finally:
                os.chdir(old_cwd)

    def test_non_github_contributions_added(self):
        """Non-GitHub contributions from overrides.toml are appended."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            baseline = [{"contributions": [{"owner": "a", "name": "b", "commits": 1}]}]
            with (td / "2026.jsonl").open("w") as f:
                f.write(json.dumps(baseline[0]) + "\n")
            overrides = """
[github]
remove = []
started = []

[[contributions]]
name = "PyPy"
url = "https://example.com/pypy"
commits = ["sha1"]
"""
            (td / "overrides.toml").write_text(overrides)

            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
                    details = self._make_details()

                    async def run():
                        async with __import__("httpx").AsyncClient() as client:
                            await contribution_details(details, client)

                    _run(run())

                repo_names = [c.repo_name for c in details["contributions"]]
                self.assertIn("PyPy", repo_names)
            finally:
                os.chdir(old_cwd)

    def test_first_run_sets_watermark_without_searching(self):
        """First run (no watermark in JSONL) sets watermark to now but does not search."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            baseline = [{"contributions": [{"owner": "a", "name": "b", "commits": 1}]}]
            with (td / "2026.jsonl").open("w") as f:
                f.write(json.dumps(baseline[0]) + "\n")
            (td / "overrides.toml").write_text("[github]\nremove = []\nstarted = []\n")

            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                with patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
                    with patch.object(_MOD, "search_commits_in_window") as mock_search:
                        details = self._make_details()

                        async def run():
                            async with __import__("httpx").AsyncClient() as client:
                                await contribution_details(details, client)

                        _run(run())

                        # search should NOT have been called (no prior watermark)
                        mock_search.assert_not_called()

                # But a new watermark IS set
                self.assertIsNotNone(details["contribution_refresh_watermark"])
            finally:
                os.chdir(old_cwd)

    def test_incremental_search_adds_new_repo(self):
        """Incremental search finds a commit in a new repo and adds it."""
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            old_wm = "2026-07-01T00:00:00+00:00"
            baseline = [{
                "contributions": [{"owner": "existing", "name": "repo", "commits": 5}],
                "contribution_refresh_watermark": old_wm,
                "contribution_seen_shas": [],
            }]
            with (td / "2026.jsonl").open("w") as f:
                f.write(json.dumps(baseline[0]) + "\n")
            (td / "overrides.toml").write_text("[github]\nremove = []\nstarted = []\n")

            new_commit = _commit_item("sha_new", "brandnew", "project")
            search_result = [new_commit]

            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                with patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
                    with patch.object(_MOD, "search_commits_in_window", new=AsyncMock(return_value=search_result)):
                        details = self._make_details()

                        async def run():
                            async with __import__("httpx").AsyncClient() as client:
                                await contribution_details(details, client)

                        _run(run())

                repo_names = [c.repo_name for c in details["contributions"]]
                self.assertIn("brandnew/project", repo_names)
                self.assertIn("existing/repo", repo_names)
                # Watermark should have advanced
                self.assertNotEqual(details["contribution_refresh_watermark"], old_wm)
            finally:
                os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 5. CPython ranking uses GITHUB_TOKEN
# ---------------------------------------------------------------------------

class TestFetchCpythonContributors(unittest.TestCase):

    def test_uses_github_token(self):
        """fetch_cpython_contributors reads GITHUB_TOKEN, not GH_USER_READ_TOKEN."""
        import tempfile, os
        fetch_cpython = _MOD.fetch_cpython_contributors
        details = {"github_username": "testuser"}

        async def run():
            # Fake getitem that records the token used
            calls = []
            class FakeClient:
                async def request(self, method, url, **kwargs):
                    calls.append(("request", url))
                    raise NotImplementedError("fake client")
            # We just verify the function reads the correct env var
            with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
                if "GH_USER_READ_TOKEN" in os.environ:
                    del os.environ["GH_USER_READ_TOKEN"]
                async with __import__("httpx").AsyncClient() as client:
                    with patch.object(_MOD.gidgethub.httpx, "GitHubAPI") as mock_api_cls:
                        mock_gh = MagicMock()
                        mock_gh.getitem = AsyncMock(return_value=[
                            {"login": "someone", "total": 1},
                            {"login": "testuser", "total": 42},
                        ])
                        mock_api_cls.return_value = mock_gh
                        await fetch_cpython(details, client)
            return calls

        _run(run())
        # With no token, ranking is 0 (not 0th from missing GH_USER_READ_TOKEN)
        self.assertEqual(details["cpython_contributor_ranking"], 0)

    def test_ranking_found_with_token(self):
        """With GITHUB_TOKEN set, correctly extracts ranking from contributors list."""
        import tempfile, os
        fetch_cpython = _MOD.fetch_cpython_contributors
        details = {"github_username": "brettcannon"}

        async def run():
            with patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"}, clear=False):
                async with __import__("httpx").AsyncClient() as client:
                    with patch.object(_MOD.gidgethub.httpx, "GitHubAPI") as mock_api_cls:
                        mock_gh = MagicMock()
                        mock_gh.getitem = AsyncMock(return_value=[
                            {"login": "user1"},
                            {"login": "user2"},
                            {"login": "brettcannon"},
                        ])
                        mock_api_cls.return_value = mock_gh
                        await fetch_cpython(details, client)

        _run(run())
        # brettcannon is 3rd in the list
        self.assertEqual(details["cpython_contributor_ranking"], 3)


if __name__ == "__main__":
    unittest.main()
