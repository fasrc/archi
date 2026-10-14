"""Unit tests for the Slack bot access limits (openspec/changes/slack-access-limits).

Two config switches limit who the bot answers and where: ``internal_users_only``
rejects guests and users from other organizations, and ``allowed_channels`` keeps
the bot in a fixed set of channels. Slack clients are mocks.
"""

from types import SimpleNamespace
from unittest import mock

import pytest

from src.interfaces.slack_bot import (
    DEFAULT_EXTERNAL_USER_TEXT,
    ERROR_TEXT,
    SlackBot,
    channel_allowed,
    is_internal_user,
    parse_allowed_channels,
)
from src.interfaces.slack_feedback import HELPFUL_ACTION_ID

BOT = "UBOT"
TEAM = "T1"


class _InlineExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


def _completion_response():
    resp = mock.Mock(status_code=200)
    resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    return resp


def _member(user_id="U1", team_id=TEAM, **extra):
    user = {"id": user_id, "team_id": team_id}
    user.update(extra)
    return {"ok": True, "user": user}


def _bot(**overrides):
    web = mock.Mock()
    web.chat_postMessage.return_value = {"ts": "99.0"}
    web.conversations_replies.return_value = {"messages": []}
    web.users_info.return_value = _member()
    session = mock.Mock()
    session.post.return_value = _completion_response()
    kwargs = dict(
        web_client=web,
        socket_client=mock.Mock(),
        session=session,
        chat_url="http://chatbot:7861",
        bot_user_id=BOT,
        executor=_InlineExecutor(),
        team_id=TEAM,
    )
    kwargs.update(overrides)
    bot = SlackBot(**kwargs)
    bot.model = "my_archi"
    return bot


def _req(event, type_="events_api", envelope="E1", team=TEAM):
    return SimpleNamespace(
        type=type_, envelope_id=envelope, payload={"team_id": team, "event": event}
    )


def _mention(**extra):
    event = {
        "type": "app_mention",
        "user": "U1",
        "text": "<@UBOT> quota?",
        "ts": "10.0",
        "channel": "C1",
    }
    event.update(extra)
    return event


def _dm(**extra):
    event = {
        "type": "message",
        "channel_type": "im",
        "user": "U1",
        "text": "quota?",
        "ts": "11.0",
        "channel": "D1",
    }
    event.update(extra)
    return event


# ---------------------------------------------------------------------------
# is_internal_user
# ---------------------------------------------------------------------------


def test_member_of_the_workspace_is_internal():
    assert is_internal_user({"team_id": TEAM}, TEAM)


def test_user_of_another_workspace_is_external():
    assert not is_internal_user({"team_id": "T999"}, TEAM)


@pytest.mark.parametrize("flag", ["is_restricted", "is_ultra_restricted"])
def test_guest_is_external(flag):
    assert not is_internal_user({"team_id": TEAM, flag: True}, TEAM)


def test_stranger_is_external():
    assert not is_internal_user({"team_id": TEAM, "is_stranger": True}, TEAM)


def test_user_without_team_is_external():
    assert not is_internal_user({}, TEAM)
    assert not is_internal_user(None, TEAM)


def test_unknown_workspace_rejects_everyone():
    assert not is_internal_user({"team_id": None}, None)


def test_same_enterprise_other_workspace_is_internal():
    user = {"team_id": "T2", "enterprise_user": {"enterprise_id": "E1"}}
    assert is_internal_user(user, TEAM, enterprise_id="E1")


def test_other_enterprise_is_external():
    user = {"team_id": "T2", "enterprise_user": {"enterprise_id": "E9"}}
    assert not is_internal_user(user, TEAM, enterprise_id="E1")


# ---------------------------------------------------------------------------
# channel_allowed and parse_allowed_channels
# ---------------------------------------------------------------------------


def test_no_channel_list_allows_every_channel():
    assert channel_allowed(_mention(channel="C9"), frozenset())


def test_listed_channel_is_allowed():
    assert channel_allowed(_mention(channel="C1"), frozenset({"C1"}))


def test_unlisted_channel_is_not_allowed():
    assert not channel_allowed(_mention(channel="C9"), frozenset({"C1"}))


