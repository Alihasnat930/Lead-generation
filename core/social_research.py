"""Post-level research records, evidence scoring and bounded export imports."""
import csv
from datetime import datetime, timezone
import io
import json
import re
from urllib.parse import parse_qs, urlsplit

PLATFORMS = ('Reddit', 'Facebook', 'Discord')
SERVICES = {
    'Assignment support': ('assignment', 'assignments'),
    'Thesis & dissertation support': ('thesis', 'theses', 'dissertation', 'dissertations'),
    'Quiz preparation': ('quiz', 'quizzes', 'practice quiz', 'practice quizzes'),
    'Homework & coursework': ('homework', 'coursework', 'course work'),
    'Essay & report support': ('essay', 'essays', 'academic report', 'academic reports', 'lab report', 'lab reports'),
    'Exam preparation': ('exam', 'exams', 'examination', 'examinations', 'exam revision', 'practice test', 'practice tests', 'test preparation'),
    'Proofreading': ('proofread', 'proofreading', 'proofreader'),
    'Thesis editing': ('thesis', 'dissertation', 'academic editing'),
    'Writing tutoring': ('writing tutor', 'writing help', 'essay feedback', 'assignment help'),
    'Research guidance': ('research proposal', 'research methods', 'methodology', 'literature review'),
    'Referencing': ('referencing', 'citation', 'bibliography', 'harvard referencing', 'apa style'),
}
DEFAULT_TOPICS = ('Assignment support', 'Thesis & dissertation support', 'Quiz preparation', 'Homework & coursework')
ACADEMIC = re.compile(
    r'\b(thesis|theses|dissertations?|assignments?|essays?|quizzes|quiz|homework|course\s?work|'
    r'exams?|examinations?|practice tests?|test preparation|lab reports?|university|undergraduate|'
    r'postgraduate|academic|phd|research proposal|literature review)\b', re.I)
REQUEST = re.compile(r'\b(looking for|need(?:ing)?|seeking|can anyone|could anyone|recommend|help me|struggling|looking to hire)\b', re.I)
ADVERT = re.compile(r'\b(we offer|our services|hire me|dm me|contact us|guaranteed grades|assignment writing service|essay writing service)\b', re.I)
MARKETS = {
    'UK': re.compile(r'\b(united kingdom|uk|british|england|scotland|wales|northern ireland|uk university|uk curriculum)\b', re.I),
    'US': re.compile(r'\b(united states|usa|(?-i:US)|american university|us university|us college|us curriculum)\b|\b(u\.s\.)', re.I),
}
CURRICULUM = re.compile(r'\b(uk curriculum|british curriculum|harvard referencing|apa(?:\s*7)?|mla|chicago|undergraduate|postgraduate|phd)\b', re.I)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def source_url(value):
    """Accept post links only; remove trackers and reject lookalike hosts/DMs."""
    try:
        u = urlsplit(str(value).strip())
        if u.scheme not in ('http', 'https') or u.username or u.password or u.port not in (None, 80, 443):
            return None
        host = (u.hostname or '').lower()
        path = u.path.rstrip('/')
        if host in ('reddit.com', 'www.reddit.com', 'old.reddit.com', 'new.reddit.com'):
            match = re.fullmatch(r'/r/([\w]{2,21})/comments/([a-z0-9]+)(?:/[^/]*)?(?:/([a-z0-9]+))?', path, re.I)
            if match:
                sub, post, comment = match.groups()
                url = f'https://www.reddit.com/r/{sub.lower()}/comments/{post.lower()}/'
                if comment:
                    url += '_/' + comment.lower() + '/'
                return 'Reddit', url
        if host in ('facebook.com', 'www.facebook.com', 'm.facebook.com', 'mbasic.facebook.com'):
            if re.fullmatch(r'/(?:groups/)?[\w.\-]+/(?:posts|permalink)/[\w.\-]+', path):
                return 'Facebook', 'https://www.facebook.com' + path
            if path in ('/permalink.php', '/story.php'):
                q = parse_qs(u.query)
                story, page = q.get('story_fbid', [''])[0], q.get('id', [''])[0]
                if re.fullmatch(r'[A-Za-z0-9]+', story) and page.isdigit():
                    return 'Facebook', f'https://www.facebook.com/permalink.php?story_fbid={story}&id={page}'
        if host in ('discord.com', 'www.discord.com', 'discordapp.com') and re.fullmatch(r'/channels/\d{15,22}/\d{15,22}/\d{15,22}', path):
            return 'Discord', 'https://discord.com' + path
    except (ValueError, TypeError):
        pass
    return None


