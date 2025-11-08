# Biographical Links
- [FAQ about me](https://opensource.snarky.ca/About+Me/Frequently+Asked+Questions) (including links to [talks and interviews](https://opensource.snarky.ca/About+Me/Appearances))
- [Blog](https://snarky.ca) ([latest post]({{ post_url }}) published on {{ post_date }})
- [Mastodon](https://mastodon.social/@brettcannon) (with {{"{:,}".format(mastodon_follower_count)}} followers)
- [Bluesky](https://bsky.app/profile/snarky.ca) (with {{"{:,}".format(bluesky_follower_count)}} followers)

# Open Source

<small>Last updated {{ today }}.</small>

## Contributions

I have made _some_ commit to {{ contributions|length }} projects (some of which I started and are denoted with *italics*).

<small>(Grouped by commit count.)</small>
{% for exponent in range (3, -1, -1) %}
<details><summary>&ge; 10<sup>{{ exponent }}</sup></summary>

<ol>
{% for project in contributions %}
{% if 10**(exponent + 1) > project.commits >= 10**exponent %}
<li>{% if project.started %}<i>{% endif %}<a href="{{ project.contributions_url }}">{{ project.repo_name }}</a>{% if project.started %}</i>{% endif %}</li>
{% endif %}
{% endfor %}
</ol>

</details>
{% endfor %}

## [Python](https://python.org)

### [Core Development](https://github.com/python/cpython)

I have had commit rights for over {{ cpython_contributor_years }} years ([since 2003-04-18 PDT](https://github.com/python/cpython/commit/1e91d8eb030656386ef3a07e8a516683bea85610)).

In that time I have become the {{ cpython_contributor_ranking|nth }} most prolific [contributor to CPython](https://github.com/python/cpython/graphs/contributors).


### [Python Enhancement Proposals](https://peps.python.org)

<details>
<summary>I have (co-)authored {{pep_count}} PEPs.</summary>

(Listed from oldest to newest, although I may have become a co-author post-creation.)

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
<td><a href="https://peps.python.org/{{pep.number}}">{{pep.number}}</a></td>
<td>{{pep.title}}</td>
<td title="{{pep.status}}">{{pep.status|status_emoji}}</td>
<td>{{pep.co_authors|join(", ")}}</td>
</tr>
{% endfor %}

</tbody>
</table>

</details>

<details>
<summary>I'm the {{pep_author_ranking|nth}} most prolific PEP author.</summary>

<ol>
{% for author in sorted_authors %}
<li value="{{ author_rankings[author] }}">{% if author == my_name %}<b><i>{% endif %}{{ author }} ({{ author_count[author] }}){% if author == my_name %}</i></b>{% endif %}</li>
{% endfor %}

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
