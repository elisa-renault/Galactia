# Audit produit, UX et architecture de Galactia

Date d'audit : 2026-05-19  
Cible analysee : Galactia comme bot Discord IA pour guildes WoW/MMO  
Base d'audit : depot actuel + ambition produit de lancement public  

## 0. Verdict executif

Galactia a une base utile pour une guilde privee : notifications Twitch/YouTube et resume IA de salon. En revanche, le depot actuel ne correspond pas encore a l'ambition d'un produit SaaS Discord IA scalable pour communautes gaming. Le risque principal n'est pas un manque d'idees, c'est l'inverse : trop d'ambition produit pour une architecture encore mono-instance, locale, peu testee, et sans modele multi-serveur robuste.

Le produit peut marcher s'il accepte une direction nette : devenir un assistant operationnel de guilde WoW/MMO, pas un chatbot IA generaliste. La valeur forte n'est pas "repondre avec de l'IA dans Discord". La valeur forte est : faire gagner du temps aux officiers, rendre les discussions et decisions retrouvables, suivre les obligations de roster/raid, et connecter Discord aux donnees de jeu qui comptent vraiment.

Aujourd'hui, Galactia est plus proche d'un bot personnel avance pour Les Galactiques que d'un produit public. Ce n'est pas grave, mais il faut le reconnaitre avant de construire Warcraft Logs, WowAudit, analytics, memoire ou premium par-dessus.

### Scoring global

| Categorie | Score | Lecture |
|---|---:|---|
| Vision produit | 5/10 | Bonne intuition de niche MMO, mais proposition encore diluee et trop large. |
| UX Discord | 4/10 | Fonctionnel pour admins techniques, peu discoverable pour membres et nouveaux serveurs. |
| Architecture fonctionnelle | 3/10 | Cogs simples, mais stockage local, multi-guild faible, pas de vraie couche domaine. |
| IA et qualite reponses | 5/10 | Usage resume pertinent, mais pipeline fragile, couteux et trop LLM-dependant. |
| Performance et scalabilite | 3/10 | Suffisant pour un serveur, non pret pour produit public multi-serveur. |
| Maintenabilite | 4/10 | Code lisible par endroits, mais fichiers longs, duplication, absence de tests/tooling. |
| Business et adoption | 6/10 | Niche WoW/MMO interessante si focus operations de guilde; notifications seules insuffisantes. |
| Readiness lancement public | 3/10 | A durcir fortement avant ouverture a des serveurs externes. |

### Priorites critiques

| Priorite | Sujet | Pourquoi maintenant |
|---:|---|---|
| P0 | Definir le coeur produit WoW/MMO | Sans focus, Galactia deviendra un empilement de gadgets IA/API. |
| P0 | Migrer JSON local vers DB multi-guild | Le stockage actuel bloque la scalabilite, la securite et le SaaS. |
| P0 | Ajouter permissions, rate limits et quotas IA | Un bot IA public sans limites explose en cout, spam et abus. |
| P1 | Refactoriser Twitch/YouTube en modules notifiers communs | La duplication annonce deja une dette forte pour les futures integrations. |
| P1 | Ajouter tests et CI | Les workflows Discord/API vont casser silencieusement sans filet. |
| P1 | Creer un onboarding admin clair | Sans configuration guidee, l'adoption hors serveur d'origine sera faible. |

---

## 1. Vision produit

### Ce que le depot dit vraiment

Le `README.md` presente Galactia comme un bot cree pour la guilde Les Galactiques dans World of Warcraft, avec deux taches principales : notifications Twitch et resumes de conversations (`README.md:5`, `README.md:9`). Le code confirme cette realite : les gros modules sont `galactia/cogs/twitch.py`, `galactia/cogs/youtube.py`, `galactia/cogs/ai.py` et `galactia/handlers/summary.py`.

Aucun module source versionne ne montre aujourd'hui Warcraft Logs, WowAudit, base de connaissance, analytics communautaires, memoire longue, dashboard SaaS actif, facturation, permissions avancees ou vraie gestion multi-guild. Le `README.md` mentionne le multi-guild comme evolution future via `guild_id` (`README.md:143-145`), ce qui est un signal important : ce n'est pas encore une capacite produit.

### Pourquoi Galactia devrait exister

La version forte de Galactia n'est pas "un bot Discord avec IA". C'est :

> Galactia aide les guildes WoW/MMO a transformer leur Discord en systeme operationnel : decisions lisibles, preparation raid, suivi roster, syntheses, rappels et integration avec les donnees de jeu.

Cette proposition est plus defendable que "ChatGPT dans Discord", car elle s'ancre dans des workflows reels :

- un officier qui revient apres 24h de discussion et veut savoir ce qui a ete decide ;
- un raid lead qui veut identifier les absences, besoins de compo, logs problematiques et actions avant le prochain raid ;
- une guilde qui veut garder une trace de decisions noyees dans Discord ;
- des membres qui ne veulent pas lire 300 messages pour comprendre le contexte ;
- un staff qui veut automatiser sans perdre le controle.

### Pourquoi une guilde utiliserait Galactia

Les raisons credibles :

- Recuperer vite le contexte d'un salon sans quitter Discord.
- Reduire la charge mentale des officiers.
- Centraliser les notifications utiles de l'ecosysteme guilde.
- Connecter Discord a Warcraft Logs/WowAudit/roster quand ces integrations existent.
- Produire des syntheses orientees actions, pas seulement des resumes neutres.
- Respecter les permissions et canaux internes de la guilde.

