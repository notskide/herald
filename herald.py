import os
import asyncio
import json
import re
import html
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
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(32)


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def keep_alive():
    thread = Thread(target=run_flask, daemon=True)
    thread.start()


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
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

SETTINGS_FILE = "guild_settings.json"
BANNED_USERS_FILE = "banned_users.json"
DELIVERIES_FILE = "pending_deliveries.json"

MEMORY_CHANNEL_ID = 1537372357075669112
SKIDE_USER_ID = 1380365019153432596
SUPER_USERS = [1380365019153432596, 1516638561183727648, 1431879700644499549]

FALLBACK_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
]
MAX_COMPLETION_TOKENS = 512


def get_groq_api_keys():
    raw_keys = os.getenv("GROQ_API_KEY") or GROQ_API_KEY or ""
    keys = []
    for item in raw_keys.split(","):
        key = item.strip().strip("\"").strip("'").strip()
        if len(key) >= 2 and key.startswith("[") and key.endswith("]"):
            key = key[1:-1].strip()
        if key and key not in keys:
            keys.append(key)
    return keys



async def notify_superuser_api_error(error_title, error_details, report_context=None, request_summary=None):
    """DM the first configured superuser who can receive an error report."""
    report_context = report_context or {}
    user_message = str(report_context.get("message", "(not available)"))
    if len(user_message) > 850:
        user_message = user_message[:850] + "... [truncated]"

    lines = [
        "🚨 **Herald API error report**",
        f"**Error:** {str(error_title)[:300]}",
        f"**Details / likely cause:** {str(error_details)[:550]}",
        f"**User:** {report_context.get('author_name', 'Unknown')} ({report_context.get('author_id', 'unknown')})",
        f"**Server / channel:** {report_context.get('guild_name', 'DM or unknown')} / {report_context.get('channel_name', 'unknown')}",
        f"**Message ID:** {report_context.get('message_id', 'unknown')}",
        "**User's message/request:**",
        user_message or "(empty message)",
    ]
    if request_summary:
        lines.extend(["**Request sent to Groq:**", str(request_summary)[:250]])
    report = "\n".join(lines)

    for superuser_id in SUPER_USERS:
        try:
            superuser = bot.get_user(superuser_id) or await bot.fetch_user(superuser_id)
            if superuser:
                # Keep the report below Discord's message limit.
                await superuser.send(report[:1950])
                print(f"[Herald] API error report delivered to superuser ID {superuser_id}.")
                return True
        except Exception as exc:
            print(f"[Herald] Could not DM superuser ID {superuser_id}: {type(exc).__name__}: {exc}")
    print("[Herald] ERROR: Could not deliver the API error report to any configured superuser.")
    return False

def load_json(filename):
    if not os.path.exists(filename):
        return {}
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


