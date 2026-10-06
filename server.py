import socket
import threading

from protocol import (
    create_message,
    send_message,
    receive_messages,
    validate_message,
    validate_action
)

from game import (
    PokerGame,
    hand_name
)


HOST = "0.0.0.0"
PORT = 5050


# --------------------------------------------------
# SERVER SOCKET
# --------------------------------------------------

server_socket = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM
)

server_socket.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_REUSEADDR,
    1
)


# --------------------------------------------------
# SHARED SERVER STATE
# --------------------------------------------------

players = {}

game = PokerGame(
    max_rounds=10
)

game_lock = threading.Lock()


# --------------------------------------------------
# MESSAGE HELPERS
# --------------------------------------------------

def send_error(sock, code, text):

    message = create_message(
        "ERROR",
        "SERVER",
        {
            "code": code,
            "message": text
        }
    )

    send_message(
        sock,
        message
    )


def broadcast(message):
    """
    Send a message to every connected player.
    """

    dead_players = []

    for player_id, sock in list(players.items()):

        try:
            send_message(
                sock,
                message
            )

        except (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError
        ):
            dead_players.append(
                player_id
            )

    for player_id in dead_players:
        handle_disconnect(
            player_id
        )


def broadcast_state(last_action=None):
    """
    Send the current public game state to both players.
    """

    payload = game.public_state()

    payload["last_action"] = last_action

    message = create_message(
        "STATE_UPDATE",
        "SERVER",
        payload
    )

    broadcast(message)


def send_hand(player_id):
    """
    Send a player's private hand only to that player.
    """

    if player_id not in players:
        return

    hand = game.get_hand(
        player_id
    )

    message = create_message(
        "HAND",
        "SERVER",
        {
            "cards": hand
        }
    )

    send_message(
        players[player_id],
        message
    )


def send_all_hands():

    send_hand(
        "Player_1"
    )

    send_hand(
        "Player_2"
    )


# --------------------------------------------------
# GAME START
# --------------------------------------------------

def start_game():

    game.start_game()

    for player_id in players:

        opponent = (
            "Player_2"
            if player_id == "Player_1"
            else "Player_1"
        )

        message = create_message(
            "GAME_START",
            "SERVER",
            {
                "your_role": player_id,
                "opponent": opponent,
                "round": game.round_number,
                "max_rounds": game.max_rounds,
                "first_player": game.active_player
            }
        )

        send_message(
            players[player_id],
            message
        )

    send_all_hands()

    broadcast_state(
        "GAME_START"
    )


# --------------------------------------------------
# ROUND MANAGEMENT
# --------------------------------------------------

def end_round(winner, reason="SHOWDOWN"):

    # Evaluate the hands before changing the game state.
    p1_score = None
    p2_score = None

    if reason == "SHOWDOWN":

        _, p1_score, p2_score = game.showdown()

    game.record_round_win(
        winner
    )

    winning_hand = None

    if reason == "SHOWDOWN":

        if winner == "Player_1":
            winning_hand = hand_name(
                p1_score
            )

        elif winner == "Player_2":
            winning_hand = hand_name(
                p2_score
            )

    message = create_message(
        "ROUND_OVER",
        "SERVER",
        {
            "round": game.round_number,
            "winner": winner,
            "reason": reason,
            "winning_hand": winning_hand,
            "player_1_wins": game.player_1_wins,
            "player_2_wins": game.player_2_wins,
            "player_1_streak": game.player_1_streak,
            "player_2_streak": game.player_2_streak
        }
    )

    broadcast(message)

    over, match_winner, match_reason = (
        game.check_game_over()
    )

    if over:

        end_game(
            match_winner,
            match_reason
        )

        return

    game.next_round()

    send_all_hands()

    broadcast_state(
        "NEW_ROUND"
    )


def end_game(winner, reason):

    game.state = "GAME_OVER"

    message = create_message(
        "GAME_OVER",
        "SERVER",
        {
            "winner": winner,
            "reason": reason,
            "rounds_played": game.round_number,
            "player_1_wins": game.player_1_wins,
            "player_2_wins": game.player_2_wins
        }
    )

    broadcast(message)


# --------------------------------------------------
# PLAYER ACTIONS
# --------------------------------------------------

