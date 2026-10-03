"""WhatsApp templates via Twilio or Meta; queue acceptance is not delivery."""
from datetime import datetime
import hashlib
import json
import re
import time
import requests
from . import integrations
from .local_lock import LocalLock
from .outreach_store import OutreachStore, TZ, local_day


def phone_number(value):
    value=str(value).strip().removeprefix('whatsapp:')
    if not re.fullmatch(r'\+[1-9]\d{7,14}',value):
        raise ValueError('Use an international phone number, for example +14155551234.')
    return value


def _credentials(provider,values,template=False):
    if provider=='twilio':
        if not re.fullmatch(r'AC[a-fA-F0-9]{32}',values.get('TWILIO_ACCOUNT_SID','')) or not values.get('TWILIO_AUTH_TOKEN'):
            raise ValueError('Configure a valid Twilio account SID and auth token.')
        if template:
            phone_number(values.get('TWILIO_WHATSAPP_FROM',''))
            if not re.fullmatch(r'HX[a-fA-F0-9]{32}',values.get('TWILIO_CONTENT_SID','')):
                raise ValueError('Configure an approved Twilio Content SID.')
    elif provider=='meta':
        if not values.get('WHATSAPP_ACCESS_TOKEN') or not str(values.get('WHATSAPP_PHONE_NUMBER_ID','')).isdigit():
            raise ValueError('Configure your Meta access token and phone number ID.')
        if not re.fullmatch(r'v\d+\.\d+',values.get('WHATSAPP_API_VERSION','')):
            raise ValueError('Configure a valid Graph API version.')
        if template and not re.fullmatch(r'[a-z0-9_]{1,512}',values.get('WHATSAPP_TEMPLATE_NAME','')):
            raise ValueError('Configure an approved WhatsApp template name.')
    else:
        raise ValueError('Choose Twilio or Meta.')


def check_authentication(provider,values):
    _credentials(provider,values)
    try:
        if provider=='twilio':
            sid=values['TWILIO_ACCOUNT_SID']
            response=requests.get(f'https://api.twilio.com/2010-04-01/Accounts/{sid}.json',
                auth=(sid,values['TWILIO_AUTH_TOKEN']),timeout=25)
            if response.status_code!=200 or response.json().get('sid')!=sid or response.json().get('status')!='active':
                raise ValueError('Twilio credentials were rejected or the account is not active.')
        else:
            response=requests.get(f"https://graph.facebook.com/{values['WHATSAPP_API_VERSION']}/{values['WHATSAPP_PHONE_NUMBER_ID']}",
                headers={'Authorization':'Bearer '+values['WHATSAPP_ACCESS_TOKEN']},params={'fields':'id'},timeout=25)
            if response.status_code!=200 or str(response.json().get('id'))!=values['WHATSAPP_PHONE_NUMBER_ID']:
                raise ValueError('Meta credentials were rejected or the phone number is not accessible.')
    except requests.RequestException:
        raise ValueError('Provider connection failed. No message was sent.') from None
    return True


