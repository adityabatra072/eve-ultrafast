"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.daemon import PROFILES
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

# Browser Harness gives a CDP call 5 seconds. A slow site can take longer just to accept a navigation.
NAVIGATION_TIMEOUT = 30
CHROME_PORT = int(os.environ.get("EVE_CHROME_PORT", "9333"))
CHROME_PROFILE = Path.home() / ".cache" / "eve-ultrafast" / "chrome"
CHROME_BINARIES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Unavailable(StalePage):
    """The executor refused a target before any input. The agent stops offering it on that page."""


def listening(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def debuggable_profile():
    """True when an everyday browser profile already has remote debugging on."""
    for base in PROFILES:
        try:
            port = int((Path(base) / "DevToolsActivePort").read_text().splitlines()[0])
        except (OSError, ValueError, IndexError):
            continue
        if listening(port):
            return True
    return False


def connect():
    """Use BU_CDP_URL, then a browser with remote debugging on, then a dedicated Chrome we start.

    Returns True when the agent owns the browser window, so its tab can come to the front."""
    if os.environ.get("BU_CDP_URL") or os.environ.get("BU_CDP_WS") or os.environ.get("BU_BROWSER_ID"):
        ensure_daemon()
        return False
    if debuggable_profile():
        ensure_daemon()
        return False
    if not listening(CHROME_PORT):
        binary = next((b for b in CHROME_BINARIES if Path(b).exists() or shutil.which(b)), None)
        if not binary:
            raise RuntimeError("Chrome not found. Install Chrome or set BU_CDP_URL to a browser with remote debugging.")
        CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
        flags = ["--headless=new"] if os.environ.get("EVE_HEADLESS") == "1" else []
        subprocess.Popen(
            [binary, f"--remote-debugging-port={CHROME_PORT}", f"--user-data-dir={CHROME_PROFILE}",
             "--no-first-run", "--no-default-browser-check", *flags, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        deadline = time.monotonic() + 20
        while not listening(CHROME_PORT):
            if time.monotonic() > deadline:
                raise RuntimeError(f"Chrome did not open its debugging port {CHROME_PORT}.")
            time.sleep(0.1)
    os.environ["BU_CDP_URL"] = f"http://127.0.0.1:{CHROME_PORT}"
    ensure_daemon()
    return True


class Browser:
    def __init__(self, url):
        owned = connect()
        # In someone's everyday Chrome the tab stays in the background; in our own window it comes forward.
        self.target = cdp("Target.createTarget", url="about:blank", background=not owned)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        try:
            self.call("Page.navigate", url=url, _response_timeout=NAVIGATION_TIMEOUT)
        except TimeoutError:
            self.wait_for_navigation("about:blank")
        self.wait_for_load()

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        try:
            response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        except TimeoutError:
            # A page busy navigating can leave an evaluation unanswered; observe it again.
            raise StalePage("Page did not answer in time") from None
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__eveFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except (RuntimeError, TimeoutError):
                pass
        # Heavy pages (YouTube, say) can stay mid-navigation for seconds. Back off instead of giving up.
        deadline, attempt = time.monotonic() + 10, 0
        while True:
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot}
                )
            except StalePage:
                if time.monotonic() > deadline:
                    raise
                time.sleep(min(0.02 * 2**attempt, 0.25))
                attempt += 1

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__eveFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] not in {"wait", "navigate", "enter", "back"} else None
        if action["kind"] == "navigate" and text != page["url"]:
            self.wait_for_navigation(page["url"])
        if action["kind"] in {"navigate", "enter", "back"}:
            self.wait_for_load()
        if action["kind"] == "click":
            self.adopt_popups()
        return result

    def adopt_popups(self):
        """A click that still opened a tab (window.open, say): load its page here and close it."""
        try:
            self._adopt_popups()
        except TimeoutError:
            pass

    def _adopt_popups(self):
        popups = [
            t for t in cdp("Target.getTargets").get("targetInfos", [])
            if t.get("openerId") == self.target and t.get("type") == "page"
        ]
        for popup in popups:
            url, deadline = popup.get("url", ""), time.monotonic() + 2
            while url in {"", "about:blank"} and time.monotonic() < deadline:
                time.sleep(0.05)
                info = cdp("Target.getTargetInfo", targetId=popup["targetId"]).get("targetInfo", {})
                url = info.get("url", "")
            cdp("Target.closeTarget", targetId=popup["targetId"])
            if url.startswith(("http://", "https://")):
                try:
                    self.call("Page.navigate", url=url, _response_timeout=NAVIGATION_TIMEOUT)
                except TimeoutError:
                    pass
                self.wait_for_load()
                self.after_input = None

    def wait_for_navigation(self, before, seconds=60):
        """A slow server can take longer than the CDP call to answer. Keep waiting for the new document."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if self.evaluate("location.href") != before:
                    return
            except StalePage:
                pass
            time.sleep(0.1)

    def wait_for_load(self, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    return
            except StalePage:
                pass
            time.sleep(0.05)

    def close(self):
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        try:
            result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        except TimeoutError:
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution timed out; inspect before retrying.") from None
            raise StalePage("Page did not answer in time") from None
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind == "navigate":
            # The helper's URL was checked for an http(s) scheme and host before it got here.
            try:
                call("Page.navigate", url=request["text"], _response_timeout=NAVIGATION_TIMEOUT)
            except TimeoutError:
                pass  # Chrome keeps loading; Browser.act waits for the new document.
        elif kind == "enter":
            for event in ("keyDown", "keyUp"):
                call("Input.dispatchKeyEvent", type=event, key="Enter", code="Enter", windowsVirtualKeyCode=13,
                     **({"text": "\r"} if event == "keyDown" else {}))
        elif kind == "back":
            entries = call("Page.getNavigationHistory")
            if entries.get("currentIndex", 0) > 0:
                entry = entries["entries"][entries["currentIndex"] - 1]["id"]
                call("Page.navigateToHistoryEntry", entryId=entry, _response_timeout=NAVIGATION_TIMEOUT)
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            # 'unavailable' means the checks failed before any input, so nothing changed on the page.
            target = evaluate("""(action => {
              const e=window.__eveFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:!(e.tagName==='INPUT' && ['checkbox','radio'].includes(e.type)),
                    checkVisibilityCSS:true})) return 'unavailable';
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true'))
                return 'unavailable';
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              // A native select changes by value, not by pointer, so a styled overlay on top of it
              // (Amazon's sort menu, for one) must not block it. Clicks and typing still need a clear target.
              if (action.kind!=='select') {
                if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return 'unavailable';
                // A label drawn over its own checkbox is the same control.
                const hit=document.elementFromPoint(x,y);
                if (!e.contains(hit) && ![...(e.labels||[])].some(l=>l.contains(hit))) return 'unavailable';
              }
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return 'unavailable';
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              if (action.kind==='click') {
                // The agent watches one tab, so links and forms open there instead of in a new one.
                for (const owner of [e.closest('a[target]'), e.closest('form[target]'), e.form]) {
                  if (owner?.target && owner.target!=='_self') owner.target='_self';
                }
              }
              return {x,y};
            })(""" + json.dumps(action) + ")")
            if target == "unavailable":
                raise Unavailable("Target is covered, disabled or gone; nothing was executed.")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
