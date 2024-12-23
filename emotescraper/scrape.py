#!/usr/bin/env python2
# --------------------------------------------------------------------
#
# Copyright (C) 2013 Marminator <cody_y@shaw.ca>
# Copyright (C) 2013 pao <patrick.oleary@gmail.com>
# Copyright (C) 2013 Daniel Triendl <daniel@pew.cc>
#
# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# COPYING for more details.
#
# --------------------------------------------------------------------

import logging
import time
import requests
import gzip
from bmscraper.ratelimiter import TokenBucket
import make_v2

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
from bmscraper import BMScraper, UserscriptEmotesProcessorFactory

try:
    import brotli
except ImportError:
    logger.warn('Brotli is not available')
    brotli = None

from data import *
from json import dumps
import os

CDN_ORIGIN = 'https://berrytube.tv/berrymotes'

factory = UserscriptEmotesProcessorFactory(single_emotes_filename=os.path.join('..', 'single_emotes', '{}', '{}.png'),
                                           apng_dir=os.path.join('..', 'images'),
                                           apng_url=CDN_ORIGIN + '/images/{}/{}')

scraper = BMScraper(factory)
scraper.user = None #os.environ['REDDIT_USERNAME']
scraper.password = None #os.environ['REDDIT_PASSWORD']
scraper.subreddits = subreddits
scraper.legacy_subreddits = legacy_subreddits
scraper.image_blacklist = image_blacklist
scraper.nsfw_subreddits = nsfw_subreddits
scraper.emote_info = emote_info
scraper.rate_limit_lock = TokenBucket(15, 30)
scraper.tags_data = requests.get(CDN_ORIGIN + "/data/tags.js").json()

start = time.time()
scraper.scrape()
logger.info("Finished scrape in {}.".format(time.time() - start))

def output(basename, data):
    with open(basename, 'wb') as fh:
        fh.write(data.encode('utf-8'))

    fname = basename + '.gz'
    try:
        with gzip.open(fname, 'wb') as fh:
            fh.write(data.encode('utf-8'))
    except:
        logger.exception('Unable to gzip emote data')
        try:
            os.unlink(fname)
        except FileNotFoundError:
            pass

    if brotli:
        fname = basename + '.br'
        try:
            with open(fname, 'wb') as fh:
                fh.write(brotli.compress(data, brotli.MODE_TEXT))
        except:
            logger.exception('Unable to brotli emote data')
            try:
                os.unlink(fname)
            except FileNotFoundError:
                pass

json = dumps(scraper.emotes, separators=(',', ':'))

output(os.path.join('..', 'data', 'berrymotes_data.js'), ''.join(["var berryEmotes=", json, ";"]))
output(os.path.join('..', 'data', 'berrymotes_json_data.json'), json)
output(os.path.join('..', 'data', 'berrymotes_json_data.v2.json'), dumps(make_v2.from_data(scraper.emotes), separators=(',', ':')))
