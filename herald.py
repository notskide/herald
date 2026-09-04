import os
import re
import asyncio
import requests
import aiohttp
from flask import Flask, render_template, request, jsonify
import discord
from discord.ext import commands

app = Flask(__name__)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

FALLBACK_MODELS = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "qwen-2.5-32b",
    "qwen/qwen3-32b",
    "mixtral-8x7b-32768"
]

def clean_think_tags(text):
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()

def generate_ai_response_sync(messages):
    api_key = os.getenv("GROQ_API_KEY")
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
            r = requests.post(
                url, 
                headers=headers, 
                json={"model": model_name, "messages": sanitized}, 
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                if "choices" in data and len(data["choices"]) > 0:
                    reply = clean_think_tags(data["choices"][0]["message"]["content"])
                    if reply:
                        return reply
        except Exception:
            continue
    return "api error."

async def generate_ai_response(messages):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "groq api key is missing."
        
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }
    
    sanitized_messages = []
    for msg in messages:
        role = "assistant" if msg.get("role") == "model" else msg.get("role", "user")
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

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/chat", methods=["POST"])
def chat_api():
    data = request.get_json() or {}
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"response": "No messages provided."}), 400
    response = generate_ai_response_sync(messages)
    return jsonify({"response": response})

@bot.event
async def on_ready():
    print(f"Herald v2.26 logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if bot.user in message.mentions or isinstance(message.channel, discord.DMChannel):
        async with message.channel.typing():
            prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
            history = [{"role": "user", "content": prompt}]
            reply = await generate_ai_response(history)
            await message.reply(reply)

    await bot.process_commands(message)

def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

async def main():
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, run_flask)
    if DISCORD_TOKEN:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
