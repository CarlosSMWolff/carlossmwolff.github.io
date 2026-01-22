---
layout: page
permalink: /publications/
title: publications
description: Publication list and metrics, automatically updated every day by fetching and enriching data from my <a href='https://scholar.google.com/citations?hl=es&user=-VPPZ8YAAAAJ' style='color:#b509ac;'>Google Scholar profile</a>.
nav: true
nav_order: 2
chart:
  echarts: true
---
- [Metrics summary](#metrics-summary)
- [Publication List](#publication-list)

#### Metrics summary


{% comment %}
Auto-summary from _data/author_metrics.yml
{% endcomment %}

{% assign m = site.data.author_metrics %}
{% assign peer = m.stats.peer_reviewed %}
{% assign js = m.journal_summary %}
{% assign h_index = m.metrics.h_index.all %}
{% assign citation_counts =  m.metrics.citations.all %}

{% assign peer_count = peer.count | default: 0 %}
{% assign peer_first = peer.first_author | default: 0 %}
{% assign peer_last  = peer.last_author  | default: 0 %}

{% assign journal_clauses = "" | split: "|" %}

{% for j in js %}
  {% assign c = j.count | default: 0 %}
  {% if c and c > 0 %}
    {% assign fa = j.first_author | default: 0 %}
    {% assign la = j.last_author  | default: 0 %}

    {% assign extra = "" %}
    {% if fa > 0 or la > 0 %}
      {% capture extra %} ({% if fa > 0 %}{{ fa }} first author{% if fa != 1 %}s{% endif %}{% endif %}{% if fa > 0 and la > 0 %}, {% endif %}{% if la > 0 %}{{ la }} last author{% if la != 1 %}s{% endif %}{% endif %}){% endcapture %}
    {% endif %}

    {% assign extra = extra | strip_newlines | replace: "  ", " " %}
    {% capture clause %}{{ c }} **{{ j.journal }}**{{ extra }}{% endcapture %}
    {% assign clause = clause | strip_newlines | replace: "  ", " " | strip %}

    {% assign journal_clauses = journal_clauses | push: clause %}
  {% endif %}
{% endfor %}

- **h-index:** {{ h_index }}
- **Total citations:** {{citation_counts}}

- **{{ peer_count }} peer-reviewed publication{% if peer_count != 1 %}s{% endif %}**.
  - {{ peer_first }} paper{% if peer_first != 1 %}s{% endif %} as first author.
  - {{ peer_last }} paper{% if peer_last != 1 %}s{% endif %} as last author.
- {% if journal_clauses.size > 0 %}
These include {{ journal_clauses | join: ", " }}.
{% endif %}


{% assign cpy = site.data.author_metrics.citations_per_year %}
{% assign years = "" | split: "" %}
{% assign counts = "" | split: "" %}
{% for pair in cpy %}
  {% assign years = years | push: pair[0] %}
  {% assign counts = counts | push: pair[1] %}
{% endfor %}


```echarts
{
  "title": { "text": "Citations per year" },
  "responsive": true,
  "tooltip": { "trigger": "axis" },
  "xAxis": {
    "type": "category",
    "data": {{ years | jsonify }}
  },
  "yAxis": { "type": "value" },
  "series": [
    {
      "name": "Citations",
      "type": "bar",
      "data": {{ counts | jsonify }}
    }
  ]
}
```




#### Publication List

<!-- Bibsearch Feature -->

<!-- {% include bib_search.liquid %} -->
<div class="publications">
{% bibliography %}
</div>