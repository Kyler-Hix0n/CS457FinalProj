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

def take_turn():

    print("\nYour turn.")

    print("1. Check")
    print("2. Bet")
    print("3. Call")
    print("4. Fold")
    print("5. Draw")
    print("Q. Quit")

    choice = input(
        "> "
    ).strip().lower()

    # CHECK
    if choice == "1":

        send_action(
            "CHECK"
        )

    # BET
    elif choice == "2":

        try:

            amount = int(
                input(
                    "Bet amount: "
                )
            )

            send_action(
                "BET",
                amount=amount
            )

        except ValueError:

            print(
                "Bet must be a number."
            )

    # CALL
    elif choice == "3":

        send_action(
            "CALL"
        )

    # FOLD
    elif choice == "4":

        send_action(
            "FOLD"
        )

    # DRAW
    elif choice == "5":

        raw = input(
            "Card positions to replace "
            "(example: 0,2,4): "
        )

        try:

            indexes = [
                int(value.strip())
                for value in raw.split(",")
                if value.strip()
            ]

            send_action(
                "DRAW",
                cards=indexes
            )

        except ValueError:

            print(
                "Invalid card indexes."
            )

    # QUIT
    elif choice == "q":

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

    else:

        print(
            "Invalid selection."
        )


# ------------------------------------------------------
# HANDLE SERVER MESSAGES
# --------------------------------------------------------

def handle_message(message):

    global player_id
    global running

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

        if (
            payload["active_player"]
            == player_id
        ):

            take_turn()

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

        running = False


# --------------------------------------------------
# RECEIVE THREAD
# --------------------------------------------------

def receive_loop():

    global running

    buffer = ""

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

    # Get player's display name.
    name = input(
        "Enter your name: "
    ).strip()

    if not name:
        name = "Player"

    # Send CONNECT.
    connect_message = create_message(
        "CONNECT",
        name
    )

    send_message(
        sock,
        connect_message
    )

    # Start listening for server messages.
    receive_thread = threading.Thread(
        target=receive_loop
    )

    receive_thread.start()

    # Wait until receive thread finishes
    receive_thread.join()

    try:
        sock.close()

    except OSError:
        pass

    print(
        "Disconnected."
    )


if __name__ == "__main__":
    main()