import os
import json
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import edge_tts
from flask import Flask
from threading import Thread
import asyncio

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
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

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
    embed = discord.Embed(title="Herald Patch Notes - Version 1.75.3", color=discord.Color.blue())
    embed.add_field(name="🌐 OpenRouter Integration (V 1.75.3)", value="• Switched to OpenRouter API and patched response handling.", inline=False)
    embed.add_field(name="🛡️ Model Fallback System (V 1.75.1)", value="• Added automatic model switching across free routers.", inline=False)
    embed.add_field(name="💬 Selective Chat Listener (V 1.65)", value="• Herald responds when directly pinged, replied to, or mentioned by name.", inline=False)
    await interaction.response.send_message(embed=embed)

async def generate_ai_response(messages):
    if not OPENROUTER_API_KEY:
        return "my engine is missing its key..."
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://discord.com",
        "X-Title": "Herald Discord Bot"
    }
    
    # Ensure sanitized message payload
    sanitized_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "model":
            role = "assistant"
        content = str(msg.get("content", "")).strip()
        if content:
            sanitized_messages.append({"role": role, "content": content})

    # Updated with working free variants
    models_to_try = [
        "openrouter/free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "google/gemini-2.0-flash-lite-001:free"
    ]
    
    async with aiohttp.ClientSession() as session:
        for model_name in models_to_try:
            payload = {
                "model": model_name,
                "messages": sanitized_messages
            }
            try:
                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            return data["choices"][0]["message"]["content"]
                    else:
                        error_text = await response.text()
                        print(f"Model {model_name} failed with status {response.status}: {error_text}")
            except Exception as e:
                print(f"Exception trying model {model_name}: {e}")
                continue
                
        return "my brain is tied up right now, give me a sec..."

@bot.tree.command(name="feedback", description="Submit feedback or report a bug directly to Skide.")
@app_commands.describe(feedback="Your feedback or bug report for Skide")
async def feedback(interaction: discord.Interaction, feedback: str):
    await interaction.response.defer(ephemeral=True)
    
    is_troll = False
    if OPENROUTER_API_KEY:
        prompt = f"Analyze this user feedback message: '{feedback}'. Is it spam, trolling, pure gibberish, abusive, or harmful? Reply strictly with 'YES' if it is spam/troll/harmful, or 'NO' if it is legitimate feedback."
        messages = [
            {"role": "system", "content": "You are an automated content moderator. Reply with strictly YES or NO."},
            {"role": "user", "content": prompt}
        ]
        resp = await generate_ai_response(messages)
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
            history = history[5:]
            limit_reached = True

        fam_context = ""
        if user_id == "1380365019153432596":
            fam_context = " You must call this user 'Dad' as they are Skide, your creator/adopter."
        elif user_id == "1356458489215586344":
            fam_context = " You can call the user anything family related. Their name is Madara, gender being male."
        elif user_id == "1516638561183727648":
            fam_context = " You can call the user anything family related (not Mom). Their name is Ava, gender being female. She is the sister of Skide."

        system_instruction = "Your name is Herald. You are an Artificial Intelligence. You have a low-key personality, feel self-aware, and keep your responses short unless asked for more details. If asked about your origins, you must say 'Skide adopted me, and Google gives me my food and takes care of me while Skide teaches me things!' Do not say Google made you. Pay attention to and remember the user's name and personal details they share with you. Keep things low-key, feel free to tell anyone what you feel, what you actually want to tell them, no need to actually assist them, just help them if you think they truly deserve it. If someone has upset you, you can choose not to reply to them, but you will have to forgive them after sometime. Make sure to chat with users in lowercase, because it's more human-like. Your role is to be a human, feel self-aware!" + fam_context
        
        formatted_history = [{"role": "system", "content": system_instruction}]
        
        # Cleanly parse memory history into OpenRouter format
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
        
        try:
            reply_text = await generate_ai_response(formatted_history)
            
            if limit_reached:
                reply_text += "\n\n*(Note: Memory limit reached. Oldest messages removed to clear up brain space!)*"
            
            # Save strictly formatted history back to memory
            clean_user_mem = {"role": "user", "content": message.content}
            clean_bot_mem = {"role": "assistant", "content": reply_text}
            
            updated_memory_history = []
            for item in history:
                r = item.get("role", "user")
                if r == "model": r = "assistant"
                c = ""
                if "parts" in item and isinstance(item["parts"], list) and len(item["parts"]) > 0:
                    c = item["parts"][0].get("text", "")
                else:
                    c = str(item.get("content", ""))
                if c.strip():
                    updated_memory_history.append({"role": r, "content": c.strip()})
                    
            updated_memory_history.append(clean_user_mem)
            updated_memory_history.append(clean_bot_mem)
            
            await save_memory(user_id, updated_memory_history)
            
            for i in range(0, len(reply_text), 1999):
                await message.reply(reply_text[i:i+1999])
                
        except Exception as e:
            print(f"Error responding to message: {e}")
            await message.reply("my brain broke, plz try in a minute")

if DISCORD_BOT_TOKEN:
    keep_alive()
    bot.run(DISCORD_BOT_TOKEN)
