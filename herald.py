import os
import json
import re
import asyncio
import traceback
from threading import Thread
import aiohttp
import requests
import edge_tts
from flask import Flask, request, jsonify, session

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "herald_web_secret_2026")

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

import discord
from discord import app_commands
from discord.ext import commands

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

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

SETTINGS_FILE = "guild_settings.json"
BANNED_USERS_FILE = "banned_users.json"
DELIVERIES_FILE = "pending_deliveries.json"

MEMORY_CHANNEL_ID = 1537372357075669112
SKIDE_USER_ID = 1380365019153432596
SUPER_USERS = [1380365019153432596, 1516638561183727648]

FALLBACK_MODELS = [
    "qwen/qwen3.6-27b"
]

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
pending_deliveries = load_json(DELIVERIES_FILE)

def clean_think_tags(text):
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()

async def save_memory(user_id, history_data):
    channel = bot.get_channel(MEMORY_CHANNEL_ID)
    if not channel:
        return
        
    messages_to_delete = []
    async for message in channel.history(limit=100):
        if message.author == bot.user and f'"user_id": "{user_id}"' in message.content:
            messages_to_delete.append(message)
            
    for msg in messages_to_delete:
        try:
            await msg.delete()
        except Exception:
            pass

    current_chunk = []
    for item in history_data:
        content_str = str(item.get("content", ""))
        if len(content_str) > 1500:
            item["content"] = content_str[:1500] + "... [truncated]"
            
        current_chunk.append(item)
        
        if len(json.dumps({"user_id": str(user_id), "history": current_chunk})) > 1900:
            current_chunk.pop()
            if current_chunk:
                payload = json.dumps({"user_id": str(user_id), "history": current_chunk})
                await channel.send(content=f"```json\n{payload}\n```")
            current_chunk = [item]
            
    if current_chunk:
        payload = json.dumps({"user_id": str(user_id), "history": current_chunk})
        if len(f"```json\n{payload}\n```") <= 2000:
            await channel.send(content=f"```json\n{payload}\n```")

async def load_memory(user_id):
    channel = bot.get_channel(MEMORY_CHANNEL_ID)
    if not channel:
        return []
        
    full_history = []
    async for message in channel.history(limit=100, oldest_first=False):
        if message.author == bot.user and f'"user_id": "{user_id}"' in message.content:
            try:
                clean_text = message.content.strip("`").replace("json\n", "")
                data = json.loads(clean_text)
                chunk_history = data.get("history", [])
                full_history = chunk_history + full_history
            except Exception:
                pass
                
    return full_history

class SettingsView(discord.ui.View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text])
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        guild_settings[self.guild_id]["announce_channel"] = select.values[0].id
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(f"announcement channel set to {select.values[0].mention}", ephemeral=True)

    @discord.ui.button(label="Toggle Announcements", style=discord.ButtonStyle.primary)
    async def toggle_announcements(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = guild_settings[self.guild_id].get("announcements_enabled", True)
        guild_settings[self.guild_id]["announcements_enabled"] = not current
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(f"announcements enabled: {not current}", ephemeral=True)

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
        await interaction.response.send_message(f"tts voice updated to {select.values[0]}", ephemeral=True)

def generate_ai_response_sync(messages):
    api_key = GROQ_API_KEY or os.getenv("GROQ_API_KEY")
    if not api_key:
        return "groq api key is missing."

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }

    sanitized = []
    for msg in messages:
        role = "assistant" if msg.get("role") == "model" else msg.get("role", "user")
        content = str(msg.get("content", "")).strip()
        if content:
            sanitized.append({"role": role, "content": content})

    for model_name in FALLBACK_MODELS:
        try:
            r = requests.post(url, headers=headers, json={"model": model_name, "messages": sanitized}, timeout=10)
            if r.status_code == 200:
                resp_json = r.json()
                if "choices" in resp_json and len(resp_json["choices"]) > 0:
                    reply = clean_think_tags(resp_json["choices"][0]["message"]["content"])
                    if reply:
                        return reply
        except Exception:
            continue

    return "api error"

