# API privée et synchronisation

L'API FastAPI/Pydantic utilise le même fichier SQLite que les scrapers. Aucun
accès à GAPS n'est réalisé depuis une route HTTP. Toutes les connexions passent
par `storage.database()`, avec transactions et délai d'attente de 10 secondes.
Le service API ne reçoit pas le fichier `.env` contenant les identifiants scolaires.

## Lancement local

```bash
docker compose up --build -d
```

Avec `DEV=true`, l’origine est `http://127.0.0.1:8000`. Avec `DEV=false`
(valeur par défaut), `APP_ORIGIN` est obligatoire et doit être
l'origine exacte vue dans le navigateur, sans chemin ni slash final. HTTPS est
obligatoire hors boucle locale. `GAPS_DATA_DIR` définit le dossier SQLite, par
défaut `data/private`, pour toutes les commandes. Ne pas exposer la configuration
HTTP de développement sur un nom public. Les en-têtes de proxy ne sont pas
acceptés automatiquement.

## Association d'un appareil

Après une première synchronisation, générer localement un code :

```bash
docker compose exec scraper python -m gaps_scraper devices pair
docker compose exec scraper python -m gaps_scraper devices list
docker compose exec scraper python -m gaps_scraper devices revoke IDENTIFIANT_APPAREIL
```

La première commande affiche volontairement un secret temporaire dans le terminal.
Ne pas la planifier ni enregistrer sa sortie dans les journaux. Le code comporte
192 bits aléatoires, expire après 10 minutes et n'est consommable qu'une fois,
y compris en cas de demandes concurrentes. Émettre un autre code pour le même
compte invalide le précédent. Seules ses empreintes SHA-256 sont conservées.
Si plusieurs comptes scolaires existent dans la base, `--account` est requis.

Le navigateur envoie le code et le nom de l'appareil dans le corps JSON de
`POST /api/pair`, jamais dans une URL. Le serveur crée une session opaque de
256 bits liée au compte de ce code. Le cookie de production `__Host-gaps_session`
est `Secure`, `HttpOnly`, `SameSite=Strict`, avec chemin `/` et sans `Domain`.
Un nom distinct sans `Secure` est utilisé uniquement en HTTP local.

