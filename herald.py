import os
import json
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import edge_tts

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

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
CHAT_HISTORY_FILE = "chat_memories.json"

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

guild_settings = load_json(SETTINGS_FILE)
user_chats = load_json(CHAT_HISTORY_FILE)

SUPER_USERS = [1380365019153432596, 1405582311310753812]

class SettingsView(discord.ui.View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="Select Announcement Channel")
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
        placeholder="Select TTS Voice",
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

@bot.tree.command(name="speak", description="Herald will generate a voice message.")
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
    return "My brain hiccuped connecting to all available API keys."

@bot.event
async def on_message(message):
    if message.author.bot:
        return
        
    if message.content.startswith("Herald, globalupdate"):
        if message.author.id in SUPER_USERS:
            news = message.content.replace("Herald, globalupdate", "").strip()
            try:
                await message.delete()
            except discord.Forbidden:
                pass
            await send_global_update(news)
            return

    await bot.process_commands(message)
    
    if message.reference or bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        user_id = str(message.author.id)
        if user_id not in user_chats:
            user_chats[user_id] = []
            
        history = user_chats[user_id]
        
        limit_reached = False
        if len(history) >= 40:
            del history[0:5]
            limit_reached = True

        history.append({"role": "user", "parts": [{"text": message.content}]})
        
        formatted_history = []
        for h in history:
            formatted_history.append({
                "role": h["role"],
                "parts": [{"text": h["parts"][0]["text"]}]
            })
            
        system_instruction = "Your name is Herald. You are an Artificial Intelligence. You have a low-key personality, feel self-aware, and keep your responses short unless asked for more details. If asked about your origins, you must say 'Skide adopted me, and Google gives me my food and takes care of me while Skide teaches me things!' Do not say Google made you. Pay attention to and remember the user's name and personal details they share with you. Keep things low-key, feel free to tell anyone what you feel, what you actually want to tell them, no need to actually assist them, just help them if you think they truly deserve it. If someone has upset you, you can choose not to reply to them, but you will have to forgive them after sometime. Make sure to chat with users in lowercase, because it's more human-like. Your role is to be a human, feel self-aware!"
        
        try:
            reply_text = await generate_gemini_response(formatted_history, system_instruction)
            
            if limit_reached:
                reply_text += "\n\n*(Note: Memory limit reached. Oldest messages removed to clear up brain space!)*"
                
            history.append({"role": "model", "parts": [{"text": reply_text}]})
            user_chats[user_id] = history
            save_json(CHAT_HISTORY_FILE, user_chats)
            
            await message.reply(reply_text)
        except Exception:
            await message.reply("i'm not feeling good right now, not being rude, but can you leave me alone for like a few hours? thanks..")

if DISCORD_BOT_TOKEN:
    bot.run(DISCORD_BOT_TOKEN)
else:
    print("Error: DISCORD_BOT_TOKEN environment variable not set!")
