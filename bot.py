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
        return {"guilds": {}, "global": {"users": {}}, "anonymous_games": {}}
    
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
            "anonymous": settings["anonymous"]
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
    def __init__(self, cog, guild_id: Optional[int], scope: str = "server"):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.scope = scope
        self.leaderboard_data = []
        self.initialize_data()
        self.create_components()
    
    def initialize_data(self):
        self.leaderboard_data = self.cog.history.get_leaderboard(self.scope, self.guild_id)[:10]
    
    def create_components(self):
        self.clear_items()
        server_btn = Button(
            emoji="🏠",
            label="Server",
            style=discord.ButtonStyle.primary if self.scope == "server" else discord.ButtonStyle.secondary,
            disabled=self.scope == "server",
            custom_id="scope_server"
        )
        global_btn = Button(
            emoji="🌍",
            label="Global",
            style=discord.ButtonStyle.primary if self.scope == "global" else discord.ButtonStyle.secondary,
            disabled=self.scope == "global",
            custom_id="scope_global"
        )
        server_btn.callback = self.switch_scope_server
        global_btn.callback = self.switch_scope_global
        self.add_item(server_btn)
        self.add_item(global_btn)
        
        stats_btn = Button(
            emoji="📊",
            label="Beste Stats",
            style=discord.ButtonStyle.secondary,
            custom_id="show_stats"
        )
        stats_btn.callback = self.show_stats
        self.add_item(stats_btn)
        
        recent_games_btn = Button(
            emoji="🕒",
            label="Letzte Spiele",
            style=discord.ButtonStyle.secondary,
            custom_id="show_recent"
        )
        recent_games_btn.callback = self.show_recent_games
        self.add_item(recent_games_btn)
        
        if self.leaderboard_data:
            options = []
            for entry in self.leaderboard_data:
                user = self.cog.bot.get_user(entry["user_id"])
                options.append(discord.SelectOption(
                    label=user.display_name[:25],
                    value=str(entry["user_id"])
                ))
            select = Select(placeholder="Spieler auswählen", options=options, custom_id="select_player")
            select.callback = self.select_player
            self.add_item(select)
    
    async def switch_scope_server(self, interaction: discord.Interaction):
        await self.switch_scope(interaction, "server")
    
    async def switch_scope_global(self, interaction: discord.Interaction):
        await self.switch_scope(interaction, "global")
    
    async def switch_scope(self, interaction: discord.Interaction, scope: str):
        self.scope = scope
        self.guild_id = interaction.guild.id if scope == "server" else None
        self.initialize_data()
        self.create_components()
        await interaction.response.edit_message(embed=self.create_leaderboard_embed(), view=self)
    
    async def show_stats(self, interaction: discord.Interaction):
        sorted_data = sorted(self.leaderboard_data, 
                           key=lambda x: (-x["win_rate"], -x["avg_attempts"]))
        embed = discord.Embed(
            title=f"📊 {get_scope_label(self.scope)} Beste Stats",
            color=discord.Color.gold()
        )
        for idx, entry in enumerate(sorted_data[:10], 1):
            user = self.cog.bot.get_user(entry["user_id"])
            embed.add_field(
                name=f"{idx}. {user.display_name}",
                value=f"🏆 {entry['win_rate']*100:.1f}% Winrate | Ø {entry['avg_attempts']:.1f} Versuche",
                inline=False
            )
        await interaction.response.edit_message(embed=embed)
    
    async def show_recent_games(self, interaction: discord.Interaction):
        games = []
        for entry in self.leaderboard_data:
            user_games = self.cog.history.get_user_games(entry["user_id"], "global")
            games.extend([(entry["user_id"], g) for g in user_games[-10:]])
        
        view = RecentGamesView(self.cog, games)
        await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)
    
    async def select_player(self, interaction: discord.Interaction):
        selected_id = int(self.children[-1].values[0])
        view = PlayerOptionsView(self.cog, selected_id, self.guild_id, self.scope)
        await interaction.response.edit_message(embed=view.create_options_embed(), view=view)
    
    def create_leaderboard_embed(self):
        embed = discord.Embed(
            title=f"🏆 {get_scope_label(self.scope)} Rangliste",
            color=discord.Color.gold()
        )
        for idx, entry in enumerate(self.leaderboard_data[:10], 1):
            user = self.cog.bot.get_user(entry["user_id"])
            last_games = "\n".join(
                f"{datetime.fromisoformat(g['timestamp']).strftime('%d.%m %H:%M')}: {g['word'].upper()}"
                for g in entry["last_games"][:3]
            )
            embed.add_field(
                name=f"{idx}. {user.display_name}",
                value=f"✅ {entry['wins']} Siege | 📊 {entry['win_rate']*100:.1f}%\n{last_games}",
                inline=False
            )
        return embed

