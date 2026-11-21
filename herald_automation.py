import asyncio
import json
import os
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

import discord
from discord import app_commands

DATA_FILE = "herald_data.json"
DATA = {"guilds": {}, "scheduled": []}
SUPER_USERS = set()
SPAM_WINDOWS = defaultdict(deque)
SPAM_COOLDOWNS = {}
ALLOWED_MENTIONS = discord.AllowedMentions(users=True, roles=False, everyone=False)


def load_data():
    global DATA
    if not os.path.exists(DATA_FILE):
        DATA = {"guilds": {}, "scheduled": []}
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            loaded = json.load(file)
        DATA = loaded if isinstance(loaded, dict) else {"guilds": {}, "scheduled": []}
        DATA.setdefault("guilds", {})
        DATA.setdefault("scheduled", [])
    except (OSError, json.JSONDecodeError):
        DATA = {"guilds": {}, "scheduled": []}


def save_data():
    temporary = DATA_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(DATA, file, indent=2, ensure_ascii=False)
    os.replace(temporary, DATA_FILE)


def guild_config(guild_id):
    config = DATA.setdefault("guilds", {}).setdefault(str(guild_id), {})
    config.setdefault("permissions", {})
    config.setdefault("audit_log", [])
    config.setdefault("automations", [])
    config.setdefault("anti_spam", {"enabled": False, "channel_id": None, "threshold": 6, "window_seconds": 7})
    config.setdefault("inactivity", {"enabled": False, "channel_id": None, "days": 14, "notified": []})
    config.setdefault("last_activity", {})
    return config


def is_owner(guild, user):
    return bool(guild and user and guild.owner_id == user.id)


def is_superuser(user):
    return bool(user and user.id in SUPER_USERS)


def has_access(guild, user, scope):
    if not guild or not user:
        return False
    if is_owner(guild, user) or is_superuser(user) or getattr(getattr(user, "guild_permissions", None), "administrator", False):
        return True
    permissions = guild_config(guild.id).get("permissions", {})
    granted = set(permissions.get(str(user.id), []))
    return "admin" in granted or scope in granted


def can_manage(guild, user):
    return has_access(guild, user, "admin")


def audit_event(guild_id, actor_id, event, details):
    config = guild_config(guild_id)
    entries = config.setdefault("audit_log", [])
    entries.append({
        "timestamp": int(time.time()),
        "actor_id": int(actor_id) if actor_id is not None else 0,
        "event": str(event)[:60],
        "details": str(details).replace("@everyone", "@ everyone").replace("@here", "@ here")[:180],
    })
    config["audit_log"] = entries[-100:]
    save_data()


def grant_permission(guild_id, user_id, scope, actor_id=None):
    config = guild_config(guild_id)
    entries = config["permissions"].setdefault(str(user_id), [])
    if scope not in entries:
        entries.append(scope)
    audit_event(guild_id, actor_id if actor_id is not None else user_id, "permission_granted", f"member {user_id} received {scope}")


def revoke_permission(guild_id, user_id, scope, actor_id=None):
    config = guild_config(guild_id)
    entries = config["permissions"].get(str(user_id), [])
    if scope in entries:
        entries.remove(scope)
    if not entries:
        config["permissions"].pop(str(user_id), None)
    audit_event(guild_id, actor_id if actor_id is not None else user_id, "permission_revoked", f"member {user_id} lost {scope}")


def get_automation(guild, automation_id):
    return next((item for item in guild_config(guild.id)["automations"] if item.get("id") == automation_id), None)


def find_role(guild, value):
    raw = str(value or "").strip()
    mention = re.search(r"<@&(\d+)>", raw)
    id_match = mention or re.search(r"(?<!\d)(\d{15,22})(?!\d)", raw)
    role = guild.get_role(int(id_match.group(1))) if id_match else None
    normalized = re.sub(r"<@&\d+>", "", raw)
    normalized = re.sub(r"^@+", "", normalized).strip().casefold()
    if role is None and normalized:
        role = discord.utils.find(lambda item: item.name.casefold() == normalized, guild.roles)
    if role is None or role.is_default() or role.managed:
        raise ValueError(f"could not find a usable role for {raw[:80]}")
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        raise ValueError("Herald needs the Manage Roles permission.")
    if role >= me.top_role:
        raise ValueError(f"Herald's highest role must be above {role.name}.")
    return role


def find_text_channel(guild, value):
    raw = str(value or "").strip()
    match = re.search(r"<#(\d+)>", raw)
    channel = guild.get_channel(int(match.group(1))) if match else None
    if channel is None and raw.isdigit():
        channel = guild.get_channel(int(raw))
    if channel is None:
        channel = discord.utils.find(
            lambda item: isinstance(item, (discord.TextChannel, discord.Thread))
            and item.name.casefold() == raw.casefold(),
            guild.channels,
        )
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise ValueError(f"could not find a text channel for {raw[:80]}")
    permissions = channel.permissions_for(guild.me)
    if not permissions.send_messages:
        raise ValueError(f"Herald cannot send messages in #{channel.name}.")
    return channel


def validate_plan(guild, incoming):
    if not isinstance(incoming, dict):
        raise ValueError("the automation planner did not return a valid object")
    trigger = str(incoming.get("trigger", "")).strip().lower()
    if trigger not in ("member_join", "member_leave", "message_contains"):
        raise ValueError("supported triggers are member joins, member leaves, and matching messages")
    condition = str(incoming.get("condition", "")).strip()[:120]
    if trigger == "message_contains" and not condition:
        raise ValueError("a message trigger needs a keyword or phrase")
    actions = incoming.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 4:
        raise ValueError("an automation needs between 1 and 4 actions")
    clean_actions = []
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("one of the actions was malformed")
        action_type = str(action.get("type", "")).strip().lower()
        if action_type == "add_role":
            if trigger != "member_join":
                raise ValueError("role assignment is only supported when a member joins")
            role = find_role(guild, action.get("role") or action.get("role_id"))
            clean_actions.append({
                "type": "add_role",
                "role_id": role.id,
                "apply_to_existing": bool(action.get("apply_to_existing", False)),
            })
        elif action_type == "send_message":
            channel = find_text_channel(guild, action.get("channel") or action.get("channel_id"))
            message = str(action.get("message", "")).strip()
            if not message or len(message) > 1700:
                raise ValueError("message actions must contain 1–1700 characters")
            if "@everyone" in message or "@here" in message:
                raise ValueError("mass mentions are not allowed in automations")
            clean_actions.append({
                "type": "send_message",
                "channel_id": channel.id,
                "message": message,
                "mention_member": bool(action.get("mention_member", False)),
            })
        else:
            raise ValueError("supported actions are role assignment and sending a message")
    name = re.sub(r"\s+", " ", str(incoming.get("name", "custom automation"))).strip()[:70]
    return {"name": name or "custom automation", "trigger": trigger, "condition": condition, "actions": clean_actions}