def test_direct_message_is_always_allowed():
    assert channel_allowed(_dm(), frozenset({"C1"}))


def test_group_dm_mention_obeys_the_list():
    assert not channel_allowed(_mention(channel="G5"), frozenset({"C1"}))


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, frozenset()),
        ([], frozenset()),
        ("C1", frozenset({"C1"})),
        (["C1", " C2 ", ""], frozenset({"C1", "C2"})),
    ],
)
def test_parse_allowed_channels(value, expected):
    assert parse_allowed_channels(value) == expected


@pytest.mark.parametrize("value", [42, {"C1": True}, ["C1", 7], "", "   ", ["", " "]])
def test_parse_allowed_channels_rejects_bad_values(value):
    with pytest.raises(ValueError, match="allowed_channels"):
        parse_allowed_channels(value)


# ---------------------------------------------------------------------------
# internal_users_only in handle_request
# ---------------------------------------------------------------------------


def test_switch_off_answers_without_a_lookup():
    bot = _bot()
    bot.handle_request(mock.Mock(), _req(_mention()))
    bot.web.users_info.assert_not_called()
    assert bot.session.post.call_count == 1


def test_internal_user_gets_an_answer():
    bot = _bot(internal_users_only=True)
    bot.handle_request(mock.Mock(), _req(_mention()))
    bot.web.users_info.assert_called_once_with(user="U1")
    assert bot.session.post.call_count == 1
    bot.web.chat_postEphemeral.assert_not_called()


@pytest.mark.parametrize(
    "member",
    [
        _member(team_id="T999"),
        _member(is_restricted=True),
        _member(is_ultra_restricted=True),
    ],
)
def test_external_user_gets_the_notice_and_no_answer(member):
    bot = _bot(internal_users_only=True)
    bot.web.users_info.return_value = member
    bot.handle_request(mock.Mock(), _req(_mention(thread_ts="5.0")))
    bot.session.post.assert_not_called()
    bot.web.chat_postMessage.assert_not_called()
    bot.web.chat_postEphemeral.assert_called_once_with(
        channel="C1", user="U1", text=DEFAULT_EXTERNAL_USER_TEXT, thread_ts="5.0"
    )


def test_external_user_in_a_dm_gets_the_notice():
    bot = _bot(internal_users_only=True)
    bot.web.users_info.return_value = _member(team_id="T999")
    bot.handle_request(mock.Mock(), _req(_dm()))
    bot.session.post.assert_not_called()
    bot.web.chat_postEphemeral.assert_called_once_with(
        channel="D1", user="U1", text=DEFAULT_EXTERNAL_USER_TEXT
    )


def test_notice_for_a_top_level_mention_starts_no_thread():
    # Slack shows an ephemeral thread reply only in a thread that already exists.
    bot = _bot(internal_users_only=True)
    bot.web.users_info.return_value = _member(team_id="T999")
    bot.handle_request(mock.Mock(), _req(_mention()))
    assert "thread_ts" not in bot.web.chat_postEphemeral.call_args.kwargs


def test_notice_text_comes_from_config():
    bot = _bot(internal_users_only=True, external_user_text="Staff only.")
    bot.web.users_info.return_value = _member(is_restricted=True)
    bot.handle_request(mock.Mock(), _req(_mention()))
    assert bot.web.chat_postEphemeral.call_args.kwargs["text"] == "Staff only."


def test_failed_lookup_rejects_and_is_retried_next_time():
    bot = _bot(internal_users_only=True)
    bot.web.users_info.side_effect = [RuntimeError("slack down"), _member()]
    bot.handle_request(mock.Mock(), _req(_mention(ts="1.0"), envelope="E1"))
    bot.session.post.assert_not_called()
    # The sender can be internal, so the reply is the error, not the external notice.
    assert bot.web.chat_postEphemeral.call_args.kwargs["text"] == ERROR_TEXT
    bot.handle_request(mock.Mock(), _req(_mention(ts="2.0"), envelope="E2"))
    assert bot.session.post.call_count == 1


