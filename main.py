
import pygame
import chess
import chess.engine
import os
import time
import threading
import queue
import copy

# =========================================================
# CONFIGURATION
# =========================================================

pygame.init()

WIDTH, HEIGHT = 1100, 820
SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Chess Game")

FPS = 60
BOARD_SIZE = 640
SQUARE_SIZE = BOARD_SIZE // 8
BOARD_X, BOARD_Y = 20, 100

FONT = pygame.font.SysFont("Arial", 22)
SMALL_FONT = pygame.font.SysFont("Arial", 16)
BIG_FONT = pygame.font.SysFont("Arial", 34, bold=True)

WHITE = (240, 217, 181)
BROWN = (181, 136, 99)
GREEN = (80, 170, 100)
YELLOW = (240, 220, 70)
BLACK = (25, 25, 25)
GRAY = (90, 90, 90)
LIGHT = (245, 245, 245)
RED = (210, 60, 60)
BLUE = (60, 120, 210)

CLOCK = pygame.time.Clock()

STOCKFISH_PATH = (
    r"C:\Users\paren\Downloads\stockfish"
    r"\stockfish-windows-x86-64-universal.exe"
)

# =========================================================
# GAME STATE
# =========================================================

board = chess.Board()

screen_mode = "menu"

game_type = "Casual"
game_mode = "Player vs Player"
human_color = chess.WHITE

clock_mode = "Basic"
starting_minutes = 10
increment_seconds = 3

white_time = starting_minutes * 60
black_time = starting_minutes * 60

paused = False
game_over_message = ""

selected_square = None
legal_moves = []

move_history = []

undo_stack = []
redo_stack = []

check_notice = ""
check_notice_until = 0

last_update = time.monotonic()

engine = None
engine_error = ""

ai_thinking = False
ai_queue = queue.Queue()

resignation_message = ""
resignation_until = 0

# =========================================================
# STOCKFISH
# =========================================================

def start_engine():
    global engine, engine_error

    if not os.path.isfile(STOCKFISH_PATH):
        engine_error = "Stockfish executable not found."
        return

    try:
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        engine_error = ""
    except Exception as exc:
        engine = None
        engine_error = str(exc)


def stop_engine():
    global engine

    if engine:
        try:
            engine.quit()
        except Exception:
            pass

    engine = None


def ai_worker(position):
    global ai_thinking

    try:
        if engine is None:
            ai_queue.put(("error", "Stockfish is not available"))
            return

        result = engine.play(position, chess.engine.Limit(time=0.5))

        if result.move:
            ai_queue.put(("move", result.move))
        else:
            ai_queue.put(("error", "Computer could not find a move"))

    except Exception as exc:
        ai_queue.put(("error", str(exc)))


def request_ai_move():
    global ai_thinking

    if ai_thinking or engine is None:
        return

    if board.is_game_over() or paused or game_over_message:
        return

    if board.turn == human_color:
        return

    ai_thinking = True

    position = board.copy()
    threading.Thread(
        target=ai_worker,
        args=(position,),
        daemon=True
    ).start()


# =========================================================
# CLOCK AND GAME STATE
# =========================================================

def format_time(seconds):
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    remaining = seconds % 60
    return f"{minutes:02}:{remaining:02}"


def reset_clocks():
    global white_time, black_time, last_update

    white_time = starting_minutes * 60
    black_time = starting_minutes * 60
    last_update = time.monotonic()


def update_clock():
    global white_time, black_time, last_update
    global game_over_message

    now = time.monotonic()
    elapsed = now - last_update
    last_update = now

    if screen_mode != "game":
        return

    if paused or game_over_message:
        return

    if board.turn == chess.WHITE:
        white_time -= elapsed

        if white_time <= 0:
            white_time = 0
            game_over_message = "Black wins on time!"
    else:
        black_time -= elapsed

        if black_time <= 0:
            black_time = 0
            game_over_message = "White wins on time!"


def add_increment(moving_color):
    if clock_mode != "Increment":
        return

    if moving_color == chess.WHITE:
        global white_time
        white_time += increment_seconds
    else:
        global black_time
        black_time += increment_seconds


# =========================================================
# MOVE HISTORY AND UNDO/REDO
# =========================================================

