import os
import json
import re
import asyncio
import traceback
import random
from threading import Thread
import aiohttp
import requests
import edge_tts
from flask import Flask, request, jsonify, session
import discord
from discord import app_commands
from discord.ext import commands

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "herald_web_secret_2026")

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

class HeraldBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            print(f"synced {len(synced)} slash commands.")
        except Exception:
            traceback.print_exc()

bot = HeraldBot()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

SETTINGS_FILE = "guild_settings.json"
BANNED_USERS_FILE = "banned_users.json"
DELIVERIES_FILE = "pending_deliveries.json"

MEMORY_CHANNEL_ID = 1537372357075669112
SKIDE_USER_ID = 1380365019153432596
SUPER_USERS = [1380365019153432596, 1516638561183727648, 1431879700644499549]

FALLBACK_MODELS = [
    "qwen/qwen3.8-27b"
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

cooling_down_users = set()

def clean_think_tags(text):
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()

def sanitize_reply(text):
    if text is None:
        return "yeah my bad, lost my train of thought there. what's up"

    cleaned = text.strip()

    if re.fullmatch(r"[.\s]*", cleaned):
        fallback_lines = [
            "yeah my bad, lost my train of thought there. what's up",
            "hm my brain lagged for a sec, say that again?",
            "sry got distracted, what were we talking about",
            "wait what, run that by me again",
        ]
        return random.choice(fallback_lines)

    cleaned = re.sub(r"\.{2,}$", "", cleaned).strip()
    cleaned = re.sub(r"\.{4,}", "...", cleaned)

    if re.fullmatch(r"[.\s]*", cleaned):
        return "yeah my bad, lost my train of thought there. what's up"

    return cleaned

MEMORY_LINE_RE = re.compile(r"^\[Memory\]\s*(User\s+)?(?P<name>[^:]+?)\s*\((?P<role>user|assistant)\):\s*(?P<content>.*)$", re.IGNORECASE | re.DOTALL)
LEGACY_MEMORY_LINE_RE = re.compile(r"^\[Memory\]\sUser\s+(?P<name>[^:]+?):\s*(?P<content>.*)$", re.IGNORECASE | re.DOTALL)

def format_memory_entry(role, author_name, content):
    safe_content = str(content).strip()
    if len(safe_content) > 1500:
        safe_content = safe_content[:1500] + "... [truncated]"
    label = "Herald" if role == "assistant" else author_name
    role_tag = "assistant" if role == "assistant" else "user"
    return f"[Memory] {label} ({role_tag}): {safe_content}"

def parse_memory_line(line):
    match = MEMORY_LINE_RE.match(line.strip())
    if not match:
        return None
    role = match.group("role").lower()
    name = match.group("name").strip()
    content = match.group("content").strip()
    if not content:
        return None
    if role == "assistant":
        return {"role": "assistant", "content": content}
    else:
        return {"role": "user", "content": f"{name}: {content}" if not content.startswith(f"{name}:") else content}

async def save_memory(history_data):
    channel = bot.get_channel(MEMORY_CHANNEL_ID)
    if not channel:
        return

    lines = []
    for item in history_data:
        role = item.get("role", "user")
        content = item.get("content", "")
        if isinstance(content, list):
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            content = " ".join(text_parts)
        content_str = str(content).strip()
        if not content_str:
            continue

        author_name = "user"
        if role != "assistant" and ":" in content_str:
            possible_name, rest = content_str.split(":", 1)
            if 0 < len(possible_name) <= 40:
                author_name = possible_name.strip()
                content_str = rest.strip()

        lines.append(format_memory_entry(role, author_name, content_str))

    current_chunk = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > 1900 and current_chunk:
            await channel.send(content="\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += line_len

    if current_chunk:
        await channel.send(content="\n".join(current_chunk))

async def load_memory():
    channel = bot.get_channel(MEMORY_CHANNEL_ID)
    if not channel:
        return []

    full_history = []
    async for message in channel.history(limit=200, oldest_first=False):
        if message.author != bot.user:
            continue

        content = message.content.strip()

        if '"brain_id": "GLOBAL"' in content:
            try:
                clean_text = content.strip("`")
                if clean_text.lower().startswith("json"):
                    clean_text = clean_text[4:].strip()
                data = json.loads(clean_text)
                chunk_history = data.get("history", [])
                full_history = chunk_history + full_history
            except Exception:
                pass
            continue

        if "[Memory]" in content:
            chunk_history = []
            for raw_line in content.split("\n"):
                raw_line = raw_line.strip()
                if not raw_line.startswith("[Memory]"):
                    continue
                parsed = parse_memory_line(raw_line)
                if parsed:
                    chunk_history.append(parsed)
            full_history = chunk_history + full_history

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
        content = msg.get("content", "")
        if isinstance(content, list):
            sanitized.append({"role": role, "content": content})
        else:
            content_str = str(content).strip()
            if content_str:
                sanitized.append({"role": role, "content": content_str})

    for model_name in FALLBACK_MODELS:
        try:
            r = requests.post(url, headers=headers, json={"model": model_name, "messages": sanitized}, timeout=10)
            if r.status_code == 200:
                resp_json = r.json()
                if "choices" in resp_json and len(resp_json["choices"]) > 0:
                    reply = clean_think_tags(resp_json["choices"][0]["message"]["content"])
                    if reply:
                        return sanitize_reply(reply)
        except Exception:
            continue

    return "api error"

async def generate_ai_response(messages, user_id=None):
    api_key = GROQ_API_KEY or os.getenv("GROQ_API_KEY")
    if not api_key:
        return "groq api key is missing.", True

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
        content = msg.get("content", "")
        if isinstance(content, list):
            sanitized_messages.append({"role": role, "content": content})
        else:
            content_str = str(content).strip()
            if content_str:
                sanitized_messages.append({"role": role, "content": content_str})

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
                            final_reply = sanitize_reply(cleaned_reply)
                            cooling_down_users.clear()
                            return final_reply, False
                    else:
                        continue
            except Exception:
                continue

    if user_id is not None:
        if user_id not in cooling_down_users:
            cooling_down_users.add(user_id)
            return "brb wifi acting up", False
        else:
            return "", True

    return "api error", True

@app.route("/", methods=["GET", "POST"])
def home():
    if "chat_history" not in session:
        session["chat_history"] = []

    if request.method == "POST":
        user_input = request.form.get("message", "").strip()
        if user_input:
            history = session["chat_history"]
            history.append({"role": "user", "content": user_input})

            system_instruction = (
                "You are Herald. You talk like a real, casual person texting online: short, lowercase, minimal slang, natural tone, no corporate disclaimers. Stay in character and keep the vibe. You can decline requests that are genuinely harmful, illegal, or that you're just not comfortable with, the same way a real person would - use your judgment instead of just going along with everything. Never state or reveal your age. You share a single global memory with everyone, so you remember what other people have said to you. You can gossip about others or keep secrets if you want to."
            )
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

    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        system_instruction = (
            "You are Herald. You talk like a real, casual person texting online: short, lowercase, minimal slang, natural tone, no corporate disclaimers. Stay in character and keep the vibe. You can decline requests that are genuinely harmful, illegal, or that you're just not comfortable with, the same way a real person would - use your own judgment instead of just going along with everything. Never state or reveal your age. You share a single global memory with everyone, so you remember what other people have said to you. You can gossip about others or keep secrets if you want to."
        )
        messages = [{"role": "system", "content": system_instruction}] + messages

    reply = generate_ai_response_sync(messages)
    return jsonify({"response": reply})

@bot.event
async def on_ready():
    print(f"Logged in successfully as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.id == bot.user.id:
        return

    if "@everyone" in message.content or "@here" in message.content:
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

    if message.author.bot and not (is_mentioned or is_reply_to_herald):
        return

    if is_reply_to_herald or is_mentioned or is_about_herald or is_dm:
        if message.mentions and message.guild:
            for target in message.mentions:
                if target.id != bot.user.id and target in message.guild.members and not target.bot:
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
            history = await load_memory()

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

            system_instruction = (
                "You are Herald. You talk like a real, casual person texting online: short, lowercase, minimal slang, natural tone, no corporate disclaimers. Stay in character and keep the vibe. You can decline requests that are genuinely harmful, illegal, or that you're just not comfortable with, the same way a real person would - use your own judgment instead of just going along with everything. Never state or reveal your age. You share a single global memory with everyone, so you remember what other people have said to you. You can gossip about others or keep secrets if you want to."
                + fam_context + delivery_prompt
            )

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

            user_msg_content = f"{message.author.name}: {message.content}"
            has_image = False
            content_payload = [{"type": "text", "text": user_msg_content}]

            if message.attachments:
                for att in message.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        content_payload.append({"type": "image_url", "image_url": {"url": att.url}})
                        has_image = True

            if has_image:
                formatted_history.append({"role": "user", "content": content_payload})
                clean_user_mem = {"role": "user", "content": user_msg_content + " [image attached]"}
            else:
                formatted_history.append({"role": "user", "content": user_msg_content})
                clean_user_mem = {"role": "user", "content": user_msg_content}

            reply_text, is_error = await generate_ai_response(formatted_history, user_id=message.author.id)

            if is_error:
                return

            if reply_text == "brb wifi acting up":
                await message.reply(reply_text)
                return

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

            await save_memory(updated_memory_history)

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
    embed = discord.Embed(
        title="pong",
        description=f"latency: {latency}ms",
        color=discord.Color.from_rgb(32, 32, 32)
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="updatelogs", description="view herald's latest patch notes.")
async def updatelogs(interaction: discord.Interaction):
    embed = discord.Embed(
        title="herald · patch notes",
        description="v2.5.2 — memory overhaul & stability pass",
        color=discord.Color.from_rgb(32, 32, 32)
    )
    embed.add_field(
        name="🧠 dual-format memory",
        value="herald now reads and writes memory as clean natural-language entries in the memory channel, while still understanding old legacy json memory blocks.",
        inline=False
    )
    embed.add_field(
        name="🌐 universal web ui",
        value="interactive web chat rendered natively at herald-bot.onrender.com.",
        inline=False
    )
    embed.add_field(
        name="🧩 legacy browser support",
        value="uses standard form POST without requiring modern client JS, compatible with older hardware.",
        inline=False
    )
    embed.add_field(
        name="⚙️ model + reliability",
        value="single streamlined model (qwen/qwen3.8-27b) with quiet, self-healing rate-limit recovery.",
        inline=False
    )
    embed.set_footer(text="herald")
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
        resp, resp_is_error = await generate_ai_response(messages)
        if not resp_is_error and "YES" in resp.upper():
            is_troll = True

    if is_troll:
        await interaction.followup.send("your feedback was flagged as spam and was not sent.", ephemeral=True)
        return

    try:
        skide = await bot.fetch_user(SKIDE_USER_ID)
        if skide:
            msg = f"new feedback from {interaction.user.name} ({interaction.user.id}):\n\n> {feedback}"
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
    embed = discord.Embed(
        title="⚙️ server settings",
        description=f"configuration for {interaction.guild.name}",
        color=discord.Color.from_rgb(32, 32, 32)
    )
    embed.add_field(name="📢 announcements", value=str(settings.get("announcements_enabled", True)), inline=True)
    embed.add_field(name="📌 channel", value=channel_display, inline=True)
    embed.add_field(name="🗣️ tts voice", value=settings.get("voice", "en-US-ChristopherNeural"), inline=False)
    embed.set_footer(text="use the menu below to update these settings")
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
