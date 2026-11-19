# Scraper GAPS

## Exécution Docker

Le scraper utilise HTTP directement, sans navigateur automatisé et sans code
copié du dépôt de référence. L’image utilise Python 3.13, avec base épinglée par
digest et dépendances figées dans `config/runtime-constraints.txt`.

```bash
docker compose up --build -d
docker compose exec scraper python -m gaps_scraper sync
```

`sync` réalise une connexion, récupère les trois catégories, traite la file Push
puis termine. Le conteneur reste disponible avec son cron Supercronic. Les
passages automatiques sont définis dans `config/crontab` : `*/10 6-23 * * *`,
fuseau `Europe/Zurich`. Aucune intervention sur le cron de Debian n’est nécessaire.

Pour une catégorie ou un essai historique :

```bash
docker compose exec scraper python -m gaps_scraper notes
docker compose exec scraper python -m gaps_scraper notes --year 2025 --semester S1
docker compose exec scraper python -m gaps_scraper absences --year 2025
docker compose exec scraper python -m gaps_scraper horaire
```

Les commandes individuelles enregistrent leurs résultats, mais seul `sync` met à
jour l’état commun de fraîcheur et de période servi par l’API. Utiliser `sync`
pour une actualisation complète de la PWA.

## Connexion et secrets

Le formulaire direct de `/consultation/` est vérifié avant d’envoyer les champs
`login` et `password`. L’accès étudiant est ensuite validé par son identifiant
renvoyé dans la page. La présence d’un cookie seule ne prouve pas la connexion.
Les redirections hors de `https://gaps.heig-vd.ch` sont refusées avant transmission
des identifiants ; aucune MFA, restriction d’accès ou CAPTCHA n’est contourné.
Une apparition de ces contraintes nécessite d’adapter le parcours avec l’utilisateur.

Le fichier **`.env`**, modèle `.env.example`, est l’unique configuration
manuelle. Il contient l’origine, UID/GID et les champs `GAPS_USERNAME` et
`GAPS_PASSWORD`. Compose le monte comme secret à `/run/secrets/app.env`, uniquement
dans le scraper, en lecture seule. Le fichier doit être privé : permissions 600
et propriétaire correspondant à `LOCAL_UID`/`LOCAL_GID`.

`python-dotenv` lit les identifiants sans interpolation et sans charger les autres
champs dans l’environnement du processus. Les guillemets simples préservent `$`
et `#`, les apostrophes et antislashs sont échappés selon le modèle. Les erreurs
restent génériques et n’affichent aucune valeur. Des variables de processus
`GAPS_USERNAME`/`GAPS_PASSWORD` peuvent remplacer la paire pour un usage avancé,
mais Compose ne les injecte pas : il fournit uniquement le fichier privé.
Aucun `source .env` ou traitement shell du fichier n’est utilisé.

Le secret n’entre ni dans l’image, ni dans Git, ni dans le navigateur. L’API ne
monte ni `.env` ni le dossier de clés Push. Les cookies GAPS restent en mémoire
pendant le passage et ne sont pas conservés sur disque. La session de cinq ans
est celle de la PWA, pas celle du portail scolaire.

## Périodes et récupération

Aucune année ni aucun semestre n’est codé en dur. Les notes suivent les
sélecteurs par défaut de GAPS ; les absences, l’année sélectionnée ; l’horaire,
la période du lien d’export individuel. Cela dépend du basculement du portail,
pas du calendrier supposé par le scraper. Les tests simulent les changements de
semestre et d’année ; un basculement réel du portail reste à observer.

Pour les notes, la page initiale peut afficher un résultat vide avant AJAX.
Le client appelle le mécanisme AJAX du portail avec l’étudiant authentifié et
les sélections vérifiées. Il distingue le message explicite d’absence d’épreuves
d’une structure inconnue ou d’un résultat initial non chargé.

Pour les absences, la page `/consultation/etudiant/` et son détail AJAX sont
récupérés avec le filtre « Toutes les absences » (valeur `0`). Le résultat est
une synthèse annuelle par cours et périodes, pas une liste d’événements datés.
Les nombres entre crochets désignent les absences justifiées ; une cellule vide
reste distincte du nombre zéro. Le parseur accepte les fermetures HTML omises
observées sur le portail.

Pour l’horaire, le lien d’export iCalendar de `/consultation/horaires/` est vérifié
pour l’étudiant et le mode individuel. Le code de période du calendrier n’est
pas assimilé à celui des notes : la correspondance est vérifiée par le libellé.
`icalendar` et `python-dateutil` traitent les dates, fuseaux et récurrences.
Les récurrences quotidiennes/hebdomadaires sont bornées à 400 jours et 5 000
occurrences ; EXDATE et le changement d’heure sont pris en charge. Une forme
non supportée, dont une exception RECURRENCE-ID, provoque une erreur explicite.

## Résultats et stockage

Les parseurs produisent des documents JSON `schema_version: 1`. Le stockage
ajoute `account_id`, dérivé du portail et de l’identifiant étudiant. Cela sépare
les comptes sans anonymiser les données.

