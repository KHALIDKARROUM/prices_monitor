# Déploiement et opérations

## Déploiement

Le démarrage importe aussi `data/processed/bitumen_index.csv` dans les indicateurs BLS, de façon idempotente. `PRICE_MONITOR_BITUMEN_DATA` permet de changer ce chemin. La migration `009` préserve les anciennes alertes de prix et ajoute une base distincte pour les seuils d'indice bitume, sur SQLite et MySQL. La collecte bitume se replie sur le relais FRED de WPU058 lorsque BLS est limité ou indisponible. Les prix et budgets fournisseur restent en USD/tonne.

Les collectes utilisent désormais un calendrier propre à chaque fournisseur : FRED/EIA toutes les six heures, Alpha Vantage toutes les douze heures, BLS/FRED une fois par jour et Yahoo chaque minute pendant les séances ouvertes. L'application sert les données sauvegardées dès son lancement, pendant la première collecte en arrière-plan. Les workers ne partagent pas leurs attentes réseau. La migration `012` conserve calendriers, budgets de requêtes et délais de quota en base, après un redémarrage comme entre instances partageant la même base.

Alpha Vantage et BLS disposent d'un plafond local de 25 tentatives HTTP sur 24 heures glissantes ; les requêtes échouées et manuelles sont incluses. Les échecs réseau appliquent un délai exponentiel et les HTTP 429/503 respectent `Retry-After`. Les avis de quota quotidiens reçus dans un JSON HTTP 200 suspendent les demandes directes pendant 24 heures. Le relais officiel FRED de WPU058 reste possible, avec une réservation FRED distincte. L'ancien garde BLS en mémoire espace encore les essais directs lors d'une collecte manuelle, mais le quota et la planification ne reposent plus sur cette mémoire. Aucune date source ni heure de dernier accès réussi n'est inventée. Les connecteurs choisissent toujours la dernière valeur finie et positive, indépendamment de l'ordre des lignes.

La page Activité et `/api/collection-schedules` montrent les prochaines échéances, quotas et reports. `/api/connectivity` agrège les fournisseurs : un échec optionnel n'efface pas la réussite des autres flux. Les sessions BZ/HO respectent les week-ends et la pause de 17 h à 18 h, heure de New York, avec changements d'heure. Maintenez les fenêtres `PRICE_MONITOR_MARKET_CLOSURES` selon le calendrier CME pour les jours fériés, clôtures anticipées et arrêts exceptionnels. Les alertes de fraîcheur sont suspendues pendant ces fenêtres sans simuler un rétablissement ; elles reprennent après le délai de grâce de leur règle. Les variables et limites sont détaillées dans `.env.example` et dans le README.

Le service HTTP utilise Waitress. Le compose fourni utilise MySQL 8.4 avec un volume persistant, attend sa sonde et publie le backend uniquement sur `127.0.0.1:8080`. Configurez un reverse proxy TLS qui transmet le nom d’hôte public et empêche l’accès direct au backend. Le premier administrateur est créé par une commande locale ; aucun compte ni mot de passe par défaut n’est fourni.

```powershell
$env:PRICE_MONITOR_PUBLIC_ORIGIN = "https://prices.example.com"
$env:MYSQL_PASSWORD = "change-me"
$env:MYSQL_ROOT_PASSWORD = "change-me-too"
docker compose build
docker compose run --rm --entrypoint price-monitor-users app bootstrap --username votre.identifiant
docker compose up --build -d
```

Remplacez le domaine et les mots de passe d’exemple. La commande demande un mot de passe individuel d’au moins douze caractères. Connectez-vous ensuite avec ce compte et créez les autres dans **Équipe**. Réutiliser `bootstrap` sur une base ayant déjà des comptes est refusé. Pour récupérer un accès depuis le serveur : `price-monitor-users reset-password --username votre.identifiant`, ou la même commande via `docker compose run --rm --entrypoint price-monitor-users app reset-password --username votre.identifiant`.

Pour un déploiement sans Docker, installez les dépendances actualisées (`python -m pip install .`), créez le premier compte avec `python -m price_monitor.accounts bootstrap --username votre.identifiant`, puis configurez `PRICE_MONITOR_ENV=production`, `PRICE_MONITOR_TEAM_MODE=true`, `PRICE_MONITOR_SECURE_COOKIES=true` et `PRICE_MONITOR_PUBLIC_ORIGIN=https://votre-domaine`. Sur un backend privé derrière TLS, `PRICE_MONITOR_URL_SCHEME=https` déclare le protocole public. Si vous utilisez les en-têtes de proxy à la place, renseignez l’IP exacte de ce proxy dans `PRICE_MONITOR_TRUSTED_PROXY` ; les en-têtes de proxy non approuvés sont supprimés. Aucun proxy joker n’est accepté. Le contrôle Host/Origin compare le domaine et le port à l’origine configurée. Le serveur refuse de démarrer sans administrateur actif ou avec une configuration publique incomplète.

Le lecteur consulte et exporte ; l’éditeur modifie et collecte ; l’administrateur gère aussi l’équipe, l’audit et les métriques. Tous les historiques, devis, budgets et exports sont privés en mode équipe. La clé API partagée ne remplace pas un compte. Pour la supervision ou une intégration, créez un jeton personnel avec le rôle requis et envoyez-le dans `Authorization: Bearer <jeton>`.

