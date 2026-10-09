"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from browser_harness.admin import ensure_daemon
from browser_harness.daemon import PROFILES
from browser_harness.helpers import _send, cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

# Browser Harness gives a CDP call 5 seconds. A slow site can take longer just to accept a navigation.
NAVIGATION_TIMEOUT = 30
DOWNLOADS = Path(os.environ.get("EVE_DOWNLOADS", Path.home() / "Downloads" / "eve-ultrafast"))
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


# Keys EVE can press. Each is a choice in one keyboard head, never free text.
KEYS = {
    "Escape": 27, "Tab": 9, "ArrowDown": 40, "ArrowUp": 38, "ArrowLeft": 37, "ArrowRight": 39,
    "PageDown": 34, "PageUp": 33, "Home": 36, "End": 35, "Space": 32, "Backspace": 8, "Delete": 46,
}


def saved_files():
    """Downloads folder contents with modification times."""
    if not DOWNLOADS.is_dir():
        return {}
    return {entry.name: entry.stat().st_mtime for entry in os.scandir(DOWNLOADS) if entry.is_file()}


def changed_since(before):
    """Names that are new, or rewritten since the earlier listing."""
    return {name for name, mtime in saved_files().items() if before.get(name) != mtime}


def pending_dialog():
    """An open alert, confirm or prompt. While one is open the page answers nothing else."""
    try:
        return _send({"meta": "pending_dialog"}).get("dialog")
    except Exception:  # noqa: BLE001 - an unreachable daemon shows up on the next real call
        return None


def dialog_page(dialog, url):
    """Show an open dialog to the policy as a page whose only controls answer it."""
    kind, message = dialog.get("type", "alert"), dialog.get("message", "")
    accept = {"prompt": "Type a reply and press OK", "alert": "Press OK"}.get(kind, "Press OK / confirm")
    state = {
        "url": url, "title": f"{kind} dialog", "text": f"The page opened a {kind} dialog: {message}",
        "scroll": {"y": 0, "height": 0}, "dialog": dialog,
        "actions": [{"id": "dialog_accept", "kind": "dialog", "value": kind, "label": accept}]
        + ([] if kind == "alert" else [{"id": "dialog_dismiss", "kind": "dialog", "value": "dismiss",
                                        "label": "Press Cancel"}]),
    }
    state["fingerprint"] = fingerprint(state)
    return state


# Everything a reader would see, for answers: visible light-DOM text, open shadow roots, same-origin frames.
FULL_TEXT = """(() => {
  const parts=[document.body?.innerText || ''];
  const shadows=root => {
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) {
      const walker=document.createTreeWalker(el.shadowRoot,NodeFilter.SHOW_TEXT); let node;
      while ((node=walker.nextNode())) {
        const p=node.parentElement, t=node.textContent.trim();
        if (t && p && !p.closest('script,style') && p.checkVisibility()) parts.push(t);
      }
      shadows(el.shadowRoot);
    }
  };
  shadows(document);
  for (const f of document.querySelectorAll('iframe,frame')) {
    try { if (f.contentDocument?.body) parts.push(f.contentDocument.body.innerText); } catch { /* cross-origin */ }
  }
  return {text:parts.join('\\n').slice(0,60000), pdf:document.contentType==='application/pdf'};
})()"""


