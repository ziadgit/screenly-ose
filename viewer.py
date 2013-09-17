#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012-2013, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"

from datetime import datetime, timedelta
from glob import glob
from os import path, getenv, remove, makedirs
from os import stat as os_stat, utime, system, kill
from platform import machine
from random import shuffle
from requests import get as req_get, head as req_head
from time import sleep, time
from sys import argv
import json
import logging
import sh
import signal
from ctypes import cdll
import socket
from settings import settings
import html_templates

from utils import url_fails

import db
import assets_helper

WATCHDOG_PATH = '/tmp/screenly.watchdog'
SCREENLY_HTML = '/tmp/screenly_html/'
UZBL = '/tmp/uzbl_socket_'

last_settings_refresh = None
load_screen_pid = None
load_screen_visible = True
is_pro_init = None
current_browser_url = None



def send_to_front(name):
    """Instruct X11 to bring a window with the given name in its title to front."""

    r = [l for l in sh.xwininfo('-root', '-tree', '-int').split('\n') if name in l]
    if not len(r) == 1:
        logging.info('Unable to send window with %s in title to front - %s matches found.', name, len(r))
        return False

    win_id = int(r[0].strip().split(' ')[0])
    dsp = libx11.XOpenDisplay(None)
    logging.info('Raising %s window %s to front', name, win_id)
    libx11.XRaiseWindow(dsp, win_id)
    libx11.XCloseDisplay(dsp)


def sigusr1(signum, frame):
    """
    This is the signal handler for SIGUSR1
    The signal interrupts sleep() calls, so
    the currently running asset will be skipped.
    Since video assets don't have a duration field,
    the video player has to be killed.
    """
    logging.info("Signal received, skipping.")
    system("killall omxplayer.bin")


def sigusr2(signum, frame):
    """
    This is the signal handler for SIGUSR2
    Resets the last_settings_refresh timestamp to force
    settings reloading.
    """
    global last_settings_refresh
    logging.info("Signal received, reloading settings.")
    last_settings_refresh = None
    reload_settings()


class Scheduler(object):
    def __init__(self, *args, **kwargs):
        logging.debug('Scheduler init')
        self.update_playlist()

    def get_next_asset(self):
        logging.debug('get_next_asset')
        self.refresh_playlist()
        logging.debug('get_next_asset after refresh')
        if self.nassets == 0:
            return None
        idx = self.index
        self.index = (self.index + 1) % self.nassets
        logging.debug('get_next_asset counter %s returning asset %s of %s', self.counter, idx + 1, self.nassets)
        if settings['shuffle_playlist'] and self.index == 0:
            self.counter += 1
        return self.assets[idx]

    def refresh_playlist(self):
        logging.debug('refresh_playlist')
        time_cur = datetime.utcnow()
        logging.debug('refresh: counter: (%s) deadline (%s) timecur (%s)', self.counter, self.deadline, time_cur)
        if self.dbisnewer():
            self.update_playlist()
        elif settings['shuffle_playlist'] and self.counter >= 5:
            self.update_playlist()
        elif self.deadline and self.deadline <= time_cur:
            self.update_playlist()

    def update_playlist(self):
        logging.debug('update_playlist')
        (self.assets, self.deadline) = generate_asset_list()
        self.nassets = len(self.assets)
        self.gentime = time()
        self.counter = 0
        self.index = 0
        logging.debug('update_playlist done, count %s, counter %s, index %s, deadline %s', self.nassets, self.counter, self.index, self.deadline)

    def dbisnewer(self):
        # get database file last modification time
        try:
            db_mtime = path.getmtime(settings['database'])
        except:
            db_mtime = 0
        return db_mtime >= self.gentime


def generate_asset_list():
    logging.info('Generating asset-list...')
    playlist = assets_helper.get_playlist(db_conn)
    deadline = sorted([asset['end_date'] for asset in playlist])[0] if len(playlist) > 0 else None
    logging.debug('generate_asset_list deadline: %s', deadline)

    if settings['shuffle_playlist']:
        shuffle(playlist)

    return (playlist, deadline)


def watchdog():
    """Notify the watchdog file to be used with the watchdog-device."""
    if not path.isfile(WATCHDOG_PATH):
        open(WATCHDOG_PATH, 'w').close()
    else:
        utime(WATCHDOG_PATH, None)


