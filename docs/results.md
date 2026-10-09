# How it was tested

Model: `eve` on Wally through `POST /v1/systemone`. Text helper: `glm-5.3-flash` on Wally at its default settings. Every run starts from a fresh tab in a dedicated Chrome profile and gets one natural-language goal. A script checks the final page on its own; the model's `DONE` counts for nothing.

| Task | Start page | Passed | Actions | EVE requests |
| --- | --- | --- | --- | --- |
| Hotel search with two filters, then open one stay | local fixture | 3/3 | 5 | 6 |
| Open one article from a list | local fixture | 3/3 | 1 | 2 |
| Search Wikipedia and open an article | en.wikipedia.org | 3/3 | 2 | 4 to 5 |
| Open the first story's comments | news.ycombinator.com | 3/3 | 1 | 3 |
| One-way Zürich → London, one adult, economy | google.com/travel/flights | 6/9 | 10 to 16 | 16 to 26 |

What each check looks at:

- **Hotel:** the URL ends at Casa Flora, and the page reads "Design · Free cancellation enabled · Destination Lisbon". Opening Casa Flora without applying the search fails.
- **Article:** the URL ends at the requested article.
- **Wikipedia:** the URL is the Gödel's incompleteness theorems article. GLM-5.3 Flash typed the query.
- **Hacker News:** the URL is the comments page of the story ranked first on the front page at that moment. The page has 150+ links, so EVE answered through split target heads. In all three runs one chunk was confident enough to skip the final round.
- **Google Flights:** `examples/flights.py` checks the results URL, one-way, Zürich, London, Fri Nov 20 and 2026, and that every visible flight departs that day.

All three Flights failures ended on Google's "Oops, something went wrong" page. One-way, both cities and the date were correct in each of them. Google showed that page after the Search click in 7 of the 9 runs; EVE clicked Reload, which recovered in 4 of those 7. Loading the same results URL directly never failed, so the error comes from Google's side of the automated search.

The table leaves out wall-clock times. These runs went from a machine about 270 ms away from Wally's gateway, so most of each run was network travel and the seconds say little about the agent.

Before these runs the offline suite passed (`uv run pytest`, 44 tests) along with the 21 local browser guard checks (`uv run python scripts/check_guards.py`). The tests cover chunking up to 700 targets, `NONE` never executing, the state staying within System One's nesting limit, and the wally key fallback.
