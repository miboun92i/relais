# Relais — assistant et panel Telegram

Pour déployer la V1 commerciale hébergée, suivre [INSTALLATION-V1.md](INSTALLATION-V1.md).
Les sections historiques ci-dessous décrivent aussi le mode local initial.

Aperçu local : http://127.0.0.1:8765

Adresse prévue après publication privée : https://relais-telegram-miboun.chummy-shell-0459.chatgpt.site

La publication est en attente d’autorisation explicite pour envoyer le code du panel vers Sites. Le programme Telegram n’a pas encore été installé sur un serveur.

Le panel est conçu pour être hébergé séparément du programme Telegram. Sa démonstration utilise des clients fictifs ; elle n'envoie rien et ses modifications disparaissent en rechargeant la page. Une fois connecté au programme, le panel affiche les vraies données du serveur. L'accès au site est actuellement privé pour son propriétaire ; donner l'accès à une autre personne demande un partage du site.

## Où choisir votre identifiant et votre mot de passe

Sur Mac, double-cliquer sur `Configurer-mon-acces.command` dans ce dossier. Il ouvre un petit assistant dans Terminal qui demande votre nom d'utilisateur, votre mot de passe et sa confirmation. Le mot de passe n'apparaît pas pendant la saisie. Choisir une phrase unique de 15 caractères minimum.

Sur le serveur, lancer `.venv/bin/python configurer_compte.py` depuis le dossier du programme. La configuration est privée et locale au serveur : il n'y a pas d'inscription publique sur le site. Ne transmettez pas votre mot de passe dans une conversation.

Le fichier créé, `panel-account.json`, contient le nom d'utilisateur, un sel aléatoire et une empreinte scrypt du mot de passe. Il est exclu des sources à partager. Si le compte est configuré sur le Mac, ce fichier doit être transféré de façon privée vers le dossier du programme sur le serveur ; il ne faut pas l'envoyer à Sites. Le nom d'utilisateur et le mot de passe ne fonctionneront sur le panel qu'une fois le serveur installé et connecté.

Pour changer les identifiants ou réinitialiser un mot de passe oublié, relancer le même assistant sur le serveur, puis redémarrer Relais. Toutes les anciennes sessions sont alors invalidées. La création d'un compte remplace le compte précédent : cette version gère un compte d'administration unique.

## Ce qui est prêt

- Page de connexion avec nom d’utilisateur et mot de passe, déconnexion, expiration de session et limitation des tentatives.
- Boîte de réception, recherche, filtres « Non lus » et « À ma charge ».
- Réponses depuis le panel ; propositions IA à relire et modifier avant envoi.
- Un message envoyé depuis le téléphone met l'IA en pause pour ce client.
- Bouton « Réactiver l'IA » par client et pause générale.
- Consignes, tarifs et FAQ enregistrés sur le serveur.
- Fournisseur Ollama par défaut, ou Anthropic si souhaité.
- Historique, attribution IA/humain, pauses et compteur quotidien conservés dans SQLite.

L'étiquette « Assistant IA » n'est ajoutée que dans le panel. Le texte envoyé sur Telegram n'a aucun préfixe ni présentation automatique. Une question directe sur l’identité reçoit une réponse honnête indiquant que l’assistant automatique répond, sans passage en manuel. Ces contrôles ne garantissent pas de reconnaître toutes les formulations possibles d'un modèle ou d'un client.

## Ce qu'il reste à connecter

Il faut un serveur Linux, son domaine HTTPS, les identifiants Telegram de la propriétaire du compte et un fournisseur IA opérationnel. Aucun compte Telegram n'a été connecté pendant la création. Aucun message réel n'a été envoyé. Le serveur Telegram n'est pas hébergé par le site.

Ollama ne nécessite pas de clé Anthropic, mais le serveur qui exécute le modèle peut être payant et doit disposer de suffisamment de mémoire. Le choix du modèle dépendra du serveur disponible. Installer Ollama et un modèle adapté, puis reporter le nom exact du modèle installé dans `OLLAMA_MODEL`. Référence : https://docs.ollama.com/api/chat . Pour utiliser Anthropic à la place, régler `AI_PROVIDER=anthropic` et remplir `ANTHROPIC_API_KEY` ; son API est facturée selon l'utilisation.

## Installation sur un serveur Linux

