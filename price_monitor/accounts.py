"""Local operator CLI for first administrator setup and credential recovery."""
from __future__ import annotations

import argparse
from getpass import getpass

from .database import create_database


def main():
    parser = argparse.ArgumentParser(description="Comptes individuels Price Monitor")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="Créer le premier administrateur")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--name", default="")
    reset = commands.add_parser("reset-password", help="Réinitialiser un compte existant en accès local au serveur")
    reset.add_argument("--username", required=True)
    args = parser.parse_args()
    database = create_database()
    password = getpass("Nouveau mot de passe (12 caractères minimum) : ")
    if password != getpass("Confirmez le mot de passe : "):
        parser.error("Les mots de passe ne correspondent pas.")
    actor = {"id": None, "username": "server-operator"}
    if args.command == "bootstrap":
        payload = {"username": args.username, "full_name": args.name or args.username, "role": "admin", "password": password}
        user = database.audited_write(actor, "user.bootstrapped", "account", lambda: database.create_team_user(payload, bootstrap=True))
    else:
        user = next((row for row in database.team_users() if row["username"] == args.username.strip().lower()), None)
        if user is None:
            parser.error("Compte introuvable.")
        user = database.audited_write(actor, "password.reset_by_operator", "account", lambda: database.update_team_user(user["id"], {"version": user["version"], "password": password}), user["id"])
    print(f"Compte {user['username']} enregistré. Aucun mot de passe n'est écrit dans les journaux.")


if __name__ == "__main__":
    main()