def clean_text(value, limit=1600):
    text = re.sub(r'<[^>]+>', ' ', str(value or ''))
    text = re.sub(r'[\w.+\-]+@[\w.\-]+\.[a-zA-Z]{2,}', '[email omitted]', text)
    text = re.sub(r'(?<!\w)\+?\d[\d ()\-]{8,}\d(?!\w)', '[number omitted]', text)
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def assess(record, settings):
    """Query geography and subreddit names are never evidence of a person's location."""
    text = record['title'] + ' ' + record['excerpt']
    service = [name for name in settings['services'] if any(re.search(r'\b' + re.escape(term) + r'\b', text, re.I) for term in SERVICES[name])]
    custom = [term for term in settings.get('keywords', []) if term.casefold() in text.casefold()]
    academic = bool(ACADEMIC.search(text))
    advert, request = bool(ADVERT.search(text)), bool(REQUEST.search(text))
    intent = 'Service advertisement' if advert else 'Possible request' if request and academic else 'Discussion'
    evidence = {market: list(dict.fromkeys(m.group(0) for m in pattern.finditer(text))) for market, pattern in MARKETS.items() if pattern.search(text)}
    region = next(iter(evidence)) if len(evidence) == 1 else 'Mixed' if evidence else 'Unknown'
    score = min(100, (25 if academic else 0) + (25 if service or custom else 0) + (30 if intent == 'Possible request' else 0) + (20 if region in settings['markets'] else 0))
    # Indexed excerpts and geography mentions are discovery hints, never verified people.
    return {**record, 'services': service, 'keyword_matches': custom, 'intent': intent,
            'market': region, 'market_evidence': evidence, 'curriculum_mentions': list(dict.fromkeys(CURRICULUM.findall(text))),
            'score': score, 'relevant': academic and bool(service or custom), 'review_status': 'Needs review'}


def normalize_record(row, settings, source_type='Imported post'):
    parsed = source_url(row.get('url') or row.get('source_url') or row.get('permalink') or '')
    if not parsed or parsed[0] not in settings['platforms']:
        return None
    record = {'platform': parsed[0], 'url': parsed[1], 'title': clean_text(row.get('title'), 250),
              'excerpt': clean_text(row.get('text') or row.get('body') or row.get('content') or row.get('excerpt')),
              'posted_at': '', 'collected_at': utc_now(), 'source_type': source_type}
    date = str(row.get('posted_at') or row.get('created_at') or row.get('timestamp') or '')
    if date:
        try:
            dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
            if dt.tzinfo:
                record['posted_at'] = dt.astimezone(timezone.utc).isoformat(timespec='seconds')
        except ValueError:
            pass
    return assess(record, settings)


def validate_settings(values):
    settings = dict(values)
    if not str(settings.get('name', '')).strip():
        raise ValueError('Enter a research name.')
    for key, allowed in (('platforms', PLATFORMS), ('services', SERVICES), ('markets', ('UK', 'US'))):
        if not isinstance(settings.get(key), list) or not settings[key] or any(v not in allowed for v in settings[key]):
            raise ValueError('Choose valid ' + key + '.')
    keywords = settings.get('keywords', [])
    if not isinstance(keywords, list) or len(keywords) > 8 or any(not isinstance(v, str) or not 2 <= len(v.strip()) <= 80 for v in keywords):
        raise ValueError('Use up to eight extra phrases, each 2–80 characters.')
    for key, bounds in (('target', (1, 5000)), ('max_requests', (1, 200)), ('max_pages', (1, 5))):
        if type(settings.get(key)) is not int or not bounds[0] <= settings[key] <= bounds[1]:
            raise ValueError(f'{key} must be {bounds[0]}–{bounds[1]}.')
    settings['name'] = settings['name'].strip()[:150]
    settings['keywords'] = [v.strip() for v in keywords]
    return settings


def import_records(raw, filename, settings):
    """Normalized JSON/CSV or DiscordChatExporter JSON; ignores author/member data."""
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('Import up to 5 MB at a time.')
    try:
        text = raw.decode('utf-8-sig')
        if filename.lower().endswith('.csv'):
            rows = list(csv.DictReader(io.StringIO(text)))
        else:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get('messages'), list):
                guild, channel = str(data.get('guild', {}).get('id', '')), str(data.get('channel', {}).get('id', ''))
                rows = [{**m, 'url': f'https://discord.com/channels/{guild}/{channel}/{m.get("id", "")}'} for m in data['messages'] if isinstance(m, dict)]
            elif isinstance(data, dict):
                rows = data.get('posts', [])
            else:
                rows = data
        if not isinstance(rows, list) or len(rows) > 10000 or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Use an array of up to 10,000 post objects.')
        accepted, skipped = [], 0
        for row in rows:
            record = normalize_record(row, settings)
            if record and record['relevant']:
                accepted.append(record)
            else:
                skipped += 1
        return accepted, skipped
    except (UnicodeError, json.JSONDecodeError, csv.Error, AttributeError, TypeError) as exc:
        raise ValueError('Cannot read this export. Use UTF-8 CSV or a JSON list with url, title and text fields.') from None


def export_records(records):
    fields = ('platform', 'url', 'title', 'excerpt', 'source_type', 'posted_at', 'collected_at', 'intent', 'market',
              'market_evidence', 'curriculum_mentions', 'services', 'keyword_matches', 'score', 'review_status')
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for record in records:
        row = {}
        for key in fields:
            value = record.get(key, '')
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                value = "'" + value
            row[key] = value
        writer.writerow(row)
    return ('\ufeff' + buffer.getvalue()).encode('utf-8')