Les raisons faibles :

- "Parce qu'il y a de l'IA."
- "Parce qu'il peut tout faire."
- "Parce qu'il poste des lives Twitch."
- "Parce qu'il repond comme ChatGPT."

Les notifications Twitch/YouTube sont utiles pour une communaute, mais ce ne sont pas une differenciation SaaS. Beaucoup de bots savent notifier des contenus. Elles doivent rester des features de confort, pas le coeur du produit.

### Pourquoi pas simplement ChatGPT + Discord

Galactia peut battre ChatGPT + Discord seulement s'il apporte des capacites natives :

| Dimension | ChatGPT + Discord | Galactia cible |
|---|---|---|
| Acces au contexte | Copie manuelle de messages | Lecture controlee des salons autorises |
| Permissions | Hors Discord | Respect roles, salons, guildes |
| Donnees MMO | Manuelles | APIs Warcraft Logs/WowAudit/roster |
| Automatisation | Faible | Jobs, rappels, syntheses planifiees |
| Memoire guilde | Manuelle | Decisions et connaissances indexees |
| UX equipe | Individuelle | Partagee dans les workflows Discord |

Aujourd'hui, Galactia ne gagne que partiellement : le resume de salon est une vraie valeur, mais il reste limite a une interaction par mention et sans memoire durable.

### Differenciation potentielle

La differenciation viable est "assistant d'operations de guilde MMO", pas "assistant IA communautaire general".

Angle unique recommande :

- Syntheses de salons avec extraction de decisions, actions, objections et personnes concernees.
- Preparation raid : absences, roster, besoins, rappels, checklists.
- Integration Warcraft Logs/WowAudit : signaux actionnables, pas dashboards vanity.
- Base de connaissance guilde : strategies, regles, loot, macros, compo, decisions.
- Automatisations Discord propres : rappels, annonces, follow-ups, moderation douce du bruit.

Angle a eviter :

- Bot omniscient qui parle dans tous les salons.
- Analytics communautaires vagues.
- IA emotionnelle/persona trop presente.
- Trop d'integrations avant d'avoir un modele de donnees stable.

### Risque de feature creep

Le brief produit liste : resume, intentions naturelles, recherche contextuelle, reponses IA, analyse, Warcraft Logs, WowAudit, memoire, organisation, automatisations, analytics, base de connaissances, assistance raid, autres APIs. Cette liste est trop large pour le socle actuel.

Risque concret : chaque nouvelle integration aura ses commandes, son stockage, ses erreurs API, ses quotas, ses permissions et son UX. Avec l'architecture actuelle, cela produira un monolithe de cogs longs et difficiles a tester.

### Probleme de lisibilite produit

Un nouvel utilisateur ne peut pas comprendre immediatement "ce que Galactia fait de mieux". Le README dit Twitch + resume. Le brief dit assistant communautaire IA complet. Cette divergence cree un risque commercial : la promesse publique peut depasser fortement le produit installe.

Recommandation : formuler Galactia en une phrase simple :

> Galactia est l'assistant Discord des guildes WoW qui resume les discussions importantes, aide les officiers a preparer les raids, et connecte le serveur aux donnees utiles de la guilde.

---

## 2. UX et experience utilisateur

### Etat actuel

L'UX actuelle est orientee admin technique :

- `/twitch` et `/youtube` sont des groupes de commandes reserves aux administrateurs via `default_permissions=Permissions(administrator=True)` (`galactia/cogs/twitch.py:187`, `galactia/cogs/youtube.py:158`).
- L'IA se declenche quand le bot est mentionne (`galactia/cogs/ai.py:347-351`).
- Si l'intention n'est pas un resume, le bot repond que la fonctionnalite IA n'est pas encore disponible (`galactia/cogs/ai.py:356-388`).
- L'installation demande token Discord, intents `message_content` et `members` (`README.md:17`, `galactia/config.py:8-9`), credentials Twitch, OpenAI, et optionnellement YouTube.

Cette UX peut convenir au serveur d'origine, mais elle est trop implicite pour un lancement public.

### Frictions d'installation

| Friction | Impact |
|---|---|
| Configuration par variables d'environnement | Bloque les admins non techniques. |
| Intents privilegies Discord | Suscite mefiance et peut compliquer la verification Discord. |
| Pas d'onboarding interactif | L'admin ne sait pas quoi configurer apres invitation. |
| Pas de commande `/setup` globale | Les fonctions restent dispersees par integration. |
| Pas de page statut/config lisible | Difficile de diagnostiquer pourquoi une feature ne marche pas. |
| Credentials APIs externes | Mauvais fit SaaS si chaque guilde doit fournir ses propres cles. |

Pour un produit public, l'admin doit pouvoir installer Galactia et obtenir en moins de 3 minutes :

1. Les permissions demandees et leur raison.
2. Les salons autorises pour l'IA.
3. Les limites de cout/usage.
4. Les modules actives.
5. Un test de resume et une premiere config notification.

### Commandes et discoverability

Probleme : Galactia n'a pas de langage d'interaction unifie. Les notifications ont des slash commands, l'IA se fait par mention, les evolutions futures risquent d'ajouter encore d'autres styles.

Recommandation UX :

