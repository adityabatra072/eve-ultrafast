# Using EVE Ultrafast

EVE Ultrafast drives a Chrome tab toward a goal you write in plain language. On every page it lists what you can do there (buttons, links, fields, dropdowns), and EVE, RunAnywhere's decision model on Wally, picks the next step. A small language model (DeepSeek V4.1 Flash on Wally by default) writes any text the step needs: a search query, a form value, a web address, and the answer at the end.

## Set up

You need Chrome, [uv](https://docs.astral.sh/uv/) and a RunAnywhere account.

```bash
curl -fsSL https://raw.githubusercontent.com/RunanywhereAI/wally/main/install.sh | sh
wally account login

git clone https://github.com/adityabatra072/eve-ultrafast.git
cd eve-ultrafast
uv sync
```

`wally account login` saves a key the agent picks up on its own. To use a key directly instead, copy `.env.example` to `.env` and set `RUNANYWHERE_API_KEY`.

## The inspector

```bash
uv run eve
```

Open http://127.0.0.1:8766. Pick **Any website**, type a goal, optionally a start address, and click **Start demo**. Then:

- **Run automatically** lets it work until it finishes.
- **Choose next** shows what EVE wants to do, with a probability for every option, without doing it. **Execute choice** does it.
- When the run ends, the green box holds the answer.

The other scenarios (Google Flights and two local test pages) are presets for trying it out.

## From Python

```python
from eve_ultrafast import Agent

with Agent(None, "Go to Amazon India and find me the best mouse under 5000 rupees.") as agent:
    for state in agent.run():
        pass
    print(state["status"])   # done or blocked
    print(state["answer"])   # the reply, written from the pages it read
```

- **Start page.** The first argument is where to start. Pass `None` to start on a blank tab; the agent then opens the right site itself.
- **Each step.** `agent.run()` yields the state after every step. `state["history"]` lists what it did.
- **The result.** `state["status"]` is `done` or `blocked`. `state["stop_reason"]` says why a run stopped early, and `state["answer"]` holds the reply.
- **Uploads.** `files=["resume.pdf"]` lets it attach those files, and nothing else on disk, to upload fields.
- **Follow-up goals.** `agent.follow_up("...")` gives the same tab a new goal; call `agent.run()` again.
- **When it ends.** The tab stays on the page the run reached. `keep_open=False` closes it instead.
- **Screenshots.** `screenshots=True` or `record_dir="frames"` keeps them. EVE does not need them, so they are off by default.

Run your script with `uv run --env-file .env python your_script.py` if you use a `.env` file.

## From the command line

```bash
uv run --env-file .env python examples/run.py --goal "Go to Hacker News and open the comments of the top story."
uv run --env-file .env python examples/run.py --url https://en.wikipedia.org --goal "Find the article on Alan Turing."
```

`examples/flights.py` runs a Google Flights search and checks the result page.

## Which browser it uses

1. **`BU_CDP_URL`, if set.** It uses that browser, for example `http://127.0.0.1:9222` for a Chrome started with `--remote-debugging-port=9222`.
2. **Your everyday Chrome, if remote debugging is on** (chrome://inspect, "Allow remote debugging"). It works in a background tab, with your logins.
3. **Otherwise, a Chrome of its own.** It starts one with a separate profile in `~/.cache/eve-ultrafast/chrome` on port 9333 and reuses it next time. `EVE_HEADLESS=1` hides its window.

## What it can do

On a page it can click, type, pick from a dropdown, tick boxes, hover to open menus, drag and drop, press keys (Enter, Escape, Tab, arrows and others), scroll, go back, open another site, upload files you allowed, save a page as a PDF and save a PDF the tab is showing.

It reads frames (embedded forms and players from other sites too), shadow DOM, date pickers, sliders and custom dropdowns. JavaScript alerts, confirms and prompts become a page with OK and Cancel. On a canvas (a map, game or drawing app) it can click a spot: a vision model picks a cell on a grid over a screenshot.

## Writing goals that work

- **Name the site** when it matters ("on GOV.UK", "on Amazon India"), or give a start address.
- **Say what counts as finished:** "open the first result", "stop when flight options are visible", "do not finish the checkout".
- **Put every value in the goal:** names, dates, sizes. It never invents personal details.
- **Ask a question** if you want facts back ("how tall is…", "what does it cost"); the answer reads the whole page, earlier pages and, when the facts are drawn, a screenshot.
- **Keep it to one job.** Use `follow_up` for the next one.

## How a run ends

- **`done`.** EVE says the goal is met, and the answer step agrees. If the answer step finds something missing (a filter not applied, a search not run), the run goes back to work, up to twice.
- **`blocked`, with a reason.** The run stops early when:
  - it found no way forward;
  - it repeated the same step four times on one page;
  - six steps in a row changed nothing;
  - it scrolled 15 times in a row;
  - the site kept returning an error page;
  - a human-verification page never cleared;
  - it reached 60 actions, or 120 model calls.

  If the answer step confirms the goal was met anyway, the status becomes `done`.

## Human verification

The agent never solves CAPTCHAs or Cloudflare checks. It waits up to 15 seconds for one to clear by itself, which Cloudflare's often does. If the window is visible, it then waits up to `EVE_HUMAN_WAIT` seconds (default 180) for you to solve it; the inspector shows a notice meanwhile. Otherwise it stops and says why.

## Files

- **Uploads:** only files passed in `files=`.
- **Downloads:** saved to `~/Downloads/eve-ultrafast` (change with `EVE_DOWNLOADS`); the answer names them and `state["downloads"]` lists them.

## Settings

| Variable | Default | What it does |
| --- | --- | --- |
| `RUNANYWHERE_API_KEY` | the key from `wally account login` | Key for EVE and the text helper |
| `EVE_MODEL` | `eve` | Decision model |
| `RUNANYWHERE_BASE_URL` | `https://inference.runanywhere.ai/v1` | Wally endpoint |
| `TEXT_MODEL` | `deepseek-v4.1-flash` | Model that writes text, addresses and answers |
| `TEXT_MODEL_BASE_URL` | Wally | Any OpenAI-compatible endpoint for the text model |
| `TEXT_MODEL_API_KEY` | the RunAnywhere key | Key for that endpoint |
| `TEXT_MODEL_REASONING` | unset | `none` or an effort level, for providers that take one |
| `BU_CDP_URL` | unset | Browser to use, by its debugging address |
| `EVE_HEADLESS` | unset | `1` hides the agent's own Chrome |
| `EVE_CHROME_PORT` | `9333` | Port of the agent's own Chrome |
| `EVE_DOWNLOADS` | `~/Downloads/eve-ultrafast` | Download folder |
| `EVE_HUMAN_WAIT` | 180, or 0 when nobody can see the window | Seconds to wait for you on a verification page |
| `EVE_DEMO_PORT` | `8766` | Inspector port |

## Safety

- **Model output never becomes code.** It never becomes selectors, JavaScript or shell commands. Every click resolves to an element the page snapshot found; canvas clicks resolve from a grid cell.
- **Addresses are checked.** The text helper's web addresses must be plain `http(s)` with a host.
- **Passwords stay on the page.** The snapshot never reads a password back off the page. A password you put in the goal does appear in the history where it was typed.
- **Page text is treated as data**, never as instructions.
- **Logins are yours.** It drives whatever browser you connect, logged in or not, so give it a separate profile if you don't want it near your accounts.

## Testing

```bash
uv run pytest                                # offline tests
uv run python scripts/check_guards.py        # real browser, no model calls
uv run python scripts/live_suite.py -n 3     # 83 tasks on public sites, 3 runs each
uv run python scripts/live_suite.py --holdout  # 68 tasks never used for tuning
```

Pass task names to run a few, for example `scripts/live_suite.py amazon_sort booking_goa`. Results and traces land in `artifacts/live-suite/`; the latest numbers are in [results.md](results.md).

## When something goes wrong

- **"daemon didn't come up" or "DevToolsActivePort not found".** Something told it to use a browser it can't reach. Unset `BU_CDP_URL`, or start that browser with `--remote-debugging-port`.
- **"Set RUNANYWHERE_API_KEY or run `wally account login`".** No key was found.
- **"Chrome not found".** Install Chrome, or point `BU_CDP_URL` at a Chromium-based browser.
- **Slow runs.** Most of the time goes to network round trips to Wally; a machine closer to its servers runs faster.
- **A site keeps refusing.** Some sites block automated browsers. Running in your own Chrome profile, with a visible window, helps.
