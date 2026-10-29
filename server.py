import socket
import threading
import time

from protocol import (
    create_message,
    send_message,
    receive_messages,
    validate_message,
    validate_action
)

from game import (
    PokerGame,
    STARTING_CHIPS,
    hand_name,
    other_player
)


HOST = "0.0.0.0"
PORT = 5050

# Bounds how long a sendall() can block while game_lock is held.
# A recv() that hits this timeout is just an idle wake-up.
SOCKET_TIMEOUT = 30

# How often the accept loop wakes up to check for game over.
ACCEPT_TIMEOUT = 1.0

# How long shutdown waits for clients to close on their own.
SHUTDOWN_GRACE = 5

# How long a player who drops mid-match has to reconnect
# before they forfeit.
RECONNECT_TIMEOUT = 120


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
#
# Everything in this section is protected by game_lock.
# Functions ending in _locked, and every helper below that
# sends or touches the game, expect the caller to hold it.
# Only the entry points (accept loop, process_message,
# handle_disconnect, shutdown) acquire it.
# --------------------------------------------------

# Connected sockets by role.
players = {}

# (name, ip) of each seated player. Kept after a player drops
# mid-match so they can reclaim their seat.
identities = {}

# Players who dropped mid-match: role -> time.monotonic()
# deadline for reconnecting.
reconnect_deadlines = {}

worker_threads = []

game = PokerGame(
    max_rounds=10
)

# Sequence number of the last STATE_UPDATE sent.
state_seq = 0

game_lock = threading.Lock()

game_over_event = threading.Event()


# --------------------------------------------------
# MESSAGE HELPERS (caller holds game_lock)
# --------------------------------------------------

def send_to(player_id, message):
    """
    Send to one player. A failed send is treated as a
    disconnect of that player.
    """

    sock = players.get(
        player_id
    )

    if sock is None:
        return

    try:
        send_message(
            sock,
            message
        )

    except OSError:
        _disconnect_locked(
            player_id,
            sock
        )


def send_error(sock, code, text):

    message = create_message(
        "ERROR",
        "SERVER",
        {
            "code": code,
            "message": text
        }
    )

    try:
        send_message(
            sock,
            message
        )

    except OSError:
        # The worker's recv() loop will see the failure too.
        pass


def broadcast(message):
    """
    Send a message to every connected player.
    """

    dead_players = []

    # Snapshot so a disconnect below cannot change the dict
    # while we iterate.
    for player_id, sock in list(players.items()):

        try:
            send_message(
                sock,
                message
            )

        except OSError:
            # BrokenPipeError, ConnectionResetError,
            # ConnectionAbortedError and TimeoutError
            # are all subclasses of OSError.
            dead_players.append(
                (player_id, sock)
            )

    for player_id, sock in dead_players:
        _disconnect_locked(
            player_id,
            sock
        )


def broadcast_state(last_action=None):
    """
    Send the current public game state to both players.
    """

    global state_seq

    state_seq += 1

    payload = game.public_state()

    payload["last_action"] = last_action
    payload["seq"] = state_seq

    message = create_message(
        "STATE_UPDATE",
        "SERVER",
        payload
    )

    broadcast(message)


def send_state_to(player_id, last_action):
    """
    Send the current public state to one player without
    advancing seq, so the other player sees no gap.
    """

    payload = game.public_state()

    payload["last_action"] = last_action
    payload["seq"] = state_seq

    send_to(
        player_id,
        create_message(
            "STATE_UPDATE",
            "SERVER",
            payload
        )
    )


