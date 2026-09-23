# Design: per-deployment config pins

## Where the pins live

The pins stay in the tracked `deploy/scripts/lib.sh`, as a `case "$DEPLOYMENT"` table
directly after the identity block that resolves `$DEPLOYMENT`. Rejected option: a
`CONFIG_REF`/`CONFIG_SHA` line in `host.env`. `host.env` is git-excluded, so a pin there
cannot be reviewed, and `host.env.example:35-37` already refuses it for that reason.

Rejected option: a pin file inside each environment file of fasrc/archi-config. The
config checkout is what the pin selects, so it cannot also hold the pin.

## Unknown deployment names

A name with no row gets empty defaults. The check that aborts is at the top of
`ensure_config`, not at source time: `status.sh` and `nuke.sh` source `lib.sh` and never
provision, and they must still work for any valid name. A shared fallback row is
rejected, because it rebuilds the coupling this change removes.

## Precedence

`CONFIG_REF="${CONFIG_REF:-$row_ref}"` keeps the existing command-line override. Each
key falls back to its row independently, as today. An override of only one key then
fails the existing SHA check, which is the correct result.
