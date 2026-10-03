from copy import deepcopy
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
import smtplib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import pipeline, sheets, email_sender, outreach_scheduler
from core.config import config
from core.outreach_store import OutreachStore, TZ
from core.outreach_drafts import draft
from core.mailbox import Mailbox

NOW=datetime(2026,10,3,15,0,tzinfo=TZ).timestamp()


def lead(domain='business.co.uk'):
    return {'domain':domain,'email':'hello@'+domain,'company_name':'Business','status':'QUALIFIED',
            'qualification_status':'QUALIFIED','lead_score':'90','last_contacted':'','next_followup':'',
            'followup_count':'0','industry':'Dental','recommended_service':'Patient intake','website':'https://'+domain}


class FakeMailbox:
    def __init__(self):
        self.today=[]
        self.histories={}
        self.found=[]
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def sent_today(self,*_): return self.today
    def history(self,email,domain): return self.histories.get(domain,{'sent':[],'state':'','reason':''})
    def find_message(self,message_id): return self.found


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=OutreachStore(Path(self.temp.name)/'outreach.db')
        self.mailbox=FakeMailbox()
        self.rows=[lead()]
        self.suppression=[]
        self.patches=[patch.object(config,'GMAIL_ADDRESS','sender@gmail.com'),
                      patch.object(config,'GOOGLE_SHEET_ID','separate-app-sheet'),
                      patch('core.pipeline.sheets.outreach_snapshot',side_effect=lambda *_:(deepcopy(self.rows),deepcopy(self.suppression))),
                      patch('core.pipeline.sheets.update_lead_fields',side_effect=self.update),
                      patch('core.pipeline.email_sender.deliver',return_value={'state':'sent','reason':''})]
        self.mocks=[p.start() for p in self.patches]
        self.send=self.mocks[-1]
    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()
    def update(self,domain,fields,*args,**kwargs):
        row=next(r for r in self.rows if r['domain']==domain)
        row.update(fields)
        return True
    def run_cycle(self,mode='initial',now=NOW,**kwargs):
        return pipeline.run_cycle(mode,store=self.store,mailbox_factory=lambda:self.mailbox,
                                  clock=lambda:now,sleeper=lambda _:None,log=lambda _:None,**kwargs)
    def successful_initial(self,when=NOW-11*86400):
        item=self.store.reserve(self.rows[0],0,draft(self.rows[0],'Ali'),now=when)
        self.store.update(item['id'],'sent',sent_at=when)
        self.store.mark_synced(item['id'])
        self.rows[0].update(status='CONTACTED',next_followup='2026-01-01',last_contacted='2026-01-01')
        self.mailbox.histories[self.rows[0]['domain']]={'state':'','reason':'','sent':[{'message_id':item['message_id'],'sent_at':when}]}
        return item

    def test_send_updates_crm_and_contains_all_links_without_ai(self):
        with patch('core.ai._call') as ai:
            self.assertEqual(self.run_cycle(),['Business'])
            ai.assert_not_called()
        record=self.store.deliveries()[0]
        self.assertEqual(record['state'],'sent')
        self.assertEqual(record['sheet_synced'],1)
        self.assertEqual(self.rows[0]['next_followup'],'2026-10-07')
        self.assertEqual(self.rows[0]['status'],'CONTACTED')
        message=self.send.call_args.args[0]
        text=message.get_body(preferencelist=('html',)).get_content()
        for label in ('LinkedIn','GitHub','Upwork'): self.assertIn('>'+label+'</a>',text)

    def test_prior_sent_by_another_tool_blocks_new_outreach(self):
        self.mailbox.histories['business.co.uk']={'state':'','reason':'','sent':[{'message_id':'<external>','sent_at':NOW-100}]}
        self.assertEqual(self.run_cycle(),[])
        self.send.assert_not_called()
        self.assertEqual(self.store.stopped('business.co.uk','hello@business.co.uk')['status'],'EXTERNAL_CONTACTED')

    def test_external_daily_usage_blocks_both_types(self):
        self.mailbox.today=[{'id':str(i),'message_id':f'<external{i}>'} for i in range(30)]
        self.assertEqual(self.run_cycle(),[])
        self.send.assert_not_called()
        self.assertTrue(any('Daily cap' in k for k in self.store.get('last_run')['reasons']))

    def test_uncertain_send_is_not_retried_or_followed_up(self):
        self.send.return_value={'state':'uncertain','reason':'Connection lost after submission.'}
        self.run_cycle()
        self.assertEqual(self.store.deliveries()[0]['state'],'uncertain')
        self.run_cycle(now=NOW+86400)
        self.assertEqual(self.send.call_count,1)
        self.assertIsNone(pipeline.due_stage(self.store.deliveries(),NOW+20*86400))

    def test_crm_failure_after_send_recovers_without_resending(self):
        with patch('core.pipeline.sheets.update_lead_fields',side_effect=OSError('Fixture failure')):
            with self.assertRaises(OSError): self.run_cycle()
        self.assertEqual(self.store.deliveries()[0]['state'],'sent')
        self.assertEqual(self.store.deliveries()[0]['sheet_synced'],0)
        self.run_cycle()
        self.assertEqual(self.send.call_count,1)
        self.assertEqual(self.store.deliveries()[0]['sheet_synced'],1)

    def test_recover_ambiguous_delivery_from_sent_mail(self):
        item=self.store.reserve(self.rows[0],0,draft(self.rows[0],'Ali'),now=NOW)
        self.store.update(item['id'],'uncertain')
        self.mailbox.found=[{'is_sent':True,'recipients':{item['email']},'sent_at':NOW,'message_id':item['message_id']}]
        self.run_cycle()
        self.assertEqual(self.store.deliveries()[0]['state'],'sent')
        self.send.assert_not_called()

    def test_followup_waits_four_days(self):
        self.successful_initial(NOW-4*86400+1)
        self.assertEqual(self.run_cycle('followup'),[])
        self.send.assert_not_called()

    def test_late_first_followup_delays_second_six_days_and_threads(self):
        first=self.successful_initial(NOW-11*86400)
        self.run_cycle('followup')
        message=self.send.call_args.args[0]
        self.assertEqual(message['In-Reply-To'],first['message_id'])
        self.assertEqual(self.rows[0]['next_followup'],'2026-10-09')
        self.assertIsNone(pipeline.due_stage(self.store.deliveries(),NOW+6*86400-1))
        self.assertEqual(pipeline.due_stage(self.store.deliveries(),NOW+6*86400),2)

    def test_reply_or_optout_stops_due_sequence(self):
        self.successful_initial()
        self.mailbox.histories['business.co.uk'].update(state='DO_NOT_CONTACT',reason='Opt-out received.')
        self.run_cycle('followup')
        self.send.assert_not_called()
        self.assertEqual(self.rows[0]['status'],'DO_NOT_CONTACT')
        self.assertEqual(self.rows[0]['next_followup'],'')

    def test_manual_run_does_not_enable_automatic_sending(self):
        self.run_cycle()
        self.assertFalse(self.store.settings()['auto_send_enabled'])

    def test_suppression_blocks_initial(self):
        self.suppression=[{'email':self.rows[0]['email']}]
        self.assertEqual(self.run_cycle(),[])
        self.send.assert_not_called()

    def test_same_email_for_two_businesses_only_one_send(self):
        other=lead('another.co.uk'); other['email']=self.rows[0]['email']; self.rows.append(other)
        self.run_cycle()
        self.assertEqual(self.send.call_count,1)

    def test_cap_is_checked_again_after_each_send(self):
        self.rows=[lead(f'business{i}.co.uk') for i in range(3)]
        self.run_cycle(daily_limit=1)
        self.assertEqual(self.send.call_count,1)

    def test_old_enabled_setting_does_not_silently_enable_scheduler(self):
        self.store.set('preferences',{'enabled':True,'auto_sync_enabled':False})
        with patch('core.pipeline.run_cycle') as run:
            outreach_scheduler.tick(self.store,NOW)
            run.assert_not_called()