def pdf_text(url, limit=60000):
    """Text of a PDF the tab is showing. Chrome's viewer keeps it out of the DOM, so fetch and read it."""
    import io

    import httpx
    from pypdf import PdfReader

    data = httpx.get(url, follow_redirects=True, timeout=30).content
    pages = [f"[Page {n}]\n{page.extract_text() or ''}" for n, page in enumerate(PdfReader(io.BytesIO(data)).pages, 1)]
    return "\n".join(pages)[:limit]


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
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        # Files already there are not this run's; slow downloads that finish later still count. Chrome
        # overwrites a same-named file, so a file counts as new when it was written after this moment.
        self.download_baseline = saved_files()
        try:
            cdp("Browser.setDownloadBehavior", behavior="allow", downloadPath=str(DOWNLOADS))
        except Exception:  # noqa: BLE001 - downloads are a convenience; browsing works without them
            pass
        # In someone's everyday Chrome the tab stays in the background; in our own window it comes forward.
        self.target = cdp("Target.createTarget", url="about:blank", background=not owned)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        # Dialog events reach the daemon only with the Page domain on.
        self.call("Page.enable")
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
        dialog = pending_dialog()
        if dialog:
            self.after_input = None
            return dialog_page(dialog, dialog.get("url", ""))
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
        deadline, attempt = time.monotonic() + 30, 0
        while True:
            try:
                info = browser_operation({"operation": "observe", "session": self.session, "screenshot": screenshot})
                if info.get("pdf"):
                    # Chrome's PDF viewer shows no text to the DOM; give the policy the document's own text.
                    cache = getattr(self, "pdf_cache", {})
                    if info["url"] not in cache:
                        try:
                            cache[info["url"]] = pdf_text(info["url"])
                        except Exception:  # noqa: BLE001 - an unreadable PDF just stays blank
                            cache[info["url"]] = ""
                    self.pdf_cache = cache
                    info["text"] = cache[info["url"]][:6000]
                    info["fingerprint"] = fingerprint(info)
                return info
            except StalePage:
                if time.monotonic() > deadline:
                    raise
                time.sleep(min(0.02 * 2**attempt, 0.25))
                attempt += 1

    def fresh(self, page, action=None):
        if page.get("dialog") or pending_dialog():
            return bool(page.get("dialog")) and pending_dialog() == page["dialog"]
        # Actions that aim at no element (scroll, keys, navigation, waiting) stay meaningful on a page whose
        # clock or ticker keeps changing, so they skip the whole-page comparison.
        unaimed = {"scroll", "wait", "key", "enter", "navigate", "back", "pdf", "save_file", "frame"}
        if action is not None and action["kind"] in unaimed:
            return True
        if action is not None and action["kind"] in {"click", "select", "hover", "upload", "fill"}:
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
        before = saved_files() if action["kind"] == "click" else None
        if action["kind"] == "wait":
            time.sleep(0.1)
        # While the pointer rests where a hover left it, moving away can close a menu and shift the layout.
        # The next click or field first moves the pointer, lets the page settle, and measures again.
        settle = getattr(self, "pointer_parked", False) and action["kind"] in {"click", "fill"}
        result = browser_operation(
            {"operation": "act", "session": self.session, "action": action, "text": text, "settle": settle}
        )
        if action["kind"] == "hover":
            self.pointer_parked = True
        elif result.get("pressed") or action["kind"] in {"navigate", "back", "frame"}:
            self.pointer_parked = False
        self.after_input = action if action["kind"] not in {"wait", "navigate", "enter", "back"} else None
        if action["kind"] == "navigate" and text != page["url"]:
            self.wait_for_navigation(page["url"])
        if action["kind"] in {"navigate", "enter", "back"}:
            self.wait_for_load()
        if action["kind"] == "click":
            self.adopt_popups()
        if before is not None:
            result["downloaded"] = self.new_downloads(before)
        return result

    def downloads_since_start(self, seconds=20):
        """Every file this run saved, waiting for any still in progress to finish."""
        baseline = getattr(self, "download_baseline", {})
        deadline = time.monotonic() + seconds
        while True:
            fresh = changed_since(baseline)
            if not any(n.endswith(".crdownload") for n in fresh) or time.monotonic() > deadline:
                return sorted(n for n in fresh if not n.endswith(".crdownload"))
            time.sleep(0.2)

    def new_downloads(self, before, seconds=1.5):
        """Files a click saved. A download still in progress gets up to a minute to finish."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            fresh = changed_since(before)
            if any(name.endswith(".crdownload") for name in fresh):
                deadline = max(deadline, time.monotonic() + 60) if seconds == 1.5 else deadline
                seconds = 60
            elif fresh:
                return sorted(fresh)
            time.sleep(0.1)
        return sorted(name for name in changed_since(before) if not name.endswith(".crdownload"))

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

    def full_text(self, read_pdf=True):
        """The whole document's text for answers and page memory; a PDF's own text when the tab shows one."""
        found = self.evaluate(FULL_TEXT) or {}
        if found.get("pdf") and read_pdf:
            try:
                url = self.evaluate("location.href")
                return getattr(self, "pdf_cache", {}).get(url) or pdf_text(url)
            except Exception:  # noqa: BLE001 - an unreadable PDF falls back to what the page shows
                pass
        return found.get("text", "")

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
        try:
            return cdp(method, session_id=session, **params)
        except TimeoutError:
            # An alert opened by this input holds the input call open. The dialog is the result.
            if method.startswith("Input.") and pending_dialog():
                return {}
            raise

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
        pressed = False
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
        elif kind == "frame":
            # The frame's own src, read from the page, never written by a model.
            if not str(action.get("value", "")).startswith(("http://", "https://")):
                raise Unavailable("Embedded page has no web address; nothing was opened.")
            try:
                call("Page.navigate", url=action["value"], _response_timeout=NAVIGATION_TIMEOUT)
            except TimeoutError:
                pass
        elif kind == "pdf":
            title = re.sub(r"[^A-Za-z0-9]+", "-", evaluate("document.title") or "page").strip("-")[:60] or "page"
            data = call("Page.printToPDF", printBackground=True, paperWidth=8.27, paperHeight=11.69,
                        _response_timeout=60)["data"]
            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            (DOWNLOADS / f"{title}.pdf").write_bytes(base64.b64decode(data))
            return {"executed": action["id"], "downloaded": [f"{title}.pdf"]}
        elif kind == "save_file":
            # The tab shows a PDF in Chrome's viewer; fetch that same address and keep the file.
            import httpx

            url = evaluate("location.href") or ""
            if not url.startswith(("http://", "https://")):
                raise Unavailable("The open file has no web address; nothing was saved.")
            name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(urlsplit(url).path).name or "file.pdf")[:80] or "file.pdf"
            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            (DOWNLOADS / name).write_bytes(httpx.get(url, follow_redirects=True, timeout=60).content)
            return {"executed": action["id"], "downloaded": [name]}
        elif kind == "dialog":
            accept = action["value"] != "dismiss"
            reply = {"promptText": request.get("text") or ""} if action["value"] == "prompt" else {}
            call("Page.handleJavaScriptDialog", accept=accept, **reply)
        elif kind == "key":
            key = action["value"]
            code = KEYS[key]  # only the fixed key list ever gets here
            name = " " if key == "Space" else key
            for event in ("keyDown", "keyUp"):
                call("Input.dispatchKeyEvent", type="rawKeyDown" if event == "keyDown" else event, key=name,
                     code="Space" if key == "Space" else key, windowsVirtualKeyCode=code,
                     **({"text": " "} if key == "Space" and event == "keyDown" else {}))
        elif kind == "upload":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            found = call("Runtime.evaluate", expression=f"window.__eveFast?.nodes.get({action['node']}) ?? null")
            handle = found.get("result", {}).get("objectId")
            if not handle:
                raise Unavailable("File input is gone; nothing was attached.")
            # Only paths the caller passed to the Agent ever reach this call.
            call("DOM.setFileInputFiles", files=[action["value"]], objectId=handle)
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
            script = """((action, text) => {
              const e=window.__eveFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:!(e.tagName==='INPUT' && ['checkbox','radio'].includes(e.type)),
                    checkVisibilityCSS:true})) return 'unavailable';
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true'))
                return 'unavailable';
              // Elements inside same-origin frames are hit-tested in their frame, then offset to the page.
              const view=e.ownerDocument.defaultView;
              let ox=0, oy=0;
              for (let w=view; w.frameElement; w=w.frameElement.ownerDocument.defaultView) {
                const f=w.frameElement, fr=f.getBoundingClientRect();
                ox+=fr.x+f.clientLeft; oy+=fr.y+f.clientTop;
              }
              // Items inside a scrolled menu or list come into view before anything else is measured.
              if (action.kind!=='select') e.scrollIntoView({block:'nearest',inline:'nearest'});
              const r=e.getBoundingClientRect(), lx=r.x+r.width/2, ly=r.y+r.height/2, x=ox+lx, y=oy+ly;
              // A native select changes by value, not by pointer, so a styled overlay on top of it
              // (Amazon's sort menu, for one) must not block it. Clicks and typing still need a clear target.
              if (action.kind!=='select') {
                if (!r.width || !r.height || lx<0 || ly<0 || lx>=view.innerWidth || ly>=view.innerHeight ||
                    x<0 || y<0 || x>=innerWidth || y>=innerHeight) return 'unavailable';
                // A label drawn over its own checkbox is the same control.
                const scope=e.getRootNode().elementFromPoint ? e.getRootNode() : e.ownerDocument;
                const hit=scope.elementFromPoint(lx,ly);
                if (!e.contains(hit) && ![...(e.labels||[])].some(l=>l.contains(hit))) return 'unavailable';
              }
              // Date, time, colour and slider inputs take a formatted value, not keystrokes.
              if (action.kind==='fill' && e.tagName==='INPUT' &&
                  ['date','time','datetime-local','month','week','color','range'].includes(e.type)) {
                Object.getOwnPropertyDescriptor(view.HTMLInputElement.prototype,'value').set.call(e,text);
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
                return {set:e.value===text};
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
            })(""" + json.dumps(action) + "," + json.dumps(request.get("text")) + ")"
            target = evaluate(script)
            if request.get("settle") and isinstance(target, dict) and "x" in target:
                call("Input.dispatchMouseEvent", type="mouseMoved", x=target["x"], y=target["y"])
                time.sleep(0.05)
                target = evaluate(script)
            if target == "unavailable":
                raise Unavailable("Target is covered, disabled or gone; nothing was executed.")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if "set" in target:
                if not target["set"]:
                    raise Unavailable("The field refused that value; nothing else was typed.")
            elif kind == "hover":
                call("Input.dispatchMouseEvent", type="mouseMoved", x=target["x"], y=target["y"])
            elif kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                pressed = True
                if kind == "fill":
                    # The first click into a frame can leave focus behind; make sure the field has it.
                    evaluate(f"(() => {{ const e=window.__eveFast?.nodes.get({action['node']}); "
                             f"if (e && e.ownerDocument.activeElement!==e) e.focus(); return 1; }})()")
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
                    # Confirm the field holds the text; if keystrokes went astray, set it through its own setter.
                    evaluate("""((node, text) => {
                      const e=window.__eveFast?.nodes.get(node);
                      if (!e || !('value' in e) || e.value===text) return 1;
                      const proto=Object.getPrototypeOf(e), setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;
                      if (!setter) return 0;
                      setter.call(e,text);
                      e.dispatchEvent(new Event('input',{bubbles:true}));
                      e.dispatchEvent(new Event('change',{bubbles:true}));
                      return 2;
                    })(""" + json.dumps(action["node"]) + "," + json.dumps(request["text"]) + ")")
        return {"executed": action["id"], "pressed": pressed}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
