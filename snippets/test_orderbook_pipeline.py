"""Тест для orderbook_pipeline.py: aggregate() без сделок не должен ронять main().

Запуск без pytest: python3 test_orderbook_pipeline.py
"""
import os
import tempfile

import orderbook_pipeline as m


def test_aggregate_without_trades_gives_formattable_vwap():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "market_data.sqlite3")
        conn = m.connect(db)
        book = m.parse_book("BTC-USD", m.DEMO["BTC-USD"][0])
        m.store(conn, book, [])

        row = m.aggregate(conn)[0]

        # main() форматирует vwap через f"{row['vwap']:.2f}" – без сделок
        # подзапрос SUM(price*size)/SUM(size) даёт SQL NULL (None), format падает.
        assert isinstance(row["vwap"], float)


if __name__ == "__main__":
    test_aggregate_without_trades_gives_formattable_vwap()
    print("OK")
