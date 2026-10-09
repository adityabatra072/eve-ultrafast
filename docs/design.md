# Dynamic operation + target

The input is a natural-language goal. Every page observation builds an indexed table of accessible elements and their current values. One node receives one index, even when it supports both clicking and typing.

One EVE request asks which operation to perform and which target would be appropriate for each available operation. The executor consumes only the target head corresponding to the selected operation. This avoids serial operation-then-target calls and rejects targets incompatible with the operation. Dropdown targets include a code-owned option index.

Operation and target questions receive the same next-step rules, one rule per list item. Target criteria include current values and control state in words (checked, unchecked, expanded). The questions run independently: a target cannot read the operation answer, so its premise explicitly names the operation it assumes.

TYPE_TEXT sends the goal, selected field, visible page context, and recent actions to a small LLM. Its JSON must contain exactly one valid `text` value. The code does not extract quoted literals. A value can be reused after a stale decision only while the entire helper input is identical, and is discarded after a successful mutation.

## Opening other websites

`NAVIGATE` sits in the operation list on every page, next to `DONE` and `BLOCKED`. EVE cannot write text, so when it picks `NAVIGATE` the text helper receives the goal, the current page and recent actions, and must return `{"url": "..."}`. The executor opens the address only if it parses as `http` or `https` with a host and no whitespace; anything else stops before the browser is touched. A rule tells EVE to navigate only from a blank page or when the goal needs a different site, and to use the current site's own links otherwise. Navigation resets the list of unsubmitted fields. The library and inspector accept no start page at all; the agent then begins on `about:blank`.

## Operations without an element

`PRESS_ENTER` appears only right after a `TYPE_TEXT` on the same page and sends one Enter key through CDP. Search boxes and to-do inputs that submit on Enter need it. `GO_BACK` appears once the run has been on another URL and steps back one history entry. Both reset the unsubmitted-fields list, like `NAVIGATE`.

## Frames, shadow DOM, hover, files and dialogs

- **Frames.** Same-origin frames (w3schools "Try it" results, jQuery UI demos, nested framesets) are read like the page: their controls carry the frame's offset, and the executor hit-tests inside the frame. Chrome runs cross-origin frames in their own process, out of the page script's reach, so the agent attaches to each one's `iframe` target, runs the same snapshot there and places its controls by the frame's box (`DOM.getFrameOwner`, `DOM.getBoxModel`). Clicks and keys still go to the page at the translated position; freshness is checked in the frame's own session. An action to open the frame's address by itself remains as a fallback.
- **Shadow DOM.** Open shadow roots are searched for controls and text, and hit tests run inside the root that owns the element.
- **Hover.** Menus, dropdown parents and figures that hide a link or caption until hovered become `HOVER` targets. Moving the pointer away from a hover can close a menu and shift the page, so while the pointer rests from a hover the next click first moves there, waits a moment and measures again.
- **Uploads.** File inputs are offered only when the caller passes `files=[...]` to `Agent`; each allowed file becomes one choice, and the executor sets it through CDP. The state lists `files_not_attached`, because without it EVE clicked a button named Upload before attaching anything.
- **Downloads.** Chrome saves downloads to `~/Downloads/eve-ultrafast` (`EVE_DOWNLOADS` changes it). A click that saves a file records it, and the answer names it. `SAVE_PDF` prints the page to an A4 PDF in the same folder.
- **Dialogs.** An open alert, confirm or prompt freezes the page, so the agent checks for one before reading the page. While one is open, EVE sees the dialog as the page, with OK and Cancel as its only controls; a prompt's reply comes from the text helper.
- **Drag and drop.** `DRAG` asks two heads in one request: what to drag (`draggable` elements, sortable items, jQuery UI draggables) and where to drop it (drop zones and the other draggables). `draggable="true"` elements get the HTML5 drag events in order, since CDP mouse input does not start a native drag; anything else gets a pointer press, a ten-step move and a release. The history records "A → B", so EVE can see the drop happened.
- **Closed shadow roots.** Page scripts cannot open a closed shadow root, so the snapshot lists custom elements that show no shadow root. The agent reads their subtree through `DOM.describeNode` with `pierce`, resolves the inputs, buttons, links and selects inside, and registers them (with their guards) in the same node cache the executor uses.
- **Canvases.** A visible canvas of at least 200×150 pixels makes `POINT_CANVAS` available. The agent draws a 6×4 labelled grid over a screenshot of it; the vision-capable text helper names a cell, then one of nine parts of an enlarged copy of that cell; the code clicks the part's centre. The model picks labels from a fixed set, so its output never becomes coordinates directly.
- **Human verification.** Cloudflare's interstitial, Google's unusual-traffic page and visible reCAPTCHA or hCaptcha boxes are recognised (an invisible reCAPTCHA badge is not). The agent never solves them: it waits up to 15 seconds for the check to clear by itself, then up to `EVE_HUMAN_WAIT` seconds when someone can see the window, and otherwise stops with that reason. The inspector shows the notice while it waits.
- **Keys.** `PRESS_KEY` is one more head with a fixed list (Escape, Tab, arrows, Page Up/Down, Home, End, Space, Backspace, Delete).
- **Dates and sliders.** Date, time and range inputs take a formatted value set through the element's own setter; the text helper gets the input type and, for a slider, its min, max and step.
- **Script-wired controls.** Table headers, links without `href` and elements with click-handler attributes are offered even though they are not buttons, and the screen is sampled for anything with a pointer cursor.
- **Below the fold.** Controls up to one screen below the visible area are offered; the executor scrolls a target into view before measuring it.

