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
from datetime import datetime, timedelta
from dateutil.tz import tzutc
import requests
from workerpool import WorkerPool
import threading
import tinycss
import re
from collections import defaultdict
import itertools
import os
from .downloadjob import DownloadJob
from .filenameutils import FileNameUtils
from multiprocessing import cpu_count
from dateutil import parser
from operator import itemgetter
from .ratelimiter import TokenBucket

import logging

logger = logging.getLogger(__name__)


class BMScraper(FileNameUtils):
    def __init__(self, processor_factory):
        self.subreddits = []
        self.legacy_subreddits = []
        self.user = None
        self.password = None
        self.emotes = []
        self.image_blacklist = []
        self.nsfw_subreddits = []
        self.emote_info = []
        self.tags_data = {}
        self.cache_dir = '../images'
        self.workers = cpu_count()
        self.processor_factory = processor_factory
        self.rate_limit_lock = None

        self.mutex = threading.RLock()

        self._requests = requests.Session()
        # self._requests.headers = {'user-agent', 'User-Agent: Ponymote harvester v2.0 by /u/marminatoror'}
        self._requests.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Connection": "keep-alive",
            "TE": "trailers",
        })

    def _dedupe_emotes(self):
        with self.mutex:
            for subreddit in self.subreddits:
                subreddit_emotes = [
                    x for x in self.emotes if x['sr'] == subreddit]
                other_subreddits_emotes = [
                    x for x in self.emotes if x['sr'] != subreddit]
                for subreddit_emote in subreddit_emotes:
                    for emote in other_subreddits_emotes:
                        for name in subreddit_emote['names']:
                            if name in emote['names']:
                                emote['names'].remove(name)
                                logger.debug(
                                    'Removing {} from {}'.format(name, emote['sr']))
                                if len(emote['names']) == 0:
                                    logger.debug('Completely removed')
                                    self.emotes.remove(emote)

    def _fetch_css(self):
        logger.debug("Fetching css using {} threads".format(self.workers))
        workpool = WorkerPool(size=self.workers)

        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

        for subreddit in self.subreddits:

            if subreddit in self.legacy_subreddits:
                legacy_file = '{}/../legacy_css/{}.css'.format(
                    os.path.dirname(__file__), subreddit)
                if os.path.exists(legacy_file):
                    with open(legacy_file) as fh:
                        css = fh.read()
                        self._process_stylesheet_response(200,
                                                          css,
                                                          "text/css",
                                                          subreddit)
                else:
                    logger.error(
                        "No css file found for legacy subreddit {}".format(subreddit))
            else:
                workpool.put(DownloadJob(self._requests,
                                         'https://old.reddit.com/r/{}/stylesheet'.format(
                                             subreddit),
                                         retry=5,
                                         rate_limit_lock=self.rate_limit_lock,
                                         callback=self._callback_fetch_stylesheet,
                                         **{'subreddit': subreddit}))

        workpool.shutdown()
        workpool.join()

    def _download_images(self):
        logger.debug(
            "Downloading images using {} threads".format(self.workers))
        workpool = WorkerPool(size=self.workers)

        # we are not constrained by the Reddit rate limits here 
        rate_limit = TokenBucket(15, 30)

        # cache emotes
        key_func = lambda e: e['background-image']
        with self.mutex:
            for image_url, group in itertools.groupby(sorted(self.emotes, key=key_func), key_func):
                if not image_url:
                    continue

                file_path = self.get_file_path(
                    image_url, rootdir=self.cache_dir)
                if not os.path.isfile(file_path):
                    # Temp workaround for downloading apngs straight from amazon instead of broken ones from cloudflare
                    if "s3.amazonaws.com" not in image_url:
                        image_url = re.sub(r'^(https?:)?//', 'https://s3.amazonaws.com/', image_url)
                        
                    workpool.put(DownloadJob(self._requests,
                                             image_url,
                                             retry=5,
                                             rate_limit_lock=rate_limit,
                                             callback=self._callback_download_image,
                                             **{'image_path': file_path}))

        workpool.shutdown()
        workpool.join()

    def _process_emotes(self):
        logger.debug("Processing emotes using {} threads".format(self.workers))
        workpool = WorkerPool(self.workers)

        key_func = lambda e: e['background-image']
        with self.mutex:
            for image_url, group in itertools.groupby(sorted(self.emotes, key=key_func), key_func):
                if not image_url:
                    continue

                workpool.put(self.processor_factory.new_processor(scraper=self, image_url=image_url, group=list(group)))

        workpool.shutdown()
        workpool.join()

    def scrape(self):
        logger.debug(self.rate_limit_lock)
        # Login
        if self.user and self.password:
            body = {'user': self.user, 'passwd': self.password, "rem": False}
            self.rate_limit_lock and self.rate_limit_lock.acquire()
            self._requests.post('https://old.reddit.com/api/login', body)

        self._fetch_css()

        self._dedupe_emotes()

        self._download_images()

        self._process_emotes()

        logger.info('All Done')

    def _parse_css(self, data):
        cssparser = tinycss.make_parser('page3')
        css = cssparser.parse_stylesheet(data)

        if not css:
            return None

        re_emote = re.compile(r'a\[href[|^$]?=["\']\/([\w:]+)["\']\](:hover)?(\sem|\sstrong)?')
        emotes_staging = defaultdict(dict)

        for rule in css.rules:
            if re_emote.match(rule.selector.as_css()):
                for match in re_emote.finditer(rule.selector.as_css()):
                    rules = {}

                    for declaration in rule.declarations:
                        if match.group(3):
                            name = match.group(3).strip() + '-' + declaration.name
                            rules[name] = declaration.value.as_css()
                            emotes_staging[match.group(1)].update(rules)
                        elif declaration.name in ['text-align',
                                                  'line-height',
                                                  'color'] or declaration.name.startswith('font') or declaration.name.startswith('text'):
                            name = 'text-' + declaration.name
                            rules[name] = declaration.value.as_css()
                            emotes_staging[match.group(1)].update(rules)
                        elif declaration.name in ['width',
                                                   'height',
                                                   'background-image',
                                                   'background-position',
                                                   'background', ]:
                            name = declaration.name
                            if name == 'background-position':
                                val = ['{}{}'.format(v.value, v.unit if v.unit else '') for v in declaration.value if
                                       v.value != ' ']
                            else:
                                val = declaration.value[0].value
                            if match.group(2):
                                name = 'hover-' + name
                            rules[name] = val
                            emotes_staging[match.group(1)].update(rules)
        return emotes_staging

    def _callback_fetch_stylesheet(self, response, subreddit=None):
        if not subreddit:
            logger.error("Subreddit not set")
            return

        if response is not None:
            self._process_stylesheet_response(response.status_code,
                                              response.text,
                                              response.headers['Content-Type'],
                                              subreddit)
        else:
            logger.error("Failed to get response when fetching css for {}".format(subreddit))
            self._process_stylesheet_response(None,
                                              None,
                                              None,
                                              subreddit)

    def _process_stylesheet_response(self, status_code, text, content_type, subreddit=None):
        if not subreddit:
            logger.error("Subreddit not set")
            return

        css = ''

        css_path = os.path.sep.join([self.cache_dir, subreddit + '.css'])
        if os.path.exists(css_path):
            with open(css_path, 'r', encoding='utf8') as css_file:
                css = css_file.read()

        if status_code != 200:
            logger.error("Failed to fetch css for {} (Status {})".format(subreddit, status_code))
        elif content_type != "text/css":
            logger.error("Got something that wasn't css for {} (Context-Type {})".format(subreddit, content_type))
        else:
            logger.debug('Found css for {}'.format(subreddit))
            css = text
            with open(css_path, 'w', encoding='utf8') as css_file:
                css_file.write(css)

        if css == '':
            logger.error("No css for {} found".format(subreddit))
            return

        emotes_staging = self._parse_css(css)
        if not emotes_staging:
            return

        #group the emotes based on their css rule content
        for emote, group in itertools.groupby(sorted(emotes_staging.items(), key=lambda e: str(e[1])), lambda e: e[1]):
            emote['names'] = [a[0] for a in group]
            
            if 'tags' not in emote:
                emote['tags'] = []
            for name in emote['names']:
                meta_data = next((x for x in self.emote_info if x['name'] == name), None)

                if meta_data:
                    for key, val in meta_data.items():
                        if key != 'name':
                            emote[key] = val

                tag_data = None
                if name in self.tags_data:
                    tag_data = self.tags_data[name]

                if tag_data:
                    if 'tags' not in emote:
                        emote['tags'] = []
                    logger.debug('Tagging: {} with {}'.format(name, tag_data))
                    emote['tags'].extend(k for k, v in tag_data['tags'].items() if v['score'] >= 1)
                    if tag_data.get('specialTags'):
                        emote['tags'].extend(tag_data['specialTags'])

                    if 'added_date' in tag_data:
                        added_date = parser.parse(tag_data['added_date'])
                        now = datetime.now(tzutc())
                        if now - added_date < timedelta(days=7):
                            emote['tags'].append('new')

            if subreddit in self.nsfw_subreddits:
                emote['nsfw'] = True
                emote['tags'].append('nsfw')
            emote['sr'] = subreddit

            # Sometimes people make css errors, fix those.
            if 'background-image' not in emote and 'background' in emote:
                if re.match(r'^(https?:)?//', emote['background']):
                    emote['background-image'] = emote['background']
                    del emote['background']

            # need at least an image for a ponymote. Some trash was getting in.
            # 1500 pixels should be enough for anyone!
            if ('background-image' in emote
                and re.sub(r'^(https?:)?//', '', emote['background-image']) not in self.image_blacklist
                and 'height' in emote and emote['height'] < 1500
                and 'width' in emote and emote['width'] < 1500):
                with self.mutex:
                    self.emotes.append(emote)
            else:
                logger.warn('Discarding emotes {}'.format(emote['names'][0]))

    def _callback_download_image(self, response, image_path=None):
        if not image_path:
            logger.error("image_path not set")
            return

        if not response:
            logger.error("Failed to fetch image {}".format(image_path))
            return

        if response.status_code != 200:
            logger.error("Failed to fetch image {} (Status {})".format(image_path, response.status_code))
            return

        data = response.content
        if not data or len(data) == 0:
            logger.error("Failed to fetch image {}, data is empty".format(image_path))
            return

        image_dir = os.path.dirname(image_path)
        if not os.path.exists(image_dir):
            try:
                os.makedirs(image_dir)
            except OSError:
                pass

        with open(image_path, 'wb') as f:
            f.write(data)
