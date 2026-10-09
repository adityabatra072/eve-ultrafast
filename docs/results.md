# How it was tested

Decisions: `eve` on Wally through `POST /v1/systemone`. Text, addresses and answers: `deepseek-v4.1-flash` on Wally at its default settings. Every run gets one natural-language goal in a fresh tab of a dedicated Chrome profile. Before each task the suite clears cookies and the start site's storage, so a remembered login, consent choice or to-do list can't decide the result. A check on the final URL, page text, form values, cookies, downloaded files or the answer decides pass or fail; the model's own `DONE` counts for nothing.

## The live suite

`uv run python scripts/live_suite.py` runs 68 tasks on public websites. The task list comes from the use cases in [use-cases.md](use-cases.md), which draws on Browser Use's examples, Online-Mind2Web, WebVoyager and Browser Harness skills.

The final run on the final code ran every task 3 times: **189 of 204 runs passed, and 61 of 68 tasks passed all 3 times.**

| Kind | Tasks | Result |
| --- | --- | --- |
| Shopping | best mouse under ₹5,000 on Amazon India from a blank tab; USB-C cables sorted by price; MacBook Air price on Apple India; MacBook Air and iPad Air together; demoblaze add to cart (with its alert) | all 3/3 |
| Search | Google search to runwally.com; DuckDuckGo search to a GitHub repo; Wikipedia search box | Google, Wikipedia 3/3; DuckDuckGo see GitHub below |
| Reference lookups | Eiffel Tower height; Inception's director's birth date; India's population from a long table; serendipity in Cambridge Dictionary; react's version on npm; a Hugging Face model's license; Gurugram weather; Tokyo time on timeanddate (a live clock); birth years of three people from three pages | all 3/3 |
| Docs and long pages | Python's json module; MDN Array.prototype.map; arXiv's withdrawal help; finishing a sentence deep in Wikipedia's "Internet" article | all 3/3 |
| Lists, news, scraping | BBC top story; HN newest, rank 20 and first five Show HN posts; arXiv search; Allrecipes 4.5+ recipe; Books to Scrape page 2, cheapest poetry and travel books; first and last laptop pages on webscraper.io; infinite scroll to quote 30; sorting a table | all 3/3 |
| Filters and dropdowns | quotes by author and tag (dependent dropdowns); react-select custom dropdown; a slider set to 3.5 | all 3/3 |
| Travel and dates | Google Flights one-way Zürich → London; Booking.com Goa for two, 20 to 22 November 2026; jQuery UI date picker inside a frame | date picker 3/3; Flights 2/3; Booking 2/3 |
| Forms and login | httpbin pizza order (radio, checkboxes, textarea, time field) twice; Selenium web form with password; quotes login; w3schools form inside a frame; BMI calculator | all 3/3 |
| Files | upload from a page and from inside a frame; download from a hover menu; read page 3 of an arXiv PDF; save an article as a PDF | all 3/3 |
| Browser features | hover to reveal a profile link; a link that opens a new window; nested frames; JS prompt; Escape key; shadow DOM text; cookie banner; a follow-up question in the same tab; to-do app | all 3/3 |
| Jobs and places | Python.org job board; cafés near Connaught Place on OpenStreetMap; India Gate's coordinates | all 3/3 |
| Apps on GitHub | star count; Issues tab; repo search; a contributor's first commit | see below |

The two failures outside GitHub were real misses. In one Booking run the dates did not reach the search, though the answer read them off the date box. In one Flights run EVE drifted into Google's Explore page and looped until the repeat guard stopped it.

**GitHub.** 13 of the 15 failures were GitHub answering "504 Gateway Time-out". That hit the four GitHub tasks and the DuckDuckGo task, which ends on GitHub. Plain page loads in the same browser, with no agent involved, got the same 504 for `browser-use/browser-use`, after several hundred test runs from this machine's IP. When GitHub served the pages, the tasks passed: every GitHub task passed in the earlier rounds, and re-runs after the final run passed 2 of 3 for the star count and repo search. The agent reported the 504 each time instead of making up an answer.

## How it got here

The first pass of the research-derived tasks passed 20 of 33. Each failure became a fix, listed in [design.md](design.md) under "Real-site fixes":
- frames, shadow DOM, hover menus, uploads and downloads;
- dialogs, keyboard keys, dates and sliders;
- script-wired controls and controls below the fold;
- live clocks, oversized pages, typing focus in frames, overwritten downloads;
- duplicated menu items, and actions that change nothing;
- answers that only saw part of a page, and a `DONE` declared too early.

An earlier 3× run of 66 tasks, before the last of those fixes, passed 183 of 198.

The offline suite passes (`uv run pytest`, 91 tests), and so do the 29 local browser guard checks (`uv run python scripts/check_guards.py`). The guard checks cover a button inside a frame, a hover-revealed link, a file upload, date and slider values, a download, a cross-origin embedded page and a script-wired table header, besides the original freshness and occlusion checks.

## Timing

This page leaves out wall-clock times. The runs went from a machine about 270 ms from Wally's gateway, so most of each run was network travel and the seconds say little about the agent.
