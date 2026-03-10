#!/usr/bin/env python3
from __future__ import annotations

import argparse

from voxera.openrouter_catalog import CATALOG_PATH, refresh_curated_catalog_from_live


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh curated OpenRouter catalog metadata from the live models endpoint."
    )
    parser.add_argument(
        "--catalog-path",
        default=str(CATALOG_PATH),
        help="Path to curated catalog JSON (default: src/voxera/data/openrouter_catalog.json)",
    )
    args = parser.parse_args()

    summary = refresh_curated_catalog_from_live(
        curated_path=__import__("pathlib").Path(args.catalog_path)
    )
    print(
        "Refreshed curated OpenRouter catalog: "
        f"total={summary['total']} matched_live={summary['matched']} missing_live={summary['missing']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
