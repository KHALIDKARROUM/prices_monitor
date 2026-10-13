# Price Monitor

Application de suivi des prix internationaux du gasoil, du Brent et du bitume, avec tableau de bord responsive, prévisions transparentes et workflow data science reproductible.

## Ce qui est livré

- collectes en arrière-plan selon chaque fournisseur, quotas persistants et reprises progressives ; les données enregistrées sont accessibles dès le démarrage ; le tableau de bord ouvert s'actualise chaque minute ;
- historique réel de cinq ans pour le Brent et le gasoil, issu des séries EIA publiées par FRED ;
- historique réel de 60 indices mensuels bitume BLS/FRED WPU058, utilisé dans les cartes, graphiques, archives, exports et comparaisons ;
- quatre APIs et six flux traçables : FRED (Brent et diesel), EIA directe (Brent et diesel), Alpha Vantage (Brent, optionnel) et BLS (indice bitume) ;
- indicateur bitume/asphalte `WPU058` collecté gratuitement et sans clé via l'API publique BLS, stocké séparément des prix négociables ;
- stockage SQLite ou MySQL 8 sélectionnable dans `.env`, avec migrations suivies dans `sql/mysql_migrations/` ;
- contrôle de qualité, validation stricte des dates et unités, déduplication idempotente, date de publication distincte de la date de collecte, variation absolue et en pourcentage ;
- saisie validée des devis, commandes ou factures de bitume ;
- répertoire SQL de canaux fournisseurs officiels, filtré par produit et région, avec processus RFQ, contrôle de contrepartie et calcul du coût rendu ;
- tableau de bord responsive à fond noir, navigation continue par défilement, graphiques de tendance, moyennes/minimums/maximums, journal des collectes ;
- prévisions statistiques à 7, 30 et 90 jours pour le gasoil et le Brent, avec bandes calibrées et comparaison au dernier prix connu ;
- projections mensuelles de l'indice bitume à 1, 3 et 6 mois, avec validation historique et alertes distinctes des devis USD/tonne ;
- exports CSV et Excel `.xlsx` ;
- notebook data science unique couvrant définition du problème, collecte, compréhension, nettoyage, EDA, feature engineering, préparation chronologique, sélection, entraînement, évaluation et tuning ;
- galerie de graphiques intégrée : distributions, box plots, co-mouvements, heatmap de corrélation, valeurs manquantes, pair plot, relations feature-cible, séries temporelles et importance par permutation ;
- mode démonstration disponible en option avec `PRICE_MONITOR_DEMO=true`.

## Démarrage rapide

Depuis la racine du dépôt, une seule commande suffit :

```powershell
bash run.sh
```

Puis ouvrir `http://127.0.0.1:8080`.

Le script crée `.venv` si nécessaire, installe les dépendances manquantes et démarre l'application. Sous PowerShell, la méthode équivalente reste disponible :

```powershell
.\.venv\Scripts\Activate.ps1
python -m price_monitor.app
```

Pour utiliser MySQL, copiez `.env.example` vers `.env`, configurez `PRICE_MONITOR_DB_BACKEND=mysql` ainsi que `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER` et `MYSQL_PASSWORD`. L'application crée la base configurée si le compte possède ce droit, applique `sql/mysql_schema.sql`, puis exécute les migrations non encore enregistrées.

## Calendrier des fournisseurs

Chaque fournisseur dispose d'un worker indépendant : une réponse lente ne bloque ni les autres flux, ni les lectures du tableau de bord. Au lancement, seuls les fournisseurs arrivés à échéance sont collectés. Le calendrier enregistré est conservé après un redémarrage. La page **Activité** affiche la cadence, la prochaine collecte, les requêtes sur 24 heures et les raisons d'attente ; ces informations sont également disponibles via `GET /api/collection-schedules`.

| Fournisseur | Intervalle par défaut | Configuration |
| --- | --- | --- |
| FRED, prix quotidiens | 6 heures | `PRICE_MONITOR_FRED_INTERVAL_MINUTES=360` |
| EIA, prix quotidiens | 6 heures | `PRICE_MONITOR_EIA_INTERVAL_MINUTES=360` |
| Alpha Vantage, Brent quotidien | 12 heures | `PRICE_MONITOR_ALPHA_VANTAGE_INTERVAL_MINUTES=720` |
| BLS/FRED, indice mensuel | 24 heures | `PRICE_MONITOR_BLS_INTERVAL_MINUTES=1440` |
| Yahoo, contrats BZ et HO | 1 minute pendant les séances | `PRICE_MONITOR_YAHOO_INTERVAL_MINUTES=1` |

L'ancien `PRICE_MONITOR_COLLECTION_INTERVAL_MINUTES` fournit désormais uniquement la valeur par défaut des cotations Yahoo et du mode démonstration. Il n'accélère plus les publications quotidiennes ou mensuelles. Les clés optionnelles continuent d'activer les fournisseurs correspondants.

