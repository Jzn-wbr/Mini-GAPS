# PWA personnelle

L'interface HTML/CSS/JavaScript est servie par le même service FastAPI que
`/api/`. Elle ne dépend d'aucun framework JavaScript, CDN, police distante ou
outil Node en production. Les fichiers se trouvent dans `src/gaps_scraper/web/`
et sont inclus dans le paquet Python ainsi que dans l'image Docker.

## Style Bleu minimal

La direction [Bleu minimal](design/05_bleu_minimal.txt) est appliquée : fond
porcelaine `#F7F8FA`, surfaces blanches, accent bleu ardoise `#3A6282`, textes
et bordures gris bleuté, ombres de cartes très discrètes. Les dimensions,
espacements, typographie, contenu et comportements existants sont conservés.

Les variables CSS séparent la couleur de marque `--accent` du vert sémantique
`--green` : les taux d’absence et les états restent verts/orange/rouges selon
leur sens. Les pastilles de notes non lues restent bleues.
Le SVG du logo garde sa géométrie et prend les couleurs de la palette ; les
icônes PNG 180/192/512 px sont réexportées depuis ce SVG. Le manifeste et la
couleur du navigateur sont harmonisés avec le thème, sans changer le nom de l’app.

## Consultation

Trois onglets restent en bas : Horaire, Notes, Absences. L’horaire s’ouvre par
défaut. Les titres sont directs, sans bandeau « Mon école » ni sous-titres
décoratifs. L’engrenage ouvre les réglages : notifications et déconnexion.
L’état et la date de synchronisation de la page consultée se trouvent dans les
réglages. Les messages d’erreur, de données anciennes et de hors connexion
restent visibles sur la page concernée.

- Horaire : navigation journée/semaine, retour à aujourd’hui et salles. Les
  descriptions dépliables de cours ne sont plus affichées.
- Notes : modules encadrés, branches dépliables et repliées par défaut à
  l’ouverture. Les détails et moyennes sont conservés. Regroupement par modules lus
  dans le programme GAPS, coefficients de
  branches et moyenne de module **provisoire** pondérée sur les moyennes disponibles.
  Les moyennes de branches viennent de GAPS ; leur calcul n’est pas réimplémenté.
  Les modules couvrent parfois plusieurs semestres : la moyenne présentée concerne
  uniquement les branches disponibles du semestre affiché et n’est pas une note
  officielle ni une décision de validation. Les règles de seuils, examens et
  arrondis réglementaires ne sont pas simulées.
- Les notes affichent uniquement le semestre courant fourni par GAPS, dont le
  libellé reste visible. L’ancienne sélection historique enregistrée dans le
  navigateur est ignorée. Les archives sont conservées dans SQLite et restent
  consultables par les routes privées de l’API, sans sélecteur dans la PWA.
- Absences : pour chaque branche, uniquement le taux relatif non justifié et le
  taux absolu non justifié, affichés en principal. Les noms abrégés de fin de
  libellé sont retirés de l’affichage. Tri par taux absolu non justifié décroissant ;
  chaque taux est vert sous 15 %, orange de 15 à moins de 30 %, rouge dès 30 %. Le scraper conserve néanmoins
  la synthèse complète et distingue toujours les cellules vides des zéros.

Il n’y a pas de bouton d’actualisation ni de contrôle périodique côté navigateur.
L’ouverture et le retour au premier plan (focus, visibilité ou restauration de
page) relisent les trois catégories via l’API. Les événements de reprise rapprochés
sont regroupés ; une lecture déjà en cours n’est pas dupliquée. Les onglets, les
glissements horizontaux et les changements de jour/semaine utilisent les données
conservées, sans appel à l’API. Un clic sur une notification relit aussi les données.

Si l’application reste ouverte sans interaction, les nouvelles données seront
consultées à la prochaine reprise de focus ou au geste manuel. Le retour du réseau
seul ne provoque pas de relecture des trois catégories ; la déconnexion différée
continue toutefois à révoquer la session au retour du réseau. Le scraper garde sa
propre planification, indépendante de ces lectures.

## Tirer pour actualiser

