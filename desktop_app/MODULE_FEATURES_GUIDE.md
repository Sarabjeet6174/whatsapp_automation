# Desktop App Module And Feature Guide

This document explains what each major module does in `desktop_app`, and what each visible UI feature/button does when clicked.

---

## 1) App Entry And Shell Modules

### `main_desktop.py`
- Main app launcher.
- Chooses runtime mode:
  - `local` (default, Qt by default)
  - `sql` / `hybrid` (uses legacy Tk flow)
- Initializes either Qt (`run_qt_app`) or Tk (`MainWindow`).

### `app/ui/qt/modern_main_window.py`
- Main Qt shell (sidebar + page stack).
- Creates shared `LocalWorkflowController`.
- Initializes local Access DB (`init_local_db()`).
- Wires page navigation and inter-page actions.
- Starts the local schedule worker (`ensure_schedule_worker()`).

### `app/ui/main_window.py` (legacy Tk UI)
- Older full UI used for SQL/hybrid mode and optional local mode.
- Still contains full send/schedule/logging flows for legacy behavior.

### `config.py`
- Resolves runtime paths for:
  - `.env`
  - Chrome profile directory
  - Access DB file (`local_store.accdb`)
- Handles script vs packaged EXE path behavior.

---

## 2) Qt Pages: Buttons And Behavior

## Dashboard (`app/ui/qt/pages/dashboard_page.py`)
- Purpose: quick navigation page.
- Buttons (Open cards):
  - Open Send
  - Open Profiles
  - Open WhatsApp contacts
  - Open WhatsApp groups
  - Open Schedule
  - Open Logs
- Click action:
  - Emits `open_stack_index` and switches page.
- Data side effects:
  - None (navigation only).

## Profiles (`app/ui/qt/pages/profiles_page.py`)
- Purpose: manage profile identities (name + phone).
- Buttons:
  - `Save profile`
  - `Open WhatsApp`
  - `Delete profile`
  - `Refresh list`
- Click actions:
  - Save profile:
    - Calls `create_local_profile(name, phone)`.
    - Inserts into `local_profiles`.
  - Open WhatsApp:
    - Calls `workflow.ensure_local_profile_ready(...)` in background thread.
    - Opens/attaches browser profile and WhatsApp Web.
  - Delete profile:
    - Calls `delete_local_profile(profile_id)` after confirmation.
    - Deletes profile-owned data in related local tables.
  - Refresh:
    - Reloads from `fetch_local_profiles()`.

## Contacts & Lists (`app/ui/qt/pages/contacts_page.py`)
- Purpose: normal contact list management (same target as CSV import).
- Top list controls:
  - `Add list`
  - `Rename list`
  - `Delete list`
  - `List columns…`
  - `Import Contacts CSV`
- Contact row controls:
  - `Add contact row`
  - `Delete selected`
  - `New list from selected…`
  - `Send selected…`
- Click actions:
  - Add list -> `create_contact_list(...)`.
  - Rename list -> `rename_contact_list(...)`.
  - Delete list -> `delete_contact_list(...)` and list contacts.
  - List columns -> `update_contact_list_fields(...)`.
  - Import CSV -> parses rows, creates contacts via `create_contact(...)`.
  - Add contact row -> inserts placeholder contact.
  - Delete selected -> `delete_contacts([...])`.
  - New list from selected -> creates a new list and copies selected contacts.
  - Send selected -> opens Send page preselected with chosen contact IDs.
- Table edit behavior:
  - Direct edit triggers `update_contact(...)` per changed row.

## WhatsApp Contacts (`app/ui/qt/pages/wa_contacts_page.py`)
- Purpose: sync and store WhatsApp directory entries from WhatsApp UI.
- Buttons:
  - `Open WhatsApp`
  - `Load from WhatsApp`
  - `Export CSV`
  - `Clear saved list`
  - `Send selected…`
