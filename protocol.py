import json
import time


VALID_CLIENT_MESSAGES = {
    "CONNECT",
    "ACTION",
    "DISCONNECT"
}

VALID_ACTIONS = {
    "CHECK",
    "BET",
    "CALL",
    "FOLD",
    "DRAW"
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


def receive_messages(sock, buffer=""):
    """
    Receive TCP data and extract all complete newline-delimited
    JSON messages.

    Returns:
        messages  - list of complete decoded messages
        buffer    - incomplete data that must be saved
        connected - False when TCP EOF occurs
    """

    data = sock.recv(4096)

    # TCP EOF
    if not data:
        return [], buffer, False

    buffer += data.decode("utf-8")

    messages = []

    # There may be multiple messages in one recv()
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)

        if not line:
            continue

        try:
            message = json.loads(line)
            messages.append(message)

        except json.JSONDecodeError:
            messages.append({
                "msg_type": "INVALID"
            })

    return messages, buffer, True


def validate_message(message):
    """
    Validate the common message structure.
    """

    if not isinstance(message, dict):
        return False, "INVALID_SCHEMA"

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

        if not isinstance(amount, int):
            return False, "INVALID_BET"

        if amount <= 0:
            return False, "INVALID_BET"

    # DRAW requires unique card indexes from 0 through 4.
    if action == "DRAW":
        cards = payload.get("cards")

        if not isinstance(cards, list):
            return False, "INVALID_CARD_INDEX"

        if len(cards) != len(set(cards)):
            return False, "INVALID_CARD_INDEX"

        for index in cards:
            if not isinstance(index, int):
                return False, "INVALID_CARD_INDEX"

            if index < 0 or index > 4:
                return False, "INVALID_CARD_INDEX"

    return True, None