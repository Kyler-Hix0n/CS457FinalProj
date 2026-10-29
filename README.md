# CS457FinalProj — Two-Player Five-Card Draw Poker

A terminal poker game for two players. One Python TCP server runs the game and decides every result. Two clients connect to it, show each player their cards and the game state, and send the player's moves.

## Requirements

- **Python 3.10 or newer.** Check with `python3 --version`. The server relies on `socket.timeout` being `TimeoutError`, which started in 3.10.
- Standard library only, so there is nothing to `pip install`.
- Three terminal windows: one for the server and one for each player.

## Files

| File | Purpose |
|---|---|
| `server.py` | Authoritative game server. Accepts 2 players on port 5050. |
| `client.py` | Terminal client, one per player |
| `game.py` | Deck, hand evaluation, betting/draw phases, scoring |
| `protocol.py` | LF-terminated JSON message framing and validation |
| `docs/` | Protocol blueprint, FSM specification, concurrency design |

## Running locally (one computer)

Run each command from the project folder (the one that contains `server.py`).

**1. Start the server** in terminal 1:

```bash
python3 server.py
```

You should see `Poker server listening on port 5050`.

**2. Start player 1** in terminal 2:

```bash
python3 client.py
```

Enter a name when prompted. The client prints `Players connected: 1/2` and `Waiting for opponent...`.

**3. Start player 2** in terminal 3:

```bash
python3 client.py
```

Enter a name. The game starts right away: both clients show `GAME START`, their own five cards, and the game state.

## Running across machines (e.g. CML)

1. Start the server on the host machine with `python3 server.py`. It listens on all interfaces (`0.0.0.0`), port `5050`.
2. On each client machine, edit the connection settings near the top of `client.py`:
   ```python
   # SERVER_HOST = "127.0.0.1"
   SERVER_HOST = "192.168.20.100"   # the server machine's IP address
   PORT = 5050
   ```
3. Run `python3 client.py` on each client machine.

Make sure TCP port 5050 is reachable on the server machine (firewall or lab ACLs). If you change `PORT`, change it in both `server.py` and `client.py`.

## How to play

Each round goes through these phases. The round's first player acts first in every phase.

| Phase | Your options |
|---|---|
| `BETTING_1` | **Check**, **Bet**, **All in**, or **Fold** if no bet is outstanding. **Call**, **All in**, or **Fold** if your opponent has bet. |
| `DRAW_PHASE` | **Draw**: enter card positions to replace, e.g. `0,2,4`. Leave it blank to keep all five. Each player draws once. |
| `BETTING_2` | Same as `BETTING_1` |
| Showdown | Automatic. The better poker hand wins the round. |

- When it's your turn, the client shows a numbered menu with only the moves that are legal right now. Type the number and press Enter. Type `Q` to quit.
- Each player starts with **100 chips**. Bets come out of your stack and go into the pot, and the round winner takes the pot. A tied showdown gives each player back what they put in.
- A bet can't be more than the smaller of the two stacks, so every bet can be called.
- **All in** puts in your whole stack, capped at what your opponent can match. Facing a bet, it raises if you have more than the bet, which is the only way to raise.
- If either player is all in, the second betting phase is skipped and the round goes straight to showdown after the draw.
- Two checks in a row, or a call, ends a betting phase.
- **Fold** gives the round to your opponent immediately.
- If the server rejects a move, the client prints the `ERROR` and asks you again.

### Winning the match

- A player who runs **out of chips** loses the match.
- The first player to win **3 rounds in a row** wins the match. A tied round resets both streaks.
- If nobody gets 3 in a row within **10 rounds**, the match is a draw.
- If a player chooses **Q** (quit) during a match, the other player wins by **forfeit**.

### Disconnecting and reconnecting

If a player's connection drops mid-match (closed terminal, Ctrl+C, network failure), the server **saves the game and pauses it**. The other player sees that their opponent left, and any move they try is refused until the opponent returns.

To resume, run `python3 client.py` again **from the same computer (same IP) and enter the exact same name**. You get your seat back with the same cards, chips, scores, and turn. Anyone else who tries to join is turned away with `RECONNECT_REJECTED`.

If the player isn't back within **120 seconds**, they forfeit. Names must be different from each other when you join, since the name is how the server recognizes a returning player.
- **First-player rule:** round 1 is 50/50. After that, the previous round's winner has a 40% chance of going first next round, and a tie resets it to 50/50.

When the match ends, both clients show `GAME OVER` with the final score and close. The server shuts down on its own within a few seconds, so restart it for a new match.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Could not connect to server.` | Start the server first, and check `SERVER_HOST`/`PORT` in `client.py`. |
| `OSError: [Errno 48] Address already in use` | A previous server is still running. Stop it with Ctrl+C, or find it with `lsof -i :5050` and kill it. |
| A third client gets `ERROR: GAME_FULL` | Expected: the server only takes 2 players. Restart the server for a new match. |
| `ERROR: NAME_TAKEN` | The other player already uses that name. Pick a different one. |
| `ERROR: RECONNECT_REJECTED` | A match is paused for a dropped player. Reconnect from that player's computer with their exact name. |
| A client seems frozen | It's waiting for the opponent's move, or for you to finish typing at a prompt. |
| Stopping the server early | Press Ctrl+C in the server terminal. |
