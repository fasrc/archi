"""Unit tests for the Slack bot (openspec/changes/add-slack-service).

The bot is a thin client of the chat app's /v1 API. Every seam is tested without
network access: Slack clients and the HTTP session are mocks.
"""

import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from src.interfaces import slack_bot
from src.interfaces.slack_bot import (
    ERROR_TEXT,
    PLACEHOLDER_TEXT,
    ArchiApiError,
    EventDeduper,
    SlackBot,
    ask_archi,
    build_messages,
    should_answer,
    strip_mention,
    thread_key,
    to_mrkdwn,
    wait_for_model,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT = "UBOT"


# ---------------------------------------------------------------------------
# Dependency pin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["pyproject.toml", "requirements/requirements-base.txt"]
)
def test_slack_sdk_is_pinned_exactly(path):
    text = (REPO_ROOT / path).read_text()
    assert len(re.findall(r"slack_sdk==3\.45\.0", text)) == 1


# ---------------------------------------------------------------------------
# thread_key
# ---------------------------------------------------------------------------


def test_thread_key_joins_team_channel_and_thread():
    assert thread_key("T1", "C1", "1700.0001") == "slack:T1:C1:1700.0001"


def test_thread_key_fits_the_header_limit():
    assert len(thread_key("T" * 12, "C" * 12, "1700000000.000100")) <= 200


# ---------------------------------------------------------------------------
# strip_mention
# ---------------------------------------------------------------------------


def test_strip_mention_removes_every_mention_and_trims():
    assert strip_mention("<@UBOT> what is <@U2> scratch?  ") == "what is scratch?"


def test_strip_mention_keeps_internal_whitespace():
    # Pasted code keeps its lines and indentation (PR #598 review).
    text = "<@UBOT> why does this fail?\n```\ndef f():\n    return 1\n```"
    assert strip_mention(text) == (
        "why does this fail?\n```\ndef f():\n    return 1\n```"
    )


def test_strip_mention_keeps_plain_text():
    assert strip_mention("no mention") == "no mention"


def test_strip_mention_handles_none():
    assert strip_mention(None) == ""


# ---------------------------------------------------------------------------
# should_answer
# ---------------------------------------------------------------------------


def _mention(**extra):
    event = {"type": "app_mention", "user": "U1", "text": "<@UBOT> hi", "ts": "1.0"}
    event.update(extra)
    return event


def _dm(**extra):
    event = {
        "type": "message",
        "channel_type": "im",
        "user": "U1",
        "text": "hi",
        "ts": "1.0",
    }
    event.update(extra)
    return event


def test_should_answer_channel_mention():
    assert should_answer(_mention(), BOT) is True


def test_should_answer_direct_message():
    assert should_answer(_dm(), BOT) is True


def test_should_not_answer_own_message():
    assert should_answer(_dm(user=BOT), BOT) is False


def test_should_not_answer_bot_id_message():
    assert should_answer(_dm(bot_id="B1"), BOT) is False


@pytest.mark.parametrize("subtype", ["message_changed", "bot_message", "channel_join"])
def test_should_not_answer_any_subtype(subtype):
    assert should_answer(_dm(subtype=subtype), BOT) is False


@pytest.mark.parametrize("channel_type", ["channel", "group", "mpim", None])
def test_should_not_answer_message_outside_im(channel_type):
    assert should_answer(_dm(channel_type=channel_type), BOT) is False


def test_should_not_answer_empty_text_after_mention():
    assert should_answer(_mention(text="<@UBOT>   "), BOT) is False


def test_should_not_answer_other_event_types():
    assert should_answer({"type": "reaction_added", "user": "U1"}, BOT) is False


# ---------------------------------------------------------------------------
# to_mrkdwn
# ---------------------------------------------------------------------------


def test_to_mrkdwn_converts_bold():
    assert to_mrkdwn("a **b** c") == "a *b* c"


def test_to_mrkdwn_converts_sources_header():
    answer = "Text\n\n---\n**Sources:**\n- `doc.md`"
    assert to_mrkdwn(answer).endswith("*Sources:*\n- `doc.md`")


def test_to_mrkdwn_converts_links():
    assert to_mrkdwn("see [docs](https://x.org/a)") == "see <https://x.org/a|docs>"


