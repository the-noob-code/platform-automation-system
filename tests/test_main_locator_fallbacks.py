from src.discovery.scrubber import PIIScrubber
from src.main import locator_candidates_for_action


def test_member_id_xpath_falls_back_to_accessible_names():
    target = {
        "primary_locator": {"type": "xpath", "value": "//input[@name='Member ID:']"},
        "fallback_locator": {"type": "xpath", "value": "//input[@name='Member ID:']"},
    }

    candidates = locator_candidates_for_action(target, {
        "spatial_map": [
            {"name": "Member ID:"},
            {"name": "PIN:"},
            {"name": "Log In"},
        ]
    })

    assert any("Member ID:" in candidate for candidate in candidates)
    assert any("textbox" in candidate.lower() or "role='textbox'" in candidate.lower() for candidate in candidates)
    assert any("Log In" in candidate for candidate in candidates)


def test_scrubber_keeps_field_labels_visible():
    scrubber = PIIScrubber()
    state = {
        "accessibility_tree": {
            "name": "Member Login",
            "children": [
                {"name": "PIN:", "value": "4471", "type": "password"}
            ],
        },
        "spatial_map": [{"tag": "input", "type": "password", "name": "PIN:", "value": "4471"}]
    }

    sanitized = scrubber.scrub_state(state)
    assert sanitized["accessibility_tree"]["children"][0]["name"] == "PIN:"
    assert sanitized["spatial_map"][0]["name"] == "PIN:"
    assert sanitized["accessibility_tree"]["children"][0]["value"] == "[REDACTED_SECRET]"
