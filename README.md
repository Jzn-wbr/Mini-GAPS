# Mini GAPS

PWA personnelle pour les notes, les absences annuelles et l’horaire individuel.
L’API privée et le scraper utilisent une base SQLite persistante. Le scraper
fonctionne même quand la PWA est fermée.

Le nom du projet Compose est fixé à `mini-gaps`, indépendamment de son dossier.

## Développement avec Docker

Deux services utilisent la même image Python et les mêmes versions de dépendances :

- `api` sert la PWA et l’API FastAPI/Pydantic. Il ne reçoit aucun secret scolaire.
- `scraper` exécute un cron interne : toutes les dix minutes de 06h00 à 23h50,
  heure suisse. Chaque récupération termine puis laisse seulement le cron en attente.

Depuis ce dossier, avec Docker Desktop démarré :

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail 30 scraper
docker compose exec scraper python -m gaps_scraper sync
```

Ouvrir **http://127.0.0.1:8000**. Pour associer le navigateur :

```bash
docker compose exec scraper python -m gaps_scraper devices pair
```

Coller le code affiché dans la PWA. Il est valable dix minutes et une seule fois.
La session de l’appareil est valable **cinq années civiles**, révocable ; le
navigateur peut toutefois supprimer ses cookies et demander une nouvelle association.
L’horaire s’ouvre par défaut, avec les onglets Horaire, Notes, Absences.

```bash
docker compose exec scraper python -m gaps_scraper devices list
docker compose exec scraper python -m gaps_scraper devices revoke IDENTIFIANT
docker compose down
```

`down` arrête les services sans effacer `data/`. Après modification du code,
relancer `docker compose up --build -d`. Aucun venv n’est nécessaire.

Sur un nouveau clone : créer `data/private/` et `data/push/` en permissions 700,
puis copier [.env.example](.env.example) vers **`.env`**. C’est le seul
fichier de configuration à renseigner : mode `DEV`, UID/GID, `GAPS_USERNAME` et
`GAPS_PASSWORD`, ainsi que `APP_ORIGIN` en production. Lui donner les permissions
600. Ne pas écraser un `.env` existant.

Compose utilise les paramètres publics de `.env` et monte ce même fichier comme
secret en lecture seule uniquement dans le scraper. L’API ne reçoit ni le fichier
ni les identifiants scolaires. Les mots de passe restent littéraux : garder les
guillemets simples du modèle et échapper les apostrophes/antislashs comme indiqué.
Ne jamais publier ce fichier ni envoyer son contenu dans la conversation.

Après modification de `.env`, recréer les services pour appliquer la configuration :

```bash
docker compose up -d --force-recreate
```

## Mode développement ou production

Dans le seul fichier `.env` :

- `DEV=true` : origine `http://127.0.0.1:8000`, messages de démarrage API et
  comptes rendus techniques des passages du cron.
- `DEV=false` : origine HTTPS obligatoire définie par `APP_ORIGIN`, journaux automatiques
  limités aux avertissements et erreurs ; les passages réussis sont silencieux.

Sans variable, le mode production est retenu. Après changement, recréer les
services avec `docker compose up -d --force-recreate`. `APP_ORIGIN` est facultatif
en développement et obligatoire en production (HTTPS, sans chemin ni slash final).
Aucun domaine de déploiement n’est codé dans le projet.
Les journaux n’affichent pas les identifiants, cookies ou contenus scolaires.
Les logs d’accès HTTP restent désactivés dans les deux modes. Les commandes
manuelles, dont `sync` et `devices pair`, gardent leur sortie dans le terminal.
Les journaux Docker restent plafonnés dans les deux modes.

