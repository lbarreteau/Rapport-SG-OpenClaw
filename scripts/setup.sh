#!/usr/bin/env bash
# Prépare le dépôt après un clonage : .env, dossiers privés, droits d'exécution, prérequis.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

chmod +x scripts/run_weekly_report.sh scripts/sg-report scripts/setup.sh
mkdir -p keys data
chmod 700 keys data

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Fichier .env créé à partir de .env.example : complétez-le."
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 est introuvable." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 ou plus récent est requis (actuel : $(python3 -V 2>&1))." >&2
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1 && ! python3 -c 'import cryptography' 2>/dev/null; then
  echo "Il faut la commande openssl ou le module Python cryptography pour signer les requêtes Enable Banking." >&2
  exit 1
fi

echo "Prérequis OK. Essai sans banque : scripts/sg-report --mock run"