def handle_action(player_id, sock, payload):

    valid, error = validate_action(
        payload
    )

    if not valid:

        send_error(
            sock,
            error,
            "Invalid poker action."
        )

        return

    if game.state != "PLAYER_TURN":

        send_error(
            sock,
            "INVALID_STATE",
            "The game is not accepting actions."
        )

        return

    if player_id != game.active_player:

        send_error(
            sock,
            "OUT_OF_TURN",
            "It is not your turn."
        )

        return

    action = payload["action"]

    # ---------------------------
    # FOLD
    # ---------------------------

    if action == "FOLD":

        winner = (
            "Player_2"
            if player_id == "Player_1"
            else "Player_1"
        )

        end_round(
            winner,
            "FOLD"
        )

        return

    # ---------------------------
    # DRAW
    # ---------------------------

    if action == "DRAW":

        indexes = payload["cards"]

        game.draw_cards(
            player_id,
            indexes
        )

        send_hand(
            player_id
        )

        game.switch_turn()

        broadcast_state(
            "DRAW"
        )

        return

    # ---------------------------
    # CHECK
    # ---------------------------

    if action == "CHECK":

        game.switch_turn()

        broadcast_state(
            "CHECK"
        )

        return

    # ---------------------------
    # BET
    # ---------------------------

    if action == "BET":

        # Basic placeholder behavior.
        # Betting state can be expanded later.

        game.switch_turn()

        broadcast_state(
            "BET"
        )

        return

    # ---------------------------
    # CALL
    # ---------------------------

    if action == "CALL":

        winner, p1_score, p2_score = (
            game.showdown()
        )

        end_round(
            winner,
            "SHOWDOWN"
        )

        return


# --------------------------------------------------
# DISCONNECT HANDLING
# --------------------------------------------------

def handle_disconnect(player_id):

    if player_id not in players:
        return

    print(
        player_id,
        "disconnected"
    )

    sock = players.pop(
        player_id
    )

    try:
        sock.close()

    except OSError:
        pass

    if game.state not in (
        "WAITING_FOR_PLAYERS",
        "GAME_OVER"
    ):

        opponent = (
            "Player_2"
            if player_id == "Player_1"
            else "Player_1"
        )

        if opponent in players:

            end_game(
                opponent,
                "FORFEIT"
            )


# --------------------------------------------------
# MESSAGE DISPATCHER
# --------------------------------------------------

def process_message(player_id, sock, message):

    valid, error = validate_message(
        message
    )

    if not valid:

        send_error(
            sock,
            error,
            "Invalid protocol message."
        )

        return

    msg_type = message["msg_type"]

    if msg_type == "ACTION":

        with game_lock:

            handle_action(
                player_id,
                sock,
                message["payload"]
            )

    elif msg_type == "DISCONNECT":

        handle_disconnect(
            player_id
        )

    elif msg_type == "CONNECT":

        # Connection has already been registered.
        pass


# --------------------------------------------------
# CLIENT THREAD
# --------------------------------------------------

def handle_client(player_id, sock, address):

    print(
        player_id,
        "connected from",
        address
    )

    buffer = ""

    try:

        while True:

            messages, buffer, connected = (
                receive_messages(
                    sock,
                    buffer
                )
            )

            # recv() returned b""
            if not connected:
                break

            for message in messages:

                process_message(
                    player_id,
                    sock,
                    message
                )

    except (
        ConnectionResetError,
        BrokenPipeError,
        ConnectionAbortedError,
        TimeoutError
    ) as error:

        print(
            "Connection error for",
            player_id,
            ":",
            error
        )

    finally:

        handle_disconnect(
            player_id
        )


# --------------------------------------------------
# MAIN SERVER
# --------------------------------------------------

def main():

    server_socket.bind(
        (HOST, PORT)
    )

    server_socket.listen(2)

    print(
        f"Poker server listening on port {PORT}"
    )

    # Wait for two players.
    while len(players) < 2:

        sock, address = (
            server_socket.accept()
        )

        if "Player_1" not in players:
            player_id = "Player_1"

        else:
            player_id = "Player_2"

        players[player_id] = sock

        wait_message = create_message(
            "LOBBY_WAIT",
            "SERVER",
            {
                "players_connected": len(players),
                "players_required": 2
            }
        )

        send_message(
            sock,
            wait_message
        )

        thread = threading.Thread(
            target=handle_client,
            args=(
                player_id,
                sock,
                address
            ),
            daemon=True
        )

        thread.start()

    # Two players are connected.
    with game_lock:
        start_game()

    # Keep server alive until game ends.
    while game.state != "GAME_OVER":
        threading.Event().wait(1)

    server_socket.close()


if __name__ == "__main__":
    main()