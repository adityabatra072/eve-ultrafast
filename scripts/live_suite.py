"""Live end-to-end tasks on public websites, each with its own check. Paid API calls; never buys or posts anything.

uv run python scripts/live_suite.py                 # every task once
uv run python scripts/live_suite.py amazon_sort -n 3
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

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


HELLO = str(Path(__file__).with_name("fixtures") / "hello.txt")


def next_month():
    today = datetime.now()
    return today.month % 12 + 1


def show_titles(count):
    """The current Show HN titles, from HN's own API, at check time."""
    ids = json.loads(urlopen("https://hacker-news.firebaseio.com/v0/showstories.json", timeout=20).read())[:count]
    items = [
        json.loads(urlopen(f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=20).read()) for i in ids
    ]
    return [item.get("title", "") for item in items]


def embedded_title():
    """Title of the YouTube video in W3Schools' Try-it example, from YouTube's own oEmbed, at check time."""
    agent = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/154 Safari/537.36"}
    request = Request("https://www.w3schools.com/html/tryit.asp?filename=tryhtml_youtubeiframe", headers=agent)
    page = urlopen(request, timeout=20).read().decode()
    video = re.search(r"youtube\.com/embed/([\w-]{11})", page).group(1)
    meta = json.loads(
        urlopen(f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video}", timeout=20).read()
    )
    return meta["title"]


CANVAS_PAGE = """<!doctype html><title>Shapes</title><body style="margin:30px;font-family:sans-serif">
<h1>Shapes</h1><canvas id="c" width="600" height="300" style="border:1px solid #888"></canvas>
<p id="out">Nothing clicked</p>
<script>
const c=document.getElementById('c'), g=c.getContext('2d');
const dots=[['red',110,150],['green',300,90],['blue',480,220]];
for (const [color,x,y] of dots) { g.fillStyle=color; g.beginPath(); g.arc(x,y,45,0,7); g.fill(); }
c.addEventListener('click', e => {
  const r=c.getBoundingClientRect(), x=e.clientX-r.left, y=e.clientY-r.top;
  const hit=dots.find(([, cx, cy]) => Math.hypot(cx-x, cy-y) <= 45);
  document.getElementById('out').textContent = hit ? 'You clicked ' + hit[0] : 'You missed';
});
</script>"""


def first_author(page):
    """Author of the first quote on a quotes.toscrape.com page, fetched at check time."""
    html = urlopen(f"https://quotes.toscrape.com/page/{page}/", timeout=20).read().decode()
    return re.search(r'class="author"[^>]*>([^<]+)', html).group(1)


def first_prices(url, count):
    html = urlopen(url, timeout=20).read().decode()
    return re.findall(r'price_color">£([\d.]+)', html)[:count]


def cheapest_tablet():
    html = urlopen("https://webscraper.io/test-sites/e-commerce/allinone/computers/tablets", timeout=20).read().decode()
    return min((p for p in re.findall(r"\$([\d.]+)", html) if "." in p), key=float)


