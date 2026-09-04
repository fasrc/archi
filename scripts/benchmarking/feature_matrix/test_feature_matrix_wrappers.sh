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
#   13. archive_run.sh refuses a later run whose fingerprint differs from the pin unless
#       --new-corpus, which then rewrites the pin
#   14. qa_arm.sh refuses when the stack's rendered config is not on the requested arm
#   15. qa_arm.sh records the rendered config's sha256 and the corpus fingerprint
#   16. archive_run.sh refuses an artifact that is already in the ledger
#   17. archive_run.sh refuses an artifact older than the stack's latest ragas-start
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
printf 'abc\n' > "$T/fp"

# --- stubs -----------------------------------------------------------------------------
cat > "$T/bin/docker" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$T/docker.calls"
case "\$1" in
  inspect) [ -f "$T/state/\$2" ] && { cat "$T/state/\$2"; exit 0; } || exit 1 ;;
  exec)    sql="\$*"
           case "\$sql" in
             *md5\(*)            cat "$T/fp" ;;
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
chmod +x "$T/bin/docker" "$T/bin/archi"

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
mkdir -p "$T/arms" "$T/cfg/qa"
cat > "$T/arms/01-rerank-off.yaml" <<'EOF'
name: fm-01
data_manager:
  chunking: {strategy: sentence}
  processing: {html_to_markdown: {enabled: true}, categorization: {enabled: true}}
  stemming: {enabled: false}
  retrievers: {hierarchical_rerank: {enabled: false, candidate_pool_size: 20, num_documents_to_retrieve: 5}}
EOF
cat > "$T/arms/02-chunking-character.yaml" <<'EOF'
name: fm-02
data_manager:
  chunking: {strategy: character}
  processing: {html_to_markdown: {enabled: true}, categorization: {enabled: true}}
  stemming: {enabled: false}
  retrievers: {hierarchical_rerank: {enabled: false, candidate_pool_size: 20, num_documents_to_retrieve: 5}}
EOF
printf 'HUIT_API_KEY=x\n' > "$T/judge.env"
printf 'version: 1\n' > "$T/cfg/qa/profile.yaml"; printf -- '---\nname: x\ntools: []\n---\n' > "$T/cfg/spec.md"

artifact() { # $1 = path, $2 = divergence JSON, $3 = fingerprint
  cat > "$1" <<EOF
{"metadata": {"corpus_snapshot_id": "snap-1", "code_version": {"digest": "sha256:code"}},
 "benchmarking_results": [{
   "configuration_file": "configs/config.yaml",
   "config_version": {"digest": "sha256:cfg", "divergence_from_selected_file": $2},
   "corpus_fingerprint": "$3", "ingest_wall_seconds": 4321.0,
   "total_results": {"aggregate_context_precision": 0.5, "context_precision_scored": "3 of 3", "aggregate_faithfulness": 0.6},
   "single_question_results": {
     "question_1": {"question": "q1", "status": "ok", "context_precision": 0.4, "faithfulness": 0.6, "time_elapsed": 10},
     "question_2": {"question": "q2", "status": "ok", "context_precision": NaN, "faithfulness": 0.7, "time_elapsed": 12},
     "question_3": {"question": "q3", "status": "degraded", "context_precision": 0.9, "faithfulness": 0.5, "time_elapsed": 90}}}]}
EOF
}

run() { set +e; "$@" >"$T/stdout" 2>"$T/stderr"; RC=$?; set -e; }

# 1
run bash "$HERE/run_arm.sh" "0x" "$T/arms/01-rerank-off.yaml"
[ "$RC" = 2 ] && grep -q "bad arm label" "$T/stderr" && ok "bad arm label refused" || notok "bad arm label refused (rc=$RC)"

# 2
: > "$T/archi.calls"
run env RAGAS_ENV_FILE="$T/judge.env" bash "$HERE/run_arm.sh" 00 "$T/arms/01-rerank-off.yaml"
if [ "$RC" = 0 ] && grep -qx "evaluate --name fm-00 --config $T/arms/01-rerank-off.yaml --env-file $T/judge.env --hostmode" "$T/archi.calls"; then ok "run_arm calls archi evaluate --hostmode"; else notok "run_arm calls archi evaluate --hostmode (rc=$RC: $(cat "$T/archi.calls" "$T/stderr"))"; fi

# 3
run bash "$HERE/run_arm.sh" 00 "$T/arms/01-rerank-off.yaml"
[ "$RC" = 2 ] && grep -q "RAGAS_ENV_FILE" "$T/stderr" && ok "run_arm refuses without the judge env file" || notok "run_arm refuses without the judge env file (rc=$RC)"