La session expire de manière absolue après cinq années civiles à partir de son
association. Les sessions actives de l’ancien modèle 90 jours sont migrées ; les
sessions révoquées ou expirées ne sont pas réactivées. `/api/session` réémet le
cookie avec le temps restant, sans repousser l’expiration serveur. Le navigateur
peut limiter ou supprimer ce cookie : cinq ans décrit la validité serveur, pas
une garantie de conservation sur le téléphone. Voir la
[limite documentée par Chrome](https://developer.chrome.com/blog/cookie-max-age-expires). La révocation
locale et la déconnexion invalident immédiatement le jeton côté serveur.
Seules les empreintes des jetons sont stockées ; ils ne sont ni retournés dans
le JSON ni accessibles au JavaScript. `last_seen` est mis à jour au plus une fois
toutes les cinq minutes par appareil pour limiter les écritures.

Les tentatives d'association sont limitées globalement à 20 par fenêtre de
10 minutes, compteur conservé dans SQLite après redémarrage. Les mutations
requièrent l'en-tête `Origin` exact ; les requêtes explicitement `cross-site`
sont refusées. Aucun accès CORS n'est accordé. Les corps sont bornés à 2 Kio
(4 Kio pour une souscription Push),
les erreurs de validation n'affichent pas le contenu reçu et les accès HTTP
ne sont pas journalisés par Uvicorn. Les appareils peuvent être listés/révoqués
uniquement par les commandes locales, sans route d'administration publique.

## Routes

| Route | Usage |
| --- | --- |
| `GET /healthz` | Vivacité du processus, sans données scolaires |
| `POST /api/pair` | Code et `device_name`, puis cookie de session |
| `GET /api/session` | Nom, identifiant et expiration de l'appareil courant |
| `POST /api/logout` | Révocation de cet appareil et suppression du cookie |
| `GET /api/notes` | Notes courantes, avec modules et moyennes provisoires |
| `GET /api/notes?year=2025&semester=0` | Instantané historique déjà enregistré (0 = S1, 1 = S2) |
| `GET /api/notes/periods` | Semestres enregistrés du compte associé uniquement |
| `GET /api/absences` | Synthèse annuelle complète des absences |
| `GET /api/horaire` | Occurrences de l'horaire individuel |
| `GET /api/push/config` | Configuration publique Push et état de cet appareil |
| `POST /api/push/subscribe` | Souscrire pour cet appareil uniquement |
| `POST /api/push/unsubscribe` | Désactiver ses notifications |

Les trois réponses de données contiennent `data`, `last_sync`, `last_attempt`,
`freshness` (`fresh`, `stale`, `unavailable`) et un code d'erreur générique éventuel.
Sans données valides, `data` vaut `null` ; les données anciennes restent lisibles
après un échec. Le compte est imposé par la session, pas par un paramètre client.
Les identifiants internes du compte et les champs techniques non nécessaires
sont filtrés par les modèles Pydantic. La documentation OpenAPI publique est
désactivée. Les réponses de l'API sont `Cache-Control: no-store` : le cache
hors connexion est explicitement géré sur l'appareil, pas par un CDN partagé.

## Récupération commune

```bash
docker compose exec scraper python -m gaps_scraper sync
docker compose exec scraper python -m gaps_scraper sync --scheduled
```

La connexion GAPS est partagée en mémoire entre les trois récupérations pendant
ce passage. Le cookie disparaît à l'arrêt. Les catégories sont validées et
enregistrées indépendamment : une erreur de parsing des notes n'efface pas les
absences et n'empêche pas de récupérer l'horaire. Aucun corps de réponse, nom
d'utilisateur ou secret n'est enregistré dans les journaux. Les erreurs réseau,
de structure/session, de parsing et de stockage sont classées sans afficher
leur message brut. Les appels ne sont pas répétés automatiquement en boucle.

Une table `dataset_state` mémorise la période réellement suivie par chaque
catégorie, le dernier essai et la dernière réussite. Ainsi, un essai historique
manuel ne remplace pas la période courante de l'API. Si le processus s'interrompt
entre l'écriture d'un instantané et celle de cet état, le dernier contexte validé
reste utilisé. Avant la première synchronisation commune, l'API utilise une
lecture de migration limitée au compte et à la période la plus récente en base.

## Planification Docker

Les deux services `api` et `scraper` démarrent avec `docker compose up --build -d`.
Le scraper lance Supercronic sans privilèges, avec [config/crontab](../config/crontab).
Il planifie un processus ponctuel toutes les dix minutes de 06h00 à 23h50,
sept jours sur sept, en `Europe/Zurich` : 108 passages par jour. Le cron ne
reconstruit pas l’image et ne demande aucun cron sur l’hôte.
`--scheduled` vérifie aussi le créneau avant de joindre GAPS. Un verrou partagé
empêche le chevauchement avec un lancement manuel. La fraîcheur tient compte de
la pause nocturne et laisse trois minutes après chaque créneau pour terminer.

Les limites sont 128 Mio / 0,5 CPU pour le scraper et 256 Mio / 0,5 CPU pour l’API.
L’API monte uniquement `data/private/`, le scraper reçoit aussi le secret scolaire et le
dossier de clé Push en lecture seule. Le port local est lié à `127.0.0.1`.
Aucun service ne monte le socket Docker.

## Vérifications

Les tests Docker couvrent les cookies, cinq années civiles, migration, révocation,
consommation atomique du code, origine, limites des corps et tentatives,
conservation après échec, indépendance des catégories et file Push.
Les vérifications réelles et leurs limites sont consignées dans
[validation.md](validation.md).

## Choix de la PWA et des notifications

L'horaire est affiché à l'ouverture, avec trois onglets Horaire, Notes, Absences
et la dernière synchronisation dans les réglages. Le dernier état est consultable hors
connexion sur le téléphone. Une révocation serveur ne peut pas supprimer une
copie sur un appareil déconnecté avant son prochain contact.

Les notifications sont préparées pour les nouvelles notes publiées et les corrections,
pas les épreuves sans note ni les changements de moyenne de classe seuls. Le
texte visible indiquera la matière et nouveauté/correction, sans la valeur de la
note. Les tests réels d’installation sur iPhone et Web
Push nécessiteront un accès HTTPS sur un iPhone réel. Ils ne sont pas encore réalisés.

## Modules et consultation des archives

La réponse Notes ajoute `modules` (identité, nom, unités et coefficients, aliases,
moyenne indicative, `provisional: true`), `programme_plan` et `unmapped_courses`.
Les liens et identifiants techniques inutiles des fiches ne sont pas exposés.
Les données anciennes sans modules restent lisibles grâce aux champs optionnels.

Les paramètres `year` et `semester` doivent être fournis ensemble. La sélection
est bornée et combinée avec le compte imposé par la session ; elle ne permet pas
de choisir un autre compte, d’appeler GAPS ou de changer `dataset_state`.
La PWA affiche désormais uniquement le semestre courant et conserve sa dernière
copie hors connexion. Les routes d’archives sont conservées côté API. Les archives
ne reprennent pas une erreur récente du scraper portant sur le semestre courant.
