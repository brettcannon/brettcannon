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
I have made _some_ contribution to {{ contributions|length }} projects over the past {{ years_contributing }} years
(sorted by my commit count to the project; font size is `√my_commits`).

<ol style="list-style: none">
{% for project in contributions %}
<li><a href="https://github.com/{{ project.owner }}/{{ project.name }}/commits?author={{ username }}" style="font-size: {{ project.sqrt_commits }}pt">{{ project.name_with_owner }}</a></li>
{% endfor %}
</ol>