def get_snapshot():
    return {
        "fen": board.fen(),
        "white_time": white_time,
        "black_time": black_time,
    }


def restore_snapshot(snapshot):
    global board, white_time, black_time
    global selected_square, legal_moves, game_over_message

    board.set_fen(snapshot["fen"])

    white_time = snapshot["white_time"]
    black_time = snapshot["black_time"]

    selected_square = None
    legal_moves = []
    game_over_message = ""

    update_game_status()


def save_undo_state():
    if game_type == "Analysis":
        undo_stack.append(get_snapshot())
        redo_stack.clear()


def undo_move():
    if game_type != "Analysis" or not undo_stack:
        return

    redo_stack.append(get_snapshot())
    snapshot = undo_stack.pop()
    restore_snapshot(snapshot)


def redo_move():
    if game_type != "Analysis" or not redo_stack:
        return

    undo_stack.append(get_snapshot())
    snapshot = redo_stack.pop()
    restore_snapshot(snapshot)


# =========================================================
# GAME STATUS
# =========================================================

def update_game_status():
    global game_over_message

    if board.is_checkmate():
        winner = "Black" if board.turn == chess.WHITE else "White"
        game_over_message = f"CHECKMATE! {winner} wins!"

    elif board.is_stalemate():
        game_over_message = "STALEMATE! Draw!"

    elif board.is_insufficient_material():
        game_over_message = "DRAW! Insufficient material"

    elif board.is_seventyfive_moves():
        game_over_message = "DRAW! 75-move rule"

    elif board.is_fivefold_repetition():
        game_over_message = "DRAW! Fivefold repetition"


def show_check_notice():
    global check_notice, check_notice_until

    if board.is_check() and not board.is_checkmate():
        side = "White" if board.turn == chess.WHITE else "Black"
        check_notice = f"CHECK! {side} king is in check"
        check_notice_until = time.monotonic() + 3


# =========================================================
# MOVE EXECUTION
# =========================================================

def make_move(move, is_computer=False):
    global selected_square, legal_moves
    global last_update

    if paused or game_over_message:
        return

    if move not in board.legal_moves:
        return

    moving_color = board.turn

    if not is_computer:
        save_undo_state()

    board.push(move)

    add_increment(moving_color)

    selected_square = None
    legal_moves = []

    update_game_status()

    if not game_over_message:
        show_check_notice()

    last_update = time.monotonic()


def process_ai_result():
    global ai_thinking

    try:
        while True:
            result_type, result = ai_queue.get_nowait()

            ai_thinking = False

            if result_type == "move":
                if (
                    screen_mode == "game"
                    and not paused
                    and not game_over_message
                    and board.turn != human_color
                    and result in board.legal_moves
                ):
                    make_move(result, is_computer=True)

            elif result_type == "error":
                print("Stockfish error:", result)

    except queue.Empty:
        pass


# =========================================================
# RESIGNATION
# =========================================================

def resign_game():
    global screen_mode, paused
    global resignation_message, resignation_until
    global selected_square, legal_moves

    if screen_mode != "game":
        return

    if game_mode == "Player vs Computer":
        resigning_side = human_color
    else:
        resigning_side = board.turn

    winner = "Black" if resigning_side == chess.WHITE else "White"

    resignation_message = f"{winner} wins by resignation!"
    resignation_until = time.monotonic() + 1.5

    paused = False
    selected_square = None
    legal_moves = []


def handle_resignation_return():
    global screen_mode, resignation_message

    if resignation_message and time.monotonic() >= resignation_until:
        resignation_message = ""
        screen_mode = "menu"


# =========================================================
# START / RESTART GAME
# =========================================================

def start_game():
    global board, screen_mode, paused
    global game_over_message, selected_square
    global legal_moves, undo_stack, redo_stack
    global move_history, check_notice, resignation_message
    global ai_thinking

    board = chess.Board()

    screen_mode = "game"
    paused = False

    game_over_message = ""
    resignation_message = ""
    check_notice = ""

    selected_square = None
    legal_moves = []

    undo_stack.clear()
    redo_stack.clear()
    move_history.clear()

    ai_thinking = False

    while not ai_queue.empty():
        try:
            ai_queue.get_nowait()
        except queue.Empty:
            break

    reset_clocks()


