# Captures de démonstration

Les trois PNG sont des captures directes du frontend de Mini GAPS dans Chromium,
avec un écran mobile de 390 × 844 pixels CSS et une densité de 2. Le navigateur
exécute le code de l’application sans remplacement du HTML ni retouche des images.

La démonstration utilise une API FastAPI locale et une base SQLite temporaire,
remplie à partir des exemples de tests et de données entièrement inventées.
Le programme de démonstration passe par le parseur du programme pour regrouper
les branches. La date de consultation est fixée au 16 novembre 2026 pour rendre
les captures reproductibles. Il ne s’agit pas des notes ou absences d’une personne.

Aucun secret, fichier `.env`, volume de production, portail scolaire ou service
Push n’est utilisé. Le script bloque les requêtes navigateur hors de son origine
locale et détruit la base temporaire à la fin.

## Reproduire avec Docker

Construire l’image de test navigateur (Playwright reste absent de l’image runtime) :

```bash
docker build --target browser-tests -t gaps-school-browser-tests:local .
```

Depuis la racine du projet :

```bash
docker run --rm --shm-size=256m \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD/docs/screenshots,dst=/artifacts" \
  --entrypoint python gaps-school-browser-tests:local \
  /app/tests/capture_demo.py --output /artifacts
```

Seul le dossier des captures est monté en écriture. La création des PNG vérifie
que les trois cours de l’horaire, les deux modules, les trois branches de notes
et les trois branches d’absences sont affichés sans erreur JavaScript.
