# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1"]
# ///
"""Pull all places of one brand from Overture Maps (public S3 bucket, no AWS account).

Matches the brand's Wikidata id, or any of the given name patterns (many places have no brand tag).
Resumable: each Overture Parquet file is queried in its own subprocess with a timeout, and its result is
saved under <out>.parts/ before moving on. Re-running skips files that are already done.

Usage: uv run scripts/pipeline/overture_brand.py <release> <wikidata_id> <out.csv> [name_pattern ...]
"""
import csv
import subprocess
import sys
import time
from pathlib import Path

import duckdb

PER_FILE_TIMEOUT_S = 900
RETRIES = 6


def connect():
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET http_retries=10; SET http_retry_wait_ms=2000; SET http_timeout=60000;")
    con.execute("CREATE SECRET (TYPE s3, PROVIDER config, REGION 'us-west-2');")
    return con


def where(wikidata: str, patterns: list[str]) -> str:
    pats = [p.replace("'", "''") for p in patterns]
    return f"brand.wikidata = '{wikidata}'" + "".join(f" OR names.primary ILIKE '{p}'" for p in pats)


def one_file(url: str, out: str, cond: str):
    tmp = out + ".tmp"
    connect().execute(f"COPY (SELECT bbox.xmin AS lng, bbox.ymin AS lat FROM read_parquet('{url}') WHERE {cond}) TO '{tmp}' (HEADER, DELIMITER ',');")
    Path(tmp).rename(out)


def main():
    if sys.argv[1] == "--one":
        one_file(sys.argv[2], sys.argv[3], sys.argv[4])
        return
    release, wikidata, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    cond = where(wikidata, sys.argv[4:])
    parts = Path(str(out) + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    files = [r[0] for r in connect().execute(f"SELECT file FROM glob('s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*.parquet') ORDER BY file").fetchall()]
    print(f"{len(files)} Overture files", flush=True)
    for i, url in enumerate(files):
        part = parts / f"{i:03d}.csv"
        if part.exists():
            continue
        for attempt in range(1, RETRIES + 1):
            t = time.time()
            try:
                subprocess.run([sys.executable, __file__, "--one", url, str(part), cond], check=True, timeout=PER_FILE_TIMEOUT_S, capture_output=True)
                print(f"file {i + 1}/{len(files)} done in {time.time() - t:.0f}s", flush=True)
                break
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                print(f"file {i + 1}/{len(files)} attempt {attempt} failed: {type(e).__name__}", flush=True)
        else:
            sys.exit(f"Gave up on {url}; re-run to resume.")
    rows = []
    for p in sorted(parts.glob("*.csv")):
        with open(p) as fh:
            rows.extend((r["lng"], r["lat"]) for r in csv.DictReader(fh))
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lng", "lat"])
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out}", flush=True)


if __name__ == "__main__":
    main()
