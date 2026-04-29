# WhatsApp Desktop App - Description

## What this app does

This desktop app helps teams send WhatsApp messages using multiple profiles from one interface.
It supports:

- Sending to contacts or groups
- Reusable message templates with placeholders (like `{name}`, `{company}`)
- Optional file attachments
- Immediate sends and scheduled sends
- Multi-profile operation with separate Chrome sessions per WhatsApp number

The app can run as Python script or as a standalone Windows `.exe`.

---

## Run modes

The app starts in one of three modes:

- `local` (default): Uses local MS Access storage for profiles, contacts, templates, groups, schedules, and logs
- `sql`: Uses SQL-backed client list and scheduled message queue
- `hybrid`: Shows both Local and SQL experiences

---

## Main screens and menus

## Top-level UI

- **Window title:** WhatsApp Desktop - Multi-Profile Sender
- **How To Use button:** Opens in-app usage guide
- **Status strip:** Shows real-time current state (Ready, Opened, Queued, Paused, etc.)
- **Tabs:** Depends on mode
  - SQL Mode tab (in `sql`/`hybrid`)
  - Local Mode tab (in `local`/`hybrid`)
- **Runtime Logs panel:** Visible for SQL operations

## Local Mode workflow menu (left sidebar)

Local mode has a step-by-step sidebar:

1. **Profiles**
2. **Contacts & Lists**
3. **Templates**
4. **Send Messages**
5. **Scheduling**
6. **Logs**

---

## Local Mode pages

### 1) Profiles

- Select existing profile
- Open profile (opens WhatsApp Web with that Chrome profile)
- Create new profile (name + WhatsApp number)
- Delete profile

### 2) Contacts & Lists

- Select contact list
- Add/Rename/Delete list
- Add contact manually
- Import contacts from CSV
- Pick contacts using checkbox-style first column
- Check all / Uncheck all / Delete selected

### 3) Templates

- Select template
- Create or edit template content
- Save template
- Rename template
- Delete template

### 4) Send Messages

- Select message template
- Attach files / Clear attachments
- Choose target type:
  - Send to Contacts
  - Send to Group
- Add group
- Open profile
- Send actions:
  - **Send Selected**
  - **Send All**

### 5) Scheduling

- Choose date-time (`YYYY-MM-DD HH:MM`)
- Choose target (Contacts or Group)
- Schedule actions:
  - **Schedule Selected**
  - **Schedule All**
- View scheduled jobs
- Delete selected scheduled job

### 6) Logs

- Refresh local logs
- Delete logs
- View send outcomes (SENT/ERROR)

---

## SQL Mode actions

SQL Mode includes:

- Client list table (Client, Phone, Status)
- Option: **Allow side search for phone numbers**
- Buttons:
  - Open Profile
  - Start
  - Pause
  - Resume
  - Stop
  - Pause All
  - Resume All
  - Refresh SQL List

The SQL scheduler checks for pending messages periodically and processes them per active profile.

---

## Send options: Now or Later

The app supports both sending patterns:

- **Send now**
  - Go to **Send Messages**
  - Use **Send Selected** or **Send All**
  - Messages are queued and sent using the opened profile

- **Schedule for later**
  - Go to **Scheduling**
  - Set future date-time
  - Use **Schedule Selected** or **Schedule All**
  - App dispatches scheduled jobs automatically when they become due

This gives users a clear choice between immediate delivery and planned delivery.

---

## Typical user flow

1. Create/select profile
2. Open profile and ensure WhatsApp Web is logged in
3. Add/import contacts
4. Create/select template
5. Choose:
   - Send now (Send Messages), or
   - Schedule for later (Scheduling)
6. Monitor status and logs

