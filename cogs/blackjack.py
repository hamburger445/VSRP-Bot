import json
import random
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import log_action
from utils.core import load_config
from utils.database import get_db
from utils.economy import InsufficientFunds, modify_wallet
from utils.core import defer, reply

SUITS = ["Hearts", "Diamonds", "Clubs", "Spades"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _new_deck() -> list[str]:
    deck = [f"{rank} of {suit}" for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def _hand_value(cards: list[str]) -> int:
    value = 0
    aces = 0
    for card in cards:
        rank = card.split(" of ")[0]
        if rank in ("J", "Q", "K"):
            value += 10
        elif rank == "A":
            aces += 1
            value += 11
        else:
            value += int(rank)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def _format_hand(cards: list[str], hide_first: bool = False) -> str:
    if hide_first and cards:
        return f"Hidden, {', '.join(cards[1:])}"
    return ", ".join(cards)


class BlackjackView(discord.ui.View):
    def __init__(self, cog: "Blackjack", user_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your game.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._hit(interaction)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._stand(interaction)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.success)
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._double(interaction)


class Blackjack(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._decks: dict[int, list[str]] = {}

    def _cfg(self) -> dict:
        return load_config().get("blackjack", {})

    async def _get_game(self, user_id: int) -> dict | None:
        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT * FROM blackjack_games WHERE user_id = ? AND status = 'active'",
            (user_id,),
        )
        return row

    async def _build_embed(self, game: dict, reveal: bool = False) -> discord.Embed:
        player = json.loads(game["player_cards"])
        dealer = json.loads(game["dealer_cards"])
        embed = discord.Embed(title="Blackjack", color=0x2B2D31)
        embed.add_field(name="Your Hand", value=_format_hand(player), inline=False)
        embed.add_field(name="Your Total", value=str(_hand_value(player)), inline=True)
        embed.add_field(name="Bet", value=f"${game['bet']:,}", inline=True)
        if reveal:
            embed.add_field(name="Dealer Hand", value=_format_hand(dealer), inline=False)
            embed.add_field(name="Dealer Total", value=str(_hand_value(dealer)), inline=True)
        else:
            embed.add_field(name="Dealer Hand", value=_format_hand(dealer, hide_first=True), inline=False)
        return embed

    async def _end_game(self, user_id: int, outcome: str, payout: int) -> None:
        db = await get_db()
        await db.execute(
            "UPDATE blackjack_games SET status = ?, updated_at = NOW() WHERE user_id = ?",
            (outcome, user_id),
        )
        await db.commit()
        if payout != 0:
            await modify_wallet(user_id, payout, "blackjack", f"Blackjack {outcome}")
        self._decks.pop(user_id, None)
        await log_action(
            self.bot, "economy", user_id, details={"action": "blackjack", "outcome": outcome, "payout": payout}
        )

    @app_commands.command(name="blackjack", description="Play a hand of blackjack")
    @app_commands.describe(bet="Bet amount")
    async def blackjack(self, interaction: discord.Interaction, bet: int):
        cfg = self._cfg()
        min_bet = cfg.get("min_bet", 10)
        max_bet = cfg.get("max_bet", 10000)
        if bet < min_bet or bet > max_bet:
            await reply(interaction, f"Bet must be between ${min_bet:,} and ${max_bet:,}.", ephemeral=True)
            return

        await defer(interaction, ephemeral=True)
        existing = await self._get_game(interaction.user.id)
        if existing:
            embed = await self._build_embed(existing)
            view = BlackjackView(self, interaction.user.id)
            await reply(interaction, "You already have an active game.", embed=embed, view=view, ephemeral=True)
            return

        try:
            await modify_wallet(interaction.user.id, -bet, "blackjack_bet", "Blackjack bet placed")
        except InsufficientFunds:
            await reply(interaction, "Insufficient funds for this bet.", ephemeral=True)
            return

        deck = _new_deck()
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]
        self._decks[interaction.user.id] = deck

        db = await get_db()
        await db.execute(
            """
            INSERT INTO blackjack_games (user_id, bet, player_cards, dealer_cards, status)
            VALUES (?, ?, ?, ?, 'active')
            ON CONFLICT (user_id) DO UPDATE SET
                bet = excluded.bet, player_cards = excluded.player_cards,
                dealer_cards = excluded.dealer_cards, status = 'active',
                doubled = 0, updated_at = NOW()
            """,
            (interaction.user.id, bet, json.dumps(player), json.dumps(dealer)),
        )
        await db.commit()

        if _hand_value(player) == 21:
            payout = int(bet * 2.5)
            await self._end_game(interaction.user.id, "blackjack", payout)
            await reply(interaction, f"Blackjack! You won ${payout:,}.", ephemeral=True)
            return

        game = await self._get_game(interaction.user.id)
        embed = await self._build_embed(game)
        view = BlackjackView(self, interaction.user.id)
        await reply(interaction, embed=embed, view=view, ephemeral=True)

    async def _hit(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        game = await self._get_game(interaction.user.id)
        if not game:
            await reply(interaction, "No active game.", ephemeral=True)
            return
        deck = self._decks.get(interaction.user.id, _new_deck())
        player = json.loads(game["player_cards"])
        player.append(deck.pop())
        self._decks[interaction.user.id] = deck
        db = await get_db()
        await db.execute(
            "UPDATE blackjack_games SET player_cards = ?, updated_at = NOW() WHERE user_id = ?",
            (json.dumps(player), interaction.user.id),
        )
        await db.commit()
        total = _hand_value(player)
        if total > 21:
            await self._end_game(interaction.user.id, "bust", 0)
            await reply(interaction, f"Bust! You lost ${game['bet']:,}.", ephemeral=True)
            return
        game["player_cards"] = json.dumps(player)
        embed = await self._build_embed(game)
        view = BlackjackView(self, interaction.user.id)
        await reply(interaction, embed=embed, view=view, ephemeral=True)

    async def _stand(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        game = await self._get_game(interaction.user.id)
        if not game:
            await reply(interaction, "No active game.", ephemeral=True)
            return
        deck = self._decks.get(interaction.user.id, _new_deck())
        dealer = json.loads(game["dealer_cards"])
        while _hand_value(dealer) < 17:
            dealer.append(deck.pop())
        player = json.loads(game["player_cards"])
        player_val = _hand_value(player)
        dealer_val = _hand_value(dealer)
        bet = game["bet"]
        if dealer_val > 21 or player_val > dealer_val:
            payout = bet * 2
            outcome = "win"
            msg = f"You won ${payout:,}."
        elif player_val == dealer_val:
            payout = bet
            outcome = "push"
            msg = f"Push. ${payout:,} returned."
        else:
            payout = 0
            outcome = "loss"
            msg = f"You lost ${bet:,}."
        await self._end_game(interaction.user.id, outcome, payout)
        game["dealer_cards"] = json.dumps(dealer)
        embed = await self._build_embed(game, reveal=True)
        embed.description = msg
        await reply(interaction, embed=embed, ephemeral=True)

    async def _double(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        game = await self._get_game(interaction.user.id)
        if not game or game.get("doubled"):
            await reply(interaction, "Double down not available.", ephemeral=True)
            return
        player = json.loads(game["player_cards"])
        if len(player) != 2:
            await reply(interaction, "Double down only available on first action.", ephemeral=True)
            return
        try:
            await modify_wallet(interaction.user.id, -game["bet"], "blackjack_double", "Blackjack double down")
        except InsufficientFunds:
            await reply(interaction, "Insufficient funds to double.", ephemeral=True)
            return
        db = await get_db()
        await db.execute(
            "UPDATE blackjack_games SET bet = ?, doubled = 1, updated_at = NOW() WHERE user_id = ?",
            (game["bet"] * 2, interaction.user.id),
        )
        await db.commit()
        await self._hit(interaction)
        game = await self._get_game(interaction.user.id)
        if game and game["status"] == "active":
            await self._stand(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(Blackjack(bot))