# 4
run bash "$HERE/run_arm.sh" 00 --rerun
[ "$RC" = 2 ] && grep -q "no corpus pin" "$T/stderr" && ok "rerun refuses without a pin" || notok "rerun refuses without a pin (rc=$RC: $(cat "$T/stderr"))"

# 5  (the ledger already holds the ragas-start rows from checks 2 and 4; a refusal must add nothing)
ledger_rows() { "$FM_PYTHON" -c "import json,sys; print(len(json.load(open(sys.argv[1]))) if __import__('os').path.exists(sys.argv[1]) else 0)" "$FM_OUT/ledger.json"; }
artifact "$FM_OUT/benchmarking-fm-00-20260903_000001.json" '["data_manager.retrievers.hierarchical_rerank.enabled"]' abc
BEFORE="$(ledger_rows)"
run bash "$HERE/archive_run.sh" 00 1
if [ "$RC" = 2 ] && grep -q "divergence_from_selected_file" "$T/stderr" && [ "$(ledger_rows)" = "$BEFORE" ] && [ ! -f "$FM_OUT/corpus-pin-fm-00" ]; then ok "archive refuses a diverged run, writes nothing"; else notok "archive refuses a diverged run (rc=$RC: $(cat "$T/stderr"))"; fi
rm -f "$FM_OUT"/benchmarking-fm-00-*.json

# 6
artifact "$FM_OUT/benchmarking-fm-00-20260903_000002.json" '[]' abc
run bash "$HERE/archive_run.sh" 00 1
if [ "$RC" = 0 ] && [ "$(cat "$FM_OUT/corpus-pin-fm-00")" = abc ] && "$FM_PYTHON" - "$FM_OUT/ledger.json" <<'EOF'
import json, sys
rows = json.load(open(sys.argv[1])); e = rows[-1]
assert e["kind"] == "ragas" and e["run"] == 1 and e["arm"] == "00", e
assert e["corpus_fingerprint"] == "abc" and e["documents"] == 1132 and e["chunks"] == 6096, e
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
printf 'zzz\n' > "$T/fp"
run bash "$HERE/run_arm.sh" 00 --rerun
[ "$RC" = 2 ] && grep -q "fingerprint zzz != pin abc" "$T/stderr" && ok "rerun refuses a drifted corpus" || notok "rerun refuses a drifted corpus (rc=$RC: $(cat "$T/stderr"))"
printf 'abc\n' > "$T/fp"

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
if "$FM_PYTHON" -c "import json,sys; e=json.load(open('$FM_OUT/ledger.json'))[-1]; sys.exit(0 if e.get('rendered_config_sha256')=='$EXPECT_SHA' and e.get('corpus_fingerprint')=='abc' and e.get('arm_config')=='$T/arms/01-rerank-off.yaml' else 1)"; then ok "qa_arm records the rendered config sha256, the arm config, and the corpus fingerprint"; else notok "qa_arm ledger identity fields"; fi

# 13
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000003.json" '[]' def
run bash "$HERE/archive_run.sh" 00 2
R1=$RC
run bash "$HERE/archive_run.sh" 00 2 --new-corpus
if [ "$R1" = 2 ] && [ "$RC" = 0 ] && [ "$(cat "$FM_OUT/corpus-pin-fm-00")" = def ]; then ok "archive refuses a drifted fingerprint unless --new-corpus, which re-pins"; else notok "archive fingerprint gate (rc1=$R1 rc2=$RC: $(cat "$T/stderr"))"; fi

# 16: the same artifact again → refused, ledger unchanged
BEFORE="$(ledger_rows)"
run bash "$HERE/archive_run.sh" 00 3
if [ "$RC" = 2 ] && grep -q "already archived as arm 00 run 2" "$T/stderr" && [ "$(ledger_rows)" = "$BEFORE" ]; then ok "archive refuses an artifact already in the ledger"; else notok "archive duplicate guard (rc=$RC: $(cat "$T/stderr"))"; fi

# 17: a re-run that produced nothing leaves run 2's file as the newest; the file predates
# the new ragas-start, so archiving "run 3" must refuse
rm -f "$FM_OUT"/benchmarking-fm-00-*.json
artifact "$FM_OUT/benchmarking-fm-00-20260903_000004.json" '[]' def
touch -d '2020-01-01T00:00:00Z' "$FM_OUT/benchmarking-fm-00-20260903_000004.json"
run bash "$HERE/run_arm.sh" 00 --rerun          # appends a fresh ragas-start for fm-00
run bash "$HERE/archive_run.sh" 00 3
if [ "$RC" = 2 ] && grep -q "before the latest ragas-start" "$T/stderr"; then ok "archive refuses an artifact older than the latest ragas-start"; else notok "archive stale-artifact guard (rc=$RC: $(cat "$T/stderr"))"; fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
