import discord
from discord import app_commands, ui
from discord.ui import Modal, TextInput, View, Button
from discord.ext import commands
import random
import os
from typing import Optional
from dotenv import load_dotenv
from datetime import datetime
import uuid

# Konfiguration laden
load_dotenv()
TOKEN = os.getenv("TOKEN")
WORDS_FILE = os.getenv("WORDS_FILE", "words.txt")
MAX_ATTEMPTS = 6

# Wörterliste laden
with open(WORDS_FILE, "r") as f:
    WORDS = [word.strip().lower() for word in f.readlines() if len(word.strip()) == 5]

class GameHistory:
    def __init__(self):
        self.games = []
        self.streak = 0
        
    def add_game(self, won: bool, attempts: int, hints: int, word: str, duration: float):
        game_id = str(uuid.uuid4())[:8].upper()
        self.games.append({
            "id": game_id,
            "date": datetime.now(),
            "won": won,
            "attempts": attempts,
            "hints": hints,
            "word": word,
            "duration": duration,
            "guesses": []
        })
        if won:
            self.streak += 1
        else:
            self.streak = 0
        return game_id

class WordleGame:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.secret_word = random.choice(WORDS)
        self.attempts = []
        self.remaining = MAX_ATTEMPTS
        self.message_id: Optional[int] = None
        self.hinted_letters = set()
        self.correct_positions = [False]*5
        self.hints_used = 0
        self.start_time = datetime.now()

    def get_duration(self):
        return (datetime.now() - self.start_time).total_seconds()

    def check_guess(self, guess: str) -> list:
        result = []
        for i, (g, s) in enumerate(zip(guess, self.secret_word)):
            if g == s:
                result.append("green")
                self.correct_positions[i] = True
            elif g in self.secret_word:
                result.append("yellow")
            else:
                result.append("gray")
        self.attempts.append((guess, result))
        self.remaining -= 1
        return result

    def add_hint(self):
        hidden_positions = [i for i, correct in enumerate(self.correct_positions) if not correct]
        if hidden_positions:
            pos = random.choice(hidden_positions)
            self.hinted_letters.add(self.secret_word[pos])
            self.hints_used += 1

    def get_hint_display(self):
        display = []
        for i, char in enumerate(self.secret_word):
            if self.correct_positions[i]:
                display.append(char.upper())
            elif char in self.hinted_letters:
                display.append(char.upper())
            else:
                display.append("▢")
        return " ".join(display)

