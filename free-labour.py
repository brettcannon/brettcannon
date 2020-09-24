from __future__ import annotations

import abc
import dataclasses
import datetime
import math
import operator
import typing

import gidgethub.httpx
import gidgethub.abc
import httpx
import iso8601
import jinja2
import tomlkit
import trio


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
        stats_str = ", ".join("=".join(formatted_stats))
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
    # None of my projects are popular enough to have over 100 contributors,
    # so just hard-code the number to keep it simple.
    return await gh.getitem(
        "/repos/{owner}/{repo}/stats/contributors?anon=0&per_page=100&page=1",
        {"owner": project.owner, "repo": project.name},
        accept="application/vnd.github.v3+json",
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


def generate_readme(
    creations: typing.Iterable[GitHubProject],
    contributions: typing.Iterable[Contribution],
    start_date: datetime.datetime,
    username: str,
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
        creations=sorted_creations,
        contributions=sorted_contributions,
        years_contributing=years_contributing,
        username=username,
        today=today.isoformat(),
        sqrt=math.isqrt,
    )


def gh_overrides_repos(
    repo_names: list[str], username: str
) -> frozenset[GitHubProject]:
    repos = set()
    for name in repo_names:
        repos.add(GitHubProject(*name.split("/", 2), username))
    return frozenset(repos)


async def main(token: str, username: str):
    with open("overrides.toml", "r", encoding="utf-8") as file:
        manual_overrides = tomlkit.loads(file.read())
    creation_overrides = gh_overrides_repos(
        manual_overrides["github"]["created"], username
    )
    contribution_overrides = gh_overrides_repos(
        manual_overrides["github"]["contributed"], username
    )
    async with httpx.AsyncClient() as client:
        gh = gidgethub.httpx.GitHubAPI(
            client, "brettcannon/brettcannon", oauth_token=token
        )
        start_date, projects = await contribution_counts(gh, username)
        for remove in manual_overrides["github"]["remove"]:
            owner, _, name = remove.partition("/")
            projects.remove(GitHubProject(owner, name))
        creations, contributions = separate_creations_and_contributions(
            username, projects
        )
        for creation in creation_overrides:
            try:
                contributions.remove(creation)
            except KeyError:
                pass
        try:
            await contributors(gh, next(iter(contribution_overrides)))
        except gidgethub.GitHubException:
            # For some annoying reason, the /repos/{owner}/{repo}/stats/contributors
            # endpoint needs a warm-up call, otherwise it will throw an exception
            # over the 'Accepted' header.
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
    print(
        generate_readme(impactful_creations, contributions_list, start_date, username)
    )


if __name__ == "__main__":
    import fire

    def cli(token: str, username: str):
        """Provide a CLI for the script for use by Fire."""
        trio.run(main, token, username)

    fire.Fire(cli)