def asset_is_accessible(uri):
    """Determine if content is accessible or not."""

    asset_folder = settings['assetdir']

    # If it's local content, just check if the file exist on disk.
    if ((asset_folder in uri) or (SCREENLY_HTML in uri) and path.exists(uri)):
        return True
    else:
        return not url_fails(uri)


def load_browser():
    logging.info('Loading browser...')

    global browser_pid

    geom = [l for l in sh.xwininfo('-root').split('\n') if 'geometry' in l][0].split('y ')[1]
    browser = sh.Command('uzbl-browser')(g=geom, uri=current_browser_url, _bg=True)
    browser_pid = browser.pid
    logging.info('Browser loading %s. Running as PID %s.', current_browser_url, browser.pid)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_path = UZBL + str(browser_pid)
    sock_ready = False

    while not sock_ready:
        try:
            sock.connect(sock_path)
            sock_ready = True
        except socket.error as (errno, msg):
            if errno == 2:
                logging.info('waiting for uzbl socket %s', sock_path)
                sleep(0.5)
            else:
                raise Exception('socket error')
    sock.close()

    if not settings['verify_ssl']:
       browser_socket('set ssl_verify = 0')
    browser_socket('set show_status = 0')

    if settings['show_splash']:
        sleep(60)

    return browser

