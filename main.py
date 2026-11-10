import discord
from discord import app_commands
from discord.ext import commands
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import json
import os
import aiohttp
from io import BytesIO
from datetime import datetime

# ====================== CONFIG ======================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
UNB_API_TOKEN = os.environ.get("UNB_API_TOKEN")
GUILD_ID = int(os.environ.get("GUILD_ID", 0))
GRAPH_CHANNEL_ID = int(os.environ.get("GRAPH_CHANNEL_ID", 0))
DATA_FILE = "mmm_data.json"
# =====================================================

print("Discord token configured:", bool(DISCORD_TOKEN))
print("UNB token configured:", bool(UNB_API_TOKEN))
print("Guild ID:", GUILD_ID)
print("Graph Channel ID:", GRAPH_CHANNEL_ID)

UNB_BASE = "https://unbelievaboat.com/api/v1"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {
        "price": 1600.0,
        "history": [{"price": 1600.0, "time": datetime.now().isoformat()}],
        "investors": {},
        "total_invested": 0.0,
        "graph_message_id": None
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

data = load_data()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

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
        print(f"UNB connection error: {e}")
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
                if resp.status != 200:
                    print(f"UNB PATCH error {resp.status}: {await resp.text()}")
                    return False
                return True
    except Exception as e:
        print(f"UNB connection error: {e}")
        return False

def generate_graph():
    prices = [h["price"] for h in data["history"]]
    times = list(range(len(prices)))

    fig, ax = plt.subplots(figsize=(14, 7), facecolor="#0a0e14")
    ax.set_facecolor("#0a0e14")
    ax.plot(times, prices, color="#00ff9f", linewidth=3, marker="o", markersize=4)
    ax.fill_between(times, prices, alpha=0.15, color="#00ff9f")
    ax.set_title("MMM Share Price (Live)", color="#00ff9f", fontsize=18, fontweight="bold")
    ax.set_xlabel("Transactions", color="#aaaaaa", fontsize=12)
    ax.set_ylabel("Price", color="#aaaaaa", fontsize=12)
    ax.tick_params(colors="#888888", labelsize=10)
    ax.grid(True, color="#1a3a4a", linestyle="--", alpha=0.6)
    for spine in ax.spines.values():
        spine.set_color("#1a3a4a")

    if prices:
        ax.annotate(f"{prices[-1]:,.0f}", xy=(times[-1], prices[-1]),
                    xytext=(10, 10), textcoords="offset points",
                    color="#00ff9f", fontsize=13, fontweight="bold")

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=120, facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close()
    return buf

async def update_live_graph(channel: discord.TextChannel):
    graph = generate_graph()
    file = discord.File(graph, filename="mmm_graph.png")
    embed = discord.Embed(
        title="MMM Share Price",
        description=f"**Current Price:** {data['price']:,.0f}\n**Total Invested:** {data['total_invested']:,.0f}",
        color=0x00ff9f
    )
    embed.set_image(url="attachment://mmm_graph.png")
    embed.set_footer(text="Updates when people invest or sell")

    if data.get("graph_message_id"):
        try:
            msg = await channel.fetch_message(data["graph_message_id"])
            await msg.edit(embed=embed, attachments=[file])
            return
        except discord.NotFound:
            pass

    msg = await channel.send(embed=embed, file=file)
    data["graph_message_id"] = msg.id
    save_data(data)

@bot.event
async def on_ready():
    print("=== ON_READY FIRED ===")
    print(f"Logged in as {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to guild {GUILD_ID}")
        for command in synced:
            print(f"Registered: /{command.name}")
    except Exception as e:
        print(f"Command sync failed: {e}")

@bot.tree.command(name="mmm_invest", description="Invest UnbelievaBoat cash into MMM")
@app_commands.describe(amount="Amount of cash to invest")
async def mmm_invest(interaction: discord.Interaction, amount: int):
    if amount <= 0:
        return await interaction.response.send_message("Amount must be positive.", ephemeral=True)

    balance = await get_unb_balance(interaction.user.id)
    if not balance or balance.get("cash", 0) < amount:
        return await interaction.response.send_message("You don't have enough cash.", ephemeral=True)

    success = await edit_unb_balance(interaction.user.id, -amount, "Invested in MMM")
    if not success:
        return await interaction.response.send_message("Failed to take money from UnbelievaBoat.", ephemeral=True)

    user_id = str(interaction.user.id)
    shares = amount / data["price"]
    data["investors"][user_id] = data["investors"].get(user_id, 0) + shares
    data["total_invested"] += amount

    growth = 1 + (amount / max(data["total_invested"], 1000)) * 0.12
    data["price"] *= growth
    data["history"].append({"price": data["price"], "time": datetime.now().isoformat()})
    save_data(data)

    await interaction.response.send_message(
        f"Invested **{amount:,}** → bought **{shares:.4f}** shares.\nNew price: **{data['price']:,.0f}**",
        ephemeral=True
    )

    channel = bot.get_channel(GRAPH_CHANNEL_ID)
    if channel:
        await update_live_graph(channel)

@bot.tree.command(name="mmm_sell", description="Sell MMM shares for UnbelievaBoat cash")
@app_commands.describe(shares="Shares to sell or 'all'")
async def mmm_sell(interaction: discord.Interaction, shares: str):
    user_id = str(interaction.user.id)
    owned = data["investors"].get(user_id, 0)

    try:
        to_sell = owned if shares.lower() == "all" else float(shares)
    except:
        return await interaction.response.send_message("Invalid amount.", ephemeral=True)

    if to_sell > owned or to_sell <= 0 or to_sell != to_sell:
        return await interaction.response.send_message("You don't own that many shares.", ephemeral=True)

    payout = int(to_sell * data["price"])

    success = await edit_unb_balance(interaction.user.id, payout, "Sold MMM shares")
    if not success:
        return await interaction.response.send_message("Failed to give money via UnbelievaBoat.", ephemeral=True)

    data["investors"][user_id] -= to_sell
    if data["investors"][user_id] <= 0:
        del data["investors"][user_id]

    data["price"] *= 0.985
    data["history"].append({"price": data["price"], "time": datetime.now().isoformat()})
    save_data(data)

    await interaction.response.send_message(
        f"Sold **{to_sell:.4f}** shares for **{payout:,}** cash.\nNew price: **{data['price']:,.0f}**",
        ephemeral=True
    )

    channel = bot.get_channel(GRAPH_CHANNEL_ID)
    if channel:
        await update_live_graph(channel)

@bot.tree.command(name="mmm_setup", description="Create or refresh the live graph")
async def mmm_setup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    channel = bot.get_channel(GRAPH_CHANNEL_ID)
    if not channel:
        return await interaction.followup.send("Check GRAPH_CHANNEL_ID.", ephemeral=True)

    try:
        await update_live_graph(channel)
        await interaction.followup.send("Live graph updated.", ephemeral=True)
    except Exception as e:
        print(f"Graph error: {e}")
        await interaction.followup.send("Failed to update graph. Check Railway logs.", ephemeral=True)

@bot.tree.command(name="mmm_portfolio", description="See your shares")
async def mmm_portfolio(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    owned = data["investors"].get(user_id, 0)
    value = owned * data["price"]
    await interaction.response.send_message(
        f"You own **{owned:.4f}** shares worth **{value:,.0f}** at the current price.",
        ephemeral=True
    )

@bot.tree.command(name="mmm_crash", description="Admin force the pyramid to collapse")
async def mmm_crash(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("You need Administrator permission.", ephemeral=True)

    data["price"] = max(1, data["price"] * 0.05)
    data["history"].append({
        "price": data["price"],
        "time": datetime.now().isoformat()
    })
    save_data(data)

    await interaction.response.send_message(
        f"**The pyramid has collapsed.**\nPrice crashed to **{data['price']:,.0f}**.",
        ephemeral=False
    )

    channel = bot.get_channel(GRAPH_CHANNEL_ID)
    if channel:
        await update_live_graph(channel)

@bot.tree.command(name="mmm_debug", description="Debug UnbelievaBoat connection")
async def mmm_debug(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)

    balance = await get_unb_balance(interaction.user.id)
    await interaction.response.send_message(
        f"**Debug Info**\n"
        f"Guild ID: `{GUILD_ID}`\n"
        f"Your User ID: `{interaction.user.id}`\n"
        f"Balance response: `{balance}`",
        ephemeral=True
    )

bot.run(DISCORD_TOKEN)
