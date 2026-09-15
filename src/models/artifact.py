from typing import List, Dict, Any, Literal, Optional
from pydantic import BaseModel, Field

# --- 1. Locators & Targeting ---
class Locator(BaseModel):
    type: Literal["accessibility_role", "ocr_text", "xpath", "test_id", "coordinates"]
    value: str
    name: Optional[str] = None
    role: Optional[str] = None
    tag: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    
class TargetElement(BaseModel):
    primary_locator: Locator
    fallback_locator: Optional[Locator] = None
    # Satisfies: "how each target... is identified (with your reasoning about robustness)"
    robustness_reasoning: str = Field(
        ..., 
        description="Explanation of why these locators are resilient to UI drift."
    )

# --- 2. Checkpoints & Validation ---
class Checkpoint(BaseModel):
    type: Literal["element_visible", "text_exists", "url_match"]
    target: Optional[TargetElement] = None
    value: Optional[str] = None

# --- 3. Actions (The Ordered Flow) ---
class Action(BaseModel):
    step_id: str
    intent: str
    action_type: Literal[
        "click", 
        "type_text", 
        "select_dropdown", 
        "extract_data",  # Added to explicitly satisfy data extraction
        "navigate"
    ]
    target: Optional[TargetElement] = None
    
    # Input mapping: Reference to input variable, e.g., "${input.account_id}"
    value_ref: Optional[str] = None 
    
    # Output mapping: Binds extracted text to the output schema, e.g., "${output.transaction_id}"
    output_ref: Optional[str] = None 
    
    checkpoint: Optional[Checkpoint] = Field(
        None, 
        description="Verification condition after action execution"
    )
    
    # Satisfies: Distinguish "safe/reversible" actions from risky/irreversible ones
    is_risky: bool = Field(
        default=False, 
        description="Flags if this step requires human approval before execution"
    )

# --- 4. Error Taxonomy ---
# Satisfies: Distinguish between expected business outcomes and recoverable conditions
class BusinessOutcome(BaseModel):
    trigger_condition: Checkpoint
    result_status: str
    message: str

class RecoverableCondition(BaseModel):
    trigger_condition: Literal["timeout", "element_not_found"]
    action: Literal["retry_step", "wait"]
    max_retries: int

class ErrorTaxonomy(BaseModel):
    expected_business_outcomes: List[BusinessOutcome]
    recoverable_conditions: List[RecoverableCondition]

# --- 5. The Root Capability Contract ---
class CapabilityArtifact(BaseModel):
    version: str = "1.0.0"
    capability_name: str
    description: str
    
    # Satisfies: "typed input parameters" and "typed outputs"
    inputs_schema: Dict[str, Any] = Field(..., description="JSON Schema for inputs")
    outputs_schema: Dict[str, Any] = Field(..., description="JSON Schema for outputs")
    
    # Satisfies: "ordered steps / actions"
    steps: List[Action] = Field(..., description="Ordered steps to execute the capability")
    
    # Satisfies: "a checkpoint or success condition"
    global_success_condition: Checkpoint
    error_handling: ErrorTaxonomy