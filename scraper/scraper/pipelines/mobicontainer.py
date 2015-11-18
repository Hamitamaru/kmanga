# -*- coding: utf-8 -*-
#
# (c) 2014 Alberto Planas <aplanas@gmail.com>
#
# This file is part of KManga.
#
# KManga is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# KManga is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with KManga.  If not, see <http://www.gnu.org/licenses/>.

import collections
import hashlib
import logging
import os

try:
    import cPickle as pickle
except:
    import pickle

from scrapy.mail import MailSender
from scrapy.utils.decorators import inthread

from core.models import Result
from core.models import Issue
from core.models import Subscription
from django.utils import timezone

from mobi import Container
from mobi import MangaMobi

# https://docs.djangoproject.com/en/dev/releases/1.8/#standalone-scripts
import django
django.setup()


# Empty page.  Used when the original one can't be downloaded.
EMPTY = 'empty.png'

logger = logging.getLogger(__name__)


class MobiCache(collections.MutableMapping):
    """Cache for `.mobi` documents.

    This cache avoid the creation of new MOBI documents previously
    created.

    key = ('spider_name', 'manga_name', 'manga_number', 'url')
    value = (
        [('mobi1.1.mobi', 'tests/fixtures/cache/mobi1.1.mobi'),
         ('mobi1.2.mobi', 'tests/fixtures/cache/mobi1.2.mobi')],
        stats_dict)

    """
    def __init__(self, mobi_store):
        self.mobi_store = mobi_store
        self.index = os.path.join(mobi_store, 'cache', 'index')
        self.data = os.path.join(mobi_store, 'cache', 'data')

        # Create cache directories if they don't exists.
        for directory in (self.index, self.data):
            if not os.path.exists(directory):
                os.makedirs(directory)

    def __file(self, key):
        """Return the MD5 hash for the key."""
        # Generate an unique hash from the key components.
        spider, name, number, url = key
        name = '%s_%s_%s_%s' % (spider, name, number, url)
        name = hashlib.md5(name).hexdigest()
        return name

    def __index_file(self, key):
        """Return the full path of the index file."""
        name = self.__file(key)
        return os.path.join(self.index, name)

    def __data_file(self, key):
        """Return the full path of the data file."""
        name = self.__file(key)
        return os.path.join(self.data, name)

    def __getitem__(self, key):
        try:
            # The last element contains the key.
            return pickle.load(open(self.__index_file(key), 'rb'))[:-1]
        except:
            raise KeyError

    def __setitem__(self, key, value):
        # Makes sure that the element is not there anymore.
        if key in self:
            del self[key]

        # Create first the links into the data store.
        data_file_prefix = self.__data_file(key)
        value_ext = [(v[0], v[1], '%s-%02d' % (data_file_prefix, i))
                     for i, v in enumerate(value[0])]
        for _, mobi_file, data_file in value_ext:
            os.link(mobi_file, data_file)

        # Store the index in the index file.  The index file is
        # composed of the mobi name, the path of the mobi file, the
        # statistics and the key.
        index = [(v[0], v[2]) for v in value_ext]
        index = [index, value[-1], key]
        pickle.dump(index, open(self.__index_file(key), 'wb'),
                    pickle.HIGHEST_PROTOCOL)

    def __delitem__(self, key):
        index, _ = self[key]
        for _, _data_file in index:
            os.unlink(_data_file)
        os.unlink(self.__index_file(key))

    def __iter__(self):
        for index_path in os.listdir(self.index):
            index_path = os.path.join(self.index, index_path)
            yield pickle.load(open(index_path, 'rb'))[-1]

    def __len__(self):
        return len(os.listdir(self.index))


