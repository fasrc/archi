"""Slack bot that answers @mentions and direct messages through the chat app's /v1 API.

The bot is a thin client (openspec/changes/add-slack-service/design.md, D1): it loads no
pipeline and no model. It receives Slack events over Socket Mode and forwards each
question, with the earlier turns of its Slack thread, to ``POST /v1/chat/completions``.
"""

import re
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple, Optional

import requests
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse

from src.interfaces.slack_feedback import (
    FEEDBACK_PROMPT_TEXT,
    FEEDBACK_THANKS_TEXT,
    HELPFUL_ACTION_ID,
    NOT_HELPFUL_ACTION_ID,
    SlackFeedbackStore,
    build_feedback_blocks,
    build_feedback_modal,
    ephemeral_thanks_kwargs,
    interaction_key,
    like_record,
    parse_feedback_submission,
    parse_message_id,
)
from src.utils.env import read_secret
from src.utils.logging import get_logger
from src.utils.postgres_service_factory import PostgresServiceFactory

logger = get_logger(__name__)

# Slack rejects a message `text` over 40,000 characters; keep a margin (design D8).
MAX_TEXT_CHARS = 39_000
TRUNCATION_MARKER = "\n\n_[answer truncated]_"

PLACEHOLDER_TEXT = "_Searching the archi knowledge base…_"
ERROR_TEXT = (
    ":warning: archi could not answer this question. "
    "An operator can find the reason in the slack service log."
)

DEFAULT_EXTERNAL_USER_TEXT = (
    "This assistant is available only to internal users. "
    "Guests and users from other organizations cannot use it."
)

_STATUS_TEXTS = frozenset({PLACEHOLDER_TEXT, ERROR_TEXT, FEEDBACK_PROMPT_TEXT})

DEFAULT_CHAT_URL = "http://chatbot:7861"
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_MAX_WORKERS = 4
DEFAULT_HISTORY_LIMIT = 20
# conversations.replies returns the oldest messages first; 1000 is its maximum page.
REPLIES_PAGE_LIMIT = 1000
# A thread of more than 10,000 messages is cut, with a warning in the log.
MAX_REPLY_PAGES = 10
CONNECT_TIMEOUT_SECONDS = 10

