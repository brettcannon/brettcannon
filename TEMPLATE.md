# Biographical Links
- [FAQ about me](https://opensource.snarky.ca/About+Me/Frequently+Asked+Questions) (including links to [talks and interviews](https://opensource.snarky.ca/About+Me/Appearances))
- [Blog](https://snarky.ca) ([latest post]({{ post_url }}) published on {{ post_date }})
- [Mastodon](https://fosstodon.org/@brettcannon) (with {{mastodon_follower_count}} followers)
- [Bluesky](https://bsky.app/profile/snarky.ca) (with {{bluesky_follower_count}} followers)

# Open Source

<small>Last updated {{ today }}.</small>

## Creations

<details>
<summary>I have started {{ creations|length }} projects which have received at least one external contribution.</summary>

<small>(Sorted by [☆](https://docs.github.com/en/github/getting-started-with-github/saving-repositories-with-stars#about-stars).)</small>

<ol style="list-style: none">
{% for project in creations %}
<li><a href="{{ project.url }}">{{ project.name }}</a></li>
{% endfor %}
</ol>

  </details>

## Contributions

Over the past [{{ years_contributing }} years](https://github.com/python/cpython/commit/1e91d8eb030656386ef3a07e8a516683bea85610), I have made _some_ commit to {{ contributions|length }} projects.

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

## [Python](https://python.org)

### [Python Enhancement Proposals](https://peps.python.org)

I have (co-)authored {{pep_count}} [PEPs](https://peps.python.org/) ({{pep_author_ranking}} most prolific).

<details>
<summary>PEPs I have (co-authored)</summary>

Listed in creation order, oldest to newest.

(Hover over the status emojis for the textual representation.)

<table>

<thead>
<tr>
<th>#</th>
<th>Title</th>
<th>Status</th>
<th>Co-authors</th>
</tr>
</thead>

<tbody>

{% for pep in pep_details %}
<tr>
<td><a href="https://peps.python.org/{{pep[0]}}">{{pep[0]}}</a></td>
<td>{{pep[2]}}</td>
<td title="{{pep[1][0]}}">{{pep[1][1]}}</td>
<td>{{pep[3]}}</td>
</tr>
{% endfor %}

</tbody>
</table>

</details>

## Planets My Code has Visited

<details>
  <summary>2/8</summary>

- [ ] Mercury
- [ ] Venus
- [X] Earth
- [X] [Mars](https://linuxunplugged.com/396?t=2580)
- [ ] Jupiter
- [ ] Saturn
- [ ] Uranus
- [ ] Neptune

</details>