class WordleView(View):
    def __init__(self, game: WordleGame):
        super().__init__(timeout=300)
        self.game = game

    @ui.button(label="Raten", style=discord.ButtonStyle.primary, emoji="✏️")
    async def guess_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(GuessModal(self.game))

    @ui.button(label="Tipp (0x)", style=discord.ButtonStyle.secondary, emoji="💡")
    async def hint_button(self, interaction: discord.Interaction, button: Button):
        if self.game.remaining > 0:
            self.game.add_hint()
            button.label = f"Tipp ({self.game.hints_used}x)"
            await self.update_message(interaction)
        await interaction.response.defer()

    @ui.button(label="Beenden", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def quit_button(self, interaction: discord.Interaction, button: Button):
        cog = interaction.client.get_cog("WordleCog")
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("❌ Nur der Spieler kann das Spiel beenden!", ephemeral=True)
            return
            
        cog.history.add_game(
            won=False,
            attempts=MAX_ATTEMPTS - self.game.remaining,
            hints=self.game.hints_used,
            word=self.game.secret_word,
            duration=self.game.get_duration()
        )
        del cog.games[interaction.user.id]
        await interaction.message.delete()
        await interaction.response.send_message("🎮 Spiel wurde beendet", ephemeral=True)

    async def update_message(self, interaction: discord.Interaction):
        try:
            message = await interaction.channel.fetch_message(self.game.message_id)
            await message.edit(view=self)
        except:
            pass

class GuessModal(Modal, title="Wordle Rateversuch"):
    guess = TextInput(
        label="Gib dein 5-Buchstaben-Wort ein",
        placeholder="Beispiel: apfel",
        min_length=5,
        max_length=5
    )

    def __init__(self, game: WordleGame):
        super().__init__()
        self.game = game

    async def on_submit(self, interaction: discord.Interaction):
        guess = self.guess.value.lower()
        if not guess.isalpha():
            await interaction.response.send_message("❌ Nur Buchstaben erlaubt!", ephemeral=True)
            return

        result = self.game.check_guess(guess)
        embed = self.create_embed()
        
        try:
            message = await interaction.channel.fetch_message(self.game.message_id)
            new_view = WordleView(self.game) if self.game.remaining > 0 else None
            await message.edit(embed=embed, view=new_view)
        except:
            pass

        if guess == self.game.secret_word or self.game.remaining == 0:
            await self.handle_game_end(interaction)

        await interaction.response.defer()

    def create_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"WORDLE - {MAX_ATTEMPTS} Versuche",
            color=discord.Color.blurple()
        )
        
        for idx, (guess, result) in enumerate(self.game.attempts, 1):
            blocks = " ".join([self.get_emoji(r) for r in result])
            embed.add_field(
                name=f"Versuch {idx}",
                value=f"**{guess.upper()}**\n{blocks}",
                inline=False
            )
            
        status = f"🔄 {self.game.remaining} Versuche übrig | Tipps: {self.game.hints_used}"
        hint_display = self.game.get_hint_display()
        
        embed.add_field(
            name="Aktueller Hinweis",
            value=f"`{hint_display}`",
            inline=False
        )
            
        if self.game.remaining == 0:
            status = f"💥 Spiel beendet! Wort: ||{self.game.secret_word.upper()}||"
            
        embed.set_footer(text=f"{status} | Spiel ID: {self.game.get_duration():.0f}s")
        return embed

    def get_emoji(self, result: str) -> str:
        return {
            "green": "🟩",
            "yellow": "🟨",
            "gray": "⬛"
        }[result]

    async def handle_game_end(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("WordleCog")
        won = self.game.secret_word == self.guess.value.lower()
        game_id = cog.history.add_game(
            won=won,
            attempts=len(self.game.attempts),
            hints=self.game.hints_used,
            word=self.game.secret_word,
            duration=self.game.get_duration()
        )
    
        final_view = View(timeout=60)
        
        new_game_btn = Button(label="Neues Spiel", style=discord.ButtonStyle.success, emoji="🔄")
        stats_btn = Button(label="Statistiken", style=discord.ButtonStyle.primary, emoji="📊")
    
        async def new_game_callback(interaction: discord.Interaction):
            await cog.start_new_game(interaction)
    
        async def stats_callback(interaction: discord.Interaction):
            await cog.show_stats(interaction)
    
        new_game_btn.callback = new_game_callback
        stats_btn.callback = stats_callback

        final_view.add_item(new_game_btn)
        final_view.add_item(stats_btn)
    
        try:
            message = await interaction.channel.fetch_message(self.game.message_id)
            await message.edit(view=final_view)
            del cog.games[interaction.user.id]
        except:
            pass

class HistoryView(View):
    def __init__(self, cog, user_id, page=0):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.page = page
        self.max_page = len(self.cog.history.games) - 1
        
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        
        first = Button(emoji="⏮️", style=discord.ButtonStyle.secondary, disabled=self.page == 0)
        prev = Button(emoji="◀️", style=discord.ButtonStyle.primary, disabled=self.page == 0)
        next_btn = Button(emoji="▶️", style=discord.ButtonStyle.primary, disabled=self.page >= self.max_page)
        last = Button(emoji="⏭️", style=discord.ButtonStyle.secondary, disabled=self.page >= self.max_page)
        
        first.callback = self.first_page
        prev.callback = self.prev_page
        next_btn.callback = self.next_page
        last.callback = self.last_page
        
        self.add_item(first)
        self.add_item(prev)
        self.add_item(next_btn)
        self.add_item(last)

    async def first_page(self, interaction: discord.Interaction):
        self.page = 0
        await self.update_view(interaction)

    async def prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        await self.update_view(interaction)

    async def next_page(self, interaction: discord.Interaction):
        self.page = min(self.max_page, self.page + 1)
        await self.update_view(interaction)

    async def last_page(self, interaction: discord.Interaction):
        self.page = self.max_page
        await self.update_view(interaction)

    async def update_view(self, interaction: discord.Interaction):
        self.update_buttons()
        embed = self.create_history_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_history_embed(self):
        game = self.cog.history.games[self.page]
        embed = discord.Embed(
            title=f"Spielverlauf #{self.page + 1}",
            color=discord.Color.blue() if game['won'] else discord.Color.red(),
            description=f"**ID:** {game['id']}\n**Datum:** {game['date'].strftime('%d.%m.%Y %H:%M')}"
        )
        
        embed.add_field(name="Wort", value=f"||{game['word']}||", inline=True)
        embed.add_field(name="Versuche", value=game['attempts'], inline=True)
        embed.add_field(name="Tipps", value=game['hints'], inline=True)
        embed.add_field(name="Dauer", value=self.format_duration(game['duration']), inline=True)
        embed.add_field(name="Ergebnis", value="🏆 Gewonnen" if game['won'] else "💥 Verloren", inline=True)
        
        for idx, guess in enumerate(game['guesses'], 1):
            blocks = " ".join([self.get_emoji(r) for r in guess[1]])
            embed.add_field(
                name=f"Versuch {idx}",
                value=f"**{guess[0].upper()}**\n{blocks}",
                inline=False
            )
            
        embed.set_footer(text=f"Seite {self.page + 1}/{self.max_page + 1}")
        return embed

    def format_duration(self, seconds: float) -> str:
        minutes, seconds = divmod(seconds, 60)
        return f"{int(minutes)}m {int(seconds)}s"

    def get_emoji(self, result: str) -> str:
        return {
            "green": "🟩",
            "yellow": "🟨",
            "gray": "⬛"
        }[result]

class WordleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games = {}
        self.history = GameHistory()

    async def start_new_game(self, interaction: discord.Interaction):
        game = WordleGame(interaction.user.id)
        self.games[interaction.user.id] = game
        
        embed = discord.Embed(
            title="🎮 Neues Wordle-Spiel gestartet!",
            color=discord.Color.green(),
            description=f"Du hast **{MAX_ATTEMPTS} Versuche** um das Wort zu erraten!"
        )
        embed.add_field(
            name="Steuerung",
            value="• ✏️ Raten - Öffnet das Eingabefenster\n• 💡 Tipp - Zeigt Buchstaben an\n• 🗑️ Beenden - Spiel abbrechen",
            inline=False
        )
        embed.set_thumbnail(url="https://i.imgur.com/V7gJd3W.png")
        
        view = WordleView(game)
        message = await interaction.channel.send(embed=embed, view=view)
        game.message_id = message.id

    @app_commands.command(name="wordle", description="Starte ein neues Wordle-Spiel")
    async def start_game(self, interaction: discord.Interaction):
        if interaction.user.id in self.games:
            await interaction.response.send_message("❌ Du hast bereits ein aktives Spiel!", ephemeral=True)
            return
            
        await self.start_new_game(interaction)
        await interaction.response.send_message("🎮 Spiel wurde im Kanal gestartet!", ephemeral=True)

    @app_commands.command(name="stats", description="Zeige deine Wordle-Statistiken")
    async def show_stats(self, interaction: discord.Interaction):
        total_games = len(self.history.games)
        wins = sum(1 for g in self.history.games if g['won'])
        losses = total_games - wins
        total_duration = sum(g['duration'] for g in self.history.games)
        avg_duration = total_duration / total_games if total_games else 0
        
        embed = discord.Embed(
            title=f"📊 Statistiken für {interaction.user.name}",
            color=discord.Color.gold(),
            description=f"**Gesamtspiele:** {total_games}\n**Gesamtspielzeit:** {self.format_duration(total_duration)}"
        )
        
        embed.add_field(name="🏆 Gewonnen", value=f"{wins} ({(wins/total_games*100):.1f}%)" if total_games else "0", inline=True)
        embed.add_field(name="💥 Verloren", value=losses, inline=True)
        embed.add_field(name="🔥 Aktuelle Serie", value=self.history.streak, inline=True)
        
        # Korrigierte Felder
        embed.add_field(
            name="🎯 Durchschn. Versuche", 
            value=f"{sum(g['attempts'] for g in self.history.games)/total_games:.1f}" if total_games else "-", 
            inline=True
        )
        embed.add_field(
            name="💡 Durchschn. Tipps", 
            value=f"{sum(g['hints'] for g in self.history.games)/total_games:.1f}" if total_games else "-", 
            inline=True
        )
        embed.add_field(
            name="⏱️ Durchschn. Dauer", 
            value=self.format_duration(avg_duration) if total_games else "-", 
            inline=True
        )
        
        if self.history.games:
            last_game = self.history.games[-1]
            last_result = "🏆 Gewonnen" if last_game['won'] else "💥 Verloren"
            embed.add_field(
                name="Letztes Spiel",
                value=f"**ID:** {last_game['id']}\n**Wort:** ||{last_game['word']}||\n**Versuche:** {last_game['attempts']}\n**Dauer:** {self.format_duration(last_game['duration'])}",
                inline=False
            )
            
        view = View()
        history_btn = Button(label="Spielverlauf", style=discord.ButtonStyle.primary, emoji="📜")
        history_btn.callback = self.show_history_callback
        view.add_item(history_btn)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_history_callback(self, interaction: discord.Interaction):
        await self.show_history(interaction)

    @app_commands.command(name="history", description="Zeige deine Spielhistorie an")
    async def show_history(self, interaction: discord.Interaction):
        if not self.history.games:
            await interaction.response.send_message("📭 Keine Spiele in der Historie!", ephemeral=True)
            return
            
        view = HistoryView(self, interaction.user.id)
        embed = view.create_history_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    def format_duration(self, seconds: float) -> str:
        minutes, seconds = divmod(seconds, 60)
        return f"{int(minutes)}m {int(seconds)}s"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.add_cog(WordleCog(bot))
    await bot.tree.sync()
    print(f"{bot.user} ist online!")

bot.run(TOKEN)