def in_answer(compute):
    """The answer contains every value compute() returns at check time."""
    def check(r):
        values = compute()
        values = [values] if isinstance(values, str) else values
        return bool(r["answer"]) and all(v.lower() in r["answer"].lower() for v in values)
    return check


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
    # Adapted from Browser Use's examples, Online-Mind2Web, WebVoyager and Browser Harness skills (docs/results.md).
    "upload": (
        "https://the-internet.herokuapp.com/upload",
        "Upload the file hello.txt and submit it.",
        has("file uploaded", "hello.txt"),
        {"files": [HELLO]},
    ),
    "upload_in_frame": (
        "https://www.w3schools.com/tags/tryit.asp?filename=tryhtml5_input_type_file",
        "In the result frame on the right, choose the file hello.txt and submit the form.",
        has("myfile=hello.txt"),
        {"files": [HELLO]},
    ),
    "hover_profile": (
        "https://the-internet.herokuapp.com/hovers",
        "Hover over the third avatar, tell me the user's name, then open their profile.",
        # The site's profile pages are 404s by design, so opening one is checked from the actions taken.
        both(answer_matches(r"user3"), lambda r: any("view profile" in a.lower() for a in r["actions"])),
    ),
    "menu_download": (
        "https://the-internet.herokuapp.com/jqueryui/menu",
        "Use the menu to go to Enabled, then Downloads, then PDF, and download the PDF.",
        lambda r: any(name.lower().endswith(".pdf") for name in r["downloads"]),
    ),
    "new_window": (
        "https://the-internet.herokuapp.com/windows",
        "Click the link that opens a new window and tell me the heading of the page that opens.",
        answer_matches(r"new window"),
    ),
    "births_three_pages": (
        None,
        "Look up the birth years of Elon Musk, Sam Altman and Steve Jobs on Wikipedia.",
        both(answer_matches(r"1971"), answer_matches(r"1985"), answer_matches(r"1955")),
    ),
    "nested_frames": (
        "https://the-internet.herokuapp.com/nested_frames",
        "What text is shown in the middle frame at the top?",
        answer_matches(r"middle"),
    ),
    "form_in_frame": (
        "https://www.w3schools.com/html/tryit.asp?filename=tryhtml_form_submit",
        "In the result frame, set the first name to Asha and the last name to Rao, then submit.",
        has("fname=asha", "lname=rao"),
    ),
    "datepicker_frame": (
        "https://jqueryui.com/datepicker/",
        "In the demo, open the date picker and pick the 15th of next month.",
        lambda r: f"{next_month():02d}/15/" in r["values"],
    ),
    "booking_goa": (
        "https://www.booking.com/",
        "Search for stays in Goa for 2 adults in 1 room, checking in on 20 November 2026 and checking out on "
        "22 November 2026.",
        url_has("checkin=2026-11-20", "checkout=2026-11-22"),
    ),
    "slider": (
        "https://the-internet.herokuapp.com/horizontal_slider",
        "Set the slider to 3.5.",
        lambda r: re.search(r"\b3\.5\b", r["text"]) is not None,
    ),
    "dependent_dropdowns": (
        "https://quotes.toscrape.com/search.aspx",
        "Search for quotes by Jane Austen with the tag humor.",
        has("the person, be it gentleman or lady"),
    ),
    "infinite_scroll": (
        "https://quotes.toscrape.com/scroll",
        "Load at least 30 quotes, then tell me who wrote quote number 25 and quote number 30.",
        both(answer_matches(r"jim henson"), answer_matches(r"bob marley")),
    ),
    "table_sort": (
        "https://the-internet.herokuapp.com/tables",
        "Sort the first table by Due from largest to smallest and tell me who owes the most.",
        answer_matches(r"jason doe"),
    ),
    "books_travel": (
        "https://books.toscrape.com",
        "What is the cheapest book in the Travel category, and what does it cost?",
        # The listing itself truncates the title to "The Road to Little ...".
        both(answer_matches(r"road to little"), answer_matches(r"23\.21")),
    ),
    "quotes_follow_up": (
        "https://quotes.toscrape.com",
        "List the first 5 quotes on the page with their authors.",
        both(answer_matches(r"einstein"), answer_matches(r"rowling"), answer_matches(r"austen")),
        {
            "follow_up": (
                "Go to the next page and tell me who wrote the first quote there.",
                both(answer_matches(r"monroe"), url_has("/page/2/")),
            )
        },
    ),
    "pizza_time": (
        "https://httpbin.org/forms/post",
        "Order a medium pizza with cheese for John Doe, phone 555-123-4567, email john.doe@example.com, "
        "delivery time 19:30. Then submit the order.",
        has('"custname": "John Doe"', '"size": "medium"', '"delivery": "19:30"'),
    ),
    "js_prompt": (
        "https://the-internet.herokuapp.com/javascript_alerts",
        "Click the JS Prompt button, type harness into the prompt and accept it.",
        has("you entered: harness"),
    ),
    "key_escape": (
        "https://the-internet.herokuapp.com/key_presses",
        "Press the Escape key.",
        has("you entered: escape"),
    ),
    "shadow_dom": (
        "https://the-internet.herokuapp.com/shadowdom",
        "What text is shown inside the shadow DOM on this page?",
        answer_matches(r"different text"),
    ),
    "cookie_banner": (
        "https://www.cookiebot.com/en/",
        "Decline the cookie banner, keeping only necessary cookies, then tell me the page's main heading.",
        lambda r: "marketing:false" in r["cookies"].replace("%3A", ":").replace(" ", ""),
    ),
    "pdf_page3": (
        "https://arxiv.org/pdf/1706.03762",
        "What is on page 3 of this paper?",
        answer_matches(r"transformer|encoder"),
    ),
    "save_pdf": (
        "https://en.wikipedia.org/wiki/Web_browser",
        "Save this article as a PDF.",
        lambda r: any(name.lower().endswith(".pdf") for name in r["downloads"]),
    ),
    "scroll_sentence": (
        "https://en.wikipedia.org/wiki/Internet",
        "Find the sentence that starts with 'The vast majority of computer' and tell me how it ends.",
        answer_matches(r"monitoring of data and traffic"),
    ),
    "arxiv_withdraw": (
        None,
        "On arXiv's help pages, find out how to withdraw an article that has not been announced yet.",
        url_has("arxiv.org/help/withdraw"),
    ),
    "github_first_commit": (
        "https://github.com/facebookresearch/sam2",
        "Find the first commit by NielsRogge in this repository and tell me its message.",
        answer_matches(r"first draft"),
    ),
    "osm_coordinates": (
        "https://www.openstreetmap.org",
        "Search for India Gate, New Delhi and tell me its coordinates.",
        both(answer_matches(r"28\.6[01]"), answer_matches(r"77\.2[23]")),
    ),
    "bmi": (
        "https://www.calculator.net/bmi-calculator.html",
        "Using metric units, calculate the BMI of a 30-year-old male who is 175 cm tall and weighs 70 kg.",
        answer_matches(r"22\.9"),
    ),
    "hn_show": (
        "https://news.ycombinator.com/show",
        "Give me the first 5 Show HN posts with their points.",
        lambda r: sum(t.lower()[:25] in (r["answer"] or "").lower() for t in show_titles(5)) >= 3,
    ),
    "demoblaze_cart": (
        "https://www.demoblaze.com",
        "Add the Sony vaio i5 to the cart, then open the cart.",
        both(url_has("cart.html"), has("sony vaio i5")),
    ),
    "webscraper_last": (
        "https://webscraper.io/test-sites/e-commerce/static/computers/laptops",
        "Go to the last page of laptops and tell me the name and price of the last laptop listed.",
        both(answer_matches(r"rog strix|gl702vm"), answer_matches(r"1,?399")),
    ),
    "stackoverflow_question": (
        None,
        "On Stack Overflow, open the question 'How do I undo the most recent local commits in Git?' and tell me "
        "the score of the accepted answer.",
        both(url_has("stackoverflow.com/questions/"), answer_matches(r"\d")),
    ),
    "timeanddate": (
        None,
        "What time is it now in Tokyo according to timeanddate.com?",
        answer_matches(r"\d{1,2}:\d{2}"),
    ),
    "jobs_search": (
        "https://www.python.org/jobs/",
        "Find a remote job on this job board and open its listing.",
        lambda r: re.search(r"python\.org/jobs/\d+", r["url"]) is not None,
    ),
    "places_nearby": (
        "https://www.openstreetmap.org",
        "Search for cafes near Connaught Place, New Delhi and tell me the name of one of them.",
        both(url_has("openstreetmap.org"), answer_matches(r"caf|coffee|\w{3,}")),
    ),
    # Logged-in work on demo sites built for it, so no real account is involved.
    "login_logout": (
        "https://the-internet.herokuapp.com/login",
        "Log in with username tomsmith and password SuperSecretPassword!, then log out.",
        both(url_has("/login"), has("you logged out of the secure area")),
    ),
    "saucedemo_checkout": (
        "https://www.saucedemo.com",
        "Log in as standard_user with password secret_sauce, add the Sauce Labs Backpack to the cart and go through "
        "checkout up to the overview page using first name Asha, last name Rao and postal code 110001. Do not finish.",
        both(url_has("checkout-step-two"), has("sauce labs backpack")),
    ),
    "drag_columns": (
        "https://the-internet.herokuapp.com/drag_and_drop",
        "Drag column A onto column B.",
        lambda r: r["text"].split("Drag and Drop", 1)[-1].strip().startswith("B"),
    ),
    "drag_droppable": (
        "https://jqueryui.com/droppable/",
        "In the demo, drag the small box onto the big target box.",
        has("dropped!"),
    ),
    "embedded_video_title": (
        "https://www.w3schools.com/html/tryit.asp?filename=tryhtml_youtubeiframe",
        "What is the title of the YouTube video shown in the result frame?",
        lambda r: bool(r["answer"]) and embedded_title()[:12].lower() in r["answer"].lower(),
    ),
    "wolfram_derivative": (
        "https://www.wolframalpha.com",
        "Compute the derivative of x^2 when x = 5.6.",
        answer_matches(r"11\.2"),
    ),
    # More of Online-Mind2Web's domains.
    "finance_quote": (
        None,
        "What is the current value of the NIFTY 50 index on Google Finance?",
        both(url_has("google.com/finance"), answer_matches(r"\d{2},?\d{3}")),
    ),
    "imdb_rating": (None, "What is the IMDb rating of The Shawshank Redemption?", answer_matches(r"9\.[23]")),
    "coursera_course": (
        "https://www.coursera.org",
        "Search for machine learning courses and open the first result.",
        lambda r: re.search(r"coursera\.org/(learn|specializations|professional-certificates)/", r["url"]) is not None,
    ),
    "gov_vat": (
        None,
        "On GOV.UK, find the standard rate of VAT in the UK.",
        both(url_has("gov.uk"), answer_matches(r"20\s?%")),
    ),
    "nhs_flu": (
        None,
        "On the NHS website, find the main symptoms of flu.",
        both(url_has("nhs.uk"), answer_matches(r"fever|temperature")),
    ),
    "sport_section": (None, "Open the football section of BBC Sport.", url_has("bbc.com/sport/football")),
    "recipe_time": (
        None,
        "Find a chicken tikka masala recipe on BBC Good Food and tell me how long it takes to cook.",
        both(url_has("bbcgoodfood.com"), answer_matches(r"tikka"), answer_matches(r"\d+\s*(min|hr|hour)")),
    ),
    "airline_baggage": (
        None,
        "Find the weight of the checked baggage allowance for economy class on Qatar Airways.",
        both(url_has("qatarairways.com"), answer_matches(r"\d+\s?kg")),
    ),
    "canvas_click": (
        "data:text/html," + quote(CANVAS_PAGE),
        "Click the green circle on the canvas.",
        has("you clicked green"),
    ),
    "flights": (
        "https://www.google.com/travel/flights?hl=en",
        "Find one-way flights from Zurich to London on November 20, 2026, for one adult in economy. "
        "Stop when matching flight options are visible. Do not select or book a flight.",
        lambda r: "/travel/flights/search" in r["url"] and "Select flight" in r["text"] + r["controls"],
    ),
}


