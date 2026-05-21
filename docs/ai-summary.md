# Galactia AI Summary

Cette page documente le comportement actuel de la fonctionnalite de resume IA.
Le flux est commun aux mentions directes et a `/summary`, avec configuration
par guilde, quotas soft, observabilite DB et resume map-reduce pour les gros
volumes.

## Declenchement

Galactia traite une demande IA si le message contient une mention utilisateur
directe du bot, par exemple `@Galactia resume les 20 derniers messages`.

La meme fonctionnalite est disponible avec la commande slash publique :

```text
/summary demande:les 20 derniers messages preset:catchup channel:#raid
```

Sans option `channel`, `/summary` resume le salon dans lequel la commande est
lancee. Avec `channel`, Galactia resume ce salon cible et poste le resultat
dans le salon d'appel.

Cas ignores :

- `@everyone` sans mention directe de Galactia.
- `@here` sans mention directe de Galactia.
- mention d'un role dont Galactia fait partie, sans mention directe du bot.

Si un message contient a la fois `@Galactia` et `@everyone` ou un role,
Galactia repond, mais toutes les reponses IA utilisent
`discord.AllowedMentions.none()`.

## Commandes Discord

- `/summary demande:<texte> [preset] [channel]` : resume le salon courant ou un salon cible.
- `/galactia status` : affiche sante IA, configuration resume et usage du jour.
- `/galactia setup start` : initialise l'onboarding de la guilde.
- `/galactia setup start` ouvre aussi le panneau interactif ephemeral de configuration.
- `/galactia setup summary ...` : active/configure les resumes IA.
- `/galactia setup finish` : valide les permissions et marque le setup termine.
- `/galactia config timezone <tz>` : definit la timezone, par defaut `Europe/Paris`.
- `/galactia config language <fr|en>` : definit la langue de guilde.
- `/galactia config max_messages <1..2000>` : ajuste le maximum selectionnable.
- `/galactia config allowed_channel add|remove|all|list [channel]` : limite les salons resumables ou revient a tous les salons accessibles.
- `/galactia config allowed_role add|remove|clear|list [role]` : limite les roles autorises.
- `/galactia config manager_role add|remove|clear|list [role]` : delegue l'administration Galactia a des roles Discord.

Les commandes de configuration sont admin-only. Si une liste de salons resumables
est definie, Galactia refuse les resumes dont le salon cible n'y figure pas. Si une liste de roles
autorises est definie, l'utilisateur doit avoir un role autorise ou etre
administrateur.

Sur une nouvelle guilde publique, le resume IA est desactive tant qu'un
administrateur n'a pas termine `/galactia setup`.

## Intention structuree

Le message utilisateur est analyse par OpenAI puis valide par Pydantic v2 dans
`SummaryIntent`.

Schema attendu :

```json
{
  "summary": true,
  "wrong_channel": false,
  "authors": null,
  "time_limit": null,
  "count_limit": 20,
  "selection_mode": "latest",
  "preset": "catchup",
  "focus": null
}
```

Champs :

- `summary`: `true` si la demande est un resume.
- `wrong_channel`: `true` si l'utilisateur demande un autre salon par texte non resolu ou une source externe.
- `authors`: auteurs demandes par texte, ou `null`. Les vraies `@mentions` Discord sont prioritaires.
- `time_limit`: periode floue ou explicite, par exemple `hier`, `ce matin`, `depuis 8h`.
- `count_limit`: nombre de messages demandes.
- `selection_mode`: `latest` par defaut, `earliest` pour les premiers messages.
- `preset`: `catchup`, `decisions`, `actions`, `raid`, `drama`, `funny` ou `null`.
- `focus`: precision libre, par exemple `strats raid` ou `annonces importantes`.

Si la sortie JSON est absente, invalide ou non conforme au schema, Galactia
repond avec un message clair et ne lance pas de resume silencieux.

## Parser temporel

Les dates ne passent plus par un LLM. `galactia/time_parser.py` contient un
parser deterministe avec support FR pour :

- `hier`, `aujourd'hui`, `ce matin`, `cet apres-midi`, `ce soir` ;
- `cette semaine`, `semaine derniere`, `ce mois` ;
- periodes relatives : `les 3 dernieres heures`, `les 2 derniers jours`,
  `les 4 dernieres semaines`, `les 6 derniers mois`, `depuis 30 minutes`,
  `depuis 2h`, `depuis 3 jours` ;
- jours de semaine : `lundi`, `mardi dernier`, `mercredi soir`,
  `jeudi matin`, `vendredi apres-midi` ;
- mois nommes et dates partielles : `janvier 2025`, `en mars`,
  `mars dernier`, `le 5 juin`, `5 juin 2025` ;
- annees completes comme `2025`, `en 2025`, `annee 2025` ;
- trimestres, semestres et saisons : `T1 2025`, `Q2 2025`,
  `premier trimestre`, `trimestre dernier`, `S2 2025`,
  `printemps 2025`, `ete 2025`, `automne 2025`, `hiver 2025` ;
