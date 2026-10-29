import random
from collections import Counter


SUITS = ["H", "D", "C", "S"]

RANKS = [
    "2", "3", "4", "5", "6", "7",
    "8", "9", "10", "J", "Q", "K", "A"
]

RANK_VALUES = {
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 11,
    "Q": 12,
    "K": 13,
    "A": 14
}


def create_deck():
    """
    Create a standard 52-card deck.
    """

    return [
        rank + suit
        for suit in SUITS
        for rank in RANKS
    ]


def card_rank(card):
    return card[:-1]


def card_suit(card):
    return card[-1]


def evaluate_hand(hand):
    """
    Evaluate a five-card poker hand.

    Returns a tuple.

    The first number represents the hand category.
    Remaining numbers are used as tie breakers.
    """

    values = sorted(
        [
            RANK_VALUES[card_rank(card)]
            for card in hand
        ],
        reverse=True
    )

    suits = [
        card_suit(card)
        for card in hand
    ]

    counts = Counter(values)

    # Handle A-2-3-4-5 straight.
    if values == [14, 5, 4, 3, 2]:
        straight = True
        straight_high = 5

    else:
        straight = all(
            values[i] - 1 == values[i + 1]
            for i in range(4)
        )

        straight_high = values[0]

    flush = len(set(suits)) == 1

    # Find groups.
    fours = [
        value
        for value, count in counts.items()
        if count == 4
    ]

    triples = [
        value
        for value, count in counts.items()
        if count == 3
    ]

    pairs = [
        value
        for value, count in counts.items()
        if count == 2
    ]

    # 9 - Straight Flush
    if straight and flush:
        return (9, straight_high)

    # 8 - Four of a Kind
    if fours:
        four = max(fours)

        kicker = max(
            value
            for value in values
            if value != four
        )

        return (8, four, kicker)

    # 7 - Full House
    if triples and pairs:
        return (
            7,
            max(triples),
            max(pairs)
        )

    # 6 - Flush
    if flush:
        return (6, *values)

    # 5 - Straight
    if straight:
        return (5, straight_high)

    # 4 - Three of a Kind
    if triples:
        triple = max(triples)

        kickers = sorted(
            [
                value
                for value in values
                if value != triple
            ],
            reverse=True
        )

        return (
            4,
            triple,
            *kickers
        )

    # 3 - Two Pair
    if len(pairs) == 2:
        pairs.sort(reverse=True)

        kicker = max(
            value
            for value in values
            if value not in pairs
        )

        return (
            3,
            pairs[0],
            pairs[1],
            kicker
        )

    # 2 - One Pair
    if len(pairs) == 1:
        pair = pairs[0]

        kickers = sorted(
            [
                value
                for value in values
                if value != pair
            ],
            reverse=True
        )

        return (
            2,
            pair,
            *kickers
        )

    # 1 - High Card
    return (1, *values)


def hand_name(score):
    """
    Convert an evaluated hand score into a readable name.
    """

    names = {
        #temp add royal flush later -- 10: "Royal Flush",
        9: "Straight Flush",
        8: "Four of a Kind",
        7: "Full House",
        6: "Flush",
        5: "Straight",
        4: "Three of a Kind",
        3: "Two Pair",
        2: "One Pair",
        1: "High Card"
    }

    return names[score[0]]


BETTING_PHASES = (
    "BETTING_1",
    "BETTING_2"
)

# Custom first-player rule: the winner of the previous round
# has a 40% chance (50% - 10 points) of acting first next round.
WINNER_FIRST_CHANCE = 0.40

# Chips each player starts the match with. A player who
# runs out of chips loses the match.
STARTING_CHIPS = 100

PLAYERS = (
    "Player_1",
    "Player_2"
)


def other_player(player):

    if player == "Player_1":
        return "Player_2"

    return "Player_1"


