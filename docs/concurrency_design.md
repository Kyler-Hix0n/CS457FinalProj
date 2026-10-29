# CS457 Sprint 2 — Server Concurrency & Game Behavior Architecture

## 1. Scope

This document specifies the concurrency model, thread-safety strategy, turn synchronization scheme, and score broadcast design for the two-player Five-Card Draw Poker server. It builds on `protocol_blueprint.md` (message catalog, LF-framed JSON) and `fsm_specification.md` (game states). `server.py`, `game.py`, `protocol.py`, and `client.py` implement this design. Section 10 maps each requirement to the code.

## 2. Concurrency Model Selection

**Chosen model: Option A — Multi-Threading (`threading.Thread`, one worker thread per client socket).**

### 2.1 Rationale

| Factor | Multi-threading (chosen) | `selectors` event loop (rejected) |
|---|---|---|
| Client count | Fixed at 2 players. Two worker threads use very little memory. | Built for hundreds or thousands of sockets. That scale isn't needed here. |
| Receive code | Blocking `recv()` in a simple `while True` loop for each client | Non-blocking reads plus a per-socket read buffer and a per-socket write queue, with `EVENT_WRITE` handling for partial sends |
| Shared state | Needs an explicit `threading.Lock` | Runs in a single thread, so it needs no locks |
| Fit with existing code | `server.py`, `protocol.receive_messages()`, and `PokerGame` already use this model | Would need the I/O layer rewritten |
| Main risk | Race conditions on shared state, which the locking strategy in Section 5 controls | A single slow handler stalls every client |

Poker is turn-based and has exactly two players, so contention is low. The cost of threading is the need for disciplined locking, and Section 5 makes that explicit and auditable.

### 2.2 Why the main loop never blocks on a client

Each client gets its own worker thread, which spends nearly all of its time blocked in `sock.recv()`. A blocked `recv()` only suspends that worker; it does not suspend the main thread or the other worker. The main thread only blocks in `accept()`, which has a 1 s timeout so the loop can check `game_over_event` and expired reconnect deadlines regularly. It never reads from a client socket.

### 2.3 Thread inventory

| Thread | Count | Created by | Responsibilities | Blocking calls |
|---|---|---|---|---|
| Main thread | 1 | Python interpreter | `bind`/`listen`, accept loop (runs for the whole match), configuring and handing each new socket to a worker, forfeiting players whose reconnect deadline passed, final shutdown | `accept()` (1 s timeout), `thread.join()` at shutdown |
| Client worker | 1 per accepted connection (2 players, plus short-lived ones for rejected or returning clients) | Main thread | Read `CONNECT` and join, rejoin, or reject; starting the game when the second player joins; receive, frame, and parse messages for one socket; validate and apply actions; broadcast results; run disconnect cleanup | `recv()` and `game_lock.acquire()` |

Workers are created with `daemon=True`, so a worker stuck in `recv()` cannot keep the process alive after shutdown.

## 3. Connection Lifecycle

```text
 LISTEN ──► ACCEPT ──► JOIN (on CONNECT) ──► LOBBY ──► SERVICE (game loop) ──► TEARDOWN
                          │                                │       ▲
                          │                          drop  ▼       │ same name + IP
                          │                         PAUSED (seat held 120 s) ──► FORFEIT
                          └── 3rd client / wrong identity: ERROR, close
```

### 3.1 Listen

1. Create `socket.socket(AF_INET, SOCK_STREAM)`.
2. Set `SO_REUSEADDR` so the server can restart right away while the old port is in `TIME_WAIT`.
3. Run `bind(("0.0.0.0", 5050))`, then `listen(2)`. The backlog of 2 holds both players while the main thread is handing the first one to its worker.

### 3.2 Accept (main thread) and join (worker thread)

For each `sock, address = server_socket.accept()`, the main thread:

1. Sets `sock.settimeout(SOCKET_TIMEOUT)` and enables `SO_KEEPALIVE` (see Section 5.4).
2. Starts `threading.Thread(target=handle_client, args=(sock, address), daemon=True)`. It does **not** assign a role: the role depends on the name in `CONNECT`, which only the worker reads.

The worker's first message must be `CONNECT {name}`. Anything else gets `ERROR NOT_JOINED`, and a connection that sends nothing for 30 s is closed. On `CONNECT`, the worker **acquires `game_lock`** and calls `_join_locked(sock, address, name)`. The player's identity is `(name, address[0])`, meaning the name plus the IP the connection came from.

- **Lobby (`WAITING_FOR_PLAYERS`):** if two players are seated, send `ERROR GAME_FULL` and close. If another seated player already uses the name, send `ERROR NAME_TAKEN` and close, so a returning player always matches exactly one seat. Otherwise assign the first free role (`Player_1`, then `Player_2`), insert `players[role] = sock` and `identities[role] = (name, ip)`, and send `LOBBY_WAIT {players_connected, players_required: 2}`.
- **Match paused for a dropped player:** if the identity equals `identities[role]` for a role in `reconnect_deadlines`, the connection retakes that role (Section 3.6). Any other connection gets `ERROR RECONNECT_REJECTED` and is closed.
- **Match full or over:** `ERROR GAME_FULL` and close.

