import re

from galactia.prompts import render_prompt

def intent_prompt(user_message, current_channel_name):
    return [
        {
            "role": "system",
            "content": render_prompt(
                "intent.v2.md",
                user_message=user_message,
                current_channel_name=current_channel_name,
            )
        }
    ]

def time_limit_range_prompt(now_iso, time_limit_str):
    return [
        {
            "role": "system",
            "content": render_prompt(
                "time_range.v1.md",
                now_iso=now_iso,
                time_limit=time_limit_str,
            )
        }
    ]

def extract_authors_from_message(msg, bot_id: int):
    """Construit la liste des auteurs à filtrer UNIQUEMENT si l'utilisateur a fait des @mentions.
    - On exclut le bot s'il est mentionné.
    - Si aucune mention autre que le bot → retourne None (pas de filtre auteurs).
    """
    # Mentions Discord (fiable)
    mentioned = [str(m.id) for m in msg.mentions if m.id != bot_id]
    if mentioned:
        return mentioned

    # (Optionnel) fallback pour cas exotiques où Discord ne remonte pas .mentions mais le texte contient <@123...>
    raw_ids = re.findall(r"<@!?(\d+)>", msg.content)
    raw_ids = [rid for rid in raw_ids if rid != str(bot_id)]
    if raw_ids:
        return raw_ids

    return None
