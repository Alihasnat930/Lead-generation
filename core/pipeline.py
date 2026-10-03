"""Outreach orchestration shared by the dashboard and scheduled jobs."""
from collections import Counter
from datetime import datetime
import json
import time
from . import sheets, email_sender
from .config import config
from .local_lock import LocalLock
from .mailbox import Mailbox
from .outreach_drafts import draft
from .outreach_store import OutreachStore, TZ
from .websites import domain_from_url, valid_email

STOP_STATUSES = {'REPLIED','INTERESTED','CALL_BOOKED','PROPOSAL_SENT','WON','LOST','DO_NOT_CONTACT','BOUNCED','EXTERNAL_CONTACTED'}


def run_discovery(*args, **kwargs):
    raise ValueError('Use the Campaigns screen for discovery.')


def _number(value):
    try: return int(float(value or 0))
    except (ValueError,TypeError): return 0


def normalize_lead(lead):
    return dict(lead,domain=domain_from_url(lead.get('domain') or lead.get('website','')),
                email=str(lead.get('email','')).strip().lower(),status=str(lead.get('status','')).strip().upper())


def sync_local_leads(store=None):
    from .store import Store
    store=store or OutreachStore()
    result=sheets.append_new_qualified(Store().leads(status='QUALIFIED'),store.settings()['sheet_name'])
    store.set('last_sync',dict(result,at=time.time()))
    store.event(f"Sheet sync verified: {result['inserted']} added, {result['updated']} missing fields filled, {result['verified_domains']} business domains in {result['worksheet']}.")
    return result


def due_stage(previous, now):
    # An interrupted SMTP operation blocks every later stage until Sent mail proves delivery.
    if any(r['state'] in ('prepared','sending','uncertain') for r in previous):
        return None
    successful={r['stage']:r for r in previous if r['state']=='sent'}
    initial=successful.get(0)
    if not initial or 2 in successful:
        return None
    if 1 not in successful:
        return 1 if now>=initial['sent_at']+4*86400 else None
    return 2 if now>=max(initial['sent_at']+10*86400,successful[1]['sent_at']+6*86400) else None


def _record_delivery(store, delivery, sheet_name):
    previous=store.deliveries(delivery['domain'])
    sent=[r for r in previous if r['state']=='sent']
    initial=next((r for r in sent if r['stage']==0),None)
    if not initial:
        return
    latest=max(sent,key=lambda r:r['stage'])
    if latest['stage']==0:
        due=initial['sent_at']+4*86400
    elif latest['stage']==1:
        due=max(initial['sent_at']+10*86400,latest['sent_at']+6*86400)
    else:
        due=None
    current,_=sheets.outreach_snapshot(sheet_name)
    matches=[normalize_lead(l) for l in current if domain_from_url(l.get('domain',''))==delivery['domain']]
    if len(matches)!=1:
        raise ValueError('Could not identify the sent business uniquely in the CRM.')
    current_status=matches[0]['status']
    stopped=store.stopped(delivery['domain'],delivery['email'])
    fields={'last_contacted':datetime.fromtimestamp(latest['sent_at'],TZ).date().isoformat(),
            'followup_count':latest['stage'],'updated_at':sheets.now_iso()}
    if current_status not in STOP_STATUSES and not stopped:
        fields.update(status='CONTACTED',next_followup=datetime.fromtimestamp(due,TZ).date().isoformat() if due else '')
    else:
        fields['next_followup']=''
    sheets.update_lead_fields(delivery['domain'],fields,sheet_name,email=delivery['email'])
    for row in sent:
        store.mark_synced(row['id'])


def _recover(store, mailbox, sheet_name):
    recovered=0
    for row in store.deliveries():
        if row['sheet_id']!=config.GOOGLE_SHEET_ID:
            continue
        if row['state'] in ('prepared','sending','uncertain'):
            found=[m for m in mailbox.find_message(row['message_id']) if m['is_sent'] and row['email'] in m['recipients']]
            if found:
                stamp=min(m['sent_at'] for m in found)
                store.update(row['id'],'sent','Recovered from Gmail Sent mail.',sent_at=stamp)
                row.update(state='sent',sent_at=stamp)
                recovered+=1
            else:
                store.update(row['id'],'uncertain','No confirmed Sent copy yet. Automatic resend and follow-ups are blocked.')
        if row['state']=='sent' and not row['sheet_synced']:
            _record_delivery(store,row,sheet_name)
    return recovered


def _apply_history(store,lead,history,sheet_name):
    if history['state']:
        store.stop(lead['domain'],lead['email'],history['state'],history['reason'])
        sheets.update_lead_fields(lead['domain'],{'status':history['state'],'next_followup':'','updated_at':sheets.now_iso()},sheet_name,email=lead['email'])
        return history['state']
    return ''


