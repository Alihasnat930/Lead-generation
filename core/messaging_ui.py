import streamlit as st
from . import messaging


def render(store):
    st.title('WhatsApp outreach')
    st.caption('Send approved templates through Twilio or Meta to recipients with recorded WhatsApp opt-in. Scraped phone numbers are not automatically opted in.')
    with st.expander('Record or revoke WhatsApp opt-in'):
        with st.form('whatsapp_consent'):
            phone=st.text_input('Recipient phone (+country code)',key='consent_phone')
            opted=st.checkbox('This recipient opted in to WhatsApp messages')
            evidence=st.text_input('Opt-in source and date')
            if st.form_submit_button('Save recipient consent'):
                try:
                    messaging.set_consent(store,phone,opted,evidence)
                    st.success('Consent saved. Revoked recipients are blocked before sending.')
                except ValueError as exc: st.error(str(exc))
    with st.form('whatsapp_template'):
        provider=st.selectbox('Provider',['twilio','meta'],format_func=lambda v:'Twilio WhatsApp' if v=='twilio' else 'Meta WhatsApp Cloud API')
        phone=st.text_input('Recipient phone (+country code)')
        variables=st.text_area('Template body variables (one per line)',help='Order must match the approved template. Leave empty for a template without variables.')
        if st.form_submit_button('Queue template'):
            try:
                messaging.queue_message(store,provider,phone,variables.splitlines() if variables else [])
                st.success('Queued. Send manually below or enable queued WhatsApp messages on the Schedule page.')
            except ValueError as exc: st.error(str(exc))
    if st.button('Send queued WhatsApp messages',type='primary'):
        try:
            count=messaging.run_queue(store)
            st.success(f'{count} messages accepted by the provider. Acceptance does not confirm final delivery.')
        except Exception as exc: st.error(str(exc) if isinstance(exc,ValueError) else 'Provider operation failed. Check the saved queue state before retrying.')
    records=messaging.messages(store)
    if records:
        st.dataframe([{k:r[k] for k in ('id','provider','phone','state','provider_id','reason')} for r in records],hide_index=True,width='stretch')
        queued=[r for r in records if r['state']=='queued']
        if queued:
            selected=st.selectbox('Queued message to cancel',[r['id'] for r in queued],format_func=lambda key:next(r['phone']+' / '+r['provider'] for r in queued if r['id']==key))
            if st.button('Cancel selected message'):
                messaging.cancel_message(store,selected)
                st.rerun()
