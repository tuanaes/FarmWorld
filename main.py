import asyncio
import contextlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto, LabeledPrice, PreCheckoutQuery

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

bot = Bot(TOKEN)
dp = Dispatcher()
ADMIN_ID = 1531051967
ACTIVE_SECTION_DELAY = 0.4
DATA_FILE = "/data/farmworld.json"
if not os.path.isdir("/data"):
    DATA_FILE = "farmworld.json"

@dataclass
class Bed:
    state: str = "empty"
    crop: str = ""
    growth_seconds: int = 0
    planted_at: float = 0.0
    notified: bool = False

@dataclass
class Player:
    coins: int = 100
    spheres: int = 5
    inventory: Dict[str, int] = field(default_factory=lambda: {"Тяпка": 1})
    beds: List[Bed] = field(default_factory=lambda: [Bed(), Bed(), Bed()])
    level: int = 0
    xp: int = 0
    total_harvest: int = 0
    earned_coins: int = 0
    soil_level: int = 0
    watered_until: float = 0.0  # cooldown: next watering available at this timestamp
    achievements: List[str] = field(default_factory=list)
    daily_last: int = 0
    daily_streak: int = 0
    quests_done: List[str] = field(default_factory=list)
    quest_progress: Dict[str, int] = field(default_factory=dict)
    npc_stage: int = 0
    vip_until: float = 0.0
    vip_trial_claimed: bool = False
    lucky_boost: int = 0
    cosmetics: Dict[str, str] = field(default_factory=dict)
    owned_cosmetics: List[str] = field(default_factory=list)
    level_rewards_claimed: List[int] = field(default_factory=list)
    boss_damage: int = 0
    orders: List[Dict] = field(default_factory=list)
    orchard_level: int = 1
    orchard_next_collect: float = 0.0
    apple_processing: Dict[str, float] = field(default_factory=dict)
    nickname: str = ""  # отображаемое имя в рейтинге, без @ и без ссылки
    event_score: int = 0
    event_participations: int = 0
    event_wins: int = 0
    event_podiums: int = 0
    event_history: List[Dict] = field(default_factory=list)
    fishing: Dict = field(default_factory=dict)
    equipment: Dict[str, int] = field(default_factory=dict)
    mine: Dict = field(default_factory=dict)
    flowerbed_lines: List[List[str]] = field(default_factory=lambda: [["" for _ in range(10)]])
    wood: int = 0
    flower_harvested: int = 0
    flower_wood_awarded: int = 0
    axe: int = 0
    cherry_tree_state: str = "empty"
    cherry_tree_planted_at: float = 0.0
    cherry_tree_ready_at: float = 0.0
    cherries_harvested: int = 0
    lumberjack_quest_stage: int = 0
    lumberjack_quest_claimed: bool = False
    lumberjack_stage_claimed: List[int] = field(default_factory=list)
    theft_permissions: int = 4
    theft_day: int = 0
    chairman_application_until: float = 0.0

players: Dict[int, Player] = {}

# Босс хранится в постоянном /data вместе с прогрессом игроков.
# Это важно для Deployka: файлы проекта могут заменяться при загрузке новой ZIP-версии,
# а /data предназначен для сохранения данных между обновлениями/перезапусками.
BOSS_FILE = os.path.join(os.path.dirname(DATA_FILE), "boss_event.json") if os.path.dirname(DATA_FILE) else "boss_event.json"
LEGACY_BOSS_FILE = "boss_event.json"
BOSS_MAX_HP = 100000
BOSS_DURATION = 48 * 3600
boss_event = {"started_at": 0.0, "hp": BOSS_MAX_HP, "defeated": False, "victory_notified": False}

