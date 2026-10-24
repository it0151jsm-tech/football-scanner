"""Run all tests while denying external Python socket connections."""
import os
import socket
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
os.chdir(ROOT)
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex
blocked=[]


def permitted(address):
    return isinstance(address,str) or (isinstance(address,tuple) and address[0] in ('127.0.0.1','::1','localhost'))


def connect(sock,address):
    if not permitted(address):
        blocked.append(str(address));raise AssertionError('offline CI denied external socket')
    return original_connect(sock,address)


def connect_ex(sock,address):
    if not permitted(address):
        blocked.append(str(address));raise AssertionError('offline CI denied external socket')
    return original_connect_ex(sock,address)


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='value-bet-offline-') as isolated, \
            patch.dict(os.environ,VALUE_BET_DB=str(Path(isolated)/'app.db'),
                VALUE_BET_API_LEDGER=str(Path(isolated)/'budget.db'),VALUE_BET_OBSERVATION_DB=''), \
            patch.object(socket.socket,'connect',connect),patch.object(socket.socket,'connect_ex',connect_ex):
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT/'tests')))
    if blocked:
        print('Unexpected external network attempts:',len(blocked),file=sys.stderr)
    sys.exit(0 if result.wasSuccessful() and not blocked else 1)
