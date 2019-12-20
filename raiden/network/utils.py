import errno
import json
import socket
import sys
from contextlib import closing
from itertools import count, repeat
from socket import SocketKind
from time import sleep

import psutil
import requests
from requests import Response

from raiden.utils.typing import Any, Iterator, Optional, Port, Tuple

LOOPBACK = "127.0.0.1"


def get_response_json(response: Response) -> Any:
    """Decode response.

    Fixes issues: #4174 #4378. simplejson failed to decode some responses,
    whereas stdlib's json module does not fail.
    """
    return json.loads(response.content)


# The solution based on psutils does not work on MacOS because it needs
# root access
if sys.platform == "darwin":  # pragma: no cover

    def _unused_ports(initial_port: Optional[int]) -> Iterator[Port]:
        socket_kind: SocketKind = SocketKind.SOCK_STREAM

        if not initial_port:
            next_port = repeat(0)
        else:
            next_port = count(start=initial_port)

        for port_candidate in next_port:
            # Don't inline the variable until
            # https://github.com/PyCQA/pylint/issues/1437 is fixed
            sock = socket.socket(socket.AF_INET, socket_kind)
            with closing(sock):
                # Force the port into TIME_WAIT mode, ensuring that it will not
                # be considered 'free' by the OS for the next 60 seconds. This
                # does however require that the process using the port sets
                # SO_REUSEADDR on it's sockets. Most 'server' applications do.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((LOOPBACK, port_candidate))
                except OSError as ex:
                    if ex.errno == errno.EADDRINUSE:
                        continue
                    raise

                sock_addr = sock.getsockname()
                port = sock_addr[1]

                # Connect to the socket to force it into TIME_WAIT state (see
                # above)
                sock.listen(1)
                sock2 = socket.socket(socket.AF_INET, socket_kind)
                with closing(sock2):
                    sock2.connect(sock_addr)
                    sock.accept()

            yield Port(port)


else:

    def _unused_ports(initial_port: Optional[int]) -> Iterator[Port]:
        initial_port = initial_port or 27854

        for port in count(initial_port):
            # Because it is not known which interface the socket will bind to,
            # if there is any socket in the target port it must be skiped.
            connect_using_port = (
                conn
                for conn in psutil.net_connections()
                if hasattr(conn, "laddr") and conn.laddr[1] == port
            )

            if not any(connect_using_port):
                yield Port(port)


def get_free_port(initial_port: Optional[int] = None) -> Iterator[Port]:
    """Find an unused TCP port.

    If `initial_port` is passed the function will try to find a port as close as possible.
    Otherwise a random port is chosen by the OS.

    Returns an iterator that will return unused port numbers.
    """
    return _unused_ports(initial_port=initial_port)


def return_after_retries(
    url: str, timeout: float, samples: int = 3, method: str = "head"
) -> Optional[Tuple[str, float]]:
    """Return the `url` after `samples` successful requests.

    Use this to sort the fastest servers, the vallues `samples` and `timeout`
    must be equal for each `url`. The first `url` to return is the fastest
    server.
    """
    durations = 0.0
    for _ in range(samples):
        try:
            response = requests.request(method, url, timeout=timeout)
            response.raise_for_status()
            durations += response.elapsed.total_seconds()
        except (OSError, requests.RequestException):
            return None

        sleep(0.125)  # Slight delay to avoid overloading

    return (url, durations / samples)
