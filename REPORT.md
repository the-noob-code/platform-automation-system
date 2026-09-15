# Platform Automation System Report

## 1. Architecture
```text
+---------------------------------------------------------+
|                  Legacy Web Application                 |
+---------------------------+-----------------------------+
                            | (Raw HTML & DOM)
+---------------------------v-----------------------------+
|    Perception Engine (DOM Cleaner & Spatial Mapper)     |
+---------------------------+-----------------------------+
                            | (Minified JSON State)
            +---------------+---------------+
            |                               |
+-----------v-----------+       +-----------v-----------+
|    Discovery Mode     |       | Deterministic Replay  |
|   ( llama3.2:3b)      |       |   (Saved JSON File)   |
+-----------+-----------+       +-----------+-----------+
            |                               |
            +---------------+---------------+
                            | (Action Capability Schema)
+---------------------------v-----------------------------+
|              Hybrid Execution Engine (Python)           |
+---------------------------+-----------------------------+
                            |
         +------------------+------------------+
         |                  |                  |
+--------v-------+ +--------v-------+ +--------v----------+
| 1. XPath Route | | 2. Spatial Fall| | 3. HITL Retry     |
| (Playwright)   | | (X/Y Mouse)    | | (Terminal Handoff)|
+----------------+ +----------------+ +-------------------+
```

**Perception Engine:** Cleans non-semantic legacy HTML into a lightweight JSON payload while simultaneously extracting spatial bounding boxes (X, Y, width, height) for visual targeting.

**Hybrid Execution Router:** Attempts standard structural XPaths first, but gracefully intercepts Playwright timeouts to fall back on spatial X/Y coordinate mouse clicks.

**Human-in-the-Loop (HITL) Handoff:** Utilizes an asynchronous retry loop that pauses the execution state during a failure (for example, incorrect credentials). This allows an operator to manually correct the live UI state and resume the automation without crashing the script.

### Architectural trade-offs

Designing a resilient system for legacy UIs using an ultra-lightweight local model required balancing several distinct engineering trade-offs:

  #### Local Llama 3.2 (3B) vs. cloud LLMs

  Trade-off: Utilizing a local 3B model ensures zero-latency inference, complete data privacy, and zero API costs. However, this comes at the cost of reasoning depth. The 3B model struggled to strictly adhere to complex dual-locator JSON schemas and occasionally hallucinated attributes.

  Mitigation: The Python execution engine was hardened to tolerate bad JSON outputs, using regex extraction and manual artifact stubbing to ensure deterministic execution.

  #### Spatial execution vs. XPath execution

  Trade-off: Spatial targeting (X/Y mouse coordinate clicks) is highly resilient to legacy HTML changes and undocumented DOM structures. However, it is highly fragile to visual layout shifts, viewport resizing, or responsive design changes. XPath is immune to visual shifts but brittle against raw DOM changes.

  Mitigation: Implemented a cascading hybrid router. The engine attempts XPath first for structural reliability, and seamlessly falls back to spatial targeting only if the DOM rejects the query.

  #### Deterministic replay vs. agentic execution

  Trade-off: Relying on a saved artifact guarantees repeatable execution, reliability, and speed during replay. However, it sacrifices the autonomous agent's ability to adapt on the fly if the UI drastically changes between runs.
## 2. Artifact schema

The following JSON is a generic illustrative superset of the artifact contract.
It intentionally shows many optional capabilities in one example. A real
artifact includes only the inputs, outputs, steps, locators, checkpoints, and
error conditions required by its capability. For example, a login artifact may
need only `type_text`, `click`, and an authenticated-state checkpoint; it does
not need a dropdown, extraction step, OCR fallback, or risky confirmation
unless that workflow actually uses them.

`steps` is an ordered list, not a checklist of mandatory operations. A
capability may contain one step or many steps. Likewise, `inputs_schema`,
`outputs_schema`, checkpoints, fallbacks, business outcomes, and recoverable
conditions may contain only the entries needed by that capability. Empty
`properties`, `required`, or error lists are valid when no corresponding data
or policy is needed.

