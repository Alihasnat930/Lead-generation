"""Free indexed-post discovery. Source blocks stay visible and retain the cursor."""
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup
from .providers import ProviderError, SearchPage, check_status
from .social_research import SERVICES, normalize_record
from .websites import decode_bing_url, is_challenge


def plan_queries(settings, discord_channels=None):
    terms = [SERVICES[name][0] for name in settings['services']] + settings.get('keywords', [])
    queries = []
    for term in dict.fromkeys(terms):
        term = term.replace('"', ' ')
        for market in settings['markets']:
            location = '(UK OR "United Kingdom")' if market=='UK' else '(USA OR "United States")'
            for platform in settings['platforms']:
                if platform=='Discord':
                    continue
                domain = 'reddit.com/r/' if platform=='Reddit' else 'facebook.com'
                for engine in ('duckduckgo', 'bing'):
                    queries.append({'engine':engine,'platform':platform,'market':market,
                                    'query':f'site:{domain} "{term}" {location} ("need" OR "looking for" OR "help")'})
    if 'Discord' in settings['platforms']:
        queries.extend({'engine':'discord','platform':'Discord','channel':channel,'query':'Configured channel history'} for channel in (discord_channels or []))
    return queries


def search(query, page, settings):
    engine = query['engine']
    try:
        if engine=='discord':
            from .integrations import load
            from .outreach_store import OutreachStore
            from .social_discord import collect
            return collect(query,settings,load(OutreachStore()))
        if engine=='duckduckgo':
            from ddgs import DDGS
            with DDGS(timeout=20) as client:
                client.threads = 1
                rows = list(client.text(query['query'], region='uk-en' if query['market']=='UK' else 'us-en',
                                        safesearch='moderate', max_results=10, page=page, backend='duckduckgo'))
            raw = [{'url':r.get('href'),'title':r.get('title'),'text':r.get('body')} for r in rows]
            more = len(rows)>=10
        else:
            url = 'https://www.bing.com/search?' + urlencode({'q':query['query'],'first':(page-1)*10+1,'count':10})
            with requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=(8,25), stream=True) as response:
                check_status(response.status_code)
                parts, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size>2*1024*1024:
                        raise ProviderError('response_size','Search response exceeded the download limit.')
                    parts.append(chunk)
                html = b''.join(parts).decode(response.encoding or 'utf-8',errors='replace')
            if is_challenge(html):
                raise ProviderError('captcha','Bing requested a challenge. Retry this source after its cooldown.')
            soup = BeautifulSoup(html,'html.parser')
            blocks = soup.select('li.b_algo')
            if not blocks and not soup.select('.b_no'):
                raise ProviderError('parse_error','Bing returned an unreadable page. The query remains saved.')
            raw = []
            for block in blocks:
                link, snippet = block.select_one('h2 a[href]'), block.select_one('p')
                if link:
                    raw.append({'url':decode_bing_url(link['href']),'title':link.get_text(' ',strip=True),
                                'text':snippet.get_text(' ',strip=True) if snippet else ''})
            more = bool(soup.select('a.sb_pagN'))
        records = []
        for row in raw:
            record = normalize_record(row, settings, 'Search snippet · ' + engine)
            if record and record['platform']==query['platform'] and record['relevant']:
                records.append(record)
        return SearchPage(records, more)
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('source_unavailable',engine.title()+' did not return usable results. Saved searches can be resumed after the cooldown.',True) from None