- Notes : `academic_year`, `semester`, `timezone`, `courses`, `assessments`.
  Une épreuve comporte son identifiant, cours/groupe, date ISO, intitulé,
  moyenne de classe, coefficient fractionnaire et pourcentage, puis `grade`
  (`value`, `status`, `ignored`, `absent`). Les décimaux sont des chaînes,
  une note non publiée vaut `null`. Les moyennes de branches viennent de GAPS sans recalcul. Les moyennes de modules
  sont indicatives, calculées séparément comme décrit ci-dessous.
- Absences : année, filtre complet et cours avec leurs valeurs par période,
  justifications et taux relatifs/absolus. Aucune cellule vide n’est transformée
  implicitement en zéro.
- Horaire : année, période source, semestre, fuseau, séries et occurrences avec
  dates de début/fin, journée entière, lieu et identifiants stables.

Les identifiants d’épreuves sont des SHA-256 du contexte année/semestre,
matière/groupe, date et intitulé. Une correction de valeur conserve l’identité.
Une modification de date/intitulé apparaît comme disparition/découverte : ces
nouveautés simultanées ambiguës ne déclenchent pas de Push. Deux épreuves
indiscernables provoquent une erreur plutôt qu’un écrasement.

SQLite conserve les instantanés, versions connues, événements et `dataset_state`.
Les périodes sont séparées. La première synchronisation établit une référence
silencieuse ; une lecture identique n’ajoute aucun changement et ne réécrit pas
le document. Une disparition est conservée comme non confirmée, sans effacement
irréversible. Une erreur de réseau, session ou structure ne remplace jamais les
dernières données valides. Les catégories sont enregistrées indépendamment.
L’API expose la date de dernière réussite, le dernier essai et le code d’erreur.

Les deux conteneurs utilisent `./data/private:/app/data/private`, en dehors de leur couche
éphémère. Les accès passent par `common.storage.database`, avec transactions,
clés étrangères et attente maximale de verrou SQLite de dix secondes. Un verrou
`flock` partagé bloque les récupérations simultanées, y compris manuelles.
Le cron ne superpose pas non plus ses passages. Le délai HTTP est de vingt
secondes par opération, la réponse limitée à 4 Mio, sans répétition automatique
en boucle. Les journaux normaux affichent états et compteurs, sans HTML brut,
notes, identifiants ou jetons. Ils sont plafonnés par Compose à deux fichiers de 2 Mio.

## Modules du programme GAPS

Après les notes, le client consulte `/consultation/programmes/detail.php` avec
la même connexion. `notes/programme.py` lit le tableau `teachingplanbox`, les
lignes de modules/branches et la colonne Coef. Les fusions de cellules verticales
et horizontales sont prises en compte, y compris les alternatives et unités à
choix observées. Les liens de fiches, identifiants et plan sont vérifiés ; les
coefficients facultatifs absents restent `null` et ne sont pas inventés.

Les branches des notes sont associées par leur code. Une variante de code est
acceptée uniquement après vérification dans la fiche de branche liée au programme,
avec confirmation du module et du plan. Une ressemblance de préfixe sert seulement
à limiter les fiches à consulter ; elle ne valide pas une correspondance. Aucune
liste manuelle de branches/modules n’est codée en dur. Une correspondance ambiguë
ou une structure de programme inconnue provoque une erreur de notes et conserve
le dernier instantané valide. Une branche non identifiée reste visible à part.

La moyenne de module est la somme des moyennes de branches disponibles multipliées
par leurs coefficients, divisée par la somme de ces coefficients. Elle est toujours
marquée provisoire, calculée en Decimal, sans règles de validation ni arrondi
réglementaire. Les branches sans moyenne ne sont pas assimilées à zéro. Un
coefficient manquant pour une branche notée empêche le calcul du module.
Le programme consulté est celui proposé par GAPS ; utiliser ses coefficients pour
une archive ne constitue pas une vérification d’un ancien règlement de formation.

Ajouter/changer ces métadonnées modifie l’instantané mais ne crée aucun événement
de publication/correction de note. La première synchronisation silencieuse et
l’idempotence du journal des notes sont conservées.

## Diagnostic volontaire

```bash
docker compose exec scraper python -m gaps_scraper capture
docker compose exec scraper python -m gaps_scraper inspect
docker compose exec scraper python -m gaps_scraper absences --review
docker compose exec scraper python -m gaps_scraper horaire --review
```

Ces commandes peuvent écrire des pages ou JSON scolaires privés dans `data/private/`.
Ne pas les partager sans anonymisation. Les captures HTML/ICS et JSON de découverte
antérieurs ont été supprimés. Une synchronisation normale ne les recrée pas.
Le rejeu des notes via `notes --from-capture CHEMIN` écrit une base séparée
`notes-replay.sqlite3`, sans donner aux anciennes pages une nouvelle date de
synchronisation active. `inspect` observe la sélection et l’état des résultats,
sans prétendre valider une nouvelle connexion réseau.

## Validation

Voir [validation.md](validation.md) pour les tests Docker, la récupération réelle
et les limites. Les tests utilisent des exemples synthétiques/anonymisés ; les
captures scolaires privées ne sont pas dans le dépôt. Les sauvegardes cohérentes,
limites de ressources et mises à jour sont décrites dans [deploiement.md](deploiement.md).
