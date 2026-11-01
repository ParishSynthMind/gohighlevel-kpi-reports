"""Put clearly fake test leads into a GHL sandbox / trial sub-account, and take them out again.

Needs a SEPARATE, short-lived Private Integration token with contacts.write and
opportunities.write (the reporting job itself stays read-only). Every record created
is written to a manifest so --delete removes exactly those records and nothing else.

  python tools/seed_sandbox.py --count 25 --pipeline-id <id>   # create
  python tools/seed_sandbox.py --delete                         # remove everything in the manifest

Env: GHL_SEED_TOKEN, GHL_LOCATION_ID. Manifest: out/seed-manifest.json (git-ignored).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ghl_kpi.client import GHLClient, GHLError  # noqa: E402

MANIFEST = Path("out/seed-manifest.json")
TAG = "kpi-demo-seed"
SOURCES = ["Facebook Ads", "Google Ads", "Website Form", "Referral", "Instagram"]


def load_manifest():
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"contacts": [], "opportunities": []}


def save_manifest(m):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1))


def create(c: GHLClient, count: int, pipeline_id: str, seed: int):
    rng = random.Random(seed)
    pipes = {p["id"]: p for p in c.pipelines()}
    if pipeline_id not in pipes:
        sys.exit(f"pipeline {pipeline_id} not found; available: {[(p['id'], p['name']) for p in pipes.values()]}")
    stages = sorted(pipes[pipeline_id]["stages"], key=lambda s: s.get("position", 0))
    m = load_manifest()
    start = len(m["contacts"])
    for i in range(start + 1, start + count + 1):
        source = rng.choice(SOURCES)
        body = {"locationId": c.location_id, "firstName": "Test", "lastName": f"Lead {i:02d}",
                "email": f"test.lead{i:02d}@example.com", "source": source, "tags": [TAG]}
        contact = c._request("POST", "/contacts/", json=body)["contact"]
        m["contacts"].append({"id": contact["id"], "name": f"Test Lead {i:02d}", "source": source})
        save_manifest(m)  # save after every record so a crash never leaves untracked data
        if rng.random() < 0.8:
            stage = stages[min(int(rng.random() ** 1.5 * len(stages)), len(stages) - 1)]
            status = rng.choices(["open", "won", "lost"], [0.7, 0.15, 0.15])[0]
            opp = c._request("POST", "/opportunities/", json={
                "locationId": c.location_id, "pipelineId": pipeline_id, "pipelineStageId": stage["id"],
                "name": f"Test Lead {i:02d} - demo deal", "status": status, "contactId": contact["id"],
                "monetaryValue": rng.choice([1500, 2500, 5000, 7500]), "source": source})["opportunity"]
            m["opportunities"].append({"id": opp["id"], "contact_id": contact["id"], "stage": stage["name"], "status": status})
            save_manifest(m)
        print(f"created Test Lead {i:02d} ({source})")
        time.sleep(0.15)  # stay far below the 100 requests / 10 s burst limit
    print(f"manifest: {MANIFEST} ({len(m['contacts'])} contacts, {len(m['opportunities'])} opportunities)")


def delete(c: GHLClient):
    m = load_manifest()
    for kind, path in (("opportunities", "/opportunities/{}"), ("contacts", "/contacts/{}")):
        keep = []
        for rec in m[kind]:
            try:
                c._request("DELETE", path.format(rec["id"]))
                print(f"deleted {kind[:-1]} {rec['id']}")
            except GHLError as e:
                if e.status == 404:
                    print(f"already gone: {rec['id']}")
                else:
                    print(f"FAILED to delete {rec['id']}: {e}")
                    keep.append(rec)
        m[kind] = keep
        save_manifest(m)
    print("left in manifest:", {k: len(v) for k, v in m.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=25)
    ap.add_argument("--pipeline-id")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--delete", action="store_true")
    a = ap.parse_args()
    c = GHLClient(os.environ.get("GHL_SEED_TOKEN", ""), os.environ.get("GHL_LOCATION_ID", ""))
    if a.delete:
        delete(c)
    else:
        if not a.pipeline_id:
            sys.exit("--pipeline-id is required")
        if a.count > 25:
            sys.exit("refusing to create more than 25 test leads")
        create(c, a.count, a.pipeline_id, a.seed)


if __name__ == "__main__":
    main()
