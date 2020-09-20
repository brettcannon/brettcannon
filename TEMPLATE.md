# Biographical Links
- [Curriculum Vitae](https://www.linkedin.com/in/drbrettcannon/) (including links to talk videos)
- [Blog](https://snarky.ca/)
- [Twitter](https://twitter.com/brettsky/)

# Open Source

<span style="font-size: 50%">Last updated {{ today }}</span>

## Creations
I have started {{ creations|length }} projects which have received _at least_ one external contribution (sorted by [stars](https://docs.github.com/en/github/getting-started-with-github/saving-repositories-with-stars#about-stars)).

<ol style="list-style: none">
{% for project in creations %}
<li><a href="{{ project.url }}">{{ project.name }}</a></li>
{% endfor %}
</ol>

## Contributions
I have made _at least_ a commit to {{ contributions|length }} projects over the past {{ years_contributing }} years
(sorted by my commit count to the project; font size is `√my_commits` to
de-emphasize casual contributions).

<ol style="list-style: none">
{% for project in contributions %}
<li><a href="{{ project.contributions_url }}" style="font-size: {{ sqrt(project.commits) }}pt">{{ project.repo_name }}</a></li>
{% endfor %}
</ol>
