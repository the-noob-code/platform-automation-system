import re


def humanize_error(error: object) -> str:
    """Turns implementation details into actionable operator-facing text."""
    message = str(error).strip()
    normalized = message.lower()

    if "could not locate element" in normalized:
        element = message.split(":", 1)[-1].strip()
        return f"The required page control could not be found: {element}. Check that the page is open and try again."
    if "missing target element" in normalized:
        return "This action is missing its target control. The saved workflow needs to be reviewed."
    if "missing required inputs" in normalized:
        fields = message.split(":", 1)[-1].strip()
        return f"Required information is missing: {fields}. Provide it and try again."
    if "inputs must be strings" in normalized:
        fields = message.split(":", 1)[-1].strip()
        return f"These input values must be text: {fields}. Check the supplied values and try again."
    if "invalid member" in normalized or "invalid credentials" in normalized:
        return "The login credentials were rejected. Enter valid credentials and try again."
    if "timeout" in normalized:
        return "The page took too long to respond. Check the connection and try again."
    if "list missing" in normalized or "missing href" in normalized:
        return "The page did not provide the expected navigation link. Check the current page and try again."
    if "checkpoint failed" in normalized:
        return "The page did not reach the expected state after the action. Check the page and try again."
    if "action execution failed" in normalized:
        detail = message.split(":", 1)[-1].strip()
        return f"The browser could not complete this action: {detail}."

    cleaned = re.sub(r"\b(Recoverable|HardFailure|CheckpointVerification)\w*:?\s*", "", message)
    return cleaned or "The automation reached an unexpected page state."


class AutomationError(Exception):
    """Base class for all automation execution exceptions."""
    pass

class BusinessOutcomeReached(AutomationError):
    """
    Raised when a legitimate business condition is encountered.
    Example: 'No such member' or 'Insufficient Funds'.
    This is a successful execution of a business rule, not a crash.
    """
    def __init__(self, status: str, message: str):
        self.status = status
        self.message = message
        super().__init__(f"Business Outcome [{status}]: {message}")

class RecoverableConditionError(AutomationError):
    """
    Raised for transient runtime issues like network timeouts or delayed DOM renders.
    Triggers the executor's retry loop.
    """
    def __init__(self, condition_type: str, message: str):
        self.condition_type = condition_type
        super().__init__(f"Recoverable [{condition_type}]: {message}")

class HardFailureError(AutomationError):
    """
    Raised for unexpected states, missing critical elements, or unhandled dialogs.
    Triggers immediate Human-in-the-Loop (HITL) escalation.
    """
    pass

class CheckpointVerificationError(HardFailureError):
    """Raised when a post-action validation flag evaluates to false."""
    pass