def send_hand(player_id):
    """
    Send a player's private hand only to that player.
    """

    hand = game.get_hand(
        player_id
    )

    message = create_message(
        "HAND",
        "SERVER",
        {
            "cards": hand,
            "round": game.round_number
        }
    )

    send_to(
        player_id,
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
# GAME START (caller holds game_lock)
# --------------------------------------------------

def game_start_message(player_id, resumed=False):

    return create_message(
        "GAME_START",
        "SERVER",
        {
            "your_role": player_id,
            "opponent": other_player(player_id),
            "round": game.round_number,
            "max_rounds": game.max_rounds,
            "first_player": game.first_player,
            "starting_chips": STARTING_CHIPS,
            "resumed": resumed
        }
    )


def start_game():

    game.start_game()

    for player_id in list(players):

        send_to(
            player_id,
            game_start_message(
                player_id
            )
        )

    send_all_hands()

    broadcast_state(
        "GAME_START"
    )


# --------------------------------------------------
# ROUND MANAGEMENT (caller holds game_lock)
# --------------------------------------------------

def end_round(winner, reason="SHOWDOWN"):

    winning_hand = None

    if reason == "SHOWDOWN":

        winner, p1_score, p2_score = game.showdown()

        if winner == "Player_1":
            winning_hand = hand_name(
                p1_score
            )

        elif winner == "Player_2":
            winning_hand = hand_name(
                p2_score
            )

    game.record_round_win(
        winner
    )

    message = create_message(
        "ROUND_OVER",
        "SERVER",
        {
            "round": game.round_number,
            "winner": winner,
            "reason": reason,
            "winning_hand": winning_hand,
            "pot": game.pot,
            "player_1_chips": game.chips["Player_1"],
            "player_2_chips": game.chips["Player_2"],
            "player_1_wins": game.player_1_wins,
            "player_2_wins": game.player_2_wins,
            "player_1_streak": game.player_1_streak,
            "player_2_streak": game.player_2_streak
        }
    )

    broadcast(message)

    # A send failure during the broadcast may already
    # have ended the match by forfeit.
    if game.state == "GAME_OVER":
        return

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

    if game.state == "GAME_OVER":
        return

    game.state = "GAME_OVER"

    message = create_message(
        "GAME_OVER",
        "SERVER",
        {
            "winner": winner,
            "reason": reason,
            "rounds_played": game.round_number,
            "player_1_wins": game.player_1_wins,
            "player_2_wins": game.player_2_wins,
            "player_1_chips": game.chips["Player_1"],
            "player_2_chips": game.chips["Player_2"]
        }
    )

    broadcast(message)

    game_over_event.set()


# --------------------------------------------------
# PLAYER ACTIONS (caller holds game_lock)
# --------------------------------------------------

def handle_action(player_id, sock, payload):
    """
    Validate in order, and change nothing until every
    check has passed.
    """

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

    if len(players) < 2:

        send_error(
            sock,
            "GAME_PAUSED",
            "Your opponent disconnected. "
            "Waiting for them to reconnect."
        )

        return

    if player_id != game.active_player:

        send_error(
            sock,
            "OUT_OF_TURN",
            "It is not your turn."
        )

        return

    illegal = game.action_error(
        player_id,
        payload
    )

    if illegal:

        code, text = illegal

        send_error(
            sock,
            code,
            text
        )

        return

    # All checks passed: mutate, then broadcast.
    action = payload["action"]

    last_action = f"{player_id} {action}"

    if action == "BET":
        last_action += f" {payload['amount']}"

    if action == "ALL_IN":
        last_action += f" {game.all_in_amount(player_id)}"

    result = game.apply_action(
        player_id,
        payload
    )

    # Sent before any showdown so the player sees the cards
    # they drew (a draw goes straight to showdown when a
    # player is all in).
    if action == "DRAW":

        send_hand(
            player_id
        )

        # A failed send may have ended the match by forfeit.
        if game.state == "GAME_OVER":
            return

    if result == "FOLD":

        end_round(
            other_player(player_id),
            "FOLD"
        )

        return

    if result == "SHOWDOWN":

        end_round(
            None,
            "SHOWDOWN"
        )

        return

    broadcast_state(
        last_action
    )


# --------------------------------------------------
# DISCONNECT HANDLING
# --------------------------------------------------

def handle_disconnect(player_id, sock, forfeit=False):

    with game_lock:

        _disconnect_locked(
            player_id,
            sock,
            forfeit
        )


def _disconnect_locked(player_id, sock, forfeit=False):
    """
    Remove a player's socket. Safe to call more than once for
    the same departure: only the first call does anything.

    forfeit=True (the player sent DISCONNECT) ends the match.
    Otherwise a mid-match drop pauses the game and holds the
    seat for RECONNECT_TIMEOUT seconds.
    """

    # Also ignores a stale socket whose seat was already
    # reclaimed by a reconnect.
    if players.get(player_id) is not sock:
        return

    print(
        player_id,
        "disconnected"
    )

    players.pop(
        player_id
    )

    try:
        sock.close()

    except OSError:
        pass

    if game.state == "WAITING_FOR_PLAYERS":

        # Lobby: free the seat for anyone.
        identities.pop(
            player_id,
            None
        )

        return

    if game.state == "GAME_OVER":
        return

    opponent = other_player(
        player_id
    )

    if forfeit:

        end_game(
            opponent,
            "FORFEIT"
        )

        return

    reconnect_deadlines[player_id] = (
        time.monotonic() + RECONNECT_TIMEOUT
    )

    send_to(
        opponent,
        create_message(
            "PLAYER_DISCONNECTED",
            "SERVER",
            {
                "player": player_id,
                "reconnect_timeout": RECONNECT_TIMEOUT
            }
        )
    )


def expire_reconnects():
    """
    Forfeit any player who did not reconnect in time.
    Called by the main thread about once a second.
    """

    with game_lock:

        now = time.monotonic()

        for player_id, deadline in list(
            reconnect_deadlines.items()
        ):

            if now < deadline:
                continue

            del reconnect_deadlines[player_id]

            print(
                player_id,
                "did not reconnect in time"
            )

            opponent = other_player(
                player_id
            )

            if opponent in players:
                end_game(opponent, "FORFEIT")

            else:
                # Both players are gone.
                end_game(None, "ABANDONED")


# --------------------------------------------------
# MESSAGE DISPATCHER
# --------------------------------------------------

def process_message(player_id, sock, message):

    # Envelope check needs no shared state, so no lock.
    valid, error = validate_message(
        message
    )

    if not valid:

        # Lock only to keep this send from interleaving
        # with a broadcast on the same socket.
        with game_lock:

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
            player_id,
            sock,
            forfeit=True
        )

    # A second CONNECT after joining is ignored.


