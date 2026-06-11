# Bot_sterior

A Discord verification + role-management bot for the Astrostatistics School.

When a new member joins the server, they can only see the `#verify` channel.
They post their **full name** or **registered email**, and the bot:

1. Looks them up in the attendee list (CSV / TSV / XLSX files)
2. Assigns them `Verified` plus any roles listed in the `Discord Roles` column
3. Renames them to their real name on the server
4. Deletes the message for privacy and welcomes them publicly

It also handles the natural school lifecycle — when a school edition ends,
a single `!graduate` command promotes that class from `student` → `veteran`
and posts a congratulations message in `#general`.

## Features

- Multi-file attendee loading (one CSV per school edition, lecturers, TAs, …)
- Multiple roles per person (comma-separated in the `Discord Roles` column)
- Status filter — only people with the right registration status can verify
- `!reload` to pick up updated attendee files without restarting
- `!graduate <edition_role>` to promote a whole class from student → veteran
- Optional scheduled auto-graduation by date (editable at runtime)
- Public congratulations message in `#general` when a class graduates
- Admin commands restricted to specific Discord roles (counsellor, admin, …)
- Admin roles and the graduation schedule are **editable from Discord** —
  no restart needed, changes persist across restarts via `config.json`

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

<!-- ### 2. Configure the Discord application

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Create a new application → **Bot** tab → copy the **token**
2. Enable both privileged intents:
   - **Message Content Intent**
   - **Server Members Intent**
3. Under **OAuth2 → URL Generator** select scope `bot` and these permissions:
   - View Channels
   - Send Messages
   - Manage Messages
   - Manage Roles
   - Manage Nicknames
4. Open the generated URL to add the bot to your server

### 3. Server preparation