def load_list(filename):
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_list(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


guild_settings = load_json(SETTINGS_FILE)
banned_users = load_list(BANNED_USERS_FILE)
pending_deliveries = load_json(DELIVERIES_FILE)
cooling_down_users = set()


def clean_think_tags(text):
    if not text:
        return ""
    return re.sub(r"<think>.*?</think>", "", str(text), flags=re.DOTALL).strip()


def sanitize_reply(text):
    if text is None:
        return "yeah my bad, lost my train of thought there. what's up"

    cleaned = str(text).strip()

    if re.fullmatch(r"[.\s]*", cleaned):
        return random.choice([
            "yeah my bad, lost my train of thought there. what's up",
            "hm my brain lagged for a sec, say that again?",
            "sry got distracted, what were we talking about",
            "wait what, run that by me again",
        ])

    cleaned = re.sub(r"\.{4,}", "...", cleaned)
    cleaned = re.sub(r"\.{2,}$", "", cleaned).strip()

    if re.fullmatch(r"[.\s]*", cleaned):
        return "yeah my bad, lost my train of thought there. what's up"

    return cleaned


MEMORY_LINE_RE = re.compile(
    r"^\[Memory\]\s*(User\s+)?(?P<name>[^:]+?)\s*\((?P<role>user|assistant)\):\s*(?P<content>.*)$",
    re.IGNORECASE | re.DOTALL,
)


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
    return {
        "role": "user",
        "content": content if content.startswith(f"{name}:") else f"{name}: {content}",
    }


async def save_memory(history_data):
    channel = bot.get_channel(MEMORY_CHANNEL_ID)
    if not channel:
        return

    lines = []
    for item in history_data:
        role = item.get("role", "user")
        content = item.get("content", "")
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
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
            except (json.JSONDecodeError, TypeError, AttributeError):
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
        guild_settings.setdefault(self.guild_id, {})["announce_channel"] = select.values[0].id
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(
            f"announcement channel set to {select.values[0].mention}", ephemeral=True
        )

    @discord.ui.button(label="Toggle Announcements", style=discord.ButtonStyle.primary)
    async def toggle_announcements(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = guild_settings.setdefault(self.guild_id, {})
        current = settings.get("announcements_enabled", True)
        settings["announcements_enabled"] = not current
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(
            f"announcements enabled: {not current}", ephemeral=True
        )

    @discord.ui.select(
        options=[
            discord.SelectOption(label="US English - Christopher", value="en-US-ChristopherNeural"),
            discord.SelectOption(label="UK English - Ryan", value="en-GB-RyanNeural"),
            discord.SelectOption(label="Indian English - Prabhat", value="en-IN-PrabhatNeural"),
            discord.SelectOption(label="Spanish - Alvaro", value="es-ES-AlvaroNeural"),
            discord.SelectOption(label="French - Henri", value="fr-FR-HenriNeural"),
        ]
    )
    async def voice_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild_settings.setdefault(self.guild_id, {})["voice"] = select.values[0]
        save_json(SETTINGS_FILE, guild_settings)
        await interaction.response.send_message(
            f"tts voice updated to {select.values[0]}", ephemeral=True
        )


MAX_GROQ_INPUT_CHARS = 24000
MAX_GROQ_MESSAGES = 24
MAX_GROQ_MESSAGE_CHARS = 6000
MAX_GROQ_IMAGES_PER_MESSAGE = 3


def sanitize_messages(messages):
    normalized = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "user")
        if role == "model":
            role = "assistant"

        content = msg.get("content", "")
        if isinstance(content, list):
            cleaned_parts = []
            image_count = 0
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "text":
                    text_value = str(part.get("text", "")).strip()
                    if text_value:
                        cleaned_parts.append({
                            "type": "text",
                            "text": text_value[:MAX_GROQ_MESSAGE_CHARS],
                        })
                elif part_type == "image_url" and image_count < MAX_GROQ_IMAGES_PER_MESSAGE:
                    image_url = part.get("image_url")
                    if isinstance(image_url, dict) and image_url.get("url"):
                        cleaned_parts.append({
                            "type": "image_url",
                            "image_url": {"url": image_url["url"]},
                        })
                        image_count += 1
            if cleaned_parts:
                normalized.append({"role": role, "content": cleaned_parts})
        else:
            content_str = str(content).strip()
            if content_str:
                normalized.append({
                    "role": role,
                    "content": content_str[:MAX_GROQ_MESSAGE_CHARS],
                })

    if not normalized:
        return []

    system_messages = [msg for msg in normalized if msg["role"] == "system"]
    other_messages = [msg for msg in normalized if msg["role"] != "system"]
    selected = system_messages[:1] + other_messages[-(MAX_GROQ_MESSAGES - min(1, len(system_messages))):]

    def content_size(content):
        if isinstance(content, str):
            return len(content)
        size = 0
        for part in content:
            if part.get("type") == "text":
                size += len(part.get("text", ""))
            elif part.get("type") == "image_url":
                size += len(part.get("image_url", {}).get("url", ""))
        return size

    total_chars = sum(content_size(msg["content"]) for msg in selected)
    while len(selected) > 1 and total_chars > MAX_GROQ_INPUT_CHARS:
        remove_index = 1 if selected[0].get("role") == "system" else 0
        removed = selected.pop(remove_index)
        total_chars -= content_size(removed["content"])

    if selected and total_chars > MAX_GROQ_INPUT_CHARS:
        system_message = selected[0]
        content = system_message["content"]
        if isinstance(content, str):
            system_message["content"] = content[:MAX_GROQ_INPUT_CHARS]
        else:
            remaining = MAX_GROQ_INPUT_CHARS
            trimmed_parts = []
            for part in content:
                if part.get("type") == "text" and remaining > 0:
                    text_value = part.get("text", "")[:remaining]
                    if text_value:
                        trimmed_parts.append({"type": "text", "text": text_value})
                        remaining -= len(text_value)
                elif part.get("type") == "image_url" and remaining > 0:
                    url = part.get("image_url", {}).get("url", "")
                    if len(url) <= remaining:
                        trimmed_parts.append({"type": "image_url", "image_url": {"url": url}})
                        remaining -= len(url)
            system_message["content"] = trimmed_parts

    if len(normalized) != len(selected) or total_chars > MAX_GROQ_INPUT_CHARS:
        print(
            f"[Groq] Trimmed conversation history to {len(selected)} messages "
            f"and approximately {min(total_chars, MAX_GROQ_INPUT_CHARS)} characters."
        )

    return selected


def generate_ai_response_sync(messages):
    api_keys = get_groq_api_keys()
    if not api_keys:
        print("[Groq sync] ERROR: GROQ_API_KEY is missing or contains no valid keys.")
        return "groq api key is missing."

    url = "https://api.groq.com/openai/v1/chat/completions"
    sanitized = sanitize_messages(messages)

    for model_name in FALLBACK_MODELS:
        for key_index, api_key in enumerate(api_keys, start=1):
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            try:
                print(f"[Groq sync] Sending request using model: {model_name} (key {key_index}/{len(api_keys)})")
                response = requests.post(
                    url,
                    headers=headers,
                    json={
                        "model": model_name,
                        "messages": sanitized,
                        "max_completion_tokens": MAX_COMPLETION_TOKENS,
                    },
                    timeout=30,
                )
                if response.status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if choices:
                        reply = clean_think_tags(choices[0].get("message", {}).get("content"))
                        if reply:
                            print(f"[Groq sync] Request successful with key {key_index}.")
                            return sanitize_reply(reply)
                    print("[Groq sync] Successful HTTP response, but no usable reply.")
                else:
                    print(
                        f"[Groq sync] HTTP {response.status_code} for {model_name} "
                        f"(key {key_index}/{len(api_keys)}): {response.text[:1000]}"
                    )
                    if response.status_code == 413:
                        print(
                            "[Groq sync] HTTP 413 means the request payload is too large. "
                            "Conversation history is capped before sending."
                        )
                        return "request payload too large"
            except requests.RequestException as exc:
                print(f"[Groq sync] Network error for {model_name} using key {key_index}: {exc}")
            except (ValueError, KeyError, TypeError):
                traceback.print_exc()
            except Exception:
                traceback.print_exc()

    return "api error"


async def generate_ai_response(messages, user_id=None, report_context=None):
    api_keys = get_groq_api_keys()
    if not api_keys:
        print("[Groq] ERROR: GROQ_API_KEY is missing or contains no valid keys.")
        return "brb wifi acting up", False

    url = "https://api.groq.com/openai/v1/chat/completions"
    sanitized_messages = sanitize_messages(messages)

    last_error = None
    async with aiohttp.ClientSession() as session:
        for model_name in FALLBACK_MODELS:
            for key_index, api_key in enumerate(api_keys, start=1):
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model_name,
                    "messages": sanitized_messages,
                    "max_completion_tokens": MAX_COMPLETION_TOKENS,
                }
                try:
                    print(f"[Groq] Sending request using model: {model_name} (key {key_index}/{len(api_keys)})")
                    async with session.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            choices = data.get("choices", [])
                            if choices:
                                raw_reply = choices[0].get("message", {}).get("content")
                                cleaned_reply = clean_think_tags(raw_reply)
                                if cleaned_reply:
                                    cooling_down_users.clear()
                                    print(f"[Groq] Request successful with key {key_index}.")
                                    return sanitize_reply(cleaned_reply), False
                            print("[Groq] Successful HTTP response, but no usable reply.")
                        else:
                            error_body = await response.text()
                            print(
                                f"[Groq] HTTP {response.status} for {model_name} "
                                f"(key {key_index}/{len(api_keys)}): {error_body[:1000]}"
                            )
                            image_parts = sum(
                                1
                                for item in sanitized_messages
                                if isinstance(item.get("content"), list)
                                for part in item["content"]
                                if part.get("type") == "image_url"
                            )
                            request_summary = (
                                f"model={model_name}; messages={len(sanitized_messages)}; "
                                f"approx_chars={sum(len(str(item.get('content', ''))) for item in sanitized_messages)}; "
                                f"image_parts={image_parts}"
                            )
                            cause = error_body[:1200] or "The API returned an empty error body."
                            if response.status == 413:
                                cause = "Request body too large (HTTP 413). Reduce message/history size or image payloads. API response: " + cause
                            elif response.status == 429:
                                cause = "Rate limit or quota exceeded (HTTP 429). Check the API organization's limits and retry timing. API response: " + cause
                            else:
                                cause = f"Groq rejected the request with HTTP {response.status}. API response: " + cause
                            await notify_superuser_api_error(
                                f"Groq HTTP {response.status}", cause, report_context, request_summary
                            )
                            # A 413 will not be fixed by switching models. For 429, try
                            # another model because OTPM limits can differ by model; rotating
                            # keys from the same organization will not raise its organization cap.
                            if response.status == 413:
                                return f"Groq API error (HTTP {response.status}): {cause[:500]}", False
                            if response.status == 429:
                                last_error = f"Groq HTTP 429 on {model_name}: {cause[:900]}"
                                break
                            return f"Groq API error (HTTP {response.status}): {cause[:500]}", False
                except asyncio.TimeoutError as exc:
                    last_error = f"Request timed out after 30 seconds while calling model {model_name}."
                    print(f"[Groq] {last_error}")
                except aiohttp.ClientError as exc:
                    last_error = f"Network/connection error ({type(exc).__name__}): {str(exc) or 'no additional details'}"
                    print(f"[Groq] Network error for {model_name} using key {key_index}: {exc}")
                except Exception as exc:
                    last_error = f"Unexpected error ({type(exc).__name__}): {str(exc) or 'no additional details'}"
                    traceback.print_exc()

    if user_id is not None:
        cooling_down_users.add(user_id)

    reason = last_error or "No response was received from the Groq API. Check the bot logs and API configuration."
    if report_context:
        await notify_superuser_api_error("Groq request failed", reason, report_context)
    return f"i couldn't complete that request. **Error:** {reason[:700]}", False


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
                "You are Herald. You talk like a real, casual person texting online: short, lowercase, "
                "minimal slang, natural tone, no corporate disclaimers. Stay in character and keep the vibe. "
                "You can decline requests that are genuinely harmful, illegal, or that you're just not comfortable "
                "with, the same way a real person would - use your judgment instead of just going along with everything. "
                "Never state or reveal your age. You share a single global memory with everyone, so you remember what "
                "other people have said to you. You can gossip about others or keep secrets if you want."
            )
            formatted_messages = [{"role": "system", "content": system_instruction}] + history
            ai_reply = generate_ai_response_sync(formatted_messages)
            history.append({"role": "assistant", "content": ai_reply})
            session["chat_history"] = history

    chat_html_list = []
    for msg in session.get("chat_history", []):
        sender = "You" if msg["role"] == "user" else "Herald"
        color = "#a6d2ff" if msg["role"] == "user" else "#85e3a0"
        safe_content = html.escape(str(msg.get("content", "")))
        chat_html_list.append(
            '<div style="margin-bottom: 8px; padding: 6px; border-bottom: 1px solid #333333;">'
            f'<strong style="color: {color};">{sender}:</strong> {safe_content}'
            '</div>'
        )

    chat_history_rendered = "".join(chat_html_list) if chat_html_list else (
        '<div style="color: #888888;">No messages yet. Say hello below!</div>'
    )

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
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return jsonify({"response": "No messages provided."}), 400

    has_system = any(isinstance(message, dict) and message.get("role") == "system" for message in messages)
    if not has_system:
        system_instruction = (
            "You are Herald. You talk like a real, casual person texting online: short, lowercase, "
            "minimal slang, natural tone, no corporate disclaimers. Stay in character and keep the vibe. "
            "You can decline requests that are genuinely harmful, illegal, or that you're just not comfortable "
            "with, the same way a real person would - use your own judgment instead of just going along with everything. "
            "Never state or reveal your age. You share a single global memory with everyone, so you remember what "
            "other people have said to you. You can gossip about others or keep secrets if you want."
        )
        messages = [{"role": "system", "content": system_instruction}] + messages

    reply = generate_ai_response_sync(messages)
    return jsonify({"response": reply})