# Written after the agent was tuned and never used for tuning: scored once, to show how it does on goals it has not
# seen. Run with --holdout.
HOLDOUT = {
    "mars_moons": (None, "How many moons does Mars have according to Wikipedia?", answer_matches(r"\b(two|2)\b")),
    "mdn_gap": (
        None,
        "Find the MDN reference page for the CSS gap property.",
        lambda r: "developer.mozilla.org" in r["url"] and r["url"].rstrip("/").lower().endswith("/gap"),
    ),
    "itertools_docs": (
        None,
        "Find the Python documentation page for the itertools module.",
        url_has("docs.python.org", "itertools"),
    ),
    "express_version": (
        None,
        "What is the latest version of the express package on npm?",
        both(url_has("npmjs.com/package/express"), answer_matches(r"\d+\.\d+\.\d+")),
    ),
    "pypi_requests": (None, "What is the latest version of the requests package on PyPI?", answer_matches(r"\d+\.\d+")),
    "ask_hn": (
        "https://news.ycombinator.com",
        "Open the Ask HN page and open the comments of the first story there.",
        url_has("news.ycombinator.com/item?id="),
    ),
    "sharp_objects": (
        "https://books.toscrape.com",
        "What is the price of the book Sharp Objects?",
        answer_matches(r"47\.82"),
    ),
    "inspirational_quote": (
        "https://quotes.toscrape.com",
        "Find quotes tagged inspirational and tell me who wrote the first one.",
        answer_matches(r"einstein"),
    ),
    "dynamic_loading": (
        "https://the-internet.herokuapp.com/dynamic_loading/1",
        "Start the loading and tell me the text that appears when it finishes.",
        answer_matches(r"hello world"),
    ),
    "dropdown_option": (
        "https://the-internet.herokuapp.com/dropdown",
        "Select Option 2 in the dropdown.",
        lambda r: "2" in r["values"].split(" | "),
    ),
    "js_confirm_cancel": (
        "https://the-internet.herokuapp.com/javascript_alerts",
        "Click the JS Confirm button and cancel the dialog.",
        has("you clicked: cancel"),
    ),
    "taj_city": (
        "https://www.openstreetmap.org",
        "Search for the Taj Mahal and tell me which city it is in.",
        answer_matches(r"agra"),
    ),
    "uk_minimum_wage": (
        None,
        "On GOV.UK, find the National Living Wage rate for workers aged 21 and over.",
        both(url_has("gov.uk"), answer_matches(r"£\s?\d+\.\d\d")),
    ),
    "edx_course": (
        "https://www.edx.org",
        "Search for Python courses and open the first result.",
        url_has("edx.org/"),
    ),
    "tokyo_population": (
        None,
        "What population does Wikipedia give for Tokyo?",
        answer_matches(r"\d{1,3}([,.]\d{3})+|million"),
    ),
    "arxiv_cs_lg": (
        None,
        "Open arXiv's listing of new Machine Learning (cs.LG) papers.",
        url_has("arxiv.org/list/cs.lg"),
    ),
    "httpbin_agent": (
        "https://httpbin.org",
        "Open the page that shows my user agent and tell me what it says.",
        both(url_has("httpbin.org"), answer_matches(r"mozilla")),
    ),
    "mumbai_weather": (None, "What is the weather in Mumbai right now?", answer_matches(r"°|degree")),
    "so_python_top": (
        None,
        "On Stack Overflow, open the highest-voted question tagged python.",
        url_has("stackoverflow.com/questions/"),
    ),
    "wiki_random_fact": (
        None,
        "On Wikipedia, find the year the Golden Gate Bridge opened.",
        answer_matches(r"1937"),
    ),
    # A second batch, also written before it was ever run.
    "everest_height": (None, "How tall is Mount Everest according to Wikipedia?", answer_matches(r"8,?84[89]")),
    "pride_author": (None, "According to Wikipedia, who wrote Pride and Prejudice?", answer_matches(r"austen")),
    "gold_symbol": (None, "What is the chemical symbol for gold according to Wikipedia?", answer_matches(r"\bAu\b")),
    "python_article": (
        None,
        "Open the Wikipedia article about the Python programming language.",
        url_has("wikipedia.org/wiki/python_(programming_language)"),
    ),
    "mdn_promise_all": (
        None,
        "Find the MDN reference page for JavaScript's Promise.all.",
        url_has("developer.mozilla.org", "promise/all"),
    ),
    "pathlib_docs": (
        None,
        "Find the Python documentation page for the pathlib module.",
        url_has("docs.python.org", "pathlib"),
    ),
    "lodash_version": (
        None,
        "What is the latest version of lodash on npm?",
        both(url_has("npmjs.com/package/lodash"), answer_matches(r"\d+\.\d+\.\d+")),
    ),
    "numpy_version": (None, "What is the latest version of numpy on PyPI?", answer_matches(r"\d+\.\d+")),
    "flask_pypi": (None, "Open the PyPI project page for flask.", url_has("pypi.org/project/flask")),
    "velvet_price": (
        "https://books.toscrape.com",
        "What does the book Tipping the Velvet cost?",
        answer_matches(r"53\.74"),
    ),
    "mystery_count": (
        "https://books.toscrape.com",
        "How many books are in the Mystery category?",
        answer_matches(r"\b32\b"),
    ),
    "twain_quote": (
        "https://quotes.toscrape.com",
        "Find a quote by Mark Twain and tell me what it says.",
        answer_matches(r"twain"),
    ),
    "add_elements": (
        "https://the-internet.herokuapp.com/add_remove_elements/",
        "Add three elements.",
        lambda r: r["controls"].count("Delete") >= 3,
    ),
    "number_input": (
        "https://the-internet.herokuapp.com/inputs",
        "Enter 42 in the number field.",
        lambda r: "42" in r["values"],
    ),
    "tab_key": ("https://the-internet.herokuapp.com/key_presses", "Press the Tab key.", has("you entered: tab")),
    "first_hover": (
        "https://the-internet.herokuapp.com/hovers",
        "Hover over the first avatar and tell me the user's name.",
        answer_matches(r"user1"),
    ),
    "status_404": (
        "https://the-internet.herokuapp.com/status_codes",
        "Open the 404 status code page and tell me what it says.",
        both(url_has("status_codes/404"), answer_matches(r"404")),
    ),
    "my_ip": ("https://httpbin.org", "Open the page that shows my IP address.", url_has("httpbin.org/ip")),
    "hn_past": (
        "https://news.ycombinator.com",
        "Open the past front pages section of Hacker News.",
        url_has("news.ycombinator.com/front"),
    ),
    "attention_title": (
        None,
        "Open the arXiv abstract page for paper 1706.03762 and tell me its title.",
        both(url_has("arxiv.org/abs/1706.03762"), answer_matches(r"attention is all you need")),
    ),
    "eiffel_city": (
        "https://www.openstreetmap.org",
        "Search for the Eiffel Tower and tell me which city it is in.",
        answer_matches(r"paris"),
    ),
    "passport_fee": (
        None,
        "On GOV.UK, find how much it costs to renew an adult passport online.",
        both(url_has("gov.uk"), answer_matches(r"£\s?\d+")),
    ),
    "nhs_chickenpox": (None, "Open the NHS page about chickenpox.", url_has("nhs.uk/conditions/chickenpox")),
    "bengaluru_weather": (None, "What is the weather in Bengaluru right now?", answer_matches(r"°|degree")),
    "new_york_time": (
        None,
        "What time is it now in New York according to timeanddate.com?",
        answer_matches(r"\d{1,2}:\d{2}"),
    ),
    "tallest_building": (
        None,
        "According to Wikipedia's list of tallest buildings, what is the tallest building in the world?",
        answer_matches(r"burj khalifa"),
    ),
    "galaxy_cart": (
        "https://www.demoblaze.com",
        "Add the Samsung galaxy s6 to the cart, then open the cart.",
        both(url_has("cart.html"), has("samsung galaxy s6")),
    ),
    "saucedemo_sort": (
        "https://www.saucedemo.com",
        "Log in as standard_user with password secret_sauce and sort the products by price from low to high.",
        lambda r: "lohi" in r["values"],
    ),
    "autocomplete_java": (
        "https://jqueryui.com/autocomplete/",
        "In the demo, type Ja into the field and pick Java from the suggestions.",
        lambda r: "Java" in r["values"].split(" | "),
    ),
    # Third held-out batch, committed before its first run: page patterns the suite did not cover yet.
    "checkboxes": (
        "https://the-internet.herokuapp.com/checkboxes",
        "Tick the first checkbox and untick the second one.",
        lambda r: r["checked"] == "true,false",
    ),
    "enable_field": (
        "https://the-internet.herokuapp.com/dynamic_controls",
        "Enable the text field and type hello into it.",
        lambda r: "hello" in r["values"].split(" | "),
    ),
    "notification": (
        "https://the-internet.herokuapp.com/notification_message_rendered",
        "Click the link that loads a new message and tell me what the message says.",
        answer_matches(r"action (un)?succes+ful"),
    ),
    "table_cell": (
        "https://the-internet.herokuapp.com/challenging_dom",
        "In the table, what is in the Diceret column of the row whose Lorem value is Iuvaret4?",
        answer_matches(r"phaedrum4"),
    ),
    "wrong_password": (
        "https://the-internet.herokuapp.com/login",
        "Log in with username tomsmith and password wrongpass, and tell me what error the site shows.",
        answer_matches(r"password is invalid"),
    ),
    "floating_menu": (
        "https://the-internet.herokuapp.com/floating_menu",
        "Scroll down the page, then click About in the floating menu.",
        url_has("#about"),
    ),
    "demoqa_tabs": (
        "https://demoqa.com/tabs",
        "Open the Origin tab and tell me how its text begins.",
        answer_matches(r"contrary to popular belief"),
    ),
    "demoqa_accordion": (
        "https://demoqa.com/accordian",
        "Expand the section 'Why do we use it?' and tell me how its text begins.",
        answer_matches(r"long established fact"),
    ),
    "demoqa_modal": (
        "https://demoqa.com/modal-dialogs",
        "Open the small modal and tell me what it says.",
        answer_matches(r"small modal"),
    ),
    "demoqa_textbox": (
        "https://demoqa.com/text-box",
        "Fill in the full name Ada Lovelace and the email ada@example.com, then submit the form.",
        has("name:ada lovelace", "email:ada@example.com"),
    ),
    "demoqa_radio": ("https://demoqa.com/radio-button", "Select Impressive.", has("you have selected impressive")),
    "quotes_page3": (
        "https://quotes.toscrape.com",
        "Go to page 3 of the quotes and tell me who said the first quote there.",
        in_answer(lambda: first_author(3)),
    ),
    "mystery_prices": (
        "https://books.toscrape.com",
        "List the titles and prices of the first three books in the Mystery category.",
        in_answer(lambda: first_prices(
            "https://books.toscrape.com/catalogue/category/books/mystery_3/index.html", 3)),
    ),
    "cheapest_tablet": (
        "https://webscraper.io/test-sites/e-commerce/allinone",
        "Open the tablets category and tell me the price of the cheapest tablet.",
        in_answer(cheapest_tablet),
    ),
    "wiki_no_results": (
        "https://en.wikipedia.org",
        "Search Wikipedia for qzxvbnmplk and tell me whether any article matches.",
        answer_matches(r"\bno\b|not find|did not|didn't|does not|doesn't|zero|0 results"),
    ),
    "wiki_french": (
        None,
        "Open the English Wikipedia article on Paris, then switch to the French version of the article.",
        url_has("fr.wikipedia.org"),
    ),
    "compare_population": (
        None,
        "Using Wikipedia, which country has more people, Norway or Sweden?",
        answer_matches(r"sweden"),
    ),
    "requests_release": (
        None,
        "On PyPI, find the release history of the requests package and tell me in which year version 2.0.0 came out.",
        answer_matches(r"2013"),
    ),
    "hn_profile": (
        "https://news.ycombinator.com",
        "Open the Hacker News profile of the user pg and tell me when the account was created.",
        answer_matches(r"2006"),
    ),
}