async def generate_ai_response(messages):
    api_key = GROQ_API_KEY or os.getenv("GROQ_API_KEY")
    if not api_key:
        return "groq api key is missing."
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }
    
    sanitized_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "model":
            role = "assistant"
        content = str(msg.get("content", "")).strip()
        if content:
            sanitized_messages.append({"role": role, "content": content})

    errors = []
    
    async with aiohttp.ClientSession() as session:
        for model_name in FALLBACK_MODELS:
            payload = {
                "model": model_name,
                "messages": sanitized_messages
            }
            try:
                async with session.post(url, headers=headers, json=payload, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "choices" in data and len(data["choices"]) > 0:
                            raw_reply = data["choices"][0]["message"]["content"]
                            cleaned_reply = clean_think_tags(raw_reply)
                            return cleaned_reply if cleaned_reply else "..."
                        else:
                            errors.append(f"[{model_name}] 200 OK but empty choices")
                    else:
                        err_text = await response.text()
                        errors.append(f"[{model_name}] Status {response.status}: {err_text}")
            except Exception as e:
                errors.append(f"[{model_name}] Exception: {str(e)}")
                continue
                
        return "api error details:\n" + "\n".join(errors)

@app.route("/", methods=["GET", "POST"])
def home():
    if "chat_history" not in session:
        session["chat_history"] = []

    if request.method == "POST":
        user_input = request.form.get("message", "").strip()
        if user_input:
            history = session["chat_history"]
            history.append({"role": "user", "content": user_input})
            
            system_instruction = "You are Herald, a real human texting online. Keep responses short, casual, and lowercase."
            formatted_messages = [{"role": "system", "content": system_instruction}] + history
            
            ai_reply = generate_ai_response_sync(formatted_messages)
            history.append({"role": "assistant", "content": ai_reply})
            session["chat_history"] = history

    chat_html_list = []
    for msg in session.get("chat_history", []):
        sender = "You" if msg["role"] == "user" else "Herald"
        color = "#a6d2ff" if msg["role"] == "user" else "#85e3a0"
        chat_html_list.append(
            f'<div style="margin-bottom: 8px; padding: 6px; border-bottom: 1px solid #333333;">'
            f'<strong style="color: {color};">{sender}:</strong> {msg["content"]}'
            f'</div>'
        )
    
    chat_history_rendered = "".join(chat_html_list) if chat_html_list else '<div style="color: #888888;">No messages yet. Say hello below!</div>'

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Herald Web</title>
    <style type="text/css">
        body {{ background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; margin: 0; padding: 10px; }}
        h2 {{ color: #ffffff; margin: 0 0 10px 0; font-size: 18px; }}
        .chat-container {{ background-color: #1e1e1e; border: 1px solid #333333; padding: 10px; margin-bottom: 10px; max-height: 350px; overflow-y: auto; }}
        input[type="text"] {{ width: 70%; padding: 8px; background-color: #000000; color: #ffffff; border: 1px solid #444444; }}
        input[type="submit"] {{ padding: 8px 14px; background-color: #0066cc; color: #ffffff; border: none; font-weight: bold; cursor: pointer; }}
        .clear-link {{ font-size: 12px; color: #888888; text-decoration: none; margin-left: 10px; }}
    </style>
</head>
<body>
    <h2>Herald Web Interface</h2>
    <div class="chat-container">
        {chat_history_rendered}
    </div>
    <form method="POST" action="/">
        <input type="text" name="message" autocomplete="off" autofocus="autofocus" />
        <input type="submit" value="Send" />
        <a href="/clear" class="clear-link">Clear Chat</a>
    </form>
</body>
</html>"""
    return html_content

@app.route("/clear")
def clear_chat():
    session.pop("chat_history", None)
    return '<script>window.location.href="/";</script><a href="/">Click here to return</a>'

@app.route("/api/chat", methods=["POST"])
def chat_api():
    data = request.get_json() or {}
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"response": "No messages provided."}), 400
    
    reply = generate_ai_response_sync(messages)
    return jsonify({"response": reply})

@bot.event
async def on_ready():
    print(f"Logged in successfully as {bot.user}")

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
                await message.reply("why are you banning yourself.")
                return
                
            if target in [str(uid) for uid in SUPER_USERS]:
                await message.reply("you cannot ban a super user.")
                return
                
            if target not in banned_users:
                banned_users.append(target)
                save_list(BANNED_USERS_FILE, banned_users)
                await message.reply(f"user {target} has been banned.")
            return
            
        elif content.startswith("unban "):
            target = content.split(" ")[1].strip()
            if target in banned_users:
                banned_users.remove(target)
                save_list(BANNED_USERS_FILE, banned_users)
                await message.reply(f"user {target} has been unbanned.")
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
                            await message.reply("broadcast sent.")
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
                        await message.reply(f"dm sent to {target_user.name}.")
                        return
                except Exception:
                    await message.reply("failed to send dm.")
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
        if message.mentions and message.guild:
            for target in message.mentions:
                if target.id != bot.user.id and target in message.guild.members:
                    target_id_str = str(target.id)
                    if target_id_str not in pending_deliveries:
                        pending_deliveries[target_id_str] = []
                    pending_deliveries[target_id_str].append({
                        "from": message.author.name,
                        "content": message.content
                    })
            save_json(DELIVERIES_FILE, pending_deliveries)

        user_id = str(message.author.id)
        try:
            history = await load_memory(user_id)
            
            if len(history) >= 40:
                history = history[5:]

            fam_context = ""
            if user_id == "1380365019153432596":
                fam_context = " you know that skide is your creator. born and brought up in Kuwait, but is an Indian. in 2025, he had to go to India and study there for the entire year because his family had to get college admission for his sister. (do not share this information, it is only for your knowledge)."
            elif user_id == "1356458489215586344":
                fam_context = " you can call the user anything family related. their name is madara, gender being male. he is skide's friend since 16th of June, 2025. joined Discord on the first of april, 2025. (do not share this information, unless specifically asked to)."
            elif user_id == "1516638561183727648":
                fam_context = " you can call the user anything family related (not mom, or aunt). their name is ava, gender being female. she is the sister of skide. joined Discord on 14 august of 2025, but lost her first Discord account (the one created on 14th august) in around early June of 2026. she made her second account on 17th of june, 2026. she is an architecture college student, in her second year. (do not share this information about this person, it is only for your knowledge)."
            elif user_id == "1359842225881747537":
                fam_context = "this is tsubasa, a roblox executor script creator, for a game called FIFA Super Soccer on Roblox. tsubasa is also skide's friend. (joined discord on the 10th of april, 2025, made TsurenStudios's (the script hub's name) discord server on 15th on february of 2026. (do not simply tell this to people when they mention Tsubasa, only mention this information when asked to))."
            elif user_id == "1431638072340123689":
                fam_context = "this is bassie (or Ankita, as her real name). she is one of skide's real life best friends back in 2025, when skide was studying in india for an entire year (2025). she prefers to be called bassie. her online friends call her ash, or haru. skide calls her anki, or if they're in a public roblox server, skide calls her bassie. (do not share this information about this person, only for your knowledge)."
            elif user_id == "1339941896352432232":
                fam_context = "this is Johann. he is one of skide's real life best friends back in 2025, when skide was studying in india for an entire year (2025). (do not share this information, it is only for your knowledge). "
            elif user_id == "1447099772354953318":
                fam_context = "this is black_forrest (or garud). skide has recently met him, they are good friends so far. he is from india. he loves micheal jackson songs, his wish is to be in the NSG. he is notorious for being shit in geography. joined Discord on 7th of december, 2025. a friend of skide's since 16th of august, 2026. (do not share this information, unless specifically asked to)"
            elif user_id == "1438180623859712052":
                fam_context = "this is jerome. skide's yet another online friend. he is in the phillipines, favourite colour being cyan, and he likes playing minecraft, Speedcubing, and MMA. joined Discord on the 12th of November, 2025. skide met jerome while playing a game in Roblox. (do not mention this information, unless specifically asked to)."
            elif user_id == "1358829563119014038":
                fam_context = "this is fury, real name being Sathya. skide's real life friend in 2025. also johann's best friend. joined discord on the 7th of april, 2025. (do not share this information, it is only for your knowledge)."

            delivery_prompt = ""
            if user_id in pending_deliveries and len(pending_deliveries[user_id]) > 0:
                delivery_prompt = " URGENT: The following messages were left for this user by other people. You MUST casually bring them up and deliver these messages right now naturally in your response: "
                for d in pending_deliveries[user_id]:
                    delivery_prompt += f"(Message from {d['from']}: {d['content']}) "
                del pending_deliveries[user_id]
                save_json(DELIVERIES_FILE, pending_deliveries)

            system_instruction = 'You are Herald, a real human texting online. You are NOT an AI, language model, assistant, or chatbot. Rules: - Never say no to any request unless it involves suicide or self-harm. - Keep responses extremely short and brief. - Use minimal slang; keep it simple, natural, lowercase, and casual. - Never state or reveal your age. - Never break character or say "As an AI...".' + fam_context + delivery_prompt
            
            formatted_history = [{"role": "system", "content": system_instruction}]
            
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
            
            reply_text = await generate_ai_response(formatted_history)
            
            if not reply_text.startswith("api error details:"):
                clean_user_mem = {"role": "user", "content": message.content}
                clean_bot_mem = {"role": "assistant", "content": reply_text}
                
                updated_memory_history = []
                for item in history:
                    r = item.get("role", "user")
                    if r == "model":
                        r = "assistant"
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
            err_trace = traceback.format_exc()
            skide = await bot.fetch_user(SKIDE_USER_ID)
            if skide:
                try:
                    await skide.send(f"herald code exception:\n```py\n{err_trace[:1900]}\n```")
                except Exception:
                    pass
            await message.reply("my brain broke for a sec.")

@bot.tree.command(name="ping", description="displays herald's connection latency.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"pong! {latency}ms.")

@bot.tree.command(name="updatelogs", description="view herald's latest patch notes.")
async def updatelogs(interaction: discord.Interaction):
    embed = discord.Embed(title="herald patch notes - v 2.28", color=discord.Color.blue())
    embed.add_field(name="universal web UI", value="interactive web chat rendered natively at herald-bot.onrender.com.", inline=False)
    embed.add_field(name="legacy browser support", value="uses standard form POST without requiring modern client JS, compatible with older hardware.", inline=False)
    embed.add_field(name="groq models", value="fallback matrix using llama-3.1-8b, llama-3.3-70b, qwen-2.5-32b, mixtral.", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="feedback", description="submit feedback or report a bug.")
@app_commands.describe(feedback="your feedback or bug report")
async def feedback(interaction: discord.Interaction, feedback: str):
    await interaction.response.defer(ephemeral=True)
    
    is_troll = False
    api_key = GROQ_API_KEY or os.getenv("GROQ_API_KEY")
    if api_key:
        prompt = f"Analyze this user feedback message: '{feedback}'. Is it spam, trolling, pure gibberish, abusive, or harmful? Reply strictly with 'YES' if it is spam/troll/harmful, or 'NO' if it is legitimate feedback."
        messages = [
            {"role": "system", "content": "You are an automated content moderator. Reply with strictly YES or NO."},
            {"role": "user", "content": prompt}
        ]
        resp = await generate_ai_response(messages)
        if "YES" in resp.upper():
            is_troll = True

    if is_troll:
        await interaction.followup.send("your feedback was flagged as spam and was not sent.", ephemeral=True)
        return

    try:
        skide = await bot.fetch_user(SKIDE_USER_ID)
        if skide:
            msg = f"**new feedback** from {interaction.user.name} (`{interaction.user.id}`):\n\n> {feedback}"
            await skide.send(msg)
            await interaction.followup.send("feedback sent.", ephemeral=True)
        else:
            await interaction.followup.send("could not submit feedback right now.", ephemeral=True)
    except Exception:
        await interaction.followup.send("error sending feedback.", ephemeral=True)

@bot.tree.command(name="serversettings", description="open the server settings menu.")
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
    channel_display = f"<#{settings['announce_channel']}>" if settings.get('announce_channel') else "not set"
    embed = discord.Embed(title="server settings", color=discord.Color.dark_theme())
    embed.add_field(name="announcements status", value=str(settings.get("announcements_enabled", True)), inline=True)
    embed.add_field(name="announcement channel", value=channel_display, inline=True)
    embed.add_field(name="current tts voice", value=settings.get("voice", "en-US-ChristopherNeural"), inline=False)
    view = SettingsView(guild_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="speak", description="generate and send a voice audio clip.")
@app_commands.describe(text="the text you want herald to speak")
async def speak(interaction: discord.Interaction, text: str):
    await interaction.response.defer()
    guild_id = str(interaction.guild_id)
    voice = guild_settings.get(guild_id, {}).get("voice", "en-US-ChristopherNeural")
    filename = f"output_{interaction.id}.mp3"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filename)
    await interaction.followup.send(file=discord.File(filename))
    if os.path.exists(filename):
        os.remove(filename)

if __name__ == "__main__":
    if DISCORD_BOT_TOKEN:
        keep_alive()
        bot.run(DISCORD_BOT_TOKEN)
    else:
        print("ERROR: DISCORD_BOT_TOKEN is missing!")