def reconcile(store=None, mailbox_factory=Mailbox):
    store=store or OutreachStore()
    prefs=store.settings()
    with LocalLock(store.path+'.lock'), mailbox_factory() as mailbox:
        recovered=_recover(store,mailbox,prefs['sheet_name'])
        leads,suppression=sheets.outreach_snapshot(prefs['sheet_name'])
        stopped=0
        owned={r['domain'] for r in store.deliveries() if r['sheet_id']==config.GOOGLE_SHEET_ID}
        for row in leads:
            lead=normalize_lead(row)
            if lead['domain'] not in owned or not valid_email(lead['email']):
                continue
            if sheets.suppressed(lead['domain'],lead['email'],suppression) or lead['status'] in STOP_STATUSES:
                store.stop(lead['domain'],lead['email'],lead['status'] if lead['status'] in STOP_STATUSES else 'DO_NOT_CONTACT','CRM or suppression list stops this sequence.')
                continue
            if _apply_history(store,lead,mailbox.history(lead['email'],lead['domain']),prefs['sheet_name']):
                stopped+=1
        usage=store.usage(mailbox.sent_today())
        result={'recovered':recovered,'stopped':stopped,'usage':usage,'at':time.time()}
        store.set('last_reconciliation',result)
        store.event(f'Reconciled Gmail: {recovered} sends recovered, {stopped} conversations stopped.')
        return result


