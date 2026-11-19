"""Modules et pondérations lus dans le plan GAPS, sans correspondances prédéfinies."""
from decimal import Decimal, localcontext
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from gaps_scraper.common.parsing import Node, Tree, ParseError, clean, number
from gaps_scraper.portal.client import BASE, CaptureError, check_origin, login_forms


def _cells(row):
    return [n for n in row.children if isinstance(n, Node) and n.tag in ('td', 'th')]


def _span(cell, name):
    raw=cell.attrs.get(name,'1')
    if not re.fullmatch(r'\d+(?:\.0*)?',raw):raise ParseError('Fusion de cellules inconnue dans le programme.')
    value=int(Decimal(raw))
    if not 1<=value<=100:raise ParseError('Fusion de cellules de programme hors limites.')
    return value


def _rows(table):
    remaining={}
    for row in table.descendants('tr'):
        occupied=set(remaining)
        remaining={column:count-1 for column,count in remaining.items() if count>1}
        columns={};column=0
        for cell in _cells(row):
            while column in occupied:column+=1
            width=_span(cell,'colspan');height=_span(cell,'rowspan')
            for position in range(column,column+width):
                if position in occupied:raise ParseError('Cellules de programme superposées.')
                columns[position]=cell
                if height>1:remaining[position]=height-1
            column+=width
        yield row,columns


def _identity(cell, kind):
    links=list(cell.descendants('a'))
    if len(links)!=1:
        raise ParseError('Identité de module ou de branche ambiguë dans le programme.')
    link=links[0]
    url=urljoin(BASE+'/consultation/programmes/',link.attrs.get('href',''))
    check_origin(url)
    parts=urlsplit(url);query=parse_qs(parts.query)
    keys=[key for key in (('id','iduv') if kind=='uv' else ('id',)) if key in query]
    if len(keys)!=1:raise ParseError('Identifiant de fiche absent ou ambigu.')
    identity=keys[0]
    if parts.path!=f'/consultation/fiches/{kind}/{kind}.php' or any(
        len(query.get(key,[]))!=1 or not query[key][0].isdigit() for key in (identity,'plan')):
        raise ParseError('Lien de fiche du programme inconnu.')
    match=re.fullmatch(r'(.+?)\s+\(([^()]+)\)\s+(\d{4})(?:\s+#\d+)?',clean(link.text()))
    if not match:
        raise ParseError('Libellé de branche ou de module inconnu.')
    return {'id':query[identity][0],'plan':query['plan'][0],'name':match[1],
            'code':match[2],'url':parts.path+'?'+parts.query}


def parse_programme(source):
    if login_forms(source):
        raise CaptureError('La page du programme redemande une connexion.')
    tree=Tree();tree.feed(source)
    tables=[n for n in tree.root.descendants('table') if n.attrs.get('id')=='teachingplanbox']
    if len(tables)!=1:
        raise ParseError('Plan de formation absent ou ambigu.')
    modules=[];module=None;identities=set();plans=set()
    for row,columns in _rows(tables[0]):
        classes=row.attrs.get('class','').split()
        if not {'program_module_row','program_unit_row'}.intersection(classes):continue
        cells=_cells(row)
        if not cells:raise ParseError('Ligne de programme incomplète.')
        if 'program_module_row' in classes:
            module=_identity(cells[0],'mv')
            if module['id'] in identities:raise ParseError('Module dupliqué dans le programme.')
            if 2 not in columns or clean(columns[2].text())!='Coef.':raise ParseError('Colonne des coefficients inconnue.')
            identities.add(module['id']);plans.add(module['plan']);module['units']=[];module['elective']=False;modules.append(module)
        else:
            if module is None:raise ParseError('Branche sans module dans le programme.')
            if clean(cells[0].text())=='Unités à choix' and not list(cells[0].descendants('a')):
                module['elective']=True;continue
            unit=_identity(cells[0],'uv');plans.add(unit['plan'])
            if 2 not in columns:raise ParseError('Coefficient absent du programme.')
            raw=clean(columns[2].text());optional=raw=='fac.'
            alternative=raw.startswith('(') and raw.endswith(')')
            coefficient=None if optional else number(raw[1:-1] if alternative else raw)
            if coefficient is not None and Decimal(coefficient)<=0:raise ParseError('Coefficient de branche nul.')
            if any(u['id']==unit['id'] for u in module['units']):raise ParseError('Branche dupliquée dans un module.')
            unit.update(coefficient=coefficient,optional=optional,alternative=alternative,aliases=[])
            module['units'].append(unit)
    if not modules or len(plans)!=1 or any(not m['units'] and not m['elective'] for m in modules):
        raise ParseError('Programme vide, incomplet ou mélangeant plusieurs plans.')
    return {'plan':next(iter(plans)),'modules':modules}


