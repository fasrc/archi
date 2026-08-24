# Install

## System Requirements

Archi is deployed using a Python-based CLI onto containers. It requires:

- `docker` version 24+ or `podman` version 5.4.0+ (for containers)
- `python 3.11.0+` (for the CLI)

> **Note:** We support either running open-source models locally or connecting to existing APIs. If you plan to run open-source models on your machine's GPUs, see the [Advanced Setup & Deployment](advanced_setup_deploy.md) section.

## Installation

Clone the Archi repository:

```bash
git clone https://github.com/archi-physics/archi.git
```

Check out the latest stable tag (recommended for users; stay on `main` only if you're actively developing):

```bash
cd archi
git checkout $(git describe --tags $(git rev-list --tags --max-count=1))
```

Install Archi (from inside the repository):

```bash
pip install -e .
```

This installs Archi's dependencies and the CLI tool. Verify the installation with:

```bash
which archi
```

The command prints the path to the `archi` executable.

<details>
<summary>Show Full Installation Script</summary>

```bash
# Clone the repository
git clone https://github.com/archi-physics/archi.git
cd archi
export ARCHI_DIR=$(pwd)

# (Optional) Checkout the latest stable tag (recommended for users)
# Skip this if you're developing and want the tip of main.
git checkout $(git describe --tags $(git rev-list --tags --max-count=1))

# (Optional) Create and activate a virtual environment
python3 -m venv archi_venv
source archi_venv/bin/activate

# Install dependencies
cd "$ARCHI_DIR"
pip install -e .

# Verify installation
which archi
```

</details>

## Container registry access

The service images build on base images this fork publishes to the GitHub Container
Registry (`ghcr.io/fasrc/`). Those packages have **internal** visibility: every member of the
`fasrc` organization may pull them, but an anonymous pull is refused. So a machine needs to
log in once before its first `archi create`.

```bash
echo "$YOUR_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

Use `podman login ghcr.io` instead if you deploy with `--podman`. Podman does not read
Docker's credential store, so logging one in does not authenticate the other.

### The token must be a classic personal access token

Create it at **Settings → Developer settings → Personal access tokens → Tokens (classic)**
and give it the **`read:packages`** scope. This URL pre-selects only that scope:

```
https://github.com/settings/tokens/new?scopes=read:packages
```

Two things commonly go wrong here:

- **A fine-grained token cannot work.** Fine-grained personal access tokens have no Packages
  permission at all, so there is no way to grant them registry access. They fail with the
  same "denied" message a correctly scoped token gives when it is simply unauthorized, which
  makes the cause hard to see.
- **Single sign-on needs separate authorization.** If your organization enforces SSO, open
  the token's **Configure SSO** menu and authorize it for the organization. A correctly
  scoped token that skips this step still fails, and the error does not mention SSO.

Do not add the `repo` scope. The container registry uses granular permissions and does not
need it, and the token creation form will try to select it for you.

`archi create` checks the base images before it changes anything, so a machine that is not
logged in is told so up front — naming the image and the command to run — rather than failing
part way through a build. Under `--force` this check runs before the existing deployment is
removed, so a create that cannot obtain its base images leaves the running deployment alone.

If you already have the base images locally, no login is required: the check accepts an image
that is present on the host without contacting any registry.
