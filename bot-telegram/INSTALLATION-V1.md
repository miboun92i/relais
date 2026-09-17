# V1 commerciale — exploitation par Jordan

Cette édition est hébergée et administrée par le propriétaire. Un service Railway,
un volume `/data` exclusif et un compte Telegram par installation. Une licence
avec N comptes réserve au maximum N installations actives auprès du serveur de
licences. Les clients disposent du panel, pas de l'accès Railway ni du code serveur.
Ne pas vendre cette édition comme une protection anticopie de code distribué.

## Serveur propriétaire (une fois)

Déployer `bot-telegram` avec son Dockerfile, commande `python license_server.py`,
un volume `/data`, une seule réplique, vérification HTTP `/health`, port 8787,
domaine HTTPS. Fournir `DATA_DIR=/data`, `PORT=8787`,
`PANEL_BOOTSTRAP_USERNAME`, `PANEL_BOOTSTRAP_PASSWORD` (15 caractères minimum),
`LICENSE_PRIVATE_KEY` et `LICENSE_PUBLIC_KEY` (Ed25519 brut, base64url).
La clé privée reste exclusivement dans les secrets de ce service.
Se connecter au domaine de ce service pour créer les licences, les révoquer ou
libérer une installation. Toute nouvelle licence a une expiration obligatoire.
Les changements et réservations sont enregistrés dans `/data/licenses.sqlite3`.
Sauvegarder ce volume et les clés avec les outils privés de Railway et un stockage
chiffré du propriétaire. La perte de la clé privée empêche de signer les licences ;
sa compromission impose une rotation de la clé publique de toutes les installations.
Ne jamais placer de clés, mots de passe ou sessions Telegram dans Git.

## Installation de chaque client

1. Créer une licence dans l'administration, avec le nombre de comptes et la durée
   voulus. Conserver le jeton dans un gestionnaire de mots de passe.
2. Créer un service Railway depuis cette version du dépôt, répertoire
   `/bot-telegram`, Dockerfile fourni, commande `python run_commercial.py`, une
   réplique, `/health`, redémarrage sur erreur. Ajouter un **nouveau** volume à
   `/data`, jamais celui d'un autre client ou du bot principal.
3. Configurer les variables privées :
   - `DATA_DIR=/data`, `HOST=0.0.0.0`, `PORT=8787`
   - `COMMERCIAL_TEST_MODE=0`, `LICENSE_REQUIRED=1`, `LICENSE_BIND_INSTALLATION=1`
   - `LICENSE_TOKEN`, `LICENSE_PUBLIC_KEY`, `LICENSE_SERVER_URL=https://...`
   - `PANEL_BOOTSTRAP_USERNAME`, `PANEL_BOOTSTRAP_PASSWORD`
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
   - `AI_PROVIDER`, le modèle et la clé correspondants (`OPENAI_MODEL` et
     `OPENAI_API_KEY` pour OpenAI), selon le fournisseur déjà retenu.
   - `PANEL_ORIGINS` : origine HTTPS exacte du panel client.
4. Déployer le panel depuis `/panel-telegram/dist`, avec son Dockerfile,
   `PORT=8787` et un domaine HTTPS. Le même panel statique peut être partagé ; les
   accès et données restent dans chaque serveur client.
5. Ouvrir le panel, renseigner l'adresse HTTPS du serveur client et les identifiants
   d'installation. Saisir le numéro Telegram, le code puis la 2FA si demandée.
   Ces codes sont saisis uniquement dans le panel, jamais dans une conversation.
6. Renseigner consignes, catalogue, FAQ, plafond, puis activer l'IA volontairement.
   Le premier démarrage est en pause.
7. Valider avec un contact de test : message reçu, réponse correcte, prise en main
   manuelle, pause/reprise, puis redéploiement et conservation du compte, de
   l'historique et des réglages. Un `/health` vert ne remplace pas ce test.
8. Après le premier démarrage réussi, retirer `PANEL_BOOTSTRAP_PASSWORD` de la
   configuration Railway : le compte haché est conservé dans le volume. Pour une
   réinitialisation, utiliser `configurer_compte.py` dans l'installation concernée.

## Renouvellement

Le bouton « Renouveler 30 jours » prolonge la licence et affiche un nouveau jeton.
Remplacer `LICENSE_TOKEN` dans chaque service de cette licence puis redéployer.
L'identité de licence et ses installations restent identiques : les sessions et
conversations sont conservées. L'ancien jeton est refusé après renouvellement ;
prévoir cette opération avec le redéploiement pour limiter l'interruption. Une
licence révoquée ne peut pas être renouvelée.

## Contrôles et comportements

- Signature Ed25519, expiration vérifiée avant les générations et envois.
- Autorisation centrale signée valable au plus 90 secondes, renouvelée toutes les
  25 secondes. Révocation normalement prise en compte au renouvellement suivant,
  au plus 90 secondes après la dernière autorisation en cas de panne réseau.
  Aucun envoi déjà accepté par Telegram ne peut être annulé.
- Panne de l'autorité : les envois s'arrêtent à l'expiration de l'autorisation.
  Le panel permet encore de se connecter et de voir le statut. Reprise après
  rétablissement de l'autorité, sans effacer les réglages enregistrés.
- Un emplacement réserve un compte même avant sa connexion Telegram. Désactiver
  une installation dans l'administration libère cet emplacement et bloque
  définitivement cet identifiant. Pour remplacer un compte, créer un service et
  un volume vierges ; ne pas réutiliser l'historique d'un autre compte.
- Identité du client et identité Telegram liées au volume ; verrou local contre
  deux processus concurrents. Une seule réplique Railway obligatoire.
- Arrêt SIGTERM propre ; SQLite et session Telethon persistent sur `/data`.
  Une reconnexion au panel après redémarrage est normale (jetons en mémoire).
- Les messages arrivés pendant un arrêt complet ne sont pas rejoués : comportement
  conservé du moteur existant. La réception reprend pour les nouveaux messages.
- Les sauvegardes contiennent des conversations et une session donnant accès à
  Telegram : chiffrement et accès propriétaire uniquement.

## Livraison et retour arrière

Les dépendances Python sont fixées dans `requirements.lock`. Le Dockerfile utilise
Python 3.13. Les mises à jour doivent être validées dans une nouvelle installation
avant de changer le principal. Garder la révision Git et le déploiement précédent.
Avant toute migration client, sauvegarder son volume. Ne jamais lancer la même
session Telegram dans deux services. Ne pas supprimer un volume lors d'un retour
arrière. La branche `main` et les services historiques ne font pas partie de cette
livraison de validation.

## Tests

`python -m pip install -r requirements.lock` puis `python -m pytest -q`.
Les tests automatisés utilisent des transports Telegram et IA simulés ; le test
client zéro vérifie la vraie persistance SQLite/Telethon avec une clé de test.
Il ne prouve pas l'autorisation Telegram réelle ni une réponse réelle du fournisseur.