def run_cycle(mode='initial', *, daily_limit=None, min_score=None, your_name=None, log=print,
              store=None, mailbox_factory=Mailbox, clock=time.time, sleeper=time.sleep, scheduled=False):
    if mode not in ('initial','followup'):
        raise ValueError('Invalid outreach mode.')
    store=store or OutreachStore()
    prefs=store.settings()
    sheet_name=prefs['sheet_name']
    if not config.CLOUD_MODE and store.get('cloud_migrated',False):
        raise ValueError('This workspace was migrated to the cloud. Send from the hosted app to keep one delivery ledger.')
    cap_key='initial_limit' if mode=='initial' else 'followup_limit'
    cap=min(prefs[cap_key],daily_limit if daily_limit is not None else prefs[cap_key])
    if cap<0:
        raise ValueError('Daily limit cannot be negative.')
    minimum=prefs['min_score'] if min_score is None else int(min_score)
    name=your_name or prefs['signature_name']
    report={'mode':mode,'sent':0,'failed':0,'uncertain':0,'eligible':0,'reasons':{},'at':clock()}
    reasons=Counter()
    sent=[]
    with LocalLock(store.path+'.lock'):
        try:
            if scheduled and not store.settings()['auto_send_enabled']:
                reasons['Scheduled sending is disabled.']+=1
                return sent
            if scheduled and (prefs['sender']!=config.GMAIL_ADDRESS.lower() or prefs['sheet_id']!=config.GOOGLE_SHEET_ID):
                raise ValueError('Sender or Sheet changed. Review and save scheduling settings again.')
            if cap==0:
                reasons['This daily cap is set to zero.']+=1
                return sent
            with mailbox_factory() as mailbox:
                _recover(store,mailbox,sheet_name)
                rows,suppression=sheets.outreach_snapshot(sheet_name)
                rows=[normalize_lead(row) for row in rows]
                domains=Counter(l['domain'] for l in rows)
                rows.sort(key=lambda l:_number(l.get('lead_score')),reverse=True)
                considered=set()
                for old in rows:
                    domain,email=old['domain'],old['email']
                    if not domain or not valid_email(email):
                        reasons['Missing or unsuitable business email/domain.']+=1; continue
                    if domains[domain]!=1 or email in considered:
                        reasons['Duplicate business or email in CRM.']+=1; continue
                    considered.add(email)
                    if mode=='initial' and (old['status']!='QUALIFIED' or old.get('last_contacted') or _number(old.get('lead_score'))<minimum):
                        reasons['Not qualified, below score, or previously contacted.']+=1; continue
                    if mode=='followup' and old['status']!='CONTACTED':
                        continue
                    if store.stopped(domain,email) or sheets.suppressed(domain,email,suppression):
                        reasons['Suppressed or stopped conversation.']+=1; continue
                    previous=store.deliveries(domain)
                    if any(r['sheet_id']!=config.GOOGLE_SHEET_ID for r in previous):
                        reasons['This business belongs to another saved Sheet sequence.']+=1; continue
                    now=clock()
                    stage=0 if mode=='initial' else due_stage(previous,now)
                    if stage is None or (stage==0 and any(r['state']!='failed' for r in previous)):
                        reasons['Already sent, awaiting reconciliation, or not yet due.']+=1; continue
                    if mode=='followup':
                        requested=str(old.get('next_followup',''))[:10]
                        if not requested or requested>datetime.fromtimestamp(now,TZ).date().isoformat():
                            reasons['CRM follow-up date is later or cleared.']+=1; continue
                    report['eligible']+=1
                    usage=store.usage(mailbox.sent_today(now),now)
                    report['usage']=usage
                    if usage[mode]>=cap or usage['total']>=prefs['total_limit']:
                        reasons[f'Daily cap reached ({usage[mode]}/{cap}; total {usage["total"]}/{prefs["total_limit"]}).']+=1; break
                    # Refresh the specific row and suppression immediately before a send.
                    latest,suppression=sheets.outreach_snapshot(sheet_name)
                    matches=[normalize_lead(l) for l in latest if domain_from_url(l.get('domain',''))==domain]
                    if len(matches)!=1 or matches[0]['email']!=email or matches[0]['status']!=old['status']:
                        reasons['CRM recipient or status changed during this run.']+=1; continue
                    lead=matches[0]
                    if stage == 0 and (lead.get('last_contacted') or _number(lead.get('lead_score')) < minimum):
                        reasons['CRM eligibility changed during this run.']+=1; continue
                    if stage and (not lead.get('next_followup') or str(lead['next_followup'])[:10] > datetime.fromtimestamp(clock(),TZ).date().isoformat()):
                        reasons['CRM follow-up date changed during this run.']+=1; continue
                    if sheets.suppressed(domain,email,suppression):
                        reasons['New suppression entry.']+=1; continue
                    history=mailbox.history(email,domain)
                    if _apply_history(store,lead,history,sheet_name):
                        reasons['Reply, opt-out or bounce found in Gmail.']+=1; continue
                    if stage==0 and history['sent']:
                        store.stop(domain,email,'EXTERNAL_CONTACTED','Gmail already contains sent mail to this business.')
                        sheets.update_lead_fields(domain,{'status':'CONTACTED','next_followup':'',
                            'last_contacted':datetime.fromtimestamp(history['sent'][-1]['sent_at'],TZ).date().isoformat()},sheet_name,email=email)
                        reasons['Already contacted in Gmail, including another sending tool.']+=1; continue
                    successful=[r for r in previous if r['state']=='sent']
                    if stage:
                        known={r['message_id'] for r in successful}
                        if any(m['message_id'] not in known for m in history['sent']):
                            store.stop(domain,email,'EXTERNAL_CONTACTED','A separate Gmail send changed this conversation. Review it manually.')
                            reasons['Conversation changed outside this app.']+=1; continue
                        if not any(m['message_id'] in known for m in history['sent']):
                            reasons['Original sent message is not visible in Gmail yet.']+=1; continue
                    initial=next((r for r in successful if r['stage']==0),None)
                    last=max(successful,key=lambda r:r['stage']) if successful else None
                    content=draft(lead,name,stage,initial['subject'] if initial else '')
                    if scheduled and not store.settings()['auto_send_enabled']:
                        reasons['Scheduled sending was disabled.']+=1; break
                    # Recheck persisted local stops after potentially slow network calls.
                    if store.stopped(domain,email):
                        reasons['Conversation was stopped.']+=1; continue
                    usage=store.usage(mailbox.sent_today(clock()),clock())
                    report['usage']=usage
                    if usage[mode]>=cap or usage['total']>=prefs['total_limit']:
                        reasons['Daily cap reached before submission.']+=1; break
                    delivery=store.reserve(lead,stage,content,last['message_id'] if last else '',now=clock())
                    if not delivery:
                        reasons['Delivery already reserved or duplicate email.']+=1; continue
                    message=email_sender.build_message(email,delivery['subject'],delivery['body'],html_body=delivery['html'],
                        message_id=delivery['message_id'],reply_to=delivery['reply_to'],your_name=name)
                    store.update(delivery['id'],'sending')
                    from .cloud_runtime import checkpoint_outreach
                    checkpoint_outreach(store.path)
                    result=email_sender.deliver(message)
                    if result['state']=='sent':
                        stamp=clock()
                        store.update(delivery['id'],'sent',sent_at=stamp)
                        checkpoint_outreach(store.path)
                        delivery.update(state='sent',sent_at=stamp)
                        sent.append(lead.get('company_name',domain)); report['sent']+=1
                        log(f'Sent to {lead.get("company_name",domain)}.')
                        _record_delivery(store,delivery,sheet_name)
                    else:
                        store.record_failure(delivery['id'],result,now=clock())
                        checkpoint_outreach(store.path)
                        report[result['state']]+=1
                        reasons[result['reason']]+=1
                        # Authentication, disconnection or rejection should not trigger a burst of attempts.
                        break
                    sleeper(30)
        except Exception as exc:
            report['error']=str(exc) if isinstance(exc,ValueError) else f'{type(exc).__name__}: connection or CRM operation failed.'
            raise
        finally:
            report['reasons']=dict(reasons)
            store.set('last_run',report)
            store.event(f"{mode.title()} run: {report['sent']} sent, {report['failed']} failed, {report['uncertain']} awaiting reconciliation. " + report.get('error',''))
            log(f"Run finished: {report['sent']} sent. " + '; '.join(reasons))
    return sent


def run_outreach(daily_limit,min_score,your_name,log=print):
    # Manual sends never enable the daily scheduler implicitly.
    return run_cycle('initial',daily_limit=int(daily_limit),min_score=int(min_score),your_name=your_name,log=log)


def run_followups(day1=4,day2=10,day3=None,your_name=None,log=print,daily_limit=None):
    # Legacy callers may still pass 3/7/14; actual minimum timing is centrally enforced as 4/10/6.
    return run_cycle('followup',daily_limit=daily_limit,your_name=your_name,log=log)
