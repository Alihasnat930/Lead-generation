"""Prepare private Streamlit secrets without printing passwords or credentials."""
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.config import config, DATA_DIR, hash_app_password


def main():
    folder=DATA_DIR/'deployment'
    folder.mkdir(parents=True,exist_ok=True)
    password_file=folder/'client-password.txt'
    if not password_file.exists():
        password_file.write_text(secrets.token_urlsafe(20)+'\n',encoding='utf-8')
    password=password_file.read_text(encoding='utf-8').strip()
    values={key:getattr(config,key) for key in (
        'GMAIL_ADDRESS','GMAIL_APP_PASSWORD','GOOGLE_SHEET_ID','SUPABASE_URL',
        'SUPABASE_SERVICE_ROLE_KEY','SUPABASE_ANON_KEY','APP_AUTH_MODE','APP_ADMIN_EMAILS','APP_PUBLIC_URL',
        'GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','GOOGLE_REDIRECT_URI','AUTH_COOKIE_SECRET',
        'TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_WHATSAPP_FROM','TWILIO_CONTENT_SID',
        'WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_API_VERSION',
        'WHATSAPP_TEMPLATE_NAME','WHATSAPP_TEMPLATE_LANGUAGE',
        'YOUR_NAME','LINKEDIN_URL','GITHUB_URL','UPWORK_URL')}
    values['APP_LOGIN_PASSWORD_HASH']=hash_app_password(password)
    values['SERVICE_ACCOUNT_JSON']=config.SERVICE_ACCOUNT_JSON or Path(config.SERVICE_ACCOUNT_FILE).read_text(encoding='utf-8')
    target=folder/'streamlit-secrets.toml'
    target.write_text('\n'.join(f'{key} = {json.dumps(value,ensure_ascii=True)}' for key,value in values.items())+'\n',encoding='utf-8')
    print('Private files prepared in data/deployment. Never commit or share the secrets file.')
    missing=[key for key in ('SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY') if not values[key]]
    if missing:
        print('Still required in private configuration: '+', '.join(missing))


if __name__=='__main__':
    main()
