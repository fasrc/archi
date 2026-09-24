## 1. Tests first

- [x] 1.1 Add `test_ensure_config.sh` case 14: a copied `lib.sh` with a changed `claw` row
      resolves the new pin for `claw` and the old pin for `dev`
- [x] 1.2 Add case 15: an unknown deployment with no environment pin sources cleanly, and
      `ensure_config` aborts naming it, with no `config/` created
- [x] 1.3 Add case 16: an environment pin works for a deployment with no row
- [x] 1.4 Run the test and watch cases 14 and 15 fail
- [x] 1.5 Add cases 17-18: a one-key environment pin aborts before provisioning (red,
      then green)

## 2. Implementation

- [x] 2.1 Replace the single pin in `lib.sh` with the per-deployment table (both rows at
      `deploy-pin-2026-09e`, the pin PR #541 set)
- [x] 2.2 Abort at the top of `ensure_config` when the pin is empty
- [x] 2.3 Update the bump procedure comment in `lib.sh`

## 3. Docs

- [x] 3.1 Update `host.env.example`, `deploy/scripts/README.md`, `docs/docs/fasrc_archi.md`,
      and `docs/docs/glossary.md`

## 4. Verify

- [x] 4.1 `bash deploy/scripts/test_ensure_config.sh` and `bash deploy/scripts/test_host_env.sh` pass
- [x] 4.2 `bash scripts/gate.sh` green
