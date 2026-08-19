# gitlab-github-sync

A Python CLI that keeps repositories on **GitLab** and **GitHub** in sync — metadata, code,
issues and merge requests / pull requests — by driving the official vendor CLIs (`glab`, `gh`)
and GitLab's documented [remote mirrors API](https://docs.gitlab.com/api/remote_mirrors/).

📖 **Documentation:** <https://rennerdo30.github.io/gitlab-github-sync>

## Why

Mirroring a repository is easy; keeping *the rest* in sync is not. GitLab can mirror code
natively, but nothing carries the description, the topics, the issues or the merge requests
across. And doing it via raw REST calls means handling two different authentication schemes,
two different pagination models and two different JSON shapes.

This tool avoids all of that by delegating to the CLIs that already solve authentication:
`gh` and `glab`. If you can run `gh repo list`, the tool works — there is no token to
configure, no API client to keep up to date.

## What it does

| Area | Direction | Mechanism |
| --- | --- | --- |
| **Metadata** — description, homepage URL, topics/tags | both ways | `gh repo edit` / `glab repo update` |
| **Code** — branches, tags, commits | both ways | GitLab remote mirrors, configured through `glab api` (push: `projects/:id/remote_mirrors`, pull: `projects/:id/mirror/pull`) |
| **Issues** | both ways | `gh issue create/edit` / `glab issue create/update` |
| **Merge requests ↔ pull requests** | both ways | `gh pr create/edit` / `glab mr create/update` |

Additional behaviour:

- **Auto-discovery (`--sync-all`)** — lists repositories on both sides and pairs them by
  repository name (namespace-independent).
- **Repository creation (`--create-missing`)** — when only one side has a repository, the
  counterpart is created with the description copied over.
- **Blacklist** — `fnmatch` patterns (`group/*-test`) exclude repositories from discovery
  and from the final sync list.
- **State file** — `.sync_state.json` records last-sync timestamps per pair and the
  issue/MR ↔ issue/PR number mappings, so re-runs update instead of duplicating.
- **Dry run (`--dry-run`)** — logs every intended change and performs none.
- **Progress + logging** — a `tqdm` progress bar over repository pairs, with a logging
  handler that routes log records through `tqdm.write()` so the bar is never corrupted.

## Requirements

