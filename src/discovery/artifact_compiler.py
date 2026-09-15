import json
import re
from typing import List, Dict, Any
from models.artifact import CapabilityArtifact, Action, Checkpoint, ErrorTaxonomy

class ArtifactCompiler:
    def __init__(self, capability_name: str, description: str):
        self.capability_name = capability_name
        self.description = description

    def compile_artifact(
        self, 
        action_history: List[Action], 
        global_success: Checkpoint,
        error_taxonomy: ErrorTaxonomy
    ) -> CapabilityArtifact:
        """Synthesizes the LLM's discovery run into a deterministic capability contract."""
        
        executable_actions = [
            action for action in action_history
            if not (
                action.action_type in ("type_text", "select_dropdown")
                and not action.value_ref
            )
        ]

        inputs_schema = self._infer_schema_from_refs(executable_actions, "value_ref")
        outputs_schema = self._infer_schema_from_refs(executable_actions, "output_ref")

        unique_actions = []
        seen_actions = {}
        for action in executable_actions:
            action_key = self._action_key(action)
            existing_index = seen_actions.get(action_key)
            if existing_index is None:
                seen_actions[action_key] = len(unique_actions)
                unique_actions.append(action)
            elif self._locator_quality(action) > self._locator_quality(unique_actions[existing_index]):
                unique_actions[existing_index] = action

        artifact = CapabilityArtifact(
            capability_name=self.capability_name,
            description=self.description,
            inputs_schema=inputs_schema,
            outputs_schema=outputs_schema,
            steps=unique_actions,
            global_success_condition=global_success,
            error_handling=error_taxonomy
        )
        
        self._save_to_disk(artifact)
        return artifact

    def _infer_schema_from_refs(self, actions: List[Action], ref_key: str) -> Dict[str, Any]:
        """Dynamically builds JSON schemas based on parameterized variables (e.g., input.amount)."""
        namespace = "input" if ref_key == "value_ref" else "output"
        reference_pattern = re.compile(rf"\$\{{{namespace}\.([^}}]+)\}}")
        properties = {}
        for action in actions:
            ref_val = getattr(action, ref_key, None)
            match = reference_pattern.fullmatch(ref_val or "")
            if match:
                var_name = match.group(1)
                properties[var_name] = {"type": "string"} # Defaulting to string for basic synthesis
                
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties.keys())
        }

    @staticmethod
    def _action_key(action: Action):
        if action.action_type == "type_text" and action.value_ref:
            return (action.action_type, action.value_ref)
        return (
            action.action_type,
            action.target.primary_locator.value if action.target else None,
            action.value_ref,
        )

    @staticmethod
    def _locator_quality(action: Action) -> int:
        if not action.target:
            return 0
        value = action.target.primary_locator.value
        score = 1 if action.target.fallback_locator else 0
        score += 2 if "placeholder=" in value else 0
        score += 1 if "@name=" in value else 0
        return score

    def _save_to_disk(self, artifact: CapabilityArtifact):
        with open(f"artifacts/{artifact.capability_name}.json", "w") as f:
            f.write(artifact.model_dump_json(indent=2))