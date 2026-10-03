"""Account sign-in/signup and Google OIDC; authorization is checked separately."""
import streamlit as st
from . import accounts
from .config import config


def google_available():
    try:
        auth=st.secrets.get('auth',{})
        return bool(auth.get('google',{}).get('client_id') or auth.get('client_id'))
    except (FileNotFoundError,KeyError):
        return False


def logout():
    google=bool(st.user.get('is_logged_in',False))
    st.session_state.clear()
    if google:
        st.logout()
    else:
        st.rerun()


def require_identity():
    error=''
    if st.user.get('is_logged_in',False):
        try:
            return accounts.google_identity(dict(st.user))
        except ValueError as exc:
            error=str(exc)
    if st.session_state.get('account_session'):
        try:
            return accounts.verify_session(st.session_state['account_session'])
        except ValueError as exc:
            st.session_state.pop('account_session',None)
            error=str(exc)
    st.markdown('''<style>.stMainBlockContainer{max-width:760px;padding-top:3rem;}
        [data-testid="stForm"]{border-radius:16px;background:#fff;}
        .login-hero{background:#173f42;color:#edf9f5;padding:28px 32px;border-radius:18px;margin-bottom:24px;}
        .login-hero h1{color:white;font-size:34px;letter-spacing:-1px;}
        .login-hero .label{font-size:11px;letter-spacing:2px;color:#a6ddcb;}
        </style><div class="login-hero"><p class="label">PROSPECT STUDIO</p><h1>Your growth workspace</h1>
        <p>Find businesses, manage outreach, and stay on schedule.</p></div>''',unsafe_allow_html=True)
    if error: st.error(error)
    if st.user.get('is_logged_in',False) and st.button('Sign out of Google'):
        logout()
    login,signup=st.tabs(['Sign in','Create account'])
    with login:
        with st.form('account_login'):
            email=st.text_input('Email address',key='signin_email')
            password=st.text_input('Password',type='password',key='signin_password')
            if st.form_submit_button('Sign in',type='primary'):
                try:
                    st.session_state['account_session']=accounts.sign_in(email,password)
                    st.session_state.pop('signin_password',None)
                    st.rerun()
                except ValueError as exc: st.error(str(exc))
        st.caption('Or use your verified Google account.')
        if st.button('Continue with Google',disabled=not google_available(),use_container_width=True):
            st.login('google' if 'google' in st.secrets['auth'] else None)
        if not google_available():
            st.caption('Google sign-in will be available after the admin configures Google OAuth.')
    with signup:
        with st.form('account_signup'):
            name=st.text_input('Your name')
            email=st.text_input('Email address',key='signup_email')
            password=st.text_input('Password (12+ characters)',type='password',key='signup_password')
            confirm=st.text_input('Confirm password',type='password',key='signup_confirm')
            st.caption('Verify your email, then request workspace access. Creating an account does not grant access to leads or sending controls.')
            if st.form_submit_button('Create account',type='primary'):
                try:
                    if password!=confirm: raise ValueError('Passwords do not match.')
                    message=accounts.sign_up(email,password,name)
                    st.session_state.pop('signup_password',None)
                    st.session_state.pop('signup_confirm',None)
                    st.success(message)
                except ValueError as exc: st.error(str(exc))
    st.stop()


def require_access(identity,store):
    role=accounts.role_for(identity,store)
    if role:
        return role
    st.title('Workspace access required')
    st.write(f"Signed in as {identity['email']}.")
    state=store.get('access_members',{}).get(identity['email'],{}).get('status')
    if state=='revoked':
        st.warning('Your workspace access has been revoked. Contact the admin.')
    elif state=='pending':
        st.info('Your access request is waiting for admin approval.')
    elif st.button('Request access'):
        accounts.request_access(identity,store)
        st.rerun()
    if st.button('Sign out'): logout()
    st.stop()
