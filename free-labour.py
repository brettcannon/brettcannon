from __future__ import annotations

# /// script
# dependencies = [
#   "feedparser",
#   "gidgethub",
#   "httpx",
#   "jinja2",
#   "trio",
# ]
# requires-python = ">=3.13"
# ///
import argparse
import collections
import dataclasses
import datetime
import http
import json
import operator
import os
import pathlib
import sys
import typing
import urllib.parse

import feedparser
import gidgethub.abc
import gidgethub.httpx
import httpx
import jinja2
import tomllib
import trio


# GitHub search API returns at most 1000 results per query.
_COMMIT_SEARCH_MAX_RESULTS = 1000


async def fetch_json(url, client):
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


class Contribution(typing.Protocol):
    """The interface expected by the README template for contributions."""

    repo_name: str
    contributions_url: str
    commits: int
    started: bool


@dataclasses.dataclass
class RecordedContribution(Contribution):
    repo_name: str
    contributions_url: str
    commits: int
    started: bool = False


@dataclasses.dataclass
class GitHubProject(Contribution):
    """Representation of a GitHub project and one's contributions."""

    owner: str
    name: str
    contributor: str = ""
    stars: int = 0
    commits: int = 0
    contributors: int = 0
    started: bool = False

    @property
    def repo_name(self):
        return f"{self.owner}/{self.name}"

    @property
    def url(self):
        return f"https://github.com/{self.owner}/{self.name}"

    @property
    def contributions_url(self):
        return f"{self.url}/commits?author={self.contributor}"

    # Cutting a corner here by leaving 'contributor' out, but it makes finding
    # duplicates in a set in a generic fashion easier.
    def __eq__(self, other):
        return self.owner == other.owner and self.name == other.name

    def __hash__(self):
        return hash((self.owner, self.name))

    def __repr__(self):
        stats = {"👷‍♀️": self.contributors, "⭐️": self.stars, "👩‍💻": self.commits}
        formatted_stats = map(
            lambda item: (item[0], format(item[1], ",")), stats.items()
        )
        stats_str = ", ".join(map("=".join, formatted_stats))
        return f"<{self.owner}/{self.name}: {stats_str}>"


