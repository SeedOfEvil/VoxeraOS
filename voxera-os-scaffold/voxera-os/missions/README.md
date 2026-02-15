# Missions (concept)

Voxera treats every request as a Mission Card:

Observe -> Suggest -> Simulate -> Approve -> Apply -> Verify -> Remember

This scaffold includes:
- skill runner
- policy + approvals
- audit trail
- built-in mission templates (`work_mode`, `system_check`)
- cloud-assisted mission planning (`voxera missions plan "<goal>"`)

Cloud planning uses the configured `primary` brain provider to draft steps, then hands execution back to the local mission runner so policy decisions, approvals, and audit logging remain enforced.
