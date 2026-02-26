import json
import os
import random
from pathlib import Path
from typing import Optional, Dict, Any

import chess
from fastapi import FastAPI
from pydantic import BaseModel


def normalize_fen(fen: str) -> str:
    parts = fen.split()
    if len(parts) < 4:
        return fen.strip()
    return " ".join(parts[:4])


def weighted_choice(moves: Dict[str, int]) -> str:
    # moves: {uci: count}
    items = list(moves.items())
    total = sum(c for _, c in items)
    r = random.randint(1, total)
    s = 0
    for uci, c in items:
        s += c
        if r <= s:
            return uci
    return items[0][0]


class MoveRequest(BaseModel):
    fen: str
    ply: int = 0
    mode: str = "capo298bot"


class MoveResponse(BaseModel):
    move: Optional[str]
    source: str
    confidence: float


app = FastAPI()

BOOK_PATH = Path(os.getenv("BOOK_PATH", "capo298_opening_book.json"))
PROFILE_PATH = Path(os.getenv("PROFILE_PATH", "capo298bot_profile.json"))

book: Dict[str, Any] = {}
positions: Dict[str, Any] = {}
meta: Dict[str, Any] = {}
profile: Dict[str, Any] = {}


@app.on_event("startup")
def load_artifacts():
    global book, positions, meta, profile
    if BOOK_PATH.exists():
        book = json.load(open(BOOK_PATH, "r", encoding="utf-8"))
        meta = book.get("meta", {})
        positions = book.get("positions", {})
        print(f"[move-service] Loaded book: {BOOK_PATH} positions={len(positions)}")
    else:
        print(f"[move-service] Book not found: {BOOK_PATH}")

    if PROFILE_PATH.exists():
        profile = json.load(open(PROFILE_PATH, "r", encoding="utf-8"))
        print(f"[move-service] Loaded profile: {PROFILE_PATH}")
    else:
        print(f"[move-service] Profile not found: {PROFILE_PATH}")


@app.post("/move", response_model=MoveResponse)
def get_move(req: MoveRequest):
    # Validate FEN is legal-ish
    try:
        board = chess.Board(req.fen)
    except Exception:
        return MoveResponse(move=None, source="invalid_fen", confidence=0.0)

    fen_key = normalize_fen(board.fen())
    entry = positions.get(fen_key)
    if not entry:
        return MoveResponse(move=None, source="no_book_hit", confidence=0.0)

    total = int(entry.get("total", 0))
    moves = entry.get("moves", {})
    if not moves or total <= 0:
        return MoveResponse(move=None, source="empty_entry", confidence=0.0)

    # Adaptive thresholds (from book meta; fallback to sane defaults)
    min_position_count = int(meta.get("min_position_count", 8))
    min_top_move_ratio = float(meta.get("min_top_move_ratio", 0.55))
    max_ply_cap = int(meta.get("max_ply_cap", 20))

    if req.ply >= max_ply_cap:
        return MoveResponse(move=None, source="ply_cap", confidence=0.0)

    # Top move ratio
    top_move_count = max(moves.values())
    top_ratio = top_move_count / float(total)

    if total < min_position_count:
        return MoveResponse(move=None, source="below_min_count", confidence=top_ratio)

    if top_ratio < min_top_move_ratio:
        return MoveResponse(move=None, source="low_confidence", confidence=top_ratio)

    # Force deterministic first move from start position (optional)
    # STARTPOS = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
    # if fen_key == STARTPOS:
    #     chosen = max(moves.items(), key=lambda kv: kv[1])[0]
    # else:
    #     chosen = weighted_choice(moves)

    # Choose a move weighted by frequency
    chosen = weighted_choice(moves)

    # Sanity: ensure chosen move is legal in this position; if not, try others
    legal_uci = {m.uci() for m in board.legal_moves}
    if chosen not in legal_uci:
        # try highest-frequency legal move
        for uci, _ in sorted(moves.items(), key=lambda kv: kv[1], reverse=True):
            if uci in legal_uci:
                chosen = uci
                break
        else:
            return MoveResponse(move=None, source="no_legal_book_move", confidence=top_ratio)

    return MoveResponse(move=chosen, source="opening_book", confidence=top_ratio)


@app.get("/health")
def health():
    return {
        "ok": True,
        "book_loaded": bool(positions),
        "positions": len(positions),
        "book_path": str(BOOK_PATH),
        "profile_path": str(PROFILE_PATH),
    }