async def plan_from_request(guild, request_text, generate_ai_response):
    roles = [{"name": role.name, "id": role.id} for role in guild.roles if not role.is_default() and not role.managed]
    channels = [{"name": channel.name, "id": channel.id} for channel in guild.text_channels]
    system = (
        "You translate a server owner's request into one safe Discord automation plan. "
        "Return exactly one JSON object, with no markdown. Allowed keys: name, trigger, condition, actions. "
        "Allowed triggers: member_join, member_leave, message_contains. "
        "Allowed actions: {type:add_role, role:<role name or id>, apply_to_existing:<boolean>} and "
        "{type:send_message, channel:<text channel name or id>, message:<text>, mention_member:<boolean>}. "
        "For a request that says to give a role to new members AND also to existing members who lack it, "
        "use one add_role action with apply_to_existing=true. This means Herald must backfill the role "
        "to current non-bot members who do not already have it, as well as assign it to future joins. "
        "Do not ignore the existing-member part of the request."
        "Use only the roles and channels listed in the server inventory. Never invent IDs. "
        "Use {member} as the placeholder for the member mention. When naming a channel inside message text, use its exact <#ID> mention from the inventory. Do not include @everyone or @here. "
        "Do not create destructive, moderation, permission, or code-execution actions. "
        "If the request cannot be represented by the allowed schema, return {\"error\":\"unsupported request\"}. "
        "A welcome/verification request should usually add the requested role and send a short welcome message "
        "to the requested channel, with verification instructions included in the message."
    )
    user = (
        f"Server roles: {json.dumps(roles, ensure_ascii=False)}\n"
        f"Server text channels: {json.dumps(channels, ensure_ascii=False)}\n"
        f"Owner request: {request_text[:2500]}"
    )
    response, failed = await generate_ai_response([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])
    if failed or not response or response == "brb wifi acting up":
        raise ValueError("Herald couldn't understand that request right now.")
    cleaned = response.strip()
    if cleaned.startswith(chr(96) * 3):
        cleaned = cleaned.strip(chr(96)).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the planner did not return a usable automation plan")
    try:
        incoming = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("the planner returned invalid JSON; try phrasing the request more clearly") from exc
    if incoming.get("error"):
        raise ValueError("that request needs an action Herald doesn't support yet")
    wants_existing_members = re.search(
        r"\b(existing members?|anyone who (?:doesn't|does not|don't|do not) have (?:it|that role)|"
        r"members? who (?:don't|do not|doesn't|does not) have (?:it|that role))\b",
        request_text,
        re.IGNORECASE,
    )
    if wants_existing_members and isinstance(incoming.get("actions"), list):
        for action in incoming["actions"]:
            if isinstance(action, dict) and str(action.get("type", "")).casefold() == "add_role":
                action["apply_to_existing"] = True
    return validate_plan(guild, incoming)


def save_automation(guild_id, plan, creator_id):
    item = {
        "id": uuid.uuid4().hex[:8],
        "name": plan["name"],
        "trigger": plan["trigger"],
        "condition": plan.get("condition", ""),
        "actions": plan["actions"],
        "enabled": True,
        "created_by": int(creator_id),
        "created_at": int(time.time()),
    }
    guild_config(guild_id)["automations"].append(item)
    audit_event(guild_id, creator_id, "automation_created", f"created {item['name']} ({item['id']})")
    return item


def update_automation(guild, automation_id, plan, actor_id):
    item = get_automation(guild, automation_id)
    if item is None:
        raise ValueError("that automation no longer exists")
    item["name"] = plan["name"]
    item["trigger"] = plan["trigger"]
    item["condition"] = plan.get("condition", "")
    item["actions"] = plan["actions"]
    audit_event(guild.id, actor_id, "automation_edited", f"updated {item['name']} ({item['id']})")
    return item


def plan_embed(plan):
    labels = {
        "member_join": "when a member joins",
        "member_leave": "when a member leaves",
        "message_contains": f"when a message contains “{plan.get('condition', '')}”",
    }
    embed = discord.Embed(
        title="automation preview",
        description=f"**{plan['name']}**\nTrigger: {labels[plan['trigger']]}",
        color=discord.Color.blurple(),
    )
    for index, action in enumerate(plan["actions"], start=1):
        if action["type"] == "add_role":
            description = f"Assign <@&{action['role_id']}> to new members"
            if action.get("apply_to_existing"):
                description += " and existing members who don't have it yet"
        else:
            description = f"Send in <#{action['channel_id']}>:\n{action['message'][:700]}"
        embed.add_field(name=f"Action {index}", value=description, inline=False)
    embed.set_footer(text="Nothing is saved until an authorized person confirms.")
    return embed