def restart_game():
    start_game()


# =========================================================
# DRAWING HELPERS
# =========================================================

def draw_text(surface, text, x, y, font=FONT, color=BLACK):
    rendered = font.render(str(text), True, color)
    surface.blit(rendered, (x, y))


def draw_button(surface, text, rect, color=BLUE):
    pygame.draw.rect(surface, color, rect, border_radius=8)
    pygame.draw.rect(surface, BLACK, rect, 2, border_radius=8)

    rendered = FONT.render(text, True, LIGHT)

    surface.blit(
        rendered,
        rendered.get_rect(center=rect.center)
    )


def draw_menu():
    SCREEN.fill((225, 230, 235))

    draw_text(
        SCREEN,
        "CHESS GAME",
        390,
        35,
        BIG_FONT
    )

    draw_text(SCREEN, "Game Type", 100, 120)

    draw_button(
        SCREEN,
        "Casual",
        pygame.Rect(100, 155, 180, 45),
        GREEN if game_type == "Casual" else BLUE
    )

    draw_button(
        SCREEN,
        "Analysis",
        pygame.Rect(300, 155, 180, 45),
        GREEN if game_type == "Analysis" else BLUE
    )

    draw_text(SCREEN, "Game Mode", 100, 230)

    draw_button(
        SCREEN,
        "Player vs Player",
        pygame.Rect(100, 265, 220, 45),
        GREEN if game_mode == "Player vs Player" else BLUE
    )

    draw_button(
        SCREEN,
        "Player vs Computer",
        pygame.Rect(340, 265, 250, 45),
        GREEN if game_mode == "Player vs Computer" else BLUE
    )

    if game_mode == "Player vs Computer":
        draw_text(SCREEN, "Play as", 100, 345)

        draw_button(
            SCREEN,
            "White",
            pygame.Rect(100, 380, 150, 45),
            GREEN if human_color == chess.WHITE else BLUE
        )

        draw_button(
            SCREEN,
            "Black",
            pygame.Rect(270, 380, 150, 45),
            GREEN if human_color == chess.BLACK else BLUE
        )

    draw_text(SCREEN, "Clock Mode", 650, 120)

    draw_button(
        SCREEN,
        "Basic",
        pygame.Rect(650, 155, 150, 45),
        GREEN if clock_mode == "Basic" else BLUE
    )

    draw_button(
        SCREEN,
        "Increment",
        pygame.Rect(820, 155, 170, 45),
        GREEN if clock_mode == "Increment" else BLUE
    )

    draw_text(
        SCREEN,
        f"Starting minutes: {starting_minutes}",
        650,
        235
    )

    draw_button(
        SCREEN,
        "-",
        pygame.Rect(650, 270, 55, 40),
        RED
    )

    draw_button(
        SCREEN,
        "+",
        pygame.Rect(720, 270, 55, 40),
        GREEN
    )

    if clock_mode == "Increment":
        draw_text(
            SCREEN,
            f"Increment seconds: {increment_seconds}",
            650,
            340
        )

        draw_button(
            SCREEN,
            "-",
            pygame.Rect(650, 375, 55, 40),
            RED
        )

        draw_button(
            SCREEN,
            "+",
            pygame.Rect(720, 375, 55, 40),
            GREEN
        )

    draw_button(
        SCREEN,
        "START GAME",
        pygame.Rect(390, 540, 260, 65),
        GREEN
    )

    if engine_error and game_mode == "Player vs Computer":
        draw_text(SCREEN, engine_error, 100, 650, SMALL_FONT, RED)


# =========================================================
# CHESS BOARD
# =========================================================

PIECE_SYMBOLS = {
    "K": "♔", "Q": "♕", "R": "♖",
    "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜",
    "b": "♝", "n": "♞", "p": "♟",
}


def square_to_pixel(square):
    file_index = chess.square_file(square)
    rank_index = chess.square_rank(square)

    x = BOARD_X + file_index * SQUARE_SIZE
    y = BOARD_Y + (7 - rank_index) * SQUARE_SIZE

    return x, y


