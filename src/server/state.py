import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

class InterventionTracker:
    def __init__(self):
        self.is_paused = False
        self.resume_event = asyncio.Event()
        self.current_context: Dict[str, Any] = {}
        self.page = None

    def set_page(self, page: Any) -> None:
        """Connects the tracker to the live browser page used for HITL."""
        self.page = page

    def resume(self) -> bool:
        """Signals the waiting execution to continue after operator intervention."""
        if not self.is_paused:
            return False
        self.resume_event.set()
        return True

    async def escalate_to_human(self, goal: str, step_id: str, error_details: str):
        """Locks the execution thread and alerts the operator."""
        self.is_paused = True
        self.resume_event.clear()
        self.current_context = {
            "goal": goal, 
            "step": step_id, 
            "error": error_details
        }
        
        logger.warning("HITL escalation at '%s': %s", step_id, error_details)
        logger.info("Take control of the live browser, correct the issue, and click Resume automation.")

        if self.page is None:
            await self.resume_event.wait()
        else:
            await self._wait_for_browser_resume(goal, step_id, error_details)

        await self._clear_browser_intervention()
        self.is_paused = False
        self.current_context = {}
        logger.info("HITL resumed; handing control back to the deterministic engine.")

    async def _wait_for_browser_resume(self, goal: str, step_id: str, error_details: str) -> None:
        self.resume_event.clear()
        await self.page.evaluate(
            """({goal, step, error}) => {
                window.__hitlResumeRequested = false;
                document.documentElement.style.outline = '8px solid #dc2626';
                document.documentElement.style.outlineOffset = '-8px';
                const existing = document.getElementById('__hitl-intervention');
                if (existing) existing.remove();
                const panel = document.createElement('div');
                panel.id = '__hitl-intervention';
                panel.style.cssText = [
                    'position:fixed', 'top:16px', 'right:16px', 'z-index:2147483647',
                    'max-width:360px', 'padding:16px', 'border:3px solid #dc2626',
                    'border-radius:8px', 'background:#fff7ed', 'color:#111827',
                    'font:14px/1.4 sans-serif', 'box-shadow:0 4px 16px #0005'
                ].join(';');
                panel.innerHTML = `<strong style="color:#b91c1c">Human intervention required</strong>
                    <p style="margin:8px 0"><b>Step:</b> ${step}</p>
                    <p style="margin:8px 0">${error}</p>
                    <button id="__hitl-resume" style="cursor:pointer;padding:8px 12px;border:0;border-radius:4px;background:#b91c1c;color:white;font-weight:700">
                        Resume automation
                    </button>`;
                document.body.appendChild(panel);
                document.getElementById('__hitl-resume').onclick = () => {
                    window.__hitlResumeRequested = true;
                };
            }""",
            {"goal": goal, "step": step_id, "error": error_details},
        )
        # HITL is intentionally unbounded: waiting for the operator is a valid
        # execution state and must not become a Playwright timeout crash.
        await self.page.wait_for_function(
            "window.__hitlResumeRequested === true",
            timeout=0,
        )

    async def _clear_browser_intervention(self) -> None:
        if self.page is None:
            return
        await self.page.evaluate(
            """() => {
                document.documentElement.style.outline = '';
                document.documentElement.style.outlineOffset = '';
                document.getElementById('__hitl-intervention')?.remove();
                window.__hitlResumeRequested = false;
            }"""
        )

    async def request_approval(self, goal: str, step_id: str, reason: str):
        """Used for risky/irreversible actions."""
        await self.escalate_to_human(goal, step_id, reason)

# At the bottom of src/server/state.py
tracker = InterventionTracker()