class AutomationConfirmationView(discord.ui.View):
    def __init__(self, guild_id, creator_id, plan, replace_id=None):
        super().__init__(timeout=150)
        self.guild_id = int(guild_id)
        self.creator_id = int(creator_id)
        self.plan = plan
        self.replace_id = replace_id

    async def interaction_check(self, interaction):
        guild = interaction.guild
        if guild is None or not (can_manage(guild, interaction.user) or interaction.user.id == self.creator_id):
            await interaction.response.send_message("only the server owner or the person who requested this can confirm it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm and enable", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id or not has_access(guild, interaction.user, "automation"):
            await interaction.response.send_message("you no longer have permission to enable this automation.", ephemeral=True)
            return
        try:
            plan = validate_plan(guild, self.plan)
            item = update_automation(guild, self.replace_id, plan, interaction.user.id) if self.replace_id else save_automation(guild.id, plan, interaction.user.id)
        except Exception as exc:
            await interaction.response.send_message(f"couldn't save this automation: {str(exc)[:700]}", ephemeral=True)
            return
        result = "updated" if self.replace_id else "enabled"
        backfill_roles = [action["role_id"] for action in plan["actions"] if action["type"] == "add_role" and action.get("apply_to_existing")]
        if backfill_roles:
            for role_id in backfill_roles:
                asyncio.create_task(backfill_existing_members(guild, role_id, item["id"]))
            detail = " i'm now assigning it to existing members who don't have it yet."
        else:
            detail = ""
        await interaction.response.edit_message(content=f"{result} {item['name']} ({item['id']}).{detail}", embed=None, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="automation cancelled; nothing was saved.", embed=None, view=None)


class SetupWizardView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=180)
        self.guild_id = guild.id
        self.role_id = None
        self.channel_id = None
        self.verify_channel_id = None

    async def interaction_check(self, interaction):
        if interaction.guild is None or interaction.guild.id != self.guild_id or not can_manage(interaction.guild, interaction.user):
            await interaction.response.send_message("only the server owner or an authorized server manager can use this setup.", ephemeral=True)
            return False
        return True

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Choose the role new members should receive", min_values=1, max_values=1)
    async def choose_role(self, interaction, select):
        self.role_id = select.values[0].id
        await interaction.response.send_message(f"role selected: {select.values[0].mention}", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text, discord.ChannelType.news], placeholder="Choose the welcome / verification channel", min_values=1, max_values=1)
    async def choose_channel(self, interaction, select):
        self.channel_id = select.values[0].id
        await interaction.response.send_message(f"channel selected: {select.values[0].mention}", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text, discord.ChannelType.news], placeholder="Choose the verification channel", min_values=1, max_values=1)
    async def choose_verify_channel(self, interaction, select):
        self.verify_channel_id = select.values[0].id
        await interaction.response.send_message(f"verification channel selected: {select.values[0].mention}", ephemeral=True)

    @discord.ui.button(label="Enable welcome setup", style=discord.ButtonStyle.success)
    async def enable(self, interaction, button):
        if self.role_id is None or self.channel_id is None or self.verify_channel_id is None:
            await interaction.response.send_message("choose a role, welcome channel, and verification channel first.", ephemeral=True)
            return
        try:
            role = find_role(interaction.guild, str(self.role_id))
            channel = find_text_channel(interaction.guild, str(self.channel_id))
            verify_channel = find_text_channel(interaction.guild, str(self.verify_channel_id))
            plan = validate_plan(interaction.guild, {
                "name": "welcome and verification",
                "trigger": "member_join",
                "actions": [
                    {"type": "add_role", "role": str(role.id)},
                    {
                        "type": "send_message",
                        "channel": str(channel.id),
                        "message": f"welcome {{member}}! please verify yourself in {verify_channel.mention} to unlock the server.",
                        "mention_member": True,
                    },
                ],
            })
            item = save_automation(interaction.guild.id, plan, interaction.user.id)
        except Exception as exc:
            await interaction.response.send_message(f"couldn't enable the welcome setup: {str(exc)[:700]}", ephemeral=True)
            return
        await interaction.response.edit_message(content=f"welcome automation enabled ({item['id']}). New members will receive {role.mention}, a welcome in {channel.mention}, and a verification link to {verify_channel.mention}.", view=None)


def find_automation_from_request(guild, request_text):
    items = [item for item in guild_config(guild.id)["automations"] if item.get("enabled")]
    lowered = request_text.casefold()
    id_matches = re.findall(r"\b[a-f0-9]{8}\b", lowered)
    for automation_id in id_matches:
        item = get_automation(guild, automation_id)
        if item and item.get("enabled"):
            return item
    named = [
        item for item in items
        if item.get("name", "").casefold() in lowered
        or any(word in lowered for word in item.get("name", "").casefold().split() if len(word) > 3)
    ]
    if len(named) == 1:
        return named[0]
    if len(items) == 1:
        return items[0]
    return None


