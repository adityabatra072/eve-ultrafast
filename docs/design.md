# Dynamic operation + target

The input is a natural-language goal. Every page observation builds an indexed table of accessible elements and their current values. One node receives one index, even when it supports both clicking and typing.

One EVE request asks which operation to perform and which target would be appropriate for each available operation. The executor consumes only the target head corresponding to the selected operation. This avoids serial operation-then-target calls and rejects targets incompatible with the operation. Dropdown targets include a code-owned option index.

Operation and target questions receive the same next-step rules, one rule per list item. Target criteria include current values and control state in words (checked, unchecked, expanded). The questions run independently: a target cannot read the operation answer, so its premise explicitly names the operation it assumes.

TYPE_TEXT sends the goal, selected field, visible page context, and recent actions to a small LLM. Its JSON must contain exactly one valid `text` value. The code does not extract quoted literals. A value can be reused after a stale decision only while the entire helper input is identical, and is discarded after a successful mutation.

## Opening other websites

`NAVIGATE` sits in the operation list on every page, next to `DONE` and `BLOCKED`. EVE cannot write text, so when it picks `NAVIGATE` the text helper receives the goal, the current page and recent actions, and must return `{"url": "..."}`. The executor opens the address only if it parses as `http` or `https` with a host and no whitespace; anything else stops before the browser is touched. A rule tells EVE to navigate only from a blank page or when the goal needs a different site, and to use the current site's own links otherwise. Navigation resets the list of unsubmitted fields. The library and inspector accept no start page at all; the agent then begins on `about:blank`.

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
