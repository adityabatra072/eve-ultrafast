"""Local-browser freshness/execution regressions. No model calls or external websites."""

import time
from urllib.parse import quote

from eve_ultrafast.browser import Browser, StalePage

HTML = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay does not alter the model's semantic state, but must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")

        # Styled dropdowns put a nearly transparent native select under a decorative label.
        browser.evaluate("""(() => {
          const s=document.querySelector('#category'); s.value='All'; s.style.opacity='0.01';
          const r=s.getBoundingClientRect(), cover=document.createElement('span');
          cover.textContent='Sort'; Object.assign(cover.style,{position:'fixed',left:r.x+'px',top:r.y+'px',
            width:r.width+'px',height:r.height+'px',background:'white',pointerEvents:'none'});
          document.body.append(cover); return 1})()""")
        page = browser.observe(screenshot=False)
        select = next(a for a in page["actions"] if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("a styled overlay does not block a native dropdown")

        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        browser.act(field, page, text="Generated")
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")
        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
    print("\n".join(passed))
    capabilities(passed)
    isolated_frame_and_drag(passed)
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


CAPABILITIES = """<!doctype html><title>Capabilities</title>
<style>.figure .figcaption{display:none}.figure:hover .figcaption{display:block}body{margin:20px}</style>
<iframe id="inner" style="width:300px;height:80px"
  srcdoc="<button onclick='parent.frameClicks=(parent.frameClicks||0)+1'>Inside frame</button>"></iframe>
<div class="figure"><img alt="Profile one" width="80" height="60"
  src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='60'/%3E">
  <div class="figcaption"><a href="#user1">View profile</a></div></div>
<input type="file" id="file" aria-label="Resume">
<input type="date" id="date" aria-label="Departure">
<input type="range" id="range" aria-label="Volume" min="0" max="50">
<a href="data:text/plain,eve" download="eve-guard-download.txt">Download notes</a>
<iframe title="Example" src="https://example.com/" style="width:320px;height:160px"></iframe>
<table><tr><th style="cursor:pointer" onclick="window.sorted=1">Due</th></tr></table>
<img alt="Avatar" width="30" height="30"><img alt="Avatar" width="30" height="30">"""


def capabilities(passed):
    import os
    import tempfile

    from eve_ultrafast.browser import DOWNLOADS

    browser = Browser("data:text/html," + quote(CAPABILITIES))
    try:
        time.sleep(1)
        page = browser.observe(screenshot=False)
        inside = next(a for a in page["actions"] if a["label"] == "Inside frame")
        browser.act(inside, page)
        assert browser.evaluate("window.frameClicks") == 1
        passed.append("a button inside a same-origin frame is clicked")

        page = browser.observe(screenshot=False)
        hover = next(a for a in page["actions"] if a["kind"] == "hover")
        browser.act(hover, page)
        page = browser.observe(screenshot=False)
        assert any(a["label"] == "View profile" for a in page["actions"])
        passed.append("hovering reveals a hidden caption link")

        upload = next(a for a in page["actions"] if a["kind"] == "file")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("resume")
        browser.act({**upload, "kind": "upload", "value": handle.name}, page)
        assert browser.evaluate("document.querySelector('#file').files[0].name") == os.path.basename(handle.name)
        os.unlink(handle.name)
        passed.append("an allowed file is attached to a file input")

        page = browser.observe(screenshot=False)
        date = next(a for a in page["actions"] if a["kind"] == "fill" and a.get("input_type") == "date")
        browser.act(date, page, text="2026-11-20")
        assert browser.evaluate("document.querySelector('#date').value") == "2026-11-20"
        page = browser.observe(screenshot=False)
        slider = next(a for a in page["actions"] if a["kind"] == "fill" and a.get("input_type") == "range")
        assert slider["max"] == "50"
        browser.act(slider, page, text="30")
        assert browser.evaluate("document.querySelector('#range').value") == "30"
        passed.append("date and slider inputs take formatted values")

        page = browser.observe(screenshot=False)
        link = next(a for a in page["actions"] if a["label"] == "Download notes")
        target = DOWNLOADS / "eve-guard-download.txt"
        target.unlink(missing_ok=True)
        result = browser.act(link, page)
        assert result.get("downloaded") == ["eve-guard-download.txt"] and target.read_text() == "eve", result
        target.unlink()
        passed.append("a download is saved and reported")

        page = browser.observe(screenshot=False)
        header = next(a for a in page["actions"] if a["label"] == "Due")
        browser.act(header, page)
        assert browser.evaluate("window.sorted") == 1
        passed.append("a header clickable only by script is offered and clicked")

        page = browser.observe(screenshot=False)
        frame = next(a for a in page["actions"] if a["kind"] == "frame")
        assert frame["value"] == "https://example.com/"
        browser.act(frame, page)
        assert browser.evaluate("location.href") == "https://example.com/"
        passed.append("a cross-origin embedded page opens by itself")
    finally:
        browser.close()


OUTER = """<!doctype html><title>Outer</title><body style="margin:20px">
<script>window.got=null; addEventListener('message', e => { window.got = e.data; });</script>
<iframe src="http://127.0.0.1:PORT2/inner.html" style="width:400px;height:150px;border:1px solid"></iframe>
<div id="box" draggable="true" style="width:80px;height:40px;background:#cde">Card</div>
<div id="zone" class="dropzone" style="width:200px;height:60px;border:2px dashed"
  ondragover="event.preventDefault()" ondrop="window.dropped='box'">Done column</div>
<div id="slide" class="draggable" style="position:relative;width:60px;height:30px;background:#ecd">Handle</div>
<div id="target" class="droppable" style="width:200px;height:40px;border:1px solid">Target area</div>
<script>
  const h=document.querySelector('#slide'), t=document.querySelector('#target'); let down=false;
  h.addEventListener('mousedown',()=>{down=true}); addEventListener('mouseup',e=>{
    if (down) { const r=t.getBoundingClientRect();
      if (e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom) window.mouseDropped=true; }
    down=false; });
</script>"""
INNER = """<!doctype html><title>Inner</title><body>
<label>Your name <input id="name"></label>
<button onclick="parent.postMessage(document.querySelector('#name').value,'*')">Send to page</button>"""


def isolated_frame_and_drag(passed):
    import socket
    import tempfile
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    def free_port():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    folder, port1, port2 = Path(tempfile.mkdtemp()), free_port(), free_port()
    (folder / "outer.html").write_text(OUTER.replace("PORT2", str(port2)))
    (folder / "inner.html").write_text(INNER)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    servers = [ThreadingHTTPServer(("127.0.0.1", port), partial(Quiet, directory=folder)) for port in (port1, port2)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    # localhost and 127.0.0.1 are different sites, so Chrome runs the frame in its own process.
    browser = Browser(f"http://localhost:{port1}/outer.html")
    try:
        time.sleep(1)
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a.get("frame") and a["kind"] == "fill")
        browser.act(field, page, text="Asha Rao")
        page = browser.observe(screenshot=False)
        send = next(a for a in page["actions"] if a.get("frame") and a["label"] == "Send to page")
        browser.act(send, page)
        time.sleep(0.3)
        assert browser.evaluate("window.got") == "Asha Rao", browser.evaluate("window.got")
        passed.append("typing and clicking inside a cross-origin frame")

        page = browser.observe(screenshot=False)
        card = next(a for a in page["actions"] if a["kind"] == "drag" and a["label"] == "Card")
        zone = next(a for a in page["actions"] if a["kind"] == "drop" and a["label"] == "Done column")
        browser.act({**card, "drop": zone}, page)
        assert browser.evaluate("window.dropped") == "box"
        page = browser.observe(screenshot=False)
        handle = next(a for a in page["actions"] if a["kind"] == "drag" and a["label"] == "Handle")
        target = next(a for a in page["actions"] if a["kind"] == "drop" and a["label"] == "Target area")
        browser.act({**handle, "drop": target}, page)
        assert browser.evaluate("window.mouseDropped") is True
        passed.append("HTML5 and mouse-driven drag and drop")
    finally:
        browser.close()
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    main()