## The answer

Every run ends with an answer. The agent reads the whole document (open shadow roots and same-origin frames included, or a PDF's own text), keeps the passages that share the most words with the goal when the page is long, and adds the text of earlier pages it left. The text helper returns the answer, whether the goal is complete, and what is missing if not.

The completion flag checks EVE. A `DONE` the helper calls incomplete goes back to work, with the missing step added to the history, at most twice. A run that stopped (a limit, or EVE's `BLOCKED`) counts as done when the helper confirms everything the goal asked for was done or found. The prompt asks it to copy prices and numbers as written and to say plainly when the page lacks what was asked. A missing or invalid answer leaves `answer` empty and the run still ends `done`.

## Real-site fixes

Live runs on public sites turned up a handful of patterns the original fixtures never hit:

- Styled dropdowns (Amazon's sort menu) lay a decorative label over a nearly transparent native `<select>`. Choosing an option sets the value and fires `change`, so the executor no longer hit-tests selects.
- Result links with `target="_blank"` opened new tabs the agent never saw. Clicks now retarget their link or form to the agent's tab, and any pop-up a click still opens is loaded in the agent's tab and closed.
- Styled checkboxes are transparent inputs over a drawn box. The snapshot and executor accept them, and a label drawn over its checkbox counts as the same control.
- Several elements often share one label ("Toggle Todo", "Add to cart", "Reply"). Repeated labels get their row's text appended.
- Heavy pages stay mid-navigation for seconds, and an evaluation can go unanswered while they do. Observation backs off for up to thirty seconds, and an unanswered evaluation counts as a stale page instead of an error.
- When the executor refuses a target before any input, the agent drops it for that page and EVE chooses again, instead of the run ending.
- Goals that gather facts from several pages looped: the policy saw only the current page, so it never felt done. The state now lists `visited_pages`, the rules count a visited page as read, and the answer gets an excerpt of each earlier page.
- A run that repeats the same action on the same page four times, or reaches the 60-action budget, now stops with an answer about what it found instead of an error.
- A navigation the server takes longer than 30 seconds to answer no longer ends the run; the agent waits up to another minute for the new document.
- "Best" goals sometimes opened sponsored listings. A rule now prefers regular results unless the goal asks for ads.
- EVE accepts at most about 8,000 tokens per question. A big page (a full Hacker News front page) first drops controls below the fold, then trims text and labels, and EVE is asked again.
- The first click into a frame can leave keyboard focus behind, so typed text went nowhere. The executor focuses the field before typing and, if the field still lacks the text, sets it through the element's own setter.
- Chrome overwrites a download that has the same name as an existing file. Downloads are detected by modification time, not by new names, and files that finish after the click are picked up when the run ends. A PDF that opens in the viewer instead of downloading can be saved with `SAVE_FILE`.
- Menu items and grid cells that wrap their own link or checkbox were offered twice, so EVE could pick the inert container. The container is skipped, and labels have their whitespace collapsed.
- An action that visibly changed nothing is set aside until the page changes, so EVE tries something else instead of clicking the same search box three times. A target the executor refused is set aside the same way, per page state.
- Live clocks and tickers kept every decision stale. Actions that aim at no element (scroll, keys, navigation, waiting, DONE) no longer need the page to be unchanged, and typing uses the field's own guard.
- A page reached through a login redirect stopped receiving CDP mouse input after a few seconds, while Playwright's quick clicks still landed. The executor now watches for the press or click it sent; if the page saw neither, it clicks the element through the DOM. A page that saw the press is never clicked twice.
- A click on a real link waits for the page it opens (up to 15 seconds on slow servers) instead of reading the old page again.
- When EVE cannot find the page a goal needs, it can NAVIGATE, and the text helper may write a DuckDuckGo search limited to the named site. Qatar Airways' baggage allowance went from unreachable to two actions this way.
- A NAVIGATE that lands on a 404 sends EVE back to the site's home page.
- A site that keeps answering with a short error page ("something went wrong", a gateway 5xx, "too many requests") gets three tries, then the run stops with that reason. Google Flights' recurring results error used to eat the whole 60-action budget; it now ends after about a dozen actions.
- Password inputs are offered for typing. The snapshot reports only `filled` or empty and never reads a password back off the page. A password you put in the goal still shows up where the agent typed it: the trace and the decision trail.

## Fitting EVE's limits

EVE answers System One requests on Wally with two limits Jev does not have: a choice question takes 2 to 26 options, and the state nests three levels deep at most.

A head with one candidate needs no question. A head with up to 26 candidates is one question. A larger head becomes chunks of at most 25 elements plus a `NONE` option, all in the first request. In our traces the chunk holding the right element answers with a peaked leader (0.87 to 0.97) while the other chunks put their weight on `NONE` or spread it thin. When one chunk leader reaches 0.8 and no other chunk's best element passes 0.25, the agent takes that leader. Otherwise it sends one more request with the top few elements of each chunk; if those still exceed 26, it repeats. `NONE` can never execute.

The element table and the action history reach EVE as text lines, which keeps the state shallow and reads well. The state also lists `typed_not_yet_submitted`: fields typed since the last button or link click. Without it EVE sometimes opened a visible result before submitting the search that should have filtered it.

## Runtime

One browser-side DOM snapshot supplies common HTML/ARIA roles, names, values, visible text, and executable targets. A WeakMap gives each actual node a code-owned identity; a Map keeps the live references used for execution. Replaced elements receive new identities, disconnected references are pruned, and navigation starts a new cache. These IDs are not CDP backend node IDs. Geometry is always read again immediately before input.

The model sees visible text. Background focus emulation keeps animation frames running in the owned tab. Screenshots are optional and disabled in library calls by default; `screenshots=True` or `record_dir=...` enables them. The inspector enables them explicitly. A continuous screencast can record a run separately.

Freshness compares semantic state instead of counting DOM mutations. Before a click/select, guards compare the document, full URL, viewport, safe form values/states, selected target, and nearby form/dialog/row context. Text generation, typing, scrolling, waiting, and completion use a full semantic comparison. The executor rechecks target visibility, enabled state, geometry, and click occlusion. Scoped guards intentionally permit unrelated visible content to change; this is a practical heuristic, not proof that arbitrary page changes are irrelevant to the goal.

Browser mutations are not retried by transport recovery. Completed execution is logged before the next observation, including when that observation encounters a navigation. An interrupted native-select evaluation stops because its change event may already have fired. Typing uses a browser select-all command followed by CDP text insertion, so existing input contents are replaced.

The next observation waits for up to two animation frames or 50 ms after an interaction. Editable ARIA comboboxes instead wait for visible options, capped at 200 ms. This avoids paying for a prediction before autocomplete suggestions arrive. An explicit WAIT remains 100 ms; network loading is never fast-forwarded in the recording.

## What changed after the first demo

The initial prototype used five manually prepared steps and copied quoted strings. That proved finite-choice browser execution but did not demonstrate task decomposition or text generation. The current policy removes that shortcut and uses the original goal throughout. Operation/target distributions replace the old flat-choice/lookahead/Noul arrangement.

The audit also found that treating every INPUT as editable misclassified checkboxes. Editable roles now control TYPE_TEXT availability. Tests cover checkbox/radio/button distinction, invalid operation/target outputs, stale decisions, text-cache invalidation, missing credentials, waits, and final-route verification.

## Boundaries

Sixty browser actions and 120 decision requests bound a run. Up to 250 action candidates are retained; truncated candidates cannot be selected. The service stays loopback-only, serializes inspector actions, and checks Host, Origin, and a local request token. Credentials remain server-side. Tabs share the existing Chrome profile.

The policy is generic, but two websites do not establish broad reliability. Name resolution covers common labels, ARIA references, and text; it is not the browser's full accessibility algorithm. Shadow roots, frames, canvas, uploads, nested scrolling, pop-ups, and complex keyboard interactions can block progress. A valid action can still be wrong. Independent checks, rather than the model's DONE choice, determine whether the demonstrated task succeeded.