Le Dockerfile contient plusieurs **cibles de construction**. Compose sélectionne
`runtime`, issue de `base` : **ni Playwright, ni Chromium, ni les dépendances de
tests** n’entrent dans les conteneurs API et scraper, même avec `DEV=true`.
`tests` et `browser-tests` produisent des images distinctes pour la validation ;
elles ne tournent pas en permanence. Une image de test conservée occupe du disque,
mais aucun CPU ni RAM quand aucun conteneur ne l’exécute. SQLite est un fichier
partagé par les deux services, sans conteneur de base de données supplémentaire.

## Stockage et organisation

`data/private/notes.sqlite3` contient les dernières données valides, les changements,
les appareils et la file de notifications. Les deux services partagent `data/private/`
avec transactions SQLite et verrou de récupération ; aucun troisième conteneur DB.
Un échec de connexion ou de parsing conserve les données valides et affiche l’erreur.
Sans option historique, les périodes sont celles sélectionnées par GAPS.

```text
src/gaps_scraper/
  portal/          Client HTTP et connexion scolaire
  notes/           Récupération, parsing et stockage des notes
  absences/        Récupération, parsing et stockage des absences
  schedule/        Export iCalendar et horaire individuel
  common/          SQLite, secrets locaux, DOM et état de synchronisation
  jobs/            Commandes et planification cron
  api/             FastAPI, modèles Pydantic et appareils
  notifications/   Configuration, file et envoi Web Push
  diagnostics/     Capture volontaire et inspection hors connexion
  web/             Interface PWA et service worker
```

Les petits modules séparent réseau, parsing et stockage pour les tester indépendamment.
Les clés Push générées sont dans `data/push/`, monté uniquement dans le scraper ;
ce sont des données persistantes produites par l’application, sans second fichier
de configuration à remplir.
Les fichiers temporaires ne sont pas dans le code. `.env`, `data/` et l’ancien `.venv/` sont hors Git et hors contexte de construction Docker.

## Consultation

L’interface s’ouvre sur l’horaire, avec trois onglets en bas et un engrenage
pour les réglages. Les trois catégories sont relues auprès de l’API à l’ouverture
et au retour dans l’application. Changer d’onglet utilise les données conservées,
sans requête supplémentaire ni contrôle périodique. Tirer vers le bas depuis le
haut de la page déclenche une relecture, avec un petit cercle pendant l’attente.
Cela ne lance pas le scraper.
Les absences montrent les deux taux non justifiés, relatif et absolu, colorés
selon les seuils 15/30 % et triés par taux absolu décroissant.

Les notes sont regroupées par modules issus du programme GAPS et de leurs fiches,
sans correspondances manuelles. Les moyennes de modules sont pondérées sur les
branches disponibles et marquées provisoires. Les branches sont repliées par
défaut ; les pastilles signalent les notes nouvelles/corrigées jusqu’à ouverture
de la branche, avec état de lecture conservé sur l’appareil.

Les notes affichent uniquement le semestre courant sélectionné par GAPS. Les
archives restent conservées dans SQLite, sans sélecteur dans l’interface. Les
réglages conservent l’activation des notifications ; le bouton d’aperçu a été
retiré après validation du rendu.

## Tests

Les tests s’exécutent dans une étape Docker utilisant la même base que l’application :

```bash
docker build --target tests -t gaps-school-tests:local .
```

Playwright est réservé au test optionnel de l’interface, décrit dans
[la documentation PWA](docs/pwa.md). Il est absent des dépendances de l’application
et de l’image normale : le scraper utilise directement HTTP, sans navigateur.
La base Python est fixée par digest, les dépendances runtime par versions.

## Documentation et limites

- [Scraper, données et diagnostic](docs/scraper.md)
- [Accès privé, API et sessions](docs/api.md)
- [PWA et hors connexion](docs/pwa.md)
- [Web Push et conditions iOS](docs/push.md)
- [Transmission pour le déploiement](docs/deploiement.md)

Le domaine et le raccordement réseau sont propres à chaque déploiement. Renseigner
l’origine publique dans le `.env` privé. L’installation et les notifications
réelles sur iPhone restent à valider après publication HTTPS.
