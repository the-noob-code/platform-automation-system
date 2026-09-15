import re
from typing import Dict, Any

class PIIScrubber:
    def __init__(self):
        # Common PII patterns (e.g., SSN, 16-digit credit cards)
        self.pii_patterns = [
            re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),  # SSN
            re.compile(r'\b(?:\d[ -]*?){13,16}\b') # Credit Card
        ]
        self.sensitive_keywords = ['password', 'pin', 'ssn', 'secret']

    def scrub_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitizes the accessibility tree and spatial map before LLM ingestion."""
        if "accessibility_tree" in state:
            state["accessibility_tree"] = self._scrub_node(state["accessibility_tree"])
        
        if "spatial_map" in state and isinstance(state["spatial_map"], list):
            for element in state["spatial_map"]:
                if isinstance(element, dict):
                    self._scrub_element(element)
                
        return state

    def _scrub_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively scrubs accessibility tree nodes.

        Only redact the actual field values, never the UI labels that tell the agent
        which control to target. Preserve field labels and metadata so the agent can
        identify credentials on different login page designs.
        """
        # Defensive check: if node is None or not a dictionary, return it safely
        if not node or not isinstance(node, dict):
            return node

        if 'value' in node and isinstance(node['value'], str):
            # Mask the value completely if the name implies a sensitive field
            if any(kw in str(node.get('name', '')).lower() for kw in self.sensitive_keywords):
                node['value'] = '[REDACTED_SECRET]'
            else:
                node['value'] = self._mask_text(node['value'])

        # Defensive check: only iterate if 'children' is explicitly a list
        if 'children' in node and isinstance(node['children'], list):
            node['children'] = [self._scrub_node(child) for child in node['children'] if child is not None]

        return node

    def _scrub_element(self, element: Dict[str, Any]):
        """Scrubs flat spatial map elements without breaking form targeting labels."""
        if element.get('tag') == 'input' and element.get('type') == 'password':
            element['name'] = element.get('name', '')

        # Keep the visible label name intact so target discovery can still resolve the
        # correct input/button by accessible label. Only the actual field value should be masked.
        if 'value' in element and isinstance(element['value'], str):
            element['value'] = '[REDACTED_SECRET]' if any(kw in str(element.get('name', '')).lower() for kw in self.sensitive_keywords) else self._mask_text(element['value'])

    def _mask_text(self, text: str) -> str:
        """Replaces regex-matched PII with a mask."""
        for pattern in self.pii_patterns:
            text = pattern.sub('[REDACTED_PII]', text)
        return text