The **socket identifies the player**. The role is bound to the socket here and never taken from a client-supplied `player_id`. The worker then **releases `game_lock`**.

### 3.3 Game start

After a join, the worker calls `try_start_game()`. That function acquires `game_lock`, confirms two players are connected and the game is still in the lobby, and calls `start_game()`. That function does the following:

- shuffles and deals
- picks `first_player`
- sends `GAME_START` to each client, with a role-specific `your_role` field
- sends each client its own `HAND` privately
- broadcasts the first `STATE_UPDATE`

The worker that seated the second player holds the lock for this whole sequence. That way, neither worker can process an `ACTION` until both clients have their cards and the initial state.

### 3.4 Active game loop (worker threads)

```python
def handle_client(sock, address):
    player_id = None                                              # no seat until CONNECT
    buffer = b""
    try:
        while True:
            messages, buffer, connected = receive_messages(sock, buffer)  # NO lock held
            if not connected:                                     # recv() == b""
                break
            for message in messages:
                if player_id is None:
                    player_id, rejected = process_join(sock, address, message)  # takes game_lock
                    if rejected:
                        return
                    continue
                process_message(player_id, sock, message)         # takes game_lock
            if player_id is not None and players.get(player_id) is not sock:
                break                                             # DISCONNECT or failed send
    except OSError:
        pass
    finally:
        if player_id is not None:
            handle_disconnect(player_id, sock)                    # takes game_lock
```

- `buffer` and `player_id` are **local variables of the worker**, so framing state is never shared between threads.
- The worker receives, frames, and checks schema (`validate_message`, `validate_action`) **without holding the lock**. These are pure functions of the bytes on one socket.
- `process_join` and `process_message` acquire `game_lock` before they read or change game state, and before they send anything (Section 5).

### 3.5 Teardown

Any of the following events triggers teardown for one player:

| Trigger | Detected by | Path | Result mid-match |
|---|---|---|---|
| `DISCONNECT {reason: QUIT}` | Worker parses the message | `handle_disconnect(player_id, sock, forfeit=True)` | Forfeit |
| Orderly close (FIN) | `recv()` returns `b""` | Loop `break` leads to the `finally` block | Paused, seat held |
| Reset or abort | `ConnectionResetError` / `ConnectionAbortedError` | `except` leads to the `finally` block | Paused, seat held |
| Silent network drop | `SO_KEEPALIVE` probes fail, and `recv()` raises `TimeoutError` with an errno (kernel `ETIMEDOUT`) | `except` leads to the `finally` block | Paused, seat held |
| Send to dead peer | `BrokenPipeError` during `broadcast` | Broadcast calls `_disconnect_locked(player_id, sock)` | Paused, seat held |

`handle_disconnect` acquires `game_lock` and then calls `_disconnect_locked(player_id, sock, forfeit)`, which does the following:

1. If `players.get(player_id) is not sock`, it returns immediately. This **idempotency guard** stops EOF and a failed send from both handling the same departure. Because it compares the socket, it also stops a stale worker from removing a player who has already reconnected on a new socket.
2. `players.pop(player_id)` and `sock.close()` (ignoring `OSError`).
3. In the lobby, it frees the role and the name so a new client can take them.
4. Mid-match with `forfeit=True`, it calls `end_game(opponent, "FORFEIT")`. That function broadcasts `GAME_OVER` and sets `game_over_event`.
5. Mid-match without `forfeit`, it **keeps all game state**, records `reconnect_deadlines[player_id] = now + 120 s`, and sends `PLAYER_DISCONNECTED` to the opponent. While a seat is empty, `handle_action` answers every `ACTION` with `ERROR GAME_PAUSED`.

### Reconnect

When a joining connection matches a paused seat (Section 3.2), `_resume_locked` runs in the same critical section:

1. Remove the deadline and set `players[player_id] = sock`.
2. Send the returning client `GAME_START {resumed: true}`, its private `HAND`, and the current `STATE_UPDATE` (same `seq`, so the opponent sees no gap).
3. Send `PLAYER_RECONNECTED` to the opponent. Play continues from the same phase, turn, pot, and chips.

Each time around the accept loop, the main thread calls `expire_reconnects()`. Under `game_lock`, it forfeits any player whose deadline has passed: `end_game(opponent, "FORFEIT")`, or `end_game(None, "ABANDONED")` if both players are gone.

### 3.7 Server shutdown

`end_game()` sets `game_over_event`. The accept loop sees the event within 1 s and calls `shutdown()`, which does the following:

1. Join each worker thread for up to 5 s. Clients close their end after `GAME_OVER`, so workers normally exit on EOF. Waiting first means no unread data is left to trigger a TCP reset that could discard `GAME_OVER`.
2. Acquire `game_lock`, close every remaining socket in `players`, clear the dict, and release the lock.
3. Close `server_socket`.

Any worker still blocked in `recv()` gets an `OSError` or EOF and exits. Its `finally` cleanup does nothing because its player has already been removed.