# --------------------------------------------------
# CLIENT THREAD
# --------------------------------------------------

def handle_client(sock, address):

    print(
        "Connection from",
        address
    )

    # Assigned once this connection's CONNECT is accepted.
    player_id = None

    # Per-connection framing buffer: local to this thread.
    buffer = b""

    try:

        while True:

            try:

                # Never hold game_lock while blocked in recv().
                messages, buffer, connected = (
                    receive_messages(
                        sock,
                        buffer
                    )
                )

            except TimeoutError as error:

                # settimeout() expired with no data. A joined
                # player is just waiting for their opponent; a
                # connection that never sent CONNECT is dropped.
                # A kernel ETIMEDOUT (dead peer) has an errno.
                if error.errno is None and player_id is not None:
                    continue

                raise

            # recv() returned b""
            if not connected:
                break

            for message in messages:

                if player_id is None:

                    player_id, rejected = process_join(
                        sock,
                        address,
                        message
                    )

                    if rejected:
                        return

                    continue

                process_message(
                    player_id,
                    sock,
                    message
                )

            # DISCONNECT (or a failed send) already removed
            # this socket.
            if (
                player_id is not None
                and players.get(player_id) is not sock
            ):
                break

    except OSError as error:

        # Reset, abort, broken pipe, timeout, or the socket
        # was closed by another thread during cleanup.
        print(
            "Connection error for",
            player_id or address,
            ":",
            error
        )

    finally:

        if player_id is None:

            try:
                sock.close()

            except OSError:
                pass

        else:

            handle_disconnect(
                player_id,
                sock
            )


# --------------------------------------------------
# JOINING AND RECONNECTING
# --------------------------------------------------

def process_join(sock, address, message):
    """
    Handle a message from a connection that has no seat yet.
    Returns (player_id, rejected). player_id is None until a
    CONNECT is accepted; rejected means the socket was closed.
    """

    valid, error = validate_message(
        message
    )

    name = None

    if valid and message["msg_type"] == "CONNECT":

        name = message["payload"].get(
            "name"
        )

        if isinstance(name, str):
            name = name.strip()[:32]

    with game_lock:

        if not valid:

            send_error(
                sock,
                error,
                "Invalid protocol message."
            )

            return None, False

        if message["msg_type"] != "CONNECT":

            send_error(
                sock,
                "NOT_JOINED",
                "Send CONNECT before anything else."
            )

            return None, False

        if not name:

            send_error(
                sock,
                "INVALID_NAME",
                "CONNECT needs a non-empty name."
            )

            return None, False

        player_id, rejected = _join_locked(
            sock,
            address,
            name
        )

    try_start_game()

    return player_id, rejected


def _reject_locked(sock, code, text):

    send_error(
        sock,
        code,
        text
    )

    try:
        sock.close()

    except OSError:
        pass

    return None, True


