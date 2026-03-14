import voxera.skills.runner as runner_module
from voxera.models import PolicyApprovals, SkillManifest
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner


def _manifest(*, capabilities, risk="low"):
    return SkillManifest(
        id="test.skill",
        name="Test Skill",
        description="Test description",
        entrypoint="voxera_builtin_skills.system_status:run",
        capabilities=capabilities,
        risk=risk,
    )


def test_simulate_allow_low_risk():
    runner = SkillRunner(SkillRegistry())
    sim = runner.simulate(_manifest(capabilities=[]), args={}, policy=PolicyApprovals())

    assert sim.blocked is False
    assert sim.approvals_required == 0
    assert sim.steps[0].policy_decision == "allow"


def test_simulate_approval_required_for_ask_policy():
    policy = PolicyApprovals(system_settings="ask")
    runner = SkillRunner(SkillRegistry())
    sim = runner.simulate(
        _manifest(capabilities=["system.settings"], risk="medium"),
        args={"level": "30"},
        policy=policy,
    )

    assert sim.blocked is False
    assert sim.approvals_required == 1
    assert sim.steps[0].requires_approval is True
    assert sim.steps[0].policy_decision == "ask"


def test_simulate_blocked_when_policy_denies():
    policy = PolicyApprovals(installs="deny")
    runner = SkillRunner(SkillRegistry())
    sim = runner.simulate(
        _manifest(capabilities=["install.packages"], risk="high"),
        args={"pkg": "curl"},
        policy=policy,
    )

    assert sim.blocked is True
    assert sim.summary == "Blocked by policy"
    assert sim.steps[0].policy_decision == "deny"


def test_simulate_canonicalizes_open_app_args():
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    manifest = reg.get("system.open_app")

    sim = runner.simulate(manifest, args={"name": "terminal"}, policy=PolicyApprovals())
    assert sim.steps[0].args == {"name": "gnome-terminal"}


def test_simulate_canonicalizes_write_text_aliases():
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    manifest = reg.get("files.write_text")

    sim = runner.simulate(
        manifest,
        args={"path": "~/VoxeraOS/notes/test.txt", "content": "hello"},
        policy=PolicyApprovals(),
    )
    assert sim.steps[0].args["text"] == "hello"


def test_run_redacts_sensitive_args_in_audit_log(monkeypatch):
    reg = SkillRegistry()
    manifest = _manifest(capabilities=["state.read"])
    reg.load_entrypoint = lambda _mf: lambda **_kwargs: "ok"
    runner = SkillRunner(reg)

    events = []
    monkeypatch.setattr(runner_module, "log", lambda event: events.append(event))

    rr = runner.run(
        manifest,
        args={
            "command": ["curl", "-H", "Authorization: Bearer sk_live_123456789012345678901234"],
            "env": {"API_KEY": "super-secret-value-1234567890"},
            "note": "safe",
        },
        policy=PolicyApprovals(),
    )

    assert rr.ok is True
    start_event = next(event for event in events if event.get("event") == "skill_start")
    assert start_event["args"]["env"]["API_KEY"] == "REDACTED"
    assert start_event["args"]["command"][2] == "REDACTED"
    assert start_event["args"]["note"] == "safe"


def test_run_fail_closed_when_capability_metadata_missing(monkeypatch):
    reg = SkillRegistry()
    manifest = _manifest(capabilities=[])
    reg.load_entrypoint = lambda _mf: lambda **_kwargs: "should-not-run"
    runner = SkillRunner(reg)

    rr = runner.run(manifest, args={}, policy=PolicyApprovals())

    assert rr.ok is False
    assert rr.data["status"] == "blocked"
    assert rr.data["blocked_reason_class"] == "missing_capability_metadata"


def test_run_fail_closed_when_capability_metadata_malformed(monkeypatch):
    reg = SkillRegistry()
    manifest = _manifest(capabilities=["apps.open", "apps.open"])
    reg.load_entrypoint = lambda _mf: lambda **_kwargs: "should-not-run"
    runner = SkillRunner(reg)

    rr = runner.run(manifest, args={}, policy=PolicyApprovals())

    assert rr.ok is False
    assert rr.data["status"] == "blocked"
    assert rr.data["blocked_reason_class"] == "ambiguous_capability_metadata"


def test_run_fail_closed_when_capability_metadata_unknown(monkeypatch):
    reg = SkillRegistry()
    manifest = _manifest(capabilities=["unknown.capability"])
    reg.load_entrypoint = lambda _mf: lambda **_kwargs: "should-not-run"
    runner = SkillRunner(reg)

    rr = runner.run(manifest, args={}, policy=PolicyApprovals())

    assert rr.ok is False
    assert rr.data["status"] == "blocked"
    assert rr.data["blocked_reason_class"] == "unknown_capability_metadata"


def test_run_returns_pending_approval_with_capability_evidence(monkeypatch):
    reg = SkillRegistry()
    manifest = _manifest(capabilities=["system.settings"], risk="medium")
    reg.load_entrypoint = lambda _mf: lambda **_kwargs: "should-not-run"
    runner = SkillRunner(reg)

    def _pending(*_args, **_kwargs):
        return {"status": "pending"}

    rr = runner.run(
        manifest,
        args={},
        policy=PolicyApprovals(system_settings="ask"),
        require_approval_cb=_pending,
    )

    assert rr.ok is False
    assert rr.data["status"] == "pending_approval"
    assert rr.data["capabilities"] == ["system.settings"]
    assert rr.data["effect_classes"] == ["write"]


def test_run_fail_closed_on_runtime_network_boundary_mismatch():
    reg = SkillRegistry()
    manifest = SkillManifest(
        id="sandbox.exec",
        name="Sandbox Exec",
        description="Run one command in sandbox",
        entrypoint="voxera_builtin_skills.sandbox_exec:run",
        capabilities=["sandbox.exec"],
        needs_network=False,
    )
    reg.load_entrypoint = lambda _mf: lambda **_kwargs: "should-not-run"
    runner = SkillRunner(reg)

    rr = runner.run(
        manifest,
        args={"command": ["echo", "hello"], "network": True},
        policy=PolicyApprovals(),
    )

    assert rr.ok is False
    assert rr.data["status"] == "blocked"
    assert rr.data["blocked_reason_class"] == "capability_boundary_mismatch"
    payload = rr.data["skill_result"]["machine_payload"]["execution_capabilities"]
    assert payload["runtime_boundary_violation"]["boundary"] == "network"