def draw_board():
    for rank in range(8):
        for file_index in range(8):
            square = chess.square(file_index, 7 - rank)

            x = BOARD_X + file_index * SQUARE_SIZE
            y = BOARD_Y + rank * SQUARE_SIZE

            color = WHITE if (rank + file_index) % 2 == 0 else BROWN

            pygame.draw.rect(
                SCREEN,
                color,
                (x, y, SQUARE_SIZE, SQUARE_SIZE)
            )

            if selected_square == square:
                pygame.draw.rect(
                    SCREEN,
                    YELLOW,
                    (x, y, SQUARE_SIZE, SQUARE_SIZE)
                )

            if square in legal_moves:
                center = (
                    x + SQUARE_SIZE // 2,
                    y + SQUARE_SIZE // 2
                )

                pygame.draw.circle(
                    SCREEN,
                    GREEN,
                    center,
                    9
                )

            piece = board.piece_at(square)

            if piece:
                symbol = PIECE_SYMBOLS[piece.symbol()]

                piece_color = (
                    (250, 250, 250)
                    if piece.color == chess.WHITE
                    else (20, 20, 20)
                )

                rendered = pygame.font.SysFont(
                    "Segoe UI Symbol",
                    58
                ).render(symbol, True, piece_color)

                SCREEN.blit(
                    rendered,
                    rendered.get_rect(
                        center=(
                            x + SQUARE_SIZE // 2,
                            y + SQUARE_SIZE // 2
                        )
                    )
                )


# =========================================================
# GAME SCREEN
# =========================================================

def draw_game():
    SCREEN.fill((225, 230, 235))

    draw_board()

    draw_text(
        SCREEN,
        "CHESS",
        790,
        25,
        BIG_FONT
    )

    draw_text(
        SCREEN,
        "White: " + format_time(white_time),
        790,
        100
    )

    draw_text(
        SCREEN,
        "Black: " + format_time(black_time),
        790,
        140
    )

    turn = "White" if board.turn == chess.WHITE else "Black"

    draw_text(
        SCREEN,
        f"Turn: {turn}",
        790,
        190
    )

    draw_text(
        SCREEN,
        f"Mode: {game_type}",
        790,
        230
    )

    draw_text(
        SCREEN,
        f"Players: {game_mode}",
        790,
        270,
        SMALL_FONT
    )

    draw_button(
        SCREEN,
        "Pause" if not paused else "Resume",
        pygame.Rect(790, 330, 125, 45),
        GREEN
    )

    draw_button(
        SCREEN,
        "Restart",
        pygame.Rect(930, 330, 125, 45)
    )

    if game_type == "Analysis":
        draw_button(
            SCREEN,
            "Undo",
            pygame.Rect(790, 390, 125, 45)
        )

        draw_button(
            SCREEN,
            "Redo",
            pygame.Rect(930, 390, 125, 45)
        )

    draw_button(
        SCREEN,
        "Resign",
        pygame.Rect(790, 460, 265, 45),
        RED
    )

    draw_button(
        SCREEN,
        "Main Menu",
        pygame.Rect(790, 520, 265, 45),
        GRAY
    )

    if paused:
        draw_text(
            SCREEN,
            "GAME PAUSED",
            350,
            55,
            BIG_FONT,
            RED
        )

    if check_notice and time.monotonic() < check_notice_until:
        draw_text(
            SCREEN,
            check_notice,
            330,
            55,
            BIG_FONT,
            RED
        )

    if ai_thinking:
        draw_text(
            SCREEN,
            "Computer thinking...",
            790,
            600,
            FONT,
            BLUE
        )

    if game_over_message:
        pygame.draw.rect(
            SCREEN,
            (255, 255, 255),
            (250, 350, 600, 100),
            border_radius=12
        )

        draw_text(
            SCREEN,
            game_over_message,
            290,
            385,
            BIG_FONT,
            RED
        )

    if resignation_message:
        pygame.draw.rect(
            SCREEN,
            (255, 255, 255),
            (250, 350, 600, 100),
            border_radius=12
        )

        draw_text(
            SCREEN,
            resignation_message,
            290,
            385,
            BIG_FONT,
            RED
        )


# =========================================================
# CLICK HANDLING
# =========================================================

