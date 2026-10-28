"""Redact registered API credentials before Python logging handlers receive records."""
import http.client
import logging
import re
import threading
import traceback
from urllib.parse import quote

_lock=threading.Lock()
_keys=set()
_installed=False


def redact(value):
    text=str(value)
    with _lock:keys=tuple(_keys)
    for key in keys:
        text=text.replace(key,'[REDACTED]').replace(quote(key,safe=''),'[REDACTED]')
    return re.sub(r'(?i)(api_?key\s*[=:]\s*)[^\s&\"\'<>]+',r'\1[REDACTED]',text)


def protect(api_key):
    global _installed
    with _lock:
        if api_key:_keys.add(api_key)
        if _installed:return
        previous=logging.getLogRecordFactory()
        def factory(*args,**kwargs):
            record=previous(*args,**kwargs)
            record.msg=redact(record.getMessage());record.args=()
            if record.exc_info:
                record.exc_text=redact(''.join(traceback.format_exception(*record.exc_info)))
                record.exc_info=None
            return record
        logging.setLogRecordFactory(factory)
        # http.client debug output bypasses logging; keep raw wire debugging disabled.
        http.client.HTTPConnection.debuglevel=0
        _installed=True
