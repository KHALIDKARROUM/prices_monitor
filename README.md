# Price Monitor

Application de veille des prix du Brent, du gasoil et du bitume. Elle rassemble les historiques de marché, les cotations disponibles, les alertes et les devis fournisseurs dans un tableau de bord en français.

Le projet fonctionne en local avec **Python 3.11+ et SQLite**, sans serveur de base de données à installer. MySQL et Docker sont disponibles pour les déploiements partagés.

## Fonctionnalités

- Tableau de bord responsive, thèmes clair et sombre, graphiques et historique filtrable.
- Collectes en arrière-plan, calendrier par fournisseur, suivi des quotas et reprise après erreur.
- Comparaison des sources lorsque les instruments, dates, devises et unités correspondent.
- Prévisions du Brent et du gasoil, projections mensuelles de l’indice bitume et suivi des performances.
- Devis versionnés, pièces justificatives, estimation du coût rendu et suivi des budgets.
- Alertes de prix et de fraîcheur, notifications par email ou webhook, bilan périodique optionnel.
- Exports CSV/Excel, API HTTP et comptes individuels avec rôles lecteur, éditeur et administrateur.
- Workflow d’analyse reproductible avec notebook, rapports et séparation chronologique des données.

## Installation et démarrage

### Windows — PowerShell

