# coding: utf-8
from __future__ import unicode_literals

import hashlib
import hmac
import re
import time
import uuid

from .common import InfoExtractor
from ..compat import (
    compat_HTTPError,
    compat_str,
)
from ..utils import (
    determine_ext,
    ExtractorError,
    int_or_none,
    str_or_none,
    try_get,
    url_or_none,
)


class HotStarBaseIE(InfoExtractor):
    _AKAMAI_ENCRYPTION_KEY = b'\x05\xfc\x1a\x01\xca\xc9\x4b\xc4\x12\xfc\x53\x12\x07\x75\xf9\xee'

    def _call_api(self, path, video_id, query_name='contentId'):
        st = int(time.time())
        exp = st + 6000
        auth = 'st=%d~exp=%d~acl=/*' % (st, exp)
        auth += '~hmac=' + hmac.new(self._AKAMAI_ENCRYPTION_KEY, auth.encode(), hashlib.sha256).hexdigest()
        response = self._download_json(
            'https://api.hotstar.com/' + path, video_id, headers={
                'hotstarauth': auth,
                'x-country-code': 'IN',
                'x-platform-code': 'JIO',
            }, query={
                query_name: video_id,
                'tas': 10000,
            })
        if response['statusCode'] != 'OK':
            raise ExtractorError(
                response['body']['message'], expected=True)
        return response['body']['results']

    def _call_api_v2(self, path, video_id, country_code):
        cookies = self._get_cookies('https://www.hotstar.com/%s' % (country_code))
        user_up = cookies.get('userUP')
        if not user_up or not user_up.value:
            raise ExtractorError(
                'You must pass the cookies for a logged in hotstar.com '
                'session with --cookies to download replays.',
                expected=True)

        st = int(time.time())
        exp = st + 6000
        auth = 'st=%d~exp=%d~acl=/*' % (st, exp)
        auth += '~hmac=' + hmac.new(self._AKAMAI_ENCRYPTION_KEY, auth.encode(), hashlib.sha256).hexdigest()
        response = self._download_json(
            'https://api.hotstar.com/%s/%s' % (path, video_id), video_id, headers={
                'hotstarauth': auth,
                'x-country-code': country_code,
                'x-HS-AppVersion': '6.88.2',
                'x-HS-Platform': 'web',
                'x-HS-UserToken': user_up.value,
                'x-Request-Id': compat_str(uuid.uuid4()),
            }, query={
                'desired-config': 'encryption:plain|ladder:phone|package:hls',
                'device-id': compat_str(uuid.uuid4()),
                'os-name': 'Windows',
                'os-version': '10',
            })
        if 'errorCode' in response:
            raise ExtractorError(
                response, expected=True)
        return response['data']


