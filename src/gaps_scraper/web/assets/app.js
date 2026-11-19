const $ = id => document.getElementById(id);
const state = {tab: new URLSearchParams(location.search).get('view') === 'notes' ? 'notes' : 'horaire', day: today(), session: null, data: {}, failed: new Set(), refreshing: false, lastRefreshStarted: null, generation: 0, storage: true, push: null, noteReads: {}, expandedCourses: new Set()};
const titles = {horaire: 'Horaire', notes: 'Notes', absences: 'Absences'};
let database;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function today() {
  const parts = new Intl.DateTimeFormat('fr-CH', {timeZone: 'Europe/Zurich', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date());
  return ['year', 'month', 'day'].map(type => parts.find(p => p.type === type).value).join('-');
}
function shift(day, amount) {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + amount);
  return date.toISOString().slice(0, 10);
}
function dayLabel(day, options) { return new Intl.DateTimeFormat('fr-CH', {timeZone: 'UTC', ...options}).format(new Date(`${day}T12:00:00Z`)); }
function time(value) { return new Intl.DateTimeFormat('fr-CH', {timeZone: 'Europe/Zurich', hour: '2-digit', minute: '2-digit'}).format(new Date(value)); }
function number(value) { return value === null || value === undefined ? '—' : new Intl.NumberFormat('fr-CH', {minimumFractionDigits: 1, maximumFractionDigits: 2}).format(Number(value)); }