async def handle_superuser_direct_message(message, bot):
    guild = message.guild
    if guild is None or message.author.bot or not is_superuser(message.author):
        return False

    content = re.sub(r"[\x60*_~]", "", message.content or "").strip()
    lowered = content.casefold()
    add_request = bool(re.search(r"\b(?:give|add|assign|grant)\b", content, re.IGNORECASE))
    remove_request = bool(re.search(r"\b(?:remove|take\s+away|strip|revoke)\b", content, re.IGNORECASE))
    if not (add_request or remove_request):
        return False

    is_permission_request = bool(
        re.search(r"\b(?:permission|permissions|access)\b", content, re.IGNORECASE)
        and re.search(r"\b(?:commands?|automation|automations|admin|management|manage)\b", content, re.IGNORECASE)
    )
    is_role_request = bool(re.search(r"\brole\b|<@&\d+>", content, re.IGNORECASE))

    user_mention = re.search(r"<@!?(\d+)>", content)
    target_id = int(user_mention.group(1)) if user_mention else None
    if target_id == getattr(bot.user, "id", None):
        target_id = None
    if target_id is None and re.search(r"\b(?:me|myself|my account)\b", content, re.IGNORECASE):
        target_id = message.author.id
    if target_id is None:
        numeric_ids = re.findall(r"(?<!\d)(\d{15,22})(?!\d)", content)
        role_mention = re.search(r"<@&(\d+)>", content)
        if role_mention:
            numeric_ids = [item for item in numeric_ids if item != role_mention.group(1)]
        if is_permission_request and len(numeric_ids) == 1:
            target_id = int(numeric_ids[0])

    if is_permission_request and target_id is not None:
        scope = "admin" if re.search(r"\b(?:full management|admin|administrator)\b", content, re.IGNORECASE) else (
            "automation" if re.search(r"\bautomations?\b", content, re.IGNORECASE) else "commands"
        )
        try:
            target = guild.get_member(target_id)
            if target is None:
                target = await guild.fetch_member(target_id)
            if target.id == guild.owner_id:
                await message.reply("the server owner already has full access.")
                return True
            if add_request:
                grant_permission(guild.id, target.id, scope, message.author.id)
                await message.reply(f"done, <@{target.id}> now has Herald {scope} access.")
            else:
                revoke_permission(guild.id, target.id, scope, message.author.id)
                await message.reply(f"done, removed Herald {scope} access from <@{target.id}>.")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            await message.reply(f"i couldn't update that access: {str(exc)[:400]}")
        return True

    if not is_role_request:
        return False
    if target_id is None:
        await message.reply("who should receive the role? mention them, use their user ID, or say 'me'.")
        return True

    role_mention = re.search(r"<@&(\d+)>", content)
    role_id = role_mention.group(1) if role_mention else None
    if role_id is None:
        role_position = re.search(r"\brole\b", content, re.IGNORECASE)
        trailing_ids = re.findall(r"(?<!\d)(\d{15,22})(?!\d)", content[role_position.end():]) if role_position else []
        all_ids = re.findall(r"(?<!\d)(\d{15,22})(?!\d)", content)
        all_ids = [item for item in all_ids if item != str(target_id)]
        if len(trailing_ids) == 1:
            role_id = trailing_ids[0]
        elif len(all_ids) == 1:
            role_id = all_ids[0]
    try:
        role = None
        if role_id:
            role = find_role(guild, role_id)
        else:
            role = next((candidate for candidate in sorted(guild.roles, key=lambda item: len(item.name), reverse=True)
                         if not candidate.is_default() and not candidate.managed and candidate.name.casefold() in lowered), None)
        if role is None:
            await message.reply("which role should i use? mention the role, include its name, or send its role ID.")
            return True
        member = guild.get_member(target_id)
        if member is None:
            member = await guild.fetch_member(target_id)
        if add_request:
            if role in member.roles:
                await message.reply(f"<@{member.id}> already has **{role.name}**.")
                return True
            await member.add_roles(role, reason=f"Herald direct instruction from superuser {message.author.id}")
            audit_event(guild.id, message.author.id, "superuser_role_added", f"assigned {role.name} ({role.id}) to {member.id}")
            await message.reply(f"done, gave **{role.name}** to <@{member.id}>.")
        else:
            if role not in member.roles:
                await message.reply(f"<@{member.id}> doesn't have **{role.name}**.")
                return True
            await member.remove_roles(role, reason=f"Herald direct instruction from superuser {message.author.id}")
            audit_event(guild.id, message.author.id, "superuser_role_removed", f"removed {role.name} ({role.id}) from {member.id}")
            await message.reply(f"done, removed **{role.name}** from <@{member.id}>.")
    except (ValueError, discord.Forbidden, discord.HTTPException) as exc:
        action = "assign" if add_request else "remove"
        await message.reply(f"i couldn't {action} that role: {str(exc)[:500]}")
    except Exception as exc:
        action = "assign" if add_request else "remove"
        await message.reply(f"i couldn't {action} that role: {type(exc).__name__}: {str(exc)[:400]}")
    return True


async def handle_owner_automation_message(message, bot, generate_ai_response):
    guild = message.guild
    if guild is None or message.author.bot:
        return False
    content = message.content or ""
    if not is_superuser(message.author) and not (bot.user and (bot.user.mentioned_in(message) or "herald" in content.casefold())):
        return False
    target_matches = re.findall(r"<@!?(\d+)>", content)
    target_id_text = next((item for item in target_matches if not bot.user or int(item) != bot.user.id), None)
    permission_request = (
        re.search(r"\b(allow|grant|enable|let|authorize|revoke|remove|make)\b", content, re.IGNORECASE)
        and re.search(r"\b(commands?|access)\b", content, re.IGNORECASE)
        and re.search(r"\b(able|use|access|permission|commands?)\b", content, re.IGNORECASE)
    )
    if target_id_text and permission_request:
        if not (is_owner(guild, message.author) or is_superuser(message.author)):
            await message.reply("only the server owner or a Herald superuser can change who has access to my commands.")
            return True
        target_id = int(target_id_text)
        if target_id == guild.owner_id or target_id == message.author.id:
            await message.reply("the server owner already has full access.")
            return True
        if re.search(r"\b(revoke|remove)\b", content, re.IGNORECASE):
            revoke_permission(guild.id, target_id, "commands", message.author.id)
            await message.reply(f"removed command access from <@{target_id}>.")
        else:
            grant_permission(guild.id, target_id, "commands", message.author.id)
            await message.reply(f"<@{target_id}> can now use Herald's delegated server commands.")
        return True
    if not has_access(guild, message.author, "automation"):
        return False
    cancel_request = bool(re.search(
        r"\b(stop|cancel|undo|disable|pause|don't|dont|do not|no longer|never)\b",
        content,
        re.IGNORECASE,
    ) and re.search(r"\b(automation|automations|it|that|this|doing|do that|anymore)\b", content, re.IGNORECASE))
    if cancel_request:
        item = find_automation_from_request(guild, content)
        if item is None:
            active = [entry for entry in guild_config(guild.id)["automations"] if entry.get("enabled")]
            if active:
                await message.reply("i found multiple active automations. tell me the automation name or ID so i pause the right one.")
            else:
                await message.reply("there aren't any active automations to pause.")
            return True
        item["enabled"] = False
        audit_event(guild.id, message.author.id, "automation_paused", f"{item['name']} ({item['id']})")
        await message.reply(f"got it. i've paused **{item['name']}**. it won't run again unless someone resumes it.")
        return True
    looks_like_automation = bool(re.search(
        r"\b(automate|automation|make it so|set it up|when .*joins|if .*joins|new member|welcome message|"
        r"when .*leaves|goodbye message)\b",
        content,
        re.IGNORECASE,
    ))
    if not looks_like_automation:
        return False
    try:
        plan = await plan_from_request(guild, content, generate_ai_response)
    except Exception as exc:
        await message.reply(f"i couldn't turn that into an automation yet: {str(exc)[:700]}")
        return True
    await message.reply(embed=plan_embed(plan), view=AutomationConfirmationView(guild.id, message.author.id, plan), allowed_mentions=discord.AllowedMentions.none())
    return True


