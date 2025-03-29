import discord
import json
import random
import os
import uuid
import asyncio
import bcrypt
from datetime import datetime
from typing import Optional, List, Dict
from discord import app_commands, ui
from discord.ui import Modal, TextInput, View, Button, Select
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")
WORDS_FILE = os.getenv("WORDS_FILE", "words.txt")
MAX_ATTEMPTS = 6
MAX_HINTS = 3
DATA_FILE = "wordle_data.json"
CONFIG_FILE = "server_config.json"
SETTINGS_FILE = "user_settings.json"
DAILY_FILE = "daily_data.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

def get_scope_label(scope: str) -> str:
    return "Server" if scope == "server" else "Global"

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(stored_hash: str, password: str) -> bool:
    return bcrypt.checkpw(password.encode(), stored_hash.encode())

class UserSettings:
    def __init__(self):
        self.settings = self.load_settings()
    
    def load_settings(self):
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def save_settings(self):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(self.settings, f, indent=2)
    
    def get_settings(self, user_id: int) -> dict:
        default_settings = {
            "stats_public": True,
            "history_public": True,
            "anonymous": False,
            "anon_id": str(uuid.uuid4())[:8].upper(),
            "anon_password": None,
            "anon_games": []
        }
        user_id_str = str(user_id)
        
        if user_id_str not in self.settings:
            self.settings[user_id_str] = default_settings.copy()
            self.save_settings()
        else:
            for key in default_settings:
                if key not in self.settings[user_id_str]:
                    if key == "anon_id":
                        self.settings[user_id_str][key] = str(uuid.uuid4())[:8].upper()
                    else:
                        self.settings[user_id_str][key] = default_settings[key]
            self.save_settings()
        
        return self.settings[user_id_str].copy()
    
    def update_settings(self, user_id: int, **kwargs):
        user_id_str = str(user_id)
        self.get_settings(user_id)
        
        if 'anon_password' in kwargs and kwargs['anon_password']:
            kwargs['anon_password'] = hash_password(kwargs['anon_password'])
        
        valid_keys = ["stats_public", "history_public", "anonymous", 
                     "anon_id", "anon_password", "anon_games"]
        for key, value in kwargs.items():
            if key in valid_keys and key in self.settings[user_id_str]:
                self.settings[user_id_str][key] = value
        self.save_settings()

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
                return self.validate_data_structure(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return self.default_data_structure()
    
    def validate_data_structure(self, data):
        data.setdefault("guilds", {})
        data.setdefault("global", {"users": {}})
        data.setdefault("anonymous_games", {})
        data.setdefault("achievements", {})
        data.setdefault("daily_challenges", {}) 
        
        # Migration für alte Datensätze
        for scope in [data["global"], *data["guilds"].values()]:
            for user_games in scope.get("users", {}).values():
                for game in user_games:
                    game.setdefault("id", str(uuid.uuid4())[:8].upper())
                    game.setdefault("anonymous", False)
    
        for anon_games in data["anonymous_games"].values():
            for game in anon_games:
                game.setdefault("id", str(uuid.uuid4())[:8].upper())
                game.setdefault("anonymous", True)
    
        return data
    
    def default_data_structure(self):
        return {"guilds": {}, 
                "global": {"users": {}}, 
                "anonymous_games": {},
                "achievements": {},
                "daily_challenges": {}
                }
    
    def save_data(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.data, f, indent=2)
    
    def add_game(self, guild_id: int, user_id: int, game_data: dict):
        settings = UserSettings().get_settings(user_id)
    
        game_entry = {
        "id": str(uuid.uuid4())[:8].upper(),
        "timestamp": datetime.now().isoformat(),
        "won": game_data["won"],
        "word": game_data["word"],
        "attempts": len(game_data["guesses"]),
        "hints": game_data["hints"],
        "guesses": [{"word": g[0], "result": g[1]} for g in game_data["guesses"]],
        "duration": game_data["duration"],
        "anonymous": settings["anonymous"],
        "guild_id": guild_id  # 👈 Füge guild_id für alle Spiele hinzu
        }
    
        if settings["anonymous"]:
            anon_id = settings["anon_id"]
            self.data["anonymous_games"].setdefault(anon_id, []).insert(0, game_entry)
            settings["anon_games"].insert(0, game_entry["id"])
            UserSettings().update_settings(user_id, anon_games=settings["anon_games"])
        else:
            guild_str = str(guild_id)
            user_str = str(user_id)
            self.data["guilds"].setdefault(guild_str, {"users": {}})
            self.data["guilds"][guild_str]["users"].setdefault(user_str, []).insert(0, game_entry)
            self.data["global"]["users"].setdefault(user_str, []).insert(0, game_entry)
    
        self.save_data()
    
    def get_leaderboard(self, scope: str, guild_id: Optional[int] = None) -> List[dict]:
        source = self.data["global"] if scope == "global" else self.data["guilds"].get(str(guild_id), {"users": {}})
        leaderboard = []
        for user_id_str, games in source["users"].items():
            valid_games = [g for g in games if not g.get("anonymous", False)]
            total = len(valid_games)
            if total == 0:
                continue
            wins = sum(g["won"] for g in valid_games)
            avg_attempts = sum(g["attempts"] for g in valid_games) / total
            win_rate = wins / total
            last_games = sorted(valid_games[:10], key=lambda x: x["timestamp"], reverse=True)
            leaderboard.append({
                "user_id": int(user_id_str),
                "wins": wins,
                "total": total,
                "avg_attempts": avg_attempts,
                "win_rate": win_rate,
                "last_games": last_games
            })
        return sorted(leaderboard, key=lambda x: (-x["wins"], -x["total"]))
    
    def get_user_games(self, user_id: int, scope: str, guild_id: Optional[int] = None) -> List[dict]:
        source = self.data["global"] if scope == "global" else self.data["guilds"].get(str(guild_id), {"users": {}})
        return source["users"].get(str(user_id), [])
    
    def get_anonymous_games(self, anon_id: str) -> List[dict]:
        return self.data["anonymous_games"].get(anon_id, [])

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
        secret = list(self.secret_word)
        result = [""]*5
        
        for i in range(5):
            if guess[i] == secret[i]:
                result[i] = "🟩"
                secret[i] = None
                self.correct_positions[i] = True
        
        for i in range(5):
            if result[i] == "🟩":
                continue
            if guess[i] in secret:
                result[i] = "🟨"
                secret[secret.index(guess[i])] = None
            else:
                result[i] = "⬛"
        
        self.attempts.append((guess.lower(), result.copy()))
        self.remaining -= 1
        return result
    
    def add_hint(self):
        if self.hints_used >= MAX_HINTS:
            return False
        available = [i for i, c in enumerate(self.correct_positions) if not c]
        if not available:
            return False
        pos = random.choice(available)
        self.hinted_letters.add(self.secret_word[pos])
        self.hints_used += 1
        return True
    
    @property
    def hint_display(self):
        return " ".join(c.upper() if c in self.hinted_letters or self.correct_positions[i] else "▢" 
                      for i, c in enumerate(self.secret_word))

class EnhancedLeaderboardView(View):
    def __init__(self, cog, interaction: discord.Interaction, guild_id: Optional[int], scope: str = "server"):
        super().__init__(timeout=60)
        self.cog = cog
        self.interaction = interaction  # Speichere die Interaktion
        self.guild_id = guild_id
        self.scope = scope
        self.leaderboard_data = []
        self.current_page = 0
        self.page_size = 10
        self.view_id = str(uuid.uuid4())[:8]
        self.initialize_data()
        self.create_components()

    def initialize_data(self):
        raw_data = self.cog.history.get_leaderboard(self.scope, self.guild_id)
        self.leaderboard_data = sorted(
            [entry for entry in raw_data if entry['total'] > 0],
            key=lambda x: (-x['wins'], -x['total'], x['avg_attempts']),
            reverse=False
        )[:100]

    def create_components(self):
        self.clear_items()
        
        # Scope Buttons
        server_btn = Button(
            emoji="🏠",
            label="Server",
            style=discord.ButtonStyle.primary if self.scope == "server" else discord.ButtonStyle.secondary,
            custom_id=f"scope_server_{self.view_id}"
        )
        server_btn.callback = self.switch_scope_server
        self.add_item(server_btn)

        global_btn = Button(
            emoji="🌍",
            label="Global",
            style=discord.ButtonStyle.primary if self.scope == "global" else discord.ButtonStyle.secondary,
            custom_id=f"scope_global_{self.view_id}"
        )
        global_btn.callback = self.switch_scope_global
        self.add_item(global_btn)

        # Pagination
        if len(self.leaderboard_data) > self.page_size:
            prev_btn = Button(emoji="⬅️", custom_id=f"prev_page_{self.view_id}")
            prev_btn.callback = self.prev_page
            self.add_item(prev_btn)

            next_btn = Button(emoji="➡️", custom_id=f"next_page_{self.view_id}")
            next_btn.callback = self.next_page
            self.add_item(next_btn)

        # Zusätzliche Buttons
        stats_btn = Button(
            emoji="📊",
            label="Server Stats",
            style=discord.ButtonStyle.secondary,
            custom_id=f"show_stats_{self.view_id}"
        )
        stats_btn.callback = self.show_server_stats
        self.add_item(stats_btn)
        
        recent_btn = Button(
            emoji="🕒",
            label="Letzte Spiele",
            style=discord.ButtonStyle.secondary,
            custom_id=f"show_recent_{self.view_id}"
        )
        recent_btn.callback = self.show_recent_games
        self.add_item(recent_btn)

    def create_leaderboard_embed(self):
        embed = discord.Embed(
            title=f"🏆 {get_scope_label(self.scope)} Rangliste",
            color=discord.Color.gold()
        )
        
        start_idx = self.current_page * self.page_size
        end_idx = start_idx + self.page_size
        page_data = self.leaderboard_data[start_idx:end_idx]

        # Korrektur: Verwende self.interaction statt self.cog.ctx
        user_position = next(
            (i+1 for i, entry in enumerate(self.leaderboard_data) 
            if entry["user_id"] == self.interaction.user.id  # 👈 Korrigierte Zeile
        ), None)

        if user_position:
            embed.description = f"Deine Position: #{user_position}"

        for idx, entry in enumerate(page_data, start=1):
            user = self.cog.bot.get_user(entry['user_id'])
            if not user:
                continue
                
            stats = [
                f"🏆 Siege: {entry['wins']}",
                f"📊 Winrate: {entry['win_rate']*100:.1f}%",
                f"🔢 Ø Versuche: {entry['avg_attempts']:.1f}"
            ]
            
            embed.add_field(
                name=f"{start_idx + idx}. {user.display_name}",
                value="\n".join(stats),
                inline=False
            )

        embed.set_footer(text=f"Seite {self.current_page + 1}/{(len(self.leaderboard_data)-1)//self.page_size + 1}")
        return embed

    async def update_view(self, interaction: discord.Interaction):
        self.create_components()
        try:
            await interaction.response.edit_message(
                embed=self.create_leaderboard_embed(),
                view=self
            )
        except discord.NotFound:
            await interaction.followup.send("❌ Leaderboard konnte nicht aktualisiert werden!", ephemeral=True)

    async def switch_scope_server(self, interaction: discord.Interaction):
        self.scope = "server"
        self.guild_id = interaction.guild.id
        self.current_page = 0
        self.initialize_data()
        await self.update_view(interaction)

    async def switch_scope_global(self, interaction: discord.Interaction):
        self.scope = "global"
        self.guild_id = None
        self.current_page = 0
        self.initialize_data()
        await self.update_view(interaction)

    async def prev_page(self, interaction: discord.Interaction):
        self.current_page = max(0, self.current_page - 1)
        await self.update_view(interaction)

    async def next_page(self, interaction: discord.Interaction):
        max_page = (len(self.leaderboard_data) - 1) // self.page_size
        self.current_page = min(max_page, self.current_page + 1)
        await self.update_view(interaction)

    async def show_own_stats(self, interaction: discord.Interaction):
        await self.cog.show_own_stats(interaction)

    async def show_own_games(self, interaction: discord.Interaction):
        view = RecentGamesView(
            cog=self.cog,
            user_id=interaction.user.id,
            public_games=[]
        )
        await interaction.response.send_message(
            embed=view.create_embed(),
            view=view,
            ephemeral=True
        )

    async def show_server_stats(self, interaction: discord.Interaction):
        """Zeigt Server-Statistiken für alle sichtbar"""
        try:
            leaderboard = self.cog.history.get_leaderboard("server", self.guild_id)
            total_games = sum(entry['total'] for entry in leaderboard)
            total_wins = sum(entry['wins'] for entry in leaderboard)
            
            embed = discord.Embed(
                title=f"📊 {get_scope_label(self.scope)} Statistiken",
                color=discord.Color.gold()
            )
            
            embed.add_field(
                name="Gesamtspiele",
                value=f"🕹️ {total_games}",
                inline=True
            )
            
            embed.add_field(
                name="Gewonnene Spiele",
                value=f"🏆 {total_wins}",
                inline=True
            )
            
            embed.add_field(
                name="Durchschnittliche Winrate",
                value=f"📊 {total_wins/total_games*100:.1f}%" if total_games > 0 else "📊 0%",
                inline=True
            )
            
            await interaction.response.send_message(embed=embed)

        except Exception as e:
            await interaction.response.send_message(
                "❌ Fehler beim Laden der Statistiken!",
                ephemeral=True
            )

    async def show_recent_games(self, interaction: discord.Interaction):
        """Zeigt die letzten Server-Spiele für alle sichtbar"""
        try:
            view = RecentGamesView(self.cog, interaction.guild.id)  # 👈 guild.id statt guild_id
            await interaction.response.send_message(
                embed=view.create_embed(),
                view=view,
                ephemeral=True
            )
        except Exception as e:
            print(f"Fehler: {str(e)}")
            await interaction.response.send_message(
                "❌ Fehler beim Laden der Spiele!",
                ephemeral=True
            )

class RecentGamesView(View):
    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.page = 0
        self.page_size = 5  # Reduzierte Anzahl für bessere Übersicht
        self.games = self.load_games()
        self.total_pages = max(1, (len(self.games) - 1) // self.page_size + 1)
        self.include_anonymous = False  # Neuer Filter

        prev_button = Button(emoji="⬅️", style=discord.ButtonStyle.primary)
        prev_button.callback = self.prev_page
        self.add_item(prev_button)

        next_button = Button(emoji="➡️", style=discord.ButtonStyle.primary)
        next_button.callback = self.next_page
        self.add_item(next_button)

    def load_games(self):
        all_games = []
        guild_data = self.cog.history.data["guilds"].get(str(self.guild_id), {})

        # Öffentliche Spiele
        for user_id_str, games in guild_data.get("users", {}).items():
            all_games.extend([
                ("public", int(user_id_str), game) 
                for game in games 
                if not game.get("anonymous", False)
            ])

        # Anonyme Spiele
        for anon_id in self.cog.history.data["anonymous_games"]:
            anon_games = self.cog.history.get_anonymous_games(anon_id)
            all_games.extend([
                ("anon", anon_id, game)
                for game in anon_games
                if game.get("guild_id") == self.guild_id
            ])

        return sorted(all_games, key=lambda x: x[2]["timestamp"], reverse=True)[:100]

    def create_embed(self):
        embed = discord.Embed(title="🕒 Letzte Server-Spiele", color=discord.Color.blue())
        
        start_idx = self.page * self.page_size
        for idx, (game_type, identifier, game) in enumerate(self.paginated_games(), start=1):
            global_number = start_idx + idx
            if game_type == "public":
                user = self.cog.bot.get_user(identifier)
                name = f"{user.display_name if user else 'Unbekannt'} (ID: {game['id']})"
            else:
                name = f"🎭 Anonym (ID: {game['id']})"

            embed.add_field(
                name=f"Spiel {global_number} - {name}",
                value=(
                    f"🏆 {'Gewonnen' if game['won'] else 'Verloren'}\n"
                    f"🔑 Wort: ||{game['word'].upper()}||\n"
                    f"📅 {datetime.fromisoformat(game['timestamp']).strftime('%d.%m.%Y %H:%M')}\n"
                    f"🔢 Versuche: {game['attempts']}/{MAX_ATTEMPTS}"
                ),
                inline=False
            )

        embed.set_footer(text=f"Seite {self.page + 1}/{self.total_pages}")
        return embed

    def paginated_games(self):
        start = self.page * self.page_size
        end = start + self.page_size
        return self.games[start:end]

    async def prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        await self.update_view(interaction)

    async def next_page(self, interaction: discord.Interaction):
        self.page = min(self.total_pages - 1, self.page + 1)
        await self.update_view(interaction)

    async def update_view(self, interaction: discord.Interaction):
        # Update Button-Status
        for child in self.children:
            if isinstance(child, Button):
                if child.emoji.name == "⬅️":
                    child.disabled = (self.page == 0)
                elif child.emoji.name == "➡️":
                    child.disabled = (self.page >= self.total_pages - 1)
        
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    def update_button_states(self):
        for child in self.children:
            if isinstance(child, Button):
                if child.emoji.name == "⬅️":
                    child.disabled = self.page == 0
                elif child.emoji.name == "➡️":
                    child.disabled = self.page >= self.total_pages - 1

class HistoryView(View):
    def __init__(self, cog, user_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.current_scope = "server"
        self.current_mode = "public"
        self.page = 0
        self.view_id = str(uuid.uuid4())[:8]

        # UI-Komponenten
        self.scope_select = Select(placeholder="🌐 Bereich wählen", options=[], custom_id=f"scope_{self.view_id}")
        self.mode_select = Select(placeholder="🎭 Modus wählen", options=[], custom_id=f"mode_{self.view_id}")
        self.update_selects()
        
        self.nav_buttons = [
            Button(emoji="⏮️", style=discord.ButtonStyle.grey, custom_id=f"first_{self.view_id}"),
            Button(emoji="⬅️", style=discord.ButtonStyle.primary, custom_id=f"prev_{self.view_id}"),
            Button(emoji="🔢", style=discord.ButtonStyle.secondary, custom_id=f"page_{self.view_id}"),
            Button(emoji="➡️", style=discord.ButtonStyle.primary, custom_id=f"next_{self.view_id}"),
            Button(emoji="⏭️", style=discord.ButtonStyle.grey, custom_id=f"last_{self.view_id}")
        ]
        
        # Komponenten hinzufügen
        self.scope_select.callback = self.update_scope
        self.mode_select.callback = self.update_mode
        self.add_item(self.scope_select)
        self.add_item(self.mode_select)
        
        for btn in self.nav_buttons:
            btn.callback = self.handle_navigation
            self.add_item(btn)

        self.update_button_states()

    def update_selects(self):
        self.scope_select.options = [
            discord.SelectOption(label="🏠 Server", value="server", default=self.current_scope == "server"),
            discord.SelectOption(label="🌍 Global", value="global", default=self.current_scope == "global")
        ]
        self.mode_select.options = [
            discord.SelectOption(label="🌍 Öffentlich", value="public", default=self.current_mode == "public"),
            discord.SelectOption(label="🎭 Anonym", value="anonymous", default=self.current_mode == "anonymous")
        ]

    def update_button_states(self):
        games = self.get_games()
        total = len(games)
        for btn in self.nav_buttons:
            if btn.emoji.name == "⏮️":
                btn.disabled = self.page <= 0 or total == 0
            elif btn.emoji.name == "⬅️":
                btn.disabled = self.page <= 0 or total == 0
            elif btn.emoji.name == "➡️":
                btn.disabled = self.page >= total - 1 or total == 0
            elif btn.emoji.name == "⏭️":
                btn.disabled = self.page >= total - 1 or total == 0

    async def handle_navigation(self, interaction: discord.Interaction):
        action = interaction.data["custom_id"].split("_")[0]
        games = self.get_games()
        total = len(games)

        if action == "page":
            modal = PageSelectModal(total)
            await interaction.response.send_modal(modal)
            await modal.wait()
            if modal.page_number:
                self.page = modal.page_number - 1
        else:
            if action == "first": self.page = 0
            elif action == "prev": self.page = max(0, self.page - 1)
            elif action == "next": self.page = min(total - 1, self.page + 1)
            elif action == "last": self.page = total - 1

        await self.safe_update(interaction)

    async def update_scope(self, interaction: discord.Interaction):
        self.current_scope = interaction.data["values"][0]
        self.page = 0
        await self.safe_update(interaction)

    async def update_mode(self, interaction: discord.Interaction):
        new_mode = interaction.data["values"][0]
        if new_mode == "anonymous":
            if not await self.verify_anonymity(interaction):
                return
        self.current_mode = new_mode
        self.page = 0
        await self.safe_update(interaction)

    async def verify_anonymity(self, interaction: discord.Interaction) -> bool:
        settings = self.cog.settings.get_settings(self.user_id)
        if not settings["anon_password"]:
            await interaction.response.send_message("❌ Kein Anonym-Passwort gesetzt!", ephemeral=True)
            return False
        modal = AnonCheckModal(self.cog, self.user_id)
        await interaction.response.send_modal(modal)
        await modal.wait()
        return modal.verified

    def get_games(self):
        if self.current_mode == "anonymous":
            settings = self.cog.settings.get_settings(self.user_id)
            return self.cog.history.get_anonymous_games(settings["anon_id"])
        return self.cog.history.get_user_games(
            self.user_id,
            self.current_scope,
            self.guild_id if self.current_scope == "server" else None
        )

    def create_embed(self):
        games = self.get_games()
        game = games[self.page] if games else None
        
        embed = discord.Embed(
            title=f"📜 {'Globale' if self.current_scope == 'global' else 'Server'} Historie",
            description=f"Modus: {'🎭 Anonym' if self.current_mode == 'anonymous' else '🌍 Öffentlich'}",
            color=discord.Color.blue()
        ).set_footer(text=f"Seite {self.page + 1}/{len(games)}")

        if game:
            # Server-Info für globale Spiele
            server_info = ""
            if self.current_scope == "global" and "guild_id" in game:
                guild = self.cog.bot.get_guild(game["guild_id"])
                server_info = f"\n🏰 Server: {guild.name if guild else 'Unbekannt'}"
            
            # Spielverlauf
            attempts = "\n".join(
                f"`{g['word'].upper()}`: {' '.join(g['result'])}" 
                for g in game["guesses"]
            )
            
            embed.add_field(
                name="🔍 Spiel-Details",
                value=(
                    f"🔑 ID: `{game['id']}`{server_info}\n"
                    f"📅 {datetime.fromisoformat(game['timestamp']).strftime('%d.%m.%Y %H:%M')}\n"
                    f"🏆 {'Gewonnen' if game['won'] else 'Verloren'} in {game['attempts']} Versuchen\n"
                    f"💡 {game['hints']} Tipps | ⏱️ {game['duration']:.1f}s"
                ),
                inline=False
            )
            
            if attempts:
                embed.add_field(name="📈 Versuchsverlauf", value=attempts, inline=False)
            
            embed.set_thumbnail(url="https://emojicdn.elk.sh/🎭" if game.get("anonymous") else "https://emojicdn.elk.sh/🌍")

        return embed

    async def safe_update(self, interaction: discord.Interaction):
        self.update_selects()
        self.update_button_states()
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=self.create_embed(), view=self)
            else:
                await interaction.response.edit_message(embed=self.create_embed(), view=self)
        except Exception as e:
            print(f"Update Error: {e}")

    async def update_display(self, interaction: discord.Interaction):
        """Veraltet, wird durch safe_update ersetzt"""
        await self.safe_update(interaction)

class HistorySelectionView(View):
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.cog = cog
        
        self.add_item(Button(
            label="Meine Historie", 
            style=discord.ButtonStyle.primary, 
            custom_id="own_history"
        ))
        self.add_item(Button(
            label="Anderer Spieler", 
            style=discord.ButtonStyle.secondary, 
            custom_id="other_history"
        ))

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.data["custom_id"] == "own_history":
            await self.cog.show_user_history(interaction, interaction.user)
        else:
            await interaction.response.send_modal(HistorySearchModal(self.cog))
        return False

class HistorySearchModal(Modal, title="🔍 Spieler suchen"):
    username = TextInput(label="Benutzername oder ID", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user = await commands.UserConverter().convert(interaction, self.username.value)
            await self.cog.show_user_history(interaction, user)
        except commands.UserNotFound:
            await interaction.response.send_message("❌ Spieler nicht gefunden!", ephemeral=True)

# Zuerst die Modal-Klassen definieren
class AnonCheckModal(Modal, title="🔒 Anonyme Spiele - Passwortprüfung"):
    password = TextInput(label="Passwort", placeholder="Dein Anonym-Passwort...", style=discord.TextStyle.short)
    
    def __init__(self, cog, user_id: int):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.verified = False

    async def on_submit(self, interaction: discord.Interaction):
        settings = self.cog.settings.get_settings(self.user_id)
        self.verified = verify_password(settings["anon_password"], self.password.value)
        await interaction.response.defer()

class PageSelectModal(Modal, title="🔢 Direkt zur Seite springen"):
    page_input = TextInput(label="Seitennummer", placeholder="Gib eine Zahl zwischen 1 und ... ein", required=True)
    
    def __init__(self, max_pages: int):
        super().__init__()
        self.page_number = None
        self.max_pages = max_pages
        self.page_input.placeholder = f"1 - {max_pages}"

    async def on_submit(self, interaction: discord.Interaction):
        try:
            page = int(self.page_input.value)
            if 1 <= page <= self.max_pages:
                self.page_number = page
                await interaction.response.defer()
            else:
                await interaction.response.send_message(
                    f"❌ Ungültige Seite! Bitte zwischen 1 und {self.max_pages} wählen.",
                    ephemeral=True
                )
        except ValueError:
            await interaction.response.send_message("❌ Bitte eine gültige Zahl eingeben!", ephemeral=True)

class InitialHistoryView(View):
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.cog = cog
        
        self.add_item(Button(
            label="Meine Historie", 
            style=discord.ButtonStyle.primary, 
            custom_id="own_history"
        ))
        self.add_item(Button(
            label="Anderer Spieler", 
            style=discord.ButtonStyle.secondary, 
            custom_id="other_history"
        ))

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.data["custom_id"] == "own_history":
            await self.cog.show_own_history(interaction)
        else:
            await interaction.response.send_modal(SearchHistoryModal(self.cog))
        return False
    
class SearchHistoryModal(Modal, title="🎮 Spieler suchen"):
    username = TextInput(label="Benutzername oder ID", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user = await commands.UserConverter().convert(interaction, self.username.value)
            settings = self.cog.settings.get_settings(user.id)
            
            if not settings["history_public"]:
                await interaction.response.send_message("❌ Dieser Spieler hat seine Historie privat!", ephemeral=True)
                return
                
            await self.cog.show_history(interaction, user)
            
        except commands.UserNotFound:
            await interaction.response.send_message("❌ Spieler nicht gefunden!", ephemeral=True)

class StatsView(View):
    def __init__(self, cog, user: discord.User):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.page = 0
        self.page_size = 5
        self.games = self.load_games()
        self.total_pages = max(1, (len(self.games) - 1) // self.page_size + 1)

    def load_games(self):
        public_games = self.cog.history.get_user_games(self.user.id, "global")
        anon_games = self.cog.history.get_anonymous_games(
            self.cog.settings.get_settings(self.user.id)["anon_id"]
        )
        return public_games + anon_games

    def create_embed(self):
        embed = discord.Embed(title=f"📊 Statistiken für {self.user.display_name}", color=discord.Color.gold())
        
        for game in self.paginated_games():
            status = "🟢" if game["won"] else "🔴"
            embed.add_field(
                name=f"{status} Spiel {game['id']}",
                value=(
                    f"🔑 Wort: ||{game['word'].upper()}||\n"
                    f"📅 {datetime.fromisoformat(game['timestamp']).strftime('%d.%m.%Y %H:%M')}\n"
                    f"💡 Tipps: {game['hints']} | ⏱️ {game['duration']:.1f}s"
                ),
                inline=False
            )

        embed.set_footer(text=f"Seite {self.page + 1}/{self.total_pages}")
        return embed

    @ui.button(emoji="⬅️", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: Button):
        self.page = max(0, self.page - 1)
        await self.update(interaction)

    @ui.button(emoji="➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        await self.update(interaction)

    @ui.button(emoji="🔍", style=discord.ButtonStyle.secondary, label="Nach ID suchen")
    async def search_id(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(SearchIDModal(self.cog, self.user))

    async def update(self, interaction: discord.Interaction):
        for child in self.children:
            if isinstance(child, Button):
                if child.emoji.name == "⬅️":
                    child.disabled = self.page == 0
                elif child.emoji.name == "➡️":
                    child.disabled = self.page >= self.total_pages - 1
        
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

class SearchIDModal(Modal, title="🔍 Spiel nach ID suchen"):
    game_id = TextInput(label="Spiel-ID", placeholder="Gib die 8-stellige ID ein", min_length=8, max_length=8)

    def __init__(self, cog, user: discord.User):
        super().__init__()
        self.cog = cog
        self.user = user

    async def on_submit(self, interaction: discord.Interaction):
        game = self.find_game(self.game_id.value.upper())
        if game:
            embed = discord.Embed(title=f"🔍 Spiel {self.game_id.value}", color=discord.Color.blue())
            embed.add_field(name="Wort", value=f"||{game['word'].upper()}||")
            embed.add_field(name="Ergebnis", value="Gewonnen 🏆" if game['won'] else "Verloren 💥")
            embed.add_field(name="Datum", value=datetime.fromisoformat(game['timestamp']).strftime('%d.%m.%Y %H:%M'))
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Kein Spiel mit dieser ID gefunden!", ephemeral=True)

    def find_game(self, game_id: str):
        games = self.cog.history.data["global"]["users"].get(str(self.user.id), [])
        anon_games = self.cog.history.get_anonymous_games(
            self.cog.settings.get_settings(self.user.id)["anon_id"]
        )
        return next((g for g in games + anon_games if g["id"] == game_id), None)

class WordleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: Dict[int, WordleGame] = {}
        self.history = GameHistory()
        self.config = ServerConfig()
        self.settings = UserSettings()
        self.persistent_views_added = False
        self.achievement_system = AchievementSystem(self)
        self.daily_challenge = DailyChallenge()

    async def on_interaction(self, interaction: discord.Interaction):
        """Globaler Interaktions-Handler"""
        try:
            if interaction.type == discord.InteractionType.component:
            # Füge fehlende Handler hier hinzu
                pass
        except Exception as e:
            print(f"Interaktionsfehler: {str(e)}")
    
    async def add_persistent_views(self):
        if not self.persistent_views_added:
            self.bot.add_view(MainMenu(self))
            self.persistent_views_added = True
    
    async def start_new_game(self, interaction: discord.Interaction):
        if interaction.user.id in self.games:
            await interaction.response.send_message("❌ Du hast bereits ein aktives Spiel!", ephemeral=True)
            return
        
        self.games[interaction.user.id] = WordleGame(interaction.user.id)
        view = GameView(self, interaction.user.id)
        await interaction.response.send_message(embed=self.create_game_embed(interaction.user.id), view=view)

    async def show_user_history(self, interaction: discord.Interaction, user: discord.User):
        is_own = interaction.user.id == user.id
        settings = self.settings.get_settings(user.id)
        
        if not is_own and not settings["history_public"]:
            embed = discord.Embed(
                title="🔒 Private Historie",
                description=f"{user.display_name} hat seine Historie privat eingestellt!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        view = HistoryView(self, user.id, interaction.guild.id)
        embed = view.create_embed()
        
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_own_history(self, interaction: discord.Interaction):
        """Zeigt die eigene Historie des Benutzers an"""
        await self.show_user_history(interaction, interaction.user)

    @app_commands.command(name="historie", description="Zeige Spielverläufe an")
    async def history_command(self, interaction: discord.Interaction):
        """Hauptbefehl für die Historie-Anzeige"""
        embed = discord.Embed(
            title="📜 Historie auswählen",
            description="Wessen Spielhistorie möchtest du einsehen?",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(
            embed=embed,
            view=HistorySelectionView(self),
            ephemeral=True
        )    

    def create_game_embed(self, user_id: int, is_daily: bool = False):  # 👈 Parameter hinzufügen
        settings = self.settings.get_settings(user_id)
        embed = discord.Embed(
            title="🎮 Neues Wordle-Spiel" if not is_daily else "🌞 Daily Challenge",
            description=f"🔤 Errate das 5-Buchstaben-Wort in 6 Versuchen!\n"
                    f"Anonymmodus: {'✅ Aktiv' if settings['anonymous'] else '❌ Inaktiv'}"
                    + ("\n\n🔥 **Tägliche Herausforderung:**\n- Nur 1 Versuch pro Tag!\n- Globales Leaderboard" if is_daily else ""),
            color=discord.Color.green()
        )
        embed.add_field(
        name="Farben", 
        value="🟩 Richtiger Buchstabe\n🟨 Falsche Position\n⬛ Nicht im Wort", 
        inline=False
        )
        return embed

    @app_commands.command(name="wordle", description="Starte ein neues Wordle-Spiel")
    async def wordle(self, interaction: discord.Interaction):
        await self.start_new_game(interaction)
    
    @app_commands.command(name="wordle_setup", description="Richte den Wordle-Channel ein")
    @app_commands.default_permissions(administrator=True)
    async def wordle_setup(self, interaction: discord.Interaction):
        self.config.set_wordle_channel(interaction.guild_id, interaction.channel_id)
        try:
            await interaction.channel.purge(limit=1)
        except: pass
        await interaction.channel.send(
            embed=discord.Embed(
                title="🎮 Wordle-Hauptmenü",
                description=(
                    "**Willkommen im Wordle-Hauptmenü!**\n\n"
                    "▸ 🎮 Starte ein neues Spiel\n"
                    "▸ 🌞 Tägliche Challenge\n"
                    "▸ 🏆 Zeige die Bestenlisten an\n"
                    "▸ 📊 Überprüfe deine Statistiken\n"
                    "▸ 📜 Durchsuche deine Spielhistorie\n"
                    "▸ ⚙️ Passe deine Einstellungen an\n"
                    "▸ ❓ Erhalte Spielhilfe"
                ),
                color=discord.Color.blue()
            ),
            view=MainMenu(self)
        )
        await interaction.response.send_message("✅ Channel eingerichtet!", ephemeral=True)
    
    async def handle_process_guess(self, interaction: discord.Interaction, guess: str, is_daily=False):
        game = self.games.get(interaction.user.id)
        if not game:
            await interaction.response.send_message("❌ Starte erst ein Spiel!", ephemeral=True)
            return
        
        if len(guess) != 5 or not guess.isalpha():
            await interaction.response.send_message("❌ Ungültige Eingabe!", ephemeral=True)
            return
        
        result = game.check_guess(guess.lower())
        embed = discord.Embed(
            title=f"Versuche übrig: {game.remaining}",
            color=discord.Color.blurple()
        )

        if is_daily:
            daily_word = self.daily_challenge.get_daily_word()
            if guess.lower() != daily_word:
                await interaction.response.send_message("❌ Falsches Wort für die Daily Challenge!", ephemeral=True)
            return
        
        
        for i, (attempt, res) in enumerate(game.attempts):
            embed.add_field(name=f"Versuch {i+1}", value=f"{attempt.upper()}\n{' '.join(res)}", inline=False)
        
        embed.add_field(name="Hinweis", value=f"`{game.hint_display}`", inline=False)
        
        if guess.lower() == game.secret_word or game.remaining == 0:
            await self.handle_end_game(interaction, guess.lower() == game.secret_word, is_daily=is_daily)
        else:
            view = GameView(self, interaction.user.id)
            await interaction.response.edit_message(embed=embed, view=view)
    
    async def handle_give_hint(self, interaction: discord.Interaction):
        game = self.games.get(interaction.user.id)
        if not game or not game.add_hint():
            await interaction.response.send_message("❌ Keine Tipps mehr verfügbar!", ephemeral=True)
            return
        
        embed = interaction.message.embeds[0]
        embed.set_field_at(-1, name="Hinweis", value=f"`{game.hint_display}`", inline=False)
        view = GameView(self, interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @app_commands.command(name="daily", description="Tägliche Herausforderung")
    async def daily_command(self, interaction: discord.Interaction):
        """Handle Daily Challenge mit speziellem Embed"""
        if interaction.user.id in self.games:
            await interaction.response.send_message("❌ Du hast bereits ein aktives Spiel!", ephemeral=True)
            return
        
        # Daily-spezifisches Spiel erstellen
        self.games[interaction.user.id] = WordleGame(interaction.user.id)
        self.games[interaction.user.id].secret_word = self.daily_challenge.get_daily_word()
        
        view = GameView(self, interaction.user.id)
        await interaction.response.send_message(
            embed=self.create_game_embed(interaction.user.id, is_daily=True),
            view=view
        )

    async def handle_end_game(self, interaction: discord.Interaction, won: bool, is_daily: bool = False):
        game = self.games.pop(interaction.user.id, None)
        if not game:
            return
        
        new_achievements = self.achievement_system.check_achievements(interaction.user.id, game)
        
        self.history.add_game(interaction.guild_id, interaction.user.id, {
            "won": won,
            "word": game.secret_word,
            "guesses": game.attempts,
            "hints": game.hints_used,
            "duration": game.get_duration()
        })
        
        settings = self.settings.get_settings(interaction.user.id)
        embed = discord.Embed(
            title="🎉 Gewonnen!" if won else "💥 Verloren!",
            description=f"Das Wort war: ||{game.secret_word.upper()}||",
            color=discord.Color.green() if won else discord.Color.red()
        )
        
        if settings["anonymous"]:
            anon_id = settings.get("anon_id", "UNKNOWN")
            embed.set_footer(text=f"Anonyme ID: {anon_id}")
            embed.add_field(name="Spielmodus", value="🎭 Anonymes Spiel", inline=False)
        else:
            embed.add_field(name="Spielmodus", value="🔓 Öffentliches Spiel", inline=False)
        
        view = EndGameView(self, interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)
        await asyncio.sleep(10)
        try:
            await interaction.delete_original_response()
        except:
            pass

        if is_daily:
            self.daily_challenge.add_participant(interaction.user.id, len(game.attempts))
            embed.add_field(name="Daily Challenge", 
                      value=f"🏆 Du bist Platz {self.get_daily_rank(interaction.user.id)}!",
                      inline=False)

        if game.secret_word == self.daily_challenge.get_daily_word():
            self.daily_challenge.add_participant(interaction.user.id, len(game.attempts))

        if new_achievements:
            achievements_text = "\n".join(f"🎉 {a['name']}: {a['description']}" for a in new_achievements)
            embed.add_field(name="Neue Achievements freigeschaltet!", value=achievements_text, inline=False)
    
    def get_daily_rank(self, user_id: int):
        leaderboard = self.daily_challenge.get_leaderboard()
        user_str = str(user_id)
        return next((i+1 for i, (u_id, _) in enumerate(leaderboard) if u_id == user_str), None)

    async def show_stats(self, interaction: discord.Interaction, user: discord.User):
        is_own_stats = interaction.user.id == user.id
        settings = self.settings.get_settings(user.id)
    
        if not is_own_stats and not settings["stats_public"]:
            await interaction.response.send_message("❌ Diese Statistiken sind privat!", ephemeral=True)
            return
    
        public_games = [g for g in self.history.get_user_games(user.id, "global") if not g.get("anonymous", False)]
        anon_games = self.history.get_anonymous_games(settings["anon_id"])
    
        embed = discord.Embed(
            title=f"📊 Statistiken",
            color=discord.Color.gold()
        )
    
        if public_games or anon_games:
            # Kopf entfernt - nur noch "Anonyme Spiele" als Unterscheidung
            if public_games:
                embed.add_field(name="Öffentliche Spiele", 
                            value=f"✅ {sum(g['won'] for g in public_games)} Siege\n"
                                    f"❌ {len(public_games)-sum(g['won'] for g in public_games)} Niederlagen\n"
                                    f"📊 {sum(g['won'] for g in public_games)/len(public_games)*100:.1f}% Winrate",
                            inline=True)
        
            if anon_games:
                embed.add_field(name="🎭 Anonyme Spiele", 
                            value=f"✅ {sum(g['won'] for g in anon_games)} Siege\n"
                                    f"❌ {len(anon_games)-sum(g['won'] for g in anon_games)} Niederlagen\n"
                                    f"📊 {sum(g['won'] for g in anon_games)/len(anon_games)*100:.1f}% Winrate",
                            inline=True)
        else:
            embed.description = "📭 Noch keine Spiele gespielt!"
    
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def show_anon_history(self, interaction: discord.Interaction, user: discord.User):
        settings = self.settings.get_settings(user.id)
        if not settings["anon_password"]:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Es wurde kein Anonym-Passwort gesetzt!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Es wurde kein Anonym-Passwort gesetzt!", ephemeral=True)
            return
        
        view = HistoryView(self, user.id, interaction.guild.id)
        await interaction.response.send_modal(AnonPasswordModal(self, user.id, view))
    
    async def show_history(self, interaction: discord.Interaction, user: discord.User):
        is_own_history = interaction.user.id == user.id
        settings = self.settings.get_settings(user.id)
        
        if not is_own_history and not settings["history_public"]:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Diese Historie ist privat!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Diese Historie ist privat!", ephemeral=True)
            return
        
        view = HistoryView(self, user.id, interaction.guild.id)
        if interaction.response.is_done():
            await interaction.followup.send(embed=view.create_embed(), view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)
    
    async def show_leaderboard(self, interaction: discord.Interaction):
        """Zeigt die Bestenliste nur dem aufrufenden Spieler"""
        try:
            await interaction.response.defer(ephemeral=True)
        # Korrekte Parameter: cog, interaction, guild_id, scope
            view = EnhancedLeaderboardView(self, interaction, interaction.guild_id, "server")
            await interaction.followup.send(
            embed=view.create_leaderboard_embed(),
            view=view,
            ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send("❌ Fehler beim Laden der Bestenliste!", ephemeral=True)
            print(f"Leaderboard Error: {str(e)}")
            
    async def show_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="❓ Wordle-Hilfe",
            description=(
                "🌟 **Spielregeln:**\n"
                "1. Errate das 5-Buchstaben-Wort in 6 Versuchen\n"
                "2. Farben zeigen Treffergenauigkeit:\n"
                "   🟩 = Richtiger Buchstabe an richtiger Position\n"
                "   🟨 = Buchstabe im Wort, aber falsche Position\n"
                "   ⬛ = Buchstabe nicht im Wort\n\n"
                "💡 **Tipps:**\n"
                "- Nutze maximal 3 Tipps pro Spiel\n"
                "- Vergleiche dich mit anderen über die Ranglisten"
            ),
            color=discord.Color.blue()
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="search", description="Suche nach Benutzerstatistiken")
    async def search_stats(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SearchModal(self))
    
    @app_commands.command(name="settings", description="Privatsphäre-Einstellungen")
    async def user_settings_command(self, interaction: discord.Interaction):
        await self.open_settings(interaction)
    
    async def open_settings(self, interaction: discord.Interaction):
        view = SettingsView(self, interaction.user.id)
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=discord.Embed(
                    title="⚙️ Einstellungen",
                    description="Wähle deine Privatsphäre-Einstellungen:",
                    color=discord.Color.blue()
                ),
                view=view,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⚙️ Einstellungen",
                    description="Wähle deine Privatsphäre-Einstellungen:",
                    color=discord.Color.blue()
                ),
                view=view,
                ephemeral=True
            )
    async def show_own_stats(self, interaction: discord.Interaction):
        """Zeigt die eigenen Statistiken an"""
        await self.show_stats(interaction, interaction.user)  

    async def show_stats(self, interaction: discord.Interaction, user: discord.User):
        """Hauptmethode für Statistiken"""
        try:
            is_own = interaction.user.id == user.id
            settings = self.settings.get_settings(user.id)
            
            if not is_own and not settings["stats_public"]:
                await interaction.response.send_message("❌ Diese Statistiken sind privat!", ephemeral=True)
                return
            
            public_games = [g for g in self.history.get_user_games(user.id, "global") if not g.get("anonymous", False)]
            anon_games = self.history.get_anonymous_games(settings["anon_id"])
            
            embed = discord.Embed(title="📊 Statistiken", color=discord.Color.gold())
            
            if public_games or anon_games:
                if public_games:
                    embed.add_field(
                        name="Öffentliche Spiele",
                        value=f"✅ {sum(g['won'] for g in public_games)} Siege\n"
                              f"❌ {len(public_games)-sum(g['won'] for g in public_games)} Niederlagen\n"
                              f"📊 {sum(g['won'] for g in public_games)/len(public_games)*100:.1f}% Winrate",
                        inline=True
                    )
                
                if anon_games:
                    embed.add_field(
                        name="🎭 Anonyme Spiele",
                        value=f"✅ {sum(g['won'] for g in anon_games)} Siege\n"
                              f"❌ {len(anon_games)-sum(g['won'] for g in anon_games)} Niederlagen\n"
                              f"📊 {sum(g['won'] for g in anon_games)/len(anon_games)*100:.1f}% Winrate",
                        inline=True
                    )
            else:
                embed.description = "📭 Noch keine Spiele gespielt!"
            
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message("❌ Fehler beim Laden der Statistiken!", ephemeral=True)
            print(f"Stats Error: {str(e)}")

        def get_daily_rank(self, user_id: int):
            leaderboard = self.daily_challenge.get_leaderboard()
            user_str = str(user_id)
            return next((i+1 for i, (u_id, _) in enumerate(leaderboard) if u_id == user_str), None)

    @app_commands.command(name="achievements", description="Zeige deine Achievements")
    async def _show_achievements(self, interaction: discord.Interaction):
        user_achievements = self.history.data["achievements"].get(str(interaction.user.id), {})
    
        embed = discord.Embed(
        title=f"🏆 Achievements - {interaction.user.display_name}",
        color=discord.Color.gold()
        )
    
        for achievement_id, data in self.achievement_system.ACHIEVEMENTS.items():
            status = "✅ " + datetime.fromisoformat(user_achievements[achievement_id]).strftime("%d.%m.%Y") if achievement_id in user_achievements else "❌"
            embed.add_field(
            name=f"{data['name']} {status}",
            value=data['description'],
            inline=False
            )
    
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="achievements", description="Zeige deine Achievements")
    async def show_achievements_command(self, interaction: discord.Interaction):
        await self._show_achievements(interaction)

    @app_commands.command(name="dailylb", description="Daily Challenge Bestenliste")
    async def daily_leaderboard(self, interaction: discord.Interaction):
        leaderboard = self.daily_challenge.get_leaderboard()[:10]
        
        embed = discord.Embed(
            title="🏆 Daily Challenge Leaderboard",
            description=f"Wort des Tages: ||{self.daily_challenge.current_word.upper()}||",
            color=discord.Color.blurple()
        )
        
        for i, (user_id, data) in enumerate(leaderboard):
            user = await self.bot.fetch_user(int(user_id))
            embed.add_field(
                name=f"{i+1}. {user.display_name}",
                value=f"Versuche: {data['attempts']} | Zeit: {datetime.fromisoformat(data['timestamp']).strftime('%H:%M:%S')}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed)

    async def handle_daily(self, interaction: discord.Interaction):
        """Handle Daily Challenge aus dem Menü"""
        user_id = interaction.user.id
        
        if self.daily_challenge.has_played(user_id):
            await interaction.response.send_message(
                "❌ Du hast heute bereits gespielt! Komm morgen wieder!",
                ephemeral=True
            )
            return
        
        if user_id in self.games:
            await interaction.response.send_message("❌ Du hast bereits ein aktives Spiel!", ephemeral=True)
            return
        
        daily_word = self.daily_challenge.get_daily_word()
        game = WordleGame(user_id)
        game.secret_word = daily_word
        self.games[user_id] = game
        
        view = GameView(self, user_id)
        await interaction.response.send_message(
            embed=self.create_game_embed(user_id, is_daily=True),
            view=view
        )

class EndGameView(View):
    def __init__(self, cog, user_id: int):
        super().__init__(timeout=10)
        self.cog = cog
        self.user_id = user_id
    
    @ui.button(label="Neues Spiel", style=discord.ButtonStyle.green, emoji="🔄", custom_id="new_game")
    async def new_game(self, interaction: discord.Interaction, button: Button):
        await self.cog.start_new_game(interaction)
    
    @ui.button(label="Statistiken", style=discord.ButtonStyle.blurple, emoji="📊", custom_id="end_stats")
    async def show_stats(self, interaction: discord.Interaction, button: Button):
        user = await self.cog.bot.fetch_user(self.user_id)
        await self.cog.show_stats(interaction, user)

class MainMenu(View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        
        self.add_item(Button(
            label="Neues Spiel", 
            style=discord.ButtonStyle.green, 
            emoji="🎮",
            custom_id="persistent_new_game"
        ))

        self.add_item(Button(
            label="Daily Challenge",
            style=discord.ButtonStyle.blurple,
            emoji="🌞",
            custom_id="persistent_daily"
        ))
        
        options = [
            discord.SelectOption(label="Daily Challenge", value="daily", emoji="🌞"),
            discord.SelectOption(label="Achievements", value="achievements", emoji="🏆"),
            discord.SelectOption(label="Leaderboard", value="leaderboard", emoji="🏆"),
            discord.SelectOption(label="Statistiken", value="stats", emoji="📊"),
            discord.SelectOption(label="Historie", value="history", emoji="📜"),
            discord.SelectOption(label="Einstellungen", value="settings", emoji="⚙️"),
            discord.SelectOption(label="Hilfe", value="help", emoji="❓")
        ]
        self.select = Select(
            placeholder="🏅 Wordle-Menü",
            options=options,
            custom_id="persistent_main_menu"
        )
        self.select.callback = self.menu_select
        self.add_item(self.select)

    async def new_game_callback(self, interaction: discord.Interaction):
        await self.cog.start_new_game(interaction)

# In der MainMenu-Klasse:
    async def menu_select(self, interaction: discord.Interaction):
        choice = interaction.data["values"][0]
        handlers = {
        "daily": self.cog.handle_daily,
        "achievements": self.cog._show_achievements,
        "leaderboard": self.cog.show_leaderboard,
        "stats": self.cog.show_own_stats,
        "history": self.cog.show_own_history,
        "settings": self.cog.open_settings,
        "help": self.cog.show_help
        }
        handler = handlers.get(choice)
        if handler:
            await handler(interaction)  # 👈 Korrekter Methodenaufruf
        else:
            await interaction.response.send_message("❌ Ungültige Auswahl!", ephemeral=True)

    async def show_daily_options(self, interaction: discord.Interaction):
        """Zeigt Daily-Challenge-Optionen an"""
        view = DailyChallengeView(self.cog)
        embed = discord.Embed(
        title="🌞 Daily Challenges",
        description="Wähle eine Option:",
        color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.data["custom_id"] == "persistent_new_game":
            await self.cog.start_new_game(interaction)
            return False
        return True

class GameView(View):
    def __init__(self, cog, user_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.update_buttons()
    
    def update_buttons(self):
        self.clear_items()
        game = self.cog.games.get(self.user_id)
        
        self.add_item(Button(
            label="Raten ✏️", 
            style=discord.ButtonStyle.primary, 
            emoji="📝",
            custom_id="guess_button"
        ))
        self.add_item(Button(
            label=f"Tipp 💡 ({game.hints_used if game else 0}/{MAX_HINTS})",
            style=discord.ButtonStyle.secondary,
            disabled=(not game or game.hints_used >= MAX_HINTS),
            emoji="💡",
            custom_id="hint_button"
        ))
        self.add_item(Button(
            label="Beenden 🗑️", 
            style=discord.ButtonStyle.danger, 
            emoji="❌",
            custom_id="quit_button"
        ))
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("❌ Nicht dein Spiel!", ephemeral=True)
                return False
        
            custom_id = interaction.data["custom_id"]
        
            if custom_id == "guess_button":
                await interaction.response.send_modal(GuessModal(self.cog))
            elif custom_id == "hint_button":
                await self.cog.handle_give_hint(interaction)
            elif custom_id == "quit_button":
                await self.cog.handle_end_game(interaction, False)
            
            return False
        except Exception as e:
            if not interaction.response.is_done():  # 👈 Prüfen ob bereits geantwortet wurde
                await interaction.response.send_message(
                    "⚠️ Ein Fehler ist aufgetreten! Bitte versuche es erneut.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(  # 👈 followup verwenden
                    "⚠️ Ein Fehler ist aufgetreten!",
                    ephemeral=True
                )
            print(f"Interaktionsfehler: {str(e)}")

class GuessModal(Modal, title="Wort eingeben"):
    guess = TextInput(label="Dein 5-Buchstaben-Wort", min_length=5, max_length=5, custom_id="guess_input")
    
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    
    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_process_guess(interaction, self.guess.value)

class SettingsView(View):
    def __init__(self, cog, user_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.settings = cog.settings.get_settings(user_id)
        self.add_buttons()
    
    def add_buttons(self):
        buttons = [
            ("stats_public", "📊 Stats", 0),
            ("history_public", "📜 Historie", 0),
            ("anonymous", "🎭 Anonym", 1),
            ("anon_password", "🔑 Passwort", 2)
        ]
        
        for setting, label, row in buttons:
            btn = Button(
                label=f"{label} {'✅' if self.settings[setting] else '❌'}" if setting != "anon_password" else "🔑 Passwort setzen",
                style=discord.ButtonStyle.primary,
                row=row,
                emoji="⚙️" if setting != "anon_password" else "🔒",
                custom_id=f"setting_{setting}"
            )
            btn.callback = lambda i, s=setting: self.toggle_setting(i, s)
            self.add_item(btn)
    
    async def toggle_setting(self, interaction: discord.Interaction, setting: str):
        if setting == "anon_password":
            await interaction.response.send_modal(AnonPasswordSetModal(self.cog, self.user_id))
            return
        
        new_value = not self.settings[setting]
        self.cog.settings.update_settings(self.user_id, **{setting: new_value})
        await interaction.response.edit_message(view=SettingsView(self.cog, self.user_id))

class AnonPasswordSetModal(Modal, title="Anonym-Passwort setzen"):
    password = TextInput(label="Neues Passwort", placeholder="Mindestens 8 Zeichen", required=True, min_length=8)
    
    def __init__(self, cog, user_id: int):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
    
    async def on_submit(self, interaction: discord.Interaction):
        self.cog.settings.update_settings(self.user_id, anon_password=self.password.value)
        await interaction.response.send_message("✅ Passwort erfolgreich gesetzt!", ephemeral=True)

class AnonPasswordModal(Modal, title="Anonyme Spiele Passwort"):
    password = TextInput(label="Passwort", placeholder="Gib dein Anonym-Passwort ein", required=True)
    
    def __init__(self, cog, user_id: int, parent_view: View):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.parent_view = parent_view
    
    async def on_submit(self, interaction: discord.Interaction):
        settings = self.cog.settings.get_settings(self.user_id)
        if verify_password(settings["anon_password"], self.password.value):
            if hasattr(self.parent_view, 'anon_mode'):
                self.parent_view.anon_mode = True
                self.parent_view.page = 0
                self.parent_view.update_buttons()
                await interaction.response.edit_message(embed=self.parent_view.create_embed(), view=self.parent_view)
            else:
                view = HistoryView(self.cog, self.user_id, interaction.guild.id)
                await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Falsches Passwort!", ephemeral=True)

class SearchModal(Modal, title="Benutzer suchen"):
    name = TextInput(label="Benutzername oder ID", custom_id="search_input")
    
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user = await commands.UserConverter().convert(interaction, self.name.value)
            await self.cog.show_stats(interaction, user)
        except commands.UserNotFound:
            await interaction.response.send_message("❌ Benutzer nicht gefunden!", ephemeral=True)

# Achievement System
class AchievementSystem:
    ACHIEVEMENTS = {
        "speedster": {
            "name": "Blitzmeister ⚡",
            "condition": lambda game: game.get_duration() < 30,
            "description": "Gewinne ein Spiel in unter 30 Sekunden"
        },
        "perfectionist": {
            "name": "Perfektionist 🎯",
            "condition": lambda game: game.attempts == 1,
            "description": "Gewinne im ersten Versuch"
        },
        "hint_hater": {
            "name": "Tipp-Verweigerer 🙈",
            "condition": lambda game: game.hints_used == 0,
            "description": "Gewinne ohne Tipps"
        },
        "veteran": {
            "name": "Veteran 🏆",
            "condition": lambda count: count >= 100,
            "description": "Spiele 100 Spiele"
        }
    }

    def __init__(self, cog):
        self.cog = cog

    def check_achievements(self, user_id: int, game: WordleGame):
        self.cog.history.data.setdefault("achievements", {})
        
        user_achievements = self.cog.history.data["achievements"].setdefault(str(user_id), {})
        new_achievements = []
        
        total_games = len(self.cog.history.get_user_games(user_id, "global")) + len(
            self.cog.history.get_anonymous_games(
                self.cog.settings.get_settings(user_id)["anon_id"]
            )
        )
        
        for achievement_id, data in self.ACHIEVEMENTS.items():
            if achievement_id not in user_achievements:
                try:
                    if achievement_id == "veteran":
                        if data["condition"](total_games):  # 👈 Nur total_games übergeben
                            user_achievements[achievement_id] = datetime.now().isoformat()
                            new_achievements.append(data)
                    elif data["condition"](game):
                        user_achievements[achievement_id] = datetime.now().isoformat()
                        new_achievements.append(data)
                except Exception as e:
                    print(f"Achievement check error: {e}")
        
        return new_achievements

# Daily Challanges   
class DailyChallenge:
    def __init__(self):
        self.data = self.load_data()
    
    def load_data(self):
        try:
            with open(DAILY_FILE) as f:
                data = json.load(f)
                data["last_updated"] = datetime.strptime(data["last_updated"], "%Y-%m-%d").date()
                return data
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return {
                "current_word": None,
                "last_updated": None,
                "participants": {}
            }
    
    def save_data(self):
        with open(DAILY_FILE, "w") as f:
            save_data = self.data.copy()
            save_data["last_updated"] = self.data["last_updated"].isoformat()
            json.dump(save_data, f, indent=2)
    
    def get_daily_word(self):
        if self.should_reset():
            self.data["current_word"] = random.choice(WORDS)
            self.data["last_updated"] = datetime.now().date()
            self.data["participants"] = {}
            self.save_data()
        return self.data["current_word"]
    
    def should_reset(self):
        return self.data["last_updated"] != datetime.now().date()
    
    def has_played(self, user_id: int):
        return str(user_id) in self.data["participants"]
    
    def add_participant(self, user_id: int, attempts: int):
        self.data["participants"][str(user_id)] = {
            "attempts": attempts,
            "timestamp": datetime.now().isoformat()
        }
        self.save_data()

class DailyChallengeView(View):
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.cog = cog
        
        self.add_item(Button(
            label="Heutige Challenge starten", 
            style=discord.ButtonStyle.green, 
            emoji="🎮",
            custom_id="daily_start"
        ))
        
        self.add_item(Button(
            label="Leaderboard anzeigen", 
            style=discord.ButtonStyle.blurple, 
            emoji="🏆",
            custom_id="daily_leaderboard"
        ))

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.data["custom_id"] == "daily_start":
            await self.cog.daily_command(interaction)
        elif interaction.data["custom_id"] == "daily_leaderboard":
            await self.cog.daily_leaderboard(interaction)
        return False
    
    @bot.tree.command(name="daily_leaderboard", description="Zeigt das Daily-Challenge-Ranking")
    async def daily_leaderboard(self, interaction: discord.Interaction):
        leaderboard = self.daily_challenge.get_leaderboard()[:10]
    
        embed = discord.Embed(
        title="🏆 Daily Leaderboard",
        color=discord.Color.gold()
        )
    
        for idx, (user_id, data) in enumerate(leaderboard, 1):
            user = await self.bot.fetch_user(int(user_id))
            embed.add_field(
            name=f"{idx}. {user.display_name}",
            value=f"Versuche: {data['attempts']} | Zeit: {data['timestamp']}",
            inline=False
            )
    
        await interaction.response.send_message(embed=embed)

@bot.event
async def on_ready():
    cog = None
    try:
        cog = WordleCog(bot)
        await bot.add_cog(cog)
        print(f"Cog erfolgreich geladen")
    except Exception as e:
        print(f"Fehler beim Cog-Loading: {str(e)}")
        return

    await bot.tree.sync()
    
    if cog:
        await cog.add_persistent_views()
        
        for guild in bot.guilds:
            if channel_id := cog.config.get_wordle_channel(guild.id):
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        await channel.purge(limit=1)
                        await channel.send(
                            embed=discord.Embed(
                                title="🎮 Wordle-Hauptmenü",
                                                           description=(
                                "**Willkommen im Wordle-Hauptmenü!**\n\n"
                                "▸ 🎮 Starte ein neues Spiel\n"
                                "▸ 🏆 Zeige die Bestenliste an\n"
                                "▸ 📊 Überprüfe deine Statistiken\n"
                                "▸ 📜 Durchsuche deine Spielhistorie\n"
                                "▸ ⚙️ Passe deine Einstellungen an\n"
                                "▸ ❓ Erhalte Spielhilfe\n"
                                "▸ 🔍 Finde andere Spieler"
                            ),
                                color=discord.Color.blue()
                            ),
                            view=MainMenu(cog)
                        )
                    except Exception as e:
                        print(f"Fehler beim Senden der Nachricht: {e}")
    
    print(f"{bot.user} ist bereit!")



if __name__ == "__main__":
    if not os.path.exists(WORDS_FILE):
        with open(WORDS_FILE, "w") as f:
            f.write("\n".join(["apfel", "birne", "banane", "mango", "beere"]))
    
    with open(WORDS_FILE) as f:
        WORDS = [w.strip().lower() for w in f.readlines() if len(w.strip()) == 5]
    
    if not WORDS:
        raise ValueError("Keine gültigen Wörter in der Datei!")
    
    bot.run(TOKEN)
