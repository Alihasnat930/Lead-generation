import streamlit as st
from . import accounts, integrations
from .config import config


def render(store,identity):
    st.subheader('Accounts and integrations')
    if accounts.role_for(identity,store)!='admin':
        st.info('API credentials and user approvals are managed by the workspace admin.')
        return
    access,google,twilio,whatsapp,backend=st.tabs(['User access','Google sign-in','Twilio','WhatsApp Cloud API','Supabase'])
    values=integrations.load(store)
    with access:
        st.write('Admin: '+', '.join(sorted(accounts.admins())))
        st.caption('Approved users receive full workspace access, including outreach and scheduling. API keys and user approvals stay admin-only.')
        members=store.get('access_members',{})
        if members: st.dataframe([{'email':email,**info} for email,info in members.items()],hide_index=True,width='stretch')
        with st.form('approve_account'):
            email=st.text_input('User email address')
            status=st.selectbox('Access',['approved','revoked'])
            if st.form_submit_button('Update user access'):
                try:
                    accounts.set_access(identity,email,status,store)
                    st.success('Access updated. The next workspace action rechecks approval.')
                except (ValueError,PermissionError) as exc: st.error(str(exc))
    groups=[(google,'google',[
        ('GOOGLE_CLIENT_ID','Google OAuth client ID',False),('GOOGLE_CLIENT_SECRET','Google OAuth client secret',True),
        ('GOOGLE_REDIRECT_URI','Redirect URI',False)]),
        (twilio,'twilio',[
        ('TWILIO_ACCOUNT_SID','Twilio account SID',False),('TWILIO_AUTH_TOKEN','Twilio auth token',True),
        ('TWILIO_WHATSAPP_FROM','WhatsApp sender (whatsapp:+15551234567)',False),('TWILIO_CONTENT_SID','Approved Content SID (HX...)',False)]),
        (whatsapp,'meta',[
        ('WHATSAPP_PHONE_NUMBER_ID','Meta phone number ID',False),('WHATSAPP_ACCESS_TOKEN','WhatsApp access token',True),
        ('WHATSAPP_API_VERSION','Graph API version',False),('WHATSAPP_TEMPLATE_NAME','Approved template name',False),
        ('WHATSAPP_TEMPLATE_LANGUAGE','Template language code',False)])]
    for tab,provider,fields in groups:
        with tab:
            if provider=='google':
                st.caption('Create a Google OAuth web client and register this exact redirect URI. Google verifies identity; workspace approval still applies.')
                st.link_button('Open Google Auth Platform','https://console.cloud.google.com/auth/clients')
            else:
                st.caption('Uses your provider account and approved WhatsApp templates. Provider charges may apply. Saving or checking credentials sends no messages.')
            with st.form('integration_'+provider):
                entries={}
                for key,label,secret in fields:
                    if secret:
                        entries[key]=st.text_input(label,type='password',key='setting_'+key,
                            help='Configured. Leave blank to keep it.' if values[key] else 'Not configured.')
                    else:
                        entries[key]=st.text_input(label,value=values[key],key='setting_'+key)
                if st.form_submit_button('Save '+('Google sign-in' if provider=='google' else provider.title()+' settings')):
                    try:
                        integrations.save(identity,entries,store)
                        if provider=='google': integrations.sync_google_secrets(store)
                        for key,_,secret in fields:
                            if secret: st.session_state.pop('setting_'+key,None)
                        st.success('Saved. Reload the page to use the updated configuration.')
                    except (ValueError,PermissionError) as exc: st.error(str(exc))
                    except Exception: st.error('Settings could not be saved. Check server storage.')
            if provider!='google' and st.button('Check '+provider.title()+' authentication',key='check_'+provider):
                try:
                    from .messaging import check_authentication
                    check_authentication(provider,integrations.load(store))
                    st.success('Provider authenticated. No message sent.')
                except Exception as exc: st.error(str(exc) if isinstance(exc,ValueError) else 'Provider authentication failed.')
    with backend:
        st.write('Supabase URL: '+('Configured' if config.SUPABASE_URL else 'Missing'))
        st.write('User authentication key: '+('Configured' if config.SUPABASE_ANON_KEY else 'Missing'))
        st.write('Server storage key: '+('Configured' if config.SUPABASE_SERVICE_ROLE_KEY else 'Missing'))
        if config.CLOUD_MODE:
            st.caption('Manage backend credentials in Streamlit Cloud Secrets. Reboot after changing them.')
        else:
            with st.form('supabase_environment'):
                url=st.text_input('Supabase project URL',value=config.SUPABASE_URL)
                public=st.text_input('Publishable / anon key',type='password',key='sb_public')
                server=st.text_input('Server secret / service-role key',type='password',key='sb_server')
                app_url=st.text_input('App URL for verification emails',value=config.APP_PUBLIC_URL)
                if st.form_submit_button('Save Supabase to .env'):
                    try:
                        integrations.save_supabase_environment(identity,{'SUPABASE_URL':url,'SUPABASE_ANON_KEY':public,
                            'SUPABASE_SERVICE_ROLE_KEY':server,'APP_PUBLIC_URL':app_url},store)
                        st.session_state.pop('sb_public',None)
                        st.session_state.pop('sb_server',None)
                        st.success('Private .env updated. Restart the app and scheduler to load it.')
                    except (ValueError,PermissionError) as exc: st.error(str(exc))