def browser_socket(command, cb=lambda _: True):
    """Like browser_fifo but also read back any immediate response from UZBL.

    Note that the response can be anything, including events entirely unrelated
    to the command executed.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(UZBL + str(browser_pid))
    sock.send(command + '\n')
    logging.info('>> %s', command)
    buf = ''
    while not cb(buf):
        buf += sock.recv(1024)
    sock.close()
    logging.debug('<< %s', buf)
    return buf


def browser_page_has(name):
    """Return true if the given name is defined on the currently loaded browser page."""
    run = browser_socket("js typeof({0}) !== 'undefined'".format(name), cb=lambda buf: 'COMMAND_EXECUTED' in buf)
    return run


def browser_reload(force=False):
    """Reload the browser. Use to Force=True to force-reload"""
    browser_socket('reload_ign_cache' if force else 'reload')


def browser_clear(force=False):
    """Clear the browser if necessary.

    Call this function right before displaying now browser content (with feh or omx).

    When a web assset is loaded into the browser, it's not cleared after the duration but instead
    remains displayed until the next asset is ready to show. This minimises the amount of transition
    time - in the case where the next asset is also web content the browser is never cleared,
    and in other cases it's cleared as late as possible.

    """
    browser_url(black_page, force=force, cb=lambda buf: 'LOAD_FINISH' in buf)


def browser_url(url, cb=lambda _: True, force=False):
    global current_browser_url

    if url == current_browser_url and not force:
        logging.debug('Already showing %s, keeping it.', current_browser_url)
    else:
        current_browser_url = url
        browser_socket('set uri = {0}'.format(current_browser_url), cb=cb)
        logging.info('current url is %s', current_browser_url)


def view_image(uri, duration):
    logging.debug('Displaying image %s for %s seconds.', (uri, duration))

    browser_clear()
    browser_socket('js document.body.style.backgroundImage = "url({0})"'.format(uri))
    sleep(int(duration))


def view_video(uri):
    logging.debug('Displaying video %s', uri)

    if arch == 'armv6l':
        run = sh.omxplayer(uri, o=settings['audio_output'], _bg=True)
    elif arch in ['x86_64', 'x86_32']:
        run = sh.mplayer(uri, '-nosound', _bg=True)

    browser_clear(force=True)
    run.wait()

    # Clean up after omxplayer
    omxplayer_logfile = HOME + '/omxplayer.log'
    if path.isfile(omxplayer_logfile):
        remove(omxplayer_logfile)


def view_web(url, duration):
    logging.debug('Displaying url %s for %s seconds.', url, duration)
    browser_url(url)
    sleep(int(duration))


def start_load_screen():
    """Toggle the load screen. Set status to either True or False."""
    load_screen = HOME + '/screenly/loading.jpg'
    logging.info('showing load screen %s', load_screen)
    feh = sh.feh(load_screen, scale_down=True, borderless=True, fullscreen=True, _bg=True)
    return feh


def check_update():
    """
    Check if there is a later version of Screenly-OSE
    available. Only do this update once per day.

    Return True if up to date was written to disk,
    False if no update needed and None if unable to check.
    """

    sha_file = path.join(settings.get_configdir(), 'latest_screenly_sha')

    if path.isfile(sha_file):
        sha_file_mtime = path.getmtime(sha_file)
        last_update = datetime.fromtimestamp(sha_file_mtime)
    else:
        last_update = None

    logging.debug('Last update: %s' % str(last_update))

    if last_update is None or last_update < (datetime.now() - timedelta(days=1)):

        if asset_is_accessible('http://stats.screenlyapp.com'):
            latest_sha = req_get('http://stats.screenlyapp.com/latest')

            if latest_sha.status_code == 200:
                with open(sha_file, 'w') as f:
                    f.write(latest_sha.content.strip())
                return True
            else:
                logging.debug('Received on 200-status')
                return
        else:
            logging.debug('Unable to retreive latest SHA')
            return
    else:
        return False


def reload_settings():
    """
    Reload settings if the timestamp of the
    settings file is newer than the settings
    file loaded in memory.
    """

    settings_file = settings.get_configfile()
    settings_file_mtime = path.getmtime(settings_file)
    settings_file_timestamp = datetime.fromtimestamp(settings_file_mtime)

    if not last_settings_refresh or settings_file_timestamp > last_settings_refresh:
        settings.load()

    logging.getLogger().setLevel(logging.DEBUG if settings['debug_logging'] else logging.INFO)

    global last_setting_refresh
    last_setting_refresh = datetime.utcnow()


def cond_pro_init():
    """Function to handle first-run on Screenly Pro"""
    global is_pro_init
    is_pro_init = path.isfile(path.join(settings.get_configdir(), 'not_initialized'))
    intro_file = path.join(settings.get_configdir(), 'intro.html')
    if is_pro_init:
        logging.debug('Detected Pro initiation cycle.')
        while not path.isfile(intro_file):
            logging.debug('intro.html missing. Going to sleep.')
            sleep(5)
        return 'file://' + intro_file
    else:
        return False


if __name__ == "__main__":
    arch = machine()
    libx11 = cdll.LoadLibrary('libX11.so')
    HOME = getenv('HOME', '/home/pi')

    start_load_screen()

    signal.signal(signal.SIGUSR1, sigusr1)
    signal.signal(signal.SIGUSR2, sigusr2)

    reload_settings()

    if not path.isdir(SCREENLY_HTML):
        makedirs(SCREENLY_HTML)

    black_page = html_templates.black_page()
    pro_intro_file = cond_pro_init()

    if is_pro_init:
        load_screen_visible = False
        current_browser_url = pro_intro_file
    elif settings['show_splash']:
        current_browser_url = 'http://{0}:{1}/splash_page'.format(settings.get_listen_ip(), settings.get_listen_port())
    else:
        current_browser_url = black_page

    load_browser()

    # Wait until initialized (Pro only).
    did_show_pin = False
    did_show_claimed = False
    while is_pro_init:
        with open(path.join(settings.get_configdir(), 'setup_status.json'), 'rb') as status_file:
            status = json.load(status_file)

        if status['claimed']:
            browser_socket('js showUpdating()')
        else:
            browser_socket('js showPin("{0}")'.format(status['pin']))

        logging.debug('Waiting for node to be initialized.')
        sleep(5)


    global db_conn
    db_conn = db.conn(settings['database'])
    scheduler = Scheduler()

    logging.debug('Entering infinite loop.')
    while True:
        asset = scheduler.get_next_asset()
        logging.debug('got asset %s' % asset)

        is_up_to_date = check_update()
        logging.debug('Check update: %s' % str(is_up_to_date))

        if asset is None:
            logging.info('Playlist is empty. Going to sleep.')
            send_to_front('feh')
            browser_clear()
            sleep(5)
        elif asset_is_accessible(asset['uri']):
            mime = asset['mimetype']
            uri = asset['uri']
            logging.info('Showing asset %s.', asset['name'])
            send_to_front('browser')
            watchdog()

            if 'image' in mime:
                view_image(uri, asset['duration'])
            elif 'video' in mime:
                view_video(uri)
            elif 'web' in mime:
                view_web(uri, asset['duration'])
            else:
                logging.error('Unknown MimeType %s', mime)
        else:
            logging.info('Asset %s is not available, skipping.', asset['uri'])
