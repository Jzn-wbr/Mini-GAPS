# Notifications Web Push

## Conditions iOS vérifiées dans les sources officielles

WebKit documente le support depuis iOS/iPadOS 16.4 pour une application ajoutée
à l'écran d'accueil. La demande d'abonnement doit découler d'une action explicite
de l'utilisateur. La réception s'effectue par le service worker, même sans page
ouverte ; chaque push doit produire une notification visible, pas un traitement
silencieux. Les réglages système et modes Concentration interviennent dans la
présentation. Sources : [WebKit iOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/),
[WebKit Web Push](https://webkit.org/blog/12945/meet-web-push/).

La livraison n'est pas instantanée ni garantie dans tous les états du téléphone.
La documentation Apple précise les en-têtes VAPID, les destinations sous
`*.push.apple.com`, la limite de charge et les erreurs du service :
[documentation Apple](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers.md).
Le code détecte les API disponibles et le mode installé plutôt que de se fonder
sur un numéro de version annoncé par le navigateur.

## Configuration locale

Pour l’origine configurée dans `APP_ORIGIN`, générer les clés une
seule fois avec une URL de contact HTTPS valide ou une adresse `mailto:` :

```bash
docker compose run --rm --no-deps scraper push init --subject https://mini-gaps.example.org
```

Remplacer le domaine fictif de la commande par l’origine de son `.env`.

Aucun override de configuration n’est nécessaire. La commande écrit la clé privée
générée dans `data/push/vapid.pem` (600), monté uniquement dans le scraper, et la
clé publique ainsi que le contact dans SQLite. Le seul fichier à renseigner reste
`.env`, monté en lecture seule. L’API monte seulement `data/private/` et n’accède
pas au PEM. `GAPS_VAPID_KEY` permet de remplacer le chemin dans un usage avancé.

La commande est idempotente et ne remplace pas silencieusement une clé perdue :
restaurer sa sauvegarde. Les clés actives n’ont pas encore été générées pendant
le développement ; les générer puis les sauvegarder sur le serveur cible.
Cette opération ne crée ni DNS ni tunnel.

L'API ne lit pas le PEM privé. `GET /api/push/config` fournit à un appareil associé
la clé publique, l'état de configuration et son propre état d'abonnement.
`POST /api/push/subscribe` enregistre sa souscription ; `POST /api/push/unsubscribe`
la désactive. Les mutations ont les mêmes protections de session/origine que le
reste de l'API. Les corps de souscription sont limités à 4 Kio.

L'interface précharge la clé et le service worker avant d'activer le bouton.
`pushManager.subscribe()` est appelé directement dans le clic, avant tout fetch
qui pourrait perdre le geste utilisateur sur iOS. Le consentement n'est jamais
demandé automatiquement à l'ouverture.

## Détection et contenu

Le journal `changes` des notes est la source des événements. La première
synchronisation d'une période crée uniquement une référence. Un abonnement
mémorise la dernière séquence existante lors de son activation : aucun historique
ancien n'est annoncé lors de l'association ou de l'activation des notifications.

Sont retenues : les épreuves découvertes avec une note publiée, la publication
d'une note jusque-là absente et les corrections d'une note déjà publiée. Les
changements de moyenne de classe seuls et les épreuves encore sans note ne
notifient pas. Avec les identifiants provisoires fondés sur date/intitulé, une
disparition et une découverte simultanées peuvent être un renommage. Ces
nouveautés ambiguës ne sont pas notifiées automatiquement pour éviter les faux
doublons ; une véritable nouveauté dans ce même passage peut donc être retenue.
Les données restent visibles dans la PWA. Un identifiant source stable reste
préférable si GAPS en expose un vérifiable.

Le message contient uniquement la matière et « nouvelle note » ou « note
corrigée ». Il ne contient ni valeur, moyenne, nom d'étudiant ni description
d'épreuve. Le clic ouvre les notes ; leur lecture nécessite toujours la session.

## File persistante et envoi

Les tables `push_subscriptions`, `push_outbox`, `push_config` et
`push_vapid_headers` sont dans la même base SQLite. La file possède une unicité
`(abonnement, événement)`. Le message conserve un tag stable lors des tentatives.
Une reprise du processus ne recrée pas les envois déjà marqués acceptés.

Le traitement est exécuté après la récupération commune, même si GAPS vient
d'échouer, et utilise le même verrou. Il peut aussi être lancé volontairement :

```bash
docker compose exec scraper python -m gaps_scraper push send
```

Au plus 20 messages sont traités par passage. Un bail de cinq minutes permet la
reprise après arrêt ; un envoi possède un délai réseau de dix secondes. Les
échecs transitoires sont reportés avec temporisation croissante et prise en
compte de `Retry-After`, cinq tentatives
maximum ; les messages vieux de plus de 24 heures sont abandonnés. Le TTL
fournisseur est de 24 heures. Les statuts 404/410 désactivent la souscription,
sans présumer de la raison précise de disparition. Les destinations sont limitées
aux hôtes HTTPS connus Apple, FCM et Mozilla ; les redirections sont refusées.
Les abonnements ne servent pas de proxy vers une URL arbitraire.

Les en-têtes VAPID signés sont réutilisés par origine pendant une heure, y compris
entre passages du cron, pour respecter la recommandation Apple de ne pas les
régénérer trop souvent. Ils ne sont jamais exposés à l'API. Les clés
d'abonnement, endpoints opaques et corps d'erreurs des fournisseurs ne sont pas
journalisés. Le chiffrement et l'envoi utilisent
[pywebpush](https://github.com/web-push-libs/pywebpush).

Un arrêt ou un timeout après acceptation par le fournisseur mais avant le commit
local laisse une ambiguïté. Le tag stable vise à remplacer un doublon visible,
mais aucune livraison « exactement une fois » de bout en bout n'est promise.
Une révocation interdit les futurs traitements ; elle ne peut pas retirer un
message déjà accepté et en transit chez le fournisseur.

Le délai inclut l'attente du prochain passage de récupération, puis le transport
Push. Une note apparue après 23h50 attend normalement le passage de 06h00. La
PWA fermée n'empêche pas ces passages côté serveur.

## Validation

Les tests couvrent la première référence silencieuse, les publications et
corrections, les métadonnées seules, les renommages ambigus, l'isolation des
comptes, l'idempotence de la file, la reprise, les délais de retry, la révocation,
les abonnements expirés et la réutilisation des signatures VAPID.
Le chiffrement réel de pywebpush a été exécuté avec une session HTTP simulée,
vérifiant délai et refus de redirection, sans contacter un fournisseur.

La permission et la souscription du test Chromium sont simulées. Aucun envoi
réel ni réception sur iOS n'a été validé. Ces étapes nécessitent le déploiement
HTTPS autorisé et l'iPhone. Il faudra vérifier : consentement depuis la PWA
installée, première notification, correction, répétition d'un passage sans
nouveau message, redémarrage du scraper, écran verrouillé, application fermée,
mode Concentration et comportement hors réseau. La fermeture forcée et les
économies d'énergie doivent être observées, pas supposées.