- Click actions:
  - Open WhatsApp -> `ensure_local_profile_ready(...)`.
  - Load from WhatsApp:
    - Runs `sync_whatsapp_contacts_from_new_chat(driver)`.
    - Saves via `replace_whatsapp_directory(profile_id, names)`.
  - Export CSV:
    - Exports rows for later import into normal Contacts lists.
  - Clear saved list:
    - Clears `whatsapp_directory` for profile.
  - Send selected:
    - Opens Send page preselected with chosen WA names.

## WhatsApp Groups (`app/ui/qt/pages/groups_page.py`)
- Purpose: sync group names and pull group members into normal contact lists.
- Buttons:
  - `Open WhatsApp`
  - `Load groups`
  - `Clear saved list`
  - `Add members to contact list`
  - `Send selected…`
- Click actions:
  - Open WhatsApp -> `ensure_local_profile_ready(...)`.
  - Load groups:
    - Runs `sync_whatsapp_groups_from_new_chat(driver)`.
    - Replaces local saved groups via `replace_groups(profile_id, groups)`.
  - Clear saved list:
    - Clears profile groups (`replace_groups(profile_id, [])`).
  - Add members to contact list:
    - For each selected group:
      - Opens group info and members
      - Opens each member contact info
      - Reads name + phone
      - Saves to normal Contacts & Lists using list name = group name
      - Uses `merge_group_members_into_contact_list(profile_id, group_name, members)`
  - Send selected:
    - Opens Send page preselected with selected group names.

## Templates (`app/ui/qt/pages/templates_page.py`)
- Purpose: create and manage reusable message templates.
- Buttons:
  - `Save template`
  - `Update selected`
  - `Delete selected`
  - variable insert chips, emoji insert controls
- Click actions:
  - Save -> `upsert_template(...)`.
  - Update selected -> optional rename + content update.
  - Delete selected -> `delete_template(...)`.

## Send Messages (`app/ui/qt/pages/send_messages_page.py`)
- Purpose: select recipients and send now / schedule.
- Main controls:
  - Profile selector, template selector
  - `Open WhatsApp`
  - Recipient-source toggles:
    - Contact lists
    - WhatsApp directory
    - Groups
  - Contact/group tables with row checkboxes
  - Select-all-visible checkboxes
  - Composer:
    - message body
    - emoji
    - attachments
    - clear files
    - attachment-only mode
  - Action buttons:
    - `Send Now`
    - `Schedule…`
- Click actions:
  - Send Now:
    - Validates selection/message/attachments.
    - Builds job payload (`items` with receiver/name/rendered).
    - Calls `workflow.enqueue_send_job(job)`.
  - Schedule:
    - Opens datetime picker.
    - Stores payload via `create_local_scheduled_job(profile_id, run_at, job)`.

## Schedule (`app/ui/qt/pages/schedule_page.py`)
- Purpose: view/manage scheduled jobs.
- Buttons:
  - `Refresh`
  - `Edit selected job`
  - `Delete selected job`
- Table columns:
  - Run at, Status, Target, Recipients, Message, Items, Last error
- Click actions:
  - Refresh:
    - Loads jobs from `fetch_local_scheduled_jobs(profile_id)`.
  - Edit selected job:
    - For `PENDING` jobs only.
    - Lets user edit:
      - Run time
      - Message content
    - Saves with `update_local_scheduled_job(...)`.
  - Delete selected job:
    - Removes via `delete_local_scheduled_job(...)`.
  - Double-click row:
    - Opens same edit dialog.

## Logs (`app/ui/qt/pages/logs_page.py`)
- Purpose: inspect send history and errors.
- Buttons:
  - `Refresh`
  - `Clear logs`
- Click actions:
  - Refresh -> `fetch_local_logs(profile_id)`.
  - Clear logs -> `delete_local_logs(profile_id)`.

---

## 3) Core Service/Automation Modules

### `app/services/local_workflow_controller.py`
- Central orchestration for local mode.
- Responsibilities:
  - per-profile send queues
  - send worker thread lifecycle
  - schedule worker polling
  - profile readiness via browser/session open
  - call `send_message(...)` for each recipient item
  - write send logs via DB functions

