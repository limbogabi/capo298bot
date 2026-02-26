import argparse
import json
import zipfile
from collections import defaultdict
from io import StringIO
from pathlib import Path

import chess
import chess.pgn
from tqdm import tqdm


def normalize_fen(fen: str) -> str:
    parts = fen.split()
    if len(parts) < 4:
        return fen.strip()
    return " ".join(parts[:4])


def iter_chesscom_games_from_zip(zip_path: Path):
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = [n for n in zf.namelist() if n.lower().endswith((".json", ".txt"))]
        for name in names:
            raw = zf.read(name)
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            games = data.get("games")
            if not isinstance(games, list):
                continue
            for g in games:
                if isinstance(g, dict) and isinstance(g.get("pgn"), str):
                    yield g


def pgn_to_game(pgn_text: str):
    try:
        return chess.pgn.read_game(StringIO(pgn_text))
    except Exception:
        return None


def build_opening_book(zip_path: Path, player: str, max_ply_cap: int):
    # fen -> move_uci -> count
    move_counts = defaultdict(lambda: defaultdict(int))
    total_positions = defaultdict(int)

    games = list(iter_chesscom_games_from_zip(zip_path))
    if not games:
        raise SystemExit("No games found in zip (expected Chess.com JSON exports with 'games' array).")

    skipped = 0
    used = 0

    for g in tqdm(games, desc="Processing games"):
        pgn_text = g.get("pgn", "")
        if not pgn_text.strip():
            skipped += 1
            continue

        game = pgn_to_game(pgn_text)
        if game is None:
            skipped += 1
            continue

        headers = game.headers
        white = headers.get("White", "")
        black = headers.get("Black", "")

        if white != player and black != player:
            # not one of the player's games
            continue

        player_is_white = (white == player)

        board = game.board()
        ply = 0

        for move in game.mainline_moves():
            if ply >= max_ply_cap:
                break

            # Only record moves where it's the player's turn to move
            if board.turn == chess.WHITE:
                if player_is_white:
                    fen_key = normalize_fen(board.fen())
                    uci = move.uci()
                    total_positions[fen_key] += 1
                    move_counts[fen_key][uci] += 1
            else:
                if not player_is_white:
                    fen_key = normalize_fen(board.fen())
                    uci = move.uci()
                    total_positions[fen_key] += 1
                    move_counts[fen_key][uci] += 1

            board.push(move)
            ply += 1

        used += 1

    positions_out = {}
    for fen_key, moves_dict in move_counts.items():
        total = total_positions[fen_key]
        positions_out[fen_key] = {
            "total": total,
            "moves": dict(sorted(moves_dict.items(), key=lambda kv: kv[1], reverse=True)),
        }

    meta = {
        "player": player,
        "source": "Chess.com exports (zip)",
        "note": "Counts only moves where it's player's turn (fixes opponent-move contamination)",
        "max_ply_cap": max_ply_cap,
        "fen_format": "pieces side castling ep (no half/fullmove)",
    }

    return {"meta": meta, "positions": positions_out, "stats": {"games_seen": len(games), "games_used": used, "games_skipped": skipped}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, help="Zip file with Chess.com exports")
    ap.add_argument("--player", default="capo298", help="Username to model")
    ap.add_argument("--max-ply-cap", type=int, default=20, help="Max ply to record (safety cap)")
    ap.add_argument("--out", default="../data/capo298_opening_book_turnonly.json", help="Output JSON")
    args = ap.parse_args()

    zip_path = Path(args.zip)
    if not zip_path.exists():
        raise SystemExit(f"Zip not found: {zip_path}")

    book = build_opening_book(zip_path, args.player, args.max_ply_cap)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote: {out_path.resolve()}")
    print("Unique positions:", len(book["positions"]))
    print("Stats:", book["stats"])


if __name__ == "__main__":
    main()