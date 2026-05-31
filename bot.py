import discord
import asyncio
import itertools
import string
import aiohttp
from discord.ext import commands
from discord import app_commands

import os
DISCORD_BOT_TOKEN = "DISCORD_BOT_TOKEN"

CONCURRENT_REQUESTS = 20

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

active_checks = {}
watchlist: dict[int, list[str]] = {}

WORD_LIST_URL = "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"

# ─── GENERATORS ─────────────────────────────────────────────────────────────

def generate_semis(length: int):
    chars = string.ascii_lowercase + string.digits
    for combo in itertools.product(chars, repeat=length):
        username = "".join(combo)
        if any(c.isalpha() for c in username) and any(c.isdigit() for c in username):
            yield username

def generate_usernames(length, mixed=False):
    chars = string.ascii_lowercase + string.digits
    for combo in itertools.product(chars, repeat=length):
        username = "".join(combo)
        if mixed:
            if any(c.isalpha() for c in username) and any(c.isdigit() for c in username):
                yield username
        else:
            if username.isalpha():
                yield username

# ─── HELPERS ─────────────────────────────────────────────────────────────────

async def fetch_word_list():
    async with aiohttp.ClientSession() as session:
        async with session.get(WORD_LIST_URL) as response:
            text = await response.text()
            return [w.strip().lower() for w in text.splitlines() if w.strip()]

async def check_username(session, username):
    url = f"https://feds.lol/{username}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
            return username, "available" if response.status == 404 else "taken"
    except Exception:
        return username, "error"

async def send_available_chunk(interaction, chunk, label="Available"):
    formatted = "\n".join(f"• {u}" for u in chunk)
    await interaction.followup.send(f"✅ **{label} ({len(chunk)}):**\n{formatted}")

# ─── MAIN CHECK RUNNER ───────────────────────────────────────────────────────

async def run_checks(interaction, generator, label):
    user_id = interaction.user.id
    user_mention = interaction.user.mention

    cancel_event = asyncio.Event()
    active_checks[user_id] = cancel_event

    await interaction.response.send_message(
        f"Starting check for **{label}** usernames. {user_mention} I'll ping you when done! Use `/cancel` to stop."
    )

    found = 0
    checked = 0
    available_batch = []
    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

    async with aiohttp.ClientSession() as session:
        tasks = []

        async def check_with_semaphore(username):
            async with semaphore:
                return await check_username(session, username)

        for username in generator:
            if cancel_event.is_set():
                break

            tasks.append(asyncio.create_task(check_with_semaphore(username)))

            if len(tasks) >= 500:
                results = await asyncio.gather(*tasks)
                tasks = []

                for uname, result in results:
                    checked += 1
                    if result == "available":
                        found += 1
                        available_batch.append(uname)

                while len(available_batch) >= 50:
                    chunk = available_batch[:50]
                    available_batch = available_batch[50:]
                    await send_available_chunk(interaction, chunk)

                if cancel_event.is_set():
                    if available_batch:
                        await send_available_chunk(interaction, available_batch)
                    await interaction.followup.send(
                        f"{user_mention} ⛔ Check cancelled! Checked **{checked}** usernames, found **{found}** available before stopping."
                    )
                    del active_checks[user_id]
                    return

                await interaction.followup.send(f"📊 Progress: **{checked}** checked, **{found}** available so far...")

        if tasks and not cancel_event.is_set():
            results = await asyncio.gather(*tasks)
            for uname, result in results:
                checked += 1
                if result == "available":
                    found += 1
                    available_batch.append(uname)

        while available_batch:
            chunk = available_batch[:50]
            available_batch = available_batch[50:]
            await send_available_chunk(interaction, chunk)

    if user_id in active_checks:
        del active_checks[user_id]

    if cancel_event.is_set():
        await interaction.followup.send(
            f"{user_mention} ⛔ Cancelled! Checked **{checked}**, found **{found}** available."
        )
    else:
        await interaction.followup.send(
            f"{user_mention} ✅ Done! Checked **{checked}** usernames. Found **{found}** available."
        )

# ─── ON READY ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")
    print("Slash commands synced globally!")
    bot.loop.create_task(watchlist_monitor())

# ─── HELP ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="help", description="Show all available commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 feds.lol Username Checker",
        description="Check username availability on feds.lol across multiple modes.",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="/checkletters `length`",
        value="Check all letter-only usernames of a given length (1–4).\nExample: `/checkletters 3`",
        inline=False
    )
    embed.add_field(
        name="/checkmixed `length`",
        value="Check usernames with both letters AND numbers (1–4 chars).\nExample: `/checkmixed 3`",
        inline=False
    )
    embed.add_field(
        name="/checksemis `length`",
        value="Check semi-OG usernames (mixed letters+numbers, 3–5 chars).\nExample: `/checksemis 4`",
        inline=False
    )
    embed.add_field(
        name="/checkfinals `maxlength`",
        value="Check real English dictionary words as usernames (default max 6 letters).\nExample: `/checkfinals 5`",
        inline=False
    )
    embed.add_field(
        name="/checkfile `file`",
        value="Upload a `.txt` file with one username per line.\nExample: Attach `usernames.txt` and run `/checkfile`",
        inline=False
    )
    embed.add_field(
        name="/watchlist add `usernames`",
        value="Add usernames to your watchlist (comma-separated). Bot will DM you when available.\nExample: `/watchlist add fire,void,x9`",
        inline=False
    )
    embed.add_field(
        name="/watchlist remove `usernames`",
        value="Remove usernames from your watchlist.\nExample: `/watchlist remove fire,void`",
        inline=False
    )
    embed.add_field(
        name="/watchlist view",
        value="View all usernames currently on your watchlist.",
        inline=False
    )
    embed.add_field(
        name="/cancel",
        value="Stop your currently running username check.",
        inline=False
    )
    embed.set_footer(text="Bot by @shenji2x")
    await interaction.response.send_message(embed=embed)

