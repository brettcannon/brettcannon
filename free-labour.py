from __future__ import annotations

import dataclasses
import datetime
import http
import math
import operator

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import typing

import feedparser
import gidgethub.abc
import gidgethub.httpx
import httpx
import iso8601
import jinja2
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
    with open("commit_counts.graphql", "r", encoding="utf-8") as file:
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


def separate_creations_and_contributions(
    username, projects
) -> tuple[set[GitHubProject], set[Contribution]]:
    """Separate repositories based on which ones are owned by 'username'."""
    creations = set()
    contributions = set()
    for project in projects:
        if project.owner == username:
            category = creations
        else:
            category = contributions
        category.add(project)
    return creations, contributions


async def star_count(gh: gidgethub.abc.GitHubAPI, project: GitHubProject):
    """Add the star count to a GitHub project."""
    with open("star_count.graphql", "r", encoding="utf-8") as file:
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
                # so just hard-code the number to keep it simple.
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
    # None of my projects are popular enough to have over 100 contributors,
    # so just hard-code the number to keep it simple.
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
            f"{username!r} not found to be a contributor to {project.repo_name}"
        )


def gh_overrides_repos(
    repo_names: list[str], username: str
) -> frozenset[GitHubProject]:
    repos = set()
    for name in repo_names:
        repos.add(GitHubProject(*name.split("/", 2), username))
    return frozenset(repos)


async def contribution_details(details, client, token, username):
    """Gather relevant contribution details."""
    with open("overrides.toml", "r", encoding="utf-8") as file:
        manual_overrides = tomllib.loads(file.read())
    creation_overrides = gh_overrides_repos(
        manual_overrides["github"]["created"], username
    )
    contribution_overrides = gh_overrides_repos(
        manual_overrides["github"]["contributed"], username
    )
    gh = gidgethub.httpx.GitHubAPI(client, "brettcannon/brettcannon", oauth_token=token)
    start_date, projects = await contribution_counts(gh, username)
    for remove in manual_overrides["github"]["remove"]:
        owner, _, name = remove.partition("/")
        projects.remove(GitHubProject(owner, name))
    creations, contributions = separate_creations_and_contributions(username, projects)
    for creation in creation_overrides:
        try:
            contributions.remove(creation)
        except KeyError:
            pass
    async with trio.open_nursery() as nursery:
        for project in creation_overrides:
            nursery.start_soon(star_count, gh, project)
        for project in contribution_overrides:
            nursery.start_soon(my_contributions, gh, project, username)
        for project in creations:
            nursery.start_soon(contributor_count, gh, project)

    impactful_creations = {
        creation for creation in creations if creation.contributors > 1
    }
    impactful_creations = frozenset(impactful_creations | creation_overrides)
    contributions |= contribution_overrides
    contributions_list = list(contributions)
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
            "creations": impactful_creations,
            "contributions": contributions_list,
            "start_date": start_date,
        }
    )


async def latest_blog_post(details, client, feed):
    """Find the latest blog post's URL and publication date."""
    rss_xml = await client.get(feed)
    rss_xml.raise_for_status()
    rss_feed = feedparser.parse(rss_xml)
    post = rss_feed.entries[0]
    url = post.link
    date = datetime.datetime(*post.published_parsed[:6])
    details.update({"post_url": url, "post_date": date})


async def fetch_mastodon_follower_count(details, client, server, user_id):
    url = f"{server}/api/v1/accounts/{user_id}"
    data = await fetch_json(url, client)
    details["mastodon_follower_count"] = data["followers_count"]


async def fetch_bluesky_follower_count(details, client):
    url = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=snarky.ca"
    data = await fetch_json(url, client)
    details["bluesky_follower_count"] = data["followersCount"]


async def pep_details(details, client):
    url = "https://peps.python.org/api/peps.json"
    data = await fetch_json(url, client)
    author_count = {}
    for pep in data.values():
        authors = pep["authors"].split(", ")
        for author in authors:
            author_count[author] = author_count.get(author, 0) + 1
    details["pep_count"] = author_count["Brett Cannon"]

    author_rankings = sorted(author_count, key=author_count.__getitem__, reverse=True)

    ranking = author_rankings.index("Brett Cannon") + 1

    not_th = {1: "st", 2: "nd", 3: "rd"}
    # Exceptions
    if ranking in {11, 12, 13}:
        ending = "th"
    else:
        ending = not_th.get(ranking % 10, "th")
    details["pep_author_ranking"] = f"{ranking}{ending}"


def generate_readme(
    creations: typing.Iterable[GitHubProject],
    contributions: typing.Iterable[Contribution],
    start_date: datetime.datetime,
    username: str,
    post_url: str,
    post_date: datetime.datetime,
    # twitter_follower_count: int,
    mastodon_follower_count: int,
    bluesky_follower_count: int,
    pep_count: int,
    pep_author_ranking: str,
):
    """Create the README from TEMPLATE.md."""
    with open("TEMPLATE.md", "r", encoding="utf-8") as file:
        template = jinja2.Template(file.read())
    sorted_creations = sorted(creations, key=operator.attrgetter("stars"), reverse=True)
    sorted_contributions = sorted(
        contributions, key=operator.attrgetter("commits"), reverse=True
    )
    today = datetime.date.today()
    years_contributing = today.year - start_date.year
    return template.render(
        post_url=post_url,
        post_date=post_date.date().isoformat(),
        creations=sorted_creations,
        contributions=sorted_contributions,
        years_contributing=years_contributing,
        username=username,
        today=today.isoformat(),
        sqrt=math.isqrt,
        # twitter_follower_count=format(twitter_follower_count, ","),
        mastodon_follower_count=format(mastodon_follower_count, ","),
        bluesky_follower_count=format(bluesky_follower_count, ","),
        pep_count=pep_count,
        pep_author_ranking=pep_author_ranking,
    )


async def main(
    token: str = "",
    username: str = "",
    feed: str = "",
    mastodon_server: str = "",
    mastodon_account_id: str = "",
):
    details = {
        "post_url": "",
        "post_date": datetime.datetime(1, 1, 1),
        "mastodon_follower_count": -1,
    }
    async with httpx.AsyncClient() as client:
        async with trio.open_nursery() as nursery:
            if token and username:
                nursery.start_soon(
                    contribution_details, details, client, token, username
                )
            if feed:
                nursery.start_soon(latest_blog_post, details, client, feed)
            if mastodon_server and mastodon_account_id:
                nursery.start_soon(
                    fetch_mastodon_follower_count,
                    details,
                    client,
                    mastodon_server,
                    mastodon_account_id,
                )
            nursery.start_soon(fetch_bluesky_follower_count, details, client)
            nursery.start_soon(pep_details, details, client)

    print(generate_readme(username=username, **details))


if __name__ == "__main__":
    import fire

    def cli(
        token: str,
        username: str,
        feed: str,
        mastodon_server: str,
        mastodon_account_id: str,
    ):
        """Provide a CLI for the script for use by Fire."""
        trio.run(main, token, username, feed, mastodon_server, mastodon_account_id)

    fire.Fire(cli)
