import json
import time


VALID_CLIENT_MESSAGES = {
    "CONNECT",
    "ACTION",
    "DISCONNECT"
}

# Placeholder produced by receive_messages() for an unparseable line.
MALFORMED = "MALFORMED"

VALID_ACTIONS = {
    "CHECK",
    "BET",
    "CALL",
    "FOLD",
    "DRAW",
    "ALL_IN"
}


def create_message(msg_type, player_id, payload=None):
    if payload is None:
        payload = {}

    return {
        "msg_type": msg_type,
        "player_id": player_id,
        "payload": payload,
        "timestamp": int(time.time())
    }


def send_message(sock, message):
    """
    Convert a Python dictionary into newline-delimited JSON
    and send it through the TCP connection.
    """
    data = json.dumps(message) + "\n"
    sock.sendall(data.encode("utf-8"))


def receive_messages(sock, buffer=b""):
    """
    Receive TCP data and extract all complete newline-delimited
    JSON messages.

    The buffer holds raw bytes so a multi-byte UTF-8 character
    split across two recv() calls is decoded only once complete.

    Returns:
        messages  - list of complete decoded messages
        buffer    - incomplete data that must be saved
        connected - False when TCP EOF occurs
    """

    data = sock.recv(4096)

    # TCP EOF
    if not data:
        return [], buffer, False

    buffer += data

    messages = []

    # There may be multiple messages in one recv()
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)

        if not line.strip():
            continue

        try:
            message = json.loads(line.decode("utf-8"))
            messages.append(message)

        except (json.JSONDecodeError, UnicodeDecodeError):
            messages.append({
                "msg_type": MALFORMED
            })

    return messages, buffer, True


def validate_message(message):
    """
    Validate the common message structure.
    """

    if not isinstance(message, dict):
        return False, "INVALID_SCHEMA"

    if message.get("msg_type") == MALFORMED:
        return False, "MALFORMED_JSON"

    required = {
        "msg_type",
        "player_id",
        "payload",
        "timestamp"
    }

    if not required.issubset(message):
        return False, "INVALID_SCHEMA"

    if not isinstance(message["msg_type"], str):
        return False, "INVALID_SCHEMA"

    if not isinstance(message["player_id"], str):
        return False, "INVALID_SCHEMA"

    if not isinstance(message["payload"], dict):
        return False, "INVALID_SCHEMA"

    if not isinstance(message["timestamp"], int):
        return False, "INVALID_SCHEMA"

    if message["msg_type"] not in VALID_CLIENT_MESSAGES:
        return False, "UNKNOWN_MESSAGE"

    return True, None


def validate_action(payload):
    """
    Validate the payload of an ACTION message.
    """

    if "action" not in payload:
        return False, "INVALID_ACTION"

    action = payload["action"]

    if action not in VALID_ACTIONS:
        return False, "INVALID_ACTION"

    # BET requires a positive integer amount.
    if action == "BET":
        amount = payload.get("amount")

        # bool is a subclass of int, so reject it explicitly.
        if not isinstance(amount, int) or isinstance(amount, bool):
            return False, "INVALID_BET"

        if amount <= 0:
            return False, "INVALID_BET"

    # DRAW requires unique card indexes from 0 through 4.
    if action == "DRAW":
        cards = payload.get("cards")

        if not isinstance(cards, list):
            return False, "INVALID_CARD_INDEX"

        for index in cards:
            if not isinstance(index, int) or isinstance(index, bool):
                return False, "INVALID_CARD_INDEX"

            if index < 0 or index > 4:
                return False, "INVALID_CARD_INDEX"

        # Checked after the type loop: set() fails on unhashable items.
        if len(cards) != len(set(cards)):
            return False, "INVALID_CARD_INDEX"

    return True, None