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
    file = discord.File(graph, filename
