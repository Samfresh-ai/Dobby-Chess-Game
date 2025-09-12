print("Starting app.py")
import os
import random
import chess
import subprocess
import time
print("Imported chess")
from flask import Flask, render_template, request
print("Imported Flask")
from flask_socketio import SocketIO, emit
print("Imported Flask-SocketIO")
from openai import OpenAI
print("Imported OpenAI")
from dotenv import load_dotenv
print("Imported dotenv")

load_dotenv()
print("Loaded .env")

app = Flask(__name__)
print("Created Flask app")
socketio = SocketIO(app)
print("Initialized SocketIO")

# Fireworks API setup
client = OpenAI(
    base_url="https://api.fireworks.ai/inference/v1",
    api_key=os.getenv("FIREWORKS_API_KEY")
)
print("Initialized Fireworks client")
DOBBY_MODEL = "accounts/sentientfoundation-serverless/models/dobby-mini-unhinged-plus-llama-3-1-8b"

# Stockfish setup
STOCKFISH_PATH = "./stockfish-ubuntu-x86-64"
print("Checking Stockfish path:", STOCKFISH_PATH)
if not os.path.exists(STOCKFISH_PATH):
    print("Stockfish binary not found at", STOCKFISH_PATH)
    raise FileNotFoundError("Stockfish binary missing")

# Game state
board = chess.Board()
bot_color = chess.BLACK
difficulty = 'novice'
captured_by_white = []
captured_by_black = []
last_player_move_quality = None
DIFFICULTY = {
    'novice': {'depth': 2, 'skill': 0},
    'apprentice': {'depth': 4, 'skill': 5},
    'journeyman': {'depth': 6, 'skill': 10},
    'expert': {'depth': 8, 'skill': 15},
    'master': {'depth': 10, 'skill': 18},
    'grandmaster': {'depth': 12, 'skill': 20}
}
print("Initialized game state")

def get_captured_piece(move):
    if board.is_capture(move):
        if board.is_en_passant(move):
            captured_square = chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
        else:
            captured_square = move.to_square
        return board.piece_at(captured_square)
    return None