- bornes ouvertes ou fermees : `avant 18h`, `jusqu'a 18h`,
  `apres 21h`, `a partir de 8h`, `entre lundi et mercredi`, `du 5 juin au 7 juin`,
  `depuis lundi jusqu'a mercredi`, `de 21h a 23h` ;
- abreviations FR Discord : `auj`, `ajd`, `aprem`, `lun`, `mar der`,
  `ven soir`, `janv 2025`, `sept der`, `les 3 der j`, `dep 30 mn`,
  `dep. 2 sem`, `avt 18h`, `jusq. 18h`, `dep lun jusq mer`,
  `2e trim 2025`, `trim der` ;
- dates explicites via `dateutil`.

La timezone vient de la configuration guilde, avec fallback `Europe/Paris`.
Le fallback 24h n'est applique que si l'utilisateur ne fournit ni periode ni
nombre de messages. Si une periode explicite n'est pas comprise, Galactia
demande de reformuler au lieu d'utiliser silencieusement les dernieres 24h.

Les dates sans annee qui tomberaient dans le futur prennent la derniere
occurrence passee. Exemple : le 21 mai 2026, `le 5 juin` cible le 5 juin 2025.

La borne minimale reste le 15/10/2024 :

- periode entierement anterieure : refus clair sans scan Discord ;
- debut anterieur mais fin autorisee : debut ajuste au 15/10/2024 avec notice ;
- plage invalide avec `end < start` : refus clair.

## Prompts versionnes

Les prompts OpenAI sont stockes dans `galactia/prompts/` et rendus avec
`importlib.resources`.

Prompts actifs :

- `intent.v2.md`
- `sanitize.v1.md`
- `summary_single.v3.md`
- `summary_map.v2.md`
- `summary_reduce.v2.md`

Les anciens prompts restent dans le repo pour historique/tests. Les logs peuvent
indiquer le nom/version du prompt, mais ne loggent pas son contenu.

## Ciblage deterministe du salon source

Avant tout appel OpenAI, Galactia resout le salon a resumer :

- option slash `channel:#salon` ;
- mention Discord `<#channel_id>` dans le texte ;
- lien `discord.com/channels/<guild>/<channel>`.

S'il n'y a pas de cible explicite, le salon courant reste la source. Un seul
salon cible est autorise par demande. Les liens ou salons d'une autre guilde,
les DM et les cibles ambiguës sont refuses avant OpenAI.

La config `summary_allowed_channel_ids` designe les salons resumables, pas
necessairement les salons d'appel. Liste vide = tous les salons de la guilde
sont resumables. Galactia verifie aussi que l'utilisateur et le bot ont
`view_channel` et `read_message_history` sur le salon cible.

Les noms texte non resolus comme `#general` ne sont pas supportes de facon
fiable; il faut utiliser une vraie mention Discord, un lien Discord ou l'option
slash `channel`.

## Limites et recuperation Discord

La limite utilisateur est configurable par guilde :

- defaut `summary_max_messages=500` ;
- maximum absolu `2000` ;
- `scan_limit` par defaut `min(result_limit * 10, summary_max_scan_messages, 5000)`.

Comportement :

- `time_limit` present sans `count_limit` : Galactia calcule `start/end` et recupere jusqu'a 150 messages max par defaut pour rester rapide.
- `count_limit` present sans `time_limit` : `start=None`, `end=now`, et recuperation des N derniers messages valides sans borne basse de date.
- Aucun `time_limit` et aucun `count_limit` : fallback sur les dernieres 24h avec `limit=100` et notice utilisateur.

`fetch_valid_messages()` utilise un mode explicite :

- `selection_mode="latest"` : historique Discord lu avec `oldest_first=False`.
- `selection_mode="earliest"` : historique Discord lu avec `oldest_first=True`.

Messages exclus :

- messages vides ;
- messages de bots ;
- messages qui mentionnent Galactia directement, y compris via `<@id>` brut ;
- anciennes invocations de resume comme `/summary ...` ou `/galactia summary ...` ;
- messages dont l'auteur ne correspond pas au filtre demande.

Le sous-ensemble final est toujours transmis au modele en ordre chronologique.

## Filtres auteurs

Les `@mentions` Discord explicites sont le mode fiable et prioritaire.

Si OpenAI detecte un auteur en texte libre, Galactia tente de le resoudre parmi
les membres du salon. Si le nom est inconnu ou ambigu, Galactia refuse le
resume et demande une `@mention` Discord, au lieu de resumer tout le salon par
erreur.

La mention de Galactia elle-meme sert uniquement a appeler le bot et n'est
jamais consideree comme un filtre auteur.

## Generation et presets

Presets disponibles :

- `catchup` : resume general court ;
- `decisions` : decisions et arbitrages ;
- `actions` : taches, responsables, prochaines etapes ;
- `raid` : roster, strats, logs, preparation raid ;
- `drama` : conflits et tensions sans amplification ;
- `funny` : ton leger sans inventer.

Le preset slash est prioritaire sur le preset detecte dans l'intention.

Format de sortie principal :

- 3 a 7 bullet points courts par defaut ;
- petit paragraphe court si la discussion est simple ou narrative ;
- pas de citations, pas d'IDs source `[Sx]`, pas de jump links, pas de section `Sources`.