def load_contribution_baseline(
    jsonl_glob: str = "*.jsonl",
) -> tuple[dict[tuple[str, str], GitHubProject], datetime.datetime | None, set[str]]:
    """Load the most recent valid contribution baseline from JSONL log files.

    Scans all files matching *jsonl_glob* (e.g. yearly files like 2025.jsonl,
    2026.jsonl) from newest to oldest and returns the most recent log entry that
    contains GitHub-project contribution data.

    Returns:
        github_projects: mapping from (owner, name) to GitHubProject
        watermark:       datetime of the last successful incremental refresh,
                         or None if no watermark has been stored yet
        seen_shas:       set of commit SHAs already processed in the last window
                         (used to prevent double-counting across runs)
    """
    all_files = sorted(pathlib.Path(".").glob(jsonl_glob))

    for jsonl_file in reversed(all_files):
        try:
            with jsonl_file.open(encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue

        for raw_line in reversed(lines):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            contributions = entry.get("contributions")
            if not contributions:
                continue

            github_projects: dict[tuple[str, str], GitHubProject] = {}
            for contrib in contributions:
                if "owner" not in contrib or "name" not in contrib:
                    # RecordedContribution (non-GitHub); skip – will be re-applied
                    # from overrides.toml at render time.
                    continue
                owner = contrib["owner"]
                name = contrib["name"]
                github_projects[(owner, name)] = GitHubProject(
                    owner=owner,
                    name=name,
                    contributor=contrib.get("contributor", ""),
                    stars=contrib.get("stars", 0),
                    commits=contrib.get("commits", 0),
                    contributors=contrib.get("contributors", 0),
                    started=contrib.get("started", False),
                )

            if not github_projects:
                continue

            # Backward-compatible: older entries lack these fields.
            watermark: datetime.datetime | None = None
            raw_watermark = entry.get("contribution_refresh_watermark")
            if raw_watermark:
                try:
                    watermark = datetime.datetime.fromisoformat(raw_watermark)
                except ValueError:
                    pass

            seen_shas: set[str] = set(entry.get("contribution_seen_shas", []))

            return github_projects, watermark, seen_shas

    return {}, None, set()


async def _commit_search_page(
    gh: gidgethub.abc.GitHubAPI,
    query: str,
    page: int,
    per_page: int = 100,
) -> dict:
    """Fetch one page of GitHub commit-search results."""
    encoded = urllib.parse.quote(query, safe="")
    url = f"/search/commits?q={encoded}&per_page={per_page}&page={page}"
    return await gh.getitem(url, accept="application/vnd.github+json")


async def search_commits_in_window(
    gh: gidgethub.abc.GitHubAPI,
    username: str,
    since: datetime.datetime,
    until: datetime.datetime,
) -> list[dict]:
    """Return all public commits by *username* in the interval [since, until].

    Searches commits reachable from each repository's default branch only.
    Independent mirrors may expose the same SHA; callers must deduplicate by SHA.

    When the result set exceeds the 1 000-item GitHub API limit the window is
    recursively split in half until each sub-window fits within the limit.
    """
    since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_str = until.strftime("%Y-%m-%dT%H:%M:%SZ")
    query = f"author:{username} author-date:{since_str}..{until_str}"

    first_page = await _commit_search_page(gh, query, page=1, per_page=100)
    total_count = first_page.get("total_count", 0)

    if total_count == 0:
        return []

    if total_count > _COMMIT_SEARCH_MAX_RESULTS:
        window = until - since
        if window <= datetime.timedelta(seconds=1):
            # Cannot split further; take the first 1 000 and warn.
            print(
                f"WARNING: date window [{since_str}, {until_str}] has "
                f"{total_count} commits but cannot be split further; "
                f"retrieving first {_COMMIT_SEARCH_MAX_RESULTS} only.",
                file=sys.stderr,
            )
            # Fall through to normal pagination below.
        else:
            mid = since + window / 2
            # Left half:  [since, mid]
            left = await search_commits_in_window(gh, username, since, mid)
            # Right half: [mid + 1 s, until]  (avoid double-counting mid)
            right_since = mid + datetime.timedelta(seconds=1)
            right = await search_commits_in_window(gh, username, right_since, until)
            return left + right

    # Normal pagination: collect up to _COMMIT_SEARCH_MAX_RESULTS items.
    items: list[dict] = list(first_page.get("items", []))
    max_items = min(total_count, _COMMIT_SEARCH_MAX_RESULTS)
    page = 2
    while len(items) < max_items:
        result = await _commit_search_page(gh, query, page=page, per_page=100)
        page_items = result.get("items", [])
        if not page_items:
            break
        items.extend(page_items)
        page += 1

    return items


async def process_new_commits(
    gh: gidgethub.abc.GitHubAPI,
    username: str,
    raw_commits: list[dict],
    cached: dict[tuple[str, str], GitHubProject],
    seen_shas: set[str],
) -> tuple[dict[tuple[str, str], GitHubProject], set[str]]:
    """Apply newly found commit-search results to the cached contribution data.

    Deduplicates commits by SHA using the following preference order:
      1. A repository already present in the historical contribution cache.
      2. A repository already selected for another SHA in this refresh batch.
      3. Fetch metadata for the remaining candidates; prefer a non-fork
         repository with the highest star count, with full_name as a stable
         tiebreaker.  Ambiguous selections are logged to stderr so they can be
         corrected via overrides.toml.

    Returns (updated_cached, new_seen_shas).
    """
    # ------------------------------------------------------------------ #
    # Build SHA → [candidate repos] mapping, filtering formal forks/private
    # ------------------------------------------------------------------ #
    sha_to_candidates: dict[str, list[tuple[str, str]]] = {}
    for item in raw_commits:
        sha = item["sha"]
        if sha in seen_shas:
            continue
        repo_info = item.get("repository", {})
        if repo_info.get("fork", False) or repo_info.get("private", False):
            continue
        full_name = repo_info.get("full_name", "")
        if "/" not in full_name:
            continue
        owner, _, name = full_name.partition("/")
        key = (owner, name)
        cands = sha_to_candidates.setdefault(sha, [])
        if key not in cands:
            cands.append(key)

    # ------------------------------------------------------------------ #
    # First pass: resolve unambiguous SHAs and those matching cached/selected
    # ------------------------------------------------------------------ #
    metadata_cache: dict[tuple[str, str], dict] = {}
    sha_to_repo: dict[str, tuple[str, str]] = {}
    selected_repos: set[tuple[str, str]] = set()
    ambiguous: list[tuple[str, list[tuple[str, str]]]] = []

    for sha, candidates in sha_to_candidates.items():
        if len(candidates) == 1:
            key = candidates[0]
            sha_to_repo[sha] = key
            selected_repos.add(key)
            continue

        # Prefer a repo already present in the historical baseline.
        cached_cands = [c for c in candidates if c in cached]
        if cached_cands:
            best = max(cached_cands, key=lambda k: cached[k].commits)
            sha_to_repo[sha] = best
            selected_repos.add(best)
            continue

        # Prefer a repo already selected for a different SHA in this batch.
        sel_cands = [c for c in candidates if c in selected_repos]
        if len(sel_cands) == 1:
            sha_to_repo[sha] = sel_cands[0]
            continue
        if len(sel_cands) > 1:
            counts = {k: sum(1 for v in sha_to_repo.values() if v == k) for k in sel_cands}
            sha_to_repo[sha] = max(sel_cands, key=lambda k: counts[k])
            continue

        ambiguous.append((sha, candidates))

    # ------------------------------------------------------------------ #
    # Fetch metadata only for repos involved in ambiguous SHAs
    # ------------------------------------------------------------------ #
    repos_needing_meta: set[tuple[str, str]] = {
        cand
        for _, candidates in ambiguous
        for cand in candidates
        if cand not in metadata_cache
    }
    for owner, name in repos_needing_meta:
        try:
            meta = await gh.getitem(
                "/repos/{owner}/{repo}",
                {"owner": owner, "repo": name},
                accept="application/vnd.github+json",
            )
            metadata_cache[(owner, name)] = meta
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARNING: Could not fetch metadata for {owner}/{name}: {exc}; "
                "treating as fork.",
                file=sys.stderr,
            )
            metadata_cache[(owner, name)] = {"fork": True, "stargazers_count": 0}

    # ------------------------------------------------------------------ #
    # Second pass: resolve ambiguous SHAs using metadata
    # ------------------------------------------------------------------ #
    for sha, candidates in ambiguous:
        # A first-pass assignment may now cover one of these candidates.
        sel_cands = [c for c in candidates if c in selected_repos]
        if len(sel_cands) == 1:
            sha_to_repo[sha] = sel_cands[0]
            selected_repos.add(sel_cands[0])
            continue

        # Filter out repositories that GitHub metadata marks as actual forks.
        non_fork_cands = [
            c
            for c in candidates
            if not metadata_cache.get(c, {}).get("fork", False)
        ]
        if not non_fork_cands:
            print(
                f"WARNING: SHA {sha[:12]}: all candidate repositories are forks "
                f"({[f'{o}/{n}' for o, n in candidates]}); skipping.",
                file=sys.stderr,
            )
            continue

        if len(non_fork_cands) == 1:
            sha_to_repo[sha] = non_fork_cands[0]
            selected_repos.add(non_fork_cands[0])
            continue

        # Pick highest-starred non-fork; full_name is the stable tiebreaker.
        def sort_key(k: tuple[str, str]) -> tuple[int, str]:
            meta = metadata_cache.get(k, {})
            return (-meta.get("stargazers_count", 0), f"{k[0]}/{k[1]}")

        best = min(non_fork_cands, key=sort_key)
        sha_to_repo[sha] = best
        selected_repos.add(best)
        print(
            f"WARNING: Ambiguous SHA {sha[:12]}: selected {best[0]}/{best[1]} "
            f"from {[f'{o}/{n}' for o, n in non_fork_cands]} "
            "(resolve via overrides.toml if incorrect).",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------ #
    # Apply assignments to the cached contribution dict
    # ------------------------------------------------------------------ #
    updated = dict(cached)
    new_seen_shas = set(seen_shas)

    for sha, (owner, name) in sha_to_repo.items():
        new_seen_shas.add(sha)
        key = (owner, name)
        if key in updated:
            updated[key] = dataclasses.replace(
                updated[key], commits=updated[key].commits + 1
            )
        else:
            stars = metadata_cache.get(key, {}).get("stargazers_count", 0)
            updated[key] = GitHubProject(
                owner=owner,
                name=name,
                contributor=username,
                stars=stars,
                commits=1,
            )

    return updated, new_seen_shas


async def contribution_details(details, client):
    """Gather relevant contribution details via incremental public commit search.

    Loads the most recent contribution baseline from JSONL log files, then
    searches GitHub's public commit-search API for commits authored by the user
    since the last successful refresh watermark.  Applies overrides.toml on top
    of the refreshed data.

    Uses GITHUB_TOKEN (the workflow-provided token) for all GitHub API reads.
    No organisation-scoped SAML-protected token is required.
    """
    username = details["github_username"]
    token = os.environ.get("GITHUB_TOKEN", "")

    with open("overrides.toml", "r", encoding="utf-8") as file:
        manual_overrides = tomllib.loads(file.read())

    # Load the most recent cached contribution baseline across all yearly JSONL files.
    cached_projects, watermark, seen_shas = load_contribution_baseline()

    new_watermark = watermark
    new_seen_shas = seen_shas

    if not token:
        print(
            "WARNING: GITHUB_TOKEN is not set; using cached contributions only.",
            file=sys.stderr,
        )
    elif watermark is None:
        # First run after switching to the incremental approach: the cached
        # baseline is already correct from previous GraphQL-based runs.  Set
        # the watermark to now so the *next* daily run performs an incremental
        # search from today onward.
        new_watermark = datetime.datetime.now(tz=datetime.UTC)
    else:
        gh = gidgethub.httpx.GitHubAPI(
            client, f"{username}/{username}", oauth_token=token
        )
        try:
            now = datetime.datetime.now(tz=datetime.UTC)
            raw_commits = await search_commits_in_window(gh, username, watermark, now)
            cached_projects, new_seen_shas = await process_new_commits(
                gh, username, raw_commits, cached_projects, seen_shas
            )
            new_watermark = now
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARNING: Incremental contribution search failed: {exc}\n"
                "Retaining cached contributions and prior watermark.",
                file=sys.stderr,
            )
            # new_watermark and new_seen_shas stay unchanged (no progress made).

    # ------------------------------------------------------------------ #
    # Apply overrides.toml
    # ------------------------------------------------------------------ #
    github_overrides = manual_overrides.get("github", {})

    # 1. Remove explicitly excluded repositories.
    for remove in github_overrides.get("remove", []):
        owner, _, name = remove.partition("/")
        cached_projects.pop((owner, name), None)

    # 2. Replace/augment with manually corrected entries from github.repos.
    contribution_overrides = []
    for repo in github_overrides.get("repos", []):
        owner, name = repo["owner"], repo["name"]
        cached_projects.pop((owner, name), None)
        contribution_overrides.append(
            RecordedContribution(
                f"{owner}/{name}",
                f"https://github.com/{owner}/{name}/commits?author={username}",
                repo["commits"],
            )
        )

    # 3. Mark "started by me" projects.
    started_orgs = {
        proj.partition("/")[0] for proj in github_overrides.get("started", [])
    }
    started_orgs.add(username)

    contributions_list = list(cached_projects.values())
    for project in contributions_list:
        if project.owner in started_orgs:
            project.started = True

    contributions_list.extend(contribution_overrides)

    # 4. Add non-GitHub contributions.
    for project in manual_overrides.get("contributions", []):
        name = project["name"]
        url = project["url"]
        if "commit_count" in project:
            commits = project["commit_count"]
        else:
            commits = len(project["commits"])
        contributions_list.append(RecordedContribution(name, url, commits))

    details.update(
        {
            "contributions": contributions_list,
            "contribution_refresh_watermark": (
                new_watermark.isoformat() if new_watermark is not None else None
            ),
            # Store the SHAs processed in the last window for idempotency.
            "contribution_seen_shas": sorted(new_seen_shas),
        }
    )


