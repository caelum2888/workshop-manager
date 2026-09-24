"""Cria o primeiro administrador sem usar credenciais padrão.

Uso:
    python scripts/create_admin.py --name "Coordenação" --email admin@exemplo.org
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db  # noqa: E402
from app.services.auth import create_user  # noqa: E402
from app.services.errors import ServiceError  # noqa: E402
from app.services.organizations import get_or_create_default_organization  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Senha inicial (mínimo 8 caracteres): ")
    confirmation = getpass.getpass("Repita a senha: ")
    if password != confirmation:
        print("As senhas não conferem.", file=sys.stderr)
        return 2
    init_db()
    try:
        with SessionLocal() as db:
            organization = get_or_create_default_organization(db)  # explícito: primeiro admin = organização inicial
            user = create_user(
                db,
                {
                    "name": args.name,
                    "email": args.email,
                    "password": password,
                    "role": "admin",
                    "organization_id": organization.id,
                },
            )
    except ServiceError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(f"Administrador criado: {user['email']} (organização {user['organization_name']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
