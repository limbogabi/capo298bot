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
    """
    Normalize FEN by dropping halfmove/fullmove counters so transpositions match better.
    Keep: pieces, side to move, castling rights, en passant square.
    """
    parts = fen.split()
    if len(parts) < 4:
        return fen.strip()
    return " ".join(parts[:4])


def iter_chesscom_games_from_zip(zip_path: Path):
    """
    Yield each game dict from Chess.com monthly export JSON files inside a zip.
    Expected file content: {"games": [ ...game objects... ]}
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Read only likely JSON-ish files
        names = [n for n in zf.namelist() if n.lower().endswith((".json", ".txt"))]
        for name in names:
            with zf.open(name) as f:
                raw = f.read()
            # Try utf-8 with fallback
            text = raw.decode("utf-8", errors="replace").strip()

            if not text:
                continue

            # Some exports may have BOM or extra whitespace
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # If it isn't valid JSON, skip
                continue

            games = data.get("games")
            if not isinstance(games, list):
                continue

            for g in games:
                if isinstance(g, dict) and "pgn" in g:
                    yield g


def pgn_to_game(pgn_text: str):
    """
    Parse PGN string into a python-chess Game object.
    """
    pgn_io = StringIO(pgn_text)
    try:
        game = chess.pgn.read_game(pgn_io)
        return game
    except Exception:
        return None


def build_opening_book(
    zip_path: Path,
    max_ply_cap: int,
    min_position_count: int,
    min_top_move_ratio: float,
):
    """
    Returns a dict structured for capo298_opening_book.json:
      {
        "meta": {...},
        "positions": {
           "<normalized fen>": {"total": N, "moves": {"e2e4": c, ...}}
        }
      }
    """
    move_counts = defaultdict(lambda: defaultdict(int))  # fen -> move_uci -> count
    total_positions = defaultdict(int)  # fen -> total seen

    games_iter = list(iter_chesscom_games_from_zip(zip_path))
    if not games_iter:
        raise SystemExit(
            "No games found in zip. Make sure the zip contains Chess.com JSON export files with a top-level 'games' array."
        )

    for g in tqdm(games_iter, desc="Processing games"):
        pgn_text = g.get("pgn")
        if not isinstance(pgn_text, str) or not pgn_text.strip():
            continue

        game = pgn_to_game(pgn_text)
        if game is None:
            continue

        board = game.board()
        ply = 0

        for move in game.mainline_moves():
            if ply >= max_ply_cap:
                break

            fen_key = normalize_fen(board.fen())
            uci = move.uci()

            total_positions[fen_key] += 1
            move_counts[fen_key][uci] += 1

            board.push(move)
            ply += 1

    # Build final output structure
    positions_out = {}
    for fen_key, moves_dict in move_counts.items():
        total = total_positions[fen_key]
        # Convert nested defaultdict to normal dict
        positions_out[fen_key] = {
            "total": total,
            "moves": dict(sorted(moves_dict.items(), key=lambda kv: kv[1], reverse=True)),
        }

    meta = {
        "player": "capo298",
        "source": "Chess.com exports (zip)",
        "adaptive": True,
        "min_position_count": min_position_count,
        "min_top_move_ratio": min_top_move_ratio,
        "max_ply_cap": max_ply_cap,
        "fen_format": "pieces side castling ep (no half/fullmove)",
    }

    return {"meta": meta, "positions": positions_out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, help="Path to zip with Chess.com export files")
    ap.add_argument("--out", default="capo298_opening_book.json", help="Output JSON path")
    ap.add_argument("--max-ply-cap", type=int, default=20, help="Maximum ply to record (safety cap)")
    ap.add_argument("--min-position-count", type=int, default=8, help="Adaptive: minimum occurrences to stay in book")
    ap.add_argument("--min-top-move-ratio", type=float, default=0.55, help="Adaptive: top move frequency threshold")
    args = ap.parse_args()

    zip_path = Path(args.zip)
    if not zip_path.exists():
        raise SystemExit(f"Zip not found: {zip_path}")

    book = build_opening_book(
        zip_path=zip_path,
        max_ply_cap=args.max_ply_cap,
        min_position_count=args.min_position_count,
        min_top_move_ratio=args.min_top_move_ratio,
    )

    out_path = Path(args.out)
    out_path.write_text(json.dumps(book, ensure_ascii=False, indent=2))
    print(f"\nWrote opening book to: {out_path.resolve()}")
    print(f"Unique positions: {len(book['positions'])}")


if __name__ == "__main__":
    main()