def test_to_mrkdwn_converts_headings():
    assert to_mrkdwn("## Quota\nbody") == "*Quota*\nbody"


def test_to_mrkdwn_leaves_code_fences_alone():
    text = "**a**\n```\n**x** [t](u)\n# not a heading\n```\n**b**"
    assert to_mrkdwn(text) == "*a*\n```\n**x** [t](u)\n# not a heading\n```\n*b*"


@pytest.mark.parametrize(
    "raw, escaped",
    [
        ("ping <!channel> now", "ping &lt;!channel&gt; now"),
        ("<!here>", "&lt;!here&gt;"),
        ("ask <@U123>", "ask &lt;@U123&gt;"),
        ("a & b", "a &amp; b"),
    ],
)
def test_to_mrkdwn_escapes_slack_control_sequences(raw, escaped):
    # Model text must not become a real broadcast or mention (PR #598 review).
    assert to_mrkdwn(raw) == escaped


def test_to_mrkdwn_escapes_inside_code_fences_and_link_urls():
    text = "```\nif a < b & c:\n```\n[q](https://x.org/s?a=1&b=2)"
    assert to_mrkdwn(text) == (
        "```\nif a &lt; b &amp; c:\n```\n<https://x.org/s?a=1&amp;b=2|q>"
    )


def test_to_mrkdwn_truncates_long_text_with_marker():
    out = to_mrkdwn("x" * 50_000)
    assert len(out) <= slack_bot.MAX_TEXT_CHARS
    assert out.endswith(slack_bot.TRUNCATION_MARKER)


def test_to_mrkdwn_keeps_short_text_unchanged_in_length():
    assert to_mrkdwn("x" * 100) == "x" * 100


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


def _reply(ts, text, user: str | None = "U1", **extra):
    msg = {"ts": ts, "text": text, "user": user}
    msg.update(extra)
    return msg


def test_build_messages_without_history():
    assert build_messages([], BOT, "q", before_ts="5.0", limit=20) == [
        {"role": "user", "content": "q"}
    ]


def test_build_messages_maps_roles_in_time_order():
    replies = [
        _reply("2.0", "answer", user=BOT),
        _reply("1.0", "<@UBOT> first"),
        _reply("3.0", "follow"),
    ]
    assert build_messages(replies, BOT, "now", before_ts="4.0", limit=20) == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "follow"},
        {"role": "user", "content": "now"},
    ]


def test_build_messages_excludes_current_and_newer_messages():
    replies = [
        _reply("1.0", "old"),
        _reply("4.0", "<@UBOT> now"),
        _reply("5.0", "_Searching…_", user=BOT),
    ]
    assert build_messages(replies, BOT, "now", before_ts="4.0", limit=20) == [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "now"},
    ]


def test_build_messages_treats_only_own_messages_as_assistant():
    # Only this bot's answers are archi turns; another bot's post is not (review #4).
    replies = [
        _reply("1.0", "own answer", user=BOT, bot_id="B1"),
        _reply("1.5", "other bot", user="UOTHER", bot_id="B2"),
        _reply("1.7", "integration", user=None, bot_id="B3"),
    ]
    out = build_messages(replies, BOT, "q", before_ts="2.0", limit=20)
    assert [m["role"] for m in out] == ["assistant", "user", "user", "user"]


def test_build_messages_drops_empty_texts():
    replies = [_reply("1.0", "<@UBOT>"), _reply("1.5", "")]
    assert build_messages(replies, BOT, "q", before_ts="2.0", limit=20) == [
        {"role": "user", "content": "q"}
    ]


def test_build_messages_keeps_the_newest_turns_within_limit():
    replies = [_reply(f"{i}.0", f"m{i}") for i in range(1, 6)]
    out = build_messages(replies, BOT, "q", before_ts="9.0", limit=2)
    assert [m["content"] for m in out] == ["m4", "m5", "q"]


def test_build_messages_limit_zero_sends_only_the_question():
    replies = [_reply("1.0", "old")]
    assert build_messages(replies, BOT, "q", before_ts="2.0", limit=0) == [
        {"role": "user", "content": "q"}
    ]