Depuis la racine du projet :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m price_monitor.app
```

Ouvrez ensuite [http://127.0.0.1:8080](http://127.0.0.1:8080). Aucune activation de l’environnement virtuel n’est nécessaire avec ces commandes. Pour les démarrages suivants, seule la dernière commande est requise.

### Linux, macOS ou Git Bash

```bash
bash run.sh
```

Le script crée `.venv` si nécessaire, installe les dépendances manquantes et lance le serveur. Vous pouvez aussi installer le paquet avec `python -m pip install -e .`, puis lancer `python -m price_monitor.app` dans votre environnement virtuel.

### Premier lancement

Le serveur initialise la base et applique les migrations automatiquement. Les historiques publics fournis dans `data/processed/` sont importés de façon idempotente ; les collectes réseau démarrent en arrière-plan. Les données sauvegardées restent consultables lorsque les fournisseurs sont indisponibles. Le tableau de bord indique leur ancienneté.

Les graphiques utilisent une copie locale de Chart.js : aucun CDN n’est nécessaire pour afficher les données déjà enregistrées. Arrêtez le serveur avec `Ctrl+C`.

## Configuration

L’application charge automatiquement le fichier `.env` à la racine du dépôt. Les variables déjà définies dans le processus sont prioritaires. Modifiez votre configuration locale, puis redémarrez l’application.

Le fichier [`.env.example`](.env.example) contient les options documentées, avec des champs de secrets vides.

| Variable | Valeur par défaut / usage |
| --- | --- |
| `PRICE_MONITOR_HOST` / `PRICE_MONITOR_PORT` | `127.0.0.1` / `8080` |
| `PRICE_MONITOR_ENV` | `development` pour l’usage local |
| `PRICE_MONITOR_DB_BACKEND` | `sqlite` ; `mysql` pour MySQL |
| `PRICE_MONITOR_DB` | Base SQLite locale, par défaut `var/price_monitor.db` dans le dépôt |
| `PRICE_MONITOR_LIVE_QUOTES_ENABLED` | `true` ; `false` pour désactiver les cotations Yahoo |
| `FRED_API_KEY`, `EIA_API_KEY`, `ALPHA_VANTAGE_API_KEY` | Clés optionnelles des fournisseurs correspondants |
| `PRICE_MONITOR_TEAM_MODE` | `false` en local ; requis pour un accès partagé ou public |
| `PRICE_MONITOR_ALERT_SMTP_*` | Configuration SMTP pour les notifications optionnelles |
| `PRICE_MONITOR_ALERT_EMAIL_TO` | Destinataires des alertes |
| `PRICE_MONITOR_DIGEST_EMAIL_TO` | Destinataires du bilan des collectes ; vide pour le désactiver |
| `PRICE_MONITOR_DIGEST_INTERVAL_HOURS` | `5` heures pendant l’exécution du serveur |

**Confidentialité :** conservez les identifiants, clés API, mots de passe SMTP et webhooks dans votre `.env` local ou dans les secrets de votre environnement de déploiement. `.gitignore` et `.dockerignore` excluent les fichiers d’environnement, clés privées et données d’exécution usuelles. Seul le modèle `.env.example`, sans secrets, doit être versionné. Ne publiez pas les bases, sauvegardes, exports ni justificatifs fournisseurs ; vérifiez toujours les fichiers sélectionnés avant un commit.

### MySQL

Dans `.env`, définissez `PRICE_MONITOR_DB_BACKEND=mysql`, puis renseignez `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER` et `MYSQL_PASSWORD`. Utilisez un compte dédié à l’application. Le serveur crée la base si le compte possède ce droit, puis applique le schéma et les migrations.

Les migrations SQLite se trouvent dans `src/price_monitor/migrations/` et les migrations MySQL dans `sql/mysql_migrations/`. Des copies MySQL sont incluses dans le paquet Python pour son installation autonome.

### Données fictives

Pour essayer l’interface avec des données fictives, utilisez une base distincte :

```powershell
$env:PRICE_MONITOR_DEMO = "true"
$env:PRICE_MONITOR_DB_BACKEND = "sqlite"
$env:PRICE_MONITOR_DB = "$PWD\var\demo.db"
.\.venv\Scripts\python.exe -m price_monitor.app
```

Ces variables restent actives dans ce terminal. Pour revenir aux données réelles, fermez-le ou supprimez ces trois variables avant de redémarrer. N’activez pas ce mode sur votre base habituelle.

## Sources et unités

Les types de données sont conservés séparément pour éviter des comparaisons trompeuses :

| Donnée | Source / instrument | Unité et interprétation |
| --- | --- | --- |
| Brent spot | EIA/FRED `DCOILBRENTEU`, EIA directe, Alpha Vantage optionnel | USD/baril ; benchmark historique |
| Diesel spot | EIA/FRED `DDFUELNYH`, EIA directe | USD/gallon ; prix FOB New York Harbor |
| Brent et proxy gasoil en direct | Yahoo `BZ=F` et `HO=F` | Contrats à terme ; `HO=F` est un proxy heating oil, distinct du diesel spot |
| Indice bitume/asphalte | BLS/FRED `WPU058` | Indice mensuel, base décembre 1984 = 100 |
| Prix fournisseur bitume | Devis, commandes ou factures saisis dans l’application | USD/tonne, avec les conditions commerciales |

L’indice bitume n’est jamais converti en prix USD/tonne. Les contrats à terme, prix spot et devis ne sont pas des offres commerciales interchangeables. Les prévisions affichent leurs limites et les résultats de validation disponibles.

Le dépôt contient un historique public de cinq ans pour le Brent et le gasoil ainsi que 60 observations mensuelles de l’indice bitume. La provenance et les fichiers sont décrits dans [`data/README.md`](data/README.md). Pour actualiser les sources et reconstruire les fichiers :

```powershell
.\.venv\Scripts\python.exe scripts/prepare_market_data.py --years 5 --refresh
```

Cette commande nécessite un accès Internet. Elle met à jour les fichiers du snapshot ; l’application les synchronise au prochain démarrage.

### Cadences de collecte

| Fournisseur | Intervalle par défaut |
| --- | --- |
| FRED et EIA | 6 heures |
| Alpha Vantage | 12 heures |
| BLS / relais FRED de l’indice | 24 heures |
| Yahoo | 1 minute pendant les séances configurées |

Les variables `PRICE_MONITOR_<FOURNISSEUR>_INTERVAL_MINUTES` permettent de modifier ces cadences. Le calendrier, les quotas et les délais de reprise sont persistants ; une collecte manuelle respecte également ces limites. La section **Activité** affiche les prochaines échéances et les motifs de report. Les fermetures exceptionnelles se configurent avec `PRICE_MONITOR_MARKET_CLOSURES`.

## Analyse des données

Installez les dépendances d’analyse, puis générez les rapports :

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[analysis]"
.\.venv\Scripts\python.exe scripts/run_data_science.py
```

