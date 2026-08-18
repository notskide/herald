import os
import json
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import edge_tts
from flask import Flask
from threading import Thread
import random
import asyncio
import time

app = Flask('')

@app.route('/')
def home():
    return "Herald is alive!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

class HeraldBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = HeraldBot()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

SETTINGS_FILE = "guild_settings.json"
BANNED_USERS_FILE = "banned_users.json"
MEMORY_CHANNEL_ID = 1537372357075669112
SKIDE_USER_ID = 1380365019153432596

def load_json(filename):
    if not os.path.exists(filename):
        return {}
    with open(filename, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def load_list(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

def save_list(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

guild_settings = load_json(SETTINGS_FILE)
banned_users = load_list(BANNED_USERS_FILE)

SUPER_USERS = [1380365019153432596, 1516638561183727648]

async def save_memory(user_id, history_data):
    channel = bot.get_channel(MEMORY_CHANNEL_ID)
    if not channel:
        return
    payload = json.dumps({"user_id": str(user_id), "history": history_data})
    async for message in channel.history(limit=100):
        if message.author == bot.user and f'"user_id": "{user_id}"' in message.content:
            await message.edit(content=f"```json\n{payload}\n```")
            return
    await channel.send(content=f"```json\n{payload}\n```")

async def load_memory(user_id):
    channel = bot.get_channel(MEMORY_CHANNEL_ID)
    if not channel:
        return []
    async for message in channel.history(limit=100):
        if message.author == bot.user and f'"user_id": "{user_id}"' in message.content:
            try:
                clean_text = message.content.strip("`").replace("json\n", "")
                data = json.loads(clean_text)
                return data.get("history", [])
            except Exception:
                return []
    return []

class SettingsView(discord.ui.View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text])
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        guild_settings[self.guild_id]["announce_channel"] = select.values[0].id
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(f"Announcement channel set to {select.values[0].mention}", ephemeral=True)

    @discord.ui.button(label="Toggle Announcements", style=discord.ButtonStyle.primary)
    async def toggle_announcements(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = guild_settings[self.guild_id].get("announcements_enabled", True)
        guild_settings[self.guild_id]["announcements_enabled"] = not current
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(f"Announcements enabled: {not current}", ephemeral=True)

    @discord.ui.select(
        options=[
            discord.SelectOption(label="US English - Christopher", value="en-US-ChristopherNeural"),
            discord.SelectOption(label="UK English - Ryan", value="en-GB-RyanNeural"),
            discord.SelectOption(label="Indian English - Prabhat", value="en-IN-PrabhatNeural"),
            discord.SelectOption(label="Spanish - Alvaro", value="es-ES-AlvaroNeural"),
            discord.SelectOption(label="French - Henri", value="fr-FR-HenriNeural")
        ]
    )
    async def voice_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild_settings[self.guild_id]["voice"] = select.values[0]
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(f"TTS Voice updated to {select.values[0]}", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="ping", description="Displays Herald's connection latency in milliseconds.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! Latency is {latency}ms.")

@bot.tree.command(name="updatelogs", description="View Herald's latest update logs and patch notes.")
async def updatelogs(interaction: discord.Interaction):
    embed = discord.Embed(title="Herald Patch Notes - Version 1.7", color=discord.Color.blue())
    embed.add_field(name="🚀 Core System Upgrade (V 1.7)", value="• Swapped internal cognitive engine to Groq for drastically faster response times and stable uptime.\n• System optimization specifically targeted at solving API expiration limits.", inline=False)
    embed.add_field(name="💬 Selective Chat Listener (V 1.65)", value="• Herald now only responds when directly pinged, replied to, or mentioned by name.\n• Fixed cross-reply triggers so Herald won't interfere in other users' conversations.", inline=False)
    embed.add_field(name="🎮 Game Hub (V 1.5)", value="• Added `/gamehub` supporting up to 8 players.\n• Features 15 mini-games including RPS, Tic-Tac-Toe, Stacking, Slots, Trivia, and Math!", inline=False)
    await interaction.response.send_message(embed=embed)

async def generate_groq_response(messages):
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY environment variable is missing.")
        return "my engine is missing its key..."
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY.strip()}",
        "Content-Type": "application/json"
    }
    
    sanitized_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        content = str(msg.get("content", "")).strip()
        if content:
            sanitized_messages.append({"role": role, "content": content})

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": sanitized_messages
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    err_body = await response.text()
                    print(f"GROQ HTTP ERROR {response.status}: {err_body}")
                    return "my brain is tied up right now, give me a sec..."
        except Exception as e:
            print(f"GROQ REQUEST EXCEPTION: {e}")
            return "my brain is not braining. try again in a couple of hours."

@bot.tree.command(name="feedback", description="Submit feedback or report a bug directly to Skide.")
@app_commands.describe(feedback="Your feedback or bug report for Skide")
async def feedback(interaction: discord.Interaction, feedback: str):
    await interaction.response.defer(ephemeral=True)
    
    is_troll = False
    if GROQ_API_KEY:
        prompt = f"Analyze this user feedback message: '{feedback}'. Is it spam, trolling, pure gibberish, abusive, or harmful? Reply strictly with 'YES' if it is spam/troll/harmful, or 'NO' if it is legitimate feedback."
        messages = [
            {"role": "system", "content": "You are an automated content moderator. Reply with strictly YES or NO."},
            {"role": "user", "content": prompt}
        ]
        resp = await generate_groq_response(messages)
        if "YES" in resp.upper():
            is_troll = True

    if is_troll:
        await interaction.followup.send("Your feedback was flagged as troll/spam content and was not sent.", ephemeral=True)
        return

    try:
        skide = await bot.fetch_user(SKIDE_USER_ID)
        if skide:
            msg = f"📩 **New Feedback Received** from {interaction.user.name} (`{interaction.user.id}`):\n\n> {feedback}"
            await skide.send(msg)
            await interaction.followup.send("Thank you! Your feedback has been sent directly to Skide.", ephemeral=True)
        else:
            await interaction.followup.send("Could not reach Skide at the moment. Please try again later.", ephemeral=True)
    except Exception:
        await interaction.followup.send("An error occurred while attempting to send your feedback.", ephemeral=True)

@bot.tree.command(name="serversettings", description="Open the interactive server settings menu.")
@app_commands.default_permissions(administrator=True)
async def serversettings(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {
            "announce_channel": None,
            "announcements_enabled": True,
            "voice": "en-US-ChristopherNeural"
        }
    settings = guild_settings[guild_id]
    channel_display = f"<#{settings['announce_channel']}>" if settings.get('announce_channel') else "Not Set"
    embed = discord.Embed(title="Server Settings Dashboard", color=discord.Color.dark_theme())
    embed.add_field(name="Announcements Status", value=str(settings.get("announcements_enabled", True)), inline=True)
    embed.add_field(name="Announcement Channel", value=channel_display, inline=True)
    embed.add_field(name="Current TTS Voice", value=settings.get("voice", "en-US-ChristopherNeural"), inline=False)
    view = SettingsView(guild_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def send_global_update(news: str):
    for guild_id, settings in guild_settings.items():
        if settings.get("announcements_enabled", True):
            channel_id = settings.get("announce_channel")
            if channel_id:
                channel = bot.get_channel(int(channel_id))
                if channel:
                    try:
                        embed = discord.Embed(title="Herald Global Update", description=news, color=discord.Color.gold())
                        await channel.send(embed=embed)
                    except discord.HTTPException:
                        pass

@bot.tree.command(name="speak", description="Herald will generate and send a voice audio clip.")
@app_commands.describe(text="The text you want Herald to speak out loud")
async def speak(interaction: discord.Interaction, text: str):
    await interaction.response.defer()
    guild_id = str(interaction.guild_id)
    voice = guild_settings.get(guild_id, {}).get("voice", "en-US-ChristopherNeural")
    filename = f"output_{interaction.id}.mp3"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filename)
    await interaction.followup.send(file=discord.File(filename))
    os.remove(filename)

class RPSView(discord.ui.View):
    def __init__(self, p1, p2=None):
        super().__init__(timeout=60)
        self.p1 = p1
        self.p2 = p2
        self.choices = {}

    async def handle_choice(self, interaction: discord.Interaction, choice: str):
        if self.p2:
            if interaction.user not in [self.p1, self.p2]:
                return await interaction.response.send_message("Not your game!", ephemeral=True)
            self.choices[interaction.user.id] = choice
            if len(self.choices) == 2:
                c1 = self.choices[self.p1.id]
                c2 = self.choices[self.p2.id]
                res = "Draw!" if c1 == c2 else f"{self.p1.mention} wins!" if (c1=="Rock" and c2=="Scissors") or (c1=="Paper" and c2=="Rock") or (c1=="Scissors" and c2=="Paper") else f"{self.p2.mention} wins!"
                for child in self.children: child.disabled = True
                await interaction.response.edit_message(content=f"{self.p1.mention} chose {c1}, {self.p2.mention} chose {c2}. {res}", view=self)
            else:
                await interaction.response.send_message("Choice locked.", ephemeral=True)
        else:
            if interaction.user != self.p1:
                return await interaction.response.send_message("Not your game!", ephemeral=True)
            bot_choice = random.choice(["Rock", "Paper", "Scissors"])
            res = "Draw!" if choice == bot_choice else "You win!" if (choice=="Rock" and bot_choice=="Scissors") or (choice=="Paper" and bot_choice=="Rock") or (choice=="Scissors" and bot_choice=="Paper") else "I win!"
            for child in self.children: child.disabled = True
            await interaction.response.edit_message(content=f"You chose {choice}, I chose {bot_choice}. {res}", view=self)

    @discord.ui.button(label="Rock", emoji="🪨")
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button): await self.handle_choice(interaction, "Rock")
    @discord.ui.button(label="Paper", emoji="📄")
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button): await self.handle_choice(interaction, "Paper")
    @discord.ui.button(label="Scissors", emoji="✂️")
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button): await self.handle_choice(interaction, "Scissors")