class RecentGamesView(View):
    def __init__(self, cog, games: List[tuple], page: int = 0):
        super().__init__(timeout=60)
        self.cog = cog
        self.games = [g for g in games if isinstance(g[1], dict) and 'id' in g[1]]
        self.page = page
        self.page_size = 5
    
    def create_embed(self):
        embed = discord.Embed(
            title="🕒 Letzte Spiele",
            color=discord.Color.blurple()
        )
        
        start = self.page * self.page_size
        end = start + self.page_size
        paginated_games = self.games[start:end]
        
        for user_id, game in paginated_games:
            user = self.cog.bot.get_user(user_id)
            status = "✅ Gewonnen" if game.get("won", False) else "❌ Verloren"
            mode = "🎭 Anonym" if game.get("anonymous", False) else "🌍 Öffentlich"
            time = datetime.fromisoformat(game["timestamp"]).strftime("%d.%m.%Y %H:%M")
            
            embed.add_field(
                name=f"{user.display_name if user else 'Unknown'} - {time}",
                value=f"{status} | {mode}\nID: `{game.get('id', '???')}`\nWort: ||{game.get('word', 'UNKNOWN').upper()}||",
                inline=False
            )
        
        max_pages = max((len(self.games) - 1) // self.page_size + 1, 1)
        embed.set_footer(text=f"Seite {self.page + 1}/{max_pages}")
        return embed
    
    @ui.button(emoji="⬆️", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: Button):
        if self.page > 0:
            self.page -= 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
    
    @ui.button(emoji="⬇️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        if (self.page + 1) * self.page_size < len(self.games):
            self.page += 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

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
    
    async def show_user_history(self, interaction: discord.Interaction, user: discord.User):
        is_own = interaction.user.id == user.id
        settings = self.settings.get_settings(user.id)
        
        if not is_own and not settings["history_public"]:
            embed = discord.Embed(
                title="🔒 Private Historie",
                description=f"{user.display_name} hat seine Spielhistorie privat eingestellt.",
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

class WordleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: Dict[int, WordleGame] = {}
        self.history = GameHistory()
        self.config = ServerConfig()
        self.settings = UserSettings()
        self.persistent_views_added = False
    
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
    
    def create_game_embed(self, user_id: int):
        settings = self.settings.get_settings(user_id)
        embed = discord.Embed(
            title="🎮 Neues Wordle-Spiel",
            description=f"🔤 Errate das 5-Buchstaben-Wort in 6 Versuchen!\n"
                        f"Anonymmodus: {'✅ Aktiv' if settings['anonymous'] else '❌ Inaktiv'}",
            color=discord.Color.green()
        )
        embed.add_field(name="Farben", 
                       value="🟩 Richtiger Buchstabe\n🟨 Falsche Position\n⬛ Nicht im Wort", 
                       inline=False)
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
                    "▸ 🏆 Zeige die Bestenliste an\n"
                    "▸ 📊 Überprüfe deine Statistiken\n"
                    "▸ 📜 Durchsuche deine Spielhistorie\n"
                    "▸ ⚙️ Passe deine Einstellungen an\n"
                    "▸ ❓ Erhalte Spielhilfe\n"
                    "▸ 🔍 Finde andere Spieler"
                ),
                color=discord.Color.blue()
            ),
            view=MainMenu(self)
        )
        await interaction.response.send_message("✅ Channel eingerichtet!", ephemeral=True)
    
    async def handle_process_guess(self, interaction: discord.Interaction, guess: str):
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
        
        for i, (attempt, res) in enumerate(game.attempts):
            embed.add_field(name=f"Versuch {i+1}", value=f"{attempt.upper()}\n{' '.join(res)}", inline=False)
        
        embed.add_field(name="Hinweis", value=f"`{game.hint_display}`", inline=False)
        
        if guess.lower() == game.secret_word or game.remaining == 0:
            await self.handle_end_game(interaction, guess.lower() == game.secret_word)
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
    
    async def handle_end_game(self, interaction: discord.Interaction, won: bool):
        game = self.games.pop(interaction.user.id, None)
        if not game:
            return
        
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
    
    async def show_stats(self, interaction: discord.Interaction, user: discord.User):
        is_own_stats = interaction.user.id == user.id
        settings = self.settings.get_settings(user.id)
        
        if not is_own_stats and not settings["stats_public"]:
            if interaction.response.is_done():
                await interaction.followup.send("❌ Diese Statistiken sind privat!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Diese Statistiken sind privat!", ephemeral=True)
            return
        
        public_games = self.history.get_user_games(user.id, "global")
        anon_games = self.history.get_anonymous_games(settings["anon_id"])
        
        embed = discord.Embed(
            title=f"📊 Statistiken für {user.display_name}",
            color=discord.Color.gold()
        )
        
        if public_games or anon_games:
            stats_status = "🔓 Öffentlich" if settings["stats_public"] else "🔒 Privat"
            history_status = "🔓 Öffentlich" if settings["history_public"] else "🔒 Privat"
            anonymous_status = "✅ Aktiv" if settings["anonymous"] else "❌ Inaktiv"
            
            embed.description = (
                f"**Einstellungen:**\n"
                f"• Statistiken: {stats_status}\n"
                f"• Historie: {history_status}\n"
                f"• Anonymmodus: {anonymous_status}\n"
                f"• Anonyme Spiele: {len(anon_games)}"
            )
            
            if public_games:
                valid_games = [g for g in public_games if not g.get("anonymous", False)]
                embed.add_field(name="Öffentliche Spiele", 
                               value=f"Gewonnen: {sum(g['won'] for g in valid_games)}\n"
                                     f"Verloren: {len(valid_games)-sum(g['won'] for g in valid_games)}\n"
                                     f"Winrate: {sum(g['won'] for g in valid_games)/len(valid_games)*100:.1f}%",
                               inline=True)
            
            if anon_games:
                embed.add_field(name="Anonyme Spiele", 
                               value=f"Gewonnen: {sum(g['won'] for g in anon_games)}\n"
                                     f"Verloren: {len(anon_games)-sum(g['won'] for g in anon_games)}\n"
                                     f"Winrate: {sum(g['won'] for g in anon_games)/len(anon_games)*100:.1f}%",
                               inline=True)
        else:
            embed.description = "📭 Noch keine Spiele gespielt!"
        
        view = View()
        history_btn = Button(label="Historie anzeigen", style=discord.ButtonStyle.primary, emoji="📜", custom_id="show_history")
        anon_history_btn = Button(label="Anonyme Historie", style=discord.ButtonStyle.secondary, emoji="🎭", custom_id="show_anon_history")
        
        history_btn.callback = lambda i: self.show_history(i, user)
        anon_history_btn.callback = lambda i: self.show_anon_history(i, user)
        
        view.add_item(history_btn)
        if len(anon_games) > 0:
            view.add_item(anon_history_btn)
        
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
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
        if not interaction.response.is_done():
            await interaction.response.defer()
        view = EnhancedLeaderboardView(self, interaction.guild.id, "server")
        await interaction.followup.send(
            embed=view.create_leaderboard_embed(),
            view=view,
            ephemeral=True
        )
    
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
        await self.show_stats(interaction, interaction.user)
    
    async def show_own_history(self, interaction: discord.Interaction):
        await self.show_history(interaction, interaction.user)

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
            label="Neues Spiel 🎮", 
            style=discord.ButtonStyle.green, 
            emoji="🎲",
            custom_id="persistent_new_game"
        ))
        
        options = [
            discord.SelectOption(label="🏆 Leaderboard", value="leaderboard", emoji="🏆"),
            discord.SelectOption(label="📊 Statistiken", value="stats", emoji="📊"),
            discord.SelectOption(label="📜 Historie", value="history", emoji="📜"),
            discord.SelectOption(label="⚙️ Einstellungen", value="settings", emoji="⚙️"),
            discord.SelectOption(label="❓ Hilfe", value="help", emoji="❓"),
            discord.SelectOption(label="🔍 Suche", value="search", emoji="🔍")
        ]
        self.select = Select(
            placeholder="🏅 Wordle-Menü",
            options=options,
            custom_id="persistent_main_menu"
        )
        self.select.callback = self.menu_select
        self.add_item(self.select)
        
        self.children[0].callback = self.new_game_callback

    async def new_game_callback(self, interaction: discord.Interaction):
        await self.cog.start_new_game(interaction)

    async def menu_select(self, interaction: discord.Interaction):
        try:
            choice = interaction.data["values"][0]
        
            handlers = {
                "leaderboard": self.cog.show_leaderboard,
                "stats": self.cog.show_own_stats,
                "history": self.cog.show_own_history,
                "settings": self.cog.open_settings,
                "help": self.cog.show_help,
                "search": self.cog.search_stats
            }
        
            handler = handlers.get(choice)
            if handler:
                if interaction.response.is_done():
                    await handler(interaction)
                else:
                    await interaction.response.defer()
                    await handler(interaction)
            else:
                await interaction.response.send_message("❌ Ungültige Auswahl!", ephemeral=True)
                
        except discord.errors.NotFound:
        # Ignore if the interaction token is invalid (e.g., expired)
            pass
        except Exception as e:
            error_msg = f"⚠️ Fehler bei der Menüauswahl: {str(e)}"
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(error_msg, ephemeral=True)
                else:
                    await interaction.response.send_message(error_msg, ephemeral=True)
            except discord.errors.HTTPException:
            # Ignore if sending the error message fails
                pass
            print(f"Menüfehler: {repr(e)}")

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
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Nicht dein Spiel!", ephemeral=True)
            return False
        
        handler = {
            "guess_button": self.show_guess_modal,
            "hint_button": self.cog.handle_give_hint,
            "quit_button": lambda i: self.cog.handle_end_game(i, False)
        }.get(interaction.data["custom_id"])
        
        if handler:
            await handler(interaction)
        return False
    
    async def show_guess_modal(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GuessModal(self.cog))

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
