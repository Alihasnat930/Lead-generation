"""Verified identities plus a separately managed workspace approval list."""
import time
from .config import config
from .websites import EMAIL_RE


def normalize_email(value):
    email=str(value or '').strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise ValueError('Enter a valid email address.')
    return email


def admins():
    return {e.strip().lower() for e in config.APP_ADMIN_EMAILS.split(',') if e.strip()}


def auth_client():
    if not config.SUPABASE_URL or not config.SUPABASE_ANON_KEY:
        raise ValueError('Email login needs SUPABASE_URL and SUPABASE_ANON_KEY in server settings.')
    from supabase import create_client, ClientOptions
    # Never share a mutable Supabase authentication client between browser sessions.
    return create_client(config.SUPABASE_URL,config.SUPABASE_ANON_KEY,
        options=ClientOptions(auto_refresh_token=False,persist_session=False))


def _session(response):
    user,session=response.user,response.session
    if not user or not session or not user.email_confirmed_at:
        raise ValueError('Verify your email address before signing in.')
    return {'access_token':session.access_token,'refresh_token':session.refresh_token,
            'expires_at':session.expires_at,'signed_in_at':time.time()}


def sign_in(email,password):
    email=normalize_email(email)
    if not password:
        raise ValueError('Enter your password.')
    try:
        return _session(auth_client().auth.sign_in_with_password({'email':email,'password':password}))
    except ValueError:
        raise
    except Exception:
        raise ValueError('Sign-in failed. Check your password and email verification, then try again.') from None


def sign_up(email,password,name):
    email=normalize_email(email)
    if len(password)<12:
        raise ValueError('Use a password with at least 12 characters.')
    try:
        auth_client().auth.sign_up({'email':email,'password':password,
            'options':{'data':{'display_name':str(name).strip()[:100]},'email_redirect_to':config.APP_PUBLIC_URL}})
    except Exception:
        raise ValueError('Signup could not be completed. Check the authentication configuration or try again later.') from None
    # Do not create a workspace session or grant access from a signup response.
    return 'Check your inbox to verify your email, then sign in. Workspace access requires admin approval.'


def verify_session(saved,now=None):
    now=time.time() if now is None else now
    if now-saved.get('signed_in_at',0)>8*3600:
        raise ValueError('Your session expired. Sign in again.')
    client=auth_client()
    try:
        if saved.get('expires_at',0)<=now+60:
            refreshed=_session(client.auth.refresh_session(saved['refresh_token']))
            refreshed['signed_in_at']=saved['signed_in_at']
            saved.update(refreshed)
        user=client.auth.get_user(saved['access_token']).user
        if not user or not user.email_confirmed_at:
            raise ValueError('Email verification is required.')
        return {'email':normalize_email(user.email),'subject':user.id,'provider':'email','verified':True}
    except Exception:
        raise ValueError('Your session could not be verified. Sign in again.') from None


def google_identity(user,now=None):
    now=time.time() if now is None else now
    if user.get('email_verified') is not True or not user.get('sub') or user.get('iss') not in ('accounts.google.com','https://accounts.google.com'):
        raise ValueError('A verified Google email is required.')
    if user.get('exp') and float(user['exp'])<=now:
        raise ValueError('Your Google session expired. Sign out and sign in again.')
    return {'email':normalize_email(user.get('email')),'subject':user['sub'],'provider':'google','verified':True}


def role_for(identity,store):
    if not identity or identity.get('verified') is not True:
        return None
    try:
        email=normalize_email(identity.get('email'))
    except ValueError:
        return None
    if email in admins():
        return 'admin'
    member=store.get('access_members',{}).get(email,{})
    return member.get('role','member') if member.get('status')=='approved' else None


def request_access(identity,store):
    if identity.get('verified') is not True:
        raise ValueError('Verify your account first.')
    email=normalize_email(identity['email'])
    with store.db() as db:
        import json
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT value FROM settings WHERE key='access_members'").fetchone()
        members=json.loads(row[0]) if row else {}
        if email not in members and email not in admins():
            members[email]={'status':'pending','role':'member','requested_at':time.time()}
            db.execute("INSERT OR REPLACE INTO settings VALUES('access_members',?)",(json.dumps(members),))


def set_access(actor,email,status,store):
    if role_for(actor,store)!='admin':
        raise PermissionError('Only the workspace admin can change access.')
    email=normalize_email(email)
    if email in admins():
        raise ValueError('Admin accounts are managed in APP_ADMIN_EMAILS on the server.')
    if status not in ('approved','revoked'):
        raise ValueError('Choose approved or revoked.')
    import json
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT value FROM settings WHERE key='access_members'").fetchone()
        members=json.loads(row[0]) if row else {}
        members[email]={'status':status,'role':'member','updated_at':time.time()}
        db.execute("INSERT OR REPLACE INTO settings VALUES('access_members',?)",(json.dumps(members),))
