import asyncio
import json
import math
import os
from datetime import datetime, timezone
from io import BytesIO

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ====================== CONFIG ======================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
UNB_API_TOKEN = os.environ.get("UNB_API_TOKEN")
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
GRAPH_CHANNEL_ID = int(os.environ.get("GRAPH_CHANNEL_ID", "0"))
DATA_FILE = os.environ.get("DATA_FILE", "mmm_data.json")
UNB_BASE = "https://unbelievaboat.com/api/v1"
# ====================================================

print("Discord token configured:", bool(DISCORD_TOKEN))
print("UNB token configured:", bool(UNB_API_TOKEN))
print("Guild ID:", GUILD_ID)
print("Graph Channel ID:", GRAPH_CHANNEL_ID)
print("Data file:", DATA_FILE)

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN in Railway Variables")
if not UNB_API_TOKEN:
    raise RuntimeError("Missing UNB_API_TOKEN in Railway Variables")
if GUILD_ID <= 0:
    raise RuntimeError("Missing or invalid GUILD_ID in Railway Variables")
if GRAPH_CHANNEL_ID <= 0:
    raise RuntimeError("Missing or invalid GRAPH_CHANNEL_ID in Railway Variables")


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "price": 1600.0,
        "history": [{"price": 1600.0, "time": datetime.now(timezone.utc).isoformat()}],
        "investors": {},
        "total_invested": 0.0,
        "graph_message_id": None,
    }


def save_data(data_to_save):
    directory = os.path.dirname(os.path.abspath(DATA_FILE))
    os.makedirs(directory, exist_ok=True)
    temporary_file = DATA_FILE + ".tmp"
    with open(temporary_file, "w", encoding="utf-8") as f:
        json.dump(data_to_save, f, indent=2)
    os.replace(temporary_file, DATA_FILE)


data = load_data()
trade_lock = asyncio.Lock()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook():
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to guild {GUILD_ID}")
        for command in synced:
            print(f"Registered: /{command.name}")
    except Exception as e:
        print(f"Command sync failed: {type(e).__name__}: {e}")
        raise


@bot.event
async def on_ready():
    print("=== ON_READY FIRED ===")
    print(f"Logged in as {bot.user}")


async def get_unb_balance(user_id: int):
    headers = {"Authorization": UNB_API_TOKEN}
    url = f"{UNB_BASE}/guilds/{GUILD_ID}/users/{user_id}"
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"UNB GET error {resp.status}: {await resp.text()}")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"UNB GET connection error: {type(e).__name__}: {e}")
        return None


async def edit_unb_balance(user_id: int, cash_change: int, reason: str = "MMM"):
    headers = {
        "Authorization": UNB_API_TOKEN,
        "Content-Type": "application/json"
    }
    url = f"{UNB_BASE}/guilds/{GUILD_ID}/users/{user_id}"
    payload = {"cash": cash_change, "reason": reason}

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.patch(url, headers=headers, json=payload) as resp:
                response_text = await resp.text()
                print(f"UNB PATCH status: {resp.status}", flush=True)
                if resp.status == 200:
                    return True
                print(f"UNB PATCH failed: {response_text}", flush=True)
                return False
    except Exception as e:
        print(f"UNB PATCH exception: {type(e).__name__}: {e}", flush=True)
        return False


def generate_graph():
    prices = [entry["price"] for entry in data["history"]]
    times = list(range(len(prices)))

    fig, ax = plt.subplots(figsize=(16, 9), facecolor="#0a0e14")
    ax.set_facecolor("#0a0e14")
    ax.plot(times, prices, color="#00ff9f", linewidth=4, marker="o", markersize=7)
    ax.fill_between(times, prices, alpha=0.20, color="#00ff9f")
    ax.set_title("MMM STOCK MARKET", color="#00ff9f", fontsize=26,
                 fontweight="bold", pad=25)
    ax.set_xlabel("Transactions", color="#aaaaaa", fontsize=16)
    ax.set_ylabel("Share Price", color="#aaaaaa", fontsize=16)
    ax.tick_params(colors="#aaaaaa", labelsize=13)
    ax.grid(True, color="#1a3a4a", linestyle="--", alpha=0.5)
    for spine in ax.spines.values():
        spine.set_color("#1a3a4a")
    if prices:
        ax.annotate(f"{prices[-1]:,.2f}", xy=(times[-1], prices[-1]),
                    xytext=(10, 15), textcoords="offset points",
                    color="#00ff9f", fontsize=17, fontweight="bold")
    ax.margins(x=0.07, y=0.20)
    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=150, facecolor=fig.get_facecolor())
    buffer.seek(0)
    plt.close(fig)
    return buffer


