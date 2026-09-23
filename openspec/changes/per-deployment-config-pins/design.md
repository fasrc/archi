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

The command-line override is a pair. If the environment sets both keys, the deploy uses
them. If it sets neither, the deploy uses the row. If it sets only one, `ensure_config`
aborts before it provisions and names that key. Rejected option: fill the missing key
from the row, as the single pin did. That mixed pin cannot deploy wrong content, because
the SHA check stops it, but it fails after the clone with a false "re-pointed tag?"
message.
