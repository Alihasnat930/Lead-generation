"""One dedicated schedule page shared with the worker's persisted preferences."""
from datetime import time as daytime, datetime
import time
from zoneinfo import available_timezones
import streamlit as st
from .outreach_scheduler import next_run, launch
from .config import config

DAY_NAMES=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']


def render(store):
    prefs=store.settings()
    st.title('Schedule')
    st.caption('Choose when this workspace syncs leads and processes outreach. Saving a schedule does not send a message immediately.')
    upcoming=next_run(prefs,last_day=store.get('scheduled_day',''))
    a,b,c=st.columns(3)
    a.metric('Next eligible run',upcoming.strftime('%a %d %b, %H:%M') if upcoming else 'Paused')
    b.metric('Schedule timezone',prefs['schedule_timezone'])
    c.metric('Service', 'Running' if time.time()-store.get('scheduler_heartbeat',0)<120 else 'Not detected')
    if upcoming: st.caption('A run becomes eligible at this time. Sleeping hosts and active jobs can delay execution.')
    with st.form('workspace_schedule'):
        st.subheader('Sending days and time')
        zones=sorted(available_timezones())
        timezone_name=st.selectbox('Timezone',zones,index=zones.index(prefs['schedule_timezone']))
        weekdays=st.multiselect('Days',range(7),default=prefs['schedule_weekdays'],format_func=lambda d:DAY_NAMES[d])
        when=st.time_input('Sending time',value=daytime(prefs['hour'],prefs['minute']))
        st.subheader('Email outreach')
        auto_send=st.checkbox('Enable scheduled emails',value=prefs['auto_send_enabled'])
        x,y=st.columns(2)
        initials=x.checkbox('Initial outreach',value=prefs['initial_enabled'])
        followups=y.checkbox('Due follow-ups',value=prefs['followup_enabled'])
        a,b,c=st.columns(3)
        initial=a.number_input('Daily initial limit',0,20,prefs['initial_limit'])
        follow=b.number_input('Daily follow-up limit',0,10,prefs['followup_limit'])
        total=c.number_input('Daily combined email limit',0,30,prefs['total_limit'])
        score=st.slider('Minimum outreach score',0,100,prefs['min_score'])
        signature=st.text_input('Signature name',value=prefs['signature_name'])
        st.subheader('WhatsApp queue')
        whatsapp=st.checkbox('Enable scheduled queued WhatsApp messages',value=prefs['auto_whatsapp_enabled'])
        whatsapp_limit=st.number_input('Daily WhatsApp limit',0,30,prefs['whatsapp_daily_limit'])
        st.caption('Only manually queued, opted-in template recipients can be sent. Email and WhatsApp daily allowances reset at midnight Asia/Karachi.')
        st.subheader('Google Sheets sync')
        auto_sync=st.checkbox('Automatically sync qualified leads',value=prefs['auto_sync_enabled'])
        interval=st.number_input('Sync interval (minutes)',1,60,prefs['sync_interval_minutes'])
        if st.form_submit_button('Save schedule',type='primary'):
            try:
                store.save_settings({'schedule_timezone':timezone_name,'schedule_weekdays':weekdays,
                    'hour':when.hour,'minute':when.minute,'auto_send_enabled':auto_send,
                    'initial_enabled':initials,'followup_enabled':followups,'initial_limit':initial,
                    'followup_limit':follow,'total_limit':total,'min_score':score,'signature_name':signature,
                    'auto_whatsapp_enabled':whatsapp,'whatsapp_daily_limit':whatsapp_limit,
                    'auto_sync_enabled':auto_sync,'sync_interval_minutes':interval})
                store.event('Workspace schedule updated.')
                st.success('Schedule saved. Refresh to update the next-run preview.')
            except ValueError as exc: st.error(str(exc))
    a,b=st.columns(2)
    if a.button('Pause all automatic sending'):
        store.save_settings({'auto_send_enabled':False,'auto_whatsapp_enabled':False})
        st.rerun()
    if b.button('Start sync and scheduling service'):
        launch()
        st.success('Service start requested. The process lock prevents duplicate workers.')
    st.caption('Runs while this computer is awake.' if not config.CLOUD_MODE else 'Runs while the hosted app is awake; configured GitHub Actions can handle sleeping periods.')
    if store.get('schedule_last_error'):
        st.warning('Last scheduled cycle stopped: '+store.get('schedule_last_error')+'. Inspect activity and connection checks before restarting.')
    events=store.events(30)
    if events: st.dataframe(events,hide_index=True,width='stretch')