async def get_graph_channel():
    channel = bot.get_channel(GRAPH_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(GRAPH_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("GRAPH_CHANNEL_ID must refer to a text channel")
    return channel


async def update_live_graph(channel: discord.TextChannel):
    graph = generate_graph()
    embed = discord.Embed(
        title="MMM Share Price",
        description=(
            f"**Current Price:** {data['price']:,.0f}\n"
            f"**Total Invested:** {data['total_invested']:,.0f}"
        ),
        color=0x00FF9F,
    )
    embed.set_image(url="attachment://mmm_graph.png")
    embed.set_footer(text="Updates when people invest or sell")

    file = discord.File(graph, filename="mmm_graph.png")
    try:
        if data.get("graph_message_id"):
            try:
                message = await channel.fetch_message(data["graph_message_id"])
                await message.edit(embed=embed, attachments=[file])
                return
            except discord.NotFound:
                data["graph_message_id"] = None
        message = await channel.send(embed=embed, file=file)
        data["graph_message_id"] = message.id
        save_data(data)
    finally:
        file.close()


async def refresh_graph_safely():
    try:
        channel = await get_graph_channel()
        await update_live_graph(channel)
    except Exception as e:
        print(f"Graph update failed: {type(e).__name__}: {e}")


@bot.tree.command(name="mmm_invest", description="Invest UnbelievaBoat cash into MMM")
@app_commands.describe(amount="Amount of cash to invest")
async def mmm_invest(interaction: discord.Interaction, amount: int):
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    async with trade_lock:
        balance = await get_unb_balance(interaction.user.id)
        if balance is None:
            await interaction.followup.send("Unable to check UnbelievaBoat balance. Check Railway logs.", ephemeral=True)
            return
        if balance.get("cash", 0) < amount:
            await interaction.followup.send("You don't have enough cash.", ephemeral=True)
            return

        success = await edit_unb_balance(interaction.user.id, -amount, "Invested in MMM")
        if not success:
            await interaction.followup.send("Failed to take money from UnbelievaBoat.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        shares = amount / data["price"]
        data["investors"][user_id] = data["investors"].get(user_id, 0) + shares
        data["total_invested"] += amount
        growth = 1 + (amount / max(data["total_invested"], 1000)) * 0.12
        data["price"] *= growth
        data["history"].append({"price": data["price"], "time": datetime.now(timezone.utc).isoformat()})
        save_data(data)

        await interaction.followup.send(
            f"Invested **{amount:,}** → bought **{shares:.4f}** shares.\n"
            f"New price: **{data['price']:,.0f}**",
            ephemeral=True,
        )
        await refresh_graph_safely()


@bot.tree.command(name="mmm_sell", description="Sell MMM shares for UnbelievaBoat cash")
@app_commands.describe(shares="Shares to sell, or 'all'")
async def mmm_sell(interaction: discord.Interaction, shares: str):
    await interaction.response.defer(ephemeral=True)
    async with trade_lock:
        user_id = str(interaction.user.id)
        owned = data["investors"].get(user_id, 0)
        try:
            to_sell = owned if shares.strip().lower() == "all" else float(shares)
        except ValueError:
            await interaction.followup.send("Invalid share amount.", ephemeral=True)
            return

        if not math.isfinite(to_sell) or to_sell <= 0 or to_sell > owned:
            await interaction.followup.send("You don't own that many shares.", ephemeral=True)
            return

        payout = int(to_sell * data["price"])
        if payout <= 0:
            await interaction.followup.send("Sale amount is too small to pay out.", ephemeral=True)
            return

        success = await edit_unb_balance(interaction.user.id, payout, "Sold MMM shares")
        if not success:
            await interaction.followup.send("Failed to give money via UnbelievaBoat.", ephemeral=True)
            return

        data["investors"][user_id] -= to_sell
        if data["investors"][user_id] <= 1e-12:
            del data["investors"][user_id]
        data["price"] *= 0.985
        data["history"].append({"price": data["price"], "time": datetime.now(timezone.utc).isoformat()})
        save_data(data)

        await interaction.followup.send(
            f"Sold **{to_sell:.4f}** shares for **{payout:,}** cash.\n"
            f"New price: **{data['price']:,.0f}**",
            ephemeral=True,
        )
        await refresh_graph_safely()


@bot.tree.command(name="mmm_setup", description="Create or refresh the live graph")
async def mmm_setup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        async with trade_lock:
            channel = await get_graph_channel()
            await update_live_graph(channel)
        await interaction.followup.send("Live graph updated.", ephemeral=True)
    except Exception as e:
        print(f"Graph setup failed: {type(e).__name__}: {e}")
        await interaction.followup.send("Failed to update graph. Check Railway logs.", ephemeral=True)


@bot.tree.command(name="mmm_portfolio", description="See your shares")
async def mmm_portfolio(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    owned = data["investors"].get(user_id, 0)
    value = owned * data["price"]
    await interaction.response.send_message(
        f"You own **{owned:.4f}** shares worth **{value:,.0f}** at the current price.",
        ephemeral=True,
    )


@bot.tree.command(name="mmm_crash", description="Admin force the pyramid to collapse")
async def mmm_crash(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need Administrator permission.", ephemeral=True)
        return

    await interaction.response.defer()
    async with trade_lock:
        data["price"] = max(1, data["price"] * 0.05)
        data["history"].append({"price": data["price"], "time": datetime.now(timezone.utc).isoformat()})
        save_data(data)
        await interaction.followup.send(
            f"**The pyramid has collapsed.**\nPrice crashed to **{data['price']:,.0f}**."
        )
        await refresh_graph_safely()


@bot.tree.command(name="mmm_debug", description="Debug UnbelievaBoat connection")
async def mmm_debug(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    url = f"{UNB_BASE}/guilds/{GUILD_ID}/users/{interaction.user.id}"
    headers = {"Authorization": UNB_API_TOKEN}

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                status = resp.status
                response_text = await resp.text()

        await interaction.followup.send(
            f"**MMM API Debug**\n"
            f"API Token Configured: {bool(UNB_API_TOKEN)}\n"
            f"Guild ID: `{GUILD_ID}`\n"
            f"HTTP Status: `{status}`\n"
            f"Response: ```{response_text[:1000]}```",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"API Error: `{type(e).__name__}: {e}`",
            ephemeral=True
        )


bot.run(DISCORD_TOKEN)