def fresh_site(start):
    """Forget what earlier runs left on the start site (to-dos, logins), so each run starts the same."""
    connect()
    # Cookies set on a parent domain (consent banners) survive per-origin clearing, so clear them all.
    cdp("Storage.clearCookies")
    if start and start.startswith("http"):
        parts = urlsplit(start)
        cdp("Storage.clearDataForOrigin", origin=f"{parts.scheme}://{parts.netloc}", storageTypes="all")


# Field values in the page and its same-origin frames, for checks a form's text alone cannot show.
VALUES = """(() => {
  const docs=[document];
  for (const f of document.querySelectorAll('iframe,frame')) {
    try { if (f.contentDocument) docs.push(f.contentDocument); } catch {}
  }
  return docs.flatMap(d => [...d.querySelectorAll('input,select,textarea')].map(e => e.value)).join(' | ');
})()"""

CHECKED = "[...document.querySelectorAll('input[type=checkbox]')].map(e => e.checked).join(',')"


def finished(agent, state, error):
    """Everything the checks read about how a run ended."""
    text = controls = values = cookies = checked = ""
    try:
        text = agent.browser.full_text(read_pdf=False)
        controls = " ".join(a["label"] for a in agent.browser.observe(screenshot=False)["actions"])
        values = agent.browser.evaluate(VALUES) or ""
        checked = agent.browser.evaluate(CHECKED) or ""
        cookies = agent.browser.evaluate("document.cookie") or ""
    except Exception:  # noqa: BLE001 - a page that will not answer still gets judged on what is known
        pass
    return {
        "url": state["page"]["url"],
        "status": state["status"],
        "stop_reason": state.get("stop_reason"),
        "answer": state.get("answer"),
        "downloads": state.get("downloads", []),
        "text": text[:30000],
        "controls": controls[:20000],
        "values": values[:5000],
        "checked": checked,
        "cookies": cookies[:3000],
        "error": error,
        "actions": [f"{h['kind']}: {(h['text'] or h['action'])[:60]}" for h in state.get("history", [])],
        "decisions": len(state.get("decisions", [])),
    }


