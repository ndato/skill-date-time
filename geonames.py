import sys
import urllib.parse as urllib
import urllib.request as urllib2
import json
import logging

from mycroft.api import Api

class GeonamesError(Exception):
    
    def __init__(self, status):
        Exception.__init__(self, status)        # Exception is an old-school class
        self.status = status
    
    def __str__(self):
        return self.status
    
    def __unicode__(self):
        return unicode(self.__str__())


class GeonamesClient(Api):
    BASE_URL = 'https://secure.geonames.org/'

    def __init__(self, username):
        super(GeonamesClient, self).__init__("GeonamesAPI")

    def call(self, service, params=None):
        url = self.build_url(service, params)

        try:
            response = urllib2.urlopen(urllib2.Request(url))
            json_response = json.loads(response.read())
        except (urllib2.URLError):
            raise GeonamesError('API didnt return 200 response.')
        except ValueError:
            raise GeonamesError('API did not return valid json response.')
        else:
            if 'status' in json_response:
                raise GeonamesError(json_response['status']['message'])
        return json_response

    def build_url(self, service, params=None):
        url = '%s%s?username=%s' % (GeonamesClient.BASE_URL, service, self.username)
        if params:
            if isinstance(params, dict):
                params = dict((k, v) for k, v in params.items() if v is not None)
                params = urllib.urlencode(params)
            url = '%s&%s' % (url, params)
        return url
    
    def find_timezone(self, params):
        return self.call('timezoneJSON', params)

    def get_location_data(self, params):
        return self.call('searchJSON', params)