Les commandes supposent un serveur avec Python 3, le module venv et un utilisateur dédié `relais`. Copier ce dossier dans `/opt/relais`, avec les droits d'écriture pour cet utilisateur.

```sh
cd /opt/relais
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Renseigner dans `.env` :

- `TELEGRAM_API_ID` et `TELEGRAM_API_HASH`, obtenus sur https://my.telegram.org/apps avec le compte de la propriétaire.
- `AI_PROVIDER`, `OLLAMA_URL` et `OLLAMA_MODEL` pour Ollama, ou la clé Anthropic si ce fournisseur est choisi.
- `PANEL_ORIGINS` : l'adresse exacte du panel est déjà dans l'exemple.
- Garder `HOST=127.0.0.1` : l'accès externe passe par HTTPS.

Lancer une première fois en session interactive, en tant qu'utilisateur `relais` :

```sh
.venv/bin/python main.py
```

Avant ce premier lancement, créer le compte du panel avec `.venv/bin/python configurer_compte.py`.

Au lancement de Telegram, saisir le numéro de téléphone, le code Telegram et, si demandé, le mot de passe de double authentification. Le fichier `compte.session` conserve cette connexion.

Installer un relais HTTPS sur ce serveur, par exemple Caddy. `Caddyfile.example` contient la configuration à adapter au domaine qui pointe sur le serveur. Autoriser les ports nécessaires à HTTPS ; le port 8787 reste local. Ne pas exposer l'API sans HTTPS.

Pour fonctionner en continu, adapter `relais.service.example` à l'emplacement et à l'utilisateur retenus, puis l'installer comme service systemd. **Arrêter le lancement interactif avant de démarrer le service : un seul programme doit utiliser ce compte et cette base.** Le service redémarre après une erreur.

## Connecter le panel

1. Ouvrir le site : la page « Se connecter » apparaît avant le tableau de bord.
2. Déplier « Configuration du serveur » et indiquer l'adresse HTTPS du serveur, par exemple `https://telegram.votre-domaine.fr`. Cette adresse est mémorisée dans le navigateur pour les prochaines connexions.
3. Saisir le nom d'utilisateur et le mot de passe choisis avec l'assistant de configuration.
4. Dans « Mon assistant », saisir les vraies prestations, les prix, les FAQ et les consignes.
5. Activer les réponses automatiques après vérification.

Les conversations et toutes les actions de gestion exigent une session valide côté serveur. Le simple fait de connaître l'adresse ou de modifier la page dans le navigateur ne donne pas accès aux données. La démonstration reste séparée, en mémoire, sans accès aux vraies conversations.

Le mot de passe n'est jamais enregistré par le code du navigateur. Un jeton temporaire reste en mémoire dans l'onglet ; il n'est pas placé dans le stockage du navigateur. Un rechargement demande une nouvelle connexion. Le bouton « Se déconnecter » révoque la session sur le serveur et efface les données de l'écran. En cas de panne réseau, il efface l'accès local ; la session distante expire ensuite. Les sessions expirent au bout de huit heures et sont invalidées à chaque redémarrage du serveur. Cinq connexions refusées en moins d'une minute bloquent temporairement les nouvelles tentatives sur ce compte unique.

Fermer le panel, se déconnecter ou revenir à la démonstration **n'arrête pas** le programme Telegram sur le serveur. Les clés `PANEL_TOKEN` et `panel-key.txt` de l'ancienne version ne permettent plus de s'authentifier.

Sur Mac, `Lancer.command` propose aussi la création du compte lors du premier démarrage si nécessaire. Pour un essai local, servir le dossier du panel à `http://127.0.0.1:8765` et utiliser le serveur `http://127.0.0.1:8787`.

## Reprendre la main

- Écrire dans la conversation sur Telegram ou envoyer un message depuis le panel met ce client en mode manuel.
- Le mode manuel reste actif jusqu'au clic « Réactiver l'IA », y compris après un redémarrage.
- Une réponse encore en préparation est ignorée si une reprise manuelle, une pause, un nouveau message ou un changement de consignes l'a rendue obsolète. Un envoi déjà transmis à Telegram ne peut pas être annulé rétroactivement.
- Au redémarrage, l’activation globale et les pauses manuelles enregistrées sont conservées. Le premier démarrage reste en pause, jusqu’à l’activation volontaire. Les messages reçus pendant un arrêt complet ne sont pas automatiquement rejoués par cette correction.
- Dans les « Messages enregistrés » de la propriétaire : `/ia pause`, `/ia reprendre`, `/ia statut` contrôlent l'état global.