def test_build_messages_compares_ts_numerically():
    # "10.0" sorts before "9.0" as text; Slack ts values must compare as numbers.
    replies = [_reply("9.0", "nine"), _reply("10.0", "ten")]
    out = build_messages(replies, BOT, "q", before_ts="11.0", limit=20)
    assert [m["content"] for m in out] == ["nine", "ten", "q"]


# ---------------------------------------------------------------------------
# EventDeduper
# ---------------------------------------------------------------------------


def test_deduper_accepts_first_and_rejects_repeat():
    dedupe = EventDeduper(maxlen=4)
    assert dedupe.first_time("C1", "1.0") is True
    assert dedupe.first_time("C1", "1.0") is False
    assert dedupe.first_time("C2", "1.0") is True


def test_deduper_is_atomic_under_concurrent_deliveries():
    # Socket Mode runs listeners on a thread pool, so the check and the insert must
    # be one step (review #1). A slow membership check makes the race repeatable.
    class _SlowDict(OrderedDict):
        def __contains__(self, key):
            found = super().__contains__(key)
            time.sleep(0.05)
            return found

    dedupe = EventDeduper()
    dedupe._seen = _SlowDict()
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(dedupe.first_time("C", "1")))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1


def test_deduper_forgets_the_oldest_beyond_maxlen():
    dedupe = EventDeduper(maxlen=2)
    dedupe.first_time("C", "1")
    dedupe.first_time("C", "2")
    dedupe.first_time("C", "3")
    assert dedupe.first_time("C", "1") is True
    assert dedupe.first_time("C", "3") is False


# ---------------------------------------------------------------------------
# /v1 HTTP client
# ---------------------------------------------------------------------------


def _response(status=200, payload=None, json_error=False):
    resp = mock.Mock(status_code=status)
    if json_error:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = payload
    return resp


def _completion(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_ask_archi_posts_and_returns_content():
    session = mock.Mock()
    session.post.return_value = _response(payload=_completion("hi"))
    msgs = [{"role": "user", "content": "q"}]
    out = ask_archi(
        session, "http://chatbot:7861/", "m", msgs, "slack:T:C:1", timeout=30
    )
    assert out == "hi"
    args, kwargs = session.post.call_args
    assert args[0] == "http://chatbot:7861/v1/chat/completions"
    assert kwargs["json"] == {"model": "m", "messages": msgs, "stream": False}
    assert kwargs["headers"] == {"X-OpenWebUI-Chat-Id": "slack:T:C:1"}
    assert kwargs["timeout"] == (10, 30)


def test_ask_archi_sends_bearer_only_with_token():
    session = mock.Mock()
    session.post.return_value = _response(payload=_completion("hi"))
    ask_archi(session, "http://c", "m", [], "k", token="archi_x")
    headers = session.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer archi_x"


def test_ask_archi_raises_server_error_message():
    session = mock.Mock()
    session.post.return_value = _response(
        500, {"error": {"message": "boom", "type": "server_error"}}
    )
    with pytest.raises(ArchiApiError, match="HTTP 500: boom"):
        ask_archi(session, "http://c", "m", [], "k")


def test_ask_archi_raises_on_non_json_error():
    session = mock.Mock()
    session.post.return_value = _response(502, json_error=True)
    with pytest.raises(ArchiApiError, match="HTTP 502"):
        ask_archi(session, "http://c", "m", [], "k")


def test_ask_archi_raises_on_malformed_body():
    session = mock.Mock()
    session.post.return_value = _response(payload={"choices": []})
    with pytest.raises(ArchiApiError, match="unexpected"):
        ask_archi(session, "http://c", "m", [], "k")


def test_wait_for_model_retries_then_returns_first_id():
    session = mock.Mock()
    session.get.side_effect = [
        ConnectionError("down"),
        _response(503, json_error=True),
        _response(payload={"data": [{"id": "my_archi"}, {"id": "other"}]}),
    ]
    sleeps = []
    out = wait_for_model(session, "http://c", attempts=5, delay=7, sleep=sleeps.append)
    assert out == "my_archi"
    assert sleeps == [7, 7]
    assert session.get.call_args.args[0] == "http://c/v1/models"
    assert session.get.call_args.kwargs["headers"] == {}


def test_wait_for_model_sends_bearer_with_token():
    session = mock.Mock()
    session.get.return_value = _response(payload={"data": [{"id": "a"}]})
    wait_for_model(session, "http://c", token="archi_x", sleep=lambda _: None)
    headers = session.get.call_args.kwargs["headers"]
    assert headers == {"Authorization": "Bearer archi_x"}


def test_wait_for_model_gives_up_with_config_hint():
    session = mock.Mock()
    session.get.return_value = _response(404, json_error=True)
    sleeps = []
    with pytest.raises(ArchiApiError, match="services.chat_app.openai_compat.enabled"):
        wait_for_model(session, "http://c", attempts=3, delay=1, sleep=sleeps.append)
    assert session.get.call_count == 3
    assert sleeps == [1, 1]


def test_wait_for_model_treats_empty_list_as_failure():
    session = mock.Mock()
    session.get.return_value = _response(payload={"data": []})
    with pytest.raises(ArchiApiError):
        wait_for_model(session, "http://c", attempts=2, delay=0, sleep=lambda _: None)


# ---------------------------------------------------------------------------
# SlackBot
# ---------------------------------------------------------------------------


class _InlineExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class _DeferredExecutor:
    """Hold submitted work until the test runs it."""

    def __init__(self):
        self.tasks = []

    def submit(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))

    def run_all(self):
        while self.tasks:
            fn, args, kwargs = self.tasks.pop(0)
            fn(*args, **kwargs)