```json

{
  "version": "1.0.0",
  "capability_name": "example_capability",
  "description": "Performs an application task and returns an optional result.",

  "inputs_schema": {
    "type": "object",
    "properties": {
      "example_input": {
        "type": "string",
        "description": "A capability-specific value supplied at replay time."
      }
    },
    "required": [
      "example_input"
    ]
  },

  "outputs_schema": {
    "type": "object",
    "properties": {
      "example_output": {
        "type": "string",
        "description": "An optional value extracted or produced by the capability."
      }
    },
    "required": [
      "example_output"
    ]
  },

  "steps": [
    {
      "step_id": "open_target",
      "intent": "Open Target",
      "action_type": "click",
      "target": {
        "primary_locator": {
          "type": "accessibility_role",
          "value": "link",
          "name": "Target",
          "role": "link",
          "tag": "a"
        },
        "fallback_locator": {
          "type": "coordinates",
          "value": "viewport",
          "name": "Target",
          "role": "link",
          "tag": "a",
          "x": 820.0,
          "y": 210.0,
          "width": 118.0,
          "height": 24.0
        },
        "robustness_reasoning": "Uses the semantic link role and accessible name first. The captured rectangle is only a last-resort fallback and should be refreshed against the current page before use."
      },
      "value_ref": null,
      "output_ref": null,
      "checkpoint": {
        "type": "element_visible",
        "target": {
          "primary_locator": {
            "type": "accessibility_role",
            "value": "textbox",
            "name": "Example field",
            "role": "textbox",
            "tag": "input"
          },
          "fallback_locator": null,
          "robustness_reasoning": "Confirms that the capability's starting form or target state is visible."
        },
        "value": null
      },
      "is_risky": false
    },
  ],

  "global_success_condition": {
    "type": "text_exists",
    "target": null,
    "value": "Task completed"
  },

  "error_handling": {
    "expected_business_outcomes": [
      {
        "trigger_condition": {
          "type": "text_exists",
          "target": null,
          "value": "No matching result"
        },
        "result_status": "not_found",
        "message": "The application returned no matching result."
      }
    ],
    "recoverable_conditions": [
      {
        "trigger_condition": "timeout",
        "action": "retry_step",
        "max_retries": 2
      },
      {
        "trigger_condition": "element_not_found",
        "action": "wait",
        "max_retries": 1
      }
    ]
  }
}
```

The artifact is designed as a declarative contract rather than a recording of
one browser session. It separates:

- What the capability does: capability_name and description.
- What data it accepts: inputs_schema.
- What ordered work it performs: steps.
- How it identifies controls: target locators.
- How it verifies progress: checkpoints.
- What it returns: outputs_schema and output_ref.
- What counts as a valid application result: expected_business_outcomes.
- What can be retried: recoverable_conditions.
- When a human must approve or repair execution: is_risky and HITL handling.

The primary locator should be semantic or DOM-based. The fallback should be
meaningfully different. Coordinates can improve recovery, but they must be
refreshed against the current page before use because saved viewport positions
can become stale after scrolling or layout changes.

The dummy artifact intentionally includes more fields than a minimal capability
needs so that it demonstrates the contract's available options, including
output extraction, dropdown selection, checkpoints, business outcomes, retries,
OCR, test IDs, accessibility roles, and coordinate fallbacks. These options are
independent; a real artifact should include only the fields that apply.

P.S: Check the dummy_artifact_schema.txt for in detail explanation of each individual feilds.

3. **Determinism & error handling** —

### Deterministic replay

The replay phase does not ask the local language model to decide what to do
next. Discovery produces a versioned JSON capability artifact, and replay
executes the saved `steps` list in its recorded order. This separates the
model-assisted discovery phase from the repeatable execution phase.

For every replay run, the executor:

1. Loads the selected artifact for the configured target.
2. Validates all required inputs against `inputs_schema` before opening the
  workflow.
3. Resolves references such as `${input.username}` and
  `${input.password}` from the runtime input object.
4. Executes each action in the artifact's fixed sequence.
5. Maps extracted values through `output_ref` into the declared output object.
6. Verifies any step-level checkpoint before moving to the next step.
7. Verifies the global success condition before returning `success`.

This makes replay predictable: the artifact, inputs, action order, locator
strategy, and checkpoint rules are explicit inputs to execution. The model is
not involved in replay decisions, so it cannot introduce a new action or
change the order halfway through a run.

Artifacts are also target-specific. The `APP_TARGET` setting selects the local
or ParaBank URL and credentials, and artifact generation writes the matching
capability name, such as `local_app_login` or `parabank_login`. This prevents a
run against one application from silently overwriting the artifact for another
application.

### Input and action validation

The executor rejects missing required inputs before execution begins and checks
declared string inputs for the correct runtime type. Actions without a target
are treated as execution errors rather than being guessed at. Unbound form
actions are removed during discovery and artifact compilation so an empty
model-generated typing action cannot erase a previously entered value.

### Runtime error detection

The Playwright adapter detects failures at the point where they occur:

- It waits for the primary locator to become visible.
- It tries legacy label resolution for older artifacts.
- It tries an independent fallback locator when the primary locator fails.
- It detects visible application errors after actions such as login.
- It verifies that expected elements, text, URLs, and authenticated landing
  states are actually present.

Invalid credentials are treated as a business-level result instead of being
allowed to continue toward the next page. This prevents the system from
claiming that login succeeded when the target application displayed an error.
Technical failures are converted into human-readable messages, for example:

`The required page control could not be found: Log In.`

This avoids exposing low-level messages such as `list missing` or `href
missing` to the operator.

### Structured error handling

The artifact separates expected application outcomes from technical recovery
conditions. An expected business outcome, such as invalid credentials or no
matching result, returns a defined status and readable message. A technical
condition, such as `element_not_found` or a timeout, is handled as an
execution problem rather than a business result.

The executor catches business outcomes separately from general exceptions. A
business outcome returns immediately with its declared status. Other failures
are passed through the error taxonomy and then escalated to human intervention
when the live page needs repair.

The artifact schema includes retry and wait policies with `max_retries` so
future recovery strategies can be bounded rather than looping forever. The
current runtime uses HITL escalation for unresolved failures, which is safer
than blindly repeating an action against an unknown page state.

### UI drift and locator recovery

The system uses a cascading locator strategy:

1. Try the recorded primary DOM or semantic locator.
2. Try legacy label resolution where supported.
3. Try a distinct fallback locator, such as an accessibility role.
4. If the fallback is spatial, capture the current page layout again and
  re-identify the target using its saved name, role, tag, and approximate
  original position.
5. Use the refreshed rectangle's current coordinates only after verifying that
  a visible element exists at that point.

This is important because saved viewport coordinates become stale after
scrolling, responsive layout changes, banners, or dynamic content. The saved
coordinates are therefore a search hint and emergency fallback, not blindly
trusted positions.

The approach deliberately prefers semantic and DOM-based locators because
they survive visual movement. Spatial targeting exists for legacy or poorly
structured pages where the DOM locator is unavailable, and is refreshed at
execution time to reduce the risk of clicking the wrong control.

### Checkpoint-based failure detection

An action completing does not automatically mean the workflow succeeded. Each
important transition can have a checkpoint such as:

- A login form disappearing and an authenticated landing page appearing.
- A result element becoming visible.
- A URL changing to the expected route.
- Expected confirmation text appearing.

The executor verifies these checkpoints immediately after the relevant action,
then verifies the artifact's global success condition at the end. This catches
false successes, failed navigation, invalid credentials, and pages that remain
in the wrong state.

### Human-in-the-loop recovery

When an unresolved exception occurs, the executor pauses at the failed step
and sends the live browser into HITL mode. The browser receives a red outline
and an intervention panel containing the step and readable failure reason.
The operator can correct the page, such as fixing an invalid member ID, and
click `Resume automation`.

The shared asynchronous resume event then releases the waiting executor. The
failed step is retried in place, preserving the artifact's original order.
After resumption, the intervention UI is removed and normal checkpoint
verification continues.

This design prevents the process from crashing or silently continuing after a
recoverable problem. It also keeps the human's correction outside the saved
artifact, so a one-time repair does not pollute the reusable capability.

### Current limitation

The artifact schema models timeout and element-not-found recovery policies,
but the current runtime does not yet implement an independent bounded retry
counter for every declared recoverable condition. Unresolved failures are
currently escalated to HITL, where the operator can repair the live page and
the executor retries the failed step. 

4. **Heterogeneity & multi-tenant** —

### Heterogeneous surfaces

The execution boundary is represented by the `UIAdapter` protocol. The
deterministic executor calls the adapter through operations such as
`perform_action` and `verify_checkpoint`, rather than embedding browser calls
throughout the workflow engine. This gives the project a place to add other
surface implementations while keeping artifact execution and error routing
consistent.

The current implementation provides `PlaywrightWebAdapter` for legacy and
modern web pages. It supports XPath, accessibility-role locators, test IDs,
legacy table-label resolution, refreshed spatial coordinates, clicks, text
entry, dropdown selection, extraction, and checkpoint verification.

The same contract can support a desktop adapter later. A desktop adapter would
translate the same action types into native-window operations and implement
the same `UIAdapter` methods. The artifact should remain focused on intent,
inputs, outputs, checkpoints, and locator metadata rather than on Playwright
objects. This is the main portability boundary in the design.

There are deliberate differences between surfaces:

- Web pages can use DOM, accessibility, URL, and viewport information.
- Legacy web pages can use label heuristics and spatial recovery when the DOM
  is poorly structured.
- Desktop surfaces would need a separate adapter for window handles,
  accessibility APIs, image matching, or native coordinates.

Only the web adapter is implemented today. Desktop execution is an extension
point, not a completed feature.

### Reuse across institutions and tenants

Reuse is achieved by keeping institution-specific values outside the artifact
steps. Runtime inputs are injected through references such as
`${input.username}` and `${input.password}`, while the artifact stores the
workflow and locator strategies.

