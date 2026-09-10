# Relais — panel Telegram

Interface statique, hébergée avec Sites. L'API Telegram et la base privée tournent dans le programme Python séparé, livré dans le dossier bot-telegram. Aucun secret ni message réel n'est embarqué dans ce site.

La démonstration est en mémoire et ne contacte aucun fournisseur IA. La page de connexion demande un nom d’utilisateur et un mot de passe, vérifiés par le serveur Python. Celui-ci stocke une empreinte scrypt salée, limite les tentatives et émet un jeton de session temporaire conservé seulement en mémoire dans l’onglet. HTTPS et une liste d’origines autorisées protègent les échanges. Toutes les routes de données et de gestion exigent une session valide ; la déconnexion la révoque. Les requêtes sortantes ne transmettent pas de cookies. L'interface ne bascule pas silencieusement en démo après une panne serveur.

Validation : syntaxe JavaScript et références des ressources vérifiées. Les routes du serveur et les courses entre IA et reprise manuelle sont couvertes par les tests Python du programme. Pas de QA navigateur demandée. WebMCP est facultatif, détecté à l'exécution ; pas de contexte compatible disponible pour sa validation.