def load_boss():
    global boss_event
    source = BOSS_FILE
    # Однократно переносим старый локальный файл в /data, если он есть.
    # Это позволяет сохранить уже начатое событие при первом обновлении этой версии.
    if not os.path.exists(source) and source != LEGACY_BOSS_FILE and os.path.exists(LEGACY_BOSS_FILE):
        try:
            with open(LEGACY_BOSS_FILE, "r", encoding="utf-8") as f:
                data=json.load(f)
            with open(source, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            boss_event.update(data)
            boss_event.setdefault("victory_notified", False)
            return
        except Exception:
            logging.exception("legacy boss migration failed")
    if os.path.exists(source):
        try:
            with open(source, "r", encoding="utf-8") as f:
                data=json.load(f)
            if isinstance(data, dict):
                boss_event.update(data)
                boss_event.setdefault("victory_notified", False)
        except Exception:
            logging.exception("load_boss failed")

def save_boss():
    try:
        target_dir = os.path.dirname(BOSS_FILE)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        tmp = BOSS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(boss_event, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, BOSS_FILE)
    except Exception:
        logging.exception("save_boss failed")

def boss_active():
    if not boss_event["started_at"]:
        return False
    if boss_event["defeated"]:
        return False
    return time.time() < boss_event["started_at"] + BOSS_DURATION and boss_event["hp"] > 0

def boss_time_left():
    return max(0, int(boss_event["started_at"] + BOSS_DURATION - time.time()))

def boss_damage_for_crop(crop, amount):
    return max(1, int(CROPS[crop].get("boss_damage", 1) * amount))

def boss_rating_lines(limit=50):
    data = [(uid, p) for uid, p in players.items() if p.boss_damage > 0]
    data.sort(key=lambda x: (x[1].boss_damage, x[1].level, x[1].earned_coins), reverse=True)
    return data[:limit]

def boss_rating_text(limit=50):
    data = boss_rating_lines(limit)
    if not data:
        return "📊 Рейтинг игроков\n\nПока никто не нанёс урон боссу."
    lines = ["📊 Рейтинг игроков", "", "⚔️ Урон по Полевому Вредителю:"]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, (uid, p) in enumerate(data, 1):
        nickname = p.nickname or "Игрок"
        prefix = medals.get(i, f"{i}.")
        lines.append(f"{prefix} 👤 {nickname} — ⚔️ {p.boss_damage:,}")
    return "\n".join(lines)

async def send_boss_victory_notification():
    """Однократно сообщает всем зарегистрированным игрокам о победе над боссом."""
    if boss_event.get("victory_notified", False):
        return
    boss_event["victory_notified"] = True
    save_boss()
    text = (
        "🏆 БОСС ПОВЕРЖЕН!\n\n"
        "🐀 Полевой Вредитель уничтожен!\n"
        "🌾 Благодаря совместным усилиям игроков FarmWorld снова в безопасности!\n\n"
        f"⚔️ Боссу нанесено урона: {BOSS_MAX_HP:,}"
    )
    kb = inline([[
        InlineKeyboardButton(text="🏆 Мой вклад", callback_data="boss:me"),
        InlineKeyboardButton(text="📊 Рейтинг игроков", callback_data="boss:rating"),
    ]])
    for uid in list(players.keys()):
        try:
            await bot.send_message(uid, text, reply_markup=kb)
        except Exception:
            logging.exception("boss victory notification failed for user %s", uid)

load_boss()

# ---------- EXPANDED EVENT SYSTEM ----------
EVENT_FILE = os.path.join(os.path.dirname(DATA_FILE), "farmworld_event.json") if os.path.dirname(DATA_FILE) else "farmworld_event.json"
EVENT_DURATION = 48 * 3600
EVENT_NAME = "🌾 Великий сбор урожая"
EVENT_ID = "harvest_festival_001"
EVENT_MAX_RANK = 10
event_state = {
    "id": EVENT_ID,
    "name": EVENT_NAME,
    "started_at": 0.0,
    "ended": False,
    "rewards_distributed": False,
}

def load_event():
    global event_state
    if os.path.exists(EVENT_FILE):
        try:
            with open(EVENT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                event_state.update(data)
        except Exception:
            logging.exception("load_event failed")

def save_event():
    try:
        target_dir = os.path.dirname(EVENT_FILE)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        tmp = EVENT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(event_state, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, EVENT_FILE)
    except Exception:
        logging.exception("save_event failed")

def event_active():
    started = float(event_state.get("started_at", 0) or 0)
    return bool(started and not event_state.get("ended", False) and time.time() < started + EVENT_DURATION)

def event_time_left():
    started = float(event_state.get("started_at", 0) or 0)
    return max(0, int(started + EVENT_DURATION - time.time())) if started else 0

def event_points_for_crop(crop, amount):
    # Чем сложнее культура, тем больше очков в событии.
    return max(0, int(CROPS.get(crop, {}).get("event_points", 1) * amount))

def event_reward_for_place(place: int):
    rewards = {
        1: {"coins": 50000, "spheres": 100, "premium": 3, "title": "title:event_hero"},
        2: {"coins": 30000, "spheres": 70, "premium": 2},
        3: {"coins": 20000, "spheres": 50, "premium": 2},
        4: {"coins": 15000, "spheres": 35, "premium": 1},
        5: {"coins": 10000, "spheres": 25, "premium": 1},
        6: {"coins": 7000, "spheres": 20, "premium": 0},
        7: {"coins": 5000, "spheres": 15, "premium": 0},
        8: {"coins": 4000, "spheres": 12, "premium": 0},
        9: {"coins": 3000, "spheres": 10, "premium": 0},
        10: {"coins": 2000, "spheres": 8, "premium": 0},
    }
    return rewards.get(place, {"coins": 0, "spheres": 3, "premium": 0})

def event_reward_text(place: int):
    r = event_reward_for_place(place)
    parts=[]
    if r.get("coins"): parts.append(f"💰 {r['coins']:,}")
    if r.get("spheres"): parts.append(f"🔮 {r['spheres']}")
    if r.get("premium"): parts.append(f"🌱 {r['premium']} премиум-сем.")
    if r.get("title"): parts.append("🏆 эксклюзивный титул")
    return " + ".join(parts)

def event_rating_lines(limit=50):
    data=[(uid,p) for uid,p in players.items() if p.event_score>0]
    data.sort(key=lambda x:(x[1].event_score,x[1].level,x[1].earned_coins), reverse=True)
    return data[:limit]

def event_rank_for(uid):
    data=event_rating_lines(100000)
    for i,(xuid,_) in enumerate(data,1):
        if xuid==uid: return i
    return None

def event_rating_text(limit=10):
    data=event_rating_lines(limit)
    if not data:
        return f"📊 Рейтинг — {EVENT_NAME}\n\nПока никто не набрал очки."
    medals={1:"🥇",2:"🥈",3:"🥉"}
    lines=[f"📊 Рейтинг — {EVENT_NAME}","","🌾 Очки за собранный урожай:"]
    for i,(uid,p) in enumerate(data,1):
        prefix=medals.get(i,f"{i}.")
        nickname=p.nickname or "Игрок"
        lines.append(f"{prefix} {nickname} — ⭐ {p.event_score:,}")
    return "\n".join(lines)

def event_history_text():
    lines = [
        "🏆 История ивентов",
        "",
        "📜 Здесь будут появляться завершённые события FarmWorld.",
        "Здесь можно открыть информацию о прошедших событиях,",
        "посмотреть свой вклад и итоговый рейтинг участников.",
    ]
    return "\n".join(lines)

def event_profile_history_text(p: Player):
    return "\n".join([
        "🏆 История ивентов",
        "",
        f"🎯 Участия: {p.event_participations}",
        f"🥇 Победы: {p.event_wins}",
        f"🏅 Топ-3: {p.event_podiums}",
    ])

load_event()

CROPS = {
    "Пшеница": {"seed": "Семена пшеницы", "price": 1, "growth": 60, "sell": 1, "yield": 4, "yield_min": 4, "yield_max": 4, "xp": 1, "unlock": 0, "sale_xp": 1, "boss_damage": 5, "event_points": 1},
    "Морковь": {"seed": "Семена моркови", "price": 10, "growth": 600, "sell": 5, "yield": 6, "yield_min": 6, "yield_max": 6, "xp": 15, "unlock": 0, "sale_xp": 15, "boss_damage": 10, "event_points": 2},
    "Картофель": {"seed": "Семена картофеля", "price": 80, "growth": 3600, "sell": 20, "yield": 10, "yield_min": 10, "yield_max": 10, "xp": 30, "unlock": 0, "sale_xp": 30, "boss_damage": 15, "event_points": 3},
    "Помидор": {"seed": "Семена помидора", "price": 500, "growth": 18000, "sell": 100, "yield": 12, "yield_min": 12, "yield_max": 12, "xp": 100, "unlock": 0, "sale_xp": 100, "boss_damage": 20, "event_points": 4},
    "Огурец": {"seed": "Семена огурца", "price": 1000, "growth": 43200, "sell": 150, "yield": 14, "yield_min": 14, "yield_max": 14, "xp": 150, "unlock": 0, "sale_xp": 150, "boss_damage": 25, "event_points": 5},
    "Клубника": {"seed": "Семена клубники", "price": 2000, "growth": 86400, "sell": 300, "yield": 16, "yield_min": 16, "yield_max": 16, "xp": 300, "unlock": 0, "sale_xp": 300, "boss_damage": 35, "event_points": 7},
    "Кукуруза": {"seed": "Семена кукурузы", "price": 1, "growth": 60, "sell": 1, "yield": 8, "yield_min": 8, "yield_max": 8, "xp": 5, "unlock": 0, "sale_xp": 5, "boss_damage": 50, "event_points": 9},
    "Тыква": {"seed": "Семена тыквы", "price": 2, "growth": 480, "sell": 5, "yield": 8, "yield_min": 8, "yield_max": 8, "xp": 15, "unlock": 0, "sale_xp": 15, "boss_damage": 80, "event_points": 13},
    "Баклажан": {"seed": "Семена баклажана", "price": 3, "growth": 3000, "sell": 20, "yield": 12, "yield_min": 12, "yield_max": 12, "xp": 30, "unlock": 0, "sale_xp": 30, "boss_damage": 110, "event_points": 18},
    "Виноград": {"seed": "Семена винограда", "price": 5, "growth": 14400, "sell": 100, "yield": 14, "yield_min": 14, "yield_max": 14, "xp": 100, "unlock": 0, "sale_xp": 100, "boss_damage": 150, "event_points": 25},
    "Арбуз": {"seed": "Семена арбуза", "price": 10, "growth": 36000, "sell": 150, "yield": 16, "yield_min": 16, "yield_max": 16, "xp": 150, "unlock": 0, "sale_xp": 150, "boss_damage": 200, "event_points": 35},
    "Роза": {"seed": "Семена розы", "price": 3, "growth": 1800, "sell": 0, "yield": 1, "yield_min": 1, "yield_max": 2, "xp": 0, "unlock": 0, "sale_xp": 0, "boss_damage": 0, "event_points": 0, "flower": True},
    "Тюльпан": {"seed": "Семена тюльпана", "price": 200, "growth": 3600, "sell": 0, "yield": 1, "yield_min": 1, "yield_max": 2, "xp": 0, "unlock": 0, "sale_xp": 0, "boss_damage": 0, "event_points": 0, "flower": True},
}


APPLE_PRODUCTS = {
    "Яблочный сок": {"emoji": "🧃", "input": 5, "time": 600, "sell": 100, "xp": 12},
    "Яблочный пирог": {"emoji": "🥧", "input": 10, "time": 1200, "sell": 230, "xp": 30},
    "Яблочное варенье": {"emoji": "🍯", "input": 15, "time": 1800, "sell": 380, "xp": 55},
    "Золотой сок": {"emoji": "✨🧃", "input": 3, "time": 1200, "sell": 900, "xp": 40, "source": "Золотые яблоки"},
    "Сияющее варенье": {"emoji": "💎🍯", "input": 2, "time": 2700, "sell": 2500, "xp": 100, "source": "Сияющие яблоки"},
}

APPLE_SALES = {
    "Яблоки": {"emoji": "🍎", "sell": 15, "xp": 1},
    "Золотые яблоки": {"emoji": "✨🍎", "sell": 150, "xp": 8},
    "Сияющие яблоки": {"emoji": "💎🍎", "sell": 500, "xp": 25},
}

ORCHARD_YIELD = {1: 6, 2: 8, 3: 11, 4: 15, 5: 20}
ORCHARD_COST = {1: 6000, 2: 12000, 3: 24000, 4: 48000}

CROP_EMOJI = {
    "Пшеница": "🌾", "Морковь": "🥕", "Картофель": "🥔", "Помидор": "🍅",
    "Огурец": "🥒", "Клубника": "🍓", "Кукуруза": "🌽", "Тыква": "🎃",
    "Баклажан": "🍆", "Виноград": "🍇", "Арбуз": "🍉",
    "Роза": "🌹", "Тюльпан": "🌷",
}

def crop_emoji(crop: str) -> str:
    return CROP_EMOJI.get(crop, "🌱")
BED_BASE_PRICE = 1000
BED_PRICE_MULTIPLIER = 5

def bed_price(p: Player) -> int:
    # При стартовых 3 грядках 4-я стоит 1000 монет.
    # Каждая следующая покупка стоит в 5 раз дороже предыдущей.
    purchase_index = max(0, len(p.beds) - 3)
    return BED_BASE_PRICE * (BED_PRICE_MULTIPLIER ** purchase_index)

def save_data():
    try:
        raw = {str(uid): {**asdict(p), "beds": [asdict(b) for b in p.beds]} for uid, p in players.items()}
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)
    except Exception:
        logging.exception("save_data failed")

def load_data():
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for uid, d in raw.items():
            beds = [Bed(**b) for b in d.pop("beds", [])]
            had_level_rewards = "level_rewards_claimed" in d
            p = Player(**d)
            p.beds = beds or [Bed(), Bed(), Bed()]
            if not isinstance(p.inventory, dict):
                p.inventory = {}
            p.inventory.setdefault("Тяпка", 1)
            if not isinstance(getattr(p, "mine", None), dict):
                p.mine = {}
            if not isinstance(getattr(p, "flowerbed_lines", None), list) or not p.flowerbed_lines:
                p.flowerbed_lines = [["" for _ in range(10)]]
            p.flowerbed_lines = [(list(line[:10]) + [""] * 10)[:10] for line in p.flowerbed_lines[:40]]
            if not p.flowerbed_lines:
                p.flowerbed_lines = [["" for _ in range(10)]]
            if not hasattr(p, "wood"): p.wood = 0
            if not hasattr(p, "flower_harvested"): p.flower_harvested = 0
            if not hasattr(p, "flower_wood_awarded"): p.flower_wood_awarded = 0
            if not hasattr(p, "axe"): p.axe = 0
            if not hasattr(p, "cherry_tree_state"): p.cherry_tree_state = "empty"
            if not hasattr(p, "cherry_tree_planted_at"): p.cherry_tree_planted_at = 0.0
            if not hasattr(p, "cherry_tree_ready_at"): p.cherry_tree_ready_at = 0.0
            if not hasattr(p, "cherries_harvested"): p.cherries_harvested = 0
            if not hasattr(p, "lumberjack_quest_stage"): p.lumberjack_quest_stage = 0
            if not hasattr(p, "lumberjack_quest_claimed"): p.lumberjack_quest_claimed = False
            if not hasattr(p, "lumberjack_stage_claimed"): p.lumberjack_stage_claimed = []
            if not isinstance(p.lumberjack_stage_claimed, list): p.lumberjack_stage_claimed = []
            if not hasattr(p, "theft_permissions"): p.theft_permissions = 4
            if not hasattr(p, "theft_day"): p.theft_day = 0
            if not hasattr(p, "chairman_application_until"): p.chairman_application_until = 0.0
            if p.lumberjack_quest_claimed and not p.lumberjack_stage_claimed: p.lumberjack_stage_claimed = [1,2,3,4,5]
            # Миграция старых сохранений: существующие уровни считаются уже пройденными,
            # чтобы новая система не выдала награды задним числом. Новые уровни 1+
            # будут получать награду по новой системе.
            if not had_level_rewards:
                # Старая сохранёнка: считаем награды за уровни до текущего
                # уже полученными, но награда за сам текущий уровень доступна.
                p.level_rewards_claimed = list(range(1, max(1, p.level))) if p.level > 0 else []
            # Если в старой сохранёнке список был неполным, не помечаем текущий
            # уровень полученным автоматически — его награду игрок должен забрать.
            players[int(uid)] = p
    except Exception:
        logging.exception("load_data failed")

def get_player(uid: int) -> Player:
    if uid not in players:
        players[uid] = Player(vip_until=time.time() + 7*86400, vip_trial_claimed=True)
        save_data()
    return players[uid]

def remember_nickname(p: Player, user) -> None:
    # Для соседей и рейтингов используем красивое отображаемое имя Telegram,
    # а не @username. Например: «Алексей» или «Алексей Иванов».
    first_name = (getattr(user, "first_name", None) or "").strip()
    last_name = (getattr(user, "last_name", None) or "").strip()
    nickname = " ".join(part for part in (first_name, last_name) if part).strip() or "Игрок"
    if p.nickname != nickname:
        p.nickname = nickname

def vip_active(p: Player) -> bool:
    return p.vip_until > time.time()

def vip_left(p: Player) -> str:
    if not vip_active(p): return "неактивен"
    sec=max(0,int(p.vip_until-time.time())); days=sec//86400; hours=(sec%86400)//3600
    return f"{days} дн. {hours} ч."

def vip_status(p: Player) -> str:
    return f"👑 VIP: Активен — осталось {vip_left(p)}" if vip_active(p) else "👑 VIP: Неактивен"

TITLES = [(1,"Начинающий фермер"),(5,"Юный земледелец"),(10,"Опытный фермер"),(25,"Умелый фермер"),(50,"Знаток полей"),(75,"Мастер урожая"),(100,"Хозяин фермы"),(150,"Великий агроном"),(200,"Легенда фермы"),(300,"Император урожая")]

def get_title(level: int) -> str:
    title = "Новичок"
    for req, name in TITLES:
        if level >= req:
            title = name
        else:
            break
    return title

def xp_need(level: int) -> int:
    # С нулевого уровня первая ступень требует 100 XP; далее стоимость растёт.
    return 0 if level >= 300 else (level + 1) * 100

PREMIUM_REWARD_CROPS = ["Тыква", "Баклажан", "Виноград", "Арбуз"]

def level_reward(level: int):
    """Каждый уровень: 1 премиум-семя + ровно 5 сфер."""
    crop = PREMIUM_REWARD_CROPS[(level - 1) % len(PREMIUM_REWARD_CROPS)]
    return crop, 5

def next_unclaimed_reward(p: Player):
    # Показываем следующую награду даже до её достижения.
    # Игрок начинает с 0 уровня, поэтому первая награда всегда за 1 уровень.
    for level in range(1, 301):
        if level not in p.level_rewards_claimed:
            return level
    return None

def add_xp(p: Player, amount: int):
    if p.level >= 300:
        return []
    p.xp += max(0, amount)
    rewards=[]
    while p.level < 300 and p.xp >= xp_need(p.level):
        p.xp -= xp_need(p.level)
        p.level += 1
        rewards.append((p.level, 0, 0))
    if p.level >= 300:
        p.xp = 0
    return rewards

async def send_level_up_notification(user_id: int, levels: list):
    """Отдельное уведомление о повышении уровня с кнопкой перехода в профиль."""
    if not levels:
        return
    player = get_player(user_id)
    latest = levels[-1][0]
    reward_level = next_unclaimed_reward(player)
    if reward_level is not None:
        crop, spheres = level_reward(reward_level)
        reward_text = f"🎁 Награда за {reward_level} уровень: {crop_emoji(crop)} 1 семя + {spheres} 🔮"
    else:
        reward_text = "🎁 Все награды за уровни получены."
    text = (f"🎉 УРОВЕНЬ ПОВЫШЕН!\n\n"
            f"🏆 Новый уровень: {latest}\n"
            f"⭐ Поздравляем с новым достижением!\n\n"
            f"{reward_text}")
    kb = inline([[InlineKeyboardButton(text="👤 Профиль — забрать награду", callback_data="profile:open")]])
    try:
        await bot.send_message(user_id, text, reply_markup=kb)
    except Exception:
        logging.exception("Не удалось отправить уведомление о новом уровне пользователю %s", user_id)


@dp.callback_query(F.data == "profile:open")
async def profile_open(c):
    p = get_player(c.from_user.id)
    remember_nickname(p, c.from_user)
    save_data()
    u = f"@{c.from_user.username}" if c.from_user.username else "@не указан"
    await c.answer()
    await c.message.edit_text(build_profile_text(p, u), reply_markup=profile_inline_kb(p))


def progress(p):
    if p.level >= 300: return "▓"*10, "MAX", 0
    need=xp_need(p.level); filled=min(10,int(p.xp*10/need))
    return "▓"*filled+"░"*(10-filled), f"{p.xp}/{need}", need-p.xp

def update_bed(b):
    if b.state == "growing" and time.time()-b.planted_at >= b.growth_seconds:
        b.state="ready"

def remaining(b):
    return max(0, b.growth_seconds-int(time.time()-b.planted_at)) if b.state=="growing" else 0

def effective_growth(p, crop):
    base=CROPS[crop]["growth"]
    soil=max(0.5, 1-0.10*p.soil_level)
    vip=0.80 if vip_active(p) else 1.0
    return max(10, int(base*soil*vip))

COSMETICS = {
    "frame:wood": ("🪵 Деревянная рамка", "frame", 25),
    "frame:gold": ("🏆 Золотая рамка", "frame", 50),
    "frame:magic": ("🔮 Магическая рамка", "frame", 75),
    "frame:royal": ("👑 Королевская рамка", "frame", 100),
    "title:vip": ("💎 Титул: VIP-фермер", "title", 30),
    "title:lord": ("👑 Титул: Лорд фермы", "title", 50),
    "title:mage": ("✨ Титул: Волшебник урожая", "title", 50),
    "title:event_hero": ("🏆 Титул: Герой события", "title", 0),
    "bed:wood": ("🪵 Скин грядок: Дерево", "bed", 40),
    "bed:stone": ("🪨 Скин грядок: Камень", "bed", 50),
    "bed:magic": ("🔮 Скин грядок: Магия", "bed", 75),
    "sale:coins": ("💰 Эффект продажи: Монеты", "sale", 25),
    "sale:spark": ("✨ Эффект продажи: Искры", "sale", 35),
    "harvest:confetti": ("🎉 Эффект урожая: Конфетти", "harvest", 30),
    "harvest:fireworks": ("🎆 Эффект урожая: Фейерверк", "harvest", 50),
    "theme:autumn": ("🍂 Тема фермы: Осень", "theme", 60),
    "theme:winter": ("❄️ Тема фермы: Зима", "theme", 60),
    "theme:spring": ("🌸 Тема фермы: Весна", "theme", 60),
    "theme:magic": ("🌌 Тема фермы: Магия", "theme", 100),
    "badge:first": ("🌱 Значок: Первый урожай", "badge", 30),
    "badge:master": ("🏆 Значок: Мастер урожая", "badge", 50),
    "badge:royal": ("👑 Значок: Королевская ферма", "badge", 75),
    "name:gold": ("🟡 Стиль имени: Золото", "name", 50),
    "name:magic": ("🔮 Стиль имени: Магия", "name", 75),
    "name:royal": ("👑 Стиль имени: Королевский", "name", 100),
}

COSMETIC_CATEGORIES = {
    "frame": ("🖼️ Рамки профиля", ["frame:wood","frame:gold","frame:magic","frame:royal"]),
    "title": ("🎖️ Титулы", ["title:vip","title:lord","title:mage","title:event_hero"]),
    "bed": ("🛏️ Скины грядок", ["bed:wood","bed:stone","bed:magic"]),
    "sale": ("💰 Эффекты продажи", ["sale:coins","sale:spark"]),
    "harvest": ("🌾 Эффекты урожая", ["harvest:confetti","harvest:fireworks"]),
    "theme": ("🌍 Темы фермы", ["theme:autumn","theme:winter","theme:spring","theme:magic"]),
    "badge": ("🏅 Значки достижений", ["badge:first","badge:master","badge:royal"]),
    "name": ("✨ Стили имени", ["name:gold","name:magic","name:royal"]),
}

def cosmetic_label(p: Player, key: str) -> str:
    return COSMETICS[key][0] + (" ✅" if key in p.owned_cosmetics else f" — {COSMETICS[key][2]} 🔮")

def active_cosmetic(p: Player, kind: str) -> str:
    return p.cosmetics.get(kind, "")

def cosmetic_display(p: Player, kind: str, default: str = "") -> str:
    key = active_cosmetic(p, kind)
    if key in COSMETICS:
        return COSMETICS[key][0]
    return default

def main_kb(uid, active=None):
    sections = ("🌾", "🚗", "🛒", "👤", "👥")
    row = [KeyboardButton(text=(f"🔸{emoji}" if active == emoji else emoji)) for emoji in sections]
    rows=[row]
    if uid==ADMIN_ID: rows.append([KeyboardButton(text="🛠️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# Telegram не позволяет редактировать уже отправленную ReplyKeyboardMarkup.
# Поэтому при переходе между основными разделами отправляем ОДНО маленькое
# служебное сообщение с эмодзи раздела и новой клавиатурой, а предыдущее
# такое сообщение удаляем. Пустых/невидимых сообщений больше нет.
_active_indicator_messages: Dict[int, int] = {}

async def refresh_main_keyboard(chat_id, uid, active=None):
    if active not in {"🌾", "🚗", "🛒", "👤", "👥"}:
        return
    previous_id = _active_indicator_messages.get(uid)
    try:
        indicator = await bot.send_message(
            chat_id=chat_id,
            text=active,
            reply_markup=main_kb(uid, active=active),
        )
        _active_indicator_messages[uid] = indicator.message_id
    except Exception:
        logging.exception("failed to update active section keyboard")
        return
    if previous_id and previous_id != indicator.message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=previous_id)
        except Exception:
            # Старое служебное сообщение могло быть уже удалено или быть недоступно.
            pass

def inline(rows): return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_kb():
    return inline([
        [
            InlineKeyboardButton(text="🌱 Семена", callback_data="shop:seeds"),
            InlineKeyboardButton(text="🏡 Грядки", callback_data="shop:beds"),
        ],
        [InlineKeyboardButton(text="✨ Премиум-магазин", callback_data="shop:premium")]
    ])

def premium_kb():
    return inline([
        [InlineKeyboardButton(text="🔮 Сферы",callback_data="premium:spheres")],
        [InlineKeyboardButton(text="🌱 Премиум-семена",callback_data="premium:seeds")],
        [InlineKeyboardButton(text="👑 VIP",callback_data="premium:vip")],
        [InlineKeyboardButton(text="⚡ Усилители",callback_data="premium:boost")],
        [InlineKeyboardButton(text="🎨 Косметика",callback_data="premium:cosmetic")],
        [InlineKeyboardButton(text="⬅️ В магазин",callback_data="shop:main")]
    ])

def admin_kb():
    return inline([
        [InlineKeyboardButton(text="📰 Опубликовать новость",callback_data="admin:news_help")],
        [InlineKeyboardButton(text="💰 Накрутить 1.000.000",callback_data="admin:add_money")],
        [InlineKeyboardButton(text="🔮 Выдать 100 сфер",callback_data="admin:add_spheres_100")],
        [InlineKeyboardButton(text="🔮 Выдать 1.000 сфер",callback_data="admin:add_spheres_1000")],
        [InlineKeyboardButton(text="👑 Выдать VIP",callback_data="admin:activate_vip")],
        [InlineKeyboardButton(text="🎪 Запустить следующий ивент",callback_data="admin:event_start")],
        [InlineKeyboardButton(text="🗑️ Сбросить мой прогресс",callback_data="admin:reset_confirm")]
    ])

# ---------- START / PROFILE ----------
@dp.message(CommandStart())
async def start(m: Message):
    p=get_player(m.from_user.id)
    remember_nickname(p, m.from_user)
    save_data()
    # Выдаём пробный VIP один раз при первом входе, включая игроков, созданных до добавления VIP.
    if not p.vip_trial_claimed:
        p.vip_until = max(time.time(), p.vip_until) + 7*86400
        p.vip_trial_claimed = True
        save_data()
    name=m.from_user.first_name or "фермер"
    await m.answer(f"🌱 Добро пожаловать в FarmWorld, {name}!\n\nРазвивай ферму, выполняй задания, прокачивай уровень и открывай город.\n\n💰 {p.coins} монет\n🔮 {p.spheres} сфер\n🌱 {len(p.beds)} грядок", reply_markup=main_kb(m.from_user.id))

@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer("🌾 Ферма — выращивание\n🚗 Город — рынок, задания, банк, мастерская и NPC\n🛒 Магазин — семена и грядки\n👤 Профиль — уровень, достижения и статистика\n👥 Друзья и соседи — соседи и рейтинги")

def profile_inline_kb(p: Player):
    pending = next_unclaimed_reward(p)
    if pending is None:
        label = "🎁 Награды за уровни получены"
    else:
        crop, spheres = level_reward(pending)
        status = "" if p.level >= pending else " 🔒"
        label = f"🎁 Награда {pending} ур. — {crop_emoji(crop)} 1 семя + {spheres} 🔮{status}"
    return inline([
        [InlineKeyboardButton(text=label, callback_data="level:reward")],
        [InlineKeyboardButton(text="🏆 История ивентов", callback_data="event:profile_history")],
    ])

def build_profile_text(p: Player, username: str):
    bar,xp,rem=progress(p)
    seeds=[]; harvest=[]
    for crop,d in CROPS.items():
        seed_count = p.inventory.get(d['seed'],0)
        harvest_count = p.inventory.get('Урожай '+crop,0)
        if seed_count > 0:
            seeds.append(f"{crop_emoji(crop)} {crop}: {seed_count}")
        if harvest_count > 0:
            harvest.append(f"{crop_emoji(crop)} {crop}: {harvest_count}")
    if not seeds:
        seeds.append("— Пока нет семян")
    for item,d in APPLE_SALES.items():
        count=p.inventory.get(item,0)
        if count>0: harvest.append(f"{d['emoji']} {item}: {count}")
    for item,d in APPLE_PRODUCTS.items():
        count=p.inventory.get(item,0)
        if count>0: harvest.append(f"{d['emoji']} {item}: {count}")
    if not harvest:
        harvest.append("— Пока нет урожая")
    frame=cosmetic_display(p,"frame","🖼️ Классика")
    badge=cosmetic_display(p,"badge")
    name_style=cosmetic_display(p,"name")
    styled_name=username + (f" · {name_style}" if name_style else "")
    title=get_title(p.level)
    title_cosmetic=cosmetic_display(p,"title")
    if title_cosmetic:
        title += " · " + title_cosmetic
    pending=next_unclaimed_reward(p)
    reward_line = (f"\n🎁 Награда за {pending} уровень уже доступна: {crop_emoji(level_reward(pending)[0])} 1 семя + 5 🔮" if pending and p.level >= pending else (f"\n🎁 Следующая награда: {pending} уровень — {crop_emoji(level_reward(pending)[0])} 1 семя + 5 🔮" if pending else "\n🎁 Все награды за уровни получены"))
    return (f"👤 Профиль игрока\n\n📝 Имя: {styled_name}\n🎖️ Титул: {title}\n🏆 Уровень: {p.level}/300\n📊 Прогресс: {bar}\n🔹 Опыт: {xp} ({rem})"
            f"{reward_line}\n\n💰 Монеты: {p.coins}\n🔮 Сферы: {p.spheres}\n🪵 Древесина: {p.wood}\n{vip_status(p)}\n{frame}\n{badge}\n🟤 Грядки: {len(p.beds)}\n🪨 Улучшение почвы: {p.soil_level}/5\n\n🏆 Ивенты: {p.event_wins} побед · {p.event_podiums} раз в топ-3 · {p.event_participations} участий\n\n🌱 Семена:\n"+'\n'.join(seeds)+"\n\n🏠 Урожай:\n"+'\n'.join(harvest)+f"\n\n📦 Всего собрано: {p.total_harvest}\n💰 Всего заработано: {p.earned_coins}")

@dp.message(F.text.in_({"👤", "🔸👤"}))
async def profile(m: Message):
    p=get_player(m.from_user.id)
    remember_nickname(p, m.from_user)
    save_data()
    u=f"@{m.from_user.username}" if m.from_user.username else "@не указан"
    # Сначала обновляем Reply-клавиатуру служебным эмодзи, затем открываем раздел.
    await refresh_main_keyboard(m.chat.id, m.from_user.id, active="👤")
    await asyncio.sleep(ACTIVE_SECTION_DELAY)
    await m.answer(build_profile_text(p, u), reply_markup=profile_inline_kb(p))

@dp.callback_query(F.data=="level:reward")
async def claim_level_reward(c):
    p=get_player(c.from_user.id)
    level=next_unclaimed_reward(p)
    if level is None:
        await c.answer("🎁 Все награды уже получены.", show_alert=False)
        return
    if p.level < level:
        await c.answer(f"🔒 Награда будет доступна после достижения {level} уровня.", show_alert=False)
        return
    crop, spheres = level_reward(level)
    seed_key = CROPS[crop]["seed"]
    p.inventory[seed_key] = p.inventory.get(seed_key, 0) + 1
    p.spheres += spheres
    p.level_rewards_claimed.append(level)
    save_data()
    await c.answer()
    u=f"@{c.from_user.username}" if c.from_user.username else "@не указан"
    text=(f"🎁 Награда за {level} уровень получена!\n\n"
          f"{crop_emoji(crop)} +1 семя: {crop}\n"
          f"🔮 +{spheres} сфер")
    await c.message.edit_text(text+"\n\n"+build_profile_text(p, u), reply_markup=profile_inline_kb(p))


# ---------- FARM ----------
def _growth_clock(seconds):
    seconds=max(0,int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}ч {m}м"
    if m:
        return f"{m}м" if s < 30 else f"{m}м {s}с"
    return f"{s}с"

def water_button_text(p: Player) -> str:
    left = max(0, int(p.watered_until - time.time()))
    if left <= 0:
        return "💧 Полить"
    h, rem = divmod(left, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"💧 Полить (через {h}ч {m}м)"
    if m:
        return f"💧 Полить (через {m}м)"
    return f"💧 Полить (через {s}с)"

def farm_kb(p):
    rows=[]
    for i,b in enumerate(p.beds):
        update_bed(b)
        if b.state=="empty":
            status="🟤 Пусто"
        elif b.state=="growing":
            status=f"🌱 Растёт {crop_emoji(b.crop)} {b.crop} ({_growth_clock(remaining(b))})"
        else:
            status=f"{crop_emoji(b.crop)} {b.crop} — готово"
        # Эмодзи перед номером грядки убраны: 🟤 остаётся только перед «Пусто».
        rows.append([InlineKeyboardButton(text=f"Грядка {i+1}: {status}",callback_data=f"bed:{i}")])
    rows += [
        [InlineKeyboardButton(text="🪏 Посадить везде",callback_data="plant:all")],
        [InlineKeyboardButton(text="👑 Собрать все",callback_data="harvest:all")],
        [InlineKeyboardButton(text=water_button_text(p),callback_data="farm:water")],
        [InlineKeyboardButton(text="🌸 Клумба",callback_data="flowerbed:open")],
        [InlineKeyboardButton(text="🌳 Дерево",callback_data="tree:open")],
    ]
    return inline(rows)

async def show_farm(x, notice="", update_keyboard=False):
    p=get_player(x.from_user.id)
    theme=active_cosmetic(p,"theme")
    theme_prefix={"theme:autumn":"🍂","theme:winter":"❄️","theme:spring":"🌸","theme:magic":"🌌"}.get(theme,"🌱")
    text=(f"{theme_prefix} Ваша ферма, нажмите на грядку\n"
          "чтобы посадить или собрать урожай")
    if notice:
        text += f"\n\n{notice}"
    kb=farm_kb(p)
    if isinstance(x,Message):
        if update_keyboard:
            # Сначала обновляем клавиатуру, затем отправляем содержимое фермы.
            await refresh_main_keyboard(x.chat.id, x.from_user.id, active="🌾")
            await asyncio.sleep(ACTIVE_SECTION_DELAY)
        await x.answer(text,reply_markup=kb)
    else:
        await x.message.edit_text(text,reply_markup=kb)

@dp.message(F.text.in_({"🌾", "🔸🌾"}))
async def farm(m): await show_farm(m, update_keyboard=True)

@dp.callback_query(F.data.startswith("bed:"))
async def bed_click(c):
    p=get_player(c.from_user.id); i=int(c.data.split(":")[1])
    if i>=len(p.beds): await c.answer(); return await show_farm(c, notice="❌ Грядка не найдена.")
    b=p.beds[i]; update_bed(b)
    if b.state=="empty":
        # На пустой грядке показываем только те семена, которые уже куплены.
        owned=[]
        for crop,d in CROPS.items():
            count=p.inventory.get(d["seed"],0)
            if count>0:
                owned.append((crop,count))
        rows=[]
        if owned:
            rows.extend([
                [InlineKeyboardButton(text=f"{crop_emoji(crop)} {crop} — {count} шт.",callback_data=f"plant:bed:{i}:{crop}")]
                for crop,count in owned
            ])
            text=f"🌱 Грядка {i+1}\n\n🟤 Пусто\n\nВыберите семя для посадки:"
        else:
            text=f"🌱 Грядка {i+1}\n\n🟤 Пусто\n\n🌱 У вас нет купленных семян."
        rows.append([InlineKeyboardButton(text="🔙 К грядкам",callback_data="farm:list")])
        await c.message.edit_text(text,reply_markup=inline(rows))
    elif b.state=="growing":
        await c.message.edit_text(f"🌱 Грядка {i+1}\n\n🌱 Растёт: {b.crop}\n⏳ Осталось: {max(1,remaining(b))} сек.\n🔮 Сфер: {p.spheres}",reply_markup=inline([[InlineKeyboardButton(text="🔮 Завершить рост — 1 сфера",callback_data=f"speed:{i}")],[InlineKeyboardButton(text="🔙 К грядкам",callback_data="farm:list")]]))
    else:
        result=collect_bed(p,i)
        if not result:
            await c.answer(); return await show_farm(c, notice="📦 Урожай ещё не готов.")
        crop,rewards,amount=result
        save_data(); await c.answer(); await show_farm(c, notice=f"📦 Собрано: {crop} × {amount}")
        if boss_event["defeated"] and not boss_event.get("victory_notified", False):
            await send_boss_victory_notification()
        await send_level_up_notification(c.from_user.id, rewards)

def collect_bed(p,i):
    b=p.beds[i]; update_bed(b)
    if b.state!="ready": return None
    crop=b.crop; d=CROPS[crop]; amount=random.randint(d["yield_min"], d["yield_max"]);
    double_harvest=equipment_double(p,6)
    if double_harvest: amount*=2
    key="Урожай "+crop
    p.inventory[key]=p.inventory.get(key,0)+amount
    p.total_harvest+=amount
    rewards=add_xp(p,CROPS[crop]["xp"])

    if d.get("flower"):
        p.flower_harvested += amount
    else:
        if event_active():
            p.event_score += event_points_for_crop(crop, amount)
        if boss_active():
            dmg=boss_damage_for_crop(crop, amount)
            p.boss_damage += dmg
            boss_event["hp"]=max(0, boss_event["hp"]-dmg)
            if boss_event["hp"] <= 0:
                boss_event["defeated"]=True
            save_boss()
    b.state,b.crop,b.growth_seconds,b.planted_at,b.notified="empty","",0,0.0,False
    return crop,rewards,amount


# ---------- CHERRY TREE / WOOD ----------
CHERRY_GROWTH = 2 * 3600
CHERRY_REGROWTH = 6 * 3600
CHERRY_YIELD_MIN = 5
CHERRY_YIELD_MAX = 8
CHERRY_WOOD_YIELD = 3

def cherry_tree_update(p: Player):
    if p.cherry_tree_state == "growing" and p.cherry_tree_ready_at and time.time() >= p.cherry_tree_ready_at:
        p.cherry_tree_state = "ready"
        p.cherry_tree_ready_at = 0.0
        save_data()

def cherry_tree_time(p: Player) -> int:
    if p.cherry_tree_state != "growing": return 0
    return max(0, int(p.cherry_tree_ready_at - time.time()))

def tree_kb(p: Player):
    cherry_tree_update(p)
    rows=[]
    if p.cherry_tree_state == "empty":
        rows.append([InlineKeyboardButton(text="Грядка: 🟫 Пусто",callback_data="tree:plant")])
    elif p.cherry_tree_state == "growing":
        rows.append([InlineKeyboardButton(text=f"⏳ До созревания: {_growth_clock(cherry_tree_time(p))}",callback_data="tree:refresh")])
        if p.axe>0:
            rows.append([InlineKeyboardButton(text="🪓 Срубить дерево",callback_data="tree:chop_confirm")])
    elif p.cherry_tree_state == "ready":
        rows.append([InlineKeyboardButton(text="🍒 Собрать вишню",callback_data="tree:harvest")])
        rows.append([InlineKeyboardButton(text=f"🪓 Срубить дерево (топоров: {p.axe})",callback_data="tree:chop_confirm")])
    rows.append([InlineKeyboardButton(text="⬅️ К ферме",callback_data="farm:list")])
    return inline(rows)

def tree_text(p: Player, notice="") -> str:
    cherry_tree_update(p)
    lines=["🌳 Вишнёвое дерево", ""]
    if p.cherry_tree_state=="empty":
        lines += ["🟫 Участок свободен.", "", "Посадите семя вишни, чтобы вырастить дерево."]
    elif p.cherry_tree_state=="growing":
        left=cherry_tree_time(p)
        lines += ["🌱 Молодое дерево растёт.", f"⏳ До созревания: {_growth_clock(left)}", "", "🍒 После созревания дерево даст 5–8 вишен."]
    else:
        lines += ["🌳 Дерево созрело и готово к сбору!", "", "🍒 Урожай: 5–8 вишен", "♻️ После сбора новое созревание займёт 6 ч."]
    lines += ["", f"🍒 Собрано вишен всего: {p.cherries_harvested}", f"🪵 Древесина: {p.wood}", f"🪓 Топориков: {p.axe}"]
    if notice: lines=[notice, ""]+lines
    return "\n".join(lines)

async def show_tree(c, notice=""):
    p=get_player(c.from_user.id); cherry_tree_update(p); await c.answer(); await c.message.edit_text(tree_text(p,notice),reply_markup=tree_kb(p))

@dp.callback_query(F.data=="tree:open")
async def tree_open(c): await show_tree(c)

@dp.callback_query(F.data=="tree:refresh")
async def tree_refresh(c): await show_tree(c)

@dp.callback_query(F.data=="tree:plant")
async def tree_plant(c):
    p=get_player(c.from_user.id)
    if p.cherry_tree_state!="empty": return await show_tree(c,"❌ Здесь уже растёт дерево.")
    if p.inventory.get("Семя вишни",0)<=0: return await show_tree(c,"❌ У вас нет семени вишни.")
    p.inventory["Семя вишни"]-=1; p.cherry_tree_state="growing"; p.cherry_tree_planted_at=time.time(); p.cherry_tree_ready_at=time.time()+CHERRY_GROWTH; save_data()
    await show_tree(c,"🌱 Семя посажено! Через 2 часа вырастет вишнёвое дерево.")

@dp.callback_query(F.data=="tree:harvest")
async def tree_harvest(c):
    p=get_player(c.from_user.id); cherry_tree_update(p)
    if p.cherry_tree_state!="ready": return await show_tree(c,"⏳ Дерево ещё не созрело.")
    amount=random.randint(CHERRY_YIELD_MIN,CHERRY_YIELD_MAX); p.inventory["Вишни"]=p.inventory.get("Вишни",0)+amount; p.cherries_harvested+=amount
    p.cherry_tree_state="growing"; p.cherry_tree_ready_at=time.time()+CHERRY_REGROWTH; p.cherry_tree_planted_at=time.time(); save_data()
    await lumberjack_check(p); save_data(); await show_tree(c,f"🍒 Собрано {amount} вишен! Следующий урожай через 6 ч.")

@dp.callback_query(F.data=="tree:chop_confirm")
async def tree_chop_confirm(c):
    p=get_player(c.from_user.id); cherry_tree_update(p)
    if p.cherry_tree_state=="empty": return await show_tree(c,"❌ Здесь нет дерева.")
    if p.axe<=0: return await show_tree(c,"🪓 Для рубки нужен Топорик. Его нельзя купить — получайте его за особое задание или редкие события.")
    rows=[[InlineKeyboardButton(text="🪓 Да, срубить",callback_data="tree:chop")],
          [InlineKeyboardButton(text="⬅️ Оставить дерево",callback_data="tree:open")]]
    text=tree_text(p)+"\n\n⚠️ Срубить дерево?\n🪓 Будет потрачен 1 Топорик.\n🪵 Вы получите 3 древесины.\n🍒 Дерево исчезнет, и для нового понадобится ещё одно семя."
    await c.answer(); await c.message.edit_text(text,reply_markup=inline(rows))

@dp.callback_query(F.data=="tree:chop")
async def tree_chop(c):
    p=get_player(c.from_user.id)
    if p.cherry_tree_state=="empty": return await show_tree(c,"❌ Дерево уже срублено.")
    if p.axe<=0: return await show_tree(c,"❌ У вас нет Топорика.")
    p.axe-=1; p.wood+=CHERRY_WOOD_YIELD; p.cherry_tree_state="empty"; p.cherry_tree_planted_at=0.0; p.cherry_tree_ready_at=0.0; save_data()
    await show_tree(c,"🪓 Дерево срублено! Получено 🪵 3 древесины. Топорик потрачен.")

# ---------- FLOWERBED / PIXEL ART ----------
FLOWERBED_MAX_LINES = 40
FLOWERBED_SLOTS_PER_LINE = 10
FLOWERBED_LINE_COIN_COST = 100000
FLOWERBED_LINE_WOOD_COST = 1


def normalize_flowerbed(p: Player):
    if not isinstance(p.flowerbed_lines, list) or not p.flowerbed_lines:
        p.flowerbed_lines = [[""] * FLOWERBED_SLOTS_PER_LINE]
    normalized=[]
    for line in p.flowerbed_lines[:FLOWERBED_MAX_LINES]:
        line=list(line) if isinstance(line,list) else []
        normalized.append((line[:FLOWERBED_SLOTS_PER_LINE] + [""] * FLOWERBED_SLOTS_PER_LINE)[:FLOWERBED_SLOTS_PER_LINE])
    p.flowerbed_lines=normalized or [[""] * FLOWERBED_SLOTS_PER_LINE]


def flowerbed_used_slots(p: Player, line_index: int) -> int:
    normalize_flowerbed(p)
    if line_index < 0 or line_index >= len(p.flowerbed_lines):
        return 0
    return sum(1 for x in p.flowerbed_lines[line_index] if x)


def flowerbed_art(p: Player) -> str:
    normalize_flowerbed(p)
    rows=[]
    for line in p.flowerbed_lines:
        rows.append("".join(crop_emoji(x) if x else "🟫" for x in line))
    return "\n".join(rows)


def flowerbed_kb(p: Player):
    normalize_flowerbed(p)
    rows=[]
    for i in range(len(p.flowerbed_lines)):
        used=flowerbed_used_slots(p,i)
        rows.append([InlineKeyboardButton(text=f"✏️ Линия {i+1} ({used}/10)", callback_data=f"flowerbed:line:{i}")])
    if len(p.flowerbed_lines) < FLOWERBED_MAX_LINES:
        next_cost=f"{fmt_num(FLOWERBED_LINE_COIN_COST)} 💰 + {FLOWERBED_LINE_WOOD_COST} 🪵"
        rows.append([InlineKeyboardButton(text=f"➕ Открыть линию {len(p.flowerbed_lines)+1} — {next_cost}", callback_data="flowerbed:unlock")])
    rows.append([InlineKeyboardButton(text="⬅️ К ферме", callback_data="farm:list")])
    return inline(rows)


def flowerbed_text(p: Player, notice="") -> str:
    normalize_flowerbed(p)
    used=sum(flowerbed_used_slots(p,i) for i in range(len(p.flowerbed_lines)))
    next_line=len(p.flowerbed_lines)+1
    lines=["🌸 Клумба", "", "Ваш цветочный пиксель-арт:", "", flowerbed_art(p), "", "📊 Статистика клумбы:",
           f"• Линий: {len(p.flowerbed_lines)}/{FLOWERBED_MAX_LINES}",
           f"• Использовано слотов: {used}/{len(p.flowerbed_lines)*FLOWERBED_SLOTS_PER_LINE}",
           f"• 🌸 Собрано цветов: {p.flower_harvested}",
           f"• 🪵 Древесина: {p.wood}"]
    if len(p.flowerbed_lines) < FLOWERBED_MAX_LINES:
        lines += ["", f"💰 Следующая линия {next_line}:",
                  f"• Стоимость: {fmt_num(FLOWERBED_LINE_COIN_COST)} 💰 + {FLOWERBED_LINE_WOOD_COST} 🪵",
                  f"• У вас есть: {fmt_num(p.coins)} 💰 + {p.wood} 🪵"]
    if notice:
        lines = [notice, ""] + lines
    return "\n".join(lines)


async def show_flowerbed(c, notice=""):
    p=get_player(c.from_user.id)
    await c.answer()
    await c.message.edit_text(flowerbed_text(p, notice), reply_markup=flowerbed_kb(p))


@dp.callback_query(F.data=="flowerbed:open")
async def flowerbed_open(c):
    await show_flowerbed(c)


@dp.callback_query(F.data=="flowerbed:unlock")
async def flowerbed_unlock(c):
    p=get_player(c.from_user.id)
    normalize_flowerbed(p)
    if len(p.flowerbed_lines) >= FLOWERBED_MAX_LINES:
        return await show_flowerbed(c, "🌸 Клумба уже полностью открыта — 40 линий.")
    if p.coins < FLOWERBED_LINE_COIN_COST:
        return await show_flowerbed(c, f"❌ Недостаточно монет. Нужно {fmt_num(FLOWERBED_LINE_COIN_COST)} 💰.")
    if p.wood < FLOWERBED_LINE_WOOD_COST:
        return await show_flowerbed(c, f"❌ Недостаточно древесины. Нужно {FLOWERBED_LINE_WOOD_COST} 🪵. Получите древесину, срубив вишнёвое дерево с помощью 🪓 Топорика.")
    p.coins -= FLOWERBED_LINE_COIN_COST
    p.wood -= FLOWERBED_LINE_WOOD_COST
    p.flowerbed_lines.append([""] * FLOWERBED_SLOTS_PER_LINE)
    save_data()
    await show_flowerbed(c, f"🌸 Открыта линия {len(p.flowerbed_lines)}!")


@dp.callback_query(F.data.startswith("flowerbed:line:"))
async def flowerbed_line(c):
    p=get_player(c.from_user.id); normalize_flowerbed(p)
    line=int(c.data.split(":")[2])
    if line < 0 or line >= len(p.flowerbed_lines):
        return await show_flowerbed(c, "❌ Линия не найдена.")
    cells="".join(crop_emoji(x) if x else "🟫" for x in p.flowerbed_lines[line])
    rows=[]
    for start in (0,5):
        rows.append([InlineKeyboardButton(text=(crop_emoji(p.flowerbed_lines[line][i]) if p.flowerbed_lines[line][i] else "🟫"), callback_data=f"flower:slot:{line}:{i}") for i in range(start,start+5)])
    rows.append([InlineKeyboardButton(text="⬅️ К клумбе", callback_data="flowerbed:open")])
    text=f"🌸 Редактирование линии {line+1}\n\nТекущий вид:\n{cells}\n\nВыберите позицию для редактирования (1–10):"
    await c.answer(); await c.message.edit_text(text, reply_markup=inline(rows))


@dp.callback_query(F.data.startswith("flower:slot:"))
async def flower_slot(c):
    p=get_player(c.from_user.id); normalize_flowerbed(p)
    _,_,line_s,slot_s=c.data.split(":"); line=int(line_s); slot=int(slot_s)
    if line<0 or line>=len(p.flowerbed_lines) or slot<0 or slot>=10:
        return await show_flowerbed(c,"❌ Слот не найден.")
    current=p.flowerbed_lines[line][slot]
    rows=[]
    owned=[]
    for flower in FLOWER_CROPS:
        count=p.inventory.get("Урожай "+flower,0)
        if count>0 or current==flower:
            owned.append(flower)
    for flower in owned:
        count=p.inventory.get("Урожай "+flower,0)
        label=f"{crop_emoji(flower)} {flower} ({count})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"flower:set:{line}:{slot}:{flower}")])
    if current:
        rows.append([InlineKeyboardButton(text="🗑️ Очистить слот", callback_data=f"flower:clear:{line}:{slot}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к линии", callback_data=f"flowerbed:line:{line}")])
    rows.append([InlineKeyboardButton(text="🏡 К клумбе", callback_data="flowerbed:open")])
    text=f"🌸 Слот {slot+1} в линии {line+1}\n\n"
    if current:
        text += f"Текущий цветок: {crop_emoji(current)} {current}\n\n"
    else:
        text += "Слот пустой.\n\n"
    if owned:
        text += "Выберите цветок для высаживания:"
    else:
        text += "У вас нет цветов для высаживания.\nВыращивайте цветы на грядках, чтобы использовать их в клумбе."
    await c.answer(); await c.message.edit_text(text, reply_markup=inline(rows))


@dp.callback_query(F.data.startswith("flower:set:"))
async def flower_set(c):
    p=get_player(c.from_user.id); normalize_flowerbed(p)
    parts=c.data.split(":"); line=int(parts[2]); slot=int(parts[3]); flower=parts[4]
    if flower not in FLOWER_CROPS or line>=len(p.flowerbed_lines) or slot>=10:
        return await show_flowerbed(c,"❌ Цветок или слот не найден.")
    key="Урожай "+flower
    if p.inventory.get(key,0)<=0:
        return await flower_slot(c)
    current=p.flowerbed_lines[line][slot]
    if current==flower:
        return await flower_slot(c)
    if current:
        p.inventory["Урожай "+current]=p.inventory.get("Урожай "+current,0)+1
    p.inventory[key]-=1
    p.flowerbed_lines[line][slot]=flower
    save_data()
    await c.answer("🌸 Цветок установлен!")
    await flowerbed_line(c)


@dp.callback_query(F.data.startswith("flower:clear:"))
async def flower_clear(c):
    p=get_player(c.from_user.id); normalize_flowerbed(p)
    _,_,line_s,slot_s=c.data.split(":"); line=int(line_s); slot=int(slot_s)
    if line>=len(p.flowerbed_lines) or slot>=10:
        return await show_flowerbed(c,"❌ Слот не найден.")
    current=p.flowerbed_lines[line][slot]
    if current:
        p.inventory["Урожай "+current]=p.inventory.get("Урожай "+current,0)+1
        p.flowerbed_lines[line][slot]=""
        save_data()
    await c.answer("🗑️ Слот очищен!")
    await flowerbed_line(c)

@dp.callback_query(F.data.startswith("speed:"))
async def speed(c):
    p=get_player(c.from_user.id); i=int(c.data.split(":")[1]); b=p.beds[i]
    if b.state!="growing": await c.answer(); return await show_farm(c, notice="Здесь нечего ускорять.")
    if p.spheres<=0: await c.answer(); return await show_farm(c, notice="🔮 Нет сфер.")
    p.spheres-=1; b.state="ready"; b.growth_seconds=0; b.planted_at=0; save_data()
    await c.answer(); await show_farm(c, notice="🔮 Рост завершён!")

@dp.callback_query(F.data=="plant:all")
async def plant_all(c):
    p=get_player(c.from_user.id)
    empty_count=sum(1 for b in p.beds if b.state=="empty")
    if empty_count<=0:
        await c.answer(); return await show_farm(c, notice="🟤 Нет свободных грядок.")
    rows=[]
    for crop,d in CROPS.items():
        if crop not in PREMIUM_CROPS and p.level < d["unlock"]:
            continue
        seed_count=p.inventory.get(d["seed"],0)
        # Не показываем культуры, семян которых у пользователя нет.
        if seed_count<=0:
            continue
        emoji=crop_emoji(crop)
        rows.append([InlineKeyboardButton(text=f"{emoji} {crop} — {seed_count} семян",callback_data=f"plant:crop:{crop}")])
    rows.append([InlineKeyboardButton(text="🔙 К ферме",callback_data="farm:list")])
    await c.answer()
    if len(rows)==1:
        return await show_farm(c, notice="🌱 У вас нет купленных семян для посадки.")
    await c.message.edit_text(
        f"🪏 Посадить везде\n\n🟤 Свободных грядок: {empty_count}\n\nВыберите, какие семена посадить:",
        reply_markup=inline(rows)
    )

@dp.callback_query(F.data.startswith("plant:crop:"))
async def plant_crop(c):
    crop=c.data.split(":",2)[2]
    if crop not in CROPS:
        await c.answer(); return await show_farm(c, notice="❌ Культура не найдена.")
    p=get_player(c.from_user.id); d=CROPS[crop]
    if crop not in PREMIUM_CROPS and p.level < d["unlock"]:
        await c.answer(); return await show_farm(c, notice=f"🔒 {crop} откроется с {d['unlock']} уровня.")
    seed=d["seed"]
    available=p.inventory.get(seed,0)
    empty=[b for b in p.beds if b.state=="empty"]
    if available<=0:
        await c.answer(); return await show_farm(c, notice=f"🌱 У вас нет семян: {seed}.")
    if not empty:
        await c.answer(); return await show_farm(c, notice="🟤 Нет свободных грядок.")
    count=min(available,len(empty))
    for b in empty[:count]:
        b.state="growing"; b.crop=crop; b.growth_seconds=effective_growth(p,crop); b.planted_at=time.time(); b.notified=False
    p.inventory[seed]-=count
    save_data()
    emoji=crop_emoji(crop)
    await c.answer(); await show_farm(c, notice=f"{emoji} Посажено: {count} шт. {crop}")

@dp.callback_query(F.data.startswith("plant:bed:"))
async def plant_single_bed(c):
    parts=c.data.split(":",2)
    rest=parts[2].split(":",1)
    i=int(rest[0]); crop=rest[1]
    p=get_player(c.from_user.id)
    if i<0 or i>=len(p.beds):
        await c.answer(); return await show_farm(c, notice="❌ Грядка не найдена.")
    if crop not in CROPS:
        await c.answer(); return await show_farm(c, notice="❌ Культура не найдена.")
    b=p.beds[i]; update_bed(b)
    if b.state!="empty":
        await c.answer(); return await show_farm(c, notice="❌ Эта грядка уже занята.")
    seed=CROPS[crop]["seed"]
    if p.inventory.get(seed,0)<=0:
        await c.answer(); return await show_farm(c, notice=f"🌱 У вас нет семян: {seed}.")
    b.state="growing"
    b.crop=crop
    b.growth_seconds=effective_growth(p,crop)
    b.planted_at=time.time()
    b.notified=False
    p.inventory[seed]-=1
    save_data()
    await c.answer()
    await show_farm(c, notice=f"{crop_emoji(crop)} Посажено: 1 шт. {crop}")

@dp.callback_query(F.data=="harvest:all")
async def harvest_all(c):
    p=get_player(c.from_user.id); totals={}; beds_collected=0; levels=[]
    for i in range(len(p.beds)):
        r=collect_bed(p,i)
        if r:
            crop, rewards, amount = r
            totals[crop]=totals.get(crop,0)+amount
            beds_collected += 1
            levels += rewards
    if not totals:
        await c.answer(); return await show_farm(c, notice="📦 Нет созревшего урожая.")

    emoji_map=CROP_EMOJI
    lines=[f"{emoji_map.get(crop, '🌱')} {crop} × {amount}" for crop, amount in totals.items()]
    effect={"harvest:confetti":"\n🎉 Конфетти!","harvest:fireworks":"\n🎆 Фейерверк!"}.get(active_cosmetic(p,"harvest"),"")
    msg=f"📦 Собрано грядок: {beds_collected}\n\n" + "\n".join(lines) + effect
    if levels:
        msg+=f"\n\n🎉 Новый уровень: {levels[-1][0]}"
    save_data()
    await c.answer()
    await show_farm(c, notice=msg)
    if boss_event["defeated"] and not boss_event.get("victory_notified", False):
        await send_boss_victory_notification()
    await send_level_up_notification(c.from_user.id, levels)

@dp.callback_query(F.data=="farm:water")
async def water(c):
    p=get_player(c.from_user.id)
    now=time.time()
    cooldown_left=int(p.watered_until-now)
    if cooldown_left > 0:
        h, rem = divmod(cooldown_left, 3600)
        m, s = divmod(rem, 60)
        left = f"{h}ч {m}м" if h else (f"{m}м" if m else f"{s}с")
        await c.answer()
        return await show_farm(c, notice=f"⏳ Поливать снова можно через {left}.")

    watered = 0
    for b in p.beds:
        update_bed(b)
        if b.state != "growing":
            continue
        remaining_before = remaining(b)
        if remaining_before <= 0:
            b.state = "ready"
            continue
        # Полив сразу сокращает оставшееся время роста на 30%.
        elapsed = max(0, int(now - b.planted_at))
        new_remaining = max(1, int(remaining_before * 0.70))
        b.planted_at = now - elapsed
        b.growth_seconds = elapsed + new_remaining
        watered += 1

    p.watered_until = now + 1800
    save_data()
    await c.answer()
    if watered:
        await show_farm(c, notice=f"💧 Полито грядок: {watered}. Оставшееся время роста сокращено на 30%!\n⏳ Следующий полив через 30 минут.")
    else:
        await show_farm(c, notice="💧 Сейчас нечего поливать — нет растущих культур.\n⏳ Следующий полив через 30 минут.")

@dp.callback_query(F.data=="farm:list")
async def farm_list(c): await c.answer(); await show_farm(c)
@dp.callback_query(F.data=="farm:back")
async def farm_back(c):
    await c.answer(); await c.message.delete(); await c.message.answer("Выбери раздел меню 👇",reply_markup=main_kb(c.from_user.id))


FISH_LOTTERY_FILE = os.path.join(os.path.dirname(DATA_FILE), "farmworld_fishing.json") if os.path.dirname(DATA_FILE) else "farmworld_fishing.json"
fishing_lottery = {"next_draw": 0.0}

def load_fishing_lottery():
    global fishing_lottery
    if os.path.exists(FISH_LOTTERY_FILE):
        try:
            with open(FISH_LOTTERY_FILE, "r", encoding="utf-8") as f:
                data=json.load(f)
            if isinstance(data,dict): fishing_lottery.update(data)
        except Exception:
            logging.exception("load_fishing_lottery failed")

def save_fishing_lottery():
    try:
        target=os.path.dirname(FISH_LOTTERY_FILE)
        if target: os.makedirs(target,exist_ok=True)
        with open(FISH_LOTTERY_FILE+".tmp","w",encoding="utf-8") as f:
            json.dump(fishing_lottery,f,ensure_ascii=False)
        os.replace(FISH_LOTTERY_FILE+".tmp",FISH_LOTTERY_FILE)
    except Exception:
        logging.exception("save_fishing_lottery failed")

load_fishing_lottery()

# ---------- FRIENDS & NEIGHBORS / FISHING ----------
FISH_DURATION = 12 * 3600
FISH_DARTS = 3
FISH_BAIT_ITEM = "Червяк"
FISH_LOTUS_ITEM = "Семя лотоса"
FISH_VALUES = {
    1: {"emoji": "🐡", "name": "Фугу"},
    2: {"emoji": "🦐", "name": "Креветка"},
    3: {"emoji": "🐟", "name": "Карась"},
    4: {"emoji": "🐠", "name": "Окунь"},
    5: {"emoji": "🦈", "name": "Щука"},
    6: {"emoji": "🐋", "name": "Белуга"},
}
EQUIP_SLOTS = {
    1: ("👜", "Аксессуар"),
    2: ("🧤", "Перчатки"),
    3: ("👟", "Обувь"),
    4: ("👖", "Штаны"),
    5: ("👕", "Верх"),
    6: ("🎩", "Головной убор"),
}
EQUIP_GRADES = ["E", "D", "C", "B", "A"]
EQUIP_CHANCE = {"E": 5, "D": 10, "C": 15, "B": 20, "A": 25}
MERGE_COST = {"E": 50000, "D": 300000, "C": 1500000, "B": 5000000}

def best_equipment_grade(p: Player, slot: int):
    for grade in reversed(EQUIP_GRADES):
        if p.equipment.get(f"{slot}:{grade}",0)>0:
            return grade
    return None

def equipment_double(p: Player, slot: int) -> bool:
    grade=best_equipment_grade(p,slot)
    return bool(grade and random.random() < EQUIP_CHANCE[grade]/100)


def fishing_state(p: Player) -> Dict:
    if not isinstance(p.fishing, dict):
        p.fishing = {}
    return p.fishing


def fishing_active(p: Player) -> bool:
    f = fishing_state(p)
    return bool(f.get("started_at") and f.get("finish_at") and time.time() < float(f["finish_at"]))


def fishing_finished(p: Player) -> bool:
    f = fishing_state(p)
    return bool(f.get("started_at") and f.get("finish_at") and time.time() >= float(f["finish_at"]))


def fishing_points(p: Player) -> int:
    f = fishing_state(p)
    rolls = list(f.get("rolls") or [])
    if not rolls:
        return 0
    try:
        return int("".join(str(int(x)) for x in rolls))
    except Exception:
        return 0


def fishing_display_rolls(p: Player) -> str:
    rolls = list(fishing_state(p).get("rolls") or [])
    return "".join(FISH_VALUES[int(x)]["emoji"] for x in rolls) if rolls else "—"


def fishing_reward_slot(rolls: List[int]) -> int | None:
    if len(rolls) < 3:
        return None
    counts = {n: rolls.count(n) for n in set(rolls)}
    triple = next((n for n,c in counts.items() if c >= 3), None)
    return triple


def add_equipment(p: Player, slot: int, grade: str, count: int = 1) -> None:
    key = f"{slot}:{grade}"
    p.equipment[key] = p.equipment.get(key, 0) + count


def equipment_name(slot: int, grade: str) -> str:
    emoji, name = EQUIP_SLOTS[slot]
    return f"{emoji} {name} [{grade}]"


def fish_equipment_for_rolls(rolls: List[int]) -> tuple[int, str] | None:
    # Three equal digits give E; four equal digits give D. For four darts with
    # exactly three equal digits, the repeated digit determines the slot.
    if len(rolls) >= 4 and len(set(rolls)) == 1:
        return int(rolls[0]), "D"
    if len(rolls) >= 3:
        slot_value = fishing_reward_slot(rolls)
        if slot_value:
            return slot_value, "E"
    return None


def equipment_text(p: Player) -> str:
    lines = ["🏡 Домик\n", "Экипировка и её бонусы:"]
    for slot, (emoji, name) in EQUIP_SLOTS.items():
        parts=[]
        for grade in EQUIP_GRADES:
            n=p.equipment.get(f"{slot}:{grade}",0)
            if n:
                parts.append(f"{grade} × {n}")
        chance=" / ".join(f"[{g}] {EQUIP_CHANCE[g]}%" for g in EQUIP_GRADES)
        lines.append(f"\n{emoji} {name}: {', '.join(parts) if parts else 'нет'}\n✨ Шанс x2: {chance}")
    lines += [
        "\n✨ Бонусы слотов:",
        "🧢 Голова → 🌾 Двойной урожай",
        "👕 Верх → 🏭 Двойная продукция зданий",
        "👖 Штаны → 🐄 Двойной сбор с животных",
        "👟 Обувь → ⛏️ Двойная добыча в шахте",
        "🧤 Перчатки → 🎣 Двойной улов (рыба + экипировка!)",
        "👜 Аксессуар → 🍀 Двойная награда (Жанна/Председатель)",
        "\n💰 Объединение 3 одинаковых:",
        "[E] → [D]: 50.000 💰",
        "[D] → [C]: 300.000 💰",
        "[C] → [B]: 1.500.000 💰",
        "[B] → [A]: 5.000.000 💰",
    ]
    return "\n".join(lines)


def friends_kb():
    return inline([
        [InlineKeyboardButton(text="🔍 Найти соседей", callback_data="friends:neighbors")],
        [InlineKeyboardButton(text="🏆 Рейтинги", callback_data="friends:rating")],
    ])


def friends_text():
    return "👥 Друзья и соседи\n\nОбщайся с другими фермерами, находи соседей и следи за рейтингами."

async def show_friends(c_or_m, text=None):
    text = text or friends_text()
    if isinstance(c_or_m, Message):
        await refresh_main_keyboard(c_or_m.chat.id, c_or_m.from_user.id, active="👥")
        await asyncio.sleep(ACTIVE_SECTION_DELAY)
        await c_or_m.answer(text, reply_markup=friends_kb())
    else:
        c=c_or_m; await c.answer()
        if c.message.photo:
            await c.message.delete()
            await c.message.answer(text, reply_markup=friends_kb())
        else:
            await c.message.edit_text(text, reply_markup=friends_kb())

@dp.message(F.text.in_({"👥", "🔸👥"}))
async def friends(m):
    await show_friends(m)

@dp.callback_query(F.data=="friends:home")
async def friends_home(c):
    await c.answer()
    try:
        if c.message.photo:
            await c.message.delete()
            await c.message.answer(friends_text(), reply_markup=friends_kb())
        else:
            await c.message.edit_text(friends_text(), reply_markup=friends_kb())
    except Exception:
        try:
            await c.message.answer(friends_text(), reply_markup=friends_kb())
        except Exception:
            pass

NEIGHBORS_PER_PAGE = 12

def _neighbor_name(p: Player) -> str:
    return (p.nickname or "Игрок").strip() or "Игрок"

def _short_neighbor_name(name: str, limit: int = 22) -> str:
    name = " ".join(name.split())
    if len(name) <= limit:
        return name
    return name[:max(1, limit - 1)].rstrip() + "…"

def _theft_day_key() -> int:
    # Лимит краж обновляется раз в календарный день по времени сервера.
    return int(time.time() // 86400)

def _refresh_theft_permissions(p: Player) -> None:
    day = _theft_day_key()
    if p.theft_day != day:
        p.theft_day = day
        p.theft_permissions = 4

def _neighbor_bed_label(bed: Bed, index: int) -> str:
    update_bed(bed)
    if bed.state == "empty":
        return f"Грядка {index}: 🟫 Пусто"
    if bed.state == "growing":
        return f"Грядка {index}: 🌱 {crop_emoji(bed.crop)} {bed.crop} · ⏳ {_growth_clock(remaining(bed))}"
    return f"Грядка {index}: ✨ {crop_emoji(bed.crop)} {bed.crop} · ГОТОВО"

def _neighbor_farm_text(p: Player) -> str:
    # Состояние грядок всегда рассчитывается заново при открытии фермы.
    for bed in p.beds:
        update_bed(bed)
    cherry_tree_update(p)
    ready = sum(1 for b in p.beds if b.state == "ready")
    occupied = sum(1 for b in p.beds if b.state != "empty")
    return "\n".join([
        f"🌾 Ферма {_neighbor_name(p)}",
        "",
        f"🏆 Уровень: {p.level}",
        "",
        "🌱 Грядки ниже — нажмите на готовую грядку, чтобы посмотреть урожай.",
        f"📊 Занято: {occupied}/{len(p.beds)} · ✨ Готово: {ready}",
        "",
        "🥷 На этой ферме можно украсть только созревший урожай."
    ])

def _neighbor_farm_keyboard(viewer: Player, owner: Player, owner_id: int):
    _refresh_theft_permissions(viewer)
    rows = []
    for i, bed in enumerate(owner.beds):
        rows.append([InlineKeyboardButton(
            text=_neighbor_bed_label(bed, i + 1),
            callback_data=f"friends:neighborbed:{owner_id}:{i}"
        )])
    rows.append([InlineKeyboardButton(text=f"🥷 Разрешений на кражу: {viewer.theft_permissions}", callback_data="friends:stealinfo")])
    rows.append([InlineKeyboardButton(text="⬅️ К соседям", callback_data="friends:neighbors")])
    rows.append([InlineKeyboardButton(text="🏠 Друзья и соседи", callback_data="friends:home")])
    return inline(rows)

def _neighbor_bed_keyboard(viewer: Player, owner: Player, owner_id: int, bed_index: int):
    _refresh_theft_permissions(viewer)
    bed = owner.beds[bed_index]
    update_bed(bed)
    rows = []
    if bed.state == "ready":
        rows.append([InlineKeyboardButton(text="🥷 Украсть 10 шт.", callback_data=f"friends:steal:{owner_id}:{bed_index}")])
    rows.append([InlineKeyboardButton(text="⬅️ К грядкам соседа", callback_data=f"friends:neighbor:{owner_id}")])
    return inline(rows)


def _neighbors_keyboard(page: int, data: list[tuple[int, Player]]):
    rows = []
    for i in range(0, len(data), 2):
        pair = data[i:i+2]
        row = []
        for uid, p in pair:
            name = _short_neighbor_name(_neighbor_name(p))
            row.append(InlineKeyboardButton(
                text=f"👨‍🌾 {name} · {p.level} ур.",
                callback_data=f"friends:neighbor:{uid}"
            ))
        rows.append(row)

    total_pages = max(1, (len(players) - 1) // NEIGHBORS_PER_PAGE + 1)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"friends:neighbors:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="friends:neighbors:noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"friends:neighbors:{page+1}"))
    if nav and total_pages > 1:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Друзья и соседи", callback_data="friends:home")])
    return inline(rows)

async def _show_neighbors(c, page: int = 0):
    # Себя в список соседей не показываем. Остальные фермеры сортируются по уровню.
    data = [(uid, p) for uid, p in players.items() if uid != c.from_user.id]
    data.sort(key=lambda x: (x[1].level, x[1].earned_coins, _neighbor_name(x[1]).lower()), reverse=True)
    total_pages = max(1, (len(data) + NEIGHBORS_PER_PAGE - 1) // NEIGHBORS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    page_data = data[page * NEIGHBORS_PER_PAGE:(page + 1) * NEIGHBORS_PER_PAGE]

    lines = [
        "🔍 Найти соседей",
        "",
        f"👥 Фермеров в FarmWorld: {len(data)}",
        "",
        "Выберите фермера, чтобы заглянуть на его ферму:",
    ]
    if not page_data:
        lines += ["", "🌱 Пока других фермеров нет."]

    await c.message.edit_text("\n".join(lines), reply_markup=_neighbors_keyboard(page, page_data))

@dp.callback_query(F.data=="friends:neighbors")
async def neighbors(c):
    await c.answer()
    await _show_neighbors(c, 0)

@dp.callback_query(F.data.startswith("friends:neighbors:"))
async def neighbors_page(c):
    parts = c.data.split(":")
    if parts[-1] == "noop":
        return await c.answer()
    try:
        page = int(parts[-1])
    except ValueError:
        return await c.answer("Страница не найдена.")
    await c.answer()
    await _show_neighbors(c, page)

@dp.callback_query(F.data.startswith("friends:neighborbed:"))
async def neighbor_bed(c):
    parts = c.data.split(":")
    if len(parts) != 4:
        return await c.answer("Грядка не найдена.")
    try:
        owner_id = int(parts[2]); bed_index = int(parts[3])
    except ValueError:
        return await c.answer("Грядка не найдена.")
    if owner_id == c.from_user.id or owner_id not in players:
        return await c.answer("Фермер не найден.")
    owner = players[owner_id]
    if bed_index < 0 or bed_index >= len(owner.beds):
        return await c.answer("Грядка не найдена.")
    viewer = get_player(c.from_user.id)
    remember_nickname(viewer, c.from_user)
    bed = owner.beds[bed_index]
    update_bed(bed)
    if bed.state == "empty":
        text = f"🟫 Грядка {bed_index+1}\n\nГрядка пуста."
    elif bed.state == "growing":
        text = f"🌱 Грядка {bed_index+1}\n\n{crop_emoji(bed.crop)} {bed.crop}\n⏳ Осталось: {_growth_clock(remaining(bed))}\n\n🥷 Украсть можно только после созревания."
    else:
        text = f"✨ Грядка {bed_index+1}\n\n{crop_emoji(bed.crop)} {bed.crop}\n\n🌾 Урожай созрел!\n🥷 Его можно украсть."
    await c.answer()
    await c.message.edit_text(text, reply_markup=_neighbor_bed_keyboard(viewer, owner, owner_id, bed_index))

@dp.callback_query(F.data.startswith("friends:steal:"))
async def steal_neighbor_harvest(c):
    parts = c.data.split(":")
    if len(parts) != 4:
        return await c.answer("Грядка не найдена.")
    try:
        owner_id = int(parts[2]); bed_index = int(parts[3])
    except ValueError:
        return await c.answer("Грядка не найдена.")
    thief_id = c.from_user.id
    if owner_id == thief_id or owner_id not in players:
        return await c.answer("Нельзя украсть у себя.")
    thief = get_player(thief_id)
    owner = players[owner_id]
    remember_nickname(thief, c.from_user)
    _refresh_theft_permissions(thief)
    if thief.theft_permissions <= 0:
        await c.answer("🥷 Разрешений на кражу больше нет.", show_alert=True)
        return await c.message.edit_text(
            _neighbor_farm_text(owner),
            reply_markup=_neighbor_farm_keyboard(thief, owner, owner_id)
        )
    if bed_index < 0 or bed_index >= len(owner.beds):
        return await c.answer("Грядка не найдена.")
    bed = owner.beds[bed_index]
    update_bed(bed)
    if bed.state != "ready":
        await c.answer("❌ Урожай уже забрали или он ещё не созрел.", show_alert=True)
        return await c.message.edit_text(
            _neighbor_farm_text(owner),
            reply_markup=_neighbor_farm_keyboard(thief, owner, owner_id)
        )

    crop = bed.crop
    amount = 10
    emoji = crop_emoji(crop)
    # Кража забирает созревшую грядку целиком и даёт фиксированные 10 единиц.
    # Это отдельная механика и не меняет обычную награду за честный сбор.
    owner_key = "Урожай " + crop
    thief.inventory[owner_key] = thief.inventory.get(owner_key, 0) + amount
    level_rewards = add_xp(thief, 10)
    thief.theft_permissions -= 1
    bed.state = "empty"
    bed.crop = ""
    bed.growth_seconds = 0
    bed.planted_at = 0.0
    bed.notified = False
    save_data()

    remaining_permissions = thief.theft_permissions
    await c.answer("🥷 Урожай украден!")
    await c.message.edit_text(
        f"🥷 Вы успешно украли урожай\n\n{emoji} {crop}: + {amount} шт.\n🔷 Опыт: +10\n📜 Разрешений осталось: {remaining_permissions}",
        reply_markup=inline([
            [InlineKeyboardButton(text="⬅️ К грядкам соседа", callback_data=f"friends:neighbor:{owner_id}")],
            [InlineKeyboardButton(text="🏠 Друзья и соседи", callback_data="friends:home")],
        ])
    )
    try:
        await bot.send_message(
            owner_id,
            f"❌ У вас украли урожай!\n\n{emoji} {crop}: -{amount} шт.\n\n🥷 Фермер: { _neighbor_name(thief) }"
        )
    except Exception:
        logging.exception("failed to notify victim about theft")
    await send_level_up_notification(thief_id, level_rewards)

@dp.callback_query(F.data == "friends:stealinfo")
async def steal_info(c):
    viewer = get_player(c.from_user.id)
    _refresh_theft_permissions(viewer)
    save_data()
    await c.answer(f"📜 Осталось разрешений: {viewer.theft_permissions}", show_alert=True)

@dp.callback_query(F.data.startswith("friends:neighbor:"))
async def neighbor_farm(c):
    try:
        uid = int(c.data.split(":")[-1])
    except ValueError:
        return await c.answer("Фермер не найден.")
    if uid == c.from_user.id or uid not in players:
        return await c.answer("Фермер не найден.")
    viewer = get_player(c.from_user.id)
    remember_nickname(viewer, c.from_user)
    owner = players[uid]
    save_data()
    await c.answer()
    await c.message.edit_text(_neighbor_farm_text(owner), reply_markup=_neighbor_farm_keyboard(viewer, owner, uid))

@dp.callback_query(F.data=="friends:lake")
async def lake(c, notice=""):
    p=get_player(c.from_user.id); f=fishing_state(p)
    if p.level < 5:
        text=f"🎣 Озеро\n\n🔒 Рыбалка доступна с 5 уровня.\nТвой уровень: {p.level}."
        kb=inline([[InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")]])
        await c.answer(); return await c.message.edit_text(text, reply_markup=kb)
    if f.get("started_at") and len(list(f.get("rolls") or [])) < 3:
        rolls=list(f.get("rolls") or [])
        text=f"🎣 Озеро\n\n🎯 Броски: {fishing_display_rolls(p)}\n\n🔵 Нажимай на кнопку и бросай дротики. Осталось: {3-len(rolls)}"
        rows=[[InlineKeyboardButton(text=f"🔵 Бросить дротик ({3-len(rolls)} осталось)",callback_data="fish:dart")],
              [InlineKeyboardButton(text="🔄 Перебросить — 1 🪱",callback_data="fish:reroll")],
              [InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")]]
        await c.answer(); return await c.message.edit_text(text,reply_markup=inline(rows))
    if fishing_active(p):
        left=max(0,int(float(f.get("finish_at",0))-time.time())); h,rem=divmod(left,3600); m=rem//60
        rolls=list(f.get("rolls") or [])
        text=(f"🎣 Озеро\n\n🎯 Броски: {fishing_display_rolls(p)}\n📊 Комбинация: {fishing_points(p) if len(rolls)==3 else 'броски ещё не завершены'}\n"
              f"⏱ Рыбалка: {h}ч {m}м\n")
        rows=[]
        if len(rolls)<3:
            rows.append([InlineKeyboardButton(text=f"🔵 Бросить дротик ({3-len(rolls)} осталось)",callback_data="fish:dart")])
        else:
            rows.append([InlineKeyboardButton(text="➕ Подкормка — 4-й бросок",callback_data="fish:bait")])
            rows.append([InlineKeyboardButton(text="⚡ Ускорить за монеты",callback_data="fish:speed")])
            rows.append([InlineKeyboardButton(text="🔄 Перебросить — 1 🪱",callback_data="fish:reroll")])
        rows.append([InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")])
        if notice: text=f"{notice}\n\n{text}"
        await c.answer(); return await c.message.edit_text(text, reply_markup=inline(rows))
    if fishing_finished(p):
        rolls=list(f.get("rolls") or [])
        combo=fishing_points(p)
        text=f"🎣 Улов готов!\n\n🎯 {fishing_display_rolls(p)}\n🏆 Комбинация: {combo}\n\nМожно забрать улов или использовать подкормку до сбора."
        rows=[]
        if len(rolls)==3 and not f.get("bait_used"):
            rows.append([InlineKeyboardButton(text="➕ Подкормка — 4-й бросок",callback_data="fish:bait")])
        rows.append([InlineKeyboardButton(text="🎁 Забрать улов",callback_data="fish:collect")])
        rows.append([InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")])
        await c.answer(); return await c.message.edit_text(text,reply_markup=inline(rows))
    worms=p.inventory.get(FISH_BAIT_ITEM,0)
    text=(f"🎣 Озеро\n\nМини-игра на рыбалке.\n🎯 3 дротика → комбинация → 12 часов рыбалки.\n\n🪱 Червяки: {worms}\n🔒 Для старта нужен 5 уровень.")
    rows=[]
    if worms>0: rows.append([InlineKeyboardButton(text="🎣 Начать рыбалку — 1 🪱",callback_data="fish:start")])
    else: text += "\n\n❌ Нет червяков. Они попадаются в шахте."
    rows.append([InlineKeyboardButton(text="🏡 Домик",callback_data="friends:house")])
    rows.append([InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")])
    await c.answer(); await c.message.edit_text((f"{notice}\n\n" if notice else "")+text,reply_markup=inline(rows))

@dp.callback_query(F.data=="fish:start")
async def fish_start(c):
    p=get_player(c.from_user.id)
    if p.level<5: return await c.answer("🔒 Рыбалка доступна с 5 уровня.")
    if p.inventory.get(FISH_BAIT_ITEM,0)<1: return await c.answer("🪱 Нужен 1 червяк.")
    p.inventory[FISH_BAIT_ITEM]-=1
    p.fishing={"started_at":time.time(),"finish_at":0.0,"rolls":[],"bait_used":False,"lotus_claimed":False}
    save_data(); await c.answer(); await lake(c)

@dp.callback_query(F.data=="fish:dart")
async def fish_dart(c):
    p=get_player(c.from_user.id); f=fishing_state(p); rolls=list(f.get("rolls") or [])
    if not f.get("started_at") or len(rolls)>=3: return await c.answer("🎯 Броски уже завершены.")
    rolls.append(random.randint(1,6)); f["rolls"]=rolls
    if len(rolls)==3:
        f["finish_at"]=time.time()+FISH_DURATION
    save_data(); await c.answer(f"🎯 Выпало: {rolls[-1]}"); await lake(c)

@dp.callback_query(F.data=="fish:bait")
async def fish_bait(c):
    p=get_player(c.from_user.id); f=fishing_state(p)
    if len(f.get("rolls") or [])<3: return await c.answer("Сначала сделай 3 броска.")
    if f.get("bait_used"): return await c.answer("Подкормка уже использована.")
    essence_keys=[k for k,v in p.inventory.items() if v>0 and "Эссенция" in k]
    if not essence_keys: return await c.answer("💚 Нет эссенции для подкормки.")
    key=essence_keys[0]; p.inventory[key]-=1
    f["bait_used"]=True; f["rolls4"]=random.randint(1,6)
    f["rolls"]=list(f.get("rolls") or [])+[f["rolls4"]]
    save_data(); await c.answer(f"💚 Подкормка использована: выпало {f['rolls4']}"); await lake(c)

@dp.callback_query(F.data=="fish:reroll")
async def fish_reroll(c):
    p=get_player(c.from_user.id); f=fishing_state(p)
    if p.inventory.get(FISH_BAIT_ITEM,0)<1: return await c.answer("🪱 Нужен 1 червяк.")
    if not f.get("started_at") or fishing_finished(p): return await c.answer("Перебросить сейчас нельзя.")
    p.inventory[FISH_BAIT_ITEM]-=1
    f.update({"rolls":[],"rolls4":None,"bait_used":False,"finish_at":0.0})
    save_data(); await c.answer("🔄 Новые 3 броска готовы."); await lake(c)

@dp.callback_query(F.data=="fish:speed")
async def fish_speed(c):
    p=get_player(c.from_user.id); f=fishing_state(p)
    if not fishing_active(p): return await c.answer("Ускорить можно только активную рыбалку.")
    left=max(0,int(float(f.get("finish_at",0))-time.time()))
    # Fixed transparent price: 100 coins per remaining hour, minimum 100.
    cost=max(100, ((left+3599)//3600)*100)
    if p.coins<cost: return await c.answer(f"💰 Нужно {cost:,} монет.",show_alert=False)
    p.coins-=cost; f["finish_at"]=time.time()+300; save_data(); await c.answer("⚡ Рыбалка ускорена до 5 минут!"); await lake(c)

@dp.callback_query(F.data=="fish:collect")
async def fish_collect(c):
    p=get_player(c.from_user.id); f=fishing_state(p)
    if not fishing_finished(p): return await c.answer("⏱️ Улов ещё не готов.")
    rolls=list(f.get("rolls") or []); combo=fishing_points(p)
    fish_value=sum(rolls[:3])
    # A fish item is created from the highest die; equipment is awarded for triples.
    highest=max(rolls[:3]) if rolls else 1
    fish_name=FISH_VALUES[highest]["name"]
    fish_key=f"Рыба: {fish_name}"; p.inventory[fish_key]=p.inventory.get(fish_key,0)+1
    reward=fish_equipment_for_rolls(rolls)
    reward_text=f"🐟 {fish_name} × 1"
    if reward:
        slot,grade=reward; add_equipment(p,slot,grade); reward_text += f"\n🎁 {equipment_name(slot,grade)}"
    # Gloves can double the fishing catch. The chance is based on best owned grade.
    glove_grade=max((g for g in EQUIP_GRADES if p.equipment.get(f"2:{g}",0)), key=lambda g:EQUIP_GRADES.index(g), default=None)
    if glove_grade and random.random()<EQUIP_CHANCE[glove_grade]/100:
        p.inventory[fish_key]+=1
        if reward:
            add_equipment(p, reward[0], reward[1])
        reward_text += "\n🧤 Сработал бонус перчаток: x2 улов (рыба + экипировка)!"
    p.fishing={}; save_data(); await c.answer();
    await c.message.edit_text(f"🎣 Рыбалка завершена!\n\n🎯 {fishing_display_rolls_from(rolls)}\n🏆 Комбинация: {combo}\n\n{reward_text}",reply_markup=inline([[InlineKeyboardButton(text="🎣 В озеро",callback_data="friends:lake")],[InlineKeyboardButton(text="🏡 Домик",callback_data="friends:house")]]))


def fishing_display_rolls_from(rolls):
    return "".join(FISH_VALUES[int(x)]["emoji"] for x in rolls)

@dp.callback_query(F.data=="friends:house")
async def house(c):
    p=get_player(c.from_user.id); await c.answer()
    rows=[]
    for slot,(_,name) in EQUIP_SLOTS.items():
        for grade in EQUIP_GRADES:
            n=p.equipment.get(f"{slot}:{grade}",0)
            if n>=3 and grade in MERGE_COST:
                rows.append([InlineKeyboardButton(text=f"🔗 {name} [{grade}] ×3 → [{EQUIP_GRADES[EQUIP_GRADES.index(grade)+1]}] — {fmt_num(MERGE_COST[grade])} 💰",callback_data=f"equip:merge:{slot}:{grade}")])
    rows.append([InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")])
    await c.message.edit_text(equipment_text(p),reply_markup=inline(rows))

@dp.callback_query(F.data.startswith("equip:merge:"))
async def equipment_merge(c):
    _,_,slot_text,grade=c.data.split(":"); slot=int(slot_text); p=get_player(c.from_user.id)
    if grade not in MERGE_COST or p.equipment.get(f"{slot}:{grade}",0)<3: return await c.answer("❌ Нужно 3 одинаковых предмета.")
    cost=MERGE_COST[grade]
    if p.coins<cost: return await c.answer(f"💰 Нужно {fmt_num(cost)} монет.")
    next_grade=EQUIP_GRADES[EQUIP_GRADES.index(grade)+1]
    p.equipment[f"{slot}:{grade}"]-=3; add_equipment(p,slot,next_grade)
    p.coins-=cost; save_data(); await c.answer("✨ Объединение выполнено!"); await house(c)

# ---------- CITY ----------
def city_kb():
    return inline([[
        InlineKeyboardButton(text="🥬 Рынок",callback_data="city:market")
    ],[
        InlineKeyboardButton(text="📦 Заказы",callback_data="city:orders")
    ],[
        InlineKeyboardButton(text="📜 Задания",callback_data="city:quests")
    ],[
        InlineKeyboardButton(text="🏦 Банк",callback_data="city:bank")
    ],[
        InlineKeyboardButton(text="🔨 Мастерская",callback_data="city:workshop")
    ],[
        InlineKeyboardButton(text="🌳 Сад",callback_data="city:garden")
    ],[
        InlineKeyboardButton(text="🧑‍🌾 NPC",callback_data="city:npc")
    ],[
        InlineKeyboardButton(text="👨‍💼 Председатель СНТ",callback_data="city:chairman")
    ],[
        InlineKeyboardButton(text="🎪 Ивенты",callback_data="city:events")
    ],[
        InlineKeyboardButton(text="⬅️ Назад",callback_data="city:back")
    ]])

def city_text(): return "🚗 Город\n\nЗдесь можно продавать урожай, выполнять задания, получать ежедневные награды, улучшать ферму и общаться с жителями."

async def show_city_message(target, text=None):
    # Всегда восстанавливаем именно городскую картинку. Это важно при возврате
    # из Сада/Босса и других разделов, где медиа сообщения меняется.
    text = text or city_text()
    city_photo = FSInputFile("city.jpg")
    if isinstance(target, Message):
        # Сначала ставим 🔸 на кнопку города, затем открываем сам город.
        await refresh_main_keyboard(target.chat.id, target.from_user.id, active="🚗")
        await asyncio.sleep(ACTIVE_SECTION_DELAY)
        await target.answer_photo(photo=city_photo, caption=text, reply_markup=city_kb())
    else:
        c = target
        if c.message.photo:
            try:
                await c.message.edit_media(
                    media=InputMediaPhoto(media=city_photo, caption=text),
                    reply_markup=city_kb()
                )
            except Exception:
                # Если заменить фото не удалось, хотя бы восстанавливаем текст/кнопки.
                await c.message.edit_caption(caption=text, reply_markup=city_kb())
        else:
            await c.message.delete()
            await c.message.answer_photo(photo=city_photo, caption=text, reply_markup=city_kb())

async def edit_garden_content(c, text, reply_markup):
    """Показывает раздел сада с отдельной картинкой сада."""
    garden_photo = FSInputFile("garden.jpg")
    if c.message.photo:
        try:
            await c.message.edit_media(
                media=InputMediaPhoto(media=garden_photo, caption=text),
                reply_markup=reply_markup
            )
        except Exception:
            await c.message.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await c.message.delete()
        await c.message.answer_photo(photo=garden_photo, caption=text, reply_markup=reply_markup)

async def edit_market_content(c, text, reply_markup):
    """Показывает рынок с отдельной картинкой рынка."""
    market_photo = FSInputFile("market.jpg")
    if c.message.photo:
        try:
            await c.message.edit_media(
                media=InputMediaPhoto(media=market_photo, caption=text),
                reply_markup=reply_markup
            )
        except Exception:
            await c.message.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await c.message.delete()
        await c.message.answer_photo(photo=market_photo, caption=text, reply_markup=reply_markup)

async def edit_rating_content(c, text, reply_markup):
    """Показывает рейтинг с отдельной картинкой рейтинга."""
    rating_photo = FSInputFile("rating.jpg")
    if c.message.photo:
        try:
            await c.message.edit_media(
                media=InputMediaPhoto(media=rating_photo, caption=text),
                reply_markup=reply_markup
            )
        except Exception:
            await c.message.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await c.message.delete()
        await c.message.answer_photo(photo=rating_photo, caption=text, reply_markup=reply_markup)

async def edit_boss_rating_content(c, text, reply_markup):
    """Показывает рейтинг урона по боссу с отдельной картинкой награждения."""
    rating_photo = FSInputFile("boss_rating.jpg")
    if c.message.photo:
        try:
            await c.message.edit_media(
                media=InputMediaPhoto(media=rating_photo, caption=text),
                reply_markup=reply_markup
            )
        except Exception:
            await c.message.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await c.message.answer_photo(photo=rating_photo, caption=text, reply_markup=reply_markup)

async def edit_event_content(c, text, reply_markup):
    """Показывает главный экран 🎪 Ивенты с отдельной картинкой события."""
    event_photo = FSInputFile("event.jpg")
    if c.message.photo:
        try:
            await c.message.edit_media(
                media=InputMediaPhoto(media=event_photo, caption=text),
                reply_markup=reply_markup
            )
        except Exception:
            await c.message.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await c.message.delete()
        await c.message.answer_photo(photo=event_photo, caption=text, reply_markup=reply_markup)

async def edit_city_content(c, text, reply_markup):
    # Все разделы города используют городскую картинку.
    # Если пользователь пришёл сюда из раздела босса, заменяем boss.jpg обратно на city.jpg.
    if c.message.photo:
        current_photo = c.message.photo[-1].file_id
        city_photo = FSInputFile("city.jpg")
        try:
            await c.message.edit_media(
                media=InputMediaPhoto(media=city_photo, caption=text),
                reply_markup=reply_markup
            )
        except Exception:
            # Если Telegram не позволяет заменить медиа (например, медиа уже совпадает),
            # просто обновляем подпись и клавиатуру.
            await c.message.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await c.message.edit_text(text, reply_markup=reply_markup)

@dp.message(F.text.in_({"🚗", "🔸🚗"}))
async def city(m): await show_city_message(m)

@dp.callback_query(F.data=="city:back")
async def city_back(c):
    await c.answer()
    try:
        await c.message.delete()
    except Exception:
        pass
    await c.message.answer("Выбери раздел меню 👇", reply_markup=main_kb(c.from_user.id))

@dp.callback_query(F.data=="city:home")
async def city_home(c):
    await c.answer()
    await show_city_message(c)

def sale_xp(crop: str, amount: int) -> int:
    # XP за продажу зависит от культуры: чем дороже семя, тем больше XP за 1 проданный урожай.
    return CROPS[crop].get("sale_xp", 1) * amount

# ---------- MARKET ----------
def sale_item_rows(p):
    rows=[]; total=0
    for crop,d in CROPS.items():
        if d.get("flower"):
            continue
        n=p.inventory.get("Урожай "+crop,0)
        if n > 0:
            val=n*d["sell"]; total+=val
            rows.append([InlineKeyboardButton(text=f"{crop_emoji(crop)} {crop} ({n}) за {val} 💰",callback_data=f"market:{crop}")])
    for item,d in APPLE_SALES.items():
        n=p.inventory.get(item,0)
        if n > 0:
            val=n*d["sell"]; total+=val
            rows.append([InlineKeyboardButton(text=f"{d['emoji']} {item} ({n}) за {val} 💰",callback_data=f"market:item:{item}")])
    for item,d in APPLE_PRODUCTS.items():
        n=p.inventory.get(item,0)
        if n > 0:
            val=n*d["sell"]; total+=val
            rows.append([InlineKeyboardButton(text=f"{d['emoji']} {item} ({n}) за {val} 💰",callback_data=f"market:item:{item}")])
    return rows,total

def market_view(p, notice=""):
    rows,total=sale_item_rows(p)
    rows += [[InlineKeyboardButton(text=f"💰 Продать всё за {total} монет",callback_data="market:sellall")],[InlineKeyboardButton(text="⬅️ Назад",callback_data="city:home")]]
    text=f"🥬 Рынок\n💰 Общая стоимость: {total} монет\n\nВыбери урожай, яблоки или продукты переработки для продажи."
    if notice: text=f"{notice}\n\n{text}"
    return text,inline(rows)

@dp.callback_query(F.data=="city:market")
async def market(c):
    p=get_player(c.from_user.id); text,kb=market_view(p); await c.answer(); await edit_market_content(c, text, kb)

@dp.callback_query(F.data.startswith("market:"))
async def market_sell(c):
    p=get_player(c.from_user.id); data=c.data
    sold_lines=[]; value=0; xp_gain=0; sold=False
    if data=="market:sellall":
        for crop,d in CROPS.items():
            if d.get("flower"):
                continue
            key="Урожай "+crop; n=p.inventory.get(key,0)
            if n:
                sold=True; value+=n*d["sell"]; xp_gain+=sale_xp(crop,n); sold_lines.append(f"{crop} × {n}"); p.inventory[key]=0
        for item,d in APPLE_SALES.items():
            n=p.inventory.get(item,0)
            if n:
                sold=True; value+=n*d["sell"]; xp_gain+=d["xp"]*n; sold_lines.append(f"{item} × {n}"); p.inventory[item]=0
        for item,d in APPLE_PRODUCTS.items():
            n=p.inventory.get(item,0)
            if n:
                sold=True; value+=n*d["sell"]; xp_gain+=d["xp"]*n; sold_lines.append(f"{item} × {n}"); p.inventory[item]=0
    elif data.startswith("market:item:"):
        item=data.split(":",2)[2]
        if item in APPLE_SALES:
            d=APPLE_SALES[item]
        elif item in APPLE_PRODUCTS:
            d=APPLE_PRODUCTS[item]
        else:
            return await c.answer("Товар недоступен.")
        n=p.inventory.get(item,0)
        if n<=0:
            await c.answer(); text,kb=market_view(p,"📦 Нет такого товара."); return await edit_market_content(c,text,kb)
        sold=True; value=n*d["sell"]; xp_gain=d["xp"]*n; sold_lines.append(f"{item} × {n}"); p.inventory[item]=0
    else:
        kind=data.split(":",1)[1]
        if kind not in CROPS or CROPS[kind].get("flower"): return
        key="Урожай "+kind; n=p.inventory.get(key,0)
        if n<=0:
            await c.answer(); text,kb=market_view(p,"📦 Нет такого урожая."); return await edit_market_content(c,text,kb)
        value=n*CROPS[kind]["sell"]; xp_gain=sale_xp(kind,n); sold=True; sold_lines.append(f"{kind} × {n}"); p.inventory[key]=0
    if not sold:
        await c.answer(); text,kb=market_view(p,"📦 Нечего продавать."); return await edit_market_content(c,text,kb)
    p.coins+=value; p.earned_coins+=value; levels=add_xp(p,xp_gain); save_data(); await c.answer()
    level_notice=f"\n🎉 Новый уровень: {levels[-1][0]}!\n🎁 Награда доступна в профиле." if levels else ""
    text,kb=market_view(p,notice=f"💰 Продано на {value} монет!\n⭐ +{xp_gain} XP\n\n{'\n'.join(sold_lines)}{level_notice}")
    await edit_market_content(c,text,kb)
    await send_level_up_notification(c.from_user.id, levels)


# ---------- SHOP ----------
def shop_caption(p): return f"🛒 Магазин\n\n💰 {p.coins} монет\n🔮 {p.spheres} сфер\n\nВыберите раздел."
FREE_CROPS = ["Пшеница", "Морковь", "Картофель", "Помидор", "Огурец", "Клубника"]
PREMIUM_CROPS = ["Кукуруза", "Тыква", "Баклажан", "Виноград", "Арбуз", "Роза", "Тюльпан"]
FLOWER_CROPS = ["Роза", "Тюльпан"]

def fmt_num(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")

def growth_text(seconds: int) -> str:
    seconds = int(seconds)
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч"
    if seconds % 60 == 0:
        return f"{seconds // 60} мин."
    return f"{seconds} сек."

ORDINARY_SEED_PRICES = {
    1: lambda base: base,
    10: lambda base: base * 9,
    100: lambda base: base * 90,
}

PREMIUM_SEED_PRICES = {
    "Кукуруза": {1: 1, 10: 3, 50: 15, 100: 30},
    "Тыква": {1: 2, 10: 5, 50: 25, 100: 50},
    "Баклажан": {1: 3, 10: 8, 50: 40, 100: 80},
    "Виноград": {1: 5, 10: 45, 50: 225, 100: 450},
    "Арбуз": {1: 10, 10: 90, 50: 450, 100: 900},
    "Роза": {1: 3, 10: 15, 50: 60, 100: 100},
    "Тюльпан": {1: 200, 10: 1800, 50: 9000, 100: 18000},
}

def seeds_kb(p):
    rows=[]
    for crop in FREE_CROPS:
        rows.append([InlineKeyboardButton(text=f"{crop_emoji(crop)} {crop} ({fmt_num(CROPS[crop]['price'])} 💰)",callback_data=f"buy:{crop}")])
    rows.append([InlineKeyboardButton(text="🍒 Вишня-саженец 10.000 💰",callback_data="shop:trees")])
    rows.append([InlineKeyboardButton(text="✨ Премиум-семена",callback_data="premium:seeds")])
    rows.append([InlineKeyboardButton(text="🔙 В магазин",callback_data="shop:main")]); return inline(rows)

def seed_qty_kb(crop):
    d=CROPS[crop]; rows=[]
    for n in (1,10,100):
        total=ORDINARY_SEED_PRICES[n](d['price'])
        rows.append([InlineKeyboardButton(text=f"{n} шт. — {fmt_num(total)} 💰",callback_data=f"buyq:{crop}:{n}")])
    rows.append([InlineKeyboardButton(text="🔙 К семенам",callback_data="shop:seeds")]); return inline(rows)

def premium_seed_qty_kb(crop):
    rows=[]
    for n in (1,10,50,100):
        total=PREMIUM_SEED_PRICES[crop][n]
        rows.append([InlineKeyboardButton(text=f"{n} шт. — {fmt_num(total)} 🔮",callback_data=f"buy_pq:{crop}:{n}")])
    rows.append([InlineKeyboardButton(text="🔙 Премиум-семена",callback_data="premium:seeds")]); return inline(rows)

def beds_kb(p):
    price = bed_price(p)
    return inline([[InlineKeyboardButton(text=f"Купить грядку ({price} 💰)",callback_data="buy:bed")],[InlineKeyboardButton(text="⬅️ Назад",callback_data="shop:main")]])

@dp.message(F.text.in_({"🛒", "🔸🛒"}))
async def shop(m):
    p=get_player(m.from_user.id)
    # Сначала обновляем клавиатуру, затем открываем магазин.
    await refresh_main_keyboard(m.chat.id, m.from_user.id, active="🛒")
    await asyncio.sleep(ACTIVE_SECTION_DELAY)
    await m.answer_photo(photo=FSInputFile("shop.jpg"),caption=shop_caption(p),reply_markup=shop_kb())

@dp.callback_query(F.data=="shop:main")
async def shop_main(c): p=get_player(c.from_user.id); await c.answer(); await c.message.edit_caption(caption=shop_caption(p),reply_markup=shop_kb())
@dp.callback_query(F.data=="shop:seeds")
async def shop_seeds(c, notice=""):
    p=get_player(c.from_user.id); await c.answer(); caption=(f"{notice}\n\n" if notice else "")+"🌱 Семена\n\n💰 Баланс: %s монет\n\nПервые 6 культур доступны всем без ограничений по уровню."%p.coins; await c.message.edit_caption(caption=caption,reply_markup=seeds_kb(p))
@dp.callback_query(F.data=="shop:beds")
async def shop_beds(c, notice=""):
    p=get_player(c.from_user.id); await c.answer(); price=bed_price(p); caption=(f"{notice}\n\n" if notice else "")+f"🏡 Магазин грядок\n\n💰 Баланс: {p.coins}\n🟤 Грядок: {len(p.beds)}\n\nСтоимость следующей: {price} 💰"; await c.message.edit_caption(caption=caption,reply_markup=beds_kb(p))
@dp.callback_query(F.data=="buy:bed")
async def buy_bed(c):
    p=get_player(c.from_user.id)
    price=bed_price(p)
    if p.coins<price: return await shop_beds(c, notice=f"💰 Недостаточно монет. Нужно {price} 💰.")
    p.coins-=price; p.beds.append(Bed()); save_data(); await shop_beds(c, notice=f"🏡 Новая грядка куплена за {price} 💰!")
@dp.callback_query(F.data.startswith("buy:") )
async def buy_crop(c):
    crop=c.data.split(":",1)[1]
    if crop not in CROPS: return
    p=get_player(c.from_user.id); d=CROPS[crop]
    if crop not in FREE_CROPS:
        return
    await c.answer(); await c.message.edit_caption(caption=f"{crop_emoji(crop)} {crop}\n\n💰 Цена: {fmt_num(d['price'])} за 1 семя\n⏱ Рост: {growth_text(d['growth'])}\n🌾 Урожай: {d['yield_min']}–{d['yield_max']} шт.\n⭐ Опыт: {d['xp']} XP\n💰 Продажа: {fmt_num(d['sell'])} за 1 шт.",reply_markup=seed_qty_kb(crop))
@dp.callback_query(F.data.startswith("buyq:"))
async def buy_qty(c):
    _,crop,ntext=c.data.split(":"); n=int(ntext); p=get_player(c.from_user.id); d=CROPS[crop]; total=ORDINARY_SEED_PRICES[n](d['price'])
    if p.coins<total: return await shop_seeds(c, notice="💰 Недостаточно монет.")
    p.coins-=total; key=d['seed']; p.inventory[key]=p.inventory.get(key,0)+n; save_data(); await shop_seeds(c, notice=f"🌱 Куплено {n} семян.")

CHERRY_SEED_PRICES = {1: 10000, 10: 90000, 100: 900000}

def cherry_seed_qty_kb():
    rows=[]
    for n, total in CHERRY_SEED_PRICES.items():
        rows.append([InlineKeyboardButton(text=f"{n} шт. — {fmt_num(total)} 💰", callback_data=f"buy_cherry:{n}")])
    rows.append([InlineKeyboardButton(text="⬅️ К семенам", callback_data="shop:seeds")])
    return inline(rows)

@dp.callback_query(F.data=="shop:trees")
async def shop_trees(c, notice=""):
    p=get_player(c.from_user.id); await c.answer()
    owned=p.inventory.get("Семя вишни",0)
    text=(f"{notice}\n\n" if notice else "")+f"🍒 Вишня-саженец\n\n💰 Баланс: {fmt_num(p.coins)} монет\n\n🌱 Саженец вишни выращивает вишнёвое дерево.\n⏱ Первое созревание: 2 ч\n🍒 Урожай: 5–8 вишен\n♻️ После сбора дерево снова плодоносит через 6 ч\n🪓 Срубить можно только с 🪓 Топориком\n\n📦 У вас саженцев: {owned}\n\nВыберите количество:"
    await c.message.edit_caption(caption=text, reply_markup=cherry_seed_qty_kb())

@dp.callback_query(F.data.startswith("buy_cherry:"))
async def buy_cherry_seed(c):
    n=int(c.data.split(":",1)[1])
    total=CHERRY_SEED_PRICES.get(n)
    if total is None: return
    p=get_player(c.from_user.id)
    if p.coins<total:
        return await shop_trees(c, f"❌ Недостаточно монет. Нужно {fmt_num(total)} 💰.")
    p.coins-=total
    p.inventory["Семя вишни"]=p.inventory.get("Семя вишни",0)+n
    save_data()
    await shop_trees(c, f"🌱 Куплено саженцев вишни: {n} шт. за {fmt_num(total)} 💰!")

# ---------- PREMIUM / VIP ----------
def premium_caption(p, notice=""):
    extra=f"{notice}\n\n" if notice else ""
    return extra+f"✨ Премиум-магазин\n\n{vip_status(p)}\n🔮 Сфер: {p.spheres}\n\nВыберите раздел."

@dp.callback_query(F.data=="shop:premium")
async def shop_premium(c):
    p=get_player(c.from_user.id); await c.answer(); await c.message.edit_caption(caption=premium_caption(p),reply_markup=premium_kb())

@dp.callback_query(F.data=="premium:seeds")
async def premium_seeds(c, notice=""):
    p=get_player(c.from_user.id); await c.answer()
    text=(f"{notice}\n\n" if notice else "")+f"🌱 Премиум-семена\n\n🔮 Баланс: {p.spheres} сфер\n\nПремиум-культуры и цветы для клумбы покупаются только за 🔮 сферы."
    rows=[]
    for crop in PREMIUM_CROPS:
        d=CROPS[crop]
        rows.append([InlineKeyboardButton(text=f"{crop_emoji(crop)} {crop} ({fmt_num(PREMIUM_SEED_PRICES[crop][1])} 🔮)",callback_data=f"buy_pseed:{crop}")])
    rows.append([InlineKeyboardButton(text="⬅️ К семенам",callback_data="shop:seeds")])
    rows.append([InlineKeyboardButton(text="⬅️ Премиум",callback_data="shop:premium")])
    await c.message.edit_caption(caption=text,reply_markup=inline(rows))

@dp.callback_query(F.data.startswith("buy_pseed:"))
async def premium_seed_info(c):
    crop=c.data.split(":",1)[1]
    if crop not in PREMIUM_CROPS: return
    p=get_player(c.from_user.id); d=CROPS[crop]; await c.answer()
    if d.get("flower"):
        text=f"{crop_emoji(crop)} {crop}\n\n🔮 Цена: {fmt_num(PREMIUM_SEED_PRICES[crop][1])} сфер за 1 семя\n⏱ Рост: {growth_text(d['growth'])}\n🌸 Урожай: {d['yield_min']}–{d['yield_max']} цветка\n\n🌸 Цветы используются для создания пиксель-арта в клумбе.\n💰 Не продаются."
    else:
        text=f"{crop_emoji(crop)} {crop}\n\n🔮 Цена: {fmt_num(PREMIUM_SEED_PRICES[crop][1])} сфер за 1 семя\n⏱ Рост: {growth_text(d['growth'])}\n🌾 Урожай: {d['yield_min']}–{d['yield_max']} шт.\n⭐ Опыт: {d['xp']} XP\n💰 Продажа: {fmt_num(d['sell'])} за 1 шт."
    await c.message.edit_caption(caption=text,reply_markup=premium_seed_qty_kb(crop))

@dp.callback_query(F.data.startswith("buy_pq:"))
async def buy_premium_seed_qty(c):
    _,crop,ntext=c.data.split(":"); n=int(ntext)
    if crop not in PREMIUM_CROPS: return
    p=get_player(c.from_user.id); d=CROPS[crop]; total=PREMIUM_SEED_PRICES[crop][n]
    if p.spheres<total:
        return await premium_seeds(c, notice=f"🔮 Недостаточно сфер. Нужно {total} 🔮.")
    p.spheres-=total; key=d['seed']; p.inventory[key]=p.inventory.get(key,0)+n; save_data()
    await premium_seeds(c, notice=f"🌱 Куплено {n} семян {crop} за {total} 🔮!")

@dp.callback_query(F.data=="premium:vip")
async def premium_vip(c, notice=""):
    p=get_player(c.from_user.id); await c.answer()
    text=(f"{notice}\n\n" if notice else "")+f"👑 VIP\n\n{vip_status(p)}\n\nПреимущества:\n⚡ Рост растений на 20% быстрее\n🍀 +1 к минимальному урожаю\n🔮 +5% к шансу получить сферу\n🎁 +50% к ежедневному бонусу\n🏆 +25% к наградам заданий\n👑 VIP-статус в профиле\n\nПокупка через Telegram Stars:"
    rows=[
                [InlineKeyboardButton(text="👑 7 дней — 50 ⭐",callback_data="vipbuy:7:50")],
        [InlineKeyboardButton(text="👑 30 дней — 150 ⭐",callback_data="vipbuy:30:150")],
        [InlineKeyboardButton(text="👑 90 дней — 350 ⭐",callback_data="vipbuy:90:350")],
        [InlineKeyboardButton(text="⬅️ Премиум",callback_data="shop:premium")]
    ]
    await c.message.edit_caption(caption=text,reply_markup=inline(rows))

@dp.callback_query(F.data.startswith("vipbuy:"))
async def vip_buy(c):
    _,days,stars=c.data.split(":"); days=int(days); stars=int(stars)
    await c.answer()
    await bot.send_invoice(chat_id=c.from_user.id,title=f"VIP на {days} дней",description="VIP-привилегия FarmWorld",payload=f"vip:{days}",currency="XTR",prices=[LabeledPrice(label=f"VIP {days} дней",amount=stars)])

@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    if q.invoice_payload.startswith("vip:") or q.invoice_payload.startswith("spheres:"):
        await q.answer(ok=True)
    else:
        await q.answer(ok=False,error_message="Неизвестный товар.")

@dp.message(F.successful_payment)
async def successful_payment(m: Message):
    sp=m.successful_payment; payload=sp.invoice_payload
    if payload.startswith("vip:"):
        days=int(payload.split(":")[1]); p=get_player(m.from_user.id); now=time.time(); base=max(now,p.vip_until); p.vip_until=base+days*86400; p.vip_trial_claimed=True; save_data()
        await m.answer(f"👑 VIP активирован на {days} дней!\n\n⏳ Осталось: {vip_left(p)}",reply_markup=main_kb(m.from_user.id))
    elif payload.startswith("spheres:"):
        count=int(payload.split(":")[1]); p=get_player(m.from_user.id); p.spheres+=count; save_data()
        await m.answer(f"🔮 Покупка успешна! +{count} сфер.",reply_markup=main_kb(m.from_user.id))

@dp.callback_query(F.data=="premium:spheres")
async def premium_spheres(c):
    await c.answer()
    text="🔮 Сферы\n\nСфера мгновенно завершает рост одной грядки.\n\nПокупка через Telegram Stars:"
    rows=[
                [InlineKeyboardButton(text="🔮 5 сфер — 50 ⭐",callback_data="spherebuy:5:50")],
        [InlineKeyboardButton(text="🔮 15 сфер — 120 ⭐",callback_data="spherebuy:15:120")],
        [InlineKeyboardButton(text="🔮 50 сфер — 350 ⭐",callback_data="spherebuy:50:350")],
        [InlineKeyboardButton(text="⬅️ Премиум",callback_data="shop:premium")]
    ]
    await c.message.edit_caption(caption=text,reply_markup=inline(rows))

@dp.callback_query(F.data.startswith("spherebuy:"))
async def sphere_buy(c):
    _,count,stars=c.data.split(":"); count=int(count); stars=int(stars); await c.answer()
    await bot.send_invoice(chat_id=c.from_user.id,title=f"🔮 {count} сфер",description="Сферы FarmWorld",payload=f"spheres:{count}",currency="XTR",prices=[LabeledPrice(label=f"{count} сфер",amount=stars)])

@dp.callback_query(F.data=="premium:boost")
async def premium_boost(c):
    await c.answer()
    text="⚡ Усилители\n\n🍀 Удачный урожай — следующий сбор получает +2 к урожаю.\n\nСтоимость: 10 🔮"
    rows=[[InlineKeyboardButton(text="🍀 Купить — 10 🔮",callback_data="boost:luck")],[InlineKeyboardButton(text="⬅️ Премиум",callback_data="shop:premium")]]
    await c.message.edit_caption(caption=text,reply_markup=inline(rows))

@dp.callback_query(F.data=="boost:luck")
async def buy_boost(c):
    p=get_player(c.from_user.id)
    if p.spheres<10: await c.answer("Недостаточно сфер",show_alert=False); return await premium_boost(c)
    p.spheres-=10; p.lucky_boost+=1; save_data(); await c.answer(); await premium_boost(c)

@dp.callback_query(F.data=="premium:cosmetic")
async def premium_cosmetic(c, notice=""):
    p=get_player(c.from_user.id); await c.answer()
    rows=[]
    for kind,(title,keys) in COSMETIC_CATEGORIES.items():
        rows.append([InlineKeyboardButton(text=title,callback_data=f"coscat:{kind}")])
    text=(f"{notice}\n\n" if notice else "")+"🎨 Косметика\n\nВсе предметы только для внешнего вида — они не дают игрового преимущества.\n\n💎 Косметика покупается только за 🔮 сферы. Сферы можно получить только через Telegram Stars.\n\nВыбери категорию:"
    await c.message.edit_caption(caption=text,reply_markup=inline(rows+[[InlineKeyboardButton(text="⬅️ Премиум",callback_data="shop:premium")]]))

@dp.callback_query(F.data.startswith("coscat:"))
async def cosmetic_category(c):
    kind=c.data.split(":",1)[1]
    if kind not in COSMETIC_CATEGORIES: return
    p=get_player(c.from_user.id); title,keys=COSMETIC_CATEGORIES[kind]
    rows=[[InlineKeyboardButton(text=cosmetic_label(p,k),callback_data=f"cosbuy:{k}")] for k in keys]
    current=active_cosmetic(p,kind)
    current_text=f"\n\nАктивно: {COSMETICS[current][0]}" if current in COSMETICS else "\n\nАктивно: Классика"
    rows.append([InlineKeyboardButton(text="⬅️ К косметике",callback_data="premium:cosmetic")])
    await c.answer(); await c.message.edit_caption(caption=f"🎨 {title}\n\nВыбирай внешний вид. Цена указана в 🔮 сферах.{current_text}",reply_markup=inline(rows))

@dp.callback_query(F.data.startswith("cosbuy:"))
async def cosmetic_buy(c):
    key=c.data.split(":",1)[1]
    if key not in COSMETICS: return
    p=get_player(c.from_user.id); label,kind,cost=COSMETICS[key]
    title,keys=COSMETIC_CATEGORIES[kind]

    # Уже куплено — просто переключаем косметику без повторной оплаты.
    if key in p.owned_cosmetics:
        p.cosmetics[kind]=key
        save_data()
        await c.answer()
        rows=[[InlineKeyboardButton(text=cosmetic_label(p,k),callback_data=f"cosbuy:{k}")] for k in keys]
        rows.append([InlineKeyboardButton(text="⬅️ К косметике",callback_data="premium:cosmetic")])
        await c.message.edit_caption(caption=f"🎨 {title}\n\n✅ {label} уже куплена и теперь активна.",reply_markup=inline(rows))
        return

    if p.spheres < cost:
        await c.answer()
        rows=[[InlineKeyboardButton(text=cosmetic_label(p,k),callback_data=f"cosbuy:{k}")] for k in keys]
        rows.append([InlineKeyboardButton(text="⬅️ К косметике",callback_data="premium:cosmetic")])
        await c.message.edit_caption(caption=f"🎨 {title}\n\n🔮 Недостаточно сфер.\n\nНужно: {cost} 🔮\nУ тебя: {p.spheres} 🔮",reply_markup=inline(rows))
        return

    # Небольшая анимация покупки прямо в том же сообщении — никаких всплывающих окон.
    await c.answer()
    await c.message.edit_caption(caption=f"🎨 {title}\n\n🔮 Проверяем баланс...\n💎 Стоимость: {cost} 🔮",reply_markup=inline([[InlineKeyboardButton(text="⏳ Покупка...",callback_data="cosnoop")]]))
    await asyncio.sleep(0.45)
    await c.message.edit_caption(caption=f"🎨 {title}\n\n🔮 Списываем {cost} сфер...\n✨ Наносим косметику...",reply_markup=inline([[InlineKeyboardButton(text="✨ Применяем...",callback_data="cosnoop")]]))
    await asyncio.sleep(0.45)

    p.spheres -= cost
    p.owned_cosmetics.append(key)
    p.cosmetics[kind]=key
    save_data()

    rows=[[InlineKeyboardButton(text=cosmetic_label(p,k),callback_data=f"cosbuy:{k}")] for k in keys]
    rows.append([InlineKeyboardButton(text="⬅️ К косметике",callback_data="premium:cosmetic")])
    await c.message.edit_caption(caption=f"🎉 Покупка завершена!\n\n{label}\n🔮 Потрачено: {cost}\n🔮 Осталось: {p.spheres}\n\n✅ Косметика активна.",reply_markup=inline(rows))

@dp.callback_query(F.data=="cosnoop")
async def cosmetic_noop(c):
    await c.answer()

# ---------- ORDERS ----------
ORDER_COUNT = 3

def order_item_data(item):
    if item in CROPS and not CROPS[item].get("flower"):
        return crop_emoji(item), "Урожай "+item, CROPS[item]["sell"], CROPS[item].get("sale_xp",1)
    if item in APPLE_SALES:
        d=APPLE_SALES[item]; return d["emoji"], item, d["sell"], d["xp"]
    if item in APPLE_PRODUCTS:
        d=APPLE_PRODUCTS[item]; return d["emoji"], item, d["sell"], d["xp"]
    return "🌱", item, 1, 1

def order_reward(item, amount):
    _,_,sell,xp_per=order_item_data(item)
    return max(10,int(sell*amount*1.35)), max(5,xp_per*amount+10)

def generate_order(p: Player):
    items=[k for k,v in CROPS.items() if not v.get("flower")]
    if p.level>=15:
        items += list(APPLE_SALES.keys())
        items += list(APPLE_PRODUCTS.keys())
    item=random.choice(items)
    if item in CROPS:
        amount=random.randint(5,20) if item in FREE_CROPS else random.randint(2,8)
    elif item=="Яблоки": amount=random.randint(15,50)
    elif item=="Золотые яблоки": amount=random.randint(1,3)
    elif item=="Сияющие яблоки": amount=1
    elif item=="Яблочный сок": amount=random.randint(1,4)
    elif item=="Яблочный пирог": amount=random.randint(1,3)
    elif item=="Яблочное варенье": amount=random.randint(1,2)
    elif item=="Золотой сок": amount=1
    else: amount=1
    coins,xp=order_reward(item,amount)
    return {"item":item,"amount":amount,"coins":coins,"xp":xp}

def ensure_orders(p: Player):
    if not isinstance(p.orders,list): p.orders=[]
    while len(p.orders)<ORDER_COUNT: p.orders.append(generate_order(p))
    if len(p.orders)>ORDER_COUNT: p.orders=p.orders[:ORDER_COUNT]

def normalize_order(o):
    # Миграция старых заказов формата {crop: ...}.
    if "item" not in o and "crop" in o: o["item"]=o["crop"]
    return o

def orders_view(p: Player, notice=""):
    ensure_orders(p); rows=[]
    lines=["📦 Заказы","","Горожане готовы купить урожай и товары из сада. Выполняй заказы и получай повышенную награду!",""]
    for i,o in enumerate(p.orders):
        o=normalize_order(o); item=o["item"]; amount=o["amount"]; emoji,key,_,_=order_item_data(item); have=p.inventory.get(key,0)
        lines.append(f"{i+1}. {emoji} {item} — {amount} шт.\n   📦 Есть: {have} | 💰 {o['coins']} | ⭐ {o['xp']} XP")
        if have>=amount:
            rows.append([InlineKeyboardButton(text=f"📦 Выполнить заказ №{i+1}",callback_data=f"order:complete:{i}")])
    lines.append("\n💡 Яблоки и продукты сада тоже могут требоваться в заказах.")
    rows.append([InlineKeyboardButton(text="🔄 Обновить заказы",callback_data="orders:refresh")])
    rows.append([InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")])
    text=(f"{notice}\n\n" if notice else "")+"\n".join(lines)
    return text,inline(rows)

@dp.callback_query(F.data=="city:orders")
async def orders(c):
    p=get_player(c.from_user.id); ensure_orders(p); save_data(); text,kb=orders_view(p); await c.answer(); await edit_city_content(c,text,kb)

@dp.callback_query(F.data=="orders:refresh")
async def orders_refresh(c):
    p=get_player(c.from_user.id); ensure_orders(p); save_data(); await c.answer("Новые заказы появляются после выполнения."); text,kb=orders_view(p,"📦 Заказы обновятся после выполнения текущих."); await edit_city_content(c,text,kb)

@dp.callback_query(F.data.startswith("order:complete:"))
async def order_complete(c):
    p=get_player(c.from_user.id); ensure_orders(p)
    try: idx=int(c.data.split(":")[-1])
    except ValueError: return await c.answer("Ошибка заказа.")
    if idx<0 or idx>=len(p.orders): return await c.answer("Заказ уже недоступен.")
    o=normalize_order(p.orders[idx]); item=o["item"]; amount=o["amount"]; emoji,key,_,_=order_item_data(item); have=p.inventory.get(key,0)
    if have<amount:
        await c.answer("Не хватает нужного товара."); text,kb=orders_view(p,"📦 Для этого заказа пока не хватает товара."); return await edit_market_content(c,text,kb)
    p.inventory[key]=have-amount; p.coins+=o["coins"]; p.earned_coins+=o["coins"]; levels=add_xp(p,o["xp"])
    completed=f"📦 Заказ выполнен!\n{emoji} {item} × {amount}\n💰 +{o['coins']} монет\n⭐ +{o['xp']} XP"
    if levels: completed+=f"\n🎉 Новый уровень: {levels[-1][0]}!\n🎁 Награда доступна в профиле."
    p.orders[idx]=generate_order(p); save_data(); await c.answer("Заказ выполнен!"); text,kb=orders_view(p,completed); await edit_city_content(c,text,kb)
    await send_level_up_notification(c.from_user.id, levels)

# ---------- SPECIAL LUMBERJACK QUEST ----------
LUMBERJACK_STAGES = [
    {
        "title": "🌾 Первый выход в лес",
        "goal": "Соберите 20 единиц урожая",
        "target": 20,
        "progress": lambda p: p.total_harvest,
        "reward": "💰 1.000 монет",
    },
    {
        "title": "💰 Заработать на топор",
        "goal": "Заработайте 1.000 монет",
        "target": 1000,
        "progress": lambda p: p.earned_coins,
        "reward": "💰 2.500 монет",
    },
    {
        "title": "🌸 Цветущий путь",
        "goal": "Соберите 10 цветов",
        "target": 10,
        "progress": lambda p: p.flower_harvested,
        "reward": "🔮 5 сфер",
    },
    {
        "title": "🍒 След вишнёвого сада",
        "goal": "Соберите 10 вишен",
        "target": 10,
        "progress": lambda p: p.cherries_harvested,
        "reward": "💰 5.000 монет",
    },
    {
        "title": "🪓 Испытание лесоруба",
        "goal": "Соберите всего 20 вишен",
        "target": 20,
        "progress": lambda p: p.cherries_harvested,
        "reward": "🪓 Топорик ×1",
    },
]

def lumberjack_progress(p, index):
    st = LUMBERJACK_STAGES[index]
    return min(st["target"], max(0, int(st["progress"](p))))

def lumberjack_check(p: Player):
    # Stage is informational; rewards are claimed separately and never granted automatically.
    stage = 0
    for i, st in enumerate(LUMBERJACK_STAGES, 1):
        if lumberjack_progress(p, i - 1) >= st["target"]:
            stage = i
        else:
            break
    p.lumberjack_quest_stage = max(p.lumberjack_quest_stage, stage)
    if 5 in p.lumberjack_stage_claimed:
        p.lumberjack_quest_claimed = True

def lumberjack_bar(cur, target, size=10):
    if target <= 0: return "░" * size
    filled = min(size, int((cur / target) * size))
    return "█" * filled + "░" * (size - filled)

def lumberjack_text(p: Player, notice="") -> str:
    lumberjack_check(p)
    claimed = set(p.lumberjack_stage_claimed)
    completed_count = len([x for x in claimed if 1 <= x <= 5])
    current = next((i for i in range(1, 6) if i not in claimed), 5)
    lines = [
        "🪓 ДОРОГА ЛЕСОРУБА",
        "",
        "🌲 Особый путь фермера к редкому Топорику.",
        "Выполняйте этапы по порядку, забирайте награды и открывайте следующий шаг.",
        "",
        f"🛤️ Прогресс пути: {completed_count}/5",
        f"[{lumberjack_bar(completed_count, 5)}]",
        "",
    ]
    for i, st in enumerate(LUMBERJACK_STAGES, 1):
        cur = lumberjack_progress(p, i - 1)
        is_claimed = i in claimed
        unlocked = i == 1 or (i - 1 in claimed)
        if is_claimed:
            status = "✅ ВЫПОЛНЕНО"
        elif not unlocked:
            status = "🔒 ЗАБЛОКИРОВАНО"
        elif cur >= st["target"]:
            status = "🎁 НАГРАДА ГОТОВА"
        else:
            status = "🌲 В ПРОЦЕССЕ"
        lines += [
            f"{i}. {st['title']}",
            f"   {st['goal']}",
            f"   [{lumberjack_bar(cur, st['target'])}] {cur}/{st['target']}",
            f"   🎁 {st['reward']}",
            f"   {status}",
            "",
        ]
    lines += [
        "────────────────────",
        f"🪓 Топориков: {p.axe}",
        "🌳 Топорик нельзя купить в магазине.",
        "Он расходуется при рубке одного вишнёвого дерева.",
    ]
    if 5 in claimed:
        lines += ["", "🏆 Путь лесоруба пройден!"]
    if notice:
        lines = [notice, ""] + lines
    return "\n".join(lines)

def lumberjack_kb(p: Player):
    lumberjack_check(p)
    claimed = set(p.lumberjack_stage_claimed)
    rows = []
    for i, st in enumerate(LUMBERJACK_STAGES, 1):
        if i in claimed:
            continue
        # Награда становится доступной сразу после выполнения самого этапа.
        # Можно иметь несколько выполненных этапов и забрать их награды по отдельности.
        if lumberjack_progress(p, i - 1) >= st["target"]:
            rows.append([InlineKeyboardButton(text=f"🎁 Забрать награду {i}", callback_data=f"lumberjack:claim:{i}")])
    rows.append([InlineKeyboardButton(text="📖 Как работает путь", callback_data="lumberjack:rules")])
    rows.append([InlineKeyboardButton(text="⬅️ К заданиям", callback_data="city:quests")])
    return inline(rows)

@dp.callback_query(F.data=="lumberjack:open")
async def lumberjack_open(c):
    p=get_player(c.from_user.id)
    await c.answer()
    await edit_city_content(c, lumberjack_text(p), lumberjack_kb(p))

@dp.callback_query(F.data=="lumberjack:rules")
async def lumberjack_rules(c):
    p=get_player(c.from_user.id)
    text = (
        "📖 КАК РАБОТАЕТ ДОРОГА ЛЕСОРУБА\n\n"
        "🪓 Это особая цепочка из 5 этапов.\n\n"
        "🌲 Этапы открываются по мере выполнения. Награды за все выполненные этапы можно забирать отдельно.\n\n"
        "🎁 За первые четыре этапа вы получите полезные ресурсы.\n"
        "🏆 За пятый этап — редкий 🪓 Топорик.\n\n"
        "⚠️ Топорик нельзя купить. Он расходуется при рубке одного вишнёвого дерева.\n"
        "🪵 После рубки дерево исчезает, а вы получаете древесину для развития Клумбы.\n\n"
        f"🪓 Сейчас у вас: {p.axe}"
    )
    kb=inline([[InlineKeyboardButton(text="⬅️ К Дороге лесоруба",callback_data="lumberjack:open")]])
    await c.answer(); await edit_city_content(c, text, kb)

@dp.callback_query(F.data.startswith("lumberjack:claim:"))
async def lumberjack_claim(c):
    p=get_player(c.from_user.id); lumberjack_check(p)
    try: idx=int(c.data.split(":")[-1])
    except ValueError: return await c.answer("Ошибка этапа.")
    if idx<1 or idx>5: return await c.answer("Этап недоступен.")
    claimed=set(p.lumberjack_stage_claimed)
    if idx in claimed: return await c.answer("Эта награда уже получена.")
    st=LUMBERJACK_STAGES[idx-1]
    if lumberjack_progress(p,idx-1)<st["target"]: return await c.answer("Этап ещё не выполнен.")
    if idx==1:
        p.coins+=1000; p.earned_coins+=1000
    elif idx==2:
        p.coins+=2500; p.earned_coins+=2500
    elif idx==3:
        p.spheres+=5
    elif idx==4:
        p.coins+=5000; p.earned_coins+=5000
    elif idx==5:
        p.axe+=1; p.lumberjack_quest_claimed=True
    p.lumberjack_stage_claimed=sorted(list(claimed|{idx}))
    p.lumberjack_quest_stage=max(p.lumberjack_quest_stage,idx)
    save_data()
    await c.answer(f"🎁 Награда за этап {idx} получена!")
    await edit_city_content(c, lumberjack_text(p, f"🎉 Этап {idx} завершён! Получено: {st['reward']}"), lumberjack_kb(p))

# ---------- QUESTS / ACHIEVEMENTS ----------
QUESTS={"wheat100":{"name":"🌾 Первые 100","goal":100,"kind":"wheat","reward":150},"carrot60":{"name":"🥕 Морковный заказ","goal":60,"kind":"carrot","reward":300},"coins1000":{"name":"💰 Большая выручка","goal":1000,"kind":"coins","reward":500}}

def quest_progress(p,q):
    if q['kind']=='wheat': return p.inventory.get('Урожай Пшеница',0)+sum(1 for b in p.beds if b.crop=='Пшеница')*0
    if q['kind']=='carrot': return p.inventory.get('Урожай Морковь',0)
    return p.earned_coins

@dp.callback_query(F.data=="city:quests")
async def quests(c, notice=""):
    p=get_player(c.from_user.id); rows=[]
    text="📜 Задания\n\n"
    for qid,q in QUESTS.items():
        if qid in p.quests_done: text+=f"✅ {q['name']} — выполнено\n"; continue
        cur=min(q['goal'],quest_progress(p,q)); text+=f"{q['name']}\n📊 {cur}/{q['goal']}\n🎁 Награда: {q['reward']} 💰\n\n"
        if cur>=q['goal']: rows.append([InlineKeyboardButton(text=f"🎁 Забрать: {q['name']}",callback_data=f"quest:claim:{qid}")])
    rows.append([InlineKeyboardButton(text="🪓 Дорога лесоруба",callback_data="lumberjack:open")])
    rows.append([InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")]); await c.answer(); await edit_city_content(c, (f"{notice}\n\n" if notice else "")+text, inline(rows))

@dp.callback_query(F.data.startswith("quest:claim:"))
async def quest_claim(c):
    p=get_player(c.from_user.id); qid=c.data.split(":")[-1]; q=QUESTS[qid]
    if qid in p.quests_done or quest_progress(p,q)<q['goal']:
        await c.answer(); return await quests(c, notice="📜 Задание ещё не выполнено.")
    p.quests_done.append(qid); p.coins+=q['reward']; levels=add_xp(p,50); save_data(); await c.answer(); await quests(c, notice=f"🎁 Получено {q['reward']} 💰 и 50 XP!"); await send_level_up_notification(c.from_user.id, levels)

ACH={"first":"🌱 Первая посадка","harvest100":"🌾 100 урожая","coins1000":"💰 1.000 заработано","level10":"🏆 Уровень 10","beds5":"🏡 5 грядок"}
def check_ach(p):
    cond={"first":p.total_harvest>0,"harvest100":p.total_harvest>=100,"coins1000":p.earned_coins>=1000,"level10":p.level>=10,"beds5":len(p.beds)>=5}
    new=[]
    for k,v in cond.items():
        if v and k not in p.achievements: p.achievements.append(k); p.coins+=100; new.append(k)
    return new

@dp.message(Command("achievements"))
async def achievements_cmd(m): await show_achievements(m)
async def show_achievements(x):
    p=get_player(x.from_user.id); check_ach(p); text="🏆 Достижения\n\n"+"\n".join(("✅ " if k in p.achievements else "🔒 ")+v+(" — +100 💰" if k in p.achievements else "") for k,v in ACH.items()); save_data()
    if isinstance(x,Message): await x.answer(text,reply_markup=main_kb(x.from_user.id))
    else: await x.message.edit_text(text,reply_markup=inline([[InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")]]))

# ---------- DAILY / BANK / WORKSHOP / GARDEN / NPC / RATING ----------
# ---------- MINE / DUNGEON ----------
MINE_MAX_FLOOR = 15
MINE_LIVES = 3
MINE_GRID_SIZES = {1: 3, 2: 3, 3: 3, 4: 3, 5: 3, 6: 4, 7: 4, 8: 4, 9: 4, 10: 4,
                   11: 5, 12: 5, 13: 5, 14: 5, 15: 5}
MINE_FLOOR_REWARDS = {5: 1000, 10: 2000, 15: 3000}
MINE_ENEMIES = {
    "easy": [("🦇 Летучая мышь", 1), ("🐀 Крыса", 1)],
    "medium": [("🕷️ Паук", 2), ("🐍 Змея", 2), ("👺 Гоблин", 3), ("💀 Скелет", 3)],
    "hard": [("🧌 Тролль", 4), ("😈 Демон", 5), ("🐉 Дракон", 6), ("🗿 Страж", 6), ("👻 Призрак", 5)],
}
FLOWER_ESSENCES = {
    "🌷": "🩷 Эссенция", "🪻": "💙 Эссенция", "🌼": "💛 Эссенция", "🌺": "❤️ Эссенция",
    "🌸": "💖 Эссенция", "🌹": "❤️‍🔥 Эссенция", "🌵": "💚 Эссенция", "💮": "🤍 Эссенция", "🌻": "🤎 Эссенция",
}


def mine_state(p: Player) -> Dict:
    if not isinstance(p.mine, dict):
        p.mine = {}
    return p.mine


def mine_tier(floor: int) -> str:
    return "easy" if floor <= 5 else ("medium" if floor <= 10 else "hard")


def mine_grid_size(floor: int) -> int:
    return MINE_GRID_SIZES.get(floor, 5)


def mine_idx(x: int, y: int, size: int) -> int:
    return y * size + x


def mine_xy(idx: int, size: int):
    return idx % size, idx // size


def mine_new_floor(p: Player, floor: int):
    size = mine_grid_size(floor)
    total = size * size
    start = mine_idx(size // 2, size // 2, size)
    candidates = [i for i in range(total) if i != start]
    random.shuffle(candidates)
    stairs = candidates.pop()
    exit_room = candidates.pop() if floor in (5, 10, 15) else None
    rooms = ["unknown"] * total
    rooms[start] = "empty"
    rooms[stairs] = "stairs" if floor < 15 else "empty"
    if exit_room is not None:
        rooms[exit_room] = "exit"
    # Guaranteed variety on every floor where space allows.
    room_pool = ["empty"] * max(1, total // 3) + ["resource"] * max(2, total // 4) + ["enemy"] * max(2, total // 4)
    if total >= 9:
        room_pool += ["healer", "alchemist"]
    random.shuffle(room_pool)
    for i in candidates:
        if i in (stairs, exit_room):
            continue
        rooms[i] = room_pool.pop() if room_pool else random.choice(["empty", "resource", "enemy"])
    p.mine.update({"floor": floor, "size": size, "x": size // 2, "y": size // 2,
                   "rooms": rooms, "found": {}, "current_event": None, "enemy": None, "exit_claimed": False})


def mine_init(p: Player):
    p.mine = {"active": True, "floor": 1, "lives": MINE_LIVES, "shield_used": False,
              "found": {}, "rescue": {}, "current_event": None, "enemy": None}
    mine_new_floor(p, 1)
    # Shield is consumed when entering a raid and increases max/current lives to 8.
    if p.inventory.get("Щит", 0) > 0:
        p.inventory["Щит"] -= 1
        p.mine["lives"] = 8
        p.mine["shield_used"] = True


def mine_active(p: Player) -> bool:
    return bool(mine_state(p).get("active"))


def mine_add_found(p: Player, key: str, amount: int):
    f = mine_state(p)
    found = f.setdefault("found", {})
    found[key] = found.get(key, 0) + amount


def mine_room_label(room: str, current=False, revealed=False):
    if current:
        return "🔵"
    if not revealed:
        return "⬛"
    return {"empty": "⬜", "resource": "💎", "enemy": "👹", "healer": "🩸", "alchemist": "🧙", "stairs": "⬇️", "exit": "🚪"}.get(room, "⬜")


def mine_revealed(f):
    return set(f.get("revealed", []))


def mine_reveal_current(f):
    size = int(f.get("size", 3)); idx = mine_idx(int(f.get("x", 0)), int(f.get("y", 0)), size)
    rev = mine_revealed(f); rev.add(idx); f["revealed"] = list(rev)
    return idx


def mine_adjacent(f, nx, ny):
    size = int(f.get("size", 3)); x=int(f.get("x",0)); y=int(f.get("y",0))
    return 0 <= nx < size and 0 <= ny < size and abs(nx-x)+abs(ny-y)==1


def mine_room_buttons(p: Player):
    f=mine_state(p); size=int(f.get("size",3)); rooms=f.get("rooms",[]); rev=mine_revealed(f)
    rows=[]
    for y in range(size):
        row=[]
        for x in range(size):
            idx=mine_idx(x,y,size); label=mine_room_label(rooms[idx], idx==mine_reveal_current(f), idx in rev)
            if idx in rev or idx==mine_idx(int(f.get("x",0)),int(f.get("y",0)),size):
                cb=f"mine:move:{x}:{y}" if mine_adjacent(f,x,y) else "mine:noop"
            else:
                cb=f"mine:move:{x}:{y}" if mine_adjacent(f,x,y) else "mine:noop"
            row.append(InlineKeyboardButton(text=label,callback_data=cb))
        rows.append(row)
    return rows


def mine_found_text(f):
    found=f.get("found",{}) or {}
    if not found:
        return "— пока ничего"
    names={"Камень":"🪨 Камень", "Железо":"🔗 Железо", "Чертёж":"📄 Чертёж", "Монеты":"💰 Монеты", "Образцы":"🩸 Образцы", "Химикаты":"⚗️ Химикаты"}
    return "\n".join(f"• {names.get(k,k)}: {v}" for k,v in found.items())


def mine_status_text(p: Player):
    f=mine_state(p); floor=int(f.get("floor",1)); lives=int(f.get("lives",0)); size=int(f.get("size",mine_grid_size(floor)))
    return (f"⛏️ Шахта — Этаж {floor}\n\n"
            f"❤️ Жизни: {lives}/{8 if f.get('shield_used') else 3}\n"
            f"🗺️ Размер: {size}×{size}\n\n"
            f"💎 Найденные ресурсы:\n{mine_found_text(f)}")


def mine_resource_roll(p: Player, floor: int):
    r=random.random()
    if floor<=5:
        if r<0.40: return "Камень", random.randint(1,5)
        return "Монеты", random.randint(100,500)
    if floor<=10:
        if r<0.20: return "Камень", random.randint(1,6)
        if r<0.30: return "Железо", random.randint(1,3)
        return "Монеты", random.randint(200,1000)
    if r<0.30: return "Камень", random.randint(2,8)
    if r<0.50: return "Железо", random.randint(1,5)
    if r<0.60: return "Чертёж", 1
    return "Монеты", random.randint(300,1500)


def mine_enemy_roll(floor: int):
    return random.choice(MINE_ENEMIES[mine_tier(floor)])


def mine_enemy_text(p: Player):
    f=mine_state(p); enemy=f.get("enemy") or {}; return f"👹 Враг!\n\n{enemy.get('name','Монстр')}\n⚔️ Сила атаки: {enemy.get('attack',1)}\n\n❤️ Твои жизни: {f.get('lives',0)}"


def mine_finalize(p: Player, success=False, reason=""):
    f=mine_state(p); found=dict(f.get("found",{}) or {})
    keep_resources = success or reason == "Выход"
    if keep_resources:
        for k,v in found.items():
            if k=="Монеты":
                p.coins += v; p.earned_coins += v
            elif k=="Камень": p.inventory["Камень"]=p.inventory.get("Камень",0)+v
            elif k=="Железо": p.inventory["Железо"]=p.inventory.get("Железо",0)+v
            elif k=="Чертёж": p.inventory["Чертёж"]=p.inventory.get("Чертёж",0)+v
            elif k=="Образцы": p.inventory["Образцы"]=p.inventory.get("Образцы",0)+v
            elif k=="Химикаты": p.inventory["Химикаты"]=p.inventory.get("Химикаты",0)+v
        f["rescue"]={}
    else:
        f["rescue"] = found
    f["active"]=False; f["current_event"]=None; f["enemy"]=None; f["found"]={}
    return found


@dp.callback_query(F.data=="city:mine")
async def mine(c, notice=""):
    p=get_player(c.from_user.id); f=mine_state(p)
    if p.level < 5:
        text=f"⛏️ Шахта\n\n🔒 Доступна с 5 уровня.\nТвой уровень: {p.level}."
        return await edit_city_content(c,text,inline([[InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")]]))
    if mine_active(p) and f.get("paused_for_alchemist"):
        text=mine_status_text(p)+"\n\n🌸 Рейд приостановлен. Можно выйти, собрать цветок и вернуться без новой тяпки."
        rows=[[InlineKeyboardButton(text="↩️ Вернуться в шахту",callback_data="mine:resume")],
              [InlineKeyboardButton(text="🏃 Завершить рейд",callback_data="mine:exit")]]
        return await edit_city_content(c,text,inline(rows))
    if mine_active(p):
        event=f.get("current_event")
        if event=="enemy":
            return await mine_enemy(c)
        if event=="alchemist":
            return await mine_alchemist(c)
        text=mine_status_text(p)+"\n\n🗺️ Исследуй соседние комнаты:"
        rows=mine_room_buttons(p)
        if event=="stairs":
            rows.append([InlineKeyboardButton(text="⬇️ Спуститься на следующий этаж",callback_data="mine:advance")])
        elif event=="exit":
            floor=int(f.get("floor",1))
            rows.append([InlineKeyboardButton(text=f"🚪 Завершить этаж и получить {MINE_FLOOR_REWARDS.get(floor,0):,} 💰",callback_data="mine:complete_floor")])
        rows += [[InlineKeyboardButton(text="🗃️ Сундук спасения",callback_data="mine:rescue")],
                 [InlineKeyboardButton(text="🧊 Эвакуационная капсула",callback_data="mine:evacuate")],
                 [InlineKeyboardButton(text="🏃 Выйти из шахты",callback_data="mine:exit")]]
        if notice: text=f"{notice}\n\n{text}"
        await c.answer(); return await edit_city_content(c,text,inline(rows))
    rescue=f.get("rescue") or {}
    text=("⛏️ Шахта\n\nОпасное подземелье с сокровищами и монстрами.\n"
          "🔒 Доступна с 5 уровня.\n⛏️ Для входа нужна 1 тяпка.\n❤️ 3 жизни.\n🗺️ 15 этажей.\n\n"
          f"⛏️ Тяпки: {p.inventory.get('Тяпка',0)}\n🗡️ Мечи: {p.inventory.get('Меч',0)}\n🛡️ Щиты: {p.inventory.get('Щит',0)}")
    rows=[]
    if p.inventory.get("Тяпка",0)>0: rows.append([InlineKeyboardButton(text="⛏️ Начать рейд — 1 тяпка",callback_data="mine:start")])
    if rescue: rows.append([InlineKeyboardButton(text="🗃️ Забрать ресурсы из сундука спасения",callback_data="mine:rescue")])
    rows.append([InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")])
    if notice: text=f"{notice}\n\n{text}"
    await c.answer(); await edit_city_content(c,text,inline(rows))

@dp.callback_query(F.data=="mine:start")
async def mine_start(c):
    p=get_player(c.from_user.id)
    if p.level<5: return await c.answer("🔒 Шахта доступна с 5 уровня.")
    if p.inventory.get("Тяпка",0)<1: return await c.answer("⛏️ Нужна 1 тяпка.")
    p.inventory["Тяпка"]-=1; mine_init(p); mine_reveal_current(mine_state(p)); save_data(); await c.answer("⛏️ Рейд начался!"); await mine(c)

@dp.callback_query(F.data.startswith("mine:move:"))
async def mine_move(c):
    p=get_player(c.from_user.id); f=mine_state(p)
    if not mine_active(p): return await c.answer("Рейд не активен.")
    try: nx=int(c.data.split(":")[2]); ny=int(c.data.split(":")[3])
    except Exception: return await c.answer("Ошибка комнаты.")
    if not mine_adjacent(f,nx,ny): return await c.answer("Можно перейти только в соседнюю комнату.")
    size=int(f["size"]); idx=mine_idx(nx,ny,size); f["x"]=nx; f["y"]=ny; mine_reveal_current(f)
    room=f["rooms"][idx]
    if room in ("empty", "stairs", "exit") and f.get("current_event") is None:
        if room=="stairs":
            f["current_event"]="stairs"
        elif room=="exit":
            f["current_event"] = None if f.get("exit_claimed") else "exit"
        else:
            f["current_event"]=None
        save_data(); await c.answer(); return await mine(c)
    if room=="resource":
        key,amount=mine_resource_roll(p,int(f["floor"])); mine_add_found(p,key,amount); f["rooms"][idx]="empty"; f["current_event"]=None
        save_data(); await c.answer(f"💎 Найдено: {amount} {key}"); return await mine(c)
    if room=="enemy":
        name,attack=mine_enemy_roll(int(f["floor"])); f["enemy"]={"name":name,"attack":attack}; f["current_event"]="enemy"; f["rooms"][idx]="empty"; save_data(); await c.answer();
        return await mine_enemy(c)
    if room=="healer":
        amount=random.randint(1,3); mine_add_found(p,"Образцы",amount); f["rooms"][idx]="empty"; f["current_event"]=None; save_data(); await c.answer(f"🩸 Лечебный энкаунтер: +{amount} образцов"); return await mine(c)
    if room=="alchemist":
        f["current_event"]="alchemist"; save_data(); await c.answer(); return await mine_alchemist(c)
    await c.answer(); await mine(c)

@dp.callback_query(F.data=="mine:noop")
async def mine_noop(c): await c.answer("⬛ Комната ещё не рядом.")

async def mine_enemy(c):
    p=get_player(c.from_user.id); f=mine_state(p); text=mine_enemy_text(p)
    rows=[]
    if p.inventory.get("Меч",0)>0 and p.inventory.get("Меч_прочность",0)>0:
        rows.append([InlineKeyboardButton(text="⚔️ Сражаться",callback_data="mine:fight")])
    rows.append([InlineKeyboardButton(text="🏃 Убегать",callback_data="mine:flee")])
    await c.answer(); await edit_city_content(c,text,inline(rows))

@dp.callback_query(F.data=="mine:fight")
async def mine_fight(c):
    p=get_player(c.from_user.id); f=mine_state(p); enemy=f.get("enemy")
    if not enemy: return await c.answer("Врага рядом нет.")
    if p.inventory.get("Меч",0)<=0 or p.inventory.get("Меч_прочность",0)<=0: return await c.answer("🗡️ Нужен меч.")
    p.inventory["Меч_прочность"]-=1
    if p.inventory["Меч_прочность"]<=0: p.inventory["Меч"]-=1; p.inventory["Меч_прочность"]=0
    if random.random()<0.5:
        mine_add_found(p,"Химикаты",1)
    f["enemy"]=None; f["current_event"]=None; save_data(); await c.answer("⚔️ Победа! Меч потерял 1 прочность."); await mine(c)

@dp.callback_query(F.data=="mine:flee")
async def mine_flee(c):
    p=get_player(c.from_user.id); f=mine_state(p); floor=int(f.get("floor",1)); chance=0.8 if floor<=5 else (0.6 if floor<=10 else 0.4)
    if random.random()<chance:
        f["enemy"]=None; f["current_event"]=None; save_data(); await c.answer("🏃 Удалось убежать!"); return await mine(c)
    dmg=int((f.get("enemy") or {}).get("attack",1)); f["lives"]=max(0,int(f.get("lives",0))-dmg); f["enemy"]=None; f["current_event"]=None
    if f["lives"]<=0:
        if p.inventory.get("Аптечка",0)>0:
            p.inventory["Аптечка"]-=1; f["lives"]=1; save_data(); await c.answer("🇨🇭 Аптечка спасла рейд! +1 ❤️"); return await mine(c)
        found=mine_finalize(p,False,"Поражение")
        save_data(); await c.answer("💀 Все жизни потеряны."); return await mine(c,notice="💀 Рейд завершён. Найденные ресурсы ждут в сундуке спасения 🗃️.")
    save_data(); await c.answer(f"💥 Не удалось убежать. -{dmg} ❤️"); await mine(c)

@dp.callback_query(F.data=="mine:exit")
async def mine_exit(c):
    p=get_player(c.from_user.id); f=mine_state(p)
    if not mine_active(p): return await c.answer("Рейд не активен.")
    found=mine_finalize(p,False,"Выход")
    save_data(); await c.answer("🏃 Ты вышел из шахты."); await mine(c,notice="🏃 Рейд завершён. Все найденные ресурсы сохранены.")

@dp.callback_query(F.data=="mine:evacuate")
async def mine_evacuate(c):
    p=get_player(c.from_user.id); f=mine_state(p)
    if not mine_active(p): return await c.answer("Рейд не активен.")
    if p.inventory.get("Эвакуационная капсула",0)<=0: return await c.answer("🧊 Нужна эвакуационная капсула.")
    p.inventory["Эвакуационная капсула"]-=1; mine_finalize(p,True,"Капсула"); save_data(); await c.answer("🧊 Мгновенная эвакуация! Ресурсы сохранены."); await mine(c,notice="🧊 Ты эвакуировался и сохранил все найденные ресурсы.")

@dp.callback_query(F.data=="mine:rescue")
async def mine_rescue(c):
    p=get_player(c.from_user.id); f=mine_state(p); rescue=f.get("rescue") or {}
    if not rescue: return await c.answer("🗃️ Сундук спасения пуст.")
    for k,v in rescue.items(): mine_add_found(p,k,v)
    found=dict(f.get("found") or {}); f["rescue"]={}
    # Rescue chest is immediately deposited into inventory/balance.
    for k,v in found.items():
        if k=="Монеты": p.coins+=v; p.earned_coins+=v
        else: p.inventory[k]=p.inventory.get(k,0)+v
    f["found"]={}; save_data(); await c.answer("🗃️ Ресурсы спасены!"); await mine(c,notice="🗃️ Все найденные ресурсы забраны из сундука спасения.")

async def mine_alchemist(c):
    p=get_player(c.from_user.id); f=mine_state(p)
    rows=[]
    for flower,essence in FLOWER_ESSENCES.items():
        if p.inventory.get(flower,0)>0:
            rows.append([InlineKeyboardButton(text=f"{flower} → {essence}",callback_data=f"mine:alchemy:{flower}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="🎫 Получить билет удачи",callback_data="mine:luck_ticket")])
    rows.append([InlineKeyboardButton(text="🌸 Выйти за цветком и вернуться",callback_data="mine:leave_alchemist")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в комнату",callback_data="mine:alchemy:back")])
    await c.answer(); await edit_city_content(c,"🧙 Алхимик-отшельник\n\nОбмен цветов с клумбы на эссенции 1:1. После обмена алхимик исчезнет из комнаты.",inline(rows))

@dp.callback_query(F.data=="mine:leave_alchemist")
async def mine_leave_alchemist(c):
    p=get_player(c.from_user.id); f=mine_state(p)
    # The raid stays saved and can be resumed without spending another hoe.
    f["paused_for_alchemist"]=True
    f["current_event"]=None
    save_data(); await c.answer("🌸 Рейд сохранён. Собери цветок и возвращайся.")
    await mine(c)

@dp.callback_query(F.data=="mine:resume")
async def mine_resume(c):
    p=get_player(c.from_user.id); f=mine_state(p)
    if not mine_active(p): return await c.answer("Рейд уже завершён.")
    f["paused_for_alchemist"]=False
    x,y=int(f.get("x",0)),int(f.get("y",0)); idx=mine_idx(x,y,int(f.get("size",3)))
    f["current_event"]="alchemist" if f.get("rooms",[])[idx]=="alchemist" else None
    save_data(); await c.answer("↩️ Возвращаемся в шахту."); await mine(c)

@dp.callback_query(F.data.startswith("mine:alchemy:"))
async def mine_alchemy(c):
    p=get_player(c.from_user.id); f=mine_state(p); value=c.data.split(":",2)[2]
    if value=="back": f["current_event"]=None; save_data(); return await mine(c)
    if value not in FLOWER_ESSENCES or p.inventory.get(value,0)<=0: return await c.answer("🌸 Цветка нет.")
    p.inventory[value]-=1; key=FLOWER_ESSENCES[value]; p.inventory[key]=p.inventory.get(key,0)+1; f["current_event"]=None
    # NPC disappears after one exchange.
    x,y=int(f["x"]),int(f["y"]); idx=mine_idx(x,y,int(f["size"])); f["rooms"][idx]="empty"; save_data(); await c.answer("🧙 Обмен завершён!"); await mine(c)

@dp.callback_query(F.data=="mine:luck_ticket")
async def mine_luck_ticket(c):
    p=get_player(c.from_user.id); f=mine_state(p); f["current_event"]=None
    p.inventory["Билет удачи"]=p.inventory.get("Билет удачи",0)+1
    x,y=int(f["x"]),int(f["y"]); f["rooms"][mine_idx(x,y,int(f["size"]))]="empty"; save_data(); await c.answer("🎫 Билет удачи получен!"); await mine(c)

@dp.callback_query(F.data=="mine:advance")
async def mine_advance(c):
    p=get_player(c.from_user.id); f=mine_state(p); floor=int(f.get("floor",1))
    if floor>=15: return await mine(c,notice="🏆 Последний этаж уже пройден.")
    mine_new_floor(p,floor+1); f["lives"]=int(f.get("lives",3)); f["current_event"]=None; save_data(); await c.answer(f"⬇️ Спуск на этаж {floor+1}"); await mine(c)

@dp.callback_query(F.data=="mine:complete_floor")
async def mine_complete_floor(c):
    p=get_player(c.from_user.id); f=mine_state(p); floor=int(f.get("floor",1))
    if floor not in MINE_FLOOR_REWARDS: return await c.answer("🚪 Здесь нет выхода.")
    reward=MINE_FLOOR_REWARDS[floor]; p.coins+=reward; p.earned_coins+=reward
    if floor==15:
        mine_finalize(p,True,"Победа"); msg=f"🏆 Шахта полностью пройдена!\n🎁 Награда: {reward:,} 💰"
    else:
        # Keep raid active and continue from the exit room; next floor has the same current lives.
        f["current_event"]="exit_done"; f["exit_claimed"]=True
        idx=mine_idx(int(f.get("x",0)),int(f.get("y",0)),int(f.get("size",3)))
        f["rooms"][idx]="empty"
        msg=f"🏆 Этаж {floor} завершён!\n🎁 +{reward:,} 💰\n\nМожно спуститься глубже."
    save_data(); await c.answer("🏆 Награда получена!"); await mine(c,notice=msg)

# Intercept exit/stairs actions through a compact callback helper.
@dp.callback_query(F.data=="mine:room_action")
async def mine_room_action(c):
    await c.answer()

@dp.callback_query(F.data=="city:bank")
async def bank(c, notice=""):
    p=get_player(c.from_user.id); now=int(time.time()); can=now-p.daily_last>=86400; rows=[]
    if can: rows.append([InlineKeyboardButton(text="🎁 Забрать ежедневный бонус",callback_data="daily")])
    rows.append([InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")]); await c.answer(); await edit_city_content(c, (f"{notice}\n\n" if notice else "")+f"🏦 Банк\n\n🎁 Ежедневный бонус: {'доступен' if can else 'уже получен сегодня'}\n🔥 Серия: {p.daily_streak} дней", inline(rows))

@dp.callback_query(F.data=="daily")
async def daily(c):
    p=get_player(c.from_user.id); now=int(time.time())
    if now-p.daily_last<86400:
        await c.answer(); return await bank(c, notice="🎁 Бонус уже получен сегодня.")
    if p.daily_last and now-p.daily_last<=172800: p.daily_streak+=1
    else: p.daily_streak=1
    reward=50+p.daily_streak*25; p.coins+=reward; p.daily_last=now; save_data()
    await c.answer(); await bank(c, notice=f"🎁 +{reward} 💰")

@dp.callback_query(F.data=="city:workshop")
async def workshop(c, notice=""):
    p=get_player(c.from_user.id); cost=500*(p.soil_level+1); rows=[]
    if p.soil_level<5: rows.append([InlineKeyboardButton(text=f"🪨 Улучшить почву — {cost} 💰",callback_data="upgrade:soil")])
    rows.append([InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")]); await c.answer(); await edit_city_content(c, (f"{notice}\n\n" if notice else "")+f"🔨 Мастерская\n\n🪨 Почва: уровень {p.soil_level}/5\nКаждый уровень ускоряет рост на 10%.\n\nСледующее улучшение: {cost} 💰", inline(rows))

@dp.callback_query(F.data=="upgrade:soil")
async def upgrade(c):
    p=get_player(c.from_user.id); cost=500*(p.soil_level+1)
    if p.soil_level>=5:
        await c.answer(); return await workshop(c, notice="🪨 Максимальный уровень.")
    if p.coins<cost:
        await c.answer(); return await workshop(c, notice="💰 Недостаточно монет.")
    p.coins-=cost; p.soil_level+=1; save_data(); await c.answer(); await workshop(c, notice="🪨 Почва улучшена!")

def orchard_yield(p):
    return ORCHARD_YIELD.get(min(5,max(1,p.orchard_level)),6)

def finish_apple_processing(p):
    proc=p.apple_processing if isinstance(p.apple_processing,dict) else {}
    if not proc: return None
    finish=float(proc.get("finish_at",0)); product=proc.get("product","")
    if product and finish and time.time()>=finish:
        count=2 if equipment_double(p,5) else 1
        p.inventory[product]=p.inventory.get(product,0)+count
        p.apple_processing={}
        return (product,count)
    return None

def orchard_status_text(p):
    finish_apple_processing(p)
    now=time.time(); nxt=p.orchard_next_collect
    if nxt<=0 or now>=nxt:
        collect_text=f"🍎 Доступно для сбора: {orchard_yield(p)} яблок"
    else:
        left=int(nxt-now); h=left//3600; m=(left%3600)//60; s=left%60
        collect_text=f"⏳ Следующий сбор через: {h}ч {m}м {s}с" if h else f"⏳ Следующий сбор через: {m}м {s}с"
    proc=p.apple_processing
    if proc:
        left=max(0,int(float(proc.get("finish_at",0))-now)); ph=left//3600; pm=(left%3600)//60; ps=left%60
        processing=f"\n🏭 Сейчас готовится: {proc.get('product','')}\n⏱ Осталось: {ph}ч {pm}м {ps}с" if ph else f"\n🏭 Сейчас готовится: {proc.get('product','')}\n⏱ Осталось: {pm}м {ps}с"
    else:
        processing="\n🏭 Переработка свободна"
    return collect_text+processing

@dp.callback_query(F.data=="city:garden")
async def garden(c, notice=""):
    p=get_player(c.from_user.id)
    if p.level<15:
        text=f"🌳 Сад\n\n🔒 Открывается на 15 уровне.\nТвой уровень: {p.level}"; kb=inline([[InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")]])
    else:
        finish_apple_processing(p)
        key="Яблоки"; rare1=p.inventory.get("Золотые яблоки",0); rare2=p.inventory.get("Сияющие яблоки",0); now=time.time(); ready=p.orchard_next_collect<=0 or now>=p.orchard_next_collect
        y=orchard_yield(p)
        text=(f"🌳 Сад\n\n🍎 Яблоки: {p.inventory.get(key,0)}\n✨ Золотые яблоки: {rare1}\n💎 Сияющие яблоки: {rare2}\n\n🌳 Уровень дерева: {p.orchard_level}/5\nДерево приносит {y} яблок раз в час.\n\n{orchard_status_text(p)}")
        rows=[]
        if ready: rows.append([InlineKeyboardButton(text=f"🍎 Собрать {y} яблок",callback_data="garden:collect")])
        rows.append([InlineKeyboardButton(text="🏭 Переработка",callback_data="garden:processing")])
        rows.append([InlineKeyboardButton(text="💰 Продать яблоки",callback_data="city:market")])
        if p.orchard_level<5: rows.append([InlineKeyboardButton(text=f"⬆️ Улучшить дерево — {ORCHARD_COST[p.orchard_level]} 💰",callback_data="garden:upgrade")])
        rows.append([InlineKeyboardButton(text="📦 Заказы",callback_data="city:orders")])
        rows.append([InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")]); kb=inline(rows)
    save_data(); await c.answer(); await edit_garden_content(c,(f"{notice}\n\n" if notice else "")+text,kb)

@dp.callback_query(F.data=="garden:collect")
async def garden_collect(c):
    p=get_player(c.from_user.id); now=time.time(); y=orchard_yield(p)
    if p.orchard_next_collect>0 and now<p.orchard_next_collect:
        left=int(p.orchard_next_collect-now); return await c.answer(f"⏳ Ещё {left//60} мин. до сбора.")
    p.inventory["Яблоки"]=p.inventory.get("Яблоки",0)+y; p.total_harvest+=y; levels=add_xp(p,50); p.orchard_next_collect=now+3600
    # Редкие яблоки выпадают при обычном сборе.
    golden_drop = random.random()<0.05
    shining_drop = random.random()<0.01
    if golden_drop: p.inventory["Золотые яблоки"]=p.inventory.get("Золотые яблоки",0)+1
    if shining_drop: p.inventory["Сияющие яблоки"]=p.inventory.get("Сияющие яблоки",0)+1
    if event_active():
        p.event_score += y * 3
        if golden_drop: p.event_score += 20
        if shining_drop: p.event_score += 50
    save_data(); await c.answer(); await garden(c,notice=f"🍎 Собрано {y} яблок!"); await send_level_up_notification(c.from_user.id, levels)

@dp.callback_query(F.data=="garden:upgrade")
async def garden_upgrade(c):
    p=get_player(c.from_user.id); lvl=p.orchard_level
    if lvl>=5: return await c.answer("🌳 Максимальный уровень дерева.")
    cost=ORCHARD_COST[lvl]
    if p.coins<cost: await c.answer("💰 Недостаточно монет."); return await garden(c,notice=f"Нужно {cost} 💰.")
    p.coins-=cost; p.orchard_level+=1; save_data(); await c.answer(); await garden(c,notice=f"🌳 Дерево улучшено до {p.orchard_level} уровня!\n🍎 Теперь приносит {orchard_yield(p)} яблок в час.")

def apple_processing_kb(p):
    rows=[]
    proc=p.apple_processing
    if proc:
        rows.append([InlineKeyboardButton(text="⏳ Переработка занята",callback_data="garden:processing")])
    else:
        for product,d in APPLE_PRODUCTS.items():
            source=d.get("source","Яблоки"); have=p.inventory.get(source,0); rows.append([InlineKeyboardButton(text=f"{d['emoji']} {product} — {d['input']} {source} · {d['time']//60} мин",callback_data=f"garden:process:{product}")])
    rows.append([InlineKeyboardButton(text="⬅️ В сад",callback_data="city:garden")]); return inline(rows)

@dp.callback_query(F.data=="garden:processing")
async def garden_processing(c, notice=""):
    p=get_player(c.from_user.id); ready=finish_apple_processing(p); save_data(); now=time.time(); proc=p.apple_processing
    lines=["🏭 Переработка","","🍎 Яблоки: " + str(p.inventory.get("Яблоки",0)),"✨ Золотые яблоки: " + str(p.inventory.get("Золотые яблоки",0)),"💎 Сияющие яблоки: " + str(p.inventory.get("Сияющие яблоки",0)),""]
    if ready:
        ready_product, ready_count = ready
        lines.append(f"🎉 Готово: {APPLE_PRODUCTS[ready_product]['emoji']} {ready_product} × {ready_count}\n")
    if proc:
        left=max(0,int(float(proc.get('finish_at',0))-now)); lines.append(f"🔄 Готовится: {proc['product']}\n⏱ Осталось: {left//60}м {left%60}с")
    else:
        lines.append("Выбери, что переработать:")
    if notice: lines.insert(0,notice); lines.insert(1,"")
    await c.answer(); await c.message.edit_caption(caption="\n".join(lines),reply_markup=apple_processing_kb(p))

@dp.callback_query(F.data.startswith("garden:process:"))
async def garden_process(c):
    product=c.data.split(":",2)[2]; p=get_player(c.from_user.id)
    if product not in APPLE_PRODUCTS: return await c.answer("Неизвестный рецепт.")
    finish_apple_processing(p)
    if p.apple_processing: return await c.answer("⏳ Переработка уже занята.")
    d=APPLE_PRODUCTS[product]; source=d.get("source","Яблоки"); have=p.inventory.get(source,0)
    if have<d["input"]:
        return await c.answer(f"Нужно {d['input']} {source}.")
    p.inventory[source]=have-d["input"]; p.apple_processing={"product":product,"finish_at":time.time()+d["time"]}; save_data(); await c.answer(f"🏭 Запущено: {product}"); await garden_processing(c)

@dp.callback_query(F.data=="city:npc")
async def npc(c, notice=""):
    p=get_player(c.from_user.id); stage=p.npc_stage
    if stage==0: text="🧑‍🌾 Старый фермер\n\n«Добро пожаловать! Собери свой первый урожай, и я дам тебе награду.»"; btn="🌾 Я собрал урожай"
    elif stage==1: text="🧑‍🌾 Старый фермер\n\n«Отлично! Теперь заработай 100 монет на рынке.»"; btn="💰 Я заработал 100 монет"
    else: text="🧑‍🌾 Старый фермер\n\n«Ты уже настоящий хозяин фермы. Продолжай развиваться!»"; btn="🏆 Спасибо"
    rows=[]
    if stage<2: rows.append([InlineKeyboardButton(text=btn,callback_data="npc:advance")])
    rows.append([InlineKeyboardButton(text="🪓 Дорога лесоруба",callback_data="lumberjack:open")])
    rows.append([InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")]); await c.answer(); await edit_city_content(c, (f"{notice}\n\n" if notice else "")+text, inline(rows))

@dp.callback_query(F.data=="npc:advance")
async def npc_advance(c):
    p=get_player(c.from_user.id)
    levels=[]
    if p.npc_stage==0 and p.total_harvest>0:
        p.npc_stage=1; reward=100*(2 if equipment_double(p,1) else 1); p.coins+=reward; levels=add_xp(p,50); msg=f"🎁 +{reward} 💰 и 50 XP!"
    elif p.npc_stage==1 and p.earned_coins>=100:
        p.npc_stage=2; reward=250*(2 if equipment_double(p,1) else 1); p.coins+=reward; levels=add_xp(p,100); msg=f"🎁 +{reward} 💰 и 100 XP!"
    else:
        await c.answer(); return await npc(c, notice="🧑‍🌾 Выполни условие сначала.")
    save_data(); await c.answer(); await npc(c, notice=msg); await send_level_up_notification(c.from_user.id, levels)

@dp.callback_query(F.data=="friends:rating")
async def rating(c):
    data=sorted(players.items(),key=lambda x:(x[1].level,x[1].earned_coins,x[1].total_harvest),reverse=True)[:10]
    lines=["🏆 Рейтинг фермеров\n"]
    changed=False
    for i,(uid,p) in enumerate(data,1):
        nickname=p.nickname or ""
        if not nickname:
            try:
                chat=await bot.get_chat(uid)
                nickname=getattr(chat, "username", None) or getattr(chat, "first_name", None) or "Игрок"
                p.nickname=nickname
                changed=True
            except Exception:
                nickname="Игрок"
        # Имя выводится обычным текстом без @ — оно не кликабельно.
        name_style=cosmetic_display(p,"name")
        badge=cosmetic_display(p,"badge")
        title_cosmetic=cosmetic_display(p,"title")
        extras=" ".join(x for x in (name_style, badge, title_cosmetic) if x)
        if extras:
            extras=" · "+extras
        lines.append(f"{i}. 👤 {nickname}{extras} — 🏆 Ур. {p.level} — 💰 {p.earned_coins}")
    if changed:
        save_data()
    rows=[[InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")]]
    await c.answer(); await edit_rating_content(c, "\n".join(lines), inline(rows))

@dp.callback_query(F.data=="friends:fish_rating")
async def fish_rating(c):
    data=[]
    for uid,p in players.items():
        rolls=list(fishing_state(p).get("rolls") or [])
        if len(rolls)>=3:
            data.append((fishing_points(p), p.level, p))
    data.sort(key=lambda x:(x[0],x[1]), reverse=True)
    lines=["🎣 Рейтинг рыболовов\n"]
    if not data:
        lines.append("Пока никто не начал рыбалку.")
    else:
        medals={1:"🥇",2:"🥈",3:"🥉"}
        for i,(score,level,p) in enumerate(data[:20],1):
            prefix=medals.get(i,f"{i}.")
            lines.append(f"{prefix} 👤 {p.nickname or 'Игрок'} — 🎯 {score} — Ур. {level}")
    rows=[[InlineKeyboardButton(text="🏆 Рейтинг фермеров",callback_data="friends:rating")],
          [InlineKeyboardButton(text="⬅️ Друзья и соседи",callback_data="friends:home")]]
    await c.answer(); await edit_rating_content(c,"\n".join(lines),inline(rows))

# ---------- EXPANDED EVENTS ----------
def event_status_text():
    if not event_state.get("started_at"):
        return "🟢 Следующий ивент ещё не запущен. Следи за новостями!"
    if event_state.get("ended") or event_state.get("rewards_distributed"):
        return "🏁 Ивент завершён. Награды выданы участникам."
    left=event_time_left()
    h,rem=divmod(left,3600); m,_=divmod(rem,60)
    return f"🟢 Ивент активен\n⏱ Осталось: {h}ч {m}м"

def event_view(p):
    status=event_status_text()
    rank=event_rank_for(next((uid for uid,x in players.items() if x is p), -1))
    rank_text=f"\n🏅 Твоё место: #{rank}" if rank else ""
    text=(f"🎪 {EVENT_NAME}\n\n"
          "Собирай урожай и набирай очки события. Чем выше место в рейтинге — тем больше награда!\n\n"
          f"{status}\n\n⭐ Твои очки: {p.event_score:,}{rank_text}\n\n"
          "🎁 Топ-10 получают разные награды. Остальные участники получают награду за участие.")
    rows=[
        [InlineKeyboardButton(text="📊 Рейтинг игроков",callback_data="event:rating")],
        [InlineKeyboardButton(text="⭐ Мой результат",callback_data="event:me")],
        [InlineKeyboardButton(text="🎁 Награды",callback_data="event:rewards")],
        [InlineKeyboardButton(text="📜 История ивентов",callback_data="event:history")],
        [InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")],
    ]
    return text,inline(rows)

def event_rewards_text():
    lines=[f"🎁 Награды — {EVENT_NAME}","","🥇 1 место — "+event_reward_text(1),"🥈 2 место — "+event_reward_text(2),"🥉 3 место — "+event_reward_text(3)]
    for place in range(4,11):
        lines.append(f"{place} место — {event_reward_text(place)}")
    lines += ["","🎯 Участники вне топ-10 — 🔮 3 сферы"]
    return "\n".join(lines)

async def distribute_event_rewards():
    if event_state.get("rewards_distributed") or not event_state.get("started_at"):
        return
    data=event_rating_lines(100000)
    event_state["ended"]=True
    event_state["rewards_distributed"]=True
    save_event()
    for place,(uid,p) in enumerate(data,1):
        reward=event_reward_for_place(place)
        p.event_participations += 1
        if place == 1: p.event_wins += 1
        if place <= 3: p.event_podiums += 1
        if reward.get("coins"): p.coins += reward["coins"]; p.earned_coins += reward["coins"]
        p.spheres += reward.get("spheres",0)
        premium_count=reward.get("premium",0)
        premium_names=[]
        for j in range(premium_count):
            crop=PREMIUM_REWARD_CROPS[(place+j-1)%len(PREMIUM_REWARD_CROPS)]
            p.inventory[CROPS[crop]["seed"]]=p.inventory.get(CROPS[crop]["seed"],0)+1
            premium_names.append(crop)
        if reward.get("title"):
            key=reward["title"]
            if key not in p.owned_cosmetics: p.owned_cosmetics.append(key)
            if not p.cosmetics.get("title"): p.cosmetics["title"]=key
        reward_desc=event_reward_text(place)
        p.event_history.append({"id":EVENT_ID,"name":EVENT_NAME,"place":place,"score":p.event_score,"reward":reward_desc})
        text=(f"🏁 {EVENT_NAME} завершён!\n\n"
              f"🏆 Твоё место: #{place}\n⭐ Очки: {p.event_score:,}\n\n"
              f"🎁 Твоя награда: {reward_desc}")
        if premium_names:
            text += "\n\n" + "\n".join(f"🌱 +1 семя: {x}" for x in premium_names)
        try:
            await bot.send_message(uid,text,reply_markup=inline([[InlineKeyboardButton(text="📊 Посмотреть рейтинг",callback_data="event:rating")],[InlineKeyboardButton(text="🏆 История ивентов",callback_data="event:history")]]))
        except Exception:
            logging.exception("event reward notification failed for user %s",uid)
    # Участники, попавшие в data, уже получили награду. Все остальные с event_score=0 не считаются участниками.
    save_data()

def event_reset_scores():
    for p in players.values():
        p.event_score=0

def chairman_application_status(p: Player) -> str:
    until = float(getattr(p, "chairman_application_until", 0.0) or 0.0)
    if until <= 0:
        return "📋 Заявление ещё не подавалось."
    left = int(until - time.time())
    if left > 0:
        hours, rem = divmod(left, 3600)
        minutes = rem // 60
        return f"⏳ Заявление рассматривается.\nОсталось: {hours} ч. {minutes} мин."
    return "✅ Заявление одобрено.\nРазрешения на сбор урожая соседей доступны."

def chairman_kb(p: Player):
    until = float(getattr(p, "chairman_application_until", 0.0) or 0.0)
    rows = []
    if until <= time.time():
        rows.append([InlineKeyboardButton(text="📝 Подать заявление", callback_data="city:chairman:apply")])
    else:
        rows.append([InlineKeyboardButton(text="⏳ Заявление на рассмотрении", callback_data="city:chairman:wait")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в город", callback_data="city:home")])
    return inline(rows)

@dp.callback_query(F.data=="city:chairman")
async def city_chairman(c):
    p = get_player(c.from_user.id)
    await c.answer()
    text = ("👨‍💼 Председатель кооператива\n\n"
            "Добро пожаловать, здесь вы можете подать заявление на сбор урожая с участков ваших соседей.\n"
            "Процесс оформления занимает 8 часов.\n\n" + chairman_application_status(p))
    await edit_city_content(c, text, chairman_kb(p))

@dp.callback_query(F.data=="city:chairman:wait")
async def city_chairman_wait(c):
    p = get_player(c.from_user.id)
    until = float(getattr(p, "chairman_application_until", 0.0) or 0.0)
    if until > 0 and time.time() >= until:
        p.theft_permissions = 4
        save_data()
    await c.answer()
    text = ("👨‍💼 Председатель кооператива\n\n"
            "Добро пожаловать, здесь вы можете подать заявление на сбор урожая с участков ваших соседей.\n"
            "Процесс оформления занимает 8 часов.\n\n" + chairman_application_status(p))
    await edit_city_content(c, text, chairman_kb(p))

@dp.callback_query(F.data=="city:chairman:apply")
async def city_chairman_apply(c):
    p = get_player(c.from_user.id)
    now = time.time()
    until = float(getattr(p, "chairman_application_until", 0.0) or 0.0)
    if until > now:
        return await c.answer("⏳ Заявление уже рассматривается.", show_alert=True)
    p.chairman_application_until = now + 8 * 3600
    save_data()
    await c.answer("📝 Заявление принято. Оформление займёт 8 часов.", show_alert=True)
    text = ("👨‍💼 Председатель кооператива\n\n"
            "Добро пожаловать, здесь вы можете подать заявление на сбор урожая с участков ваших соседей.\n"
            "Процесс оформления занимает 8 часов.\n\n" + chairman_application_status(p))
    await edit_city_content(c, text, chairman_kb(p))

@dp.callback_query(F.data=="city:events")
async def events_page(c):
    p=get_player(c.from_user.id); remember_nickname(p,c.from_user); save_data()
    text,kb=event_view(p); await c.answer(); await edit_event_content(c,text,kb)

@dp.callback_query(F.data=="event:rating")
async def event_rating(c):
    await c.answer(); text=event_rating_text(EVENT_MAX_RANK)
    kb=inline([[InlineKeyboardButton(text="⭐ Мой результат",callback_data="event:me"),InlineKeyboardButton(text="🎁 Награды",callback_data="event:rewards")],[InlineKeyboardButton(text="⬅️ К ивентам",callback_data="city:events")]])
    await edit_boss_rating_content(c,text,kb)

@dp.callback_query(F.data=="event:me")
async def event_me(c):
    p=get_player(c.from_user.id); rank=event_rank_for(c.from_user.id)
    if not rank:
        await c.answer("⭐ Пока у тебя 0 очков события.",show_alert=True); return
    await c.answer(f"🏅 Место #{rank} · ⭐ {p.event_score:,} очков",show_alert=True)

@dp.callback_query(F.data=="event:rewards")
async def event_rewards(c):
    await c.answer(); await edit_city_content(c,event_rewards_text(),inline([[InlineKeyboardButton(text="⬅️ К ивентам",callback_data="city:events")]]))

def boss_history_text(p):
    if boss_event.get("defeated") or boss_event.get("hp", BOSS_MAX_HP) <= 0:
        result = "🏆 Босс повержен!"
    elif boss_event.get("started_at") and boss_time_left() <= 0:
        result = "⏰ Срок события истёк"
    elif boss_event.get("started_at"):
        result = f"🟢 Событие ещё активно · ❤️ {max(0, boss_event.get('hp', BOSS_MAX_HP)):,} HP"
    else:
        result = "⚪ Событие ещё не запускалось"
    return (
        "🐀 Полевой Вредитель\n\n"
        f"{result}\n"
        f"❤️ Максимум HP: {BOSS_MAX_HP:,}\n\n"
        f"⚔️ Твой вклад: {p.boss_damage:,} урона\n\n"
        "Первое событие FarmWorld. Здесь можно посмотреть свой вклад и итоговый рейтинг участников."
    )

@dp.callback_query(F.data=="event:profile_history")
async def event_profile_history(c):
    p=get_player(c.from_user.id); await c.answer()
    # В истории из Профиля показываем только статистику игрока,
    # без списка отдельных ивентов — подробная история открывается из раздела 🎪 Ивенты.
    rows=[[InlineKeyboardButton(text="⬅️ Профиль",callback_data="profile:open")]]
    await c.message.edit_text(event_profile_history_text(p),reply_markup=inline(rows))

@dp.callback_query(F.data=="event:history")
async def event_history(c):
    p=get_player(c.from_user.id); await c.answer()
    rows=[]
    if boss_event.get("started_at") or boss_event.get("defeated") or any(x.boss_damage > 0 for x in players.values()):
        rows.append([InlineKeyboardButton(text="🐀 Полевой Вредитель",callback_data="event:history_boss")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад",callback_data="city:events")])
    # Экран Ивенты использует фото, поэтому edit_text здесь не подходит.
    # Переключаемся через общий обработчик городского контента, который корректно
    # заменяет/обновляет картинку и клавиатуру.
    await edit_city_content(c, event_history_text(), inline(rows))

@dp.callback_query(F.data=="event:history_boss")
async def event_history_boss(c):
    p=get_player(c.from_user.id); await c.answer()
    text=boss_history_text(p)
    rows=[
        [InlineKeyboardButton(text="🏆 Мой вклад",callback_data="boss:me")],
        [InlineKeyboardButton(text="📊 Рейтинг игроков",callback_data="boss:rating")],
        [InlineKeyboardButton(text="⬅️ История ивентов",callback_data="event:history")],
    ]
    if c.message.photo:
        try:
            await c.message.edit_media(media=InputMediaPhoto(media=FSInputFile("boss.jpg"),caption=text),reply_markup=inline(rows))
        except Exception:
            await c.message.edit_caption(caption=text,reply_markup=inline(rows))
    else:
        await c.message.answer_photo(photo=FSInputFile("boss.jpg"),caption=text,reply_markup=inline(rows))

# ---------- BOSS EVENT ----------
def boss_view(p):
    if not boss_event["started_at"]:
        status="🟢 Событие ещё не началось. Нажми «Начать событие»!"
    elif boss_event["defeated"] or boss_event["hp"] <= 0:
        status="🏆 Босс повержен! FarmWorld спасён!"
    elif boss_time_left() <= 0:
        status="⏰ Время события истекло. Босс скрылся."
    else:
        left=boss_time_left(); h,left2=divmod(left,3600); m,_=divmod(left2,60)
        status=f"❤️ HP босса: {max(0,boss_event['hp']):,} / {BOSS_MAX_HP:,}\n⏱ Осталось: {h}ч {m}м"
    rows=[]
    if not boss_event["started_at"]:
        rows.append([InlineKeyboardButton(text="🐀 Начать событие",callback_data="boss:start")])
    rows.append([InlineKeyboardButton(text="🏆 Мой вклад",callback_data="boss:me")])
    if boss_event["defeated"] or boss_event["hp"] <= 0:
        rows.append([InlineKeyboardButton(text="📊 Рейтинг игроков",callback_data="boss:rating")])
    rows.append([InlineKeyboardButton(text="⬅️ В город",callback_data="city:home")])
    text=("🐀 ПОЛЕВОЙ ВРЕДИТЕЛЬ\n\n"
          "Огромный грызун пробрался на поля и уничтожает посевы!\n\n"+status+
          f"\n\n⚔️ Твой вклад: {p.boss_damage:,} урона\n\n🌾 Собирай урожай во время события — каждый сбор наносит урон боссу!")
    return text, inline(rows)

@dp.callback_query(F.data=="boss:start")
async def boss_start(c):
    if not boss_event["started_at"]:
        boss_event.update({"started_at":time.time(),"hp":BOSS_MAX_HP,"defeated":False})
        save_boss()
    p=get_player(c.from_user.id); text,kb=boss_view(p)
    await c.answer("🐀 Событие началось!")
    await c.message.edit_caption(caption=text, reply_markup=kb)

@dp.callback_query(F.data=="boss:me")
async def boss_me(c):
    p=get_player(c.from_user.id)
    await c.answer(f"⚔️ Твой вклад: {p.boss_damage:,} урона", show_alert=False)

@dp.callback_query(F.data=="boss:rating")
async def boss_rating(c):
    await c.answer()
    text = boss_rating_text()
    kb = inline([[
        InlineKeyboardButton(text="🏆 Мой вклад", callback_data="boss:me"),
        InlineKeyboardButton(text="⬅️ История ивентов", callback_data="event:history_boss"),
    ]])
    await edit_boss_rating_content(c, text, kb)

async def fishing_lottery_draw():
    now=time.time()
    next_draw=float(fishing_lottery.get("next_draw",0) or 0)
    if next_draw<=0:
        next_draw=((int(now)//(4*3600))+1)*(4*3600)
        fishing_lottery["next_draw"]=next_draw; save_fishing_lottery(); return
    if now < next_draw: return
    # Draw at most once per elapsed 4-hour slot; best active/completed fisherman wins.
    candidates=[]
    for uid,p in players.items():
        f=fishing_state(p)
        rolls=list(f.get("rolls") or [])
        if len(rolls)>=3 and f.get("started_at"):
            candidates.append((fishing_points(p), p.level, uid, p))
    if candidates:
        candidates.sort(key=lambda x:(x[0],x[1]), reverse=True)
        _,_,uid,winner=candidates[0]
        winner.inventory[FISH_LOTUS_ITEM]=winner.inventory.get(FISH_LOTUS_ITEM,0)+1
        save_data()
    fishing_lottery["next_draw"]=((int(now)//(4*3600))+1)*(4*3600)
    save_fishing_lottery()

# ---------- ADMIN ----------
@dp.message(F.text=="🛠️ Админ-панель")
async def admin(m):
    if m.from_user.id!=ADMIN_ID: return
    await m.answer("🛠️ Админ-панель\n\nВыберите действие:",reply_markup=admin_kb())
@dp.callback_query(F.data=="admin:news_help")
async def admin_news_help(c):
    if c.from_user.id!=ADMIN_ID: return
    await c.answer()
    await c.message.edit_text(
        "📰 Публикация новости\n\n"
        "Чтобы опубликовать пост в канал @FarmWrld, отправь команду:\n\n"
        "/news Текст новости\n\n"
        "Бот отправит этот текст в канал.\n"
        "Кнопка ниже возвращает в админ-панель.",
        reply_markup=inline([[InlineKeyboardButton(text="⬅️ В админ-панель",callback_data="admin:back")]])
    )

@dp.message(Command("news"))
async def publish_news(m):
    if m.from_user.id!=ADMIN_ID: return
    text=m.text.partition(" ")[2].strip()
    if not text:
        await m.answer("📰 Напиши текст после команды.\n\nПример:\n/news 🌱 В FarmWorld вышло новое обновление!")
        return
    try:
        await bot.send_message(chat_id="@FarmWrld", text=text)
        # Удаляем длинную админскую команду после успешной публикации,
        # чтобы она не засоряла чат. Подтверждение оставляем.
        try:
            await m.delete()
        except Exception:
            logging.exception("Could not delete /news command")
        await m.answer("✅ Новость опубликована в @FarmWrld")
    except Exception as e:
        logging.exception("publish_news failed")
        await m.answer("❌ Не удалось опубликовать новость. Проверь, что бот является администратором канала @FarmWrld с правом публикации сообщений.")

@dp.callback_query(F.data=="admin:add_money")
async def admin_money(c):
    if c.from_user.id!=ADMIN_ID: return
    p=get_player(c.from_user.id); p.coins+=1_000_000; save_data(); await c.answer(); await c.message.edit_text("💰 Начислено 1.000.000!\n\n🛠️ Админ-панель\n\nВыберите действие:",reply_markup=admin_kb())

@dp.callback_query(F.data=="admin:add_spheres_100")
async def admin_spheres_100(c):
    if c.from_user.id!=ADMIN_ID: return
    p=get_player(c.from_user.id); p.spheres+=100; save_data(); await c.answer(); await c.message.edit_text("🔮 Выдано 100 сфер!\n\n🛠️ Админ-панель\n\nВыберите действие:",reply_markup=admin_kb())

@dp.callback_query(F.data=="admin:add_spheres_1000")
async def admin_spheres_1000(c):
    if c.from_user.id!=ADMIN_ID: return
    p=get_player(c.from_user.id); p.spheres+=1000; save_data(); await c.answer(); await c.message.edit_text("🔮 Выдано 1.000 сфер!\n\n🛠️ Админ-панель\n\nВыберите действие:",reply_markup=admin_kb())

@dp.callback_query(F.data=="admin:activate_vip")
async def admin_activate_vip(c):
    if c.from_user.id!=ADMIN_ID: return
    p=get_player(c.from_user.id)
    now=time.time()
    # Админская выдача VIP: 7 дней. Если VIP уже активен, продлеваем ещё на 7 дней.
    p.vip_until=max(now,p.vip_until)+7*86400
    p.vip_trial_claimed=True
    save_data()
    await c.answer("👑 VIP выдан на 7 дней!")
    await c.message.edit_text(
        f"👑 VIP выдан!\n\n⏳ Срок: 7 дней\n{vip_status(p)}\n\n🛠️ Админ-панель\n\nВыберите действие:",
        reply_markup=admin_kb()
    )

@dp.callback_query(F.data=="admin:event_start")
async def admin_event_start(c):
    if c.from_user.id!=ADMIN_ID: return
    if event_state.get("started_at") and not event_state.get("ended"):
        return await c.answer("🎪 Ивент уже запущен.",show_alert=True)
    event_state.update({"id":EVENT_ID,"name":EVENT_NAME,"started_at":time.time(),"ended":False,"rewards_distributed":False})
    event_reset_scores(); save_event(); save_data()
    announcement=("🎪 НОВЫЙ ИВЕНТ НАЧАЛСЯ!\n\n"
                  f"{EVENT_NAME}\n\n"
                  "🌾 Собирай урожай и набирай очки.\n"
                  "🏆 Чем выше место в рейтинге — тем больше награда!\n\n"
                  "⏱ Длительность: 48 часов")
    for uid in list(players.keys()):
        try:
            await bot.send_message(uid,announcement,reply_markup=inline([[InlineKeyboardButton(text="🎪 Открыть ивент",callback_data="city:events")]]))
        except Exception:
            logging.exception("event start notification failed for user %s",uid)
    await c.answer("🎪 Следующий ивент запущен!")
    await c.message.edit_text("🎪 Следующий ивент запущен!\n\n"+EVENT_NAME+"\n⏱ Длительность: 48 часов\n🏆 Награды получат участники по итоговому рейтингу.",reply_markup=admin_kb())

@dp.callback_query(F.data=="admin:reset_confirm")
async def admin_reset_confirm(c):
    if c.from_user.id!=ADMIN_ID: return
    await c.answer()
    await c.message.edit_text(
        "⚠️ Сброс прогресса\n\n"
        "Будет полностью сброшен твой игровой прогресс: монеты, сферы, семена, урожай, грядки, уровень, XP, задания, достижения, ежедневная серия, NPC и улучшения.\n\n"
        "После сброса аккаунт вернётся к стартовым значениям.",
        reply_markup=inline([
            [InlineKeyboardButton(text="🗑️ Да, сбросить",callback_data="admin:reset")],
            [InlineKeyboardButton(text="⬅️ Отмена",callback_data="admin:back")]
        ])
    )

@dp.callback_query(F.data=="admin:reset")
async def admin_reset(c):
    if c.from_user.id!=ADMIN_ID: return
    players[c.from_user.id]=Player()
    save_data()
    await c.answer()
    await c.message.edit_text(
        "✅ Прогресс полностью сброшен.\n\n"
        "Ты снова начинаешь с нуля.\n\n"
        "🛠️ Админ-панель\n\nВыберите действие:",
        reply_markup=admin_kb()
    )

@dp.callback_query(F.data=="admin:back")
async def admin_back(c):
    if c.from_user.id!=ADMIN_ID: return
    await c.answer()
    await c.message.edit_text("🛠️ Админ-панель\n\nВыберите действие:",reply_markup=admin_kb())

# ---------- ACHIEVEMENT HOOK + NOTIFICATIONS ----------
async def event_monitor_loop():
    while True:
        try:
            if event_state.get("started_at") and not event_state.get("ended") and event_time_left() <= 0:
                await distribute_event_rewards()
            await fishing_lottery_draw()
        except Exception:
            logging.exception("event monitor loop failed")
        await asyncio.sleep(5)

async def farm_notification_loop():
    while True:
        try:
            for uid, p in list(players.items()):
                newly_ready = []
                for i, b in enumerate(p.beds, 1):
                    was_growing = b.state == "growing"
                    update_bed(b)
                    if was_growing and b.state == "ready" and not b.notified:
                        b.notified = True
                        newly_ready.append((i, b.crop))

                if newly_ready:
                    count = len(newly_ready)
                    word = "грядке" if count == 1 else "грядках"
                    crop_groups = {}
                    for bed_no, crop in newly_ready:
                        crop_groups.setdefault(crop, []).append(bed_no)

                    lines = [
                        "📬 <b>У вас есть новости!</b>",
                        "",
                        "🌾 <b>Ваш урожай готов к сбору! 🚜</b>",
                        "",
                        f"У вас созрел урожай на {count} обычной {word}!",
                        "",
                    ]
                    for crop, beds in crop_groups.items():
                        beds_text = ", ".join(map(str, beds))
                        lines.append(f"{crop_emoji(crop)} <b>{crop} на грядках: {beds_text}</b>")

                    lines += [
                        "",
                        "Соберите урожай, пока он не пропал!"
                    ]

                    try:
                        await bot.send_message(
                            uid,
                            "\n".join(lines),
                            parse_mode="HTML",
                            reply_markup=inline([[InlineKeyboardButton(text="🌾 На ферму", callback_data="farm:list")]])
                        )
                    except Exception:
                        logging.exception("notification failed")
                    save_data()
        except Exception:
            logging.exception("notification loop failed")
        await asyncio.sleep(2)

@dp.message(Command("ach"))
async def ach_short(m): await show_achievements(m)

@dp.message()
async def unknown(m): await m.answer("Используй кнопки меню 👇",reply_markup=main_kb(m.from_user.id))

async def main():
    load_data()
    logging.info("FarmWorld bot started")
    task=asyncio.create_task(farm_notification_loop())
    event_task=asyncio.create_task(event_monitor_loop())
    try: await dp.start_polling(bot)
    finally:
        task.cancel(); event_task.cancel()
        with contextlib.suppress(asyncio.CancelledError): await task
        with contextlib.suppress(asyncio.CancelledError): await event_task
        save_data(); save_event()

if __name__=="__main__": asyncio.run(main())
