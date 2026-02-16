from voxera.models import PolicyApprovals, SkillManifest
from voxera.policy import decide


def test_policy_ask_on_unknown_cap():
    mf = SkillManifest(
        id="x",
        name="x",
        description="x",
        entrypoint="a:b",
        capabilities=["unknown.cap"],
        risk="low",
    )
    d = decide(mf, PolicyApprovals())
    assert d.decision == "ask"


def test_policy_deny():
    pol = PolicyApprovals()
    pol.installs = "deny"
    mf = SkillManifest(
        id="i",
        name="i",
        description="i",
        entrypoint="a:b",
        capabilities=["install.packages"],
        risk="medium",
    )
    d = decide(mf, pol)
    assert d.decision == "deny"


def test_policy_sandbox_network_request_requires_approval():
    mf = SkillManifest(
        id="sandbox.exec",
        name="Sandbox",
        description="x",
        entrypoint="a:b",
        exec_mode="sandbox",
    )
    d = decide(mf, PolicyApprovals(), args={"network": True})
    assert d.decision == "ask"
    assert "runs in SANDBOX" in d.reason
