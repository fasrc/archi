Ralph loop initialized; not yet run. This file is the cold-start breadcrumb AND
the loop's stop signal — the runner snapshots it at startup and stops only when a
turn CHANGES it to a new non-empty reason. Leave this line in place until the loop
writes its own.
