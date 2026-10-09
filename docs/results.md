# How it was tested

Model: `eve` on Wally through `POST /v1/systemone`. Text helper: `deepseek-v4.1-flash` on Wally at its default settings. Every run starts from a fresh tab in a dedicated Chrome profile and gets one natural-language goal. A script checks the final page on its own; the model's `DONE` counts for nothing.

| Task | Start page | Passed | Actions | EVE requests |
| --- | --- | --- | --- | --- |
| Hotel search with two filters, then open one stay | local fixture | 3/3 | 5 | 6 |
| Open one article from a list | local fixture | 3/3 | 1 | 2 |
| Search Wikipedia and open an article | en.wikipedia.org | 3/3 | 2 | 5 to 6 |
| Open the first story's comments | news.ycombinator.com | 3/3 | 1 | 3 |
| One-way Zürich → London, one adult, economy | google.com/travel/flights | 8/12 | 10 to 16 | 16 to 26 |

Starting from a blank tab, with no start page, EVE has to pick `NAVIGATE` and the text helper writes the address:

| Goal | Passed | What happened |
| --- | --- | --- |
| Go to Hacker News and open the comments of the top story | 2/2 | opened news.ycombinator.com, clicked the first story's comments |
| Go to Wikipedia and open the article about the Eiffel Tower | 2/2 | opened the article's address directly |
| Open the GitHub repository browser-use/browser-use | 2/2 | opened the repository's address directly |
| Go to python.org and open the Downloads page | 2/2 | opened python.org/downloads directly |

After `NAVIGATE` was added, the earlier tasks were run again to check EVE doesn't reach for it when the current site will do: hotel 3/3, reading room 3/3, Wikipedia 3/3, Hacker News 3/3 and Google Flights 3/3, with no navigation in any of them.

What each check looks at:

- **Hotel:** the URL ends at Casa Flora, and the page reads "Design · Free cancellation enabled · Destination Lisbon". Opening Casa Flora without applying the search fails.
- **Article:** the URL ends at the requested article.
- **Wikipedia:** the URL is the Gödel's incompleteness theorems article. DeepSeek V4.1 Flash typed the query.
- **Hacker News:** the URL is the comments page of the story ranked first on the front page at that moment. The page has 150+ links, so EVE answered through split target heads. In all three runs one chunk was confident enough to skip the final round.
- **Google Flights:** `examples/flights.py` checks the results URL, one-way, Zürich, London, Fri Nov 20 and 2026, and that every visible flight departs that day.

All four Flights failures ended on Google's "Oops, something went wrong" page. One-way, both cities and the date were correct in each of them. Google showed that page after the Search click in 8 of the 12 runs; EVE clicked Reload, which recovered in 4 of those 8. Loading the same results URL directly never failed, so the error comes from Google's side of the automated search.

The table leaves out wall-clock times. These runs went from a machine about 270 ms away from Wally's gateway, so most of each run was network travel and the seconds say little about the agent.

The first nine Flights runs used GLM-5.3 Flash as the text helper; the last three, the hotel runs and the Wikipedia runs used DeepSeek V4.1 Flash, the current default. The helper only writes field text, so EVE's decisions are the same either way, and both helpers typed every field correctly. On one Flights field, measured from the same machine, DeepSeek answered in a median of 576 ms and GLM in 641 ms.

The offline suite passes (`uv run pytest`, 73 tests), and so do the 21 local browser guard checks (`uv run python scripts/check_guards.py`). The tests cover chunking up to 700 targets, URL checks for `NAVIGATE` and the inspector's start page, `NONE` never executing, the state staying within System One's nesting limit, and the wally key fallback.