async def contributors(gh: gidgethub.abc.GitHubAPI, project: GitHubProject):
    """Get the contributors list for a project."""
    # Sometimes GitHub returns a 202/Accepted response when requesting the
    # contributors. But if you give it enough time it will eventually return
    # a 200/OK.
    tries = 60
    sleep_for = 10
    while tries:
        try:
            return await gh.getitem(
                # None of my projects are popular enough to have over 100 contributors,
                # so just hard-code the number to keep it simple and avoid going over
                # quota limits.
                "/repos/{owner}/{repo}/stats/contributors?anon=0&per_page=100&page=1",
                {"owner": project.owner, "repo": project.name},
                accept="application/vnd.github.v3+json",
            )
        except gidgethub.HTTPException as exc:
            if exc.status_code != http.HTTPStatus.ACCEPTED or not tries:
                raise
            else:
                tries -= 1
                await trio.sleep(sleep_for)
                continue
    else:
        raise RuntimeError(
            f"{project.repo_name} never stopped returning ACCEPTED after {tries * sleep_for} seconds"
        )


async def contributor_count(gh: gidgethub.abc.GitHubAPI, project: GitHubProject):
    """Add the contributor count to the 'project' statistics."""
    contributors_list = await contributors(gh, project)
    contributor_names = {
        contributor["author"]["login"] for contributor in contributors_list
    }
    project.contributors = len(contributor_names - {"actions-user"})


