from __future__ import annotations

# /// script
# dependencies = [
#   "feedparser",
#   "gidgethub",
#   "httpx",
#   "iso8601",
#   "jinja2",
#   "trio",
# ]
# requires-python = ">=3.13"
# ///
import collections
import dataclasses
import datetime
import http
import operator
import os
import typing

import feedparser
import gidgethub.abc
import gidgethub.httpx
import httpx
import iso8601
import jinja2
import tomllib
import trio


async def fetch_json(url, client):
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


class Contribution(typing.Protocol):
    """The interface expected by the README template for contributions."""

    repo_name: str
    contributions_url: str
    commits: int


@dataclasses.dataclass
class RecordedContribution(Contribution):
    repo_name: str
    contributions_url: str
    commits: int


@dataclasses.dataclass
class GitHubProject(Contribution):
    """Representation of a GitHub project and one's contributions."""

    owner: str
    name: str
    contributor: str = ""
    stars: int = 0
    commits: int = 0
    contributors: int = 0

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


async def contribution_counts(gh: gidgethub.httpx.GitHubAPI, username: str):
    """Get the commit count for repositories.

    All private repositories and forks are dropped.

    """
    with open("queries/commit_counts.graphql", "r", encoding="utf-8") as file:
        query = file.read()
    contributions = {}
    activity_in_the_past = True
    contributions_up_to = None
    contributions_from = None
    while activity_in_the_past:
        query_result = await gh.graphql(
            query, username=username, endDate=contributions_up_to
        )
        data = query_result["user"]["contributionsCollection"]
        activity_in_the_past = data["hasActivityInThePast"]
        contributions_up_to = data["startedAt"]
        contributions_from = data["endedAt"]
        for contribution in data["commitContributionsByRepository"]:
            repo = contribution["repository"]
            # Don't care about forks as those are not contributions to upstream.
            # Don't care about private repositories as that isn't giving back
            # to open source.
            if repo["isFork"] or repo["isPrivate"]:
                continue
            owner = repo["owner"]["login"]
            name = repo["name"]
            project = contributions.setdefault(
                (owner, name), GitHubProject(owner, name, username)
            )
            project.stars = repo["stargazers"]["totalCount"]
            project.commits += contribution["contributions"]["totalCount"]
    return iso8601.parse_date(contributions_from), set(contributions.values())


async def star_count(gh: gidgethub.abc.GitHubAPI, project: GitHubProject):
    """Add the star count to a GitHub project."""
    with open("queries/star_count.graphql", "r", encoding="utf-8") as file:
        query = file.read()
    data = await gh.graphql(query, owner=project.owner, name=project.name)
    project.stars = data["repository"]["stargazers"]["totalCount"]


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


async def contribution_details(details, client):
    """Gather relevant contribution details."""
    username = details["github_username"]
    if not (token := os.environ.get("GITHUB_TOKEN")):
        details.update(
            {
                "contributions": [],
                # 2003-04-18 21:00 PDT
                "start_date": datetime.datetime(2003, 4, 19, 4, tzinfo=datetime.UTC),
            }
        )
        return

    with open("overrides.toml", "r", encoding="utf-8") as file:
        manual_overrides = tomllib.loads(file.read())
    contribution_overrides = [
        RecordedContribution(
            f"{repo['owner']}/{repo['name']}",
            f"https://github.com/{repo['owner']}/{repo['name']}/commits?author=brettcannon",
            repo["commits"],
        )
        for repo in manual_overrides["github"]["repos"]
    ]
    gh = gidgethub.httpx.GitHubAPI(client, "brettcannon/brettcannon", oauth_token=token)
    start_date, projects = await contribution_counts(gh, username)
    for remove in manual_overrides["github"]["remove"]:
        owner, _, name = remove.partition("/")
        projects.remove(GitHubProject(owner, name))
    for remove in manual_overrides["github"]["repos"]:
        try:
            projects.remove(GitHubProject(remove["owner"], remove["name"]))
        except KeyError:
            pass
    contributions_list = list(projects)
    contributions_list.extend(contribution_overrides)
    for project in manual_overrides["contributions"]:
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
            "start_date": start_date,
        }
    )


async def latest_blog_post(details, client):
    """Find the latest blog post's URL and publication date."""
    feed = "https://snarky.ca/rss/"
    rss_xml = await client.get(feed)
    rss_xml.raise_for_status()
    rss_feed = feedparser.parse(rss_xml)
    post = rss_feed.entries[0]
    url = post.link
    date = datetime.datetime(*post.published_parsed[:6])
    details.update({"post_url": url, "post_date": date})


async def fetch_mastodon_follower_count(details, client):
    server = "https://fosstodon.org"
    user_id = "108285802173994961"
    url = f"{server}/api/v1/accounts/{user_id}"
    data = await fetch_json(url, client)
    details["mastodon_follower_count"] = data["followers_count"]


async def fetch_bluesky_follower_count(details, client):
    profile = "snarky.ca"
    url = f"https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={profile}"
    data = await fetch_json(url, client)
    details["bluesky_follower_count"] = data["followersCount"]


async def fetch_cpython_contributors(details, client):
    username = details["github_username"]
    if not (token := os.environ.get("GITHUB_TOKEN")):
        details["cpython_contributor_ranking"] = 0
        return
    gh = gidgethub.httpx.GitHubAPI(client, "brettcannon/brettcannon", oauth_token=token)
    contributors = await gh.getitem("/repos/{owner}/{repo}/contributors", {"owner": "python", "repo": "cpython"}, accept="application/vnd.github+json", extra_headers={"X-GitHub-Api-Version": "2022-11-28"})
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
    details["my_name"] = author_name = "Brett Cannon"
    url = "https://peps.python.org/api/peps.json"
    data = await fetch_json(url, client)
    author_count = collections.defaultdict(int)
    my_peps = []
    for pep in data.values():
        authors = pep["authors"].split(", ")
        for author in authors:
            if author == author_name:
                my_peps.append(pep)
            author_count[author] += 1
    details["pep_count"] = author_count[author_name]

    author_rankings = sorted(author_count, key=author_count.__getitem__, reverse=True)
    details["pep_author_ranking"] = author_rankings.index(author_name) + 1

    pep_details = []
    for pep in my_peps:
        co_authors = [
            name for name in pep["authors"].split(", ") if name != author_name
        ]
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
    details["author_rankings"] = author_rankings


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
    cpython_contributor_years = today.year - start_date.year
    if (today.month, today.day) < (start_date.month, start_date.day):
        cpython_contributor_years -= 1

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


async def main():
    details = {"github_username": "brettcannon"}
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


if __name__ == "__main__":
    trio.run(main)
