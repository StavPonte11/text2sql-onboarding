#!/usr/bin/env python3
"""
Export an evaluation run's questions with their golden and generated SQL and
the rows each one returned, from the text2sql backend's API.

Needs only Python 3.9+ (no packages), network access to the backend, and the
backend's export API key (its EVAL_EXPORT_API_KEY), passed as --api-key or in
the EVAL_EXPORT_API_KEY environment variable.

    export EVAL_EXPORT_API_KEY=...

    # the latest finished run of connection 6
    python export_eval_results.py --backend http://backend:8000 --connection-id 6

    # a specific run, also as a spreadsheet
    python export_eval_results.py --backend http://backend:8000 --run-id <run id> --csv run.csv

Writes JSON (default <run id>.json): the run, then one entry per question with
question, status, scores, golden_sql, generated_sql, executed_generated_sql,
golden_result and generated_result ({success, error, columns, rows, row_count,
truncated}). The CSV has one row per question, with each result as JSON text.

API used (also callable directly, with the header Authorization: Bearer <key>):
    GET /api/export/evaluations/runs?connection_id=<id>&status=completed
    GET /api/export/evaluations/runs/<run id>/results?offset=0&limit=10
        -> {..., "total", "next_offset", "results": [...]}; follow next_offset until null.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PAGE_SIZE = 10


API_KEY = ""


def get_json(url: str, retries: int = 3) -> object:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {API_KEY}"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries:
                body = e.read().decode(errors="replace")[:300]
                sys.exit(f"GET {url} failed: HTTP {e.code} {body}")
        except urllib.error.URLError as e:
            if attempt == retries:
                sys.exit(f"GET {url} failed: {e.reason}")
        time.sleep(3 * attempt)
    raise AssertionError("unreachable")


def latest_run_id(api: str, connection_id: int) -> str:
    query = urllib.parse.urlencode(
        {"connection_id": connection_id, "status": "completed", "limit": 1}
    )
    runs = get_json(f"{api}/evaluations/runs?{query}")
    if not runs:
        sys.exit(f"Connection {connection_id} has no finished evaluation run.")
    return runs[0]["id"]


def export_run(api: str, run_id: str) -> dict:
    offset: int | None = 0
    run: dict = {}
    results: list[dict] = []
    while offset is not None:
        page = get_json(f"{api}/evaluations/runs/{run_id}/results?offset={offset}&limit={PAGE_SIZE}")
        results.extend(page["results"])
        run = {k: v for k, v in page.items() if k not in ("results", "offset", "next_offset")}
        print(f"  {len(results)}/{page['total']} questions", file=sys.stderr)
        offset = page["next_offset"]
    return {**run, "results": results}


def write_csv(path: str, results: list[dict]) -> None:
    fields = [
        "question_id",
        "question",
        "status",
        "contains_accuracy",
        "time_shift",
        "golden_sql",
        "generated_sql",
        "golden_result",
        "generated_result",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in results:
            scores = r.get("scores") or {}
            writer.writerow(
                {
                    "question_id": r["question_id"],
                    "question": r["question"],
                    "status": r["status"],
                    "contains_accuracy": scores.get("contains_accuracy"),
                    "time_shift": scores.get("time_shift"),
                    "golden_sql": r["golden_sql"],
                    "generated_sql": r["generated_sql"],
                    "golden_result": json.dumps(r["golden_result"], ensure_ascii=False),
                    "generated_result": json.dumps(r["generated_result"], ensure_ascii=False),
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backend", required=True, help="Backend URL, e.g. http://localhost:8000")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--run-id", help="The evaluation run to export")
    which.add_argument("--connection-id", type=int, help="Export this connection's latest finished run")
    parser.add_argument("--out", help="JSON file to write (default: <run id>.json)")
    parser.add_argument("--csv", help="Also write a CSV with one row per question")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("EVAL_EXPORT_API_KEY"),
        help="The backend's EVAL_EXPORT_API_KEY (default: that environment variable)",
    )
    args = parser.parse_args()
    if not args.api_key:
        parser.error("pass --api-key or set EVAL_EXPORT_API_KEY")
    global API_KEY
    API_KEY = args.api_key

    api = args.backend.rstrip("/") + "/api/export"
    run_id = args.run_id or latest_run_id(api, args.connection_id)
    print(f"Exporting run {run_id}", file=sys.stderr)
    export = export_run(api, run_id)

    out = args.out or f"{run_id}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2, default=str)
    print(f"Wrote {out}", file=sys.stderr)
    if args.csv:
        write_csv(args.csv, export["results"])
        print(f"Wrote {args.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
