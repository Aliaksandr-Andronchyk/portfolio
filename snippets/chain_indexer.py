"""Resumable blockchain indexer: Aptos REST -> SQLite.

Тянет блоки с публичной ноды, разбирает транзакции, пишет в SQLite
с индексами, догоняет с последней сохранённой высоты и считает статистику.
Только стандартная библиотека.

    python3 chain_indexer.py --limit 25
    python3 chain_indexer.py --limit 25 --stats
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request

DEFAULT_RPC = "https://fullnode.mainnet.aptoslabs.com/v1"
DEFAULT_DB = os.path.join(tempfile.gettempdir(), "chain_index.db")
USER_AGENT = "chain-indexer/1.0"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    height          INTEGER PRIMARY KEY,
    block_hash      TEXT NOT NULL,
    block_timestamp INTEGER NOT NULL,
    first_version   INTEGER NOT NULL,
    last_version    INTEGER NOT NULL,
    tx_count        INTEGER NOT NULL,
    indexed_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS txs (
    version   INTEGER PRIMARY KEY,
    height    INTEGER NOT NULL,
    tx_type   TEXT NOT NULL,
    success   INTEGER NOT NULL,
    vm_status TEXT,
    gas_used  INTEGER NOT NULL DEFAULT 0,
    sender    TEXT,
    entry_fn  TEXT
);
CREATE INDEX IF NOT EXISTS idx_txs_height ON txs (height);
CREATE INDEX IF NOT EXISTS idx_txs_type   ON txs (tx_type);
CREATE INDEX IF NOT EXISTS idx_txs_sender ON txs (sender);
CREATE INDEX IF NOT EXISTS idx_blocks_ts  ON blocks (block_timestamp);
"""


class RpcError(RuntimeError):
    """Нода ответила так, что повторять бессмысленно."""


def rpc_get(rpc, path, attempts=5, timeout=20.0):
    """GET с повторами и экспоненциальным backoff + jitter."""
    url = rpc.rstrip("/") + path
    delay = 0.5
    last = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            last = "HTTP %s %s" % (exc.code, body)
            if exc.code not in RETRY_STATUS:
                raise RpcError(last) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
        if attempt < attempts:
            sleep_for = delay + random.uniform(0, delay / 2)
            print("  retry %d/%d in %.1fs - %s" % (attempt, attempts, sleep_for, last),
                  file=sys.stderr)
            time.sleep(sleep_for)
            delay = min(delay * 2, 8.0)
    raise RpcError("giving up after %d attempts - %s" % (attempts, last))


def open_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def last_indexed_height(conn):
    row = conn.execute("SELECT MAX(height) FROM blocks").fetchone()
    return row[0]


def parse_tx(tx, height):
    """Достаёт из транзакции ровно те поля, что нужны индексу."""
    payload = tx.get("payload") or {}
    return (
        int(tx["version"]),
        height,
        tx.get("type", "unknown"),
        1 if tx.get("success") else 0,
        tx.get("vm_status"),
        int(tx.get("gas_used") or 0),
        tx.get("sender"),
        payload.get("function"),
    )


def store_block(conn, block):
    height = int(block["block_height"])
    txs = [parse_tx(tx, height) for tx in block.get("transactions") or []]
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO blocks VALUES (?,?,?,?,?,?,?)",
            (height, block["block_hash"], int(block["block_timestamp"]),
             int(block["first_version"]), int(block["last_version"]),
             len(txs), int(time.time())),
        )
        conn.executemany("INSERT OR REPLACE INTO txs VALUES (?,?,?,?,?,?,?,?)", txs)
    return height, len(txs)


def index_range(conn, rpc, start_height, limit, pause=0.15):
    """Индексирует limit блоков начиная со start_height. Возвращает счётчики."""
    blocks_done = txs_done = 0
    height = start_height
    while blocks_done < limit:
        try:
            block = rpc_get(rpc, "/blocks/by_height/%d?with_transactions=true" % height)
        except RpcError as exc:
            print("  block %d skipped - %s" % (height, exc), file=sys.stderr)
            height += 1
            continue
        h, n = store_block(conn, block)
        blocks_done += 1
        txs_done += n
        print("  block %d - %d tx" % (h, n))
        height += 1
        time.sleep(pause)
    return blocks_done, txs_done


def print_stats(conn):
    blocks, txs, gas = conn.execute(
        "SELECT (SELECT COUNT(*) FROM blocks), (SELECT COUNT(*) FROM txs),"
        " (SELECT COALESCE(SUM(gas_used), 0) FROM txs)").fetchone()
    lo, hi = conn.execute("SELECT MIN(height), MAX(height) FROM blocks").fetchone()
    print("blocks=%s heights=%s..%s txs=%s tx/block=%.2f gas=%s"
          % (blocks, lo, hi, txs, (txs / blocks if blocks else 0), gas))
    for tx_type, cnt, ok in conn.execute(
            "SELECT tx_type, COUNT(*), SUM(success) FROM txs"
            " GROUP BY tx_type ORDER BY 2 DESC"):
        print("  %-28s %5d  ok=%d" % (tx_type, cnt, ok))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Resumable Aptos block indexer")
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--limit", type=int, default=10, help="сколько блоков за проход")
    ap.add_argument("--from-height", type=int, help="начать с этой высоты, игнорируя resume")
    ap.add_argument("--stats", action="store_true", help="только статистика, без запросов")
    args = ap.parse_args(argv)

    conn = open_db(args.db)
    if args.stats:
        print_stats(conn)
        return 0

    info = rpc_get(args.rpc, "")
    tip = int(info["block_height"])
    resume = last_indexed_height(conn)
    if args.from_height is not None:
        start = args.from_height
    elif resume is not None:
        start = min(resume + 1, tip)
        print("resume from %d (tip %d, lag %d)" % (start, tip, tip - start))
    else:
        start = max(tip - args.limit + 1, 0)
    print("chain_id=%s tip=%d indexing %d blocks from %d -> %s"
          % (info.get("chain_id"), tip, args.limit, start, args.db))

    t0 = time.time()
    blocks_done, txs_done = index_range(conn, args.rpc, start, args.limit)
    print("done: %d blocks, %d txs in %.1fs" % (blocks_done, txs_done, time.time() - t0))
    print_stats(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