class SheetRepairTests(unittest.TestCase):
    def test_extra_column_blank_rows_and_targeted_update(self):
        ws=Mock(row_count=100)
        headers=['company_name','domain','extra','email','status','next_followup']
        ws.row_values.return_value=headers+['']*200
        ws.get.return_value=[headers,[],['Business','business.co.uk','keep','hello@business.co.uk','QUALIFIED','']]
        with patch('core.sheets.get_spreadsheet') as book:
            book.return_value.worksheet.return_value=ws
            sheets.update_lead_fields('business.co.uk',{'status':'CONTACTED','next_followup':'2026-10-07'})
        ws.batch_update.assert_called_once_with([
            {'range':'E3','values':[['CONTACTED']]},{'range':'F3','values':[['2026-10-07']]}],value_input_option='RAW')
        ws.get.assert_called_once_with('A1:F100')

    def test_explicit_write_verification_and_crm_preservation(self):
        headers=['email','status','domain','company_name']
        before=[headers,['old@clinic.co.uk','CONTACTED','clinic.co.uk','Clinic']]
        after=before+[['hello@new.co.uk','QUALIFIED','new.co.uk','Business']]
        ws=Mock(row_count=100,title='Leads',id=1)
        ws.spreadsheet.url='https://docs.google.com/spreadsheets/d/fixture/edit'
        ws.row_values.return_value=headers+['']*100
        ws.get.side_effect=[before,after]
        with tempfile.TemporaryDirectory() as folder, patch('core.sheets.DATA_DIR',Path(folder)), patch('core.sheets.get_or_create_worksheet',return_value=ws):
            result=sheets.append_new_qualified([dict(lead('clinic.co.uk'),email='old@clinic.co.uk'),lead('new.co.uk')])
        self.assertEqual(result['inserted'],1)
        ws.update.assert_called_once_with(range_name='A3',values=[after[-1]],value_input_option='RAW')
        ws.append_rows.assert_not_called()
        ws.batch_update.assert_not_called()

    def test_unconfirmed_sheet_write_is_not_reported_as_success(self):
        headers=['domain','email','status']
        ws=Mock(row_count=100); ws.row_values.return_value=headers; ws.get.return_value=[headers]
        with tempfile.TemporaryDirectory() as folder, patch('core.sheets.DATA_DIR',Path(folder)), patch('core.sheets.get_or_create_worksheet',return_value=ws), patch('core.sheets.time.sleep'):
            with self.assertRaises(ValueError): sheets.append_new_qualified([lead()])