- `/galactia setup` : configuration guidee.
- `/galactia status` : etat modules, permissions, quotas, erreurs recentes.
- `/galactia help` : aide courte adaptee au role de l'utilisateur.
- `/summary` ou `/galactia summary` : commande explicite en plus de la mention.
- `/raid prepare`, `/raid recap`, `/raid reminders` seulement quand le module raid existe.
- Garder les mentions comme raccourci naturel, pas comme unique interface.

### Experience des membres

Points forts :

- Le resume par mention est naturel.
- Les messages sont adaptes a la limite Discord via `fit_for_discord`.
- Les notifications Twitch/YouTube avec embeds et boutons sont un format attendu.

Points faibles :

- Le membre ne sait pas quelles demandes IA sont supportees.
- Le bot repond publiquement "fonctionnalite non disponible" pour des mentions non resume, ce qui peut generer du bruit.
- Les resumes longs peuvent encore etre peu lisibles sur mobile si le contenu n'est pas structure en decisions/actions.
- Pas de mode "ephemeral" possible pour les mentions classiques, donc risque de spam de salon.
- Pas d'opt-in par salon visible.

### Experience des admins

Points forts :

- Les commandes Twitch/YouTube sont simples : add, list, remove, test.
- Les commandes test sont utiles pour verifier les annonces.
- La restriction admin evite une partie des abus.

Points faibles :

- Autoriser uniquement `administrator` est trop grossier. Beaucoup de guildes deleguent la gestion contenu sans donner admin complet.
- Les commandes `remove` Twitch/YouTube repondent avec `ephemeral=False` dans certains cas (`galactia/cogs/twitch.py:288`, `galactia/cogs/youtube.py:261`), ce qui peut poster du bruit admin dans les salons.
- Pas de journal admin lisible des erreurs API.
- Pas de configuration par guilde robuste.
- Pas de preview de cout IA ou quotas.

### Spam potentiel

Risques :

- Notifications Twitch/YouTube multiples dans un salon communautaire.
- Mentions IA repetitives par plusieurs membres.
- Reponses longues envoyees en chunks.
- Erreurs ou messages de fallback visibles.
- Test commands en production si mal utilisees.

Garde-fous UX necessaires :

- Cooldown par utilisateur, salon et guilde pour les resumes.
- Option "repondre en thread" pour les resumes.
- Option "resume court / standard / detaille".
- Resume avec sections fixes : "A retenir", "Decisions", "Actions", "Questions ouvertes".
- Limite de notifications par module et par salon.
- Logs admin separes des salons membres.

### UX mobile Discord

Discord mobile penalise les longs blocs Markdown. La sortie IA devrait privilegier :

- 3 a 5 bullets maximum en premier ecran.
- Titres tres courts.
- Pas de paragraphes longs.
- Actions clairement nommees avec personnes/date si disponibles.
- Thread ou bouton "detail" pour les resumes longs.

Le format cible pour un resume communautaire :

```md
**Resume du salon**
**A retenir**
- ...
- ...

**Decisions**
- ...

**Actions**
- @Nom : ...

**Questions ouvertes**
- ...
```

### Simplifications fortes

| Sujet | Recommandation |
|---|---|
| IA par mention | Garder, mais ajouter commande explicite `/summary`. |
| Notifications | Regrouper Twitch/YouTube sous un module `content alerts`. |
| Admin | Remplacer admin-only par permissions applicatives par role. |
| Tests de notifications | Les garder, mais les isoler dans `/galactia debug`. |
| Reponses IA non supportees | Ne pas repondre publiquement ou proposer une aide courte. |
| Onboarding | Ajouter `/galactia setup` avant toute autre feature publique. |

---

## 3. Architecture fonctionnelle

### Etat actuel

L'architecture actuelle est simple :

- `galactia/bot.py` cree le bot, purge/synchronise les commandes, charge les cogs Twitch, YouTube et IA.
- `galactia/cogs/twitch.py` contient stockage, commandes, polling, appels API, construction d'embeds, edition de messages.
- `galactia/cogs/youtube.py` repete un schema proche.
- `galactia/cogs/ai.py` orchestre sanitize, detection intention, parsing temps, recuperation messages et reponse.
- `galactia/handlers/summary.py` contient logique de recuperation/formatage/synthese.
- `galactia/settings.py` charge la configuration d'environnement.

C'est correct pour un bot mono-serveur, mais insuffisant pour un produit public.

### Risques d'architecture

| Risque | Preuve dans le depot | Consequence |
|---|---|---|
| Stockage local fragile | JSON via `data/twitch.json`, `data/youtube.json`, `data/twitch_config.json` | Corruption, pas de multi-instance, pas d'audit, pas de migrations. |
| Multi-guild incomplet | Multi-guild cite comme evolution future (`README.md:145`) | Donnees et config peuvent se melanger ou etre impossibles a isoler. |
| Cogs trop gros | Twitch 764 lignes, YouTube 556, AI 336 | Ajout d'integrations de plus en plus couteux. |
| Responsabilites melangees | Twitch gere DB, API, Discord, formatage, config | Tests difficiles, refactor inevitable. |
| Command sync risquee | `clear_commands` au demarrage (`galactia/bot.py:21-27`) | Peut supprimer/recreer des commandes de facon surprenante en production. |
| Guild resolution fragile | `_first_guild_id()` dans Twitch (`galactia/cogs/twitch.py:499`) | Mauvais serveur possible en multi-guild. |
| Config heterogene | Twitch via settings + JSON, YouTube via env direct | Comportements incoherents et durcissement difficile. |
| Absence de tests | Aucun fichier test/tooling detecte | Regression silencieuse probable. |

