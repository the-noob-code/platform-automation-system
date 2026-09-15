import re
import logging
from typing import Optional, Protocol, Any, Dict
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from models.artifact import Locator, Checkpoint
from .exceptions import (
    BusinessOutcomeReached,
    CheckpointVerificationError,
    HardFailureError,
    RecoverableConditionError,
)

logger = logging.getLogger(__name__)

class UIAdapter(Protocol):
    """Abstract interface to support heterogeneous surfaces (Web, Desktop, Legacy)."""
    async def perform_action(
        self, 
        action_type: str, 
        primary: Locator, 
        fallback: Optional[Locator] = None, 
        value: Optional[str] = None
    ) -> Optional[Dict[str, Any]]: ...
    
    async def verify_checkpoint(self, checkpoint: Checkpoint) -> bool: ...


class PlaywrightWebAdapter:
    def __init__(self, page: Page):
        self.page = page

    async def _resolve_locator(self, locator: Locator):
        """Maps schema locators to Playwright queries."""
        if locator.type == "accessibility_role":
            # Playwright's get_by_role is highly resilient to DOM changes
            return self.page.get_by_role(locator.value, name=locator.name, exact=True)
        elif locator.type == "xpath":
            return self.page.locator(locator.value)
        elif locator.type == "test_id":
            return self.page.get_by_test_id(locator.value)
        elif locator.type == "coordinates":
            return locator
        raise HardFailureError(f"Unsupported locator type: {locator.type}")

    @staticmethod
    def _coordinate_point(locator: Locator):
        if locator.x is None or locator.y is None:
            raise RecoverableConditionError(
                "element_not_found",
                "The coordinate fallback is missing its viewport position.",
            )
        return (
            locator.x + (locator.width or 0) / 2,
            locator.y + (locator.height or 0) / 2,
        )

    async def _refresh_coordinate_locator(self, saved: Locator):
        """Re-identifies a coordinate target against the current page layout."""
        elements = await self.page.evaluate(
            """() => Array.from(document.querySelectorAll(
                'button, input, a, select, textarea, [role="button"], [role="link"], [role="combobox"], [role="textbox"]'
            )).map(el => {
                const rect = el.getBoundingClientRect();
                const name = (el.getAttribute('aria-label') || el.getAttribute('name') ||
                    el.getAttribute('placeholder') || el.innerText || el.value || '').trim();
                return {
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role'),
                    name: name.substring(0, 100),
                    x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                    visible: rect.width > 0 && rect.height > 0 &&
                        getComputedStyle(el).visibility !== 'hidden' &&
                        getComputedStyle(el).display !== 'none'
                };
            }).filter(el => el.visible)"""
        )
        if not elements:
            return None

        old_x, old_y = saved.x or 0, saved.y or 0
        old_center = (old_x + (saved.width or 0) / 2, old_y + (saved.height or 0) / 2)

        def score(element):
            value = 0
            if saved.name and element["name"].casefold() == saved.name.casefold():
                value += 100
            elif saved.name and saved.name.casefold() in element["name"].casefold():
                value += 50
            if saved.tag and element["tag"] == saved.tag:
                value += 20
            if saved.role and element["role"] == saved.role:
                value += 20
            center = (element["x"] + element["width"] / 2, element["y"] + element["height"] / 2)
            distance = ((center[0] - old_center[0]) ** 2 + (center[1] - old_center[1]) ** 2) ** 0.5
            return value - min(distance / 10, 30)

        match = max(elements, key=score)
        if score(match) < 20:
            return None
        return saved.model_copy(update={
            "x": match["x"],
            "y": match["y"],
            "width": match["width"],
            "height": match["height"],
        })

    async def _find_element(self, primary: Locator, fallback: Optional[Locator]):
        """Attempts primary targeting, gracefully falling back to secondary."""
        try:
            pw_locator = await self._resolve_locator(primary)
            await pw_locator.wait_for(state="visible", timeout=3000)
            return pw_locator
        except PlaywrightTimeoutError:
            legacy_locator = self._legacy_label_locator(primary)
            if legacy_locator is not None:
                try:
                    await legacy_locator.wait_for(state="visible", timeout=3000)
                    return legacy_locator
                except PlaywrightTimeoutError:
                    pass

            if fallback:
                logger.info("Primary locator failed; attempting %s fallback.", fallback.type)
                if fallback.type == "coordinates":
                    refreshed = await self._refresh_coordinate_locator(fallback)
                    if refreshed is not None:
                        x, y = self._coordinate_point(refreshed)
                        visible = await self.page.evaluate(
                            """({x, y}) => {
                                const element = document.elementFromPoint(x, y);
                                if (!element) return false;
                                const style = getComputedStyle(element);
                                return style.visibility !== 'hidden' && style.display !== 'none';
                            }""",
                            {"x": x, "y": y},
                        )
                        if visible:
                            return refreshed
                else:
                    try:
                        pw_locator = await self._resolve_locator(fallback)
                        await pw_locator.wait_for(state="visible", timeout=5000)
                        return pw_locator
                    except PlaywrightTimeoutError:
                        pass
            
            # If both fail, it is a recoverable condition (triggering a retry in the engine)
            raise RecoverableConditionError(
                "element_not_found",
                f"Could not locate element: {primary.name or primary.value}",
            )

    def _legacy_label_locator(self, locator: Locator):
        """Resolves old artifacts whose XPath treated visible labels as names."""
        if locator.type != "xpath":
            return None

        match = re.search(r"@name=['\"]([^'\"]+)['\"]", locator.value)
        if not match:
            return None

        label = match.group(1)
        return self.page.locator(
            f"//td[normalize-space()={self._xpath_literal(label)}]"
            "/following-sibling::td//input"
        )

    @staticmethod
    def _xpath_literal(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"

    async def perform_action(
        self, 
        action_type: str, 
        primary: Locator, 
        fallback: Optional[Locator] = None, 
        value: Optional[str] = None
    ) -> Optional[str]:
        """Executes interactions using stable element targeting."""
        element = await self._find_element(primary, fallback)

        try:
            if isinstance(element, Locator) and element.type == "coordinates":
                x, y = self._coordinate_point(element)
                await self.page.mouse.click(x, y)
                if action_type == "type_text":
                    await self.page.keyboard.insert_text(value or "")
                elif action_type not in ("click", "type_text"):
                    raise HardFailureError(
                        "Coordinate fallback supports click and text input only."
                    )
                return None
            if action_type == "click":
                await element.click(no_wait_after=True)
                await self._raise_for_page_error()
            elif action_type == "type_text":
                await element.fill(value or "")
            elif action_type == "select_dropdown":
                await element.select_option(value or "")
            elif action_type == "extract_data":
                return await element.inner_text()
            else:
                raise HardFailureError(f"Unknown action type: {action_type}")
                
            return None
            
        except BusinessOutcomeReached:
            raise
        except Exception as e:
            raise HardFailureError(f"Action execution failed: {str(e)}")

    async def _raise_for_page_error(self) -> None:
        """Stops after actions that leave a visible application error message."""
        selectors = ".err:visible, [role='alert']:visible, .error:visible"
        error_locator = self.page.locator(selectors).first
        try:
            await error_locator.wait_for(state="visible", timeout=1000)
        except PlaywrightTimeoutError:
            return

        message = (await error_locator.inner_text()).strip()
        if message:
            raise HardFailureError(f"Invalid login credentials: {message}")

    async def verify_checkpoint(self, checkpoint: Checkpoint) -> bool:
        """Validates that expected state changes actually occurred."""
        try:
            if checkpoint.type == "text_exists" and checkpoint.value == "__authenticated_landing__":
                await self._verify_authenticated_landing()
                return True

            if checkpoint.type == "element_visible" and checkpoint.target:
                element = await self._find_element(checkpoint.target.primary_locator, checkpoint.target.fallback_locator)
                is_visible = await element.is_visible()
                if not is_visible:
                    raise CheckpointVerificationError(f"Checkpoint failed: Element not visible.")
            
            elif checkpoint.type == "text_exists":
                try:
                    await self.page.get_by_text(checkpoint.value, exact=True).first.wait_for(
                        state="visible", timeout=3000
                    )
                except PlaywrightTimeoutError:
                    if checkpoint.value and "history" in checkpoint.value.lower():
                        history_link = self.page.locator(
                            "a[href*='history'], a[href*='activity'], "
                            "a[href*='transaction'], a[href*='statement'], "
                            "a:has-text('History'), a:has-text('Activity'), "
                            "a:has-text('Transactions'), a:has-text('Statements')"
                        ).first
                        try:
                            await history_link.wait_for(state="visible", timeout=1000)
                        except PlaywrightTimeoutError:
                            current_url = self.page.url.lower()
                            if not any(
                                token in current_url
                                for token in ("history", "activity", "transaction", "statement")
                            ):
                                page_text = (await self.page.locator("body").inner_text()).lower()
                                if not any(
                                    token in page_text
                                    for token in ("history", "activity", "transaction", "statement")
                                ):
                                    raise
                    else:
                        raise
                
            return True
        except PlaywrightTimeoutError:
            raise CheckpointVerificationError(f"Checkpoint failed: {checkpoint.type}")

    async def _verify_authenticated_landing(self) -> None:
        """Confirms login succeeded without assuming a site's landing-page labels."""
        login_controls = self.page.locator(
            "input[type='password'], input[name*='user' i], input[name*='email' i]"
        )
        if await login_controls.count() > 0:
            raise CheckpointVerificationError(
                "The application still shows its sign-in form."
            )

        body_text = (await self.page.locator("body").inner_text()).lower()
        has_logout_control = await self.page.locator(
            "button, a, input[type='submit']"
        ).evaluate_all(
            "elements => elements.some(el => /log ?out|sign ?out|logout|logoff/i.test((el.innerText || el.value || '')) )"
        )
        if not has_logout_control and not body_text.strip():
            raise CheckpointVerificationError(
                "The authenticated landing page did not render."
            )