class MobiContainer(object):
    def __init__(self, kindlegen, images_store, mobi_store,
                 volume_max_size):
        self.kindlegen = kindlegen
        self.images_store = images_store
        self.mobi_store = mobi_store
        self.volume_max_size = volume_max_size
        self.items = {}

    @classmethod
    def from_settings(cls, settings):
        return cls(settings['KINDLEGEN'],
                   settings['IMAGES_STORE'],
                   settings['MOBI_STORE'],
                   settings['VOLUME_MAX_SIZE'])

    def process_item(self, item, spider):
        # Bypass the pipeline if called with dry-run parameter.
        if hasattr(spider, 'dry_run'):
            return item

        # Recover the `stats` from the crawler
        self.stats = spider.crawler.stats

        if spider._operation == 'manga':
            key = (spider.name, spider.manga, spider.issue, spider.url)
            if key not in self.items:
                self.items[key] = []
            self.items[key].append(item)
        return item

    def close_spider(self, spider):
        # If there is a 503 error, the parse() method of mangaspider
        # is never called and the attribute is not set.  This can be
        # used as an indication of error in the download.
        if hasattr(spider, '_operation'):
            if spider._operation == 'manga':
                return self.create_mobi(spider)

    def _normalize(self, number):
        """Normalize the string tha represent a `number`."""
        number = [i if i.isalnum() else '_' for i in number.lower()]
        return ''.join(number)

    def _create_mobi(self, name, number, images, issue):
        """Create the MOBI file and return a list of values and containers."""
        dir_name = '%s_%s' % (name, self._normalize(number))
        container = Container(os.path.join(self.mobi_store, dir_name))
        container.create(clean=True)
        images = sorted(images, key=lambda x: x['number'])
        _images = []
        for i in images:
            if i['images']:
                image_path = i['images'][0]['path']
            else:
                image_path = EMPTY
                self.stats.inc_value('mobi/missing_pages')
            _images.append(os.path.join(self.images_store, image_path))
        container.add_images(_images, adjust=Container.ROTATE, as_link=True)

        if container.get_size() > self.volume_max_size:
            containers = container.split(self.volume_max_size, clean=True)
            container.clean()
        else:
            containers = [container]

        # Basic container to store issue information
        class Info(object):
            def __init__(self, issue, multi_vol=False, vol=None):
                if multi_vol:
                    self.title = '%s %s/%02d' % (issue.manga.name,
                                                 issue.number, vol)
                else:
                    self.title = '%s %s' % (issue.manga.name,
                                            issue.number)
                self.language = issue.language.lower()
                self.author = issue.manga.author
                self.publisher = issue.manga.source.name
                reading_direction = issue.manga.reading_direction.lower()
                self.reading_direction = 'horizontal-%s' % reading_direction

        values_and_containers = []
        for volume, container in enumerate(containers):
            multi_vol, vol = len(containers) > 1, volume + 1
            info = Info(issue, multi_vol, vol)

            mobi = MangaMobi(container, info, kindlegen=self.kindlegen)
            mobi_name, mobi_file = mobi.create()
            values_and_containers.append(((mobi_name, mobi_file), container))
        return values_and_containers

    @inthread
    def create_mobi(self, spider):
        cache = MobiCache(self.mobi_store)

        # Signalize as an error the missing self.items, probably there
        # is a hidden bug in the spider.
        if not self.items:
            logger.error('Items are empty, please check [%s]' % spider)

        for key, value in self.items.items():
            # First element is the spider name
            _, name, number, url = key

            issue = Issue.objects.get(url=url)

            # Move the Result status to PROCESSING
            subscription = Subscription.objects.get(
                manga=issue.manga,
                user__userprofile__email_kindle=spider.to_email)
            result, _ = Result.objects.get_or_create(
                issue=issue,
                subscription=subscription)
            result.status = Result.PROCESSING
            result.save()

            if key not in cache:
                # The containers need to be cleaned here.
                values_and_containers = self._create_mobi(name, number,
                                                          value, issue)
                cache[key] = ([v[0] for v in values_and_containers],
                              self.stats.get_stats())
                for _, container in values_and_containers:
                    container.clean()
            mobi_info, stats = cache[key]
            for mobi_name, mobi_file in mobi_info:
                mail = MailSender.from_settings(spider.settings)
                deferred = mail.send(
                    to=[spider.to_email],
                    subject='Your kmanga.net request',
                    body='',
                    attachs=((mobi_name, 'application/x-mobipocket-ebook',
                              open(mobi_file, 'rb')),))
                cb_data = [spider.from_email, spider.to_email, name,
                           number, result]
                deferred.addCallbacks(self.mail_ok, self.mail_err,
                                      callbackArgs=cb_data,
                                      errbackArgs=cb_data)
                # XXX TODO - Send email when errors

    def mail_ok(self, result_mail, from_mail, to_mail, manga_name,
                manga_number, result):
        result.send_date = timezone.now()
        result.status = Result.SENT
        result.save()

    def mail_err(self, result_mail, from_mail, to_mail, manga_name,
                 manga_number, result):
        result.status = Result.FAILED
        result.save()
