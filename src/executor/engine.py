import asyncio
from typing import Dict, Any
from typing import Optional

from models.artifact import CapabilityArtifact, Action
from server.state import InterventionTracker
from executor.exceptions import BusinessOutcomeReached, HardFailureError, humanize_error

class DeterministicExecutor:
    def __init__(self, ui_adapter, hitl_tracker: InterventionTracker):
        self.ui = ui_adapter # Abstracted Playwright adapter
        self.hitl = hitl_tracker

    async def execute(self, artifact: CapabilityArtifact, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Replays the recorded flow deterministically with strict error handling."""
        outputs = {}
        try:
            self._validate_inputs(artifact, inputs)
        except Exception as error:
            return {"status": "invalid_input", "message": humanize_error(error)}

        for step in artifact.steps:
            while True:
                try:
                    # 1. Guardrail Check: HITL Escalation for Risky Actions
                    if step.is_risky:
                        await self.hitl.request_approval(
                            goal=artifact.capability_name,
                            step_id=step.step_id,
                            reason="Action flagged as risky/irreversible."
                        )

                    # 2. Variable Resolution
                    value_to_input = None
                    if step.value_ref:
                        var_name = step.value_ref.removeprefix("${input.").removesuffix("}")
                        value_to_input = inputs.get(var_name)

                    # 3. Execution via Adapter (Handling Primary & Fallback Locators)
                    if not step.target:
                        raise HardFailureError(f"Action {step.action_type} missing target element.")

                    extracted_data = await self.ui.perform_action(
                        action_type=step.action_type,
                        primary=step.target.primary_locator,
                        fallback=step.target.fallback_locator,
                        value=value_to_input
                    )

                    # 4. Output Mapping
                    if step.output_ref and extracted_data:
                        out_var = step.output_ref.split('.')[1]
                        outputs[out_var] = extracted_data

                    # 5. Checkpoint Verification
                    if step.checkpoint:
                        await self.ui.verify_checkpoint(step.checkpoint)
                    break

                except BusinessOutcomeReached as e:
                    return {
                        "status": e.status,
                        "message": humanize_error(e.message),
                    }
                except Exception as e:
                    result = await self._handle_execution_error(e, artifact, step)
                    if result is not None:
                        return result
                    # The operator repaired the live page; retry this step in-place.

        # Final Global Checkpoint
        while True:
            try:
                await self.ui.verify_checkpoint(artifact.global_success_condition)
                break
            except BusinessOutcomeReached as e:
                return {
                    "status": e.status,
                    "message": humanize_error(e.message),
                }
            except Exception as error:
                result = await self._handle_execution_error(
                    error,
                    artifact,
                    None,
                )
                if result is not None:
                    return result

        return {"status": "success", "data": outputs}

    @staticmethod
    def _validate_inputs(artifact: CapabilityArtifact, inputs: Dict[str, Any]) -> None:
        schema = artifact.inputs_schema or {}
        required = schema.get("required", [])
        missing = [name for name in required if name not in inputs]
        if missing:
            raise HardFailureError(f"Missing required inputs: {', '.join(missing)}")

        properties = schema.get("properties", {})
        invalid_types = [
            name for name, definition in properties.items()
            if name in inputs and definition.get("type") == "string" and not isinstance(inputs[name], str)
        ]
        if invalid_types:
            raise HardFailureError(f"Inputs must be strings: {', '.join(invalid_types)}")

    async def _handle_execution_error(
        self,
        error: Exception,
        artifact: CapabilityArtifact,
        step: Optional[Action],
    ):
        """Routes failures based on the predefined Error Taxonomy."""
        error_msg = humanize_error(error)

        # Check Business Outcomes (e.g., "Insufficient Funds")
        for outcome in artifact.error_handling.expected_business_outcomes:
            if outcome.trigger_condition.value in error_msg:
                return {"status": outcome.result_status, "message": outcome.message}

        # Check Recoverable Conditions (e.g., timeouts)
        for recoverable in artifact.error_handling.recoverable_conditions:
            if recoverable.trigger_condition == "timeout" and "TimeoutError" in error_msg:
                # In production, implement the retry logic loop here
                pass 

        # Hard Failure: Trigger Operator Handoff
        await self.hitl.escalate_to_human(
            goal=artifact.capability_name,
            step_id=step.step_id if step else "final verification",
            error_details=error_msg
        )
        # A completed handoff means the operator repaired the live page. The
        # caller retries the failed step with that repaired browser state.
        return None