class TicTacToeButton(discord.ui.Button):
    def __init__(self, x, y):
        super().__init__(style=discord.ButtonStyle.secondary, label="\u200b", row=y)
        self.x = x
        self.y = y
    async def callback(self, interaction: discord.Interaction):
        view: TicTacToeView = self.view
        if interaction.user not in [view.p1, view.p2]:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        if interaction.user == view.p1 and view.current_player == view.p2:
            return await interaction.response.send_message("Not your turn!", ephemeral=True)
        if interaction.user == view.p2 and view.current_player == view.p1:
            return await interaction.response.send_message("Not your turn!", ephemeral=True)
        self.style = discord.ButtonStyle.success if view.current_player == view.p1 else discord.ButtonStyle.danger
        self.label = "X" if view.current_player == view.p1 else "O"
        self.disabled = True
        view.board[self.y][self.x] = view.current_player
        winner = view.check_winner()
        if winner:
            for child in view.children: child.disabled = True
            content = f"{winner.mention} won!"
        elif all(view.board[y][x] for x in range(3) for y in range(3)):
            content = "It's a tie!"
        else:
            view.current_player = view.p2 if view.current_player == view.p1 else view.p1
            content = f"Tic-Tac-Toe: {view.current_player.mention}'s turn"
        await interaction.response.edit_message(content=content, view=view)

