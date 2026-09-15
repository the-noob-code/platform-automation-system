from typing import List
from pydantic import BaseModel, Field

# --- 1. Domain & Navigation Guardrails ---
class DomainPolicy(BaseModel):
    allowed_domains: List[str] = Field(
        ..., 
        description="Explicit list of permitted domains. Navigation outside these is blocked."
    )
    blocked_routes: List[str] = Field(
        default_factory=list,
        description="Specific URL paths to block even within allowed domains (e.g., '/admin')."
    )

# --- 2. Action & Risk Guardrails ---
class ActionPolicy(BaseModel):
    allowed_action_types: List[str] = Field(
        ..., 
        description="Strict allowlist of permitted actions (e.g., 'click', 'type_text')."
    )
    blocked_action_types: List[str] = Field(
        default_factory=list,
        description="Irreversible system actions that are explicitly forbidden."
    )
    requires_approval_keywords: List[str] = Field(
        default_factory=list,
        description="Keywords in the action intent (e.g., 'transfer', 'delete') that pause execution for human review."
    )

# --- 3. Data Privacy & Redaction Guardrails ---
class DataPrivacyPolicy(BaseModel):
    sensitive_input_keys: List[str] = Field(
        default_factory=list, 
        description="Keys (like 'password', 'pin') that the Artifact Compiler must parameterize and never hardcode."
    )
    pii_regex_patterns: List[str] = Field(
        default_factory=list, 
        description="Regex patterns (SSNs, Card Numbers) used by the scrubber to sanitize DOMs and logs."
    )
    scrub_password_fields: bool = Field(
        default=True,
        description="Automatically strips the 'value' attribute from any DOM element where type='password'."
    )

# --- 4. The Global Configuration ---
class GuardrailConfig(BaseModel):
    version: str = "1.0.0"
    domain_policy: DomainPolicy
    action_policy: ActionPolicy
    data_privacy: DataPrivacyPolicy