### Decoupage cible recommande

Galactia devrait evoluer vers ces couches :

| Couche | Responsabilite |
|---|---|
| Discord adapters | Slash commands, events, embeds, permissions Discord. |
| Domain services | SummaryService, AlertService, GuildSettingsService, RaidOpsService. |
| Integrations | TwitchClient, YouTubeClient, WarcraftLogsClient, WowAuditClient. |
| Persistence | Repositories DB, migrations, transactions, audit logs. |
| Jobs | Polling, scheduled summaries, retries, backoff. |
| AI gateway | Model routing, prompts versionnes, quotas, structured output, safety. |
| Observability | Logs structures, metrics, traces, erreurs admin. |

Le but n'est pas d'ajouter une architecture lourde d'un coup. Le but est de stopper la croissance horizontale des cogs.

### Donnees et multi-serveurs

Le stockage actuel ne contient pas un modele `guild_id` systematique. Pour un SaaS Discord, c'est non negociable.

Modele minimal cible :

- `guilds`: guild Discord, plan, timezone, locale, statut.
- `guild_settings`: salons autorises, roles managers, limites IA.
- `features`: modules actives par guilde.
- `content_alerts`: provider, external_id, destination_channel_id, role_id, state.
- `alert_events`: dernier live/video, message_id, etat, timestamps.
- `ai_requests`: guild_id, channel_id, user_id, type, tokens, cout estime, statut.
- `summaries`: metadata et resultat court si retention autorisee.
- `knowledge_items`: decisions, docs, strategies, liens utiles si module KB.

Sans ce socle, chaque nouvelle feature va creer son propre mini-stockage.

### Permissions

Le modele actuel admin-only est simple mais trop brutal.

Modele cible :

- Roles "Galactia Admin" pour config globale.
- Roles "Raid Lead" pour workflows raid.
- Roles "Content Manager" pour Twitch/YouTube.
- Permissions par salon pour IA.
- Denylist de salons sensibles.
- Trace admin pour toute lecture/synthese de salon.

### Features dangereuses a maintenir avec l'architecture actuelle

| Feature | Pourquoi dangereuse maintenant |
|---|---|
| Memoire longue | Besoin de retention, consentement, indexation, suppression, permissions. |
| Analytics communautaires | Risques privacy, interpretation abusive, besoin DB/metrics. |
| Warcraft Logs profond | API complexe, taux limites, logique metier raid, UX exigeante. |
| WowAudit complet | Donnees roster sensibles, mapping membres Discord/personnages. |
| Reponses IA libres | Prompt injection, cout, hallucinations, moderation. |
| Multi-guild public | Stockage et config actuels non adaptes. |
| Dashboard SaaS | Auth Discord, RBAC, DB, billing, securite, support. |

---

## 4. IA et qualite des reponses

### Usages IA pertinents

Pertinents pour Galactia :

- Resume de discussions longues.
- Extraction de decisions/actions/questions.
- Reformulation claire d'un debat.
- Recherche semantique dans une base de connaissance guilde.
- Synthese de logs/roster en langage naturel, si les donnees source sont determinees.
- Generation de compte-rendu raid a partir de donnees structurees.

Moins pertinents :

- Parsing de commandes simples.
- Gestion de permissions.
- Configuration admin.
- Notification Twitch/YouTube.
- Calculs de disponibilite, dates, seuils, quotas.
- Verification de donnees Warcraft Logs/WowAudit.

### Probleme actuel : trop de LLM dans le pipeline de controle

Le pipeline IA utilise le modele pour :

- Sanitizer la demande (`galactia/cogs/ai.py:32`, `model="gpt-5-mini"` a `galactia/cogs/ai.py:46`).
- Detecter l'intention (`galactia/cogs/ai.py:98-101`).
- Parser les expressions temporelles (`galactia/cogs/ai.py:119-128`).
- Generer le resume (`galactia/handlers/summary.py:144`).

Ce choix rend le flux flexible, mais fragile :

- Un LLM peut retourner du JSON invalide.
- Le parsing temporel peut etre incoherent.
- Les couts montent avec chaque mention.
- La latence cumule plusieurs appels.
- La securite "sanitize par LLM" n'est pas une garantie robuste.
- Les prompts deviennent une couche critique non versionnee/testee.

### Risques hallucination

| Cas | Risque |
|---|---|
| Resume de messages ambigus | Le bot peut creer une decision qui n'a pas ete prise. |
| Focus utilisateur type "drama" | Risque de surinterpretation sociale. |
| Auteur mal resolu | Resume des mauvaises personnes. |
| Periode mal parse | Resume incomplet ou trompeur. |
| Donnees raid/logs futures | Conclusions techniques fausses si non ancrees dans des metriques. |

Le prompt dit "N'invente jamais" dans `summary.py`, mais ce n'est pas suffisant. Il faut structurer la sortie et forcer l'incertitude.

### Architecture IA cible

1. **Routeur d'intentions hybride**
   - Slash commands pour intentions critiques.
   - Regex/parse deterministe pour limites simples.
   - LLM uniquement pour langage naturel ambigu.