def get_evaluation(fen, depth=10):
    try:
        process = subprocess.Popen(
            [STOCKFISH_PATH],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        process.stdin.write("uci\n")
        process.stdin.flush()
        process.stdin.write("setoption name Skill Level value 20\n")
        process.stdin.flush()
        process.stdin.write(f"position fen {fen}\n")
        process.stdin.flush()
        process.stdin.write(f"go depth {depth}\n")
        process.stdin.flush()

        score = None
        start_time = time.time()
        timeout = 10
        while time.time() - start_time < timeout:
            line = process.stdout.readline().strip()
            if "score cp" in line:
                parts = line.split()
                cp_index = parts.index("cp") if "cp" in parts else None
                if cp_index is not None:
                    score = int(parts[cp_index + 1])
            if line.startswith("bestmove"):
                break
        process.stdin.write("quit\n")
        process.stdin.flush()
        process.terminate()
        return score if score is not None else 0
    except Exception as e:
        print("Evaluation failed:", str(e))
        return 0

@app.route('/')
def index():
    print("Serving index route")
    return render_template('index.html')

@socketio.on('select_difficulty')
def handle_difficulty(data):
    global difficulty, board, captured_by_white, captured_by_black, last_player_move_quality
    difficulty = data['difficulty']
    board = chess.Board()
    captured_by_white = []
    captured_by_black = []
    last_player_move_quality = None
    print("Difficulty selected:", difficulty)
    emit('start_game', {'fen': board.fen(), 'captured_white': captured_by_white, 'captured_black': captured_by_black})

@socketio.on('player_move')
def handle_move(data):
    global last_player_move_quality
    print("Received player move:", data)
    try:
        move = chess.Move.from_uci(data['move'])
        if move in board.legal_moves:
            print("Player move is legal:", move)
            old_fen = board.fen()
            old_score = get_evaluation(old_fen)
            captured = get_captured_piece(move)
            board.push(move)
            new_score = get_evaluation(board.fen())
            delta = new_score - old_score
            threshold = 200  # centipawns, equivalent to 2 pawns
            if delta > threshold:
                move_quality = "great"
            elif delta < -threshold:
                move_quality = "blunder"
            else:
                move_quality = "normal"
            last_player_move_quality = move_quality
            if captured:
                captured_by_white.append(captured.symbol())
            print("Board after player move:", board.fen())
            emit('update_board', {'fen': board.fen(), 'captured_white': captured_by_white, 'captured_black': captured_by_black})
            if board.is_game_over():
                if board.is_checkmate():
                    msg = "Ha! I win. Loser!" if board.turn == bot_color else "Dang, you win! Cheater."
                else:
                    msg = "Game over!"
                print("Game over after player move:", msg)
                emit('game_over', {'message': msg})
                emit('chat_update', {'user': '', 'bot': msg})
            else:
                bot_turn()
        else:
            print("Invalid move attempted:", move)
            emit('chat_update', {'user': '', 'bot': f"Invalid move: {data['move']}"})
    except Exception as e:
        print("Error processing player move:", str(e))
        emit('chat_update', {'user': '', 'bot': f"Error processing move: {str(e)}"})

@socketio.on('chat_message')
def handle_chat(data):
    user_msg = data['message']
    fen = board.fen()
    print("Received chat message:", user_msg)
    prompt = f"You are Black, and the player is White. Current board: {fen}. Analyze the current board position and incorporate accurate observations about the game state in your response. Respond to player's trash talk: '{user_msg}'. Be sharp, annoying, unhinged, varied, random, and chess-related. Keep your response to a maximum of 2 sentences. Respond in natural language only, no code or move lists, no fluff, no mistakes."
    try:
        response = client.chat.completions.create(
            model=DOBBY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
            temperature=1.0
        )
        bot_reply = response.choices[0].message.content.strip()
        print("Chat response:", bot_reply)
        emit('chat_update', {'user': user_msg, 'bot': bot_reply})
    except Exception as e:
        print("Chat error:", str(e))
        emit('chat_update', {'user': user_msg, 'bot': f"Chat error: {str(e)}"})

@socketio.on('restart_game')
def handle_restart():
    global board, captured_by_white, captured_by_black, last_player_move_quality
    board = chess.Board()
    captured_by_white = []
    captured_by_black = []
    last_player_move_quality = None
    print("Restarting game")
    emit('show_welcome')

def bot_turn():
    global last_player_move_quality
    print("Bot turn triggered for board:", board.fen())
    depth = DIFFICULTY[difficulty]['depth']
    skill_level = DIFFICULTY[difficulty]['skill']
    move = None
    try:
        print("Starting Stockfish process")
        process = subprocess.Popen(
            [STOCKFISH_PATH],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        print("Stockfish process started, configuring UCI")
        process.stdin.write("uci\n")
        process.stdin.flush()
        process.stdin.write(f"setoption name Skill Level value {skill_level}\n")
        process.stdin.flush()
        process.stdin.write(f"position fen {board.fen()}\n")
        process.stdin.flush()
        process.stdin.write(f"go depth {depth}\n")
        process.stdin.flush()

        # Read output
        start_time = time.time()
        timeout = 5  # seconds
        while time.time() - start_time < timeout:
            line = process.stdout.readline().strip()
            print("Stockfish output:", line)
            if line.startswith("bestmove"):
                move_str = line.split()[1]
                move = chess.Move.from_uci(move_str)
                break
        process.stdin.write("quit\n")
        process.stdin.flush()
        process.terminate()
        print("Stockfish process terminated")
    except Exception as e:
        print("Stockfish move failed:", str(e))
        process.terminate()

    if not move:
        legal_moves = list(board.legal_moves)
        if legal_moves:
            move = random.choice(legal_moves)
            print("Using fallback move due to Stockfish failure:", move)
            emit('chat_update', {'user': '', 'bot': f"Stockfish failed, so I picked a random move!"})
        else:
            print("No legal moves available")
            emit('chat_update', {'user': '', 'bot': "No legal moves available"})
            return

    captured = get_captured_piece(move)
    board.push(move)
    if captured:
        captured_by_black.append(captured.symbol())
    print("Board updated with move:", move, "New FEN:", board.fen())

    # Trash talk
    fen = board.fen()
    extra = ""
    if last_player_move_quality:
        extra = f"The player's last move was a {last_player_move_quality} move. Incorporate jest if blunder, acknowledgment if great, in your trash talk. "
    prompt = f"You are Black, and the player is White. Current board: {fen}. You just made move {move.uci()}. {extra}Analyze the current board position and incorporate accurate observations about the game state in your response. Trash talk the player based on the game state. Be sharp, infuriating, unhinged, annoying, varied, random, chess-related. Don't fixate on one phrase or repeat yourself. Keep your response to a maximum of 2 sentences. Respond in natural language only, no code or move lists, no fluff, no mistakes."
    try:
        response = client.chat.completions.create(
            model=DOBBY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=1.0
        )
        trash = response.choices[0].message.content.strip()
        print("Trash talk:", trash)
    except Exception as e:
        print("Trash talk failed:", str(e))
        trash = "My trash talk crashed, but your position’s a joke!"

    emit('update_board', {'fen': board.fen(), 'captured_white': captured_by_white, 'captured_black': captured_by_black})
    print("Emitted update_board with FEN:", board.fen())
    emit('chat_update', {'user': '', 'bot': trash})

    if board.is_game_over():
        if board.is_checkmate():
            msg = "Dang, you win! Cheater." if board.turn == bot_color else "Ha! I win. Loser!"
        else:
            msg = "Game over!"
        print("Game over:", msg)
        emit('game_over', {'message': msg})
        emit('chat_update', {'user': '', 'bot': msg})

if __name__ == '__main__':
    print("Starting Flask server")
    try:
        socketio.run(app, debug=True, host='0.0.0.0', port=5000)
        print("Flask server started")
    except Exception as e:
        print("Flask server failed to start:", str(e))