Avant l'appel OpenAI, chaque message est compacte et tronque pour eviter qu'un
petit nombre de messages tres longs force plusieurs appels IA.

Galactia privilegie un resume single-pass rapide. Le map-reduce est reserve aux
gros volumes (`messages_selected > 300`) ou aux corpus encore trop lourds apres
compaction :

1. messages chronologiques en lots token-safe ;
2. jusqu'a 6 lots traites en parallele avec concurrence max 3 ;
3. resume partiel de chaque lot en bullets courts ;
4. reduction finale simple sans citations ni sources.

## Service OpenAI

`galactia/ai_service.py` fournit `AIService`, base sur `openai.AsyncOpenAI` :

- appels async natifs, sans `asyncio.to_thread` ;
- timeout par type d'appel ;
- retry sur 429, 5xx, timeouts et erreurs de connexion ;
- retour `AIResponse(content, model, usage, latency_ms, attempts)`.

Timeouts actuels :

- intention/nettoyage : 25s + buffer global ;
- generation single-pass : 30s + buffer global ;
- generation map-reduce : 35s par appel map/reduce, avec lots map paralleles.

## Cooldown, cache et quotas

Cooldown fort apres confirmation `intent.summary=True` :

- 30s par couple `(guild_id, user_id)` ;
- 10s par couple `(guild_id, channel_id)` ou `channel_id` est le salon source.

Cache memoire process :

- resumes reussis uniquement ;
- TTL 2 minutes ;
- cle exacte incluant guilde, salon source, periode, limite, auteurs, mode, focus et preset ;
- pour les demandes count-only sans `time_limit`, le timestamp precis `end=now` n'est pas inclus.

Quotas persistants :

- guilde : 100 resumes/jour par defaut ;
- user : 20/jour ;
- salon source : 50/jour ;
- tokens guilde : 500k/jour.

Un depassement est logge, visible dans `/galactia status`, refuse proprement la
generation et journalise `quota_exceeded`.

## Observabilite DB

La table `ai_requests` journalise une ligne par demande resume suivie :

- IDs techniques : guilde, salon source, user ;
- source : `mention` ou `slash` ;
- statut : `sent`, `empty`, `cache_hit`, `cooldown`, `wrong_channel`, `error` ;
- preset, version de prompt, modele ;
- `messages_scanned`, `messages_selected`, `messages_ignored` ;
- tokens, latence, attempts, type d'erreur.

Aucun contenu utilisateur complet, prompt complet ou resume source brut n'est
stocke en DB.

## Sortie Discord

Le texte complet est genere avant toute decision de formatage Discord.

- Si le texte fait `<= 2000` caracteres : edition directe du message initial.
- Sinon : decoupage en chunks de `<= 1900` caracteres.
- `fit_for_discord()` reste un fallback d'urgence en cas d'echec Discord.

Sur resume reussi, la reponse commence par :

```text
Résumé de X messages.
```

Pour un resume cross-channel, le feedback indique le salon source :

```text
Résumé de X messages de #salon.
```

Si plusieurs notices s'appliquent, elles sont fusionnees sur la premiere ligne,
sans emoji Discord. Les compteurs de messages ignores et de lots map-reduce
restent disponibles dans les logs et `ai_requests`, mais ne sont plus affiches
dans la reponse publique.

## Tests locaux

Verification complete :

```powershell
python -m compileall galactia tests scripts
python -m pytest -q
python scripts\test_summary_flow_local.py
```

Le script local ne contacte ni Discord ni OpenAI. Il simule une demande
`resume les 20 derniers messages`, affiche `start/end/limit/mode`, les kwargs
passes a `channel.history()`, les messages selectionnes et la sortie finale.

Les tests incluent :

- fake Discord pour selection latest/earliest, scan stats, split et cache ;
- golden intents offline sur 30 prompts FR ;
- parser temporel deterministe ;
- AIService async avec retry/usage ;
- config guilde et quotas normalises ;
- map-reduce sans citations ni jump links.

## Exemples attendus

| Prompt | Comportement attendu |
|---|---|
| `@Galactia resume les 20 derniers messages` | `count_limit=20`, `time_limit=null`, `selection_mode=latest`, aucune borne basse de date. |
| `@Galactia resume les premiers messages de ce matin` | `time_limit=ce matin`, `selection_mode=earliest`. |
| `@Galactia resume les messages de @Elsia` | filtre auteur par ID Discord. |
| `@Galactia resume les messages de Elsia` si ambigu | refus clair et demande d'utiliser une `@mention`. |
| `@everyone resume` | ignore, commandes classiques toujours traitees. |
| `/summary demande:les dramas d'hier preset:drama` | meme pipeline que la mention, avec preset force. |
| `/summary demande:resume <#raid>` | resume `#raid` si meme guilde, config autorisee et permissions OK. |
| `/summary demande:les 50 derniers channel:#raid` | resume `#raid` et repond dans le salon d'appel. |
| `/summary demande:resume <#salon_a> <#salon_b>` | refus deterministe avant OpenAI. |
| `/galactia status` | affiche config resume, quotas soft et usage du jour. |
