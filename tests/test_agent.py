"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from eve_ultrafast import agent as loop
from eve_ultrafast import model
from eve_ultrafast.browser import StalePage, browser_operation, fingerprint


@pytest.fixture(autouse=True)
def no_saved_wally_key(monkeypatch, tmp_path):
    # Tests never read the developer's real wally sign-in.
    monkeypatch.setattr(model, "WALLY_CREDENTIALS", tmp_path / "credentials.json")


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid EVE"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []
    p = page()
    p["actions"].insert(1, {"id": "e4", "kind": "fill", "label": "Author", "role": "textbox", "value": "", "node": 40})

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1", "2"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid EVE"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["state"] == "checked, not selected"
        assert "current_value" not in target["criteria"]["1"]
        assert '[1] checkbox "Free cancellation" (checked, not selected) · CLICK' in body["state"]["elements"]
        assert set(questions["operation"]["instructions"]["rules"]) <= set(target["instructions"]["rules"])
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def many_links(count):
    p = page()
    p["actions"] = [
        {"id": f"l{i}", "kind": "click", "label": f"Story {i}", "role": "link", "value": "", "node": 100 + i}
        for i in range(1, count + 1)
    ] + [{"id": "wait", "kind": "wait", "label": "Wait"}]
    return p


def depth(value):
    if isinstance(value, dict):
        return 1 + max(map(depth, value.values()), default=0)
    if isinstance(value, list):
        return 1 + max(map(depth, value), default=0)
    return 0


def test_state_fits_the_system_one_nesting_limit(monkeypatch):
    p = page()
    p["actions"].append({
        "id": "s1", "kind": "select", "label": "Cabin → Economy", "role": "combobox", "value": "economy",
        "current_value": "business", "node": 50,
    })
    sent = []
    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda _u, _k, body: sent.append(body) or {
        "model": "eve", "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "WAIT")},
    })
    model.choose(p, "Fly economy", [{"action": "Go", "kind": "click", "text": None, "page_changed": True}])
    state = sent[0]["state"]
    # Arrays and objects inside the state may nest three levels in all.
    assert all(depth(v) <= 2 for v in state.values())
    assert '[3] combobox "Cabin" value="business" · SELECT · options: [3:1] Cabin → Economy' in state["elements"]


def test_history_reaches_the_model_as_numbered_lines_with_unsubmitted_fields():
    history = [
        {"action": "Destination", "kind": "fill", "role": "searchbox", "text": "Lisbon", "page_changed": True},
        {"action": "Free cancellation", "kind": "click", "role": "checkbox", "text": None, "page_changed": False},
    ]
    assert model.history_text(history) == '1. fill Destination "Lisbon"\n2. click Free cancellation (no visible change)'
    assert model.unsubmitted(history) == "Destination, Free cancellation"
    history.append({"action": "Find stays", "kind": "click", "role": "button", "text": None, "page_changed": True})
    assert model.unsubmitted(history) == "none"
    assert model.history_text([]) == "none"


def test_single_candidate_head_needs_no_question(monkeypatch):
    def post(_url, _key, body):
        assert set(body["questions"]) == {"operation", "click_target"}
        operations = body["questions"]["operation"]["criteria"]
        return {"model": "eve", "answers": {"operation": choice(operations, "TYPE_TEXT")}}

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert d["choice"] == "e1" and d["rounds"] == 1


@pytest.mark.parametrize("count", [27, 60, 156, 700])
def test_large_heads_split_and_finish_with_a_final_round(monkeypatch, count):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        questions = body["questions"]
        assert all(2 <= len(q["criteria"]) <= model.MAX_OPTIONS for q in questions.values())
        answers = {}
        for name, q in questions.items():
            ids = list(q["criteria"])
            # Every chunk sounds sure, so the split head needs a final round.
            answers[name] = choice(ids, "CLICK" if name == "operation" else ("2" if "2" in ids else ids[0]))
        return {"model": "eve", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 0}}

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(many_links(count), "Open story 2", [])
    rounds = 3 if count > model.MAX_OPTIONS**2 else 2
    assert len(calls) == rounds and d["rounds"] == rounds
    first, final = calls[0], calls[-1]
    heads = [q for name, q in first["questions"].items() if name.startswith("click_target_")]
    offered = [i for q in heads for i in q["criteria"] if i != model.NONE]
    assert sorted(offered) == sorted(str(i) for i in range(1, count + 1))
    assert set(final["questions"]) == {"click_target"} and "2" in final["questions"]["click_target"]["criteria"]
    assert final["state"] == first["state"]
    assert d["choice"] == "l2" and d["usage"]["input_tokens"] == 10 * rounds