def _join_locked(sock, address, name):
    """
    Seat a new player in the lobby, or give a dropped player
    their seat back. The identity is the name plus the IP the
    player connects from.
    """

    identity = (
        name,
        address[0]
    )

    if game.state == "WAITING_FOR_PLAYERS":

        if len(players) >= 2:
            return _reject_locked(
                sock,
                "GAME_FULL",
                "Two players are already connected."
            )

        # Names must be unique so a returning player can be
        # matched to exactly one seat.
        if any(
            seated_name == name
            for seated_name, _ in identities.values()
        ):
            return _reject_locked(
                sock,
                "NAME_TAKEN",
                "That name is already taken."
            )

        if "Player_1" not in players:
            player_id = "Player_1"

        else:
            player_id = "Player_2"

        players[player_id] = sock
        identities[player_id] = identity

        print(
            player_id,
            "is",
            name,
            "from",
            address[0]
        )

        send_to(
            player_id,
            create_message(
                "LOBBY_WAIT",
                "SERVER",
                {
                    "players_connected": len(players),
                    "players_required": 2
                }
            )
        )

        # LOBBY_WAIT may have failed and removed the player.
        if players.get(player_id) is not sock:
            return None, True

        return player_id, False

    if game.state == "GAME_OVER":
        return _reject_locked(
            sock,
            "GAME_FULL",
            "The match is over."
        )

    for player_id in reconnect_deadlines:

        if identities.get(player_id) == identity:

            _resume_locked(
                player_id,
                sock
            )

            return player_id, False

    if reconnect_deadlines:
        return _reject_locked(
            sock,
            "RECONNECT_REJECTED",
            "A match is paused. Only the player who left can "
            "rejoin, using the same name from the same computer."
        )

    return _reject_locked(
        sock,
        "GAME_FULL",
        "Two players are already connected."
    )


def _resume_locked(player_id, sock):
    """
    Put a returning player back in their seat and bring
    their screen up to date.
    """

    del reconnect_deadlines[player_id]

    players[player_id] = sock

    print(
        player_id,
        "reconnected"
    )

    send_to(
        player_id,
        game_start_message(
            player_id,
            resumed=True
        )
    )

    send_hand(
        player_id
    )

    send_state_to(
        player_id,
        f"{player_id} reconnected"
    )

    send_to(
        other_player(player_id),
        create_message(
            "PLAYER_RECONNECTED",
            "SERVER",
            {
                "player": player_id
            }
        )
    )


def try_start_game():

    with game_lock:

        if (
            len(players) == 2
            and game.state == "WAITING_FOR_PLAYERS"
        ):

            start_game()


# --------------------------------------------------
# SHUTDOWN
# --------------------------------------------------

def shutdown():

    # Give clients a moment to read GAME_OVER and close
    # their end first, so no unread data triggers a reset.
    for thread in worker_threads:
        thread.join(
            SHUTDOWN_GRACE
        )

    with game_lock:

        for sock in players.values():

            try:
                sock.close()

            except OSError:
                pass

        players.clear()

    server_socket.close()

    print(
        "Server shut down."
    )


# --------------------------------------------------
# MAIN SERVER
# --------------------------------------------------

def main():

    server_socket.bind(
        (HOST, PORT)
    )

    server_socket.listen(2)

    # Wake up regularly so the loop can notice game over.
    server_socket.settimeout(
        ACCEPT_TIMEOUT
    )

    print(
        f"Poker server listening on port {PORT}"
    )

    try:

        # Keep accepting for the whole match so a third
        # client is rejected with GAME_FULL instead of
        # hanging in the backlog.
        while not game_over_event.is_set():

            expire_reconnects()

            try:
                sock, address = (
                    server_socket.accept()
                )

            except TimeoutError:
                continue

            # The accepted socket must not inherit the
            # listening socket's short timeout.
            sock.settimeout(
                SOCKET_TIMEOUT
            )

            # Lets the OS notice a peer that vanished without
            # sending FIN or RST.
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_KEEPALIVE,
                1
            )

            # The worker reads CONNECT and decides whether this
            # is a new player, a returning one, or a rejection.
            thread = threading.Thread(
                target=handle_client,
                args=(
                    sock,
                    address
                ),
                daemon=True
            )

            worker_threads.append(
                thread
            )

            thread.start()

    except KeyboardInterrupt:

        print(
            "\nServer interrupted."
        )

    finally:

        shutdown()


if __name__ == "__main__":
    main()