async def my_contributions(
    gh: gidgethub.abc.GitHubAPI, project: GitHubProject, username: str
):
    contributors_list = await contributors(gh, project)
    for contributor in contributors_list:
        if contributor["author"]["login"] != username:
            continue
        else:
            project.commits = int(contributor["total"])
            break
    else:
        raise ValueError(
            f"{username!r} not found to be a contributor to {project.repo_name} "
            f"among {len(contributors_list)} contributors"
        )


def gh_overrides_repos(
    repo_names: list[str], username: str
) -> frozenset[GitHubProject]:
    repos = set()
    for name in repo_names:
        repos.add(GitHubProject(*name.split("/", 2), username))
    return frozenset(repos)


async def latest_blog_post(details, client):
    """Find the latest blog post's URL and publication date."""
    feed = details["feed"]
    rss_xml = await client.get(feed)
    rss_xml.raise_for_status()
    rss_feed = feedparser.parse(rss_xml)
    post = rss_feed.entries[0]
    url = post.link
    date = datetime.datetime(*post.published_parsed[:6])
    details.update({"post_url": url, "post_date": date})


async def fetch_mastodon_follower_count(details, client):
    server = details["mastodon_server"]
    # https://INSTANCE/api/v1/accounts/lookup?acct=USERNAME
    user_id = details["mastodon_id"]
    url = f"{server}/api/v1/accounts/{user_id}"
    data = await fetch_json(url, client)
    details["mastodon_follower_count"] = data["followers_count"]


