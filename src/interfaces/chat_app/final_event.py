"""Assembly of ``ChatWrapper.stream()``'s ``final`` stream event.

Extracted so the event contract — including the ``answer`` field — is
unit-testable without driving the whole streaming pipeline.
"""

from datetime import datetime, timezone
from typing import Any, Dict


def build_final_event(
    *,
    last_output,
    response,
    conversation_id,
    message_ids,
    trace_id,
    server_response_msg_ts,
    usage,
    model,
    model_used,
    source_documents,
    retriever_scores,
) -> Dict[str, Any]:
    """Build the ``final`` event dict yielded at the end of a chat stream.

    ``answer`` is the bare pipeline answer, without the wrapper's appended
    source list — extracted from ``last_output.get("answer")`` when
    ``last_output`` is truthy. It is included only when non-``None``; a
    missing pipeline answer must stay distinguishable from a legitimately
    empty one, since the ``/v1`` endpoint treats an absent key as its
    defensive arm.
    """
    answer = last_output.get("answer") if last_output else None

    event: Dict[str, Any] = {
        "type": "final",
        "response": response,
        "conversation_id": conversation_id,
        "archi_msg_id": message_ids[-1] if message_ids else None,
        "message_id": message_ids[-1] if message_ids else None,
        "user_message_id": (
            message_ids[0] if message_ids and len(message_ids) > 1 else None
        ),
        "trace_id": trace_id,
        "server_response_msg_ts": server_response_msg_ts,
        "final_response_msg_ts": datetime.now(timezone.utc).timestamp(),
        "usage": usage,
        "model": model,
        "model_used": model_used,
        "source_documents": source_documents,
        "retriever_scores": retriever_scores,
    }
    if answer is not None:
        event["answer"] = answer

    return event