class TransportTests(unittest.TestCase):
    def test_smtp_timeout_after_submission_is_uncertain(self):
        server=Mock(); server.send_message.side_effect=smtplib.SMTPServerDisconnected('Fixture disconnect')
        with patch('core.email_sender.smtplib.SMTP_SSL',return_value=server):
            result=email_sender.deliver(email_sender.build_message('hello@business.co.uk','Test','Body'))
        self.assertEqual(result['state'],'uncertain')

    def test_authentication_check_never_sends(self):
        with patch('core.email_sender.smtplib.SMTP_SSL') as client:
            email_sender.check_authentication()
            server=client.return_value.__enter__.return_value
            server.login.assert_called_once()
            server.send_message.assert_not_called()

    def test_header_injection_rejected(self):
        with self.assertRaises(ValueError):
            email_sender.build_message('hello@business.co.uk','Subject\nBcc: other@example.com','Body')

    def test_imap_reads_sent_headers_without_marking_read(self):
        mailbox=Mailbox(); mailbox.folders={k:k for k in ('All','Junk','Trash')}; mailbox.client=Mock()
        mailbox.client.select.return_value=('OK',[b'1'])
        header=b'Message-ID: <one@test>\r\nFrom: sender@gmail.com\r\nTo: hello@business.co.uk\r\nSubject: Hello\r\n\r\n'
        meta=b'1 (UID 42 X-GM-MSGID 123 X-GM-THRID 456 X-GM-LABELS (\\Sent) INTERNALDATE "03-Oct-2026 10:00:00 +0000" BODY[HEADER.FIELDS] {100}'
        mailbox.client.uid.side_effect=[('OK',[b'42']),('OK',[(meta,header),b')']),('OK',[b'']),('OK',[b''])]
        records=mailbox.search('in:sent')
        self.assertTrue(records[0]['is_sent'])
        self.assertEqual(records[0]['message_id'],'<one@test>')
        self.assertIn('BODY.PEEK',mailbox.client.uid.call_args_list[1].args[-1])
        self.assertTrue(all(c.kwargs['readonly'] for c in mailbox.client.select.call_args_list))


if __name__=='__main__': unittest.main()
