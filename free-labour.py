from __future__ import annotations

import dataclasses
import datetime

import gidgethub.httpx
import httpx
import iso8601
import trio


@dataclasses.dataclass
class Project:

    """Representation of a GitHub project."""

    owner: str
    name: str
    url: str
    stars: int = 0
    commits: int = 0

    def __eq__(self, other):
        return self.owner == other.owner and self.name == other.name

    def __hash__(self):
        return hash((self.owner, self.name))

    def __repr__(self):
        return f"<{self.owner}/{self.name}: ⭐️={self.stars:,}, 👩‍💻={self.commits:,}>"


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
        print(f"{contributions_from} to {contributions_up_to}")
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
    creations = set()
    contributions = set()
    for project in projects:
        if project.owner == username:
            category = creations
        else:
            category = contributions
        category.add(project)
    return creations, contributions


# XXX async impactful_creations()
# XXX recorded_contributions()
# XXX generate_readme()


async def main(token: str, username: str):
    async with httpx.AsyncClient() as client:
        gh = gidgethub.httpx.GitHubAPI(
            client, "brettcannon/brettcannon", oauth_token=token
        )
        start_date, projects = await contribution_counts(gh, username)
        # XXX Handle special cases (e.g. microsoft/vscode-python)

    creations, contributions = separate_creations_and_contributions(username, projects)
    print(f"Started {datetime.date.today().year - start_date.year} years ago")
    print(creations)
    print()
    print(contributions)
    # XXX Filter creations down to the "impactful" ones
    # XXX Get contributions that are manually recorded
    # XXX Generate README


if __name__ == "__main__":
    import sys

    # XXX https://github.com/google/python-fire, https://click.palletsprojects.com, http://docopt.org/
    trio.run(main, sys.argv[1], sys.argv[2])
