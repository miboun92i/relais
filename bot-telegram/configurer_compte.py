"""Créer ou remplacer le compte du panel, uniquement depuis le serveur."""
import getpass
import json
import os
from pathlib import Path
from auth import create_account


def main():
    os.umask(0o077)
    path = Path(__file__).resolve().parent / 'panel-account.json'
    print('Configuration du compte privé Relais. Aucun mot de passe ne sera affiché.')
    if path.exists():
        print('Le compte existant sera remplacé. Redémarrez ensuite le programme pour fermer les anciennes sessions.')
    username = input('Nom d’utilisateur : ').strip()
    password = getpass.getpass('Mot de passe (15 caractères minimum) : ')
    if password != getpass.getpass('Répétez le mot de passe : '):
        raise SystemExit('Les mots de passe ne correspondent pas. Aucun changement enregistré.')
    try:
        account = create_account(username, password)
    except ValueError as error:
        raise SystemExit(str(error))
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(account, indent=2), encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)
    print('Compte enregistré. Le fichier contient une empreinte du mot de passe, pas le mot de passe lui-même.')
    print('Démarrez ou redémarrez Relais pour utiliser ce compte.')


if __name__ == '__main__':
    main()
