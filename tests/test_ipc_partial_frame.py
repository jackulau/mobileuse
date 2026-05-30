"""Regression: the IPC client must turn a truncated/empty response into a
reconnectable ConnectionError, not a raw JSONDecodeError or a silent {}.

request()'s recv loop breaks on EOF. If the peer closes mid-write the buffer
holds partial JSON; json.loads() on it used to raise ValueError (NOT caught by
_send's OSError handler → leaks to the agent, no retry). An empty buffer used to
become {} (so callers indexing ['result'] hit KeyError instead of a recoverable
disconnect). The fix raises ConnectionError (an OSError subclass) whenever the
buffer isn't newline-terminated, routing both into _send's reconnect path.
"""
import socket

import pytest

from android_harness import _ipc as anh_ipc
from iphone_harness import _ipc as iph_ipc


@pytest.mark.parametrize("ipc_mod", [iph_ipc, anh_ipc])
def test_partial_frame_raises_connection_error(ipc_mod):
    a, b = socket.socketpair()
    try:
        # Peer writes a truncated JSON frame (no trailing newline) then half-closes
        # its write side, so the client reads the partial bytes and then hits EOF.
        b.sendall(b'{"result": {"w": 3')
        b.shutdown(socket.SHUT_WR)
        with pytest.raises(ConnectionError):
            ipc_mod.request(a, None, {"method": "x"})
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("ipc_mod", [iph_ipc, anh_ipc])
def test_empty_frame_raises_connection_error(ipc_mod):
    a, b = socket.socketpair()
    try:
        # Peer closes its write side without sending anything → empty buffer.
        b.shutdown(socket.SHUT_WR)
        with pytest.raises(ConnectionError):
            ipc_mod.request(a, None, {"method": "x"})
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("ipc_mod", [iph_ipc, anh_ipc])
def test_connection_error_is_osError_subclass(ipc_mod):
    # _send only retries/reconnects on OSError; ConnectionError must qualify.
    assert issubclass(ConnectionError, OSError)


@pytest.mark.parametrize("ipc_mod", [iph_ipc, anh_ipc])
def test_complete_frame_still_parses(ipc_mod):
    a, b = socket.socketpair()
    try:
        b.sendall(b'{"result": {"w": 42}}\n')
        b.shutdown(socket.SHUT_WR)
        resp = ipc_mod.request(a, None, {"method": "x"})
        assert resp["result"]["w"] == 42
    finally:
        a.close()
        b.close()
