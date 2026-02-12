from voxera.models import SkillManifest, PolicyApprovals
from voxera.policy import decide

def test_policy_ask_on_unknown_cap():
    mf = SkillManifest(id="x", name="x", description="x", entrypoint="a:b", capabilities=["unknown.cap"], risk="low")
    d = decide(mf, PolicyApprovals())
    assert d.decision == "ask"

def test_policy_deny():
    pol = PolicyApprovals()
    pol.installs = "deny"
    mf = SkillManifest(id="i", name="i", description="i", entrypoint="a:b", capabilities=["install.packages"], risk="medium")
    d = decide(mf, pol)
    assert d.decision == "deny"
