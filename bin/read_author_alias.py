#!/usr/bin/env python3

import yaml
from pathlib import Path


def main():
    # Path to _config.yml in repo root
    config_path = Path(__file__).resolve().parents[1] / "_config.yml"

    if not config_path.exists():
        raise FileNotFoundError(f"Could not find {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    first = config.get("first_name", "")
    middle = config.get("middle_name", "")
    last = config.get("last_name", "")

    # Collect initials:
    # - first letter of first name
    # - first letter of middle name (if present)
    # - first letter of each last-name component
    initials = []

    if first:
        initials.append(first.strip()[0])

    if middle:
        initials.append(middle.strip()[0])

    for part in last.split():
        if part:
            initials.append(part[0])

    alias = "".join(initials).upper()
    print(alias)


if __name__ == "__main__":
    main()