# ─── CANCEL ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="cancel", description="Cancel your active username check")
async def cancel(interaction: discord.Interaction):
    if interaction.user.id not in active_checks:
        await interaction.response.send_message("You don't have an active check running.")
        return
    active_checks[interaction.user.id].set()
    await interaction.response.send_message("⛔ Cancelling your check...")

# ─── CHECK LETTERS ───────────────────────────────────────────────────────────

@bot.tree.command(name="checkletters", description="Check all usernames of a certain length (letters only)")
@app_commands.describe(length="Length of usernames to check (1-4)")
async def checkletters(interaction: discord.Interaction, length: int):
    if length < 1 or length > 4:
        await interaction.response.send_message("Please choose a length between 1 and 4.")
        return
    if interaction.user.id in active_checks:
        await interaction.response.send_message("You already have an active check running! Use `/cancel` to stop it first.")
        return
    await run_checks(interaction, generate_usernames(length), f"{length}-letter")

# ─── CHECK MIXED ─────────────────────────────────────────────────────────────

@bot.tree.command(name="checkmixed", description="Check all usernames of a certain length (letters AND numbers)")
@app_commands.describe(length="Length of usernames to check (1-4)")
async def checkmixed(interaction: discord.Interaction, length: int):
    if length < 1 or length > 4:
        await interaction.response.send_message("Please choose a length between 1 and 4.")
        return
    if interaction.user.id in active_checks:
        await interaction.response.send_message("You already have an active check running! Use `/cancel` to stop it first.")
        return
    await run_checks(interaction, generate_usernames(length, mixed=True), f"{length}-character mixed")

# ─── CHECK SEMIS ─────────────────────────────────────────────────────────────

@bot.tree.command(name="checksemis", description="Check semi-OG usernames (mixed letters+numbers, 3-5 chars)")
@app_commands.describe(length="Length of usernames to check (3-5)")
async def checksemis(interaction: discord.Interaction, length: int):
    if length < 3 or length > 5:
        await interaction.response.send_message("Please choose a length between 3 and 5 for semis.")
        return
    if interaction.user.id in active_checks:
        await interaction.response.send_message("You already have an active check running! Use `/cancel` to stop it first.")
        return
    await run_checks(interaction, generate_semis(length), f"{length}-char semi-OG")

# ─── CHECK FINALS ────────────────────────────────────────────────────────────

@bot.tree.command(name="checkfinals", description="Check real English dictionary words (final/OG usernames)")
@app_commands.describe(maxlength="Only check words up to this length (default 6, max 10)")
async def checkfinals(interaction: discord.Interaction, maxlength: int = 6):
    if maxlength < 1 or maxlength > 10:
        await interaction.response.send_message("Please choose a max length between 1 and 10.")
        return
    if interaction.user.id in active_checks:
        await interaction.response.send_message("You already have an active check running! Use `/cancel` to stop it first.")
        return

    await interaction.response.send_message(
        f"Fetching word list and checking finals up to **{maxlength}** letters... This may take a while!"
    )

    words = await fetch_word_list()
    filtered = [w for w in words if len(w) <= maxlength and w.isalpha()]
    await interaction.followup.send(f"Found **{len(filtered)}** dictionary words to check. Starting now...")

    cancel_event = asyncio.Event()
    active_checks[interaction.user.id] = cancel_event

    user_mention = interaction.user.mention
    found = 0
    checked = 0
    available_batch = []
    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

    async with aiohttp.ClientSession() as session:
        tasks = []

        async def check_with_semaphore(username):
            async with semaphore:
                return await check_username(session, username)

        for word in filtered:
            if cancel_event.is_set():
                break
            tasks.append(asyncio.create_task(check_with_semaphore(word)))

            if len(tasks) >= 500:
                results = await asyncio.gather(*tasks)
                tasks = []

                for uname, result in results:
                    checked += 1
                    if result == "available":
                        found += 1
                        available_batch.append(uname)

                while len(available_batch) >= 50:
                    chunk = available_batch[:50]
                    available_batch = available_batch[50:]
                    await send_available_chunk(interaction, chunk, label="Finals available")

                if cancel_event.is_set():
                    if available_batch:
                        await send_available_chunk(interaction, available_batch, label="Finals available")
                    await interaction.followup.send(
                        f"{user_mention} ⛔ Cancelled! Checked **{checked}**, found **{found}** available."
                    )
                    del active_checks[interaction.user.id]
                    return

                await interaction.followup.send(f"📊 Progress: **{checked}** checked, **{found}** available so far...")

        if tasks and not cancel_event.is_set():
            results = await asyncio.gather(*tasks)
            for uname, result in results:
                checked += 1
                if result == "available":
                    found += 1
                    available_batch.append(uname)

        while available_batch:
            chunk = available_batch[:50]
            available_batch = available_batch[50:]
            await send_available_chunk(interaction, chunk, label="Finals available")

    if interaction.user.id in active_checks:
        del active_checks[interaction.user.id]

    await interaction.followup.send(
        f"{user_mention} ✅ Done! Checked **{checked}** words. Found **{found}** available finals."
    )

