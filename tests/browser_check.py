"""Vérification facultative Chromium, uniquement avec des données fictives."""

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
from urllib.request import urlopen
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, expect

from gaps_scraper.absences.store import synchronize_absences
from gaps_scraper.api.auth import issue_pairing, revoke_device
from gaps_scraper.notes.store import synchronize
from gaps_scraper.schedule.store import synchronize_schedule
from gaps_scraper.notifications.sender import initialize_keys
from gaps_scraper.notifications.store import initialize_push
from test_absences import document as absence_document
from test_notes import document as notes_document
from test_push import subscription as synthetic_subscription
from test_programme import Client as ProgrammeClient, programme, module, unit
from gaps_scraper.notes.programme import enrich_notes


def swipe_touch(cdp, x, y, dx, dy=0):
    cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y,'id':1}]})
    for step in range(1,9):
        cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x+dx*step/8,'y':y+dy*step/8,'id':1}]})
        time.sleep(.012)
    cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})


def resume_read(page):
    page.wait_for_load_state('networkidle')
    page.clock.fast_forward(1000)
    page.evaluate("""() => {
        window.dispatchEvent(new Event('focus'));
        document.dispatchEvent(new Event('visibilitychange'));
        window.dispatchEvent(new Event('pageshow'));
    }""")