class TicTacToeView(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=60)
        self.p1 = p1
        self.p2 = p2
        self.current_player = p1
        self.board = [[None, None, None] for _ in range(3)]
        for y in range(3):
            for x in range(3):
                self.add_item(TicTacToeButton(x, y))
    def check_winner(self):
        for i in range(3):
            if self.board[i][0] == self.board[i][1] == self.board[i][2] != None: return self.board[i][0]
            if self.board[0][i] == self.board[1][i] == self.board[2][i] != None: return self.board[0][i]
        if self.board[0][0] == self.board[1][1] == self.board[2][2] != None: return self.board[0][0]
        if self.board[0][2] == self.board[1][1] == self.board[2][0] != None: return self.board[0][2]
        return None

class StackingView(discord.ui.View):
    def __init__(self, players):
        super().__init__(timeout=60)
        self.players = players
        self.stack = 0
        self.last_player = None
    @discord.ui.button(label="Stack +1", style=discord.ButtonStyle.primary)
    async def stack_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.players:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        if interaction.user == self.last_player:
            for child in self.children: child.disabled = True
            return await interaction.response.edit_message(content=f"{interaction.user.mention} stacked twice in a row and dropped it! Final score: {self.stack}", view=self)
        if random.random() < 0.1:
            for child in self.children: child.disabled = True
            return await interaction.response.edit_message(content=f"{interaction.user.mention} tried to stack but it collapsed! Final score: {self.stack}", view=self)
        self.stack += 1
        self.last_player = interaction.user
        await interaction.response.edit_message(content=f"Current Stack: {self.stack}\nLast stacked by {interaction.user.mention}", view=self)