# ─── CHECK FILE ──────────────────────────────────────────────────────────────

@bot.tree.command(name="checkfile", description="Check usernames from an attached .txt file")
async def checkfile(interaction: discord.Interaction, file: discord.Attachment):
    if interaction.user.id in active_checks:
        await interaction.response.send_message("You already have an active check running! Use `/cancel` to stop it first.")
        return
    content = await file.read()
    usernames = [u.strip() for u in content.decode("utf-8").splitlines() if u.strip()]
    await run_checks(interaction, iter(usernames), f"{len(usernames)}-username file")

# ─── WATCHLIST ───────────────────────────────────────────────────────────────

watchlist_group = app_commands.Group(name="watchlist", description="Manage your username watchlist")

@watchlist_group.command(name="add", description="Add usernames to your watchlist (comma-separated)")
@app_commands.describe(usernames="Usernames to watch, separated by commas. Example: fire,void,x9")
async def watchlist_add(interaction: discord.Interaction, usernames: str):
    user_id = interaction.user.id
    new_names = [u.strip().lower() for u in usernames.split(",") if u.strip()]

    invalid = [u for u in new_names if not u.isalnum()]
    if invalid:
        await interaction.response.send_message(
            f"⚠️ Invalid characters detected (letters and numbers only): {', '.join(f'`{u}`' for u in invalid)}"
        )
        return

    if user_id not in watchlist:
        watchlist[user_id] = []

    added = []
    already = []
    for name in new_names:
        if name not in watchlist[user_id]:
            watchlist[user_id].append(name)
            added.append(name)
        else:
            already.append(name)

    lines = []
    if added:
        lines.append(f"✅ Added to watchlist: {', '.join(f'`{u}`' for u in added)}")
    if already:
        lines.append(f"ℹ️ Already watching: {', '.join(f'`{u}`' for u in already)}")

    await interaction.response.send_message("\n".join(lines) or "Nothing to add.")

@watchlist_group.command(name="remove", description="Remove usernames from your watchlist")
@app_commands.describe(usernames="Usernames to remove, separated by commas. Example: fire,void")
async def watchlist_remove(interaction: discord.Interaction, usernames: str):
    user_id = interaction.user.id
    to_remove = [u.strip().lower() for u in usernames.split(",") if u.strip()]

    if user_id not in watchlist or not watchlist[user_id]:
        await interaction.response.send_message("Your watchlist is empty.")
        return

    removed = []
    not_found = []
    for name in to_remove:
        if name in watchlist[user_id]:
            watchlist[user_id].remove(name)
            removed.append(name)
        else:
            not_found.append(name)

    lines = []
    if removed:
        lines.append(f"🗑️ Removed: {', '.join(f'`{u}`' for u in removed)}")
    if not_found:
        lines.append(f"⚠️ Not in your watchlist: {', '.join(f'`{u}`' for u in not_found)}")

    await interaction.response.send_message("\n".join(lines))

@watchlist_group.command(name="view", description="View all usernames on your watchlist")
async def watchlist_view(interaction: discord.Interaction):
    user_id = interaction.user.id
    if user_id not in watchlist or not watchlist[user_id]:
        await interaction.response.send_message("Your watchlist is empty. Use `/watchlist add` to add usernames.")
        return

    names = "\n".join(f"• {u}" for u in watchlist[user_id])
    await interaction.response.send_message(f"👀 **Your watchlist ({len(watchlist[user_id])}):**\n{names}")

bot.tree.add_command(watchlist_group)

# ─── WATCHLIST MONITOR ───────────────────────────────────────────────────────

async def watchlist_monitor():
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            for user_id, names in list(watchlist.items()):
                if not names:
                    continue
                for username in list(names):
                    _, result = await check_username(session, username)
                    if result == "available":
                        try:
                            user = await bot.fetch_user(user_id)
                            await user.send(
                                f"🔔 **Watchlist Alert!**\n"
                                f"The username `{username}` on feds.lol is now **available** to claim!\n"
                                f"➡️ https://feds.lol/{username}"
                            )
                            watchlist[user_id].remove(username)
                        except Exception:
                            pass
                    await asyncio.sleep(0.5)
            await asyncio.sleep(300)

# ─── RUN ─────────────────────────────────────────────────────────────────────

bot.run(DISCORD_BOT_TOKEN)