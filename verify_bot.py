"""
Astrostatistics School — Discord verification bot.

Expected columns (case-insensitive, any order):
    name, email, status, institute, country, Discord Roles

Admin commands (caller must hold one of ADMIN_ROLES):
    !reload                       — re-read all files in attendees/
    !stats                        — show how many attendees are loaded
    !graduate <EditionRole>       — promote that class: remove `student`,
                                    add `veteran`, keep the edition role
    !admins                       — list admin roles
    !admins add <role>            — grant a role admin privileges
    !admins remove <role>         — revoke admin from a role
    !schedule                     — list scheduled graduations
    !schedule add <iso_date> <edition_role>
                                  — schedule an auto-graduation
    !schedule remove <number>     — cancel a scheduled graduation

Runtime changes to admin roles and the schedule are persisted to
config.json, so they survive restarts.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import discord
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ---- Config ---------------------------------------------------------------
VERIFY_CHANNEL_NAME = "verify"
VERIFIED_ROLE_NAME = "Verified"
ATTENDEES_DIR = "attendees"
RENAME_ON_VERIFY = True
DELETE_USER_MESSAGE = True
# ALLOWED_STATUSES = {"confirmed", "accepted", "registered", "paid"}
ALLOWED_STATUSES = None  # set to a set of lowercase status strings to filter

# Graduation: when graduating an edition, members of the edition role have
# GRADUATE_FROM removed and GRADUATE_TO added. Edition role itself is kept
# (so you keep a record of which school they attended).
GRADUATE_FROM = "student"
GRADUATE_TO = "veteran"

# Where to post the congrats message after a successful graduation.
# Set ANNOUNCE_CHANNEL_NAME = None to disable the announcement.
ANNOUNCE_CHANNEL_NAME = "general"
ANNOUNCE_CATEGORY_NAME = "chat-stuff"  # set to None to match by name only

# Who can run admin commands. Members must have AT LEAST ONE of these
# roles (case-insensitive). Server owner and members with the Discord
# "Administrator" permission bypass the check. These initial values are
# defaults — runtime changes are persisted to CONFIG_FILE.
ADMIN_ROLES = {"counsellor", "admin", "lecturer-7-rome", "lecturer-8-ny"}

# Runtime config file. ADMIN_ROLES and GRADUATION_SCHEDULE live here so
# they can be edited via !admins and !schedule commands without restarting
# the bot. The file is created on first run if it doesn't exist.
CONFIG_FILE = "config.json"

# Auto-graduation schedule. Each entry runs ONCE at its date and is then
# remembered in graduation_state.json so it never re-runs after a restart.
# Use ISO 8601 with timezone. Leave list empty to disable.
GRADUATION_SCHEDULE = [
    # {"date": "2026-07-15T20:00:00+02:00", "edition_role": "astrostat_school_7"},
]
GRADUATION_STATE_FILE = "graduation_state.json"
# ---------------------------------------------------------------------------


def load_runtime_config() -> None:
    """Read config.json if it exists; otherwise create it from defaults."""
    global ADMIN_ROLES, GRADUATION_SCHEDULE
    p = Path(CONFIG_FILE)
    if not p.exists():
        save_runtime_config()
        return
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        print(f"⚠️ Could not read {CONFIG_FILE}: {e}. Using defaults.")
        return
    if isinstance(data.get("admin_roles"), list):
        ADMIN_ROLES = set(data["admin_roles"])
    if isinstance(data.get("graduation_schedule"), list):
        GRADUATION_SCHEDULE = list(data["graduation_schedule"])
    print(f"Loaded config: {len(ADMIN_ROLES)} admin role(s), "
          f"{len(GRADUATION_SCHEDULE)} scheduled graduation(s).")


def save_runtime_config() -> None:
    Path(CONFIG_FILE).write_text(json.dumps({
        "admin_roles": sorted(ADMIN_ROLES),
        "graduation_schedule": GRADUATION_SCHEDULE,
    }, indent=2))


def _norm(s) -> str:
    return str(s or "").strip().lower()


def _merge_frame(table: dict, df: pd.DataFrame) -> int:
    df = df.fillna("")
    df.columns = [_norm(c) for c in df.columns]
    if "name" not in df.columns and "email" not in df.columns:
        return 0
    added = 0
    for _, row in df.iterrows():
        record = {k: str(v).strip() for k, v in row.items()}
        raw_roles = record.get("discord roles", "")
        record["_roles"] = [r.strip() for r in raw_roles.split(",") if r.strip()]
        if record.get("name"):
            table[_norm(record["name"])] = record
        if record.get("email"):
            table[_norm(record["email"])] = record
        added += 1
    return added


def load_attendees_from_dir(directory: str) -> dict:
    table: dict = {}
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise SystemExit(f"Attendees folder not found: {dir_path.resolve()}")
    total = 0
    files_loaded = []
    for f in sorted(dir_path.iterdir()):
        ext = f.suffix.lower()
        if ext == ".csv":
            df = pd.read_csv(f, dtype=str)
            n = _merge_frame(table, df)
        elif ext == ".tsv":
            df = pd.read_csv(f, sep="\t", dtype=str)
            n = _merge_frame(table, df)
        elif ext in (".xlsx", ".xls"):
            n = 0
            for _, df in pd.read_excel(f, sheet_name=None, dtype=str).items():
                n += _merge_frame(table, df)
        else:
            continue
        print(f"  {f.name}: {n} rows")
        files_loaded.append((f.name, n))
        total += n
    print(f"Loaded {total} attendees ({len(table)} lookup keys).")
    table["_meta"] = {"total": total, "keys": len(table), "files": files_loaded}
    return table


# ---- Graduation -----------------------------------------------------------
def _load_grad_state() -> dict:
    p = Path(GRADUATION_STATE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {"done": []}
    return {"done": []}


def _save_grad_state(state: dict) -> None:
    Path(GRADUATION_STATE_FILE).write_text(json.dumps(state, indent=2))


def find_channel_by_name(guild: discord.Guild, name: str,
                         category_name: str | None = None):
    if not name:
        return None
    name_l = name.lower()
    cat_l = category_name.lower() if category_name else None
    for ch in guild.text_channels:
        if ch.name.lower() != name_l:
            continue
        if cat_l is None:
            return ch
        if ch.category and ch.category.name.lower() == cat_l:
            return ch
    return None


async def do_graduation(guild: discord.Guild, edition_role_name: str
                        ) -> tuple[int, list[str]]:
    """For every member with `edition_role_name`, remove GRADUATE_FROM and
    add GRADUATE_TO. The edition role itself is preserved."""
    edition_role = discord.utils.get(guild.roles, name=edition_role_name)
    from_role = discord.utils.get(guild.roles, name=GRADUATE_FROM)
    to_role = discord.utils.get(guild.roles, name=GRADUATE_TO)

    errors = []
    if not edition_role:
        errors.append(f"edition role '{edition_role_name}' not found")
    if not from_role:
        errors.append(f"role '{GRADUATE_FROM}' not found")
    if not to_role:
        errors.append(f"role '{GRADUATE_TO}' not found")
    if errors:
        return 0, errors

    moved = 0
    for member in list(edition_role.members):
        try:
            if to_role not in member.roles:
                await member.add_roles(to_role, reason=f"Graduated from {edition_role_name}")
            if from_role in member.roles:
                await member.remove_roles(from_role, reason=f"Graduated from {edition_role_name}")
            moved += 1
        except discord.Forbidden:
            errors.append(f"forbidden: {member.display_name}")
        except Exception as e:
            errors.append(f"{member.display_name}: {e}")
    return moved, errors


async def graduation_scheduler():
    await client.wait_until_ready()
    state = _load_grad_state()
    done = set(state.get("done", []))
    while not client.is_closed():
        now = datetime.now(timezone.utc)
        for entry in GRADUATION_SCHEDULE:
            key = f"{entry['date']}|{entry['edition_role']}"
            if key in done:
                continue
            sched = datetime.fromisoformat(entry["date"])
            if sched.tzinfo is None:
                sched = sched.replace(tzinfo=timezone.utc)
            if now < sched:
                continue
            for guild in client.guilds:
                moved, errs = await do_graduation(guild, entry["edition_role"])
                print(f"🎓 Scheduled graduation in {guild.name}: "
                      f"{moved} member(s) of {entry['edition_role']} graduated "
                      f"({GRADUATE_FROM} → {GRADUATE_TO}). Errors: {errs}")
                if moved > 0 and ANNOUNCE_CHANNEL_NAME:
                    ch = find_channel_by_name(
                        guild, ANNOUNCE_CHANNEL_NAME, ANNOUNCE_CATEGORY_NAME)
                    if ch:
                        try:
                            await ch.send(
                                f"🎓✨ Congratulations to the "
                                f"**{entry['edition_role']}** class "
                                f"for graduating and getting promoted to "
                                f"**{GRADUATE_TO}s**! 🎉🥂"
                            )
                        except discord.Forbidden:
                            pass
            done.add(key)
            state["done"] = list(done)
            _save_grad_state(state)
        await asyncio.sleep(60)


# ---- Discord --------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

load_runtime_config()
ATTENDEES = load_attendees_from_dir(ATTENDEES_DIR)


async def reply_and_clean(message, text, delete_user_msg=True):
    await message.channel.send(text, delete_after=15)
    if delete_user_msg and DELETE_USER_MESSAGE:
        try:
            await message.delete()
        except discord.Forbidden:
            pass


def is_admin(member: discord.Member) -> bool:
    # Server owner and Discord-level admins always pass
    if member.guild.owner_id == member.id:
        return True
    if member.guild_permissions.administrator:
        return True
    # Otherwise must have at least one of the configured admin roles
    admin_roles_lower = {r.lower() for r in ADMIN_ROLES}
    return any(r.name.lower() in admin_roles_lower for r in member.roles)


async def handle_admin_command(message: discord.Message) -> bool:
    global ATTENDEES
    content = message.content.strip()
    cmd = content.lower()

    if cmd == "!reload":
        if not is_admin(message.author):
            await message.channel.send(
                f"🚫 Only members with one of these roles can run that: "
                f"{', '.join(sorted(ADMIN_ROLES))}",
                delete_after=10)
            return True
        try:
            ATTENDEES = load_attendees_from_dir(ATTENDEES_DIR)
        except Exception as e:
            await message.channel.send(f"❌ Reload failed: `{e}`")
            return True
        meta = ATTENDEES.get("_meta", {})
        files_summary = "\n".join(
            f"  • `{name}` — {n} rows" for name, n in meta.get("files", [])
        ) or "  (no files found)"
        await message.channel.send(
            f"♻️ Reloaded.\n**{meta.get('total', 0)} attendees** "
            f"({meta.get('keys', 0)} lookup keys) from:\n{files_summary}")
        return True

    if cmd == "!stats":
        if not is_admin(message.author):
            return True
        meta = ATTENDEES.get("_meta", {})
        await message.channel.send(
            f"📊 {meta.get('total', 0)} attendees loaded "
            f"({meta.get('keys', 0)} lookup keys) "
            f"across {len(meta.get('files', []))} file(s).")
        return True

    if cmd.startswith("!graduate"):
        if not is_admin(message.author):
            await message.channel.send(
                f"🚫 Only members with one of these roles can run that: "
                f"{', '.join(sorted(ADMIN_ROLES))}",
                delete_after=10)
            return True
        parts = content.split(maxsplit=1)
        if len(parts) != 2:
            await message.channel.send(
                f"Usage: `!graduate <EditionRole>`\n"
                f"Takes everyone with that role and swaps "
                f"`{GRADUATE_FROM}` → `{GRADUATE_TO}` "
                f"(the edition role itself is kept).\n"
                f"Example: `!graduate participant-8-ny`",
                delete_after=25)
            return True
        edition_role = parts[1].strip()
        moved, errs = await do_graduation(message.guild, edition_role)
        msg = (f"🎓 {moved} member(s) of **{edition_role}** graduated "
               f"(`{GRADUATE_FROM}` → `{GRADUATE_TO}`).")
        if errs:
            msg += f"\nIssues: {errs}"

        # Auto-clean if run in #verify so the channel stays tidy
        in_verify = (message.channel.name == VERIFY_CHANNEL_NAME)
        send_kwargs = {"delete_after": 5} if in_verify else {}
        await message.channel.send(msg, **send_kwargs)
        if in_verify:
            try:
                await message.delete()
            except discord.Forbidden:
                pass

        # Public congrats in the announce channel
        if moved > 0 and ANNOUNCE_CHANNEL_NAME:
            announce_ch = find_channel_by_name(
                message.guild, ANNOUNCE_CHANNEL_NAME, ANNOUNCE_CATEGORY_NAME)
            if announce_ch:
                try:
                    await announce_ch.send(
                        f"🎓✨ Congratulations to the **{edition_role}** class "
                        f"for graduating and getting promoted to "
                        f"**{GRADUATE_TO}s**! 🎉🥂"
                    )
                except discord.Forbidden:
                    print(f"⚠️ Can't post in #{announce_ch.name}: missing permission")
            else:
                print(f"⚠️ Announce channel not found: "
                      f"#{ANNOUNCE_CHANNEL_NAME} "
                      f"(category={ANNOUNCE_CATEGORY_NAME})")
        return True

    # ----- Admin roles management -----
    if cmd.startswith("!admins"):
        if not is_admin(message.author):
            await message.channel.send(
                f"🚫 Only members with one of these roles can run that: "
                f"{', '.join(sorted(ADMIN_ROLES))}",
                delete_after=10)
            return True
        parts = content.split(maxsplit=2)
        if len(parts) == 1:
            roles_str = ", ".join(f"`{r}`" for r in sorted(ADMIN_ROLES)) or "(none)"
            await message.channel.send(f"👥 Admin roles: {roles_str}")
            return True
        action = parts[1].lower()
        if action == "add" and len(parts) == 3:
            role = parts[2].strip().lower()
            if role in ADMIN_ROLES:
                await message.channel.send(f"ℹ️ `{role}` is already an admin role.")
            else:
                ADMIN_ROLES.add(role)
                save_runtime_config()
                await message.channel.send(f"✅ Added `{role}` to admin roles.")
            return True
        if action == "remove" and len(parts) == 3:
            role = parts[2].strip().lower()
            if role in ADMIN_ROLES:
                ADMIN_ROLES.discard(role)
                save_runtime_config()
                await message.channel.send(f"✅ Removed `{role}` from admin roles.")
            else:
                await message.channel.send(f"❌ `{role}` is not in admin roles.")
            return True
        await message.channel.send(
            "Usage:\n"
            "`!admins` — list admin roles\n"
            "`!admins add <role>` — add a role\n"
            "`!admins remove <role>` — remove a role",
            delete_after=20)
        return True

    # ----- Graduation schedule management -----
    if cmd.startswith("!schedule"):
        if not is_admin(message.author):
            await message.channel.send(
                f"🚫 Only members with one of these roles can run that: "
                f"{', '.join(sorted(ADMIN_ROLES))}",
                delete_after=10)
            return True
        parts = content.split(maxsplit=3)
        if len(parts) == 1:
            if not GRADUATION_SCHEDULE:
                await message.channel.send("📅 No scheduled graduations.")
                return True
            lines = ["📅 Scheduled graduations:"]
            for i, e in enumerate(GRADUATION_SCHEDULE, 1):
                lines.append(f"  **{i}.** `{e['edition_role']}` at `{e['date']}`")
            await message.channel.send("\n".join(lines))
            return True
        action = parts[1].lower()
        if action == "add" and len(parts) == 4:
            date_str, edition_role = parts[2].strip(), parts[3].strip()
            try:
                dt = datetime.fromisoformat(date_str)
                if dt.tzinfo is None:
                    await message.channel.send(
                        "⚠️ Date has no timezone. Add one, e.g. `+02:00` for CEST.",
                        delete_after=20)
                    return True
            except ValueError:
                await message.channel.send(
                    "❌ Bad date format. Use ISO 8601, e.g. "
                    "`2026-07-15T20:00:00+02:00`",
                    delete_after=20)
                return True
            GRADUATION_SCHEDULE.append({"date": date_str, "edition_role": edition_role})
            save_runtime_config()
            # Make sure the scheduler is running (it may not have started)
            if not getattr(graduation_scheduler, "_running", False):
                client.loop.create_task(graduation_scheduler())
                graduation_scheduler._running = True
            await message.channel.send(
                f"✅ Scheduled: **{edition_role}** will graduate on `{date_str}`.")
            return True
        if action == "remove" and len(parts) == 3:
            try:
                idx = int(parts[2]) - 1
            except ValueError:
                await message.channel.send(
                    "❌ Use a number from `!schedule`, e.g. `!schedule remove 2`",
                    delete_after=15)
                return True
            if 0 <= idx < len(GRADUATION_SCHEDULE):
                removed = GRADUATION_SCHEDULE.pop(idx)
                save_runtime_config()
                await message.channel.send(
                    f"✅ Removed: `{removed['edition_role']}` at `{removed['date']}`")
            else:
                await message.channel.send(
                    f"❌ No entry #{idx + 1}. Run `!schedule` to see valid numbers.",
                    delete_after=15)
            return True
        await message.channel.send(
            "Usage:\n"
            "`!schedule` — list scheduled graduations\n"
            "`!schedule add <ISO_date> <edition_role>` — add one\n"
            "    e.g. `!schedule add 2026-07-15T20:00:00+02:00 participant-8-ny`\n"
            "`!schedule remove <number>` — remove one (number from the list)",
            delete_after=30)
        return True

    return False


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    # Always start the scheduler so runtime-added entries (via !schedule add)
    # get picked up. It just sleeps when the schedule is empty.
    if not getattr(graduation_scheduler, "_running", False):
        client.loop.create_task(graduation_scheduler())
        graduation_scheduler._running = True
        print(f"Graduation scheduler armed: {len(GRADUATION_SCHEDULE)} entry(s)")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    if message.content.startswith("!"):
        if await handle_admin_command(message):
            return

    if message.channel.name != VERIFY_CHANNEL_NAME:
        return

    query = _norm(message.content)
    record = ATTENDEES.get(query) if query != "_meta" else None

    if record and ALLOWED_STATUSES is not None:
        if _norm(record.get("status")) not in ALLOWED_STATUSES:
            record = None

    if record is None:
        await reply_and_clean(
            message,
            f"{message.author.mention} ❌ I couldn't find that name or email. "
            "Use exactly what you wrote on your application, or ping an organiser.")
        return

    guild = message.guild
    member = message.author
    role_objects = []
    verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
    if verified_role:
        role_objects.append(verified_role)
    missing = []
    for name in record.get("_roles", []):
        r = discord.utils.get(guild.roles, name=name)
        if r:
            role_objects.append(r)
        else:
            missing.append(name)
    if missing:
        print(f"⚠️ roles not found on server: {missing} "
              f"(for {record.get('name') or record.get('email')})")

    try:
        await member.add_roles(*role_objects, reason="Verified via #verify")
    except discord.Forbidden:
        await reply_and_clean(
            message,
            "⚠️ I don't have permission to assign roles.",
            delete_user_msg=False)
        return

    if RENAME_ON_VERIFY and record.get("name"):
        try:
            await member.edit(nick=record["name"])
        except discord.Forbidden:
            pass

    extras = []
    if record.get("institute"):
        extras.append(f"from **{record['institute']}**")
    if record.get("country"):
        extras.append(f"({record['country']})")
    extra_str = " " + " ".join(extras) if extras else ""

    await reply_and_clean(
        message,
        f"✅ Welcome **{record['name']}**{extra_str}! You now have access to the rest of the server.")


if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN in your environment or .env file.")
    client.run(token)
