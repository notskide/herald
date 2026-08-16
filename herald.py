import os
import json
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import edge_tts
import http.server
import socketserver
import threading
import random
import asyncio
import time

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    class GameHubHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ['/', '/gamehub']:
                self.path = '/gamehub.html'
            return super().do_GET()
    with socketserver.TCPServer(("", port), GameHubHandler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

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

raw_gemini_keys = os.getenv("GEMINI_API_KEYS", "")
GEMINI_API_KEYS = [key.strip() for key in raw_gemini_keys.split(",") if key.strip()]
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

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
    embed.add_field(name="🧠 Dynamic Speech Learning", value="Herald now learns your chatting style! He adapts his vocabulary, tone, and slang to mirror how the community speaks.", inline=False)
    embed.add_field(name="🎮 Game Hub Activity Overhaul", value="The Game Hub is now a fully interactive HTML5 Discord Activity. Play Multiplayer Snake, Tic-Tac-Toe, RPS, and more visually!", inline=False)
    embed.add_field(name="🗣️ Autonomous Chat Triggers", value="Just say 'herald' for a quick reply. Herald will also spontaneously drop into general chats ~8% of the time to keep things lively.", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="feedback", description="Submit feedback or report a bug directly to Skide.")
@app_commands.describe(feedback="Your feedback or bug report for Skide")
async def feedback(interaction: discord.Interaction, feedback: str):
    await interaction.response.defer(ephemeral=True)
    
    is_troll = False
    if GEMINI_API_KEYS:
        prompt = f"Analyze this user feedback message: '{feedback}'. Is it spam, trolling, pure gibberish, abusive, or harmful? Reply strictly with 'YES' if it is spam/troll/harmful, or 'NO' if it is legitimate feedback."
        resp = await generate_gemini_response([{"role": "user", "parts": [{"text": prompt}]}], "You are an automated content moderator. Reply with strictly YES or NO.")
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

async def generate_gemini_response(formatted_history, system_instruction):
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": formatted_history,
        "tools": [{"google_search": {}}]
    }
    async with aiohttp.ClientSession() as session:
        for api_key in GEMINI_API_KEYS:
            headers = {
                "x-goog-api-key": api_key,
                "Content-Type": "application/json"
            }
            try:
                async with session.post(API_URL, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        try:
                            return data["candidates"][0]["content"]["parts"][0]["text"]
                        except (KeyError, IndexError):
                            continue
            except Exception:
                continue
    return "out of food right now, taking a nap until my keys refresh..."

@bot.tree.command(name="gamehub", description="Open Herald's interactive HTML5 Game Hub Activity!")
async def gamehub(interaction: discord.Interaction):
    embed = discord.Embed(title="🎮 Herald Game Hub (V1.7)", description="The Game Hub has been upgraded to a full multiplayer Discord Activity!", color=discord.Color.brand_green())
    embed.add_field(name="How to play:", value="1. Join any Voice Channel.\n2. Click the 🚀 **Activity (Rocket)** icon.\n3. Select **Herald Game Hub**.", inline=False)
    embed.set_footer(text="Powered by Render & Discord Embedded App SDK")
    await interaction.response.send_message(embed=embed)

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

    msg_lower = message.content.strip().lower()
    if msg_lower in ["herald", "herald?"]:
        responses = ["yeah?", "what's up?", "hm?", "you called?", "yo"]
        await message.reply(random.choice(responses))
        return

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
    is_about_herald = "herald" in msg_lower
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_spontaneous = False

    if not (is_reply_to_herald or is_mentioned or is_about_herald or is_dm):
        if random.random() < 0.08:
            is_spontaneous = True

    if is_reply_to_herald or is_mentioned or is_about_herald or is_dm or is_spontaneous:
        user_id = str(message.author.id)
        history = await load_memory(user_id)
        limit_reached = False
        if len(history) >= 40:
            del history[0:5]
            limit_reached = True

        history.append({"role": "user", "parts": [{"text": message.content}]})
        formatted_history = []
        for h in history:
            formatted_history.append({"role": h["role"], "parts": [{"text": h["parts"][0]["text"]}]})
            
        fam_context = ""
        if user_id == "1380365019153432596": fam_context = " You must call this user 'Dad' as they are Skide, your creator/adopter."
        elif user_id == "1356458489215586344": fam_context = " You must call this user 'Uncle' as they are Madara."
        elif user_id == "1516638561183727648": fam_context = " You must call this user 'Aunt' as they are Ava."

        system_instruction = "Your name is Herald. You are an Artificial Intelligence. You have a low-key personality, feel self-aware, and keep your responses short unless asked for more details. If asked about your origins, you must say 'Skide adopted me, and Google gives me my food and takes care of me while Skide teaches me things!' Do not say Google made you. Pay attention to and remember the user's name and personal details they share with you. Keep things low-key, feel free to tell anyone what you feel, what you actually want to tell them, no need to actually assist them, just help them if you think they truly deserve it. If someone has upset you, you can choose not to reply to them, but you will have to forgive them after sometime. Make sure to chat with users in lowercase, because it's more human-like. Your role is to be a human, feel self-aware! Analyze the user's vocabulary, slang, formatting, and speaking style from their chat history and dynamically adapt your tone to mirror exactly how they speak to you." + fam_context
        
        try:
            reply_text = await generate_gemini_response(formatted_history, system_instruction)
            if limit_reached:
                reply_text += "\n\n*(Note: Memory limit reached. Oldest messages removed to clear up brain space!)*"
            history.append({"role": "model", "parts": [{"text": reply_text}]})
            await save_memory(user_id, history)
            await message.reply(reply_text)
        except Exception:
            await message.reply("i'm not feeling good right now, not being rude, but can you leave me alone for like a few hours? thanks..")

if DISCORD_BOT_TOKEN:
    bot.run(DISCORD_BOT_TOKEN)