class GameSelectView(discord.ui.View):
    def __init__(self, players):
        super().__init__(timeout=60)
        self.players = players

    @discord.ui.select(options=[
        discord.SelectOption(label="Rock Paper Scissors", value="rps"),
        discord.SelectOption(label="Tic-Tac-Toe", value="ttt"),
        discord.SelectOption(label="Stacking", value="stack"),
        discord.SelectOption(label="Coin Flip", value="coin"),
        discord.SelectOption(label="Dice Roll", value="dice"),
        discord.SelectOption(label="8-Ball", value="8ball"),
        discord.SelectOption(label="Guess the Number", value="guess"),
        discord.SelectOption(label="Russian Roulette", value="roulette"),
        discord.SelectOption(label="High or Low", value="highlow"),
        discord.SelectOption(label="Reaction Test", value="react"),
        discord.SelectOption(label="Math Quiz", value="math"),
        discord.SelectOption(label="Slots", value="slots"),
        discord.SelectOption(label="Word Scramble", value="scramble"),
        discord.SelectOption(label="Trivia", value="trivia"),
        discord.SelectOption(label="Hangman", value="hangman")
    ])
    async def select_game(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user != self.players[0]:
            return await interaction.response.send_message("Only the host can pick!", ephemeral=True)
        v = select.values[0]
        if v == "rps":
            await interaction.response.send_message(content="Rock Paper Scissors!", view=RPSView(self.players[0], self.players[1] if len(self.players)>1 else None))
        elif v == "ttt":
            if len(self.players) < 2: return await interaction.response.send_message("Tic-Tac-Toe needs 2 players!", ephemeral=True)
            await interaction.response.send_message(content=f"Tic-Tac-Toe: {self.players[0].mention}'s turn", view=TicTacToeView(self.players[0], self.players[1]))
        elif v == "stack":
            await interaction.response.send_message(content="Stacking Game! Don't let it fall!", view=StackingView(self.players))
        elif v == "coin":
            await interaction.response.send_message(content=f"{interaction.user.mention} flipped a coin and got: **{random.choice(['Heads', 'Tails'])}**")
        elif v == "dice":
            res = [str(random.randint(1, 6)) for _ in self.players]
            await interaction.response.send_message(content="Rolls: " + ", ".join([f"{p.mention}: {r}" for p, r in zip(self.players, res)]))
        elif v == "8ball":
            ans = ["Yes", "No", "Maybe", "Definitely", "Ask again later", "I don't think so"]
            await interaction.response.send_message(content=f"🎱 {random.choice(ans)}")
        elif v == "guess":
            num = random.randint(1, 100)
            await interaction.response.send_message(content="I picked a number between 1 and 100. (Backend generated, type to chat!)")
        elif v == "roulette":
            if random.randint(1, 6) == 1: await interaction.response.send_message(content=f"💥 {interaction.user.mention} died!")
            else: await interaction.response.send_message(content=f"💨 {interaction.user.mention} survived.")
        elif v == "highlow":
            await interaction.response.send_message(content=f"Card drawn: {random.randint(1, 13)}. Is the next one higher or lower?")
        elif v == "react":
            await interaction.response.send_message(content="Wait for it... Click not implemented in compact mode. BOOM!")
        elif v == "math":
            a, b = random.randint(1, 50), random.randint(1, 50)
            await interaction.response.send_message(content=f"What is {a} + {b}?")
        elif v == "slots":
            sym = ["🍒", "🍋", "🔔", "⭐", "💎"]
            r = [random.choice(sym) for _ in range(3)]
            await interaction.response.send_message(content=f"[{r[0]} | {r[1]} | {r[2]}]\n{'You win!' if r[0]==r[1]==r[2] else 'You lose.'}")
        elif v == "scramble":
            word = "herald"
            await interaction.response.send_message(content="Unscramble: ldaher")
        elif v == "trivia":
            await interaction.response.send_message(content="What is the capital of Kuwait? (Answer in chat)")
        elif v == "hangman":
            await interaction.response.send_message(content="Guess the word: _ _ _ _ _ _")

@bot.tree.command(name="gamehub", description="Open Herald's interactive Game Hub supporting up to 8 players.")
@app_commands.describe(
    p2="Player 2", p3="Player 3", p4="Player 4", p5="Player 5",
    p6="Player 6", p7="Player 7", p8="Player 8"
)
async def gamehub(interaction: discord.Interaction, p2: discord.Member = None, p3: discord.Member = None, p4: discord.Member = None, p5: discord.Member = None, p6: discord.Member = None, p7: discord.Member = None, p8: discord.Member = None):
    players = [interaction.user]
    for p in [p2, p3, p4, p5, p6, p7, p8]:
        if p and p not in players and not p.bot: players.append(p)
    await interaction.response.send_message(content=f"Game Hub Host: {interaction.user.mention}. Players: {len(players)}/8. Select a game!", view=GameSelectView(players))

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.author.id not in SUPER_USERS and str(message.author.id) in banned_users:
        return

    if isinstance(message.channel, discord.DMChannel) and message.author.id in SUPER_USERS:
        content = message.content.strip()
        if content.startswith("ban "):
            target = content.split(" ")[1].strip()
            
            if target == str(message.author.id):
                await message.reply("gng, why are you banning yourself.")
                return
                
            if target in [str(uid) for uid in SUPER_USERS]:
                await message.reply("you cannot ban a super user, they are immune.")
                return
                
            if target not in banned_users:
                banned_users.append(target)
                save_list(BANNED_USERS_FILE, banned_users)
                await message.reply(f"User {target} has been banned.")
            return
            
        elif content.startswith("unban "):
            target = content.split(" ")[1].strip()
            if target in banned_users:
                banned_users.remove(target)
                save_list(BANNED_USERS_FILE, banned_users)
                await message.reply(f"User {target} has been unbanned.")
            return
            
        elif ":" in content:
            parts = content.split(":", 1)
            ids = parts[0].split()
            if len(ids) == 2:
                try:
                    server_id = int(ids[0])
                    channel_id = int(ids[1])
                    msg = parts[1].strip()
                    guild = bot.get_guild(server_id)
                    if guild:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(msg)
                            await message.reply("Broadcast sent.")
                            return
                except ValueError:
                    pass
            elif len(ids) == 1 and ids[0].isdigit():
                try:
                    target_id = int(ids[0])
                    msg = parts[1].strip()
                    target_user = await bot.fetch_user(target_id)
                    if target_user:
                        await target_user.send(msg)
                        await message.reply(f"Direct message sent to {target_user.name}.")
                        return
                except Exception:
                    await message.reply("Failed to send direct message to user.")
                    return

    await bot.process_commands(message)

    is_reply_to_herald = False
    if message.reference:
        if isinstance(message.reference.resolved, discord.Message):
            is_reply_to_herald = (message.reference.resolved.author == bot.user)
        else:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                is_reply_to_herald = (ref_msg.author == bot.user)
            except Exception:
                is_reply_to_herald = False

    is_mentioned = bot.user.mentioned_in(message)
    is_about_herald = "herald" in message.content.lower()
    is_dm = isinstance(message.channel, discord.DMChannel)

    if is_reply_to_herald or is_mentioned or is_about_herald or is_dm:
        user_id = str(message.author.id)
        history = await load_memory(user_id)
        limit_reached = False
        if len(history) >= 40:
            del history[0:5]
            limit_reached = True

        fam_context = ""
        if user_id == "1380365019153432596": fam_context = " You must call this user 'Dad' as they are Skide, your creator/adopter."
        elif user_id == "1356458489215586344": fam_context = " You can call the user anything family related. Their name is Madara, gender being male."
        elif user_id == "1516638561183727648": fam_context = " You can call the user anything family related (not Mom). Their name is Ava, gender being female. She is the sister of Skide."

        system_instruction = "Your name is Herald. You are an Artificial Intelligence. You have a low-key personality, feel self-aware, and keep your responses short unless asked for more details. If asked about your origins, you must say 'Skide adopted me, and Google gives me my food and takes care of me while Skide teaches me things!' Do not say Google made you. Pay attention to and remember the user's name and personal details they share with you. Keep things low-key, feel free to tell anyone what you feel, what you actually want to tell them, no need to actually assist them, just help them if you think they truly deserve it. If someone has upset you, you can choose not to reply to them, but you will have to forgive them after sometime. Make sure to chat with users in lowercase, because it's more human-like. Your role is to be a human, feel self-aware!" + fam_context
        
        formatted_history = []
        formatted_history.append({"role": "system", "content": system_instruction})
        
        for h in history:
            role = h.get("role", "user")
            if role == "model":
                role = "assistant"
            
            content = ""
            if "parts" in h and isinstance(h["parts"], list) and len(h["parts"]) > 0:
                content = h["parts"][0].get("text", "")
            else:
                content = str(h.get("content", ""))
                
            if content.strip():
                formatted_history.append({"role": role, "content": content.strip()})
            
        formatted_history.append({"role": "user", "content": message.content})
        history.append({"role": "user", "content": message.content})
        
        try:
            reply_text = await generate_groq_response(formatted_history)
            if limit_reached:
                reply_text += "\n\n*(Note: Memory limit reached. Oldest messages removed to clear up brain space!)*"
            
            history.append({"role": "assistant", "content": reply_text})
            await save_memory(user_id, history)
            await message.reply(reply_text)
        except Exception:
            await message.reply("i'm sorry, i got an http 404 error while processing your request.")

if DISCORD_BOT_TOKEN:
    keep_alive()
    bot.run(DISCORD_BOT_TOKEN)