def test_each_question_looks_up_the_sender_again():
    # A member who becomes a guest loses access at the next question.
    bot = _bot(internal_users_only=True)
    bot.web.users_info.side_effect = [_member(), _member(is_restricted=True)]
    bot.handle_request(mock.Mock(), _req(_mention(ts="1.0"), envelope="E1"))
    bot.handle_request(mock.Mock(), _req(_mention(ts="2.0"), envelope="E2"))
    assert bot.web.users_info.call_count == 2
    assert bot.session.post.call_count == 1
    assert bot.web.chat_postEphemeral.call_args.kwargs["text"] == (
        DEFAULT_EXTERNAL_USER_TEXT
    )


def test_retried_envelope_posts_the_notice_once():
    bot = _bot(internal_users_only=True)
    bot.web.users_info.return_value = _member(team_id="T999")
    bot.handle_request(mock.Mock(), _req(_mention(), envelope="E1"))
    bot.handle_request(mock.Mock(), _req(_mention(), envelope="E1-retry"))
    bot.web.chat_postEphemeral.assert_called_once()


def test_failed_notice_never_raises():
    bot = _bot(internal_users_only=True)
    bot.web.users_info.return_value = _member(team_id="T999")
    bot.web.chat_postEphemeral.side_effect = RuntimeError("slack down")
    bot.handle_request(mock.Mock(), _req(_mention()))
    bot.session.post.assert_not_called()


def test_a_failed_notice_is_retried_once():
    bot = _bot(internal_users_only=True)
    bot.web.users_info.return_value = _member(team_id="T999")
    bot.web.chat_postEphemeral.side_effect = [RuntimeError("slack down"), None]
    bot.handle_request(mock.Mock(), _req(_mention()))
    assert bot.web.chat_postEphemeral.call_count == 2
    bot.session.post.assert_not_called()


def test_a_notice_that_fails_twice_is_given_up():
    bot = _bot(internal_users_only=True)
    bot.web.users_info.return_value = _member(team_id="T999")
    bot.web.chat_postEphemeral.side_effect = RuntimeError("slack down")
    bot.handle_request(mock.Mock(), _req(_mention()))
    assert bot.web.chat_postEphemeral.call_count == 2


class _DeferredExecutor:
    def __init__(self):
        self.tasks = []

    def submit(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))

    def run_all(self):
        while self.tasks:
            fn, args, kwargs = self.tasks.pop(0)
            fn(*args, **kwargs)


def test_access_check_runs_in_the_thread_worker_not_the_listener():
    # The listener runs in a thread pool. A slow lookup there could let a later
    # question in the same thread overtake an earlier one.
    executor = _DeferredExecutor()
    bot = _bot(internal_users_only=True, executor=executor)
    bot.handle_request(mock.Mock(), _req(_mention(ts="1.0"), envelope="E1"))
    bot.handle_request(
        mock.Mock(), _req(_mention(ts="2.0", thread_ts="1.0"), envelope="E2")
    )
    bot.web.users_info.assert_not_called()
    executor.run_all()
    assert bot.web.users_info.call_count == 2
    questions = [
        c.kwargs["json"]["messages"][-1]["content"]
        for c in bot.session.post.call_args_list
    ]
    assert questions == ["quota?", "quota?"]
    first, second = bot.web.chat_postMessage.call_args_list
    assert (first.kwargs["thread_ts"], second.kwargs["thread_ts"]) == ("1.0", "1.0")


def test_workspace_falls_back_to_the_payload_team():
    bot = _bot(internal_users_only=True, team_id=None)
    bot.web.users_info.return_value = _member(team_id="T7")
    bot.handle_request(mock.Mock(), _req(_mention(), team="T7"))
    assert bot.session.post.call_count == 1


# ---------------------------------------------------------------------------
# internal_users_only on the feedback buttons
# ---------------------------------------------------------------------------


def _click(user_id="U1"):
    payload = {
        "type": "block_actions",
        "trigger_id": "TR1",
        "channel": {"id": "C1"},
        "user": {"id": user_id},
        "container": {"thread_ts": "5.0"},
        "actions": [
            {"action_id": HELPFUL_ACTION_ID, "value": "42", "action_ts": "1.1"}
        ],
    }
    return SimpleNamespace(type="interactive", envelope_id="E1", payload=payload)


