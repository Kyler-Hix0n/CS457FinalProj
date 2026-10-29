import socket
import threading

from protocol import (
    create_message,
    send_message,
    receive_messages
)


# ---------------------------------------------------
# CONNECTION SETTINGS
# ----------------------------------------------------------

# Local test
SERVER_HOST = "127.0.0.1"

#CML:
# SERVER_HOST = "192.168.20.100"

PORT = 5050
#PORT = 5000 mac no like

SPACER = "==================="


# ------------------------------------------------------
# CLIENT STATE
# ---------------------------------------------------

sock = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM
)

player_id = None
running = True

# Most recent STATE_UPDATE payload, used to re-prompt
# after the server rejects a move.
last_state = None

# True after a move was rejected because the opponent
# dropped. The turn is offered again when they return.
paused = False


# ---------------------------------------------------
# CARD DISPLAY
# ---------------------------------------------------------------

SUIT_SYMBOLS = {
    "H": "♥",
    "D": "♦",
    "C": "♣",
    "S": "♠"
}


def pretty_card(card):

    rank = card[:-1]
    suit = card[-1]

    return (
        rank
        + SUIT_SYMBOLS.get(
            suit,
            suit
        )
    )


def display_hand(cards):

    print("\nYour hand:")

    for index, card in enumerate(cards):

        print(
            f"[{index}] {pretty_card(card)}"
        )

    print()


# ---------------------------------------------------------
# SEND ACTION
# --------------------------------------------------------------

def send_action(action, **kwargs):

    payload = {
        "action": action
    }

    payload.update(kwargs)
    message = create_message("ACTION", player_id, payload)
    send_message(sock,message)


# -------------------------------------------------------
# PLAYER TURN
# --------------------------------------------------------------

def legal_choices(state):
    """
    Only offer actions the server will accept in this phase.
    The server still validates everything.
    """

    if state["phase"] == "DRAW_PHASE":
        return ["DRAW"]

    current_bet = state.get("current_bet", 0)
    all_in = state.get("all_in_amount", 0)

    if current_bet > 0:

        # When the opponent is already all in, ALL_IN would
        # only match the bet, which is the same as CALL.
        if all_in > current_bet:
            return ["CALL", "ALL_IN", "FOLD"]

        return ["CALL", "FOLD"]

    return ["CHECK", "BET", "ALL_IN", "FOLD"]


def choice_label(action, state):

    if action == "ALL_IN":
        return f"All in ({state.get('all_in_amount', 0)})"

    if action == "CALL":
        return f"Call ({state.get('current_bet', 0)})"

    return action.title()


def my_chips(state):

    if player_id == "Player_1":
        return state.get("player_1_chips")

    return state.get("player_2_chips")


def send_quit():

    global running

    disconnect = create_message(
        "DISCONNECT",
        player_id,
        {
            "reason": "QUIT"
        }
    )

    send_message(
        sock,
        disconnect
    )

    print(
        "You left the game."
    )

    running = False


def take_turn(state):
    """
    Prompt until one action has been sent (or the player quits).
    """

    choices = legal_choices(
        state
    )

    print("\nYour turn.")

    print(
        "Your chips:",
        my_chips(state)
    )

    if state.get("current_bet", 0) > 0:
        print(
            "Bet to call:",
            state["current_bet"]
        )

    while True:

        for number, action in enumerate(choices, start=1):
            print(f"{number}. {choice_label(action, state)}")

        print("Q. Quit (forfeits the match)")

        choice = input(
            "> "
        ).strip().lower()

        if choice == "q":
            send_quit()
            return

        if (
            not choice.isdigit()
            or not 1 <= int(choice) <= len(choices)
        ):
            print(
                "Invalid selection."
            )
            continue

        action = choices[int(choice) - 1]

        # BET
        if action == "BET":

            max_bet = state.get("max_bet", 0)

            try:

                amount = int(
                    input(
                        f"Bet amount (1-{max_bet}): "
                    )
                )

            except ValueError:

                print(
                    "Bet must be a number."
                )
                continue

            if not 1 <= amount <= max_bet:

                print(
                    f"Bet must be between 1 and {max_bet}."
                )
                continue

            send_action(
                "BET",
                amount=amount
            )

        # DRAW
        elif action == "DRAW":

            raw = input(
                "Card positions to replace "
                "(example: 0,2,4 or blank to keep all): "
            )

            try:

                indexes = [
                    int(value.strip())
                    for value in raw.split(",")
                    if value.strip()
                ]

            except ValueError:

                print(
                    "Invalid card indexes."
                )
                continue

            send_action(
                "DRAW",
                cards=indexes
            )

        # CHECK, CALL, ALL_IN, FOLD
        else:

            send_action(
                action
            )

        return


