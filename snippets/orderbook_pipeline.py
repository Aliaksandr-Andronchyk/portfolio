"""Мини-конвейер рыночных данных Coinbase Exchange: стакан + сделки -> SQLite -> агрегация.

Публичные REST-эндпоинты, ключи не нужны. Один файл, стандартная библиотека
(requests берётся, если он есть в окружении, иначе urllib).

    python3 snippets/orderbook_pipeline.py --products BTC-USD,ETH-USD
    python3 snippets/orderbook_pipeline.py --demo      # без сети, встроенные данные
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple

API = "https://api.exchange.coinbase.com"
DEPTH = 10  # сколько уровней с каждой стороны идёт в метрики

try:  # requests не обязателен, urllib закрывает тот же контракт
    import requests
except ImportError:  # pragma: no cover
    requests = None


class Level(NamedTuple):
    price: float
    size: float


@dataclass(frozen=True)
class BookSnapshot:
    """Нормализованный снимок стакана, единая форма для любой биржи."""

    product: str
    ts: str
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]

    @property
    def mid(self) -> float:
        return (self.bids[0].price + self.asks[0].price) / 2

    @property
    def spread_bps(self) -> float:
        return (self.asks[0].price - self.bids[0].price) / self.mid * 10_000

    @property
    def imbalance(self) -> float:
        """Дисбаланс объёма по DEPTH уровням: +1 весь объём в бидах, -1 в асках."""
        bid_vol = sum(level.size for level in self.bids[:DEPTH])
        ask_vol = sum(level.size for level in self.asks[:DEPTH])
        total = bid_vol + ask_vol
        return (bid_vol - ask_vol) / total if total else 0.0


class Trade(NamedTuple):
    product: str
    trade_id: int
    ts: str
    side: str
    price: float
    size: float


def get_json(path: str, params: dict) -> object:
    url = f"{API}{path}?" + "&".join(f"{k}={v}" for k, v in params.items())
    headers = {"User-Agent": "portfolio-pipeline"}
    if requests is not None:
        resp = requests.get(url, timeout=10, headers=headers)
        resp.raise_for_status()
        return resp.json()
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as resp:
        return json.load(resp)


def parse_book(product: str, payload: dict) -> BookSnapshot:
    def levels(raw):
        return tuple(Level(float(p), float(s)) for p, s, *_ in raw[:DEPTH])

    return BookSnapshot(
        product=product,
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        bids=levels(payload["bids"]),
        asks=levels(payload["asks"]),
    )


def parse_trades(product: str, payload: list) -> list[Trade]:
    return [
        Trade(product, int(t["trade_id"]), t["time"], t["side"], float(t["price"]), float(t["size"]))
        for t in payload
    ]


def fetch(product: str, trade_limit: int) -> tuple[BookSnapshot, list[Trade]]:
    book = parse_book(product, get_json(f"/products/{product}/book", {"level": 2}))
    trades = parse_trades(product, get_json(f"/products/{product}/trades", {"limit": trade_limit}))
    return book, trades


DEMO = {
    "BTC-USD": (
        {"bids": [["80120.10", "0.81", 3], ["80119.50", "1.20", 5]],
         "asks": [["80121.40", "0.35", 2], ["80122.00", "0.90", 4]]},
        [{"trade_id": 1, "time": "2026-09-18T14:00:00Z", "side": "buy", "price": "80120.73", "size": "0.0008"},
         {"trade_id": 2, "time": "2026-09-18T14:00:01Z", "side": "sell", "price": "80120.10", "size": "0.0021"}],
    ),
    "ETH-USD": (
        {"bids": [["3120.11", "12.4", 7], ["3119.80", "8.1", 3]],
         "asks": [["3120.55", "5.2", 4], ["3121.02", "9.9", 6]]},
        [{"trade_id": 3, "time": "2026-09-18T14:00:02Z", "side": "buy", "price": "3120.55", "size": "1.4"}],
    ),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS book_snapshots (
    id INTEGER PRIMARY KEY, product TEXT NOT NULL, ts TEXT NOT NULL,
    bid REAL, ask REAL, mid REAL, spread_bps REAL, imbalance REAL);
CREATE TABLE IF NOT EXISTS trades (
    product TEXT NOT NULL, trade_id INTEGER NOT NULL, ts TEXT NOT NULL,
    side TEXT, price REAL, size REAL, PRIMARY KEY (product, trade_id));
CREATE INDEX IF NOT EXISTS idx_book_product_ts ON book_snapshots(product, ts);
CREATE INDEX IF NOT EXISTS idx_trades_product_ts ON trades(product, ts);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def store(conn: sqlite3.Connection, book: BookSnapshot, trades: list[Trade]) -> None:
    conn.execute(
        "INSERT INTO book_snapshots (product, ts, bid, ask, mid, spread_bps, imbalance)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (book.product, book.ts, book.bids[0].price, book.asks[0].price,
         book.mid, book.spread_bps, book.imbalance),
    )
    conn.executemany(  # idempotent: повторный прогон не размножит сделки
        "INSERT OR IGNORE INTO trades (product, trade_id, ts, side, price, size)"
        " VALUES (?, ?, ?, ?, ?, ?)", trades,
    )
    conn.commit()


def aggregate(conn: sqlite3.Connection) -> list[dict]:
    """Сводка по продукту: средние метрики стакана плюс объём и VWAP сделок."""
    rows = conn.execute(
        "SELECT b.product, COUNT(*) AS snaps, AVG(b.mid), AVG(b.spread_bps), AVG(b.imbalance),"
        " (SELECT COALESCE(SUM(size), 0) FROM trades t WHERE t.product = b.product),"
        " (SELECT SUM(price * size) / SUM(size) FROM trades t WHERE t.product = b.product)"
        " FROM book_snapshots b GROUP BY b.product ORDER BY b.product"
    ).fetchall()
    keys = ("product", "snapshots", "mid", "spread_bps", "imbalance", "volume", "vwap")
    return [dict(zip(keys, row)) for row in rows]


def run(products, db: str, trade_limit: int, rounds: int, pause: float, demo: bool) -> list[dict]:
    conn = connect(db)
    try:
        for round_no in range(rounds):
            for product in products:
                if demo:
                    raw_book, raw_trades = DEMO.get(product, DEMO["BTC-USD"])
                    book, trades = parse_book(product, raw_book), parse_trades(product, raw_trades)
                else:
                    book, trades = fetch(product, trade_limit)
                store(conn, book, trades)
                print(f"{book.ts} {product:<10} mid={book.mid:>12.2f}"
                      f" spread={book.spread_bps:>6.2f}bps imbalance={book.imbalance:>+6.3f}"
                      f" trades={len(trades)}")
            if pause and round_no < rounds - 1:
                time.sleep(pause)
        return aggregate(conn)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Coinbase order book and trade pipeline")
    parser.add_argument("--products", default="BTC-USD,ETH-USD")
    parser.add_argument("--db", default="market_data.sqlite3")
    parser.add_argument("--trade-limit", type=int, default=50)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--demo", action="store_true", help="use built-in data, no network")
    args = parser.parse_args()

    products = [p.strip().upper() for p in args.products.split(",") if p.strip()]
    summary = run(products, args.db, args.trade_limit, args.rounds, args.pause, args.demo)
    print("\nsummary")
    for row in summary:
        print(f"  {row['product']:<10} snapshots={row['snapshots']} mid={row['mid']:.2f}"
              f" spread={row['spread_bps']:.2f}bps imbalance={row['imbalance']:+.3f}"
              f" volume={row['volume']:.6f} vwap={row['vwap']:.2f}")


if __name__ == "__main__":
    main()
