"""Server-only integration settings. Secret inputs never echo stored credentials."""
import json
from pathlib import Path
import re
import secrets
import tomllib
from .config import config, BASE_DIR, DATA_DIR
from .accounts import role_for
from .local_lock import LocalLock

FIELDS=('GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_REDIRECT_URI','AUTH_COOKIE_SECRET',
        'TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_WHATSAPP_FROM','TWILIO_CONTENT_SID',
        'WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_API_VERSION',
        'WHATSAPP_TEMPLATE_NAME','WHATSAPP_TEMPLATE_LANGUAGE')


def load(store):
    return {**{key:getattr(config,key,'') for key in FIELDS},**store.get('integrations',{})}


def save(actor,values,store):
    if role_for(actor,store)!='admin':
        raise PermissionError('Only the admin can change API credentials.')
    if set(values)-set(FIELDS):
        raise ValueError('Unsupported integration setting.')
    changes={key:str(value).strip() for key,value in values.items() if str(value).strip()}
    if any('\n' in v or '\r' in v for v in changes.values()):
        raise ValueError('Integration values must be on one line.')
    for key,prefix in (('TWILIO_ACCOUNT_SID','AC'),('TWILIO_CONTENT_SID','HX')):
        if key in changes and not re.fullmatch(prefix+r'[a-fA-F0-9]{32}',changes[key]):
            raise ValueError(f'{key} must be a valid {prefix} identifier.')
    if 'WHATSAPP_PHONE_NUMBER_ID' in changes and not changes['WHATSAPP_PHONE_NUMBER_ID'].isdigit():
        raise ValueError('Meta phone number ID must contain digits only.')
    if 'WHATSAPP_API_VERSION' in changes and not re.fullmatch(r'v\d+\.\d+',changes['WHATSAPP_API_VERSION']):
        raise ValueError('Graph API version must look like v23.0.')
    if 'GOOGLE_REDIRECT_URI' in changes:
        from urllib.parse import urlparse
        uri=urlparse(changes['GOOGLE_REDIRECT_URI'])
        if uri.path!='/oauth2callback' or (uri.scheme!='https' and not (uri.scheme=='http' and uri.hostname in ('localhost','127.0.0.1'))):
            raise ValueError('Google redirect must be HTTPS and end in /oauth2callback (localhost HTTP is allowed).')
    with LocalLock(store.path+'.integrations.lock'):
        saved=store.get('integrations',{})
        saved.update(changes)
        if changes.get('GOOGLE_CLIENT_ID') and not saved.get('AUTH_COOKIE_SECRET') and not config.AUTH_COOKIE_SECRET:
            saved['AUTH_COOKIE_SECRET']=secrets.token_urlsafe(48)
        store.set('integrations',saved)
    store.event('Admin updated integration settings. Credential values are not logged.')


def sync_google_secrets(store):
    """Keep Streamlit's supported OIDC configuration available after cloud restore."""
    values=load(store)
    if not values['GOOGLE_CLIENT_ID'] or not values['GOOGLE_CLIENT_SECRET']:
        return False
    target=BASE_DIR/'.streamlit'/'secrets.toml'
    auth={'redirect_uri':values['GOOGLE_REDIRECT_URI'],
          'cookie_secret':values['AUTH_COOKIE_SECRET'] or config.AUTH_COOKIE_SECRET,
          'google':{'client_id':values['GOOGLE_CLIENT_ID'],'client_secret':values['GOOGLE_CLIENT_SECRET'],
                    'server_metadata_url':'https://accounts.google.com/.well-known/openid-configuration'}}
    if not auth['cookie_secret']:
        raise ValueError('Configure a random AUTH_COOKIE_SECRET before enabling Google login.')
    with LocalLock(DATA_DIR/'runtime'/'google-settings.lock'):
        previous=tomllib.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
        if previous.get('auth')==auth:
            return True
        previous['auth']=auth
        import toml
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=DATA_DIR/'runtime'/'google-secrets.tmp'
        temporary.write_text(toml.dumps(previous),encoding='utf-8')
        temporary.replace(target)
    return True


def save_supabase_environment(actor,values,store):
    if role_for(actor,store)!='admin':
        raise PermissionError('Only the admin can configure Supabase.')
    if config.CLOUD_MODE:
        raise ValueError('Update Supabase connection secrets in Streamlit Cloud, then reboot the app.')
    from dotenv import set_key
    allowed={'SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY','SUPABASE_ANON_KEY','APP_PUBLIC_URL'}
    if set(values)-allowed:
        raise ValueError('Unsupported server setting.')
    if values.get('SUPABASE_SERVICE_ROLE_KEY','').startswith('sb_publishable_'):
        raise ValueError('Cloud storage needs a server secret/service-role key, not a publishable key.')
    with LocalLock(DATA_DIR/'runtime'/'environment.lock'):
        for key,value in values.items():
            if str(value).strip(): set_key(str(BASE_DIR/'.env'),key,str(value).strip())
