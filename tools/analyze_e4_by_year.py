import json
import zipfile
from collections import defaultdict
from io import StringIO
from pathlib import Path
import chess.pgn

zip_path = Path("../data/pabloGames-20260224T115620Z-1-001.zip")

e4_by_year = defaultdict(int)
total_white_by_year = defaultdict(int)

with zipfile.ZipFile(zip_path, "r") as zf:
    for name in zf.namelist():
        if not name.lower().endswith((".json", ".txt")):
            continue

        data = json.loads(zf.read(name).decode("utf-8", errors="replace"))

        for g in data.get("games", []):
            pgn_text = g.get("pgn")
            if not pgn_text:
                continue

            game = chess.pgn.read_game(StringIO(pgn_text))
            if not game:
                continue

            headers = game.headers
            if headers.get("White") != "capo298":
                continue

            date = headers.get("Date", "")
            if not date or "." not in date:
                continue

            year = date.split(".")[0]
            total_white_by_year[year] += 1

            board = game.board()
            first_move = next(iter(game.mainline_moves()), None)

            if first_move and first_move.uci() == "e2e4":
                e4_by_year[year] += 1

print("\n=== e4 usage by year ===\n")

for year in sorted(total_white_by_year):
    total = total_white_by_year[year]
    e4 = e4_by_year[year]
    pct = (e4 / total * 100) if total else 0
    print(f"{year}: e4 = {e4}/{total} ({pct:.1f}%)")