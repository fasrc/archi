#!/usr/bin/env bash
# Self-test for the feature-matrix runbook wrappers: stubbed docker and archi, a fake
# stack under a temp ARCHI_DIR, fixture artifacts — no network, no containers, nothing
# written outside the sandbox. Contract under test:
#    1. an arm label that is not NN or NNa is refused before anything runs
#    2. run_arm.sh <arm> <yaml> calls `archi evaluate --name fm-<arm> ... --hostmode`
#    3. run_arm.sh refuses without the judge env file
#    4. run_arm.sh --rerun refuses when no corpus pin exists yet
#    5. archive_run.sh refuses an artifact whose run diverged from the selected config
#       and writes neither ledger entry nor pin
#    6. archive_run.sh records the run, recomputes scored counts from finite values,
#       tolerates bare NaN, and writes the corpus pin on run 1
#    7. run_arm.sh --rerun on a pinned corpus recreates ONLY the benchmark service
#    8. run_arm.sh --rerun refuses when the live fingerprint differs from the pin
#    9. reseed_arm.sh refuses an arm that changes an ingest-side key (chunking.strategy)
#   10. reseed_arm.sh copies the retrieval key into the rendered config, backs the
#       original up OUTSIDE configs/, and recreates config-seed
#   11. qa_arm.sh overwrites chat_app's SUT fields from services.benchmarking, drops the
#       evaluations block, and calls `archi eval qa` with one attempt and one run worker
#   12. qa_arm.sh refuses when the converted QA dataset is missing
#   13. archive_run.sh refuses a later run whose fingerprint differs from the pin; --new-corpus
#       is refused after a re-run and re-pins only after a fresh arm-00 deploy, recording the old pin
#   14. qa_arm.sh refuses when the stack's rendered config is not on the requested arm
#   15. qa_arm.sh records the rendered config's sha256 and the corpus fingerprint
#   16. archive_run.sh refuses an artifact that is already in the ledger
#   17. archive_run.sh refuses an artifact older than the stack's latest ragas-start
#   18. an arm label that does not match the YAML's own `name` is refused
#   19. archive_run.sh refuses an artifact whose recorded running configuration is not the arm's
#   20. archive_run.sh records the arm config, the selected file, and the fingerprint source
#   21. qa_arm.sh refuses a corpus that drifted from the stack's pin
#   22. an arm YAML that lacks a factor key is refused (fail closed)
#   23. archive_run.sh refuses an artifact without running_configuration
#   24. nothing runs before lock_campaign.sh has written the campaign lock
#   25. lock_campaign.sh records the pinned inputs once and refuses a silent re-lock
#   26. a same-label arm YAML that names a different bank is refused by the lock
#   27. qa_arm.sh refuses a dataset whose content differs from the lock
#   28. --new-corpus is refused for a non-baseline arm (13 covers the re-run case)
#   29. archive_run.sh refuses an artifact whose corpus changed between its endpoints
#   30. the agent class is a locked fixed factor
#   31-32. a checkout that moved past the locked code, or carries tracked source changes, is refused
#   33. reseed_arm.sh --no-run restores a configuration without starting a run
#   34. archive_run.sh refuses a duplicate (arm, stack, run) identity
#   35. qa_arm.sh refuses a non-numeric run number before anything runs
#   36. archive_run.sh accepts only run 1 while a stack has no pin
# Run: bash scripts/benchmarking/feature_matrix/test_feature_matrix_wrappers.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
ok()    { printf 'ok - %s\n' "$1"; PASS=$((PASS + 1)); }
notok() { printf 'not ok - %s\n' "$1"; FAIL=$((FAIL + 1)); }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export HOME="$T/home"; mkdir -p "$HOME"
export ARCHI_DIR="$T/archi" FM_OUT="$T/out"
export FM_DOCKER="$T/bin/docker" FM_ARCHI="$T/bin/archi" FM_PYTHON="${FM_PYTHON:-python3}"
export FM_POLL_SECONDS=0
unset RAGAS_ENV_FILE HUIT_API_KEY_FILE OPENAI_API_KEY FM_AGENT_SPEC
mkdir -p "$T/bin" "$T/state" "$FM_OUT"
printf 'sha256:abc\n' > "$T/fp"

# --- stubs -----------------------------------------------------------------------------
cat > "$T/bin/docker" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$T/docker.calls"
case "\$1" in
  inspect) [ -f "$T/state/\$2" ] && { cat "$T/state/\$2"; exit 0; } || exit 1 ;;
  exec)    sql="\$*"
           case "\$sql" in
             *benchmark_provenance*) cat "$T/fp" ;;
             *document_chunks*)  echo 6096 ;;
             *documents*)        echo 1132 ;;
           esac ;;
  *) exit 0 ;;
