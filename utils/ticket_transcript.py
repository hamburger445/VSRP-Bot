import discord

from utils.core import format_datetime

async def build_transcript(channel: discord.TextChannel) -> str:
    lines = [
        f"Ticket Transcript: #{channel.name}",
        f"Channel ID: {channel.id}",
        f"Guild: {channel.guild.name} ({channel.guild.id})",
        "=" * 50,
        "",
    ]
    async for message in channel.history(limit=500, oldest_first=True):
        ts = format_datetime(message.created_at)
        author = f"{message.author} ({message.author.id})"
        content = message.content or ""
        lines.append(f"[{ts}] {author}")
        if content:
            lines.append(content)
        for attachment in message.attachments:
            lines.append(f"[Attachment] {attachment.filename}: {attachment.url}")
        for embed in message.embeds:
            if embed.title:
                lines.append(f"[Embed] {embed.title}")
            if embed.description:
                lines.append(embed.description)
        if message.components:
            lines.append("[Message contains buttons/components]")
        lines.append("")
    return "\n".join(lines)