### `app/whatsapp/sender.py`
- Selenium automation layer.
- Responsibilities:
  - open/search contact/group chats
  - send text and attachments
  - sync WhatsApp contacts and groups
  - group-member traversal and contact extraction
  - reads contact numbers from WhatsApp contact-info UI selectors

### `app/core/scheduler.py` and `app/core/message_loop.py` (legacy path)
- Used primarily by Tk SQL/hybrid flows.
- Manage SQL pending-message dispatch loops and profile/browser runtime.

### `app/services/constants.py`
- Shared constants used in UI and send logic (e.g., source offsets/labels).

---

## 4) Database Access Modules And Data Impact

All local DB logic is in:
- `app/db/local_access.py`

### Main entities/tables
- `local_profiles`
- `local_contact_lists`
- `local_contacts`
- `local_templates`
- `local_groups`
- `whatsapp_directory`
- `local_send_logs`
- `local_scheduled_jobs`

### Key feature functions

#### Profiles
- `fetch_local_profiles()`
- `create_local_profile(...)`
- `delete_local_profile(...)` (cascades related data)

#### Contacts & Lists
- `fetch_contact_lists(...)`
- `create_contact_list(...)`
- `rename_contact_list(...)`
- `update_contact_list_fields(...)`
- `delete_contact_list(...)`
- `fetch_contacts(...)`
- `create_contact(...)`
- `update_contact(...)`
- `delete_contacts(...)`
- `merge_group_members_into_contact_list(...)` (group->normal list upsert)

#### WhatsApp directory
- `replace_whatsapp_directory(...)`
- `merge_whatsapp_directory_entries(...)`
- `fetch_whatsapp_directory(...)`

#### Templates
- `fetch_templates(...)`
- `upsert_template(...)`
- `rename_template(...)`
- `delete_template(...)`

#### Groups
- `fetch_groups(...)`
- `create_group(...)`
- `replace_groups(...)`

#### Logs
- `log_local_send(...)`
- `fetch_local_logs(...)`
- `delete_local_logs(...)`

#### Scheduling
- `create_local_scheduled_job(...)`
- `fetch_local_scheduled_jobs(...)`
- `fetch_due_local_scheduled_jobs(...)`
- `mark_local_scheduled_job_dispatched(...)`
- `mark_local_scheduled_job_error(...)`
- `update_local_scheduled_job(...)`
- `delete_local_scheduled_job(...)`

---

## 5) End-To-End Behavior Summary

## Immediate Send
1. User prepares recipients + message + optional files in Send page.
2. App creates send job and enqueues to `LocalWorkflowController`.
3. Worker ensures profile WhatsApp session is ready.
4. Each item is sent via Selenium.
5. Result logged in `local_send_logs`.

## Scheduled Send
1. User clicks `Schedule…` on Send page.
2. Job payload is stored in `local_scheduled_jobs` with `PENDING`.
3. Background schedule worker picks due jobs.
4. Marks job `DISPATCHED` and enqueues for normal send flow.
5. Errors are stored in job `error_text`.

## Group Members To Normal Contact List
1. User selects groups on Groups page.
2. Clicks `Add members to contact list`.
3. App opens each group and member contact info.
4. Reads member name + phone.
5. Saves/upserts into `local_contacts` under list name = group name.

---

## 6) UI Components/Wiring Helpers

### `app/ui/qt/widgets/send_page_widgets.py`
- Reusable send-page widgets:
  - composer
  - action bar
  - schedule time picker

### `app/ui/qt/widgets/chat_preview.py`
- Live rendered preview of message + attachments.

### `app/ui/qt/widgets/profile_combo_utils.py`
- Shared profile combo population and selection helpers.

### `app/ui/qt/styles.py`
- Global Qt styling/theme rules.

---

## 7) Notes For Future Documentation Updates

When adding a new button/feature:
1. Add it to this file under the page/module section.
2. Include:
   - Button label
   - Method called on click
   - DB/network side effects
   - Any background thread behavior

