# Transmission à l’agent serveur

Cette procédure décrit un déploiement Docker générique. Adapter le domaine,
le propriétaire des fichiers et le raccordement HTTPS à l’environnement cible.

## Deux conteneurs et une base

`api` sert FastAPI/Pydantic et la PWA ; `scraper` garde Supercronic en attente et
lance une récupération ponctuelle toutes les dix minutes, de 06h00 à 23h50,
heure `Europe/Zurich`. Les deux utilisent la même image `gaps-school:local`, cible `runtime` sans
Playwright ni Chromium. Le projet Compose porte le nom stable `mini-gaps`.
`DEV=false` réduit les journaux automatiques aux avertissements et erreurs ;
`DEV=true` conserve les diagnostics et sélectionne l’origine HTTP locale.
Seul `api` définit sa construction Compose pour éviter deux builds concurrents
sous le même nom d’image. `docker compose up --build -d` construit puis démarre
les deux services. Ne pas démarrer le scraper seul avant la première construction.

La base SQLite est `./data/private/notes.sqlite3`, montée dans les deux services
via `./data/private:/app/data/private`. Les connexions transactionnelles attendent un verrou au
plus dix secondes. Le verrou de récupération empêche les passages simultanés.
Aucun troisième conteneur DB, socket Docker ou navigateur automatisé.

Le secret Compose `app_configuration` provient du seul fichier **`.env`**, monté
en lecture seule à `/run/secrets/app.env` dans le scraper uniquement. Compose en
utilise les paramètres publics pour configurer les services ; l’API ne reçoit
jamais le fichier complet ni les variables des identifiants scolaires.

Les clés Push sont des données générées dans `data/push/vapid.pem`, volume privé
monté uniquement dans le scraper à `/app/data/push`. Elles ne constituent pas un
second fichier de configuration à remplir. Les secrets Compose locaux sont des
fichiers montés, pas un coffre chiffré géré par Swarm.

## Préparation sur le serveur, à exécuter par son agent

1. Copier le code et créer `data/private/` et `data/push/` avec le propriétaire
   du projet et des permissions 700. Copier `.env.example` vers `.env`
   s’il n’existe pas, sans écraser une configuration existante. Permissions 600.
2. Dans ce seul `.env`, renseigner `GAPS_USERNAME` et `GAPS_PASSWORD`, adapter
   `LOCAL_UID`/`LOCAL_GID` au propriétaire (`id -u`, `id -g`) et définir
   `DEV=false` et `APP_ORIGIN` avec l’origine publique HTTPS de l’application,
   sans chemin ni slash final (exemple fictif : `https://mini-gaps.example.org`).
   Garder le format de guillemets du modèle pour les caractères spéciaux. Ne pas exécuter `.env` avec `source`.
   Le fuseau applicatif ne modifie pas celui de Debian. Après une modification
   de `.env`, recréer les services avec `docker compose up -d --force-recreate`.
3. Construire et valider avant démarrage automatique :

   ```bash
   docker compose config --quiet
   docker build --target tests -t gaps-school-tests:local .
   docker compose build
   docker compose run --rm --no-deps scraper sync
   ```

4. Générer une seule fois les clés Push et sauvegarder le PEM. Voir [push.md](push.md) :

   ```bash
   docker compose run --rm --no-deps scraper push init --subject https://mini-gaps.example.org
   ```

   Remplacer le domaine fictif par son origine publique.
   La commande génère le PEM dans le volume persistant `data/push/`, permissions
   600. Le fichier `.env` reste monté en lecture seule. Aucun override Compose supplémentaire.
5. Démarrer les deux services avec `docker compose up -d`, contrôler `docker compose ps`
   et `docker compose logs --tail 30 scraper`. Le cron est interne au scraper :
   aucun cron supplémentaire sur l’hôte, aucune reconstruction à chaque passage.
6. Raccorder l’application au reverse proxy ou tunnel HTTPS de l’environnement.
   Vérifier son réseau Docker : le port actuel est lié à `127.0.0.1:8000` et un
   proxy conteneurisé nécessitera un réseau adapté. Préserver le Host public
   pour le contrôle d’origine et les autres services existants.
7. Contrôler `/healthz`, les 401 sans appareil associé et `Cache-Control: no-store`
   sur l’API. Aucun cache CDN de `/api/`. Le healthcheck intégré vérifie la
   disponibilité du processus API ; la fraîcheur GAPS est un état distinct par catégorie.

Les limites proposées sont 128 Mio / 0,5 CPU / 32 processus pour le scraper et
256 Mio / 0,5 CPU / 64 processus pour l’API. Les racines sont en lecture seule,
les privilèges supprimés, les journaux plafonnés à 2 × 2 Mio par service.
Confirmer la consommation sur le serveur cible ; les mesures Docker Desktop sont dans
[validation.md](validation.md). Supercronic attend entre les passages sans garder
Python et ses dépendances chargés ; le travail réseau/parsing se fait seulement au passage.

## Association et iPhone

Installer la PWA sur l’écran d’accueil de l’iPhone, puis l’ouvrir avant association.
Dans un terminal privé, générer le code :

```bash
docker compose exec scraper python -m gaps_scraper devices pair
```

Ne pas journaliser ce code. La session est valable cinq années civiles, révocable,
avec réémission du cookie à l’ouverture sans prolongation du délai serveur.
La conservation du cookie dépend aussi du navigateur ; une suppression peut
imposer une nouvelle association. Tester installation, hors connexion, révocation
et notifications depuis « Réglages ». Les tests réels iOS et fournisseur Push
restent à réaliser ; un test Chromium simulé ne les valide pas.

## Sauvegarde cohérente

Ne pas copier à chaud le seul fichier SQLite. Utiliser `Connection.backup()` pour
une copie cohérente ; exemple depuis Docker, destination nouvelle dans le volume :

```bash
docker compose exec -T api python - <<'BACKUP'
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import os, sqlite3
os.umask(0o077)
folder=Path('/app/data/private/backups'); folder.mkdir(mode=0o700,exist_ok=True)
name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
with closing(sqlite3.connect('file:/app/data/private/notes.sqlite3?mode=ro',uri=True)) as source:
    with closing(sqlite3.connect(folder / (name+'.sqlite3'))) as destination:
        source.backup(destination)
print('Sauvegarde SQLite créée dans data/private/backups.')
BACKUP
```

Copier ensuite la sauvegarde vers un emplacement privé durable et appliquer une
rétention pour éviter de remplir le disque. Sauvegarder séparément `.env` et `data/push/vapid.pem`, permissions strictes ; jamais dans Git ou une archive
de code. La base contient aussi les appareils et endpoints Push. Ne pas régénérer
silencieusement une clé VAPID perdue.

## Mise à jour et restauration

Construire la nouvelle image et exécuter les tests. Arrêter le scraper avec
`docker compose stop scraper` : le délai de quatre minutes laisse terminer le
passage en cours. Réaliser la sauvegarde, puis recréer les deux services avec
`docker compose up --build -d`. Vérifier un passage manuel et l’état des catégories.
Le cron interne reprend automatiquement. Versionner le cache du service worker
lorsque les assets changent. Une modification de schéma incompatible devra avoir
une migration explicite ; ne pas effacer la base.

Pour restaurer, arrêter les deux services avant de remplacer la base. Restaurer
les secrets et permissions, puis redémarrer avec une image compatible. Une ancienne
sauvegarde peut contenir des appareils révoqués depuis : les révoquer de nouveau
avant de rouvrir l’accès. Vérifier les abonnements et l’ambiguïté de reprise après
acceptation Push décrite dans [push.md](push.md).
