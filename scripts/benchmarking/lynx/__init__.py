"""PatronusAI Lynx faithfulness verdicts over an offload judge bundle (#734).

Lynx gives one PASS or FAIL verdict per row, so two arms compare row by row with
the exact McNemar test of ``scripts/benchmarking/paired_tests.py``. It is not a
RAGAS metric and feeds no gate. Every module here imports only the standard
library at module level; ``backends.VllmBackend`` imports ``vllm`` when built.
"""