## 4. Shared Resources

Both workers and the main thread can reach everything below, so all of it is protected by `game_lock`.

| Resource | Type | Written by | Read by | Corruption if unprotected |
|---|---|---|---|---|
| `players` | `dict[str, socket]` | Workers (join, reconnect, disconnect), main (shutdown) | Every send path, `broadcast`, pause check | `RuntimeError: dictionary changed size during iteration` in the middle of a broadcast. A message sent to a closed socket. Two clients both assigned `Player_1`. |
| `identities` | `dict[str, (name, ip)]` | Workers (join, lobby disconnect) | Join and reconnect matching | Two players with the same name, so a returning player matches the wrong seat. |
| `reconnect_deadlines` | `dict[str, float]` | Workers (drop, reconnect), main (`expire_reconnects`) | Join, `expire_reconnects` | A player reconnects in the same instant their deadline expires, so they are both reseated and forfeited. |
| `game.state`, `game.phase` | `str` | `start_game`, `handle_action`, `end_round`, `end_game` | Every action validation | An action accepted after `GAME_OVER`. A phase skipped. |
| `game.active_player` | `str` | `switch_turn`, `start_game` | Turn check in `handle_action` | **Check-then-act race:** both players pass the turn check before either switches the turn. |
| `game.deck` | `list[str]` | `start_round`, `draw_cards` | — | Two `pop()` calls during interleaved draws give a duplicate or lost card, or an `IndexError`. |
| `game.player_1_hand`, `game.player_2_hand` | `list[str]` | `start_round`, `draw_cards` | `send_hand`, `showdown` | Showdown evaluates a hand halfway through a replacement. |
| `game.round_number` | `int` | `next_round` | `ROUND_OVER`, `STATE_UPDATE`, `check_game_over` | Round skipped or counted twice. |
| `game.player_N_wins`, `game.player_N_streak` | `int` | `record_round_win` | `check_game_over`, every broadcast | A lost update (`+=` is not atomic). A streak check that reads an old value. |
| Socket send stream | Kernel buffer per socket | Any thread that calls `send_message` | Peer | Bytes from two `sendall` calls **interleave**, producing a corrupt JSON line. |

**State that is not shared and needs no lock:** each worker's `buffer` and loop variables, and the parsed `message` dicts. A worker also calls `recv()` only on its own socket.

## 5. Thread Safety & Lock Acquisition Strategy

### 5.1 One coarse lock

The server uses a **single `threading.Lock` named `game_lock`** that covers every resource in Section 4. Here is why it is coarse-grained:

- With one lock, threads cannot acquire locks in conflicting orders, so **lock-ordering deadlock cannot happen**.
- Most poker actions touch several resources at once (turn, deck, hand, scores, players). Separate locks would need to be held together anyway.
- With only 2 clients and human think time between moves, contention is negligible.

### 5.2 Lock rules

1. **Never hold `game_lock` during `recv()` or `accept()`.** A thread holding the lock while it waits for network input would freeze every other thread.
2. **Validate, mutate, and broadcast inside one critical section.** The turn check, the state change, and the resulting broadcasts happen under one lock acquisition. Releasing the lock between the check and the change would bring back the check-then-act race.
3. **Every send happens under `game_lock`**, including single-client `ERROR` and `HAND` messages. `sendall()` from two threads on the same socket is not atomic, so this rule is what prevents interleaved frames.
4. **No re-acquisition.** `threading.Lock` is not reentrant. Functions that expect the caller to already hold the lock end in `_locked` (for example `_join_locked`, `_resume_locked`, `_disconnect_locked`) or are documented as such (`broadcast`, `end_round`, `end_game`). Only the entry points `process_join`, `try_start_game`, `process_message`, `handle_disconnect`, `expire_reconnects`, and the shutdown sequence call `with game_lock:`.
5. **Always use `with game_lock:`**, never bare `acquire()`/`release()`. The lock is then released even when an exception is raised inside the critical section.

### 5.3 Critical sections

| # | Entry point (thread) | Lock held across |
|---|---|---|
| CS-1 | `process_join` (worker) | capacity, name, and identity checks → role assignment or reconnect → `players[...] = sock` → `LOBBY_WAIT`, or for a reconnect `GAME_START`/`HAND`/`STATE_UPDATE`/`PLAYER_RECONNECTED` |
| CS-2 | `try_start_game` (worker that seated player 2) | deal → `GAME_START` ×2 → `HAND` ×2 → `STATE_UPDATE` broadcast |
| CS-3 | `process_message` → `ACTION` (worker) | state/phase/turn checks → mutation → `HAND`/`STATE_UPDATE`/`ROUND_OVER`/`GAME_OVER` sends |
| CS-4 | `process_message` → schema failure (worker) | sending `ERROR` only |
| CS-5 | `handle_disconnect` (worker) | idempotency guard → `players.pop` → `close` → forfeit `GAME_OVER`, or reconnect deadline + `PLAYER_DISCONNECTED` |
| CS-6 | Shutdown (main) | closing all sockets, clearing `players` |
| CS-7 | `expire_reconnects` (main, about once a second) | deadline check → forfeit `GAME_OVER` |

