#!/usr/bin/env python3
"""S-1.0 map author local-only read/validate/send utility.

The author's original batch schema has NOT yet been verified. This sender
accepts only the explicit bidguard.site-map-batch.v1 adapter contract, never
silently reinterprets unknown source files, and never deletes originals.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import site_map_ingest as maps


def within(path, root):
    path = path.resolve()
    root = root.resolve()
    if root != path and root not in path.parents:
        raise ValueError("evidence outside source root")
    return path


def prepare_batch(path, root):
    # Manifest is metadata; evidence bytes remain in the source computer until send.
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("batch manifest too large")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("protocol") != maps.PROTOCOL:
        raise ValueError("unmapped author schema; adapter review required")
    out = dict(obj)
    enriched = []
    for meta in obj.get("evidence", []):
        rel = maps.safe_path(meta["path"])
        candidates = (path.parent / rel, root / rel)
        src = next((p for p in candidates if p.is_file() and
                    within(p, root)), None)
        if src is None:
            raise ValueError("missing evidence file: " + rel)
        if src.stat().st_size > maps.MAX_IMAGE:
            raise ValueError("evidence exceeds size limit")
        raw = src.read_bytes()
        if maps.digest(raw) != meta.get("sha256"):
            raise ValueError("local evidence sha256 mismatch")
        enriched.append({"path": rel, "sha256": meta["sha256"],
                         "content_b64": __import__("base64").b64encode(raw).decode("ascii")})
    out["evidence"] = enriched
    manifest, expected, _ = maps.validate(out)
    return out, expected


def send(url, token, payload, expected, collector_name=None, collector_token=None):
    endpoint = url.rstrip("/") + "/api/v1/site-maps/batches"
    p = urlsplit(endpoint)
    if p.scheme != "https":
        raise ValueError("HTTPS endpoint required (use TLS reverse proxy)")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = Request(endpoint, data=raw, method="POST", headers={
        "Content-Type": "application/json",
        "X-Map-Token": token,
        "X-Collector-Name": collector_name or "",
        "X-Collector-Token": collector_token or "",
    })
    try:
        with urlopen(req, timeout=40) as resp:
            result = json.load(resp)
    except HTTPError as exc:
        raise RuntimeError("server rejected batch with HTTP " + str(exc.code)) from None
    except URLError as exc:
        raise RuntimeError("connection unavailable; original retained") from None
    if not result.get("ok") or result.get("status") != "stored" or result.get("sha256") != expected:
        raise RuntimeError("ACK/hash mismatch; original retained")
    return result


def write_ack(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".ack-", delete=False) as f:
        tmp = Path(f.name)
        json.dump({"site_id": result["site_id"], "batch_id": result["batch_id"],
                   "sha256": result["sha256"], "status": result["status"],
                   "backup_status": result.get("backup_status", "pending")},
                  f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv=None):
    p = argparse.ArgumentParser(description="Send S-1.0 screen map batches; never delete originals")
    p.add_argument("--root", default="/workspace/map-data")
    p.add_argument("--endpoint", default=os.environ.get("MAP_INGEST_URL", ""))
    p.add_argument("--state", help="ACK folder (default <root>/.send-state)")
    p.add_argument("--limit", type=int, default=1, help="max batches each run; default first 1")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    root = Path(args.root).resolve()
    batches = root / "batches"
    state = Path(args.state).resolve() if args.state else root / ".send-state"
    if not batches.is_dir():
        print("No batches folder; source computer not available", file=sys.stderr)
        return 2
    files = sorted(batches.glob("*.json"))
    if not files:
        print("No compatible JSON manifests; original author format must be mapped")
        return 2
    if args.limit < 1:
        print("limit must be positive", file=sys.stderr)
        return 2
    if not args.dry_run:
        token = os.environ.get("MAP_INGEST_TOKEN", "")
        collector_name = os.environ.get("BIDGUARD_COLLECTOR_NAME", "")
        collector_token = os.environ.get("BIDGUARD_COLLECTOR_TOKEN", "")
        if len(token) < 24 or not args.endpoint or not collector_name or len(collector_token) < 40:
            print("Set MAP_INGEST_URL, MAP_INGEST_TOKEN and paired BIDGUARD_COLLECTOR_NAME/TOKEN", file=sys.stderr)
            return 2
    sent = 0
    failed = 0
    for src in files:
        if sent >= args.limit:
            break
        try:
            payload, sha = prepare_batch(src, root)
            receipt_path = state / (src.name + ".ack.json")
            if receipt_path.is_file():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if receipt.get("sha256") == sha and receipt.get("status") == "stored":
                    print(src.name, "already ACKed; preserving original")
                    continue
            if args.dry_run:
                print(src.name, "validated; manifest_sha256=", sha)
                sent += 1
                continue
            ack = send(args.endpoint, token, payload, sha, collector_name, collector_token)
            write_ack(receipt_path, ack)
            print(src.name, "stored and ACKed:", sha, "(NAS/Drive backup not yet confirmed)")
            sent += 1
        except (ValueError, OSError, RuntimeError, KeyError, TypeError, json.JSONDecodeError) as exc:
            print(src.name, "BLOCKED:", str(exc)[:160], file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