def pull_read(page,context,distance=110):
    cdp=context.new_cdp_session(page)
    try:swipe_touch(cdp,160,145,0,distance)
    finally:cdp.detach()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium',help='Chemin facultatif d’un Chromium déjà installé')
    parser.add_argument('--screenshot',help='Capture facultative, données entièrement fictives')
    args=parser.parse_args()
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='school-browser-') as tmp:
        private=Path(tmp);path=private/'notes.sqlite3'
        note_data=notes_document()
        literal_description='<img src=x onerror=alert("synthetic")>'
        note_data['assessments'][0]['description']=literal_description
        enrich_notes(ProgrammeClient(programme(module()+unit(code='MAT-FICTIVE'))),note_data)
        synchronize(path,note_data)
        historical=deepcopy(note_data);historical['academic_year']={'value':'2023','label':'2023-2024'}
        extra=deepcopy(historical['assessments'][0]);extra.update(id='historic-extra',description='Autre épreuve historique')
        historical['assessments'].append(extra);synchronize(path,historical)
        absences=absence_document();original=absences['courses'][0];absences['courses']=[]
        for index,(name,relative,absolute) in enumerate([
            ('Physique - Phy3-TIN','14.99','0'),('Mesures - capteurs - MES','15','14.99'),
            ('Cours orange - ORA','29.99','15'),('Cours rouge - RED','30','30')]):
            item=deepcopy(original);item.update(id=f'absence-{index}',name=name)
            item['relative']['unjustified_rate_percent']=relative
            item['absolute']['unjustified_rate_percent']=absolute
            absences['courses'].append(item)
        synchronize_absences(path,absences)
        day=datetime.now(ZoneInfo('Europe/Zurich')).date().isoformat()
        occurrences=[]
        for index,hour in enumerate(('08:15','10:15','14:00')):
            occurrences.append({'id':f'fiction-{index}','series_id':'fictional-series','summary':f'Cours fictif {index+1}',
                                'description':'Exemple de test, sans données scolaires réelles.','location':'Salle fictive',
                                'status':'CONFIRMED','all_day':False,'start':day+'T'+hour+':00+01:00',
                                'end':day+'T'+str(int(hour[:2])+1).zfill(2)+hour[2:]+':00+01:00'})
        schedule={'schema_version':1,'account_id':'synthetic-account','academic_year':{'value':'2026','label':'2026-2027'},
                  'semester':{'value':'S1','label':'Semestre 1'},'source_period':'2','timezone':'Europe/Zurich',
                  'series':[],'occurrences':occurrences}
        synchronize_schedule(path,schedule)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        env=os.environ.copy();env.update(DEV="true",GAPS_DATA_DIR=tmp,APP_ORIGIN=origin,PYTHONDONTWRITEBYTECODE='1')
        server=subprocess.Popen([sys.executable,'-m','gaps_scraper','api','--port',str(port)],env=env,
                                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if server.poll() is not None:raise RuntimeError('Server startup failed')
                try:
                    with urlopen(origin+'/healthz',timeout=1):break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Server readiness timeout')
            code=issue_pairing(path)
            initialize_push(path)
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=args.chromium,headless=True)
                context=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=2,is_mobile=True,has_touch=True)
                import json
                fake_subscription=json.dumps(synthetic_subscription())
                context.add_init_script(f"""if ('PushManager' in window) {{
                    Object.defineProperty(Notification,'permission',{{get:()=> 'granted'}});
                    PushManager.prototype.subscribe=function() {{ window.__pushGesture=navigator.userActivation.isActive; window.__currentSub={{toJSON:()=>({fake_subscription}),unsubscribe:()=>{{window.__currentSub=null;return Promise.resolve(true);}}}}; return Promise.resolve(window.__currentSub); }};
                    PushManager.prototype.getSubscription=function() {{return Promise.resolve(window.__currentSub || null);}};
                }}""")
                page=context.new_page();page.clock.install();errors=[];
                data_calls=[]
                page.on('request',lambda r: data_calls.append(urlsplit(r.url).path) if urlsplit(r.url).path in ('/api/notes','/api/horaire','/api/absences') else None)
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(origin)
                expect(page.locator('#association')).to_be_visible()
                assert page.locator('script[type=module]').get_attribute('src')=='/assets/app.js?v=13'
                assert page.locator('link[rel=stylesheet]').get_attribute('href')=='/assets/app.css?v=13'
                page.evaluate("""async () => {
                    await navigator.serviceWorker.ready;
                    if (!navigator.serviceWorker.controller) await new Promise(resolve =>
                        navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true}));
                    const legacy = await caches.open('mon-ecole-shell-v11');
                    await legacy.put('/assets/app.js',new Response('window.__staleAsset = true;',
                        {headers:{'Content-Type':'application/javascript'}}));
                }""")
                page.reload()
                expect(page.locator('#association')).to_be_visible()
                assert page.evaluate('window.__staleAsset === undefined')
                assert page.evaluate("async () => (await navigator.serviceWorker.getRegistration()).updateViaCache")=='none'
                expect(page.locator('.brand')).to_have_count(0)
                expect(page.locator('#refresh')).to_have_count(0)
                page.locator('#pair-code').fill(code);page.locator('#device-name').fill('Browser test')
                page.locator('#pair-submit').click()
                expect(page.locator('#page-title')).to_have_text('Horaire')
                expect(page.locator('.event-card')).to_have_count(3)
                expect(page.locator('.event-details')).to_have_count(0)
                page.wait_for_load_state('networkidle');initial_calls=len(data_calls)
                assert initial_calls==3,data_calls
                # Événements tactiles réels Chromium : jour, semaine et onglets.
                cdp=context.new_cdp_session(page)
                today_number=str(datetime.fromisoformat(day).day)
                title=page.locator('.day-title').bounding_box()
                swipe_touch(cdp,210,title['y']+title['height']/2,-130)
                expect(page.locator('.day-button.active strong')).to_have_text(str((datetime.fromisoformat(day)+timedelta(days=1)).day))
                expect(page.locator('#page-title')).to_have_text('Horaire')
                title=page.locator('.day-title').bounding_box()
                swipe_touch(cdp,80,title['y']+title['height']/2,130)
                expect(page.locator('.day-button.active strong')).to_have_text(today_number)
                week=page.locator('.week-strip').bounding_box()
                swipe_touch(cdp,280,week['y']+week['height']/2,-170)
                expect(page.locator('.day-button.active strong')).to_have_text(str((datetime.fromisoformat(day)+timedelta(days=7)).day))
                week=page.locator('.week-strip').bounding_box()
                swipe_touch(cdp,100,week['y']+week['height']/2,170)
                expect(page.locator('.day-button.active strong')).to_have_text(today_number)
                expect(page.locator('.event-card')).to_have_count(3)
                # Même les marges appartiennent à la navigation ; le calendrier garde son propre geste.
                edge=page.locator('.day-title').evaluate("""node=>{
                    const y=node.getBoundingClientRect().top+10;
                    const touch=x=>new Touch({identifier:9,target:node,clientX:x,clientY:y});
                    node.dispatchEvent(new TouchEvent('touchstart',{bubbles:true,touches:[touch(8)]}));
                    const move=new TouchEvent('touchmove',{bubbles:true,cancelable:true,touches:[touch(150)]});node.dispatchEvent(move);
                    node.dispatchEvent(new TouchEvent('touchend',{bubbles:true,touches:[],changedTouches:[touch(150)]}));
                    return move.defaultPrevented;
                }""")
                assert edge is True
                expect(page.locator('.day-button.active strong')).to_have_text(str((datetime.fromisoformat(day)-timedelta(days=1)).day))
                today_button=page.get_by_role('button',name='Aujourd’hui',exact=True)
                assert today_button.evaluate("n=>parseFloat(getComputedStyle(n).borderTopWidth)")>=1
                today_button.click()
                expect(page.locator('.day-button.active strong')).to_have_text(today_number)
                # Les cartes changent le jour, y compris le retour depuis une journée vide.
                event=page.locator('.event-card').first.bounding_box()
                swipe_touch(cdp,270,event['y']+event['height']/2,-150)
                expect(page.locator('#page-title')).to_have_text('Horaire')
                expect(page.locator('.timeline .empty')).to_be_visible()
                blank_day=page.locator('.timeline').bounding_box()
                swipe_touch(cdp,90,blank_day['y']+blank_day['height']/2,150)
                expect(page.locator('.event-card')).to_have_count(3)
                expect(page.locator('.day-button.active strong')).to_have_text(today_number)
                # Le titre, le fond hors contenu et la navigation basse changent les onglets.
                heading=page.locator('#page-title').bounding_box()
                swipe_touch(cdp,270,heading['y']+heading['height']/2,-150)
                expect(page.locator('#page-title')).to_have_text('Notes')
                assert page.evaluate("document.elementFromPoint(270,730).closest('#workspace') === null")
                swipe_touch(cdp,270,730,-150)
                expect(page.locator('#page-title')).to_have_text('Absences')
                navigation=page.locator('#navigation').bounding_box()
                swipe_touch(cdp,100,navigation['y']+20,160)
                expect(page.locator('#page-title')).to_have_text('Notes')
                swipe_touch(cdp,8,730,170)
                expect(page.locator('#page-title')).to_have_text('Horaire')
                expect(page.locator('.day-button.active strong')).to_have_text(today_number)
                # Les deux doigts ne naviguent pas et ne modifient pas l'échelle.
                scale=page.evaluate('visualViewport.scale')
                cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':120,'y':160,'id':1},{'x':240,'y':160,'id':2}]})
                cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':70,'y':160,'id':1},{'x':290,'y':160,'id':2}]})
                cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
                assert abs(page.evaluate('visualViewport.scale')-scale)<.01
                expect(page.locator('#page-title')).to_have_text('Horaire')
                assert page.evaluate("getComputedStyle(document.documentElement).touchAction")=='pan-y'
                assert page.evaluate("document.querySelector('meta[name=viewport]').content.includes('user-scalable=no')")
                assert page.evaluate("""() => {
                    return ['gesturestart','gesturechange','gestureend','dblclick'].every(type => {
                        const event=new Event(type,{bubbles:true,cancelable:true});
                        document.dispatchEvent(event);return event.defaultPrevented;
                    });
                }""")
                # Deux touchers rapides sur Aujourd'hui restent deux clics, sans zoom.
                page.get_by_role('button',name='Aujourd’hui',exact=True).tap()
                page.get_by_role('button',name='Aujourd’hui',exact=True).tap()
                assert abs(page.evaluate('visualViewport.scale')-scale)<.01
                expect(page.locator('.event-card')).to_have_count(3)
                page.locator('[data-tab="notes"]').click()
                header=page.locator('.course-header').bounding_box()
                swipe_touch(cdp,280,header['y']+header['height']/2,-160)
                expect(page.locator('#page-title')).to_have_text('Absences')
                card=page.locator('.absence-course').first.bounding_box()
                swipe_touch(cdp,100,card['y']+card['height']/2,160)
                expect(page.locator('#page-title')).to_have_text('Notes')
                expect(page.locator('details.course[open]')).to_have_count(0)
                header=page.locator('.course-header').bounding_box()
                swipe_touch(cdp,100,header['y']+header['height']/2,160)
                expect(page.locator('#page-title')).to_have_text('Horaire')
                page.locator('[data-tab="absences"]').click()
                card=page.locator('.absence-course').first.bounding_box()
                swipe_touch(cdp,280,card['y']+card['height']/2,-160)
                expect(page.locator('#page-title')).to_have_text('Absences')
                page.set_viewport_size({'width':320,'height':740})
                swipe_touch(cdp,160,620,0,-350)
                page.wait_for_function('() => window.scrollY > 0')
                expect(page.locator('#page-title')).to_have_text('Absences')
                page.set_viewport_size({'width':390,'height':844})
                cdp.detach()
                page.locator('[data-tab="horaire"]').click();page.evaluate('scrollTo(0,0)')
                page.wait_for_load_state('networkidle')
                assert len(data_calls)==initial_calls,'Un onglet ou un swipe a relu les données.'
                expect(page.locator('.event-card')).to_have_count(3)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                if args.screenshot:page.screenshot(path=args.screenshot,full_page=True)
                page.locator('#settings-button').click()
                expect(page.locator('#app-version')).to_have_text('Version de l’interface : 13')
                settings=page.locator('#settings-dialog').bounding_box()
                settings_cdp=context.new_cdp_session(page)
                try:swipe_touch(settings_cdp,260,settings['y']+settings['height']/2,-130)
                finally:settings_cdp.detach()
                expect(page.locator('#settings-dialog')).to_be_visible()
                expect(page.locator('#page-title')).to_have_text('Horaire')
                expect(page.locator('#notification-state')).to_have_text('Les notifications ne sont pas encore configurées.')
                expect(page.locator('#push-toggle')).to_be_disabled()
                expect(page.locator('#notification-preview')).to_have_count(0)
                config=context.request.get(origin+'/api/push/config').json()
                assert config['enabled'] is False and config['subscribed'] is False
                initialize_keys(path,private/'test-vapid.pem','https://school.example')
                page.locator('.close-dialog').click();page.locator('#settings-button').click()
                expect(page.locator('#push-toggle')).to_be_enabled()
                assert page.evaluate('typeof window.__pushGesture')=='undefined'
                page.locator('#push-toggle').click()
                expect(page.locator('#push-toggle')).to_have_text('Désactiver les notifications')
                assert page.evaluate('window.__pushGesture') is True
                page.locator('.close-dialog').click()
                page.locator('[data-tab="notes"]').click();expect(page.locator('.assessment')).to_have_count(1)
                expect(page.locator('.module-header h2')).to_have_text('Module fictif')
                expect(page.locator('.module-average span')).to_have_text('Provisoire')
                expect(page.locator('details.course[open]')).to_have_count(0)
                expect(page.locator('summary .unread-dot')).to_have_count(0)
                assert page.locator('.module-group').evaluate("n=>getComputedStyle(n).borderTopWidth")!='0px'
                expect(page.locator('#notes-period')).to_have_count(0)
                expect(page.locator('.section-caption')).to_have_text('S1 · 2024-2025')
                # Une ancienne sélection d’archive persistée n’est plus utilisée au démarrage.
                page.evaluate("""async () => {
                    const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('mon-ecole-private',1);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
                    await new Promise((resolve,reject)=>{const tx=db.transaction('state','readwrite');tx.objectStore('state').put('2023:0','notesPeriod');tx.oncomplete=resolve;tx.onerror=reject;});
                    db.close();
                }""")
                page.reload();expect(page.locator('#page-title')).to_have_text('Horaire')
                page.locator('[data-tab="notes"]').click()
                expect(page.locator('.assessment')).to_have_count(1)
                expect(page.locator('.section-caption')).to_have_text('S1 · 2024-2025')
                changed=deepcopy(note_data);changed['assessments'][0]['grade']['value']='4.5'
                synchronize(path,changed)
                page.wait_for_load_state('networkidle')
                before=len(data_calls)
                page.clock.fast_forward(60000)
                expect(page.locator('.grade')).to_have_text('5,0')
                assert len(data_calls)==before,'Une relecture périodique subsiste.'
                resume_read(page)
                expect(page.locator('.grade')).to_have_text('4,5')
                assert len(data_calls)==before+3,'Les événements de reprise doivent former un seul lot.'
                page.evaluate("window.dispatchEvent(new Event('focus'));window.dispatchEvent(new Event('pageshow'))")
                assert len(data_calls)==before+3
                expect(page.locator('summary .unread-dot')).to_have_count(1)
                expect(page.locator('.module-header .unread-dot')).to_have_count(1)
                page.reload();expect(page.locator('#page-title')).to_have_text('Horaire')
                page.locator('[data-tab="notes"]').click()
                expect(page.locator('summary .unread-dot')).to_have_count(1)
                expect(page.locator('details.course[open]')).to_have_count(0)
                page.locator('.course-header').click()
                expect(page.locator('.grade')).to_be_visible()
                expect(page.locator('.unread-dot')).to_have_count(0)
                page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true})")
                changed['assessments'][0]['grade']['value']='4.7';synchronize(path,changed)
                page.wait_for_load_state('networkidle')
                page.clock.fast_forward(60000)
                expect(page.locator('.grade')).to_have_text('4,5')
                page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))")
                expect(page.locator('.grade')).to_have_text('4,7')
                expect(page.locator('details.course[open]')).to_have_count(1)
                expect(page.locator('summary .unread-dot')).to_have_count(1)
                page.locator('.course-header').click();page.locator('.course-header').click()
                expect(page.locator('.unread-dot')).to_have_count(0)
                page.reload();expect(page.locator('#page-title')).to_have_text('Horaire')
                page.locator('[data-tab="notes"]').click()
                expect(page.locator('.unread-dot')).to_have_count(0)
                expect(page.locator('details.course[open]')).to_have_count(0)
                page.locator('.course-header').click()
                expect(page.locator('.assessment-title')).to_have_text(literal_description)
                expect(page.locator('#content img')).to_have_count(0)
                page.set_viewport_size({'width':320,'height':740})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.set_viewport_size({'width':390,'height':844})
                # Une publication nouvelle n’est pas perdue après avoir lu les anciennes notes.
                page.locator('.course-header').click()
                extra=deepcopy(changed['assessments'][0]);extra.update(id='published-later',description='Nouvelle épreuve')
                extra['grade'].update(status='unpublished',value=None)
                changed['assessments'].append(extra);synchronize(path,changed)
                page.wait_for_load_state('networkidle')
                resume_read(page)
                expect(page.locator('.assessment')).to_have_count(2)
                expect(page.locator('summary .unread-dot')).to_have_count(0)
                changed['assessments'][-1]['grade'].update(status='published',value='5.2')
                synchronize(path,changed);resume_read(page)
                expect(page.locator('summary .unread-dot')).to_have_count(1)
                page.locator('.course-header').click();expect(page.locator('.unread-dot')).to_have_count(0)
                # Changer d’onglet ne relit rien, même si une note a changé côté serveur.
                before=len(data_calls)
                page.locator('[data-tab="absences"]').click()
                expect(page.locator('.absence-rate')).to_have_count(8)
                changed['assessments'][0]['grade']['value']='4.8';synchronize(path,changed)
                page.locator('[data-tab="notes"]').click()
                expect(page.locator('.grade').first).to_have_text('4,7')
                page.wait_for_load_state('networkidle')
                assert len(data_calls)==before
                page.evaluate('scrollTo(0,0)')
                pull_read(page,context,distance=40)
                expect(page.locator('#refresh-indicator')).to_be_hidden()
                assert len(data_calls)==before,'Un petit déplacement ne doit pas relire les données.'
                # Maintenir les réponses en attente pour observer le cercle et les doublons.
                pending=[]
                def hold_data(route):
                    if urlsplit(route.request.url).path in ('/api/notes','/api/horaire','/api/absences'):pending.append(route)
                    else:route.continue_()
                page.route('**/api/*',hold_data)
                pull_read(page,context)
                expect(page.locator('#refresh-indicator')).to_be_visible()
                expect(page.locator('#content')).to_have_attribute('aria-busy','true')
                assert len(pending)==3
                if args.screenshot:page.screenshot(path=str(Path(args.screenshot).with_stem(Path(args.screenshot).stem+'-loading')),full_page=False)
                page.evaluate("window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'))")
                pull_read(page,context)
                assert len(data_calls)==before+3 and len(pending)==3,'Relecture concurrente dupliquée.'
                for route in pending:route.continue_()
                page.unroute('**/api/*',hold_data)
                expect(page.locator('#refresh-indicator')).to_be_hidden()
                expect(page.locator('.grade').first).to_have_text('4,8')
                expect(page.locator('summary .unread-dot')).to_have_count(1)
                assert len(data_calls)==before+3
                # Une réponse en erreur conserve les notes et fait aussi disparaître le cercle.
                pending.clear();page.route('**/api/*',hold_data)
                pull_read(page,context)
                expect(page.locator('#refresh-indicator')).to_be_visible()
                assert len(pending)==3
                for route in pending:
                    if urlsplit(route.request.url).path=='/api/notes':route.fulfill(status=503,json={'detail':'Indisponibilité fictive'})
                    else:route.continue_()
                page.unroute('**/api/*',hold_data)
                expect(page.locator('#refresh-indicator')).to_be_hidden()
                expect(page.locator('.grade').first).to_have_text('4,8')
                expect(page.locator('#notice')).to_be_visible()
                resume_read(page)
                expect(page.locator('#notice')).to_be_hidden()
                page.locator('[data-tab="absences"]').click()
                expect(page.locator('.absence-course h3')).to_have_text(['Cours rouge','Cours orange','Mesures - capteurs','Physique'])
                expected_colors=['rgb(163, 68, 56)','rgb(163, 68, 56)','rgb(164, 101, 41)','rgb(164, 101, 41)',
                                 'rgb(164, 101, 41)','rgb(23, 111, 100)','rgb(23, 111, 100)','rgb(23, 111, 100)']
                assert page.locator('.absence-rate strong').evaluate_all('nodes=>nodes.map(n=>getComputedStyle(n).color)')==expected_colors
                expect(page.locator('.absence-total')).to_have_count(0)
                expect(page.locator('.absence-details')).to_have_count(0)
                before=len(data_calls)
                page.set_viewport_size({'width':320,'height':740})
                page.evaluate('scrollTo(0,60)')
                assert page.evaluate('scrollY')>0
                pull_read(page,context)
                page.wait_for_load_state('networkidle')
                assert len(data_calls)==before,'Un geste commencé après défilement ne doit pas actualiser.'
                page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)')
                page.locator('[data-tab="horaire"]').click()
                page.evaluate("async () => { await navigator.serviceWorker.ready; if (!navigator.serviceWorker.controller) await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, {once:true})); }")
                context.set_offline(True);page.reload(wait_until='domcontentloaded')
                expect(page.locator('#freshness')).to_have_text('Hors connexion')
                expect(page.locator('.event-card')).to_have_count(3)
                page.locator('#settings-button').click();page.locator('#logout').click()
                expect(page.locator('#association')).to_be_visible()
                expect(page.locator('.event-card')).to_have_count(0)
                page.reload(wait_until='domcontentloaded');expect(page.locator('#association')).to_be_visible()
                context.set_offline(False)
                expect(page.locator('#pair-message')).to_have_text('')
                assert context.request.get(origin+'/api/session').status==401
                code=issue_pairing(path)
                page.locator('#pair-code').fill(code);page.locator('#pair-submit').click()
                expect(page.locator('.event-card')).to_have_count(3)
                session=context.request.get(origin+'/api/session').json()
                revoke_device(path,session['id'])
                resume_read(page);expect(page.locator('#association')).to_be_visible()
                expect(page.locator('.event-card')).to_have_count(0)
                assert not errors,errors
                browser.close()
            print('Chromium: association, three views, module grouping, current semester despite legacy archive selection, native touch swipes/day/week/tabs and vertical scroll, no tab/polling requests, deduplicated focus, pull refresh/spinner/error, persisted unread/read markers, collapsed courses, absence names/order/threshold colors, notification preview removed, offline reload/logout, revocation and push activation gesture verified with synthetic data. Notification permission and Push subscription mocked; no provider contacted.')
        finally:
            server.terminate();server.wait(timeout=5)


if __name__=='__main__':main()
