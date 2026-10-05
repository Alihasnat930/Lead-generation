"""Research setup, saved source evidence, review and exports."""
import json
import time
import pandas as pd
import streamlit as st
from . import integrations
from .social_discord import channel_ids
from .social_research import PLATFORMS, SERVICES, DEFAULT_TOPICS, import_records, export_records
from .social_sources import plan_queries
from .social_store import SocialStore
from .social_worker import launch


def render(business_store, outreach_store):
    store = SocialStore(business_store.path)
    st.title('Social research')
    st.write('Find posts about assignments, theses, quizzes and other academic support, then review potential opportunities.')
    st.caption('A post mentioning the UK or US does not verify its author’s location, age or buying intent. Search excerpts may be incomplete or outdated.')
    with st.expander('Create a research run',expanded=not store.jobs()):
        with st.form('social_create'):
            name=st.text_input('Research name',value='US / UK academic support')
            platforms=st.multiselect('Platforms',list(PLATFORMS),default=['Reddit','Facebook'])
            markets=st.multiselect('Markets',['UK','US'],default=['UK','US'])
            services=st.multiselect('Topics',list(SERVICES),default=list(DEFAULT_TOPICS))
            keywords=st.text_area('Extra phrases (one per line, up to eight)',placeholder='statistics assignment\nbiology quiz\ndissertation feedback')
            a,b,c=st.columns(3)
            target=a.number_input('Research-record target',1,5000,1000)
            requests=b.number_input('Search request allowance',1,200,200)
            pages=c.number_input('Pages per search / channel',1,5,5)
            st.caption('Reddit and Facebook: indexed public post links via DDGS/Bing. Discord: only admin-configured server channels. Targets are limits, not guaranteed yields; imported exports are also supported.')
            if st.form_submit_button('Create research',type='primary'):
                try:
                    values={'name':name,'platforms':platforms,'markets':markets,'services':services,
                            'keywords':[line.strip() for line in keywords.splitlines() if line.strip()],
                            'target':target,'max_requests':requests,'max_pages':pages}
                    from .social_research import validate_settings
                    values=validate_settings(values)
                    channels=channel_ids(integrations.load(outreach_store)['DISCORD_RESEARCH_CHANNELS'])
                    identifier=store.create(values,plan_queries(values,channels))
                    st.session_state['social_selected']=identifier
                    st.success('Research saved. Start discovery or import an export below.')
                except ValueError as exc: st.error(str(exc))
                except Exception: st.error('Research could not be saved. Check the workspace storage connection.')
    jobs=store.jobs()
    if not jobs:
        st.info('Create a research run to begin. You can use public-post discovery without adding API credentials.')
        return
    names={job['id']:job['name'] for job in jobs}
    if st.session_state.get('social_selected') not in names:
        st.session_state['social_selected']=jobs[0]['id']
    job_id=st.selectbox('Saved research',list(names),format_func=names.get,key='social_selected')
    progress(store,job_id,outreach_store)


