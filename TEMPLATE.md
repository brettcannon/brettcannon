# Biographical Links
- [Curriculum Vitae](https://www.linkedin.com/in/drbrettcannon/) (including links to talk videos)
- [Blog](https://snarky.ca/) ([latest post]({{ post_url }}) published on {{ post_date }})
- [Twitter](https://twitter.com/brettsky/)

# Open Source

<small>Last updated {{ today }}.</small>

## Creations
I have started {{ creations|length }} projects which have received _at least_ one external contribution.

<small>(Sorted by [☆](https://docs.github.com/en/github/getting-started-with-github/saving-repositories-with-stars#about-stars).)</small>

<ol style="list-style: none">
{% for project in creations %}
<li><a href="{{ project.url }}">{{ project.name }}</a></li>
{% endfor %}
</ol>

## Contributions
I have made _some_ commit to {{ contributions|length }} projects spanning {{ years_contributing }} years.

<small>(Grouped by commit count.)</small>


{% for exponent in range (3, -1, -1) %}

<details><summary>&ge; 10<sup>{{ exponent }}</sup></summary>

<ol>
{% for project in contributions %}
{% if 10**(exponent + 1) > project.commits >= 10**exponent %}
<li><a href="{{ project.contributions_url }}">{{ project.repo_name }}</a></li>
{% endif %}
{% endfor %}
</ol>

</details>

{% endfor %}
