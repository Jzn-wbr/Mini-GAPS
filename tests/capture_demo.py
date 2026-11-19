"""Captures du vrai frontend, avec API locale et SQLite de démonstration jetable."""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, expect

from gaps_scraper.absences.store import synchronize_absences
from gaps_scraper.api.auth import issue_pairing
from gaps_scraper.notes.programme import enrich_notes
from gaps_scraper.notes.store import synchronize
from gaps_scraper.schedule.store import synchronize_schedule
from test_absences import document as absence_document
from test_notes import course, document
from test_programme import Client, module, programme, unit


def seed(path, day):
    courses = [('ANA', 'Analyse', '5.2', 'Dérivées et intégrales'),
               ('ALG', 'Algèbre', '4.8', 'Espaces vectoriels'),
               ('PROG', 'Programmation', '5.5', 'Structures de données')]
    html = ''
    for code, name, mean, title in courses:
        html += course(name=code, description=title, day=(day-timedelta(days=3)).strftime('%d.%m.%Y'), grade=mean).replace(
            'moyenne hors examen : 5.00', f'moyenne hors examen : {mean}').replace('moyenne : 5.0', f'moyenne : {mean}')
    notes = document(html)
    notes['academic_year'] = {'value': '2026', 'label': '2026-2027'}
    rows = module('11', 'MATH', 'Mathématiques')
    rows += unit('ANA', '21', '2').replace('Branche fictive', 'Analyse')
    rows += unit('ALG', '22', '1').replace('Branche fictive', 'Algèbre')
    rows += module('12', 'INFO', 'Informatique')
    rows += unit('PROG', '23', '3').replace('Branche fictive', 'Programmation')
    enrich_notes(Client(programme(rows)), notes)
    synchronize(path, notes)

    absences = absence_document()
    absences['academic_year'] = deepcopy(notes['academic_year'])
    original = absences['courses'][0]
    absences['courses'] = []
    for index, (name, relative, absolute) in enumerate([
        ('Analyse', '18', '16'), ('Algèbre', '8', '4'), ('Programmation', '4', '2')
    ]):
        item = deepcopy(original)
        item.update(id=f'demo-{index}', name=name)
        item['relative']['unjustified_rate_percent'] = relative
        item['absolute']['unjustified_rate_percent'] = absolute
        absences['courses'].append(item)
    synchronize_absences(path, absences)

    occurrences = []
    for index, (title, start, end, room) in enumerate([
        ('Analyse', '08:15', '09:50', 'Salle A201'),
        ('Programmation', '10:15', '11:50', 'Salle B104'),
        ('Algèbre', '13:15', '14:50', 'Salle A203'),
    ]):
        def stamp(hour):
            return datetime.fromisoformat(f'{day}T{hour}:00').replace(tzinfo=ZoneInfo('Europe/Zurich')).isoformat()
        occurrences.append({'id': f'demo-{index}', 'series_id': f'demo-series-{index}',
                            'summary': title, 'description': '', 'location': room,
                            'status': 'CONFIRMED', 'all_day': False, 'start': stamp(start), 'end': stamp(end)})
    synchronize_schedule(path, {'schema_version': 1, 'account_id': 'synthetic-account',
        'academic_year': notes['academic_year'], 'semester': notes['semester'],
        'source_period': 'demo', 'timezone': 'Europe/Zurich', 'series': [], 'occurrences': occurrences})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mini-gaps-demo-') as tmp:
        path = Path(tmp) / 'notes.sqlite3'
        day = datetime(2026, 11, 16).date()
        seed(path, day)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        env = {key: value for key, value in os.environ.items() if not key.startswith(('GAPS_', 'APP_ORIGIN'))}
        env.update(DEV='true', APP_ORIGIN=origin, GAPS_DATA_DIR=tmp, PYTHONDONTWRITEBYTECODE='1')
        server = subprocess.Popen([sys.executable, '-m', 'gaps_scraper', 'api', '--port', str(port)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                if server.poll() is not None:
                    raise RuntimeError('Le serveur de démonstration ne démarre pas.')
                try:
                    with urlopen(origin + '/healthz', timeout=1):
                        break
                except (OSError, URLError):
                    time.sleep(.1)
            else:
                raise RuntimeError('Délai de démarrage dépassé.')
            code = issue_pairing(path)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={'width': 390, 'height': 844},
                    device_scale_factor=2, is_mobile=True, has_touch=True, locale='fr-CH', timezone_id='Europe/Zurich')
                # Aucune requête sortante vers GAPS, un fournisseur Push ou une autre origine.
                context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.clock.install(time=datetime(2026, 11, 16, 7, 45, tzinfo=ZoneInfo('Europe/Zurich')))
                page.goto(origin)
                page.locator('#pair-code').fill(code)
                page.locator('#device-name').fill('Démonstration')
                page.locator('#pair-submit').click()
                expect(page.locator('.event-card')).to_have_count(3)
                page.wait_for_load_state('networkidle')
                expect(page.locator('#notice')).to_be_hidden()
                page.screenshot(path=str(args.output / 'horaire.png'), animations='disabled')
                page.locator('[data-tab="notes"]').click()
                expect(page.locator('.module-group')).to_have_count(2)
                expect(page.locator('.course')).to_have_count(3)
                page.locator('.course summary').first.click()
                expect(page.locator('.assessment').first).to_be_visible()
                page.screenshot(path=str(args.output / 'notes.png'), animations='disabled')
                page.locator('[data-tab="absences"]').click()
                expect(page.locator('.absence-course')).to_have_count(3)
                page.screenshot(path=str(args.output / 'absences.png'), animations='disabled')
                assert not errors, errors
                browser.close()
            print('Trois captures du frontend réel, API locale, données fictives ; aucun accès au portail ou au Push.')
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    main()