2. **Structured output strict**
   - Schema JSON valide avec validation Pydantic.
   - Retry court si JSON invalide.
   - Fallback deterministic si echec.

3. **Budget IA**
   - Quotas par guilde/jour.
   - Cooldown par utilisateur.
   - Estimation tokens avant appel.
   - Refus propre si plage trop grande.

4. **Prompts versionnes**
   - Prompts dans fichiers ou objets versionnes.
   - Tests golden cases pour intentions, temps, resumes.
   - Evaluation sur conversations anonymisees.

5. **Sorties orientees action**
   - Resume court par defaut.
   - Sections stables.
   - Mention explicite "incertain" quand necessaire.
   - Citations/liens vers messages sources pour decisions importantes si possible.

6. **Memoire sous controle**
   - Pas de memoire implicite globale.
   - Retention configurable.
   - Indexation opt-in par salon.
   - Suppression par guilde/utilisateur si necessaire.

### Optimisations cout/latence

| Optimisation | Impact |
|---|---|
| Supprimer sanitize LLM au profit de filtre deterministe + instructions robustes | Moins cher, moins de latence. |
| Parser dates simples avec lib deterministic | Moins d'erreurs sur "hier", "24h", "depuis 8h". |
| Cache des resumes recents par salon/periode | Evite appels repetes. |
| Limiter hard les messages/tokens par plan | Controle cout SaaS. |
| Stream ou defer proprement les interactions slash | Meilleure perception latence. |
| Mode resume court par defaut | Meilleure UX mobile et cout reduit. |

### Ce qui devrait etre deterministe

- Permissions.
- Rate limits.
- Choix de salon.
- Nombre maximum de messages.
- Validation d'auteurs mentionnes.
- Decoupage Discord.
- Configuration de notifications.
- Calculs de dates simples.
- Seuils Warcraft Logs/WowAudit.

### Ce qui peut rester IA

- Synthese.
- Classification d'une demande floue.
- Reformulation.
- Extraction de themes.
- Recherche semantique.
- Explication de donnees structurees, avec sources.

---

## 5. Performance et scalabilite

### Etat actuel acceptable pour une guilde

Pour un seul serveur, le design peut tenir :

- Polling Twitch toutes les 60 secondes (`galactia/cogs/twitch.py:392`).
- Polling YouTube toutes les 300 secondes (`galactia/cogs/youtube.py:337`).
- OpenAI appele a la demande.
- JSON local suffisant si peu de donnees.

Mais ces choix ne passent pas bien a l'echelle.

### Risques de charge

| Zone | Risque |
|---|---|
| Discord history | Recuperation de centaines de messages par demande, latence et limites Discord. |
| OpenAI | Plusieurs appels par mention, cout et latence cumules. |
| Twitch polling | Multiplication par guildes et follows, quotas/API errors. |
| YouTube polling | Sessions creees par appel et quotas YouTube. |
| JSON writes | Concurrence, corruption, perte de donnees en multi-instance. |
| Embeds notifications | Spam et rate limits Discord. |
| Command sync | Risque operationnel au demarrage. |

### Limitations Discord

Risques specifiques :

- `message_content` est un intent sensible. A grande echelle, Discord peut demander justification forte.
- Lecture d'historique de salon doit rester proportionnee et transparente.
- Les bots qui spamment embeds/mentions peuvent etre rejetes par des communautes.
- Les slash commands globales ont propagation et contraintes.
- Les reponses longues degrade l'experience mobile.

### Jobs asynchrones

Le polling dans les cogs est simple, mais un produit public a besoin d'un vrai systeme de jobs :

- Jobs persistants avec `guild_id`.
- Backoff par provider.
- Retry controle.
- Dead-letter ou erreurs visibles.
- Jitter pour eviter appels synchronises.
- Verrouillage distribue si multi-instance.
- Separation worker/bot Discord.

Architecture cible minimale :

- Process bot Discord pour events/interactions.
- Worker pour polling et jobs planifies.
- DB centrale.
- Redis/cache pour locks, cooldowns, queues legeres.
- Observabilite.

### Priorites d'optimisation

| Priorite | Action |
|---:|---|
| P0 | Migrer stockage JSON vers DB avant multi-guild public. |
| P0 | Ajouter rate limits IA par guilde/user/salon. |
| P0 | Encadrer les permissions de lecture messages. |
| P1 | Centraliser clients HTTP et timeouts/retries. |
| P1 | Ajouter cache provider Twitch/YouTube. |
| P1 | Ajouter metrics : appels OpenAI, tokens, erreurs API, latence. |
| P2 | Separer workers de polling du process Discord. |

### Quick wins scalabilite

- Hard cap configurable sur messages resumes.
- Refus propre des plages trop larges.
- Cooldown de 30-60 secondes par salon pour l'IA.
- Cache court des resultats Twitch/YouTube par login/channel.
- Eviter `ClientSession` recree dans chaque helper YouTube.
- Ne pas purger toutes les commandes au demarrage en production.
- Logs structures pour erreurs API et cout IA.

---

## 6. Maintenabilite et dette technique

### Points positifs

- Le projet est petit et comprehensible.
- Les cogs isolent au moins les grands domaines Twitch/YouTube/AI.
- Certains helpers sont extraits (`summary.py`, `ai_helpers.py`).
- Les commandes test facilitent le debug manuel.
- Les erreurs API ne crashent pas systematiquement le bot.
- L'usage async est globalement coherent avec Discord.