TRACES = None


def save_decisions(name, state):
    """What EVE saw and chose at each step, so a miss can be traced to a page change or a wrong pick."""
    if TRACES is None:
        return
    steps = []
    for d in state.get("decisions", []):
        seen = d.get("request", {}).get("state", {})
        steps.append({
            "url": seen.get("page", {}).get("url"),
            "elements": seen.get("elements"),
            "recent_actions": seen.get("recent_actions"),
            "operation": d.get("operation"),
            "target": d.get("target"),
            "confidence": d.get("confidence"),
        })
    count = len(list(TRACES.glob(f"{name}-*.json")))
    (TRACES / f"{name}-{count + 1}.json").write_text(json.dumps(steps, indent=1, ensure_ascii=False))


def run(name, start, goal, check, options=None):
    options = options or {}
    started = time.perf_counter()
    fresh_site(start)
    result = {"task": name, "url": "", "status": "error", "answer": None, "error": None, "actions": []}
    try:
        with Agent(start, goal, files=options.get("files", ())) as agent:
            error, state = None, None
            try:
                for state in agent.run():
                    pass
            except Exception as exc:  # noqa: BLE001 - the suite records every failure and keeps going
                error = f"{type(exc).__name__}: {exc}"
                result["traceback"] = traceback.format_exc()[-3000:]
            result.update(finished(agent, agent.snapshot(), error))
            save_decisions(name, agent.snapshot())
            passed = not error and result["status"] == "done" and check(result)
            if passed and "follow_up" in options:
                # The same tab gets a second goal, as a person would ask a follow-up question.
                follow, second = options["follow_up"]
                agent.follow_up(follow)
                try:
                    for state in agent.run():
                        pass
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"
                result.update(finished(agent, agent.snapshot(), error))
                result["follow_up_answer"] = result["answer"]
                passed = not error and result["status"] == "done" and second(result)
            result["passed"] = bool(passed)
    except Exception as exc:  # noqa: BLE001
        result.update(error=f"{type(exc).__name__}: {exc}", passed=False)
    result["seconds"] = round(time.perf_counter() - started, 1)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tasks", nargs="*", help=f"Any of: {', '.join(TASKS)}")
    parser.add_argument("-n", type=int, default=1, help="Runs per task")
    parser.add_argument("--holdout", action="store_true", help="Run the held-out tasks instead")
    args = parser.parse_args()
    load_environment()
    tasks = HOLDOUT if args.holdout else TASKS
    names = args.tasks or list(tasks)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path("artifacts/live-suite") / f"{stamp}-{os.getpid()}"
    folder.mkdir(parents=True, exist_ok=True)
    global TRACES
    TRACES = folder / "decisions"
    TRACES.mkdir()
    results = []
    for name in names:
        for _ in range(args.n):
            result = run(name, *tasks[name])
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