Un glissement vertical vers le bas, commencé tout en haut de la page et dépassant
72 px, relit Notes, Horaire et Absences en un seul lot. Un petit cercle près de
l’engrenage suit le geste puis tourne pendant les requêtes. Il n’occupe pas de
place dans le contenu et disparaît à leur fin, y compris en cas d’erreur.
Les dernières données valides restent affichées. Un mouvement court ou commencé
plus bas dans la page ne déclenche aucune requête, même s’il finit au sommet.

Un second geste pendant le chargement ne lance pas un nouveau lot. Les mouvements
horizontaux continuent à naviguer ; le défilement vertical ordinaire est conservé.
`overscroll-behavior-y: contain` et l’annulation du mouvement capturé limitent le
rafraîchissement natif du document : le geste doit appeler l’API, pas recharger
la page entière. Le contrôle d’overscroll est décrit dans la
[documentation WebKit](https://webkit.org/blog/13152/webkit-features-in-safari-16-0/#overscroll-behavior).
Le ressenti et les gestes natifs restent à confirmer sur Safari réel.

L’indicateur dispose d’un texte réservé au lecteur d’écran, le contenu annonce
son état occupé et l’animation respecte la préférence de réduction des mouvements.
Les documents identiques ne sont ni réécrits dans IndexedDB ni réaffichés à chaque
lecture. Aucun de ces gestes n’exécute une récupération scolaire.

## Glissements tactiles

Un glissement vers la gauche avance ; vers la droite, il recule.

- Sur toute la page, y compris les titres, marges, espaces vides et barre de
  navigation inférieure, il sélectionne l’onglet voisin dans l’ordre Horaire,
  Notes, Absences. Aucun rebouclage de la dernière à la première page.
- Sur Horaire, les zones calendrier font exception : la bande des sept jours
  change la semaine ; la liste des cours (ou son message de journée vide) et les
  contrôles de date changent le jour. Le bouton « Aujourd’hui », encadré pour
  signaler son caractère interactif, revient à la date actuelle.

La reconnaissance utilise les événements tactiles natifs, sans dépendance ni
animation permanente. Elle exige un mouvement principalement horizontal d’au
moins 60 px, terminé en 800 ms maximum. Les gestes verticaux, diagonaux dominés
par le vertical, à plusieurs doigts, dans les champs de saisie et dans les réglages ne
changent pas de page. Les gestes sont écoutés sur le document entier.
Le clic résiduel après un glissement est filtré afin d’éviter d’ouvrir une branche
ou de sélectionner un jour par accident ; un nouveau toucher reste disponible.

Le zoom de page par pincement ou double toucher est désactivé : viewport à
échelle fixe, `touch-action: pan-y` et gardes tactiles pour Safari. Le défilement
vertical reste disponible et deux touchers rapides sur un bouton restent des
actions utilisables. Les fonctions d’agrandissement du système d’exploitation
restent sous le contrôle de l’utilisateur.

Les contrôles tactiles sont vérifiés dans Chromium. Les gestes système et le
ressenti Safari doivent encore être observés sur l’iPhone réel.

## Notes non lues

Une pastille bleue indique les notes publiées nouvelles ou corrigées, sur la
branche repliée, le module et l’épreuve concernée. Ouvrir une branche marque les
versions de ses notes affichées comme lues. Revenir à la page, replier la branche
ou recevoir une réponse API ne marque pas une nouveauté comme lue.

Les repères de lecture sont conservés dans IndexedDB sur cet appareil, séparés
par année et semestre, et effacés avec la déconnexion. Ils sont indépendants du
journal de changements et du Web Push côté serveur. La première consultation
d’une période établit une référence silencieuse. Les épreuves sans note publiée
et les seuls changements de moyenne de classe ne créent pas de pastille.
Une correction ultérieure réactive la pastille. Les disparitions temporaires
n’effacent pas les versions déjà lues.

Les branches ouvertes restent ouvertes pendant une relecture automatique ; cela
ne marque pas automatiquement les nouvelles versions comme lues. Il faut alors
refermer puis rouvrir la branche. Après un rechargement, les branches sont à
nouveau repliées, tandis que les repères de lecture sont conservés.

## Association et confidentialité

La page publique ne contient que l'interface générique et le formulaire
d'association. Les écrans personnels sont remplis après vérification de
`/api/session`. Le code temporaire est envoyé dans un POST JSON, effacé du
formulaire après usage et n'est pas placé dans une URL. Le mot de passe scolaire
n'est jamais saisi dans la PWA. Le cookie HttpOnly reste inaccessible au JavaScript.
Sur iPhone, l'installation sur l'écran d'accueil est recommandée avant de saisir
le code : l'association se fait ainsi dans le contexte de la PWA installée.

Le service worker conserve uniquement la coque de l'application : HTML, CSS,
JavaScript, manifeste et icônes. Il exclut `/api/` de son cache. Le dernier
instantané des trois catégories est conservé séparément dans IndexedDB, sur
l'appareil, avec l'identifiant non secret et la date d'expiration de la session.
Ce stockage privé n'est pas un chiffrement applicatif : sa confidentialité
repose notamment sur la protection du téléphone et de son profil de navigateur.
Le système peut purger ce stockage ; l'accès hors connexion n'est pas garanti
indéfiniment. La suppression des données du site ou des cookies peut nécessiter
une nouvelle association. Aucun jeton de session n'est stocké dans localStorage ou IndexedDB.

Hors réseau, l'interface peut lire cette dernière copie pour une session locale
non expirée. En ligne, un refus 401 efface cette copie et demande une nouvelle
association. Un changement de session efface aussi les données de la session
précédente. La déconnexion efface les données locales et les éléments scolaires
du DOM. Si elle survient hors réseau, un marqueur persistant bloque leur
réouverture et la révocation serveur est envoyée au prochain contact. Une
révocation distante ne peut pas effacer immédiatement une copie sur un téléphone
déconnecté ; cette limite a été discutée et acceptée.

## Installation et notifications

### Mise à jour de l’interface

Les URL CSS/JavaScript portent la version de la coque, identique à celle du
cache du service worker. L’installation recharge explicitement les fichiers et
la recherche d’une nouvelle version du worker ignore le cache HTTP. Une reprise
de focus vérifie les mises à jour, au plus une fois par minute et sans minuterie
permanente. Lorsqu’une nouvelle coque remplace un ancien cache, les fenêtres
sont rechargées sans effacer les cookies, IndexedDB ou les abonnements Push.
La version est visible dans les réglages et sur l’écran d’association.
Une interface ancienne déjà ouverte doit d’abord contacter le serveur ou être
réouverte réellement pour recevoir ce nouveau mécanisme.

Le manifeste utilise le mode `standalone`, une portée `/` et des icônes PNG
192/512 pixels ainsi qu'une icône Apple 180 pixels. HTTPS sera nécessaire sur
l'iPhone ; le HTTP de boucle locale ne constitue qu'un environnement de test
sur l'ordinateur. Les conditions officielles et les tests Web Push sont détaillés
dans [push.md](push.md).

L'utilisateur a choisi : horaire au démarrage ; notifications des nouvelles notes
et corrections ; matière et nature du changement sur l'écran verrouillé, sans
valeur de note. Le bouton de notification est dans « Réglages ». Sur iOS,
une visite dans Safari affiche l'indication d'ajouter l'application à l'écran
d'accueil. Aucune demande d'autorisation n'est lancée automatiquement.

## Vérifications Docker

Les routes statiques et le manifeste sont vérifiés par les tests Python.
Le parcours navigateur optionnel s’exécute dans une étape Docker séparée :

```bash
docker build --target browser-tests -t gaps-school-browser-tests:local .
```

Cette étape installe Playwright et Chromium uniquement dans l’image de test.
L’image normale `runtime`, utilisée par les deux services Compose, reste sans
Playwright ni navigateur. Le test utilise des données synthétiques et une base
temporaire ; il ne contacte ni GAPS ni un fournisseur Push.

Il couvre association, regroupement par modules, semestre courant malgré une
ancienne sélection d’archive, relecture automatique et pause quand la page est
cachée, branches repliables et pastilles persistantes, absences simplifiées,
hors connexion, déconnexion différée, révocation et activation Push dans le geste
utilisateur. Le bouton de test des notifications a été retiré après validation
manuelle de l’aperçu. L’autorisation et l’abonnement Push sont simulés dans le
parcours automatisé ; ce test ne prouve pas une livraison réelle par un fournisseur.

Il reste à tester sur un iPhone réel : installation, rendu Safari, conservation
de la session, mode hors connexion et réception réelle après fermeture de la PWA.