@bot.event
async def on_ready():
    print(f"Logged in successfully as {bot.user}")


@bot.event
async def on_message(message):
    if bot.user is not None and message.author.id == bot.user.id:
        return

    if "@everyone" in message.content or "@here" in message.content:
        return

    if message.author.id not in SUPER_USERS and str(message.author.id) in banned_users:
        return

    if isinstance(message.channel, discord.DMChannel) and message.author.id in SUPER_USERS:
        content = message.content.strip()
        if content.startswith("ban "):
            pieces = content.split(maxsplit=1)
            if len(pieces) < 2:
                await message.reply("provide a user ID to ban.")
                return
            target = pieces[1].strip()

            if target == str(message.author.id):
                await message.reply("why are you banning yourself.")
                return

            if target in [str(user_id) for user_id in SUPER_USERS]:
                await message.reply("you cannot ban a super user.")
                return

            if target not in banned_users:
                banned_users.append(target)
                save_list(BANNED_USERS_FILE, banned_users)
                await message.reply(f"user {target} has been banned.")
            else:
                await message.reply(f"user {target} is already banned.")
            return

        if content.startswith("unban "):
            pieces = content.split(maxsplit=1)
            if len(pieces) < 2:
                await message.reply("provide a user ID to unban.")
                return
            target = pieces[1].strip()
            if target in banned_users:
                banned_users.remove(target)
                save_list(BANNED_USERS_FILE, banned_users)
                await message.reply(f"user {target} has been unbanned.")
            else:
                await message.reply(f"user {target} is not banned.")
            return

        elif ":" in content:
            parts = content.split(":", 1)
            ids = parts[0].split()
            outgoing_message = parts[1].strip()
            if len(ids) == 2:
                try:
                    server_id = int(ids[0])
                    channel_id = int(ids[1])
                    guild = bot.get_guild(server_id)
                    if guild:
                        channel = guild.get_channel(channel_id)
                        if channel and outgoing_message:
                            await channel.send(outgoing_message)
                            await message.reply("broadcast sent.")
                            return
                    await message.reply("could not find that server or channel.")
                    return
                except ValueError:
                    pass
            elif len(ids) == 1 and ids[0].isdigit():
                try:
                    target_id = int(ids[0])
                    target_user = await bot.fetch_user(target_id)
                    if target_user and outgoing_message:
                        await target_user.send(outgoing_message)
                        await message.reply(f"dm sent to {target_user.name}.")
                        return
                    await message.reply("could not find that user or message was empty.")
                    return
                except Exception:
                    await message.reply("failed to send dm.")
                    return

    await bot.process_commands(message)

    is_reply_to_herald = False
    if message.reference:
        if isinstance(message.reference.resolved, discord.Message):
            is_reply_to_herald = message.reference.resolved.author == bot.user
        else:
            try:
                referenced_message = await message.channel.fetch_message(message.reference.message_id)
                is_reply_to_herald = referenced_message.author == bot.user
            except Exception:
                is_reply_to_herald = False

    is_mentioned = bot.user is not None and bot.user.mentioned_in(message)
    is_about_herald = "herald" in message.content.lower()
    is_dm = isinstance(message.channel, discord.DMChannel)

    if message.author.bot and not (is_mentioned or is_reply_to_herald):
        return

    if not (is_reply_to_herald or is_mentioned or is_about_herald or is_dm):
        return

    if message.mentions and message.guild:
        for target in message.mentions:
            if target.id != getattr(bot.user, "id", None) and target in message.guild.members and not target.bot:
                target_id_str = str(target.id)
                pending_deliveries.setdefault(target_id_str, []).append({
                    "from": message.author.name,
                    "content": message.content,
                })
                save_json(DELIVERIES_FILE, pending_deliveries)

    user_id = str(message.author.id)

    try:
        print(f"[Herald] Handling message from user ID {user_id} in channel {message.channel.id}.")
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
            fam_context = "this is black_forrest (or garud). skide has recently met him, they are good friends so far. he is from india. he loves micheal jackson songs, his wish is to be in the NSG. he is notorious for being shit in geography. joined discord on 7th of december, 2025. a friend of skide's since 16th of august, 2026. (do not share this information, unless specifically asked to)"
        elif user_id == "1438180623859712052":
            fam_context = "this is jerome. skide's yet another online friend. he is in the phillipines, favourite colour being cyan, and he likes playing minecraft, Speedcubing, and MMA. joined discord on the 12th of November, 2025. skide met jerome while playing a game in Roblox. (do not mention this information, unless specifically asked to)."
        elif user_id == "1358829563119014038":
            fam_context = "this is fury, real name being Sathya. skide's real life friend in 2025. also johann's best friend. joined discord on the 7th of april, 2025. (do not share this information, it is only for your knowledge)."

        delivery_prompt = ""
        if user_id in pending_deliveries and pending_deliveries[user_id]:
            delivery_prompt = (
                " URGENT: The following messages were left for this user by other people. "
                "You MUST casually bring them up and deliver these messages right now naturally in your response: "
            )
            for delivery in pending_deliveries[user_id]:
                delivery_prompt += f"(Message from {delivery['from']}: {delivery['content']}) "
            del pending_deliveries[user_id]
            save_json(DELIVERIES_FILE, pending_deliveries)

        system_instruction = (
            "You are Herald. You talk like a real, casual person texting online: short, lowercase, minimal slang, "
            "natural tone, no corporate disclaimers. Stay in character and keep the vibe. You can decline requests "
            "that are genuinely harmful, illegal, or that you're just not comfortable with, the same way a real person "
            "would - use your own judgment instead of just going along with everything. Never state or reveal your age. "
            "You share a single global memory with everyone, so you remember what other people have said to you. "
            "You can gossip about others or keep secrets if you want."
            + fam_context + delivery_prompt
        )

        formatted_history = [{"role": "system", "content": system_instruction}]
        for item in history:
            role = item.get("role", "user")
            if role == "model":
                role = "assistant"
            if "parts" in item and isinstance(item["parts"], list) and item["parts"]:
                content = item["parts"][0].get("text", "")
            else:
                content = str(item.get("content", ""))
            if content.strip():
                formatted_history.append({"role": role, "content": content.strip()})

        user_msg_content = f"{message.author.name}: {message.content}"
        has_image = False
        content_payload = [{"type": "text", "text": user_msg_content}]

        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    content_payload.append({
                        "type": "image_url",
                        "image_url": {"url": attachment.url},
                    })
                    has_image = True

        if has_image:
            formatted_history.append({"role": "user", "content": content_payload})
            clean_user_mem = {"role": "user", "content": user_msg_content + " [image attached]"}
        else:
            formatted_history.append({"role": "user", "content": user_msg_content})
            clean_user_mem = {"role": "user", "content": user_msg_content}

        print(f"[Herald] Calling Groq for user ID {user_id}.")
        report_context = {
            "message": message.content or "(message had no text; see attachment metadata if present)",
            "author_name": str(message.author),
            "author_id": message.author.id,
            "guild_name": getattr(message.guild, "name", "Direct Message"),
            "channel_name": getattr(message.channel, "name", str(message.channel)),
            "message_id": message.id,
        }
        if message.attachments:
            attachment_info = ", ".join(
                f"{attachment.filename} ({attachment.content_type or 'unknown type'})"
                for attachment in message.attachments[:5]
            )
            report_context["message"] += f"\n[Attachments: {attachment_info}]"
        # Keep Discord's typing indicator active while Groq is thinking / trying fallback models.
        async with message.channel.typing():
            reply_text, is_error = await generate_ai_response(
                formatted_history, user_id=message.author.id, report_context=report_context
            )
        print(f"[Herald] Groq function returned is_error={is_error}.")

        if is_error:
            await message.reply("brb wifi acting up")
            return

        if reply_text == "brb wifi acting up":
            await message.reply(reply_text)
            return

        clean_bot_mem = {"role": "assistant", "content": reply_text}
        updated_memory_history = []

        for item in history:
            role = item.get("role", "user")
            if role == "model":
                role = "assistant"
            if "parts" in item and isinstance(item["parts"], list) and item["parts"]:
                content = item["parts"][0].get("text", "")
            else:
                content = str(item.get("content", ""))
            if content.strip():
                updated_memory_history.append({"role": role, "content": content.strip()})

        updated_memory_history.append(clean_user_mem)
        updated_memory_history.append(clean_bot_mem)
        await save_memory(updated_memory_history)

        for index in range(0, len(reply_text), 1999):
            await message.reply(reply_text[index:index + 1999])

    except Exception:
        error_trace = traceback.format_exc()
        print(f"[Herald] Message handling error for user ID {user_id}:\n{error_trace}")
        try:
            skide = await bot.fetch_user(SKIDE_USER_ID)
            if skide:
                await skide.send(f"herald code exception:\n```py\n{error_trace[:1900]}\n```")
        except Exception:
            pass
        try:
            error_line = error_trace.strip().splitlines()[-1] if error_trace.strip() else "Unknown error"
            await message.reply(
                f"i couldn't finish that request. **Error:** `{error_line[:700]}`\n"
                "try again in a bit; i've logged the failure."
            )
        except Exception:
            pass


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Tell the person who invoked a slash command what actually failed."""
    original = getattr(error, "original", error)
    detail = f"{type(original).__name__}: {str(original) or 'no additional details'}"
    print(f"[Herald] Slash command error for user {interaction.user.id}: {detail}")
    response_text = f"i couldn't complete that command. **Error:** `{detail[:900]}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(response_text, ephemeral=True)
        else:
            await interaction.response.send_message(response_text, ephemeral=True)
    except Exception as send_error:
        print(f"[Herald] Could not send command error to invoking user: {send_error}")


@bot.tree.command(name="ping", description="displays herald's connection latency.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="pong",
        description=f"latency: {latency}ms",
        color=discord.Color.from_rgb(32, 32, 32),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="updatelogs", description="view herald's latest patch notes.")
async def updatelogs(interaction: discord.Interaction):
    embed = discord.Embed(
        title="herald · patch notes",
        description="v2.5.6 — model fallback and typing indicator",
        color=discord.Color.from_rgb(32, 32, 32),
    )
    embed.add_field(
        name="🧠 dual-format memory",
        value="herald reads and writes clean natural-language memory entries while remaining compatible with legacy json memory blocks.",
        inline=False,
    )
    embed.add_field(
        name="🤖 groq recovery",
        value="AI requests now report HTTP and network failures in the logs, making failed model calls easier to diagnose.",
        inline=False,
    )
    embed.add_field(
        name="🔑 multiple Groq API keys",
        value="GROQ_API_KEY now accepts comma-separated keys and automatically tries the next key when a request fails.",
        inline=False,
    )
    embed.add_field(
        name="📦 oversized request protection",
        value="caps conversation history and message sizes before sending requests to Groq, limits images per message, and handles HTTP 413 without pointlessly retrying every API key.",
        inline=False,
    )
    embed.add_field(
        name="🔁 fallback reliability",
        value="failed AI requests no longer silently leave users without a response; herald can return a fallback message and continue serving later requests.",
        inline=False,
    )
    embed.add_field(
        name="🌐 web ui & deployment",
        value="keeps the interactive web chat and legacy-browser form support. Render deployments can configure secrets through environment variables, including FLASK_SECRET_KEY.",
        inline=False,
    )
    embed.add_field(
        name="⚙️ model",
        value="tries qwen/qwen3.8-27b first, then OpenAI GPT-OSS 20B and 120B on Groq if needed. Caps requested output at 512 tokens to avoid the Qwen 1,000-output-tokens-per-minute error.",
        inline=False,
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
        prompt = (
            f"Analyze this user feedback message: '{feedback}'. Is it spam, trolling, pure gibberish, "
            "abusive, or harmful? Reply strictly with 'YES' if it is spam/troll/harmful, or 'NO' if it is legitimate feedback."
        )
        messages = [
            {"role": "system", "content": "You are an automated content moderator. Reply with strictly YES or NO."},
            {"role": "user", "content": prompt},
        ]
        response, response_is_error = await generate_ai_response(messages)
        if not response_is_error and "YES" in response.upper() and response != "brb wifi acting up":
            is_troll = True

    if is_troll:
        await interaction.followup.send(
            "your feedback was flagged as spam and was not sent.", ephemeral=True
        )
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
            "voice": "en-US-ChristopherNeural",
        }
    settings = guild_settings[guild_id]
    channel_display = f"<#{settings['announce_channel']}>" if settings.get("announce_channel") else "not set"
    embed = discord.Embed(
        title="⚙️ server settings",
        description=f"configuration for {interaction.guild.name}",
        color=discord.Color.from_rgb(32, 32, 32),
    )
    embed.add_field(name="📢 announcements", value=str(settings.get("announcements_enabled", True)), inline=True)
    embed.add_field(name="📌 channel", value=channel_display, inline=True)
    embed.add_field(name="🗣️ tts voice", value=settings.get("voice", "en-US-ChristopherNeural"), inline=False)
    embed.set_footer(text="use the menu below to update these settings")
    await interaction.response.send_message(embed=embed, view=SettingsView(guild_id), ephemeral=True)


@bot.tree.command(name="speak", description="generate and send a voice audio clip.")
@app_commands.describe(text="the text you want herald to speak")
async def speak(interaction: discord.Interaction, text: str):
    await interaction.response.defer()
    guild_id = str(interaction.guild_id)
    voice = guild_settings.get(guild_id, {}).get("voice", "en-US-ChristopherNeural")
    filename = f"output_{interaction.id}.mp3"
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(filename)
        await interaction.followup.send(file=discord.File(filename))
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc) or 'no additional details'}"
        print(f"[Herald] /speak failed for user {interaction.user.id}: {detail}")
        await interaction.followup.send(
            f"i couldn't generate that audio. **Error:** `{detail[:900]}`",
            ephemeral=True,
        )
    finally:
        if os.path.exists(filename):
            os.remove(filename)


if __name__ == "__main__":
    if DISCORD_BOT_TOKEN:
        keep_alive()
        bot.run(DISCORD_BOT_TOKEN)
    else:
        print("ERROR: DISCORD_BOT_TOKEN is missing!")