esac
EOF
cat > "$T/bin/archi" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$T/archi.calls"
EOF
cat > "$T/bin/git" <<EOF
#!/usr/bin/env bash
case "\$1" in
  rev-parse) cat "$T/codesha" ;;
  status)    cat "$T/dirty" 2>/dev/null || true ;;
  *) exit 0 ;;
esac
EOF
chmod +x "$T/bin/docker" "$T/bin/archi" "$T/bin/git"
export FM_GIT="$T/bin/git"; printf 'c0ffee00\n' > "$T/codesha"; : > "$T/dirty"

# --- fake stack fm-00 --------------------------------------------------------------------
S="$ARCHI_DIR/archi-fm-00"; mkdir -p "$S/configs" "$S/secrets"
: > "$S/compose.yaml"; printf 'pw\n' > "$S/secrets/pg_password.txt"; printf 'k\n' > "$S/secrets/huit_api_key.txt"
cat > "$S/configs/config.yaml" <<'EOF'
name: fm-00
services:
  chat_app:
    agent_class: CMSCompOpsAgent
    default_provider: local
    default_model: llama3.2
    evaluations:
      enabled: false
    providers:
      openai:
        api_key: EMPTY
        base_url: http://archi.rc.fas.harvard.edu:8001/v1
  benchmarking:
    agent_class: FASRCDocsAgent
    provider: openai
    model: palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4
  postgres:
    host: localhost
    port: 5434
data_manager:
  chunking:
    strategy: sentence
  processing:
    html_to_markdown:
      enabled: true
    categorization:
      enabled: true
  stemming:
    enabled: false
  retrievers:
    hierarchical_rerank:
      enabled: true
      candidate_pool_size: 20
      num_documents_to_retrieve: 5
EOF
printf 'running\n' > "$T/state/postgres-fm-00"; printf 'running\n' > "$T/state/data-manager-fm-00"
printf 'exited\n' > "$T/state/benchmarking-fm-00"