- Create roles: `Verified`, `student`, `veteran`, and any cohort/edition roles
- In **Server Settings → Roles**, drag the bot's role **above** all roles it
  will manage (otherwise it can't assign them)
- Set `#verify` so that `@everyone` can view and post there, and `Verified`
  cannot. All other channels: `@everyone` denied, `Verified` allowed -->

### 2. Project files

Create a `.env` file next to `verify_bot.py`:

```
DISCORD_TOKEN=your_token_here
```

Create an `attendees/` folder and drop your registration exports inside.
The bot reads `.csv`, `.tsv`, `.xlsx`, and `.xls` files — every file in the
directory is merged into one big lookup table at startup.

Required columns (case-insensitive, any order):

| Column          | Purpose                                                       |
| --------------- | ------------------------------------------------------------- |
| `name`          | Full name as written on the application form                  |
| `email`         | Registration email (alternative match key)                    |
| `status`        | Filtered against `ALLOWED_STATUSES` in the config             |
| `institute`     | Used in the welcome message (optional)                        |
| `country`       | Used in the welcome message (optional)                        |
| `Discord Roles` | Comma-separated list of Discord role names to assign          |

Example row:

```csv
name,email,status,institute,country,Discord Roles
Alice Martin,alice@inaf.it,confirmed,INAF,IT,"Students, participant-8-ny"
```

### 3. Run

```bash
python verify_bot.py
```

You should see `Loaded N attendees (...)` followed by `Logged in as Bot_sterior`.

## Commands

All admin commands require the caller to have one of the roles in
`ADMIN_ROLES` (defaults: `counsellor`, `admin`, `organiser`, `organizer`),
or to be the server owner / a Discord-level admin.

### Attendee management

| Command   | What it does                                              |
| --------- | --------------------------------------------------------- |
| `!reload` | Re-reads all files in `attendees/`. Use after editing a CSV. |
| `!stats`  | Shows how many attendees are loaded and from how many files. |

### Graduation

| Command                          | What it does                                                                |
| -------------------------------- | --------------------------------------------------------------------------- |
| `!graduate <edition_role>`       | For everyone with `<edition_role>`: removes `student`, adds `veteran`, keeps the edition role. Posts congrats in `#general`. |

If `!graduate` is run inside `#verify`, the command and the reply
auto-delete after 5 seconds to keep the channel tidy.

### Admin roles (runtime-editable)

| Command                       | What it does                                |
| ----------------------------- | ------------------------------------------- |
| `!admins`                     | List the roles allowed to run admin commands |
| `!admins add <role>`          | Grant a role admin privileges               |
| `!admins remove <role>`       | Revoke admin from a role                    |

Role names are case-insensitive. The server owner and Discord-level admins
always pass the check, so you can't lock yourself out completely.

### Scheduled (automatic) graduation (runtime-editable)

| Command                                       | What it does                                |
| --------------------------------------------- | ------------------------------------------- |
| `!schedule`                                   | List pending scheduled graduations          |
| `!schedule add <iso_date> <edition_role>`     | Schedule an auto-graduation                 |
| `!schedule remove <number>`                   | Cancel one (number comes from `!schedule`)  |

Date must be ISO 8601 with timezone, e.g.
`2026-07-19T18:00:00+02:00`. Once the date passes, the bot performs the
graduation and posts the same congrats message as `!graduate` does.
Triggers exactly once — restarts won't repeat it.

Examples:

```
!schedule add 2026-07-19T18:00:00+02:00 participant-8-ny
!schedule
!schedule remove 1
```

## Configuration

Two layers:

### Python-level defaults (top of `verify_bot.py`)

These take effect on first run, or any run where `config.json` is missing.

```python
VERIFY_CHANNEL_NAME = "verify"
VERIFIED_ROLE_NAME = "Verified"
ATTENDEES_DIR = "attendees"
RENAME_ON_VERIFY = True
DELETE_USER_MESSAGE = True

ALLOWED_STATUSES = {"confirmed", "accepted", "registered", "paid"}

GRADUATE_FROM = "student"
GRADUATE_TO = "veteran"

ANNOUNCE_CHANNEL_NAME = "general"
ANNOUNCE_CATEGORY_NAME = "chat-stuff"

ADMIN_ROLES = {"counsellor", "admin", "organiser", "organizer"}

GRADUATION_SCHEDULE = []
```

Change any of these by editing the file and restarting.

### Runtime config (`config.json`)

`ADMIN_ROLES` and `GRADUATION_SCHEDULE` are also persisted to a
`config.json` file next to `verify_bot.py`. The bot reads this on
startup and writes to it every time you use `!admins` or `!schedule`
commands. Example:

```json
{
  "admin_roles": ["admin", "counsellor", "organiser", "tutor"],
  "graduation_schedule": [
    {"date": "2026-07-19T18:00:00+02:00", "edition_role": "participant-8-ny"}
  ]
}
```

You can edit this file directly with a text editor instead of using the
commands, but you'll need to restart for hand-edits to take effect (or
just use the commands and skip the restart).

If you ever want to wipe runtime config back to the Python defaults,
delete `config.json` and restart.

## File layout

```
Verification_bot/
├── verify_bot.py            # the bot itself
├── requirements.txt         # Python deps
├── .env                     # DISCORD_TOKEN (NEVER commit this)
├── attendees/               # one CSV/XLSX per school edition
│   ├── astro_school_8_ny.csv
│   ├── lecturers.csv
│   └── tutors.csv
├── config.json              # runtime config (admin roles + schedule)
├── graduation_state.json    # which scheduled graduations have run
├── botsterior.service       # systemd unit (for server deployment)
├── DEPLOYMENT.md            # step-by-step server setup
└── README.md
```

The last three files are created automatically; you don't have to make
them yourself.

## Updating the attendee list during a school

1. Drop the new/updated CSV into `attendees/`
2. In Discord, type `!reload`
3. Bot replies with a per-file summary so you can confirm the new file
   was picked up

No restart needed.


## Security notes

- The `.env` file contains the bot token — **never commit it** to a public
  repo. It's already covered by `.gitignore`.
- The `attendees/` folder contains personal data (names, emails,
  affiliations) — same care applies.
- If the token leaks, rotate it: Developer Portal → Bot → **Reset Token**,
  then update `.env` and restart.
