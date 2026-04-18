import discord
from discord.ext import commands
import sqlite3
import random
import asyncio
from datetime import datetime, UTC
import os

TOKEN = os.getenv("TOKEN")
PREFIX = "!"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# =========================
# BANCO SQLITE
# =========================
conn = sqlite3.connect("levels.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0
)
""")
conn.commit()

# =========================
# CONFIGURAÇÕES
# =========================
LEVEL_ROLES = {
    1: "Noob de Guerra",
    5: "Sobrevivente",
    10: "Caçador",
    20: "Executor",
    30: "Predador",
    40: "Aniquilador",
    50: "Deus da Guerra ⚡"
}

ROLE_LEVELS = sorted(LEVEL_ROLES.keys())

# XP de mensagem
MESSAGE_XP_MIN = 10
MESSAGE_XP_MAX = 15
MESSAGE_COOLDOWN = 20  # segundos
MIN_MESSAGE_LENGTH = 5

# XP de call
VOICE_XP_AMOUNT = 12          # XP por ciclo
VOICE_INTERVAL_SECONDS = 1200 # 20 minutos
VOICE_CHECK_EVERY = 60        # checa a cada 1 minuto

# cooldown anti-spam do chat
xp_cooldown = {}

# controla o tempo em call
voice_start = {}

# =========================
# FUNÇÕES DE XP / LEVEL
# =========================
def xp_needed_for_level(level: int) -> int:
    return 100 + (level * 50)

def add_user_if_not_exists(user_id: int):
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO users (user_id, xp, level) VALUES (?, ?, ?)",
            (user_id, 0, 0)
        )
        conn.commit()

def get_user_data(user_id: int):
    add_user_if_not_exists(user_id)
    cursor.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    return data[0], data[1]

def set_user_data(user_id: int, xp: int, level: int):
    add_user_if_not_exists(user_id)
    cursor.execute(
        "UPDATE users SET xp = ?, level = ? WHERE user_id = ?",
        (xp, level, user_id)
    )
    conn.commit()

def recalculate_level_from_total_xp(total_xp: int):
    if total_xp < 0:
        total_xp = 0

    level = 0
    remaining_xp = total_xp

    while remaining_xp >= xp_needed_for_level(level):
        remaining_xp -= xp_needed_for_level(level)
        level += 1

    return remaining_xp, level

def total_xp_from_user(xp: int, level: int):
    total = xp
    for lv in range(level):
        total += xp_needed_for_level(lv)
    return total

# =========================
# FUNÇÕES DE CARGO
# =========================
async def update_member_roles(member: discord.Member, new_level: int):
    guild = member.guild

    eligible_role_name = None
    for lvl in ROLE_LEVELS:
        if new_level >= lvl:
            eligible_role_name = LEVEL_ROLES[lvl]

    managed_roles = []
    for role_name in LEVEL_ROLES.values():
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            managed_roles.append(role)

    if managed_roles:
        roles_to_remove = [r for r in managed_roles if r in member.roles]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Atualização de cargo por nível")

    if eligible_role_name:
        role_to_add = discord.utils.get(guild.roles, name=eligible_role_name)
        if role_to_add:
            await member.add_roles(role_to_add, reason="Novo cargo por nível")

async def announce_level_up(channel, member: discord.Member, old_level: int, new_level: int):
    if new_level > old_level:
        try:
            await update_member_roles(member, new_level)
        except Exception as e:
            print(f"Erro ao atualizar cargos de {member}: {e}")

        await channel.send(f"🎉 {member.mention} subiu para o **nível {new_level}**!")

        for lvl in ROLE_LEVELS:
            if old_level < lvl <= new_level:
                await channel.send(f"🏅 {member.mention} recebeu o cargo **{LEVEL_ROLES[lvl]}**!")

# =========================
# LOOP DE XP POR CALL
# =========================
async def voice_xp_loop():
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            for guild in bot.guilds:
                for vc in guild.voice_channels:
                    for member in vc.members:
                        if member.bot:
                            continue

                        if not member.voice or not member.voice.channel:
                            voice_start.pop(member.id, None)
                            continue

                        # não ganha se estiver mutado ou surdo
                        if member.voice.self_mute or member.voice.self_deaf:
                            voice_start[member.id] = datetime.now(UTC)
                            continue

                        # inicia contagem se ainda não estiver contando
                        if member.id not in voice_start:
                            voice_start[member.id] = datetime.now(UTC)
                            continue

                        tempo = (datetime.now(UTC) - voice_start[member.id]).total_seconds()

                        if tempo >= VOICE_INTERVAL_SECONDS:
                            current_xp, current_level = get_user_data(member.id)
                            total_xp = total_xp_from_user(current_xp, current_level)
                            total_xp += VOICE_XP_AMOUNT

                            new_xp, new_level = recalculate_level_from_total_xp(total_xp)
                            set_user_data(member.id, new_xp, new_level)

                            if new_level > current_level:
                                # manda no primeiro canal de texto disponível que o bot consiga falar
                                target_channel = None
                                for channel in guild.text_channels:
                                    perms = channel.permissions_for(guild.me)
                                    if perms.send_messages:
                                        target_channel = channel
                                        break

                                if target_channel:
                                    await announce_level_up(target_channel, member, current_level, new_level)
                                else:
                                    try:
                                        await update_member_roles(member, new_level)
                                    except Exception as e:
                                        print(f"Erro ao atualizar cargos por call: {e}")

                            # reinicia contagem do próximo ciclo
                            voice_start[member.id] = datetime.now(UTC)

        except Exception as e:
            print(f"Erro no loop de voz: {e}")

        await asyncio.sleep(VOICE_CHECK_EVERY)

# =========================
# EVENTOS
# =========================
@bot.event
async def on_ready():
    print(f"Bot online como {bot.user}")
    if not hasattr(bot, "voice_loop_started"):
        bot.voice_loop_started = True
        bot.loop.create_task(voice_xp_loop())

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    # entrou na call
    if after.channel and not before.channel:
        voice_start[member.id] = datetime.now(UTC)

    # saiu da call
    elif before.channel and not after.channel:
        voice_start.pop(member.id, None)

    # trocou estado dentro da call
    elif after.channel:
        voice_start.setdefault(member.id, datetime.now(UTC))

@bot.event
async def on_message(message):
    if message.author.bot or message.guild is None:
        return

    # mensagem muito curta não ganha xp
    if len(message.content.strip()) < MIN_MESSAGE_LENGTH:
        await bot.process_commands(message)
        return

    user_id = message.author.id
    now = message.created_at.timestamp()
    last_time = xp_cooldown.get(user_id, 0)

    if now - last_time >= MESSAGE_COOLDOWN:
        xp_cooldown[user_id] = now

        gained_xp = random.randint(MESSAGE_XP_MIN, MESSAGE_XP_MAX)

        current_xp, current_level = get_user_data(user_id)
        total_xp = total_xp_from_user(current_xp, current_level)
        total_xp += gained_xp

        new_xp, new_level = recalculate_level_from_total_xp(total_xp)
        set_user_data(user_id, new_xp, new_level)

        if new_level > current_level:
            await announce_level_up(message.channel, message.author, current_level, new_level)

    await bot.process_commands(message)

# =========================
# COMANDOS
# =========================
@bot.command()
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    xp, level = get_user_data(member.id)
    next_xp = xp_needed_for_level(level)

    embed = discord.Embed(
        title=f"Rank de {member.display_name}",
        color=discord.Color.red(),
        timestamp=datetime.now(UTC)
    )
    embed.add_field(name="Nível", value=str(level), inline=True)
    embed.add_field(name="XP atual", value=f"{xp}/{next_xp}", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)

    await ctx.send(embed=embed)

@bot.command()
async def leaderboard(ctx):
    cursor.execute("SELECT user_id, xp, level FROM users ORDER BY level DESC, xp DESC LIMIT 10")
    rows = cursor.fetchall()

    if not rows:
        await ctx.send("Ainda não há ranking.")
        return

    text = ""
    for i, (user_id, xp, level) in enumerate(rows, start=1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"Usuário {user_id}"
        text += f"**{i}.** {name} - Nível **{level}** | XP **{xp}**\n"

    embed = discord.Embed(
        title="🏆 Leaderboard",
        description=text,
        color=discord.Color.gold(),
        timestamp=datetime.now(UTC)
    )
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setxp(ctx, member: discord.Member, total_xp: int):
    if total_xp < 0:
        await ctx.send("O XP não pode ser negativo.")
        return

    _, old_level = get_user_data(member.id)
    new_xp, new_level = recalculate_level_from_total_xp(total_xp)
    set_user_data(member.id, new_xp, new_level)

    try:
        await update_member_roles(member, new_level)
    except Exception as e:
        print(f"Erro ao atualizar cargos em setxp: {e}")

    await ctx.send(
        f"✅ XP total de {member.mention} definido para **{total_xp}**.\n"
        f"Agora ele está no **nível {new_level}** com **{new_xp} XP** no nível atual."
    )

@bot.command()
@commands.has_permissions(administrator=True)
async def addxp(ctx, member: discord.Member, amount: int):
    if amount < 0:
        await ctx.send("Use um valor positivo.")
        return

    current_xp, current_level = get_user_data(member.id)
    total_xp = total_xp_from_user(current_xp, current_level)
    total_xp += amount

    new_xp, new_level = recalculate_level_from_total_xp(total_xp)
    set_user_data(member.id, new_xp, new_level)

    try:
        await update_member_roles(member, new_level)
    except Exception as e:
        print(f"Erro ao atualizar cargos em addxp: {e}")

    await ctx.send(
        f"✅ Foram adicionados **{amount} XP** para {member.mention}.\n"
        f"Agora ele está no **nível {new_level}** com **{new_xp} XP** no nível atual."
    )

@bot.command()
@commands.has_permissions(administrator=True)
async def givexp(ctx, member: discord.Member, amount: int):
    if amount < 0:
        await ctx.send("Use um valor positivo.")
        return

    current_xp, current_level = get_user_data(member.id)
    total_xp = total_xp_from_user(current_xp, current_level)
    total_xp += amount

    new_xp, new_level = recalculate_level_from_total_xp(total_xp)
    set_user_data(member.id, new_xp, new_level)

    try:
        await update_member_roles(member, new_level)
    except Exception as e:
        print(f"Erro ao atualizar cargos em givexp: {e}")

    await ctx.send(
        f"🎁 {member.mention} recebeu **{amount} XP**.\n"
        f"Agora está no **nível {new_level}** com **{new_xp} XP** no nível atual."
    )

@bot.command()
@commands.has_permissions(administrator=True)
async def removexp(ctx, member: discord.Member, amount: int):
    if amount < 0:
        await ctx.send("Use um valor positivo.")
        return

    current_xp, current_level = get_user_data(member.id)
    total_xp = total_xp_from_user(current_xp, current_level)
    total_xp = max(0, total_xp - amount)

    new_xp, new_level = recalculate_level_from_total_xp(total_xp)
    set_user_data(member.id, new_xp, new_level)

    try:
        await update_member_roles(member, new_level)
    except Exception as e:
        print(f"Erro ao atualizar cargos em removexp: {e}")

    await ctx.send(
        f"✅ Foram removidos **{amount} XP** de {member.mention}.\n"
        f"Agora ele está no **nível {new_level}** com **{new_xp} XP** no nível atual."
    )

@bot.command()
@commands.has_permissions(administrator=True)
async def setlevel(ctx, member: discord.Member, level: int):
    if level < 0:
        await ctx.send("O nível não pode ser negativo.")
        return

    set_user_data(member.id, 0, level)

    try:
        await update_member_roles(member, level)
    except Exception as e:
        print(f"Erro ao atualizar cargos em setlevel: {e}")

    await ctx.send(f"✅ {member.mention} agora está no **nível {level}**.")

@bot.command()
async def ajuda(ctx):
    comandos = (
        f"**Comandos do bot**\n"
        f"`{PREFIX}rank` - mostra seu rank\n"
        f"`{PREFIX}rank @usuario` - mostra o rank de alguém\n"
        f"`{PREFIX}leaderboard` - top 10\n"
        f"`{PREFIX}setxp @usuario quantidade` - define o XP total (admin)\n"
        f"`{PREFIX}addxp @usuario quantidade` - adiciona XP (admin)\n"
        f"`{PREFIX}givexp @usuario quantidade` - recompensa com XP (admin)\n"
        f"`{PREFIX}removexp @usuario quantidade` - remove XP (admin)\n"
        f"`{PREFIX}setlevel @usuario nivel` - define o nível (admin)\n"
    )
    await ctx.send(comandos)

bot.run(TOKEN)
