"""Cron non privilégié, intégré au conteneur scraper."""
import os
from gaps_scraper.common.runtime import development


def main():
    os.umask(0o077)
    # Docker --init réadopte déjà les processus orphelins.
    options = [] if development() else ['-quiet']
    os.execv('/usr/local/bin/supercronic', ['supercronic', '-no-reap', *options, '/app/config/crontab'])


if __name__=='__main__':main()
