from __future__ import annotations

import dataclasses
import datetime
import math
import operator

import gidgethub.httpx
import gidgethub.abc
import httpx
import iso8601
import jinja2
import trio


@dataclasses.dataclass
class Project:

    """Representation of a GitHub project."""

    owner: str
    name: str
    url: str
    stars: int = 0
    commits: int = 0
    contributors: int = 0

    @property
    def name_with_owner(self):
        return f"{self.owner}/{self.name}"

    @property
    def sqrt_commits(self):
        return math.isqrt(self.commits)

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
            url = repo["url"]
            project = contributions.setdefault((owner, name), Project(owner, name, url))
            project.stars = repo["stargazers"]["totalCount"]
            project.commits += contribution["contributions"]["totalCount"]
    return iso8601.parse_date(contributions_from), frozenset(contributions.values())


def separate_creations_and_contributions(username, projects):
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


async def contributor_count(gh: gidgethub.abc.GitHubAPI, project: Project):
    """Add the contributor count to the 'project' statistics."""
    contributors = await gh.getitem(
        "/repos/{owner}/{repo}/stats/contributors",
        {"owner": project.owner, "repo": project.name},
        accept="application/vnd.github.v3+json",
    )
    project.contributors = len(contributors)


def generate_readme(creations, contributions, start_date: datetime.datetime, username):
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
    )


async def main(token: str, username: str):
    async with httpx.AsyncClient() as client:
        gh = gidgethub.httpx.GitHubAPI(
            client, "brettcannon/brettcannon", oauth_token=token
        )
        start_date, projects = await contribution_counts(gh, username)
        # XXX Handle special cases (e.g. microsoft/vscode-python; fake-cpython/cpython; which-film/which-film.info, DinoV/Pyjion)
        # XXX Can use REST API call for microsoft/vscode-python

    creations, contributions = separate_creations_and_contributions(username, projects)
    async with trio.open_nursery() as nursery:
        for project in creations:
            nursery.start_soon(contributor_count, gh, project)
    impactful_creations = frozenset(
        creation for creation in creations if creation.contributors > 1
    )

    # XXX Get contributions that are manually recorded
    # XXX Make sure GH Actions has SSO taken care of for Microsoft repositories
    print(generate_readme(impactful_creations, contributions, start_date, username))


if __name__ == "__main__":
    import sys

    # XXX https://github.com/google/python-fire, https://click.palletsprojects.com, http://docopt.org/
    trio.run(main, sys.argv[2], sys.argv[1])
