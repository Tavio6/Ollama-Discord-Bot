import discord
from discord.ext import commands
import aiohttp
import asyncio
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.txt")

if not os.path.isfile(TOKEN_FILE):
    raise FileNotFoundError(f"token.txt not found at: {TOKEN_FILE}")

with open(TOKEN_FILE, "r") as f:
    DISCORD_TOKEN = f.read().strip()

OLLAMA_URL = "http://localhost:11434/api/generate"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

processing_lock = asyncio.Lock()

MAX_CONTEXT_CHARS = 2048
chat_history = {}

def update_context(channel_id, role, content):
    if channel_id not in chat_history:
        chat_history[channel_id] = ""

    new_entry = f"{role.upper()}: {content}\n"
    chat_history[channel_id] += new_entry

    if len(chat_history[channel_id]) > MAX_CONTEXT_CHARS:
        overflow = len(chat_history[channel_id]) - MAX_CONTEXT_CHARS
        chat_history[channel_id] = chat_history[channel_id][overflow:]

def build_prompt(channel_id, user_input):
    memory = chat_history.get(channel_id, "")

    system_instructions = (
        "You are a helpful assistant. "
        "Follow instructions carefully, stay consistent with prior context, "
        "and respond clearly.\n\n"
    )

    return (
        f"{system_instructions}"
        f"--- CONTEXT (previous conversation) ---\n"
        f"{memory}\n"
        f"--- NEW USER INPUT ---\n"
        f"USER: {user_input}\n"
        f"ASSISTANT:"
    )

def chunk_message(text, limit=2000):
    return [text[i:i+limit] for i in range(0, len(text), limit)]

@bot.event
async def on_ready():
    print(f"[DEBUG] Bot is online as {bot.user}")

@bot.command()
async def message(ctx, model: str, *, user_input: str):
    if processing_lock.locked():
        await ctx.send("⏳ Busy with another request, try again after the current one finishes.")
        print("[DEBUG] Ignored input because bot is busy.")
        return

    async with processing_lock:
        print(f"[DEBUG] Received command from {ctx.author}: model={model}, input={user_input}")

        channel_id = ctx.channel.id
        update_context(channel_id, "user", user_input)

        final_prompt = build_prompt(channel_id, user_input)

        await ctx.send(f"Sending to Ollama model `{model}`...")

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    OLLAMA_URL,
                    json={"model": model, "prompt": final_prompt}
                ) as resp:

                    print(f"[DEBUG] Ollama POST status: {resp.status}")

                    if resp.status == 200:
                        full_reply = ""

                        async for line in resp.content:
                            data = line.decode("utf-8").strip()
                            if not data:
                                continue

                            try:
                                obj = json.loads(data)
                                chunk = obj.get("response", "")
                                full_reply += chunk
                            except Exception as e:
                                print(f"[DEBUG] Failed to parse line: {e}")

                        if full_reply:
                            update_context(channel_id, "assistant", full_reply)

                            for part in chunk_message(full_reply):
                                await ctx.send(part)

                            print("[DEBUG] Reply sent to Discord successfully.")
                        else:
                            await ctx.send("⚠️ No response from Ollama.")
                            print("[DEBUG] Ollama returned empty stream.")

                    else:
                        await ctx.send(f"Error: Ollama returned status {resp.status}")
                        print(f"[DEBUG] Ollama error status: {resp.status}")

            except Exception as e:
                await ctx.send(f"⚠️ Failed to reach Ollama: {e}")
                print(f"[DEBUG] Exception contacting Ollama: {e}")

@bot.command()
async def reset(ctx):
    channel_id = ctx.channel.id
    chat_history[channel_id] = ""
    await ctx.send("🧹 Context reset for this channel.")
    print(f"[DEBUG] Context reset for channel {channel_id}")

bot.run(DISCORD_TOKEN)
