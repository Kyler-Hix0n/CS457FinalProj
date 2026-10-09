# CS457 Sprint 1 — Application Protocol Blueprint

## 1. Purpose and architecture

This specification defines the application-layer protocol for a two-player, terminal-based Five-Card Draw Poker game. A Python TCP server is authoritative for player assignment, deck order, hands, legal actions, rounds, scores, streaks, and match outcome. Two clients send requests and render server responses. This is the **target protocol design**; not every gameplay rule below is implemented in the current prototype.

## 2. Transport, encoding, and framing

- **Transport:** TCP over IPv4 (`AF_INET`, `SOCK_STREAM`). Local development uses port **5050**; CML clients must use the server's configured address and the same port.
- **Serialization:** one compact JSON object per message, encoded as UTF-8.
- **Frame boundary:** one LF byte (`0x0A`, written `\n`) after each serialized JSON object. Embedded newlines in JSON strings are escaped by the JSON serializer. A message is not complete until LF is received.
- **Send:** `sock.sendall((json.dumps(message, separators=(',', ':')) + '\n').encode('utf-8'))`.
- **Receive:** maintain a **per-connection byte buffer**, append each `recv()` chunk, extract each LF-terminated frame in a loop, decode UTF-8 and parse JSON only after a complete frame has been extracted. Keep any trailing incomplete bytes for the next `recv()` call.
- **Resource limits (planned hardening):** reject oversized frames (e.g., >64 KiB) and unreasonable nesting/payloads; do not allow unbounded buffer growth.

### Raw wire examples

Two back-to-back messages in one continuous TCP byte stream (the displayed `\n` represents an actual LF byte):

```text
{"msg_type":"CONNECT","player_id":"UNASSIGNED","payload":{"name":"Alex"},"timestamp":1727000000}\n{"msg_type":"ACTION","player_id":"Player_1","payload":{"action":"CHECK"},"timestamp":1727000005}\n
```

**Fragmentation:** `recv()` #1 may contain `{"msg_type":"CON`; `recv()` #2 may contain the remainder through `\n`. The parser retains #1 until #2 arrives. **Coalescing:** one `recv()` may contain both complete lines; the parser extracts and validates two messages. TCP packet boundaries never define message boundaries.

## 3. Envelope schema

Every message is a JSON object with these required fields:

| Field | Type | Meaning |
|---|---|---|
| `msg_type` | string | Protocol message discriminator (uppercase) |
| `player_id` | string | `Player_1`, `Player_2`, `SERVER`, or `UNASSIGNED` during initial join |
| `payload` | object | Message-specific fields; `{}` when empty |
| `timestamp` | integer | Unix epoch seconds assigned by sender |

**Trust boundary:** The server identifies the player from the accepted socket, not from a client-supplied `player_id`. The timestamp is diagnostic, not an authorization or ordering mechanism. TCP provides ordered bytes within each connection; the server determines authoritative action order.

## 4. Message catalog and exact payload contracts

All examples show complete envelopes. The exact payloads below are the **intended contract** and should be reconciled with the final implementation before the final-project submission.

### `CONNECT` — Client → Server

Purpose: request to join; server assigns the socket a role. Payload: `name` (nonempty string, display alias).

```json
{"msg_type":"CONNECT","player_id":"UNASSIGNED","payload":{"name":"Alex"},"timestamp":1727000000}
```

### `LOBBY_WAIT` — Server → Client

Purpose: report waiting-room status. Payload: `connected` (integer 0–2), `required` (integer, 2), `message` (string).

```json
{"msg_type":"LOBBY_WAIT","player_id":"SERVER","payload":{"connected":1,"required":2,"message":"Waiting for opponent"},"timestamp":1727000001}
```

### `GAME_START` — Server → Each client

Purpose: assign role and announce match configuration. Payload: `your_player_id` (string), `opponent_id` (string), `first_player` (string), `max_rounds` (integer), `round` (integer).

```json
{"msg_type":"GAME_START","player_id":"SERVER","payload":{"your_player_id":"Player_1","opponent_id":"Player_2","first_player":"Player_1","max_rounds":10,"round":1},"timestamp":1727000002}
```

### `HAND` — Server → One client only

Purpose: privately deliver the recipient's cards. Payload: `cards` (array of five unique card strings such as `AH`, `10D`), `round` (integer). **Never broadcast both hands.**

```json
{"msg_type":"HAND","player_id":"SERVER","payload":{"cards":["AH","10D","3S","KC","7H"],"round":1},"timestamp":1727000003}
```

### `ACTION` — Client → Server

Purpose: request a game move. Payload: `action` (one of `CHECK`, `BET`, `CALL`, `FOLD`, `DRAW`); for `BET`, `amount` (positive integer); for `DRAW`, `cards` (array of distinct integer indices 0–4, including empty array if standing pat is allowed).

```json
{"msg_type":"ACTION","player_id":"Player_1","payload":{"action":"DRAW","cards":[1,3]},"timestamp":1727000005}
```

