# Platform Automation System

## Overview

This project discovers a browser workflow, compiles it into a JSON capability
artifact, and replays the workflow deterministically with Playwright.

The target application is selected through `APP_TARGET` in `.env`:

- `parabank`: uses the online ParaBank application.
- `local`: uses the local FastAPI target application.

## Prerequisites

- Python 3.12 or compatible Python version
- `uv`
- Ollama with the llama3.2:3b model available
- Playwright browser dependencies

Install the project dependencies from the repository root:

```bash
uv sync
uv run playwright install chromium
```

Activate the virtual environment if you prefer running commands directly:

```bash
source .venv/bin/activate
```

## Environment Configuration

The project reads values from the root `.env` file. The application loads this
file with override enabled, so the values in `.env` take precedence over stale
exported shell variables.

The `.env` file is intentionally excluded from the repository because it may
contain environment-specific or sensitive values. After cloning the project,
create it from the committed template:

```bash
cp .env.example .env
```

Then edit `.env` and choose the target you want to run. The application will
not work correctly until this file has been created and configured.

The main switch is:

```env
APP_TARGET="parabank"
```

or:

```env
APP_TARGET="local"
```

Each target has separate URL and discovery/replay credentials:

```env
LOCAL_TARGET_URL="http://127.0.0.1:9000/login"
LOCAL_DISCOVERY_USERNAME="1001"
LOCAL_DISCOVERY_PASSWORD="4471"
LOCAL_REPLAY_USERNAME="1002"
LOCAL_REPLAY_PASSWORD="8823"

PARABANK_TARGET_URL="http://parabank.parasoft.com/parabank/index.htm"
PARABANK_DISCOVERY_USERNAME="john"
PARABANK_DISCOVERY_PASSWORD="demo"
PARABANK_REPLAY_USERNAME="mary"
PARABANK_REPLAY_PASSWORD="jane"
```

Do not commit real credentials to the repository. The values above are the
current test values for this project.

## Run With ParaBank

ParaBank is the online target used to exercise the human-in-the-loop error
flow.

1. Set the target in `.env`:

	```env
	APP_TARGET="parabank"
	```

2. Run the automation from the repository root:

	```bash
	uv run src/main.py
	```

3. Discovery uses the configured ParaBank discovery credentials:

	```text
	username: john
	password: demo
	```

4. The replay phase intentionally uses the configured replay credentials. With
	the current values (`mary` / `jane`), ParaBank is expected to reject the
	login and trigger HITL.

5. When the browser displays the red HITL panel, use the live browser to
	correct the login values to:

	```text
	username: john
	password: demo
	```

6. Click **Resume automation** in the browser. The failed step is retried and
	the execution continues.

Evidence from the run is written to:

- `evidence/discovery_run.log`
- `evidence/replay_run.log`
- `evidence/discovery_step_*.jpg`
- `artifacts/parabank_login.json`

## Run With the Local Target

The local target application is not included yet. It will be added later under
the repository root:

```text
target_app/
```

The target app is expected to be a FastAPI Python application and should listen
on port `9000` so that the configured URL is available at:

```text
http://127.0.0.1:9000/login
```

When the target app has been added:

1. Open a terminal at the repository root.
2. Start the FastAPI target app on port `9000`. The exact command depends on
	the entrypoint added under `target_app/`. A typical command will look like:

	```bash
	uv run uvicorn target_app.main:app --host 127.0.0.1 --port 9000
	```

3. Confirm that the local login page is available at:

	```text
	http://127.0.0.1:9000/login
	```

4. In a second terminal, set the automation target in `.env`:

	```env
	APP_TARGET="local"
	```

5. Run the automation:

	```bash
	uv run src/main.py
	```

For a successful local run, the target app should accept the configured local
discovery and replay credentials:

```text
discovery username: 1001
discovery password: 4471
replay username: 1002
replay password: 8823
```

Evidence and the generated artifact are written to:

- `evidence/discovery_run.log`
- `evidence/replay_run.log`
- `evidence/discovery_step_*.jpg`
- `artifacts/local_app_login.json`

## Expected Test Scenarios

### Successful local replay

Use `APP_TARGET="local"`, start the local FastAPI target on port `9000`, and
run `uv run src/main.py`. Discovery and replay should complete successfully.

### ParaBank HITL replay

Use `APP_TARGET="parabank"`. Discovery uses `john/demo`, while replay uses the
configured failing credentials. ParaBank should reject the replay login,
display the HITL intervention state, and pause until the operator corrects the
live browser values to `john/demo` and clicks **Resume automation**.

## Generated Files

The main generated files are:

| Path | Purpose |
| --- | --- |
| `artifacts/local_app_login.json` | Local target capability artifact |
| `artifacts/parabank_login.json` | ParaBank capability artifact |
| `evidence/discovery_run.log` | Discovery action log |
| `evidence/replay_run.log` | Replay result log |
| `evidence/discovery_step_*.jpg` | Screenshots captured during discovery |

The artifact name is selected from `APP_TARGET`, so a local run and a ParaBank
run do not overwrite each other's capability artifact.

## Tests

Run the focused tests with the package roots configured:

```bash
PYTHONPATH=.:src uv run pytest tests/test_main_locator_fallbacks.py -q
```

Compile the main Python modules:

```bash
PYTHONPATH=.:src uv run python -m py_compile src/main.py src/discovery/agent.py src/executor/adapter.py
```
