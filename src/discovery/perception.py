import base64
import logging
from typing import Dict, Any, List
from playwright.async_api import Page

logger = logging.getLogger(__name__)

class PerceptionEngine:
    def __init__(self, page: Page):
        self.page = page

    async def capture_state(self) -> Dict[str, Any]:
        """Captures the semantic AT, visual snapshot, and spatial map."""
        accessibility_tree = await self._get_accessibility_tree()
        bounding_boxes = await self._get_interactive_bounding_boxes()
        screenshot_b64 = await self._get_screenshot()
        return {
            "accessibility_tree": accessibility_tree,
            "spatial_map": bounding_boxes,
            "screenshot_base64": screenshot_b64
        }

    async def _get_accessibility_tree(self) -> Dict[str, Any]:
        """
        Extracts an accessibility tree via JS evaluation.
        This replaces the Node-only page.accessibility API by manually 
        traversing the DOM and filtering for 'interesting' semantic nodes.
        """
        js_extractor = """
        () => {
            function buildNode(el) {
                // Filter out non-element nodes
                if (el.nodeType !== Node.ELEMENT_NODE) return null;
                
                // Filter out hidden elements
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return null;

                const tag = el.tagName.toLowerCase();
                const role = el.getAttribute('role') || tag;
                
                // --- START LEGACY UI HEURISTIC ---
                let extractedName = (el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('autocomplete') || el.getAttribute('placeholder') || el.alt || el.innerText || el.value || '').trim();
                
                if (!extractedName && (tag === 'input' || tag === 'select' || tag === 'textarea')) {
                    let prevCell = el.parentElement?.previousElementSibling;
                    if (prevCell && prevCell.tagName === 'TD') {
                        extractedName = prevCell.innerText.trim();
                    } 
                    else if (el.previousSibling && el.previousSibling.nodeType === Node.TEXT_NODE) {
                        extractedName = el.previousSibling.textContent.trim();
                    }
                }
                const name = extractedName.substring(0, 100);
                // --- END LEGACY UI HEURISTIC ---

                const typeAttr = el.getAttribute('type');
                
                let children = [];
                for (let child of el.childNodes) {
                    const childNode = buildNode(child);
                    if (childNode) children.push(childNode);
                }
                
                const isInteractive = ['input', 'button', 'a', 'select', 'textarea'].includes(tag);
                const hasContent = name.trim() !== '';
                
                // Return node if it is interactive, has semantic text, or contains valid children
                if (isInteractive || hasContent) {
                    return {
                        role: role,
                        name: name,
                            name_attr: el.getAttribute('name'),
                        value: el.value || undefined,
                        type: typeAttr || undefined,
                        children: children.length > 0 ? children : undefined
                    };
                }
                
                // If it's a layout wrapper, pass its children up the tree
                if (children.length === 1) return children[0];
                if (children.length > 1) return { role: 'group', children: children };
                
                return null;
            }
            return buildNode(document.body) || {};
        }
        """
        return await self.page.evaluate(js_extractor)

    async def _get_interactive_bounding_boxes(self) -> List[Dict[str, Any]]:
        """Injects JS to extract coordinates of all visible, interactive elements."""
        js_extractor = """
        () => {
            const elements = document.querySelectorAll('button, input, a, select, textarea, [role="button"], [role="link"], [role="combobox"], [role="textbox"]');
            const map = [];
            elements.forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    
                    // --- START LEGACY UI HEURISTIC ---
                    let extractedName = (el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('autocomplete') || el.getAttribute('placeholder') || el.innerText || el.value || '').trim();
                    
                    if (!extractedName && (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA')) {
                        let prevCell = el.parentElement?.previousElementSibling;
                        if (prevCell && prevCell.tagName === 'TD') {
                            extractedName = prevCell.innerText.trim();
                        } else if (el.previousSibling && el.previousSibling.nodeType === Node.TEXT_NODE) {
                            extractedName = el.previousSibling.textContent.trim();
                        }
                    }
                    // --- END LEGACY UI HEURISTIC ---

                    map.push({
                        tag: el.tagName.toLowerCase(),
                        role: el.getAttribute('role'),
                        name: extractedName.substring(0, 50),
                            name_attr: el.getAttribute('name'),
                            type: el.getAttribute('type'),
                            autocomplete: el.getAttribute('autocomplete'),
                            placeholder: el.getAttribute('placeholder'),
                        href: el.getAttribute('href'),
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height,
                        id: el.id
                    });
                }
            });
            return map;
        }
        """
        return await self.page.evaluate(js_extractor)

    async def _get_screenshot(self) -> str:
        """Captures a JPEG screenshot of the current page safely."""
        import base64
        try:
            # Ensure the DOM is fully loaded before capturing
            await self.page.wait_for_load_state("domcontentloaded", timeout=2000)
            
            screenshot_bytes = await self.page.screenshot(type="jpeg", quality=70)
            return base64.b64encode(screenshot_bytes).decode("utf-8")
            
        except Exception as e:
            logger.warning("Screenshot capture failed; proceeding with DOM only: %s", e)
            # Return a blank 1x1 transparent pixel in base64 to satisfy the payload without crashing
            return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="