Le workflow couvre la qualité des données, l’analyse exploratoire, les variables explicatives, la validation chronologique, la calibration et la comparaison aux prévisions naïves. Les modèles sauvegardés sont optionnels avec `--save-models`.

- [Notebook du workflow](notebooks/data_workflow.ipynb)
- [Rapport Brent et gasoil](reports/data_workflow/report.md)
- [Rapport de l’indice bitume](reports/data_workflow/bitumen_report.md)

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,analysis]"
.\.venv\Scripts\python.exe -m pytest
node --test tests/*.cjs
```

Node.js est requis uniquement pour les tests d’interface. Les tests couvrent notamment la validation des prix, les migrations, les droits d’accès, les devis, les alertes, les collectes et les prévisions. Les tests MySQL et les parcours navigateur sont optionnels.

Pour les parcours Chromium :

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
$env:RUN_BROWSER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/test_browser_journeys.py
```

Pour MySQL, utilisez exclusivement un serveur de test : `PRICE_MONITOR_TEST_MYSQL=1` active les contrats métier et `RUN_MYSQL_TESTS=1` le contrôle du schéma. Les contrats créent puis suppriment leurs bases temporaires. La [CI](.github/workflows/ci.yml) vérifie Python 3.11/3.12, l’interface, MySQL 8.4, Chromium et la construction du paquet et du conteneur.

## Structure du projet

| Chemin | Contenu |
| --- | --- |
| `src/price_monitor/` | Serveur HTTP, configuration et logique métier |
| `src/price_monitor/services/` | Collectes, planification, alertes, prévisions et exports |
| `src/price_monitor/templates/` / `src/price_monitor/static/` | Interface et ressources locales |
| `src/price_monitor/analysis/` | Pipeline d’analyse et évaluation des modèles |
| `data/` | Sources publiques et snapshots normalisés |
| `sql/` | Schéma et migrations MySQL |
| `scripts/` / `notebooks/` | Préparation des données et analyses reproductibles |
| `tests/` | Tests Python, interface et navigateur |
| `var/` | Données locales d’exécution : base SQLite, journaux et sauvegardes ; ignorées par Git |
| `docs/operations.md` | Déploiement, comptes, sauvegardes et exploitation |

## API et déploiement

Une fois le serveur lancé, consultez la [documentation API](http://127.0.0.1:8080/docs) ou le [schéma OpenAPI](http://127.0.0.1:8080/openapi.json). Les principales routes sont `/api/dashboard`, `/api/history`, `/api/forecast`, `/api/quotes`, `/api/alerts` et `/api/collection-schedules`. Les exports sont disponibles via `/export.csv` et `/export.xlsx` ; les permissions suivent le mode d’accès configuré.

Le projet fournit un `Dockerfile` et un `docker-compose.yml` avec MySQL 8.4. Un accès partagé ou public exige le mode équipe, un premier administrateur, une origine HTTPS et des cookies sécurisés. Le compose publie le serveur sur la boucle locale pour un reverse proxy TLS.

La commande `price-monitor-users bootstrap --username votre.identifiant` crée le premier administrateur et demande son mot de passe dans le terminal. Les commandes `price-monitor-migrate` et `price-monitor-backup` permettent de gérer les migrations et les sauvegardes après installation du paquet. Les sondes sont `/health/live` et `/health/ready` ; `/metrics` est réservé aux administrateurs.

Suivez le [guide de déploiement et d’exploitation](docs/operations.md) pour la configuration complète, Docker, les rôles, la supervision et la restauration des sauvegardes.
