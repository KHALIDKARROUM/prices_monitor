# Déploiement et opérations

## Déploiement

Le démarrage importe aussi `data/processed/bitumen_index.csv` dans les indicateurs BLS, de façon idempotente. `PRICE_MONITOR_BITUMEN_DATA` permet de changer ce chemin. La migration `009` préserve les anciennes alertes de prix et ajoute une base distincte pour les seuils d'indice bitume, sur SQLite et MySQL. La collecte bitume se replie sur le relais FRED de WPU058 lorsque BLS est limité ou indisponible. Les prix et budgets fournisseur restent en USD/tonne.

Les contrôles BLS directs sont espacés de six heures. Un quota journalier atteint impose une pause de 24 heures, et une autre erreur directe une pause d'une heure. Entre les contrôles, l'application interroge le relais officiel FRED ; aucune date source ni heure de dernier accès réussi n'est inventée. Cette pause est conservée en mémoire pendant la vie du processus. Les connecteurs FRED, EIA et Alpha Vantage sélectionnent la dernière valeur finie et positive, indépendamment de l'ordre des lignes. EIA demande explicitement les dates les plus récentes.

Le déploiement recommandé utilise `Dockerfile` et `docker-compose.yml`. Le service écoute sur le port 8080 dans le conteneur et doit recevoir une valeur longue pour `PRICE_MONITOR_API_KEY`. Le compose fourni utilise MySQL 8.4 avec un volume persistant et attend que la sonde MySQL soit saine avant de démarrer l'application.

```powershell
$env:PRICE_MONITOR_API_KEY = "change-me-with-a-long-random-value"
$env:MYSQL_PASSWORD = "change-me"
$env:MYSQL_ROOT_PASSWORD = "change-me-too"
docker compose up --build -d
```

SQLite reste adapté au développement local. Le démarrage initialise le schéma et applique automatiquement les migrations non encore enregistrées. La commande explicite `price-monitor-migrate` est disponible pour les déploiements qui préfèrent séparer cette étape.

## Sondes et monitoring

- `/health/live` ou `/healthz` vérifie uniquement que le processus répond ; elle convient à la liveness probe Docker/Kubernetes.
- `/health/ready` ou `/health` vérifie la connexion à la base ; un échec retourne HTTP 503.
- `/metrics` expose des compteurs Prometheus sans inclure de clé API ni de données de prix.
- La console annonce la disponibilité une seule fois au démarrage, puis à chaque changement : `no collections , try again` ou `the website is up to date`. Les diagnostics restent au format JSON ; les requêtes HTTP et les détails de collecte sont au niveau DEBUG. Le niveau se règle avec `PRICE_MONITOR_LOG_LEVEL`.

Les métriques et les sondes devraient rester accessibles uniquement au réseau de supervision dans un déploiement public. Le reverse proxy peut donc filtrer `/metrics` et les endpoints de santé selon le réseau source.

## Sauvegarde et restauration

La sauvegarde doit être exécutée quotidiennement, conservée hors de l'hôte de production et testée par une restauration mensuelle.

- SQLite : `price-monitor-backup` utilise l'API online backup de SQLite et produit un fichier cohérent même lorsque le service fonctionne.
- MySQL : `price-monitor-backup` appelle `mysqldump` avec `--single-transaction`, les routines et les événements. Le client `mysqldump` doit être installé sur le runner de sauvegarde ; le mot de passe passe par `MYSQL_PWD` et non par la ligne de commande.
- Rétention indicative : 7 sauvegardes quotidiennes, 4 hebdomadaires et 12 mensuelles, avec chiffrement et copie hors site gérés par l'infrastructure.
- MySQL doit être configuré avec des binlogs conservés pour permettre une restauration à un instant donné. Les volumes Docker ne remplacent pas les sauvegardes.

Exemple SQLite :

```powershell
price-monitor-backup --destination backups\price-monitor-$(Get-Date -Format yyyyMMdd).db --retention-days 30
```

Restauration SQLite : arrêter l'application, vérifier l'intégrité avec `PRAGMA integrity_check`, remplacer le fichier de base par une copie validée, puis redémarrer. Restauration MySQL : charger le dump dans une base de restauration, vérifier les tables et les volumes de données, puis basculer selon la procédure de changement de l'équipe.

## Migrations

La migration `010_instrument_histories` ajoute l'identité d'instrument aux observations et règles sur SQLite et MySQL. Elle conserve les IDs et les relevés, recalcule les variations par instrument et fournisseur et permet des seuils distincts pour le spot et les contrats à terme. Les anciennes règles de prix Brent/gasoil sont affectées au spot; les règles `stale` restent affectées aux flux en direct. Les anciens états `price:brent:*` et `price:gasoil:*` sont supprimés, car ils mélangeaient plusieurs instruments; leurs nouveaux états sont évalués à la prochaine collecte. Les états de fraîcheur, fournisseurs et indice bitume sont conservés.

Les migrations SQLite versionnées sont dans `price_monitor/migrations/`. Les migrations MySQL sont dans `sql/mysql_migrations/`. Elles sont idempotentes au niveau du runner et leurs versions sont enregistrées dans `schema_migrations`. Une migration doit être appliquée en CI sur une base de test avant déploiement ; ne pas modifier une migration déjà appliquée, ajouter une nouvelle version.

## Notifications

Les notifications sont optionnelles et ne bloquent jamais une collecte. Configurez au moins un canal avant de renseigner des seuils :

- `PRICE_MONITOR_ALERT_THRESHOLDS` : objet JSON, par exemple `{"brent":{"above":90,"below":70},"gasoil":{"above":3.5}}` ;
- `PRICE_MONITOR_ALERT_WEBHOOK_URL` : webhook Teams entrant ou endpoint générique JSON ;
- `PRICE_MONITOR_ALERT_EMAIL_TO` et les paramètres `PRICE_MONITOR_ALERT_SMTP_*` : livraison par email.
- `PRICE_MONITOR_DIGEST_EMAIL_TO` et `PRICE_MONITOR_DIGEST_INTERVAL_HOURS=5` : bilan des collectes envoyé toutes les cinq heures pendant l'exécution, avec les mêmes paramètres SMTP.

Le système conserve l'état des alertes dans `alert_states`. Il notifie les franchissements, les rétablissements, les échecs de source et les sources devenues obsolètes, sans répéter une alerte tant que la condition reste inchangée.

Les règles créées dans la section « Alertes de prix » du tableau de bord sont stockées dans `alert_rules`. Le canal choisi par règle limite la livraison au webhook, à l'email ou aux deux. Une règle en pause continue d'être évaluée afin que son état reste visible, mais aucune notification ne lui est envoyée. Les règles enregistrées dans la base prennent le relais de la configuration JSON d'environnement ; celle-ci reste disponible comme secours pour une première installation.