def parse_unit_alias(source, *, module_id, plan):
    if login_forms(source):raise CaptureError('La fiche de branche redemande une connexion.')
    tree=Tree();tree.feed(source)
    subtitles=[n for n in tree.root.descendants('span') if 'sheet-subtitle' in n.attrs.get('class','').split()]
    links=[n for n in tree.root.descendants('a') if '/fiches/mv/mv.php?' in n.attrs.get('href','')]
    if len(subtitles)!=1:raise ParseError('Code de fiche de branche absent ou ambigu.')
    match=re.fullmatch(r'(\S+)\s+\d{4}(?:\s+.*)?',clean(subtitles[0].text()))
    if not match:raise ParseError('Code de fiche de branche inconnu.')
    matches=[]
    for link in links:
        url=urljoin(BASE,link.attrs['href']);check_origin(url);query=parse_qs(urlsplit(url).query)
        if query.get('id')==[module_id] and query.get('plan')==[plan]:matches.append(link)
    if not matches:raise ParseError('La fiche ne confirme pas le module du programme.')
    return match[1]


def enrich_notes(client, document):
    programme=parse_programme(client.fetch('/consultation/programmes/detail.php'))
    entries=[(module,unit) for module in programme['modules'] for unit in module['units']]
    matched={};unmapped=[]
    for course in document['courses']:
        code=course['name']
        exact=[(m,u) for m,u in entries if code.casefold() in [c.casefold() for c in [u['code'],*u['aliases']]]]
        if not exact:
            candidates=[(m,u) for m,u in entries if code.casefold().startswith(u['code'].casefold()+'-')]
            if len(candidates)==1:
                module,unit=candidates[0]
                alias=parse_unit_alias(client.fetch(unit['url']),module_id=module['id'],plan=programme['plan'])
                unit['aliases'].append(alias)
                if alias.casefold()==code.casefold():exact=candidates
        if len(exact)>1:raise ParseError('Branche associée à plusieurs modules : correspondance ambiguë.')
        if not exact:
            unmapped.append(code);continue
        module,unit=exact[0]
        matched.setdefault(module['id'],[]).append((course,unit))
    result=[]
    for module in programme['modules']:
        courses=matched.get(module['id'])
        if not courses:continue
        available=[(c,u) for c,u in courses if c['mean'] is not None]
        # Moyenne indicative des seules moyennes de branches disponibles, sans arrondi réglementaire.
        mean=None
        if available and all(u['coefficient'] is not None for c,u in available):
            with localcontext() as context:
                context.prec=28
                total=sum(Decimal(u['coefficient']) for c,u in available)
                mean=format(sum(Decimal(c['mean'])*Decimal(u['coefficient']) for c,u in available)/total,'f')
        result.append({'id':module['id'],'code':module['code'],'name':module['name'],
                       'units':module['units'],'mean':mean,'provisional':True})
    document.update(modules=result,unmapped_courses=unmapped,programme_plan=programme['plan'])
    return document
