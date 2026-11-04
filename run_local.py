"""Start the website on this computer and open its real local address."""
import argparse
import os
import socket
import threading
import webbrowser
from pathlib import Path


def choose_port():
    for port in range(8088,8109):
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port));return port
            except OSError:continue
    raise RuntimeError('Ports 8088–8108 are in use. Close an old server and retry.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    os.chdir(Path(__file__).resolve().parent)
    os.environ.setdefault('DATA_DIR',str(Path.cwd()/'.data'))
    os.environ['HOST']='127.0.0.1';os.environ['PORT']=str(choose_port())
    url='http://127.0.0.1:'+os.environ['PORT']+'/'
    print('\nOpen your website: '+url+'\nKeep this window open. Stop with Ctrl+C.\n',flush=True)
    if not args.no_browser:
        opener=threading.Timer(1.5,lambda:webbrowser.open(url));opener.daemon=True;opener.start()
    from app.server import main as serve
    try:serve()
    except KeyboardInterrupt:print('\nWebsite stopped. Your data remains saved.')


if __name__=='__main__':main()
