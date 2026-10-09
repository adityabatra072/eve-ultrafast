# Known issues and fixes still needed

The agent handles short, clearly stated jobs on ordinary websites well: look something up, search and filter, fill a form, pull a list, upload or download a file. The latest full run passed 146 of 151 live tasks. That number flatters it, for reasons listed under "How far to trust the results". This page lists what is still wrong or missing, most important first.

## Fixes found in the latest full run

Three of the five misses were the agent's fault. Each has a general cause.

**1. It left the site it was given.** Started on httpbin.org and asked for "the page that shows my IP address", it went to whatismyipaddress.com. The link on httpbin was hidden in a collapsed section, so EVE chose to type an address, which was reasonable. But the helper that writes addresses knows the goal, not the site the run started on.
*Fix:* give the address helper the start site and the rule "stay on it unless the goal names another site". Check any address that leaves the start site without the goal naming a new one.

**2. It read the wrong part of the page.** On a YouTube embed it named a suggested video instead of the one playing. On a player page, most of the text is recommendations; the real title is in the tab title and page metadata.
*Fix:* give the answer step the page's own title, `og:title` and main heading as "what this page is", and tell it to prefer them over lists of other items. Rerun to confirm that is the whole cause.

**3. It knew the next step but gave up.** Asked for a contributor's first commit in a GitHub repo, it clicked their name, which opens their profile. The completion check said exactly what was missing ("open the repository's commit history filtered by this author"), but the profile page had no control for that, so EVE answered BLOCKED until the run ended.
*Fix:* when the check names a missing step and EVE gives up, hand that step to the address helper, which can write the filtered commits URL, or go back. This helps any task where the right page exists but nothing links to it from where the agent is.

## Before using it on a logged-in browser

**4. Nothing asks before irreversible actions.** It will buy, send, delete or post if the goal leads there. Only the goal's wording holds it back.
*Fix:* recognise buttons and forms that commit something (buy, pay, place order, send, post, delete, confirm, submit on a checkout or message form) and pause for a person, unless the goal explicitly says to go ahead. The inspector shows the pending step with Approve and Cancel; the library gets a callback.

**5. Text on the page can steer the models.** Page text is labelled as untrusted data, but EVE and the text helper both read it, and nothing technically stops them from following instructions planted in it.
*Fix:* fix 4 is the main protection. Beyond that, keep page text out of the address helper, flag pages whose text addresses the agent ("ignore previous instructions", "you are an AI"), and test with a page built to hijack it.

## Harder tasks

**6. Long tasks drift.** EVE sees the last 10 actions and has no plan, and a run stops at 60 actions. A 30-step checkout or a multi-page application form is where it loses the thread (miss 3 is a small example).
*Fix:* split the goal into steps at the start (the text helper can do this), keep a done/not-done list in the state, and show EVE the current step. Summarise older actions instead of dropping them.

**7. EVE sees only part of a busy page.** Each question takes at most 26 options and about 8,000 tokens. The agent offers controls on screen plus one screen below, and splits big pages into chunks of 25. On dense pages the right control can be off the list or lost in a chunk.
*Fix:* rank controls by how well they match the goal before chunking, so likely targets land in the same chunk. Log when a page was trimmed, so misses caused by trimming show up in the traces.

**8. Some page types are barely covered.** These have never been tested:
- logged-in apps such as Gmail, Notion, Salesforce, Google Docs and Figma;
- rich text editors;
- right-click menus;
- keyboard shortcuts beyond the fixed key list;
- copy and paste.

*Fix:* add tasks for each on throwaway accounts or open-source copies of these apps, then fix what breaks. Right-click and clipboard need new operations.

**9. Canvas apps are coarse.** Clicking on a canvas uses a 6×4 grid with 9 parts per cell, about a 6% by 8% patch, so drawing, fine map work and games are out of reach, and there is no dragging on a canvas.
*Fix:* a third, finer grid level, and a canvas drag made of two grid picks.

**10. One tab, no memory between runs.** Links that open a new tab open in the same tab. It cannot compare two tabs side by side, run steps in parallel, or remember anything from an earlier run.
*Fix:* tabs as a choice EVE can make (switch, close), and an optional notes store a later run can read.

## Sites that push back

**11. Bot detection.** Headless runs got a CAPTCHA on Google and DuckDuckGo every time. From cloud machines at scale this gets worse. The agent never solves CAPTCHAs, by design: with a visible window it waits for a person, otherwise it stops and says so.
*Fix:* nothing on the solving side. Prefer a visible window or the person's own Chrome profile, and add search fallbacks (go straight to the site, or use a search engine that is not blocking right now).

**12. Guessed web addresses.** Addresses come from the text helper's memory, so a deep link can be wrong and the run spends steps recovering.
*Fix:* prefer the site's home page or its own search, which the instructions already ask for, and remember addresses that worked in this run.

**13. English-only hints.** A few rules match English words: the submit-button check (search, apply, submit, find, update, continue) and the calendar month names. Sites in other languages lose that help.
*Fix:* match on button type and form role instead of words where possible, and add month names for the main languages.

## How far to trust the results

**14. The tests are easier than real use.**
- **Same author.** The same person wrote the tasks, the checks and the fixes; even the held-out tasks come from that person.
- **Short tasks.** Most finish in 0 to 5 actions.
- **Friendly sites.** Many were built for testing (Books to Scrape, the-internet, demoqa).
- **Loose checks.** Many only match a pattern in the answer, so a right-sounding answer passes even if the path was sloppy.
- **Small samples.** 1 to 3 runs per task leaves wide error bars.

*Fix:* run 50 random Online-Mind2Web tasks, unmodified, and have someone else (or a strict grader) judge the full trace, not just the answer. That one number would be worth more than the whole suite.

**15. The model grades its own work.** The same text helper writes the answer and decides whether the goal is complete, and that verdict can turn a stopped run into `done`.
*Fix:* make the completion check stricter about evidence (quote the line on the page that shows it), or give it to a different model.

## Speed, cost and the comparison with jev-ultrafast

**16. Speed and cost are unmeasured.** Every test so far ran from a machine about 270 ms from Wally's servers, so the timings mostly measure the network.
*Fix:* measure from a machine near Wally (in the US): seconds and model calls per task, per step, for the suite.

**17. It does more work per task than upstream.** jev-ultrafast makes one request per step and nothing else. This fork also:
- writes an answer at the end of every run;
- may re-check a "done";
- checks every 4 scrolls whether the goal is already met;
- splits big pages into chunks because of EVE's 26-option limit.

It is far more capable, but on upstream's own Google Flights demo it is probably somewhat slower.
*Fix:* skip the final answer for "open this page" goals, and let a high-confidence DONE skip the check. Then compare head to head.

**18. "Better than jev-ultrafast" is unproven.** On EVE's launch benchmark, EVE is level with Jev or ahead, and faster per decision. The two harnesses have never run the same tasks.
*Fix:* run upstream's Flights demo and the part of this suite that uses only upstream's 8 operations, from the same US machine, with Jev and with EVE. Without a Jev key, running upstream's harness on EVE at least separates the harness's share from the model's.