def test_external_click_stores_nothing_and_gets_the_notice():
    store = mock.Mock()
    bot = _bot(internal_users_only=True, feedback_store=store)
    bot.web.users_info.return_value = _member(is_ultra_restricted=True)
    bot.handle_request(mock.Mock(), _click())
    store.insert.assert_not_called()
    bot.web.chat_postEphemeral.assert_called_once_with(
        channel="C1", user="U1", text=DEFAULT_EXTERNAL_USER_TEXT, thread_ts="5.0"
    )


def test_external_form_submission_stores_nothing():
    from src.interfaces.slack_feedback import FEEDBACK_FORM_CALLBACK_ID

    store = mock.Mock()
    bot = _bot(internal_users_only=True, feedback_store=store)
    bot.web.users_info.return_value = _member(team_id="T999")
    payload = {
        "type": "view_submission",
        "user": {"id": "U1"},
        "view": {
            "id": "V1",
            "callback_id": FEEDBACK_FORM_CALLBACK_ID,
            "private_metadata": "42",
            "state": {"values": {}},
        },
    }
    bot.handle_request(
        mock.Mock(),
        SimpleNamespace(type="interactive", envelope_id="E", payload=payload),
    )
    store.insert.assert_not_called()


def test_internal_click_stores_the_like():
    store = mock.Mock()
    bot = _bot(internal_users_only=True, feedback_store=store)
    bot.handle_request(mock.Mock(), _click())
    store.insert.assert_called_once()


# ---------------------------------------------------------------------------
# allowed_channels in handle_request
# ---------------------------------------------------------------------------


def test_mention_in_an_unlisted_channel_is_ignored():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.handle_request(mock.Mock(), _req(_mention(channel="C9")))
    bot.session.post.assert_not_called()
    bot.web.chat_postMessage.assert_not_called()


def test_mention_in_a_listed_channel_is_answered():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.handle_request(mock.Mock(), _req(_mention(channel="C1")))
    assert bot.session.post.call_count == 1


def _joined(channel, user=BOT):
    return {"type": "member_joined_channel", "user": user, "channel": channel}


def test_bot_leaves_an_unlisted_channel_on_join():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.handle_request(mock.Mock(), _req(_joined("C9")))
    bot.web.conversations_leave.assert_called_once_with(channel="C9")


def test_bot_stays_in_a_listed_channel_on_join():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.handle_request(mock.Mock(), _req(_joined("C1")))
    bot.web.conversations_leave.assert_not_called()


def test_join_of_another_user_is_ignored():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.handle_request(mock.Mock(), _req(_joined("C9", user="U1")))
    bot.web.conversations_leave.assert_not_called()


def test_no_channel_list_never_leaves():
    bot = _bot()
    bot.handle_request(mock.Mock(), _req(_joined("C9")))
    bot.web.conversations_leave.assert_not_called()


def test_failed_leave_never_raises():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.web.conversations_leave.side_effect = RuntimeError("missing_scope")
    bot.handle_request(mock.Mock(), _req(_joined("C9")))
    bot.web.conversations_leave.assert_called_once()


# ---------------------------------------------------------------------------
# allowed_channels at start-up
# ---------------------------------------------------------------------------


def _conversations(pages):
    def users_conversations(types, limit, cursor=None):
        return pages[(types, cursor)]

    return users_conversations


def test_leave_unlisted_channels_lists_each_type_and_follows_pagination():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.web.users_conversations.side_effect = _conversations(
        {
            ("public_channel", None): {
                "channels": [{"id": "C1"}, {"id": "C9"}],
                "response_metadata": {"next_cursor": "next"},
            },
            ("public_channel", "next"): {"channels": [{"id": "C8"}]},
            ("private_channel", None): {"channels": [{"id": "G7"}]},
            ("mpim", None): {"channels": [{"id": "G6"}]},
        }
    )
    bot.leave_unlisted_channels()
    assert [c.kwargs["types"] for c in bot.web.users_conversations.call_args_list] == [
        "public_channel",
        "public_channel",
        "private_channel",
        "mpim",
    ]
    assert bot.web.conversations_leave.call_args_list == [
        mock.call(channel="C9"),
        mock.call(channel="C8"),
        mock.call(channel="G7"),
        mock.call(channel="G6"),
    ]


