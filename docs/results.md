# How it was tested

Decisions: `eve` on Wally through `POST /v1/systemone`. Text, addresses and answers: `deepseek-v4.1-flash` on Wally at its default settings. Every run gets one natural-language goal in a fresh tab of a dedicated Chrome profile. The suite clears the start site's storage and cookies first, so a remembered login or to-do list can't decide the result. A check on the final URL, page text or answer decides pass or fail; the model's own `DONE` counts for nothing.

## Live suite

`uv run python scripts/live_suite.py` runs 33 tasks on public websites. The task list follows what the WebVoyager and Online-Mind2Web benchmarks cover: shopping, search, navigation, form filling, travel lookup and information retrieval. The last full run passed 33/33.

| Kind | Tasks | What the check looks at |
| --- | --- | --- |
| Shopping | best mouse under ₹5,000 on Amazon India (from a blank tab); sort USB-C cables by price; MacBook Air price on Apple India; MacBook Air and iPad Air prices together | Amazon product or sorted URL; prices in the answer, two of them for the comparison |
| Search engines | Google search to runwally.com; DuckDuckGo search to a GitHub repo | final URL |
| Reference | Eiffel Tower height; Inception's director and his birth date; India's population from a long table; meaning of serendipity; react's version on npm; a model's license on Hugging Face; weather in Gurugram | facts in the answer (330 m, 1970, a population figure, a definition, a version number, Apache, a temperature) |
| Docs | Python's json module; MDN's Array.prototype.map | final URL |
| GitHub | star count; Issues tab; repo search | URL plus a number for stars |
| News and lists | BBC top story; HN newest story's comments; HN story ranked 20; arXiv search; Allrecipes recipe rated 4.5+; Books to Scrape page 2 and cheapest poetry book | article or item URL; title or price in the answer |
| Video | YouTube search, open the first video | watch URL |
| Forms | httpbin pizza order (radio, checkboxes, textarea); Selenium web form (password, dropdown); quotes login; react-select custom dropdown | the submitted values echoed back, "Received!", Logout link, selected option |
| Apps | TodoMVC: add three to-dos, complete one | "2 items left" with all three listed |
| Travel | Google Flights one-way Zürich → London | results URL and flight results on the page |

Each task took 0 to 10 actions; the population lookup needed none because the answer reads the whole page. Earlier rounds failed for reasons that turned into fixes, listed under "Real-site fixes" in [design.md](design.md): styled dropdowns, result links that open new tabs, transparent checkboxes, repeated labels, password fields, slow navigations, loops between pages, ads picked as "best", and answers that only saw the visible part of a page.

Some failures came from the sites. Google Flights sometimes answers an automated search with "Oops, something went wrong"; EVE clicks Reload, which usually recovers. During testing arXiv's search took 45 seconds per request and later answered "Rate exceeded". In both cases the run ends with an answer that says what went wrong.

## Earlier checks

The hotel fixture, the reading-room fixture, Wikipedia and Hacker News each passed 3/3 after `NAVIGATE`, Enter and Back were added, with no stray navigation. Starting from a blank tab, Hacker News, Wikipedia, GitHub and python.org tasks passed 8/8.

The offline suite passes (`uv run pytest`, 84 tests), and so do the 22 local browser guard checks (`uv run python scripts/check_guards.py`). They cover chunking up to 700 targets, `NONE` never executing, the nesting limit, URL checks for `NAVIGATE`, Enter and Back, pop-up and covered-target handling, the loop guard, answers on every way a run can end, and the wally key fallback.

## Timing

This page leaves out wall-clock times. The runs went from a machine about 270 ms from Wally's gateway, so most of each run was network travel and the seconds say little about the agent.
