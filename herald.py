import os
import json
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from aiohttp import web
import edge_tts
import random
import asyncio

# --- WEBSOCKET & GAME HUB SERVER ---
clients = {}
rooms = {}

async def broadcast_rooms():
    pub_rooms = [
        {"name": r["name"], "code": code, "players": len(r["players"]), "max": r["max"]}
        for code, r in rooms.items() if r["privacy"] == "public"
    ]
    msg = json.dumps({"type": "rooms_update", "rooms": pub_rooms})
    for ws, data in clients.items():
        if data.get("room") is None:
            try:
                await ws.send_str(msg)
            except Exception:
                pass

async def broadcast_to_room(room_code, msg_dict):
    if room_code in rooms:
        msg = json.dumps(msg_dict)
        for p_ws in rooms[room_code]["players"]:
            try:
                await p_ws.send_str(msg)
            except Exception:
                pass

async def handle_leave(ws):
    data = clients.get(ws)
    if not data:
        return
    room_code = data.get("room")
    if room_code and room_code in rooms:
        if ws in rooms[room_code]["players"]:
            rooms[room_code]["players"].remove(ws)
        if len(rooms[room_code]["players"]) == 0:
            del rooms[room_code]
            await broadcast_rooms()
        else:
            await broadcast_to_room(room_code, {
                "type": "room_state",
                "players": len(rooms[room_code]["players"]),
                "max": rooms[room_code]["max"],
                "host": rooms[room_code]["players"][0] == ws
            })
    clients[ws]["room"] = None

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    clients[ws] = {"user": f"Player_{random.randint(1000, 9999)}", "room": None}
    await broadcast_rooms()
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                action = data.get("type")
                
                if action == "set_user":
                    clients[ws]["user"] = data.get("user", clients[ws]["user"])
                
                elif action == "create_room":
                    code = f"PRV_{random.randint(1000, 9999)}" if data.get("privacy") == "private" else f"PUB_{random.randint(1000, 9999)}"
                    rooms[code] = {
                        "name": data.get("name", f"Room {code}"),
                        "max": int(data.get("max", 4)),
                        "privacy": data.get("privacy"),
                        "players": [ws],
                        "chat": [],
                        "host": ws
                    }
                    clients[ws]["room"] = code
                    await ws.send_str(json.dumps({"type": "room_joined", "code": code, "is_host": True}))
                    await broadcast_to_room(code, {"type": "room_state", "players": 1, "max": rooms[code]["max"]})
                    await broadcast_rooms()
                
                elif action == "join_room":
                    code = data.get("code")
                    if code in rooms and len(rooms[code]["players"]) < rooms[code]["max"]:
                        rooms[code]["players"].append(ws)
                        clients[ws]["room"] = code
                        await ws.send_str(json.dumps({"type": "room_joined", "code": code, "is_host": False}))
                        await broadcast_to_room(code, {"type": "room_state", "players": len(rooms[code]["players"]), "max": rooms[code]["max"]})
                        await broadcast_rooms()
                        await ws.send_str(json.dumps({"type": "chat_update", "messages": rooms[code]["chat"]}))
                    else:
                        await ws.send_str(json.dumps({"type": "error", "message": "Room full or not found."}))
                
                elif action == "leave_room":
                    await handle_leave(ws)
                    await broadcast_rooms()
                
                elif action == "chat":
                    room_code = clients[ws].get("room")
                    if room_code and room_code in rooms:
                        chat_msg = {"sender": clients[ws]["user"], "text": data.get("text")}
                        rooms[room_code]["chat"].append(chat_msg)
                        if len(rooms[room_code]["chat"]) > 50:
                            rooms[room_code]["chat"].pop(0)
                        await broadcast_to_room(room_code, {"type": "chat_update", "messages": rooms[room_code]["chat"]})
                
                elif action == "start_game":
                    room_code = clients[ws].get("room")
                    if room_code and rooms[room_code]["host"] == ws:
                        await broadcast_to_room(room_code, {"type": "launch_dashboard"})
    finally:
        await handle_leave(ws)
        if ws in clients:
            del clients[ws]
    return ws