def test_a_missing_scope_for_one_type_does_not_stop_the_others():
    bot = _bot(allowed_channels=frozenset({"C1"}))

    def users_conversations(types, limit, cursor=None):
        if types == "mpim":
            raise RuntimeError("missing_scope")
        return {"channels": [{"id": f"X-{types}"}]}

    bot.web.users_conversations.side_effect = users_conversations
    bot.leave_unlisted_channels()
    assert bot.web.conversations_leave.call_args_list == [
        mock.call(channel="X-public_channel"),
        mock.call(channel="X-private_channel"),
    ]


def test_leave_unlisted_channels_does_nothing_without_a_list():
    bot = _bot()
    bot.leave_unlisted_channels()
    bot.web.users_conversations.assert_not_called()


def test_leave_unlisted_channels_never_raises():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.web.users_conversations.side_effect = RuntimeError("missing_scope")
    bot.leave_unlisted_channels()
    assert bot.web.users_conversations.call_count == 3
    bot.web.conversations_leave.assert_not_called()


def test_run_leaves_unlisted_channels_before_it_connects():
    bot = _bot(allowed_channels=frozenset({"C1"}))
    bot.model = None
    resp = mock.Mock(status_code=200)
    resp.json.return_value = {"data": [{"id": "cfg"}]}
    bot.session.get.return_value = resp
    bot.socket.socket_mode_request_listeners = []
    order = []
    bot.web.users_conversations.side_effect = lambda **_: order.append("list") or {
        "channels": []
    }
    bot.socket.connect.side_effect = lambda: order.append("connect")
    stop = mock.Mock()
    bot.run(stop_event=stop)
    assert order == ["list", "list", "list", "connect"]


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------

SECRETS = {"SLACK_BOT_TOKEN": "xoxb-secret", "SLACK_APP_TOKEN": "xapp-secret"}


def _from_config(slack_cfg, auth=None):
    web_cls = mock.Mock()
    web_cls.return_value.auth_test.return_value = auth or {
        "user_id": "UBOT9",
        "team_id": "T42",
    }
    bot = SlackBot.from_config(
        {"services": {"slack": slack_cfg}},
        secret_reader=lambda name, default="": SECRETS.get(name, default),
        web_client_cls=web_cls,
        socket_client_cls=mock.Mock(),
    )
    bot.executor.shutdown(wait=False)
    return bot


def test_from_config_defaults_keep_the_bot_open():
    bot = _from_config({})
    assert bot.internal_users_only is False
    assert bot.allowed_channels == frozenset()
    assert bot.external_user_text == DEFAULT_EXTERNAL_USER_TEXT
    assert bot.team_id == "T42"
    assert bot.enterprise_id is None


def test_from_config_reads_the_access_keys():
    bot = _from_config(
        {
            "internal_users_only": True,
            "allowed_channels": ["C0C8TS7H8BV", "C0BTTAFE4ET"],
            "external_user_text": "Staff only.",
        },
        auth={"user_id": "UBOT9", "team_id": "T42", "enterprise_id": "E1"},
    )
    assert bot.internal_users_only is True
    assert bot.allowed_channels == frozenset({"C0C8TS7H8BV", "C0BTTAFE4ET"})
    assert bot.external_user_text == "Staff only."
    assert bot.enterprise_id == "E1"


def test_from_config_null_access_keys_fall_back():
    bot = _from_config(
        dict.fromkeys(["internal_users_only", "allowed_channels", "external_user_text"])
    )
    assert bot.internal_users_only is False
    assert bot.allowed_channels == frozenset()
    assert bot.external_user_text == DEFAULT_EXTERNAL_USER_TEXT


def test_from_config_rejects_a_bad_channel_list():
    with pytest.raises(ValueError, match="allowed_channels"):
        _from_config({"allowed_channels": 42})


def test_click_with_no_actions_from_an_internal_user_does_nothing():
    store = mock.Mock()
    bot = _bot(internal_users_only=True, feedback_store=store)
    req = _click()
    req.payload["actions"] = []
    bot.handle_request(mock.Mock(), req)
    bot.web.users_info.assert_called_once_with(user="U1")
    store.insert.assert_not_called()
    bot.web.chat_postEphemeral.assert_not_called()
