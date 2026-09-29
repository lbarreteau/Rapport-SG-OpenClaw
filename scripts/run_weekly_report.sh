#!/usr/bin/env bash
# Rapport hebdomadaire : synchronise les comptes puis affiche le rapport Markdown.
# Si REPORT_CHANNEL=ha dans .env, envoie aussi la notification Home Assistant.
# Les options sont transmises à « sg_report run » (ex. --mock, --no-send).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export SG_REPORT_COMMAND="$ROOT/scripts/sg-report"
exec "${PYTHON:-python3}" -m sg_report run "$@"