async def backfill_existing_members(guild, role_id, automation_id):
    role = guild.get_role(int(role_id))
    if role is None:
        print(f"[Herald automation] Backfill skipped in guild {guild.id}: role {role_id} no longer exists.")
        return
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles or role >= me.top_role:
        print(f"[Herald automation] Backfill skipped in guild {guild.id}: Herald cannot manage role {role_id}.")
        return
    assigned = 0
    skipped = 0
    failed = 0
    try:
        async for member in guild.fetch_members(limit=None):
            if member.bot or role in member.roles:
                skipped += 1
                continue
            try:
                await member.add_roles(role, reason=f"Herald automation {automation_id} existing-member backfill")
                assigned += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                failed += 1
                print(f"[Herald automation] Backfill failed for member {member.id} in guild {guild.id}: {type(exc).__name__}: {exc}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"[Herald automation] Could not fetch members for backfill in guild {guild.id}: {type(exc).__name__}: {exc}")
        return
    print(f"[Herald automation] Backfill finished for guild {guild.id}, automation {automation_id}: {assigned} assigned, {skipped} already had role or were bots, {failed} failed.")


async def execute_actions(guild, member, automation, test=False):
    for action in automation.get("actions", []):
        if action["type"] == "add_role":
            if test or member is None:
                continue
            role = guild.get_role(int(action["role_id"]))
            if role is None:
                continue
            try:
                await member.add_roles(role, reason=f"Herald automation {automation.get('id')}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[Herald automation] Role assignment failed in guild {guild.id}: {type(exc).__name__}: {exc}")
        elif action["type"] == "send_message":
            channel = guild.get_channel(int(action["channel_id"]))
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue
            content = str(action.get("message", ""))[:1800]
            if member is not None:
                content = content.replace("{member}", member.mention)
            else:
                content = content.replace("{member}", "a member")
            if test:
                content = f"[automation test] {content}"
            allowed = discord.AllowedMentions(users=[member] if action.get("mention_member") and member is not None and not test else [], roles=False, everyone=False)
            try:
                await channel.send(content, allowed_mentions=allowed)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[Herald automation] Message action failed in guild {guild.id}: {type(exc).__name__}: {exc}")


async def member_join_event(bot, member):
    if member.bot:
        return
    config = guild_config(member.guild.id)
    config["last_activity"].setdefault(str(member.id), int(time.time()))
    save_data()
    for automation in list(config["automations"]):
        if automation.get("enabled") and automation.get("trigger") == "member_join":
            await execute_actions(member.guild, member, automation)


async def member_leave_event(bot, member):
    if member.bot:
        return
    config = guild_config(member.guild.id)
    for automation in list(config["automations"]):
        if automation.get("enabled") and automation.get("trigger") == "member_leave":
            await execute_actions(member.guild, member, automation)


async def message_tracking(message):
    if message.guild is None or message.author.bot:
        return
    config = guild_config(message.guild.id)
    config["last_activity"][str(message.author.id)] = int(time.time())
    inactivity = config["inactivity"]
    if str(message.author.id) in inactivity.get("notified", []):
        inactivity["notified"].remove(str(message.author.id))
    save_data()
    for automation in list(config["automations"]):
        if automation.get("enabled") and automation.get("trigger") == "message_contains":
            if str(automation.get("condition", "")).casefold() in (message.content or "").casefold():
                await execute_actions(message.guild, message.author, automation)
    spam = config["anti_spam"]
    if not spam.get("enabled"):
        return
    key = (message.guild.id, message.author.id)
    now = time.monotonic()
    bucket = SPAM_WINDOWS[key]
    bucket.append(now)
    window = int(spam.get("window_seconds", 7))
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    threshold = int(spam.get("threshold", 6))
    if len(bucket) >= threshold and now - SPAM_COOLDOWNS.get(key, 0) >= 60:
        SPAM_COOLDOWNS[key] = now
        channel = message.guild.get_channel(int(spam["channel_id"])) if spam.get("channel_id") else message.channel
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                await channel.send(f"{message.author.mention}, please slow down a little. The server's anti-spam alert was triggered.", allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            except (discord.Forbidden, discord.HTTPException):
                pass


async def maintenance_loop(bot):
    last_inactivity_check = 0
    while not bot.is_closed():
        now = int(time.time())
        changed = False
        pending = []
        for item in DATA.get("scheduled", []):
            if int(item.get("run_at", 0)) > now:
                pending.append(item)
                continue
            guild = bot.get_guild(int(item.get("guild_id", 0)))
            channel = guild.get_channel(int(item.get("channel_id", 0))) if guild else None
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                try:
                    await channel.send(str(item.get("message", ""))[:1900], allowed_mentions=discord.AllowedMentions.none())
                    changed = True
                except (discord.Forbidden, discord.HTTPException):
                    item["attempts"] = int(item.get("attempts", 0)) + 1
                    if item["attempts"] < 3:
                        pending.append(item)
                    changed = True
            else:
                item["attempts"] = int(item.get("attempts", 0)) + 1
                if item["attempts"] < 3:
                    pending.append(item)
                changed = True
        DATA["scheduled"] = pending
        if now - last_inactivity_check >= 3600:
            last_inactivity_check = now
            for guild_id, config in DATA.get("guilds", {}).items():
                settings = config.get("inactivity", {})
                if not settings.get("enabled") or not settings.get("channel_id"):
                    continue
                guild = bot.get_guild(int(guild_id))
                channel = guild.get_channel(int(settings["channel_id"])) if guild else None
                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    continue
                days = max(1, min(365, int(settings.get("days", 14))))
                notified = set(settings.get("notified", []))
                for user_id, last_seen in config.get("last_activity", {}).items():
                    if user_id in notified or now - int(last_seen) < days * 86400:
                        continue
                    member = guild.get_member(int(user_id))
                    if member is None or member.bot:
                        continue
                    try:
                        await channel.send(f"{member.display_name} hasn't chatted here for {days}+ days.", allowed_mentions=discord.AllowedMentions.none())
                        notified.add(user_id)
                        changed = True
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                settings["notified"] = sorted(notified)
        if changed:
            save_data()
        await asyncio.sleep(30)


async def start_maintenance(bot):
    task = getattr(bot, "_herald_maintenance_task", None)
    if task is None or task.done():
        bot._herald_maintenance_task = asyncio.create_task(maintenance_loop(bot))


def build_command_tree(bot, generate_ai_response):
    root = app_commands.Group(name="herald", description="Herald server assistant and automation tools")
    automate_group = app_commands.Group(name="automate", description="Create automations from plain English")
    automations_group = app_commands.Group(name="automations", description="Manage this server's automations")
    permissions_group = app_commands.Group(name="permissions", description="Manage delegated Herald access")

    @root.command(name="setup", description="Open the guided welcome and verification setup")
    async def setup(interaction: discord.Interaction):
        if interaction.guild is None or not can_manage(interaction.guild, interaction.user):
            await interaction.response.send_message("you need server management permission to open setup.", ephemeral=True)
            return
        await interaction.response.send_message(
            "choose the role new members should receive and the channel where Herald should welcome them.",
            view=SetupWizardView(interaction.guild),
            ephemeral=True,
        )

    @automate_group.command(name="request", description="Describe an automation in normal language")
    @app_commands.describe(request="What should happen, and when?")
    async def automate_request(interaction: discord.Interaction, request: str):
        if interaction.guild is None or not has_access(interaction.guild, interaction.user, "automation"):
            await interaction.response.send_message("you don't have permission to create automations in this server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            plan = await plan_from_request(interaction.guild, request, generate_ai_response)
            await interaction.followup.send(embed=plan_embed(plan), view=AutomationConfirmationView(interaction.guild.id, interaction.user.id, plan), ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"i couldn't create that preview: {str(exc)[:700]}", ephemeral=True)

    @automations_group.command(name="list", description="List this server's saved automations")
    async def automations_list(interaction: discord.Interaction):
        if interaction.guild is None or not has_access(interaction.guild, interaction.user, "automation"):
            await interaction.response.send_message("you don't have permission to view server automations.", ephemeral=True)
            return
        items = guild_config(interaction.guild.id)["automations"]
        if not items:
            await interaction.response.send_message("there aren't any automations set up yet. try /herald setup or /herald automate request.", ephemeral=True)
            return
        lines = [f"{item['id']} · {item['name']} · {item['trigger']} · {'on' if item.get('enabled') else 'paused'}" for item in items[:12]]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @automations_group.command(name="edit", description="Edit an automation using a new plain-English request")
    @app_commands.describe(automation_id="The automation ID to replace", request="How the automation should work after the edit")
    async def automations_edit(interaction: discord.Interaction, automation_id: str, request: str):
        if interaction.guild is None or not has_access(interaction.guild, interaction.user, "automation"):
            await interaction.response.send_message("you don't have permission to edit automations.", ephemeral=True)
            return
        if get_automation(interaction.guild, automation_id) is None:
            await interaction.response.send_message("i couldn't find that automation ID.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            plan = await plan_from_request(interaction.guild, request, generate_ai_response)
            await interaction.followup.send(
                "review the replacement plan. the current automation stays active unless you confirm.",
                embed=plan_embed(plan),
                view=AutomationConfirmationView(interaction.guild.id, interaction.user.id, plan, replace_id=automation_id),
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"i couldn't prepare that edit: {str(exc)[:700]}", ephemeral=True)

    async def change_automation(interaction, automation_id, mode):
        if interaction.guild is None or not has_access(interaction.guild, interaction.user, "automation"):
            await interaction.response.send_message("you don't have permission to manage automations.", ephemeral=True)
            return
        item = get_automation(interaction.guild, automation_id)
        if item is None:
            await interaction.response.send_message("i couldn't find that automation ID.", ephemeral=True)
            return
        if mode == "delete":
            guild_config(interaction.guild.id)["automations"].remove(item)
            audit_event(interaction.guild.id, interaction.user.id, "automation_deleted", f"deleted {item['name']} ({item['id']})")
            await interaction.response.send_message(f"deleted {item['name']}.", ephemeral=True)
            return
        item["enabled"] = mode == "resume"
        audit_event(interaction.guild.id, interaction.user.id, "automation_resumed" if item["enabled"] else "automation_paused", f"{item['name']} ({item['id']})")
        await interaction.response.send_message(f"{item['name']} is now {'enabled' if item['enabled'] else 'paused'}.", ephemeral=True)

    @automations_group.command(name="pause", description="Pause an automation")
    async def automations_pause(interaction: discord.Interaction, automation_id: str):
        await change_automation(interaction, automation_id, "pause")

    @automations_group.command(name="resume", description="Resume an automation")
    async def automations_resume(interaction: discord.Interaction, automation_id: str):
        await change_automation(interaction, automation_id, "resume")

    @automations_group.command(name="delete", description="Delete an automation")
    async def automations_delete(interaction: discord.Interaction, automation_id: str):
        await change_automation(interaction, automation_id, "delete")

    @automations_group.command(name="test", description="Test an automation's message actions")
    async def automations_test(interaction: discord.Interaction, automation_id: str):
        if interaction.guild is None or not has_access(interaction.guild, interaction.user, "automation"):
            await interaction.response.send_message("you don't have permission to test automations.", ephemeral=True)
            return
        item = get_automation(interaction.guild, automation_id)
        if item is None:
            await interaction.response.send_message("i couldn't find that automation ID.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await execute_actions(interaction.guild, interaction.user, item, test=True)
        audit_event(interaction.guild.id, interaction.user.id, "automation_tested", f"{item['name']} ({item['id']})")
        await interaction.followup.send("test finished. Role changes are skipped during tests.", ephemeral=True)

    @permissions_group.command(name="grant", description="Give a member a Herald permission")
    @app_commands.describe(user="The member to authorize", scope="What they can do")
    @app_commands.choices(scope=[
        app_commands.Choice(name="Use delegated commands", value="commands"),
        app_commands.Choice(name="Create and manage automations", value="automation"),
        app_commands.Choice(name="Full Herald management", value="admin"),
    ])
    async def permissions_grant(interaction: discord.Interaction, user: discord.Member, scope: app_commands.Choice[str]):
        if interaction.guild is None or not (is_owner(interaction.guild, interaction.user) or is_superuser(interaction.user)):
            await interaction.response.send_message("only the server owner or a Herald superuser can grant Herald permissions.", ephemeral=True)
            return
        if user.bot or user.id == interaction.guild.owner_id:
            await interaction.response.send_message("choose a non-bot member other than the server owner.", ephemeral=True)
            return
        grant_permission(interaction.guild.id, user.id, scope.value, interaction.user.id)
        await interaction.response.send_message(f"{user.mention} now has {scope.name} access.", ephemeral=True)

    @permissions_group.command(name="revoke", description="Remove a member's Herald permission")
    @app_commands.describe(user="The member to de-authorize", scope="The permission to remove")
    @app_commands.choices(scope=[
        app_commands.Choice(name="Use delegated commands", value="commands"),
        app_commands.Choice(name="Create and manage automations", value="automation"),
        app_commands.Choice(name="Full Herald management", value="admin"),
    ])
    async def permissions_revoke(interaction: discord.Interaction, user: discord.Member, scope: app_commands.Choice[str]):
        if interaction.guild is None or not (is_owner(interaction.guild, interaction.user) or is_superuser(interaction.user)):
            await interaction.response.send_message("only the server owner or a Herald superuser can change Herald permissions.", ephemeral=True)
            return
        revoke_permission(interaction.guild.id, user.id, scope.value, interaction.user.id)
        await interaction.response.send_message(f"removed {scope.name} access from {user.mention}.", ephemeral=True)

    @permissions_group.command(name="list", description="Show delegated Herald permissions")
    async def permissions_list(interaction: discord.Interaction):
        if interaction.guild is None or not can_manage(interaction.guild, interaction.user):
            await interaction.response.send_message("you need server management permission to view this.", ephemeral=True)
            return
        entries = guild_config(interaction.guild.id)["permissions"]
        if not entries:
            await interaction.response.send_message("no delegated permissions have been granted.", ephemeral=True)
            return
        lines = [f"<@{user_id}> · {', '.join(scopes)}" for user_id, scopes in entries.items()]
        await interaction.response.send_message("\n".join(lines[:25]), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @root.command(name="audit", description="View recent Herald configuration and automation actions")
    async def audit(interaction: discord.Interaction):
        if interaction.guild is None or not can_manage(interaction.guild, interaction.user):
            await interaction.response.send_message("you need server management permission to view the audit log.", ephemeral=True)
            return
        entries = list(reversed(guild_config(interaction.guild.id).get("audit_log", [])[-6:]))
        if not entries:
            await interaction.response.send_message("there are no Herald audit entries for this server yet.", ephemeral=True)
            return
        lines = [
            f"<t:{entry['timestamp']}:R> · <@{entry['actor_id']}> · {entry['event'][:40]}: {entry['details'][:90]}"
            for entry in entries
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @root.command(name="template", description="Enable or disable a ready-made server automation")
    @app_commands.describe(name="The template to change", channel="Where Herald should post alerts", days="Inactivity threshold in days")
    @app_commands.choices(name=[
        app_commands.Choice(name="Enable anti-spam alerts", value="anti_spam"),
        app_commands.Choice(name="Enable inactivity alerts", value="inactivity"),
        app_commands.Choice(name="Enable goodbye messages", value="goodbye"),
        app_commands.Choice(name="Disable anti-spam alerts", value="disable_anti_spam"),
        app_commands.Choice(name="Disable inactivity alerts", value="disable_inactivity"),
    ])
    async def template(interaction: discord.Interaction, name: app_commands.Choice[str], channel: discord.TextChannel | None = None, days: app_commands.Range[int, 1, 365] = 14):
        if interaction.guild is None or not can_manage(interaction.guild, interaction.user):
            await interaction.response.send_message("you need server management permission to change templates.", ephemeral=True)
            return
        config = guild_config(interaction.guild.id)
        if name.value == "disable_anti_spam":
            config["anti_spam"]["enabled"] = False
            audit_event(interaction.guild.id, interaction.user.id, "template_disabled", "anti-spam alerts")
            await interaction.response.send_message("anti-spam alerts are off.", ephemeral=True)
            return
        if name.value == "disable_inactivity":
            config["inactivity"]["enabled"] = False
            audit_event(interaction.guild.id, interaction.user.id, "template_disabled", "inactivity alerts")
            await interaction.response.send_message("inactivity alerts are off.", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("choose a text channel for this template.", ephemeral=True)
            return
        if not target.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message("Herald cannot send messages in that channel.", ephemeral=True)
            return
        if name.value == "anti_spam":
            config["anti_spam"] = {"enabled": True, "channel_id": target.id, "threshold": 6, "window_seconds": 7}
            audit_event(interaction.guild.id, interaction.user.id, "template_enabled", f"anti-spam alerts in channel {target.id}")
            await interaction.response.send_message(f"anti-spam alerts are on. Herald will warn after 6 messages in 7 seconds in {target.mention}; it won't delete or punish members.", ephemeral=True)
        elif name.value == "inactivity":
            config["inactivity"] = {"enabled": True, "channel_id": target.id, "days": days, "notified": []}
            audit_event(interaction.guild.id, interaction.user.id, "template_enabled", f"inactivity alerts in channel {target.id}; threshold {days} days")
            await interaction.response.send_message(f"inactivity alerts are on. Herald will post in {target.mention} after {days} days without a message.", ephemeral=True)
        else:
            plan = validate_plan(interaction.guild, {
                "name": "goodbye message",
                "trigger": "member_leave",
                "actions": [{"type": "send_message", "channel": str(target.id), "message": "goodbye {member}, take care!", "mention_member": False}],
            })
            item = save_automation(interaction.guild.id, plan, interaction.user.id)
            await interaction.response.send_message(f"goodbye messages enabled in {target.mention} ({item['id']}).", ephemeral=True)

    @root.command(name="schedule", description="Schedule an announcement using an ISO date and time")
    @app_commands.describe(channel="Where to post", time_utc="ISO date and time with timezone, e.g. 2026-12-01T18:30:00+00:00", message="Announcement text")
    async def schedule(interaction: discord.Interaction, channel: discord.TextChannel, time_utc: str, message: str):
        if interaction.guild is None or not can_manage(interaction.guild, interaction.user):
            await interaction.response.send_message("you need server management permission to schedule announcements.", ephemeral=True)
            return
        if not channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message("Herald cannot send messages in that channel.", ephemeral=True)
            return
        try:
            run_at = datetime.fromisoformat(time_utc.replace("Z", "+00:00"))
            if run_at.tzinfo is None:
                raise ValueError("timezone is required")
            timestamp = int(run_at.astimezone(timezone.utc).timestamp())
        except ValueError:
            await interaction.response.send_message("use an ISO date and time with a timezone, such as 2026-12-01T18:30:00+00:00.", ephemeral=True)
            return
        if timestamp <= int(time.time()) or timestamp > int(time.time()) + 366 * 86400:
            await interaction.response.send_message("choose a future time within the next year.", ephemeral=True)
            return
        if not message.strip() or len(message) > 1800 or "@everyone" in message or "@here" in message:
            await interaction.response.send_message("the announcement must be 1–1800 characters and cannot use mass mentions.", ephemeral=True)
            return
        schedule_id = uuid.uuid4().hex[:8]
        DATA["scheduled"].append({
            "id": schedule_id,
            "guild_id": interaction.guild.id,
            "channel_id": channel.id,
            "run_at": timestamp,
            "message": message.strip(),
            "attempts": 0,
        })
        audit_event(interaction.guild.id, interaction.user.id, "announcement_scheduled", f"schedule {schedule_id} in channel {channel.id} at {timestamp}")
        await interaction.response.send_message(f"announcement scheduled for <t:{timestamp}:F> in {channel.mention}.", ephemeral=True)

    @root.command(name="status", description="Check Herald's server permissions and feature status")
    async def status(interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("use this command inside a server.", ephemeral=True)
            return
        me = interaction.guild.me
        permissions = me.guild_permissions if me else None
        config = guild_config(interaction.guild.id)
        automation_count = len(config["automations"])
        enabled_count = sum(1 for item in config["automations"] if item.get("enabled"))
        embed = discord.Embed(title="Herald · server status", color=discord.Color.blurple())
        embed.add_field(name="Automations", value=f"{enabled_count} enabled / {automation_count} total", inline=True)
        embed.add_field(name="Anti-spam", value="on" if config["anti_spam"].get("enabled") else "off", inline=True)
        embed.add_field(name="Inactivity alerts", value="on" if config["inactivity"].get("enabled") else "off", inline=True)
        embed.add_field(name="Manage Roles", value="available" if permissions and permissions.manage_roles else "missing", inline=True)
        embed.add_field(name="View Channels", value="available" if permissions and permissions.view_channel else "missing", inline=True)
        embed.add_field(name="Send Messages", value="available" if permissions and permissions.send_messages else "missing", inline=True)
        embed.set_footer(text="Herald 4.0.0")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @root.command(name="command", description="Use a delegated, non-destructive Herald command")
    @app_commands.describe(action="The action to run")
    @app_commands.choices(action=[
        app_commands.Choice(name="Ping", value="ping"),
        app_commands.Choice(name="Server status", value="status"),
        app_commands.Choice(name="List automations", value="automations"),
    ])
    async def delegated_command(interaction: discord.Interaction, action: app_commands.Choice[str]):
        if interaction.guild is None or not has_access(interaction.guild, interaction.user, "commands"):
            await interaction.response.send_message("the server owner hasn't granted you delegated command access.", ephemeral=True)
            return
        if action.value == "ping":
            await interaction.response.send_message(f"pong · {round(bot.latency * 1000)}ms", ephemeral=True)
        elif action.value == "status":
            config = guild_config(interaction.guild.id)
            enabled = sum(1 for item in config["automations"] if item.get("enabled"))
            await interaction.response.send_message(f"Herald has {enabled} enabled automation(s) in this server.", ephemeral=True)
        else:
            items = guild_config(interaction.guild.id)["automations"]
            lines = [f"{item['id']} · {item['name']} · {'on' if item.get('enabled') else 'paused'}" for item in items[:20]]
            await interaction.response.send_message("\n".join(lines) if lines else "no automations are configured.", ephemeral=True)

    root.add_command(automate_group)
    root.add_command(automations_group)
    root.add_command(permissions_group)
    bot.tree.add_command(root)


def install_herald_admin(bot, generate_ai_response, super_users=None):
    SUPER_USERS.clear()
    SUPER_USERS.update(int(user_id) for user_id in (super_users or []))
    load_data()
    if bot.tree.get_command("herald") is None:
        build_command_tree(bot, generate_ai_response)

    async def ready_listener():
        await start_maintenance(bot)

    async def join_listener(member):
        await member_join_event(bot, member)

    async def leave_listener(member):
        await member_leave_event(bot, member)

    bot.add_listener(ready_listener, "on_ready")
    bot.add_listener(join_listener, "on_member_join")
    bot.add_listener(leave_listener, "on_member_remove")
    bot.add_listener(message_tracking, "on_message")