## Limites de cette version

- Elle synchronise les nouveaux messages pendant son fonctionnement, pas tout l'historique antérieur. Le panel affiche les 200 derniers messages enregistrés par conversation. Les suppressions, modifications et statuts de lecture Telegram ne sont pas répliqués ; « Non lu » désigne la lecture dans le panel.
- Les photos, vidéos, vocaux et fichiers doivent être consultés sur Telegram et font passer le client en manuel. Les stickers de salutation et les aperçus de liens restent en automatique.
- Groupes, bots, messages de service et identifiants de `IGNORE_CHAT_IDS` sont ignorés.
- Le regroupement attend deux secondes avant de préparer une réponse. Deux générations IA au maximum s'exécutent en parallèle.
- Une panne technique IA conserve le mode automatique ; les erreurs temporaires et réponses vides sont réessayées deux fois. Après épuisement, l’erreur est signalée et un prochain message peut être traité. Seul un relais humain explicite met la conversation en manuel. Le compteur de tentatives est durable et se réinitialise chaque jour à minuit UTC. Les propositions et les erreurs comptent dans le plafond ; ce n'est pas un plafond financier.
- Les paiements, commandes et disponibilités ne sont pas vérifiés automatiquement. Les consignes demandent à l'IA de ne pas les confirmer, mais une réponse générée doit rester surveillée.
- La base contient les conversations en clair sur le serveur. Les identifiants et sessions du panel permettent de les lire et d'envoyer des messages ; la session Telegram donne accès au compte. Protéger les accès au serveur et les sauvegardes. Aucun de ces fichiers ne doit être partagé avec les sources.

## Vérifications réalisées

Tests automatisés avec Telegram et IA simulés : reprise depuis le téléphone pendant une génération, reprise depuis le panel, envoi sans doublon pour une même demande, pause puis reprise sans libérer une ancienne réponse, pause globale, reconnaissance des messages IA, rafales de messages, conservation des réglages et du plafond, authentification et origines autorisées du panel, mot de passe haché et salé, refus des mauvais identifiants, limitation des tentatives, refus des accès directs sans connexion, expiration et révocation des sessions. 17 tests automatisés passent.

```sh
.venv/bin/python -m unittest -v test_bot.py
```

Les connexions réelles Telegram/Ollama/Anthropic et l'installation du service sur un serveur restent à vérifier une fois les accès disponibles. La syntaxe du panel a été validée ; aucun test visuel de navigateur n'a été réalisé. Les outils WebMCP facultatifs sont présents, mais n'ont pas été validés dans un navigateur compatible.

Documentation de référence : https://docs.telethon.dev/en/stable/basic/updates.html et https://docs.ollama.com/api/chat . Les anciennes consignes de `personnalite.txt` sont remplacées par celles du panel.

Références pour le stockage des mots de passe : https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html et https://docs.python.org/3/library/hashlib.html#hashlib.scrypt .



## Fiabilité de la version commerciale

Le bootstrap, les deux runtimes et l’identifiant d’installation résolvent le même
dossier après chargement de `.env` : `DATA_DIR`, puis le volume Railway, puis le
dossier local hors Railway. Une installation Railway sans chemin persistant
configuré refuse de démarrer. Les identifiants panel déjà enregistrés sont
conservés ; en mode test seulement, des identifiants explicitement configurés
peuvent les resynchroniser. Aucun identifiant de secours n’est imprimé dans les logs.

Le panel distingue une connexion réseau Telegram d’un compte réellement autorisé.
Le parcours téléphone/code/2FA expire au bout de dix minutes et peut rétablir la
connexion réseau entre les étapes. `/health` vérifie la disponibilité du panel ;
il ne certifie ni la connexion du compte ni une réponse réelle du fournisseur IA.

La suite `python -m pytest -q` couvre aussi le parcours HTTP panel → connexion
Telegram simulée → DM → réponse → redémarrage. Une installation correspond à un
compte Telegram et un administrateur de panel : ce n’est pas un serveur multi-clients.
Chaque installation cliente doit disposer de son propre stockage et de sa session.