- Python 3.9 or newer
- [`gh`](https://cli.github.com/manual/installation) — GitHub CLI, authenticated
- [`glab`](https://gitlab.com/gitlab-org/cli#installation) — GitLab CLI, authenticated
- Python packages: `PyYAML`, `tqdm`

Code sync relies on GitLab **remote mirrors**. Push mirrors are available on all GitLab
tiers; **pull mirrors require GitLab Premium or higher**. Without pull mirroring the
GitHub → GitLab code direction will not be configured (the rest still works).

## Install

```bash
git clone https://github.com/rennerdo30/gitlab-github-sync.git
cd gitlab-github-sync

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Authenticate both CLIs once:

```bash
gh auth login
glab auth login
```

## Configure

```bash
cp config.example.yaml config.yaml
```

`config.yaml` is git-ignored. It holds **no credentials** — only what to sync:

```yaml
sync_mode:
  sync_all: true          # discover and pair repositories automatically
  gitlab_owner: my-group  # null -> repositories of the authenticated user
  github_owner: my-org    # null -> repositories of the authenticated user
  create_missing: false   # create the counterpart repository when one side is missing

sync_options:
  metadata: true
  code: true
  issues: true
  mrs: true
  use_native_mirrors: true

blacklist:
  gitlab:
    - "my-group/*-scratch"
  github: []

repositories:             # only used when sync_all is false
  - gitlab: "my-group/project"
    github: "my-org/project"
```

| Key | Default | Meaning |
| --- | --- | --- |
| `state_file` | `.sync_state.json` | Where timestamps and number mappings are stored |
| `work_dir` | `.sync_work` | Scratch directory; created on start-up |
| `sync_mode.sync_all` | `false` | Discover repositories instead of using the explicit list |
| `sync_mode.gitlab_owner` | `null` | GitLab group or user namespace to enumerate |
| `sync_mode.github_owner` | `null` | GitHub user or organization to enumerate |
| `sync_mode.create_missing` | `false` | Create the missing counterpart repository |
| `sync_options.metadata` | `true` | Sync description, homepage, topics |
| `sync_options.code` | `true` | Configure GitLab remote mirrors |
| `sync_options.issues` | `true` | Sync issues |
| `sync_options.mrs` | `true` | Sync merge requests / pull requests |
| `sync_options.use_native_mirrors` | `true` | Required for code sync; when `false`, code sync is skipped |
| `blacklist.gitlab` / `blacklist.github` | empty | `fnmatch` exclusion patterns |
| `repositories` | empty | Explicit `gitlab` / `github` pairs |

Anything under `sync_mode` can be overridden on the command line.

## Usage

```bash
# Sync the pairs from config.yaml
python sync.py

# See what would happen, change nothing
python sync.py --dry-run

# Discover and pair everything for a namespace
python sync.py --sync-all --gitlab-owner my-group --github-owner my-org

# ... and create whatever is missing on the other side
python sync.py --sync-all --gitlab-owner my-group --github-owner my-org --create-missing

# A single pair, ignoring the configured list
python sync.py -r "my-group/project:my-org/project"

# Verbose (DEBUG) logging, alternative config file
python sync.py -v -c config.staging.yaml
```

### `sync.py` options

| Flag | Description |
| --- | --- |
| `-c`, `--config PATH` | Configuration file (default `config.yaml`) |
| `-d`, `--dry-run` | Log intended changes without applying them |
| `-v`, `--verbose` | Enable DEBUG logging |
| `-r`, `--repo GITLAB:GITHUB` | Sync exactly one pair |
| `--sync-all` | Auto-discover and pair repositories |
| `--gitlab-owner NAME` | GitLab group or user namespace |
| `--github-owner NAME` | GitHub user or organization |
| `--create-missing` | Create the counterpart repository when one side is missing |

Exit code is `1` if any repository pair ended in an error, otherwise `0`. Pairs where a
repository does not exist are reported as *skipped*, not as errors.

### Mirrors as a separate step: `setup_mirrors.py`

`sync.py` already configures the mirrors as part of code sync. `setup_mirrors.py` exists to
do only that, and to generate a GitHub Actions workflow as an alternative when GitLab pull
mirroring is unavailable:

```bash
# Mirrors for every pair in config.yaml (both directions)
python setup_mirrors.py

# One direction only
python setup_mirrors.py --direction push     # GitLab -> GitHub
python setup_mirrors.py --direction pull     # GitHub -> GitLab

# A single pair
python setup_mirrors.py --repo "my-group/project:my-org/project"

# Instead of a mirror: write .github/workflows/sync-to-gitlab.yml
python setup_mirrors.py --github-action --repo "my-group/project:my-org/project"
```

The generated workflow pushes all branches and tags to GitLab on every push and expects a
`GITLAB_TOKEN` repository secret with `api` scope. The file is only written locally — commit
and push it yourself.

## Scheduling

The tool does not schedule itself. Code sync is continuous once the mirrors exist; the other
areas need periodic runs:

```cron
0 */6 * * * cd /path/to/gitlab-github-sync && ./venv/bin/python sync.py >> sync.log 2>&1
```

`run.sh` is a thin convenience wrapper that activates `venv/` and runs `python sync.py`.

## Credentials

No credential is ever read from the configuration file. Authentication comes from the
vendor CLIs, and both honour environment variables in non-interactive contexts:

| Variable | Used by | Scope needed |
| --- | --- | --- |
| `GH_TOKEN` (or `GITHUB_TOKEN`) | `gh` | `repo` |
| `GITLAB_TOKEN` | `glab` | `api` |

To build the mirror URL, the tool calls `gh auth token` and embeds the resulting token in
the remote URL handed to the GitLab API — that is how GitLab authenticates against GitHub.
Please read [Security notes](#security-notes) before using this on anything that matters.

## Security notes

- **The mirror URL contains a GitHub token.** It is passed as an argument to `glab api`, so
  it is visible in the process list on a shared machine, and `cli_wrapper.run_command()`
  writes full command lines to the log at DEBUG level. Do not run with `--verbose` while
  redirecting into a log file you intend to keep or share, and treat `sync.log` as a secret.
- GitLab stores the mirror credential; rotating the GitHub token means the mirror has to be
  reconfigured.
- `.sync_state.json` lists the full paths of every repository processed, including private
  ones. It is git-ignored for that reason.

## How it works

```
sync.py            CLI, config loading, discovery, pairing, blacklist, progress bar
 └── sync_engine.py  per-pair orchestration: metadata, code, issues, MRs/PRs
      ├── cli_wrapper.py    thin typed wrappers around `gh` and `glab` subprocesses
      └── state_manager.py  JSON state: timestamps + number mappings
setup_mirrors.py   standalone mirror setup / GitHub Actions workflow generator
```

For each pair, `sync_engine` first verifies both repositories are reachable (`glab repo view`,
`gh repo view`) and skips the pair if not. It then runs the enabled areas in order and
records a timestamp per area.

Code sync resolves the numeric GitLab project ID via `glab api projects/<url-encoded-path>`
and then configures two independent things, because GitLab exposes them separately:

- **Push mirror** (GitLab → GitHub): `POST projects/<id>/remote_mirrors`. Available on all
  tiers. An "already been taken" response is treated as success, so runs are idempotent.
- **Pull mirror** (GitHub → GitLab): `PUT projects/<id>/mirror/pull`. The `remote_mirrors`
  endpoint is push-only, so this is a different API — and it needs GitLab Premium or higher.
  A `403`/`404` there is logged as a warning and the run continues with push-only code sync.

Issues and MRs are matched through the number mappings in the state file: a mapping present
means *update*, absent means *create*. Because GitLab and GitHub number resources
independently, deleting the state file and re-running will create duplicates.

## Limitations

These are real, current limitations — not a roadmap:

- **Comments are not synced.** Only title, body and state of issues and MRs/PRs.
- **Nothing is ever deleted.** Closing is propagated, deletion is not.
- **A new MR/PR needs its branches to exist on the target side already**, which in practice
  means code sync must have run first.
- **Pull mirrors need GitLab Premium.** On lower tiers only GitLab → GitHub code sync works.
- **Metadata sync is last-writer-wins.** GitLab → GitHub runs first, then GitHub → GitLab in
  the same pass; there is no conflict detection.
- **Discovery pairs by name only.** Two repositories with the same name in different
  namespaces resolve to the first match.
- **Labels are passed through unchanged**; a label that does not exist on the target platform
  will make the create call fail.
- **No automated tests** ship with the project.

## Tech stack

Python 3.9+ · [PyYAML](https://pyyaml.org/) · [tqdm](https://tqdm.github.io/) ·
`gh` + `glab` as the API layer · [Astro Starlight](https://starlight.astro.build/) with the
Galaxy theme for the documentation site.

## License

[MIT](LICENSE) © rennerdo30