def _bot(**overrides):
    web = mock.Mock()
    web.chat_postMessage.return_value = {"ts": "99.0"}
    web.conversations_replies.return_value = {"messages": []}
    session = mock.Mock()
    session.post.return_value = _response(payload=_completion("**ok**"))
    kwargs = dict(
        web_client=web,
        socket_client=mock.Mock(),
        session=session,
        chat_url="http://chatbot:7861",
        bot_user_id=BOT,
        executor=_InlineExecutor(),
    )
    kwargs.update(overrides)
    bot = SlackBot(**kwargs)
    bot.model = "my_archi"
    return bot


def _req(event, type_="events_api", envelope="E1", team="T1"):
    return SimpleNamespace(
        type=type_, envelope_id=envelope, payload={"team_id": team, "event": event}
    )


def _channel_mention(**extra):
    event = {
        "type": "app_mention",
        "user": "U1",
        "text": "<@UBOT> quota?",
        "ts": "10.0",
        "channel": "C1",
    }
    event.update(extra)
    return event


def test_handle_request_acks_before_anything_else():
    bot = _bot()
    calls = []
    client = mock.Mock()
    client.send_socket_mode_response.side_effect = lambda r: calls.append(("ack", r))

    def post(**kwargs):
        calls.append(("post", kwargs))
        return {"ts": "99.0"}

    bot.web.chat_postMessage.side_effect = post
    bot.handle_request(client, _req(_channel_mention()))
    assert calls[0][0] == "ack"
    assert calls[0][1].envelope_id == "E1"
    assert [c[0] for c in calls][1:] == ["post"]


def test_handle_request_ignores_non_events_api():
    bot = _bot()
    client = mock.Mock()
    bot.handle_request(client, _req(_channel_mention(), type_="interactive"))
    client.send_socket_mode_response.assert_called_once()
    bot.session.post.assert_not_called()


def test_handle_request_ignores_filtered_event():
    bot = _bot()
    bot.handle_request(mock.Mock(), _req(_channel_mention(user=BOT)))
    bot.session.post.assert_not_called()


def test_handle_request_answers_each_message_once():
    bot = _bot()
    bot.handle_request(mock.Mock(), _req(_channel_mention(), envelope="E1"))
    bot.handle_request(mock.Mock(), _req(_channel_mention(), envelope="E2"))
    assert bot.session.post.call_count == 1


def test_handle_request_never_raises_when_ack_fails():
    bot = _bot()
    client = mock.Mock()
    client.send_socket_mode_response.side_effect = RuntimeError("socket gone")
    bot.handle_request(client, _req(_channel_mention()))
    assert bot.session.post.call_count == 1