Acquire and release pattern for CS-3:

```python
def process_message(player_id, sock, message):
    valid, error = validate_message(message)        # outside lock: pure function
    if not valid:
        with game_lock:                             # CS-4: lock only to serialize the send
            send_error(sock, error, "Invalid protocol message.")
        return

    if message["msg_type"] == "ACTION":
        with game_lock:                             # CS-3: acquire
            handle_action(player_id, sock, message["payload"])
                                                    # validate → mutate → broadcast
                                                    # release (also on exception)
    elif message["msg_type"] == "DISCONNECT":
        handle_disconnect(player_id)                # CS-5: acquires internally
```

Broadcast failure handling while the lock is held (no recursion into the lock):

```python
def broadcast(message):                             # caller holds game_lock
    dead = []
    for player_id, sock in list(players.items()):   # snapshot: safe even if we pop below
        try:
            send_message(sock, message)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            dead.append(player_id)
    for player_id in dead:
        _disconnect_locked(player_id)               # does NOT re-acquire game_lock
```

### 5.4 Bounding time spent holding the lock

The lock is held while sending, so a peer that stops reading could block `sendall()` once its TCP window fills. To bound that:

- Each client socket gets `settimeout(SOCKET_TIMEOUT)` (30 s). A stalled `sendall()` then raises `TimeoutError`, and the peer is treated as disconnected.
- The same timeout also applies to `recv()`. A waiting player can legitimately stay silent for a long time while the opponent thinks, so a `recv()` timeout is **not** a disconnect. The worker checks `error.errno is None` (Python's socket timeout) and loops again. A kernel `ETIMEDOUT` has an errno and is treated as a dead peer.
- `SO_KEEPALIVE` lets the OS detect a peer that disappeared without sending FIN or RST.
- Messages are small (well under 1 KiB) compared with socket buffers (tens of KiB), so in normal play a send never blocks.

### 5.5 Race-condition analysis

| # | Race scenario | Without protection | With this design |
|---|---|---|---|
| R1 | Both players submit `ACTION` at the same instant | Both read `active_player == Player_1` before either switches. Two moves are applied for one turn. | CS-3 serializes them. The second worker sees the updated `active_player` and gets `ERROR OUT_OF_TURN`, or its move is valid if the turn has really passed to it. |
| R2 | Player 1 sends `DRAW` twice in quick succession | Two concurrent `deck.pop()` sequences | Both messages come from the same worker, so they are handled one after the other. The second sees `active_player == Player_2` and is rejected. |
| R3 | A disconnect while the other worker is broadcasting | `players` changes size while being iterated, or a send goes to a closed socket | CS-5 cannot start until CS-3 releases the lock. The broadcast also iterates over a `list()` snapshot. |
| R4 | EOF and a broadcast send failure both detect the same dead player | Two forfeits and two `GAME_OVER` messages | The `if player_id not in players: return` guard runs under the lock, so only the first detection does anything. |
| R5 | `ERROR` to Player 1 from its own worker while Player 2's worker broadcasts `STATE_UPDATE` to Player 1 | Interleaved bytes produce a corrupt JSON line on Player 1's socket | Rule 3: every send runs under `game_lock`, so the frames on one socket stay whole. |
| R6 | A player's `ACTION` arrives before `start_game` finishes | The action runs against a half-dealt game | Its worker blocks on the lock until CS-2 is finished, then sees `state == PLAYER_TURN`. |
| R7 | Two clients send `CONNECT` at the same time | Two sockets assigned `Player_2` | Each join is handled under CS-1, so the capacity check and the insert into `players` are atomic. |
| R9 | A dropped player reconnects before their old worker has finished cleanup | The old worker's `finally` removes the **new** socket, and the player is dropped again | The guard compares sockets (`players.get(player_id) is not sock`), so the stale worker's cleanup does nothing. |
| R10 | A player reconnects in the same second their deadline expires | Reseated and forfeited at once | The join (CS-1) and `expire_reconnects` (CS-7) both run under `game_lock`. Whichever runs first removes the deadline, so the other sees a consistent result. |
| R11 | The opponent acts while the seat is empty | A move is applied that the missing player never sees, or a broadcast is skipped | `handle_action` checks `len(players) < 2` under the lock and answers `ERROR GAME_PAUSED` without changing state. |
| R8 | A disconnect lands between `ROUND_OVER` and the next deal | A hand is dealt to a departed player | `send_hand` checks `player_id in players` under the same lock. The forfeit path sets `GAME_OVER`, and all later actions are rejected. |

## 6. Turn Enforcement & Move Validation

`handle_action` (inside CS-3) runs these checks **in order** and stops at the first failure. **No state is changed until every check passes**, so a rejected move cannot corrupt the game.

| Order | Check | Failure response |
|---|---|---|
| 0 | JSON parses and the envelope is valid (outside the lock) | `ERROR MALFORMED_JSON` / `INVALID_MESSAGE` |
| 1 | Payload schema: known `action`, `BET.amount` a positive int, `DRAW.cards` distinct ints 0–4 | `ERROR INVALID_ACTION` / `INVALID_BET` / `INVALID_CARD_INDEX` |
| 2 | `game.state == "PLAYER_TURN"` (not lobby, showdown, or game over) | `ERROR INVALID_STATE` |
| 2b | Both players connected (no seat waiting for a reconnect) | `ERROR GAME_PAUSED` |
| 3 | `player_id == game.active_player`, where `player_id` comes from the **socket**, not the envelope | `ERROR OUT_OF_TURN` |
| 4 | Action legal in the current `phase` (for example `DRAW` only in `DRAW_PHASE`, once per player; `CALL` only when a bet is outstanding; `BET` no larger than either player's chip stack) | `ERROR INVALID_ACTION` / `INVALID_BET` |
| 5 | Apply the mutation, then broadcast | — |

`ERROR` goes **only** to the offending client. The opponent's view doesn't change, because the shared state didn't change.

## 7. Broadcast & Synchronization Guarantees

"Synchronous broadcast" in this design means the following guarantees:

1. **One snapshot.** Each `STATE_UPDATE` payload is built once from `game.public_state()`, inside the same critical section as the mutation. Both clients get byte-for-byte identical public state.
2. **All or nothing before the next change.** The broadcast loop sends to every connected player **before** `game_lock` is released. No later move can be applied, and no other message sent, until both clients have the update in their TCP send buffers.
3. **Same order for both clients.** Every send happens under one lock and TCP keeps the byte order on each connection, so both clients see the message sequence (`STATE_UPDATE` → `ROUND_OVER` → `HAND` → `STATE_UPDATE` …) in the same relative order.
4. **Private data stays private.** `HAND` is sent with `send_message(players[pid], …)` to its owner only and is never passed to `broadcast`. `public_state()` contains no cards.
5. **Failure isolation.** If the send to one client fails, that client goes through the disconnect and forfeit path. The other client still gets the broadcast, followed by `GAME_OVER {reason: FORFEIT}`.
6. **Ordering check.** Every `STATE_UPDATE` carries a `seq` counter that is incremented under the lock. The client prints a warning if it ever sees a gap.

## 8. Score & Leaderboard Synchronization

### 8.1 In-memory storage

`PokerGame` holds the scores. The single `game` instance is created once at server start and is protected by `game_lock`.

| Field | Meaning | Updated in |
|---|---|---|
| `round_number` | Current round (1–`max_rounds`) | `next_round()` |
| `player_1_wins`, `player_2_wins` | Total rounds won (the leaderboard) | `record_round_win()` |
| `player_1_streak`, `player_2_streak` | Current consecutive-win streak | `record_round_win()` |
| `chips` | Each player's chip stack (starts at 100). A stack of 0 ends the match with `OUT_OF_CHIPS` | `put_chips()`, `record_round_win()` (pot payout) |
| `max_rounds` | Round limit (10) | Constructor |

Scores are **server-authoritative**. Clients only display what the server sends and never compute or report a winner.

### 8.2 Update rules (`record_round_win`)

- Winner: `wins += 1`, `streak += 1`, opponent's `streak = 0`.
- Tie (`winner is None`): both streaks reset to 0, and wins are unchanged.
- Chips: the winner takes the pot. On a tie, each player gets back what they put in that round.
- `check_game_over()` then tests for an empty chip stack (`OUT_OF_CHIPS`), `streak >= 3` (match win), or `round_number >= max_rounds` (draw).

### 8.3 Broadcast sequence on round completion

All of these steps happen inside the **single critical section** of the `ACTION` (`CALL` or `FOLD`) that ended the round:

1. Determine the winner: `showdown()` compares the two hands, and `FOLD` makes the opponent the winner.
2. `record_round_win(winner)` updates wins and streaks.
3. `broadcast(ROUND_OVER)`, which carries the new score table:
   ```json
   {"msg_type":"ROUND_OVER","player_id":"SERVER","payload":{"round":3,"winner":"Player_1","reason":"SHOWDOWN","winning_hand":"Two Pair","player_1_wins":2,"player_2_wins":1,"player_1_streak":2,"player_2_streak":0},"timestamp":1727000010}
   ```
4. `check_game_over()`:
   - **Continue:** `next_round()` (which picks the next first player: the previous round's winner has a 40% chance, and a tie is 50/50), a private `HAND` to each player, then `broadcast(STATE_UPDATE {last_action: "NEW_ROUND", round, scores…})`.
   - **Match over:** `broadcast(GAME_OVER {winner, reason, rounds_played, player_1_wins, player_2_wins})` and set `game_over_event`.

The score change and both broadcasts happen under the same lock acquisition, so a client can never see the next round's `STATE_UPDATE` before the `ROUND_OVER` that justified it. The two clients' leaderboards also can't disagree. Every `STATE_UPDATE` repeats the current wins and streaks, so a client's scoreboard is correct even if it skipped processing a `ROUND_OVER`.

## 9. State & Score Broadcast Sequence Diagram

The diagram shows every protocol message the game uses: `CONNECT`, `LOBBY_WAIT`, `GAME_START`, `HAND`, `ACTION` (`DRAW`, `CHECK`, `BET`, `CALL`, `ALL_IN`, `FOLD`), `STATE_UPDATE`, `ERROR`, `ROUND_OVER`, `PLAYER_DISCONNECTED`, `PLAYER_RECONNECTED`, `GAME_OVER`, and `DISCONNECT`. It also shows which thread holds `game_lock`.

```mermaid
sequenceDiagram
    autonumber
    actor C1 as Client 1 (Player_1)
    participant W1 as Worker Thread 1
    participant M as Main Thread
    participant GS as Shared State (game_lock)
    participant W2 as Worker Thread 2
    actor C2 as Client 2 (Player_2)
    participant W3 as Worker Thread 3 (later connections)
    actor C3 as Client 3 (extra)

    Note over C1,C3: Phase 1 - Connection, joining, waiting to join

    C1->>M: TCP connect (accept returns sock1)
    M->>W1: spawn Thread(handle_client, sock1)
    C1->>W1: CONNECT (name Alex)
    critical W1 holds game_lock
        W1->>GS: lobby has a free seat, name not taken
        W1->>GS: players[Player_1] = sock1, identities[Player_1] = (Alex, IP 1)
        W1-->>C1: LOBBY_WAIT (players_connected 1, players_required 2)
    end

    C2->>M: TCP connect (accept returns sock2)
    M->>W2: spawn Thread(handle_client, sock2)
    C2->>W2: CONNECT (name Blake)
    critical W2 holds game_lock
        W2->>GS: players[Player_2] = sock2, identities[Player_2] = (Blake, IP 2)
        W2-->>C2: LOBBY_WAIT (players_connected 2, players_required 2)
    end

    Note over W2,GS: Two players joined - game starts
    critical W2 holds game_lock (W1 blocks on any ACTION)
        W2->>GS: start_game() shuffle, deal, pick first_player, 100 chips each
        par GAME_START to both
            W2-->>C1: GAME_START (your_role Player_1, first_player Player_1, round 1, starting_chips 100)
        and
            W2-->>C2: GAME_START (your_role Player_2, first_player Player_1, round 1, starting_chips 100)
        end
        W2-->>C1: HAND (Player_1 cards only - private)
        W2-->>C2: HAND (Player_2 cards only - private)
        par STATE_UPDATE broadcast
            W2-->>C1: STATE_UPDATE (round 1, active Player_1, chips 100-100, last_action GAME_START)
        and
            W2-->>C2: STATE_UPDATE (round 1, active Player_1, chips 100-100, last_action GAME_START)
        end
    end

    C3->>M: TCP connect (accept returns sock3)
    M->>W3: spawn Thread(handle_client, sock3)
    C3->>W3: CONNECT (name Casey)
    critical W3 holds game_lock
        W3->>GS: match running, no seat held for a reconnect
        W3-->>C3: ERROR (code GAME_FULL)
    end
    W3->>C3: close sock3

    Note over C1,C2: Phase 2 - Concurrent moves, turn enforcement, validation

    par Both clients send at the same instant
        C1->>W1: ACTION (DRAW, cards 1 and 3)
    and
        C2->>W2: ACTION (CHECK)
    end
    Note over W1,W2: Both workers parse and schema-check OUTSIDE the lock<br/>then race to acquire game_lock

    critical W2 acquires game_lock first (W1 blocks)
        W2->>GS: state PLAYER_TURN? yes. active_player == Player_2? NO
        W2-->>C2: ERROR (code OUT_OF_TURN, Wait for your turn)
        Note over GS: State unchanged - nothing broadcast
    end

    critical W1 acquires game_lock
        W1->>GS: state PLAYER_TURN? active_player == Player_1? phase allows DRAW?
        alt Valid move
            W1->>GS: draw_cards(Player_1, 1 and 3), switch_turn to Player_2
            W1-->>C1: HAND (new Player_1 cards - private)
            par STATE_UPDATE broadcast
                W1-->>C1: STATE_UPDATE (active Player_2, last_action DRAW)
            and
                W1-->>C2: STATE_UPDATE (active Player_2, last_action DRAW)
            end
        else Illegal action or bad payload
            W1-->>C1: ERROR (code INVALID_ACTION or INVALID_CARD_INDEX)
            Note over GS: State unchanged - nothing broadcast
        end
    end

    C1->>W1: malformed bytes then LF
    Note over W1: json parse fails outside lock
    critical W1 holds game_lock (send only)
        W1-->>C1: ERROR (code MALFORMED_JSON)
    end

    C2->>W2: ACTION (BET, amount 10)
    critical W2 holds game_lock
        W2->>GS: turn and phase valid, record bet, switch_turn to Player_1
        par STATE_UPDATE broadcast
            W2-->>C1: STATE_UPDATE (active Player_1, last_action BET)
        and
            W2-->>C2: STATE_UPDATE (active Player_1, last_action BET)
        end
    end

    Note over C1,C2: Phase 3 - Round end and score synchronization

    alt Player_1 calls
        C1->>W1: ACTION (CALL)
    else Player_1 folds
        C1->>W1: ACTION (FOLD)
    end
    critical W1 holds game_lock for the whole round transition
        alt CALL
            W1->>GS: showdown() compare hands, winner by hand rank
        else FOLD
            W1->>GS: winner = Player_2 (no showdown)
        end
        W1->>GS: record_round_win(winner) update wins and streaks
        par ROUND_OVER broadcast with score table
            W1-->>C1: ROUND_OVER (winner, reason, winning_hand, wins, streaks)
        and
            W1-->>C2: ROUND_OVER (winner, reason, winning_hand, wins, streaks)
        end
        W1->>GS: check_game_over()
        alt Match continues
            W1->>GS: next_round() round + 1, new deck, deal
            W1-->>C1: HAND (new Player_1 cards - private)
            W1-->>C2: HAND (new Player_2 cards - private)
            par STATE_UPDATE broadcast
                W1-->>C1: STATE_UPDATE (round 2, active player, wins, streaks, last_action NEW_ROUND)
            and
                W1-->>C2: STATE_UPDATE (round 2, active player, wins, streaks, last_action NEW_ROUND)
            end
        else Streak reaches 3 or round limit reached
            par GAME_OVER broadcast
                W1-->>C1: GAME_OVER (winner, reason THREE_CONSECUTIVE_WINS or ROUND_LIMIT_DRAW, wins)
            and
                W1-->>C2: GAME_OVER (winner, reason THREE_CONSECUTIVE_WINS or ROUND_LIMIT_DRAW, wins)
            end
            W1->>M: set game_over_event
        end
    end

    Note over C1,C2: Phase 4 - Dropped connection, saved game, reconnect

    C2--xW2: TCP FIN or reset (no DISCONNECT sent)
    Note over W2: recv returns empty bytes, finally calls handle_disconnect
    critical W2 holds game_lock
        W2->>GS: players[Player_2] is sock2? yes (idempotency guard)
        W2->>GS: players.pop(Player_2), sock2.close()
        W2->>GS: keep all game state, reconnect_deadlines[Player_2] = now + 120 s
        W2-->>C1: PLAYER_DISCONNECTED (player Player_2, reconnect_timeout 120)
    end
    Note over W2: worker thread exits

    C1->>W1: ACTION (CHECK)
    critical W1 holds game_lock
        W1->>GS: state PLAYER_TURN? both seats filled? NO
        W1-->>C1: ERROR (code GAME_PAUSED)
        Note over GS: State unchanged - nothing broadcast
    end

    alt Player_2 returns within 120 s
        C2->>M: TCP connect (accept returns sock4)
        M->>W3: spawn Thread(handle_client, sock4)
        C2->>W3: CONNECT (name Blake)
        critical W3 holds game_lock
            alt Name and IP match the held seat
                W3->>GS: players[Player_2] = sock4, clear deadline
                W3-->>C2: GAME_START (resumed true, your_role Player_2)
                W3-->>C2: HAND (Player_2 cards, unchanged - private)
                W3-->>C2: STATE_UPDATE (same round, phase, turn, pot, chips)
                W3-->>C1: PLAYER_RECONNECTED (player Player_2)
            else Different name or IP
                W3-->>C2: ERROR (code RECONNECT_REJECTED)
                W3->>C2: close sock4
            end
        end
    else Deadline passes
        critical Main holds game_lock (expire_reconnects)
            M->>GS: deadline passed, remove the held seat
            M-->>C1: GAME_OVER (winner Player_1, reason FORFEIT)
            M->>M: set game_over_event
        end
    end

    Note over C1,C2: Phase 5 - Quit (forfeit) and teardown

    C1->>W1: DISCONNECT (reason QUIT)
    critical W1 holds game_lock
        W1->>GS: players[Player_1] is sock1? (idempotency guard)
        alt Match still active
            W1->>GS: players.pop(Player_1), sock1.close()
            W1-->>C2: GAME_OVER (winner Player_2, reason FORFEIT)
            W1->>M: set game_over_event
        else Game already over
            Note over W1: No-op - forfeit never awarded twice
        end
    end

    critical Main holds game_lock (shutdown)
        M->>GS: close remaining sockets, clear players
    end
    M->>C2: close sock4
    Note over W3: recv raises OSError or returns EOF, worker exits
    Note over M: close server_socket
```

### 9.1 Narrative

**Phase 1: connection, waiting to join, joined.** The main thread accepts each TCP connection and hands it to a new worker. The worker reads `CONNECT` and joins the player inside a short critical section, recording the name and IP as the player's identity. Client 1 gets `LOBBY_WAIT` (1 of 2) and waits. When Client 2 joins, its worker calls `start_game()` while holding `game_lock`. Each client receives its own `GAME_START`, its own private `HAND`, and the first public `STATE_UPDATE`. Because one thread holds the lock for this whole sequence, no worker can process an `ACTION` against a half-initialized game. A third client is rejected with `ERROR GAME_FULL` and disconnected, and the existing players are not affected.

**Phase 2: concurrent moves and turn enforcement.** Both clients send an `ACTION` at the same moment. Each worker parses its own frame without the lock, then both compete for `game_lock`. In the case shown, Worker 2 wins the lock. Its turn check fails (`active_player` is `Player_1`), so it sends `ERROR OUT_OF_TURN` to Client 2 only and changes nothing. Worker 1 then gets the lock and checks the state, turn, and phase:

- **Valid move:** it replaces the cards, sends the new `HAND` privately to Client 1, switches the turn, and broadcasts a single `STATE_UPDATE` snapshot to both clients before releasing the lock.
- **Illegal move:** the `alt`/`else` branch sends `ERROR` to the mover and changes nothing.

Malformed JSON is caught outside the lock, and the lock is taken only to send the `ERROR` without interleaving bytes with other messages. Player 2's `BET` follows the same validate, mutate, broadcast pattern.

**Phase 3: round end and score sync.** A `CALL` leads to a showdown, while a `FOLD` gives the round to the opponent. Either way, a single critical section does the following:

- updates wins and streaks
- broadcasts `ROUND_OVER` with the full score table to both clients
- checks whether the match is over

If the match continues, the worker deals the next round, sends each private `HAND`, and broadcasts `STATE_UPDATE` with the new round number and scores. If it is over, the worker broadcasts `GAME_OVER` and signals the main thread. Both clients get the same score table in the same order, and no move from the next round can arrive in between.

**Phase 4: dropped connection, saved game, reconnect.** Client 2's connection closes without a `DISCONNECT`. Its worker's cleanup runs `_disconnect_locked` under the lock. The socket guard makes it idempotent, so a later failed send for the same socket does nothing. Instead of ending the match, it keeps every piece of game state, starts a 120 s reconnect deadline, and sends `PLAYER_DISCONNECTED` to Client 1. While the seat is empty, Client 1's moves get `ERROR GAME_PAUSED` and change nothing.

- **Reconnect:** Client 2 connects again and sends `CONNECT` with the same name from the same IP. Its new worker matches the identity under the lock, puts the new socket in the old seat, and sends `GAME_START {resumed: true}`, the unchanged private `HAND`, and the current `STATE_UPDATE`. Client 1 gets `PLAYER_RECONNECTED`, and play continues from the same phase and turn. A connection with a different name or IP gets `RECONNECT_REJECTED`.
- **Timeout:** if nobody reclaims the seat in time, the main thread's `expire_reconnects()` forfeits the missing player under the lock and broadcasts `GAME_OVER`.

**Phase 5: quit and teardown.** `DISCONNECT {reason: QUIT}` is a deliberate exit, so it forfeits right away instead of holding the seat. The main thread then closes the remaining sockets and the listening socket, and every worker exits.

## 10. Implementation Mapping

| Design requirement | Where it is implemented |
|---|---|
| One worker thread per client, main loop never reads client sockets | `server.py` `main()`, `handle_client()` |
| Single `game_lock` and the `_locked` convention | `server.py` shared state section, `_disconnect_locked()` |
| Every send under `game_lock` (R5) | `process_message()` wraps the schema-error send. All other sends run inside locked entry points. |
| Join under the lock on `CONNECT`, third client gets `GAME_FULL` (CS-1, R7) | `process_join()` → `_join_locked()`. The accept loop runs for the whole match. |
| Saved game, reconnect by name and IP, 120 s forfeit (3.5, R9–R11) | `_disconnect_locked()`, `_resume_locked()`, `expire_reconnects()`, `RECONNECT_TIMEOUT` |
| Idempotent disconnect and forfeit (R3, R4, R9) | `handle_disconnect()` → `_disconnect_locked()` socket guard. `end_game()` also ignores a second call. |
| Validate before mutating (Section 6) | `handle_action()` → `validate_action()`, state, turn, then `PokerGame.action_error()`, and only then `apply_action()` |
| FSM phase rules: two checks or a call close betting, no raise, `DRAW` once and only in `DRAW_PHASE` | `game.py` `action_error()`, `apply_action()`, `close_betting()` |
| First-player rule (40% for the previous winner) | `game.py` `choose_first_player()` |
| Bounded send time and silent-drop detection (5.4) | `SOCKET_TIMEOUT`, `SO_KEEPALIVE`, errno check in `handle_client()` |
| `seq` ordering check (Section 7) | `broadcast_state()`, client `STATE_UPDATE` handler |
| `MALFORMED_JSON` and byte-level framing | `protocol.py` `receive_messages()` (byte buffer), `validate_message()` |
| Client re-prompts after `ERROR` and offers only legal actions | `client.py` `legal_choices()`, `ERROR` handler |

**Known naming difference:** score payloads use flat keys (`player_1_wins` and so on), while the Sprint 1 blueprint shows `wins: {...}` objects. Messages also include `pot`, `current_bet`, `first_player`, and `seq`, which the blueprint doesn't list.
