# Page in History

An Anki deck of civilizations and the people who shaped them, and the pipeline that builds it.

It covers the civilizations that Wikipedia's editors list as vital, that Wikipedia writes about in many languages,
that ruled a vast territory, or whose people are among the best known of their age, from Akkad and Old Kingdom Egypt
to the Samanids, the Ottoman and Qing empires and the United States, and the people who shaped them, chosen era by era
within each civilization. Most of today's countries are left to a geography deck. How everything is chosen is set out
step by step in [How the deck is chosen](#how-the-deck-is-chosen).

Every value comes from public sources through code. There are no hand-written overrides: a field is filled only when
independent sources agree, and is left empty otherwise.

## The deck

Two note types, with each kind of question in its own subdeck. The civilization is the hub and every figure links to
one, so the cards build a web: Cyrus → Achaemenid Empire → its map, its neighbours, what came before and after.

### Civilization notes

| Subdeck | Question | Answer | Made only when |
|---|---|---|---|
| Civilizations | the map, with every neighbour labelled | the civilization and its period | Cliopatria draws its borders |
| Succession | Abbasid Caliphate, after it in Iraq: ? | Mongol Empire | the next ruler of its home region is confirmed (see below) |
| Succession | Sasanian Empire, before it in Iran: ? | Parthian Empire | the previous ruler of its home region is confirmed |
| Periods | When: Achaemenid Empire | 550–330 BC | the period is confirmed |

The back of each card also shows the capital, notable rulers, the civilizations before and after, and the modern countries
on its territory.

Historians use "succession" in several ways: legal succession of modern states, claims of legitimate descent, and
the sequence of rulers of a region. The deck asks only the last, which is a matter of record: who ruled the
civilization's home region right before and right after it. The home is the capital it held longest (Baghdad for the
Abbasids), or, without a confirmed capital, the spot it held for the most years; the region named on the card is the
modern country around it. So a conquest counts (Babylon, then the Achaemenids, in Iraq), while losing a province does
not, and the answer may be a civilization that is not in the deck itself.

Each map shows the civilization in red at its largest extent within its confirmed period, preferring years when it
holds one of its capitals so that a year of foreign occupation is not shown, with the year in a corner and a small world
map showing the whole civilization in red. Parts that Wikidata lists for a civilization are painted with it when
Cliopatria draws them separately, such as Spain within the Spanish Empire. The main map is framed on its home region, the land
around its capital, so a few distant colonies do not shrink it to a speck; other regions holding at least a tenth of
its territory, such as Portugal's Angola and Mozambique, get up to three panels beside it. A region spanning a third of
the globe is drawn on the Equal Earth projection, and a civilization too small to spot at its map's scale, such as a
city-state, is circled. Neighbouring civilizations of that year are coloured so that touching ones
never share a colour, and labelled when their name fits inside them. Today's borders are drawn in the same thin line
as the historical ones, light over the civilization and grey elsewhere, so you can see which modern countries it
covered. Capitals are marked with stars where the civilization holds them that year, and major rivers are drawn. Historical borders are smoothed by about a pixel
and cut to today's coastline.

### Figure notes

| Subdeck | Question | Answer | Made only when |
|---|---|---|---|
| Figures | Who: Cyrus the Great | Founder of the Achaemenid Empire, with what they are known for, their civilization and its map | always |
| Photos | a photograph | the person | they died in 1860 or later and a photograph of them alone is found (see below) |

Images of people from before photography are artists' depictions, so they are shown on the back as illustrations
and never asked about.

