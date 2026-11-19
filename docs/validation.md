# Validation et limites

## Vérifications réalisées

- Les tests Python dans la cible Docker `tests` couvrent le parsing des notes,
  absences et horaires, les modules, la synchronisation idempotente, les erreurs,
  les créneaux suisses, l’API, l’association et la révocation des appareils.
- Les tests de configuration couvrent le défaut production, les valeurs DEV
  invalides, l’origine obligatoire et HTTPS en production, ainsi que les journaux.
- Le parcours Chromium utilise des données synthétiques : association, trois
  vues, navigation tactile, hors connexion, état de lecture, gestes de relecture
  et absence de requêtes lors des changements d’onglet. Les autorisations Push
  et abonnements sont simulés ; aucun fournisseur n’est contacté.
- L’image runtime a été vérifiée sans Playwright ni navigateurs de test.
- Une récupération authentifiée des trois catégories et leur lecture par l’API
  ont été vérifiées pendant le développement. Les captures, données scolaires,
  secrets, identifiants de machine et résultats détaillés ne sont pas publiés.
  Cela ne valide pas la connexion depuis un nouvel environnement de déploiement.
- Les icônes MG sont servies dans les tailles attendues, les chemins du manifeste
  sont accessibles et le service worker utilise le cache v11.

## Reproduire les tests

```bash
docker build --target tests -t gaps-school-tests:local .
docker build --target browser-tests -t gaps-school-browser-tests:local .
docker compose config --quiet
```

Les tests de parsing et de changements utilisent des exemples anonymisés. Les
réussites synthétiques ne prouvent pas la connexion au portail réel.

## À vérifier pour chaque déploiement

- Droits des volumes, configuration privée, synchronisation réelle et accès HTTPS.
- Consommation au repos et en pic sur la machine cible, sauvegarde et restauration.
- Installation sur iPhone, gestes Safari, conservation du cookie et hors connexion.
- Consentement et réception réelle du Web Push, écran verrouillé et PWA fermée.
- Basculement réel des périodes par GAPS ; les tests de basculement sont synthétiques.

La validité serveur de cinq ans ne garantit pas la conservation du cookie par le
navigateur. Ne jamais versionner `.env`, la base, les captures ou la clé Push.
