"""Live end-to-end tasks on public websites, each with its own check. Paid API calls; never buys or posts anything.

uv run python scripts/live_suite.py                 # every task once
uv run python scripts/live_suite.py amazon_sort -n 3
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_harness.helpers import cdp  # noqa: E402

from eve_ultrafast import Agent  # noqa: E402
from eve_ultrafast.browser import connect  # noqa: E402
from eve_ultrafast.demo import load_environment  # noqa: E402


def has(*words):
    return lambda r: all(w.lower() in (r["text"] + " " + (r["answer"] or "")).lower() for w in words)


def url_has(*parts):
    return lambda r: all(p.lower() in r["url"].lower() for p in parts)


def answer_matches(pattern):
    return lambda r: bool(r["answer"] and re.search(pattern, r["answer"], re.I))


def hn_rank(rank):
    """Story ids at and next to a front-page rank right now (the page shifts while a run is going)."""
    page = urlopen("https://news.ycombinator.com/", timeout=20).read().decode()
    ids = re.findall(r'class="athing submission" id="(\d+)"', page)
    return set(ids[max(0, rank - 2) : rank + 1])


def both(*checks):
    return lambda r: all(c(r) for c in checks)


# name: (start URL or None, goal, check on {"url", "text", "answer"})
TASKS = {
    "amazon_mouse": (
        None,
        "go to amazon india find me the best mouse under 5000 indian rs",
        both(url_has("amazon.in"), answer_matches(r"₹|rs\.?|inr|rupee")),
    ),
    "amazon_sort": (
        "https://www.amazon.in",
        "Search for usb c cable and sort the results by price from low to high.",
        url_has("amazon.in", "price-asc-rank"),
    ),
    "wiki_fact": (None, "How tall is the Eiffel Tower according to Wikipedia?", answer_matches(r"\b3[0-3]\d\b")),
    "wiki_search_box": (
        "https://en.wikipedia.org/wiki/Main_Page",
        "Use the search box to find Alan Turing and open his article.",
        url_has("wikipedia.org/wiki/alan_turing"),
    ),
    "duckduckgo": (
        "https://duckduckgo.com",
        "Search for browser-use github and open the browser-use GitHub repository.",
        url_has("github.com/browser-use/browser-use"),
    ),
    "github_stars": (
        None,
        "How many stars does the browser-use/browser-use repository have on GitHub?",
        both(url_has("github.com/browser-use/browser-use"), answer_matches(r"\d")),
    ),
    "github_issues": (
        "https://github.com/microsoft/playwright",
        "Open the Issues tab of this repository.",
        url_has("github.com/microsoft/playwright/issues"),
    ),
    "github_search": (
        "https://github.com",
        "Use GitHub search to find the pallets/flask repository and open it.",
        lambda r: r["url"].rstrip("/").lower().endswith("github.com/pallets/flask"),
    ),
    "hn_newest": (
        "https://news.ycombinator.com",
        "Open the newest submissions page, then open the comments of its first story.",
        url_has("news.ycombinator.com/item?id="),
    ),
    "python_docs": (
        None,
        "Find the official Python documentation page for the json module.",
        url_has("docs.python.org", "library/json"),
    ),
    "mdn": (
        None,
        "Find the MDN reference page for JavaScript's Array.prototype.map.",
        url_has("developer.mozilla.org", "array/map"),
    ),
    "huggingface": (
        None,
        "Open the Hugging Face page of the model perplexity-ai/pplx-decider-v1-27b and tell me its license.",
        answer_matches(r"apache"),
    ),
    "dictionary": (
        None,
        "Look up the meaning of serendipity in the Cambridge Dictionary.",
        both(url_has("dictionary.cambridge.org"), answer_matches(r"chance|luck|accident|unexpected")),
    ),
    "arxiv": (
        None,
        "Search arXiv for speculative decoding and open the abstract page of the first result.",
        url_has("arxiv.org/abs/"),
    ),
    "bbc": (None, "Go to BBC News and open the top story.", lambda r: "bbc." in r["url"] and "/articles/" in r["url"]),
    "youtube": (None, "Search YouTube for lofi hip hop radio and open the first video.", url_has("youtube.com/watch")),
    "httpbin_form": (
        "https://httpbin.org/forms/post",
        "Order a large pizza with bacon and extra cheese for customer Aditya, phone 9999999999, "
        "email aditya@example.com, delivery instructions: ring the bell. Then submit the order.",
        has('"custname": "Aditya"', '"size": "large"', "bacon", "cheese", "ring the bell"),
    ),
    "selenium_form": (
        "https://www.selenium.dev/selenium/web/web-form.html",
        "Type hello into the text input, secret123 into the password, testing EVE into the textarea, "
        "choose Two in the dropdown, then submit the form.",
        has("received"),
    ),
    "quotes_login": (
        "https://quotes.toscrape.com/login",
        "Log in with username test and password test.",
        has("logout"),
    ),
    "todomvc": (
        "https://demo.playwright.dev/todomvc",
        "Add three todos: buy milk, call mom, ship eve-ultrafast. Then mark call mom as completed.",
        both(has("buy milk", "call mom", "ship eve-ultrafast", "2 items left")),
    ),
    "books_cheapest": (
        "https://books.toscrape.com",
        "Open the Poetry category and tell me the price of the cheapest book on that page.",
        both(url_has("poetry"), answer_matches(r"£\s?\d")),
    ),
    "wiki_multihop": (
        None,
        "Who directed the film Inception? Open that director's Wikipedia article and tell me their date of birth.",
        both(url_has("wikipedia.org/wiki/christopher_nolan"), answer_matches(r"1970")),
    ),
    "books_page2": (
        "https://books.toscrape.com",
        "Go to the second page of the catalogue and tell me the title of the first book on it.",
        both(url_has("page-2"), answer_matches(r"in her wake")),
    ),
    "hn_rank20": (
        "https://news.ycombinator.com",
        "Open the comments of the story ranked 20 on the front page.",
        lambda r: "item?id=" in r["url"] and r["url"].split("id=")[-1] in hn_rank(20),
    ),
    "google_search": (
        "https://www.google.com",
        "Search Google for runwally and open the runwally.com website.",
        url_has("runwally.com"),
    ),
    "apple_price": (
        None,
        "What is the starting price of the MacBook Air on Apple's India website?",
        answer_matches(r"₹"),
    ),
    "weather": (None, "What is the weather in Gurugram right now?", answer_matches(r"°|degree")),
    "allrecipes": (
        None,
        "Find a vegetarian lasagna recipe on Allrecipes rated at least 4.5 stars and open it.",
        url_has("allrecipes.com/recipe/"),
    ),
    "npm_version": (None, "What is the latest version of the react package on npm?", answer_matches(r"\d+\.\d+\.\d+")),
    "apple_compare": (
        None,
        "On Apple's India website, find the starting prices of the MacBook Air and the iPad Air and tell me both.",
        lambda r: bool(r["answer"]) and len(re.findall(r"₹\s?[\d,]{4,}", r["answer"])) >= 2,
    ),
    "react_select": (
        "https://react-select.com/home",
        "In the first example select box, choose Purple.",
        lambda r: "Purple" in r["text"] and "Ocean" not in r["text"].split("Purple")[0][-200:],
    ),
    "wiki_table": (
        "https://en.wikipedia.org/wiki/List_of_countries_and_dependencies_by_population",
        "What population does this page give for India?",
        answer_matches(r"1[.,]\d|billion|\d{3},\d{3},\d{3}"),
    ),
    "flights": (
        "https://www.google.com/travel/flights?hl=en",
        "Find one-way flights from Zurich to London on November 20, 2026, for one adult in economy. "
        "Stop when matching flight options are visible. Do not select or book a flight.",
        lambda r: "/travel/flights/search" in r["url"] and "Select flight" in r["text"] + r["controls"],
    ),
}


def fresh_site(start):
    """Forget what earlier runs left on the start site (to-dos, logins), so each run starts the same."""
    if start:
        parts = urlsplit(start)
        connect()
        cdp("Storage.clearDataForOrigin", origin=f"{parts.scheme}://{parts.netloc}", storageTypes="all")


def run(name, start, goal, check):
    error, state, text, controls = None, None, "", ""
    started = time.perf_counter()
    fresh_site(start)
    try:
        with Agent(start, goal) as agent:
            try:
                for state in agent.run():
                    pass
            except Exception as exc:  # noqa: BLE001 - the suite records every failure and keeps going
                error = f"{type(exc).__name__}: {exc}"
            state = agent.snapshot()
            try:
                text = agent.browser.evaluate("document.body?.innerText || ''") or ""
                controls = " ".join(a["label"] for a in agent.browser.observe(screenshot=False)["actions"])
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    result = {
        "task": name,
        "url": state["page"]["url"] if state else "",
        "status": state["status"] if state else "error",
        "answer": state.get("answer") if state else None,
        "text": text[:20000],
        "controls": controls[:20000],
        "error": error,
    }
    result["passed"] = bool(state) and not error and result["status"] == "done" and check(result)
    result["actions"] = [f"{h['kind']}: {(h['text'] or h['action'])[:60]}" for h in (state or {}).get("history", [])]
    result["decisions"] = len((state or {}).get("decisions", []))
    result["seconds"] = round(time.perf_counter() - started, 1)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tasks", nargs="*", help=f"Any of: {', '.join(TASKS)}")
    parser.add_argument("-n", type=int, default=1, help="Runs per task")
    args = parser.parse_args()
    load_environment()
    names = args.tasks or list(TASKS)
    folder = Path("artifacts/live-suite") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for name in names:
        for _ in range(args.n):
            result = run(name, *TASKS[name])
            results.append(result)
            mark = "PASS" if result["passed"] else "FAIL"
            print(
                f"{mark} {name:<16} {result['status']:<8} {len(result['actions']):>2} actions  {result['url'][:70]}",
                flush=True,
            )
            if result["answer"]:
                print(f"     answer: {result['answer'][:160]}", flush=True)
            if result["error"]:
                print(f"     error: {result['error'][:160]}", flush=True)
            (folder / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed. Traces: {folder}")


if __name__ == "__main__":
    main()
