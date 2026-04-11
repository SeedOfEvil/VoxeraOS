"""Lane modules extracted from ``voxera.vera_web.app``.

This package is deliberately small. It exists to hold the two lane
areas — automation and review — that grew large enough in ``app.py`` to
warrant a gentle, behavior-preserving decomposition.

``app.py`` remains the top-level orchestrator: it still owns lane order,
the canonical gating logic, and the final render. The modules in this
package expose narrow lane entry points so that lane-specific decision
logic can live next to its detectors, away from the dispatcher.

Only automation and review are extracted here. Older / stable lanes
(explicit submit, early-exit utility lanes, LLM orchestration) remain
inline in ``app.py`` — this package is intentionally not a generic lane
framework.

See:

* :mod:`voxera.vera_web.lanes.automation_lane`
* :mod:`voxera.vera_web.lanes.review_lane`
"""

from __future__ import annotations

__all__: list[str] = []