# --- arm fixtures ------------------------------------------------------------------------
# Every arm YAML carries the same pinned inputs (bank, anchors, prompt, sources, SUT, judge)
# and differs only in the factor keys, exactly like the real files in archi-config.
mkdir -p "$T/arms" "$T/cfg/qa" "$FM_OUT/qa"
printf '[{"user_input": "q1", "reference": "a1"}]\n' > "$T/cfg/bank.json"
printf '[{"user_input": "anchor", "reference": "r", "anchor_type": "should_refuse"}]\n' > "$T/cfg/anchors.json"
printf 'https://docs.example/kb/\n' > "$T/cfg/sources.list"
printf -- '---\nname: x\ntools: []\n---\n' > "$T/cfg/spec.md"
printf 'version: 1\n' > "$T/cfg/qa/profile.yaml"
printf '{"format":"qa-dataset-v2","items":[]}\n' > "$FM_OUT/qa/fasrc_ragas_queries.qa-v2.json"
mk_arm() { # path name strategy html categorization stemming rerank k [bank]
  cat > "$1" <<EOF
name: $2
data_manager:
  sources: {links: {input_lists: [$T/cfg/sources.list]}}
  embedding_name: HuggingFaceEmbeddings
  chunking: {strategy: $3}
  processing: {html_to_markdown: {enabled: $4}, categorization: {enabled: $5}}
  stemming: {enabled: $6}
  retrievers: {hierarchical_rerank: {enabled: $7, candidate_pool_size: 20, num_documents_to_retrieve: $8}}
services:
  benchmarking:
    agent_class: FASRCDocsAgent
    provider: openai
    model: palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4
    queries_path: ${9:-$T/cfg/bank.json}
    agent_md_file: $T/cfg/spec.md
    anchors: {enabled: true, path: $T/cfg/anchors.json}
    modes: [RAGAS, SOURCES]
    mode_settings: {ragas_settings: {embedding_model: HuggingFace, evaluator_provider: huit_bedrock, evaluator_model: sonnet-4-5, enabled_metrics: [answer_relevancy, faithfulness]}}
  chat_app:
    providers: {openai: {base_url: http://sut:8001/v1, extra_kwargs: {temperature: 0.3}}}
EOF
}
mk_arm "$T/arms/00-baseline.yaml"           fm-00  sentence  true true false true  5
mk_arm "$T/arms/05a-k3.yaml"                fm-05a sentence  true true false true  3
mk_arm "$T/arms/01-rerank-off.yaml"         fm-01  sentence  true true false false 5
mk_arm "$T/arms/02-chunking-character.yaml" fm-02  character true true false false 5
printf 'HUIT_API_KEY=x\n' > "$T/judge.env"

artifact() { # $1 = path, $2 = divergence JSON, $3 = fingerprint, $4 = k in the recorded running configuration (default 5), $5 = fingerprint BEFORE the run (default = $3)
  cat > "$1" <<EOF
{"metadata": {"corpus_snapshot_id": "snap-1", "code_version": {"digest": "sha256:code"}},
 "benchmarking_results": [{
   "configuration_file": "configs/config.yaml",
   "running_configuration": {"data_manager": {"chunking": {"strategy": "sentence"},
     "processing": {"html_to_markdown": {"enabled": true}, "categorization": {"enabled": true}},
     "stemming": {"enabled": false},
     "retrievers": {"hierarchical_rerank": {"enabled": true, "candidate_pool_size": 20, "num_documents_to_retrieve": ${4:-5}}}}},
   "config_version": {"digest": "sha256:cfg", "divergence_from_selected_file": $2},
   "corpus_fingerprint": "sha256:$3", "corpus_fingerprint_before": "sha256:${5:-$3}", "corpus_unchanged_at_endpoints": $( [ "${5:-$3}" = "$3" ] && echo true || echo false ), "ingest_wall_seconds": 4321.0,
   "total_results": {"aggregate_context_precision": 0.5, "context_precision_scored": "3 of 3", "aggregate_faithfulness": 0.6},
   "single_question_results": {
     "question_1": {"question": "q1", "status": "ok", "context_precision": 0.4, "faithfulness": 0.6, "time_elapsed": 10},
     "question_2": {"question": "q2", "status": "ok", "context_precision": NaN, "faithfulness": 0.7, "time_elapsed": 12},
     "question_3": {"question": "q3", "status": "degraded", "context_precision": 0.9, "faithfulness": 0.5, "time_elapsed": 90}}}]}
EOF
}

run() { set +e; "$@" >"$T/stdout" 2>"$T/stderr"; RC=$?; set -e; }

# 24: nothing runs before the campaign is locked
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml"
[ "$RC" = 2 ] && grep -q "no campaign lock" "$T/stderr" && ok "run_arm refuses before the campaign is locked" || notok "lock precondition (rc=$RC: $(cat "$T/stderr"))"

# 25: lock_campaign writes the lock once; a second lock needs --relock
run bash "$HERE/lock_campaign.sh" "$T/arms/00-baseline.yaml" --qa-dataset "$FM_OUT/qa/fasrc_ragas_queries.qa-v2.json" --qa-profile "$T/cfg/qa/profile.yaml"
R1=$RC
run bash "$HERE/lock_campaign.sh" "$T/arms/00-baseline.yaml" --qa-dataset "$FM_OUT/qa/fasrc_ragas_queries.qa-v2.json" --qa-profile "$T/cfg/qa/profile.yaml"
if [ "$R1" = 0 ] && [ "$RC" = 2 ] && grep -q "already locked" "$T/stderr" && "$FM_PYTHON" -c "import json,sys; l=json.load(open('$FM_OUT/campaign.lock')); sys.exit(0 if set(l['files'])=={'bank','anchors','prompt','sources[0]'} and l['values']['judge.model']=='sonnet-4-5' and l['qa']['dataset_sha256'] else 1)"; then ok "lock_campaign records the pinned inputs and refuses a silent re-lock"; else notok "lock_campaign (rc1=$R1 rc2=$RC: $(cat "$T/stderr"))"; fi
rm -f "$FM_OUT/qa/fasrc_ragas_queries.qa-v2.json"    # checks 12/14 expect it absent until they create it

# 1
run bash "$HERE/run_arm.sh" "0x" "$T/arms/01-rerank-off.yaml"
[ "$RC" = 2 ] && grep -q "bad arm label" "$T/stderr" && ok "bad arm label refused" || notok "bad arm label refused (rc=$RC)"

# 2
: > "$T/archi.calls"
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml"
if [ "$RC" = 0 ] && grep -qx "evaluate --name fm-00 --config $T/arms/00-baseline.yaml --env-file $T/judge.env --hostmode" "$T/archi.calls"; then ok "run_arm calls archi evaluate --hostmode"; else notok "run_arm calls archi evaluate --hostmode (rc=$RC: $(cat "$T/archi.calls" "$T/stderr"))"; fi

# 3
run bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml"
[ "$RC" = 2 ] && grep -q "RAGAS_ENV_FILE" "$T/stderr" && ok "run_arm refuses without the judge env file" || notok "run_arm refuses without the judge env file (rc=$RC)"

# 4
run bash "$HERE/run_arm.sh" 00 --rerun
[ "$RC" = 2 ] && grep -q "no corpus pin" "$T/stderr" && ok "rerun refuses without a pin" || notok "rerun refuses without a pin (rc=$RC: $(cat "$T/stderr"))"

# 5  (the ledger already holds the ragas-start rows from checks 2 and 4; a refusal must add nothing)
ledger_rows() { "$FM_PYTHON" -c "import json,sys; print(len(json.load(open(sys.argv[1]))) if __import__('os').path.exists(sys.argv[1]) else 0)" "$FM_OUT/ledger.json"; }
artifact "$FM_OUT/benchmarking-fm-00-20260903_000001.json" '["data_manager.retrievers.hierarchical_rerank.enabled"]' abc
BEFORE="$(ledger_rows)"
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml"
if [ "$RC" = 2 ] && grep -q "divergence_from_selected_file" "$T/stderr" && [ "$(ledger_rows)" = "$BEFORE" ] && [ ! -f "$FM_OUT/corpus-pin-fm-00" ]; then ok "archive refuses a diverged run, writes nothing"; else notok "archive refuses a diverged run (rc=$RC: $(cat "$T/stderr"))"; fi
rm -f "$FM_OUT"/benchmarking-fm-00-*.json

# 6
artifact "$FM_OUT/benchmarking-fm-00-20260903_000002.json" '[]' abc
run bash "$HERE/archive_run.sh" 00 1 "$T/arms/00-baseline.yaml"
if [ "$RC" = 0 ] && [ "$(cat "$FM_OUT/corpus-pin-fm-00")" = sha256:abc ] && "$FM_PYTHON" - "$FM_OUT/ledger.json" <<'EOF'
import json, sys
rows = json.load(open(sys.argv[1])); e = rows[-1]
assert e["kind"] == "ragas" and e["run"] == 1 and e["arm"] == "00", e
assert e["corpus_fingerprint"] == "sha256:abc" and e["documents"] == 1132 and e["chunks"] == 6096, e
assert e["scored"]["context_precision"] == "1 of 3", e["scored"]      # q2 NaN, q3 degraded
assert e["scored"]["faithfulness"] == "2 of 3", e["scored"]
assert e["degraded"] == 1 and e["ingest_wall_seconds"] == 4321.0 and e["code_digest"] == "sha256:code", e
EOF
then ok "archive records the run, recomputes scored counts, writes the pin"; else notok "archive records the run (rc=$RC: $(cat "$T/stderr"))"; fi

# 7
: > "$T/docker.calls"
run bash "$HERE/run_arm.sh" 00 --rerun
if [ "$RC" = 0 ] && grep -q "compose -f $S/compose.yaml up --no-deps -d benchmark" "$T/docker.calls" && ! grep -q -E "up.*(postgres|data-manager)" "$T/docker.calls"; then ok "rerun recreates only the benchmark service"; else notok "rerun recreates only the benchmark service (rc=$RC: $(cat "$T/stderr"))"; fi

# 8
printf 'sha256:zzz\n' > "$T/fp"
run bash "$HERE/run_arm.sh" 00 --rerun
[ "$RC" = 2 ] && grep -q "fingerprint sha256:zzz != pin sha256:abc" "$T/stderr" && ok "rerun refuses a drifted corpus" || notok "rerun refuses a drifted corpus (rc=$RC: $(cat "$T/stderr"))"
printf 'sha256:abc\n' > "$T/fp"

# 9
cp "$S/configs/config.yaml" "$T/rendered.before"
run bash "$HERE/reseed_arm.sh" 02 "$T/arms/02-chunking-character.yaml" --stack fm-00
if [ "$RC" = 2 ] && grep -q "ingest-side" "$T/stderr" && cmp -s "$S/configs/config.yaml" "$T/rendered.before"; then ok "reseed refuses an ingest-side arm"; else notok "reseed refuses an ingest-side arm (rc=$RC: $(cat "$T/stderr"))"; fi

# 14 (before the re-seed: the stack is still on the baseline, so arm 01 must be refused)
mkdir -p "$FM_OUT/qa"; printf '{"format":"qa-dataset-v2","items":[]}\n' > "$FM_OUT/qa/fasrc_ragas_queries.qa-v2.json"
: > "$T/archi.calls"
run env FM_AGENT_SPEC="$T/cfg/spec.md" bash "$HERE/qa_arm.sh" 01 "$T/arms/01-rerank-off.yaml" --stack fm-00 --profile "$T/cfg/qa/profile.yaml"
if [ "$RC" = 2 ] && grep -q "factor retrievers.hierarchical_rerank.enabled: arm=False stack=True" "$T/stderr" && [ ! -s "$T/archi.calls" ]; then ok "qa_arm refuses a stack that is not on the requested arm"; else notok "qa_arm refuses a stack not on the arm (rc=$RC: $(cat "$T/stderr"))"; fi
rm -f "$FM_OUT/qa/fasrc_ragas_queries.qa-v2.json"

# 10
: > "$T/docker.calls"
run bash "$HERE/reseed_arm.sh" 01 "$T/arms/01-rerank-off.yaml" --stack fm-00
if [ "$RC" = 0 ] && grep -q "up --force-recreate config-seed" "$T/docker.calls" && [ -f "$S/fm-backup/config.yaml" ] && [ "$(ls "$S/configs" | wc -l)" = 1 ] \
   && "$FM_PYTHON" -c "import yaml,sys; c=yaml.safe_load(open('$S/configs/config.yaml')); hr=c['data_manager']['retrievers']['hierarchical_rerank']; sys.exit(0 if hr['enabled'] is False and hr['num_documents_to_retrieve']==5 and c['data_manager']['chunking']['strategy']=='sentence' else 1)"; then
  ok "reseed writes the retrieval key, backs up outside configs/, recreates config-seed"; else notok "reseed writes the retrieval key (rc=$RC: $(cat "$T/stderr"))"; fi

# 12 (before 11: the dataset does not exist yet)
run bash "$HERE/qa_arm.sh" 01 "$T/arms/01-rerank-off.yaml" --stack fm-00 --profile "$T/cfg/qa/profile.yaml"
[ "$RC" = 2 ] && grep -q "QA dataset not found" "$T/stderr" && ok "qa_arm refuses without the converted dataset" || notok "qa_arm refuses without the converted dataset (rc=$RC: $(cat "$T/stderr"))"

# 11 + 15
mkdir -p "$FM_OUT/qa"; printf '{"format":"qa-dataset-v2","items":[]}\n' > "$FM_OUT/qa/fasrc_ragas_queries.qa-v2.json"
: > "$T/archi.calls"
run env FM_AGENT_SPEC="$T/cfg/spec.md" bash "$HERE/qa_arm.sh" 01 "$T/arms/01-rerank-off.yaml" --stack fm-00 --profile "$T/cfg/qa/profile.yaml"
if [ "$RC" = 0 ] && grep -q -- "eval qa --dataset $FM_OUT/qa/fasrc_ragas_queries.qa-v2.json --agent-config $FM_OUT/qa/01.agent-config.yaml --agent-spec $T/cfg/spec.md --evaluator-profile $T/cfg/qa/profile.yaml --output-dir $FM_OUT/qa/fm-00-arm01-r1 --attempts 1 --run-workers 1 --score-workers 4" "$T/archi.calls" \
   && "$FM_PYTHON" -c "import yaml,sys; c=yaml.safe_load(open('$FM_OUT/qa/01.agent-config.yaml'))['services']['chat_app']; sys.exit(0 if (c['agent_class'],c['default_provider'],c['default_model'])==('FASRCDocsAgent','openai','palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4') and 'evaluations' not in c and c['providers']['openai']['api_key']=='EMPTY' else 1)" \
   && "$FM_PYTHON" -c "import json,sys; e=json.load(open('$FM_OUT/ledger.json'))[-1]; sys.exit(0 if e['kind']=='qa' and e['arm']=='01' and e['stack']=='fm-00' else 1)"; then
  ok "qa_arm overwrites the SUT fields, drops evaluations, calls archi eval qa serially"; else notok "qa_arm agent config + call (rc=$RC: $(cat "$T/archi.calls" "$T/stderr"))"; fi
EXPECT_SHA="$(sha256sum "$S/configs/config.yaml" | cut -d' ' -f1)"
if "$FM_PYTHON" -c "import json,sys; e=json.load(open('$FM_OUT/ledger.json'))[-1]; sys.exit(0 if e.get('rendered_config_sha256')=='$EXPECT_SHA' and e.get('corpus_fingerprint')=='sha256:abc' and e.get('arm_config')=='$T/arms/01-rerank-off.yaml' else 1)"; then ok "qa_arm records the rendered config sha256, the arm config, and the corpus fingerprint"; else notok "qa_arm ledger identity fields"; fi

# 13: a drifted fingerprint is refused; --new-corpus is refused after a re-run, honoured only after a fresh deploy of arm 00
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000003.json" '[]' def
run bash "$HERE/archive_run.sh" 00 2 "$T/arms/00-baseline.yaml"
R1=$RC
run bash "$HERE/archive_run.sh" 00 2 "$T/arms/00-baseline.yaml" --new-corpus
R2=$RC; grep -q "needs a fresh deploy" "$T/stderr" && R2M=1 || R2M=0
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml"   # a fresh deploy start for fm-00
touch "$FM_OUT/benchmarking-fm-00-20260903_000003.json"                                          # the artifact that deploy wrote
run bash "$HERE/archive_run.sh" 00 2 "$T/arms/00-baseline.yaml" --new-corpus
if [ "$R1" = 2 ] && [ "$R2" = 2 ] && [ "$R2M" = 1 ] && [ "$RC" = 0 ] && [ "$(cat "$FM_OUT/corpus-pin-fm-00")" = sha256:def ] \
   && "$FM_PYTHON" -c "import json,sys; e=json.load(open('$FM_OUT/ledger.json'))[-1]; sys.exit(0 if e['repinned_from']=='sha256:abc' and e['corpus_fingerprint']=='sha256:def' else 1)"; then
  ok "archive refuses a drifted fingerprint; --new-corpus re-pins only after a fresh arm-00 deploy and records the old pin"; else notok "archive fingerprint gate (rc1=$R1 rc2=$R2 m=$R2M rc3=$RC: $(cat "$T/stderr"))"; fi

# 16: the same artifact again → refused, ledger unchanged
BEFORE="$(ledger_rows)"
run bash "$HERE/archive_run.sh" 00 3 "$T/arms/00-baseline.yaml"
if [ "$RC" = 2 ] && grep -q "already archived as arm 00 run 2" "$T/stderr" && [ "$(ledger_rows)" = "$BEFORE" ]; then ok "archive refuses an artifact already in the ledger"; else notok "archive duplicate guard (rc=$RC: $(cat "$T/stderr"))"; fi

# 17: a re-run that produced nothing leaves run 2's file as the newest; the file predates
# the new ragas-start, so archiving "run 3" must refuse
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000004.json" '[]' def
touch -d '2020-01-01T00:00:00Z' "$FM_OUT/benchmarking-fm-00-20260903_000004.json"
run bash "$HERE/run_arm.sh" 00 --rerun          # appends a fresh ragas-start for fm-00
run bash "$HERE/archive_run.sh" 00 3 "$T/arms/00-baseline.yaml"
if [ "$RC" = 2 ] && grep -q "before the latest ragas-start" "$T/stderr"; then ok "archive refuses an artifact older than the latest ragas-start"; else notok "archive stale-artifact guard (rc=$RC: $(cat "$T/stderr"))"; fi

# 18: the operator's label must match the YAML's own name
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 01 "$T/arms/05a-k3.yaml"
[ "$RC" = 2 ] && grep -q "arm label 01 does not match" "$T/stderr" && ok "run_arm refuses a label that does not match the YAML's name" || notok "label/YAML mismatch guard (rc=$RC: $(cat "$T/stderr"))"

# 19: the artifact proves which arm ran — a baseline artifact archived as arm 05a is refused
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000005.json" '[]' def 5
run bash "$HERE/archive_run.sh" 05a 1 "$T/arms/05a-k3.yaml" --stack fm-00
[ "$RC" = 2 ] && grep -q "artifact ran factor retrievers.hierarchical_rerank.num_documents_to_retrieve=5, arm 05a wants 3" "$T/stderr" && ok "archive refuses an artifact whose running configuration is not the arm's" || notok "archive running-configuration guard (rc=$RC: $(cat "$T/stderr"))"

# 20: the same artifact recorded with k=3 archives as 05a and carries the arm config + fingerprint source
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000006.json" '[]' def 3
run bash "$HERE/archive_run.sh" 05a 1 "$T/arms/05a-k3.yaml" --stack fm-00
if [ "$RC" = 0 ] && "$FM_PYTHON" -c "import json,sys; e=json.load(open('$FM_OUT/ledger.json'))[-1]; sys.exit(0 if e['arm']=='05a' and e['arm_config']=='$T/arms/05a-k3.yaml' and e['configuration_file']=='configs/config.yaml' and e['fingerprint_source']=='artifact' else 1)"; then ok "archive records the arm config, the selected file, and the fingerprint source"; else notok "archive identity fields (rc=$RC: $(cat "$T/stderr"))"; fi

# 21: qa_arm refuses a corpus that drifted from the pin (pin is now def, live is abc)
printf 'sha256:abc\n' > "$T/fp"
run env FM_AGENT_SPEC="$T/cfg/spec.md" bash "$HERE/qa_arm.sh" 01 "$T/arms/01-rerank-off.yaml" --stack fm-00 --profile "$T/cfg/qa/profile.yaml" --run 2
[ "$RC" = 2 ] && grep -q "fingerprint sha256:abc != pin sha256:def" "$T/stderr" && [ ! -e "$FM_OUT/qa/fm-00-arm01-r2" ] && ok "qa_arm refuses a corpus that drifted from the pin" || notok "qa_arm pin guard (rc=$RC: $(cat "$T/stderr"))"

# 22: a sparse arm YAML (no stemming key) is refused everywhere the YAML is accepted
cat > "$T/arms/sparse.yaml" <<'EOF'
name: fm-00
data_manager:
  chunking: {strategy: sentence}
  processing: {html_to_markdown: {enabled: true}, categorization: {enabled: true}}
  retrievers: {hierarchical_rerank: {enabled: true, candidate_pool_size: 20, num_documents_to_retrieve: 5}}
EOF
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/sparse.yaml"
[ "$RC" = 2 ] && grep -q "lacks factor key(s): stemming.enabled" "$T/stderr" && ok "a sparse arm YAML is refused (fail closed)" || notok "sparse YAML guard (rc=$RC: $(cat "$T/stderr"))"

# 23: an artifact without running_configuration (pre-#269) cannot prove an arm and is refused
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
cat > "$FM_OUT/benchmarking-fm-00-20260903_000007.json" <<'EOF'
{"metadata": {"corpus_snapshot_id": "snap-1", "code_version": {"digest": "sha256:code"}},
 "benchmarking_results": [{"configuration_file": "configs/config.yaml", "configuration": {"data_manager": {"chunking": {"strategy": "sentence"}}},
   "config_version": {"digest": "sha256:cfg", "divergence_from_selected_file": null}, "corpus_fingerprint": "sha256:def", "corpus_fingerprint_before": "sha256:def", "corpus_unchanged_at_endpoints": true,
   "total_results": {}, "single_question_results": {"question_1": {"question": "q1", "status": "ok", "faithfulness": 0.5, "time_elapsed": 1}}}]}
EOF
run bash "$HERE/archive_run.sh" 00 4 "$T/arms/00-baseline.yaml"
[ "$RC" = 2 ] && grep -q "no running_configuration" "$T/stderr" && ok "archive refuses an artifact without running_configuration" || notok "archive legacy-artifact guard (rc=$RC: $(cat "$T/stderr"))"

# 26: a same-label YAML that names a different bank is refused by the lock
printf '[{"user_input": "q1 changed", "reference": "a1"}]\n' > "$T/cfg/bank2.json"
mk_arm "$T/arms/00-altbank.yaml" fm-00 sentence true true false true 5 "$T/cfg/bank2.json"
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-altbank.yaml"
[ "$RC" = 2 ] && grep -q "fixed factor bank: locked sha256" "$T/stderr" && ok "a same-label YAML with a different bank is refused by the lock" || notok "lock bank guard (rc=$RC: $(cat "$T/stderr"))"

# 27: qa_arm refuses a dataset whose content differs from the lock, even though the file exists
printf 'sha256:abc\n' > "$T/fp"; printf 'sha256:abc\n' > "$FM_OUT/corpus-pin-fm-00"
printf '{"format":"qa-dataset-v2","items":[{"id":"x"}]}\n' > "$T/cfg/other-dataset.json"
run env FM_AGENT_SPEC="$T/cfg/spec.md" bash "$HERE/qa_arm.sh" 01 "$T/arms/01-rerank-off.yaml" --stack fm-00 --profile "$T/cfg/qa/profile.yaml" --dataset "$T/cfg/other-dataset.json" --run 3
[ "$RC" = 2 ] && grep -q "does not match the campaign lock" "$T/stderr" && [ ! -e "$FM_OUT/qa/fm-00-arm01-r3" ] && ok "qa_arm refuses a dataset that differs from the lock" || notok "lock dataset guard (rc=$RC: $(cat "$T/stderr"))"

# 28: --new-corpus is never valid for a non-baseline arm
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000008.json" '[]' ghi 3
run bash "$HERE/archive_run.sh" 05a 2 "$T/arms/05a-k3.yaml" --stack fm-00 --new-corpus
[ "$RC" = 2 ] && grep -q "only valid for arm 00" "$T/stderr" && ok "--new-corpus is refused for a non-baseline arm" || notok "new-corpus arm guard (rc=$RC: $(cat "$T/stderr"))"

# 29: a run whose corpus changed between its endpoints is void, pin or no pin
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000009.json" '[]' def 5 abc
run bash "$HERE/archive_run.sh" 00 5 "$T/arms/00-baseline.yaml"
[ "$RC" = 2 ] && grep -q "corpus changed during the run" "$T/stderr" && ok "archive refuses an artifact whose corpus changed between its endpoints" || notok "endpoint fingerprint gate (rc=$RC: $(cat "$T/stderr"))"

# 30: the agent class is a locked fixed factor
sed 's/agent_class: FASRCDocsAgent/agent_class: CMSCompOpsAgent/' "$T/arms/00-baseline.yaml" > "$T/arms/00-otheragent.yaml"
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-otheragent.yaml"
[ "$RC" = 2 ] && grep -q "fixed factor sut.agent_class: locked 'FASRCDocsAgent'" "$T/stderr" && ok "a different agent class is refused by the lock" || notok "agent class lock (rc=$RC: $(cat "$T/stderr"))"

# 31/32: the locked code revision is enforced for fresh deploys and QA runs
printf 'deadbeef\n' > "$T/codesha"
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/00-baseline.yaml"
R1=$RC; grep -q "is not the locked campaign code c0ffee00" "$T/stderr" && M1=1 || M1=0
printf 'c0ffee00\n' > "$T/codesha"; printf ' M src/x.py\n' > "$T/dirty"
: > "$T/archi.calls"
run env FM_AGENT_SPEC="$T/cfg/spec.md" bash "$HERE/qa_arm.sh" 01 "$T/arms/01-rerank-off.yaml" --stack fm-00 --profile "$T/cfg/qa/profile.yaml" --run 4
R2=$RC; grep -q "uncommitted source changes" "$T/stderr" && M2=1 || M2=0
: > "$T/dirty"
if [ "$R1" = 2 ] && [ "$M1" = 1 ] && [ "$R2" = 2 ] && [ "$M2" = 1 ] && [ ! -s "$T/archi.calls" ]; then ok "a checkout that moved or is dirty is refused by the code lock"; else notok "code lock (rc1=$R1 m1=$M1 rc2=$R2 m2=$M2: $(cat "$T/stderr"))"; fi

# 33: restoring the baseline with --no-run re-seeds without launching a benchmark
: > "$T/docker.calls"
run bash "$HERE/reseed_arm.sh" 00 "$T/arms/00-baseline.yaml" --stack fm-00 --no-run
if [ "$RC" = 0 ] && grep -q "up --force-recreate config-seed" "$T/docker.calls" && ! grep -q "up --no-deps -d benchmark" "$T/docker.calls" \
   && "$FM_PYTHON" -c "import json,sys; e=json.load(open('$FM_OUT/ledger.json'))[-1]; sys.exit(0 if e['kind']=='reseed' and e['arm']=='00' else 1)"; then ok "reseed --no-run restores the config without starting a run"; else notok "reseed --no-run (rc=$RC: $(cat "$T/docker.calls" "$T/stderr"))"; fi

# 34: the same (arm, stack, run) identity cannot be archived twice
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000010.json" '[]' def 3
run bash "$HERE/archive_run.sh" 05a 1 "$T/arms/05a-k3.yaml" --stack fm-00
[ "$RC" = 2 ] && grep -q "arm 05a run 1 on fm-00 is already archived" "$T/stderr" && ok "archive refuses a duplicate (arm, stack, run) identity" || notok "duplicate identity gate (rc=$RC: $(cat "$T/stderr"))"

# 35: a non-numeric QA run number is refused before anything runs
: > "$T/archi.calls"
run env FM_AGENT_SPEC="$T/cfg/spec.md" bash "$HERE/qa_arm.sh" 01 "$T/arms/01-rerank-off.yaml" --stack fm-00 --profile "$T/cfg/qa/profile.yaml" --run two
[ "$RC" = 2 ] && grep -q "run number must be a positive integer" "$T/stderr" && [ ! -s "$T/archi.calls" ] && ok "qa_arm refuses a non-numeric run number up front" || notok "run number validation (rc=$RC: $(cat "$T/stderr"))"

# 36: a stack with no pin accepts only run 1 first
artifact "$FM_OUT/benchmarking-fm-03-20260903_000011.json" '[]' ggg 5
mk_arm "$T/arms/03-categorization-off.yaml" fm-03 sentence true false false true 5
run bash "$HERE/archive_run.sh" 03 2 "$T/arms/03-categorization-off.yaml"
[ "$RC" = 2 ] && grep -q "archive run 1 first" "$T/stderr" && [ ! -f "$FM_OUT/corpus-pin-fm-03" ] && ok "archive refuses run 2 before run 1 has pinned the stack" || notok "pin-by-run-1 gate (rc=$RC: $(cat "$T/stderr"))"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
