#!/usr/bin/env python3
"""Serve a build state directory on loopback on an OS-chosen free port."""

import argparse
import functools
import http.server
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, help='directory to serve')
    parser.add_argument('--port-file', required=True, help='file to receive the chosen port')
    args = parser.parse_args()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=args.directory)
    with http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
        Path(args.port_file).write_text(f'{server.server_address[1]}\n')
        server.serve_forever()


if __name__ == '__main__':
    main()
