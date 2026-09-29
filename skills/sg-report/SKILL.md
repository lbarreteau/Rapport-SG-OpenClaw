---
name: sg-report
description: Rapport hebdo des comptes Société Générale (soldes, dépenses, alertes) via Open Banking. Aussi pour les questions de budget.
metadata: {"openclaw":{"requires":{"bins":["python3"]},"os":["linux","darwin"]}}
---

# Rapport Société Générale

Ce skill lit **en lecture seule** les comptes Société Générale de l'utilisateur (consentement Open Banking DSP2 via Enable Banking) et analyse ses dépenses. Tout passe par deux scripts du dépôt, deux dossiers au-dessus de ce skill :

- `{baseDir}/../../scripts/run_weekly_report.sh` : synchronise les comptes et affiche le rapport de la semaine, en Markdown prêt à envoyer.
- `{baseDir}/../../scripts/sg-report <commande>` : les autres commandes (`status`, `report`, `link`).

Lance ces scripts directement, sans `bash` devant : ce sont ces chemins qui sont autorisés à s'exécuter sans validation.

## Rapport hebdomadaire (automatisation du dimanche ou « /sg-report »)

1. Exécute `{baseDir}/../../scripts/run_weekly_report.sh`.
2. Si la commande réussit, ta réponse est **exactement** sa sortie standard : pas de résumé, pas de reformulation, ni introduction ni conclusion. Elle est transmise telle quelle sur Telegram.
3. Si elle échoue, explique le problème en une ou deux phrases à partir de la ligne `Erreur : …`, puis propose la suite :
   - code 2 (accès absent ou expiré, configuration) : propose de relier ou renouveler l'accès (section plus bas), ou indique le réglage `.env` en cause ;
   - code 3 (banque ou Enable Banking indisponible) : propose de réessayer plus tard ;
   - code 4 (notification Home Assistant refusée) : le rapport a quand même été affiché ; transmets-le, puis signale le problème de notification en une phrase.

Pour une démonstration sans banque (données fictives), ajoute `--mock` à la commande.

## Questions ponctuelles sur le budget

Exemples : « combien en restaurants ce mois-ci ? », « mes plus grosses dépenses de la semaine ? ».

1. Exécute `{baseDir}/../../scripts/sg-report report --days 30 --format json --fetch`. Adapte `--days` (1 à 44) et `--end AAAA-MM-JJ` pour une autre période. Retire `--fetch` si une synchronisation a eu lieu dans l'heure : la banque limite le nombre d'accès quotidiens.
2. Réponds uniquement à partir de ce JSON : `current` (totaux, `categories`, `top_merchants`), `previous` (période précédente, peut valoir `null`), `transactions` (détail catégorisé), `warnings` (à mentionner s'ils affectent la réponse).
3. Écris les montants au format français (1 234,56 €).

## Relier ou renouveler l'accès bancaire

L'accès dure jusqu'à 180 jours ; le rapport prévient 14 jours avant l'expiration.

1. Exécute `{baseDir}/../../scripts/sg-report link` et envoie le lien affiché à l'utilisateur.
2. Demande-lui de valider dans l'Appli SG, puis de te renvoyer l'adresse complète de la page atteinte ensuite. Elle contient `code=` ; la page elle-même peut afficher une erreur, c'est normal.
3. Exécute `{baseDir}/../../scripts/sg-report link --callback-url "<adresse reçue>"` et confirme les comptes trouvés.
4. Lance ensuite le rapport hebdomadaire.

État de l'accès et de la dernière synchronisation : `{baseDir}/../../scripts/sg-report status`.

## Règles

- Lecture seule : aucun virement, aucune connexion au site de la banque, aucune autre commande que celles de ce skill.
- N'affiche jamais d'IBAN complet, de jeton, ni le contenu de `.env` ou du dossier `keys/`.
- Ne modifie pas `.env`, `data/` ni `keys/` sans demande explicite de l'utilisateur.