@st.fragment(run_every=5)
def progress(store,job_id,outreach_store):
    job=store.job(job_id)
    running=job['status']=='running' and job['heartbeat']>time.time()-90
    records=store.records(job_id)
    a,b,c=st.columns(3)
    a.metric('Saved posts',len(records))
    b.metric('Possible requests',sum(r['intent']=='Possible request' for r in records))
    c.metric('Reviewed as relevant',sum(r['review_status']=='Relevant' for r in records))
    st.progress(min(len(records)/job['settings']['target'],1),text=f"{len(records):,} / {job['settings']['target']:,} research records")
    st.caption(job['status'].replace('_',' ').title()+f" · {job['requests']} / {job['settings']['max_requests']} search batches used")
    if job['reason']: st.info(job['reason'])
    if 'Discord' in job['settings']['platforms']:
        values=integrations.load(outreach_store)
        if not values['DISCORD_BOT_TOKEN'] or not values['DISCORD_RESEARCH_CHANNELS']:
            st.info('Discord collection needs a bot and channel IDs in Settings → Social sources. Imports can be used now. Create a new research run after adding channels.')
    if running:
        if st.button('Pause social research',key='social_pause'):
            store.pause(job_id)
            st.rerun()
    elif len(records)<job['settings']['target'] and job['status']!='exhausted':
        if st.button('Start / resume social research',type='primary',key='social_start'):
            try:
                launch(job_id,store)
                st.rerun()
            except Exception as exc:
                st.error(str(exc) if isinstance(exc,ValueError) else 'Worker could not start. Check workspace storage.')
    with st.expander('Adjust run limits'):
        with st.form('social_limits_'+job_id):
            target=st.number_input('Total record target',1,5000,job['settings']['target'])
            requests=st.number_input('Total request allowance',1,200,job['settings']['max_requests'])
            if st.form_submit_button('Save research limits',disabled=running):
                try:
                    store.revise_limits(job_id,target,requests)
                    st.rerun()
                except ValueError as exc: st.error(str(exc))
    with st.expander('Import an existing post export'):
        st.caption('Import exports you are allowed to use. CSV columns: url, title, text, posted_at. JSON accepts the same fields or DiscordChatExporter server-channel exports. Author profiles and member lists are not imported.')
        template='url,title,text,posted_at\n'
        st.download_button('Download import template',template,'social-import-template.csv','text/csv',key='social_template')
        uploaded=st.file_uploader('Post export (CSV or JSON, up to 5 MB)',type=['csv','json'],key='social_import')
        if st.button('Import posts',disabled=uploaded is None or running,key='social_import_button'):
            try:
                rows,skipped=import_records(uploaded.getvalue(),uploaded.name,job['settings'])
                added=store.save_records(job_id,rows)
                st.success(f'{added} new records saved; {skipped} rows were invalid or outside the chosen topics. Existing URLs and the record limit can reduce the added count.')
                records=store.records(job_id)
            except ValueError as exc: st.error(str(exc))
            except Exception: st.error('Import failed. Check the file format and workspace storage.')
    if not records:
        st.info('No matching posts saved yet. Source blocks and shortfalls appear above; results are never generated to fill a target.')
        return
    a,b,c=st.columns(3)
    intent=a.selectbox('Post intent',['All','Possible request','Discussion','Service advertisement'])
    market=b.selectbox('Location mentioned',['All','UK','US','Unknown','Mixed'])
    status=c.selectbox('Review status',['All','Needs review','Relevant','Dismissed'])
    filtered=[r for r in records if (intent=='All' or r['intent']==intent) and (market=='All' or r['market']==market) and (status=='All' or r['review_status']==status)]
    st.caption(f'{len(filtered):,} matching posts. Scores are explained topic/request signals; these records do not enter the email or WhatsApp queue.')
    st.download_button('Export social research CSV',export_records(filtered),'social-research-'+job_id+'.csv','text/csv',key='social_export')
    st.dataframe(pd.DataFrame([{k:r.get(k,'') for k in ('platform','title','excerpt','intent','market','score','review_status','url','source_type','posted_at')} for r in filtered]),
                 hide_index=True,width='stretch',column_config={'url':st.column_config.LinkColumn('Source post')})
    if filtered:
        selected=st.selectbox('Review a source post',[r['url'] for r in filtered],format_func=lambda url:next((r['title'] or r['excerpt'][:80] or url) for r in filtered if r['url']==url))
        record=next(r for r in filtered if r['url']==selected)
        st.link_button('Open original post',selected)
        st.text(record['excerpt'])
        st.caption('Location evidence: '+json.dumps(record['market_evidence'],ensure_ascii=False)+' · Curriculum/style mentions: '+', '.join(record['curriculum_mentions']))
        review=st.selectbox('Set review', ['Needs review','Relevant','Dismissed'],index=['Needs review','Relevant','Dismissed'].index(record['review_status']))
        if st.button('Save post review'):
            store.review(selected,review)
            st.rerun()
