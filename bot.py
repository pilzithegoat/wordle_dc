import discord
import json
import random
import os
import uuid
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from discord import app_commands, ui
from discord.ui import Modal, TextInput, View, Button, Select
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")
WORDS_FILE = os.getenv("WORDS_FILE", "words.txt")
MAX_ATTEMPTS = 6
MAX_HINTS = 3  # Maximale Anzahl an Tipps
DATA_FILE = "wordle_data.json"
CONFIG_FILE = "server_config.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

class ServerConfig:
    def __init__(self):
        self.config = self.load_config()
    
    def load_config(self):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=2)
    
    def set_wordle_channel(self, guild_id: int, channel_id: int):
        self.config[str(guild_id)] = channel_id
        self.save_config()
    
    def get_wordle_channel(self, guild_id: int) -> Optional[int]:
        return self.config.get(str(guild_id))

class GameHistory:
    def __init__(self):
        self.data = self.load_data()
    
    def load_data(self):
        try:
            with open(DATA_FILE) as f:
                data = json.load(f)
                return data if "users" in data else {"users": {}}
        except (FileNotFoundError, json.JSONDecodeError):
            return {"users": {}}
    
    def save_data(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.data, f, indent=2)
    
    def add_game(self, user_id: int, game_data: dict):
        user_id = str(user_id)
        if user_id not in self.data["users"]:
            self.data["users"][user_id] = []
            
        game_data.update({
            "id": str(uuid.uuid4())[:8].upper(),
            "timestamp": datetime.now().isoformat(),
            "attempts": len(game_data["guesses"]),
            "hints": game_data["hints"],
            "guesses": [{"word": g[0], "result": g[1]} for g in game_data["guesses"]]
        })
        
        self.data["users"][user_id].insert(0, game_data)
        self.save_data()
    
    def get_user_games(self, user_id: int) -> List[dict]:
        return self.data["users"].get(str(user_id), [])
    
    def get_leaderboard(self) -> List[dict]:
        leaderboard = []
        for user_id, games in self.data["users"].items():
            wins = sum(1 for g in games if g["won"])
            total = len(games)
            leaderboard.append({
                "user_id": int(user_id),
                "wins": wins,
                "total": total,
                "win_rate": wins/total if total > 0 else 0,
                "avg_attempts": sum(len(g["guesses"]) for g in games)/total if total > 0 else 0
            })
        return sorted(leaderboard, key=lambda x: (-x["wins"], -x["win_rate"]))

class WordleGame:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.secret_word = random.choice(WORDS)
        self.attempts = []
        self.remaining = MAX_ATTEMPTS
        self.hints_used = 0
        self.start_time = datetime.now()
        self.correct_positions = [False]*5
        self.hinted_letters = set()
    
    def get_duration(self):
        return (datetime.now() - self.start_time).total_seconds()
    
    def check_guess(self, guess: str) -> List[str]:
        self.correct_positions = [False]*5
        result = []
        for i, (g, s) in enumerate(zip(guess, self.secret_word)):
            if g == s:
                result.append("🟩")
                self.correct_positions[i] = True
            elif g in self.secret_word:
                result.append("🟨")
            else:
                result.append("⬛")
        self.attempts.append((guess, result.copy()))
        self.remaining -= 1
        return result
    
    def add_hint(self):
        if self.hints_used >= MAX_HINTS:
            return False
            
        hidden_positions = [i for i, correct in enumerate(self.correct_positions) if not correct]
        if hidden_positions:
            pos = random.choice(hidden_positions)
            self.hinted_letters.add(self.secret_word[pos])
            self.hints_used += 1
            return True
        return False
    
    @property
    def hint_display(self):
        display = []
        for i, char in enumerate(self.secret_word):
            if self.correct_positions[i] or char in self.hinted_letters:
                display.append(char.upper())
            else:
                display.append("▢")
        return " ".join(display)