def handle_menu_click(pos):
    global game_type, game_mode, human_color
    global clock_mode, starting_minutes, increment_seconds

    x, y = pos

    if 155 <= y <= 200:
        if 100 <= x <= 280:
            game_type = "Casual"

        elif 300 <= x <= 480:
            game_type = "Analysis"

    elif 265 <= y <= 310:
        if 100 <= x <= 320:
            game_mode = "Player vs Player"

        elif 340 <= x <= 590:
            game_mode = "Player vs Computer"

    elif 380 <= y <= 425 and game_mode == "Player vs Computer":
        if 100 <= x <= 250:
            human_color = chess.WHITE

        elif 270 <= x <= 420:
            human_color = chess.BLACK

    elif 155 <= y <= 200:
        pass

    if 155 <= y <= 200 and 650 <= x <= 800:
        clock_mode = "Basic"

    elif 155 <= y <= 200 and 820 <= x <= 990:
        clock_mode = "Increment"

    elif 270 <= y <= 310:
        if 650 <= x <= 705:
            starting_minutes = max(1, starting_minutes - 1)

        elif 720 <= x <= 775:
            starting_minutes = min(180, starting_minutes + 1)

    elif clock_mode == "Increment" and 375 <= y <= 415:
        if 650 <= x <= 705:
            increment_seconds = max(0, increment_seconds - 1)

        elif 720 <= x <= 775:
            increment_seconds = min(60, increment_seconds + 1)

    if 540 <= y <= 605 and 390 <= x <= 650:
        start_game()


def handle_game_click(pos):
    global selected_square, legal_moves
    global paused, screen_mode

    x, y = pos

    if resignation_message:
        return

    if 330 <= y <= 375 and 790 <= x <= 915:
        paused = not paused
        return

    if 330 <= y <= 375 and 930 <= x <= 1055:
        restart_game()
        return

    if 390 <= y <= 435 and 790 <= x <= 915:
        undo_move()
        return

    if 390 <= y <= 435 and 930 <= x <= 1055:
        redo_move()
        return

    if 460 <= y <= 505 and 790 <= x <= 1055:
        resign_game()
        return

    if 520 <= y <= 565 and 790 <= x <= 1055:
        screen_mode = "menu"
        paused = False
        return

    if paused or game_over_message:
        return

    if (
        game_mode == "Player vs Computer"
        and board.turn != human_color
    ):
        return

    if not (
        BOARD_X <= x < BOARD_X + BOARD_SIZE
        and BOARD_Y <= y < BOARD_Y + BOARD_SIZE
    ):
        return

    file_index = (x - BOARD_X) // SQUARE_SIZE
    rank_index = 7 - ((y - BOARD_Y) // SQUARE_SIZE)

    square = chess.square(file_index, rank_index)

    if selected_square is None:
        piece = board.piece_at(square)

        if piece and piece.color == board.turn:
            selected_square = square

            legal_moves = [
                move.to_square
                for move in board.legal_moves
                if move.from_square == square
            ]

    else:
        move = chess.Move(selected_square, square)

        # Automatic queen promotion
        piece = board.piece_at(selected_square)

        if (
            piece
            and piece.piece_type == chess.PAWN
            and chess.square_rank(square) in (0, 7)
        ):
            move.promotion = chess.QUEEN

        if move in board.legal_moves:
            make_move(move)
        else:
            piece = board.piece_at(square)

            if piece and piece.color == board.turn:
                selected_square = square

                legal_moves = [
                    m.to_square
                    for m in board.legal_moves
                    if m.from_square == square
                ]
            else:
                selected_square = None
                legal_moves = []


# =========================================================
# MAIN LOOP
# =========================================================

def main():
    global last_update

    start_engine()

    running = True

    while running:
        CLOCK.tick(FPS)

        process_ai_result()

        if screen_mode == "game":
            update_clock()

            if (
                game_mode == "Player vs Computer"
                and board.turn != human_color
                and not paused
                and not game_over_message
                and not resignation_message
            ):
                request_ai_move()

            handle_resignation_return()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if screen_mode == "menu":
                    handle_menu_click(event.pos)

                elif screen_mode == "game":
                    handle_game_click(event.pos)

        if screen_mode == "menu":
            draw_menu()

        elif screen_mode == "game":
            draw_game()

        pygame.display.flip()

    stop_engine()
    pygame.quit()


if __name__ == "__main__":
    main()