async def fetch_bluesky_follower_count(details, client):
    profile = details["bluesky"]
    url = f"https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={profile}"
    data = await fetch_json(url, client)
    details["bluesky_follower_count"] = data["followersCount"]


async def fetch_cpython_contributors(details, client):
    username = details["github_username"]
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        details["cpython_contributor_ranking"] = 0
        return
    gh = gidgethub.httpx.GitHubAPI(client, f"{username}/{username}", oauth_token=token)
    contributors = await gh.getitem(
        "/repos/{owner}/{repo}/contributors",
        {"owner": "python", "repo": "cpython"},
        accept="application/vnd.github+json",
        extra_headers={"X-GitHub-Api-Version": "2022-11-28"},
    )
    for ranking, contributor in enumerate(contributors, start=1):
        if contributor["login"] == username:
            details["cpython_contributor_ranking"] = ranking
            break
    else:
        details["cpython_contributor_ranking"] = 0


@dataclasses.dataclass
class PEP:
    number: int
    title: str
    status: str
    co_authors: list[str]


async def pep_details(details, client):
    author_name = details["my_name"]
    url = "https://peps.python.org/api/peps.json"
    data = await fetch_json(url, client)
    author_count = collections.defaultdict(int)
    my_peps = []
    for pep in data.values():
        for author in pep["author_names"]:
            if author == author_name:
                my_peps.append(pep)
            author_count[author] += 1
    details["pep_count"] = author_count[author_name]
    details["total_pep_count"] = len(data)

    absolute_author_rankings = sorted(
        ((count, author) for author, count in author_count.items()), reverse=True
    )

    author_rankings_iter = iter(absolute_author_rankings)
    adjusted_rank = 1
    current_count, author = next(author_rankings_iter)
    adjusted_author_rankings = [(adjusted_rank, author)]
    for absolute_rank, (count, author) in enumerate(author_rankings_iter, start=2):
        if count < current_count:
            adjusted_rank = absolute_rank
            current_count = count
        if author == author_name:
            details["pep_author_ranking"] = adjusted_rank
        adjusted_author_rankings.append((adjusted_rank, author))

    pep_details = []
    for pep in my_peps:
        co_authors = [name for name in pep["author_names"] if name != author_name]
        pep_details.append(
            PEP(
                pep["number"],
                pep["title"],
                pep["status"],
                co_authors,
            )
        )
    pep_details.sort(
        key=lambda pep_data: datetime.datetime.strptime(
            data[str(pep_data.number)]["created"], "%d-%b-%Y"
        ).date()
    )

    details["pep_details"] = pep_details
    details["author_count"] = author_count
    details["author_rankings"] = adjusted_author_rankings