The Photo card does not have to use the same picture as the Who card. When someone's main Wikidata image is a painting,
as it is for many rulers, the person's other Wikidata images and the files in their own Commons category are tried:
first Wikidata's, then the files most used across Wikipedia's articles, up to ten. A file is accepted when an image
model ([CLIP ViT-L/14](https://github.com/openai/CLIP), run locally on the CPU) sees a portrait of one person rather than
a group, a building or a document, and judges it a photograph rather than a painting, drawing, print or a photograph of
a statue: on its own when it is sure, or when Commons agrees, by a photographic category, by classifying the file as a
photograph, or by a capture date during the person's lifetime. On 410 old portraits labelled by Commons categories
(daguerreotypes, cartes de visite, cabinet cards against paintings, engravings, lithographs and drawings) it found 181
of 200 photographs at the lower bar and wrongly accepted 2 of 210 pictures that were not, both of them mislabelled on
Commons. Without the model (`--photos metadata`), a file is accepted only when a photographic Commons category says so,
which finds fewer photographs.

### Tags

Notes are tagged so you can filter or suspend groups of them:

- civilization: `PIH::Civilization::Sasanian_Empire`, on the civilization and on every figure linked to it
- century: `PIH::Century::BC::06th`, `PIH::Century::AD::03rd`, every century of a civilization's period or a figure's life
- country: `PIH::Country::Iran`, the main modern countries of a civilization and its home region, or where a figure
  was born
- occupation: `PIH::Occupation::Philosopher`, for figures

To skip a kind of question, suspend its subdeck rather than deleting it: deleted cards come back when you import an
update.

## How the deck is chosen

### Sources

| Source | What it provides | Used for |
|---|---|---|
| [Cliopatria](https://github.com/Seshat-Global-History-Databank/cliopatria) (Seshat) | Borders of about 1,600 polities from 3400 BC to 2024 | Which civilizations exist and when, maps, neighbours, succession, where people lived and died |
| [Wikidata](https://www.wikidata.org/) | Structured facts about states and people | Identities, dates, capitals, offices, citizenship, places of death |
| [English Wikipedia](https://en.wikipedia.org/) | Infoboxes, opening paragraphs, categories, the [Vital Articles](https://en.wikipedia.org/wiki/Wikipedia:Vital_articles) lists, the number of language editions | Notability and periods of civilizations, capitals, what a figure is known for, placement votes |
| [Cross-verified database of notable people](https://doi.org/10.21410/7E4/RDAG3O) (Laouenan et al., *Scientific Data*, 2022) | 2.29 million people with their Wikidata id, region, period and a visibility score built from language editions, page length, page views, external links and biographical completeness | Who can be a figure, and how figures rank against their contemporaries |
| [Meta-Wiki's list of articles every Wikipedia should have](https://meta.wikimedia.org/wiki/List_of_articles_every_Wikipedia_should_have) | 1,000 articles chosen by editors from many language communities | A safety net: the people on it are always considered |
| [Pantheon](https://pantheon.world/) 2025 | Occupation and birthplace of 88,000 well-known people | Leaving out occupations such as sport and entertainment, telling rulers from others, the birthplace vote |
| [Natural Earth](https://www.naturalearthdata.com/) | Today's borders, coastline and rivers | Maps, modern countries |
| [Wikimedia Commons](https://commons.wikimedia.org/) and [CLIP](https://github.com/openai/CLIP) | Images and an image model | Portraits and photographs |

### Procedure

1. **Catalogue.** Every polity Cliopatria draws, tied to the Wikidata item of its Wikipedia article, plus states on the
   Vital Articles lists that Cliopatria does not draw, such as Classical Athens and Sparta (see *Civilizations* below).
2. **Civilizations notable on their own.** Any one of: Vital Articles level 4 or above; a Wikipedia article in at least
   60 languages; Vital Articles level 5 with 45 languages or a peak of 1 million km²; a peak of 2 million km² without
   being a colony. A state that still exists qualifies only if Cliopatria draws it before 1900 and it reached 2 million
   km² (the United States, Mexico, Brazil).
3. **Candidate people.** The database of notable people is split into cells: one of its 20 world regions (such as
   Eastern Asia or West Africa) in one period of birth (before 500, 500–1500, 1500–1750, 1750–1900, after 1900). Within
   each cell people are ranked by the database's visibility score, and the first 60 of every cell are candidates, so
   every part of the world and of history brings the same number and the many recent figures do not crowd out the few
   ancient ones. Everyone on Meta-Wiki's list of 1,000 is a candidate too. Left out: people whose adult life began in 1945 or later, sport, business, entertainment and family roles,
   and figures Wikidata files as legendary rather than human (Moses; Jesus is filed as both).
4. **Placement.** Each candidate is tied to the civilizations they belonged to as an adult, from seven sources
   (offices, categories, citizenship, the Vital Articles section, place of death, birthplace and the links in their
   article's opening), against the whole catalogue, not only the civilizations chosen so far (see *A figure's
   civilization* below).
5. **Civilizations their people make notable.** A civilization that step 2 passed over joins when two of the people
   placed there are among the first 10 of their cell, or one among the first 3 or on Meta-Wiki's list, as Confucius is
   for Lu and Laozi for Chu. It must have begun before 1900 and reached 20,000 km².
6. **Segments.** Each figure belongs to their first civilization in the deck. A civilization is split into eras of at
   most 300 years, so the Ottomans of 1300–1600 and of 1600–1923 are separate segments and people compete only with
   their contemporaries.
7. **Figures.** Within each segment, people are ranked by the database's visibility score. Chosen are the candidates
   from step 3, the segment's best person even when they are not one, and anyone on Meta-Wiki's list. A segment keeps at most 25, and
   rulers, politicians and holders of the civilization's offices at most as many as everyone else, so places stay
   empty rather than going to minor kings. Composers are capped at 20 across the deck.
8. **The rest.** People placed in a deck civilization but not chosen are listed in `data/extended.json` and are not
   in the deck.

Both people sources lean towards Europe and, for the last two centuries, North America. Taking the same number of
candidates from every region and period, and comparing people only with their own contemporaries, offsets most of it.
The database's own region and birth year are used only to form the cells; segments and the 1945 cut use Wikidata's
dates, since the database gets a few wrong (it gives Shaka a birth year of 1971).
The deck still reflects who held power and was written about: about 7% of the figures are women.

### Details per field

| Field | Sources | Rule |
|---|---|---|
| Civilizations, borders, neighbours | [Cliopatria](https://github.com/Seshat-Global-History-Databank/cliopatria), Wikipedia Vital Articles (history and geography, levels 3–5), Wikidata | Polities Cliopatria draws that meet any of: Vital Articles level 4 or above; a Wikipedia article in at least 60 languages; Vital Articles level 5 with 45 languages or a peak of 1 million km²; a peak of 2 million km² without being a colony. A sovereign state that still exists qualifies only if Cliopatria draws it before 1900 and it reached 2 million km² (the United States, Mexico, Brazil). Any other polity without an end date must be classed as historical in Wikidata, and today's provinces and towns never qualify. States on the Vital Articles lists that Cliopatria does not draw, such as Classical Athens and Sparta, join by the same signals when Wikidata or the Wikipedia infobox dates them; city-states count although Wikidata also files them as settlements. Left out are items Wikidata files as a civilization (Ancient Egypt), umbrellas over drawn states (Ancient Rome over the Roman Republic and Empire), and states whose capital or location lies inside drawn civilizations of the deck for most of their years (the city of Babylon inside Babylonia). They have no map, but figures can belong to them. Civilizations also join through their people (procedure step 5). With `--region`: polities with at least 5% of the region, or 25% of their own territory inside it, and a peak of 50,000 km² or more |
| Identity of a civilization | Cliopatria's Wikipedia link, Wikidata | The article's Wikidata item must be a state; Cliopatria's own Wikidata ids are often wrong and only count when they match. When the link fails, Cliopatria's own id or a Wikidata state named exactly like the polity stands in, if its dates overlap and it is the only one |
| Period | Wikipedia infobox (start and end years, or the life span), Wikidata (inception and dissolution, or start and end time), Cliopatria | Each end needs two sources to agree: within a year for modern dates and about 1% of a date's age for older ones (47 years at 2700 BC), or at least 25 years against Cliopatria's time steps. Centuries count as their whole range, and the most precise of the agreeing dates is shown |
| Capital | Wikidata, Wikipedia infobox | Capitals both list, as the same item or as two items within 25 km of each other (Wikidata's Shuntian Fu, the infobox's Beijing), shown under the infobox's name and once per place |
| Before and after in its home region | Cliopatria shapes, Wikidata, Wikipedia infobox | The next holder of the home on Cliopatria's maps within 30 years, which Wikidata or either civilization's infobox must also name as predecessor or successor. Holders that stayed under 5 years, such as wartime occupations, are passed over; Cliopatria names that share a Wikidata item count as one civilization; and an empire whose last territory went in large parts (15% or more each) to three or more states has no single successor |
| Rulers | Wikipedia infobox, Wikidata | Infobox leaders that Wikidata records as holding an office of the state |
| Modern countries | Cliopatria, Natural Earth | At least 5% of the civilization's territory or half of the country |
| Map: borders, coastline, rivers | Natural Earth | Rivers of scale rank 4 and above everywhere, rank 5 where they cross the civilization |
| Map: capitals | Wikidata coordinates of the confirmed capitals | |
| Figures | Cross-verified database of notable people, Meta-Wiki's list of 1,000 articles, Pantheon | See procedure steps 3, 6 and 7 |
| A figure's civilization | Wikidata offices held, Wikipedia "people of" categories, place of death (Wikidata) and birthplace (Pantheon) on the Cliopatria map, links in the article's opening paragraph, Wikidata citizenship, the Vital Articles section the person is filed under | At least two of the seven, counting only civilizations that existed during the person's adult life (from age 20). A civilization backed only by places and opening links, with no office, category, citizenship or Vital section for it, counts only when none of the person's other civilizations has more votes, so a death under a foreign occupation does not place someone there. Citizenship given as today's country counts for the Cliopatria state that held its capital during most of the person's adult life, when Wikidata ties that state to the country as an earlier form of it or by naming it as its country, so an occupier (Japan in Seoul) does not count; and as an umbrella civilization (Ancient Rome) for its parts that existed then. People on Meta-Wiki's list with no civilization confirmed can be placed by a single office, category or Vital section. An office counts for the state it governs, or whose head-of-state or head-of-government office it is, during the term; holding that head office confirms the state on its own. A category counts when Wikidata describes it as holding citizens, subjects or office holders of the state, so "collaborators with" and similar categories do not. Civilizations are listed by support, a place of death counting half since exiles die abroad, then by adult years spent in each, and cards show only those in the deck |
| Role | Wikipedia short description, or Wikidata's description when there is none | Dates and centuries are removed, since the life years are shown separately |
| Contribution | Wikipedia opening paragraph | The opening sentence as written, without its subject |
| Portraits | Wikidata, Wikimedia Commons | The person's main Wikidata image; when Commons files it under coins or calligraphy, or there is none, the first other candidate (as for photographs) that the image model sees as a portrait of one person, or without the model that sits in a portrait, painting or statue category named after the person |
| Photographs | Wikidata, Wikimedia Commons, CLIP ViT-L/14 | See the Photos card above: a portrait of one person that the model is sure is a photograph (0.9), or fairly sure (0.7) with Commons agreeing; with `--photos metadata`, a photographic Commons category |

Cliopatria occasionally labels a single time step with the wrong shape (Alexander's empire appears as the Ptolemaic
Kingdom for 326–324 BC). A step more than 2.5 times larger than both its neighbours is ignored and reported by
`check`.

## Building it yourself

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra ml   # or plain `uv sync` and collect with --photos metadata
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

Photographs are recognised with an image model by default. It needs the optional `ml` extra (`onnxruntime` and
`tokenizers`) and downloads about 800 MB of model files into `cache/` on first use. `--photos metadata` skips the model
and relies on Commons categories alone.

## Licence

The code is released under the [MIT licence](LICENSE). The data keeps the licences of its sources:

- Cliopatria: CC BY 4.0
- Wikidata: CC0
- Wikipedia: CC BY-SA
- Pantheon: CC BY
- Cross-verified database of notable people: CC BY-SA 4.0
- Meta-Wiki: CC BY-SA
- Natural Earth: public domain
- Inter typeface, used on the maps: SIL Open Font License
- CLIP model weights (OpenAI, via the ONNX export by Xenova on Hugging Face): MIT
- Wikimedia Commons images: each under the licence credited on its card