def initialize(store):
    with store.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS messaging_contacts(
          phone TEXT PRIMARY KEY, opted_in INTEGER NOT NULL, evidence TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS channel_messages(
          id TEXT PRIMARY KEY, provider TEXT NOT NULL, phone TEXT NOT NULL, payload TEXT NOT NULL,
          state TEXT NOT NULL, created REAL NOT NULL, attempt_day TEXT DEFAULT '',
          provider_id TEXT DEFAULT '', reason TEXT DEFAULT '');
        ''')


def set_consent(store,phone,opted_in,evidence):
    phone=phone_number(phone)
    if opted_in and len(str(evidence).strip())<5:
        raise ValueError('Record where and when this person opted in to WhatsApp messages.')
    initialize(store)
    with store.db() as db:
        db.execute('INSERT OR REPLACE INTO messaging_contacts VALUES(?,?,?,?)',
            (phone,int(bool(opted_in)),str(evidence).strip()[:1000],time.time()))


def _allowed(store,phone):
    with store.db() as db:
        row=db.execute('SELECT opted_in FROM messaging_contacts WHERE phone=?',(phone,)).fetchone()
    return bool(row and row[0]) and not store.stopped('',phone)


def queue_message(store,provider,phone,parameters):
    from .config import config
    if not config.CLOUD_MODE and store.get('cloud_migrated',False):
        raise ValueError('Use the hosted workspace after cloud migration.')
    initialize(store)
    phone=phone_number(phone)
    if not _allowed(store,phone):
        raise ValueError('Record WhatsApp opt-in for this recipient before queueing a message.')
    values=integrations.load(store)
    _credentials(provider,values,template=True)
    if not isinstance(parameters,list) or len(parameters)>20 or any(not isinstance(v,str) or len(v)>1000 for v in parameters):
        raise ValueError('Use at most 20 text template variables, up to 1,000 characters each.')
    payload={'template':values['TWILIO_CONTENT_SID'] if provider=='twilio' else values['WHATSAPP_TEMPLATE_NAME'],
             'language':values['WHATSAPP_TEMPLATE_LANGUAGE'],'parameters':parameters,
             'sender':values['TWILIO_WHATSAPP_FROM'] if provider=='twilio' else values['WHATSAPP_PHONE_NUMBER_ID']}
    encoded=json.dumps(payload,sort_keys=True)
    identifier=hashlib.sha256((provider+phone+encoded+local_day()).encode()).hexdigest()
    with store.db() as db:
        db.execute('INSERT OR IGNORE INTO channel_messages(id,provider,phone,payload,state,created) VALUES(?,?,?,?,?,?)',
            (identifier,provider,phone,encoded,'queued',time.time()))
    return identifier


def messages(store):
    initialize(store)
    with store.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM channel_messages ORDER BY created DESC LIMIT 300')]


def cancel_message(store,identifier):
    initialize(store)
    with store.db() as db:
        db.execute("UPDATE channel_messages SET state='cancelled',reason='Cancelled in workspace' WHERE id=? AND state='queued'",(identifier,))


def submit(provider,phone,payload,values):
    _credentials(provider,values)
    try:
        if provider=='twilio':
            sid=values['TWILIO_ACCOUNT_SID']
            response=requests.post(f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json',
                auth=(sid,values['TWILIO_AUTH_TOKEN']),timeout=25,
                data={'From':'whatsapp:'+phone_number(payload['sender']),'To':'whatsapp:'+phone,
                      'ContentSid':payload['template'],'ContentVariables':json.dumps({str(i+1):v for i,v in enumerate(payload['parameters'])})})
        else:
            template={'name':payload['template'],'language':{'code':payload['language']}}
            if payload['parameters']:
                template['components']=[{'type':'body','parameters':[{'type':'text','text':v} for v in payload['parameters']]}]
            response=requests.post(f"https://graph.facebook.com/{values['WHATSAPP_API_VERSION']}/{payload['sender']}/messages",
                headers={'Authorization':'Bearer '+values['WHATSAPP_ACCESS_TOKEN']},timeout=25,
                json={'messaging_product':'whatsapp','recipient_type':'individual','to':phone.lstrip('+'),'type':'template','template':template})
        if response.status_code>=500:
            return {'state':'uncertain','reason':'Provider server error. Check provider logs before any retry.','provider_id':''}
        if not 200<=response.status_code<300:
            return {'state':'failed','reason':f'Provider rejected submission (HTTP {response.status_code}).','provider_id':''}
        data=response.json()
        if provider=='twilio' and data.get('status') in ('failed','undelivered'):
            return {'state':'failed','reason':'Twilio reported a failed submission.','provider_id':data.get('sid','')}
        identifier=data.get('sid') if provider=='twilio' else (data.get('messages') or [{}])[0].get('id')
        if not identifier:
            return {'state':'uncertain','reason':'Provider returned no message identifier. Automatic resend is blocked.','provider_id':''}
        return {'state':'accepted','reason':'Accepted by provider; final delivery is not yet confirmed.','provider_id':identifier}
    except (requests.RequestException,ValueError,KeyError,TypeError):
        return {'state':'uncertain','reason':'Submission response was lost or invalid. Check provider logs; do not resend automatically.','provider_id':''}


def run_queue(store=None,limit=None,scheduled=False):
    from .config import config
    store=store or OutreachStore()
    if not config.CLOUD_MODE and store.get('cloud_migrated',False):
        raise ValueError('Send from the hosted workspace after migration.')
    initialize(store)
    cap=store.settings()['whatsapp_daily_limit']
    sent=0
    with LocalLock(store.path+'.messaging.lock'):
        with store.db() as db:
            # A process that died after reservation cannot cause a second submission.
            db.execute("UPDATE channel_messages SET state='uncertain',reason='Interrupted submission; inspect provider logs.' WHERE state='sending'")
            queued=[dict(r) for r in db.execute("SELECT * FROM channel_messages WHERE state='queued' ORDER BY created LIMIT 300")]
        for item in queued:
            if scheduled and not store.settings()['auto_whatsapp_enabled']: break
            if limit is not None and sent>=limit: break
            with store.db() as db:
                used=db.execute("SELECT count(*) FROM channel_messages WHERE attempt_day=? AND state IN ('sending','accepted','uncertain')",(local_day(),)).fetchone()[0]
            if used>=cap: break
            if not _allowed(store,item['phone']):
                with store.db() as db:
                    db.execute("UPDATE channel_messages SET state='blocked',reason='Opt-in removed or contact suppressed' WHERE id=?",(item['id'],))
                continue
            values=integrations.load(store)
            payload=json.loads(item['payload'])
            sender=values['TWILIO_WHATSAPP_FROM'] if item['provider']=='twilio' else values['WHATSAPP_PHONE_NUMBER_ID']
            if payload['sender']!=sender:
                raise ValueError('Provider sender changed. Cancel and recreate the queued message.')
            _credentials(item['provider'],values)
            with store.db() as db:
                claim=db.execute("UPDATE channel_messages SET state='sending',attempt_day=? WHERE id=? AND state='queued'",(local_day(),item['id']))
                if claim.rowcount!=1: continue
            # OutreachStore commits and durably checkpoints this reservation before HTTP POST.
            if not _allowed(store,item['phone']):
                with store.db() as db:
                    db.execute("UPDATE channel_messages SET state='blocked',reason='Consent revoked before submission' WHERE id=?",(item['id'],))
                continue
            if scheduled and not store.settings()['auto_whatsapp_enabled']:
                with store.db() as db:
                    db.execute("UPDATE channel_messages SET state='queued',attempt_day='' WHERE id=?",(item['id'],))
                break
            result=submit(item['provider'],item['phone'],payload,values)
            with store.db() as db:
                db.execute('UPDATE channel_messages SET state=?,provider_id=?,reason=? WHERE id=?',
                    (result['state'],result['provider_id'],result['reason'],item['id']))
            if result['state']!='accepted': break
            sent+=1
    return sent
