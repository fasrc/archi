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

import requests
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse

from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Slack rejects a message `text` over 40,000 characters; keep a margin (design D8).
MAX_TEXT_CHARS = 39_000
TRUNCATION_MARKER = "\n\n_[answer truncated]_"

PLACEHOLDER_TEXT = "_Searching the archi knowledge base…_"
ERROR_TEXT = (
    ":warning: archi could not answer this question. "
    "An operator can find the reason in the slack service log."
)

_STATUS_TEXTS = frozenset({PLACEHOLDER_TEXT, ERROR_TEXT})

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
        return resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ArchiApiError("/v1/chat/completions returned an unexpected body") from exc


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
        "is true."
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
        bot_user_id = web.auth_test()["user_id"]
        socket = socket_client_cls(app_token=app_token, web_client=web)
        chat_url = slack_cfg.get("chat_url") or DEFAULT_CHAT_URL
        # 0 is a valid history_limit (send no earlier turns); only null falls back.
        history_limit = slack_cfg.get("history_limit")
        if history_limit is None:
            history_limit = DEFAULT_HISTORY_LIMIT
        logger.info(f"slack bot: user {bot_user_id}, chat API {chat_url}")
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
            if req.type != "events_api":
                return
            payload = req.payload or {}
            event = payload.get("event") or {}
            if not should_answer(event, self.bot_user_id):
                return
            if not self._deduper.first_time(event.get("channel"), event.get("ts")):
                return
            team_id = payload.get("team_id")
            key = thread_key(
                team_id, event.get("channel"), event.get("thread_ts") or event.get("ts")
            )
            self._enqueue(key, event, team_id)
        except Exception:
            logger.exception("slack bot: failed to dispatch a Slack event")

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
            answer = ask_archi(
                self.session,
                self.chat_url,
                self.model,
                messages,
                thread_key(team_id, channel, thread_ts),
                token=self.api_token,
                timeout=self.timeout_seconds,
            )
            final_text = to_mrkdwn(answer)
        except Exception:
            logger.exception(f"slack bot: failed to answer {channel} {ts}")
            try:
                self._post_final(channel, thread_ts, placeholder_ts, ERROR_TEXT)
            except Exception:
                logger.exception(f"slack bot: failed to report the error in {channel}")
            return
        self._deliver_answer(channel, placeholder_ts, final_text)

    def _deliver_answer(self, channel, placeholder_ts, text):
        """Put the answer in the placeholder; retry the same text once on failure.

        Slack can apply an update and still time out to the client, so a failed
        delivery never falls back to the error line: that could replace a real answer.
        Updating the placeholder with the same text twice is safe.
        """
        for attempt in (1, 2):
            try:
                self.web.chat_update(channel=channel, ts=placeholder_ts, text=text)
                return
            except Exception:
                logger.exception(
                    f"slack bot: failed to deliver the answer in {channel} "
                    f"(attempt {attempt}/2)"
                )

    def _post_final(self, channel, thread_ts, placeholder_ts, text):
        if placeholder_ts:
            self.web.chat_update(channel=channel, ts=placeholder_ts, text=text)
        else:
            self.web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
