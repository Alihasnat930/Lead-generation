"""Google Sheets CRM adapter with explicit ranges and verified writes."""
from datetime import datetime, timezone
import hashlib
import json
import time
import gspread
from gspread.exceptions import APIError, WorksheetNotFound
from google.oauth2.service_account import Credentials
from .config import config, DATA_DIR
from .local_lock import LocalLock
from .websites import domain_from_url

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
_client = None
_sheet = None
LEADS_HEADERS = ['lead_id','company_name','website','domain','industry','city','state','country',
    'phone','email','email_source','email_status','decision_maker','decision_maker_role','source',
    'source_url','lead_score','fit','likely_problem','recommended_service','outreach_angle',
    'linkedin_url','linkedin_status','status','created_at','updated_at','last_contacted','next_followup','followup_count']
SUPPRESSION_HEADERS = ['email','domain','reason','added_at']


def get_client():
    global _client
    if _client is None:
        if config.SERVICE_ACCOUNT_JSON:
            creds = Credentials.from_service_account_info(json.loads(config.SERVICE_ACCOUNT_JSON), scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file(config.SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
        _client.set_timeout(25)
    return _client


def get_spreadsheet():
    global _sheet
    if _sheet is None:
        try:
            _sheet = get_client().open_by_key(config.GOOGLE_SHEET_ID)
        except PermissionError:
            raise PermissionError('The service account cannot access the configured app Sheet. Share that Sheet with its service-account email.') from None
    return _sheet


def get_or_create_worksheet(name, headers):
    book = get_spreadsheet()
    try:
        return book.worksheet(name)
    except WorksheetNotFound:
        ws = book.add_worksheet(title=name,rows=1000,cols=max(32,len(headers)))
        ws.update(range_name='A1',values=[headers],value_input_option='RAW')
        return ws


def worksheet_options():
    return [(ws.title,ws.id) for ws in get_spreadsheet().worksheets()]


def _permission_error(exc):
    if isinstance(exc,APIError) and getattr(exc.response,'status_code',None)==403:
        return PermissionError('Google Sheets write permission denied. Give the configured service account Editor access to this app Sheet.')
    return exc


def _col_letter(number):
    result = ''
    while number:
        number, digit = divmod(number-1,26)
        result = chr(65+digit)+result
    return result


def _values(ws):
    # row_values/get_all_values can include thousands of trailing empty columns.
    headers = [str(h).strip() for h in ws.row_values(1)]
    while headers and not headers[-1]:
        headers.pop()
    if not headers:
        return []
    if len(headers)>300:
        raise ValueError('CRM headers span more than 300 columns. Check the selected worksheet.')
    rows = list(ws.get(f'A1:{_col_letter(len(headers))}{ws.row_count}'))
    if not rows:
        return [headers]
    rows[0] = headers
    return rows


def _read_records_from_values(values, headers):
    return [{key:row[i] if i<len(row) else '' for i,key in enumerate(headers) if key} for row in values[1:]]


def _read_records(ws, expected_headers):
    values = _values(ws)
    if not values:
        return []
    indexes = {h:values[0].index(h) for h in expected_headers if h in values[0]}
    records = []
    for number,row in enumerate(values[1:],2):
        record = {h:row[indexes[h]] if h in indexes and indexes[h]<len(row) else '' for h in expected_headers}
        if any(record.values()):
            record['_row_number'] = number
            records.append(record)
    return records


def read_all(sheet_name, headers):
    return _read_records(get_or_create_worksheet(sheet_name,headers),headers)


def outreach_snapshot(sheet_name='Leads'):
    """No writes while checking eligibility; missing columns stop sending."""
    book = get_spreadsheet()
    ws = book.worksheet(sheet_name)
    values = _values(ws)
    if not values:
        raise ValueError('The outreach worksheet is empty. Sync qualified leads first.')
    for key in ('domain','email','status','lead_score','last_contacted','next_followup','followup_count'):
        if values[0].count(key)!=1:
            raise ValueError(f"The outreach worksheet must contain exactly one '{key}' column.")
    records = _read_records_from_values(values,values[0])
    try:
        suppression = _read_records(book.worksheet('Suppression'),SUPPRESSION_HEADERS)
    except WorksheetNotFound:
        raise ValueError('Create a Suppression tab with email, domain, reason and added_at headers before sending.') from None
    return [r for r in records if any(r.values())], suppression


def suppressed(domain, email, records):
    return any((email and str(r.get('email','')).strip().lower()==email.lower()) or
               (domain and domain_from_url(r.get('domain',''))==domain) for r in records)


def domain_in_suppression(domain,email):
    return suppressed(domain_from_url(domain),email,read_all('Suppression',SUPPRESSION_HEADERS))


def update_lead_fields(domain, fields, sheet_name='Leads', email=None):
    ws = get_spreadsheet().worksheet(sheet_name)
    values = _values(ws)
    if not values or values[0].count('domain')!=1:
        raise ValueError('CRM domain column is missing or duplicated.')
    headers = values[0]
    records = _read_records_from_values(values,headers)
    matches = [(i+2,r) for i,r in enumerate(records) if domain_from_url(r.get('domain',''))==domain_from_url(domain)]
    if len(matches)!=1:
        raise ValueError('CRM business is missing or duplicated; no status fields were changed.')
    row_number, row = matches[0]
    if email and row.get('email','').strip().lower()!=email.lower():
        raise ValueError('The CRM recipient changed. Review the saved delivery before updating this row.')
    changes = []
    for key,value in fields.items():
        if headers.count(key)!=1:
            raise ValueError(f"CRM update requires exactly one '{key}' column.")
        changes.append({'range':f'{_col_letter(headers.index(key)+1)}{row_number}','values':[[str(value)]]})
    if changes:
        ws.batch_update(changes,value_input_option='RAW')
    return True


def append_row_to(sheet_name,headers,row_dict):
    ws=get_or_create_worksheet(sheet_name,headers)
    values=_values(ws)
    actual=values[0] if values else headers
    ws.update(range_name=f'A{len(values)+1}',values=[[str(row_dict.get(h,'')) for h in actual]],value_input_option='RAW')


def upsert_lead(lead):
    rows=read_all('Leads',LEADS_HEADERS)
    if any(domain_from_url(r.get('domain',''))==domain_from_url(lead.get('domain','')) for r in rows):
        update_lead_fields(lead['domain'],{k:v for k,v in lead.items() if k in LEADS_HEADERS})
        return 'updated'
    append_row_to('Leads',LEADS_HEADERS,lead)
    return 'inserted'


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def today_str():
    from .outreach_store import local_day
    return local_day()


def append_new_qualified(leads, sheet_name='Leads'):
    identity=hashlib.sha256((config.GOOGLE_SHEET_ID+'|'+sheet_name).encode()).hexdigest()[:16]
    with LocalLock(DATA_DIR/'runtime'/f'sheet-{identity}.lock'):
        ws=get_or_create_worksheet(sheet_name,LEADS_HEADERS)
        values=_values(ws)
        if not values:
            ws.update(range_name='A1',values=[LEADS_HEADERS],value_input_option='RAW')
            values=[LEADS_HEADERS[:]]
        headers=values[0]
        for required in ('domain','email','status'):
            if headers.count(required)!=1:
                raise ValueError(f"CRM must have exactly one '{required}' column before syncing.")
        records=_read_records_from_values(values,headers)
        domains={domain_from_url(r.get('domain','')):i+2 for i,r in enumerate(records) if r.get('domain')}
        emails={r.get('email','').strip().lower():i+2 for i,r in enumerate(records) if r.get('email')}
        protected={'status','last_contacted','next_followup','followup_count','updated_at'}
        pending, updates, expected = [],[],set()
        for lead in leads:
            if lead.get('qualification_status')!='QUALIFIED':
                continue
            domain=domain_from_url(lead.get('domain',''))
            email=str(lead.get('email','')).strip().lower()
            if not domain:
                continue
            existing=domains.get(domain) or (emails.get(email) if email else None)
            if existing:
                if existing<0:
                    continue
                old=records[existing-2]
                for index,h in enumerate(headers,1):
                    if h and h not in protected and h in lead and not str(old.get(h,'')).strip() and lead[h] not in ('',None):
                        updates.append({'range':f'{_col_letter(index)}{existing}','values':[[str(lead[h])]]})
                continue
            new=dict(lead,domain=domain,email=email,status='QUALIFIED')
            pending.append([str(new.get(h,'')) if h else '' for h in headers])
            domains[domain]=-1
            if email: emails[email]=-1
            expected.add(domain)
        for start in range(0,len(updates),100):
            ws.batch_update(updates[start:start+100],value_input_option='RAW')
        # Write A..last-header explicitly. Never let Sheets guess where a table starts.
        last_used=max([1]+[i+2 for i,row in enumerate(values[1:]) if any(row)])
        needed=last_used+len(pending)
        if needed>ws.row_count:
            ws.add_rows(needed-ws.row_count)
        for offset in range(0,len(pending),200):
            ws.update(range_name=f'A{last_used+1+offset}',values=pending[offset:offset+200],value_input_option='RAW')
        confirmed=set()
        for attempt in range(3):
            written=_read_records(ws,LEADS_HEADERS)
            confirmed={domain_from_url(r.get('domain','')) for r in written}
            if expected<=confirmed:
                break
            time.sleep(attempt+1)
        if expected-confirmed:
            raise ValueError(f"Sheet did not confirm {len(expected-confirmed)} new lead rows. Recheck the destination; retrying will deduplicate confirmed rows.")
        return {'inserted':len(pending),'updated':len(updates),'worksheet':ws.title,
                'worksheet_id':ws.id,'spreadsheet_url':f'{ws.spreadsheet.url}#gid={ws.id}',
                'verified_domains':len(confirmed-{''})}
