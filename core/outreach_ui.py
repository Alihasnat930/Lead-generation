"""Explicit dashboard controls for Sheet sync, email and scheduling."""
from datetime import time as daytime
import time
import pandas as pd
import streamlit as st
from .config import config
from . import pipeline, sheets, email_sender
from .mailbox import Mailbox
from .outreach_store import OutreachStore
from .outreach_drafts import draft, signature_links


def error(exc):
    st.error(str(exc) if isinstance(exc,(ValueError,PermissionError)) else
             f'{type(exc).__name__}: connection or CRM operation failed. Saved delivery records remain available.')


def summary(store):
    report=store.get('last_run')
    if report:
        if report.get('error'): st.error(report['error'])
        st.write(f"Last {report['mode']} run: {report['sent']} sent, {report['failed']} failed, {report['uncertain']} awaiting reconciliation.")
        if report.get('reasons'):
            st.dataframe([{'Reason':k,'Count':v} for k,v in report['reasons'].items()],hide_index=True,width='stretch')


def render(discovery_store):
    store=OutreachStore()
    prefs=store.settings()
    st.title('Outreach & CRM')
    st.caption('This app uses its own Google Sheet. Local Python drafts and signatures require no paid AI API.')
    st.markdown(' | '.join(f'[{label}]({url})' for label,url in signature_links() if url.startswith('https://')))
    sync_tab,email_tab,followup_tab,schedule_tab=st.tabs(['Sync & review','Send outreach','Follow-ups','Activity'])
    with sync_tab:
        a,b=st.columns(2)
        a.metric('Qualified leads in workspace',discovery_store.stats()['qualified'])
        b.metric('Business domains verified in Sheet',store.get('last_sync',{}).get('verified_domains','Not checked'))
        sheet_name=st.text_input('Worksheet for sync and outreach',value=prefs['sheet_name'])
        if st.button('Save worksheet selection'):
            try:
                store.save_settings({'sheet_name':sheet_name})
                st.success('Worksheet saved for both syncing and sending.')
            except Exception as exc: error(exc)
        st.link_button('Open this app’s separate Sheet',f'https://docs.google.com/spreadsheets/d/{config.GOOGLE_SHEET_ID}/edit')
        if st.button('Sync qualified leads to Google Sheets',type='primary'):
            try:
                with st.spinner('Writing and verifying lead rows...'):
                    result=pipeline.sync_local_leads(store)
                st.success(f"Verified {result['verified_domains']:,} business domains. Added {result['inserted']:,} leads and filled {result['updated']:,} missing fields.")
                st.link_button('Open verified worksheet',result['spreadsheet_url'])
            except Exception as exc: error(exc)
        if st.button('Load CRM snapshot'):
            try:
                records,_=sheets.outreach_snapshot(store.settings()['sheet_name'])
                st.dataframe(pd.DataFrame(records).astype(str),hide_index=True,width='stretch')
            except Exception as exc: error(exc)
        if st.button('Check Gmail authentication and Sheet access'):
            try:
                with st.spinner('Checking connections without sending mail...'):
                    email_sender.check_authentication()
                    with Mailbox():
                        records,_=sheets.outreach_snapshot(store.settings()['sheet_name'])
                st.success(f'Gmail SMTP and IMAP authentication passed. Read {len(records)} CRM rows. No email sent.')
            except Exception as exc: error(exc)
    with email_tab:
        st.write('Send to qualified, uncontacted businesses. Every send checks Gmail history, current CRM status, suppression and daily allowance.')
        a,b,c=st.columns(3)
        limit=a.number_input('Initial-email limit for this run',0,20,prefs['initial_limit'])
        minimum=b.slider('Minimum outreach score',0,100,prefs['min_score'])
        name=c.text_input('Signature name',value=prefs['signature_name'])
        with st.expander('Preview a draft with your signature'):
            candidates=discovery_store.leads(status='QUALIFIED')
            if candidates:
                selected=st.selectbox('Business to preview',range(len(candidates)),format_func=lambda i:candidates[i].get('company_name',candidates[i]['domain']))
                preview=draft(candidates[selected],name)
                st.write('Subject: '+preview['subject'])
                st.text(preview['body'])
                st.caption('Sent emails also have an HTML alternative with clickable profile labels.')
            else: st.info('Run discovery to create a qualified lead for preview.')
        if st.button('Check recipients and daily allowance'):
            try:
                rows,_=sheets.outreach_snapshot(prefs['sheet_name'])
                count=sum(1 for r in rows if r.get('status')=='QUALIFIED' and r.get('email') and not r.get('last_contacted') and pipeline._number(r.get('lead_score'))>=minimum)
                with Mailbox() as mailbox: usage=store.usage(mailbox.sent_today())
                st.write(f'{count} CRM candidates before individual Gmail-history and suppression checks.')
                st.json(usage)
                st.caption('Other external messages from the same Gmail account conservatively count against daily allowances.')
            except Exception as exc: error(exc)
        if st.button('Send outreach emails',type='primary'):
            status=st.empty()
            try:
                with st.spinner('Syncing leads and checking recipients...'):
                    pipeline.sync_local_leads(store)
                    sent=pipeline.run_outreach(limit,minimum,name,log=status.text)
                if sent: st.success(f'Sent {len(sent)} outreach emails.')
                else: st.warning('No outreach emails sent. See the recorded reasons below.')
            except Exception as exc: error(exc)
        summary(store)
    with followup_tab:
        st.write('Two follow-ups: after at least 4 days, then at least 10 days from the initial email and 6 days from follow-up 1. Replies, bounces and opt-outs stop the sequence.')
        cap=st.number_input('Follow-up limit for this run',0,10,prefs['followup_limit'])
        if st.button('Reconcile Gmail replies and saved sends'):
            try:
                with st.spinner('Checking Gmail...'): result=pipeline.reconcile(store)
                st.success(f"Recovered {result['recovered']} sends; stopped {result['stopped']} conversations. No email sent.")
            except Exception as exc: error(exc)
        if st.button('Send due follow-ups',type='primary'):
            status=st.empty()
            try:
                with st.spinner('Checking due conversations...'):
                    sent=pipeline.run_followups(4,10,None,prefs['signature_name'],log=status.text,daily_limit=cap)
                if sent: st.success(f'Sent {len(sent)} follow-ups.')
                else: st.warning('No follow-ups sent. Check last-run reasons and due dates.')
            except Exception as exc: error(exc)
        summary(store)
    with schedule_tab:
        st.info('Configure timezone, weekdays, limits and automatic sending on the Schedule page in the sidebar.')
        heartbeat=store.get('scheduler_heartbeat',0)
        st.caption('Sync and scheduling service: '+('Running' if time.time()-heartbeat<120 else 'Not detected. Start it below.'))
        if st.button('Start sync and scheduling service'):
            from .outreach_scheduler import launch
            launch()
            st.success('Service launch requested. Its process lock prevents duplicate services.')
        events=store.events(30)
        if events: st.dataframe(pd.DataFrame(events)[['created','message']],hide_index=True,width='stretch')
        records=store.deliveries()
        if records:
            st.subheader('Saved delivery history')
            st.dataframe(pd.DataFrame(records)[['domain','email','stage','state','reason','sheet_synced']],hide_index=True,width='stretch')