# ------------------------------------------------------
# HANDLE SERVER MESSAGES
# --------------------------------------------------------

def handle_message(message):

    global player_id
    global running
    global last_state
    global paused

    msg_type = message.get(
        "msg_type"
    )

    payload = message.get(
        "payload",
        {}
    )

    # -----------------------------------------------
    # LOBBY
    # ---------------------------------------------------

    if msg_type == "LOBBY_WAIT":

        print(
            "\nPlayers connected:",
            f"{payload['players_connected']}/"
            f"{payload['players_required']}"
        )

        print(
            "Waiting for opponent..."
        )

    # -----------------------------------------
    # GAME START
    # ------------------------------------

    elif msg_type == "GAME_START":

        player_id = payload[
            "your_role"
        ]

        print("\n" + SPACER)

        if payload.get("resumed"):
            print("GAME RESUMED")

        else:
            print("GAME START")

        print(SPACER)
        print("You are:",player_id)

        print(
            "First player:",
            payload["first_player"]
        )

        print(
            "Maximum rounds:",
            payload["max_rounds"]
        )

        print(
            "Starting chips:",
            payload.get("starting_chips")
        )

    # ----------------------------------
    # PRIVATE HAND
    # ------------------------------------------

    elif msg_type == "HAND":

        display_hand(
            payload["cards"]
        )

    # ---------------------------------
    # STATE UPDATE
    # -----------------------------------

    elif msg_type == "STATE_UPDATE":

        print(
            "\n--- Game State ---"
        )

        print(
            "Round:",
            payload["round"]
        )

        print(
            "Active player:",
            payload["active_player"]
        )

        print(
            "Phase:",
            payload["phase"]
        )

        print(
            "Pot:",
            payload.get("pot", 0),
            "| Bet to call:",
            payload.get("current_bet", 0)
        )

        print(
            "Chips:",
            payload.get("player_1_chips"),
            "-",
            payload.get("player_2_chips")
        )

        print(
            "Last action:",
            payload.get(
                "last_action"
            )
        )

        print(
            "Score:",
            payload["player_1_wins"],
            "-",
            payload["player_2_wins"]
        )

        print(
            "Streaks:",
            payload["player_1_streak"],
            "-",
            payload["player_2_streak"]
        )

        seq = payload.get("seq")

        if (
            last_state is not None
            and seq is not None
            and last_state.get("seq") is not None
            and seq != last_state["seq"] + 1
        ):
            print(
                "Warning: missed a state update."
            )

        last_state = payload

        if (
            payload["active_player"]
            == player_id
        ):

            take_turn(
                payload
            )

        else:

            print(
                "Waiting for opponent..."
            )

    # ----------------------------
    # ROUND OVER
    # --------------------------------

    elif msg_type == "ROUND_OVER":

        print("\n" + SPACER)
        print("ROUND OVER")
        print(SPACER)

        if payload["winner"] is None:

            print(
                "Round ended in a tie."
            )

        else:

            print(
                "Winner:",
                payload["winner"]
            )

        print(
            "Reason:",
            payload["reason"]
        )

        if payload.get(
            "winning_hand"
        ):

            print(
                "Winning hand:",
                payload["winning_hand"]
            )

        print(
            "Pot:",
            payload.get("pot", 0)
        )

        print(
            "Chips:",
            payload.get("player_1_chips"),
            "-",
            payload.get("player_2_chips")
        )

        print(
            "Score:",
            payload["player_1_wins"],
            "-",
            payload["player_2_wins"]
        )

        print(
            "Streaks:",
            payload["player_1_streak"],
            "-",
            payload["player_2_streak"]
        )

    # -------------------------------
    # ERROR
    # ---------------------------

    elif msg_type == "ERROR":

        print(
            "\nERROR:",
            payload.get(
                "code"
            )
        )

        print(
            payload.get(
                "message"
            )
        )

        # The opponent dropped. Wait for PLAYER_RECONNECTED
        # instead of asking again right away.
        if payload.get("code") == "GAME_PAUSED":

            paused = True

            print(
                "Waiting for your opponent to reconnect..."
            )

            return

        # A rejected move changes nothing on the server, so
        # no new STATE_UPDATE is coming. Ask again.
        if (
            last_state is not None
            and last_state["active_player"] == player_id
            and running
        ):
            take_turn(
                last_state
            )

    # ---------------------------
    # OPPONENT LEFT / RETURNED
    # ------------------------------

    elif msg_type == "PLAYER_DISCONNECTED":

        print(
            f"\n{payload['player']} lost connection. "
            f"Waiting up to {payload['reconnect_timeout']}s "
            "for them to reconnect..."
        )

    elif msg_type == "PLAYER_RECONNECTED":

        print(
            f"\n{payload['player']} reconnected."
        )

        # Offer the turn again if a move was refused while
        # the opponent was away.
        if (
            paused
            and last_state is not None
            and last_state["active_player"] == player_id
            and running
        ):

            paused = False

            take_turn(
                last_state
            )

        paused = False

    # ---------------------------
    # GAME OVER
    # ------------------------------

    elif msg_type == "GAME_OVER":

        print(
            "\n===================="
        )

        print(
            "GAME OVER"
        )

        print(
            "===================="
        )

        if payload["winner"] is None:

            print(
                "The match is a draw."
            )

        elif payload["winner"] == player_id:

            print(
                "You won the match."
            )

        else:

            print(
                "You lost the match."
            )

        print(
            "Winner:",
            payload["winner"]
        )

        print(
            "Reason:",
            payload["reason"]
        )

        print(
            "Rounds played:",
            payload["rounds_played"]
        )

        print(
            "Final score:",
            payload["player_1_wins"],
            "-",
            payload["player_2_wins"]
        )

        print(
            "Final chips:",
            payload.get("player_1_chips"),
            "-",
            payload.get("player_2_chips")
        )

        running = False


