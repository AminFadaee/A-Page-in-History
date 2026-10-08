# Page in History

An Anki deck of civilizations and the people who shaped them, and the pipeline that builds it.

It covers the civilizations that Wikipedia writes about in at least 60 languages and that ended before 1945, from
Akkad and Old Kingdom Egypt to the Ottoman and Qing empires, and the historical figures linked to them. Importance is
measured by that reach rather than by size, so small early civilizations like Phoenicia stay in, and today's
countries are left to a geography deck.

Every value comes from public sources through code. There are no hand-written overrides: a field is filled only when
independent sources agree, and is left empty otherwise.

## The deck

Two note types, with each kind of question in its own subdeck. The civilization is the hub and every figure links to
one, so the cards build a web: Cyrus → Achaemenid Empire → its map, its neighbours, what came before and after.

### Civilization notes

| Subdeck | Question | Answer | Made only when |
|---|---|---|---|
| Civilizations | the map, with every neighbour labelled | the civilization and its period | always |
| Succession | a diagram of what came directly before and after, with the civilization as ? | the civilization | at least one neighbour's own links are all known |
| Periods | When: Achaemenid Empire | 550–330 BC | the period is confirmed |

The back of each card also shows the capital, notable rulers, the civilizations before and after, and the modern countries
on its territory.

The succession diagram shows the civilization's place in the graph of who followed whom, not a single chain: when
the Umayyad Caliphate was followed by both the Abbasids and the Emirate of Córdoba, both appear, so the one asked
about is the only unknown. Each arrow names today's countries where the handover happened (Spain, Portugal for
Córdoba), so a link that is true for one region is not read as true everywhere. A neighbour is drawn only when all its
arrows can be placed, and a civilization gets the card only when at least one neighbour has all its own links in the
deck, so the picture cannot fit another civilization.

Each map shows the civilization in red at its largest extent within its confirmed period, with the year in a corner
and a small world map for orientation. Neighbouring civilizations of that year are coloured so that touching ones
never share a colour, and labelled when their name fits inside them. Today's borders are drawn in the same thin line
as the historical ones, light over the civilization and grey elsewhere, so you can see which modern countries it
covered. Capitals are marked with stars and major rivers are drawn. Historical borders are smoothed by about a pixel
and cut to today's coastline.

### Figure notes

| Subdeck | Question | Answer | Made only when |
|---|---|---|---|
| Figures | Who: Cyrus the Great | Founder of the Achaemenid Empire, with what they are known for, their civilization and its map | always |
| Photos | a photograph | the person | they died in 1860 or later and the image is a photograph: taken during their lifetime by its Commons date, or classified as a photograph on Commons |

Images of people from before photography are artists' depictions, so they are shown on the back as illustrations
and never asked about.

### Tags

Notes are tagged so you can filter or suspend groups of them:

- civilization: `PIH::Civilization::Sasanian_Empire`, on the civilization and on every figure linked to it
- century: `PIH::Century::BC::06th`, `PIH::Century::AD::03rd`, every century of a civilization's period or a figure's life
- country: `PIH::Country::Iran`, the main modern countries of a civilization and where its successions happened, or
  where a figure was born
- occupation: `PIH::Occupation::Philosopher`, for figures

To skip a kind of question, suspend its subdeck rather than deleting it: deleted cards come back when you import an
update.

## Sources and rules

| Field | Sources | Rule |
|---|---|---|
| Civilizations, borders, neighbours | [Cliopatria](https://github.com/Seshat-Global-History-Databank/cliopatria), Wikidata | Polities with a Wikipedia article in at least 60 languages that ended before 1945. With `--region`: polities with at least 5% of the region, or 25% of their own territory inside it, and a peak of 50,000 km² or more |
| Identity of a civilization | Cliopatria's Wikipedia link, Wikidata | The article's Wikidata item must be a state; Cliopatria's own Wikidata ids are often wrong and only count when they match |
| Period | Wikipedia infobox (start and end years, or the life span), Wikidata, Cliopatria | Each end needs two sources to agree: within a year, or 25 years against Cliopatria's time steps. Centuries count as their whole range, and the most precise of the agreeing dates is shown |
| Capital | Wikidata, Wikipedia infobox | Capitals both list |
| Predecessor, successor | Wikidata, Wikipedia infobox, Cliopatria shapes | At least two of the three, pooling what both civilizations' articles say about the link. Links are never chained, and a link that skips a confirmed middle step is dropped |
| Rulers | Wikipedia infobox, Wikidata | Infobox leaders that Wikidata records as holding an office of the state |
| Modern countries | Cliopatria, Natural Earth | At least 5% of the civilization's territory or half of the country |
| Map: borders, coastline, rivers | Natural Earth | Rivers of scale rank 4 and above everywhere, rank 5 where they cross the civilization |
| Map: capitals | Wikidata coordinates of the confirmed capitals | |
| Figures | Wikipedia Vital Articles (levels 4 and 5), Pantheon | Everyone on level 4; from level 5 only people with a Pantheon Historical Popularity Index of 78 or more. Leaves out entertainers, athletes and similar occupations |
| A figure's civilization | Wikidata citizenship, links in the article's opening paragraph, the Vital Articles section the person is filed under, birthplace (Pantheon) on the Cliopatria map | At least two of the four |
| Role | Wikipedia short description, or Wikidata's description when there is none | Dates and centuries are removed, since the life years are shown separately |
| Contribution | Wikipedia opening paragraph | The opening sentence as written, without its subject |
| Portraits | Wikidata, Wikimedia Commons | The person's main Wikidata image, unless Commons files it under coins or calligraphy |

Cliopatria occasionally labels a single time step with the wrong shape (Alexander's empire appears as the Ptolemaic
Kingdom for 326–324 BC). A step more than 2.5 times larger than both its neighbours is ignored and reported by
`check`.

## Building it yourself

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

```sh
uv run page-in-history collect   # select civilizations and figures, render maps into data/
uv run page-in-history check     # what was confirmed, what was left out and why
uv run page-in-history deck      # build build/page_in_history.apkg
uv run page-in-history samples   # render sample cards into docs/samples/
```

`collect` builds the whole world by default. `--region` (a modern country as named by Natural Earth), `--start` and
`--end` (negative years are BC) limit it to one slice, such as `--region Iran --start -1000 --end 1000`. Downloads
and API responses are cached in `cache/`, so later runs work offline.

## Licence

The code is released under the [MIT licence](LICENSE). The data keeps the licences of its sources:

- Cliopatria: CC BY 4.0
- Wikidata: CC0
- Wikipedia: CC BY-SA
- Pantheon: CC BY
- Natural Earth: public domain
- Inter typeface, used on the maps: SIL Open Font License
- Wikimedia Commons images: each under the licence credited on its card