def nth(number):
    """Add the appropriate suffix to a ranking."""
    # Not "th"
    not_th = {1: "st", 2: "nd", 3: "rd"}
    # Exceptions
    if number % 100 in {11, 12, 13}:
        ending = "th"
    else:
        ending = not_th.get(number % 10, "th")
    return f"{number:,}{ending}"


def generate_readme(post_date, contributions, start_date, **details):
    """Create the README from TEMPLATE.md."""
    status_emojis = {
        "Draft": "✍",
        "Provisional": "🚧",
        "Accepted": "👍",
        "Final": "✅",
        "Active": "🏃",
        "Rejected": "❌",
        "Withdrawn": "🤦",
        "Deferred": "✋",
        "Superseded": "🪜",
    }
    sorted_contributions = sorted(
        contributions, key=operator.attrgetter("commits"), reverse=True
    )

    today = datetime.date.today()
    # Calculate years since contribution start date using Julian days for more precision.

    # Get the day of year (Julian day) for both dates.
    today_julian = today.timetuple().tm_yday
    start_julian = start_date.timetuple().tm_yday

    # Calculate the difference in years.
    year_diff = today.year - start_date.year

    # Adjust the year difference if we haven't reached the anniversary yet this year.
    if today_julian < start_julian:
        year_diff -= 1

    cpython_contributor_years = year_diff

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(["."]))
    env.filters["status_emoji"] = status_emojis.__getitem__
    env.filters["nth"] = nth
    template = env.get_template("TEMPLATE.md")
    return template.render(
        # New data
        today=today.isoformat(),
        cpython_contributor_years=cpython_contributor_years,
        # Changed data
        post_date=post_date.date(),
        contributions=sorted_contributions,
        # Original data
        **details,
    )


def json_serializer(obj):
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    else:
        raise TypeError(f"Type {type(obj)} not serializable")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-u", "--username", default="brettcannon", help="GitHub username"
    )
    parser.add_argument(
        "-f", "--feed", default="https://snarky.ca/rss/", help="Blog feed"
    )
    parser.add_argument("-n", "--name", default="Brett Cannon", help="PEP author name")
    parser.add_argument(
        "--mastodon-server", default="https://mastodon.social", help="Mastodon instance"
    )
    parser.add_argument(
        "-m",
        "--mastodon-id",
        default="114633944987767035",
        help="Mastodon user ID (https://INSTANCE/api/v1/accounts/lookup?acct=USERNAME)",
    )
    parser.add_argument("-b", "--bluesky", default="snarky.ca", help="Bluesky profile")
    parser.add_argument(
        "--log", type=pathlib.Path, help="Log of data, written as JSONL"
    )
    args = parser.parse_args()

    details = {
        "github_username": args.username,
        "my_name": args.name,
        "feed": args.feed,
        "mastodon_server": args.mastodon_server,
        "mastodon_id": args.mastodon_id,
        "bluesky": args.bluesky,
        # First CPython contribution: 2003-04-18 21:00 PDT
        # Hard-coding as it tends to drift based on GitHub API responses.
        "start_date": datetime.datetime(2003, 4, 19, 4, 00, tzinfo=datetime.UTC),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        async with trio.open_nursery() as nursery:
            for func in (
                contribution_details,
                latest_blog_post,
                fetch_mastodon_follower_count,
                fetch_bluesky_follower_count,
                fetch_cpython_contributors,
                pep_details,
            ):
                nursery.start_soon(func, details, client)

    print(generate_readme(**details))

    if args.log:
        log_path = args.log
        with log_path.open("a", encoding="utf-8", newline="\n") as file:
            json.dump(details, file, default=json_serializer)
            file.write("\n")


if __name__ == "__main__":
    trio.run(main)
