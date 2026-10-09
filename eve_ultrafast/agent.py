"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import threading
import time
from pathlib import Path

from . import canvas
from .browser import KEYS, Browser, StalePage, Unavailable, verification_wall
from .model import (
    SYNTHETIC,
    action_space,
    canvas_cell,
    choose,
    field_context,
    field_text,
    final_answer,
    navigate_context,
    page_url,
    warm,
)
from .questions import MAX_STEPS


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False, files=()):
        # With no start URL the agent opens a blank tab and NAVIGATEs on its own.
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        # Files the caller allows the agent to attach to upload fields. Nothing else on disk is reachable.
        self.files = [str(Path(f).expanduser().resolve()) for f in files]
        missing = [f for f in self.files if not Path(f).is_file()]
        if missing:
            raise ValueError(f"Files not found: {', '.join(missing)}")
        self.pending_text = None
        # Whole-page text of each page the run moved on from, for answers that gather facts across pages.
        self.page_texts = {}
        # How many times the answer check has sent a premature DONE back to work.
        self.rechecks = 0
        # (page state, action id) pairs the executor refused. Once the page changes (a menu reopens), they
        # are offered again.
        self.unavailable = set()
        warming = threading.Thread(target=warm, daemon=True)
        warming.start()
        self.browser = Browser(url or "about:blank")
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        warming.join(timeout=10)
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            answer=None,
            stop_reason=None,
            notice=None,
            downloads=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.offerable(self.state["page"]["actions"]))[0],
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                if state["status"] in {"done", "blocked"}:
                    return self.snapshot()
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                return self.finish("blocked", state["page"], "Reached the model-call budget")
            wall = verification_wall(state["page"])
            if wall:
                # Never solved by the agent: wait for it to clear on its own, then for a person if one can see it.
                state["page"] = self.wait_out(wall)
                if verification_wall(state["page"]):
                    return self.finish("blocked", state["page"], f"The site asked for human verification ({wall})")
            page = state["page"]
            offered = self.offerable(page["actions"])
            usable = [a for a in offered if (page["fingerprint"], a.get("id")) not in self.unavailable]
            observed = {**page, "actions": usable}
            if self.files:
                # A plain fact moves EVE to attach a file before clicking a button named Upload.
                attached = {h.get("text") or "" for h in state["history"] if h["kind"] == "upload"}
                waiting = [Path(f).name for f in self.files if Path(f).name not in attached]
                observed["files_not_attached"] = ", ".join(waiting) or "none"
            state["decision"] = choose(observed, state["goal"], state["history"])
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                return self.finish("done" if selected == "DONE" else "blocked", page)
            if selected in SYNTHETIC:
                action = SYNTHETIC[selected]
            else:
                action = next(a for a in self.offerable(page["actions"]) if a["id"] == selected)
                if decision.get("drop"):
                    drop = next(a for a in page["actions"] if a["id"] == decision["drop"])
                    action = {**action, "drop": {k: drop[k] for k in ("id", "node", "label")}}
            if len(state["history"]) >= MAX_STEPS:
                return self.finish("blocked", page, f"Stopped at the {MAX_STEPS}-action budget")
            text, helper = None, None
            if action["kind"] == "upload":
                text = Path(action["value"]).name
            prompt = action["kind"] == "dialog" and action["value"] == "prompt"
            if action["kind"] in {"fill", "navigate"} or prompt:
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                if action["kind"] in {"fill", "dialog"}:
                    context, write = field_context(state["goal"], action, page, state["history"]), field_text
                else:
                    context, write = navigate_context(state["goal"], page, state["history"]), page_url
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    try:
                        text, helper = write(context)
                    except ValueError:
                        try:
                            text, helper = write(context)  # one more try: the helper's output varies
                        except ValueError:
                            # Leave this field for now and let EVE choose something else on this page.
                            self.unavailable.add((page["fingerprint"], selected))
                            raise StalePage("The text helper gave no usable value; choosing again.") from None
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            if action["kind"] in {"click", "navigate", "enter", "back", "frame", "key"}:
                try:
                    self.page_texts[page["url"]] = state["browser"].full_text(read_pdf=False)
                except (StalePage, AttributeError, TypeError):
                    pass
            if action["kind"] == "canvas":
                return self.point_at_canvas(page, decision, selected)
            # Browser.act checks freshness immediately before input, including after text generation.
            try:
                outcome = state["browser"].act(action, page, text=text) or {}
            except Unavailable:
                self.unavailable.add((page["fingerprint"], selected))
                raise
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"] + (f" → {action['drop']['label']}" if action.get("drop") else ""),
                    "kind": action["kind"],
                    "role": action.get("role"),
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                    "downloaded": outcome.get("downloaded", []) if isinstance(outcome, dict) else [],
                }
            )
            state.setdefault("downloads", []).extend(state["history"][-1]["downloaded"])
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if not state["history"][-1]["page_changed"] and action["kind"] != "wait":
                # It did nothing visible here; offer something else until the page changes.
                self.unavailable.add((state["page"]["fingerprint"], selected))
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            last = state["history"][-1]
            key = (last["kind"], last["action"], last["url"])
            same = [h for h in state["history"] if (h["kind"], h["action"], h["url"]) == key]
            if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated):
                return self.finish("blocked", state["page"], "Three actions in a row changed nothing")
            if last["kind"] not in {"wait", "scroll"} and len(same) >= 4:
                return self.finish("blocked", state["page"], "Repeating the same action on the same page")
            scrolls = 0
            for h in reversed(state["history"]):
                if h["kind"] != "scroll":
                    break
                scrolls += 1
            if scrolls >= 15:
                return self.finish("blocked", state["page"], "Scrolled 15 times in a row")
            state["status"] = "ready"
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def point_at_canvas(self, page, decision, selected):
        """Coarse cell, then a finer one inside it; the code clicks that sub-cell's centre."""
        state, browser = self.state, self.state["browser"]
        target = page["canvases"][0]
        try:
            shot, rect, scale = browser.canvas_shot(target["node"])
            cell, meta = canvas_cell(
                state["goal"], state["history"], canvas.coarse(shot, rect, scale), canvas.labels(), 1
            )
            box = canvas.cell_box(rect, cell)
            sub, _ = canvas_cell(state["goal"], state["history"], canvas.fine(shot, box, scale), canvas.sub_labels(), 2)
        except ValueError:
            self.unavailable.add((page["fingerprint"], selected))
            raise StalePage("The vision helper named no valid spot; choosing again.") from None
        x, y = canvas.point(box, sub)
        browser.click_point(x, y)
        state["text_calls"].append({**meta, "field": "Canvas", "value": f"{cell}.{sub}"})
        state["history"].append({
            "step": len(state["history"]) + 1, "action": f"Click the canvas at cell {cell}, part {sub}",
            "kind": "canvas", "role": "canvas", "choice": selected,
            "probability": decision["probabilities"].get(selected, 0), "confidence": decision["confidence"],
            "latency_ms": decision["latency_ms"], "text": None, "text_helper": meta.get("model"),
            "text_latency_ms": meta.get("latency_ms", 0), "operation": "POINT_CANVAS", "target": None,
            "page_changed": None, "url": page["url"], "usage": decision["usage"], "downloaded": [],
            "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
        })
        time.sleep(0.3)
        state["page"] = browser.observe(screenshot=self.screenshots)
        state["history"][-1]["page_changed"] = state["page"]["fingerprint"] != page["fingerprint"]
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        state["history"][-1]["elapsed_ms"] = state["elapsed_ms"]
        state["status"] = "ready"
        return self.snapshot()

    def wait_out(self, wall):
        """Cloudflare's check often clears by itself in a few seconds; a CAPTCHA needs a person at the window."""
        browser = self.state["browser"]
        waits = [15] + ([getattr(browser, "human_wait", 0)] if getattr(browser, "human_wait", 0) else [])
        page = self.state["page"]
        for limit in waits:
            if limit > 15:
                self.state["notice"] = f"The site shows {wall}. Solve it in the browser window and EVE will continue."
            deadline = time.monotonic() + limit
            while time.monotonic() < deadline:
                time.sleep(1.5)
                try:
                    page = browser.observe(screenshot=self.screenshots)
                except StalePage:
                    continue
                if not verification_wall(page):
                    self.state["notice"] = None
                    return page
        return page

    def offerable(self, actions):
        """File inputs become one upload choice per allowed file; without files they are not offered.
        Every page also gets the keyboard, one choice per key."""
        out = []
        for a in actions:
            if a.get("kind") != "file":
                out.append(a)
                continue
            for n, path in enumerate(self.files, 1):
                label = f"{a['label']} ← {Path(path).name}"
                out.append({**a, "kind": "upload", "id": f"{a['id']}_file{n}", "value": path, "label": label})
        if not any(a.get("kind") == "dialog" for a in actions):
            out += [
                {"id": f"key_{key.lower()}", "kind": "key", "node": "keyboard", "role": "keyboard", "value": key,
                 "label": f"Keyboard → {key}"}
                for key in KEYS
            ]
        return out

    def finish(self, status, page, reason=None):
        """End the run. Done or not, the text helper answers from what the pages showed."""
        state = self.state
        try:
            # The answer reads the whole document (or PDF), not only the part on screen.
            full = state["browser"].full_text() or page["text"]
        except (StalePage, AttributeError, TypeError):
            full = page["text"]
        if not isinstance(full, str):
            full = page["text"]
        # Slow downloads can finish after the click that started them.
        try:
            late = state["browser"].downloads_since_start()
            if isinstance(late, list):
                state["downloads"] = sorted(set(state.get("downloads", [])) | set(late))
        except (AttributeError, TypeError, OSError):
            pass
        # Pages seen on the way: their whole text when the run kept it, else what was on screen.
        earlier = {}
        for seen in state["decisions"]:
            visited = seen["request"]["state"]["page"]
            if visited["url"] != page["url"]:
                earlier[visited["url"]] = visited["text"]
        for url, text in getattr(self, "page_texts", {}).items():
            if url != page["url"] and isinstance(text, str) and text:
                earlier[url] = text
        browser = state["browser"]

        def answer(image=None):
            final = {**page, "text": full}
            found, meta = final_answer(
                state["goal"], final, state["history"], list(earlier.items()), state.get("downloads", []), image
            )
            if not isinstance(found, dict):
                found = {"answer": found}
            state["text_calls"].append({**meta, "field": "Answer", "value": found.get("answer")})
            return found

        try:
            # Charts, maps, canvases and image results carry facts the text lacks: show the helper the screen.
            drawn = browser.drawn_share() if hasattr(browser, "drawn_share") else 0
            shot = browser.screenshot() if isinstance(drawn, (int, float)) and drawn > 0.3 else None
            verdict = answer(shot if isinstance(shot, str) else None)
            if verdict.get("complete") is False and not isinstance(shot, str) and hasattr(browser, "screenshot"):
                shot = browser.screenshot()
                if isinstance(shot, str):
                    verdict = answer(shot)
        except (ValueError, RuntimeError):
            verdict = {}
        answer = verdict.get("answer")
        # The answer step reads every page the run saw, so it doubles as a check on EVE's DONE.
        # A premature BLOCKED (giving up on the first page) gets the same second look.
        if verdict.get("complete") is False and getattr(self, "rechecks", 0) < 2 and not reason:
            self.rechecks = getattr(self, "rechecks", 0) + 1
            missing = verdict.get("missing") or "the goal is not met yet"
            state["history"].append({
                "step": len(state["history"]) + 1, "action": f"Not done yet: {missing}"[:300], "kind": "check",
                "text": None, "page_changed": False, "url": page["url"], "downloaded": [], "latency_ms": 0,
                "probability": 1.0, "text_helper": None,
            })
            state["status"] = "ready"
            return self.snapshot()
        # A run that stopped (a limit, or EVE saw nothing left to do) still counts as done when the answer
        # step confirms everything the goal asked for was done or found.
        if status == "blocked" and verdict.get("complete") is True:
            status = "done"
        state["answer"] = answer
        state["stop_reason"] = reason
        state["status"] = status
        state["plan_index"] = int(status == "done")
        state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
        return self.snapshot()

    def follow_up(self, goal):
        """Give the same tab a new goal. History and page memory carry over."""
        if not goal.strip():
            raise ValueError("Supply a task")
        self.state.update(goal=goal.strip(), plan=[goal.strip()], plan_index=0, status="ready", decision=None,
                          answer=None, stop_reason=None)
        self.state["page"] = self.state["browser"].observe(screenshot=self.screenshots)
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
