"""Read-only Gmail checks using IMAP; never marks mail read or changes labels."""
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import imaplib
import re
import ssl
import time
from .config import config
from .outreach_store import TZ
from .websites import domain_from_url, EMAIL_RE

PUBLIC_EMAIL_DOMAINS = {'gmail.com','googlemail.com','outlook.com','hotmail.com','live.com','yahoo.com',
                        'aol.com','icloud.com','me.com','proton.me','protonmail.com','mail.com','gmx.com','gmx.de','yahoo.co.uk'}


def addresses(message, fields):
    return {address.lower().strip() for _, address in getaddresses([str(message.get(field,'')) for field in fields]) if address}


def quote(value):
    return '"' + value.replace('\\','\\\\').replace('"','\\"') + '"'


def same_business(address, email, domain):
    address = address.lower()
    if address == email.lower():
        return True
    host = address.rsplit('@',1)[-1]
    root = domain_from_url(host)
    email_root = domain_from_url(email.rsplit('@',1)[-1])
    return root not in PUBLIC_EMAIL_DOMAINS and root in {domain, email_root}


class Mailbox:
    def __enter__(self):
        if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
            raise ValueError('Configure GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env.')
        self.client = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context(), timeout=25)
        try:
            self.client.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD.replace(' ',''))
            status, lines = self.client.list()
            if status != 'OK':
                raise ValueError('Could not read Gmail folders.')
            self.folders = {}
            for line in lines:
                match = re.match(rb'^\(([^)]*)\) "[^"]*" (.+)$',line or b'')
                if match:
                    for special in ('All','Sent','Junk','Trash'):
                        if ('\\'+special).encode() in match[1].split():
                            self.folders[special] = match[2].decode('ascii')
            if not all(key in self.folders for key in ('All','Sent','Junk','Trash')):
                raise ValueError('Gmail must expose All Mail, Sent, Spam and Trash to IMAP before outreach can run.')
            return self
        except Exception:
            self.client.logout()
            raise

    def __exit__(self, *_):
        try:
            self.client.logout()
        except (OSError, imaplib.IMAP4.error):
            pass

    def _select(self, folder):
        if self.client.select(self.folders[folder], readonly=True)[0] != 'OK':
            raise ValueError('Gmail folder could not be read; sending stopped.')

    def search(self, query, limit=500):
        """Search All Mail plus Spam/Trash and deduplicate Gmail's stable message IDs."""
        result = {}
        for folder in ('All','Junk','Trash'):
            self._select(folder)
            status, data = self.client.uid('SEARCH', None, 'X-GM-RAW', quote(query))
            if status != 'OK':
                raise ValueError('Gmail history search failed; sending stopped.')
            uids = (data[0] or b'').split()
            if len(uids) + len(result) > limit:
                raise ValueError('Gmail history exceeds this bounded check. Review this business manually before sending.')
            for start in range(0,len(uids),50):
                batch = uids[start:start+50]
                status, fetched = self.client.uid('FETCH', b','.join(batch),
                    '(UID X-GM-MSGID X-GM-THRID X-GM-LABELS INTERNALDATE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC SUBJECT IN-REPLY-TO REFERENCES CONTENT-TYPE)])')
                if status != 'OK':
                    raise ValueError('Could not read Gmail message headers; sending stopped.')
                seen = set()
                for item in fetched:
                    if not isinstance(item,tuple):
                        continue
                    meta, raw = item
                    uid, gmail_id = re.search(rb'\bUID (\d+)',meta), re.search(rb'X-GM-MSGID (\d+)',meta)
                    internal = re.search(rb'INTERNALDATE "([^"]+)"',meta)
                    if not uid or not gmail_id or not internal or len(raw)>65536:
                        raise ValueError('Incomplete Gmail metadata; sending stopped.')
                    seen.add(uid[1])
                    message = BytesParser(policy=policy.default).parsebytes(raw)
                    thread = re.search(rb'X-GM-THRID (\d+)',meta)
                    labels = re.search(rb'X-GM-LABELS \((.*?)\)',meta)
                    record = {'id':gmail_id[1].decode(), 'message_id':str(message.get('Message-ID','')).strip(),
                              'sender':next(iter(addresses(message,['From'])),'').lower(),
                              'recipients':addresses(message,['To','Cc']), 'subject':str(message.get('Subject','')),
                              'references':str(message.get('In-Reply-To',''))+' '+str(message.get('References','')),
                              'thread':thread[1].decode() if thread else '',
                              'sent_at':datetime.strptime(internal[1].decode(),'%d-%b-%Y %H:%M:%S %z').timestamp(),
                              'content_type':message.get_content_type(), 'uid':uid[1], 'folder':folder,
                              'is_sent':bool(labels and b'\\Sent' in labels[1]),
                              'is_draft':bool(labels and b'\\Drafts' in labels[1])}
                    if not record['is_draft']:
                        result[record['id']] = record
                if seen != set(batch):
                    raise ValueError('Gmail changed during the history check; retry reconciliation before sending.')
        return list(result.values())

    def text(self, message):
        self._select(message['folder'])
        status, data = self.client.uid('FETCH', message['uid'], '(BODY.PEEK[]<0.65536>)')
        if status != 'OK':
            raise ValueError('Could not check a possible reply or delivery failure.')
        raw = next((part[1] for part in data if isinstance(part,tuple)),None)
        if raw is None:
            raise ValueError('A message disappeared during reply checking. Retry reconciliation.')
        # Bounded text is used only for reply/DSN classification; it is not retained in the database.
        return raw.decode('utf-8','replace').lower()

    def sent_today(self, now=None):
        current = datetime.fromtimestamp(time.time() if now is None else now, TZ)
        start = current.replace(hour=0,minute=0,second=0,microsecond=0)
        end = start + timedelta(days=1)
        records = self.search(f'in:sent after:{int(start.timestamp())-1} before:{int(end.timestamp())}',limit=1000)
        return [r for r in records if r['is_sent'] and start.timestamp() <= r['sent_at'] < end.timestamp()
                and any(a != config.GMAIL_ADDRESS.lower() for a in r['recipients'])]

    def history(self, email, domain):
        if not EMAIL_RE.fullmatch(email) or not re.fullmatch(r'[a-z0-9.-]+',domain):
            raise ValueError('Invalid business email or domain.')
        terms = [f'from:{email}',f'to:{email}',quote(email)]
        for host in {domain,domain_from_url(email.rsplit('@',1)[-1])} - PUBLIC_EMAIL_DOMAINS:
            terms.extend([f'from:({host})',f'to:({host})'])
        messages = self.search('{'+ ' '.join(terms) +'}')
        sent = [m for m in messages if m['is_sent'] and any(same_business(a,email,domain) for a in m['recipients'])]
        sent_ids = {m['message_id'] for m in sent if m['message_id']}
        threads = {m['thread'] for m in sent if m['thread']}
        state, reason = '', ''
        for message in messages:
            if message['is_sent'] or message['sender'] == config.GMAIL_ADDRESS.lower():
                continue
            bounce = ('mailer-daemon' in message['sender'] or 'postmaster@' in message['sender']
                      or message['content_type'] == 'multipart/report')
            if bounce:
                body = self.text(message)
                if email.lower() in body or any(mid.lower() in body for mid in sent_ids):
                    state, reason = 'BOUNCED', 'Gmail contains a delivery-failure report for this recipient.'
                    break
            elif (same_business(message['sender'],email,domain) or message['thread'] in threads
                  or any(mid in message['references'] for mid in sent_ids)):
                body = self.text(message)
                optout = any(term in body for term in ('unsubscribe','remove me','stop emailing','do not contact','not interested','opt out'))
                state = 'DO_NOT_CONTACT' if optout else 'REPLIED'
                reason = 'An opt-out or negative reply was found.' if optout else 'An incoming conversation was found; automatic follow-ups stopped.'
        return {'sent':sorted(sent,key=lambda m:m['sent_at']), 'state':state, 'reason':reason}

    def find_message(self, message_id):
        return self.search('in:sent rfc822msgid:' + message_id.strip('<>'),limit=20)