[Alpha Vantage annonce 25 requêtes quotidiennes pour son accès standard gratuit](https://www.alphavantage.co/support/) et [BLS limite les utilisateurs non enregistrés à 25 requêtes quotidiennes](https://www.bls.gov/developers/api_faqs.htm). Les limites locales `PRICE_MONITOR_ALPHA_VANTAGE_DAILY_LIMIT=25` et `PRICE_MONITOR_BLS_DAILY_LIMIT=25` utilisent une fenêtre glissante de 24 heures, sans supposer une heure de remise à zéro du fournisseur. Chaque tentative HTTP consomme une réservation, même en cas d'échec. Les réservations sont atomiques et sauvegardées dans la même base pour les collectes planifiées, manuelles et les instances utilisant cette base. Les requêtes d'autres applications utilisant la même clé ou adresse IP ne sont pas connues localement ; les réponses de quota du fournisseur provoquent également une pause. Une augmentation de limite doit correspondre à votre abonnement. Des plafonds locaux facultatifs existent pour FRED, EIA et Yahoo (`PRICE_MONITOR_<FOURNISSEUR>_DAILY_LIMIT`, 0 par défaut).

Une erreur réseau applique un délai exponentiel de 60 secondes à six heures, configurable avec `PRICE_MONITOR_COLLECTION_BACKOFF_SECONDS` et `PRICE_MONITOR_COLLECTION_MAX_BACKOFF_SECONDS`. Le délai `Retry-After`, numérique ou daté, des réponses HTTP 429/503 est respecté. Les avis de quota Alpha Vantage et BLS reçus avec HTTP 200 provoquent une pause de 24 heures. FRED ne contourne pas une limitation HTTP par son relais CSV. Le relais officiel FRED de l'indice BLS reste utilisable : il est comptabilisé comme une requête FRED. Une collecte manuelle peut avancer la collecte d'un fournisseur sain ; elle ne contourne ni quota, ni délai d'échec, ni collecte déjà en cours, ni fermeture de marché. Les collectes différées ne sont pas enregistrées comme de nouveaux échecs du fournisseur.

Les cotations NYMEX BZ/HO utilisent les séances du dimanche 18 h au vendredi 17 h, heure de New York, et la pause quotidienne de 17 h à 18 h, avec changements d'heure gérés par `tzdata`. Les fermetures exceptionnelles, jours fériés et clôtures anticipées se configurent dans `PRICE_MONITOR_MARKET_CLOSURES`, une liste JSON de fenêtres `{ "start": "horodatage ISO avec fuseau", "end": "horodatage ISO avec fuseau" }`. Maintenez ces fenêtres d'après le [calendrier officiel CME](https://www.cmegroup.com/trading-hours.html) ; les jours fériés des marchés actions ne sont pas appliqués automatiquement aux contrats énergie. Les contrôles de fraîcheur des cotations sont suspendus pendant ces fermetures, puis reprennent après cinq minutes de grâce au maximum, sans dépasser le seuil de la règle. La réouverture seule ne déclenche aucune notification de rétablissement.

Les migrations `012` ajoutent les calendriers, les leases de concurrence et le suivi des requêtes dans SQLite et MySQL. Elles s'appliquent au prochain démarrage et sont incluses dans les sauvegardes de la base.

## Navigation

L’interface utilise un fond noir, des panneaux gris et huit sections : **Marchés**, **Tendances**, **Veille**, **Archives**, **Nouveau prix**, **Fournisseurs**, **Budget** et **Activité**. Les sections restent dans une page continue ; les liens de navigation défilent doucement vers la section choisie et la page active suit la position de défilement. Les animations sont désactivées lorsque la préférence système de réduction des mouvements est activée.

Chaque section possède un lien direct (`#marches`, `#tendances`, `#veille`, `#archives`, `#nouveau-prix`, `#fournisseurs`, `#budget`, `#activite`). Les anciens liens restent compatibles. La navigation conserve les formulaires en cours et prend en charge les boutons précédent/suivant du navigateur. Sur mobile, le bouton de menu affiche les sections ; la touche Échap referme le menu.

Après chaque collecte (au démarrage, programmée ou manuelle), les prix reçus des API sont comparés deux à deux. Une différence entre séries équivalentes de même date, unité et devise affiche « Les prix des API diffèrent : un problème est possible », avec les fournisseurs, les prix et l'écart. L'avertissement est enregistré dans le journal de collecte, affiché dans la console à chaque collecte concernée et conservé en haut du tableau de bord tant que l'écart existe. Seuls les arrondis sont tolérés : 0,005 USD/baril pour le Brent et 0,0005 USD/gallon pour le diesel. Les dates différentes, contrats à terme, proxies, indices et données manquantes restent explicitement non comparables ou non vérifiés. La réponse de `/api/collect` inclut le résultat dans `source_comparison`.

## Tests

Installer les dépendances puis lancer la suite automatisée :

```powershell
.\.venv\Scripts\python.exe -m pytest
```
     
Les tests couvrent la validation et la persistance SQLite, les calculs du tableau de bord, les exports CSV/Excel, l'import du snapshot réel EIA/FRED, les collectes complètes, partielles ou en échec, ainsi que le schéma et le moteur de migrations MySQL. Ils vérifient aussi l'absence de fuite temporelle dans les variables et les cibles, la purge des horizons, l'isolation du holdout, les métriques et la logique Holt en jours calendaires. La CI installe donc les extras `test,analysis`. Pour exécuter aussi le test d'intégration MySQL, définissez `RUN_MYSQL_TESTS=1` avec des paramètres `.env` valides.

Le projet démarre avec un snapshot réel de cinq ans dans `data/processed/market_prices.csv`, sans clé API. Pour actualiser les fichiers source et reconstruire le snapshot :

```powershell
python scripts/prepare_market_data.py --years 5 --refresh
```

Les cartes principales du tableau de bord utilisent des cotations de marché en direct pour le Brent (`BZ=F`) et un proxy gasoil clairement identifié (`HO=F`, USD/gallon). Elles sont renouvelées chaque minute par défaut et affichent leur heure de collecte. Les flux FRED/EIA, EIA v2, Alpha Vantage et BLS restent disponibles pour l'historique, les contrôles de qualité et la traçabilité. Ajoutez `EIA_API_KEY` pour activer les deux flux EIA v2 directs (diesel et Brent), et `ALPHA_VANTAGE_API_KEY` pour le flux Brent supplémentaire. Conservez `PRICE_MONITOR_DEMO=false`; définissez `PRICE_MONITOR_LIVE_QUOTES_ENABLED=false` uniquement si vous voulez désactiver les cotations de marché en direct.

Une fenêtre de notification apparaît dans le site à la perte de la connexion Internet, puis à son rétablissement. Elle ne se répète pas tant que l'état reste identique. Le navigateur signale immédiatement une déconnexion réseau ; le serveur vérifie aussi l'accès HTTPS à deux fournisseurs existants toutes les cinq secondes, et la page consulte cet état toutes les cinq secondes lorsqu'elle est visible. Cette vérification peut donc prendre quelques secondes. Le badge reste visible et distingue une connexion perdue, une application inaccessible, une collecte partielle et des sources indisponibles malgré un accès Internet. Les prix enregistrés restent consultables hors ligne. Les tentatives de collecte continuent d'être conservées dans le journal d'activité.

La console affiche une seule ligne au démarrage, puis uniquement lorsque la disponibilité change : `no collections , try again` si Internet est indisponible ou qu'aucune donnée n'est collectée, et `the website is up to date` lorsqu'une collecte reçoit des données réelles des API. Une collecte partielle contenant des données reste disponible. Le retour d'Internet seul ne déclenche pas le message de succès : une nouvelle collecte doit le confirmer. Les messages ne se répètent pas à chaque minute ou rafraîchissement de page. Les détails des collectes et des requêtes HTTP restent accessibles au niveau DEBUG. Chaque tentative reste enregistrée en base et comptabilisée dans les métriques et le bilan email. Les tests des notifications du navigateur se lancent avec `node --test tests/test_connectivity_ui.cjs`.

Au démarrage, l'application synchronise de façon idempotente l'historique FRED/EIA présent dans `data/processed/market_prices.csv`, y compris pour une base SQLite ou MySQL déjà existante. Le graphique filtre et affiche les séries selon leur date de publication (`source_date`). Une valeur de carte plus ancienne que sept jours pour le gasoil ou le Brent, ou que quarante-cinq jours pour un devis bitume. L’indice mensuel bitume est signalé ancien après soixante-quinze jours depuis son mois source, pour tenir compte du délai de publication BLS, est explicitement signalée comme ancienne.

La section **Comparer les prix des API** affiche le dernier prix et la date de chaque fournisseur. Les flux spot Brent de FRED, EIA et Alpha Vantage, ainsi que les flux spot diesel FRED/EIA, sont comparés seulement si leur date, leur unité et leur devise correspondent. Un écart supérieur à l'arrondi publié (0,005 USD/baril ou 0,0005 USD/gallon) est signalé « À vérifier » ; une révision de la série peut aussi expliquer cet écart. Les contrats à terme Yahoo, le proxy heating oil et l'indice BLS restent clairement distingués. Les API absentes, désactivées ou en échec ne prouvent aucun accord. Les valeurs affichées sont les derniers relevés enregistrés, pas une nouvelle requête aux fournisseurs ; aucune notification répétée n'est ajoutée. FRED et Alpha Vantage relaient EIA, ce qui vérifie la cohérence des flux sans constituer une mesure indépendante.

Le projet distingue volontairement trois types de données :

- **gasoil** : `DDFUELNYH`, prix spot FOB New York Harbor en USD/gallon — benchmark de négociation, pas prix rendu à destination ;
- **bitume — indicateur public** : `WPU058`, indice mensuel américain de tendance, **pas** un prix en USD/tonne ;
- **bitume — prix achetable** : devis, commandes ou factures fournisseur enregistrés en USD/tonne avec grade, origine, Incoterm et date.

Il n'existe pas de prix spot mondial gratuit et exécutable du bitume dans une API publique comparable à une cotation boursière. Une décision d'achat doit donc comparer des devis homogènes. L'application ne convertit jamais l'indice BLS en faux prix USD/tonne.

## Analyse exploratoire, feature engineering et machine learning

Un notebook unique est fourni :

- `notebooks/data_science_workflow.ipynb` regroupe les dix étapes du workflow, avec 22 cellules de code exécutées et les figures intégrées au document.

Les graphiques adaptés au snapshot comprennent les histogrammes, box plots des rendements, scatter plots Brent-gasoil, heatmap de corrélation, comptage des observations et valeurs manquantes, pair plot, feature vs target, box plot de la cible par quartile de volatilité, séries temporelles et importance des variables par permutation. Les graphiques de variables continues remplacent volontairement les count plots catégoriels, car le snapshot ne contient pas de variable catégorielle pertinente à compter.

Le pipeline complet peut aussi être relancé sans interface :

```powershell
python scripts/run_data_science.py --save-models
```

Le rapport généré est `reports/data_workflow/report.md`. Il contient les contrôles de qualité, les graphiques EDA, les découpages temporels, les scores de validation et de holdout, les prédictions, l'importance des variables et une fiche par expérience. Le manifeste enregistre le hash du snapshot, les versions des dépendances, la graine et les empreintes du code.

Les variables comprennent les rendements logarithmiques retardés, momentum, volatilité, écarts aux moyennes mobiles, cycles calendaires, durée entre publications et informations antérieures de l'autre produit. Les cibles restent toujours des prix réellement publiés : aucun prix cible n'est interpolé. Les transformations sont ajustées dans chaque pli d'entraînement, les cibles qui dépassent une frontière de validation sont purgées, et les 20 % les plus récents restent un holdout final.

Le benchmark de persistance, Holt amorti corrigé, Ridge, gradient boosting et forêt aléatoire sont comparés aux horizons 1, 7, 30 et 90 jours calendaires. La sélection exige au moins 2 % de gain MAE par rapport au dernier prix connu. Sur le snapshot actuellement livré, aucun modèle complexe ne franchit ce seuil de façon régulière en validation ; la persistance reste donc le modèle sélectionné. Le choix persistance/Holt utilise maintenant la même comparaison dans la recherche et le service. Les modèles ML de recherche ne sont pas automatiquement mis en production.

Les séries Brent et gasoil sont conservées dans leurs unités publiées respectives : USD/baril et USD/gallon. Les corrélations de rendement utilisent des intervalles de dates identiques. Les chocs détectés restent dans les données et sont seulement signalés. L’indice bitume fait l’objet d’une analyse mensuelle séparée; les expériences ML quotidiennes conservent Brent et gasoil, sans mélanger l’indice aux prix USD/tonne.

## Prévisions

Les entrées de prévision sont agrégées en SQL avant toute limite : un relevé réel par instrument et date source, choisi avec la priorité de fournisseur décrite ci-dessous puis la dernière heure de collecte. La limite de 5 000 porte sur les points agrégés les plus récents, restitués dans l'ordre chronologique. Une journée dense en cotations intraday ne peut donc plus masquer les publications suivantes. Le tableau de bord utilise aussi les séries quotidiennes complètes de la période; les archives paginées conservent les relevés bruts.

Les exports CSV et Excel incluent tous les relevés correspondant aux filtres, sans plafond de 10 000 lignes. La lecture SQL se fait par lots sur une seule requête. Excel écrit les lignes progressivement et ouvre une nouvelle feuille après 1 048 575 lignes de données, avec les en-têtes répétés. Les comparaisons de périodes utilisent également toutes les données correspondantes.

Les instruments sont séparés par un identifiant stable : `brent_spot`, `brent_futures`, `diesel_spot`, `heating_oil_futures`, `bitumen_quote` et `asphalt_index`. Une variation compare les observations du même instrument et de la même source. Les cartes et graphiques utilisent le même instrument choisi dans Marchés; la sélection automatique conserve les cotations à terme lorsqu'elles sont enregistrées, sinon le spot. Les statistiques et comparaisons de périodes prennent un relevé par jour et par instrument, avec priorité FRED, puis EIA, puis Alpha Vantage pour les séries spot. Les désaccords entre API restent visibles dans le contrôle des sources.

Les archives et exports acceptent `instrument=brent_spot` (ou un autre identifiant). `/api/dashboard` accepte `brent_instrument` et `gasoil_instrument`; les prévisions quotidiennes utilisent exclusivement les séries spot. Les comparaisons de périodes retournent une ligne séparée par instrument, sans moyenne commune spot/contrat à terme/proxy. Les estimations d'achat utilisent le benchmark spot pour les produits quotidiens, jamais le proxy heating oil.

Chaque règle de prix choisit son instrument dans Veille et possède son propre état. Une collecte évalue une seule valeur par instrument, indépendamment de l'ordre des fournisseurs. Une règle au-dessus et une règle en-dessous peuvent être créées pour chaque instrument. Les règles existantes Brent/gasoil et les seuils JSON d'environnement sans identifiant utilisent le spot par défaut; les règles de fraîcheur continuent de suivre les cotations à terme. La migration `010` conserve les relevés et les règles, corrige les anciennes variations et réinitialise les anciens états de seuils quotidiens mélangés. Un premier franchissement peut donc être notifié à la prochaine évaluation.

La route `GET /api/forecast?history_days=1825` calcule des projections à 7, 30 et 90 jours calendaires à partir de la dernière date source pour le gasoil et le Brent. Pour le bitume, elle projette l'indice BLS WPU058 à 1, 3 et 6 mois, dans son unité publiée (déc. 1984 = 100). Chaque produit expose sa fréquence et ses horizons. Chaque horizon choisit entre le dernier prix connu (persistance) et Holt amorti. L’API retourne les deux scores comparables, le modèle retenu, les avertissements de performance et les valeurs prévues.

Un historique d'au moins 30 relevés est nécessaire pour afficher une projection. La comparaison partage le protocole `chronological_endpoints_v1` avec la recherche : cible réellement publiée au jour demandé ou dans les quatre jours suivants, premiers 65 % des dates pour trois plis de validation à fenêtre croissante, 15 % suivants pour la calibration, 20 % finaux pour le test. Les cibles qui traversent une frontière sont purgées. Holt doit gagner au moins 2 % de MAE moyenne en validation ; le test final ne change jamais ce choix. Sans validation suffisante, la persistance est retenue par défaut. La recherche peut aussi comparer des modèles ML avec ces mêmes cibles et scores, sans les déployer automatiquement.

Les bandes utilisent un quantile corrigé des erreurs logarithmiques absolues sur la période de calibration, propre au modèle retenu et à chaque pas de prévision. Il faut au moins 19 erreurs pour un quantile fini à 95 % nominal. Une bande insuffisamment calibrée reste absente (`lower` et `upper` à `null`). Les horizons qui se chevauchent rendent les erreurs dépendantes : la couverture n’est pas garantie. `model_selection[horizon]` expose les scores de validation, les deux candidats, les dates et tailles des périodes, ainsi que les avertissements ; `backtest[horizon]` mesure le modèle retenu sur le test final. L’absence de gain démontré, les petits échantillons, les pertes face à la persistance et la sous-couverture sont affichés au-dessus du graphique. L’origine et l’âge des données restent visibles.

Pour le bitume, 30 observations mensuelles sont nécessaires. Le même protocole travaille en mois et exige une publication au mois cible, sans interpolation. Avec le snapshot de 60 mois, la validation et la calibration sont trop courtes : la persistance est utilisée, les bandes à 95 % restent absentes et cette limitation est signalée. `data/processed/bitumen_index.csv` fournit 60 mois réels et est synchronisé au démarrage, dans SQLite et MySQL. La collecte utilise BLS puis son relais FRED si BLS est indisponible ou limité. Les archives et exports indiquent `data_kind=index` et `benchmark_code=bls_wpu058`; `data_kind=quote` filtre les prix et devis. Les comparaisons de périodes séparent les indices des devis. Sur une période courte, le graphique peut prolonger le dernier indice connu avec sa vraie date source indiquée; cela n'ajoute aucune observation mensuelle.

Dans Veille, choisissez « Bitume », puis « Indice mensuel BLS » ou « Devis fournisseur ». Le champ `basis=index` surveille uniquement l'indice; `basis=quote` conserve les seuils USD/tonne existants. Les budgets et achats utilisent uniquement les devis, jamais l'indice. Le notebook et les rapports incluent une analyse mensuelle distincte (`bitumen_statistics.csv`, `bitumen_forecast.json`, `bitumen_backtest.csv`, `eda_bitumen_index.png`).

## Devis fournisseurs et coûts rendus

Dans **Fournisseurs → Gérer les devis**, créez un dossier avec fournisseur, référence, grade, norme, origine, Incoterm et lieu nommé, destination, quantité/unité, conditionnement, livraison, paiement, date de devis, validité et type de pièce commerciale. Les dossiers sont distincts des relevés publics de marché et des indices BLS. Les anciens relevés et budgets sont conservés; les conditions commerciales manquantes ne sont pas devinées à partir de leurs notes.

**Corriger** exige un motif et conserve une nouvelle version avec valeurs avant/après, auteur déclaré et horodatage UTC. Un numéro de version attendu empêche une correction ancienne d’écraser une modification concurrente (réponse `409`). L’auteur est une indication saisie par l’équipe, pas une identité individuelle authentifiée : l’application conserve son contrôle d’accès existant par clé API et son exception de développement local.

Joignez PDF, PNG/JPEG, TXT UTF-8, DOCX ou XLSX sans macros : **5 Mo par pièce**, **50 Mo par dossier**, historique compris. Chaque pièce conserve son type, nom, taille et empreinte SHA-256. Les téléchargements passent par les routes privées du dossier, avec disposition pièce jointe. **Retirer** demande un motif et masque la pièce des justificatifs actifs; ses octets restent consultables dans l’historique. Les documents sont stockés dans la base : la sauvegarde SQLite et le dump MySQL (avec `--hex-blob`) les incluent.

Sélectionnez **2 à 6 devis** pour une comparaison côte à côte. Le classement utilise le coût rendu total en USD, avec le taux enregistré de chaque offre. Il n’est affiché que si produit, grade, norme, origine, Incoterm/lieu, destination, quantité/unité, conditionnement, livraison, paiement et type commercial correspondent et si toutes les offres sont actives et valides à la date choisie. Les différences restent visibles sans faux gagnant. La comparaison lit les versions actuelles; le choix de date contrôle leur validité et ne reconstruit pas une ancienne version du dossier.

Le budget comprend **marchandise + transport + assurance + inspection + droits + taxes + stockage + chauffage + frais financiers + autres frais nommés**. Tous les frais sont des montants totaux dans la devise du budget, ajoutés hors prix unitaire; un coût déjà inclus ne doit pas être ajouté une seconde fois. Les taux, taxes et frais sont saisis par l’utilisateur, sans estimation réglementaire automatique. Le détail, la conversion USD, le coût rendu par unité et l’écart au budget sont enregistrés.

**Préparer le budget** depuis un dossier reprend ses montants et conserve une copie de cette version, y compris les conditions et métadonnées des justificatifs. Une correction ultérieure ne modifie jamais l’estimation déjà enregistrée. **Passer à un budget libre** permet de préparer une autre estimation sans lien avec le devis.

La migration **011**, exécutée au démarrage dans SQLite et MySQL, ajoute les tables et champs sans modifier les montants historiques. Redémarrez l’application après la mise à jour. Les nouveaux coûts des anciens budgets valent zéro. Les routes `/api/quotes`, `/api/quotes/{id}/history`, `/api/quotes/compare` et les routes documentaires sont décrites dans `/openapi.json`; elles utilisent la protection des données locales existante.

## API locale

Les routes d'écriture sont protégées par une clé API. L'historique détaillé et sa comparaison restent publics en lecture seule afin que le tableau de bord fonctionne sans transmettre de secret. Définissez `PRICE_MONITOR_API_KEY` dans `.env` avec une valeur longue et aléatoire, puis envoyez-la avec `X-API-Key` ou `Authorization: Bearer <clé>` pour collecter, ajouter ou modifier des données. La clé n'est jamais acceptée dans l'URL.

```powershell
$bytes = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$env:PRICE_MONITOR_API_KEY = [Convert]::ToBase64String($bytes)
```

Sur votre ordinateur, avec l'hôte local par défaut et en mode développement, la collecte, les règles de surveillance, les observations manuelles et la page Budget fonctionnent sans clé. Les observations et budgets autorisent les requêtes du tableau de bord local et refusent celles provenant d'un autre site. Une clé configurée est toujours vérifiée. Une clé est obligatoire pour toute instance publique ou en production (`PRICE_MONITOR_ENV=production`) ; le tableau de bord la demande au premier appel protégé et la conserve uniquement dans la session du navigateur.

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/dashboard?days=30` | indicateurs, séries et derniers relevés |
| GET | `/api/connectivity` | connexion Internet vérifiée et disponibilité agrégée des fournisseurs |
| GET | `/api/collection-schedules` | calendriers, quotas de requêtes et raisons des reports par fournisseur |
| GET | `/api/source-comparison` | prix par fournisseur, dates, unités et écarts entre séries équivalentes |
| GET | `/api/forecast?history_days=1825` | gasoil/Brent à 7/30/90 jours et indice bitume à 1/3/6 mois, bandes indicatives et backtest |
| GET | `/api/alerts` | règles d'alerte et états courants |
| GET | `/api/benchmarks?product=bitume` | derniers indicateurs publics, séparés des devis |
| GET | `/api/suppliers?product=bitume&region=europe` | canaux fournisseurs stockés en SQL |
| GET | `/api/procurement-guide?product=gasoil&region=africa_middle_east` | contexte, fournisseurs, RFQ et étapes d'achat |
| POST | `/api/alerts` | créer une règle (clé API) |
| PUT / DELETE | `/api/alerts/{id}` | modifier, mettre en pause ou supprimer une règle (clé API) |
| GET | `/api/observations?product=bitume` | historique filtré (sans clé en local ; clé API ailleurs) |
| GET | `/api/history?page=1&page_size=50&product=bitume&supplier=ABC&source=devis&date_from=2026-01-01&date_to=2026-09-15&min_price=400&max_price=700` | historique public paginé et filtré |
| GET | `/api/history/compare?period_a_from=2026-01-01&period_a_to=2026-03-31&period_b_from=2026-04-01&period_b_to=2026-06-30` | comparaison publique de deux périodes |
| POST | `/api/collect` | collecte les fournisseurs disponibles, avec détail des reports (sans clé en local ; clé API ailleurs) |
| POST | `/api/observations` | ajoute une observation validée (sans clé en local ; clé API ailleurs) |
| GET | `/export.csv` / `/export.xlsx` | exports |

En production, une limitation en mémoire s'applique par adresse cliente : 60 requêtes par fenêtre de 60 secondes par défaut. Ajustez `PRICE_MONITOR_RATE_LIMIT_REQUESTS` et `PRICE_MONITOR_RATE_LIMIT_WINDOW_SECONDS` si nécessaire.

## Alertes de prix et de disponibilité

Les règles d'alerte peuvent être créées, modifiées, mises en pause ou supprimées depuis la page « Veille » du tableau de bord. Chaque règle associe un produit, une direction (`above` ou `below`), un seuil et un canal (`webhook`, `email` ou `both`). L'interface affiche les règles actives, rétablies, surveillées et en pause.

Pour surveiller la fraîcheur des cotations en direct, choisissez « Cotation en direct non actualisée » pour le Brent ou le proxy gasoil, puis un délai de 2 à 1440 minutes et « Email ». L'application compare l'heure réelle de la dernière cotation renvoyée par le fournisseur, et non l'heure de collecte. Pendant les séances ouvertes, elle contrôle l'échéance à la seconde près, puis tente d'envoyer un email au franchissement du délai et un autre quand une cotation récente revient. Les fermetures connues suspendent les contrôles sans modifier l'état de l'alerte ; à la réouverture, un délai de grâce de cinq minutes au maximum, borné par le seuil, permet de recevoir une nouvelle cotation sans réinitialiser indéfiniment les délais longs. La livraison dans la boîte de réception dépend du serveur SMTP et peut prendre plus de temps. Aucune notification ne peut être envoyée si l'application est arrêtée.

L'envoi exige `PRICE_MONITOR_ALERT_EMAIL_TO`, `PRICE_MONITOR_ALERT_SMTP_HOST` et `PRICE_MONITOR_ALERT_SMTP_FROM` (ou `PRICE_MONITOR_ALERT_SMTP_USER`) dans `.env`, ainsi que les identifiants SMTP nécessaires au fournisseur. Pour Gmail, utilisez un [mot de passe d'application Google](https://support.google.com/accounts/answer/185833) dans `PRICE_MONITOR_ALERT_SMTP_PASSWORD` ; le mot de passe habituel du compte peut être refusé. Veille affiche si le canal email est configuré, mais seul un envoi réussi confirme les identifiants. Les erreurs de livraison restent dans les journaux du serveur et la notification de fraîcheur est retentée lors des contrôles suivants.

Pour recevoir un bilan des messages de collecte, renseignez `PRICE_MONITOR_DIGEST_EMAIL_TO` dans `.env`. Tant que l'application tourne, elle envoie un seul email de synthèse toutes les cinq heures (`PRICE_MONITOR_DIGEST_INTERVAL_HOURS=5`) avec le nombre de collectes par fournisseur réussies, partielles et échouées, le total de données collectées et les erreurs regroupées. Le premier bilan part cinq heures après le démarrage ; chaque fournisseur suit sa propre cadence. Le bilan utilise les paramètres `PRICE_MONITOR_ALERT_SMTP_*` déjà configurés. Si l'envoi échoue, les collectes non envoyées restent dans le prochain bilan, et une nouvelle tentative a lieu cinq heures plus tard. Les alertes de prix configurées dans « Veille » restent des notifications distinctes.

Les règles configurées dans l'interface sont persistées dans `alert_rules`. Pour conserver la compatibilité avec les installations existantes, `PRICE_MONITOR_ALERT_THRESHOLDS` reste utilisé comme configuration de secours tant qu'aucune règle enregistrée n'existe :

```text
PRICE_MONITOR_ALERT_THRESHOLDS={"brent":{"above":90,"below":70},"gasoil":{"above":3.5},"bitume":{"above":600}}
```

Une alerte est envoyée lors du franchissement du seuil, puis une notification de rétablissement est envoyée lorsque le prix revient dans la zone normale. L'état est conservé en base afin d'éviter une notification à chaque collecte.

Les notifications peuvent être envoyées vers un webhook compatible Teams ou générique avec `PRICE_MONITOR_ALERT_WEBHOOK_URL`, et/ou par SMTP avec `PRICE_MONITOR_ALERT_EMAIL_TO`, `PRICE_MONITOR_ALERT_SMTP_HOST`, `PRICE_MONITOR_ALERT_SMTP_FROM` et les paramètres SMTP associés. Les échecs de collecte et les sources quotidiennes ou mensuelles devenues obsolètes déclenchent également une alerte. Les sources « à la demande » et jamais collectées ne sont pas considérées comme obsolètes.

Exemple de saisie bitume :

```json
{"product":"bitume","price":528.5,"unit":"USD/tonne","source":"Devis Fournisseur ABC","source_date":"2026-09-08","notes":"FOB Méditerranée"}
```

`source_date` doit respecter le format `YYYY-MM-DD` et `collected_at`, lorsqu'il est fourni, doit être un horodatage ISO 8601 avec fuseau. L'unité doit correspondre à celle du produit. Une nouvelle tentative avec le même produit, la même source, la même date source et le même prix réutilise l'observation existante.

Les exports `/export.csv` et `/export.xlsx` acceptent les mêmes filtres que `/api/history` et exportent tous les résultats correspondants, sans pagination. La page « Archives » permet également de comparer les moyennes, minimums, maximums et volumes de relevés de deux périodes.

## Impact d'un achat

Le formulaire « Préparer un budget » et la route protégée `POST /api/procurements` enregistrent une estimation d'achat avec le fournisseur, la quantité, la devise, le prix unitaire, le transport, le taux de change et le budget prévu. Le taux de change est exprimé en **USD pour une unité de la devise de l'achat** : `1 EUR = 1.09 USD` se saisit donc `1.09`.

L'application calcule :

- le coût total dans la devise de l'achat et en USD (`quantité × prix unitaire + transport`) ;
- l'écart au budget, positif lorsque l'achat dépasse le budget ;
- l'écart du prix unitaire et l'impact total par rapport au dernier prix marché connu du produit.

L'historique est disponible via `GET /api/procurements?limit=100` et dans la page « Budget » du tableau de bord. La lecture et l'enregistrement d'estimations fonctionnent sans clé uniquement en développement local, avec un serveur lié à l'adresse de boucle locale et un client local. Une clé configurée est toujours vérifiée. Un historique vide affiche « Aucun achat enregistré » ; les erreurs affichent leur cause et un bouton « Réessayer ».

## Où acheter et comment acheter

La page « Fournisseurs » ne place aucune commande automatiquement. Elle fournit des voies de contact officielles vers des producteurs, distributeurs ou services B2B et un processus de contrôle :

1. définir le grade et la norme (`EN 590` pour le diesel, `EN 12591`/`ASTM D946` pour le bitume) ;
2. envoyer le même RFQ à au moins trois vendeurs avec quantité, destination, fenêtre et Incoterm 2020 ;
3. vérifier société, licence, bénéficiaire effectif, sanctions, références et compte bancaire ;
4. comparer le coût rendu : marchandise + fret + assurance + inspection + droits/taxes + stockage/chauffage + frais financiers ± change ;
5. contractualiser qualité, documents, inspection, tolérances, paiement et réclamations ;
6. privilégier un crédit documentaire, un crédit fournisseur approuvé ou un autre mécanisme sécurisé plutôt qu'un prépaiement à un vendeur non vérifié.

Pour le bitume, le RFQ doit aussi préciser le grade, le conditionnement, la température, le coût de maintien en chauffe et les surestaries. Les entreprises du répertoire sont des exemples de voies de contact officielles, pas une recommandation ni une garantie de disponibilité.

Les deux nouvelles tables sont `market_benchmarks` et `supplier_channels`. Elles sont créées par la migration `007_market_benchmarks_and_suppliers.sql` dans SQLite et MySQL.

## Passage en production

Le fichier `sql/mysql_schema.sql` contient la structure MySQL correspondant aux tables du rapport : `price_observations`, `data_sources`, `collection_logs` et `schema_migrations`. Le stockage SQLite intégré reste le défaut local ; MySQL peut être activé sans modifier les routes de l'application.

## Déploiement et opérations

Le projet fournit un `Dockerfile`, un `docker-compose.yml` MySQL 8.4 et une pipeline GitHub Actions dans `.github/workflows/ci.yml`. Pour l'installation comme paquet Python :

```powershell
python -m pip install .
price-monitor-migrate
price-monitor
```

Les sondes opérationnelles sont `/health/live`, `/health/ready` et `/health` (readiness), tandis que `/metrics` expose des métriques Prometheus et `/openapi.json` la spécification OpenAPI. Une page d'accès rapide est disponible sur `/docs`.

Les journaux sont structurés en JSON sur stdout. Les migrations SQLite versionnées sont dans `price_monitor/migrations/`; les migrations MySQL existantes sont dans `sql/mysql_migrations/`. La commande `price-monitor-backup` crée une sauvegarde cohérente SQLite ou un dump MySQL ; la stratégie de rétention, de copie hors site et de restauration est décrite dans [`docs/operations.md`](docs/operations.md).
