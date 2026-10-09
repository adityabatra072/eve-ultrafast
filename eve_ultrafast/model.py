"""EVE makes choices; a small OpenAI-compatible model writes field values."""

import json
import math
import os
import time
from pathlib import Path

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)
API_BASE = "https://inference.runanywhere.ai/v1"
# A choice question on EVE takes 2 to 26 options. Larger target heads are split into chunks,
# each with a NONE option so a chunk without the right element can say so.
MAX_OPTIONS = 26
NONE = "NONE"
FINALISTS = 3
# Skip the final round only when one chunk is sure and every other chunk points elsewhere.
SURE, ELSEWHERE = 0.8, 0.25


WALLY_CREDENTIALS = Path.home() / ".config" / "wally" / "credentials.json"


def api_key(required=True):
    """RUNANYWHERE_API_KEY, or the key `wally account login` saved."""
    key = os.environ.get("RUNANYWHERE_API_KEY")
    if not key and WALLY_CREDENTIALS.exists():
        try:
            key = json.loads(WALLY_CREDENTIALS.read_text()).get("access_token")
        except (OSError, ValueError, AttributeError):
            key = None
    if not key and required:
        raise ValueError("Set RUNANYWHERE_API_KEY or run `wally account login`; no action executed.")
    return key


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def warm():
    """Open the HTTP/2 connection to Wally while the first page loads."""
    key = api_key(required=False)
    url = os.environ.get("RUNANYWHERE_BASE_URL", API_BASE).rstrip("/") + "/models"
    if key:
        try:
            CLIENT.get(url, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            pass


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid EVE response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


STATE_WORDS = {
    "checked": {"true": "checked", "false": "unchecked", "mixed": "partly checked"},
    "selected": {"true": "selected", "false": "not selected"},
    "expanded": {"true": "expanded", "false": "collapsed"},
}
TOGGLES = {"checkbox", "radio", "switch"}


def control_state(a):
    """ARIA flags in plain words. EVE reads "unchecked" far better than checked=false."""
    words = [STATE_WORDS[k].get(str(a[k]).lower()) for k in STATE_WORDS if k in a]
    return ", ".join(w for w in words if w)


def element_table(elements):
    """One line per element, the same table the inspector shows."""
    lines = []
    for e in elements:
        line = f"[{e['index']}] {e.get('role') or 'element'} {json.dumps(e['label'], ensure_ascii=False)}"
        if e.get("value") and e.get("role") not in TOGGLES:
            line += f" value={json.dumps(e['value'], ensure_ascii=False)}"
        if state := control_state(e):
            line += f" ({state})"
        line += " · " + ", ".join(e["operations"])
        if e.get("options"):
            line += " · options: " + "; ".join(f"[{o['index']}] {o['label']}" for o in e["options"])
        lines.append(line)
    return "\n".join(lines)


def history_text(history):
    """Numbered lines read better than a list of objects, and the state stays shallow."""
    lines = []
    for n, h in enumerate(history, 1):
        text = f" {json.dumps(h['text'], ensure_ascii=False)}" if h.get("text") else ""
        effect = " (no visible change)" if h.get("page_changed") is False else ""
        lines.append(f"{n}. {h.get('kind')} {h.get('action')}{text}{effect}")
    return "\n".join(lines) or "none"


def unsubmitted(history):
    """Fields typed since the last button or link click. Typing alone rarely applies a search."""
    fields = []
    for h in history:
        if h.get("kind") == "fill" and h.get("action") not in fields:
            fields.append(h["action"])
        elif h.get("kind") == "click" and h.get("role") in {"button", "link"}:
            fields = []
    return ", ".join(fields) or "none"


def chunks(ids):
    """Split a head into near-equal chunks that each fit one choice question, NONE included."""
    if len(ids) <= MAX_OPTIONS:
        return [ids]
    count = -(-len(ids) // (MAX_OPTIONS - 1))
    size = -(-len(ids) // count)
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def target_question(goal, operation, candidates):
    return {
        "type": "choice",
        "criteria": {
            index: {
                "element": f"[{index}] {a['label']}",
                **({} if a.get("role") in TOGGLES else {"current_value": a.get("current_value", a.get("value", ""))}),
                **({"role": a["role"]} if "role" in a else {}),
                **({"state": control_state(a)} if control_state(a) else {}),
            }
            for index, a in candidates.items()
        },
        "instructions": {"goal": goal, "operation": operation, "rules": [*NEXT_ACTION, *TARGET]},
    }


def target_heads(goal, operation, candidates):
    """Question name -> question. A head with one candidate needs no question."""
    ids = list(candidates)
    name = operation.lower() + "_target"
    if len(ids) < 2:
        return {}
    parts = chunks(ids)
    if len(parts) == 1:
        return {name: target_question(goal, operation, candidates)}
    heads = {}
    for n, part in enumerate(parts, 1):
        question = target_question(goal, operation, {i: candidates[i] for i in part})
        question["criteria"][NONE] = "None of these elements is the right target for this operation."
        heads[f"{name}_{n}"] = question
    return heads


def leaders(answers, name, ids):
    """Read a split head. Returns (answer, None) when one chunk settles it, else (None, finalists)."""
    parts = chunks(ids)
    keep = max(1, min(FINALISTS, MAX_OPTIONS // len(parts)))
    tops, finalists = [], []
    for n, part in enumerate(parts, 1):
        answer = validate_choice(answers.get(f"{name}_{n}", {}), [*part, NONE])
        ranked = sorted(part, key=lambda i: answer["probabilities"][i], reverse=True)
        tops.append((answer["probabilities"][ranked[0]], ranked[0], answer))
        finalists += ranked[:keep]
    tops.sort(key=lambda top: top[0], reverse=True)
    (best, index, answer), rest = tops[0], tops[1:]
    if best >= SURE and all(p <= ELSEWHERE for p, _, _ in rest):
        probabilities = {i: p for i, p in answer["probabilities"].items() if i != NONE}
        return {"choice": index, "confidence": answer["confidence"], "probabilities": probabilities}, None
    return None, finalists


def pick_target(url, body, result, operation, candidates):
    """Return the selected head's answer, plus any later responses a split head needed."""
    name = operation.lower() + "_target"
    if len(candidates) == 1:
        index = next(iter(candidates))
        return {"choice": index, "confidence": 1.0, "probabilities": {index: 1.0}}, []
    if name in body["questions"]:
        return validate_choice(result["answers"].get(name, {}), candidates), []
    goal = body["questions"]["operation"]["instructions"]["goal"]
    later = []
    answer, finalists = leaders(result["answers"], name, list(candidates))
    while answer is None:
        heads = target_heads(goal, operation, {i: candidates[i] for i in finalists})
        later.append(post_json(url, api_key(), {**body, "questions": heads}))
        if name in heads:
            answer = validate_choice(later[-1]["answers"].get(name, {}), finalists)
        else:
            answer, finalists = leaders(later[-1]["answers"], name, finalists)
    return answer, later


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions.update(target_heads(goal, operation, candidates))
    body = {
        "model": os.environ.get("EVE_MODEL", "eve"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": element_table(elements),
            "recent_actions": history_text(history[-10:]),
            "typed_not_yet_submitted": unsubmitted(history),
        },
        "questions": questions,
    }
    started = time.perf_counter()
    url = os.environ.get("RUNANYWHERE_BASE_URL", API_BASE).rstrip("/") + "/systemone"
    result = post_json(url, api_key(), body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    rounds = 1
    usage = dict(result.get("usage", {}))
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        candidates = targets[operation]
        target_answer, later = pick_target(url, body, result, operation, candidates)
        rounds += len(later)
        for response in later:
            for key, value in response.get("usage", {}).items():
                usage[key] = usage.get(key, 0) + value
        target = target_answer["choice"]
        choice = candidates[target]["id"]
        probabilities = {candidates[i]["id"]: p for i, p in target_answer["probabilities"].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": usage,
        "rounds": rounds,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY") or api_key(required=False)
    if not key:
        raise ValueError("TYPE_TEXT needs RUNANYWHERE_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", API_BASE).rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-v4.1-flash")
    # Wally models run at their default reasoning. Other providers can be told to skip or limit it.
    effort = os.environ.get("TEXT_MODEL_REASONING")
    reasoning = {}
    if effort == "none":
        reasoning = {"reasoning": {"enabled": False}}
    elif effort:
        reasoning = {"reasoning": {"effort": effort}}
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
