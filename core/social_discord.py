"""Read configured server channels using a bot; never uses user tokens or DMs."""
from dataclasses import dataclass
import re
import requests
from .providers import ProviderError, SearchPage
from .social_research import normalize_record


@dataclass
class DiscordPage(SearchPage):
    cursor: str = ''


def channel_ids(value):
    parts = [p.strip() for p in str(value or '').split(',') if p.strip()]
    if len(parts)>20 or any(not re.fullmatch(r'\d{15,22}',p) for p in parts):
        raise ValueError('Enter up to 20 Discord channel IDs separated by commas.')
    return list(dict.fromkeys(parts))


def _get(path, token, params=None):
    try:
        with requests.get('https://discord.com/api/v10'+path, params=params,
                          headers={'Authorization':'Bot '+token,'User-Agent':'ProspectStudio (community research, 1.0)'},
                          timeout=(8,25),allow_redirects=False,stream=True) as response:
            if response.status_code==429:
                error=ProviderError('rate_limit','Discord rate limit reached. Retry after the cooldown.')
                try:
                    error.retry_after=max(300,float(response.headers.get('Retry-After',300)))
                except (ValueError,TypeError):
                    error.retry_after=300
                raise error
            if response.status_code in (401,403):
                raise ProviderError('access_denied','Discord denied access. Check the bot token and View Channel / Read Message History permissions.')
            if response.status_code!=200:
                raise ProviderError('http_error',f'Discord returned HTTP {response.status_code}.')
            raw=bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw)>2*1024*1024:
                    raise ProviderError('response_size','Discord response exceeded the download limit.')
            import json
            return json.loads(raw)
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('network','Discord returned an unreadable response or could not be reached.') from None


def check_bot(values):
    token=values.get('DISCORD_BOT_TOKEN','')
    if not token:
        raise ValueError('Configure the Discord bot token in Settings → Social sources.')
    user=_get('/users/@me',token)
    if not isinstance(user,dict) or user.get('bot') is not True:
        raise ValueError('Use a Discord bot account for this connection.')
    return True


def collect(query,settings,values):
    channel=query.get('channel','')
    if channel not in channel_ids(values.get('DISCORD_RESEARCH_CHANNELS','')):
        raise ProviderError('configuration','This Discord channel is no longer in the admin-configured channel list.')
    try:
        check_bot(values)
    except ValueError as exc:
        raise ProviderError('configuration',str(exc)) from None
    token=values['DISCORD_BOT_TOKEN']
    meta=_get('/channels/'+channel,token)
    guild=str(meta.get('guild_id','')) if isinstance(meta,dict) else ''
    if not re.fullmatch(r'\d{15,22}',guild):
        raise ProviderError('configuration','Only configured Discord server channels are supported.')
    params={'limit':100}
    if query.get('before'):
        if not re.fullmatch(r'\d{15,22}',str(query['before'])):
            raise ProviderError('cursor','Invalid Discord history cursor.')
        params['before']=query['before']
    messages=_get('/channels/'+channel+'/messages',token,params)
    if not isinstance(messages,list) or any(not isinstance(m,dict) for m in messages):
        raise ProviderError('invalid_response','Discord returned unexpected message data.')
    if messages and not any(m.get('content') for m in messages):
        raise ProviderError('message_content','Discord returned no text. Check Message Content intent; media-only batches also need review.')
    records=[]
    for message in messages:
        if message.get('author',{}).get('bot'):
            continue
        record=normalize_record({'url':f'https://discord.com/channels/{guild}/{channel}/{message.get("id", "")}',
                                 'text':message.get('content'),'timestamp':message.get('timestamp')},settings,'Discord bot · configured server channel')
        if record and record['relevant']:
            records.append(record)
    cursor=str(messages[-1].get('id','')) if messages else ''
    if cursor and not re.fullmatch(r'\d{15,22}',cursor):
        raise ProviderError('cursor','Discord returned an invalid history cursor.')
    return DiscordPage(records,len(messages)==100,cursor)
