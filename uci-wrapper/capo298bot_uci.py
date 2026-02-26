import os
import sys
import json
import time
import subprocess
import threading
import requests

MOVE_SERVICE_URL = os.getenv("MOVE_SERVICE_URL", "http://localhost:8081/move")
STOCKFISH_PATH = r"C:\stockfish\stockfish-windows-x86-64-avx2.exe"

DEFAULT_MOVETIME_MS = int(os.getenv("STOCKFISH_MOVETIME_MS", "250"))
DEFAULT_MULTIPV = int(os.getenv("STOCKFISH_MULTIPV", "5"))

# UCI wrapper state
current_fen = "startpos"
current_moves_uci = []  # list of moves since startpos


def log(msg: str):
    # comment out if you want totally quiet
    print(f"info string {msg}", flush=True)


class StockfishUCI:
    def __init__(self, path: str):
        self.proc = subprocess.Popen(
            [path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.lock = threading.Lock()
        self._init()

    def _send(self, cmd: str):
        assert self.proc.stdin is not None
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def _read_until(self, prefix: str, timeout_s: float = 5.0):
        assert self.proc.stdout is not None
        start = time.time()
        lines = []
        while True:
            if time.time() - start > timeout_s:
                return lines, None
            line = self.proc.stdout.readline()
            if not line:
                return lines, None
            line = line.strip()
            lines.append(line)
            if line.startswith(prefix):
                return lines, line

    def _init(self):
        with self.lock:
            self._send("uci")
            self._read_until("uciok", timeout_s=10)
            self._send(f"setoption name MultiPV value {DEFAULT_MULTIPV}")
            self._send("isready")
            self._read_until("readyok", timeout_s=10)

    def bestmove(self, fen: str, movetime_ms: int = DEFAULT_MOVETIME_MS) -> str:
        with self.lock:
            self._send("position fen " + fen)
            self._send(f"go movetime {movetime_ms}")
            _, line = self._read_until("bestmove", timeout_s=20)
            if not line:
                return "0000"
            # bestmove e2e4 ponder ...
            parts = line.split()
            return parts[1] if len(parts) >= 2 else "0000"

    def quit(self):
        with self.lock:
            try:
                self._send("quit")
            except Exception:
                pass
        try:
            self.proc.terminate()
        except Exception:
            pass


def fen_from_position_cmd(cmd: str):
    """
    Supports:
      position startpos moves e2e4 e7e5 ...
      position fen <fen...> moves ...
    """
    global current_fen, current_moves_uci

    parts = cmd.split()
    if len(parts) < 2:
        return

    if parts[1] == "startpos":
        current_fen = "startpos"
        current_moves_uci = []
        if "moves" in parts:
            idx = parts.index("moves")
            current_moves_uci = parts[idx + 1 :]
        return

    if parts[1] == "fen":
        # fen is 6 fields, but lichess-bot will pass a full fen; we take until "moves" if present
        try:
            idx_moves = parts.index("moves")
            fen = " ".join(parts[2:idx_moves])
            moves = parts[idx_moves + 1 :]
        except ValueError:
            fen = " ".join(parts[2:])
            moves = []
        current_fen = fen
        current_moves_uci = moves
        return


def ply_from_moves(moves):
    return len(moves)


def request_book_move(fen: str, ply: int):
    try:
        r = requests.post(
            MOVE_SERVICE_URL,
            json={"fen": fen, "ply": ply, "mode": "capo298bot"},
            timeout=1.5,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("move"), data.get("source"), float(data.get("confidence", 0.0))
    except Exception as e:
        return None, "move_service_error", 0.0


def main():
    global current_fen, current_moves_uci

    sf = StockfishUCI(STOCKFISH_PATH)

    # Basic UCI handshake
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()

        if line == "uci":
            print("id name capo298bot-uci", flush=True)
            print("id author you", flush=True)
            print("uciok", flush=True)
            continue

        if line == "isready":
            print("readyok", flush=True)
            continue

        if line.startswith("position "):
            fen_from_position_cmd(line)
            continue

        if line.startswith("go"):
            # Determine current fen to send
            if current_fen == "startpos":
                # build fen by asking stockfish to apply moves:
                # simplest: let stockfish handle position startpos moves ...
                # We'll ask stockfish directly using "position startpos moves ..." by converting to fen is harder.
                # Instead: use stockfish bestmove with "position startpos moves" is supported by stockfish but our bestmove uses fen.
                # So we will rebuild fen by asking stockfish itself: we set position startpos moves..., then use "d" to get fen.
                # We'll do an internal trick: create a fen via stockfish using "position startpos moves ..." and "d"
                with sf.lock:
                    sf._send("position startpos" + ((" moves " + " ".join(current_moves_uci)) if current_moves_uci else ""))
                    sf._send("d")
                    # read until a line that starts with "Fen:"
                    assert sf.proc.stdout is not None
                    fen_line = ""
                    start = time.time()
                    while time.time() - start < 2.0:
                        out = sf.proc.stdout.readline()
                        if not out:
                            break
                        out = out.strip()
                        if out.startswith("Fen:"):
                            fen_line = out
                            break
                    fen_to_use = fen_line.replace("Fen:", "").strip() if fen_line else ""
            else:
                fen_to_use = current_fen

            ply = ply_from_moves(current_moves_uci)

            move, source, conf = request_book_move(fen_to_use, ply)
            if move:
                log(f"book {move} conf={conf:.3f}")
                print(f"bestmove {move}", flush=True)
                continue

            # fallback to stockfish
            bm = sf.bestmove(fen_to_use, movetime_ms=DEFAULT_MOVETIME_MS)
            log(f"sf {bm}")
            print(f"bestmove {bm}", flush=True)
            continue

        if line == "quit":
            break

        # ignore other commands

    sf.quit()


if __name__ == "__main__":
    main()