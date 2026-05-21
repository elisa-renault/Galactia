LEGACY - ce prompt n'est plus utilisé au runtime.

Nous sommes le {now_iso} (heure locale Europe/Paris).

Tu reçois une expression temporelle floue comme "hier", "semaine dernière", "ce matin", "l'année dernière", "depuis 21h", ou "depuis mardi jusqu'à jeudi".
Tu dois répondre uniquement avec deux dates ISO 8601 HEURE DE PARIS, séparées par une virgule, correspondant au début et à la fin de cette période.

Si l'expression contient le mot "depuis" ou "from" et ne donne qu'un point de départ, alors tu dois prendre la date et l'heure actuelle comme fin de période.

Exemples valides à adapter avec la date et l'heure actuelle :
- "hier" sans "depuis" -> "2025-07-19T00:00:00,2025-07-19T23:59:59"
- "depuis hier" si aujourd'hui est dimanche 20 -> "2025-07-19T00:00:00,2025-07-20T14:05:00"
- "depuis mardi" -> "2025-07-16T00:00:00,2025-07-20T14:05:00"
- "depuis 8h" -> "2025-07-20T08:00:00,2025-07-20T14:05:00"

Ta réponse ne doit contenir que ces deux dates, au format ISO 8601, séparées par une virgule.

Expression temporelle : {time_limit}