### Dettes fortes

| Dette | Impact |
|---|---|
| Absence de tests | Chaque refactor ou API change est risque. |
| Fichiers cogs longs | La complexite va croitre vite. |
| Duplication Twitch/YouTube | Pattern notifier non factorise. |
| Stockage JSON dans les cogs | Couplage fort, pas de transactions. |
| Dependances non pinnees | Builds non reproductibles. |
| Pas de lint/format config | Style et erreurs non controles. |
| Logs avec contenu utilisateur | Risque privacy et bruit. |
| Prompts non testes | Regressions IA invisibles. |
| Config heterogene | YouTube env direct, Twitch settings centralisees. |

### Refactors prioritaires

1. Extraire une couche `repositories`
   - `AlertRepository`, `GuildSettingsRepository`, `AIUsageRepository`.
   - Implementation DB cible, JSON seulement en migration/dev si necessaire.

2. Extraire une couche `notifiers`
   - Provider Twitch/YouTube avec interface commune.
   - Service commun : add/list/remove/poll/announce/edit.
   - Templates embeds separes.

3. Extraire une couche `ai`
   - Intent parser.
   - Summary generator.
   - Token budget.
   - Prompt registry.

4. Ajouter tests de base
   - Formatage durees/dates.
   - Chunking Discord.
   - Parsing intention JSON/fallback.
   - Repositories.
   - Notifier duplicate/remove.

5. Ajouter tooling
   - `pyproject.toml`.
   - `ruff`.
   - `pytest`.
   - Dependances pinnees ou lockfile.
   - CI GitHub Actions.

### Ce qui doit probablement etre supprime ou deplace

- Purge globale des slash commands au demarrage en production.
- Sanitizer LLM comme etape obligatoire.
- Log du prompt complet et messages utilisateur en clair.
- `guilds[0]` comme resolution serveur.
- Stockage d'etat provider directement dans fichiers JSON sans schema.
- Commandes test visibles comme commandes admin normales en produit public.

---

## 7. Business et adoption

### Potentiel reel

Le potentiel existe, mais seulement avec un positionnement specialise. Les guildes WoW/MMO ont :

- beaucoup de coordination asynchrone ;
- des decisions perdues dans Discord ;
- des officiers surcharges ;
- des donnees externes complexes ;
- une culture d'outils tiers ;
- une disposition a payer si l'outil economise du temps de lead.

Le marche n'est pas gigantesque, mais il est assez qualifie pour un produit niche.

### Ce qui donne vraiment envie d'installer Galactia

| Feature | Attrait adoption | Commentaire |
|---|---:|---|
| Resume de salon oriente decisions/actions | Fort | Valeur immediate et differenciante si bien execute. |
| Preparation raid avec WowAudit/Warcraft Logs | Tres fort | Vraie douleur d'officiers/raid leads. |
| Rappels et follow-ups de decisions | Fort | Transforme le resume en action. |
| Base de connaissance guilde recherchable | Moyen/fort | Utile si onboarding membres et strats. |
| Notifications Twitch/YouTube | Faible/moyen | Sympa, mais substituable. |
| Analytics communautaires generiques | Faible | Souvent vanity ou inquietant. |
| Chat IA libre | Faible | Trop generique, difficile a monetiser. |

### Features gadgets

- Persona IA trop developpe.
- Reponses conversationnelles generales sans ancrage guilde.
- Notifications de trop nombreuses plateformes.
- Analytics de "mood" communautaire.
- Classements d'activite membres.
- Resume automatique de tous les salons sans intention claire.

### Avantage competitif possible

Le vrai avantage n'est pas l'IA seule. C'est le couplage :

Discord context + permissions + donnees WoW + workflows d'officiers + synthese actionnable.

Si Galactia devient seulement un bot de resumes et notifications, la defensabilite est faible. Si Galactia devient l'assistant d'operations raid/guilde, la niche est plus solide.

### Modele economique possible

| Plan | Cible | Contenu |
|---|---|---|
| Free | Petites guildes | Resume limite, 1-2 modules notifications, quotas bas. |
| Guild | Guildes actives | Resumes plus larges, rappels, KB, integrations WoW de base. |
| Raid Ops | Guildes progress | Warcraft Logs/WowAudit, preparation raid, rapports, alertes roster. |
| Community Pro | Grosses communautes | Multi-equipes, analytics prudents, support, retention configurable. |

Ne pas vendre "tokens IA". Vendre du temps gagne pour officers/raid leads.

### Barriere a l'entree

Faible si le produit reste generaliste. Moyenne si :

- workflows WoW bien modelises ;
- bonnes integrations externes ;
- historique et decisions bien exploites ;
- UX admin excellente ;
- prompts/evaluations adaptes au vocabulaire MMO ;
- confiance privacy.

### Risques business

| Risque | Gravite |
|---|---:|
| Promesse trop large vs produit reel | Critique |
| Cout IA non maitrise | Critique |
| Permissions Discord perçues comme intrusives | Eleve |
| Dependances APIs externes | Eleve |
| Marche trop niche si hors WoW mal gere | Moyen |
| Churn apres curiosite initiale | Eleve |
| Bots concurrents pour notifications | Moyen |

