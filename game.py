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

        self.deck = []

        self.player_1_hand = []
        self.player_2_hand = []

        self.phase = "WAITING"

    def start_game(self):
        """
        Initialize the game and select the first player.
        """

        self.state = "GAME_START"

        self.active_player = random.choice([
            "Player_1",
            "Player_2"
        ])

        self.start_round()

    def start_round(self):
        """
        Create a fresh deck and deal five cards to each player.
        """

        self.state = "DEAL"
        self.phase = "DRAW"

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

        self.state = "PLAYER_TURN"

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

        if self.active_player == "Player_1":
            self.active_player = "Player_2"

        else:
            self.active_player = "Player_1"

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
        Update total wins and consecutive-win streaks.
        """

        self.state = "ROUND_OVER"

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

        self.start_round()

    def public_state(self):
        """
        Return information that is safe to send to both players.
        Private cards are intentionally excluded.
        """

        return {
            "round": self.round_number,
            "active_player": self.active_player,
            "phase": self.phase,
            "player_1_wins": self.player_1_wins,
            "player_2_wins": self.player_2_wins,
            "player_1_streak": self.player_1_streak,
            "player_2_streak": self.player_2_streak
        }