Les sessions et jetons expirent et peuvent être révoqués dans l’interface. Les rôles et désactivations sont relus à chaque requête. Les sessions navigateur demandent un jeton CSRF pour les écritures, y compris les collectes et alertes. Les cookies de session ne sont pas accessibles à JavaScript. Les limites de connexion persistantes protègent chaque nom de compte et adresse cliente. L’audit est consultable uniquement par un administrateur ; il conserve l’identité et le type d’action, sans copie de secret ni du contenu fournisseur. Les écritures métier et leur audit sont atomiques. Cet audit applicatif n’est pas une archive inviolable face à un opérateur ayant accès à la base.

SQLite reste adapté au développement local. Le démarrage initialise le schéma et applique automatiquement les migrations non encore enregistrées. La commande explicite `price-monitor-migrate` est disponible pour les déploiements qui préfèrent séparer cette étape.

## Sondes et monitoring

- `/health/live` ou `/healthz` vérifie uniquement que le processus répond ; elle convient à la liveness probe Docker/Kubernetes.
- `/health/ready` ou `/health` vérifie la connexion à la base ; un échec retourne HTTP 503.
- `/metrics` expose des compteurs Prometheus, avec une session ou un jeton administrateur, sans inclure de secret ni de données de prix.
- La console annonce la disponibilité une seule fois au démarrage, puis à chaque changement : `no collections , try again` ou `the website is up to date`. Les diagnostics restent au format JSON ; les requêtes HTTP et les détails de collecte sont au niveau DEBUG. Le niveau se règle avec `PRICE_MONITOR_LOG_LEVEL`.

Les métriques et les sondes devraient rester accessibles uniquement au réseau de supervision dans un déploiement public. Le reverse proxy peut donc filtrer `/metrics` et les endpoints de santé selon le réseau source.

## Sauvegarde et restauration

La sauvegarde doit être exécutée quotidiennement, conservée hors de l'hôte de production et testée par une restauration mensuelle. Elle contient les données commerciales, documents, empreintes de mots de passe et événements d’audit : limitez son accès et chiffrez les copies hors site. Après restauration d’une ancienne sauvegarde, supprimez les lignes `team_sessions` et `team_api_tokens` avant la remise en service pour éviter de réactiver des accès révoqués depuis cette sauvegarde.

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

La migration `013_team_access` ajoute les comptes individuels, sessions et jetons hachés, tentatives de connexion, verrou de protection du dernier administrateur et journal d’audit sur SQLite et MySQL. Elle préserve les données métier existantes ; elle ne crée pas de compte ni ne réattribue les anciennes corrections à un utilisateur. Créez le premier administrateur avant le redémarrage en mode équipe.

La migration `010_instrument_histories` ajoute l'identité d'instrument aux observations et règles sur SQLite et MySQL. Elle conserve les IDs et les relevés, recalcule les variations par instrument et fournisseur et permet des seuils distincts pour le spot et les contrats à terme. Les anciennes règles de prix Brent/gasoil sont affectées au spot; les règles `stale` restent affectées aux flux en direct. Les anciens états `price:brent:*` et `price:gasoil:*` sont supprimés, car ils mélangeaient plusieurs instruments; leurs nouveaux états sont évalués à la prochaine collecte. Les états de fraîcheur, fournisseurs et indice bitume sont conservés.

Les migrations SQLite versionnées sont dans `src/price_monitor/migrations/`. Les migrations MySQL sont dans `sql/mysql_migrations/`. Elles sont idempotentes au niveau du runner et leurs versions sont enregistrées dans `schema_migrations`. Une migration doit être appliquée en CI sur une base de test avant déploiement ; ne pas modifier une migration déjà appliquée, ajouter une nouvelle version.

## Notifications

Les notifications sont optionnelles et ne bloquent jamais une collecte. Configurez au moins un canal avant de renseigner des seuils :

- `PRICE_MONITOR_ALERT_THRESHOLDS` : objet JSON, par exemple `{"brent":{"above":90,"below":70},"gasoil":{"above":3.5}}` ;
- `PRICE_MONITOR_ALERT_WEBHOOK_URL` : webhook Teams entrant ou endpoint générique JSON ;
- `PRICE_MONITOR_ALERT_EMAIL_TO` et les paramètres `PRICE_MONITOR_ALERT_SMTP_*` : livraison par email.
- `PRICE_MONITOR_DIGEST_EMAIL_TO` et `PRICE_MONITOR_DIGEST_INTERVAL_HOURS=5` : bilan des collectes envoyé toutes les cinq heures pendant l'exécution, avec les mêmes paramètres SMTP.

Le système conserve l'état des alertes dans `alert_states`. Il notifie les franchissements, les rétablissements, les échecs de source et les sources devenues obsolètes, sans répéter une alerte tant que la condition reste inchangée.

Les règles créées dans la section « Alertes de prix » du tableau de bord sont stockées dans `alert_rules`. Le canal choisi par règle limite la livraison au webhook, à l'email ou aux deux. Une règle en pause continue d'être évaluée afin que son état reste visible, mais aucune notification ne lui est envoyée. Les règles enregistrées dans la base prennent le relais de la configuration JSON d'environnement ; celle-ci reste disponible comme secours pour une première installation.
