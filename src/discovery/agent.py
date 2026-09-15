import json
import logging
from typing import List, Dict, Any, Optional
import ollama
from pydantic import ValidationError
from models.artifact import Action

logger = logging.getLogger(__name__)

class DiscoveryAgent:
    def __init__(self, model_name: str = "llama3.2:3b"):
        self.model_name = model_name
        self.client = ollama.Client()
        self.action_history: List[Action] = []
        
    def decide_next_action(self, goal: str, safe_state: Dict[str, Any]) -> Optional[Action]:
        if any(action.step_id == "click_login" for action in self.action_history):
            # The capability ends at the authenticated landing page. Do not
            # mistake its search/filter form for another login workflow step.
            return None

        fallback_form_action = self._next_unrecorded_form_action(safe_state)
        if fallback_form_action is not None and not self.action_history:
            self.action_history.append(fallback_form_action)
            return fallback_form_action

        submit_action = self._submit_after_credentials(safe_state)
        if submit_action is not None:
            self.action_history.append(submit_action)
            return submit_action

        # OPTIMIZATION 1: Drastically compress the context window
        # Extract only named, interactive elements from the spatial map
        interactive_elements = [
            el for el in safe_state.get("spatial_map", []) 
            if el.get('name') and el.get('name') != '[REDACTED_SECRET]'
        ]
        
        # Minify the JSON string to save tokens
        minified_state = json.dumps(interactive_elements, separators=(',', ':'))

        # OPTIMIZATION 2: Few-Shot Prompting
        system_prompt = """
            You are an autonomous UI discovery agent exploring a legacy web application. 

            ### CRITICAL RULES
            1. **DUAL LOCATORS:** For every target, you MUST provide both an XPath (primary) and a Spatial name (fallback).
            2. **SPATIAL FALLBACK:** The spatial locator value MUST exactly match the `name` field from the `spatial_map` payload.
            3. **SEQUENCE:** Fill out forms (type_text) step-by-step BEFORE clicking submit buttons. 

            ### EXAMPLE SEQUENCE (GENERIC LOGIN)
            Identify the sign-in fields from their type, autocomplete metadata, placeholder, label, or nearby text. Map the non-secret credential to `${input.username}` and the secret credential to `${input.password}`. Do not assume a particular label vocabulary.
        """
        
        user_content = f"GOAL: {goal}\n\n"
        if self.action_history:
            minified_history = json.dumps([a.model_dump() for a in self.action_history], separators=(',', ':'))
            user_content += f"PREVIOUS ACTIONS:\n{minified_history}\n\n"
            
        user_content += f"CURRENT UI STATE:\n{minified_state}"
        
        try:
            logger.debug("Requesting the next discovery action from the local model.")
            response = self.client.chat(
                model=self.model_name,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_content}
                ],
                format=Action.model_json_schema(),
                # OPTIMIZATION 3: Force absolute determinism
                options={
                    "temperature": 0.0,
                    "top_p": 0.1
                }
            )
            
            raw_json = response['message']['content']
            next_action = Action.model_validate_json(raw_json)

            if next_action.action_type == "click" and self._looks_like_non_submit_login_link(next_action):
                submit_action = self._login_submit_action(safe_state)
                if submit_action is not None:
                    next_action = submit_action

            # A type action without an input binding is a model description,
            # not an executable credential entry.
            if next_action.action_type in ("type_text", "select_dropdown") and not next_action.value_ref:
                next_action = self._next_unrecorded_form_action(safe_state)
                if next_action is None:
                    return None

            # Small local models can keep selecting the first field even after it
            # was filled. Advance through the visible form controls instead of
            # recording the same action repeatedly.
            if any(self._same_action(next_action, previous) for previous in self.action_history):
                fallback_action = self._next_unrecorded_form_action(safe_state)
                if fallback_action is not None:
                    next_action = fallback_action
            
            self.action_history.append(next_action)
            return next_action
            
        except ValidationError as e:
            logger.warning("Model returned an invalid action schema: %s", e)
            return None

    def _submit_after_credentials(self, safe_state: Dict[str, Any]) -> Optional[Action]:
        """Uses the observed form submit once both credential fields are filled."""
        value_refs = {
            action.value_ref
            for action in self.action_history
            if action.action_type == "type_text" and action.value_ref
        }
        if "${input.username}" not in value_refs or "${input.password}" not in value_refs:
            return None

        if any(action.action_type == "click" and action.step_id == "click_login" for action in self.action_history):
            return None
        return self._login_submit_action(safe_state)

    @staticmethod
    def _same_action(current: Action, previous: Action) -> bool:
        return (
            current.action_type == previous.action_type
            and current.value_ref == previous.value_ref
            and current.target is not None
            and previous.target is not None
            and current.target.primary_locator.value == previous.target.primary_locator.value
        )

    def _next_unrecorded_form_action(self, safe_state: Dict[str, Any]) -> Optional[Action]:
        recorded_values = {
            action.target.primary_locator.value
            for action in self.action_history
            if action.action_type == "type_text" and action.target is not None
        }
        form_elements = [
            element for element in safe_state.get("spatial_map", [])
            if element.get("tag") in ("input", "textarea")
            and (element.get("name") or element.get("placeholder") or element.get("type"))
        ]

        for element in form_elements:
            field_name = (
                element.get("name")
                or element.get("placeholder")
                or element.get("type")
                or "credential"
            ).strip()
            placeholder = (element.get("placeholder") or "").strip()
            field_name_attr = (element.get("name_attr") or "").strip()
            autocomplete = (element.get("autocomplete") or "").strip()
            if field_name_attr:
                xpath = f"//{element['tag']}[@name='{field_name_attr}']"
            elif placeholder:
                xpath = f"//{element['tag']}[@placeholder='{placeholder}']"
            elif autocomplete:
                xpath = f"//{element['tag']}[@autocomplete='{autocomplete}']"
            else:
                xpath = f"//{element['tag']}[@type='{element.get('type')}']"
            if xpath in recorded_values:
                continue
            field_key = " ".join(
                str(element.get(key) or "")
                for key in ("name", "placeholder", "type")
            ).lower()
            is_secret = (
                element.get("type") == "password"
                or any(token in field_key for token in ("password", "passcode", "secret", "pin"))
            )
            if element.get("type") != "hidden":
                primary_locator, fallback_locator = self._field_locators(element)
                return Action.model_validate({
                    "step_id": f"enter_{field_key.replace(' ', '_').replace(':', '')}",
                    "intent": f"Type {field_name}",
                    "action_type": "type_text",
                    "target": {
                        "primary_locator": primary_locator,
                        "fallback_locator": fallback_locator,
                        "robustness_reasoning": "Uses the strongest observed field attribute first, with a distinct metadata fallback when available.",
                    },
                    "value_ref": f"${{input.{ 'password' if is_secret else 'username' }}}",
                })

        recorded_actions = {
            action.target.primary_locator.value
            for action in self.action_history
            if action.action_type == "click" and action.target is not None
        }
        for element in safe_state.get("spatial_map", []):
            element_name = (element.get("name") or "").strip()
            name_key = element_name.lower()
            if (
                element.get("tag") == "a"
                and any(
                    token in name_key
                    for token in ("history", "transaction", "activity", "statement")
                )
            ):
                href = element.get("href") or "/history"
                xpath = f"//a[@href='{href}']"
                if xpath not in recorded_actions:
                    fallback_locator = {
                        "type": "accessibility_role",
                        "value": "link",
                        "name": element_name,
                    }
                    return Action.model_validate({
                        "step_id": "view_transaction_history",
                        "intent": f"Click {element_name}",
                        "action_type": "click",
                        "target": {
                            "primary_locator": {"type": "xpath", "value": xpath},
                            "fallback_locator": self._coordinate_locator(element) or fallback_locator,
                            "robustness_reasoning": "Uses the stable history link href.",
                        },
                    })
            if element.get("tag") in ("button", "a") and any(
                token in name_key for token in ("log in", "login", "sign in")
            ):
                xpath = f"//{element['tag']}[contains(normalize-space(), '{element_name}')]"
                fallback_locator = {
                    "type": "accessibility_role",
                    "value": "button",
                    "name": element_name,
                }
                return Action.model_validate({
                    "step_id": "click_login",
                    "intent": f"Click {element_name}",
                    "action_type": "click",
                    "target": {
                        "primary_locator": {"type": "xpath", "value": xpath},
                        "fallback_locator": self._coordinate_locator(element) or fallback_locator,
                        "robustness_reasoning": "Uses the visible login control text with resolver fallbacks.",
                    },
                })
        return None

    @staticmethod
    def _looks_like_non_submit_login_link(action: Action) -> bool:
        text = f"{action.intent} {action.target.primary_locator.value if action.target else ''}".lower()
        return any(token in text for token in ("forgot", "reset", "register", "sign up", "help"))

    def _login_submit_action(self, safe_state: Dict[str, Any]) -> Optional[Action]:
        """Returns the real form submit control, excluding account-help links."""
        for element in safe_state.get("spatial_map", []):
            if not isinstance(element, dict):
                continue
            tag = element.get("tag")
            element_type = (element.get("type") or "").lower()
            name = (element.get("name") or "").strip()
            name_key = name.lower()
            is_submit = element_type in ("submit", "button") or tag == "button"
            looks_like_login = any(token in name_key for token in ("log in", "login", "sign in", "submit"))
            if not is_submit or not looks_like_login:
                continue

            escaped_name = name.replace("'", "&apos;")
            if tag == "input" and name:
                xpath = f"//input[@type='{element_type}' and @value='{escaped_name}']"
            elif tag == "button" and name:
                xpath = f"//button[contains(normalize-space(), '{escaped_name}')]"
            else:
                xpath = f"//*[@type='{element_type}']"
            fallback_locator = {
                "type": "accessibility_role",
                "value": "button",
                "name": name or None,
            }
            return Action.model_validate({
                "step_id": "click_login",
                "intent": f"Click {name or 'submit'}",
                "action_type": "click",
                "target": {
                    "primary_locator": {"type": "xpath", "value": xpath},
                        "fallback_locator": self._coordinate_locator(element) or fallback_locator,
                    "robustness_reasoning": "Targets the observed form submit control, not recovery links.",
                },
            })
        return None

    @staticmethod
    def _field_locators(element: Dict[str, Any]):
        """Builds distinct locators from the field metadata observed in the page."""
        tag = element.get("tag", "input")
        name_attr = (element.get("name_attr") or "").strip()
        placeholder = (element.get("placeholder") or "").strip()
        autocomplete = (element.get("autocomplete") or "").strip()
        input_type = (element.get("type") or "").strip()

        candidates = []
        for attribute, value in (
            ("name", name_attr),
            ("placeholder", placeholder),
            ("autocomplete", autocomplete),
            ("type", input_type),
        ):
            if value:
                candidates.append({"type": "xpath", "value": f"//{tag}[@{attribute}='{value}']"})

        if not candidates:
            candidates.append({"type": "xpath", "value": f"//{tag}"})

        primary = candidates[0]
        fallback = next(
            (candidate for candidate in candidates[1:] if candidate != primary),
            None,
        )
        coordinate_fallback = DiscoveryAgent._coordinate_locator(element)
        if coordinate_fallback is not None:
            fallback = coordinate_fallback
        return primary, fallback

    @staticmethod
    def _coordinate_locator(element: Dict[str, Any]):
        if element.get("x") is None or element.get("y") is None:
            return None
        return {
            "type": "coordinates",
            "value": "viewport",
            "name": (element.get("name") or "").strip() or None,
            "role": element.get("role"),
            "tag": element.get("tag"),
            "x": element["x"],
            "y": element["y"],
            "width": element.get("width"),
            "height": element.get("height"),
        }