The current target configuration separates local and ParaBank URLs and
credentials through `APP_TARGET`. This prevents discovery and replay from
mixing credentials or overwriting the wrong target artifact. The generated
artifact name also follows the selected target, for example
`local_app_login` or `parabank_login`.

For institutions running the same application, the same artifact structure can
be reused with different runtime inputs and target configuration. Where an
institution has small UI differences, discovery can produce a tenant-specific
artifact while preserving the same capability name, input schema, output
schema, and business outcome contract.

This is configuration-level multi-tenancy, not a shared tenant service. The
current code does not provide a database-backed tenant registry, per-tenant
artifact repository, secret vault, or concurrent tenant isolation. 

5. **Escalation & handoff** —

### Detecting a stuck or unsafe state

The system treats an execution as stuck when it cannot complete the recorded
action or cannot prove the expected state transition. Detection points include:

- A primary locator timing out.
- A legacy-label lookup and independent fallback locator both failing.
- A refreshed spatial target not being found or not containing a visible
  element.
- A browser action raising an execution exception.
- A visible application error after login, such as rejected credentials.
- A step checkpoint failing after an action.
- The final global success condition failing.
- A required action having no target element.

The system does not use a vague elapsed-time watchdog to guess that the page is
stuck. It uses concrete action and checkpoint failures, which are easier to
explain to an operator and less likely to interrupt a slow but valid page.

### Human takeover

When an unresolved failure reaches the escalation path, the
`InterventionTracker` records the capability, failed step, and human-readable
reason. With a live browser page connected, it adds a red outline and an
in-page intervention panel. The panel tells the operator which step failed,
why it failed, and provides a `Resume automation` control.

This makes the live Chromium page the handoff surface. The operator can correct
an invalid value, dismiss an unexpected page state, or otherwise repair the
current session without rebuilding the entire workflow.

### Returning control to automation

The browser resume control sets a page-level resume flag. The tracker waits for
that flag without applying a normal Playwright timeout, because waiting for a
human is a valid execution state. Once the operator resumes:

1. The intervention overlay and red outline are removed.
2. The tracker clears its paused state and context.
3. The executor retries the failed step in place.
4. The normal checkpoint and global-success verification continues.

The human correction is applied only to the live session; it is not appended to
the artifact. This preserves the deterministic workflow while allowing a
one-time repair.

The current limitation is that there is no independent operator timeout,
approval audit store, or multi-user lock around the browser. 

6. **Safety** —

### Guardrail model

The project defines a `GuardrailConfig` with three policy groups:

- `DomainPolicy` lists allowed domains and blocked routes.
- `ActionPolicy` allowlists action types, blocks forbidden action types, and
  identifies intent keywords that require approval.
- `DataPrivacyPolicy` identifies sensitive input keys, PII patterns, and
  password-field redaction behavior.

The artifact also has an `is_risky` flag on every action. Before a risky action
is executed, the deterministic executor routes through HITL approval. This is
intended for operations such as transfers, deletion, or other irreversible
actions.

Privacy protection is active in discovery: the scrubber masks sensitive values
and PII patterns while preserving labels and metadata needed to identify form
controls. Credentials are parameterized as input references rather than stored
as literal values in the artifact.

The system also validates targets and verifies checkpoints before treating a
workflow as successful. These controls reduce the risk of acting on the wrong
page or silently accepting a failed business operation.

### Safety limits

The guardrail data model is broader than the enforcement currently wired into
the runtime. In particular:

- Domain allowlists and blocked routes are defined but are not yet enforced by
  every navigation path.
- The action allowlist and blocked-action list are defined but the executor
  primarily relies on the artifact action type and `is_risky` approval path.
- Approval keywords are modeled but are not independently scanned in the
  current replay loop.
- Coordinate fallback can still be unsafe if target matching is ambiguous;
  refreshing coordinates reduces risk but does not make visual targeting
  equivalent to semantic targeting.
- A human can approve or repair a state incorrectly. HITL is a control point,
  not proof that the human decision is safe.

7. **Cuts** —

The implementation intentionally cuts several features to keep the prototype
small, local, and explainable:

- No cloud LLM or remote inference service; discovery uses the local Ollama
  model.
- No full computer-vision pipeline; screenshots are captured for evidence,
  while current recovery uses DOM metadata and refreshed bounding boxes.
- No desktop adapter yet; the abstraction exists, but only Playwright web
  execution is implemented.
- No broad autonomous replay replanning; replay follows the saved artifact and
  escalates when the recorded contract cannot be satisfied.
- No database-backed artifact registry or tenant management service.
- No automatic bounded retry counter fully implemented for every declared
  recoverable condition; unresolved failures currently go to HITL.
- No complete security policy enforcement layer for every navigation and action
  path.
- No persistent human-session audit trail or concurrent operator coordination.