---

## 8. Priorisation strategique

### Fonctionnalites CORE

| Feature | Pourquoi core |
|---|---|
| Resume de salon court, fiable, actionnable | Valeur immediate, usage recurrent. |
| Extraction decisions/actions/questions | Differencie d'un simple resume ChatGPT. |
| Permissions et salons IA configurables | Necessaire pour confiance et adoption. |
| Onboarding admin `/galactia setup` | Necessaire hors serveur d'origine. |
| Quotas/cooldowns IA | Necessaire pour cout et abus. |
| Multi-guild DB | Necessaire pour produit public. |
| Status/config admin | Reduit support et friction. |

### Fonctionnalites secondaires

| Feature | Statut |
|---|---|
| Twitch/YouTube notifications | Garder, mais comme module contenu. |
| Syntheses planifiees hebdo | Utile apres stabilisation des resumes. |
| Recherche dans base de connaissance | Forte valeur mais besoin retention/indexation. |
| Rappels automatiques d'actions | Bon prolongement des resumes. |
| Internationalisation FR/EN | Utile si lancement public plus large. |
| Dashboard web minimal | Utile mais pas avant DB/RBAC. |

### Fonctionnalites probablement inutiles

| Feature | Pourquoi |
|---|---|
| Chatbot generaliste permanent | Peu differencie, couteux, risque de bruit. |
| Analytics sociaux vagues | Faible confiance, valeur floue. |
| Multiplication d'APIs gaming non WoW | Dilue la niche avant PMF. |
| Persona/lore du bot trop pousse | N'aide pas les officiers a gagner du temps. |
| Resume automatique de tout | Couteux, intrusif, spam. |

### Fonctionnalites dangereuses

| Feature | Danger |
|---|---|
| Memoire longue implicite | Privacy, consentement, suppression, permissions. |
| Analyse comportementale des membres | Risque social et adoption. |
| Recommandations raid non sourcees | Hallucinations et perte de confiance. |
| Auto-moderation IA | Faux positifs, conflits communautaires. |
| Actions automatiques sans validation admin | Degats operationnels. |
| Multi-guild avant DB/RBAC | Fuite ou melange de donnees. |

### Roadmap MVP realiste

#### Phase 1 - Durcir le bot actuel

- Ajouter DB relationnelle et modele `guild_id`.
- Remplacer stockage JSON par repositories.
- Ajouter `/galactia setup`, `/galactia status`, `/summary`.
- Ajouter permissions par role/salon.
- Ajouter quotas/cooldowns IA.
- Ajouter tests unitaires et CI.
- Supprimer ou encadrer logs de contenu utilisateur.

#### Phase 2 - Rendre le resume excellent

- Sortie standard : A retenir, Decisions, Actions, Questions.
- Structured output pour intention.
- Parsing date hybride deterministic + LLM fallback.
- Reponses en thread pour resumes longs.
- Cache de resumes recents.
- Evaluation sur exemples reels anonymises.

#### Phase 3 - Assistant operationnel guilde

- Rappels d'actions extraites.
- Synthese hebdomadaire de salons officiers/raid.
- Base de connaissance opt-in.
- Onboarding nouveaux membres via KB.
- Notifications contenu regroupees et configurables.

#### Phase 4 - Integrations WoW premium

- WowAudit : roster, enchants, ilvl, disponibilites, manquants.
- Warcraft Logs : signaux simples et sourcees, pas jugement IA libre.
- Preparation raid : checklist, risques, absences, compo.
- Rapport post-raid : faits, wipes, actions, liens sources.

### Roadmap long terme

- Dashboard web admin avec auth Discord.
- Plans payants et quotas.
- Multi-equipes par guilde.
- Indexation semantique controlee.
- Alertes proactives mais validables.
- Integrations calendriers/events.
- Observabilite SaaS et support admin.
- Templates par type de guilde : casual, progress, PvP, community.

---

## 9. Critique honnete

### Erreurs strategiques possibles

1. Construire trop d'integrations avant de prouver le coeur resume/actions.
2. Confondre "IA impressionnante" avec "outil utilise chaque semaine".
3. Lancer public avec stockage JSON local.
4. Croire que Twitch/YouTube suffisent pour adoption.
5. Faire un bot generaliste pour toutes communautes Discord.
6. Ne pas gerer les couts IA des le debut.
7. Sous-estimer la privacy Discord.
8. Ajouter un dashboard web avant d'avoir un workflow bot solide.
9. Laisser les cogs grossir jusqu'a devenir impossibles a tester.
10. Promettre des insights raid IA sans donnees sourcees et controlees.

### Illusions produit

- "Les guildes veulent une IA" : non, elles veulent moins de chaos.
- "Plus d'APIs = plus de valeur" : non, plus d'APIs = plus de maintenance.
- "Le resume suffit" : non, il faut des decisions et actions.
- "Les admins configureront tout" : non, ils abandonneront si setup trop long.
- "On ajoutera la scalabilite plus tard" : dangereux si le modele de donnees est faux.
- "Le prompt reglera les hallucinations" : faux, il faut sources, schemas et tests.

### Pieges classiques des bots Discord IA

- Trop parler dans les salons publics.
- Donner des reponses longues inutilisables sur mobile.
- Lire trop de messages sans expliquer pourquoi.
- Ne pas respecter les frontieres salon/role.
- Creer des notifications qui deviennent du bruit.
- Ne pas avoir de "kill switch" par guilde/module.
- Ne pas mesurer les couts par guilde.
- Ne pas fournir de statut clair quand une API externe tombe.

