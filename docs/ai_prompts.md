# CS457 Sprint 1 — AI Prompting and Constraint Strategy

## 1. Purpose

This document describes a constrained approach to using AI assistance for the CS457 Five-Card Draw Poker protocol and server FSM. The objective is to avoid generic socket boilerplate that contradicts the project's application-specific JSON schema, TCP framing rule, or state transitions.

**Provenance note:** The prompts below are **reconstructed/recommended prompt templates** based on the project's design discussions, not a verbatim audit log of all earlier AI interactions. Replace or supplement them with the exact prompts actually used if your instructor requires historical prompt evidence. Do not present reconstructed wording as an exact transcript.

## 2. Fixed project constraints supplied to an AI coding assistant

1. Language: Python 3; standard library networking (`socket`, `threading`, `json`, `time`) unless a dependency is explicitly approved.
2. Architecture: one authoritative TCP server, two terminal clients, each with an assigned role.
3. Transport: IPv4 TCP; development port 5050.
4. Encoding: UTF-8 JSON with **one LF (`\n`) terminator per complete message**.
5. Envelope: every message must include `msg_type` (string), `player_id` (string), `payload` (object), and `timestamp` (integer Unix seconds).
6. Allowed client messages: `CONNECT`, `ACTION`, `DISCONNECT`.
7. Allowed action names: `CHECK`, `BET`, `CALL`, `FOLD`, `DRAW`.
8. Server messages: `LOBBY_WAIT`, `GAME_START`, `HAND`, `STATE_UPDATE`, `ROUND_OVER`, `ERROR`, `GAME_OVER`.
9. The receiver must preserve incomplete bytes across `recv()` calls and extract all complete LF-delimited frames; never assume one `recv()` equals one message.
10. Handle EOF (`recv() == b''`), socket exceptions, invalid messages, and disconnections without hanging or corrupting shared state.
11. Server owns deck, hands, turn, phase, winner, scores, and streaks. Do not trust a client-supplied role or accept client-computed wins.
12. Private hands must go only to the owner. Public `STATE_UPDATE` must not leak hidden cards.
13. Use the FSM specification as the source of truth; illegal phase/action combinations must generate `ERROR` and leave state unchanged.

## 3. Example constrained prompts

### Prompt A — Protocol serialization and framing

> Implement `protocol.py` for my two-player Python TCP poker game. Use the exact envelope fields `msg_type: str`, `player_id: str`, `payload: dict`, and `timestamp: int`. Serialize compact UTF-8 JSON terminated by one LF byte. Implement `create_message`, `send_message`, `receive_messages`, `validate_message`, and `validate_action`. `receive_messages` must maintain a persistent **byte** buffer per socket, extract multiple complete messages in one read, preserve a partial final message for the next read, detect zero-byte EOF, and handle malformed JSON without terminating the server. Do not substitute pickle, unframed JSON, or a length prefix. Include tests for fragmentation, coalescing, and invalid JSON.

### Prompt B — Authoritative game logic

> Implement `game.py` independently of networking. Model a standard 52-card deck, five unique cards per player, five-card poker hand evaluation including A-2-3-4-5, tie-break tuples, a maximum of ten rounds, and a match win after three consecutive round victories. Provide methods to start a round, get one player's hand, replace indexed cards, compare hands, update streaks, and produce a public state without hidden cards. Do not make the client responsible for shuffle, cards, or winners. Flag any rule not yet fully specified rather than inventing it.

### Prompt C — Server-side FSM and validation

> Implement the server according to `fsm_specification.md`. Assign socket-bound roles `Player_1` and `Player_2`; do not trust client-supplied player IDs. Validate all ACTION requests against schema, active player, and current phase while holding a lock. Only allow DRAW during DRAW_PHASE and only once per player per round. Reject out-of-turn, malformed, and illegal actions with an ERROR message while preserving state. Enforce BETTING_1 → DRAW_PHASE → BETTING_2 → SHOWDOWN → ROUND_OVER. Send private HAND only to its owner. On confirmed disconnect, award forfeit once and clean up sockets. Do not implement arbitrary alternative state transitions.

### Prompt D — Client terminal UI

> Implement a terminal client that connects to the configured TCP server, sends CONNECT with a display name, and processes only the specified server messages. Display numbered private cards, scores, turn status, errors, round results, and match results. Show action choices only when the server's public state identifies this client as active. Encode actions with the exact protocol envelope and LF framing. Do not shuffle, evaluate hands, assign roles, or decide wins locally.

### Prompt E — Verification and debugging

> Review the existing `protocol.py`, `game.py`, `server.py`, and `client.py` against the attached protocol blueprint and Mermaid FSM. Produce a table of implemented, missing, and contradictory requirements. Test two clients connecting, malformed JSON, split frames, combined frames, out-of-turn moves, repeated DRAW, invalid BET, abrupt disconnect, three-win match termination, and round-limit draw. Report failures and minimal changes; do not claim tests passed unless executed.

## 4. Validation workflow

1. **Design first:** define message catalog, field types, wire examples, and legal FSM transitions in Markdown.
2. **Constrain generation:** supply the exact design files and prohibit substitutions such as unframed `recv()` parsing or client-authoritative outcomes.
3. **Inspect code:** compare function signatures and message payloads against the blueprint; verify that no unexpected fields or message names were introduced.
4. **Test transport:** send fragmented and coalesced JSON frames and malformed messages; check EOF and exception handling.
5. **Test state machine:** verify legal transitions, out-of-turn rejection, invalid payload rejection, draw limits, showdown, and disconnect forfeit.
6. **Record discrepancies:** update code or documentation explicitly; never silently assume the generated implementation matches the design.

## 5. Known discrepancies requiring follow-up

The current prototype implements the basic TCP/JSON architecture and some game mechanics, but its betting/draw progression is not yet fully enforced. In particular, the planned design requires betting-round completion tracking, phase-specific legality, repeated-draw prevention, and a precise first-player probability rule. AI-generated code must be reviewed against these gaps before it is described as final.

## 6. Academic integrity and attribution

AI assistance can help draft schemas, code, documentation, and test cases, but the project owner remains responsible for understanding the implementation, verifying behavior, and accurately reporting AI usage. Replace reconstructed examples with actual prompts and outcomes where the course policy calls for a record of prompts used.