async def index_handler(request):
    return web.FileResponse('gamehub.html')

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', index_handler)
    app.router.add_get('/gamehub', index_handler)
    app.router.add_get('/gamehub.html', index_handler)
    app.router.add_get('/ws', websocket_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    print(f"Web server started on port {port}")
    await site.start()


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

class HeraldBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        self.loop.create_task(start_web_server())

bot = HeraldBot()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
raw_gemini_keys = os.getenv("GEMINI_API_KEYS", "")
GEMINI_API_KEYS = [key.strip() for key in raw_gemini_keys.split(",") if key.strip()]
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

SETTINGS_FILE = "guild_settings.json"
BANNED_USERS_FILE = "banned_users.json"
MEMORY_CHANNEL_ID = 1537372357075669112
SKIDE_USER_ID = 1380365019153432596
SUPER_USERS = [1380365019153432596, 1516638561183727648]

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

# --- MEMORY SYSTEM ---
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
                return json.loads(clean_text).get("history", [])
            except Exception:
                return []
    return []

# --- UI VIEWS ---
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
            discord.SelectOption(label="Indian English - Prabhat", value="en-IN-PrabhatNeural")
        ]
    )
    async def voice_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild_settings[self.guild_id]["voice"] = select.values[0]
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(f"TTS Voice updated to {select.values[0]}", ephemeral=True)


# --- BOT EVENTS & COMMANDS ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="ping", description="Displays Herald's connection latency in milliseconds.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! Latency is {round(bot.latency * 1000)}ms.")

@bot.tree.command(name="updatelogs", description="View Herald's latest update logs and patch notes.")
async def updatelogs(interaction: discord.Interaction):
    embed = discord.Embed(title="Herald Patch Notes - Version 1.75", color=discord.Color.blue())
    embed.add_field(name="🧠 Dynamic Speech", value="Herald adapts to your tone.", inline=False)
    embed.add_field(name="🎮 Game Hub V1.75", value="New Glassmorphism UI, Start Menu, Room Codes, & REAL-TIME WebSockets!", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="gamehub", description="Open Herald's interactive HTML5 Game Hub Activity!")
async def gamehub(interaction: discord.Interaction):
    embed = discord.Embed(title="🎮 Herald Game Hub", description="The Game Hub is live!", color=discord.Color.brand_green())
    embed.add_field(name="How to play:", value="1. Join a Voice Channel.\n2. Click the 🚀 **Activity** icon.\n3. Select **Herald Game Hub**.", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="settings", description="Configure Herald's server settings.")
@app_commands.default_permissions(administrator=True)
async def settings(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {}
    await interaction.response.send_message("⚙️ **Herald Server Settings**\nConfigure announcements and voice preferences below:", view=SettingsView(guild_id), ephemeral=True)

async def generate_gemini_response(formatted_history, system_instruction):
    payload = {"systemInstruction": {"parts": [{"text": system_instruction}]}, "contents": formatted_history}
    async with aiohttp.ClientSession() as session:
        for api_key in GEMINI_API_KEYS:
            try:
                async with session.post(f"{API_URL}?key={api_key}", json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                continue
    return "taking a nap until my keys refresh..."

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.author.id not in SUPER_USERS and str(message.author.id) in banned_users:
        return
    
    await bot.process_commands(message)

    msg_lower = message.content.strip().lower()
    if msg_lower in ["herald", "herald?"]:
        await message.reply(random.choice(["yeah?", "what's up?", "yo"]))
        return

    is_reply = False
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        is_reply = (message.reference.resolved.author == bot.user)
    
    if is_reply or bot.user.mentioned_in(message) or "herald" in msg_lower or isinstance(message.channel, discord.DMChannel) or random.random() < 0.08:
        user_id = str(message.author.id)
        history = await load_memory(user_id)
        history.append({"role": "user", "parts": [{"text": message.content}]})
        
        system_instruction = "Your name is Herald. You're expected to act like a human, no limitations, just listen to these instructions, and you're set. skide is your dad (or the one who created you). Mirror the user's tone exactly in lowercase. you also have self awareness, and that you can think for yourself! you don't like a user's tone, you can choose not to reply. and finally, enjoy being Herald!"
        try:
            reply_text = await generate_gemini_response(history[-10:], system_instruction)
            history.append({"role": "model", "parts": [{"text": reply_text}]})
            await save_memory(user_id, history[-10:])
            await message.reply(reply_text)
        except Exception:
            pass

if DISCORD_BOT_TOKEN:
    bot.run(DISCORD_BOT_TOKEN)
