#!/usr/bin/env python3
"""Ask the separately running GPIO owner to de-energise every relay.

Run only AFTER stopping the kiln client: the relay master handles one client
at a time. An OK means the master accepted the request, not proof of contacts.
"""
import socket
import sys
import time

SOCKET_PATH = '/run/kiln-relay/relay.sock'

for attempt in range(1, 4):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(SOCKET_PATH)
            sock.sendall(b'ALL_OFF\n')
            reply = b''
            while not reply.endswith(b'\n') and len(reply) < 128:
                chunk = sock.recv(128)
                if not chunk:
                    raise ConnectionError('Relay master closed connection')
                reply += chunk
            if reply != b'OK\n':
                raise RuntimeError(f'Unexpected reply: {reply!r}')
        print('[SUPERVISOR] Relay master acknowledged ALL_OFF.', flush=True)
        sys.exit(0)
    except (OSError, RuntimeError) as exc:
        print(f'[SUPERVISOR] ALL_OFF attempt {attempt}/3 failed: {exc}',
              file=sys.stderr, flush=True)
        if attempt < 3:
            time.sleep(1.0)

print('[FATAL] Could not confirm an ALL_OFF command with relay master!',
      file=sys.stderr, flush=True)
sys.exit(2)