def test_one_sure_chunk_settles_a_split_head_without_a_final_round(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        answers = {"operation": choice(body["questions"]["operation"]["criteria"], "CLICK")}
        for name, q in body["questions"].items():
            if name == "operation":
                continue
            ids = list(q["criteria"])
            assert ids[-1] == model.NONE and len(ids) <= model.MAX_OPTIONS
            sure = "45" in ids
            p = {i: 0.0 for i in ids}
            p.update({"45": 0.95, "46": 0.05} if sure else {model.NONE: 0.7, ids[0]: 0.2, ids[1]: 0.1})
            answers[name] = {"choice": max(p, key=p.get), "confidence": 0.9, "probabilities": p}
        return {"model": "eve", "answers": answers}

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(many_links(70), "Open story 45", [])
    assert len(calls) == 1 and d["rounds"] == 1
    assert d["choice"] == "l45" and model.NONE not in d["target_probabilities"]


def test_none_is_never_a_target(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        answers = {}
        for name, q in body["questions"].items():
            if name == "operation":
                answers[name] = choice(q["criteria"], "CLICK")
            else:
                ids = list(q["criteria"])
                answers[name] = choice(ids, model.NONE if model.NONE in ids else ids[0])
        return {"model": "eve", "answers": answers}

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(many_links(70), "Open nothing in particular", [])
    assert len(calls) == 2 and d["choice"].startswith("l")
    assert model.NONE not in calls[1]["questions"]["click_target"]["criteria"]


def test_split_head_is_not_requested_again_for_other_operations(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {"model": "eve", "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "WAIT")}}

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    assert model.choose(many_links(80), "Wait for stories", [])["choice"] == "wait"
    assert len(calls) == 1


def test_missing_decision_credential_stops_before_any_request(monkeypatch):
    monkeypatch.delenv("RUNANYWHERE_API_KEY", raising=False)
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="RUNANYWHERE_API_KEY"):
        model.choose(page(), "Find a book", [])
    post.assert_not_called()


def test_saved_wally_sign_in_is_used_when_no_key_is_set(monkeypatch, tmp_path):
    monkeypatch.delenv("RUNANYWHERE_API_KEY", raising=False)
    (tmp_path / "credentials.json").write_text('{"access_token": "sk-saved"}')
    assert model.api_key() == "sk-saved"
    monkeypatch.setenv("RUNANYWHERE_API_KEY", "sk-env")
    assert model.api_key() == "sk-env"


def test_text_helper_defaults_to_wally_without_reasoning_flags(monkeypatch):
    for name in ("TEXT_MODEL_API_KEY", "TEXT_MODEL_BASE_URL", "TEXT_MODEL", "TEXT_MODEL_REASONING"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUNANYWHERE_API_KEY", "ra-key")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"London"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    assert model.field_text({"goal": "Fly to London"})[0] == "London"
    url, key, body = post.call_args.args
    assert url == "https://inference.runanywhere.ai/v1/chat/completions" and key == "ra-key"
    assert body["model"] == "deepseek-v4.1-flash" and "reasoning" not in body


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("RUNANYWHERE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="RUNANYWHERE_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    a.unavailable = set()
    a.files = []
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import eve_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import eve_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import eve_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-11-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Fri, Nov 20"),
                ("Nonstop flight on Friday, November 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    import eve_ultrafast.browser as browser

    for name in ("BU_CDP_URL", "BU_CDP_WS", "BU_BROWSER_ID", "EVE_HEADLESS"):
        # setenv first so the variable connect() writes is removed again after the test.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setattr(browser, "PROFILES", [])
    monkeypatch.setattr(browser, "CHROME_PROFILE", tmp_path / "chrome")
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser.subprocess, "Popen", Mock())
    return browser


def test_explicit_cdp_url_is_used_as_is(harness, monkeypatch):
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9999")
    assert harness.connect() is False
    harness.subprocess.Popen.assert_not_called()
    harness.ensure_daemon.assert_called_once()


def test_everyday_chrome_with_debugging_on_is_used(harness, monkeypatch):
    monkeypatch.setattr(harness, "debuggable_profile", lambda: True)
    assert harness.connect() is False
    harness.subprocess.Popen.assert_not_called()
    assert "BU_CDP_URL" not in harness.os.environ


def test_dedicated_chrome_starts_when_nothing_is_debuggable(harness, monkeypatch):
    ports = iter([False, False, True])
    monkeypatch.setattr(harness, "listening", lambda _port: next(ports))
    monkeypatch.setattr(harness, "CHROME_BINARIES", (harness.sys.executable,))
    assert harness.connect() is True
    command = harness.subprocess.Popen.call_args.args[0]
    assert f"--remote-debugging-port={harness.CHROME_PORT}" in command
    assert f"--user-data-dir={harness.CHROME_PROFILE}" in command
    assert harness.os.environ["BU_CDP_URL"] == f"http://127.0.0.1:{harness.CHROME_PORT}"
    harness.ensure_daemon.assert_called_once()


def test_running_dedicated_chrome_is_reused(harness, monkeypatch):
    monkeypatch.setattr(harness, "listening", lambda _port: True)
    assert harness.connect() is True
    harness.subprocess.Popen.assert_not_called()


def test_navigate_is_always_offered(monkeypatch):
    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    sent = []
    monkeypatch.setattr(model, "post_json", lambda _u, _k, body: sent.append(body) or {
        "model": "eve", "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "NAVIGATE")},
    })
    blank = {"url": "about:blank", "title": "", "text": "", "actions": []}
    d = model.choose(blank, "Go to Hacker News", [])
    assert d["choice"] == "NAVIGATE" and d["target"] is None
    assert set(sent[0]["questions"]) == {"operation"}


@pytest.mark.parametrize("content, url", [
    ('{"url":"https://news.ycombinator.com/"}', "https://news.ycombinator.com/"),
    ('{"url":" http://example.com/search?q=a "}', "http://example.com/search?q=a"),
])
def test_navigate_url_accepts_plain_web_addresses(monkeypatch, content, url):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    assert model.page_url({"goal": "Go"})[0] == url


@pytest.mark.parametrize("content", [
    '{"url":"javascript:alert(1)"}', '{"url":"file:///etc/passwd"}', '{"url":"chrome://settings"}',
    '{"url":"https://"}', '{"url":"news.ycombinator.com"}', '{"url":"https://a b.com"}', '{"text":"https://x.com"}',
    '{"url":null}', "Sure! https://x.com",
])
def test_navigate_url_rejects_anything_else(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing opened"):
        model.page_url({"goal": "Go"})


def test_navigate_executes_the_generated_url_and_logs_it(runner, monkeypatch):
    writer = Mock(return_value=("https://news.ycombinator.com/", {"model": "test", "latency_ms": 5}))
    monkeypatch.setattr(loop, "page_url", writer)
    runner.state["decision"] = {**decision("NAVIGATE"), "operation": "NAVIGATE", "target": None,
                                "probabilities": {"NAVIGATE": 0.9}}
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    action, _page = runner.state["browser"].act.call_args.args
    assert action["kind"] == "navigate"
    assert runner.state["browser"].act.call_args.kwargs["text"] == "https://news.ycombinator.com/"
    assert runner.state["history"][-1]["kind"] == "navigate"
    assert runner.state["history"][-1]["text"] == "https://news.ycombinator.com/"


def test_navigation_clears_unsubmitted_fields():
    history = [{"action": "Search", "kind": "fill", "text": "x"}, {"action": "Open", "kind": "navigate", "text": "https://a.com"}]
    assert model.unsubmitted(history) == "none"


def test_browser_navigates_with_cdp_and_never_types(monkeypatch):
    import eve_ultrafast.browser as browser

    cdp = Mock(return_value={})
    monkeypatch.setattr(browser, "cdp", cdp)
    browser_operation({"operation": "act", "session": "s", "action": {"id": "NAVIGATE", "kind": "navigate"},
                       "text": "https://example.com/"})
    assert cdp.call_args_list == [
        (("Page.navigate",), {"session_id": "s", "url": "https://example.com/", "_response_timeout": 30})
    ]


BAD_START_URLS = [
    "javascript:alert(1)", "file:///etc/passwd", "ftp://x.com", "https://", "data:text/html,x",
    "localhost:8080", "https://a.com:99999",
]


@pytest.mark.parametrize("url", BAD_START_URLS)
def test_inspector_rejects_non_web_start_urls(monkeypatch, url):
    from eve_ultrafast import demo

    monkeypatch.setattr(demo, "Agent", Mock(side_effect=AssertionError("no browser for a rejected URL")))
    with pytest.raises(ValueError, match="http"):
        demo.command("reset", {"scenario": "web", "goal": "Go", "url": url})


@pytest.mark.parametrize("url, start", [
    ("news.ycombinator.com", "https://news.ycombinator.com"),
    ("http://127.0.0.1:8766/fixture.html", "http://127.0.0.1:8766/fixture.html"),
    ("", "about:blank"),
])
def test_inspector_web_scenario_start_page(monkeypatch, url, start):
    from eve_ultrafast import demo

    agent = Mock(state={}, snapshot=Mock(return_value={"status": "ready"}))
    monkeypatch.setattr(demo, "Agent", Mock(return_value=agent))
    monkeypatch.setattr(demo, "AGENT", None)
    demo.command("reset", {"scenario": "web", "goal": "Go", "url": url})
    assert demo.Agent.call_args.args[0] == start
    monkeypatch.setattr(demo, "AGENT", None)


def test_refused_dropdown_is_unavailable_not_fatal(monkeypatch):
    import eve_ultrafast.browser as browser

    monkeypatch.setattr(browser, "cdp", Mock(return_value={"result": {"type": "string", "value": "unavailable"}}))
    with pytest.raises(browser.Unavailable):
        browser_operation({"operation": "act", "session": "s", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})


def test_refused_target_is_not_offered_again_on_that_page(runner, monkeypatch):
    from eve_ultrafast.browser import Unavailable

    runner.state["decision"] = decision("e3")
    runner.state["browser"].act.side_effect = Unavailable("covered")
    with pytest.raises(Unavailable):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    seen = []
    monkeypatch.setattr(loop, "choose", lambda p, _g, _h: seen.append(p) or decision("e1"))
    runner.state["status"] = "ready"
    runner.command("predict", {})
    assert "e3" not in {a["id"] for a in seen[0]["actions"]}
    assert "e1" in {a["id"] for a in seen[0]["actions"]}


def test_enter_is_offered_right_after_typing_and_back_after_leaving_a_page():
    page_now = {"url": "https://a.com/results"}
    assert set(model.synthetic_operations(page_now, [])) == {"NAVIGATE", "SAVE_PDF"}
    typed = [{"kind": "fill", "action": "Search", "url": "https://a.com/results"}]
    assert "PRESS_ENTER" in model.synthetic_operations(page_now, typed)
    moved = [{"kind": "click", "action": "Next", "url": "https://a.com/"}, {"kind": "click", "url": "https://a.com/results"}]
    operations = model.synthetic_operations(page_now, moved)
    assert "GO_BACK" in operations and "PRESS_ENTER" not in operations


@pytest.mark.parametrize("kind, calls", [
    ("enter", ["Input.dispatchKeyEvent", "Input.dispatchKeyEvent"]),
    ("back", ["Page.getNavigationHistory", "Page.navigateToHistoryEntry"]),
])
def test_enter_and_back_use_cdp_only(monkeypatch, kind, calls):
    import eve_ultrafast.browser as browser

    cdp = Mock(return_value={"currentIndex": 1, "entries": [{"id": 7}, {"id": 8}]})
    monkeypatch.setattr(browser, "cdp", cdp)
    browser_operation({"operation": "act", "session": "s", "action": {"id": kind, "kind": kind}})
    assert [c.args[0] for c in cdp.call_args_list] == calls
    if kind == "back":
        assert cdp.call_args.kwargs["entryId"] == 7


def test_done_reads_an_answer_off_the_final_page(runner, monkeypatch):
    answer = ({"answer": "Logitech G203, ₹1,495", "complete": True}, {"model": "t", "latency_ms": 3})
    monkeypatch.setattr(loop, "final_answer", Mock(return_value=answer))
    runner.state["decision"] = {**decision("DONE"), "operation": "DONE", "target": None, "probabilities": {"DONE": 1.0}}
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "done" and runner.state["answer"] == "Logitech G203, ₹1,495"
    runner.state["browser"].act.assert_not_called()


def test_a_missing_answer_does_not_fail_the_run(runner, monkeypatch):
    monkeypatch.setattr(loop, "final_answer", Mock(return_value=(None, {"model": "t", "latency_ms": 3})))
    runner.state["decision"] = {**decision("DONE"), "operation": "DONE", "target": None, "probabilities": {"DONE": 1.0}}
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "done" and runner.state["answer"] is None



def test_repeating_one_action_on_one_page_stops_with_an_answer(runner, monkeypatch):
    saw = ({"answer": "Saw the iPad page", "complete": False}, {"model": "t", "latency_ms": 1})
    monkeypatch.setattr(loop, "final_answer", Mock(return_value=saw))
    page_now = runner.state["page"]
    for n in range(4):
        page_now["text"] = f"changed {n}"  # each click changes the page, so only the repeat guard can stop it
        runner.state["browser"].observe.return_value = {**page_now, "fingerprint": f"f{n}"}
        runner.state["decision"] = decision("e3")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
        if runner.state["status"] == "blocked":
            break
    assert runner.state["status"] == "blocked" and len(runner.state["history"]) == 4
    assert runner.state["stop_reason"] == "Repeating the same action on the same page"
    assert runner.state["answer"] == "Saw the iPad page"


def test_action_budget_ends_the_run_instead_of_raising(runner, monkeypatch):
    monkeypatch.setattr(loop, "final_answer", Mock(return_value=(None, {"model": "t", "latency_ms": 1})))
    runner.state["history"] = [{"kind": "click", "action": f"a{i}", "url": "u"} for i in range(loop.MAX_STEPS)]
    runner.state["decision"] = decision("e3")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked" and "budget" in runner.state["stop_reason"]
    runner.state["browser"].act.assert_not_called()


def test_visited_pages_list_earlier_urls_only():
    history = [{"url": "https://a.com/mac"}, {"url": "https://a.com/ipad"}, {"url": "https://a.com/mac"}]
    assert model.visited_pages("https://a.com/ipad", history) == "https://a.com/mac"
    assert model.visited_pages("https://a.com/", []) == "none"


def test_a_slow_site_does_not_end_the_run(monkeypatch):
    import eve_ultrafast.browser as browser

    monkeypatch.setattr(browser, "cdp", Mock(side_effect=TimeoutError("Page.navigate timed out")))
    result = browser_operation({"operation": "act", "session": "s", "action": {"id": "NAVIGATE", "kind": "navigate"},
                                "text": "https://slow.example/"})
    assert result["executed"] == "NAVIGATE"



def test_a_premature_done_goes_back_to_work_with_what_is_missing(runner, monkeypatch):
    verdict = ({"answer": "Only page 6 of 20", "complete": False, "missing": "open the last page"}, {"model": "t"})
    monkeypatch.setattr(loop, "final_answer", Mock(return_value=verdict))
    runner.rechecks = 0
    for _ in range(3):
        runner.state["decision"] = {**decision("DONE"), "operation": "DONE", "target": None,
                                    "probabilities": {"DONE": 1.0}}
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
        if runner.state["status"] == "done":
            break
    checks = [h for h in runner.state["history"] if h["kind"] == "check"]
    assert len(checks) == 2 and "open the last page" in checks[0]["action"]
    assert runner.state["status"] == "done"  # after two rechecks EVE's DONE stands


def test_a_run_stopped_by_a_limit_is_done_when_the_answer_was_found(runner, monkeypatch):
    found = ({"answer": "Jim Henson and Bob Marley", "complete": True}, {"model": "t"})
    monkeypatch.setattr(loop, "final_answer", Mock(return_value=found))
    runner.finish("blocked", runner.state["page"], "Scrolled 15 times in a row")
    assert runner.state["status"] == "done" and runner.state["stop_reason"] == "Scrolled 15 times in a row"


def test_answer_parses_complete_and_missing(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    content = '{"answer": "Page 6 of 20", "complete": false, "missing": "go to page 20"}'
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    verdict, _ = model.final_answer("Last laptop?", {"url": "u", "title": "t", "text": "x"}, [])
    assert verdict == {"answer": "Page 6 of 20", "complete": False, "missing": "go to page 20"}



def test_long_pages_keep_the_passage_the_goal_quotes():
    filler = "\n".join(f"Paragraph {i} about routers and cables." for i in range(3000))
    text = "Internet\n" + filler + "\nThe vast majority of computer surveillance involves data.\n" + filler
    kept = model.relevant(text, "Find the sentence that starts with 'The vast majority of computer'", 16000)
    assert len(kept) <= 16000 + 200
    assert kept.startswith("Internet") and "vast majority of computer surveillance" in kept


def test_short_pages_pass_through_whole():
    assert model.relevant("short page", "anything", 16000) == "short page"



def test_a_page_too_long_for_eve_is_retried_smaller(monkeypatch):
    p = many_links(40)
    for i, a in enumerate(p["actions"]):
        if a["kind"] == "click":  # controls such as WAIT have no position on the page
            a["rect"] = {"x": 0, "y": 30 * i, "w": 10, "h": 10}
    p["h"] = 600
    sizes = []

    def post(_url, _key, body):
        sizes.append(body["state"]["elements"].count("\n") + 1)
        if len(sizes) == 1:
            raise model.TooLong("too long")
        return {"model": "eve", "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "WAIT")}}

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    assert model.choose(p, "Wait", [])["choice"] == "wait"
    assert sizes[1] < sizes[0]  # the retry dropped the links below the fold



def test_an_action_that_changed_nothing_is_not_offered_again_on_the_same_page(runner, monkeypatch):
    runner.state["decision"] = decision("e3")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})  # observe returns the same page
    assert runner.state["history"][-1]["page_changed"] is False
    seen = []
    monkeypatch.setattr(loop, "choose", lambda p, _g, _h: seen.append(p) or decision("e1"))
    runner.command("predict", {})
    assert "e3" not in {a["id"] for a in seen[0]["actions"]}


def test_drag_asks_for_source_and_drop_zone_in_one_request(monkeypatch):
    p = page()
    p["actions"][:0] = [
        {"id": "d1", "kind": "drag", "label": "Card", "role": "draggable", "node": 70},
        {"id": "z1", "kind": "drop", "label": "Done column", "role": "drop zone", "node": 71},
        {"id": "z2", "kind": "drop", "label": "Doing column", "role": "drop zone", "node": 72},
    ]
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        q = body["questions"]
        assert "drop_target" in q and "DROP" not in q["operation"]["criteria"]
        return {"model": "eve", "answers": {
            "operation": choice(q["operation"]["criteria"], "DRAG"),
            "drop_target": choice(q["drop_target"]["criteria"], "2"),
        }}

    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Move the card to Done", [])
    assert len(calls) == 1 and d["choice"] == "d1" and d["drop"] == "z1"


def test_drag_is_not_offered_without_a_drop_zone(monkeypatch):
    p = page()
    p["actions"].insert(0, {"id": "d1", "kind": "drag", "label": "Card", "role": "draggable", "node": 70})
    monkeypatch.setenv("RUNANYWHERE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda _u, _k, body: {"model": "eve", "answers": {
        "operation": choice(body["questions"]["operation"]["criteria"], "WAIT")}})
    assert "DRAG" not in model.choose(p, "x", [])["operation_probabilities"]


def test_a_helper_that_gives_no_value_twice_sets_the_field_aside(runner, monkeypatch):
    helper = Mock(side_effect=ValueError("no value"))
    monkeypatch.setattr(loop, "field_text", helper)
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2
    assert (runner.state["page"]["fingerprint"], "e1") in runner.unavailable
    runner.state["browser"].act.assert_not_called()



@pytest.mark.parametrize("answer", ["oops", None, 42, {"no": "actions"}])
def test_a_frame_that_answers_garbage_is_skipped(monkeypatch, answer):
    import eve_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.target, b.session, b.frame_sessions = "page", "s", {"f1": "fs"}
    b.frame_box = Mock(return_value=(0, 0, 300, 200))

    def cdp(method, **_params):
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "f1", "type": "iframe", "parentId": "page", "url": "https://ads.test/"}]}
        return {"result": {"value": answer}}

    monkeypatch.setattr(browser, "cdp", cdp)
    info = {"url": "https://shop.test/", "text": "Shop", "actions": [], "scroll": {}, "w": 1120, "h": 780}
    assert b.merge_frames(dict(info))["actions"] == []



@pytest.mark.parametrize("page, expected", [
    ({"title": "Just a moment...", "text": "", "url": "https://x.com/"}, True),
    ({"title": "Dictionary", "text": "Performing security verification", "url": "https://x.com/"}, True),
    ({"title": "Google", "text": "", "url": "https://www.google.com/sorry/index?continue=x"}, True),
    ({"title": "Login", "text": "Sign in", "url": "https://x.com/",
      "frames": {"f": {"url": "https://www.google.com/recaptcha/api2/anchor?k=1&size=normal"}}}, True),
    ({"title": "Login", "text": "Sign in", "url": "https://x.com/",
      "frames": {"f": {"url": "https://www.google.com/recaptcha/api2/anchor?k=1&size=invisible"}}}, False),
    ({"title": "Shop", "text": "Add to cart", "url": "https://x.com/"}, False),
])
def test_verification_walls_are_recognised(page, expected):
    from eve_ultrafast.browser import verification_wall

    page.setdefault("actions", [])
    assert bool(verification_wall(page)) is expected


def test_a_wall_that_never_clears_stops_the_run_with_a_reason(runner, monkeypatch):
    wall = {**runner.state["page"], "title": "Just a moment...", "text": "Verifying you are human"}
    runner.state["page"] = wall
    runner.state["browser"].observe.return_value = wall
    runner.state["browser"].human_wait = 0
    monkeypatch.setattr(loop.time, "sleep", lambda _s: None)
    clock = iter(range(0, 10000, 5))
    monkeypatch.setattr(loop.time, "monotonic", lambda: next(clock))
    blocked = ({"answer": "Blocked by a check", "complete": False}, {})
    monkeypatch.setattr(loop, "final_answer", Mock(return_value=blocked))
    runner.state["status"] = "ready"
    runner.command("predict", {})
    assert runner.state["status"] == "blocked" and "human verification" in runner.state["stop_reason"]



def test_canvas_cells_turn_into_points_inside_the_canvas():
    from eve_ultrafast import canvas

    rect = {"x": 30, "y": 100, "w": 600, "h": 300}
    assert len(canvas.labels()) == 24 and canvas.labels()[0] == "A1" and canvas.labels()[-1] == "F4"
    box = canvas.cell_box(rect, "C2")
    assert box == {"x": 230.0, "y": 175.0, "w": 100.0, "h": 75.0}
    assert canvas.point(box, "5") == (280.0, 212.5)  # the middle of the middle sub-cell


def test_an_invalid_cell_is_refused(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "t")
    reply = {"choices": [{"message": {"content": '{"cell": "Z9"}'}}]}
    monkeypatch.setattr(model, "post_json", Mock(return_value=reply))
    with pytest.raises(ValueError, match="no valid cell"):
        model.canvas_cell("Click green", [], "img", ["A1", "B1"], 1)



def test_a_site_that_keeps_failing_stops_after_three_tries(runner, monkeypatch):
    broken = {**runner.state["page"], "text": "No results returned. Oops, something went wrong. Reload"}
    runner.state["page"] = broken
    runner.state["history"] = [
        {"kind": "click", "action": "Reload", "url": "u", "site_error": True, "page_changed": True} for _ in range(3)
    ]
    monkeypatch.setattr(loop, "final_answer", Mock(return_value=({"answer": "Site broken", "complete": False}, {})))
    runner.state["status"] = "ready"
    runner.rechecks = 2
    runner.command("predict", {})
    assert runner.state["status"] == "blocked" and "kept failing" in runner.state["stop_reason"]


def test_ordinary_pages_are_not_site_errors():
    from eve_ultrafast.browser import site_error

    assert site_error({"title": "Shop", "text": "Add to cart"}) is None
    article = "Something went wrong in our deploy. " + "Details of the incident and the fix. " * 100
    assert site_error({"title": "Postmortem", "text": article}) is None
    assert site_error({"title": "504 Gateway Time-out", "text": ""}) == "504 gateway time-out"