# --------------------------------------------------
# RECEIVE THREAD
# --------------------------------------------------

def receive_loop():

    global running

    buffer = b""

    try:

        while running:

            messages, buffer, connected = (
                receive_messages(
                    sock,
                    buffer
                )
            )

            if not connected:

                print(
                    "\nServer disconnected."
                )

                running = False
                break

            for message in messages:

                handle_message(
                    message
                )

    except (
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError
    ):

        print(
            "\nConnection to server lost."
        )

        running = False


# --------------------------------------------------
# MAIN CLIENT
# --------------------------------------------------

def main():

    global running

    print(
        "===================="
    )

    print(
        "5-CARD DRAW POKER"
    )

    print(
        "===================="
    )

    # Ask before connecting so the server is not left
    # waiting for CONNECT while the player types.
    name = input(
        "Enter your name: "
    ).strip()

    if not name:
        name = "Player"

    # Connect to server.
    try:

        sock.connect(
            (
                SERVER_HOST,
                PORT
            )
        )

    except ConnectionRefusedError:

        print(
            "Could not connect to server."
        )

        return

    except OSError as error:

        print(
            "Connection error:",
            error
        )

        return

    print(
        f"Connected to "
        f"{SERVER_HOST}:{PORT}"
    )

    # Send CONNECT.
    connect_message = create_message(
        "CONNECT",
        "UNASSIGNED",
        {
            "name": name
        }
    )

    send_message(
        sock,
        connect_message
    )

    # Start listening for server messages.
    # Daemon, so Ctrl+C can exit even while the thread is
    # waiting at an input() prompt.
    receive_thread = threading.Thread(
        target=receive_loop,
        daemon=True
    )

    receive_thread.start()

    # Wait until receive thread finishes
    try:
        receive_thread.join()

    except KeyboardInterrupt:

        # Leaving without DISCONNECT keeps the match saved.
        print(
            "\nConnection closed. Reconnect with the same "
            "name to resume."
        )

    try:
        sock.close()

    except OSError:
        pass

    print(
        "Disconnected."
    )


if __name__ == "__main__":
    main()