# A mention plus the spaces or tabs beside it; newlines and indentation elsewhere stay.
_MENTION_RE = re.compile(r"[ \t]*<@[A-Z0-9]+>[ \t]*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


class AskResult(NamedTuple):
    text: str
    message_id: Optional[int]


def thread_key(team_id, channel, thread_ts):
    """Return the X-OpenWebUI-Chat-Id value for one Slack thread (design D4)."""
    return f"slack:{team_id}:{channel}:{thread_ts}"


def strip_mention(text):
    """Remove every ``<@U…>`` mention from ``text``; keep its internal whitespace."""
    return _MENTION_RE.sub(" ", text or "").strip()


def should_answer(event, bot_user_id):
    """Return True when the bot must answer this Slack event."""
    if event.get("subtype") or event.get("bot_id"):
        return False
    if event.get("user") == bot_user_id:
        return False
    event_type = event.get("type")
    if event_type == "message":
        if event.get("channel_type") != "im":
            return False
    elif event_type != "app_mention":
        return False
    return bool(strip_mention(event.get("text")))


def channel_allowed(event, allowed_channels):
    """Return True when the bot may answer in the event's channel.

    An empty ``allowed_channels`` puts no limit on channels. A direct message is not a
    channel, so it is always allowed.
    """
    if not allowed_channels:
        return True
    if event.get("type") == "message" and event.get("channel_type") == "im":
        return True
    return event.get("channel") in allowed_channels


def parse_allowed_channels(value):
    """Return the ``services.slack.allowed_channels`` config value as a frozenset.

    Only ``null`` or an empty list means "no limit". A value with no channel IDs in
    it, such as a blank string, is an error: a config mistake must not remove the
    limit.
    """
    if value is None or (isinstance(value, (list, tuple)) and not value):
        return frozenset()
    if isinstance(value, str):
        value = [value]
    channels = None
    if isinstance(value, (list, tuple)) and all(isinstance(i, str) for i in value):
        channels = frozenset(item.strip() for item in value if item.strip())
    if not channels:
        raise ValueError(
            "services.slack.allowed_channels must be a list of Slack channel IDs, "
            f"not {value!r}."
        )
    return channels


def is_internal_user(user, team_id, enterprise_id=None):
    """Return True when a ``users.info`` user is a full member of the bot's organization.

    Guests (``is_restricted``, ``is_ultra_restricted``) and users of other workspaces
    are external. On Enterprise Grid, a member of another workspace of the same
    enterprise is internal.
    """
    if not user or not team_id:
        return False
    if (
        user.get("is_restricted")
        or user.get("is_ultra_restricted")
        or user.get("is_stranger")
    ):
        return False
    if user.get("team_id") == team_id:
        return True
    user_enterprise = (user.get("enterprise_user") or {}).get("enterprise_id")
    return bool(enterprise_id) and user_enterprise == enterprise_id


def _convert_line(line):
    heading = _HEADING_RE.match(line)
    if heading:
        line = f"**{heading.group(1).strip()}**"
    line = _LINK_RE.sub(r"<\2|\1>", line)
    return _BOLD_RE.sub(r"*\1*", line)


def _escape_control(text):
    # Slack reads <!channel>, <!here> and <@U…> as real broadcasts and mentions.
    # Escape the three control characters in all model text (code included), so the
    # only markup Slack acts on is the <url|title> links this converter adds.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_mrkdwn(markdown):
    """Convert the /v1 Markdown answer to Slack mrkdwn (design D8)."""
    out = []
    in_fence = False
    for line in _escape_control(markdown or "").split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
        elif in_fence:
            out.append(line)
        else:
            out.append(_convert_line(line))
    text = "\n".join(out)
    if len(text) > MAX_TEXT_CHARS:
        text = text[: MAX_TEXT_CHARS - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return text


def _is_own_message(message, bot_user_id):
    # Only this bot's answers are archi turns. A post by another bot or integration
    # is context from the thread, so it stays a user turn.
    return message.get("user") == bot_user_id


def build_messages(replies, bot_user_id, question, before_ts, limit):
    """Return the /v1 ``messages``: earlier thread turns, then the question (design D4)."""
    cutoff = float(before_ts)
    turns = []
    for message in sorted(replies, key=lambda m: float(m["ts"])):
        if float(message["ts"]) >= cutoff:
            continue
        content = strip_mention(message.get("text"))
        if not content:
            continue
        own = _is_own_message(message, bot_user_id)
        # A placeholder left by a failed delivery, or the error line, is not an answer.
        if own and content in _STATUS_TEXTS:
            continue
        role = "assistant" if own else "user"
        turns.append({"role": role, "content": content})
    kept = turns[-limit:] if limit > 0 else []
    return kept + [{"role": "user", "content": question}]


class EventDeduper:
    """Remember the last ``maxlen`` (channel, ts) pairs so each is answered once (D6)."""

    def __init__(self, maxlen=1024):
        self._maxlen = maxlen
        self._seen = OrderedDict()
        # Socket Mode runs listeners on a thread pool: check and insert in one step.
        self._lock = threading.Lock()

    def first_time(self, channel, ts):
        key = (channel, ts)
        with self._lock:
            if key in self._seen:
                return False
            self._seen[key] = None
            if len(self._seen) > self._maxlen:
                self._seen.popitem(last=False)
            return True

    def forget(self, channel, ts):
        """Let ``(channel, ts)`` through again, so a retry can redo a failed step."""
        with self._lock:
            self._seen.pop((channel, ts), None)


class ArchiApiError(RuntimeError):
    """The chat app's /v1 API refused or failed a request."""


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


def _error_detail(resp):
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return ""


def ask_archi(
    session,
    base_url,
    model,
    messages,
    chat_id,
    token=None,
    timeout=DEFAULT_TIMEOUT_SECONDS,
):
    """Send ``messages`` to ``POST /v1/chat/completions`` and return the answer text."""
    headers = {"X-OpenWebUI-Chat-Id": chat_id, **_auth_headers(token)}
    resp = session.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        json={"model": model, "messages": messages, "stream": False},
        headers=headers,
        timeout=(CONNECT_TIMEOUT_SECONDS, timeout),
    )
    if resp.status_code != 200:
        detail = _error_detail(resp)
        suffix = f": {detail}" if detail else ""
        raise ArchiApiError(
            f"/v1/chat/completions returned HTTP {resp.status_code}{suffix}"
        )
    try:
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ArchiApiError("/v1/chat/completions returned an unexpected body") from exc
    raw_id = body.get("archi_message_id")
    message_id = (
        raw_id if isinstance(raw_id, int) and not isinstance(raw_id, bool) else None
    )
    return AskResult(text=content, message_id=message_id)


def wait_for_model(
    session, base_url, token=None, attempts=30, delay=10, sleep=time.sleep
):
    """Return the first model ID from ``GET /v1/models``, retrying (design D7)."""
    url = f"{base_url.rstrip('/')}/v1/models"
    last_problem = ""
    for attempt in range(1, attempts + 1):
        try:
            resp = session.get(
                url, headers=_auth_headers(token), timeout=CONNECT_TIMEOUT_SECONDS
            )
            if resp.status_code == 200:
                models = resp.json().get("data") or []
                if models:
                    return models[0]["id"]
                last_problem = "no models listed"
            else:
                last_problem = f"HTTP {resp.status_code}"
        except Exception as exc:  # any failure is a retry until attempts run out
            last_problem = f"{type(exc).__name__}: {exc}"
        logger.info(
            f"slack bot: {url} not ready (attempt {attempt}/{attempts}, {last_problem})"
        )
        if attempt < attempts:
            sleep(delay)
    raise ArchiApiError(
        f"{url} did not answer after {attempts} attempts ({last_problem}). Check that "
        "the chatbot service runs and that services.chat_app.openai_compat.enabled "
        "is true (or, in host mode with auth off, that "
        "services.chat_app.openai_compat.local_only is true and this bot calls the "
        "chat app on localhost)."
    )


class SlackBot:
    """Answer Slack mentions and direct messages through the chat app's /v1 API."""

    def __init__(
        self,
        web_client,
        socket_client,
        session,
        chat_url,
        bot_user_id,
        api_token="",
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        history_limit=DEFAULT_HISTORY_LIMIT,
        max_workers=DEFAULT_MAX_WORKERS,
        executor=None,
        startup_attempts=30,
        startup_delay=10,
        feedback_store=None,
        team_id=None,
        enterprise_id=None,
        internal_users_only=False,
        external_user_text=DEFAULT_EXTERNAL_USER_TEXT,
        allowed_channels=frozenset(),
    ):
        self.web = web_client
        self.socket = socket_client
        self.session = session
        self.chat_url = chat_url
        self.bot_user_id = bot_user_id
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.history_limit = history_limit
        self.executor = executor or ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="slack-answer"
        )
        self.startup_attempts = startup_attempts
        self.startup_delay = startup_delay
        self.feedback_store = feedback_store
        self.team_id = team_id
        self.enterprise_id = enterprise_id
        self.internal_users_only = internal_users_only
        self.external_user_text = external_user_text
        self.allowed_channels = allowed_channels
        self.model = None
        self._deduper = EventDeduper()
        # One FIFO queue per Slack thread with work pending; a thread is drained by
        # one worker at a time, so a follow-up sees the earlier answer (design D3).
        self._thread_queues = {}
        self._queues_lock = threading.Lock()

    @classmethod
    def from_config(
        cls,
        config,
        secret_reader=read_secret,
        web_client_cls=WebClient,
        socket_client_cls=SocketModeClient,
    ):
        """Build the bot from the deployment config and secrets (design D9)."""
        slack_cfg = (config.get("services") or {}).get("slack") or {}
        bot_token = secret_reader("SLACK_BOT_TOKEN")
        app_token = secret_reader("SLACK_APP_TOKEN")
        if not bot_token or not app_token:
            raise RuntimeError(
                "The slack service needs both SLACK_BOT_TOKEN and SLACK_APP_TOKEN."
            )
        web = web_client_cls(token=bot_token)
        identity = web.auth_test()
        bot_user_id = identity["user_id"]
        socket = socket_client_cls(app_token=app_token, web_client=web)
        chat_url = slack_cfg.get("chat_url") or DEFAULT_CHAT_URL
        # 0 is a valid history_limit (send no earlier turns); only null falls back.
        history_limit = slack_cfg.get("history_limit")
        if history_limit is None:
            history_limit = DEFAULT_HISTORY_LIMIT
        allowed_channels = parse_allowed_channels(slack_cfg.get("allowed_channels"))
        internal_users_only = bool(slack_cfg.get("internal_users_only"))
        logger.info(
            f"slack bot: user {bot_user_id}, chat API {chat_url}, "
            f"internal users only {internal_users_only}, "
            f"allowed channels {sorted(allowed_channels) or 'all'}"
        )
        factory = PostgresServiceFactory.get_instance()
        feedback_store = (
            SlackFeedbackStore(factory.connection_pool) if factory is not None else None
        )
        return cls(
            web_client=web,
            socket_client=socket,
            session=requests.Session(),
            chat_url=chat_url,
            bot_user_id=bot_user_id,
            api_token=secret_reader("ARCHI_API_TOKEN", default=""),
            timeout_seconds=int(
                slack_cfg.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS
            ),
            history_limit=int(history_limit),
            max_workers=int(slack_cfg.get("max_workers") or DEFAULT_MAX_WORKERS),
            feedback_store=feedback_store,
            team_id=identity.get("team_id"),
            enterprise_id=identity.get("enterprise_id"),
            internal_users_only=internal_users_only,
            external_user_text=slack_cfg.get("external_user_text")
            or DEFAULT_EXTERNAL_USER_TEXT,
            allowed_channels=allowed_channels,
        )

    def run(self, stop_event=None, sleep=time.sleep):
        """Prove the chat API answers, then listen to Slack until ``stop_event``."""
        self.model = wait_for_model(
            self.session,
            self.chat_url,
            token=self.api_token,
            attempts=self.startup_attempts,
            delay=self.startup_delay,
            sleep=sleep,
        )
        self.leave_unlisted_channels()
        logger.info(f"slack bot: using model {self.model}; connecting to Slack")
        self.socket.socket_mode_request_listeners.append(self.handle_request)
        self.socket.connect()
        (stop_event or threading.Event()).wait()

    def handle_request(self, client, req):
        """Socket Mode listener: acknowledge first, then hand off (design D3)."""
        try:
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
        except Exception:
            logger.exception("slack bot: failed to acknowledge a Socket Mode request")
        try:
            if req.type == "events_api":
                payload = req.payload or {}
                event = payload.get("event") or {}
                if event.get("type") == "member_joined_channel":
                    self._on_member_joined(event)
                    return
                if not should_answer(event, self.bot_user_id):
                    return
                if not channel_allowed(event, self.allowed_channels):
                    return
                if not self._deduper.first_time(event.get("channel"), event.get("ts")):
                    return
                team_id = payload.get("team_id")
                key = thread_key(
                    team_id,
                    event.get("channel"),
                    event.get("thread_ts") or event.get("ts"),
                )
                self._enqueue(key, event, team_id)
            elif req.type == "interactive":
                self.handle_interactive(req.payload or {})
        except Exception:
            logger.exception("slack bot: failed to dispatch a Slack event")

    def handle_interactive(self, payload):
        """Handle block_actions and view_submission payloads; never raises (design D6-D7)."""
        try:
            payload_type = payload.get("type")
            user_id = (payload.get("user") or {}).get("id")
            team_id = (payload.get("team") or {}).get("id")
            access = self._user_access(user_id, team_id)
            if access is not True:
                if payload_type == "block_actions":
                    kwargs = ephemeral_thanks_kwargs(payload, "")
                    self._post_access_notice(
                        access,
                        kwargs["channel"],
                        kwargs["user"],
                        kwargs.get("thread_ts"),
                    )
                return
            if payload_type == "block_actions":
                actions = payload.get("actions") or []
                if not actions:
                    return
                action = actions[0]
                action_id = action.get("action_id")
                message_id = parse_message_id(action.get("value"))
                if message_id is None:
                    return
                if not self._first_interaction(payload):
                    return
                if action_id == HELPFUL_ACTION_ID:
                    try:
                        self.feedback_store.insert(like_record(message_id))
                    except Exception:
                        logger.exception("slack bot: failed to store helpful feedback")
                        self._forget_interaction(payload)
                        return
                    try:
                        self.web.chat_postEphemeral(
                            **ephemeral_thanks_kwargs(payload, FEEDBACK_THANKS_TEXT)
                        )
                    except Exception:
                        logger.exception(
                            "slack bot: failed to post ephemeral feedback thanks"
                        )
                elif action_id == NOT_HELPFUL_ACTION_ID:
                    try:
                        self.web.views_open(
                            trigger_id=payload.get("trigger_id"),
                            view=build_feedback_modal(message_id),
                        )
                    except Exception:
                        logger.exception("slack bot: failed to open feedback modal")
                        self._forget_interaction(payload)
            elif payload_type == "view_submission":
                view = payload.get("view") or {}
                record = parse_feedback_submission(view)
                if record is None:
                    return
                if not self._first_interaction(payload):
                    return
                try:
                    self.feedback_store.insert(record)
                except Exception:
                    logger.exception("slack bot: failed to store dislike feedback")
                    self._forget_interaction(payload)
        except Exception:
            logger.exception("slack bot: failed to handle interactive payload")

    def _user_access(self, user_id, payload_team_id):
        """True to serve the user, False for an external user, None if unknown.

        The lookup runs for each request and is not cached, so a member who becomes
        a guest or leaves the workspace loses access at the next question.
        """
        if not self.internal_users_only:
            return True
        team_id = self.team_id or payload_team_id
        try:
            user = self.web.users_info(user=user_id)["user"]
        except Exception:
            # Fail closed: an unknown user gets no answer.
            logger.exception(f"slack bot: users.info failed for {user_id}")
            return None
        internal = is_internal_user(user, team_id, self.enterprise_id)
        if not internal:
            logger.info(f"slack bot: rejected external or guest user {user_id}")
        return internal

    def _post_access_notice(self, access, channel, user_id, thread_ts):
        """Tell a rejected user why, with one retry; never raises.

        A failed lookup gets the error text, because the sender can be internal.
        The bot acknowledged the event before this point, so Slack sends it no
        second time: the retry here is the only one.
        """
        text = self.external_user_text if access is False else ERROR_TEXT
        kwargs = {"channel": channel, "user": user_id, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        for attempt in (1, 2):
            try:
                self.web.chat_postEphemeral(**kwargs)
                return
            except Exception:
                logger.exception(
                    f"slack bot: failed to post the notice in {channel} "
                    f"(attempt {attempt}/2)"
                )

    def _on_member_joined(self, event):
        if event.get("user") != self.bot_user_id or not self.allowed_channels:
            return
        channel = event.get("channel")
        if channel not in self.allowed_channels:
            self._leave(channel)

    def _leave(self, channel):
        try:
            self.web.conversations_leave(channel=channel)
            logger.info(f"slack bot: left {channel}, which allowed_channels omits")
        except Exception:
            logger.exception(f"slack bot: failed to leave {channel}")

    def leave_unlisted_channels(self):
        """Leave each channel that ``allowed_channels`` omits; never raises."""
        if not self.allowed_channels:
            return
        # One listing per type: each type needs its own read scope, and a missing
        # scope for one type must not stop the bot from leaving the others.
        for conversation_type in ("public_channel", "private_channel", "mpim"):
            try:
                cursor = None
                while True:
                    kwargs = {"types": conversation_type, "limit": 200}
                    if cursor:
                        kwargs["cursor"] = cursor
                    page = self.web.users_conversations(**kwargs)
                    for channel in page.get("channels") or []:
                        if channel.get("id") not in self.allowed_channels:
                            self._leave(channel.get("id"))
                    cursor = (page.get("response_metadata") or {}).get("next_cursor")
                    if not cursor:
                        break
            except Exception:
                logger.exception(
                    f"slack bot: failed to list the bot's {conversation_type} "
                    "conversations"
                )

    def _first_interaction(self, payload):
        """False for a retried interactive envelope already handled; True otherwise."""
        key = interaction_key(payload)
        if key is None:
            return True
        return self._deduper.first_time(*key)

    def _forget_interaction(self, payload):
        """After a failed side effect, let a Slack retry of ``payload`` run again."""
        key = interaction_key(payload)
        if key is not None:
            self._deduper.forget(*key)

    def _enqueue(self, key, event, team_id):
        with self._queues_lock:
            pending = self._thread_queues.get(key)
            if pending is not None:
                pending.append((event, team_id))
                return
            self._thread_queues[key] = deque([(event, team_id)])
        self.executor.submit(self._drain_thread, key)

    def _drain_thread(self, key):
        """Answer one thread's questions in arrival order, then forget the thread."""
        while True:
            with self._queues_lock:
                pending = self._thread_queues[key]
                if not pending:
                    del self._thread_queues[key]
                    return
                event, team_id = pending.popleft()
            try:
                self.answer(event, team_id)
            except Exception:
                logger.exception(f"slack bot: answer raised in thread {key}")

    def _thread_replies(self, channel, thread_ts):
        """Return every message of a thread, following pagination up to a cap."""
        messages = []
        cursor = None
        for _ in range(MAX_REPLY_PAGES):
            kwargs = {"channel": channel, "ts": thread_ts, "limit": REPLIES_PAGE_LIMIT}
            if cursor:
                kwargs["cursor"] = cursor
            page = self.web.conversations_replies(**kwargs)
            messages.extend(page.get("messages") or [])
            cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return messages
        logger.warning(
            f"slack bot: thread {channel} {thread_ts} has more than "
            f"{MAX_REPLY_PAGES} pages; later messages are left out"
        )
        return messages

    def answer(self, event, team_id):
        """Answer one Slack message in its thread; never raises."""
        channel = event.get("channel")
        ts = event.get("ts")
        thread_ts = event.get("thread_ts") or ts
        # The access check runs here, in the thread's ordered worker, not in the
        # Socket Mode listener: a slow lookup there could reorder a thread.
        access = self._user_access(event.get("user"), team_id)
        if access is not True:
            # Only a question already in a thread gets the notice in that thread:
            # Slack does not show an ephemeral reply that would start a thread.
            self._post_access_notice(
                access, channel, event.get("user"), event.get("thread_ts")
            )
            return
        placeholder_ts = None
        try:
            placeholder = self.web.chat_postMessage(
                channel=channel, thread_ts=thread_ts, text=PLACEHOLDER_TEXT
            )
            placeholder_ts = placeholder["ts"]
            replies = []
            if event.get("thread_ts"):
                replies = self._thread_replies(channel, thread_ts)
            messages = build_messages(
                replies,
                self.bot_user_id,
                strip_mention(event.get("text")),
                before_ts=ts,
                limit=self.history_limit,
            )
            logger.info(
                f"slack bot: question from {event.get('user')} in {channel} "
                f"thread {thread_ts} ({len(messages) - 1} earlier turns)"
            )
            result = ask_archi(
                self.session,
                self.chat_url,
                self.model,
                messages,
                thread_key(team_id, channel, thread_ts),
                token=self.api_token,
                timeout=self.timeout_seconds,
            )
            final_text = to_mrkdwn(result.text)
        except Exception:
            logger.exception(f"slack bot: failed to answer {channel} {ts}")
            try:
                self._post_final(channel, thread_ts, placeholder_ts, ERROR_TEXT)
            except Exception:
                logger.exception(f"slack bot: failed to report the error in {channel}")
            return
        delivered = self._deliver_answer(channel, placeholder_ts, final_text)
        if (
            delivered
            and self.feedback_store is not None
            and result.message_id is not None
            and result.message_id > 0
        ):
            try:
                self.web.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=FEEDBACK_PROMPT_TEXT,
                    blocks=build_feedback_blocks(result.message_id),
                )
            except Exception:
                logger.exception(
                    f"slack bot: failed to post feedback prompt in {channel}"
                )

    def _deliver_answer(self, channel, placeholder_ts, text):
        """Put the answer in the placeholder; retry the same text once on failure.

        Slack can apply an update and still time out to the client, so a failed
        delivery never falls back to the error line: that could replace a real answer.
        Updating the placeholder with the same text twice is safe.
        Returns True if delivery succeeded, False if both attempts failed.
        """
        for attempt in (1, 2):
            try:
                self.web.chat_update(channel=channel, ts=placeholder_ts, text=text)
                return True
            except Exception:
                logger.exception(
                    f"slack bot: failed to deliver the answer in {channel} "
                    f"(attempt {attempt}/2)"
                )
        return False

    def _post_final(self, channel, thread_ts, placeholder_ts, text):
        if placeholder_ts:
            self.web.chat_update(channel=channel, ts=placeholder_ts, text=text)
        else:
            self.web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
