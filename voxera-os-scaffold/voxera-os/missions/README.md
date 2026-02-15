# Missions (concept)

Voxera treats every request as a Mission Card:

Observe -> Suggest -> Simulate -> Approve -> Apply -> Verify -> Remember

This scaffold includes:
- skill runner
- policy + approvals
- audit trail (including `mission_approved` / `mission_denied`)
- built-in mission templates (`work_mode`, `focus_mode`, `daily_checkin`, `incident_mode`, `wrap_up`, `system_check`)
- cloud-assisted mission planning (`voxera missions plan "<goal>"`)

Cloud planning uses a deterministic fallback sequence (`primary` -> `fast` -> `fallback`), then hands execution back to the local mission runner so policy decisions, approvals, and audit logging remain enforced.
