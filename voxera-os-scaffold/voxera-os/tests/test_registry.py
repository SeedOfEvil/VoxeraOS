from voxera.skills.registry import SkillRegistry


def test_discover_skills():
    reg = SkillRegistry()
    m = reg.discover()
    assert "system.status" in m
    assert "system.open_app" in m
