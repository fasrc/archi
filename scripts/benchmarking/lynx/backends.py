"""Lynx backends: ``generate(prompts) -> list[str]`` and ``count_tokens(text) -> int``.

``VllmBackend`` imports ``vllm`` in its constructor, never at module level: the
gate env has no ``vllm`` and no ``transformers``, and the scorer runs inside a
vLLM container on the cluster (#735). Tests use ``StubBackend``.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Union

#: Lynx (Llama 3) reads 8,000 tokens; the model card generates 600.
CONTEXT_TOKENS = 8000
MAX_NEW_TOKENS = 600

STUB_OUTPUT = '{"REASONING": ["stub backend"], "SCORE": "PASS"}'


class StubBackend:
    """A backend for tests: records every prompt and answers without a model."""

    name = "stub"

    def __init__(
        self,
        output: Union[str, Callable[[str], str]] = STUB_OUTPUT,
        token_counter: Optional[Callable[[str], int]] = None,
    ) -> None:
        self.output = output
        self.received: List[str] = []
        self._count = token_counter or (lambda text: len(text.split()))

    def count_tokens(self, text: str) -> int:
        return self._count(text)

    def generate(self, prompts: Sequence[str]) -> List[str]:
        self.received.extend(prompts)
        if callable(self.output):
            return [self.output(prompt) for prompt in prompts]
        return [self.output for _ in prompts]


class VllmBackend:
    """vLLM offline inference at a pinned revision, greedy, 600 new tokens.

    ``tensor_parallel_size`` is the number of GPUs that vLLM splits the model
    over; Lynx 70B does not fit on one GPU (#755).

    The model card trained Lynx on chat format and sends the prompt as one user
    message, so each prompt goes through the tokenizer's chat template. The
    template's token ids go to ``generate`` and are also what ``count_tokens``
    counts, so the token budget counts exactly what the model gets.
    """

    name = "vllm"

    def __init__(
        self, model: str, revision: str, *, tensor_parallel_size: int = 1
    ) -> None:
        from vllm import LLM, SamplingParams

        self._llm = LLM(
            model=model,
            revision=revision,
            tokenizer_revision=revision,
            tensor_parallel_size=tensor_parallel_size,
        )
        self._params = SamplingParams(temperature=0, max_tokens=MAX_NEW_TOKENS)
        self._tokenizer = self._llm.get_tokenizer()

    def _token_ids(self, prompt: str) -> List[int]:
        text = self._tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
        # The chat text already holds the begin-of-text token.
        return list(self._tokenizer.encode(text, add_special_tokens=False))

    def count_tokens(self, text: str) -> int:
        return len(self._token_ids(text))

    def generate(self, prompts: Sequence[str]) -> List[str]:
        requests = [{"prompt_token_ids": self._token_ids(p)} for p in prompts]
        outputs = self._llm.generate(requests, self._params)
        return [output.outputs[0].text for output in outputs]


def make_backend(
    name: str, model: str, revision: str, *, tensor_parallel_size: int = 1
):
    """The backend called *name* (``vllm`` or ``stub``).

    The stub ignores ``tensor_parallel_size``.
    """
    if name == "stub":
        return StubBackend()
    if name == "vllm":
        return VllmBackend(model, revision, tensor_parallel_size=tensor_parallel_size)
    raise ValueError(f"unknown backend {name!r}; allowed: 'vllm', 'stub'")