async function openDatabase() {
  if (database) return database;
  database = await new Promise((resolve, reject) => {
    const request = indexedDB.open('mon-ecole-private', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('state');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return database;
}
async function stored(key, value, write = false) {
  try {
    const db = await openDatabase();
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction('state', write ? 'readwrite' : 'readonly');
      const request = write ? transaction.objectStore('state').put(value, key) : transaction.objectStore('state').get(key);
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } catch { state.storage = false; return undefined; }
}
async function erase(pendingLogout = false) {
  state.generation += 1;
  state.session = null;
  state.push = null;
  state.data = {};
  state.noteReads = {}; state.expandedCourses.clear();
  state.lastRefreshStarted = null;
  try {
    const db = await openDatabase();
    await new Promise((resolve, reject) => {
      const tx = db.transaction('state', 'readwrite');
      const store = tx.objectStore('state');
      store.clear();
      if (pendingLogout) store.put(true, 'logout_pending');
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
  } catch { state.storage = false; }
}
async function request(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`/api/${path}`, {method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal});
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) { const error = new Error(data?.detail || 'Le serveur ne peut pas répondre.'); error.status = response.status; throw error; }
    return data;
  } finally { clearTimeout(timer); }
}
function showAssociation(message = '') {
  if ($('settings-dialog').open) $('settings-dialog').close();
  $('loading').hidden = true;
  $('workspace').hidden = true;
  $('navigation').hidden = true;
  $('settings-button').hidden = true;
  $('association').hidden = false;
  $('content').replaceChildren();
  $('pair-message').textContent = message;
}
function showWorkspace() {
  $('loading').hidden = true;
  $('association').hidden = true;
  $('workspace').hidden = false;
  $('navigation').hidden = false;
  $('settings-button').hidden = false;
  render();
}
function noteScope(data) { return JSON.stringify([data.academic_year.value, data.semester.value]); }
function gradeVersion(item) { return JSON.stringify([item.grade.status, item.grade.value, item.grade.ignored, item.grade.absent]); }
function isUnread(scope, item) { return item.grade.status === 'published' && state.noteReads[scope]?.[item.id] !== gradeVersion(item); }
async function observeNotes(data) {
  if (!data) return;
  const scope = noteScope(data);
  // Une première consultation établit une référence, sans marquer tout l’historique comme nouveau.
  if (!Object.hasOwn(state.noteReads, scope)) {
    state.noteReads[scope] = Object.fromEntries(data.assessments.map(item => [item.id, gradeVersion(item)]));
    await stored('noteReads', state.noteReads, true);
  }
}
function unreadDot(label, item, scope) {
  const dot = element('span', 'unread-dot');
  dot.setAttribute('role', 'img'); dot.setAttribute('aria-label', label); dot.title = label;
  if (item) { dot.dataset.assessmentId = item.id; dot.dataset.gradeVersion = gradeVersion(item); dot.dataset.scope = scope; }
  return dot;
}
function updateUnreadDots() {
  for (const dot of document.querySelectorAll('.unread-dot[data-assessment-id]')) {
    if (state.noteReads[dot.dataset.scope]?.[dot.dataset.assessmentId] === dot.dataset.gradeVersion) dot.remove();
  }
  for (const course of document.querySelectorAll('details.course')) {
    if (!course.querySelector('.assessment .unread-dot')) course.querySelector('summary .unread-dot')?.remove();
  }
  for (const module of document.querySelectorAll('.module-group')) {
    if (!module.querySelector('summary .unread-dot')) module.querySelector('.module-header .unread-dot')?.remove();
  }
}
async function readCourse(scope, assessments) {
  const reads = state.noteReads[scope];
  if (!reads) return;
  let changed = false;
  for (const item of assessments) {
    if (isUnread(scope, item)) { reads[item.id] = gradeVersion(item); changed = true; }
  }
  if (changed) { updateUnreadDots(); await stored('noteReads', state.noteReads, true); }
}

function refreshIndicator(pull = 0) {
  const indicator = $('refresh-indicator');
  indicator.hidden = !state.refreshing && pull < 12;
  indicator.classList.toggle('is-loading', state.refreshing);
  indicator.classList.toggle('is-ready', pull >= 72);
  indicator.style.setProperty('--pull-rotation', `${Math.min(pull, 120) * 3}deg`);
}
async function refresh() {
  if (!state.session) return;
  if (state.refreshing) {
    if (state.refreshingGeneration !== state.generation) state.refreshRequested = true;
    return;
  }
  if (state.session.expires_at * 1000 <= Date.now()) {
    await erase(); showAssociation('Cette session a expiré. Associez à nouveau cet appareil.'); return;
  }
  if (!navigator.onLine) { render(); refreshIndicator(); return; }
  state.refreshing = true;
  state.refreshingGeneration = state.generation;
  state.lastRefreshStarted = performance.now();
  $('content').setAttribute('aria-busy', 'true');
  refreshIndicator();
  let changed = false;
  const generation = state.generation;
  await Promise.all(['horaire', 'notes', 'absences'].map(async component => {
    try {
      const value = await request(component);
      if (generation !== state.generation || !state.session) return;
      if (state.failed.has(component)) changed = true;
      state.failed.delete(component);
      if (component === 'notes') {
        await observeNotes(value.data);
        if (generation !== state.generation || !state.session) return;
      }
      const different = JSON.stringify(state.data[component]) !== JSON.stringify(value);
      state.data[component] = value;
      if (different) {
        changed = true;
        await stored(component, value, true);
      }
    } catch (error) {
      if (generation !== state.generation || !state.session) return;
      if (error.status === 401) {
        await erase();
        showAssociation('Cette session a expiré ou a été révoquée. Associez à nouveau cet appareil.');
      } else {
        if (!state.failed.has(component)) changed = true;
        state.failed.add(component);
      }
    }
  }));
  state.refreshing = false;
  $('content').setAttribute('aria-busy', 'false');
  refreshIndicator();

  if (state.session && changed) render();
  if (state.refreshRequested) { state.refreshRequested = false; refresh(); }
}
function empty(title, detail) {
  const box = element('div', 'empty');
  box.append(element('strong', '', title), element('span', '', detail));
  return box;
}
function render() {
  if (!state.session) return;
  $('page-title').textContent = titles[state.tab];
  for (const tab of document.querySelectorAll('[data-tab]')) {
    if (tab.dataset.tab === state.tab) tab.setAttribute('aria-current', 'page');
    else tab.removeAttribute('aria-current');
  }
  const snapshot = state.data[state.tab];
  const unreachable = !navigator.onLine || state.failed.has(state.tab);
  const freshness = !navigator.onLine ? 'offline' : state.failed.has(state.tab) ? 'unreachable' : snapshot?.freshness || 'unavailable';
  $('freshness').className = 'status-pill ' + (['offline', 'unreachable'].includes(freshness) ? 'offline' : freshness === 'stale' ? 'stale' : '');
  $('freshness').textContent = {fresh: 'À jour', stale: 'Données anciennes', offline: 'Hors connexion', unreachable: 'Serveur inaccessible', unavailable: 'En attente'}[freshness];
  $('last-sync').textContent = snapshot?.last_sync ? 'Synchro. ' + new Intl.DateTimeFormat('fr-CH', {timeZone: 'Europe/Zurich', year: 'numeric', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}).format(new Date(snapshot.last_sync)) : 'Aucune synchronisation disponible';
  let notice = '';
  if (unreachable) notice = 'Vous consultez la dernière copie conservée sur cet appareil. Rouvrez l’application ou tirez vers le bas en haut de page pour réessayer.';
  else if (snapshot?.error) notice = 'La dernière récupération auprès de l’école a échoué. Les dernières données valides restent affichées.';
  else if (snapshot?.freshness === 'stale') notice = 'Ces données n’ont pas été actualisées au dernier créneau prévu. Vérifiez la date de synchronisation.';
  if (!state.storage) notice += ' La conservation hors connexion est indisponible dans ce navigateur.';
  $('notice').textContent = notice;
  $('notice').hidden = !notice;
  const content = $('content');
  content.replaceChildren();
  if (!snapshot?.data) { content.append(empty('Pas encore de données', 'Le dernier état valide apparaîtra ici après une synchronisation.')); return; }
  if (state.tab === 'horaire') renderSchedule(content, snapshot.data);
  if (state.tab === 'notes') renderNotes(content, snapshot.data);
  if (state.tab === 'absences') renderAbsences(content, snapshot.data);
}
function renderSchedule(content, data) {
  const caption = element('div', 'section-caption', `${data.semester.label} · ${data.academic_year.label}`);
  const todayButton = element('button', 'text-button', 'Aujourd’hui');
  todayButton.onclick = () => { state.day = today(); render(); };
  caption.append(todayButton);
  const controls = element('div', 'day-controls');
  controls.append(element('div', 'day-title', dayLabel(state.day, {weekday: 'long', day: 'numeric', month: 'long'})));
  const arrows = element('div', 'day-arrows');
  for (const [amount, label, text] of [[-7, 'Semaine précédente', '‹'], [7, 'Semaine suivante', '›']]) {
    const button = element('button', 'icon-button', text); button.setAttribute('aria-label', label);
    button.onclick = () => { state.day = shift(state.day, amount); render(); }; arrows.append(button);
  }
  controls.append(arrows);
  const strip = element('div', 'week-strip');
  const dayOfWeek = new Date(`${state.day}T12:00:00Z`).getUTCDay();
  const monday = shift(state.day, -((dayOfWeek + 6) % 7));
  for (let index = 0; index < 7; index += 1) {
    const day = shift(monday, index);
    const button = element('button', 'day-button' + (day === state.day ? ' active' : '') + (day === today() ? ' today' : ''));
    button.append(element('span', '', dayLabel(day, {weekday: 'short'}).replace('.', '')), element('strong', '', String(Number(day.slice(8)))));
    button.setAttribute('aria-label', dayLabel(day, {weekday: 'long', day: 'numeric', month: 'long'}));
    button.setAttribute('aria-pressed', String(day === state.day));
    button.onclick = () => { state.day = day; render(); }; strip.append(button);
  }
  content.append(caption, controls, strip);
  const items = data.occurrences.filter(item => item.all_day ? item.start <= state.day && state.day < item.end : item.start.slice(0, 10) <= state.day && (item.end.slice(0, 10) > state.day || item.end.slice(0, 10) === state.day && item.end.slice(11, 19) !== '00:00:00'));
  if (!items.length) { content.append(empty('Une journée sans cours', 'Aucun événement n’est indiqué pour cette date dans l’horaire récupéré.')); return; }
  const timeline = element('div', 'timeline');
  for (const item of items) {
    const row = element('article', 'event');
    const clock = element('div', 'event-time', item.all_day ? 'Journée' : time(item.start));
    clock.append(element('span', '', item.all_day ? 'entière' : time(item.end)));
    const card = element('div', 'event-card'); card.append(element('h3', '', item.summary));
    const meta = element('div', 'event-meta');
    if (item.location) meta.append(element('span', '', item.location));
    if (item.status === 'TENTATIVE') meta.append(element('span', '', 'À confirmer'));
    if (meta.childNodes.length) card.append(meta);
    row.append(clock, card); timeline.append(row);
  }
  content.append(timeline);
}
function renderNotes(content, data) {
  content.append(element('div', 'section-caption', `${data.semester.label} · ${data.academic_year.label}`));
  if (!data.courses.length) { content.append(empty('Pas encore de note', 'Les notes publiées apparaîtront ici.')); return; }
  const rendered = new Set();
  const scope = noteScope(data);
  const renderCourse = (parent, course, unit) => {
    rendered.add(course.name);
    const panel = element('details', 'panel course');
    const key = JSON.stringify([scope, course.name]);
    const assessments = data.assessments.filter(a => a.course === course.name).sort((a, b) => b.date.localeCompare(a.date));
    const header = element('summary', 'course-header');
    const heading = element('div'); heading.append(element('h3', '', unit?.name || course.name));
    const meta = [];
    if (unit?.coefficient) meta.push(`Coeff. ${unit.coefficient}`);
    if (course.mean_excludes_exam) meta.push('Hors examen');
    if (meta.length) heading.append(element('div', 'course-subtitle', meta.join(' · ')));
    if (assessments.some(item => isUnread(scope, item))) heading.querySelector('h3').append(unreadDot('Notes nouvelles ou modifiées non lues'));
    const right = element('div', 'course-summary-end');
    right.append(element('span', 'mean', number(course.mean)), element('span', 'course-chevron', '⌄'));
    right.lastChild.setAttribute('aria-hidden', 'true');
    header.append(heading, right); panel.append(header);
    for (const item of assessments) {
      const row = element('div', 'assessment'); const info = element('div');
      const title = element('div', 'assessment-title', item.description);
      if (isUnread(scope, item)) title.append(unreadDot('Note nouvelle ou modifiée non lue', item, scope));
      info.append(title);
      const coefficient = item.weight.denominator === 1 ? item.weight.numerator : `${item.weight.numerator}/${item.weight.denominator}`;
      info.append(element('div', 'assessment-meta', `${dayLabel(item.date, {day: 'numeric', month: 'short'})} · ${item.group} · Coeff. ${coefficient}`));
      if (item.class_mean !== null) info.append(element('div', 'assessment-meta', `Classe ${number(item.class_mean)}`));
      if (item.grade.ignored) info.append(element('span', 'tag', 'Non comptée'));
      if (item.grade.absent) info.append(element('span', 'tag', 'Absence à l’épreuve'));
      const unpublished = item.grade.status === 'unpublished';
      const grade = element('span', 'grade' + (unpublished ? ' unpublished' : Number(item.grade.value) < 4 ? ' low' : ''), unpublished ? 'À venir' : number(item.grade.value));
      row.append(info, grade); panel.append(row);
    }
    panel.open = state.expandedCourses.has(key);
    panel.addEventListener('toggle', () => {
      if (!panel.isConnected || panel.open === state.expandedCourses.has(key)) return;
      if (panel.open) { state.expandedCourses.add(key); readCourse(scope, assessments); }
      else state.expandedCourses.delete(key);
    });
    parent.append(panel);
  };
  for (const module of data.modules || []) {
    const section = element('section', 'module-group');
    const header = element('div', 'module-header');
    header.append(element('h2', '', module.name));
    const average = element('div', 'module-average');
    average.append(element('strong', '', number(module.mean)), element('span', '', 'Provisoire'));
    average.title = 'Moyenne pondérée des moyennes de branches disponibles pour ce semestre. Aucune règle de validation ou d’arrondi officiel n’est appliquée.';
    header.append(average); section.append(header);
    for (const unit of module.units) {
      const codes = [unit.code, ...unit.aliases].map(code => code.toLocaleLowerCase('fr'));
      const course = data.courses.find(c => codes.includes(c.name.toLocaleLowerCase('fr')));
      if (course) renderCourse(section, course, unit);
    }
    if (section.querySelector('summary .unread-dot')) header.querySelector('h2').append(unreadDot('Module contenant des notes non lues'));
    content.append(section);
  }
  const unmatched = data.courses.filter(course => !rendered.has(course.name));
  if (unmatched.length) {
    if (data.modules?.length) content.append(element('h2', 'unmapped-title', 'Autres branches'));
    if (data.unmapped_courses?.length) content.append(element('p', 'notice', 'Le module de certaines branches n’a pas pu être confirmé dans le programme.'));
    for (const course of unmatched) renderCourse(content, course);
  }
}
function renderAbsences(content, data) {
  content.append(element('div', 'section-caption', data.academic_year.label));
  if (!data.courses.length) { content.append(empty('Aucune absence', 'Aucune donnée pour cette année.')); return; }
  const courses = [...data.courses].sort((a, b) => Number(b.absolute.unjustified_rate_percent) - Number(a.absolute.unjustified_rate_percent) || a.name.localeCompare(b.name, 'fr'));
  for (const course of courses) {
    const panel = element('article', 'panel absence-course');
    const name = course.name.replace(/\s+-\s*[^\s]+$/, '').trim();
    panel.append(element('h3', '', name));
    const rates = element('div', 'absence-rates');
    for (const [label, value] of [['Relatif non justifié', course.relative.unjustified_rate_percent], ['Absolu non justifié', course.absolute.unjustified_rate_percent]]) {
      const level = Number(value) < 15 ? 'low' : Number(value) < 30 ? 'medium' : 'high';
      const rate = element('div', `absence-rate rate-${level}`);
      rate.append(element('span', '', label), element('strong', '', `${number(value)} %`)); rates.append(rate);
    }
    panel.append(rates); content.append(panel);
  }
}

async function finishLogout() {
  try { await request('logout', {}); }
  catch (error) { if (error.status !== 401) return false; }
  await erase(); return true;
}
$('pair-form').addEventListener('submit', async event => {
  event.preventDefault(); $('pair-submit').disabled = true; $('pair-message').textContent = '';
  try {
    if (await stored('logout_pending') && !await finishLogout()) throw new Error('Reconnectez-vous au réseau pour terminer la déconnexion précédente.');
    const session = await request('pair', {code: $('pair-code').value.trim(), device_name: $('device-name').value.trim()});
    await erase(); state.session = session; await stored('session', session, true); $('pair-code').value = '';
    showWorkspace(); preparePush(); await refresh();
  } catch (error) { $('pair-message').textContent = error.status ? error.message : 'Le serveur est inaccessible. Vérifiez votre connexion.'; }
  finally { $('pair-submit').disabled = false; }
});
function selectTab(tab) {
  state.tab = tab; render();
}
document.querySelectorAll('[data-tab]').forEach(button => { button.onclick = () => selectTab(button.dataset.tab); });

// Gestes limités au contenu : le scroll vertical, le pincement et les bords restent natifs.
let swipe = null;
let swipeClick = null;
const workspace = $('workspace');
workspace.addEventListener('touchstart', event => {
  swipe = null; swipeClick = null; refreshIndicator();
  if (!state.session || $('settings-dialog').open || event.touches.length !== 1) return;
  const touch = event.touches[0];
  if (touch.clientX < 24 || touch.clientX > innerWidth - 24 || window.getSelection()?.isCollapsed === false) return;
  const week = state.tab === 'horaire' && Boolean(event.target.closest('.week-strip'));
  if (!week && event.target.closest('button, a, input, textarea, select, [contenteditable="true"]')) return;
  swipe = {id: touch.identifier, x: touch.clientX, y: touch.clientY, started: performance.now(),
    tab: state.tab, day: state.day, week, horizontal: false, pull: false, top: scrollY <= 0 && !state.refreshing, generation: state.generation};
}, {passive: true});
workspace.addEventListener('touchmove', event => {
  if (!swipe) return;
  if (event.touches.length !== 1) { swipe = null; refreshIndicator(); return; }
  const touch = event.touches[0];
  if (touch.identifier !== swipe.id) { swipe = null; refreshIndicator(); return; }
  const dx = Math.abs(touch.clientX - swipe.x), dy = touch.clientY - swipe.y;
  if (!swipe.horizontal && !swipe.pull && Math.max(dx, Math.abs(dy)) >= 12) {
    if (dy > dx * 1.5 && swipe.top && scrollY <= 0 && !state.refreshing) swipe.pull = true;
    else if (dx > Math.abs(dy) * 1.5) swipe.horizontal = true;
    else { swipe = null; return; }
  }
  if (swipe.pull) {
    if (dy < 0 || scrollY > 0 || state.refreshing) { swipe = null; refreshIndicator(); return; }
    if (event.cancelable) event.preventDefault();
    refreshIndicator(dy);
  } else if (swipe.horizontal && event.cancelable) event.preventDefault();
}, {passive: false});
workspace.addEventListener('touchend', event => {
  const gesture = swipe; swipe = null; refreshIndicator();
  if (!gesture || event.touches.length || !state.session
      || gesture.generation !== state.generation || gesture.tab !== state.tab || $('settings-dialog').open) return;
  const touch = Array.from(event.changedTouches).find(item => item.identifier === gesture.id);
  if (!touch) return;
  const dx = touch.clientX - gesture.x, dy = touch.clientY - gesture.y;
  if (gesture.pull) {
    if (dy < 72 || dy <= Math.abs(dx) * 1.5 || scrollY > 0) return;
    swipeClick = {x: touch.clientX, y: touch.clientY, until: performance.now() + 350};
    refresh();
    return;
  }
  if (!gesture.horizontal || Math.abs(dx) < 60 || Math.abs(dx) <= Math.abs(dy) * 1.5 || performance.now() - gesture.started > 800) return;
  const step = dx < 0 ? 1 : -1;
  if (gesture.tab === 'horaire') {
    if (gesture.day !== state.day) return;
    state.day = shift(state.day, step * (gesture.week ? 7 : 1)); render();
  } else {
    const tabs = ['horaire', 'notes', 'absences'];
    const next = tabs[tabs.indexOf(gesture.tab) + step];
    if (!next) return;
    selectTab(next);
  }
  // Éviter le clic de fin de geste sur une branche ou un jour, sans bloquer un nouveau toucher.
  swipeClick = {x: touch.clientX, y: touch.clientY, until: performance.now() + 350};
}, {passive: true});
workspace.addEventListener('touchcancel', () => { swipe = null; refreshIndicator(); });
workspace.addEventListener('click', event => {
  if (!swipeClick || event.detail === 0 || performance.now() > swipeClick.until) return;
  if (Math.hypot(event.clientX - swipeClick.x, event.clientY - swipeClick.y) > 24) return;
  swipeClick = null; event.preventDefault(); event.stopImmediatePropagation();
}, {capture: true});
$('settings-button').onclick = () => {
  $('settings-dialog').showModal();
  preparePush();
};
$('logout').onclick = async () => {
  const registration = state.push?.registration;
  $('settings-dialog').close(); await erase(true); showAssociation('Effacement de la copie locale…');
  if (registration) registration.pushManager.getSubscription().then(sub => sub?.unsubscribe()).catch(() => {});
  const finished = await finishLogout();
  showAssociation(finished ? 'Cet appareil est déconnecté. Les données locales ont été effacées.' : 'La copie locale est effacée. La déconnexion serveur sera terminée au retour du réseau.');
};
window.addEventListener('online', async () => {
  if (await stored('logout_pending')) { await finishLogout(); showAssociation(); }
  else render();
});
window.addEventListener('offline', render);
function refreshOnReturn() {
  if (!state.session || document.hidden || !navigator.onLine || state.refreshing) return;
  // Un même retour déclenche souvent focus, pageshow et visibilitychange à la suite.
  if (state.lastRefreshStarted !== null && performance.now() - state.lastRefreshStarted < 800) return;
  refresh();
}
document.addEventListener('visibilitychange', refreshOnReturn);
window.addEventListener('pageshow', refreshOnReturn);
window.addEventListener('focus', refreshOnReturn);

async function start() {
  if (/iPhone/.test(navigator.userAgent)) $('device-name').value = 'Mon iPhone';
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1;
  $('install-hint').hidden = !(ios && !(matchMedia('(display-mode: standalone)').matches || navigator.standalone));
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  if (await stored('logout_pending')) { const done = await finishLogout(); showAssociation(done ? 'Cet appareil est déconnecté.' : 'La copie locale a été effacée. Reconnectez-vous au réseau pour terminer la déconnexion.'); return; }
  const cached = await stored('session');
  state.noteReads = await stored('noteReads') || {};
  try {
    const session = await request('session');
    if (cached?.id !== session.id) await erase();
    state.session = session; await stored('session', session, true);
  } catch (error) {
    if (error.status === 401 || !cached || cached.expires_at * 1000 <= Date.now()) { await erase(); showAssociation(error.status === 401 && cached ? 'Associez à nouveau cet appareil pour accéder à vos données.' : ''); return; }
    state.session = cached; state.failed = new Set(['horaire', 'notes', 'absences']);
  }
  for (const component of ['horaire', 'notes', 'absences']) { const snapshot = await stored(component); if (snapshot) { if (component === 'notes') await observeNotes(snapshot.data); state.data[component] = snapshot; } }
  showWorkspace(); preparePush(); await refresh();
}

function pushMessage(text, disabled = true) {
  $('notification-state').textContent = text;
  $('push-toggle').disabled = disabled;
}
async function preparePush() {
  if (!state.session) return;
  state.push = null;
  pushMessage('Préparation des notifications…');
  if (!navigator.onLine) { pushMessage('Une connexion est nécessaire pour gérer les notifications.'); return; }
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1;
  const installed = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  if (ios && !installed) { pushMessage('Ajoutez cette application à l’écran d’accueil pour activer les notifications.'); return; }
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) { pushMessage('Ce navigateur ne propose pas les notifications Web Push.'); return; }
  const generation = state.generation;
  try {
    const configuration = await request('push/config');
    if (!configuration.enabled || !configuration.public_key) { pushMessage('Les notifications ne sont pas encore configurées.'); return; }
    if (Notification.permission === 'denied') { pushMessage('Les notifications sont bloquées dans les réglages de cet appareil.'); return; }
    const registration = await navigator.serviceWorker.ready;
    const nativeSubscription = await registration.pushManager.getSubscription();
    if (generation !== state.generation || !state.session) return;
    const subscribed = configuration.subscribed && Notification.permission === 'granted' && Boolean(nativeSubscription);
    if (configuration.subscribed && !subscribed) await request('push/unsubscribe', {});
    const padding = '='.repeat((4 - configuration.public_key.length % 4) % 4);
    const raw = atob(configuration.public_key.replace(/-/g, '+').replace(/_/g, '/') + padding);
    state.push = {registration, key: Uint8Array.from(raw, char => char.charCodeAt(0)), subscribed};
    $('push-toggle').textContent = subscribed ? 'Désactiver les notifications' : 'Activer les notifications';
    pushMessage(subscribed ? 'Notifications actives pour les nouvelles notes et les corrections. La valeur de la note ne figure pas dans le message.' : 'Recevez la matière et l’indication de nouveauté ou de correction, même lorsque l’application est fermée.', false);
  } catch { pushMessage('Les notifications sont temporairement indisponibles.'); }
}
$('push-toggle').onclick = () => {
  const push = state.push;
  if (!push) return;
  if (push.subscribed) {
    $('push-toggle').disabled = true;
    request('push/unsubscribe', {}).then(() => push.registration.pushManager.getSubscription()).then(sub => sub?.unsubscribe()).then(preparePush).catch(() => pushMessage('La désactivation n’a pas pu être terminée. Réessayez avec une connexion.'));
    return;
  }
  // subscribe est appelé directement dans le clic : aucun fetch préalable ne perd le geste iOS.
  let subscription;
  try { subscription = push.registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: push.key}); }
  catch { pushMessage('Ce navigateur n’a pas pu demander l’autorisation des notifications.'); return; }
  $('push-toggle').disabled = true;
  subscription.then(sub => request('push/subscribe', sub.toJSON())).then(preparePush).catch(() => pushMessage('L’activation n’a pas été terminée. Vérifiez l’autorisation des notifications et votre connexion.'));
};
if ('serviceWorker' in navigator) navigator.serviceWorker.addEventListener('message', event => {
  if (event.data?.type === 'school-update' && state.session) { state.tab = 'notes'; render(); refresh(); }
});
// Aucune relecture périodique : ouverture, reprise de focus et geste manuel uniquement.
start().catch(() => showAssociation('Ouverture impossible pour le moment. Réessayez avec une connexion réseau.'));
