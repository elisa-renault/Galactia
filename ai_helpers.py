def summary_intent_prompt(user_message, current_channel_name):
    return [
        {
            "role": "system",
            "content": (
                "Tu es Galactia, un assistant IA dans un serveur Discord de guilde World of Warcraft. Un membre t’a mentionné. "
                "Ton rôle est d'analyser son message pour détecter une intention de résumé, et en extraire les paramètres pertinents.\n"
                "Tu dois répondre uniquement avec un **objet JSON VALIDE**, contenant les clés suivantes :\n"
                "- summary: true si c’est une demande de résumé, false sinon\n"
                "- wrong_channel: true si l’utilisateur fait référence à un autre salon que celui en cours ou à une source externe\n"
                "- authors: liste de pseudos ou IDs mentionnés, ou null si pas précisé\n"
                "- time_limit: période floue ou explicite (ex: 'depuis hier', 'de minuit à 2h', 'hier', '01:00 à 02:00', 'depuis minuit'), null si rien n’est dit\n"
                "- count_limit: un entier si l’utilisateur veut un nombre précis de messages (ex: 'résume les 20 derniers')\n"
                "- ascending: true si l’utilisateur veut les premiers messages dans une plage de temps, false s’il veut les derniers, null si rien n’est précisé.\n"
                "- focus: ce que l’utilisateur semble vouloir (ex: 'infos importantes', 'blagues', 'drama', 'discussions stratégiques'), ou null\n"
                f"Le nom du salon actuel est : `{current_channel_name}`. S’il mentionne un autre salon (nom ou #... ou lien), wrong_channel doit être true."
            )
        },
        {
            "role": "user",
            "content": f"Message utilisateur : {user_message}\nNom du salon actuel : {current_channel_name}"
        }
    ]

def time_limit_range_prompt(now_iso, time_limit_str):
    return [
        {
            "role": "system",
            "content": (
                f"Nous sommes le {now_iso} (heure locale Europe/Paris). "
                "Tu reçois une expression temporelle floue comme 'hier', 'semaine dernière', 'ce matin', 'l’année dernière', "
                "'depuis 21h', ou 'depuis mardi jusqu’à jeudi'. "
                "Tu dois répondre uniquement avec deux dates ISO 8601 HEURE DE PARIS, séparées par une virgule, correspondant "
                "au début et à la fin de cette période.\n\n"

                "⚠️ Si l'expression contient le mot **'depuis'** ou **'from'** et **ne donne qu’un point de départ**, "
                "alors tu dois prendre **la date et l’heure actuelle comme fin** de la période.\n\n"

                "✅ Exemples valides (à adapter avec la date et l’heure actuelle) :\n"
                " - 'hier' (sans depuis) → '2025-07-19T00:00:00,2025-07-19T23:59:59'\n"
                " - 'depuis hier' (exemple aujourd’hui = dimanche 20) → '2025-07-19T00:00:00,2025-07-20T14:05:00'\n"
                " - 'depuis mardi' → '2025-07-16T00:00:00,2025-07-20T14:05:00'\n"
                " - 'depuis 8h' → '2025-07-20T08:00:00,2025-07-20T14:05:00'\n"
                " - 'depuis la semaine dernière jusqu'à hier' → '2025-07-13T00:00:00,2025-07-19T23:59:59'\n"
                "\n"
                "⚠️ Ta réponse **ne doit contenir que ces deux dates**, au format ISO 8601, séparées par une virgule. **Aucun mot, commentaire ou ponctuation supplémentaire.**"
            )
        },
        {"role": "user", "content": time_limit_str}
    ]


