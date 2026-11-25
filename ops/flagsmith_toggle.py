"""Flip a Flagsmith feature on/off via the Management API (same call NudgeBee makes).

    python ops/flagsmith_toggle.py rush_mode on
    python ops/flagsmith_toggle.py rush_mode off
    python ops/flagsmith_toggle.py rush_mode --show-id   # prints the feature-state id for NudgeBee

Env: FLAGSMITH_ADMIN_API_KEY (Organisation API key), FLAGSMITH_ENV_API_KEY (environment
client key, the one shown under SDK Keys), optional FLAGSMITH_ADMIN_URL.
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

BASE = os.getenv("FLAGSMITH_ADMIN_URL", "https://api.flagsmith.com/api/v1").rstrip("/")


def _client() -> tuple[httpx.Client, str]:
    key, env = os.getenv("FLAGSMITH_ADMIN_API_KEY"), os.getenv("FLAGSMITH_ENV_API_KEY")
    if not key or not env:
        sys.exit("Set FLAGSMITH_ADMIN_API_KEY and FLAGSMITH_ENV_API_KEY")
    scheme = os.getenv("FLAGSMITH_ADMIN_AUTH_SCHEME", "Api-Key")
    return httpx.Client(base_url=BASE, headers={"Authorization": f"{scheme} {key}"}, timeout=15), env


def feature_state_id(http: httpx.Client, env: str, feature: str) -> int:
    r = http.get(f"/environments/{env}/featurestates/", params={"feature_name": feature})
    r.raise_for_status()
    results = r.json().get("results", [])
    # Environment default (not segment / identity overrides)
    for fs in results:
        if not fs.get("feature_segment") and not fs.get("identity"):
            return fs["id"]
    sys.exit(f"Feature '{feature}' not found in environment")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("feature")
    p.add_argument("state", nargs="?", choices=["on", "off"])
    p.add_argument("--show-id", action="store_true")
    a = p.parse_args()

    http, env = _client()
    fs_id = feature_state_id(http, env, a.feature)
    if a.show_id or not a.state:
        print(f"PATCH {BASE}/environments/{env}/featurestates/{fs_id}/")
        return
    r = http.patch(f"/environments/{env}/featurestates/{fs_id}/", json={"enabled": a.state == "on"})
    r.raise_for_status()
    print(f"{a.feature} -> {a.state} (feature state {fs_id})")


if __name__ == "__main__":
    main()