def test_handle_request_never_raises_on_bad_payload():
    bot = _bot()
    req = SimpleNamespace(type="events_api", envelope_id="E", payload=None)
    bot.handle_request(mock.Mock(), req)
    bot.session.post.assert_not_called()


def test_answer_top_level_mention_posts_placeholder_then_updates():
    bot = _bot()
    bot.answer(_channel_mention(), "T1")
    bot.web.chat_postMessage.assert_called_once_with(
        channel="C1", thread_ts="10.0", text=PLACEHOLDER_TEXT
    )
    bot.web.conversations_replies.assert_not_called()
    kwargs = bot.session.post.call_args.kwargs
    assert kwargs["headers"]["X-OpenWebUI-Chat-Id"] == "slack:T1:C1:10.0"
    assert kwargs["json"]["model"] == "my_archi"
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "quota?"}]
    bot.web.chat_update.assert_called_once_with(channel="C1", ts="99.0", text="*ok*")


def test_answer_thread_reply_sends_history():
    bot = _bot(history_limit=5)
    bot.web.conversations_replies.return_value = {
        "messages": [
            {"ts": "1.0", "user": "U1", "text": "<@UBOT> first"},
            {"ts": "2.0", "user": BOT, "text": "answer one"},
            {"ts": "3.0", "user": "U1", "text": "<@UBOT> again?"},
        ]
    }
    event = _channel_mention(ts="3.0", thread_ts="1.0", text="<@UBOT> again?")
    bot.answer(event, "T1")
    bot.web.conversations_replies.assert_called_once_with(
        channel="C1", ts="1.0", limit=1000
    )
    kwargs = bot.session.post.call_args.kwargs
    assert kwargs["headers"]["X-OpenWebUI-Chat-Id"] == "slack:T1:C1:1.0"
    roles = [m["role"] for m in kwargs["json"]["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_answer_reports_api_error_in_thread_without_raising():
    bot = _bot()
    bot.session.post.return_value = _response(500, {"error": {"message": "boom"}})
    bot.answer(_channel_mention(), "T1")
    bot.web.chat_update.assert_called_once_with(
        channel="C1", ts="99.0", text=ERROR_TEXT
    )


def test_answer_posts_error_when_placeholder_failed():
    bot = _bot()
    bot.web.chat_postMessage.side_effect = [RuntimeError("rate limited"), {"ts": "1"}]
    bot.answer(_channel_mention(), "T1")
    assert bot.web.chat_postMessage.call_args.kwargs == {
        "channel": "C1",
        "thread_ts": "10.0",
        "text": ERROR_TEXT,
    }
    bot.session.post.assert_not_called()


def test_answer_swallows_failure_of_the_error_report():
    bot = _bot()
    bot.session.post.side_effect = ConnectionError("down")
    bot.web.chat_update.side_effect = RuntimeError("slack down too")
    bot.answer(_channel_mention(), "T1")  # must not raise


def test_answer_passes_api_token_and_timeout():
    bot = _bot(api_token="archi_x", timeout_seconds=42)
    bot.answer(_channel_mention(), "T1")
    kwargs = bot.session.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer archi_x"
    assert kwargs["timeout"] == (10, 42)


def test_same_thread_questions_run_in_arrival_order_on_one_worker():
    # A follow-up must see the earlier answer and must not overtake it (review #2).
    executor = _DeferredExecutor()
    bot = _bot(executor=executor)
    bot.session.post.side_effect = [
        _response(payload=_completion("one")),
        _response(payload=_completion("two")),
    ]
    bot.web.chat_postMessage.side_effect = [{"ts": "10.5"}, {"ts": "11.5"}]
    bot.web.conversations_replies.return_value = {
        "messages": [
            {"ts": "10.0", "user": "U1", "text": "<@UBOT> q1"},
            {"ts": "10.5", "user": BOT, "text": "one"},
            {"ts": "11.0", "user": "U1", "text": "<@UBOT> q2"},
        ]
    }
    bot.handle_request(mock.Mock(), _req(_channel_mention(ts="10.0", text="q1")))
    follow_up = _channel_mention(ts="11.0", thread_ts="10.0", text="<@UBOT> q2")
    bot.handle_request(mock.Mock(), _req(follow_up, envelope="E2"))
    assert len(executor.tasks) == 1

    executor.run_all()

    names = [c[0] for c in bot.web.mock_calls]
    assert names.index("chat_update") < names.index("conversations_replies")
    assert [c.kwargs["text"] for c in bot.web.chat_update.call_args_list] == [
        "one",
        "two",
    ]
    second = bot.session.post.call_args_list[1].kwargs["json"]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert bot._thread_queues == {}


def test_different_threads_get_separate_workers():
    executor = _DeferredExecutor()
    bot = _bot(executor=executor)
    bot.handle_request(mock.Mock(), _req(_channel_mention(ts="1.0")))
    bot.handle_request(mock.Mock(), _req(_channel_mention(ts="2.0"), envelope="E2"))
    assert len(executor.tasks) == 2
    executor.run_all()
    assert bot._thread_queues == {}


def test_thread_worker_survives_a_raising_answer():
    executor = _DeferredExecutor()
    bot = _bot(executor=executor)
    bot.answer = mock.Mock(side_effect=[RuntimeError("bug"), None])
    bot.handle_request(mock.Mock(), _req(_channel_mention(ts="1.0")))
    follow_up = _channel_mention(ts="2.0", thread_ts="1.0")
    bot.handle_request(mock.Mock(), _req(follow_up, envelope="E2"))
    executor.run_all()
    assert bot.answer.call_count == 2
    assert bot._thread_queues == {}


def test_answer_follows_reply_pagination():
    # A thread longer than one page must not lose its newest turns (review #3).
    bot = _bot()
    bot.web.conversations_replies.side_effect = [
        {
            "messages": [{"ts": "1.0", "user": "U1", "text": "first"}],
            "response_metadata": {"next_cursor": "c2"},
        },
        {
            "messages": [{"ts": "2.0", "user": BOT, "text": "answer"}],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    bot.answer(_channel_mention(ts="3.0", thread_ts="1.0"), "T1")
    calls = bot.web.conversations_replies.call_args_list
    assert calls[0].kwargs == {"channel": "C1", "ts": "1.0", "limit": 1000}
    assert calls[1].kwargs == {
        "channel": "C1",
        "ts": "1.0",
        "limit": 1000,
        "cursor": "c2",
    }
    messages = bot.session.post.call_args.kwargs["json"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]


def test_answer_stops_reply_pagination_at_the_page_cap():
    bot = _bot()
    bot.web.conversations_replies.return_value = {
        "messages": [],
        "response_metadata": {"next_cursor": "again"},
    }
    bot.answer(_channel_mention(ts="3.0", thread_ts="1.0"), "T1")
    assert bot.web.conversations_replies.call_count == slack_bot.MAX_REPLY_PAGES
    assert bot.session.post.call_count == 1


def test_default_executor_is_bounded():
    bot = _bot(executor=None, max_workers=3)
    assert bot.executor._max_workers == 3
    bot.executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# SlackBot.from_config and run
# ---------------------------------------------------------------------------

SECRETS = {
    "SLACK_BOT_TOKEN": "xoxb-secret",
    "SLACK_APP_TOKEN": "xapp-secret",
    "ARCHI_API_TOKEN": "archi_secret",
}


def _from_config(config, secrets=SECRETS):
    web_cls = mock.Mock()
    web_cls.return_value.auth_test.return_value = {"user_id": "UBOT9"}
    socket_cls = mock.Mock()
    bot = SlackBot.from_config(
        config,
        secret_reader=lambda name, default="": secrets.get(name, default),
        web_client_cls=web_cls,
        socket_client_cls=socket_cls,
    )
    return bot, web_cls, socket_cls


def test_from_config_uses_defaults(caplog):
    caplog.set_level("DEBUG")
    bot, web_cls, socket_cls = _from_config({"services": {}})
    web_cls.assert_called_once_with(token="xoxb-secret")
    socket_cls.assert_called_once_with(
        app_token="xapp-secret", web_client=web_cls.return_value
    )
    assert bot.bot_user_id == "UBOT9"
    assert bot.chat_url == "http://chatbot:7861"
    assert bot.timeout_seconds == 600
    assert bot.history_limit == 20
    assert bot.api_token == "archi_secret"
    assert bot.executor._max_workers == 4
    bot.executor.shutdown(wait=False)
    for secret in SECRETS.values():
        assert secret not in caplog.text


def test_from_config_reads_services_slack():
    config = {
        "services": {
            "slack": {
                "chat_url": "http://localhost:7999",
                "timeout_seconds": "120",
                "max_workers": 2,
                "history_limit": 5,
            }
        }
    }
    bot, _, _ = _from_config(config)
    assert bot.chat_url == "http://localhost:7999"
    assert bot.timeout_seconds == 120
    assert bot.history_limit == 5
    assert bot.executor._max_workers == 2
    bot.executor.shutdown(wait=False)


def test_from_config_null_values_fall_back_and_zero_history_is_kept():
    keys = ("chat_url", "timeout_seconds", "max_workers")
    bot, _, _ = _from_config({"services": {"slack": dict.fromkeys(keys)}})
    assert (bot.chat_url, bot.timeout_seconds) == ("http://chatbot:7861", 600)
    assert bot.history_limit == 20
    bot.executor.shutdown(wait=False)
    bot, _, _ = _from_config({"services": {"slack": {"history_limit": None}}})
    assert bot.history_limit == 20
    bot.executor.shutdown(wait=False)
    bot, _, _ = _from_config({"services": {"slack": {"history_limit": 0}}})
    assert bot.history_limit == 0
    bot.executor.shutdown(wait=False)


def test_from_config_handles_null_slack_block():
    bot, _, _ = _from_config({"services": {"slack": None}})
    assert bot.chat_url == "http://chatbot:7861"
    bot.executor.shutdown(wait=False)


def test_from_config_requires_both_slack_tokens():
    with pytest.raises(RuntimeError, match="SLACK_APP_TOKEN"):
        _from_config({}, secrets={"SLACK_BOT_TOKEN": "xoxb-secret"})


def test_run_resolves_model_registers_listener_and_connects():
    bot = _bot()
    bot.model = None
    bot.session.get.return_value = _response(payload={"data": [{"id": "cfg"}]})
    bot.socket.socket_mode_request_listeners = []
    stop = threading.Event()
    stop.set()
    bot.run(stop_event=stop)
    assert bot.model == "cfg"
    assert bot.socket.socket_mode_request_listeners == [bot.handle_request]
    bot.socket.connect.assert_called_once_with()


def test_run_does_not_connect_when_chat_api_is_down():
    bot = _bot(startup_attempts=2, startup_delay=0)
    bot.session.get.return_value = _response(404, json_error=True)
    bot.socket.socket_mode_request_listeners = []
    with pytest.raises(ArchiApiError):
        bot.run(sleep=lambda _: None)
    bot.socket.connect.assert_not_called()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def test_service_slack_main_sets_up_config_then_runs(monkeypatch):
    from src.bin import service_slack

    order = []
    factory_cls = mock.Mock()

    def from_env(**kwargs):
        order.append(("from_env", kwargs))
        return "F"

    def get_config():
        order.append("cfg")
        return {"x": 1}

    factory_cls.from_env.side_effect = from_env
    factory_cls.set_instance.side_effect = lambda f: order.append(("set", f))
    bot_cls = mock.Mock()
    monkeypatch.setattr(service_slack, "PostgresServiceFactory", factory_cls)
    monkeypatch.setattr(service_slack, "setup_logging", lambda: order.append("log"))
    monkeypatch.setattr(service_slack, "read_secret", lambda name: f"<{name}>")
    monkeypatch.setattr(service_slack, "get_full_config", get_config)
    monkeypatch.setattr(service_slack, "SlackBot", bot_cls)

    service_slack.main()

    assert order == [
        "log",
        ("from_env", {"password_override": "<PG_PASSWORD>"}),
        ("set", "F"),
        "cfg",
    ]
    bot_cls.from_config.assert_called_once_with({"x": 1})
    bot_cls.from_config.return_value.run.assert_called_once_with()


def test_service_slack_has_no_startup_sleep():
    source = (REPO_ROOT / "src/bin/service_slack.py").read_text()
    assert "sleep" not in source