class PokerGame:

    def __init__(self, max_rounds=10):

        self.state = "WAITING_FOR_PLAYERS"

        self.max_rounds = max_rounds
        self.round_number = 1

        self.player_1_wins = 0
        self.player_2_wins = 0

        self.player_1_streak = 0
        self.player_2_streak = 0

        self.active_player = None

        # Player who acts first in every phase of this round.
        self.first_player = None

        # Winner of the previous round (None for a tie).
        self.last_round_winner = None

        self.deck = []

        self.player_1_hand = []
        self.player_2_hand = []

        self.phase = "WAITING"

        # Chips each player has not yet put in the pot.
        self.chips = {
            player: STARTING_CHIPS
            for player in PLAYERS
        }

        # Chips each player has put in during the current
        # betting phase, and during the whole round.
        self.bets = {
            player: 0
            for player in PLAYERS
        }

        self.committed = {
            player: 0
            for player in PLAYERS
        }

        self.checks = 0
        self.pot = 0

        # Players who have already drawn this round.
        self.drawn = set()

    def start_game(self):
        """
        Initialize the game and select the first player.
        """

        self.state = "GAME_START"

        self.first_player = random.choice([
            "Player_1",
            "Player_2"
        ])

        self.start_round()

    def choose_first_player(self):
        """
        Pick who acts first next round. 50/50 after a tie,
        otherwise the previous winner has a 40% chance.
        """

        if self.last_round_winner is None:

            return random.choice([
                "Player_1",
                "Player_2"
            ])

        if random.random() < WINNER_FIRST_CHANCE:
            return self.last_round_winner

        return other_player(
            self.last_round_winner
        )

    def start_round(self):
        """
        Create a fresh deck and deal five cards to each player.
        """

        self.state = "DEAL"

        self.deck = create_deck()

        random.shuffle(
            self.deck
        )

        self.player_1_hand = [
            self.deck.pop()
            for _ in range(5)
        ]

        self.player_2_hand = [
            self.deck.pop()
            for _ in range(5)
        ]

        self.pot = 0
        self.drawn = set()

        for player in PLAYERS:
            self.committed[player] = 0

        self.start_phase(
            "BETTING_1"
        )

        self.state = "PLAYER_TURN"

    def start_phase(self, phase):
        """
        Enter a betting or draw phase. The round's first
        player always acts first in each phase.
        """

        self.phase = phase

        for player in PLAYERS:
            self.bets[player] = 0

        self.checks = 0

        self.active_player = self.first_player

    # --------------------------------------------------
    # CHIPS
    # --------------------------------------------------

    def to_call(self, player):
        """
        Chips the player must add to match the opponent's bet.
        """

        return max(
            0,
            self.bets[other_player(player)]
            - self.bets[player]
        )

    def max_bet(self, player):
        """
        Largest BET allowed. A bet can never be more than the
        opponent can call, so every bet can always be called.
        """

        return min(
            self.chips[player],
            self.chips[other_player(player)]
        )

    def all_in_amount(self, player):
        """
        Chips an ALL_IN puts in: the player's whole stack,
        capped at what the opponent can match. Chips above
        that cap could never be won, so they stay with the
        player.
        """

        opponent = other_player(player)

        return min(
            self.chips[player],
            self.chips[opponent] + self.to_call(player)
        )

    def put_chips(self, player, amount):
        """
        Move chips from a player's stack into the pot.
        """

        self.chips[player] -= amount
        self.bets[player] += amount
        self.committed[player] += amount
        self.pot += amount

    def anyone_all_in(self):

        return any(
            self.chips[player] == 0
            for player in PLAYERS
        )

    def get_hand(self, player):
        """
        Return the requested player's private hand.
        """

        if player == "Player_1":
            return self.player_1_hand

        if player == "Player_2":
            return self.player_2_hand

        return None

    def draw_cards(self, player, indexes):
        """
        Replace selected cards with cards from the deck.
        """

        hand = self.get_hand(player)

        if hand is None:
            return

        for index in indexes:
            hand[index] = self.deck.pop()

    def switch_turn(self):
        """
        Change the active player.
        """

        self.active_player = other_player(
            self.active_player
        )

    def action_error(self, player, payload):
        """
        Check whether an action is legal in the current phase.
        Returns (code, message) if illegal, otherwise None.
        Does not change any state.
        """

        action = payload["action"]

        if self.phase in BETTING_PHASES:

            to_call = self.to_call(player)

            if action == "DRAW":
                return (
                    "INVALID_ACTION",
                    "DRAW is only allowed in the draw phase."
                )

            if action == "CHECK" and to_call > 0:
                return (
                    "INVALID_ACTION",
                    "There is a bet to answer. "
                    "CALL, ALL_IN, or FOLD."
                )

            if action == "BET" and to_call > 0:
                return (
                    "INVALID_ACTION",
                    "BET cannot raise. "
                    "CALL, ALL_IN, or FOLD."
                )

            if action == "BET":

                limit = self.max_bet(player)

                if payload["amount"] > limit:
                    return (
                        "INVALID_BET",
                        f"You can bet at most {limit}."
                    )

            if action == "CALL" and to_call == 0:
                return (
                    "INVALID_ACTION",
                    "There is no bet to call."
                )

            return None

        if self.phase == "DRAW_PHASE":

            if action != "DRAW":
                return (
                    "INVALID_ACTION",
                    "Only DRAW is allowed in the draw phase."
                )

            if player in self.drawn:
                return (
                    "INVALID_ACTION",
                    "You have already drawn this round."
                )

            return None

        return (
            "INVALID_STATE",
            "The game is not accepting actions."
        )

    def apply_action(self, player, payload):
        """
        Apply a legal action. Call action_error() first.

        Returns:
            "FOLD"     - player folded, opponent wins the round
            "SHOWDOWN" - second betting phase closed
            None       - round continues
        """

        action = payload["action"]

        if action == "FOLD":
            return "FOLD"

        if action == "CHECK":

            self.checks += 1

            # Two checks with no bet close the betting phase.
            if self.checks == 2:
                return self.close_betting()

            self.switch_turn()

            return None

        if action == "BET":

            self.put_chips(
                player,
                payload["amount"]
            )

            self.switch_turn()

            return None

        if action == "CALL":

            # Matching the bet closes the betting phase.
            self.put_chips(
                player,
                self.to_call(player)
            )

            return self.close_betting()

        if action == "ALL_IN":

            to_call = self.to_call(player)
            amount = self.all_in_amount(player)

            self.put_chips(
                player,
                amount
            )

            # Only enough to match the bet: this is a call.
            if to_call > 0 and amount == to_call:
                return self.close_betting()

            # Otherwise it is a bet or raise to answer.
            self.switch_turn()

            return None

        if action == "DRAW":

            self.draw_cards(
                player,
                payload["cards"]
            )

            self.drawn.add(
                player
            )

            if len(self.drawn) == 2:

                # An all-in player has nothing left to bet,
                # so skip the second betting phase.
                if self.anyone_all_in():
                    return "SHOWDOWN"

                self.start_phase(
                    "BETTING_2"
                )

            else:
                self.switch_turn()

            return None

        return None

    def close_betting(self):

        if self.phase == "BETTING_1":

            self.start_phase(
                "DRAW_PHASE"
            )

            return None

        return "SHOWDOWN"

    def showdown(self):
        """
        Evaluate both hands and return the round winner.
        """

        self.state = "SHOWDOWN"

        p1_score = evaluate_hand(
            self.player_1_hand
        )

        p2_score = evaluate_hand(
            self.player_2_hand
        )

        if p1_score > p2_score:
            return (
                "Player_1",
                p1_score,
                p2_score
            )

        if p2_score > p1_score:
            return (
                "Player_2",
                p1_score,
                p2_score
            )

        return (
            None,
            p1_score,
            p2_score
        )

    def record_round_win(self, winner):
        """
        Pay out the pot and update total wins and
        consecutive-win streaks.
        """

        self.state = "ROUND_OVER"

        self.last_round_winner = winner

        if winner is None:
            # Tie: everyone gets back what they put in.
            for player in PLAYERS:
                self.chips[player] += self.committed[player]

        else:
            self.chips[winner] += self.pot

        if winner == "Player_1":

            self.player_1_wins += 1
            self.player_1_streak += 1

            self.player_2_streak = 0

        elif winner == "Player_2":

            self.player_2_wins += 1
            self.player_2_streak += 1

            self.player_1_streak = 0

        else:
            # Tie resets both streaks.
            self.player_1_streak = 0
            self.player_2_streak = 0

    def check_game_over(self):
        """
        Determine whether the match has ended.
        """

        self.state = "CHECK_MATCH"

        for player in PLAYERS:

            if self.chips[player] == 0:
                return (
                    True,
                    other_player(player),
                    "OUT_OF_CHIPS"
                )

        if self.player_1_streak >= 3:
            return (
                True,
                "Player_1",
                "THREE_CONSECUTIVE_WINS"
            )

        if self.player_2_streak >= 3:
            return (
                True,
                "Player_2",
                "THREE_CONSECUTIVE_WINS"
            )

        if self.round_number >= self.max_rounds:
            return (
                True,
                None,
                "ROUND_LIMIT_DRAW"
            )

        return (
            False,
            None,
            None
        )

    def next_round(self):
        """
        Increment the round and deal a new game.
        """

        self.round_number += 1

        self.first_player = self.choose_first_player()

        self.start_round()

    def public_state(self):
        """
        Return information that is safe to send to both players.
        Private cards are intentionally excluded.
        """

        # Betting limits for the player whose turn it is.
        current_bet = 0
        max_bet = 0
        all_in = 0

        if (
            self.phase in BETTING_PHASES
            and self.active_player in PLAYERS
        ):
            current_bet = self.to_call(self.active_player)
            max_bet = self.max_bet(self.active_player)
            all_in = self.all_in_amount(self.active_player)

        return {
            "round": self.round_number,
            "active_player": self.active_player,
            "first_player": self.first_player,
            "phase": self.phase,
            "current_bet": current_bet,
            "max_bet": max_bet,
            "all_in_amount": all_in,
            "pot": self.pot,
            "player_1_chips": self.chips["Player_1"],
            "player_2_chips": self.chips["Player_2"],
            "player_1_wins": self.player_1_wins,
            "player_2_wins": self.player_2_wins,
            "player_1_streak": self.player_1_streak,
            "player_2_streak": self.player_2_streak
        }
