# What people use browser agents for

The live suite's tasks come from four public sources:

- **Browser Use's examples.** Every task string in `examples/use-cases`, `getting_started` and the feature examples for tabs, downloads, scrolling, PDFs, follow-ups, structured output and file upload in [browser-use/browser-use](https://github.com/browser-use/browser-use/tree/main/examples).
- **Online-Mind2Web.** 300 human-written tasks on 136 live sites ([paper](https://arxiv.org/abs/2504.01382), [repo](https://github.com/OSU-NLP-Group/Online-Mind2Web)). The Hugging Face copy is gated, so the task list came from a public mirror of `Online_Mind2Web.json`; a second file with the same 300 task texts agrees with it.
- **WebVoyager.** 643 tasks on 15 sites ([data](https://github.com/MinorJerry/WebVoyager/blob/main/data/WebVoyager_data.jsonl)).
- **Browser Harness.** The 97 site skills and 18 interaction skills people wrote ([browser-use/browser-harness](https://github.com/browser-use/browser-harness)).

## Categories, most frequent first

| Category | Example from the sources | Capabilities it needs |
| --- | --- | --- |
| Look up a fact or open a page | "Find the weight of baggage allowance for economy class on Qatar Airways." (Online-Mind2Web) | search boxes, menus, cookie banners, an answer |
| Scrape a list | "Go to hackernews show hn and give me the first 5 posts" (Browser Use) | scrolling, infinite scroll, pagination, extraction |
| Search with filters and sorting | "external hard drives for Mac with a capacity greater than 2TB on Micro Center, sorted by price" (Online-Mind2Web) | dropdowns, checkboxes, sliders, sort controls |
| Find something near a place | "Find a shelter or rescue group near zip code 90011." (Online-Mind2Web) | autocomplete, distance filters |
| Travel and booking with dates | "a hotel in Amsterdam with a review score of 9 or higher … from March 15 to March 22" (WebVoyager) | date pickers, guest steppers, modals |
| Forms and calculators | "Calculate a FedEx Ground shipping rate for a 3-pound package" (Online-Mind2Web); job application with resume upload (Browser Use) | text, radio, checkbox, time and date fields, file upload, result pages |
| Add to cart | "wireless headphones … for $100 or less and add them to the cart" (Online-Mind2Web) | variants, confirmation dialogs, cart panels |
| Compare items or sites | "Compare the breeds Afghan Hound, Akita and Azawakh." (Online-Mind2Web) | facts gathered across pages |
| Files | "download the smallest doc file" (Browser Use); "what is on page 3?" of a PDF (Browser Use); "save the front page as a PDF" (Browser Use) | downloads, uploads, PDF reading, print to PDF |
| Jobs | "Find support services jobs in Bentonville, Arkansas." (Online-Mind2Web) | filters, pagination |
| Multi-tab research | "open 3 tabs with elon musk, sam altman, and steve jobs, then go back to the first" (Browser Use) | several pages, follow-up tasks |
| Logged-in work | posting, email, admin panels (Browser Harness skills) | logins, iframes, shadow DOM, drag-and-drop |

## How the suite uses this

Each category has tasks in `scripts/live_suite.py`, picked so they run without logging in, paying, posting or sending anything, on sites reachable from India. Checks rely on facts that stay put (a URL pattern, a confirmation message, a submitted value, a downloaded file, a number on a static page) or are computed when the check runs (Hacker News's current front page, next month's date).

Logged-in work is tested on demo sites made for it: the-internet's login and secure area, and SauceDemo's shop from login through checkout. Real accounts are left out of a public suite; the agent drives whatever Chrome you connect, logins included.

Sites left out: Reddit blocks logged-out views, and Wolfram Alpha answers with images, which this agent does not read. Cambridge Dictionary sometimes shows automation a Cloudflare challenge; it stays in the suite, and a run that meets the challenge fails.