class DateFilterModal(Modal, title="Historie filtern"):
    start_date = TextInput(
        label="Startdatum (TT.MM.JJJJ)",
        placeholder="01.01.2023",
        required=False
    )
    end_date = TextInput(
        label="Enddatum (TT.MM.JJJJ)",
        placeholder="31.12.2023",
        required=False
    )

    def __init__(self, cog, user_id):
        super().__init__()
        self.cog = cog
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start_str = self.start_date.value
            end_str = self.end_date.value
            
            start = datetime.strptime(start_str, "%d.%m.%Y") if start_str else None
            end = (datetime.strptime(end_str, "%d.%m.%Y") + timedelta(days=1)) if end_str else None
        except ValueError:
            await interaction.response.send_message("❌ Ungültiges Datumsformat! Verwende TT.MM.JJJJ", ephemeral=True)
            return

        view = HistoryView(self.cog, self.user_id, date_filter=(start, end))
        await interaction.response.edit_message(embed=view.create_history_embed(), view=view)

class HistoryView(View):
    def __init__(self, cog, user_id, page=0, date_filter=None):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.page = page
        self.date_filter = date_filter
        self.update_buttons()
    
    def get_filtered_games(self):
        games = self.cog.history.get_user_games(self.user_id)
        if not self.date_filter:
            return games
            
        start, end = self.date_filter
        filtered = []
        for g in games:
            game_date = datetime.fromisoformat(g["timestamp"])
            if (not start or game_date >= start) and (not end or game_date <= end):
                filtered.append(g)
        return filtered
    
    def create_history_embed(self) -> discord.Embed:
        user_games = self.get_filtered_games()
        total_pages = max(len(user_games), 1)
        
        embed = discord.Embed(
            title=f"📜 Spielhistorie - Seite {self.page + 1}/{total_pages}",
            color=discord.Color.blue()
        )
        
        if user_games and self.page < len(user_games):
            game = user_games[self.page]
            status = "✅ Gewonnen" if game["won"] else "❌ Verloren"
            date = datetime.fromisoformat(game["timestamp"]).strftime("%d.%m.%Y %H:%M")
            duration = self.cog.format_duration(game["duration"])
            
            embed.description = f"**{status}** • {date} • {duration}"
            embed.add_field(name="Wort", value=f"||{game['word'].upper()}||", inline=False)
            
            attempts = []
            for idx, guess in enumerate(game["guesses"]):
                attempts.append(
                    f"**Versuch {idx + 1}:** {guess['word'].upper()}\n"
                    f"{' '.join(guess['result'])}"
                )
            embed.add_field(name="Versuche", value="\n\n".join(attempts) or "Keine Versuche", inline=False)
            embed.add_field(name="Tipps verwendet", value=game["hints"], inline=True)
            embed.set_footer(text=f"Spiel-ID: {game['id']}")
        else:
            embed.description = "📭 Keine Spiele im gewählten Zeitraum!"
            
        return embed
    
    def update_buttons(self):
        user_games = self.get_filtered_games()
        total_pages = max(len(user_games), 1)
        self.first_page.disabled = self.page <= 0
        self.prev_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= total_pages - 1
        self.last_page.disabled = self.page >= total_pages - 1
    
    @ui.button(emoji="⏮️", style=discord.ButtonStyle.gray)
    async def first_page(self, interaction: discord.Interaction, button: Button):
        self.page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_history_embed(), view=self)
    
    @ui.button(emoji="⬅️", style=discord.ButtonStyle.gray)
    async def prev_page(self, interaction: discord.Interaction, button: Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_history_embed(), view=self)
    
    @ui.button(emoji="➡️", style=discord.ButtonStyle.gray)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_history_embed(), view=self)
    
    @ui.button(emoji="⏭️", style=discord.ButtonStyle.gray)
    async def last_page(self, interaction: discord.Interaction, button: Button):
        self.page = len(self.get_filtered_games()) - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.create_history_embed(), view=self)
    
    @ui.button(emoji="🗓️", style=discord.ButtonStyle.grey)
    async def filter_date(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(DateFilterModal(self.cog, self.user_id))

class EnhancedLeaderboardView(View):
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.cog = cog
        self.mode = "leaderboard"
        self.leaderboard_data = []
        self.recent_games = []
        self.select_menu = None
        self.initialize_data()
        self.create_components()
    
    def initialize_data(self):
        self.leaderboard_data = self.cog.history.get_leaderboard()[:10]
        
        all_games = []
        for user_id_str, games in self.cog.history.data["users"].items():
            user_id = int(user_id_str)
            for game in games:
                game_copy = game.copy()
                game_copy["user_id"] = user_id
                all_games.append(game_copy)
        
        self.recent_games = sorted(
            all_games,
            key=lambda x: x["timestamp"],
            reverse=True
        )[:10]
    
    def create_components(self):
        self.clear_items()
        
        sorts = {
            "🏆 Siege": "wins",
            "📈 Winrate": "win_rate",
            "🎯 Avg. Versuche": "avg_attempts"
        }
        
        for label, mode in sorts.items():
            btn = Button(label=label, style=discord.ButtonStyle.secondary)
            btn.callback = lambda i, m=mode: self.sort_leaderboard(i, m)
            self.add_item(btn)
        
        recent_btn = Button(label="🕒 Letzte Spiele", style=discord.ButtonStyle.primary)
        recent_btn.callback = self.show_recent_games
        self.add_item(recent_btn)
        
        if self.leaderboard_data:
            options = []
            for entry in self.leaderboard_data:
                user = self.cog.bot.get_user(entry["user_id"])
                label = user.display_name if user else f"Unbekannt ({entry['user_id']})"
                options.append(discord.SelectOption(label=label, value=str(entry["user_id"])))
            
            self.select_menu = Select(
                placeholder="🎖️ Spieler auswählen",
                options=options
            )
            self.select_menu.callback = self.select_player
            self.add_item(self.select_menu)
    
    async def sort_leaderboard(self, interaction: discord.Interaction, mode: str):
        self.mode = "leaderboard"
        embed = self.create_leaderboard_embed(mode)
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def show_recent_games(self, interaction: discord.Interaction):
        self.mode = "recent"
        await interaction.response.edit_message(embed=self.create_recent_embed(), view=self)
    
    def create_leaderboard_embed(self, sort_mode="wins"):
        sorted_data = sorted(
            self.leaderboard_data,
            key=lambda x: -x[sort_mode]
        )
        
        embed = discord.Embed(
            title=f"🏆 Rangliste - {sort_mode.replace('_', ' ').title()}",
            color=discord.Color.gold()
        )
        
        for idx, entry in enumerate(sorted_data, 1):
            user = self.cog.bot.get_user(entry["user_id"])
            name = user.display_name if user else f"Unbekannt ({entry['user_id']})"
            
            embed.add_field(
                name=f"{idx}. {name}",
                value=(
                    f"Siege: {entry['wins']}\n"
                    f"Spiele: {entry['total']}\n"
                    f"Winrate: {entry['win_rate']:.0%}\n"
                    f"Avg. Versuche: {entry['avg_attempts']:.1f}"
                ),
                inline=False
            )
        
        embed.set_footer(text="Wähle einen Spieler aus dem Menü unten um die Historie anzuzeigen")
        return embed
    
    def create_recent_embed(self):
        embed = discord.Embed(
            title="🕒 Letzte Spiele",
            color=discord.Color.blurple()
        )
        
        for game in self.recent_games:
            user = self.cog.bot.get_user(game["user_id"])
            name = user.display_name if user else f"Unbekannt ({game['user_id']})"
            status = "✅ Gewonnen" if game["won"] else "❌ Verloren"
            date = datetime.fromisoformat(game["timestamp"]).strftime("%d.%m.%Y %H:%M")
            embed.add_field(
                name=f"{name} - {date}",
                value=f"{status} | Wort: ||{game['word'].upper()}|| | Versuche: {len(game['guesses'])}/{MAX_ATTEMPTS}",
                inline=False
            )
        
        return embed
    
    async def select_player(self, interaction: discord.Interaction):
        selected_id = int(self.select_menu.values[0])
        view = HistoryView(self.cog, selected_id)
        await interaction.response.edit_message(embed=view.create_history_embed(), view=view)

class MainMenu(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @ui.button(label="Neues Spiel 🎮", style=discord.ButtonStyle.green, custom_id="new_game")
    async def new_game(self, interaction: discord.Interaction, button: Button):
        cog = interaction.client.get_cog("WordleCog")
        await cog.handle_new_game(interaction)
    
    @ui.button(label="Statistiken 📊", style=discord.ButtonStyle.blurple, custom_id="stats")
    async def show_stats(self, interaction: discord.Interaction, button: Button):
        cog = interaction.client.get_cog("WordleCog")
        await cog.handle_show_stats(interaction)
    
    @ui.button(label="Historie 📜", style=discord.ButtonStyle.gray, custom_id="history")
    async def show_history(self, interaction: discord.Interaction, button: Button):
        cog = interaction.client.get_cog("WordleCog")
        await cog.handle_show_history(interaction)
    
    @ui.button(label="Rangliste 🏆", style=discord.ButtonStyle.success, custom_id="leaderboard")
    async def show_leaderboard(self, interaction: discord.Interaction, button: Button):
        cog = interaction.client.get_cog("WordleCog")
        await cog.handle_show_leaderboard(interaction)
    
    @ui.button(label="Hilfe ❓", style=discord.ButtonStyle.secondary, custom_id="help")
    async def show_help(self, interaction: discord.Interaction, button: Button):
        cog = interaction.client.get_cog("WordleCog")
        await cog.handle_show_help(interaction)

class GameView(View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.update_buttons()
    
    def update_buttons(self):
        game = self.cog.games.get(self.user_id)
        hint_count = game.hints_used if game else 0
        
        self.clear_items()
        
        guess_btn = Button(
            style=discord.ButtonStyle.primary,
            label="Raten ✏️",
            custom_id=f"guess_{self.user_id}"
        )
        guess_btn.callback = self.guess_callback
        
        hint_btn = Button(
            style=discord.ButtonStyle.secondary,
            label=f"Tipp 💡 ({hint_count}x)",
            custom_id=f"hint_{self.user_id}",
            disabled=hint_count >= MAX_HINTS
        )
        hint_btn.callback = self.hint_callback
        
        quit_btn = Button(
            style=discord.ButtonStyle.danger,
            label="Beenden 🗑️",
            custom_id=f"quit_{self.user_id}"
        )
        quit_btn.callback = self.quit_callback
        
        self.add_item(guess_btn)
        self.add_item(hint_btn)
        self.add_item(quit_btn)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Dies ist nicht dein Spiel!", ephemeral=True)
            return False
        return True
    
    async def guess_callback(self, interaction: discord.Interaction):
        if interaction.response.is_done():
            return
            
        await interaction.response.send_modal(GuessModal(self.cog))
    
    async def hint_callback(self, interaction: discord.Interaction):
        await self.cog.handle_give_hint(interaction)
    
    async def quit_callback(self, interaction: discord.Interaction):
        await self.cog.handle_end_game(interaction, won=False)

class GuessModal(Modal, title="Wordle-Ratespiel"):
    guess = TextInput(
        label="Gib dein 5-Buchstaben-Wort ein",
        placeholder="Beispiel: apfel",
        min_length=5,
        max_length=5
    )
    
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    
    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_process_guess(interaction, self.guess.value.lower())

class WordleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: Dict[int, WordleGame] = {}
        self.history = GameHistory()
        self.config = ServerConfig()
        self.persistent_views_added = False
    
    async def add_persistent_views(self):
        if not self.persistent_views_added:
            self.bot.add_view(MainMenu())
            self.persistent_views_added = True
    
    @app_commands.command(name="wordle", description="Starte ein neues Wordle-Spiel")
    async def wordle(self, interaction: discord.Interaction):
        await self.handle_new_game(interaction)
    
    @app_commands.command(name="wordle_setup", description="Richte den Wordle-Channel ein")
    @app_commands.default_permissions(administrator=True)
    async def wordle_setup(self, interaction: discord.Interaction):
        await self.handle_setup(interaction)
    
    async def check_channel(self, interaction: discord.Interaction) -> bool:
        channel_id = self.config.get_wordle_channel(interaction.guild_id)
        if interaction.channel_id != channel_id:
            await interaction.response.send_message(
                "❌ Wordle kann nur im vorgesehenen Channel gespielt werden!",
                ephemeral=True
            )
            return False
        return True
    
    async def handle_new_game(self, interaction: discord.Interaction):
        if not await self.check_channel(interaction):
            return
        
        # Vorhandenes Spiel entfernen falls vorhanden
        if interaction.user.id in self.games:
            del self.games[interaction.user.id]
            
        self.games[interaction.user.id] = WordleGame(interaction.user.id)
        game = self.games[interaction.user.id]
        
        embed = discord.Embed(
            title="🎮 Neues Wordle-Spiel",
            description="🔤 Errate das geheime 5-Buchstaben-Wort in 6 Versuchen!\n\n"
                      "💡 **Tipps:**\n"
                      "- 🟩 = Richtiger Buchstabe am richtigen Platz\n"
                      "- 🟨 = Richtiger Buchstabe am falschen Platz\n"
                      "- ⬛ = Buchstabe nicht im Wort",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Steuerung",
            value=f"• ✏️ Raten - Wort eingeben\n• 💡 Tipp - Buchstaben enthüllen (max. {MAX_HINTS}x)\n• 🗑️ Beenden - Spiel abbrechen",
            inline=False
        )
        
        view = GameView(self, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view)
    
    async def handle_process_guess(self, interaction: discord.Interaction, guess: str):
        try:
            if interaction.user.id not in self.games:
                await interaction.response.send_message("❌ Starte erst ein Spiel!", ephemeral=True)
                return
            
            game = self.games[interaction.user.id]
            
            if len(guess) != 5 or not guess.isalpha():
                await interaction.response.send_message("❌ Ungültige Eingabe!", ephemeral=True)
                return
            
            result = game.check_guess(guess)
            embed = discord.Embed(
                title=f"Wordle - {MAX_ATTEMPTS} Versuche",
                description=f"Verbleibende Versuche: {game.remaining}",
                color=discord.Color.blurple()
            )
            
            for idx, (attempt, res) in enumerate(game.attempts):
                embed.add_field(
                    name=f"Versuch {idx + 1}",
                    value=f"**{attempt.upper()}**\n{' '.join(res)}",
                    inline=False
                )
            
            embed.add_field(name="Aktueller Hinweis", value=f"`{game.hint_display}`", inline=False)
            
            if guess == game.secret_word or game.remaining == 0:
                await self.handle_end_game(interaction, guess == game.secret_word)
            else:
                view = GameView(self, interaction.user.id)
                await interaction.response.edit_message(embed=embed, view=view)
        
        except Exception as e:
            print(f"Fehler beim Raten: {e}")
            await interaction.response.send_message("❌ Fehler beim Verarbeiten des Versuchs!", ephemeral=True)

    async def handle_give_hint(self, interaction: discord.Interaction):
        try:
            if interaction.user.id not in self.games:
                await interaction.response.send_message("❌ Starte erst ein Spiel!", ephemeral=True)
                return
            
            game = self.games[interaction.user.id]
            if not game.add_hint():
                await interaction.response.send_message("❌ Maximal 3 Tipps pro Spiel!", ephemeral=True)
                return
            
            embed = interaction.message.embeds[0]
            embed.set_field_at(
                -1,
                name="Aktueller Hinweis",
                value=f"`{game.hint_display}`",
                inline=False
            )
            
            view = GameView(self, interaction.user.id)
            
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                await interaction.response.edit_message(embed=embed, view=view)
        
        except Exception as e:
            print(f"Fehler bei Tipp: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Fehler beim Verarbeiten des Tipps!", ephemeral=True)

    async def handle_end_game(self, interaction: discord.Interaction, won: bool):
        try:
            if interaction.user.id not in self.games:
                return
            
            game = self.games.pop(interaction.user.id, None)
            if game is None:
                return
            
            self.history.add_game(interaction.user.id, {
                "won": won,
                "word": game.secret_word,
                "guesses": game.attempts,
                "hints": game.hints_used,
                "duration": game.get_duration()
            })
            
            embed = discord.Embed(
                title="🎉 Gewonnen!" if won else "💥 Verloren!",
                description=f"Das Wort war: ||{game.secret_word.upper()}||",
                color=discord.Color.green() if won else discord.Color.red()
            )
            
            final_view = View(timeout=60)
            
            async def new_game_callback(interaction: discord.Interaction):
                await self.handle_new_game(interaction)
                await interaction.message.delete()
            
            async def stats_callback(interaction: discord.Interaction):
                await self.handle_show_stats(interaction)
                await interaction.message.delete()
            
            new_game_btn = Button(label="Neues Spiel 🎮", style=discord.ButtonStyle.success)
            stats_btn = Button(label="Statistiken 📊", style=discord.ButtonStyle.primary)
            
            new_game_btn.callback = new_game_callback
            stats_btn.callback = stats_callback
            
            final_view.add_item(new_game_btn)
            final_view.add_item(stats_btn)
            
            await interaction.response.edit_message(embed=embed, view=final_view)
            
            await asyncio.sleep(10)
            try:
                await interaction.delete_original_response()
            except discord.NotFound:
                pass
        
        except Exception as e:
            print(f"Fehler beim Beenden: {e}")

    async def handle_show_stats(self, interaction: discord.Interaction):
        try:
            user_games = self.history.get_user_games(interaction.user.id)
            total_games = len(user_games)
            wins = sum(1 for g in user_games if g["won"])
            losses = total_games - wins
            
            embed = discord.Embed(
                title=f"📊 Statistiken für {interaction.user.name}",
                color=discord.Color.gold()
            )
            
            if total_games > 0:
                total_duration = sum(g["duration"] for g in user_games)
                avg_duration = total_duration / total_games
                win_percent = (wins / total_games) * 100
                
                current_streak = 0
                for game in user_games:
                    if game["won"]:
                        current_streak += 1
                    else:
                        break
                
                embed.description = f"**Gesamtspiele:** {total_games}\n**Gesamtspielzeit:** {self.format_duration(total_duration)}"
                embed.add_field(name="🏆 Gewonnen", value=f"{wins} ({win_percent:.1f}%)", inline=True)
                embed.add_field(name="💥 Verloren", value=losses, inline=True)
                embed.add_field(name="🔥 Aktuelle Serie", value=current_streak, inline=True)
                embed.add_field(name="🎯 Durchschn. Versuche", value=f"{sum(len(g['guesses']) for g in user_games)/total_games:.1f}", inline=True)
                embed.add_field(name="💡 Durchschn. Tipps", value=f"{sum(g['hints'] for g in user_games)/total_games:.1f}", inline=True)
                embed.add_field(name="⏱️ Durchschn. Dauer", value=self.format_duration(avg_duration), inline=True)
            else:
                embed.description = "📭 Keine Spiele gespielt!"
            
            view = View()
            history_btn = Button(label="Spielverlauf 📜", style=discord.ButtonStyle.primary)
            history_btn.callback = self.handle_show_history
            view.add_item(history_btn)
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        except Exception as e:
            print(f"Fehler in Statistiken: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Fehler beim Laden der Statistiken!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Fehler beim Laden der Statistiken!", ephemeral=True)

    async def handle_show_history(self, interaction: discord.Interaction):
        try:
            view = HistoryView(self, interaction.user.id)
            await interaction.response.send_message(embed=view.create_history_embed(), view=view, ephemeral=True)
        except Exception as e:
            print(f"Fehler in Historie: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Fehler beim Laden der Historie!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Fehler beim Laden der Historie!", ephemeral=True)

    async def handle_show_leaderboard(self, interaction: discord.Interaction):
        try:
            view = EnhancedLeaderboardView(self)
            await interaction.response.send_message(
                embed=view.create_leaderboard_embed(),
                view=view,
                ephemeral=True
            )
        except Exception as e:
            print(f"Fehler in Rangliste: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Fehler beim Laden der Rangliste!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Fehler beim Laden der Rangliste!", ephemeral=True)

    async def handle_show_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="❓ Wordle-Hilfe",
            description="🌟 **Willkommen beim Wordle-Bot!** 🌟",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🎮 **Spielregeln**",
            value="• Errate das 5-Buchstaben-Wort in 6 Versuchen\n"
                  "• Farben zeigen an, wie nah dein Versuch war:\n"
                  "  🟩 = Richtiger Buchstabe an richtiger Position\n"
                  "  🟨 = Buchstabe im Wort, aber falsche Position\n"
                  "  ⬛ = Buchstabe nicht im Wort\n"
                  "• Nutze 💡 Tipps um Buchstaben aufzudecken (max. 3x)",
            inline=False
        )
        
        embed.add_field(
            name="📊 **Statistiken**",
            value="• Zeigt deine persönlichen Erfolge an\n"
                  "• Gewinnrate, durchschnittliche Versuche\n"
                  "• Aktuelle Gewinnserie und Spielzeit",
            inline=False
        )
        
        embed.add_field(
            name="📜 **Historie**",
            value="• Zeigt alle deine bisherigen Spiele\n"
                  "• Filterfunktion nach Datum verfügbar\n"
                  "• Detailansicht für jeden Versuch",
            inline=False
        )
        
        embed.add_field(
            name="🏆 **Rangliste**",
            value="• Vergleiche dich mit anderen Spielern\n"
                  "• Verschiedene Sortieroptionen verfügbar\n"
                  "• Zeigt die letzten Spiele aller Spieler",
            inline=False
        )
        
        embed.add_field(
            name="⚙️ **Tipps & Tricks**",
            value="• Beginne mit Wörtern mit vielen Vokalen\n"
                  "• Nutze Tipps strategisch bei schwierigen Wörtern\n"
                  "• Beobachte die Farbsymbole für Muster",
            inline=False
        )
        
        embed.add_field(
            name="🔧 **Befehle**",
            value="• `/wordle` - Starte ein neues Spiel\n"
                  "• Klicke die Buttons im Hauptmenü\n"
                  "• Admins: `/wordle_setup` zum Einrichten",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def handle_setup(self, interaction: discord.Interaction):
        try:
            self.config.set_wordle_channel(interaction.guild_id, interaction.channel_id)
            
            main_embed = discord.Embed(
                title="🎮 Wordle-Hauptmenü",
                description=(
                    "🌟 **Wordle-Spielmenü** 🌟\n\n"
                    "Teste dein Vokabular und errate das geheime Wort!\n"
                    "Klicke auf die Buttons unten um zu spielen oder "
                    "deine Statistiken einzusehen."
                ),
                color=discord.Color.blue()
            )
            main_embed.set_thumbnail(url="https://i.imgur.com/7kFU4b3.png")
            
            try:
                await interaction.channel.purge(limit=1)
            except:
                pass
                
            await interaction.channel.send(embed=main_embed, view=MainMenu())
            await interaction.response.send_message("✅ Channel erfolgreich eingerichtet!", ephemeral=True)
        
        except Exception as e:
            print(f"Fehler im Setup: {e}")
            await interaction.response.send_message("❌ Fehler beim Einrichten des Channels!", ephemeral=True)
    
    def format_duration(self, seconds: float) -> str:
        minutes, seconds = divmod(seconds, 60)
        return f"{int(minutes)}m {int(seconds)}s"

@bot.event
async def on_ready():
    await bot.add_cog(WordleCog(bot))
    cog = bot.get_cog("WordleCog")
    await cog.add_persistent_views()
    
    await bot.tree.sync()
    
    for guild in bot.guilds:
        if channel_id := cog.config.get_wordle_channel(guild.id):
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    await channel.purge(limit=1)
                    main_embed = discord.Embed(
                        title="🎮 Wordle-Hauptmenü",
                        description=(
                            "🌟 **Willkommen beim Wordle-Spiel!** 🌟\n\n"
                            "Klicke auf 'Neues Spiel 🎮' um zu starten!\n"
                            "Verwende die Buttons unten zur Navigation."
                        ),
                        color=discord.Color.blue()
                    )
                    await channel.send(embed=main_embed, view=MainMenu())
                except:
                    pass
    
    print(f"{bot.user} ist bereit!")

if __name__ == "__main__":
    if not os.path.exists(WORDS_FILE):
        with open(WORDS_FILE, "w") as f:
            f.write("apfel\nbirne\nbanane\nmango\nbeere\n")
        print(f"Beispiel-Wörterdatei {WORDS_FILE} erstellt!")
    
    with open(WORDS_FILE) as f:
        WORDS = [word.strip().lower() for word in f.readlines() if len(word.strip()) == 5]
    
    if not WORDS:
        raise ValueError("Keine gültigen Wörter in der Datei!")
    
    bot.run(TOKEN)