class HotStarIE(HotStarBaseIE):
    IE_NAME = 'hotstar'
    _VALID_URL = r'https?://(?:www\.)?hotstar\.com/(?:(?P<country_code>\w\w)/)?(?:.+?[/-])?(?P<id>\d{10})'
    _TESTS = [{
        # contentData
        'url': 'https://www.hotstar.com/can-you-not-spread-rumours/1000076273',
        'info_dict': {
            'id': '1000076273',
            'ext': 'mp4',
            'title': 'Can You Not Spread Rumours?',
            'description': 'md5:c957d8868e9bc793ccb813691cc4c434',
            'timestamp': 1447248600,
            'upload_date': '20151111',
            'duration': 381,
        },
        'params': {
            # m3u8 download
            'skip_download': True,
        }
    }, {
        # contentDetail
        'url': 'https://www.hotstar.com/movies/radha-gopalam/1000057157',
        'only_matching': True,
    }, {
        'url': 'http://www.hotstar.com/sports/cricket/rajitha-sizzles-on-debut-with-329/2001477583',
        'only_matching': True,
    }, {
        'url': 'http://www.hotstar.com/1000000515',
        'only_matching': True,
    }, {
        # only available via api v2
        'url': 'https://www.hotstar.com/tv/ek-bhram-sarvagun-sampanna/s-2116/janhvi-targets-suman/1000234847',
        'only_matching': True,
    }]
    _GEO_BYPASS = False

    def _real_extract(self, url):
        country_code, video_id = re.match(HotStarIE._VALID_URL, url).groups()
        if country_code is None:
            country_code = 'in'

        webpage = self._download_webpage(url, video_id)
        app_state = self._parse_json(self._search_regex(
            r'<script>window\.APP_STATE\s*=\s*({.+?})</script>',
            webpage, 'app state'), video_id)
        video_data = {}
        getters = list(
            lambda x, k=k: x['initialState']['content%s' % k]['content']
            for k in ('Data', 'Detail')
        )
        for v in app_state.values():
            content = try_get(v, getters, dict)
            if content and content.get('contentId') == video_id:
                video_data = content
                break

        title = video_data['title']

        headers = {'Referer': url}
        geo_restricted = False
        playback_sets = self._call_api_v2('play/v2/playback/content', video_id, country_code)['playBackSets']
        entries = []
        index = -1
        for playback_set in playback_sets:
            formats = []
            index += 1
            if not isinstance(playback_set, dict):
                continue
            format_url = url_or_none(playback_set.get('playbackUrl'))
            if not format_url:
                continue
            format_url = re.sub(
                r'(?<=//staragvod)(\d)', r'web\1', format_url)
            tags = str_or_none(playback_set.get('tagsCombination')) or ''
            if tags and 'encryption:plain' not in tags:
                continue
            ext = determine_ext(format_url)
            try:
                if 'package:hls' in tags or ext == 'm3u8':
                    formats.extend(self._extract_m3u8_formats(
                        format_url, video_id, 'mp4',
                        entry_protocol='m3u8_native',
                        m3u8_id='hls', headers=headers))
                elif 'package:dash' in tags or ext == 'mpd':
                    formats.extend(self._extract_mpd_formats(
                        format_url, video_id, mpd_id='dash', headers=headers))
                elif ext == 'f4m':
                    # produce broken files
                    pass
                else:
                    formats.append({
                        'url': format_url,
                        'width': int_or_none(playback_set.get('width')),
                        'height': int_or_none(playback_set.get('height')),
                    })
            except ExtractorError as e:
                if isinstance(e.cause, compat_HTTPError) and e.cause.code == 403:
                    geo_restricted = True
                continue
            if not formats:
                continue

            self._sort_formats(formats)
            for f in formats:
                f.setdefault('http_headers', {}).update(headers)

            entries.append({
                'id': video_id + '-' + str(index),
                'title': title,
                'description': video_data.get('description'),
                'duration': int_or_none(video_data.get('duration')),
                'timestamp': int_or_none(video_data.get('broadcastDate') or video_data.get('startDate')),
                'formats': formats,
                'channel': video_data.get('channelName'),
                'channel_id': video_data.get('channelId'),
                'series': video_data.get('showName'),
                'season': video_data.get('seasonName'),
                'season_number': int_or_none(video_data.get('seasonNo')),
                'season_id': video_data.get('seasonId'),
                'episode': title,
                'episode_number': int_or_none(video_data.get('episodeNo')),
            })

        if not entries and geo_restricted:
            self.raise_geo_restricted(countries=['IN'])

        return self.playlist_result(entries, video_id)


class HotStarPlaylistIE(HotStarBaseIE):
    IE_NAME = 'hotstar:playlist'
    _VALID_URL = r'https?://(?:www\.)?hotstar\.com/(?:(?P<country_code>\w\w)/)?tv/[^/]+/s-\w+/list/[^/]+/t-(?P<id>\w+)'
    _TESTS = [{
        'url': 'https://www.hotstar.com/tv/savdhaan-india/s-26/list/popular-clips/t-3_2_26',
        'info_dict': {
            'id': '3_2_26',
        },
        'playlist_mincount': 20,
    }, {
        'url': 'https://www.hotstar.com/tv/savdhaan-india/s-26/list/extras/t-2480',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        country_code, playlist_id = re.match(HotStarPlaylistIE._VALID_URL, url).groups()
        if country_code is None:
            country_code = 'in'

        collection = self._call_api('o/v1/tray/find', playlist_id, 'uqId')

        entries = [
            self.url_result(
                'https://www.hotstar.com/%s/%s' % (country_code, video['contentId']),
                ie=HotStarIE.ie_key(), video_id=video['contentId'])
            for video in collection['assets']['items']
            if video.get('contentId')]

        return self.playlist_result(entries, playlist_id)