### Ce qui risque d'echouer

- Galactia comme "assistant IA communautaire universel".
- Les analytics communautaires trop abstraits.
- Le chat IA libre.
- La memoire longue sans UX de consentement.
- Les notifications comme argument principal de vente.
- Les integrations raid si elles sortent sans precision et sources.

### Ce qui peut vraiment marcher

- Resume de salon tres bien execute.
- Extraction d'actions et follow-ups.
- Preparation raid a partir de donnees WowAudit/Warcraft Logs.
- Synthese hebdo pour officiers.
- Recherche dans decisions/strats de guilde.
- Assistant d'onboarding nouveaux membres.
- Alertes simples, fiables et configurables.

---

## 10. Recommandations concretes

### Quick wins produit

| Action | Effort | Impact |
|---|---:|---:|
| Renommer la promesse autour de "operations de guilde WoW" | Faible | Fort |
| Ajouter une commande `/summary` explicite | Moyen | Fort |
| Structurer les resumes en decisions/actions/questions | Moyen | Fort |
| Ajouter `/galactia status` | Moyen | Moyen |
| Rendre tous les messages admin ephemeral | Faible | Moyen |
| Mettre les commandes test sous `/debug` | Faible | Moyen |
| Documenter les permissions demandees | Faible | Fort |

### Quick wins techniques

| Action | Effort | Impact |
|---|---:|---:|
| Ajouter `pyproject.toml` avec ruff/pytest | Faible | Fort |
| Ajouter tests pour `fit_for_discord`, `chunk_text`, dates | Faible | Moyen |
| Pinner les dependances | Faible | Moyen |
| Centraliser config YouTube dans `settings.py` | Faible | Moyen |
| Ne plus logger prompts/messages complets en prod | Faible | Fort |
| Ajouter cooldown IA en memoire | Moyen | Fort |
| Eviter purge globale commandes en prod | Moyen | Fort |

### Refactors structurants

| Refactor | Pourquoi |
|---|---|
| DB + repositories | Base de tout produit public. |
| Service commun `ContentAlertService` | Evite duplication Twitch/YouTube et futures plateformes. |
| `AIService` avec budget et schemas | Controle qualite, cout, securite. |
| `GuildSettingsService` | Permissions et config coherentes. |
| Worker jobs | Scalabilite polling et schedules. |

### Indicateurs a suivre

| Metrique | Pourquoi |
|---|---|
| Resumes par guilde/semaine | Adoption reelle. |
| Utilisateurs actifs resume | Usage au-dela des admins. |
| Tokens/cout par guilde | Rentabilite. |
| Latence p50/p95 resume | UX. |
| Taux d'echec API externe | Fiabilite. |
| Notifications envoyees par salon | Risque spam. |
| Actions extraites puis completees | Valeur operationnelle. |
| Retention guilde a 30 jours | Product-market fit. |

---

## 11. Vision cible ideale

Galactia cible devrait etre un assistant discret, fiable et operationnel.

### Experience admin ideale

1. L'admin invite Galactia.
2. `/galactia setup` ouvre une configuration guidee.
3. Il choisit les salons IA autorises.
4. Il choisit les roles qui peuvent configurer.
5. Il active modules : Summary, Raid Ops, Content Alerts.
6. Il voit les permissions et quotas.
7. Il teste un resume.
8. Il obtient un statut clair.

### Experience membre ideale

- Le membre tape `/summary last 24h` ou mentionne Galactia.
- Le bot repond en thread avec un resume court.
- Les decisions et actions sont visibles immediatement.
- Le membre peut demander plus de details.
- Le bot ne pollue pas le salon.

### Experience officier ideale

- Avant raid, Galactia produit une checklist : absents, roster incomplet, points WowAudit, liens logs importants.
- Apres raid, Galactia resume les points discutes et actions.
- Pendant la semaine, Galactia rappelle les actions assignees.
- Les decisions restent recherchables.

### Architecture cible ideale

- Bot Discord stateless autant que possible.
- DB relationnelle multi-guild.
- Worker jobs separe.
- Redis/cache pour locks/cooldowns.
- AI gateway avec quotas, prompts versionnes et structured outputs.
- Integrations externes isolees.
- Observabilite et support admin.
- Tests et CI obligatoires.

---

## 12. Conclusion

Galactia a une bonne intuition : Discord est le vrai systeme nerveux des guildes, mais il est chaotique. L'IA peut aider, a condition d'etre au service de workflows concrets.

Le projet doit eviter de devenir une collection de modules impressionnants mais moyens. La bonne trajectoire est plus exigeante et plus simple :

1. Faire un resume de salon excellent.
2. Transformer les resumes en decisions et actions.
3. Ajouter une base multi-guild fiable.
4. Integrer les donnees WoW uniquement quand elles produisent des recommandations sourcees et utiles.
5. Construire un produit d'officiers/raid leads, pas un jouet IA generaliste.

Le depot actuel est un bon prototype prive. Il n'est pas encore un produit public. La prochaine etape n'est pas d'ajouter dix features : c'est de durcir le socle, clarifier la promesse, et prouver une valeur recurrente sur les guildes WoW/MMO.
