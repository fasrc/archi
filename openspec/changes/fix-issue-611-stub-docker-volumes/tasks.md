## 1. Stub the docker volume step

- [x] 1.1 In `tests/unit/test_cli_create_dev_smoke.py`, in `test_force_create_with_agent_config_inside_deployment_keeps_existing_deployment` (~line 2438), add `monkeypatch.setattr(VolumeManager, "create_required_volumes", lambda self, *a, **kw: None)` next to the `check_docker_available` stub, the same way the sibling tests do (~lines 1031, 1117, 1508; import `VolumeManager` as they do if it is not in scope). Prove it with no docker: make a temp dir with symlinks to only `python`, `python3`, `pytest`, and `git`, then run `PATH=<that dir> python -m pytest tests/unit/test_cli_create_dev_smoke.py -q -p no:cacheprovider` — all tests must pass (before the fix: 1 failed with `No such file or directory: 'docker'`). In the loop sandbox there is no docker, so a plain run is the same proof. Then run `bash scripts/gate.sh` and commit (test-only change; no production code).

## 2. Verify, push, and open the PR

- [x] 2.1 Run `bash scripts/gate.sh` on the branch tip and confirm it is green. Push with `git push -u origin fix/issue-611-stub-docker-volumes`. Open the PR with `gh pr create --repo fasrc/archi --base dev`, with `closes #611` in the PR **body** (not the title). The body states the before and after counts of the no-docker run. No `Co-Authored-By` trailer. Do not merge.
