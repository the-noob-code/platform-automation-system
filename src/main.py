import asyncio
import json
import logging
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from server.state import tracker  
from discovery.perception import PerceptionEngine
from discovery.scrubber import PIIScrubber
from discovery.agent import DiscoveryAgent
from discovery.artifact_compiler import ArtifactCompiler
from executor.adapter import PlaywrightWebAdapter
from executor.engine import DeterministicExecutor
from models.artifact import Checkpoint, ErrorTaxonomy

logger = logging.getLogger(__name__)


def _as_locator_dict(locator):
    if locator is None:
        return {}
    if isinstance(locator, dict):
        return locator
    return {
        "type": getattr(locator, "type", None),
        "value": getattr(locator, "value", None),
        "name": getattr(locator, "name", None),
    }


def locator_candidates_for_action(target, safe_state):
    """Returns locator candidates from recorded and observed page metadata."""
    if target is None:
        return []

    primary = _as_locator_dict(getattr(target, "primary_locator", None) if not isinstance(target, dict) else target.get("primary_locator"))
    fallback = _as_locator_dict(getattr(target, "fallback_locator", None) if not isinstance(target, dict) else target.get("fallback_locator"))

    candidates = []
    for locator in (primary, fallback):
        value = (locator.get("value") or "").strip()
        if value:
            candidates.append(value)
        name = (locator.get("name") or "").strip()
        if name:
            candidates.append(name)

    spatial_map = safe_state.get("spatial_map", []) if isinstance(safe_state, dict) else []
    for element in spatial_map:
        if not isinstance(element, dict):
            continue
        name = (element.get("name") or "").strip()
        if not name:
            continue
        candidates.append(name)
        candidates.extend([
            f"input[aria-label='{name}']",
            f"input[placeholder='{name}']",
            f"input[name='{name}']",
            f"textarea[aria-label='{name}']",
            f"button:has-text('{name}')",
            f"[role='textbox'][aria-label='{name}']",
            f"[role='button'][aria-label='{name}']",
        ])

    # Deduplicate while preserving order.
    seen = set()
    ordered = []
    for candidate in candidates:
        key = candidate.strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


async def resolve_and_execute_action(page, action, safe_state, text_to_type):
    """Attempts the original and fallback locators, including label-based selectors."""
    last_error = None

    for candidate in locator_candidates_for_action(action.target, safe_state):
        try:
            locator = None
            if candidate.startswith("//") or candidate.startswith("(//") or candidate.startswith("./"):
                locator = page.locator(candidate)
            elif candidate.startswith("input[") or candidate.startswith("button:has-text(") or candidate.startswith("textarea[") or candidate.startswith("[role="):
                locator = page.locator(candidate)
            else:
                locator = page.get_by_label(candidate, exact=False)
                if await locator.count() == 0:
                    locator = page.get_by_role("textbox", name=candidate, exact=False)
                if await locator.count() == 0:
                    locator = page.get_by_role("button", name=candidate, exact=False)
                if await locator.count() == 0:
                    continue

            if action.action_type == "click":
                await locator.first.click(timeout=2000, no_wait_after=True)
                return True
            elif action.action_type == "type_text":
                await locator.first.fill(text_to_type, timeout=2000)
                return True
        except Exception as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    return False


async def logout_after_discovery(page) -> bool:
    """Resets the discovery session without adding cleanup to the artifact."""
    controls = page.locator("button, a, input[type='submit']")
    count = await controls.count()
    logout_tokens = ("logout", "log out", "sign out", "logoff", "log off")
    for index in range(count):
        control = controls.nth(index)
        label = (
            await control.inner_text()
            if await control.evaluate("el => el.tagName.toLowerCase() !== 'input'")
            else await control.get_attribute("value")
        ) or ""
        if any(token in label.lower() for token in logout_tokens):
            await control.click(timeout=3000)
            return True
    return False


async def open_target_page(page, target_url: str) -> None:
    """Opens a target while tolerating normal login redirects."""
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
    except Exception as error:
        if "interrupted by another navigation" not in str(error).lower():
            raise
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass


async def main():
    os.makedirs("evidence", exist_ok=True)
    os.makedirs("artifacts", exist_ok=True)
    
    # Load project credentials and let the current .env override stale exported values.
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    load_dotenv(env_path, override=True)
    app_target = os.environ.get("APP_TARGET", "local").strip().lower()
    target_configs = {
        "local": {
            "url": os.environ.get("LOCAL_TARGET_URL", "http://127.0.0.1:9000/login"),
            "discovery_user": os.environ.get("LOCAL_DISCOVERY_USERNAME"),
            "discovery_pass": os.environ.get("LOCAL_DISCOVERY_PASSWORD"),
            "replay_user": os.environ.get("LOCAL_REPLAY_USERNAME"),
            "replay_pass": os.environ.get("LOCAL_REPLAY_PASSWORD"),
        },
        "parabank": {
            "url": os.environ.get("PARABANK_TARGET_URL", "http://parabank.parasoft.com/parabank/index.htm"),
            "discovery_user": os.environ.get("PARABANK_DISCOVERY_USERNAME"),
            "discovery_pass": os.environ.get("PARABANK_DISCOVERY_PASSWORD"),
            "replay_user": os.environ.get("PARABANK_REPLAY_USERNAME"),
            "replay_pass": os.environ.get("PARABANK_REPLAY_PASSWORD"),
        },
    }
    if app_target not in target_configs:
        raise ValueError("APP_TARGET must be either 'local' or 'parabank'.")

    config = target_configs[app_target]
    disc_user = config["discovery_user"] or os.environ.get("DISCOVERY_USERNAME", "default_disc_user")
    disc_pass = config["discovery_pass"] or os.environ.get("DISCOVERY_PASSWORD", "default_disc_pass")
    rep_user = config["replay_user"] or os.environ.get("REPLAY_USERNAME", "default_rep_user")
    rep_pass = config["replay_pass"] or os.environ.get("REPLAY_PASSWORD", "default_rep_pass")
    
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Initializing computer-use automation system for target '%s'.", app_target)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=['--start-maximized'], slow_mo=500)
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()
        tracker.set_page(page)
        
        perception = PerceptionEngine(page)
        scrubber = PIIScrubber()
        agent = DiscoveryAgent()
        
        target_url = config["url"]
        
        goal = (
            "Sign into the application using the configured credentials. "
            "Once authenticated, find and open the page for the account."
        )

        logger.info("Starting discovery run against %s.", target_url)
        await open_target_page(page, target_url)
        await page.wait_for_timeout(500)
        
        with open("evidence/discovery_run.log", "w") as f:
            for step_num in range(5):  
                try:
                    raw_state = await asyncio.wait_for(
                        perception.capture_state(),
                        timeout=8,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Discovery state capture timed out; continuing with the recorded flow.")
                    break
                
                # Save discovery screenshots to disk
                import base64
                if raw_state.get("screenshot_base64"):
                    try:
                        img_data = base64.b64decode(raw_state["screenshot_base64"])
                        with open(f"evidence/discovery_step_{step_num}.jpg", "wb") as img_file:
                            img_file.write(img_data)
                    except Exception as e:
                        pass # Ignore decode errors if fallback pixel was used

                safe_state = scrubber.scrub_state(raw_state)
                action = agent.decide_next_action(goal, safe_state)
                
                if not action:
                    break
                    
                f.write(f"Step {step_num}: {action.intent}\n")
                logger.info("Discovery action selected: %s", action.intent)
                
                try:
                    if action.target:
                        if action.action_type in ("type_text", "select_dropdown") and not action.value_ref:
                            logger.warning("Skipping unbound form action: %s", action.intent)
                            continue

                        # Resolve Credentials Early
                        text_to_type = action.value_ref if action.value_ref else "dummy_text"
                        if text_to_type.startswith("${"):
                            text_to_type = (
                                disc_pass
                                if any(token in text_to_type.lower() for token in ("password", "passcode", "secret", "pin"))
                                else disc_user
                            )

                        element_resolved = await resolve_and_execute_action(page, action, safe_state, text_to_type)
                        if not element_resolved:
                            raise Exception("All generated and fallback locators failed.")

                        logger.info("Discovery action completed: %s", action.intent)

                        if action.step_id == "click_login":
                            break

                except Exception as e:
                    if agent.action_history and agent.action_history[-1] is action:
                        agent.action_history.pop()
                    logger.warning("Discovery action failed: %s", e)
                
                if action.checkpoint and action.checkpoint.type == "url_match":
                    break

        try:
            if await logout_after_discovery(page):
                logger.info("Discovery session logged out after landing-page verification.")
        except Exception as error:
            logger.warning("Discovery logout skipped: %s", error)
                    
        capability_name = f"{app_target}_login"
        logger.info("Compiling artifact '%s'.", capability_name)
        compiler = ArtifactCompiler(
            capability_name,
            "Signs into the configured web application and reaches its authenticated landing page.",
        )
        mock_success = Checkpoint(type="text_exists", value="__authenticated_landing__")
        mock_taxonomy = ErrorTaxonomy(expected_business_outcomes=[], recoverable_conditions=[])
        
        artifact = compiler.compile_artifact(agent.action_history, mock_success, mock_taxonomy) 
        
        with open("evidence/saved_artifact.json", "w") as f:
            f.write(artifact.model_dump_json(indent=2))

        logger.info("Starting deterministic replay for '%s'.", artifact.capability_name)
        await context.clear_cookies()
        await open_target_page(page, target_url)
        
        # ==========================================
        # POINT 5: REPLAY CREDENTIAL INJECTION
        # ==========================================
        adapter = PlaywrightWebAdapter(page)
        executor = DeterministicExecutor(adapter, tracker)
        
        secure_inputs = {
            "username": rep_user,
            "password": rep_pass,
        }
        
        with open("evidence/replay_run.log", "w") as f:
            f.write(f"Starting replay of {artifact.capability_name} with inputs: {list(secure_inputs.keys())}\n")
            
            result = await executor.execute(artifact, secure_inputs)
            
            f.write(f"Result: {json.dumps(result)}\n")
            logger.info("Replay finished with status: %s", result["status"])
            if result.get("status") == "success":
                try:
                    if await logout_after_discovery(page):
                        logger.info("Replay session logged out after landing-page verification.")
                except Exception as error:
                    logger.warning("Replay logout skipped: %s", error)
        # ==========================================

        await browser.close()
        
    logger.info("Automation complete.")

if __name__ == "__main__":
    asyncio.run(main())