```json
{"msg_type":"ACTION","player_id":"Player_1","payload":{"action":"BET","amount":10},"timestamp":1727000006}
```

### `STATE_UPDATE` — Server → Both clients

Purpose: publish public state after a legal action or transition. Payload: `round` (integer), `phase` (string), `active_player` (string or null), `last_action` (string), `wins` (object mapping player IDs to integers), `streaks` (same structure). No private cards.

```json
{"msg_type":"STATE_UPDATE","player_id":"SERVER","payload":{"round":1,"phase":"DRAW_PHASE","active_player":"Player_2","last_action":"Player_1 DRAW","wins":{"Player_1":0,"Player_2":0},"streaks":{"Player_1":0,"Player_2":0}},"timestamp":1727000007}
```

### `ROUND_OVER` — Server → Both clients

Purpose: publish round outcome. Payload: `round` (integer), `winner` (player ID or null for tie), `reason` (e.g. `SHOWDOWN`, `FOLD`, `TIE`), `hand_names` (object, if showdown), `wins` and `streaks` (objects). Showdown disclosure of hands should be defined separately if desired.

```json
{"msg_type":"ROUND_OVER","player_id":"SERVER","payload":{"round":1,"winner":"Player_1","reason":"SHOWDOWN","hand_names":{"Player_1":"Two Pair","Player_2":"One Pair"},"wins":{"Player_1":1,"Player_2":0},"streaks":{"Player_1":1,"Player_2":0}},"timestamp":1727000010}
```

### `ERROR` — Server → One client

Purpose: reject malformed, unauthorized, or illegal requests without changing game state. Payload: `code` (string), `message` (string). Suggested codes: `MALFORMED_JSON`, `INVALID_MESSAGE`, `INVALID_ACTION`, `INVALID_BET`, `INVALID_CARD_INDEX`, `OUT_OF_TURN`, `INVALID_STATE`, `GAME_FULL`.

```json
{"msg_type":"ERROR","player_id":"SERVER","payload":{"code":"OUT_OF_TURN","message":"Wait for your turn"},"timestamp":1727000008}
```

### `DISCONNECT` — Client → Server

Purpose: intentional departure before socket closure. Payload: `reason` (string such as `QUIT`). The server treats a departure during an active match as a forfeit.

```json
{"msg_type":"DISCONNECT","player_id":"Player_2","payload":{"reason":"QUIT"},"timestamp":1727000011}
```

### `GAME_OVER` — Server → Both clients when reachable

Purpose: publish match result. Payload: `winner` (player ID or null), `reason` (`THREE_CONSECUTIVE_WINS`, `ROUND_LIMIT_DRAW`, `FORFEIT`), `rounds_played` (integer), `wins` (object).

```json
{"msg_type":"GAME_OVER","player_id":"SERVER","payload":{"winner":"Player_1","reason":"FORFEIT","rounds_played":2,"wins":{"Player_1":1,"Player_2":1}},"timestamp":1727000012}
```

## 5. Validation and authoritative handling

1. Reject non-JSON data, non-object JSON, missing envelope fields, incorrect types, unknown message types, and unexpected client-to-server message types.
2. Bind the request to the **socket's assigned player role**, never blindly trust the claimed `player_id`.
3. For `ACTION`, validate required fields and values. `BET.amount` must be a positive integer (not a Boolean); `DRAW.cards` must be a list of unique integer indices 0–4.
4. Check game state, phase, turn ownership, and whether an action is currently legal **before mutating game state**. Invalid and out-of-turn actions produce `ERROR` and leave state unchanged.
5. Lock shared server state while validating and applying moves. Send private hands only to their owners; broadcast only public state.

## 6. Connection lifecycle and failure paths

**Graceful departure:** Client sends `DISCONNECT`, then closes its socket. TCP normally performs FIN-based teardown. Server removes that player, closes resources, and awards a forfeit to the connected opponent if a match is underway.

**EOF:** A zero-length result from `recv()` (`b''`) indicates that the peer's sending direction has closed. The receive loop must `break` and run cleanup; it must not repeatedly call `recv()` in a busy loop.

**Abrupt termination:** Handle `ConnectionResetError`, `BrokenPipeError`, `ConnectionAbortedError`, and applicable timeouts. A network outage may not immediately generate a TCP RST; configured timeouts or keepalives are needed for bounded failure detection. Once disconnection is confirmed, transition to forfeit/cleanup rather than crashing the whole server.

**Cleanup:** Avoid double-awarding a forfeit if EOF and a send failure race. Remove the socket from the player map, close it once, and prevent actions after `GAME_OVER`.

## 7. Design/implementation alignment notes

The existing prototype includes TCP, JSON, newline framing, player roles, private hands, and basic action handling. The **planned** phase-aware betting rules, repeated-draw prevention, full bet/call semantics, and first-player probability adjustment must be implemented or explicitly revised in the final specification. Do not claim they are complete merely because they appear here.
